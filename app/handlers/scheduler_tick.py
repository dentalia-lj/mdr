"""scheduler.tick — the cron cadence as queue state.

One perpetual job per cron. The handler runs that cron's existing `_tick_*`
function and self-defers; the job never completes, so it never frees its dedupe
key and can never be duplicated.

`run_after` is a POLL interval, not a fire time. `scheduler_run` remains the
sole authority on whether a period has fired, so defer precision does not
matter, drift is harmless, and moving the cadence here cannot change WHEN
anything fires -- only which process asks. That is what lets this ship while
`app/scheduler.py`'s loop is still running: both consult the same ledger, so
whichever asks first fires and the other is a no-op.

Isolation is structural rather than a try/except: one cron per job means one
cron's failure dead-letters one cron and leaves the other six claimable. That
is what `tick()`'s per-cron try/except used to provide inside a single loop.

Bound: `claim`'s visibility timeout is 300s (app/queue.py), so nothing here may
take longer than that or a second worker could claim the same tick. Every
`_tick_*` is sub-second in-DB work today; that is a constraint on what a cron
may ever grow into, not an observation.
"""
from __future__ import annotations

import datetime as dt
import logging

from app import queue, scheduler
from app.config import load_config
from app.handlers import register

log = logging.getLogger("dentalia.handlers.scheduler_tick")

#: cron key -> the function that runs it. The same dispatch table `tick()`
#: iterates, lifted out: one entry per value the payload's `cron` may take.
#: `_tick_*` are imported, not reimplemented -- the ledger semantics, the
#: config gates and all 44 of their tests stay exactly as they are.
CRONS = {
    "ingest.monthly": lambda conn, cfg, now: scheduler._tick_ingest_monthly(
        conn, cfg.scheduler, now),
    "expiry-scan": lambda conn, cfg, now: scheduler._tick_expiry_scan(
        conn, cfg.scheduler,
        horizon_days=max(cfg.renewal.horizon_days),
        cadence_days=cfg.renewal.request_cadence_days,
        now=now),
    "failure-monitor": lambda conn, cfg, now: scheduler._tick_failure_monitor(
        conn, cfg.scheduler, now=now),
    "coverage-scan": lambda conn, cfg, now: scheduler._tick_coverage_scan(
        conn, cfg.scheduler, now=now),
    "report.weekly": lambda conn, cfg, now: scheduler._tick_weekly_report(
        conn, now=now),
    "email.poll": lambda conn, cfg, now: scheduler._tick_email_poll(
        conn, cfg.scheduler, now=now),
    "eudamed.certregister": lambda conn, cfg, now: scheduler._tick_eudamed_certregister(
        conn, cfg.scheduler, now=now),
    "eudamed.sweep-due": lambda conn, cfg, now: scheduler._tick_eudamed_sweep_due(
        conn, cfg.scheduler, now=now),
    # Takes the whole Config, not just `cfg.scheduler`: the alert endpoint is
    # `cfg.alerts.webhook_url`, and this is the only cron that sends anything.
    "health-watch": lambda conn, cfg, now: scheduler._tick_health_watch(
        conn, cfg, now=now),
    "bc.push-drift": lambda conn, cfg, now: scheduler._tick_bc_push_drift(
        conn, enabled=cfg.bc.write_enabled, batch=scheduler.BC_PUSH_BATCH,
        cap=cfg.bc.drift_cap, now=now),
}

#: How often each cron ASKS -- a fraction of its own period in every case,
#: because the ledger and not this number decides whether the period fires. A
#: poll that arrives late fires the same period; a poll that arrives twice
#: inside one period fires once.
#:
#: `eudamed.sweep-due` has no ledger at all (RULING 45) and its `due_at` write
#: is an idempotent UPSERT, so polling IS its correctness model already.
POLL_SECONDS = {
    "ingest.monthly": 21600,        # month period, 6h poll
    "expiry-scan": 3600,            # day period
    "failure-monitor": 3600,        # day period
    "coverage-scan": 3600,          # day period
    "report.weekly": 3600,          # ISO week period
    "email.poll": 900,              # 6h band, wants the finest poll of the set
    "eudamed.certregister": 21600,  # 30-day bucket
    "eudamed.sweep-due": 3600,      # no ledger; idempotent every poll
    "health-watch": 300,            # the finest poll here: it is the
                                    # one cron whose whole job is to
                                    # notice that something stopped
    "bc.push-drift": 3600,          # day window; the dedupe key is per day,
                                    # so an hourly poll fires it once
}


def handle_scheduler_tick(conn, job: dict) -> dict:
    """Run one cron, then put this job back on the clock.

    Returns `{"_deferred": True, ...}` so `run_once` commits the cron's writes
    and skips `finish` -- the job stays `pending` with a later `run_after`, and
    `queue.defer` refunds the attempt the claim charged, so a perpetual tick
    never walks its way to `dead` just by running.
    """
    cron = job["payload"].get("cron")
    if cron not in CRONS:
        # Loud rather than a silent no-op: an unknown cron means the payload and
        # this table have drifted, and a tick that quietly does nothing is
        # indistinguishable from one whose period has not come round.
        raise LookupError(
            f"unknown cron {cron!r} on scheduler.tick job {job['id']}; "
            f"known: {sorted(CRONS)}"
        )

    now = dt.datetime.now(dt.timezone.utc)
    result = CRONS[cron](conn, load_config(), now)
    queue.defer(conn, job["id"], POLL_SECONDS[cron])
    log.debug("scheduler.tick %s -> %s", cron, result)
    return {"_deferred": True, "cron": cron, **(result or {})}


register("scheduler.tick", handle_scheduler_tick)
