"""The transient spool carrying an uploaded BC export from the web producer to
the worker that parses it (migration 040).

TWO-PHASE, and that is the whole point. A PREVIEW job reads the row and leaves
it; an APPLY job reads the same bytes and consumes it. `upload_inbox`, the
document spool this is modelled on, is single-phase -- there "the row is gone"
means "already processed" -- which is why these are separate tables.

The web role holds INSERT only; every function here runs as the schema owner
inside a worker's claiming transaction. Nothing outside this module knows the
table exists.
"""

from __future__ import annotations

#: Abandoned previews -- uploaded, looked at, never applied -- are collected
#: after this many days. A GC window, not per-environment policy, so it stays a
#: constant rather than a config key (spec open question 2, decided 2026-08-25).
SPOOL_RETENTION_DAYS = 7


class SpoolMissing(Exception):
    """The spool row is gone: already applied, or garbage-collected."""


def read(conn, upload_id: int) -> dict:
    """The spool row, WITHOUT consuming it.

    Raises rather than returning None: every caller needs the bytes to do any
    work at all, so a missing row is a dead job with an actionable message, not
    a silent zero-row import that would read as "0 items changed".
    """
    row = conn.execute(
        "SELECT id, kind, filename, content, catalogue FROM import_inbox WHERE id=%s",
        (upload_id,),
    ).fetchone()
    if row is None:
        raise SpoolMissing(
            f"import_inbox row {upload_id} is gone: already applied, or collected "
            f"after {SPOOL_RETENTION_DAYS} days. Re-upload the file."
        )
    return row


def consume(conn, upload_id: int) -> None:
    """Delete the row an apply has finished with. Idempotent — a retry after a
    committed delete is a no-op (at-least-once delivery)."""
    conn.execute("DELETE FROM import_inbox WHERE id=%s", (upload_id,))


def gc(conn, *, retention_days: int = SPOOL_RETENTION_DAYS) -> int:
    """Collect spool rows nobody applied. Returns how many were dropped.

    Opportunistic, run by the import handlers rather than by a scheduler cron:
    `app/scheduler.py` is explicit that a `_tick_*` enqueues and never otherwise
    writes, and this is a DELETE.
    """
    cur = conn.execute(
        "DELETE FROM import_inbox WHERE created_at < now() - make_interval(days => %s)",
        (retention_days,),
    )
    return cur.rowcount
