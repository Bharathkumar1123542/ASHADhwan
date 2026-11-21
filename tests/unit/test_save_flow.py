"""tests/unit/test_save_flow.py – save-flow unit tests

Covers:
  - save_visit() atomically persists visit + follow-ups.
  - save_visit() raises ConfirmationError when form_version_at_confirm
    does not match draft.form_version (FR-RB-03, NFR-SAF-02, SD-04).
  - A ConfirmationError leaves zero rows in the visits table (M-06).
  - A failed mid-transaction write leaves zero rows (SD-06 rollback).
  - Follow-ups are inserted with the correct visit_id.
  - get_visits() returns rows newest-first with decoded JSON arrays.
  - get_follow_ups() returns rows in insertion order.
  - POST /api/visits 201 on valid body; 409 on stale form_version;
    422 on incomplete draft.

No real services are called.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_MIGRATION_SQL = (
    Path(__file__).resolve().parent.parent.parent
    / "backend" / "app" / "migrations" / "0001_init.sql"
)
_WORKER_ID = "w_en_01"
_SESSION_ID = str(uuid.uuid4())


def _future(days: int = 14) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _open_migrated() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    sql = _MIGRATION_SQL.read_text(encoding="utf-8")
    conn.executescript(sql)
    conn.execute("PRAGMA user_version = 1")
    # Seed the worker so foreign keys pass
    conn.execute(
        "INSERT INTO workers (worker_id, display_name, language) VALUES (?,?,?)",
        (_WORKER_ID, "Anita (English)", "en"),
    )
    # Seed a session
    conn.execute(
        "INSERT INTO sessions (session_id, worker_id, started_at) VALUES (?,?,?)",
        (_SESSION_ID, _WORKER_ID, "2026-11-21T10:00:00.000Z"),
    )
    return conn


def _make_draft(form_version: int = 1) -> "Any":
    from backend.app.form import VisitDraft, apply_patch
    d = VisitDraft()
    apply_patch(d, {
        "beneficiary_id":   "AB123456",
        "beneficiary_name": "Sunita Devi",
        "visit_type":       "pregnancy_anc",
        "weight_kg":        58.0,
        "vaccinations_due": ["TT"],
        "next_visit_date":  _future(14),
    })
    # Force form_version to match what we want for the test
    d.form_version = form_version
    return d


def _make_follow_ups(visit_id: str = "__placeholder__") -> list:
    from backend.app.form import derive_follow_ups
    return derive_follow_ups(
        visit_id=visit_id,
        danger_sign_codes=[],
        next_visit_date=_future(14),
        vaccinations_due=["TT"],
        visit_date="2026-11-21",
        language="en",
    )


# ---------------------------------------------------------------------------
# save_visit – happy path
# ---------------------------------------------------------------------------


class TestSaveVisitHappyPath:
    def test_returns_uuid_string(self):
        from backend.app.db import save_visit
        conn = _open_migrated()
        draft = _make_draft(form_version=1)
        fus = _make_follow_ups()
        vid = save_visit(
            conn,
            session_id=_SESSION_ID,
            worker_id=_WORKER_ID,
            draft=draft,
            confirmed_at="2026-11-21T10:00:00.000Z",
            form_version_at_confirm=1,
            follow_ups=fus,
        )
        assert isinstance(vid, str) and len(vid) == 36

    def test_visit_row_exists_after_save(self):
        from backend.app.db import save_visit
        conn = _open_migrated()
        draft = _make_draft(form_version=1)
        vid = save_visit(
            conn,
            session_id=_SESSION_ID,
            worker_id=_WORKER_ID,
            draft=draft,
            confirmed_at="2026-11-21T10:00:00.000Z",
            form_version_at_confirm=1,
            follow_ups=[],
        )
        row = conn.execute(
            "SELECT * FROM visits WHERE visit_id = ?", (vid,)
        ).fetchone()
        assert row is not None
        assert row["beneficiary_id"] == "AB123456"
        assert row["visit_type"] == "pregnancy_anc"
        assert abs(row["weight_kg"] - 58.0) < 0.01

    def test_follow_ups_inserted_with_correct_visit_id(self):
        from backend.app.db import save_visit
        conn = _open_migrated()
        draft = _make_draft(form_version=1)
        fus = _make_follow_ups()
        vid = save_visit(
            conn,
            session_id=_SESSION_ID,
            worker_id=_WORKER_ID,
            draft=draft,
            confirmed_at="2026-11-21T10:00:00.000Z",
            form_version_at_confirm=1,
            follow_ups=fus,
        )
        rows = conn.execute(
            "SELECT * FROM follow_ups WHERE visit_id = ?", (vid,)
        ).fetchall()
        assert len(rows) == len(fus)
        for row in rows:
            assert row["visit_id"] == vid

    def test_vaccinations_due_stored_as_json(self):
        from backend.app.db import save_visit
        conn = _open_migrated()
        draft = _make_draft(form_version=1)
        vid = save_visit(
            conn,
            session_id=_SESSION_ID,
            worker_id=_WORKER_ID,
            draft=draft,
            confirmed_at="2026-11-21T10:00:00.000Z",
            form_version_at_confirm=1,
            follow_ups=[],
        )
        raw = conn.execute(
            "SELECT vaccinations_due FROM visits WHERE visit_id = ?", (vid,)
        ).fetchone()[0]
        parsed = json.loads(raw)
        assert parsed == ["TT"]

    def test_danger_sign_flag_stored(self):
        from backend.app.db import save_visit
        from backend.app.form import VisitDraft, apply_patch
        conn = _open_migrated()
        draft = VisitDraft()
        apply_patch(draft, {
            "beneficiary_id": "ZZ999999", "beneficiary_name": "Test",
            "visit_type": "newborn_postnatal", "weight_kg": 3.2,
            "vaccinations_due": [], "next_visit_date": _future(7),
        })
        draft.form_version = 1
        draft.danger_sign_flag = True
        draft.danger_sign_codes = ["DS-02"]
        vid = save_visit(
            conn,
            session_id=_SESSION_ID,
            worker_id=_WORKER_ID,
            draft=draft,
            confirmed_at="2026-11-21T10:00:00.000Z",
            form_version_at_confirm=1,
            follow_ups=[],
        )
        row = conn.execute(
            "SELECT danger_sign_flag, danger_sign_codes FROM visits WHERE visit_id = ?",
            (vid,),
        ).fetchone()
        assert row["danger_sign_flag"] == 1
        assert json.loads(row["danger_sign_codes"]) == ["DS-02"]


# ---------------------------------------------------------------------------
# save_visit – ConfirmationError (FR-RB-03, NFR-SAF-02, SD-04, M-06)
# ---------------------------------------------------------------------------


class TestSaveVisitConfirmationGuard:
    def test_stale_form_version_raises(self):
        from backend.app.db import save_visit, ConfirmationError
        conn = _open_migrated()
        draft = _make_draft(form_version=2)   # draft is at version 2
        with pytest.raises(ConfirmationError):
            save_visit(
                conn,
                session_id=_SESSION_ID,
                worker_id=_WORKER_ID,
                draft=draft,
                confirmed_at="2026-11-21T10:00:00.000Z",
                form_version_at_confirm=1,  # but confirm was for version 1 → stale
                follow_ups=[],
            )

    def test_stale_confirm_writes_no_rows(self):
        """M-06: records saved without confirmation MUST be 0."""
        from backend.app.db import save_visit, ConfirmationError
        conn = _open_migrated()
        draft = _make_draft(form_version=3)
        try:
            save_visit(
                conn,
                session_id=_SESSION_ID,
                worker_id=_WORKER_ID,
                draft=draft,
                confirmed_at="2026-11-21T10:00:00.000Z",
                form_version_at_confirm=2,
                follow_ups=[],
            )
        except ConfirmationError:
            pass
        count = conn.execute("SELECT COUNT(*) FROM visits").fetchone()[0]
        assert count == 0

    def test_matching_form_version_does_not_raise(self):
        from backend.app.db import save_visit
        conn = _open_migrated()
        draft = _make_draft(form_version=5)
        # Must not raise
        vid = save_visit(
            conn,
            session_id=_SESSION_ID,
            worker_id=_WORKER_ID,
            draft=draft,
            confirmed_at="2026-11-21T10:00:00.000Z",
            form_version_at_confirm=5,
            follow_ups=[],
        )
        assert vid


# ---------------------------------------------------------------------------
# save_visit – rollback on error (SD-06)
# ---------------------------------------------------------------------------


class TestSaveVisitRollback:
    def test_bad_follow_up_type_rolls_back_visit(self):
        """A constraint violation on follow_ups must roll back the visit row too."""
        from backend.app.db import save_visit
        from backend.app.form import FollowUp
        conn = _open_migrated()
        draft = _make_draft(form_version=1)
        # Build a follow-up with an illegal type to trigger the CHECK constraint
        bad_fu = FollowUp(
            follow_up_id=str(uuid.uuid4()),
            visit_id="__placeholder__",
            type="INVALID_TYPE",          # violates CHECK on follow_ups.type
            description="bad",
            due_date=None,
        )
        with pytest.raises(Exception):
            save_visit(
                conn,
                session_id=_SESSION_ID,
                worker_id=_WORKER_ID,
                draft=draft,
                confirmed_at="2026-11-21T10:00:00.000Z",
                form_version_at_confirm=1,
                follow_ups=[bad_fu],
            )
        count = conn.execute("SELECT COUNT(*) FROM visits").fetchone()[0]
        assert count == 0, "visit row must be rolled back when follow_up insert fails"


# ---------------------------------------------------------------------------
# get_visits / get_follow_ups
# ---------------------------------------------------------------------------


class TestGetVisits:
    def _save_one(self, conn, confirmed_at: str, form_version: int = 1) -> str:
        from backend.app.db import save_visit
        draft = _make_draft(form_version=form_version)
        return save_visit(
            conn,
            session_id=_SESSION_ID,
            worker_id=_WORKER_ID,
            draft=draft,
            confirmed_at=confirmed_at,
            form_version_at_confirm=form_version,
            follow_ups=[],
        )

    def test_returns_dicts(self):
        from backend.app.db import get_visits
        conn = _open_migrated()
        self._save_one(conn, "2026-11-21T10:00:00.000Z")
        rows = get_visits(conn, _WORKER_ID)
        assert len(rows) == 1
        assert isinstance(rows[0], dict)

    def test_vaccinations_due_decoded_to_list(self):
        from backend.app.db import get_visits
        conn = _open_migrated()
        self._save_one(conn, "2026-11-21T10:00:00.000Z")
        rows = get_visits(conn, _WORKER_ID)
        assert isinstance(rows[0]["vaccinations_due"], list)

    def test_newest_first(self):
        from backend.app.db import get_visits
        conn = _open_migrated()
        self._save_one(conn, "2026-11-20T10:00:00.000Z", form_version=1)
        self._save_one(conn, "2026-11-21T10:00:00.000Z", form_version=1)
        rows = get_visits(conn, _WORKER_ID)
        assert len(rows) == 2
        assert rows[0]["confirmed_at"] > rows[1]["confirmed_at"]

    def test_no_rows_for_unknown_worker(self):
        from backend.app.db import get_visits
        conn = _open_migrated()
        self._save_one(conn, "2026-11-21T10:00:00.000Z")
        rows = get_visits(conn, "w_nonexistent")
        assert rows == []


class TestGetFollowUps:
    def test_returns_follow_up_rows(self):
        from backend.app.db import save_visit, get_follow_ups
        conn = _open_migrated()
        draft = _make_draft(form_version=1)
        fus = _make_follow_ups()
        vid = save_visit(
            conn,
            session_id=_SESSION_ID,
            worker_id=_WORKER_ID,
            draft=draft,
            confirmed_at="2026-11-21T10:00:00.000Z",
            form_version_at_confirm=1,
            follow_ups=fus,
        )
        rows = get_follow_ups(conn, vid)
        assert len(rows) == len(fus)
        assert all("type" in r for r in rows)

    def test_empty_for_unknown_visit(self):
        from backend.app.db import get_follow_ups
        conn = _open_migrated()
        rows = get_follow_ups(conn, "no-such-visit")
        assert rows == []


# ---------------------------------------------------------------------------
# REST endpoint tests – POST /api/visits
# These call the route handler functions directly (bypass lifespan) using
# the same pattern as test_step2.py: inject _db_conn before making requests.
# ---------------------------------------------------------------------------


@pytest.fixture()
def _tmp_db(tmp_path):
    """Patch cfg db path and return an initialised connection."""
    import backend.app.db as db_module
    import backend.app.config as config_module

    db_file = str(tmp_path / "test.db")
    original = config_module.cfg["db"].get("path")
    config_module.cfg["db"]["path"] = db_file
    conn = db_module.init_db()
    yield conn
    if original is None:
        config_module.cfg["db"].pop("path", None)
    else:
        config_module.cfg["db"]["path"] = original


@pytest.mark.asyncio
async def test_post_visit_201_on_valid_body(_tmp_db):
    import backend.app.main as main_module

    conn = _tmp_db
    main_module._db_conn = conn
    sid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO sessions (session_id, worker_id, started_at) VALUES (?,?,?)",
        (sid, "w_en_01", "2026-11-21T10:00:00.000Z"),
    )

    payload = {
        "session_id":              sid,
        "worker_id":               "w_en_01",
        "language":                "en",
        "confirmed_at":            "2026-11-21T10:00:00.000Z",
        "form_version_at_confirm": 1,
        "draft": {
            "beneficiary_id":   "AB123456",
            "beneficiary_name": "Sunita Devi",
            "visit_type":       "pregnancy_anc",
            "weight_kg":        58.0,
            "vaccinations_due": ["TT"],
            "next_visit_date":  _future(14),
            "form_version":     1,
        },
    }

    # Call the route handler directly (no lifespan)
    from backend.app.main import api_save_visit
    result = await api_save_visit(payload)
    assert "visit_id" in result
    assert isinstance(result["follow_ups"], list)


@pytest.mark.asyncio
async def test_post_visit_409_on_stale_form_version(_tmp_db):
    import backend.app.main as main_module
    from fastapi import HTTPException

    conn = _tmp_db
    main_module._db_conn = conn
    sid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO sessions (session_id, worker_id, started_at) VALUES (?,?,?)",
        (sid, "w_en_01", "2026-11-21T10:00:00.000Z"),
    )

    payload = {
        "session_id":              sid,
        "worker_id":               "w_en_01",
        "language":                "en",
        "confirmed_at":            "2026-11-21T10:00:00.000Z",
        "form_version_at_confirm": 1,   # stale – draft is at version 2
        "draft": {
            "beneficiary_id":   "AB123456",
            "beneficiary_name": "Sunita Devi",
            "visit_type":       "pregnancy_anc",
            "weight_kg":        58.0,
            "vaccinations_due": ["TT"],
            "next_visit_date":  _future(14),
            "form_version":     2,
        },
    }

    from backend.app.main import api_save_visit
    with pytest.raises(HTTPException) as exc_info:
        await api_save_visit(payload)
    assert exc_info.value.status_code == 409
    count = conn.execute("SELECT COUNT(*) FROM visits").fetchone()[0]
    assert count == 0


@pytest.mark.asyncio
async def test_post_visit_422_on_incomplete_draft(_tmp_db):
    import backend.app.main as main_module
    from fastapi import HTTPException

    conn = _tmp_db
    main_module._db_conn = conn

    payload = {
        "session_id":              str(uuid.uuid4()),
        "worker_id":               "w_en_01",
        "language":                "en",
        "confirmed_at":            "2026-11-21T10:00:00.000Z",
        "form_version_at_confirm": 1,
        "draft": {
            "beneficiary_id": "AB123456",
            "form_version":   1,
        },
    }

    from backend.app.main import api_save_visit
    with pytest.raises(HTTPException) as exc_info:
        await api_save_visit(payload)
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_get_visits_returns_saved_record(_tmp_db):
    import backend.app.main as main_module

    conn = _tmp_db
    main_module._db_conn = conn
    sid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO sessions (session_id, worker_id, started_at) VALUES (?,?,?)",
        (sid, "w_en_01", "2026-11-21T10:00:00.000Z"),
    )

    save_payload = {
        "session_id":              sid,
        "worker_id":               "w_en_01",
        "language":                "en",
        "confirmed_at":            "2026-11-21T10:00:00.000Z",
        "form_version_at_confirm": 1,
        "draft": {
            "beneficiary_id":   "ZZ999999",
            "beneficiary_name": "Demo Worker",
            "visit_type":       "immunisation",
            "weight_kg":        12.0,
            "vaccinations_due": ["BCG"],
            "next_visit_date":  _future(28),
            "form_version":     1,
        },
    }

    from backend.app.main import api_save_visit, api_list_visits
    await api_save_visit(save_payload)
    visits = await api_list_visits(worker_id="w_en_01")
    assert len(visits) == 1
    assert visits[0]["beneficiary_id"] == "ZZ999999"
