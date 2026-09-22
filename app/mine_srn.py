"""Read EUDAMED SRNs off documents we already hold.

`manufacturer_srn` is filled from two places, and neither reaches a
manufacturer that has no notified-body certificates. The register pull
(`eudamed.certregister`) sees only actors who appear in the CERTIFICATE
register; the article probe that exists for exactly that gap is unreachable,
because `probe_srn` runs only inside a sweep and the release route refuses with
400 on the same empty-SRN condition. GC EUROPE has 356 device articles, zero
certificates, and sits in that deadlock.

It need not. Measured 2026-09-09, GC EUROPE's SRN appears in **126 documents
already in the archive**, and 6 of the 340 manufacturers carrying item groups
with no trusted SRN have theirs sitting in text we have already parsed. This
module reads them out. No network call, no EUDAMED round trip, no LLM.

**Everything it writes is `pending`.** The pattern matches an importer's or a
notified body's SRN quoted inside somebody else's document exactly as happily
as the manufacturer's own, and a confirmed SRN makes that manufacturer's WHOLE
catalogue sweepable -- so the decision belongs to the person at
`/manufacturers/srn-queue`, which already exists and already renders
`discovered_via` generically. `source_doc_id` (migration 066) is what makes
that decision possible: a reviewer opens the page instead of confirming a bare
string.

Shape is `repair_ref_list`'s -- plan / render / apply, dry run by default --
with one departure: there is nothing to re-extract and no `validate.doc` to
re-emit, because an SRN is not document evidence. It is an attribution about a
manufacturer, and the only thing downstream of it is whether a sweep may run.
"""

from __future__ import annotations

import re


#: `BE-MF-000001608`. Two letters, `-MF-`, then **nine** digits. The tracker
#: entry that proposed this work carried `[0-9]{12}` and therefore matched
#: nothing at all -- anyone implementing it as written would have measured an
#: empty result and concluded the SRNs were not there.
SRN_RE = re.compile(r"\b([A-Z]{2}-MF-[0-9]{9})\b")

#: A row in one of these states has been decided, or is being proposed already.
#: Re-running must not put a rejected candidate back in somebody's queue.
_DECIDED = ("auto", "confirmed", "rejected", "pending")

OUTCOMES = ("found", "no_manufacturer", "already_trusted", "already_decided")


_CANDIDATE_SQL = """
SELECT d.doc_id, d.canonical_manufacturer, t.content
  FROM document d
  JOIN document_text t ON t.content_hash = d.content_hash
 WHERE t.content ~ '[A-Z]{2}-MF-[0-9]{9}'
 ORDER BY d.doc_id
"""


def plan(conn) -> tuple[list[dict], dict]:
    """`(candidates, counts)`. Writes nothing.

    One row per (manufacturer, SRN) pair, keeping the LOWEST `doc_id` that
    names it: the reviewer needs one page to look at, not 126, and the oldest
    is the most likely to be the manufacturer's own declaration rather than a
    later document quoting it.
    """
    counts = {k: 0 for k in OUTCOMES}
    counts["scanned"] = 0

    trusted = {r["canonical_name"] for r in conn.execute(
        "SELECT canonical_name FROM trusted_manufacturer_srn").fetchall()}
    decided = {(r["canonical_name"], r["srn"]) for r in conn.execute(
        "SELECT canonical_name, srn FROM manufacturer_srn WHERE status = ANY(%s)",
        (list(_DECIDED),)).fetchall()}

    seen: dict[tuple[str, str], dict] = {}
    for row in conn.execute(_CANDIDATE_SQL).fetchall():
        counts["scanned"] += 1
        mfr = row["canonical_manufacturer"]
        srns = set(SRN_RE.findall(row["content"] or ""))
        if not srns:
            continue
        if not mfr:
            # No manufacturer to attribute it to. Inferring one from the
            # filename or the surrounding text is precisely the guess this
            # table's `status` column exists to prevent.
            counts["no_manufacturer"] += 1
            continue
        if mfr in trusted:
            # A trusted SRN already answers "who is this in EUDAMED". Piling
            # more candidates behind a decision already taken is noise.
            counts["already_trusted"] += 1
            continue
        for srn in sorted(srns):
            key = (mfr, srn)
            if key in decided:
                counts["already_decided"] += 1
                continue
            if key in seen:
                continue                   # keep the first (lowest) doc_id
            seen[key] = {"canonical_name": mfr, "srn": srn,
                         "source_doc_id": row["doc_id"]}
            counts["found"] += 1

    return sorted(seen.values(), key=lambda c: (c["canonical_name"], c["srn"])), counts


def apply(conn, candidates: list[dict]) -> dict:
    """Insert each candidate as a `pending` row. The caller owns the transaction.

    `ON CONFLICT DO NOTHING` rather than an upsert: a row that already exists
    carries somebody's decision, or an earlier discovery path's provenance, and
    a re-run must not overwrite either with a weaker one.
    """
    inserted = 0
    for c in candidates:
        cur = conn.execute(
            "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, "
            "  status, source_doc_id) VALUES (%s, %s, 'text-mined', 'pending', %s) "
            "ON CONFLICT (canonical_name, srn) DO NOTHING",
            (c["canonical_name"], c["srn"], c["source_doc_id"]))
        inserted += cur.rowcount
    return {"inserted": inserted, "proposed": len(candidates)}


def render(candidates: list[dict], counts: dict, *, apply: bool) -> str:
    """The operator's view. Names every candidate WITH its document, because
    the whole point of the source column is that a decision is checkable."""
    lines = [
        f"scanned {counts['scanned']} document(s) naming an SRN",
        f"  found            {counts['found']}",
        f"  no manufacturer  {counts['no_manufacturer']}",
        f"  already trusted  {counts['already_trusted']}",
        f"  already decided  {counts['already_decided']}",
        "",
    ]
    if candidates:
        lines.append(f"{len(candidates)} candidate(s) for /manufacturers/srn-queue:")
        for c in candidates:
            lines.append(f"  {c['canonical_name']:<28} {c['srn']}  "
                         f"(read from document {c['source_doc_id']})")
    else:
        lines.append("no new candidates")
    lines.append("")
    lines.append(
        "written as `pending`; confirm at /manufacturers/srn-queue"
        if apply else
        "dry run -- nothing written. Re-run with --apply to propose these.")
    return "\n".join(lines)
