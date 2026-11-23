"""Webstore rate limits, in memory (2026-11-23 webshop API spec § 5.5).

**Block per customer IP, alert per key** (Denis, 2026-11-23). Erpium has one
key for all its customers, so a block on the key would lock every customer out
at once. Blocking therefore happens on `/d/*`, where each request is one
person's browser, and the key gets an alert. The one per-key block is a high
API ceiling that only an Erpium loop would reach.

Downloads count DISTINCT documents per IP, never requests: a PDF viewer fetches
one file in several byte-range requests and a clinic behind NAT shares an
address, and a document already counted today is free.

In memory because `web` is one container running one uvicorn process. A
deploy resets every counter -- accepted, the limits exist to slow a bulk walk,
not to account. Thread-safe: FastAPI runs sync dependencies and routes in a
threadpool.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)


class Limiter:
    def __init__(self, api_per_min: int, ip_docs_per_min: int, ip_docs_per_day: int,
                 key_alert_per_day: int, clock: Callable[[], float] = time.time,
                 tz: str = "Europe/Ljubljana"):
        self.api_per_min = api_per_min
        self.ip_docs_per_min = ip_docs_per_min
        self.ip_docs_per_day = ip_docs_per_day
        self.key_alert_per_day = key_alert_per_day
        self._clock = clock
        self._tz = ZoneInfo(tz)
        self._lock = threading.Lock()
        self._api: dict[str, tuple[int, int]] = {}            # key -> (minute, n)
        self._ip_day: dict[str, tuple[object, set]] = {}      # ip -> (day, docs)
        self._ip_min: dict[str, tuple[int, set]] = {}         # ip -> (minute, new docs)
        self._key_day: dict[str, tuple[object, set]] = {}     # key -> (day, docs)
        self._alerted: dict[str, object] = {}                 # key -> day warned

    # ------------------------------------------------------------ clock #
    def _now(self) -> tuple[float, int, object]:
        now = self._clock()
        return now, int(now // 60), datetime.fromtimestamp(now, self._tz).date()

    def _to_next_minute(self, now: float) -> int:
        return max(1, 60 - int(now % 60))

    def _to_midnight(self, now: float) -> int:
        local = datetime.fromtimestamp(now, self._tz)
        midnight = datetime.combine(local.date() + timedelta(days=1),
                                    datetime.min.time(), tzinfo=self._tz)
        return max(1, int(midnight.timestamp() - now))

    # ------------------------------------------------------------ checks #
    def check_api(self, key_id: str) -> int | None:
        """Seconds to wait, or None. Counts the call when it passes."""
        with self._lock:
            now, minute, _ = self._now()
            m, n = self._api.get(key_id, (minute, 0))
            if m != minute:
                n = 0
            if n >= self.api_per_min:
                return self._to_next_minute(now)
            self._api[key_id] = (minute, n + 1)
            return None

    def check_download(self, ip: str, doc_id: int) -> int | None:
        """Seconds to wait, or None. A document this IP already got today is
        free; a new one counts against the minute and the day."""
        with self._lock:
            now, minute, day = self._now()
            d, seen = self._ip_day.get(ip, (day, set()))
            if d != day:
                seen = set()
            if doc_id in seen:
                return None
            if len(seen) >= self.ip_docs_per_day:
                return self._to_midnight(now)
            m, fresh = self._ip_min.get(ip, (minute, set()))
            if m != minute:
                fresh = set()
            if len(fresh) >= self.ip_docs_per_min:
                return self._to_next_minute(now)
            seen.add(doc_id)
            fresh.add(doc_id)
            self._ip_day[ip] = (day, seen)
            self._ip_min[ip] = (minute, fresh)
            return None

    def note_link(self, key_id: str, doc_id: int) -> None:
        """Count a link issued under this key. Never blocks: warns once a day
        when the key crosses `key_alert_per_day` distinct documents."""
        with self._lock:
            _, _, day = self._now()
            d, docs = self._key_day.get(key_id, (day, set()))
            if d != day:
                docs = set()
            docs.add(doc_id)
            self._key_day[key_id] = (day, docs)
            if len(docs) > self.key_alert_per_day and self._alerted.get(key_id) != day:
                self._alerted[key_id] = day
                log.warning("webstore key %s: %d distinct documents linked today, "
                            "over the alert threshold of %d", key_id, len(docs),
                            self.key_alert_per_day)

    def snapshot(self) -> dict:
        with self._lock:
            _, _, day = self._now()
            return {
                "links_today": {k: len(docs) for k, (d, docs) in self._key_day.items()
                                if d == day},
                "alert_per_day": self.key_alert_per_day,
            }
