"""Tier ladder: T0 -> T1 -> T2 with per-field escalation (handbook 5).

Escalation is per field, never per document: a doc with a clean date but an
unreadable REF list escalates only the REF list. LLM tiers are injected (an
object with `extract(tier, doc, filename, missing) -> {field: evidence}`), so
the ladder is testable with a stub and the real Anthropic client drops in later.

Calibration (gap G2): extraction stores each field's RAW self-reported
confidence. `calibrate()` is the mechanism applied later at GATE time —
`effective = raw * factor(model_id, field)`, clamped to [0, 1], default factor
1.0. T0 hits (model_id None) are never calibrated. Factors come from S0.4 data;
this ships the identity default so the mechanism is in place and re-calibratable
without re-extraction.
"""

from __future__ import annotations

from psycopg.types.json import Json

from app.extract import economics
from app.extract import pdf as pdfutil
from app.extract import t0_templates as t0_keys
from app.extract.t0_templates import t0_extract

# The 11-field target schema, defined in a leaf module and re-exported here.
# `app.playbooks._parse` and the playbook editor route need these names and run
# in the slim web image, which has no PyMuPDF; importing them from this module
# pulled in `app.extract.pdf` and broke every playbook parse there (2026-08-27).
# `tiers.TARGET` stays the address the extractor, tools and tests already use.
from app.extract.target import TARGET

# Classification fields — always escalate when T0 is unsure.
# `manufacturer` is here rather than in _ESCALATE_ITEM_ID on purpose. The item-id
# fields are suppressed for a manufacturer-scope cert because it carries none by
# nature; identity is the ONE thing such a document does assert, and the C4
# binding flow cannot be actioned without it -- that suppression is why every
# mfr-binding task read "manufacturer unknown".
_ESCALATE_ALWAYS = ["type", "regulation", "validity_from", "validity_to",
                    "coverage_scope", "cert_number", "manufacturer"]

# Item-identifier fields — a DoC/EC covers a single item or a group, and we NEED at
# least one item REF or a UDI to link it to the catalogue, so we pursue these. The
# ONE exception is a manufacturer/catalogue-wide certificate (ISO 27001/13485,
# coverage_scope=manufacturer): it carries no item id by nature and routes to the
# manufacturer-binding flow (C4), so chasing one just burns a vision tier. Truncation
# from a huge REF list is handled in the prompt (bounded enumeration) + graceful JSON
# degrade; T0/pdfplumber is the full-list enumerator, the LLM the fallback.
_ESCALATE_ITEM_ID = ["ref_list", "basic_udi_di"]

# Certificate/validity fields — suppressed for an IFU, on the same "carries none
# by nature" argument that suppresses the item-id fields for a manufacturer-scope
# certificate. Instructions for use accompany a product; they are not issued by a
# notified body, they cite no certificate number and they carry no validity
# period. Asking a tier for them does not return nothing -- it returns something
# wrong, because a model asked to find a date on a document that has none will
# find the nearest date-shaped string on the page.
#
# Both STRAUMANN IFUs demonstrated it on the same day (2026-08-17), and neither
# failure was subtle:
#
#   * `navodilo za uporabo Straumann vsadki` -> validity_from "2025", read off
#     "(c) Institut Straumann AG, 2025. All rights reserved." -- a copyright
#     notice. Malformed, so it crashed GATE rather than landing.
#   * `navodila za uporabo varibase` -> validity_from "2026-04-26", read off
#     "701593/M/12 04/26" -- a document revision code. Well-formed, so it landed
#     in the registry at a score of 0.97 as doc 326 and nothing objected.
#
# The second is the dangerous one: a plausible date derived from a part number is
# invisible to every downstream guard, and validity_from drives supersession.
# Two documents, two fabricated dates, zero real ones.
_ESCALATE_CERTIFICATE = ["validity_from", "validity_to", "cert_number"]

#: Document types that carry no certificate and no validity period by nature.
#: A tuple, not a single value, because the same is true of any accompanying
#: literature we later learn to type.
_NO_CERTIFICATE_TYPES = ("IFU",)

# referenced_docs (low priority, not an item id) never forces escalation; it is
# still captured opportunistically by T0. `stated_class` is in the same
# position for a different reason: it is not needed to link, date or type a
# document at all -- it exists to cross-check BC's own class column -- so a
# document that does not print it costs nothing and buys nothing.
#
# validity_from JOINED the escalate set on 2026-08-13 (Denis's ruling). It was
# excluded as S0.4's biggest false-escalation driver, which was true while the
# rule said "the START of the validity PERIOD, not the issue date" — a value
# most declarations genuinely do not carry, so the tier was paid to find
# nothing. Under the new rule the issue or signing date IS the start, so the
# field is present on nearly every document and the call is worth making.
# Excluding it was also why the prompt fix alone changed nothing: T1 was never
# asked for the field, and 0 of 127 declarations in the registry carried a start
# date. T0's place-and-date fallback answers at 0.95 precisely so that a signed
# declaration still does NOT escalate for this field alone.

# Maximal escalate set (SCHEMA/doc references + tests). needs_escalation() applies
# the manufacturer-scope condition to the item-id fields.
ESCALATE = _ESCALATE_ALWAYS + _ESCALATE_ITEM_ID


def _below(fields: dict, f: str, threshold: float) -> bool:
    return f not in fields or fields[f].get("conf", 0.0) < threshold


def needs_escalation(fields: dict, threshold: float = 0.95) -> list[str]:
    """Escalate-worthy fields absent or below `threshold`. Item-identifier fields
    (ref_list, basic_udi_di) are pursued only when the doc is NOT confidently
    manufacturer-scope — a manufacturer/catalogue-wide cert (ISO 27001/13485) has no
    item id and uses the binding flow, so chasing one just wastes a vision call.
    Certificate and validity fields are pursued for every type EXCEPT one whose
    type is confidently one of `_NO_CERTIFICATE_TYPES` (an IFU), which carries
    neither by nature -- asking yields a fabricated date, not a blank.

    referenced_docs never escalates."""
    always = _ESCALATE_ALWAYS
    doc_type = fields.get("type") or {}
    # Confidently, or not at all. An uncertain type must keep chasing the fields,
    # exactly as an unknown coverage_scope below still pursues an item id: the
    # conservative direction is to spend a tier, never to skip a real expiry.
    if (doc_type.get("value") in _NO_CERTIFICATE_TYPES
            and doc_type.get("conf", 0.0) >= threshold):
        always = [f for f in always if f not in _ESCALATE_CERTIFICATE]
    missing = [f for f in always if _below(fields, f, threshold)]
    cov = (fields.get("coverage_scope") or {}).get("value")
    if cov != "manufacturer":   # unknown scope still pursues an id (conservative)
        missing += [f for f in _ESCALATE_ITEM_ID if _below(fields, f, threshold)]
    return missing


def _is_blank(evidence: dict | None) -> bool:
    """No evidence, or evidence carrying an empty value. Emptiness is tested by
    length, never by truthiness: `0` and `False` are answers, not blanks."""
    if evidence is None:
        return True
    value = evidence.get("value")
    if value is None:
        return True
    return isinstance(value, (str, list, tuple, dict, set)) and len(value) == 0


#: Fields whose value is an ENUMERATION of the document's own contents rather
#: than a judgement about it. For these, a shorter answer is a less complete
#: reading, never a correction -- see `merge`.
_ENUMERATED_FIELDS = frozenset({"ref_list"})


def _shrinks(incumbent: dict | None, challenger: dict) -> bool:
    """Whether `challenger` enumerates strictly FEWER items than `incumbent`.
    Non-list values never shrink: the guard must not touch scalar fields even if
    one is somehow routed here."""
    if incumbent is None:
        return False
    a, b = incumbent.get("value"), challenger.get("value")
    if not isinstance(a, list) or not isinstance(b, list):
        return False
    return len(b) < len(a)


def merge(base: dict, new: dict) -> dict:
    """Later tier wins for the fields it returned; earlier hits are preserved.

    Escalation fills gaps — it never erases. A later tier that returns *nothing*
    for a field an earlier tier already answered is reporting its own failure,
    not a correction, so the earlier hit stands. This is what makes T0 the REF
    enumerator and the LLM the fallback (see `_ESCALATE_ITEM_ID`): `_ref_column`
    parses GC's article table correctly, but `extract_ref_list` stamps a flat
    0.9 that can never clear the 0.95 threshold, so a successful T0 REF list is
    escalated every time — and a vision tier reading the page-2 "Artikelliste:
    Gemäß Anhang" field answers `[]` in good faith. Measured on the GC corpus,
    an unconditional `{**base, **new}` deleted 26 of 61 correct REF lists.

    A later tier that returns a *different, non-empty* value still wins: this is
    deliberately not confidence-aware, since T0's confidences are flat literals
    that `calibrate()` leaves uncalibrated by design.

    ONE exception, for `_ENUMERATED_FIELDS`: a later tier may not SHRINK a
    non-empty enumeration. The blank guard above closed the case where the LLM
    answered `[]`; this closes the case where it answers a truncated list. The
    prompt caps `ref_list` and says why -- "a deterministic parser enumerates the
    full list, you only need enough to identify the coverage" -- so an LLM
    ref_list is a SAMPLE by construction, and storing a sample over an
    enumeration is a silent data loss, not a correction. Measured on the GC
    corpus 2026-08-13: 1.502 codes stored against the 2.843 T0's stitched-table
    reads, costing 199 catalogue items of coverage.

    Longer still wins, whichever tier produced it. Under-capture is the failure
    mode of BOTH sides -- an unparsed table for T0, a prompt cap for the LLM --
    so "more codes" is the better answer regardless of who found them, and the
    KOMET text-column gap (where T0 under-reads and the LLM sees more) keeps
    working. Lists are never unioned: evidence carries one verbatim, page and
    tier per field, and a spliced value would have no single trail to cite.
    """
    out = dict(base)
    for field, evidence in new.items():
        if _is_blank(evidence) and not _is_blank(base.get(field)):
            continue
        if field in _ENUMERATED_FIELDS and _shrinks(base.get(field), evidence):
            continue
        out[field] = evidence
    return out


#: The most REF codes either prompt asks a tier to return. Defined HERE and
#: rendered into `prompts/*.md` by `app.extract.llm` (the `{{REF_LIST_CAP}}`
#: placeholder), so the number a tier was asked for and the number this module
#: tests a stored list against are the same number and cannot drift.
#:
#: 150 was measured, not guessed: on the GC corpus the REF-list length
#: distribution is median 4 with a top ten of 53/56/67/67/84/130/133/150/150/1121,
#: so the cap covers 124 of the 125 documents that yield a list at all. The
#: 1.121-code outlier is beyond any prompt cap (~14KB of JSON), which is exactly
#: why a list arriving AT the cap has to be treated as a suspect.
REF_LIST_PROMPT_CAP = 150

#: Tiers the prompt cap applies to. T0 enumerates the document deterministically
#: (pdfplumber tables, playbook templates) and T3 is a human: a list of exactly
#: `REF_LIST_PROMPT_CAP` codes from either of those is a real count, not a
#: truncation, and flagging it would be a false alarm on our own best reading.
_PROMPT_CAPPED_TIERS = frozenset({"T1", "T2"})

#: Informational (gate.py `INFORMATIONAL_FLAGS`): it describes the SHAPE of the
#: answer, not a defect in the document, so it must never gate.
REF_TRUNCATION_FLAG = "ref-list-possibly-truncated"

#: `type='ISO'` on a document whose evidence names no ISO standard number.
#:
#: Extends the 2026-08-20 ruling -- `iso 13485` is the ONLY ISO signal
#: (`t0_templates._TYPE_MARKERS`) -- from T0, where it was enforced, to whatever
#: tier actually produced the type. It was needed: doc 967 is an MDR Annex IX
#: quality-management certificate that T1 typed `ISO` on 2026-09-03, because the
#: T1 prompt's own preamble still equates ISO with "Quality Management System
#: certificates" -- the exact conflation removed from T0 after doc 420 reached
#: `production` as ISO, and never removed from the prompt.
#:
#: NOT `type='ISO'` together with a regulation, which is the rule that suggests
#: itself and does not hold: a genuine ISO 13485 certificate may name MDR in its
#: scope ("quality system for devices under Regulation (EU) 2017/745"), and
#: `regulation` is defined as "cites anywhere", so such a rule would fire on
#: correct documents. A real ISO certificate always names its standard, which is
#: what makes this test safe in both directions.
ISO_WITHOUT_STANDARD_FLAG = "iso-without-standard-number"

#: A labelled date T0 refused because content sat between the label and the
#: value, and which nothing else in T0 then read. Informational (Denis,
#: 2026-09-03): it appears on the document and in review, caps nothing and
#: blocks nothing.
#:
#: Not the same shape as the flag above, and the difference is the whole reason
#: it took a second pass to build. `iso-without-standard-number` is computed
#: HERE from the stored fields, because a wrong type is still a stored value. A
#: refused date pairing stores nothing -- it is a non-event -- so the extractor
#: has to record it at the time. `t0_templates.DATE_LABEL_REFUSED_KEY` is that
#: record; this function only reads it.
DATE_LABEL_NOT_ADJACENT_FLAG = "date-label-not-adjacent"

#: What counts as naming the standard. Bare "ISO" is deliberately not enough --
#: it is the word the conflation turns on.
_ISO_STANDARD_NUMBERS = ("13485", "9001")


def integrity_flags(fields: dict) -> list[str]:
    """Flags about how an extraction ARRIVED, computed from the stored fields.

    Today there is one: an LLM `ref_list` of exactly `REF_LIST_PROMPT_CAP`
    entries. The prompt tells T1/T2 to return at most that many and to say so in
    `verbatim` when the document lists more -- but the self-report is not usable
    as the signal: document 258 said "total of 44 codes listed, first 40
    returned" while returning 46 against a true 53. The length is deterministic
    and needs nobody's honesty.

    It REPORTS, it never prunes. Nothing here reads or rewrites the stored value:
    a suspected truncation is context for a reviewer, and a partial list still
    links every item it does name. Shortening or rejecting the list would throw
    away the coverage the flag exists to protect (Denis, 2026-08-13).
    """
    flags: list[str] = []

    # `type='ISO'` with no ISO standard number anywhere in the evidence. Reports,
    # never rewrites: the document keeps the type it was given, and a human sees
    # that nothing supports it. See ISO_WITHOUT_STANDARD_FLAG for why this is not
    # a type-versus-regulation check.
    type_ev = fields.get("type") or {}
    if type_ev.get("value") == "ISO":
        seen = " ".join(
            str((ev or {}).get("verbatim") or "") for ev in fields.values()
            if isinstance(ev, dict)
        )
        if not any(n in seen for n in _ISO_STANDARD_NUMBERS):
            flags.append(ISO_WITHOUT_STANDARD_FLAG)

    # Recorded by `extract_dates`, never computed here -- see the constant.
    # Already narrowed to the fields T0 lost: a refusal a later label satisfied
    # never reaches this key.
    if fields.get(t0_keys.DATE_LABEL_REFUSED_KEY):
        flags.append(DATE_LABEL_NOT_ADJACENT_FLAG)

    ev = fields.get("ref_list")
    if isinstance(ev, dict):
        value = ev.get("value")
        if (isinstance(value, list)
                and len(value) == REF_LIST_PROMPT_CAP
                and ev.get("tier") in _PROMPT_CAPPED_TIERS):
            flags.append(REF_TRUNCATION_FLAG)
    return flags


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def calibrate(raw_conf: float, model_id: str | None, field: str, calib_map: dict) -> float:
    """Effective confidence = raw x calibration factor (gap G2). T0 (model_id
    None) is uncalibrated. Factor lookup: per-model field factor, then the
    model's '*' default, then 1.0."""
    if not model_id:
        return _clamp(raw_conf)
    per_model = calib_map.get(model_id, {})
    factor = per_model.get(field, per_model.get("*", 1.0))
    return _clamp(raw_conf * factor)


#: Confidence carried by a type the doc-class call decided. Below the 0.95
#: escalation threshold on purpose: it is a real read of the header, but the
#: ladder should still let T2 look at a scan that disagrees.
T1_DOC_CLASS_CONF = 0.90

#: The doc-class call's "this is not one of ours" answer. Lives here, not in
#: `llm.py`, because `llm.py` already imports from this module -- putting it the
#: other way round is a circular import. It is also the right home: this module
#: owns the doc_class vocabulary the ladder branches on.
#:
#: Distinct from `business-doc` / `msds`, which name what a file IS. This one
#: says only what it is not, and unlike those two it suppresses the
#: `validate.doc` emit in `app/handlers/extract.py` -- nothing downstream should
#: see a candidate for it.
NOT_A_DOCUMENT = "not-a-compliance-document"


def run_extraction(doc, filename: str, llm, threshold: float = 0.95,
                   result=None, companion_url: str | None = None,
                   hints: str | None = None) -> tuple[str, dict, list[str]]:
    """T0, then escalate absent/low fields to T1, then T2 (T2 also forced for
    scans). Business/MSDS docs short-circuit with no LLM. Returns
    (doc_class, merged_fields, tiers_used).

    `result` is the caller's `app.results.Result`, passed to T0 so a playbook
    template that matched but parsed nothing is counted at the point it happens
    -- before any tier can mask it by answering the same field.

    `hints` is forwarded to every `llm.extract` call, but only as a kwarg when
    it is not None -- the injected `llm` is a protocol object (module
    docstring: `extract(tier, doc, filename, missing) -> {field: evidence}`),
    and every stub implementing it predates this parameter. Passing `hints=`
    unconditionally would break every one of them with a TypeError even when
    nobody asked for a hint; omitting the kwarg entirely when there is nothing
    to hint keeps every existing caller working unchanged, exactly as today."""
    doc_class, fields = t0_extract(doc, filename, result=result,
                                   companion_url=companion_url)
    used = ["T0"]
    if doc_class in ("business-doc", "msds"):
        return doc_class, fields, used

    # T0 declined to type this file. Ask, rather than guess.
    #
    # `unknown` used to proceed into the full ladder and, worse, be promoted to
    # `compliance-doc` by any type marker found anywhere in the text -- which is
    # how four web pages became registry documents on 2026-09-02. T0 now only
    # classifies from its two-page header window (88% of the real corpus), and
    # everything else comes here for a real answer, including
    # `not-a-compliance-document`, which is terminal.
    #
    # `classify` is optional on the injected llm: the protocol is
    # `extract(tier, doc, filename, missing)` and every stub predates this.
    # Absent it, behaviour is exactly what it was before -- an `unknown`
    # document extracts normally.
    if doc_class == "unknown":
        classifier = getattr(llm, "classify", None)
        if classifier is not None:
            answer, quote = classifier(doc, filename)
            used.append("T1")
            if answer == NOT_A_DOCUMENT:
                return NOT_A_DOCUMENT, {}, used
            doc_class = "compliance-doc"
            # The classifier's answer IS evidence about the type, and it is a
            # better one than a filename guess. It never overwrites a T0 read.
            if "type" not in fields:
                fields["type"] = {"value": answer, "conf": T1_DOC_CLASS_CONF,
                                  "tier": "T1", "verbatim": quote, "page": 1}

    llm_kwargs = {"hints": hints} if hints is not None else {}

    missing = needs_escalation(fields, threshold)
    if missing:
        fields = merge(fields, llm.extract("T1", doc, filename, missing, **llm_kwargs))
        used.append("T1")
        missing = needs_escalation(fields, threshold)

    if missing or pdfutil.is_scan(doc):
        target_fields = missing or needs_escalation(fields, threshold)
        if target_fields:   # don't pay for a vision call with nothing to extract
            fields = merge(fields, llm.extract("T2", doc, filename, target_fields, **llm_kwargs))
            used.append("T2")

    return doc_class, fields, used


def next_extract_rev(conn, content_hash: str) -> int:
    """Next extract_rev for a content_hash (1 for the first extraction)."""
    return conn.execute(
        "SELECT COALESCE(MAX(extract_rev), 0) + 1 AS rev "
        "FROM extraction_attempt WHERE content_hash = %s",
        (content_hash,),
    ).fetchone()["rev"]


def write_extraction_attempt(
    conn, content_hash: str, tiers_used: list[str], fields: dict,
    extract_rev: int = 1, model_id: str | None = None,
    playbook_slug: str | None = None, playbook_rev: int | None = None,
) -> int:
    """Insert one extraction_attempt row. Per-field tier/model_id/evidence live
    inside `fields`; the row's `tier` summarises the ladder ('T0+T1').

    `playbook_slug`/`playbook_rev` name the authored rules that shaped this read
    (migration 028) -- NULL when no playbook claimed the document, which is the
    common case. Without them a hint-steered extraction is not reproducible from
    its evidence, which invariant 2 requires."""
    return conn.execute(
        "INSERT INTO extraction_attempt (content_hash, tier, model_id, fields, "
        "extract_rev, playbook_slug, playbook_rev) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (content_hash, "+".join(tiers_used), model_id, Json(fields), extract_rev,
         playbook_slug, playbook_rev),
    ).fetchone()["id"]


def write_extraction_cost(conn, content_hash: str, extract_rev: int, call) -> None:
    """Persist one LLM call's measured cost (the economics ledger, migration 010).
    `call` is an economics.CallCost. Idempotent per (content_hash, tier,
    extract_rev) so a batch retry that re-fetches results is a no-op. cost_usd is
    null when the model is unpriced -- tokens are recorded regardless."""
    try:
        cost = call.cost
    except economics.UnknownModelPricing:
        cost = None
    u = call.usage
    conn.execute(
        "INSERT INTO extraction_cost (content_hash, tier, extract_rev, model_id, batch, "
        "input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, cost_usd) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (content_hash, tier, extract_rev) DO NOTHING",
        (content_hash, call.tier, extract_rev, call.model_id, call.batch,
         u.input_tokens, u.output_tokens, u.cache_read_tokens, u.cache_creation_tokens, cost),
    )
