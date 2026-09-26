"""Discovery pages: pre-rendered channel reports, channel/artist indexes, sitemaps, removals."""

import httpx
import pytest
from conftest import FIXTURES
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from youtrained import cli, db
from youtrained.artists import build_artist_index
from youtrained.loaders import LOADERS, run_loader
from youtrained.loaders.audioset import iter_segments
from youtrained.mapping import run_mapping
from youtrained.platforms import StaticYouTubeClient, Video, YouTubeApiClient
from youtrained.urls import ChannelRef
from youtrained.web import create_app

BIG = "UCbig000000000000000000"  # owns the shared segment and several fixture videos


@pytest.fixture
def shared_id() -> str:
    return next(iter_segments(FIXTURES / "eval_segments.csv"))[0]


@pytest.fixture
def db_path(tmp_path, cache_dir, shared_id):
    path = tmp_path / "d.sqlite"
    conn = db.connect(path)
    db.init_schema(conn)
    for name in LOADERS:
        run_loader(LOADERS[name], conn, cache_dir, log=lambda _: None)
    build_artist_index(conn, log=lambda _: None)
    big_videos = {shared_id, *[r[0] for r in iter_segments(FIXTURES / "eval_segments.csv")][1:6]}

    def videos(request):
        ids = request.url.params["id"].split(",")
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": v,
                        "snippet": {
                            "channelId": BIG if v in big_videos else "UCsmall" + v[:3],
                            "channelTitle": "Big Channel" if v in big_videos else "Small " + v[:3],
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
    return path


@pytest.fixture
def client(db_path):
    return TestClient(create_app(db_path, youtube_client=lambda conn: StaticYouTubeClient({})))


def test_prerendered_channel_report_from_mapping(client, shared_id, db_path):
    r = client.get(f"/r/yt_{BIG}")
    assert r.status_code == 200
    assert "6 videos from this channel appear in AI training datasets" in r.text
    assert "Built from the dataset index" in r.text and "Check the full channel" in r.text
    assert f"Video {shared_id}" in r.text
    assert '<meta name="robots" content="noindex">' not in r.text, "6 videos is above the threshold"
    j = client.get(f"/r/yt_{BIG}.json").json()
    assert j["prerendered"] is True and j["summary"]["checked_known"] is False
    conn = db.connect(db_path)
    stored = db.get_report(conn, f"yt_{BIG}")
    conn.close()
    assert stored and stored["prerendered"] is True, "first render is stored for next time"
    # a live check of the same channel replaces the pre-rendered report
    live_client = TestClient(
        create_app(
            db_path,
            youtube_client=lambda conn: StaticYouTubeClient(
                {ChannelRef("id", BIG): [Video(shared_id, "Live title")]}
            ),
        )
    )
    live_client.post("/check", data={"youtube": f"https://www.youtube.com/channel/{BIG}"})
    assert "Built from the dataset index" not in client.get(f"/r/yt_{BIG}").text
    assert client.get("/r/yt_UCnotmapped000000000000").status_code == 404
    small = client.get("/channels").text
    assert "Small " in small


def test_channels_index_ranked_and_searchable(client):
    r = client.get("/channels")
    assert r.status_code == 200
    assert r.text.index("Big Channel") < r.text.index("Small "), "ranked by video count"
    assert f'href="/r/yt_{BIG}"' in r.text
    r = client.get("/channels?q=big")
    assert "1 channels match" in r.text and "Small " not in r.text
    assert '<meta name="robots" content="noindex">' in r.text, "search results are noindex"
    assert client.get("/channels?page=999").status_code == 200


def test_artists_index_and_artist_page(client, shared_id):
    r = client.get("/artists")
    assert r.status_code == 200 and 'href="/a/UCartist0000000000000010"' in r.text
    r = client.get("/artists?q=some+band")
    assert "1 artists match" in r.text and "Some Band" in r.text
    r = client.get("/a/UCartist0000000000000010")
    assert r.status_code == 200
    assert "1 song by Some Band appear in an AI training dataset" in r.text
    assert "Guitar Lesson" in r.text and shared_id in r.text
    assert '<meta name="robots" content="noindex">' in r.text, "1 song is below the threshold"
    assert client.get("/a/UCnope").status_code == 404
    assert client.get("/a/UCartist0000000000000010?page=2").status_code == 404


def test_sitemaps_and_robots(client, db_path):
    conn = db.connect(db_path)
    conn.execute("UPDATE artists SET song_count = 9 WHERE artist_id = 'UCartist0000000000000003'")
    conn.commit()
    conn.close()
    r = client.get("/sitemap.xml")
    assert r.status_code == 200 and "sitemapindex" in r.text
    assert (
        "/sitemap-labels.xml" in r.text
        and "/sitemap-channels-0.xml" in r.text
        and "/sitemap-artists-0.xml" in r.text
    )
    ch = client.get("/sitemap-channels-0.xml").text
    assert f"/r/yt_{BIG}</loc>" in ch and "UCsmall" not in ch, (
        "only channels at or above the threshold"
    )
    ar = client.get("/sitemap-artists-0.xml").text
    assert "/a/UCartist0000000000000003</loc>" in ar and "UCartist0000000000000010" not in ar
    assert client.get("/sitemap-channels-7.xml").status_code == 404
    lb = client.get("/sitemap-labels.xml").text
    assert "/labels</loc>" in lb
    robots = client.get("/robots.txt").text
    assert "Sitemap: http://testserver/sitemap.xml" in robots and "Disallow: /check" in robots


def test_removal_hides_pages_and_sitemap_entries(client, db_path):
    res = CliRunner().invoke(
        cli.app, ["remove", f"yt_{BIG}", "--reason", "asked", "--db", str(db_path)]
    )
    assert res.exit_code == 0 and "removed" in res.output
    assert client.get(f"/r/yt_{BIG}").status_code == 404
    assert f"yt_{BIG}" not in client.get("/sitemap-channels-0.xml").text
    assert "Big Channel" not in client.get("/channels").text
    res = CliRunner().invoke(cli.app, ["remove", f"yt_{BIG}", "--undo", "--db", str(db_path)])
    assert "restored" in res.output
    assert client.get(f"/r/yt_{BIG}").status_code == 200
    assert CliRunner().invoke(cli.app, ["remove", "bogus", "--db", str(db_path)]).exit_code == 2


def test_navigation_and_about(client):
    home = client.get("/").text
    for href in ("/channels", "/artists", "/labels", "/about"):
        assert f'href="{href}"' in home
    about = client.get("/about").text
    assert 'id="removal"' in about and "not a claim about the channel" in about


def test_index_channels_cli(db_path):
    res = CliRunner().invoke(cli.app, ["index-channels", "--db", str(db_path)])
    assert res.exit_code == 0 and "channels with dataset videos" in res.output
