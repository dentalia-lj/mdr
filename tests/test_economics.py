"""Slice 1: the pure pricing core. No API key, no DB -- this is where the
chars-based cost ESTIMATE is proven against exact token arithmetic."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.extract import economics
from app.extract.economics import Usage, cost_usd


def _usage_msg(**kw):
    """A stand-in for an Anthropic Message: an object whose `.usage` carries the
    given token attrs (SimpleNamespace mimics the SDK's usage object)."""
    return SimpleNamespace(usage=SimpleNamespace(**kw))


# --- cost_usd: exact arithmetic (in/out per MTok) --------------------------------

@pytest.mark.parametrize(
    "model_id, usage, batch, expected",
    [
        # Haiku 4.5: $1 in / $5 out per MTok.
        ("claude-haiku-4-5", Usage(1000, 1000), False, Decimal("0.006")),
        ("claude-haiku-4-5", Usage(1000, 1000), True, Decimal("0.003")),  # batch 50%
        # Sonnet 4.6: $3 in / $15 out per MTok.
        ("claude-sonnet-4-6", Usage(1000, 1000), False, Decimal("0.018")),
        ("claude-sonnet-4-6", Usage(1000, 1000), True, Decimal("0.009")),
        # Output-only / input-only isolate each rate.
        ("claude-haiku-4-5", Usage(0, 1000), False, Decimal("0.005")),
        ("claude-haiku-4-5", Usage(1000, 0), False, Decimal("0.001")),
        # Zero usage -> zero cost (not an error).
        ("claude-haiku-4-5", Usage(), False, Decimal("0")),
    ],
)
def test_cost_exact(model_id, usage, batch, expected):
    assert cost_usd(model_id, usage, batch) == expected


def test_cache_read_discounted_and_write_surcharged():
    # cache_read = 0.1x input rate, cache_creation = 1.25x input rate (Haiku $1/MTok).
    read = cost_usd("claude-haiku-4-5", Usage(cache_read_tokens=1000))
    write = cost_usd("claude-haiku-4-5", Usage(cache_creation_tokens=1000))
    assert read == Decimal("1000") * Decimal("0.1") * Decimal("1") / Decimal(1_000_000)
    assert write == Decimal("1000") * Decimal("1.25") * Decimal("1") / Decimal(1_000_000)
    # Non-cached input tokens are separate from cache counts and add on top.
    both = cost_usd("claude-haiku-4-5", Usage(input_tokens=1000, cache_read_tokens=1000))
    assert both == cost_usd("claude-haiku-4-5", Usage(input_tokens=1000)) + read


def test_unknown_model_raises_never_silent():
    with pytest.raises(economics.UnknownModelPricing):
        cost_usd("gpt-4", Usage(1000, 1000))
    # It is a KeyError subclass but must carry the offending id.
    with pytest.raises(economics.UnknownModelPricing, match="mystery-model"):
        cost_usd("mystery-model", Usage(1, 1))


# --- the ESTIMATE, now proven ----------------------------------------------------

def test_reproduces_documented_per_doc_estimates():
    """The session's cost quote: a median text doc on T1 ~ $0.004, a scanned doc on
    T2 vision ~ $0.037. Proven here from the token counts behind those figures."""
    text_t1 = cost_usd("claude-haiku-4-5", Usage(input_tokens=1596, output_tokens=500))
    assert float(text_t1) == pytest.approx(0.0041, abs=5e-4)
    scan_t2 = cost_usd("claude-sonnet-4-6", Usage(input_tokens=9753, output_tokens=500))
    assert float(scan_t2) == pytest.approx(0.037, abs=1e-3)
    # Batch is exactly half.
    assert cost_usd("claude-sonnet-4-6", Usage(9753, 500), batch=True) == scan_t2 / 2


# --- Usage.from_message: defensive reader ----------------------------------------

def test_from_message_reads_sdk_shape():
    msg = _usage_msg(
        input_tokens=1200,
        output_tokens=340,
        cache_read_input_tokens=800,
        cache_creation_input_tokens=64,
    )
    u = Usage.from_message(msg)
    assert (u.input_tokens, u.output_tokens) == (1200, 340)
    assert (u.cache_read_tokens, u.cache_creation_tokens) == (800, 64)


def test_from_message_tolerates_missing_and_none():
    # Caching off -> cache fields are None; must read as 0, not crash.
    u = Usage.from_message(_usage_msg(input_tokens=10, output_tokens=5,
                                      cache_read_input_tokens=None,
                                      cache_creation_input_tokens=None))
    assert (u.cache_read_tokens, u.cache_creation_tokens) == (0, 0)
    # No usage attribute at all -> all zeros.
    assert Usage.from_message(SimpleNamespace()) == Usage()
    assert Usage.from_message(SimpleNamespace(usage=None)) == Usage()
