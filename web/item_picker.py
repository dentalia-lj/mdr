"""The Review item picker's reads (spec 2026-10-02-review-item-picker-design.md §4).

Read-only, on the web's `dentalia_api` connection. The candidate set is one
manufacturer's items, through `manufacturers.item_codes_for`; nothing outside
it is ever returned (§4.1). Lists are capped at CAP rows and count what they
did not show, so the reviewer is told to narrow the word rather than shown a
silently clipped list.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from app import manufacturers

CAP = 200
#: Measured on the server before the picker was built (spec §4.4; the plan's
#: "Before execution" step, figures in docs/state/2026-10-05-picker-thresholds.md).
SEARCH_THRESHOLD = 0.5
SIMILAR_THRESHOLD = 0.2
#: Items listed before and after a number match, in the manufacturer's
#: item-number order (§4.3): the same family for 83% of E.MAX and 50% of
#: Variobase numbers at ±10.
NEARBY = 10
#: A name's score against the query, punctuation ignored on the name's side:
#: "E.MAX" splits on its dot, so "emax" scored 0.40 against it, the same as
#: against "EMPRESS" (measured 2026-10-05). `%(q)s` is the query.
NAME_SCORE = ("greatest(word_similarity(%(q)s, m.name), word_similarity(%(q)s, "
              "regexp_replace(m.name, '[^[:alnum:][:space:]]', '', 'g')))")
DOC_TYPES = ("DoC", "EC", "IFU", "ISO")
#: The capped bases (PRD inv. 3): a link on one of these is a proposal.
WEAK_BASES = ("name-family", "fetch-context", "ref-catalogue")
#: Words that say what kind of document it is, not what it covers (§4.2).
STOP_WORDS = frozenset({
    "izjava", "skladnosti", "navodila", "uporabo", "uporaba", "certifikat", "novo",
    "declaration", "conformity", "instructions", "instruction", "certificate",
    "with", "from", "for", "the", "and", "use", "pdf", "eu", "ec", "mdr", "mdd",
})
WORDS_TRIED = 40    # first words of the file name and page 1 looked up at all
WORDS_SHOWN = 8
DOCS_SHOWN = 5

_WORD = re.compile(r"[^\W\d_]{4,}")
_HAS_WORD = re.compile(r"[^\W_]")
_HAS_LETTER = re.compile(r"[^\W\d_]")


@dataclass
class Row:
    item_ref: str
    name: str
    md_flag: bool | None
    group_id: int | None
    group_label: str | None
    score: float
    has: dict[str, bool] = field(default_factory=dict)
    same_type: dict | None = None
    refused: bool = False
    linked: bool = False
    proposed: str | None = None


@dataclass
class Group:
    group_id: int | None
    label: str
    rows: list[Row]


@dataclass
class Result:
    groups: list[Group]
    shown: int
    total: int
    capped: bool
    mode: str
    query: str = ""
    example: str = ""
    nearby: Group | None = None


_ROWS = """
SELECT m.item_ref, m.name, m.md_flag, g.group_id, g.label AS group_label,
       {score} AS score, count(*) OVER () AS total
  FROM item_mirror m
  LEFT JOIN LATERAL (
       SELECT ig.group_id, ig.label
         FROM item_group_member gm JOIN item_group ig ON ig.group_id = gm.group_id
        WHERE gm.item_ref = m.item_ref
        ORDER BY ig.group_id LIMIT 1) g ON true
 WHERE m.manufacturer_raw = ANY(%(codes)s) AND {where}
 ORDER BY score DESC, m.item_ref
 LIMIT %(cap)s
"""


def compare(theirs: date | None, ours: date | None) -> str:
    """How an item's document of this type stands against this one (§6)."""
    if theirs is None or ours is None:
        return "no date"
    if theirs < ours:
        return "older"
    if theirs > ours:
        return "newer"
    return "same date"


def _codes(conn, manufacturer: str | None) -> list[str]:
    return manufacturers.item_codes_for(conn, manufacturer) if manufacturer else []


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _run(conn, codes, score: str, where: str, params: dict, cap: int = CAP):
    """`score` and `where` are fixed SQL fragments from this module, never input."""
    rows = conn.execute(_ROWS.format(score=score, where=where),
                        {"codes": codes, "cap": cap, **params}).fetchall()
    total = rows[0]["total"] if rows else 0
    return [Row(item_ref=r["item_ref"], name=r["name"], md_flag=r["md_flag"],
                group_id=r["group_id"], group_label=r["group_label"],
                score=float(r["score"])) for r in rows], total


def _decorate(conn, doc: dict, rows: list[Row]) -> None:
    """Per row: which document types the item already has published, its newest
    published document of this one's type, and its link to this document."""
    refs = [r.item_ref for r in rows]
    if not refs:
        return
    held = defaultdict(list)
    for h in conn.execute(
            "SELECT l.item_ref, d.type, d.doc_id, d.validity_from "
            "FROM item_document l JOIN document d ON d.doc_id = l.doc_id "
            "WHERE l.item_ref = ANY(%s) AND l.status = 'production' "
            "AND d.status = 'production' AND d.doc_id <> %s",
            (refs, doc["doc_id"])).fetchall():
        held[h["item_ref"]].append(h)
    own = {r["item_ref"]: r for r in conn.execute(
        "SELECT item_ref, status, match_basis FROM item_document "
        "WHERE doc_id = %s AND item_ref = ANY(%s)", (doc["doc_id"], refs)).fetchall()}
    for r in rows:
        mine = held[r.item_ref]
        r.has = {t: any(h["type"] == t for h in mine) for t in DOC_TYPES}
        same = [h for h in mine if h["type"] == doc["type"]]
        if same:
            newest = max(same, key=lambda h: (h["validity_from"] is not None,
                                              h["validity_from"] or date.min, h["doc_id"]))
            r.same_type = {"doc_id": newest["doc_id"], "validity_from": newest["validity_from"],
                           "compare": compare(newest["validity_from"], doc["validity_from"])}
        link = own.get(r.item_ref)
        r.refused = bool(link and link["status"] == "rejected")
        r.linked = bool(link and link["status"] == "production")
        r.proposed = (link["match_basis"] if link and link["status"] == "staged"
                      and link["match_basis"] in WEAK_BASES else None)


def _result(conn, doc, rows, total, **meta) -> Result:
    _decorate(conn, doc, rows)
    groups: dict = {}
    for r in rows:
        if r.group_id not in groups:
            label = r.group_label or (f"Group {r.group_id}" if r.group_id else "No catalogue group")
            groups[r.group_id] = Group(r.group_id, label, [])
        groups[r.group_id].rows.append(r)
    return Result(list(groups.values()), len(rows), total, total > len(rows), **meta)


def _empty(mode: str, **meta) -> Result:
    return Result([], 0, 0, False, mode, **meta)


def search(conn, doc: dict, manufacturer: str | None, q: str) -> Result:
    """One box, an item number or a name (§4.3, ruled 2026-10-05).

    Item numbers first: those containing the text, those starting with it
    first, never fuzzy, plus the nearby numbers. Only when no number matches
    and the text has a letter: names, NAME_SCORE at or above SEARCH_THRESHOLD,
    closest first."""
    q = (q or "").strip()
    codes = _codes(conn, manufacturer)
    if not codes or not _HAS_WORD.search(q):
        return _empty("search", query=q)
    esc = _like_escape(q)
    rows, total = _run(conn, codes,
                       "CASE WHEN m.item_ref ILIKE %(prefix)s THEN 2.0 ELSE 1.0 END",
                       "m.item_ref ILIKE %(inside)s",
                       {"prefix": esc + "%", "inside": "%" + esc + "%"})
    if rows:
        result = _result(conn, doc, rows, total, mode="number", query=q)
        result.nearby = _nearby(conn, doc, codes, rows, q)
        return result
    if not _HAS_LETTER.search(q):
        return _empty("number", query=q)
    rows, total = _run(conn, codes, NAME_SCORE, f"{NAME_SCORE} >= %(t)s",
                       {"q": q, "t": SEARCH_THRESHOLD})
    return _result(conn, doc, rows, total, mode="search", query=q)


def _nearby(conn, doc: dict, codes: list[str], matched: list[Row], q: str) -> Group | None:
    """NEARBY items before the lowest and after the highest anchor, in the
    database's own `item_ref` order (the order the measurement used). The
    anchors are the matches whose number starts with the text, or the single
    match when only one contains it; scattered "contains" hits get no
    neighbours, which would mean nothing around them."""
    starts = [r.item_ref for r in matched if r.item_ref.lower().startswith(q.lower())]
    anchors = starts or ([matched[0].item_ref] if len(matched) == 1 else [])
    if not anchors:
        return None
    near = [r["item_ref"] for r in conn.execute(
        "WITH a AS (SELECT min(x) AS lo, max(x) AS hi FROM unnest(%(anchors)s::text[]) AS x) "
        "(SELECT m.item_ref FROM item_mirror m, a WHERE m.manufacturer_raw = ANY(%(codes)s) "
        "   AND m.item_ref < a.lo ORDER BY m.item_ref DESC LIMIT %(n)s) "
        "UNION ALL "
        "(SELECT m.item_ref FROM item_mirror m, a WHERE m.manufacturer_raw = ANY(%(codes)s) "
        "   AND m.item_ref > a.hi ORDER BY m.item_ref LIMIT %(n)s)",
        {"anchors": anchors, "codes": codes, "n": NEARBY}).fetchall()]
    shown = {r.item_ref for r in matched}
    near = [ref for ref in near if ref not in shown]
    if not near:
        return None
    rows, _ = _run(conn, codes, "1.0", "m.item_ref = ANY(%(refs)s)", {"refs": near},
                   cap=len(near))
    _decorate(conn, doc, rows)
    return Group(None, "Nearby item numbers", rows)


def similar(conn, doc: dict, manufacturer: str | None, item_ref: str) -> Result:
    """The manufacturer's items whose names are like `item_ref`'s (§4.4)."""
    codes = _codes(conn, manufacturer)
    example = conn.execute("SELECT name FROM item_mirror WHERE item_ref = %s AND "
                           "manufacturer_raw = ANY(%s)", (item_ref, codes)).fetchone()
    if not codes or example is None:
        return _empty("similar", example=item_ref)
    rows, total = _run(conn, codes, "similarity(m.name, %(name)s)",
                       "similarity(m.name, %(name)s) >= %(t)s AND m.item_ref <> %(ref)s",
                       {"name": example["name"], "t": SIMILAR_THRESHOLD, "ref": item_ref})
    return _result(conn, doc, rows, total, mode="similar", example=item_ref)


def items_of(conn, doc: dict, manufacturer: str | None, source_doc_id: int) -> Result:
    """The manufacturer's items a published document covers (§4.2, source 2)."""
    codes = _codes(conn, manufacturer)
    if not codes:
        return _empty("source")
    rows, total = _run(conn, codes, "1.0",
                       "m.item_ref IN (SELECT l.item_ref FROM item_document l "
                       "JOIN document d ON d.doc_id = l.doc_id WHERE l.doc_id = %(src)s "
                       "AND l.status = 'production' AND d.status = 'production')",
                       {"src": source_doc_id})
    return _result(conn, doc, rows, total, mode="source")


def proposed(conn, doc: dict, manufacturer: str | None) -> Result:
    """Items linked to this document only by a weak match (§4.2), never ticked."""
    codes = _codes(conn, manufacturer)
    if not codes:
        return _empty("proposed")
    rows, total = _run(conn, codes, "1.0",
                       "m.item_ref IN (SELECT item_ref FROM item_document WHERE "
                       "doc_id = %(doc_id)s AND status = 'staged' AND match_basis = ANY(%(weak)s))",
                       {"doc_id": doc["doc_id"], "weak": list(WEAK_BASES)})
    return _result(conn, doc, rows, total, mode="proposed")


def selected(conn, doc: dict, manufacturer: str | None, refs: list[str]) -> list[Row]:
    """The ticked items, decorated, in item order: what the panel summarises."""
    refs = sorted(set(refs))
    codes = _codes(conn, manufacturer)
    if not codes or not refs:
        return []
    rows, _ = _run(conn, codes, "1.0", "m.item_ref = ANY(%(refs)s)", {"refs": refs},
                   cap=len(refs))
    _decorate(conn, doc, rows)
    return sorted(rows, key=lambda r: r.item_ref)


def _words(text: str | None) -> list[str]:
    return [w.lower() for w in _WORD.findall(text or "")]


def suggestions(conn, doc: dict, manufacturer: str | None,
                name_of: Callable[[dict], str | None]) -> dict:
    """Chips on opening (§4.2): words from the file name and page 1 that match
    at least one of the manufacturer's items, and the manufacturer's published
    documents whose file name carries one of those words."""
    codes = _codes(conn, manufacturer)
    if not codes:
        return {"words": [], "docs": []}
    stop = STOP_WORDS | set(_words(manufacturer))
    text = conn.execute("SELECT content FROM document_text WHERE content_hash = %s",
                        (doc["content_hash"],)).fetchone()
    page1 = (text["content"] if text else "").split("[[page 2]]", 1)[0]
    tried: list[str] = []
    for w in _words(doc.get("file_name")) + _words(page1):
        if w not in stop and w not in tried:
            tried.append(w)
    tried = tried[:WORDS_TRIED]
    hits = {r["q"] for r in conn.execute(
        "SELECT q FROM unnest(%(tried)s::text[]) AS q WHERE EXISTS (SELECT 1 FROM item_mirror m "
        "WHERE m.manufacturer_raw = ANY(%(codes)s) AND "
        + NAME_SCORE.replace("%(q)s", "q") + " >= %(t)s)",
        {"tried": tried, "codes": codes, "t": SEARCH_THRESHOLD}).fetchall()} if tried else set()
    words = [w for w in tried if w in hits][:WORDS_SHOWN]
    docs = []
    if words:
        for d in conn.execute(
                "SELECT d.doc_id, d.source_url, d.archive_url, d.content_hash, "
                "       count(*) AS items "
                "  FROM document d JOIN item_document l ON l.doc_id = d.doc_id "
                "   AND l.status = 'production' "
                "  JOIN item_mirror m ON m.item_ref = l.item_ref "
                "   AND m.manufacturer_raw = ANY(%s) "
                " WHERE d.status = 'production' AND d.doc_id <> %s "
                " GROUP BY d.doc_id ORDER BY d.doc_id", (codes, doc["doc_id"])).fetchall():
            name = name_of(d) or ""
            if set(_words(name)) & set(words):
                docs.append({"doc_id": d["doc_id"], "file_name": name, "items": d["items"]})
            if len(docs) == DOCS_SHOWN:
                break
    return {"words": words, "docs": docs}
