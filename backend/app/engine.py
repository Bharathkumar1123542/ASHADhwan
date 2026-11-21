"""backend/app/engine.py – conversation engine (Steps 6, 9, 10, 11).

Rules-first extraction + danger detection, templated speech only (locales/),
confirmation-gated save for the current form_version, follow-up derivation.
No transcript text is ever logged.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import yaml

from backend.app.db import ConfirmationError, get_follow_ups, get_visits, save_visit
from backend.app.log import get_logger

_log = get_logger()
_ROOT = Path(__file__).resolve().parent.parent.parent

DB_LOCK = threading.Lock()

COLLECTING, CONFIRMING, SAVED = "collecting", "confirming", "saved"
ORDER = ["beneficiary_id", "beneficiary_name", "visit_type", "weight_kg",
         "vaccinations_due", "next_visit_date"]

# ---------------------------------------------------------------- locales
_EN_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
              "August", "September", "October", "November", "December"]
_LOC: dict[str, dict] = {}


def _locale(lang: str) -> dict:
    if lang not in _LOC:
        try:
            _LOC[lang] = json.loads((_ROOT / "locales" / f"{lang}.json").read_text("utf-8"))
        except Exception:
            _LOC[lang] = {}
    return _LOC[lang]


def say(key: str, lang: str, **kw: Any) -> str:
    s = _locale(lang).get(key) or _locale("en").get(key) or ""
    if not isinstance(s, str):
        return ""
    try:
        return s.format(**kw)
    except (KeyError, IndexError):
        return s


def _months(lang: str) -> list[str]:
    m = _locale(lang).get("months")
    return m if isinstance(m, list) and len(m) == 12 else _EN_MONTHS


# ---------------------------------------------------------- danger lexicon
_EXTRA = {
    "bleeding_terms": ["bleed", "khoon", "rakta"],
    "pregnancy_terms": ["pregnan", "antenatal", "garbh", "गर्भ", "ಗರ್ಭ"],
    "fever_terms": ["fever", "bukhar", "bukhaar", "jwara", "बुखार", "ಜ್ವರ"],
    "newborn_terms": ["newborn", "new born", "baby", "babies", "infant", "neonate",
                      "navjaat", "नवजात", "शिशु", "ನವಜಾತ", "ಮಗು"],
}
_NEG_BEFORE = {"no", "not", "without", "never", "nahi", "nahin", "नहीं", "नही", "ಇಲ್ಲ", "illa"}
_NEG_AFTER = {"nahi", "nahin", "नहीं", "नही", "ಇಲ್ಲ", "illa"}


def _rx(term: str) -> re.Pattern:
    t = re.escape(term)
    if term.isascii():
        return re.compile(r"(?<![a-z0-9])" + t + (r"(?![a-z0-9])" if len(term) <= 4 else ""))
    return re.compile(t)


def _load_lex() -> dict[str, list[re.Pattern]]:
    data: dict = {}
    try:
        data = yaml.safe_load((_ROOT / "config" / "danger_lexicon.yaml").read_text("utf-8")) or {}
    except Exception:
        _log.warning("danger.lexicon_unreadable")
    out: dict[str, list[re.Pattern]] = {}
    for key in ("bleeding_terms", "pregnancy_terms", "fever_terms", "newborn_terms"):
        terms = set(_EXTRA[key])
        for lst in (data.get(key) or {}).values():
            terms.update(str(t).lower() for t in lst)
        out[key] = [_rx(t) for t in terms if t.strip()]
    for lst in (data.get("negation_terms") or {}).values():
        _NEG_BEFORE.update(str(t).lower() for t in lst)
    return out


_LEX = _load_lex()


def _tok(s: str) -> list[str]:
    return [w for w in re.split(r"[\s,.;!?]+", s) if w]


def _negated(t: str, start: int, end: int) -> bool:
    before = t[:start]
    cut = max(before.rfind(c) for c in ",.;!?")
    words = _tok(before[cut + 1:])[-3:]
    if any(w.strip("'\"") in _NEG_BEFORE for w in words):
        return True
    after = re.split(r"[,.;!?]", t[end:])[0]
    return any(w in _NEG_AFTER for w in _tok(after)[:3])


def detect_danger(text: str, context: str, visit_type: str | None) -> list[str]:
    """Rules-first DS-01 / DS-02. Conservative: never misses a non-negated hit."""
    t = text.lower()
    ctx = f"{context} {t}"
    codes: list[str] = []

    def live_hits(regs: list[re.Pattern]) -> bool:
        return any(not _negated(t, m.start(), m.end()) for r in regs for m in r.finditer(t))

    if live_hits(_LEX["bleeding_terms"]):
        if visit_type == "pregnancy_anc" or any(r.search(ctx) for r in _LEX["pregnancy_terms"]):
            codes.append("DS-01")
    if live_hits(_LEX["fever_terms"]):
        if visit_type == "newborn_postnatal" or any(r.search(ctx) for r in _LEX["newborn_terms"]):
            codes.append("DS-02")
    return codes


# --------------------------------------------------------------- parsing
NUMWORDS = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
            "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}
_KG = {"kilo", "kilos", "kg", "kgs", "kilogram", "kilograms", "किलो", "ಕೆಜಿ", "ಕಿಲೋ"}
_STOP = set("""id ids beneficiary number num no not anc antenatal newborn postnatal immunisation
immunization visit checkup check weight wazan vajan next vaccine vaccines vaccination
vaccinations due tt ifa bcg opv penta kilo kilos kg kgs kilogram kilograms name naam patient
is are was the and for of in on to at with her his has have had date note notes fever bleeding
pregnancy pregnant baby infant refer phc hai home house today tomorrow yes okay ok please also
change update correct sorry from week month day days""".split())
_MONTHS = {"jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4,
           "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8,
           "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10,
           "nov": 11, "november": 11, "dec": 12, "december": 12}
_VACC = [
    ("TT", r"\btt\b|\bt\.t\b|tetanus|टीटी|ಟಿಟಿ"),
    ("IFA", r"\bifa\b|\biron\b|आईएफए"),
    ("BCG", r"\bbcg\b"),
    ("OPV", r"\bopv\b|\bpolio\b"),
    ("Penta", r"\bpenta\w*"),
    ("Measles", r"measles|\bmmr\b|\bmr\b"),
    ("Hepatitis B", r"hep(?:atitis)?\s?-?b\b"),
    ("Rotavirus", r"rota\s?virus"),
    ("Vitamin A", r"vitamin\s?a\b"),
]
_YES = {"yes", "yeah", "yep", "yup", "correct", "confirm", "confirmed", "save", "ok", "okay",
        "right", "sure", "haan", "han", "ha", "haa", "ji", "sahi", "theek", "thik", "हाँ",
        "हां", "हा", "जी", "सही", "ठीक", "होय", "ಹೌದು", "ಸರಿ", "ಆಯ್ತು"}
_FILL = {"is", "it", "that", "this", "the", "record", "please", "thats", "that's", "all",
         "looks", "good", "its", "it's", "hai"}
_NO = {"no", "nope", "nahi", "nahin", "नहीं", "नही", "ಇಲ್ಲ", "wrong", "incorrect"}
_OOS = re.compile(r"\b(medicine|medicines|medication|dose|dosage|treat|treatment|diagnos\w*|"
                  r"prescribe\w*|prescription|advice|advise|cure|remedy|what should|should i|"
                  r"can i give)\b", re.I)


def _clean(w: str) -> str:
    return w.strip(".,;:!?\"'()[]।").lower()


def _words(text: str) -> list[str]:
    return text.split()


def _valid_id(s: str | None) -> bool:
    return bool(s) and 6 <= len(s) <= 16 and any(c.isdigit() for c in s)


def _id_tokens(words: list[str]) -> tuple[str, int]:
    out: list[str] = []
    n = 0
    for i, w in enumerate(words[:14]):
        c = re.sub(r"[^A-Za-z0-9]", "", w)
        lc = c.lower()
        if not c:
            break
        if lc in NUMWORDS:
            c = NUMWORDS[lc]
        elif lc in _STOP:
            break
        elif c.isdigit():
            nxt = _clean(words[i + 1]) if i + 1 < len(words) else ""
            if out and nxt in _KG:
                break
        elif not (re.fullmatch(r"[A-Za-z]{1,2}", c) or re.search(r"\d", c)):
            break
        out.append(c.upper())
        n = i + 1
        if sum(len(x) for x in out) >= 16 or w[-1:] in ",.;":
            break
    return "".join(out), n


def extract_id(words: list[str]) -> tuple[str | None, int]:
    lw = [_clean(w) for w in words]
    for i, w in enumerate(lw):
        if w in ("id", "ids", "आईडी"):
            j = i + 1
            while j < len(lw) and lw[j] in ("is", "number", "no", "num", ":", "#", "hai", "-"):
                j += 1
            s, n = _id_tokens(words[j:])
            if _valid_id(s):
                return s, j + n
    m = re.search(r"\b([A-Za-z]{1,3})[\s-]?(\d{4,12})\b", " ".join(words))
    if m:
        s = (m.group(1) + m.group(2)).upper()
        if _valid_id(s):
            return s, -1
    return None, -1


def _clean_name(words: list[str]) -> str:
    out: list[str] = []
    for w in words:
        c = w.strip(".,;:!?\"'()")
        if not c or any(ch.isdigit() for ch in c) or c.lower() in _STOP:
            break
        out.append(c)
        if len(out) == 3 or w[-1:] in ",.;":
            break
    return " ".join(out)


def extract_name(words: list[str]) -> str:
    for i, w in enumerate(words):
        if _clean(w) in ("name", "naam"):
            rest = words[i + 1:]
            while rest and _clean(rest[0]) in ("is", "to", "hai", ":", "-", "="):
                rest = rest[1:]
            n = _clean_name(rest)
            if n:
                return n
    return ""


def bare_name(text: str) -> str:
    t = re.sub(r"^(?:(?:it'?s|its|this is|her name is|his name is|the name is|name is|mera naam|naam|name)\s+)+",
               "", text.strip(), flags=re.I)
    return _clean_name(t.split())


def _title(s: str) -> str:
    return " ".join(p[:1].upper() + p[1:].lower() if p.isascii() else p for p in s.split())


def _norm(text: str) -> str:
    return re.sub(r"(\d)\s*(?:point|dot)\s*(\d)", r"\1.\2", text.strip(), flags=re.I)


def parse_weight(text: str, bare: bool = False) -> float | None:
    t = _norm(text)
    m = re.search(r"(?:weight|wazan|vajan|वज़न|वजन|ತೂಕ|thooka|tooka)\s*(?:is|hai|=|:|to|of)?\s*(\d+(?:\.\d+)?)", t, re.I)
    if not m:
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:kilos?|kgs?|kilograms?|किलो|ಕೆಜಿ|ಕಿಲೋ)", t, re.I)
    if not m and bare:
        m = re.search(r"(\d+(?:\.\d+)?)", t)
    if m:
        try:
            v = float(m.group(1))
        except ValueError:
            return None
        if 0.5 <= v <= 150:
            return round(v, 2)
    return None


def parse_visit_type(text: str) -> str | None:
    l = text.lower()
    if re.search(r"immuni[sz]ation|vaccination visit|vaccine visit|टीकाकरण|ಲಸಿಕೆ", l):
        return "immunisation"
    if re.search(r"new\s?born|post\s?natal|\bpnc\b|नवजात|ನವಜಾತ", l):
        return "newborn_postnatal"
    if re.search(r"\banc\b|ante\s?natal|pregnan|गर्भ|ಗರ್ಭ", l):
        return "pregnancy_anc"
    return None


def parse_vaccines(text: str) -> list[str]:
    l = text.lower()
    return [name for name, rx in _VACC if re.search(rx, l)]


def parse_none(text: str, expecting: bool) -> bool:
    l = text.lower()
    if re.search(r"\bno\s+vaccin|\bnone\b|\bnothing\b", l):
        return True
    toks = [_clean(w) for w in text.split()]
    return expecting and 0 < len(toks) <= 3 and toks[0] in (_NO | {"कोई"})


def _mk_date(y: int, mo: int, d: int, today: date) -> str | None:
    try:
        dt = date(y, mo, d)
        if dt < today:
            dt = date(y + 1, mo, d)
        return dt.isoformat()
    except ValueError:
        return None


def parse_date(text: str, today: date, allow_relative: bool = False) -> str | None:
    t = _norm(text).lower()
    for m in re.finditer(r"\b(\d{1,2})(?:st|nd|rd|th)?\s*(?:of\s+)?([a-z]{3,9})\b", t):
        if m.group(2) in _MONTHS:
            r = _mk_date(today.year, _MONTHS[m.group(2)], int(m.group(1)), today)
            if r:
                return r
    for m in re.finditer(r"\b([a-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?\b", t):
        if m.group(1) in _MONTHS:
            r = _mk_date(today.year, _MONTHS[m.group(1)], int(m.group(2)), today)
            if r:
                return r
    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", t)
    if m and int(m.group(2)) <= 12:
        y = int(m.group(3)) if m.group(3) else today.year
        y = y + 2000 if y < 100 else y
        try:
            return date(y, int(m.group(2)), int(m.group(1))).isoformat()
        except ValueError:
            pass
    if allow_relative:
        if "tomorrow" in t:
            return (today + timedelta(days=1)).isoformat()
        if re.search(r"next\s+week", t):
            return (today + timedelta(days=7)).isoformat()
        m = re.search(r"\b(?:in|after)\s+(\d{1,2})\s*(day|days|week|weeks)\b", t)
        if m:
            n = int(m.group(1)) * (7 if m.group(2).startswith("week") else 1)
            return (today + timedelta(days=n)).isoformat()
    return None


def is_yes(text: str) -> bool:
    toks = [_clean(w) for w in text.split()]
    toks = [t for t in toks if t]
    if not toks or len(toks) > 5 or any(t in _NO for t in toks):
        return False
    return any(t in _YES for t in toks) and all(t in _YES or t in _FILL for t in toks)


def is_no(text: str) -> bool:
    toks = [_clean(w) for w in text.split()]
    return 0 < len(toks) <= 2 and toks[0] in _NO


def is_discard(text: str) -> bool:
    l = text.lower()
    return bool(re.search(r"\b(discard|cancel|delete)\b|start over|रद्द", l))


# ----------------------------------------------------------------- model
@dataclass
class Draft:
    beneficiary_id: str | None = None
    beneficiary_name: str | None = None
    visit_type: str | None = None
    weight_kg: float | None = None
    vaccinations_due: list[str] | None = None
    next_visit_date: str | None = None
    notes: str | None = None
    danger_sign_codes: list[str] = field(default_factory=list)
    form_version: int = 0
    context: str = ""

    @property
    def danger_sign_flag(self) -> bool:
        return bool(self.danger_sign_codes)

    def missing_fields(self) -> list[str]:
        return [f for f in ORDER if getattr(self, f) in (None, "")]

    def complete(self) -> bool:
        return not self.missing_fields()

    def has_any(self) -> bool:
        return any(getattr(self, f) not in (None, "") for f in ORDER) or bool(self.danger_sign_codes)

    def to_dict(self) -> dict[str, Any]:
        return {f: getattr(self, f) for f in ORDER} | {"danger_sign_codes": list(self.danger_sign_codes)}


@dataclass
class FollowUp:
    type: str
    description: str
    due_date: str | None
    follow_up_id: str = field(default_factory=lambda: str(uuid.uuid4()))


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# --------------------------------------------------------------- session
class VisitSession:
    def __init__(self, session_id: str, worker_id: str, language: str,
                 send: Callable[[dict], Awaitable[None]], conn: Any) -> None:
        self.session_id, self.worker_id, self.lang = session_id, worker_id, language
        self.send, self.conn = send, conn
        self.draft = Draft()
        self.state = COLLECTING
        self._lock = asyncio.Lock()

    # ---- helpers
    async def _db(self, fn: Callable[[], Any]) -> Any:
        def run() -> Any:
            with DB_LOCK:
                return fn()
        return await asyncio.to_thread(run)

    def _say(self, key: str, **kw: Any) -> str:
        return say(key, self.lang, **kw)

    def _date_spoken(self, iso: str) -> str:
        y, m, d = (int(x) for x in iso.split("-"))
        s = f"{d} {_months(self.lang)[m - 1]}"
        return s if y == date.today().year else f"{s} {y}"

    def _vacc_text(self) -> str:
        v = self.draft.vaccinations_due
        if not v:
            return self._say("none")
        if len(v) == 1:
            return v[0]
        return ", ".join(v[:-1]) + f" {self._say('and_word')} {v[-1]}"

    def _sign_label(self, code: str) -> str:
        return self._say("sign_label_" + code.lower().replace("-", ""))

    def _display(self) -> dict[str, str]:
        d = self.draft
        return {
            "beneficiary_id": d.beneficiary_id or "",
            "beneficiary_name": d.beneficiary_name or "",
            "visit_type": self._say(f"visit_type_label_{d.visit_type}") if d.visit_type else "",
            "weight_kg": f"{d.weight_kg:g} kg" if d.weight_kg is not None else "",
            "vaccinations_due": self._vacc_text() if d.vaccinations_due is not None else "",
            "next_visit_date": self._date_spoken(d.next_visit_date) if d.next_visit_date else "",
        }

    def _rb_line(self, f: str) -> str:
        d = self.draft
        if f == "beneficiary_id":
            return self._say("rb_beneficiary_id", id_spelled=" ".join(d.beneficiary_id or ""))
        if f == "beneficiary_name":
            return self._say("rb_beneficiary_name", name=d.beneficiary_name)
        if f == "visit_type":
            return self._say("rb_visit_type", visit_type_label=self._say(f"visit_type_label_{d.visit_type}"))
        if f == "weight_kg":
            return self._say("rb_weight_kg", weight=f"{d.weight_kg:g}")
        if f == "vaccinations_due":
            return self._say("rb_vaccinations_due", vaccinations=self._vacc_text())
        if f == "next_visit_date":
            return self._say("rb_next_visit_date", date_spoken=self._date_spoken(d.next_visit_date or ""))
        return ""

    def _full_readback(self) -> str:
        parts = [self._say("read_back_intro")] + [self._rb_line(f) for f in ORDER]
        parts += [self._say("rb_danger_line", sign_label=self._sign_label(c)) for c in self.draft.danger_sign_codes]
        parts.append(self._say("confirm_question"))
        return " ".join(p for p in parts if p)

    async def _speak(self, kind: str, text: str, field_: str | None = None) -> None:
        if not text:
            return
        uid = str(uuid.uuid4())
        await self.send({"type": "agent.speak.start", "utterance_id": uid, "kind": kind,
                         "text": text, "field": field_, "sample_rate_hz": 16000, "interruptible": True})
        await self.send({"type": "agent.speak.end", "utterance_id": uid, "status": "completed"})

    async def _set_state(self, st: str) -> None:
        self.state = st
        await self.send({"type": "state.change", "state": st})

    async def _emit_form(self, changed: list[str]) -> None:
        await self.send({"type": "form.update", "form_version": self.draft.form_version,
                         "form": self.draft.to_dict(), "display": self._display(),
                         "missing_fields": self.draft.missing_fields(),
                         "changed_fields": changed, "danger_codes": list(self.draft.danger_sign_codes)})

    async def _ask_next(self) -> None:
        miss = self.draft.missing_fields()
        if not miss:
            return
        f = miss[0]
        text = self._say("ask_vaccinations") if f == "vaccinations_due" else \
            self._say("ask_missing", field_label=self._say(f"label_{f}"))
        await self._speak("prompt", text, f)

    def _new_visit(self) -> None:
        self.draft = Draft()

    # ---- lifecycle
    async def start(self, prefill: dict[str, Any] | None = None) -> None:
        """Create the session and optionally prefill a known beneficiary.

        Only beneficiary identity and visit type may be prefilled from the
        beneficiary list; clinical values still require a fresh worker input.
        """
        if isinstance(prefill, dict):
            for key in ("beneficiary_id", "beneficiary_name", "visit_type"):
                value = prefill.get(key)
                if isinstance(value, str) and value.strip():
                    setattr(self.draft, key, value.strip())
            if self.draft.has_any():
                self.draft.form_version = 1
        def ins() -> None:
            self.conn.execute("INSERT OR IGNORE INTO sessions(session_id, worker_id, started_at) VALUES (?,?,?)",
                              (self.session_id, self.worker_id, _utc_iso()))
        try:
            await self._db(ins)
        except Exception:
            _log.error("session.insert_failed")
        await self.send({"type": "session.ready", "session_id": self.session_id,
                         "worker_id": self.worker_id, "language": self.lang})
        await self._set_state(COLLECTING)
        await self._emit_form([])
        await self._speak("prompt", self._say("ready"), self.draft.missing_fields()[0])

    async def end(self) -> None:
        def upd() -> None:
            self.conn.execute("UPDATE sessions SET ended_at=? WHERE session_id=?", (_utc_iso(), self.session_id))
        try:
            await self._db(upd)
        except Exception:
            pass

    # ---- inputs
    async def handle_text(self, raw: str) -> None:
        text = (raw or "").strip()[:500]
        if not text:
            return
        async with self._lock:
            await self.send({"type": "transcript.final", "turn_id": str(uuid.uuid4()), "text": text})
            await self._process(text)

    async def handle_discard(self) -> None:
        async with self._lock:
            if self.state == SAVED or not self.draft.has_any():
                return
            await self._reset_visit()
            await self._speak("prompt", self._say("discarded"))

    async def handle_confirm(self, form_version: Any) -> None:
        async with self._lock:
            if self.state != CONFIRMING or not self.draft.complete():
                await self.send({"type": "error", "code": "not_ready", "text": "Record is not ready to confirm."})
                return
            if form_version != self.draft.form_version:
                await self.send({"type": "error", "code": "stale_confirm",
                                 "text": "The record changed. Please review and confirm again."})
                await self._speak("readback", self._full_readback())
                return
            await self._save(self.draft.form_version)

    async def _reset_visit(self) -> None:
        self._new_visit()
        await self.send({"type": "visit.reset"})
        await self._set_state(COLLECTING)
        await self._emit_form([])

    # ---- core turn
    async def _process(self, text: str) -> None:
        if self.state == SAVED:
            await self._reset_visit()
        if is_discard(text) and self.draft.has_any():
            await self._reset_visit()
            await self._speak("prompt", self._say("discarded"))
            return
        if self.state == CONFIRMING and is_yes(text):
            await self._save(self.draft.form_version)
            return

        miss = self.draft.missing_fields()
        expecting = miss[0] if (self.state == COLLECTING and miss) else None
        changed = self._extract(text, expecting)

        codes = [c for c in detect_danger(text, self.draft.context, self.draft.visit_type)
                 if c not in self.draft.danger_sign_codes]
        self.draft.context = (self.draft.context + " " + text.lower())[-1500:]
        for c in codes:
            self.draft.danger_sign_codes.append(c)
            await self._raise_danger(c)

        if changed or codes:
            self.draft.form_version += 1
            await self._emit_form(changed)

        if self.state == CONFIRMING:
            if changed or codes:
                lines = " ".join(self._rb_line(f) for f in changed if f in ORDER).strip()
                if lines:
                    await self._speak("readback", self._say("corrected_read_back", changed_fields_readback=lines)
                                      + " " + self._say("confirm_question"))
                else:
                    await self._speak("readback", self._full_readback())
            elif is_no(text):
                await self._speak("prompt", self._say("confirm_question"))
            elif _OOS.search(text):
                await self._speak("prompt", self._say("out_of_scope"))
            else:
                await self._speak("prompt", self._say("didnt_catch"))
            return

        if self.draft.complete():
            await self._set_state(CONFIRMING)
            await self._speak("readback", self._full_readback())
        elif changed or codes:
            await self._ask_next()
        else:
            await self._speak("prompt", self._say("out_of_scope") if _OOS.search(text) else self._say("didnt_catch"))
            await self._ask_next()

    def _extract(self, text: str, expecting: str | None) -> list[str]:
        d, changed = self.draft, []

        def setf(name: str, val: Any) -> None:
            if val in (None, ""):
                return
            if getattr(d, name) != val:
                setattr(d, name, val)
                changed.append(name)

        words = _words(text)
        bid, end = extract_id(words)
        if bid is None and expecting == "beneficiary_id":
            s, _ = _id_tokens(words)
            bid = s if _valid_id(s) else None
        setf("beneficiary_id", bid)

        name = extract_name(words)
        if not name and bid and end >= 0:
            name = _clean_name(words[end:])
        if not name and expecting == "beneficiary_name":
            name = bare_name(text)
        if name:
            setf("beneficiary_name", _title(name))

        setf("weight_kg", parse_weight(text, bare=expecting == "weight_kg"))
        setf("visit_type", parse_visit_type(text))

        vs = parse_vaccines(text)
        if vs:
            cur = list(d.vaccinations_due or [])
            setf("vaccinations_due", cur + [v for v in vs if v not in cur])
        elif parse_none(text, expecting == "vaccinations_due"):
            setf("vaccinations_due", [])

        rel = expecting == "next_visit_date" or "next visit" in text.lower()
        setf("next_visit_date", parse_date(text, date.today(), allow_relative=rel))
        return changed

    # ---- danger (rules-first, immediate, templated only)
    async def _raise_danger(self, code: str) -> None:
        spoken = self._say("danger_referral_" + code.lower().replace("-", ""))
        await self.send({"type": "danger.alert", "code": code, "sign_label": self._sign_label(code),
                         "text": spoken, "beneficiary_id": self.draft.beneficiary_id})
        await self._speak("danger", spoken)

        def ins() -> None:
            self.conn.execute(
                "INSERT INTO danger_events(event_id, session_id, visit_id, turn_id, code, detected_by, created_at) "
                "VALUES (?,?,NULL,NULL,?,'lexicon',?)",
                (str(uuid.uuid4()), self.session_id, code, _utc_iso()))
        try:
            await self._db(ins)
        except Exception:
            _log.error("danger.event_insert_failed")
        _log.info("danger.flagged", session_id=self.session_id, code=code)

    # ---- follow-ups + save
    def _derive_followups(self) -> list[FollowUp]:
        d, out = self.draft, []
        for c in d.danger_sign_codes:
            out.append(FollowUp("phc_referral", self._say("fu_phc_referral", sign_label=self._sign_label(c)),
                                date.today().isoformat()))
        if d.next_visit_date:
            out.append(FollowUp("next_visit",
                                self._say("fu_next_visit", date_spoken=self._date_spoken(d.next_visit_date)),
                                d.next_visit_date))
        for v in d.vaccinations_due or []:
            out.append(FollowUp("vaccination_due", self._say("fu_vaccination_due", name=v), d.next_visit_date))
        return out

    async def _save(self, version: int) -> None:
        if self.state != CONFIRMING or not self.draft.complete():
            return
        fus = self._derive_followups()
        confirmed_at = _utc_iso()
        try:
            visit_id = await self._db(lambda: save_visit(
                self.conn, session_id=self.session_id, worker_id=self.worker_id, draft=self.draft,
                confirmed_at=confirmed_at, form_version_at_confirm=version, follow_ups=fus))
        except ConfirmationError:
            await self.send({"type": "error", "code": "stale_confirm",
                             "text": "The record changed. Please review and confirm again."})
            return
        except Exception:
            _log.error("save.failed", session_id=self.session_id)
            await self.send({"type": "error", "code": "save_failed", "text": "Could not save. Please try again."})
            return
        await self.send({"type": "record.saved", "visit_id": visit_id, "form_version": version,
                         "danger_codes": list(self.draft.danger_sign_codes),
                         "follow_ups": [{"type": f.type, "description": f.description, "due_date": f.due_date}
                                        for f in fus]})
        await self._set_state(SAVED)
        await self._speak("saved", " ".join([self._say("saved"), self._say("fu_intro")]
                                            + [f.description for f in fus]).strip())


# --------------------------------------------------------------- queries
def list_records(conn: Any, worker_id: str) -> list[dict[str, Any]]:
    with DB_LOCK:
        visits = get_visits(conn, worker_id)
        for v in visits:
            v["follow_ups"] = get_follow_ups(conn, v["visit_id"])
    return visits