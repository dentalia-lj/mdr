"""T0 deterministic extraction over the committed real-corpus fixture subset.

Table-driven from ``tests/fixtures/corpus_manifest.py`` (22 representative PDFs,
tracked in git so this runs in CI, not just on Denis's machine). Complements the
pure-function unit cases in ``test_t0.py``: those assert the extractors on hand-
written strings; these assert the whole orchestrator on real files.

Each fixture asserts:
  * the G1 doc-class gate (compliance-doc / msds) and is_scan routing
  * every scalar field in ``entry['t0']`` — exact value + tier=='T0' + full evidence
  * REF-list floor via ``t0_ref_min`` (codes kept verbatim — G5)

Deterministic misses that T0 *ought* to catch (KNOWN_T0_GAPS) are asserted as
xfail: they surface now and flip to a pass the moment the regex/parser is fixed,
which then prompts a manifest update.
"""

from __future__ import annotations

import pytest

from app.extract import pdf
from app.extract import t0_templates as t0
from tests.fixtures import corpus_manifest as m

PNG_MAGIC = bytes.fromhex("89504e470d0a1a0a")
EVIDENCE_KEYS = {"value", "conf", "tier", "verbatim", "page"}


def _extract(path: str):
    doc = pdf.open_doc(path)
    try:
        return t0.t0_extract(doc, filename=path)
    finally:
        doc.close()


def _ids(entries):
    return [e["name"] for e in entries]


# --------------------------------------------------------------------------- #
# doc-class gate (G1) + scan routing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("entry", m.FIXTURES, ids=_ids(m.FIXTURES))
def test_doc_class(entry, fixture_pdf):
    doc_class, _ = _extract(fixture_pdf(entry))
    assert doc_class == entry["doc_class"]


@pytest.mark.parametrize("entry", m.FIXTURES, ids=_ids(m.FIXTURES))
def test_is_scan_routing(entry, fixture_pdf):
    doc = pdf.open_doc(fixture_pdf(entry))
    try:
        assert pdf.is_scan(doc) is entry["is_scan"]
    finally:
        doc.close()


# --------------------------------------------------------------------------- #
# per-field deterministic extraction (exact value + T0 tier + full evidence)
# --------------------------------------------------------------------------- #
_FIELD_CASES = [
    (e, field, value)
    for e in m.FIXTURES
    for field, value in e["t0"].items()
]


@pytest.mark.parametrize(
    "entry,field,value",
    _FIELD_CASES,
    ids=[f"{e['name']}::{field}" for e, field, value in _FIELD_CASES],
)
def test_t0_field(entry, field, value, fixture_pdf):
    _, fields = _extract(fixture_pdf(entry))
    assert field in fields, f"T0 did not extract {field!r}"
    ev = fields[field]
    assert ev["value"] == value
    assert ev["tier"] == "T0"
    assert ev["conf"] >= 0.5
    assert EVIDENCE_KEYS <= set(ev), f"incomplete evidence on {field}: {ev}"


# --------------------------------------------------------------------------- #
# REF-list floor — codes verbatim, no coercion (G5)
# --------------------------------------------------------------------------- #
_REF_CASES = [(e, e["t0_ref_min"]) for e in m.FIXTURES if e.get("t0_ref_min")]


@pytest.mark.parametrize(
    "entry,ref_min", _REF_CASES, ids=[e["name"] for e, _ in _REF_CASES]
)
def test_t0_ref_list_floor(entry, ref_min, fixture_pdf):
    _, fields = _extract(fixture_pdf(entry))
    assert "ref_list" in fields, "expected a T0 REF list"
    ev = fields["ref_list"]
    assert ev["tier"] == "T0"
    assert len(ev["value"]) >= ref_min
    # verbatim: strings preserved as read (no int coercion / zero-stripping)
    assert all(isinstance(code, str) for code in ev["value"])


# --------------------------------------------------------------------------- #
# known deterministic gaps — xfail now, xpass when the parser/regex is fixed
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "gap", m.KNOWN_T0_GAPS, ids=[f"{g['name']}::{g['field']}" for g in m.KNOWN_T0_GAPS]
)
def test_t0_known_gap(gap, fixture_pdf):
    entry = m.by_name(gap["name"])
    _, fields = _extract(fixture_pdf(entry))
    if gap["field"] not in fields:
        pytest.xfail(f"KNOWN T0 GAP: {gap['reason']}")
    # reached only once the gap is closed -> passes, prompting a manifest update
    assert fields[gap["field"]]["tier"] == "T0"


# --------------------------------------------------------------------------- #
# focused negative / routing regressions
# --------------------------------------------------------------------------- #
def test_regulation_abstains_on_non_md_declaration(fixture_pdf):
    """Battery DoC misfiled under an 'MDR - Declaration' folder must NOT be forced
    to regulation=MDR. T0 sees only the (flattened) basename, cites 2023/1542 in
    the body -> abstains. Guards the folder-leak fix."""
    entry = m.by_name("VOCO_DoC_(EU)2023-1542_Celalux 3.pdf")
    _, fields = _extract(fixture_pdf(entry))
    assert "regulation" not in fields


def test_msds_is_skipped_without_extraction(fixture_pdf):
    """A Safety Data Sheet must be gated out (G1) before any field extraction."""
    entry = m.by_name("pattern-resin-ls-liquid-sds-ca-en.pdf")
    doc_class, fields = _extract(fixture_pdf(entry))
    assert doc_class == "msds"
    assert fields == {}


def test_scanned_cert_flagged_and_rasterizable(fixture_pdf):
    """Scanned EC cert: no extractable text -> is_scan True (routes to T2), and the
    page rasterizes to PNG for the vision tier."""
    entry = m.by_name("MDR Certificate IV AG 2017_745.pdf")
    doc = pdf.open_doc(fixture_pdf(entry))
    try:
        assert pdf.is_scan(doc) is True
        png = pdf.rasterize_page(doc, 0, dpi=100)
        assert png.startswith(PNG_MAGIC)
    finally:
        doc.close()


def test_iso_cert_has_no_ref_list(fixture_pdf):
    """QMS/ISO certs cover the manufacturer, never a product REF list (G2)."""
    entry = m.by_name("Ivoclar ISO certifikat do 30_10_2027.pdf")
    _, fields = _extract(fixture_pdf(entry))
    assert fields["type"]["value"] == "ISO"
    assert fields["coverage_scope"]["value"] == "manufacturer"
    assert "ref_list" not in fields or not fields["ref_list"]["value"]
