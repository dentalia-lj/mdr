"""Hand-rolled Postgres job queue (CLAUDE.md: ~200 lines, no pgqueuer).

Every function operates inside the caller's transaction and never commits — the
worker wraps claim + result + audit in one transaction so a crash can never
half-apply (invariant 10). Semantics:

  * claim      — `SELECT ... FOR UPDATE SKIP LOCKED` over claimable work, ordered
                 priority (interactive<delta<sweep) then run_after; marks the row
                 running and increments attempts. A `running` job whose worker
                 died (claimed_at older than the visibility timeout) is reclaimable
                 — the increment on every claim is the poison-message guard.
  * enqueue    — insert, deduped against ACTIVE jobs only via the partial unique
                 index (C2): done/dead never block a re-enqueue.
  * fail       — exponential backoff via run_after; exhausted attempts -> dead,
                 and a dead job raises a `dead-job-followup` manual task in the
                 same transaction, because a counter nobody reads is not an alarm.
  * defer      — voluntary reschedule (e.g. batch poll); refunds the claim's
                 attempt increment so polling never exhausts the retry budget.
  * try_domain_lease — one FETCH per domain per politeness interval, or the
                 robots.txt `Crawl-delay` floor where one is recorded.
"""

from __future__ import annotations

import datetime as dt
import decimal
import json

import psycopg
from psycopg.types.json import Json

# Claimable = waiting work whose time has come, or a job whose worker died.
_CLAIMABLE = (
    "((status IN ('pending','failed') AND run_after <= now())"
    " OR (status='running' AND claimed_at < now() - make_interval(secs => %s)))"
)

# Which job's handler is currently dispatching, so `enqueue` can stamp
# `caused_by` implicitly (no parameter threaded through any handler). Workers
# are sync and single-job-per-process-at-a-time (CLAUDE.md: no asyncio), so a
# module-level slot is race-free. `run_once` sets this immediately before
# calling the handler and clears it in a `finally` around exactly that call —
# never around claim or finish, so a failure in that bookkeeping can't leak a
# stale id onto whatever enqueues next. None = a root (CLI, scheduler tick,
# web producer) enqueuing outside any job dispatch.
current_job_id: int | None = None


def _json(payload: dict) -> Json:
    """Adapt a dict for a jsonb column."""
    return Json(payload)


def _json_default(o):
    """Serialise the non-JSON types psycopg hands back out of this schema.

    Job results are assembled from query rows, so `date`, `timestamptz` and
    `numeric` columns arrive as `date`, `datetime` and `Decimal` -- none of
    which `json.dumps` accepts. `report.weekly` returned a `date` from
    `document_effective_expiry.expires` and dead-lettered on all five attempts,
    every time, on a registry holding 85 production documents with an expiry.
    The stage had never completed. Widening the envelope here rather than
    stringifying at each handler is the point: the next handler that reads a
    date column must not have to know this.

    Deliberately NOT `default=str`. Coercing anything unknown to its repr turns
    a handler bug into a result row that looks plausible -- `{"conn":
    "<psycopg.Connection ...>"}` reads as data. Unknown types keep raising, so
    they surface as a dead job with a real traceback, which is what the
    dead-letter path is for.
    """
    if isinstance(o, dt.datetime):
        return o.isoformat()
    if isinstance(o, (dt.date, dt.time)):
        return o.isoformat()
    if isinstance(o, decimal.Decimal):
        # float, not str: a result envelope is read by humans and by the KPI
        # board, and both want a number. Cost/confidence magnitudes here are
        # nowhere near float's precision limits.
        return float(o)
    raise TypeError(f"Object of type {o.__class__.__name__} is not JSON serializable")


def enqueue(
    conn: psycopg.Connection,
    type: str,
    payload: dict,
    dedupe_key: str,
    *,
    priority: str = "sweep",
    run_after=None,
    max_attempts: int = 5,
) -> int | None:
    """Insert a job, or no-op if an ACTIVE job already holds `dedupe_key` (C2).

    Returns the new job id, or None when deduped.
    """
    row = conn.execute(
        """
        INSERT INTO job (type, payload, dedupe_key, priority, run_after, max_attempts, caused_by)
        VALUES (%s::job_type, %s, %s, %s::job_priority,
                COALESCE(%s::timestamptz, now()), %s, %s)
        ON CONFLICT (dedupe_key) WHERE status IN ('pending','running','failed')
        DO NOTHING
        RETURNING id
        """,
        (type, _json(payload), dedupe_key, priority, run_after, max_attempts, current_job_id),
    ).fetchone()
    return row["id"] if row else None


def claim(
    conn: psycopg.Connection,
    worker_id: str,
    types: list[str] | None = None,
    visibility_timeout_s: int = 300,
) -> dict | None:
    """Atomically claim the next job (or None). Increments attempts.

    The chosen row is locked with FOR UPDATE SKIP LOCKED and flipped to running
    in one statement — competing workers never see the same job.
    """
    return conn.execute(
        f"""
        UPDATE job
           SET status='running', claimed_by=%s, claimed_at=now(), attempts=attempts+1
         WHERE id = (
               SELECT id FROM job
                WHERE {_CLAIMABLE}
                  AND (%s::job_type[] IS NULL OR type = ANY(%s::job_type[]))
                ORDER BY priority, run_after
                FOR UPDATE SKIP LOCKED
                LIMIT 1
         )
        RETURNING *
        """,
        (worker_id, visibility_timeout_s, types, types),
    ).fetchone()


def finish(conn: psycopg.Connection, job_id: int, result: dict | None = None) -> None:
    """Mark a job done (terminal - frees its dedupe key), storing its result
    envelope. Same transaction as the status flip, so a done job and its
    report can never disagree.

    Also closes any `dead-job-followup` still open for an EARLIER dead attempt
    at this same work, matched on the dedupe key -- which is what "this same
    work" means to the queue. Placed here rather than on the re-run button
    because re-queueing is not success: a job that dies again would have had
    its alarm cleared by the attempt to fix it, which is the one case the alarm
    exists for. The board therefore empties only as work actually completes,
    and it is the worker that writes, keeping the UI a producer (the web role
    holds no write grant on manual_task)."""
    conn.execute(
        "UPDATE job SET status='done', result=%s, finished_at=now() WHERE id=%s",
        (json.dumps(result, default=_json_default) if result is not None else None, job_id),
    )
    conn.execute(
        """
        UPDATE manual_task SET status='resolved', resolved_by='worker',
                               resolved_at=now()
        WHERE kind='dead-job-followup' AND status='open'
          AND (payload->>'job_id')::bigint IN (
              SELECT d.id FROM job d
              WHERE d.status='dead'
                AND d.dedupe_key = (SELECT dedupe_key FROM job WHERE id=%s))
        """,
        (job_id,),
    )


def _raise_dead_job_task(conn: psycopg.Connection, job_id: int, row, error: str) -> None:
    """A dead job must reach a human, not just a counter.

    `manual_kind` has carried `dead-job-followup` since the schema was written
    and nothing ever produced one, so a document that died mid-pipeline left no
    manual task, no anomaly, and no trace outside the `/dead` board.

    The 45 dead jobs standing on 2026-08-14 came to 2 distinct documents, both
    safety data sheets that correctly do not belong in the registry -- so the
    audit that found them cost nothing in lost work. It did find a job failing
    permanently with no path to success (see `gate._is_current_rev`'s caller),
    which is the shape that does cost work, and which nothing would have
    surfaced. "Any job whose loss loses work is a design bug" (CLAUDE.md): the
    alarm is for what a dead job can cost, not for what these particular ones
    did.

    Raised here rather than in each handler so no job type can be forgotten:
    every dead-letter in the system goes through this one transition, and the
    write shares `fail`'s transaction, so a task cannot exist for a job that is
    not dead nor a dead job go unannounced.

    Carries the fields a re-run needs -- the identifying hash and where the
    bytes live -- so the task is actionable without opening the job row, and
    the error itself, so triage can group by cause. `doc_id` stays null: the
    job died, so in general no document exists to point at."""
    payload = row["payload"] if isinstance(row["payload"], dict) else {}
    conn.execute(
        "INSERT INTO manual_task (kind, payload) VALUES ('dead-job-followup', %s)",
        (Json({
            "job_id": job_id,
            "job_type": row["type"],
            "attempts": row["attempts"],
            "error": error,
            # Present for document-carrying tags, absent for the rest (a dead
            # report.weekly has no hash). Recorded when known rather than
            # required, so the alarm covers every tag either way.
            **{k: payload[k] for k in ("content_hash", "archive_url", "source_url")
               if payload.get(k)},
        }),),
    )


def fail(
    conn: psycopg.Connection,
    job_id: int,
    error: str,
    *,
    backoff_base_s: int = 5,
    backoff_cap_s: int = 3600,
) -> str:
    """Record a failure. Retries with exponential backoff until attempts are
    exhausted, then dead-letters. Returns the resulting status."""
    row = conn.execute(
        "SELECT attempts, max_attempts, type, payload FROM job WHERE id=%s", (job_id,)
    ).fetchone()
    if row["attempts"] >= row["max_attempts"]:
        # `finished_at` on the terminal transitions only (migration 026): dead
        # is terminal, the `failed` branch below is not — that job runs again,
        # and stamping it would make the column mean "when the last attempt
        # stopped", which is a different question.
        conn.execute(
            "UPDATE job SET status='dead', last_error=%s, finished_at=now() WHERE id=%s",
            (error, job_id),
        )
        _raise_dead_job_task(conn, job_id, row, error)
        return "dead"
    delay = min(backoff_base_s * (2 ** (row["attempts"] - 1)), backoff_cap_s)
    conn.execute(
        "UPDATE job SET status='failed', last_error=%s, "
        "run_after = now() + make_interval(secs => %s) WHERE id=%s",
        (error, delay, job_id),
    )
    return "failed"


def defer(conn: psycopg.Connection, job_id: int, seconds: int) -> None:
    """Reschedule a job `seconds` into the future without consuming a retry
    (e.g. a Batch API poll). Refunds the attempt increment from the claim."""
    conn.execute(
        "UPDATE job SET status='pending', "
        "run_after = now() + make_interval(secs => %s), "
        "attempts = GREATEST(attempts - 1, 0) WHERE id=%s",
        (seconds, job_id),
    )


def try_domain_lease(
    conn: psycopg.Connection, domain: str, politeness_ms: int = 2000
) -> bool:
    """Acquire the FETCH politeness lease for `domain`. Returns False if the
    domain was fetched within the last politeness interval.

    The interval actually held is `GREATEST(politeness_ms, min_politeness_ms)`.
    The caller passes the configured global (`cfg.fetch.politeness_ms`);
    `min_politeness_ms` is a per-host FLOOR written only by the robots reader
    from a robots.txt `Crawl-delay` (migration 036), never by an acquire.

    That split is the whole point. `politeness_ms` is rewritten from the caller
    on every acquire, so it cannot carry a per-host value: FETCH always passes
    the global, so a 10s delay written for `renfert` was overwritten with the
    2s default by the very next `fetch.url` on that host -- robots obeyed for
    exactly one request. Keeping the floor in its own column means config is
    the floor, robots raises it, and the robots TTL refresh lowers it again if
    a host drops its `Crawl-delay`. Default 0, so every host without one
    behaves exactly as before.
    """
    row = conn.execute(
        """
        INSERT INTO domain_lease (domain, leased_until, politeness_ms)
        VALUES (%s, now() + make_interval(secs => %s / 1000.0), %s)
        ON CONFLICT (domain) DO UPDATE
           SET leased_until = now() + make_interval(secs =>
                   GREATEST(EXCLUDED.politeness_ms, domain_lease.min_politeness_ms) / 1000.0),
               politeness_ms = EXCLUDED.politeness_ms
         WHERE domain_lease.leased_until IS NULL
            OR domain_lease.leased_until <= now()
        RETURNING domain
        """,
        (domain, politeness_ms, politeness_ms),
    ).fetchone()
    return row is not None
