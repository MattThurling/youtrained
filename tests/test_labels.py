"""AudioSet label tables, MusicCaps tags, and the intersection / subtree queries."""

import json

import pytest
from conftest import FIXTURES

from youtrained import db
from youtrained.labels import (
    breadcrumb,
    build_label_tables,
    build_tag_tables,
    build_video_labels,
    label_by_name,
    label_by_slug,
    label_children,
    label_counts,
    label_parents,
    label_video_count,
    label_videos_page,
    parse_aspect_list,
    slugify,
    videos_under,
    videos_with_labels,
    videos_with_tags,
)
from youtrained.models import Hit

A, B, C = "vidAAAAAAAA", "vidBBBBBBBB", "vidCCCCCCCC"
JAZZ, GUITAR, SPEECH, POP = "/m/03_d0", "/m/0342h", "/m/09x0r", "/m/064t9"


def _audioset_row(vid: str, mids: list[str], split: str = "eval") -> Hit:
    return Hit(
        "audioset",
        f"{split}:{vid}:0:10",
        "youtube_video",
        vid,
        start_s=0,
        end_s=10,
        extra={"split": split, "label_mids": mids, "label_names": mids},
    )


@pytest.fixture
def labelled(conn, cache_dir):
    db.insert_hits(
        conn,
        [
            _audioset_row(A, [JAZZ, GUITAR]),
            _audioset_row(A, [JAZZ], split="balanced_train"),  # same video, second split
            _audioset_row(B, [JAZZ]),
            _audioset_row(C, [GUITAR, SPEECH]),
            Hit(
                "musiccaps",
                f"{A}:0:10",
                "youtube_video",
                A,
                extra={"aspect_list": "['Low quality', 'sad', 'jazz', 'low quality']"},
            ),
            Hit("musiccaps", f"{B}:0:10", "youtube_video", B, extra={"aspect_list": "['sad']"}),
            Hit("musiccaps", f"{C}:0:10", "youtube_video", C, extra={"aspect_list": "not a list"}),
        ],
    )
    conn.commit()
    build_label_tables(
        conn, cache_dir, ontology_path=FIXTURES / "ontology.json", log=lambda _: None
    )
    build_video_labels(conn, log=lambda _: None)
    build_tag_tables(conn, log=lambda _: None)
    return conn


def test_slugify():
    assert slugify("Acoustic guitar") == "acoustic-guitar"
    assert slugify("Hi-hat") == "hi-hat"
    assert slugify("Music genre") == "music-genre"
    assert slugify("  ") == "label"


def test_labels_and_parents_from_ontology(labelled):
    c = label_counts(labelled)
    assert c["labels"] == 10
    jazz = label_by_name(labelled, "jazz")  # case-insensitive
    assert jazz and jazz.mid == JAZZ and jazz.slug == "jazz"
    genre = label_by_name(labelled, "Music genre")
    vocal = label_by_name(labelled, "Vocal music")
    assert jazz.parent_id == genre.id, "first parent by file order"
    assert {p.id for p in label_parents(labelled, jazz.id)} == {genre.id, vocal.id}
    assert [c.name for c in label_children(labelled, genre.id)] == ["Jazz", "Pop music"]
    assert [b.name for b in breadcrumb(labelled, jazz)] == ["Music", "Music genre"]
    assert label_by_name(labelled, "Music").parent_id is None
    assert label_by_slug(labelled, "guitar").mid == GUITAR
    assert label_by_slug(labelled, "nope") is None


def test_video_labels_dedupe_across_splits(labelled):
    rows = labelled.execute("SELECT video_id, label_id FROM video_labels").fetchall()
    assert len(rows) == 5  # A: jazz+guitar, B: jazz, C: guitar+speech
    assert label_video_count(labelled, label_by_name(labelled, "Jazz").id) == 2


def test_intersection_and_subtree(labelled):
    assert videos_with_labels(labelled, ["Jazz", "Guitar"]) == [A]
    assert videos_with_labels(labelled, ["Jazz"]) == [A, B]
    assert videos_with_labels(labelled, ["Guitar", "Speech"]) == [C]
    assert videos_with_labels(labelled, ["Pop music"]) == []
    assert videos_under(labelled, "Music genre") == [A, B]
    assert videos_under(labelled, "Musical instrument") == [A, C]
    assert videos_under(labelled, "Music") == [A, B, C]
    assert videos_under(labelled, "Vocal music") == [A, B], "reachable via the second parent"
    assert videos_under(labelled, "Speech") == [C]
    with pytest.raises(KeyError):
        videos_with_labels(labelled, ["Jazz", "Kazoo"])


def test_subtree_counts_and_paging(labelled):
    music = label_by_name(labelled, "Music")
    assert label_video_count(labelled, music.id) == 0
    assert label_video_count(labelled, music.id, subtree=True) == 3
    page1 = label_videos_page(labelled, music.id, page=1, per_page=2, subtree=True)
    page2 = label_videos_page(labelled, music.id, page=2, per_page=2, subtree=True)
    assert [r[0] for r in page1] == [A, B] and [r[0] for r in page2] == [C]
    assert page1[0][1:] == (None, None, None), "no channel mapping in this fixture"
    db.record_video_channels(labelled, {A: ("UCx", "Chan X", "Title A")}, [])
    assert label_videos_page(labelled, music.id, subtree=True)[0] == (A, "Title A", "UCx", "Chan X")


def test_rebuild_is_idempotent(labelled, cache_dir):
    before = label_counts(labelled)
    build_label_tables(
        labelled, cache_dir, ontology_path=FIXTURES / "ontology.json", log=lambda _: None
    )
    assert build_video_labels(labelled, log=lambda _: None)["inserted"] == 0
    assert build_tag_tables(labelled, log=lambda _: None)["inserted"] == 0
    assert label_counts(labelled) == before
    assert videos_with_labels(labelled, ["Jazz", "Guitar"]) == [A]


def test_unknown_mids_are_skipped(conn, cache_dir):
    db.insert_hits(conn, [_audioset_row(A, [JAZZ, "/m/unknown"])])
    conn.commit()
    build_label_tables(
        conn, cache_dir, ontology_path=FIXTURES / "ontology.json", log=lambda _: None
    )
    stats = build_video_labels(conn, log=lambda _: None)
    assert stats == {"rows": 1, "inserted": 1, "unknown": 1}


def test_tags(labelled):
    assert parse_aspect_list("['Low quality', 'sad', 'jazz', 'low quality']") == [
        "low quality",
        "sad",
        "jazz",
    ]
    assert parse_aspect_list("not a list") == [] and parse_aspect_list(None) == []
    assert label_counts(labelled)["tags"] == 3 and label_counts(labelled)["video_tags"] == 4
    assert videos_with_tags(labelled, ["sad"]) == [A, B]
    assert videos_with_tags(labelled, ["Sad", "low quality"]) == [A]
    assert videos_with_tags(labelled, ["nope"]) == []
    slugs = dict(labelled.execute("SELECT name, slug FROM tags"))
    assert slugs["low quality"] == "low-quality"


def test_real_ontology_shape_roundtrip(tmp_path, conn, cache_dir):
    """Duplicate names get distinct slugs."""
    ont = [
        {"id": "/a", "name": "Root", "child_ids": ["/b", "/c"]},
        {"id": "/b", "name": "Same name", "child_ids": []},
        {"id": "/c", "name": "Same Name", "child_ids": []},
    ]
    path = tmp_path / "ont.json"
    path.write_text(json.dumps(ont))
    build_label_tables(conn, cache_dir, ontology_path=path, log=lambda _: None)
    assert sorted(dict(conn.execute("SELECT name, slug FROM labels")).values()) == [
        "root",
        "same-name",
        "same-name-2",
    ]
