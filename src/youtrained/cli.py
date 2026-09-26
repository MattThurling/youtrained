"""`youtrained` command line: init-db, load, check."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Annotated

import typer

from . import __version__, config, db
from .check import format_hits, run_check
from .loaders import LOADERS, run_loader
from .platforms import (
    PlatformCache,
    PlatformUnavailable,
    StaticSpotifyClient,
    StaticYouTubeClient,
    YouTubeApiClient,
    YouTubeClient,
)
from .urls import UnsupportedUrl, parse_spotify_artist_url, parse_youtube_channel_url

app = typer.Typer(help="Is my music in a known AI training dataset?", no_args_is_help=True)

# The Spotify client is still the static stub; the real one is the next milestone-3 piece.
SPOTIFY_CLIENT = StaticSpotifyClient()


def youtube_client(conn) -> YouTubeClient:
    """Real API client when YOUTUBE_API_KEY is set (with 24h cache), else the static stub."""
    key = config.youtube_api_key()
    if key:
        return YouTubeApiClient(key, cache=PlatformCache(conn))
    return StaticYouTubeClient()


def _err(msg: str) -> None:
    typer.echo(msg, err=True)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"youtrained {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show version.")
    ] = False,
) -> None:
    pass


@app.command("init-db")
def init_db(db_path: Annotated[Path | None, typer.Option("--db")] = None) -> None:
    """Create the SQLite database and schema."""
    path = db_path or config.db_path()
    conn = db.connect(path)
    db.init_schema(conn)
    typer.echo(f"initialised {path}")


@app.command()
def load(
    dataset: Annotated[str, typer.Argument(help="Dataset name, or 'all'.")],
    force: Annotated[bool, typer.Option(help="Reload files already marked done.")] = False,
    limit: Annotated[
        int | None, typer.Option(help="Stop after N rows per file (smoke test).")
    ] = None,
    db_path: Annotated[Path | None, typer.Option("--db")] = None,
    cache: Annotated[Path | None, typer.Option("--cache")] = None,
) -> None:
    """Download a dataset manifest and load its rows. Idempotent and resumable."""
    names = list(LOADERS) if dataset == "all" else [dataset]
    unknown = [n for n in names if n not in LOADERS]
    if unknown:
        _err(f"unknown dataset(s): {', '.join(unknown)}. Known: {', '.join(LOADERS)}")
        raise typer.Exit(2)
    conn = db.connect(db_path or config.db_path())
    cache_dir = cache or config.cache_dir()
    for name in names:
        results = run_loader(LOADERS[name], conn, cache_dir, force=force, limit=limit, log=_err)
        total = sum(results.values())
        typer.echo(f"{name}: {total:,} new rows; {db.count_hits(conn, name):,} rows in table")


@app.command()
def status(db_path: Annotated[Path | None, typer.Option("--db")] = None) -> None:
    """Show what has been loaded."""
    conn = db.connect(db_path or config.db_path())
    db.init_schema(conn)
    for dataset, source, state, rows, finished in db.load_states(conn):
        typer.echo(f"{dataset:12} {source:32} {state:8} {rows or 0:>10,} {finished or ''}")
    typer.echo(f"total hits rows: {db.count_hits(conn):,}")
    m = db.mapping_stats(conn)
    state = "complete" if db.mapping_complete(conn) else "incomplete"
    typer.echo(
        f"channel mapping: {m['mapped']:,} mapped, {m['missing']:,} missing, "
        f"{m['queued']:,} queued ({state})"
    )
    from .labels import label_counts

    c = label_counts(conn)
    typer.echo(
        f"labels: {c['labels']:,} labels, {c['video_labels']:,} video-label pairs; "
        f"tags: {c['tags']:,} tags, {c['video_tags']:,} video-tag pairs"
    )


@app.command("map-channels")
def map_channels(
    budget: Annotated[int, typer.Option(help="Max API calls this run (1 quota unit each).")] = 9000,
    db_path: Annotated[Path | None, typer.Option("--db")] = None,
) -> None:
    """Map dataset video ids to their YouTube channels, 50 per API call. Resumable."""
    from .mapping import run_mapping

    key = config.youtube_api_key()
    if not key:
        _err("YOUTUBE_API_KEY is not set (see .env.example)")
        raise typer.Exit(2)
    conn = db.connect(db_path or config.db_path())
    run = run_mapping(conn, YouTubeApiClient(key), budget=budget, log=_err)
    typer.echo(
        f"{run.calls} calls, {run.mapped:,} mapped, {run.missing:,} missing; {run.stopped_reason}"
    )


@app.command("index-artists")
def index_artists(db_path: Annotated[Path | None, typer.Option("--db")] = None) -> None:
    """Group LAION-DISCO-12M rows by artist id for artist-level matching (run once per load)."""
    from .artists import build_artist_index

    conn = db.connect(db_path or config.db_path())
    n = build_artist_index(conn, log=_err)
    typer.echo(f"indexed {n:,} artists")


@app.command("index-labels")
def index_labels(
    db_path: Annotated[Path | None, typer.Option("--db")] = None,
    cache: Annotated[Path | None, typer.Option("--cache")] = None,
) -> None:
    """Build the AudioSet label tables (ontology + video_labels) from loaded rows."""
    from .labels import build_label_tables, build_video_labels, label_counts

    conn = db.connect(db_path or config.db_path())
    build_label_tables(conn, cache or config.cache_dir(), log=_err)
    build_video_labels(conn, log=_err)
    c = label_counts(conn)
    typer.echo(f"{c['labels']:,} labels, {c['video_labels']:,} video-label pairs")


@app.command("index-tags")
def index_tags(db_path: Annotated[Path | None, typer.Option("--db")] = None) -> None:
    """Build MusicCaps aspect tags (tags + video_tags) from loaded rows."""
    from .labels import build_tag_tables, label_counts

    conn = db.connect(db_path or config.db_path())
    build_tag_tables(conn, log=_err)
    c = label_counts(conn)
    typer.echo(f"{c['tags']:,} tags, {c['video_tags']:,} video-tag pairs")


@app.command("index-channels")
def index_channels(db_path: Annotated[Path | None, typer.Option("--db")] = None) -> None:
    """Recount mapped videos per channel (run after map-channels completes)."""
    conn = db.connect(db_path or config.db_path())
    db.init_schema(conn)
    typer.echo(f"{db.refresh_channel_stats(conn):,} channels with dataset videos")


@app.command()
def remove(
    subject_id: Annotated[str, typer.Argument(help="yt_<channel id> or a_<artist id>")],
    reason: Annotated[str | None, typer.Option()] = None,
    undo: Annotated[bool, typer.Option(help="Restore a removed page.")] = False,
    db_path: Annotated[Path | None, typer.Option("--db")] = None,
) -> None:
    """Hide a channel or artist page (404, out of sitemaps) on request; --undo restores it."""
    conn = db.connect(db_path or config.db_path())
    db.init_schema(conn)
    if not subject_id.startswith(("yt_", "a_")):
        _err("subject id must start with yt_ (channel) or a_ (artist)")
        raise typer.Exit(2)
    if undo:
        typer.echo("restored" if db.drop_removal(conn, subject_id) else "was not removed")
    else:
        db.add_removal(conn, subject_id, reason)
        typer.echo(f"removed {subject_id}")


@app.command("top-channels")
def top_channels_cmd(
    limit: Annotated[int, typer.Option()] = 50,
    db_path: Annotated[Path | None, typer.Option("--db")] = None,
) -> None:
    """Channels with the most videos in the mapped datasets (outreach list)."""
    conn = db.connect(db_path or config.db_path())
    db.init_schema(conn)
    for channel_id, title, n in db.top_channels(conn, limit):
        typer.echo(f"{n:6,}  {channel_id}  {title}")


def channel_videos_for_check(conn, client: YouTubeApiClient, ref):
    """Prefer the local channel mapping (1 API unit) over walking the uploads playlist."""
    from .platforms.base import Video

    if db.mapping_complete(conn):
        info = client.resolve_channel(ref)
        videos = [
            Video(vid, title or "") for vid, title in db.videos_for_channel(conn, info.channel_id)
        ]
        return info, videos, "channel mapping"
    info, videos = client.channel_lookup(ref)
    return info, videos, "uploads playlist"


def _split_ids(value: str | None) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.replace("\n", ",").split(",") if v.strip()]


@app.command()
def check(
    spotify: Annotated[str | None, typer.Option(help="Spotify artist URL.")] = None,
    youtube: Annotated[str | None, typer.Option(help="YouTube channel URL.")] = None,
    spotify_ids: Annotated[
        str | None, typer.Option(help="Comma-separated Spotify track ids.")
    ] = None,
    youtube_ids: Annotated[
        str | None, typer.Option(help="Comma-separated YouTube video ids.")
    ] = None,
    artist: Annotated[
        str | None, typer.Option(help="Artist name to match in LAION-DISCO-12M (probable).")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print the result as JSON.")] = False,
    db_path: Annotated[Path | None, typer.Option("--db")] = None,
) -> None:
    """Check platform ids against the loaded datasets and print hits."""
    from .artists import match_artists
    from .check import format_artists

    s_ids = _split_ids(spotify_ids)
    y_ids = _split_ids(youtube_ids)
    conn = db.connect(db_path or config.db_path())
    db.init_schema(conn)
    artist_channel_id: str | None = None
    artist_names: list[str] = [artist] if artist else []
    try:
        if spotify:
            artist = parse_spotify_artist_url(spotify)
            _err(f"parsed Spotify artist id {artist.artist_id}")
            s_ids += [t.track_id for t in SPOTIFY_CLIENT.artist_tracks(artist)]
        if youtube:
            channel = parse_youtube_channel_url(youtube)
            _err(f"parsed YouTube channel {channel.kind}={channel.value}")
            client = youtube_client(conn)
            if isinstance(client, YouTubeApiClient):
                info, videos, source = channel_videos_for_check(conn, client, channel)
                _err(
                    f"channel {info.title!r} ({info.channel_id}): {len(videos):,} videos ({source})"
                )
            else:
                videos = client.channel_videos(channel)
            y_ids += [v.video_id for v in videos]
    except UnsupportedUrl as exc:
        _err(f"error: {exc}")
        raise typer.Exit(2) from None
    except PlatformUnavailable as exc:
        _err(f"error: {exc}")
        raise typer.Exit(2) from None
    matches = match_artists(conn, channel_id=artist_channel_id, names=artist_names)
    if not s_ids and not y_ids and not matches:
        _err("nothing to check: pass --spotify-ids/--youtube-ids/--artist or an artist/channel URL")
        raise typer.Exit(2)

    result = run_check(conn, s_ids, y_ids)
    if as_json:
        payload = result.to_dict()
        payload["artists"] = [{**m.to_dict(), "song_ids": m.song_ids} for m in matches]
        payload["not_legal_advice"] = config.NOT_LEGAL_ADVICE
        payload["tool_version"] = __version__
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        typer.echo(format_hits(result))
        if matches:
            typer.echo("")
            typer.echo(format_artists(matches))
    sys.exit(0)


if __name__ == "__main__":
    app()


@app.command()
def serve(
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option()] = 8000,
    db_path: Annotated[Path | None, typer.Option("--db")] = None,
    reload: Annotated[bool, typer.Option(help="Auto-reload on code changes (dev).")] = False,
) -> None:
    """Run the web UI."""
    import uvicorn

    from .web import create_app

    if db_path:
        os.environ["YOUTRAINED_DB"] = str(db_path)
    # Trust X-Forwarded-* from the platform proxy so share URLs and OG tags are https.
    opts = {"host": host, "port": port, "proxy_headers": True, "forwarded_allow_ips": "*"}
    if reload:
        uvicorn.run("youtrained.web:create_app", reload=True, factory=True, **opts)
    else:
        uvicorn.run(create_app(), **opts)
