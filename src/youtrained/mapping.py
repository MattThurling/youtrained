"""Map every dataset video id to its owning YouTube channel, in quota-bounded batches.

Once complete, "check my channel" is a local query: resolve the channel id (1 unit) and
select every mapped dataset video it owns, instead of walking the uploads playlist.
"""

from __future__ import annotations

import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass

from . import db
from .platforms.base import PlatformUnavailable
from .platforms.youtube import PAGE_SIZE, YouTubeApiClient


@dataclass(slots=True)
class MappingRun:
    calls: int = 0
    mapped: int = 0
    missing: int = 0
    stopped_reason: str = ""


def run_mapping(
    conn: sqlite3.Connection,
    client: YouTubeApiClient,
    *,
    budget: int,
    log: Callable[[str], None] | None = None,
) -> MappingRun:
    """Spend at most `budget` API calls (1 unit each) mapping queued ids. Resumable."""
    log = log or (lambda msg: print(msg, file=sys.stderr))
    db.init_schema(conn)
    if db.map_queue_size(conn) == 0:
        seeded = db.seed_map_queue(conn)
        log(f"queued {seeded:,} unmapped video ids")
    run = MappingRun()
    while run.calls < budget:
        batch = db.next_unmapped(conn, PAGE_SIZE)
        if not batch:
            run.stopped_reason = "queue empty"
            break
        try:
            found = client.videos_channels(batch)
        except PlatformUnavailable as exc:
            run.stopped_reason = str(exc)
            break
        run.calls += 1
        missing = [vid for vid in batch if vid not in found]
        db.record_video_channels(conn, found, missing)
        run.mapped += len(found)
        run.missing += len(missing)
        if run.calls % 20 == 0:
            log(
                f"{run.calls} calls: {run.mapped:,} mapped, {run.missing:,} missing, "
                f"{db.map_queue_size(conn):,} still queued"
            )
    if not run.stopped_reason:
        run.stopped_reason = "budget spent"
    stats = db.mapping_stats(conn)
    log(
        f"stopped ({run.stopped_reason}); totals: {stats['mapped']:,} mapped, "
        f"{stats['missing']:,} missing, {stats['queued']:,} queued"
    )
    return run
