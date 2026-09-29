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

import shlex
from collections import Counter
from dataclasses import dataclass, field

from app import manufacturers, playbooks, vendor_master
from app.manufacturers import normalize

#: "Article numbers" evidence needs at least this many of the documents' REFs
#: held by one manufacturer and by NO other...
STRONG_MIN_REFS = 3
#: ...and those exclusive REFs making up at least this share of every REF the
#: catalogue knows at all. A REF two manufacturers hold counts toward the share's
#: denominator and toward nobody's numerator, so a declaration whose articles
#: are shared, or spread across manufacturers, never qualifies -- the case
#: VALIDATE's own unscoped path refuses as `multi-manufacturer-ref`.
STRONG_SHARE = 0.8
#: Below this share of the listed REFs being in the catalogue at all, the card
#: says so: a distributor's declaration lists many manufacturers' articles, and
#: Dentalia may stock only a few of one of them.
FEW_KNOWN_SHARE = 0.5

# The waiting documents: staged or filed, whose LATEST validate.doc flagged the
# manufacturer unresolved, outside any catalogue group. The latest job, not the
# review task: GATE never refreshes an open task's payload (`gate._push_manual`
# returns when one is open), so a task keeps saying `manufacturer-unresolved`
# after a re-validation that resolved the name. The printed name and the REFs
# come from the latest extraction, which is what that validation read.
# One scan of validate.doc jobs (DISTINCT ON), not a lookup per document.
_WAITING_SQL = """
WITH lastv AS (
    SELECT DISTINCT ON (j.payload->>'content_hash')
           j.payload->>'content_hash' AS content_hash,
           j.payload->>'group_id'     AS group_id,
           j.result
      FROM job j
     WHERE j.type = 'validate.doc'
     ORDER BY j.payload->>'content_hash', j.id DESC
)
SELECT d.doc_id, d.type, d.status,
       a.fields->'manufacturer'->>'value' AS printed,
       a.fields->'ref_list'->'value'     AS refs
  FROM document d
  JOIN lastv v ON v.content_hash = d.content_hash
  JOIN LATERAL (SELECT fields FROM extraction_attempt x
                 WHERE x.content_hash = d.content_hash
                 ORDER BY x.extract_rev DESC, x.id DESC LIMIT 1) a ON true
 WHERE d.status IN ('staged', 'filed')
   AND v.group_id IS NULL
   AND COALESCE(v.result->'flags' ? 'manufacturer-unresolved', false)
 ORDER BY d.doc_id
"""

# Every catalogue member carrying any of the REFs, as `mfr_ref` OR `item_ref`
# -- the two columns and the raw comparison of VALIDATE's REF-holder guard
# (`validate._manufacturers_holding_refs`), in its `= ANY` shape so one scan
# serves every name on the page.
_HOLDERS_SQL = """
SELECT m.mfr_ref, m.item_ref, g.canonical_manufacturer AS mfr
  FROM item_group_member m JOIN item_group g USING (group_id)
 WHERE m.mfr_ref = ANY(%s) OR m.item_ref = ANY(%s)
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
    held: int = 0                   # REFs only the suggested manufacturer holds
    matched: int = 0                # REFs the catalogue knows at all
    listed: int = 0                 # eligible REFs on the documents
    shared: int = 0                 # matched REFs more than one manufacturer holds
    has_items: bool = True          # 'alias': does the manufacturer have catalogue groups

    @property
    def few_known(self) -> bool:
        return bool(self.listed) and self.matched < FEW_KNOWN_SHARE * self.listed


@dataclass
class NameGroup:
    printed: str                    # the most common spelling
    docs: list[dict] = field(default_factory=list)
    suggestion: Suggestion = field(default_factory=lambda: Suggestion("none"))

    @property
    def waiting(self) -> int:
        return len(self.docs)

    @property
    def shell_name(self) -> str:
        """The name quoted for a shell, for the command the card shows."""
        return shlex.quote(self.printed)


@dataclass
class Found:
    groups: list[NameGroup]
    unnamed: int = 0                # waiting documents whose printed name is blank


def _slug(conn, canonical: str) -> str | None:
    row = conn.execute(
        "SELECT slug FROM manufacturer WHERE canonical_name = %s", (canonical,)).fetchone()
    return row["slug"] if row else None


def _holders(conn, refs: set[str]) -> dict[str, set[str]]:
    """REF -> every manufacturer whose catalogue carries it."""
    out: dict[str, set[str]] = {}
    if not refs:
        return out
    arr = sorted(refs)
    for r in conn.execute(_HOLDERS_SQL, (arr, arr)).fetchall():
        for ref in (r["mfr_ref"], r["item_ref"]):
            if ref in refs:
                out.setdefault(ref, set()).add(r["mfr"])
    return out


def _by_alias(conn, printed: str, grouped: set[str]) -> Suggestion | None:
    """The name already resolves: the alias exists but these documents were
    validated before it did, so they need re-validating, not another alias.
    Unless the manufacturer has no catalogue groups at all -- then VALIDATE
    flags the name unresolved however often it runs, and the card says so."""
    found = manufacturers.resolve_canonicals(conn, printed)
    if len(found) != 1:
        return None
    return Suggestion("alias", found[0], _slug(conn, found[0]),
                      has_items=normalize(found[0]) in grouped)


def _by_refs(conn, refs: list[str], holders: dict[str, set[str]]) -> Suggestion:
    """Always returns the counts; the level is 'refs' only when they qualify."""
    matched = [r for r in refs if r in holders]
    shared = sum(1 for r in matched if len(holders[r]) > 1)
    exclusive = Counter(next(iter(holders[r])) for r in matched if len(holders[r]) == 1)
    s = Suggestion("none", matched=len(matched), listed=len(refs), shared=shared)
    if not exclusive:
        return s
    # Highest count wins; ties broken by name so the page never flickers.
    top, held = min(exclusive.items(), key=lambda kv: (-kv[1], kv[0]))
    s.held = held
    if held >= STRONG_MIN_REFS and held >= STRONG_SHARE * len(matched):
        s.level, s.manufacturer, s.slug = "refs", top, _slug(conn, top)
    return s


def _by_name(conn, printed: str, brands) -> tuple[str, str | None] | None:
    """A BC brand inside the printed name, as (canonical, slug). A hint, never
    proof: HAGER's declarations carry no article numbers at all, so a name is
    all there is -- and a distributor's name (HENRY SCHEIN) is a BC brand too."""
    hit = playbooks.brand_collision(printed, brands, claimed=frozenset())
    if not hit:
        return None
    _master, codes = hit
    row = conn.execute(
        "SELECT m.canonical_name, m.slug FROM manufacturer_bc_code c "
        "JOIN manufacturer m ON m.id = c.manufacturer_id "
        "WHERE c.code = ANY(%s) ORDER BY m.canonical_name LIMIT 1", (list(codes),)).fetchone()
    return (row["canonical_name"], row["slug"]) if row else None


def find(conn, *, min_ref_len: int) -> Found:
    """Every printed name waiting on Review for want of an alias, most
    documents first, each with its suggestion."""
    by_key: dict[str, list[dict]] = {}
    unnamed = 0
    for r in conn.execute(_WAITING_SQL).fetchall():
        key = normalize(r["printed"])
        if not key:
            unnamed += 1
            continue
        by_key.setdefault(key, []).append(dict(r))
    if not by_key:
        return Found([], unnamed)

    refs_by_key = {k: sorted({ref for d in docs for ref in eligible_refs(d["refs"], min_ref_len)})
                   for k, docs in by_key.items()}
    holders = _holders(conn, {ref for refs in refs_by_key.values() for ref in refs})
    # Sorted, so a name containing two brands always gets the same hint.
    brands = dict(sorted(vendor_master.brand_index(conn).items()))
    grouped = {normalize(r["canonical_manufacturer"]) for r in conn.execute(
        "SELECT DISTINCT canonical_manufacturer FROM item_group").fetchall()}

    out: list[NameGroup] = []
    for key, docs in by_key.items():
        printed = Counter(d["printed"] for d in docs).most_common(1)[0][0]
        counts = _by_refs(conn, refs_by_key[key], holders)
        suggestion = _by_alias(conn, printed, grouped)
        if suggestion is None:
            suggestion = counts
            if counts.level != "refs":
                named = _by_name(conn, printed, brands)
                if named:
                    suggestion.level = "name"
                    suggestion.manufacturer, suggestion.slug = named
        for attr in ("matched", "listed", "shared"):
            setattr(suggestion, attr, getattr(counts, attr))
        if suggestion.level != "refs":
            suggestion.held = 0
        out.append(NameGroup(printed, [
            {"doc_id": d["doc_id"], "type": d["type"], "status": d["status"]} for d in docs
        ], suggestion))
    out.sort(key=lambda g: (-g.waiting, normalize(g.printed)))
    return Found(out, unnamed)


def summary(found: Found) -> dict:
    """The numbers the weekly report states."""
    gs = found.groups
    return {
        "names": len(gs),
        "documents": sum(g.waiting for g in gs),
        "by_refs": sum(1 for g in gs if g.suggestion.level == "refs"),
        "alias_exists": sum(1 for g in gs if g.suggestion.level == "alias"),
        "unnamed": found.unnamed,
    }
