"""T0 layout-template engine (S1.5): anchor-based template selection +
per-manufacturer ref_strategy dispatch. Text-relative regions, not pixel/bbox
(docs/specs/t0-layout.md §1). Real fixture PDFs per CLAUDE.md.
"""

from __future__ import annotations

import json
import re

import pytest

from app.extract import pdf as pdfutil


# --------------------------------------------------------------------------- #
# load_templates / match — isolated temp-dir cases
# --------------------------------------------------------------------------- #
def _write_template(dir_path, filename, data):
    (dir_path / filename).write_text(json.dumps(data))


def test_load_templates_empty_dir_returns_empty(tmp_path):
    from app.extract.t0_layout import load_templates

    assert load_templates(tmp_path) == ()


def test_load_templates_missing_dir_returns_empty(tmp_path):
    from app.extract.t0_layout import load_templates

    assert load_templates(tmp_path / "does-not-exist") == ()


def test_load_templates_skips_malformed_json(tmp_path):
    from app.extract.t0_layout import load_templates

    _write_template(
        tmp_path,
        "good.json",
        {"manufacturer": "ACME", "match": {"anchors": ["Acme Corp"]}, "ref_strategy": "table"},
    )
    (tmp_path / "bad.json").write_text("{not valid json")

    templates = load_templates(tmp_path)

    assert len(templates) == 1
    assert templates[0].manufacturer == "ACME"


def test_match_finds_by_anchor(tmp_path):
    from app.extract.t0_layout import load_templates, match

    _write_template(
        tmp_path,
        "acme.json",
        {"manufacturer": "ACME", "match": {"anchors": ["Acme Corp"]}, "ref_strategy": "table"},
    )
    templates = load_templates(tmp_path)

    found = match("Declaration by Acme Corp, a widget maker", templates)

    assert found is not None
    assert found.manufacturer == "ACME"


def test_match_no_anchor_hit_returns_none(tmp_path):
    from app.extract.t0_layout import load_templates, match

    _write_template(
        tmp_path,
        "acme.json",
        {"manufacturer": "ACME", "match": {"anchors": ["Acme Corp"]}, "ref_strategy": "table"},
    )
    templates = load_templates(tmp_path)

    assert match("Some unrelated document text", templates) is None


def test_load_templates_carries_slug(tmp_path):
    from app.extract.t0_layout import load_templates

    _write_template(
        tmp_path,
        "acme-corp.json",
        {"manufacturer": "Acme Corp AG", "match": {"anchors": ["Acme"]},
         "ref_strategy": "table"},
    )

    (tpl,) = load_templates(tmp_path)

    assert tpl.slug == "acme-corp"


def test_load_templates_silently_skips_files_with_no_parse_section(tmp_path, caplog):
    """A URLs-only playbook is a normal, complete file -- not a malformed one.
    Warning on it would bury real authoring errors under ~15 lines of noise."""
    from app.extract.t0_layout import load_templates

    _write_template(
        tmp_path,
        "urls-only.json",
        {"manufacturer": "NSK", "domains": ["nsk-dental.com"]},
    )

    with caplog.at_level("WARNING"):
        assert load_templates(tmp_path) == ()

    assert caplog.records == []


def test_load_templates_still_warns_on_a_real_error(tmp_path, caplog):
    from app.extract.t0_layout import load_templates

    _write_template(
        tmp_path,
        "broken.json",
        {"manufacturer": "BAD", "match": {"anchors": ["x"]}, "ref_strategy": "nonsense"},
    )

    with caplog.at_level("WARNING"):
        assert load_templates(tmp_path) == ()

    assert any("malformed" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# real default templates dir — match on the 6 templated manufacturers
# --------------------------------------------------------------------------- #
def _first_page(relpath):
    doc = pdfutil.open_doc(f"tests/fixtures/corpus/{relpath}")
    try:
        return pdfutil.first_page_text(doc)
    finally:
        doc.close()


def test_match_voco_real_fixture():
    from app.extract.t0_layout import load_templates, match

    txt = _first_page("VOCO/VOCO_DoC_MDR_Ceramic Bond_2026-1-signed.pdf")
    found = match(txt, load_templates())
    assert found is not None and found.slug == "voco"


def test_match_denstply_real_fixture():
    from app.extract.t0_layout import load_templates, match

    txt = _first_page(
        "DENSTPLY/IMP - EC Certificate - MDD - Dentsply Implants "
        "Manufacturing GmbH - G1 082649 0002 - EN.pdf"
    )
    found = match(txt, load_templates())
    assert found is not None and found.slug == "dentsply-sirona"


def test_match_komet_real_fixture():
    from app.extract.t0_layout import load_templates, match

    txt = _first_page("KOMET/533068_RA_812_Liste_DoC.pdf")
    found = match(txt, load_templates())
    assert found is not None and found.slug == "komet"


def test_match_ivoclar_real_fixture():
    from app.extract.t0_layout import load_templates, match

    txt = _first_page("IVOCLAR/IPS e.max Ceram.pdf")
    found = match(txt, load_templates())
    assert found is not None and found.slug == "ivoclar"


def test_match_gc_real_fixture():
    from app.extract.t0_layout import load_templates, match

    txt = _first_page("GC/New_Metal_Strips_10102022_R.pdf")
    found = match(txt, load_templates())
    assert found is not None and found.slug == "gc"


def test_match_non_templated_manufacturer_returns_none():
    from app.extract.t0_layout import load_templates, match

    txt = _first_page("STRAUMANN/Izjava o skladnosti za vsadke TL SP.pdf")
    assert match(txt, load_templates()) is None


# --------------------------------------------------------------------------- #
# ref_from_text_columns — KOMET
# --------------------------------------------------------------------------- #
def test_ref_from_text_columns_komet(fixture_pdf):
    from app.extract.t0_layout import ref_from_text_columns

    path = fixture_pdf("KOMET/533068_RA_812_Liste_DoC.pdf")
    cfg = {"header_anchors": ["ref", "basic udi-di"], "field_index": 2}

    codes, page_hit = ref_from_text_columns(path, cfg)

    assert len(codes) == 20
    assert "K210L16.204.020" in codes
    assert page_hit == 1
    assert all(isinstance(c, str) for c in codes)


def test_match_neodent_real_fixture():
    from app.extract.t0_layout import load_templates, match

    txt = _first_page("NEODENT/neodent izjava o skaldnosti za T-baze.pdf")
    found = match(txt, load_templates())
    assert found is not None and found.slug == "neodent"


def test_neodent_template_reads_all_26_pages_of_the_t_bases_declaration(fixture_pdf):
    """`[neodent-ref-list-needs-t0]`. With no template this 26-page declaration
    fell through to the vision tier, which sees 4 pages and is asked for 150
    codes at most, and stored 137 of the 948 it lists, linking 38 catalogue
    items of the 279 it names. Read by the playbook's own template, every page
    of the `Item | Description | GMDN Code` table comes out; 947 is the count
    measured through this parser on 2026-09-11."""
    from app.extract.t0_layout import load_templates, ref_from_text_columns

    template = next(t for t in load_templates() if t.slug == "neodent")
    codes, page_hit = ref_from_text_columns(
        fixture_pdf("NEODENT/neodent izjava o skaldnosti za T-baze.pdf"),
        template.ref_strategy_config,
    )
    assert len(codes) == len(set(codes)) == 947
    assert page_hit == 1
    assert {"102.089", "135.413"} <= set(codes)


def test_field_pattern_reads_the_code_wherever_the_column_sits(fixture_pdf):
    """Komet prints its lists in at least three layouts and the code does not
    sit in the same column in all of them: index 2 in
    '004081K3 K3 368.204.023 DIAM ...', index 1 in
    '001627 1.204.005 STEEL BUR ...' -- under the same column header. Reading
    by shape rather than by position is what makes both work; measured over the
    186 Komet corpus PDFs on 2026-08-18, positional reading matched 35 catalogue
    items and shape reading matched 174."""
    from app.extract.t0_layout import ref_from_text_columns

    path = fixture_pdf("KOMET/533068_RA_812_Liste_DoC.pdf")
    cfg = {"header_anchors": ["description", "umdns"],
           "field_pattern": r"[A-Z0-9]{1,10}\.\d{3}\.\d{1,3}"}

    codes, page_hit = ref_from_text_columns(path, cfg)

    assert "K210L16.204.020" in codes
    assert page_hit == 1
    assert all(re.fullmatch(r"[A-Z0-9]{1,10}\.\d{3}\.\d{1,3}", c) for c in codes)


def test_field_pattern_never_returns_a_packing_group_or_a_product_name(fixture_pdf):
    """The guarantee positional reading cannot make. A Komet row carries a
    packing group ('K3', 'R0', 'S9') and a product name ('STEEL BUR') beside the
    article code, and both have been read as the code before now -- positional
    reading returned product names out of 121 of the 186 files."""
    from app.extract.t0_layout import ref_from_text_columns

    path = fixture_pdf("KOMET/533068_RA_812_Liste_DoC.pdf")
    cfg = {"header_anchors": ["description", "umdns"],
           "field_pattern": r"[A-Z0-9]{1,10}\.\d{3}\.\d{1,3}"}

    codes, _ = ref_from_text_columns(path, cfg)

    assert codes, "fixture must yield codes or the assertion below proves nothing"
    for junk in ("K3", "R0", "S9", "K2", "STEEL", "BUR", "DIAM"):
        assert junk not in codes


def test_field_pattern_keeps_reading_after_the_header_page(fixture_pdf):
    """A Komet list runs to dozens of pages and repeats neither header nor
    title, so a per-page header requirement threw the continuation pages away:
    39 of the 41 catalogue codes in '532861_RA_812_Liste_DoC.pdf' were on pages
    2-17. `page` stays the FIRST page a code came from -- that is where a
    reviewer should look."""
    from app.extract.t0_layout import ref_from_text_columns
    import pdfplumber

    path = fixture_pdf("KOMET/533068_RA_812_Liste_DoC.pdf")
    cfg = {"header_anchors": ["description", "umdns"],
           "field_pattern": r"[A-Z0-9]{1,10}\.\d{3}\.\d{1,3}"}
    with pdfplumber.open(path) as pl:
        pages = len(pl.pages)

    codes, page_hit = ref_from_text_columns(path, cfg)

    assert page_hit == 1
    if pages > 1:
        first_only = {"header_anchors": cfg["header_anchors"], "field_index": 2}
        assert len(codes) >= len(ref_from_text_columns(path, first_only)[0])


def test_the_komet_pattern_leaves_out_a_sizeless_code(fixture_pdf):
    """One of this fixture's 20 rows prints its article code with the size
    column blank -- '589.204.', a BOHRERSCHAFTVERLAENGERUNG -- and the playbook
    pattern deliberately does not take it, which is why the manifest floor is
    19 and not 20.

    Measured 2026-08-18 over the 186 Komet corpus PDFs: allowing an empty size
    (`\\d{0,3}`) admits 1.176 distinct sizeless codes over 2.521 occurrences in
    38 files, +23% on ref_list (11.146 -> 13.667). Against the 629 Komet
    catalogue keys, both spellings folded, **none** of them matches one exactly
    and three are merely a PREFIX of one -- so they link nothing today, and a
    prefix rule built on them would fan a single code out over a whole family's
    sized siblings, which is the false link invariant 3 exists to prevent.

    Read from the playbook, not from a literal: widening the pattern in
    `playbooks/komet.json` without revisiting that measurement fails here."""
    from app.extract.t0_layout import load_templates, ref_from_text_columns

    komet = next(t for t in load_templates() if t.manufacturer.upper() == "KOMET")
    path = fixture_pdf("KOMET/533068_RA_812_Liste_DoC.pdf")

    codes, _ = ref_from_text_columns(path, komet.ref_strategy_config)

    assert "589.204." not in codes
    assert "K210L16.204.020" in codes, "the sized codes on the same page are kept"
    # The widened pattern is what WOULD take it -- proving the omission is the
    # pattern's doing and not a reader that never saw the line at all.
    wide = dict(komet.ref_strategy_config,
                field_pattern=r"[A-Z0-9]{1,10}\.\d{3}\.\d{0,3}")
    assert "589.204." in ref_from_text_columns(path, wide)[0]


def test_a_template_must_choose_exactly_one_way_to_find_the_field(fixture_pdf):
    """Both or neither is an authoring mistake, and a silent default would pick
    the wrong column rather than say so."""
    from app.extract.t0_layout import ref_from_text_columns

    path = fixture_pdf("KOMET/533068_RA_812_Liste_DoC.pdf")
    for cfg in ({"header_anchors": ["description"], "field_index": 2,
                 "field_pattern": r"\d+"},
                {"header_anchors": ["description"]}):
        with pytest.raises(ValueError, match="exactly one"):
            ref_from_text_columns(path, cfg)


def test_ref_from_text_columns_no_header_match_returns_empty(fixture_pdf):
    from app.extract.t0_layout import ref_from_text_columns

    # A doc with no such header anchors at all.
    path = fixture_pdf("VOCO/VOCO_DoC_MDR_Ceramic Bond_2026-1-signed.pdf")
    cfg = {"header_anchors": ["ref", "basic udi-di"], "field_index": 2}

    codes, page_hit = ref_from_text_columns(path, cfg)

    assert codes == []
    assert page_hit is None


# --------------------------------------------------------------------------- #
# ref_from_text_columns / ref_from_stitched_tables — ref_pattern (task 5)
# --------------------------------------------------------------------------- #
def test_ref_from_text_columns_ref_pattern_narrows_field_pattern_mode(fixture_pdf):
    """A playbook's `ref_pattern` narrows what `field_pattern` already selected
    -- it does not replace it. komet.json's own field_pattern already returns a
    mix of prefixes; a stricter authored shape keeps only a subset."""
    from app.extract.t0_layout import ref_from_text_columns

    path = fixture_pdf("KOMET/533068_RA_812_Liste_DoC.pdf")
    cfg = {"header_anchors": ["description", "umdns"],
           "field_pattern": r"[A-Z0-9]{1,10}\.\d{3}\.\d{1,3}"}

    baseline, _ = ref_from_text_columns(path, cfg)
    narrowed, page_hit = ref_from_text_columns(path, cfg, ref_pattern=r"^K")

    assert narrowed, "fixture must yield at least one K-prefixed code or this proves nothing"
    assert all(c.startswith("K") for c in narrowed)
    assert len(narrowed) < len(baseline)
    assert set(narrowed) <= set(baseline)   # narrows, never adds a code baseline didn't have
    assert page_hit == 1


def test_ref_from_text_columns_no_pattern_matches_field_pattern_mode_baseline(fixture_pdf):
    """Regression guard for the threading itself: passing `ref_pattern=None`
    explicitly must be indistinguishable from the pre-task-5 call."""
    from app.extract.t0_layout import ref_from_text_columns

    path = fixture_pdf("KOMET/533068_RA_812_Liste_DoC.pdf")
    cfg = {"header_anchors": ["description", "umdns"],
           "field_pattern": r"[A-Z0-9]{1,10}\.\d{3}\.\d{1,3}"}

    assert ref_from_text_columns(path, cfg) == ref_from_text_columns(path, cfg, ref_pattern=None)


def test_ref_from_text_columns_ref_pattern_alone_keeps_what_looks_like_ref_would_reject(tmp_path):
    """The decisive case from the S1.7 review (2026-08-19): a candidate that
    matches BOTH `field_pattern` (the column selector) and the authored
    `ref_pattern`, but would fail one of `_looks_like_ref`'s own generic tests
    (here: a 9-letter lowercase run reads as prose), must be KEPT. This
    strategy applies the authored `ref_pattern` alone -- unlike
    `_ref_from_tables` and `ref_from_stitched_tables`, it never ran
    `_looks_like_ref`'s generic tests before `ref_pattern` existed, so
    composing them in now would silently drop codes for a reason invisible
    from the playbook's own pattern."""
    import pymupdf
    from app.extract.t0_layout import ref_from_text_columns
    from app.extract.t0_templates import _looks_like_ref

    path = tmp_path / "synthetic.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    lines = ["Description UMDNS Code", "widget someprose1 16669"]
    page.insert_text((20, 40), "\n".join(lines), fontsize=9)
    doc.save(str(path))
    doc.close()

    cfg = {"header_anchors": ["description", "umdns"], "field_pattern": r"[a-z]+\d"}

    assert not _looks_like_ref("someprose1"), \
        "fixture must be a value the generic gate rejects or this proves nothing"

    codes, _ = ref_from_text_columns(path, cfg, ref_pattern=r"^some")

    assert "someprose1" in codes


def test_ref_from_text_columns_ref_pattern_narrows_positional_mode_too(fixture_pdf):
    """The field_index (positional) branch has no shape check of its own today
    -- `ref_pattern` is the only narrowing available there, so it must reach it
    exactly as it reaches the field_pattern branch above."""
    from app.extract.t0_layout import ref_from_text_columns

    path = fixture_pdf("KOMET/533068_RA_812_Liste_DoC.pdf")
    cfg = {"header_anchors": ["ref", "basic udi-di"], "field_index": 2}

    baseline, _ = ref_from_text_columns(path, cfg)
    narrowed, _ = ref_from_text_columns(path, cfg, ref_pattern=r"^K")

    assert narrowed, "fixture must yield at least one K-prefixed code or this proves nothing"
    assert all(c.startswith("K") for c in narrowed)
    assert len(narrowed) < len(baseline)


def test_ref_from_text_columns_ref_pattern_skips_a_row_without_ending_the_table(fixture_pdf):
    """A row whose positional field fails `ref_pattern` is one excluded code,
    not the end of the table: further, later rows that DO match must still come
    through. (Distinct from a row that fails to PARSE at all -- that still ends
    the run, unchanged from before this task.)"""
    from app.extract.t0_layout import ref_from_text_columns

    path = fixture_pdf("KOMET/533068_RA_812_Liste_DoC.pdf")
    cfg = {"header_anchors": ["ref", "basic udi-di"], "field_index": 2}

    baseline, _ = ref_from_text_columns(path, cfg)
    # Excludes the very first code (whichever it is) by shape, keeps the rest.
    narrowed, _ = ref_from_text_columns(path, cfg, ref_pattern=rf"^(?!{re.escape(baseline[0])}$).+$")

    assert baseline[0] not in narrowed
    assert len(narrowed) == len(baseline) - 1
    assert set(narrowed) == set(baseline) - {baseline[0]}


# --------------------------------------------------------------------------- #
# ref_from_stitched_tables — IVOCLAR e.max
# --------------------------------------------------------------------------- #
def test_ref_from_stitched_tables_emax(fixture_pdf):
    from app.extract.t0_layout import ref_from_stitched_tables

    path = fixture_pdf("IVOCLAR/IPS e.max Ceram.pdf")

    codes, page_hit = ref_from_stitched_tables(path)

    assert len(codes) == 206
    assert page_hit == 3
    # Revision History table (page 7, second table) never contributes.
    assert "1.0" not in codes
    assert "2.0" not in codes
    # Signing Page (page 8) never contributes — density gate stops stitching.
    assert "596839" in codes  # first real code, page 3
    assert "762718EN" in codes  # last real code, page 7


def test_ref_from_stitched_tables_ref_pattern_narrows(fixture_pdf):
    """`762718EN` carries a market suffix; an authored shape requiring pure
    digits keeps `596839` and drops it, without needing a T0 layout template
    for the pattern itself to take effect."""
    from app.extract.t0_layout import ref_from_stitched_tables

    path = fixture_pdf("IVOCLAR/IPS e.max Ceram.pdf")

    baseline, _ = ref_from_stitched_tables(path)
    narrowed, page_hit = ref_from_stitched_tables(path, ref_pattern=r"^\d+$")

    assert "596839" in narrowed
    assert "762718EN" not in narrowed
    assert len(narrowed) < len(baseline)
    assert set(narrowed) <= set(baseline)
    assert page_hit == 3


def test_ref_from_stitched_tables_no_pattern_matches_baseline(fixture_pdf):
    """Regression guard: `ref_pattern=None` (the default, and every call before
    this task) must be indistinguishable from omitting the argument."""
    from app.extract.t0_layout import ref_from_stitched_tables

    path = fixture_pdf("IVOCLAR/IPS e.max Ceram.pdf")

    assert ref_from_stitched_tables(path) == ref_from_stitched_tables(path, ref_pattern=None)


def test_ref_from_stitched_tables_no_header_returns_empty(fixture_pdf):
    from app.extract.t0_layout import ref_from_stitched_tables

    path = fixture_pdf("KOMET/533068_RA_812_Liste_DoC.pdf")  # no pdfplumber table at all

    codes, page_hit = ref_from_stitched_tables(path)

    assert codes == []
    assert page_hit is None


# --------------------------------------------------------------------------- #
# ref_strategy — GC's article table spans pages, and the continuation repeats
# no header, so the per-page "table" strategy stopped at the first page and
# dropped the rest of the list silently.
# --------------------------------------------------------------------------- #
def test_gc_multipage_article_table_is_stitched(fixture_pdf):
    from app.extract.t0_layout import load_templates, match
    from app.extract.t0_templates import extract_ref_list

    path = fixture_pdf("GC/Unifast_III_23042025.pdf")
    template = match(_first_page("GC/Unifast_III_23042025.pdf"), load_templates())
    assert template is not None and template.slug == "gc"
    assert template.ref_strategy == "stitched-table"

    ev = extract_ref_list(path, template=template)
    codes = ev["value"]

    assert "003477" in codes   # page 5, the only code the per-page strategy found
    assert "003489" in codes   # page 6 continuation — a real catalogue item, lost today
    assert len(codes) >= 50    # 55 six-digit codes live in this file


def test_stitched_skips_a_headerish_table_that_yields_no_codes(fixture_pdf):
    """Fuji Coat LC carries a 1-row table on page 3 whose single header cell
    reads as a REF column. Latching onto it makes page 4 (no tables at all) end
    the scan before page 5, where the real 20-row article table lives — so the
    document returned zero codes and lost catalogue item 000176. A header
    candidate that yields no codes is not the article table."""
    from app.extract.t0_layout import ref_from_stitched_tables

    codes, page_hit = ref_from_stitched_tables(fixture_pdf("GC/Fuji_Coat_LC_12022026.pdf"))

    assert "000176" in codes
    assert "004856" in codes
    assert page_hit == 5


# --------------------------------------------------------------------------- #
# template JSON stays engine-agnostic (no PyMuPDF-specific keys)
# --------------------------------------------------------------------------- #
def test_template_json_files_are_engine_agnostic():
    """Playbooks now carry identity/URL fields alongside the T0 parse section
    (S1.7), and not every playbook has a parse section at all (see
    test_load_templates_silently_skips_files_with_no_parse_section). This only
    checks the parse-bearing subset -- the fields `app.playbooks` owns are out
    of scope here."""
    import pathlib

    from app.extract.t0_layout import REF_STRATEGIES
    from app.playbooks import PLAYBOOKS_DIR

    allowed = {
        "manufacturer", "match", "ref_strategy", "ref_strategy_config",
        "aliases", "bc_codes", "domains", "doc_sources",
        # ref_normalize (backfill-matching plan, task 3, Part B) is a
        # comparison-time rule owned by app/playbooks.py, not a parsing key --
        # same category as aliases/bc_codes/domains/doc_sources above.
        "ref_normalize",
        # companion (2026-08-18): which files are an annex to another file's
        # document. Read by BACKFILL, owned by app/playbooks.py -- same
        # category as ref_normalize, not a parsing key.
        "companion",
        # coverage_map (2026-08-18): the manufacturer's own article -> document
        # index. Read by the komet-coverage CLI, owned by app/playbooks.py --
        # same category as ref_normalize and companion, not a parsing key.
        "coverage_map",
        # date_labels (2026-08-21, komet is the first author): extra date-label
        # spellings merged into T0's global EN/DE/SL list. A parsing key, and
        # engine-agnostic by construction -- it is label text, not a coordinate
        # or a PyMuPDF call.
        "date_labels",
        # rev: which revision of the authored rules shaped a read, recorded on
        # extraction_attempt (migration 031). Provenance, not a parsing key.
        "rev",
        # contacts (2026-09-03): role mailboxes for renewal requests, seeded
        # into manufacturer.contact_emails. Identity, not parsing -- it never
        # reaches the extractor at all, and it is plain address text, so it is
        # engine-agnostic by construction.
        "contacts",
        # skip_backfill (2026-08-19, NEODENT): a client ruling that one
        # manufacturer's SFTP folder is not to be scanned. Read by BACKFILL,
        # owned by app/playbooks.py, free text -- same category as companion.
        # Joined this list when NEODENT gained a parse section (2026-09-11).
        "skip_backfill",
        # The other S1.7 extraction keys -- cert_number_pattern, ref_pattern,
        # extract_hints -- are deliberately NOT listed yet: no playbook authors
        # one. Add each here when it is first authored, having checked it is
        # engine-agnostic (a regex or free text, never an engine call), which is
        # the whole point of this guard.
    }
    all_data = {f: json.loads(f.read_text()) for f in sorted(PLAYBOOKS_DIR.glob("*.json"))}
    parse_files = {f: d for f, d in all_data.items() if "ref_strategy" in d}
    # 6 since 2026-09-11: NEODENT's text-column template ([neodent-ref-list-needs-t0]).
    assert len(parse_files) == 6

    for f, data in parse_files.items():
        assert set(data) <= allowed, f"{f}: unexpected keys {set(data) - allowed}"
        assert data["ref_strategy"] in REF_STRATEGIES
        assert set(data["match"]) <= {"anchors"}
