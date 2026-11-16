"""Read-only dry run of the subcontractor rules (spec 2026-11-13 § 7, plan Task 9).

Answers, before anything ships: which whole-range documents would the new rules
treat differently, and how would the subcontractor documents be routed. It reads
the playbooks from a DIRECTORY (the edited files), because the database still
holds the old ones, and it simulates the alias removal the deploy will do: the
names that moved to a `subcontractors` list are ignored when resolving.

Writes nothing. Run inside the worker container:

    python tools/subcontractor_dry_run.py --playbooks /tmp/playbooks

Decisions per document, in the order the rules apply:
  type-not-whole-range  IFU / other / SPP never machine-bind a whole range
  subcontractor         the printed issuer is listed by the document's brand
  issuer-not-brand      another known company printed it
  t0-only               the only manufacturer evidence is T0's "playbook identity",
                        which a re-read would replace: cannot tell from stored data
  unchanged
"""
from __future__ import annotations

import argparse
import collections

import json
import pathlib

from app import db, manufacturers

#: The subcontractor documents found on 2026-11-13 (spec F7, F9).
SUBCONTRACTOR_DOCS = (165, 166, 383, 384, 385, 387, 390, 391, 392, 434, 450, 458, 587,
                      588, 591, 597, 1149, 1150, 1170, 1171, 1185, 2155, 2159, 2192, 2199)
NEVER_WHOLE_RANGE_TYPES = ("IFU", "other", "SPP")   # mirrors gate.NEVER_WHOLE_RANGE_TYPES


class _Lists:
    """`app.subcontractors`, re-done on the JSON files: the server runs the code
    from before this change, so neither the module nor the parsed key exists
    there yet. Same matching rule (spec § 4.2)."""

    def __init__(self, directory):
        self.lists = {}
        for f in sorted(pathlib.Path(directory).glob("*.json")):
            data = json.loads(f.read_text(encoding="utf-8"))
            self.lists[data["manufacturer"].strip()] = [e.strip() for e in data.get("subcontractors", [])]
        canon = {manufacturers.normalize(m) for m in self.lists}
        self.moved = {manufacturers.normalize(e) for es in self.lists.values() for e in es
                      if manufacturers.normalize(e) not in canon}

    def listing_brands(self, conn, printed):
        key = manufacturers.normalize(printed)
        if not key:
            return []
        resolved = {manufacturers.normalize(c) for c in manufacturers.resolve_canonicals(conn, printed)}
        return sorted(b for b, es in self.lists.items()
                      if any(manufacturers.normalize(e) in (key,) or manufacturers.normalize(e) in resolved
                             for e in es))


LISTS = None


def _patch(directory, conn):
    """Make the lookups see the edited playbooks and the post-deploy alias table."""
    global LISTS
    LISTS = _Lists(directory)
    moved = LISTS.moved
    rows = [r for r in conn.execute("SELECT raw_name, canonical_name FROM manufacturer_alias")
            if manufacturers.normalize(r["raw_name"]) not in moved]

    def resolve(_conn, name):
        target = manufacturers.normalize(name)
        if not target:
            return []
        return sorted({r["canonical_name"] for r in rows
                       if target in (manufacturers.normalize(r["raw_name"]),
                                     manufacturers.normalize(r["canonical_name"]))})
    manufacturers.resolve_canonicals = resolve
    return moved


def _issuer_not_brand(conn, printed, brand):
    """Same rule as gate._issuer_not_brand, without importing GATE's PDF stack."""
    if not printed or not brand:
        return False
    listed_by = LISTS.listing_brands(conn, printed)
    if listed_by and brand not in listed_by:
        return True
    canonicals = manufacturers.resolve_canonicals(conn, printed)
    if not canonicals:
        return False
    theirs = set(manufacturers.item_codes_for(conn, brand))
    return not any(set(manufacturers.item_codes_for(conn, c)) & theirs or c == brand
                   for c in canonicals)


def _printed(conn, doc_id):
    """(printed name, only-T0) from the stored evidence: a paid tier's reading first."""
    rows = conn.execute("SELECT value, tier, verbatim FROM evidence WHERE doc_id=%s "
                        "AND field='manufacturer' AND value IS NOT NULL", (doc_id,)).fetchall()
    read = [r["value"] for r in rows
            if not (r["tier"] == "T0" and (r["verbatim"] or "").startswith("playbook identity"))]
    if read:
        return read[0], False
    return (rows[0]["value"], True) if rows else (None, False)


def decide(conn, doc) -> tuple[str, str | None]:
    printed, t0_only = _printed(conn, doc["doc_id"])
    if doc["type"] in NEVER_WHOLE_RANGE_TYPES:
        return "type-not-whole-range", printed
    brand = doc["canonical_manufacturer"]
    listed_by = LISTS.listing_brands(conn, printed)
    if listed_by and (brand is None or brand in listed_by):
        return "subcontractor", printed
    if brand and _issuer_not_brand(conn, printed, brand):
        return "issuer-not-brand", printed
    if t0_only:
        return "t0-only", printed
    return "unchanged", printed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--playbooks", required=True, help="directory of the edited playbook files")
    args = ap.parse_args()
    with db.connect() as conn:
        conn.read_only = True
        conn.execute("SET statement_timeout = '20s'")
        moved = _patch(args.playbooks, conn)
        print(f"playbooks: {len(LISTS.lists)}; moved names ignored: {sorted(moved)}\n")

        docs = conn.execute(
            "SELECT d.doc_id, d.type, d.status, d.canonical_manufacturer, "
            "  (SELECT count(*) FROM item_document l WHERE l.doc_id=d.doc_id "
            "    AND l.status='production') AS links "
            "FROM document d WHERE d.coverage_scope='manufacturer' "
            "AND d.status IN ('production','superseded','staged') ORDER BY d.doc_id").fetchall()
        tally = collections.Counter()
        changed_linked = []
        for d in docs:
            verdict, printed = decide(conn, d)
            tally[(d["status"], verdict)] += 1
            if d["links"] and verdict not in ("unchanged", "t0-only"):
                changed_linked.append((d["doc_id"], d["type"], d["canonical_manufacturer"],
                                       printed, d["links"], verdict))
        print("whole-range documents by status and verdict:")
        for (status, verdict), n in sorted(tally.items()):
            print(f"  {status:11s} {verdict:22s} {n}")
        print("\nlinked whole-range documents the new rules treat differently:")
        for row in changed_linked:
            print("  doc {} {} {} printed={!r} links={} -> {}".format(*row))

        print("\nsubcontractor documents (spec F7, F9):")
        for doc_id in SUBCONTRACTOR_DOCS:
            d = conn.execute("SELECT doc_id, type, status, coverage_scope, canonical_manufacturer "
                             "FROM document WHERE doc_id=%s", (doc_id,)).fetchone()
            if d is None:
                print(f"  doc {doc_id}: not found")
                continue
            printed, _ = _printed(conn, doc_id)
            print(f"  doc {doc_id} {d['type']} {d['status']} {d['coverage_scope']} "
                  f"canonical={d['canonical_manufacturer']} printed={printed!r} "
                  f"listed_by={LISTS.listing_brands(conn, printed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
