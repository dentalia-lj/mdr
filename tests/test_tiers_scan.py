"""The scan ladder, S-2 (extraction-ladder plan Task 3; PRD v3 section 5,
amended 2026-10-12): a document with no text page skips every text read, is
classified off its page images, and is read by a cheap image read, a second
cheap read for what that leaves open, and Sonnet only for what still is.

The documents here are real PyMuPDF documents built in memory, so
`pdf.has_text_page`, the page count and the page classes are the real ones. T0
is replaced where a test needs a given starting point, and the LLM is a stub:
no test here makes a call.
"""

from __future__ import annotations

import pymupdf
import pytest

from app.extract import pdf, tiers
from app.extract.tiers import NOT_A_DOCUMENT


# --- documents ---------------------------------------------------------------

def _png() -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 80), False)
    pix.clear_with(200)
    return pix.tobytes("png")


def _scan(pages: int) -> pymupdf.Document:
    """`pages` scanned pages: a full-page image each, no text layer."""
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page().insert_image(pymupdf.Rect(0, 0, 595, 842), stream=_png())
    return doc


def _blank(pages: int = 1) -> pymupdf.Document:
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page()
    return doc


def _mixed() -> pymupdf.Document:
    """Page 1 a scan, page 2 a real text layer."""
    doc = _scan(1)
    page = doc.new_page()
    for i in range(12):
        page.insert_text((72, 72 + 20 * i), "EU Declaration of Conformity text line.")
    return doc


# --- stubs -------------------------------------------------------------------

def ev(value, conf, page=1):
    return {"value": value, "conf": conf, "verbatim": f"read {value}", "page": page,
            "tier": "T2", "model_id": "stub-model"}


class ScanLlm:
    """Answers by label from `answers[label][field] = (value, conf)`; records
    every read as (label, missing). `classify_image` exists only when an
    answer for it is given, as on a client that predates it."""

    def __init__(self, answers=None, image_class=None, text_class=None):
        self.answers = answers or {}
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self.classified: list[str] = []
        if image_class is not None:
            self.classify_image = lambda doc, filename: self._classify("image", image_class)
        if text_class is not None:
            self.classify = lambda doc, filename: self._classify("text", text_class)

    def _classify(self, kind, answer):
        self.classified.append(kind)
        return answer, "quoted header"

    def extract(self, label, doc, filename, missing, **kw):
        self.calls.append((label, tuple(missing)))
        given = self.answers.get(label, {})
        return {f: ev(*given[f]) for f in missing if f in given}

    def asked(self, label):
        return next((set(m) for lb, m in self.calls if lb == label), None)

    @property
    def labels(self):
        return [lb for lb, _ in self.calls]


@pytest.fixture
def t0(monkeypatch):
    """Set T0's answer: `t0(doc_class, fields)`."""
    def _set(doc_class="compliance-doc", fields=None):
        monkeypatch.setattr(
            tiers, "t0_extract",
            lambda doc, filename="", result=None, companion_url=None: (
                doc_class, {k: dict(v) for k, v in (fields or {}).items()}))
    return _set


def t0ev(value, conf):
    return {"value": value, "conf": conf, "tier": "T0", "verbatim": f"t0 {value}", "page": None}


def run(doc, llm, filename="scan.pdf", **kw):
    return tiers.run_extraction(doc, filename, llm=llm, threshold=0.95, **kw)


#: What a cheap read answers on a DoC scan when everything is legible.
EVERYTHING = {
    "type": ("DoC", 0.95), "regulation": ("MDR", 0.95), "validity_from": ("2025-08-25", 0.95),
    "validity_to": ("2030-05-26", 0.95), "coverage_scope": ("group", 0.95),
    "cert_number": ("G10 082649 0004", 0.95), "manufacturer": ("GC EUROPE N.V.", 0.95),
    "ref_list": (["003477", "003478"], 0.95), "basic_udi_di": ("++J022MD0098KV", 0.95),
}


# --- routing -----------------------------------------------------------------

def test_a_scan_never_reaches_a_text_read(t0):
    t0()
    llm = ScanLlm({"T2-first": EVERYTHING})
    _, fields, used = run(_scan(1), llm)
    assert used == ["T0", "T2-first"]
    assert llm.labels == ["T2-first"]
    assert fields["regulation"]["step"] == "T2-first"


def test_a_mixed_pdf_takes_the_text_branch(t0):
    """A text page anywhere makes it a text document; page 1 being a scan still
    forces the Sonnet read there, as before (Task 4 rebuilds that branch)."""
    doc = _mixed()
    assert pdf.has_text_page(doc) and pdf.is_scan(doc)
    t0()
    llm = ScanLlm({"T1": {}, "T2": {}})
    _, _, used = run(doc, llm)
    assert used == ["T0", "T1", "T2"]
    assert "T2-first" not in llm.labels


# --- the doc-class call on page images ---------------------------------------

def test_a_blank_document_is_classified_from_its_images_and_refused(t0):
    """A blank page has no text page, so it is a scan: the doc-class call
    looks at the images (never at the empty text), and the refusal is
    terminal."""
    doc = _blank()
    assert not pdf.has_text_page(doc)
    t0("unknown")
    llm = ScanLlm(image_class=NOT_A_DOCUMENT, text_class="DoC")
    doc_class, fields, used = run(doc, llm)
    assert doc_class == NOT_A_DOCUMENT and fields == {}
    assert llm.classified == ["image"]           # never the text call
    assert llm.calls == [] and used == ["T0", "doc-class-image"]


def test_a_scanned_invoice_is_not_a_document(t0):
    t0("unknown")
    llm = ScanLlm({"T2-first": EVERYTHING}, image_class=NOT_A_DOCUMENT)
    doc_class, fields, used = run(_scan(2), llm)
    assert (doc_class, fields) == (NOT_A_DOCUMENT, {})
    assert llm.calls == []                       # no field read is paid for


def test_an_accepted_scan_class_is_type_evidence_ranked_below_the_reads(t0):
    t0("unknown")
    llm = ScanLlm({"T2-first": {"type": ("EC", 0.95)}}, image_class="DoC")
    doc_class, fields, used = run(_scan(1), llm)
    assert doc_class == "compliance-doc"
    assert used[:3] == ["T0", "doc-class-image", "T2-first"]
    # The image read's answer prevails over the one-word header answer.
    assert fields["type"]["value"] == "EC" and fields["type"]["step"] == "T2-first"


def test_the_scan_class_is_kept_when_the_reads_say_nothing(t0):
    t0("unknown")
    llm = ScanLlm({}, image_class="IFU")
    _, fields, _ = run(_scan(1), llm)
    assert fields["type"] == {"value": "IFU", "conf": tiers.T1_DOC_CLASS_CONF, "tier": "T2",
                              "verbatim": "quoted header", "page": 1,
                              "step": "doc-class-image"}


def test_a_scan_t0_already_typed_is_not_classified_again(t0):
    t0("compliance-doc", {"type": t0ev("DoC", 0.6)})
    llm = ScanLlm({"T2-first": EVERYTHING}, image_class=NOT_A_DOCUMENT)
    doc_class, _, _ = run(_scan(1), llm)
    assert doc_class == "compliance-doc" and llm.classified == []


def test_a_client_without_an_image_classifier_reads_an_unknown_scan_as_before(t0):
    """`classify_image` is optional on the injected llm. The TEXT classifier is
    never asked about a scan: it would see no text and refuse it."""
    t0("unknown")
    llm = ScanLlm({"T2-first": EVERYTHING}, text_class=NOT_A_DOCUMENT)
    doc_class, _, used = run(_scan(1), llm)
    assert doc_class == "unknown" and llm.classified == []
    assert used == ["T0", "T2-first"]


# --- 1, 3 and 6 pages --------------------------------------------------------

@pytest.mark.parametrize("pages, unread", [(1, False), (3, False), (6, True)])
def test_an_empty_field_on_a_short_scan_settles_and_on_a_long_one_is_unread(t0, pages, unread):
    """Both cheap reads leave `validity_to` empty. On a scan the image reads
    see whole, two empty reads settle it as absent; on a 6-page scan pages 5-6
    were never read, so it is unread instead: not settled, not sent to Sonnet
    (who sees the same four pages), and flagged."""
    t0()
    answers = {k: v for k, v in EVERYTHING.items() if k != "validity_to"}
    llm = ScanLlm({"T2-first": answers, "T2-second": {}})
    _, fields, used = run(_scan(pages), llm)
    assert llm.asked("T2-second") == {"validity_to"}
    assert "T2" not in used                       # Sonnet never asked
    if unread:
        assert fields[tiers.PAGES_UNREAD_KEY] == ["validity_to"]
        assert tiers.PAGES_UNREAD_FLAG in tiers.integrity_flags(fields)
    else:
        assert tiers.PAGES_UNREAD_KEY not in fields
        assert tiers.PAGES_UNREAD_FLAG not in tiers.integrity_flags(fields)


def test_a_field_one_read_found_on_a_long_scan_still_goes_to_sonnet(t0):
    """Unread means BOTH cheap reads were empty. Two reads that disagree, one
    empty and one not, are a disagreement about pages Sonnet does see."""
    t0()
    first = {k: v for k, v in EVERYTHING.items() if k != "cert_number"}
    llm = ScanLlm({"T2-first": first, "T2-second": {"cert_number": ("C-1", 0.8)},
                   "T2": {"cert_number": ("C-1", 0.9)}})
    _, fields, used = run(_scan(6), llm)
    assert llm.asked("T2") == {"cert_number"}
    assert tiers.PAGES_UNREAD_KEY not in fields


# --- the first read settles at image_settle ----------------------------------

@pytest.mark.parametrize("conf, settles", [(0.85, True), (0.84, False)])
def test_the_first_read_settles_a_well_formed_field_at_image_settle(t0, conf, settles):
    t0()
    first = dict(EVERYTHING, cert_number=("G10 082649 0004", conf))
    llm = ScanLlm({"T2-first": first, "T2-second": {"cert_number": ("X-9", 0.9)},
                   "T2": {"cert_number": ("G10 082649 0004", 0.9)}})
    _, _, used = run(_scan(1), llm)
    if settles:
        assert used == ["T0", "T2-first"]
    else:
        assert llm.asked("T2-second") == {"cert_number"}


def test_a_malformed_value_never_settles_at_any_confidence(t0):
    """A date that is not ISO, a notified-body number for a certificate: the
    shape is wrong whatever the read says about itself."""
    t0()
    first = dict(EVERYTHING, validity_from=("25.08.2025", 0.99), cert_number=("0482", 0.99))
    llm = ScanLlm({"T2-first": first, "T2-second": {}, "T2": {}})
    run(_scan(1), llm)
    assert llm.asked("T2-second") == {"validity_from", "cert_number"}


def test_the_configured_threshold_is_the_one_used(t0):
    t0()
    first = dict(EVERYTHING, cert_number=("G10 082649 0004", 0.88))
    llm = ScanLlm({"T2-first": first, "T2-second": {}, "T2": {}})
    run(_scan(1), llm, image_settle=0.90)
    assert llm.asked("T2-second") == {"cert_number"}


# --- the second read: agreement ----------------------------------------------

@pytest.mark.parametrize("conf, agreed", [(0.70, True), (0.69, False)])
def test_two_cheap_reads_agreeing_at_agreement_min_settle(t0, conf, agreed):
    t0()
    first = dict(EVERYTHING, manufacturer=("GC EUROPE N.V.", 0.80))
    llm = ScanLlm({"T2-first": first,
                   "T2-second": {"manufacturer": ("GC Europe N.V", conf)},
                   "T2": {"manufacturer": ("GC EUROPE N.V.", 0.9)}})
    _, fields, used = run(_scan(1), llm)
    assert ("T2" in used) is not agreed
    if agreed:   # the first read's evidence stays: the second only confirmed it
        assert fields["manufacturer"]["step"] == "T2-first"
        assert fields["manufacturer"]["conf"] == 0.80


def test_disagreeing_reads_go_to_sonnet_and_sonnet_prevails(t0):
    t0()
    first = dict(EVERYTHING, type=("EC", 0.80))
    llm = ScanLlm({"T2-first": first, "T2-second": {"type": ("ISO", 0.80)},
                   "T2": {"type": ("DoC", 0.90)}})
    _, fields, used = run(_scan(1), llm)
    assert used == ["T0", "T2-first", "T2-second", "T2"]
    assert llm.asked("T2") == {"type"}
    assert fields["type"]["value"] == "DoC" and fields["type"]["step"] == "T2"


def test_an_empty_sonnet_answer_never_erases_a_cheap_read(t0):
    t0()
    first = dict(EVERYTHING, type=("EC", 0.80))
    llm = ScanLlm({"T2-first": first, "T2-second": {"type": ("ISO", 0.80)}, "T2": {}})
    _, fields, _ = run(_scan(1), llm)
    assert fields["type"]["value"] == "EC"


@pytest.mark.parametrize("field_name, settles", [
    ("validity_to", True), ("cert_number", True), ("ref_list", True),
    ("basic_udi_di", False),          # never on two nulls on a scan
])
def test_two_empty_reads_settle_every_field_but_the_udi(t0, field_name, settles):
    t0()
    first = {k: v for k, v in EVERYTHING.items() if k != field_name}
    llm = ScanLlm({"T2-first": first, "T2-second": {}, "T2": {}})
    _, _, used = run(_scan(2), llm)
    assert ("T2" in used) is not settles
    if not settles:
        assert llm.asked("T2") == {"basic_udi_di"}


# --- type to scope -----------------------------------------------------------

@pytest.mark.parametrize("doc_type, read_scope, expected", [
    ("EC", "group", "manufacturer"),    # the eval's MDD certificate: both reads said group
    ("ISO", "group", "manufacturer"),
    ("IFU", "manufacturer", "group"),
    ("EC", None, "manufacturer"),       # an empty scope is filled
    ("EC", "manufacturer", "manufacturer"),
])
def test_a_known_type_decides_a_disagreeing_or_empty_scope(t0, doc_type, read_scope, expected):
    t0()
    first = dict(EVERYTHING, type=(doc_type, 0.90))
    first.pop("coverage_scope")
    if read_scope is not None:
        first["coverage_scope"] = (read_scope, 0.90)
    second = {"coverage_scope": (read_scope, 0.90)} if read_scope else {}
    llm = ScanLlm({"T2-first": first, "T2-second": second, "T2": {}})
    _, fields, used = run(_scan(1), llm)
    assert fields["coverage_scope"]["value"] == expected
    assert "coverage_scope" not in (llm.asked("T2") or set())
    if read_scope != expected:   # replaced by the derivation, no surer than the type
        assert fields["coverage_scope"]["tier"] == "T0"
        assert fields["coverage_scope"]["verbatim"] == f"type={doc_type}"
        assert fields["coverage_scope"]["conf"] == 0.90
    else:                        # agrees: the read keeps its own evidence
        assert fields["coverage_scope"]["step"] == "T2-first"


def test_a_doc_keeps_the_scope_its_reads_give(t0):
    t0()
    llm = ScanLlm({"T2-first": dict(EVERYTHING, coverage_scope=("group", 0.95))})
    _, fields, _ = run(_scan(1), llm)
    assert fields["coverage_scope"]["value"] == "group"


def test_an_unsettled_type_does_not_decide_the_scope_before_sonnet(t0):
    t0()
    first = dict(EVERYTHING, type=("EC", 0.60), coverage_scope=("group", 0.60))
    llm = ScanLlm({"T2-first": first, "T2-second": {"type": ("ISO", 0.6)},
                   "T2": {"type": ("EC", 0.9), "coverage_scope": ("group", 0.9)}})
    _, fields, _ = run(_scan(1), llm)
    assert llm.asked("T2") == {"type", "coverage_scope"}
    # Sonnet's type closes the field, and its scope then follows from it.
    assert fields["coverage_scope"]["value"] == "manufacturer"


# --- Sonnet: only what is still open -----------------------------------------

def test_sonnet_is_never_called_when_nothing_is_open(t0):
    t0()
    llm = ScanLlm({"T2-first": EVERYTHING})
    _, _, used = run(_scan(3), llm)
    assert used == ["T0", "T2-first"] and "T2" not in llm.labels


def test_sonnet_is_asked_only_the_fields_still_open(t0):
    t0()
    first = dict(EVERYTHING, cert_number=("C-1", 0.75), manufacturer=("GC", 0.75))
    llm = ScanLlm({"T2-first": first,
                   "T2-second": {"cert_number": ("C-1", 0.75), "manufacturer": ("Other", 0.9)},
                   "T2": {"manufacturer": ("GC EUROPE N.V.", 0.9)}})
    run(_scan(2), llm)
    assert llm.asked("T2-second") == {"cert_number", "manufacturer"}
    assert llm.asked("T2") == {"manufacturer"}   # cert_number agreed


def test_an_agreement_needs_both_reads_over_the_floor(t0):
    t0()
    first = dict(EVERYTHING, cert_number=("C-1", 0.5))
    llm = ScanLlm({"T2-first": first, "T2-second": {"cert_number": ("C-1", 0.75)},
                   "T2": {"cert_number": ("C-1", 0.9)}})
    run(_scan(2), llm)
    assert llm.asked("T2") == {"cert_number"}


def test_t0_settled_fields_are_never_asked(t0):
    t0("compliance-doc", {"regulation": t0ev("MDR", 1.0), "type": t0ev("DoC", 0.6)})
    llm = ScanLlm({"T2-first": EVERYTHING})
    _, fields, _ = run(_scan(1), llm)
    assert "regulation" not in llm.asked("T2-first")
    assert "type" in llm.asked("T2-first")
    assert fields["regulation"]["tier"] == "T0"


def test_a_manufacturer_scope_t0_guessed_still_asks_for_item_ids(t0):
    """A DoC typed off its file name with no REF list gets `manufacturer` at 0.6
    from T0. That is a guess, so the cheap read is still asked for the item
    identifiers a DoC needs."""
    t0("compliance-doc", {"type": t0ev("DoC", 0.6), "coverage_scope": t0ev("manufacturer", 0.6)})
    llm = ScanLlm({"T2-first": EVERYTHING})
    run(_scan(1), llm)
    assert {"ref_list", "basic_udi_di"} <= llm.asked("T2-first")


def test_a_known_manufacturer_scope_stops_the_item_id_reads(t0):
    t0()
    first = dict(EVERYTHING, type=("ISO", 0.95), coverage_scope=("manufacturer", 0.95))
    first.pop("basic_udi_di")
    llm = ScanLlm({"T2-first": first, "T2-second": {}, "T2": {}})
    _, _, used = run(_scan(1), llm)
    assert used == ["T0", "T2-first"]           # the empty UDI is not chased


def test_a_known_ifu_stops_the_certificate_reads(t0):
    t0()
    first = {"type": ("IFU", 0.95), "regulation": ("MDR", 0.95),
             "manufacturer": ("GC EUROPE N.V.", 0.95), "ref_list": (["1"], 0.95),
             "basic_udi_di": ("++J022MD0098KV", 0.95)}
    llm = ScanLlm({"T2-first": first, "T2-second": {}, "T2": {}})
    _, _, used = run(_scan(1), llm)
    assert used == ["T0", "T2-first"]


# --- every read kept, disagreements counted ----------------------------------

def test_every_read_is_kept_on_the_attempt(t0):
    t0()
    first = dict(EVERYTHING, type=("EC", 0.80))
    llm = ScanLlm({"T2-first": first, "T2-second": {"type": ("ISO", 0.70)},
                   "T2": {"type": ("EC", 0.90)}})
    _, fields, _ = run(_scan(1), llm)
    reads = fields[tiers.READS_KEY]
    assert [r["step"] for r in reads] == ["T2-first", "T2-second", "T2"]
    assert reads[1] == {"step": "T2-second", "model_id": "stub-model", "fields": {
        "type": {"value": "ISO", "conf": 0.70, "page": 1}}}
    assert reads[0]["fields"]["type"] == {"value": "EC", "conf": 0.80, "page": 1}


def test_a_field_asked_and_not_answered_is_kept_as_none(t0):
    t0()
    llm = ScanLlm({"T2-first": {"type": ("EC", 0.95)}, "T2-second": {}, "T2": {}})
    _, fields, _ = run(_scan(1), llm)
    assert fields[tiers.READS_KEY][1]["fields"]["regulation"] is None


def test_disagreements_list_the_field_the_steps_and_the_values(t0):
    t0()
    first = dict(EVERYTHING, type=("EC", 0.80), manufacturer=("GC EUROPE N.V.", 0.80))
    llm = ScanLlm({"T2-first": first,
                   "T2-second": {"type": ("ISO", 0.80), "manufacturer": ("GC Europe N.V", 0.80)},
                   "T2": {"type": ("EC", 0.90)}})
    _, fields, _ = run(_scan(1), llm)
    assert tiers.read_disagreements(fields) == [
        {"field": "type", "steps": ["T2-first", "T2-second", "T2"],
         "values": ["EC", "ISO", "EC"]}]       # manufacturer agreed, normalized


def test_an_empty_answer_against_a_value_is_a_disagreement():
    fields = {tiers.READS_KEY: [
        {"step": "T2-first", "model_id": "m",
         "fields": {"cert_number": {"value": "C-1", "conf": .8, "page": 1}}},
        {"step": "T2-second", "model_id": "m", "fields": {"cert_number": None}}]}
    assert tiers.read_disagreements(fields) == [
        {"field": "cert_number", "steps": ["T2-first", "T2-second"], "values": ["C-1", None]}]


def test_no_reads_no_disagreements():
    assert tiers.read_disagreements({}) == []


# --- merge: the stronger read prevails ---------------------------------------

def _e(value, tier, step=None, conf=0.9):
    out = {"value": value, "conf": conf, "tier": tier, "verbatim": str(value), "page": 1}
    if step:
        out["step"] = step
    return out


@pytest.mark.parametrize("incumbent, challenger, winner", [
    # stronger over weaker, in both orders
    (_e("A", "T2", "T2-first"), _e("B", "T2", "T2"), "B"),           # Sonnet over cheap image
    (_e("A", "T2", "T2"), _e("B", "T2", "T2-first"), "A"),
    (_e("A", "T1"), _e("B", "T2", "T2-first"), "B"),                  # cheap image over text
    (_e("A", "T2", "T2-first"), _e("B", "T1"), "A"),
    (_e("A", "T0", conf=0.6), _e("B", "T1"), "B"),                    # text over a T0 guess
    (_e("A", "T0", conf=0.95), _e("B", "T2", "T2"), "A"),             # T0 at 0.95: never
    (_e("A", "T2", "T2-first"), _e("B", "T2", "T2-second"), "A"),     # a second read confirms
    (_e("A", "T2", "doc-class-image"), _e("B", "T2", "T2-second"), "B"),
    (_e("A", "T1"), _e("B", "T2"), "B"),                              # today's text ladder
    (_e("A", "T1"), _e("B", "T1"), "B"),                              # equal: later wins
    (_e(None, "T0", conf=1.0), _e("B", "T2", "T2-first"), "B"),       # a blank is no rival
])
def test_merge_lets_the_stronger_read_prevail(incumbent, challenger, winner):
    got = tiers.merge({"type": incumbent}, {"type": challenger})
    assert got["type"]["value"] == winner


def test_merge_keeps_its_blank_guard_against_a_stronger_read():
    got = tiers.merge({"type": _e("A", "T2", "T2-first")}, {"type": _e(None, "T2", "T2")})
    assert got["type"]["value"] == "A"


@pytest.mark.parametrize("incumbent, challenger, winner", [
    (_e(["1", "2", "3"], "T2", "T2"), _e(["1", "2"], "T2", "T2"), ["1", "2", "3"]),  # no shrink
    (_e(["1"], "T2", "T2"), _e(["1", "2"], "T2", "T2-second"), ["1", "2"]),  # longer wins
    (_e(["1", "2"], "T2", "T2"), _e(["3", "4"], "T2", "T2-second"), ["1", "2"]),  # equal: stronger
    (_e(["1"], "T0", conf=0.95), _e(["1", "2"], "T2", "T2"), ["1"]),     # T0 at 0.95 stands
])
def test_merge_decides_an_enumeration_by_length_before_strength(incumbent, challenger, winner):
    got = tiers.merge({"ref_list": incumbent}, {"ref_list": challenger})
    assert got["ref_list"]["value"] == winner


def test_merge_copies_a_record_key_as_given():
    got = tiers.merge({"_date_label_refused": ["validity_to"]},
                      {"_date_label_refused": ["validity_from"], tiers.READS_KEY: [{"step": "T1"}]})
    assert got["_date_label_refused"] == ["validity_from"]
    assert got[tiers.READS_KEY] == [{"step": "T1"}]


def test_merge_by_strength_off_is_the_old_later_wins_rule():
    """For `repair_ref_list`, which replays a fresh T0 under stored answers."""
    t0 = {"type": _e("DoC", "T0", conf=1.0), "ref_list": _e(["1", "2"], "T0", conf=0.9)}
    stored = {"type": _e("DoC", "T1"), "ref_list": _e([], "T2")}
    got = tiers.merge(t0, stored, by_strength=False)
    assert got["type"]["tier"] == "T1"            # the stored read stays
    assert got["ref_list"]["value"] == ["1", "2"]  # the blank guard still holds


def test_item_ids_brought_in_by_type_to_scope_are_read_once_then_sonnet(t0):
    """T0 typed the file `EC` off its name and so holds `manufacturer` at 1.0;
    the first read finds an IFU, whose scope is `group`, so the item ids become
    pursued after the first read was asked. The second read is their first: one
    empty answer neither settles them as absent nor marks them unread."""
    t0("compliance-doc", {"type": t0ev("EC", 0.6), "coverage_scope": t0ev("manufacturer", 1.0)})
    first = {"type": ("IFU", 0.95), "regulation": ("MDR", 0.95),
             "manufacturer": ("GC EUROPE N.V.", 0.95)}
    llm = ScanLlm({"T2-first": first, "T2-second": {"ref_list": (["003477"], 0.9)},
                   "T2": {}})
    _, fields, used = run(_scan(6), llm)
    assert "ref_list" not in llm.asked("T2-first")
    assert llm.asked("T2-second") == {"ref_list", "basic_udi_di"}
    assert llm.asked("T2") == {"basic_udi_di"}         # ref_list settled at 0.9
    assert fields["coverage_scope"]["value"] == "group"
    assert tiers.PAGES_UNREAD_KEY not in fields
