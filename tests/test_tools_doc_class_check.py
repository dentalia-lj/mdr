"""tools.doc_class_check: the doc-class measurement of 2026-10-13.

What is pinned is what makes its numbers mean what the docs say they mean:
the prompt the pipeline ships is the candidate that was measured (`a-ifu`),
the "as built" arms put the earlier prompt back byte for byte (so their cached
answers are the ones measured), arm `a` is the pipeline's own request, and the
route a scanned file record takes before and after the scanned-page rule.
"""

from __future__ import annotations

import pymupdf

from app.config import Models
from app.extract import llm
from tools import doc_class_check as dc


def _doc() -> pymupdf.Document:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((40, 60), "EU Declaration of Conformity " * 8, fontsize=9)
    return doc


def test_the_shipped_doc_class_prompt_is_the_measured_candidate():
    assert dc.system_for(dc.ARMS["a-ifu"], llm.DOC_CLASS_SYSTEM) == llm.DOC_CLASS_SYSTEM
    old = dc.system_for(dc.ARMS["a"], llm.DOC_CLASS_SYSTEM)
    assert "operation manual or user guide" in old
    assert dc.system_for(dc.ARMS["a-ifu"], old) == llm.DOC_CLASS_SYSTEM


def test_arm_a_is_the_pipelines_request_on_the_earlier_prompt():
    sent = {}

    class Client:
        messages = None

        def __init__(self):
            self.messages = self

        def create(self, **params):
            sent.update(params)
            raise RuntimeError("captured")

    doc = _doc()
    try:
        llm.AnthropicLlm(Client(), Models()).classify(doc, "/archive/X/doc.pdf")
    except RuntimeError:
        pass
    a = dc.build_params(dc.ARMS["a-ifu"], doc, "/archive/X/doc.pdf")
    assert a == sent
    pre = dc.build_params(dc.ARMS["a"], doc, "/archive/X/doc.pdf")
    assert {k: v for k, v in pre.items() if k != "system"} == {
        k: v for k, v in sent.items() if k != "system"}


def test_arm_b_thinks_and_c_is_haiku_4_5_without_a_thinking_key():
    doc = _doc()
    b = dc.build_params(dc.ARMS["b"], doc, "f.pdf")
    c = dc.build_params(dc.ARMS["c"], doc, "f.pdf")
    assert (b["thinking"], b["max_tokens"]) == ({"type": "adaptive"}, 8192)
    assert c["model"] == "claude-haiku-4-5" and "thinking" not in c


def test_routes_before_and_after_the_scanned_page_rule():
    rec = {"t0_class": "unknown", "has_text_page": True, "dc_text_empty": False,
           "classes12": ["image", "text"]}
    assert (dc.old_route(rec), dc.route(rec)) == ("text", "image")
    assert dc.route({**rec, "classes12": ["text", "text"]}) == "text"
    assert dc.route({**rec, "has_text_page": False}) == "scan"
    assert dc.route({**rec, "t0_class": "compliance-doc"}) == "t0:compliance-doc"


def test_a_hand_label_wins_and_a_typed_production_document_is_positive():
    d = {"content_hash": "ab" * 32, "source": "prod", "registry": {"type": "IFU"}}
    assert dc.label_of(d, {}) == "pos"
    assert dc.label_of(d, {"ab" * 6: {"label": "neg"}}) == "neg"
    assert dc.label_of({**d, "registry": {"type": "other"}}, {}) is None
