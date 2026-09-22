"""Slice 2: the tier clients capture real token usage into `usage_log`.

Stubs carry a `.usage` on the response (the SDK shape); `extract()` / `results()`
still return the same fields they always did -- capture is a side effect that
rides on a response we already have, so the ladder and the RecordedLlm stub are
untouched. No API key needed.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from app.extract import llm, pdf
from tests.fixtures import corpus_manifest as m

TEXT_FIXTURE = m.by_name("Ivoclar ISO certifikat do 30_10_2027.pdf")
_ONE_FIELD = '{"regulation": {"value": "MDR", "confidence": 0.99, "verbatim": "x", "page": 1}}'


class Models:
    t1 = "claude-haiku-4-5"
    t2 = "claude-sonnet-4-6"
    rank = "claude-haiku-4-5"


def _usage(i, o, cr=None, cc=None):
    return SimpleNamespace(input_tokens=i, output_tokens=o,
                           cache_read_input_tokens=cr, cache_creation_input_tokens=cc)


def _resp(text, usage):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], usage=usage)


class _SyncClient:
    """messages.create returns a canned response carrying `.usage`."""

    def __init__(self, resp):
        self.messages = SimpleNamespace(create=lambda **kw: resp)


def test_sync_extract_captures_usage_and_cost(fixture_pdf):
    tier = llm.AnthropicLlm(_SyncClient(_resp(_ONE_FIELD, _usage(1596, 500))), Models())
    doc = pdf.open_doc(fixture_pdf(TEXT_FIXTURE))

    got = tier.extract("T1", doc, "f.pdf", missing=["regulation"])

    assert got["regulation"]["value"] == "MDR"          # normal return unchanged
    assert len(tier.usage_log) == 1
    call = tier.usage_log[0]
    assert (call.tier, call.model_id, call.batch) == ("T1", "claude-haiku-4-5", False)
    assert (call.usage.input_tokens, call.usage.output_tokens) == (1596, 500)
    # Haiku: (1596*1 + 500*5)/1e6 = 0.004096
    assert float(call.cost) == pytest.approx(0.004096)


def test_usage_log_accumulates_across_calls(fixture_pdf):
    tier = llm.AnthropicLlm(_SyncClient(_resp(_ONE_FIELD, _usage(1000, 100))), Models())
    doc = pdf.open_doc(fixture_pdf(TEXT_FIXTURE))
    tier.extract("T1", doc, "f.pdf", ["regulation"])
    tier.extract("T1", doc, "f.pdf", ["regulation"])
    assert len(tier.usage_log) == 2


def test_missing_usage_is_zero_not_error(fixture_pdf):
    # A response with no `.usage` (older stubs, partial mocks) -> zero-cost record,
    # never a crash.
    resp = SimpleNamespace(content=[SimpleNamespace(type="text", text=_ONE_FIELD)])
    tier = llm.AnthropicLlm(_SyncClient(resp), Models())
    doc = pdf.open_doc(fixture_pdf(TEXT_FIXTURE))
    tier.extract("T1", doc, "f.pdf", ["regulation"])
    assert float(tier.usage_log[0].cost) == 0.0


# --- batch client -----------------------------------------------------------------

def _batch_item(custom_id, text, usage):
    return SimpleNamespace(
        custom_id=custom_id,
        result=SimpleNamespace(type="succeeded", message=_resp(text, usage)),
    )


class _BatchClient:
    def __init__(self, items):
        self.messages = SimpleNamespace(
            batches=SimpleNamespace(results=lambda batch_id: items))


def test_batch_results_capture_usage_at_batch_rate():
    payload = '{"basic_udi_di": {"value": "76152082X", "confidence": 0.9, "verbatim": "y", "page": 2}}'
    items = [_batch_item("T2_deadbeef", payload, _usage(9753, 500))]
    client = llm.AnthropicBatchClient(_BatchClient(items), Models())

    out = client.results("b1")

    assert "basic_udi_di" in out                        # normal return unchanged
    assert len(client.usage_log) == 1
    call = client.usage_log[0]
    assert (call.tier, call.model_id, call.batch) == ("T2", "claude-sonnet-4-6", True)
    # Sonnet (9753*3 + 500*15)/1e6 = 0.036759; batch = half.
    assert float(call.cost) == pytest.approx(0.036759 / 2)


def test_batch_skips_failed_items_no_usage():
    failed = SimpleNamespace(custom_id="T1_x", result=SimpleNamespace(type="errored"))
    client = llm.AnthropicBatchClient(_BatchClient([failed]), Models())
    assert client.results("b1") == {}
    assert client.usage_log == []


# --- never-silent on an unpriced model --------------------------------------------

class _UnpricedModels:
    t1 = "some-unpriced-model"
    t2 = "some-unpriced-model"
    rank = "some-unpriced-model"


def test_unpriced_model_warns_but_records_tokens(fixture_pdf, caplog):
    tier = llm.AnthropicLlm(_SyncClient(_resp(_ONE_FIELD, _usage(100, 10))), _UnpricedModels())
    doc = pdf.open_doc(fixture_pdf(TEXT_FIXTURE))
    with caplog.at_level(logging.WARNING):
        got = tier.extract("T1", doc, "f.pdf", ["regulation"])
    assert got["regulation"]["value"] == "MDR"          # extraction not broken
    assert tier.usage_log[0].usage.input_tokens == 100  # tokens still captured
    assert "UNPRICED" in caplog.text                     # reported, not silent
    with pytest.raises(Exception):                        # cost access surfaces the gap
        _ = tier.usage_log[0].cost
