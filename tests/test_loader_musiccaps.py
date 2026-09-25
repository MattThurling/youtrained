import csv

from conftest import FIXTURES

from youtrained import db
from youtrained.loaders import LOADERS, run_loader


def test_loads_all_fixture_rows(conn, cache_dir):
    results = run_loader(LOADERS["musiccaps"], conn, cache_dir, log=lambda _: None)
    assert results == {"musiccaps-public.csv": 20}
    assert db.count_hits(conn, "musiccaps") == 20
    assert db.load_state_done(conn, "musiccaps", "musiccaps-public.csv")


def test_row_id_and_extra(conn, cache_dir):
    run_loader(LOADERS["musiccaps"], conn, cache_dir, log=lambda _: None)
    with (FIXTURES / "musiccaps.csv").open(newline="") as f:
        first = next(csv.DictReader(f))
    hits = db.find_hits(conn, "youtube_video", [first["ytid"]])
    assert len(hits) == 2, "fixture has two clips of the first ytid"
    ids = {h.dataset_row_id for h in hits}
    assert f"{first['ytid']}:30:40" in ids and f"{first['ytid']}:60:70" in ids
    h = next(h for h in hits if h.dataset_row_id.endswith(":60:70"))
    assert h.extra["caption"] == "Second clip of the first video."
    assert h.extra["is_balanced_subset"] is True and h.extra["is_audioset_eval"] is True
    assert h.start_s == 60.0 and h.end_s == 70.0


def test_rerun_is_noop_and_force_inserts_nothing_new(conn, cache_dir):
    run_loader(LOADERS["musiccaps"], conn, cache_dir, log=lambda _: None)
    assert run_loader(LOADERS["musiccaps"], conn, cache_dir, log=lambda _: None) == {
        "musiccaps-public.csv": 0
    }
    assert run_loader(LOADERS["musiccaps"], conn, cache_dir, force=True, log=lambda _: None) == {
        "musiccaps-public.csv": 0
    }
    assert db.count_hits(conn, "musiccaps") == 20


def test_limit_marks_partial(conn, cache_dir):
    run_loader(LOADERS["musiccaps"], conn, cache_dir, limit=5, log=lambda _: None)
    assert db.count_hits(conn, "musiccaps") == 5
    assert not db.load_state_done(conn, "musiccaps", "musiccaps-public.csv")
    run_loader(LOADERS["musiccaps"], conn, cache_dir, log=lambda _: None)
    assert db.count_hits(conn, "musiccaps") == 20
