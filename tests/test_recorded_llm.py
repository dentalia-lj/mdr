"""Unit tests for RecordedLlm — the recorded-response LLM stub the corpus ladder
tests depend on. Guards the interface (extract(tier, doc, filename, missing)),
content-hash keying, call recording, evidence stamping, and the fill/no-fill modes.
"""

from __future__ import annotations

from tests.fixtures.evidence import assert_extractor_evidence
from tests.fixtures.llm import RecordedLlm

EMAX = "IVOCLAR/IPS e.max Ceram.pdf"


def test_returns_recorded_value_keyed_by_file_hash(fixture_pdf):
    llm = RecordedLlm.from_dir()
    out = llm.extract("T1", None, fixture_pdf(EMAX), ["basic_udi_di"])
    ev = out["basic_udi_di"]
    assert ev["value"] == "76152082ACERA008F6"
    assert_extractor_evidence(ev, expected_tier="T1", llm=True)
    assert ev["model_id"] == "haiku-4.5"


def test_returns_only_requested_missing_fields(fixture_pdf):
    llm = RecordedLlm.from_dir()
    # e.max T1 only recorded basic_udi_di; asking for an unrecorded field yields it absent
    out = llm.extract("T1", None, fixture_pdf(EMAX), ["cert_number"])
    assert out == {}


def test_records_calls_for_escalation_assertions(fixture_pdf):
    llm = RecordedLlm.from_dir()
    llm.extract("T1", None, fixture_pdf(EMAX), ["basic_udi_di", "validity_to"])
    assert llm.calls == [("T1", ("basic_udi_di", "validity_to"))]


def test_wrong_tier_has_no_recorded_value(fixture_pdf):
    llm = RecordedLlm.from_dir()  # e.max recorded only under T1
    assert llm.extract("T2", None, fixture_pdf(EMAX), ["basic_udi_di"]) == {}


def test_fill_missing_produces_wellformed_evidence(fixture_pdf):
    llm = RecordedLlm.from_dir(fill_missing=True, fill_conf=0.97)
    out = llm.extract("T1", None, fixture_pdf(EMAX), ["cert_number"])
    ev = out["cert_number"]
    assert_extractor_evidence(ev, expected_tier="T1", llm=True)
    assert ev["conf"] == 0.97


# --- ladder labels (plan 2026-10-12): the label keys the recording, the read's
# kind stamps the evidence tier, exactly as AnthropicLlm does. -----------------

SCAN = "DENSTPLY/IMP - DOC - ATIS TX SURGICAL INSTRUMENTS - DEC-00101190.pdf"


def test_a_ladder_label_is_its_own_key_and_stamps_the_evidence_tier(fixture_pdf):
    from tests.fixtures.llm import hash_file

    path = fixture_pdf(EMAX)
    llm = RecordedLlm()
    llm.register(hash_file(path), "T2-first",
                 {"type": {"value": "DoC", "conf": 0.9, "verbatim": "x", "page": 1}},
                 model_id="haiku-5.5")

    ev = llm.extract("T2-first", None, path, ["type"])["type"]

    assert_extractor_evidence(ev, expected_tier="T2", llm=True)
    assert ev["model_id"] == "haiku-5.5"
    assert llm.extract("T2", None, path, ["type"]) == {}   # a different key


def test_a_second_text_read_reads_nothing_off_a_scan(fixture_pdf):
    """Same rule as T1: the text kind sees no text on a scanned page."""
    from app.extract import pdf as pdfutil

    path = fixture_pdf(SCAN)
    llm = RecordedLlm.from_dir(fill_missing=True)
    assert llm.extract("T1-second", pdfutil.open_doc(path), path, ["regulation"]) == {}
