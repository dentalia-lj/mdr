"""Evidence checks: is a field's value PROVEN by the document's own text?

Pure functions, no I/O, no model. The extraction ladder (PRD v3 section 5,
rows "Logic" and "AI", amended 2026-10-12) uses them in place of the 0.95
self-reported-confidence rule: a field the text proves needs no further read;
one it cannot prove goes to the next tier. Measured on the eval set in
`docs/private/2026-10-08-extraction-ladder-research.md` sections 13-15; the
logic is ported from `tools/ladder_probe.py` (`passes`, `has_label`).

`settled(field, evidence, ctx)` is True only when proven. False means "not
proven", never "wrong". The semantic fields (`type`, `coverage_scope`) settle
on a quote found on the cited page, as the measured route did (Denis,
2026-10-12): the text cannot prove a classification, but a read that quotes the
page for it needs no second read to stop the ladder. The gate still asks for
T0 or two agreeing reads before production (task 5).

Two helpers decide an EMPTY field, and they answer different questions:
`null_settled` (one read is empty and the text could not hold the field) and
`both_null_settled` (two reads are both empty; PRD v3 section 5, corrected
2026-10-12 to what was measured).

A scan has no text layer to prove anything against, so its reads are judged by
shape and by each other instead: `well_formed` (the value has the shape its
field takes), `normalized` (the form two reads are compared in) and `agree`
(two reads give the same well-formed value, each sure enough). Ported from
`tools/ladder_probe.py` `format_ok` and `tools/haiku_compare.py` `norm`, the
functions the scan route was measured with.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from app.extract.pdf import TEXT_BEARING
from app.extract.t0_templates import _DATE_RE, _parse_date
from app.extract.udi import INVALID, VALID, validate_basic_udi

DATE_FIELDS = ("validity_from", "validity_to")

#: Fields the text layer can prove literally. `regulation` and `ref_list` are
#: proven from the whole document rather than from one quote.
LITERAL_FIELDS = frozenset(
    {"validity_from", "validity_to", "regulation", "ref_list", "basic_udi_di",
     "cert_number", "manufacturer"})

#: Fields the text cannot prove, settled on a quote found on the cited page.
SEMANTIC_FIELDS = frozenset({"type", "coverage_scope"})

#: Labels whose presence in the text means a field may be stated somewhere.
#: From `tools/ladder_probe.py` LABELS, with one fix: the UDI label is a word.
#: `udi` as a substring matches "including" and "guidelines", which would
#: veto every null for the field.
LABELS = {
    "validity_to": r"valid until|valid to|expir|gültig bis|gueltig bis|scadenza|valido fino|velja do|vrijedi do|valable jusqu",
    "validity_from": r"date|datum|issued|gültig ab|valid from|data|izdan",
    "cert_number": r"certific|zertifikat|certifikat|certificato|registration no",
    "basic_udi_di": r"\budi\b",
    "ref_list": r"\bref\b|article|artikel|art\.|item|catalog|katalog|šifra|codice",
    "regulation": r"2017/745|93/42|regulation|richtlinie|directive|uredb|direktiv",
    "manufacturer": r"manufactur|hersteller|fabbricante|proizvajalec|proizvođač|fabricant",
}

#: A notified-body number is not a certificate number: "0482", "CE-0197",
#: "NB1639" sit in `cert_number` today (decisions.md, 2026-08-26).
_NOTIFIED_BODY = re.compile(r"^(?:CE|NB)?[\s-]?\d{4}$", re.IGNORECASE)

#: Share of REF codes that must be printed for the list to count as proven.
REF_PRESENT_SHARE = 0.95

#: Fuzzy floor for "the quote is on the cited page" (OCR and ligature noise).
QUOTE_MATCH = 95

#: Page classes whose text layer cannot prove an absence.
_UNRELIABLE_CLASSES = frozenset({"image", "ocr"})

#: A quote the reader shortened: "Basis-UDI-DI ... ++J022MD0098KV". Each piece
#: must be on the page; the elision itself is not page text.
_ELLIPSIS = re.compile(r"\.{3,}|\u2026")


def compact(s) -> str:
    """Whitespace removed, case folded: the literal-presence comparison."""
    return "".join(str(s).split()).casefold()


def _ws(s: str) -> str:
    return " ".join(s.split()).casefold()


@dataclass(frozen=True)
class Ctx:
    """What the checks may look at. `pages[0]` is page 1."""

    pages: list[str]
    classes: list[str] = field(default_factory=list)
    #: The text handed to the reader was cut (`pdf.bounded_text` dropped chars).
    truncated: bool = False
    #: 1-based pages no read looked at (e.g. a scan over the page cap).
    unread_pages: frozenset[int] = frozenset()

    @property
    def text(self) -> str:
        return "\n".join(self.pages)

    @property
    def text_c(self) -> str:
        return compact(self.text)


def _found(piece: str, hay: str) -> bool:
    return piece in hay or fuzz.partial_ratio(piece, hay) >= QUOTE_MATCH


def quote_on_page(verbatim, page, pages: list[str]) -> bool:
    """The quote is on the cited page (whitespace/case tolerant, fuzzy >= 95);
    with no valid page number, on some one page of the document. A quote
    shortened with "..." or an ellipsis character holds when every piece is on
    that page (measured: two GC declarations quote "Basis-UDI-DI ... <code>")."""
    pieces = [p for p in (_ws(x) for x in _ELLIPSIS.split(verbatim or "")) if p]
    if not pieces:
        return False
    if isinstance(page, int) and not isinstance(page, bool) and 1 <= page <= len(pages):
        hay = [_ws(pages[page - 1])]
    else:
        hay = [_ws(p) for p in pages]
    return any(all(_found(p, h) for p in pieces) for h in hay)


def dates_in(text: str) -> set[str]:
    """Every ISO date the T0 parser reads out of `text`."""
    out = {d for m in _DATE_RE.finditer(text) if (d := _parse_date(m.group(0)))}
    return out | set(re.findall(r"\d{4}-\d{2}-\d{2}", text))


def udi_check_valid(value) -> bool:
    """The Basic UDI-DI's own check pair verifies. Stricter than "not invalid":
    an unscorable code (EUDAMED-assigned, unknown agency) is not check-valid.
    A later task uses this to settle a UDI read off an image even when the
    text layer differs (OCR "OOIYC" against "001YC")."""
    return bool(value) and validate_basic_udi(str(value)).status == VALID


def is_notified_body_number(value) -> bool:
    return bool(_NOTIFIED_BODY.match(str(value).strip()))


def _blank(value) -> bool:
    if value is None:
        return True
    if isinstance(value, (str, list, tuple, dict)):
        return len(value) == 0 or (isinstance(value, str) and not value.strip())
    return False


def settled(field_name: str, evidence: dict | None, ctx: Ctx) -> bool:
    """A non-null value proven present in the document's text layer; for a
    semantic field, a non-null value whose quote is on the cited page."""
    known = field_name in LITERAL_FIELDS or field_name in SEMANTIC_FIELDS
    if not known or not isinstance(evidence, dict):
        return False
    v = evidence.get("value")
    if _blank(v):
        return False
    if field_name in SEMANTIC_FIELDS:
        return quote_on_page(evidence.get("verbatim"), evidence.get("page"), ctx.pages)
    full_c = ctx.text_c

    if field_name == "regulation":
        mdr, mdd = "2017/745" in full_c, "93/42" in full_c
        return {"mdr": mdr and not mdd, "mdd": mdd and not mdr,
                "n.a.": not (mdr or mdd)}.get(str(v).strip().casefold(), False)

    if field_name == "ref_list":
        if not isinstance(v, (list, tuple)):
            return False
        codes = [compact(c) for c in v if str(c).strip()]
        return bool(codes) and sum(c in full_c for c in codes) >= REF_PRESENT_SHARE * len(codes)

    grounded = quote_on_page(evidence.get("verbatim"), evidence.get("page"), ctx.pages)
    if not grounded:
        return False
    if field_name in DATE_FIELDS:
        return str(v) in dates_in(evidence.get("verbatim") or "")
    if compact(v) not in full_c:
        return False
    if field_name == "basic_udi_di":
        return validate_basic_udi(str(v)).status != INVALID
    if field_name == "cert_number":
        return not is_notified_body_number(v)
    return True  # manufacturer


def _classes_known(ctx: Ctx) -> bool:
    return bool(ctx.classes) and len(ctx.classes) == len(ctx.pages)


def null_settled(field_name: str, ctx: Ctx) -> bool:
    """ONE read's null is proven only when the text could not hold the field:
    no label for it occurs, no page is an image (a scan or a vector-drawn page)
    / OCR layer / unread, and the text was not cut. Fails closed when the page
    classes are missing or do not match the pages, and when no page carries a
    text layer: an empty text layer holds no label, which proves nothing."""
    pat = LABELS.get(field_name)
    if pat is None:
        return False
    if ctx.truncated or ctx.unread_pages:
        return False
    if not _classes_known(ctx) or not any(c in TEXT_BEARING for c in ctx.classes):
        return False
    if any(c in _UNRELIABLE_CLASSES for c in ctx.classes):
        return False
    return re.search(pat, ctx.text, re.IGNORECASE) is None


def both_null_settled(field_name: str, ctx: Ctx) -> bool:
    """TWO reads both returned the field empty: settled, for any field (PRD v3
    section 5, corrected 2026-10-12 to the measured rule), except

    - a Basic UDI-DI when the text carries a UDI label (as a word), or when no
      page carries a text layer to look for one in (a scan: the measured scan
      route never settled a UDI on two nulls),
    - any field when part of the document was not read (unread pages) or the
      text handed to the reader was cut.

    Unlike `null_settled`, labels for other fields and image or OCR pages do not
    stop it: two reads agreeing on an absence is the evidence."""
    if ctx.truncated or ctx.unread_pages:
        return False
    if field_name == "basic_udi_di":
        if not any(c in TEXT_BEARING for c in ctx.classes):
            return False
        if re.search(LABELS["basic_udi_di"], ctx.text, re.IGNORECASE):
            return False
    return True


# --------------------------------------------------------------------------- #
# Reads off page images: shape and agreement, no text layer
# --------------------------------------------------------------------------- #

#: The closed vocabularies the prompts ask for. `coverage_scope` is also closed
#: by the response schema; `type` and `regulation` are free strings there.
TYPES = frozenset({"DoC", "EC", "ISO", "IFU", "SPP", "other"})
SCOPES = frozenset({"group", "manufacturer"})
REGULATIONS = frozenset({"MDR", "MDD", "n.a."})

#: Fields whose value is a list of codes, compared as a set.
LIST_FIELDS = frozenset({"ref_list", "referenced_docs"})

#: Scalar fields compared as codes: spacing is not part of the value
#: ("G10 082649 0004" and "G10082649 0004" are one certificate).
_CODE_FIELDS = frozenset({"basic_udi_di", "cert_number"})

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def normalized(field_name: str, value):
    """The form two reads are compared in, or None for an empty answer: a list
    becomes the set of its compacted codes, a date its ISO form when it parses,
    a code its compacted form, anything else whitespace-collapsed and case-
    folded with trailing punctuation dropped."""
    if _blank(value):
        return None
    if field_name in LIST_FIELDS:
        items = value if isinstance(value, (list, tuple, set, frozenset)) else [value]
        return frozenset(compact(c) for c in items if str(c).strip()) or None
    if field_name in DATE_FIELDS:
        s = str(value).strip()
        return _parse_date(s) or _ws(s)
    if field_name in _CODE_FIELDS:
        return compact(value)
    return _ws(str(value)).rstrip(".;,")


def well_formed(field_name: str, value) -> bool:
    """The value has the shape its field takes, which is all that can be
    checked on a read with no text layer to hold it against: not empty; a
    date in ISO form that is a real day; `type`, `coverage_scope` and
    `regulation` inside their vocabularies; a Basic UDI-DI whose check pair is
    not invalid; a `cert_number` that is not a notified-body number; a list
    holding at least one code."""
    if normalized(field_name, value) is None:
        return False
    if field_name in DATE_FIELDS:
        s = str(value).strip()
        return bool(_ISO_DATE.fullmatch(s)) and _parse_date(s) == s
    if field_name == "type":
        return value in TYPES
    if field_name == "coverage_scope":
        return value in SCOPES
    if field_name == "regulation":
        return value in REGULATIONS
    if field_name == "basic_udi_di":
        return validate_basic_udi(str(value)).status != INVALID
    if field_name == "cert_number":
        return not is_notified_body_number(value)
    if field_name in LIST_FIELDS:
        return isinstance(value, (list, tuple)) and all(isinstance(c, str) for c in value)
    return True


def _conf(evidence) -> float:
    try:
        return float((evidence or {}).get("conf") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def agree(field_name: str, a: dict | None, b: dict | None, minimum: float) -> bool:
    """Two reads settle a field by agreeing: the same normalized value, well-
    formed, and each read at least `minimum` sure (`gate.agreement_min`,
    rulings 2026-10-12). Two empty answers are not this rule's business; they
    are `both_null_settled`'s."""
    va = (a or {}).get("value")
    vb = (b or {}).get("value")
    if not (well_formed(field_name, va) and well_formed(field_name, vb)):
        return False
    if normalized(field_name, va) != normalized(field_name, vb):
        return False
    return _conf(a) >= minimum and _conf(b) >= minimum
