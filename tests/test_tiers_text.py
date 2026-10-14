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
from tests.fixtures.annotated_pdf import SIGNED_AT, annotate

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

#: Page 2 of USTOMED's Wundhaken declaration (doc 520, Task 9 replay): the
#: article list annexed to a scanned page 1. On its own it announces nothing.
ARTICLE_LIST = """Anhang 1 Artikelliste_Wundhaken
Item No. Item Description EU Risk-Class
24-021-001 Fine retractor, 16 cm, 1 prong, sharp
24-021-002 Fine retractor, 16 cm, 2 prongs, sharp"""


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


def ev(value, verbatim=None, page=1, conf=0.9, tier="T1", model="stub-model"):
    return {"value": value, "conf": conf, "tier": tier,
            "verbatim": str(value) if verbatim is None else verbatim,
            "page": page, "model_id": model}


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
        tier = "T1" if label.startswith("T1") else "T2"
        model = f"model-{label}"
        out = {f: ev(None, "", None, 0.0, tier, model) for f in missing}
        for f, spec in given.items():
            out[f] = (ev(*spec, tier=tier, model=model) if isinstance(spec, tuple)
                      else ev(spec, tier=tier, model=model))
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


#: Page 1 of MELAG's Euroklav 29 VS+ declaration (doc 754, Task 9 replay): one
#: product, named, and no article list.
ONE_PRODUCT_PAGE = DOC_PAGE + "\nProduktname / Product name: Euroklav 29 VS+"


@pytest.mark.parametrize("field, t1, settles", [
    # doc 754: Haiku 5.5 read `manufacturer` at 0.6 and quoted the product name
    ("coverage_scope", ("manufacturer", "Product name: Euroklav 29 VS+", 1, 0.6), False),
    ("coverage_scope", ("group", "Product name: Euroklav 29 VS+", 1, 0.69), False),
    ("coverage_scope", ("group", "Product name: Euroklav 29 VS+", 1, 0.7), True),
    # the Komet RA_812 article lists of the eval: `other` at 0.5
    ("type", ("other", "EU Declaration of Conformity", 1, 0.5), False),
    ("type", ("DoC", "EU Declaration of Conformity", 1, 0.9), True),
    # a literal field the text proves settles whatever the read's own number
    ("regulation", ("MDR", "Regulation (EU) 2017/745", 1, 0.5), True),
], ids=["754-manufacturer-0.6", "group-0.69", "group-0.70", "type-0.5", "type-0.9",
        "literal-0.5"])
def test_a_semantic_t1_read_under_the_agreement_floor_does_not_settle(t0, field, t1, settles):
    """Task 9 replay, doc 754: a single-product declaration whose scope T1
    read as `manufacturer` at 0.6, quoting the product name. The quote is on
    the page, which was all a `type` or `coverage_scope` needed to settle, so
    no other read was asked, and the scope sent the document to the
    manufacturer binding route (production, after Sonnet: `group`). Under
    `gate.agreement_min` a read can neither reach GATE's 0.75 nor be counted
    in an agreement, and `T1-second` is not asked for it: settled there, the
    field ends the ladder on the one read that said it was unsure. So such a
    semantic read goes to the image read, which either agrees or not."""
    t0(_only_open(field))
    llm = TextLlm({"T1": {field: t1},
                   "T2-first": {field: ("group", "Product name: Euroklav 29 VS+", 1, 0.8)
                                if field == "coverage_scope" else DECLARATION},
                   "T2": {}, "T1-second": {}})
    _, fields, used = run(_doc(ONE_PRODUCT_PAGE), llm)
    assert ("T2-first" not in used) is settles
    if settles:
        assert fields[field]["step"] == "T1"
    else:
        assert llm.asked("T2-first") == {field}
        assert fields[field]["step"] == "T2-first"


@pytest.mark.parametrize("pages, settles", [
    ((IMAGE, ARTICLE_LIST), False),            # USTOMED 507: the scanned declaration first
    ((ARTICLE_LIST, IMAGE), False),            # the scan second
    ((ARTICLE_LIST, ARTICLE_LIST, IMAGE), True),   # pages 1-2 are text: T1 saw the header
], ids=["image-page-1", "image-page-2", "image-page-3"])
def test_a_type_t1_read_off_a_text_page_does_not_settle_when_page_1_or_2_is_scanned(
        t0, pages, settles):
    """Task 9 replay, USTOMED 507-530: page 1 is the scanned declaration, page 2
    its article list as text. T0 could not type the file (0.6, the file name);
    T1 read only the list and typed it `other`, quoting the list, which settled
    the type. Where page 1 or 2 is a scanned page, a type read off the text is
    not settled: the image read is asked, and it renders page 1."""
    fields0 = _only_open("type")
    fields0["type"] = t0ev("DoC", 0.6, "filename:Konformitätserklärung.pdf")
    t0(fields0)
    llm = TextLlm({"T1": {"type": ("other", "Anhang 1 Artikelliste_Wundhaken", None, 0.9)},
                   "T2-first": {"type": ("DoC", "Konformitätserklärung", 1, 0.95)},
                   "T2": {"type": ("DoC", "Konformitätserklärung", 1, 0.98)}})
    page = next(n for n, p in enumerate(pages, start=1) if p is ARTICLE_LIST)
    llm.answers["T1"]["type"] = ("other", "Anhang 1 Artikelliste_Wundhaken", page, 0.9)
    _, fields, used = run(_doc(*pages), llm)
    assert ("T2-first" not in used) is settles
    if settles:
        assert (fields["type"]["value"], fields["type"]["step"]) == ("other", "T1")
    else:
        assert "type" in llm.asked("T2-first")
        assert 1 in (llm.options("T2-first") or {}).get("pages", (1,))
        assert fields["type"]["value"] == "DoC"


def test_a_mixed_pdf_regulation_proven_by_absence_asks_the_image_read(t0):
    """Task 9 replay, USTOMED 507: the text layer (the article list) cites
    nothing, which proved `n.a.`; the scanned page 1 prints 93/42."""
    t0(_only_open("regulation"))
    llm = TextLlm({"T1": {"regulation": ("n.a.", "no regulation cited", 2, 0.6)},
                   "T2-first": {"regulation": ("MDD", "EG-Richtlinie 93/42/EWG", 1, 0.95)},
                   "T2": {"regulation": ("MDD", "EG-Richtlinie 93/42/EWG", 1, 0.97)}})
    _, fields, used = run(_doc(IMAGE, ARTICLE_LIST), llm)
    assert "regulation" in llm.asked("T2-first")
    assert fields["regulation"]["value"] == "MDD"


def test_a_semantic_t1_read_under_the_floor_settles_by_agreement_with_the_image_read(t0):
    """The floor asks the image read; it does not overrule it. Two reads that
    agree settle the field, as ruling 4 has it, with no per-read minimum."""
    t0(_only_open("coverage_scope"))
    unsure = ("manufacturer", "Product name: Euroklav 29 VS+", 1, 0.6)
    llm = TextLlm({"T1": {"coverage_scope": unsure}, "T2-first": {"coverage_scope": unsure},
                   "T2": {}})
    _, fields, used = run(_doc(ONE_PRODUCT_PAGE), llm)
    assert used == ["T0", "T1", "T2-first"]
    assert fields["coverage_scope"]["value"] == "manufacturer"


@pytest.mark.parametrize("field, t1, first, settles", [
    # the floor in step 4: the image read's own disagreeing value at 0.5
    ("coverage_scope", ("manufacturer", "Product name: Euroklav 29 VS+", 1, 0.6),
     ("group", "Product name: Euroklav 29 VS+", 1, 0.5), False),
    ("type", ("other", "EU Declaration of Conformity", 1, 0.5),
     ("DoC", "EU Declaration of Conformity", 1, 0.6), False),
    # at the floor its quote settles it, as T1's does
    ("coverage_scope", ("manufacturer", "Product name: Euroklav 29 VS+", 1, 0.6),
     ("group", "Product name: Euroklav 29 VS+", 1, 0.7), True),
], ids=["scope-0.5", "type-0.6", "scope-0.70"])
def test_a_semantic_image_read_under_the_floor_does_not_settle_on_its_quote(
        t0, field, t1, first, settles):
    """Review of the Task 9 fixes: the floor applied to T1 only, so a
    disagreeing image read at 0.5 settled the field on its quote and Sonnet
    was never asked. The image read's own `type` or `coverage_scope` settles on
    its quote only at `gate.agreement_min`; agreeing with T1 still settles it
    at any number (the test above)."""
    t0(_only_open(field))
    llm = TextLlm({"T1": {field: t1}, "T2-first": {field: first},
                   "T2": {field: (first[0], first[1], 1, 0.9)}})
    _, fields, used = run(_doc(ONE_PRODUCT_PAGE), llm)
    assert ("T2" not in used) is settles
    if not settles:
        assert llm.asked("T2") == {field}


def test_t1s_empty_manufacturer_goes_to_the_image_read_without_a_label(t0):
    """Doc 2173: T1 could not read the name off a text layer in a private
    encoding, and with no "Manufacturer" label in it the null rule settled the
    empty answer. The image read is asked instead, and settles it where it
    agrees or the text proves it."""
    page = "EU Declaration of Conformity\nRegulation (EU) 2017/745\nDate of issue: 2025-08-25"
    t0(_only_open("manufacturer"))
    llm = TextLlm({"T1": {"manufacturer": (None, "", None, 0.3)},
                   "T2-first": {"manufacturer": ("EURONDA S.P.A.", "EURONDA S.P.A.", 1, 0.95)},
                   "T2": {"manufacturer": ("EURONDA S.P.A.", "EURONDA S.P.A.", 1, 0.9)}})
    _, fields, used = run(_doc(page), llm)
    assert "manufacturer" in llm.asked("T2-first")
    assert fields["manufacturer"]["value"] == "EURONDA S.P.A."


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
    asked the expiry or the certificate number (a model asked for a value an
    IFU cannot hold finds a revision code or a copyright year). Its revision
    date is still asked (Denis, 2026-10-14)."""
    t0({"regulation": t0ev("MDR", 1.0)})
    page = ("Instructions for use\nManufacturer: GC EUROPE N.V.\n"
            "Regulation (EU) 2017/745\nDate of revision 04/26\nCertificate of training")
    llm = TextLlm({"T1": {"type": ("IFU", "Instructions for use")}, "T2-first": {}, "T2": {}})
    _, fields, _ = run(_doc(page), llm)
    assert {"validity_from", "cert_number", "manufacturer"} <= llm.asked("T1")
    assert llm.asked("T2-first") == {"manufacturer", "validity_from"}
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
    # No image read; the scope T1 read is a gate field the second read confirms.
    assert used == ["T0", "T1", "T1-second"]
    assert llm.asked("T1-second") == {"coverage_scope"}
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


def test_a_t0_list_is_kept_when_a_read_proves_a_shorter_one(t0):
    """`_proven` replaces only a list a read wrote, never T0's (review round 2):
    T0's list is the deterministic enumeration, and where it fails the check
    it is because its codes sit elsewhere (a companion annex, below). The
    read's own list passing settles the field; T0's list stays."""
    fields0 = _only_open("ref_list")
    fields0["ref_list"] = t0ev(["003477", "003478", "999999"], 0.9, "3 REF codes", 1)
    t0(fields0)
    llm = TextLlm({"T1": {"ref_list": (["003477", "003478"], "REF 003477 003478")}})
    _, fields, used = run(_doc(DOC_PAGE), llm)
    assert used == ["T0", "T1"]
    assert fields["ref_list"]["value"] == ["003477", "003478", "999999"]
    assert fields["ref_list"]["tier"] == "T0"


@pytest.mark.parametrize("answers, used", [
    # T1 reads the one code the declaration prints: settled there.
    ({"T1": {"ref_list": (["003477"], "REF 003477")}}, ["T0", "T1"]),
    # T1 finds none, the image read reads it: settled there.
    ({"T1": {"ref_list": ([], "")}, "T2-first": {"ref_list": (["003477"], "REF 003477")}},
     ["T0", "T1", "T2-first"]),
], ids=["t1-reads-one", "image-reads-one"])
def test_a_komet_companion_annex_list_is_never_replaced(t0, answers, used):
    """Komet: the declaration (RA_810) carries its article list in a separate
    annex (RA_812), which BACKFILL hands T0 as the companion file. T0's list
    is read there, so its codes are not in the declaration's text and the 95%
    check fails by construction (63 of 63 on the mirror). A list a read finds
    in the declaration itself must not replace it."""
    fields0 = _only_open("ref_list")
    annex = dict(t0ev(["111111", "222222", "333333", "444444"], 0.9, "4 REF codes", 1),
                 archive_url="/archive/komet/533156_RA_812_Liste_DoC.pdf")
    fields0["ref_list"] = annex
    t0(fields0)
    llm = TextLlm(answers)
    _, fields, got = run(_doc(DOC_PAGE), llm)
    assert got == used
    assert fields["ref_list"]["value"] == ["111111", "222222", "333333", "444444"]
    assert fields["ref_list"]["archive_url"].endswith("RA_812_Liste_DoC.pdf")


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


# --- a REF list at the prompt cap (Task 9 replay, doc 839) -----------------------

CAP = tiers.REF_LIST_PROMPT_CAP


def _codes(n: int) -> list[str]:
    return [str(100000 + i) for i in range(n)]


def _ref_pages(codes: list[str]) -> tuple[str, ...]:
    """The codes printed ten to a line, 100 to a page, under a REF label."""
    lines = [" ".join(codes[i:i + 10]) for i in range(0, len(codes), 10)]
    return tuple("Article list REF\n" + "\n".join(lines[i:i + 10])
                 for i in range(0, len(lines), 10))


def test_a_t1_list_at_the_cap_settles_and_is_flagged(t0):
    """Doc 839 (Task 9 replay): T1 returned 150 of 184 printed codes, every
    one printed, and the 95% check settled the list. A rule that let no list
    at the cap settle sent it on to the image read and Sonnet, but every read
    is capped at 150 too, so none could return more and the rule only added
    reads (review of the Task 9 fixes, 2026-10-14). The list settles as any
    other, and the truncation flag says it may be cut."""
    printed = _codes(184)
    t0(_only_open("ref_list"))
    llm = TextLlm({"T1": {"ref_list": (printed[:CAP], "first 150 returned", 1, 0.8)}})
    _, fields, used = run(_doc(*_ref_pages(printed)), llm)
    assert used == ["T0", "T1"]
    assert fields["ref_list"]["value"] == printed[:CAP]
    assert tiers.REF_TRUNCATION_FLAG in tiers.integrity_flags(fields)


def test_a_list_at_the_cap_keeps_its_flag_when_codes_are_rejected(t0):
    """The flag reads the length the read returned, before any code was set
    aside: 150 codes, two of them nomenclature codes, is still a list the
    prompt cut, though 148 are stored."""
    printed = _codes(184)
    pages = list(_ref_pages(printed))
    pages[0] = ("EMDN code: Q010199 Conservative dentistry\n"
                "GMDN code: 16668 Carbide dental bur\n" + pages[0])
    t0(_only_open("ref_list"))
    llm = TextLlm({"T1": {"ref_list": (printed[:148] + ["Q010199", "16668"],
                                       "first 150 returned", 1, 0.8)}})
    _, fields, used = run(_doc(*pages), llm)
    assert used == ["T0", "T1"]
    assert fields["ref_list"]["value"] == printed[:148]
    assert tiers.REF_TRUNCATION_FLAG in tiers.integrity_flags(fields)


def test_a_list_one_under_the_cap_still_settles_on_t1(t0):
    printed = _codes(CAP - 1)
    t0(_only_open("ref_list"))
    llm = TextLlm({"T1": {"ref_list": (printed, "all codes", 1, 0.8)}})
    _, fields, used = run(_doc(*_ref_pages(printed)), llm)
    assert used == ["T0", "T1"]
    assert fields["ref_list"]["value"] == printed


def test_a_t0_list_of_exactly_the_cap_is_a_real_count(t0):
    """T0 enumerates the document; the cap is a prompt's, so a T0 list of 150
    codes proven by the text still settles where T0 held it below 0.95."""
    printed = _codes(CAP)
    fields0 = _only_open("ref_list")
    fields0["ref_list"] = t0ev(printed, 0.9, "150 REF codes", 1)
    t0(fields0)
    llm = TextLlm({"T1": {"ref_list": ([], "", None, 0.0)}})
    _, fields, used = run(_doc(*_ref_pages(printed)), llm)
    assert used == ["T0", "T1"]
    assert fields["ref_list"]["tier"] == "T0"


# --- a read's value that is provably not the field (Task 9 replay) ---------------

#: Doc 622's text layer: the form's labels first, then its values.
ULTRADENT_FORM = ("DECLARATION OF CONFORMITY\nRegulation (EU) 2017/745\nGMDN Code:\nEMDN Code:\n"
                  "Model Numbers:\n14066, Tongue Depressor\n36311, Occlusal splint\n"
                  "V9001, Tongue Depressors, Single-Use\n331-")


def test_nomenclature_codes_t1_read_as_ref_codes_are_rejected(t0):
    """Doc 622: T1 read the GMDN and EMDN codes as the REF list, every one
    printed, so the 95% check settled them. They are not the field: the read
    counts as no answer for it, the image read is asked, and the read keeps
    its value and the reason in the record."""
    t0(_only_open("ref_list"))
    llm = TextLlm({"T1": {"ref_list": (["14066", "36311", "V9001"], "Model Numbers: 14066", 1, 0.8)},
                   "T2-first": {"ref_list": (["331-"], "Model Numbers: 331-", 1, 0.6)}})
    _, fields, used = run(_doc(ULTRADENT_FORM), llm)
    assert used[:3] == ["T0", "T1", "T2-first"]
    assert fields["ref_list"]["value"] == ["331-"]
    record = next(r for r in fields[tiers.READS_KEY] if r["step"] == "T1")
    assert record["fields"]["ref_list"]["value"] == ["14066", "36311", "V9001"]
    assert "nomenclature" in record["fields"]["ref_list"]["rejected"]
    assert tiers.rejected_values(fields) == [
        {"step": "T1", "field": "ref_list", "reason": record["fields"]["ref_list"]["rejected"]}]


def test_a_rejected_value_t1_gave_for_a_field_pursued_later_is_recorded(t0):
    """T1 changes the scope to `group`, which brings the item identifiers in
    after T1 (`_from_t1`). Its REF list was rejected, so it is not taken; it is
    still recorded as T1's, so the job result counts it."""
    fields0 = _only_open("coverage_scope", "ref_list")
    fields0["coverage_scope"] = t0ev("manufacturer", 0.6)
    t0(fields0)
    llm = TextLlm({"T1": {"coverage_scope": ("group", "Model Numbers:", 1, 0.9),
                          "ref_list": (["14066", "36311"], "Model Numbers: 14066", 1, 0.8)},
                   "T2-first": {"ref_list": (["331-"], "Model Numbers: 331-", 1, 0.6)}})
    _, fields, _ = run(_doc(ULTRADENT_FORM), llm)
    assert "ref_list" not in llm.asked("T1")
    assert fields["ref_list"]["value"] == ["331-"]
    assert [(r["step"], r["field"]) for r in tiers.rejected_values(fields)] == [("T1", "ref_list")]


def test_a_code_t0_listed_is_never_rejected_as_nomenclature(t0):
    """Review of the Task 9 fixes: T0 enumerated the article list, so a code
    it holds is an article, whatever its shape and wherever it sits."""
    fields0 = _only_open("ref_list")
    fields0["ref_list"] = t0ev(["14066", "331-"], 0.9, "2 REF codes", 1)
    t0(fields0)
    llm = TextLlm({"T1": {"ref_list": (["14066", "331-"], "Model Numbers: 14066", 1, 0.8)}})
    _, fields, used = run(_doc(ULTRADENT_FORM), llm)
    assert used == ["T0", "T1"]
    assert tiers.rejected_values(fields) == []
    assert fields["ref_list"]["value"] == ["14066", "331-"]


def test_a_real_code_beside_nomenclature_codes_stays(t0):
    t0(_only_open("ref_list"))
    llm = TextLlm({"T1": {"ref_list": (["331-", "14066"], "Model Numbers: 331-", 1, 0.8)}})
    _, fields, used = run(_doc(ULTRADENT_FORM), llm)
    assert used == ["T0", "T1"]
    assert fields["ref_list"]["value"] == ["331-"]


def test_a_list_the_read_disowns_is_no_answer(t0):
    """Doc 305: type designations T1 returned at 0.4 while saying they are
    not REF codes."""
    page = DOC_PAGE + "\nType: MDAC-2422 SNIB TER, MDAC-2422 SNNB ESA"
    t0(_only_open("ref_list"))
    quote = "Type: MDAC-2422 SNIB TER (these are type designations, not explicit REF codes)"
    llm = TextLlm({"T1": {"ref_list": (["MDAC-2422 SNIB TER", "MDAC-2422 SNNB ESA"], quote, 1, 0.4)},
                   "T2-first": {"ref_list": ([], "", None, 0.0)},
                   "T2": {"ref_list": ([], "", None, 0.0)}})
    _, fields, used = run(_doc(page), llm)
    assert "T2-first" in used
    assert not fields["ref_list"]["value"]


def test_sonnets_own_reference_for_a_certificate_keeps_the_cheap_reads_number(t0):
    """Doc 1406 (RTD, Dentsply Sirona): pages 2-3 are scans. The cheap image
    read gave the cited certificate, "8445 rev.18", at 0.8 (under
    image_settle), and Sonnet then gave the declaration's own reference. That
    is not a certificate number: Sonnet's read counts as no answer for the
    field, which keeps the number it holds."""
    t0(_only_open("cert_number"))
    llm = TextLlm({"T2-first": {"cert_number": ("8445 rev.18", "EC certificate n° 8445 rev.18", 3, 0.8)},
                   "T2": {"cert_number": ("1000-TF_8_EUDOC_000219 [5]",
                                          "Declaration of conformity reference: 1000-TF_8_EUDOC_000219 [5]",
                                          2, 0.6)}})
    _, fields, used = run(_doc(DOC_PAGE.replace("Certificate G1 021108 0031\n", ""), IMAGE, IMAGE), llm)
    assert used == ["T0", "T1", "T2-first", "T2"]
    assert fields["cert_number"]["value"] == "8445 rev.18"
    assert [(r["step"], r["field"]) for r in tiers.rejected_values(fields)] == [("T2", "cert_number")]


#: Doc 617's text layer (Ultradent UltraTemp): the declaration's own date is
#: in the notary's stamp, which is no text; the template's is in the footer.
ULTRADENT_617 = ("DECLARATION OF CONFORMITY\nRegulation (EU) 2017/745\nModel Numbers:\n"
                 "5916-, S5917-\nTemplate RP00S0.07 Released May 31, 2024")


def test_a_template_release_date_is_not_the_declarations(t0):
    """Doc 617: T1 gave the template's release date, which its quote prints,
    so the checks settled it and no image read was asked the date the stamp
    shows. A date the quote gives as a template's is no answer."""
    t0(_only_open("validity_from"))
    llm = TextLlm({"T1": {"validity_from": ("2024-05-31", "Template RP00S0.07 Released May 31, 2024",
                                            1, 0.3)},
                   "T2-first": {"validity_from": ("2024-08-08", "08 August 2024", 2, 0.9)},
                   "T2": {"validity_from": ("2024-08-08", "08 August 2024", 2, 0.9)}})
    _, fields, used = run(_doc(ULTRADENT_617, "Notary Public\nSubscribed and sworn to before me"),
                          llm)
    assert "T2-first" in used
    assert fields["validity_from"]["value"] == "2024-08-08"


def test_model_numbers_an_empty_t1_answer_missed_go_to_the_image_read(t0):
    """Doc 617: T1 answered the REF list empty ("not extracted as field in
    scope"), and with no REF label in the text the null rule settled it. The
    "Model Numbers" label keeps it open; the image read's codes are printed,
    so they settle it."""
    t0(_only_open("ref_list"))
    llm = TextLlm({"T1": {"ref_list": ([], "Model Numbers: not extracted", 1, 0.2)},
                   "T2-first": {"ref_list": (["5916-", "S5917-"], "Model Numbers: 5916-, S5917-",
                                             1, 0.6)}})
    _, fields, used = run(_doc(ULTRADENT_617), llm)
    assert used == ["T0", "T1", "T2-first"]
    assert fields["ref_list"]["value"] == ["5916-", "S5917-"]


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


@pytest.mark.parametrize("value, quote, kept", [
    ("2023-06-01", "REV.00_06-2023", True),          # docs 709, 712, 714, 717, 719
    ("2025-04-01", "04/2025", True),                 # doc 1059
    ("2026-04-01", "701593/M/12 04/26", True),       # decisions.md 2026-10-02
    ("2026-04-26", "701593/M/12 04/26", False),      # the STRAUMANN read: a made-up day
])
def test_an_ifu_keeps_the_revision_date_its_quote_prints(t0, value, quote, kept):
    """Task 9 replay: an IFU's revision date ("REV.00_06-2023") is the only
    date it carries, and dropping every date a read gave before the type was
    known lost it on six documents. A date the quote prints, as a whole date
    or as month and year stored as the first of the month, stays; one the
    quote does not print (a day read off a revision code) still goes, and
    `validity_to` and `cert_number` go whatever the quote."""
    page = "Instructions for use\nManufacturer: Dental World S.r.l.\n" + quote
    t0({"regulation": t0ev("MDR", 1.0)})
    llm = TextLlm({"T1": {"type": ("IFU", "Instructions for use", 1, 0.97),
                          "validity_from": (value, quote, 1, 0.4),
                          "validity_to": ("2030-01-01", quote, 1, 0.5),
                          "cert_number": ("701593/M/12", quote, 1, 0.5)}})
    _, fields, _ = run(_doc(page), llm)
    assert ("validity_from" in fields) is kept
    if kept:
        assert (fields["validity_from"]["value"], fields["validity_from"]["step"]) == (value, "T1")
    assert "validity_to" not in fields and "cert_number" not in fields


def test_an_ifu_keeps_a_proven_date_a_later_read_gave_malformed(t0):
    """Doc 712: T1 read "REV.01_01-2024" as 2024-01-01; the image read, asked
    while the type was still open, gave "2024-01", which as the stronger read
    replaced it and is no date. The IFU keeps the date a read's quote proves."""
    page = "Istruzioni d'uso\nManufacturer: Dental World S.r.l.\nREV.01_01-2024"
    t0({"regulation": t0ev("MDR", 1.0), "manufacturer": t0ev("DENTAL WORLD", 0.97)})
    llm = TextLlm({"T1": {"type": ("IFU", "Istruzioni d'uso / IFU", 1, 0.93),
                          "validity_from": ("2024-01-01", "REV.01_01-2024", 1, 0.35)},
                   "T2-first": {"type": ("IFU", "Istruzioni d'uso", 1, 0.9),
                                "validity_from": ("2024-01", "REV.01_01-2024", 1, 0.4)}})
    _, fields, used = run(_doc(page), llm)
    assert "validity_from" in llm.asked("T2-first")
    assert (fields["validity_from"]["value"], fields["validity_from"]["step"]) == ("2024-01-01", "T1")


def test_an_ifu_never_takes_back_a_date_a_rejection_dropped(t0):
    """Review of the Task 9 fixes: T1's template release date was rejected
    (`checks.not_the_field`), so the ladder never took it; but its quote
    prints the date, and the IFU rule looked for a proven date among every
    read's raw answer, found it there and stored it. A rejected value is no
    read's answer for the field, here as anywhere."""
    page = ("Istruzioni d'uso\nManufacturer: Dental World S.r.l.\n"
            "Template RP00S0.07 Released May 31, 2024")
    t0({"regulation": t0ev("MDR", 1.0), "manufacturer": t0ev("DENTAL WORLD", 0.97)})
    llm = TextLlm({"T1": {"type": ("IFU", "Istruzioni d'uso / IFU", 1, 0.93),
                          "validity_from": ("2024-05-31", "Template RP00S0.07 Released May 31, 2024",
                                            1, 0.3)},
                   "T2-first": {"type": ("IFU", "Istruzioni d'uso", 1, 0.9),
                                "validity_from": ("2024-06", "Istruzioni d'uso", 1, 0.4)}})
    _, fields, _ = run(_doc(page), llm)
    assert fields["type"]["value"] == "IFU"
    assert not (fields.get("validity_from") or {}).get("value")
    assert ("T1", "validity_from") in [(r["step"], r["field"]) for r in tiers.rejected_values(fields)]


@pytest.mark.parametrize("answers, value", [
    # the revision line read alike by T1 and the image read: kept
    ({"T1": ("2023-06-01", "REV.00_06-2023", 1, 0.4),
      "T2-first": ("2023-06-01", "REV.00_06-2023", 1, 0.8)}, "2023-06-01"),
    # a day read off a revision code by every read: no date at all
    ({"T1": ("2026-04-26", "701593/M/12 04/26", 1, 0.6),
      "T2-first": ("2026-04-26", "701593/M/12 04/26", 1, 0.9),
      "T2": ("2026-04-26", "701593/M/12 04/26", 1, 0.9)}, None),
], ids=["revision-line", "made-up-day"])
def test_an_ifu_t0_typed_is_asked_its_revision_date(t0, answers, value):
    """Denis, 2026-10-14: an IFU keeps its revision date as `validity_from`,
    a month and year as the first of the month. An IFU T0 types at the
    threshold was never asked the field, and 6 of the 8 such IFUs on the
    mirror got no date. It is asked now; `validity_to` and `cert_number`
    still are not, and a date no quote prints still goes."""
    quote = answers["T1"][1]
    page = "Instructions for use\nManufacturer: GC EUROPE N.V.\n" + quote
    t0({"type": t0ev("IFU", 1.0), "regulation": t0ev("MDR", 1.0),
        "manufacturer": t0ev("GC EUROPE N.V.", 0.97), "coverage_scope": t0ev("group", 1.0),
        "ref_list": t0ev(["003477"], 1.0), "basic_udi_di": t0ev(None, 1.0)})
    llm = TextLlm({label: {"validity_from": spec} for label, spec in answers.items()})
    _, fields, _ = run(_doc(page), llm)
    assert llm.asked("T1") == {"validity_from"}
    assert (fields.get("validity_from") or {}).get("value") == value


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


@pytest.mark.parametrize("pages", [
    (IMAGE, ARTICLE_LIST, ARTICLE_LIST),       # doc 520: a scanned declaration, a text annex
    (ARTICLE_LIST, IMAGE, ARTICLE_LIST),       # the scanned page second
], ids=["image-page-1", "image-page-2"])
def test_a_scanned_page_among_pages_one_and_two_asks_the_image_doc_class_call(t0, pages):
    """Task 9 replay, doc 520: page 1 is the scanned declaration and pages 2-3
    its article list as text. The text call saw only the list and refused the
    file, which is terminal; production had read it as a DoC with 39 REF codes.
    An image page among pages 1-2 means the text call cannot see what the file
    announces itself to be, so the images are asked, as on a scan."""
    t0({}, doc_class="unknown")
    llm = TextLlm({"T1": FAILED, "T2-first": FAILED, "T2": FAILED},
                  text_class=NOT_A_DOCUMENT, image_class="DoC")
    doc_class, fields, used = run(_doc(*pages), llm)
    assert llm.classified == ["image"]
    assert doc_class == "compliance-doc"
    assert used[:3] == ["T0", "doc-class-image", "T1"]
    assert (fields["type"]["value"], fields["type"]["step"]) == ("DoC", "doc-class-image")


#: A text layer whose fonts map glyphs to arbitrary codes: a TÜV SÜD ISO 13485
#: certificate's pages 1-2 as PyMuPDF reads them (D1393800015_S_sig.pdf, 9% letters).
GARBLED = '!   " #$%&\'(\')*+, "   - .     / .   012324563789:;<88 =52>56?5@8AB8 BCBBD8'


def test_an_unreadable_text_layer_asks_the_image_doc_class_call(t0):
    """Doc-class check, 2026-10-13: 10 certificates production holds (ISO 13485,
    MDSAP, a CE certificate) have text layers under 30% letters. Haiku 5.5
    refused 8 of them ("unreadable binary/encoded content") where Haiku 4.5
    had accepted all 10; their page images were read as what they are, 10 of
    10. FILLER pads the page to a text layer: a sixth of it letters."""
    t0({}, doc_class="unknown")
    llm = TextLlm({"T1": FAILED, "T2-first": FAILED, "T2": FAILED},
                  text_class=NOT_A_DOCUMENT, image_class="ISO")
    doc = pymupdf.open()
    page = doc.new_page()
    for i in range(12):
        page.insert_text((40, 60 + 16 * i), GARBLED, fontsize=9)
    page.insert_text((40, 300), FILLER, fontsize=9)
    doc_class, fields, used = run(doc, llm)
    assert llm.classified == ["image"]
    assert used[:2] == ["T0", "doc-class-image"]
    assert fields["type"]["value"] == "ISO"


@pytest.mark.parametrize("letters, kind", [(0.29, "image"), (0.31, "text")])
def test_the_unreadable_cut_is_thirty_percent_letters(letters, kind):
    n = 1000
    text = "a" * int(n * letters) + "#" * (n - int(n * letters))
    assert tiers._unreadable(text) is (kind == "image")


#: A manual's table of contents: dot leaders make most of its characters.
#: Real case: NSK's Perio Mate manual (OM-DT0912CS000, Czech), 0.28 letters
#: with its leaders counted, 0.92 without.
CONTENTS = "\n".join(f"{n} {title} " + "." * 60 + f" {n * 3}" for n, title in enumerate(
    ("Safety", "Parts", "Use", "Cleaning", "Storage", "Service"), start=1))


def test_a_table_of_contents_is_readable_text():
    """Review of the Task 9 fixes: the letter share counted the dot leaders
    of a table of contents, so manuals whose pages 1-2 are a contents page fell
    under the cut with the garbled certificates. Leader runs (dots, an
    ellipsis character, underscores) are not counted; garbled text still is."""
    assert tiers._unreadable(CONTENTS) is False
    assert tiers._unreadable(CONTENTS.replace(".", "…")) is False
    assert tiers._unreadable(CONTENTS.replace(".", "_")) is False
    assert tiers._unreadable(GARBLED * 4) is True


def test_a_contents_page_keeps_the_text_doc_class_call(t0):
    """The page holds the contents alone (no filler), as a manual's page 2."""
    t0({}, doc_class="unknown")
    llm = TextLlm({"T1": FAILED, "T2-first": FAILED, "T2": FAILED},
                  text_class="IFU", image_class="IFU")
    doc = pymupdf.open()
    page = doc.new_page()
    for i, line in enumerate(("Operation manual", *CONTENTS.splitlines())):
        page.insert_text((40, 60 + 16 * i), line, fontsize=9)
    assert pdf.page_classes(doc) == ["text"]
    run(doc, llm)
    assert llm.classified == ["text"]


def test_text_on_pages_one_and_two_keeps_the_text_call(t0):
    """A scanned page after page 2 does not change what the text call sees."""
    t0({}, doc_class="unknown")
    llm = TextLlm({"T1": FAILED, "T2-first": FAILED, "T2": FAILED},
                  text_class="DoC", image_class="EC")
    run(_doc(DOC_PAGE, DOC_PAGE, IMAGE), llm)
    assert llm.classified == ["text"]


def test_a_type_t0_holds_skips_the_doc_class_call_whatever_the_pages(t0):
    """Only a file T0 could not type is asked: an image page 1 changes nothing
    for a `compliance-doc`."""
    t0(_only_open("cert_number"), doc_class="compliance-doc")
    llm = TextLlm({"T1": FAILED, "T2-first": FAILED, "T2": FAILED},
                  text_class=NOT_A_DOCUMENT, image_class=NOT_A_DOCUMENT)
    doc_class, _, used = run(_doc(IMAGE, DOC_PAGE), llm)
    assert llm.classified == []
    assert (doc_class, used[:2]) == ("compliance-doc", ["T0", "T1"])


def test_a_client_without_an_image_classifier_keeps_the_text_call(t0):
    t0({}, doc_class="unknown")
    llm = TextLlm({"T1": FAILED, "T2-first": FAILED, "T2": FAILED}, text_class="DoC")
    run(_doc(IMAGE, IMAGE, DOC_PAGE), llm)
    assert llm.classified == ["text"]


def test_a_file_name_guess_of_ifu_drops_nothing(t0):
    """Review round 2: a file named "IFU" gives T0 the type at 0.6; both reads
    answer `type` empty, which settles the field with T0's guess still in it.
    That guess is not a type anyone read, so the certificate fields the reads
    found stay."""
    t0({"type": t0ev("IFU", 0.6, "filename:IFU x.pdf"), "regulation": t0ev("MDR", 1.0),
        "manufacturer": t0ev("GC EUROPE N.V.", 0.97)})
    llm = TextLlm({"T1": {"type": (None, "", None, 0.0),
                          "validity_to": ("2030-01-01", "valid to 2030-01-01", 1, 0.9)},
                   "T2-first": {"type": (None, "", None, 0.0)}})
    _, fields, used = run(_doc(DOC_PAGE + "\nvalid until 2030-01-01"), llm)
    assert (fields["type"]["value"], fields["type"]["tier"]) == ("IFU", "T0")
    assert fields["validity_to"]["value"] == "2030-01-01"


# --- the second text read: confirmation of the gate fields (plan Task 5b) -------
#
# The evidence contract GATE reads (`gate._confidences`): `conf` stays the
# stored read's own number; `agreed_model_id` / `agreed_conf` name another read
# that gave the same well-formed value; `proven: "citation"` marks a
# `regulation` its one printed citation proves. GATE applies the 0.70 floor.

def _agreement(fields, f):
    ev_ = fields[f]
    return ev_.get("agreed_model_id"), ev_.get("agreed_conf")


DECLARATION = ("DoC", "EU Declaration of Conformity", 1, 0.9)


@pytest.mark.parametrize("second, stored, agreed", [
    # agrees, verified and surer: its evidence is stored, T1 agrees (ruling D1)
    (("DoC", "EU Declaration of Conformity", 1, 0.95), (0.95, "T1-second"), ("model-T1", 0.9)),
    (("DoC", "EU Declaration of Conformity", 1, 0.85), (0.9, "T1"), ("model-T1-second", 0.85)),
    (("EC", "EC Certificate", 1, 0.95), (0.9, "T1"), (None, None)),   # disagrees: nothing
    ((None, "", None, 0.0), (0.9, "T1"), (None, None)),               # found nothing
    (FAILED, (0.9, "T1"), (None, None)),                              # no answer at all
], ids=["agrees-surer", "agrees", "disagrees", "empty", "failed"])
def test_the_second_text_read_confirms_an_unproven_gate_field(t0, second, stored, agreed):
    """T1 settles `type` by a quote on the page, but a quote is not a second
    read, and GATE scores T1's 0.9 alone. `T1-second` is asked that field
    only, after the ladder; its value never replaces T1's. Of the two when
    they agree, the stored evidence is chosen as `merge` chooses it (D1)."""
    t0(_only_open("type"))
    llm = TextLlm({"T1": {"type": DECLARATION},
                   "T1-second": second if second == FAILED else {"type": second}})
    _, fields, used = run(_doc(DOC_PAGE), llm)
    assert used == ["T0", "T1", "T1-second"]
    assert llm.asked("T1-second") == {"type"}
    assert (fields["type"]["value"], fields["type"]["conf"], fields["type"]["step"]) == (
        "DoC", *stored)
    assert _agreement(fields, "type") == agreed
    record = [r["step"] for r in fields[tiers.READS_KEY]]
    assert record == ["T1", "T1-second"]
    if second == FAILED:
        assert tiers.failed_reads(fields) == ["T1-second"]
    elif agreed == (None, None):
        assert tiers.read_disagreements(fields) == [
            {"field": "type", "steps": ["T1", "T1-second"], "values": ["DoC", second[0]]}]
    else:
        assert tiers.read_disagreements(fields) == []


def test_one_second_read_for_every_unproven_gate_field(t0):
    t0(_only_open("type", "coverage_scope"))
    llm = TextLlm({"T1": {"type": DECLARATION,
                          "coverage_scope": ("group", "REF 003477 003478", 1, 0.9)},
                   "T1-second": {"type": ("DoC", "EU Declaration of Conformity", 1, 0.9),
                                 "coverage_scope": ("group", "REF 003477", 1, 0.8)}})
    _, fields, used = run(_doc(DOC_PAGE), llm)
    assert used.count("T1-second") == 1
    assert llm.asked("T1-second") == {"type", "coverage_scope"}
    assert _agreement(fields, "type") == ("model-T1-second", 0.9)
    assert _agreement(fields, "coverage_scope") == ("model-T1-second", 0.8)


def test_hints_go_to_the_second_read_too(t0):
    t0(_only_open("type"))
    llm = TextLlm({"T1": {"type": DECLARATION}})
    run(_doc(DOC_PAGE), llm, hints="House style notes.")
    assert llm.options("T1-second") == {"hints": "House style notes."}


#: The citation pages: DOC_PAGE prints only 2017/745.
BOTH_CITED = DOC_PAGE + "\nreplaces Directive 93/42/EEC"


@pytest.mark.parametrize("pages, proven, image_asked", [
    ((DOC_PAGE,), True, None),              # only 2017/745 printed: proven, no other read
    # A scanned page may print 93/42: the text proves nothing (Task 9 replay), so
    # the image read is asked, and its agreement confirms the value instead.
    ((DOC_PAGE, IMAGE), False, {"regulation"}),
], ids=["citation", "mixed-pdf"])
def test_a_regulation_its_one_printed_citation_proves_needs_no_second_read(
        t0, pages, proven, image_asked):
    t0(_only_open("regulation"))
    llm = TextLlm({"T1": {"regulation": ("MDR", "Regulation (EU) 2017/745")},
                   "T2-first": {"regulation": ("MDR", "Regulation (EU) 2017/745", 1, 0.85)},
                   "T1-second": {"regulation": ("MDR", "Regulation (EU) 2017/745", 1, 0.95)}})
    _, fields, _ = run(_doc(*pages), llm)
    assert (fields["regulation"].get("proven") == "citation") is proven
    assert llm.asked("T2-first") == image_asked
    assert llm.asked("T1-second") is None
    assert fields["regulation"]["value"] == "MDR"


def test_a_regulation_with_both_citations_printed_is_never_proven(t0):
    """Both citations printed: the checks cannot tell which applies, the image
    read agrees with T1, and the agreement is what GATE counts."""
    t0(_only_open("regulation"))
    llm = TextLlm({"T1": {"regulation": ("MDR", "Regulation (EU) 2017/745")},
                   "T2-first": {"regulation": ("MDR", "Regulation (EU) 2017/745", 1, 0.9)}})
    _, fields, used = run(_doc(BOTH_CITED), llm)
    assert "proven" not in fields["regulation"]
    assert _agreement(fields, "regulation") == ("model-T2-first", 0.9)
    assert "T1-second" not in used        # an agreement GATE counts: proven


@pytest.mark.parametrize("second, stored, agreed", [
    # verified and surer: stored (ruling D1); T1, the surest other, agrees
    (("DoC", "EU Declaration of Conformity", 1, 0.95), (0.95, "T1-second"), ("model-T1", 0.9)),
    (FAILED, (0.9, "T1"), ("model-T2-first", 0.5)),
], ids=["second-read-agrees", "second-read-failed"])
def test_an_image_read_agreeing_under_the_floor_does_not_spare_the_second_read(
        t0, second, stored, agreed):
    """Review finding 9: T1 reads `type` at 0.9, the image read agrees at 0.5,
    and neither quote is on the page. T1's evidence stays (the surer of two
    agreeing reads neither of which the checks verify), the image read is
    stamped as agreeing, and since GATE will not count a 0.5, `T1-second` is
    still asked. The agreeing read stamped is the surest one."""
    t0(_only_open("type"))
    llm = TextLlm({"T1": {"type": ("DoC", "a declaration elsewhere", 1, 0.9)},
                   "T2-first": {"type": ("DoC", "a declaration printed nowhere", 1, 0.5)},
                   "T1-second": second if second == FAILED else {"type": second}})
    _, fields, used = run(_doc(DOC_PAGE), llm)
    assert used == ["T0", "T1", "T2-first", "T1-second"]
    assert (fields["type"]["conf"], fields["type"]["step"]) == stored
    assert _agreement(fields, "type") == agreed


@pytest.mark.parametrize("field, t1, image, agreed", [
    # the reviewer's scenario A: the surer read's quote is not on its page
    ("type", ("DoC", "Declaration printed somewhere else", 1, 0.95),
     ("DoC", "EU Declaration of Conformity", 1, 0.8), ("model-T1", 0.95)),
    ("coverage_scope", ("group", "covers the products listed in annex B", 1, 0.95),
     ("group", "REF 003477 003478", 1, 0.85), ("model-T1", 0.95)),
    ("manufacturer", ("GC EUROPE N.V.", "GC Europe NV, Leuven, Belgium", 1, 0.95),
     ("GC EUROPE N.V.", "Manufacturer: GC EUROPE N.V.", 1, 0.85), (None, None)),
], ids=["type", "coverage-scope", "manufacturer"])
def test_of_two_agreeing_reads_the_verified_one_is_stored(t0, field, t1, image, agreed):
    """Ruling D1 (Denis, 2026-10-13): of two reads that agree, the stored
    evidence is one the checks verify (its quote on its cited page), the
    surer of those; only where neither is verified does the surer read win.
    T1 is surer here, but its quote is nowhere on the page."""
    t0(_only_open(field))
    llm = TextLlm({"T1": {field: t1}, "T2-first": {field: image}})
    _, fields, used = run(_doc(DOC_PAGE), llm)
    ev_ = fields[field]
    assert (ev_["step"], ev_["conf"], ev_["verbatim"]) == ("T2-first", image[3], image[1])
    assert _agreement(fields, field) == agreed
    assert "T1-second" not in used        # 0.85 / 0.8 with 0.95 agreeing: it counts


def test_sonnet_agreeing_with_an_unverified_t1_is_stored(t0):
    """The reviewer's scenario B: the image read finds nothing, Sonnet gives
    T1's value less surely, and only Sonnet's quote is on the page. Sonnet's
    evidence is stored and T1 is the agreeing read (ruling D1 applies after
    Sonnet too)."""
    t0(_only_open("type"))
    llm = TextLlm({"T1": {"type": ("DoC", "Declaration printed somewhere else", 1, 0.95)},
                   "T2-first": {"type": (None, "", None, 0.0)},
                   "T2": {"type": ("DoC", "EU Declaration of Conformity", 1, 0.9)}})
    _, fields, used = run(_doc(DOC_PAGE), llm)
    assert used == ["T0", "T1", "T2-first", "T2"]
    assert (fields["type"]["step"], fields["type"]["conf"]) == ("T2", 0.9)
    assert _agreement(fields, "type") == ("model-T1", 0.95)


@pytest.mark.parametrize("answers, stored", [
    # two verified reads agree, both under the floor (T1 alone under it no
    # longer settles a semantic field: it asks the image read, Task 9 replay)
    ({"T1": {"type": ("DoC", "EU Declaration of Conformity", 1, 0.6)},
      "T2-first": {"type": ("DoC", "EU Declaration of Conformity", 1, 0.6)}}, ("T1", 0.6)),
    # the verified read stored is the less sure one, 0.5: no read can raise it
    ({"T1": {"type": ("DoC", "a declaration elsewhere", 1, 0.9)},
      "T2-first": {"type": ("DoC", "EU Declaration of Conformity", 1, 0.5)}}, ("T2-first", 0.5)),
], ids=["agreeing-under-the-floor", "verified-but-unsure"])
def test_no_second_read_for_a_stored_read_under_the_floor(t0, answers, stored):
    """Review 2026-10-13, F4: GATE combines only when each read is at least
    `gate.agreement_min`, and the stored read's own number is one of the two.
    Under the floor a second read cannot count, so it is not asked."""
    t0(_only_open("type"))
    llm = TextLlm(answers)
    _, fields, used = run(_doc(DOC_PAGE), llm)
    assert "T1-second" not in used
    assert (fields["type"]["step"], fields["type"]["conf"]) == stored


def test_a_read_never_asked_the_field_is_not_its_agreeing_read(t0):
    """Review 2026-10-13, F5: a read answers every field, asked or not. Only a
    read that was asked the field is stamped: here the image read was asked the
    certificate number alone, answered `type` the same anyway, and is not the
    type's agreeing read; the failed `T1-second` leaves none."""
    t0(_only_open("type", "cert_number"))
    llm = TextLlm({"T1": {"type": DECLARATION, "cert_number": ("X-1", "X-1")},
                   "T2-first": {"type": ("DoC", "EU Declaration of Conformity", 1, 0.99),
                                "cert_number": ("G1 021108 0031", "Certificate G1 021108 0031")},
                   "T1-second": FAILED})
    _, fields, used = run(_doc(DOC_PAGE), llm)
    assert llm.asked("T2-first") == {"cert_number"}
    assert used == ["T0", "T1", "T2-first", "T1-second"]
    assert _agreement(fields, "type") == (None, None)


def test_an_agreement_gate_will_count_needs_no_second_read(t0):
    t0(_only_open("type"))
    llm = TextLlm({"T1": {"type": ("DoC", "a declaration elsewhere", 1, 0.9)},
                   "T2-first": {"type": ("DoC", "EU Declaration of Conformity", 1, 0.95)}})
    _, fields, used = run(_doc(DOC_PAGE), llm)
    assert used == ["T0", "T1", "T2-first"]
    # the surer read's evidence is stored; the other is the agreeing read
    assert (fields["type"]["conf"], fields["type"]["step"]) == (0.95, "T2-first")
    assert _agreement(fields, "type") == ("model-T1", 0.9)


def test_sonnet_giving_an_earlier_reads_value_is_stamped_as_agreeing(t0):
    """T1 and the image read disagree; Sonnet prevails with T1's value. T1 is
    then the agreeing read, and the field needs no second read."""
    t0(_only_open("type"))
    llm = TextLlm({"T1": {"type": ("DoC", "a declaration elsewhere", 1, 0.9)},
                   "T2-first": {"type": ("EC", "a certificate", 1, 0.6)},
                   "T2": {"type": ("DoC", "EU Declaration of Conformity", 1, 0.95)}})
    _, fields, used = run(_doc(DOC_PAGE), llm)
    assert used == ["T0", "T1", "T2-first", "T2"]
    assert (fields["type"]["value"], fields["type"]["step"]) == ("DoC", "T2")
    assert _agreement(fields, "type") == ("model-T1", 0.9)


def test_a_scope_that_follows_the_type_is_not_asked_of_the_second_read(t0):
    """Type to scope: the scope is derived from a certificate's type (T0
    evidence, no model), so no read can agree with it; only the type is asked."""
    t0({"regulation": t0ev("MDR", 1.0), "manufacturer": t0ev("GC EUROPE N.V.", 0.97),
        "validity_from": t0ev("2025-08-25", 1.0), "validity_to": t0ev(None, 1.0),
        "cert_number": t0ev("G1 021108 0031", 1.0)})
    page = "EC Certificate\nCertificate G1 021108 0031\nRegulation (EU) 2017/745"
    llm = TextLlm({"T1": {"type": ("EC", "EC Certificate", 1, 0.9)}})
    _, fields, _ = run(_doc(page), llm)
    assert fields["coverage_scope"]["tier"] == "T0"
    # marked for GATE, which scores the type in its place (ruling D3)
    assert fields["coverage_scope"]["follows"] == "type"
    assert fields["coverage_scope"]["conf"] == 0.9          # the type's own number
    assert llm.asked("T1-second") == {"type"}


@pytest.mark.parametrize("t0_type", [t0ev("DoC", 0.9), None], ids=["t0-guess", "empty"])
def test_a_gate_field_no_read_holds_is_not_asked(t0, t0_type):
    """A T0 guess under 0.95 (GATE never combines a row with no model id), or
    an empty field (nothing to confirm): no second read."""
    fields0 = _only_open("type")
    if t0_type is not None:
        fields0["type"] = t0_type
    t0(fields0)
    llm = TextLlm({"T1": {}, "T2-first": {}, "T2": {}})
    _, _, used = run(_doc(DOC_PAGE), llm)
    assert "T1-second" not in used


# --- ruling D1 for a confirming read: one rule for every read (2026-10-13) --------

def test_a_verified_surer_second_read_is_stored_over_an_unverified_t1(t0):
    """The 533048 RA_810 `coverage_scope` shape from the eval: T1 at 0.9 and the
    image read at 0.5 agree, neither quote on the page, so T1 is stored and
    the 0.5 cannot count; `T1-second` agrees at 0.97 with its quote on the
    page. Its evidence is stored now, and T1 becomes the agreeing read."""
    t0(_only_open("coverage_scope"))
    llm = TextLlm({"T1": {"coverage_scope": ("group", "covers the products in annex B", 1, 0.9)},
                   "T2-first": {"coverage_scope": ("group", "products of annex B", 1, 0.5)},
                   "T1-second": {"coverage_scope": ("group", "REF 003477 003478", 1, 0.97)}})
    _, fields, used = run(_doc(DOC_PAGE), llm)
    assert used == ["T0", "T1", "T2-first", "T1-second"]
    scope = fields["coverage_scope"]
    assert (scope["value"], scope["step"], scope["conf"]) == ("group", "T1-second", 0.97)
    assert _agreement(fields, "coverage_scope") == ("model-T1", 0.9)


def test_a_less_sure_unverified_second_read_changes_nothing(t0):
    t0(_only_open("type"))
    llm = TextLlm({"T1": {"type": ("DoC", "EU Declaration of Conformity", 1, 0.9)},
                   "T1-second": {"type": ("DoC", "a declaration elsewhere", 1, 0.85)}})
    _, fields, _ = run(_doc(DOC_PAGE), llm)
    assert (fields["type"]["step"], fields["type"]["conf"]) == ("T1", 0.9)
    assert _agreement(fields, "type") == ("model-T1-second", 0.85)


def test_a_confirmed_certificate_type_carries_its_scope_and_passes_scope_stated(t0):
    """An EC certificate T1 types at 0.90, `T1-second` confirms at 0.95, both
    quotes on the page: the surer is stored (0.95), type to scope runs again,
    so the derived scope carries 0.95, which clears GATE's `scope_stated`
    (`gate.high`, 0.92) on the stored type's own number."""
    from app.config import GateThresholds
    from app.handlers import gate

    t0({"regulation": t0ev("MDR", 1.0), "manufacturer": t0ev("GC EUROPE N.V.", 0.97),
        "validity_from": t0ev("2025-08-25", 1.0), "validity_to": t0ev(None, 1.0),
        "cert_number": t0ev("G1 021108 0031", 1.0)})
    page = "EC Certificate\nCertificate G1 021108 0031\nRegulation (EU) 2017/745"
    llm = TextLlm({"T1": {"type": ("EC", "EC Certificate", 1, 0.90)},
                   "T1-second": {"type": ("EC", "EC Certificate", 1, 0.95)}})
    _, fields, _ = run(_doc(page), llm)
    assert (fields["type"]["step"], fields["type"]["conf"]) == ("T1-second", 0.95)
    assert _agreement(fields, "type") == ("model-T1", 0.90)
    scope = fields["coverage_scope"]
    assert (scope["value"], scope["conf"], scope["follows"]) == ("manufacturer", 0.95, "type")
    assert gate._field_confidence(fields, "coverage_scope", {}) >= GateThresholds().high


# --- content only an image read sees: an annotation painting an image ----------
#
# Task 8, 2026-10-13: VOCO Ceramic Bond prints its issue date only inside the
# e-signature field's appearance, an image on a sparse page 3. T1's empty
# `validity_from` passed the null rule (no date label in the text) and settled,
# no image read was made, and the date was lost. A page whose annotation paints
# an image is a page T1 did not see in full: unread for the null rule, and the
# image read renders it, as it renders a scanned page of a mixed PDF.

#: DOC_PAGE with no issue date: nothing on it labels `validity_from`.
UNDATED_PAGE = DOC_PAGE.replace("\nDate of issue: 2025-08-25", "")
SIGNATURES = "Signatures\nThis document has been electronically signed by:"
SIGNED_DATE = ("2026-01-14", SIGNED_AT, 6, 0.9)


def test_a_null_beside_a_signature_image_goes_to_an_image_read_of_that_page(t0):
    t0(_only_open("validity_from"))
    doc = annotate(_doc(UNDATED_PAGE, FILLER, FILLER, FILLER, FILLER, SIGNATURES))
    llm = TextLlm({"T1": {}, "T2-first": {"validity_from": SIGNED_DATE},
                   "T2": {"validity_from": SIGNED_DATE}})
    _, fields, used = run(doc, llm)
    # The date is in no text the checks could prove it by; cited on the page
    # whose annotation paints it, well-formed at 0.9, it settles as a scan read
    # does (Denis, 2026-10-13), and Sonnet is not asked.
    assert used == ["T0", "T1", "T2-first"]
    assert llm.options("T2-first") == {"pages": (1, 2, 3, 6)}
    assert (fields["validity_from"]["value"], fields["validity_from"]["step"],
            fields["validity_from"]["page"]) == ("2026-01-14", "T2-first", 6)
    assert tiers.PAGES_UNREAD_KEY not in fields


# --- a value only an image shows (Denis, 2026-10-13) ---------------------------

def _signed_six_pages():
    return annotate(_doc(UNDATED_PAGE, FILLER, FILLER, FILLER, FILLER, SIGNATURES))


@pytest.mark.parametrize("answer, settles", [
    (("2026-01-14", SIGNED_AT, 6, 0.85), True),       # at `extract.image_settle`
    (("2026-01-14", SIGNED_AT, 6, 0.84), False),      # under it: Sonnet decides
    (("14.01.2026", SIGNED_AT, 6, 0.95), False),      # not an ISO date: not well-formed
    (("2026-01-14", SIGNED_AT, 1, 0.95), False),      # cited on a text page: the checks
], ids=["at-0.85", "at-0.84", "malformed", "text-page"])
def test_a_value_cited_on_the_annotation_page_settles_at_image_settle(t0, answer, settles):
    t0(_only_open("validity_from"))
    llm = TextLlm({"T1": {}, "T2-first": {"validity_from": answer},
                   "T2": {"validity_from": SIGNED_DATE}})
    _, fields, used = run(_signed_six_pages(), llm)
    assert ("T2" not in used) is settles
    if settles:
        assert fields["validity_from"]["step"] == "T2-first"


def test_image_settle_is_the_configured_one(t0):
    t0(_only_open("validity_from"))
    llm = TextLlm({"T1": {}, "T2-first": {"validity_from": SIGNED_DATE},
                   "T2": {"validity_from": SIGNED_DATE}})
    _, _, used = run(_signed_six_pages(), llm, image_settle=0.95)
    assert used == ["T0", "T1", "T2-first", "T2"]


def test_a_value_cited_on_a_scanned_page_of_a_mixed_pdf_settles_too(t0):
    t0(_only_open("validity_from"))
    date = ("2025-08-25", "25.08.2025", 2, 0.9)
    llm = TextLlm({"T1": {}, "T2-first": {"validity_from": date}, "T2": {}})
    _, fields, used = run(_doc(UNDATED_PAGE, IMAGE), llm)
    assert used == ["T0", "T1", "T2-first"]
    assert fields["validity_from"]["value"] == "2025-08-25"


@pytest.mark.parametrize("labelled, settles", [(False, True), (True, False)],
                         ids=["no-udi-label", "udi-label"])
def test_a_udi_off_an_image_settles_only_where_the_text_labels_none(t0, labelled, settles):
    """Never a Basic UDI-DI the text labels: a multi-UDI list goes to Sonnet.
    `B-12345` is well-formed (EUDAMED-assigned) and not check-valid, so only
    the image rule could settle it."""
    t0(_only_open("basic_udi_di"))
    page = UNDATED_PAGE + ("\nBasic UDI-DI: see annex" if labelled else "")
    doc = _doc(page, IMAGE)
    llm = TextLlm({"T1": {}, "T2-first": {"basic_udi_di": ("B-12345", "B-12345", 2, 0.95)},
                   "T2": {"basic_udi_di": ("B-12345", "B-12345", 2, 0.95)}})
    _, _, used = run(doc, llm)
    assert ("T2" not in used) is settles


def test_two_empty_reads_settle_once_the_signature_page_was_rendered(t0):
    t0(_only_open("validity_from"))
    llm = TextLlm({"T1": {}, "T2-first": {}, "T2": {}})
    _, fields, used = run(annotate(_doc(UNDATED_PAGE, SIGNATURES)), llm)
    assert used == ["T0", "T1", "T2-first"]
    assert tiers.PAGES_UNREAD_KEY not in fields


@pytest.mark.parametrize("kw", [
    None,                                                    # no annotation at all
    {"appearance": "text", "text": "Digitally signed"},      # its text is in the layer
    {"appearance": None},                                    # an invisible signature
], ids=["no-annotation", "text-appearance", "no-appearance"])
def test_a_signature_painting_no_image_leaves_the_null_rule_as_it_was(t0, kw):
    t0(_only_open("validity_from"))
    doc = _doc(UNDATED_PAGE, SIGNATURES)
    llm = TextLlm({"T1": {}, "T2-first": {}, "T2": {}})
    _, _, used = run(doc if kw is None else annotate(doc, **kw), llm)
    assert used == ["T0", "T1"]


def test_without_page_lists_a_signature_image_past_page_four_is_unread():
    llm = TextLlm({"T1": {}, "T2-first": {}, "T2": {}})
    doc = annotate(_doc(UNDATED_PAGE, FILLER, FILLER, FILLER, FILLER, SIGNATURES))
    used = ["T0"]

    def read(label, missing, **kw):
        return llm.extract(label, doc, "d.pdf", missing, **kw)

    _, out = tiers.run_text_ladder(doc, "d.pdf", "compliance-doc",
                                   dict(_only_open("validity_from")), used, read,
                                   page_aware=False)
    assert used == ["T0", "T1", "T2-first", "T2"]
    assert out[tiers.PAGES_UNREAD_KEY] == ["validity_from"]


@pytest.mark.parametrize("painted, proven", [(False, tiers.CITATION), (True, None)],
                         ids=["signature-page", "signature-image"])
def test_a_citation_proves_a_regulation_only_where_the_text_holds_every_page(
        t0, painted, proven):
    """`checks.citation_proven` claims the other citation is NOT printed; an
    image a text read cannot see leaves that unknown, as a scanned page does.
    Since the Task 9 fixes the absence proof does not settle T1's `n.a.` there
    either: the image read, which renders the painted page, is asked, and two
    agreeing reads settle it, unproven."""
    t0(_only_open("regulation"))
    page = UNDATED_PAGE.replace("Regulation (EU) 2017/745", "Regulation (EC) No 1223/2009")
    doc = _doc(page, SIGNATURES)
    llm = TextLlm({"T1": {"regulation": ("n.a.", "Regulation (EC) No 1223/2009", 1, 0.85)},
                   "T2-first": {"regulation": ("n.a.", "Regulation (EC) No 1223/2009", 1, 0.85)}})
    _, fields, used = run(annotate(doc) if painted else doc, llm)
    assert fields["regulation"]["value"] == "n.a."
    assert fields["regulation"].get(tiers.PROVEN_KEY) == proven
    assert ("T2-first" in used) is painted


# --- the two real documents ------------------------------------------------------

VOCO_SIGNED = "VOCO/VOCO_DoC_MDR_Ceramic Bond_2026-1-signed.pdf"
LUMIWHITE = "LUMIWHITE/Declaration_of_Conformity_LUMIWHITE_OU.pdf"


def _run_fixture(path, llm):
    with pdf.open_doc(path) as doc:
        return tiers.run_extraction(doc, path, llm=llm, threshold=0.95)


def test_voco_ceramic_bond_reads_its_issue_date_off_the_signature_image(fixture_pdf):
    """The Task 8 case on the document itself: T0 real, the reads stubbed with
    what the run's reads answered (T1: the two REF codes on page 2, no date;
    the image read: 2026-01-14, "Cuxhaven, 14.01.2026", page 3)."""
    date = ("2026-01-14", SIGNED_AT, 3, 0.9)
    llm = TextLlm({"T1": {"ref_list": (["1106", "1107"], "1106 Bottle 5 ml 1107", 2, 0.85)},
                   "T2-first": {"validity_from": date}, "T2": {"validity_from": date}})
    _, fields, used = _run_fixture(fixture_pdf(VOCO_SIGNED), llm)
    assert llm.asked("T1") == {"validity_from", "ref_list"}
    assert used == ["T0", "T1", "T2-first"]          # settled off the stamp: no Sonnet
    # Three pages: the image read renders all of them, the default request.
    assert llm.asked("T2-first") == {"validity_from"} and llm.options("T2-first") == {}
    assert fields["validity_from"]["value"] == "2026-01-14"


def test_lumiwhites_sparse_signature_page_keeps_its_citation_proof_and_null_rule(fixture_pdf):
    """A signature page with no annotation stays a page the text reads in full:
    `n.a.` is proven by the citation alone, and the empty fields settle on T1,
    all but the manufacturer, which one empty read never settles (Task 9
    replay, doc 2173): the image read is asked that field alone."""
    quote = ("LUMI SOOTHE Desensitising Gel", 1)
    llm = TextLlm({"T1": {"regulation": ("n.a.", "Regulation (EC) No. 1223/2009", 1, 0.85),
                          "validity_from": ("2025-04-20", "Date: 20 April 2025", 1, 0.9),
                          "coverage_scope": ("group", *quote, 0.85),
                          # a read answers every field: these two, pursued once
                          # T1's scope is `group`, it answered empty
                          "ref_list": ([], "", 1, 0.6), "basic_udi_di": (None, "", 1, 0.9)},
                   "T1-second": {"coverage_scope": ("group", *quote, 0.9)}})
    _, fields, used = _run_fixture(fixture_pdf(LUMIWHITE), llm)
    assert fields["regulation"][tiers.PROVEN_KEY] == tiers.CITATION
    assert used == ["T0", "T1", "T2-first", "T1-second"]
    assert llm.asked("T2-first") == {"manufacturer"}
