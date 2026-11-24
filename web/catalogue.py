"""Manufacturer catalogue and order-batch endpoints (slice 2).

Service-API-shaped and never public. The webshop calls these server-side and
renders into its own pages under its own login; nothing here is reachable by a
clinic. That is not a detail -- a manufacturer->items listing IS Dentalia's
supplier map, and it is the single most sensitive thing this system holds.
Because these live under `/api/`, `web/access.py`'s existing policy already
covers them (service + staff, never `bc`, never anonymous) with no change.

Production-visibility only, via `item_document_production`. Deliberately does
NOT reuse `registry.manufacturer_detail`: that one shows staged and filed
documents too, because the UI's question is "why is this not published yet".
A consumer's question is "what may I hand a customer", which is a different
question with a different answer.

Pagination on the item list is mandatory, not a nicety: ~16k items mirrored
today, ~100k at target, and an uncapped list endpoint is a way to ask for the
whole catalogue in one request.
"""

from __future__ import annotations

from fastapi import HTTPException, Query, Request

from web import access

from web.item_docs import (
    CUSTOMER_HIDDEN_DOC_FIELDS,
    decorate_documents,
    is_legally_expired,
    item_documents,
    legal_expiry,
    local_today,
)

#: `include_unlinked` widens the row set to documents bound to the
#: manufacturer through no item, and that branch has no status filter. The
#: webstore key never gets it (2026-11-23 webshop API spec § 5.2).
SHOP_UNLINKED_REFUSAL = (
    "`include_unlinked` is not available to API keys: it returns documents "
    "that are not published for any item."
)

# ~16k items today, ~100k at target.
DEFAULT_PER_PAGE = 100
MAX_PER_PAGE = 500
# An order page renders one row per line. 100 lines is a generous order and a
# cheap ceiling; beyond it the caller wants the catalogue, not an order.
MAX_BATCH_REFS = 100


def _codes(conn, canonical_name: str) -> list[str]:
    return [r["raw_name"] for r in conn.execute(
        "SELECT raw_name FROM manufacturer_alias WHERE canonical_name = %s "
        "ORDER BY raw_name", (canonical_name,)).fetchall()]


def _resolve_id(conn, mfr_id: int) -> tuple[str, list[str]] | None:
    """`(current canonical name, BC codes)` for `manufacturer.id`, or None.

    Resolved through EVERY name the manufacturer has had, not only the
    current one (2026-11-23 webshop API spec § 5.3). The UI rename writes
    `manufacturer` and `manufacturer_name` (the old name demoted to an alias)
    but not `manufacturer_alias`, which keeps the old canonical name until the
    next `playbooks sync`; looking the codes up by the new name alone would
    404 the webstore's saved id in between."""
    row = conn.execute("SELECT canonical_name FROM manufacturer WHERE id=%s",
                       (mfr_id,)).fetchone()
    if row is None:
        return None
    codes = [r["raw_name"] for r in conn.execute(
        "SELECT DISTINCT a.raw_name FROM manufacturer_alias a "
        "WHERE a.canonical_name = %s OR a.canonical_name IN ("
        "  SELECT n.name FROM manufacturer_name n WHERE n.manufacturer_id = %s) "
        "ORDER BY a.raw_name", (row["canonical_name"], mfr_id)).fetchall()]
    return row["canonical_name"], codes


def _id_by_name(conn, canonical_name: str) -> int | None:
    """The stable id behind a name, current or former; None when the name has
    no `manufacturer` row (an alias-only spelling)."""
    row = conn.execute(
        "SELECT id FROM manufacturer WHERE canonical_name = %s UNION "
        "SELECT manufacturer_id FROM manufacturer_name WHERE name = %s LIMIT 1",
        (canonical_name, canonical_name)).fetchone()
    return row["id"] if row else None


def manufacturer_summary(conn, canonical_name: str, *, codes: list[str] | None = None
                         ) -> dict | None:
    """Identity plus PRODUCTION counts, or None if no such canonical name.
    `codes` given = already resolved by id (`_resolve_id`)."""
    codes = codes if codes is not None else _codes(conn, canonical_name)
    if not codes:
        return None
    row = conn.execute(
        "SELECT count(DISTINCT i.item_ref) AS items, "
        "       count(DISTINCT ip.doc_id)  AS documents "
        "FROM item_mirror i "
        "LEFT JOIN item_document_production ip ON ip.item_ref = i.item_ref "
        "WHERE i.manufacturer_raw = ANY(%s)", (codes,)).fetchone()
    return {
        "canonical_name": canonical_name,
        "codes": codes,
        "items": row["items"],
        "documents": row["documents"],
    }


def manufacturer_documents(conn, canonical_name: str, *, view: str = "full",
                           base_url: str = "", include_unlinked: bool = False,
                           for_shop: bool = False, codes: list[str] | None = None
                           ) -> list[dict] | None:
    """Every production document reachable from any item of this manufacturer,
    DEDUPED.

    The dedupe is the point of the endpoint. One ISO 13485 certificate can
    cover a whole product line, so listing per item would return the same
    document hundreds of times and bury the handful that differ.

    `include_unlinked` (053) additionally returns documents BOUND to this
    manufacturer that reach it through no item at all. That set is large and
    was entirely invisible here: this function reads `item_document_production`,
    so a document with no production link cannot appear, and measured
    2026-08-31 `/api/manufacturers/VOCO/documents` returned 32 of the 270
    documents held under that name; re-measured 2026-09-16, 32 of 283 -- the
    linked count is unchanged and the held count grew, which is what `filed`
    MEANS (C15: we stock nothing it covers, and this query's path runs through
    what we stock).

    It is off by default, deliberately. A filed document is real, held and
    evidenced, and explicitly NOT coverage; returning it unannounced would
    change what an existing consumer's payload means. A separate flag rather
    than a `view` value because `view` is the PROJECTION (full/customer) and
    this is the ROW SET -- one knob per question.
    """
    codes = codes if codes is not None else _codes(conn, canonical_name)
    if not codes:
        return None
    linked = (
        "SELECT ip.doc_id, ip.match_basis, ip.type, ip.regulation, "
        "       ip.validity_from AS valid_from, ip.expires AS valid_to, "
        "       ip.expiry_basis "
        "FROM item_document_production ip "
        "JOIN item_mirror i ON i.item_ref = ip.item_ref "
        "WHERE i.manufacturer_raw = ANY(%s) "
    )
    if not include_unlinked:
        docs = conn.execute(
            f"SELECT DISTINCT ON (doc_id) * FROM ({linked}) l ORDER BY doc_id",
            (codes,)).fetchall()
    else:
        # UNION, not UNION ALL: a document that is both bound AND linked --
        # every one of them once the backfill has run -- must appear once.
        # `expires`/`basis` come from `document_effective_expiry` because a
        # link-less document has no `item_document_production` row to read them
        # from, and that view is where the inheritance rule already lives.
        docs = conn.execute(
            "SELECT DISTINCT ON (doc_id) * FROM ("
            + linked +
            "  UNION "
            "  SELECT d.doc_id, 'mfr-bound' AS match_basis, d.type, d.regulation, "
            "         d.validity_from AS valid_from, e.expires AS valid_to, "
            "         e.basis AS expiry_basis "
            "  FROM document d "
            "  LEFT JOIN document_effective_expiry e ON e.doc_id = d.doc_id "
            "  WHERE d.canonical_manufacturer = %s "
            ") l ORDER BY doc_id",
            (codes, canonical_name)).fetchall()
    return decorate_documents(conn, docs, view=view, base_url=base_url,
                              for_shop=for_shop)


def manufacturer_items(conn, canonical_name: str, *, page: int, per_page: int,
                       codes: list[str] | None = None) -> dict | None:
    """One page of this manufacturer's items, each with its production
    document count."""
    codes = codes if codes is not None else _codes(conn, canonical_name)
    if not codes:
        return None
    total = conn.execute(
        "SELECT count(*) AS n FROM item_mirror WHERE manufacturer_raw = ANY(%s)",
        (codes,)).fetchone()["n"]
    rows = conn.execute(
        "SELECT i.item_ref, i.name AS item_name, i.mfr_ref, "
        "       count(ip.doc_id) AS documents "
        "FROM item_mirror i "
        "LEFT JOIN item_document_production ip ON ip.item_ref = i.item_ref "
        "WHERE i.manufacturer_raw = ANY(%s) "
        "GROUP BY i.item_ref, i.name, i.mfr_ref "
        "ORDER BY i.item_ref LIMIT %s OFFSET %s",
        (codes, per_page, (page - 1) * per_page)).fetchall()
    return {
        "canonical_name": canonical_name,
        "page": page,
        "per_page": per_page,
        "total": total,
        # ceil without importing math; 0 items is 0 pages, not 1.
        "pages": (total + per_page - 1) // per_page,
        "items": rows,
    }


# One row per `manufacturer.id`, its codes found through every name it has had
# (see `_resolve_id`), with the item count and the PUBLISHED document ids.
_SHOP_LIST_SQL = """
WITH names AS (
  SELECT m.id, m.canonical_name, m.canonical_name AS name FROM manufacturer m
  UNION
  SELECT m.id, m.canonical_name, n.name FROM manufacturer m
    JOIN manufacturer_name n ON n.manufacturer_id = m.id
), codes AS (
  SELECT DISTINCT nm.id, nm.canonical_name, a.raw_name
    FROM names nm JOIN manufacturer_alias a ON a.canonical_name = nm.name
)
SELECT c.id, c.canonical_name,
       count(DISTINCT i.item_ref) AS items,
       coalesce(array_agg(DISTINCT ip.doc_id) FILTER (WHERE ip.doc_id IS NOT NULL),
                '{}') AS doc_ids
  FROM codes c
  LEFT JOIN item_mirror i ON i.manufacturer_raw = c.raw_name
  LEFT JOIN item_document_production ip ON ip.item_ref = i.item_ref
 GROUP BY c.id, c.canonical_name
"""


def shop_manufacturers(conn, today) -> list[dict]:
    """The webstore's manufacturer tree (2026-11-23 webshop API spec § 5.3):
    `id`, name and counts, no BC codes, and only manufacturers with at least
    one document the webstore may show -- the same legal-expiry rule
    `decorate_documents(for_shop=True)` applies, so a manufacturer listed here
    never opens on an empty documents page."""
    rows = conn.execute(_SHOP_LIST_SQL).fetchall()
    legal = legal_expiry(conn, sorted({d for r in rows for d in r["doc_ids"]}))
    out = []
    for r in rows:
        visible = [d for d in r["doc_ids"]
                   if not is_legally_expired(legal.get(d), today)]
        if visible:
            out.append({"id": r["id"], "canonical_name": r["canonical_name"],
                        "items": r["items"], "documents": len(visible)})
    return out


def register_routes(app, conn_factory, cfg) -> None:
    from web import registry

    @app.get("/api/manufacturers")
    def api_manufacturers(request: Request, q: str = Query(default="")):
        needle = q.strip().lower()
        if access.caller(request) == access.SERVICE:
            with conn_factory() as conn:
                rows = shop_manufacturers(conn, local_today())
            if needle:
                rows = [r for r in rows if needle in r["canonical_name"].lower()]
            rows.sort(key=lambda r: (-r["items"], r["canonical_name"]))
            return {"manufacturers": rows, "total": len(rows)}
        with conn_factory() as conn:
            rows = registry.manufacturer_rows(conn)
            ids = {r["canonical_name"]: _id_by_name(conn, r["canonical_name"])
                   for r in rows}
        # `manufacturer_rows` already joins item_document_production, so its
        # `docs` count is production-only. Renamed to `documents` here to match
        # every other payload in this API.
        #
        # Filtered and sorted here rather than through
        # `registry.filter_and_sort`: that one ranks by whether a playbook has
        # been authored, which is a crawl-authoring concern belonging to the
        # UI. A consumer has no idea what a playbook is and should not have its
        # result order depend on one.
        if needle:
            rows = [r for r in rows
                    if needle in r["canonical_name"].lower()
                    or any(needle in c.lower() for c in r["codes"])]
        rows = sorted(rows, key=lambda r: (-r["items"], r["canonical_name"]))
        out = [{"id": ids.get(r["canonical_name"]),
                "canonical_name": r["canonical_name"], "codes": r["codes"],
                "items": r["items"], "documents": r["docs"]} for r in rows]
        return {"manufacturers": out, "total": len(out)}

    # ---- by stable id: the webstore's paths (spec § 5.3) -------------------
    def _by_id(conn, mfr_id: int) -> tuple[str, list[str]]:
        found = _resolve_id(conn, mfr_id)
        if found is None:
            raise HTTPException(status_code=404, detail="unknown manufacturer")
        return found

    def _documents(request, canonical_name, codes, view, include_unlinked):
        for_shop = access.caller(request) == access.SERVICE
        if for_shop and include_unlinked:
            raise HTTPException(status_code=400, detail=SHOP_UNLINKED_REFUSAL)
        with conn_factory() as conn:
            return manufacturer_documents(conn, canonical_name, view=view,
                                          base_url=cfg.public_base_url,
                                          include_unlinked=include_unlinked,
                                          for_shop=for_shop, codes=codes)

    def _summary(request, canonical_name, codes, mfr_id):
        with conn_factory() as conn:
            out = manufacturer_summary(conn, canonical_name, codes=codes)
            if out is not None and mfr_id is None:
                mfr_id = _id_by_name(conn, canonical_name)
        if out is None:
            raise HTTPException(status_code=404, detail="unknown manufacturer")
        out = {"id": mfr_id, **out}
        if access.caller(request) == access.SERVICE:
            out.pop("codes", None)
        return out

    @app.get("/api/manufacturers/{mfr_id:int}/documents")
    def api_manufacturer_documents(request: Request, mfr_id: int,
                                   view: str = "full",
                                   include_unlinked: bool = False):
        with conn_factory() as conn:
            name, codes = _by_id(conn, mfr_id)
        docs = _documents(request, name, codes, view, include_unlinked)
        return {"id": mfr_id, "canonical_name": name, "documents": docs or []}

    @app.get("/api/manufacturers/{mfr_id:int}/items")
    def api_manufacturer_items(mfr_id: int, page: int = 1,
                               per_page: int = DEFAULT_PER_PAGE):
        page = max(1, page)
        per_page = max(1, min(per_page, MAX_PER_PAGE))
        with conn_factory() as conn:
            name, codes = _by_id(conn, mfr_id)
            out = manufacturer_items(conn, name, page=page, per_page=per_page,
                                     codes=codes)
        if out is None:
            raise HTTPException(status_code=404, detail="unknown manufacturer")
        return {"id": mfr_id, **out}

    @app.get("/api/manufacturers/{mfr_id:int}")
    def api_manufacturer(request: Request, mfr_id: int):
        with conn_factory() as conn:
            name, codes = _by_id(conn, mfr_id)
        return _summary(request, name, codes, mfr_id)

    # ---- by name: staff only (access.SERVICE_ROUTES does not list them) ----
    # A canonical name can be a bare BC code like `077` (RESOLVE stores an
    # unmapped code as its own name), so "numeric means id" would be wrong the
    # day one appears; names get their own prefix instead.
    @app.get("/api/manufacturers/by-name/{canonical_name:path}/documents")
    def api_manufacturer_documents_by_name(request: Request, canonical_name: str,
                                           view: str = "full",
                                           include_unlinked: bool = False):
        docs = _documents(request, canonical_name, None, view, include_unlinked)
        if docs is None:
            raise HTTPException(status_code=404, detail="unknown manufacturer")
        with conn_factory() as conn:
            mfr_id = _id_by_name(conn, canonical_name)
        return {"id": mfr_id, "canonical_name": canonical_name, "documents": docs}

    @app.get("/api/manufacturers/by-name/{canonical_name:path}/items")
    def api_manufacturer_items_by_name(canonical_name: str, page: int = 1,
                                       per_page: int = DEFAULT_PER_PAGE):
        page = max(1, page)
        per_page = max(1, min(per_page, MAX_PER_PAGE))
        with conn_factory() as conn:
            out = manufacturer_items(conn, canonical_name, page=page,
                                     per_page=per_page)
            mfr_id = _id_by_name(conn, canonical_name) if out else None
        if out is None:
            raise HTTPException(status_code=404, detail="unknown manufacturer")
        return {"id": mfr_id, **out}

    @app.get("/api/manufacturers/by-name/{canonical_name:path}")
    def api_manufacturer_by_name(request: Request, canonical_name: str):
        return _summary(request, canonical_name, None, None)

    # Registered here rather than beside the single-item route so the whole of
    # slice 2 lives in one module. No collision with
    # `/api/items/{item_ref:path}/documents`: that pattern needs a segment
    # BETWEEN `items` and `documents`, and this path has none.
    @app.get("/api/items/documents")
    def api_items_documents_batch(
        request: Request, view: str = "full",
        ref: list[str] = Query(default=[]),
        include_superseded: bool = False,
    ):
        """Documents for several items in one call -- an order page.

        A webshop rendering 20 order lines should not make 20 round trips.
        Entries come back in the order asked for, and an unknown ref is kept
        as a null-identity entry rather than dropped: the caller is drawing a
        row per line and needs to know WHICH line has nothing, which a shorter
        array cannot tell it.

        `include_superseded` applies to every entry, for the same reason the
        endpoint exists: reconciling a page of old paperwork is one question,
        not one per line. The customer-view refusal is `item_docs`' rule, not
        restated here -- it is checked once per entry and the first ValueError
        answers for the call, which is correct because the flag is per-call.
        """
        if not ref:
            raise HTTPException(
                status_code=400, detail="at least one `ref` is required")
        if len(ref) > MAX_BATCH_REFS:
            raise HTTPException(
                status_code=400,
                detail=f"at most {MAX_BATCH_REFS} refs per call, got {len(ref)}")
        with conn_factory() as conn:
            try:
                items = [
                    item_documents(conn, r, view=view,
                                   base_url=cfg.public_base_url,
                                   include_superseded=include_superseded,
                                   for_shop=access.caller(request) == access.SERVICE)
                    for r in ref
                ]
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"items": items, "total": len(items)}
