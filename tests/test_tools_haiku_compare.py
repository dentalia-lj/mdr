"""tools.haiku_compare: the eval response cache stays addressable after the
pipeline started marking its system prompt for prompt caching (extraction-ladder
Task 6, 2026-10-12).

Replay rebuilds each request through the pipeline's own code and looks the
response up by `request_key`. Every response in the cache was keyed on the
request as it was before Task 6: a plain string system prompt, or two unmarked
blocks when hints were sent. If the marker reached the hash, every lookup would
miss and replay would raise KeyError.
"""

from __future__ import annotations

import pytest

from app.extract import llm, pdf
from tools import haiku_compare as hc

TEXT_PDF = "IVOCLAR/Ivoclar ISO certifikat do 30_10_2027.pdf"


class Models:
    t1 = "claude-haiku-4-5"
    t2 = "claude-sonnet-5-5"


def _request(fixture_pdf, label, hints=None):
    doc = pdf.open_doc(fixture_pdf(TEXT_PDF))
    return llm._params(label, Models(), doc, ["validity_from"], 4096, hints=hints)


@pytest.mark.parametrize("label, prompt", [("T1", llm.T1_SYSTEM), ("T2", llm.T2_SYSTEM)])
def test_a_marked_request_keys_as_the_plain_string_request_did(fixture_pdf, label, prompt):
    params = _request(fixture_pdf, label)
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}

    before_task_6 = {**params, "system": prompt}
    assert hc.request_key(params) == hc.request_key(before_task_6)


def test_a_hinted_request_keys_as_its_two_unmarked_blocks_did(fixture_pdf):
    params = _request(fixture_pdf, "T1", hints="A hint.")

    before_task_6 = {**params, "system": [{"type": "text", "text": llm.T1_SYSTEM},
                                          {"type": "text", "text": "A hint."}]}
    assert hc.request_key(params) == hc.request_key(before_task_6)


def test_the_prompt_text_still_reaches_the_key(fixture_pdf):
    """Only the marker is dropped: a changed prompt (a PROMPT_VARIANTS arm) must
    still key differently, or two arms would share one cached response."""
    params = _request(fixture_pdf, "T1")
    edited = {**params, "system": [{**params["system"][0], "text": llm.T1_SYSTEM + " "}]}

    assert hc.request_key(edited) != hc.request_key(params)


def test_keying_never_changes_the_request_it_reads(fixture_pdf):
    params = _request(fixture_pdf, "T1")
    hc.request_key(params)

    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
