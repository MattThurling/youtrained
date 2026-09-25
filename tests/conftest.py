"""Shared fixtures. No test touches the network: downloads are replaced by fixture copies."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from youtrained import config, db
from youtrained.loaders import audioset, laion_disco, musiccaps

FIXTURES = Path(__file__).parent / "fixtures"

# Map each URL the loaders would download to the fixture file that stands in for it.
FAKE_DOWNLOADS = {
    musiccaps.CSV_URL: FIXTURES / "musiccaps.csv",
    audioset.ONTOLOGY_URL: FIXTURES / "ontology.json",
    audioset.BASE + "class_labels_indices.csv": FIXTURES / "class_labels_indices.csv",
    audioset.BASE + "eval_segments.csv": FIXTURES / "eval_segments.csv",
    audioset.BASE + "balanced_train_segments.csv": FIXTURES / "balanced_train_segments.csv",
    audioset.BASE + "unbalanced_train_segments.csv": FIXTURES / "balanced_train_segments.csv",
}

DISCO_FILES = ["data/train-00000-of-00002.parquet", "data/train-00001-of-00002.parquet"]


def _disco_rows(shared_ytid: str) -> tuple[list[dict], list[dict]]:
    """Two small parquet files; one song id overlaps the AudioSet fixture, one is duplicated."""

    def row(i: int, **over) -> dict:
        base = {
            "song_id": f"disco{i:06d}"[:11].ljust(11, "x"),
            "title": f"Song {i}",
            "artist_names": [f"Artist {i}"],
            "artist_ids": [f"UCartist{i:016d}"[:24]],
            "album_name": f"Album {i}",
            "album_id": f"MPREb_{i}",
            "isExplicit": False,
            "views": f"{i}K plays",
            "duration": 180 + i,
        }
        return {**base, **over}

    file_a = [row(i) for i in range(10)]
    file_a.append(
        row(
            10,
            song_id=shared_ytid,
            title="Guitar Lesson",
            artist_names=["Some Band", "Guest"],
            artist_ids=["UCartist0000000000000010", "UCguest00000000000000000"],
        )
    )
    file_a.append(row(11, song_id="duplicate01", album_name=None))
    file_b = [row(i) for i in range(20, 27)]
    file_b.append(row(11, song_id="duplicate01"))  # same song in both files
    file_b.append(row(99, song_id=""))  # blank id is skipped
    return file_a, file_b


@pytest.fixture(scope="session")
def disco_fixture_dir(tmp_path_factory) -> Path:
    import pyarrow as pa
    import pyarrow.parquet as pq

    shared = next(audioset.iter_segments(FIXTURES / "eval_segments.csv"))[0]
    out = tmp_path_factory.mktemp("disco")
    for name, rows in zip(DISCO_FILES, _disco_rows(shared), strict=True):
        pq.write_table(pa.Table.from_pylist(rows), out / Path(name).name)
    return out


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = db.connect(":memory:")
    db.init_schema(c)
    return c


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    return tmp_path / "cache"


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch, disco_fixture_dir: Path) -> None:
    """Replace download_to_cache everywhere it was imported; refuse any unknown URL."""
    downloads = dict(FAKE_DOWNLOADS)
    for name in DISCO_FILES:
        downloads[laion_disco.REPO + name] = disco_fixture_dir / Path(name).name
    monkeypatch.setattr(laion_disco, "FILES", DISCO_FILES)

    def fake_download(url: str, dest: Path, *, log=None) -> Path:
        if url not in downloads:
            raise AssertionError(f"unexpected download in tests: {url}")
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(downloads[url], dest)
        return dest

    for module in (musiccaps, audioset, laion_disco):
        monkeypatch.setattr(module, "download_to_cache", fake_download)

    import httpx

    def boom(*args, **kwargs):
        raise AssertionError("network access attempted in tests")

    # Block the real transport only, so tests can still use httpx.MockTransport.
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", boom)


@pytest.fixture(autouse=True)
def no_real_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Never read the developer's .env or exported API keys inside tests."""
    monkeypatch.setenv("YOUTRAINED_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setattr(config, "_DOTENV_LOADED", False)
    for var in (
        "YOUTUBE_API_KEY",
        "SPOTIFY_CLIENT_ID",
        "SPOTIFY_CLIENT_SECRET",
        "GA_MEASUREMENT_ID",
    ):
        monkeypatch.delenv(var, raising=False)
