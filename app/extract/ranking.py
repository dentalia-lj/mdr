"""T1 candidate ranking for the DISCOVER search rung (followup `discover-t1-ranking`).

PRD §3, invariant 12: **the LLM ranks, it never decides.** This module turns a
list of `SearchCandidate` into `[(candidate, score)]` and stops there. The
fetch/no-fetch decision stays a threshold rule in `discover._threshold_topk`
(`score >= discovery.rank_threshold`, best `topk` first), so changing the model
can change the ordering but never the rule that spends money on a fetch.

Shape is fixed by `docs/specs/discover.md` §4:

    rank(facts, candidates) -> list[tuple[SearchCandidate, float]]

`AnthropicRanker` satisfies it as a callable, and `handle_discover_group(...,
rank=)` still takes any other callable — the injection seam the tests use is
unchanged.

**Failure is never fatal.** `_search` catches everything this raises, logs the
`discovery_log` search row as a `miss` with `detail.rank_error`, and lets the
ladder fall through to manual. That is the same degrade path the deferred
`NotImplementedError` had, so a bad API key or a truncated response costs a
group its search rung, never the group itself.

**Scores are clamped and index-matched, not zipped.** The model is asked for
`{index, score}` pairs rather than a bare list, because a model that returns 9
scores for 10 candidates would otherwise silently shift every score onto the
wrong URL — and a shifted 0.95 is a fetch of the wrong document, which is the
one failure mode here that costs real money. A candidate the model omits scores
0.0 and is logged; it is never dropped from the returned list, so the caller's
count always matches what it passed in.
"""

from __future__ import annotations

import json
import logging
import pathlib
from dataclasses import dataclass

from app.extract import economics
from app.extract.economics import CallCost, Usage

_log = logging.getLogger("dentalia.discover.rank")

_PROMPTS = pathlib.Path(__file__).resolve().parent / "prompts"

RANK_SYSTEM = (_PROMPTS / "rank_candidates.md").read_text(encoding="utf-8")

DEFAULT_MAX_TOKENS = 2048

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["scores"],
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["index", "score", "reason"],
                "properties": {
                    "index": {"type": "integer"},
                    "score": {"type": "number"},
                    "reason": {"type": "string"},
                },
            },
        }
    },
}


def _candidate_block(facts, candidates) -> str:
    """The user turn. Numbered so the model answers by index, and the numbering
    is the array position -- `_scores_by_index` trusts nothing else."""
    lines = [f"Manufacturer: {facts.manufacturer}"]
    if facts.label:
        lines.append(f"Our catalogue label (internal, Slovene): {facts.label}")
    if facts.member_mfr_refs:
        # A DoC's REF list is the strongest confirmation there is, and the
        # snippet sometimes carries one. Capped: a group can hold thousands of
        # members (76152082APROS004VZ holds 7.586) and the whole point of the
        # cheap tier is that this prompt stays small.
        refs = ", ".join(facts.member_mfr_refs[:20])
        lines.append(f"Article numbers we expect it to cover: {refs}")
    lines.append("")
    lines.append("Candidates:")
    for i, c in enumerate(candidates):
        lines.append(f"[{i}] {c.url}")
        if c.title:
            lines.append(f"    title: {c.title}")
        if c.snippet:
            lines.append(f"    snippet: {c.snippet}")
    return "\n".join(lines)


def _params(model: str, facts, candidates, max_tokens: int) -> dict:
    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": RANK_SYSTEM,
        "messages": [
            {"role": "user", "content": _candidate_block(facts, candidates)}
        ],
        "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}},
    }


def _response_json(resp) -> dict:
    """Same posture as `llm._response_json`: a response truncated at max_tokens
    is not valid JSON, and that must degrade rather than crash. Here it degrades
    to no scores, which the caller turns into all-zeros -> nothing clears the
    threshold -> search miss -> manual."""
    text = next(
        (b.text for b in resp.content if getattr(b, "type", None) == "text"), ""
    )
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        _log.warning(
            "discover rank: unparseable structured output (%d chars, likely "
            "max_tokens truncation): %s", len(text), exc
        )
        return {}


def _scores_by_index(payload: dict, n: int) -> list[float]:
    """`{"scores": [{index, score}]}` -> a dense list of n floats.

    Defensive on purpose (see the module docstring): out-of-range and duplicate
    indices are dropped rather than trusted, scores are clamped to [0, 1], and a
    non-numeric score is treated as absent. Anything the model did not answer
    for stays 0.0."""
    out = [0.0] * n
    seen: set[int] = set()
    for row in payload.get("scores") or []:
        if not isinstance(row, dict):
            continue
        idx = row.get("index")
        if not isinstance(idx, int) or not (0 <= idx < n) or idx in seen:
            _log.warning("discover rank: ignoring score for bad index %r", idx)
            continue
        try:
            score = float(row.get("score"))
        except (TypeError, ValueError):
            _log.warning("discover rank: non-numeric score for index %d", idx)
            continue
        seen.add(idx)
        out[idx] = min(1.0, max(0.0, score))
    missing = n - len(seen)
    if missing:
        _log.warning(
            "discover rank: model scored %d of %d candidates; the rest keep 0.0",
            len(seen), n
        )
    return out


class AnthropicRanker:
    """Live T1 ranker. `client` is an `anthropic.Anthropic` (or a stub in tests);
    `models` exposes `.rank`.

    `last_cost` holds the priced usage of the most recent call, for the caller
    that wants to log it. It is deliberately not threaded through the `rank()`
    return value -- that shape is frozen by the spec and by every injected stub
    in the tests."""

    def __init__(self, client, models, max_tokens: int = DEFAULT_MAX_TOKENS):
        self.client = client
        self.models = models
        self.max_tokens = max_tokens
        self.last_cost: CallCost | None = None

    def __call__(self, facts, candidates) -> list[tuple[object, float]]:
        if not candidates:
            return []
        model = self.models.rank
        resp = self.client.messages.create(
            **_params(model, facts, candidates, self.max_tokens)
        )
        self.last_cost = CallCost(
            tier="rank", model_id=model, usage=Usage.from_message(resp), batch=False
        )
        scores = _scores_by_index(_response_json(resp), len(candidates))
        _log.info(
            "discover rank: group %s, %d candidates, max score %.2f, cost %s",
            facts.group_id, len(candidates), max(scores, default=0.0),
            _fmt_cost(self.last_cost),
        )
        return list(zip(candidates, scores))


def _fmt_cost(cc: CallCost) -> str:
    """Never let an unpriced model turn a successful rank into an exception --
    `cost_usd` raises `UnknownModelPricing` by design, and this is a log line."""
    try:
        return f"${cc.cost:.6f}"
    except economics.UnknownModelPricing:
        return f"unpriced({cc.model_id})"


# --- crawl link triage (ruled 2026-09-04, `[crawl-ai-link-triage]`) ----------
#
# A different question from the search ranker's. Every link on a library page
# is already on the manufacturer's own site, so "is this an official document
# for them" is answered; what is not known is WHAT KIND of document a link is
# and WHICH ARTICLES it names. The model answers exactly those two things per
# link; the ordering rule that spends the fetch budget on the answer lives in
# `discover._rank_links` and never in the prompt -- the LLM judges, it never
# decides (invariant 12). A predicted type steers fetch order only. The
# document's real type comes from its own content at T0/T1 (invariant 2).

CRAWL_SYSTEM = (_PROMPTS / "rank_crawl_links.md").read_text(encoding="utf-8")

LINK_TYPES = ("DoC", "EC", "ISO", "IFU", "other")

#: `reason` on a link the model did not answer for, so a caller can tell it
#: apart from a genuine judgement of `other`.
_UNJUDGED = "unjudged"

# Measured 2026-09-07 against nsk-library.com: a judgement runs ~55 output
# tokens once the URL, the article numbers and the reason are counted, so a
# batch of 150 needs ~8.2k and truncated at exactly this ceiling -- the JSON
# came back unterminated, `_response_json` returned {}, and 150 links silently
# became `other`. Batches are 50 now and the ceiling is 16k, which is three
# times the worst case; `JudgementTruncated` covers the rest.
CRAWL_MAX_TOKENS = 16384
CRAWL_BATCH = 50

CRAWL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["judgements"],
    "properties": {
        "judgements": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["index", "type", "article_numbers", "confidence", "reason"],
                "properties": {
                    "index": {"type": "integer"},
                    "type": {"type": "string", "enum": list(LINK_TYPES)},
                    "article_numbers": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                },
            },
        }
    },
}


@dataclass(frozen=True)
class LinkJudgement:
    url: str
    type: str                          # one of LINK_TYPES; a fetch-order hint only
    article_numbers: tuple[str, ...]   # verbatim, as the link text or file name shows them
    confidence: float                  # clamped to [0, 1]
    reason: str = ""


def _link_block(manufacturer: str, candidates) -> str:
    lines = [f"Manufacturer: {manufacturer}", "", "Links:"]
    for i, c in enumerate(candidates):
        lines.append(f"[{i}] {c.url}")
        if c.title:
            lines.append(f"    text: {c.title}")
    return "\n".join(lines)


def _judgements_by_index(payload: dict, candidates) -> list[LinkJudgement]:
    """Index-matched and defensive, for the same reason `_scores_by_index` is:
    a judgement that lands on the wrong link fetches the wrong document. A link
    the model did not answer for is `other` at 0.0 -- never dropped, so the
    caller's count always matches what it passed in."""
    n = len(candidates)
    out: list[LinkJudgement | None] = [None] * n
    for row in payload.get("judgements") or []:
        if not isinstance(row, dict):
            continue
        idx = row.get("index")
        if not isinstance(idx, int) or not (0 <= idx < n) or out[idx] is not None:
            _log.warning("crawl rank: ignoring judgement for bad index %r", idx)
            continue
        kind = row.get("type")
        if kind not in LINK_TYPES:
            kind = "other"
        try:
            conf = min(1.0, max(0.0, float(row.get("confidence"))))
        except (TypeError, ValueError):
            conf = 0.0
        refs = tuple(str(a).strip() for a in (row.get("article_numbers") or [])
                     if isinstance(a, (str, int)) and str(a).strip())
        out[idx] = LinkJudgement(candidates[idx].url, kind, refs, conf,
                                 str(row.get("reason") or "")[:120])
    missing = sum(1 for j in out if j is None)
    if missing:
        _log.warning("crawl rank: model judged %d of %d links; the rest are `other`",
                     n - missing, n)
    return [j if j is not None else LinkJudgement(c.url, "other", (), 0.0, _UNJUDGED)
            for j, c in zip(out, candidates)]


class JudgementTruncated(RuntimeError):
    """The model returned nothing usable for a non-empty batch.

    Raised rather than absorbed, because the `other` fallback in
    `_judgements_by_index` is indistinguishable from a real judgement of
    `other` once it reaches the caller -- and `other` sorts last, so a
    truncated batch would push whatever it held (declarations included) below
    every judged IFU. `discover._rank_links` catches this and degrades the
    whole crawl to page order, which is at least a behaviour somebody can
    reason about."""


class CrawlLinkRanker:
    """Live T1 link triage. Same client and `models.rank` as `AnthropicRanker`;
    a different prompt, a different answer shape."""

    def __init__(self, client, models, max_tokens: int = CRAWL_MAX_TOKENS):
        self.client = client
        self.models = models
        self.max_tokens = max_tokens
        self.last_cost: CallCost | None = None
        self.model_id = models.rank     # recorded on the crawl_link_rank row

    def __call__(self, manufacturer: str, candidates) -> list[LinkJudgement]:
        if not candidates:
            return []
        model = self.models.rank
        resp = self.client.messages.create(
            model=model,
            max_tokens=self.max_tokens,
            system=CRAWL_SYSTEM,
            messages=[{"role": "user", "content": _link_block(manufacturer, candidates)}],
            output_config={"format": {"type": "json_schema", "schema": CRAWL_SCHEMA}},
        )
        self.last_cost = CallCost(
            tier="rank", model_id=model, usage=Usage.from_message(resp), batch=False
        )
        out = _judgements_by_index(_response_json(resp), candidates)
        judged = sum(1 for j in out if j.reason != _UNJUDGED)
        if not judged:
            raise JudgementTruncated(
                f"crawl rank: {len(candidates)} links, none judged "
                f"(max_tokens={self.max_tokens}, likely truncation)")
        _log.info("crawl rank: %s, %d links judged, cost %s",
                  manufacturer, len(out), _fmt_cost(self.last_cost))
        return out
