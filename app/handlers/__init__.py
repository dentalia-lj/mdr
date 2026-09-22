"""Handler registry: queue tag -> handler function.

One module per queue tag lands here across later sessions (ingest.py,
resolve.py, ...). Each finishes by enqueueing the next stage's job(s) — the
topology IS the enqueue graph. A handler receives the claiming transaction's
connection and the claimed job row; it must not commit (the worker owns the
transaction) and must be idempotent (at-least-once delivery).
"""

from __future__ import annotations

from collections.abc import Callable

import psycopg

Handler = Callable[[psycopg.Connection, dict], None]

HANDLERS: dict[str, Handler] = {}


def register(job_type: str, fn: Handler) -> None:
    """Register (or override) the handler for a queue tag."""
    HANDLERS[job_type] = fn
