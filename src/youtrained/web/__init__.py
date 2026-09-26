"""FastAPI app: one input box, a stable report URL per channel, JSON/PDF export, OG image."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import __version__, config, db
from .. import labels as L
from ..artists import match_artists
from ..check import run_check
from ..og import render_og_png
from ..pdf import PdfUnavailable, html_to_pdf
from ..platforms import (
    PlatformCache,
    PlatformUnavailable,
    StaticYouTubeClient,
    Video,
    YouTubeApiClient,
    YouTubeClient,
)
from ..report import build_report, clip_urls, headline, report_json, report_sha256
from ..urls import UnsupportedUrl, parse_youtube_channel_url

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
LABEL_PAGE_SIZE = 50
NOINDEX_BELOW = 5  # label pages with fewer videos are noindex (thin content)
_CACHE_TTL_S = 3600


class _Cached:
    """Per-process cache of the label slug map and direct counts (one index scan each)."""

    def __init__(self) -> None:
        self.at: float | None = None  # None = never filled; monotonic() is small at boot
        self.slugs: dict[str, str] = {}
        self.counts: dict[int, int] = {}

    def get(self, conn: sqlite3.Connection) -> _Cached:
        if self.at is None or time.monotonic() - self.at > _CACHE_TTL_S:
            self.slugs = L.label_slugs(conn)
            self.counts = L.direct_counts(conn)
            self.at = time.monotonic()
        return self

    def invalidate(self) -> None:
        self.at = None


def default_youtube_client(conn: sqlite3.Connection) -> YouTubeClient:
    key = config.youtube_api_key()
    return YouTubeApiClient(key, cache=PlatformCache(conn)) if key else StaticYouTubeClient()


def create_app(
    db_path: Path | str | None = None,
    youtube_client: Callable[[sqlite3.Connection], YouTubeClient] = default_youtube_client,
) -> FastAPI:
    app = FastAPI(title="YouTrained", version=__version__, docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    path = db_path or config.db_path()

    cache = _Cached()
    app.state.label_cache = cache

    def connect() -> sqlite3.Connection:
        conn = db.connect(path)
        db.init_schema(conn)
        return conn

    def render(name: str, request: Request, status: int = 200, **ctx) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            name,
            {"version": __version__, "ga_id": config.ga_measurement_id(), **ctx},
            status_code=status,
        )

    def load_report(report_id: str) -> dict:
        conn = connect()
        try:
            report = db.get_report(conn, report_id)
        finally:
            conn.close()
        if report is None:
            raise HTTPException(404, "no such report")
        return report

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return render("index.html", request)

    @app.post("/check")
    def check(request: Request, youtube: str = Form("")):
        youtube = youtube.strip()
        if not youtube:
            return render("index.html", request, 400, error="Paste your YouTube channel link.")
        try:
            ref = parse_youtube_channel_url(youtube)
        except UnsupportedUrl as exc:
            return render("index.html", request, 400, error=str(exc), youtube=youtube)
        conn = connect()
        try:
            client = youtube_client(conn)
            checked_override = None
            if isinstance(client, YouTubeApiClient):
                if db.mapping_complete(conn):
                    channel = client.resolve_channel(ref)
                    videos = [
                        Video(vid, title or "")
                        for vid, title in db.videos_for_channel(conn, channel.channel_id)
                    ]
                    checked_override = channel.video_count
                else:
                    channel, videos = client.channel_lookup(ref)
                subject_id, subject_title = channel.channel_id, channel.title
            else:
                videos = client.channel_videos(ref)
                subject_id, subject_title = ref.value, ref.value
            result = run_check(conn, youtube_ids=[v.video_id for v in videos])
            matches = match_artists(
                conn,
                channel_id=subject_id if subject_id.startswith("UC") else None,
                names=[subject_title] if subject_title else [],
            )
            report = build_report(
                conn,
                result,
                platform="yt",
                subject_id=subject_id,
                subject_title=subject_title,
                subject_url=f"https://www.youtube.com/channel/{subject_id}",
                titles={v.video_id: v.title for v in videos},
                artist_matches=matches,
            )
            if checked_override is not None:
                report["summary"]["checked"] = max(checked_override, result.checked)
            db.save_report(conn, report)
        except PlatformUnavailable as exc:
            return render("index.html", request, 503, error=str(exc), youtube=youtube)
        finally:
            conn.close()
        return RedirectResponse(f"/r/{report['report_id']}", status_code=303)

    # Export routes are registered before the page route: a bare {report_id} would otherwise
    # swallow "yt_x.json" as a report id.
    @app.get("/r/{report_id}.json")
    def report_as_json(report_id: str):
        report = load_report(report_id)
        return Response(
            report_json(report),
            media_type="application/json",
            headers={
                "X-Report-SHA256": report_sha256(report),
                "Content-Disposition": f'attachment; filename="{report_id}.json"',
            },
        )

    @app.get("/r/{report_id}.pdf")
    def report_as_pdf(request: Request, report_id: str):
        report = load_report(report_id)
        html = templates.get_template("report_pdf.html").render(
            report=report,
            headline=headline(report),
            sha256=report_sha256(report),
            version=__version__,
            base_url=str(request.base_url).rstrip("/"),
        )
        try:
            pdf = html_to_pdf(html)
        except PdfUnavailable as exc:
            return JSONResponse({"error": str(exc)}, status_code=501)
        return Response(
            pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{report_id}.pdf"'},
        )

    @app.get("/r/{report_id}/og.png")
    def report_og(report_id: str):
        report = load_report(report_id)
        subtitle = report["subject"]["title"] or report["subject"]["id"]
        return Response(render_og_png(headline(report), subtitle), media_type="image/png")

    @app.get("/r/{report_id}", response_class=HTMLResponse)
    def report_page(request: Request, report_id: str):
        report = load_report(report_id)
        conn = connect()
        try:
            slugs = cache.get(conn).slugs
        finally:
            conn.close()
        return render(
            "report.html",
            request,
            report=report,
            headline=headline(report),
            sha256=report_sha256(report),
            base_url=str(request.base_url).rstrip("/"),
            label_slugs=slugs,
        )

    # --- label pages ---------------------------------------------------------

    @app.get("/labels", response_class=HTMLResponse)
    def labels_index(request: Request):
        conn = connect()
        try:
            counts = cache.get(conn).counts
            root = L.label_by_name(conn, "Music")
            tree = []
            if root is not None:

                def walk(label: L.Label, depth: int, seen: set[int]) -> None:
                    if label.id in seen:
                        return
                    seen.add(label.id)
                    sub = [c for c in L.label_children(conn, label.id)]
                    if (
                        counts.get(label.id, 0)
                        or any(counts.get(c.id, 0) for c in sub)
                        or depth == 0
                    ):
                        tree.append((depth, label, counts.get(label.id, 0)))
                        for child in sub:
                            walk(child, depth + 1, seen)

                walk(root, 0, set())
        finally:
            conn.close()
        return render("labels.html", request, tree=tree)

    @app.get("/label/{slug}", response_class=HTMLResponse)
    def label_page(
        request: Request,
        slug: str,
        page: int = Query(1, ge=1),
        subtree: int = Query(0, ge=0, le=1),
    ):
        conn = connect()
        try:
            label = L.label_by_slug(conn, slug)
            if label is None:
                raise HTTPException(404, "no such label")
            counts = cache.get(conn).counts
            total = L.label_video_count(conn, label.id, subtree=bool(subtree))
            subtree_total = L.label_video_count(conn, label.id, subtree=True)
            rows = L.label_videos_page(
                conn, label.id, page=page, per_page=LABEL_PAGE_SIZE, subtree=bool(subtree)
            )
            clips = {}
            if rows:
                for h in db.find_hits(conn, "youtube_video", [r[0] for r in rows]):
                    if h.dataset == "audioset" and h.key not in clips:
                        clips[h.key] = clip_urls(h)
            children = [(c, counts.get(c.id, 0)) for c in L.label_children(conn, label.id)]
            crumbs = L.breadcrumb(conn, label)
            parents = L.label_parents(conn, label.id)
        finally:
            conn.close()
        pages = max(1, -(-total // LABEL_PAGE_SIZE))
        if page > pages:
            raise HTTPException(404, "no such page")
        return render(
            "label.html",
            request,
            label=label,
            direct_total=counts.get(label.id, 0),
            subtree_total=subtree_total,
            total=total,
            subtree=bool(subtree),
            rows=rows,
            clips=clips,
            children=children,
            crumbs=crumbs,
            parents=parents,
            page=page,
            pages=pages,
            noindex=total < NOINDEX_BELOW,
        )

    return app
