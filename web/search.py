"""`GET /api/search?q=` -- the webstore's query box (2026-11-23 webshop API
spec § 5.10, Denis 2026-11-24).

One string, matched against what a dentist might type: Dentalia's item number,
the supplier's article number, a Basic UDI-DI printed on a document, the item's
name, or the manufacturer's. Answers with the matching ITEMS and their
documents, and separately with matching MANUFACTURERS (never their items: one
manufacturer can have 2,581).

Only items that have at least one document the caller may be shown come back,
at most 20, and a query needs two characters. Together that keeps search from
being a way to list the catalogue, which is Dentalia's supplier map
(`web/catalogue.py`).

BC items carry no UDI (0 of 20,405, measured 2026-11-24), so a UDI matches the
`basic_udi_di` read off a document and leads to the items that document is
current for.
"""

from __future__ import annotations

import re

from fastapi import HTTPException, Request

from web import access
from web.catalogue import shop_manufacturers
from web.item_docs import item_documents, local_today

MIN_QUERY = 2
MAX_QUERY = 100
MAX_ITEMS = 20
# Fuzzy name candidates read before the "has a document" filter; most items
# have none, so the 20 shown need a wider net than 20.
NAME_CANDIDATES = 200

QUERY_REFUSAL = f"`q` must be {MIN_QUERY} to {MAX_QUERY} characters"


def _like(q: str) -> str:
    return "%" + re.sub(r"([%_\\])", r"\\\1", q) + "%"


def _candidates(conn, q: str) -> list[tuple[str, str]]:
    """`(item_ref, matched_on)` in match order, each item once, at its first
    (strongest) match."""
    out: dict[str, str] = {}

    def add(rows, how):
        for r in rows:
            out.setdefault(r["item_ref"], how)

    add(conn.execute("SELECT item_ref FROM item_mirror WHERE lower(item_ref) = lower(%s)",
                     (q,)).fetchall(), "item_ref")
    add(conn.execute("SELECT item_ref FROM item_mirror WHERE lower(mfr_ref) = lower(%s) "
                     "ORDER BY item_ref", (q,)).fetchall(), "mfr_ref")
    udi = re.sub(r"\s+", "", q)
    add(conn.execute(
        "SELECT DISTINCT ip.item_ref FROM document d "
        "JOIN item_document_production ip ON ip.doc_id = d.doc_id "
        "WHERE replace(d.basic_udi_di, ' ', '') = %s ORDER BY ip.item_ref",
        (udi,)).fetchall(), "basic_udi_di")
    # `%` (trigram similarity) and ILIKE both use `item_mirror_name_trgm`.
    add(conn.execute(
        "SELECT item_ref FROM item_mirror WHERE name %% %s OR name ILIKE %s "
        "ORDER BY similarity(name, %s) DESC, item_ref LIMIT %s",
        (q, _like(q), q, NAME_CANDIDATES)).fetchall(), "name")
    return list(out.items())


def search(conn, q: str, *, base_url: str = "", for_shop: bool = True) -> dict:
    """The search payload. Raises ValueError for a query out of bounds."""
    q = (q or "").strip()
    if not MIN_QUERY <= len(q) <= MAX_QUERY:
        raise ValueError(QUERY_REFUSAL)
    cands = _candidates(conn, q)
    # Items with no published link at all are dropped before any per-item
    # work; the expiry rule is then applied by `item_documents` itself.
    linked = {r["item_ref"] for r in conn.execute(
        "SELECT DISTINCT item_ref FROM item_document "
        "WHERE status = 'production' AND item_ref = ANY(%s)",
        ([ref for ref, _ in cands],)).fetchall()} if cands else set()
    items = []
    for ref, how in cands:
        if ref not in linked:
            continue
        out = item_documents(conn, ref, view="customer", base_url=base_url,
                             for_shop=for_shop)
        if out["documents"]:
            items.append({**out, "matched_on": how})
            if len(items) == MAX_ITEMS:
                break

    needle = _like(q)
    mfr_ids = {r["id"] for r in conn.execute(
        "SELECT m.id FROM manufacturer m WHERE m.canonical_name ILIKE %s UNION "
        "SELECT n.manufacturer_id FROM manufacturer_name n WHERE n.name ILIKE %s",
        (needle, needle)).fetchall()}
    manufacturers = ([m for m in shop_manufacturers(conn, local_today()) if m["id"] in mfr_ids]
                     if mfr_ids else [])
    manufacturers.sort(key=lambda m: (-m["items"], m["canonical_name"]))
    return {"query": q, "manufacturers": manufacturers, "items": items}


def register_routes(app, conn_factory, cfg) -> None:
    @app.get("/api/search")
    def api_search(request: Request, q: str = ""):
        for_shop = access.caller(request) == access.SERVICE
        with conn_factory() as conn:
            try:
                return search(conn, q, base_url=cfg.public_base_url, for_shop=for_shop)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
