from youtrained import db
from youtrained.models import KEY_YOUTUBE_VIDEO, Hit


def _hit(i: int, dataset: str = "ds") -> Hit:
    return Hit(dataset, f"row{i}", KEY_YOUTUBE_VIDEO, f"key{i}", extra={"i": i})


def test_schema_is_idempotent(conn):
    db.init_schema(conn)
    db.init_schema(conn)
    assert db.count_hits(conn) == 0


def test_insert_ignores_duplicates(conn):
    assert db.insert_hits(conn, [_hit(1), _hit(2)]) == 2
    assert db.insert_hits(conn, [_hit(1), _hit(3)]) == 1
    assert db.count_hits(conn) == 3


def test_same_row_id_different_datasets_are_distinct(conn):
    assert db.insert_hits(conn, [_hit(1, "a"), _hit(1, "b")]) == 2


def test_find_hits_chunks_over_sqlite_variable_limit(conn):
    n = db.SQLITE_MAX_VARS * 2 + 7
    db.insert_hits(conn, [_hit(i) for i in range(n)])
    conn.commit()
    keys = [f"key{i}" for i in range(n)] + ["missing", "key0"]  # duplicate + missing
    found = db.find_hits(conn, KEY_YOUTUBE_VIDEO, keys)
    assert len(found) == n
    assert found[0].extra == {"i": 0}
    assert db.find_hits(conn, "spotify_track", keys) == []


def test_load_state_roundtrip(conn):
    assert not db.load_state_done(conn, "ds", "f.csv")
    db.mark_load_state(conn, "ds", "f.csv", "loading")
    assert not db.load_state_done(conn, "ds", "f.csv")
    db.mark_load_state(conn, "ds", "f.csv", "done", rows=5, sha256="abc")
    assert db.load_state_done(conn, "ds", "f.csv")
    (row,) = db.load_states(conn)
    assert row[:4] == ("ds", "f.csv", "done", 5) and row[4]
