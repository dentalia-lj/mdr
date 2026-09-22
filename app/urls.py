"""Shared URL normalization for the fetch dedupe key.

DISCOVER (S1.3) emits `fetch.url` with dedupe_key `fetch:{normalize_url(url)}`;
FETCH (S1.4) consumes that same key and writes `fetch_log.url_normalized`. If the
two derived the key differently they would re-fetch the same document (breaks
invariant 6 "unchanged content never re-fetched" and the active-scope dedupe of
invariant 8). So normalization lives here, once, and both import it — never a
per-handler re-implementation.

Conservative on purpose: query strings are kept verbatim (a `/download?id=` link
is identity-bearing), no `utm_*` stripping in the MVP (followup
`url-normalize-refine`). Assumes absolute http(s) URLs — search adapters only
ever return those.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

_DEFAULT_PORTS = {"http": "80", "https": "443"}


def normalize_url(url: str) -> str:
    """Canonical form: lowercase scheme + host, strip a default port and the URL
    fragment, drop a lone root trailing slash; path (case-sensitive) and query
    kept verbatim. Idempotent."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()

    netloc = host
    if parts.port is not None and str(parts.port) != _DEFAULT_PORTS.get(scheme):
        netloc = f"{host}:{parts.port}"

    path = parts.path
    if path == "/":  # only the root trailing slash — "/a/" may differ from "/a"
        path = ""

    return urlunsplit((scheme, netloc, path, parts.query, ""))


def domain_of(url: str) -> str:
    """The lowercased host — the `fetch.url` `domain` field and the domain-lease
    key (worker claims one job per domain per interval, PRD §4)."""
    return (urlsplit(url.strip()).hostname or "").lower()
