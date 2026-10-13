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



# --- ruling A is the pipeline prompt now (extraction-ladder Task 4) -------------

def test_the_ruling_a_variant_is_a_no_op_on_the_current_prompt():
    """The pipeline prompt carries the ruling-A line since Task 4, so the h55a
    arm sends the default prompt, the system text its responses were cached
    under."""
    with hc._prompt_variant("ruling-a"):
        assert llm.T1_SYSTEM == hc.llm_mod.T1_SYSTEM
        varied = hc.llm_mod.T1_SYSTEM
    assert varied == llm.T1_SYSTEM


def test_a_variant_that_matches_nothing_still_fails(monkeypatch):
    monkeypatch.setitem(hc.PROMPT_VARIANTS, "nowhere", [("no such line", "other line")])
    with pytest.raises(AssertionError, match="exactly once"):
        with hc._prompt_variant("nowhere"):
            pass


# --- the baseline arms keep the prompt they were measured with (review r2) ------

def test_the_baseline_arms_send_the_pre_ruling_a_prompt():
    """h45, h55, h55n and h55hi were cached with the T1 prompt before ruling A;
    the pipeline prompt carries the ruling now, so they put the old line back
    (a reverse variant) and their requests still key to their responses."""
    for arm_id in ("h45", "h55", "h55n", "h55hi"):
        assert hc.ARMS[arm_id].prompt == "pre-ruling-a"
    (old, new), = hc.PROMPT_VARIANTS["ruling-a"]
    with hc._prompt_variant("pre-ruling-a"):
        assert old in hc.llm_mod.T1_SYSTEM and new not in hc.llm_mod.T1_SYSTEM
    assert new in llm.T1_SYSTEM                     # restored afterwards


@pytest.mark.parametrize("arm_id, thinking", [
    ("h45", None), ("h55", None), ("h55n", {"type": "disabled"}), ("h55hi", None)])
def test_a_doc_class_request_takes_its_thinking_from_the_arm(fixture_pdf, arm_id, thinking):
    """The pipeline's doc-class call sends thinking off since Task 4
    (`READ_STEPS`); an arm's cached classify request carries the arm's own
    setting, none for an arm that names none."""
    arm = hc.ARMS[arm_id]
    doc = pdf.open_doc(fixture_pdf(TEXT_PDF))
    params = hc._capture(lambda: hc._llm(arm_id, "capture").classify(doc, "f.pdf"))
    assert params.get("thinking") == thinking
    assert params["model"] == arm.model


def test_an_extract_request_keeps_the_pipelines_thinking_when_the_arm_names_none(fixture_pdf):
    doc = pdf.open_doc(fixture_pdf(TEXT_PDF))
    params = hc._capture(lambda: hc._llm("h55", "capture").extract("T1", doc, "f.pdf", ["type"]))
    assert "thinking" not in params
