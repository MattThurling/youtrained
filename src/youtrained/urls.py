"""Parse the artist/channel URLs musicians paste in."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse


class UnsupportedUrl(ValueError):
    """The URL is not a Spotify artist or YouTube channel URL we understand."""


@dataclass(frozen=True, slots=True)
class ArtistRef:
    """A Spotify artist, identified by its 22-character base-62 id."""

    artist_id: str


@dataclass(frozen=True, slots=True)
class ChannelRef:
    """A YouTube channel reference. kind is 'id', 'handle', 'custom' or 'user'."""

    kind: str
    value: str


_SPOTIFY_ID = re.compile(r"^[0-9A-Za-z]{22}$")
_YT_CHANNEL_ID = re.compile(r"^UC[0-9A-Za-z_-]{22}$")


def _path_parts(url: str) -> tuple[str, list[str]]:
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.").removeprefix("m.")
    parts = [p for p in parsed.path.split("/") if p]
    return host, parts


def parse_spotify_artist_url(url: str) -> ArtistRef:
    """Accepts open.spotify.com/artist/<id>, /intl-xx/artist/<id>, spotify:artist:<id>, bare id."""
    url = url.strip()
    if url.startswith("spotify:artist:"):
        candidate = url.removeprefix("spotify:artist:")
        if _SPOTIFY_ID.match(candidate):
            return ArtistRef(candidate)
        raise UnsupportedUrl(f"not a Spotify artist URI: {url}")
    if _SPOTIFY_ID.match(url):
        return ArtistRef(url)
    host, parts = _path_parts(url)
    if host not in {"open.spotify.com", "play.spotify.com", "spotify.com"}:
        raise UnsupportedUrl(f"not a Spotify URL: {url}")
    if parts and parts[0].startswith("intl-"):
        parts = parts[1:]
    if len(parts) >= 2 and parts[0] == "artist" and _SPOTIFY_ID.match(parts[1]):
        return ArtistRef(parts[1])
    if parts and parts[0] in {"track", "album", "playlist", "user", "show", "episode"}:
        raise UnsupportedUrl(
            f"this is a Spotify {parts[0]} link; paste your artist page link instead "
            "(open.spotify.com/artist/...)"
        )
    raise UnsupportedUrl(f"not a Spotify artist URL: {url}")


def parse_youtube_channel_url(url: str) -> ChannelRef:
    """Accepts youtube.com/@handle, /channel/UC..., /c/<name>, /user/<name>, bare @handle or UC id."""
    url = url.strip()
    if url.startswith("@") and len(url) > 1 and "/" not in url:
        return ChannelRef("handle", url[1:])
    if _YT_CHANNEL_ID.match(url):
        return ChannelRef("id", url)
    host, parts = _path_parts(url)
    if host not in {"youtube.com", "youtu.be", "music.youtube.com"}:
        raise UnsupportedUrl(f"not a YouTube URL: {url}")
    if not parts:
        raise UnsupportedUrl(f"not a YouTube channel URL: {url}")
    head = parts[0]
    if head.startswith("@") and len(head) > 1:
        return ChannelRef("handle", head[1:])
    if head == "channel" and len(parts) >= 2 and _YT_CHANNEL_ID.match(parts[1]):
        return ChannelRef("id", parts[1])
    if head == "c" and len(parts) >= 2:
        return ChannelRef("custom", parts[1])
    if head == "user" and len(parts) >= 2:
        return ChannelRef("user", parts[1])
    if head in {"watch", "playlist", "shorts", "embed"} or host == "youtu.be":
        raise UnsupportedUrl(
            f"this is a YouTube {head if host != 'youtu.be' else 'video'} link; paste your "
            "channel link instead (youtube.com/@yourhandle or youtube.com/channel/UC...)"
        )
    raise UnsupportedUrl(f"not a YouTube channel URL: {url}")
