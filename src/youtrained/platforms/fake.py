"""In-memory platform clients for tests and for milestone 1, where no API clients exist yet."""

from __future__ import annotations

from ..urls import ArtistRef, ChannelRef
from .base import PlatformUnavailable, Track, Video


class StaticSpotifyClient:
    """Returns a fixed track list per artist id. Unknown artists raise PlatformUnavailable."""

    def __init__(self, tracks: dict[str, list[Track]] | None = None) -> None:
        self._tracks = tracks or {}

    def artist_tracks(self, artist: ArtistRef) -> list[Track]:
        if artist.artist_id not in self._tracks:
            raise PlatformUnavailable(
                f"Spotify lookup for artist {artist.artist_id} is not available yet: "
                "the Spotify Web API client arrives in milestone 3. "
                "Use --spotify-ids to check track ids directly."
            )
        return list(self._tracks[artist.artist_id])


class StaticYouTubeClient:
    """Returns a fixed video list per channel reference. Unknown channels raise."""

    def __init__(self, videos: dict[ChannelRef, list[Video]] | None = None) -> None:
        self._videos = videos or {}

    def channel_videos(self, channel: ChannelRef) -> list[Video]:
        if channel not in self._videos:
            raise PlatformUnavailable(
                f"YouTube lookup for channel {channel.kind}={channel.value} needs an API key: "
                "set YOUTUBE_API_KEY in the environment or in .env (see .env.example), "
                "or use --youtube-ids to check video ids directly."
            )
        return list(self._videos[channel])
