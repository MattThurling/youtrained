"""Artist-level matching for LAION-DISCO-12M.

LAION lists songs under the artist's YouTube Music channel id (`artist_ids`), which is not
the channel the artist uploads to. So besides matching uploaded video ids we match the
artist: exactly by that id, or probably by name.
"""

from __future__ import annotations

import re
import sqlite3
import sys
import unicodedata
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from . import db

ID_LEN = 11
_STRIP_SUFFIXES = (" - topic", " topic", "vevo", " official", " music", " records")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_name(name: str) -> str:
    """Casefold, strip accents and channel-style suffixes, drop punctuation and spaces."""
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().casefold().strip()
    for suf in _STRIP_SUFFIXES:
        if n.endswith(suf) and len(n) > len(suf) + 1:
            n = n[: -len(suf)].strip()
    return _NON_ALNUM.sub("", n)


def build_artist_index(
    conn: sqlite3.Connection, *, log: Callable[[str], None] | None = None
) -> int:
    """Group every LAION-DISCO-12M row by artist id. One sequential scan; ~250k artists."""
    log = log or (lambda msg: print(msg, file=sys.stderr))
    db.init_schema(conn)
    songs: dict[str, bytearray] = defaultdict(bytearray)
    names: dict[str, str] = {}
    seen = 0
    cur = conn.execute(
        "SELECT key, artist, extra FROM hits NOT INDEXED WHERE dataset = 'laion_disco_12m'"
    )
    import json

    for key, artist, extra in cur:
        seen += 1
        if len(key) != ID_LEN:
            continue
        ids = json.loads(extra).get("artist_ids") or []
        artist_names = (artist or "").split(", ")
        for i, aid in enumerate(ids):
            songs[aid] += key.encode("ascii")
            if aid not in names and len(artist_names) == len(ids):
                names[aid] = artist_names[i]
        if seen % 1_000_000 == 0:
            log(f"scanned {seen:,} rows, {len(songs):,} artists so far")
    log(f"scanned {seen:,} rows; writing {len(songs):,} artists")
    conn.execute("DELETE FROM artists")
    conn.execute("DELETE FROM artist_names")
    batch: list[tuple] = []
    name_batch: list[tuple] = []
    for aid, blob in songs.items():
        name = names.get(aid)
        batch.append((aid, name, len(blob) // ID_LEN, bytes(blob)))
        if name:
            norm = normalize_name(name)
            if norm:
                name_batch.append((norm, aid))
        if len(batch) >= 20_000:
            conn.executemany("INSERT INTO artists VALUES (?,?,?,?)", batch)
            conn.executemany("INSERT OR IGNORE INTO artist_names VALUES (?,?)", name_batch)
            conn.commit()
            batch.clear()
            name_batch.clear()
    conn.executemany("INSERT INTO artists VALUES (?,?,?,?)", batch)
    conn.executemany("INSERT OR IGNORE INTO artist_names VALUES (?,?)", name_batch)
    conn.commit()
    return len(songs)


def artist_index_size(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM artists").fetchone()[0]


@dataclass(slots=True)
class ArtistMatch:
    artist_id: str
    name: str | None
    song_count: int
    basis: str  # 'artist_id' (exact) | 'artist_name' (probable)
    song_ids: list[str]

    def to_dict(self) -> dict:
        return {
            "artist_id": self.artist_id,
            "name": self.name,
            "song_count": self.song_count,
            "basis": self.basis,
            "url": f"https://music.youtube.com/channel/{self.artist_id}",
        }


def _split_ids(blob: bytes) -> list[str]:
    s = blob.decode("ascii")
    return [s[i : i + ID_LEN] for i in range(0, len(s), ID_LEN)]


def artist_by_id(conn: sqlite3.Connection, artist_id: str) -> ArtistMatch | None:
    row = conn.execute(
        "SELECT artist_id, name, song_count, song_ids FROM artists WHERE artist_id = ?",
        (artist_id,),
    ).fetchone()
    if not row:
        return None
    return ArtistMatch(row[0], row[1], row[2], "artist_id", _split_ids(row[3]))


def artists_by_name(conn: sqlite3.Connection, name: str) -> list[ArtistMatch]:
    norm = normalize_name(name)
    if not norm:
        return []
    rows = conn.execute(
        "SELECT a.artist_id, a.name, a.song_count, a.song_ids FROM artist_names n "
        "JOIN artists a ON a.artist_id = n.artist_id WHERE n.name_norm = ? "
        "ORDER BY a.song_count DESC",
        (norm,),
    ).fetchall()
    return [ArtistMatch(r[0], r[1], r[2], "artist_name", _split_ids(r[3])) for r in rows]


def match_artists(
    conn: sqlite3.Connection,
    *,
    channel_id: str | None = None,
    names: list[str] | None = None,
) -> list[ArtistMatch]:
    """Exact match on the channel id first, then probable matches on any of the names."""
    out: list[ArtistMatch] = []
    seen: set[str] = set()
    if channel_id and (m := artist_by_id(conn, channel_id)):
        out.append(m)
        seen.add(m.artist_id)
    for name in names or []:
        for m in artists_by_name(conn, name):
            if m.artist_id not in seen:
                out.append(m)
                seen.add(m.artist_id)
    return out
