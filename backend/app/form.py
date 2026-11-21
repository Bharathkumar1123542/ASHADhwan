"""backend/app/form.py – Step 2 (prototype)

Visit draft model, field validation, spoken correction patch application,
and follow-up generation.

Rules enforced here (from prd.md / spec.md):
  - Patch semantics: a key present replaces the field; absent → unchanged;
    null → absent (FR-FM-02).
  - Validation: each field rule from FR-FM-01 / A-06 / A-07.
  - ``danger_sign_flag`` and ``danger_sign_codes`` are NOT writable via patch
    (FR-DS-05).  Any attempt to include them in a patch raises ``ValueError``.
  - Grams-to-kg conversion for weight_kg (FR-FM-01).
  - ``vaccinations_due`` three states: None (unanswered), [] (none due),
    [str, ...] (names).
  - ``next_visit_date`` must be today or later in Asia/Kolkata, and within
    next_visit_max_days days (from config/app.yaml).
  - Follow-ups derived from the saved record only (FR-FU-01, FR-FU-02).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Constants (defaults mirror config/app.yaml; real code should read from
# config.py at startup, but form.py is used in unit tests without the full
# server so we provide safe defaults here)
# ---------------------------------------------------------------------------

_TZ = ZoneInfo("Asia/Kolkata")
_WEIGHT_MIN = 0.5
_WEIGHT_MAX = 150.0
_NAME_MAX = 100
_VACC_MAX = 10
_VACC_ENTRY_MAX = 60
_NOTES_MAX = 1000
_NEXT_VISIT_MAX_DAYS = 365
_ID_RE = re.compile(r"^[A-Z0-9]{6,16}$")

VALID_VISIT_TYPES = ("pregnancy_anc", "newborn_postnatal", "immunisation")

# Fields that danger handling owns — patch must NOT touch them (FR-DS-05)
_DANGER_FIELDS = {"danger_sign_flag", "danger_sign_codes"}

# Required fields (FR-FM-04 order)
REQUIRED_FIELDS: tuple[str, ...] = (
    "beneficiary_id",
    "beneficiary_name",
    "visit_type",
    "weight_kg",
    "next_visit_date",
    "vaccinations_due",
)

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


class ValidationError(ValueError):
    """Raised when a single field value fails its rule."""
    def __init__(self, field_name: str, reason: str) -> None:
        super().__init__(f"{field_name}: {reason}")
        self.field_name = field_name
        self.reason = reason


def _today_ist() -> date:
    """Return today's date in Asia/Kolkata."""
    from datetime import datetime
    return datetime.now(_TZ).date()


def _validate_beneficiary_id(value: Any) -> str:
    """Uppercase, strip spaces/hyphens, then check regex."""
    if not isinstance(value, str):
        raise ValidationError("beneficiary_id", "must be a string")
    cleaned = value.upper().replace(" ", "").replace("-", "")
    if not _ID_RE.match(cleaned):
        raise ValidationError(
            "beneficiary_id",
            f"must match ^[A-Z0-9]{{6,16}}$, got {cleaned!r}",
        )
    return cleaned


def _validate_beneficiary_name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("beneficiary_name", "must be a non-empty string")
    name = value.strip()
    if len(name) > _NAME_MAX:
        raise ValidationError(
            "beneficiary_name", f"must be at most {_NAME_MAX} characters"
        )
    return name


def _validate_visit_type(value: Any) -> str:
    if value not in VALID_VISIT_TYPES:
        raise ValidationError(
            "visit_type",
            f"must be one of {VALID_VISIT_TYPES}, got {value!r}",
        )
    return value


def _validate_weight_kg(value: Any) -> float:
    """Accept kg (float/int) or grams (>= 200 treated as grams if > 150)."""
    if isinstance(value, str):
        value = value.strip()
        try:
            value = float(value)
        except ValueError:
            raise ValidationError("weight_kg", f"cannot parse {value!r} as a number")
    if not isinstance(value, (int, float)):
        raise ValidationError("weight_kg", "must be a number")
    # Convert grams if the value is implausibly large for kg
    if value > _WEIGHT_MAX:
        kg = round(value / 1000.0, 2)
    else:
        kg = round(float(value), 2)
    if not (_WEIGHT_MIN <= kg <= _WEIGHT_MAX):
        raise ValidationError(
            "weight_kg",
            f"must be between {_WEIGHT_MIN} and {_WEIGHT_MAX} kg, got {kg}",
        )
    return kg


def _validate_vaccinations_due(value: Any) -> list[str]:
    """Expect a list of strings (possibly empty).  Null handled upstream."""
    if not isinstance(value, list):
        raise ValidationError("vaccinations_due", "must be a list of strings")
    if len(value) > _VACC_MAX:
        raise ValidationError(
            "vaccinations_due", f"at most {_VACC_MAX} entries, got {len(value)}"
        )
    result: list[str] = []
    for i, entry in enumerate(value):
        if not isinstance(entry, str) or not entry.strip():
            raise ValidationError(
                "vaccinations_due", f"entry {i} must be a non-empty string"
            )
        trimmed = entry.strip()
        if len(trimmed) > _VACC_ENTRY_MAX:
            raise ValidationError(
                "vaccinations_due",
                f"entry {i} must be at most {_VACC_ENTRY_MAX} characters",
            )
        result.append(trimmed)
    return result


def _validate_next_visit_date(value: Any) -> str:
    """Accept YYYY-MM-DD string; must be today..today+365 in IST."""
    if not isinstance(value, str):
        raise ValidationError("next_visit_date", "must be a YYYY-MM-DD string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValidationError(
            "next_visit_date", f"cannot parse {value!r} as YYYY-MM-DD"
        )
    today = _today_ist()
    if parsed < today:
        raise ValidationError(
            "next_visit_date", f"{value} is in the past (today is {today})"
        )
    if parsed > today + timedelta(days=_NEXT_VISIT_MAX_DAYS):
        raise ValidationError(
            "next_visit_date",
            f"{value} is more than {_NEXT_VISIT_MAX_DAYS} days from today",
        )
    return value


def _validate_notes(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError("notes", "must be a string or null")
    if len(value) > _NOTES_MAX:
        raise ValidationError("notes", f"at most {_NOTES_MAX} characters")
    return value if value.strip() else None


# Map field name → validator
_VALIDATORS: dict[str, Any] = {
    "beneficiary_id": _validate_beneficiary_id,
    "beneficiary_name": _validate_beneficiary_name,
    "visit_type": _validate_visit_type,
    "weight_kg": _validate_weight_kg,
    "vaccinations_due": _validate_vaccinations_due,
    "next_visit_date": _validate_next_visit_date,
    "notes": _validate_notes,
}

# ---------------------------------------------------------------------------
# Draft model
# ---------------------------------------------------------------------------


@dataclass
class VisitDraft:
    """In-memory visit draft.  Only confirmed drafts are written to the DB."""

    # Fields the worker fills in
    beneficiary_id: str | None = None
    beneficiary_name: str | None = None
    visit_type: str | None = None
    weight_kg: float | None = None
    vaccinations_due: list[str] | None = None   # None = unanswered
    next_visit_date: str | None = None
    notes: str | None = None

    # Set only by danger handling (FR-DS-05)
    danger_sign_flag: bool = False
    danger_sign_codes: list[str] = field(default_factory=list)

    # Tracks the number of accepted patches (FR-RB-03, SD-04)
    form_version: int = 0

    # Accumulated worker turn texts for danger context (spec.md §6.3)
    _worker_turns: list[str] = field(default_factory=list, repr=False)

    # Danger event IDs created for this draft (spec.md §4 persistence rules)
    _danger_event_ids: list[str] = field(default_factory=list, repr=False)

    def missing_fields(self) -> list[str]:
        """Return required fields still unset, in FR-FM-04 order."""
        result: list[str] = []
        for f in REQUIRED_FIELDS:
            value = getattr(self, f)
            if value is None:
                result.append(f)
        return result

    def is_complete(self) -> bool:
        """True when all required fields are set and valid."""
        return len(self.missing_fields()) == 0

    def to_dict(self) -> dict[str, Any]:
        """Serialise the public fields (excludes private turn text)."""
        return {
            "beneficiary_id": self.beneficiary_id,
            "beneficiary_name": self.beneficiary_name,
            "visit_type": self.visit_type,
            "weight_kg": self.weight_kg,
            "vaccinations_due": self.vaccinations_due,
            "next_visit_date": self.next_visit_date,
            "notes": self.notes,
            "danger_sign_flag": self.danger_sign_flag,
            "danger_sign_codes": list(self.danger_sign_codes),
        }

    def add_worker_turn(self, text: str) -> None:
        """Append a final worker turn text for danger context accumulation."""
        self._worker_turns.append(text)

    def worker_context(self) -> str:
        """All prior worker turn texts joined by newlines (for danger §6.3)."""
        return "\n".join(self._worker_turns)


# ---------------------------------------------------------------------------
# Patch application
# ---------------------------------------------------------------------------


def apply_patch(
    draft: VisitDraft, patch: dict[str, Any]
) -> tuple[list[str], list[str]]:
    """Apply *patch* to *draft* with replace semantics (FR-FM-02).

    - Keys present in patch replace the field.
    - Keys absent in patch leave the field unchanged.
    - Null values are treated as absent (spec.md gotchas).
    - ``danger_sign_flag`` / ``danger_sign_codes`` in patch raise ``ValueError``
      (FR-DS-05).

    Returns:
        changed_fields: list of field names that were updated.
        rejected_fields: list of field names whose values failed validation.

    The draft's ``form_version`` is incremented by 1 for each field that
    changes (callers that want a single atomic increment should increment once
    after calling this; here we increment once total if anything changed,
    matching spec SD-03).
    """
    # Guard: danger fields must not be patched
    forbidden = _DANGER_FIELDS & patch.keys()
    if forbidden:
        raise ValueError(
            f"Patch must not contain danger fields: {forbidden} (FR-DS-05)"
        )

    changed: list[str] = []
    rejected: list[str] = []

    for field_name, raw_value in patch.items():
        if raw_value is None:
            # Null = absent; leave draft field unchanged
            continue
        if field_name not in _VALIDATORS:
            # Unknown field — ignore silently (future-proof)
            continue

        try:
            validated = _VALIDATORS[field_name](raw_value)
        except ValidationError:
            rejected.append(field_name)
            continue

        old_value = getattr(draft, field_name)
        if old_value != validated:
            setattr(draft, field_name, validated)
            changed.append(field_name)

    if changed:
        draft.form_version += 1

    return changed, rejected


# ---------------------------------------------------------------------------
# Follow-up generation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FollowUp:
    """A single follow-up item derived from a saved visit record."""
    follow_up_id: str
    visit_id: str
    type: str           # "phc_referral" | "next_visit" | "vaccination_due"
    description: str    # rendered in the worker's language (caller supplies)
    due_date: str | None


def derive_follow_ups(
    visit_id: str,
    danger_sign_codes: list[str],
    next_visit_date: str | None,
    vaccinations_due: list[str] | None,
    visit_date: str,
    language: str,
    *,
    render_fn: Any = None,
) -> list[FollowUp]:
    """Derive the follow-up list from a saved record (FR-FU-01, FR-FU-02).

    Order: phc_referral (one per danger code), next_visit (one),
    vaccination_due (one per vaccination).

    ``render_fn`` is called as ``render_fn(key, language, **slots)`` to
    produce the description string.  If not provided, a plain English fallback
    is used (useful in tests that do not load the locale stack).

    ``visit_date`` is YYYY-MM-DD (SD-01: due_date for phc_referral).
    """

    def _desc(key: str, **slots: Any) -> str:
        if render_fn is not None:
            return render_fn(key, language, **slots)
        # Minimal fallback for tests
        _fallbacks = {
            "fu_phc_referral": "Refer to PHC now: {sign_label}",
            "fu_next_visit": "Next visit on {date_spoken}",
            "fu_vaccination_due": "Vaccination due: {name}",
        }
        tmpl = _fallbacks.get(key, key)
        for k, v in slots.items():
            tmpl = tmpl.replace("{" + k + "}", str(v))
        return tmpl

    items: list[FollowUp] = []

    # --- phc_referral: one per danger code, due today (the visit date) ---
    _code_labels = {
        "DS-01": "bleeding in pregnancy",
        "DS-02": "high fever in a newborn",
    }
    for code in danger_sign_codes:
        label = _code_labels.get(code, code)
        items.append(FollowUp(
            follow_up_id=str(uuid.uuid4()),
            visit_id=visit_id,
            type="phc_referral",
            description=_desc(
                "fu_phc_referral",
                sign_label=code,
            ),
            due_date=visit_date,
        ))

    # --- next_visit: one, due on next_visit_date ---
    if next_visit_date:
        items.append(FollowUp(
            follow_up_id=str(uuid.uuid4()),
            visit_id=visit_id,
            type="next_visit",
            description=_desc(
                "fu_next_visit",
                date_spoken=next_visit_date,
            ),
            due_date=next_visit_date,
        ))

    # --- vaccination_due: one per entry ---
    for vacc_name in (vaccinations_due or []):
        items.append(FollowUp(
            follow_up_id=str(uuid.uuid4()),
            visit_id=visit_id,
            type="vaccination_due",
            description=_desc(
                "fu_vaccination_due",
                name=vacc_name,
            ),
            due_date=None,
        ))

    return items
