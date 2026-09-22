"""Shared URL normalization (app/urls.py).

DISCOVER emits `fetch.url` with dedupe_key `fetch:{normalize_url(url)}`; FETCH
(S1.4) consumes the same key. A mismatch between the two would re-fetch the same
document (breaks invariant 6/8), so the normalizer is a single shared function
both import. These tests pin the canonical form.
"""

from __future__ import annotations

from app import urls


def test_lowercases_scheme_and_host_preserves_path_case():
    assert urls.normalize_url("HTTPS://Ivoclar.COM/Path/Doc.PDF") == "https://ivoclar.com/Path/Doc.PDF"


def test_strips_default_ports():
    assert urls.normalize_url("https://x.com:443/a") == "https://x.com/a"
    assert urls.normalize_url("http://x.com:80/a") == "http://x.com/a"


def test_keeps_nondefault_port():
    assert urls.normalize_url("https://x.com:8443/a") == "https://x.com:8443/a"


def test_strips_fragment_keeps_query():
    assert urls.normalize_url("https://x.com/d?id=5#frag") == "https://x.com/d?id=5"


def test_drops_root_trailing_slash_only():
    assert urls.normalize_url("https://x.com/") == "https://x.com"
    assert urls.normalize_url("https://x.com/a/") == "https://x.com/a/"


def test_idempotent():
    once = urls.normalize_url("HTTPS://X.com:443/A?q=1#f")
    assert urls.normalize_url(once) == once


def test_domain_of_lowercases_host_keeps_subdomain():
    assert urls.domain_of("https://www.Ivoclar.com/a") == "www.ivoclar.com"


def test_domain_of_strips_default_port():
    assert urls.domain_of("https://x.com:443/a") == "x.com"
