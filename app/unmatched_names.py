"""Printed names without a manufacturer: which names need a playbook alias.

A document that prints a manufacturer name no alias knows is flagged
`manufacturer-unresolved` by VALIDATE and waits on Review. Nothing a reviewer
can press fixes it: the fix is an alias in the manufacturer's playbook, reviewed
in git, and until 2026-09-29 the only way to find which names needed one was by
hand. This module finds them, groups them by the name they print, and suggests
the manufacturer with the evidence for it.

Read-only. It writes nothing and suggests only; a person adds the alias.
Import-free of the extractor stack (no `app.handlers.validate`), because the web
image renders it on /data-quality and cannot import PyMuPDF.

Spec: docs/superpowers/specs/2026-09-29-unmatched-manufacturer-names-design.md.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from app import manufacturers, playbooks, vendor_master
from app.manufacturers import normalize

#: "Article numbers" evidence needs at least this many of the documents' REFs
#: held by one manufacturer...
STRONG_MIN_REFS = 3
#: ...and that manufacturer holding at least this share of the REFs the
#: catalogue knows at all. A REF held by two manufacturers counts for both, so a
#: distributor's mixed declaration never reaches the share.
STRONG_SHARE = 0.8

# The waiting documents and what VALIDATE saw when it flagged them. GATE copies
# the extraction into the review task (`tier_attempts`), so the printed name and
# the REF list come from the same values the flag was raised on.
_WAITING_SQL = """
SELECT t.doc_id, d.type, d.status,
       t.payload->'tier_attempts'->'manufacturer'->>'value' AS printed,
       t.payload->'tier_attempts'->'ref_list'->'value'     AS refs
  FROM manual_task t
  JOIN document d ON d.doc_id = t.doc_id
 WHERE t.status = 'open' AND t.kind = 'gate-manual'
   AND t.payload->'flags' ? 'manufacturer-unresolved'
   AND d.status IN ('staged', 'filed')
 ORDER BY t.doc_id
"""

# Per manufacturer, how many of `refs` its catalogue articles carry, as
# `mfr_ref` OR `item_ref` -- the two columns VALIDATE's own REF-holder guard
# searches (`validate._manufacturers_holding_refs`), raw comparison, same reason.
_HOLDERS_SQL = """
SELECT g.canonical_manufacturer AS mfr, count(DISTINCT r.ref) AS held
  FROM unnest(%s::text[]) AS r(ref)
  JOIN item_group_member m ON m.mfr_ref = r.ref OR m.item_ref = r.ref
  JOIN item_group g USING (group_id)
 GROUP BY g.canonical_manufacturer
"""

_MATCHED_SQL = """
SELECT count(DISTINCT r.ref) AS n
  FROM unnest(%s::text[]) AS r(ref)
 WHERE EXISTS (SELECT 1 FROM item_group_member m
                WHERE m.mfr_ref = r.ref OR m.item_ref = r.ref)
"""


def eligible_refs(ref_list, min_len: int) -> list[str]:
    """The REFs allowed to point at a manufacturer. The same two guards as
    `validate._eligible_unscoped_refs` (min length, no "!" prose), kept apart so
    the web image need not import VALIDATE; `tests/test_unmatched_names.py`
    holds the two equal."""
    out: list[str] = []
    for raw in ref_list or []:
        if not raw:
            continue
        ref = str(raw).strip()
        if len(ref) < min_len or "!" in ref:
            continue
        out.append(ref)
    return out


@dataclass
class Suggestion:
    level: str                      # 'alias' | 'refs' | 'name' | 'none'
    manufacturer: str | None = None
    slug: str | None = None
    held: int = 0                   # REFs the suggested manufacturer holds
    matched: int = 0                # REFs the catalogue knows at all
    listed: int = 0                 # eligible REFs on the documents


@dataclass
class NameGroup:
    printed: str                    # the most common spelling
    docs: list[dict] = field(default_factory=list)
    suggestion: Suggestion = field(default_factory=lambda: Suggestion("none"))

    @property
    def waiting(self) -> int:
        return len(self.docs)


def _slug(conn, canonical: str) -> str | None:
    row = conn.execute(
        "SELECT slug FROM manufacturer WHERE canonical_name = %s", (canonical,)).fetchone()
    return row["slug"] if row else None


def _by_alias(conn, printed: str) -> Suggestion | None:
    """The name already resolves: the alias was added after these documents
    were validated, so they need re-validating, not another alias. Found on
    the dev database 2026-09-29 (NEODENT's legal name, two declarations)."""
    found = manufacturers.resolve_canonicals(conn, printed)
    if len(found) != 1:
        return None
    return Suggestion("alias", found[0], _slug(conn, found[0]))


def _by_refs(conn, refs: list[str]) -> Suggestion | None:
    if not refs:
        return None
    counts = {r["mfr"]: r["held"] for r in conn.execute(_HOLDERS_SQL, (refs,)).fetchall()}
    if not counts:
        return None
    matched = conn.execute(_MATCHED_SQL, (refs,)).fetchone()["n"]
    # Highest count wins; ties broken by name so the page never flickers.
    top, held = min(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    if held < STRONG_MIN_REFS or held < STRONG_SHARE * matched:
        return None
    return Suggestion("refs", top, _slug(conn, top), held=held, matched=matched,
                      listed=len(refs))


def _by_name(conn, printed: str, brands) -> Suggestion | None:
    """A BC brand inside the printed name. A hint, never proof: HAGER's
    declarations carry no article numbers at all, so a name is all there is."""
    hit = playbooks.brand_collision(printed, brands, claimed=frozenset())
    if not hit:
        return None
    _master, codes = hit
    row = conn.execute(
        "SELECT m.canonical_name, m.slug FROM manufacturer_bc_code c "
        "JOIN manufacturer m ON m.id = c.manufacturer_id "
        "WHERE c.code = ANY(%s) ORDER BY m.canonical_name LIMIT 1", (list(codes),)).fetchone()
    if row is None:
        return None
    return Suggestion("name", row["canonical_name"], row["slug"])


def groups(conn, *, min_ref_len: int) -> list[NameGroup]:
    """Every printed name waiting on Review for want of an alias, most
    documents first, each with its suggestion."""
    by_key: dict[str, list[dict]] = {}
    for r in conn.execute(_WAITING_SQL).fetchall():
        key = normalize(r["printed"])
        if not key:
            continue
        by_key.setdefault(key, []).append(dict(r))
    if not by_key:
        return []
    brands = vendor_master.brand_index(conn)
    out: list[NameGroup] = []
    for docs in by_key.values():
        printed = Counter(d["printed"] for d in docs).most_common(1)[0][0]
        refs = sorted({ref for d in docs for ref in eligible_refs(d["refs"], min_ref_len)})
        suggestion = (_by_alias(conn, printed) or _by_refs(conn, refs)
                      or _by_name(conn, printed, brands) or Suggestion("none"))
        suggestion.listed = len(refs)
        out.append(NameGroup(printed, [
            {"doc_id": d["doc_id"], "type": d["type"], "status": d["status"]} for d in docs
        ], suggestion))
    out.sort(key=lambda g: (-g.waiting, normalize(g.printed)))
    return out


def summary(found: list[NameGroup]) -> dict:
    """The numbers the weekly report states."""
    return {
        "names": len(found),
        "documents": sum(g.waiting for g in found),
        "by_refs": sum(1 for g in found if g.suggestion.level == "refs"),
        "alias_exists": sum(1 for g in found if g.suggestion.level == "alias"),
    }
