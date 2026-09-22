"""Cron period keys — the buckets SCHEDULER fires once per (S1.5).

Split out of `app/scheduler.py` so the producer UI can name the period a cron
is currently in. `web/` may not import `app.scheduler`: that module imports
`app.handlers.report`, and `app.handlers`/`app.workers` stay out of `web/`
structurally (web/registry.py). This module is pure stdlib — no DB, no queue,
no handlers — the same footing on which `web/` already imports `app.playbooks`.

The keys are not display strings. Each one is both the `scheduler_run` primary
key for its cron and the tail of the job's `dedupe_key` (PRD §0), so a second
implementation that formatted a week or a band differently would enqueue jobs
the scheduler's own tick could neither dedupe nor recognise as already run.
One definition, imported by both sides.
"""

from __future__ import annotations

import datetime as dt


def period_key_month(now: dt.datetime) -> str:
    return now.strftime("%Y-%m")


def period_key_day(now: dt.datetime) -> str:
    return now.strftime("%Y-%m-%d")


def period_key_isoweek(now: dt.datetime) -> str:
    iso_year, iso_week, _ = now.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def period_key_interval(now: dt.datetime, hours: int) -> str:
    """A sub-day bucket for the mailbox poll: the day plus the interval band
    within it, so `email.poll` fires once per `hours`-wide window (unlike the
    day-granularity scans, a mailbox wants sub-day cadence for replies). `hours`
    <= 0 degrades to a single daily bucket."""
    band = (now.hour // hours) if hours and hours > 0 else 0
    return f"{now.strftime('%Y-%m-%d')}:{band:02d}"


def period_key_days(now: dt.datetime, interval_days: int) -> str:
    """Day-granularity bucket `interval_days` wide, anchored at the proleptic
    Gregorian epoch (`datetime.toordinal()`).

    `period_key_interval` (app/periods.py) cannot be reused here: it buckets
    on `now.hour // hours`, and `now.hour` never reaches 24 -- so any
    `hours >= 24` always resolves to band 0, while the key's date component
    still changes every calendar day. Multiplying a day count into that
    function (e.g. 30 days -> 720 hours) would therefore silently degrade a
    30-day cadence into a DAILY tick, firing ~24x more often than intended.
    Bucketing on the calendar day itself avoids that."""
    if interval_days <= 0:
        return now.strftime("%Y-%m-%d")
    return f"days{interval_days}:{now.toordinal() // interval_days}"
