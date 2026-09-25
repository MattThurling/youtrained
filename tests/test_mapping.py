"""Channel mapper against a mocked videos.list. No network."""

from urllib.parse import parse_qs

import httpx
from conftest import FIXTURES

from youtrained import db
from youtrained.loaders import LOADERS, run_loader
from youtrained.loaders.audioset import iter_segments
from youtrained.mapping import run_mapping
from youtrained.platforms import YouTubeApiClient


class FakeVideos:
    """Every id maps to channel 'UC' + first 3 chars; ids in `deleted` are not returned."""

    def __init__(self, quota_after: int | None = None, deleted: set[str] | None = None):
        self.calls = 0
        self.quota_after = quota_after
        self.deleted = deleted or set()

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/videos")
        q = parse_qs(request.url.query.decode())
        self.calls += 1
        if self.quota_after is not None and self.calls > self.quota_after:
            return httpx.Response(403, json={"error": {"errors": [{"reason": "quotaExceeded"}]}})
        ids = q["id"][0].split(",")
        assert len(ids) <= 50
        items = [
            {
                "id": v,
                "snippet": {
                    "channelId": "UC" + v[:3],
                    "channelTitle": "Chan " + v[:3],
                    "title": "Video " + v,
                },
            }
            for v in ids
            if v not in self.deleted
        ]
        return httpx.Response(200, json={"items": items})


def _client(fake: FakeVideos) -> YouTubeApiClient:
    return YouTubeApiClient("k", http=httpx.Client(transport=httpx.MockTransport(fake.handle)))


def _loaded(conn, cache_dir):
    for name in LOADERS:
        run_loader(LOADERS[name], conn, cache_dir, log=lambda _: None)


def test_seed_queue_covers_audioset_and_musiccaps_only(conn, cache_dir):
    _loaded(conn, cache_dir)
    n = db.seed_map_queue(conn)
    ids = {
        r[0] for r in conn.execute("SELECT key FROM hits WHERE dataset IN ('audioset','musiccaps')")
    }
    assert n == len(ids) == db.map_queue_size(conn)
    assert "duplicate01" not in {r[0] for r in conn.execute("SELECT video_id FROM map_queue")}


def test_mapping_runs_to_completion_and_records_missing(conn, cache_dir):
    _loaded(conn, cache_dir)
    db.seed_map_queue(conn)
    gone = db.next_unmapped(conn, 1)[0]
    fake = FakeVideos(deleted={gone})
    run = run_mapping(conn, _client(fake), budget=100, log=lambda _: None)
    total = db.map_queue_size(conn)
    assert total == 0 and run.stopped_reason == "queue empty"
    stats = db.mapping_stats(conn)
    assert stats["queued"] == 0 and stats["mapped"] + stats["missing"] == run.mapped + run.missing
    assert stats["missing"] == 1
    assert (
        conn.execute("SELECT status FROM video_channels WHERE video_id = ?", (gone,)).fetchone()[0]
        == "missing"
    )
    assert db.mapping_complete(conn)
    assert run.calls == fake.calls
    assert not db.seed_map_queue(conn), "nothing left to seed"


def test_budget_and_quota_stop_cleanly_then_resume(conn, cache_dir, monkeypatch):
    from youtrained import mapping

    monkeypatch.setattr(mapping, "PAGE_SIZE", 10)  # fixtures have ~38 ids; force several calls
    _loaded(conn, cache_dir)
    db.seed_map_queue(conn)
    before = db.map_queue_size(conn)
    run = run_mapping(conn, _client(FakeVideos()), budget=1, log=lambda _: None)
    assert run.calls == 1 and run.stopped_reason == "budget spent"
    assert db.map_queue_size(conn) == before - 10
    assert not db.mapping_complete(conn)
    run = run_mapping(conn, _client(FakeVideos(quota_after=0)), budget=10, log=lambda _: None)
    assert run.calls == 0 and "quota" in run.stopped_reason
    run = run_mapping(conn, _client(FakeVideos()), budget=100, log=lambda _: None)
    assert run.stopped_reason == "queue empty" and db.mapping_complete(conn)


def test_videos_for_channel_and_top_channels(conn, cache_dir):
    _loaded(conn, cache_dir)
    run_mapping(conn, _client(FakeVideos()), budget=100, log=lambda _: None)
    shared = next(iter_segments(FIXTURES / "eval_segments.csv"))[0]
    owned = db.videos_for_channel(conn, "UC" + shared[:3])
    assert (shared, "Video " + shared) in owned
    top = db.top_channels(conn, 3)
    assert top and top[0][2] >= top[-1][2]
