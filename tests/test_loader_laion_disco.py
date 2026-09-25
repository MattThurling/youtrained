from conftest import FIXTURES

from youtrained import db
from youtrained.loaders import LOADERS, run_loader
from youtrained.loaders.audioset import iter_segments


def test_streams_both_files_and_dedupes(conn, cache_dir):
    results = run_loader(LOADERS["laion_disco_12m"], conn, cache_dir, log=lambda _: None)
    # file a: 12 rows; file b: 9 rows minus 1 blank id minus 1 duplicate already inserted
    assert results == {"train-00000-of-00002.parquet": 12, "train-00001-of-00002.parquet": 7}
    assert db.count_hits(conn, "laion_disco_12m") == 19
    assert db.load_state_done(conn, "laion_disco_12m", "train-00001-of-00002.parquet")


def test_row_shape(conn, cache_dir):
    run_loader(LOADERS["laion_disco_12m"], conn, cache_dir, log=lambda _: None)
    shared = next(iter_segments(FIXTURES / "eval_segments.csv"))[0]
    hits = [
        h for h in db.find_hits(conn, "youtube_video", [shared]) if h.dataset == "laion_disco_12m"
    ]
    assert len(hits) == 1
    h = hits[0]
    assert h.dataset_row_id == shared and h.key == shared
    assert h.artist == "Some Band, Guest" and h.title == "Guitar Lesson"
    assert h.start_s is None and h.end_s is None
    assert h.extra == {
        "artist_ids": ["UCartist0000000000000010", "UCguest00000000000000000"],
        "album_name": "Album 10",
        "views": "10K plays",
        "duration_s": 190,
    }
    (dup,) = db.find_hits(conn, "youtube_video", ["duplicate01"])
    assert dup.extra["album_name"] is None, "first occurrence wins"


def test_rerun_skips(conn, cache_dir):
    run_loader(LOADERS["laion_disco_12m"], conn, cache_dir, log=lambda _: None)
    logs: list[str] = []
    assert set(
        run_loader(LOADERS["laion_disco_12m"], conn, cache_dir, log=logs.append).values()
    ) == {0}
    assert sum("skipping" in m for m in logs) == 2
