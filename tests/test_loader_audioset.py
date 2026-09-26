import json

import pytest
from conftest import FIXTURES

from youtrained import db
from youtrained.loaders import LOADERS, run_loader
from youtrained.loaders.audioset import AudioSetLoader, iter_segments, music_label_ids


def test_music_label_ids_is_music_subtree():
    ontology = json.loads((FIXTURES / "ontology.json").read_text())
    ids = music_label_ids(ontology)
    assert ids == {
        "/m/04rlf",
        "/m/04szw",
        "/m/0342h",
        "/m/0kpv1t",
        "/m/064t9",
        "/m/03_d0",
        "/t/test01",
    }


def test_iter_segments_skips_comments_and_parses_quoted_labels():
    rows = list(iter_segments(FIXTURES / "eval_segments.csv"))
    assert len(rows) == 16
    ytid, start, end, mids = rows[0]
    assert len(ytid) == 11 and (start, end) == (10.0, 20.0)
    assert mids == ["/m/0342h", "/m/09x0r"]


def test_only_music_rows_are_loaded_with_split_prefix(conn, cache_dir):
    results = run_loader(LOADERS["audioset"], conn, cache_dir, log=lambda _: None)
    # eval: 1 shared + 9 pop + 1 musiccaps overlap = 11 music, 5 speech dropped
    # balanced: 1 shared + 9 music = 10, 5 vehicle dropped
    # unbalanced fixture is a copy of balanced: distinct split prefix, so 10 more
    assert results == {
        "eval_segments.csv": 11,
        "balanced_train_segments.csv": 10,
        "unbalanced_train_segments.csv": 10,
    }
    rows = list(iter_segments(FIXTURES / "eval_segments.csv"))
    shared = rows[0][0]
    hits = db.find_hits(conn, "youtube_video", [shared])
    assert {h.dataset_row_id for h in hits} == {
        f"eval:{shared}:10:20",
        f"balanced_train:{shared}:10:20",
        f"unbalanced_train:{shared}:10:20",
    }
    h = hits[0]
    assert h.extra["label_names"] == ["Guitar", "Speech"]
    assert h.extra["split"] in {"eval", "balanced_train", "unbalanced_train"}
    speech_only = rows[10][0]
    assert db.find_hits(conn, "youtube_video", [speech_only]) == []


def test_skip_done_files_unless_force(conn, cache_dir):
    run_loader(LOADERS["audioset"], conn, cache_dir, log=lambda _: None)
    logs: list[str] = []
    again = run_loader(LOADERS["audioset"], conn, cache_dir, log=logs.append)
    assert set(again.values()) == {0}
    assert sum("skipping" in m for m in logs) == 3
    logs.clear()
    forced = run_loader(LOADERS["audioset"], conn, cache_dir, force=True, log=logs.append)
    assert set(forced.values()) == {0}
    assert not any("skipping" in m for m in logs)
    assert db.count_hits(conn, "audioset") == 31


def test_crash_mid_file_then_resume(conn, cache_dir):
    """Simulate a crash after the first batch of eval; rerun must finish with the right count."""
    loader = AudioSetLoader()
    real_iter = loader.iter_rows

    def crashing_iter(path, cache):
        for i, hit in enumerate(real_iter(path, cache)):
            if path.name == "eval_segments.csv" and i == 6:
                raise RuntimeError("simulated crash")
            yield hit

    loader.iter_rows = crashing_iter  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        run_loader(loader, conn, cache_dir, batch_size=3, log=lambda _: None)
    assert 0 < db.count_hits(conn, "audioset") < 11
    assert not db.load_state_done(conn, "audioset", "eval_segments.csv")

    fresh = AudioSetLoader()
    results = run_loader(fresh, conn, cache_dir, batch_size=3, log=lambda _: None)
    assert results["eval_segments.csv"] == 11 - 6  # committed batches survived
    assert db.count_hits(conn, "audioset") == 31
    assert db.load_state_done(conn, "audioset", "eval_segments.csv")
