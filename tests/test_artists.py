"""Artist index and matching over the LAION-DISCO-12M fixture."""

from conftest import FIXTURES
from typer.testing import CliRunner

from youtrained import cli, db
from youtrained.artists import (
    artist_by_id,
    artists_by_name,
    build_artist_index,
    match_artists,
    normalize_name,
)
from youtrained.check import run_check
from youtrained.loaders import LOADERS, run_loader
from youtrained.loaders.audioset import iter_segments
from youtrained.report import build_report, headline


def _indexed(conn, cache_dir):
    for name in LOADERS:
        run_loader(LOADERS[name], conn, cache_dir, log=lambda _: None)
    return build_artist_index(conn, log=lambda _: None)


def test_normalize_name():
    assert normalize_name("Bonobo") == "bonobo"
    assert normalize_name("Bonobo - Topic") == "bonobo"
    assert normalize_name("BonoboVEVO") == "bonobo"
    assert normalize_name("Björk Official") == "bjork"
    assert normalize_name("The Black Keys") == "theblackkeys"
    assert normalize_name("  ") == ""


def test_index_groups_songs_by_artist(conn, cache_dir):
    n = _indexed(conn, cache_dir)
    # fixture: artists 0..9, 10 (two artists), 11 (dup song), 20..26 -> ids "UCartist00000000000000NN"
    assert n == 20
    m = artist_by_id(conn, "UCartist0000000000000010")
    assert m and m.name == "Some Band" and m.song_count == 1 and m.basis == "artist_id"
    shared = next(iter_segments(FIXTURES / "eval_segments.csv"))[0]
    assert m.song_ids == [shared]
    dup = artist_by_id(conn, "UCartist0000000000000011")
    assert dup.song_count == 1, "duplicate song across files counted once"
    assert artist_by_id(conn, "UCnothing") is None
    assert _indexed(conn, cache_dir) == 20, "rebuild is idempotent"
    guest = artist_by_id(conn, "UCguest00000000000000000")
    assert guest and guest.name == "Guest" and guest.song_ids == [shared]


def test_name_lookup_is_probable_and_normalised(conn, cache_dir):
    _indexed(conn, cache_dir)
    [m] = artists_by_name(conn, "some band - Topic")
    assert m.artist_id == "UCartist0000000000000010" and m.basis == "artist_name"
    assert artists_by_name(conn, "Artist 3") and artists_by_name(conn, "artist3")
    assert artists_by_name(conn, "Nobody") == []


def test_match_artists_dedupes_and_orders_exact_first(conn, cache_dir):
    _indexed(conn, cache_dir)
    ms = match_artists(conn, channel_id="UCartist0000000000000010", names=["Some Band", "Artist 3"])
    assert [m.basis for m in ms] == ["artist_id", "artist_name"]
    assert [m.artist_id for m in ms] == ["UCartist0000000000000010", "UCartist0000000000000003"]


def test_report_includes_artist_section_and_headline(conn, cache_dir):
    _indexed(conn, cache_dir)
    ms = match_artists(conn, names=["Artist 3"])
    result = run_check(conn, youtube_ids=["zzzzzzzzzzz"])
    report = build_report(
        conn, result, platform="yt", subject_id="UCx", subject_title="Artist 3", artist_matches=ms
    )
    assert report["summary"]["artist_songs"] == 1 and report["summary"]["artist_matches"] == 1
    assert report["artists"][0]["songs"][0]["title"] == "Song 3"
    assert report["artists"][0]["songs"][0]["basis"] == "artist_name"
    assert "laion_disco_12m" in report["datasets"], "descriptor included for artist-only reports"
    assert headline(report) == "1 songs under your artist name appear in an AI training dataset"
    shared = next(iter_segments(FIXTURES / "eval_segments.csv"))[0]
    both = build_report(
        conn,
        run_check(conn, youtube_ids=[shared]),
        platform="yt",
        subject_id="UCx",
        subject_title="x",
        artist_matches=ms,
    )
    assert (
        headline(both)
        == "1 of your 1 videos appear in AI training datasets, plus 1 songs under your artist name"
    )


def test_cli_artist_option(tmp_path, cache_dir):
    db_path = tmp_path / "a.sqlite"
    conn = db.connect(db_path)
    db.init_schema(conn)
    _indexed(conn, cache_dir)
    conn.close()
    res = CliRunner().invoke(cli.app, ["check", "--artist", "Some Band", "--db", str(db_path)])
    assert res.exit_code == 0, res.output
    assert (
        "artist 'Some Band' (UCartist0000000000000010): 1 songs listed in LAION-DISCO-12M, matched by name (probable)"
        in res.output
    )
    res = CliRunner().invoke(
        cli.app, ["check", "--artist", "Some Band", "--db", str(db_path), "--json"]
    )
    assert '"basis": "artist_name"' in res.output
