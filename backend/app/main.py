"""backend/app/main.py – FastAPI entry point."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status
from fastapi.staticfiles import StaticFiles

from backend.app.api import ensure_schema, router as api_router, set_conn
from backend.app.config import cfg
from backend.app.db import (get_workers, init_db_async, save_visit, ConfirmationError, get_visits, get_follow_ups)
from backend.app.engine import VisitSession, FollowUp, say as engine_say, _utc_iso
from backend.app.log import get_logger
from backend.app.templates import check_locales
from backend.app.form import VisitDraft, apply_patch

_log = get_logger()

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_FRONTEND_DIR = _REPO_ROOT / "frontend"
_LOCALES_DIR = _REPO_ROOT / "locales"

_db_conn = None


@asynccontextmanager
async def _lifespan(application: FastAPI):  # type: ignore[type-arg]
    global _db_conn
    try:
        check_locales()
    except RuntimeError:
        _log.error("startup.locale_check_failed")
        raise
    try:
        _db_conn = await init_db_async()
        await asyncio.to_thread(ensure_schema, _db_conn)
        set_conn(_db_conn)
    except Exception:
        _log.error("startup.db_init_failed")
        raise
    _log.info("startup.ok")
    yield
    _log.info("shutdown.ok")


app = FastAPI(title="ASHA Voice Visit Recorder", lifespan=_lifespan)
app.include_router(api_router)


@app.get("/api/workers")
async def api_get_workers() -> list[dict[str, Any]]:
    return await asyncio.to_thread(get_workers, _db_conn)


async def api_save_visit(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist a complete, explicitly confirmed visit (compatibility/demo API).

    The live WebSocket engine normally performs this transaction itself. This
    endpoint is also useful for integrations and is guarded by form_version.
    """
    from fastapi import HTTPException
    import re
    from datetime import date

    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="JSON object required")
    required_top = ("session_id", "worker_id", "draft", "form_version_at_confirm")
    if any(k not in payload for k in required_top) or not isinstance(payload.get("draft"), dict):
        raise HTTPException(status_code=422, detail="Missing required visit fields")
    conn = _db_conn
    if conn is None:
        raise HTTPException(status_code=503, detail="Database is not ready")
    sid, wid = str(payload["session_id"]), str(payload["worker_id"])
    session = conn.execute("SELECT worker_id FROM sessions WHERE session_id=?", (sid,)).fetchone()
    worker = conn.execute("SELECT language FROM workers WHERE worker_id=?", (wid,)).fetchone()
    if not session or not worker or session["worker_id"] != wid:
        raise HTTPException(status_code=422, detail="Unknown session or worker")

    raw = payload["draft"]
    draft = VisitDraft()
    try:
        changed, rejected = apply_patch(draft, {k: v for k, v in raw.items() if k in {
            "beneficiary_id", "beneficiary_name", "visit_type", "weight_kg",
            "vaccinations_due", "next_visit_date", "notes"}})
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Match the submitted version exactly; apply_patch increments from zero.
    draft.form_version = int(raw.get("form_version", 0))
    codes = raw.get("danger_sign_codes", []) or []
    if not isinstance(codes, list) or any(c not in ("DS-01", "DS-02") for c in codes):
        raise HTTPException(status_code=422, detail="Invalid danger sign codes")
    draft.danger_sign_codes = list(dict.fromkeys(codes))
    draft.danger_sign_flag = bool(draft.danger_sign_codes)
    missing = draft.missing_fields()
    if missing:
        raise HTTPException(status_code=422, detail={"missing_fields": missing})
    version = int(payload["form_version_at_confirm"])
    if version != draft.form_version:
        raise HTTPException(status_code=409, detail="Confirmation is stale")

    lang = str(payload.get("language") or worker["language"])
    today = date.today().isoformat()
    fus: list[FollowUp] = []
    for code in draft.danger_sign_codes:
        fus.append(FollowUp("phc_referral", engine_say("fu_phc_referral", lang, sign_label=code), today))
    if draft.next_visit_date:
        fus.append(FollowUp("next_visit", engine_say("fu_next_visit", lang, date_spoken=draft.next_visit_date), draft.next_visit_date))
    for vaccine in (draft.vaccinations_due or []):
        fus.append(FollowUp("vaccination_due", engine_say("fu_vaccination_due", lang, name=vaccine), draft.next_visit_date))
    confirmed_at = payload.get("confirmed_at") or _utc_iso()
    try:
        visit_id = await asyncio.to_thread(save_visit, conn, session_id=sid, worker_id=wid,
            draft=draft, confirmed_at=confirmed_at, form_version_at_confirm=version, follow_ups=fus)
    except ConfirmationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        _log.exception("api.save_visit.failed")
        raise HTTPException(status_code=500, detail="Could not save visit") from exc
    return {"visit_id": visit_id, "follow_ups": [
        {"follow_up_id": f.follow_up_id, "type": f.type, "description": f.description, "due_date": f.due_date}
        for f in fus]}


async def api_list_visits(worker_id: str) -> list[dict[str, Any]]:
    """Return visit records and their follow-ups, newest first."""
    if _db_conn is None:
        return []
    visits = await asyncio.to_thread(get_visits, _db_conn, worker_id)
    for visit in visits:
        visit["follow_ups"] = await asyncio.to_thread(get_follow_ups, _db_conn, visit["visit_id"])
    return visits


@app.post("/api/visits", status_code=201)
async def http_save_visit(payload: dict[str, Any]) -> dict[str, Any]:
    return await api_save_visit(payload)


@app.get("/api/visits")
async def http_list_visits(worker_id: str) -> list[dict[str, Any]]:
    return await api_list_visits(worker_id)


_ALLOWED_ORIGINS: list[str] = list(cfg.get("server", {}).get(
    "allowed_origins", ["http://127.0.0.1:8000", "http://localhost:8000"]))
_ALLOWED_ORIGINS += [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]


def _origin_ok(origin: str, host: str) -> bool:
    """Allowed list, or same-origin (Origin host == Host header). Blocks cross-site (NFR-SEC-02)."""
    if origin in _ALLOWED_ORIGINS:
        return True
    return bool(origin) and bool(host) and urlparse(origin).netloc == host


_ERROR_TEXT = {"protocol_error": "Protocol error.", "worker_not_found": "Worker not found."}


async def _send_error_and_close(ws: WebSocket, code: str, close_code: int = 1008) -> None:
    try:
        await ws.send_json({"type": "error", "code": code, "text": _ERROR_TEXT.get(code, "Error.")})
        await ws.close(code=close_code)
    except Exception:
        pass


@app.websocket("/ws/session")
async def ws_session(websocket: WebSocket) -> None:
    origin = websocket.headers.get("origin", "")
    host = websocket.headers.get("host", "")
    if not _origin_ok(origin, host):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        _log.warning("ws.origin_rejected")
        return

    await websocket.accept()
    session: VisitSession | None = None

    async def send_json(payload: dict[str, Any]) -> None:
        try:
            await websocket.send_json(payload)
        except Exception:
            pass

    try:
        async for raw in websocket.iter_text():
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                await _send_error_and_close(websocket, "protocol_error")
                return
            ftype = frame.get("type") if isinstance(frame, dict) else None
            if ftype is None:
                await _send_error_and_close(websocket, "protocol_error")
                return

            if session is None:
                if ftype != "session.start":
                    await _send_error_and_close(websocket, "protocol_error")
                    return
                rows = await asyncio.to_thread(get_workers, _db_conn)
                worker = {r["worker_id"]: r for r in rows}.get(frame.get("worker_id", ""))
                if not worker:
                    await _send_error_and_close(websocket, "worker_not_found")
                    return
                session = VisitSession(str(uuid.uuid4()), worker["worker_id"], worker["language"],
                                       send_json, _db_conn)
                await session.start(frame.get("prefill"))
                _log.info("session.start", session_id=session.session_id)
                continue

            if ftype == "session.start":
                await send_json({"type": "error", "code": "protocol_error", "text": _ERROR_TEXT["protocol_error"]})
            elif ftype == "session.end":
                break
            elif ftype in ("worker.turn", "transcript.text"):
                await session.handle_text(str(frame.get("text", "")))
            elif ftype == "confirm":
                await session.handle_confirm(frame.get("form_version"))
            elif ftype == "discard":
                await session.handle_discard()
            elif ftype == "ping":
                await send_json({"type": "pong"})
            else:
                await _send_error_and_close(websocket, "protocol_error")
                return
    except WebSocketDisconnect:
        _log.info("session.disconnect")
    finally:
        if session:
            await session.end()


# Mount /locales BEFORE / so the more specific prefix wins.
if _LOCALES_DIR.exists():
    app.mount("/locales", StaticFiles(directory=str(_LOCALES_DIR)), name="locales")
if _FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")