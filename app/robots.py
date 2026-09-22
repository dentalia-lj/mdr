"""robots.txt: ask permission before the crawl recipe fetches an index page.

Ruled 2026-08-21 (`docs/superpowers/specs/2026-08-21-playbook-crawl-recipe-design.md`
§4.1). Four manufacturers covering 822 catalogue items publish a written refusal,
and until now that refusal was enforced entirely by an operator choosing
`kind:"portal"` in a playbook. A crawl recipe introduces a second way to name a
fetch target, so the boundary had to stop depending on what a human remembers to
type: the four refused hosts were found by a deliberate sweep, and the next one
will not be.

So the pipeline makes exactly one request no playbook authored -- `robots.txt`
for a host it is about to crawl -- and that is the narrowest possible exception,
since it exists solely to find out whether the crawl is permitted.

Three things this module deliberately does:

* **Fetches through the `Fetcher` adapter**, never `urllib.request`. Invariant 11:
  nothing downstream of FETCH may know which transport is live, and
  `RobotFileParser.read()` would open its own socket with its own user agent.
  We parse a body we fetched ourselves.
* **Parses the rules here rather than with `urllib.robotparser`.** The stdlib
  parser has no wildcard support: `RuleLine.applies_to` is a bare
  `startswith`, so it reads `Disallow: /*.pdf` as a literal path beginning
  "/*.pdf" and answers ALLOWED for `/downloads/doc.pdf`. That is PRITIDENTA's
  actual robots.txt -- one of the four hosts we are forbidden to fetch, whose
  every document is behind exactly that rule. A reader that misses it is worse
  than no reader, because it reports permission we do not have. So this module
  implements RFC 9309 matching: `*` and `$` wildcards, longest-match wins,
  Allow beats Disallow on an equal-length tie.
* **Takes no domain lease.** The lease interval is what robots.txt is being read
  to determine, so waiting on it first is circular. One request per host per TTL
  window is the politeness argument instead, and it is a small one.
* **Fails closed.** A host we cannot reach is a host we have no permission from.
  Only 404/410 -- a site that positively has no robots.txt -- reads as allowed.
  But it fails closed BRIEFLY: a verdict reached without an answer (DNS
  failure, refused connection, 5xx) is cached for `UNREACHABLE_TTL_MINUTES`
  rather than `ttl_hours`, so a blip on our side cannot stand in for a
  manufacturer's decision for a day. See that constant.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.adapters.fetcher import _USER_AGENT, make_fetcher
from app.urls import domain_of

log = logging.getLogger("dentalia.robots")

#: Statuses that mean "this site publishes no robots.txt", which RFC 9309 reads
#: as full allow. Everything else that is not a 200 fails closed.
_NO_ROBOTS = (404, 410)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    crawl_delay_ms: int | None
    host: str
    status: int | None
    reason: str          # allow | disallow | no-robots | unreachable | parse-empty
    from_cache: bool


#: How long a TRANSPORT failure is allowed to stand in for an answer, against
#: `ttl_hours` for a real one. A 404 and a parsed body are facts about what the
#: host permits; a DNS or connection error is the absence of a fact, and
#: caching it for a day turns one blip into "this manufacturer refuses us"
#: until tomorrow. Found 2026-09-07: Docker's embedded resolver under WSL2
#: failed about one lookup in ten, and a probe of ultradent.com came back
#: `disallow (unreachable)` twice -- once for real, once served from the cache
#: the first one wrote. Still fails closed either way; it just stops being
#: durable. Short enough that a person re-probing gets a real attempt, long
#: enough that a genuinely dead host is not re-asked on every job.
UNREACHABLE_TTL_MINUTES = 5


def _cached(conn, host: str, ttl_hours: int):
    return conn.execute(
        "SELECT body, status, crawl_delay_ms FROM robots_cache "
        "WHERE host=%s AND fetched_at > now() - make_interval(hours => %s) "
        # A row keeps the long TTL only if it carries information about what
        # the host PERMITS: a body we parsed, or a 404/410 that positively says
        # there is no robots.txt. Everything else -- a DNS failure, a refused
        # connection, a 5xx -- is the absence of an answer and keeps the short
        # window. Same fail-closed verdict; it just stops being durable.
        "AND (body IS NOT NULL OR status = ANY(%s) "
        "     OR fetched_at > now() - make_interval(mins => %s))",
        (host, ttl_hours, list(_NO_ROBOTS), UNREACHABLE_TTL_MINUTES),
    ).fetchone()


def _store(conn, host: str, body: str | None, status: int | None,
           crawl_delay_ms: int | None) -> None:
    conn.execute(
        "INSERT INTO robots_cache (host, body, status, crawl_delay_ms, fetched_at) "
        "VALUES (%s,%s,%s,%s, now()) "
        "ON CONFLICT (host) DO UPDATE SET body=EXCLUDED.body, status=EXCLUDED.status, "
        "crawl_delay_ms=EXCLUDED.crawl_delay_ms, fetched_at=now()",
        (host, body, status, crawl_delay_ms),
    )
    # Project the delay onto the politeness lease. Written here and nowhere else:
    # `politeness_ms` is rewritten from the caller on every acquire, so a per-host
    # value has to live in a column acquires never touch (migration 036). A host
    # that has DROPPED its Crawl-delay since the last read is lowered back to 0 --
    # that is the TTL refresh doing its job, not a lost setting.
    conn.execute(
        "INSERT INTO domain_lease (domain, min_politeness_ms) VALUES (%s,%s) "
        "ON CONFLICT (domain) DO UPDATE SET min_politeness_ms=EXCLUDED.min_politeness_ms",
        (host, crawl_delay_ms or 0),
    )


#: Our agent as robots.txt names it: the token before the "/", lowercased.
_UA_TOKEN = _USER_AGENT.split("/")[0].strip().lower()


def _compile(pattern: str) -> re.Pattern:
    """RFC 9309 path matching: `*` is any run of characters, a trailing `$`
    anchors the end. Everything else is literal."""
    out = []
    for i, ch in enumerate(pattern):
        if ch == "*":
            out.append(".*")
        elif ch == "$" and i == len(pattern) - 1:
            out.append("$")
        else:
            out.append(re.escape(ch))
    return re.compile("".join(out))


@dataclass(frozen=True)
class _Rule:
    allow: bool
    pattern: str
    regex: re.Pattern


class _Rules:
    """The group of rules that applies to us, plus its Crawl-delay.

    Group selection follows RFC 9309: the record naming our agent wins outright,
    `*` is the fallback, and a robots.txt with neither leaves us unconstrained.
    """

    def __init__(self, rules: list[_Rule], crawl_delay: float | None):
        self._rules = rules
        self.crawl_delay = crawl_delay

    def allows(self, url: str) -> bool:
        parts = urlsplit(url)
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"
        best: _Rule | None = None
        for r in self._rules:
            if not r.regex.match(path):
                continue
            # Longest matching pattern wins; on an equal-length tie the Allow
            # wins, which is what lets a site carve an exception out of a broad
            # Disallow rather than the other way round.
            if (best is None
                    or len(r.pattern) > len(best.pattern)
                    or (len(r.pattern) == len(best.pattern) and r.allow)):
                best = r
        return True if best is None else best.allow


def _parse(body: str) -> _Rules:
    """Group the file by User-agent and keep the group that applies to us.

    Consecutive `User-agent:` lines share one group, and a group ends at the
    first rule line following them -- the shape every real robots.txt uses.
    """
    groups: dict[str, list[_Rule]] = {}
    delays: dict[str, float] = {}
    current: list[str] = []
    starting = False   # are we still collecting agent names for this group?

    for raw in body.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field, value = field.strip().lower(), value.strip()

        if field == "user-agent":
            if not starting:
                current = []
                starting = True
            current.append(value.lower())
            groups.setdefault(value.lower(), [])
            continue

        if not current:
            continue          # a rule before any User-agent line: not ours
        starting = False

        if field in ("allow", "disallow"):
            if field == "disallow" and value == "":
                continue      # "Disallow:" with no path constrains nothing
            if value == "":
                continue
            rule = _Rule(field == "allow", value, _compile(value))
            for agent in current:
                groups[agent].append(rule)
        elif field == "crawl-delay":
            try:
                for agent in current:
                    delays[agent] = float(value)
            except ValueError:
                log.warning("robots: unparseable Crawl-delay %r", value)

    for agent in (_UA_TOKEN, "*"):
        if agent in groups:
            return _Rules(groups[agent], delays.get(agent))
    return _Rules([], None)


def check(conn, url: str, *, fetcher=None, ttl_hours: int = 24) -> Decision:
    """May we fetch `url`, and how slowly? Cached per host for `ttl_hours`.

    The caller owns the transaction, as everywhere else here. A cache write and
    the `min_politeness_ms` projection commit with whatever the caller commits.
    """
    host = domain_of(url)
    if not host:
        return Decision(False, None, host, None, "unreachable", False)

    row = _cached(conn, host, ttl_hours)
    if row is not None:
        if row["status"] in _NO_ROBOTS:
            return Decision(True, None, host, row["status"], "no-robots", True)
        if row["body"] is None:
            return Decision(False, None, host, row["status"], "unreachable", True)
        ok = _parse(row["body"]).allows(url)
        return Decision(ok, row["crawl_delay_ms"], host, row["status"],
                        "allow" if ok else "disallow", True)

    fetcher = fetcher or make_fetcher()
    robots_url = f"https://{host}/robots.txt"
    try:
        resp = fetcher.get(robots_url)
        status, body = resp.status, resp.body
    except Exception:  # noqa: BLE001 - an unreachable host is a refusal, not a crash
        log.warning("robots: %s unreachable", robots_url, exc_info=True)
        _store(conn, host, None, None, None)
        return Decision(False, None, host, None, "unreachable", False)

    if status in _NO_ROBOTS:
        _store(conn, host, None, status, None)
        return Decision(True, None, host, status, "no-robots", False)

    if status != 200 or body is None:
        _store(conn, host, None, status, None)
        return Decision(False, None, host, status, "unreachable", False)

    text = body.decode("utf-8", errors="replace")
    rules = _parse(text)
    delay_ms = int(rules.crawl_delay * 1000) if rules.crawl_delay is not None else None
    _store(conn, host, text, status, delay_ms)
    ok = rules.allows(url)
    return Decision(ok, delay_ms, host, status, "allow" if ok else "disallow", False)
