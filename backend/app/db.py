"""backend/app/db.py – Step 2 + save-flow extension

SQLite connection helper, schema migration, workers upsert, and
atomic visit + follow-up save.

Design:
- Every connection is opened with ``isolation_level=None`` (autocommit off at
  the Python level; explicit ``BEGIN IMMEDIATE``/``COMMIT`` are used for writes).
- ``PRAGMA foreign_keys = ON`` and ``PRAGMA journal_mode = WAL`` are executed
  on every new connection (spec.md Section 4, NFR-REL-03).
- Migration runs once at startup via ``init_db()``.  It checks
  ``PRAGMA user_version``; if it is 0 it executes ``migrations/0001_init.sql``
  and sets ``user_version`` to 1.  Subsequent calls are no-ops (S2.4).
- ``upsert_workers(conn, workers)`` inserts or replaces the seed profiles
  from ``config/workers.yaml`` (FR-WK-01, spec.md Section 4 "Persistence rules").
- ``save_visit`` performs the atomic visit + follow-up insert under
  ``BEGIN IMMEDIATE`` and raises ``ConfirmationError`` when the caller did not
  supply an explicit ``confirmed_at`` for the current ``form_version``
  (FR-RB-03, NFR-SAF-02, SD-04, SD-06).

All blocking I/O is wrapped in ``asyncio.to_thread`` by callers that live in
an async context.  ``get_connection`` and ``init_db`` are synchronous helpers;
``init_db_async`` is the async wrapper used by the lifespan.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from backend.app.config import cfg
from backend.app.log import get_logger

_log = get_logger()

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MIGRATION_SQL = Path(__file__).resolve().parent / "migrations" / "0001_init.sql"
_WORKERS_YAML = _REPO_ROOT / "config" / "workers.yaml"

# ---------------------------------------------------------------------------
# Connection factory
# ---------------------------------------------------------------------------


def get_connection() -> sqlite3.Connection:
    """Open and configure a SQLite connection.

    Returns a connection with:
    - ``isolation_level=None`` (Python-level autocommit; use explicit BEGIN).
    - ``PRAGMA foreign_keys = ON`` (spec.md Section 4, NFR-REL-03).
    - ``PRAGMA journal_mode = WAL`` (spec.md Section 4, NFR-REL-03).
    - ``row_factory = sqlite3.Row`` for dict-like access to rows.
    """
    db_path: str = cfg["db"]["path"]
    # Ensure parent directory exists (data/ is git-ignored but must be present)
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def _run_migration(conn: sqlite3.Connection) -> None:
    """Apply ``0001_init.sql`` if ``user_version`` is 0 (S2.4).

    Running twice is a no-op because the second call finds ``user_version = 1``
    and returns immediately.
    """
    version: int = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= 1:
        _log.info("db.migration.skip", version=version)
        return

    sql = _MIGRATION_SQL.read_text(encoding="utf-8")
    conn.executescript(sql)
    # executescript commits automatically; set user_version after the fact.
    # user_version is an integer pragma and cannot be set with a parameter, but
    # the value is an integer literal we control, not user input.
    conn.execute(f"PRAGMA user_version = 1")
    _log.info("db.migration.applied", version=1)


# ---------------------------------------------------------------------------
# Workers upsert
# ---------------------------------------------------------------------------


def _load_workers() -> list[dict[str, Any]]:
    """Load seed workers from ``config/workers.yaml``."""
    with _WORKERS_YAML.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data.get("workers", [])


def upsert_workers(conn: sqlite3.Connection) -> None:
    """Insert or replace seed worker profiles (FR-WK-01).

    Uses ``INSERT OR REPLACE`` so an existing row with the same ``worker_id``
    is updated.  This is safe because ``worker_id`` values are stable keys
    that never change once records exist (spec.md Section 8.4).
    """
    workers = _load_workers()
    conn.execute("BEGIN")
    try:
        for w in workers:
            conn.execute(
                "INSERT OR REPLACE INTO workers (worker_id, display_name, language) "
                "VALUES (?, ?, ?)",
                (w["worker_id"], w["display_name"], w["language"]),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    _log.info("db.workers.upserted", count=len(workers))


# ---------------------------------------------------------------------------
# Startup helpers
# ---------------------------------------------------------------------------


def init_db() -> sqlite3.Connection:
    """Open the DB, run migration, upsert workers.  Returns the open connection.

    Called synchronously from ``init_db_async`` via ``asyncio.to_thread``.
    """
    conn = get_connection()
    _run_migration(conn)
    upsert_workers(conn)
    return conn


async def init_db_async() -> sqlite3.Connection:
    """Async wrapper around ``init_db`` for use in the FastAPI lifespan."""
    return await asyncio.to_thread(init_db)


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def get_workers(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all worker rows as a list of dicts (for ``GET /api/workers``)."""
    rows = conn.execute(
        "SELECT worker_id, display_name, language FROM workers ORDER BY worker_id"
    ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Confirmation guard
# ---------------------------------------------------------------------------


class ConfirmationError(RuntimeError):
    """Raised when ``save_visit`` is called without valid confirmation.

    The server must NOT persist a visit record without an explicit confirmation
    for the current ``form_version`` (FR-RB-03, NFR-SAF-02, SD-04).
    """


# ---------------------------------------------------------------------------
# Atomic visit + follow-up save
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    """Return the current UTC time as ISO 8601 with milliseconds and Z suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
        f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z"


def save_visit(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    worker_id: str,
    draft: "Any",          # VisitDraft – typed as Any to avoid circular import
    confirmed_at: str,     # ISO-8601 UTC, supplied by caller after spoken confirm
    form_version_at_confirm: int,  # form_version the confirm was issued for (SD-04)
    follow_ups: list["Any"],       # list[FollowUp] from derive_follow_ups
) -> str:
    """Atomically insert a visit and its follow-ups.

    Safety contract (FR-RB-03, NFR-SAF-02, SD-04, SD-06):
    - ``form_version_at_confirm`` MUST equal ``draft.form_version`` at call time.
      If they differ, ``ConfirmationError`` is raised and nothing is written.
    - The entire insert is wrapped in ``BEGIN IMMEDIATE`` … ``COMMIT`` so that
      a partial write on exception leaves no row behind (SD-06).

    Returns the new ``visit_id`` (UUID v4 text).
    """
    if form_version_at_confirm != draft.form_version:
        _log.warning(
            "db.save_visit.stale_confirm",
            form_version_at_confirm=form_version_at_confirm,
            draft_form_version=draft.form_version,
        )
        raise ConfirmationError(
            f"Confirmation is stale: confirmed form_version={form_version_at_confirm} "
            f"but draft is now at form_version={draft.form_version} (SD-04)"
        )

    visit_id = str(uuid.uuid4())
    created_at = _utcnow()

    # vaccinations_due is JSON-serialised; None → '[]' by schema DEFAULT,
    # but we store explicitly to be clear.
    vacc_json = json.dumps(draft.vaccinations_due if draft.vaccinations_due is not None else [])
    danger_codes_json = json.dumps(draft.danger_sign_codes)

    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            INSERT INTO visits (
                visit_id, session_id, worker_id,
                beneficiary_id, beneficiary_name, visit_type,
                weight_kg, vaccinations_due, next_visit_date,
                notes, danger_sign_flag, danger_sign_codes,
                form_version, created_at, confirmed_at
            ) VALUES (
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?
            )
            """,
            (
                visit_id, session_id, worker_id,
                draft.beneficiary_id, draft.beneficiary_name, draft.visit_type,
                draft.weight_kg, vacc_json, draft.next_visit_date,
                draft.notes, 1 if draft.danger_sign_flag else 0, danger_codes_json,
                draft.form_version, created_at, confirmed_at,
            ),
        )

        for fu in follow_ups:
            conn.execute(
                """
                INSERT INTO follow_ups (follow_up_id, visit_id, type, description, due_date)
                VALUES (?, ?, ?, ?, ?)
                """,
                (fu.follow_up_id, visit_id, fu.type, fu.description, fu.due_date),
            )

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    _log.info(
        "db.save_visit.ok",
        visit_id=visit_id,
        session_id=session_id,
        follow_up_count=len(follow_ups),
    )
    return visit_id


async def save_visit_async(
    conn: sqlite3.Connection,
    **kwargs: Any,
) -> str:
    """Async wrapper around ``save_visit`` for use in the FastAPI handlers."""
    return await asyncio.to_thread(save_visit, conn, **kwargs)


# ---------------------------------------------------------------------------
# Visit / follow-up queries
# ---------------------------------------------------------------------------


def get_visits(conn: sqlite3.Connection, worker_id: str) -> list[dict[str, Any]]:
    """Return saved visits for *worker_id*, newest first."""
    rows = conn.execute(
        """
        SELECT visit_id, session_id, beneficiary_id, beneficiary_name,
               visit_type, weight_kg, vaccinations_due, next_visit_date,
               notes, danger_sign_flag, danger_sign_codes,
               form_version, created_at, confirmed_at
        FROM visits
        WHERE worker_id = ?
        ORDER BY confirmed_at DESC
        """,
        (worker_id,),
    ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["vaccinations_due"] = json.loads(d["vaccinations_due"])
        d["danger_sign_codes"] = json.loads(d["danger_sign_codes"])
        result.append(d)
    return result


def get_follow_ups(conn: sqlite3.Connection, visit_id: str) -> list[dict[str, Any]]:
    """Return follow-ups for *visit_id* in insertion order."""
    rows = conn.execute(
        """
        SELECT follow_up_id, visit_id, type, description, due_date, status
        FROM follow_ups
        WHERE visit_id = ?
        ORDER BY rowid
        """,
        (visit_id,),
    ).fetchall()
    return [dict(row) for row in rows]
