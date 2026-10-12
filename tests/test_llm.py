"""T1/T2 Anthropic tiers with an injected stub client (no live API calls).

Verifies request routing (model, text vs image content) and that the structured
response is parsed into per-field evidence, filtered to the requested fields.
"""

from __future__ import annotations

import base64
import re
from types import SimpleNamespace

import pytest

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

    def __init__(self, text, status="ended", custom_id="T1_abc123"):
        self._text = text
        self._status = status
        self._custom_id = custom_id
        self.created_requests = None

    def create(self, *, requests):
        self.created_requests = list(requests)
        return SimpleNamespace(id="batch_123")

    def retrieve(self, batch_id):
        return SimpleNamespace(processing_status=self._status)

    def results(self, batch_id):
        # one succeeded item; custom_id carries the tier (batch-of-1 per hash+tier)
        return [_BatchResultItem(self._custom_id, self._text)]


class StubBatchSdk:
    def __init__(self, text, status="ended", custom_id="T1_abc123"):
        self.messages = SimpleNamespace(batches=StubBatches(text, status, custom_id))


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
    """Changed deliberately with prompt caching (extraction-ladder Task 6). It
    used to pin a plain-string system prompt; with no hints `system` is now a
    one-block list whose only block is the unchanged prompt text plus
    `cache_control`. Everything else in the request is pinned by
    `test_caching_changes_only_the_system_field`."""
    doc = pdf.open_doc(fixture_pdf(TEXT_PDF))
    p = llm._params("T1", Models(), doc, ["validity_from"], 4096)

    assert p["system"] == [
        {"type": "text", "text": llm.T1_SYSTEM, "cache_control": {"type": "ephemeral"}}]


@pytest.mark.parametrize("label", ["T1", "T2"])
def test_caching_changes_only_the_system_field(fixture_pdf, label):
    """Compare against a request rebuilt by hand from the same parts: model,
    max_tokens, messages, output_config (and thinking) are as they were."""
    doc = pdf.open_doc(fixture_pdf(TEXT_PDF))
    p = llm._params(label, Models(), doc, ["validity_from"], 4096)
    read = llm.resolve_read(label, Models())
    if read.kind == llm.TEXT:
        content = llm._text_content(doc, ["validity_from"])
    else:
        content = llm._vision_content(doc, ["validity_from"], read.pages)
    expected = {
        "model": read.model, "max_tokens": 4096,
        "messages": [{"role": "user", "content": content}],
        "output_config": {"format": {"type": "json_schema", "schema": llm.SCHEMA}},
    }
    assert {k: v for k, v in p.items() if k != "system"} == expected


@pytest.mark.parametrize("label", ["T1", "T2"])
def test_cache_marker_sits_on_the_static_block_never_on_the_hints(fixture_pdf, label):
    doc = pdf.open_doc(fixture_pdf(TEXT_PDF))
    p = llm._params(label, Models(), doc, ["validity_from"], 4096, hints="A hint.")

    assert p["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in p["system"][1]
    assert p["system"][1]["text"] == "A hint."


def test_batch_requests_carry_the_cache_marker(fixture_pdf):
    doc = pdf.open_doc(fixture_pdf(TEXT_PDF))
    req = llm.AnthropicBatchClient(StubBatchSdk("{}"), Models()).build_requests(
        "abc123", "T1", doc, ["validity_from"])
    assert req[0]["params"]["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_doc_class_prompt_is_not_cached():
    """Decided in Task 6: the prompt is about 2.75k characters (roughly 700
    tokens), over Haiku 5.5's 512-token minimum but under Haiku 4.5's 4,096, and
    `models.t1` can be either, so a marker would be a no-op on one and a cache
    write nothing re-reads soon on the other. Left as the plain string."""
    assert isinstance(llm.DOC_CLASS_SYSTEM, str)
    assert len(llm.DOC_CLASS_SYSTEM) < 4 * 4096


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


# --- read steps and labels (extraction-ladder plan 2026-10-12, Task 1) ---------
#
# A read names its kind (text / image), model, thinking and, for an image read,
# its pages. The label exists for the cost ledger and `batch_ref` only; the
# evidence keeps `tier` T1 (text) or T2 (image). Before this, `_params` routed
# on `tier == "T1"` and sent EVERY other label to `models.t2` -- so the first
# cheap image read the ladder added would have been billed at Sonnet.

class LadderModels:
    """Every model key distinct, so a request's model names the step that sent it."""
    t1 = "model-t1"
    t2 = "model-t2"
    vision_first = "model-vision-first"
    second_read = "model-second-read"
    rank = "model-rank"


THINKING_OFF = {"type": "disabled"}

#: The plan's "Read steps and their labels" table, transcribed.
PLAN_STEPS = [
    # label,            kind,    model,                thinking
    ("doc-class",       "text",  "model-t1",           None),
    ("doc-class-image", "image", "model-vision-first", None),
    ("T1",              "text",  "model-t1",           None),
    ("T1-second",       "text",  "model-second-read",  THINKING_OFF),
    ("T2-first",        "image", "model-vision-first", None),
    ("T2-second",       "image", "model-second-read",  THINKING_OFF),
    ("T2",              "image", "model-t2",           None),
]
EXTRACT_STEPS = [s for s in PLAN_STEPS if not s[0].startswith("doc-class")]


def _probe_pdf(pages: int):
    """An in-memory document whose pages are told apart by their text, so an
    image block can be matched to the page it was rendered from."""
    import pymupdf
    doc = pymupdf.open()
    for n in range(1, pages + 1):
        doc.new_page().insert_text((72, 72), f"Probe page {n} " * 3, fontsize=12)
    return doc


def _images(params) -> list[str]:
    return [b["source"]["data"] for b in params["messages"][0]["content"]
            if b.get("type") == "image"]


def _png(doc, page_no: int) -> str:
    return base64.standard_b64encode(pdf.rasterize_page(doc, page_no - 1, dpi=150)).decode("ascii")


def test_read_steps_are_the_plans_table():
    assert set(llm.READ_STEPS) == {s[0] for s in PLAN_STEPS}


@pytest.mark.parametrize("label, kind, model, thinking", PLAN_STEPS)
def test_each_label_resolves_to_its_kind_model_and_thinking(label, kind, model, thinking):
    read = llm.resolve_read(label, LadderModels())
    assert (read.label, read.kind, read.model, read.thinking) == (label, kind, model, thinking)
    assert read.evidence_tier == ("T1" if kind == "text" else "T2")


@pytest.mark.parametrize("label", [s[0] for s in PLAN_STEPS])
def test_every_label_survives_the_batch_custom_id(label):
    """`_tier_of` splits on the first `_`, so a label may hold hyphens only."""
    assert "_" not in label
    cid = llm._custom_id("a" * 64, label)
    assert CUSTOM_ID_RE.match(cid), cid
    assert llm._tier_of(cid) == label


def test_a_label_with_an_underscore_is_refused():
    with pytest.raises(ValueError):
        llm._custom_id("abc", "T2_first")


@pytest.mark.parametrize("label, kind, model, thinking", EXTRACT_STEPS)
def test_params_route_by_kind_not_by_label(fixture_pdf, label, kind, model, thinking):
    doc = pdf.open_doc(fixture_pdf(TEXT_PDF))
    p = llm._params(label, LadderModels(), doc, ["type"], 4096)

    assert p["model"] == model
    if kind == "text":
        assert p["system"][0]["text"] == llm.T1_SYSTEM
        assert _images(p) == []
    else:
        assert p["system"][0]["text"] == llm.T2_SYSTEM
        assert _images(p)
    if thinking is None:
        assert "thinking" not in p
    else:
        assert p["thinking"] == thinking


def test_a_cheap_image_read_is_never_sent_to_the_sonnet_model(fixture_pdf):
    """The regression the kind routing exists for."""
    doc = pdf.open_doc(fixture_pdf(TEXT_PDF))
    for label in ("T2-first", "T2-second"):
        assert llm._params(label, LadderModels(), doc, ["type"], 4096)["model"] != LadderModels.t2


@pytest.mark.parametrize("label", ["T1", "T2"])
def test_todays_two_reads_send_exactly_the_keys_they_always_did(fixture_pdf, label):
    """No `thinking` key, nothing new: the eval cache keys requests by their
    exact bytes, and the live ladder must not change in this task."""
    doc = pdf.open_doc(fixture_pdf(TEXT_PDF))
    p = llm._params(label, LadderModels(), doc, ["type"], 4096)
    assert set(p) == {"model", "max_tokens", "system", "messages", "output_config"}


@pytest.mark.parametrize("label", ["doc-class", "doc-class-image"])
def test_doc_class_labels_are_not_extraction_reads(fixture_pdf, label):
    """They name the classify calls, which send their own prompt and schema."""
    doc = pdf.open_doc(fixture_pdf(TEXT_PDF))
    with pytest.raises(ValueError):
        llm._params(label, LadderModels(), doc, ["type"], 4096)


def test_an_unknown_label_must_name_its_kind_and_model(fixture_pdf):
    doc = pdf.open_doc(fixture_pdf(TEXT_PDF))
    with pytest.raises(ValueError):
        llm._params("T3-maybe", LadderModels(), doc, ["type"], 4096)
    with pytest.raises(ValueError):
        llm._params("T3-maybe", LadderModels(), doc, ["type"], 4096, kind="image")
    p = llm._params("T3-maybe", LadderModels(), doc, ["type"], 4096,
                    kind="image", model="named-model")
    assert p["model"] == "named-model" and _images(p)


def test_a_kind_outside_text_and_image_is_refused(fixture_pdf):
    doc = pdf.open_doc(fixture_pdf(TEXT_PDF))
    with pytest.raises(ValueError):
        llm._params("T3-maybe", LadderModels(), doc, ["type"], 4096,
                    kind="audio", model="named-model")


@pytest.mark.parametrize("override", [
    {"kind": "image"},            # a "T1" that is really an image read
    {"kind": "text"},             # even the row's own kind: the row decides
    {"model": "other-model"},     # a "T1" billed to another model
])
@pytest.mark.parametrize("label", [s[0] for s in EXTRACT_STEPS])
def test_a_table_label_refuses_a_kind_or_model_override(fixture_pdf, label, override):
    """The label is the ledger's key: a different read under it would share
    a row with the real one, and the unique key would drop one of them."""
    doc = pdf.open_doc(fixture_pdf(TEXT_PDF))
    with pytest.raises(ValueError, match="own label"):
        llm._params(label, LadderModels(), doc, ["type"], 4096, **override)


def test_thinking_can_be_set_or_cleared_explicitly(fixture_pdf):
    doc = pdf.open_doc(fixture_pdf(TEXT_PDF))
    # set on a row that names none
    p = llm._params("T2-first", LadderModels(), doc, ["type"], 4096, thinking=THINKING_OFF)
    assert p["thinking"] == THINKING_OFF
    # cleared on a row that names one: no thinking key at all
    p = llm._params("T2-second", LadderModels(), doc, ["type"], 4096, thinking=None)
    assert "thinking" not in p
    # left alone: the row's own
    p = llm._params("T2-second", LadderModels(), doc, ["type"], 4096)
    assert p["thinking"] == THINKING_OFF


# --- image pages -------------------------------------------------------------

def test_an_image_read_defaults_to_pages_one_to_four():
    doc = _probe_pdf(6)
    p = llm._params("T2-first", LadderModels(), doc, ["type"], 4096)
    assert _images(p) == [_png(doc, n) for n in (1, 2, 3, 4)]


def test_a_short_document_sends_every_page_it_has():
    doc = _probe_pdf(2)
    p = llm._params("T2", LadderModels(), doc, ["type"], 4096)
    assert _images(p) == [_png(doc, 1), _png(doc, 2)]


def test_an_explicit_page_list_is_sent_in_its_own_order():
    doc = _probe_pdf(6)
    p = llm._params("T2-first", LadderModels(), doc, ["type"], 4096, pages=[5, 2])
    assert _images(p) == [_png(doc, 5), _png(doc, 2)]


@pytest.mark.parametrize("pages", [
    [1, 2, 3, 4, 5],     # more than MAX_VISION_PAGES
    [],                  # nothing to read
    [0],                 # pages are 1-based
    [7],                 # past the end of a 6-page document
    [2, 2],              # the same page twice
])
def test_a_bad_page_list_is_refused_not_trimmed(pages):
    doc = _probe_pdf(6)
    with pytest.raises(ValueError):
        llm._params("T2-first", LadderModels(), doc, ["type"], 4096, pages=pages)


def test_pages_on_a_text_read_are_refused(fixture_pdf):
    doc = pdf.open_doc(fixture_pdf(TEXT_PDF))
    with pytest.raises(ValueError):
        llm._params("T1", LadderModels(), doc, ["type"], 4096, pages=[1])


# --- the sync read: label to the ledger, kind to the evidence ----------------

_THREE_FIELDS = (
    '{"regulation": {"value": "MDR", "confidence": 0.9, "verbatim": "a", "page": 1}, '
    '"cert_number": {"value": "C-1", "confidence": 0.9, "verbatim": "b", "page": 2}, '
    '"type": {"value": "EC", "confidence": 0.9, "verbatim": "c", "page": 9}}'
)


@pytest.mark.parametrize("label, kind, model, _t", EXTRACT_STEPS)
def test_label_goes_to_the_ledger_and_kind_to_the_evidence(fixture_pdf, label, kind, model, _t):
    tier = llm.AnthropicLlm(StubClient(_THREE_FIELDS), LadderModels())
    doc = pdf.open_doc(fixture_pdf(TEXT_PDF))

    got = tier.extract(label, doc, "f.pdf", missing=["regulation"])

    ev = got["regulation"]
    assert ev["tier"] == ("T1" if kind == "text" else "T2")
    assert ev["model_id"] == model
    assert [(c.tier, c.model_id) for c in tier.usage_log] == [(label, model)]


def test_an_image_read_cites_document_pages_not_image_positions():
    """The vision prompt reports `page` as the 1-based index of the IMAGE. With
    an explicit page list that index is translated back to the document's page,
    and a cite outside the images sent is nulled rather than kept wrong, and
    counted. The value and its quote stay."""
    client = StubClient(_THREE_FIELDS)
    tier = llm.AnthropicLlm(client, LadderModels())
    doc = _probe_pdf(6)

    got = tier.extract("T2-first", doc, "f.pdf", ["regulation", "cert_number", "type"],
                       pages=[5, 2])

    assert got["regulation"]["page"] == 5
    assert got["cert_number"]["page"] == 2
    assert got["type"]["page"] is None
    assert (got["type"]["value"], got["type"]["verbatim"]) == ("EC", "c")
    assert tier.cited_pages_nulled == 1
    assert _images(client.messages.kwargs) == [_png(doc, 5), _png(doc, 2)]


def test_only_the_fields_returned_count_as_nulled():
    """`type` cites image 9 of 2, but nobody asked for it: nothing to count."""
    tier = llm.AnthropicLlm(StubClient(_THREE_FIELDS), LadderModels())
    tier.extract("T2-first", _probe_pdf(6), "f.pdf", ["regulation"], pages=[5, 2])
    assert tier.cited_pages_nulled == 0


def test_the_default_pages_keep_the_cited_page_as_it_was():
    """Today's T2: images are pages 1-4 in order, so the image index already is
    the page, and an out-of-range cite passes through untouched as before."""
    tier = llm.AnthropicLlm(StubClient(_THREE_FIELDS), LadderModels())
    got = tier.extract("T2", _probe_pdf(6), "f.pdf", ["regulation", "cert_number", "type"])
    assert [got[f]["page"] for f in ("regulation", "cert_number", "type")] == [1, 2, 9]
    assert tier.cited_pages_nulled == 0


def test_classify_logs_its_own_label():
    """It used to log `T1`, the same as the T1 read on the same document, and the
    cost ledger's (content_hash, tier, extract_rev) key dropped one of them."""
    tier = llm.AnthropicLlm(StubClient('{"doc_class": "DoC", "quote": "EU DoC"}'), Models())

    assert tier.classify(_probe_pdf(1), "f.pdf") == ("DoC", "EU DoC")
    assert [(c.tier, c.model_id) for c in tier.usage_log] == [("doc-class", Models.t1)]


# --- the batch read ----------------------------------------------------------

@pytest.mark.parametrize("label, kind, model, _t", EXTRACT_STEPS)
def test_batch_results_map_each_label_to_its_model(label, kind, model, _t):
    """A fresh client (a later poll) recovers the model from the label alone."""
    payload = '{"regulation": {"value": "MDR", "confidence": 0.9, "verbatim": "x", "page": 1}}'
    bc = llm.AnthropicBatchClient(StubBatchSdk(payload, custom_id=f"{label}_abc123"),
                                  LadderModels())

    got = bc.results("batch_123")

    assert got["regulation"]["tier"] == ("T1" if kind == "text" else "T2")
    assert got["regulation"]["model_id"] == model
    assert [(c.tier, c.model_id, c.batch) for c in bc.usage_log] == [(label, model, True)]


def test_batch_results_use_the_model_the_request_was_built_with(fixture_pdf):
    """A label with no READ_STEPS row names its own model; the poll that built
    the request prices and stamps the result with that model."""
    payload = '{"regulation": {"value": "MDR", "confidence": 0.9, "verbatim": "x", "page": 1}}'
    bc = llm.AnthropicBatchClient(StubBatchSdk(payload, custom_id="T3-probe_abc123"),
                                  LadderModels())
    doc = pdf.open_doc(fixture_pdf(TEXT_PDF))
    req = bc.build_requests("abc123", "T3-probe", doc, ["regulation"],
                            kind="image", model="override-model")

    got = bc.results("batch_123")

    assert req[0]["params"]["model"] == "override-model"
    assert got["regulation"]["model_id"] == "override-model"
    assert got["regulation"]["tier"] == "T2"
    assert bc.usage_log[0].model_id == "override-model"


def test_a_batch_read_refuses_an_explicit_page_list():
    """`batch_ref` does not store the pages sent, so `results()` on a later poll
    could translate the cited image index only by recomputing the list, and a
    list that changed in between would cite the wrong page without a sound."""
    bc = llm.AnthropicBatchClient(StubBatchSdk("{}"), LadderModels())
    with pytest.raises(ValueError, match="page list"):
        bc.build_requests("abc123", "T2-first", _probe_pdf(6), ["regulation"], pages=[5, 2])
    assert bc._reads == {}


def test_batch_results_refuse_a_label_no_table_row_explains():
    """Never priced as Sonnet by default, which is what the old fallback did."""
    bc = llm.AnthropicBatchClient(StubBatchSdk("{}", custom_id="mystery_abc123"), LadderModels())
    with pytest.raises(ValueError):
        bc.results("batch_123")


def test_sync_and_batch_build_identical_requests_for_a_ladder_read():
    """The byte-identity guarantee above, for a ladder read with hints and its
    row's thinking setting (a batch read takes no page list)."""
    missing, hints = ["validity_to"], "A hint."
    sync = llm._params("T2-second", LadderModels(), _probe_pdf(6), missing,
                       llm.DEFAULT_MAX_TOKENS, hints=hints)
    batch = llm.AnthropicBatchClient(StubBatchSdk("{}"), LadderModels()).build_requests(
        "abc123", "T2-second", _probe_pdf(6), missing, hints=hints)

    assert batch[0]["params"] == sync
    assert batch[0]["custom_id"] == "T2-second_abc123"
