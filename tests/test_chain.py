"""A document walks four stages of the REAL queue (F20).

Twelve of eighteen job families are exercised only as a direct handler call, so
what the queue does BETWEEN two handlers -- claim, dispatch, finish, audit,
lineage -- was covered by nothing. These tests close that: the only thing
injected is the outbound world (LLM, storage), and every stage transition goes
through `runner.run_once`.

The chain is BACKFILL -> EXTRACT -> VALIDATE -> GATE, driven from a committed
fixture PDF with a stub LLM, so it costs nothing and reaches the registry.
"""

from __future__ import annotations

import functools
import shutil

import pytest

from app import queue
from app.adapters.storage import LocalFsStore
from app.handlers import backfill as bh
from app.handlers import extract as eh
from tests import chain

DOC = "LUMIWHITE/Declaration_of_Conformity_LUMIWHITE_OU.pdf"

CHAIN = ["backfill.scan", "extract.doc", "validate.doc", "gate.candidate"]


#: Fields whose value the DATABASE constrains. A stub that returns "x" for
#: `coverage_scope` does not produce a test failure in EXTRACT -- it produces a
#: CheckViolation three stages later in GATE, which is a real thing this chain
#: can catch and a useless way to spend it.
_VOCABULARY = {"coverage_scope": "group", "stated_class": "IIa",
               "type": "DoC", "regulation": "MDR"}

#: Left null on purpose: a stub that invented a `ref_list` would make the REF
#: gate fire on fiction. These tests are about the queue between the stages, not
#: about what the model returns, so the document lands `staged` on
#: `no-item-identifier` -- the honest outcome for a declaration whose article
#: list nobody read.
_NULL = {"ref_list", "basic_udi_di", "referenced_docs"}


class StubLlm:
    """Fills escalated scalars with values the schema accepts."""

    def extract(self, tier, doc, filename, missing):
        return {
            f: {"value": None if f in _NULL else _VOCABULARY.get(f, "x"),
                "conf": 0.96, "tier": tier, "verbatim": "x", "page": 1,
                "model_id": "stub"}
            for f in missing
        }


@pytest.fixture
def walked(conn, fixture_pdf, tmp_path):
    """One document, carried from a folder scan to a gate decision."""
    shutil.copy(fixture_pdf(DOC), tmp_path / "doc.pdf")
    store = LocalFsStore(str(tmp_path / "archive"))

    job_id = queue.enqueue(conn, "backfill.scan",
                           {"drive_folder": str(tmp_path)}, "chain:backfill")
    conn.commit()

    ran = chain.drain(conn, overrides={
        "backfill.scan": functools.partial(bh.handle_backfill_scan, store=store),
        "extract.doc": functools.partial(eh.handle_extract_doc, llm=StubLlm()),
    })
    return {"seed_job": job_id, "ran": ran, "store": store}


def test_a_declaration_walks_four_stages_of_the_real_queue(walked):
    """Not "each handler works" -- "each handler's OUTPUT is claimable by the
    next one". A payload the next stage cannot read fails here and in no other
    test in this suite."""
    assert walked["ran"].tags == CHAIN


def test_the_walk_ends_with_one_evidenced_document(walked, conn):
    """Invariant 2 across a whole chain: GATE rejects a candidate without the
    evidence tuple, so a document that reached the registry proves the tuple
    survived three payload hand-offs."""
    docs = conn.execute("SELECT doc_id, status FROM document").fetchall()
    assert len(docs) == 1, docs
    doc_id = docs[0]["doc_id"]
    assert docs[0]["status"] in ("production", "staged", "filed")

    rows = conn.execute(
        "SELECT field, archive_url, verbatim, tier, extracted_at "
        "  FROM evidence WHERE doc_id = %s", (doc_id,)).fetchall()
    assert rows, "a gated document with no evidence"
    for r in rows:
        assert r["archive_url"] and r["verbatim"] and r["tier"]
        assert r["extracted_at"] is not None


def test_every_emitted_job_carries_the_lineage_of_the_one_that_emitted_it(
        walked, conn):
    """`caused_by` is stamped by the RUNNER, around the dispatch and nowhere
    else, so no direct-call test can show it. Without it the audit trail cannot
    say which scan produced which registry write."""
    rows = {r["type"]: r for r in conn.execute(
        "SELECT type, id, caused_by, status FROM job").fetchall()}

    assert rows["backfill.scan"]["caused_by"] is None, \
        "the seed job was enqueued by a person, not by another job"
    for tag in CHAIN[1:]:
        assert rows[tag]["caused_by"] is not None, f"{tag} has no lineage"

    # The chain is a chain, not a fan-out from the seed: each stage names the
    # stage before it.
    for parent, child in zip(CHAIN, CHAIN[1:]):
        assert rows[child]["caused_by"] == rows[parent]["id"], \
            f"{child} should be caused by {parent}"


def test_every_job_the_chain_ran_finished(walked, conn):
    """A stage that raised would leave `failed`/`dead` behind while the tags
    above still read as a clean walk -- the drain only proves something was
    claimed, not that it succeeded."""
    stuck = conn.execute(
        "SELECT type, status, last_error FROM job WHERE status <> 'done'"
    ).fetchall()
    assert not stuck, stuck


def test_a_second_drain_over_unchanged_content_moves_nothing(walked, conn):
    """AC5 at chain scale. The producer-level proof exists
    (`test_backfill_second_cycle_emits_nothing`); this is the same claim with the
    queue in the loop, which is where a re-emitted job would actually appear."""
    again = chain.drain(conn, overrides={
        "backfill.scan": functools.partial(bh.handle_backfill_scan,
                                           store=walked["store"]),
        "extract.doc": functools.partial(eh.handle_extract_doc, llm=StubLlm()),
    })
    assert again.tags == []
