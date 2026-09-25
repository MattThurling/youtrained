"""Platform clients (Spotify, YouTube)."""

from .base import PlatformUnavailable, SpotifyClient, Track, Video, YouTubeClient
from .cache import PlatformCache
from .fake import StaticSpotifyClient, StaticYouTubeClient
from .youtube import Channel, YouTubeApiClient

__all__ = [
    "Channel",
    "PlatformCache",
    "PlatformUnavailable",
    "SpotifyClient",
    "StaticSpotifyClient",
    "StaticYouTubeClient",
    "Track",
    "Video",
    "YouTubeApiClient",
    "YouTubeClient",
]
