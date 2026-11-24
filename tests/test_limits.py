"""Webstore rate limits (2026-11-23 webshop API spec § 5.5).

Block per customer IP on downloads, alert per key, a high per-key ceiling on
the API against a loop. Erpium has ONE key for all its customers, so anything
that blocks on the key blocks every customer at once; the tests below pin that
the alert never blocks.
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from web.limits import Limiter

LJ = ZoneInfo("Europe/Ljubljana")


class Clock:
    def __init__(self, t: float):
        self.t = t

    def __call__(self) -> float:
        return self.t


def _at(*args) -> float:
    return datetime(*args, tzinfo=LJ).timestamp()


def _limiter(clock, **over):
    kw = dict(api_per_min=1200, ip_docs_per_min=30, ip_docs_per_day=100,
              key_alert_per_day=200, clock=clock)
    kw.update(over)
    return Limiter(**kw)


def test_api_ceiling_per_minute():
    clock = Clock(_at(2050, 6, 1, 10, 0, 0))
    lim = _limiter(clock)
    assert all(lim.check_api("k1") is None for _ in range(1200))
    retry = lim.check_api("k1")
    assert retry is not None and 0 < retry <= 60
    # another key is not affected
    assert lim.check_api("k2") is None
    clock.t += 60
    assert lim.check_api("k1") is None


def test_ip_per_minute_counts_distinct_documents():
    clock = Clock(_at(2050, 6, 1, 10, 0, 0))
    lim = _limiter(clock)
    assert all(lim.check_download("203.0.113.9", d) is None for d in range(30))
    assert lim.check_download("203.0.113.9", 30) is not None
    # another customer is not affected
    assert lim.check_download("198.51.100.1", 30) is None


def test_repeat_document_is_free():
    """A PDF viewer fetching byte ranges of one file, or a clinic opening the
    same certificate twice, costs one."""
    lim = _limiter(Clock(_at(2050, 6, 1, 10, 0, 0)), ip_docs_per_min=1, ip_docs_per_day=1)
    assert all(lim.check_download("203.0.113.9", 7) is None for _ in range(500))


def test_a_repeat_after_the_minute_is_still_free_today():
    clock = Clock(_at(2050, 6, 1, 10, 0, 0))
    lim = _limiter(clock, ip_docs_per_min=1)
    assert lim.check_download("203.0.113.9", 7) is None
    clock.t += 120
    assert lim.check_download("203.0.113.9", 7) is None


def test_ip_per_day_waits_until_local_midnight():
    clock = Clock(_at(2050, 6, 1, 23, 0, 0))
    lim = _limiter(clock, ip_docs_per_min=1000)
    assert all(lim.check_download("203.0.113.9", d) is None for d in range(100))
    retry = lim.check_download("203.0.113.9", 100)
    assert retry == 3600          # 23:00 -> 00:00 Europe/Ljubljana
    clock.t = _at(2050, 6, 2, 0, 0, 0)
    assert lim.check_download("203.0.113.9", 100) is None


def test_key_alert_never_blocks_and_warns_once(caplog):
    lim = _limiter(Clock(_at(2050, 6, 1, 10, 0, 0)), key_alert_per_day=200)
    with caplog.at_level(logging.WARNING, logger="web.limits"):
        for d in range(500):
            lim.note_link("k1", d)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "k1" in warnings[0].getMessage()
    assert lim.snapshot() == {"links_today": {"k1": 500}, "alert_per_day": 200}


def test_key_alert_counts_distinct_documents():
    lim = _limiter(Clock(_at(2050, 6, 1, 10, 0, 0)))
    for _ in range(50):
        lim.note_link("k1", 7)
    assert lim.snapshot()["links_today"] == {"k1": 1}


def test_key_count_resets_at_local_midnight():
    clock = Clock(_at(2050, 6, 1, 23, 59, 0))
    lim = _limiter(clock)
    lim.note_link("k1", 1)
    clock.t = _at(2050, 6, 2, 0, 0, 1)
    assert lim.snapshot()["links_today"] == {}


@pytest.mark.parametrize("value,expected", [
    (" 203.0.113.9, 172.18.0.1", "203.0.113.9"),
    ("203.0.113.9", "203.0.113.9"),
])
def test_client_ip_is_the_first_forwarded_entry(value, expected):
    from types import SimpleNamespace
    from web.access import client_ip
    req = SimpleNamespace(headers={"X-Forwarded-For": value},
                          client=SimpleNamespace(host="172.18.0.5"))
    assert client_ip(req) == expected


def test_client_ip_without_a_proxy_is_the_peer():
    from types import SimpleNamespace
    from web.access import client_ip
    req = SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"))
    assert client_ip(req) == "127.0.0.1"


def test_stale_entries_are_dropped():
    """Every address that ever downloaded used to stay in memory until a deploy."""
    clock = Clock(_at(2050, 6, 1, 23, 0, 0))
    lim = _limiter(clock)
    for n in range(50):
        lim.check_download(f"203.0.113.{n}", 1)
        lim.check_api(f"k{n}")
    assert lim.tracked() == {"ips": 50, "keys": 50}
    clock.t = _at(2050, 6, 2, 0, 5, 0)
    lim.check_download("198.51.100.1", 1)
    assert lim.tracked() == {"ips": 1, "keys": 0}
