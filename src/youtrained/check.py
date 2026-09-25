"""Look up platform ids in the hits table and format the result. No CLI/web imports here."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from . import db
from .config import NOT_LEGAL_ADVICE
from .descriptors import Descriptor, load_descriptor
from .models import KEY_SPOTIFY_TRACK, KEY_YOUTUBE_VIDEO, CheckResult, Hit


def run_check(
    conn: sqlite3.Connection,
    spotify_ids: Sequence[str] = (),
    youtube_ids: Sequence[str] = (),
) -> CheckResult:
    spotify_ids = [s.strip() for s in spotify_ids if s.strip()]
    youtube_ids = [y.strip() for y in youtube_ids if y.strip()]
    hits: list[Hit] = []
    if spotify_ids:
        hits.extend(db.find_hits(conn, KEY_SPOTIFY_TRACK, spotify_ids))
    if youtube_ids:
        hits.extend(db.find_hits(conn, KEY_YOUTUBE_VIDEO, youtube_ids))
    return CheckResult(spotify_ids=spotify_ids, youtube_ids=youtube_ids, hits=hits)


def _clip(hit: Hit) -> str:
    if hit.start_s is None or hit.end_s is None:
        return ""
    return f" [{hit.start_s:g}s-{hit.end_s:g}s]"


def _detail(hit: Hit) -> str:
    extra = hit.extra
    if "caption" in extra:
        caption = extra["caption"].strip().replace("\n", " ")
        return caption[:117] + "..." if len(caption) > 120 else caption
    if "label_names" in extra:
        names = ", ".join(extra["label_names"])
        split = extra.get("split")
        return f"split={split}; labels: {names}" if split else f"labels: {names}"
    if hit.artist or hit.title:
        parts = [f"{hit.artist or '?'} - {hit.title or '?'}"]
        if extra.get("album_name"):
            parts.append(f"album: {extra['album_name']}")
        if extra.get("views"):
            parts.append(str(extra["views"]))
        return "; ".join(parts)
    return ""


def format_artists(matches) -> str:
    if not matches:
        return ""
    lines = []
    for m in matches:
        how = (
            "matched by YouTube Music artist id"
            if m.basis == "artist_id"
            else "matched by name (probable)"
        )
        lines.append(
            f"artist {m.name!r} ({m.artist_id}): {m.song_count} songs listed in LAION-DISCO-12M, {how}"
        )
        for key in m.song_ids[:5]:
            lines.append(f"  laion_disco_12m  row {key}  https://www.youtube.com/watch?v={key}")
        if m.song_count > 5:
            lines.append(f"  ... and {m.song_count - 5} more (see --json)")
    return "\n".join(lines)


def format_hits(result: CheckResult, descriptors: dict[str, Descriptor] | None = None) -> str:
    """Plain-text report for the CLI."""
    lines = [
        (
            f"checked {result.checked} id(s), {len(result.hits)} hit(s) across "
            f"{len(result.matched_keys)} id(s)"
        )
    ]
    for hit in result.hits:
        detail = _detail(hit)
        lines.append(
            f"  {hit.dataset}  row {hit.dataset_row_id}  key {hit.key_type}={hit.key}{_clip(hit)}"
            + (f"\n      {detail}" if detail else "")
        )
    if result.hits:
        lines.append("")
        for name in result.datasets:
            desc = (descriptors or {}).get(name) or load_descriptor(name)
            lines.append(f"About {desc.name} ({desc.publisher}, {desc.year}):")
            lines.append("  " + desc.description.strip().replace("\n", "\n  "))
            lines.append("")
        lines.append(NOT_LEGAL_ADVICE)
    return "\n".join(lines)
