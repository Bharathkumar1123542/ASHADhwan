"""backend/app/config.py – Step 1

Loads config/app.yaml and environment variables.  Provides typed access
through a module-level singleton (``cfg``) and ``require(key)`` for
configuration values that must not be empty.

Usage::

    from backend.app.config import cfg, require

    host = cfg["server"]["host"]
    api_key = require("GNANI_API_KEY")

``require(key)`` first checks the environment.  If the value is absent or
empty it raises ``RuntimeError`` with a clear message, so the server fails
at construction rather than at the first real API call.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_CONFIG_PATH = _REPO_ROOT / "config" / "app.yaml"


# ---------------------------------------------------------------------------
# Load YAML once at import time
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


cfg: dict[str, Any] = _load_yaml(_CONFIG_PATH)


# ---------------------------------------------------------------------------
# Environment overrides
# ---------------------------------------------------------------------------
# Overlay selected environment variables onto the config dict so callers
# can read everything through ``cfg`` without special-casing env vars.

def _apply_env_overrides() -> None:
    gemini_model = os.environ.get("GEMINI_MODEL", "").strip()
    if gemini_model:
        cfg.setdefault("llm", {})["model"] = gemini_model

    db_path = os.environ.get("DB_PATH", "").strip()
    if db_path:
        cfg.setdefault("db", {})["path"] = db_path
    else:
        cfg.setdefault("db", {}).setdefault("path", str(_REPO_ROOT / "data" / "app.db"))

    data_dir = os.environ.get("DATA_DIR", "").strip()
    if data_dir:
        cfg.setdefault("data", {})["dir"] = data_dir
    else:
        cfg.setdefault("data", {}).setdefault("dir", str(_REPO_ROOT / "data"))


_apply_env_overrides()


# ---------------------------------------------------------------------------
# require()
# ---------------------------------------------------------------------------

def require(key: str) -> str:
    """Return the value of environment variable *key*.

    Raises ``RuntimeError`` if the variable is absent or empty.  Call this
    inside adapter constructors so the server starts in Steps 1–4 without
    needing any API keys, but fails loudly as soon as a component that needs
    a key is constructed (spec.md §2.5).
    """
    value = os.environ.get(key, "").strip()
    if not value:
        raise RuntimeError(
            f"Required environment variable {key!r} is not set or empty.  "
            f"Add it to your .env file or export it before starting the server."
        )
    return value
