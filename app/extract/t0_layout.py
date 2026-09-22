"""T0 layout-template engine (S1.5). Anchor text + text-relative regions —
never pixel/bbox coordinates (docs/specs/t0-layout.md §1: every real defect
this closes is solvable by text/table-structure reasoning; the corpus is
text-layer PDFs, scans already route to T2 vision via `pdf.is_scan`).

Templates are engine-agnostic data (S1.7: one home for authored
per-manufacturer config; schema: `manufacturer`, `match.anchors`,
`ref_strategy`, `ref_strategy_config`), read through `app.playbooks.load_raw`
rather than globbed here -- so they come from the same store the rest of the
playbook does, files or rows. A playbook with no parse section is a
complete, valid file (identity/URL data only) and is silently skipped -- not
every manufacturer has a T0 template. A malformed parse section is logged and
skipped — never
crashes the worker (templates are an enhancement, not a dependency: an empty
template set degrades to T0's pre-existing generic path).
"""

from __future__ import annotations

import logging
import pathlib
import re
from dataclasses import dataclass, field

import pdfplumber

from app.extract import pdf as pdfutil
from app import playbooks

log = logging.getLogger("dentalia.extract.t0_layout")

REF_STRATEGIES = ("table", "text-column", "stitched-table")

_PAGE_FOOTER = re.compile(r"^\d+/\d+$")


@dataclass(frozen=True)
class Template:
    slug: str
    manufacturer: str
    anchors: tuple[str, ...]
    ref_strategy: str
    ref_strategy_config: dict = field(default_factory=dict)


def load_templates(dir_path: pathlib.Path | None = None) -> tuple[Template, ...]:
    """The parse templates, projected off whichever store `app.playbooks` is
    reading.

    This used to be a SECOND loader with its own glob, its own `json.loads` and
    its own cache-less re-read per call -- two views of one directory that could
    disagree about which files existed. It is now one `load_raw` call, so the
    store swap happened in one place and this followed for free.

    `dir_path` is passed straight through and keeps its old meaning (read that
    directory), which is what leaves the existing template tests unchanged.
    """
    raw = playbooks.load_raw(dir_path)

    templates = []
    for slug, data in sorted(raw.items()):
        # A playbook with no parse section is a complete, valid file (it may
        # carry only identity and URLs). Skipping it is normal, not an error --
        # warning here would bury real authoring mistakes in noise.
        if "ref_strategy" not in data and "match" not in data:
            continue

        try:
            strategy = data["ref_strategy"]
            if strategy not in REF_STRATEGIES:
                raise ValueError(f"unknown ref_strategy {strategy!r}")
            templates.append(
                Template(
                    slug=slug,
                    manufacturer=data["manufacturer"],
                    anchors=tuple(data["match"]["anchors"]),
                    ref_strategy=strategy,
                    ref_strategy_config=data.get("ref_strategy_config", {}),
                )
            )
        except Exception:
            log.warning("t0_layout: skipping malformed template %s", slug, exc_info=True)
    return tuple(templates)


def match(first_page_text: str, templates: tuple[Template, ...] | None = None) -> Template | None:
    if templates is None:
        templates = load_templates()
    low = first_page_text.lower()
    for t in templates:
        if any(a.lower() in low for a in t.anchors):
            return t
    return None


# --------------------------------------------------------------------------- #
# ref_strategy: "text-column" (KOMET)
# --------------------------------------------------------------------------- #
def ref_from_text_columns(
    path, cfg: dict, ref_pattern: str | None = None
) -> tuple[list[str], int | None]:
    """Locate a header line matching every `header_anchors` term, then read one
    field out of each following line. Stops at the first line that yields
    nothing, or a bare page-number footer (e.g. "1/1").

    `ref_pattern` is a playbook's authored REF-code shape (task 5, distinct from
    `cfg["field_pattern"]` above, which selects the COLUMN). A candidate that
    fails it is dropped from the result but never ends the table scan the way a
    genuinely unparseable line does -- narrowing which codes come out must not
    also throw away every code after the first one it excludes.

    Applied as a bare `re.search`, NOT routed through `_looks_like_ref` the way
    `_ref_from_tables` and `ref_from_stitched_tables` route it (S1.7 review,
    2026-08-19): those two strategies already ran `_looks_like_ref`'s generic
    tests (digit present, <=30 chars, no prose word, no UDI prefix) before
    `ref_pattern` existed, so adding the pattern to them is pure narrowing on
    top of an unchanged gate. This strategy never ran those tests at all --
    composing them in now would be a hidden behaviour change toggled by an
    unrelated authoring choice: a manufacturer whose `field_pattern`
    legitimately selects a value over 30 chars, or containing a lowercase run,
    would silently lose codes for a reason invisible from their own pattern.
    Here `ref_pattern` narrows by the authored shape alone.

    Two ways to say WHICH field, and a template must give exactly one:

    `field_index` — positional, `str.split(None, n)[i]`. Correct only where
    every row of every document in that manufacturer's corpus puts the code in
    the same column.

    `field_pattern` — a regex the field must fully match; the first field on the
    line that matches wins. Authored for Komet, where positional reading is
    provably wrong: its list documents come in at least three layouts and the
    code sits at index 2 in one ("004081K3 K3 368.204.023 DIAM ...") and index 1
    in another ("001627 1.204.005 STEEL BUR ..."), under the same column header.
    Measured 2026-08-18: index 2 read refs out of 65 of 186 files and silently
    read PRODUCT NAMES ("STEEL BUR") out of the rest.

    The pattern also makes one guarantee positional reading cannot: a packing
    group (`K3`, `R0`, `S9`) can never be mistaken for an article code, because
    it does not have an article code's shape."""
    header_anchors = [a.lower() for a in cfg["header_anchors"]]
    field_index = cfg.get("field_index")
    pattern = cfg.get("field_pattern")
    if (field_index is None) == (pattern is None):
        raise ValueError("ref_strategy_config needs exactly one of "
                         "field_index / field_pattern")
    field_re = re.compile(pattern) if pattern else None
    ref_re = re.compile(ref_pattern) if ref_pattern else None

    all_codes: list[str] = []
    first_page: int | None = None
    header_seen = False
    with pdfplumber.open(pdfutil.resolve_local(str(path))) as pl:
        for pnum, page in enumerate(pl.pages, start=1):
            text = page.extract_text() or ""
            lines = text.splitlines()
            start = None
            for i, ln in enumerate(lines):
                low = ln.lower()
                if all(a in low for a in header_anchors):
                    start = i + 1
                    break
            if start is None:
                if field_re is not None and header_seen:
                    start = 0          # continuation page: no header, all data
                else:
                    continue

            # Pattern mode: once the header has been seen, every following
            # page is a continuation. Komet repeats neither header nor title on
            # page 2 of an 18-page list, so a per-page header requirement threw
            # 39 of 41 catalogue codes away on `532861_RA_812_Liste_DoC.pdf`
            # alone (measured 2026-08-18). A line that matches nothing is
            # skipped rather than ending the table, which is safe here in a way
            # it is not for positional reading: the pattern is the article
            # code's own shape, so a page header, a footer or a signature block
            # simply does not match.
            if field_re is not None:
                for ln in lines[start:]:
                    hit = next((f for f in ln.split() if field_re.fullmatch(f)), None)
                    if hit is not None and (ref_re is None or ref_re.search(hit)):
                        all_codes.append(hit)
                        if first_page is None:
                            first_page = pnum
                header_seen = True
                continue

            # A wrapped header cell (e.g. "REF / Product\ngroup name") can spill
            # onto its own line after the anchor line — not data, but not a
            # stop condition either since real data hasn't started yet. Any
            # non-parsing line BEFORE the first real row is skipped; once
            # collection has started, the same failure means end-of-table.
            codes: list[str] = []
            started = False
            for ln in lines[start:]:
                ln = ln.strip()
                if not ln or _PAGE_FOOTER.match(ln):
                    if started:
                        break
                    continue
                if field_re is not None:
                    hit = next((f for f in ln.split() if field_re.fullmatch(f)), None)
                    if hit is None:
                        if started:
                            break
                        continue
                    codes.append(hit)
                    started = True
                    continue
                parts = ln.split(None, field_index + 1)
                if len(parts) < field_index + 2:
                    if started:
                        break
                    continue
                started = True
                candidate = parts[field_index]
                if ref_re is None or ref_re.search(candidate):
                    codes.append(candidate)
            # Every page, not just the first that hits. A Komet list runs to
            # dozens of pages under one repeated header, and returning at the
            # first page threw the rest away: measured 2026-08-18 over the 186
            # Komet PDFs, page-one-only found 35 catalogue items where the full
            # walk finds 98. `page` on the evidence stays the FIRST page a code
            # came from, which is where a reviewer should look.
            if codes:
                all_codes.extend(codes)
                if first_page is None:
                    first_page = pnum
    return all_codes, first_page


# --------------------------------------------------------------------------- #
# ref_strategy: "stitched-table" (IVOCLAR e.max)
# --------------------------------------------------------------------------- #
def ref_from_stitched_tables(
    path, *, density_threshold: float = 0.5, ref_pattern: str | None = None
) -> tuple[list[str], int | None]:
    """Pick the REF column with `_ref_column` — the SAME chooser the default
    table strategy uses — then continue onto each subsequent page's FIRST table
    only (headerless continuation — pdfplumber's row 0 is data), gated by a
    per-page density check on the carried column. Stops at the first page whose
    first table fails the density check or which has no tables.

    Sharing `_ref_column` rather than re-scanning the header here is load
    bearing: it carries the preference for a column whose header does NOT also
    read as Basic UDI-DI. A local first-`_REF_HEADER`-match scan picked GC's
    Basic-UDI-DI column instead of its article column, yielding one code
    repeated on every row (`++J022MD0102JT` x56) and zero real REFs. Same class
    of bug as this module's earlier local `_looks_like_ref` redefinition.

    `ref_pattern` (task 5) is a playbook's authored REF-code shape, forwarded to
    every `_looks_like_ref` call below -- both the header-column discovery pass
    and the per-page density check narrow the same way."""
    # Deferred import: t0_templates imports this module at load time, so a
    # module-level import here would be circular.
    from app.extract.t0_templates import _looks_like_ref, _ref_column

    with pdfplumber.open(pdfutil.resolve_local(str(path))) as pl:
        header_col: int | None = None
        header_page: int | None = None
        codes: list[str] = []

        for pnum, page in enumerate(pl.pages, start=1):
            tables = page.extract_tables()

            if header_col is None:
                for table in tables:
                    idx = _ref_column(table)
                    if idx is None:
                        continue
                    found = [
                        str(row[idx]).strip()
                        for row in table[1:]
                        if idx < len(row) and row[idx]
                        and _looks_like_ref(str(row[idx]).strip(), ref_pattern)
                    ]
                    # A header-looking table that yields NO codes is not the
                    # article table: GC's Fuji Coat LC has a 1-row table on
                    # page 3 whose only cell reads as a REF column, and
                    # latching onto it let page 4 (no tables) end the scan
                    # before page 5, where the real 20-row table lives.
                    if not found:
                        continue
                    header_col = idx
                    header_page = pnum
                    codes.extend(found)
                    break
                continue

            if not tables:
                break
            table = tables[0]
            vals = [
                str(row[header_col]).strip()
                for row in table
                if header_col < len(row) and row[header_col]
            ]
            if not vals:
                break
            matched = [v for v in vals if _looks_like_ref(v, ref_pattern)]
            if len(matched) / len(vals) < density_threshold:
                break
            codes.extend(matched)

        if not codes:
            return [], None
        return codes, header_page
