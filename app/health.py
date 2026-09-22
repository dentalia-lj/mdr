"""What the system can notice about itself.

`app/alerts.py` has always been able to push a line off this machine; until
2026-09-07 exactly one thing called it, a dead-lettered job. This module is the
rest of the answer: it reads the database and returns the conditions a person
would want woken for.

**It returns conditions; it does not send.** Keeping the judgement pure means
the rules are testable without a network, and the one place that sends stays
`alerts.notify`, which cannot break its caller.

**The limit, stated where it cannot be missed.** These run as a cron ON the
queue. They can report a dead *web* container, a dead *caddy*, one worker of
several, a queue that has stopped moving and a cron that has stopped firing --
but they cannot report that the queue itself is dead, because then nothing
evaluates them. That last mile needs an external dead-man's switch; see
`docs/dev/limits.md`.
"""

from __future__ import annotations

import datetime as dt

#: Condition kinds. Stable strings: they are half of a condition's dedupe key,
#: so renaming one re-alerts everything of that kind once.
SERVICE_STALE = "service-stale"
QUEUE_STALLED = "queue-stalled"
CRON_SILENT = "cron-silent"

#: How long a service may stay silent before it is presumed down. The worker
#: beats before each unit of work, so a long extraction is still alive; this
#: window has to clear the longest one comfortably.
DEFAULT_STALE_AFTER_S = 3600

#: How long due work may sit with nothing being claimed before the queue is
#: presumed stuck. Depth is not the signal -- a healthy sweep always has a deep
#: backlog -- so this measures MOVEMENT: the age of the newest claim.
DEFAULT_STALL_AFTER_S = 1800


def conditions(conn, *, now: dt.datetime,
               stale_after_s: int = DEFAULT_STALE_AFTER_S,
               stall_after_s: int = DEFAULT_STALL_AFTER_S) -> list[dict]:
    """Everything currently wrong, worst first.

    Each condition carries a stable `key`, so the caller can tell "still broken"
    from "broken again" without re-reading the world.
    """
    out: list[dict] = []
    for r in conn.execute(
        "SELECT service, instance, EXTRACT(EPOCH FROM (%s - last_seen)) AS age_s "
        "  FROM service_heartbeat ORDER BY service, instance",
        (now,),
    ).fetchall():
        if r["age_s"] > stale_after_s:
            minutes = int(r["age_s"] // 60)
            out.append({
                "kind": SERVICE_STALE,
                "key": f"{SERVICE_STALE}:{r['service']}:{r['instance']}",
                "message": (f"{r['service']} ({r['instance']}) has not beaten "
                            f"for {minutes} minutes"),
            })
    if _queue_stalled(conn, now=now, stall_after_s=stall_after_s):
        out.append({
            "kind": QUEUE_STALLED,
            "key": QUEUE_STALLED,
            "message": (f"work is due and nothing has been claimed for "
                        f"{stall_after_s // 60} minutes"),
        })

    for r in conn.execute(
        "SELECT DISTINCT payload->>'cron' AS cron FROM job "
        " WHERE type = 'scheduler.tick' AND status NOT IN ('pending','running') "
        "   AND NOT EXISTS (SELECT 1 FROM job live "
        "                    WHERE live.type = 'scheduler.tick' "
        "                      AND live.payload->>'cron' = job.payload->>'cron' "
        "                      AND live.status IN ('pending','running')) "
        " ORDER BY 1"
    ).fetchall():
        out.append({
            "kind": CRON_SILENT,
            "key": f"{CRON_SILENT}:{r['cron']}",
            "message": (f"cron {r['cron']} has no live tick -- it stopped and "
                        f"will stay stopped until a worker restarts"),
        })

    return out


def _queue_stalled(conn, *, now: dt.datetime, stall_after_s: int) -> bool:
    """Due work waiting, and no job claimed inside the window.

    Both halves are required. Backlog alone is normal -- a sweep is meant to
    have one -- and an idle queue with nothing due is healthy, not stuck.
    """
    row = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM job "
        "                WHERE status = 'pending' AND run_after <= %s) AS due, "
        "       (SELECT max(claimed_at) FROM job) AS last_claim",
        (now,),
    ).fetchone()
    if not row["due"]:
        return False
    last = row["last_claim"]
    if last is None:
        # Never claimed anything, ever. That is an install nobody has started,
        # not a queue that stopped -- and it is the normal state of a fresh
        # database, because migration 055 seeds the eight perpetual cron ticks
        # as pending and immediately due. A worker that is up but wedged still
        # reaches this check the moment it claims its first job; a worker that
        # was never there is `service-stale`'s to report, or nobody's.
        return False
    return (now - last).total_seconds() > stall_after_s


#: How long a condition stays quiet after it has been announced once. Long
#: enough that a broken Friday evening is one line, not ninety.
DEFAULT_COOLDOWN_S = 21600


def raise_alerts(conn, *, webhook_url: str | None, now: dt.datetime,
                 notify=None, cooldown_s: int = DEFAULT_COOLDOWN_S,
                 **check_kw) -> dict:
    """Evaluate, announce what is new, and say when something recovers.

    `alert_state` holds one row per open condition. A row that is still open is
    silent until the cooldown; a row whose condition has gone is deleted and its
    recovery announced, because silence after an alert cannot be read -- fixed
    and forgotten look identical.

    With no `webhook_url` this does nothing at all, state included: an install
    that cannot alert must not accumulate a backlog it would flush the day
    somebody configures it.
    """
    if not webhook_url:
        return {"open": 0, "announced": 0, "recovered": 0}

    if notify is None:  # pragma: no cover - the real sender, wired in prod
        from app.alerts import notify as notify

    live = {c["key"]: c for c in conditions(conn, now=now, **check_kw)}
    known = {
        r["key"]: r
        for r in conn.execute(
            "SELECT key, kind, message, last_alerted FROM alert_state"
        ).fetchall()
    }

    announced = 0
    for key, c in live.items():
        prior = known.get(key)
        if prior is not None and (
            (now - prior["last_alerted"]).total_seconds() < cooldown_s
        ):
            continue
        notify(webhook_url, f"Dentalia: {c['kind']}", c["message"], priority="high")
        announced += 1
        conn.execute(
            "INSERT INTO alert_state (key, kind, message, first_seen, last_alerted) "
            "VALUES (%s,%s,%s,%s,%s) "
            "ON CONFLICT (key) DO UPDATE SET message = EXCLUDED.message, "
            "  last_alerted = EXCLUDED.last_alerted",
            (key, c["kind"], c["message"], now, now),
        )

    recovered = 0
    for key, prior in known.items():
        if key in live:
            continue
        notify(webhook_url, f"Dentalia: {prior['kind']} recovered",
               prior["message"], priority="default")
        recovered += 1
        conn.execute("DELETE FROM alert_state WHERE key = %s", (key,))

    return {"open": len(live), "announced": announced, "recovered": recovered}
