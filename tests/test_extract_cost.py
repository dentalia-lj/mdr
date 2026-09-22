"""Slice 3: the economics ledger (extraction_cost). Drives the REAL extract.doc
handler with the REAL tier clients wrapping usage-carrying fakes, so the whole
capture -> price -> persist path is exercised against Postgres. No API key.

Covers: sync writes one cost row per LLM tier at the attempt's rev; a T0-only doc
(MSDS) writes none; the batch path writes each tier's row as it resolves and stays
idempotent across the polls that re-visit an already-resolved tier; an unpriced
model still records tokens with a null cost; the extraction_spend rollup sums up.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from app import queue
from app.extract import llm
from app.extract import tiers as tiers_mod
from app.handlers import extract as eh
from tests.fixtures import corpus_manifest as m

ISO_CERT = m.by_name("Ivoclar ISO certifikat do 30_10_2027.pdf")
MSDS = m.by_name("pattern-resin-ls-liquid-sds-ca-en.pdf")


class Models:
    t1 = "claude-haiku-4-5"
    t2 = "claude-sonnet-4-6"
    rank = "claude-haiku-4-5"


class UnpricedModels:
    t1 = "some-unpriced-model"
    t2 = "some-unpriced-model"
    rank = "some-unpriced-model"


def _usage(i, o):
    return SimpleNamespace(input_tokens=i, output_tokens=o,
                           cache_read_input_tokens=None, cache_creation_input_tokens=None)


def _resp(payload, usage):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=payload)], usage=usage)


def _payload(fields, conf=0.99):
    """Structured-output JSON for the given fields (arrays for list fields)."""
    d = {}
    for f in fields:
        val = [] if f in ("ref_list", "referenced_docs") else f"v_{f}"
        d[f] = {"value": val, "confidence": conf, "verbatim": f"verb {f}", "page": 1}
    return json.dumps(d)


def _rows(conn, content_hash):
    return conn.execute(
        "SELECT tier, model_id, batch, input_tokens, output_tokens, cost_usd "
        "FROM extraction_cost WHERE content_hash=%s ORDER BY tier",
        (content_hash,),
    ).fetchall()


# --- sync path --------------------------------------------------------------------

class _SyncClient:
    """A low-level Anthropic stand-in: messages.create returns a canned response
    carrying `.usage`. Wrapped by the real AnthropicLlm so capture runs for real."""

    def __init__(self, payload, usage):
        self.messages = SimpleNamespace(create=lambda **kw: _resp(payload, usage))


def test_sync_writes_one_cost_row_per_llm_tier(conn, fixture_pdf):
    # ISO cert (text) -> T0 then T1 fills the rest (payload covers all fields -> no T2).
    tier_client = llm.AnthropicLlm(_SyncClient(_payload(tiers_mod.TARGET), _usage(1500, 400)),
                                   Models())
    job = {"payload": {"archive_url": fixture_pdf(ISO_CERT),
                       "content_hash": "sha-cost-sync", "group_id": 1}}

    result = eh.handle_extract_doc(conn, job, llm=tier_client)
    conn.commit()

    assert result["tiers_used"] == ["T0", "T1"]
    rows = _rows(conn, "sha-cost-sync")
    assert len(rows) == 1                                    # T0 is free -> no row
    (r,) = rows
    assert r["tier"] == "T1"
    assert r["model_id"] == "claude-haiku-4-5"
    assert r["batch"] is False
    assert (r["input_tokens"], r["output_tokens"]) == (1500, 400)
    # Haiku (1500*1 + 400*5)/1e6 = 0.0035
    assert float(r["cost_usd"]) == 0.0035


def test_t0_only_doc_writes_no_cost_rows(conn, fixture_pdf):
    # MSDS short-circuits at T0 (no LLM call) -> no cost rows at all.
    tier_client = llm.AnthropicLlm(_SyncClient(_payload(tiers_mod.TARGET), _usage(1, 1)),
                                   Models())
    job = {"payload": {"archive_url": fixture_pdf(MSDS),
                       "content_hash": "sha-cost-msds", "group_id": 2}}

    result = eh.handle_extract_doc(conn, job, llm=tier_client)
    conn.commit()

    assert result["doc_class"] == "msds"
    assert result["tiers_used"] == ["T0"]
    assert _rows(conn, "sha-cost-msds") == []
    assert tier_client.usage_log == []                       # LLM never called


def test_unpriced_model_records_tokens_with_null_cost(conn, fixture_pdf):
    tier_client = llm.AnthropicLlm(_SyncClient(_payload(tiers_mod.TARGET), _usage(120, 40)),
                                   UnpricedModels())
    job = {"payload": {"archive_url": fixture_pdf(ISO_CERT),
                       "content_hash": "sha-cost-unpriced", "group_id": 3}}

    eh.handle_extract_doc(conn, job, llm=tier_client)
    conn.commit()

    (r,) = _rows(conn, "sha-cost-unpriced")
    assert r["input_tokens"] == 120                          # tokens kept
    assert r["cost_usd"] is None                             # never silently 0


# --- batch path -------------------------------------------------------------------

def _item(tier, content_hash, payload, usage):
    return SimpleNamespace(
        custom_id=f"{tier}_{content_hash}",
        result=SimpleNamespace(type="succeeded", message=_resp(payload, usage)),
    )


class _LowBatches:
    """Anthropic messages.batches stand-in: batches are immediately 'ended'; the
    real AnthropicBatchClient.results() reads `.usage` off each item's message."""

    def __init__(self, items_by_tier):
        self.items_by_tier = items_by_tier
        self._tier = {}
        self._n = 0

    def create(self, requests):
        self._n += 1
        bid = f"batch-{self._n}"
        self._tier[bid] = requests[0]["custom_id"].split("_", 1)[0]
        return SimpleNamespace(id=bid)

    def retrieve(self, bid):
        return SimpleNamespace(processing_status="ended")

    def results(self, bid):
        return list(self.items_by_tier[self._tier[bid]])


def _low_client(items_by_tier):
    return SimpleNamespace(messages=SimpleNamespace(batches=_LowBatches(items_by_tier)))


def _enqueue(conn, content_hash, group_id, path):
    jid = queue.enqueue(conn, "extract.doc",
                        {"archive_url": path, "content_hash": content_hash, "group_id": group_id},
                        f"extract:{content_hash}")
    conn.commit()
    return conn.execute("SELECT * FROM job WHERE id=%s", (jid,)).fetchone()


def _drive_batch(conn, job, batch_client, limit=8):
    r = None
    for _ in range(limit):
        r = eh.handle_extract_doc(conn, job, batch_client=batch_client, poll_interval_s=0)
        conn.commit()
        if isinstance(r, dict) and r.get("_deferred"):
            continue
        break
    return r


def test_batch_writes_cost_row_at_batch_rate(conn, fixture_pdf):
    job = _enqueue(conn, "sha-cost-b1", 5, fixture_pdf(ISO_CERT))
    items = {"T1": [_item("T1", "sha-cost-b1", _payload(tiers_mod.TARGET), _usage(1500, 400))]}
    bc = llm.AnthropicBatchClient(_low_client(items), Models())

    r = _drive_batch(conn, job, bc)

    assert r["tiers_used"] == ["T0", "T1"]
    (row,) = _rows(conn, "sha-cost-b1")
    assert row["tier"] == "T1" and row["batch"] is True
    # Haiku batch: 0.0035 / 2 = 0.00175
    assert float(row["cost_usd"]) == 0.00175


def test_batch_two_tiers_two_rows_idempotent(conn, fixture_pdf):
    # The ISO cert is manufacturer-scope, so item-ids aren't chased; regulation is
    # the always-escalated field T0 misses. T1 leaves it -> it escalates to T2. Across
    # the polls T1 is re-visited via the resolved branch; the row must stay unique.
    items = {
        "T1": [_item("T1", "sha-cost-b2", _payload([]), _usage(1500, 400))],
        "T2": [_item("T2", "sha-cost-b2", _payload(["regulation"]), _usage(8000, 500))],
    }
    job = _enqueue(conn, "sha-cost-b2", 6, fixture_pdf(ISO_CERT))
    bc = llm.AnthropicBatchClient(_low_client(items), Models())

    r = _drive_batch(conn, job, bc)

    assert r["tiers_used"] == ["T0", "T1", "T2"]
    rows = _rows(conn, "sha-cost-b2")
    assert [x["tier"] for x in rows] == ["T1", "T2"]         # exactly one row each
    t1, t2 = rows
    assert float(t1["cost_usd"]) == 0.00175                  # Haiku batch
    # Sonnet batch: (8000*3 + 500*15)/1e6 / 2 = 0.0315 / 2 = 0.01575
    assert float(t2["cost_usd"]) == 0.01575


def test_spend_view_sums_measured_cost(conn, fixture_pdf):
    tier_client = llm.AnthropicLlm(_SyncClient(_payload(tiers_mod.TARGET), _usage(1000, 200)),
                                   Models())
    job = {"payload": {"archive_url": fixture_pdf(ISO_CERT),
                       "content_hash": "sha-cost-view", "group_id": 8}}
    eh.handle_extract_doc(conn, job, llm=tier_client)
    conn.commit()

    row = conn.execute(
        "SELECT calls, input_tokens, cost_usd, unpriced_calls FROM extraction_spend "
        "WHERE model_id='claude-haiku-4-5' AND tier='T1' AND batch=false"
    ).fetchone()
    assert row["calls"] >= 1
    assert row["unpriced_calls"] == 0
    assert row["cost_usd"] is not None
