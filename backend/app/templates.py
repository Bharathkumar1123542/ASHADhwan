"""backend/app/templates.py – Step 1

Locale loading, startup check, and string rendering.

The startup check (spec.md §8.1, NFR-L10N-01) runs at import time via
``check_locales()``, which is called by ``main.py`` during app startup.  It
raises ``RuntimeError`` if:
  - any key listed in ``REQUIRED_KEYS`` is missing from any locale file,
  - any value is an empty string, or
  - the ``{slot}`` names in a key differ between the three locale files.

``render(key, language, **slots)`` replaces ``{slot}`` placeholders and raises
``KeyError`` on a missing or unknown slot.

Slot renderers (spec.md §8.1):
  - ``id_spelled``: characters joined with ``", "``.
  - ``weight``: number without trailing zeros.
  - ``date_spoken``: ``{day} {month_name} {year}`` using the locale's ``months``
    array.
  - ``vaccinations``: empty list → ``none``; one entry → as-is; two or more →
    ``a, b and c`` using ``and_word``.
  - ``field_label``, ``visit_type_label``, ``sign_label``: looked up from the
    locale file by the sub-key convention defined in spec.md §8.1.
  - ``changed_fields_readback``: the ``rb_*`` strings of changed fields in
    read-back order, joined by a space.

NOTE: slot renderers that require runtime values (``date_spoken``,
``vaccinations``, ``id_spelled``, ``changed_fields_readback``) expect the
caller to pass the already-rendered string as the slot value, OR to use the
helper functions defined here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_LOCALES_DIR = _REPO_ROOT / "locales"
_LANGUAGES = ("en", "hi", "kn")

# Every key that must appear in all three locale files (spec.md §8.1).
REQUIRED_KEYS: tuple[str, ...] = (
    # Catalogue keys (prd.md §6.1)
    "ready",
    "ask_missing",
    "ask_vaccinations",
    "read_back_intro",
    "rb_beneficiary_id",
    "rb_beneficiary_name",
    "rb_visit_type",
    "rb_weight_kg",
    "rb_vaccinations_due",
    "rb_next_visit_date",
    "rb_notes",
    "rb_danger_line",
    "confirm_question",
    "corrected_read_back",
    "saved",
    "discarded",
    "fu_intro",
    "fu_phc_referral",
    "fu_next_visit",
    "fu_vaccination_due",
    "didnt_catch",
    "out_of_scope",
    "danger_referral_ds01",
    "danger_referral_ds02",
    "ui_start",
    "ui_end",
    # Slot value keys (spec.md §8.1 point 2)
    "label_beneficiary_id",
    "label_beneficiary_name",
    "label_visit_type",
    "label_weight_kg",
    "label_next_visit_date",
    "label_vaccinations_due",
    "label_notes",
    "visit_type_label_pregnancy_anc",
    "visit_type_label_newborn_postnatal",
    "visit_type_label_immunisation",
    "sign_label_ds01",
    "sign_label_ds02",
    # UI labels (spec.md §8.1 point 3)
    "ui_transcript",
    "ui_record",
    # Speech helpers (spec.md §8.1 point 4)
    "and_word",
    "none",
    "months",
)

# Slot name pattern (braces around word characters)
_SLOT_RE = re.compile(r"\{(\w+)\}")

# ---------------------------------------------------------------------------
# Module-level locale store
# ---------------------------------------------------------------------------

_locales: dict[str, dict[str, Any]] = {}


def _load_locales() -> dict[str, dict[str, Any]]:
    """Load all three locale JSON files and return a language→data mapping."""
    result: dict[str, dict[str, Any]] = {}
    for lang in _LANGUAGES:
        path = _LOCALES_DIR / f"{lang}.json"
        with path.open("r", encoding="utf-8") as fh:
            result[lang] = json.load(fh)
    return result


def _slot_names(template: str) -> set[str]:
    """Return the set of slot names in *template*."""
    return set(_SLOT_RE.findall(template))


def check_locales() -> None:
    """Run the startup check (spec.md §8.1, NFR-L10N-01).

    Loads all locale files, validates required keys, non-empty values, and
    slot-name consistency across languages.  Raises ``RuntimeError`` on the
    first violation.  Populates the module-level ``_locales`` store on success.
    """
    global _locales
    data = _load_locales()

    for key in REQUIRED_KEYS:
        slot_names_by_lang: dict[str, set[str]] = {}
        for lang in _LANGUAGES:
            if key not in data[lang]:
                raise RuntimeError(
                    f"Locale startup check failed: key {key!r} is missing from "
                    f"locales/{lang}.json"
                )
            value = data[lang][key]
            # ``months`` is a list; check each month name is non-empty.
            if key == "months":
                if not isinstance(value, list) or len(value) != 12:
                    raise RuntimeError(
                        f"Locale startup check failed: 'months' in locales/{lang}.json "
                        f"must be a list of 12 strings, got {value!r}"
                    )
                for i, month in enumerate(value):
                    if not isinstance(month, str) or not month.strip():
                        raise RuntimeError(
                            f"Locale startup check failed: 'months[{i}]' in "
                            f"locales/{lang}.json is empty"
                        )
                slot_names_by_lang[lang] = set()  # no slots in months list
                continue
            if not isinstance(value, str) or not value.strip():
                raise RuntimeError(
                    f"Locale startup check failed: key {key!r} in "
                    f"locales/{lang}.json is empty or not a string"
                )
            slot_names_by_lang[lang] = _slot_names(value)

        if key == "months":
            continue
        # Slot names must be identical across languages
        langs = list(_LANGUAGES)
        ref_slots = slot_names_by_lang[langs[0]]
        for lang in langs[1:]:
            if slot_names_by_lang[lang] != ref_slots:
                raise RuntimeError(
                    f"Locale startup check failed: slot names for key {key!r} "
                    f"differ between locales/{langs[0]}.json ({ref_slots}) and "
                    f"locales/{lang}.json ({slot_names_by_lang[lang]})"
                )

    _locales = data


# ---------------------------------------------------------------------------
# Slot renderers
# ---------------------------------------------------------------------------

def render_id_spelled(beneficiary_id: str, language: str) -> str:
    """Render ``id_spelled``: each character joined with ``", "`` (SD-19).

    If the locale has a ``char_names`` mapping, each character is replaced by
    its spoken name before joining.
    """
    locale = _locales.get(language, {})
    char_names: dict[str, str] = locale.get("char_names", {})
    parts = [char_names.get(ch, ch) for ch in beneficiary_id]
    return ", ".join(parts)


def render_weight(weight_kg: float) -> str:
    """Render weight without trailing zeros (spec.md §8.1)."""
    # Round to 2 decimals, then strip trailing zeros and a trailing dot.
    formatted = f"{weight_kg:.2f}".rstrip("0").rstrip(".")
    return formatted


def render_date_spoken(date_str: str, language: str) -> str:
    """Render ``date_spoken`` as ``{day} {month_name} {year}`` (spec.md §8.1).

    *date_str* is ``YYYY-MM-DD``.
    """
    locale = _locales.get(language, {})
    months: list[str] = locale.get("months", [])
    year, month, day = date_str.split("-")
    month_name = months[int(month) - 1] if months else month
    # Strip leading zero from day
    day_str = str(int(day))
    return f"{day_str} {month_name} {year}"


def render_vaccinations(entries: list[str], language: str) -> str:
    """Render vaccination list (spec.md §8.1).

    - Empty list → locale ``none``.
    - One entry → as-is.
    - Two or more → ``a, b and c`` using locale ``and_word``.
    """
    locale = _locales.get(language, {})
    if not entries:
        return locale.get("none", "none")
    if len(entries) == 1:
        return entries[0]
    and_word = locale.get("and_word", "and")
    return ", ".join(entries[:-1]) + f" {and_word} " + entries[-1]


# ---------------------------------------------------------------------------
# Core render function
# ---------------------------------------------------------------------------

def render(key: str, language: str, **slots: Any) -> str:
    """Render locale string *key* in *language* with the given *slots*.

    Raises:
        KeyError: if *language* or *key* is not found, or if a slot name in
            the template has no corresponding kwarg, or if an unexpected kwarg
            was passed.
    """
    if language not in _locales:
        raise KeyError(f"Unknown language: {language!r}")
    locale = _locales[language]
    if key not in locale:
        raise KeyError(f"Unknown locale key: {key!r} for language {language!r}")
    template: str = locale[key]
    expected_slots = _slot_names(template)

    # Resolve special slot keys that expand to locale sub-keys
    resolved: dict[str, str] = {}
    for slot_name, slot_value in slots.items():
        if slot_name == "field_label":
            label_key = f"label_{slot_value}"
            resolved["field_label"] = locale.get(label_key, slot_value)
        elif slot_name == "visit_type_label":
            vt_key = f"visit_type_label_{slot_value}"
            resolved["visit_type_label"] = locale.get(vt_key, slot_value)
        elif slot_name == "sign_label":
            # slot_value is a code like "ds01" or "DS-01"
            code = slot_value.lower().replace("-", "")
            sign_key = f"sign_label_{code}"
            resolved["sign_label"] = locale.get(sign_key, slot_value)
        elif slot_name == "id_spelled":
            # Caller may pass the raw ID; we render it here.
            resolved["id_spelled"] = render_id_spelled(str(slot_value), language)
        elif slot_name == "weight":
            resolved["weight"] = render_weight(float(slot_value))
        elif slot_name == "date_spoken":
            resolved["date_spoken"] = render_date_spoken(str(slot_value), language)
        elif slot_name == "vaccinations":
            resolved["vaccinations"] = render_vaccinations(
                list(slot_value) if slot_value else [], language
            )
        else:
            resolved[slot_name] = str(slot_value)

    # Check for unexpected slots
    extra = set(resolved) - expected_slots
    if extra:
        raise KeyError(
            f"Unexpected slot(s) {extra} for key {key!r} (expected {expected_slots})"
        )
    # Check for missing slots
    missing = expected_slots - set(resolved)
    if missing:
        raise KeyError(
            f"Missing slot(s) {missing} for key {key!r} in language {language!r}"
        )

    result = template
    for name, value in resolved.items():
        result = result.replace("{" + name + "}", value)
    return result
