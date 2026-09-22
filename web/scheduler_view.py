"""SCHEDULER visibility (/scheduler) — the cron producer made legible.

S1.5 shipped `app/scheduler.py` and nothing in the UI ever mentioned it: "did
last night's expiry scan run" was answerable only by reading `scheduler_run`
over psql, and the weekly report the scheduler produces was written to
`job.result` and displayed nowhere. This module is those two facts on a screen.

Producer boundary, unchanged. The reads are `scheduler_run` (SELECT granted in
migration 013) and `job`; the only writes are job rows — Run now, and Re-arm.

**Run now** stays limited to the two crons whose product IS a job
(`report.weekly`, `email.poll`). The others are rendered unrunnable rather than
given a button with a 404 behind it. That used to be because no job type
existed for them; since migration 054 one does (`scheduler.tick`), but running
a cron on demand through it would mean pulling its perpetual row forward rather
than enqueuing a second one — enqueuing would create a SECOND self-deferring
job for that cron, because the handler always defers. Left undone deliberately;
`tasks/followups.md` carries it.

**Armed** is the column that reports liveness: whether a live `scheduler.tick`
row exists for each cron. Absence means stopped — a cron that broke five times
dead-letters, and `dead` sits outside the active-only dedupe index, so it stays
stopped until something re-arms it. Re-arm is that something, and so is any
worker restart (`runner.arm_crons`).

Split out of `web/app.py` for the same reason `web/registry.py` was: that file
is long enough. Unlike registry.py this module does hold POSTs — they enqueue,
exactly as `/ingest` and `/dead/{id}/rerun` do, and touch nothing else.

Period keys come from `app.periods`, never recomputed here: the string is both
the `scheduler_run` primary key and the tail of the dedupe key, so a UI copy
that formatted a week differently would enqueue a job the scheduler's own tick
could neither dedupe against nor recognise as done.
"""

from __future__ import annotations

import datetime as dt

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from app import periods, queue
from web import words

# How long a cron may go unrecorded before the panel calls it stale: two of its
# own periods. One is not enough — a daily scan that ran yesterday and not yet
# today is a healthy scheduler at 00:05, and a panel that cried "stale" every
# morning would be ignored by the second week.
_STALE_AFTER_PERIODS = 2

_MONTH_HOURS = 24 * 31


def _cron_specs(sched) -> list[dict]:
    """One entry per `app.scheduler.tick` cron, in tick order.

    `key` is the stable display id (and the run-now path segment); `ledger` is
    what the scheduler actually writes into `scheduler_run.name` — they differ
    for the monthly ingest, whose ledger name carries the catalogue.
    """
    return [
        {
            "key": "ingest.monthly",
            "ledger": f"ingest.monthly:{sched.ingest_catalogue}",
            "label": "Monthly catalogue ingest",
            "cadence": "monthly",
            "cadence_hours": _MONTH_HOURS,
            "period": periods.period_key_month,
            # Runnable in principle, deliberately not from here: the file it
            # would pick lives in the SCHEDULER container's watch dir, which
            # `web` does not mount. /ingest already enqueues an ingest.run
            # against a path a person can see.
            "job_type": None,
            "enabled": bool(sched.ingest_watch_dir),
            "note": (
                f"watch dir {sched.ingest_watch_dir}, glob {sched.ingest_glob}, "
                f"catalogue {sched.ingest_catalogue}"
                if sched.ingest_watch_dir
                else "no watch dir set (SCHEDULER_INGEST_WATCH_DIR) — the tick is a "
                "documented no-op; import by hand from /ingest"
            ),
        },
        {
            "key": "expiry-scan",
            "ledger": "expiry-scan",
            "label": "Expiry scan",
            "cadence": "daily",
            "cadence_hours": 24,
            "period": periods.period_key_day,
            "job_type": None,
            "enabled": True,
            "note": (
                "the scan itself always runs; email.request emission is "
                + ("ON" if sched.expiry_email_enabled else "OFF")
                + " (SCHEDULER_EXPIRY_EMAIL_ENABLED); re-look emission is "
                + ("ON" if sched.expiry_rediscover_enabled else "OFF")
                + " (SCHEDULER_EXPIRY_REDISCOVER_ENABLED), at most "
                + f"{sched.expiry_rediscover_cap} groups a day"
            ),
        },
        {
            "key": "failure-monitor",
            "ledger": "failure-monitor",
            "label": "Discovery failure monitor",
            "cadence": "daily",
            "cadence_hours": 24,
            "period": periods.period_key_day,
            "job_type": None,
            "enabled": True,
            "note": (
                f"miss rate over {sched.failure_window_days}d, threshold "
                f"{sched.failure_miss_rate_threshold}, min sample "
                f"{sched.failure_min_sample}; playbook.reonboard emission is "
                + ("ON" if sched.failure_reonboard_enabled else "OFF")
                + " (SCHEDULER_FAILURE_REONBOARD_ENABLED)"
            ),
        },
        # Registered in `tick()` 2026-09-02 and, for a few hours, not listed here
        # either -- the same omission the EUDAMED note below records.
        {
            "key": "coverage-scan",
            "ledger": "coverage-scan",
            "label": "Coverage scan",
            "cadence": "daily",
            "cadence_hours": 24,
            "period": periods.period_key_day,
            "job_type": None,
            "enabled": sched.coverage_scan_enabled,
            "note": (
                "emission is "
                + ("ON" if sched.coverage_scan_enabled else "OFF")
                + " (SCHEDULER_COVERAGE_SCAN_ENABLED); looks for groups with no "
                f"production document, oldest first, at most {sched.coverage_scan_cap} "
                "a day (SCHEDULER_COVERAGE_SCAN_CAP). A manufacturer's own button "
                "is the fast path"
            ),
        },
        {
            "key": "report.weekly",
            "ledger": "report.weekly",
            "label": "Weekly report",
            "cadence": "weekly",
            "cadence_hours": 24 * 7,
            "period": periods.period_key_isoweek,
            "job_type": "report.weekly",
            "enabled": True,
            "note": "always on; the report is the job's result envelope, shown below",
        },
        {
            "key": "email.poll",
            "ledger": "email.poll",
            "label": "Mailbox poll",
            "cadence": f"every {sched.email_poll_interval_hours}h",
            "cadence_hours": max(sched.email_poll_interval_hours, 1),
            "period": lambda now: periods.period_key_interval(
                now, sched.email_poll_interval_hours
            ),
            "job_type": "email.poll",
            "enabled": sched.email_poll_enabled,
            "note": (
                "emission is "
                + ("ON" if sched.email_poll_enabled else "OFF")
                + " (SCHEDULER_EMAIL_POLL_ENABLED); the button below enqueues one "
                "poll regardless — the flag gates the cron, not a person"
            ),
        },
        # Both EUDAMED crons shipped 2026-08-27 and this list was not extended,
        # so for four days they ran with no representation anywhere in the UI.
        {
            "key": "eudamed.certregister",
            "ledger": "eudamed.certregister",
            "label": "EUDAMED certificate register pull",
            "cadence": f"every {sched.eudamed_certregister_interval_days}d",
            "cadence_hours": 24 * sched.eudamed_certregister_interval_days,
            # A day-width bucket, not a calendar month: period_key_interval
            # buckets on now.hour // hours and any hours >= 24 collapses to
            # band 0, which would silently turn a 30-day cadence into a daily
            # one. See app/periods.py period_key_days.
            "period": lambda now: periods.period_key_days(
                now, sched.eudamed_certregister_interval_days
            ),
            # Runnable by hand. It was `None` until 2026-09-02, which routed
            # the template to "runs in-process -- no job to enqueue" -- true of
            # the expiry scan and the sweep-due tick, false here:
            # `eudamed.certregister` is in the closed job_type enum and has a
            # handler. With the cron off by default AND the scheduler process
            # not running, that left the drift and certificate-gap findings on
            # /expiry with no way to refresh at all, silently ageing (last
            # pulled 2026-08-27, holding a live Ivoclar Rev. 01 -> Rev. 02
            # drift and 42 certificates we hold no copy of).
            "job_type": "eudamed.certregister",
            "enabled": sched.eudamed_certregister_enabled,
            "note": (
                "emission is "
                + ("ON" if sched.eudamed_certregister_enabled else "OFF")
                + " (SCHEDULER_EUDAMED_CERTREGISTER_ENABLED); the button enqueues "
                "one pull regardless — the flag gates the cron, not a person. "
                "A whole-register bulk pull, 16 calls — carved out of the "
                "no-unattended-sweep ruling, unlike the per-manufacturer sweep "
                "below"
            ),
        },
        {
            # RULING 45: deliberately no ledger. It emits nothing, and its
            # due_at write is an idempotent UPSERT gated on due_at IS NULL, so
            # it runs on every poll on purpose -- a period ledger would leave a
            # manufacturer that crossed the threshold mid-period unproposed
            # until the next boundary. `verdict` is therefore not meaningful
            # here; the armed column is what says whether it is running.
            "key": "eudamed.sweep-due",
            "ledger": None,
            "label": "EUDAMED device sweep — mark due",
            "cadence": f"every {sched.eudamed_sweep_interval_days}d per manufacturer",
            "cadence_hours": 24 * sched.eudamed_sweep_interval_days,
            "period": None,
            "job_type": None,
            "enabled": True,
            "note": (
                "never enqueues a sweep — it marks manufacturers due and an "
                "operator releases each one at /manufacturers/sweep-due. No "
                "ledger by design (RULING 45), so it has no period verdict"
            ),
        },
        {
            # No ledger, for the same reason as sweep-due but a different one:
            # this is not periodic work with an output, it is a condition
            # check. Suppression lives in `alert_state`, keyed by the condition
            # rather than by a calendar bucket, so a period verdict would say
            # nothing about whether alerting is healthy.
            "key": "health-watch",
            "ledger": None,
            "label": "Health watch — alert when something stops",
            "cadence": "every 5m",
            "cadence_hours": 1 / 12,
            "period": None,
            "job_type": None,
            "enabled": True,
            "note": (
                "pushes one line to the alert webhook when a service stops "
                "beating, the queue stops moving, or a cron dies -- once per "
                "condition, with a recovery line when it clears. It runs ON "
                "the queue, so it cannot report that the queue itself is dead"
            ),
        },
        {
            # No ledger: the dedupe key is the day bucket, so an hourly poll
            # enqueues one run per day and a second tick is a no-op against the
            # active-scope index rather than a duplicate.
            "key": "bc.push-drift",
            "ledger": None,
            "label": "Business Central — re-push what drifted",
            "cadence": "daily, rolling window",
            "cadence_hours": 24,
            "period": None,
            "job_type": "bc.push",
            "enabled": True,
            "note": (
                "does nothing while bc.write_enabled is false, which is the "
                "default. A rolling window, oldest-pushed first, because these "
                "values change with the calendar and not only with a row -- an "
                "item that turns out unchanged costs a diff and no write"
            ),
        },
    ]


_LAST_RUN_SQL = """
SELECT DISTINCT ON (sr.name)
       sr.name, sr.period_key, sr.ran_at, sr.job_id,
       j.status::text AS job_status
  FROM scheduler_run sr
  LEFT JOIN job j ON j.id = sr.job_id
 WHERE sr.name = ANY(%s)
 ORDER BY sr.name, sr.ran_at DESC, sr.period_key DESC
"""


def _verdict(last: dict | None, *, current_period: str, cadence_hours: int, now) -> str:
    """`never` / `ok` / `due` / `stale`.

    Freshness alone cannot prove a scheduler is alive: a daily cron writes the
    ledger once per day however often it ticks. So the primary signal is
    whether THIS period is recorded, and age only separates "hasn't come round
    yet" from "has not run in a long time".
    """
    if last is None:
        return "never"
    if last["period_key"] == current_period:
        return "ok"
    age_h = (now - last["ran_at"]).total_seconds() / 3600
    return "due" if age_h <= _STALE_AFTER_PERIODS * cadence_hours else "stale"


#: The live `scheduler.tick` row per cron, if any. Active-only on purpose: a
#: `done`/`dead` tick is not running, and `dead` is exactly the state a cron
#: lands in when it has broken five times. Absence here means STOPPED.
_ARMED_SQL = """
SELECT dedupe_key, id, status::text AS status, run_after
  FROM job
 WHERE type = 'scheduler.tick'
   AND status IN ('pending', 'running', 'failed')
"""


def cron_rows(conn, sched, now: dt.datetime) -> list[dict]:
    specs = _cron_specs(sched)
    ledgers = [s["ledger"] for s in specs if s["ledger"]]
    last_by_name = {
        r["name"]: r
        for r in conn.execute(_LAST_RUN_SQL, (ledgers,)).fetchall()
    }
    armed = {r["dedupe_key"]: r for r in conn.execute(_ARMED_SQL).fetchall()}
    rows = []
    for spec in specs:
        # A ledgerless cron (RULING 45) has no period and no verdict to give:
        # it is correct for it to run on every poll, so "is this period
        # recorded" is the wrong question. The armed column answers the right
        # one for it.
        last = last_by_name.get(spec["ledger"]) if spec["ledger"] else None
        current_period = spec["period"](now) if spec["period"] else None
        live = armed.get(f"scheduler.tick:{spec['key']}")
        rows.append(
            {
                **{k: v for k, v in spec.items() if k != "period"},
                "current_period": current_period,
                "last": last,
                "age_h": None if last is None else (now - last["ran_at"]).total_seconds() / 3600,
                "verdict": (
                    "unledgered" if spec["ledger"] is None
                    else _verdict(
                        last,
                        current_period=current_period,
                        cadence_hours=spec["cadence_hours"],
                        now=now,
                    )
                ),
                "armed": live is not None,
                "live_job_id": None if live is None else live["id"],
                "next_poll": None if live is None else live["run_after"],
            }
        )
    return rows


_LATEST_REPORT_SQL = """
SELECT id, status::text AS status, result, created_at, finished_at
  FROM job
 WHERE type = 'report.weekly' AND result IS NOT NULL
 ORDER BY COALESCE(finished_at, created_at) DESC, id DESC
 LIMIT 1
"""


def latest_weekly_report(conn) -> dict | None:
    """The newest `report.weekly` result envelope, unpacked for rendering.

    `.get(... ) or []` on both lists deliberately: envelopes written before the
    expiring/lapsed split (2026-08-18) carry only `expiring`, and an older
    report must render as what it said, not 500 the page.
    """
    row = conn.execute(_LATEST_REPORT_SQL).fetchone()
    if row is None:
        return None
    result = row["result"] or {}
    return {
        "job_id": row["id"],
        "job_status": row["status"],
        "at": row["finished_at"] or row["created_at"],
        "period_key": result.get("period_key"),
        "expiring": result.get("expiring") or [],
        "lapsed": result.get("lapsed") or [],
        "dead_jobs": result.get("dead_jobs"),
        "job_counts": result.get("job_counts") or [],
    }


def _run_now_payload(cron: str, sched, now: dt.datetime) -> tuple[dict, str, str]:
    """`(payload, dedupe_key, period_key)` — each identical to what the matching
    `_tick_*` in `app/scheduler.py` enqueues, so a hand-run and the cron
    collapse into one job instead of racing to do the same work twice."""
    if cron == "report.weekly":
        period_key = periods.period_key_isoweek(now)
        return {"period_key": period_key}, f"report:{period_key}", period_key
    if cron == "email.poll":
        period_key = periods.period_key_interval(now, sched.email_poll_interval_hours)
        return (
            {"mailbox": "INBOX", "since": period_key},
            f"email.poll:{period_key}",
            period_key,
        )
    if cron == "eudamed.certregister":
        # Day-width bucket, not `period_key_interval`: any hours >= 24 collapse
        # to band 0 there, which would silently turn a 30-day cadence into a
        # daily one. Matches `_tick_eudamed_certregister` exactly, empty
        # payload included -- the whole register is pulled and matched locally,
        # so there is no manufacturer to send.
        period_key = periods.period_key_days(
            now, sched.eudamed_certregister_interval_days)
        return {}, f"eudamed.certregister:{period_key}", period_key
    raise HTTPException(status_code=404, detail=f"{cron} cannot be run from here")


def arm_cron(conn, cron: str) -> int | None:
    """Put a stopped cron back on the queue. Returns the new job id, or None if
    it was already live.

    The same `enqueue` `runner.arm_crons` makes, and idempotent for the same
    reason: the active-only dedupe index refuses a second row while one is
    `pending`/`running`/`failed`. A cron reaches "not armed" by dead-lettering,
    and `dead` is outside that index, so the key is free and this revives it.

    Deliberately does NOT write `scheduler_run` — same rule as run-now: marking
    a period run would make the cron skip the period you just helped with.
    """
    return queue.enqueue(
        conn, "scheduler.tick", {"cron": cron},
        dedupe_key=f"scheduler.tick:{cron}", priority="delta",
    )


def register_routes(app, templates, conn_factory, sched,
                    require_operator=None) -> None:
    """`require_operator` is `web/access.py::operator_guard`, built in
    `web/app.py` where `cfg` lives. Both buttons on this panel are D3
    writes: Run now spends whatever the cron spends, and Arm changes what
    the machine does from then on. The PANEL stays open to every staff
    login (D3), so an office person can still read what runs when.

    None means no route-level guard, for callers that build these routes
    directly in a test."""

    def _operator_only():
        return [Depends(require_operator)] if require_operator else []

    @app.get("/scheduler", response_class=HTMLResponse)
    def scheduler_panel(request: Request):
        now = dt.datetime.now(dt.timezone.utc)
        with conn_factory() as conn:
            rows = cron_rows(conn, sched, now)
            report = latest_weekly_report(conn)
        ran = [r["last"]["ran_at"] for r in rows if r["last"]]
        return templates.TemplateResponse(
            request,
            "scheduler.html",
            {
                "rows": rows,
                "report": report,
                "last_ledger_write": max(ran) if ran else None,
                "tick_interval_s": sched.tick_interval_s,
                "now": now,
            },
        )

    @app.post("/scheduler/run/{cron}", response_class=HTMLResponse,
              dependencies=_operator_only())
    def scheduler_run_now(request: Request, cron: str):
        """Enqueue what the cron would enqueue — nothing more.

        Notably it does NOT write `scheduler_run`. That ledger is the
        scheduler's own restart-safety record ("this period has been handled");
        marking a period run because a person asked for one extra run would
        make the scheduler skip the very period it was helping with.
        """
        now = dt.datetime.now(dt.timezone.utc)
        payload, dedupe_key, _ = _run_now_payload(cron, sched, now)
        with conn_factory() as conn:
            job_id = queue.enqueue(conn, cron, payload, dedupe_key, priority="sweep")
            conn.commit()
        return templates.TemplateResponse(
            request,
            "_result.html",
            {"request": request, "job_id": job_id, "dedupe_key": dedupe_key,
             # A receipt in words (spec § 9, P7a). It says what this button does
             # NOT do as well: the ledger is untouched, so the cron still runs
             # its own period on schedule.
             "receipt": words.receipt(
                 f"{cron} queued to run now. This extra run is not recorded as "
                 "the period's run, so the scheduled one still happens.",
                 job_id)},
        )

    @app.post("/scheduler/arm/{cron}", response_class=HTMLResponse,
              dependencies=_operator_only())
    def scheduler_arm(request: Request, cron: str):
        """Put a stopped cron back on the queue.

        A worker restart does this too (`runner.arm_crons`), so this button is
        for the case where nobody wants to restart a worker to revive one cron.
        """
        known = {s["key"] for s in _cron_specs(sched)}
        if cron not in known:
            raise HTTPException(status_code=404, detail=f"unknown cron {cron}")
        dedupe_key = f"scheduler.tick:{cron}"
        with conn_factory() as conn:
            job_id = arm_cron(conn, cron)
            conn.commit()
        return templates.TemplateResponse(
            request,
            "_result.html",
            {"request": request, "job_id": job_id, "dedupe_key": dedupe_key,
             "receipt": words.receipt(
                 f"{cron} is back on the queue. It runs at its next due time "
                 "and re-arms itself from then on.", job_id)},
        )
