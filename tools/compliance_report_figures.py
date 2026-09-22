"""Every figure in the client report, re-measured in one run.

    python -m tools.compliance_report_figures            # against the dev DB
    python -m tools.compliance_report_figures --markdown # ready to paste

The report (`docs/2026-08-24-what-the-system-does-{en,sl}.md` and the published
artifact) hardcodes its numbers, on purpose: it is a dated statement of what
was true on a day, not a live dashboard. That only works if re-measuring is one
command, so this is it. Re-run, diff against the report, edit both languages.

Deliberately reuses `web.registry` rather than re-deriving the SQL: figures a
client reads must be the same ones the completeness card shows them, computed
by the same code. A second hand-written query here is how the two drift.

Dev-time analysis, not pipeline (`tools/` is excluded from the wheel). Read-only.
"""

from __future__ import annotations

import argparse
import collections
import os
from datetime import date

import psycopg
from psycopg.rows import dict_row

from app.compliance import ROW_DOC, ROW_IFU, ROW_NB
from web.registry import _card_rows

DEFAULT_DSN = os.environ.get(
    "DATABASE_URL", "postgresql://dentalia:dentalia@localhost:5432/dentalia"
)

# Same shape `manufacturer_completeness` builds, minus the manufacturer filter.
# `l.status <> 'retracted'` on the JOIN, not in WHERE: as a WHERE it would drop
# every article with no link at all, which is most of them.
_HOLDINGS_SQL = """
SELECT i.item_ref, i.product_class,
       d.doc_id, d.type, d.status::text AS doc_status, d.cert_number,
       l.status::text AS link_status, l.match_basis,
       ee.expires, ee.basis AS expiry_basis
FROM item_mirror i
LEFT JOIN item_document l              ON l.item_ref = i.item_ref
                                      AND l.status <> 'retracted'
LEFT JOIN document d                   ON d.doc_id = l.doc_id
LEFT JOIN document_effective_expiry ee ON ee.doc_id = d.doc_id
WHERE i.md_flag IS TRUE
"""

_HEADLINE_SQL = """
SELECT (SELECT count(*) FROM item_mirror)                                  AS items_mirrored,
       (SELECT count(*) FROM item_mirror WHERE md_flag IS TRUE)            AS md_items,
       (SELECT count(*) FROM document)                                     AS documents,
       (SELECT count(*) FROM document WHERE status='production')           AS production_docs,
       (SELECT count(*) FROM document WHERE status='staged')               AS staged_docs,
       (SELECT count(*) FROM evidence)                                     AS evidence_rows,
       (SELECT count(*) FROM item_document)                                AS links,
       (SELECT count(*) FROM manual_task WHERE status='open')              AS open_manual
"""

# Report order, worst-first, matching the table's columns.
_STATES = ("held", "expired", "review-due", "superseded", "review", "missing")
_STATE_LABEL = {
    "held": "current",
    "expired": "expired",
    "review-due": "review due",
    "superseded": "replaced",
    "review": "awaiting",
    "missing": "none at all",
}
# Class order as the report prints it: biggest population first.
_CLASS_ORDER = ("Ir", "IIa", "I", "IIb", "III", "Is", "Im")


def measure(dsn: str, today: date) -> dict:
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        headline = conn.execute(_HEADLINE_SQL).fetchone()
        rows = conn.execute(_HOLDINGS_SQL).fetchall()

    by_item: dict[str, list[dict]] = {}
    classes: dict[str, str | None] = {}
    for h in rows:
        by_item.setdefault(h["item_ref"], []).append(h)
        classes[h["item_ref"]] = h["product_class"]

    per_class: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter
    )
    per_row: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter
    )
    for ref, holdings in by_item.items():
        cls = classes[ref] or "(no class)"
        per_class[cls]["items"] += 1
        for r in _card_rows(classes[ref], holdings, today=today):
            per_row[r["row"]][r["state"]] += 1
            if r["row"] == ROW_DOC:
                per_class[cls][r["state"]] += 1

    ordered = [c for c in _CLASS_ORDER if c in per_class]
    ordered += sorted(c for c in per_class if c not in _CLASS_ORDER)
    return {
        "as_of": today,
        "headline": headline,
        "classes": ordered,
        "per_class": per_class,
        "per_row": per_row,
    }


def _fmt(n: int, sl: bool) -> str:
    return f"{n:,}".replace(",", "." if sl else ",")


def render(m: dict, *, markdown: bool, sl: bool) -> str:
    h = m["headline"]
    out: list[str] = []
    w = out.append
    w(f"# Report figures — {m['as_of'].isoformat()}")
    w("")
    w(f"articles mirrored          {_fmt(h['items_mirrored'], sl)}")
    w(f"of those medical devices   {_fmt(h['md_items'], sl)}")
    w(f"documents                  {_fmt(h['documents'], sl)}"
      f"  ({h['production_docs']} production, {h['staged_docs']} staged)")
    w(f"evidence values            {_fmt(h['evidence_rows'], sl)}")
    w(f"item-document links        {_fmt(h['links'], sl)}")
    w(f"open manual tasks          {h['open_manual']}")
    w("")

    cols = [_STATE_LABEL[s] for s in _STATES]
    if markdown:
        w("| Class | Articles | " + " | ".join(c.capitalize() for c in cols) + " |")
        w("|---|---:|" + "---:|" * len(cols))
    else:
        w(f"{'class':<12}{'items':>9}" + "".join(f"{c:>13}" for c in cols))

    totals = collections.Counter()
    total_items = 0
    for cls in m["classes"]:
        c = m["per_class"][cls]
        total_items += c["items"]
        for s in _STATES:
            totals[s] += c[s]
        vals = [_fmt(c[s], sl) for s in _STATES]
        if markdown:
            w(f"| {cls} | {_fmt(c['items'], sl)} | " + " | ".join(vals) + " |")
        else:
            w(f"{cls:<12}{_fmt(c['items'], sl):>9}" + "".join(f"{v:>13}" for v in vals))

    vals = [_fmt(totals[s], sl) for s in _STATES]
    if markdown:
        w(f"| **Total** | **{_fmt(total_items, sl)}** | "
          + " | ".join(f"**{v}**" for v in vals) + " |")
    else:
        w(f"{'TOTAL':<12}{_fmt(total_items, sl):>9}"
          + "".join(f"{v:>13}" for v in vals))

    w("")
    w("Declaration is the column above. The other two scored rows:")
    for row in (ROW_IFU, ROW_NB):
        counts = m["per_row"][row]
        w(f"  {row:<10} " + ", ".join(
            f"{state} {_fmt(n, sl)}" for state, n in counts.most_common()
        ))
    w("")
    w("Then edit BOTH languages of docs/2026-08-24-what-the-system-does-*.md and")
    w("republish the artifact — its numbers are hardcoded HTML, not a live query.")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dsn", default=DEFAULT_DSN)
    ap.add_argument("--markdown", action="store_true",
                    help="emit the class table as a markdown table")
    ap.add_argument("--sl", action="store_true",
                    help="Slovene thousands separator (1.594, not 1,594)")
    ap.add_argument("--as-of", default=None, metavar="YYYY-MM-DD",
                    help="measure expiry against this date instead of today")
    args = ap.parse_args()
    today = date.fromisoformat(args.as_of) if args.as_of else date.today()
    print(render(measure(args.dsn, today), markdown=args.markdown, sl=args.sl))


if __name__ == "__main__":
    main()
