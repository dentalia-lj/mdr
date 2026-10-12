"""Economics: measured token usage -> USD cost for the LLM tiers.

Every messages.create / batch result carries a `usage` object; the tier clients
capture it (llm.py) and this module prices it. Pure and DB-free, so the cost math
is unit-tested with no API key -- this is where the chars-based ESTIMATE becomes a
MEASURED number (S0.4 findings / the KPI board's 'sweep spend vs budget cap').

Prices are per-model constants here (a config-driven override is a later 1-line
addition). A model with no price entry RAISES rather than silently costing 0 -- an
economics ledger must never under-report (the never-silent rule).

Token accounting matches the Anthropic API: `input_tokens` is already the
NON-cached input count, so cache_read / cache_creation are billed additively on
top at their own multipliers (this pipeline doesn't cache today, so both are 0 and
cost reduces to input*rate + output*rate).

One model is priced by prompt length: Haiku 5.5 bills a request whose prompt is
over 100,000 tokens at a second, higher pair of rates, output included. "Prompt"
counts every input token, cache reads and writes too, and each request is priced
on its own (pricing page, read 2026-10-12).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


class UnknownModelPricing(KeyError):
    """No price entry for a model_id. Raised instead of silently pricing at 0."""


@dataclass(frozen=True)
class Price:
    """Per-MTok USD rates. cache_read / cache_write are multipliers on the input
    rate (Anthropic: 5-min cache write = 1.25x input, cache read = 0.1x input,
    except where a row says otherwise).

    `long_prompt_tokens` set means the model is priced by prompt length: a
    request whose prompt (input + cache read + cache write tokens) is OVER it
    pays `long_input_per_mtok` / `long_output_per_mtok` for the whole call, and
    the cache multipliers apply to the long input rate."""

    input_per_mtok: Decimal
    output_per_mtok: Decimal
    cache_read_mult: Decimal = Decimal("0.1")
    cache_write_mult: Decimal = Decimal("1.25")
    long_prompt_tokens: int | None = None
    long_input_per_mtok: Decimal | None = None
    long_output_per_mtok: Decimal | None = None

    def rates(self, prompt_tokens: int) -> tuple[Decimal, Decimal]:
        """(input, output) per MTok for a request with this many prompt tokens."""
        if self.long_prompt_tokens is not None and prompt_tokens > self.long_prompt_tokens:
            return self.long_input_per_mtok, self.long_output_per_mtok
        return self.input_per_mtok, self.output_per_mtok


# Model prices, checked against platform.claude.com/docs/en/about-claude/pricing
# on 2026-09-28, and again on 2026-10-12 for the Haiku 5.5 row and the Sonnet 5.5
# cache read. Keys are config.models ids (config.models.t1 / .t2 / .vision_first
# / .second_read). Add a row here when a tier's model is swapped. Sonnet 5 was
# $3/$15 here until 2026-09-28; its $2/$10 launch price became the standard one,
# so every Sonnet 5 row logged before it overstates the spend by half (the ledger
# stores the computed cost).
PRICES: dict[str, Price] = {
    "claude-haiku-4-5": Price(Decimal("1"), Decimal("5")),
    # Cheap reads of the 2026-10-12 ladder. $0.10 / $0.50 up to 100,000 prompt
    # tokens, $0.50 / $2.50 over; cache hit $0.01 (0.1x), 5-min write $0.125.
    "claude-haiku-5-5": Price(Decimal("0.10"), Decimal("0.50"),
                              long_prompt_tokens=100_000,
                              long_input_per_mtok=Decimal("0.50"),
                              long_output_per_mtok=Decimal("2.50")),
    # T2 from 2026-09-29. Its cache hit is $0.10, 0.05x the input rate, not the
    # 0.1x default (2026-10-12; nothing caches yet, so no logged row is off).
    "claude-sonnet-5-5": Price(Decimal("2"), Decimal("10"), cache_read_mult=Decimal("0.05")),
    "claude-sonnet-5": Price(Decimal("2"), Decimal("10")),  # retained: prices already-logged spend
    "claude-sonnet-4-6": Price(Decimal("3"), Decimal("15")),  # retained: prices already-logged spend
}

BATCH_DISCOUNT = Decimal("0.5")  # Batch API = flat 50% off the whole call.
_MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class Usage:
    """Token usage from one LLM response. cache_* default 0 so a stub -- or a
    response with caching off -- only needs input/output."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    @classmethod
    def from_message(cls, msg) -> "Usage":
        """Read `msg.usage`. A messages.create response IS a Message (has .usage);
        a batch result wraps it as item.result.message -- pass either's .message.
        Missing / None fields -> 0 (cache fields are None when caching is off, and
        a stub may set only some)."""
        u = getattr(msg, "usage", None)
        if u is None:
            return cls()
        return cls(
            input_tokens=int(getattr(u, "input_tokens", 0) or 0),
            output_tokens=int(getattr(u, "output_tokens", 0) or 0),
            cache_read_tokens=int(getattr(u, "cache_read_input_tokens", 0) or 0),
            cache_creation_tokens=int(getattr(u, "cache_creation_input_tokens", 0) or 0),
        )


def cost_usd(model_id: str, usage: Usage, batch: bool = False) -> Decimal:
    """USD cost of one call. Raises UnknownModelPricing for an unpriced model
    (never silently 0). Batch applies a flat 50% discount to the whole call.
    Returned at full Decimal precision -- the caller / DB column defines scale."""
    try:
        p = PRICES[model_id]
    except KeyError as e:
        raise UnknownModelPricing(model_id) from e
    input_rate, output_rate = p.rates(
        usage.input_tokens + usage.cache_read_tokens + usage.cache_creation_tokens)
    input_units = (
        Decimal(usage.input_tokens)
        + Decimal(usage.cache_read_tokens) * p.cache_read_mult
        + Decimal(usage.cache_creation_tokens) * p.cache_write_mult
    )
    raw = (
        input_units * input_rate + Decimal(usage.output_tokens) * output_rate
    ) / _MILLION
    return raw * BATCH_DISCOUNT if batch else raw


@dataclass(frozen=True)
class CallCost:
    """One priced LLM call -- what the log line and the ledger both need. `cost`
    is derived (a property) so capture never fails on an unpriced model: tokens
    are recorded regardless; only accessing `.cost` surfaces UnknownModelPricing.

    `tier` is the read step's LABEL (`llm.READ_STEPS`: `doc-class`, `T1`,
    `T2-first`, ...), not the evidence tier: it is the ledger's key, and two
    calls on one document at one revision must never share it."""

    tier: str
    model_id: str
    usage: Usage
    batch: bool = False

    @property
    def cost(self) -> Decimal:
        return cost_usd(self.model_id, self.usage, self.batch)
