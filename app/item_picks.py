"""The Review item picker's tick-set check (spec 2026-10-02 §5.1, §5.3).

One function, read-only, shared by the two places that must refuse a bad tick
set: the web route, which answers 422 before enqueueing, and GATE, which is the
last word against hand-built payloads and replays and raises before writing.
"""
from __future__ import annotations

from app import manufacturers


def check_items(conn, doc_id: int, manufacturer: str | None,
                refs: list[str]) -> dict[str, list[str]]:
    """What is wrong with ticking `refs` for `doc_id` under `manufacturer`.

    `unknown`: not in the catalogue mirror (never synced, or BC deleted it).
    `other-manufacturer`: not under any BC code the manufacturer fans out to
    (`item_codes_for`); a name with no codes owns nothing, so every item is
    this. `refused`: a person rejected this item's link to this document
    (C17); it comes back only through `reopen-link`. Empty dict when clean.
    """
    codes = set(manufacturers.item_codes_for(conn, manufacturer)) if manufacturer else set()
    rows = conn.execute(
        "SELECT t.item_ref, m.manufacturer_raw, l.status "
        "FROM unnest(%s::text[]) AS t(item_ref) "
        "LEFT JOIN item_mirror m ON m.item_ref = t.item_ref "
        "LEFT JOIN item_document l ON l.item_ref = t.item_ref AND l.doc_id = %s "
        "ORDER BY t.item_ref",
        (sorted(set(refs)), doc_id),
    ).fetchall()
    problems: dict[str, list[str]] = {}
    for r in rows:
        if r["manufacturer_raw"] is None:
            kind = "unknown"
        elif r["manufacturer_raw"] not in codes:
            kind = "other-manufacturer"
        elif r["status"] == "rejected":
            kind = "refused"
        else:
            continue
        problems.setdefault(kind, []).append(r["item_ref"])
    return problems
