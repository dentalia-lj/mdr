"""tools.__main__ — argparse dispatch for the analyzers.

`main(argv)` returns an exit code so these run in-process, no subprocess needed.

The `corpus` and `claims` subcommands each re-parse all 24 committed fixture
PDFs, ~12s a call. The runs whose output is merely inspected share one cached
invocation apiece (`corpus_cli_run` / `claims_cli_run` in conftest.py); only
the reproducibility test still pays for a second, genuinely independent run.
"""

from __future__ import annotations

import json
import pathlib

from tools.__main__ import main

FIXTURE_ROOT = pathlib.Path(__file__).parent / "fixtures" / "corpus"


def _run(out_dir, *extra):
    return main(["corpus", "--root", str(FIXTURE_ROOT), "--out", str(out_dir),
                 "--date", "2026-07-24", *extra])


def _outputs(run):
    """The three artefacts a `corpus` run writes, from a cached run's dict."""
    stem = f"corpus-{run['date']}"
    return [run["out"] / f"{stem}.{ext}" for ext in ("csv", "json", "md")]


def test_corpus_run_succeeds(corpus_cli_run):
    assert corpus_cli_run["code"] == 0


def test_corpus_run_writes_dataset_summary_and_report(corpus_cli_run):
    assert all(path.exists() for path in _outputs(corpus_cli_run))


def test_dataset_has_one_row_per_fixture_pdf(corpus_cli_run):
    csv_path, _, _ = _outputs(corpus_cli_run)
    assert len(csv_path.read_text().splitlines()) == 25   # 24 fixtures + header


def test_summary_json_records_the_measured_totals(corpus_cli_run):
    _, json_path, _ = _outputs(corpus_cli_run)
    summary = json.loads(json_path.read_text())
    assert summary["total_pdfs"] == 24
    assert summary["extraction_eligible"] == 23


def test_report_is_readable_markdown(corpus_cli_run):
    _, _, md_path = _outputs(corpus_cli_run)
    md = md_path.read_text()
    assert md.startswith("# ")
    assert "## T0 field yield" in md


def test_rerunning_produces_byte_identical_output(corpus_cli_run, tmp_path):
    """The reproducibility property this tooling exists to create.

    Compares the session's cached run against a second, genuinely fresh one --
    still two separate `main()` invocations writing to two directories, which is
    the property under test. Only the first of the two is shared, so this keeps
    costing one full corpus scan rather than two.
    """
    fresh = tmp_path / "fresh"
    assert _run(fresh) == 0
    for cached in _outputs(corpus_cli_run):
        assert cached.read_bytes() == (fresh / cached.name).read_bytes()


def test_missing_root_fails_cleanly_without_a_traceback(tmp_path, capsys):
    code = main(["corpus", "--root", str(tmp_path / "nope"), "--out", str(tmp_path)])
    assert code == 2
    assert "not a directory" in capsys.readouterr().err.lower()


def test_unknown_subcommand_is_rejected(tmp_path):
    import pytest
    with pytest.raises(SystemExit):
        main(["bogus"])


# --------------------------------------------------------------------------- #
# claims subcommand
# --------------------------------------------------------------------------- #
def _claims_outputs(run):
    """The two artefacts a `claims` run writes, from a cached run's dict."""
    stem = f"claims-{run['date']}"
    return [run["out"] / f"{stem}.{ext}" for ext in ("csv", "md")]


def test_claims_run_succeeds(claims_cli_run):
    assert claims_cli_run["code"] == 0


def test_claims_writes_dataset_and_report(claims_cli_run):
    assert all(path.exists() for path in _claims_outputs(claims_cli_run))


def test_claims_csv_has_one_row_per_documented_claim(claims_cli_run):
    from tools import claims
    csv_path, _ = _claims_outputs(claims_cli_run)
    lines = csv_path.read_text().splitlines()
    assert len(lines) == len(claims.DOCUMENTED) + 1     # + header


def test_claims_report_is_readable_markdown(claims_cli_run):
    _, md_path = _claims_outputs(claims_cli_run)
    md = md_path.read_text()
    assert md.startswith("# ")
    assert "not-measured" in md


def test_claims_missing_root_fails_cleanly(tmp_path, capsys):
    code = main(["claims", "--root", str(tmp_path / "nope"), "--out", str(tmp_path)])
    assert code == 2
    assert "not a directory" in capsys.readouterr().err.lower()


# --------------------------------------------------------------------------- #
# catalogue subcommand
# --------------------------------------------------------------------------- #
def _synth_export(tmp_path):
    from app.adapters.source import LJ_CSV_PROFILE as H
    import csv
    path = tmp_path / "export.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([H["item_ref"], H["name"], H["name_fallback"],
                    H["manufacturer_raw"], H["mfr_ref"], H["md_class"]])
        w.writerow(["1001", "Item A", "", "001", "V-100", "RAZRED IIA"])
        w.writerow(["2003", "Item C", "", "008", "", "NI MP"])
    return str(path)


def test_catalogue_run_succeeds(tmp_path):
    export = _synth_export(tmp_path)
    assert main(["catalogue", "--export", export, "--out", str(tmp_path),
                 "--date", "2026-07-24"]) == 0


def test_catalogue_writes_dataset_and_report(tmp_path):
    export = _synth_export(tmp_path)
    main(["catalogue", "--export", export, "--out", str(tmp_path), "--date", "2026-07-24"])
    assert (tmp_path / "catalogue-2026-07-24.csv").exists()
    assert (tmp_path / "catalogue-2026-07-24.md").exists()


def test_catalogue_dataset_has_one_row_per_item(tmp_path):
    export = _synth_export(tmp_path)
    main(["catalogue", "--export", export, "--out", str(tmp_path), "--date", "2026-07-24"])
    lines = (tmp_path / "catalogue-2026-07-24.csv").read_text().splitlines()
    assert len(lines) == 3      # header + 2 items


def test_catalogue_missing_export_fails_cleanly(tmp_path, capsys):
    code = main(["catalogue", "--export", str(tmp_path / "nope.csv"), "--out", str(tmp_path)])
    assert code == 2
    assert "not a file" in capsys.readouterr().err.lower()


# --------------------------------------------------------------------------- #
# crossref subcommand
# --------------------------------------------------------------------------- #
def test_crossref_run_succeeds(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()                       # no trackers -> all noted absent, still 0
    export = _synth_export(tmp_path)
    assert main(["crossref", "--root", str(corpus), "--export", export,
                 "--out", str(tmp_path), "--date", "2026-07-24"]) == 0


def test_crossref_writes_dataset_and_report(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    main(["crossref", "--root", str(corpus), "--export", _synth_export(tmp_path),
          "--out", str(tmp_path), "--date", "2026-07-24"])
    assert (tmp_path / "crossref-2026-07-24.csv").exists()
    assert (tmp_path / "crossref-2026-07-24.md").exists()


def test_crossref_missing_export_fails_cleanly(tmp_path, capsys):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    code = main(["crossref", "--root", str(corpus), "--export", str(tmp_path / "nope.csv"),
                 "--out", str(tmp_path)])
    assert code == 2
    assert "not a file" in capsys.readouterr().err.lower()


def test_crossref_missing_root_fails_cleanly(tmp_path, capsys):
    code = main(["crossref", "--root", str(tmp_path / "nope"), "--export", _synth_export(tmp_path),
                 "--out", str(tmp_path)])
    assert code == 2
    assert "not a directory" in capsys.readouterr().err.lower()
