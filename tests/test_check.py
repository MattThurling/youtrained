import json

from conftest import FIXTURES
from typer.testing import CliRunner

from youtrained import cli, db
from youtrained.check import format_hits, run_check
from youtrained.config import NOT_LEGAL_ADVICE
from youtrained.loaders import LOADERS, run_loader
from youtrained.loaders.audioset import iter_segments

runner = CliRunner()


def _first_musiccaps_ytid() -> str:
    return (FIXTURES / "musiccaps.csv").read_text().splitlines()[1].split(",")[0]


def _loaded(conn, cache_dir):
    for name in LOADERS:
        run_loader(LOADERS[name], conn, cache_dir, log=lambda _: None)
    return conn


def test_run_check_hits_and_no_hits(conn, cache_dir):
    _loaded(conn, cache_dir)
    ytid = _first_musiccaps_ytid()
    result = run_check(conn, youtube_ids=[ytid, "nope1234567"])
    assert result.checked == 2
    assert {h.dataset for h in result.hits} == {"musiccaps", "audioset"}
    assert len([h for h in result.hits if h.dataset == "musiccaps"]) == 2
    assert result.matched_keys == {("youtube_video", ytid)}
    assert run_check(conn, youtube_ids=["nope1234567"]).hits == []
    assert run_check(conn, spotify_ids=[ytid]).hits == [], "youtube ids never match spotify keys"


def test_format_hits_text(conn, cache_dir):
    _loaded(conn, cache_dir)
    ytid = _first_musiccaps_ytid()
    text = format_hits(run_check(conn, youtube_ids=[ytid]))
    assert text.startswith("checked 1 id(s), 3 hit(s) across 1 id(s)")
    assert f"musiccaps  row {ytid}:30:40" in text
    assert "About MusicCaps (Google Research, 2023)" in text
    assert "About AudioSet" in text
    assert text.rstrip().endswith(NOT_LEGAL_ADVICE)
    assert "checked 1 id(s), 0 hit(s)" in format_hits(run_check(conn, youtube_ids=["nope1234567"]))


def test_cli_check_youtube_ids(tmp_path, cache_dir):
    db_path = tmp_path / "t.sqlite"
    conn = db.connect(db_path)
    db.init_schema(conn)
    _loaded(conn, cache_dir)
    conn.close()
    shared = next(iter_segments(FIXTURES / "eval_segments.csv"))[0]
    res = runner.invoke(
        cli.app, ["check", "--youtube-ids", f"{shared},zzzzzzzzzzz", "--db", str(db_path)]
    )
    assert res.exit_code == 0, res.output
    assert "checked 2 id(s), 4 hit(s) across 1 id(s)" in res.output
    assert "split=eval; labels: Guitar, Speech" in res.output
    assert "Some Band, Guest - Guitar Lesson; album: Album 10; 10K plays" in res.output

    res = runner.invoke(cli.app, ["check", "--youtube-ids", shared, "--db", str(db_path), "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["hit_count"] == 4 and payload["not_legal_advice"] == NOT_LEGAL_ADVICE
    assert payload["hits"][0]["dataset"] == "audioset"


def test_cli_check_url_parses_then_reports_platform_unavailable(tmp_path):
    db_path = tmp_path / "t.sqlite"
    res = runner.invoke(
        cli.app, ["check", "--youtube", "https://youtube.com/@someband", "--db", str(db_path)]
    )
    assert res.exit_code == 2
    assert "parsed YouTube channel handle=someband" in res.output
    assert "YOUTUBE_API_KEY" in res.output

    res = runner.invoke(
        cli.app, ["check", "--spotify", "https://open.spotify.com/track/4Z8W4fKeB5YxbusRsdQVPb"]
    )
    assert res.exit_code == 2 and "artist page link" in res.output

    res = runner.invoke(cli.app, ["check"])
    assert res.exit_code == 2 and "nothing to check" in res.output
