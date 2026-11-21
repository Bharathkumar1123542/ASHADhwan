"""backend/app/danger/lexicon.py – Step 2 (prototype)

Deterministic danger sign lexicon matcher (FR-DS-02, FR-DS-06, spec.md §6.2).

Loads term lists from ``config/danger_lexicon.yaml``.  Works without any LLM
call.

Detection rules (spec.md §6.2):
  DS-01: a ``bleeding_term`` appears in the turn AND
         (the draft visit_type is ``pregnancy_anc`` OR
          a ``pregnancy_term`` appears in the turn or in earlier worker turns).

  DS-02: a ``fever_term`` appears with an ``intensity_term`` within
         3 tokens of each other AND
         a ``newborn_term`` appears in the turn or in earlier worker turns.

Negation (SD-12): a negation word immediately preceding (within 1 token of)
a bleeding_term or fever_term suppresses that specific occurrence.  Another
un-negated occurrence in the same turn still fires.

Script awareness: terms are stored both in native script and in Latin
transliteration (see danger_lexicon.yaml); normalization lowercases and strips
punctuation so both scripts match.

Public API:
    match(turn_text, visit_type, prior_worker_text) -> list[str]
        Returns a list of danger codes ("DS-01", "DS-02") detected in the turn.
        Empty list = no danger.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Sequence

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_LEXICON_PATH = _REPO_ROOT / "config" / "danger_lexicon.yaml"

# ---------------------------------------------------------------------------
# Lexicon loading
# ---------------------------------------------------------------------------

_lexicon: dict[str, list[str]] = {}


def _load_lexicon(path: Path = _LEXICON_PATH) -> dict[str, list[str]]:
    """Load and normalize all term lists from the YAML file."""
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    result: dict[str, list[str]] = {}
    for list_name, lang_map in raw.items():
        merged: list[str] = []
        if isinstance(lang_map, dict):
            for _lang, terms in lang_map.items():
                if isinstance(terms, list):
                    merged.extend(terms)
        result[list_name] = [_normalize(t) for t in merged]
    return result


def _get_lexicon(path: Path = _LEXICON_PATH) -> dict[str, list[str]]:
    """Return the cached lexicon, loading it on first call."""
    global _lexicon
    if not _lexicon:
        _lexicon = _load_lexicon(path)
    return _lexicon


# ---------------------------------------------------------------------------
# Text normalization (spec.md §6.2)
# ---------------------------------------------------------------------------

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def _normalize(text: str) -> str:
    """Unicode NFC → lowercase → strip punctuation → collapse whitespace."""
    text = unicodedata.normalize("NFC", text)
    text = text.lower()
    text = _PUNCT_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> list[str]:
    """Split normalized text into whitespace-delimited tokens."""
    return text.split()


def _find_term_positions(tokens: list[str], terms: list[str]) -> list[int]:
    """Return token indices where any multi-word term starts.

    Multi-word terms are matched as a contiguous token sub-sequence.
    """
    positions: list[int] = []
    for term in terms:
        term_tokens = term.split()
        tlen = len(term_tokens)
        for i in range(len(tokens) - tlen + 1):
            if tokens[i : i + tlen] == term_tokens:
                positions.append(i)
    return positions


def _is_negated(pos: int, neg_positions: list[int], window: int = 2) -> bool:
    """True if any negation token falls within *window* tokens before *pos*.

    The default window is 2 so that "no high fever" (negation 2 tokens before
    'fever') and "no bleeding" (1 token before) are both caught (SD-12).
    """
    for neg_pos in neg_positions:
        if 0 < (pos - neg_pos) <= window:
            return True
    return False


# ---------------------------------------------------------------------------
# Main matcher
# ---------------------------------------------------------------------------


def match(
    turn_text: str,
    visit_type: str | None,
    prior_worker_text: str = "",
    *,
    _lexicon_path: Path = _LEXICON_PATH,
) -> list[str]:
    """Return danger codes detected in *turn_text*.

    Args:
        turn_text: the final transcript for this turn (unmodified).
        visit_type: draft ``visit_type`` at the moment of the turn, or None.
        prior_worker_text: earlier worker turn texts joined by newlines
            (used for context in DS-02 newborn check and DS-01 pregnancy check).
        _lexicon_path: override for tests (points to a test fixture YAML).

    Returns:
        A list of codes from {"DS-01", "DS-02"} that fired.  Order is
        DS-01 first, DS-02 second (when both fire).
    """
    lex = _get_lexicon(_lexicon_path)

    bleeding_terms = lex.get("bleeding_terms", [])
    pregnancy_terms = lex.get("pregnancy_terms", [])
    fever_terms = lex.get("fever_terms", [])
    intensity_terms = lex.get("intensity_terms", [])
    newborn_terms = lex.get("newborn_terms", [])
    negation_terms = lex.get("negation_terms", [])

    norm_turn = _normalize(turn_text)
    norm_prior = _normalize(prior_worker_text)

    turn_tokens = _tokenize(norm_turn)
    prior_tokens = _tokenize(norm_prior)

    # Combined context for checking pregnancy/newborn mentions in earlier turns
    all_tokens = prior_tokens + turn_tokens

    # --- Negation positions in the current turn ---
    neg_positions = _find_term_positions(turn_tokens, negation_terms)

    codes: list[str] = []

    # ------------------------------------------------------------------ DS-01
    # Fires when: bleeding_term in turn (not negated)
    #             AND (visit_type == pregnancy_anc
    #                  OR pregnancy_term in turn or prior)
    bleeding_positions = _find_term_positions(turn_tokens, bleeding_terms)
    has_unnegated_bleeding = any(
        not _is_negated(pos, neg_positions) for pos in bleeding_positions
    )

    if has_unnegated_bleeding:
        pregnancy_in_context = bool(
            _find_term_positions(all_tokens, pregnancy_terms)
        )
        if visit_type == "pregnancy_anc" or pregnancy_in_context:
            codes.append("DS-01")

    # ------------------------------------------------------------------ DS-02
    # Fires when: fever_term in turn (not negated) AND intensity_term within
    #             3 tokens of the fever_term AND newborn_term in turn or prior.
    fever_positions = _find_term_positions(turn_tokens, fever_terms)
    has_unnegated_fever = any(
        not _is_negated(pos, neg_positions) for pos in fever_positions
    )

    if has_unnegated_fever:
        intensity_positions = _find_term_positions(turn_tokens, intensity_terms)
        # Check if any intensity term is within 3 tokens of any fever term (SD-12)
        near_intensity = any(
            abs(f_pos - i_pos) <= 3
            for f_pos in fever_positions
            if not _is_negated(f_pos, neg_positions)
            for i_pos in intensity_positions
        )
        if near_intensity:
            newborn_in_context = bool(
                _find_term_positions(all_tokens, newborn_terms)
            )
            if newborn_in_context:
                codes.append("DS-02")

    return codes
