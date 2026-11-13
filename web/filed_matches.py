"""Today's "Filed, but maybe ours" card (2026-11-13).

A filed document has no item link: VALIDATE compared the numbers it prints with
the Business Central numbers of its manufacturer and found none equal
(`no-ref-overlap`). VALIDATE compares exactly, unless the manufacturer's
playbook carries a `ref_normalize` rule (`app/handlers/validate.py`), so a
document printing `606.20.110` for BC's `60620110`, or `0541` for `6050541`,
is filed although it is very likely ours. Measured on the server 2026-11-13:
69 of 1,045 filed documents, 38 after the re-check of the same day.

This looks again, more loosely, and only SUGGESTS: it writes nothing, and the
person adds the item from the document page ("Find items this document
covers..."). Three kinds, strongest first:

* EXACT: the same number once spaces, dots and dashes are dropped and letters
  upper-cased;
* ZEROS: the same number once leading zeros are dropped as well;
* PREFIX: one number is the other with something in front (the printed side
  at least 4 characters, the BC side at least 5 when it is the shorter one).

A manufacturer's items are `manufacturers.item_codes_resolver`, the rule GATE
links with and the picker offers items with. Only the latest reading of the
document's numbers counts. Read-only, on the web's `dentalia_api` connection.

Cost, measured on the server 2026-11-13 (911 filed documents printing 16,152
numbers, 12,391 items of their manufacturers): about 130 ms per call, most of
it reading the items (48 ms) and indexing them (37 ms). Link checks take 25 ms.
If Today slows, load this card after the page (htmx `load`) before anything
cleverer.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass

from app import manufacturers

EXACT, ZEROS, PREFIX = "exact", "zeros", "prefix"
#: What the card calls each kind: the full name (the key above the table and
#: the legend) and the short one (the label on a row). Order is the card's.
WORDS = {
    EXACT: ("Same number in Business Central", "Same number"),
    PREFIX: ("Number differs by a prefix", "Prefix"),
    ZEROS: ("Number differs by leading zeros", "Leading zeros"),
}
_ORDER = {EXACT: 0, ZEROS: 1, PREFIX: 2}
_MIN_PRINTED = 4     # printed side, when it is the shorter one of a prefix pair
_MIN_BC = 5          # BC side, when it is the shorter one

_FILED = """
SELECT d.doc_id, d.type, d.canonical_manufacturer AS manufacturer, ev.value
  FROM document d
  JOIN LATERAL (SELECT value FROM evidence e
                 WHERE e.doc_id = d.doc_id AND e.field = 'ref_list'
                 ORDER BY e.extract_rev DESC, e.id DESC LIMIT 1) ev ON true
 WHERE d.status = 'filed' AND d.canonical_manufacturer IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM item_document l WHERE l.doc_id = d.doc_id)
"""

# The keys are `_key` in SQL (one pass in Postgres instead of 25k regex calls
# in Python); `test_the_sql_key_is_key` holds the two to the same fold.
_ITEMS = """
SELECT item_ref, name, manufacturer_raw AS code,
       upper(regexp_replace(item_ref, '[^0-9A-Za-z]', '', 'g')) AS k_item,
       upper(regexp_replace(coalesce(mfr_ref, ''), '[^0-9A-Za-z]', '', 'g')) AS k_mfr
  FROM item_mirror WHERE manufacturer_raw = ANY(%s)
"""


def _key(s: str | None) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", s or "").upper()


def _kind(p: str, b: str) -> str | None:
    """Two normalised numbers."""
    if not p or not b:
        return None
    if p == b:
        return EXACT
    pz, bz = p.lstrip("0"), b.lstrip("0")
    if pz and pz == bz:
        return ZEROS
    if len(p) >= _MIN_PRINTED and b.endswith(p):
        return PREFIX
    if len(b) >= _MIN_BC and p.endswith(b):
        return PREFIX
    return None


def classify(printed: str, item_ref: str, mfr_ref: str | None = None) -> str | None:
    """The strongest kind by which `printed` matches an item's own number or
    its manufacturer's number, or None."""
    p = _key(printed)
    kinds = [k for k in (_kind(p, _key(item_ref)), _kind(p, _key(mfr_ref))) if k]
    return min(kinds, key=_ORDER.get) if kinds else None


@dataclass
class Row:
    doc_id: int
    doc_type: str
    manufacturer: str
    kind: str
    printed: str        # as the document prints it
    item_ref: str
    item_name: str | None
    more: int           # other items matched by the same kind


@dataclass
class Found:
    rows: list[Row]
    counts: dict[str, int]

    @property
    def total(self) -> int:
        return len(self.rows)


class _Index:
    """One manufacturer's items, looked up by normalised number, so a document
    costs a few dictionary reads per printed number, not one comparison per
    item. Gives the same answer as `classify` over every item (pinned by
    `test_the_index_agrees_with_classify`)."""

    def __init__(self, items: list[dict], printed: set[str] | None = None):
        """`printed`: the normalised numbers that will be looked up, when known.
        A BC number's tails are then kept only where one of them is printed,
        which halves the build (77 -> 37 ms over the server's 12,391 items,
        2026-11-13); None keeps every tail."""
        self.names: dict[str, str | None] = {}
        self.exact: dict[str, set[str]] = defaultdict(set)
        self.zeros: dict[str, set[str]] = defaultdict(set)
        self.suffix: dict[str, set[str]] = defaultdict(set)
        for it in items:
            ref = it["item_ref"]
            self.names[ref] = it["name"]
            for b in (it["k_item"], it["k_mfr"]):
                if not b:
                    continue
                self.exact[b].add(ref)
                bz = b.lstrip("0")
                if bz:
                    self.zeros[bz].add(ref)
                for i in range(1, len(b) - _MIN_PRINTED + 1):
                    tail = b[i:]
                    if printed is None or tail in printed:
                        self.suffix[tail].add(ref)

    def match(self, p: str) -> list[tuple[str, str]]:
        """(item_ref, kind) for every item `p` matches, strongest kind per item."""
        if not p:
            return []
        out: dict[str, str] = {}

        def put(refs, kind):
            for r in refs:
                if r not in out or _ORDER[kind] < _ORDER[out[r]]:
                    out[r] = kind

        put(self.exact.get(p, ()), EXACT)
        if p.lstrip("0"):
            put(self.zeros.get(p.lstrip("0"), ()), ZEROS)
        if len(p) >= _MIN_PRINTED:
            put(self.suffix.get(p, ()), PREFIX)
        for i in range(1, len(p) - _MIN_BC + 1):
            put(self.exact.get(p[i:], ()), PREFIX)
        return list(out.items())


def _refs(value: str) -> list[str]:
    try:
        refs = json.loads(value)
    except (TypeError, ValueError):
        return []
    return [r for r in refs if isinstance(r, str)] if isinstance(refs, list) else []


def find(conn) -> Found:
    docs = [(r, _refs(r["value"])) for r in conn.execute(_FILED).fetchall()]
    docs = [(r, refs) for r, refs in docs if refs]
    codes_for = manufacturers.item_codes_resolver(conn)
    codes = {m: set(codes_for(m)) for m in {r["manufacturer"] for r, _ in docs}}
    items: dict[str, list[dict]] = defaultdict(list)
    wanted = sorted(set().union(*codes.values())) if codes else []
    for it in conn.execute(_ITEMS, (wanted,)).fetchall() if wanted else []:
        items[it["code"]].append(it)

    printed_by: dict[str, set[str]] = defaultdict(set)
    for d, refs in docs:
        printed_by[d["manufacturer"]].update(_key(r) for r in refs)
    index: dict[str, _Index] = {}
    rows: list[Row] = []
    for d, refs in docs:
        idx = index.get(d["manufacturer"])
        if idx is None:
            idx = index[d["manufacturer"]] = _Index(
                [it for c in codes[d["manufacturer"]] for it in items.get(c, ())],
                printed_by[d["manufacturer"]])
        best_of: dict[str, tuple[int, str]] = {}      # item_ref -> (order, printed)
        for printed in refs:
            for item_ref, kind in idx.match(_key(printed)):
                cand = (_ORDER[kind], printed)
                if item_ref not in best_of or cand < best_of[item_ref]:
                    best_of[item_ref] = cand
        if not best_of:
            continue
        top = min(o for o, _ in best_of.values())
        same = sorted(i for i, (o, _) in best_of.items() if o == top)
        kind = next(k for k, o in _ORDER.items() if o == top)
        rows.append(Row(doc_id=d["doc_id"], doc_type=d["type"],
                        manufacturer=d["manufacturer"], kind=kind,
                        printed=best_of[same[0]][1], item_ref=same[0],
                        item_name=idx.names[same[0]], more=len(same) - 1))
    # Listed in the card's order (WORDS: same number, prefix, leading zeros,
    # the approved mockup), which is not `_ORDER`: that one only decides which
    # kind a document falls under when it matches several ways.
    shown = {k: i for i, k in enumerate(WORDS)}
    rows.sort(key=lambda r: (shown[r.kind], r.manufacturer, r.doc_id))
    counts = {k: sum(1 for r in rows if r.kind == k) for k in (EXACT, PREFIX, ZEROS)}
    return Found(rows=rows, counts=counts)
