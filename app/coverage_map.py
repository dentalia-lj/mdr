"""Reading a manufacturer's own article -> document index.

Komet ships two: a spreadsheet listing 3.800 medical-device articles against
the "Product family (Where to find DoC)" that covers them, and a Word table
doing the same for the articles Komet sells but did not manufacture. Both are
hand-maintained working documents, so every column is located BY ITS DECLARED
NAME and a missing one stops the read. A silently shifted column would re-point
every article to the wrong document at once and look like success.

Pure file -> rows: no database, no queue, no playbook loading. The .docx reader
is split into an XML half so it can be tested on a string instead of a binary
fixture that would rot unnoticed. It reads the XML with the stdlib's
xml.etree.ElementTree rather than pattern matching, because a hand-maintained
document can carry more than one table, and can nest a table inside another
table's cell -- both need real structure to get right, not string scanning.
"""

from __future__ import annotations

import pathlib
import xml.etree.ElementTree as ET
import zipfile
from typing import NamedTuple


class CoverageMapError(Exception):
    """An index file that cannot be read the way the playbook declares."""


class Row(NamedTuple):
    article: str
    reference: str
    key: str


def _text(v) -> str:
    """Cell value as the string a filename could carry. openpyxl hands an
    integer key back as int; str(532854) is right and str(532854.0) is not, so
    a whole float is narrowed before formatting."""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def _header_index(header: list[str], columns: dict) -> dict:
    """{role: position} for the three declared columns, or raise naming the
    first one missing -- the operator needs to know WHICH column moved."""
    index = {}
    for role in ("article", "reference", "key"):
        want = columns[role]
        try:
            index[role] = header.index(want)
        except ValueError:
            raise CoverageMapError(
                f"column {want!r} not found; header is {header!r}"
            ) from None
    return index


def rows_from_xlsx(path, sheet: str, columns: dict) -> list[Row]:
    import openpyxl

    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    try:
        if sheet not in wb.sheetnames:
            raise CoverageMapError(f"sheet {sheet!r} not in {wb.sheetnames}")
        ws = wb[sheet]
        rows = ws.iter_rows(values_only=True)
        try:
            header = [_text(c) for c in next(rows)]
        except StopIteration:
            raise CoverageMapError(f"sheet {sheet!r} is empty") from None
        idx = _header_index(header, columns)
        out = []
        for raw in rows:
            cells = [_text(c) for c in raw]
            if max(idx.values()) >= len(cells):
                continue
            article = cells[idx["article"]]
            if not article:
                continue
            out.append(Row(article, cells[idx["reference"]], cells[idx["key"]]))
        return out
    finally:
        wb.close()


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _cell_text(tc: ET.Element) -> str:
    """Every <w:t> under this cell's own content, concatenated -- this is what
    joins a key Word split across runs at a formatting change ('533221'
    arriving as '5332' + '21'). A <w:tbl> nested inside the cell is never
    descended into, so a nested table's own text can never bleed into this
    cell's value."""
    parts: list[str] = []

    def walk(el: ET.Element) -> None:
        for child in el:
            if child.tag == _W + "tbl":
                continue
            if child.tag == _W + "t":
                parts.append(child.text or "")
            walk(child)

    walk(tc)
    return "".join(parts).strip()


def _table_grid(tbl: ET.Element) -> list[list[str]]:
    """This one table's own rows as cell text -- direct <w:tr>/<w:tc> children
    only. A <w:tbl> nested inside one of this table's cells has its own
    <w:tr> elements, which are not direct children of THIS <w:tbl>, so they
    never appear in this grid and never masquerade as this table's rows."""
    grid = []
    for tr in tbl.findall(_W + "tr"):
        cells = [_cell_text(tc) for tc in tr.findall(_W + "tc")]
        if cells:
            grid.append(cells)
    return grid


def _top_level_tables(root: ET.Element) -> list[ET.Element]:
    """<w:tbl> elements in document order, excluding any table nested inside
    another table's cell. A nested table is read (or not) only as part of its
    parent table's cells -- see _cell_text -- never picked as a candidate
    table of its own."""
    tables: list[ET.Element] = []

    def walk(el: ET.Element) -> None:
        for child in el:
            if child.tag == _W + "tbl":
                tables.append(child)
                continue  # do not look for further candidate tables inside it
            walk(child)

    walk(root)
    return tables


def rows_from_docx_xml(xml: str, columns: dict) -> list[Row]:
    """Rows of the first table whose header carries all three declared
    columns. Scoped to that one table: a second, unrelated table elsewhere in
    the document never contributes rows, because each table's grid (see
    _table_grid) is built and searched independently, and the function
    returns as soon as one table's header matches.
    """
    root = ET.fromstring(xml)
    for tbl in _top_level_tables(root):
        grid = _table_grid(tbl)
        for i, header in enumerate(grid):
            try:
                idx = _header_index(header, columns)
            except CoverageMapError:
                continue
            out = []
            for cells in grid[i + 1:]:
                if max(idx.values()) >= len(cells):
                    continue
                article = cells[idx["article"]]
                if not article:
                    continue
                out.append(Row(article, cells[idx["reference"]], cells[idx["key"]]))
            return out
    raise CoverageMapError(
        f"no table header carrying {sorted(columns.values())!r}"
    )


def rows_from_docx(path, columns: dict) -> list[Row]:
    with zipfile.ZipFile(str(path)) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    return rows_from_docx_xml(xml, columns)


def read_source(base_dir, source: dict) -> list[Row]:
    """One `coverage_map.sources` entry, resolved under `base_dir`."""
    path = pathlib.Path(base_dir) / source["file"]
    if not path.exists():
        raise CoverageMapError(f"index file not found: {path}")
    if path.suffix.lower() == ".xlsx":
        return rows_from_xlsx(path, source["sheet"], source["columns"])
    if path.suffix.lower() == ".docx":
        return rows_from_docx(path, source["columns"])
    raise CoverageMapError(f"unsupported index file type: {path.suffix}")
