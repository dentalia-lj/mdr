"""One-off repair: bind existing documents to the manufacturer their evidence
already names.

Migration 053 added `document.canonical_manufacturer` nullable and additive, so
every document written before it carries NULL. That is correct but useless:
NULL means UNDECIDED, and the whole point of the column is that 540 documents
reach no manufacturer through item links -- 443 of them `filed`, which is every
filed document by definition (C15 files exactly what we stock nothing for, and
the link path runs through what we stock).

531 of those 540 carry an `evidence` row with `field='manufacturer'`: the name
EXTRACT read off the page, in the document's own spelling, never resolved to a
catalogue entity. Measured through `resolve_canonicals` on 2026-08-31:

    531 evidence strings
      -> 481  resolve to exactly one canonical   (90.6%)
      ->   0  ambiguous (>1 canonical)
      ->  50  resolve to none

The 50 are real manufacturers Dentalia holds documents for but stocks no items
from -- SHOFU INC., Alfred Becht GmbH, Sure Dent Corporation, Inter-Med Inc.,
META BIOMED CO. LTD. among them. They stay NULL. Minting `manufacturer` rows
for them would seed the registry from OCR, which is exactly what VALIDATE's
read-only alias lookup exists to prevent (PRD §219: "VALIDATE never self-seeds
the way RESOLVE does, so a garbled OCR name cannot mint a manufacturer").

Zero ambiguity is a fact about today's data, not a guarantee: a spelling that
folds to two curated canonicals is skipped and counted, never guessed at, for
the reason `resolve_canonicals` documents -- choosing between them is a coin
flip over a manufacturer's whole range.

A document that ALREADY carries a binding is never touched. A human decision,
or C16's, outranks a string read off a page, and that is what makes a second
run plan zero.

Resolution goes through `gate._bindable_manufacturer` as well as
`resolve_canonicals`, so the tool and the handler cannot disagree about what is
storable: same FK, same guard, one definition.

Writing `document` outside GATE is an invariant-1 exception of the same shape
`repair_archive_urls` and `repair_mfr_overbind` already take; `decided_by`
records it. Nothing else on the row is touched -- not status, not links, not
evidence. The document's disposition is unchanged; it merely becomes findable.

Dry run by default:

    python -m app.cli repair-document-manufacturer            # report only
    python -m app.cli repair-document-manufacturer --apply
"""

from __future__ import annotations

from psycopg.types.json import Json

from app import manufacturers
from app.handlers.gate import _bindable_manufacturer

EVENT = "manufacturer-backfilled"
DECIDED_BY = "tool:repair-document-manufacturer"
RULING = "053 document-manufacturer binding, Denis 2026-08-31"


def plan(conn) -> dict:
    """What the backfill would write, and what it would decline.

    Returns `{"resolved": [{doc_id, canonical_name, printed}],
              "ambiguous": [{doc_id, printed, canonicals}],
              "unresolved": [{doc_id, printed}]}`.

    Every document with a null binding and a manufacturer evidence row is
    classified; nothing is silently dropped (CLAUDE.md: skipped rows counted).
    """
    rows = conn.execute(
        # DISTINCT ON ... extract_rev DESC: the LATEST reading, so a
        # re-extraction that corrected the name is the one that binds.
        "SELECT DISTINCT ON (e.doc_id) e.doc_id, e.value AS printed "
        "FROM evidence e "
        "JOIN document d ON d.doc_id = e.doc_id "
        "WHERE e.field = 'manufacturer' AND d.canonical_manufacturer IS NULL "
        "ORDER BY e.doc_id, e.extract_rev DESC"
    ).fetchall()

    out = {"resolved": [], "ambiguous": [], "unresolved": []}
    for r in rows:
        canonicals = manufacturers.resolve_canonicals(conn, r["printed"])
        if len(canonicals) > 1:
            out["ambiguous"].append({"doc_id": r["doc_id"], "printed": r["printed"],
                                     "canonicals": canonicals})
            continue
        storable = _bindable_manufacturer(conn, canonicals[0]) if canonicals else None
        if storable is None:
            out["unresolved"].append({"doc_id": r["doc_id"], "printed": r["printed"]})
            continue
        out["resolved"].append({"doc_id": r["doc_id"], "canonical_name": storable,
                                "printed": r["printed"]})
    return out


def apply(conn, resolved: list[dict]) -> int:
    """Write each planned binding and audit it. The caller owns the transaction.

    Guarded UPDATE (the null condition is re-checked in the WHERE) so a document
    bound since planning -- by a reviewer, mid-run -- is skipped rather than
    overwritten, and skipped means un-audited: the trail records only what
    actually changed.
    """
    written = 0
    for r in resolved:
        changed = conn.execute(
            "UPDATE document SET canonical_manufacturer=%s "
            "WHERE doc_id=%s AND canonical_manufacturer IS NULL "
            "RETURNING doc_id",
            (r["canonical_name"], r["doc_id"]),
        ).fetchone()
        if changed is None:
            continue
        conn.execute(
            "INSERT INTO audit_log (event, doc_id, item_ref, decided_by, via_job, "
            "job_snapshot, detail) VALUES (%s, %s, NULL, %s, NULL, %s, %s)",
            (EVENT, r["doc_id"], DECIDED_BY,
             Json({"tool": "repair-document-manufacturer", "ruling": RULING}),
             Json({"canonical_manufacturer": r["canonical_name"],
                   "printed": r["printed"], "basis": "manufacturer-evidence"})))
        written += 1
    return written


def render(planned: dict, *, verbose: bool = False) -> list[str]:
    """Report lines. What is declined is printed with its reason, never silent."""
    out = []
    if verbose:
        for r in planned["resolved"]:
            out.append(f"  doc {r['doc_id']:>5}  {r['printed']!r} -> {r['canonical_name']}")
    for a in planned["ambiguous"]:
        out.append(f"  SKIP doc {a['doc_id']:>5}  {a['printed']!r} folds to "
                   f"{len(a['canonicals'])} canonicals ({', '.join(a['canonicals'])}) "
                   f"— a human must choose")
    seen = sorted({u["printed"] for u in planned["unresolved"]})
    for name in seen:
        n = sum(1 for u in planned["unresolved"] if u["printed"] == name)
        out.append(f"  LEFT doc x{n:<3} {name!r} — no curated alias; minting one "
                   f"would seed the registry from OCR")
    out.append(
        f"\n{len(planned['resolved'])} to bind, "
        f"{len(planned['ambiguous'])} ambiguous (skipped), "
        f"{len(planned['unresolved'])} unresolved (left NULL)")
    return out
