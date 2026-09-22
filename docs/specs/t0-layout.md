# Unit spec — T0 layout-template engine (S1.5, unit B)

CLAUDE.md "PDF" line (T0 templates are "engine-agnostic data, never PyMuPDF-specific code"); `app/extract/t0_templates.py` (existing G1–G6 guardrails, current REF/label extraction); `tests/fixtures/corpus_manifest.py` `KNOWN_T0_GAPS`; followups tagged `[extract-t0]` (2026-07-06) and `[corpus-verify]` (2026-07-15). Read those first.

## 0. What was actually verified against the real corpus (this session)

Before designing anything, I opened the four xfailing fixtures plus both `[corpus-verify]`-flagged GC files with pdfplumber/PyMuPDF directly. Findings, since they determine the design:

1. **KOMET `533068_RA_812_Liste_DoC.pdf`** — 1 page, **zero pdfplumber tables** (visually columnar but not a real grid). Header line: `ID Packing REF / Product Description Basic UDI-DI UMDNS Code MD ClassCE Sign First Lot MDR GMDN-Code`. Every data row is `{ID} {Packing} {REF} {Description}++{Basic-UDI-DI}{rest}` — a plain 4-field split (`str.split(None, 3)`) isolates the REF as field index 2 cleanly across all 20 rows. Confirmed by direct extraction: 20/20 codes recovered (a header-continuation line — the wrapped "REF / Product\ngroup name" column label — sits between the header and the first data row; skipped by treating any non-parsing line before the first successful row as still-header, not end-of-table). The existing `_ref_from_text` fallback doesn't fire because its start-anchor requires the line to `startswith("ref")`, and this header starts with `"ID Packing REF..."`.
2. **IVOCLAR `IPS e.max Ceram.pdf`** — 8 pages. Page 3 has the real header table (`Article No.` column, 46 usable data rows after two blank classification rows). Pages 4–6 each hold one **headerless continuation table** (same column-0-is-REF layout, 51 rows each — pdfplumber treats row 0 as a "header" but it's data). Page 7 holds one more continuation table (7 rows) **followed by an unrelated "Revision History" table on the same page**. Page 8 has no REF data. Prototyped and ran the fix end-to-end: header-page detection + carry the column index into subsequent pages' **first table only**, gated by a ≥50%-of-column `_looks_like_ref` density check per page (stops at page 8, and never reaches "Revision History" because that's the *second* table on page 7, and only the first table per continuation page is considered) → **exactly 206 codes**, matching the followup's own estimate.
3. **GC `New_Metal_Strips_10102022_R.pdf` / `everX_Posterior_12022026.pdf`** — the REF table is **embedded on the last page of the same PDF** (not a separate file); pdfplumber finds it fine (`extract_ref_list` already returns 18 codes / a partial result when called directly, bypassing today's gate). Two independent bugs, not one:
   - `t0_extract` gates OFF all REF extraction whenever `detect_external_ref_list` fires (`"according to the attachment"` etc.) — correct for a genuinely-external attachment, wrong here (`[corpus-verify]` already flagged this).
   - `_ref_column`'s header regex (`...|id\b`) false-matches the *French* "IUD-ID de base" inside a Basic-UDI-DI multi-language header cell, so on `everX` it picks the constant Basic-UDI-DI column instead of the real "Article Code" column next to it (collapses to 1 deduped "code"). `New_Metal_Strips`'s header has no such French cell and already resolves to the right column today.
4. **IVOCLAR `Basic-UDI-DI 76152082ACERA008F6`** and **PLANMECA `...BASIC UDI-DI (GMN) 6430035420245R`** — confirmed via direct text search. Current `_UDI_LABEL` requires `\s*` (whitespace only, no hyphen) between "basic" and "udi", and no gap for a parenthetical. Neither is manufacturer-specific — both are generic label-format variance, and PLANMECA is not one of the five templated manufacturers, so this fix must live in the shared regex, not a template.
5. **DENSTPLY bare-`No.`** — `'No. G1 082649 0002 Rev. 00'` sits alone on its own line, value on the *same* line. Checked every committed fixture for false positives on a line-start `No.` pattern: the only other same-line hits are two fixtures whose cert_number the primary regex *already* extracts correctly (so the fallback never even fires there); the near-misses are PDF table-header cells (`"No.\nDescription"`, `"No.\nUKCA 795695"`) where the value is on the *next* line — excluded by requiring same-line whitespace (`[ \t]+`, not `\s+`) after `No.`.

## 1. Design decision — text-relative regions, not pixel/bbox regions `[FILLED — needs review]`

CLAUDE.md's phrase "anchor text + relative regions" is satisfied here as **text-relative**: an anchor is a line (or header-row) match, a region is "the lines/pages that follow it until a stop condition", never a PyMuPDF/pdfplumber coordinate. Reasons: (a) every defect above is fully solved by text/table-structure reasoning, none needs spatial coordinates; (b) the corpus is text-layer PDFs — the coordinate case (scanned certs) already routes to T2 vision per `pdf.is_scan`, so a bbox-region engine would serve zero real documents this phase; (c) coordinate regions are brittle across a manufacturer's own template revisions (font reflow shifts pixel offsets, not anchor text). If a future manufacturer genuinely needs coordinate regions, extend the schema then — not speculatively now (CLAUDE.md: don't design for hypothetical requirements).

## 2. Module layout

```
app/extract/t0_layout.py           # engine: load templates, match, dispatch ref_strategy
playbooks/                         # per-manufacturer JSON (identity fields + T0 parse section, S1.7)
  voco.json
  dentsply-sirona.json
  komet.json
  ivoclar.json
  gc.json
```

`app/extract/t0_templates.py` changes: (a) `_ref_column` gains the UDI-header exclusion preference (generic fix, not template-gated), (b) `_UDI_LABEL` broadened (generic), (c) `extract_cert_number` gains the same-line bare-`No.` fallback (generic), (d) `t0_extract` calls into `t0_layout.match(first_page_text)` to pick a template (or `None`) and, when matched, uses `template.ref_strategy` to choose which of `_ref_from_tables` / the new `_ref_from_text_columns` / the new stitched-table path to run instead of the current fixed table→text-fallback order; the `detect_external_ref_list` gate is removed as a *blocker* (kept only as a returned signal — see §5).

## 3. Template JSON schema (engine-agnostic — checked structurally in tests)

```json
{
  "manufacturer": "KOMET",
  "match": {"anchors": ["Komet Dental", "GEBR. BRASSELER"]},
  "ref_strategy": "text-column",
  "ref_strategy_config": {
    "header_anchors": ["ref", "basic udi-di"],
    "field_index": 2
  }
}
```

`ref_strategy ∈ {"table", "text-column", "stitched-table"}`.
- `"table"` (VOCO, DENSTPLY, GC): no behaviour change from the generic path — these three already pass today; their template exists to self-identify the manufacturer (S2.1's data contract, GAP C) and carries no `ref_strategy_config`. GC's two fixtures start passing not because of `ref_strategy` but because of the generic gate/column fixes in §0 point 3 — the template itself is inert for GC today, which is the honest state to record rather than inventing a distinct strategy value for a fix that isn't manufacturer-specific.
- `"text-column"` (KOMET): `header_anchors` (all must appear, case-insensitive, in one line) locates the header line; `field_index` (0-based, after `str.split(None, field_index+1)`) locates the REF field in each subsequent line. Stops at the first line that doesn't split into `field_index + 2` parts, or matches `^\d+/\d+$` (page-number footer — the KOMET fixture ends with `"1/1"`).
  - **Amended 2026-08-18** (floor corrected same day): a config gives `field_index` *or* `field_pattern`, never both. `field_pattern` is a regex the field must fully match — the first field on the line that matches wins — and it is what KOMET now carries, because its lists put the code at index 2 in one layout and index 1 in another under the same header. In pattern mode a non-matching line is skipped rather than ending the table, and reading continues onto pages that repeat no header. The pattern requires a size (`\d{1,3}`): a sizeless family stub (`589.204.`) is deliberately not a REF — 1.176 of them across the 186 KOMET PDFs match none of the 629 catalogue keys.
- `"stitched-table"` (IVOCLAR): no extra config needed — the header page/column is still found via the existing `_ref_column` header-regex match; the strategy only changes what happens *after*: continue onto each subsequent page's **first table**, treating all its rows as data (no header row to skip), gated by a configurable `density_threshold` (default 0.5) of `_looks_like_ref` hits on the carried column; stop at the first page whose first table fails the density check or which has no tables at all.

**No manufacturer template exists for PLANMECA** — its one fix (the `(GMN)` label variant) is carried by the generic `_UDI_LABEL` regex per §0 point 4, deliberately outside the five-brand template set the session scoped.

## 4. Function signatures

```python
# app/extract/t0_layout.py
@dataclass(frozen=True)
class Template:
    manufacturer: str
    anchors: tuple[str, ...]
    ref_strategy: str                 # "table" | "text-column" | "stitched-table"
    ref_strategy_config: dict

def load_templates(dir_path: Path | None = None) -> tuple[Template, ...]: ...
def match(first_page_text: str, templates: tuple[Template, ...] | None = None) -> Template | None: ...

def ref_from_text_columns(path, cfg: dict) -> tuple[list[str], int | None]: ...
def ref_from_stitched_tables(path) -> tuple[list[str], int | None]: ...
```

`ref_from_text_columns` takes `path` (not pre-read `page_texts`) for consistency with `ref_from_stitched_tables` — both open the PDF via pdfplumber themselves, matching the existing `_ref_from_tables`/`_ref_from_text` convention in `t0_templates.py`.

```python
# app/extract/t0_templates.py (modified)
def _ref_column(table: list[list]) -> int | None: ...  # UDI-exclusion preference is unconditional, not a kwarg
def extract_ref_list(path, *, template: Template | None = None) -> dict | None: ...
def t0_extract(doc, filename: str = "") -> tuple[str, dict]:  # signature unchanged, body updated
```

`load_templates` is called once at module import (mirrors how `t0_templates.py` builds its keyword tuples at import time) — a malformed JSON file is logged and skipped, never crashes the worker (matches the error taxonomy below).

## 5. Error taxonomy

| Condition | Behaviour |
|---|---|
| Template dir missing / empty | `load_templates` returns `()`; `t0_extract` behaves exactly as it does today (no template ever matches, falls through to the generic path). Never an error — templates are an enhancement, not a dependency. |
| Malformed JSON / unknown `ref_strategy` value | Logged at WARNING, that file skipped, the rest load normally. Never raises into the worker (a bad template must not dead-letter every extraction for that manufacturer). |
| `match()` finds >1 template whose anchors hit | First match in file-list (alphabetical) order wins; logged at DEBUG. Anchors are chosen distinctively enough in practice that this is a defensive fallback, not an expected path. |
| `match()` finds none | Returns `None` → generic path (today's behaviour), unchanged output. |
| `ref_strategy="text-column"` header anchors not found | Returns `(codes=[], page_hit=None)` like today's "no REF list found" — `t0_extract` treats it as absent, no crash. |
| `ref_strategy="stitched-table"` header page not found (e.g. a KOMET-anchored doc with GC's shape — cross-template misfire) | Same: empty result, no crash — density/header checks are the only gate, not manufacturer identity. |
| G2 (ISO/QMS never gets a REF list) | Unconditional — checked *before* any `ref_strategy` dispatch, exactly as today (`type_val in (None, "DoC")` guard stays the first check in `t0_extract`). |
| G5 (REF codes verbatim) | All three strategies return `list[str]`, no int coercion, no zero-stripping — asserted structurally in tests. |

## 6. Table-driven test cases

Against the committed fixture corpus (`tests/fixtures/corpus/`, `tests/fixtures/corpus_manifest.py`), extending `tests/test_t0_corpus.py`.

| # | Case | Assert |
|---|---|---|
| 1 | All 22 existing fixtures, engine enabled | No regression — every currently-passing `test_t0_field`/`test_t0_ref_list_floor` case still passes byte-identical |
| 2 | `match()` on each of the 5 templated manufacturers' fixtures | Returns the correct `Template` (by `manufacturer`) |
| 3 | `match()` on a non-templated manufacturer (e.g. STRAUMANN, 3SHAPE) | Returns `None`; generic path runs unchanged |
| 4 | Malformed template JSON in the templates dir (test fixture, temp dir) | `load_templates` skips it, logs, other templates still load |
| 5 | KOMET `533068_RA_812_Liste_DoC.pdf` — `ref_list` | **xfail → xpass.** ≥20 codes, all strings, verbatim (`K210L16.204.020` etc. unmodified) |
| 6 | KOMET same fixture — `coverage_scope` | Flips from `manufacturer` to `group` now that a REF list exists (manifest `t0` dict updated in this commit) |
| 7 | IVOCLAR `IPS e.max Ceram.pdf` — `ref_list` | **Manifest `t0_ref_min` raised 40 → 200** (was silently under-capturing, not previously xfail — this is a floor increase, not an xfail flip). Exactly 206 in practice; floor left with a small margin |
| 8 | IVOCLAR — page 8 (Signing Page) never contributes | Assert the returned codes exclude any value sourced from `Document Approval` table content |
| 9 | IVOCLAR — page 7 "Revision History" table never contributes | Assert no `"1.0"`/`"2.0"`-style revision-version strings appear in the result (this is the regression case the density-gate + first-table-only rule exists for) |
| 10 | GC `New_Metal_Strips_10102022_R.pdf` — `ref_list` + `coverage_scope` | New assertions (previously untested — `t0` dict had no `ref_list`/wrong `coverage_scope`): ≥15 codes, `coverage_scope` flips `manufacturer` → `group` |
| 11 | GC `everX_Posterior_12022026.pdf` — `ref_list` | New assertion: ≥1 code, and specifically **not** the constant Basic-UDI-DI value (`++J022MD0126K9`) — proves the UDI-exclusion column fix, not just the gate removal |
| 12 | `_ref_column` UDI-exclusion, unit-level | Direct call with a synthetic table `[["Basis-UDI-DI\nIUD-ID de base", "Article Code"], ...]` → returns index 1, not 0 |
| 13 | `_ref_column` regression | Existing VOCO fixture (inline table, header genuinely has no UDI column) still resolves the same column index as before |
| 14 | IVOCLAR `basic_udi_di` (`Basic-UDI-DI` hyphen variant) | **xfail → xpass**, value `76152082ACERA008F6`, tier T0, conf 1.0 |
| 15 | PLANMECA `basic_udi_di` (`(GMN)` parenthetical variant) | **xfail → xpass**, value `6430035420245R` |
| 16 | Regression: `_UDI_LABEL` broadening doesn't false-match | Run across all 22 fixtures' full text — no *new* `basic_udi_di` hit appears on a fixture whose ground truth is `None` |
| 17 | DENSTPLY bare-`No.` cert number | **xfail → xpass**, value `G1 082649 0002 Rev. 00` (leading `No. ` stripped, rest verbatim) |
| 18 | Bare-`No.` false-positive guard | Direct regex test: `"No.\nDescription"` and `"No.\nUKCA 795695"` (newline-separated) do **not** match; `"No. G1 082649 0002 Rev. 00"` (same-line) does |
| 19 | Regression: bare-`No.` fallback never overrides a primary-regex hit | The two fixtures whose cert_number the primary regex already resolves (`Ivoclar ISO certifikat...`, `LAB-UKCA-Certificate...`) are unchanged |
| 20 | G2 holds under all three strategies | A synthetic/existing ISO fixture matched against a template (if any were ISO-typed) still gets no `ref_list` regardless of `ref_strategy` — checked before dispatch |
| 21 | Template JSON has no PyMuPDF-specific keys | Structural test: every file under `playbooks/*.json` (read via `PLAYBOOKS_DIR`, S1.7) that carries a `ref_strategy` key — its parsed keys are a subset of `{manufacturer, match, ref_strategy, ref_strategy_config, aliases, bc_codes, domains, doc_sources}`, `ref_strategy` is one of the three closed values |

## 7. Manifest updates in the same commit

`tests/fixtures/corpus_manifest.py`: remove the 3 REF/UDI entries from `KNOWN_T0_GAPS` that flip to xpass (KOMET `ref_list`, IVOCLAR `basic_udi_di`, PLANMECA `basic_udi_di`) and the DENSTPLY `cert_number` entry; update their fixtures' `t0`/`ground_truth` dicts to the corrected extracted values; raise IVOCLAR e.max's `t0_ref_min` 40 → 200; add `ref_list`/`coverage_scope` expectations to both GC fixtures' `t0` dict (currently absent, not merely wrong).

## 8. Ratifications needed (non-blocking)

- Closes followup `2026-07-06 [extract-t0]` (KOMET text-column) and its multi-page-stitching sibling, and `2026-07-15 [corpus-verify]` (GC embedded attachment) — mark done in `tasks/followups.md` in the same commit as the code.
- `_ref_column`'s UDI-exclusion preference and the bare-`No.` same-line rule are generic parser corrections, not covered by any existing handbook §6 rule — no handbook edit needed (T0 extraction mechanics are implementation detail, not a contract surface).
