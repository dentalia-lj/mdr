"""Tier ladder: T0 -> T1 -> T2 with per-field escalation (handbook 5).

Escalation is per field, never per document: a doc with a clean date but an
unreadable REF list escalates only the REF list. LLM tiers are injected (an
object with `extract(tier, doc, filename, missing) -> {field: evidence}`), so
the ladder is testable with a stub and the real Anthropic client drops in later.

Since 2026-10-12 (PRD v3 section 5, amended; extraction-ladder plan Tasks 3
and 4) a field leaves a rung when it is SETTLED, not when a model says it is
sure: the evidence checks (`checks`) prove it, two reads agree, or the null
rule holds. A text document takes `run_text_ladder` (T-3: T1, a cheap image
read, Sonnet only for what those leave open); a document with no text page
takes `run_scan_ladder` (S-2: a doc-class call on the page images, two cheap
image reads, Sonnet only for what they leave open).

Calibration (gap G2): extraction stores each field's RAW self-reported
confidence. `calibrate()` is the mechanism applied later at GATE time —
`effective = raw * factor(model_id, field)`, clamped to [0, 1], default factor
1.0. T0 hits (model_id None) are never calibrated. Factors come from S0.4 data;
this ships the identity default so the mechanism is in place and re-calibratable
without re-extraction.
"""

from __future__ import annotations

import re

from psycopg.types.json import Json

from app.config import Extract as _ExtractDefaults
from app.config import GateThresholds as _GateDefaults
from app.extract import checks, economics
from app.extract import pdf as pdfutil
from app.extract import t0_templates as t0_keys
from app.extract.t0_templates import derive_coverage_scope, t0_extract

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


#: How strong each read step is, for `merge` (rulings 2026-10-12: "stronger
#: read prevails"): the Sonnet image read over a cheap image read over the
#: text read over a T0 guess. A second read ranks just under its first: it
#: exists to confirm, so it fills a gap the first left and never replaces what
#: the first read. The doc-class answers rank lowest of the paid reads: one
#: word off the header, which any field read of the same document outranks.
#: Keyed by the read label (`llm.READ_STEPS`), which a scan read carries as
#: `step` on its evidence (`_stamp`).
_STEP_RANK = {
    "doc-class": 1, "doc-class-image": 1, "T1-second": 1,
    "T1": 2,
    "T2-second": 3,
    "T2-first": 4,
    "T2": 5,
}

#: Evidence with no `step` (the text ladder, unchanged; every stored
#: extraction before 2026-10-12) is ranked by its tier. A `T2` with no step is
#: the Sonnet read: until the scan ladder there was no other image read.
_TIER_RANK = {"T1": _STEP_RANK["T1"], "T2": _STEP_RANK["T2"]}

#: T0 below the escalation threshold is a guess any read may correct; at or
#: above it, T0 outranks every read and is never overwritten by one.
_T0_GUESS, _T0_SETTLED = 0, 6


def _rank(evidence: dict, threshold: float = 0.95) -> int | None:
    """Strength of one evidence dict, or None when it cannot be placed (a tier
    no read produces), in which case `merge` falls back to later-wins."""
    step = evidence.get("step")
    if step in _STEP_RANK:
        return _STEP_RANK[step]
    tier = evidence.get("tier")
    if tier == "T0":
        return _T0_SETTLED if (evidence.get("conf") or 0.0) >= threshold else _T0_GUESS
    return _TIER_RANK.get(tier)


def _weaker(challenger: dict, incumbent: dict | None) -> bool:
    """Whether `challenger` is a weaker read than a non-blank `incumbent`."""
    if not isinstance(incumbent, dict) or _is_blank(incumbent):
        return False
    a, b = _rank(challenger), _rank(incumbent)
    return a is not None and b is not None and a < b


#: The doc-class calls' labels (`llm.READ_STEPS`). Their answer is one word off
#: the header with a fixed confidence and no model id, not a field read.
_DOC_CLASS_STEPS = frozenset({"doc-class", "doc-class-image"})


def _is_read(evidence) -> bool:
    """Evidence a model READ for a field: tier T1 or T2, a model id, and not
    the doc-class call's answer. Not T0 (a template or a file-name guess), not
    a person. Only a read can agree with another (`_agree`), and GATE combines
    only on a row with a model id (`gate._agreed_columns`)."""
    return (isinstance(evidence, dict) and evidence.get("tier") in ("T1", "T2")
            and bool(evidence.get("model_id")) and evidence.get("step") not in _DOC_CLASS_STEPS)


def _conf(evidence, key: str = "conf") -> float:
    """A read's own number (or another key's), 0.0 when it is not a number."""
    try:
        return float((evidence or {}).get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _agree(field: str, a, b) -> bool:
    """Two reads gave the same well-formed value (`checks.agree` with no
    per-read minimum: whether an agreement COUNTS is GATE's floor)."""
    return _is_read(a) and _is_read(b) and checks.agree(field, a, b, 0.0)


def _stores_over(field: str, challenger: dict, incumbent: dict, ctx) -> bool:
    """Of two reads that agree (`_agree`), whether the challenger's evidence
    is stored in place of the incumbent's (Denis, 2026-10-13, ruling D1):
    a read the checks verify first (`checks.settled`: its quote on its cited
    page, its value in the text), then the surer one, the held one on a tie.
    With no text to check against (`ctx` None: a scan, or `merge` called
    outside a ladder) only the surer-read rule applies."""
    if ctx is not None:
        mine, held = checks.settled(field, challenger, ctx), checks.settled(field, incumbent, ctx)
        if mine != held:
            return mine
    return _conf(challenger) > _conf(incumbent)


def merge(base: dict, new: dict, *, by_strength: bool = True, ctx=None) -> dict:
    """The stronger read wins for the fields it returned; earlier hits are
    preserved.

    **Stronger, not later** (rulings 2026-10-12): Sonnet's image read over a
    cheap image read over the text read over T0 below 0.95 (`_STEP_RANK`). A
    weaker read never replaces a stronger one's value; on equal strength the
    later read wins, as every read did before. T0 at or above 0.95 is never
    overwritten. On the text ladder (T0, T1, Sonnet's T2, in that order) this
    is exactly the old later-wins rule, because each read there is stronger
    than the one before; it decides only on a scan, whose second cheap read
    comes after the first and ranks below it.

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
    working. So for an enumeration the length decides before the strength
    does: a longer list wins from any read, and only two lists of one length
    are settled by which read is stronger. The one exception is T0 at or above
    0.95, which no read overwrites. Lists are never unioned: evidence carries
    one verbatim, page and tier per field, and a spliced value would have no
    single trail to cite.

    A key whose value is not evidence (`_date_label_refused`, `_reads`,
    `_pages_unread`: records about the extraction, not fields) is copied as
    given.

    **Two reads that agree** (plan Task 5b, review finding 9; Denis's ruling
    D1, 2026-10-13): when the new read gives the same well-formed value as the
    read the field holds (`_agree`; T0 and the doc-class answer are not
    reads), the stored evidence (value, quote, page, model, confidence) is a
    read the checks verify against `ctx`, the text the ladder read, and the
    surer of those, the one held on a tie; with neither verified, or no `ctx`
    (a scan), simply the surer (`_stores_over`). The other becomes the
    agreeing read GATE combines with it (`_stamp_agreement`). Strength
    decides only between different values: an image read agreeing with T1 at
    0.5 used to replace T1's 0.9 by strength alone, whether or not its quote
    was on the page, and `scope_stated` and the supersede guard read that
    number.

    `by_strength=False` is the rule as it stood before 2026-10-12 (later wins,
    with the blank and shrink guards), for `app/repair_ref_list.py`: it replays
    a fresh T0 under STORED answers to restore what a blank answer erased, and
    by strength a T0 that today reads a field at 0.95 would also replace the
    stored read of it, which that repair does not do.
    """
    out = dict(base)
    for field, evidence in new.items():
        if not isinstance(evidence, dict):
            out[field] = evidence
            continue
        incumbent = base.get(field)
        if _is_blank(evidence) and not _is_blank(incumbent):
            continue
        if by_strength and _agree(field, incumbent, evidence):
            if _stores_over(field, evidence, incumbent, ctx):
                out[field] = evidence
            continue
        if field in _ENUMERATED_FIELDS and isinstance(incumbent, dict):
            if _shrinks(incumbent, evidence):
                continue
            if _shrinks(evidence, incumbent) and (
                    not by_strength or _rank(incumbent) != _T0_SETTLED):
                out[field] = evidence     # longer wins, whichever read found it
                continue
        if by_strength and _weaker(evidence, incumbent):
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

#: Part of the document was not read, so a field left empty there is unread,
#: not absent (PRD v3 section 5, added 2026-10-12). Informational (Denis,
#: 2026-10-12): it caps nothing and blocks nothing. Raised for a scan longer
#: than the pages its image reads see (`MAX_VISION_PAGES`): a field both cheap
#: image reads left empty is not settled and not sent to Sonnet, which sees the
#: same pages. And, since Task 4, for a text document whose text ran past the
#: budget or whose scanned pages no image read rendered: a field Sonnet also
#: answered empty. The same shape as `date-label-not-adjacent`: the ladder
#: records the fields under `PAGES_UNREAD_KEY` at the time, because an unread
#: field stores nothing to compute the flag from later.
PAGES_UNREAD_FLAG = "pages-unread"
PAGES_UNREAD_KEY = "_pages_unread"

#: Every read the scan ladder made, kept on the extraction attempt (PRD v3
#: section 5: "Every read is kept on the extraction attempt"): a list, in read
#: order, of {"step", "model_id", "fields": {field: {value, conf, page}}}. A
#: list because jsonb sorts an object's keys, which would lose the order. Not
#: evidence: GATE's evidence writer skips any key whose value is not a dict.
READS_KEY = "_reads"

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

    # Recorded by the scan ladder, never computed here: see the constant.
    if fields.get(PAGES_UNREAD_KEY):
        flags.append(PAGES_UNREAD_FLAG)

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


#: The scan ladder's two thresholds. The defaults are the config dataclasses'
#: own, so the numbers live in one place; the extract handler passes the
#: configured `extract.image_settle` and `gate.agreement_min`.
IMAGE_SETTLE = _ExtractDefaults.image_settle
AGREEMENT_MIN = _GateDefaults.agreement_min

#: The ladders' steps (`llm.READ_STEPS` labels). The text ladder sends
#: `doc-class`, `T1`, `T2-first`, `T2` and `T1-second`; the scan ladder
#: `doc-class-image`, `T2-first`, `T2-second` and `T2`.
DOC_CLASS_TEXT, DOC_CLASS_IMAGE = "doc-class", "doc-class-image"
TEXT_READ, SECOND_TEXT = "T1", "T1-second"
FIRST_IMAGE, SECOND_IMAGE, SONNET_IMAGE = "T2-first", "T2-second", "T2"

#: GATE's required fields (`gate.REQUIRED_FIELDS`; a test holds the two
#: equal, since this module cannot import the handler): the fields whose
#: evidence carries the agreement keys, and the ones a second read confirms.
GATE_FIELDS = ("type", "regulation", "coverage_scope")

#: The evidence contract GATE reads (plan Task 5, "Split 2026-10-12";
#: `gate._confidences`), on a gate field's evidence: `conf` stays the stored
#: read's own raw number; `agreed_model_id` / `agreed_conf` name another read
#: that gave the same well-formed value and its own raw number, set whenever
#: the values agree (the 0.70 floor is GATE's); `proven: "citation"` marks a
#: `regulation` its one printed citation proves (`checks.citation_proven`).
AGREED_MODEL_KEY, AGREED_CONF_KEY = "agreed_model_id", "agreed_conf"
PROVEN_KEY, CITATION = "proven", "citation"

#: A `coverage_scope` type to scope derived (`_scope_from_type`) says so:
#: `follows: "type"`. GATE leaves such a scope out of the score, since the
#: type is already in it, combined when a second read confirmed it (Denis,
#: ruling D3, 2026-10-13). The scope keeps the type's own number, which is
#: what `scope_stated` reads.
FOLLOWS_KEY, FOLLOWS_TYPE = "follows", "type"

#: Types whose coverage scope follows from the type alone
#: (`t0_templates.derive_coverage_scope`: EC and ISO are manufacturer-wide, an
#: IFU is for a product or a family).
_SCOPE_FROM_TYPE = ("EC", "ISO", "IFU")


class Deferred(Exception):
    """A batch read has not finished: the batch transport stops the ladder (text
    or scan) at this step and defers its job (`app/handlers/extract.py`). The
    sync transport never raises it."""

    def __init__(self, label: str, state: str):
        super().__init__(f"{label}: {state}")
        self.label, self.state = label, state


def run_extraction(doc, filename: str, llm, threshold: float = 0.95,
                   result=None, companion_url: str | None = None,
                   hints: str | None = None, image_settle: float = IMAGE_SETTLE,
                   agreement_min: float = AGREEMENT_MIN) -> tuple[str, dict, list[str]]:
    """T0, then the text ladder (`run_text_ladder`) or, for a document with no
    text page at all, the scan ladder (`run_scan_ladder`). Business/MSDS docs
    short-circuit with no LLM. Returns (doc_class, merged_fields, tiers_used).

    The two branches split on `pdf.has_text_page`: a page with a text layer,
    an OCR layer over a scan included, makes it a text document (plan
    2026-10-12, "a scan is a document with no text page at all"), so a mixed
    PDF takes the text branch. `image_settle` and `agreement_min` are the scan
    ladder's.

    `result` is the caller's `app.results.Result`, passed to T0 so a playbook
    template that matched but parsed nothing is counted at the point it happens
    -- before any tier can mask it by answering the same field.

    `hints` is forwarded to every `llm.extract` call, but only as a kwarg when
    it is not None -- the injected `llm` is a protocol object (module
    docstring: `extract(tier, doc, filename, missing) -> {field: evidence}`),
    and every stub implementing it predates this parameter. Passing `hints=`
    unconditionally would break every one of them with a TypeError even when
    nobody asked for a hint; omitting the kwarg entirely when there is nothing
    to hint keeps every existing caller working unchanged, exactly as today.
    The same holds for an image read's page list (`pages`), passed only when
    it is not pages 1-4. `agreement_min` (`gate.agreement_min`) decides, on
    both ladders, whether an agreement GATE will count already confirms a gate
    field, so no second read is asked for it."""
    doc_class, fields = t0_extract(doc, filename, result=result,
                                   companion_url=companion_url)
    used = ["T0"]
    if doc_class in ("business-doc", "msds"):
        return doc_class, fields, used

    llm_kwargs = {"hints": hints} if hints is not None else {}

    def read(label, missing, **read_options):
        return llm.extract(label, doc, filename, missing, **llm_kwargs, **read_options)

    # No text page at all: nothing for a text read or the text doc-class call
    # to see. Until 2026-10-12 such a file went to the text doc-class call,
    # which found no text and refused it -- 281 production scans were never
    # read -- or, when T0 typed it off its filename, through a T1 read of empty
    # text into a forced Sonnet image read.
    if not pdfutil.has_text_page(doc):
        doc_class, fields = run_scan_ladder(
            doc, filename, doc_class, fields, used, read,
            classify=getattr(llm, "classify_image", None), threshold=threshold,
            image_settle=image_settle, agreement_min=agreement_min)
        return doc_class, fields, used

    doc_class, fields = run_text_ladder(
        doc, filename, doc_class, fields, used, read,
        classify=getattr(llm, "classify", None),
        classify_image=getattr(llm, "classify_image", None), threshold=threshold,
        agreement_min=agreement_min)
    return doc_class, fields, used


def run_text_ladder(doc, filename: str, doc_class: str, fields: dict, used: list[str],
                    read, classify=None, classify_image=None, *, threshold: float = 0.95,
                    page_aware: bool = True,
                    agreement_min: float = AGREEMENT_MIN) -> tuple[str, dict]:
    """T-3, the text ladder (PRD v3 section 5, amended 2026-10-12; plan Task 4).
    For a document with at least one text page. Returns (doc_class, fields)
    and appends each step's label to `used`.

    `read(label, missing, **read_options) -> {field: evidence}` makes one read
    and returns every field its response carries (`llm.extract`); `pages` is
    the one read option this ladder passes, and only when the page list is not
    pages 1-4; `page_aware=False` (the batch transport, which cannot send a
    page list) keeps every image read on pages 1-4, and the ladder then knows
    those are the pages read. `classify(doc, filename) -> (doc_class, quote)` is the text
    doc-class call (optional: absent it, an `unknown` document is read as
    before). Both are injected so the sync and batch transports share every
    rule here.

    1. `unknown` to T0: the doc-class call on the page 1-2 text (`doc-class`),
       or, when pages 1-2 hold no text (a mixed PDF that opens with scanned
       pages), on their images (`classify_image`, `doc-class-image`, review
       2026-10-13). A refusal (`not-a-compliance-document`) is terminal.
    2. `T1` (`models.t1`) on the whole text (`pdf.bounded_text`), asked every
       pursued field T0 does not hold at the threshold (`_text_pursued`: the
       IFU and manufacturer-scope suppressions of `needs_escalation`). A field
       is settled when the value it then holds passes `checks.settled` (T0's
       own included, where T1 read nothing better), or T1 answered it empty and
       `checks.null_settled` holds (no label for it in the text, no image or
       OCR page, no page where an annotation paints an image
       (`pdf.annotation_image_pages`), text not cut).
    3. Fields pursued only after T1 (a scope T1 changed brings the item
       identifiers in) are taken from T1's answer, which carries every field,
       and settle as in step 2.
    4. `T2-first` (`models.vision_first`) for what is open, on the pages
       `_image_pages` picks. A field settles when its value passes the checks,
       when it is a Basic UDI-DI whose check pair verifies
       (`checks.udi_check_valid`, the text layer may be corrupt), when it
       agrees with T1 (`checks.agree`, no per-read minimum: ruling 4, "as
       measured"), or when both reads ANSWERED it empty and
       `checks.both_null_settled` allows it (never a Basic UDI-DI the text
       labels: multi-UDI lists go to Sonnet). Image pages of a mixed PDF and
       pages where an annotation paints an image, when the read did not
       render them, and text beyond the budget, count as unread.
    5. Type to scope (`_scope_from_type`) after each read, as on a scan; and
       once the type is known to be an IFU, the certificate fields a read
       wrote before that are dropped (`_drop_ifu_certificate_reads`).
    6. `T2` (`models.t2`, Sonnet) for what is still open, and only then; its
       answer closes every field it was asked, an empty answer included
       (`_sonnet_closes`, the scan ladder's guards).
    7. The gate fields (`GATE_FIELDS`, plan Task 5b): a `regulation` its one
       printed citation proves is marked `proven: "citation"`
       (`checks.citation_proven`), and a read agreeing with the stored value
       is stamped as its agreeing read (`_stamp_agreement`). A gate field a
       read holds at `agreement_min` or more that neither proves nor confirms
       with an agreement GATE will count (`_to_confirm`) is asked of
       `T1-second` (`models.second_read`, thinking off), one call for all of
       them. That read confirms or not: it is counted as a disagreement when
       it does not agree, and its value never replaces the stored one. When
       it agrees, the stored evidence is chosen as `merge` chooses it (ruling
       D1: a verified read first, then the surer; `_prefer_confirming`), and
       the other is the agreeing read.

    A read that failed answered nothing, which is not an empty answer: its
    fields stay open and the read is named in `READS_KEY` (`failed_reads`).
    Every read is recorded there; `read_disagreements` counts where they
    differ. Where part of the document was read by nobody (text past the
    budget, a scanned page or an annotation's image no image read rendered),
    a field Sonnet also answered empty is recorded under `PAGES_UNREAD_KEY`
    (`pages-unread`)."""
    # T0 declined to type this file. Ask, rather than guess.
    #
    # `unknown` used to proceed into the full ladder and, worse, be promoted to
    # `compliance-doc` by any type marker found anywhere in the text -- which is
    # how four web pages became registry documents on 2026-09-02. T0 now only
    # classifies from its two-page header window (88% of the real corpus), and
    # everything else comes here for a real answer, including
    # `not-a-compliance-document`, which is terminal. The call is logged under
    # its own label (`doc-class`, until 2026-10-12 `T1`).
    # Pages 1-2 with no text: the text call would see nothing and refuse the
    # file without a call, so their images are asked, as on a scan. A client
    # with no image call keeps the text call (and its refusal), as before.
    if doc_class == "unknown" and classify_image is not None:
        from app.extract.llm import doc_class_text   # `llm` imports this module
        if not doc_class_text(doc):
            classify, label, tier = classify_image, DOC_CLASS_IMAGE, "T2"
        else:
            label, tier = DOC_CLASS_TEXT, "T1"
    else:
        label, tier = DOC_CLASS_TEXT, "T1"
    if doc_class == "unknown" and classify is not None:
        answer, quote = classify(doc, filename)
        used.append(label)
        if answer == NOT_A_DOCUMENT:
            return NOT_A_DOCUMENT, {}
        doc_class = "compliance-doc"
        # The classifier's answer IS evidence about the type, and a better one
        # than a filename guess. It never overwrites a T0 read, and any field
        # read outranks it (`_STEP_RANK`).
        if "type" not in fields:
            fields["type"] = {"value": answer, "conf": T1_DOC_CLASS_CONF, "tier": tier,
                              "verbatim": quote, "page": 1, "step": label}

    t0 = {f: dict(ev) for f, ev in fields.items()
          if isinstance(ev, dict) and ev.get("tier") == "T0"}
    known = {f for f, ev in t0.items() if float(ev.get("conf") or 0.0) >= threshold}
    open_fields = _text_pursued(fields, known)
    if not open_fields:
        return doc_class, fields

    pages = pdfutil.page_texts(doc)
    classes = pdfutil.page_classes(doc)
    # The pages a text read does not see in full: a scanned page of a mixed
    # PDF, and a page where an annotation paints an image (VOCO's e-signature,
    # the only print of its issue date; Task 8, 2026-10-13).
    image_pages = tuple(sorted({n for n, c in enumerate(classes, start=1) if c == "image"}
                               | set(pdfutil.annotation_image_pages(doc))))
    # Read at call time, not as `bounded_text`'s default: T1 reads exactly this
    # budget, and the checks must judge the same cut.
    truncated = pdfutil.bounded_text(doc, pdfutil.LLM_TEXT_BUDGET_CHARS)[1] > 0
    # T1 does not see those pages in full: an empty answer proves nothing, nor
    # does a citation's absence (`checks.citation_proven` judges this text).
    # An image read that renders them counts them read (`image_ctx`).
    text_ctx = checks.Ctx(pages=pages, classes=classes, truncated=truncated,
                          unread_pages=frozenset(image_pages))
    reads: dict[str, tuple[list[str], dict]] = {}
    rendered: set[int] = set()

    t1 = _read_step(read, used, reads, TEXT_READ, open_fields)
    fields = merge(fields, _asked(t1, open_fields), ctx=text_ctx)
    known |= {f for f in open_fields if _text_settles(f, fields, t1, text_ctx)}
    _scope_from_type(fields, known)
    _drop_ifu_certificate_reads(fields, known, t0, threshold)
    fields = _from_t1(fields, known, t1, reads, text_ctx)

    def choose(wanted):
        return (_image_pages(doc.page_count, wanted, t1, pages, image_pages)
                if page_aware else None)

    open_fields = _text_pursued(fields, known)
    if open_fields:
        sel = choose(open_fields)
        first = _read_step(read, used, reads, FIRST_IMAGE, open_fields, pages=sel)
        fields = merge(fields, _asked(first, open_fields), ctx=text_ctx)
        rendered |= set(sel or range(1, min(doc.page_count, _max_vision_pages()) + 1))
        image_ctx = checks.Ctx(pages=pages, classes=classes, truncated=truncated,
                               unread_pages=frozenset(set(image_pages) - rendered))
        known |= {f for f in open_fields
                  if _image_settles_text(f, fields, t1, first, image_ctx)}
        _scope_from_type(fields, known)
        _drop_ifu_certificate_reads(fields, known, t0, threshold)
        fields = _from_t1(fields, known, t1, reads, text_ctx)

    open_fields = _text_pursued(fields, known)
    if open_fields:
        sel = choose(open_fields)
        sonnet = _read_step(read, used, reads, SONNET_IMAGE, open_fields, pages=sel)
        fields = merge(fields, _asked(sonnet, open_fields), ctx=text_ctx)
        rendered |= set(sel or range(1, min(doc.page_count, _max_vision_pages()) + 1))
        # The last read closes what it was asked, an empty answer included, as
        # on a scan: what the field holds is a value no rule settled.
        for f in open_fields:
            if f in sonnet and _is_blank(sonnet[f]) and _sonnet_closes(
                    f, fields.get(f), threshold):
                fields[f] = sonnet[f]
        known |= set(open_fields)
        _scope_from_type(fields, known)
        _drop_ifu_certificate_reads(fields, known, t0, threshold)
        # Part of the document no read saw: text past the budget, or a scanned
        # page of a mixed PDF or an annotation's image no image read rendered.
        # A field every read left empty there is unread, not absent (PRD v3
        # section 7, `pages-unread`). A field Sonnet gave no answer for
        # failed, which says nothing.
        if truncated or set(image_pages) - rendered:
            unread = [f for f in open_fields
                      if f in sonnet and _is_blank(sonnet[f]) and _is_blank(fields.get(f))]
            if unread:
                fields[PAGES_UNREAD_KEY] = unread

    # The gate fields, once the ladder has run: what the citation proves, what
    # two reads agree on, and one second text read for what neither covers.
    reg = fields.get("regulation")
    if _is_read(reg) and checks.citation_proven(reg, text_ctx):
        fields["regulation"] = {**reg, PROVEN_KEY: CITATION}
    _stamp_agreement(fields, reads)
    confirm = _to_confirm(fields, agreement_min)
    if confirm:
        second = _read_step(read, used, reads, SECOND_TEXT, confirm)
        _prefer_confirming(fields, known, second, confirm, text_ctx)
        _stamp_agreement(fields, reads)

    fields[READS_KEY] = _reads_record(reads)
    return doc_class, fields


def _max_vision_pages() -> int:
    # Imported here: `llm` imports this module (NOT_A_DOCUMENT, the REF cap).
    from app.extract.llm import MAX_VISION_PAGES
    return MAX_VISION_PAGES


def _read_step(read, used: list[str], reads: dict, label: str, missing: list[str],
               pages=None) -> dict:
    """One read: its whole answer, each field marked with the step
    (`_stamp`). Recorded under `reads[label]` with the fields it was asked;
    `pages` is passed to `read` only when given, so a protocol stub without
    read options keeps working."""
    opts = {"pages": pages} if pages is not None else {}
    answer = _stamp(read(label, missing, **opts), label)
    used.append(label)
    reads[label] = (list(missing), answer)
    return answer


def _stamp_agreement(fields: dict, reads: dict) -> None:
    """Name each gate field's agreeing read (`AGREED_MODEL_KEY`,
    `AGREED_CONF_KEY`): another read that was asked the field and gave the
    same well-formed value as the read whose evidence is stored (`_agree`).
    The surest such read where several did; none where none did, or where the
    field holds no read (T0, a scope that follows the type, the doc-class
    answer, nothing). Stamped whatever the numbers: GATE applies its floor.
    Replaces the field's evidence dict rather than changing it, since the
    same dict sits in the read's own answer."""
    for f in GATE_FIELDS:
        ev = fields.get(f)
        if not _is_read(ev) or ev.get("step") not in reads:
            continue
        ev = {k: v for k, v in ev.items() if k not in (AGREED_MODEL_KEY, AGREED_CONF_KEY)}
        best = None
        for label, (asked, answer) in reads.items():
            other = answer.get(f)
            if label == ev["step"] or f not in asked or not _agree(f, ev, other):
                continue
            if best is None or _conf(other) > _conf(best):
                best = other
        if best is not None:
            ev[AGREED_MODEL_KEY], ev[AGREED_CONF_KEY] = best["model_id"], _conf(best)
        fields[f] = ev


def _prefer_confirming(fields: dict, known: set[str], answer: dict, confirmed: list[str],
                       ctx) -> None:
    """Ruling D1 for a confirming read (Denis, 2026-10-13: one rule for every
    read). `T1-second`, or `T2-second` on the gate fields the first image read
    settled, is never merged, so its value never replaces the stored one; but
    where it gave the same value and `merge` would have stored it
    (`_stores_over`: verified where the stored read is not, or surer among
    verified or unverified alike), its evidence is stored and the former
    stored read becomes the agreeing read (`_stamp_agreement`, called after).
    The value stays the same, since agreeing well-formed gate values are
    identical. Where the type's stored read changes, type to scope runs again,
    so a derived scope carries the stored type's own number."""
    swapped_type = False
    for f in confirmed:
        held, mine = fields.get(f), answer.get(f)
        if _agree(f, held, mine) and _stores_over(f, mine, held, ctx):
            fields[f] = mine
            swapped_type |= f == "type"
    if swapped_type:
        _scope_from_type(fields, known, rederive=True)


def _confirmed(evidence: dict, agreement_min: float) -> bool:
    """A gate field GATE will score at more than the stored read's own number:
    a citation-proven `regulation`, or an agreeing read where both reads are
    at least `agreement_min` (GATE's floor, applied here to the raw numbers,
    which calibration leaves as they are at the default factor of 1.0)."""
    if evidence.get(PROVEN_KEY) == CITATION:
        return True
    return (bool(evidence.get(AGREED_MODEL_KEY))
            and _conf(evidence) >= agreement_min
            and _conf(evidence, AGREED_CONF_KEY) >= agreement_min)


def _to_confirm(fields: dict, agreement_min: float) -> list[str]:
    """The gate fields a second read is asked to confirm, in `GATE_FIELDS`
    order: held by a read (`_is_read`: T0 at 0.95 is GATE's own number, and a
    row with no model id is never combined), with a well-formed value (nothing
    else can be agreed with), held at `agreement_min` or more (review 2026-10-13,
    F4: the stored read's own number is half of any combination, and no later
    read can raise it, so under the floor a second read cannot count), and not
    `_confirmed` already."""
    return [f for f in GATE_FIELDS
            if _is_read(fields.get(f)) and checks.well_formed(f, fields[f].get("value"))
            and _conf(fields[f]) >= agreement_min
            and not _confirmed(fields[f], agreement_min)]


def _asked(answer: dict, wanted) -> dict:
    """The part of a read's answer the ladder merges: the fields it pursues
    at the time of the read. A read answers every field (the response schema
    requires them all), and a field nobody pursued stays out: the item
    identifiers of a manufacturer-wide certificate, `stated_class` (T0's alone,
    PRD v3 section 5), and the certificate fields of a document already known
    to be an IFU. A certificate field asked while the type was still open IS
    merged; if the type then turns out an IFU, `_drop_ifu_certificate_reads`
    removes it."""
    return {f: answer[f] for f in wanted if f in answer}


def _drop_ifu_certificate_reads(fields: dict, known: set[str], t0: dict,
                                threshold: float = 0.95) -> None:
    """Once the type is known to be an IFU (`_NO_CERTIFICATE_TYPES`), the
    certificate fields a read wrote before the type was known are dropped
    (review 2026-10-13; both ladders): the suppression exists because a model asked for a
    date an IFU cannot hold finds one anyway (STRAUMANN, "701593/M/12 04/26"
    read as 2026-04-26), and that is as true of an answer given before the
    type was known as after. T0's own read of the field comes back where there
    was one; otherwise the field is removed. The read stays in `READS_KEY`.

    Only for a type someone read (review round 2): T0 at or above the
    threshold, or a read's value (the doc-class call or a field read). A T0
    guess under it (a file named "IFU") becomes known when both reads answer
    `type` empty, and that is no evidence the document is an IFU, so nothing
    the reads found is dropped for it."""
    type_ev = fields.get("type") or {}
    if "type" not in known or type_ev.get("value") not in _NO_CERTIFICATE_TYPES:
        return
    if type_ev.get("tier") == "T0" and float(type_ev.get("conf") or 0.0) < threshold:
        return
    for f in _ESCALATE_CERTIFICATE:
        ev = fields.get(f)
        if not isinstance(ev, dict) or ev.get("tier") == "T0":
            continue
        if f in t0:
            fields[f] = dict(t0[f])
        else:
            del fields[f]


def _text_pursued(fields: dict, known: set[str]) -> list[str]:
    """`needs_escalation` for the text ladder, with "confident" meaning KNOWN
    (T0 at or above the threshold, or settled). The two suppressions as they
    stand (decisions 2026-10-12: "the text ladder keeps today's suppression"):
    no certificate fields once the type is known to be an IFU; no item
    identifiers while the scope's VALUE is `manufacturer`, known or not. T1
    reads the scope from the whole text, so after T1 that value is a read's,
    not T0's guess; unlike `_scan_pursued`, the set can grow when a read
    changes the scope, which is what `_from_t1` is for."""
    return _pursued(fields, known, skip_item_ids=(
        (fields.get("coverage_scope") or {}).get("value") == "manufacturer"))


def _text_settles(f: str, fields: dict, answer: dict, ctx) -> bool:
    """Step 2 of `run_text_ladder` for one field, after T1: the value the field
    holds is proven by the text; or T1 ANSWERED the field empty, the field
    holds nothing, and the null rule holds. A field missing from T1's answer
    was not answered (a failed read) and stays open.

    The value the field holds is `merge`'s: T1's, or T0's where T1 answered
    nothing better (empty, or a shorter list). So a T0 value under the
    threshold settles when the text proves it (plan default, "T0 fields under
    0.95 count as settled when they pass the checks"), but never against a
    different value T1 read: then the two disagree and the image read decides.
    Measured on the eval: Ceramill's T0 read the word after its UDI label
    ("Document", unscorable, 0.9) and T1 the OCR layer's invalid code; the
    image read's check-valid code settles it."""
    ev = fields.get(f)
    if _proven(f, fields, answer.get(f), ctx):
        return True
    return (f in answer and _is_blank(answer[f]) and _is_blank(ev)
            and checks.null_settled(f, ctx))


def _proven(f: str, fields: dict, own: dict | None, ctx) -> bool:
    """The field is proven by the text: the value it holds passes
    `checks.settled`, or the read's OWN value does (review 2026-10-13). The two
    differ only for an enumeration, where `merge` lets the longer list win:
    of two reads that agree, `merge` has already stored a verified one where
    either is (ruling D1, 2026-10-13).

    When the read's own list settles it, the field takes that list if the list
    it holds was written by a read: a longer read list that fails the check (a
    code not printed) is not stored over a shorter one that passes. A list T0
    wrote is never replaced (review round 2): T0's is the deterministic
    enumeration, and where it fails the check it is because its codes are
    elsewhere, a companion annex (Komet's RA_812 list beside the RA_810
    declaration, 63 of 63 on the mirror). When both pass, the longer list the
    field holds stays. T0 at or above the threshold is never asked at all."""
    held = fields.get(f)
    if checks.settled(f, held, ctx):
        return True
    if own is not None and not _is_blank(own) and checks.settled(f, own, ctx):
        if not (isinstance(held, dict) and held.get("tier") == "T0"):
            fields[f] = own
        return True
    return False


def _from_t1(fields: dict, known: set[str], t1: dict, reads: dict, ctx) -> dict:
    """Fields pursued only now, after a read changed the scope or the type:
    T1 answered them anyway (its response carries every field), so they take
    T1's answer and settle as T1's asked fields do, before any image read is
    asked them. Recorded as T1's, so the record and the disagreement count see
    them. Returns the merged fields."""
    asked = reads.get(TEXT_READ, ([], {}))[0]
    pulled = [f for f in _text_pursued(fields, known) if f not in asked and f in t1]
    if not pulled:
        return fields
    asked.extend(pulled)
    fields = merge(fields, _asked(t1, pulled), ctx=ctx)
    known |= {f for f in pulled if _text_settles(f, fields, t1, ctx)}
    _scope_from_type(fields, known)
    return fields


def _image_settles_text(f: str, fields: dict, t1: dict, image: dict, ctx) -> bool:
    """Step 4 of `run_text_ladder` for one field: the image read's own value
    passes the checks (`_proven`: the field then holds the value that settled
    it), or is a check-valid Basic UDI-DI, or agrees with T1's; or both reads
    answered it empty and `checks.both_null_settled` allows it. No answer from
    the image read (a failed read) settles nothing."""
    b = image.get(f)
    if b is None:
        return False
    if not _is_blank(b):
        if _proven(f, fields, b, ctx):
            return True
        if f == "basic_udi_di" and checks.udi_check_valid(b.get("value")):
            return True
        return checks.agree(f, t1.get(f), b, 0.0)
    a = t1.get(f)
    return a is not None and _is_blank(a) and checks.both_null_settled(f, ctx)


def _image_pages(page_count: int, open_fields: list[str], t1: dict, pages: list[str],
                 image_pages=()) -> tuple[int, ...] | None:
    """The pages a text document's image read renders (Denis, 2026-10-12),
    chosen in this order: the pages T1 cited for the open fields, the first
    page where each open field's label occurs (`checks.LABELS`), the pages
    no text read sees in full (`image_pages`: the image pages of a mixed PDF,
    the pages where an annotation paints an image), then page 1 onward; at
    most `MAX_VISION_PAGES`, each clamped to the page count
    (`llm._vision_content` refuses a page past the end). Sent in document order, so the model reads the document as it runs
    and a choice of pages 1-4 is today's request byte for byte: then this
    returns None, the read's default. Measured on the eval: 1 of 72 open
    fields pointed past page 4."""
    limit = _max_vision_pages()
    chosen: list[int] = []

    def add(n) -> None:
        if (isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= page_count
                and n not in chosen and len(chosen) < limit):
            chosen.append(n)

    for f in open_fields:
        add((t1.get(f) or {}).get("page"))
    for f in open_fields:
        pat = checks.LABELS.get(f)
        if pat is not None:
            add(next((n for n, text in enumerate(pages, start=1)
                      if re.search(pat, text, re.IGNORECASE)), None))
    for n in image_pages:
        add(n)
    for n in range(1, page_count + 1):
        add(n)
    out = tuple(sorted(chosen))
    return None if out == tuple(range(1, min(page_count, limit) + 1)) else out


def _stamp(answer: dict, label: str) -> dict:
    """One read's answer with each field's evidence marked with the step that
    produced it, so `merge` can rank it. Copies: the evidence a client hands
    back is never mutated."""
    return {f: {**ev, "step": label} for f, ev in (answer or {}).items() if isinstance(ev, dict)}


def _image_settles(field: str, evidence: dict | None, image_settle: float) -> bool:
    """A cheap image read settles a field on its own: the value is well-formed
    and the read states at least `extract.image_settle` (0.85, measured)."""
    return (isinstance(evidence, dict)
            and checks.well_formed(field, evidence.get("value"))
            and float(evidence.get("conf") or 0.0) >= image_settle)


def _scan_pursued(fields: dict, known: set[str]) -> list[str]:
    """`needs_escalation` for the scan ladder, with "confident" meaning KNOWN:
    held by T0 at or above the threshold, or settled by the reads. The same
    two suppressions: no certificate fields once the type is known to be an
    IFU, no item identifiers once the scope is known to be `manufacturer`.

    Known, where `needs_escalation` tests the scope's VALUE alone. On a scan
    that value is mostly T0's guess: a DoC typed off its file name with no REF
    list read gets `manufacturer` at 0.6 (`derive_coverage_scope`), and by
    value the item identifiers would never be asked of any read, though a DoC
    is exactly the document that needs one. Keyed to knowledge, the pursued
    set only shrinks as fields settle, so the first image read is asked
    everything a later one could be. The one exception is type to scope
    replacing a known `manufacturer` scope (an IFU): the item identifiers it
    brings in are asked of the next read and settle there as a first read
    would, or go to Sonnet."""
    scope = (fields.get("coverage_scope") or {}).get("value")
    return _pursued(fields, known, skip_item_ids=(
        "coverage_scope" in known and scope == "manufacturer"))


def _pursued(fields: dict, known: set[str], *, skip_item_ids: bool) -> list[str]:
    """The fields a ladder still pursues: the always-escalated set (without
    the certificate fields once the type is known to be an IFU), plus the item
    identifiers unless `skip_item_ids`, minus what is known. The two ladders
    differ only in when the item identifiers are skipped (`_scan_pursued`,
    `_text_pursued`)."""
    doc_type = (fields.get("type") or {}).get("value")
    always = _ESCALATE_ALWAYS
    if "type" in known and doc_type in _NO_CERTIFICATE_TYPES:
        always = [f for f in always if f not in _ESCALATE_CERTIFICATE]
    pursued = list(always)
    if not skip_item_ids:
        pursued += _ESCALATE_ITEM_ID
    return [f for f in pursued if f not in known]


def _scope_from_type(fields: dict, known: set[str], *, rederive: bool = False) -> None:
    """Type to scope (measured 2026-10-12): once the type is known to be EC,
    ISO or an IFU, the scope follows from it (`derive_coverage_scope`), and a
    scope that disagrees with that, or is empty, is replaced. Two cheap reads
    agreed on `group` for an MDD certificate in the eval; this is the rule that
    caught it. The replacement is the derivation's own evidence (T0, no page,
    "type=EC"), at the type's confidence where that is lower: the scope is no
    surer than the type it follows from. A scope that already agrees keeps
    its evidence unless the derivation is surer (Task 4): T0's scope follows
    T0's type, so a certificate typed off its file name holds `manufacturer`
    at 0.6, and once a read settles the type at 0.95 the scope is no longer
    that guess; and a read that states the same scope at less than the
    derivation is the weaker claim of the two (PRD v3 section 7, `scope_stated`:
    a certificate's manufacturer scope follows from its type). Measured on the
    eval scans: without this, two EC certificates whose first read stated the
    scope at 0.6 and 0.8 fell from production to review once T0 stopped
    holding the scope at 1.0. Either way the scope is then known.

    A derived scope is marked `follows: "type"` (`FOLLOWS_KEY`, ruling D3,
    2026-10-13), an IFU's included: GATE scores the type, not the scope, so a
    certificate whose type a second read confirmed is not held back by a
    scope that can be no surer than the type's own number. A read's scope
    kept because it agrees is not marked: it is a read, scored as one.

    `rederive` (after a confirming read took the type's place, ruling D1 for
    every read): a scope this derived before follows the new stored type's
    own number whether that is higher or lower, so the marker, the score and
    the auto-bind's input all read the type GATE reads."""
    doc_type = fields.get("type") or {}
    value = doc_type.get("value")
    if "type" not in known or value not in _SCOPE_FROM_TYPE:
        return
    type_conf = float(doc_type.get("conf") or 0.0)
    derived = derive_coverage_scope(value, False, type_conf=type_conf)
    derived = {**derived, "conf": min(type_conf, derived["conf"]), FOLLOWS_KEY: FOLLOWS_TYPE}
    scope = fields.get("coverage_scope") or {}
    if ((rederive and scope.get(FOLLOWS_KEY) == FOLLOWS_TYPE)
            or checks.normalized("coverage_scope", scope.get("value")) != checks.normalized(
                "coverage_scope", derived["value"])
            or float(scope.get("conf") or 0.0) < derived["conf"]):
        fields["coverage_scope"] = derived
    known.add("coverage_scope")


def _reads_record(reads: dict[str, tuple[list[str], dict]]) -> list[dict]:
    """`READS_KEY`'s value: per read, in read order, its step, its model and,
    for every field it was asked, what it answered: `{value, conf, page}`, a
    null value included, or None when the read gave no answer for the field at
    all. Value, confidence and page only: the verbatim of the read that
    prevailed is on the field.

    A read that answered none of the fields it was asked is marked
    `"failed": True` (`failed_reads`): a real read answers every field it is
    asked, null included (the response schema requires them all), so an empty
    answer means the read itself failed -- a response that would not parse or
    was cut at max_tokens (`llm._response_json`), or a batch request that
    errored (`AnthropicBatchClient.results` skips it)."""
    out = []
    for label, (asked, answer) in reads.items():
        model = next((ev.get("model_id") for ev in answer.values() if ev.get("model_id")), None)
        entry = {"step": label, "model_id": model, "fields": {
            f: ({"value": answer[f].get("value"), "conf": answer[f].get("conf"),
                 "page": answer[f].get("page")} if f in answer else None)
            for f in asked}}
        if asked and not answer:
            entry["failed"] = True
        out.append(entry)
    return out


def _sonnet_closes(field: str, incumbent, threshold: float) -> bool:
    """Whether Sonnet's EMPTY answer for `field` replaces what the field holds.

    It does: a field reaches Sonnet only while the cheap reads leave it open,
    so what it holds is a value no rule settled (review 2026-10-12: on the eval
    a `cert_number` only the second cheap read gave, contradicted by the first
    and by Sonnet, survived Sonnet's null, which `merge`'s blank guard kept;
    the measured route takes Sonnet's answer). Two guards stay, as in `merge`:
    an enumeration is never replaced by a shorter answer, and an empty one is
    shorter than any list; T0 at or above the threshold is never overwritten."""
    if not isinstance(incumbent, dict) or _is_blank(incumbent):
        return True
    if field in _ENUMERATED_FIELDS:
        return False
    return _rank(incumbent, threshold) != _T0_SETTLED


def run_scan_ladder(doc, filename: str, doc_class: str, fields: dict, used: list[str],
                    read, classify=None, *, threshold: float = 0.95,
                    image_settle: float = IMAGE_SETTLE,
                    agreement_min: float = AGREEMENT_MIN) -> tuple[str, dict]:
    """S-2, the scan ladder (PRD v3 section 5, amended 2026-10-12; plan Task 3).
    For a document with no text page. Returns (doc_class, fields) and appends
    each step's label to `used`.

    `read(label, missing) -> {field: evidence}` makes one image read;
    `classify(doc, filename) -> (doc_class, quote)` the doc-class call on the
    page 1-2 images (optional, like `classify` on the text ladder: absent it, an
    `unknown` document is read as before). Both are injected so the sync and
    batch transports share every rule here; the batch `read` raises `Deferred`
    while its batch runs.

    1. `unknown` to T0: the doc-class call on the images. A refusal
       (`not-a-compliance-document`) is terminal: no fields, no further read.
    2. `T2-first` (`models.vision_first`, pages 1-4) for every pursued field T0
       has not settled. A field it answers well-formed at `image_settle` or
       above is settled.
    3. `T2-second` (`models.second_read`, thinking off) for what is open. A
       field settles when both reads agree (`checks.agree`: one well-formed
       value, each read at least `agreement_min`) or both ANSWERED it empty and
       `checks.both_null_settled` allows it (never a Basic UDI-DI on a scan,
       never when pages went unread). A read that failed gave no answer, which
       is not an empty one: its fields stay open (`failed_reads`). The gate
       fields the first read settled (`_to_confirm`, plan Task 5b) ride in the
       same call, so it is made even when nothing else is open: for those it
       only confirms, as `T1-second` does on text (a counted disagreement
       when it does not agree, never sent on to Sonnet, its value never
       replacing the stored one; when it agrees and is surer, its evidence is
       stored and the first read's becomes the agreeing one, ruling D1).
    4. On a scan longer than the pages read, a field both cheap reads answered
       empty is UNREAD: not settled, not sent to Sonnet (who sees the same
       pages), recorded under `PAGES_UNREAD_KEY` (flag `pages-unread`).
    5. Type to scope (`_scope_from_type`), as soon as the type is known:
       after each read, so a type the first read settles spares the second
       read the scope. Once the type is known to be an IFU, the certificate
       fields a read wrote before that are dropped
       (`_drop_ifu_certificate_reads`, as on text).
    6. `T2` (`models.t2`, Sonnet) for what is still open, and only then; its
       answer closes every field it was asked, an empty answer included
       (`_sonnet_closes`), and type to scope runs again on the type it leaves.
       A field Sonnet gave no answer for keeps what it holds.
    7. Each gate field read twice to the same value names its agreeing read
       (`_stamp_agreement`). A scan has no citation to prove a regulation by.

    The pursued set is recomputed after every read, as the text ladder
    recomputes `needs_escalation` after T1, but keyed to what is KNOWN rather
    than to a value (`_scan_pursued`), so it only shrinks: an IFU stops being
    asked for certificate fields, a manufacturer-wide certificate for item
    identifiers. Every read is recorded under `READS_KEY`;
    `read_disagreements` counts where they differ and `failed_reads` names the
    reads that failed.
    """
    if doc_class == "unknown" and classify is not None:
        answer, quote = classify(doc, filename)
        used.append(DOC_CLASS_IMAGE)
        if answer == NOT_A_DOCUMENT:
            return NOT_A_DOCUMENT, {}
        doc_class = "compliance-doc"
        # The answer is evidence about the type, as on the text ladder, and
        # never overwrites a T0 read. Ranked below every field read (`step`).
        if "type" not in fields:
            fields["type"] = {"value": answer, "conf": T1_DOC_CLASS_CONF, "tier": "T2",
                              "verbatim": quote, "page": 1, "step": DOC_CLASS_IMAGE}

    t0 = {f: dict(ev) for f, ev in fields.items()
          if isinstance(ev, dict) and ev.get("tier") == "T0"}
    known = {f for f, ev in t0.items() if float(ev.get("conf") or 0.0) >= threshold}
    open_fields = _scan_pursued(fields, known)
    if not open_fields:
        return doc_class, fields

    ctx = checks.Ctx(pages=pdfutil.page_texts(doc), classes=pdfutil.page_classes(doc),
                     unread_pages=frozenset(range(_max_vision_pages() + 1, doc.page_count + 1)))
    reads: dict[str, tuple[list[str], dict]] = {}

    def step(label: str, missing: list[str]) -> dict:
        # The whole answer is kept for the record; only the asked fields are
        # merged (`_asked`), since a read answers every field.
        return _read_step(read, used, reads, label, missing)

    first = step(FIRST_IMAGE, open_fields)
    fields = merge(fields, _asked(first, open_fields))
    known |= {f for f in open_fields if _image_settles(f, first.get(f), image_settle)}
    _scope_from_type(fields, known)
    _drop_ifu_certificate_reads(fields, known, t0, threshold)

    unread: list[str] = []
    open_fields = _scan_pursued(fields, known)
    # The gate fields the first read settled, confirmed in the same call: one
    # call per label, or the second call's cost row would be lost. Asked in
    # the ladder's field order, so a request with nothing to confirm is the
    # one Task 3 sent.
    confirm = [f for f in _to_confirm(fields, agreement_min) if f not in open_fields]
    ask = [f for f in ESCALATE if f in open_fields or f in confirm]
    if ask:
        second = step(SECOND_IMAGE, ask)
        fields = merge(fields, _asked(second, open_fields))
        for f in open_fields:
            b = second.get(f)
            if f not in reads[FIRST_IMAGE][0]:
                # Brought in by type to scope after the first read (an IFU
                # whose scope was T0's `manufacturer`): this is its first read,
                # and it settles as one does, or goes to Sonnet.
                if _image_settles(f, b, image_settle):
                    known.add(f)
                continue
            a = first.get(f)
            if checks.agree(f, a, b, agreement_min):
                known.add(f)
            elif f in first and f in second and _is_blank(a) and _is_blank(b):
                # Both reads ANSWERED it empty. A field missing from an answer
                # is a failed read, not an empty one, and stays open.
                if checks.both_null_settled(f, ctx):
                    known.add(f)
                elif ctx.unread_pages:
                    unread.append(f)
        # The gate fields it only confirmed: no text to verify a quote
        # against on a scan, so the surer read's evidence is stored (D1).
        _prefer_confirming(fields, known, second, confirm, None)

    _scope_from_type(fields, known)
    _drop_ifu_certificate_reads(fields, known, t0, threshold)

    open_fields = [f for f in _scan_pursued(fields, known) if f not in unread]
    if open_fields:
        sonnet = step(SONNET_IMAGE, open_fields)
        fields = merge(fields, _asked(sonnet, open_fields))
        # The last read closes what it was asked, an empty answer included:
        # `merge` keeps a value against a blank, which is right only for a
        # field Sonnet gave no answer for (absent from `sonnet`).
        for f in open_fields:
            if f in sonnet and _is_blank(sonnet[f]) and _sonnet_closes(
                    f, fields.get(f), threshold):
                fields[f] = sonnet[f]
        known |= set(open_fields)
        _scope_from_type(fields, known)
        _drop_ifu_certificate_reads(fields, known, t0, threshold)

    if unread:
        fields[PAGES_UNREAD_KEY] = unread
    _stamp_agreement(fields, reads)
    fields[READS_KEY] = _reads_record(reads)
    return doc_class, fields


def _shown(value):
    """A read's value, short enough for a job result."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        head = ", ".join(str(c) for c in value[:5])
        return head + (f" ... ({len(value)})" if len(value) > 5 else "")
    return str(value)[:80]


def failed_reads(fields: dict) -> list[str]:
    """The steps whose read failed (`_reads_record`), in read order. For the
    extract job result, so a failed read is counted rather than silent."""
    return [e.get("step") for e in fields.get(READS_KEY) or [] if e.get("failed")]


def read_disagreements(fields: dict) -> list[dict]:
    """Every field two or more reads answered differently, from `READS_KEY`:
    `{field, steps, values}`, the steps in read order and each value as that
    read gave it (None for an empty answer). Compared normalized
    (`checks.normalized`), so spacing and case are not a disagreement; an
    empty answer against a value is one. A read that gave no answer for the
    field (a failed read, recorded as None) is not a reading and is left out.
    For the extract job result (PRD v3 section 5: "disagreements between reads
    are counted on the job result")."""
    by_field: dict[str, list[tuple[str, object]]] = {}
    for entry in fields.get(READS_KEY) or []:
        for f, ev in (entry.get("fields") or {}).items():
            if ev is None:
                continue
            by_field.setdefault(f, []).append((entry.get("step"), ev.get("value")))
    out = []
    for f, seen in by_field.items():
        if len(seen) > 1 and len({checks.normalized(f, v) for _, v in seen}) > 1:
            out.append({"field": f, "steps": [label for label, _ in seen],
                        "values": [_shown(v) for _, v in seen]})
    return out


def next_extract_rev(conn, content_hash: str) -> int:
    """Next extract_rev for a content_hash (1 for the first extraction): past
    every reading in `extraction_attempt` AND every evidence row of the
    document. A reviewer's edit is stamped `MAX(evidence.extract_rev) + 1`
    (`gate._apply_edits`), a rev no attempt row holds, and counted from the
    attempts alone the next reading took that same rev (Task 5a review, the
    shared counter; plan Task 5b). An edit made while a reading is in flight
    can still take that reading's rev, which GATE's reviewer hold covers
    (`gate._reviewer_held`)."""
    return conn.execute(
        "SELECT GREATEST("
        " (SELECT COALESCE(MAX(extract_rev), 0) FROM extraction_attempt"
        "  WHERE content_hash = %s),"
        " (SELECT COALESCE(MAX(e.extract_rev), 0) FROM evidence e"
        "  JOIN document d ON d.doc_id = e.doc_id WHERE d.content_hash = %s)"
        ") + 1 AS rev",
        (content_hash, content_hash),
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
    null when the model is unpriced -- tokens are recorded regardless.

    The `tier` column holds the call's read LABEL (`llm.READ_STEPS`), so every
    paid call of one revision has its own row. Until 2026-10-12 the doc-class
    call and the T1 read both wrote `T1`, and the conflict clause below dropped
    the second of them without a word."""
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
