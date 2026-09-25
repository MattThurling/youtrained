"""Build the shareable report document from a CheckResult. Used by the CLI, web UI, JSON and PDF."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from . import __version__, db
from .artists import ArtistMatch
from .config import NOT_LEGAL_ADVICE
from .descriptors import load_descriptor
from .models import KEY_YOUTUBE_VIDEO, CheckResult, Hit


def clip_urls(hit: Hit) -> dict[str, str]:
    """Links to the exact material a dataset row points at."""
    if hit.key_type != KEY_YOUTUBE_VIDEO:
        return {}
    urls = {"watch": f"https://www.youtube.com/watch?v={hit.key}"}
    if hit.start_s is not None and hit.end_s is not None:
        s, e = int(hit.start_s), int(hit.end_s)
        urls["watch"] = f"https://www.youtube.com/watch?v={hit.key}&t={s}s"
        urls["clip"] = f"https://www.youtube.com/embed/{hit.key}?start={s}&end={e}"
    return urls


def dataset_versions(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for dataset, source, status, rows, finished in db.load_states(conn):
        sha = conn.execute(
            "SELECT sha256 FROM load_state WHERE dataset = ? AND source_file = ?",
            (dataset, source),
        ).fetchone()[0]
        out.setdefault(dataset, []).append(
            {"file": source, "status": status, "rows": rows, "sha256": sha, "loaded_at": finished}
        )
    return out


def report_id_for(platform: str, subject_id: str) -> str:
    return f"{platform}_{subject_id}"


def build_report(
    conn: sqlite3.Connection,
    result: CheckResult,
    *,
    platform: str,
    subject_id: str,
    subject_title: str | None,
    subject_url: str | None = None,
    titles: dict[str, str] | None = None,
    artist_matches: list[ArtistMatch] | None = None,
) -> dict[str, Any]:
    """Assemble everything a report page, JSON export or PDF needs, as plain JSON-able data."""
    titles = titles or {}
    hits = []
    for h in result.hits:
        hits.append(
            {
                **h.to_dict(),
                "your_title": titles.get(h.key),
                "urls": clip_urls(h),
                "basis": "video_id",
            }
        )
    # Artist-level matches: the LAION rows listed under the artist, not found via uploads.
    artists_out = []
    exact_keys = {h.key for h in result.hits}
    artist_song_total = 0
    for m in artist_matches or []:
        rows = db.find_hits(conn, KEY_YOUTUBE_VIDEO, m.song_ids)
        songs = []
        for h in rows:
            if h.dataset != "laion_disco_12m":
                continue
            songs.append(
                {**h.to_dict(), "your_title": h.title, "urls": clip_urls(h), "basis": m.basis}
            )
        artist_song_total += len({s["key"] for s in songs})
        artists_out.append(
            {**m.to_dict(), "songs": songs, "new_keys": len({s["key"] for s in songs} - exact_keys)}
        )
    dataset_names = list(result.datasets)
    if artists_out and "laion_disco_12m" not in dataset_names:
        dataset_names.append("laion_disco_12m")
    datasets = {}
    for name in dataset_names:
        d = load_descriptor(name)
        datasets[name] = json.loads(d.model_dump_json())
        datasets[name]["hit_count"] = sum(1 for h in result.hits if h.dataset == name)
        datasets[name]["matched_keys"] = len({h.key for h in result.hits if h.dataset == name})
    return {
        "report_id": report_id_for(platform, subject_id),
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "tool_version": __version__,
        "subject": {
            "platform": platform,
            "id": subject_id,
            "title": subject_title,
            "url": subject_url,
        },
        "summary": {
            "checked": result.checked,
            "hit_count": len(result.hits),
            "matched_keys": len(result.matched_keys),
            "datasets": dataset_names,
            "artist_matches": len(artists_out),
            "artist_songs": artist_song_total,
        },
        "hits": hits,
        "artists": artists_out,
        "datasets": datasets,
        "dataset_versions": dataset_versions(conn),
        "not_legal_advice": NOT_LEGAL_ADVICE,
    }


def report_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True)


def report_sha256(report: dict[str, Any]) -> str:
    return hashlib.sha256(report_json(report).encode("utf-8")).hexdigest()


def headline(report: dict[str, Any]) -> str:
    s = report["summary"]
    noun = "videos" if report["subject"]["platform"] == "yt" else "tracks"
    artist_songs = s.get("artist_songs", 0)
    if s["matched_keys"] == 0 and artist_songs:
        return f"{artist_songs} songs under your artist name appear in an AI training dataset"
    line = f"{s['matched_keys']} of your {s['checked']} {noun} appear in AI training datasets"
    if artist_songs:
        line += f", plus {artist_songs} songs under your artist name"
    return line
