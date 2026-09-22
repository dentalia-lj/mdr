"""Tier ladder (T0->T1->T2) over the committed real-corpus fixtures.

Complements the unit-level ``test_tiers.py`` (which stubs the LLM and reads one
gitignored corpus file, so it skips in CI). This drives the REAL
``tiers.run_extraction`` across all 22 committed fixtures with ``RecordedLlm`` — no
network, no key — so the ladder's behaviour on the actual document shapes is
verified in CI:

  * doc-class short-circuit (msds/business never reach the LLM);
  * per-field escalation — high-confidence T0 hits survive the ladder unchanged,
    only absent/low fields are asked of T1/T2;
  * scan routing — scanned docs reach the vision tier;
  * evidence completeness on every produced value (invariant 2);
  * recorded T1/T2 answers land on the fields T0 could not read.
"""

from __future__ import annotations

import pytest

from app.extract import pdf, tiers
from app.extract.t0_templates import t0_extract
from tests.fixtures import corpus_manifest as m
from tests.fixtures.evidence import assert_extractor_evidence
from tests.fixtures.llm import RecordedLlm

THRESHOLD = 0.95


def _run(path, **llm_kw):
    doc = pdf.open_doc(path)
    try:
        llm = RecordedLlm.from_dir(**llm_kw)
        doc_class, fields, used = tiers.run_extraction(doc, path, llm=llm, threshold=THRESHOLD)
        return doc_class, fields, used, llm
    finally:
        doc.close()


def _t0(path):
    doc = pdf.open_doc(path)
    try:
        return t0_extract(doc, path)
    finally:
        doc.close()


# --------------------------------------------------------------------------- #
# ladder invariants across the whole fixture matrix
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("entry", m.FIXTURES, ids=[e["name"] for e in m.FIXTURES])
def test_ladder_over_corpus(entry, fixture_pdf):
    path = fixture_pdf(entry)
    doc_class, fields, used, llm = _run(path, fill_missing=True, fill_conf=0.97)

    assert doc_class == entry["doc_class"]

    # non-compliance docs short-circuit before any LLM call
    if doc_class in ("msds", "business-doc"):
        assert used == ["T0"]
        assert llm.calls == []
        assert fields == {}
        return

    # high-confidence T0 hits are preserved through the ladder, never re-parsed
    _, t0_fields = _t0(path)
    for field, ev in t0_fields.items():
        if ev["conf"] >= THRESHOLD:
            assert fields[field]["tier"] == "T0", f"{field} should stay T0"
            assert fields[field]["value"] == ev["value"]

    # scanned docs reach the vision tier
    if entry["is_scan"]:
        assert "T2" in used

    # every produced value carries complete evidence (T0 base, T1/T2 add model_id)
    for field, ev in fields.items():
        assert_extractor_evidence(ev, llm=(ev["tier"] in ("T1", "T2")))

    # every field the ladder asked a tier for landed in the merged result (a value,
    # even if low-confidence — an external/scanned REF list may not fully resolve,
    # which is a real gap surfaced at gate, not a ladder failure). Item-ids are
    # pursued unless manufacturer-scope; validity_from/referenced_docs never chased.
    asked = {f for _tier, miss in llm.calls for f in miss}
    assert asked <= set(fields)


# --------------------------------------------------------------------------- #
# focused: escalation targets only the fields T0 missed
# --------------------------------------------------------------------------- #
def test_emax_escalates_only_missing_fields(fixture_pdf):
    # T0 nails type/regulation/cert/validity_to/ref_list/basic_udi_di on this
    # DoC (S1.5 broadened the hyphen-label UDI regex — T0 no longer misses it);
    # coverage_scope/ref_list still escalate on confidence alone (0.9 < 0.95
    # THRESHOLD) even though T0's value is already correct.
    path = fixture_pdf("IVOCLAR/IPS e.max Ceram.pdf")
    _, fields, _, llm = _run(path)  # recorded-only (no generic fill)

    t1_asked = {f for tier, miss in llm.calls if tier == "T1" for f in miss}
    assert "type" not in t1_asked          # T0 conf 1.0 -> never re-asked
    assert "regulation" not in t1_asked
    assert "basic_udi_di" not in t1_asked  # T0 now reads the hyphen-label variant

    assert fields["basic_udi_di"]["value"] == "76152082ACERA008F6"
    assert fields["basic_udi_di"]["tier"] == "T0"
    assert fields["basic_udi_di"]["conf"] == 1.0


# --------------------------------------------------------------------------- #
# focused: a scanned DoC is read by the vision tier, not the text tier
# --------------------------------------------------------------------------- #
def test_scanned_doc_read_by_vision_tier(fixture_pdf):
    path = fixture_pdf("DENSTPLY/IMP - DOC - ATIS TX SURGICAL INSTRUMENTS - DEC-00101190.pdf")
    _, fields, used, _ = _run(path)  # recorded T2 regulation/validity_to

    assert used == ["T0", "T1", "T2"]
    # T1 (text) read nothing off the scan; the recorded T2 answer supplies regulation
    assert fields["regulation"]["value"] == "MDR"
    assert fields["regulation"]["tier"] == "T2"


# --------------------------------------------------------------------------- #
# focused: MSDS is gated out before the ladder spends an LLM call
# --------------------------------------------------------------------------- #
def test_msds_short_circuits_without_llm(fixture_pdf):
    path = fixture_pdf("GC/pattern-resin-ls-liquid-sds-ca-en.pdf")
    doc_class, fields, used, llm = _run(path, fill_missing=True)
    assert doc_class == "msds"
    assert used == ["T0"]
    assert llm.calls == []
    assert fields == {}
