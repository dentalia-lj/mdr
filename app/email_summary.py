"""Inbound email-body summarisation (S2.4 EMAIL, `email.poll`).

Why this exists: `/emails` could say which document arrived and in what state,
but not why the message was sent. The client's own words -- "I just found a DOC
for Nexco that expired in 04-05-2026" -- are the context that makes the page
actionable, and they live only in the body.

Why it is a summary and not the body: this registry keeps documents for ten
years. Supplier correspondence is not a document, is not evidence, and has no
business being retained verbatim. So the body is read into memory at poll time
(`EmailMessage.body_text`), compressed here, and dropped. The summary is what
persists (migration 030).

Invariant 12 was widened for exactly this on 2026-08-20 and ratified the same
day: cheap tier only, via `models.email_summary`. The result is UI CONTEXT ONLY
-- never evidence, never a production value, never a GATE or VALIDATE input, and
never re-read by another stage.

Runs at most once per message, by construction: a message is summarised on the
single poll that records it in `email_poll_log`, and the UID guard means it is
never polled again. A FAILED call still records its row, so a failure does not
retry either -- a missing line of UI context is not worth a second call, and a
poll must never dead-letter over one.

The prompt is a module constant rather than a file under `extract/prompts/`:
those are the tier prompts, loaded by `_load_prompt` with the REF-list cap
substituted in, and this is not a tier. One short prompt does not earn its own
package.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from app.extract import economics
from app.extract.economics import CallCost, Usage

__all__ = ["EmailSummary", "INTENTS", "summarise_email", "make_summary_client"]

_log = logging.getLogger(__name__)

#: The closed vocabulary the UI renders as a chip. Enforced by the API through
#: the response schema, not requested in prose. Migration 030 deliberately puts
#: no CHECK on the column: a model that returns something unexpected must be
#: VISIBLE in the UI, not a dead poll.
INTENTS = ("sends-documents", "requests-info", "acknowledges", "other")

#: Defensive cap on what we store. The prompt asks for two sentences and
#: `summary_max_tokens` bounds the generation, but the column is display text
#: and a runaway answer should be truncated rather than rendered whole.
SUMMARY_CHAR_CAP = 400

SYSTEM = """You summarise emails received at a medical-device company's regulatory \
compliance mailbox. Suppliers and manufacturers write to it about Declarations of \
Conformity, EC certificates, Instructions for Use and UDI codes.

Write at most two sentences saying what the sender wants or is providing, and name \
any product, article number, certificate or date they mention. Be specific: "sends \
the MDR DoC for Nexco, expiring 04-05-2026" is useful, "sends a document" is not.

Rules:
- Use only what the email says. Never infer, guess or add regulatory judgement.
- Do not restate boilerplate, signatures, disclaimers or quoted history.
- Write in English even when the email is not.
- If the message says nothing beyond a greeting, say so plainly.

Also classify the sender's intent:
- sends-documents: attaching or linking compliance documents
- requests-info: asking us for something
- acknowledges: confirming receipt, thanking, out-of-office, scheduling
- other: anything else, including messages with no discernible purpose"""

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "intent"],
    "properties": {
        # A plain string enum, NOT a union type with an enum: the API rejects
        # {"type": ["string","null"], "enum": [...]} with a 400 before any
        # tokens are billed (llm.py's `_SCOPE` comment, 2026-08-13).
        "summary": {"type": "string"},
        "intent": {"type": "string", "enum": list(INTENTS)},
    },
}


@dataclass(frozen=True)
class EmailSummary:
    """One summarisation outcome -- what migration 030's columns hold.

    `status` is never empty, so a row without a summary always carries its
    reason (never-silent): `ok` | `disabled` | `no-body` | `too-short` |
    `llm-error`. `cost_usd` is None for an unpriced model, with the token
    counts kept regardless -- the same rule `extraction_cost` follows.
    """

    status: str
    summary: str | None = None
    intent: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: Decimal | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def make_summary_client():
    """The live client, built the same way `extract.doc` builds its own: the key
    passed explicitly from config rather than left to the SDK's env lookup, so a
    TOML-only key is not silently ignored. Constructing needs no key; only the
    call does, and the worker already refuses to start without one."""
    import anthropic

    from app.config import load_config

    return anthropic.Anthropic(api_key=load_config().connection.anthropic_api_key)


def _response_json(resp) -> dict:
    import json

    for block in getattr(resp, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except ValueError:
                # A response that hit max_tokens is truncated mid-object.
                # Degrade to no summary rather than crash the poll.
                return {}
    return {}


def summarise_email(body_text: str, subject: str, *, cfg, client=None) -> EmailSummary:
    """Compress one inbound body. Never raises.

    Every early return is a recorded status, not a silent skip. The input is
    truncated from the FRONT: in a reply the new content is at the top and the
    tail is quoted history, so the first `summary_max_chars` is the half worth
    paying for.

    `client` is built here, and deliberately only AFTER every guard has passed,
    so a disabled / bodyless / too-short message can never construct one. Tests
    inject a stub; anything that reaches the call without one is asking for a
    live call, which is what `client=None` means.
    """
    if not cfg.email.summary_enabled:
        return EmailSummary("disabled")

    body = (body_text or "").strip()
    if not body:
        return EmailSummary("no-body")
    if len(body) < cfg.email.summary_min_chars:
        # Nothing to compress. "Thanks!" is already its own summary, and a call
        # here would cost more than the line of UI it produces.
        return EmailSummary("too-short")

    model = cfg.models.email_summary
    prompt = f"Subject: {subject or '(none)'}\n\n{body[:cfg.email.summary_max_chars]}"
    if client is None:
        client = make_summary_client()

    try:
        resp = client.messages.create(
            model=model,
            max_tokens=cfg.email.summary_max_tokens,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
        )
    except Exception as exc:  # noqa: BLE001 -- a poll must not die over UI context
        _log.warning("email.summary failed model=%s: %s", model, exc)
        return EmailSummary("llm-error", model=model)

    data = _response_json(resp)
    summary = (data.get("summary") or "").strip()[:SUMMARY_CHAR_CAP]
    intent = data.get("intent")
    if intent not in INTENTS:
        # Kept, not dropped: an off-vocabulary value is rendered as-is so the
        # drift is visible on the page rather than quietly normalised away.
        intent = intent or None

    usage = Usage.from_message(resp)
    call = CallCost("SUM", model, usage, False)
    try:
        cost = call.cost
    except economics.UnknownModelPricing:
        _log.warning("email.summary model=%s is UNPRICED in=%d out=%d",
                     model, usage.input_tokens, usage.output_tokens)
        cost = None

    if not summary:
        # A well-formed call that produced nothing usable is still a failure,
        # and must not read as an email with nothing to say.
        return EmailSummary("llm-error", model=model, input_tokens=usage.input_tokens,
                            output_tokens=usage.output_tokens, cost_usd=cost)

    _log.info("email.summary model=%s in=%d out=%d intent=%s",
              model, usage.input_tokens, usage.output_tokens, intent)
    return EmailSummary("ok", summary=summary, intent=intent, model=model,
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens, cost_usd=cost)
