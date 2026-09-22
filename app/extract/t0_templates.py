"""T0: deterministic extraction — free, always first (handbook 5, corpus 3/6).

Regex + filename signals + pdfplumber tables. Every hit carries evidence
{value, conf, tier='T0', verbatim, page}. Exact text confirmation gives conf 1.0;
a filename-only signal gives conf 0.6 (needs text to reach 1.0). High precision,
not exhaustive — gaps escalate to T1/T2.

Guardrails encoded here:
  G1 classify doc-class before extraction (business/MSDS are skipped)
  G2 QMS/ISO -> no REF list, coverage_scope = manufacturer (ref extraction gated)
  G3 "see attachment" detection is informational only (S1.5) — some manufacturers
     (e.g. GC) embed the referenced attachment as a later page of the SAME PDF,
     which extraction finds fine; a genuinely external/separate-file attachment
     naturally yields no match and falls through with no ref_list, same effect
  G5 REF codes captured verbatim, never normalised
  G6 multilingual keyword coverage (EN/DE/SL/FR/IT)

REF-list extraction is manufacturer-templated (S1.5, `app.extract.t0_layout`):
a page-1 anchor match selects a `ref_strategy` (table | text-column |
stitched-table); no match falls back to the generic table+text-fallback path
below, unchanged.

Keyword lists are engine-agnostic data; PyMuPDF is reached only via app.extract.pdf,
pdfplumber only in `extract_ref_list`.
"""

from __future__ import annotations

import os
import re

from datetime import date

import pdfplumber

from app.extract import pdf as pdfutil
from app import playbooks as playbooks_mod
from app.extract import t0_layout
from app.extract import udi

FILENAME_CONF = 0.6


def field_ev(value, conf: float, verbatim: str, page: int | None = None) -> dict:
    return {"value": value, "conf": conf, "tier": "T0", "verbatim": verbatim, "page": page}


# --------------------------------------------------------------------------- #
# keyword tables (order = precedence)
# --------------------------------------------------------------------------- #
_TYPE_MARKERS = [
    ("declaration of conformity", "DoC"),
    ("konformitätserklärung", "DoC"),
    ("konformitatserklarung", "DoC"),
    ("izjava o skladnosti", "DoC"),
    ("déclaration ue de conformité", "DoC"),
    ("déclaration de conformité", "DoC"),
    ("declaration de conformite", "DoC"),
    ("dichiarazione di conformità", "DoC"),
    ("dichiarazione di conformita", "DoC"),
    # `iso 13485` is the ONLY ISO signal. "quality management system" was one
    # too until 2026-08-20 and typed seven documents across five manufacturers
    # wrong -- doc 420 reached `production` as ISO off a title that reads "EU
    # Certificate / Quality Management System". The phrase names what a
    # certificate is ABOUT and is equally true of an ISO 13485 one, so it can
    # never settle which of the two a document IS. T0 declines instead.
    ("iso 13485", "ISO"),
    # `eu certificate` beside `ec certificate`: MDR-era notified bodies print
    # "EU Certificate" (Regulation 2017/745), MDD-era ones printed "EC". Without
    # it, dropping the QMS marker left docs 420 and 439 with no text signal at
    # all and they fell through to a filename guess.
    ("ec certificate", "EC"),
    ("eu certificate", "EC"),
    ("ce certificate", "EC"),
    # UKCA: the UK's post-Brexit equivalent, and a real certificate the corpus
    # holds (`LAB-UKCA-Certificate-795695-Brazil-EN.pdf`, which announces itself
    # as "UKCA Certificate - Full Quality Assurance System" on page 1). It was
    # missing from this table and the document only ever classified because the
    # old full-text promotion caught its filename.
    ("ukca certificate", "EC"),
    ("instructions for use", "IFU"),
    ("navodila za uporabo", "IFU"),
    ("gebrauchsanweisung", "IFU"),
]

_FILENAME_TYPE_MARKERS = [
    ("declaration", "DoC"),
    ("konformit", "DoC"),
    ("izjava", "DoC"),
    ("doc", "DoC"),
    ("13485", "ISO"),
    ("iso", "ISO"),
    ("ifu", "IFU"),
    ("navodila", "IFU"),
    ("cert", "EC"),
]

_MDR_TEXT = ["(eu) 2017/745", "regulation (eu) 2017/745", "2017/745"]
_MDD_TEXT = ["93/42/eec", "93/42/ewg", "directive 93/42", "rl 93/42", "93/42"]

# The MDR risk class a document prints about itself (Annex VIII). Deliberately
# label-anchored: a bare roman numeral is one of the commonest strings in these
# documents (annex numbers, rule numbers, list items), so only a value directly
# behind a class WORD is read. Measured over the dev corpus 2026-08-21: 445 of
# 694 declarations and 8 of 23 EC certificates carry such a label, with zero
# false positives in the sampled contexts.
_CLASS_VALUES = {"i": "I", "is": "Is", "im": "Im", "ir": "Ir",
                 "iia": "IIa", "iib": "IIb", "iii": "III"}
#: High, but deliberately not 1.0: the label is unambiguous, the reading is
#: still a regex over a page. Irrelevant to escalation either way -- the field
#: is in no `_ESCALATE_*` set, so its confidence can never buy an LLM call.
_CLASS_CONF = 0.95
#: Longest-first inside the alternation: `III` must be tried before `I`, or
#: "Class III" reads as "Class I" with `II` left over.
#:
#: `\s*` between the numeral and the subclass letter because typesetting splits
#: them and the split is not a different class. Kiwa Cermet prints `II a` and
#: `I m`; measured on doc 310, `IIa` occurred ZERO times and `II a` twice, so
#: both IIa readings were invisible and `I m` degraded to a bare `I`. That left
#: exactly one distinct class, the multi-class abstain guard never fired, and a
#: certificate covering three device types of two classes wrote `I` at 0.95 --
#: then machine-bound across 356 items. The guard was correct and starved.
#:
#: A bare `II` is deliberately absent: it is not an Annex VIII class, and the
#: trailing `\b` is what keeps the widening honest -- `Class I annex` cannot
#: become `Ia` and `Class I sterile` cannot become `Is`, because the boundary
#: fails mid-word and the engine falls back to the bare `I`.
_CLASS_RE = re.compile(
    r"(?:risk\s+class|class|klasse|razred|classe)\s*[:/]?\s*"
    r"(II\s*a|II\s*b|III|I\s*s|I\s*m|I\s*r|I)\b",
    re.IGNORECASE,
)
#: A class token followed by a slash- or comma-separated CONTINUATION is the
#: head of an enumeration, not a statement: pre-printed declaration forms carry
#: "Class Is/Ir/IIa/llb/lll" so one blank serves every product line. The
#: continuation test accepts `l` and `1` because that boilerplate is exactly
#: where OCR turns capital I into them (corpus docs 519/522).
_CLASS_ENUM_TAIL = re.compile(r"\s*[/,]\s*[Il1]", re.IGNORECASE)
#: A class token followed by plural "devices" is the regulation talking, not
#: the document: "For marketing of class III devices an additional Annex II
#: (4) certificate is mandatory" (doc 389), "Class I medical devices were
#: already converted to MDR" (doc 791), "If class III devices or class IIb
#: implantable devices referred to in Article 52" (docs 248/439/260). The one
#: measured counter-shape is a self-statement PHRASED plural -- "Being
#: classified as Class IIa devices and Rule 8" (doc 418) -- which the
#: `classified as` rescue below keeps. Measured over all 703 stored texts
#: 2026-08-24: this pair of rules changes four documents -- 389 III->abstain
#: and 791 I->abstain (both boilerplate reads), 248 and 439 abstain->IIa (QMS
#: certificates whose genuine device schedules are all IIa, previously starved
#: into abstention by the quoted-regulation classes) -- and loses nothing.
#:
#: The basis is the regulation itself. MDR Annex IV makes "the risk class of
#: the device in accordance with the rules set out in Annex VIII" a MANDATORY
#: element of a declaration of conformity, and Annex XII requires risk
#: classification on a notified body's certificates. The class is therefore a
#: required field ABOUT THE DEVICE the document covers -- and a conditional
#: clause about what class III devices in general need is not that field.
#: (An earlier revision of this comment cited "the Denis ruling" here. That
#: ruling was the opposite -- no suppression heuristic, the field is QA-only,
#: let the one conflict row stand -- and it is superseded by the Annex basis, not
#: implemented by this code. PHASES decision log, 2026-08-24.)
_CLASS_DEVICES_TAIL = re.compile(r"\s*(?:medical\s+|implantable\s+)?devices\b",
                                 re.IGNORECASE)
_CLASS_CLASSIFIED_AS = re.compile(r"classified\s+as\s*$", re.IGNORECASE)
#: Squeezes `II a` to `iia` for the `_CLASS_VALUES` lookup. Key only, never
#: the verbatim.
_WS = re.compile(r"\s+")

_BUSINESS = ["dobavnica", "račun", "racun", "ponudba", "invoice", "delivery note", "offer no", "quotation"]
_MSDS = ["safety data sheet", "sicherheitsdatenblatt", "varnostni list", "msds"]

_EXTERNAL_REF_MARKERS = [
    "according to the attachment", "see attached", "device schedule",
    "gemäß anhang", "gemass anhang", "artikelliste", "article list",
    "see item list", "see attachment",
]

_TO_LABELS = ["expiry date", "valid until", "declaration valid until", "gültig bis",
              "gültigkeitsdatum", "veljavnost do", "valid to"]
_FROM_LABELS = ["current issue date", "first issue date", "issue date", "date of issue",
                "effective date", "valid from", "valid as of", "izdano"]

#: Accent folding for label matching, added 2026-08-31. STRICTLY 1:1 -- every
#: mapping is one character to one character, so a folded string has the SAME
#: LENGTH as its original. That is load-bearing, not tidiness: `extract_dates`
#: takes the index from a `find()` on the folded text and then slices the
#: ORIGINAL, so a fold that changed length would read the wrong window. `ß` is
#: deliberately absent for exactly this reason -- it folds to `ss` and would
#: shift every index after it.
#:
#: Why it is needed: DENSTPLY declarations print "Diese Konformitatserklarung
#: ist gultig bis:" -- the font encoding strips the umlauts, so the page says
#: `gultig bis` while `_TO_LABELS` says `gültig bis`. The vocabulary was
#: already right; the matcher could not reach it. G7 in the corpus analysis
#: documents this artifact class ("Konjormitätserklärung", "ec Certifieate").
#: Slovenian carons are folded for the same reason.
_ACCENT_FOLD = str.maketrans({
    "ä": "a", "ö": "o", "ü": "u", "à": "a", "á": "a", "â": "a", "å": "a",
    "è": "e", "é": "e", "ê": "e", "ë": "e", "ì": "i", "í": "i", "î": "i",
    "ò": "o", "ó": "o", "ô": "o", "ø": "o", "ù": "u", "ú": "u", "û": "u",
    "ç": "c", "ñ": "n", "š": "s", "č": "c", "ć": "c", "ž": "z", "đ": "d",
})


def _fold_accents(s: str) -> str:
    """Accents flattened, length preserved. See `_ACCENT_FOLD`.

    Deliberately NOT merged into this module's older `_fold`, and deliberately
    not named `_fold`: that one also collapses whitespace, which CHANGES
    LENGTH, and `extract_dates` indexes the original text by an offset found in
    the folded copy. The two folds answer different questions and only this one
    may be used for an index."""
    return s.translate(_ACCENT_FOLD)

#: Month names in the three languages the corpus uses (EN/DE/SL, G6). Shared
#: keys are listed once -- `april`, `september`, `november` are spelled the same
#: in all three, `august` in two. `maerz` is the ASCII spelling that survives a
#: PDF with a broken umlaut, which G7 says this corpus produces.
_MONTH_NUMBERS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
    "januar": 1, "februar": 2, "märz": 3, "maerz": 3, "mai": 5, "juni": 6,
    "juli": 7, "oktober": 10, "dezember": 12,
    "marec": 3, "maj": 5, "junij": 6, "julij": 7, "avgust": 8,
}

#: Longest-first so `junij` is tried before `juni`. `\b` alone would also do it
#: (the `j` after `juni` denies the boundary), but ordering makes it explicit
#: rather than a property of the engine.
_MONTH_ALT = "|".join(
    re.escape(m) for m in sorted(_MONTH_NUMBERS, key=len, reverse=True))

#: A spelled-out date, both orders the corpus prints:
#:   `February 18, 2021` / `March 3 2024`  (EN; the comma is optional)
#:   `18. Februar 2021` / `1. marec 2021`  (DE/SL)
#: A DAY and a YEAR are both required around the month name, which is what
#: keeps `may` and `march` as ordinary English words -- "they may attend in
#: 2021" has no day, so it is not a date.
_LONG_DATE = (
    rf"\b(?:{_MONTH_ALT})\s+\d{{1,2}},?\s+\d{{4}}"
    rf"|\b\d{{1,2}}\.?\s*(?:{_MONTH_ALT})\s+\d{{4}}"
)

#: Numeric alternatives first, so a date this already read is matched by the
#: same branch it always was. Long-form was added 2026-08-31: `_DATE_RE` read
#: `2022-07-08` and `07.07.2027` and nothing else, so every spelled-out date was
#: unreadable even when its label matched. Measured that day, of the 620 text
#: PDFs carrying no `validity_from`, **464 (75%) contain a long-form date** --
#: VOCO 243, IVOCLAR 120, GC 59, KOMET 14, DENSTPLY 13, and five more brands.
#: `docs/dentalia-imports-corpus-analysis.md` listed `January 21, 2031` as an
#: observed format in July, next to the two that were implemented.
#: How far past a label to look for its date. Was 30 until 2026-08-31, which
#: cut `2020-08-06` to `2020-08-0` on nine DENSTPLY declarations and stored the
#: truncation. Measured before widening, over every text PDF and both label
#: lists (1956 comparisons): 1938 identical, 9 newly found, 0 lost, and the 9
#: CHANGED values were all this truncation -- each file name carries the true
#: date and confirms the wider read. Widening is a correctness fix here, not a
#: reach for more.
_LABEL_WINDOW = 60

_DATE_RE = re.compile(
    rf"(\d{{4}}-\d{{1,2}}-\d{{1,2}}|\d{{1,2}}[./]\d{{1,2}}[./]\d{{4}}|{_LONG_DATE})",
    re.IGNORECASE,
)

# Unlabelled place-and-date signature block: "Leuven, 12/02/2026",
# "Ljubljana, 3.4.2025". MDR Annex IV requires a place and date of issue on
# every declaration and most manufacturers print exactly this, with no label
# for a keyword list to find -- so the field fell through to the LLM, which
# stored it as an EXPIRY and made three GC declarations read as expired
# (documents 160/227/239). A declaration takes effect when it is issued, so
# this is validity_from (Denis's ruling 2026-08-13).
#
# Deliberately narrow, because a bare date is the commonest string in these
# documents and a loose pattern would turn lot numbers and print stamps into
# validity dates:
#   - the place must be a leading word or short phrase of LETTERS,
#   - a comma must separate it from the date (the signature-block convention),
#   - the date must END the line, so a table cell that happens to carry a
#     city and a date does not qualify.
# Month-name dates ("Leuven, 12 February 2026") are NOT matched here and stay
# with the LLM tiers -- see the open [extract-t0] entry on month-name parsing.
#
# The date does not sit flush against the comma, because the line is a FORM
# FIELD and the blank is drawn: GC prints "Leuven, ……………" and types the date
# into the dots, giving "Leuven, …10/10/2022…", "Leuven, .....10/10/2022..",
# "Leuven, 12/02/2026……………". Requiring adjacency cost 41 of GC's 149 documents
# -- every Class I DoC the brand has -- so a run of fill characters may sit on
# either side. Only [.…_ \t] qualify: letters or digits there would turn a
# table row carrying a city and a date into a validity date, which is the
# false positive the whole pattern exists to avoid.
_PLACE_AND_DATE = re.compile(
    r"^[ \t]*[A-Za-zÀ-ž][A-Za-zÀ-ž .'\-]{1,40},[ \t]*[.…_]*[ \t]*"
    rf"(\d{{4}}-\d{{1,2}}-\d{{1,2}}|\d{{1,2}}[./]\d{{1,2}}[./]\d{{4}}|{_LONG_DATE})"
    r"[ \t]*[.…_]*[ \t]*$",
    re.MULTILINE | re.IGNORECASE,
)
_CERT_LABEL = re.compile(
    r"(?:certificate\s*no|certificate\s*number|zertifikatsnr|cert\.?\s*no)\.?\s*:?\s*"
    r"([A-Za-z0-9][A-Za-z0-9 /.\-]{3,40})",
    re.IGNORECASE,
)
# Fallback for a bare "No. <value>" label (no "Certificate"/"Cert" prefix —
# e.g. DENSTPLY EC certs). Same-line only ([ \t]+, not \s+): a pdfplumber
# table header cell wraps as "Article\nNo." followed by the next cell's text
# on its own line, which must NOT be mistaken for a same-line declaration.
_CERT_LABEL_BARE_NO = re.compile(
    r"^No\.[ \t]+([A-Z0-9][A-Za-z0-9 /.\-]{3,40})$", re.MULTILINE
)
# [\s-]* (not \s*) between "basic" and "udi": IVOCLAR e.max labels it
# "Basic-UDI-DI" (hyphen, not whitespace). Optional "(GMN)"-style parenthetical
# between the label and the value: PLANMECA labels it "BASIC UDI-DI (GMN) <value>".
_UDI_LABEL = re.compile(
    r"basic[\s-]*udi[\s-]*di\s*(?:\([^)]*\))?\s*:?\s*([A-Za-z0-9+]{6,40})",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- #
# document-class gate (G1)
# --------------------------------------------------------------------------- #
def _fold(text: str) -> str:
    """Case- and whitespace-folded, so a phrase broken across a line or padded
    by a PDF's column spacing still matches.

    Every marker list in this module is a PHRASE matched by substring, and
    pymupdf renders a justified or tabular page as double-spaced text — so
    "Varnostni  list" did not match the `varnostni list` marker and two GC
    Safety Data Sheets were extracted at full T1+T2 price before the LLM
    refused them ([sds-reaches-extraction], 2026-08-13). How a PDF spaced its
    glyphs is not something the marker lists should have to encode."""
    return " ".join(text.lower().split())


#: Signals that a page is regulatory WITHOUT implying which type it is -- so
#: they belong here and NOT in `_TYPE_MARKERS`, which maps a marker to a type.
#:
#: Basic UDI-DI is an MDR Annex VI identifier. It appears on declarations and,
#: importantly, on their REF/UDI annexes, which carry no title of their own:
#: `533068_RA_812_Liste_DoC.pdf` is one page of columns headed "Basic UDI-DI |
#: UMDNS Code | MD Class | CE Sign | MDR" and nothing else.
#:
#: Measured 2026-09-02 before adding it -- 1.261 real archived PDFs against the
#: 13 PDFs the ranker chose off the open web, header window in both cases:
#:   real docs   570 carry it, and 43 of those carry NO other marker
#:   web junk      0 carry it
#: So it recovers 43 documents to the free path and admits nothing.
_COMPLIANCE_ONLY_MARKERS = ["basic udi-di"]


def _has_compliance_marker(low: str) -> bool:
    return (
        any(m in low for m, _ in _TYPE_MARKERS)
        or any(m in low for m in _MDR_TEXT + _MDD_TEXT)
        or any(m in low for m in _COMPLIANCE_ONLY_MARKERS)
    )


#: How many leading pages count as the document's header for classification.
#: Two, measured -- not one. A TÜV SÜD ISO 13485 certificate puts the holder and
#: the scope on a cover page and the standard's name on page 2, and the same
#: shape recurs across the corpus. Measured 2026-09-02 over 1.261 real archived
#: PDFs against 13 PDFs the ranker chose off the open web:
#:
#:     window   real kept        also admitted from the web set
#:          1   1074  (85%)      1  -- the genuine euronda DoC
#:          2   1115  (88%)      2  -- + a real DETAX IFU bundle on detax.com
#:          4   1121  (89%)      4  -- + a 45p third-party manual archive, a 56p bundle
#:
#: Two recovers 41 real documents and admits nothing that is not a genuine
#: document; four is where operation manuals start getting in for 6 more.
_HEADER_PAGES = 2


def _filename_type_marker(filename: str) -> str | None:
    """The type a filename claims, or None. Basename only -- see extract_type."""
    fl = os.path.basename(filename or "").lower()
    return next((v for m, v in _FILENAME_TYPE_MARKERS if m in fl), None)


def promote_doc_class(doc_class: str, header: str, full: str,
                      filename: str = "") -> str:
    """Decide whether an `unknown` page-1 classification may become
    `compliance-doc`. Page-1 evidence only.

    Until 2026-09-02 this read: `if doc_class == "unknown" and (type_val or
    "regulation" in fields)` -- where both came from the FULL text. That is how
    four web pages became documents. Solventum's Resources landing page carries
    nothing on page 1 and the phrase "instructions for use" at character 19.160,
    in the sentence "Access essential healthcare information, including
    instructions for use, regulatory details"; the promotion fired on it and the
    page was archived, typed `IFU`, staged, and approved into production.

    **Corroboration was tried first and does not work.** Requiring a second
    signal (a cited regulation, a certificate number, a notified-body number)
    still admitted a 320-page NSK operation manual, a 45-page third-party manual
    archive and a 44-page product info sheet -- because a manual cites MDR
    2017/745 legitimately, and a four-digit "notified body" match fires on years
    and part numbers (it hit 13 of 24 sample files, including HTML).

    Position is what separates them, and it is what a human uses: a declaration
    is *titled* "Declaration of Conformity"; a manual mentions the phrase on
    page 200. Measured over 1.261 real archived PDFs and the 13 PDFs the ranker
    chose off the open web:

                          real docs        web junk
        marker on page 1  1074  (85%)       1  (8%)   <- the genuine euronda DoC
        deeper only         48  ( 4%)       6 (46%)
        none               139  (11%)       6

    Everything that stays `unknown` is not rejected -- it goes to the T1 doc-class
    call, which can answer `not-a-compliance-document`. A false `unknown` costs
    about $0.001; a false `compliance-doc` costs a fabricated registry entry.
    """
    if doc_class != "unknown":
        return doc_class
    if _has_compliance_marker(_fold(header)):
        return "compliance-doc"
    # A file with NO TEXT LAYER cannot be a rendered web page, so the filename
    # is safe to trust here and nowhere else.
    #
    # This is the distinction the whole change turns on. The four web pages that
    # became documents on 2026-09-02 carried 30.791 characters of navigation --
    # HTML always renders to text. A scanned certificate carries none. Four real
    # ones sit in the test corpus right now (`93-42 Zertifikat_DE.pdf`,
    # `TD_14_Konformitaetserklaerung_Kl. IIa_2021_05_25.pdf`, `MDR Certificate IV
    # AG 2017_745.pdf`, `IMP - DOC - ATIS TX SURGICAL INSTRUMENTS`), every one of
    # them image-only, and every one classified today off its filename alone.
    #
    # Without this branch they fall to the doc-class call, which reads text,
    # finds none, and refuses them -- discarding genuine scanned certificates to
    # fix a problem they are not part of.
    if not _fold(header).strip():
        named = (_filename_type_marker(filename)
                 or extract_regulation("", filename) is not None)
        return "compliance-doc" if named else "unknown"
    return "unknown"


def classify_doc_class(text: str) -> str:
    """business-doc | msds | compliance-doc | unknown (from page-1 text)."""
    low = _fold(text)
    if any(m in low for m in _MSDS):
        return "msds"
    if any(m in low for m in _BUSINESS):
        return "business-doc"
    if _has_compliance_marker(low):
        return "compliance-doc"
    return "unknown"


# --------------------------------------------------------------------------- #
# field extractors (pure functions on text)
# --------------------------------------------------------------------------- #
def _first_hit(text: str, markers: list[str]) -> str:
    low = text.lower()
    for m in markers:
        i = low.find(m)
        if i != -1:
            return text[i : i + len(m)]
    return ""


#: A type marker within this many characters of the start is the document's own
#: title. Past it, the same phrase is a MENTION of some other document -- an IFU
#: citing the declaration that covers it.
#:
#: Measured, not guessed. Across the 655 corpus documents that hold text and
#: match any marker, the EARLIEST marker sits on page 1 in 647 and on page 2 in
#: 6 more; the deepest of those 653 is at character 3364. Exactly two documents
#: lie beyond -- NEODENT 520 (character 14163, page 5) and 521 (character
#: 64248, page 25) -- and both are IFUs that were typed `DoC` at confidence 1.0
#: off a citation. A first guess of 400 would have demoted a genuine TUV SUD
#: ISO 13485 certificate whose standard name prints at 1169, after the holder
#: and scope blocks.
_TYPE_TITLE_CHARS = 4000

#: Confidence for a marker found past the title region. Deliberately equal to
#: FILENAME_CONF and below `_TYPE_SETTLED_CONF`: a mention is a hint, so the
#: ladder revisits it instead of settling. NEODENT doc 520, an IFU, was typed
#: `DoC` at conf 1.0 off a citation 33k characters into the body.
_TYPE_MENTION_CONF = 0.6


def extract_type(text: str, filename: str = "", playbook=None) -> dict | None:
    """Type from the EARLIEST marker in the document, not the first in the list.

    List order used to decide, which meant a phrase's position in this file
    outranked its position on the page: `declaration of conformity` sits at
    index 0, so an IFU that cited one anywhere was a DoC. Position is the
    document's own ordering and it is the one that means something -- a title
    comes first. List order survives only as the tie-break for two markers
    starting at the same offset."""
    low = text.lower()
    # Authored markers are appended AFTER the globals, never merged into them:
    # the tie-break below is list index, so a global phrase beginning at the
    # same offset as an authored one still wins. Same additive contract as
    # `date_labels` -- an authored marker can add a read, never change one.
    markers = list(_TYPE_MARKERS)
    if playbook is not None and getattr(playbook, "type_markers", None):
        markers += [(m.lower(), doc_type)
                    for doc_type, spellings in playbook.type_markers.items()
                    for m in spellings]
    hits = []                        # (offset, list index, marker, value)
    for idx, (marker, value) in enumerate(markers):
        i = low.find(marker)
        if i != -1:
            hits.append((i, idx, marker, value))
    # `iso 13485` is a STANDARD NAME, and standards get cited. A CARL MARTIN MDD
    # declaration names the manufacturer's certification at character 943, ahead
    # of its own `konformitätserklärung` at 1023, so position alone reads the
    # citation as the class -- the same subject-versus-class confusion the
    # retired QMS marker caused, one phrase over. ISO therefore wins only when
    # no other type family is on the page at all: nothing else announces itself
    # by naming a standard, and no declaration or certificate stops being one
    # because it cites the QMS it was issued under. Measured over the 679
    # documents holding text: 131 carry an ISO marker beside another family and
    # in 130 of them ISO already loses on position, so this moves exactly one.
    if any(v != "ISO" for _, _, _, v in hits):
        hits = [h for h in hits if h[3] != "ISO"]
    best = min(hits, key=lambda h: (h[0], h[1])) if hits else None
    if best is not None:
        i, _, marker, value = best
        conf = 1.0 if i < _TYPE_TITLE_CHARS else _TYPE_MENTION_CONF
        return field_ev(value, conf, text[i : i + len(marker)])
    # BASENAME, never the whole path. `filename` is the archive_url, and
    # `archiving.archive_path` builds it as `{manufacturer}/{doc|ifu|iso}/...`
    # -- so every archived path carries a type marker as a DIRECTORY and
    # matching the whole string lets the shelf decide the document. 505 of 769
    # live archive_urls contain one (measured 2026-08-31).
    fl = os.path.basename(filename).lower()
    for marker, value in _FILENAME_TYPE_MARKERS:
        if marker in fl:
            return field_ev(value, FILENAME_CONF, f"filename:{filename}")
    return None


def extract_regulation(text: str, filename: str = "") -> dict | None:
    low = text.lower()
    mdr = any(m in low for m in _MDR_TEXT)
    mdd = any(m in low for m in _MDD_TEXT)
    if mdr and mdd:
        return None  # transition doc referencing both -> escalate to T1
    if mdr:
        return field_ev("MDR", 1.0, _first_hit(text, _MDR_TEXT))
    if mdd:
        return field_ev("MDD", 1.0, _first_hit(text, _MDD_TEXT))
    fl = os.path.basename(filename).lower()   # basename only -- see extract_type
    if "mdr" in fl or "2017-745" in fl or "2017_745" in fl:
        return field_ev("MDR", FILENAME_CONF, f"filename:{filename}")
    if "mdd" in fl or "93-42" in fl:
        return field_ev("MDD", FILENAME_CONF, f"filename:{filename}")
    return None


def _class_hits(text: str) -> tuple[dict[str, str], bool]:
    """`(distinct class -> its verbatim, whether the word appeared at all)`.

    Enumeration heads are dropped from the mapping but still count as an
    appearance: a form printing every class DID say the word, it just said
    nothing about this document."""
    seen: dict[str, str] = {}
    mentioned = False
    for m in _CLASS_RE.finditer(text):
        mentioned = True
        if _CLASS_ENUM_TAIL.match(text, m.end()):
            continue
        # Plural "devices" right after the token is the regulation being
        # quoted, unless a "classified as" immediately before makes it a
        # self-statement. Suppressed hits still count as an appearance, same
        # as enumeration heads: the document said the word.
        if (_CLASS_DEVICES_TAIL.match(text, m.end())
                and not _CLASS_CLASSIFIED_AS.search(text, max(0, m.start() - 40),
                                                    m.start())):
            continue
        # Whitespace is squeezed out of the KEY only. `m.group(0)` stays the
        # verbatim, so invariant 2's quote still matches the characters on the
        # page -- rewriting `II a` to `IIa` there would falsify the evidence a
        # reviewer checks the PDF against.
        seen.setdefault(_CLASS_VALUES[_WS.sub("", m.group(1)).lower()], m.group(0))
    return seen, mentioned


def mentions_class(text: str) -> bool:
    """Whether the document names a risk class at all, however unusably.

    Exists so a caller can tell the two ways `extract_stated_class` returns
    `None` apart -- silence versus an abstention -- which the history backfill
    has to report separately (CLAUDE.md: nothing skipped is silent)."""
    return _class_hits(text)[1]


def extract_stated_class(text: str) -> dict | None:
    """The MDR risk class the document states about itself, or None.

    QA/display only, and the contract says so in three places (PRD 5, GATE's
    `REQUIRED_FIELDS`, every `_ESCALATE_*` set): BC's `item_mirror.product_class`
    stays the source of truth for what class an ITEM is. This value exists to
    cross-check that column and to fill its blanks under human review.

    Abstains on more than one DISTINCT class. Two shapes produce that, and both
    are documents saying nothing about themselves: a pre-printed form listing
    the whole ladder (docs 519/522), and prose quoting the regulation -- an ISO
    certificate reciting "Class III devices, and Class IIb implantable devices"
    (doc 260). Repetition of ONE class is not an abstention: a declaration
    covering forty articles prints the same class forty times.

    Boilerplate quoting the regulation -- "For marketing of class III devices
    an additional Annex II (4) certificate is mandatory" -- used to be a KNOWN
    LIMIT: one class in a conditional clause about somebody else's device,
    confidently read as `III` (docs 389 and 791, the only two in the corpus).
    Resolved 2026-08-24 on the MDR Annex IV / Annex XII basis -- the class
    is a mandatory field about the device, not prose about the regulation --
    by `_CLASS_DEVICES_TAIL` +
    `_CLASS_CLASSIFIED_AS` in `_class_hits`: a class token followed by plural
    "devices" is suppressed unless "classified as" precedes it. Singular
    "device" is deliberately untouched -- "Classification: Class IIa Device
    Group" (doc 316's schedule) is a genuine per-device statement."""
    seen, _ = _class_hits(text)
    if len(seen) != 1:
        return None
    value, verbatim = next(iter(seen.items()))
    return field_ev(value, _CLASS_CONF, verbatim)


def extract_cert_number(text: str, playbook=None) -> dict | None:
    """Certificate number. The two global label regexes are tried first, then a
    playbook's `cert_number_pattern` if it authored one -- additive, so an
    authored pattern can only add a read, never change one that already worked.

    The pattern compiled and carries exactly one capture group -- both checked
    at playbook load -- but that group can still be OPTIONAL within the
    pattern (e.g. `Zertifikat\\s*(?:Nr\\.?\\s*([A-Z]{2}-\\d{6}))?`, matching a
    label with no number after it) and simply not participate in a given
    match. Whether a group can fail to participate is not decidable from `re`
    metadata at load time, so it is guarded here instead: a match whose group
    is `None` is treated as no match, never a crash -- playbook data must
    never raise on the runtime path."""
    m = _CERT_LABEL.search(text)
    if m:
        return field_ev(m.group(1).strip().rstrip("."), 1.0, m.group(0).strip())
    m = _CERT_LABEL_BARE_NO.search(text)
    if m:
        return field_ev(m.group(1).strip().rstrip("."), 1.0, m.group(0).strip())
    pattern = playbook.cert_number_pattern if playbook else None
    if pattern:
        m = re.search(pattern, text)
        if m and m.group(1) is not None:
            return field_ev(m.group(1).strip().rstrip("."), 1.0, m.group(0).strip())
    return None


#: Confidence by check-pair verdict. 1.0 is now *earned* rather than assumed:
#: it used to be returned unconditionally on whatever the label regex caught.
#: Anything we could not verify lands below `needs_escalation`'s 0.95, so the
#: document goes to a higher tier instead of feeding a corrupt identifier to
#: `validate.py`'s `basic-udi-di` link basis -- which Invariant 3 treats as
#: production-capable. Measured blast radius on the current corpus: 3 invalid
#: and 7 unscorable rows out of 352, so roughly ten extra T1 calls (~$0.09),
#: against ten identifiers we would otherwise have trusted at 1.0.
_UDI_CONF = {udi.VALID: 1.0, udi.UNSCORABLE: 0.9, udi.INVALID: 0.3}


def extract_basic_udi(text: str, result=None) -> dict | None:
    """Pull the Basic UDI-DI and score it against its own check pair.

    `result` is optional for the same reason `t0_extract`'s is: T0 runs from
    tools and tests with no job envelope, and an anomaly is a count, never an
    exception. The value is never rewritten or dropped -- a human reviewing the
    document still needs to see what was printed on it.
    """
    m = _UDI_LABEL.search(text)
    if not m:
        return None
    value = m.group(1).strip()
    verdict = udi.validate_basic_udi(value)
    if verdict.status == udi.INVALID and result is not None:
        result.anomaly("basic_udi_check_failed", subject=value,
                       detail={"agency": verdict.agency, "reason": verdict.detail})
    return field_ev(value, _UDI_CONF[verdict.status], m.group(0).strip())


#: The two spelled-out orders, split out so `_parse_date` reads the same shapes
#: `_DATE_RE` matches. Anchored like the numeric branches: the caller hands over
#: exactly what `_DATE_RE` captured.
_LONG_MDY = re.compile(rf"({_MONTH_ALT})\s+(\d{{1,2}}),?\s+(\d{{4}})", re.IGNORECASE)
_LONG_DMY = re.compile(rf"(\d{{1,2}})\.?\s*({_MONTH_ALT})\s+(\d{{4}})", re.IGNORECASE)


def _parse_date(s: str) -> str | None:
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        y, mo, d = m.groups()
    else:
        m = re.match(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", s)
        if m:
            d, mo, y = m.groups()
        else:
            # Spelled-out month. Returns None on an unknown name rather than
            # raising: `_DATE_RE` and `_MONTH_NUMBERS` are built from one dict,
            # so a miss here means they have drifted apart, and a date that
            # cannot be read must behave exactly like no date at all.
            m = _LONG_MDY.match(s)
            if m:
                mo_name, d, y = m.groups()
            else:
                m = _LONG_DMY.match(s)
                if not m:
                    return None
                d, mo_name, y = m.groups()
            mo = _MONTH_NUMBERS.get(mo_name.casefold())
            if mo is None:
                return None
    # The captured pieces have the SHAPE of a date; that is not the same as
    # being one. `2020-04-31` and `2020-08-00` both satisfy the regex, and the
    # second is what a window that cut `2020-08-06` to `2020-08-0` produced --
    # nine DENSTPLY declarations carried a wrong `validity_from` that way
    # (2026-08-31). A field with NO value escalates to a tier that can read the
    # page; a field with an impossible value is stored and believed, which is
    # strictly worse. So an unreal day is no day.
    try:
        return date(int(y), int(mo), int(d)).isoformat()
    except ValueError:
        return None


#: Characters that may sit between a date label and its value without breaking
#: the pairing. Anything else is CONTENT, and content means the value probably
#: belongs to a different field.
_LABEL_VALUE_SEPARATORS = re.compile(r"^[\s:.,\-\u2013\u2014]*$")


def _pairing_is_safe(field: str, between: str) -> bool:
    """May a label claim the date that follows it, given the text between them?

    The old rule was "first date within `_LABEL_WINDOW`", which assumes the
    label and its value are adjacent in PyMuPDF's linear text. In a two-column
    certificate they are not: doc 966 (a DQS ISO 13485 certificate) extracts as
    four labels, then the values column, so the first date after "Expiry date"
    is the EFFECTIVE date two rows above the expiry. We stored it as the expiry
    -- 19 months early, making a live certificate read as expired -- and doc 903
    was wrong by four and a half years the same way.

    The rule is asymmetric, and the asymmetry is deliberate. Measured across all
    184 registry documents holding a labelled T0 date (2026-09-03):

    - `validity_to` takes ONLY an adjacent date. 181 unchanged, 3 dropped, and
      all 3 were wrong. Reading a signature line as an expiry is the exact
      defect the `_PLACE_AND_DATE` fallback below already refuses to risk, and a
      wrong expiry raises a false compliance alarm.
    - `validity_from` also accepts a date with content between, PROVIDED that
      content holds no colon. MDR Annex IV mandates a place-and-date signature
      line, so "Schaan, 2021-05-25" legitimately follows a label; strict
      adjacency here would have dropped 21 correct documents to catch 1. A
      colon means a SECOND label claims that date (doc 913: " manufacturing
      date: "), and a signature line never has one. 64 unchanged, 1 dropped,
      and that 1 was wrong.

    A `False` here is not an error: the caller falls through to the NEXT label,
    which is what reads doc 317 correctly (a bilingual declaration whose English
    label is a bare heading and whose German label carries the value). Only when
    no label pairs safely does the field go unset and escalate to a paid tier.

    Design: `docs/superpowers/specs/2026-09-03-label-adjacency-and-type-conflation-design.md`
    """
    if _LABEL_VALUE_SEPARATORS.match(between):
        return True
    if field == "validity_to":
        return False
    return ":" not in between


#: Key under which `extract_dates` records the fields whose every labelled
#: pairing was refused for non-adjacency AND which it then produced no value
#: for. A LIST, deliberately: `gate.py`'s evidence writer skips any `fields`
#: entry that is not a dict, so this can never become an evidence row, and
#: `tiers.merge` copies `base` wholesale so it survives escalation to T1/T2.
#:
#: It has to travel this way because the thing it reports is a NON-EVENT.
#: `iso-without-standard-number` is computable from the stored fields because
#: a wrong type is still a stored value; a refused pairing stores nothing, so
#: VALIDATE has nothing to look at unless the extractor says so at the time.
DATE_LABEL_REFUSED_KEY = "_date_label_refused"


def extract_dates(text: str, playbook=None) -> dict:
    """Labelled validity dates, plus the place-and-date fallback for
    `validity_from`.

    `playbook.date_labels` EXTENDS the global label vocabulary; it never
    replaces it, and the globals are tried first so an authored label can add a
    read but never change one that already worked. Measured 2026-08-18: dates
    accounted for 208 of the 239 T2 vision calls on the corpus, which is what
    this key exists to reduce."""
    extra = (playbook.date_labels if playbook else None) or {}
    # Folded on BOTH sides: an authored label may carry the correct spelling
    # while the page carries the stripped one, or the reverse. The fold is
    # length-preserving, so `i` still indexes the original text.
    low = _fold_accents(text.lower())
    out: dict = {}
    refused: list[str] = []
    for field_name, labels in (
        ("validity_to", _TO_LABELS + [l.lower() for l in extra.get("to", [])]),
        ("validity_from", _FROM_LABELS + [l.lower() for l in extra.get("from", [])]),
    ):
        for lab in labels:
            lab = _fold_accents(lab)
            i = low.find(lab)
            if i == -1:
                continue
            window = text[i + len(lab) : i + len(lab) + _LABEL_WINDOW]
            m = _DATE_RE.search(window)
            if not m:
                continue
            # `continue`, not `break`: a label that cannot safely claim this
            # date must let the NEXT label try, or a bilingual document loses a
            # value its second label carries adjacently (doc 317).
            if not _pairing_is_safe(field_name, window[: m.start()]):
                if field_name not in refused:
                    refused.append(field_name)
                continue
            iso = _parse_date(m.group(1))
            if iso:
                # The REAL span, label through date -- never `label + " " +
                # date`, which asserts a sentence the document may not contain
                # and is what made doc 966's wrong value look quoted.
                span = text[i : i + len(lab) + m.end()]
                out[field_name] = field_ev(iso, 1.0, " ".join(span.split()))
                break

    # Fallback for validity_from ONLY, and only when no LABELLED start date was
    # found: an explicit "Date of issue" label is stronger evidence than a
    # positional guess, so it always wins. Never a fallback for validity_to --
    # reading a signature line as an expiry is the exact defect this fixes, and
    # a wrong expiry raises a false compliance alarm.
    if "validity_from" not in out:
        m = _PLACE_AND_DATE.search(text)
        if m:
            iso = _parse_date(m.group(1))
            if iso:
                # 0.95 not 0.9: it must CLEAR the escalation threshold.
                # validity_from was S0.4's biggest false-escalation driver, and
                # now that the field is pursued (tiers.py `_ESCALATE_ALWAYS`) a
                # sub-threshold T0 answer would send every signed declaration in
                # the corpus to a paid tier for a date we already read. The
                # pattern is narrow and MDR Annex IV mandates the place-and-date
                # line, so a match is strong evidence -- but still below a
                # LABELLED date, which stays 1.0 and wins above.
                out["validity_from"] = field_ev(iso, 0.95, m.group(0).strip())

    # Only the fields T0 refused AND could not read by any other route. A
    # refusal that a LATER LABEL then satisfied is working exactly as designed
    # (doc 317, a bilingual declaration whose English label is a bare heading
    # and whose German label carries the value) and must not be reported as a
    # problem -- a flag that fires on correct behaviour teaches people to
    # ignore flags. What survives here is "this document has a layout T0
    # cannot read", which is the signal that would justify authoring a
    # `t0_layout` template, and the cost signal too: the field either escalates
    # to a paid tier or goes unread.
    lost = [f for f in refused if f not in out]
    if lost:
        out[DATE_LABEL_REFUSED_KEY] = lost
    return out


def detect_external_ref_list(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in _EXTERNAL_REF_MARKERS)


#: Confidence for a coverage_scope DEDUCED from what T0 already holds. Clears
#: the ladder's escalation threshold and cfg.gate.high (both 0.95): paying a
#: vision tier to re-guess a deduction is how `item` reached 37 of 57 registered
#: documents, a value the committed ground truth never assigns (corpus_manifest:
#: 20 group, 25 manufacturer, 0 item). Deliberately short of the 1.0 given to a
#: value read verbatim off the page — this is inferred from the article list,
#: not read off a scope statement.
COVERAGE_DEDUCED_CONF = 0.97

#: A `type` at or above this settles at T0 and will not be revised by a later
#: tier (mirrors the ladder's own threshold). Below it, `type` is headed for
#: T1/T2 and anything deduced FROM it must travel with it.
_TYPE_SETTLED_CONF = 0.95


def derive_coverage_scope(type_value: str, has_ref_list: bool,
                          type_conf: float = 0.0) -> dict | None:
    """Coverage scope from the document type and whether T0 enumerated articles.

    `item` is never produced. It has no deterministic signal — a DoC listing one
    article and a DoC listing forty are structurally the same document, and the
    regulator explicitly permits either ("Multiple Basic UDI-DI can be in one
    DoC", EC UDI helpdesk) — and the committed ground truth assigns only `group`
    and `manufacturer`. Callers wanting "covers exactly one article" must read
    `len(ref_list)`; this field does not answer that and never did.

    `type_conf` guards the deduction, which is only ever as good as the `type` it
    keys off. A type T0 is itself unsure about (a filename match, FILENAME_CONF)
    is headed for T1/T2, and if it comes back EC or ISO the scope must be
    re-decided as manufacturer — so in that case the old escalating confidence is
    kept and both fields settle together. Without this guard a filename-typed
    document could sit at high confidence on `group` from a premise the ladder
    had already discarded, and an EC/ISO at manufacturer scope is precisely what
    routes to the C4 binding flow.
    """
    if type_value in ("ISO", "EC"):
        return field_ev("manufacturer", 1.0, f"type={type_value}")
    if type_value == "IFU":
        # Instructions for use are written for a product or a family and never
        # for a company: there is no such thing as an IFU for a manufacturer.
        # So the scope is deducible from the type alone, exactly as ISO/EC are,
        # and it must NOT be `manufacturer` -- that would route an IFU into the
        # C4 binding flow and attach it to the whole catalogue.
        #
        # Before this rule the field was simply absent, which made it a missing
        # REQUIRED_FIELD, which escalated to T1/T2 to go looking for a value
        # that was already determined. On STRAUMANN's two 109- and 143-page IFUs
        # that escalation is what met the context ceiling and dead-lettered
        # them, having spent a vision call to learn nothing.
        return field_ev("group", COVERAGE_DEDUCED_CONF, f"type={type_value}")
    if type_value == "DoC":
        if has_ref_list:
            settled = type_conf >= _TYPE_SETTLED_CONF
            return field_ev("group", COVERAGE_DEDUCED_CONF if settled else 0.9,
                            f"type=DoC ref=True type_conf={type_conf}")
        # T0 finding no REF list does NOT mean the document has none (the corpus
        # holds a DoC whose ~125 codes T0 cannot parse). Stays low so it
        # escalates: a wrong `manufacturer` links the doc to the entire catalogue.
        return field_ev("manufacturer", 0.6, "type=DoC ref=False")
    return None


# --------------------------------------------------------------------------- #
# REF-list extraction (pdfplumber tables) — verbatim, no normalisation (G5)
# --------------------------------------------------------------------------- #
_REF_HEADER = re.compile(r"ref|article|artikel|art\.?\s*no|cat\.?\s*no|bestell|id\b", re.IGNORECASE)


def _ref_column(table: list[list]) -> int | None:
    """First header cell matching `_REF_HEADER`, preferring a column whose
    header does NOT also read as a Basic-UDI-DI column. A multi-language UDI
    header cell (e.g. French "IUD-ID de base") can contain a literal "ID"
    substring that false-matches the bare `id\\b` keyword; Basic UDI-DI is a
    distinct field extracted separately (`extract_basic_udi`) and must never
    be picked as the REF-code column."""
    header = table[0] if table else []
    matches = [idx for idx, cell in enumerate(header) if cell and _REF_HEADER.search(str(cell))]
    if not matches:
        return None
    non_udi = [idx for idx in matches if "udi" not in str(header[idx]).lower()]
    return non_udi[0] if non_udi else matches[0]


#: A Basic UDI-DI as printed (GS1 "++" prefix). It is a real identifier, but it
#: is `extract_basic_udi`'s field, not a REF code, and it sits in a neighbouring
#: column of the very same tables. Letting it into ref_list means matching a UDI
#: against catalogue `mfr_ref` values, which can never hit.
_UDI_PREFIX = "++"

#: Two or more consecutive lowercase letters — a prose word. Deliberately NOT a
#: whitespace test: KOMET's ISO bur codes ("104 H251EF 060") contain spaces and
#: are legitimate REFs, so rejecting whitespace would drop 20 real codes. This
#: catches table cells holding a sentence ("CE 01488 issued by BSI (2797)",
#: "According to the Attachment") without touching any real code, every one of
#: which is digits, uppercase letters, dots, hyphens and slashes.
_PROSE_WORD = re.compile(r"[a-z]{2,}")


def _looks_like_ref(val: str, pattern: str | None = None) -> bool:
    """Is this table cell a REF code? Values are kept verbatim (G5), so this
    only ever rejects — it never rewrites. Called by `_ref_from_tables` here
    and by `ref_from_stitched_tables` in `t0_layout` (imported rather than
    redefined) -- both ran these generic tests before `ref_pattern` existed, so
    threading a pattern through them is pure narrowing on an unchanged gate.

    `ref_from_text_columns` (also in `t0_layout`) does NOT call this function.
    It never ran these generic tests, so composing them in now -- toggled by
    nothing but whether a playbook happens to author a `ref_pattern` -- would
    be a hidden behaviour change (S1.7 review, 2026-08-19): a candidate its own
    `field_pattern`/`field_index` selection already trusts could lose an
    authored `ref_pattern` match for a reason (length, a prose-looking run, a
    UDI prefix) invisible from the pattern itself. It applies an authored
    `ref_pattern` as a bare `re.search` instead, narrowing by the authored
    shape alone.

    `pattern` is a playbook's authored `ref_pattern`, applied AFTER the generic
    tests here and never instead of them: it can only narrow. Measured
    2026-08-18, T0 read `ref_list` on 18% of corpus documents -- the worst
    field in the ladder, and the one an exact shape helps most."""
    if not re.search(r"\d", val) or len(val) > 30 or "\n" in val:
        return False
    if val.startswith(_UDI_PREFIX):
        return False
    if _PROSE_WORD.search(val):
        return False
    return re.search(pattern, val) is not None if pattern else True


# A "bare code" line: a single token with a digit (VOCO 1800, KOMET 9553.204.060,
# STRAUMANN 033.602S). Dates and footer keywords are excluded so the run stops
# cleanly at the end of the REF section.
_REF_CODE_LINE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-/]{1,19}$")
_DATE_LIKE = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}$|^\d{1,2}[./]\d{1,2}[./]\d{4}$")
_FOOTER_WORDS = ("signature", "unterschrift", "podpis", "datum", "ort", "place", "seite", "page ")


def _ref_from_tables(path, ref_pattern: str | None = None) -> tuple[list[str], int | None]:
    codes: list[str] = []
    page_hit: int | None = None
    with pdfplumber.open(pdfutil.resolve_local(str(path))) as pl:
        for pnum, page in enumerate(pl.pages, start=1):
            for table in page.extract_tables() or []:
                col = _ref_column(table)
                if col is None:
                    continue
                for row in table[1:]:
                    if col < len(row) and row[col]:
                        val = str(row[col]).strip()
                        if _looks_like_ref(val, ref_pattern):
                            codes.append(val)
                            page_hit = page_hit or pnum
    return codes, page_hit


def _ref_from_text(page_texts: list[str]) -> tuple[list[str], int | None]:
    """Fallback for DoCs that render the REF list in the text layer under a
    'REF #' header (e.g. VOCO): collect bare-code lines, stop at a footer."""
    for pnum, text in enumerate(page_texts, start=1):
        lines = [ln.strip() for ln in text.splitlines()]
        start = None
        for i, ln in enumerate(lines):
            low = ln.lower()
            if low.startswith("ref") and ("#" in ln or "article" in low or low in ("ref", "ref no", "ref no.")):
                start = i + 1
                break
        if start is None:
            continue
        codes: list[str] = []
        for ln in lines[start:]:
            if not ln:
                continue
            if any(w in ln.lower() for w in _FOOTER_WORDS):
                break
            if _DATE_LIKE.match(ln):
                continue
            if _REF_CODE_LINE.match(ln) and re.search(r"\d", ln):
                codes.append(ln)
        if codes:
            return codes, pnum
    return [], None


#: `Codici / Codes:` on the Kiwa Cermet EC-certificate annex, and the bare
#: English half for issuers who print only that. Anchored to the whole line so
#: a sentence merely containing the word "codes" is not a label.
_CODE_LABEL = re.compile(r"^\s*(?:codici\s*/\s*)?codes?\s*:\s*$", re.IGNORECASE)
#: One code. Digits only, four or more: the annex's last pair ends
#: `901460/10003xxx`, a number the issuer had not assigned, and a looser rule
#: puts the literal `10003xxx` into the registry as a REF.
_CODE_TOKEN = re.compile(r"^\d{4,}$")


def _ref_from_labelled_codes(page_texts: list[str]) -> tuple[list[str], int | None]:
    """Codes on the line ABOVE a `Codici / Codes:` label.

    Third and last fallback in `extract_ref_list`, for the two-column
    certificate annex that defeats both of the others: there is no table for
    pdfplumber to find and no `REF #` header for `_ref_from_text` to key on.

    Why the line above and not below. The layout is a form whose value cell
    extracts before its label cell, so doc 310 reads `901595/10004798,
    901596/10004797` and only THEN `Codici / Codes:`. Looking forward from the
    label lands on the next device's heading.

    Codes print as `catalogue/internal` pairs and both halves are emitted --
    which one matches Dentalia's numbering is the matching layer's business
    (invariant 3 establishes the manufacturer before any number is compared),
    and picking here would silently drop the half that does. On doc 310 that is
    `900748`, the only one of the fifteen that is stocked: MI VARNISH MINT
    35KOS, whose BC class IIa agrees with the annex's `II a`.

    The digits-only token rule doubles as the guard against harvesting prose: a
    label whose preceding line is a device name (`Dental Prescale II`, the one
    covered device with no codes) yields nothing rather than a REF."""
    for pnum, text in enumerate(page_texts, start=1):
        lines = text.splitlines()
        codes: list[str] = []
        for i, ln in enumerate(lines):
            if i == 0 or not _CODE_LABEL.match(ln):
                continue
            for part in lines[i - 1].split(","):
                for half in part.split("/"):
                    half = half.strip()
                    if _CODE_TOKEN.match(half):
                        codes.append(half)
        if codes:
            return codes, pnum
    return [], None


def extract_ref_list(
    path, *, template: t0_layout.Template | None = None,
    playbook: playbooks_mod.Playbook | None = None,
) -> dict | None:
    """REF codes, kept exactly as read (G5). `template.ref_strategy` selects a
    manufacturer-specific parser (S1.5, `app.extract.t0_layout`); with no
    template (or `ref_strategy == "table"`) this is the original generic path:
    pdfplumber tables first, then a text-layer fallback for header-plus-bare-
    code layouts.

    `playbook.ref_pattern`, when present, is forwarded to every `_looks_like_ref`
    call site below regardless of which strategy runs -- an authored shape
    narrows the same way whether the manufacturer also has a T0 layout template
    or not. The text-layer fallback (`_ref_from_text`) is deliberately excluded:
    it is not one of the three `_looks_like_ref`-gated strategies."""
    ref_pattern = playbook.ref_pattern if playbook else None
    if template is not None and template.ref_strategy == "text-column":
        codes, page_hit = t0_layout.ref_from_text_columns(
            path, template.ref_strategy_config, ref_pattern=ref_pattern
        )
    elif template is not None and template.ref_strategy == "stitched-table":
        codes, page_hit = t0_layout.ref_from_stitched_tables(path, ref_pattern=ref_pattern)
    else:
        codes, page_hit = _ref_from_tables(path, ref_pattern=ref_pattern)
        if not codes:
            doc = pdfutil.open_doc(str(path))
            texts = pdfutil.page_texts(doc)
            codes, page_hit = _ref_from_text(texts)
            # Third fallback, deliberately last: the certificate-annex layout
            # that has neither a table nor a `REF #` header. Everything that
            # reads a ref list today keeps precedence over it.
            if not codes:
                codes, page_hit = _ref_from_labelled_codes(texts)
    if not codes:
        return None
    seen: set[str] = set()
    uniq = [c for c in codes if not (c in seen or seen.add(c))]
    return field_ev(uniq, 0.9, f"{len(uniq)} REF codes", page=page_hit)


# --------------------------------------------------------------------------- #
# orchestrator
# --------------------------------------------------------------------------- #
#: A playbook name must be at least this long to be usable as identity. GC's
#: alias is the bare string "GC", which matches inside "GCF", "MAGCARE" and any
#: table header -- a false-positive generator, not a manufacturer. Legal names
#: (the `manufacturer` field, and aliases like "Gebr. Brasseler GmbH & Co. KG")
#: comfortably clear it.
_MIN_IDENTITY_LEN = 6




#: A representative's name printed on a declaration is not the manufacturer's.
#: Both LLM prompts bar the role by LABEL, never by company name, and T0 has to
#: make the same distinction or it will promote a distributor.
#: A bounded lazy gap sits between the verb and the noun, because the corpus
#: writes "authorized eu-representative", "authorised UK representative" and
#: plain "authorised representative" interchangeably. The gap must span a HYPHEN
#: as well as a space: `eu-representative` is a single token, so a
#: `(?:[\\w-]+\\s+)?` qualifier swallows the word "representative" itself and the
#: pattern then matches none of the 41 Dentsply files this exists for.
_REP_LABEL = re.compile(
    r"author[iy]?s?z?ed[\s\w-]{0,20}?represent"
    r"|\bec\s*[-\s]?rep\b|\buk\s*r\.?p\.?\b"
    r"|bevollm(?:\u00e4|ae)chtigt|mandataire",
    re.IGNORECASE,
)

#: How far after a representative label its name is still considered part of
#: that block. Measured on the corpus: the entity name follows the label within
#: a line or two, well inside 300 characters.
_REP_BLOCK_CHARS = 300

#: Confidence when the name is printed somewhere OTHER than a representative
#: block. Above the 0.95 escalation threshold, so T0 answers alone.
#:
#: Justified by measurement, not by taste (2026-08-17, whole registry): T0 named
#: a manufacturer on 285 of 326 documents and agreed with what the paid tiers
#: concluded on **284 of 285 (99,6%)** once both sides are compared THROUGH the
#: alias table -- the raw string comparison scores 49% only because T0 answers
#: with the playbook key (`IVOCLAR`) and the LLM with the legal name (`Ivoclar
#: Vivadent AG`). One genuine disagreement, doc 89. Every document escalated on
#: this field before, so it was the single biggest cause of escalation in the
#: corpus.
MFR_TRUSTED_CONF = 0.97

#: Confidence when the ONLY occurrence sits inside a representative block --
#: deliberately below the escalation threshold, so a tier that can read the
#: layout confirms it.
#:
#: This is the guard the flat 0.9 was carrying, kept because it is live rather
#: than historical. Measured over all 1.303 readable corpus PDFs on 2026-08-17:
#: `Dentsply IH Limited` (Stonehouse, UK) appears in **41 files, and in every
#: one of them it is the post-Brexit UK representative with no real DENTSPLY
#: entity anywhere in the document** -- yet the identity match fires on all 41.
#: Promoting those without a second read is exactly the failure the original
#: flat constant existed to prevent. Note the exclusion cannot be by name
#: prefix: `DENTSPLY IH AB` (Mölndal) and `DENTSPLY IH Inc.` (Waltham) are real
#: certificate holders, so a `Dentsply IH` rule would discard real
#: manufacturers. Position relative to the ROLE LABEL is what separates them.
MFR_REP_ONLY_CONF = 0.9


def _named_outside_rep_block(haystack: str, spans) -> bool:
    """Whether any match sits outside every authorised-representative block.

    One occurrence away from a representative label is enough: a declaration
    that names the manufacturer in its header and its representative in the
    footer is the ordinary case, and must not be penalised for carrying both."""
    labels = [m.start() for m in _REP_LABEL.finditer(haystack)]
    if not labels:
        return True
    return any(
        not any(lab <= start <= lab + _REP_BLOCK_CHARS for lab in labels)
        for start, _ in spans
    )


def extract_manufacturer(text: str, playbooks) -> dict | None:
    """The document's own manufacturer, matched against the LEGAL NAMES the
    playbooks declare.

    Never `match.anchors`: those are document-management artifacts and
    nomenclature strings ("otcs", "umdns code") that would resolve a Straumann
    DoC to KOMET. Never the folder name either -- that is the supplier, not the
    manufacturer, and the DENSTPLY corpus folder holds Maillefer, Sirona and VDW
    documents (BC codes 022, 012, 010).

    Abstains when two different manufacturers appear: a distributor declaration
    naming several companies, or a document citing another company's
    certificate, is ambiguous, and ambiguous is not a value.

    Confidence depends on WHERE the name is printed (`MFR_TRUSTED_CONF` /
    `MFR_REP_ONLY_CONF`). Until 2026-08-17 it was a flat 0.9, deliberately below
    the escalation threshold on the argument that a name on the page is not
    proof it is the manufacturer's -- it may be the authorised representative,
    which both prompts bar by ROLE, never by company name.

    Measurement split that argument in two, so the constant did too. Across the
    registry T0 agreed with what the paid tiers concluded on 284 of 285
    documents (99,6%, compared through the alias table), which made a flat 0.9
    the single largest cause of escalation in the corpus -- every document
    escalated on this field. But the original concern is live, not historical:
    re-measured the same day over all 1.303 readable corpus PDFs, `Dentsply IH
    Limited` (Stonehouse, UK) appears in 41 files and in EVERY one of them is
    the post-Brexit UK representative with no real DENTSPLY entity anywhere in
    the document -- and the identity match fires on all 41.

    So the position of the match decides: a name that appears anywhere outside a
    representative block is trusted outright, and a name whose only occurrence
    sits inside one keeps the old 0.9 and goes to a tier that can read the
    layout. Over the corpus that is 771 files answered by T0 alone and 81 held
    back, the 41 above among them.

    The exclusion CANNOT be by name prefix: `DENTSPLY IH AB` (Mölndal) and
    `DENTSPLY IH Inc.` (Waltham) appear in 4 files each as certificate holders
    and production sites, so a `Dentsply IH` rule would discard real
    manufacturers. Only position relative to the role label separates them.
    """
    haystack = _fold(text)
    hits = []
    matched_spans: list[tuple[int, int]] = []
    for pb in playbooks or ():
        for name in pb.names():
            if len(name) < _MIN_IDENTITY_LEN:
                continue
            folded = _fold(name)
            at = haystack.find(folded)
            if at != -1:
                hits.append(pb.manufacturer)
                matched_spans.append((at, at + len(folded)))
                break
    unique = sorted(set(hits))
    if len(unique) != 1:
        return None
    conf = MFR_TRUSTED_CONF if _named_outside_rep_block(haystack, matched_spans) \
        else MFR_REP_ONLY_CONF
    return field_ev(unique[0], conf, f"playbook identity: {unique[0]}")


#: TARGET fields `t0_extract` deliberately does NOT produce, with the reason.
#: Parity is a contract (ext-manufacturer plan): T0 produced 8 of 9 and silently
#: skipped one, so a tenth field could be half-wired without anything noticing.
#: A field belongs here or in `T0_PRODUCED` -- never in neither.
T0_EXEMPT = {
    # Cross-document references need the citation resolved against the registry,
    # which T0 cannot see. The LLM tiers read them off the page instead.
    "referenced_docs",
    # Optional by nature and the biggest false-escalation driver measured in
    # S0.4; T0 captures it opportunistically inside extract_dates when a real
    # validity-period start is printed, but never guarantees it.
    "validity_from",
}

#: TARGET fields `t0_extract` does produce. Kept beside T0_EXEMPT so the two
#: together must cover TARGET exactly.
T0_PRODUCED = {
    "type", "regulation", "cert_number", "basic_udi_di", "manufacturer",
    "validity_to", "coverage_scope", "ref_list",
    # T0 is the ONLY tier that ever produces this one: it is in no escalate
    # set, so nothing above T0 is ever asked for it.
    "stated_class",
}

def _ref_from_companion(companion_url: str, playbook: playbooks_mod.Playbook | None = None):
    """The REF list of a companion annex, tagged with the ANNEX's own handle.

    Komet issues a declaration and that declaration's product list as two files
    (`532624_RA_810_DoC_EU_SIGNED.pdf` / `532624_RA_812_Liste_DoC.pdf`); BACKFILL
    pairs them and passes the annex's handle on the payload. The evidence says
    where the value was read -- invariant 2 -- so a reviewer opening it lands on
    the annex, which is the page that actually holds the list.

    Reached only when the primary carries no list of its own. 21 of Komet's 102
    declarations print a complete list inline (measured 2026-08-18), and an
    annex must never overwrite a reading the document itself supports.

    `playbook` is the SAME manufacturer's steering playbook the primary read
    used -- the annex is that manufacturer's own document, so its `ref_pattern`
    (if any) applies here too. Omitting it would leave the annex path on the
    generic filter while the primary path uses the authored one, a silent split
    on Komet's own corpus (task 5)."""
    try:
        with pdfutil.open_doc(companion_url) as annex:
            template = t0_layout.match(pdfutil.first_page_text(annex))
        ev = extract_ref_list(companion_url, template=template, playbook=playbook)
    except Exception:
        return None
    if not (ev and ev.get("value")):
        return None
    ev = dict(ev)
    ev["archive_url"] = companion_url
    ev["verbatim"] = f"{len(ev['value'])} REF codes, read from the companion annex"
    return ev


#: Sentinel for `steering_playbook`'s `manufacturer_ev` parameter, distinct
#: from `None` -- `extract_manufacturer` legitimately RETURNS `None` (no
#: unique manufacturer matched, the common case: most of the corpus matches
#: none of the 11 playbooks, and an ambiguous multi-match document is a
#: documented outcome too). Defaulting `manufacturer_ev` to `None` collapsed
#: "the caller passed nothing" into "the caller passed a negative result",
#: so `t0_extract` handing over its own `None` read as unsupplied and
#: `steering_playbook` re-ran the full-alias scan anyway -- silently
#: reintroducing the double scan on exactly the documents it matters most
#: for. `is _UNSET` is the only check that tells the two apart.
_UNSET = object()


def steering_playbook(full_text: str, playbooks=None, override=None,
                      manufacturer_ev=_UNSET):
    """The playbook whose lexicons and hints apply to this document.

    `override` is an authoritative manufacturer the CALLER established before
    extraction (a group's `canonical_manufacturer`); it always wins, because
    T0's own text match is a guess and the group's is not. With no override this
    falls back to the same identity match `extract_manufacturer` performs.

    `manufacturer_ev` lets a caller that has ALREADY run `extract_manufacturer`
    (namely `t0_extract`) hand over that result instead of paying for a second
    full-text scan against every playbook alias -- `extract_manufacturer` is a
    substring search per alias, so running it twice per document doubles that
    cost for no new information. Standalone callers (and this function's own
    tests) omit it and get the original self-contained behaviour. Its default
    is the `_UNSET` sentinel, not `None`: `extract_manufacturer` can itself
    legitimately return `None` (no match), and that is a supplied answer, not
    an absent one -- see `_UNSET`'s own docstring.

    Returns None when nothing claims the document -- the common case, and the
    one that must behave exactly as T0 did before playbook lexicons existed."""
    if override is not None:
        return override
    if playbooks is None:
        playbooks = playbooks_mod.load_playbooks()
    ev = extract_manufacturer(full_text, playbooks) if manufacturer_ev is _UNSET else manufacturer_ev
    if not ev:
        return None
    return playbooks_mod.for_manufacturer(ev["value"], playbooks)


def t0_extract(doc, filename: str = "", result=None,
               companion_url: str | None = None,
               playbook: playbooks_mod.Playbook | None = None) -> tuple[str, dict]:
    """Return (doc_class, fields). Business/MSDS docs short-circuit with no
    fields. REF extraction is gated by type (G2: QMS/ISO never get a REF list).

    `result` is an optional `app.results.Result`: when given, a playbook REF
    template that matched but parsed nothing is recorded on it as a
    `t0_ref_template_miss` anomaly. It is optional because T0 is also called by
    tools and tests that carry no job envelope, and because an anomaly is a
    count, never an exception -- extraction proceeds either way.

    `playbook` is an optional authoritative override (the requesting group's
    manufacturer). Absent it, T0 resolves the steering playbook from the
    document's own text -- which is the live path, because every backfilled
    document carries `group_id: None`."""
    first = pdfutil.first_page_text(doc)
    full = "\n".join(pdfutil.page_texts(doc))

    doc_class = classify_doc_class(first)
    if doc_class in ("business-doc", "msds"):
        return doc_class, {}

    all_playbooks = playbooks_mod.load_playbooks()
    # Computed once and shared below: `extract_manufacturer` scans the full
    # text against every alias of every playbook, and `fields["manufacturer"]`
    # and `steering` both need its answer -- running it twice per document
    # would double that scan, on every one of the corpus's ~1.300 documents,
    # for no new information.
    mfr_ev = extract_manufacturer(full, all_playbooks)
    steering = steering_playbook(full, all_playbooks, override=playbook, manufacturer_ev=mfr_ev)

    fields: dict = {}
    # Identity is read from the document's OWN text, never the filename: the
    # corpus folder is the supplier, not the manufacturer. `manufacturer` is
    # computed first (above) because `steering` reads it too; the extractors
    # below are otherwise order-independent (each reads only `full`).
    for name, ev in (
        ("manufacturer", mfr_ev),
        ("type", extract_type(full, filename, steering)),
        ("regulation", extract_regulation(full, filename)),
        ("cert_number", extract_cert_number(full, steering)),
        ("basic_udi_di", extract_basic_udi(full, result)),
        # Opportunistic, exactly like `referenced_docs`: in no escalate set, so
        # a document that does not print its class simply has none recorded and
        # no tier is ever paid to go looking.
        ("stated_class", extract_stated_class(full)),
    ):
        if ev:
            fields[name] = ev
    fields.update(extract_dates(full, steering))

    type_val = fields.get("type", {}).get("value")

    # G2: only product DoCs (or unclassified) get REF extraction. A "see
    # attachment"/"according to the attachment" marker (detect_external_ref_list)
    # does NOT gate extraction: some manufacturers (e.g. GC) embed that
    # attachment as a later page of the same PDF, which pdfplumber finds fine —
    # a genuinely external (separate-file) attachment naturally yields no table
    # match and falls through to no ref_list below (corpus-verify followup
    # 2026-07-15).
    ref_ev = None
    if type_val in (None, "DoC") and filename:
        template = t0_layout.match(first)
        try:
            ref_ev = extract_ref_list(filename, template=template, playbook=steering)
        except Exception:
            ref_ev = None
        # A template matched this document and its parser still returned
        # nothing. That is OUR parser breaking -- a layout change on the
        # manufacturer's side -- and until now it was indistinguishable from
        # "this document lists no articles": the document fell through quietly
        # to the LLM, which costs money and answers with a capped SAMPLE where
        # T0 enumerates. The external-attachment marker deliberately does not
        # suppress it: GC embeds that attachment as a later page of the same
        # PDF, so the marker is present on documents T0 parses perfectly and
        # would hide the very regressions this counts.
        if template is not None and result is not None and not (ref_ev and ref_ev["value"]):
            result.anomaly(
                "t0_ref_template_miss", subject=template.slug,
                detail={"manufacturer": template.manufacturer,
                        "ref_strategy": template.ref_strategy,
                        "archive_url": str(filename)},
            )
    # The declaration's list lives in a separate file. Deliberately AFTER the
    # template-miss anomaly above: a Komet declaration legitimately parses to
    # nothing, and its annex is the answer rather than the regression.
    if companion_url and not (ref_ev and ref_ev.get("value")):
        ref_ev = _ref_from_companion(companion_url, playbook=steering) or ref_ev
    if ref_ev:
        fields["ref_list"] = ref_ev

    if type_val:
        cs = derive_coverage_scope(
            type_val, bool(ref_ev and ref_ev["value"]),
            type_conf=fields.get("type", {}).get("conf", 0.0),
        )
        if cs:
            fields["coverage_scope"] = cs

    header = "\n".join(pdfutil.page_texts(doc)[:_HEADER_PAGES])
    doc_class = promote_doc_class(doc_class, header, full, filename)
    return doc_class, fields
