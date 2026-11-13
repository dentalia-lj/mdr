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
