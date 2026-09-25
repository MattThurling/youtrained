"""MusicCaps (Google, 2023): 5.5k 10-second YouTube clips with captions.

Manifest: a single CSV on Hugging Face with columns
ytid, start_s, end_s, audioset_positive_labels, aspect_list, caption, author_id,
is_balanced_subset, is_audioset_eval.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path

from ..models import KEY_YOUTUBE_VIDEO, Hit
from .common import download_to_cache

CSV_URL = "https://huggingface.co/datasets/google/MusicCaps/resolve/main/musiccaps-public.csv"


def _bool(value: str) -> bool:
    return value.strip().lower() in {"true", "1", "yes"}


class MusicCapsLoader:
    name = "musiccaps"

    def fetch(self, cache_dir: Path) -> list[Path]:
        return [download_to_cache(CSV_URL, cache_dir / self.name / "musiccaps-public.csv")]

    def iter_rows(self, path: Path, cache_dir: Path) -> Iterator[Hit]:
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                ytid = row["ytid"].strip()
                start = int(float(row["start_s"]))
                end = int(float(row["end_s"]))
                yield Hit(
                    dataset=self.name,
                    dataset_row_id=f"{ytid}:{start}:{end}",
                    key_type=KEY_YOUTUBE_VIDEO,
                    key=ytid,
                    start_s=float(start),
                    end_s=float(end),
                    extra={
                        "caption": row.get("caption", ""),
                        "aspect_list": row.get("aspect_list", ""),
                        "audioset_positive_labels": row.get("audioset_positive_labels", ""),
                        "author_id": row.get("author_id", ""),
                        "is_balanced_subset": _bool(row.get("is_balanced_subset", "")),
                        "is_audioset_eval": _bool(row.get("is_audioset_eval", "")),
                    },
                )
