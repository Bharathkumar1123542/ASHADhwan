"""backend/app/api.py – REST endpoints for the register UI (records, follow-ups)."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException

from backend.app.engine import DB_LOCK

router = APIRouter(prefix="/api")
_conn: Any = None


def set_conn(conn: Any) -> None:
    global _conn
    _conn = conn


def ensure_schema(conn: Any) -> None:
    """Side table so follow-ups can be completed without altering the 0001 migration."""
    with DB_LOCK:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS follow_up_done ("
            "follow_up_id TEXT PRIMARY KEY, done_at TEXT NOT NULL)"
        )


def _records(worker_id: str) -> list[dict[str, Any]]:
    with DB_LOCK:
        vrows = _conn.execute(
            """SELECT visit_id, beneficiary_id, beneficiary_name, visit_type, weight_kg,
                      vaccinations_due, next_visit_date, danger_sign_flag, danger_sign_codes,
                      confirmed_at
               FROM visits WHERE worker_id = ? ORDER BY confirmed_at DESC""",
            (worker_id,),
        ).fetchall()
        frows = _conn.execute(
            """SELECT f.follow_up_id, f.visit_id, f.type, f.description, f.due_date, d.done_at
               FROM follow_ups f
               JOIN visits v ON v.visit_id = f.visit_id
               LEFT JOIN follow_up_done d ON d.follow_up_id = f.follow_up_id
               WHERE v.worker_id = ? ORDER BY f.rowid""",
            (worker_id,),
        ).fetchall()
    by_visit: dict[str, list[dict[str, Any]]] = {}
    for r in frows:
        by_visit.setdefault(r["visit_id"], []).append({
            "follow_up_id": r["follow_up_id"],
            "type": r["type"],
            "description": r["description"],
            "due_date": r["due_date"],
            "done": r["done_at"] is not None,
        })
    out = []
    for r in vrows:
        d = dict(r)
        d["vaccinations_due"] = json.loads(d["vaccinations_due"] or "[]")
        d["danger_sign_codes"] = json.loads(d["danger_sign_codes"] or "[]")
        d["danger_sign_flag"] = bool(d["danger_sign_flag"])
        d["follow_ups"] = by_visit.get(d["visit_id"], [])
        out.append(d)
    return out


def _owned_followup(fid: str, worker_id: str) -> Any:
    return _conn.execute(
        """SELECT f.due_date FROM follow_ups f
           JOIN visits v ON v.visit_id = f.visit_id
           WHERE f.follow_up_id = ? AND v.worker_id = ?""",
        (fid, worker_id),
    ).fetchone()


def _set_done(fid: str, worker_id: str, undo: bool) -> bool:
    with DB_LOCK:
        if not _owned_followup(fid, worker_id):
            return False
        if undo:
            _conn.execute("DELETE FROM follow_up_done WHERE follow_up_id = ?", (fid,))
        else:
            _conn.execute(
                "INSERT OR REPLACE INTO follow_up_done(follow_up_id, done_at) VALUES (?, ?)",
                (fid, datetime.now(timezone.utc).isoformat()),
            )
    return True


def _reschedule(fid: str, worker_id: str, days: int) -> str | None:
    with DB_LOCK:
        row = _owned_followup(fid, worker_id)
        if not row:
            return None
        base = date.today()
        if row["due_date"]:
            try:
                base = max(base, date.fromisoformat(row["due_date"]))
            except ValueError:
                pass
        new_due = (base + timedelta(days=days)).isoformat()
        _conn.execute("UPDATE follow_ups SET due_date = ? WHERE follow_up_id = ?", (new_due, fid))
    return new_due


@router.get("/records")
async def api_records(worker_id: str) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_records, worker_id)


@router.post("/followups/{follow_up_id}/done")
async def api_followup_done(follow_up_id: str, worker_id: str, undo: bool = False) -> dict[str, Any]:
    if not await asyncio.to_thread(_set_done, follow_up_id, worker_id, undo):
        raise HTTPException(status_code=404, detail="follow-up not found")
    return {"ok": True}


@router.post("/followups/{follow_up_id}/reschedule")
async def api_followup_reschedule(follow_up_id: str, worker_id: str, days: int = 2) -> dict[str, Any]:
    days = max(1, min(days, 30))
    new_due = await asyncio.to_thread(_reschedule, follow_up_id, worker_id, days)
    if new_due is None:
        raise HTTPException(status_code=404, detail="follow-up not found")
    return {"ok": True, "due_date": new_due}