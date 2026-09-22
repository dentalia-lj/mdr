# Komet Coverage Map Implementation Plan

**Goal:** Link Komet's 229 medical-device catalogue items to their declarations using the index Komet itself ships, instead of parsing REF lists out of 186 PDFs.

**Architecture:** A playbook key declares the two index files and how their "Where to find DoC" key resolves to a document. BACKFILL archives those files and files one document per `(family, regulation)` instead of one per PDF. A one-shot CLI tool then reads the archived index, appends a `ref_list` to each document's extraction at a new revision — carrying the index file as that field's `archive_url` — and re-emits `validate.doc`, so the existing C1 gate forms the links and GATE writes them.

**Tech Stack:** Python 3.12, psycopg 3 (raw SQL, no ORM), openpyxl and pandas (already in the worker image via `pip install ".[extract,resolve,ingest,discover,fetch]"`, Dockerfile:27), zipfile+re for the .docx, pytest against a real Postgres.

**Spec:** `docs/superpowers/specs/2026-08-18-komet-coverage-map-design.md` — read it first. This plan implements phase 1 (§2 "In") under the §9(a) ruling.

## Global Constraints

- **Invariant 1** — only `gate.candidate` / `gate.apply` handlers write `document`, `item_document`, `evidence`. Nothing in this plan writes them directly.
- **Invariant 2** — every production value carries complete evidence `(archive_url, page, verbatim, tier, model_id, confidence, extracted_at)`. A map-sourced `ref_list` carries the **index file's** `archive_url`, not the document's.
- **Invariant 7** — job types are a closed enum. This plan adds none.
- **Invariant 9** — job payloads are immutable after enqueue; payload fields are additive-only.
- **No mocking Postgres** (CLAUDE.md). Tests use the `conn` fixture in `tests/conftest.py`.
- **Every test is mutation-checked** before it is kept: break the line it guards, watch it fail, restore.
- Commit messages carry no Claude/AI attribution, no emoji, no em-dashes.
- Playbook rules are authored for one manufacturer only and a test holds them there — the pattern `ref_normalize` and `companion` already use.
- Run the full suite before each commit: `python -m pytest -q`. Baseline at the time of writing: **1414 passed, 2 skipped**.

---

## File Structure

| File | Responsibility |
|---|---|
| `app/playbooks.py` | parse + validate the `coverage_map` block (Task 1) |
| `playbooks/komet.json` | the authored rule (Task 1) |
| `app/coverage_map.py` | **new** — read index rows out of .xlsx / .docx; no database, no queue (Task 2) |
| `app/handlers/backfill.py` | choose the canonical file per `(family, regulation)`; archive the index files (Tasks 3, 4) |
| `app/handlers/validate.py` | a `ref_list` marked `source: coverage-map` yields basis `map-supplier` (Task 5) |
| `app/komet_coverage.py` | **new** — plan / render / apply, mirroring `app/repair_ref_list.py` (Task 6) |
| `app/cli.py` | register `komet-coverage`, dry run by default (Task 6) |

---

### Task 1: The `coverage_map` playbook key

**Files:**
- Modify: `app/playbooks.py` (dataclass field, `_parse` validation, constructor call)
- Modify: `playbooks/komet.json`
- Modify: `playbooks/README.md`
- Test: `tests/test_playbooks.py`, `tests/test_t0_layout.py` (allowed-keys set)

**Interfaces:**
- Produces: `Playbook.coverage_map: dict | None`, with keys `sources` (list of `{file, sheet?, columns:{article,reference,key}}`), `key_resolves` (list of `{kind, under}`), `canonical` (`{prefer: [...], annex: [...]}`).

- [ ] **Step 1: Write the failing tests**

```python
def test_load_parses_coverage_map(tmp_path):
    cm = {"sources": [{"file": "index.xlsx", "sheet": "S",
                       "columns": {"article": "A", "reference": "R", "key": "K"}}],
          "key_resolves": [{"kind": "filename-prefix", "under": "DOC"}],
          "canonical": {"prefer": ["_List_SIGNED"], "annex": ["_RA_812_Liste"]}}
    _write(tmp_path, "komet.json", dict(VOCO, coverage_map=cm))

    assert playbooks.load_playbooks(tmp_path)[0].coverage_map == cm


def test_coverage_map_defaults_to_none(tmp_path):
    _write(tmp_path, "voco.json", VOCO)
    assert playbooks.load_playbooks(tmp_path)[0].coverage_map is None


@pytest.mark.parametrize("bad,why", [
    ("not-an-object", "must be an object"),
    ({"sources": []}, "needs"),
    ({"sources": [], "key_resolves": [], "canonical": {}}, "must not be empty"),
    ({"sources": [{"file": "i.xlsx", "columns": {"article": "A"}}],
      "key_resolves": [], "canonical": {}}, "columns need"),
    ({"sources": [{"columns": {"article": "A", "reference": "R", "key": "K"}}],
      "key_resolves": [], "canonical": {}}, "needs a file"),
])
def test_a_malformed_coverage_map_is_an_authoring_error(tmp_path, bad, why):
    """The map decides which document covers which article. A source whose
    columns are half-declared would map some rows and drop the rest without a
    word, so it is rejected at load, exactly as `companion` and `ref_normalize`
    are."""
    with pytest.raises(ValueError, match=why):
        playbooks._parse("bad", dict(VOCO, coverage_map=bad))
    _write(tmp_path, "bad.json", dict(VOCO, coverage_map=bad))
    assert playbooks.load_playbooks(tmp_path) == ()


def test_only_komet_authors_a_coverage_map():
    loaded = {pb.slug: pb for pb in playbooks.load_playbooks()}
    assert loaded["komet"].coverage_map is not None
    for slug, pb in loaded.items():
        if slug != "komet":
            assert pb.coverage_map is None, f"{slug} must not carry a coverage_map"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python -m pytest tests/test_playbooks.py -q -k coverage_map`
Expected: FAIL — `Playbook.__init__() got an unexpected keyword argument 'coverage_map'` on the first, `AttributeError` on the rest.

- [ ] **Step 3: Add the field and its validation**

In `app/playbooks.py`, after the `companion` field on the `Playbook` dataclass:

```python
    # Komet ships an index -- a spreadsheet and a Word table -- naming which
    # document covers which article. Read by the one-shot `komet-coverage` CLI,
    # never by a handler on the live path: 246 of 319 items, stated by the
    # manufacturer rather than parsed out of its PDFs (measured 2026-08-18).
    coverage_map: dict | None = None
```

In `_parse`, after the `companion` block:

```python
    coverage_map = data.get("coverage_map")
    if coverage_map is not None:
        if not isinstance(coverage_map, dict):
            raise ValueError("coverage_map must be an object")
        absent = {"sources", "key_resolves", "canonical"} - set(coverage_map)
        if absent:
            raise ValueError(f"coverage_map needs {sorted(absent)}")
        if not coverage_map["sources"]:
            raise ValueError("coverage_map.sources must not be empty")
        for src in coverage_map["sources"]:
            if not src.get("file"):
                raise ValueError("coverage_map source needs a file")
            missing_cols = {"article", "reference", "key"} - set(src.get("columns") or {})
            if missing_cols:
                raise ValueError(f"coverage_map source columns need {sorted(missing_cols)}")
        canonical = coverage_map["canonical"]
        # `canonical.key` is the pairing regex and it is deliberately NOT
        # borrowed from `companion`: a manufacturer may split documents without
        # shipping an index, or ship an index without splitting documents.
        if not canonical.get("key"):
            raise ValueError("coverage_map.canonical needs a key regex")
        try:
            if re.compile(canonical["key"]).groups != 1:
                raise ValueError("coverage_map.canonical.key needs exactly one capture group")
        except re.error as exc:
            raise ValueError(f"coverage_map.canonical.key is not a valid regex: {exc}") from exc
```

and pass `coverage_map=coverage_map` in the `Playbook(...)` constructor call.

- [ ] **Step 4: Author the rule on `playbooks/komet.json`**

Append after the `companion` line (mind the trailing comma on the line above):

```json
  "coverage_map": {
    "sources": [
      {"file": "1-Where to find DoC (Medical Devices Only).xlsx",
       "sheet": "Medical Devices Only - find DoC",
       "columns": {"article": "Article no.", "reference": "Reference no.",
                   "key": "Product family (Where to find DoC)"}},
      {"file": "Artikli, kjer je originalni proizvajalec drug kot Komet/SPECIFIKACIJA.docx",
       "columns": {"article": "Article no (Komet)", "reference": "Reference no.",
                   "key": "Where to find DoC"}}
    ],
    "key_resolves": [
      {"kind": "filename-prefix", "under": "DOC"},
      {"kind": "folder", "under": "Artikli, kjer je originalni proizvajalec drug kot Komet"}
    ],
    "canonical": {"key": "^(\\d+)",
                  "prefer": ["_List_SIGNED", "_DoC_List_"],
                  "annex": ["_RA_812_Liste", "_Liste.pdf"]}
  }
```

- [ ] **Step 5: Add `coverage_map` to the allowed-keys set**

`tests/test_t0_layout.py` asserts every playbook's keys are a subset of a fixed set. Add to that set, beside `ref_normalize` and `companion`:

```python
        # coverage_map (2026-08-18): the manufacturer's own article -> document
        # index. Read by the komet-coverage CLI, owned by app/playbooks.py --
        # same category as ref_normalize and companion, not a parsing key.
        "coverage_map",
```

- [ ] **Step 6: Run the tests**

Run: `python -m pytest tests/test_playbooks.py tests/test_t0_layout.py -q`
Expected: PASS.

- [ ] **Step 7: Document it in `playbooks/README.md`**

Add a row to the Fields table:

```
| `coverage_map` | no | `{sources, key_resolves, canonical}`. The manufacturer's own article -> document index. Read by the one-shot `komet-coverage` CLI, never on the live path. See below. |
```

and a section after the `companion` one explaining: what the two index files are, that the `key` column is the document key, that it resolves as a filename prefix under `DOC/` or a folder name under `Artikli.../`, and that 246 of 319 Komet items are covered by it (229 of the 258 BC flags as medical devices).

- [ ] **Step 8: Mutation-check**

Delete the `if not coverage_map["sources"]` guard; `test_a_malformed_coverage_map_is_an_authoring_error[...must not be empty]` must fail. Restore.

- [ ] **Step 9: Commit**

```bash
git add app/playbooks.py playbooks/komet.json playbooks/README.md \
        tests/test_playbooks.py tests/test_t0_layout.py
git commit -m "playbooks: the index a manufacturer keeps of its own documents"
```

---

### Task 2: Reading the index files

**Files:**
- Create: `app/coverage_map.py`
- Test: `tests/test_coverage_map.py` (new)

**Interfaces:**
- Produces:
  - `class CoverageMapError(Exception)`
  - `Row = NamedTuple("Row", [("article", str), ("reference", str), ("key", str)])`
  - `rows_from_xlsx(path: str | Path, sheet: str, columns: dict) -> list[Row]`
  - `rows_from_docx_xml(xml: str, columns: dict) -> list[Row]`
  - `rows_from_docx(path: str | Path, columns: dict) -> list[Row]`
  - `read_source(base_dir: Path, source: dict) -> list[Row]` — dispatches on the file suffix
- Consumes: nothing. No database, no queue, no playbook loading — this module is pure file→rows so the awkward half is testable without fixtures.

- [ ] **Step 1: Write the failing tests**

```python
"""Reading a manufacturer's own article -> document index.

Pure file -> rows. The .docx half is tested on a crafted XML string rather than
a committed binary: the real file is a hand-maintained working document and a
fixture of it would rot without anyone noticing.
"""

from __future__ import annotations

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


def test_the_key_is_read_as_text_not_a_float(tmp_path):
    """openpyxl gives an integer cell back as int and pandas as float; both
    render 532854 as '532854.0' or 532854, and neither matches a filename."""
    p = tmp_path / "index.xlsx"
    _xlsx(p, [["Article no.", "Reference no.", "Product family (Where to find DoC)"],
              ["000085K3", "H1.314.006", 532854]])

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


def _cell(text):
    return f"<w:tc><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:tc>"


def _docx_xml(rows):
    return "<w:document><w:body><w:tbl>" + "".join(
        "<w:tr>" + "".join(_cell(c) for c in row) + "</w:tr>" for row in rows
    ) + "</w:tbl></w:body></w:document>"


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
    xml = ("<w:document><w:body><w:tbl>"
           "<w:tr>" + "".join(_cell(c) for c in
                              ["Article no (Komet)", "Reference no.", "Where to find DoC"]) + "</w:tr>"
           "<w:tr>" + _cell("043690K0") + _cell("9978.000.000") + split + "</w:tr>"
           "</w:tbl></w:body></w:document>")

    assert cm.rows_from_docx_xml(xml, DOCX_COLS)[0].key == "533221"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python -m pytest tests/test_coverage_map.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.coverage_map'`.

- [ ] **Step 3: Write the module**

```python
"""Reading a manufacturer's own article -> document index.

Komet ships two: a spreadsheet listing 3.800 medical-device articles against
the "Product family (Where to find DoC)" that covers them, and a Word table
doing the same for the articles Komet sells but did not manufacture. Both are
hand-maintained working documents, so every column is located BY ITS DECLARED
NAME and a missing one stops the read. A silently shifted column would re-point
every article to the wrong document at once and look like success.

Pure file -> rows: no database, no queue, no playbook loading. The .docx reader
is split into an XML half so it can be tested on a string instead of a binary
fixture that would rot unnoticed.
"""

from __future__ import annotations

import pathlib
import re
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


_CELL = re.compile(r"<w:tc>.*?</w:tc>", re.S)
_ROW = re.compile(r"<w:tr[ >].*?</w:tr>|<w:tr>.*?</w:tr>", re.S)
_TAG = re.compile(r"<[^>]+>")


def rows_from_docx_xml(xml: str, columns: dict) -> list[Row]:
    """Rows of the first table whose header carries all three declared columns.

    Word splits a cell's text across <w:r> runs at every formatting change, so
    the tags are stripped from the whole cell rather than the first run read --
    '533221' arrives as '5332' + '21' often enough to matter.
    """
    grid = []
    for row_xml in _ROW.findall(xml):
        cells = [_TAG.sub("", c).strip() for c in _CELL.findall(row_xml)]
        if cells:
            grid.append(cells)
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
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_coverage_map.py -q`
Expected: PASS, 7 tests.

- [ ] **Step 5: Prove it on the real files**

Run:

```bash
python -c "
from app import coverage_map as cm, playbooks
pb = playbooks.for_manufacturer('KOMET')
base = 'imports/dentalia-sftp/KOMET'
for s in pb.coverage_map['sources']:
    rows = cm.read_source(base, s)
    print(f\"{s['file'][:44]:46} {len(rows):5} rows  {len({r.key for r in rows}):3} keys\")
"
```

Expected, and this is the acceptance figure: **3800 rows / 42 keys** for the .xlsx and **39 rows / 11 keys** for the .docx. Any other number means the reader is wrong; stop and find out why before continuing.

- [ ] **Step 6: Mutation-check**

Replace `_header_index`'s `raise` with `index[role] = 0`; `test_a_reordered_or_renamed_column_is_a_hard_error` and `test_a_docx_without_the_declared_header_is_a_hard_error` must both fail. Restore.

- [ ] **Step 7: Commit**

```bash
git add app/coverage_map.py tests/test_coverage_map.py
git commit -m "coverage-map: read the index by column name, or not at all"
```

---

### Task 3: One document per `(family, regulation)`

**Files:**
- Modify: `app/handlers/backfill.py`
- Test: `tests/test_backfill_handler.py`

**Interfaces:**
- Consumes: `Playbook.coverage_map["canonical"]` from Task 1.
- Produces: `_canonical_choice(candidates, rule) -> tuple[set[Path], dict]` — `(paths that are NOT documents, {path: reason})`; the scan result gains `redundant` and `canonical_ambiguous` counts.

**Why:** Komet publishes the MDR declaration twice — bare, and merged with its product list. Both would file as `DoC` / `MDR` over the same group, which is invariant 5's `(coverage subject, type, regulation)` supersession triple, racing itself. Verified on `532797`: the merged file is 2 pages / 10.188 chars / 22 codes, the bare declaration 1 page / 3.178 chars / 0 codes, and the merged file's code set equals the annex's exactly.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_merged_form_wins_and_the_bare_one_is_not_a_document(conn, tmp_path):
    """Komet publishes the MDR declaration bare AND merged with its list. Both
    would file as DoC/MDR over one group -- invariant 5's supersession triple,
    racing itself. The merged form is what Komet signed as one artifact, so it
    is the document and the bare form is kept only as bytes."""
    folder = _komet(tmp_path)
    (folder / "532797_RA_810_DoC_EU_MDR_List_SIGNED.pdf").write_bytes(b"MERGED")
    (folder / "532797_RA_810_DoC_EU_MDR_SIGNED.pdf").write_bytes(b"BARE-MDR")
    (folder / "532797_RA_810_DoC_EU_SIGNED.pdf").write_bytes(b"MDD")
    (folder / "532797_RA_812_Liste_DoC.pdf").write_bytes(b"LIST")

    res = bh.handle_backfill_scan(
        conn, {"payload": {"drive_folder": str(folder)}}, store=_MemStore())

    assert res["emitted"] == 2          # merged MDR + MDD declaration
    assert res["annexes"] == 1
    assert res["redundant"] == 1
    emitted = {j["payload"]["source_url"] for j in _extract_jobs(conn)}
    assert any(s.endswith("_MDR_List_SIGNED.pdf") for s in emitted)
    assert not any(s.endswith("_RA_810_DoC_EU_MDR_SIGNED.pdf") for s in emitted)


def test_a_family_with_no_merged_form_keeps_its_bare_declaration(conn, tmp_path):
    """11 families ship no merged file. Their declarations are the documents,
    and the annex mechanism supplies the list."""
    folder = _komet(tmp_path)
    (folder / "532624_RA_810_DoC_EU_MDR_SIGNED.pdf").write_bytes(b"BARE-MDR")
    (folder / "532624_RA_810_DoC_EU_SIGNED.pdf").write_bytes(b"MDD")
    (folder / "532624_RA_812_Liste_DoC.pdf").write_bytes(b"LIST")

    res = bh.handle_backfill_scan(
        conn, {"payload": {"drive_folder": str(folder)}}, store=_MemStore())

    assert res["emitted"] == 2
    assert res["redundant"] == 0


def test_two_merged_candidates_for_one_pair_is_a_refusal(conn, tmp_path):
    """Same discipline as the ambiguous annex key: when the rule cannot say
    which file is canonical, nothing is suppressed and the count says so.
    Guessing is what handed a Class Is list to a Class I declaration."""
    folder = _komet(tmp_path)
    (folder / "532797_RA_810_DoC_EU_MDR_List_SIGNED.pdf").write_bytes(b"MERGED-A")
    (folder / "532797_RA_810_DoC_EU_MDR_DoC_List_SIGNED.pdf").write_bytes(b"MERGED-B")

    res = bh.handle_backfill_scan(
        conn, {"payload": {"drive_folder": str(folder)}}, store=_MemStore())

    assert res["canonical_ambiguous"] == 1
    assert res["redundant"] == 0
    assert res["emitted"] == 2


def test_a_manufacturer_without_a_coverage_map_files_every_declaration(conn, tmp_path):
    folder = tmp_path / "VOCO"
    folder.mkdir()
    (folder / "532797_RA_810_DoC_EU_MDR_List_SIGNED.pdf").write_bytes(b"MERGED")
    (folder / "532797_RA_810_DoC_EU_MDR_SIGNED.pdf").write_bytes(b"BARE-MDR")

    res = bh.handle_backfill_scan(
        conn, {"payload": {"drive_folder": str(folder)}}, store=_MemStore())

    assert res["emitted"] == 2
    assert res["redundant"] == 0
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python -m pytest tests/test_backfill_handler.py -q -k "merged or redundant or canonical"`
Expected: FAIL — `KeyError: 'redundant'`.

- [ ] **Step 3: Implement the choice**

In `app/handlers/backfill.py`, beside `_pair`:

```python
def _regulation_of(name: str) -> str:
    """Which regulation a Komet filename declares. `_DoC_EU_SIGNED` with no
    marker is the MDD-era declaration; the corpus writes the MDR ones with an
    explicit MDR in the name."""
    upper = name.upper()
    if "MDR" in upper:
        return "MDR"
    if "MDD" in upper:
        return "MDD"
    return "EU"


def _canonical_choice(candidates, rule: dict) -> tuple[set, dict]:
    """Which files are NOT documents because a better form of the same
    declaration exists, and why.

    Komet publishes the MDR declaration twice: bare, and merged with its
    product list (`_List_SIGNED`). Both carry the same type, regulation and
    coverage, which is exactly the triple invariant 5 supersedes on, so filing
    both leaves two DoCs racing over one list. The merged file is the artifact
    Komet signed as a whole, so it wins.

    Two merged candidates for one `(family, regulation)` is a refusal, not a
    tie-break: the rule cannot say which is canonical, so nothing is suppressed
    and the caller counts it."""
    key_re = re.compile(rule["key"])
    prefer = tuple(rule.get("prefer", ()))
    annex = tuple(rule.get("annex", ()))
    by_pair, ambiguous = {}, set()
    for path in candidates:
        name = path.name
        if any(a in name for a in annex):
            continue
        if not any(p in name for p in prefer):
            continue
        m = key_re.search(name)
        pair = (m.group(1) if m else name, _regulation_of(name))
        if pair in by_pair:
            ambiguous.add(pair)
        by_pair[pair] = path

    for pair in ambiguous:
        by_pair.pop(pair, None)

    redundant = {}
    for path in candidates:
        name = path.name
        if any(a in name for a in annex) or any(p in name for p in prefer):
            continue
        m = key_re.search(name)
        pair = (m.group(1) if m else name, _regulation_of(name))
        winner = by_pair.get(pair)
        if winner is not None:
            redundant[path] = f"superseded by {winner.name}"
    return set(redundant), {"redundant": redundant, "ambiguous": sorted(ambiguous)}
```

In `handle_backfill_scan`, after the `_pair` call:

```python
    cmap = _coverage_map_rule(_brand(folder))
    redundant_paths, canon = (
        _canonical_choice(candidates, cmap["canonical"])
        if cmap else (set(), {"redundant": {}, "ambiguous": []})
    )
```

with, beside `_companion_rule`:

```python
def _coverage_map_rule(brand: str) -> dict | None:
    pb = playbooks.for_manufacturer(brand)
    return pb.coverage_map if pb else None
```

Add `redundant = 0` to the counter line, skip enqueueing inside the loop:

```python
        if path in redundant_paths:
            redundant += 1
            continue
```

placed immediately after the `if is_annex:` block, and extend the result:

```python
            "redundant": redundant,
            "canonical_ambiguous": len(canon["ambiguous"]),
```

Log the ambiguous pairs at WARNING beside the existing companion warning.

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_backfill_handler.py -q`
Expected: PASS — the four new tests plus the 24 already there.

- [ ] **Step 5: Mutation-check**

Change `by_pair.pop(pair, None)` to `pass`; `test_two_merged_candidates_for_one_pair_is_a_refusal` must fail. Restore.

- [ ] **Step 6: Commit**

```bash
git add app/handlers/backfill.py tests/test_backfill_handler.py
git commit -m "backfill: Komet signs the same declaration twice, we file it once"
```

---

### Task 4: Archive the index files

**Files:**
- Modify: `app/handlers/backfill.py`
- Test: `tests/test_backfill_handler.py`

**Interfaces:**
- Produces: `index_archived` on the scan result, and `{source file: archive_url}` recorded via the fetch ledger so Task 6 can find them.

**Why:** `_pdfs` yields only `.pdf`, so the index files are currently invisible to the scan. The CLI must read the **archived** copy, not the corpus path — the corpus is a hand-managed dump that gets cleared and re-dumped, and evidence pointing into it dies with the next dump (`backfill.py` module docstring; the same mistake left 731 GC evidence rows pointing at corpus paths).

- [ ] **Step 1: Write the failing test**

```python
def test_the_index_files_are_archived_and_ledgered(conn, tmp_path):
    """The CLI reads the ARCHIVED index, never the corpus path: the corpus is
    a dump that gets cleared, and a handle into it dies with the next one."""
    folder = _komet(tmp_path)
    (folder / "1-Where to find DoC (Medical Devices Only).xlsx").write_bytes(b"XLSX-BYTES")
    (folder / "532624_RA_810_DoC_EU_SIGNED.pdf").write_bytes(b"DECLARATION")
    store = _MemStore()

    res = bh.handle_backfill_scan(
        conn, {"payload": {"drive_folder": str(folder)}}, store=store)

    assert res["index_archived"] == 1
    assert any(p.endswith(".xlsx") for _, p in store.puts)
    assert conn.execute(
        "SELECT count(*) c FROM fetch_log WHERE url_normalized LIKE '%%.xlsx'"
    ).fetchone()["c"] == 1
    # and it is NOT a document
    assert all(not j["payload"]["source_url"].endswith(".xlsx")
               for j in _extract_jobs(conn))


def test_a_declared_index_file_that_is_missing_fails_the_scan(conn, tmp_path):
    """A playbook naming an index the corpus does not carry is an authoring or
    dump error. Reporting `scanned: N` and linking nothing would look like
    success, which is how a wrong path stayed invisible before."""
    folder = _komet(tmp_path)
    (folder / "532624_RA_810_DoC_EU_SIGNED.pdf").write_bytes(b"DECLARATION")

    with pytest.raises(bh.BackfillError, match="index file"):
        bh.handle_backfill_scan(
            conn, {"payload": {"drive_folder": str(folder)}}, store=_MemStore())
```

Note: the first test needs `komet.json`'s second source to be tolerated as absent, so give `_komet` both files or narrow the assertion — create both the `.xlsx` and the `Artikli.../SPECIFIKACIJA.docx` in `_komet` for these two tests.

- [ ] **Step 2: Run and watch fail**

Run: `python -m pytest tests/test_backfill_handler.py -q -k index`
Expected: FAIL — `KeyError: 'index_archived'`.

- [ ] **Step 3: Implement**

In `handle_backfill_scan`, before the main loop and after the `candidates` check:

```python
    index_archived = 0
    for source in (cmap or {}).get("sources", ()):
        src_path = folder / source["file"]
        if not src_path.is_file():
            raise BackfillError(
                f"backfill.scan: coverage_map index file not found: {src_path}")
        body = src_path.read_bytes()
        content_hash = hashlib.sha256(body).hexdigest()
        _upsert_fetch_log(conn, str(src_path.resolve()), content_hash)
        store.put(body, archiving.archive_path(
            _brand(folder), content_hash, str(src_path.relative_to(folder)),
            _INDEX_CONTENT_TYPE))
        index_archived += 1
```

with `_INDEX_CONTENT_TYPE = "application/octet-stream"` beside `_PDF_CONTENT_TYPE`, and `"index_archived": index_archived` on the result.

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_backfill_handler.py -q`
Expected: PASS.

- [ ] **Step 5: Mutation-check**

Turn the `raise BackfillError` into `continue`; `test_a_declared_index_file_that_is_missing_fails_the_scan` must fail. Restore.

- [ ] **Step 6: Commit**

```bash
git add app/handlers/backfill.py tests/test_backfill_handler.py
git commit -m "backfill: the index is evidence, so it goes in the archive too"
```

---

### Task 5: `map-supplier`, the basis that names what matched

**Files:**
- Modify: `app/handlers/validate.py`
- Test: `tests/test_validate_handler.py`

**Interfaces:**
- Consumes: a `ref_list` evidence dict carrying `"source": "coverage-map"`, written by Task 6.
- Produces: links whose `match_basis` is `map-supplier` instead of `ref-list` / `ref-item`.

**Why:** this is spec §9(a). The rule is **universal and data-driven, not manufacturer-gated** — it triggers on a marker in the evidence, and no document in the registry carries one today, so it is inert until Task 6 writes the first.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_ref_list_from_the_coverage_map_names_its_own_basis(conn):
    """match_basis is what the registry keeps as provenance, so it must name
    the thing that actually matched. A list read out of the manufacturer's
    index is not a list read off the document."""
    _seed_group_with_member(conn, group_id=7, manufacturer="KOMET", item_ref="000 H1 006")
    fields = _fields(coverage_scope="group")
    fields["ref_list"] = {"value": ["H1.314.006"], "conf": 0.95, "tier": "T0",
                          "verbatim": "row 3 of the index", "page": None,
                          "archive_url": "/archive/KOMET/idx/beef__index.xlsx",
                          "source": "coverage-map"}
    _seed_ext(conn, "komet-map", fields)

    res = vh.handle_validate_doc(conn, _job("komet-map", group_id=7))

    assert [l["match_basis"] for l in res["links"]] == ["map-supplier"]


def test_an_ordinary_ref_list_keeps_the_basis_it_always_had(conn):
    """Inert for every document in the registry: none carries the marker."""
    _seed_group_with_member(conn, group_id=7, manufacturer="KOMET", item_ref="000 H1 006")
    fields = _fields(coverage_scope="group")
    fields["ref_list"] = {"value": ["H1.314.006"], "conf": 0.9, "tier": "T0",
                          "verbatim": "1 REF code", "page": 1}
    _seed_ext(conn, "komet-parsed", fields)

    res = vh.handle_validate_doc(conn, _job("komet-parsed", group_id=7))

    assert [l["match_basis"] for l in res["links"]] == ["ref-item"]


def test_a_basic_udi_match_is_not_relabelled_by_the_marker(conn):
    """The marker describes where the REF LIST came from. A Basic UDI-DI match
    is a different key and keeps its own, stronger basis."""
    _seed_group_with_member(conn, group_id=7, manufacturer="KOMET",
                            item_ref="000 H1 006", basic_udi_di="++E2265330681")
    fields = _fields(coverage_scope="group")
    fields["basic_udi_di"] = {"value": "++E2265330681", "conf": 0.99, "tier": "T0",
                              "verbatim": "x", "page": 1}
    fields["ref_list"] = {"value": ["H1.314.006"], "conf": 0.95, "tier": "T0",
                          "verbatim": "row 3", "page": None, "source": "coverage-map"}
    _seed_ext(conn, "komet-udi", fields)

    res = vh.handle_validate_doc(conn, _job("komet-udi", group_id=7))

    assert [l["match_basis"] for l in res["links"]] == ["basic-udi-di"]
```

Use whatever seeding helpers `tests/test_validate_handler.py` already defines; if `_seed_group_with_member` and `_job` do not exist under those names, use the file's existing equivalents rather than inventing new ones.

- [ ] **Step 2: Run and watch fail**

Run: `python -m pytest tests/test_validate_handler.py -q -k "coverage_map or basis"`
Expected: FAIL — `assert ['ref-item'] == ['map-supplier']`.

- [ ] **Step 3: Thread the override**

`app/handlers/validate.py`, three edits.

Add to the rank map (a map link is as strong as the list it came from, and stronger than a bare item-number hit):

```python
_BASIS_RANK = {"basic-udi-di": 0, "map-supplier": 1, "ref-list": 2, "ref-item": 3}
```

Give `_ref_gate_for_group` and the two functions that call it an optional override, defaulting to `None`, and apply it only on the REF-overlap branch:

```python
def _ref_gate_for_group(conn, group: dict, ref_list: list, basic_udi_di,
                        basis_override: str | None = None) -> tuple[bool, list[dict]]:
    # docstring and body unchanged, including the Basic UDI-DI branch above,
    # down to the REF-overlap branch:
        if overlapping:
            links = [{"item_ref": m["item_ref"],
                      "match_basis": basis_override or basis}
                     for m, basis in overlapping]
            return True, links
```

Add the same parameter to `_links_for_manufacturer` and pass it through to its `_ref_gate_for_group` call.

In `handle_validate_doc`, beside where `ref_list` is read:

```python
    ref_list = _val(fields, "ref_list") or []
    # A ref_list read out of the manufacturer's own article -> document index
    # rather than off the document. `match_basis` is what the registry keeps as
    # provenance, so it names that. Universal and data-driven: no document
    # written before 2026-08-18 carries the marker, so this is inert for all of
    # them (spec 2026-08-18-komet-coverage-map-design.md, section 9a).
    basis_override = ("map-supplier"
                      if (fields.get("ref_list") or {}).get("source") == "coverage-map"
                      else None)
```

and pass `basis_override` at every `_links_for_manufacturer` / `_ref_gate_for_group` call site in that function.

Add the docstring note to `_ref_gate_for_group` explaining that the override never touches the Basic UDI-DI branch.

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_validate_handler.py -q`
Expected: PASS, all of them.

- [ ] **Step 5: Check the CHECK constraints**

`map-supplier` must NOT appear in the capped list. Confirm:

```bash
grep -n "match_basis IN" migrations/*.sql
```

Expected: only `name-family`, `fetch-context`, `ref-catalogue` are barred from `production` (migrations 005 and 021). `map-supplier` is absent from both, so it is production-capable with no migration — which is decision D1. If a future migration caps it, that reverses D1 and needs Denis.

- [ ] **Step 6: Mutation-check**

Change `basis_override or basis` to `basis`; `test_a_ref_list_from_the_coverage_map_names_its_own_basis` must fail while the other two pass. Restore.

- [ ] **Step 7: Commit**

```bash
git add app/handlers/validate.py tests/test_validate_handler.py
git commit -m "validate: a list read from the index is not a list read off the document"
```

---

### Task 6: The `komet-coverage` CLI

**Files:**
- Create: `app/komet_coverage.py`
- Modify: `app/cli.py`
- Test: `tests/test_komet_coverage.py` (new)

**Interfaces:**
- Consumes: `coverage_map.read_source` (Task 2), `Playbook.coverage_map` (Task 1), `tiers.next_extract_rev` / `tiers.write_extraction_attempt`, `queue.enqueue`.
- Produces: `plan(conn) -> tuple[list[dict], list[dict]]`, `render(plans, skipped) -> list[str]`, `apply(conn, plans) -> dict`.

**Why this shape:** `app/repair_ref_list.py` is the same operation — append an `extraction_attempt` at a new rev, re-point the pending `validate.doc` at it, dry run by default. Following it keeps VALIDATE's flag computation applied to the document rather than bypassed, and keeps GATE the only writer.

- [ ] **Step 1: Write the failing tests**

```python
"""komet-coverage: link a manufacturer's items from the index it ships.

Same shape as repair_ref_list -- plan / render / apply, dry run by default --
because it is the same operation: append an extraction_attempt at a new rev and
re-point the pending validate.doc at it, so the existing gate forms the links.
"""

from __future__ import annotations

import pytest

from app import komet_coverage as kc


KEY = "532624"


def _seed_index(conn, tmp_path, rows):
    """Archive an index file the way BACKFILL does, and ledger it. Returns the
    archive_url the CLI must reconstruct."""
    import hashlib, openpyxl
    from app.handlers import archiving

    src = tmp_path / "1-Where to find DoC (Medical Devices Only).xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Medical Devices Only - find DoC"
    ws.append(["Article no.", "Reference no.", "UMNDS",
               "Product family (Where to find DoC)"])
    for r in rows:
        ws.append(r)
    wb.save(str(src))
    body = src.read_bytes()
    content_hash = hashlib.sha256(body).hexdigest()
    rel = archiving.archive_path("KOMET", content_hash, src.name,
                                 "application/octet-stream")
    archive_root = tmp_path / "archive"
    dest = archive_root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    conn.execute(
        "INSERT INTO fetch_log (url_normalized, content_hash, source, fetched_at, "
        "last_checked_at) VALUES (%s, %s, 'backfill', now(), now())",
        (f"/imports/dentalia-sftp/KOMET/{src.name}", content_hash))
    return str(dest), archive_root


def _seed_document(conn, content_hash, archive_url, fields=None):
    """A document as GATE leaves it, plus the extraction it came from."""
    from app.extract import tiers
    fields = fields or {
        "type": {"value": "DoC", "conf": 0.99, "tier": "T0", "verbatim": "x", "page": 1},
        "regulation": {"value": "MDR", "conf": 1.0, "tier": "T0", "verbatim": "x", "page": 1},
        "coverage_scope": {"value": "group", "conf": 0.97, "tier": "T0",
                           "verbatim": "x", "page": 1},
    }
    tiers.write_extraction_attempt(conn, content_hash, ["T0"], fields, extract_rev=1)
    conn.execute(
        "INSERT INTO document (content_hash, archive_url, type, regulation, "
        "coverage_scope, status) VALUES (%s, %s, 'DoC', 'MDR', 'group', 'staged') "
        "ON CONFLICT (content_hash) DO NOTHING",
        (content_hash, archive_url))
    return fields


def test_plan_maps_a_document_to_the_articles_its_family_covers(conn, tmp_path, monkeypatch):
    _, archive_root = _seed_index(conn, tmp_path, [
        ["000085K3", "H1.314.006", "16-668", KEY],
        ["000086K3", "H1.314.007", "16-668", KEY],
        ["000090K2", "H1.314.012", "16-668", "999999"],
    ])
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(archive_root))
    _seed_document(conn, "hash-532624",
                   f"/archive/KOMET/doc/abcdef__{KEY}_RA_810_DoC_EU_MDR_SIGNED.pdf")

    plans, skipped = kc.plan(conn)

    assert len(plans) == 1
    p = plans[0]
    assert p["key"] == KEY
    assert p["content_hash"] == "hash-532624"
    assert sorted(p["ref_list"]) == ["H1.314.006", "H1.314.007"]   # not the 999999 row
    assert [s["reason"] for s in skipped] == ["no document for family"]
    assert skipped[0]["key"] == "999999"


def test_plan_writes_nothing(conn, tmp_path, monkeypatch):
    """Dry run is the default and it must be observably free."""
    _, archive_root = _seed_index(conn, tmp_path, [["000085K3", "H1.314.006", "16-668", KEY]])
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(archive_root))
    _seed_document(conn, "hash-532624",
                   f"/archive/KOMET/doc/abcdef__{KEY}_RA_810_DoC_EU_MDR_SIGNED.pdf")
    before_att = conn.execute("SELECT count(*) c FROM extraction_attempt").fetchone()["c"]
    before_job = conn.execute("SELECT count(*) c FROM job").fetchone()["c"]

    kc.plan(conn)

    assert conn.execute("SELECT count(*) c FROM extraction_attempt").fetchone()["c"] == before_att
    assert conn.execute("SELECT count(*) c FROM job").fetchone()["c"] == before_job


def test_apply_appends_a_new_rev_and_re_emits_validate(conn, tmp_path, monkeypatch):
    """Same mechanism as repair_ref_list: the document's own fields survive,
    the map's ref_list joins them at rev+1, and validate.doc is re-pointed."""
    _, archive_root = _seed_index(conn, tmp_path, [["000085K3", "H1.314.006", "16-668", KEY]])
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(archive_root))
    _seed_document(conn, "hash-532624",
                   f"/archive/KOMET/doc/abcdef__{KEY}_RA_810_DoC_EU_MDR_SIGNED.pdf")

    stats = kc.apply(conn, kc.plan(conn)[0])

    assert stats["appended"] == 1
    assert stats["jobs_emitted"] == 1
    row = conn.execute(
        "SELECT extract_rev, fields FROM extraction_attempt "
        "WHERE content_hash='hash-532624' ORDER BY extract_rev DESC LIMIT 1").fetchone()
    assert row["extract_rev"] == 2
    assert row["fields"]["ref_list"]["value"] == ["H1.314.006"]
    assert row["fields"]["type"]["value"] == "DoC"          # the document's own fields survive
    job = conn.execute(
        "SELECT payload FROM job WHERE type='validate.doc' ORDER BY id DESC LIMIT 1").fetchone()
    assert job["payload"]["extract_rev"] == 2


def test_the_map_sourced_ref_list_carries_the_index_as_its_handle(conn, tmp_path, monkeypatch):
    """Invariant 2 asks where the value was READ. It was read from the index,
    not from the declaration, and the marker is what earns the map-supplier
    basis in VALIDATE."""
    index_path, archive_root = _seed_index(
        conn, tmp_path, [["000085K3", "H1.314.006", "16-668", KEY]])
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(archive_root))
    _seed_document(conn, "hash-532624",
                   f"/archive/KOMET/doc/abcdef__{KEY}_RA_810_DoC_EU_MDR_SIGNED.pdf")

    kc.apply(conn, kc.plan(conn)[0])

    ev = conn.execute(
        "SELECT fields FROM extraction_attempt WHERE content_hash='hash-532624' "
        "ORDER BY extract_rev DESC LIMIT 1").fetchone()["fields"]["ref_list"]
    assert ev["source"] == "coverage-map"
    assert ev["archive_url"].endswith(".xlsx")
    assert ev["tier"] == "T0"
    assert ev["page"] is None          # T0 evidence is legitimately pageless
    assert "532624" in ev["verbatim"]


def test_a_family_with_no_document_is_skipped_and_counted(conn, tmp_path, monkeypatch):
    """`533231` is named in SPECIFIKACIJA.docx and has no folder and no file
    anywhere in the corpus. Counted, never fatal, never silent."""
    _, archive_root = _seed_index(conn, tmp_path, [["043690K0", "9978.000.000", "", "533231"]])
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(archive_root))

    plans, skipped = kc.plan(conn)

    assert plans == []
    assert skipped == [{"key": "533231", "articles": 1, "reason": "no document for family"}]
    assert any("533231" in line for line in kc.render(plans, skipped))


def test_applying_twice_is_idempotent(conn, tmp_path, monkeypatch):
    """A second run must not stack revisions or duplicate validate jobs: the
    ref_list it would write is already the document's current one."""
    _, archive_root = _seed_index(conn, tmp_path, [["000085K3", "H1.314.006", "16-668", KEY]])
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(archive_root))
    _seed_document(conn, "hash-532624",
                   f"/archive/KOMET/doc/abcdef__{KEY}_RA_810_DoC_EU_MDR_SIGNED.pdf")
    kc.apply(conn, kc.plan(conn)[0])

    plans, skipped = kc.plan(conn)

    assert plans == []
    assert [s["reason"] for s in skipped] == ["already carries this list"]
    assert conn.execute(
        "SELECT max(extract_rev) m FROM extraction_attempt "
        "WHERE content_hash='hash-532624'").fetchone()["m"] == 2


def test_the_index_content_hash_is_reported(conn, tmp_path, monkeypatch):
    """The one survivor of the update discussion: a re-run against a changed
    index is a human decision, not a silent re-map, so the hash is printed."""
    _, archive_root = _seed_index(conn, tmp_path, [["000085K3", "H1.314.006", "16-668", KEY]])
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(archive_root))
    _seed_document(conn, "hash-532624",
                   f"/archive/KOMET/doc/abcdef__{KEY}_RA_810_DoC_EU_MDR_SIGNED.pdf")

    lines = kc.render(*kc.plan(conn))

    assert any("index" in l.lower() and len([t for t in l.split() if len(t) == 64]) == 1
               for l in lines), lines
```

**On finding the archived index:** `fetch_log` stores the SOURCE path and the
content hash, not an archive handle, and the index is not a `document` so it has
no `archive_url` column to read. The path is instead **reconstructed**, which is
safe because it is deterministic:
`archiving.archive_path(brand, content_hash, relative_name, content_type)` is a
pure function of those four values, and Task 4 archives with exactly them. Read
`STORAGE_LOCAL_ROOT` from `app.config.load_config()` to resolve it to a local
path, exactly as `LocalFsStore` does. If the reconstructed path does not exist,
raise — a missing archived index means the scan and the tool disagree, and
guessing at the corpus copy would put an ephemeral handle into evidence.

- [ ] **Step 2: Run and watch fail**

Run: `python -m pytest tests/test_komet_coverage.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.komet_coverage'`.

- [ ] **Step 3: Write the module**

```python
"""Link a manufacturer's items from the index it ships, once.

Komet publishes an article -> document index (a spreadsheet and a Word table).
This reads the ARCHIVED copy of both, resolves each "Where to find DoC" key to
the document filed for that family, and appends that document's REF list as a
new extraction revision whose ref_list carries the INDEX as its archive_url.
`validate.doc` is then re-emitted at the new rev, so the existing C1 gate forms
the links and GATE writes them (invariant 1). No new job type (invariant 7).

One-shot by design: `backfill.scan` is not scheduled, and steady-state refresh
is `doc_sources[kind:"direct"]` -> `fetch.url`. The index's content_hash is
reported so a re-run against a changed index is a human decision.
"""

from __future__ import annotations

import collections
import logging

from app import coverage_map, playbooks, queue
from app.extract import tiers
from app.handlers.validate import _fold_bur_code

log = logging.getLogger("dentalia.komet_coverage")

MARKER = "coverage-map"
```

The module then needs, in this order:

1. `_index_rows(conn, pb)` — for each `coverage_map.sources` entry, find its archived copy via `fetch_log` (`url_normalized LIKE '%' || file`), read it with `coverage_map.read_source`, and return `(rows, {file: archive_url}, {file: content_hash})`.
2. `_family_articles(rows)` — `{key: {folded reference or article}}`, folding through `_fold_bur_code` so the catalogue's spaced form and the index's dotted form meet.
3. `_documents_by_family(conn, resolves)` — every document whose `archive_url` carries a family key, per `key_resolves`: `filename-prefix` matches `__{key}` in the archived name, `folder` matches `/{key}/` in `source_url`. Returns `{key: [content_hash]}`.
4. `plan(conn)` — for each family with both articles and documents, build the repair dict: `{content_hash, extract_rev, fields, ref_list, key, index_url}`. Families with articles and no document go to `skipped` with `reason="no document for family"`.
5. `apply(conn, plans)` — for each plan, `rev = tiers.next_extract_rev(...)`, merge the map's `ref_list` into a copy of the document's current fields:

```python
        fields = dict(p["fields"])
        fields["ref_list"] = {
            "value": sorted(p["ref_list"]),
            "conf": 1.0,
            "tier": "T0",
            "model_id": None,
            "verbatim": f"{len(p['ref_list'])} article(s) under "
                        f"'Where to find DoC' = {p['key']}",
            "page": None,
            "archive_url": p["index_url"],
            "source": MARKER,
        }
        tiers.write_extraction_attempt(
            conn, p["content_hash"], ["T0"], fields, extract_rev=rev)
```

then delete the pending `validate.doc` for the old rev and enqueue one at the new rev, exactly as `repair_ref_list.apply` does, keeping the old job's `group_id`.

6. `render(plans, skipped)` — one line per family (`key`, document filename, article count), then every skip with its reason, then the totals **and the index content hashes**.

`conf: 1.0` is deliberate: the manufacturer stating which document covers its own article is not a probabilistic read. `page: None` is legitimate — invariant 2 requires `page` for T1/T2 evidence only ([evidence-page] ruling 2026-07-31).

- [ ] **Step 4: Register the command**

`app/cli.py`, mirroring `repair-ref-list`:

```python
def cmd_komet_coverage(args: argparse.Namespace) -> int:
    """Link items from a manufacturer's own document index. Dry run by default."""
    with db.connect() as conn:
        plans, skipped = komet_coverage.plan(conn)
        for line in komet_coverage.render(plans, skipped):
            print(line)
        if not plans:
            return 0
        if not args.apply:
            print("dry run — nothing written. Re-run with --apply.")
            return 0
        stats = komet_coverage.apply(conn, plans)
        conn.commit()
    print(f"\napplied: {stats['appended']} extraction_attempt row(s) appended, "
          f"{stats['jobs_deleted']} stale validate.doc job(s) deleted, "
          f"{stats['jobs_emitted']} re-emitted at the new rev")
    return 0
```

and in `build_parser`:

```python
    kcv = sub.add_parser(
        "komet-coverage",
        help="link items from the manufacturer's own document index (dry run by default)",
    )
    kcv.add_argument("--apply", action="store_true", help="write; without it, dry run")
    kcv.set_defaults(func=cmd_komet_coverage)
```

Add `komet_coverage` to the `from app import (...)` list at the top.

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_komet_coverage.py -q`
Expected: PASS.

- [ ] **Step 6: Mutation-check**

Remove `"source": MARKER` from the evidence dict; `test_a_ref_list_from_the_coverage_map_names_its_own_basis` (Task 5) still passes on its own seed, but the end-to-end test in this file asserting `match_basis == "map-supplier"` must fail. Restore.

- [ ] **Step 7: Commit**

```bash
git add app/komet_coverage.py app/cli.py tests/test_komet_coverage.py
git commit -m "cli: link Komet's items from the index Komet keeps"
```

---

### Task 7: The floor test and the operational docs

**Files:**
- Test: `tests/test_komet_coverage_corpus.py` (new)
- Modify: `docs/runbook.md`, `docs/code-map.md`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the corpus floor test**

```python
"""The measured coverage of Komet's index, pinned.

Skips when the local corpus is absent, exactly as tests/conftest.py's
`corpus_pdf` fixture does -- these numbers are Denis's machine, not CI.

Measured 2026-08-18: the two index files cover 246 of the 319 catalogue items
on BC code 077, and 229 of the 258 that BC flags as medical devices. The floor
is what those numbers must not silently fall below; when the index or the
catalogue moves, this is the test that says so.
"""

import pathlib

import pytest

from app import coverage_map as cm, playbooks
from app.handlers.validate import _fold_bur_code as fold

CORPUS = pathlib.Path("imports/dentalia-sftp/KOMET")

pytestmark = pytest.mark.skipif(not CORPUS.is_dir(), reason="local corpus absent")

ITEMS_FLOOR = 246
DEVICE_ITEMS_FLOOR = 229


def _keys():
    pb = playbooks.for_manufacturer("KOMET")
    keys = set()
    for source in pb.coverage_map["sources"]:
        for row in cm.read_source(CORPUS, source):
            keys.add(row.article)
            keys.add(fold(row.reference))
    return keys


def test_the_index_covers_the_measured_share_of_the_catalogue(conn):
    keys = _keys()
    rows = conn.execute(
        "SELECT item_ref, COALESCE(mfr_ref,'') mfr_ref, md_flag "
        "FROM item_mirror WHERE manufacturer_raw='077'").fetchall()
    if not rows:
        pytest.skip("Komet catalogue not ingested on this database")

    def covered(r):
        for v in (r["item_ref"], r["mfr_ref"]):
            v = (v or "").strip()
            if v and (v in keys or fold(v) in keys):
                return True
        return False

    total = sum(1 for r in rows if covered(r))
    devices = sum(1 for r in rows if r["md_flag"] and covered(r))
    assert total >= ITEMS_FLOOR, f"{total} of {len(rows)} covered, floor {ITEMS_FLOOR}"
    assert devices >= DEVICE_ITEMS_FLOOR
```

- [ ] **Step 2: Run it**

Run: `python -m pytest tests/test_komet_coverage_corpus.py -q`
Expected: PASS on a machine with the corpus and the Komet catalogue; SKIP otherwise.

- [ ] **Step 3: Add the runbook entry**

`docs/runbook.md`, beside the `backfill.scan` section — the exact sequence, because this is the part a human runs:

```bash
# 1. scan (archives PDFs + the two index files, files one doc per family+regulation)
docker compose run --rm worker python -m app.cli enqueue backfill.scan \
    backfill:KOMET:<date> --payload '{"drive_folder": "/imports/dentalia-sftp/KOMET"}'

# 2. let extract -> validate -> gate drain

# 3. dry run, and READ IT
docker compose run --rm worker python -m app.cli komet-coverage

# 4. apply
docker compose run --rm worker python -m app.cli komet-coverage --apply
```

State plainly: a re-scan emits nothing until the KOMET `fetch_log` rows are cleared (hash dedupe), and the index `content_hash` printed by step 3 is what tells you the index changed since last time.

- [ ] **Step 4: Add the code-map rows**

`docs/code-map.md`: one row for `app/coverage_map.py` (read an index file by declared column name) and one for `app/komet_coverage.py` (plan/render/apply the index into links), each naming its test file.

- [ ] **Step 5: Full suite**

Run: `python -m pytest -q`
Expected: PASS. Baseline was 1414 passed / 2 skipped; this plan adds roughly 25 tests.

- [ ] **Step 6: Commit**

```bash
git add tests/test_komet_coverage_corpus.py docs/runbook.md docs/code-map.md
git commit -m "docs: how to run the Komet coverage map, and what it must cover"
```

---

## Amendment to the spec, made during planning

Spec §5 says the **17 items Komet treats as devices while BC's `md_flag` is
empty** should be "raised as a `data_anomaly` so the BC flag can be corrected at
source". No task here does that, deliberately: `data_anomaly` rows are written
by handlers through an `app.results.Result` envelope, and this tool is a CLI
command with no job to attach one to. Worse, `md_flag` is INGEST's field — an
anomaly raised here would be filed against the wrong stage and would not repeat
on the next catalogue import, which is when the disagreement actually matters.

So `render` **prints** the disagreements, and raising them properly belongs to
INGEST as its own small change. If Denis wants the anomaly rows now rather than
later, that is a fourth argument to `plan`/`render` and one INSERT, and it
should be its own task rather than being smuggled into Task 6.

## After the plan

1. Rebuild the worker image (`docker compose build worker`) and hash-verify it against HEAD before starting anything — a background build that prints no `Step` lines has not run.
2. Clear the KOMET `fetch_log` rows, re-scan, let it drain, then run the dry run and read it before `--apply`.
3. Verify `532797` end-to-end first: 2 documents (MDD bare, MDR merged), 2 files suppressed, 22 codes on the MDR document with evidence citing the archived `.xlsx`, its items linked at `production` with basis `map-supplier`.

## Deferred, deliberately

- **Phase 2** — the 16 `Artikli/` items need their manufacturer re-bound first (spec §7). Its own design, after phase 1 has run.
- **Extraction cost** — with the map supplying coverage, letting T1/T2 chase `ref_list` on Komet documents buys nothing, but suppressing it is a `tiers.py` change on the shared path. Left in; the re-run costs roughly $6-8 for ~100 documents. Denis's call whether to fold it in.
