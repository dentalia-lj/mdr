"""app/robots.py -- the one request no playbook authored.

The ruling these pin (2026-08-21, §4.1 of the crawl-recipe design): the boundary
that keeps the fetcher off a host that refused it must not depend on what a human
remembers to type in a playbook. So the checks are: a refusal is obeyed, an
unreachable host is a refusal, a Crawl-delay reaches the politeness lease, and
none of it costs more than one request per host per TTL window.
"""
from __future__ import annotations

import pytest

from app import robots
from app.adapters.fetcher import FetchResult

BLANKET_REFUSAL = "User-agent: *\nDisallow: /\n"
NAMED_REFUSAL = "User-agent: DentaliaComplianceBot\nDisallow: /\n"
PDF_REFUSAL = "User-agent: *\nDisallow: /*.pdf\n"
WITH_DELAY = "User-agent: *\nCrawl-delay: 10\nAllow: /\n"


class FakeFetcher:
    """Counts calls: the TTL cache is only worth having if it stops requests."""

    def __init__(self, status=200, body=BLANKET_REFUSAL, raises=False):
        self.status, self.body, self.raises = status, body, raises
        self.calls: list[str] = []

    def get(self, url, *, etag=None, last_modified=None):
        self.calls.append(url)
        if self.raises:
            raise ConnectionError("no route to host")
        return FetchResult(
            status=self.status,
            body=self.body.encode() if self.body is not None else None,
            etag=None, last_modified=None, content_type="text/plain", final_url=url,
        )


def _floor(conn, host):
    row = conn.execute(
        "SELECT min_politeness_ms FROM domain_lease WHERE domain=%s", (host,)
    ).fetchone()
    return row["min_politeness_ms"] if row else None


# --- the refusal itself ----------------------------------------------------- #

def test_a_blanket_disallow_refuses(conn):
    d = robots.check(conn, "https://media.example/doc.pdf", fetcher=FakeFetcher())
    assert d.allowed is False
    assert d.reason == "disallow"


def test_a_refusal_naming_our_bot_refuses(conn):
    """AMANN GIRRBACH-shaped: the DAM disallows everything. COLTENE's names a
    specific agent, which is the shape that must still match us when it is ours."""
    d = robots.check(conn, "https://dam.example/x.pdf",
                     fetcher=FakeFetcher(body=NAMED_REFUSAL))
    assert d.allowed is False


def test_a_path_scoped_disallow_refuses_only_that_path(conn):
    """PRITIDENTA-shaped: `Disallow: /*.pdf` bars every document while leaving
    the HTML listing crawlable -- so the answer must be per URL, not per host."""
    f = FakeFetcher(body=PDF_REFUSAL)
    assert robots.check(conn, "https://p.example/downloads/doc.pdf", fetcher=f).allowed is False
    assert robots.check(conn, "https://p.example/downloads/", fetcher=f).allowed is True


def test_an_empty_robots_allows(conn):
    """Edenta's real robots.txt: `User-agent: *` with no rules at all."""
    d = robots.check(conn, "https://edenta.example/downloads",
                     fetcher=FakeFetcher(body="User-agent: *\n"))
    assert d.allowed is True


def test_no_robots_file_allows(conn):
    d = robots.check(conn, "https://nofile.example/x", fetcher=FakeFetcher(status=404, body=None))
    assert (d.allowed, d.reason) == (True, "no-robots")


# --- failing closed --------------------------------------------------------- #

def test_an_unreachable_host_is_a_refusal(conn):
    """A host we cannot reach is a host we have no permission from. The
    alternative -- allow on error -- turns one DNS blip into a crawl of a site
    that may well have refused us."""
    d = robots.check(conn, "https://down.example/x", fetcher=FakeFetcher(raises=True))
    assert (d.allowed, d.reason) == (False, "unreachable")


def test_a_500_is_a_refusal(conn):
    d = robots.check(conn, "https://broken.example/x", fetcher=FakeFetcher(status=500, body=None))
    assert d.allowed is False


# --- the Crawl-delay reaching the lease ------------------------------------- #

def test_a_crawl_delay_becomes_the_lease_floor(conn):
    d = robots.check(conn, "https://slow.example/x", fetcher=FakeFetcher(body=WITH_DELAY))
    assert d.allowed is True
    assert d.crawl_delay_ms == 10_000
    assert _floor(conn, "slow.example") == 10_000


def test_a_host_without_a_delay_gets_a_zero_floor(conn):
    robots.check(conn, "https://plain.example/x", fetcher=FakeFetcher(body="User-agent: *\n"))
    assert _floor(conn, "plain.example") == 0


def test_a_dropped_delay_lowers_the_floor_again(conn):
    """The TTL refresh is what makes the floor recoverable: a site that removes
    its Crawl-delay must not stay slow forever."""
    robots.check(conn, "https://was-slow.example/x", fetcher=FakeFetcher(body=WITH_DELAY))
    assert _floor(conn, "was-slow.example") == 10_000
    conn.execute("UPDATE robots_cache SET fetched_at = now() - interval '48 hours'")
    robots.check(conn, "https://was-slow.example/x",
                 fetcher=FakeFetcher(body="User-agent: *\n"))
    assert _floor(conn, "was-slow.example") == 0


def test_the_floor_does_not_disturb_an_existing_lease(conn):
    """Written into a live table: the projection must not free a held lease or
    rewrite the politeness the last acquire set."""
    from app import queue
    assert queue.try_domain_lease(conn, "held.example", 2000) is True
    robots.check(conn, "https://held.example/x", fetcher=FakeFetcher(body=WITH_DELAY))
    row = conn.execute(
        "SELECT politeness_ms, min_politeness_ms, leased_until > now() AS held "
        "FROM domain_lease WHERE domain=%s", ("held.example",)
    ).fetchone()
    assert (row["politeness_ms"], row["min_politeness_ms"], row["held"]) == (2000, 10_000, True)


# --- the cache -------------------------------------------------------------- #

def test_the_second_check_costs_no_request(conn):
    f = FakeFetcher()
    robots.check(conn, "https://cached.example/a", fetcher=f)
    robots.check(conn, "https://cached.example/b", fetcher=f)
    assert f.calls == ["https://cached.example/robots.txt"]


def test_an_expired_entry_is_refetched(conn):
    f = FakeFetcher()
    robots.check(conn, "https://stale.example/a", fetcher=f)
    conn.execute("UPDATE robots_cache SET fetched_at = now() - interval '48 hours'")
    robots.check(conn, "https://stale.example/a", fetcher=f)
    assert len(f.calls) == 2


def test_the_ttl_is_the_callers(conn):
    f = FakeFetcher()
    robots.check(conn, "https://ttl.example/a", fetcher=f, ttl_hours=24)
    conn.execute("UPDATE robots_cache SET fetched_at = now() - interval '2 hours'")
    robots.check(conn, "https://ttl.example/a", fetcher=f, ttl_hours=1)
    assert len(f.calls) == 2


def test_a_cached_refusal_still_refuses(conn):
    f = FakeFetcher(body=PDF_REFUSAL)
    robots.check(conn, "https://c.example/a.pdf", fetcher=f)
    d = robots.check(conn, "https://c.example/b.pdf", fetcher=f)
    assert (d.allowed, d.from_cache) == (False, True)


def test_a_cached_unreachable_still_refuses(conn):
    f = FakeFetcher(raises=True)
    robots.check(conn, "https://gone.example/a", fetcher=f)
    d = robots.check(conn, "https://gone.example/b", fetcher=f)
    assert (d.allowed, d.reason, d.from_cache) == (False, "unreachable", True)


# --- how long a non-answer is allowed to stand (2026-09-07) ------------------ #
#
# The bug this pins: Docker's embedded resolver under WSL2 dropped about one
# lookup in ten, a probe of ultradent.com came back `disallow (unreachable)`,
# and the NEXT probe got the same verdict from the cache the first one wrote --
# no retry, for 24 hours, over a DNS blip. Failing closed is right; failing
# closed for a day on no evidence is not.

def test_a_transport_failure_is_retried_after_its_short_window(conn):
    f = FakeFetcher(raises=True)
    robots.check(conn, "https://blip.example/a", fetcher=f)
    conn.execute("UPDATE robots_cache SET fetched_at = now() - make_interval("
                 "mins => %s)", (robots.UNREACHABLE_TTL_MINUTES + 1,))
    d = robots.check(conn, "https://blip.example/a", fetcher=f)
    assert len(f.calls) == 2, "a DNS failure stood in for an answer past its window"
    assert (d.allowed, d.reason, d.from_cache) == (False, "unreachable", False)


def test_a_transport_failure_is_still_cached_inside_that_window(conn):
    """Not zero-cached: a genuinely dead host must not be re-asked by every job
    that comes past. The window is short, not absent."""
    f = FakeFetcher(raises=True)
    robots.check(conn, "https://dead.example/a", fetcher=f)
    d = robots.check(conn, "https://dead.example/b", fetcher=f)
    assert (len(f.calls), d.from_cache) == (1, True)


def test_a_5xx_keeps_the_short_window_too(conn):
    """A 500 is the server saying it is unwell, not what it permits -- the same
    absence of an answer as a refused connection."""
    f = FakeFetcher(status=500, body=None)
    robots.check(conn, "https://sick.example/a", fetcher=f)
    conn.execute("UPDATE robots_cache SET fetched_at = now() - make_interval("
                 "mins => %s)", (robots.UNREACHABLE_TTL_MINUTES + 1,))
    robots.check(conn, "https://sick.example/a", fetcher=f)
    assert len(f.calls) == 2


def test_a_404_keeps_the_FULL_ttl(conn):
    """The other side of the same clause. 404 is a POSITIVE fact -- this site
    has no robots.txt -- so it is worth a day like any parsed body, even though
    it also stores a null body."""
    f = FakeFetcher(status=404, body=None)
    robots.check(conn, "https://norobots.example/a", fetcher=f)
    conn.execute("UPDATE robots_cache SET fetched_at = now() - make_interval("
                 "mins => %s)", (robots.UNREACHABLE_TTL_MINUTES + 1,))
    d = robots.check(conn, "https://norobots.example/a", fetcher=f)
    assert len(f.calls) == 1, "a 404 was re-asked as though it were a failure"
    assert (d.allowed, d.reason, d.from_cache) == (True, "no-robots", True)


def test_a_disallow_keeps_the_full_ttl(conn):
    """And a real refusal is durable, which is the whole point of separating
    them: the manufacturer's decision outlives our network."""
    f = FakeFetcher(body=PDF_REFUSAL)
    robots.check(conn, "https://no.example/a.pdf", fetcher=f)
    conn.execute("UPDATE robots_cache SET fetched_at = now() - make_interval("
                 "mins => %s)", (robots.UNREACHABLE_TTL_MINUTES + 1,))
    d = robots.check(conn, "https://no.example/b.pdf", fetcher=f)
    assert len(f.calls) == 1
    assert (d.allowed, d.from_cache) == (False, True)


def test_a_url_with_no_host_is_refused_without_a_request(conn):
    f = FakeFetcher()
    d = robots.check(conn, "not-a-url", fetcher=f)
    assert (d.allowed, f.calls) == (False, [])


# --- RFC 9309 matching, which is ours now ----------------------------------- #
# The stdlib parser answers these wrong (`RuleLine.applies_to` is a bare
# startswith, so `/*.pdf` is a literal prefix). PRITIDENTA's real robots.txt is
# the first row: every document on the host is behind that one rule.
_MATCHING = [
    ("pritidenta: wildcard extension",
     "User-agent: *\nDisallow: /*.pdf\n",
     [("/fileadmin/Konformitaetserklaerung_MDR.pdf", False),
      ("/en/downloads/", True)]),
    ("dollar anchors the end",
     "User-agent: *\nDisallow: /*.pdf$\n",
     [("/a/b.pdf", False), ("/a/b.pdf?v=2", True)]),
    ("medentika: a long literal prefix",
     "User-agent: *\nDisallow: /typo3conf/ext/bit_medentika/Resources/Public/Files/Ifu/\n",
     [("/typo3conf/ext/bit_medentika/Resources/Public/Files/Ifu/x.pdf", False),
      ("/typo3conf/ext/other/x.pdf", True)]),
    ("longest match wins, so an Allow can carve out of a Disallow",
     "User-agent: *\nDisallow: /downloads/\nAllow: /downloads/public/\n",
     [("/downloads/secret.pdf", False), ("/downloads/public/ifu.pdf", True)]),
    ("equal-length tie goes to Allow",
     "User-agent: *\nDisallow: /a\nAllow: /a\n",
     [("/a", True)]),
    ("our own group beats the wildcard group",
     "User-agent: *\nDisallow: /\n\nUser-agent: DentaliaComplianceBot\nAllow: /\n",
     [("/anything", True)]),
    ("a group naming someone else does not bind us",
     "User-agent: ClaudeBot\nDisallow: /\n",
     [("/anything", True)]),
    ("an empty Disallow constrains nothing",
     "User-agent: *\nDisallow:\n",
     [("/anything", True)]),
    ("comments are stripped",
     "User-agent: *  # everyone\nDisallow: /x  # not here\n",
     [("/x/y", False), ("/y", True)]),
    ("consecutive agent lines share one group",
     "User-agent: SomeBot\nUser-agent: DentaliaComplianceBot\nDisallow: /shared/\n",
     [("/shared/a", False), ("/other", True)]),
]


@pytest.mark.parametrize("label,body,cases",
                         _MATCHING, ids=[m[0] for m in _MATCHING])
def test_rule_matching(conn, label, body, cases):
    f = FakeFetcher(body=body)
    for path, expected in cases:
        d = robots.check(conn, f"https://m.example{path}", fetcher=f)
        assert d.allowed is expected, f"{label}: {path}"


def test_crawl_delay_comes_from_our_group_not_the_wildcard(conn):
    body = ("User-agent: *\nCrawl-delay: 30\n\n"
            "User-agent: DentaliaComplianceBot\nCrawl-delay: 2\nAllow: /\n")
    d = robots.check(conn, "https://d.example/x", fetcher=FakeFetcher(body=body))
    assert d.crawl_delay_ms == 2000


def test_a_fractional_crawl_delay_survives_as_milliseconds(conn):
    d = robots.check(conn, "https://frac.example/x",
                     fetcher=FakeFetcher(body="User-agent: *\nCrawl-delay: 0.5\n"))
    assert d.crawl_delay_ms == 500


def test_an_unparseable_crawl_delay_is_ignored_not_fatal(conn):
    d = robots.check(conn, "https://bad.example/x",
                     fetcher=FakeFetcher(body="User-agent: *\nCrawl-delay: soon\n"))
    assert (d.allowed, d.crawl_delay_ms) == (True, None)
