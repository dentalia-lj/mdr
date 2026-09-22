"""tools.claims — checks the hand-written corpus-analysis doc's hard numbers
against what the analyzer measures. Pure comparison logic over synthetic
measured/documented pairs; the real DOCUMENTED set is asserted separately.
"""

from __future__ import annotations

from tools import claims


# Synthetic claim list injected into check() so the LOGIC is tested independent
# of the real DOCUMENTED contents.
def _measure_total(summary, support):
    return summary["total_pdfs"]


SYNTH = [
    {"id": "exact-hit", "source": "§x", "documented": 100, "tolerance": 0,
     "measure": lambda s, sup: 100, "note": ""},
    {"id": "off-by-one", "source": "§x", "documented": 300, "tolerance": 0,
     "measure": lambda s, sup: 299, "note": "method differs"},
    {"id": "within-tolerance", "source": "§x", "documented": 40.0, "tolerance": 10.0,
     "measure": lambda s, sup: 48.2, "note": ""},
    {"id": "beyond-tolerance", "source": "§x", "documented": 75.0, "tolerance": 10.0,
     "measure": lambda s, sup: 97.8, "note": ""},
    {"id": "not-measurable", "source": "§x", "documented": 19091, "tolerance": 0,
     "measure": lambda s, sup: None, "note": "needs the export"},
]

SUMMARY = {"total_pdfs": 1307}


def _rows():
    return claims.check(SUMMARY, support=[], documented=SYNTH)


def test_one_row_per_claim():
    assert len(_rows()) == len(SYNTH)


def test_rows_have_stable_schema():
    for row in _rows():
        assert set(row) == {"id", "source", "documented", "measured", "verdict", "delta", "note"}


def test_exact_match_is_match_with_zero_delta():
    row = next(r for r in _rows() if r["id"] == "exact-hit")
    assert row["verdict"] == "match"
    assert row["delta"] == 0


def test_off_by_one_at_zero_tolerance_is_mismatch_with_delta():
    row = next(r for r in _rows() if r["id"] == "off-by-one")
    assert row["verdict"] == "mismatch"
    assert row["delta"] == -1


def test_within_tolerance_is_match():
    row = next(r for r in _rows() if r["id"] == "within-tolerance")
    assert row["verdict"] == "match"


def test_beyond_tolerance_is_mismatch():
    row = next(r for r in _rows() if r["id"] == "beyond-tolerance")
    assert row["verdict"] == "mismatch"
    assert round(row["delta"], 1) == 22.8


def test_unmeasurable_claim_is_not_measured():
    row = next(r for r in _rows() if r["id"] == "not-measurable")
    assert row["verdict"] == "not-measured"
    assert row["measured"] is None
    assert row["delta"] is None


def test_note_is_carried_through():
    row = next(r for r in _rows() if r["id"] == "off-by-one")
    assert row["note"] == "method differs"


def test_measure_receives_summary_and_support():
    seen = {}
    claim = [{"id": "probe", "source": "§x", "documented": 1, "tolerance": 0,
              "measure": lambda s, sup: seen.update(s=s, sup=sup) or 1, "note": ""}]
    claims.check({"total_pdfs": 42}, support=["a.xlsx"], documented=claim)
    assert seen["s"]["total_pdfs"] == 42
    assert seen["sup"] == ["a.xlsx"]


def test_check_is_deterministic():
    assert claims.check(SUMMARY, support=[], documented=SYNTH) == _rows()


# --------------------------------------------------------------------------- #
# the real documented set
# --------------------------------------------------------------------------- #
def test_documented_claims_have_required_fields():
    for c in claims.DOCUMENTED:
        assert {"id", "source", "documented", "tolerance", "measure"} <= set(c)


def test_documented_covers_the_corpus_headline_numbers():
    docs = {c["id"]: c["documented"] for c in claims.DOCUMENTED}
    assert docs["total-pdfs"] == 1307
    assert docs["scanned"] == 300
    assert docs["neodent-business"] == 28
    assert docs["non-pdf"] == 9


def test_scanned_claim_records_the_methodology_divergence():
    scanned = next(c for c in claims.DOCUMENTED if c["id"] == "scanned")
    note = scanned.get("note", "").lower()
    assert "pdftotext" in note and "is_scan" in note


def test_catalogue_claims_are_not_measurable_from_the_pdf_corpus():
    """export rows / distinct codes / CONFIRMED codes come from the BC export
    (Chunk 3/4), so against a PDF-corpus summary they must read not-measured."""
    empty_summary = {"total_pdfs": 0, "scanned": 0, "by_brand": {}, "t0_yield": {}}
    rows = {r["id"]: r for r in claims.check(empty_summary, support=[])}
    assert rows["export-rows"]["verdict"] == "not-measured"
    assert rows["distinct-codes"]["verdict"] == "not-measured"


def test_check_runs_against_a_real_corpus_summary_shape(corpus_summary, corpus_support):
    """Smoke: a full aggregate() summary flows through check() without KeyError.

    Takes the session-wide scan from conftest rather than re-parsing the 24
    fixture PDFs itself; it asserts on the summary's SHAPE, so where the scan was
    produced is immaterial to what is being checked here.
    """
    out = claims.check(corpus_summary, support=corpus_support)
    assert len(out) == len(claims.DOCUMENTED)
    assert all(r["verdict"] in {"match", "mismatch", "not-measured"} for r in out)
