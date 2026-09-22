"""GATE — the ONLY writers to the registry (inv. 1): gate.candidate (machine
disposition) + gate.apply (human decision, S0.3 step 4).

gate.candidate, handbook §7 — two-level disposition:
  * document level: production iff score>=HIGH and ref_gate and no flags; else
    staged (>=MED, non-blocking flags) or manual (<MED or a blocking flag). A
    manual disposition still writes a STAGED document plus a manual_task.
  * link level (C5 / inv. 3): each item link gated INDEPENDENTLY by its own
    match_basis — name-family / fetch-context are ALWAYS staged, structurally
    (also the item_document CHECK). A production doc may carry a mix.

Calibration (G2) is applied HERE: score = min effective confidence over the
required fields, effective = raw x factor(model_id, field) (identity default).
Every written value carries complete evidence (inv. 2) — an incomplete required
field is a hard error, never a partial write. Idempotent (at-least-once
delivery): re-delivery upserts the same rows and does not duplicate evidence /
audit. C6 supersession is deferred to S1.0.

S0.3 note: archive_url rides in the candidate payload (the registry needs it and
extraction_attempt doesn't store it); threading it through extract->validate->gate
is a followup for when RESOLVE/FETCH make the chain live.
"""

from __future__ import annotations

import datetime
import json

from psycopg.types.json import Json

from app import manufacturers, queue
from app.config import load_config
from app.extract import tiers
from app.handlers import register

# match bases that MAY carry a production link (inv. 3). Everything else — notably
# name-family / fetch-context — is always staged, enforced here AND by the CHECK.
#: Bases that may reach `production`. The DB CHECK (migrations 005/021) is the
#: structural half — it bars name-family, fetch-context and ref-catalogue — and
#: this tuple is the other: a basis missing from here stages at any confidence,
#: silently. `ref-item` (C12, migration 023) belongs here for the same reason
#: `ref-list` does: both are formed on the SCOPED path, where the manufacturer
#: is established before any article number is compared. `map-supplier` (task
#: 5, Komet coverage map) belongs here for the same structural reason: the
#: manufacturer is established by the source document itself, before any
#: article number is compared against it.
TRUSTED_BASES = ("ref-list", "ref-item", "udi", "basic-udi-di", "mfr-scope", "manual", "map-supplier")
# flags that force manual review (can never auto-write). downgrade-uncomparable /
# ref-unscoped only CAP at staged; they are not blocking. no-item-identifier
# ([validate-itemid], S1.0) is blocking: a doc with no ref_list/basic_udi_di
# cannot be linked to the catalogue and must not silently stage.
#
# "older-than-current" is blocking again (task 5 fix round 1, reviewed 2026-08-07):
# VALIDATE keeps appending it whenever a candidate is unambiguously older, on TOP
# of the additive superseded_by_doc_id/"auto-superseded" it also emits. GATE's
# auto-file fast path (see handle_gate_candidate) only fires when superseded_by_doc_id
# is present AND no OTHER blocking flag survives (no-item-identifier still forces
# manual even for an old doc — nothing established it covers the same subject) AND
# validity_from's own calibrated confidence clears cfg.gate.high (a garbled OCR
# year must not drive a permanent, unreviewed write). Whenever that guard fails,
# "older-than-current" being in this tuple is what keeps the candidate off
# `production` and routes it to `manual` exactly as before this task — removing it
# from BLOCKING_FLAGS was the round-1 defect: it let a failed guard fall through
# with no safety net at all.
# "same-date-revision" (2026-09-04): VALIDATE found a production document for the
# same subject, type and regulation carrying the SAME validity_from. No ordering
# exists, so neither may supersede the other by machine; a person picks.
BLOCKING_FLAGS = ("older-than-current", "same-date-revision", "no-item-identifier",
                  "evidence-page-missing", "date-insane", "manufacturer-unresolved")

#: Flags that hold a candidate at `staged` for a human, but never force the
#: manual queue: a real unknown about THIS document that a reviewer can settle.
#:
#: `device-enumeration` ([qa-device-enumeration], Denis 2026-08-24): a QMS/QA
#: certificate whose own text enumerates its covered devices with model codes.
#: VALIDATE raises it only on mfr-binding candidates, where flags are not
#: classed at all — `_handle_mfr_binding` refuses the C16 machine bind on it
#: directly. It is classified here anyway so that if it ever rides a normal-
#: route candidate it holds at `staged` (a reviewer can settle whether the
#: enumeration is the whole line) rather than relying on the unclassified-flag
#: default.
#: `not-a-device-document` (2026-09-03): the document is not evidence about a
#: medical device at all -- no MDR/MDD citation and not a QMS certificate. C16
#: brought `_is_md_document` in for the machine binding path and left the REF
#: branch asking only whether the NUMBERS matched, so a machinery or cosmetics
#: declaration whose REF list overlapped the catalogue reached production on an
#: article-level basis. Capping rather than blocking for the same reason the
#: mfr path refuses rather than rejects: a person can say what the tuple cannot.
CAPPING_FLAGS = ("downgrade-uncomparable", "expiry-on-certless-doc",
                 "ref-catalogue", "ref-unscoped", "multi-manufacturer-ref",
                 "device-enumeration", "not-a-device-document")

#: Flags that describe CONTEXT, not a defect. Recorded on the candidate and
#: shown in the UI, but they do not gate the disposition.
#:
#: Until 2026-08-13 the production branch required an EMPTY flag list, so these
#: gated exactly as hard as a missing page cite. Measured on the GC corpus: 43
#: documents carrying all 309 live links -- 100% of the registry's coverage --
#: sat staged on `cert-unresolved` / `multi-group-match` alone, while the 74
#: documents flagged `no-ref-overlap` held zero links between them, because
#: "no REF overlap" means there was nothing to link in the first place. Both
#: capping flags were already documented as non-blocking where they are raised
#: (`validate.py`: "caps at staged (non-blocking) and self-corrects once the
#: certificate is gated"); only this rule disagreed. Denis's ruling 2026-08-13.
#:
#: `multi-group-match` lost its reason to cap on 2026-08-13: a document now links
#: every matching member across ALL groups under the pinned manufacturer, so no
#: group is chosen arbitrarily any more.
#: `ref-list-possibly-truncated` (tiers.REF_TRUNCATION_FLAG) is informational for
#: the same reason: an LLM list that came back at exactly the prompt cap MAY be a
#: partial reading, but every code it does name still links its item correctly.
#: Gating on it would withhold real, evidenced coverage over a suspicion — the
#: degradation Denis ruled out on 2026-08-13. It is named through the constant so
#: the flag exists in exactly one place.
INFORMATIONAL_FLAGS = ("no-ref-overlap", "multi-group-match", "cert-unresolved",
                       "auto-superseded", tiers.REF_TRUNCATION_FLAG,
                       # Informational by ruling (Denis, 2026-09-03): it reports
                       # that nothing in the evidence supports an `ISO` type, and
                       # nothing that auto-writes today stops auto-writing. The
                       # counts decide later whether a capping version is
                       # warranted -- the measured population is 5 documents,
                       # none at production.
                       tiers.ISO_WITHOUT_STANDARD_FLAG,
                       # Informational by the same ruling. It says T0 could not
                       # read a labelled date because of the page's LAYOUT, not
                       # that the date we hold is wrong -- whatever value the
                       # document ends up with came from a paid tier that read
                       # the page properly, or the field is simply absent and
                       # the existing missing-field rules already handle that.
                       # Gating here would withhold coverage over a note about
                       # our own parser.
                       tiers.DATE_LABEL_NOT_ADJACENT_FLAG)

REQUIRED_FIELDS = ("type", "regulation", "coverage_scope")

#: Dispositions that settle a document without a person, and must therefore
#: close any review task still open against it.
#:
#: `handle_gate_candidate` resolved tasks on NO route until 2026-08-17 --
#: `gate.apply` was the only caller of `_resolve_manual_tasks`, because for a
#: long time a human decision was the only way a document could stop needing
#: one. C15 (`filed`) and C16 (machine binding) both broke that assumption, and
#: the C16 replay measured the cost: 10 tasks left open against documents that
#: had already reached `production` (6) or `filed` (4). The registry was correct
#: and the queue lied about how much work was left, which is precisely what
#: those two rulings exist to prevent.
#:
#: `staged` and `manual` are deliberately absent: both mean a person still has
#: to settle this document, and `staged` is the one a reviewer acts on.
SETTLED_DISPOSITIONS = ("production", "superseded", "filed", "mfr-bound")

#: The regulations that make a document evidence about a medical device.
MD_REGULATIONS = ("MDR", "MDD")

#: Document types allowed to reach a machine binding with NO medical-device
#: regulation named. Exactly one: ISO 13485 is a quality-management standard
#: rather than an instrument issued under MDR or MDD, so `regulation = n.a.` is
#: the correct reading of it, and a QMS certificate genuinely does cover the
#: manufacturer's whole line -- the case manufacturer-scope binding exists for.
#: Live: doc 1472 (IVOCLAR, 1.069 items) and doc 117 (GC, 356), both correct.
#:
#: `EC` is deliberately absent though it can also read `n.a.`: a certificate is
#: issued UNDER a regime and should name it, so one that does not has been read
#: incompletely. GC's UKCA certificate (doc 116, "Part II of The Medical Devices
#: Regulations 2002") is a real device regime the extractor does not model yet --
#: which is a judgement for a reviewer, not for this tuple.
QMS_TYPES_WITHOUT_REGULATION = ("ISO",)


def _is_out_of_catalogue(payload, fields, flags) -> bool:
    """Whether this document covers nothing Dentalia holds (C15, migration 025).

    A manufacturer publishes documents for their whole product line; a
    distributor buys a slice of it. So a perfectly read document routinely
    covers no item we stock -- on the IVOCLAR backfill, Dentalia held 6 of the
    1.605 REF codes the staged documents named. Such a document belongs in
    `filed`, not `staged`: `staged` means "a human should confirm", and nobody
    can confirm coverage of a product the company does not sell.

    All three conditions are load-bearing:

    * NO links. One linkable item is coverage, and coverage is never filed.
    * `no-ref-overlap`, which VALIDATE emits only when the manufacturer DID
      resolve and no member's article numbers matched. Empty links for any
      other reason -- an unresolved manufacturer, a document naming no REFs at
      all -- is a different state and keeps its existing disposition. This is
      the condition that separates "we know whose document this is and we don't
      stock it" from "we don't know what this is".
    * NOT manufacturer-scope. ISO 13485 / QMS certificates name no item BY
      DESIGN and already have C4's path: one-time human binding, then
      derivation at `mfr-scope`. Waiting on a decision is not the same as
      having none to make, and filing them would silently retire that queue.

    Caller additionally requires no surviving BLOCKING flag and a score at or
    above MED, so a badly-read document still goes to `manual` rather than
    being quietly filed away."""
    return (
        not payload.get("links")
        and "no-ref-overlap" in flags
        and _val(fields, "coverage_scope") != "manufacturer"
    )


def _gates_disposition(flags) -> bool:
    """True if any flag must keep this candidate off `production`.

    Deliberately a membership test against the two NAMED classes rather than
    `not flags`: an unrecognised flag gates. A new flag someone forgets to
    classify then behaves like the old rule (conservative) instead of silently
    becoming informational and gating nothing."""
    known_safe = set(INFORMATIONAL_FLAGS)
    return any(f not in known_safe for f in flags)


def _val(fields: dict, name: str):
    ev = fields.get(name)
    return ev.get("value") if isinstance(ev, dict) else None


def _read_ext(conn, content_hash: str, extract_rev: int):
    row = conn.execute(
        "SELECT fields FROM extraction_attempt WHERE content_hash=%s AND extract_rev=%s",
        (content_hash, extract_rev),
    ).fetchone()
    return row["fields"] if row else None


def _archive_url(conn, payload: dict, content_hash: str) -> str:
    """Where our stored copy lives. Prefer an explicit payload value (live FETCH,
    S1.4, will pass a distinct Drive archive url); otherwise KEEP the handle the
    registry already holds; only then fall back to the fetch ledger, which
    records content_hash -> stored location (backfill writes the local path
    there). A value is mandatory — document/evidence.archive_url is NOT NULL.

    The registry-before-ledger order is a defect fix (2026-08-24): a candidate
    without a handle is not authority to refresh. For a live fetch the ledger's
    url_normalized is the remote SOURCE url, and resolving to it made the
    upsert + evidence heal replace doc 843's stored /archive/RENFERT/... handle
    with https://www.renfert.com/... — the exact thing invariant 2 and
    end-state goal 2 (an archive under our control) forbid."""
    url = payload.get("archive_url")
    if url:
        return url
    row = conn.execute(
        "SELECT archive_url FROM document WHERE content_hash=%s", (content_hash,)
    ).fetchone()
    if row and row["archive_url"]:
        return row["archive_url"]
    row = conn.execute(
        "SELECT url_normalized FROM fetch_log WHERE content_hash=%s "
        "ORDER BY fetched_at DESC NULLS LAST LIMIT 1",
        (content_hash,),
    ).fetchone()
    if row:
        return row["url_normalized"]
    raise ValueError(f"gate.candidate: no archive_url for {content_hash} (payload or fetch_log)")


#: Flag raised when a required field's T1/T2 evidence carries no page cite.
#: Blocking, so the candidate is archived and reviewed rather than lost.
PAGE_MISSING_FLAG = "evidence-page-missing"


def _require_complete_evidence(fields: dict) -> list[str]:
    """inv. 2 for the fields a production value needs. Returns the flags a
    recoverable gap raises; an unusable bundle still raises.

    The line is what a HUMAN could still act on. A field with no value, or with
    no verbatim/tier/confidence, leaves nothing to review -- that is a hard error
    and never writes, unchanged.

    A missing PAGE is different. The value is there and the verbatim is there;
    only the citation is absent. Invariant 2's own wording governs production --
    "Every production value carries complete evidence" -- so the correct response
    is to bar production and route to review, not to destroy the document.
    Raising killed 2 of 130 GC documents outright (roughly 18 across the full
    corpus) for one missing integer, and put them on the dead-jobs board where
    the archived file and every other extracted field were unreachable.

    T0 filename-derived and T3 human evidence are legitimately pageless and are
    not flagged ([evidence-page] ruling 2026-07-31).
    """
    flags: list[str] = []
    for f in REQUIRED_FIELDS:
        ev = fields.get(f)
        if not isinstance(ev, dict) or ev.get("value") in (None, "", []):
            raise ValueError(f"gate.candidate: required field '{f}' has no value (inv. 2)")
        if not ev.get("verbatim") or not ev.get("tier") or ev.get("conf") is None:
            raise ValueError(f"gate.candidate: required field '{f}' evidence incomplete (inv. 2)")
        if ev["tier"] in ("T1", "T2") and ev.get("page") is None and PAGE_MISSING_FLAG not in flags:
            flags.append(PAGE_MISSING_FLAG)
    return flags


def _score(fields: dict, calib_map: dict) -> float:
    return min(
        tiers.calibrate(fields[f]["conf"], fields[f].get("model_id"), f, calib_map)
        for f in REQUIRED_FIELDS
    )


def _field_confidence(fields: dict, name: str, calib_map: dict) -> float | None:
    """Calibrated confidence of ONE field, for the guards that hinge on a single
    value rather than on the whole candidate's score.

    Two such guards exist: task 5's auto-file, which turns on `validity_from`,
    and §7b's auto-bind, which turns on `manufacturer`. Neither field is in
    REQUIRED_FIELDS (`_score` never covers them), so each is read and calibrated
    separately — through the same tiers.calibrate() path `_score` uses, so that
    a model-tier swap cannot silently change what "confident" means here.

    Missing or unparseable confidence returns None and therefore FAILS every
    caller's guard; it is never treated as passing."""
    ev = fields.get(name)
    if not isinstance(ev, dict) or ev.get("conf") is None:
        return None
    try:
        raw_conf = float(ev["conf"])
    except (TypeError, ValueError):
        return None
    return tiers.calibrate(raw_conf, ev.get("model_id"), name, calib_map)


def _source_url_for(conn, content_hash: str) -> str | None:
    """Where this document came from, with its ORIGINAL filename.

    `document.source_url` had no writer at all, so it was NULL on every row and
    the true name was unrecoverable: the archive keeps only a sanitized form
    (`archiving._sanitize` folds every run of non-`[\\w.-]` into `_`, turning
    `gce_certification_MDR 778483.pdf` into `..._MDR_778483.pdf`), and
    manufacturers identify their documents by exactly the spaces and
    punctuation that fold away (Denis, 2026-08-12).

    Read from `fetch_log` rather than threaded down the payload from
    `backfill.scan`/`fetch.url`. Both hold the value, but the ledger is keyed on
    `content_hash` -- which is what GATE already has -- so it also answers on
    the REPLAY path, where VALIDATE and GATE re-run over stored
    `extraction_attempt` rows and no live payload exists.

    Ordered because a hash may legitimately have several rows (the same
    document re-dumped at a new path): the FIRST place we saw it is the stable
    answer, and `url_normalized` breaks a same-timestamp tie so the value never
    flips between runs. Returns None freely -- a document whose provenance we
    cannot name is still a document, and this must never fail a GATE write."""
    row = conn.execute(
        "SELECT url_normalized FROM fetch_log WHERE content_hash = %s "
        "ORDER BY fetched_at ASC NULLS LAST, url_normalized ASC LIMIT 1",
        (content_hash,),
    ).fetchone()
    return row["url_normalized"] if row else None


def _date_or_none(v):
    """A date column takes a parseable date or nothing at all.

    The insert below casts with `%s::date`, so any string the extraction
    produced went straight to Postgres. A Straumann IFU returned
    `validity_from = "2025"` -- read off "(c) Institut Straumann AG, 2025. All
    rights reserved." -- and the job died on `InvalidDatetimeFormat: invalid
    input syntax for type date: "2025"`, retried into the same error and
    dead-lettered (2026-08-17). An LLM must not be able to destroy a job by
    returning a badly-shaped string.

    VALIDATE now flags this as `date-insane` (BLOCKING, so the document goes to
    a human), and that is where the finding belongs. This is the second line of
    defence, not a substitute: GATE also runs on the replay path over stored
    `extraction_attempt` rows written before that rule existed, and a candidate
    reaching `manual` is still INSERTED, so the flag alone never prevented the
    crash.

    Dropping to NULL loses nothing. Every value keeps its own evidence row with
    the verbatim the model quoted, so a reviewer sees exactly what it read and
    why it was refused; the column simply declines to hold a non-date."""
    if v is None:
        return None
    if isinstance(v, str):
        try:
            return datetime.date.fromisoformat(v)
        except ValueError:
            return None
    return v


def _bindable_manufacturer(conn, name: str | None) -> str | None:
    """The canonical name safe to store on `document`, or None.

    `document.canonical_manufacturer` is an FK to `manufacturer(canonical_name)`
    (migration 053), and the review picker offers
    `COALESCE(a.canonical_name, m.manufacturer_raw)` -- so it can offer a bare
    BC vendor code that has no `manufacturer` row. All 366 offerable entities
    have one as of 2026-08-31, so this cannot fire on live data; it exists
    because an unguarded write would raise ForeignKeyViolation inside the
    runner's transaction and take the whole gate decision down with it.
    Refusing to store a binding is recoverable; losing the decision is not.
    """
    if not name or not name.strip():
        return None
    row = conn.execute(
        "SELECT canonical_name FROM manufacturer WHERE canonical_name = %s",
        (name.strip(),),
    ).fetchone()
    return row["canonical_name"] if row else None


def _upsert_document(conn, fields, content_hash, archive_url, status, *,
                      supersedes=None, related_doc_id=None, cert_doc_id=None,
                      canonical_manufacturer=None) -> int:
    # supersedes / referenced_doc_id (C6) / cert_doc_id (task 4) are STICKY on
    # conflict (COALESCE to the stored value): VALIDATE computes them on every
    # candidate, staged or production, but a later re-delivery that happens
    # not to recompute one (e.g. the other regulation's doc got rejected
    # meanwhile, or the cited certificate simply isn't held yet) must not
    # erase an already-recorded relationship — these are additive facts, not
    # status.
    return conn.execute(
        """
        INSERT INTO document (type, regulation, validity_from, validity_to, coverage_scope,
                              basic_udi_di, cert_number, stated_class, content_hash,
                              archive_url, status,
                              supersedes, referenced_doc_id, cert_doc_id, source_url,
                              canonical_manufacturer)
        VALUES (%s, %s, %s::date, %s::date, %s, %s, %s, %s, %s, %s, %s::doc_status, %s, %s, %s, %s, %s)
        ON CONFLICT (content_hash) DO UPDATE SET
            -- NEVER MOVE BACKWARD. staged < filed < production, and
            -- `rejected` / `superseded` are terminal to a machine.
            --
            -- This used to protect `production` and `superseded` only, which
            -- left `filed` and `rejected` writable by ANY re-delivery -- an
            -- ordinary re-extraction, `repair_ref_list`, `repair_label_dates`.
            -- Measured live 2026-09-04 by a 67-document re-emission: four
            -- documents moved, three `filed` -> `staged` and one that a person
            -- had REJECTED (doc 837, `admin`, 2026-08-25) put back into the
            -- review queue, all four silently and with no audit row.
            --
            -- `filed` is NOT simply protected: `filed` -> `production` is a
            -- legitimate promotion when a re-extraction finds items (see
            -- `status = "production" if n_items else "filed"` below), so only
            -- the backward step from `filed` to `staged` is refused.
            status = CASE
                WHEN document.status IN ('production', 'superseded', 'rejected')
                    THEN document.status
                WHEN document.status = 'filed' AND EXCLUDED.status = 'staged'
                    THEN document.status
                ELSE EXCLUDED.status END,
            type = EXCLUDED.type, regulation = EXCLUDED.regulation,
            validity_from = EXCLUDED.validity_from, validity_to = EXCLUDED.validity_to,
            coverage_scope = EXCLUDED.coverage_scope, basic_udi_di = EXCLUDED.basic_udi_di,
            cert_number = EXCLUDED.cert_number, archive_url = EXCLUDED.archive_url,
            -- always-refresh, with type/regulation/coverage_scope rather than
            -- sticky with the relationship pointers: a re-extraction that reads
            -- the class differently is a CORRECTION and should land. Nothing is
            -- lost by that -- evidence rows are per-rev and append-only, so the
            -- earlier reading stays queryable with its own verbatim.
            stated_class = EXCLUDED.stated_class,
            supersedes = COALESCE(EXCLUDED.supersedes, document.supersedes),
            referenced_doc_id = COALESCE(EXCLUDED.referenced_doc_id, document.referenced_doc_id),
            cert_doc_id = COALESCE(EXCLUDED.cert_doc_id, document.cert_doc_id),
            -- sticky for the same reason as the three above: provenance is an
            -- additive fact, so a re-delivery that cannot name the source (a
            -- replay, a pruned ledger) must not erase a name we already hold.
            source_url = COALESCE(EXCLUDED.source_url, document.source_url),
            -- sticky with the four above, and the most load-bearing of them:
            -- this one is a HUMAN DECISION (or C16's), not a computed pointer.
            -- A re-delivery that cannot recompute it -- a replay, a candidate
            -- whose manufacturer no longer resolves -- must not discard the
            -- answer a reviewer already gave. A newer non-null binding still
            -- wins, which is what makes re-binding the correction path.
            canonical_manufacturer = COALESCE(EXCLUDED.canonical_manufacturer,
                                              document.canonical_manufacturer)
        RETURNING doc_id
        """,
        (_val(fields, "type"), _val(fields, "regulation"),
         _date_or_none(_val(fields, "validity_from")),
         _date_or_none(_val(fields, "validity_to")),
         _val(fields, "coverage_scope"), _val(fields, "basic_udi_di"),
         _val(fields, "cert_number"), _val(fields, "stated_class"),
         content_hash, archive_url, status,
         supersedes, related_doc_id, cert_doc_id, _source_url_for(conn, content_hash),
         canonical_manufacturer),
    ).fetchone()["doc_id"]


def _backresolve_citing_docs(conn, doc_id, cert_number):
    """A newly-held certificate retroactively answers every DoC that cited it.
    Without this, resolution would depend on fetch order: a declaration
    processed before its certificate would stay unresolved forever."""
    if not cert_number:
        return 0
    # A trailing R-code is stripped from both sides, exactly as
    # `validate._resolve_cited_certificate` does it (Denis's ruling, 2026-08-19).
    # It has to be the same rule in both directions or the identical pair of
    # numbers matches or misses depending only on which document happened to be
    # gated first -- which is the fetch-order dependence this function exists to
    # remove. `Rev. NN` is left alone here for the same reason it is there.
    rows = conn.execute(
        "UPDATE document SET cert_doc_id=%s "
        "WHERE type='DoC' AND cert_doc_id IS NULL "
        "  AND regexp_replace(cert_number, '\\s+R[0-9]+$', '') "
        "      = regexp_replace(%s, '\\s+R[0-9]+$', '') "
        "RETURNING doc_id",
        (doc_id, cert_number),
    ).fetchall()
    return len(rows)


def _backresolve_here(conn, doc_id, fields) -> int:
    """Back-resolve if this document is itself a certificate. Both routes.

    Task 4's back-resolution had exactly one call site, in
    `handle_gate_candidate`'s main path -- and no EC/ISO certificate has ever
    reached it. §7b sends every manufacturer-scope document (which is what an
    ISO 13485 or an EC certificate IS, by construction) to
    `_handle_mfr_binding`, and `handle_gate_candidate` returns there before the
    back-resolution line. Measured on the live registry 2026-08-27:
    `cert_doc_id` set on 2 of 769 documents, and both were written by
    VALIDATE's forward resolver -- back-resolution had never once fired.

    That is the real reason cert inheritance addressed a population of zero.
    `[cert-citation-misses-by-a-revision-suffix]` blamed the ` R000` suffix,
    which was a genuine second defect and was fixed 2026-08-19; this one sat
    underneath it and would have kept the count at zero even so.

    The binding decision deliberately does NOT gate this. What answers a
    declaration is that we HOLD the certificate it cited, not whether we could
    match the issuer to a catalogue code -- and `document_effective_expiry`
    already refuses to inherit an expiry from a certificate that is not
    production or superseded, so a staged one records the link and lends
    nothing until a human publishes it.
    """
    if not fields or _val(fields, "type") not in ("EC", "ISO"):
        return 0
    return _backresolve_citing_docs(conn, doc_id, _val(fields, "cert_number"))


def _is_current_rev(conn, content_hash: str, extract_rev: int) -> bool:
    """Whether this candidate carries the newest extraction for its document.

    `repair-ref-list` and any re-extraction append a new `extract_rev` and
    re-emit, so two candidates for one document can be in flight at once. The
    older one is a superseded reading and must not write links -- measured
    2026-08-13, document 235's rev-1 candidate (REF list truncated to 40 codes)
    left 19 links standing that rev 2 had correctly resolved to none.
    """
    row = conn.execute(
        "SELECT max(extract_rev) AS newest FROM extraction_attempt WHERE content_hash=%s",
        (content_hash,),
    ).fetchone()
    return row is None or row["newest"] is None or extract_rev >= row["newest"]


def _retract_unsupported_links(conn, doc_id, links, job, group_id=None) -> int:
    """Retract the STAGED links this candidate re-evaluated and no longer claims.

    A link must not outlive the extraction that justified it: GATE only ever
    upserted, so coverage could grow and never shrink, and a corrected reading
    could not take back a wrong link. On a compliance registry that is the wrong
    direction of error -- it asserts coverage the evidence no longer supports.

    Scoped to the candidate's own group when it carries one (defect fix,
    2026-08-24). A group-scoped candidate only ever (re-)evaluates links for
    members of ITS group (VALIDATE's `_ref_gate_for_group` / the fetch-context
    fallback), so "not claimed by this candidate" says nothing about another
    group's links. Measured on the RENFERT smoke test: doc 843's group-1641
    candidate retracted group 1326's staged link to 18510000 -- with N groups
    sharing one document, staged links thrashed until only the last claimant's
    survived. A group-less candidate (backfill/email that found no links: the
    document self-identified and VALIDATE evaluated the whole catalogue under
    the pinned manufacturer) keeps the full retraction scope -- that is the
    doc-235 case this function was built for.

    STAGED only, deliberately. `production` means a human approved the document
    (`gate.apply approve` -> `_promote_pending_links`) or bound a manufacturer
    (`mfr-scope`, written production), so auto-retracting one would silently undo
    a human decision and contradict the PRD's "link provenance is immutable at
    production". Those keep standing and are a review question, not a machine
    one. `staged` is exactly the set nobody has ruled on yet.

    Append-only per invariant 4: status moves to `retracted` (distinct from
    `rejected`, which records a HUMAN saying no), the row and its `match_basis`
    survive, and each retraction is its own audit event.
    """
    kept = [l["item_ref"] for l in links]
    if group_id is None:
        retracted = conn.execute(
            "UPDATE item_document SET status='retracted' "
            "WHERE doc_id=%s AND status='staged' AND NOT (item_ref = ANY(%s)) "
            "RETURNING item_ref",
            (doc_id, kept),
        ).fetchall()
    else:
        retracted = conn.execute(
            "UPDATE item_document SET status='retracted' "
            "WHERE doc_id=%s AND status='staged' AND NOT (item_ref = ANY(%s)) "
            "AND item_ref IN (SELECT item_ref FROM item_group_member WHERE group_id=%s) "
            "RETURNING item_ref",
            (doc_id, kept, group_id),
        ).fetchall()
    for r in retracted:
        _audit(conn, "link-retracted", doc_id, "gate", job, item_ref=r["item_ref"])
    return len(retracted)


#: Link statuses a machine candidate may never overwrite (C17).
#:
#: `production` was always here: status per inv. 4, match_basis/udi because
#: basis is provenance — a rev-N+1 candidate that lost the REF list arrives
#: with fetch-context, and overwriting would trip the C5 CHECK (production +
#: untrusted basis) and dead-letter an otherwise valid job.
#:
#: `rejected` joined it with C17, and the hole it closes predates C17 by a
#: long way: `gate.apply reject` has cascaded links to `rejected` since v3,
#: and because only `production` was sticky, the very next candidate naming
#: the same (item_ref, doc_id) rewrote the row back to `staged`. A machine
#: silently un-rejecting what a person refused is the one direction this
#: registry must never move in. `gate.apply reopen-link` is the only way out,
#: and it requires a named human.
#:
#: `retracted` is deliberately ABSENT. It is a machine state meaning "the
#: current extraction stopped claiming this link" (`_retract_unsupported_links`),
#: not a decision — so a later extraction that claims it again SHOULD revive it,
#: and no human decision targets it either.
STICKY_LINK_STATUSES = ("production", "rejected")


def _upsert_link(conn, item_ref, doc_id, udi, basis, status) -> None:
    conn.execute(
        """
        INSERT INTO item_document (item_ref, doc_id, udi, match_basis, status)
        VALUES (%s, %s, %s, %s, %s::link_status)
        ON CONFLICT (item_ref, doc_id) DO UPDATE SET
            status = CASE WHEN item_document.status = ANY(%s)
                          THEN item_document.status ELSE EXCLUDED.status END,
            match_basis = CASE WHEN item_document.status = ANY(%s)
                               THEN item_document.match_basis
                               ELSE EXCLUDED.match_basis END,
            udi = CASE WHEN item_document.status = ANY(%s)
                       THEN item_document.udi ELSE EXCLUDED.udi END
        """,
        (item_ref, doc_id, udi, basis, status,
         list(STICKY_LINK_STATUSES), list(STICKY_LINK_STATUSES), list(STICKY_LINK_STATUSES)),
    )


def _assert_chain_identity(conn, newer_id, older_id) -> None:
    """C6 (inv. 5): a chain pointer may only join two documents with an identical
    (type, regulation), re-read LIVE from the registry rather than trusted from
    the payload — and BOTH routes to a pointer go through here.

    The payload cannot be trusted because it is a snapshot. VALIDATE scopes its
    `_current_production_doc` lookup by (type, regulation) when it computes
    `supersedes` / `superseded_by_doc_id`, but the job then waits in the queue,
    and either document's type or regulation can move underneath it meanwhile:
    `_upsert_document`'s ON CONFLICT rewrites both columns unconditionally (only
    `status` is sticky), so a re-extraction re-types even a production document
    with no human involved, and `gate.apply approve` accepts human edits to both.
    A mismatch here therefore means the pointer would join two documents that are
    no longer the same subject — MDR filed into an MDD chain, which invariant 5
    forbids outright — so it is a hard error, never a silent skip (CLAUDE.md:
    nothing wrong is silent). The handler raising rolls back its partial writes
    and the job dead-letters for a human, per app/workers/runner.py.
    """
    new_doc = conn.execute(
        "SELECT type, regulation FROM document WHERE doc_id=%s", (newer_id,)
    ).fetchone()
    old_doc = conn.execute(
        "SELECT type, regulation FROM document WHERE doc_id=%s", (older_id,)
    ).fetchone()
    if new_doc is None:
        raise ValueError(f"gate: superseding doc_id {newer_id} not found")
    if old_doc is None:
        raise ValueError(f"gate: supersedes target doc_id {older_id} not found")
    if old_doc["type"] != new_doc["type"] or old_doc["regulation"] != new_doc["regulation"]:
        raise ValueError(
            f"gate: C6 violation — doc {newer_id} ({new_doc['type']}/{new_doc['regulation']}) "
            f"cannot supersede {older_id} ({old_doc['type']}/{old_doc['regulation']})"
        )


def _apply_supersession(conn, doc_id, supersedes_id, decided_by, job) -> None:
    """C6 (inv. 5): a document may supersede only one with an identical
    (type, regulation) — re-checked here in the writer (`_assert_chain_identity`).
    Sets the OLD doc to 'superseded' + superseded_by; idempotent under
    at-least-once delivery (an update that would set the same values is a no-op,
    so no duplicate audit event) and append-only (a superseded doc is never
    un-superseded — see _upsert_document's status CASE)."""
    _assert_chain_identity(conn, doc_id, supersedes_id)
    changed = conn.execute(
        "UPDATE document SET status='superseded', superseded_by=%s "
        "WHERE doc_id=%s AND (status <> 'superseded' OR superseded_by IS DISTINCT FROM %s) "
        "RETURNING doc_id",
        (doc_id, supersedes_id, doc_id),
    ).fetchone()
    if changed is not None:
        _audit(conn, "supersede", supersedes_id, decided_by, job, detail={"superseded_by": doc_id})


def _insert_evidence(conn, doc_id, extract_rev, fields, archive_url) -> None:
    """One evidence row per field that carries a value, scoped to the extraction
    revision that produced it (evidence.extract_rev, migration 014 — [gate-evidence]
    ruling 2026-07-31). Append-only + idempotent: NOT EXISTS guard on
    (doc_id, field, extract_rev) so re-delivery of the SAME revision never
    duplicates, while a re-extraction at a NEWER revision always gets its own
    evidence row instead of being silently skipped by an older revision's row —
    closing the invariant-2 break where a production doc's field value could be
    overwritten while its evidence stayed pinned to a stale extraction. 'Current'
    evidence for a field is the row with MAX(extract_rev).

    A field may carry its OWN `archive_url`, and then that is the handle its row
    gets rather than the document's. One value can legitimately be read from a
    different file than the document it belongs to: Komet issues a declaration
    (`..._RA_810_DoC_EU_SIGNED.pdf`) whose product list lives in a separate annex
    (`..._RA_812_Liste_DoC.pdf`), and the declaration's `ref_list` is read off
    that annex. Invariant 2 asks where the value was READ, so pointing such a row
    at the declaration would send a reviewer to a page holding no list at all."""
    for field, ev in fields.items():
        if not isinstance(ev, dict):
            continue
        value = ev.get("value")
        if value in (None, "", []):
            continue
        field_url = ev.get("archive_url") or archive_url
        value_text = value if isinstance(value, str) else json.dumps(value)
        conn.execute(
            """
            INSERT INTO evidence (doc_id, field, value, archive_url, page, verbatim,
                                  tier, model_id, confidence, extracted_at, extract_rev)
            SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), %s
            WHERE NOT EXISTS (
                SELECT 1 FROM evidence WHERE doc_id=%s AND field=%s AND extract_rev=%s
            )
            """,
            (doc_id, field, value_text, field_url, ev.get("page"), ev.get("verbatim", ""),
             ev.get("tier", ""), ev.get("model_id"), ev.get("conf", 0.0), extract_rev,
             doc_id, field, extract_rev),
        )
        # Heal the storage handle on a row the guard above skipped. `_upsert_document`
        # already does this for document.archive_url; without the same here, replaying
        # a document whose handle was wrong repaired the document and left all its
        # evidence pointing at the corpus SOURCE path -- 731 rows after the GC pilot,
        # paths the next re-dump destroys, on the column invariant 2 requires.
        #
        # ONLY the handle. `value`, `verbatim`, `page`, `tier`, `model_id` and
        # `confidence` are what the model actually read; rewriting them on
        # re-delivery would falsify evidence rather than repair a pointer. A genuine
        # new reading arrives as a new extract_rev with its own row.
        # Heals to the FIELD's handle, not the document's: healing a
        # companion-sourced row back to the document would be the same
        # falsification this block exists to prevent, in the other direction.
        conn.execute(
            "UPDATE evidence SET archive_url=%s "
            "WHERE doc_id=%s AND field=%s AND extract_rev=%s AND archive_url IS DISTINCT FROM %s",
            (field_url, doc_id, field, extract_rev, field_url),
        )


def _snapshot(job: dict) -> dict:
    out = {}
    for k in ("id", "type", "payload", "dedupe_key", "claimed_by", "claimed_at", "created_at", "priority"):
        v = job.get(k)
        out[k] = v.isoformat() if hasattr(v, "isoformat") else v
    return out


def _note_detail(payload: dict) -> dict | None:
    """`{"note": ...}` for a `gate.apply` payload carrying a non-empty `note`,
    else None. The field is optional and additive, so its absence must leave the
    audit row exactly as it was before the field existed."""
    note = payload.get("note")
    return {"note": note} if isinstance(note, str) and note.strip() else None


def _audit(conn, event, doc_id, decided_by, job, item_ref=None, detail=None) -> None:
    conn.execute(
        "INSERT INTO audit_log (event, doc_id, item_ref, decided_by, via_job, job_snapshot, detail) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (event, doc_id, item_ref, decided_by, job.get("id"), Json(_snapshot(job)),
         Json(detail) if detail is not None else None),
    )


def _open_gate_task(conn, doc_id) -> bool:
    return conn.execute(
        "SELECT 1 FROM manual_task WHERE kind='gate-manual' AND doc_id=%s AND status='open'",
        (doc_id,),
    ).fetchone() is not None


def _push_manual(conn, doc_id, group_id, payload) -> None:
    if _open_gate_task(conn, doc_id):
        return  # one open task per doc (idempotent under at-least-once delivery)
    conn.execute(
        "INSERT INTO manual_task (kind, doc_id, group_id, payload) VALUES ('gate-manual', %s, %s, %s)",
        (doc_id, group_id, Json(payload)),
    )


def _is_md_document(fields: dict) -> bool:
    """Whether this document is evidence about a MEDICAL DEVICE at all.

    C16 checked that the manufacturer resolved and never that the document was
    about a device, and manufacturer scope turns that omission catalogue-wide.
    Measured the day C16 shipped: doc 1494, a PrograMill PM5 MILLING UNIT
    declaration cited under "2006/42/EG Machinery Directive, 2014/53/EU Radio
    Equipment", was bound to all 1.069 IVOCLAR medical-device items at
    production, 2026-08-17 09:05:26. The document is real and correctly read; it
    is simply not evidence of anything about a ceramic block.

    The corpus is full of these -- cosmetics (1223/2009), low voltage
    (2014/35/EU), EMC (2014/30/EU), machinery (2006/42/EC), RoHS (2011/65/EU) --
    and extraction reads them faithfully, returning `regulation = n.a.` and a
    confidence of 0.85-0.90 that says "I am unsure because it is not there".
    Thirteen such documents sit at `staged` today, held back only by the 0.95
    threshold, which is doing the right thing for the wrong reason.

    Applied ONLY to the machine binding path. Whether these documents belong in
    the registry at all is a client ruling (asked 2026-08-17, question 20) and
    the general gate is `[extract-scope]`; until both land, the narrow rule here
    is that the machine does not bind one to an entire catalogue unreviewed. A
    refusal is not a rejection -- it falls to the review queue, where a person
    can say what the tuple cannot."""
    if _val(fields, "regulation") in MD_REGULATIONS:
        return True
    return _val(fields, "type") in QMS_TYPES_WITHOUT_REGULATION


def _handle_mfr_binding(conn, job) -> dict:
    """§7b: a manufacturer-scope document (ISO 13485, EC certificate, brand-wide
    DoC) names no item by construction, so it is bound to a MANUFACTURER rather
    than matched to REFs.

    Until 2026-08-17 every one of these staged and raised a review task. That
    made the queue the bottleneck for a decision containing nothing to decide:
    when the document's own manufacturer string resolves to exactly one curated
    canonical, binding it is a table lookup, not a judgement. Denis's ruling
    2026-08-17 -- "if we found the manufacturer, and the cert is not describing
    an item, it should be related to the manufacturer anyway".

    Three outcomes, in order:

    * one canonical + at least one MD item -> `production`, mfr-scope links to
      every MD item under EVERY BC code of that manufacturer, audited, no task.
    * one canonical + no MD items -> `filed` (C15). The bind is correct and
      links nothing; staging it would park a row no human can action. NB this
      currently also catches manufacturers whose BC class column is merely
      unpopulated -- see [mfr-bind-empty-class].
    * anything else (name missing, unknown, or ambiguous — or the candidate
      carrying `device-enumeration`, [qa-device-enumeration]: the certificate's
      own text lists the devices it covers, so binding it line-wide would
      assert coverage the paper denies) -> `staged` + exactly ONE review task,
      the pre-existing behaviour, regardless of how many groups requested the
      binding.

    The guard is the alias hit, not the model's self-belief: a garbled read does
    not accidentally match a curated table. Confidence is a secondary sanity
    check at `cfg.gate.med` -- enough to establish we read a name rather than
    noise, and deliberately NOT `cfg.gate.high`, which is still the uncalibrated
    0.95 placeholder ([gate-thresholds]) and would reject GC's three T0 reads at
    0.90 for no reason connected to their correctness.
    """
    p = job["payload"]
    fields = _read_ext(conn, p["content_hash"], p["extract_rev"])
    if fields is None:
        raise LookupError(f"gate.candidate: no extraction {p['content_hash']}:{p['extract_rev']}")
    _require_complete_evidence(fields)   # binding route stages regardless

    cfg = load_config()
    calib_map = getattr(cfg, "calibration", {}) or {}
    archive_url = _archive_url(conn, p, p["content_hash"])

    # VALIDATE canonicalizes before it emits, so its answer wins where it has
    # one; the extracted string is the fallback for candidates whose payload
    # predates identity extraction.
    # Manufacturer scope must be something the document SAYS, not something the
    # extractor fell back to. `derive_coverage_scope` returns manufacturer at
    # 1.0 on `type=ISO/EC` and at 0.6 for a DoC whose REF list T0 could not
    # parse -- the field's own confidence is the discriminator, and the comment
    # there already names this exact hazard ("a wrong `manufacturer` links the
    # doc to the entire catalogue"). Held to `high`, not `med`: this is the
    # guard standing between an unparsed REF list and a catalogue-wide write.
    scope_stated = (_field_confidence(fields, "coverage_scope", calib_map) or 0.0) >= cfg.gate.high

    name = p.get("manufacturer") or _val(fields, "manufacturer")
    canonicals = manufacturers.resolve_canonicals(conn, name) if name else []
    confident = (_field_confidence(fields, "manufacturer", calib_map) or 0.0) >= cfg.gate.med
    # [qa-device-enumeration] (Denis 2026-08-24, "Guard + clean 310"): VALIDATE
    # read the stored text and found the certificate enumerating its covered
    # devices — by its own words it is NOT manufacturer-wide, and the machine
    # must not assert catalogue-wide coverage the paper limits to a device
    # list. Doc 310 (Kiwa Cermet MED 31385) names three device types with
    # model codes plus "valid only for the above mentioned Medical Devices"
    # and was bound to 356 GC items. Refusal is a review, not a rejection:
    # gate.apply bind-manufacturer stays available to a person who judges the
    # enumeration to be the manufacturer's whole line.
    flags = list(p.get("flags") or [])
    enumerated = "device-enumeration" in flags
    bound = (canonicals[0] if len(canonicals) == 1 and confident
             and scope_stated and not enumerated
             and _is_md_document(fields) else None)

    if bound is None:
        doc_id = _upsert_document(conn, fields, p["content_hash"], archive_url, "staged")
        _insert_evidence(conn, doc_id, p["extract_rev"], fields, archive_url)
        _backresolve_here(conn, doc_id, fields)
        task = {"route": "mfr-binding", "manufacturer": name}
        if flags:
            # the review queue must explain itself: the reviewer sees WHY the
            # machine declined, not just that it did.
            task["flags"] = flags
        _push_manual(conn, doc_id, p.get("group_id"), task)
        out = {"disposition": "mfr-binding", "doc_id": doc_id}
        if flags:
            out["flags"] = flags
        return out

    prev = conn.execute(
        "SELECT status FROM document WHERE content_hash=%s", (p["content_hash"],)
    ).fetchone()
    prev_status = prev["status"] if prev else None

    codes = manufacturers.item_codes_for(conn, bound)
    n_items = conn.execute(
        "SELECT count(*) c FROM item_mirror WHERE md_flag IS TRUE AND manufacturer_raw = ANY(%s)",
        (codes,),
    ).fetchone()["c"] if codes else 0

    # [mfr-bind-empty-class], Denis 2026-08-27: "an empty BC class column means
    # UNKNOWN, not 'not a device'." No MD item is therefore two different
    # findings wearing one face, and `md_flag` is tri-state precisely so they
    # can be told apart (`app/handlers/ingest.py:164-170`):
    #
    #   FALSE -> BC DECLASSIFIED this item. A positive statement that it is not
    #            a medical device, so filing the document is correct. INGEST
    #            never writes FALSE for a blank -- a brand-new non-MD row is
    #            not mirrored at all -- so this value only ever means "BC said
    #            no about something we already held".
    #   NULL  -> the class column is blank. A gap in the export, not an answer.
    #
    # Filing on NULL is filing on absence of data, and on 2026-08-27 it was
    # absence of MOST of the data: 11.693 of 15.958 mirrored items were NULL
    # and ZERO were FALSE. So this branch stages everything today and starts
    # filing again by itself once BC populates the column -- which is why it is
    # written as the tri-state test rather than as a temporary "never file".
    #
    # `product_class` cannot serve here and was measured before this was
    # written: it is empty-string on exactly the 11.693 NULL rows and holds a
    # class on exactly the 4.265 TRUE ones, never disagreeing with md_flag, so
    # it carries no third state to read.
    declassified = conn.execute(
        "SELECT count(*) c FROM item_mirror WHERE md_flag IS FALSE AND manufacturer_raw = ANY(%s)",
        (codes,),
    ).fetchone()["c"] if codes else 0

    # 053: every branch below reached a RESOLVED manufacturer -- that is what
    # separates them from the `bound is None` branch above, which records
    # nothing because nothing was decided. All three store it, and `filed` is
    # the one that matters most: 443 of the 540 link-less documents are filed,
    # and this is what makes them queryable under a manufacturer at all.
    storable = _bindable_manufacturer(conn, bound)

    if not n_items and not declassified:
        doc_id = _upsert_document(conn, fields, p["content_hash"], archive_url, "staged",
                                  canonical_manufacturer=storable)
        _insert_evidence(conn, doc_id, p["extract_rev"], fields, archive_url)
        _backresolve_here(conn, doc_id, fields)
        # The manufacturer DID resolve -- that is what separates this from the
        # `bound is None` branch above, and the reviewer needs to be told so,
        # or the task reads as "we could not identify this" when in fact we
        # could and it is the catalogue that is silent.
        task = {"route": "mfr-binding", "manufacturer": bound,
                "reason": "md-class-unknown"}
        if flags:
            task["flags"] = flags
        _push_manual(conn, doc_id, p.get("group_id"), task)
        out = {"disposition": "mfr-binding", "doc_id": doc_id, "manufacturer": bound,
               "reason": "md-class-unknown"}
        if flags:
            out["flags"] = flags
        return out

    status = "production" if n_items else "filed"
    doc_id = _upsert_document(conn, fields, p["content_hash"], archive_url, status,
                              canonical_manufacturer=storable)
    _insert_evidence(conn, doc_id, p["extract_rev"], fields, archive_url)
    _backresolve_here(conn, doc_id, fields)
    # The binding IS the decision this task was raised to obtain. Leaving it open
    # is how the review board came to show 24 items against 14 real ones.
    _resolve_manual_tasks(conn, doc_id, "gate")

    if n_items:
        _derive_mfr_scope_links(conn, doc_id, bound)
        if prev_status != "production":
            _audit(conn, "bind-manufacturer", doc_id, "gate", job, detail={"manufacturer": bound})
            _audit(conn, "production-write", doc_id, "gate", job)
        return {"disposition": "mfr-bound", "doc_id": doc_id,
                "manufacturer": bound, "links": n_items}

    if prev_status != "filed":
        _audit(conn, "filed", doc_id, "gate", job, detail={"manufacturer": bound})
    return {"disposition": "filed", "doc_id": doc_id, "manufacturer": bound, "links": 0}


def handle_gate_candidate(conn, job: dict) -> dict:
    p = job["payload"]
    if p.get("route") == "mfr-binding":
        return _handle_mfr_binding(conn, job)

    # A superseded reading is a no-op, decided BEFORE anything is read or
    # written. The link writes further down have always been guarded this way,
    # but the document upsert was not, so rev N-1 could still overwrite rev N's
    # type, regulation and dates through ON CONFLICT DO UPDATE -- the same
    # corruption the link guard prevents, one table over.
    #
    # It also ends a permanent-failure loop. Two GC safety data sheets carried
    # `coverage_scope: "item"` in their rev-1 extraction, from before the API
    # schema enumerated the value; re-extraction appended a clean rev 3, but the
    # stale rev-1 candidates kept replaying that stored value into an INSERT the
    # CHECK constraint rejects, dead-lettering on every attempt with no
    # possible path to success (2026-08-14).
    if not _is_current_rev(conn, p["content_hash"], p["extract_rev"]):
        return {"skipped": "superseded-rev", "content_hash": p["content_hash"],
                "extract_rev": p["extract_rev"]}

    fields = _read_ext(conn, p["content_hash"], p["extract_rev"])
    if fields is None:
        raise LookupError(f"gate.candidate: no extraction {p['content_hash']}:{p['extract_rev']}")
    evidence_flags = _require_complete_evidence(fields)   # inv. 2 — before any write
    archive_url = _archive_url(conn, p, p["content_hash"])

    cfg = load_config()
    calib_map = getattr(cfg, "calibration", {}) or {}
    score = _score(fields, calib_map)
    ref_gate = bool(p.get("ref_gate"))
    # GATE's own evidence findings join VALIDATE's. They are blocking, so the
    # document is archived and reviewed rather than dead-lettered.
    flags = list(p.get("flags") or []) + evidence_flags

    # The REF branch's device-type guard. `_is_md_document` was wired to the
    # mfr-scope path only when C16 shipped (2026-08-17); the scoped path asked
    # whether the article numbers matched and never whether the document was
    # about a device. Raised as a CAPPING flag rather than tested inline in the
    # production branch so the staging board can explain the refusal -- the
    # review queue must say WHY the machine declined, not just that it did.
    if not _is_md_document(fields):
        flags.append("not-a-device-document")

    # task 5 (fix round 1): an unambiguously older candidate is only auto-filed
    # into the superseded chain along a NARROW fast path — all three must hold:
    #   1. superseded_by_doc_id present (VALIDATE's C7 older-than-current case)
    #   2. no BLOCKING_FLAGS other than "older-than-current" itself survive —
    #      e.g. no-item-identifier still forces manual for an old doc, because
    #      nothing established it covers the same subject at all
    #   3. validity_from's own calibrated confidence clears cfg.gate.high — the
    #      date driving this irreversible write is not in _score()'s
    #      REQUIRED_FIELDS, so a garbled OCR year would otherwise sail through
    #      with zero confidence check
    #   4. VALIDATE did not flag the date as insane. Confidence alone does not
    #      cover this: a T0 date carries a flat high confidence that clears (3)
    #      no matter how wrong the value is, and `date-insane` is the only
    #      signal that a date-shaped string is not a plausible date. Scanned
    #      corpora reach T2 vision OCR, where a garbled year is the expected
    #      failure, and this write is irreversible.
    #      **This check is redundant as of 2026-08-13** — `date-insane` joined
    #      BLOCKING_FLAGS in the flag-class amendment, so (2) now catches it.
    #      Kept deliberately: it is the guard that must not depend on which
    #      class a flag currently sits in, and reclassifying `date-insane` back
    #      to capping would silently reopen an irreversible write path.
    #      (Two claims in this comment went stale the same day and were
    #      corrected: `validity_from` is no longer excluded from escalation
    #      either — see `tiers.py` `_ESCALATE_ALWAYS`.)
    # Any failure falls through to the normal chain, where "older-than-current"
    # (back in BLOCKING_FLAGS) forces manual exactly as it did before this task
    # — never production, never a silent downgrade.
    auto_superseded = p.get("superseded_by_doc_id")
    supersede_skipped = False
    auto_supersede_ok = (
        auto_superseded is not None
        and not any(f in BLOCKING_FLAGS and f != "older-than-current" for f in flags)
        and "date-insane" not in flags
        and (_field_confidence(fields, "validity_from", calib_map) or 0.0) >= cfg.gate.high
    )
    if auto_supersede_ok:
        disposition = "superseded"
    elif score >= cfg.gate.high and ref_gate and not _gates_disposition(flags):
        disposition = "production"
    elif (score >= cfg.gate.med
          and not any(f in BLOCKING_FLAGS for f in flags)
          and _is_out_of_catalogue(p, fields, flags)):
        # C15: read correctly, attributed correctly, covering nothing we sell.
        # Ordered AFTER production so a document that does link is never filed,
        # and BEFORE staged so it never enters a queue no human can action.
        disposition = "filed"
    elif score >= cfg.gate.med and not any(f in BLOCKING_FLAGS for f in flags):
        disposition = "staged"
    else:
        disposition = "manual"
    doc_status = {"production": "production", "superseded": "superseded",
                  "filed": "filed"}.get(disposition, "staged")

    prev = conn.execute(
        "SELECT status FROM document WHERE content_hash=%s", (p["content_hash"],)
    ).fetchone()
    prev_status = prev["status"] if prev else None

    # 053: only an UNAMBIGUOUS resolution is stored. Zero canonicals means we
    # cannot name the manufacturer; more than one means a human must choose, and
    # guessing here would attach one company's document to another's products --
    # the same wrong-merge this file's alias lookup folds case rather than
    # fuzzes to avoid. This path never guesses; the picker exists for the rest.
    _printed = _val(fields, "manufacturer")
    _canonicals = manufacturers.resolve_canonicals(conn, _printed) if _printed else []
    _bound_here = (_bindable_manufacturer(conn, _canonicals[0])
                   if len(_canonicals) == 1 else None)

    doc_id = _upsert_document(conn, fields, p["content_hash"], archive_url, doc_status,
                              supersedes=p.get("supersedes"), related_doc_id=p.get("related_doc_id"),
                              cert_doc_id=p.get("cert_doc_id"),
                              canonical_manufacturer=_bound_here)

    # task 5: the incoming candidate is OLDER than the current production doc
    # (the INVERSE of C6 supersession, computed by VALIDATE) — write the chain
    # pointer directly rather than through _apply_supersession, which re-checks
    # C6 in the other direction (new doc supersedes old) and would not apply
    # here. The existing production doc is untouched; only this doc's own
    # superseded_by is set. Guarded on auto_supersede_ok, not just
    # superseded_by_doc_id's presence — a failed guard means this candidate is
    # `manual`, not `superseded`, and must not carry a chain pointer while
    # awaiting review. Guarded + audited the same way _apply_supersession is:
    # idempotent under at-least-once delivery, no duplicate audit event on
    # redelivery.
    if auto_supersede_ok:
        # Same live (type, regulation) re-read `_apply_supersession` performs, in
        # the mirrored direction (here the TARGET is the newer document and this
        # candidate is the older one). The payload's superseded_by_doc_id was
        # computed by VALIDATE against the target as it stood then; the target can
        # be re-typed while this job waits in the queue (a re-extraction of its
        # content_hash, or a human edit through gate.apply — both rewrite
        # document.type/regulation), and a stale pointer would file an MDR
        # document into an MDD chain. Both routes to a chain pointer are guarded
        # identically, and both raise rather than skip.
        _assert_chain_identity(conn, auto_superseded, doc_id)
        # `AND status='superseded'` is load-bearing, not belt-and-braces:
        # _upsert_document's status CASE is sticky, so a doc_id already sitting
        # at 'production' keeps that status even when this candidate computed
        # `superseded`. Without the predicate the row becomes production AND
        # carrying a chain pointer — a contradiction /api/documents/{doc_id}
        # would serve to external consumers as
        # {"status": "production", "superseded_by": N}. The predicate fires
        # exactly when _upsert_document really did land this row as superseded.
        changed = conn.execute(
            "UPDATE document SET superseded_by=%s "
            "WHERE doc_id=%s AND superseded_by IS NULL AND status='superseded' "
            "RETURNING doc_id",
            (auto_superseded, doc_id),
        ).fetchone()
        if changed is not None:
            _audit(conn, "auto-superseded", doc_id, "gate", job)
        elif prev_status == "production":
            # Never silent (CLAUDE.md): the candidate qualified for auto-filing
            # but the row it targets is already production under some other
            # decision, so the chain pointer was withheld deliberately. Reported
            # on the job result, which Task 3 persists to job.result.
            supersede_skipped = True

    # task 4 back-resolution: this document may itself be a certificate that
    # earlier-processed declarations cited before we held it. Shared with the
    # mfr-binding route, which is the one real certificates actually take.
    _backresolve_here(conn, doc_id, fields)

    # A superseded reading must not touch the registry's links in EITHER
    # direction. Out-of-order delivery is real (a repair appends a new rev and
    # re-emits, so two candidates for one document can be in flight), and
    # letting rev N-1 write after rev N is how a corrected document reacquires
    # the links its correction removed.
    if _is_current_rev(conn, p["content_hash"], p["extract_rev"]):
        for link in p.get("links", []):
            basis = link["match_basis"]
            link_status = ("production"
                           if basis in TRUSTED_BASES and doc_status == "production" and score >= cfg.gate.high
                           else "staged")
            _upsert_link(conn, link["item_ref"], doc_id, link.get("udi"), basis, link_status)
        _retract_unsupported_links(conn, doc_id, p.get("links", []), job,
                                   group_id=p.get("group_id"))

    _insert_evidence(conn, doc_id, p["extract_rev"], fields, archive_url)

    if disposition == "production" and prev_status != "production":
        _audit(conn, "production-write", doc_id, "gate", job)
    elif disposition == "filed" and prev_status != "filed":
        # Audited like any other disposition change. Filing removes a document
        # from human view, so "why is this not in the queue" has to be
        # answerable from the trail alone (invariant 10) -- otherwise the
        # quietest outcome would be the only unexplained one.
        _audit(conn, "filed", doc_id, "gate", job)
    elif disposition == "manual":
        _push_manual(conn, doc_id, p.get("group_id"),
                     {"flags": flags, "tier_attempts": fields})

    if disposition in SETTLED_DISPOSITIONS:
        _resolve_manual_tasks(conn, doc_id, "gate")

    # C6: a candidate landing directly on production may act on its supersession
    # target immediately — the STAGED case waits for gate.apply approve, since a
    # staged doc must never actually supersede anything (§7).
    if disposition == "production" and p.get("supersedes") is not None:
        _apply_supersession(conn, doc_id, p["supersedes"], "gate", job)

    out = {"disposition": disposition, "doc_id": doc_id, "score": round(score, 4),
           "links": len(p.get("links", []))}
    # Report the flags that decided this disposition. GATE raises its own on top
    # of VALIDATE's (evidence findings, the device-type guard), so a job result
    # showing only the verdict hides the reason -- and the reason is the part a
    # person needs when the answer is "staged" (CLAUDE.md: never silent).
    if flags:
        out["flags"] = sorted(set(flags))
    if supersede_skipped:
        out["supersede_skipped"] = "target already production"
    return out


register("gate.candidate", handle_gate_candidate)


# --- gate.apply (human decisions, §8) -------------------------------------------

# Whitelisted editable document columns. A human edit lands as T3 evidence
# (verbatim = the human input, conf = 1.0) AND updates the column. Anything else
# (content_hash, archive_url, status, ...) is not editable through a review edit.
_EDITABLE = {
    "type": "type = %s", "regulation": "regulation = %s",
    "validity_from": "validity_from = %s::date", "validity_to": "validity_to = %s::date",
    "coverage_scope": "coverage_scope = %s", "basic_udi_di": "basic_udi_di = %s",
    "cert_number": "cert_number = %s",
}


def _apply_edits(conn, doc_id, edits) -> None:
    archive_url = conn.execute(
        "SELECT archive_url FROM document WHERE doc_id=%s", (doc_id,)
    ).fetchone()["archive_url"]
    # A human edit isn't tied to any extraction — stamp it past the highest
    # extract_rev this doc has evidence for, so "current" (MAX(extract_rev) per
    # field) always resolves to the human's value over any machine extraction.
    rev = conn.execute(
        "SELECT COALESCE(MAX(extract_rev), 0) + 1 AS rev FROM evidence WHERE doc_id=%s",
        (doc_id,),
    ).fetchone()["rev"]
    for field, value in (edits or {}).items():
        if field not in _EDITABLE:
            raise ValueError(f"gate.apply: '{field}' is not an editable field")
        conn.execute(f"UPDATE document SET {_EDITABLE[field]} WHERE doc_id=%s", (value, doc_id))
        # human edit is its own evidence tier (T3, full confidence)
        conn.execute(
            "INSERT INTO evidence (doc_id, field, value, archive_url, page, verbatim, "
            "tier, model_id, confidence, extracted_at, extract_rev) "
            "VALUES (%s, %s, %s, %s, NULL, %s, 'T3', NULL, 1.0, now(), %s)",
            (doc_id, field, str(value), archive_url, str(value), rev),
        )


def _promote(conn, doc_id) -> None:
    conn.execute("UPDATE document SET status='production' WHERE doc_id=%s", (doc_id,))


def _promote_pending_links(conn, doc_id) -> None:
    # trusted-basis staged links follow the doc into production; name-family /
    # fetch-context stay staged (C5), enforced here and by the item_document CHECK.
    conn.execute(
        "UPDATE item_document SET status='production' "
        "WHERE doc_id=%s AND status='staged' AND match_basis = ANY(%s)",
        (doc_id, list(TRUSTED_BASES)),
    )


def _derive_mfr_scope_links(conn, doc_id, manufacturer) -> None:
    # C4 §7b: link the doc to every MD item of the manufacturer, basis=mfr-scope,
    # production.
    #
    # The binding name reaches us in whatever spelling its source used: a human
    # picking a canonical label in the UI, or a document identifying itself as
    # "Ivoclar Vivadent AG". Until 2026-08-17 this resolved only the first --
    # it looked the name up as a `canonical_name` -- so a document's own
    # spelling matched nothing and bound ZERO items in silence. That is the
    # normal case, not the corner one: manufacturer_alias exists precisely
    # because manufacturers print their legal entity rather than the
    # catalogue's label. `raw_names_for` resolves alias -> canonical FIRST,
    # then fans back out to every BC code under that canonical, which also
    # picks up the manufacturers that span several (IVOCLAR is 001/005/275).
    codes = manufacturers.item_codes_for(conn, manufacturer) if manufacturer else []
    if not codes:
        # Raise, do not return ([gate-bind-zero-links]). Returning promoted the
        # document to production, linked nothing, and wrote bind-manufacturer +
        # production-write audit entries that read like a success -- a registry
        # asserting coverage no item received. A job that fails is visible; a
        # bind that links nothing is not. The handler runs inside the runner's
        # transaction, so this takes the promote and both audit rows down with
        # it.
        #
        # WHAT THIS GUARD DOES AND DOES NOT COVER (corrected 2026-08-31).
        # It fires on a name resolving to no BC CODES -- an unattributable
        # string. It has never fired on a name that resolves fine but whose
        # items hold no MD flag: `3SHAPE TRIOS A/S` resolves to
        # ['10004','10005'] and sails straight past. Three places in the tree
        # claimed otherwise, including the comment above `web/app.py`'s
        # MFR_BINDING_UNKNOWN_CLASS_REASON. That second case is no longer a
        # fault at all -- since 053 a manufacturer with no MD items is a
        # legitimate binding that records the decision and links nothing.
        #
        # The picker no longer narrows what can reach here (it offers every
        # catalogue entity since 2026-08-31), so this raise, `_bindable_
        # manufacturer`'s FK check, and POST /staging/{id}/apply's non-empty
        # check are the three things standing between a hand-built payload or a
        # replayed job and an unattributable bind.
        raise ValueError(
            f"gate.apply bind-manufacturer: '{manufacturer}' resolves to no BC "
            f"manufacturer codes, so the bind would link zero items"
        )
    rows = conn.execute(
        "SELECT item_ref FROM item_mirror "
        "WHERE md_flag IS TRUE AND manufacturer_raw = ANY(%s) ORDER BY item_ref",
        (codes,),
    ).fetchall()
    for r in rows:
        _upsert_link(conn, r["item_ref"], doc_id, None, "mfr-scope", "production")


def _resolve_manual_tasks(conn, doc_id, decided_by) -> None:
    conn.execute(
        "UPDATE manual_task SET status='resolved', resolved_by=%s, resolved_at=now() "
        "WHERE doc_id=%s AND status='open'",
        (decided_by, doc_id),
    )


def _doc_status(conn, doc_id) -> str | None:
    row = conn.execute("SELECT status FROM document WHERE doc_id=%s", (doc_id,)).fetchone()
    return row["status"] if row else None


#: C17 link-level decisions: (decision) -> (required link status, new status,
#: audit event). The table IS the contract — a decision absent from it cannot
#: touch a link, and every entry names the precondition it enforces.
LINK_DECISIONS = {
    #                      from        -> to            audit event
    "confirm-link": ("staged",   "production", "link-confirmed"),
    "reject-link":  ("staged",   "rejected",   "link-rejected"),
    "reopen-link":  ("rejected", "staged",     "link-reopened"),
}


def _apply_link_decision(conn, job, decision) -> dict:
    """C17: a human ruling on ONE (doc_id, item_ref) link. `document` untouched.

    C5 made the link the unit of trust and gave it a machine gate, but no human
    verb: `_promote_pending_links` publishes only trusted-basis links, and the
    `item_document_trusted_basis_ck` CHECK forbids `name-family` /
    `fetch-context` / `ref-catalogue` at production outright. `manual` was
    enumerated as a production-capable basis from v3 and NOTHING ever wrote it,
    so an untrusted-basis link had no terminal state at all — it could not be
    published, could not be refused, and sat `staged` under a `production`
    document indistinguishable from one nobody had looked at. Approving the
    DOCUMENT did not and could not change that, which reads to a reviewer as the
    decision being ignored (found on item 2222100, 2026-08-24).

    Every precondition below raises rather than skipping. A link decision is a
    registry write a person put their name to; if it cannot be carried out, the
    job dead-letters where somebody sees it (CLAUDE.md: nothing wrong is silent).
    A silent no-op here would reproduce the exact bug this closes.
    """
    p = job["payload"]
    doc_id, item_ref = p["doc_id"], p.get("item_ref")
    if not item_ref:
        raise ValueError(f"gate.apply {decision}: item_ref is required")
    want, new_status, event = LINK_DECISIONS[decision]

    # The document gate, shared by all three. Confirming coverage under a
    # document nobody vetted would publish an unreviewed reading through the
    # side door; re-opening under a rejected one would recreate the staged-link-
    # under-a-rejected-doc state the reject cascade (§7) exists to eliminate.
    doc_status = _doc_status(conn, doc_id)
    if doc_status is None:
        raise LookupError(f"gate.apply {decision}: no document {doc_id}")
    if doc_status != "production":
        raise ValueError(
            f"gate.apply {decision}: document {doc_id} is '{doc_status}', not 'production' — "
            f"a link decision may only be taken on a published document"
        )

    link = conn.execute(
        "SELECT status, match_basis FROM item_document WHERE doc_id=%s AND item_ref=%s",
        (doc_id, item_ref),
    ).fetchone()
    if link is None:
        raise LookupError(f"gate.apply {decision}: no link {item_ref} -> doc {doc_id}")

    # Idempotent under at-least-once delivery: a re-delivered confirm/reject
    # whose work already landed returns quietly and writes no second audit row.
    # This is NOT the silent-skip the docstring forbids — the decision holds,
    # it just already held.
    if link["status"] == new_status:
        return {"decision": decision, "doc_id": doc_id, "item_ref": item_ref,
                "link_status": new_status, "idempotent": True}
    if link["status"] != want:
        raise ValueError(
            f"gate.apply {decision}: link {item_ref} -> doc {doc_id} is "
            f"'{link['status']}', expected '{want}'"
        )

    was = link["match_basis"]
    if decision == "confirm-link":
        # Sole writer of match_basis='manual' in the codebase. The basis asserts
        # "a named person said so", and it is what lifts the link past the C5
        # CHECK — so it and the production status must be written together, in
        # this one statement, or the CHECK correctly rejects the row.
        conn.execute(
            "UPDATE item_document SET match_basis='manual', status='production' "
            "WHERE doc_id=%s AND item_ref=%s",
            (doc_id, item_ref),
        )
    else:
        # reject-link / reopen-link leave match_basis alone: the trail must keep
        # recording HOW the link was proposed, including one that was refused.
        conn.execute(
            "UPDATE item_document SET status=%s::link_status WHERE doc_id=%s AND item_ref=%s",
            (new_status, doc_id, item_ref),
        )

    # `confirm-link` overwrites the basis, and the row is the only other place
    # it lived — inv. 10 requires the trail to stand alone, so the pre-decision
    # basis is carried here. Recorded on all three for symmetry: a reader should
    # not have to know which decisions rewrite provenance to read the log.
    _audit(conn, event, doc_id, p["decided_by"], job, item_ref=item_ref,
           detail={"match_basis_before": was, "link_status_before": link["status"],
                   **(_note_detail(p) or {})})
    if decision == "confirm-link":
        # §9: every staged->production transition is audited, link-level
        # transitions included — this is the human counterpart of the
        # production-write GATE writes for a document.
        _audit(conn, "production-write", doc_id, p["decided_by"], job, item_ref=item_ref)
    return {"decision": decision, "doc_id": doc_id, "item_ref": item_ref,
            "link_status": new_status, "match_basis_before": was}


def handle_gate_apply(conn, job: dict) -> dict:
    p = job["payload"]
    doc_id = p["doc_id"]
    decision = p["decision"]
    decided_by = p["decided_by"]
    # C17 link decisions act on ONE link and never touch `document`, so they
    # take neither the manual-task resolution nor the production-write audit
    # below — both are document-level, and a link ruling settles nothing about
    # the paper. They return before any of it.
    if decision in LINK_DECISIONS:
        return _apply_link_decision(conn, job, decision)

    prev_status = _doc_status(conn, doc_id)

    if decision == "approve":
        _apply_edits(conn, doc_id, p.get("edits"))
        _promote(conn, doc_id)
        _promote_pending_links(conn, doc_id)
        # C6 human path: a STAGED candidate that carried a supersession target
        # (document.supersedes, written sticky at candidate-write time — it must
        # not act while merely staged, §7) acts on it now that a human promoted
        # it to production.
        supersedes = conn.execute(
            "SELECT supersedes FROM document WHERE doc_id=%s", (doc_id,)
        ).fetchone()["supersedes"]
        if supersedes is not None:
            _apply_supersession(conn, doc_id, supersedes, decided_by, job)
    elif decision == "reject":
        conn.execute("UPDATE document SET status='rejected' WHERE doc_id=%s", (doc_id,))
        # §7: un-bind = reject + link cascade — no link may stay production or
        # staged under a rejected doc; each cascaded link is its own audit event.
        cascaded = conn.execute(
            "UPDATE item_document SET status='rejected' "
            "WHERE doc_id=%s AND status <> 'rejected' RETURNING item_ref",
            (doc_id,),
        ).fetchall()
        for r in cascaded:
            _audit(conn, "link-rejected", doc_id, decided_by, job, item_ref=r["item_ref"])
        group_id = p.get("group_id")
        if group_id is not None:
            queue.enqueue(
                conn, "discover.group", {"group_id": group_id},
                dedupe_key=f"discover:gate-reject:{doc_id}:{group_id}",
                priority="interactive",
            )
    elif decision == "reopen":
        # The way back out of `rejected`, and the reason it had to exist:
        # making `rejected` terminal in `_upsert_document` (2026-09-04) closed
        # the hole where ANY re-delivery silently un-rejected a document, and
        # that hole was also the only way back. An accident is not an escape
        # hatch, so this is the deliberate one, with a person's name on it.
        #
        # The precondition RAISES rather than skipping, like every link
        # decision: a reopen that quietly did nothing would read to the
        # reviewer as their click being ignored, which is the exact failure
        # C17 was written to end.
        if prev_status != "rejected":
            raise ValueError(
                f"gate.apply reopen: document {doc_id} is {prev_status!r}, "
                f"not 'rejected' — there is nothing to reopen")
        conn.execute(
            "UPDATE document SET status='staged' WHERE doc_id=%s", (doc_id,))
        # Deliberately NO link cascade. `reject` cascades links to `rejected`
        # so none is left staged or production under a rejected document; the
        # reverse is not symmetric, because re-staging those links would
        # recreate the staged-link-under-an-unreviewed-doc state the cascade
        # exists to prevent. Each link comes back through `reopen-link`, one
        # human decision at a time.
    elif decision == "bind-manufacturer":   # C4 §7b
        # The binding is recorded BEFORE the links are derived, and survives a
        # derivation that produces none. That ordering is the whole of 053: a
        # manufacturer whose every item carries a blank BC device class links
        # nothing today ([mfr-bind-empty-class], which stands) and the decision
        # must still be stored, or the reviewer's answer is discarded the moment
        # they give it -- which is exactly what happened before this line.
        bound = _bindable_manufacturer(conn, p.get("manufacturer"))
        if bound:
            conn.execute(
                "UPDATE document SET canonical_manufacturer=%s WHERE doc_id=%s",
                (bound, doc_id))
        _promote(conn, doc_id)
        _derive_mfr_scope_links(conn, doc_id, p.get("manufacturer"))
    else:
        raise ValueError(f"gate.apply: unknown decision '{decision}'")

    _resolve_manual_tasks(conn, doc_id, decided_by)
    # `note` (office UI redesign P1a, 2026-09-11): the reviewer's reason,
    # optional and additive. It lands on the decision's own row and nowhere
    # else -- the cascaded `link-rejected` rows above are consequences, not
    # decisions -- and a payload without it writes the NULL detail it always did.
    _audit(conn, decision, doc_id, decided_by, job, detail=_note_detail(p))
    # §9: every production write is audited — the human promote paths log it
    # alongside the decision, once per staged->production transition (same
    # prev_status guard as gate.candidate, idempotent under re-delivery).
    if decision in ("approve", "bind-manufacturer") and prev_status != "production":
        _audit(conn, "production-write", doc_id, decided_by, job)
    return {"decision": decision, "doc_id": doc_id}


register("gate.apply", handle_gate_apply)
