"""Reading a manufacturer's own article -> document index.

Pure file -> rows. The .docx half is tested on a crafted XML string rather than
a committed binary: the real file is a hand-maintained working document and a
fixture of it would rot without anyone noticing.
"""

from __future__ import annotations

import zipfile

import pytest

from app import coverage_map as cm

COLS = {"article": "Article no.", "reference": "Reference no.",
        "key": "Product family (Where to find DoC)"}


def _xlsx(path, rows):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Medical Devices Only - find DoC"
    for r in rows:
        ws.append(r)
    wb.save(str(path))


def test_xlsx_rows_are_read_by_declared_column_name(tmp_path):
    p = tmp_path / "index.xlsx"
    _xlsx(p, [["Article no.", "Reference no.", "UMNDS", "Product family (Where to find DoC)"],
              ["000085K3", "H1.314.006", "16-668", 532854],
              ["000086K3", "H1.314.007", "16-668", 532854]])

    rows = cm.rows_from_xlsx(p, "Medical Devices Only - find DoC", COLS)

    assert rows == [cm.Row("000085K3", "H1.314.006", "532854"),
                    cm.Row("000086K3", "H1.314.007", "532854")]


def test_a_reordered_or_renamed_column_is_a_hard_error(tmp_path):
    """The failure this guard exists for: a hand-edited index whose column
    moved would re-point every article at once and report success. Columns are
    found BY NAME and their absence stops the job."""
    p = tmp_path / "index.xlsx"
    _xlsx(p, [["Article", "Reference no.", "Product family (Where to find DoC)"],
              ["000085K3", "H1.314.006", 532854]])

    with pytest.raises(cm.CoverageMapError, match="Article no."):
        cm.rows_from_xlsx(p, "Medical Devices Only - find DoC", COLS)


def _force_decimal_literal(path, old: str, new: str):
    """Rewrite a numeric cell's raw <v> literal in the saved sheet XML, e.g.
    '532854' -> '532854.0'. openpyxl's own writer normalises a whole-number
    Python float back to an integer literal on save, so writing 532854.0
    through the normal ws.append() API round-trips as int, not float -- this
    reproduces the on-disk shape a hand-authored Excel file can carry
    instead."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        data = {n: z.read(n) for n in names}
    sheet_name = next(n for n in names if n.startswith("xl/worksheets/sheet"))
    xml = data[sheet_name].decode("utf-8")
    patched = xml.replace(f"<v>{old}</v>", f"<v>{new}</v>", 1)
    assert patched != xml, f"literal {old!r} not found in {sheet_name}"
    data[sheet_name] = patched.encode("utf-8")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for n, content in data.items():
            z.writestr(n, content)


def test_the_key_is_read_as_text_not_a_float(tmp_path):
    """openpyxl gives an integer cell back as int, but a real Excel-authored
    file can carry a whole number as a decimal XML literal ('532854.0'),
    which openpyxl parses back as a Python float; pandas does the same for
    a plain int. Either way '532854.0' does not match a filename. Forced
    here by patching the saved XML directly -- ws.append(532854.0) would
    just round-trip as int and not exercise this at all."""
    p = tmp_path / "index.xlsx"
    _xlsx(p, [["Article no.", "Reference no.", "Product family (Where to find DoC)"],
              ["000085K3", "H1.314.006", 532854]])
    _force_decimal_literal(p, "532854", "532854.0")

    assert cm.rows_from_xlsx(p, "Medical Devices Only - find DoC", COLS)[0].key == "532854"


def test_rows_with_no_article_are_skipped(tmp_path):
    p = tmp_path / "index.xlsx"
    _xlsx(p, [["Article no.", "Reference no.", "Product family (Where to find DoC)"],
              ["000085K3", "H1.314.006", 532854],
              [None, None, None],
              ["", "", ""]])

    assert len(cm.rows_from_xlsx(p, "Medical Devices Only - find DoC", COLS)) == 1


DOCX_COLS = {"article": "Article no (Komet)", "reference": "Reference no.",
             "key": "Where to find DoC"}

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _cell(text):
    return f"<w:tc><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:tc>"


def _table_xml(rows):
    """One <w:tbl> block, unwrapped -- so a test can place more than one of
    these inside a single <w:body>, or nest one inside a cell."""
    return "<w:tbl>" + "".join(
        "<w:tr>" + "".join(_cell(c) for c in row) + "</w:tr>" for row in rows
    ) + "</w:tbl>"


def _docx_xml(*table_rows):
    """A minimal word/document.xml carrying one <w:tbl> per argument. Declares
    the real w: namespace URI -- the actual namespace ElementTree resolves the
    tags against -- exactly as a genuine Word-authored document.xml does."""
    body = "".join(_table_xml(rows) for rows in table_rows)
    return f'<w:document xmlns:w="{_W_NS}"><w:body>{body}</w:body></w:document>'


def _write_minimal_docx(path, rows):
    """A minimal but real .docx: a zip containing just word/document.xml,
    which is all rows_from_docx reads."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", _docx_xml(rows))


def test_docx_rows_are_read_from_the_header_row_onward():
    xml = _docx_xml([["Article no (Komet)", "Reference no.", "Where to find DoC"],
                     ["043690K0", "9978.000.000", "533221"],
                     ["044445K1", "9996.000.020", "533208"]])

    rows = cm.rows_from_docx_xml(xml, DOCX_COLS)

    assert rows == [cm.Row("043690K0", "9978.000.000", "533221"),
                    cm.Row("044445K1", "9996.000.020", "533208")]


def test_a_docx_without_the_declared_header_is_a_hard_error():
    xml = _docx_xml([["Article", "Reference", "DoC"], ["043690K0", "9978.000.000", "533221"]])

    with pytest.raises(cm.CoverageMapError, match="Article no \\(Komet\\)"):
        cm.rows_from_docx_xml(xml, DOCX_COLS)


def test_docx_text_split_across_runs_is_joined():
    """Word splits a cell's text across <w:r> runs at every formatting change,
    so '533221' can arrive as three runs. Reading only the first run silently
    truncates the document key."""
    split = ("<w:tc><w:p><w:r><w:t>5332</w:t></w:r>"
             "<w:r><w:t>21</w:t></w:r></w:p></w:tc>")
    body = ("<w:tbl><w:tr>" + "".join(_cell(c) for c in
                              ["Article no (Komet)", "Reference no.", "Where to find DoC"]) + "</w:tr>"
            "<w:tr>" + _cell("043690K0") + _cell("9978.000.000") + split + "</w:tr>"
            "</w:tbl>")
    xml = f'<w:document xmlns:w="{_W_NS}"><w:body>{body}</w:body></w:document>'

    assert cm.rows_from_docx_xml(xml, DOCX_COLS)[0].key == "533221"


def test_docx_rows_do_not_leak_across_tables():
    """A second, unrelated table elsewhere in the document must not
    contribute rows to the target table. Each table's rows are read from
    that table alone, not from 'every row anywhere in the document after
    the header was found'. The legend table below has the same column count
    as the target table on purpose: a narrower table would get filtered out
    by the cell-count guard regardless of table scoping, and would not
    actually exercise this."""
    target = [["Article no (Komet)", "Reference no.", "Where to find DoC"],
              ["043690K0", "9978.000.000", "533221"]]
    legend = [["Legend", "Meaning", "Extra"], ["X", "Excluded", "Y"]]
    xml = _docx_xml(target, legend)

    rows = cm.rows_from_docx_xml(xml, DOCX_COLS)

    assert rows == [cm.Row("043690K0", "9978.000.000", "533221")]


def test_docx_nested_table_does_not_corrupt_or_leak():
    """A <w:tbl> nested inside a data cell -- an edge case, not seen in the
    real corpus, but these are hand-maintained working documents -- must not
    bleed its own cell text into the cell that contains it, and its own rows
    must never be read as rows of the outer table."""
    nested = _table_xml([["nested-h1", "nested-h2", "nested-h3"],
                          ["nested-r1", "nested-r2", "nested-r3"]])
    key_cell = "<w:tc><w:p><w:r><w:t>533221</w:t></w:r></w:p>" + nested + "</w:tc>"
    header_row = "<w:tr>" + "".join(_cell(c) for c in
                                     ["Article no (Komet)", "Reference no.", "Where to find DoC"]) + "</w:tr>"
    data_row = "<w:tr>" + _cell("043690K0") + _cell("9978.000.000") + key_cell + "</w:tr>"
    body = f"<w:tbl>{header_row}{data_row}</w:tbl>"
    xml = f'<w:document xmlns:w="{_W_NS}"><w:body>{body}</w:body></w:document>'

    rows = cm.rows_from_docx_xml(xml, DOCX_COLS)

    assert rows == [cm.Row("043690K0", "9978.000.000", "533221")]


def test_rows_from_docx_reads_the_zip(tmp_path):
    p = tmp_path / "index.docx"
    _write_minimal_docx(p, [["Article no (Komet)", "Reference no.", "Where to find DoC"],
                             ["043690K0", "9978.000.000", "533221"]])

    rows = cm.rows_from_docx(p, DOCX_COLS)

    assert rows == [cm.Row("043690K0", "9978.000.000", "533221")]


def test_read_source_dispatches_xlsx(tmp_path):
    _xlsx(tmp_path / "index.xlsx",
          [["Article no.", "Reference no.", "Product family (Where to find DoC)"],
           ["000085K3", "H1.314.006", 532854]])
    source = {"file": "index.xlsx", "sheet": "Medical Devices Only - find DoC", "columns": COLS}

    rows = cm.read_source(tmp_path, source)

    assert rows == [cm.Row("000085K3", "H1.314.006", "532854")]


def test_read_source_dispatches_docx(tmp_path):
    _write_minimal_docx(tmp_path / "index.docx",
                         [["Article no (Komet)", "Reference no.", "Where to find DoC"],
                          ["043690K0", "9978.000.000", "533221"]])
    source = {"file": "index.docx", "columns": DOCX_COLS}

    rows = cm.read_source(tmp_path, source)

    assert rows == [cm.Row("043690K0", "9978.000.000", "533221")]


def test_read_source_missing_file_is_a_hard_error(tmp_path):
    source = {"file": "does-not-exist.xlsx", "sheet": "S", "columns": COLS}

    with pytest.raises(cm.CoverageMapError, match="does-not-exist.xlsx"):
        cm.read_source(tmp_path, source)


def test_read_source_unsupported_suffix_is_a_hard_error(tmp_path):
    p = tmp_path / "index.csv"
    p.write_text("a,b,c\n")
    source = {"file": "index.csv", "columns": COLS}

    with pytest.raises(cm.CoverageMapError, match=r"\.csv"):
        cm.read_source(tmp_path, source)
