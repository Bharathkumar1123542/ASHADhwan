"""backend/app/session.py – Step 4 / Step 9 (core turn & WebSocket loop)

SessionHandler manages one WebSocket session end-to-end:

  State machine states: collecting, reading_back, awaiting_confirmation, saving.
  Turn pipeline: Stage A (persist, lexicon danger check, referral) runs
  immediately on every final turn; Stage B (Gemini extraction + patch) runs
  one at a time under draft_lock (SD-17).

Design rules enforced here:
  - Never save without a valid confirm for the current form_version (FR-RB-03,
    NFR-SAF-02, SD-04).
  - Every spoken utterance comes from locales/ via templates.render() (A-05,
    NFR-SAF-01).
  - danger_sign_flag / danger_sign_codes are set ONLY by _handle_danger()
    (FR-DS-05).
  - Logs hold IDs and timings only (NFR-PRIV-02).
  - Lexicon danger check always runs; Gemini danger check is attempted but the
    lexicon path works when Gemini is down (FR-DS-06).
  - A danger detection in reading_back or awaiting_confirmation increments
    form_version and replays the read-back (SD-03).
  - confirm is valid only if the last read-back was for the current
    form_version and ended before the turn's speech_start_ms (SD-04).

Dependencies injected via constructor so unit tests can supply fakes.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from backend.app.danger.lexicon import match as lexicon_match
from backend.app.form import VisitDraft, apply_patch, derive_follow_ups, REQUIRED_FIELDS
from backend.app.log import get_logger
from backend.app.templates import render

_log = get_logger()

# ---------------------------------------------------------------------------
# State constants
# ---------------------------------------------------------------------------

STATE_COLLECTING = "collecting"
STATE_READING_BACK = "reading_back"
STATE_AWAITING_CONFIRMATION = "awaiting_confirmation"
STATE_SAVING = "saving"

# Read-back field order (prd.md §6.1)
_RB_FIELD_ORDER = (
    "beneficiary_id",
    "beneficiary_name",
    "visit_type",
    "weight_kg",
    "vaccinations_due",
    "next_visit_date",
    "notes",
)

_FIELD_TO_RB_KEY = {
    "beneficiary_id": "rb_beneficiary_id",
    "beneficiary_name": "rb_beneficiary_name",
    "visit_type": "rb_visit_type",
    "weight_kg": "rb_weight_kg",
    "vaccinations_due": "rb_vaccinations_due",
    "next_visit_date": "rb_next_visit_date",
    "notes": "rb_notes",
}

_DANGER_CODE_TO_REFERRAL_KEY = {
    "DS-01": "danger_referral_ds01",
    "DS-02": "danger_referral_ds02",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc_ms() -> int:
    """Current time as integer milliseconds since epoch (UTC)."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _render_rb_field(field: str, draft: VisitDraft, language: str) -> str:
    """Render a single read-back line for one draft field."""
    key = _FIELD_TO_RB_KEY.get(field)
    if key is None:
        return ""
    value = getattr(draft, field, None)
    if value is None:
        return ""
    # notes: skip when empty
    if field == "notes" and not value:
        return ""

    slots: dict[str, Any] = {}
    if field == "beneficiary_id":
        slots["id_spelled"] = value
    elif field == "beneficiary_name":
        slots["name"] = value
    elif field == "visit_type":
        slots["visit_type_label"] = value
    elif field == "weight_kg":
        slots["weight"] = value
    elif field == "vaccinations_due":
        slots["vaccinations"] = value if value is not None else []
    elif field == "next_visit_date":
        slots["date_spoken"] = value
    elif field == "notes":
        slots["notes"] = value

    try:
        return render(key, language, **slots)
    except Exception:
        return ""


def _build_read_back_text(draft: VisitDraft, language: str) -> str:
    """Compose the full read-back utterance text (prd.md §6.1 read-back composition)."""
    parts: list[str] = [render("read_back_intro", language)]

    for field in _RB_FIELD_ORDER:
        line = _render_rb_field(field, draft, language)
        if line:
            parts.append(line)

    # One rb_danger_line per flagged code (FR-DS-08)
    for code in draft.danger_sign_codes:
        code_key = code.lower().replace("-", "")
        try:
            parts.append(render("rb_danger_line", language, sign_label=code_key))
        except Exception:
            pass

    parts.append(render("confirm_question", language))
    return " ".join(parts)


def _build_corrected_read_back_text(
    draft: VisitDraft, changed_fields: list[str], language: str
) -> str:
    """Compose the corrected_read_back utterance (A-09, FR-CR-02)."""
    field_lines: list[str] = []
    for field in _RB_FIELD_ORDER:
        if field in changed_fields:
            line = _render_rb_field(field, draft, language)
            if line:
                field_lines.append(line)

    changed_readback = " ".join(field_lines)
    try:
        return render("corrected_read_back", language, changed_fields_readback=changed_readback)
    except Exception:
        return changed_readback


# ---------------------------------------------------------------------------
# SessionHandler
# ---------------------------------------------------------------------------

class SessionHandler:
    """One instance per active WebSocket session.

    Parameters
    ----------
    session_id:
        UUID4 string; created by the WebSocket route before constructing this.
    worker_id:
        Worker profile identifier.
    language:
        Worker's language ('en', 'hi', 'kn').
    send_json:
        Coroutine that sends a text (JSON) frame to the browser.
    send_bytes:
        Coroutine that sends a binary frame to the browser.
    gemini:
        Optional object with async method
        ``extract(turn_text, draft, language) -> (intent, patch)`` and
        ``classify_danger(turn_text, draft) -> list[str]``.
        When None (or when the call raises), only the lexicon is used for
        danger; extraction returns intent='unclear' and an empty patch.
    """

    def __init__(
        self,
        session_id: str,
        worker_id: str,
        language: str,
        send_json: Callable[[dict[str, Any]], Awaitable[None]],
        send_bytes: Callable[[bytes], Awaitable[None]],
        gemini: Any = None,
    ) -> None:
        self.session_id = session_id
        self.worker_id = worker_id
        self.language = language
        self._send_json = send_json
        self._send_bytes = send_bytes
        self._gemini = gemini

        self.draft = VisitDraft()
        self.state = STATE_COLLECTING
        self.turn_queue: asyncio.Queue[tuple[str, str, int]] = asyncio.Queue()
        # (turn_id, turn_text, speech_start_ms)

        self.draft_lock = asyncio.Lock()
        self.danger_lock = asyncio.Lock()
        self.state_changed = asyncio.Event()

        # Read-back tracking for SD-04 stale-confirm check
        # Set to (form_version, ended_at_ms) when a full read-back completes.
        self._last_readback: tuple[int, int] | None = None

        # Flags spoken (not re-spoken if still flagged) for danger referral
        self._spoken_danger_codes: set[str] = set()

    # ------------------------------------------------------------------
    # Public entry point – called by the WebSocket route after session.start
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Drive the Stage B turn worker loop.  Call this as a background task."""
        while True:
            turn_id, turn_text, speech_start_ms = await self.turn_queue.get()
            if turn_id == "__STOP__":
                break
            await self._stage_b(turn_id, turn_text, speech_start_ms)

    async def stop(self) -> None:
        """Signal the run() loop to exit."""
        await self.turn_queue.put(("__STOP__", "", 0))

    # ------------------------------------------------------------------
    # Stage A – called immediately on every final turn (SD-17)
    # ------------------------------------------------------------------

    async def handle_final_turn(self, turn_id: str, turn_text: str) -> None:
        """Stage A: persist (caller handles DB), run lexicon, speak referral if needed.

        Does NOT modify the draft (that is Stage B under draft_lock).
        Enqueues the turn for Stage B.
        """
        speech_start_ms = _now_utc_ms()

        # Accumulate worker context for danger (without taking draft_lock since
        # we only read danger_sign info and visit_type here).
        visit_type = self.draft.visit_type
        prior_text = self.draft.worker_context()

        # --- Lexicon danger check (always, FR-DS-06) ---
        try:
            lexicon_codes = lexicon_match(turn_text, visit_type, prior_text)
        except Exception:
            lexicon_codes = []

        # --- Gemini danger check (best-effort) ---
        gemini_danger_codes: list[str] = []
        if self._gemini is not None:
            try:
                gemini_danger_codes = await self._gemini.classify_danger(
                    turn_text, self.draft
                )
            except Exception:
                pass  # lexicon path still runs (FR-DS-06)

        # Union of both paths
        combined_codes = list({*lexicon_codes, *gemini_danger_codes})

        if combined_codes:
            await self._handle_danger(turn_id, turn_text, combined_codes)

        # Enqueue for Stage B
        await self.turn_queue.put((turn_id, turn_text, speech_start_ms))

    # ------------------------------------------------------------------
    # Stage B – serialized under draft_lock
    # ------------------------------------------------------------------

    async def _stage_b(
        self, turn_id: str, turn_text: str, speech_start_ms: int
    ) -> None:
        """Stage B: Gemini extraction, patch, state transitions, utterances."""
        async with self.draft_lock:
            # Accumulate this turn into draft worker context
            self.draft.add_worker_turn(turn_text)

            # --- Gemini extraction (best-effort) ---
            intent = "unclear"
            patch: dict[str, Any] = {}
            if self._gemini is not None:
                try:
                    intent, patch = await self._gemini.extract(
                        turn_text, self.draft, self.language
                    )
                except Exception:
                    intent = "unclear"
                    patch = {}

            await self._handle_intent(turn_id, turn_text, intent, patch, speech_start_ms)

    # ------------------------------------------------------------------
    # Intent handling (spec.md §7.4, SD-05)
    # ------------------------------------------------------------------

    async def _handle_intent(
        self,
        turn_id: str,
        turn_text: str,
        intent: str,
        patch: dict[str, Any],
        speech_start_ms: int,
    ) -> None:
        """Apply intent-driven state transitions and speak the response."""

        # --- always: medical_question → out_of_scope only (FR-DS-04, SD-05) ---
        if intent == "medical_question":
            await self._speak("out_of_scope", "notice")
            return

        # --- always: cancel_visit → discard (FR-CR-03) ---
        if intent == "cancel_visit":
            await self._discard()
            return

        # --- always: unclear → didnt_catch (SD-05) ---
        if intent == "unclear":
            await self._speak("didnt_catch", "notice")
            return

        # --- confirm: only valid in awaiting_confirmation (SD-05) ---
        if intent == "confirm":
            if self.state != STATE_AWAITING_CONFIRMATION:
                # Treat as unclear outside awaiting_confirmation
                await self._speak("didnt_catch", "notice")
                return
            await self._handle_confirm(turn_id, speech_start_ms)
            return

        # --- provide_info or correct: apply patch ---
        if intent in ("provide_info", "correct"):
            if not patch:
                await self._speak("didnt_catch", "notice")
                return
            await self._apply_patch_and_respond(turn_id, patch)
            return

        # Fallback
        await self._speak("didnt_catch", "notice")

    async def _apply_patch_and_respond(
        self, turn_id: str, patch: dict[str, Any]
    ) -> None:
        """Apply patch to draft, send form.update, then respond based on state."""
        try:
            changed_fields, rejected_fields = apply_patch(self.draft, patch)
        except ValueError:
            # Patch tried to set danger fields — ignore silently
            await self._speak("didnt_catch", "notice")
            return

        if not changed_fields and not rejected_fields:
            # Nothing changed and nothing rejected → didnt_catch (SD-05)
            await self._speak("didnt_catch", "notice")
            return

        # Send form.update for any change
        if changed_fields:
            await self._send_form_update(changed_fields)

        # If a required field was rejected, ask for it again
        if rejected_fields:
            first_rejected = rejected_fields[0]
            await self._ask_field(first_rejected)
            return

        # State transition logic
        if self.state == STATE_AWAITING_CONFIRMATION:
            # Correction during confirmation → corrected_read_back (FR-CR-02)
            if changed_fields:
                corrected_text = _build_corrected_read_back_text(
                    self.draft, changed_fields, self.language
                )
                await self._send_utterance(corrected_text, "read_back")
                # Then ask for confirmation again
                await self._speak("confirm_question", "prompt")
                await self._set_state(STATE_AWAITING_CONFIRMATION)
                return

        # In collecting state: check if form is complete
        if self.draft.is_complete():
            await self._do_read_back()
        else:
            # Ask for next missing field
            missing = self.draft.missing_fields()
            if missing:
                await self._ask_field(missing[0])

    async def _handle_confirm(self, turn_id: str, speech_start_ms: int) -> None:
        """Handle a confirm intent (SD-04 stale check, then save)."""
        # SD-04 staleness check
        if self._last_readback is None:
            _log.info(
                "confirm.stale",
                session_id=self.session_id,
                turn_id=turn_id,
            )
            return

        rb_version, rb_ended_ms = self._last_readback
        if rb_version != self.draft.form_version:
            _log.info(
                "confirm.stale",
                session_id=self.session_id,
                turn_id=turn_id,
            )
            return

        if speech_start_ms <= rb_ended_ms:
            _log.info(
                "confirm.stale",
                session_id=self.session_id,
                turn_id=turn_id,
            )
            return

        # Valid confirm – save
        await self._save(turn_id)

    # ------------------------------------------------------------------
    # Danger handling (SD-03, FR-DS-03, FR-DS-05)
    # ------------------------------------------------------------------

    async def _handle_danger(
        self, turn_id: str, turn_text: str, codes: list[str]
    ) -> None:
        """Handle new danger sign codes from Stage A.

        Sets danger flags on draft (FR-DS-05) and speaks referral on first
        detection of each code in the current draft (FR-DS-03, SD-02).
        SD-03: if in reading_back or awaiting_confirmation, increment
        form_version and schedule a replay of the read-back.
        """
        async with self.danger_lock:
            new_codes = [
                c for c in codes if c not in self.draft.danger_sign_codes
            ]
            if not new_codes:
                return

            # Set the flag and codes on the draft (only danger handling does this)
            for code in new_codes:
                if code not in self.draft.danger_sign_codes:
                    self.draft.danger_sign_codes.append(code)
            self.draft.danger_sign_flag = True
            self.draft.form_version += 1

            # Send danger.alert with ALL currently flagged codes
            all_codes = list(self.draft.danger_sign_codes)
            alert_text = " ".join(
                render(_DANGER_CODE_TO_REFERRAL_KEY[c], self.language)
                for c in all_codes
                if c in _DANGER_CODE_TO_REFERRAL_KEY
            )
            await self._send_json({
                "type": "danger.alert",
                "codes": all_codes,
                "text": alert_text,
            })

            # Speak referral for each NEW code (FR-DS-03, SD-02)
            for code in new_codes:
                if code not in self._spoken_danger_codes:
                    if code in _DANGER_CODE_TO_REFERRAL_KEY:
                        referral_text = render(
                            _DANGER_CODE_TO_REFERRAL_KEY[code], self.language
                        )
                        await self._send_utterance(referral_text, "danger_referral")
                    self._spoken_danger_codes.add(code)

            # SD-03: in reading_back or awaiting_confirmation → replay read-back
            if self.state in (STATE_READING_BACK, STATE_AWAITING_CONFIRMATION):
                await self._send_form_update([])
                await self._do_read_back()

    # ------------------------------------------------------------------
    # Read-back and saving
    # ------------------------------------------------------------------

    async def _do_read_back(self) -> None:
        """Compose and speak the full read-back, then set awaiting_confirmation."""
        await self._set_state(STATE_READING_BACK)
        rb_text = _build_read_back_text(self.draft, self.language)
        await self._send_utterance(rb_text, "read_back")
        # Record the read-back completion
        self._last_readback = (self.draft.form_version, _now_utc_ms())
        await self._set_state(STATE_AWAITING_CONFIRMATION)

    async def _save(self, turn_id: str) -> None:
        """Persist the confirmed draft (FR-RB-03, SD-06).

        In this skeleton, DB persistence is left to the caller / Step 10.
        On failure, state reverts to awaiting_confirmation.
        """
        await self._set_state(STATE_SAVING)
        try:
            visit_id = str(uuid.uuid4())
            confirmed_at = _utc_iso()

            # Derive follow-ups
            follow_ups = derive_follow_ups(
                visit_id=visit_id,
                danger_sign_codes=list(self.draft.danger_sign_codes),
                next_visit_date=self.draft.next_visit_date,
                vaccinations_due=self.draft.vaccinations_due,
                visit_date=confirmed_at[:10],
                language=self.language,
            )

            fu_dicts = [
                {
                    "type": fu.type,
                    "description": fu.description,
                    "due_date": fu.due_date,
                }
                for fu in follow_ups
            ]

            # Send record.saved before the saved utterance (spec §5.1 ordering rule 5)
            await self._send_json({
                "type": "record.saved",
                "visit_id": visit_id,
                "follow_ups": fu_dicts,
            })

            _log.info(
                "visit.saved",
                session_id=self.session_id,
                turn_id=turn_id,
            )

            # Speak "saved" then follow-up list
            await self._speak("saved", "saved")
            if follow_ups:
                fu_lines = [render("fu_intro", self.language)]
                for fu in follow_ups:
                    fu_lines.append(fu.description)
                fu_text = " ".join(fu_lines)
                await self._send_utterance(fu_text, "followups")

            # Reset draft and return to collecting (spec §5.1 ordering rule 5)
            self.draft = VisitDraft()
            self._spoken_danger_codes = set()
            self._last_readback = None

            await self._send_form_update([])
            await self._set_state(STATE_COLLECTING)

        except Exception:
            # SD-06: failed save → back to awaiting_confirmation, speak didnt_catch
            _log.error(
                "save.failed",
                session_id=self.session_id,
                turn_id=turn_id,
            )
            await self._set_state(STATE_AWAITING_CONFIRMATION)
            await self._speak("didnt_catch", "notice")

    async def _discard(self) -> None:
        """Discard the current draft (FR-CR-03)."""
        self.draft = VisitDraft()
        self._spoken_danger_codes = set()
        self._last_readback = None

        await self._speak("discarded", "notice")
        await self._send_form_update([])
        await self._set_state(STATE_COLLECTING)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _ask_field(self, field: str) -> None:
        """Ask for one missing field (FR-FM-04)."""
        if field == "vaccinations_due":
            await self._speak("ask_vaccinations", "prompt")
        else:
            text = render("ask_missing", self.language, field_label=field)
            await self._send_utterance(text, "prompt")

    async def _speak(self, key: str, kind: str) -> None:
        """Render a locale key and send it as an utterance frame."""
        text = render(key, self.language)
        await self._send_utterance(text, kind)

    async def _send_utterance(self, text: str, kind: str) -> None:
        """Send agent.speak.start and agent.speak.end (no audio in this layer)."""
        utterance_id = str(uuid.uuid4())
        await self._send_json({
            "type": "agent.speak.start",
            "utterance_id": utterance_id,
            "kind": kind,
            "text": text,
            "sample_rate_hz": 16000,
            "interruptible": kind in ("prompt", "notice", "followups"),
        })
        # In production the speaker owns audio streaming; here we just close it.
        await self._send_json({
            "type": "agent.speak.end",
            "utterance_id": utterance_id,
            "status": "completed",
        })

    async def _set_state(self, new_state: str) -> None:
        self.state = new_state
        self.state_changed.set()
        self.state_changed.clear()
        await self._send_json({"type": "state.change", "state": new_state})

    async def _send_form_update(self, changed_fields: list[str]) -> None:
        form_data = self.draft.to_dict()
        missing = self.draft.missing_fields()
        await self._send_json({
            "type": "form.update",
            "form_version": self.draft.form_version,
            "form": form_data,
            "missing_fields": missing,
            "changed_fields": changed_fields,
        })
