"""YouTube Data API client against a mocked transport. No network."""

import json
from urllib.parse import parse_qs

import httpx
import pytest

from youtrained import db
from youtrained.platforms import PlatformCache, PlatformUnavailable, YouTubeApiClient
from youtrained.urls import ChannelRef

CHANNEL = {
    "id": "UCxxxxxxxxxxxxxxxxxxxxxx",
    "snippet": {"title": "Some Band"},
    "contentDetails": {"relatedPlaylists": {"uploads": "UUxxxxxxxxxxxxxxxxxxxxxx"}},
    "statistics": {"videoCount": "3"},
}


def _item(vid: str, title: str) -> dict:
    return {"snippet": {"title": title}, "contentDetails": {"videoId": vid}}


class FakeYouTube:
    """Records requests and serves canned responses for channels, playlistItems, search."""

    def __init__(self, channel_query: dict[str, str] | None = None, quota: bool = False):
        self.requests: list[tuple[str, dict[str, list[str]]]] = []
        self.channel_query = channel_query or {"forHandle": "someband"}
        self.quota = quota

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        resource = request.url.path.rsplit("/", 1)[-1]
        q = parse_qs(request.url.query.decode())
        self.requests.append((resource, q))
        assert q["key"] == ["k"]
        if self.quota:
            return httpx.Response(403, json={"error": {"errors": [{"reason": "quotaExceeded"}]}})
        if resource == "channels":
            matches = all(q.get(k) == [v] for k, v in self.channel_query.items())
            return httpx.Response(200, json={"items": [CHANNEL] if matches else []})
        if resource == "playlistItems":
            assert q["playlistId"] == ["UUxxxxxxxxxxxxxxxxxxxxxx"]
            if "pageToken" not in q:
                return httpx.Response(
                    200,
                    json={
                        "items": [_item("aaaaaaaaaaa", "A"), _item("bbbbbbbbbbb", "B")],
                        "nextPageToken": "p2",
                    },
                )
            assert q["pageToken"] == ["p2"]
            return httpx.Response(200, json={"items": [_item("ccccccccccc", "C")]})
        if resource == "search":
            return httpx.Response(200, json={"items": [{"snippet": {"channelId": CHANNEL["id"]}}]})
        return httpx.Response(404, json={"error": {"errors": [{"reason": "notFound"}]}})


def _client(fake: FakeYouTube, cache: PlatformCache | None = None) -> YouTubeApiClient:
    return YouTubeApiClient("k", cache=cache, http=httpx.Client(transport=fake.transport()))


def test_handle_lookup_paginates_all_uploads():
    fake = FakeYouTube()
    channel, videos = _client(fake).channel_lookup(ChannelRef("handle", "someband"))
    assert channel.title == "Some Band" and channel.video_count == 3
    assert [v.video_id for v in videos] == ["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"]
    assert videos[0].title == "A"
    assert [r for r, _ in fake.requests] == ["channels", "playlistItems", "playlistItems"]


@pytest.mark.parametrize(
    "ref,query",
    [
        (ChannelRef("id", CHANNEL["id"]), {"id": CHANNEL["id"]}),
        (ChannelRef("user", "olduser"), {"forUsername": "olduser"}),
        (ChannelRef("custom", "SomeBand"), {"forHandle": "SomeBand"}),
    ],
)
def test_channel_reference_kinds_map_to_api_params(ref, query):
    fake = FakeYouTube(channel_query=query)
    assert len(_client(fake).channel_videos(ref)) == 3


def test_custom_url_falls_back_to_search():
    fake = FakeYouTube(channel_query={"id": CHANNEL["id"]})  # forHandle miss, id hit
    videos = _client(fake).channel_videos(ChannelRef("custom", "SomeBand"))
    assert len(videos) == 3
    assert [r for r, _ in fake.requests][:3] == ["channels", "search", "channels"]


def test_unknown_channel_raises():
    fake = FakeYouTube(channel_query={"forHandle": "other"})
    with pytest.raises(PlatformUnavailable, match="not found"):
        _client(fake).channel_videos(ChannelRef("handle", "someband"))


def test_quota_error_is_explained():
    with pytest.raises(PlatformUnavailable, match="quota"):
        _client(FakeYouTube(quota=True)).channel_videos(ChannelRef("handle", "someband"))


def test_missing_key_rejected():
    with pytest.raises(PlatformUnavailable, match="YOUTUBE_API_KEY"):
        YouTubeApiClient("")


def test_cache_serves_second_lookup_and_expires():
    conn = db.connect(":memory:")
    now = [1_000_000.0]
    cache = PlatformCache(conn, ttl_s=24 * 3600, clock=lambda: now[0])
    fake = FakeYouTube()
    client = _client(fake, cache)
    ref = ChannelRef("handle", "someband")
    first = client.channel_lookup(ref)
    n = len(fake.requests)
    second = client.channel_lookup(ref)
    assert second == first and len(fake.requests) == n, "cache hit made no requests"
    now[0] += 24 * 3600 + 1
    client.channel_lookup(ref)
    assert len(fake.requests) == 2 * n, "expired entry refetched"
    row = conn.execute("SELECT payload FROM platform_cache").fetchone()
    assert json.loads(row[0])["channel"]["title"] == "Some Band"
