"""24-hour per-artist/channel cache for platform lookups, stored in the YouTrained SQLite db."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from typing import Any

from .. import db
from ..config import PLATFORM_CACHE_TTL_S


class PlatformCache:
    def __init__(
        self,
        conn: sqlite3.Connection,
        ttl_s: float = PLATFORM_CACHE_TTL_S,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._conn = conn
        self._ttl = ttl_s
        self._clock = clock
        db.init_schema(conn)

    def get(self, key: str) -> Any | None:
        payload = db.cache_get(self._conn, key, self._ttl, self._clock())
        return json.loads(payload) if payload is not None else None

    def set(self, key: str, value: Any) -> None:
        db.cache_set(self._conn, key, json.dumps(value, ensure_ascii=False), self._clock())
