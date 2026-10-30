"""The site's own artist and report URLs: a readable slug followed by the id.

The id is the key; the slug is for people and search engines. `/a/UC…` and `/r/yt_UC…` without
a slug still resolve, and a stale slug resolves too (the page's canonical link corrects it).
Slugs are lowercase ASCII with no underscores, so the id can always be found at the end.
"""

from __future__ import annotations

import re
import unicodedata

SLUG_MAX = 60
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_ARTIST_PATH = re.compile(r"^(?:[a-z0-9-]*-)?(UC[0-9A-Za-z_-]{22})$")
_REPORT_PATH = re.compile(r"^(?:[a-z0-9-]*-)?([a-z]+_.+)$")


def slug(name: str | None) -> str:
    """'Bonde do Forró' -> 'bonde-do-forro'; names with no ASCII letters or digits give ''."""
    if not name:
        return ""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = _NON_ALNUM.sub("-", ascii_name.casefold()).strip("-")
    if len(s) > SLUG_MAX:
        s = s[:SLUG_MAX].rstrip("-")
    return s


def artist_url(artist_id: str, name: str | None) -> str:
    s = slug(name)
    return f"/a/{s}-{artist_id}" if s else f"/a/{artist_id}"


def report_url(report_id: str, title: str | None) -> str:
    s = slug(title)
    return f"/r/{s}-{report_id}" if s else f"/r/{report_id}"


def artist_id_from_path(segment: str) -> str | None:
    m = _ARTIST_PATH.match(segment)
    return m.group(1) if m else None


def report_id_from_path(segment: str) -> str | None:
    m = _REPORT_PATH.match(segment)
    return m.group(1) if m else None
