"""Cron producer (S1.5). Owns crons only, zero business logic (PRD §8) — each
`_tick_*` reads state and enqueues the next stage's job(s); it never writes
`document`/`item_document`/`evidence` itself.

A library, not a process. `app/handlers/scheduler_tick.py` is the only caller:
one perpetual, self-deferring `scheduler.tick` job per cron drives the cadence
off the queue, so the crons get the queue's retries, visibility and dead-letter
for free. The standalone loop this module used to carry was deleted 2026-09-02,
once the queue-hosted crons had run live for two days.

Still not a worker in the stage sense: a `_tick_*` calls `app.queue.enqueue`
directly, the same way any handler emits its next stage.

Restart safety (PHASES.md GAP B): C2's active-only dedupe frees a completed
cron's job key immediately, so job status alone can't gate re-firing across a
process restart. `scheduler_run(name, period_key)` is the out-of-queue ledger:
a period is "already run" iff its ledger row exists, written in the same
transaction as the enqueue (`record_run` never called without the enqueue that
earned it).
"""

from __future__ import annotations

import datetime as dt
import logging
import pathlib

from app import coverage
from app import playbooks
from app import queue
# Re-exported, not redefined: the producer UI names the same periods and
# cannot import this module (app.handlers is barred from web/).
from app.periods import (  # noqa: F401  -- re-export, imported by name elsewhere
    period_key_day,
    period_key_days,
    period_key_interval,
    period_key_isoweek,
    period_key_month,
)

#: Kept as a private alias: this module named it `_period_key_days` before it
#: moved to app/periods.py so `web/` could reach it (web may not import
#: app.scheduler -- it pulls app.handlers). One definition, two names.
_period_key_days = period_key_days
from app import health
from app.config import Config, Scheduler, load_config
from app.handlers.report import (
    dead_job_count,
    expiring_documents,
    failure_spikes,
    job_counts_by_type_status,
)
# The renewal key names a manufacturer and a cadence bucket, so the producer
# has to resolve both the same way the consumer will (spec §7.2, PRD §0).
from app.handlers.email_request import (
    groups_for_docs,
    manufacturers_for_docs,
    period_key as renewal_period_key,
)

log = logging.getLogger("dentalia.scheduler")


def already_ran(conn, name: str, period_key: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM scheduler_run WHERE name=%s AND period_key=%s",
        (name, period_key),
    ).fetchone()
    return row is not None


def record_run(conn, name: str, period_key: str, job_id: int | None) -> None:
    conn.execute(
        "INSERT INTO scheduler_run (name, period_key, job_id) VALUES (%s, %s, %s)",
        (name, period_key, job_id),
    )


def _find_latest_ingest_file(watch_dir: str, glob: str) -> pathlib.Path | None:
    d = pathlib.Path(watch_dir)
    if not d.is_dir():
        return None
    matches = list(d.glob(glob))
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)


def _tick_ingest_monthly(conn, cfg: Scheduler, now: dt.datetime) -> dict:
    """GAP D: no live BC feed exists — `ingest_watch_dir=""` (default) makes
    this a documented no-op. When set, globs for the lexicographically-latest
    file and enqueues `ingest.run` against it."""
    name = f"ingest.monthly:{cfg.ingest_catalogue}"
    period_key = period_key_month(now)
    result = {"fired": False, "job_id": None, "period_key": period_key}

    with conn.transaction():
        if already_ran(conn, name, period_key):
            return result

        job_id = None
        if cfg.ingest_watch_dir:
            latest = _find_latest_ingest_file(cfg.ingest_watch_dir, cfg.ingest_glob)
            if latest is not None:
                job_id = queue.enqueue(
                    conn,
                    "ingest.run",
                    {
                        "source": "csv",
                        "ref": str(latest),
                        "catalogue": cfg.ingest_catalogue,
                    },
                    dedupe_key=f"ingest.run:sched:{cfg.ingest_catalogue}:{period_key}",
                    priority="delta",
                )
                result["fired"] = True
                result["job_id"] = job_id
            else:
                log.info("scheduler: ingest.monthly %s — no file matched", period_key)
        else:
            log.debug("scheduler: ingest.monthly disabled (no watch dir)")

        record_run(conn, name, period_key, job_id)

    return result


def _tick_expiry_scan(conn, cfg: Scheduler, *, horizon_days: int,
                      cadence_days: int, now: dt.datetime) -> dict:
    """Runs at day granularity regardless of `expiry_scan_interval_hours` (no
    sub-day cadence is needed today — see docs/specs/scheduler.md §3). GAP E:
    the scan always runs for real; `email.request` emission is config-gated.

    `cadence_days` is `renewal.request_cadence_days` — the bucket the renewal
    key names, passed in rather than read here for the same reason
    `horizon_days` is: this function stays injectable."""
    name = "expiry-scan"
    period_key = period_key_day(now)
    result = {"fired": False, "expiring_count": 0, "period_key": period_key,
              "request_jobs": 0, "unaddressable_count": 0,
              "rediscover_jobs": 0, "rediscover_pending": 0}

    with conn.transaction():
        if already_ran(conn, name, period_key):
            return result

        expiring = expiring_documents(conn, horizon_days=horizon_days, today=now.date())
        result["expiring_count"] = len(expiring)
        # Counted apart, chased alike. Every one of these still gets an
        # email.request -- a lapsed certificate needs the chase more, not less
        # -- but the request says which it is, so S2.4 cannot write "about to
        # expire" to a manufacturer whose certificate lapsed in 2020. Measured
        # 2026-08-18: a 30-day scan returned 61 documents, all of them lapsed.
        result["lapsed_count"] = sum(1 for d in expiring if d["lapsed"])

        if cfg.expiry_email_enabled:
            # ONE renewal mail per manufacturer per cadence bucket -- client
            # ruling 2026-08-20 (spec §7.2), PRD §0's dedupe table. The trigger
            # document only selects WHO to write to: the handler rebuilds the
            # list from `lapsing_for_manufacturer`, so collapsing a
            # manufacturer's documents onto one job drops no work. Measured on
            # the live registry: 52 documents share the expiry 2026-05-04 and
            # all 52 are IVOCLAR, so keying by document drafted 52 mails for
            # one morning.
            bucket = renewal_period_key(now.date(), cadence_days)
            manufacturers = manufacturers_for_docs(
                conn, [d["doc_id"] for d in expiring])
            for doc in expiring:
                mfr = manufacturers.get(doc["doc_id"])
                # No production link means no manufacturer to address, so no
                # bucket to join. Those keep the doc-scoped key rather than
                # collapsing onto a shared `mfr:None:` one, which would drop
                # every unaddressable document but the first. The handler
                # refuses them with `no_manufacturer` and says so on the job.
                #
                # Both shapes are namespaced -- DISCOVER writes
                # `email.request:group:{group_id}` for its dead-end path, and
                # the three producers share job.dedupe_key's one flat unique
                # index, so each prefixes its id kind (PRD §0).
                key = (f"email.request:mfr:{mfr}:{bucket}" if mfr
                       else f"email.request:doc:{doc['doc_id']}")
                if mfr is None:
                    result["unaddressable_count"] += 1
                if queue.enqueue(
                    conn,
                    "email.request",
                    {"doc_id": doc["doc_id"],
                     "state": "lapsed" if doc["lapsed"] else "upcoming"},
                    dedupe_key=key,
                    priority="sweep",
                ) is not None:
                    result["request_jobs"] += 1

        # Look again ourselves, not only write a letter.
        #
        # Until 2026-09-02 expiry produced exactly one action: an `email.request`
        # asking the manufacturer. That is the right thing to do and it is not
        # the only thing -- manufacturers republish certificates on their own
        # sites without telling anyone, and DISCOVER already knows how to look.
        # With `expiry_email_enabled` off (the shipped default, and how the live
        # worker is configured) the scan took no action whatsoever.
        #
        # Off by default, and it must be: `DISCOVER_HOLD` is read only in
        # `resolve.py`, so a cron enqueueing `discover.group` bypasses it. This
        # flag is the only thing between a daily tick and unattended fetching.
        #
        # Capped for a measured reason. On 2026-09-02, 86 expiring or lapsed
        # production documents covered **431 groups** -- one document covers
        # many -- so an uncapped tick would queue roughly 1.300 fetches. What the
        # cap leaves behind is reported, never dropped silently (CLAUDE.md).
        if cfg.expiry_rediscover_enabled and expiring:
            groups = groups_for_docs(conn, [d["doc_id"] for d in expiring])
            result["rediscover_pending"] = max(
                0, len(groups) - cfg.expiry_rediscover_cap)
            for gid in groups[: cfg.expiry_rediscover_cap]:
                # `ignore_recency`: a lapsed certificate is the one case where
                # the recency window is exactly wrong -- it says "we looked
                # recently", and looking again is the entire point.
                #
                # Same dedupe key shape as the item button, the manufacturer
                # button and the CLI, so a human pressing any of them today and
                # this tick collapse onto one job rather than fetching twice.
                if queue.enqueue(
                    conn,
                    "discover.group",
                    {"group_id": gid, "ignore_recency": True},
                    dedupe_key=f"discover:refetch:{gid}:{now.date().isoformat()}",
                    priority="sweep",
                ) is not None:
                    result["rediscover_jobs"] += 1

        result["fired"] = True
        record_run(conn, name, period_key, None)

    return result


def _tick_coverage_scan(conn, cfg: Scheduler, *, now: dt.datetime) -> dict:
    """Send groups holding no production document through DISCOVER, capped.

    Nothing ever noticed a gap. Seven crons were registered here and none
    enqueued `discover.group`; the only occurrence of the word in this module
    was a failure-reason string. Coverage moved only when a person pressed
    something, which is fine for one supplier and hopeless for 367.

    **Off by default, and `DISCOVER_HOLD` does not gate it** (Denis, 2026-09-02:
    keep the flags separate). The hold is read only in `app/handlers/resolve.py`,
    where it stops RESOLVE *emitting* `discover.group`; a cron that enqueues one
    directly never consults it. Stopping this cron means turning this flag off,
    not setting the hold.

    **Capped at 25 a day, matching the manufacturer button on purpose** -- the
    cron should read as a trickle behind whatever a person is doing by hand.
    Measured 2026-09-02: 6.732 groups hold no production document and 6.693 have
    never been discovered at all, so this is a ~9-month backfill. That is what
    makes `app.coverage`'s ordering load-bearing: never-discovered first, then
    least recently discovered, because a stable ordering under a cap re-does the
    head of the list forever and never finishes.

    Recency is NOT bypassed here, unlike the expiry re-look. These groups have
    no document at all, so there is usually nothing in the ledger to skip; where
    there is, the ledger is right and a free skip is the correct outcome.
    """
    name = "coverage-scan"
    period_key = period_key_day(now)
    result = {"fired": False, "queued": 0, "uncovered_total": 0, "pending": 0,
              "period_key": period_key}
    if not cfg.coverage_scan_enabled:
        return result

    with conn.transaction():
        if already_ran(conn, name, period_key):
            return result

        found = coverage.uncovered_groups(conn, limit=cfg.coverage_scan_cap)
        result["uncovered_total"] = found["total"]
        result["pending"] = max(0, found["total"] - len(found["group_ids"]))
        for gid in found["group_ids"]:
            # Same dedupe key shape as the item button, the manufacturer button,
            # the CLI and the expiry re-look, so a human pressing any of them
            # today and this tick collapse onto one job.
            if queue.enqueue(
                conn,
                "discover.group",
                {"group_id": gid},
                dedupe_key=f"discover:refetch:{gid}:{now.date().isoformat()}",
                priority="sweep",
            ) is not None:
                result["queued"] += 1

        result["fired"] = True
        record_run(conn, name, period_key, None)

    return result


def _tick_weekly_report(conn, *, now: dt.datetime) -> dict:
    """GAP A: `report.weekly` — dedupe_key `report:{period_key}` per PRD §0."""
    name = "report.weekly"
    period_key = period_key_isoweek(now)
    result = {"fired": False, "job_id": None, "period_key": period_key}

    with conn.transaction():
        if already_ran(conn, name, period_key):
            return result

        job_id = queue.enqueue(
            conn,
            "report.weekly",
            {"period_key": period_key},
            dedupe_key=f"report:{period_key}",
            priority="sweep",
        )
        result["fired"] = True
        result["job_id"] = job_id
        record_run(conn, name, period_key, job_id)

    return result


def _tick_failure_monitor(conn, cfg: Scheduler, *, now: dt.datetime) -> dict:
    """Same day-granularity note as `_tick_expiry_scan`. The miss-rate query
    lives in `report.py`'s `failure_spikes` (same split as `expiring_documents`
    for the expiry scan) — this function only decides whether to emit. GAP F:
    `playbook.reonboard` payload uses `manufacturer` (canonical_manufacturer
    text) — Phase 1 has no `manufacturer` table id to send instead."""
    name = "failure-monitor"
    period_key = period_key_day(now)
    result = {"fired": False, "spikes": [], "period_key": period_key}

    with conn.transaction():
        if already_ran(conn, name, period_key):
            return result

        spikes = failure_spikes(
            conn,
            window_days=cfg.failure_window_days,
            miss_rate_threshold=cfg.failure_miss_rate_threshold,
            min_sample=cfg.failure_min_sample,
            now=now,
        )
        result["spikes"] = spikes

        # F37: resolve the recipe HERE, and enqueue only what the handler can
        # run. `playbook.reonboard` probes ONE index URL; a payload carrying a
        # manufacturer and nothing else has nothing to probe, and the handler
        # raises rather than silently reporting success -- correctly, because a
        # half-built handler that no-ops is worse than a loud one. The defect
        # was that this tick produced exactly that payload, so every spike it
        # fired dead-lettered. One such job is in the dead queue from
        # 2026-09-04.
        #
        # No authored crawl recipe means no URL to probe, and that is a real
        # state rather than an error: 38 of 384 manufacturers have a playbook at
        # all. It is recorded in the result and counted, never enqueued.
        for spike in spikes:
            if not cfg.failure_reonboard_enabled:
                continue
            pb = playbooks.for_manufacturer(spike["manufacturer"])
            recipe = pb.crawl[0] if pb and pb.crawl else None
            if recipe is None:
                spike["skipped"] = "no-crawl-recipe"
                result.setdefault("skipped", []).append(spike["manufacturer"])
                continue
            queue.enqueue(
                conn,
                "playbook.reonboard",
                {
                    "manufacturer": spike["manufacturer"],
                    "slug": pb.slug,
                    "index_url": recipe.index_url,
                    "reason": "discovery-failure-spike",
                    "miss_rate": spike["miss_rate"],
                    "window_days": spike["window_days"],
                },
                dedupe_key=f"playbook.reonboard:{spike['manufacturer']}:{period_key}",
                priority="sweep",
            )

        result["fired"] = True
        record_run(conn, name, period_key, None)

    return result


def _tick_email_poll(conn, cfg: Scheduler, *, now: dt.datetime) -> dict:
    """S2.4 inbound: enqueue `email.poll` once per interval bucket, gated
    `email_poll_enabled` (default off — no mailbox creds exist, GAP G8; same
    producer-side gating as email.request/reonboard). dedupe_key
    `email.poll:{period_key}` per PRD §0."""
    name = "email.poll"
    period_key = period_key_interval(now, cfg.email_poll_interval_hours)
    result = {"fired": False, "job_id": None, "period_key": period_key}

    with conn.transaction():
        if already_ran(conn, name, period_key):
            return result

        job_id = None
        if cfg.email_poll_enabled:
            job_id = queue.enqueue(
                conn,
                "email.poll",
                {"mailbox": "INBOX", "since": period_key},
                dedupe_key=f"email.poll:{period_key}",
                priority="sweep",
            )
            result["fired"] = True
            result["job_id"] = job_id
        else:
            log.debug("scheduler: email.poll disabled (email_poll_enabled off)")

        record_run(conn, name, period_key, job_id)

    return result


def _tick_eudamed_certregister(conn, cfg: Scheduler, *, now: dt.datetime) -> dict:
    """Pull the whole EUDAMED certificate register once per
    `eudamed_certregister_interval_days`. Carved out of the no-unattended-
    sweep ruling (Denis, 2026-08-20, restated 2026-08-26): 16 calls against a
    public register is the "prefer a bulk download to an API" the same ruling
    asks for, not the per-manufacturer sweep it forbids. Gated
    `eudamed_certregister_enabled` (default off, same footing as
    `expiry_email_enabled` / `email_poll_enabled`). No manufacturer on the
    payload -- the whole register is pulled and matched locally
    (`app.handlers.eudamed.handle_eudamed_certregister`)."""
    name = "eudamed.certregister"
    period_key = _period_key_days(now, cfg.eudamed_certregister_interval_days)
    result = {"fired": False, "job_id": None, "period_key": period_key}

    with conn.transaction():
        if already_ran(conn, name, period_key):
            return result

        job_id = None
        if cfg.eudamed_certregister_enabled:
            job_id = queue.enqueue(
                conn,
                "eudamed.certregister",
                {},
                dedupe_key=f"eudamed.certregister:{period_key}",
                priority="sweep",
            )
            result["fired"] = True
            result["job_id"] = job_id
        else:
            log.debug(
                "scheduler: eudamed.certregister disabled "
                "(eudamed_certregister_enabled off)"
            )

        record_run(conn, name, period_key, job_id)

    return result


def _tick_eudamed_sweep_due(conn, cfg: Scheduler, *, now: dt.datetime) -> dict:
    """Mark a manufacturer's per-device EUDAMED sweep due. NEVER enqueues --
    Denis, 2026-08-20, restated 2026-08-26: "No sweep ever runs unattended...
    an operator releases every run." Only `eudamed.sweep`'s own handler and
    the UI's release route (`web/registry.py` `sweep_release`) may write
    `released_at`; this tick writes `due_at` and nothing else. If you find
    yourself adding a `queue.enqueue` here, that is the defect this function
    exists to prevent.

    RULING 45 (Denis, ratified after review): the absent `scheduler_run`
    ledger here is DELIBERATE, not an oversight -- despite the brief's "both
    follow the shape of `_tick_email_poll`" and every other tick in this file
    carrying one. Three reasons, and the third is why omitting it is BETTER,
    not merely safe:
      1. The ledger in `_tick_email_poll` exists to prevent double-EMISSION
         within a period. This tick emits nothing, so the mechanism guards
         nothing here.
      2. The `due_at` write is an idempotent UPSERT gated on `due_at IS NULL`
         (checked below), so repeating it is harmless.
      3. A period ledger would make this WORSE: a manufacturer that crosses
         the staleness threshold mid-period would not be proposed until the
         next period boundary -- up to a full period of silence on a list
         whose only job is to tell a person there is work waiting.
    So this runs on every scheduler tick, on purpose. Do not "fix" this by
    adding a ledger back.

    A manufacturer is due iff it holds a trusted SRN (migration 043's
    `trusted_manufacturer_srn` view -- `auto`/`confirmed` only; otherwise
    there is nothing to sweep, and the release route's own 400 would refuse it
    anyway) AND EITHER:
      - it has never been swept -- RULING 42 (Denis): no `eudamed_sweep_state`
        row at all, or one with `last_swept_at IS NULL`, both count. This is
        the opposite of ruling 24 (a never-swept manufacturer's sweep DELTA is
        empty, because a delta needs a baseline) -- due-ness is about when we
        last looked, and never looking is the strongest possible reason to
        look. Without this, nothing would ever propose a manufacturer's FIRST
        sweep.
      - OR its `last_swept_at` is older than `eudamed_sweep_interval_days`.

    A row already carrying `released_at` is left completely alone -- RULING
    43 (Denis): it is either queued or running, and re-marking it due would
    put a completed-or-in-flight sweep back on an operator's list as if
    nothing had happened. A row already carrying `due_at` is likewise left
    alone: it is already on the list.
    """
    threshold = now - dt.timedelta(days=cfg.eudamed_sweep_interval_days)
    result = {"marked_due": 0, "already_due": 0, "skipped_released": 0, "not_due": 0}

    with conn.transaction():
        rows = conn.execute(
            "SELECT t.canonical_name, s.last_swept_at, s.due_at, s.released_at "
            "  FROM (SELECT DISTINCT canonical_name FROM trusted_manufacturer_srn) t "
            "  LEFT JOIN eudamed_sweep_state s ON s.canonical_name = t.canonical_name"
        ).fetchall()

        for row in rows:
            if row["released_at"] is not None:
                result["skipped_released"] += 1
                continue
            if row["due_at"] is not None:
                result["already_due"] += 1
                continue

            never_swept = row["last_swept_at"] is None
            if not (never_swept or row["last_swept_at"] <= threshold):
                result["not_due"] += 1
                continue

            conn.execute(
                "INSERT INTO eudamed_sweep_state (canonical_name, due_at) "
                "VALUES (%s, %s) "
                "ON CONFLICT (canonical_name) DO UPDATE SET due_at = EXCLUDED.due_at",
                (row["canonical_name"], now),
            )
            result["marked_due"] += 1

    return result


#: Items per `bc.push` job. Small enough that one failure re-runs cheaply, big
#: enough that a full catalogue is not 16.000 queue rows.
BC_PUSH_BATCH = 200


def _tick_bc_push_drift(conn, *, enabled: bool, batch: int, cap: int,
                        now: dt.datetime) -> dict:
    """Bring Business Central back in step with the registry.

    **A rolling window, not change detection.** These values move without anyone
    touching a row -- a document reaches its expiry date, a certificate is
    suspended -- so every item has to come round again regardless, and detecting
    "what changed" would still miss the ones that changed by the calendar.
    Oldest-pushed first, never-pushed before that.

    Cheap to be wrong about: `bc.push` recomputes and diffs, so an item that
    turns out unchanged costs one query and no PATCH.

    Gated on the same flag as the write itself. A queue full of pushes that will
    be withheld is noise, not safety, so the work is not created either.
    """
    if not enabled:
        return {"enqueued": 0, "batches": 0}

    rows = conn.execute(
        "SELECT m.item_ref, max(l.pushed_at) AS last_push "
        "  FROM item_mirror m "
        "  JOIN item_document d ON d.item_ref = m.item_ref "
        "  LEFT JOIN bc_push_log l ON l.item_ref = m.item_ref "
        " GROUP BY m.item_ref "
        " ORDER BY last_push ASC NULLS FIRST, m.item_ref "
        " LIMIT %s",
        (cap,),
    ).fetchall()

    refs = [r["item_ref"] for r in rows]
    enqueued = batches = 0
    for i in range(0, len(refs), batch):
        chunk = refs[i:i + batch]
        job_id = queue.enqueue(
            conn, "bc.push",
            {"run_id": f"drift:{now:%Y-%m-%d}", "item_refs": chunk},
            dedupe_key=f"bc.push:drift:{now:%Y-%m-%d}:{i // batch}",
            priority="sweep",
        )
        if job_id is not None:
            enqueued += len(chunk)
            batches += 1
    return {"enqueued": enqueued, "batches": batches}


def _tick_health_watch(conn, cfg: Config, *, now: dt.datetime) -> dict:
    """Notice that something stopped, and say so once.

    No `already_ran` ledger and no period key: this is not periodic work with an
    output, it is a condition check, and suppression lives in `alert_state`
    keyed by the condition rather than by a calendar bucket.

    It cannot report that the queue is dead -- it runs ON the queue. See
    `app/health.py` and `docs/dev/limits.md`.
    """
    return health.raise_alerts(conn, webhook_url=cfg.alerts.webhook_url, now=now)


def tick(conn, cfg: Config, now: dt.datetime) -> dict:
    """Run every due cron once. Each `_tick_*` wraps its own transaction — one
    cron's failure must not roll back or block another's (mirrors
    `runner.run_once`'s per-job isolation)."""
    results: dict = {}
    for key, fn in (
        ("ingest.monthly", lambda: _tick_ingest_monthly(conn, cfg.scheduler, now)),
        (
            "expiry-scan",
            lambda: _tick_expiry_scan(
                conn, cfg.scheduler,
                horizon_days=max(cfg.renewal.horizon_days),
                cadence_days=cfg.renewal.request_cadence_days,
                now=now,
            ),
        ),
        ("failure-monitor", lambda: _tick_failure_monitor(conn, cfg.scheduler, now=now)),
        ("coverage-scan", lambda: _tick_coverage_scan(conn, cfg.scheduler, now=now)),
        ("report.weekly", lambda: _tick_weekly_report(conn, now=now)),
        ("email.poll", lambda: _tick_email_poll(conn, cfg.scheduler, now=now)),
        (
            "eudamed.certregister",
            lambda: _tick_eudamed_certregister(conn, cfg.scheduler, now=now),
        ),
        (
            "eudamed.sweep-due",
            lambda: _tick_eudamed_sweep_due(conn, cfg.scheduler, now=now),
        ),
        ("health-watch", lambda: _tick_health_watch(conn, cfg, now=now)),
        ("bc.push-drift", lambda: _tick_bc_push_drift(
            conn, enabled=cfg.bc.write_enabled, batch=BC_PUSH_BATCH,
            cap=cfg.bc.drift_cap, now=now)),
    ):
        try:
            results[key] = fn()
        except Exception:
            log.exception("scheduler: %s tick failed", key)
    return results
