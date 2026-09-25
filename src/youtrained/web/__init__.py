"""FastAPI app: one input box, a stable report URL per channel, JSON/PDF export, OG image."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import __version__, config, db
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
from ..report import build_report, headline, report_json, report_sha256
from ..urls import UnsupportedUrl, parse_youtube_channel_url

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))


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

    def connect() -> sqlite3.Connection:
        conn = db.connect(path)
        db.init_schema(conn)
        return conn

    def render(name: str, request: Request, status: int = 200, **ctx) -> HTMLResponse:
        return templates.TemplateResponse(
            request, name, {"version": __version__, **ctx}, status_code=status
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
        return render(
            "report.html",
            request,
            report=report,
            headline=headline(report),
            sha256=report_sha256(report),
            base_url=str(request.base_url).rstrip("/"),
        )

    return app
