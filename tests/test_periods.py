"""Cron period keys, and the one thing that must never drift about them.

`app/periods.py` exists so the producer UI can name the period a cron is in
without importing `app.scheduler` — which pulls `app.handlers`, structurally
barred from `web/` (web/registry.py). The keys are not display strings: the
same value is the `scheduler_run` primary key AND the tail of the job's
dedupe_key, so a UI that recomputed them slightly differently would enqueue a
job the scheduler's own tick could not dedupe against.
"""

from __future__ import annotations

import datetime as dt

from app import periods


def test_month_key_is_the_calendar_month():
    assert periods.period_key_month(dt.datetime(2026, 7, 23, tzinfo=dt.timezone.utc)) == "2026-07"


def test_day_key_is_the_calendar_day():
    assert periods.period_key_day(dt.datetime(2026, 7, 23, tzinfo=dt.timezone.utc)) == "2026-07-23"


def test_isoweek_key_uses_the_iso_year_not_the_calendar_year():
    # 2027-01-01 is a Friday inside ISO week 2026-W53 — a calendar-year
    # formatting would call it 2027-W53 and split one week across two ledgers.
    assert (
        periods.period_key_isoweek(dt.datetime(2027, 1, 1, tzinfo=dt.timezone.utc)) == "2026-W53"
    )


def test_interval_key_bands_the_day():
    morning = dt.datetime(2026, 8, 19, 5, 0, tzinfo=dt.timezone.utc)
    later = dt.datetime(2026, 8, 19, 7, 0, tzinfo=dt.timezone.utc)
    assert periods.period_key_interval(morning, 6) == "2026-08-19:00"
    assert periods.period_key_interval(later, 6) == "2026-08-19:01"


def test_interval_key_degrades_to_one_daily_bucket_when_hours_is_zero():
    assert (
        periods.period_key_interval(dt.datetime(2026, 8, 19, 7, tzinfo=dt.timezone.utc), 0)
        == "2026-08-19:00"
    )


def test_the_scheduler_uses_these_exact_functions():
    """Not "produces the same string" — the same object. A re-implementation in
    `scheduler.py` could pass an equality check today and diverge on the next
    edit, which is the drift this module was created to make impossible."""
    from app import scheduler

    assert scheduler.period_key_month is periods.period_key_month
    assert scheduler.period_key_day is periods.period_key_day
    assert scheduler.period_key_isoweek is periods.period_key_isoweek
    assert scheduler.period_key_interval is periods.period_key_interval
