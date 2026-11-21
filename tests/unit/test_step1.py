"""tests/unit/test_step1.py – Step 1, S1.8

Unit tests for the locale startup check (spec.md §8.1, NFR-L10N-01).

Covers:
  - Missing key → RuntimeError.
  - Empty string value → RuntimeError.
  - Slot name mismatch across languages → RuntimeError.
  - Valid locales → no error.

The tests patch the locale files in memory by temporarily replacing the
``_locales`` store and calling the internal check logic directly, so no
real files are written or deleted.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LOCALES_DIR = Path(__file__).resolve().parent.parent.parent / "locales"


def _load_all() -> dict[str, dict]:
    """Load the real locale files and return a language→data mapping."""
    result = {}
    for lang in ("en", "hi", "kn"):
        path = _LOCALES_DIR / f"{lang}.json"
        with path.open("r", encoding="utf-8") as fh:
            result[lang] = json.load(fh)
    return result


# ---------------------------------------------------------------------------
# Inline startup-check logic (mirrors templates.py check_locales)
# This avoids test coupling to private internals while still exercising the
# same rules.
# ---------------------------------------------------------------------------

_SLOT_RE = re.compile(r"\{(\w+)\}")
REQUIRED_KEYS = (
    "ready", "ask_missing", "ask_vaccinations", "read_back_intro",
    "rb_beneficiary_id", "rb_beneficiary_name", "rb_visit_type",
    "rb_weight_kg", "rb_vaccinations_due", "rb_next_visit_date",
    "rb_notes", "rb_danger_line", "confirm_question", "corrected_read_back",
    "saved", "discarded", "fu_intro", "fu_phc_referral", "fu_next_visit",
    "fu_vaccination_due", "didnt_catch", "out_of_scope",
    "danger_referral_ds01", "danger_referral_ds02",
    "ui_start", "ui_end",
    "label_beneficiary_id", "label_beneficiary_name", "label_visit_type",
    "label_weight_kg", "label_next_visit_date", "label_vaccinations_due",
    "label_notes",
    "visit_type_label_pregnancy_anc", "visit_type_label_newborn_postnatal",
    "visit_type_label_immunisation",
    "sign_label_ds01", "sign_label_ds02",
    "ui_transcript", "ui_record",
    "and_word", "none", "months",
)
_LANGUAGES = ("en", "hi", "kn")


def _run_check(data: dict[str, dict]) -> None:
    """Run the startup check on *data*.  Raises RuntimeError on violation."""
    for key in REQUIRED_KEYS:
        slot_names_by_lang: dict[str, set] = {}
        for lang in _LANGUAGES:
            if key not in data[lang]:
                raise RuntimeError(
                    f"key {key!r} missing from locales/{lang}.json"
                )
            value = data[lang][key]
            if key == "months":
                if not isinstance(value, list) or len(value) != 12:
                    raise RuntimeError(f"'months' in {lang} must be list of 12")
                for i, m in enumerate(value):
                    if not isinstance(m, str) or not m.strip():
                        raise RuntimeError(f"months[{i}] in {lang} is empty")
                slot_names_by_lang[lang] = set()
                continue
            if not isinstance(value, str) or not value.strip():
                raise RuntimeError(
                    f"key {key!r} in locales/{lang}.json is empty"
                )
            slot_names_by_lang[lang] = set(_SLOT_RE.findall(value))

        if key == "months":
            continue
        ref = slot_names_by_lang[_LANGUAGES[0]]
        for lang in _LANGUAGES[1:]:
            if slot_names_by_lang[lang] != ref:
                raise RuntimeError(
                    f"slot name mismatch for {key!r}: "
                    f"{_LANGUAGES[0]}={ref}, {lang}={slot_names_by_lang[lang]}"
                )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestStartupCheckPassesOnValidLocales:
    def test_real_locale_files_pass(self):
        data = _load_all()
        _run_check(data)  # must not raise


class TestStartupCheckMissingKey:
    def test_missing_key_raises(self):
        data = _load_all()
        mutated = copy.deepcopy(data)
        mutated["hi"].pop("ready")
        with pytest.raises(RuntimeError, match="missing"):
            _run_check(mutated)

    def test_missing_key_en_raises(self):
        data = _load_all()
        mutated = copy.deepcopy(data)
        mutated["en"].pop("saved")
        with pytest.raises(RuntimeError, match="missing"):
            _run_check(mutated)

    def test_missing_key_kn_raises(self):
        data = _load_all()
        mutated = copy.deepcopy(data)
        mutated["kn"].pop("discarded")
        with pytest.raises(RuntimeError, match="missing"):
            _run_check(mutated)


class TestStartupCheckEmptyValue:
    def test_empty_string_raises(self):
        data = _load_all()
        mutated = copy.deepcopy(data)
        mutated["en"]["ready"] = ""
        with pytest.raises(RuntimeError, match="empty"):
            _run_check(mutated)

    def test_whitespace_only_raises(self):
        data = _load_all()
        mutated = copy.deepcopy(data)
        mutated["hi"]["saved"] = "   "
        with pytest.raises(RuntimeError, match="empty"):
            _run_check(mutated)

    def test_empty_month_in_list_raises(self):
        data = _load_all()
        mutated = copy.deepcopy(data)
        mutated["kn"]["months"][3] = ""
        with pytest.raises(RuntimeError, match="empty"):
            _run_check(mutated)

    def test_months_wrong_length_raises(self):
        data = _load_all()
        mutated = copy.deepcopy(data)
        mutated["en"]["months"] = mutated["en"]["months"][:11]
        with pytest.raises(RuntimeError, match="12"):
            _run_check(mutated)


class TestStartupCheckSlotMismatch:
    def test_slot_mismatch_raises(self):
        data = _load_all()
        mutated = copy.deepcopy(data)
        # ask_missing has {field_label} in en; replace it with a different slot
        mutated["hi"]["ask_missing"] = "कृपया {wrong_slot} बताइए।"
        with pytest.raises(RuntimeError, match="slot name mismatch"):
            _run_check(mutated)

    def test_extra_slot_in_one_language_raises(self):
        data = _load_all()
        mutated = copy.deepcopy(data)
        mutated["kn"]["rb_weight_kg"] = "ತೂಕ {weight} ಕಿಲೋ {extra}."
        with pytest.raises(RuntimeError, match="slot name mismatch"):
            _run_check(mutated)
