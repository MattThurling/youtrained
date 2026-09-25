"""`youtrained check --youtube <url>` end to end with a mocked API and a loaded db."""

import httpx
from conftest import FIXTURES
from typer.testing import CliRunner

from youtrained import cli, db
from youtrained.loaders import LOADERS, run_loader
from youtrained.loaders.audioset import iter_segments
from youtrained.platforms import YouTubeApiClient

runner = CliRunner()


def test_check_youtube_url_uses_api_client(tmp_path, cache_dir, monkeypatch):
    db_path = tmp_path / "t.sqlite"
    conn = db.connect(db_path)
    db.init_schema(conn)
    for name in LOADERS:
        run_loader(LOADERS[name], conn, cache_dir, log=lambda _: None)
    conn.close()
    shared = next(iter_segments(FIXTURES / "eval_segments.csv"))[0]

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/channels"):
            assert "forHandle=someband" in str(request.url)
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "UC1",
                            "snippet": {"title": "Some Band"},
                            "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}},
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "items": [
                    {"snippet": {"title": "hit"}, "contentDetails": {"videoId": shared}},
                    {"snippet": {"title": "miss"}, "contentDetails": {"videoId": "zzzzzzzzzzz"}},
                ]
            },
        )

    monkeypatch.setenv("YOUTUBE_API_KEY", "k")
    monkeypatch.setattr(
        cli,
        "youtube_client",
        lambda conn: YouTubeApiClient(
            "k", http=httpx.Client(transport=httpx.MockTransport(handle))
        ),
    )
    res = runner.invoke(
        cli.app, ["check", "--youtube", "https://youtube.com/@someband", "--db", str(db_path)]
    )
    assert res.exit_code == 0, res.output
    assert "channel 'Some Band' (UC1): 2 videos" in res.output
    assert "checked 2 id(s), 4 hit(s) across 1 id(s)" in res.output
