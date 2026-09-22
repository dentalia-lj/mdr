"""What we assert into Dentalia's Business Central.

Three fields b-s.si made writable on the `dataitems` endpoint, filled from our
registry. This module is the rule and nothing else: registry rows in, values
out, no connection and no HTTP.

**Not `app/compliance.py`, on purpose.** That module answers *what does a
reviewer need to look at* and colours a card; this one answers *what do we
state as fact inside the client's own ERP*. Same inputs, different question,
and they disagree on two states by design — see `valid_doc`. Sharing one
function would force one of the two consumers to be wrong.

Ruled by Denis, 2026-09-07. Design and the regulatory grounding:
`docs/superpowers/specs/2026-09-07-bc-writeback-design.md`.
"""

from __future__ import annotations

from datetime import date
from urllib.parse import quote

__all__ = [
    "ADVERSE_CERT_STATUSES",
    "LEGAL_EXPIRY_BASES",
    "URL_MAX_LEN",
    "fields_for",
    "valid_ce",
    "valid_doc",
    "warehouse_url",
]

#: The two `document_effective_expiry.basis` values that can falsify a field.
#: `staleness` is deliberately absent -- it is Dentalia's own five-year review
#: horizon on a declaration that states no expiry, and migration 027 calls it
#: "a review horizon, not a legal expiry". `inherited` IS here: a declaration
#: citing a notified-body certificate stands on that certificate, and MDR
#: Art. 56 caps it at five years, so once it lapses the grounds are gone.
LEGAL_EXPIRY_BASES = ("stated", "inherited")

#: EUDAMED certificate statuses that falsify the CE field on their own. A
#: notified body can suspend, restrict, withdraw or cancel a certificate
#: without its expiry date moving, and the same four drive
#: `certificate_status_alert` (migration 043).
ADVERSE_CERT_STATUSES = ("withdrawn", "cancelled", "suspended", "restricted")

#: `pteWarehouseURL` is text[250] in Business Central.
URL_MAX_LEN = 250


def _lapsed(h, *, today: date) -> bool:
    """Has this holding lapsed on a basis that is a fact about the article?

    A date equal to today has not passed: the article is covered today, and
    today is what BC is being told about.
    """
    if h["expiry_basis"] not in LEGAL_EXPIRY_BASES:
        return False
    expires = h["expires"]
    return expires is not None and expires < today


def _covers(h, *, doc_type: str, today: date) -> bool:
    """The rule both fields share: a live document of this type, covering the
    article or a group it belongs to, still within its validity."""
    return (
        h["doc_type"] == doc_type
        and h["doc_status"] == "production"
        and h["link_status"] == "production"
        and h["coverage_scope"] == "group"
        and not _lapsed(h, today=today)
    )


def valid_doc(holdings, *, today: date) -> bool:
    """True iff some holding is a live declaration covering this article.

    "Covering" is `coverage_scope='group'`: the declaration names the article
    or a group it belongs to. A manufacturer-scope claim is not enough — that
    is a statement about the supplier, not about the article.
    """
    return any(_covers(h, doc_type="DoC", today=today) for h in holdings)


def valid_ce(holdings, *, today: date) -> bool:
    """True iff some holding is a live EC certificate covering this article.

    `EC` only, never `ISO`: ISO 13485 is a voluntary quality-standard
    certificate rather than an MDR conformity certificate, and it is the type
    that carries the registry's largest single concentration.

    An adverse EUDAMED status falsifies on its own, because a certificate can
    be suspended without its expiry date moving.
    """
    return any(
        _covers(h, doc_type="EC", today=today)
        and h["certificate_status"] not in ADVERSE_CERT_STATUSES
        for h in holdings
    )


def warehouse_url(item_ref: str, *, base_url: str, link_key: str) -> str:
    """The BC item card's hyperlink to this item's documents.

    Quoted with `safe=""`, so a slash inside a BC item number cannot walk the
    path into a different route. The shape is the 2026-08-25 item-document
    access spec's; the route is `web/item_link.py:96` and the key is checked by
    `web/access.py`'s `bc` caller class.
    """
    return f"{base_url}/item/{quote(item_ref, safe='')}?k={quote(link_key, safe='')}"


def fields_for(
    item_ref: str,
    holdings,
    *,
    processed: bool,
    today: date,
    base_url: str,
    link_key: str,
) -> dict | None:
    """The three BC values for one item, or `None` if it must not be written.

    `None` means the pipeline has never reached this item. A boolean has no
    "not checked yet", so writing `false` for an item nobody has looked at
    would assert "we checked and there is none". BC keeps its own default,
    which is at least not our claim.
    """
    if not processed:
        return None
    return {
        "pteValidDeclarationOfConformity": valid_doc(holdings, today=today),
        "pteValidCECertificate": valid_ce(holdings, today=today),
        "pteWarehouseURL": warehouse_url(
            item_ref, base_url=base_url, link_key=link_key
        ),
    }
