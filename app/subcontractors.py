"""Who a listed subcontractor makes for (spec 2026-11-13-subcontractors-design.md).

A brand's playbook lists the companies that make products sold under it
(`Playbook.subcontractors`). A document such a company prints is never the
brand's own: it is never bound to the brand's whole range and never published
by itself. This module is the one place that answers "is this printed name a
listed subcontractor, and of which brands", so T0, VALIDATE, GATE and the web
cannot disagree about it.

Matching (spec § 4.2): a printed name matches an entry when the two fold equal
under `manufacturers.normalize`, or when the entry is one of the canonicals the
name resolves to through `manufacturer_alias` (an entry such as WOODPECKER, a
brand with its own playbook, is reached through that brand's legal names).

`playbooks=None` reads the configured playbook source (`load_playbooks()`).
"""
from __future__ import annotations

from app import manufacturers
from app import playbooks as playbooks_mod


def _loaded(playbooks):
    return playbooks_mod.load_playbooks() if playbooks is None else playbooks


def listing_brands(conn, printed: str | None, playbooks=None) -> list[str]:
    """The brands whose `subcontractors` list matches `printed`, sorted."""
    key = manufacturers.normalize(printed)
    if not key:
        return []
    resolved = {manufacturers.normalize(c)
                for c in manufacturers.resolve_canonicals(conn, printed)}
    out = set()
    for pb in _loaded(playbooks):
        for entry in pb.subcontractors:
            folded = manufacturers.normalize(entry)
            if folded == key or folded in resolved:
                out.add(pb.manufacturer)
                break
    return sorted(out)


def own_makers(conn, printed: str | None, playbooks=None) -> list[str]:
    """The canonicals `printed` resolves to that do not list it: the company as a
    maker in its own right ("SHOFU INC." -> SHOFU, although KOMET lists SHOFU).

    Empty for a pure subcontractor name such as Sure Dent, including while its
    old alias to KOMET still exists (code deployed before seed and sync): KOMET
    lists it, so KOMET is not Sure Dent's own."""
    listed = set(listing_brands(conn, printed, playbooks))
    return sorted({c for c in manufacturers.resolve_canonicals(conn, printed)
                   if c not in listed})


def subcontracted_for(conn, printed: str | None, brand: str | None,
                      playbooks=None) -> list[str]:
    """The brands for which this is a subcontractor's document, relative to the
    brand it reached (a group's manufacturer, or the stored one; None when it
    reached none). The one rule VALIDATE, GATE and Review share (final review
    2026-11-16, C1-C3):

    * it reached a brand that lists the printed name: that brand's subcontractor
      document, whoever else the name belongs to;
    * it reached another brand: not a subcontractor document for it (GATE's
      `issuer-not-brand` judges that case);
    * it reached no brand: a subcontractor document only when the name is no
      maker in its own right, so Shofu's own certificate stays Shofu's."""
    listed = listing_brands(conn, printed, playbooks)
    if not listed:
        return []
    if brand is not None:
        return listed if brand in listed else []
    return [] if own_makers(conn, printed, playbooks) else listed


def brand_for_document(conn, doc_id: int, playbooks=None) -> tuple[str, str] | None:
    """(brand, printed name) when the document's own readings name a listed
    subcontractor of exactly one brand, else None (2026-11-17, doc 390: Meta
    Biomed's declaration for Komet's BIOSEAL). The item picker starts on that
    brand instead of asking. T0's "playbook identity" does not count, as in
    `made_by`; a maker in its own right gets no brand (`subcontracted_for`)."""
    rows = conn.execute(
        "SELECT DISTINCT value FROM evidence WHERE doc_id = %s AND field = 'manufacturer' "
        "AND value IS NOT NULL "
        "AND NOT (tier = 'T0' AND coalesce(verbatim, '') LIKE 'playbook identity%%') "
        "ORDER BY value", (doc_id,)).fetchall()
    found: dict[str, str] = {}
    for r in rows:
        for brand in subcontracted_for(conn, r["value"], None, playbooks):
            found.setdefault(brand, r["value"])
    if len(found) != 1:
        return None
    return next(iter(found.items()))


def made_by(conn, item_ref: str, brand: str | None, playbooks=None) -> list[dict]:
    """Who makes `item_ref` for `brand`, read off its published documents
    (2026-11-16): one entry per listed subcontractor that printed a published
    document linked to the item, `{"name", "doc_ids"}`, in document order.
    [] for an ordinary item, so the item page shows nothing new.

    Only the documents' own readings count: T0's "playbook identity" names the
    brand whose name appears anywhere in the text (doc 165 carries both "Sure
    Dent Corporation" from T1 and "KOMET" from T0), so it would hide the maker.
    The rule is relative to the brand, as everywhere (`subcontracted_for`)."""
    if not brand:
        return []
    rows = conn.execute(
        "SELECT DISTINCT e.doc_id, e.value FROM item_document l "
        "JOIN document d ON d.doc_id = l.doc_id "
        "JOIN evidence e ON e.doc_id = d.doc_id AND e.field = 'manufacturer' "
        "WHERE l.item_ref = %s AND l.status = 'production' AND d.status = 'production' "
        "AND e.value IS NOT NULL "
        "AND NOT (e.tier = 'T0' AND coalesce(e.verbatim, '') LIKE 'playbook identity%%') "
        "ORDER BY e.doc_id, e.value", (item_ref,)).fetchall()
    makers: dict[str, dict] = {}
    for r in rows:
        if not subcontracted_for(conn, r["value"], brand, playbooks):
            continue
        m = makers.setdefault(manufacturers.normalize(r["value"]),
                              {"name": r["value"], "doc_ids": []})
        if r["doc_id"] not in m["doc_ids"]:
            m["doc_ids"].append(r["doc_id"])
    return list(makers.values())


def brands_listing(manufacturer: str | None, playbooks=None) -> list[str]:
    """The brands that list `manufacturer` itself (a canonical) as a subcontractor."""
    key = manufacturers.normalize(manufacturer)
    if not key:
        return []
    return sorted({pb.manufacturer for pb in _loaded(playbooks)
                   if any(manufacturers.normalize(e) == key for e in pb.subcontractors)})


def item_codes_with_listing_brands(conn, manufacturer: str | None,
                                   playbooks=None) -> list[str]:
    """`manufacturer`'s own BC codes plus those of every brand that lists it.

    What the picker, `check_items` and Today's link check treat as this
    manufacturer's items: a document of WOODPECKER may carry Komet's ENFILL."""
    if not manufacturer:
        return []
    codes = set(manufacturers.item_codes_for(conn, manufacturer))
    for brand in brands_listing(manufacturer, playbooks):
        codes.update(manufacturers.item_codes_for(conn, brand))
    return sorted(codes)


def moved_names(playbooks) -> set[str]:
    """The folded names that left `aliases` for a `subcontractors` list.

    Every entry that is not some playbook's own `manufacturer`. Seed and sync
    delete these from `manufacturer_name` and `manufacturer_alias`, because
    nothing else ever removes a name (spec F4)."""
    canonicals = {manufacturers.normalize(pb.manufacturer) for pb in playbooks}
    return {manufacturers.normalize(e) for pb in playbooks for e in pb.subcontractors
            if manufacturers.normalize(e) not in canonicals}


def bind_refusal(conn, doc_id: int, manufacturer: str | None) -> str | None:
    """Why a person may not bind this document to `manufacturer`'s whole range,
    or None. Any printed manufacturer on the document that `manufacturer` lists
    as a subcontractor refuses it (spec 2026-11-13 § 4.5 item 3). The web route
    asks the same question before enqueueing."""
    if not manufacturer:
        return None
    canonicals = manufacturers.resolve_canonicals(conn, manufacturer) or [manufacturer]
    for r in conn.execute("SELECT DISTINCT value FROM evidence WHERE doc_id=%s "
                          "AND field='manufacturer'", (doc_id,)).fetchall():
        listed_by = listing_brands(conn, r["value"])
        for brand in canonicals:
            if brand in listed_by:
                return (f"{r['value']} is a known subcontractor of {brand}: its certificate "
                        f"cannot cover all of {brand}. Change type to specific items, then "
                        f"tick the items {r['value']} makes.")
    return None
