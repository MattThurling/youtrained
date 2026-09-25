"""Protocols the real (milestone 3) and fake platform clients both satisfy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..urls import ArtistRef, ChannelRef


class PlatformUnavailable(RuntimeError):
    """Raised when a platform lookup cannot be performed (no client, quota hit, bad key)."""


@dataclass(frozen=True, slots=True)
class Track:
    track_id: str
    name: str
    isrc: str | None = None
    album: str | None = None


@dataclass(frozen=True, slots=True)
class Video:
    video_id: str
    title: str


class SpotifyClient(Protocol):
    def artist_tracks(self, artist: ArtistRef) -> list[Track]: ...


class YouTubeClient(Protocol):
    def channel_videos(self, channel: ChannelRef) -> list[Video]: ...
