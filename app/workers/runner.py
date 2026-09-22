"""Worker loop: claim -> dispatch -> finish. No business logic beyond that.

Competing identical workers poll the job table. Each handler finishes by
enqueueing the next stage's job(s); the topology is the enqueue graph, so there
is no orchestrator. Run with `python -m app.workers.runner`.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import time

import app.handlers.noop  # noqa: F401 - S0.1 no-op placeholders for every tag
# Real handlers override their no-op as each stage lands (last registration wins).
import app.handlers.ingest  # noqa: F401 - ingest.run (S1.1)
import app.handlers.extract  # noqa: F401 - extract.doc (S0.2)
import app.handlers.validate  # noqa: F401 - validate.doc (S0.3)
import app.handlers.gate  # noqa: F401 - gate.candidate / gate.apply (S0.3)
import app.handlers.backfill  # noqa: F401 - backfill.scan (S0.4)
import app.handlers.resolve  # noqa: F401 - resolve.group (S1.2)
import app.handlers.discover  # noqa: F401 - discover.group (S1.3)
import app.handlers.fetch  # noqa: F401 - fetch.url (S1.4)
import app.handlers.report  # noqa: F401 - report.weekly (S1.5)
import app.handlers.bc_push  # noqa: F401 - bc.push (BC writeback, 2026-09-07)
import app.handlers.upload  # noqa: F401 - upload.ingest (web document upload)
import app.handlers.vendor_import  # noqa: F401 - vendor.import (BC vendor upload, slice B)
import app.handlers.email_poll  # noqa: F401 - email.poll (S2.4 EMAIL inbound)
import app.handlers.email_request  # noqa: F401 - email.request / email.reminder (S2.4 EMAIL outbound)
import app.handlers.eudamed  # noqa: F401 - eudamed.sync (S2.3 stage 1: mirror fill only)
import app.handlers.scheduler_tick  # noqa: F401 - scheduler.tick (the cron cadence)
import app.handlers.playbook_probe  # noqa: F401 - playbook.reonboard (S2.2 probe)
from app import alerts, db, heartbeat, playbooks, queue, version
from app.config import load_config
from app.handlers import HANDLERS
from app.results import record_anomalies

log = logging.getLogger("dentalia.worker")


def run_once(conn, worker_id: str, handlers=None, *, queue_cfg=None,
             alert_url: str | None = None) -> bool:
    """Claim and process one job. Returns False when nothing moved forward —
    an empty queue, or a handler that self-deferred (the caller should back off
    rather than re-claim; see the note at the return).

    The claim commits on its own so `attempts` is durable — a hard crash mid-job
    leaves the row reclaimable after the visibility timeout and the poison-message
    guard still counts down. The handler's writes and the finish then commit
    together (result + audit atomic, invariant 10); a raising handler rolls back
    its partial writes and the job dead-letters through the backoff path.
    """
    if handlers is None:
        handlers = HANDLERS
    # `cfg.queue` reaches the queue ONLY through here. Both functions declare
    # these as parameters with their own defaults, so a caller that passes
    # nothing silently gets `queue.py`'s numbers and every `QUEUE_*` env var is
    # inert. That was live between 2026-09-02 and 2026-09-04: the config default
    # was raised 300 -> 1800 to stop a 38-page `eudamed.sweep` being reclaimed
    # mid-transaction (job 35679, reclaimed at 301s), and the running worker
    # kept reclaiming at 300 because this call passed nothing. `None` still
    # means "use queue.py's defaults", which is what the tests want.
    if queue_cfg is None:
        queue_cfg = load_config().queue

    with conn.transaction():
        job = queue.claim(conn, worker_id,
                          visibility_timeout_s=queue_cfg.visibility_timeout_s)
    if job is None:
        return False

    deferred = False
    try:
        with conn.transaction():
            handler = handlers.get(job["type"])
            if handler is None:
                raise LookupError(f"no handler registered for {job['type']}")
            # `caused_by` lineage: publish this job's id for the duration of the
            # handler call only, so any `queue.enqueue` it makes is stamped as
            # this job's child. Cleared in a `finally` wrapping EXACTLY the
            # dispatch (not the claim above, not `finish`/`fail` below) — a
            # handler that raises must not leak its id onto an enqueue made
            # by later bookkeeping in this same job's lifecycle.
            queue.current_job_id = job["id"]
            try:
                result = handler(conn, job)
            finally:
                queue.current_job_id = None
            # A handler may self-defer instead of completing (e.g. extract.doc
            # waiting on a Batch API result): it has already rescheduled the job
            # via queue.defer, so we commit its writes but skip finish. This must
            # be a return signal, not an exception — an exception would roll back
            # the batch_ref row and cause a resubmit (violating C9).
            deferred = isinstance(result, dict) and bool(result.get("_deferred"))
            if not deferred:
                queue.finish(conn, job["id"], result=result if isinstance(result, dict) else None)
                if isinstance(result, dict) and result.get("anomalies"):
                    record_anomalies(conn, result["anomalies"])
    except Exception as exc:
        with conn.transaction():
            status = queue.fail(conn, job["id"], f"{type(exc).__name__}: {exc}",
                                backoff_base_s=queue_cfg.backoff_base_s,
                                backoff_cap_s=queue_cfg.backoff_cap_s)
        log.warning("job %s (%s) failed -> %s: %s", job["id"], job["type"], status, exc)
        # AFTER the transaction commits, and only for `dead`. Three reasons in
        # that order: a rollback must never produce an alert about work that did
        # not happen; a 5s network call has no business inside a claim/finish
        # transaction; and `failed` is a job that will run again on its own, so
        # alerting on it would page a person for a transient 404 and teach them
        # to ignore the channel. `notify` swallows its own failures -- a job
        # that dead-lettered correctly must not then raise here and turn a
        # contained failure into an uncontained one.
        if status == "dead" and alert_url:
            alerts.notify(
                alert_url,
                f"Dentalia: {job['type']} dead-lettered",
                f"job {job['id']} ({job['type']})\n{type(exc).__name__}: {exc}\n"
                f"open /dead to re-run it",
                priority=alerts.HIGH, tags="rotating_light",
            )
        return True
    # A self-defer moved no work forward, so it reports the same "nothing to do"
    # as an empty queue and `run_forever` sleeps on it. Without this the loop
    # re-claims immediately, and a backlog sharing one domain lease spins:
    # `_CLAIMABLE` gates on `run_after`, so the deferred jobs are invisible for
    # their window, but they all become claimable again together -- one takes the
    # lease and every other costs a claim (UPDATE, attempts+1) plus a defer
    # (UPDATE, attempts-1). At the 2000ms politeness default that is ~499 wasted
    # UPDATE pairs every 2 seconds, for the ~17 minutes a 500-URL backlog takes
    # to drain politely. Sleeping the poll interval (1.0s default) cuts it to ~2.
    #
    # The sleep is the existing `poll_interval_s`, deliberately not a new knob:
    # it is already the "nothing to do" backoff and a lease wait is a species of
    # that. It must stay well UNDER the politeness interval or a freed lease goes
    # unused, and the defers self-stagger -- each one is now spaced by a sleep
    # rather than issued in a tight loop, so the backlog stops waking in lockstep.
    return not deferred


def heartbeat_instance(worker_id: str, hostname: str | None = None) -> str:
    """The heartbeat's instance key, which is NOT `worker_id`.

    `worker_id` defaults to `worker-{pid}` and inside a container that is
    `worker-1` for every replica, because each one is PID 1. Three replicas
    would then upsert a single heartbeat row and read as one instance -- and
    each container's own HEALTHCHECK could not find its own row among them.
    Docker guarantees the hostname unique per container, so that is the key.

    `claimed_by` keeps its existing naming untouched (it is a label on the
    dead-jobs board and changing it would rewrite what operators recognise);
    the worker id travels in the beat's `detail` instead."""
    host = (hostname if hostname is not None else socket.gethostname()).strip()
    return f"{worker_id}@{host}" if host else worker_id


def run_forever(worker_id: str | None = None, poll_interval_s: float | None = None) -> None:
    cfg = load_config()
    worker_id = worker_id or f"worker-{os.getpid()}"
    if poll_interval_s is None:
        poll_interval_s = float(os.environ.get("DENTALIA_POLL_INTERVAL_S", "1.0"))

    stopping = {"flag": False}

    def _stop(signum, _frame):
        log.info("worker %s got signal %s; stopping after current job", worker_id, signum)
        stopping["flag"] = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    # Refuse to start without a key, BEFORE connecting or claiming anything.
    # `anthropic.Anthropic()` constructs happily without one and only fails at
    # call time, so on 2026-08-12 a keyless worker claimed the GC pilot and
    # dead-lettered 79 extractions one at a time, twenty minutes after kickoff,
    # with the corpus half-archived. Extraction is this worker's core stage;
    # a worker that cannot call the API has no useful work to do, so failing
    # loudly here beats discovering it from the dead-jobs board.
    if not cfg.connection.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set: this worker cannot run extract.doc. "
            "Pass it into the container — docker-compose.yml declares it on the "
            "worker service and it is read from .env. Verify with: "
            "docker compose run --rm worker python -m app.cli version"
        )

    # The source fingerprint goes in the FIRST line a worker writes, so a run
    # that misbehaves can be traced to the code that actually executed rather
    # than the code in the editor. Four times on 2026-08-12 an image built
    # before a change ran old handlers and reported success; compare this
    # against `python -m app.cli version` on the host.
    log.info("worker %s starting (poll=%.1fs, source=%s)",
             worker_id, poll_interval_s, version.source_fingerprint())
    with db.connect(cfg.connection.database_url) as conn:
        # The beat carries the source fingerprint the line above logs, so the
        # header strip can show WHICH build is running. Four times on
        # 2026-08-12 an image built before a change ran old handlers and
        # reported success; that was only discoverable from this one log line.
        fingerprint = version.source_fingerprint()
        instance = heartbeat_instance(worker_id)
        while not stopping["flag"]:
            # Exit rather than spin. Every error path below this line is
            # swallowed -- beat by contract, the work by its own try -- so a
            # dead connection would otherwise leave a live process doing
            # nothing forever. `restart: unless-stopped` only fires on exit,
            # and Compose does nothing about a failing healthcheck, so with no
            # reconnect anywhere in app/ dying IS the recovery path.
            if conn.closed or conn.broken:
                raise RuntimeError(
                    "database connection lost; exiting so the supervisor restarts us"
                )
            # Before the work, not after: a worker wedged inside a long
            # extraction has still proven it is alive, and beating only on
            # completion would report a 20-minute T2 call as a dead service.
            heartbeat.beat(conn, "worker", instance,
                           detail={"source": fingerprint, "worker_id": worker_id})
            try:
                worked = run_once(conn, worker_id, queue_cfg=cfg.queue,
                                  alert_url=cfg.alerts.webhook_url)
            except Exception:
                log.exception("worker loop error")
                time.sleep(poll_interval_s)
                continue
            if not worked:
                time.sleep(poll_interval_s)
        # Say goodbye rather than decay into staleness. A SIGTERM'd worker is a
        # deliberate shutdown and the strip should show it gone at once, not
        # sixty seconds later looking like a crash.
        heartbeat.clear(conn, "worker", instance)
    log.info("worker %s stopped", worker_id)


def arm_crons(conn) -> int:
    """Ensure one live `scheduler.tick` job per cron. Returns rows created.

    A no-op when every cron is live: `enqueue` returns None against the
    active-only dedupe index, which holds `pending`/`running`/`failed`. `dead`
    is terminal and sits OUTSIDE that index, so a cron that dead-lettered has a
    free key and is revived here -- which is the whole point. Migration 055's
    seed covers the first deploy; this covers every restart after one broke.

    Cheap enough to run unconditionally on start: one insert per cron, almost
    all of them no-ops, once per process lifetime.
    """
    from app.handlers.scheduler_tick import POLL_SECONDS

    created = 0
    for cron in sorted(POLL_SECONDS):
        if queue.enqueue(
            conn, "scheduler.tick", {"cron": cron},
            dedupe_key=f"scheduler.tick:{cron}", priority="delta",
        ) is not None:
            created += 1
    if created:
        log.info("armed %d scheduler.tick cron(s)", created)
    return created


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("DENTALIA_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # Playbooks come from the database, not the bind-mounted directory. Its own
    # short-lived autocommit connection: workers never WRITE playbooks, so the
    # loader must not join the claim/finish transaction a handler is running
    # inside -- an epoch probe that took part in that transaction would hold
    # its snapshot for the length of the job.
    playbooks.set_source(lambda: db.connect(autocommit=True))
    # Re-arm the crons before claiming anything. Its own autocommit connection,
    # same reasoning as the playbook loader: this must not join a claim/finish
    # transaction, and it has to be durable whether or not the loop starts.
    with db.connect(autocommit=True) as conn:
        arm_crons(conn)
    run_forever()


if __name__ == "__main__":
    main()
