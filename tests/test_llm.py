"""T1/T2 Anthropic tiers with an injected stub client (no live API calls).

Verifies request routing (model, text vs image content) and that the structured
response is parsed into per-field evidence, filtered to the requested fields.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

from app.extract import llm, pdf

# Anthropic Batch API custom_id constraint (enforced server-side with a 400).
CUSTOM_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

TEXT_PDF = "IVOCLAR/Ivoclar ISO certifikat do 30_10_2027.pdf"
SCAN_PDF = "IVOCLAR/MDR Certificate IV AG 2017_745.pdf"


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Resp:
    def __init__(self, text):
        self.content = [_Block(text)]


class StubMessages:
    def __init__(self, text):
        self._text = text
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return _Resp(self._text)


class StubClient:
    def __init__(self, text):
        self.messages = StubMessages(text)


class Models:
    t1 = "claude-haiku-4-5"
    t2 = "claude-sonnet-4-6"
    rank = "claude-haiku-4-5"


def test_t1_parses_and_filters_to_requested_fields(fixture_pdf):
    payload = (
        '{"regulation": {"value": "MDR", "confidence": 0.96, '
        '"verbatim": "(EU) 2017/745", "page": 1}, '
        '"validity_from": {"value": "2024-03-15", "confidence": 0.9, '
        '"verbatim": "15 March 2024", "page": 2}, '
        '"type": {"value": "DoC", "confidence": 0.99, "verbatim": "x", "page": 1}}'
    )
    client = StubClient(payload)
    tier = llm.AnthropicLlm(client, Models())
    doc = pdf.open_doc(fixture_pdf(TEXT_PDF))

    got = tier.extract("T1", doc, "f.pdf", missing=["regulation", "validity_from"])

    assert set(got) == {"regulation", "validity_from"}  # 'type' filtered out
    ev = got["regulation"]
    assert ev["value"] == "MDR"
    assert ev["conf"] == 0.96
    assert ev["tier"] == "T1"
    assert ev["model_id"] == "claude-haiku-4-5"
    assert ev["verbatim"] == "(EU) 2017/745"
    assert ev["page"] == 1
    assert client.messages.kwargs["model"] == "claude-haiku-4-5"


def test_t1_truncates_an_oversized_document_and_says_so_in_the_log(caplog):
    # The regression this exists for: `bounded_text` was correct and its caller
    # logged the drop through a name that did not exist (`log` for `_log`), so
    # the ONE path that only an oversized document reaches raised
    # `NameError: name 'log' is not defined` -- and killed both STRAUMANN IFU
    # jobs (2026-08-17) after 1337 tests passed. The pdf-level tests exercise
    # the cap directly and never touch this branch; this one goes through T1.
    import pymupdf
    doc = pymupdf.open()
    # A real oversized document rather than a patched budget, so the assertion
    # covers the production path end to end. `insert_text` neither wraps nor
    # renders past the page bottom, so a page yields ~6.100 extractable
    # characters here however many lines are handed to it -- measured, not
    # assumed. 62 pages puts the document over the 360k budget with margin.
    per_page = "\n".join(["y" * 60] * 200)
    for _ in range(62):
        doc.new_page().insert_text((20, 20), per_page, fontsize=6)
    assert len(pdf.full_text(doc)) > pdf.LLM_TEXT_BUDGET_CHARS

    client = StubClient('{"type": {"value": "IFU", "confidence": 0.9, '
                        '"verbatim": "x", "page": 1}}')
    tier = llm.AnthropicLlm(client, Models())

    with caplog.at_level("WARNING"):
        got = tier.extract("T1", doc, "big.pdf", missing=["type"])

    assert got["type"]["value"] == "IFU"             # the call completed
    sent = client.messages.kwargs["messages"][0]["content"][0]["text"]
    assert len(sent) <= pdf.LLM_TEXT_BUDGET_CHARS + 200
    assert "truncated" in sent.lower()
    # getMessage(), not .message: the drop count is a lazy %-arg, and the
    # NameError this test exists for lived on the formatting line itself.
    assert any("extract.text_truncated" in r.getMessage() for r in caplog.records)
    assert any("dropped_chars=" in r.getMessage() for r in caplog.records)
    doc.close()


def test_t2_uses_vision_model_with_image_content(fixture_pdf):
    payload = (
        '{"basic_udi_di": {"value": "7612147X", "confidence": 0.88, '
        '"verbatim": "Basic UDI-DI: 7612147X", "page": 1}}'
    )
    client = StubClient(payload)
    tier = llm.AnthropicLlm(client, Models())
    doc = pdf.open_doc(fixture_pdf(SCAN_PDF))

    got = tier.extract("T2", doc, "f.pdf", missing=["basic_udi_di"])

    assert got["basic_udi_di"]["tier"] == "T2"
    assert client.messages.kwargs["model"] == "claude-sonnet-4-6"
    content = client.messages.kwargs["messages"][0]["content"]
    assert any(b.get("type") == "image" for b in content)


def test_missing_field_absent_from_response_is_skipped(fixture_pdf):
    client = StubClient(
        '{"regulation": {"value": "MDR", "confidence": 0.9, "verbatim": "x", "page": 1}}'
    )
    tier = llm.AnthropicLlm(client, Models())
    doc = pdf.open_doc(fixture_pdf(TEXT_PDF))

    got = tier.extract("T1", doc, "f.pdf", missing=["regulation", "cert_number"])

    assert "regulation" in got
    assert "cert_number" not in got  # model returned nothing for it


# --- Batch API client (C9) — same request/parse machinery as the sync tier, but
# submitted via messages.batches. Injected fake SDK, no live calls. ------------

class _BatchResultItem:
    def __init__(self, custom_id, text, kind="succeeded"):
        self.custom_id = custom_id
        self.result = SimpleNamespace(type=kind, message=_Resp(text))


class StubBatches:
    """Fake anthropic `messages.batches` resource."""

    def __init__(self, text, status="ended"):
        self._text = text
        self._status = status
        self.created_requests = None

    def create(self, *, requests):
        self.created_requests = list(requests)
        return SimpleNamespace(id="batch_123")

    def retrieve(self, batch_id):
        return SimpleNamespace(processing_status=self._status)

    def results(self, batch_id):
        # one succeeded item; custom_id carries the tier (batch-of-1 per hash+tier)
        return [_BatchResultItem("T1_abc123", self._text)]


class StubBatchSdk:
    def __init__(self, text, status="ended"):
        self.messages = SimpleNamespace(batches=StubBatches(text, status))


def test_batch_client_submit_builds_request_and_returns_id(fixture_pdf):
    sdk = StubBatchSdk("{}")
    bc = llm.AnthropicBatchClient(sdk, Models())
    doc = pdf.open_doc(fixture_pdf(TEXT_PDF))

    requests = bc.build_requests("abc123", "T1", doc, missing=["type", "regulation"])
    batch_id = bc.submit(requests)

    assert batch_id == "batch_123"
    req = sdk.messages.batches.created_requests[0]
    assert req["custom_id"] == "T1_abc123"
    assert CUSTOM_ID_RE.match(req["custom_id"])
    assert req["params"]["model"] == "claude-haiku-4-5"
    # same structured-output contract as the sync tier
    assert req["params"]["output_config"]["format"]["type"] == "json_schema"


def test_batch_custom_id_obeys_api_pattern_even_for_a_full_sha256(fixture_pdf):
    # The Batch API rejects custom_ids outside ^[a-zA-Z0-9_-]{1,64}$ with a 400,
    # so a full 64-hex content_hash plus a tier tag must still fit and stay legal.
    sdk = StubBatchSdk("{}")
    bc = llm.AnthropicBatchClient(sdk, Models())
    doc = pdf.open_doc(fixture_pdf(TEXT_PDF))
    full_hash = "a" * 64

    req = bc.build_requests(full_hash, "T1", doc, missing=["type"])[0]

    assert CUSTOM_ID_RE.match(req["custom_id"]), req["custom_id"]


def test_response_json_returns_empty_on_truncated_output():
    # A response that hit max_tokens is truncated mid-object -> invalid JSON.
    # Degrade to no fields (ladder escalates / keeps T0), never crash the whole
    # extraction (S0.4 corpus run: 2/22 docs died on exactly this).
    truncated = '{"type": {"value": "DoC", "confidence": 0.9, "verbatim": "Declar'
    assert llm._response_json(_Resp(truncated)) == {}


def test_nullish_value_strings_normalize_to_none():
    # The model emits a sentinel string ('n.a.', 'none', ...) for an absent field
    # instead of null; treat those as null so validate/gate see a real absence
    # (S0.4: regulation='n.a.' was 6/20 false-positives).
    # `cert_number`, not `regulation` — see the two tests below: regulation is
    # the one field where 'n.a.' is a REAL value (Denis ruling 2026-08-11).
    for sentinel in ("n.a.", "N/A", "none", "NA", ""):
        data = {"cert_number": {"value": sentinel, "confidence": 0.9,
                                "verbatim": sentinel, "page": 1}}
        out = llm._fields_from_json(data, "T1", "m")
        assert out["cert_number"]["value"] is None, sentinel
    # a real value passes through untouched
    keep = {"cert_number": {"value": "MDR 778483", "confidence": 0.9, "verbatim": "x", "page": 1}}
    assert llm._fields_from_json(keep, "T1", "m")["cert_number"]["value"] == "MDR 778483"


def test_na_is_a_real_value_for_regulation():
    """An ISO/QMS certificate has no MDR/MDD regulation. `005_registry.sql`
    reserves the value (regulation is MDR|MDD|n.a.), and coercing it to null
    made VALIDATE reject the document as incomplete-evidence twelve lines
    before the C4 manufacturer-binding rule written for exactly this document
    type -- 3 of 61 corpus documents (Denis ruling 2026-08-11)."""
    data = {"regulation": {"value": "n.a.", "confidence": 0.95,
                           "verbatim": "ISO 13485:2016", "page": 1}}
    out = llm._fields_from_json(data, "T1", "m")
    assert out["regulation"]["value"] == "n.a."


def test_regulation_keeps_a_real_regulation_untouched():
    for value in ("MDR", "MDD"):
        data = {"regulation": {"value": value, "confidence": 0.9, "verbatim": "x", "page": 1}}
        assert llm._fields_from_json(data, "T1", "m")["regulation"]["value"] == value


def test_batch_client_done_reflects_processing_status():
    assert llm.AnthropicBatchClient(StubBatchSdk("{}", "ended"), Models()).done("b") is True
    assert llm.AnthropicBatchClient(StubBatchSdk("{}", "in_progress"), Models()).done("b") is False


def test_batch_client_results_parse_to_evidence():
    payload = (
        '{"regulation": {"value": "MDR", "confidence": 0.96, '
        '"verbatim": "(EU) 2017/745", "page": 1}}'
    )
    bc = llm.AnthropicBatchClient(StubBatchSdk(payload), Models())

    got = bc.results("batch_123")

    ev = got["regulation"]
    assert ev["value"] == "MDR"
    assert ev["conf"] == 0.96
    assert ev["tier"] == "T1"          # derived from the custom_id
    assert ev["model_id"] == "claude-haiku-4-5"
    assert ev["page"] == 1


# --- optional hint block (Task 6): plumbing only, no playbook key yet. ---------

def test_hints_ride_a_second_system_block_leaving_the_prompt_file_untouched(fixture_pdf):
    """T1_SYSTEM stays byte-identical so it remains a cacheable static prefix
    and the prompt file stays reviewable as prose."""
    doc = pdf.open_doc(fixture_pdf(TEXT_PDF))
    p = llm._params("T1", Models(), doc, ["validity_from"], 4096,
                    hints="REF codes are printed FIGURE.SHANK.SIZE.")

    assert isinstance(p["system"], list)
    assert p["system"][0]["text"] == llm.T1_SYSTEM
    assert "FIGURE.SHANK.SIZE" in p["system"][1]["text"]


def test_no_hints_leaves_the_request_exactly_as_it_was(fixture_pdf):
    """Every document without a playbook must produce the request T1 has always
    produced -- a plain string system prompt."""
    doc = pdf.open_doc(fixture_pdf(TEXT_PDF))
    p = llm._params("T1", Models(), doc, ["validity_from"], 4096)

    assert p["system"] == llm.T1_SYSTEM


def test_sync_and_batch_build_byte_identical_requests(fixture_pdf):
    """The one property that must never break: a divergence here is silent and
    unfixable after the fact. `build_requests` returns
    [{"custom_id": ..., "params": _params(...)}], so the comparison is exact.

    Each transport opens its OWN Document rather than sharing one: reusing a
    single object across both calls would let either call's PyMuPDF state
    (e.g. anything cached or advanced by reading it) leak into the other,
    which could make the two calls agree for a reason that has nothing to do
    with `_params` actually being the same code path both take -- exactly the
    silent divergence this test exists to catch."""
    path = fixture_pdf(TEXT_PDF)
    doc_sync = pdf.open_doc(path)
    doc_batch = pdf.open_doc(path)
    models, missing, hints = Models(), ["validity_to"], "A hint."

    sync = llm._params("T2", models, doc_sync, missing,
                       llm.DEFAULT_MAX_TOKENS, hints=hints)
    batch = llm.AnthropicBatchClient(StubBatchSdk("{}"), models).build_requests(
        "abc123", "T2", doc_batch, missing, hints=hints)

    assert batch[0]["params"] == sync
