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
