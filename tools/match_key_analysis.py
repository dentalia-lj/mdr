"""Does `item_ref` beat `mfr_ref` as the REF-matching key?

Task 6 of `docs/superpowers/plans/2026-08-12-extraction-correctness.md`. This is
a MEASUREMENT, not a change: it is read-only, costs nothing, and can be redone
after any backfill. Nothing here touches `validate.py`.

Why it exists. The client's C12 answer (Nataša Palme): *"Ni tako pomembna --
glavna je primarna St., ker je ta tudi na samem fizicnem artiklu navedena"* --
the supplier article number is not important, the primary item number is, because
that is the one printed on the physical article. Invariant 3 nevertheless keys
auto-writes on `(canonical_manufacturer, mfr_ref)`, so the ruling and the
invariant disagree, and the audit calls this "the largest open assumption under
the matching design".

The trap this guards against. The `ref-catalogue` guards -- minimum length,
prose exclusion, cross-manufacturer ambiguity -- were calibrated over `mfr_ref`
values. `item_ref` is Dentalia's OWN numbering, so its collision and length
profiles are different and the guards must be RE-DERIVED, never reused. A
concrete instance found by hand: `2302` is an `item_ref` under manufacturer code
`333` (gloves), so a GC declaration listing `2302` would link to another
company's product. That is precisely what a naive switch would ship.

Catalogue scope: Dentalia imports from the Ljubljana catalogue only (Zagreb is to
be mirrored into it eventually, Denis 2026-08-12), so cross-catalogue collisions
are out of scope and `--catalogue` defaults to every row present.

Run:  PYTHONPATH=. .venv/bin/python -m tools.match_key_analysis
"""

from __future__ import annotations

import argparse
import collections
import json

from app import db
from app.config import load_config


def _rows(conn, sql, params=()):
    return conn.execute(sql, params).fetchall()


# --- 1. collisions: does one code mean one manufacturer? --------------------

def collisions(conn, column: str) -> dict:
    """How many values of `column` are held by more than one
    `canonical_manufacturer`. The mfr_ref equivalent measured 24 of 5.511
    (0,44%); item_ref's profile is expected to differ and is the whole question.
    """
    rows = _rows(conn, f"""
        SELECT m.{column} AS code, count(DISTINCT g.canonical_manufacturer) AS mfrs
        FROM item_group_member m
        JOIN item_group g USING (group_id)
        WHERE m.{column} IS NOT NULL AND m.{column} <> ''
        GROUP BY 1
    """)
    total = len(rows)
    colliding = [r for r in rows if r["mfrs"] > 1]
    return {
        "distinct": total,
        "colliding": len(colliding),
        "pct": round(100.0 * len(colliding) / total, 2) if total else 0.0,
    }


def collision_examples(conn, column: str, limit: int = 10) -> list[dict]:
    return [dict(r) for r in _rows(conn, f"""
        SELECT m.{column} AS code,
               string_agg(DISTINCT g.canonical_manufacturer, ' | ') AS manufacturers,
               count(DISTINCT g.canonical_manufacturer) AS mfrs
        FROM item_group_member m
        JOIN item_group g USING (group_id)
        WHERE m.{column} IS NOT NULL AND m.{column} <> ''
        GROUP BY 1 HAVING count(DISTINCT g.canonical_manufacturer) > 1
        ORDER BY 3 DESC, 1 LIMIT %s
    """, (limit,))]


# --- 2. do the existing guards even admit these values? ---------------------

def guard_profile(conn, column: str, min_len: int) -> dict:
    """`_eligible_unscoped_refs` drops values shorter than `min_len` and any
    containing "!" (the mfr_ref_prose rule: `UKINJENO!`, `NI VEC DOBAVLJIVO!`).
    Both guards were tuned on mfr_ref; this reports what they would do here."""
    rows = _rows(conn, f"""
        SELECT DISTINCT m.{column} AS code FROM item_group_member m
        WHERE m.{column} IS NOT NULL AND m.{column} <> ''
    """)
    codes = [r["code"] for r in rows]
    too_short = [c for c in codes if len(c.strip()) < min_len]
    prose = [c for c in codes if "!" in c]
    return {
        "distinct": len(codes),
        "too_short": len(too_short),
        "prose": len(prose),
        "eligible": len([c for c in codes
                         if len(c.strip()) >= min_len and "!" not in c]),
        "too_short_examples": sorted(set(too_short))[:10],
    }


# --- 3/4. reach over the REFs actually extracted from the corpus ------------

def _extracted_refs(conn) -> list[str]:
    rows = _rows(conn, """
        SELECT DISTINCT jsonb_array_elements_text(value::jsonb) AS ref
        FROM evidence WHERE field='ref_list' AND value LIKE '[%%'
    """)
    return [r["ref"] for r in rows]


def reach(conn, refs: list[str]) -> dict:
    """Per extracted REF: which key finds it, and does it stay unambiguous.

    `both`/`item_only`/`mfr_only` is the overlap the plan asks for; `ambiguous`
    is the number that would newly resolve to MORE than one manufacturer under
    item_ref, i.e. the ones a switch would have to flag rather than link."""
    if not refs:
        return {"refs": 0}
    rows = _rows(conn, """
        SELECT r.ref,
          (SELECT count(DISTINCT g.canonical_manufacturer) FROM item_group_member m
             JOIN item_group g USING (group_id) WHERE m.mfr_ref = r.ref)  AS mfr_hits,
          (SELECT count(DISTINCT g.canonical_manufacturer) FROM item_group_member m
             JOIN item_group g USING (group_id) WHERE m.item_ref = r.ref) AS item_hits
        FROM unnest(%s::text[]) AS r(ref)
    """, (refs,))
    c = collections.Counter()
    for r in rows:
        m, i = r["mfr_hits"], r["item_hits"]
        if m and i:
            c["both"] += 1
        elif i:
            c["item_only"] += 1
        elif m:
            c["mfr_only"] += 1
        else:
            c["neither"] += 1
        if i > 1:
            c["item_ambiguous"] += 1
        if m > 1:
            c["mfr_ambiguous"] += 1
    return {"refs": len(rows), **dict(c)}


def items_reached(conn, refs: list[str]) -> dict:
    """The number that decides the case: distinct catalogue ITEMS a document
    REF can reach under each key. Coverage of what Dentalia stocks is the goal,
    not the match rate of what manufacturers print."""
    if not refs:
        return {}
    row = _rows(conn, """
        SELECT
          (SELECT count(DISTINCT m.item_ref) FROM item_group_member m
             WHERE m.mfr_ref = ANY(%s))  AS by_mfr_ref,
          (SELECT count(DISTINCT m.item_ref) FROM item_group_member m
             WHERE m.item_ref = ANY(%s)) AS by_item_ref,
          (SELECT count(DISTINCT m.item_ref) FROM item_group_member m
             WHERE m.item_ref = ANY(%s) OR m.mfr_ref = ANY(%s)) AS by_either
    """, (refs, refs, refs, refs))[0]
    return dict(row)


def documents_gaining(conn, refs: list[str]) -> dict:
    """Documents that hold a ref_list but produced no link today, and how many
    of them would reach at least one item through item_ref."""
    rows = _rows(conn, """
        WITH nolink AS (
          SELECT e.doc_id, e.value FROM evidence e
          WHERE e.field='ref_list' AND e.value LIKE '[%%'
            AND NOT EXISTS (SELECT 1 FROM item_document l WHERE l.doc_id = e.doc_id)),
        ex AS (SELECT doc_id, jsonb_array_elements_text(value::jsonb) AS ref FROM nolink)
        SELECT count(DISTINCT doc_id) AS docs_without_links,
               count(DISTINCT doc_id) FILTER (
                 WHERE EXISTS (SELECT 1 FROM item_group_member m WHERE m.item_ref = ex.ref)
               ) AS would_gain_via_item_ref
        FROM ex
    """)
    return dict(rows[0]) if rows else {}


def analyse(conn, min_len: int) -> dict:
    refs = _extracted_refs(conn)
    return {
        "collisions": {
            "mfr_ref": collisions(conn, "mfr_ref"),
            "item_ref": collisions(conn, "item_ref"),
            "item_ref_examples": collision_examples(conn, "item_ref"),
        },
        "guards": {
            "min_unscoped_ref_len": min_len,
            "mfr_ref": guard_profile(conn, "mfr_ref", min_len),
            "item_ref": guard_profile(conn, "item_ref", min_len),
        },
        "extracted_refs": reach(conn, refs),
        "items_reached": items_reached(conn, refs),
        "documents": documents_gaining(conn, refs),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="emit raw JSON")
    args = ap.parse_args()

    cfg = load_config()
    with db.connect(cfg.connection.database_url) as conn:
        out = analyse(conn, cfg.validate.min_unscoped_ref_len)

    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    co, gu = out["collisions"], out["guards"]
    print("== collisions (one code -> more than one manufacturer) ==")
    for key in ("mfr_ref", "item_ref"):
        c = co[key]
        print(f"  {key:9} {c['colliding']:>5} of {c['distinct']:>6} distinct ({c['pct']}%)")
    if co["item_ref_examples"]:
        print("  item_ref collisions (worst first):")
        for e in co["item_ref_examples"]:
            print(f"    {e['code']:<14} {e['mfrs']}x  {e['manufacturers'][:70]}")

    print(f"\n== guards (min_len={gu['min_unscoped_ref_len']}, prose='!') ==")
    for key in ("mfr_ref", "item_ref"):
        g = gu[key]
        print(f"  {key:9} distinct={g['distinct']:>6} eligible={g['eligible']:>6} "
              f"too_short={g['too_short']:>5} prose={g['prose']:>4}")
        if g["too_short_examples"]:
            print(f"    too-short examples: {', '.join(g['too_short_examples'])}")

    print("\n== extracted corpus REFs ==")
    for k, val in out["extracted_refs"].items():
        print(f"  {k:16} {val}")
    print("\n== catalogue items reached ==")
    for k, val in out["items_reached"].items():
        print(f"  {k:16} {val}")
    print("\n== documents ==")
    for k, val in out["documents"].items():
        print(f"  {k:26} {val}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
