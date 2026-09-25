"""AudioSet (Google, 2017): ~2M 10-second YouTube segments with ontology labels.

Manifests: three segment CSVs (eval, balanced_train, unbalanced_train), each with three
`#` comment lines then rows `YTID, start_seconds, end_seconds, positive_labels`. We keep only
segments carrying at least one label under the Music node (/m/04rlf) of the ontology.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterator
from pathlib import Path

from ..models import KEY_YOUTUBE_VIDEO, Hit
from .common import download_to_cache

BASE = "http://storage.googleapis.com/us_audioset/youtube_corpus/v1/csv/"
ONTOLOGY_URL = "https://raw.githubusercontent.com/audioset/ontology/master/ontology.json"
MUSIC_ROOT = "/m/04rlf"

SPLITS = {
    "eval_segments.csv": "eval",
    "balanced_train_segments.csv": "balanced_train",
    "unbalanced_train_segments.csv": "unbalanced_train",
}
SUPPORT_FILES = ("class_labels_indices.csv",)


def music_label_ids(ontology: list[dict]) -> frozenset[str]:
    """All ontology ids under the Music node, including Music itself (BFS over child_ids)."""
    children = {node["id"]: node.get("child_ids", []) for node in ontology}
    seen: set[str] = set()
    queue = [MUSIC_ROOT]
    while queue:
        node_id = queue.pop()
        if node_id in seen:
            continue
        seen.add(node_id)
        queue.extend(children.get(node_id, []))
    return frozenset(seen)


def read_label_names(path: Path) -> dict[str, str]:
    with path.open(newline="", encoding="utf-8") as f:
        return {row["mid"]: row["display_name"] for row in csv.DictReader(f)}


def iter_segments(path: Path) -> Iterator[tuple[str, float, float, list[str]]]:
    """Yield (ytid, start, end, [mids]) from an AudioSet segments CSV."""
    with path.open(newline="", encoding="utf-8") as f:
        lines = (line for line in f if line and not line.startswith("#"))
        for row in csv.reader(lines, skipinitialspace=True):
            if len(row) < 4:
                continue
            ytid, start, end, labels = row[0], row[1], row[2], row[3]
            mids = [m.strip() for m in labels.split(",") if m.strip()]
            yield ytid.strip(), float(start), float(end), mids


class AudioSetLoader:
    name = "audioset"

    def fetch(self, cache_dir: Path) -> list[Path]:
        d = cache_dir / self.name
        download_to_cache(ONTOLOGY_URL, d / "ontology.json")
        for name in SUPPORT_FILES:
            download_to_cache(BASE + name, d / name)
        return [download_to_cache(BASE + name, d / name) for name in SPLITS]

    def iter_rows(self, path: Path, cache_dir: Path) -> Iterator[Hit]:
        d = cache_dir / self.name
        split = SPLITS[path.name]
        music = music_label_ids(json.loads((d / "ontology.json").read_text(encoding="utf-8")))
        names = read_label_names(d / "class_labels_indices.csv")
        for ytid, start, end, mids in iter_segments(path):
            if not any(m in music for m in mids):
                continue
            yield Hit(
                dataset=self.name,
                dataset_row_id=f"{split}:{ytid}:{int(start)}:{int(end)}",
                key_type=KEY_YOUTUBE_VIDEO,
                key=ytid,
                start_s=start,
                end_s=end,
                extra={
                    "split": split,
                    "label_mids": mids,
                    "label_names": [names.get(m, m) for m in mids],
                },
            )
