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
from app.extract.t0_templates import _DATE_RE, _MONTH_NUMBERS, _parse_date
from app.extract.udi import INVALID, VALID, is_word, validate_basic_udi

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

#: Fields a single read's empty answer never settles, whatever the labels: a
#: compliance document always names its manufacturer, often as a letterhead
#: with no label at all, so a missing "Manufacturer" label proves nothing
#: (Task 9 replay, doc 2173: a text layer in a private font encoding, T1
#: could not read the name, the null rule settled it, and the image read that
#: reads EURONDA S.P.A. at 0.95 was never asked). Two empty reads still
#: settle it (`both_null_settled`). Its label stays in `LABELS` for the page
#: choice of an image read (`tiers._image_pages`).
NEVER_NULL_SETTLED = frozenset({"manufacturer"})

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
    #: 1-based pages no read looked at (e.g. a scan over the page cap), or
    #: saw only in part: for a text read, a scanned page of a mixed PDF and a
    #: page where an annotation paints an image (`pdf.annotation_image_pages`).
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


def _month_year_in(value: str, quote: str) -> bool:
    """`value` is the first of a month, and `quote` prints that month and year
    as a revision line does: "06-2023", "01/2024", "04/26", "2023-06", "June
    2023". Never as part of a whole date ("15-06-2023", "2023-06-15"), whose
    day the value would drop."""
    y, m, d = value.split("-")
    if d != "01":
        return False
    mo = int(m)
    names = "|".join(re.escape(n) for n, k in _MONTH_NUMBERS.items() if k == mo)
    patterns = (
        rf"(?<!\d)(?<!\d[-/.])0?{mo}\s*[-/._]\s*(?:{y}|{y[2:]})(?!\d)",
        rf"(?<!\d){y}\s*[-/.]\s*0?{mo}(?![-/.]?\d)",
        rf"\b(?:{names})\.?,?\s*{y}(?!\d)",
    )
    return any(re.search(p, quote, re.IGNORECASE) for p in patterns)


def issue_date_proven(evidence: dict | None, ctx: Ctx | None = None) -> bool:
    """A read's `validity_from` that its own quote prints: a whole date the
    T0 parser reads out of the quote (`dates_in`), or the first of a month
    whose month and year the quote prints ("REV.00_06-2023" for 2023-06-01).
    For an IFU, whose only date is its revision date (Task 9 replay, 2026-10-14:
    six IFUs lost it). A day the quote does not print is not read off it: the
    STRAUMANN read "701593/M/12 04/26" as 2026-04-26, a day made up from a
    revision code, where the code prints April 2026 (decisions.md 2026-10-02).

    With `ctx`, a quote cited on a page the text reads in full must be on that
    page, as `settled` requires; on a page no text read sees (a scan, an image
    page), and without `ctx`, the quote stands alone."""
    if not isinstance(evidence, dict):
        return False
    value, quote = evidence.get("value"), str(evidence.get("verbatim") or "")
    if not well_formed("validity_from", value):
        return False
    page = evidence.get("page")
    if (ctx is not None and isinstance(page, int) and not isinstance(page, bool)
            and 1 <= page <= len(ctx.pages) and page not in ctx.unread_pages
            and (not _classes_known(ctx) or ctx.classes[page - 1] in TEXT_BEARING)
            and not quote_on_page(quote, page, ctx.pages)):
        return False
    return str(value) in dates_in(quote) or _month_year_in(str(value), quote)


def udi_check_valid(value) -> bool:
    """The Basic UDI-DI's own check pair verifies. Stricter than "not invalid":
    an unscorable code (EUDAMED-assigned, unknown agency) is not check-valid.
    A later task uses this to settle a UDI read off an image even when the
    text layer differs (OCR "OOIYC" against "001YC")."""
    return bool(value) and validate_basic_udi(str(value)).status == VALID


def _udi_shaped(value) -> bool:
    """A Basic UDI-DI whose check pair is not invalid and that is not a word
    (`udi.is_word`: no agency's shape and no digit; review 2026-10-13: T0 read
    "PRODUCT" and "Document" after the label, which scored UNSCORABLE and
    passed). GS1, HIBCC and EUDAMED-assigned values are judged as before."""
    return validate_basic_udi(str(value)).status != INVALID and not is_word(str(value))


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
        return _udi_shaped(v)
    if field_name == "cert_number":
        return not is_notified_body_number(v)
    return True  # manufacturer


def _classes_known(ctx: Ctx) -> bool:
    return bool(ctx.classes) and len(ctx.classes) == len(ctx.pages)


def null_settled(field_name: str, ctx: Ctx) -> bool:
    """ONE read's null is proven only when the text could not hold the field:
    no label for it occurs, no page is an image (a scan or a vector-drawn page)
    / OCR layer / unread (a page where an annotation paints an image, VOCO's
    e-signature holding the issue date, is unread to a text read), and the
    text was not cut. Fails closed when the page classes are missing or do not
    match the pages, and when no page carries a text layer: an empty text
    layer holds no label, which proves nothing. Never for a field every
    document carries (`NEVER_NULL_SETTLED`: the manufacturer)."""
    pat = LABELS.get(field_name)
    if pat is None or field_name in NEVER_NULL_SETTLED:
        return False
    if ctx.truncated or ctx.unread_pages:
        return False
    if not _classes_known(ctx) or not any(c in TEXT_BEARING for c in ctx.classes):
        return False
    if any(c in _UNRELIABLE_CLASSES for c in ctx.classes):
        return False
    return re.search(pat, ctx.text, re.IGNORECASE) is None


def citation_proven(evidence: dict | None, ctx: Ctx) -> bool:
    """A `regulation` proven by its one printed citation, for GATE's 1.0 (PRD
    v3 section 7, amended 2026-10-12; extraction-ladder plan Task 5b): a
    well-formed value (`MDR`, `MDD` or `n.a.`, as written), `settled` (only
    2017/745 printed for MDR, only 93/42 for MDD, neither for `n.a.`), and
    the text the check read covers the whole document.

    The last half is what makes it a proof. "Only one citation printed" is
    also a claim that the other is NOT printed, and that is unknown where part
    of the document has no text to read: a scanned page of a mixed PDF, an
    unread page (the text ladder counts a page where an annotation paints an
    image as one, `pdf.annotation_image_pages`), text cut at the budget. A
    scan with a short visible overlay is an image page too
    (`pdf.page_classes`, review 2026-10-13, F7), so it stops every citation
    proof; a sparse page (a cover, a signature page with no such annotation)
    is text and stops none. An OCR layer is read as text here,
    as `settled` reads it and as the measured route counted it (on the eval,
    one OCR-layer declaration, Celalux 3, is proven `n.a.` this way). Fails
    closed when the page classes are missing or do not match the pages, and
    when no page carries a text layer: a scan has no text, so `n.a.` would
    pass `settled` on it."""
    value = (evidence or {}).get("value") if isinstance(evidence, dict) else None
    if not well_formed("regulation", value):
        return False
    if ctx.truncated or ctx.unread_pages or not _classes_known(ctx):
        return False
    if "image" in ctx.classes or not any(c in TEXT_BEARING for c in ctx.classes):
        return False
    return settled("regulation", evidence, ctx)


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
# A read's value that is provably not the field (Task 9 replay, 2026-10-14)
# --------------------------------------------------------------------------- #
#: A nomenclature that codes device TYPES, not articles: GMDN, EMDN, UMDNS,
#: and EMDN's Italian origin CND.
_NOMENCLATURE_LABEL = re.compile(r"\b(?:GMDN|EMDN|UMDNS|CND)\b", re.IGNORECASE)

#: The shapes their codes take: EMDN a letter and two to ten digits in pairs
#: ("V9001", "Q01010103"), GMDN five digits ("45247"), UMDNS five digits or
#: ECRI's "16-411".
_NOMENCLATURE_SHAPE = re.compile(r"^(?:[A-Z]\d{2}(?:\d{2}){0,4}|\d{5}|\d{2}-\d{3})$")


def nomenclature_codes(codes, text: str) -> list[str]:
    """The codes of `codes` the text prints as a nomenclature entry, in their
    order: on a document that names GMDN, EMDN, UMDNS or CND, a code of their
    shape printed right after such a label, or followed by its term ("14066,
    Tongue Depressor", "Q01010102: Temporary and Permanent Dental Restoration
    Cements"). A nomenclature code names a type of device, not an article.

    Doc 622 (Task 9 replay): Ultradent's form prints its labels first and its
    values after, so T1 read the GMDN and EMDN codes as the "Model Numbers",
    every one printed, and the 95% check settled them. Measured 2026-10-14: no
    code of the 17,133 in T0's REF lists on the 1,320 PDFs of the SFTP mirror,
    and none in 415 cached REF-list reads but doc 622's, is found here."""
    if not text or not _NOMENCLATURE_LABEL.search(text):
        return []
    out = []
    for code in codes or []:
        c = str(code).strip()
        if not _NOMENCLATURE_SHAPE.match(c):
            continue
        esc = re.escape(c)
        with_term = rf"(?<![\w.-]){esc}[ \t]*[,:/\u2013-]?[ \t]*[^\W\d_]{{3,}}"
        after_label = rf"{_NOMENCLATURE_LABEL.pattern}[^\n]{{0,40}}\n?[ \t]*{esc}(?![\w.-])"
        if re.search(with_term, text) or re.search(after_label, text, re.IGNORECASE):
            out.append(code)
    return out


#: A read saying in its own quote that what it returned is not a REF code:
#: "(these are type designations, not explicit REF codes)".
_DISOWNED_REF = re.compile(r"\bnot\b[^.;:()\n]{0,30}?\b(?:REF|article|catalog(?:ue)?)\b",
                           re.IGNORECASE)

#: Below this a read is more unsure than sure of its own list.
DISOWNED_REF_CONF = 0.5


def not_the_field(field_name: str, evidence: dict | None, text: str = "") -> tuple[str | None, object]:
    """Why a read's value is provably not `field_name`, and what of it stays:
    (None, None) when nothing is wrong with it, (reason, None) when none of it
    stays, (reason, kept) for a list that keeps some codes. The ladder treats
    a value none of which stays as no answer for the field
    (`tiers._read_step`); the read's record keeps the value and the reason.

    `ref_list`: a list the read states under `DISOWNED_REF_CONF` while its own
    quote says the values are not REF codes (doc 305, Barco type designations
    at 0.4; real Ultradent model numbers were read at 0.55 to 0.7 with "not
    labelled REF", and a real list as low as 0.35, so neither half alone);
    and any code the text prints as a nomenclature entry
    (`nomenclature_codes`, doc 622)."""
    if not isinstance(evidence, dict):
        return None, None
    value = evidence.get("value")
    if field_name == "ref_list" and isinstance(value, list) and value:
        if (_conf(evidence) < DISOWNED_REF_CONF
                and _DISOWNED_REF.search(str(evidence.get("verbatim") or ""))):
            return "the read says these are not REF codes", None
        bad = nomenclature_codes(value, text)
        if bad:
            kept = [c for c in value if c not in bad]
            return f"nomenclature codes, not articles: {', '.join(map(str, bad))}", kept or None
    return None, None


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
    not invalid and that is not a word (`udi.is_word`); a `cert_number` that is
    not a notified-body number; a list holding at least one code."""
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
        return _udi_shaped(value)
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
