"""tests/unit/test_core_proto.py – prototype core unit tests

Covers:
  - VisitDraft field defaults and missing_fields() order
  - apply_patch(): happy path, partial patch, null=absent, gram conversion,
    invalid value rejected, danger field blocked, form_version increments
  - Lexicon matcher: DS-01 (English, Hindi, pregnancy context, negation),
    DS-02 (English, Hindi, Kannada, intensity window, negation),
    no false positives
  - derive_follow_ups(): phc first, then next_visit, then vaccinations

No real services are called. No real API keys are needed.
"""

from __future__ import annotations

import pytest
from datetime import date, timedelta
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TZ = ZoneInfo("Asia/Kolkata")


def _future_date(days: int = 10) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


# ---------------------------------------------------------------------------
# form.py imports
# ---------------------------------------------------------------------------

from backend.app.form import (
    VisitDraft,
    apply_patch,
    derive_follow_ups,
    ValidationError,
    REQUIRED_FIELDS,
)


# ===========================================================================
# VisitDraft
# ===========================================================================


class TestVisitDraft:
    def test_all_fields_start_none(self):
        d = VisitDraft()
        assert d.beneficiary_id is None
        assert d.beneficiary_name is None
        assert d.visit_type is None
        assert d.weight_kg is None
        assert d.vaccinations_due is None
        assert d.next_visit_date is None
        assert d.notes is None

    def test_danger_fields_default(self):
        d = VisitDraft()
        assert d.danger_sign_flag is False
        assert d.danger_sign_codes == []

    def test_form_version_starts_zero(self):
        assert VisitDraft().form_version == 0

    def test_missing_fields_order_matches_required(self):
        d = VisitDraft()
        assert d.missing_fields() == list(REQUIRED_FIELDS)

    def test_missing_fields_partial(self):
        d = VisitDraft(
            beneficiary_id="AB123456",
            beneficiary_name="Sunita",
            visit_type="pregnancy_anc",
        )
        missing = d.missing_fields()
        assert "beneficiary_id" not in missing
        assert "beneficiary_name" not in missing
        assert "visit_type" not in missing
        assert "weight_kg" in missing
        assert missing.index("weight_kg") < missing.index("next_visit_date")

    def test_is_complete_false_when_fields_missing(self):
        assert not VisitDraft().is_complete()

    def test_is_complete_true(self):
        d = VisitDraft(
            beneficiary_id="AB123456",
            beneficiary_name="Sunita Devi",
            visit_type="pregnancy_anc",
            weight_kg=58.0,
            vaccinations_due=["TT"],
            next_visit_date=_future_date(14),
        )
        assert d.is_complete()

    def test_vaccinations_due_empty_list_is_complete(self):
        """[] means 'none due', which satisfies the required field."""
        d = VisitDraft(
            beneficiary_id="AB123456",
            beneficiary_name="Sunita",
            visit_type="immunisation",
            weight_kg=10.0,
            vaccinations_due=[],          # explicitly answered as none
            next_visit_date=_future_date(7),
        )
        assert d.is_complete()

    def test_to_dict_has_all_public_keys(self):
        d = VisitDraft(beneficiary_id="AB123456")
        data = d.to_dict()
        assert "beneficiary_id" in data
        assert "danger_sign_flag" in data
        assert "danger_sign_codes" in data
        # Private turn text must NOT appear
        assert "_worker_turns" not in data

    def test_add_worker_turn_accumulates(self):
        d = VisitDraft()
        d.add_worker_turn("turn one")
        d.add_worker_turn("turn two")
        assert "turn one" in d.worker_context()
        assert "turn two" in d.worker_context()


# ===========================================================================
# apply_patch
# ===========================================================================


class TestApplyPatch:
    def _base_draft(self) -> VisitDraft:
        return VisitDraft(
            beneficiary_id="AB123456",
            beneficiary_name="Sunita Devi",
            visit_type="pregnancy_anc",
            weight_kg=58.0,
            vaccinations_due=["TT"],
            next_visit_date=_future_date(14),
        )

    def test_replace_single_field(self):
        d = self._base_draft()
        changed, rejected = apply_patch(d, {"weight_kg": 59.5})
        assert "weight_kg" in changed
        assert d.weight_kg == 59.5
        assert rejected == []

    def test_form_version_increments_on_change(self):
        d = self._base_draft()
        v0 = d.form_version
        apply_patch(d, {"weight_kg": 60.0})
        assert d.form_version == v0 + 1

    def test_form_version_unchanged_when_no_change(self):
        d = self._base_draft()
        v0 = d.form_version
        # Same value as already set → no change
        apply_patch(d, {"weight_kg": 58.0})
        assert d.form_version == v0

    def test_null_value_is_absent(self):
        d = VisitDraft()
        changed, rejected = apply_patch(d, {"beneficiary_id": None})
        assert changed == []
        assert rejected == []
        assert d.beneficiary_id is None

    def test_absent_key_leaves_field_unchanged(self):
        d = self._base_draft()
        apply_patch(d, {"notes": "new note"})
        assert d.notes == "new note"
        assert d.beneficiary_name == "Sunita Devi"  # untouched

    def test_partial_patch_does_not_reset_other_fields(self):
        d = self._base_draft()
        apply_patch(d, {"weight_kg": 57.0})
        assert d.beneficiary_name == "Sunita Devi"
        assert d.visit_type == "pregnancy_anc"

    def test_gram_conversion(self):
        """'2800 gram' represented as 2800 → converted to 2.8 kg."""
        d = VisitDraft()
        changed, rejected = apply_patch(d, {"weight_kg": 2800})
        assert "weight_kg" in changed
        assert d.weight_kg == pytest.approx(2.8)

    def test_invalid_weight_rejected(self):
        d = VisitDraft()
        changed, rejected = apply_patch(d, {"weight_kg": -5})
        assert "weight_kg" in rejected
        assert changed == []
        assert d.weight_kg is None

    def test_invalid_visit_type_rejected(self):
        d = VisitDraft()
        changed, rejected = apply_patch(d, {"visit_type": "checkup"})
        assert "visit_type" in rejected

    def test_beneficiary_id_normalized(self):
        d = VisitDraft()
        apply_patch(d, {"beneficiary_id": "ab 12-34 56"})
        assert d.beneficiary_id == "AB123456"

    def test_danger_field_in_patch_raises(self):
        d = VisitDraft()
        with pytest.raises(ValueError, match="danger"):
            apply_patch(d, {"danger_sign_flag": True})

    def test_danger_codes_in_patch_raises(self):
        d = VisitDraft()
        with pytest.raises(ValueError, match="danger"):
            apply_patch(d, {"danger_sign_codes": ["DS-01"]})

    def test_vaccinations_due_set_to_empty_list(self):
        """Empty list (worker said none) is a valid value."""
        d = VisitDraft()
        changed, rejected = apply_patch(d, {"vaccinations_due": []})
        assert "vaccinations_due" in changed
        assert d.vaccinations_due == []
        assert rejected == []

    def test_vaccinations_due_entries_trimmed(self):
        d = VisitDraft()
        apply_patch(d, {"vaccinations_due": ["  TT injection  "]})
        assert d.vaccinations_due == ["TT injection"]

    def test_next_visit_date_past_rejected(self):
        d = VisitDraft()
        past = (date.today() - timedelta(days=1)).isoformat()
        changed, rejected = apply_patch(d, {"next_visit_date": past})
        assert "next_visit_date" in rejected

    def test_next_visit_date_future_accepted(self):
        d = VisitDraft()
        future = _future_date(30)
        changed, rejected = apply_patch(d, {"next_visit_date": future})
        assert "next_visit_date" in changed
        assert rejected == []

    def test_multiple_fields_in_one_patch(self):
        d = VisitDraft()
        future = _future_date(10)
        patch = {
            "beneficiary_id": "ZZ999999",
            "beneficiary_name": "Test Worker",
            "visit_type": "immunisation",
            "weight_kg": 12.5,
            "vaccinations_due": ["BCG"],
            "next_visit_date": future,
        }
        changed, rejected = apply_patch(d, patch)
        assert len(changed) == 6
        assert rejected == []
        assert d.form_version == 1

    def test_notes_accepted(self):
        d = VisitDraft()
        apply_patch(d, {"notes": "IFA goli di hai"})
        assert d.notes == "IFA goli di hai"


# ===========================================================================
# Lexicon matcher
# ===========================================================================

from backend.app.danger.lexicon import match


class TestLexiconDS01:
    def test_english_bleeding_on_anc_visit(self):
        codes = match("she has bleeding", "pregnancy_anc", "")
        assert "DS-01" in codes

    def test_english_bleeding_with_pregnancy_term_in_turn(self):
        codes = match("pregnant and bleeding heavily", None, "")
        assert "DS-01" in codes

    def test_hindi_bleeding_on_anc_visit(self):
        codes = match("usse khoon aa raha hai", "pregnancy_anc", "")
        assert "DS-01" in codes

    def test_hindi_bleeding_with_pregnancy_in_prior(self):
        codes = match("bahut khoon beh raha", None, "garbhvati hai")
        assert "DS-01" in codes

    def test_kannada_bleeding(self):
        codes = match("raktasrava aagide", "pregnancy_anc", "")
        assert "DS-01" in codes

    def test_bleeding_without_pregnancy_context_no_fire(self):
        """Bleeding mentioned, but visit is newborn and no pregnancy in context."""
        codes = match("she has bleeding", "newborn_postnatal", "")
        assert "DS-01" not in codes

    def test_negated_bleeding_does_not_fire(self):
        codes = match("no bleeding today", "pregnancy_anc", "")
        assert "DS-01" not in codes

    def test_negated_hindi_bleeding_does_not_fire(self):
        codes = match("khoon nahi aa raha", "pregnancy_anc", "")
        assert "DS-01" not in codes

    def test_negated_occurrence_but_another_unnegated_fires(self):
        """One negated + one un-negated bleeding → still fires."""
        codes = match("no bleeding, but blood coming from wound", "pregnancy_anc", "")
        assert "DS-01" in codes

    def test_pregnancy_in_prior_turns_triggers_ds01(self):
        codes = match("she has bleeding", None, "patient is pregnant, ANC visit")
        assert "DS-01" in codes


class TestLexiconDS02:
    def test_english_high_fever_newborn(self):
        codes = match("the baby has very high fever", None, "")
        assert "DS-02" in codes

    def test_hindi_high_fever_newborn(self):
        codes = match("navjaat ko tez bukhar hai", None, "")
        assert "DS-02" in codes

    def test_kannada_high_fever_newborn(self):
        codes = match("magu ge heccu jwara ide", None, "")
        assert "DS-02" in codes

    def test_fever_without_intensity_no_fire(self):
        codes = match("baby has fever", None, "")
        assert "DS-02" not in codes

    def test_high_fever_without_newborn_no_fire(self):
        codes = match("patient has high fever", None, "")
        assert "DS-02" not in codes

    def test_newborn_in_prior_turn_triggers_ds02(self):
        codes = match("high fever present", None, "navjaat shishu ki visit")
        assert "DS-02" in codes

    def test_negated_fever_does_not_fire(self):
        codes = match("no high fever in baby", None, "")
        assert "DS-02" not in codes

    def test_intensity_beyond_window_no_fire(self):
        """'very' and 'fever' more than 3 tokens apart should not fire."""
        # "very long duration of moderate plain fever baby" – 'very' and 'fever'
        # are 5 tokens apart, well outside the 3-token window
        codes = match("very long duration of plain fever baby newborn", None, "")
        assert "DS-02" not in codes

    def test_intensity_within_window_fires(self):
        # "baby high fever" – 'high' (index 1) and 'fever' (index 2), distance 1
        codes = match("newborn high fever", None, "")
        assert "DS-02" in codes


class TestLexiconNoFalsePositives:
    def test_no_danger_on_routine_anc_note(self):
        codes = match("weight 58 kg, TT injection given, next visit in 2 weeks", "pregnancy_anc", "")
        assert codes == []

    def test_no_danger_on_immunisation_note(self):
        codes = match("BCG and OPV given today, baby is healthy", "immunisation", "")
        assert codes == []

    def test_empty_text_no_danger(self):
        codes = match("", None, "")
        assert codes == []


# ===========================================================================
# derive_follow_ups
# ===========================================================================


class TestDeriveFollowUps:
    _VID = "visit-uuid-001"
    _VDATE = "2026-11-21"

    def test_phc_referral_comes_first(self):
        items = derive_follow_ups(
            visit_id=self._VID,
            danger_sign_codes=["DS-01"],
            next_visit_date=_future_date(14),
            vaccinations_due=["TT"],
            visit_date=self._VDATE,
            language="en",
        )
        assert items[0].type == "phc_referral"

    def test_phc_referral_due_date_is_visit_date(self):
        items = derive_follow_ups(
            visit_id=self._VID,
            danger_sign_codes=["DS-01"],
            next_visit_date=None,
            vaccinations_due=None,
            visit_date=self._VDATE,
            language="en",
        )
        phc = next(i for i in items if i.type == "phc_referral")
        assert phc.due_date == self._VDATE

    def test_next_visit_comes_after_phc(self):
        future = _future_date(14)
        items = derive_follow_ups(
            visit_id=self._VID,
            danger_sign_codes=["DS-01"],
            next_visit_date=future,
            vaccinations_due=[],
            visit_date=self._VDATE,
            language="en",
        )
        types = [i.type for i in items]
        assert types.index("phc_referral") < types.index("next_visit")

    def test_next_visit_due_date_is_next_visit_date(self):
        future = _future_date(21)
        items = derive_follow_ups(
            visit_id=self._VID,
            danger_sign_codes=[],
            next_visit_date=future,
            vaccinations_due=None,
            visit_date=self._VDATE,
            language="en",
        )
        nv = next(i for i in items if i.type == "next_visit")
        assert nv.due_date == future

    def test_vaccination_due_has_no_due_date(self):
        items = derive_follow_ups(
            visit_id=self._VID,
            danger_sign_codes=[],
            next_visit_date=None,
            vaccinations_due=["BCG", "OPV"],
            visit_date=self._VDATE,
            language="en",
        )
        for item in items:
            if item.type == "vaccination_due":
                assert item.due_date is None

    def test_one_vaccination_item_per_entry(self):
        items = derive_follow_ups(
            visit_id=self._VID,
            danger_sign_codes=[],
            next_visit_date=None,
            vaccinations_due=["BCG", "OPV", "Hep-B"],
            visit_date=self._VDATE,
            language="en",
        )
        vacc_items = [i for i in items if i.type == "vaccination_due"]
        assert len(vacc_items) == 3

    def test_two_danger_codes_produce_two_phc_items(self):
        items = derive_follow_ups(
            visit_id=self._VID,
            danger_sign_codes=["DS-01", "DS-02"],
            next_visit_date=None,
            vaccinations_due=None,
            visit_date=self._VDATE,
            language="en",
        )
        phc_items = [i for i in items if i.type == "phc_referral"]
        assert len(phc_items) == 2

    def test_no_danger_no_vaccinations_just_next_visit(self):
        future = _future_date(10)
        items = derive_follow_ups(
            visit_id=self._VID,
            danger_sign_codes=[],
            next_visit_date=future,
            vaccinations_due=[],
            visit_date=self._VDATE,
            language="en",
        )
        assert len(items) == 1
        assert items[0].type == "next_visit"

    def test_all_follow_up_ids_are_unique(self):
        future = _future_date(7)
        items = derive_follow_ups(
            visit_id=self._VID,
            danger_sign_codes=["DS-01", "DS-02"],
            next_visit_date=future,
            vaccinations_due=["TT", "BCG"],
            visit_date=self._VDATE,
            language="en",
        )
        ids = [i.follow_up_id for i in items]
        assert len(ids) == len(set(ids))

    def test_visit_id_set_on_all_items(self):
        future = _future_date(7)
        items = derive_follow_ups(
            visit_id=self._VID,
            danger_sign_codes=["DS-01"],
            next_visit_date=future,
            vaccinations_due=["TT"],
            visit_date=self._VDATE,
            language="en",
        )
        for item in items:
            assert item.visit_id == self._VID
