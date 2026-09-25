"""LAION-DISCO-12M (LAION, 2024): 12.3M YouTube Music song ids with title/artist metadata.

Manifest: five parquet files on Hugging Face (about 750 MB total) with columns
song_id, title, artist_names, artist_ids, album_name, album_id, isExplicit, views, duration.
song_id is the YouTube video id of the song, so it joins directly to our youtube_video key.
We stream each file in record batches so memory stays flat.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pyarrow.parquet as pq

from ..models import KEY_YOUTUBE_VIDEO, Hit
from .common import download_to_cache

REPO = "https://huggingface.co/datasets/laion/LAION-DISCO-12M/resolve/main/"
FILES = [f"data/train-0000{i}-of-00005.parquet" for i in range(5)]
COLUMNS = ["song_id", "title", "artist_names", "artist_ids", "album_name", "views", "duration"]
BATCH_ROWS = 20_000


class LaionDisco12MLoader:
    name = "laion_disco_12m"

    def fetch(self, cache_dir: Path) -> list[Path]:
        d = cache_dir / self.name
        return [download_to_cache(REPO + f, d / Path(f).name) for f in FILES]

    def iter_rows(self, path: Path, cache_dir: Path) -> Iterator[Hit]:
        pf = pq.ParquetFile(path)
        for batch in pf.iter_batches(batch_size=BATCH_ROWS, columns=COLUMNS):
            for row in batch.to_pylist():
                song_id = (row.get("song_id") or "").strip()
                if not song_id:
                    continue
                artists = [a for a in (row.get("artist_names") or []) if a]
                yield Hit(
                    dataset=self.name,
                    dataset_row_id=song_id,
                    key_type=KEY_YOUTUBE_VIDEO,
                    key=song_id,
                    artist=", ".join(artists) or None,
                    title=row.get("title") or None,
                    extra={
                        "artist_ids": [a for a in (row.get("artist_ids") or []) if a],
                        "album_name": row.get("album_name"),
                        "views": row.get("views"),
                        "duration_s": row.get("duration"),
                    },
                )
