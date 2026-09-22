"""Pure link harvester for the playbook `crawl` recipe (design item 11 /
`docs/superpowers/specs/2026-08-21-playbook-crawl-recipe-design.md`).

This module is a PURE FUNCTION LIBRARY: HTML string in, links + counts out.
No I/O, no DB, no network, no robots.txt, no domain lease, no knowledge of
what a `Playbook` or a `crawl` block is -- all of that belongs to the DISCOVER
`playbook` rung (design §3 steps 3-5), which is a later slice and consumes
this module rather than being part of it. Kept pure on purpose: two different
callers share it -- the DISCOVER rung (emits `fetch.url` per surviving link)
and a UI probe handler that only shows an operator what a pattern would find,
emitting nothing (design intro, "the boundary").

Parsing: stdlib `html.parser.HTMLParser`, per the ladder ruled 2026-08-21
(design §4.2) -- "stdlib first, escalate only a page that yields no anchors at
all". The escalation tier (Playwright render) is a DISCOVER-rung concern, not
this module's; `harvest()` reports `anchors_seen == 0` and lets the caller
decide to re-fetch with a renderer.

Resolution order matters and is spec-pinned (design §3 step 6 / §4.2): every
href is resolved to an absolute URL via `urljoin(base_url, href)` BEFORE
`link_pattern` is matched. Edenta's document URLs
(`/zoolu-website/media/document/{id}/{Title}`, no `.pdf` extension -- design
§4.5) are the reason this module never assumes a file extension anywhere.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

__all__ = [
    "HarvestResult",
    "harvest",
    "Paginator",
]


class _AnchorHrefParser(HTMLParser):
    """Collects every `href` value from `<a>` tags, in document order, exactly
    as authored -- no resolution, no filtering. That happens in `harvest()`,
    because resolution must happen before `link_pattern` is matched (module
    docstring), and this class has no `base_url` to resolve against.

    Deliberately tolerant per design §4.2 ("must not raise on real-world
    HTML"): stdlib HTMLParser recovers from unclosed tags and bad nesting
    rather than raising, which is exactly the required behaviour -- nothing
    extra needed here beyond not raising ourselves.
    """

    #: Attributes that name a URL a click actually follows, in source order of
    #: preference. `href` on an `<a>` is the ordinary case. `data-href` is here
    #: because NSK's library -- measured 2026-09-04 -- puts all 1.362 of its
    #: PDFs on `<tr data-href="...">` with a script click handler and has
    #: exactly FIVE `<a href>` anchors on the page. To a person those rows ARE
    #: links; to an anchor-only harvester the library is empty, and the probe
    #: reported a confident zero for a recipe that was correct.
    #:
    #: Measured blast radius before adding it: BOTH authored crawl recipes
    #: (edenta, two index pages) gain exactly ZERO links, because neither page
    #: uses the attribute at all. `data-url` / `data-file` / `data-download`
    #: were measured at the same time and appear on none of the three pages, so
    #: they are deliberately NOT here -- a speculative attribute list is how a
    #: harvester starts following things nobody meant it to.
    LINK_ATTRS = ("href", "data-href")

    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []
        #: Parallel to `hrefs`: the text inside the link-bearing element, raw.
        #: A ranker needs it -- `/uploads/2023/1234.pdf` says nothing, the
        #: words a person clicks on say "Declaration of Conformity". Taken from
        #: the element that CARRIES the link, so a `<tr data-href>` row gives
        #: its cells and a nested `<a>` gives its own text to both.
        self.texts: list[str] = []
        self._open: list[tuple[str, int]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # `href` stays anchor-only: a `<link href>` in the document head is a
        # stylesheet, not a document, and a `<base href>` is the resolution
        # root. `data-href` is taken from ANY tag, because the whole point is
        # that the element carrying it is not an `<a>`.
        for name, value in attrs:
            if not value or name not in self.LINK_ATTRS:
                continue
            if name == "href" and tag != "a":
                continue
            # A bare `<a name="x">` or `<a href="">` is not a link (§ below:
            # anchors_seen counts link-bearing tags, not every `<a>`). `break`
            # after the first match: a tag cannot carry the attribute twice in
            # a way that matters here.
            self.hrefs.append(value)
            self.texts.append("")
            self._open.append((tag, len(self.hrefs) - 1))
            break

    def handle_data(self, data: str) -> None:
        if self._open and data.strip():
            for _, i in self._open:
                self.texts[i] += " " + data

    def handle_endtag(self, tag: str) -> None:
        for k in range(len(self._open) - 1, -1, -1):
            if self._open[k][0] == tag:
                del self._open[k]
                break


@dataclass(frozen=True)
class HarvestResult:
    """Design §3 API sketch. Every count is BEFORE dedupe/cap unless its own
    name says otherwise, so an author can see exactly which stage discarded
    what -- never-silent counting, design §4.6."""

    links: tuple[str, ...]      # resolved, absolute, deduped, capped, first-seen order
    # `<a href>` plus `data-href` on any tag (see LINK_ATTRS), BEFORE any
    # filtering. The name predates `data-href` and is kept: it is what the
    # crawl design, the probe row's `counts` jsonb and the UI all say.
    anchors_seen: int
    links_matched: int          # passed link_pattern + host filter (pre-dedupe count)
    links_capped: int           # matched-and-unique links discarded by max_links
    off_host: int                # matched-by-pattern hrefs rejected for host
    # URL -> the whitespace-normalised text of the element that carried the
    # link, capped at 200 characters, for every entry of `links`. Empty for a
    # bare `<a href>`; never absent. Added 2026-09-04 for the ranked triage.
    texts: dict[str, str] = field(default_factory=dict)


def harvest(
    html: str,
    *,
    base_url: str,
    link_pattern: str,
    same_host_only: bool = True,
    allow_hosts: tuple[str, ...] = (),
    max_links: int = 200,
) -> HarvestResult:
    """Parse `html`, resolve every `<a href>` against `base_url`, keep the
    ones whose RESOLVED absolute URL matches `link_pattern` and (if
    `same_host_only`) whose host is `base_url`'s host or in `allow_hosts`.

    `allow_hosts` is the ONLY host-widening mechanism (design §4.3, ruled
    2026-08-21) -- there is no "follow anything" flag. Compared case-
    insensitively, same as `same_host_only`'s own comparison.

    Raises `ValueError` if `link_pattern` does not compile -- caught HERE, at
    the edge, before any HTML is walked, so a bad authored regex never
    surfaces as a bare `re.error` from inside a loop (design requirement,
    mirrors the same ladder `app/playbooks.py:_parse` already uses for
    `cert_number_pattern` / `ref_pattern` / `companion.*`).
    """
    if max_links < 0:
        raise ValueError(f"max_links must be >= 0, got {max_links}")

    try:
        pattern = re.compile(link_pattern)
    except re.error as exc:
        raise ValueError(
            f"link_pattern is not a valid regex: {link_pattern!r}: {exc}"
        ) from exc

    parser = _AnchorHrefParser()
    parser.feed(html)
    parser.close()
    anchors_seen = len(parser.hrefs)

    base_host = (urlsplit(base_url).hostname or "").lower()
    allowed_hosts = {base_host, *(h.lower() for h in allow_hosts)}

    links_matched = 0
    off_host = 0
    seen: set[str] = set()
    ordered: list[str] = []
    text_of: dict[str, str] = {}

    for href, text in zip(parser.hrefs, parser.texts):
        # Resolve BEFORE matching -- the common case is a relative href, and
        # `link_pattern` is authored against what the document actually lives
        # at (design §3: "matched against the RESOLVED ABSOLUTE url").
        resolved = urljoin(base_url, href)
        if not pattern.search(resolved):
            continue

        if same_host_only:
            host = (urlsplit(resolved).hostname or "").lower()
            if host not in allowed_hosts:
                off_host += 1
                continue

        links_matched += 1
        if resolved in seen:
            continue
        seen.add(resolved)
        ordered.append(resolved)
        text_of[resolved] = " ".join(text.split())[:200]

    # links_capped is a COUNTED truncation, never a silent slice (design
    # §4.3/§4.6): report exactly how many unique, matched links max_links
    # discarded, distinct from links_matched (which is pre-dedupe).
    links_capped = max(0, len(ordered) - max_links)
    links = tuple(ordered[:max_links])

    return HarvestResult(
        links=links,
        anchors_seen=anchors_seen,
        links_matched=links_matched,
        links_capped=links_capped,
        off_host=off_host,
        texts={u: text_of[u] for u in links},
    )


def _page_url(index_url: str, param: str, page: int) -> str:
    """URL for `page` (1-indexed). Page 1 is `index_url` UNCHANGED -- it is
    the URL an author actually wrote, and a site's real page-1 form does not
    always accept `?page=1` (some 404, some 0-index, some just don't need
    it). Page >=2 sets/replaces `param` in the existing query string,
    preserving every other query param verbatim (a listing page filtered by
    `?cat=ifu` must stay filtered across pages)."""
    if page <= 1:
        return index_url
    parts = urlsplit(index_url)
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != param]
    kept.append((param, str(page)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment))


class Paginator:
    """Drives the three design §4.4 stop conditions across pages the CALLER
    fetches. Pure: it never fetches and never sees HTML. The caller loop is

        p = Paginator(index_url, param="page", max_pages=20)
        url = p.first_url
        while url is not None:
            html = fetch(url)                              # caller's I/O
            result = harvest(html, base_url=url, ...)       # this module
            url = p.record(result.links)

    Stop conditions (design §4.4, "all three are needed"):
      1. `max_pages` reached.
      2. A page contributes zero links NEW to the cumulative set harvested so
         far across the whole crawl (a strict subset of an earlier page, or a
         page whose content the crawl has already fully absorbed).
      3. A page's link set is IDENTICAL to the immediately preceding page's --
         named separately in the design as "the common infinite-pagination
         shape: a site that clamps out-of-range pages to the last one". It is
         checked before condition 2 (an identical repeat is always also a
         zero-new page under this module's cumulative accounting) purely so
         `stopped_reason` names the more specific, more common cause.

      Ambiguity resolved here, flagged for review: the design text does not
      say whether "zero new links" is cumulative-across-the-crawl or
      relative-only-to-the-immediately-previous-page. Cumulative is what is
      implemented -- it is the stronger guarantee ("nothing new is being
      found at all"), and condition 3 stays as an independent, cheaper check
      for the specific clamped-page shape rather than being subsumed away.
    """

    def __init__(self, index_url: str, *, param: str, max_pages: int) -> None:
        if max_pages < 1:
            raise ValueError(f"max_pages must be >= 1, got {max_pages}")
        self._index_url = index_url
        self._param = param
        self._max_pages = max_pages
        self._pages_recorded = 0
        self._seen: set[str] = set()
        self._prev_page_links: frozenset[str] | None = None
        self.stopped_reason: str | None = None

    @property
    def first_url(self) -> str:
        return self._index_url

    def record(self, page_links: Sequence[str]) -> str | None:
        """Call once per fetched page, in order, starting with page 1's
        links. Returns the next page's URL, or `None` if the crawl should
        stop -- see the class docstring for why. Idempotent bookkeeping is
        NOT guaranteed for a page recorded twice; callers must not re-record."""
        self._pages_recorded += 1
        links_set = frozenset(page_links)

        new_links = links_set - self._seen
        self._seen |= links_set

        if self._prev_page_links is not None and links_set == self._prev_page_links:
            self.stopped_reason = "repeated-page"
            next_url: str | None = None
        elif not new_links:
            self.stopped_reason = "no-new-links"
            next_url = None
        elif self._pages_recorded >= self._max_pages:
            self.stopped_reason = "max-pages"
            next_url = None
        else:
            self.stopped_reason = None
            next_url = _page_url(self._index_url, self._param, self._pages_recorded + 1)

        self._prev_page_links = links_set
        return next_url
