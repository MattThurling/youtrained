"""Core dataclasses shared by loaders, lookup and the CLI."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

KEY_SPOTIFY_TRACK = "spotify_track"
KEY_YOUTUBE_VIDEO = "youtube_video"
KEY_ARTIST_TITLE = "artist_title"
KEY_TYPES = (KEY_SPOTIFY_TRACK, KEY_YOUTUBE_VIDEO, KEY_ARTIST_TITLE)


@dataclass(slots=True)
class Hit:
    """One row of the `hits` table."""

    dataset: str
    dataset_row_id: str
    key_type: str
    key: str
    artist: str | None = None
    title: str | None = None
    start_s: float | None = None
    end_s: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def as_row(self) -> tuple:
        return (
            self.dataset,
            self.dataset_row_id,
            self.key_type,
            self.key,
            self.artist,
            self.title,
            self.start_s,
            self.end_s,
            json.dumps(self.extra, ensure_ascii=False, sort_keys=True),
        )

    @classmethod
    def from_row(cls, row: tuple) -> Hit:
        dataset, row_id, key_type, key, artist, title, start_s, end_s, extra = row
        return cls(
            dataset=dataset,
            dataset_row_id=row_id,
            key_type=key_type,
            key=key,
            artist=artist,
            title=title,
            start_s=start_s,
            end_s=end_s,
            extra=json.loads(extra) if extra else {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "dataset_row_id": self.dataset_row_id,
            "key_type": self.key_type,
            "key": self.key,
            "artist": self.artist,
            "title": self.title,
            "start_s": self.start_s,
            "end_s": self.end_s,
            "extra": self.extra,
        }


@dataclass(slots=True)
class CheckResult:
    """Outcome of looking up a set of platform IDs."""

    spotify_ids: list[str]
    youtube_ids: list[str]
    hits: list[Hit]

    @property
    def checked(self) -> int:
        return len(self.spotify_ids) + len(self.youtube_ids)

    @property
    def matched_keys(self) -> set[tuple[str, str]]:
        return {(h.key_type, h.key) for h in self.hits}

    @property
    def datasets(self) -> list[str]:
        seen: dict[str, None] = {}
        for h in self.hits:
            seen.setdefault(h.dataset, None)
        return list(seen)

    def to_dict(self) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "spotify_ids": self.spotify_ids,
            "youtube_ids": self.youtube_ids,
            "hit_count": len(self.hits),
            "matched_key_count": len(self.matched_keys),
            "hits": [h.to_dict() for h in self.hits],
        }
