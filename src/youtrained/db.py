"""SQLite storage for dataset hits and loader state."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from .models import Hit

SCHEMA = """
CREATE TABLE IF NOT EXISTS hits(
  dataset TEXT NOT NULL,
  dataset_row_id TEXT NOT NULL,
  key_type TEXT NOT NULL,
  key TEXT NOT NULL,
  artist TEXT,
  title TEXT,
  start_s REAL,
  end_s REAL,
  extra TEXT
);
CREATE INDEX IF NOT EXISTS ix_hits_key ON hits(key_type, key);
CREATE UNIQUE INDEX IF NOT EXISTS ux_hits_row ON hits(dataset, dataset_row_id, key_type);
CREATE TABLE IF NOT EXISTS load_state(
  dataset TEXT NOT NULL,
  source_file TEXT NOT NULL,
  status TEXT NOT NULL,
  rows INTEGER,
  sha256 TEXT,
  finished_at TEXT,
  PRIMARY KEY(dataset, source_file)
);
CREATE TABLE IF NOT EXISTS reports(
  report_id TEXT PRIMARY KEY,
  platform TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  subject_title TEXT,
  created_at TEXT NOT NULL,
  tool_version TEXT NOT NULL,
  checked INTEGER NOT NULL,
  hit_count INTEGER NOT NULL,
  payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS video_channels(
  video_id TEXT PRIMARY KEY,
  channel_id TEXT,
  channel_title TEXT,
  title TEXT,
  status TEXT NOT NULL,          -- 'ok' | 'missing' (deleted/private: API returned nothing)
  fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_video_channels_channel ON video_channels(channel_id);
CREATE TABLE IF NOT EXISTS map_queue(
  video_id TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS artists(
  artist_id TEXT PRIMARY KEY,    -- YouTube Music artist channel id (LAION artist_ids)
  name TEXT,
  song_count INTEGER NOT NULL,
  song_ids BLOB NOT NULL         -- concatenated 11-char YouTube ids
);
CREATE TABLE IF NOT EXISTS artist_names(
  name_norm TEXT NOT NULL,
  artist_id TEXT NOT NULL,
  PRIMARY KEY(name_norm, artist_id)
);
CREATE TABLE IF NOT EXISTS labels(
  id INTEGER PRIMARY KEY,
  mid TEXT UNIQUE NOT NULL,      -- AudioSet ontology id, e.g. /m/04rlf
  name TEXT NOT NULL,
  slug TEXT NOT NULL,
  parent_id INTEGER REFERENCES labels(id)   -- first parent; all parents in label_parents
);
CREATE INDEX IF NOT EXISTS ix_labels_slug ON labels(slug);
CREATE TABLE IF NOT EXISTS label_parents(
  label_id INTEGER NOT NULL,
  parent_id INTEGER NOT NULL,
  PRIMARY KEY(label_id, parent_id)
);
CREATE TABLE IF NOT EXISTS video_labels(
  video_id TEXT NOT NULL,
  label_id INTEGER NOT NULL,
  PRIMARY KEY(video_id, label_id)
);
CREATE INDEX IF NOT EXISTS ix_video_labels_label ON video_labels(label_id, video_id);
CREATE TABLE IF NOT EXISTS label_stats(
  label_id INTEGER PRIMARY KEY,
  direct_count INTEGER NOT NULL   -- videos carrying the label directly; refreshed by index-labels
);
CREATE TABLE IF NOT EXISTS tags(
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,     -- MusicCaps aspect, lowercased
  slug TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS video_tags(
  video_id TEXT NOT NULL,
  tag_id INTEGER NOT NULL,
  PRIMARY KEY(video_id, tag_id)
);
CREATE INDEX IF NOT EXISTS ix_video_tags_tag ON video_tags(tag_id, video_id);
CREATE TABLE IF NOT EXISTS platform_cache(
  cache_key TEXT PRIMARY KEY,
  fetched_at REAL NOT NULL,
  payload TEXT NOT NULL
);
"""

HIT_COLUMNS = "dataset, dataset_row_id, key_type, key, artist, title, start_s, end_s, extra"
SQLITE_MAX_VARS = 500


def connect(path: Path | str) -> sqlite3.Connection:
    path = Path(path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def insert_hits(conn: sqlite3.Connection, hits: Iterable[Hit]) -> int:
    """Insert hits, ignoring rows that already exist. Returns rows actually inserted."""
    before = conn.total_changes
    conn.executemany(
        f"INSERT OR IGNORE INTO hits({HIT_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?)",
        (h.as_row() for h in hits),
    )
    return conn.total_changes - before


def find_hits(conn: sqlite3.Connection, key_type: str, keys: Sequence[str]) -> list[Hit]:
    """Look up all hits for the given keys, chunked to stay under SQLite's variable limit."""
    out: list[Hit] = []
    keys = list(dict.fromkeys(keys))  # dedupe, keep order
    for i in range(0, len(keys), SQLITE_MAX_VARS):
        chunk = keys[i : i + SQLITE_MAX_VARS]
        placeholders = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT {HIT_COLUMNS} FROM hits WHERE key_type = ? AND key IN ({placeholders}) "
            "ORDER BY dataset, dataset_row_id",
            [key_type, *chunk],
        ).fetchall()
        out.extend(Hit.from_row(r) for r in rows)
    return out


def count_hits(conn: sqlite3.Connection, dataset: str | None = None) -> int:
    if dataset is None:
        return conn.execute("SELECT COUNT(*) FROM hits").fetchone()[0]
    return conn.execute("SELECT COUNT(*) FROM hits WHERE dataset = ?", (dataset,)).fetchone()[0]


# --- load_state -------------------------------------------------------------


def load_state_done(conn: sqlite3.Connection, dataset: str, source_file: str) -> bool:
    row = conn.execute(
        "SELECT status FROM load_state WHERE dataset = ? AND source_file = ?",
        (dataset, source_file),
    ).fetchone()
    return bool(row) and row[0] == "done"


def mark_load_state(
    conn: sqlite3.Connection,
    dataset: str,
    source_file: str,
    status: str,
    rows: int | None = None,
    sha256: str | None = None,
) -> None:
    finished_at = datetime.now(UTC).isoformat(timespec="seconds") if status == "done" else None
    conn.execute(
        "INSERT INTO load_state(dataset, source_file, status, rows, sha256, finished_at) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT(dataset, source_file) DO UPDATE SET "
        "status=excluded.status, rows=excluded.rows, sha256=excluded.sha256, "
        "finished_at=excluded.finished_at",
        (dataset, source_file, status, rows, sha256, finished_at),
    )
    conn.commit()


def load_states(conn: sqlite3.Connection) -> list[tuple]:
    return conn.execute(
        "SELECT dataset, source_file, status, rows, finished_at FROM load_state "
        "ORDER BY dataset, source_file"
    ).fetchall()


# --- platform_cache ---------------------------------------------------------


def cache_get(conn: sqlite3.Connection, key: str, max_age_s: float, now: float) -> str | None:
    row = conn.execute(
        "SELECT fetched_at, payload FROM platform_cache WHERE cache_key = ?", (key,)
    ).fetchone()
    if row is None or now - row[0] > max_age_s:
        return None
    return row[1]


def cache_set(conn: sqlite3.Connection, key: str, payload: str, now: float) -> None:
    conn.execute(
        "INSERT INTO platform_cache(cache_key, fetched_at, payload) VALUES (?,?,?) "
        "ON CONFLICT(cache_key) DO UPDATE SET fetched_at=excluded.fetched_at, "
        "payload=excluded.payload",
        (key, now, payload),
    )
    conn.commit()


# --- reports ----------------------------------------------------------------


def save_report(conn: sqlite3.Connection, report: dict) -> None:
    import json

    conn.execute(
        "INSERT INTO reports(report_id, platform, subject_id, subject_title, created_at, "
        "tool_version, checked, hit_count, payload) VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(report_id) DO UPDATE SET subject_title=excluded.subject_title, "
        "created_at=excluded.created_at, tool_version=excluded.tool_version, "
        "checked=excluded.checked, hit_count=excluded.hit_count, payload=excluded.payload",
        (
            report["report_id"],
            report["subject"]["platform"],
            report["subject"]["id"],
            report["subject"]["title"],
            report["created_at"],
            report["tool_version"],
            report["summary"]["checked"],
            report["summary"]["hit_count"],
            json.dumps(report, ensure_ascii=False, sort_keys=True),
        ),
    )
    conn.commit()


def get_report(conn: sqlite3.Connection, report_id: str) -> dict | None:
    import json

    row = conn.execute("SELECT payload FROM reports WHERE report_id = ?", (report_id,)).fetchone()
    return json.loads(row[0]) if row else None


# --- channel mapping --------------------------------------------------------

MAPPED_DATASETS = ("audioset", "musiccaps")


def seed_map_queue(conn: sqlite3.Connection, datasets: Sequence[str] = MAPPED_DATASETS) -> int:
    """Queue every YouTube id from the given datasets that has not been mapped yet.

    Uses a sequential scan on purpose: filtering on `dataset` alone would walk the unique
    index and do a random row lookup per row, which is far slower on 13M rows.
    """
    placeholders = ",".join("?" * len(datasets))
    before = conn.total_changes
    conn.execute(
        f"INSERT OR IGNORE INTO map_queue(video_id) "
        f"SELECT DISTINCT key FROM hits NOT INDEXED "
        f"WHERE key_type = 'youtube_video' AND dataset IN ({placeholders}) "
        f"AND key NOT IN (SELECT video_id FROM video_channels)",
        list(datasets),
    )
    conn.commit()
    return conn.total_changes - before


def map_queue_size(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM map_queue").fetchone()[0]


def next_unmapped(conn: sqlite3.Connection, limit: int) -> list[str]:
    return [
        r[0]
        for r in conn.execute("SELECT video_id FROM map_queue ORDER BY video_id LIMIT ?", (limit,))
    ]


def record_video_channels(
    conn: sqlite3.Connection, found: dict[str, tuple[str, str, str]], missing: Iterable[str]
) -> None:
    """Store API results for one batch and drop those ids from the queue."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    rows = [(vid, cid, ctitle, title, "ok", now) for vid, (cid, ctitle, title) in found.items()]
    rows += [(vid, None, None, None, "missing", now) for vid in missing]
    conn.executemany(
        "INSERT INTO video_channels(video_id, channel_id, channel_title, title, status, fetched_at) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT(video_id) DO UPDATE SET channel_id=excluded.channel_id, "
        "channel_title=excluded.channel_title, title=excluded.title, status=excluded.status, "
        "fetched_at=excluded.fetched_at",
        rows,
    )
    conn.executemany("DELETE FROM map_queue WHERE video_id = ?", [(r[0],) for r in rows])
    conn.commit()


def mapping_stats(conn: sqlite3.Connection) -> dict[str, int]:
    ok, missing = conn.execute(
        "SELECT SUM(status='ok'), SUM(status='missing') FROM video_channels"
    ).fetchone()
    return {"mapped": ok or 0, "missing": missing or 0, "queued": map_queue_size(conn)}


def mapping_complete(conn: sqlite3.Connection) -> bool:
    """True once the queue was seeded and fully drained."""
    stats = mapping_stats(conn)
    return stats["queued"] == 0 and (stats["mapped"] + stats["missing"]) > 0


def videos_for_channel(conn: sqlite3.Connection, channel_id: str) -> list[tuple[str, str]]:
    """(video_id, title) of every mapped dataset video owned by a channel."""
    return conn.execute(
        "SELECT video_id, title FROM video_channels WHERE channel_id = ? ORDER BY video_id",
        (channel_id,),
    ).fetchall()


def top_channels(conn: sqlite3.Connection, limit: int = 50) -> list[tuple]:
    """Channels ranked by how many of their videos appear in the mapped datasets."""
    return conn.execute(
        "SELECT channel_id, channel_title, COUNT(*) AS n FROM video_channels "
        "WHERE status = 'ok' GROUP BY channel_id ORDER BY n DESC, channel_title LIMIT ?",
        (limit,),
    ).fetchall()
