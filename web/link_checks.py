"""Today's link checks: every published link against Business Central (2026-11-12).

Read-only, on the web's `dentalia_api` connection, and cheap enough to run on
every load (25 ms over 14.384 published links, measured on the server). Two
questions, both answered with 0 on the day this was written:

* a published link whose item carries a BC manufacturer code the document's
  manufacturer does not own. "Owns" is `manufacturers.item_codes_resolver`,
  the rule GATE links with and the picker offers items with, so this check and
  the writers cannot disagree about what a manufacturer's items are;
* a published link under a document with no confirmed manufacturer.

What it cannot see is the right brand reached by the wrong maker (doc 165, a
Sure Dent certificate bound to Komet's range): the brand code is Komet's on
every one of those items. That needs BC's per-item maker code, not mirrored yet.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from app import manufacturers

_LINKS = """
SELECT d.doc_id, d.type, d.canonical_manufacturer AS manufacturer,
       i.manufacturer_raw AS code, count(*) AS n
  FROM item_document l
  JOIN document d USING (doc_id)
  JOIN item_mirror i USING (item_ref)
 WHERE l.status = 'production' AND d.status IN ('production', 'superseded')
 GROUP BY 1, 2, 3, 4
"""


@dataclass
class Finding:
    doc_id: int
    doc_type: str
    manufacturer: str | None
    links: int = 0
    #: (another manufacturer's name, how many of its items), most first.
    others: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class Checks:
    published: int
    crossing: list[Finding]
    crossing_links: int
    unowned: list[Finding]
    unowned_links: int

    @property
    def problems(self) -> int:
        return self.crossing_links + self.unowned_links


def _sorted(found: dict[int, Finding]) -> list[Finding]:
    return sorted(found.values(), key=lambda f: (-f.links, f.doc_id))


def check(conn) -> Checks:
    codes_for = manufacturers.item_codes_resolver(conn)
    name_of = {r["raw_name"]: r["canonical_name"] for r in conn.execute(
        "SELECT raw_name, canonical_name FROM manufacturer_alias")}
    owned: dict[str, set[str]] = {}
    crossing: dict[int, Finding] = {}
    unowned: dict[int, Finding] = {}
    others: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    published = 0
    for r in conn.execute(_LINKS).fetchall():
        published += r["n"]
        mfr = r["manufacturer"]
        if mfr is None:
            f = unowned.setdefault(r["doc_id"], Finding(r["doc_id"], r["type"], None))
            f.links += r["n"]
            continue
        if mfr not in owned:
            owned[mfr] = set(codes_for(mfr))
        if r["code"] in owned[mfr]:
            continue
        f = crossing.setdefault(r["doc_id"], Finding(r["doc_id"], r["type"], mfr))
        f.links += r["n"]
        others[r["doc_id"]][name_of.get(r["code"], r["code"])] += r["n"]
    for doc_id, by_name in others.items():
        crossing[doc_id].others = sorted(by_name.items(), key=lambda kv: (-kv[1], kv[0]))
    return Checks(published=published,
                  crossing=_sorted(crossing),
                  crossing_links=sum(f.links for f in crossing.values()),
                  unowned=_sorted(unowned),
                  unowned_links=sum(f.links for f in unowned.values()))
