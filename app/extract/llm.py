"""T1 (Haiku text) and T2 (Sonnet vision) extraction tiers via the Anthropic API.

The Anthropic client is INJECTED, so the tier is unit-tested with a stub and the
live path is exercised in S0.4 once ANTHROPIC_API_KEY is present (live calls are
deferred until then). Both tiers ask for the full 9-field target schema as
structured output and return per-field evidence
`{value, conf, tier, verbatim, page, model_id}`, filtered to the fields the
ladder actually requested (per-field escalation). Prompts (gap G1) live in
`prompts/t1_text.md` and `prompts/t2_vision.md`.
"""

from __future__ import annotations

import base64
import json
import logging
import pathlib

from app.extract import economics
from app.extract import pdf as pdfutil
from app.extract.economics import CallCost, Usage
from app.extract.tiers import NOT_A_DOCUMENT, REF_LIST_PROMPT_CAP

_log = logging.getLogger(__name__)

_PROMPTS = pathlib.Path(__file__).resolve().parent / "prompts"


def _load_prompt(name: str) -> str:
    """Read a prompt and render the one value the code also reasons about.

    The REF-list cap lives in `tiers.REF_LIST_PROMPT_CAP` because
    `tiers.integrity_flags` compares a stored list's length against it: two
    copies of the number would let a prompt edit silently retune a data-integrity
    check. `str.replace`, not `str.format` -- the prompts are full of literal
    braces (`{value, confidence, verbatim, page}`)."""
    return (_PROMPTS / name).read_text().replace(
        "{{REF_LIST_CAP}}", str(REF_LIST_PROMPT_CAP))


T1_SYSTEM = _load_prompt("t1_text.md")
T2_SYSTEM = _load_prompt("t2_vision.md")

MAX_VISION_PAGES = 4
DEFAULT_MAX_TOKENS = 8192  # 2048 truncated the structured output on long docs (S0.4)

# --- structured-output schema (gap G1): per-field {value, confidence, verbatim,
# page}. Built here rather than hand-written JSON so it stays in sync with TARGET.
_STR = {"type": ["string", "null"]}
_ARR = {"type": "array", "items": {"type": "string"}}
# Closed vocabulary, enforced by the API rather than requested by the prompt.
# `item` was retired at the T0 producer and barred by migration 022's CHECK, but
# the tiers were never told: this field was a free string and both prompts still
# offered `item`, so T1/T2 kept returning it and the constraint dead-lettered the
# document four times in the GC pilot. null stays allowed -- a tier that cannot
# tell must be able to say so, since the ladder escalates on a missing field,
# which beats a guess.
# anyOf, NOT a union `type` with an enum. The API rejects
#   {"type": ["string","null"], "enum": [...]}
# with `Enum value 'group' does not match declared type '['string','null']'`,
# and that 400 fires before any tokens are billed -- so the whole of extraction
# was broken from 2026-08-12 until 2026-08-13 and no test caught it, because every
# test asserted the schema DICT and none ever sent it. Verified against the live
# API: anyOf(string-enum, null) is accepted and keeps null available, which the
# ladder needs -- a tier that cannot tell must be able to say so.
_SCOPE = {"anyOf": [{"type": "string", "enum": ["group", "manufacturer"]},
                    {"type": "null"}]}
_FIELD_VALUE = {
    "type": _STR, "regulation": _STR, "validity_from": _STR, "validity_to": _STR,
    "coverage_scope": _SCOPE, "ref_list": _ARR, "basic_udi_di": _STR,
    "referenced_docs": _ARR, "cert_number": _STR, "manufacturer": _STR,
    # Inert at runtime and here on purpose. `stated_class` is in no escalate
    # set, so it is never in `missing` and no tier is ever asked for it -- but
    # SCHEMA is generated from this dict and a TARGET field absent from it would
    # be unreturnable if that ever changed. Declared so the two cannot drift.
    "stated_class": _STR,
}


def _field_schema(value_schema: dict) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        # `page` is OPTIONAL and a plain integer, not nullable. The API caps a
        # schema at 16 union-typed parameters ("exponential compilation cost");
        # a nullable page on every field spent 10 of them, and the tenth target
        # field tipped the total to 18 -> 400, every extraction dead. Omitting
        # the key says "no page" just as well as a null, and `_to_ev` reads it
        # with .get() so an absent page is already None downstream. GATE then
        # raises `evidence-page-missing`, archives the document and routes it to
        # review rather than losing it.
        "required": ["value", "confidence", "verbatim"],
        "properties": {
            "value": value_schema,
            "confidence": {"type": "number"},
            "verbatim": {"type": "string"},
            "page": {"type": "integer"},
        },
    }


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": list(_FIELD_VALUE),
    "properties": {k: _field_schema(v) for k, v in _FIELD_VALUE.items()},
}


# Sentinel strings the model emits for an absent field instead of null. Treat as
# null so validate (C4) / gate (evidence) see a real absence (S0.4: regulation
# 'n.a.' was 6/20 false-positives).
_NULLISH = {"n.a.", "n/a", "na", "n.a", "none", "null", ""}

#: Fields where a sentinel is a REAL value, not an absence. `regulation` is
#: MDR|MDD|n.a. in the schema (005_registry.sql:23): an ISO/QMS certificate has
#: no medical-device regulation by nature, and coercing that to null made
#: VALIDATE reject the document as `incomplete-evidence` (REQUIRED_FIELDS,
#: validate.py:151) twelve lines before the C4 manufacturer-binding rule
#: written for exactly this document type -- 3 of 61 corpus documents.
#: Denis ruling 2026-08-11: emit 'n.a.' rather than make the field conditional.
#: The S0.4 false-positive rate this coercion protected against (regulation
#: 'n.a.' was 6/20) is now handled in the prompts, which demand the document
#: actually cite no regulation before the model may answer 'n.a.'.
_SENTINEL_IS_A_VALUE = {"regulation"}


def _norm_value(v, field: str | None = None):
    if field in _SENTINEL_IS_A_VALUE:
        return v
    if isinstance(v, str) and v.strip().lower() in _NULLISH:
        return None
    return v


def _to_ev(raw: dict, tier: str, model_id: str, field: str | None = None) -> dict:
    return {
        "value": _norm_value(raw.get("value"), field),
        "conf": float(raw.get("confidence", 0.0)),
        "tier": tier,
        "verbatim": raw.get("verbatim", ""),
        "page": raw.get("page"),
        "model_id": model_id,
    }


def _text_content(doc, missing: list[str]) -> list[dict]:
    # Bounded, not `full_text`: a 143-page IFU produced 818k characters and a
    # hard `prompt is too long: 341460 tokens > 200000 maximum`, which retries
    # into the same wall and dead-letters the document (STRAUMANN, 2026-08-17).
    # The cap announces itself inside the text, so the model reports "not in the
    # window" rather than "not in the document".
    text, dropped = pdfutil.bounded_text(doc)
    if dropped:
        _log.warning("extract.text_truncated dropped_chars=%d fields=%s",
                     dropped, ",".join(missing))
    return [{
        "type": "text",
        "text": f"Extract these fields: {', '.join(missing)}.\n\n"
                f"Document text:\n{text}",
    }]


def _vision_content(doc, missing: list[str]) -> list[dict]:
    blocks: list[dict] = [{
        "type": "text",
        "text": f"Extract these fields: {', '.join(missing)}. "
                f"Read the page images below.",
    }]
    for i in range(min(doc.page_count, MAX_VISION_PAGES)):
        png = pdfutil.rasterize_page(doc, i, dpi=150)
        blocks.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.standard_b64encode(png).decode("ascii"),
            },
        })
    return blocks


def _response_json(resp) -> dict:
    # output_config.format normally guarantees valid JSON, but a response that hit
    # max_tokens is truncated mid-object. Degrade to no fields rather than crashing
    # the whole extraction (S0.4 corpus run: 2/22 docs died here).
    text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        _log.warning("extract: unparseable structured output (%d chars, likely "
                     "max_tokens truncation): %s", len(text), exc)
        return {}


def _system(base: str, hints: str | None) -> str | list[dict]:
    """System prompt for one request.

    With no hints this returns the prompt string unchanged, so every document
    without a playbook produces byte-for-byte the request it always did. With
    hints it becomes two blocks: `base` FIRST and byte-identical (a stable,
    cacheable prefix and a prompt file that stays reviewable as prose), then the
    per-manufacturer block. The hints are never interpolated into the prompt
    file itself."""
    if not hints:
        return base
    return [
        {"type": "text", "text": base},
        {"type": "text", "text": hints},
    ]


def _params(tier: str, models, doc, missing: list[str], max_tokens: int,
            hints: str | None = None) -> dict:
    """The messages.create() body for a tier. Shared by the sync tier and the
    batch client so both submit byte-identical requests (only the transport
    differs). T1 = text content, T2 = page images."""
    if tier == "T1":
        model, system, content = models.t1, T1_SYSTEM, _text_content(doc, missing)
    else:
        model, system, content = models.t2, T2_SYSTEM, _vision_content(doc, missing)
    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": _system(system, hints),
        "messages": [{"role": "user", "content": content}],
        "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}},
    }


def _custom_id(content_hash: str, tier: str) -> str:
    """Batch request id. The Batch API rejects anything outside
    ^[a-zA-Z0-9_-]{1,64}$ (a 400), and a full sha256 content_hash is already 64
    chars — so the tier goes FIRST (never truncated; that's what results() reads
    back) and the whole thing is capped at 64. Batch-of-1 per hash+tier, so this
    only has to be legal and tier-recoverable, not globally unique."""
    return f"{tier}_{content_hash}"[:64]


def _tier_of(custom_id: str) -> str:
    return custom_id.split("_", 1)[0]


def _fields_from_json(data: dict, tier: str, model: str) -> dict:
    """Every well-formed field in the structured response -> per-field evidence.
    Callers filter to the fields they actually escalated."""
    out: dict = {}
    for field, raw in data.items():
        if isinstance(raw, dict) and "value" in raw:
            out[field] = _to_ev(raw, tier, model, field)
    return out


def _capture(usage_log: list, tier: str, model_id: str, message, batch: bool) -> CallCost:
    """Capture one call's token usage into `usage_log` and emit a cost log line.
    `message` is the Anthropic Message (a messages.create response, or a batch
    result's `.message`). Capture is free (usage rides on a response we already
    have) and must never break extraction: an unpriced model is REPORTED as a
    warning with tokens intact and cost left to the ledger, never silent."""
    call = CallCost(tier, model_id, Usage.from_message(message), batch)
    usage_log.append(call)
    u = call.usage
    try:
        _log.info("extract.cost tier=%s model=%s in=%d out=%d batch=%s usd=%.6f",
                  tier, model_id, u.input_tokens, u.output_tokens, batch, call.cost)
    except economics.UnknownModelPricing:
        _log.warning("extract.cost tier=%s model=%s in=%d out=%d batch=%s usd=UNPRICED",
                     tier, model_id, u.input_tokens, u.output_tokens, batch)
    return call


DOC_CLASS_SYSTEM = _load_prompt("doc_class.md")

#: Pages shown to the doc-class call. Two is what T0's own header window reads
#: (`t0_templates._HEADER_PAGES`), and the question is the same one: what does
#: this file announce itself to be? Sending more invites the model to find a
#: "declaration of conformity" in a manual's chapter list, which is precisely
#: the confusion this call exists to end.
DOC_CLASS_PAGES = 2

DOC_CLASS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["doc_class", "quote"],
    "properties": {
        "doc_class": {
            "type": "string",
            "enum": ["DoC", "EC", "ISO", "IFU", "SPP", "NOT-A-COMPLIANCE-DOCUMENT"],
        },
        "quote": {"type": "string"},
    },
}

class AnthropicLlm:
    """LLM tier for the ladder: `extract(tier, doc, filename, missing)`.

    `client` is an anthropic.Anthropic (or a stub in tests); `models` exposes
    `.t1` / `.t2` model ids (config.models).
    """

    def __init__(self, client, models, max_tokens: int = DEFAULT_MAX_TOKENS):
        self.client = client
        self.models = models
        self.max_tokens = max_tokens
        self.usage_log: list[CallCost] = []  # priced calls this instance made

    def extract(self, tier: str, doc, filename: str, missing: list[str],
               hints: str | None = None) -> dict:
        params = _params(tier, self.models, doc, missing, self.max_tokens, hints=hints)
        resp = self.client.messages.create(**params)
        _capture(self.usage_log, tier, params["model"], resp, batch=False)
        parsed = _fields_from_json(_response_json(resp), tier, params["model"])
        return {f: parsed[f] for f in missing if f in parsed}

    def classify(self, doc, filename: str) -> tuple[str, str]:
        """Decide what this file is when T0 could not (`doc_class == "unknown"`).

        Returns `(doc_class, quote)` where doc_class is one of the five
        compliance types or `NOT_A_DOCUMENT`. T0 types 88% of the real corpus
        from its own header window and never reaches here; this call is the
        remaining 12% plus everything the open web returns that is not a
        document at all.

        Cheap tier deliberately (`models.t1`): the question is "what does the
        first two pages announce this to be", which is exactly what Haiku is
        good at, and it runs on every document T0 declined.

        A malformed or truncated answer degrades to `NOT_A_DOCUMENT` rather than
        to a guess. That direction is not arbitrary -- a document wrongly
        refused is reviewed by a person, while one wrongly admitted becomes a
        fabricated compliance record, which is the failure this whole path
        exists to prevent (four of them reached the registry on 2026-09-02, one
        into `production`).
        """
        pages = pdfutil.page_texts(doc)[:DOC_CLASS_PAGES]
        text = "\n".join(pages).strip()
        if not text:
            return NOT_A_DOCUMENT, ""
        params = {
            "model": self.models.t1,
            "max_tokens": 256,
            "system": DOC_CLASS_SYSTEM,
            "messages": [{"role": "user", "content":
                          f"File name: {filename}\n\n{text}"}],
            "output_config": {"format": {"type": "json_schema",
                                         "schema": DOC_CLASS_SCHEMA}},
        }
        resp = self.client.messages.create(**params)
        _capture(self.usage_log, "T1", params["model"], resp, batch=False)
        payload = _response_json(resp)
        answer = payload.get("doc_class")
        if answer not in ("DoC", "EC", "ISO", "IFU", "SPP"):
            return NOT_A_DOCUMENT, str(payload.get("quote") or "")
        return answer, str(payload.get("quote") or "")


class AnthropicBatchClient:
    """Batch API transport for the tier ladder (C9). Implements the interface
    `app.extract.batching.advance_batch` drives: `submit(requests) -> batch_id`,
    `done(batch_id) -> bool`, `results(batch_id) -> {field: evidence}`. The
    handler builds requests with `build_requests` and hands them to
    `advance_batch`; `batch_ref` (keyed by content_hash+tier) is the crash-safe
    state, so this client holds no state of its own.

    Batch-of-1 per (content_hash, tier): the batch_ref PK is (content_hash,
    tier), so each doc+tier is its own batch. Bulk cross-doc batching (the sweep
    economy) is a later concern (backfill.scan); the transport is identical.
    """

    def __init__(self, client, models, max_tokens: int = DEFAULT_MAX_TOKENS):
        self.client = client
        self.models = models
        self.max_tokens = max_tokens
        self.usage_log: list[CallCost] = []  # priced calls, populated by results()

    def build_requests(self, content_hash: str, tier: str, doc, missing: list[str],
                       hints: str | None = None) -> list[dict]:
        return [{
            "custom_id": _custom_id(content_hash, tier),
            "params": _params(tier, self.models, doc, missing, self.max_tokens, hints=hints),
        }]

    def submit(self, requests) -> str:
        return self.client.messages.batches.create(requests=requests).id

    def done(self, batch_id: str) -> bool:
        return self.client.messages.batches.retrieve(batch_id).processing_status == "ended"

    def results(self, batch_id: str) -> dict:
        out: dict = {}
        for item in self.client.messages.batches.results(batch_id):
            if getattr(item.result, "type", None) != "succeeded":
                continue
            tier = _tier_of(item.custom_id)
            model = self.models.t1 if tier == "T1" else self.models.t2
            _capture(self.usage_log, tier, model, item.result.message, batch=True)
            out.update(_fields_from_json(_response_json(item.result.message), tier, model))
        return out
