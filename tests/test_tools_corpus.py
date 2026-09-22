"""tools.corpus — the PDF corpus analyzer (dev-time, analysis only).

Runs against the COMMITTED fixture set (tests/fixtures/corpus/, 22 labeled PDFs),
never the gitignored real corpus, so these run in CI. Assertions are about
aggregation shape and the analyzer's agreement with the pipeline's own
classifiers — never about real-corpus counts, which live in the generated report.
"""

from __future__ import annotations

import pathlib

import pytest

from app.extract import tiers
from tests.fixtures.corpus_manifest import FIXTURES
from tools import corpus as tools_corpus

FIXTURE_ROOT = pathlib.Path(__file__).parent / "fixtures" / "corpus"

SCANNED = {f["name"] for f in FIXTURES if f["is_scan"]}
MSDS = {f["name"] for f in FIXTURES if f["doc_class"] == "msds"}


@pytest.fixture
def rows(corpus_rows):
    """The session-wide scan of the committed corpus (conftest `corpus_rows`).

    Local alias so the 25 cases below read unchanged. Was a module-scoped scan of
    its own, which meant this module, test_tools_claims.py and test_tools_cli.py
    each re-parsed the same 24 PDFs. Read-only: nothing here may mutate it.
    """
    return corpus_rows


def test_scan_yields_one_row_per_pdf(rows):
    assert len(rows) == len(FIXTURES)


def test_rows_are_sorted_by_relpath(rows):
    assert [r["relpath"] for r in rows] == sorted(r["relpath"] for r in rows)


def test_row_has_stable_schema(rows):
    for row in rows:
        assert set(row) == set(tools_corpus.COLUMNS)


def test_every_target_field_has_a_column(rows):
    for field in tiers.TARGET:
        assert f"t0_{field}" in tools_corpus.COLUMNS


def test_brand_is_the_top_level_directory(rows):
    expected = {f["manu"] for f in FIXTURES}
    assert {r["brand"] for r in rows} == expected


def test_scan_detection_agrees_with_the_labeled_manifest(rows):
    """is_scan comes from the pipeline's own app.extract.pdf.is_scan (PyMuPDF,
    <20 chars) — not a re-implementation. The manifest labels are hand-verified."""
    flagged = {pathlib.Path(r["relpath"]).name for r in rows if r["is_scan"]}
    assert flagged == SCANNED


def test_doc_class_agrees_with_the_labeled_manifest(rows):
    by_name = {pathlib.Path(r["relpath"]).name: r["doc_class"] for r in rows}
    for entry in FIXTURES:
        assert by_name[entry["name"]] == entry["doc_class"]


def test_msds_rows_carry_no_t0_field_values(rows):
    """t0_extract short-circuits business-doc/msds with no fields; the analyzer
    must record that rather than inventing empties that look like misses."""
    msds = [r for r in rows if pathlib.Path(r["relpath"]).name in MSDS]
    assert msds
    for row in msds:
        assert all(row[f"t0_{f}"] == "" for f in tiers.TARGET)


def test_a_known_doc_extracts_its_type(rows):
    by_name = {pathlib.Path(r["relpath"]).name: r for r in rows}
    voco = by_name["VOCO_DoC_MDR_Ceramic Bond_2026-1-signed.pdf"]
    assert voco["t0_type"] != ""
    assert float(voco["t0_type"]) > 0


def test_page_count_is_recorded(rows):
    assert all(isinstance(r["pages"], int) and r["pages"] >= 1 for r in rows)


def test_scan_is_deterministic(rows):
    """Deliberately re-scans: a cached value cannot show two runs agree, so this
    is the one case in the module that still pays a full ~12s corpus parse."""
    assert tools_corpus.scan(FIXTURE_ROOT) == rows


def test_unreadable_pdf_becomes_an_error_row_without_aborting(tmp_path):
    """1,307 real files: one corrupt PDF must never kill the run."""
    (tmp_path / "BRAND").mkdir()
    (tmp_path / "BRAND" / "broken.pdf").write_bytes(b"not a pdf at all")
    (tmp_path / "BRAND" / "also_broken.pdf").write_bytes(b"%PDF-1.4 truncated")

    result = tools_corpus.scan(tmp_path)

    assert len(result) == 2
    assert all(r["error"] for r in result)
    assert all(r["doc_class"] == "" for r in result)


def test_uppercase_pdf_extension_is_picked_up(tmp_path):
    """The real corpus has at least one .PDF (VOCO) — case-insensitive glob."""
    (tmp_path / "BRAND").mkdir()
    (tmp_path / "BRAND" / "upper.PDF").write_bytes(b"broken but found")

    assert len(tools_corpus.scan(tmp_path)) == 1


def test_non_pdf_files_are_counted_separately(tmp_path):
    """The corpus carries 9 support spreadsheets; they are inventory, not targets."""
    (tmp_path / "BRAND").mkdir()
    (tmp_path / "BRAND" / "tracker.xlsx").write_bytes(b"x")
    (tmp_path / "BRAND" / "doc.pdf").write_bytes(b"broken")

    assert len(tools_corpus.scan(tmp_path)) == 1
    assert tools_corpus.support_files(tmp_path) == ["BRAND/tracker.xlsx"]


# --------------------------------------------------------------------------- #
# aggregate
# --------------------------------------------------------------------------- #
@pytest.fixture
def summary(corpus_summary):
    """Session-wide `aggregate()` of `rows` (conftest `corpus_summary`)."""
    return corpus_summary


def test_aggregate_total_matches_row_count(summary, rows):
    assert summary["total_pdfs"] == len(rows)


def test_aggregate_counts_scanned_via_pipeline_is_scan(summary):
    assert summary["scanned"] == len(SCANNED)


def test_aggregate_doc_class_distribution_sums_to_total(summary):
    assert sum(summary["doc_class"].values()) == summary["total_pdfs"]


def test_aggregate_per_brand_pdf_counts_sum_to_total(summary):
    assert sum(b["pdfs"] for b in summary["by_brand"].values()) == summary["total_pdfs"]


def test_t0_yield_denominator_excludes_business_and_msds(summary):
    """business-doc/msds never reach field extraction, so counting them as T0
    misses would understate the yield. Denominator = extraction-eligible."""
    assert summary["extraction_eligible"] == len(FIXTURES) - len(MSDS)


def test_t0_yield_is_reported_for_every_target_field(summary):
    assert set(summary["t0_yield"]) == set(tiers.TARGET)
    for field, stat in summary["t0_yield"].items():
        assert 0 <= stat["n"] <= summary["extraction_eligible"]
        assert 0.0 <= stat["pct"] <= 100.0


def test_t0_fully_missed_counts_eligible_docs_with_no_fields(summary):
    """The T1/T2 workload the sweep pays for."""
    assert 0 <= summary["t0_fully_missed"] <= summary["extraction_eligible"]


def test_aggregate_is_deterministic(rows):
    assert tools_corpus.aggregate(rows) == tools_corpus.aggregate(rows)


def test_aggregate_reports_t0_yield_per_brand(summary):
    """Which manufacturers are cheap vs expensive to extract — the input to
    S1.7's 'live discovery on top ~20 manufacturers' pilot choice."""
    for brand, stat in summary["by_brand"].items():
        assert set(stat["t0_yield"]) == set(tiers.TARGET)
        assert stat["eligible"] <= stat["pdfs"]
        assert all(0.0 <= pct <= 100.0 for pct in stat["t0_yield"].values())


def test_brand_eligible_counts_sum_to_the_overall_eligible(summary):
    total = sum(b["eligible"] for b in summary["by_brand"].values())
    assert total == summary["extraction_eligible"]


# --------------------------------------------------------------------------- #
# render
# --------------------------------------------------------------------------- #
def test_render_returns_ordered_heading_body_sections(summary):
    sections = tools_corpus.render(summary, support=[])
    assert [h for h, _ in sections] == [
        "Method",
        "Inventory",
        "T0 field yield",
        "T0 yield by brand",
        "Layout templates",
        "REF-list recovery",
        "Support files",
    ]


def test_render_method_section_names_the_scan_test_it_used(summary):
    """The 2026-07-15 sweep used pdftotext/<100 chars; the pipeline uses
    is_scan/<20. A report that does not say which it ran is not checkable."""
    body = dict(tools_corpus.render(summary, support=[]))["Method"]
    assert "is_scan" in body


def test_render_inventory_lists_every_brand(summary):
    body = dict(tools_corpus.render(summary, support=[]))["Inventory"]
    for brand in summary["by_brand"]:
        assert brand in body


def test_render_t0_yield_covers_every_target_field(summary):
    body = dict(tools_corpus.render(summary, support=[]))["T0 field yield"]
    for field in tiers.TARGET:
        assert field in body


def test_render_lists_support_files(summary):
    body = dict(tools_corpus.render(summary, support=["GC/Devices.xlsx"]))["Support files"]
    assert "GC/Devices.xlsx" in body


def test_render_is_deterministic(summary):
    assert tools_corpus.render(summary, support=[]) == tools_corpus.render(summary, support=[])


# --------------------------------------------------------------------------- #
# playbook steering (2026-08-31)
#
# `scan()` called `t0_extract` with no playbook, so every corpus report measured
# extraction with steering switched OFF. That was invisible while every rule
# lived in `t0_templates`, and becomes wrong the moment one is authored into a
# playbook: the report would show no change and could not distinguish a working
# rule from a broken one. It is the verification harness for all per-manufacturer
# authoring, so it has to be able to see one.
# --------------------------------------------------------------------------- #
def _doc_with_authored_label(tmp_path):
    """A PDF whose issue date sits behind a label no GLOBAL list carries."""
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "EC Declaration of Conformity")
    page.insert_text((72, 130), "Acme Dental GmbH")
    page.insert_text((72, 160), "Freigabedatum: 2024-03-15")
    d = tmp_path / "ACME"
    d.mkdir(parents=True, exist_ok=True)
    doc.save(str(d / "doc.pdf"))
    return tmp_path


def test_scan_without_playbooks_is_the_unsteered_baseline(tmp_path):
    root = _doc_with_authored_label(tmp_path)
    row = tools_corpus.scan(root)[0]
    assert row["pb_slug"] == ""
    assert row["t0_validity_from"] == ""


def test_scan_with_playbooks_reads_an_authored_label(tmp_path):
    """The whole point: an authored `date_labels` must change the report."""
    from app import playbooks as pb_mod

    root = _doc_with_authored_label(tmp_path)
    pb = pb_mod._parse("acme", {
        "manufacturer": "Acme Dental GmbH",
        "date_labels": {"from": ["Freigabedatum"]},
    })
    row = tools_corpus.scan(root, playbooks=(pb,))[0]
    assert row["pb_slug"] == "acme", "the playbook did not resolve"
    assert row["t0_validity_from"] != "", "an authored label changed nothing"


def test_pb_slug_is_empty_when_no_playbook_claims_the_document(tmp_path):
    """A resolution MISS must be visible. Otherwise it reads identically to
    'the rule ran and changed nothing', which is the failure this column
    exists to tell apart."""
    from app import playbooks as pb_mod

    root = _doc_with_authored_label(tmp_path)
    other = pb_mod._parse("nothing-like-acme", {"manufacturer": "Zzz Dental AB"})
    row = tools_corpus.scan(root, playbooks=(other,))[0]
    assert row["pb_slug"] == ""


def test_pb_slug_is_a_column(tmp_path):
    assert "pb_slug" in tools_corpus.COLUMNS
