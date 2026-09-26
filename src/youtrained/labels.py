"""AudioSet ontology labels and MusicCaps aspect tags as queryable tables.

The loaders keep each row's labels inside its `extra` JSON. This module normalises them:
`labels` (the full ontology, with hierarchy in `label_parents`), `video_labels`, and for
MusicCaps a flat `tags` / `video_tags` pair. Build once per load with `youtrained index-labels`
and `index-tags`; both are idempotent.
"""

from __future__ import annotations

import ast
import json
import re
import sqlite3
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import db

BATCH = 10_000
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    return _NON_ALNUM.sub("-", name.casefold()).strip("-") or "label"


def _unique_slugs(names: Sequence[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for name in names:
        base = slugify(name)
        n = seen.get(base, 0) + 1
        seen[base] = n
        out.append(base if n == 1 else f"{base}-{n}")
    return out


# --- building ---------------------------------------------------------------


def build_label_tables(
    conn: sqlite3.Connection,
    cache_dir: Path,
    *,
    ontology_path: Path | None = None,
    log: Callable[[str], None] | None = None,
) -> int:
    """(Re)create `labels` and `label_parents` from ontology.json. Returns the label count."""
    log = log or (lambda msg: print(msg, file=sys.stderr))
    db.init_schema(conn)
    path = ontology_path or cache_dir / "audioset" / "ontology.json"
    ontology = json.loads(Path(path).read_text(encoding="utf-8"))
    ids = {node["id"]: i + 1 for i, node in enumerate(ontology)}  # stable ids by file order
    parents: dict[str, list[str]] = {node["id"]: [] for node in ontology}
    for node in ontology:
        for child in node.get("child_ids", []):
            if child in parents:
                parents[child].append(node["id"])
    slugs = _unique_slugs([node["name"] for node in ontology])
    with conn:
        conn.execute("DELETE FROM label_parents")
        conn.execute("DELETE FROM labels")
        conn.executemany(
            "INSERT INTO labels(id, mid, name, slug, parent_id) VALUES (?,?,?,?,?)",
            [
                (
                    ids[n["id"]],
                    n["id"],
                    n["name"],
                    slug,
                    ids[parents[n["id"]][0]] if parents[n["id"]] else None,
                )
                for n, slug in zip(ontology, slugs, strict=True)
            ],
        )
        conn.executemany(
            "INSERT INTO label_parents(label_id, parent_id) VALUES (?,?)",
            [(ids[c], ids[p]) for c, ps in parents.items() for p in ps],
        )
    log(
        f"labels: {len(ontology)} ontology nodes, {sum(len(v) for v in parents.values())} parent links"
    )
    return len(ontology)


def build_video_labels(
    conn: sqlite3.Connection, *, log: Callable[[str], None] | None = None
) -> dict[str, int]:
    """Walk AudioSet hit rows and fill `video_labels`. Idempotent, committed every 10k rows."""
    log = log or (lambda msg: print(msg, file=sys.stderr))
    mid_to_id = {mid: lid for lid, mid in conn.execute("SELECT id, mid FROM labels")}
    if not mid_to_id:
        raise RuntimeError("labels table is empty; run build_label_tables first")
    cur = conn.execute("SELECT key, extra FROM hits NOT INDEXED WHERE dataset = 'audioset'")
    batch: list[tuple[str, int]] = []
    seen = inserted = unknown = 0
    unknown_mids: set[str] = set()
    for key, extra in cur:
        seen += 1
        for mid in json.loads(extra).get("label_mids") or []:
            lid = mid_to_id.get(mid)
            if lid is None:
                unknown += 1
                unknown_mids.add(mid)
                continue
            batch.append((key, lid))
        if len(batch) >= BATCH:
            inserted += _flush(conn, "video_labels", batch)
            log(f"video_labels: {seen:,} rows scanned, {inserted:,} pairs inserted")
    inserted += _flush(conn, "video_labels", batch)
    refresh_label_stats(conn)
    if unknown_mids:
        log(f"video_labels: {unknown:,} label refs to {len(unknown_mids)} MIDs not in the ontology")
    log(f"video_labels: done, {seen:,} rows scanned, {inserted:,} new pairs")
    return {"rows": seen, "inserted": inserted, "unknown": unknown}


def parse_aspect_list(raw: str | None) -> list[str]:
    """MusicCaps aspect_list is a Python-repr list string, e.g. "['low quality', 'sad']"."""
    if not raw:
        return []
    try:
        value = ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        return []
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(" ".join(item.casefold().split()))
    return list(dict.fromkeys(out))


def build_tag_tables(
    conn: sqlite3.Connection, *, log: Callable[[str], None] | None = None
) -> dict[str, int]:
    """Fill `tags` and `video_tags` from MusicCaps aspect lists. Idempotent, batched."""
    log = log or (lambda msg: print(msg, file=sys.stderr))
    db.init_schema(conn)
    tag_ids = {name: tid for tid, name in conn.execute("SELECT id, name FROM tags")}
    cur = conn.execute("SELECT key, extra FROM hits NOT INDEXED WHERE dataset = 'musiccaps'")
    batch: list[tuple[str, int]] = []
    seen = inserted = 0
    for key, extra in cur:
        seen += 1
        for name in parse_aspect_list(json.loads(extra).get("aspect_list")):
            tid = tag_ids.get(name)
            if tid is None:
                conn.execute(
                    "INSERT OR IGNORE INTO tags(name, slug) VALUES (?,?)", (name, slugify(name))
                )
                tid = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()[0]
                tag_ids[name] = tid
            batch.append((key, tid))
        if len(batch) >= BATCH:
            inserted += _flush(conn, "video_tags", batch)
            log(f"video_tags: {seen:,} rows scanned, {inserted:,} pairs inserted")
    inserted += _flush(conn, "video_tags", batch)
    conn.commit()
    log(f"video_tags: done, {seen:,} rows scanned, {len(tag_ids):,} tags, {inserted:,} new pairs")
    return {"rows": seen, "tags": len(tag_ids), "inserted": inserted}


def _flush(conn: sqlite3.Connection, table: str, batch: list[tuple[str, int]]) -> int:
    if not batch:
        return 0
    col = "label_id" if table == "video_labels" else "tag_id"
    before = conn.total_changes
    conn.executemany(f"INSERT OR IGNORE INTO {table}(video_id, {col}) VALUES (?,?)", batch)
    conn.commit()
    batch.clear()
    return conn.total_changes - before


# --- querying ---------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class Label:
    id: int
    mid: str
    name: str
    slug: str
    parent_id: int | None


def _row_to_label(row: tuple) -> Label:
    return Label(*row)


_LABEL_COLS = "id, mid, name, slug, parent_id"


def label_by_name(conn: sqlite3.Connection, name: str) -> Label | None:
    row = conn.execute(
        f"SELECT {_LABEL_COLS} FROM labels WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()
    return _row_to_label(row) if row else None


def label_by_slug(conn: sqlite3.Connection, slug: str) -> Label | None:
    row = conn.execute(f"SELECT {_LABEL_COLS} FROM labels WHERE slug = ?", (slug,)).fetchone()
    return _row_to_label(row) if row else None


def label_by_id(conn: sqlite3.Connection, label_id: int) -> Label | None:
    row = conn.execute(f"SELECT {_LABEL_COLS} FROM labels WHERE id = ?", (label_id,)).fetchone()
    return _row_to_label(row) if row else None


def _ids_for_names(conn: sqlite3.Connection, names: Sequence[str]) -> list[int]:
    ids = []
    for name in names:
        label = label_by_name(conn, name)
        if label is None:
            raise KeyError(name)
        ids.append(label.id)
    return ids


def videos_with_labels(conn: sqlite3.Connection, names: Sequence[str]) -> list[str]:
    """Videos carrying ALL of the named labels (exact labels, no subtree)."""
    ids = sorted(set(_ids_for_names(conn, names)))
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT video_id FROM video_labels WHERE label_id IN ({placeholders}) "
        "GROUP BY video_id HAVING COUNT(DISTINCT label_id) = ? ORDER BY video_id",
        [*ids, len(ids)],
    ).fetchall()
    return [r[0] for r in rows]


SUBTREE_CTE = """
WITH RECURSIVE subtree(id) AS (
  SELECT ?
  UNION
  SELECT lp.label_id FROM label_parents lp JOIN subtree s ON lp.parent_id = s.id
)
"""


def subtree_ids(conn: sqlite3.Connection, label_id: int) -> list[int]:
    return [r[0] for r in conn.execute(SUBTREE_CTE + "SELECT id FROM subtree", (label_id,))]


def videos_under(conn: sqlite3.Connection, name: str) -> list[str]:
    """Videos with any label in the named node's subtree (the node itself included)."""
    (label_id,) = _ids_for_names(conn, [name])
    rows = conn.execute(
        SUBTREE_CTE + "SELECT DISTINCT video_id FROM video_labels "
        "WHERE label_id IN (SELECT id FROM subtree) ORDER BY video_id",
        (label_id,),
    ).fetchall()
    return [r[0] for r in rows]


def label_video_count(conn: sqlite3.Connection, label_id: int, *, subtree: bool = False) -> int:
    if subtree:
        return conn.execute(
            SUBTREE_CTE + "SELECT COUNT(DISTINCT video_id) FROM video_labels "
            "WHERE label_id IN (SELECT id FROM subtree)",
            (label_id,),
        ).fetchone()[0]
    return conn.execute(
        "SELECT COUNT(*) FROM video_labels WHERE label_id = ?", (label_id,)
    ).fetchone()[0]


def label_videos_page(
    conn: sqlite3.Connection,
    label_id: int,
    *,
    page: int = 1,
    per_page: int = 50,
    subtree: bool = False,
) -> list[tuple[str, str | None, str | None, str | None]]:
    """(video_id, title, channel_id, channel_title) for one page, titles from the channel map."""
    offset = max(page - 1, 0) * per_page
    if subtree:
        sql = (
            SUBTREE_CTE + "SELECT DISTINCT vl.video_id FROM video_labels vl "
            "WHERE vl.label_id IN (SELECT id FROM subtree) ORDER BY vl.video_id LIMIT ? OFFSET ?"
        )
        params: tuple = (label_id, per_page, offset)
    else:
        sql = "SELECT video_id FROM video_labels WHERE label_id = ? ORDER BY video_id LIMIT ? OFFSET ?"
        params = (label_id, per_page, offset)
    ids = [r[0] for r in conn.execute(sql, params)]
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    meta = {
        r[0]: r[1:]
        for r in conn.execute(
            f"SELECT video_id, title, channel_id, channel_title FROM video_channels "
            f"WHERE video_id IN ({placeholders}) AND status = 'ok'",
            ids,
        )
    }
    return [(vid, *meta.get(vid, (None, None, None))) for vid in ids]


def label_children(conn: sqlite3.Connection, label_id: int) -> list[Label]:
    rows = conn.execute(
        f"SELECT {_LABEL_COLS} FROM labels WHERE id IN "
        "(SELECT label_id FROM label_parents WHERE parent_id = ?) ORDER BY name",
        (label_id,),
    ).fetchall()
    return [_row_to_label(r) for r in rows]


def label_parents(conn: sqlite3.Connection, label_id: int) -> list[Label]:
    rows = conn.execute(
        f"SELECT {_LABEL_COLS} FROM labels WHERE id IN "
        "(SELECT parent_id FROM label_parents WHERE label_id = ?) ORDER BY name",
        (label_id,),
    ).fetchall()
    return [_row_to_label(r) for r in rows]


def breadcrumb(conn: sqlite3.Connection, label: Label) -> list[Label]:
    """Root-first chain following `parent_id` (first parent), excluding the label itself."""
    chain: list[Label] = []
    seen = {label.id}
    cur = label
    while cur.parent_id is not None and cur.parent_id not in seen:
        parent = label_by_id(conn, cur.parent_id)
        if parent is None:
            break
        chain.append(parent)
        seen.add(parent.id)
        cur = parent
    return list(reversed(chain))


def refresh_label_stats(conn: sqlite3.Connection) -> dict[int, int]:
    """Recount videos per label into `label_stats` (one covering-index scan, done at index time)."""
    counts = dict(conn.execute("SELECT label_id, COUNT(*) FROM video_labels GROUP BY label_id"))
    with conn:
        conn.execute("DELETE FROM label_stats")
        conn.executemany(
            "INSERT INTO label_stats(label_id, direct_count) VALUES (?,?)", list(counts.items())
        )
    return counts


def direct_counts(conn: sqlite3.Connection) -> dict[int, int]:
    """{label_id: videos carrying it directly}, from `label_stats`; computed once if missing."""
    counts = dict(conn.execute("SELECT label_id, direct_count FROM label_stats"))
    if not counts and conn.execute("SELECT 1 FROM video_labels LIMIT 1").fetchone():
        counts = refresh_label_stats(conn)
    return counts


def label_slugs(conn: sqlite3.Connection) -> dict[str, str]:
    """{name: slug} for linking label names in reports."""
    return dict(conn.execute("SELECT name, slug FROM labels"))


def label_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        "labels": conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0],
        "video_labels": conn.execute("SELECT COUNT(*) FROM video_labels").fetchone()[0],
        "tags": conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0],
        "video_tags": conn.execute("SELECT COUNT(*) FROM video_tags").fetchone()[0],
    }


# --- tags -------------------------------------------------------------------


def tag_by_slug(conn: sqlite3.Connection, slug: str) -> tuple[int, str, str] | None:
    return conn.execute("SELECT id, name, slug FROM tags WHERE slug = ?", (slug,)).fetchone()


def videos_with_tags(conn: sqlite3.Connection, names: Sequence[str]) -> list[str]:
    """Videos carrying ALL the named MusicCaps aspects."""
    norm = sorted({" ".join(n.casefold().split()) for n in names})
    if not norm:
        return []
    ids = [
        r[0]
        for r in conn.execute(
            f"SELECT id FROM tags WHERE name IN ({','.join('?' * len(norm))})", norm
        )
    ]
    if len(ids) != len(norm):
        return []
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT video_id FROM video_tags WHERE tag_id IN ({placeholders}) "
        "GROUP BY video_id HAVING COUNT(DISTINCT tag_id) = ? ORDER BY video_id",
        [*ids, len(ids)],
    ).fetchall()
    return [r[0] for r in rows]
