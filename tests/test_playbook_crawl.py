"""The `crawl` block (playbook-crawl-recipe design, slice 3 of 6 in its §6
sequence): parse, validate, document. INERT.

`docs/superpowers/specs/2026-08-21-playbook-crawl-recipe-design.md` §3 gives
the exact JSON shape and §4.3 the over-broad `link_pattern` defences; all five
open questions in its §7 are ruled (2026-08-21, Denis). Nothing reads
`Playbook.crawl` yet -- the DISCOVER rung that consumes it (§3 steps 3-8) is
its own later slice. This file only exercises what ships now: a malformed or
robots-refused crawl recipe is refused at authoring time, before anything
could ever fetch with it.
"""

from __future__ import annotations

import json

import pytest

from app import playbooks


def _write(dir_path, filename, data):
    (dir_path / filename).write_text(json.dumps(data))


#: Minimal identity every fixture playbook needs -- `crawl` is merged on top.
BASE = {"manufacturer": "Test AG"}

#: The exact shape from the design's §3, all keys authored.
FULL_CRAWL = {
    "index_url": "https://www.voco.dental/en/service/download/instructions-for-use.aspx",
    "link_pattern": r"\.pdf$",
    "same_host_only": True,
    "doc_type_from": {"ifu": "IFU", "konformit": "DoC"},
    "pagination": {"param": "page", "max_pages": 20},
    "max_links": 200,
}

#: Only the two required keys.
MIN_CRAWL = {
    "index_url": "https://www.example.com/downloads",
    "link_pattern": r"\.pdf$",
}


# --------------------------------------------------------------------------- #
# parsing the full shape, and the optional/default behaviour
# --------------------------------------------------------------------------- #
def test_load_parses_the_full_shape_from_the_design_doc(tmp_path):
    _write(tmp_path, "voco.json", dict(BASE, crawl=[FULL_CRAWL]))

    (pb,) = playbooks.load_playbooks(tmp_path)

    assert pb.crawl == (playbooks.Crawl(
        index_url=FULL_CRAWL["index_url"],
        link_pattern=r"\.pdf$",
        same_host_only=True,
        allow_hosts=(),
        max_links=200,
        doc_type_from={"ifu": "IFU", "konformit": "DoC"},
        pagination={"param": "page", "max_pages": 20},
    ),)


def test_crawl_defaults_to_none(tmp_path):
    """No playbook authors this yet -- the field must be absent-safe, or all
    38 shipped playbooks fail to load the moment this lands."""
    _write(tmp_path, "voco.json", BASE)

    (pb,) = playbooks.load_playbooks(tmp_path)

    assert pb.crawl == ()


def test_only_the_two_required_keys_still_parses_with_defaults(tmp_path):
    _write(tmp_path, "a.json", dict(BASE, crawl=[MIN_CRAWL]))

    (pb,) = playbooks.load_playbooks(tmp_path)

    assert pb.crawl[0].index_url == MIN_CRAWL["index_url"]
    assert pb.crawl[0].link_pattern == r"\.pdf$"
    # same_host_only default True -- ruled 2026-08-21, design §4.3/§7.2.
    assert pb.crawl[0].same_host_only is True
    assert pb.crawl[0].allow_hosts == ()
    # max_links required IN EFFECT, not in the JSON -- default fills it.
    assert pb.crawl[0].max_links == playbooks.CRAWL_MAX_LINKS_DEFAULT == 200
    assert pb.crawl[0].doc_type_from is None
    assert pb.crawl[0].pagination is None


def test_crawl_must_be_an_object(tmp_path):
    with pytest.raises(ValueError, match="crawl must be a list"):
        playbooks._parse("bad", dict(BASE, crawl="not-an-object"))
    _write(tmp_path, "bad.json", dict(BASE, crawl="not-an-object"))
    assert playbooks.load_playbooks(tmp_path) == ()


# --------------------------------------------------------------------------- #
# index_url -- rule 1: required, absolute http(s)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad_crawl,why", [
    ({k: v for k, v in MIN_CRAWL.items() if k != "index_url"}, "required"),
    (dict(MIN_CRAWL, index_url=""), "required"),
    (dict(MIN_CRAWL, index_url="   "), "required"),
    (dict(MIN_CRAWL, index_url=123), "required"),
    (dict(MIN_CRAWL, index_url="www.example.com/downloads"), "absolute http"),
    (dict(MIN_CRAWL, index_url="/relative/path"), "absolute http"),
    (dict(MIN_CRAWL, index_url="ftp://example.com/downloads"), "absolute http"),
], ids=["missing", "empty", "blank", "not-a-string", "no-scheme", "relative",
        "wrong-scheme"])
def test_index_url_must_be_an_absolute_http_url(tmp_path, bad_crawl, why):
    with pytest.raises(ValueError, match=why):
        playbooks._parse("bad", dict(BASE, crawl=[bad_crawl]))
    _write(tmp_path, "bad.json", dict(BASE, crawl=[bad_crawl]))
    assert playbooks.load_playbooks(tmp_path) == ()


def test_index_url_accepts_https_and_http():
    for scheme in ("https", "http"):
        pb = playbooks._parse(
            "x", dict(BASE, crawl=[dict(MIN_CRAWL, index_url=f"{scheme}://example.com/x")]))
        assert pb.crawl[0].index_url == f"{scheme}://example.com/x"


# --------------------------------------------------------------------------- #
# link_pattern -- rule 2: required, must compile, error names the playbook
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad_crawl,why", [
    ({k: v for k, v in MIN_CRAWL.items() if k != "link_pattern"}, "required"),
    (dict(MIN_CRAWL, link_pattern=""), "required"),
    (dict(MIN_CRAWL, link_pattern=42), "required"),
    (dict(MIN_CRAWL, link_pattern="(unclosed"), "not a valid regex"),
], ids=["missing", "empty", "not-a-string", "uncompilable"])
def test_link_pattern_is_required_and_must_compile(tmp_path, bad_crawl, why):
    with pytest.raises(ValueError, match=why):
        playbooks._parse("bad", dict(BASE, crawl=[bad_crawl]))
    _write(tmp_path, "bad.json", dict(BASE, crawl=[bad_crawl]))
    assert playbooks.load_playbooks(tmp_path) == ()


def test_an_uncompilable_link_pattern_names_the_playbook_and_the_regex_error():
    """Design rule 2: 'a validation error naming the playbook and the regex
    error' -- not just any ValueError, one that says WHICH playbook and WHY,
    the same discipline `cert_number_pattern`'s message lacks but this one
    was explicitly asked to have."""
    with pytest.raises(ValueError) as exc:
        playbooks._parse("acme-dental", dict(BASE, crawl=[dict(
            MIN_CRAWL, link_pattern="[unterminated")]))
    msg = str(exc.value)
    assert "acme-dental" in msg
    assert "link_pattern" in msg


# --------------------------------------------------------------------------- #
# same_host_only -- rule 3: optional, default True
# --------------------------------------------------------------------------- #
def test_same_host_only_can_be_authored_false(tmp_path):
    pb = playbooks._parse(
        "x", dict(BASE, crawl=[dict(MIN_CRAWL, same_host_only=False)]))
    assert pb.crawl[0].same_host_only is False


def test_same_host_only_must_be_a_boolean(tmp_path):
    with pytest.raises(ValueError, match="boolean"):
        playbooks._parse("bad", dict(BASE, crawl=[dict(MIN_CRAWL, same_host_only="true")]))


# --------------------------------------------------------------------------- #
# allow_hosts -- rule 4: the only widening mechanism, normalized like `domains`
# --------------------------------------------------------------------------- #
def test_allow_hosts_normalizes_like_domains(tmp_path):
    """Same function, same conventions (`_normalize_domain`, app/playbooks.py:221):
    a bare hostname and a full URL fold to the same value, and a path scope
    survives -- exactly as `domains` behaves for Medentika/Straumann."""
    pb = playbooks._parse("x", dict(BASE, crawl=[dict(
        MIN_CRAWL,
        allow_hosts=["https://cdn.example.com/", "assets.example.org",
                      "straumann.com/medentika"],
    )]))
    assert pb.crawl[0].allow_hosts == (
        "cdn.example.com", "assets.example.org", "straumann.com/medentika")


def test_allow_hosts_defaults_to_empty(tmp_path):
    pb = playbooks._parse("x", dict(BASE, crawl=[MIN_CRAWL]))
    assert pb.crawl[0].allow_hosts == ()


def test_allow_hosts_must_be_a_list(tmp_path):
    with pytest.raises(ValueError, match="allow_hosts must be a list"):
        playbooks._parse("bad", dict(BASE, crawl=[dict(MIN_CRAWL, allow_hosts="cdn.example.com")]))


# --------------------------------------------------------------------------- #
# max_links -- rule 5: required in effect, default 200, hard ceiling
# --------------------------------------------------------------------------- #
def test_max_links_omitted_defaults_to_200(tmp_path):
    pb = playbooks._parse("x", dict(BASE, crawl=[MIN_CRAWL]))
    assert pb.crawl[0].max_links == 200


@pytest.mark.parametrize("bad", [0, -5, "200", 1.5, True])
def test_max_links_must_be_a_positive_integer(tmp_path, bad):
    with pytest.raises(ValueError, match="positive integer"):
        playbooks._parse("bad", dict(BASE, crawl=[dict(MIN_CRAWL, max_links=bad)]))


def test_max_links_at_the_ceiling_is_accepted():
    pb = playbooks._parse("x", dict(BASE, crawl=[dict(
        MIN_CRAWL, max_links=playbooks.CRAWL_MAX_LINKS_CEILING)]))
    assert pb.crawl[0].max_links == playbooks.CRAWL_MAX_LINKS_CEILING


def test_max_links_over_the_ceiling_is_rejected(tmp_path):
    """The hard ceiling design rule 5 asks for: `max_links` is a REQUIRED cap,
    not a suggestion an author can inflate past. `_parse` enforces it (see
    module docstring note on why this lives here rather than in `validate()`:
    a bare `ValueError` from `validate()` would not be caught by the
    `SaveRefused`/`PlaybookConflict`-only catch in `web/registry.py`'s save
    path, which every other authoring error already relies on)."""
    over = playbooks.CRAWL_MAX_LINKS_CEILING + 1
    with pytest.raises(ValueError, match="exceeds the hard ceiling"):
        playbooks._parse("bad", dict(BASE, crawl=[dict(MIN_CRAWL, max_links=over)]))
    _write(tmp_path, "bad.json", dict(BASE, crawl=[dict(MIN_CRAWL, max_links=over)]))
    assert playbooks.load_playbooks(tmp_path) == ()


# --------------------------------------------------------------------------- #
# doc_type_from -- rule 6: optional, values restricted to DOC_TYPES
# --------------------------------------------------------------------------- #
def test_doc_type_from_defaults_to_none(tmp_path):
    pb = playbooks._parse("x", dict(BASE, crawl=[MIN_CRAWL]))
    assert pb.crawl[0].doc_type_from is None


def test_doc_type_from_every_doc_type_is_accepted():
    mapping = {t.lower(): t for t in playbooks.DOC_TYPES}
    pb = playbooks._parse("x", dict(BASE, crawl=[dict(MIN_CRAWL, doc_type_from=mapping)]))
    assert pb.crawl[0].doc_type_from == mapping


def test_doc_type_from_must_be_an_object(tmp_path):
    with pytest.raises(ValueError, match="doc_type_from must be an object"):
        playbooks._parse("bad", dict(BASE, crawl=[dict(MIN_CRAWL, doc_type_from=["ifu"])]))


def test_doc_type_from_rejects_an_unknown_doc_type(tmp_path):
    """An unknown value would type a crawled document as something the
    registry has no member for, at parse time, silently -- same failure mode
    `type_markers` guards against."""
    with pytest.raises(ValueError, match="DoC.*EC.*IFU.*ISO"):
        playbooks._parse("bad", dict(BASE, crawl=[dict(
            MIN_CRAWL, doc_type_from={"cert": "PDF"})]))
    _write(tmp_path, "bad.json", dict(BASE, crawl=[dict(
        MIN_CRAWL, doc_type_from={"cert": "PDF"})]))
    assert playbooks.load_playbooks(tmp_path) == ()


# --------------------------------------------------------------------------- #
# pagination -- rule 7: optional, {param, max_pages}, ceiling 50
# --------------------------------------------------------------------------- #
def test_pagination_defaults_to_none(tmp_path):
    pb = playbooks._parse("x", dict(BASE, crawl=[MIN_CRAWL]))
    assert pb.crawl[0].pagination is None


def test_pagination_parses(tmp_path):
    pb = playbooks._parse("x", dict(BASE, crawl=[dict(
        MIN_CRAWL, pagination={"param": "page", "max_pages": 20})]))
    assert pb.crawl[0].pagination == {"param": "page", "max_pages": 20}


@pytest.mark.parametrize("bad,why", [
    ({}, "needs"),
    ({"param": "page"}, "needs"),
    ({"max_pages": 20}, "needs"),
    ({"param": "", "max_pages": 20}, "non-empty string"),
    ({"param": 7, "max_pages": 20}, "non-empty string"),
    ({"param": "page", "max_pages": 0}, "positive integer"),
    ({"param": "page", "max_pages": -1}, "positive integer"),
    ({"param": "page", "max_pages": "20"}, "positive integer"),
    ({"param": "page", "max_pages": True}, "positive integer"),
])
def test_a_malformed_pagination_is_an_authoring_error(tmp_path, bad, why):
    with pytest.raises(ValueError, match=why):
        playbooks._parse("bad", dict(BASE, crawl=[dict(MIN_CRAWL, pagination=bad)]))
    _write(tmp_path, "bad.json", dict(BASE, crawl=[dict(MIN_CRAWL, pagination=bad)]))
    assert playbooks.load_playbooks(tmp_path) == ()


def test_pagination_must_be_an_object(tmp_path):
    with pytest.raises(ValueError, match="pagination must be an object"):
        playbooks._parse("bad", dict(BASE, crawl=[dict(MIN_CRAWL, pagination="page=2")]))


def test_pagination_max_pages_at_the_ceiling_is_accepted():
    pb = playbooks._parse("x", dict(BASE, crawl=[dict(
        MIN_CRAWL,
        pagination={"param": "page", "max_pages": playbooks.CRAWL_MAX_PAGES_CEILING})]))
    assert pb.crawl[0].pagination["max_pages"] == playbooks.CRAWL_MAX_PAGES_CEILING


def test_pagination_max_pages_over_the_ceiling_is_rejected(tmp_path):
    over = playbooks.CRAWL_MAX_PAGES_CEILING + 1
    with pytest.raises(ValueError, match="exceeds the hard ceiling"):
        playbooks._parse("bad", dict(BASE, crawl=[dict(
            MIN_CRAWL, pagination={"param": "page", "max_pages": over})]))


# --------------------------------------------------------------------------- #
# validate(): rule 8 -- a robots-refused index_url host must be refused,
# extending the SAME check `doc_sources[kind:"direct"]` already goes through
# (app/playbooks.py:907, comment at :930 anticipated exactly this).
# --------------------------------------------------------------------------- #
REFUSED = frozenset({"media.coltene.com", "coltene.com", "pritidenta.com"})


def test_a_crawl_index_url_on_a_refused_host_is_rejected(tmp_path):
    _write(tmp_path, "coltene.json", dict(BASE, crawl=[dict(
        MIN_CRAWL, index_url="https://media.coltene.com/downloads")]))

    with pytest.raises(playbooks.RobotsRefused) as exc:
        playbooks.validate(playbooks.load_playbooks(tmp_path), refused=REFUSED)

    msg = str(exc.value)
    assert "media.coltene.com" in msg
    assert "crawl.index_url" in msg
    assert 'kind:"portal"' in msg   # names the way out, same as the doc_sources refusal


def test_a_subdomain_of_a_refused_host_is_refused_for_crawl_too(tmp_path):
    _write(tmp_path, "c.json", dict(BASE, crawl=[dict(
        MIN_CRAWL, index_url="https://www.coltene.com/downloads")]))

    with pytest.raises(playbooks.RobotsRefused):
        playbooks.validate(playbooks.load_playbooks(tmp_path), refused=REFUSED)


def test_a_crawl_index_url_on_an_ordinary_host_is_allowed(tmp_path):
    _write(tmp_path, "r.json", dict(BASE, crawl=[dict(
        MIN_CRAWL, index_url="https://www.renfert.com/downloads")]))

    playbooks.validate(playbooks.load_playbooks(tmp_path), refused=REFUSED)


def test_a_playbook_with_no_crawl_block_is_unaffected_by_the_refused_list(tmp_path):
    _write(tmp_path, "r.json", BASE)

    playbooks.validate(playbooks.load_playbooks(tmp_path), refused=REFUSED)


def test_the_crawl_refusal_is_caught_as_a_playbook_conflict(tmp_path):
    """Operator commands and the UI save already catch `PlaybookConflict`
    (app/cli.py, web/registry.py); this must not need a second except clause."""
    _write(tmp_path, "c.json", dict(BASE, crawl=[dict(
        MIN_CRAWL, index_url="https://coltene.com/downloads")]))

    with pytest.raises(playbooks.PlaybookConflict):
        playbooks.validate(playbooks.load_playbooks(tmp_path), refused=REFUSED)


# --------------------------------------------------------------------------- #
# additive only -- every shipped playbook still validates
# --------------------------------------------------------------------------- #
def test_only_measured_playbooks_author_a_crawl_recipe():
    """Three playbooks carry a recipe, and every one was measured against a
    live fetch on the day it was authored -- never copied from a note.

    Edenta first (2026-09-03, both libraries, 13 documents where its own
    2026-08-20 note had said 11 -- the reason this tripwire exists). NSK and
    Ultradent on 2026-09-09, both re-fetched the same day:

      nsk        1367 anchors, 1362 matching, 1329 kept, 0 off-host, 0 capped
      ultradent  277 anchors, 149 matching, 147 kept WITH allow_hosts and
                 **0 without** -- every document is on assets.ctfassets.net

    Still a tripwire, not a permanent truth: the fourth recipe updates this
    test, and that edit is the moment to check the new one was measured rather
    than guessed."""
    loaded = {pb.slug: pb for pb in playbooks.load_playbooks()}
    with_crawl = {slug for slug, pb in loaded.items() if pb.crawl}
    assert with_crawl == {"edenta", "nsk", "ultradent"}, \
        f"unexpected crawl recipes: {with_crawl}"

    edenta = loaded["edenta"]
    assert len(edenta.crawl) == 2
    # The extensionless trap, pinned: `\.pdf$` matched ZERO of the 40 anchors
    # on Edenta's own page when measured. A recipe that drifts back to an
    # extension pattern harvests nothing and says so only in a counted miss.
    assert all("/zoolu-website/media/document/" == c.link_pattern
               for c in edenta.crawl)


def test_nsk_excludes_safety_data_sheets_by_the_needle_that_actually_matches():
    """`doc_type_from` matches a casefolded substring of the resolved URL, and
    the needle the design notes quote -- `sds_`, with an underscore -- reaches
    13 of NSK's 444 safety data sheets, not all of them.

    Measured over all 1329 harvested links 2026-09-09: the filenames are
    `NSK-SDS-001-DE-EU_Rev000.pdf`, so `sds` hits 444 of 444 SDS and 0 of 124
    declarations. The underscore form would have let 431 safety data sheets
    into the registry while the operator's screen reported them excluded --
    the same shape as `[crawl-doc-type-from-is-probe-only]`, where the number
    on the screen and what the pipeline did disagreed."""
    nsk = {pb.slug: pb for pb in playbooks.load_playbooks()}["nsk"]
    recipe = nsk.crawl[0]
    assert recipe.doc_type_from == {"sds": None}, recipe.doc_type_from
    # `null` is the EXCLUDE marker (Denis, 2026-09-03), not a missing value.
    assert recipe.doc_type_from["sds"] is None


def test_ultradent_names_the_cdn_its_documents_actually_live_on():
    """Without `allow_hosts` the Ultradent harvest is 0 of 149 -- measured
    2026-09-09, every link resolves off-host to Contentful. `same_host_only`
    stays true: naming the host is the only widening mechanism the crawl
    design permits (4.3), and turning the flag off would follow anything."""
    ult = {pb.slug: pb for pb in playbooks.load_playbooks()}["ultradent"]
    recipe = ult.crawl[0]
    assert recipe.same_host_only is True
    assert "assets.ctfassets.net" in recipe.allow_hosts


def test_the_shipped_playbooks_still_validate_with_the_crawl_field_added():
    """Additive-only regression: every one of the 38 delivered files must
    still parse and validate now that `Playbook` carries a new optional
    field with a default."""
    loaded = playbooks.load_playbooks()
    assert len(loaded) == 38
    playbooks.validate(loaded)


# --- doc_type_from: null means EXCLUDE (ruled Denis 2026-09-03) --------------
#
# `null` is a real authored value, not an omission. It means "harvest this link
# and count it, but never type it as a compliance document" -- the exclusion
# mechanism for a library that mixes safety data sheets in with declarations.
# The alternative it replaces was a negative lookahead in `link_pattern`, which
# would have dropped the files from the counts too and made a partial harvest
# read as a complete one.

def test_doc_type_from_accepts_null_as_an_exclusion():
    pb = playbooks._parse("nsk", dict(BASE, crawl=[{
        "index_url": "https://www.nsk-library.com/",
        "link_pattern": r"\.pdf$",
        "doc_type_from": {"ec_doc": "DoC", "sds_": None},
    }]))
    assert pb.crawl[0].doc_type_from == {"ec_doc": "DoC", "sds_": None}


def test_doc_type_from_still_refuses_an_unknown_type():
    with pytest.raises(ValueError, match="must be one of"):
        playbooks._parse("nsk", dict(BASE, crawl=[{
            "index_url": "https://www.nsk-library.com/",
            "link_pattern": r"\.pdf$",
            "doc_type_from": {"ec_doc": "DECLARATION"},
        }]))


def test_the_unknown_type_error_names_null_as_permitted():
    """The message has to teach the exclusion, or an author who wants to drop a
    file type will never guess that `null` is how."""
    with pytest.raises(ValueError, match="or null"):
        playbooks._parse("nsk", dict(BASE, crawl=[{
            "index_url": "https://www.nsk-library.com/",
            "link_pattern": r"\.pdf$",
            "doc_type_from": {"sds_": "SDS"},
        }]))


# --- crawl is a LIST: more than one library per manufacturer -----------------
#
# Ruled Denis 2026-09-03, before any recipe was authored against the singular
# shape the 2026-08-21 design sketched. 12 of the 38 delivered playbooks
# already author more than one `portal`, and Edenta is the worked case:
# instructions for use on one page, EC/ISO certificates on another, a
# different `doc_type` each, both needed.

def test_a_playbook_may_author_more_than_one_recipe():
    pb = playbooks._parse("edenta", dict(BASE, crawl=[
        {"index_url": "https://www.edenta.com/en/instructions-for-use-and-safety-guidelines",
         "link_pattern": "/zoolu-website/media/document/"},
        {"index_url": "https://www.edenta.com/en/iso-certificates",
         "link_pattern": "/zoolu-website/media/document/",
         "doc_type_from": {"13485": "ISO", "zertifikat": "EC"}},
    ]))
    assert len(pb.crawl) == 2
    assert pb.crawl[0].doc_type_from is None
    assert pb.crawl[1].doc_type_from == {"13485": "ISO", "zertifikat": "EC"}
    # Authored order is preserved: it is the order the rung runs them in.
    assert pb.crawl[0].index_url.endswith("instructions-for-use-and-safety-guidelines")


def test_an_empty_crawl_list_is_the_same_as_no_crawl_key():
    assert playbooks._parse("a", dict(BASE, crawl=[])).crawl == ()
    assert playbooks._parse("b", dict(BASE)).crawl == ()


def test_a_bare_object_is_refused_rather_than_silently_wrapped():
    """One shape, not two. Nothing authors this key yet, so an unambiguous
    schema is worth more than the ergonomics of accepting both."""
    with pytest.raises(ValueError, match="crawl must be a list"):
        playbooks._parse("bad", dict(BASE, crawl=MIN_CRAWL))


def test_one_bad_recipe_rejects_the_whole_file():
    """A partially-applied playbook is worse than a refused one."""
    with pytest.raises(ValueError, match="link_pattern"):
        playbooks._parse("bad", dict(BASE, crawl=[
            MIN_CRAWL,
            {"index_url": "https://ok.example/x", "link_pattern": "("},
        ]))
