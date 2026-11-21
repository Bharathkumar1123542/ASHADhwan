"""backend/app/log.py – Step 1

JSON-line logger.  Every log record is one JSON object on stdout with at
minimum the keys ``ts``, ``level``, ``session_id``, ``turn_id``, ``event``
(spec.md §10.1, NFR-OBS-01, NFR-PRIV-02).

Usage::

    from backend.app.log import get_logger

    log = get_logger()
    log.info("session.start", session_id=sid, worker_id=wid)

The logger is intentionally minimal: it writes directly to stdout so that
log aggregators and ``uvicorn`` pipe capture both streams without conflict.

Privacy rule (NFR-PRIV-02): logs MUST NOT contain transcript text, names,
field values or API keys.  Callers are responsible for not passing such
values.  The logger itself does not filter them (it cannot know which values
are sensitive at the call site), but the convention is enforced in code
review and by test S13.4.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any


class _JsonLogger:
    """Thin wrapper that writes one JSON object per line to stdout."""

    def _emit(
        self,
        level: str,
        event: str,
        session_id: str | None = None,
        turn_id: str | None = None,
        **extra: Any,
    ) -> None:
        record: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level": level,
            "session_id": session_id or "",
            "turn_id": turn_id or "",
            "event": event,
        }
        record.update(extra)
        print(json.dumps(record, ensure_ascii=False), file=sys.stdout, flush=True)

    def info(
        self,
        event: str,
        session_id: str | None = None,
        turn_id: str | None = None,
        **extra: Any,
    ) -> None:
        self._emit("info", event, session_id=session_id, turn_id=turn_id, **extra)

    def warning(
        self,
        event: str,
        session_id: str | None = None,
        turn_id: str | None = None,
        **extra: Any,
    ) -> None:
        self._emit("warning", event, session_id=session_id, turn_id=turn_id, **extra)

    def error(
        self,
        event: str,
        session_id: str | None = None,
        turn_id: str | None = None,
        **extra: Any,
    ) -> None:
        self._emit("error", event, session_id=session_id, turn_id=turn_id, **extra)


_logger = _JsonLogger()


def get_logger() -> _JsonLogger:
    """Return the module-level JSON logger singleton."""
    return _logger
