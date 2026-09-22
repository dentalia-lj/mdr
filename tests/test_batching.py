"""Batch API protocol (C9): batch_ref side table drives submit/poll/resolve so a
crash never resubmits. Stub batch client + real Postgres batch_ref table."""

from __future__ import annotations

from app.extract import batching


class StubBatch:
    def __init__(self, done=False, results=None):
        self._done = done
        self._results = results or {}
        self.submits = 0

    def submit(self, requests):
        self.submits += 1
        return "batch-123"

    def done(self, batch_id):
        return self._done

    def results(self, batch_id):
        return self._results


def test_first_pass_submits_and_records(conn):
    bc = StubBatch()
    status, results = batching.advance_batch(conn, bc, "h1", "T1", requests=[{"x": 1}])
    conn.commit()
    assert status == "submitted"
    assert results is None
    assert bc.submits == 1
    row = conn.execute(
        "SELECT batch_id, resolved_at FROM batch_ref WHERE content_hash='h1' AND tier='T1'"
    ).fetchone()
    assert row["batch_id"] == "batch-123"
    assert row["resolved_at"] is None


def test_pending_never_resubmits(conn):
    bc = StubBatch(done=False)
    batching.advance_batch(conn, bc, "h2", "T1", requests=[])
    conn.commit()
    status, results = batching.advance_batch(conn, bc, "h2", "T1", requests=[])
    conn.commit()
    assert status == "pending"
    assert results is None
    assert bc.submits == 1  # the batch_ref row blocks a second submit


def test_done_returns_results_and_resolves(conn):
    bc = StubBatch(done=False)
    batching.advance_batch(conn, bc, "h3", "T1", requests=[])
    conn.commit()
    bc._done = True
    bc._results = {"regulation": {"value": "MDR", "confidence": 0.9}}
    status, results = batching.advance_batch(conn, bc, "h3", "T1", requests=[])
    conn.commit()
    assert status == "done"
    assert results == {"regulation": {"value": "MDR", "confidence": 0.9}}
    row = conn.execute(
        "SELECT resolved_at FROM batch_ref WHERE content_hash='h3' AND tier='T1'"
    ).fetchone()
    assert row["resolved_at"] is not None


def test_resolved_batch_is_idempotent(conn):
    bc = StubBatch(done=True, results={"type": {"value": "DoC"}})
    batching.advance_batch(conn, bc, "h4", "T1", requests=[])  # submit
    conn.commit()
    batching.advance_batch(conn, bc, "h4", "T1", requests=[])  # done -> resolve
    conn.commit()
    status, results = batching.advance_batch(conn, bc, "h4", "T1", requests=[])
    conn.commit()
    assert status == "done"
    assert results == {"type": {"value": "DoC"}}
    assert bc.submits == 1
