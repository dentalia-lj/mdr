"""tools.ladder_quality: the money guards of the Task 8 harness.

The harness sends real Batch API batches, so the parts that decide whether a
batch is sent, and that a sent batch is never lost or sent twice, are tested
here against a fake client: the cap refuses before anything is sent, the batch
id is on disk before collection starts, a resumed run collects instead of
resending, and an interrupted submission blocks the next one.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import anthropic
import pytest

from app.config import Models
from app.extract import economics, llm, pdf
from tools import ladder_quality as lq

TEXT_PDF = "IVOCLAR/Ivoclar ISO certifikat do 30_10_2027.pdf"
DOC = {"content_hash": "a" * 64, "path": "x.pdf", "set": "text", "gt": None}


class FakeBatches:
    def __init__(self):
        self.created: list[list[dict]] = []
        self.items: dict[str, list] = {}

    def create(self, requests):
        self.created.append(requests)
        return SimpleNamespace(id=f"msgbatch_{len(self.created)}")

    def retrieve(self, batch_id):
        return SimpleNamespace(processing_status="ended", request_counts={"succeeded": 1})

    def results(self, batch_id):
        return self.items.get(batch_id, [])


def _client():
    return SimpleNamespace(messages=SimpleNamespace(batches=FakeBatches()))


def _req(fixture_pdf, label="T1", missing=("type",)):
    doc = pdf.open_doc(fixture_pdf(TEXT_PDF))
    params = llm._params(label, Models(), doc, list(missing), llm.DEFAULT_MAX_TOKENS)
    return lq.request_record(DOC, label, params, lq.hc.request_key(params))


def _answer(usage=(1000, 200)):
    return anthropic.types.Message.model_validate({
        "id": "m", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
        "content": [{"type": "text", "text": '{"type": {"value": "ISO", "confidence": 0.9, '
                                             '"verbatim": "ISO 13485", "page": 1}}'}],
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": usage[0], "output_tokens": usage[1]}})


def _quiet(_line):
    pass


def test_a_round_past_the_cap_sends_nothing(fixture_pdf, tmp_path):
    state, client = lq.State(tmp_path / "state.json"), _client()

    out = lq.submit(state, client, [_req(fixture_pdf)], {}, Decimal("0.00001"), 1, _quiet)

    assert out is None
    assert client.messages.batches.created == []
    assert state.data["batches"] == {} and state.data["attempts"] == {}


def test_spend_so_far_counts_against_the_cap(fixture_pdf, tmp_path):
    state, client = lq.State(tmp_path / "state.json"), _client()
    state.data["batches"]["earlier"] = {"collected": True, "cost_usd": "0.8999",
                                        "estimate_usd": "0.5", "requests": {}}

    assert lq.submit(state, client, [_req(fixture_pdf)], {}, Decimal("0.90"), 2, _quiet) is None
    assert client.messages.batches.created == []


def test_the_batch_id_is_on_disk_before_collection(fixture_pdf, tmp_path):
    state, client = lq.State(tmp_path / "state.json"), _client()
    req = _req(fixture_pdf, "T2-first")

    ids = lq.submit(state, client, [req], {}, Decimal("0.90"), 1, _quiet)

    reread = lq.State(tmp_path / "state.json")
    assert ids == ["msgbatch_1"] and reread.uncollected() == ["msgbatch_1"]
    (cid, meta), = reread.data["batches"]["msgbatch_1"]["requests"].items()
    assert cid.startswith("T2-first_") and meta["key"] == req["key"]
    assert meta["label"] == "T2-first"
    assert reread.data["intent"] is None and reread.data["attempts"] == {req["key"]: 1}
    sent, = client.messages.batches.created
    assert sent == [{"custom_id": cid, "params": req["params"]}]


def test_a_resumed_run_collects_and_never_resends(fixture_pdf, tmp_path):
    client = _client()
    req = _req(fixture_pdf)
    lq.submit(lq.State(tmp_path / "state.json"), client, [req], {}, Decimal("0.90"), 1, _quiet)
    (cid,) = lq.State(tmp_path / "state.json").data["batches"]["msgbatch_1"]["requests"]
    client.messages.batches.items["msgbatch_1"] = [SimpleNamespace(
        custom_id=cid, result=SimpleNamespace(type="succeeded", message=_answer()))]

    # a crash here: a new process reads the state and collects
    state = lq.State(tmp_path / "state.json")
    cache = lq.Cache(tmp_path / "base", tmp_path / "own")
    lq.collect(state, cache, client, _quiet, poll_s=0)

    measured = economics.cost_usd("claude-haiku-5-5", economics.Usage(1000, 200), batch=True)
    assert len(client.messages.batches.created) == 1
    assert state.uncollected() == [] and state.spent() == measured
    assert lq.Cache(tmp_path / "base", tmp_path / "own").get(req["key"])["label"] == "T1"


def test_an_interrupted_submission_blocks_the_next(fixture_pdf, tmp_path):
    state, client = lq.State(tmp_path / "state.json"), _client()
    state.data["intent"] = {"at": "then", "round": 1, "requests": {}, "estimate_usd": "0.01"}

    with pytest.raises(SystemExit, match="interrupted"):
        lq.submit(state, client, [_req(fixture_pdf)], {}, Decimal("0.90"), 1, _quiet)
    assert client.messages.batches.created == []


def test_a_miss_stops_the_document_and_a_stored_answer_replays(fixture_pdf, tmp_path):
    cache = lq.Cache(tmp_path / "base", tmp_path / "own")
    client = lq.CachedClient(cache, DOC, set())
    client.label = "T1"
    req = _req(fixture_pdf)

    with pytest.raises(lq.Miss) as miss:
        client.create(**req["params"])
    assert miss.value.req["key"] == req["key"] and miss.value.req["label"] == "T1"

    cache.put(lq._meta(req), _answer().to_dict(), "msgbatch_1", Decimal("0.0001"))
    assert client.create(**req["params"]).content[0].text.startswith('{"type"')


def test_a_request_the_api_failed_twice_is_a_failed_read(fixture_pdf, tmp_path):
    cache = lq.Cache(tmp_path / "base", tmp_path / "own")
    req = _req(fixture_pdf)
    client = lq.CachedClient(cache, DOC, {req["key"]})
    client.label = "T1"

    assert client.create(**req["params"]).content == []     # parses to no fields

    client.label = llm.DOC_CLASS_LABEL
    with pytest.raises(lq.Failed):
        client.create(**req["params"])


def test_the_estimate_prices_every_page_image(fixture_pdf):
    text, image = _req(fixture_pdf, "T2-first"), _req(fixture_pdf, "T2-first")
    text["image_px"] = []

    assert image["image_px"] and image["class"] == ("claude-haiku-5-5", "image", False)
    floor = economics.cost_usd("claude-haiku-5-5", economics.Usage(
        input_tokens=int(sum(image["image_px"]) / lq.IMAGE_PX_PER_TOKEN)), batch=True)
    assert lq.estimate_usd(image, {}) - lq.estimate_usd(text, {}) >= floor


def _entry(path, **kw):
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(kw))


def _read(value, conf=0.9):
    import json
    return {"id": "m", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
            "content": [{"type": "text", "text": json.dumps(
                {"type": {"value": value, "confidence": conf, "verbatim": value, "page": 1}})}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 10}}


def test_todays_own_sonnet_read_fills_the_baseline_first(tmp_path):
    """The today arm takes the baseline fill (today's request) before the
    probe's Sonnet read; the Sonnet arm takes it only where that read was not
    asked the field; neither takes a field the fill was not asked."""
    h = "b" * 64
    _entry(tmp_path / "base" / h / "s55v.json", request_key="k1", content_hash=h, arm="s55v",
           kind="extract", model="claude-sonnet-5-5", message=_read("EC"))
    _entry(tmp_path / "own" / h / "fill.json", request_key="k2", content_hash=h,
           label=lq.TODAY_FILL, kind="extract", model="claude-sonnet-5-5", asked=["type"],
           message=_read("DoC"))
    cache = lq.Cache(tmp_path / "base", tmp_path / "own")

    first, cov = lq._sonnet_answers(h, ["type"], cache, {h: ["type"]}, asked_only=True)
    assert first["type"]["value"] == "DoC" and cov["fill"] == 1
    fallback, _ = lq._sonnet_answers(h, ["type"], cache, {h: ["type"]}, asked_only=False,
                                     fill="fallback")
    assert fallback["type"]["value"] == "EC"
    unasked, cov = lq._sonnet_answers(h, ["type"], cache, {h: []}, asked_only=True,
                                      fill="fallback")
    assert unasked["type"]["value"] == "DoC" and cov["fill"] == 1
    none, cov = lq._sonnet_answers(h, ["regulation"], cache, {h: []}, asked_only=True)
    assert none == {} and cov["none"] == 1
