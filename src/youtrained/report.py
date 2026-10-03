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
    """Page title / H1 / share text. Leads with the subject's name so a search for the artist
    or channel matches, and so pages don't all share one title."""
    s = report["summary"]
    subject = report["subject"]
    who = subject.get("title") or subject.get("id") or "This channel"
    noun = "videos" if subject["platform"] == "yt" else "tracks"
    matched = s["matched_keys"]
    artist_songs = s.get("artist_songs", 0)
    if matched == 0 and artist_songs:
        return f"{who}: {artist_songs:,} songs appear in an AI training dataset"
    if not s.get("checked_known", True):
        # Pre-rendered from the channel mapping: we know the dataset videos, not the channel total.
        line = f"{who}: {matched:,} {noun} appear in AI training datasets"
    else:
        line = f"{who}: {matched:,} of {s['checked']:,} {noun} appear in AI training datasets"
    if artist_songs:
        line += f", plus {artist_songs:,} songs under the artist name"
    return line


def verdict(report: dict[str, Any]) -> str:
    """The plain-English answer under the headline. Says 'listed in datasets', never more."""
    s = report["summary"]
    videos = s["matched_keys"]
    songs = s.get("artist_songs", 0)
    if songs and videos:
        return (
            f"Yes. {songs:,} of your songs and {videos:,} of your videos are listed in datasets "
            "used to train and test AI music models."
        )
    if songs:
        return (
            f"Yes. {songs:,} of your songs are listed in a dataset used to train AI music models."
        )
    if videos:
        return (
            f"Yes. {videos:,} of your videos are listed in datasets used to train and test "
            "AI models."
        )
    return (
        "We found nothing listed under this name. That's good news, with the caveat that we "
        "only index three public datasets."
    )


def overview_cards(report: dict[str, Any]) -> list[dict[str, Any]]:
    """One plain card per dataset with hits: what it is, what we found, a few example titles."""
    cards = []
    for name, ds in report["datasets"].items():
        if name == "laion_disco_12m":
            songs = [s for a in report.get("artists", []) for s in a["songs"]]
            count = len({s["key"] for s in songs})
            titles = [s.get("title") for s in songs]
            noun = "song"
        else:
            rows = [h for h in report["hits"] if h["dataset"] == name]
            count = len({h["key"] for h in rows})
            titles = [h.get("your_title") for h in rows]
            noun = "video"
        if not count:
            continue
        examples = list(dict.fromkeys(t for t in titles if t))[:3]
        cards.append(
            {
                "dataset": name,
                "name": ds["name"],
                "plain": ds.get("plain") or ds["description"],
                "found": f"{count:,} of your {noun}{'' if count == 1 else 's'}",
                "examples": examples,
                "homepage": ds["homepage"],
            }
        )
    return cards


def prerendered_channel_report(conn: sqlite3.Connection, channel_id: str) -> dict[str, Any] | None:
    """A report built purely from the channel mapping, for channels nobody has submitted yet.

    No API call, nothing stored. Returns None when the mapping knows nothing about the channel.
    """
    from .artists import match_artists  # local import: artists imports db, report imports artists

    stat = db.channel_stat(conn, channel_id)
    if stat is None:
        return None
    _, title, _ = stat
    videos = db.videos_for_channel(conn, channel_id)
    ids = [v for v, _ in videos]
    result = CheckResult(
        spotify_ids=[], youtube_ids=ids, hits=db.find_hits(conn, KEY_YOUTUBE_VIDEO, ids)
    )
    matches = match_artists(conn, channel_id=channel_id, names=[title] if title else [])
    report = build_report(
        conn,
        result,
        platform="yt",
        subject_id=channel_id,
        subject_title=title,
        subject_url=f"https://www.youtube.com/channel/{channel_id}",
        titles={v: t or "" for v, t in videos},
        artist_matches=matches,
    )
    report["summary"]["checked_known"] = False
    report["prerendered"] = True
    return report
