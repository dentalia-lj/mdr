"""Link a manufacturer's items from the index it ships, once.

Komet publishes an article -> document index (a spreadsheet and a Word table).
This reads the ARCHIVED copy of both, resolves each "Where to find DoC" key to
the document filed for that family, and appends that document's REF list as a
new extraction revision whose ref_list carries the INDEX as its archive_url.
`validate.doc` is then re-emitted at the new rev, so the existing C1 gate forms
the links and GATE writes them (invariant 1). No new job type (invariant 7).

One-shot by design: `backfill.scan` is not scheduled, and steady-state refresh
is `doc_sources[kind:"direct"]` -> `fetch.url`. The index's content_hash is
reported so a re-run against a changed index is a human decision.

Same shape as `repair_ref_list`: plan / render / apply, dry run by default --
because it is the same operation underneath (append an extraction_attempt at a
new rev and re-point the pending validate.doc at it), so the existing gate
forms the links and GATE remains the only writer.
"""

from __future__ import annotations

import collections
import logging
import pathlib

from app import coverage_map, playbooks, queue
from app.config import load_config
from app.extract import tiers
from app.handlers import archiving
from app.handlers.backfill import _INDEX_CONTENT_TYPE
from app.handlers.validate import _fold_bur_code

log = logging.getLogger("dentalia.komet_coverage")

MARKER = "coverage-map"

#: `coverage_map.sources` entries carrying a higher `phase` are read by
#: BACKFILL (the index is evidence and is archived like any other source) but
#: contribute no LINKS here. `SPECIFIKACIJA.docx` is phase 2: its rows are the
#: articles whose original manufacturer is NOT Komet -- Shofu, MetaBiomed,
#: Becht, Stoddard, TUV SUD -- while the items sit in groups whose
#: `canonical_manufacturer` is KOMET. Linking them as they stand would assert
#: coverage across a manufacturer boundary, which is the exact thing invariant
#: 3's overlap rule exists to prevent (design doc section 7). Per D3 the data
#: gets corrected instead: those items are regrouped under the issuer their own
#: documents name. That needs its own design, because it interacts with
#: RESOLVE, so phase 1 defers them -- loudly, as a counted skip, never silently.
PHASE = 1

#: This module is Komet-specific: the coverage-map mechanism reads a
#: manufacturer's OWN index, and only Komet ships one today (playbooks/komet.json).
MANUFACTURER = "KOMET"


class KometCoverageError(Exception):
    """The archived index and this tool disagree about what was filed."""


def _archived_index_path(conn, pb, source: dict):
    """The local path of one `coverage_map.sources` entry's archived copy, and
    its content_hash -- or `(None, None)` if the fetch ledger carries no row
    for it (never archived; `backfill.scan` has not run this source).

    `fetch_log` stores the SOURCE path and the content hash, not an archive
    handle, and the index is not a `document` so it has no `archive_url`
    column. The archived path is instead RECONSTRUCTED: `archiving.archive_path`
    is a pure function of (brand, content_hash, relative path, content type),
    and `backfill.scan` archives each index by passing `source["file"]` as that
    relative path, verbatim. If the reconstructed path does not exist on disk,
    that is a genuine disagreement between the ledger and the archive -- raised,
    not guessed around.
    """
    file = source["file"]
    row = conn.execute(
        "SELECT content_hash FROM fetch_log WHERE url_normalized LIKE %s "
        "ORDER BY last_checked_at DESC LIMIT 1",
        (f"%{file}",),
    ).fetchone()
    if row is None or not row["content_hash"]:
        log.warning("komet-coverage: index never archived, skipping: %s", file)
        return None, None
    content_hash = row["content_hash"]
    cfg = load_config()
    rel = archiving.archive_path(pb.manufacturer, content_hash, file, _INDEX_CONTENT_TYPE)
    dest = pathlib.Path(cfg.storage.local_root) / rel
    if not dest.exists():
        raise KometCoverageError(
            f"komet-coverage: archived index file missing on disk: {dest}"
        )
    return dest, content_hash


def _index_rows(conn, pb):
    """For each `coverage_map.sources` entry, find its archived copy and read
    it with `coverage_map.read_source`. Returns
    `(rows_by_file, archive_urls, content_hashes)` -- `rows_by_file` is
    `{file: [Row]}`, `archive_urls`/`content_hashes` are `{file: value}` for
    every source that WAS archived (a source never archived is logged and
    left out of all three, never fatal by itself)."""
    rows_by_file: dict[str, list] = {}
    archive_urls: dict[str, str] = {}
    content_hashes: dict[str, str] = {}
    for source in pb.coverage_map["sources"]:
        if source.get("phase", PHASE) != PHASE:
            continue                                   # counted by `plan`, not lost
        dest, content_hash = _archived_index_path(conn, pb, source)
        if dest is None:
            continue
        # `read_source` resolves `base_dir / source["file"]`; the archived
        # filename is the hash-prefixed, sanitized one, not `source["file"]`,
        # so a shallow copy of `source` points it at the archived name instead.
        local_source = {**source, "file": dest.name}
        rows_by_file[source["file"]] = coverage_map.read_source(dest.parent, local_source)
        archive_urls[source["file"]] = str(dest)
        content_hashes[source["file"]] = content_hash
    return rows_by_file, archive_urls, content_hashes


def _family_articles(rows_by_file: dict) -> dict:
    """`{key: {"articles": {folded reference or article, ...}, "file": file}}`.

    The reference number is preferred: it is the form Dentalia's own catalogue
    stores (spaced, `314 H1 006`) against the index's dotted `H1.314.006`, so
    it is what the C1 gate compares as `item_ref` -- basis `ref-item` under
    C12, where the primary item number is the key and is what is printed on
    the physical article. NOT `mfr_ref`, which is the supplier's own number and
    is not a matching pivot. The article number is the fallback for a row with
    no reference no. printed. Folded through `_fold_bur_code` so the catalogue's
    spaced form and the index's dotted form meet at the same key VALIDATE's C1
    gate compares against."""
    out: dict[str, dict] = {}
    for file, rows in rows_by_file.items():
        for r in rows:
            key = r.key.strip()
            val = r.reference.strip() or r.article.strip()
            if not key or not val:
                continue
            entry = out.setdefault(key, {"articles": set(), "file": file})
            entry["articles"].add(_fold_bur_code(val))
    return out


def _brand_documents(conn, brand: str) -> list[dict]:
    """Every document archived under THIS manufacturer's bucket.

    `document` has no manufacturer column, so the bucket is the discriminator:
    `archiving.archive_path` puts the sanitized manufacturer first, so every
    archive_url for this brand carries `/{brand}/`. Scoping matters because a
    family key is a bare six-digit number and the rules below match it as a
    SUBSTRING -- unscoped, another manufacturer's document that happens to
    carry those six digits in its filename or source path becomes a family
    match, and `apply` would append Komet's article list to a foreign
    document's extraction at conf 1.0."""
    return conn.execute(
        "SELECT content_hash, archive_url, source_url FROM document "
        "WHERE archive_url LIKE %s",
        (f"%/{brand}/%",),
    ).fetchall()


def _documents_by_family(docs, keys, resolves) -> dict:
    """`{key: [content_hash, ...]}` -- every document whose `archive_url`
    carries a family key (`filename-prefix`: `__{key}` in the archived name)
    or whose `source_url` does (`folder`: `/{key}/`), per `key_resolves`."""
    out: dict[str, list[str]] = collections.defaultdict(list)
    for key in keys:
        for d in docs:
            for rule in resolves:
                kind = rule.get("kind")
                if kind == "filename-prefix":
                    hit = d["archive_url"] and f"__{key}" in d["archive_url"]
                elif kind == "folder":
                    hit = d["source_url"] and f"/{key}/" in d["source_url"]
                else:
                    hit = False
                if hit:
                    out[key].append(d["content_hash"])
                    break
    return out


def _latest_fields(conn, content_hash: str):
    """`(extract_rev, fields)` for a document's current extraction, or `None`
    if it has none (a document with no extraction_attempt at all -- should not
    happen post-GATE, but never assumed)."""
    row = conn.execute(
        "SELECT extract_rev, fields FROM extraction_attempt WHERE content_hash=%s "
        "ORDER BY extract_rev DESC LIMIT 1",
        (content_hash,),
    ).fetchone()
    if row is None:
        return None
    return row["extract_rev"], row["fields"]


def plan(conn) -> tuple[list[dict], list[dict]]:
    """`(plans, skipped)`. A plan links one filed document to the article list
    its family covers in Komet's own index; a skip is counted, never silent."""
    pb = playbooks.for_manufacturer(MANUFACTURER)
    if pb is None or not pb.coverage_map:
        return [], []

    rows_by_file, archive_urls, content_hashes = _index_rows(conn, pb)
    families = _family_articles(rows_by_file)
    docs = _brand_documents(conn, archiving._sanitize(pb.manufacturer))
    doc_map = _documents_by_family(docs, families.keys(), pb.coverage_map["key_resolves"])
    doc_archive_url = {d["content_hash"]: d["archive_url"] for d in docs}

    plans: list[dict] = []
    skipped: list[dict] = [
        {"source": src["file"], "reason": f"deferred to phase {src['phase']}"}
        for src in pb.coverage_map["sources"] if src.get("phase", PHASE) != PHASE
    ]
    for key in sorted(families):
        info = families[key]
        articles = info["articles"]
        hashes = doc_map.get(key, [])
        if not hashes:
            skipped.append({"key": key, "articles": len(articles),
                             "reason": "no document for family"})
            continue
        for content_hash in hashes:
            latest = _latest_fields(conn, content_hash)
            if latest is None:
                skipped.append({"key": key, "content_hash": content_hash,
                                 "reason": "no extraction for document"})
                continue
            extract_rev, fields = latest
            wanted = sorted(articles)
            current = (fields.get("ref_list") or {}).get("value")
            if current == wanted:
                skipped.append({"key": key, "content_hash": content_hash,
                                 "reason": "already carries this list"})
                continue
            plans.append({
                "key": key,
                "content_hash": content_hash,
                "extract_rev": extract_rev,
                "fields": fields,
                "ref_list": wanted,
                "index_url": archive_urls[info["file"]],
                "index_file": info["file"],
                "index_hash": content_hashes[info["file"]],
                "doc_archive_url": doc_archive_url.get(content_hash),
            })
    return plans, skipped


def apply(conn, plans: list[dict]) -> dict:
    """Append each plan's ref_list as a new `extract_rev` and re-point its
    `validate.doc` job at that rev. The caller owns the transaction."""
    appended = deleted = emitted = 0
    for p in plans:
        rev = tiers.next_extract_rev(conn, p["content_hash"])
        fields = dict(p["fields"])
        fields["ref_list"] = {
            "value": sorted(p["ref_list"]),
            "conf": 1.0,
            "tier": "T0",
            "model_id": None,
            "verbatim": f"{len(p['ref_list'])} article(s) under "
                        f"'Where to find DoC' = {p['key']}",
            "page": None,
            "archive_url": p["index_url"],
            "source": MARKER,
        }
        tiers.write_extraction_attempt(
            conn, p["content_hash"], ["T0"], fields, extract_rev=rev)
        appended += 1

        # Only jobs not yet started: a running job is mid-flight and a
        # terminal one is history. Both are left exactly as they are.
        stale = conn.execute(
            "DELETE FROM job WHERE type='validate.doc' AND status='pending' "
            "AND payload->>'content_hash'=%s AND (payload->>'extract_rev')::int=%s "
            "RETURNING payload",
            (p["content_hash"], p["extract_rev"]),
        ).fetchall()
        deleted += len(stale)
        # Keep the scope the original job carried; dropping it would turn a
        # group-scoped match into an unscoped one.
        group_id = stale[0]["payload"].get("group_id") if stale else None
        if queue.enqueue(
            conn, "validate.doc",
            {"content_hash": p["content_hash"], "group_id": group_id, "extract_rev": rev},
            dedupe_key=f"validate:{p['content_hash']}:{rev}:{group_id}",
        ) is not None:
            emitted += 1
    return {"appended": appended, "jobs_deleted": deleted, "jobs_emitted": emitted}


def render(plans: list[dict], skipped: list[dict]) -> list[str]:
    """Report lines. Skips are printed, never silent (CLAUDE.md)."""
    out = []
    for p in plans:
        name = (p["doc_archive_url"] or "").rsplit("/", 1)[-1]
        out.append(f"  {p['key']:>10}  {p['content_hash'][:12]}  "
                   f"{len(p['ref_list'])} article(s)  {name[:52]}")
    for s in skipped:
        bits = [s.get("key") or s.get("source", "?")]
        if "content_hash" in s:
            bits.append(s["content_hash"][:12])
        if "articles" in s:
            bits.append(f"{s['articles']} article(s)")
        out.append(f"  SKIPPED {'  '.join(bits)}  {s['reason']}")
    out.append(f"\n{len(plans)} to link, {len(skipped)} skipped")

    seen_hashes: set[str] = set()
    for p in plans:
        h = p["index_hash"]
        if h in seen_hashes:
            continue
        seen_hashes.add(h)
        out.append(f"  index {p['index_file']}: {h}")
    return out
