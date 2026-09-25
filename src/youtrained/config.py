"""Runtime configuration from environment variables (and an optional local .env file)."""

from __future__ import annotations

import os
from pathlib import Path

_DOTENV_LOADED = False


def load_dotenv(path: Path | None = None) -> None:
    """Load KEY=VALUE lines from `.env` into os.environ without overriding existing values."""
    global _DOTENV_LOADED
    if _DOTENV_LOADED and path is None:
        return
    _DOTENV_LOADED = True
    env_file = path or Path(os.environ.get("YOUTRAINED_ENV_FILE", ".env"))
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip("'\"")
        os.environ.setdefault(key, value)


def db_path() -> Path:
    return Path(os.environ.get("YOUTRAINED_DB", "data/youtrained.sqlite"))


def cache_dir() -> Path:
    return Path(os.environ.get("YOUTRAINED_CACHE_DIR", "data/cache"))


def youtube_api_key() -> str | None:
    load_dotenv()
    return os.environ.get("YOUTUBE_API_KEY") or None


def spotify_credentials() -> tuple[str, str] | None:
    load_dotenv()
    cid = os.environ.get("SPOTIFY_CLIENT_ID")
    secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    return (cid, secret) if cid and secret else None


def ga_measurement_id() -> str | None:
    """Google Analytics 4 measurement id (G-...). Unset locally and in tests: no tag rendered."""
    load_dotenv()
    return os.environ.get("GA_MEASUREMENT_ID") or None


PLATFORM_CACHE_TTL_S = 24 * 3600

NOT_LEGAL_ADVICE = (
    "This report is informational and is not legal advice. It shows that identifiers "
    "matching your work are present in the named public research datasets; it does not "
    "establish how any particular company used them."
)
