import pytest

from youtrained.urls import (
    ArtistRef,
    ChannelRef,
    UnsupportedUrl,
    parse_spotify_artist_url,
    parse_youtube_channel_url,
)

SPOTIFY_ID = "4Z8W4fKeB5YxbusRsdQVPb"


@pytest.mark.parametrize(
    "url",
    [
        f"https://open.spotify.com/artist/{SPOTIFY_ID}",
        f"https://open.spotify.com/artist/{SPOTIFY_ID}?si=abc123",
        f"open.spotify.com/artist/{SPOTIFY_ID}/",
        f"https://open.spotify.com/intl-de/artist/{SPOTIFY_ID}",
        f"spotify:artist:{SPOTIFY_ID}",
        f"  {SPOTIFY_ID}  ",
    ],
)
def test_spotify_artist_variants(url):
    assert parse_spotify_artist_url(url) == ArtistRef(SPOTIFY_ID)


@pytest.mark.parametrize(
    "url",
    [
        f"https://open.spotify.com/track/{SPOTIFY_ID}",
        f"https://open.spotify.com/album/{SPOTIFY_ID}",
        "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M",
        "https://open.spotify.com/artist/short",
        "https://example.com/artist/" + SPOTIFY_ID,
        "https://www.youtube.com/@someone",
    ],
)
def test_spotify_rejects(url):
    with pytest.raises(UnsupportedUrl):
        parse_spotify_artist_url(url)


def test_spotify_track_message_is_helpful():
    with pytest.raises(UnsupportedUrl, match="track link"):
        parse_spotify_artist_url(f"https://open.spotify.com/track/{SPOTIFY_ID}")


CHANNEL_ID = "UC-lHJZR3Gqxm24_Vd_AJ5Yw"


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.youtube.com/@pewdiepie", ChannelRef("handle", "pewdiepie")),
        ("https://youtube.com/@pewdiepie/videos", ChannelRef("handle", "pewdiepie")),
        ("youtube.com/@Some.Artist_1", ChannelRef("handle", "Some.Artist_1")),
        ("@pewdiepie", ChannelRef("handle", "pewdiepie")),
        (f"https://www.youtube.com/channel/{CHANNEL_ID}", ChannelRef("id", CHANNEL_ID)),
        (f"https://m.youtube.com/channel/{CHANNEL_ID}?view=0", ChannelRef("id", CHANNEL_ID)),
        (CHANNEL_ID, ChannelRef("id", CHANNEL_ID)),
        ("https://www.youtube.com/c/SomeBand", ChannelRef("custom", "SomeBand")),
        ("https://www.youtube.com/user/someuser", ChannelRef("user", "someuser")),
        ("https://music.youtube.com/channel/" + CHANNEL_ID, ChannelRef("id", CHANNEL_ID)),
    ],
)
def test_youtube_channel_variants(url, expected):
    assert parse_youtube_channel_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.youtube.com/playlist?list=PL123",
        "https://www.youtube.com/shorts/abc",
        "https://www.youtube.com/channel/notachannel",
        "https://www.youtube.com/",
        "https://vimeo.com/@x",
        f"https://open.spotify.com/artist/{SPOTIFY_ID}",
    ],
)
def test_youtube_rejects(url):
    with pytest.raises(UnsupportedUrl):
        parse_youtube_channel_url(url)


def test_youtube_video_message_is_helpful():
    with pytest.raises(UnsupportedUrl, match="channel link instead"):
        parse_youtube_channel_url("https://youtu.be/dQw4w9WgXcQ")
