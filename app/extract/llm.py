"""T1 (text) and T2 (image) extraction reads via the Anthropic API.

The Anthropic client is INJECTED, so the tier is unit-tested with a stub and the
live path is exercised in S0.4 once ANTHROPIC_API_KEY is present (live calls are
deferred until then). Both kinds ask for the full target schema as structured
output and return per-field evidence `{value, conf, tier, verbatim, page,
model_id}`, filtered to the fields the ladder actually requested (per-field
escalation). Prompts (gap G1) live in `prompts/t1_text.md` and
`prompts/t2_vision.md`.

Every call is a READ STEP with a label (`READ_STEPS`, the extraction-ladder
plan's table of 2026-10-12). The label names the call in the cost ledger and in
`batch_ref`; the step's KIND (text / image) picks the prompt and content and
stamps the evidence tier, which stays `T1` or `T2` whatever the label.
"""

from __future__ import annotations

import base64
import json
import logging
import pathlib
from dataclasses import dataclass

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

TEXT, IMAGE = "text", "image"
_KINDS = (TEXT, IMAGE)

#: What a second read sends (rulings 2026-10-12: "second read Haiku 5.5
#: thinking off"). The eval measured the agreement rule with exactly this.
THINKING_OFF = {"type": "disabled"}


@dataclass(frozen=True)
class ReadStep:
    """One row of the read-steps table: what kind of read a label is, which
    `config.models` key names its model, and its thinking setting (None = send
    no `thinking` key, the API default)."""

    kind: str
    model_key: str
    thinking: dict | None = None


#: The extraction-ladder plan's "Read steps and their labels" (2026-10-12), one
#: table for sync and batch alike. Labels carry hyphens only: `_custom_id` joins
#: label and hash with `_` and `_tier_of` splits on the first one. The text
#: ladder sends `doc-class`, `T1` and `T2`; the scan ladder (`tiers.
#: run_scan_ladder`) `doc-class-image`, `T2-first`, `T2-second` and `T2`;
#: `T1-second` is plumbing for Task 5. The two `doc-class` rows label the
#: classify calls, never an `extract`.
#:
#: `doc-class-image` sends thinking off (Task 3, 2026-10-12): its answer is one
#: word and a quote in 256 tokens, and a Haiku 5.5 thinking by default could
#: spend the budget before answering, which `_classify_answer` would then have
#: to fail on. The plan's table names no thinking setting for it; this row is
#: the one place that differs.
READ_STEPS: dict[str, ReadStep] = {
    "doc-class": ReadStep(TEXT, "t1"),
    "doc-class-image": ReadStep(IMAGE, "vision_first", THINKING_OFF),
    "T1": ReadStep(TEXT, "t1"),
    "T1-second": ReadStep(TEXT, "second_read", THINKING_OFF),
    "T2-first": ReadStep(IMAGE, "vision_first"),
    "T2-second": ReadStep(IMAGE, "second_read", THINKING_OFF),
    "T2": ReadStep(IMAGE, "t2"),
}
DOC_CLASS_LABEL = "doc-class"
_CLASSIFY_LABELS = frozenset({"doc-class", "doc-class-image"})


@dataclass(frozen=True)
class Read:
    """One resolved read: its label (the ledger key), kind, model, thinking and,
    for an image read, an explicit page list (None = pages 1..4, today's T2)."""

    label: str
    kind: str
    model: str
    thinking: dict | None = None
    pages: tuple[int, ...] | None = None

    @property
    def evidence_tier(self) -> str:
        """`evidence.tier`: T1 for a text read, T2 for an image read. The label
        never reaches evidence -- GATE's page rule and the REF-cap flag test
        for exactly these two values."""
        return "T1" if self.kind == TEXT else "T2"


#: `resolve_read`'s "no thinking option given" marker, so that an explicit
#: `thinking=None` can mean "send no thinking key" even on a label whose row
#: names one.
_FROM_TABLE = object()


def resolve_read(label: str, models, *, kind: str | None = None, model: str | None = None,
                 thinking=_FROM_TABLE, pages=None) -> Read:
    """The read a label names.

    A `READ_STEPS` label takes its kind and model from its row, and naming
    either is refused: the label is the cost ledger's key, so a `T1` that
    was really an image read, or a `T2-first` sent to another model, would
    share a row with the real one and the ledger's unique key would drop one
    of them -- the loss the labels exist to end. A different read takes a
    label of its own. A label with no row must name both its kind and its
    model: routing it to a default would bill it at whatever that default is,
    which is how every non-`T1` label used to go to `models.t2`.

    `thinking` defaults to the row's setting (none for a label with no row).
    `thinking=None` sends no thinking key at all, the API default, whatever the
    row says; a dict sends that dict.

    `pages` (image reads only) are 1-based document pages in the order they are
    sent: at most `MAX_VISION_PAGES`, none twice. Refused rather than trimmed --
    a caller asking for five pages has a bug, not a preference."""
    step = READ_STEPS.get(label)
    if step is not None:
        if kind is not None or model is not None:
            raise ValueError(f"read {label!r}: a READ_STEPS label takes its kind and "
                             f"model from its row; a different read needs its own label")
        kind, model = step.kind, getattr(models, step.model_key)
    if kind not in _KINDS:
        raise ValueError(f"read {label!r}: kind must be one of {_KINDS}, got {kind!r}")
    if not model:
        raise ValueError(f"read {label!r}: no model (unknown label, none named)")
    if thinking is _FROM_TABLE:
        thinking = step.thinking if step is not None else None
    if pages is not None:
        if kind != IMAGE:
            raise ValueError(f"read {label!r}: pages apply to image reads only")
        pages = tuple(pages)
        if not 1 <= len(pages) <= MAX_VISION_PAGES:
            raise ValueError(f"read {label!r}: 1 to {MAX_VISION_PAGES} pages, got {pages}")
        if len(set(pages)) != len(pages) or any(
                not isinstance(n, int) or n < 1 for n in pages):
            raise ValueError(f"read {label!r}: pages are distinct 1-based numbers, got {pages}")
    return Read(label, kind, model, thinking, pages)

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


def _vision_content(doc, missing: list[str], pages: tuple[int, ...] | None = None) -> list[dict]:
    """The prompt line, then one image per page: `pages` in the order given, or
    pages 1..MAX_VISION_PAGES when None (today's T2, byte for byte)."""
    if pages is None:
        indexes = range(min(doc.page_count, MAX_VISION_PAGES))
    else:
        beyond = [n for n in pages if n > doc.page_count]
        if beyond:
            raise ValueError(f"pages {beyond} past the end of a {doc.page_count}-page document")
        indexes = [n - 1 for n in pages]
    blocks: list[dict] = [{
        "type": "text",
        "text": f"Extract these fields: {', '.join(missing)}. "
                f"Read the page images below.",
    }]
    for i in indexes:
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


def _system(base: str, hints: str | None) -> list[dict]:
    """System prompt for one request, always a list of blocks.

    `base` is FIRST and byte-identical (a stable prefix and a prompt file that
    stays reviewable as prose) and carries `cache_control` (5-minute ephemeral,
    the default), so the static prompt is written to the cache once and read at
    a fraction of the input price by the next call inside the window. The
    per-manufacturer hints, when there are any, follow as a second block with no
    marker: they change from one manufacturer to the next, and a marker on them
    would only pay a cache write that nothing reads. The hints are never
    interpolated into the prompt file itself. Below the model's minimum
    cacheable prefix the marker is ignored by the API, not an error."""
    blocks = [{"type": "text", "text": base, "cache_control": {"type": "ephemeral"}}]
    if hints:
        blocks.append({"type": "text", "text": hints})
    return blocks


def _request(read: Read, doc, missing: list[str], max_tokens: int,
             hints: str | None = None) -> dict:
    """The messages.create() body for one resolved read. Routed by KIND, never
    by label: text = the text prompt and the bounded document text, image = the
    vision prompt and page images. `thinking` is added only when the read names
    one, so `T1` and `T2` still send no thinking key; their `system` is the
    block list `_system` builds, marked for caching since Task 6 (2026-10-12)."""
    if read.label in _CLASSIFY_LABELS:
        raise ValueError(f"{read.label!r} labels a classify call, not an extraction read")
    if read.kind == TEXT:
        system, content = T1_SYSTEM, _text_content(doc, missing)
    else:
        system, content = T2_SYSTEM, _vision_content(doc, missing, read.pages)
    params = {
        "model": read.model,
        "max_tokens": max_tokens,
        "system": _system(system, hints),
        "messages": [{"role": "user", "content": content}],
        "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}},
    }
    if read.thinking is not None:
        params["thinking"] = dict(read.thinking)   # a copy: never hand out the table's dict
    return params


def _params(tier: str, models, doc, missing: list[str], max_tokens: int,
            hints: str | None = None, **read_options) -> dict:
    """The messages.create() body for a labelled read. Shared by the sync tier
    and the batch client so both submit byte-identical requests (only the
    transport differs). `read_options` are `resolve_read`'s kind / model /
    thinking / pages."""
    return _request(resolve_read(tier, models, **read_options), doc, missing,
                    max_tokens, hints)


def _cited_pages(fields: dict, read: Read) -> int:
    """Translate each field's cited page from image position to document page,
    in place. Returns how many cites it had to null.

    The vision prompt asks for "the 1-based index of the page image", which is
    the document page only while the images are pages 1..4 in order -- the
    default, left exactly as it was. With an explicit page list the index is
    looked up in it, and a cite outside the images sent becomes None rather
    than a page nobody showed the model. The value and its quote stay.

    What a None page does downstream depends on the field. On `type`,
    `regulation` or `coverage_scope` (GATE's `REQUIRED_FIELDS`) GATE raises
    `evidence-page-missing`, a blocking flag, and the document goes to manual
    review. On every other field GATE raises nothing and the evidence row is
    written with no page. So the caller counts what this returns: the sync
    read keeps a running total that the extract handler puts on the job
    result."""
    if read.pages is None:
        return 0
    nulled = 0
    for ev in fields.values():
        n = ev.get("page")
        if n is None:
            continue
        if isinstance(n, int) and 1 <= n <= len(read.pages):
            ev["page"] = read.pages[n - 1]
        else:
            ev["page"] = None
            nulled += 1
    return nulled


def _custom_id(content_hash: str, tier: str) -> str:
    """Batch request id. The Batch API rejects anything outside
    ^[a-zA-Z0-9_-]{1,64}$ (a 400), and a full sha256 content_hash is already 64
    chars — so the tier goes FIRST (never truncated; that's what results() reads
    back) and the whole thing is capped at 64. Batch-of-1 per hash+tier, so this
    only has to be legal and tier-recoverable, not globally unique.

    `tier` is a read label, and an `_` in it would make `_tier_of` read back a
    prefix of it: refused here rather than discovered as a wrong price."""
    if "_" in tier:
        raise ValueError(f"read label {tier!r} contains '_'; labels use hyphens")
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
    # `in` is the uncached remainder only; `cr` / `cw` are the cache read and
    # write tokens, so the prompt was in + cr + cw.
    try:
        _log.info("extract.cost tier=%s model=%s in=%d cr=%d cw=%d out=%d batch=%s usd=%.6f",
                  tier, model_id, u.input_tokens, u.cache_read_tokens,
                  u.cache_creation_tokens, u.output_tokens, batch, call.cost)
    except economics.UnknownModelPricing:
        _log.warning("extract.cost tier=%s model=%s in=%d cr=%d cw=%d out=%d batch=%s "
                     "usd=UNPRICED", tier, model_id, u.input_tokens, u.cache_read_tokens,
                     u.cache_creation_tokens, u.output_tokens, batch)
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

#: The compliance classes a doc-class call may answer; anything else is
#: `NOT_A_DOCUMENT`.
_DOC_CLASSES = ("DoC", "EC", "ISO", "IFU", "SPP")

#: The doc-class prompt for a file with no text layer: the text prompt,
#: unchanged and first, then a short note that the pages come as images. Same
#: classes, same closed answer schema.
DOC_CLASS_IMAGE_SYSTEM = DOC_CLASS_SYSTEM.rstrip("\n") + "\n\n" + _load_prompt("doc_class_image.md")
DOC_CLASS_IMAGE_LABEL = "doc-class-image"
DOC_CLASS_MAX_TOKENS = 256


def _image_block(doc, page_index: int) -> dict:
    """One page rendered for an image read (150 dpi PNG, as `_vision_content`)."""
    png = pdfutil.rasterize_page(doc, page_index, dpi=150)
    return {"type": "image", "source": {
        "type": "base64", "media_type": "image/png",
        "data": base64.standard_b64encode(png).decode("ascii")}}


def _classify_image_request(read: Read, doc, filename: str) -> dict:
    """The messages.create() body for the scan doc-class call: the file name
    and pages 1..`DOC_CLASS_PAGES` as images. Shared by the sync call and the
    batch client so both send the same request."""
    n = min(doc.page_count, DOC_CLASS_PAGES)
    content = [{"type": "text",
                "text": f"File name: {filename}\n\nThe first {n} page(s) follow as images."}]
    content += [_image_block(doc, i) for i in range(n)]
    params = {
        "model": read.model,
        "max_tokens": DOC_CLASS_MAX_TOKENS,
        # A plain string, as `classify` sends: the doc-class prompt stays
        # uncached (Task 6, about $0.0001 a call to gain), so not `_system`.
        "system": DOC_CLASS_IMAGE_SYSTEM,
        "messages": [{"role": "user", "content": content}],
        "output_config": {"format": {"type": "json_schema", "schema": DOC_CLASS_SCHEMA}},
    }
    if read.thinking is not None:
        params["thinking"] = dict(read.thinking)
    return params


def _classify_answer(message, label: str) -> tuple[str, str]:
    """`(doc_class, quote)` from a doc-class response.

    A response cut at `max_tokens` RAISES rather than reading as a refusal:
    its JSON is truncated, and a truncated answer read as
    `not-a-compliance-document` drops the file for good, with nothing to say a
    model never finished (Task 1 review, 2026-10-12). Raising fails the job,
    which retries and then lands in dead jobs, where a person sees it. Any
    other answer outside the five classes is a refusal, as before."""
    if getattr(message, "stop_reason", None) == "max_tokens":
        raise RuntimeError(f"{label}: the answer was cut at max_tokens; refusing to read "
                           f"a truncated answer as {NOT_A_DOCUMENT}")
    payload = _response_json(message)
    answer = payload.get("doc_class")
    quote = str(payload.get("quote") or "")
    if answer not in _DOC_CLASSES:
        return NOT_A_DOCUMENT, quote
    return answer, quote

class AnthropicLlm:
    """LLM tier for the ladder: `extract(tier, doc, filename, missing)`.

    `client` is an anthropic.Anthropic (or a stub in tests); `models` exposes
    the model ids `READ_STEPS` names (config.models: `.t1` / `.t2`, and
    `.vision_first` / `.second_read` for the ladder's newer steps).
    """

    def __init__(self, client, models, max_tokens: int = DEFAULT_MAX_TOKENS):
        self.client = client
        self.models = models
        self.max_tokens = max_tokens
        self.usage_log: list[CallCost] = []  # priced calls this instance made
        # Cites `_cited_pages` nulled across this instance's reads (an image
        # read with an explicit page list citing an image it was not sent).
        # The extract handler reports a non-zero total on the job result.
        self.cited_pages_nulled = 0

    def extract(self, tier: str, doc, filename: str, missing: list[str],
                hints: str | None = None, **read_options) -> dict:
        """One labelled read. `tier` is the step's label (`READ_STEPS`);
        `read_options` are `resolve_read`'s (kind and model for a label with no
        row, thinking, pages). Callers holding the injected protocol object pass
        them only when set, as `tiers.run_extraction` does with `hints`: every
        stub predates them."""
        read = resolve_read(tier, self.models, **read_options)
        params = _request(read, doc, missing, self.max_tokens, hints)
        resp = self.client.messages.create(**params)
        _capture(self.usage_log, read.label, read.model, resp, batch=False)
        parsed = _fields_from_json(_response_json(resp), read.evidence_tier, read.model)
        out = {f: parsed[f] for f in missing if f in parsed}
        nulled = _cited_pages(out, read)
        if nulled:
            self.cited_pages_nulled += nulled
            _log.warning("extract.cited_page_outside_images label=%s pages=%s nulled=%d",
                         read.label, list(read.pages), nulled)
        return out

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
        read = resolve_read(DOC_CLASS_LABEL, self.models)
        params = {
            "model": read.model,
            "max_tokens": 256,
            "system": DOC_CLASS_SYSTEM,
            "messages": [{"role": "user", "content":
                          f"File name: {filename}\n\n{text}"}],
            "output_config": {"format": {"type": "json_schema",
                                         "schema": DOC_CLASS_SCHEMA}},
        }
        resp = self.client.messages.create(**params)
        # Its own label: logged as `T1` it collided with the T1 read of the same
        # document on the ledger's (content_hash, tier, extract_rev) key, and
        # `ON CONFLICT DO NOTHING` dropped the second row.
        _capture(self.usage_log, read.label, read.model, resp, batch=False)
        payload = _response_json(resp)
        answer = payload.get("doc_class")
        if answer not in ("DoC", "EC", "ISO", "IFU", "SPP"):
            return NOT_A_DOCUMENT, str(payload.get("quote") or "")
        return answer, str(payload.get("quote") or "")

    def classify_image(self, doc, filename: str) -> tuple[str, str]:
        """`classify` for a file with no text page (`tiers.run_scan_ladder`):
        the same question asked of the page 1-2 images, by
        `models.vision_first` under its own label `doc-class-image`.

        Until 2026-10-12 a scan T0 could not type went to `classify`, found no
        text and was refused without a call: 281 production scans were never
        read. A file with no pages at all has nothing to show and is still
        refused without one. A response cut at `max_tokens` raises
        (`_classify_answer`) rather than reading as a refusal."""
        if doc.page_count == 0:
            return NOT_A_DOCUMENT, ""
        read = resolve_read(DOC_CLASS_IMAGE_LABEL, self.models)
        resp = self.client.messages.create(**_classify_image_request(read, doc, filename))
        _capture(self.usage_log, read.label, read.model, resp, batch=False)
        return _classify_answer(resp, read.label)


class AnthropicBatchClient:
    """Batch API transport for the tier ladder (C9). Implements the interface
    `app.extract.batching.advance_batch` drives: `submit(requests) -> batch_id`,
    `done(batch_id) -> bool`, `results(batch_id) -> {field: evidence}`. The
    handler builds requests with `build_requests` and hands them to
    `advance_batch`; `batch_ref` (keyed by content_hash+tier) is the crash-safe
    state. The client keeps only a per-instance note of the reads it built
    (`_reads`), which nothing depends on surviving a restart. That is also why
    a batch read takes no explicit page list (`build_requests`).

    Batch-of-1 per (content_hash, tier): the batch_ref PK is (content_hash,
    tier), so each doc+tier is its own batch. `tier` is the read's label
    (`READ_STEPS`), so two reads of one kind never share a batch_ref row. Bulk
    cross-doc batching (the sweep economy) is a later concern (backfill.scan);
    the transport is identical.
    """

    def __init__(self, client, models, max_tokens: int = DEFAULT_MAX_TOKENS):
        self.client = client
        self.models = models
        self.max_tokens = max_tokens
        self.usage_log: list[CallCost] = []  # priced calls, populated by results()
        # custom_id -> the Read its request was built from. The handler builds
        # the requests on every poll before asking for results, so on the poll
        # that reads them this holds the model actually sent (a label with no
        # READ_STEPS row names its own); a fresh instance falls back to the
        # label's row.
        self._reads: dict[str, Read] = {}

    def build_requests(self, content_hash: str, tier: str, doc, missing: list[str],
                       hints: str | None = None, **read_options) -> list[dict]:
        read = resolve_read(tier, self.models, **read_options)
        # No explicit page list in batch mode until `batch_ref` stores the list
        # that was sent. The cited page comes back as an index into the images,
        # and `results()` runs on a later poll: it could only translate that
        # index by recomputing the list, and a list that changed in between (a
        # deploy, a different earlier answer) would turn every cite into the
        # wrong document page without a sound. docs/dev/limits.md.
        if read.pages is not None:
            raise ValueError(f"read {read.label!r}: a batch read takes no page list until "
                             f"batch_ref stores the pages sent")
        custom_id = _custom_id(content_hash, read.label)
        self._reads[custom_id] = read
        return [{
            "custom_id": custom_id,
            "params": _request(read, doc, missing, self.max_tokens, hints),
        }]

    def build_classify_requests(self, content_hash: str, doc, filename: str) -> list[dict]:
        """The scan doc-class call as a batch-of-1 under `doc-class-image`, the
        same request `AnthropicLlm.classify_image` sends. `results` answers it
        with `{"doc_class": ..., "quote": ...}`."""
        read = resolve_read(DOC_CLASS_IMAGE_LABEL, self.models)
        custom_id = _custom_id(content_hash, read.label)
        self._reads[custom_id] = read
        return [{"custom_id": custom_id,
                 "params": _classify_image_request(read, doc, filename)}]

    def submit(self, requests) -> str:
        return self.client.messages.batches.create(requests=requests).id

    def done(self, batch_id: str) -> bool:
        return self.client.messages.batches.retrieve(batch_id).processing_status == "ended"

    def results(self, batch_id: str) -> dict:
        out: dict = {}
        for item in self.client.messages.batches.results(batch_id):
            if getattr(item.result, "type", None) != "succeeded":
                continue
            read = (self._reads.get(item.custom_id)
                    or resolve_read(_tier_of(item.custom_id), self.models))
            _capture(self.usage_log, read.label, read.model, item.result.message, batch=True)
            if read.label in _CLASSIFY_LABELS:
                # A doc-class result is an answer, not fields (batch-of-1, so
                # these keys never meet a field read's in one dict).
                answer, quote = _classify_answer(item.result.message, read.label)
                out.update({"doc_class": answer, "quote": quote})
                continue
            # No page translation: a batch read is always pages 1..4 in order
            # (`build_requests` refuses a page list), so the cited image index
            # is the document page, as for today's sync T2.
            out.update(_fields_from_json(
                _response_json(item.result.message), read.evidence_tier, read.model))
        return out
