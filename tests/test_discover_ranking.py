"""T1 candidate ranking for the DISCOVER search rung (`app/extract/ranking.py`).

Unit-level: the Anthropic client is a stub, so nothing here makes a network call
or needs Postgres. The wiring these guard is the one that decides which URLs get
fetched, and the failure mode that costs real money is a score landing on the
wrong candidate -- so the index-matching tests are the point of the file, not
padding.
"""

from __future__ import annotations

import json
import types

import pytest

from app.adapters.search import SearchCandidate
from app.extract import ranking
from app.handlers.discover import GroupFacts


# --- stubs ------------------------------------------------------------------

class _Resp:
    def __init__(self, payload, *, text=None, usage=None):
        body = text if text is not None else json.dumps(payload)
        self.content = [types.SimpleNamespace(type="text", text=body)]
        self.usage = usage or types.SimpleNamespace(
            input_tokens=100, output_tokens=50,
            cache_read_input_tokens=0, cache_creation_input_tokens=0,
        )


class _Client:
    """Records the params it was called with, so the prompt shape is assertable."""

    def __init__(self, resp):
        self._resp = resp
        self.calls: list[dict] = []
        self.messages = types.SimpleNamespace(create=self._create)

    def _create(self, **params):
        self.calls.append(params)
        return self._resp


class _Models:
    rank = "claude-haiku-4-5"


def _facts(**kw):
    return GroupFacts(
        group_id=kw.get("group_id", 7),
        manufacturer=kw.get("manufacturer", "RENFERT"),
        label=kw.get("label", "SVEDER T63"),
        basic_udi_di=kw.get("basic_udi_di"),
        member_mfr_refs=kw.get("member_mfr_refs", []),
    )


def _cands(n):
    return [
        SearchCandidate(url=f"https://x.com/{i}.pdf", title=f"t{i}",
                        snippet=f"s{i}", rank=i)
        for i in range(n)
    ]


def _scored(pairs):
    return {"scores": [{"index": i, "score": s, "reason": "r"} for i, s in pairs]}


# --- the contract -----------------------------------------------------------

def test_returns_one_pair_per_candidate_in_input_order():
    cands = _cands(3)
    client = _Client(_Resp(_scored([(0, 0.9), (1, 0.2), (2, 0.75)])))
    out = ranking.AnthropicRanker(client, _Models())(_facts(), cands)

    assert [c for c, _ in out] == cands
    assert [s for _, s in out] == [0.9, 0.2, 0.75]


def test_scores_land_on_the_index_the_model_named_not_the_reply_order():
    """The whole reason the schema carries an explicit index. A model that
    answers out of order must not shift 0.95 onto the wrong URL."""
    cands = _cands(3)
    client = _Client(_Resp(_scored([(2, 0.95), (0, 0.1), (1, 0.4)])))
    out = ranking.AnthropicRanker(client, _Models())(_facts(), cands)

    assert [s for _, s in out] == [0.1, 0.4, 0.95]


def test_a_candidate_the_model_skipped_scores_zero_and_is_still_returned():
    cands = _cands(3)
    client = _Client(_Resp(_scored([(0, 0.9)])))
    out = ranking.AnthropicRanker(client, _Models())(_facts(), cands)

    assert len(out) == 3
    assert [s for _, s in out] == [0.9, 0.0, 0.0]


@pytest.mark.parametrize("bad_index", [-1, 3, 99, "0", None, 1.5])
def test_an_out_of_range_or_non_int_index_is_dropped_never_applied(bad_index):
    cands = _cands(2)
    payload = {"scores": [{"index": bad_index, "score": 0.99, "reason": "r"},
                          {"index": 0, "score": 0.5, "reason": "r"}]}
    out = ranking.AnthropicRanker(_Client(_Resp(payload)), _Models())(_facts(), cands)

    assert [s for _, s in out] == [0.5, 0.0]


def test_a_duplicate_index_keeps_the_first_score():
    cands = _cands(2)
    payload = _scored([(0, 0.8), (0, 0.1), (1, 0.3)])
    out = ranking.AnthropicRanker(_Client(_Resp(payload)), _Models())(_facts(), cands)

    assert [s for _, s in out] == [0.8, 0.3]


@pytest.mark.parametrize("raw,expected", [(2.5, 1.0), (-0.4, 0.0), (1.0, 1.0), (0.0, 0.0)])
def test_scores_are_clamped_into_the_threshold_rules_range(raw, expected):
    out = ranking.AnthropicRanker(
        _Client(_Resp(_scored([(0, raw)]))), _Models()
    )(_facts(), _cands(1))
    assert out[0][1] == expected


def test_a_non_numeric_score_is_treated_as_absent():
    payload = {"scores": [{"index": 0, "score": "high", "reason": "r"}]}
    out = ranking.AnthropicRanker(_Client(_Resp(payload)), _Models())(_facts(), _cands(1))
    assert out[0][1] == 0.0


# --- degrade paths ----------------------------------------------------------

def test_truncated_json_degrades_to_all_zero_rather_than_raising():
    """`_search` would catch a raise, but silently scoring nothing is the
    behaviour that keeps the ladder moving: nothing clears the threshold, the
    rung logs a miss, the group reaches manual."""
    client = _Client(_Resp(None, text='{"scores": [{"index": 0, "sco'))
    out = ranking.AnthropicRanker(client, _Models())(_facts(), _cands(2))

    assert [s for _, s in out] == [0.0, 0.0]


def test_no_candidates_makes_no_api_call():
    client = _Client(_Resp(_scored([])))
    assert ranking.AnthropicRanker(client, _Models())(_facts(), []) == []
    assert client.calls == []


def test_an_unpriced_model_still_ranks():
    """`cost_usd` raises UnknownModelPricing by design. That must not turn a
    successful ranking into a failed one -- the cost is a log line."""
    class _Odd:
        rank = "claude-experimental-9"

    out = ranking.AnthropicRanker(
        _Client(_Resp(_scored([(0, 0.7)]))), _Odd()
    )(_facts(), _cands(1))
    assert out[0][1] == 0.7


# --- the request ------------------------------------------------------------

def test_request_uses_models_rank_and_the_structured_schema():
    client = _Client(_Resp(_scored([(0, 0.5)])))
    ranking.AnthropicRanker(client, _Models())(_facts(), _cands(1))

    params = client.calls[0]
    assert params["model"] == "claude-haiku-4-5"
    assert params["output_config"]["format"]["schema"] == ranking.SCHEMA
    assert params["system"] == ranking.RANK_SYSTEM


def test_prompt_numbers_candidates_by_array_position():
    client = _Client(_Resp(_scored([(0, 0.5)])))
    ranking.AnthropicRanker(client, _Models())(_facts(), _cands(3))

    body = client.calls[0]["messages"][0]["content"]
    assert "[0] https://x.com/0.pdf" in body
    assert "[2] https://x.com/2.pdf" in body


def test_prompt_carries_manufacturer_label_and_capped_refs():
    client = _Client(_Resp(_scored([(0, 0.5)])))
    facts = _facts(manufacturer="RENFERT", label="SVEDER T63",
                   member_mfr_refs=[f"R{i}" for i in range(50)])
    ranking.AnthropicRanker(client, _Models())(facts, _cands(1))

    body = client.calls[0]["messages"][0]["content"]
    assert "RENFERT" in body and "SVEDER T63" in body
    assert "R0" in body and "R19" in body
    # Capped at 20: a group can hold thousands of members.
    assert "R20" not in body


def test_a_group_with_no_label_omits_the_label_line():
    client = _Client(_Resp(_scored([(0, 0.5)])))
    ranking.AnthropicRanker(client, _Models())(_facts(label=None), _cands(1))

    assert "catalogue label" not in client.calls[0]["messages"][0]["content"]


def test_last_cost_is_captured_for_the_caller_that_logs_it():
    client = _Client(_Resp(_scored([(0, 0.5)])))
    r = ranking.AnthropicRanker(client, _Models())
    r(_facts(), _cands(1))

    assert r.last_cost.model_id == "claude-haiku-4-5"
    assert r.last_cost.usage.input_tokens == 100
    assert r.last_cost.cost > 0


# --- crawl link triage (`CrawlLinkRanker`, 2026-09-04) -----------------------

def _links(n):
    return [SearchCandidate(url=f"https://m.example/d/{i}.pdf", title=f"text {i}",
                            snippet="", rank=i) for i in range(n)]


def _judged(rows):
    return _Resp({"judgements": rows})


def test_crawl_judgements_land_on_the_index_the_model_named():
    client = _Client(_judged([
        {"index": 2, "type": "DoC", "article_numbers": ["196.644.050"], "confidence": 0.95, "reason": "declaration"},
        {"index": 0, "type": "IFU", "article_numbers": [], "confidence": 0.8, "reason": "ifu"},
    ]))
    out = ranking.CrawlLinkRanker(client, _Models())("RENFERT", _links(3))
    assert [j.url for j in out] == [c.url for c in _links(3)]
    assert out[2].type == "DoC" and out[2].article_numbers == ("196.644.050",)
    assert out[0].type == "IFU"
    assert out[1].type == "other" and out[1].confidence == 0.0   # unjudged, never dropped


def test_crawl_judgement_with_an_unknown_type_or_bad_confidence_is_other_at_zero():
    client = _Client(_judged([
        {"index": 0, "type": "brochure", "article_numbers": [], "confidence": "high", "reason": ""},
        {"index": 1, "type": "EC", "article_numbers": [], "confidence": 7, "reason": ""},
    ]))
    out = ranking.CrawlLinkRanker(client, _Models())("RENFERT", _links(2))
    assert (out[0].type, out[0].confidence) == ("other", 0.0)
    assert (out[1].type, out[1].confidence) == ("EC", 1.0)      # clamped


def test_crawl_prompt_carries_the_link_text_and_uses_the_crawl_schema():
    client = _Client(_judged([
        {"index": 0, "type": "IFU", "article_numbers": [], "confidence": 0.5, "reason": "x"},
    ]))
    ranking.CrawlLinkRanker(client, _Models())("RENFERT", _links(2))
    params = client.calls[0]
    assert params["system"] == ranking.CRAWL_SYSTEM
    assert params["output_config"]["format"]["schema"] == ranking.CRAWL_SCHEMA
    assert "[1] https://m.example/d/1.pdf" in params["messages"][0]["content"]
    assert "text: text 1" in params["messages"][0]["content"]


def test_a_batch_the_model_answered_nothing_for_raises_rather_than_reading_as_other():
    """Measured 2026-09-07: a batch of 150 truncated at 8k tokens, came back as
    unterminated JSON, and every link in it silently became `other` -- which
    sorts last, so a truncated batch buries whatever declarations it held. The
    caller degrades the whole crawl to page order instead."""
    client = _Client(_judged([]))
    with pytest.raises(ranking.JudgementTruncated):
        ranking.CrawlLinkRanker(client, _Models())("RENFERT", _links(3))


def test_a_partly_answered_batch_is_kept_and_the_rest_read_as_unjudged():
    client = _Client(_judged([
        {"index": 1, "type": "DoC", "article_numbers": [], "confidence": 0.9, "reason": "d"},
    ]))
    out = ranking.CrawlLinkRanker(client, _Models())("RENFERT", _links(3))
    assert out[1].type == "DoC"
    assert [j.reason for j in out].count(ranking._UNJUDGED) == 2


def test_crawl_no_links_makes_no_api_call():
    client = _Client(_judged([]))
    assert ranking.CrawlLinkRanker(client, _Models())("RENFERT", []) == []
    assert client.calls == []
