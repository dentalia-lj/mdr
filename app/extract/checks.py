"""Evidence checks: is a field's value PROVEN by the document's own text?

Pure functions, no I/O, no model. The extraction ladder (PRD v3 section 5,
rows "Logic" and "AI", amended 2026-10-12) uses them in place of the 0.95
self-reported-confidence rule: a field the text proves needs no further read;
one it cannot prove goes to the next tier. Measured on the eval set in
`docs/private/2026-10-08-extraction-ladder-research.md` sections 13-15; the
logic is ported from `tools/ladder_probe.py` (`passes`, `has_label`).

`settled(field, evidence, ctx)` is True only when proven. False means "not
proven", never "wrong". Semantic fields (`type`, `coverage_scope`) are never
settled here: later tasks settle them by T0 or by agreement between reads.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from app.extract.t0_templates import _DATE_RE, _parse_date
from app.extract.udi import INVALID, VALID, validate_basic_udi

DATE_FIELDS = ("validity_from", "validity_to")

#: Fields the text layer can prove literally. `regulation` and `ref_list` are
#: proven from the whole document rather than from one quote.
LITERAL_FIELDS = frozenset(
    {"validity_from", "validity_to", "regulation", "ref_list", "basic_udi_di",
     "cert_number", "manufacturer"})

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


def quote_on_page(verbatim, page, pages: list[str]) -> bool:
    """The quote is on the cited page (whitespace/case tolerant, fuzzy >= 95);
    with no valid page number, anywhere in the document."""
    v = _ws(verbatim or "")
    if not v:
        return False
    if isinstance(page, int) and not isinstance(page, bool) and 1 <= page <= len(pages):
        hay = [_ws(pages[page - 1])]
    else:
        hay = [_ws(p) for p in pages]
    return any(v in h or fuzz.partial_ratio(v, h) >= QUOTE_MATCH for h in hay)


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
    """A non-null value proven present in the document's text layer."""
    if field_name not in LITERAL_FIELDS or not isinstance(evidence, dict):
        return False
    v = evidence.get("value")
    if _blank(v):
        return False
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


def null_settled(field_name: str, ctx: Ctx) -> bool:
    """A null is proven only when the text could not hold the field: no label
    for it occurs, no page is an image / OCR layer / unread, and the text was
    not cut."""
    pat = LABELS.get(field_name)
    if pat is None:
        return False
    if ctx.truncated or ctx.unread_pages:
        return False
    if any(c in _UNRELIABLE_CLASSES for c in ctx.classes):
        return False
    return re.search(pat, ctx.text, re.IGNORECASE) is None
