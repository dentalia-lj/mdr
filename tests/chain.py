"""Drive the REAL queue, not a sequence of handler calls.

Every other test in this suite calls a handler directly: `handle_extract_doc(conn,
{"payload": ...})`. That proves the handler's logic and nothing about the machinery
around it, and the machinery is where four of this project's incidents came from --
a claim that reclaimed mid-transaction, a payload a handler emitted that the next
stage could not read, a lineage stamp that leaked onto the wrong job, an audit row
written outside the finish.

`drain` claims through `runner.run_once`, which means every job goes through the
real `SELECT ... FOR UPDATE SKIP LOCKED` claim, the real dispatch, the real
`finish` (result + audit in one transaction, invariant 10) and the real
`caused_by` stamping. A handler that emits a payload the next stage cannot read
fails HERE and nowhere else.

**What is still injected, and why that is the whole trick.** Only the outbound
world: the LLM and the storage adapter. They are passed with `functools.partial`
against the registered handler, so the handler under test is the one the worker
runs -- not a copy, not a stub, not a re-registered double. Everything inside the
process stays real.

Usage:

    calls = chain.drain(conn, overrides={
        "backfill.scan": functools.partial(bh.handle_backfill_scan, store=store),
        "extract.doc":   functools.partial(eh.handle_extract_doc, llm=StubLlm()),
    })
    assert [c.tag for c in calls] == ["backfill.scan", "extract.doc", ...]
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Importing the runner is what populates HANDLERS: each handler module registers
# itself on import and `runner` imports all of them. Importing `app.handlers`
# alone gives an EMPTY registry, which is a silent pass rather than an error.
from app.handlers import HANDLERS
from app.workers import runner


class QueueDidNotSettle(AssertionError):
    """`drain` hit its limit with the queue still moving.

    Always a bug in the test or in the chain, never a reason to raise the limit
    blindly: a handler that re-enqueues its own tag loops forever, and the limit
    is what turns that into a failure instead of a hung suite.
    """


@dataclass
class Call:
    tag: str
    job_id: int
    result: object = None


@dataclass
class Drain:
    """What one drain did. `tags` is the sequence a chain assertion reads."""

    calls: list[Call] = field(default_factory=list)

    @property
    def tags(self) -> list[str]:
        return [c.tag for c in self.calls]

    def one(self, tag: str) -> Call:
        """The single call for `tag`, asserting there was exactly one."""
        hits = [c for c in self.calls if c.tag == tag]
        assert len(hits) == 1, f"{tag}: expected 1 call, saw {len(hits)}"
        return hits[0]

    def __len__(self) -> int:
        return len(self.calls)


def _recording(tag: str, fn, calls: list[Call]):
    def wrapped(conn, job):
        result = fn(conn, job)
        calls.append(Call(tag=tag, job_id=job["id"], result=result))
        return result

    return wrapped


def drain(conn, *, worker_id: str = "chain", overrides: dict | None = None,
          limit: int = 40, queue_cfg=None) -> Drain:
    """Run `runner.run_once` until the queue stops moving; return what ran.

    `overrides` replaces a tag's handler for this drain only -- HANDLERS itself is
    never mutated, because a test that re-registered a handler would leak that
    double into every later test in the same worker.

    `limit` is a guard against a handler that re-enqueues itself, not a budget:
    raise it only when a chain legitimately has more stages than that.
    """
    out = Drain()
    handlers = {tag: _recording(tag, fn, out.calls)
                for tag, fn in {**HANDLERS, **(overrides or {})}.items()}

    for _ in range(limit):
        if not runner.run_once(conn, worker_id, handlers=handlers,
                               queue_cfg=queue_cfg):
            return out
    raise QueueDidNotSettle(
        f"still claiming jobs after {limit} calls; ran {out.tags}")
