"""Name-first front door: /search resolves a name to artist and channel pages."""

import httpx
import pytest
from conftest import FIXTURES
from fastapi.testclient import TestClient

from youtrained import db
from youtrained.artists import build_artist_index
from youtrained.loaders import LOADERS, run_loader
from youtrained.loaders.audioset import iter_segments
from youtrained.mapping import run_mapping
from youtrained.platforms import StaticYouTubeClient, YouTubeApiClient
from youtrained.web import create_app

CHAN = "UCsomebandchannel00000000"


@pytest.fixture
def client(tmp_path, cache_dir):
    path = tmp_path / "s.sqlite"
    conn = db.connect(path)
    db.init_schema(conn)
    for name in LOADERS:
        run_loader(LOADERS[name], conn, cache_dir, log=lambda _: None)
    build_artist_index(conn, log=lambda _: None)
    shared = next(iter_segments(FIXTURES / "eval_segments.csv"))[0]

    def videos(request):
        ids = request.url.params["id"].split(",")
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": v,
                        "snippet": {
                            "channelId": CHAN if v == shared else "UCother" + v[:3],
                            "channelTitle": "Some Band" if v == shared else "Other " + v[:3],
                            "title": "Video " + v,
                        },
                    }
                    for v in ids
                ]
            },
        )

    run_mapping(
        conn,
        YouTubeApiClient("k", http=httpx.Client(transport=httpx.MockTransport(videos))),
        budget=100,
        log=lambda _: None,
    )
    db.refresh_channel_stats(conn)
    conn.close()
    return TestClient(create_app(path, youtube_client=lambda conn: StaticYouTubeClient({})))


def test_home_has_question_and_name_box(client):
    home = client.get("/").text
    assert "<h1>Has your music been used to train AI?</h1>" in home
    assert 'name="name"' in home and 'name="youtube"' in home


def test_posting_a_name_goes_to_search(client):
    r = client.post("/check", data={"name": "Artist 3"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/search?q=Artist%203"
    r = client.post("/check", data={}, follow_redirects=False)
    assert r.status_code == 400


def test_exact_artist_match_redirects(client):
    r = client.get("/search?q=artist 3", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/a/UCartist0000000000000003"


def test_ambiguous_name_lists_both_kinds(client):
    r = client.get("/search?q=some band")  # fixture has artist "Some Band" and channel "Some Band"
    assert r.status_code == 200
    assert "Is one of these you?" in r.text
    assert 'href="/a/UCartist0000000000000010"' in r.text and "song listed" in r.text
    assert f'href="/r/yt_{CHAN}"' in r.text and "video listed" in r.text
    assert '<meta name="robots" content="noindex">' in r.text


def test_prefix_search_and_empty_state(client):
    r = client.get("/search?q=artist")
    assert r.status_code == 200 and r.text.count('href="/a/UCartist') >= 5
    r = client.get("/search?q=zzzzqq")
    assert r.status_code == 200 and "We couldn't find that name" in r.text and "good news" in r.text
    assert client.get("/search", follow_redirects=False).status_code == 303


def test_robots_disallows_search(client):
    assert "Disallow: /search" in client.get("/robots.txt").text
