"""Shared loader machinery: resumable downloads and batched, idempotent inserts."""

from __future__ import annotations

import hashlib
import sqlite3
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Protocol

import httpx

from .. import db
from ..models import Hit

BATCH_SIZE = 5000


class Loader(Protocol):
    """A dataset loader. `fetch` returns local manifest files; `iter_rows` yields hits per file."""

    name: str

    def fetch(self, cache_dir: Path) -> list[Path]: ...

    def iter_rows(self, path: Path, cache_dir: Path) -> Iterator[Hit]: ...


def download_to_cache(url: str, dest: Path, *, log: Callable[[str], None] | None = None) -> Path:
    """Stream `url` to `dest`. Skips if `dest` exists; writes to `dest.part` then renames."""
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    if log:
        log(f"downloading {url}")
    timeout = httpx.Timeout(60.0, read=300.0)
    with (
        httpx.Client(follow_redirects=True, timeout=timeout) as client,
        client.stream("GET", url) as resp,
    ):
        resp.raise_for_status()
        with part.open("wb") as f:
            for chunk in resp.iter_bytes(1 << 20):
                f.write(chunk)
    part.replace(dest)
    return dest


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_loader(
    loader: Loader,
    conn: sqlite3.Connection,
    cache_dir: Path,
    *,
    force: bool = False,
    limit: int | None = None,
    batch_size: int = BATCH_SIZE,
    log: Callable[[str], None] | None = None,
) -> dict[str, int]:
    """Fetch manifests and insert their rows. Idempotent and resumable.

    Returns {source_file: rows_inserted}. Files already marked done in load_state are skipped
    unless `force`. Inserts use INSERT OR IGNORE so a crash mid-file is safe to re-run.
    """
    log = log or (lambda msg: print(msg, file=sys.stderr))
    db.init_schema(conn)
    results: dict[str, int] = {}
    for path in loader.fetch(cache_dir):
        source = path.name
        if not force and db.load_state_done(conn, loader.name, source):
            log(f"{loader.name}: {source} already loaded, skipping (use --force to reload)")
            results[source] = 0
            continue
        db.mark_load_state(conn, loader.name, source, "loading")
        inserted = 0
        seen = 0
        batch: list[Hit] = []
        for hit in loader.iter_rows(path, cache_dir):
            batch.append(hit)
            seen += 1
            if len(batch) >= batch_size:
                inserted += db.insert_hits(conn, batch)
                conn.commit()
                batch.clear()
                log(f"{loader.name}: {source} {seen:,} rows read, {inserted:,} inserted")
            if limit is not None and seen >= limit:
                break
        if batch:
            inserted += db.insert_hits(conn, batch)
            conn.commit()
        status = "done" if limit is None else "partial"
        db.mark_load_state(conn, loader.name, source, status, rows=seen, sha256=sha256_file(path))
        log(f"{loader.name}: {source} finished, {seen:,} rows read, {inserted:,} inserted")
        results[source] = inserted
    return results
