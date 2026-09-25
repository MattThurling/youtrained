"""When the channel mapping is complete, checks use it instead of the uploads playlist."""

import httpx
from conftest import FIXTURES
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from youtrained import cli, db
from youtrained.loaders import LOADERS, run_loader
from youtrained.loaders.audioset import iter_segments
from youtrained.mapping import run_mapping
from youtrained.platforms import YouTubeApiClient
from youtrained.web import create_app

CHANNEL = {
    "id": "UCmapped00000000000000000",
    "snippet": {"title": "Mapped Band"},
    "contentDetails": {"relatedPlaylists": {"uploads": "UUmapped"}},
    "statistics": {"videoCount": "77"},
}


def _mapped_db(tmp_path, cache_dir, shared):
    db_path = tmp_path / "m.sqlite"
    conn = db.connect(db_path)
    db.init_schema(conn)
    for name in LOADERS:
        run_loader(LOADERS[name], conn, cache_dir, log=lambda _: None)

    def videos(request):
        ids = request.url.params["id"].split(",")
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": v,
                        "snippet": {
                            "channelId": CHANNEL["id"] if v == shared else "UCother",
                            "channelTitle": "x",
                            "title": "Song " + v,
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
    assert db.mapping_complete(conn)
    conn.close()
    return db_path


def _api_client():
    calls = []

    def handle(request):
        calls.append(request.url.path.rsplit("/", 1)[-1])
        if request.url.path.endswith("/channels"):
            return httpx.Response(200, json={"items": [CHANNEL]})
        raise AssertionError(f"unexpected call {request.url}")

    return YouTubeApiClient("k", http=httpx.Client(transport=httpx.MockTransport(handle))), calls


def test_cli_uses_mapping(tmp_path, cache_dir, monkeypatch):
    shared = next(iter_segments(FIXTURES / "eval_segments.csv"))[0]
    db_path = _mapped_db(tmp_path, cache_dir, shared)
    client, calls = _api_client()
    monkeypatch.setattr(cli, "youtube_client", lambda conn: client)
    res = CliRunner().invoke(cli.app, ["check", "--youtube", "@mappedband", "--db", str(db_path)])
    assert res.exit_code == 0, res.output
    assert "1 videos (channel mapping)" in res.output
    assert "4 hit(s) across 1 id(s)" in res.output
    assert calls == ["channels"], "no playlist walk"


def test_web_uses_mapping_and_channel_video_count(tmp_path, cache_dir):
    shared = next(iter_segments(FIXTURES / "eval_segments.csv"))[0]
    db_path = _mapped_db(tmp_path, cache_dir, shared)
    client, calls = _api_client()
    app = create_app(db_path, youtube_client=lambda conn: client)
    tc = TestClient(app)
    r = tc.post("/check", data={"youtube": "@mappedband"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/r/yt_{CHANNEL['id']}"
    page = tc.get(r.headers["location"])
    assert "1 of your 77 videos appear in AI training datasets" in page.text
    assert f"Song {shared}" in page.text
    assert calls == ["channels"]
