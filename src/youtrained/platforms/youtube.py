"""YouTube Data API v3 client: channel reference -> uploads playlist -> all video ids.

Quota cost per lookup: 1 unit for channels.list, 1 unit per 50 videos for
playlistItems.list, plus 100 units if a /c/<custom> URL has to fall back to search.list.
The default project quota is 10,000 units per day.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any

import httpx

from ..urls import ChannelRef
from .base import PlatformUnavailable, Video
from .cache import PlatformCache

API = "https://www.googleapis.com/youtube/v3"
PAGE_SIZE = 50
log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Channel:
    channel_id: str
    title: str
    uploads_playlist: str
    video_count: int | None = None


class YouTubeApiClient:
    def __init__(
        self,
        api_key: str,
        *,
        cache: PlatformCache | None = None,
        http: httpx.Client | None = None,
        max_videos: int = 5000,
    ) -> None:
        if not api_key:
            raise PlatformUnavailable("YOUTUBE_API_KEY is not set")
        self._key = api_key
        self._cache = cache
        self._http = http or httpx.Client(timeout=30.0)
        self._max_videos = max_videos

    # --- public API ----------------------------------------------------------

    def resolve_channel(self, ref: ChannelRef) -> Channel:
        params: dict[str, str] = {"part": "snippet,contentDetails,statistics"}
        if ref.kind == "id":
            params["id"] = ref.value
        elif ref.kind == "handle":
            params["forHandle"] = ref.value
        elif ref.kind == "user":
            params["forUsername"] = ref.value
        elif ref.kind == "custom":
            # Custom /c/ URLs have no direct lookup; most now double as handles.
            try:
                return self.resolve_channel(ChannelRef("handle", ref.value))
            except PlatformUnavailable:
                params["id"] = self._search_channel(ref.value)
        else:
            raise PlatformUnavailable(f"unknown channel reference kind {ref.kind!r}")
        data = self._get("channels", params)
        items = data.get("items") or []
        if not items:
            raise PlatformUnavailable(f"YouTube channel not found for {ref.kind}={ref.value}")
        item = items[0]
        stats = item.get("statistics") or {}
        return Channel(
            channel_id=item["id"],
            title=item["snippet"]["title"],
            uploads_playlist=item["contentDetails"]["relatedPlaylists"]["uploads"],
            video_count=int(stats["videoCount"]) if "videoCount" in stats else None,
        )

    def channel_videos(self, channel: ChannelRef) -> list[Video]:
        return self.channel_lookup(channel)[1]

    def channel_lookup(self, ref: ChannelRef) -> tuple[Channel, list[Video]]:
        """Resolve the channel and list every upload, using the 24h cache when present."""
        cache_key = f"youtube:{ref.kind}:{ref.value}"
        if self._cache is not None and (hit := self._cache.get(cache_key)) is not None:
            log.info("youtube cache hit for %s", cache_key)
            return Channel(**hit["channel"]), [Video(**v) for v in hit["videos"]]
        channel = self.resolve_channel(ref)
        videos = self._playlist_videos(channel.uploads_playlist)
        if self._cache is not None:
            self._cache.set(
                cache_key, {"channel": asdict(channel), "videos": [asdict(v) for v in videos]}
            )
        return channel, videos

    def videos_channels(self, video_ids: list[str]) -> dict[str, tuple[str, str, str]]:
        """Owning channel for up to 50 video ids in one call (1 quota unit).

        Returns {video_id: (channel_id, channel_title, video_title)}. Ids the API does not
        return are deleted or private videos; callers should record them as missing.
        """
        if len(video_ids) > PAGE_SIZE:
            raise ValueError(f"at most {PAGE_SIZE} ids per call")
        if not video_ids:
            return {}
        data = self._get(
            "videos", {"part": "snippet", "id": ",".join(video_ids), "maxResults": "50"}
        )
        out: dict[str, tuple[str, str, str]] = {}
        for item in data.get("items") or []:
            snip = item.get("snippet") or {}
            out[item["id"]] = (
                snip.get("channelId", ""),
                snip.get("channelTitle", ""),
                snip.get("title", ""),
            )
        return out

    # --- internals -----------------------------------------------------------

    def _playlist_videos(self, playlist_id: str) -> list[Video]:
        videos: list[Video] = []
        page_token: str | None = None
        while True:
            params = {
                "part": "snippet,contentDetails",
                "playlistId": playlist_id,
                "maxResults": str(PAGE_SIZE),
            }
            if page_token:
                params["pageToken"] = page_token
            data = self._get("playlistItems", params)
            for item in data.get("items") or []:
                vid = (item.get("contentDetails") or {}).get("videoId")
                if not vid:
                    continue
                videos.append(Video(video_id=vid, title=item.get("snippet", {}).get("title", "")))
            page_token = data.get("nextPageToken")
            if not page_token or len(videos) >= self._max_videos:
                break
        return videos

    def _search_channel(self, query: str) -> str:
        data = self._get(
            "search", {"part": "snippet", "type": "channel", "q": query, "maxResults": "1"}
        )
        items = data.get("items") or []
        if not items:
            raise PlatformUnavailable(f"no YouTube channel found matching {query!r}")
        return items[0]["snippet"]["channelId"]

    def _get(self, resource: str, params: dict[str, str]) -> dict[str, Any]:
        try:
            resp = self._http.get(f"{API}/{resource}", params={**params, "key": self._key})
        except httpx.HTTPError as exc:
            raise PlatformUnavailable(f"YouTube API request failed: {exc}") from exc
        if resp.status_code == 200:
            return resp.json()
        reason = _error_reason(resp)
        if resp.status_code == 403 and reason in {"quotaExceeded", "dailyLimitExceeded"}:
            raise PlatformUnavailable(
                "YouTube API daily quota exhausted for this key; it resets at midnight Pacific "
                "time. Try again later or use --youtube-ids directly."
            )
        if resp.status_code in {400, 403} and reason in {
            "keyInvalid",
            "forbidden",
            "accessNotConfigured",
        }:
            raise PlatformUnavailable(
                f"YouTube API rejected the key ({reason}); check YOUTUBE_API_KEY and that "
                "YouTube Data API v3 is enabled for the project."
            )
        if resp.status_code == 404:
            raise PlatformUnavailable("YouTube API: not found")
        raise PlatformUnavailable(
            f"YouTube API error {resp.status_code}: {reason or resp.text[:200]}"
        )


def _error_reason(resp: httpx.Response) -> str:
    try:
        errors = resp.json()["error"]["errors"]
        return errors[0].get("reason", "") if errors else ""
    except (ValueError, KeyError, IndexError, TypeError):
        return ""
