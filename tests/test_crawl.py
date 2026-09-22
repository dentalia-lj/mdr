"""app/crawl.py -- pure link harvester for the playbook `crawl` recipe.

Design: docs/superpowers/specs/2026-08-21-playbook-crawl-recipe-design.md.
This module is a pure function library (HTML string in, links+counts out; no
I/O, no DB, no network), so every case below is a plain fixture string with no
mocks -- see module docstring in app/crawl.py for the boundary rationale.
"""

from __future__ import annotations

import pytest

from app.crawl import HarvestResult, Paginator, harvest

# --------------------------------------------------------------------------- #
# Fixtures based on real manufacturer shapes named in the design doc.
# --------------------------------------------------------------------------- #

# NSK: absolute hrefs under /admin/wp-content/uploads/*.pdf (design §3
# proposal cites this shape as the ordinary, extension-bearing case).
NSK_HTML = """
<html><body>
<nav><a href="/en/">Home</a></nav>
<div class="downloads">
  <a href="https://www.nsk-dental.com/admin/wp-content/uploads/2024/01/IFU-S-Max-M.pdf">IFU S-Max M</a>
  <a href="https://www.nsk-dental.com/admin/wp-content/uploads/2024/01/DoC-S-Max-M.pdf">DoC S-Max M</a>
  <a href="https://www.nsk-dental.com/admin/wp-content/uploads/2023/11/IFU-Ti-Max.pdf">IFU Ti-Max</a>
</div>
<footer><a href="https://www.nsk-dental.com/en/contact/">Contact</a></footer>
</body></html>
"""

# Edenta: relative, EXTENSIONLESS document hrefs -- design §4.5/§4.6's named
# link_pattern trap. A `\\.pdf$` pattern (the naive first guess) must match
# NONE of these; a path-scoped pattern must match all of them.
EDENTA_HTML = """
<html><body>
<div id="documents">
  <a href="/zoolu-website/media/document/1044/Instructions_for_Use_Diamond_Polishers">IFU Diamond Polishers</a>
  <a href="/zoolu-website/media/document/1045/Instructions_for_Use_Ceramic_Polishers">IFU Ceramic Polishers</a>
  <a href="../about-us">About</a>
</div>
</body></html>
"""

# Anchors present, none of them documents -- the "pattern is probably wrong"
# signal (design §4.6) must be distinguishable from a page with NO anchors.
NAV_ONLY_HTML = """
<html><body>
<nav>
  <a href="/en/products">Products</a>
  <a href="/en/about">About</a>
  <a href="/en/contact">Contact</a>
</nav>
</body></html>
"""

NO_ANCHORS_HTML = "<html><body><p>Nothing to see here.</p></body></html>"


# --------------------------------------------------------------------------- #
# Core extraction + resolution
# --------------------------------------------------------------------------- #


def test_nsk_shape_absolute_hrefs_all_match():
    result = harvest(
        NSK_HTML,
        base_url="https://www.nsk-dental.com/en/service/downloads/",
        link_pattern=r"\.pdf$",
    )
    assert result.anchors_seen == 5  # Home, 3 pdfs, Contact
    assert result.links_matched == 3
    assert result.off_host == 0
    assert result.links_capped == 0
    assert result.links == (
        "https://www.nsk-dental.com/admin/wp-content/uploads/2024/01/IFU-S-Max-M.pdf",
        "https://www.nsk-dental.com/admin/wp-content/uploads/2024/01/DoC-S-Max-M.pdf",
        "https://www.nsk-dental.com/admin/wp-content/uploads/2023/11/IFU-Ti-Max.pdf",
    )


def test_edenta_shape_pdf_suffix_pattern_matches_nothing():
    # The naive first-guess pattern on an extensionless portal: anchors exist,
    # nothing matches. This is the §4.6 "your pattern is wrong" signal.
    result = harvest(
        EDENTA_HTML,
        base_url="https://www.edenta.com/en/service/downloads",
        link_pattern=r"\.pdf$",
    )
    assert result.anchors_seen == 3
    assert result.links_matched == 0
    assert result.links == ()


def test_edenta_shape_path_scoped_pattern_matches_extensionless_docs():
    result = harvest(
        EDENTA_HTML,
        base_url="https://www.edenta.com/en/service/downloads",
        link_pattern=r"/zoolu-website/media/document/\d+/",
    )
    assert result.anchors_seen == 3
    assert result.links_matched == 2
    assert result.links == (
        "https://www.edenta.com/zoolu-website/media/document/1044/Instructions_for_Use_Diamond_Polishers",
        "https://www.edenta.com/zoolu-website/media/document/1045/Instructions_for_Use_Ceramic_Polishers",
    )


def test_pattern_matches_but_no_anchors_is_distinguishable_from_no_anchors_at_all():
    matched_none = harvest(NAV_ONLY_HTML, base_url="https://x.com/", link_pattern=r"\.pdf$")
    no_anchors = harvest(NO_ANCHORS_HTML, base_url="https://x.com/", link_pattern=r"\.pdf$")

    assert matched_none.anchors_seen > 0
    assert matched_none.links_matched == 0

    assert no_anchors.anchors_seen == 0
    assert no_anchors.links_matched == 0


def test_harvest_keeps_the_text_a_person_would_read_next_to_each_link():
    """The T1 ranker needs more than a filename: `/uploads/2023/1234.pdf` is
    opaque, "Declaration of Conformity MDR" is not. Text is taken from the
    link-bearing element, so a `<tr data-href>` row contributes its cells."""
    html = (
        '<a href="/d/1.pdf">Declaration of  Conformity\n MDR</a>'
        '<table><tr data-href="/d/2.pdf"><td>IFU</td><td>Implants</td></tr></table>'
        '<a href="/d/3.pdf"></a>'
    )
    result = harvest(html, base_url="https://x.example/lib", link_pattern=r"\.pdf$")
    assert result.links == ("https://x.example/d/1.pdf", "https://x.example/d/2.pdf",
                            "https://x.example/d/3.pdf")
    assert result.texts["https://x.example/d/1.pdf"] == "Declaration of Conformity MDR"
    assert result.texts["https://x.example/d/2.pdf"] == "IFU Implants"
    assert result.texts["https://x.example/d/3.pdf"] == ""


def test_relative_href_resolved_against_base_before_matching():
    # A pattern that can ONLY match the resolved absolute form (it names the
    # host, which never appears in a bare relative href). Proves resolution
    # happens before pattern matching, not after -- design §3's explicit
    # requirement, pinned because a relative-href site is the common case.
    html = '<a href="/download/report">Report</a>'
    result = harvest(
        html,
        base_url="https://www.example.com/service/",
        link_pattern=r"^https://www\.example\.com/download/",
    )
    assert result.links == ("https://www.example.com/download/report",)


def test_relative_href_with_dotdot_resolves_correctly():
    html = '<a href="../files/doc.pdf">Doc</a>'
    result = harvest(html, base_url="https://x.com/en/service/downloads/", link_pattern=r"\.pdf$")
    assert result.links == ("https://x.com/en/service/files/doc.pdf",)


# --------------------------------------------------------------------------- #
# href collection: only <a href>, tolerant of malformed markup
# --------------------------------------------------------------------------- #


def test_ignores_non_anchor_href_bearing_tags():
    html = """
    <link rel="stylesheet" href="https://x.com/a.css">
    <area shape="rect" href="https://x.com/map.pdf">
    <a href="https://x.com/real.pdf">Real</a>
    """
    result = harvest(html, base_url="https://x.com/", link_pattern=r"\.pdf$")
    assert result.anchors_seen == 1
    assert result.links == ("https://x.com/real.pdf",)


def test_anchor_without_href_not_counted():
    html = '<a name="top">Top</a><a href="">Empty</a><a href="https://x.com/a.pdf">Doc</a>'
    result = harvest(html, base_url="https://x.com/", link_pattern=r"\.pdf$")
    assert result.anchors_seen == 1
    assert result.links_matched == 1


def test_tolerant_of_malformed_markup_does_not_raise():
    # Unclosed <a> (no matching </a>), an unquoted href, and a stray '<' that
    # is not a tag -- html.parser recovers instead of raising (design §4.2
    # requirement: "must not raise on real-world HTML"). Note what this does
    # NOT claim: a genuinely unclosed opening tag (a bare "<div ..." with no
    # ">" at all) makes html.parser swallow everything up to the next ">" as
    # that tag's attribute soup, losing an anchor inside it -- that is a
    # documented stdlib-parser limitation, not something this module can
    # paper over without a recovering third-party parser (forbidden, see
    # module docstring / the parser ladder ruled 2026-08-21).
    html = """
    <html><body>
    <div class="downloads">
      <a href="https://x.com/a.pdf">A
      <a href=https://x.com/b.pdf>B</a>
    <p>3 < 5 and unrelated stray angle bracket
    </body>
    """
    result = harvest(html, base_url="https://x.com/", link_pattern=r"\.pdf$")
    assert "https://x.com/a.pdf" in result.links
    assert "https://x.com/b.pdf" in result.links


# --------------------------------------------------------------------------- #
# same_host_only / allow_hosts (design §4.3)
# --------------------------------------------------------------------------- #


def test_same_host_only_default_rejects_offsite_pdf():
    html = """
    <a href="https://cdn.assets.example/spec.pdf">Offsite</a>
    <a href="https://x.com/onsite.pdf">Onsite</a>
    """
    result = harvest(html, base_url="https://x.com/", link_pattern=r"\.pdf$")
    assert result.links == ("https://x.com/onsite.pdf",)
    assert result.off_host == 1
    assert result.links_matched == 1  # off-host hits do not count as matched


def test_allow_hosts_widens_without_disabling_host_check():
    html = """
    <a href="https://cdn.assets.example/spec.pdf">CDN</a>
    <a href="https://evil.example/spec.pdf">Not allowed</a>
    """
    result = harvest(
        html,
        base_url="https://x.com/",
        link_pattern=r"\.pdf$",
        allow_hosts=("cdn.assets.example",),
    )
    assert result.links == ("https://cdn.assets.example/spec.pdf",)
    assert result.off_host == 1


def test_allow_hosts_case_insensitive():
    # The ACCEPT decision is case-insensitive; the returned link is not
    # rewritten -- harvest() resolves and filters, it does not normalize (that
    # is app.urls.normalize_url's job, applied downstream at _emit_fetch).
    html = '<a href="https://CDN.Assets.Example/spec.pdf">CDN</a>'
    result = harvest(
        html,
        base_url="https://x.com/",
        link_pattern=r"\.pdf$",
        allow_hosts=("cdn.assets.example",),
    )
    assert result.links == ("https://CDN.Assets.Example/spec.pdf",)


def test_same_host_only_false_follows_any_host():
    html = '<a href="https://anywhere.example/spec.pdf">Anywhere</a>'
    result = harvest(html, base_url="https://x.com/", link_pattern=r"\.pdf$", same_host_only=False)
    assert result.links == ("https://anywhere.example/spec.pdf",)
    assert result.off_host == 0


def test_host_comparison_case_insensitive():
    html = '<a href="https://WWW.X.com/a.pdf">A</a>'
    result = harvest(html, base_url="https://www.x.com/", link_pattern=r"\.pdf$")
    assert result.links == ("https://WWW.X.com/a.pdf",)
    assert result.off_host == 0


# --------------------------------------------------------------------------- #
# Dedupe + cap (design §4.3/§4.6: cap is a counted truncation, never silent)
# --------------------------------------------------------------------------- #


def test_dedupes_repeated_link_preserving_first_seen_order():
    html = """
    <a href="https://x.com/b.pdf">nav</a>
    <a href="https://x.com/a.pdf">first</a>
    <a href="https://x.com/b.pdf">footer repeat</a>
    <a href="https://x.com/c.pdf">last</a>
    """
    result = harvest(html, base_url="https://x.com/", link_pattern=r"\.pdf$")
    assert result.links == ("https://x.com/b.pdf", "https://x.com/a.pdf", "https://x.com/c.pdf")
    # links_matched counts raw passes, before dedupe -- 4 hrefs matched, one a repeat.
    assert result.links_matched == 4
    assert result.links_capped == 0


def test_max_links_caps_and_counts_the_discard():
    html = "".join(f'<a href="https://x.com/{i}.pdf">{i}</a>' for i in range(10))
    result = harvest(html, base_url="https://x.com/", link_pattern=r"\.pdf$", max_links=3)
    assert len(result.links) == 3
    assert result.links == ("https://x.com/0.pdf", "https://x.com/1.pdf", "https://x.com/2.pdf")
    assert result.links_capped == 7
    assert result.links_matched == 10  # matched count is independent of the cap


def test_max_links_zero_discards_everything_and_counts_it():
    html = '<a href="https://x.com/a.pdf">A</a>'
    result = harvest(html, base_url="https://x.com/", link_pattern=r"\.pdf$", max_links=0)
    assert result.links == ()
    assert result.links_capped == 1


def test_max_links_negative_rejected():
    with pytest.raises(ValueError, match="max_links"):
        harvest("<a href='https://x.com/a.pdf'>A</a>", base_url="https://x.com/",
                 link_pattern=r"\.pdf$", max_links=-1)


# --------------------------------------------------------------------------- #
# Bad regex: caught at the edge, named, never a bare re.error
# --------------------------------------------------------------------------- #


def test_invalid_link_pattern_raises_named_value_error():
    with pytest.raises(ValueError, match=r"link_pattern is not a valid regex.*\(unterminated"):
        harvest("<a href='https://x.com/a.pdf'>A</a>", base_url="https://x.com/", link_pattern="(unterminated")


def test_invalid_link_pattern_is_not_a_bare_re_error():
    import re

    try:
        harvest("<a href='https://x.com/a.pdf'>A</a>", base_url="https://x.com/", link_pattern="[unclosed")
    except re.error as exc:
        # A bare re.error is not a ValueError -- if this branch runs at all,
        # the edge-catching contract was violated.
        assert isinstance(exc, ValueError), "re.error escaped uncaught, not wrapped as ValueError"
    except ValueError:
        pass
    else:
        pytest.fail("expected an exception for an invalid regex")


# --------------------------------------------------------------------------- #
# HarvestResult shape
# --------------------------------------------------------------------------- #


def test_harvest_result_is_frozen():
    result = harvest("<a href='https://x.com/a.pdf'>A</a>", base_url="https://x.com/", link_pattern=r"\.pdf$")
    assert isinstance(result, HarvestResult)
    with pytest.raises(Exception):
        result.links = ()  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Paginator (design §4.4)
# --------------------------------------------------------------------------- #


def test_paginator_first_url_is_index_url_unchanged():
    p = Paginator("https://x.com/downloads?cat=ifu", param="page", max_pages=5)
    assert p.first_url == "https://x.com/downloads?cat=ifu"


def test_paginator_generates_page_urls_preserving_existing_query():
    p = Paginator("https://x.com/downloads?cat=ifu", param="page", max_pages=5)
    next_url = p.record(["https://x.com/a.pdf", "https://x.com/b.pdf"])
    assert next_url == "https://x.com/downloads?cat=ifu&page=2"

    next_url = p.record(["https://x.com/c.pdf"])
    assert next_url == "https://x.com/downloads?cat=ifu&page=3"


def test_paginator_stops_at_max_pages():
    p = Paginator("https://x.com/downloads", param="page", max_pages=2)
    first = p.record(["https://x.com/a.pdf"])
    assert first == "https://x.com/downloads?page=2"
    second = p.record(["https://x.com/b.pdf"])
    assert second is None
    assert p.stopped_reason == "max-pages"


def test_paginator_stops_when_page_contributes_zero_new_links():
    p = Paginator("https://x.com/downloads", param="page", max_pages=20)
    p.record(["https://x.com/a.pdf", "https://x.com/b.pdf"])
    # Page 2 is a strict subset of what's already been seen -- no NEW links,
    # even though its set differs from page 1's (not an identical repeat).
    stop = p.record(["https://x.com/a.pdf"])
    assert stop is None
    assert p.stopped_reason == "no-new-links"


def test_paginator_stops_when_page_identical_to_previous():
    # The clamped-to-last-page shape named in the design (§4.4): a site that
    # returns the SAME nonempty set forever past the real last page.
    p = Paginator("https://x.com/downloads", param="page", max_pages=20)
    p.record(["https://x.com/a.pdf", "https://x.com/b.pdf"])
    stop = p.record(["https://x.com/a.pdf", "https://x.com/b.pdf"])
    assert stop is None
    assert p.stopped_reason == "repeated-page"


def test_paginator_continues_while_new_links_keep_appearing():
    p = Paginator("https://x.com/downloads", param="page", max_pages=20)
    urls = [p.first_url]
    pages = [
        ["https://x.com/a.pdf"],
        ["https://x.com/b.pdf"],
        ["https://x.com/c.pdf"],
    ]
    url = p.first_url
    for page_links in pages:
        url = p.record(page_links)
        if url is not None:
            urls.append(url)
    # 3 pages fed, each contributing a genuinely new link, well under
    # max_pages=20 -- every record() call returns a next-page URL, so the
    # 3rd call's return (page=4) is the URL for a page this test never feeds.
    # That is expected: Paginator has no way to know the fixture ran out of
    # pages, only the caller's fetch loop would (by getting nothing useful
    # back). This test pins "keeps advancing while new links appear", not a
    # stop condition -- those are covered by the dedicated tests below.
    assert urls == [
        "https://x.com/downloads",
        "https://x.com/downloads?page=2",
        "https://x.com/downloads?page=3",
        "https://x.com/downloads?page=4",
    ]
    assert p.stopped_reason is None


def test_paginator_max_pages_must_be_at_least_one():
    with pytest.raises(ValueError, match="max_pages"):
        Paginator("https://x.com/downloads", param="page", max_pages=0)


def test_paginator_single_page_config_never_advances():
    p = Paginator("https://x.com/downloads", param="page", max_pages=1)
    stop = p.record(["https://x.com/a.pdf"])
    assert stop is None
    assert p.stopped_reason == "max-pages"


# --- data-href: a row a script turns into a click is a link ----------------- #
#
# NSK's library, measured live 2026-09-04: 1.362 PDFs, every one on a
# `<tr data-href="...">`, and exactly FIVE `<a href>` anchors on the page. An
# anchor-only harvester reported "5 links, 0 matched" for a recipe that was
# correct, which is worse than reporting nothing -- it sends the operator to
# edit a pattern that was right.
#
# Blast radius measured BEFORE this landed, by re-harvesting every authored
# crawl index: both edenta pages gain exactly zero links, because neither uses
# the attribute. Followup [crawl-harvest-data-href].

_DATA_HREF = (
    "<html><body><table>"
    "<tr data-href='/docs/a.pdf'><td>A</td></tr>"
    "<tr data-href='/docs/b.pdf'><td>B</td></tr>"
    "<a href='/docs/c.pdf'>C</a>"
    "</table></body></html>"
)


def test_a_data_href_row_is_harvested_like_a_link():
    r = harvest(_DATA_HREF, base_url="https://x.example/lib",
                      link_pattern=r"\.pdf$")
    assert [u.rsplit("/", 1)[-1] for u in r.links] == ["a.pdf", "b.pdf", "c.pdf"]
    assert r.anchors_seen == 3


def test_a_stylesheet_link_href_is_still_not_a_document():
    """`href` stays anchor-only. A `<link href>` in the head is a stylesheet and
    a `<base href>` is the resolution root; widening `href` to every tag would
    harvest both."""
    html = ("<html><head><link rel='stylesheet' href='/x/style.pdf'>"
            "<base href='/y/base.pdf'></head>"
            "<body><a href='/docs/real.pdf'>R</a></body></html>")
    r = harvest(html, base_url="https://x.example/lib", link_pattern=r"\.pdf$")
    assert [u.rsplit("/", 1)[-1] for u in r.links] == ["real.pdf"]


def test_only_data_href_is_read_not_every_data_attribute():
    """`data-url` / `data-file` / `data-download` were measured on all three
    real index pages and appear on none of them. A speculative attribute list
    is how a harvester starts following things nobody meant it to."""
    html = ("<html><body>"
            "<div data-url='/docs/nope.pdf'></div>"
            "<div data-file='/docs/also-nope.pdf'></div>"
            "<tr data-href='/docs/yes.pdf'></tr>"
            "</body></html>")
    r = harvest(html, base_url="https://x.example/lib", link_pattern=r"\.pdf$")
    assert [u.rsplit("/", 1)[-1] for u in r.links] == ["yes.pdf"]
