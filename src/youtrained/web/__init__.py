"""FastAPI app: one input box, a stable report URL per channel, JSON/PDF export, OG image."""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import __version__, config, db
from .. import labels as L
from ..artists import (
    artist_by_id,
    artists_above,
    artists_page,
    count_artists_above,
    match_artists,
    normalize_name,
)
from ..check import run_check
from ..descriptors import load_descriptor
from ..links import artist_id_from_path, artist_url, report_id_from_path, report_url
from ..models import KEY_YOUTUBE_VIDEO, Hit
from ..og import render_og_png
from ..pdf import PdfFailed, PdfUnavailable, html_to_pdf
from ..platforms import (
    PlatformCache,
    PlatformUnavailable,
    StaticYouTubeClient,
    Video,
    YouTubeApiClient,
    YouTubeClient,
)
from ..report import (
    build_report,
    clip_urls,
    common_text,
    headline,
    overview_cards,
    prerendered_channel_report,
    report_json,
    report_sha256,
    verdict_parts,
)
from ..urls import UnsupportedUrl, looks_like_channel, parse_youtube_channel_url

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
templates.env.globals.update(artist_url=artist_url, report_url=report_url)


def _static_version() -> str:
    """Short content hash of the stylesheet, used as a cache-busting query string."""
    import hashlib

    css = HERE / "static" / "style.css"
    return hashlib.sha256(css.read_bytes()).hexdigest()[:10] if css.exists() else "0"


STATIC_VERSION = _static_version()
log = logging.getLogger(__name__)
LABEL_PAGE_SIZE = 50
PAGE_SIZE = 50
SITEMAP_CHUNK = 50_000
PDF_PER_MINUTE = 6  # WeasyPrint renders are CPU-heavy; a crawl must not be able to saturate the box
DOWNLOAD_CACHE = "public, max-age=86400"  # lets the CDN serve repeat downloads without touching us


class _RateLimiter:
    """Tiny in-process limiter: at most `limit` events per rolling minute."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.stamps: list[float] = []

    def allow(self) -> bool:
        now = time.monotonic()
        self.stamps = [t for t in self.stamps if now - t < 60]
        if len(self.stamps) >= self.limit:
            return False
        self.stamps.append(now)
        return True


NOINDEX_BELOW = 5  # label pages with fewer videos are noindex (thin content)
_CACHE_TTL_S = 3600


class _Cached:
    """Per-process cache of the label slug map and direct counts (one index scan each), plus
    the artist, channel and labelled-video totals the home page shows."""

    def __init__(self) -> None:
        self.at: float | None = None  # None = never filled; monotonic() is small at boot
        self.slugs: dict[str, str] = {}
        self.counts: dict[int, int] = {}
        self.artists = 0
        self.channels = 0
        self.labelled_videos = 0

    def get(self, conn: sqlite3.Connection) -> _Cached:
        if self.at is None or time.monotonic() - self.at > _CACHE_TTL_S:
            self.slugs = L.label_slugs(conn)
            self.counts = L.direct_counts(conn)
            self.artists = count_artists_above(conn, 1)
            self.channels = conn.execute(
                "SELECT COUNT(*) FROM channel_stats WHERE video_count >= 1"
            ).fetchone()[0]
            # Every indexed AudioSet video carries at least one Music-branch label (about a
            # second over a million rows, so it lives in this hourly cache).
            self.labelled_videos = conn.execute(
                "SELECT COUNT(DISTINCT video_id) FROM video_labels"
            ).fetchone()[0]
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
    pdf_limiter = _RateLimiter(PDF_PER_MINUTE)
    app.state.pdf_limiter = pdf_limiter
    # One render at a time: each is a child process of a few hundred MB on a 700 MB container.
    pdf_lock = threading.Lock()
    app.state.pdf_lock = pdf_lock

    def connect() -> sqlite3.Connection:
        conn = db.connect(path)
        db.init_schema(conn)
        return conn

    def canonical_for(request: Request) -> str:
        """Scheme+host+path, keeping only a `page` query parameter, so variants collapse."""
        base = str(request.base_url).rstrip("/")
        page = request.query_params.get("page")
        suffix = f"?page={page}" if page and page != "1" else ""
        return f"{base}{request.url.path}{suffix}"

    def render(name: str, request: Request, status: int = 200, **ctx) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            name,
            {
                "version": __version__,
                "ga_id": config.ga_measurement_id(),
                "presence_note": config.PRESENCE_NOTE,
                "static_v": STATIC_VERSION,
                "canonical": canonical_for(request),
                **ctx,
            },
            status_code=status,
        )

    def load_report(report_id: str) -> dict:
        """Stored report, or one pre-rendered from the channel mapping; 404 if neither/removed."""
        conn = connect()
        try:
            if db.is_removed(conn, report_id):
                raise HTTPException(404, "this page has been removed at the subject's request")
            report = db.get_report(conn, report_id)
            if report is None and report_id.startswith("yt_UC"):
                report = prerendered_channel_report(conn, report_id.removeprefix("yt_"))
                if report is not None:
                    # Store it: the next visit (or crawler) reads one row instead of rebuilding.
                    db.save_report(conn, report)
        finally:
            conn.close()
        if report is None:
            raise HTTPException(404, "no such report")
        return report

    def pages_for(total: int, per_page: int = PAGE_SIZE) -> int:
        return max(1, -(-total // per_page))

    def home(request: Request, status: int = 200, **ctx) -> HTMLResponse:
        """The home page, with the browse-row totals from the hourly cache."""
        conn = connect()
        try:
            c = cache.get(conn)
        finally:
            conn.close()
        return render(
            "index.html",
            request,
            status,
            artists=c.artists,
            channels=c.channels,
            labelled_videos=c.labelled_videos,
            **ctx,
        )

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return home(request)

    @app.get("/about", response_class=HTMLResponse)
    def about(request: Request):
        return render("about.html", request, contact_email=config.contact_email())

    @app.get("/robots.txt", response_class=PlainTextResponse)
    def robots(request: Request):
        base = str(request.base_url).rstrip("/")
        # Downloads (JSON, PDF, OG image) are generated per request; keep crawlers off them.
        return (
            "User-agent: *\nAllow: /\nDisallow: /check\nDisallow: /search\n"
            "Disallow: /*.json$\nDisallow: /*.pdf$\nDisallow: /*/og.png$\n"
            f"Sitemap: {base}/sitemap.xml\n"
        )

    # --- discovery: channels ---------------------------------------------------

    @app.get("/channels", response_class=HTMLResponse)
    def channels_index(
        request: Request, q: str = Query("", max_length=100), page: int = Query(1, ge=1)
    ):
        conn = connect()
        try:
            rows, total = db.channels_page(conn, q=q.strip() or None, page=page, per_page=PAGE_SIZE)
            removed = db.removed_ids(conn)
        finally:
            conn.close()
        rows = [r for r in rows if f"yt_{r[0]}" not in removed]
        return render(
            "channels.html",
            request,
            rows=rows,
            total=total,
            q=q.strip(),
            page=page,
            pages=pages_for(total),
            noindex=bool(q),
        )

    # --- discovery: artists ----------------------------------------------------

    @app.get("/artists", response_class=HTMLResponse)
    def artists_index(
        request: Request, q: str = Query("", max_length=100), page: int = Query(1, ge=1)
    ):
        conn = connect()
        try:
            rows, total = artists_page(conn, q=q.strip() or None, page=page, per_page=PAGE_SIZE)
            removed = db.removed_ids(conn)
        finally:
            conn.close()
        rows = [r for r in rows if f"a_{r[0]}" not in removed]
        return render(
            "artists.html",
            request,
            rows=rows,
            total=total,
            q=q.strip(),
            page=page,
            pages=pages_for(total),
            noindex=bool(q),
        )

    @app.get("/a/{slug_id}", response_class=HTMLResponse)
    def artist_page(request: Request, slug_id: str, page: int = Query(1, ge=1)):
        artist_id = artist_id_from_path(slug_id)
        if artist_id is None:
            raise HTTPException(404, "no such artist")
        conn = connect()
        try:
            if db.is_removed(conn, f"a_{artist_id}"):
                raise HTTPException(404, "this page has been removed at the subject's request")
            artist = artist_by_id(conn, artist_id)
            if artist is None:
                raise HTTPException(404, "no such artist")
            pages = pages_for(artist.song_count)
            if page > pages:
                raise HTTPException(404, "no such page")
            ids = artist.song_ids[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
            hits = [
                {**h.to_dict(), "your_title": h.title, "urls": clip_urls(h), "basis": "artist_id"}
                for h in db.find_hits(conn, "youtube_video", ids)
                if h.dataset == "laion_disco_12m"
            ]
            desc = load_descriptor("laion_disco_12m")
        finally:
            conn.close()
        base = str(request.base_url).rstrip("/")
        return render(
            "artist.html",
            request,
            artist=artist,
            hits=hits,
            page=page,
            pages=pages,
            dataset=desc,
            canonical=base
            + artist_url(artist_id, artist.name)
            + (f"?page={page}" if page > 1 else ""),
            noindex=artist.song_count < config.DISCOVERY_MIN,
        )

    # --- sitemaps ---------------------------------------------------------------

    def _urlset(urls: list[str]) -> Response:
        body = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
        return Response(
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>',
            media_type="application/xml",
        )

    @app.get("/sitemap.xml")
    def sitemap_index(request: Request):
        base = str(request.base_url).rstrip("/")
        conn = connect()
        try:
            n_channels = conn.execute(
                "SELECT COUNT(*) FROM channel_stats WHERE video_count >= ?", (config.DISCOVERY_MIN,)
            ).fetchone()[0]
            n_artists = count_artists_above(conn, config.DISCOVERY_MIN)
        finally:
            conn.close()
        parts = ["labels"]
        parts += [f"channels-{i}" for i in range(pages_for(n_channels, SITEMAP_CHUNK))]
        parts += [f"artists-{i}" for i in range(pages_for(n_artists, SITEMAP_CHUNK))]
        body = "".join(f"<sitemap><loc>{base}/sitemap-{p}.xml</loc></sitemap>" for p in parts)
        return Response(
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</sitemapindex>',
            media_type="application/xml",
        )

    @app.get("/sitemap-labels.xml")
    def sitemap_labels(request: Request):
        base = str(request.base_url).rstrip("/")
        conn = connect()
        try:
            counts = cache.get(conn).counts
            slugs = [
                s
                for lid, s in conn.execute("SELECT id, slug FROM labels ORDER BY id")
                if counts.get(lid, 0) >= config.DISCOVERY_MIN
            ]
        finally:
            conn.close()
        return _urlset([f"{base}/labels"] + [f"{base}/label/{s}" for s in slugs])

    @app.get("/sitemap-channels-{n}.xml")
    def sitemap_channels(request: Request, n: int):
        base = str(request.base_url).rstrip("/")
        conn = connect()
        try:
            ids = conn.execute(
                "SELECT channel_id, channel_title FROM channel_stats WHERE video_count >= ? "
                "ORDER BY channel_id LIMIT ? OFFSET ?",
                (config.DISCOVERY_MIN, SITEMAP_CHUNK, n * SITEMAP_CHUNK),
            ).fetchall()
            removed = db.removed_ids(conn)
        finally:
            conn.close()
        if not ids and n > 0:
            raise HTTPException(404)
        return _urlset(
            [base + report_url(f"yt_{c}", t) for c, t in ids if f"yt_{c}" not in removed]
        )

    @app.get("/sitemap-artists-{n}.xml")
    def sitemap_artists(request: Request, n: int):
        base = str(request.base_url).rstrip("/")
        conn = connect()
        try:
            ids = artists_above(
                conn, config.DISCOVERY_MIN, limit=SITEMAP_CHUNK, offset=n * SITEMAP_CHUNK
            )
            removed = db.removed_ids(conn)
        finally:
            conn.close()
        if not ids and n > 0:
            raise HTTPException(404)
        return _urlset([base + artist_url(a, name) for a, name in ids if f"a_{a}" not in removed])

    @app.get("/search", response_class=HTMLResponse)
    def search(request: Request, q: str = Query("", max_length=100)):
        q = q.strip()
        if not q:
            return RedirectResponse("/", status_code=303)
        norm = normalize_name(q)
        conn = connect()
        try:
            artists, _ = artists_page(conn, q=q, page=1, per_page=8)
            channels, _ = db.channels_page(conn, q=q, page=1, per_page=8)
            removed = db.removed_ids(conn)
        finally:
            conn.close()
        artists = [a for a in artists if f"a_{a[0]}" not in removed]
        channels = [c for c in channels if f"yt_{c[0]}" not in removed]
        exact_a = [a for a in artists if normalize_name(a[1] or "") == norm]
        exact_c = [c for c in channels if normalize_name(c[1] or "") == norm]
        # exact-name matches first, then by count
        artists = exact_a + [a for a in artists if a not in exact_a]
        channels = exact_c + [c for c in channels if c not in exact_c]
        # One exact-name match on one side and none on the other: go straight there. Partial
        # matches ("bonobos", "RadioBonobo") don't count against an exact hit.
        if len(exact_a) == 1 and not exact_c:
            return RedirectResponse(artist_url(exact_a[0][0], exact_a[0][1]), status_code=303)
        if len(exact_c) == 1 and not exact_a:
            return RedirectResponse(
                report_url(f"yt_{exact_c[0][0]}", exact_c[0][1]), status_code=303
            )
        return render("search.html", request, q=q, artists=artists, channels=channels, noindex=True)

    @app.post("/check")
    def check(request: Request, q: str = Form(""), youtube: str = Form(""), name: str = Form("")):
        """One box. A handle, channel id or YouTube link goes to the channel lookup; anything
        else is a name and goes to search. `youtube` and `name` are the older field names,
        still used by the prerendered report's "check all my uploads" form."""
        youtube = youtube.strip()
        raw = (q or name).strip()
        if not youtube:
            if not raw:
                return home(
                    request, 400, error="Type your artist name or paste your YouTube channel link."
                )
            if not looks_like_channel(raw):
                return RedirectResponse(f"/search?q={quote(raw)}", status_code=303)
            youtube = raw
        try:
            ref = parse_youtube_channel_url(youtube)
        except UnsupportedUrl as exc:
            return home(request, 400, error=str(exc), q=youtube)
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
            return home(request, 503, error=str(exc), q=youtube)
        finally:
            conn.close()
        return RedirectResponse(
            report_url(report["report_id"], report["subject"]["title"]), status_code=303
        )

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
                "Cache-Control": DOWNLOAD_CACHE,
            },
        )

    @app.get("/r/{report_id}.pdf")
    def report_as_pdf(request: Request, report_id: str):
        report = load_report(report_id)
        if not pdf_limiter.allow():
            return Response(
                "Too many PDF requests right now; please try again in a minute.",
                status_code=429,
                headers={"Retry-After": "60", "Cache-Control": "no-store"},
            )
        if not pdf_lock.acquire(blocking=False):
            return Response(
                "A PDF is already being rendered; please try again shortly.",
                status_code=503,
                headers={"Retry-After": "30", "Cache-Control": "no-store"},
            )
        try:
            html = templates.get_template("report_pdf.html").render(
                report=report,
                headline=headline(report),
                sha256=report_sha256(report),
                version=__version__,
                base_url=str(request.base_url).rstrip("/"),
            )
            pdf = html_to_pdf(html)
        except PdfUnavailable as exc:
            return JSONResponse({"error": str(exc)}, status_code=501)
        except PdfFailed:
            log.exception("PDF render failed for %s", report_id)
            return Response(
                "The PDF could not be generated right now; please try again in a minute.",
                status_code=503,
                headers={"Retry-After": "60", "Cache-Control": "no-store"},
            )
        finally:
            pdf_lock.release()
        return Response(
            pdf,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{report_id}.pdf"',
                "Cache-Control": DOWNLOAD_CACHE,
            },
        )

    @app.get("/r/{report_id}/og.png")
    def report_og(report_id: str):
        report = load_report(report_id)
        subtitle = report["subject"]["title"] or report["subject"]["id"]
        return Response(
            render_og_png(headline(report), subtitle),
            media_type="image/png",
            headers={"Cache-Control": DOWNLOAD_CACHE},
        )

    @app.get("/r/{slug_id}", response_class=HTMLResponse)
    def report_page(request: Request, slug_id: str):
        report_id = report_id_from_path(slug_id)
        if report_id is None:
            raise HTTPException(404, "no such report")
        report = load_report(report_id)
        conn = connect()
        try:
            slugs = cache.get(conn).slugs
        finally:
            conn.close()
        yn, verdict_text = verdict_parts(report)
        return render(
            "report.html",
            request,
            report=report,
            headline=headline(report),
            yn=yn,
            verdict_text=verdict_text,
            common=common_text(report),
            cards=overview_cards(report),
            sha256=report_sha256(report),
            base_url=str(request.base_url).rstrip("/"),
            canonical=str(request.base_url).rstrip("/")
            + report_url(report_id, report["subject"]["title"]),
            label_slugs=slugs,
            noindex=report["summary"]["matched_keys"] + report["summary"].get("artist_songs", 0)
            < config.DISCOVERY_MIN,
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
            # One AudioSet row per video, rendered with the same partial as report evidence
            # so the clip player and the full label list look the same everywhere.
            by_key: dict[str, Hit] = {}
            if rows:
                for h in db.find_hits(conn, KEY_YOUTUBE_VIDEO, [r[0] for r in rows]):
                    if h.dataset == "audioset" and h.key not in by_key:
                        by_key[h.key] = h
            hits = []
            for vid, title, channel_id, channel_title in rows:
                h = by_key.get(vid)
                if h is None:
                    continue
                hits.append(
                    {
                        **h.to_dict(),
                        "your_title": title,
                        "urls": clip_urls(h),
                        "channel_id": channel_id,
                        "channel_title": channel_title,
                    }
                )
            slugs = cache.get(conn).slugs
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
            hits=hits,
            label_slugs=slugs,
            report={"datasets": {"audioset": {"name": load_descriptor("audioset").name}}},
            children=children,
            crumbs=crumbs,
            parents=parents,
            page=page,
            pages=pages,
            noindex=total < NOINDEX_BELOW,
        )

    return app
