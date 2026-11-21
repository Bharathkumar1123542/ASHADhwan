"""tests/unit/test_step2.py – Step 2

Unit tests for the database layer (spec.md Step 2 checklist S2.4–S2.6).

S2.4  The migration runs twice with no error and leaves ``user_version`` at 1.
S2.5  A foreign-key violation is rejected.
S2.6  ``GET /api/workers`` returns the three seed workers.

All tests use a temporary in-memory or file-based SQLite database, never the
real ``data/app.db``.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport

# ---------------------------------------------------------------------------
# Helpers – thin wrappers that accept an explicit db_path, bypassing cfg
# ---------------------------------------------------------------------------

_MIGRATION_SQL = (
    Path(__file__).resolve().parent.parent.parent
    / "backend" / "app" / "migrations" / "0001_init.sql"
)


def _open_db(db_path: str = ":memory:") -> sqlite3.Connection:
    """Open a fresh SQLite connection with the two required pragmas."""
    conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply 0001_init.sql if user_version == 0, then set it to 1."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= 1:
        return
    sql = _MIGRATION_SQL.read_text(encoding="utf-8")
    conn.executescript(sql)
    conn.execute("PRAGMA user_version = 1")


# ---------------------------------------------------------------------------
# S2.4 – migration idempotency
# ---------------------------------------------------------------------------


class TestMigration:
    def test_migration_sets_user_version_to_1(self):
        conn = _open_db()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        _migrate(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1

    def test_migration_twice_no_error(self):
        conn = _open_db()
        _migrate(conn)
        _migrate(conn)  # second call must be a no-op, no exception
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1

    def test_migration_creates_all_tables(self):
        conn = _open_db()
        _migrate(conn)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"workers", "sessions", "transcript_turns", "visits",
                "follow_ups", "danger_events"} <= tables

    def test_migration_creates_indexes(self):
        conn = _open_db()
        _migrate(conn)
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert "idx_turns_created" in indexes
        assert "idx_visits_worker" in indexes


# ---------------------------------------------------------------------------
# S2.5 – foreign key enforcement
# ---------------------------------------------------------------------------


class TestForeignKeys:
    def _migrated_conn(self) -> sqlite3.Connection:
        conn = _open_db()
        _migrate(conn)
        return conn

    def test_fk_sessions_rejects_unknown_worker(self):
        conn = self._migrated_conn()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO sessions (session_id, worker_id, started_at) "
                "VALUES ('s1', 'nonexistent_worker', '2026-11-21T10:00:00.000Z')"
            )

    def test_fk_transcript_turns_rejects_unknown_session(self):
        conn = self._migrated_conn()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO transcript_turns "
                "(turn_id, session_id, speaker, text, created_at) "
                "VALUES ('t1', 'no_such_session', 'worker', 'hello', "
                "'2026-11-21T10:00:00.000Z')"
            )

    def test_fk_visits_rejects_unknown_worker(self):
        conn = self._migrated_conn()
        # First seed a worker and a session
        conn.execute(
            "INSERT INTO workers (worker_id, display_name, language) "
            "VALUES ('w1', 'Test', 'en')"
        )
        conn.execute(
            "INSERT INTO sessions (session_id, worker_id, started_at) "
            "VALUES ('s1', 'w1', '2026-11-21T10:00:00.000Z')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO visits "
                "(visit_id, session_id, worker_id, beneficiary_id, "
                " beneficiary_name, visit_type, weight_kg, next_visit_date, "
                " form_version, created_at, confirmed_at) "
                "VALUES ('v1', 's1', 'BAD_WORKER', 'AB1234', 'Raju', "
                "'pregnancy_anc', 55.0, '2027-01-01', 1, "
                "'2026-11-21T10:00:00.000Z', '2026-11-21T10:00:00.000Z')"
            )

    def test_fk_follow_ups_rejects_unknown_visit(self):
        conn = self._migrated_conn()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO follow_ups "
                "(follow_up_id, visit_id, type, description) "
                "VALUES ('fu1', 'NO_SUCH_VISIT', 'next_visit', 'Next visit')"
            )

    def test_fk_danger_events_rejects_unknown_session(self):
        conn = self._migrated_conn()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO danger_events "
                "(event_id, session_id, code, detected_by, created_at) "
                "VALUES ('e1', 'NO_SESSION', 'DS-01', 'lexicon', "
                "'2026-11-21T10:00:00.000Z')"
            )

    def test_danger_events_turn_id_set_null_on_delete(self):
        """SD-13: ON DELETE SET NULL on danger_events.turn_id."""
        conn = self._migrated_conn()
        # Seed worker + session
        conn.execute(
            "INSERT INTO workers (worker_id, display_name, language) "
            "VALUES ('w1', 'Test', 'en')"
        )
        conn.execute(
            "INSERT INTO sessions (session_id, worker_id, started_at) "
            "VALUES ('s1', 'w1', '2026-11-21T10:00:00.000Z')"
        )
        # Seed turn
        conn.execute(
            "INSERT INTO transcript_turns "
            "(turn_id, session_id, speaker, text, created_at) "
            "VALUES ('t1', 's1', 'worker', 'hello', '2026-11-21T10:00:00.000Z')"
        )
        # Seed danger_event referencing that turn
        conn.execute(
            "INSERT INTO danger_events "
            "(event_id, session_id, turn_id, code, detected_by, created_at) "
            "VALUES ('e1', 's1', 't1', 'DS-01', 'lexicon', '2026-11-21T10:00:00.000Z')"
        )
        # Delete the turn
        conn.execute("DELETE FROM transcript_turns WHERE turn_id = 't1'")
        # danger_events row must still exist with turn_id = NULL
        row = conn.execute(
            "SELECT turn_id FROM danger_events WHERE event_id = 'e1'"
        ).fetchone()
        assert row is not None
        assert row["turn_id"] is None


# ---------------------------------------------------------------------------
# S2.6 – GET /api/workers returns three seed workers
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_db_path(tmp_path):
    """Provide a temporary database path and patch cfg['db']['path']."""
    import backend.app.db as db_module
    import backend.app.config as config_module

    db_file = str(tmp_path / "test_app.db")
    # Temporarily override the db path in cfg
    original = config_module.cfg["db"].get("path")
    config_module.cfg["db"]["path"] = db_file
    yield db_file
    # Restore
    if original is None:
        config_module.cfg["db"].pop("path", None)
    else:
        config_module.cfg["db"]["path"] = original


@pytest.mark.asyncio
async def test_get_workers_returns_three_seeds(tmp_db_path):
    """GET /api/workers returns all three seed workers from config/workers.yaml."""
    from backend.app.main import app
    import backend.app.main as main_module
    import backend.app.db as db_module

    # Initialise the DB into the temp path
    conn = db_module.init_db()
    main_module._db_conn = conn

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/workers")

    assert response.status_code == 200
    workers = response.json()
    assert len(workers) == 3

    worker_ids = {w["worker_id"] for w in workers}
    assert worker_ids == {"w_en_01", "w_hi_01", "w_kn_01"}

    for w in workers:
        assert "worker_id" in w
        assert "display_name" in w
        assert "language" in w
        assert w["language"] in ("en", "hi", "kn")


@pytest.mark.asyncio
async def test_get_workers_response_shape(tmp_db_path):
    """Each worker object has exactly the three keys required by §5.2."""
    from backend.app.main import app
    import backend.app.main as main_module
    import backend.app.db as db_module

    conn = db_module.init_db()
    main_module._db_conn = conn

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/workers")

    assert response.status_code == 200
    for w in response.json():
        assert set(w.keys()) == {"worker_id", "display_name", "language"}
