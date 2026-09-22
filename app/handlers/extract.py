"""extract.doc handler: run the tier ladder, persist the attempt, emit validate.doc.

Importing this module registers the handler (overriding the S0.1 no-op). Two
transports for the LLM tiers, both injectable so the handler is unit-tested with
stubs:

  * sync  (`llm`)          — one blocking messages.create per tier.
  * batch (`batch_client`) — messages.batches; the SAME extract.doc job
                             self-reschedules via queue.defer until its batch_ref
                             batch is ended (handbook 5, C9). No new job type
                             (invariant 7); batch_ref is the crash-safe state
                             (invariant 9). On a non-done batch the handler
                             returns `{"_deferred": True, ...}` — the runner
                             commits the batch_ref write and skips finish.

The live clients are built lazily (constructing needs no key; only the call
does), so import and tests never need a key. Mode is env-selected
(`DENTALIA_EXTRACT_MODE=batch`) with sync the default.
"""

from __future__ import annotations

import logging
import os

from app import playbooks as playbooks_mod
from app import queue
from app.config import load_config
from app.extract import batching
from app.extract import llm as llm_mod
from app.extract import pdf, text_store, tiers
from app.extract import t0_templates
from app.extract.t0_templates import t0_extract
from app.handlers import archiving, register
from app.results import Result

log = logging.getLogger(__name__)

#: Guard 1. Prefixed to every hint block. A hint is a prior, not a fact: the
#: manufacturer behind it is T0's own text match on all but the fetch path, and
#: a hint that ASSERTS identity turns a wrong guess into a confident wrong
#: answer. This sentence is what makes the block safe to send at all.
_HINT_OVERRIDE = (
    "The notes below describe how one manufacturer usually writes its "
    "documents. They are context, not fact. If this document disagrees with "
    "them in any way — a different company, a different format — ignore them "
    "entirely and report only what the document itself says."
)


def _hints_for(playbook, missing: list[str], authoritative: bool) -> str | None:
    """The hint block for this request, or None.

    Guard 2 is scoped to `manufacturer` ONLY: its hint is withheld when
    `manufacturer` is itself in `missing` (i.e. when identity is the very
    thing being asked) — that is the one circularity the guard exists to stop
    (spec `docs/superpowers/specs/2026-08-18-playbooks-across-stages-design.md`
    line 275). Every OTHER field's hint passes through regardless of whether
    that field is in `missing`. This is deliberate, not an oversight: a hint
    reaching the model for a field that is NOT being asked cannot answer that
    field either, since the prompt only ever requests `missing` fields — so a
    blanket "withhold whatever is asked" rule (tried first, then reverted)
    does not add safety, it only ever removes the one case where a field's
    hint is useful at all. `missing` here is computed once per document,
    before any tier runs (see the call site in `handle_extract_doc`), so it is
    the full pursued-field list on a fresh document — `manufacturer` is always
    in it, meaning the `manufacturer` hint is always withheld at that call.

    Guard 3: `authoritative` is True only when the manufacturer came from the
    requesting group (established before extraction). False means T0 guessed it
    from the document text, and the block hedges accordingly.

    Returns None when nothing survives, rather than a block of boilerplate with
    no content in it."""
    if not playbook or not playbook.extract_hints:
        return None
    withhold_manufacturer = "manufacturer" in missing
    applicable = {k: v for k, v in playbook.extract_hints.items()
                  if not (k == "manufacturer" and withhold_manufacturer)}
    if not applicable:
        return None

    if authoritative:
        lead = f"This document belongs to {playbook.manufacturer}."
    else:
        lead = (f"This document may be from {playbook.manufacturer} — that is "
                f"inferred from the text, not established.")

    body = "\n".join(f"- {k}: {v}" for k, v in sorted(applicable.items()))
    return f"{_HINT_OVERRIDE}\n\n{lead}\n\n{body}"


def _default_llm():
    # The key is passed explicitly rather than left to the SDK's environment
    # lookup: config resolves it from env OR config.toml, and a TOML-only key
    # used to be silently ignored here. Constructing still needs no key (only
    # the call does), so a missing one is caught at worker startup instead.
    import anthropic

    cfg = load_config()
    return llm_mod.AnthropicLlm(
        anthropic.Anthropic(api_key=cfg.connection.anthropic_api_key), cfg.models
    )


def _default_batch_client():
    import anthropic

    cfg = load_config()
    return llm_mod.AnthropicBatchClient(
        anthropic.Anthropic(api_key=cfg.connection.anthropic_api_key), cfg.models
    )


def _finalize(conn, content_hash, group_id, doc_class, tiers_used, fields,
              usage_log=(), archive_url=None, steering=None, hinted=False) -> dict:
    """Write the extraction_attempt and emit validate.doc (shared completion path
    for both transports). Dedupe includes group_id + extract_rev (C3).

    `usage_log` carries the SYNC path's per-call costs -- one invocation holds
    every tier -- and they are written at the same extract_rev as the attempt. The
    BATCH path passes nothing here: it writes each tier's cost row as that tier
    resolves (_advance_or_defer), because usage only realises per defer cycle. rev
    is stable across a run: no attempt row exists until this finalize, so
    next_extract_rev returns the same value at every mid-run cost write.

    `steering` is the resolved playbook (Task 7 guard 4) -- recorded whenever a
    playbook steered ANY part of the read, T0 lexicons included, not only a
    hint block; `hinted` (whether a hint block was actually built) rides only
    the job result for the KPI board, never the attempt row."""
    rev = tiers.next_extract_rev(conn, content_hash)
    tiers.write_extraction_attempt(
        conn, content_hash, tiers_used, fields, extract_rev=rev,
        playbook_slug=steering.slug if steering else None,
        playbook_rev=steering.rev if steering else None,
    )
    for call in usage_log:
        tiers.write_extraction_cost(conn, content_hash, rev, call)
    # archive_url rides the payload the whole way to GATE. Rebuilding this
    # payload without it dropped the StorageAdapter handle here, so GATE fell
    # back to fetch_log.url_normalized -- the corpus SOURCE path -- and every
    # document of the GC pilot recorded a path that no other process can open
    # and a corpus re-dump destroys. Omitted rather than nulled when absent:
    # payloads are additive-only and the fallback still serves paths that
    # genuinely have no value to carry.
    # A file the doc-class call refused goes no further. The attempt row above is
    # still written -- what we read and why is worth keeping, and re-fetching the
    # same bytes must not re-pay for the call -- but nothing downstream should
    # ever see a candidate for it.
    #
    # This is deliberately narrower than it looks: `business-doc` and `msds` DO
    # still emit, as they always have. `doc_class` is not persisted and neither
    # VALIDATE nor GATE reads it, so for those two the short-circuit only saves
    # LLM calls; the empty-field candidate still flows. Widening it to them is a
    # separate change with its own test surface ([extract-emits-for-refused-classes]).
    if doc_class == tiers.NOT_A_DOCUMENT:
        return {
            "doc_class": doc_class,
            "tiers_used": tiers_used,
            "extract_rev": rev,
            "field_count": 0,
            "hinted": hinted,
            "emitted": None,
        }

    validate_payload = {
        "content_hash": content_hash, "group_id": group_id, "extract_rev": rev,
    }
    if archive_url:
        validate_payload["archive_url"] = archive_url
    queue.enqueue(
        conn,
        "validate.doc",
        validate_payload,
        dedupe_key=f"validate:{content_hash}:{rev}:{group_id}",
    )
    return {
        "doc_class": doc_class,
        "tiers_used": tiers_used,
        "extract_rev": rev,
        "field_count": len(fields),
        "hinted": hinted,
    }


def _handle_sync(conn, doc, filename, content_hash, group_id, llm, result=None,
                 companion_url=None, hints=None, steering=None) -> dict:
    tier_client = llm if llm is not None else _default_llm()
    doc_class, fields, tiers_used = tiers.run_extraction(doc, filename, llm=tier_client,
                                                         result=result,
                                                         companion_url=companion_url,
                                                         hints=hints)
    return _finalize(conn, content_hash, group_id, doc_class, tiers_used, fields,
                     usage_log=getattr(tier_client, "usage_log", ()),
                     archive_url=filename, steering=steering, hinted=hints is not None)


def _handle_batch(conn, job, doc, filename, content_hash, group_id,
                  batch_client, poll_interval_s, result=None, companion_url=None,
                  hints=None, steering=None) -> dict:
    """Batch transport: advance one tier's batch per poll, deferring until ended.

    T0 runs every poll (deterministic, idempotent); the batch_ref lookup inside
    advance_batch means a tier that was already submitted is never resubmitted.
    Per-field escalation is preserved: only fields still missing after a tier are
    carried to the next tier's batch -- including the scan-forced T2, which fires
    only when a field is genuinely still open. The sync ladder has always guarded
    that (`tiers.run_extraction`); without the same guard here a scanned document
    that T0/T1 already completed submits a one-request T2 batch at vision rates
    for a field list that is empty.
    """
    if poll_interval_s is None:
        poll_interval_s = load_config().batch.poll_interval_s

    doc_class, fields = t0_extract(doc, filename, result=result,
                                   companion_url=companion_url)
    tiers_used = ["T0"]
    if doc_class in ("business-doc", "msds"):
        return _finalize(conn, content_hash, group_id, doc_class, tiers_used, fields,
                         archive_url=filename, steering=steering, hinted=hints is not None)

    missing = tiers.needs_escalation(fields)
    if missing:
        deferred = _advance_or_defer(conn, job, batch_client, content_hash, "T1",
                                     doc, missing, fields, tiers_used, poll_interval_s,
                                     hints=hints)
        if deferred is not None:
            return deferred
        missing = tiers.needs_escalation(fields)

    if missing or pdf.is_scan(doc):
        target = missing or tiers.needs_escalation(fields)
        if target:   # don't pay for a vision call with nothing to extract
            deferred = _advance_or_defer(conn, job, batch_client, content_hash, "T2",
                                         doc, target, fields, tiers_used, poll_interval_s,
                                         hints=hints)
            if deferred is not None:
                return deferred

    return _finalize(conn, content_hash, group_id, doc_class, tiers_used, fields,
                     archive_url=filename, steering=steering, hinted=hints is not None)


def _advance_or_defer(conn, job, batch_client, content_hash, tier, doc, target,
                      fields, tiers_used, poll_interval_s, hints=None):
    """Advance one tier's batch. On `done`, merge results (filtered to the fields
    this tier escalated) into `fields`, record the tier, and write this tier's
    cost row NOW (usage was captured by advance_batch's results() call this same
    invocation; a later invocation would have its own client and lose it). The
    write is idempotent (ON CONFLICT), so a retry that re-fetches is a no-op.
    Otherwise defer the job and return the `_deferred` signal; return None on done.

    The merge goes through `tiers.merge`, exactly as the sync ladder does: an
    empty answer from a later tier reports that tier's failure, not a correction,
    so it must never erase an earlier hit. `fields` is the caller's dict and is
    updated in place — merge returns a superset, so updating from it is complete.

    `hints` is forwarded to `build_requests` only as a kwarg when it is not
    None -- same reasoning as `tiers.run_extraction`'s `llm_kwargs`: the
    injected `batch_client` is a protocol object, and stubs implementing it
    predate this parameter, so passing `hints=None` unconditionally would break
    them with a TypeError even when nobody asked for a hint."""
    bc_kwargs = {"hints": hints} if hints is not None else {}
    requests = batch_client.build_requests(content_hash, tier, doc, target, **bc_kwargs)
    state, results = batching.advance_batch(conn, batch_client, content_hash, tier, requests)
    if state != "done":
        queue.defer(conn, job["id"], poll_interval_s)
        return {"_deferred": True, "waiting_on": tier, "state": state}
    fields.update(tiers.merge(fields, {f: results[f] for f in target if f in results}))
    tiers_used.append(tier)
    rev = tiers.next_extract_rev(conn, content_hash)
    for call in getattr(batch_client, "usage_log", ()):
        if call.tier == tier:
            tiers.write_extraction_cost(conn, content_hash, rev, call)
    return None


def handle_extract_doc(conn, job: dict, llm=None, batch_client=None,
                       poll_interval_s: int | None = None) -> dict:
    payload = job["payload"]
    content_hash = payload["content_hash"]
    group_id = payload.get("group_id")
    filename = payload["archive_url"]
    # Set by BACKFILL when the playbook says this manufacturer splits a document
    # across two files (Komet: declaration + `_RA_812_Liste_` annex). Absent for
    # every other document, and `.get` keeps it that way -- consumers tolerate
    # unknown payload fields but never missing ones.
    companion_url = payload.get("companion_archive_url")

    # ONE open per execution. The parsed-text sidecar and the tier ladder both
    # read the same immutable archived file, so it is opened here and handed to
    # both; store_text opens for itself only when no caller gives it a document.
    #
    # That open is also the discriminator this handler used to buy with a
    # database round trip. store_text reports source == "none" for two
    # situations its return value cannot tell apart -- (a) a genuine scan with
    # no text layer, for which a document_text row IS written, and (b) a PDF
    # that could not be opened at all, where it logs a warning and writes no row
    # (see its docstring: "Never raises on an unreadable PDF") -- so the handler
    # re-read document_text to find out which it had. Owning the open answers it
    # where it actually arises: past this point the document is open, so
    # source == "none" can only mean a real scan.
    try:
        doc = pdf.open_doc(filename)
    except Exception as exc:
        # "unreadable_pdf" is not in the closed ANOMALY_KINDS vocabulary -- an
        # unreadable PDF is not "a scan", and inventing a kind on the spot would
        # let the vocabulary drift silently. Counted and noted instead, so it is
        # never silent (CLAUDE.md), pending a deliberate decision on whether it
        # deserves its own kind in both app/results.py and
        # migrations/019_job_result.sql. No further extraction is possible
        # without an openable document, so nothing downstream (validate.doc) is
        # emitted here -- there is nothing to validate, and no document_text row
        # is written either.
        log.warning("extract.doc: cannot open %s: %s", filename, exc)
        return {
            "text": {"source": "none", "pages": 0, "chars": 0},
            "counts": {"unreadable_pdf": 1},
            "notes": [f"unreadable PDF, no document_text row written: {content_hash}"],
        }

    text_summary = text_store.store_text(conn, content_hash, filename, doc=doc)

    # One envelope for this execution's data anomalies. T0 reports into it
    # directly (a playbook template that matched and parsed nothing); the scan
    # case below adds to the same one, so both leave through `anomalies` and the
    # runner records them in the transaction that finishes the job.
    r = Result()

    # The steering playbook, resolved ONCE per execution and threaded through to
    # `_finalize` (Task 7 guard 4). `group_id` alone is NOT enough to call the
    # manufacturer authoritative: `archiving.manufacturer_for_group` returns the
    # literal string "unknown" when the group carries no
    # canonical_manufacturer, and that string is not a manufacturer -- treating
    # it as one would send an unclaimed override into `for_manufacturer` and, on
    # a name collision, hedge Guard 3's lead sentence as "established" for a
    # value nobody actually established. `authoritative` instead tracks whether
    # `override` itself resolved: True only when the GROUP's manufacturer
    # produced the steering playbook; False (including group_id is None, the
    # live path for every backfilled document) means T0's own text match did,
    # and the hint block must hedge accordingly.
    group_manufacturer = (
        archiving.manufacturer_for_group(conn, group_id) if group_id is not None else None
    )
    override = (
        playbooks_mod.for_manufacturer(group_manufacturer)
        if group_manufacturer and group_manufacturer != "unknown"
        else None
    )
    steering = t0_templates.steering_playbook(
        "\n".join(pdf.page_texts(doc)), override=override,
    )
    authoritative = override is not None
    # Nothing is extracted yet at this point, so `needs_escalation({})` is the
    # full pursued-field list, which always includes `manufacturer` -- meaning
    # guard 2 (narrow: only the `manufacturer` hint is ever withheld, see
    # `_hints_for`) withholds the `manufacturer` hint at this call, every time.
    # Every other field's hint is unaffected by this call and reaches the
    # model regardless of whether that field ends up escalating.
    hints = _hints_for(steering, tiers.needs_escalation({}), authoritative=authoritative)

    if batch_client is not None:
        result = _handle_batch(conn, job, doc, filename, content_hash, group_id,
                               batch_client, poll_interval_s, result=r,
                               companion_url=companion_url, hints=hints, steering=steering)
    else:
        result = _handle_sync(conn, doc, filename, content_hash, group_id, llm, result=r,
                              companion_url=companion_url, hints=hints, steering=steering)
    result["text"] = text_summary

    # Only the genuine-scan case reaches here -- the unreadable case already
    # returned above. Skipped on a self-deferred poll cycle: this only makes
    # sense once, when the job is actually finishing.
    if text_summary["source"] == "none" and not result.get("_deferred"):
        r.anomaly("no_text_layer", subject=content_hash)

    # A deferred poll cycle is not the end of the job: the batch path re-runs T0
    # on the next cycle and re-reports whatever it finds, so nothing is lost by
    # holding the anomalies back until the execution that actually finishes.
    if not result.get("_deferred"):
        anomalies = r.as_dict()["anomalies"]
        if anomalies:
            result.setdefault("anomalies", []).extend(anomalies)

    return result


def _dispatch(conn, job):
    if os.environ.get("DENTALIA_EXTRACT_MODE", "sync").lower() == "batch":
        return handle_extract_doc(conn, job, batch_client=_default_batch_client())
    return handle_extract_doc(conn, job)


register("extract.doc", _dispatch)
