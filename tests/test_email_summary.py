"""Inbound email-body summarisation (S2.4). What `/emails` gets to say about a
message, and -- more importantly -- what never reaches the database.

The contract these assert: the body is compressed and dropped, every non-call
is a recorded reason rather than a silent skip, and no failure mode reaches the
caller as an exception (a poll must not dead-letter over UI context).
"""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

from app.config import load_config
from app.email_summary import (
    INTENTS,
    SUMMARY_CHAR_CAP,
    EmailSummary,
    summarise_email,
)

BODY = (
    "Dear all,\n\n"
    "I just found a DOC for Nexco that expired in 04-05-2026. Please send the "
    "renewed declaration of conformity for the whole Nexco family as soon as "
    "you have it, together with the EC certificate.\n\n"
    "Best regards,\nNatasa"
)


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Resp:
    def __init__(self, text, *, in_tokens=900, out_tokens=40):
        self.content = [_Block(text)]
        self.usage = SimpleNamespace(input_tokens=in_tokens, output_tokens=out_tokens)


class StubClient:
    """Records the request so the tests can assert what was actually sent --
    the input cap and the prompt shape are part of the contract."""

    def __init__(self, payload=None, *, raise_exc=None, text=None, **usage):
        if text is None:
            text = json.dumps(payload if payload is not None else {
                "summary": "Asks for the renewed Nexco DoC and EC certificate.",
                "intent": "requests-info",
            })
        self._text = text
        self._raise = raise_exc
        self._usage = usage
        self.kwargs = None
        self.calls = 0

    def _create(self, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        if self._raise is not None:
            raise self._raise
        return _Resp(self._text, **self._usage)

    @property
    def messages(self):
        return SimpleNamespace(create=self._create)


def _cfg(**email_over):
    cfg = load_config()
    return replace(cfg, email=replace(cfg.email, **email_over)) if email_over else cfg


# --------------------------------------------------------------------------- #
# the happy path
# --------------------------------------------------------------------------- #
def test_a_body_becomes_a_summary_and_an_intent():
    client = StubClient()

    out = summarise_email(BODY, "MDR DOCUMENTS", cfg=_cfg(), client=client)

    assert out.ok and out.status == "ok"
    assert "Nexco" in out.summary
    assert out.intent == "requests-info"
    assert out.model == _cfg().models.email_summary


def test_the_call_carries_tokens_and_a_price():
    """Negligible is a measurement, not a belief -- migration 030 keeps the
    numbers on the row so the claim stays checkable."""
    client = StubClient(in_tokens=1200, out_tokens=48)

    out = summarise_email(BODY, "s", cfg=_cfg(), client=client)

    assert out.input_tokens == 1200
    assert out.output_tokens == 48
    assert out.cost_usd is not None and out.cost_usd < Decimal("0.01")


def test_the_subject_is_sent_with_the_body():
    """Half of these mails say everything in the subject line."""
    client = StubClient()

    summarise_email(BODY, "RE: MDR DOCUMENTS", cfg=_cfg(), client=client)

    sent = client.kwargs["messages"][0]["content"]
    assert "RE: MDR DOCUMENTS" in sent
    assert "Nexco" in sent


def test_the_response_vocabulary_is_closed_by_the_schema_not_the_prompt():
    client = StubClient()
    summarise_email(BODY, "s", cfg=_cfg(), client=client)

    schema = client.kwargs["output_config"]["format"]["schema"]
    assert schema["properties"]["intent"]["enum"] == list(INTENTS)
    # A plain string enum, never a union type: the union form is a 400 that
    # fires before any tokens are billed (llm.py, 2026-08-13).
    assert schema["properties"]["intent"]["type"] == "string"


def test_the_cheap_tier_is_used_and_it_is_its_own_config_key():
    cfg = _cfg()
    client = StubClient()

    summarise_email(BODY, "s", cfg=cfg, client=client)

    assert client.kwargs["model"] == cfg.models.email_summary
    assert client.kwargs["max_tokens"] == cfg.email.summary_max_tokens


# --------------------------------------------------------------------------- #
# every non-call is a recorded reason (never-silent)
# --------------------------------------------------------------------------- #
def test_the_flag_off_records_disabled_and_never_calls():
    client = StubClient()

    out = summarise_email(BODY, "s", cfg=_cfg(summary_enabled=False), client=client)

    assert out.status == "disabled" and out.summary is None
    assert client.calls == 0


def test_a_message_with_no_text_records_no_body():
    client = StubClient()

    out = summarise_email("   \n  ", "s", cfg=_cfg(), client=client)

    assert out.status == "no-body"
    assert client.calls == 0


def test_a_one_liner_is_summarised_like_any_other_body():
    """A body of any length gets a summary (Denis, 2026-08-21). The floor used
    to stop here on the theory that "Thanks, received." is already its own
    summary -- but the body is never stored, so skipping the call left the row
    with NO text at all, which is emptier than the long messages beside it. The
    intent classification is the part that earns the call regardless of length."""
    client = StubClient()

    out = summarise_email("Thanks, received.", "s", cfg=_cfg(), client=client)

    assert out.ok
    assert client.calls == 1


def test_a_short_but_real_covering_note_clears_the_floor():
    """The floor was 120 until the fixture mailbox was measured: every real body
    there ran 9-80 chars, so 120 summarised none of them. This sentence says
    something -- what arrived, and that it is a renewal -- and must be
    summarised, not dropped for being brief (Denis 2026-08-20: cost is not what
    this bound is for)."""
    client = StubClient()
    note = "Dear Natasa, please find the renewed declaration attached."

    assert len(note) < 120
    assert summarise_email(note, "s", cfg=_cfg(), client=client).ok


def test_the_floor_is_off_by_default_and_still_reachable_as_a_throttle():
    """`summary_min_chars` defaults to 0 -- every body is summarised. The key
    survives as a throttle for a mailbox noisy enough to want one, so
    `too-short` stays in the status vocabulary rather than being deleted."""
    client = StubClient()
    short = "Please send it."

    assert summarise_email(short, "s", cfg=_cfg(), client=client).ok
    assert (
        summarise_email(short, "s", cfg=_cfg(summary_min_chars=40), client=client).status
        == "too-short"
    )


# --------------------------------------------------------------------------- #
# failure never reaches the caller
# --------------------------------------------------------------------------- #
def test_an_api_failure_is_a_status_not_an_exception():
    client = StubClient(raise_exc=RuntimeError("overloaded_error"))

    out = summarise_email(BODY, "s", cfg=_cfg(), client=client)

    assert out.status == "llm-error"
    assert out.summary is None
    assert out.model is not None  # which model failed is still recorded


def test_a_truncated_response_degrades_instead_of_crashing():
    """max_tokens cuts the JSON mid-object. Nothing usable, but the poll lives."""
    client = StubClient(text='{"summary": "Asks for the ren')

    out = summarise_email(BODY, "s", cfg=_cfg(), client=client)

    assert out.status == "llm-error"
    assert out.summary is None


def test_a_well_formed_but_empty_summary_is_a_failure_not_silence():
    """An empty string would render as an email with nothing to say, which is a
    different fact from "we could not summarise it"."""
    client = StubClient(payload={"summary": "   ", "intent": "other"})

    out = summarise_email(BODY, "s", cfg=_cfg(), client=client)

    assert out.status == "llm-error"
    # The call happened, so its cost is still recorded.
    assert out.input_tokens == 900


def test_an_off_vocabulary_intent_is_kept_so_the_drift_is_visible():
    """Migration 030 puts no CHECK on the column for this reason: a model that
    invents a value must show up on the page, not dead-letter the poll."""
    client = StubClient(payload={"summary": "Sends the DoC.", "intent": "invoice"})

    out = summarise_email(BODY, "s", cfg=_cfg(), client=client)

    assert out.ok
    assert out.intent == "invoice"


# --------------------------------------------------------------------------- #
# bounds
# --------------------------------------------------------------------------- #
def test_the_input_is_capped_from_the_front_where_the_new_content_is():
    """In a reply the tail is quoted history. Paying to send it back is paying
    to summarise our own earlier mail."""
    body = "NEW CONTENT AT THE TOP. " + ("quoted history line. " * 2000)
    client = StubClient()

    summarise_email(body, "s", cfg=_cfg(summary_max_chars=300), client=client)

    sent = client.kwargs["messages"][0]["content"]
    assert "NEW CONTENT AT THE TOP" in sent
    assert len(sent) < 500


def test_a_runaway_summary_is_truncated_rather_than_stored_whole():
    client = StubClient(payload={"summary": "x" * 5000, "intent": "other"})

    out = summarise_email(BODY, "s", cfg=_cfg(), client=client)

    assert len(out.summary) == SUMMARY_CHAR_CAP


def test_the_body_is_never_part_of_the_result():
    """The whole point of the slice: what persists is the summary. If the body
    could ride along in the returned object it would reach the row."""
    client = StubClient()

    out = summarise_email(BODY, "s", cfg=_cfg(), client=client)

    assert "Best regards" not in json.dumps(
        {k: str(v) for k, v in out.__dict__.items()}
    )


def test_the_result_is_frozen():
    out = EmailSummary("ok", summary="s")
    try:
        out.summary = "changed"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("EmailSummary must be immutable")
