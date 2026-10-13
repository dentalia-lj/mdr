"""The text ladder, T-3 (extraction-ladder plan Task 4; PRD v3 section 5,
amended 2026-10-12): T1 reads the whole text, the evidence checks settle what
the text proves, a cheap image read takes what stays open, and Sonnet only
what that leaves.

The documents are real PyMuPDF documents built in memory, so the page texts,
page classes and page count the checks read are the real ones. T0 is replaced
where a test needs a given starting point, and the LLM is a stub: no test here
makes a call. The cases come from the eval (`docs/private/2026-10-08-extraction-
ladder-research.md` sections 13-15): a notified-body number in `cert_number`,
an OCR layer reading `OOIYC` for `001YC`, a Basic UDI-DI label on a multi-UDI
declaration, a certificate cited on page 26.
"""

from __future__ import annotations

import pymupdf
import pytest

from app.extract import pdf, tiers
from app.extract.tiers import NOT_A_DOCUMENT

# --- documents ---------------------------------------------------------------

#: Neutral filler: no word in it is a field label (`checks.LABELS`), so a page
#: reaches the 200 characters of a text page without labelling anything.
FILLER = "Lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod."

#: Page 1 of a declaration. Labels present: date, certificate, REF,
#: regulation, manufacturer. Absent: an expiry phrase and a UDI label.
DOC_PAGE = """EU Declaration of Conformity
Manufacturer: GC EUROPE N.V.
Regulation (EU) 2017/745
Certificate G1 021108 0031
Notified body 0482
Date of issue: 2025-08-25
REF 003477 003478"""

#: A scanned page in a page list.
IMAGE = object()


def _png() -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 80), False)
    pix.clear_with(200)
    return pix.tobytes("png")


def _doc(*pages) -> pymupdf.Document:
    """One page per argument: a string is a text page (padded with filler to a
    real text layer), `IMAGE` a scanned page with no text layer."""
    doc = pymupdf.open()
    for spec in pages:
        page = doc.new_page()
        if spec is IMAGE:
            page.insert_image(pymupdf.Rect(0, 0, 595, 842), stream=_png())
            continue
        lines = spec.splitlines() + [FILLER] * 4
        for i, line in enumerate(lines):
            page.insert_text((40, 60 + 16 * i), line, fontsize=9)
    return doc


# --- stubs -------------------------------------------------------------------

FAILED = "failed"


def ev(value, verbatim=None, page=1, conf=0.9, tier="T1"):
    return {"value": value, "conf": conf, "tier": tier,
            "verbatim": str(value) if verbatim is None else verbatim,
            "page": page, "model_id": "stub-model"}


class TextLlm:
    """Answers by label. `answers[label][field]` is a tuple of `ev` arguments,
    and is returned whether the field was asked or not, as a real read
    answers every field the response schema holds; an asked field with no
    answer given is null, as a real read gives it. `FAILED` answers nothing.
    Records every read as (label, missing, read options)."""

    def __init__(self, answers=None, text_class=None, image_class=None):
        self.answers = answers or {}
        self.calls: list[tuple[str, tuple[str, ...], dict]] = []
        self.classified: list[str] = []
        if text_class is not None:
            self.classify = lambda doc, filename: self._classify("text", text_class)
        if image_class is not None:
            self.classify_image = lambda doc, filename: self._classify("image", image_class)

    def _classify(self, kind, answer):
        self.classified.append(kind)
        return answer, "EU Declaration of Conformity"

    def extract(self, label, doc, filename, missing, **kw):
        self.calls.append((label, tuple(missing), kw))
        given = self.answers.get(label, {})
        if given == FAILED:
            return {}
        tier = "T1" if label == "T1" else "T2"
        out = {f: ev(None, "", None, 0.0, tier) for f in missing}
        for f, spec in given.items():
            out[f] = ev(*spec, tier=tier) if isinstance(spec, tuple) else ev(spec, tier=tier)
        return out

    def asked(self, label):
        return next((set(m) for lb, m, _ in self.calls if lb == label), None)

    def options(self, label):
        return next((kw for lb, _, kw in self.calls if lb == label), None)

    @property
    def labels(self):
        return [lb for lb, _, _ in self.calls]


@pytest.fixture
def t0(monkeypatch):
    """Set T0's answer: `t0(fields, doc_class)`."""
    def _set(fields=None, doc_class="compliance-doc"):
        monkeypatch.setattr(
            tiers, "t0_extract",
            lambda doc, filename="", result=None, companion_url=None: (
                doc_class, {k: dict(v) for k, v in (fields or {}).items()}))
    return _set


def t0ev(value, conf, verbatim=None, page=None):
    return {"value": value, "conf": conf, "tier": "T0",
            "verbatim": f"t0 {value}" if verbatim is None else verbatim, "page": page}


def run(doc, llm, **kw):
    return tiers.run_extraction(doc, "d.pdf", llm=llm, threshold=0.95, **kw)


#: T0 settles what a T1 test is not about, so the open set stays small.
SETTLED_T0 = {
    "type": t0ev("DoC", 1.0), "regulation": t0ev("MDR", 1.0),
    "manufacturer": t0ev("GC EUROPE N.V.", 0.97),
    "validity_from": t0ev("2025-08-25", 1.0), "validity_to": t0ev(None, 1.0),
    "cert_number": t0ev("G1 021108 0031", 1.0),
    "coverage_scope": t0ev("group", 0.97),
    "ref_list": t0ev(["003477", "003478"], 1.0), "basic_udi_di": t0ev(None, 1.0),
}


def _only_open(*fields):
    """T0 holding everything at 1.0 except `fields`."""
    return {f: v for f, v in SETTLED_T0.items() if f not in fields}


# --- T1 and the checks ---------------------------------------------------------

def test_t0_settled_fields_are_never_asked(t0):
    t0(_only_open("cert_number", "validity_to"))
    llm = TextLlm({"T1": {"cert_number": ("G1 021108 0031", "Certificate G1 021108 0031")}})
    _, _, used = run(_doc(DOC_PAGE), llm)
    assert llm.asked("T1") == {"cert_number", "validity_to"}
    assert used == ["T0", "T1"]          # both settled: one proven, one a provable null


@pytest.mark.parametrize("field, answer, settles", [
    ("validity_from", ("2025-08-25", "Date of issue: 2025-08-25"), True),
    ("validity_from", ("2025-08-26", "Date of issue: 2025-08-25"), False),   # date not in quote
    ("cert_number", ("G1 021108 0031", "Certificate G1 021108 0031"), True),
    ("cert_number", ("0482", "Notified body 0482"), False),                  # a notified body
    ("manufacturer", ("GC EUROPE N.V.", "Manufacturer: GC EUROPE N.V."), True),
    ("manufacturer", ("Ivoclar Vivadent AG", "Ivoclar Vivadent AG"), False),
    ("regulation", ("MDR", "Regulation (EU) 2017/745"), True),
    ("regulation", ("MDD", "Regulation (EU) 2017/745"), False),
    ("type", ("DoC", "EU Declaration of Conformity"), True),                 # quote on the page
    ("type", ("DoC", "Declaration printed somewhere else"), False),
    ("coverage_scope", ("group", "REF 003477 003478"), True),
    ("ref_list", (["003477", "003478"], "REF 003477 003478"), True),
    ("ref_list", (["003477", "999999"], "REF 003477 003478"), False),        # 50% printed
])
def test_a_value_the_text_proves_settles_after_t1(t0, field, answer, settles):
    t0(_only_open(field))
    llm = TextLlm({"T1": {field: answer}, "T2-first": {}, "T2": {}})
    _, fields, used = run(_doc(DOC_PAGE), llm)
    assert ("T2-first" not in used) is settles
    if settles:
        assert fields[field]["step"] == "T1"


@pytest.mark.parametrize("field, pages, settles", [
    ("validity_to", (DOC_PAGE,), True),              # no expiry label anywhere
    ("basic_udi_di", (DOC_PAGE,), True),             # no UDI label anywhere
    ("basic_udi_di", (DOC_PAGE + "\nBasic UDI-DI: see annex",), False),
    ("cert_number", (DOC_PAGE,), False),             # "Certificate" is printed
    ("validity_to", (DOC_PAGE, IMAGE), False),       # a page T1 could not read
], ids=["no-expiry-label", "no-udi-label", "udi-label", "cert-label", "image-page"])
def test_t1s_empty_answer_settles_only_where_the_text_cannot_hold_the_field(
        t0, field, pages, settles):
    t0(_only_open(field))
    llm = TextLlm({"T1": {}, "T2-first": {}, "T2": {}})
    _, _, used = run(_doc(*pages), llm)
    assert ("T2-first" not in used) is settles


def test_text_beyond_the_budget_is_unread(t0, monkeypatch):
    """The cut text may hold the field: T1's null settles nothing, and two
    empty answers settle nothing either, so Sonnet is asked."""
    monkeypatch.setattr(tiers.pdfutil, "LLM_TEXT_BUDGET_CHARS", 100)
    t0(_only_open("validity_to"))
    llm = TextLlm({"T1": {}, "T2-first": {}, "T2": {}})
    _, _, used = run(_doc(DOC_PAGE), llm)
    assert used == ["T0", "T1", "T2-first", "T2"]


def test_a_failed_t1_read_is_not_an_empty_answer(t0):
    """No answer at all (a response cut at max_tokens, or one that would not
    parse): even a field whose label the text lacks stays open, and the
    failed read is named."""
    t0(_only_open("validity_to", "cert_number"))
    llm = TextLlm({"T1": FAILED, "T2-first": {"cert_number": (
        "G1 021108 0031", "Certificate G1 021108 0031")}})
    _, fields, used = run(_doc(DOC_PAGE), llm)
    assert llm.asked("T2-first") == {"validity_to", "cert_number"}
    assert tiers.failed_reads(fields) == ["T1"]


def test_t0_below_the_threshold_settles_when_the_text_proves_it(t0):
    """Plan default 2026-10-12: a T0 value under 0.95 that passes the checks
    is settled, where T1 read nothing better (here: an empty answer, which the
    blank guard does not let erase T0's list)."""
    fields0 = _only_open("ref_list")
    fields0["ref_list"] = t0ev(["003477", "003478"], 0.9, "2 REF codes", 1)
    t0(fields0)
    llm = TextLlm({"T1": {"ref_list": ([], "")}})
    _, fields, used = run(_doc(DOC_PAGE), llm)
    assert used == ["T0", "T1"]
    assert fields["ref_list"]["value"] == ["003477", "003478"]
    assert fields["ref_list"]["tier"] == "T0"


def test_a_t0_guess_never_settles_against_a_different_t1_read(t0):
    """The Ceramill case from the eval: T0 read the word after the UDI label
    ("Document", unscorable, 0.9, and the text does print it), T1 the OCR
    layer's invalid code. The two disagree, so neither settles; the image
    read's check-valid code does."""
    fields0 = _only_open("basic_udi_di")
    fields0["basic_udi_di"] = t0ev("Document", 0.9, "Basic UDI-DI\nDocument")
    t0(fields0)
    page = DOC_PAGE + "\nBasic UDI-DI\nDocument\n76152081XSCANOOIYC"
    llm = TextLlm({"T1": {"basic_udi_di": ("76152081XSCANOOIYC", "76152081XSCANOOIYC")},
                   "T2-first": {"basic_udi_di": ("76152081XSCAN001YC", "76152081XSCAN001YC")}})
    _, fields, used = run(_doc(page), llm)
    assert llm.asked("T2-first") == {"basic_udi_di"}
    assert used == ["T0", "T1", "T2-first"]
    assert fields["basic_udi_di"]["value"] == "76152081XSCAN001YC"


def test_t0_below_the_threshold_with_nothing_proving_it_stays_open(t0):
    """A T0 name match (`playbook identity: ...`) quotes nothing on the page;
    T1 answering empty does not settle a field that holds a value."""
    fields0 = _only_open("manufacturer")
    fields0["manufacturer"] = t0ev("GC EUROPE N.V.", 0.9, "playbook identity: GC EUROPE N.V.")
    t0(fields0)
    llm = TextLlm({"T1": {}, "T2-first": {}, "T2": {}})
    _, fields, used = run(_doc(DOC_PAGE), llm)
    assert "T2-first" in used
    assert fields["manufacturer"]["value"] == "GC EUROPE N.V."     # never erased


# --- the cheap image read ----------------------------------------------------

#: A page whose OCR text layer misreads the UDI (the Ceramill case).
UDI_PAGE = DOC_PAGE + "\nBasic UDI-DI 76152081XSCANOOIYC"


@pytest.mark.parametrize("field, page, t1, image, settles", [
    # The image read's value is proven by the text.
    ("cert_number", DOC_PAGE, ("G1 O21108 OO31", "G1 O21108 OO31"),
     ("G1 021108 0031", "Certificate G1 021108 0031"), True),
    # A check-valid UDI settles though the text layer differs.
    ("basic_udi_di", UDI_PAGE, ("76152081XSCANOOIYC", "Basic UDI-DI 76152081XSCANOOIYC"),
     ("76152081XSCAN001YC", "Basic UDI-DI 76152081XSCAN001YC"), True),
    # Agreement with T1, no per-read minimum (ruling 4, as measured).
    ("validity_to", DOC_PAGE, ("2030-05-26", "valid to 2030", 1, 0.3),
     ("2030-05-26", "valid to 2030", 1, 0.3), True),
    ("validity_to", DOC_PAGE, ("2030-05-26", "valid to 2030"),
     ("2031-05-26", "valid to 2031"), False),
    # A notified-body number never settles, agreed or not.
    ("cert_number", DOC_PAGE, ("CE 0123", "CE 0123"), ("CE 0123", "CE 0123"), False),
], ids=["check", "check-valid-udi", "agreement", "disagreement", "notified-body"])
def test_the_image_read_settles_by_check_valid_udi_or_agreement(t0, field, page, t1, image,
                                                                settles):
    t0(_only_open(field))
    llm = TextLlm({"T1": {field: t1}, "T2-first": {field: image}, "T2": {}})
    _, fields, used = run(_doc(page), llm)
    assert llm.asked("T2-first") == {field}
    assert ("T2" not in used) is settles
    if settles:
        assert fields[field]["value"] == image[0]


@pytest.mark.parametrize("field, page, settles", [
    ("cert_number", DOC_PAGE, True),          # labelled, but both reads found none
    ("basic_udi_di", UDI_PAGE, False),        # a UDI label: multi-UDI lists go to Sonnet
], ids=["cert-number", "labelled-udi"])
def test_two_empty_answers_settle_except_a_labelled_udi(t0, field, page, settles):
    t0(_only_open(field))
    llm = TextLlm({"T1": {}, "T2-first": {}, "T2": {}})
    _, _, used = run(_doc(page), llm)
    assert "T2-first" in used
    assert ("T2" not in used) is settles


def test_a_scanned_page_the_image_read_rendered_is_read(t0):
    """A mixed PDF: T1 cannot read page 2, the image read renders it, and two
    empty answers then settle the field."""
    t0(_only_open("validity_to"))
    llm = TextLlm({"T1": {}, "T2-first": {}, "T2": {}})
    _, _, used = run(_doc(DOC_PAGE, IMAGE), llm)
    assert used == ["T0", "T1", "T2-first"]


def test_a_scanned_page_no_read_saw_is_unread(t0):
    """Five scanned pages after a text page: the image read renders four of
    them (image pages come before page 1 onward, which T1 has read), so one
    scanned page is read by nobody and two empty answers prove nothing."""
    t0(_only_open("validity_to"))
    llm = TextLlm({"T1": {}, "T2-first": {}, "T2": {}})
    _, _, used = run(_doc(DOC_PAGE, IMAGE, IMAGE, IMAGE, IMAGE, IMAGE), llm)
    assert llm.options("T2-first") == {"pages": (2, 3, 4, 5)}
    assert used == ["T0", "T1", "T2-first", "T2"]


def test_a_failed_image_read_sends_every_open_field_to_sonnet(t0):
    t0(_only_open("cert_number", "validity_to"))
    llm = TextLlm({"T1": {"cert_number": ("X-1", "X-1")}, "T2-first": FAILED,
                   "T2": {"cert_number": ("G1 021108 0031", "Certificate G1 021108 0031")}})
    _, fields, used = run(_doc(DOC_PAGE, IMAGE), llm)
    assert llm.asked("T2") == {"cert_number", "validity_to"}
    assert tiers.failed_reads(fields) == ["T2-first"]
    assert fields["cert_number"]["value"] == "G1 021108 0031"


# --- Sonnet --------------------------------------------------------------------

def test_sonnet_is_asked_only_what_stays_open_and_prevails(t0):
    t0(_only_open("cert_number", "manufacturer"))
    llm = TextLlm({
        "T1": {"cert_number": ("X-1", "X-1"), "manufacturer": ("GC EUROPE N.V.",
                                                               "Manufacturer: GC EUROPE N.V.")},
        "T2-first": {"cert_number": ("X-2", "X-2")},
        "T2": {"cert_number": ("X-3", "X-3")}})
    _, fields, used = run(_doc(DOC_PAGE), llm)
    assert llm.asked("T2-first") == {"cert_number"}
    assert llm.asked("T2") == {"cert_number"}
    assert fields["cert_number"]["value"] == "X-3"
    assert fields["cert_number"]["step"] == "T2"


def test_sonnets_null_closes_the_field(t0):
    """A T1 value no rule settled does not survive Sonnet's explicit null
    (review of Task 3: `merge`'s blank guard used to keep it)."""
    t0(_only_open("cert_number"))
    llm = TextLlm({"T1": {"cert_number": ("X-1", "X-1")},
                   "T2-first": {"cert_number": ("X-2", "X-2")}, "T2": {}})
    _, fields, _ = run(_doc(DOC_PAGE), llm)
    assert fields["cert_number"]["value"] is None
    assert fields["cert_number"]["step"] == "T2"


def test_sonnets_null_never_shortens_a_ref_list(t0):
    t0(_only_open("ref_list"))
    llm = TextLlm({"T1": {"ref_list": (["111111"], "REF 111111")},
                   "T2-first": {"ref_list": (["222222"], "REF 222222")}, "T2": {}})
    _, fields, _ = run(_doc(DOC_PAGE), llm)
    assert fields["ref_list"]["value"] == ["222222"]


def test_a_field_sonnet_gave_no_answer_for_keeps_what_it_holds(t0):
    t0(_only_open("cert_number"))
    llm = TextLlm({"T1": {"cert_number": ("X-1", "X-1")},
                   "T2-first": {"cert_number": ("X-2", "X-2")}, "T2": FAILED})
    _, fields, _ = run(_doc(DOC_PAGE), llm)
    assert fields["cert_number"]["value"] == "X-2"
    assert tiers.failed_reads(fields) == ["T2"]


# --- the suppressions and type to scope -----------------------------------------

def test_an_ifu_t1_proves_stops_the_certificate_reads(t0):
    """T1 was asked the certificate fields (the type was not known yet) and
    left them open; once the type is known to be an IFU, no image read is
    asked them (a model asked for a date an IFU cannot hold finds a revision
    code or a copyright year)."""
    t0({"regulation": t0ev("MDR", 1.0)})
    page = ("Instructions for use\nManufacturer: GC EUROPE N.V.\n"
            "Regulation (EU) 2017/745\nDate of revision 04/26\nCertificate of training")
    llm = TextLlm({"T1": {"type": ("IFU", "Instructions for use")}, "T2-first": {}, "T2": {}})
    _, fields, _ = run(_doc(page), llm)
    assert {"validity_from", "cert_number", "manufacturer"} <= llm.asked("T1")
    assert llm.asked("T2-first") == {"manufacturer"}
    assert fields["coverage_scope"]["value"] == "group"           # type to scope


def test_a_manufacturer_scope_value_stops_the_item_id_reads_as_today(t0):
    """Decisions 2026-10-12: the text ladder keeps today's suppression, by the
    scope's VALUE (T0's 0.6 guess included); unlike a scan."""
    t0({"type": t0ev("DoC", 1.0), "regulation": t0ev("MDR", 1.0),
        "coverage_scope": t0ev("manufacturer", 0.6, "type=DoC ref=False")})
    llm = TextLlm({"T1": {"coverage_scope": ("manufacturer", "EU Declaration of Conformity")}})
    run(_doc(DOC_PAGE), llm)
    assert not llm.asked("T1") & {"ref_list", "basic_udi_di"}
    assert not (llm.asked("T2-first") or set()) & {"ref_list", "basic_udi_di"}


def test_a_scope_t1_changes_brings_the_item_ids_in_from_t1s_answer(t0):
    """T1 is not asked the item identifiers (the scope's value was
    `manufacturer`), reads the scope as `group`, and answered them anyway: the
    ladder takes them from that answer (plan Task 4, "every parsed field into
    the reads pool"), so no image read is asked a field the text proves."""
    t0({"type": t0ev("DoC", 1.0), "regulation": t0ev("MDR", 1.0),
        "manufacturer": t0ev("GC EUROPE N.V.", 0.97),
        "validity_from": t0ev("2025-08-25", 1.0), "cert_number": t0ev("G1 021108 0031", 1.0),
        "coverage_scope": t0ev("manufacturer", 0.6, "type=DoC ref=False")})
    llm = TextLlm({"T1": {"coverage_scope": ("group", "REF 003477 003478"),
                          "ref_list": (["003477", "003478"], "REF 003477 003478"),
                          "basic_udi_di": (None, "")}})
    _, fields, used = run(_doc(DOC_PAGE), llm)
    assert not llm.asked("T1") & {"ref_list", "basic_udi_di"}
    assert used == ["T0", "T1"]
    assert fields["ref_list"]["value"] == ["003477", "003478"]
    assert fields["ref_list"]["step"] == "T1"
    t1_record = next(r for r in fields[tiers.READS_KEY] if r["step"] == "T1")
    assert {"ref_list", "basic_udi_di"} <= set(t1_record["fields"])


def test_fields_nobody_pursues_are_never_merged_from_a_read(t0):
    """A read answers every field. `stated_class` is T0's alone (PRD v3
    section 5) and `referenced_docs` is in no escalate set: neither is merged."""
    t0(_only_open("cert_number"))
    llm = TextLlm({"T1": {"cert_number": ("G1 021108 0031", "Certificate G1 021108 0031"),
                          "stated_class": ("IIa", "Class IIa"),
                          "referenced_docs": (["X"], "X")}})
    _, fields, _ = run(_doc(DOC_PAGE), llm)
    assert "stated_class" not in fields and "referenced_docs" not in fields


@pytest.mark.parametrize("doc_type, page_text", [
    ("EC", "EC Certificate\nCertificate G1 021108 0031"),
    ("ISO", "Certificate ISO 13485\nCertificate G1 021108 0031"),
])
def test_a_certificate_type_decides_the_scope(t0, doc_type, page_text):
    t0({"regulation": t0ev("MDR", 1.0), "manufacturer": t0ev("GC EUROPE N.V.", 0.97)})
    quote = page_text.splitlines()[0]
    llm = TextLlm({"T1": {"type": (doc_type, quote),
                          "coverage_scope": ("group", quote)}, "T2-first": {}, "T2": {}})
    _, fields, _ = run(_doc(page_text), llm)
    assert fields["coverage_scope"]["value"] == "manufacturer"
    assert fields["coverage_scope"]["verbatim"] == f"type={doc_type}"
    assert not (llm.asked("T2-first") or set()) & {"coverage_scope", "ref_list", "basic_udi_di"}


# --- page-aware image reads ----------------------------------------------------

def _pages(n, labelled=None):
    """`n` page texts, with `labelled[page] = text` placed on those pages."""
    out = [FILLER] * n
    for p, text in (labelled or {}).items():
        out[p - 1] = text
    return out


@pytest.mark.parametrize("count, open_fields, cites, labelled, image_pages, expected", [
    (3, ["cert_number"], {}, {}, (), None),                          # pages 1-3: the default
    (10, ["cert_number"], {}, {}, (), None),                         # nothing points past 4
    (10, ["cert_number"], {"cert_number": 7}, {}, (), (1, 2, 3, 7)),
    (10, ["cert_number"], {}, {9: "Certificate no. 9"}, (), (1, 2, 3, 9)),
    # neodent: the certificate cited on page 26 of 30
    (30, ["cert_number", "validity_to"], {"cert_number": 26}, {12: "valid until 2030"},
     (20,), (1, 12, 20, 26)),
    # cites first, then labels, then image pages, then page 1 onward; at most 4
    (30, ["cert_number", "validity_to", "basic_udi_di"],
     {"cert_number": 26, "validity_to": 25}, {12: "valid until", 14: "Basic UDI-DI"},
     (20,), (12, 14, 25, 26)),
    (2, ["cert_number"], {"cert_number": 5}, {}, (), None),          # clamped to the count
    (6, ["cert_number"], {"cert_number": 0}, {}, (), None),          # not a page
    (8, ["validity_to"], {}, {}, (6, 7), (1, 2, 6, 7)),              # a mixed PDF's scans
    (1, ["cert_number"], {}, {}, (), None),
])
def test_the_image_read_renders_the_pages_that_point_at_the_open_fields(
        count, open_fields, cites, labelled, image_pages, expected):
    t1 = {f: ev("x", page=p) for f, p in cites.items()}
    got = tiers._image_pages(count, open_fields, t1, _pages(count, labelled), image_pages)
    assert got == expected


def test_a_page_list_reaches_both_image_reads_only_when_it_is_not_pages_one_to_four(t0):
    t0(_only_open("cert_number"))
    pages = [DOC_PAGE.replace("Certificate G1 021108 0031", "")] + [FILLER] * 8
    pages[7] = "Certificate G1 021108 0031"
    llm = TextLlm({"T1": {"cert_number": ("X-1", "X-1", 8)},
                   "T2-first": {"cert_number": ("X-2", "X-2")}, "T2": {}})
    run(_doc(*pages), llm)
    assert llm.options("T2-first") == {"pages": (1, 2, 3, 8)}
    assert llm.options("T2") == {"pages": (1, 2, 3, 8)}
    assert llm.options("T1") == {}


# --- every read, recorded --------------------------------------------------------

def test_every_read_is_kept_and_disagreements_are_listed(t0):
    t0(_only_open("cert_number", "validity_to"))
    llm = TextLlm({"T1": {"cert_number": ("X-1", "X-1")},
                   "T2-first": {"cert_number": ("X-2", "X-2")},
                   "T2": {"cert_number": ("X-2", "X-2")}})
    _, fields, _ = run(_doc(DOC_PAGE, IMAGE), llm)
    record = fields[tiers.READS_KEY]
    assert [r["step"] for r in record] == ["T1", "T2-first", "T2"]
    assert record[0]["fields"]["cert_number"] == {"value": "X-1", "conf": 0.9, "page": 1}
    assert tiers.read_disagreements(fields) == [
        {"field": "cert_number", "steps": ["T1", "T2-first", "T2"],
         "values": ["X-1", "X-2", "X-2"]}]


def test_no_open_field_no_read_no_record(t0):
    t0(SETTLED_T0)
    llm = TextLlm()
    _, fields, used = run(_doc(DOC_PAGE), llm)
    assert used == ["T0"] and llm.calls == []
    assert tiers.READS_KEY not in fields


def test_hints_go_to_every_read(t0):
    t0(_only_open("cert_number"))
    llm = TextLlm({"T1": {"cert_number": ("X-1", "X-1")},
                   "T2-first": {"cert_number": ("X-2", "X-2")}, "T2": {}})
    run(_doc(DOC_PAGE), llm, hints="House style notes.")
    assert [kw.get("hints") for _, _, kw in llm.calls] == ["House style notes."] * 3


# --- the text doc-class call -----------------------------------------------------

def test_an_unknown_text_document_is_classified_under_its_own_label(t0):
    t0({}, doc_class="unknown")
    llm = TextLlm({"T1": FAILED, "T2-first": FAILED, "T2": FAILED}, text_class="DoC")
    doc_class, fields, used = run(_doc(DOC_PAGE), llm)
    assert doc_class == "compliance-doc"
    assert used[:3] == ["T0", "doc-class", "T1"]
    assert fields["type"]["value"] == "DoC" and fields["type"]["step"] == "doc-class"


def test_a_refusal_on_text_is_terminal(t0):
    t0({}, doc_class="unknown")
    llm = TextLlm(text_class=NOT_A_DOCUMENT)
    doc_class, fields, used = run(_doc(DOC_PAGE), llm)
    assert (doc_class, fields, used) == (NOT_A_DOCUMENT, {}, ["T0", "doc-class"])
    assert llm.calls == []


def test_a_mixed_pdf_is_a_text_document():
    doc = _doc(DOC_PAGE, IMAGE)
    assert pdf.has_text_page(doc)
    assert pdf.page_classes(doc) == ["text", "image"]


def test_a_t0_derived_scope_follows_the_type_t1_settles(t0):
    """A certificate T0 typed off its file name (EC at 0.6, scope at 0.6):
    T1 proves the type, and the scope is derived again at T1's confidence."""
    t0({"type": t0ev("EC", 0.6), "regulation": t0ev("MDR", 1.0),
        "manufacturer": t0ev("GC EUROPE N.V.", 0.97),
        "coverage_scope": t0ev("manufacturer", 0.6, "type=EC")})
    page = "EC Certificate\nCertificate G1 021108 0031"
    llm = TextLlm({"T1": {"type": ("EC", "EC Certificate", 1, 0.97)}, "T2-first": {}, "T2": {}})
    _, fields, _ = run(_doc(page), llm)
    assert (fields["coverage_scope"]["value"], fields["coverage_scope"]["conf"]) == (
        "manufacturer", 0.97)


# --- pages-unread on text (PRD v3 section 7, informational) --------------------

def test_a_field_left_empty_past_the_text_budget_is_unread(t0, monkeypatch):
    """Text beyond the budget was not read: a field every read left empty there
    is unread, not absent, and the document carries `pages-unread`."""
    monkeypatch.setattr(tiers.pdfutil, "LLM_TEXT_BUDGET_CHARS", 100)
    t0(_only_open("validity_to"))
    llm = TextLlm({"T1": {}, "T2-first": {}, "T2": {}})
    _, fields, _ = run(_doc(DOC_PAGE), llm)
    assert fields[tiers.PAGES_UNREAD_KEY] == ["validity_to"]
    assert tiers.PAGES_UNREAD_FLAG in tiers.integrity_flags(fields)


def test_a_field_left_empty_beside_a_scanned_page_no_read_saw_is_unread(t0):
    t0(_only_open("validity_to"))
    llm = TextLlm({"T1": {}, "T2-first": {}, "T2": {}})
    _, fields, _ = run(_doc(DOC_PAGE, IMAGE, IMAGE, IMAGE, IMAGE, IMAGE), llm)
    assert fields[tiers.PAGES_UNREAD_KEY] == ["validity_to"]


@pytest.mark.parametrize("pages, sonnet", [
    ((DOC_PAGE,), {"validity_to": ("2030-05-26", "valid until 2030-05-26")}),  # found
    ((DOC_PAGE, IMAGE), {}),          # every page read by one read or another
], ids=["sonnet-found-it", "all-pages-read"])
def test_no_unread_flag_when_the_field_was_found_or_everything_was_read(
        t0, monkeypatch, pages, sonnet):
    if len(pages) == 1:
        monkeypatch.setattr(tiers.pdfutil, "LLM_TEXT_BUDGET_CHARS", 100)
    t0(_only_open("validity_to"))
    llm = TextLlm({"T1": {}, "T2-first": {"validity_to": ("2031-01-01", "x")}, "T2": sonnet})
    _, fields, _ = run(_doc(*pages), llm)
    assert tiers.PAGES_UNREAD_KEY not in fields


def test_a_failed_sonnet_read_is_not_unread(t0, monkeypatch):
    monkeypatch.setattr(tiers.pdfutil, "LLM_TEXT_BUDGET_CHARS", 100)
    t0(_only_open("validity_to"))
    llm = TextLlm({"T1": {}, "T2-first": {}, "T2": FAILED})
    _, fields, _ = run(_doc(DOC_PAGE), llm)
    assert tiers.PAGES_UNREAD_KEY not in fields
    assert tiers.failed_reads(fields) == ["T2"]


def test_without_page_lists_the_image_reads_are_pages_one_to_four_and_counted_so():
    """The batch transport cannot send a page list (`batch_ref` keeps none):
    `page_aware=False` keeps both image reads on pages 1-4, so scanned pages
    past page 4 are unread, and a field every read left empty is flagged."""
    llm = TextLlm({"T1": {}, "T2-first": {}, "T2": {}})
    doc = _doc(DOC_PAGE, FILLER, FILLER, FILLER, IMAGE, IMAGE)
    used = ["T0"]

    def read(label, missing, **kw):
        return llm.extract(label, doc, "d.pdf", missing, **kw)

    _, out = tiers.run_text_ladder(doc, "d.pdf", "compliance-doc",
                                   dict(_only_open("validity_to")), used, read,
                                   page_aware=False)
    assert llm.options("T2-first") == {} and llm.options("T2") == {}
    assert used == ["T0", "T1", "T2-first", "T2"]
    assert out[tiers.PAGES_UNREAD_KEY] == ["validity_to"]


# --- a REF list: store the list that settled it (review 2026-10-13) -------------

def test_a_failing_longer_list_is_never_stored_over_a_passing_shorter_one(t0):
    """The review's stub: T1's list is longer and one code is not printed
    (66%, under the 95% check), the image read's shorter list is all printed.
    `merge` keeps the longer list by its length rule; the field takes the
    list that settled it."""
    t0(_only_open("ref_list"))
    llm = TextLlm({"T1": {"ref_list": (["003477", "003478", "999999"], "REF 003477 003478")},
                   "T2-first": {"ref_list": (["003477", "003478"], "REF 003477 003478")}})
    _, fields, used = run(_doc(DOC_PAGE), llm)
    assert used == ["T0", "T1", "T2-first"]
    assert fields["ref_list"]["value"] == ["003477", "003478"]
    assert fields["ref_list"]["step"] == "T2-first"


def test_t1s_passing_list_is_stored_over_a_failing_longer_t0_guess(t0):
    fields0 = _only_open("ref_list")
    fields0["ref_list"] = t0ev(["003477", "003478", "999999"], 0.9, "3 REF codes", 1)
    t0(fields0)
    llm = TextLlm({"T1": {"ref_list": (["003477", "003478"], "REF 003477 003478")}})
    _, fields, used = run(_doc(DOC_PAGE), llm)
    assert used == ["T0", "T1"]
    assert fields["ref_list"]["value"] == ["003477", "003478"]
    assert fields["ref_list"]["step"] == "T1"


def test_the_longer_list_still_wins_when_both_pass(t0):
    page = DOC_PAGE.replace("REF 003477 003478", "REF 003477 003478 003479")
    fields0 = _only_open("ref_list")
    fields0["ref_list"] = t0ev(["003477", "003478", "003479"], 0.9, "3 REF codes", 1)
    t0(fields0)
    llm = TextLlm({"T1": {"ref_list": (["003477", "003478"], "REF 003477 003478")}})
    _, fields, used = run(_doc(page), llm)
    assert used == ["T0", "T1"]
    assert fields["ref_list"]["value"] == ["003477", "003478", "003479"]
    assert fields["ref_list"]["tier"] == "T0"


def test_a_t0_list_at_the_threshold_is_never_asked_or_replaced(t0):
    fields0 = _only_open("cert_number")
    fields0["ref_list"] = t0ev(["003477", "999999"], 1.0, "2 REF codes", 1)   # one not printed
    t0(fields0)
    llm = TextLlm({"T1": {"ref_list": (["003477"], "REF 003477"),
                          "cert_number": ("G1 021108 0031", "Certificate G1 021108 0031")}})
    _, fields, _ = run(_doc(DOC_PAGE), llm)
    assert "ref_list" not in llm.asked("T1")
    assert fields["ref_list"]["value"] == ["003477", "999999"]


# --- an IFU's certificate fields (review 2026-10-13) -----------------------------

IFU_PAGE = ("Instructions for use\nManufacturer: GC EUROPE N.V.\nRegulation (EU) 2017/745\n"
            "701593/M/12 04/26\n(c) Institut Straumann AG, 2025")


def test_certificate_fields_t1_read_before_the_type_was_known_are_dropped(t0):
    """T1 was asked `validity_from` because the type was not known yet, and read
    a date off the revision code (the STRAUMANN case). Once T1's own type
    settles it as an IFU, the suppression applies to what T1 already read:
    an IFU carries no certificate and no validity period."""
    t0({"regulation": t0ev("MDR", 1.0)})
    llm = TextLlm({"T1": {"type": ("IFU", "Instructions for use", 1, 0.97),
                          "validity_from": ("2026-04-26", "701593/M/12 04/26", 1, 0.6),
                          "cert_number": ("701593/M/12", "701593/M/12 04/26", 1, 0.5)}})
    _, fields, _ = run(_doc(IFU_PAGE), llm)
    assert "validity_from" in llm.asked("T1")
    assert "validity_from" not in fields and "cert_number" not in fields
    t1_record = next(r for r in fields[tiers.READS_KEY] if r["step"] == "T1")
    assert t1_record["fields"]["validity_from"]["value"] == "2026-04-26"   # still recorded


def test_the_drop_follows_a_type_the_image_read_settles(t0):
    t0({"regulation": t0ev("MDR", 1.0), "manufacturer": t0ev("GC EUROPE N.V.", 0.97)})
    llm = TextLlm({"T1": {"type": ("IFU", "a manual", 1, 0.6),
                          "validity_from": ("2026-04-26", "701593/M/12 04/26", 1, 0.6)},
                   "T2-first": {"type": ("IFU", "Instructions for use", 1, 0.95)}})
    _, fields, _ = run(_doc(IFU_PAGE), llm)
    assert fields["type"]["value"] == "IFU"
    assert "validity_from" not in fields


def test_a_t0_certificate_value_stays_when_the_type_turns_out_an_ifu(t0):
    """Only what a read wrote is dropped: T0's own read of the field is what
    the document held before any read was asked."""
    t0({"regulation": t0ev("MDR", 1.0), "validity_to": t0ev("2030-01-01", 1.0),
        "validity_from": t0ev("2024-01-01", 0.6)})
    llm = TextLlm({"T1": {"type": ("IFU", "Instructions for use", 1, 0.97),
                          "validity_from": ("2026-04-26", "701593/M/12 04/26", 1, 0.6)}})
    _, fields, _ = run(_doc(IFU_PAGE), llm)
    assert fields["validity_to"]["value"] == "2030-01-01"
    assert (fields["validity_from"]["value"], fields["validity_from"]["tier"]) == ("2024-01-01", "T0")



# --- a mixed PDF whose pages 1-2 have no text (review 2026-10-13) -----------------

def test_no_text_on_pages_one_and_two_asks_the_image_doc_class_call(t0):
    """T0 cannot type it and the text call would see nothing: the page 1-2
    images are asked instead, as on a scan. The review found two real
    compliance documents in the mirror shaped like this, which the text call
    refused without a call."""
    t0({}, doc_class="unknown")
    llm = TextLlm({"T1": FAILED, "T2-first": FAILED, "T2": FAILED},
                  text_class=NOT_A_DOCUMENT, image_class="DoC")
    doc_class, fields, used = run(_doc(IMAGE, IMAGE, DOC_PAGE), llm)
    assert llm.classified == ["image"]
    assert doc_class == "compliance-doc"
    assert used[:3] == ["T0", "doc-class-image", "T1"]
    assert (fields["type"]["value"], fields["type"]["step"]) == ("DoC", "doc-class-image")


def test_the_image_doc_class_refusal_is_terminal_on_text_too(t0):
    t0({}, doc_class="unknown")
    llm = TextLlm(text_class="DoC", image_class=NOT_A_DOCUMENT)
    doc_class, fields, used = run(_doc(IMAGE, IMAGE, DOC_PAGE), llm)
    assert (doc_class, fields, used) == (NOT_A_DOCUMENT, {}, ["T0", "doc-class-image"])
    assert llm.calls == []


def test_text_on_pages_one_and_two_keeps_the_text_call(t0):
    t0({}, doc_class="unknown")
    llm = TextLlm({"T1": FAILED, "T2-first": FAILED, "T2": FAILED},
                  text_class="DoC", image_class="EC")
    run(_doc(DOC_PAGE, IMAGE), llm)
    assert llm.classified == ["text"]


def test_a_client_without_an_image_classifier_keeps_the_text_call(t0):
    t0({}, doc_class="unknown")
    llm = TextLlm({"T1": FAILED, "T2-first": FAILED, "T2": FAILED}, text_class="DoC")
    run(_doc(IMAGE, IMAGE, DOC_PAGE), llm)
    assert llm.classified == ["text"]
