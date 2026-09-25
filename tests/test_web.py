"""Web UI end to end with a static YouTube client and fixture-loaded db. No network."""

import hashlib
import json

import pytest
from conftest import FIXTURES
from fastapi.testclient import TestClient

from youtrained import db
from youtrained.loaders import LOADERS, run_loader
from youtrained.loaders.audioset import iter_segments
from youtrained.platforms import StaticYouTubeClient, Video
from youtrained.urls import ChannelRef
from youtrained.web import create_app


@pytest.fixture
def shared_id() -> str:
    return next(iter_segments(FIXTURES / "eval_segments.csv"))[0]


@pytest.fixture
def client(tmp_path, cache_dir, shared_id):
    db_path = tmp_path / "web.sqlite"
    conn = db.connect(db_path)
    db.init_schema(conn)
    for name in LOADERS:
        run_loader(LOADERS[name], conn, cache_dir, log=lambda _: None)
    conn.close()
    videos = {
        ChannelRef("handle", "someband"): [
            Video(shared_id, "Our guitar song"),
            Video("zzzzzzzzzzz", "Unmatched"),
        ],
        ChannelRef("handle", "nobody"): [Video("yyyyyyyyyyy", "Nothing here")],
        ChannelRef("handle", "Artist 3"): [Video("yyyyyyyyyyy", "Nothing here")],
    }
    from youtrained.artists import build_artist_index

    conn = db.connect(db_path)
    build_artist_index(conn, log=lambda _: None)
    conn.close()
    app = create_app(db_path, youtube_client=lambda conn: StaticYouTubeClient(videos))
    return TestClient(app)


def test_index_renders_form(client):
    r = client.get("/")
    assert r.status_code == 200
    assert 'name="youtube"' in r.text and "LAION-DISCO-12M" in r.text
    assert client.get("/static/style.css").status_code == 200


def test_bad_url_shows_error(client):
    r = client.post("/check", data={"youtube": "https://youtu.be/dQw4w9WgXcQ"})
    assert r.status_code == 400 and "channel link instead" in r.text
    r = client.post("/check", data={"youtube": ""})
    assert r.status_code == 400 and "Paste your YouTube channel link" in r.text


def test_unknown_channel_is_503(client):
    r = client.post(
        "/check", data={"youtube": "https://youtube.com/@unknown"}, follow_redirects=False
    )
    assert r.status_code == 503 and "YOUTUBE_API_KEY" in r.text


def test_check_redirects_to_stable_report(client, shared_id):
    r = client.post(
        "/check", data={"youtube": "https://youtube.com/@someband"}, follow_redirects=False
    )
    assert r.status_code == 303 and r.headers["location"] == "/r/yt_someband"
    page = client.get("/r/yt_someband")
    assert page.status_code == 200
    assert "1 of your 2 videos appear in AI training datasets" in page.text
    assert "Our guitar song" in page.text
    assert (
        f"embed/{shared_id}?start=10&amp;end=20" in page.text
        or f"embed/{shared_id}?start=10&end=20" in page.text
    )
    assert "Guitar Lesson" in page.text, "LAION-DISCO row shows the dataset's own title"
    assert f'src="https://i.ytimg.com/vi/{shared_id}/mqdefault.jpg"' in page.text
    assert (
        f'data-id="{shared_id}" data-start="10" data-end="20"'
        in page.text.replace("\n", " ").replace("  ", " ")
        or 'data-start="10"' in page.text
    )
    assert "youtube-nocookie.com/embed/" in page.text and "<iframe" not in page.text, (
        "players load only on click"
    )
    assert "What you can do" in page.text and "not legal advice" in page.text
    assert (
        '<meta property="og:image" content="http://testserver/r/yt_someband/og.png">' in page.text
    )
    # re-checking the same channel overwrites the same URL rather than creating a new one
    r2 = client.post(
        "/check", data={"youtube": "https://youtube.com/@someband"}, follow_redirects=False
    )
    assert r2.headers["location"] == "/r/yt_someband"


def test_no_hits_report(client):
    client.post("/check", data={"youtube": "@nobody"})
    page = client.get("/r/yt_nobody")
    assert "0 of your 1 videos" in page.text and "No matches" in page.text


def test_json_export_and_sha(client, shared_id):
    client.post("/check", data={"youtube": "@someband"})
    r = client.get("/r/yt_someband.json")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    assert hashlib.sha256(r.content).hexdigest() == r.headers["X-Report-SHA256"]
    report = json.loads(r.text)
    assert report["summary"] == {
        "checked": 2,
        "hit_count": 4,
        "matched_keys": 1,
        "datasets": ["audioset", "laion_disco_12m"],
        "artist_matches": 1,  # channel title "someband" ~ fixture artist "Some Band"
        "artist_songs": 1,
    }
    assert report["artists"][0]["basis"] == "artist_name"
    assert (
        report["hits"][0]["urls"]["watch"] == f"https://www.youtube.com/watch?v={shared_id}&t=10s"
    )
    assert set(report["dataset_versions"]) == {"audioset", "laion_disco_12m", "musiccaps"}
    assert report["datasets"]["audioset"]["what_you_can_do"]
    page = client.get("/r/yt_someband")
    assert r.headers["X-Report-SHA256"] in page.text, "page shows the same hash as the download"


def test_og_image(client):
    client.post("/check", data={"youtube": "@someband"})
    r = client.get("/r/yt_someband/og.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n" and len(r.content) > 1000


def test_pdf_route_reports_missing_weasyprint_or_returns_pdf(client, monkeypatch):
    client.post("/check", data={"youtube": "@someband"})
    r = client.get("/r/yt_someband.pdf")
    if r.status_code == 501:
        assert "WeasyPrint" in r.json()["error"]
    else:
        assert r.status_code == 200 and r.content[:5] == b"%PDF-"


def test_pdf_template_renders(client):
    """Even without WeasyPrint the PDF HTML must render (catches template errors)."""
    from youtrained import pdf

    captured = {}

    def fake_html_to_pdf(html, base_url=None):
        captured["html"] = html
        return b"%PDF-fake"

    from youtrained import web

    web.html_to_pdf = fake_html_to_pdf
    client.post("/check", data={"youtube": "@someband"})
    r = client.get("/r/yt_someband.pdf")
    web.html_to_pdf = pdf.html_to_pdf
    assert r.status_code == 200 and r.content == b"%PDF-fake"
    assert (
        "YouTrained evidence report" in captured["html"] and "Our guitar song" in captured["html"]
    )


def test_unknown_report_404(client):
    assert client.get("/r/nope").status_code == 404
    assert client.get("/r/nope.json").status_code == 404


def test_report_renders_while_a_dataset_is_still_loading(tmp_path, client):
    """A load_state row with status 'loading' has no sha256 yet; the page must still render."""
    conn = db.connect(tmp_path / "web.sqlite")
    db.mark_load_state(conn, "laion_disco_12m", "train-00004-of-00005.parquet", "loading")
    conn.close()
    client.post("/check", data={"youtube": "@someband"})
    page = client.get("/r/yt_someband")
    assert page.status_code == 200 and "<em>loading</em>" in page.text
    r = client.get("/r/yt_someband.json")
    assert any(f["status"] == "loading" for f in r.json()["dataset_versions"]["laion_disco_12m"])


def test_artist_only_report_via_channel_title(client):
    r = client.post("/check", data={"youtube": "@Artist 3"}, follow_redirects=False)
    assert r.status_code == 303
    page = client.get(r.headers["location"])
    assert page.status_code == 200
    assert "1 songs under your artist name appear in an AI training dataset" in page.text
    assert (
        "Listed under your artist name" in page.text
        and "Probable match by artist name" in page.text
    )
    assert "Song 3" in page.text and "What you can do" in page.text
    assert "No matches" not in page.text
