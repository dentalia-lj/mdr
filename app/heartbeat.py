"""Liveness for the long-running processes, over the one channel they share.

Two readers, one writer. Each process beats on its own loop; its container
HEALTHCHECK reads its own row, and the web header strip reads every row. See
`migrations/035_service_heartbeat.sql` for why this is a table rather than a
query over `job.claimed_at`.

Nothing here is evidence and nothing audits it (invariant 10 is untouched: the
audit trail does not reference this table and never should). Truncating it costs
one poll interval of observability.
"""
from __future__ import annotations

import json
import logging

from psycopg.rows import dict_row

log = logging.getLogger("dentalia.heartbeat")

#: Default staleness window. Deliberately not a config key: every caller that
#: matters has a different loop period and passes its own -- a scheduler ticking
#: once a minute and a worker polling every second cannot share a threshold, and
#: a single global one would be wrong for at least one of them.
DEFAULT_STALE_AFTER_S = 60

#: Rows older than this are deleted on the next beat by any process. A beat is
#: seconds to a minute apart, so a day of silence is two orders of magnitude
#: past every staleness window: the process is dead, or the container was
#: replaced and beats under a new instance id. Measured 2026-09-04: two such
#: ghosts, one from a container gone eight days and one from the standalone
#: scheduler loop deleted 2026-09-02 -- and `read` counted both as instances.
#: Pruned here rather than by a cron so the table cannot accumulate when the
#: crons are what died.
PRUNE_AFTER_S = 24 * 3600

_UPSERT = """
    INSERT INTO service_heartbeat (service, instance, last_seen, detail)
    VALUES (%s, %s, now(), %s)
    ON CONFLICT (service, instance) DO UPDATE SET
        last_seen = now(),
        detail    = EXCLUDED.detail
"""

_PRUNE = "DELETE FROM service_heartbeat WHERE last_seen < now() - make_interval(secs => %s)"


def beat(conn, service: str, instance: str, detail: dict | None = None) -> None:
    """Record that `instance` of `service` is alive, now.

    Best-effort by contract. A heartbeat is telemetry and must never be the
    thing that kills a worker mid-job, so a failed write is logged and
    swallowed -- the reader already treats silence as staleness, which is the
    correct reading of a process that cannot reach the database anyway.

    This is the one place in the codebase that commits its own write rather
    than leaving it to the caller, and the exception is the point: a caller
    doing `beat(); conn.commit()` puts the commit OUTSIDE the guard, so a
    connection in a bad state kills the loop on the line after the one written
    not to. The commit belongs inside the try or the contract is a fiction.
    Safe because every caller beats between jobs, never inside one -- there is
    no open work transaction to end.
    """
    try:
        conn.execute(_UPSERT, (service, instance,
                               json.dumps(detail) if detail is not None else None))
        # Same guarded transaction as the beat: a prune that failed on its own
        # would be one more thing telemetry could kill a worker with.
        conn.execute(_PRUNE, (PRUNE_AFTER_S,))
        conn.commit()
    except Exception:  # noqa: BLE001 - telemetry never propagates
        log.warning("heartbeat write failed for %s/%s", service, instance, exc_info=True)


def clear(conn, service: str, instance: str) -> None:
    """Drop an instance's row on a clean shutdown.

    A SIGTERM'd worker is a deliberate stop, and letting its row decay into
    staleness would show a planned restart as a crash for a full staleness
    window. Best-effort for the same reason as `beat`: a process on its way out
    must not fail on the way."""
    try:
        conn.execute("DELETE FROM service_heartbeat WHERE service=%s AND instance=%s",
                     (service, instance))
        conn.commit()
    except Exception:  # noqa: BLE001 - telemetry never propagates
        log.warning("heartbeat clear failed for %s/%s", service, instance, exc_info=True)


def check(conn, service: str, instance: str,
          stale_after_s: int = DEFAULT_STALE_AFTER_S) -> bool:
    """Is THIS instance's own beat fresh? The container's HEALTHCHECK.

    Deliberately per-instance, not per-service, which is the opposite of what
    `read` does and for a reason: `read` answers "is the service up" for the
    header strip, where two healthy replicas legitimately cover for a slow
    third. A container probing that same question would report itself healthy
    while wedged, because its neighbours are fine. It has to ask about itself.

    No row is not health. A process that died before its first beat, or one
    whose migration never ran, fails rather than passing by absence."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT EXTRACT(EPOCH FROM (now() - last_seen))::float AS age_s
              FROM service_heartbeat WHERE service=%s AND instance=%s
            """,
            (service, instance),
        )
        row = cur.fetchone()
    return bool(row) and row["age_s"] <= stale_after_s


def read(conn, stale_after_s: int = DEFAULT_STALE_AFTER_S) -> dict[str, dict]:
    """`{service: {instance, instances, last_seen, age_s, up, detail}}`.

    Collapsed to the freshest instance per service: `--scale worker=3` with one
    replica wedged still means the service is up, and a chip that goes red
    because one of three is slow is a chip people learn to ignore.

    A service that has never beaten, or whose last beat is older than
    `PRUNE_AFTER_S` and has been pruned, is ABSENT from the mapping, not `down`.
    Absence and death are different findings -- a process a deployment does not
    run on this machine is legitimately absent, and reporting it as down would
    paint a permanent red chip on every page.
    """
    # An explicit dict_row cursor rather than the connection's default: callers
    # differ (the worker's connection is tuple-rowed, the web's is not) and
    # positional unpacking silently yields the COLUMN NAMES under dict_row
    # instead of failing, so `age_s` becomes the string "age_s".
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (service)
                   service, instance, last_seen, detail,
                   EXTRACT(EPOCH FROM (now() - last_seen))::float AS age_s,
                   count(*) OVER (PARTITION BY service)            AS instances
              FROM service_heartbeat
             ORDER BY service, last_seen DESC
            """
        )
        rows = cur.fetchall()
    return {
        r["service"]: {
            "instance": r["instance"],
            "instances": r["instances"],
            "last_seen": r["last_seen"],
            "age_s": r["age_s"],
            "up": r["age_s"] <= stale_after_s,
            "detail": r["detail"],
        }
        for r in rows
    }
