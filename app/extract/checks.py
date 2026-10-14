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
T0 or two agreeing reads before production (task 5). The text ladder takes that
quote only from a read at least `gate.agreement_min` sure (`tiers._text_settles`,
Task 9 replay, 2026-10-13); `settled` itself does not look at confidence.

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
#: veto every null for the field. `model` joined the REF labels on 2026-10-14
#: (Task 9 replay, doc 617): Ultradent prints its article numbers as "Model
#: Numbers", T1 left them out, and with no other label the null rule settled
#: the empty list.
LABELS = {
    "validity_to": r"valid until|valid to|expir|gültig bis|gueltig bis|scadenza|valido fino|velja do|vrijedi do|valable jusqu",
    "validity_from": r"date|datum|issued|gültig ab|valid from|data|izdan",
    "cert_number": r"certific|zertifikat|certifikat|certificato|registration no",
    "basic_udi_di": r"\budi\b",
    "ref_list": r"\bref\b|article|artikel|art\.|item|catalog|katalog|šifra|codice|\bmodel",
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


#: Month names the month-and-year rule never reads: "Set 2019" is a set
#: number, and "out" and "ago" are English words before a year (Denis,
#: 2026-10-14: lot, page and set numbers are never dates).
_NOT_A_MONTH_ALONE = frozenset({"set", "out", "ago"})

#: English month names that are English words too: a month only when
#: capitalised ("May 2023"; "may 2023" is the verb, Denis 2026-10-14).
_CAPITALISED_MONTHS = frozenset({"may", "march", "august"})

#: A lot, page, set or serial number's label just before the match: "Lot
#: 03/24", "Page 1/24" (Denis, 2026-10-14).
_NUMBER_LABEL = re.compile(
    r"\b(?:lot|lote|lotto|batch|charge|page|pages|pag|pagina|seite|stran|set|serie|series"
    r"|serial|sn|s/n)\.?\s*(?:no\.?|nr\.?)?\s*[:#]?\s*$", re.IGNORECASE)

#: A day printed just before the month: "15 June", "15. Juni", "25. 08.".
#: The whole date is then the document's, and its day is never cut to the
#: first (Denis, 2026-10-14).
_DAY_BEFORE_NAME = re.compile(r"\d(?:st|nd|rd|th)?\.?\s*$", re.IGNORECASE)
_DAY_BEFORE_NUMBER = re.compile(r"\d\s*[-/.]\s*$")

#: A further part of a date after the match: "06/23" of "06/23/2023".
_MORE_AFTER = re.compile(r"\s*[-/.]\s*\d")


def _month_year_in(value: str, quote: str) -> str | None:
    """Where `quote` prints the month and year of `value`, the first of a
    month, as a revision line does: "06-2023", "01/2024", "04/26", "2023-06",
    "June 2023". An IFU stores such a revision date as the first of its month
    (Denis, 2026-10-14). Never as part of a whole date ("15-06-2023",
    "2023-06-15", "06/23/2023", "15 June 2023", "Datum: 15. Juni 2023",
    "25. 08. 2025"): a printed day is never cut to the first. Never a lot,
    page or set number ("Lot 03/24", "Page 1/24", "Set 2019"), and never a
    lower-case English word ("may 2023"). None when it does not, or when
    `value` is not an ISO date."""
    if not _ISO_DATE.fullmatch(value):
        return None
    y, m, d = value.split("-")
    if d != "01":
        return None
    mo = int(m)
    names = "|".join(re.escape(n) for n, k in _MONTH_NUMBERS.items()
                     if k == mo and n not in _NOT_A_MONTH_ALONE)
    numeric = (rf"(?<!\d)0?{mo}\s*[-/._]\s*(?:{y}|{y[2:]})(?!\d)",
               rf"(?<!\d){y}\s*[-/.]\s*0?{mo}(?!\d)")
    for p in numeric:
        for found in re.finditer(p, quote):
            before = quote[:found.start()]
            if (_DAY_BEFORE_NUMBER.search(before) or _NUMBER_LABEL.search(before)
                    or _MORE_AFTER.match(quote, found.end())):
                continue
            return found.group(0)
    for found in re.finditer(rf"\b(?P<name>{names})\.?,?\s*{y}(?!\d)", quote, re.IGNORECASE):
        name, before = found.group("name"), quote[:found.start()]
        if name.casefold() in _CAPITALISED_MONTHS and not name[0].isupper():
            continue
        if _DAY_BEFORE_NAME.search(before) or _NUMBER_LABEL.search(before):
            continue
        return found.group(0)
    return None


def _printed_date(value: str, quote: str) -> str | None:
    """The part of `quote` that prints `value`: a whole date the T0 parser
    reads as `value`, the ISO date itself, or its month and year
    (`_month_year_in`). None when the quote does not print it."""
    for m in _DATE_RE.finditer(quote):
        if _parse_date(m.group(0)) == value:
            return m.group(0)
    if value in quote:
        return value
    return _month_year_in(value, quote)


def issue_date_proven(evidence: dict | None, ctx: Ctx | None = None) -> bool:
    """A read's `validity_from` that its own quote prints: a whole date the
    T0 parser reads out of the quote, or the first of a month whose month and
    year the quote prints ("REV.00_06-2023" for 2023-06-01). For an IFU, whose
    only date is its revision date (Task 9 replay, 2026-10-14: six IFUs lost
    it). A day the quote does not print is not read off it: the STRAUMANN read
    "701593/M/12 04/26" as 2026-04-26, a day made up from a revision code,
    where the code prints April 2026 (decisions.md 2026-10-02).

    With `ctx`, the part of the quote that prints the date must be on the
    cited page when the text reads that page in full; the reader's note
    around it need not be ("04/2025 (no day given; document revision date)",
    doc 1059). On a page no text read sees (a scan, an image page), and
    without `ctx`, the quote stands alone."""
    if not isinstance(evidence, dict):
        return False
    value, quote = evidence.get("value"), str(evidence.get("verbatim") or "")
    if not well_formed("validity_from", value):
        return False
    printed = _printed_date(str(value), quote)
    if printed is None:
        return False
    page = evidence.get("page")
    if (ctx is not None and isinstance(page, int) and not isinstance(page, bool)
            and 1 <= page <= len(ctx.pages) and page not in ctx.unread_pages
            and (not _classes_known(ctx) or ctx.classes[page - 1] in TEXT_BEARING)):
        return compact(printed) in compact(ctx.pages[page - 1])
    return True


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
        # Every value this accepts rests on what is NOT printed (`n.a.`:
        # neither citation; MDR or MDD: not the other one), so it holds only
        # where the text covers the whole document. Task 9 replay: a scanned
        # declaration on page 1 with its article list as text proved `n.a.`
        # while the scan printed 93/42 (USTOMED, RTD, ZHERMACK).
        if not _text_covers_document(ctx):
            return False
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


def _text_covers_document(ctx: Ctx) -> bool:
    """The text the check reads is the whole document: not cut, no page left
    unread, no scanned (image) page, and some page with a text layer (an OCR
    layer and a sparse page are read as text, as `citation_proven` reads
    them). Fails closed when the page classes are missing or do not match."""
    return (not ctx.truncated and not ctx.unread_pages and _classes_known(ctx)
            and "image" not in ctx.classes
            and any(c in TEXT_BEARING for c in ctx.classes))


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


#: How many lines below a nomenclature label its code may be printed. Doc
#: 622's form prints eight labels, then their eight values, so its codes sit
#: eight to ten lines below their labels.
NOMENCLATURE_LINES = 12


def _near_nomenclature_labels(text: str) -> list[str]:
    """The stretches of `text` that start at a nomenclature label's line and
    run `NOMENCLATURE_LINES` lines on."""
    lines = text.splitlines()
    return ["\n".join(lines[i:i + NOMENCLATURE_LINES + 1])
            for i, line in enumerate(lines) if _NOMENCLATURE_LABEL.search(line)]


def nomenclature_codes(codes, text: str, listed=()) -> list[str]:
    """The codes of `codes` the text prints as a nomenclature entry, in their
    order: a code of the shape GMDN, EMDN, UMDNS or CND codes take, printed
    within `NOMENCLATURE_LINES` lines below such a label, right after the
    label or followed by its term ("14066, Tongue Depressor", "Q01010102:
    Temporary and Permanent Dental Restoration Cements"). A nomenclature code
    names a type of device, not an article. Never a code of `listed` (T0's
    own REF list): T0 enumerated the document's article list, so a code it
    holds is an article.

    Doc 622 (Task 9 replay): Ultradent's form prints its labels first and its
    values after, so T1 read the GMDN and EMDN codes as the "Model Numbers",
    every one printed, and the 95% check settled them. Measured 2026-10-14: no
    code of the 17,133 in T0's REF lists on the 1,320 PDFs of the SFTP mirror,
    and none in 415 cached REF-list reads but doc 622's, is found here. Near a
    label only (review of the Task 9 fixes, 2026-10-14): a document that names
    GMDN once may list five-digit article numbers with their descriptions
    pages later."""
    if not text or not _NOMENCLATURE_LABEL.search(text):
        return []
    stretches = _near_nomenclature_labels(text)
    held = {compact(c) for c in listed or []}
    out = []
    for code in codes or []:
        c = str(code).strip()
        if not _NOMENCLATURE_SHAPE.match(c) or compact(c) in held:
            continue
        esc = re.escape(c)
        with_term = rf"(?<![\w.-]){esc}[ \t]*[,:/\u2013-]?[ \t]*[^\W\d_]{{3,}}"
        after_label = rf"{_NOMENCLATURE_LABEL.pattern}[^\n]{{0,40}}\n?[ \t]*{esc}(?![\w.-])"
        if any(re.search(with_term, s) or re.search(after_label, s, re.IGNORECASE)
               for s in stretches):
            out.append(code)
    return out


#: A read saying in its own quote that what it returned is not a REF code:
#: "(these are type designations, not explicit REF codes)", "not enumerated
#: discrete REF codes", "not article numbers". Disowning phrasing only
#: (review of the Task 9 fixes, 2026-10-14): "not", at most two words that do
#: not describe how the list is printed, then the codes or numbers themselves.
#: "(not labelled as REF)", "List not complete, see catalogue" and "not all
#: article numbers are shown" describe a real list.
_DISOWNED_REF = re.compile(
    r"\bnot\s+(?:(?!(?:all|every|each|as|labell?ed|marked|printed|shown|listed|stated|given)\b)"
    r"[a-z-]+\s+){0,2}(?:REF|article|catalog(?:ue)?)\s*(?:codes?|numbers?|nos?\b\.?)",
    re.IGNORECASE)

#: Below this a read is more unsure than sure of its own list.
DISOWNED_REF_CONF = 0.5

#: Up to 40 characters between the declaration word and its number label that
#: hold no certificate word. "This declaration is based on EC Certificate No.:"
#: labels the certificate the declaration cites, which is ruling A's
#: `cert_number` (review of the Task 9 fixes, 2026-10-14).
_NO_CERTIFICATE_GAP = r"(?:(?!certific|zertifikat|certificat|certifikat)[^:\n]){0,40}?"

#: The label of a declaration's OWN number, ending the quote just before the
#: value: "Declaration of conformity reference:", "DoC No.", "Konformitäts-
#: erklärung Nr.", "Référence de la déclaration de conformité :". It names the
#: declaration, so an EC or ISO certificate's own number ("Certificate No.")
#: never matches, nor does a label a certificate word separates from the
#: declaration ("Konformitätserklärung gemäß Zertifikat Nr.").
_OWN_REFERENCE = re.compile(
    rf"(?:declaration|\bDoC\b|dichiarazione|erkl[äa]rung|izjava){_NO_CERTIFICATE_GAP}"
    r"(?:reference|\bref\b\.?|\bno\b\.?|\bnr\b\.?|n°|number|nummer|num[ée]ro|\bšt\b\.?)"
    r"\s*[:.]?\s*$"
    r"|(?:r[ée]f[ée]rence|\bref\b\.?|\bno\b\.?|\bnr\b\.?|n°|number|nummer|num[ée]ro)"
    r"\s+(?:de\s+la\s+|of\s+the\s+|der\s+)?(?:d[ée]claration|declaration|erkl[äa]rung)"
    rf"{_NO_CERTIFICATE_GAP}\s*[:.]?\s*$",
    re.IGNORECASE)


#: A form template named by its ID: "Template RP00S0.07" (doc 617),
#: "Template: PLP_T_0071" (Dentsply), "Template ID: TEFO-EN-000422"
#: (Ivoclar). A product such as a "Surgical Template Kit" names no form.
_TEMPLATE = re.compile(r"\btemplate\b(?:\s*(?:id|no|nr|number)\.?)?\s*[:#]?\s*\S*\d",
                       re.IGNORECASE)


def _template_date(value, quote: str) -> bool:
    """The quote gives `value` as the date of the form the document is printed
    on: on a line of the quote that prints the date (`_printed_date`), a
    template named by its ID before it ("Template RP00S0.07 Released May 31,
    2024", doc 617). Only that line (review of the Task 9 fixes, 2026-10-14):
    Ivoclar prints "Template ID: TEFO-EN-000422" in every page footer, and a
    quote that runs into it, or names a "Surgical Template Kit", still gives
    the document's own date. False when no line prints the date."""
    for line in quote.splitlines():
        printed = _printed_date(str(value), line)
        at = line.find(printed) if printed else -1
        if at >= 0 and _TEMPLATE.search(line[:at]):
            return True
    return False


def _before(quote: str, value) -> str | None:
    """The quote up to where `value` starts in it, spacing ignored; None when
    the quote does not hold the value."""
    chars = "".join(str(value).split())
    if not chars:
        return None
    m = re.search(r"\s*".join(re.escape(ch) for ch in chars), quote, re.IGNORECASE)
    return quote[:m.start()] if m else None


def not_the_field(field_name: str, evidence: dict | None, text: str = "",
                  listed=()) -> tuple[str | None, object]:
    """Why a read's value is provably not `field_name`, and what of it stays:
    (None, None) when nothing is wrong with it, (reason, None) when none of it
    stays, (reason, kept) for a list that keeps some codes. The ladder treats
    a value none of which stays as no answer for the field
    (`tiers._read_step`); the read's record keeps the value and the reason.

    `ref_list`: a list the read states under `DISOWNED_REF_CONF` while its own
    quote says the values are not REF codes (doc 305, Barco type designations
    at 0.4; real Ultradent model numbers were read at 0.55 to 0.7 with "not
    labelled REF", and a real list as low as 0.35, so neither half alone);
    and any code the text prints as a nomenclature entry near its label
    (`nomenclature_codes`, doc 622), never one of `listed`, T0's own REF
    list.

    `cert_number`: a number the quote labels as the declaration's own
    ("Declaration of conformity reference: 1000-TF_8_EUDOC_000219 [5]", doc
    1406, Sonnet). Ruling A (2026-10-12) makes a DoC's `cert_number` the
    certificate it cites; its own number is never one, as a notified body's
    four-digit number never is (`is_notified_body_number`, a shape rule on
    the value, which keeps a read from settling the field). This one needs
    the label in the quote. Measured 2026-10-14: 1 of 377 cached
    certificate-number reads matches, doc 1406's.

    `validity_from` / `validity_to`: a date its quote gives as a template's
    ("Template RP00S0.07 Released May 31, 2024", doc 617, T1 at 0.3). The
    quote prints the date, so the checks settled it, and the declaration's
    own date, in a notary's stamp no text layer holds, was never asked of an
    image read. 1 of 1,143 cached date reads matches, doc 617's."""
    if not isinstance(evidence, dict):
        return None, None
    value = evidence.get("value")
    if field_name == "ref_list" and isinstance(value, list) and value:
        if (_conf(evidence) < DISOWNED_REF_CONF
                and _DISOWNED_REF.search(str(evidence.get("verbatim") or ""))):
            return "the read says these are not REF codes", None
        bad = nomenclature_codes(value, text, listed)
        if bad:
            kept = [c for c in value if c not in bad]
            return f"nomenclature codes, not articles: {', '.join(map(str, bad))}", kept or None
    if field_name == "cert_number" and value:
        head = _before(str(evidence.get("verbatim") or ""), value)
        if head is not None and _OWN_REFERENCE.search(head):
            return "the declaration's own reference, not a certificate", None
    if (field_name in DATE_FIELDS and value
            and _template_date(value, str(evidence.get("verbatim") or ""))):
        return "a template's release date, not the document's", None
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
