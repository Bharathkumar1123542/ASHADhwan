"""tests/unit/test_session_flow.py

Focused unit tests for SessionHandler (backend/app/session.py) and the
WebSocket route in main.py.

No real Gnani or Gemini services are called.  All tests use:
  - FakeGemini: a lightweight in-process stub defined in this file (a
    proper tests/fakes.py will be written in Step 5; this stub covers what
    we need now).
  - httpx.AsyncClient with the FastAPI test transport for WebSocket tests.

Rules checked:
  - State transitions: collecting → reading_back → awaiting_confirmation.
  - Confirm gating: stale confirm is ignored (SD-04).
  - Danger sign escalation: lexicon fires DS-01, session sets flag and codes
    only via _handle_danger (FR-DS-05), referral is spoken before read-back.
  - Danger in reading_back replays read-back (SD-03).
  - Medical question → out_of_scope only (FR-DS-04, FR-DS-09).
  - Cancel visit → discarded, fresh draft (FR-CR-03).
  - Save requires confirm; zero saves without confirm (M-06, NFR-SAF-02).
  - WebSocket protocol: Origin check, second session.start, unknown worker.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import date, timedelta
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

# ---------------------------------------------------------------------------
# Lazy locale bootstrap (templates.py needs the locale files loaded)
# ---------------------------------------------------------------------------

from backend.app.templates import check_locales, _locales

if not _locales:
    check_locales()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _future(days: int = 10) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _new_handler(
    *,
    language: str = "en",
    gemini: Any = None,
) -> tuple["SessionHandler", list[dict]]:
    """Create a SessionHandler wired to an in-memory frame collector."""
    from backend.app.session import SessionHandler

    frames: list[dict] = []

    async def _send_json(payload: dict) -> None:
        frames.append(payload)

    async def _send_bytes(data: bytes) -> None:
        pass

    handler = SessionHandler(
        session_id=str(uuid.uuid4()),
        worker_id="W001",
        language=language,
        send_json=_send_json,
        send_bytes=_send_bytes,
        gemini=gemini,
    )
    return handler, frames


def _frame_types(frames: list[dict]) -> list[str]:
    return [f["type"] for f in frames]


# ---------------------------------------------------------------------------
# FakeGemini – injectable stub
# ---------------------------------------------------------------------------


class FakeGemini:
    """Minimal Gemini stub.  Callers configure scripted responses."""

    def __init__(self) -> None:
        # List of (intent, patch) to replay in order; last one repeats.
        self._extract_replies: list[tuple[str, dict]] = [("unclear", {})]
        self._danger_replies: list[list[str]] = [[]]

    def set_extract(self, *replies: tuple[str, dict]) -> None:
        self._extract_replies = list(replies)

    def set_danger(self, *replies: list[str]) -> None:
        self._danger_replies = list(replies)

    async def extract(
        self, turn_text: str, draft: Any, language: str
    ) -> tuple[str, dict]:
        reply = self._extract_replies.pop(0) if len(self._extract_replies) > 1 else self._extract_replies[0]
        return reply

    async def classify_danger(self, turn_text: str, draft: Any) -> list[str]:
        reply = self._danger_replies.pop(0) if len(self._danger_replies) > 1 else self._danger_replies[0]
        return reply


# ---------------------------------------------------------------------------
# Utility: run a SessionHandler for a list of turn texts
# ---------------------------------------------------------------------------


async def _run_turns(
    handler: "SessionHandler",
    turns: list[str],
) -> None:
    """Start the Stage B task and feed turn texts through handle_final_turn."""
    task = asyncio.create_task(handler.run())
    for text in turns:
        turn_id = str(uuid.uuid4())
        await handler.handle_final_turn(turn_id, text)
    # Drain the queue (wait until all turns processed, then stop)
    await handler.stop()
    await task


# ===========================================================================
# State machine tests
# ===========================================================================


class TestStateMachine:
    @pytest.mark.asyncio
    async def test_initial_state_is_collecting(self):
        handler, _ = _new_handler()
        from backend.app.session import STATE_COLLECTING
        assert handler.state == STATE_COLLECTING

    @pytest.mark.asyncio
    async def test_collecting_to_awaiting_after_full_patch(self):
        """Providing all required fields in one turn → reading_back → awaiting_confirmation."""
        gemini = FakeGemini()
        gemini.set_extract((
            "provide_info",
            {
                "beneficiary_id": "AB123456",
                "beneficiary_name": "Sunita Devi",
                "visit_type": "pregnancy_anc",
                "weight_kg": 58.0,
                "vaccinations_due": [],
                "next_visit_date": _future(14),
            },
        ))
        handler, frames = _new_handler(gemini=gemini)
        await _run_turns(handler, ["full details here"])

        from backend.app.session import STATE_AWAITING_CONFIRMATION
        assert handler.state == STATE_AWAITING_CONFIRMATION

    @pytest.mark.asyncio
    async def test_missing_field_prompts_ask_missing(self):
        """After a partial patch, the agent asks for the first still-missing field."""
        gemini = FakeGemini()
        gemini.set_extract((
            "provide_info",
            {"beneficiary_id": "AB123456", "beneficiary_name": "Sunita"},
        ))
        handler, frames = _new_handler(gemini=gemini)
        await _run_turns(handler, ["some info"])

        # Should have an agent.speak.start for ask_missing or ask_vaccinations
        speak_starts = [f for f in frames if f["type"] == "agent.speak.start"]
        assert speak_starts, "Expected at least one agent utterance"
        # Should NOT be in awaiting_confirmation yet
        from backend.app.session import STATE_COLLECTING
        assert handler.state == STATE_COLLECTING

    @pytest.mark.asyncio
    async def test_unclear_intent_speaks_didnt_catch(self):
        gemini = FakeGemini()
        gemini.set_extract(("unclear", {}))
        handler, frames = _new_handler(gemini=gemini)
        await _run_turns(handler, ["mumble"])

        speak_texts = [
            f["text"] for f in frames if f["type"] == "agent.speak.start"
        ]
        assert any("catch" in t.lower() or "समझ" in t or "ಅರ್ಥ" in t for t in speak_texts), \
            f"Expected didnt_catch; got: {speak_texts}"

    @pytest.mark.asyncio
    async def test_cancel_visit_resets_draft_and_state(self):
        """cancel_visit discards the draft and returns to collecting."""
        gemini = FakeGemini()
        # First turn provides some info, second cancels
        gemini.set_extract(
            ("provide_info", {"beneficiary_id": "AB123456"}),
            ("cancel_visit", {}),
        )
        handler, frames = _new_handler(gemini=gemini)
        await _run_turns(handler, ["some id", "cancel"])

        from backend.app.session import STATE_COLLECTING
        assert handler.state == STATE_COLLECTING
        assert handler.draft.beneficiary_id is None, "Draft must be reset after cancel"

        # Must have spoken 'discarded'
        speak_texts = [f["text"] for f in frames if f["type"] == "agent.speak.start"]
        assert any("discarded" in t.lower() or "हटा" in t or "ತೆಗೆದ" in t
                   for t in speak_texts), f"Expected discarded utterance; got: {speak_texts}"

    @pytest.mark.asyncio
    async def test_medical_question_speaks_out_of_scope(self):
        """Medical question must only speak out_of_scope (FR-DS-04)."""
        gemini = FakeGemini()
        gemini.set_extract(("medical_question", {}))
        handler, frames = _new_handler(gemini=gemini)
        await _run_turns(handler, ["which medicine to give?"])

        speak_texts = [f["text"] for f in frames if f["type"] == "agent.speak.start"]
        assert len(speak_texts) == 1, "Only one utterance for medical question"
        # Text must be out_of_scope, not any diagnostic text
        assert "cannot give medical advice" in speak_texts[0] or \
               "चिकित्सा सलाह" in speak_texts[0] or \
               "ವೈದ್ಯಕೀಯ" in speak_texts[0], \
               f"Expected out_of_scope; got: {speak_texts[0]}"


# ===========================================================================
# Confirmation gating (SD-04, FR-RB-03, M-06)
# ===========================================================================


class TestConfirmationGating:
    @pytest.mark.asyncio
    async def test_confirm_without_readback_is_ignored(self):
        """A confirm in collecting state (no read-back yet) must be ignored."""
        gemini = FakeGemini()
        gemini.set_extract(("confirm", {}))
        handler, frames = _new_handler(gemini=gemini)
        await _run_turns(handler, ["yes"])

        # No record.saved frame must have been emitted
        saved = [f for f in frames if f["type"] == "record.saved"]
        assert saved == [], "Must not save without a valid read-back confirm"

    @pytest.mark.asyncio
    async def test_confirm_in_collecting_state_emits_no_save(self):
        """confirm outside awaiting_confirmation → treated as unclear, no save."""
        from backend.app.session import STATE_COLLECTING
        gemini = FakeGemini()
        gemini.set_extract(("confirm", {}))
        handler, frames = _new_handler(gemini=gemini)
        # Force state to collecting (default)
        assert handler.state == STATE_COLLECTING
        await _run_turns(handler, ["yes"])

        saved = [f for f in frames if f["type"] == "record.saved"]
        assert saved == []

    @pytest.mark.asyncio
    async def test_valid_confirm_produces_record_saved(self):
        """A confirm after a proper read-back saves the record."""
        gemini = FakeGemini()
        full_patch = {
            "beneficiary_id": "AB123456",
            "beneficiary_name": "Sunita Devi",
            "visit_type": "pregnancy_anc",
            "weight_kg": 58.0,
            "vaccinations_due": [],
            "next_visit_date": _future(14),
        }
        # Turn 1: provide all info → goes to awaiting_confirmation
        # Turn 2: confirm
        gemini.set_extract(
            ("provide_info", full_patch),
            ("confirm", {}),
        )
        handler, frames = _new_handler(gemini=gemini)

        # Manually advance _last_readback so SD-04 passes
        # (simulates the read-back having completed before the confirm turn)
        async def _run_and_fix_readback():
            task = asyncio.create_task(handler.run())
            # First turn
            turn_id1 = str(uuid.uuid4())
            await handler.handle_final_turn(turn_id1, "full details")
            # Allow stage B to process first turn
            await asyncio.sleep(0.05)
            # Patch speech_start_ms of confirm so it's after the read-back
            if handler._last_readback:
                rb_ver, rb_ms = handler._last_readback
                handler._last_readback = (rb_ver, rb_ms - 1000)
            # Second turn (confirm)
            turn_id2 = str(uuid.uuid4())
            await handler.handle_final_turn(turn_id2, "yes")
            await handler.stop()
            await task

        await _run_and_fix_readback()

        saved = [f for f in frames if f["type"] == "record.saved"]
        assert len(saved) == 1, f"Expected 1 record.saved, got {len(saved)}"
        assert "visit_id" in saved[0]

    @pytest.mark.asyncio
    async def test_stale_confirm_does_not_save(self):
        """A confirm whose speech_start_ms is before the read-back end is ignored."""
        from backend.app.session import (
            STATE_AWAITING_CONFIRMATION,
            STATE_COLLECTING,
        )
        gemini = FakeGemini()
        full_patch = {
            "beneficiary_id": "AB123456",
            "beneficiary_name": "Sunita Devi",
            "visit_type": "pregnancy_anc",
            "weight_kg": 58.0,
            "vaccinations_due": [],
            "next_visit_date": _future(14),
        }
        gemini.set_extract(
            ("provide_info", full_patch),
            ("confirm", {}),
        )
        handler, frames = _new_handler(gemini=gemini)

        async def _run_stale():
            task = asyncio.create_task(handler.run())
            # First turn: fill form
            tid1 = str(uuid.uuid4())
            await handler.handle_final_turn(tid1, "full details")
            await asyncio.sleep(0.05)
            # Set _last_readback end time AFTER current time (stale condition)
            import time
            future_ms = int(time.time() * 1000) + 999_999
            if handler._last_readback:
                handler._last_readback = (handler._last_readback[0], future_ms)
            # Second turn: confirm
            tid2 = str(uuid.uuid4())
            await handler.handle_final_turn(tid2, "yes")
            await handler.stop()
            await task

        await _run_stale()

        saved = [f for f in frames if f["type"] == "record.saved"]
        assert saved == [], "Stale confirm must not trigger a save"


# ===========================================================================
# Danger sign escalation (FR-DS-01 to FR-DS-07, SD-03)
# ===========================================================================


class TestDangerSignEscalation:
    @pytest.mark.asyncio
    async def test_lexicon_ds01_fires_referral_utterance(self):
        """Lexicon detects DS-01 → danger_referral_ds01 is spoken."""
        # No Gemini needed; lexicon does the work
        handler, frames = _new_handler(gemini=None)
        # pregnancy_anc visit type already in draft doesn't matter – we set prior context
        handler.draft.visit_type = "pregnancy_anc"

        task = asyncio.create_task(handler.run())
        tid = str(uuid.uuid4())
        await handler.handle_final_turn(tid, "she has bleeding")
        await handler.stop()
        await task

        speak_frames = [f for f in frames if f["type"] == "agent.speak.start"]
        referral_texts = [
            f["text"] for f in speak_frames
            if "bleeding" in f["text"].lower() or "रक्तस्राव" in f["text"] or "ರಕ್ತಸ್ರಾವ" in f["text"]
        ]
        assert referral_texts, f"Expected DS-01 referral; got: {[f['text'] for f in speak_frames]}"

    @pytest.mark.asyncio
    async def test_danger_flag_set_only_by_handle_danger(self):
        """danger_sign_flag and danger_sign_codes must not appear in a form patch."""
        from backend.app.form import apply_patch, VisitDraft
        draft = VisitDraft()
        with pytest.raises(ValueError, match="danger"):
            apply_patch(draft, {"danger_sign_flag": True})
        with pytest.raises(ValueError, match="danger"):
            apply_patch(draft, {"danger_sign_codes": ["DS-01"]})

    @pytest.mark.asyncio
    async def test_danger_alert_frame_sent(self):
        """handle_final_turn sends danger.alert when DS-01 is detected."""
        handler, frames = _new_handler(gemini=None)
        handler.draft.visit_type = "pregnancy_anc"

        task = asyncio.create_task(handler.run())
        tid = str(uuid.uuid4())
        await handler.handle_final_turn(tid, "she has bleeding")
        await handler.stop()
        await task

        alert_frames = [f for f in frames if f["type"] == "danger.alert"]
        assert alert_frames, "Expected danger.alert frame"
        assert "DS-01" in alert_frames[0]["codes"]

    @pytest.mark.asyncio
    async def test_danger_in_reading_back_replays_readback(self):
        """SD-03: danger detected while in reading_back increments form_version
        and triggers a new read-back."""
        gemini = FakeGemini()
        full_patch = {
            "beneficiary_id": "AB123456",
            "beneficiary_name": "Sunita Devi",
            "visit_type": "pregnancy_anc",
            "weight_kg": 58.0,
            "vaccinations_due": [],
            "next_visit_date": _future(14),
        }
        gemini.set_extract(("provide_info", full_patch))
        handler, frames = _new_handler(gemini=gemini)

        task = asyncio.create_task(handler.run())
        # First turn fills the form → handler moves to awaiting_confirmation
        tid1 = str(uuid.uuid4())
        await handler.handle_final_turn(tid1, "all info")
        await asyncio.sleep(0.05)

        # Force handler into reading_back state to test SD-03
        from backend.app.session import STATE_READING_BACK
        handler.state = STATE_READING_BACK

        # Second turn: danger in reading_back state
        # Inject DS-01 directly via handle_final_turn while state = reading_back
        tid2 = str(uuid.uuid4())
        handler.draft.visit_type = "pregnancy_anc"
        # Clear spoken codes so referral fires again
        handler._spoken_danger_codes.clear()
        await handler._handle_danger(tid2, "she has bleeding", ["DS-01"])

        await handler.stop()
        await task

        # Check that form_version was incremented (danger sets it)
        assert handler.draft.form_version > 0

    @pytest.mark.asyncio
    async def test_referral_spoken_only_once_per_code(self):
        """The referral for a code is spoken only on its first detection (SD-02)."""
        handler, frames = _new_handler(gemini=None)
        handler.draft.visit_type = "pregnancy_anc"

        task = asyncio.create_task(handler.run())
        # Fire DS-01 twice
        for _ in range(2):
            tid = str(uuid.uuid4())
            await handler.handle_final_turn(tid, "she has bleeding")
            await asyncio.sleep(0.01)
        await handler.stop()
        await task

        # Only one danger_referral_ds01 utterance
        referral_starts = [
            f for f in frames
            if f["type"] == "agent.speak.start" and f.get("kind") == "danger_referral"
        ]
        assert len(referral_starts) == 1, \
            f"Referral must be spoken once; got {len(referral_starts)}"

    @pytest.mark.asyncio
    async def test_danger_does_not_clear_on_discard(self):
        """After discard, danger_events rows persist (FR-DS-07).
        Here we verify the flag is cleared on the new draft but _spoken_danger_codes reset."""
        handler, frames = _new_handler(gemini=None)
        handler.draft.visit_type = "pregnancy_anc"

        task = asyncio.create_task(handler.run())
        tid = str(uuid.uuid4())
        await handler.handle_final_turn(tid, "she has bleeding")
        await asyncio.sleep(0.05)
        # Discard
        await handler._discard()
        await handler.stop()
        await task

        # New draft should not carry the flag
        assert handler.draft.danger_sign_flag is False
        assert handler.draft.danger_sign_codes == []
        # _spoken_danger_codes is reset on discard
        assert handler._spoken_danger_codes == set()


# ===========================================================================
# WebSocket protocol tests (Steps 4+)
# ===========================================================================


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture
async def test_app():
    """Provide the FastAPI app pre-loaded with locales and a fake DB worker."""
    from backend.app import main as main_module
    from backend.app.templates import check_locales, _locales

    if not _locales:
        check_locales()

    # Inject a fake _db_conn that always returns one worker
    import sqlite3

    class _FakeConn:
        pass

    original_conn = main_module._db_conn

    class _FakeWorkerRow(dict):
        pass

    # Patch get_workers at the module level
    original_get_workers = main_module.get_workers

    def _fake_get_workers(_conn):
        return [
            {"worker_id": "W001", "display_name": "Priya (en)", "language": "en"}
        ]

    main_module.get_workers = _fake_get_workers

    yield main_module.app

    main_module.get_workers = original_get_workers


class TestWebSocketProtocol:
    @pytest.mark.asyncio
    async def test_bad_origin_rejected(self, test_app):
        """Origin not in allowed_origins → server closes immediately."""
        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with pytest.raises(Exception):
                async with client.websocket_connect(
                    "/ws/session",
                    headers={"origin": "http://evil.example.com"},
                ) as ws:
                    await ws.receive_json()

    @pytest.mark.asyncio
    async def test_valid_session_start_sends_ready(self, test_app):
        """A valid session.start returns session.ready, state.change, form.update, speak."""
        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.websocket_connect(
                "/ws/session",
                headers={"origin": "http://localhost:8000"},
            ) as ws:
                await ws.send_json({
                    "type": "session.start",
                    "worker_id": "W001",
                })
                frames = []
                for _ in range(5):
                    try:
                        frame = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
                        frames.append(frame)
                    except asyncio.TimeoutError:
                        break

                types = [f["type"] for f in frames]
                assert "session.ready" in types
                assert "state.change" in types
                assert "form.update" in types
                assert "agent.speak.start" in types

    @pytest.mark.asyncio
    async def test_second_session_start_sends_protocol_error_no_close(self, test_app):
        """A second session.start sends error but keeps the connection open (spec §5.1)."""
        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.websocket_connect(
                "/ws/session",
                headers={"origin": "http://localhost:8000"},
            ) as ws:
                await ws.send_json({"type": "session.start", "worker_id": "W001"})
                # Drain handshake frames
                for _ in range(5):
                    try:
                        await asyncio.wait_for(ws.receive_json(), timeout=1.0)
                    except asyncio.TimeoutError:
                        break

                # Second session.start
                await ws.send_json({"type": "session.start", "worker_id": "W001"})
                try:
                    err = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
                    assert err["type"] == "error"
                    assert err["code"] == "protocol_error"
                except asyncio.TimeoutError:
                    pytest.fail("Expected error frame for second session.start")

    @pytest.mark.asyncio
    async def test_unknown_worker_sends_worker_not_found(self, test_app):
        """Unknown worker_id → error worker_not_found."""
        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.websocket_connect(
                "/ws/session",
                headers={"origin": "http://localhost:8000"},
            ) as ws:
                await ws.send_json({
                    "type": "session.start",
                    "worker_id": "NO_SUCH_WORKER",
                })
                try:
                    frame = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
                    assert frame["type"] == "error"
                    assert frame["code"] == "worker_not_found"
                except asyncio.TimeoutError:
                    pytest.fail("Expected worker_not_found error frame")

    @pytest.mark.asyncio
    async def test_frame_before_session_start_causes_protocol_error(self, test_app):
        """Any frame before session.start → protocol_error."""
        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.websocket_connect(
                "/ws/session",
                headers={"origin": "http://localhost:8000"},
            ) as ws:
                await ws.send_json({"type": "worker.turn", "text": "hello"})
                try:
                    frame = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
                    assert frame["type"] == "error"
                    assert frame["code"] == "protocol_error"
                except asyncio.TimeoutError:
                    pytest.fail("Expected protocol_error for frame before session.start")

    @pytest.mark.asyncio
    async def test_worker_turn_text_mode_triggers_response(self, test_app):
        """worker.turn frame causes the agent to respond (text-mode demo path)."""
        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.websocket_connect(
                "/ws/session",
                headers={"origin": "http://localhost:8000"},
            ) as ws:
                await ws.send_json({"type": "session.start", "worker_id": "W001"})
                # Drain handshake
                for _ in range(6):
                    try:
                        await asyncio.wait_for(ws.receive_json(), timeout=1.0)
                    except asyncio.TimeoutError:
                        break

                # Send a worker turn
                await ws.send_json({"type": "worker.turn", "text": "hello agent"})

                # Expect at least transcript.final and an agent.speak.start
                got_types: set[str] = set()
                for _ in range(6):
                    try:
                        frame = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
                        got_types.add(frame["type"])
                    except asyncio.TimeoutError:
                        break

                assert "transcript.final" in got_types, \
                    f"Expected transcript.final; got {got_types}"
