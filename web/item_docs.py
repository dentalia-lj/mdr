"""The one item-documents query.

`/api/items/{item_ref}/documents` (the webshop's service call) and
`/item/{item_ref}` (the BC hyperlink) must never disagree about what an item's
paperwork is, so the query lives here once and both routes call it.

Two views. `full` is what a staff member or the webshop backend sees. `customer`
is what the webshop may render to a clinic: it drops our internal matching
vocabulary and the inferred expiry basis, because `staleness` -- a five-year
review horizon Dentalia chose, not a legal expiry -- reads as an expiry we
invented when it is printed next to a date on a customer-facing page
(spec §6, and `docs/specs/read-api.md` on `expiry_basis`).

Read-only, and the visibility rule is a view, never an ad hoc join here: a
document is visible for an item when BOTH the document and that item's link are
production (`item_document_production`). `include_superseded=True` swaps in
`item_document_history` (migration 070), which widens the DOCUMENT filter to
production+superseded and leaves the link filter alone -- the supersession
chain, for a consumer asking what covered this item before. Two views, one
question each; the choice between them is the only thing this module decides.
"""

from __future__ import annotations

# Named rather than inlined so the tests can assert the projection is complete
# without restating the list and drifting from it.
CUSTOMER_HIDDEN_ITEM_FIELDS = ("manufacturer_code",)
CUSTOMER_HIDDEN_DOC_FIELDS = ("match_basis", "expiry_basis")

#: Raised when `view="customer"` is combined with `include_superseded=True`.
#: One string so the function, the route's 400 body and the spec cannot word
#: this three ways.
CUSTOMER_HISTORY_REFUSAL = (
    "`include_superseded` is not available in the customer view: a superseded "
    "document is the paperwork we replaced and must never be rendered to a "
    "clinic as coverage."
)


def decorate_documents(conn, docs, *, view: str = "full", base_url: str = "") -> list:
    """Add `name` and `url` to production document rows, in place, and apply
    the view projection.

    Shared by the item endpoints and the manufacturer-level one so there is a
    single definition of what a document looks like to a consumer. A second
    copy of this is how two endpoints start disagreeing about a field.

    `docs` rows must already carry `doc_id`, `type`, `regulation`, `valid_to`.
    """
    # One batched query, not one per doc. DISTINCT ON ... ORDER BY extract_rev
    # DESC reads only the LATEST extraction revision (014) -- a re-extraction
    # must not leave the name stuck on a stale rev-1 value.
    mfr = {
        row["doc_id"]: row["value"]
        for row in conn.execute(
            "SELECT DISTINCT ON (doc_id) doc_id, value FROM evidence "
            "WHERE doc_id = ANY(%s) AND field = 'manufacturer' "
            "ORDER BY doc_id, extract_rev DESC",
            ([d["doc_id"] for d in docs],),
        ).fetchall()
    } if docs else {}

    for d in docs:
        parts = [mfr.get(d["doc_id"]), d["type"],
                 f"({d['regulation']})" if d["regulation"] not in (None, "n.a.") else None]
        name = " ".join(p for p in parts if p)
        d["name"] = f"{name}, valid to {d['valid_to']}" if d["valid_to"] else name
        # Constructed, never read from the database: `archive_url` is a storage
        # handle, not a link (2026-08-24 correction, docs/specs/read-api.md).
        d["url"] = f"{base_url}/documents/{d['doc_id']}/file"
        if view == "customer":
            for field in CUSTOMER_HIDDEN_DOC_FIELDS:
                d.pop(field, None)
    return docs


def item_documents(
    conn, item_ref: str, *, view: str = "full", base_url: str = "",
    include_superseded: bool = False,
) -> dict:
    """Production documents for one item, plus the item's own identity.

    `base_url` prefixes each document `url`. Empty (the default) leaves them
    relative, which is right for same-origin browser use and wrong for a
    webshop backend embedding our links in its own pages.

    `include_superseded` additionally returns the documents this item's current
    paperwork REPLACED, each carrying `status` and `superseded_by` so the chain
    is walkable. A separate flag rather than a `view` value for the reason
    `include_unlinked` is one on the manufacturer endpoint: `view` is the
    PROJECTION, this is the ROW SET, and one knob answers one question.

    Off by default, and the two extra fields appear only when it is on. For an
    unflagged caller `status` is always "production" and `superseded_by` always
    null -- constant fields that the batch endpoint would carry a hundred times
    a call while telling nobody anything.

    Refused outright in the customer view: see `CUSTOMER_HISTORY_REFUSAL`.
    """
    if include_superseded and view == "customer":
        raise ValueError(CUSTOMER_HISTORY_REFUSAL)
    item = conn.execute(
        "SELECT m.name, m.mfr_ref, m.manufacturer_raw AS manufacturer_code, "
        "       COALESCE(a.canonical_name, m.manufacturer_raw) AS manufacturer "
        "FROM item_mirror m "
        "LEFT JOIN manufacturer_alias a ON a.raw_name = m.manufacturer_raw "
        "WHERE m.item_ref = %s",
        (item_ref,),
    ).fetchone()
    # Same projection either way bar the two history fields, so the flagged and
    # unflagged rows cannot drift apart about what a document looks like.
    extra = (", doc_status AS status, superseded_by "
             if include_superseded else "")
    source = "item_document_history" if include_superseded else "item_document_production"
    docs = conn.execute(
        "SELECT doc_id, match_basis, type, regulation, "
        "       validity_from AS valid_from, expires AS valid_to, "
        "       expiry_basis "
        + extra +
        f"FROM {source} WHERE item_ref = %s ORDER BY doc_id",
        (item_ref,),
    ).fetchall()
    decorate_documents(conn, docs, view=view, base_url=base_url)

    out = {
        "item_ref": item_ref,
        "item_name": item["name"] if item else None,
        "manufacturer": item["manufacturer"] if item else None,
        "manufacturer_code": item["manufacturer_code"] if item else None,
        "mfr_ref": item["mfr_ref"] if item else None,
        "documents": docs,
    }
    if view == "customer":
        for field in CUSTOMER_HIDDEN_ITEM_FIELDS:
            out.pop(field, None)
    return out
