"""Batch API protocol (C9): the batch_ref side table is the source of truth so a
crash between submit and record never causes a double submission.

`advance_batch` is the state machine the extract.doc handler drives when a tier
runs via the Batch API. It returns one of:
  ("submitted", None)  — first pass: batch submitted + recorded; caller defers.
  ("pending",   None)  — batch still running; caller defers again.
  ("done",   results)  — results ready (resolved); caller processes them.

The batch client is injected (`submit(requests)->batch_id`, `done(batch_id)->bool`,
`results(batch_id)->dict`), so this is testable without live API calls.
Job payloads are never mutated — external state lives here (invariant 9).
"""

from __future__ import annotations


def get_batch_ref(conn, content_hash: str, tier: str):
    return conn.execute(
        "SELECT batch_id, submitted_at, resolved_at FROM batch_ref "
        "WHERE content_hash = %s AND tier = %s",
        (content_hash, tier),
    ).fetchone()


def record_batch(conn, content_hash: str, tier: str, batch_id: str) -> None:
    conn.execute(
        "INSERT INTO batch_ref (content_hash, tier, batch_id) VALUES (%s, %s, %s) "
        "ON CONFLICT (content_hash, tier) DO NOTHING",
        (content_hash, tier, batch_id),
    )


def resolve_batch(conn, content_hash: str, tier: str) -> None:
    conn.execute(
        "UPDATE batch_ref SET resolved_at = now() "
        "WHERE content_hash = %s AND tier = %s",
        (content_hash, tier),
    )


def advance_batch(conn, batch_client, content_hash: str, tier: str, requests):
    """Advance the batch for (content_hash, tier). See module docstring."""
    ref = get_batch_ref(conn, content_hash, tier)
    if ref is None:
        batch_id = batch_client.submit(requests)
        record_batch(conn, content_hash, tier, batch_id)
        return ("submitted", None)
    if ref["resolved_at"] is not None:
        # Already processed once; idempotent re-fetch (a retry after commit).
        return ("done", batch_client.results(ref["batch_id"]))
    if batch_client.done(ref["batch_id"]):
        results = batch_client.results(ref["batch_id"])
        resolve_batch(conn, content_hash, tier)
        return ("done", results)
    return ("pending", None)
