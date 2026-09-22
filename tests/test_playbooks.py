"""Playbook store (S1.7): the authored per-manufacturer config in `playbooks/`.

`load_playbooks` is the runtime path and never raises; `validate` is the
operator path and raises loudly on cross-file conflicts.
"""

from __future__ import annotations

import json

import pytest

from app import playbooks


def _write(dir_path, filename, data):
    (dir_path / filename).write_text(json.dumps(data))



def test_a_playbook_still_loads_when_bc_codes_carry_a_catalogue_key(tmp_path):
    """The 33 authored playbooks all carry `catalogue` and are deliberately not
    being rewritten (Denis 2026-08-26: make the key optional, ignore it). The
    parser must accept and discard it, or every delivered file fails to load."""
    _write(tmp_path, "x.json", {"manufacturer": "X AG",
                                "bc_codes": [{"catalogue": "LJ", "code": "001"}]})
    pb = playbooks.load_playbooks(tmp_path)[0]
    assert pb.bc_codes == (playbooks.BcCode("001"),)
    assert not hasattr(pb.bc_codes[0], "catalogue")


def test_a_playbook_loads_with_no_catalogue_key_at_all(tmp_path):
    """Nothing authored from here on needs to carry it."""
    _write(tmp_path, "y.json", {"manufacturer": "Y AG", "bc_codes": [{"code": "002"}]})
    assert playbooks.load_playbooks(tmp_path)[0].bc_codes == (playbooks.BcCode("002"),)


def test_one_code_claimed_by_two_playbooks_is_a_conflict(tmp_path):
    """The conflict key was `(catalogue, code)`, so two files could claim `001`
    under different catalogue tags and pass validation. There is one catalogue,
    so that is one contested code. Verified against the delivered set before
    changing it: no code is claimed by more than one of the 33 files under
    either key, so this narrows the rule without re-judging anything real."""
    _write(tmp_path, "a.json", {"manufacturer": "A AG",
                                "bc_codes": [{"catalogue": "LJ", "code": "001"}]})
    _write(tmp_path, "b.json", {"manufacturer": "B AG",
                                "bc_codes": [{"catalogue": "HR", "code": "001"}]})
    with pytest.raises(playbooks.PlaybookConflict, match="001"):
        playbooks.validate(playbooks.load_playbooks(tmp_path))


VOCO = {
    "manufacturer": "VOCO GmbH",
    "bc_codes": [{"catalogue": "LJ", "code": "062"}],
    "aliases": ["VOCO"],
    "domains": ["voco.dental"],
    "doc_sources": [
        {"doc_type": "IFU", "kind": "portal", "url": "https://www.voco.dental/downloads"}
    ],
}


def test_load_empty_dir_returns_empty(tmp_path):
    assert playbooks.load_playbooks(tmp_path) == ()


def test_load_missing_dir_returns_empty(tmp_path):
    assert playbooks.load_playbooks(tmp_path / "nope") == ()


def test_load_parses_all_fields(tmp_path):
    _write(tmp_path, "voco.json", VOCO)

    (pb,) = playbooks.load_playbooks(tmp_path)

    assert pb.slug == "voco"
    assert pb.manufacturer == "VOCO GmbH"
    assert pb.bc_codes[0].code == "062"
    assert pb.aliases == ("VOCO",)
    assert pb.domains == ("voco.dental",)
    assert pb.doc_sources[0].kind == "portal"
    assert pb.doc_sources[0].doc_type == "IFU"


def test_doc_type_is_carried_verbatim_in_every_casing(tmp_path):
    """`doc_type` is free text, loaded verbatim, and no consumer filters on it
    today (`_prefilled_search_links` branches on `kind`). The authored files use
    the `document.type` spellings -- `DoC`, `IFU` -- but nothing enforces that,
    so a `doc` written by hand loads just as happily and the two would not match
    each other. Pinned rather than normalized on load: when the S2.1 crawl recipe
    makes `doc_type` load-bearing it has to casefold at the point of comparison,
    and this test is what tells it these are the shapes it will meet
    (followup `[playbooks-doc-type-casing]`).
    """
    _write(tmp_path, "a.json", dict(VOCO, bc_codes=[], aliases=[], doc_sources=[
        {"doc_type": "DoC", "kind": "portal", "url": "https://a.example/x"},
        {"doc_type": "doc", "kind": "direct", "url": "https://a.example/y.pdf"},
        {"doc_type": "IFU", "kind": "direct", "url": "https://a.example/z.pdf"},
    ]))

    (pb,) = playbooks.load_playbooks(tmp_path)

    assert [s.doc_type for s in pb.doc_sources] == ["DoC", "doc", "IFU"]


def test_domain_normalizes_url_and_bare_hostname_identically(tmp_path):
    _write(tmp_path, "a.json", dict(VOCO, bc_codes=[], aliases=[], domains=["https://voco.dental/"]))
    _write(tmp_path, "b.json", dict(VOCO, manufacturer="Other GmbH", bc_codes=[], aliases=[],
                                     domains=["voco.dental"]))

    loaded = {pb.slug: pb for pb in playbooks.load_playbooks(tmp_path)}

    assert loaded["a"].domains == ("voco.dental",)
    assert loaded["b"].domains == ("voco.dental",)


def test_load_tolerates_missing_optional_sections(tmp_path):
    _write(tmp_path, "bare.json", {"manufacturer": "Bare Co"})

    (pb,) = playbooks.load_playbooks(tmp_path)

    assert pb.manufacturer == "Bare Co"
    assert pb.bc_codes == ()
    assert pb.domains == ()
    assert pb.doc_sources == ()


def test_load_parses_ref_normalize(tmp_path):
    # [validate-ref-normalize] (backfill-matching plan, task 3, Part B):
    # `ref_normalize` is a comparison-time rule, distinct from the
    # parsing-time `ref_strategy` -- both can coexist on one playbook.
    _write(tmp_path, "voco.json", dict(
        VOCO, ref_normalize={"strategy": "strip-trailing-letters", "max_letters": 3}))

    (pb,) = playbooks.load_playbooks(tmp_path)

    assert pb.ref_normalize == {"strategy": "strip-trailing-letters", "max_letters": 3}


def test_ref_normalize_defaults_to_none(tmp_path):
    _write(tmp_path, "voco.json", VOCO)

    (pb,) = playbooks.load_playbooks(tmp_path)

    assert pb.ref_normalize is None


def test_ref_normalize_must_be_an_object(tmp_path):
    # Malformed ref_normalize is an authoring error -- `_parse` raises, and
    # `load_playbooks`'s per-file try/except turns that into "skip this file,
    # never crash the runtime path", same as any other malformed playbook.
    _write(tmp_path, "bad.json", dict(VOCO, ref_normalize="not-an-object"))

    assert playbooks.load_playbooks(tmp_path) == ()


def test_only_the_two_evidenced_playbooks_author_ref_normalize():
    """Authoring guard: a normalization rule is authored ONLY where it was
    measured, never inherited by a neighbour (see playbooks/README.md).

    ivoclar  strip-trailing-letters   -- 174-PDF corpus measurement, 19 -> 54
                                        linkable documents (Blocker 2).
    komet    reorder-shank-figure-size -- 186-PDF corpus measurement 2026-08-18,
                                        0 -> 98 catalogue items matched, and the
                                        client ruled the same day that Komet is
                                        the only manufacturer writing its codes
                                        in another order."""
    loaded = {pb.slug: pb for pb in playbooks.load_playbooks()}

    assert loaded["ivoclar"].ref_normalize == {
        "strategy": "strip-trailing-letters", "max_letters": 3
    }
    assert loaded["komet"].ref_normalize == {"strategy": "reorder-shank-figure-size"}
    for slug, pb in loaded.items():
        if slug not in ("ivoclar", "komet"):
            assert pb.ref_normalize is None, f"{slug} must not carry a ref_normalize rule"


def test_load_parses_companion(tmp_path):
    comp = {"key": r"^(\d+)_", "primary": "_RA_810_", "annex": "_RA_812_"}
    _write(tmp_path, "komet.json", dict(VOCO, companion=comp))

    pb = playbooks.load_playbooks(tmp_path)[0]

    assert pb.companion == comp


def test_companion_defaults_to_none(tmp_path):
    _write(tmp_path, "voco.json", VOCO)
    assert playbooks.load_playbooks(tmp_path)[0].companion is None


@pytest.mark.parametrize("bad,why", [
    ("not-an-object", "must be an object"),
    ({"primary": "a", "annex": "b"}, "needs"),
    ({"key": "^(", "primary": "a", "annex": "b"}, "not a valid regex"),
    ({"key": r"^\d+_", "primary": "a", "annex": "b"}, "capture group"),
    ({"key": r"^(\d+)(_)", "primary": "a", "annex": "b"}, "capture group"),
])
def test_a_malformed_companion_is_an_authoring_error(tmp_path, bad, why):
    """`_parse` raises and `load_playbooks` skips the file -- the same contract
    ref_normalize has. The capture-group check earns its place: a `key` with no
    group pairs NOTHING, so every annex would be dropped and every declaration
    left listless, with no error anywhere to say why."""
    _write(tmp_path, "bad.json", dict(VOCO, companion=bad))

    with pytest.raises(ValueError, match=why):
        playbooks._parse("bad", dict(VOCO, companion=bad))
    assert playbooks.load_playbooks(tmp_path) == ()


def test_only_komet_authors_a_companion_rule():
    """Same authoring guard as ref_normalize: the pairing was measured on
    Komet's 186-PDF corpus (39 leading numbers, every one carrying both an
    _RA_810_ declaration and an _RA_812_ annex, zero orphans) and is not a
    shape any other manufacturer in the corpus was shown to use."""
    loaded = {pb.slug: pb for pb in playbooks.load_playbooks()}

    assert loaded["komet"].companion == {
        "key": r"^(\d+)_", "primary": "_RA_810_", "annex": "_RA_812_"}
    for slug, pb in loaded.items():
        if slug != "komet":
            assert pb.companion is None, f"{slug} must not carry a companion rule"


def test_rev_parses_and_defaults_to_zero(tmp_path):
    """0 means 'unversioned'. Every playbook authored before this key existed
    reads as 0, which is honest -- it says 'nobody has stamped this yet'."""
    _write(tmp_path, "voco.json", VOCO)
    _write(tmp_path, "komet.json", {"manufacturer": "KOMET", "rev": 3})

    loaded = {pb.slug: pb for pb in playbooks.load_playbooks(tmp_path)}

    assert loaded["voco"].rev == 0
    assert loaded["komet"].rev == 3


def test_rev_must_be_an_integer(tmp_path):
    _write(tmp_path, "bad.json", {"manufacturer": "BAD AG", "rev": "3"})

    assert playbooks.load_playbooks(tmp_path) == ()


def test_load_parses_date_labels(tmp_path):
    _write(tmp_path, "voco.json", dict(VOCO, date_labels={"from": ["ausstellungsdatum"]}))

    (pb,) = playbooks.load_playbooks(tmp_path)

    assert pb.date_labels == {"from": ["ausstellungsdatum"]}


def test_date_labels_defaults_to_empty(tmp_path):
    _write(tmp_path, "voco.json", VOCO)

    (pb,) = playbooks.load_playbooks(tmp_path)

    assert pb.date_labels == {}


def test_date_labels_must_be_an_object_of_lists(tmp_path):
    _write(tmp_path, "bad.json", {"manufacturer": "BAD AG", "date_labels": ["nope"]})

    assert playbooks.load_playbooks(tmp_path) == ()


def test_date_labels_key_must_be_from_or_to(tmp_path):
    _write(tmp_path, "bad.json",
           {"manufacturer": "BAD AG", "date_labels": {"sideways": ["x"]}})

    assert playbooks.load_playbooks(tmp_path) == ()


def test_date_labels_values_must_be_strings(tmp_path):
    _write(tmp_path, "bad.json",
           {"manufacturer": "BAD AG", "date_labels": {"from": [1, 2]}})

    assert playbooks.load_playbooks(tmp_path) == ()


def test_load_parses_cert_number_pattern(tmp_path):
    _write(tmp_path, "voco.json",
           dict(VOCO, cert_number_pattern=r"Zertifikat-Kennung\s+([A-Z]{2}-\d{6})"))

    (pb,) = playbooks.load_playbooks(tmp_path)

    assert pb.cert_number_pattern == r"Zertifikat-Kennung\s+([A-Z]{2}-\d{6})"


def test_cert_number_pattern_defaults_to_none(tmp_path):
    _write(tmp_path, "voco.json", VOCO)

    (pb,) = playbooks.load_playbooks(tmp_path)

    assert pb.cert_number_pattern is None


def test_cert_number_pattern_must_be_a_string(tmp_path):
    _write(tmp_path, "bad.json", {"manufacturer": "BAD AG", "cert_number_pattern": 123})

    assert playbooks.load_playbooks(tmp_path) == ()


def test_cert_number_pattern_with_no_capture_group_is_a_malformed_playbook(tmp_path):
    # Caught at load, not at extract time, so a bad pattern never reaches a
    # document.
    _write(tmp_path, "bad.json",
           {"manufacturer": "BAD AG", "cert_number_pattern": r"no group here"})

    assert playbooks.load_playbooks(tmp_path) == ()


def test_cert_number_pattern_with_two_capture_groups_is_a_malformed_playbook(tmp_path):
    _write(tmp_path, "bad.json",
           {"manufacturer": "BAD AG", "cert_number_pattern": r"(A)(B)"})

    assert playbooks.load_playbooks(tmp_path) == ()


def test_an_uncompilable_cert_number_pattern_is_a_malformed_playbook(tmp_path):
    _write(tmp_path, "bad.json",
           {"manufacturer": "BAD AG", "cert_number_pattern": r"([unclosed"})

    assert playbooks.load_playbooks(tmp_path) == ()


def test_load_parses_ref_pattern(tmp_path):
    _write(tmp_path, "voco.json", dict(VOCO, ref_pattern=r"^[A-Z0-9]{1,10}\.\d{3}\.\d{1,3}$"))

    (pb,) = playbooks.load_playbooks(tmp_path)

    assert pb.ref_pattern == r"^[A-Z0-9]{1,10}\.\d{3}\.\d{1,3}$"


def test_ref_pattern_defaults_to_none(tmp_path):
    _write(tmp_path, "voco.json", VOCO)

    (pb,) = playbooks.load_playbooks(tmp_path)

    assert pb.ref_pattern is None


def test_ref_pattern_must_be_a_string(tmp_path):
    _write(tmp_path, "bad.json", {"manufacturer": "BAD AG", "ref_pattern": 123})

    assert playbooks.load_playbooks(tmp_path) == ()


def test_ref_pattern_must_compile(tmp_path):
    # Caught at load, not at extract time, so a bad pattern never reaches a
    # document -- runtime path never raises (`load_playbooks` skips it).
    _write(tmp_path, "bad.json", {"manufacturer": "BAD AG", "ref_pattern": "([unclosed"})

    assert playbooks.load_playbooks(tmp_path) == ()


def test_extract_hints_parse(tmp_path):
    _write(tmp_path, "komet.json", {
        "manufacturer": "KOMET", "rev": 1,
        "extract_hints": {"ref_list": "Codes are FIGURE.SHANK.SIZE."},
    })

    (pb,) = playbooks.load_playbooks(tmp_path)

    assert pb.extract_hints["ref_list"] == "Codes are FIGURE.SHANK.SIZE."


def test_extract_hints_must_be_an_object_of_strings(tmp_path):
    _write(tmp_path, "bad.json", {"manufacturer": "BAD AG",
                                  "extract_hints": {"ref_list": ["a", "b"]}})

    assert playbooks.load_playbooks(tmp_path) == ()


def test_extract_hints_rejects_a_falsy_non_object(tmp_path):
    """`[]` is falsy, same as `{}` -- an `... or {}` default would silently
    swallow this into an empty (valid-looking) dict instead of rejecting it."""
    _write(tmp_path, "bad.json", {"manufacturer": "BAD AG", "extract_hints": []})

    assert playbooks.load_playbooks(tmp_path) == ()
def test_load_parses_coverage_map(tmp_path):
    cm = {"sources": [{"file": "index.xlsx", "sheet": "S",
                       "columns": {"article": "A", "reference": "R", "key": "K"}}],
          "key_resolves": [{"kind": "filename-prefix", "under": "DOC"}],
          "canonical": {"key": "^(\\d+)", "prefer": ["_List_SIGNED"], "annex": ["_RA_812_Liste"]}}
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
    ({"sources": ["not-an-object"], "key_resolves": [], "canonical": {}},
     "source must be an object"),
    ({"sources": [{"file": "i.xlsx", "columns": {"article": "A", "reference": "R", "key": "K"}}],
      "key_resolves": [], "canonical": ["not-an-object"]}, "canonical must be an object"),
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


def test_load_tolerates_parse_template_keys(tmp_path):
    """t0_layout owns `match`/`ref_strategy`; the identity loader ignores them."""
    _write(tmp_path, "komet.json", dict(VOCO, match={"anchors": ["x"]}, ref_strategy="table"))

    (pb,) = playbooks.load_playbooks(tmp_path)

    assert pb.manufacturer == "VOCO GmbH"


def test_load_skips_malformed_and_never_raises(tmp_path):
    _write(tmp_path, "good.json", VOCO)
    (tmp_path / "bad.json").write_text("{not json")
    (tmp_path / "nameless.json").write_text(json.dumps({"domains": ["x.com"]}))

    loaded = playbooks.load_playbooks(tmp_path)

    assert [p.manufacturer for p in loaded] == ["VOCO GmbH"]


def test_validate_rejects_duplicate_bc_code(tmp_path):
    _write(tmp_path, "a.json", VOCO)
    _write(tmp_path, "b.json", dict(VOCO, manufacturer="Other GmbH", aliases=[]))

    with pytest.raises(playbooks.PlaybookConflict) as exc:
        playbooks.validate(playbooks.load_playbooks(tmp_path))

    assert "062" in str(exc.value)


def test_validate_rejects_duplicate_manufacturer(tmp_path):
    _write(tmp_path, "a.json", VOCO)
    _write(tmp_path, "b.json", dict(VOCO, bc_codes=[], aliases=[]))

    with pytest.raises(playbooks.PlaybookConflict) as exc:
        playbooks.validate(playbooks.load_playbooks(tmp_path))

    assert "VOCO GmbH" in str(exc.value)


def test_validate_rejects_alias_colliding_with_another_manufacturer(tmp_path):
    _write(tmp_path, "a.json", VOCO)
    _write(
        tmp_path,
        "b.json",
        dict(VOCO, manufacturer="Rival GmbH", bc_codes=[], aliases=["VOCO GmbH"]),
    )

    with pytest.raises(playbooks.PlaybookConflict):
        playbooks.validate(playbooks.load_playbooks(tmp_path))


def test_for_manufacturer_matches_canonical_and_alias(tmp_path):
    _write(tmp_path, "voco.json", VOCO)
    loaded = playbooks.load_playbooks(tmp_path)

    assert playbooks.for_manufacturer("VOCO GmbH", loaded).slug == "voco"
    assert playbooks.for_manufacturer("VOCO", loaded).slug == "voco"
    assert playbooks.for_manufacturer("voco gmbh", loaded).slug == "voco"
    assert playbooks.for_manufacturer("062", loaded) is None
    assert playbooks.for_manufacturer("Unknown", loaded) is None


def test_shipped_playbooks_are_valid():
    """Authoring guard: the real playbooks/ dir must always validate."""
    loaded = playbooks.load_playbooks()

    assert loaded, "playbooks/ is empty or unreadable"
    playbooks.validate(loaded)


def test_load_parses_exclude(tmp_path):
    # Neodent's corpus folder is the first to hold files that are not
    # documents at all (28 invoices, delivery notes and quotations). BACKFILL
    # reads this to drop them before they reach the archive.
    rule = {"reason": "sales paperwork", "filenames": ["^PDO\\d", "^ra[cč]un"]}
    _write(tmp_path, "neodent.json", dict(VOCO, exclude=rule))

    (pb,) = playbooks.load_playbooks(tmp_path)

    assert pb.exclude == rule


def test_exclude_defaults_to_none(tmp_path):
    _write(tmp_path, "voco.json", VOCO)

    (pb,) = playbooks.load_playbooks(tmp_path)

    assert pb.exclude is None


def test_exclude_must_be_an_object(tmp_path):
    _write(tmp_path, "bad.json", dict(VOCO, exclude=["^PDO"]))

    assert playbooks.load_playbooks(tmp_path) == ()


def test_exclude_without_a_reason_is_rejected(tmp_path):
    """A pattern nobody can account for later is the whole risk of this key:
    it drops files silently by design, so the one defence against a rule that
    eats declarations is that a human can read why it exists."""
    _write(tmp_path, "bad.json", dict(VOCO, exclude={"filenames": ["^PDO"]}))
    assert playbooks.load_playbooks(tmp_path) == ()

    _write(tmp_path, "bad.json", dict(VOCO, exclude={"reason": "   ", "filenames": ["^PDO"]}))
    assert playbooks.load_playbooks(tmp_path) == ()


def test_exclude_filenames_must_be_a_non_empty_list(tmp_path):
    for bad in ([], "^PDO", None, {"a": 1}):
        _write(tmp_path, "bad.json", dict(VOCO, exclude={"reason": "r", "filenames": bad}))
        assert playbooks.load_playbooks(tmp_path) == (), bad


def test_exclude_rejects_an_uncompilable_pattern(tmp_path):
    # Caught at load, so `_apply_exclude` can compile without a try/except and
    # a typo can never reach a scan as "matches nothing".
    _write(tmp_path, "bad.json", dict(
        VOCO, exclude={"reason": "r", "filenames": ["^PDO", "(unclosed"]}))

    assert playbooks.load_playbooks(tmp_path) == ()


def test_exclude_rejects_a_blank_pattern(tmp_path):
    # An empty string compiles fine and matches EVERY filename -- the single
    # most destructive value this key can take, and the one a trailing comma
    # in hand-authored JSON produces.
    _write(tmp_path, "bad.json", dict(VOCO, exclude={"reason": "r", "filenames": ["^PDO", ""]}))

    assert playbooks.load_playbooks(tmp_path) == ()


def test_load_parses_skip_backfill(tmp_path):
    # A manufacturer whose corpus dump is not fit to ingest at all. Distinct
    # from `exclude`, which drops named files from a dump that is otherwise
    # sound -- this says the whole folder is untrusted.
    rule = {"reason": "client ruling 2026-08-19: the folder is a mess"}
    _write(tmp_path, "neodent.json", dict(VOCO, skip_backfill=rule))

    (pb,) = playbooks.load_playbooks(tmp_path)

    assert pb.skip_backfill == rule


def test_skip_backfill_defaults_to_none(tmp_path):
    _write(tmp_path, "voco.json", VOCO)

    (pb,) = playbooks.load_playbooks(tmp_path)

    assert pb.skip_backfill is None


def test_skip_backfill_must_be_an_object(tmp_path):
    _write(tmp_path, "bad.json", dict(VOCO, skip_backfill=True))

    assert playbooks.load_playbooks(tmp_path) == ()


def test_skip_backfill_without_a_reason_is_rejected(tmp_path):
    """The symptom of an unexplained refusal is a manufacturer that simply
    never files anything, with no error anyone thinks to look for."""
    _write(tmp_path, "bad.json", dict(VOCO, skip_backfill={}))
    assert playbooks.load_playbooks(tmp_path) == ()

    _write(tmp_path, "bad.json", dict(VOCO, skip_backfill={"reason": "  "}))
    assert playbooks.load_playbooks(tmp_path) == ()


def test_load_does_not_reparse_an_unchanged_directory(tmp_path, monkeypatch):
    """The measured waste: validate.doc re-reads the whole directory once per
    candidate group in a loop (validate.py:465), for the same rule every time."""
    _write(tmp_path, "voco.json", VOCO)
    playbooks.clear_cache()

    parsed = []
    real_parse = playbooks._parse

    def counting_parse(slug, data):
        parsed.append(slug)
        return real_parse(slug, data)

    monkeypatch.setattr(playbooks, "_parse", counting_parse)

    playbooks.load_playbooks(tmp_path)
    playbooks.load_playbooks(tmp_path)

    assert parsed == ["voco"]


def test_load_reparses_when_a_file_changes(tmp_path):
    """playbooks/ is bind-mounted :ro and hand-authored live -- an edit must
    take effect on the next job, not the next restart. The two spellings differ
    in length, so the signature changes on size as well as mtime."""
    _write(tmp_path, "voco.json", VOCO)
    playbooks.clear_cache()
    assert playbooks.load_playbooks(tmp_path)[0].manufacturer == "VOCO GmbH"

    _write(tmp_path, "voco.json", {**VOCO, "manufacturer": "VOCO GmbH & Co. KG"})

    assert playbooks.load_playbooks(tmp_path)[0].manufacturer == "VOCO GmbH & Co. KG"


def test_load_sees_a_file_added_after_the_first_load(tmp_path):
    _write(tmp_path, "voco.json", VOCO)
    playbooks.clear_cache()
    assert len(playbooks.load_playbooks(tmp_path)) == 1

    _write(tmp_path, "komet.json", {"manufacturer": "KOMET"})

    assert len(playbooks.load_playbooks(tmp_path)) == 2


def test_load_sees_a_file_removed_after_the_first_load(tmp_path):
    _write(tmp_path, "voco.json", VOCO)
    _write(tmp_path, "komet.json", {"manufacturer": "KOMET"})
    playbooks.clear_cache()
    assert len(playbooks.load_playbooks(tmp_path)) == 2

    (tmp_path / "komet.json").unlink()

    assert len(playbooks.load_playbooks(tmp_path)) == 1


def test_two_directories_do_not_share_a_cache_entry(tmp_path):
    """The cache is keyed on the resolved directory: the web viewer's
    WEB_PLAYBOOKS_DIR and the pipeline's PLAYBOOKS_DIR are different dirs in
    the same process during tests."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    _write(a, "voco.json", VOCO)
    _write(b, "komet.json", {"manufacturer": "KOMET"})
    playbooks.clear_cache()

    assert playbooks.load_playbooks(a)[0].slug == "voco"
    assert playbooks.load_playbooks(b)[0].slug == "komet"


def test_load_parses_type_markers(tmp_path):
    _write(tmp_path, "voco.json",
           dict(VOCO, type_markers={"DoC": ["prohlášení o shodě"]}))
    playbooks.clear_cache()

    assert playbooks.load_playbooks(tmp_path)[0].type_markers == {
        "DoC": ["prohlášení o shodě"]}


def test_type_markers_defaults_to_empty(tmp_path):
    _write(tmp_path, "voco.json", VOCO)
    playbooks.clear_cache()

    assert playbooks.load_playbooks(tmp_path)[0].type_markers == {}


@pytest.mark.parametrize("bad,why", [
    ("not-an-object", "must be an object"),
    ({"DoC": "not-a-list"}, "list of strings"),
    ({"DoC": [1]}, "list of strings"),
    ({"XX": ["a"]}, "must be one of"),
])
def test_a_malformed_type_markers_is_an_authoring_error(tmp_path, bad, why):
    """A marker under an unknown type name would type documents as something
    the registry has no enum member for, silently, at T0 confidence."""
    with pytest.raises(ValueError, match=why):
        playbooks._parse("bad", dict(VOCO, type_markers=bad))
    _write(tmp_path, "bad.json", dict(VOCO, type_markers=bad))
    playbooks.clear_cache()
    assert playbooks.load_playbooks(tmp_path) == ()


def test_an_authored_path_scope_survives_normalization(tmp_path):
    """Medentika's documents live at straumann.com/medentika, and `straumann.com`
    is the straumann playbook's own domain -- so the scope has to be the path,
    not the host. Before this, `_normalize_domain` reduced the authored string to
    the bare host SILENTLY: the query became `site:straumann.com`, which is the
    whole-Straumann-site outcome the path scope exists to avoid, with no error to
    tell the author it had happened."""
    _write(tmp_path, "medentika.json",
           dict(VOCO, domains=["cad.medentika.com", "straumann.com/medentika"]))
    playbooks.clear_cache()

    assert playbooks.load_playbooks(tmp_path)[0].domains == (
        "cad.medentika.com", "straumann.com/medentika")


def test_a_path_scope_authored_as_a_full_url_keeps_its_path(tmp_path):
    _write(tmp_path, "a.json", dict(VOCO, domains=["https://www.straumann.com/medentika/en/"]))
    playbooks.clear_cache()

    assert playbooks.load_playbooks(tmp_path)[0].domains == ("www.straumann.com/medentika/en",)


def test_a_bare_host_is_still_a_bare_host(tmp_path):
    """The existing contract: no path authored, nothing added. `/` alone is not
    a path scope."""
    _write(tmp_path, "a.json", dict(VOCO, domains=["voco.dental", "https://voco.dental/"]))
    playbooks.clear_cache()

    assert playbooks.load_playbooks(tmp_path)[0].domains == ("voco.dental", "voco.dental")


def test_a_path_scope_never_reaches_the_fetch_dedupe_key():
    """Guard, not a feature test. `app.urls.domain_of` is the domain_lease key
    and half of the fetch dedupe key; if path scoping leaked into it, every
    lease row and every `fetch:` key in the queue would change meaning."""
    from app.urls import domain_of, normalize_url

    u = "https://www.straumann.com/medentika/en/downloads/doc.pdf"

    assert domain_of(u) == "www.straumann.com"
    assert normalize_url(u) == u


# --------------------------------------------------------------------------- #
# robots_refused: the hosts an author may not name as a fetch target
# --------------------------------------------------------------------------- #
# Layer 1 of the 2026-08-21 crawl-recipe design §4.1. Distinct from app/robots.py,
# which reads a host's live robots.txt: this file records Denis's 2026-08-20
# ruling, which does not depend on what that file says. COLTENE is the case that
# needs both -- its robots.txt refuses `ClaudeBot` by name and says nothing about
# our agent, so a runtime check reads it as PERMITTED.

REFUSED = frozenset({"media.coltene.com", "coltene.com", "pritidenta.com"})


def _with_source(kind, url):
    return dict(VOCO, doc_sources=[{"doc_type": "DoC", "kind": kind, "url": url}])


def test_a_direct_source_on_a_refused_host_is_rejected(tmp_path):
    _write(tmp_path, "coltene.json",
           _with_source("direct", "https://media.coltene.com/x/doc.pdf"))

    with pytest.raises(playbooks.RobotsRefused) as exc:
        playbooks.validate(playbooks.load_playbooks(tmp_path), refused=REFUSED)

    assert "media.coltene.com" in str(exc.value)
    assert 'kind:"portal"' in str(exc.value)   # the message says what to do instead


def test_the_refusal_is_caught_as_a_playbook_conflict(tmp_path):
    """Operator commands already catch PlaybookConflict; they must catch this
    without each growing a second except clause."""
    _write(tmp_path, "c.json", _with_source("direct", "https://coltene.com/a.pdf"))

    with pytest.raises(playbooks.PlaybookConflict):
        playbooks.validate(playbooks.load_playbooks(tmp_path), refused=REFUSED)


def test_a_portal_source_on_a_refused_host_is_allowed(tmp_path):
    """The sanctioned shape: portal means a human clicks it, which is exactly
    what the 2026-08-20 ruling permits. All four refused playbooks look like
    this today and must keep validating."""
    _write(tmp_path, "c.json", _with_source("portal", "https://media.coltene.com/EN/GB/index"))

    playbooks.validate(playbooks.load_playbooks(tmp_path), refused=REFUSED)


def test_a_subdomain_of_a_refused_host_is_refused(tmp_path):
    _write(tmp_path, "c.json", _with_source("direct", "https://www.coltene.com/a.pdf"))

    with pytest.raises(playbooks.RobotsRefused):
        playbooks.validate(playbooks.load_playbooks(tmp_path), refused=REFUSED)


def test_a_lookalike_host_is_not_refused(tmp_path):
    """Suffix matching is anchored on a dot: `notcoltene.com` is someone else."""
    _write(tmp_path, "n.json", _with_source("direct", "https://notcoltene.com/a.pdf"))

    playbooks.validate(playbooks.load_playbooks(tmp_path), refused=REFUSED)


def test_a_direct_source_on_an_ordinary_host_is_allowed(tmp_path):
    _write(tmp_path, "r.json", _with_source("direct", "https://www.renfert.com/a.pdf"))

    playbooks.validate(playbooks.load_playbooks(tmp_path), refused=REFUSED)


# --- the list file ---------------------------------------------------------- #

def test_the_list_parses_comments_and_blank_lines(tmp_path):
    (tmp_path / playbooks.ROBOTS_REFUSED_FILE).write_text(
        "# a header comment\n\n"
        "media.coltene.com   # inline reason\n"
        "  KAVO.WIDEN.NET  \n"
        "\n"
    )

    assert playbooks.load_robots_refused(tmp_path) == frozenset(
        {"media.coltene.com", "kavo.widen.net"}   # lowercased, trimmed
    )


def test_a_missing_list_is_empty_not_an_error(tmp_path):
    """Absence must not raise: `load_playbooks` is a never-raises runtime path.
    The consequence is a lint that finds nothing, never a fetch that should not
    happen -- app/robots.py still reads the live robots.txt."""
    assert playbooks.load_robots_refused(tmp_path) == frozenset()
    playbooks.validate(playbooks.load_playbooks(tmp_path), refused=frozenset())


# --- the ruling itself, pinned --------------------------------------------- #

def test_the_shipped_list_carries_every_ruled_host():
    """docs/2026-08-20-robots-blocked-manufacturers.md, Denis's ruling. If a host
    leaves this list, it should be because someone asked the manufacturer and
    got a yes -- not because a file was edited."""
    refused = playbooks.load_robots_refused()

    for host in ("kavo.widen.net", "media.amanngirrbach.com", "pritidenta.com",
                 "coltene.com", "media.coltene.com"):
        assert host in refused, host


def test_the_shipped_playbooks_name_no_refused_fetch_target():
    """The regression this guard exists for: nobody may author a `direct` source
    on a refused host. Runs against the real `playbooks/`, not a fixture."""
    playbooks.validate(playbooks.load_playbooks())


def test_source_priority_names_only_rungs_discover_can_dispatch(tmp_path):
    """A typo in an authored ladder must fail at PARSE, not at dispatch.

    `discover.group` raises `ValueError: unknown source rung` when it reaches
    one (discover.py), which fails the job and, after retries, dead-letters
    DISCOVER for every group of that manufacturer -- for a reason that is a
    spelling mistake in a file. Since slice 3a the ladder is an editable form
    field, so this is also what refuses a bad save at 422 instead of breaking
    the pipeline for one manufacturer.
    """
    _write(tmp_path, "a.json", dict(VOCO, bc_codes=[], aliases=[],
                                    source_priority=["playbook", "serach"]))
    with pytest.raises(ValueError, match="unknown rung"):
        playbooks._parse("a", json.loads((tmp_path / "a.json").read_text()))

    # And the runtime path stays never-raises: the bad playbook is skipped,
    # not propagated.
    assert playbooks.load_playbooks(tmp_path) == ()


def test_every_rung_the_default_ladder_uses_is_in_the_vocabulary(tmp_path):
    """The vocabulary is declared in `app/playbooks.py` and the ladder lives in
    `app/config.py`; nothing but this stops them drifting apart, and the drift
    would show up as a save refusing a rung the pipeline runs by default."""
    from app.config import load_config

    assert set(load_config().discovery.default_source_priority) <= playbooks.DISCOVER_RUNGS


def test_the_whole_default_ladder_is_accepted_verbatim(tmp_path):
    from app.config import load_config

    ladder = list(load_config().discovery.default_source_priority)
    _write(tmp_path, "a.json", dict(VOCO, bc_codes=[], aliases=[],
                                    source_priority=ladder))
    (pb,) = playbooks.load_playbooks(tmp_path)
    assert list(pb.source_priority) == ladder
