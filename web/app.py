"""Standalone producer UI — FastAPI + Jinja + HTMX (CLAUDE.md: no Node, no build).

Physically separate from the worker: its own package (`web/`), its own image
(`Dockerfile.web`), its own dependency group (`pyproject.toml` `[web]`). It
connects to Postgres as `dentalia_api` (migrations 007/008), never as the
schema owner, and imports nothing from `app.handlers` / `app.workers` — the
producer-only boundary (Invariant 1) is structural, not just a convention.

Routes group by function (CLAUDE.md: exception handling, registry visibility,
status board). `/manufacturers` and `/playbooks` are registered from
`web/registry.py` via `registry.register_routes`, not defined here:
  * status + jobs
    - `GET  /`          — Today: the office's first screen (what is waiting for
      a person, the failures they can act on, coverage, the week's report)
    - `GET  /status`    — status board (queue counts, KPI board, recent jobs).
      "System status" in the menu's operator group; it was `/` until 2026-09-14
    - `GET  /_pulse`    — the office menu with its counts and health line, as
      an htmx fragment (base.html renders the same template without counts)
    - `GET  /jobs/{id}` — single job detail (full payload, error, timestamps)
    - `GET  /healthz`   — liveness for compose/caddy
  * producers (every one enqueues a job; none writes the registry)
    - `GET/POST /ingest` — BC import form -> `ingest.run`
    - `GET/POST /upload` — manual document upload -> `upload.ingest` (the file
      lands in `upload_inbox`, the one table this module inserts into, on an
      INSERT-only grant it cannot read back)
    - `POST /staging/{doc_id}/apply`               -> `gate.apply`
    - `POST /links/{doc_id}/decide`                -> `gate.apply` (C17
      link-level: confirm-link / reject-link / reopen-link)
    - `POST /staging/suggestion/{id}/assign`       -> `resolve.group`
    - `POST /manual/{task_id}/resolve`             -> `discover.group` (a
      discovery dead end only; "Search again" on /missing posts here too)
    - `POST /dead/{job_id}/rerun`                  — re-enqueue at interactive
      priority (dead is terminal, so the dedupe key is reusable)
    - `POST /dead/retry-timeouts`                  -> each website timeout still
      counted, its own type, at interactive priority (web/failures.py)
    - `POST /dead/search-again`                    -> `discover.group` per item
      group behind an address that no longer works
  * exception queues
    - `GET  /staging` + the HTMX partials `/staging/docs`,
      `/staging/suggestions`, `/staging/{doc_id}/detail`,
      `/staging/suggestion/{id}/detail` — staged documents (incl. staged links
      on production docs, and mfr-binding candidates) and grouping suggestions
    - `GET  /manual` — open `manual_task` work items (gate-manual ones resolve
      via the Staging tab, since that's what carries the doc_id/evidence)
    - `GET  /missing` — Missing documents: the open discovery dead ends only,
      for the office (web/missing.py)
    - `GET  /dead`   — dead-lettered jobs
      (split by what a person can do about them, web/failures.py)
    - `GET  /emails` — inbound emails the S2.4 mailbox poll processed (which
      carried a usable document, which were skipped and why) — read-only
  * registry visibility (SELECT only)
    - `GET  /items`, `/items/{item_ref}`         — catalogue + per-item coverage
    - `GET  /documents`, `/documents/{doc_id}`   — registry documents
    - `GET  /expiry`                             — renewal horizon
    - `GET  /inflight`                           — work in flight per stage
    - `GET  /data-quality`                       — anomaly ledger
    - `GET  /manufacturers`, `/manufacturers/{name}` (web/registry.py)
    - `GET  /playbooks`, `/playbooks/{slug}`     (web/registry.py)
  * files
    - `GET  /archive/{path}`               — archived original, sandboxed to the
      archive root
    - `GET  /documents/{doc_id}/file`      — same file by document
    - `GET  /documents/{content_hash}/text` — extracted text layer
  * read-only JSON API (production-visibility only)
    - `GET  /api/items/{item_ref}/documents`, `/api/documents/{doc_id}`,
      `/api/kpi` (`?include_superseded` adds the supersession chain, 070)

Every queue write goes through `app.queue.enqueue` (active-scope dedupe, C2) —
this module never issues its own INSERT against `job`, and never writes
`document` / `item_document` / `evidence` / `manual_task` (the DB grants would
refuse it anyway — SELECT only bar the `upload_inbox` INSERT above, verified in
tests/test_web.py). Human decisions are
enqueued, never applied here: `app/handlers/gate.py` `handle_gate_apply` is
what writes the registry. `tests/fixtures/seed_ui.py` still hand-seeds
staged/manual/dead rows for the UI tests.
"""

from __future__ import annotations

import hashlib
import json
import logging
import pathlib
import re
from datetime import date, datetime, timezone
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import (
    FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse,
    Response,
)
# FastAPI's own guard, reused rather than restated: a 204 or a 304 may not
# carry a body, whatever an exception handler would like to put in one.
from fastapi.utils import is_body_allowed_for_status_code
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
# Starlette's, not FastAPI's: the router raises ITS class for an unmatched
# path, and FastAPI's HTTPException is a subclass, so a handler registered on
# this one catches both (a handler on FastAPI's would miss every 404 that never
# reached a route).
from starlette.exceptions import HTTPException as StarletteHTTPException

# `app.manufacturers` is pure stdlib + a read-only alias lookup, the same
# footing on which this module already imports `app.queue` and `web/registry.py`
# imports `app.playbooks`. It is emphatically not `app.handlers` — the review
# UI resolves a name to offer a choice; only GATE acts on the choice.
from app import db, heartbeat, manufacturers, playbooks, queue
# Export file types /import accepts. Mirrors the adapter's own set exactly, so
# the form can never reject a file the worker would have read.
# Imported as a symbol, not as `app.coverage`: this module already defines a
# route function named `coverage`, which shadows a module import of the same
# name and turns every call into `AttributeError: 'function' object has no
# attribute ...` at request time, not at import.
from app.coverage import discovery_state
from app.adapters.source import EXPORT_SUFFIXES as IMPORT_SUFFIXES
# archiving is import-light on purpose (re, urllib, app.queue) so the slim
# web image can share the archive gate with the worker.
from app.handlers import archiving
from app.config import Web, load_config
# The expiring window's one definition (see EXPIRING_WINDOW_DAYS below).
# `web/registry.py` already imports this module; it has no imports of its own.
from app.compliance import EXPIRY_HORIZON_DAYS

log = logging.getLogger("dentalia.web")
from web import access, bc_push_view, catalogue, failures, item_link, missing, onboarding, registry, scheduler_view, words
from web.item_docs import item_documents
# Re-exported: the Today page and the menu counts read the Missing documents
# list through this name (office UI redesign spec § 4 and § 7).
from web.missing import missing_summary  # noqa: F401

# Mirrors the closed `job_type` enum (migrations 001 + later ADD VALUEs,
# Invariant 7). This list only ever offers existing enum values as UI choices —
# it does not and must never define a new tag. Kept in sync by hand, enforced by
# tests/test_web.py::test_job_type_lists_match_db_enum (a tag missing here is
# silently un-filterable on the status board).
JOB_TYPES = [
    "ingest.run",
    "resolve.group",
    "discover.group",
    "fetch.url",
    "extract.doc",
    "validate.doc",
    "gate.candidate",
    "gate.apply",
    "backfill.scan",
    "upload.ingest",
    "vendor.import",
    "email.poll",
    "email.request",
    "email.reminder",
    "eudamed.sync",
    "eudamed.certregister",
    "eudamed.sweep",
    "playbook.reonboard",
    "report.weekly",
    "scheduler.tick",
    "bc.push",
]
PRIORITIES = ["interactive", "delta", "sweep"]
SOURCES = ["csv", "bc_odata"]
JOB_STATUS_ORDER = ["pending", "running", "done", "failed", "dead"]

# The counters an INGEST run reports, in the order the import screens show
# them. Held here rather than spelled out in the template because the same
# list is walked twice on an applied run — once for what the preview predicted,
# once for what the write actually did.
IMPORT_COUNT_ROWS = [
    ("Rows in file", "seen"),
    ("New or changed", "changed"),
    ("Unchanged", "unchanged"),
    # "Non-MD" and "Device class blank" were the column names of the export,
    # not words (spec § 9): the reader of this diff is the person who exported
    # the file, and what they need to know is how many rows this import is not
    # going to chase documents for.
    ("Not a medical device", "non_md"),
    ("Device class missing", "md_unknown"),
    ("No manufacturer's article no.", "missing_mfr_ref"),
    ("Skipped", "skipped"),
]

# The same idea for `vendor.import`, whose report has entirely different fields
# — reusing the rows above renders a table of blanks. `renamed` and
# `disappeared` are LISTS on that report; the count column shows how many, the
# blocks under it show which.
VENDOR_COUNT_ROWS = [
    ("Rows in master", "seen"),
    ("New codes", "added"),
    ("Renamed", "renamed"),
    ("Gone from the file", "disappeared"),
    ("Unchanged", "unchanged"),
]
COUNT_ROWS_BY_TYPE = {
    "ingest.run": IMPORT_COUNT_ROWS,
    "vendor.import": VENDOR_COUNT_ROWS,
}

# What a finished run WAS, for the "Recent runs" list on /import. A queue tag is
# a source key (spec § 9, rule 5): it belongs under Technical details, where the
# list still prints it, and not in the line a person scans. An unlisted tag is
# named generically rather than by its tag -- the tag is one click away, and a
# background task is what every one of them is from this page's point of view.
RUN_WORDS = {
    "ingest.run": "Item catalogue import",
    "vendor.import": "Manufacturer master import",
    "upload.ingest": "Uploaded document",
}
RUN_WORD_OTHER = "Background task"

# A renewal email's own state and errand, in words (spec § 9, P7c). Both now
# live in `web/words.py` with every other screen word, and these names point at
# them -- they turned out NOT to be two screens' private vocabulary: `/expiry`
# and `/documents/{id}` each carry a chase pill linking to `/drafts/{id}`, and
# both printed the stored value while the page they opened printed the word.
# One click apart, "cancelled" and "Archived" for the same draft. A map two
# modules away from a template that needs it is a map that gets re-spelled.
DRAFT_STATUS_WORDS = words.DRAFT_STATUS_WORDS
DRAFT_KIND_WORDS = words.DRAFT_KIND_WORDS

# What `email.poll` did with an attachment, for the Emails page. `archived` is
# the file held and queued to be read; `deduped` is the same bytes already on
# file from another route, which is a good outcome and not a failure.
ATTACHMENT_DISPOSITION_WORDS = {
    "archived": "Being read",
    "deduped": "Already on file",
}

# Pipeline stage each job type belongs to (CLAUDE.md architecture diagram) —
# display-only labeling, not a schema concept. GATE covers both machine and
# human-decision tags; EMAIL/PLAYBOOK/EUDAMED are the peripheral producers.
STAGE_BY_TYPE = {
    "ingest.run": "INGEST",
    "resolve.group": "RESOLVE",
    "discover.group": "DISCOVER",
    "fetch.url": "FETCH",
    "extract.doc": "EXTRACT",
    "validate.doc": "VALIDATE",
    "gate.candidate": "GATE",
    "gate.apply": "GATE",
    "backfill.scan": "BACKFILL",
    "upload.ingest": "UPLOAD",
    "vendor.import": "VENDOR-IMPORT",
    "email.poll": "EMAIL",
    "email.request": "EMAIL",
    "email.reminder": "EMAIL",
    "eudamed.sync": "EUDAMED",
    "eudamed.certregister": "EUDAMED",
    "eudamed.sweep": "EUDAMED",
    "playbook.reonboard": "PLAYBOOK",
    "report.weekly": "SCHEDULER",
    "scheduler.tick": "SCHEDULER",
    "bc.push": "BC-PUSH",
}
DEFAULT_JOB_LIST_LIMIT = 50
# Cap the /emails processed-list render (S2.4). The mailbox poll is low-volume,
# but the list must never be silently truncated — the count is always exact and
# the page says when it is showing only the most recent.
EMAILS_PAGE_LIMIT = 200
DRAFTS_PAGE_LIMIT = 100

# --------------------------------------------------------------------------- #
# Plain-language vocabulary for the review board. The people working /staging
# are Dentalia's compliance staff, not the pipeline team: they should be able
# to read a row and know what it is and why it is waiting for them, without
# knowing what a "ref gate" or a "coverage scope" is. Nothing here changes
# meaning — the raw value is always still available under Technical details.
#
# Vocabularies are the schema's own (docs/dentalia-schema-sketch.md:347-348):
# type is DoC|EC|IFU|ISO|SPP|other, regulation is MDR|MDD|n.a. Unknown values
# fall through to the raw string rather than being hidden.
# --------------------------------------------------------------------------- #
# One vocabulary (spec § 9, P7a): the labels moved to `web/words.py`, which is
# where every screen word now lives, and this name points at them. Client ruling
# 2026-08-18 (question 11) on SPP is recorded there with them.
DOC_TYPE_LABELS = words.DOC_TYPE_WORDS
REGULATION_LABELS = {"MDR": "MDR", "MDD": "MDD", "n.a.": "no regulation"}
# (value, label) pairs for the review form's correction dropdowns — the same
# vocabularies, ordered for a human rather than by dict insertion accident.
TYPE_CHOICES = [(v, DOC_TYPE_LABELS[v]) for v in ("DoC", "EC", "ISO", "IFU", "SPP", "other")]
REGULATION_CHOICES = [(v, REGULATION_LABELS[v]) for v in ("MDR", "MDD", "n.a.")]
# `coverage_scope` is a closed vocabulary of TWO values. `item` was retired
# 2026-08-12 and is unreachable on three levels: migration 022's CHECK forbids
# it in the column, `derive_coverage_scope` never returns it ("`item` is never
# produced" -- its own docstring), and the committed ground truth assigns none.
# Its key survived here until 2026-08-27 purely because both maps are read
# through `.get(scope, fallback)` below, so nothing ever noticed. Deleted rather
# than kept: a three-value map over a two-value column reads as an open
# vocabulary, which is how 37 of the first 57 documents came to carry `item` in
# the first place. Migration 022's comment is where the retirement is recorded.
COVERAGE_LABELS = {
    "group": "a group of items",
    "manufacturer": "everything from this manufacturer",
}
# `COVERAGE_SHORT` (the same scopes in a column's worth of width) went with the
# Review list's six-column grid on 2026-09-11 (office UI redesign, spec § 6):
# a row now names the document in one line and has no coverage column.

# One sentence per VALIDATE flag (app/handlers/validate.py) explaining, to a
# reviewer, what the machine could not settle. Only `cert-unresolved` and
# `ref-unscoped` occur on the live queue today, but the whole vocabulary is
# mapped so a newly-emitted flag never renders as a bare slug.
FLAG_EXPLANATIONS = {
    "not-a-device-document": "This does not look like a medical device document, so it was held back from automatic filing.",
    "cert-unresolved": "This document names a certificate we do not have on file yet.",
    "ref-unscoped": "The document lists article numbers, but we could not tie them to your catalogue.",
    "ref-catalogue": "Matched to your catalogue by article number alone.",
    "no-item-identifier": "The document does not say which items it covers.",
    "no-ref-overlap": "The article numbers in the document do not match this group's items.",
    "multi-group-match": "The article numbers point at more than one group of items.",
    "multi-manufacturer-ref": "The article numbers belong to more than one manufacturer.",
    "manufacturer-unresolved": "We could not confirm which manufacturer issued this.",
    "date-insane": "The dates on this document do not look right.",
    "older-than-current": "This is older than the document already on file for these items.",
    "same-date-revision": "This carries the same date as the document already on file for these "
                          "items, so nothing says which one is current.",
    "downgrade-uncomparable": "We cannot tell whether this is newer or older than the document on file.",
    "expiry-on-certless-doc": "The expiry date is not written as an expiry anywhere on the page, "
                              "so it may be a signing or issue date read as one.",
    "auto-superseded": "This replaces an older document already on file.",
}
# The Review list's reason filter (office UI redesign, spec § 6). One chip per
# question a reviewer answers, applied on the server; "" is the unfiltered list.
# The labels are the spec's, verbatim and in its order.
REVIEW_REASON_CHIPS = (
    ("", "All reasons"),
    ("items", "Which items is unclear"),
    ("manufacturer", "Manufacturer unclear"),
    ("range", "Covers a whole range"),
    ("expired", "Expired"),
    ("other", "Other"),
)
# Every review reason code -> the chip it files under. The codes are GATE's
# three flag classes (`app/handlers/gate.py` BLOCKING / CAPPING / INFORMATIONAL,
# restated rather than imported: web/ is a producer and must not depend on
# handler internals), every flag `FLAG_EXPLANATIONS` words, and the two task
# codes that make a document a whole-range approval. `tests/
# test_web_review_context.py` holds this against gate's tuples, so a new flag
# fails there until it is placed. A code missing here still reaches a chip: the
# list's filter files anything it does not know under Other, the way
# `_review_reason` renders an unknown flag rather than dropping it.
#
# Not chips by code: a document with no open task, or a task with no flags,
# reads NO_TASK_REASON ("could not prove ... which of your products it covers")
# and files under "items"; "expired" is the document's own date, not a code, so
# it asks a different question from the other five and crosses all of them --
# an expired whole-range candidate is under "Covers a whole range" AND under
# "Expired", deliberately (fix round 2, m2).
REASON_CODE_CHIP = {
    # the route and the task's own reason: a manufacturer's whole range
    "mfr-binding": "range",
    "md-class-unknown": "range",
    # which of our items the document covers is in doubt
    "no-item-identifier": "items",
    "no-ref-overlap": "items",
    "multi-group-match": "items",
    "ref-unscoped": "items",
    "ref-list-possibly-truncated": "items",
    "device-enumeration": "items",
    # whose document it is is in doubt. `ref-catalogue` matched an article
    # number with no manufacturer confirmed, and the guide tells the reviewer
    # to confirm the supplier first, so it is a manufacturer question.
    "manufacturer-unresolved": "manufacturer",
    "multi-manufacturer-ref": "manufacturer",
    "ref-catalogue": "manufacturer",
    # about the paper itself: its kind, its dates, its certificate
    "not-a-device-document": "other",
    "cert-unresolved": "other",
    "date-insane": "other",
    "older-than-current": "other",
    "same-date-revision": "other",
    "downgrade-uncomparable": "other",
    "expiry-on-certless-doc": "other",
    "auto-superseded": "other",
    "evidence-page-missing": "other",
    "iso-without-standard-number": "other",
    "date-label-not-adjacent": "other",
}

# Link bases that follow a document into production when it is approved:
# `gate._promote_pending_links` promotes staged links whose `match_basis` is in
# `gate.TRUSTED_BASES`, and every other basis stays staged (invariant 3, the
# item_document CHECK). Restated for the same producer-boundary reason as the
# flags above, and held equal to gate's tuple by a test. The Review panel reads
# it to promise only what an approval actually publishes: on dev on 2026-09-11,
# 53 of 234 waiting documents carried only `fetch-context` links, so "approving
# makes it count for these N items" would have been false for every one.
PUBLISHING_BASES = ("ref-list", "ref-item", "udi", "basic-udi-di", "mfr-scope",
                    "manual", "map-supplier")
# How a link was made, as the panel says it ("Matched through ..." for links an
# approval publishes, "only by ..." for the ones it does not). An unknown basis
# renders as its raw value rather than disappearing.
BASIS_WORDS = {
    "ref-list": "the manufacturer's article numbers on the document",
    "ref-item": "our item numbers on the document",
    "udi": "the UDI on the document",
    "basic-udi-di": "the Basic UDI-DI on the document",
    "mfr-scope": "the manufacturer's whole range",
    "manual": "a person's decision",
    "map-supplier": "the supplier's coverage list",
    "fetch-context": "where the file was found",
    "name-family": "similar product names",
    "ref-catalogue": "article numbers alone, with no manufacturer confirmed",
}
# "Rules" on the Review panel: the regulation in words.
REGULATION_WORDS = {
    "MDR": "MDR, the current EU rules",
    "MDD": "MDD, the older EU rules",
    "n.a.": "Neither MDR nor MDD",
}
# Items listed by name above the Review panel's buttons before the rest fold
# under "+ K more" (spec § 6).
REVIEW_ITEMS_SHOWN = 10
# `/documents/{doc_id}/file`'s answer when it serves the bytes (spec § 6): a
# document's file never changes, so the browser may keep it a year without
# asking again. See `document_file`.
DOCUMENT_FILE_CACHE = "private, max-age=31536000, immutable"

# No open task at all: VALIDATE was happy, the REF gate simply would not
# auto-publish the link (invariant 3). That is the single most common reason a
# document sits here, so it gets a sentence of its own rather than silence.
NO_TASK_REASON = (
    "Read without problems — we just could not prove strongly enough which of "
    "your items it covers, so it needs a person to confirm."
)
# Audit identity when nothing else supplies one. The proxy-authenticated user
# (G3 v0) still wins where a proxy is in front; this is the placeholder until
# real logins land, and the one line to change when they do.
DEFAULT_DECIDED_BY = "user:admin"
MFR_BINDING_REASON = (
    "This covers a manufacturer's whole range. Approving it links it to every "
    "medical-device item from that manufacturer."
)
# The same route, but the sentence above would be a lie here: there are NO
# medical-device items to link, so approving links none.
#
# GATE stages these deliberately since [mfr-bind-empty-class] (Denis,
# 2026-08-27): every item we hold from this manufacturer has a BLANK device
# class, which is a gap in BC's export and not a statement that they sell us
# nothing. Filing on it would hide the document; LINKING it would assert
# coverage we cannot evidence. Binding it asserts neither, and since migration
# 053 the binding has somewhere to live.
#
# Two corrections to what this comment said before 2026-08-31, both load-bearing:
#   * it claimed `gate.apply` bind-manufacturer "would refuse it"
#     ([gate-bind-zero-links]). It would not. That guard raises on a name
#     resolving to no BC CODES; `3SHAPE TRIOS A/S` resolves to ['10004','10005']
#     and sails past it. The guard is kept and still correct for what it
#     actually covers -- see `gate._derive_mfr_scope_links`.
#   * the sentence told the reviewer to wait for BC. Denis reversed that on
#     2026-08-31: "reviewer should be able to select what they want to select".
MFR_BINDING_UNKNOWN_CLASS_REASON = (
    "This covers a manufacturer's whole range, but every item we hold from "
    "them has a blank device class in Business Central — so there is nothing to "
    "link yet. Approving still records the manufacturer; the item links "
    "follow once that class is filled in."
)

# Why a reviewer rejects a document (office UI redesign, spec § 3 P1a). A closed
# list rather than free text, so the Decisions page reads the same six phrases
# for every rejection; the optional note beside them carries the specifics.
# `staging_apply` refuses a reject without one of these, and sends the choice
# on `gate.apply` as `note`: "<reason>" or "<reason>: <note>".
REJECT_REASONS = (
    "Wrong manufacturer",
    "Not one of our items",
    "Not a compliance document",
    "Out of date",
    "Duplicate of another document",
    "Other",
)
# The longest reject note `staging_apply` accepts. Room for a sentence or two;
# the note lands in `gate.apply`'s payload and the audit trail, and a paste of
# a whole email belongs somewhere else. The field carries it as `maxlength`.
REJECT_NOTE_MAX = 500

#: The receipt each review decision answers with (spec § 9, P7a: receipts in
#: words, not job numbers). Every one of them is in the FUTURE tense about the
#: registry, because this process does not write it: the decision is recorded
#: on the queue and `app/handlers/gate.py` applies it. "Within a minute" is the
#: worker's poll interval plus the job ahead of it, not a promise of latency.
DECISION_RECEIPTS = {
    "approve": "Approval recorded. The registry updates within a minute.",
    "reject": ("Rejection recorded. The document stays on file and can be "
               "reopened from its own page."),
    "bind-manufacturer": ("Approval recorded for the whole range. Every item "
                          "of this manufacturer shows the document within a "
                          "minute."),
    "reopen": ("Reopened. It comes back to Review within a minute so it can be "
               "decided again."),
}

#: The same, for a C17 link-level ruling: one item's link to one document.
LINK_RECEIPTS = {
    "confirm-link": "Link confirmed. The item shows this document within a minute.",
    "reject-link": ("Link rejected. The document stays on file; this item stops "
                    "showing it within a minute."),
    "reopen-link": "Link reopened. It goes back to Review within a minute.",
}

# /staging rows per page. The board is a review queue over tables that grow
# without bound (5.8k open grouping suggestions at the time this was capped),
# so every section there is paginated and every row loads its detail on
# expand — see `_staged_docs_page` for what rendering them all cost.
STAGING_PAGE_SIZE = 50
STAGED_LINKS_LIMIT = 200

# /data-quality rows per page. The ledger is one row per catalogue oddity and
# grows with the catalogue (19.147 rows at 16k items); it used to render a bare
# `LIMIT 500` with no total and no controls, which reads as "this is the list".
DATA_QUALITY_PAGE_SIZE = 50

# Actionable `item_class_check` rows listed on /data-quality (033). Capped
# rather than paged, and the section prints the full total beside the cap so a
# truncated list never reads as the whole list: this is a worklist someone works
# through item by item, not a ledger to browse. Measured 2026-08-21, today's
# registry yields 130 fillable and a handful of conflicts, so one screenful is
# the right size — and it grows with every backfill, which is why the total is
# not optional.
CLASS_CHECK_LIMIT = 50

# /documents and /items rows per page. Same failure the data-quality ledger had
# and the same fix: both rendered a bare LIMIT with no total, so a registry of
# any size looked like exactly one screenful. /documents hit its cap for real
# (200 rendered against 314 documents in three statuses alone), which is the
# worst version of it — the page did not lie about the rows it showed, it just
# never mentioned the ones it dropped.
DOCUMENTS_PAGE_SIZE = 50
ITEMS_PAGE_SIZE = 100

# Upper bound on the `page` query parameter of every paginated list. `page` is
# multiplied into OFFSET, and an unbounded Python int reaches Postgres as a value
# outside bigint, which raises uncaught -- a 500 any anonymous caller could
# produce from a query string ([final-500s]). 100_000 pages is past the end of
# every list here by three orders of magnitude at their smallest page size, so it
# constrains nothing a reader can reach; `/expiry`'s `back`/`ahead` were bounded
# the same way on 2026-08-10 after the same class of report.
MAX_PAGE = 100_000

# Rows per section on /search. Small on purpose: this is a "did you mean this
# one" box, and each section links to the list page that owns the full answer.
SEARCH_SECTION_LIMIT = 10

#: `report-<period_key>.html`, the only shape `report.weekly` writes. A
#: whitelist rather than a traversal check: the check is what you forget.
_REPORT_NAME_RE = re.compile(r"report-[0-9A-Za-z._-]{1,64}\.html")

#: The one FORWARD expiring window (D7, office UI redesign, 2026-09-11): 30
#: days, for every figure that says "expiring soon" -- the header strip's
#: counter, /expiry's default `?ahead=`, and the weekly report -- and each of
#: them names it on screen. Until then the strip and /expiry looked 180 days
#: ahead while the report looked 30.
#:
#: Not a second literal 30: it IS `app.compliance.EXPIRY_HORIZON_DAYS`. The
#: worker's `report.weekly` cannot import `web`, so the constant has to live in
#: `app/`. That one equals the scheduler's renewal clock by default and is
#: guarded on the default (`Renewal().horizon_days`, `tests/test_compliance.py`);
#: the chase itself reads `max(cfg.renewal.horizon_days)`, which a TOML overlay
#: can override, and nothing guards the overridden value. Still not a config
#: key, for the reason the page sizes above are not.
EXPIRING_WINDOW_DAYS = EXPIRY_HORIZON_DAYS

#: How far BACK a lapse still counts as recent, live renewal work: the strip's
#: counter and /expiry's default `?back=`. D7 decided only the forward window
#: (controller ruling, 2026-09-11), so this keeps the 180 days it had.
#:
#: 180 rather than a conventional 90 because of what the live registry holds:
#: measured 2026-08-18, the only lapsed cluster was 106 days old -- 52
#: documents on one certificate covering 1.046 items -- and a shorter window
#: pushes exactly that cluster into the collapsed long-expired fold and leaves
#: both active tables empty, the one outcome the split exists to prevent.
#: Applying 30 days backwards did exactly that on dev (strip 6 -> 0, four
#: certificates that lapsed 31-180 days ago folded away). Every figure that
#: uses it names it ("expired in the last 180 days").
EXPIRED_LOOKBACK_DAYS = 180

#: The `lapsing` CTE and its collapse key, shared verbatim by the `/expiry`
#: board and the header strip's `expiring` counter.
#:
#: They are constants rather than two similar queries for the same reason
#: `_pulse_counts` documents about `in_flight_docs`: a strip that disagrees
#: with the page it links to is worse than no strip. The counter is a `count(*)`
#: over exactly these rows, grouped by exactly this key, so "6 expiring" and
#: the six rows on the board cannot drift apart -- including the case that
#: makes a cheaper key wrong, a NULL `cert_number` (5 of 57 documents in the
#: live window), which without `manufacturer` in the key silently merges two
#: manufacturers into one row and under-counts.
#:
#: Cost, measured 2026-09-03 on the dev database: 39ms for the counter against
#: ~13ms for the other four together. Paid deliberately -- a compliance count
#: that is cheap and wrong is not a saving.
_LAPSING_CTE = """
                WITH lapsing AS (
                  SELECT d.doc_id, d.type, d.regulation, d.cert_number,
                         e.expires, e.inherited, e.basis,
                         COALESCE(g.canonical_manufacturer, 'unknown') AS manufacturer
                  FROM document d
                  JOIN document_effective_expiry e ON e.doc_id = d.doc_id
                  LEFT JOIN item_document id
                         ON id.doc_id = d.doc_id AND id.status='production'
                  LEFT JOIN item_group_member gm ON gm.item_ref = id.item_ref
                  LEFT JOIN item_group g ON g.group_id = gm.group_id
                  WHERE d.status='production'
                    AND e.expires IS NOT NULL
                    -- the whole retention window, partitioned below rather
                    -- than filtered here: the board has to be able to say
                    -- "and 18 more beyond this", which needs the count
                    AND e.expires <= current_date + 3650
                  GROUP BY d.doc_id, d.type, d.regulation, d.cert_number,
                           e.expires, e.inherited, e.basis, g.canonical_manufacturer
                )
"""

#: One row per lapsing THING, not per document. Changing this changes both
#: the board and the counter, which is the point.
_LAPSING_GROUP_BY = "l.manufacturer, l.type, l.regulation, l.cert_number, l.expires"

_HERE = pathlib.Path(__file__).resolve().parent


def _list_import_files(imports_dir: str, *, max_depth: int = 2, limit: int = 200) -> list[str]:
    """Relative paths of .csv/.xlsx files under `imports_dir`, best-effort.

    Missing/unreadable directory -> empty list; the ingest form falls back to
    a free-text path field rather than failing.
    """
    root = pathlib.Path(imports_dir)
    if not root.is_dir():
        return []
    out: list[str] = []
    try:
        for p in sorted(root.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in (".csv", ".xlsx"):
                continue
            rel = p.relative_to(root)
            if len(rel.parts) > max_depth:
                continue
            out.append(str(rel))
            if len(out) >= limit:
                break
    except OSError:
        return []
    return out


def _status_counts(conn) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, count(*) AS n FROM job GROUP BY status"
    ).fetchall()
    counts = {r["status"]: r["n"] for r in rows}
    return {s: counts.get(s, 0) for s in JOB_STATUS_ORDER}


def _like_escape(s: str) -> str:
    """Escape LIKE metacharacters so user input matches literally."""
    return s.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


#: One search predicate, used by /documents, /staging and /manual so the three
#: cannot answer the same query differently. Alias the `document` table as `d`.
#:
#: `cert_number` alone was the whole of /documents' search until 2026-08-21, and
#: a Class I declaration has no notified body and therefore never has a
#: certificate number -- so an entire regulatory class was unfindable by any
#: term a person would type. Of CARL MARTIN's four documents the only findable
#: one was its ISO certificate, which is the one you least want.
#:
#: The manufacturer comes from `evidence`, not from the linked items: a
#: document with no links is exactly the one somebody is hunting for, and the
#: catalogue cannot name it. `doc_id::text = %s` is an equality test, not a
#: LIKE -- typing 818 should find document 818, not 1818 and 8180.
_DOC_SEARCH = (
    "(%s = '' OR d.cert_number ILIKE %s OR d.archive_url ILIKE %s "
    " OR d.basic_udi_di ILIKE %s OR d.doc_id::text = %s "
    " OR EXISTS (SELECT 1 FROM evidence ev WHERE ev.doc_id = d.doc_id "
    "            AND ev.field = 'manufacturer' AND ev.value ILIKE %s))"
)


def _doc_search_params(q: str) -> tuple:
    """The six placeholders `_DOC_SEARCH` expects, in order."""
    like = f"%{_like_escape(q)}%" if q else ""
    return (q, like, like, like, q, like)


def _structured_edits(submitted: dict[str, str], baseline_json: str) -> dict:
    """Reduce the review form's edit inputs to only what the reviewer changed.

    The form renders every editable field pre-filled with its current value,
    so an unchanged form posts them all back. Sending all of them would write
    T3 human evidence (`_apply_edits`, app/handlers/gate.py) over values a
    human never touched, permanently outranking the extraction that produced
    them — so the baseline the form was rendered with comes back with it and
    anything equal to its baseline is dropped.

    A malformed or missing baseline means "no reliable comparison": treat
    every field as unchanged rather than guessing, so a broken form can never
    silently rewrite the registry.
    """
    try:
        baseline = json.loads(baseline_json) if baseline_json.strip() else None
    except json.JSONDecodeError:
        baseline = None
    if not isinstance(baseline, dict):
        return {}
    out = {}
    for field, value in submitted.items():
        if field not in baseline:
            continue
        value = (value or "").strip()
        before = baseline.get(field)
        before = "" if before is None else str(before).strip()
        if value and value != before:
            out[field] = value
    return out


def _doc_type_label(doc_type: str | None) -> str:
    return DOC_TYPE_LABELS.get(doc_type or "", doc_type or "Document")


def _review_reason(flags: list[str] | None, *, is_mfr_binding: bool, has_task: bool,
                   task_reason: str | None = None) -> str:
    """Why this document is waiting for a human, in one sentence.

    Unmapped flags are rendered as-is rather than dropped: a reviewer seeing a
    raw slug is a prompt to map it, whereas a silently empty reason reads as
    "nothing is wrong" — the one thing it never means.

    `task_reason` is the task payload's own `reason`, and it only narrows the
    mfr-binding sentence — the route says WHAT this is, the reason says which
    kind. Defaults to None so every existing caller keeps the sentence it had.
    """
    if is_mfr_binding:
        if task_reason == "md-class-unknown":
            return MFR_BINDING_UNKNOWN_CLASS_REASON
        return MFR_BINDING_REASON
    if not has_task or not flags:
        return NO_TASK_REASON
    return " ".join(FLAG_EXPLANATIONS.get(f, f"Flagged as {f}.") for f in flags)


def _parse_path_rewrites(spec: str) -> list[tuple[str, str]]:
    """`"/host/imports=/imports,/old/archive=/archive"` -> [(from, to), ...].

    Malformed entries are skipped rather than raising: a typo in an env var
    must not take the whole UI down, and the rewrite is an optimisation over
    the hash-verified search below, never the only way a file is found.
    """
    out = []
    for entry in (spec or "").split(","):
        entry = entry.strip()
        if not entry or "=" not in entry:
            continue
        src, _, dst = entry.partition("=")
        if src.strip() and dst.strip():
            out.append((src.strip(), dst.strip()))
    return out


def _download_name(archived_name: str, content_hash: str | None) -> str:
    """The name a reviewer should see, from the archive's on-disk name.

    The archive stores `{content_hash[:12]}__{sanitized original}`
    (app/handlers/archiving.py `archive_path`) — the hash prefix is there to
    keep the store collision-free, and it is ours, not the manufacturer's.
    Manufacturers identify their documents BY these filenames, so the prefix
    is stripped back off on the way out.

    Matched against the document's actual hash rather than a `^[0-9a-f]{12}__`
    pattern: a real filename can legitimately contain `__`, and guessing would
    silently truncate it. No match means the name is passed through untouched.
    """
    prefix = f"{(content_hash or '')[:12]}__"
    if content_hash and archived_name.startswith(prefix):
        return archived_name[len(prefix):] or archived_name
    return archived_name


def _sha256_file(path: pathlib.Path) -> str | None:
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _resolve_archive_path(
    archive_url: str,
    roots: list[str],
    *,
    rewrites: list[tuple[str, str]] | None = None,
    content_hash: str | None = None,
) -> pathlib.Path | None:
    """Map a stored `archive_url` to a servable file under one of `roots`.

    `archive_url` is a storage handle, never a browsable URL, and it has three
    shapes in practice: a `file://` URI (LocalFsStore's dev default), a bare
    absolute path (what backfill.scan records for corpus documents), and an
    http(s) URL when `storage.base_url` is configured — that last one is the
    caller's job to redirect to, not ours to open.

    Resolution runs in three passes, each containment-checked against `roots`
    so this can never become an arbitrary-file reader:

    1. the stored path as-is — the normal path. Since backfill.scan started
       archiving through the StorageAdapter, `archive_url` is
       `/archive/{brand}/{type}/{hash12}__{name}.pdf`, uniform across worker
       and web, and every document resolves here;
    2. explicit prefix rewrites from `WEB_PATH_REWRITES` — the declared way to
       say "what was recorded as /home/x/imports is mounted at /imports here";
    3. a hash-verified search: try progressively shorter tails of the stored
       path under each root, accepting a hit ONLY if its sha256 equals the
       registry's `content_hash`.

    Passes 2 and 3 are a SAFETY NET, not the working path. They were written
    when backfill recorded host paths that existed nowhere inside the
    container; that source is fixed, so they now only catch rows written
    before it and any future store whose handles stop matching the mount.
    Deliberately kept rather than deleted (Denis, 2026-08-12) — they cost
    nothing until pass 1 misses. Pass 3 is exact, not heuristic: matching on
    the registry's content hash means a wrong file cannot be served even when
    two paths share a tail. It is skipped entirely without a `content_hash`,
    because a guess is not good enough for a compliance document.
    """
    if not archive_url:
        return None
    parsed = urlsplit(archive_url)
    if parsed.scheme in ("http", "https"):
        return None
    if parsed.scheme == "file":
        stored = pathlib.Path(url2pathname(parsed.path))
    elif parsed.scheme == "":
        stored = pathlib.Path(archive_url)
    else:
        # some other scheme (e.g. a test fixture's local://) — not ours to open
        return None

    bases = []
    for root in roots:
        if not root:
            continue
        try:
            bases.append(pathlib.Path(root).resolve())
        except OSError:
            continue

    def _contained(path: pathlib.Path) -> pathlib.Path | None:
        try:
            target = path.resolve()
        except OSError:
            return None
        for base in bases:
            if target.is_relative_to(base) and target.is_file():
                return target
        return None

    hit = _contained(stored)
    if hit is not None:
        return hit

    stored_str = str(stored)
    for src, dst in rewrites or []:
        if stored_str.startswith(src):
            hit = _contained(pathlib.Path(dst) / stored_str[len(src):].lstrip("/"))
            if hit is not None:
                return hit

    if not content_hash:
        return None
    parts = stored.parts
    for base in bases:
        # longest tail first: the most specific match wins, and the hash check
        # below is what actually authorises serving it
        for i in range(1, len(parts)):
            candidate = base.joinpath(*parts[i:])
            if not candidate.is_file():
                continue
            resolved = _contained(candidate)
            if resolved is not None and _sha256_file(resolved) == content_hash:
                return resolved
    return None


def _recent_jobs(
    conn,
    *,
    limit: int = DEFAULT_JOB_LIST_LIMIT,
    job_type: str | None = None,
    job_status: str | None = None,
    q: str | None = None,
) -> list[dict]:
    """Recent jobs, optionally narrowed by exact type/status and a dedupe_key
    substring / exact id search. Callers must pre-validate `job_type` /
    `job_status` against the closed enums — this only parameter-binds them,
    it does not itself enforce membership."""
    where: list[str] = []
    params: list = []
    if job_type:
        where.append("type = %s::job_type")
        params.append(job_type)
    if job_status:
        where.append("status = %s::job_status")
        params.append(job_status)
    if q:
        pattern = f"%{_like_escape(q)}%"
        if q.isdigit():
            where.append("(dedupe_key ILIKE %s OR id = %s)")
            params.extend([pattern, int(q)])
        else:
            where.append("dedupe_key ILIKE %s")
            params.append(pattern)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    params.append(limit)
    # `finished_at` and the duration derived from it (migration 026). Computed
    # in SQL rather than Python because `claimed_at` can be NULL on a job that
    # never ran, and `interval` handles that without a branch per row.
    return conn.execute(
        f"SELECT id, type, status, priority, attempts, dedupe_key, created_at, "
        f"       finished_at, "
        f"       EXTRACT(EPOCH FROM (finished_at - claimed_at))::int AS duration_s "
        f"FROM job {clause} ORDER BY id DESC LIMIT %s",
        params,
    ).fetchall()


def _recent_runs(conn, limit: int = 25) -> list[dict]:
    """The last runs that produced a result envelope, newest first.

    "Did last night's work run, and what did it do" had no screen: the status
    board counts jobs by status, `/jobs/{id}` shows one at a time, and the
    result envelope every handler returns (`job.result`, migration 019) was
    stored and never displayed. This is that column made visible.

    Deliberately not ingest-only. The plan called for an ingest history, but no
    `ingest.run` job has ever run on this database — the catalogue arrived by
    backfill — so an ingest-only table would render empty forever while the
    work that does run stayed invisible. Filtering by type is one query string
    away once imports start.
    """
    return conn.execute(
        "SELECT id, type, status, priority, dedupe_key, result, created_at, finished_at, "
        "       EXTRACT(EPOCH FROM (finished_at - claimed_at))::int AS duration_s "
        "FROM job WHERE result IS NOT NULL "
        "ORDER BY COALESCE(finished_at, created_at) DESC, id DESC LIMIT %s",
        (limit,),
    ).fetchall()


def _job_by_id(conn, job_id: int) -> dict | None:
    return conn.execute("SELECT * FROM job WHERE id=%s", (job_id,)).fetchone()


#: The rows the Review list is built from, one per staged document, with the
#: fields every row needs. Two placeholders: `{search}` (`_DOC_SEARCH`) narrows
#: `rows`, `{chip}` (`_reason_chip_sql`) narrows `picked`, and their params
#: follow in that order. Callers append the SELECT they need over `picked`.
#:
#: Set-based on purpose (office UI redesign, spec § 6). The list used to sort by
#: doc_id and compute each row's links, manufacturer and task in correlated
#: subqueries for the 50 rows on the page. Grouping by manufacturer needs every
#: staged document's manufacturer before the page can be cut, and no index on
#: `item_document` leads with `doc_id`, so one correlated lookup per document
#: is one sequential scan of the link table each. Here the links and the open
#: tasks are each aggregated in one pass and joined.
#:
#: `group_name` is the manufacturer a row is grouped and titled under: the
#: document's own confirmed manufacturer (`document.canonical_manufacturer`,
#: migration 053: set by a person or by an unambiguous resolution of the printed
#: name), else its linked items' manufacturer. NULL is "Manufacturer not known".
#: The name the document merely printed is not a match and never groups a row.
_REVIEW_ROWS = """
WITH links AS (
    SELECT l.doc_id,
           count(*) AS link_count,
           array_agg(DISTINCT COALESCE(a.canonical_name, m.manufacturer_raw))
             FILTER (WHERE m.item_ref IS NOT NULL) AS manufacturers
      FROM item_document l
      JOIN document ld ON ld.doc_id = l.doc_id AND ld.status = 'staged'
      LEFT JOIN item_mirror m ON m.item_ref = l.item_ref
      LEFT JOIN manufacturer_alias a ON a.raw_name = m.manufacturer_raw
     WHERE l.status <> 'retracted'
     GROUP BY l.doc_id
),
tasks AS (
    SELECT DISTINCT ON (doc_id) doc_id, id, kind, payload
      FROM manual_task
     WHERE status = 'open' AND doc_id IS NOT NULL
     ORDER BY doc_id, id
),
rows AS (
    SELECT d.doc_id, d.type, d.regulation, d.cert_number, d.validity_from,
           d.validity_to, d.coverage_scope, d.created_at, d.source_url,
           d.archive_url, d.content_hash,
           COALESCE(lk.link_count, 0) AS link_count,
           lk.manufacturers,
           t.id AS task_id,
           -- Same rule as `_staged_doc_detail`: the route when VALIDATE set
           -- one, otherwise any manufacturer-scope document with nothing
           -- linked. See the comment there for why the route alone is not
           -- enough (doc 657). The second half needs no task, as there: until
           -- 2026-09-11 this query required a gate-manual task for both, so a
           -- task-less document of that shape read "could not prove which
           -- products" here while its panel offered the whole-range approval.
           (COALESCE(t.kind = 'gate-manual' AND t.payload->>'route' = 'mfr-binding', false)
            OR (d.coverage_scope = 'manufacturer' AND lk.doc_id IS NULL)) AS is_mfr_binding,
           -- The task's own `reason`, which narrows the mfr-binding
           -- sentence (`md-class-unknown` means binding would link zero
           -- items). Null on every task written before 2026-08-27.
           t.payload->>'reason' AS task_reason,
           COALESCE(ARRAY(SELECT jsonb_array_elements_text(t.payload->'flags')),
                    '{{}}'::text[]) AS flags,
           (SELECT e.value FROM evidence e
             WHERE e.doc_id = d.doc_id AND e.field = 'manufacturer' LIMIT 1)
             AS printed_manufacturer,
           COALESCE(d.canonical_manufacturer, lk.manufacturers[1]) AS group_name
      FROM document d
      LEFT JOIN links lk ON lk.doc_id = d.doc_id
      LEFT JOIN tasks t ON t.doc_id = d.doc_id
     WHERE d.status = 'staged' AND {search}
),
picked AS (
    SELECT * FROM rows WHERE {chip}
)
"""

#: One ordering for the list and for the page a deep link resolves to
#: (`_staged_doc_page`): they must agree, or `?doc=N` lands on a page that does
#: not hold N. Groups run oldest first by their oldest document, and
#: "Manufacturer not known" (a NULL group) comes last whatever its age; rows
#: run oldest first inside their group. `doc_id` makes it a total order, so
#: OFFSET paging neither repeats nor skips a row.
_REVIEW_ORDER = "(group_name IS NULL), group_oldest, group_name, created_at, doc_id"


def _review_chip(reason: str | None) -> str:
    """The chip a request asked for, or "" (all reasons) for anything that is
    not one. An unknown value shows the whole list with All reasons marked,
    which is visible, rather than an error for a hand-edited URL."""
    keys = {key for key, _ in REVIEW_REASON_CHIPS}
    return reason if reason in keys else ""


def _reason_chip_sql(chip: str, today: date) -> tuple[str, tuple]:
    """The `picked` predicate for one chip, over the columns of `rows`.

    Derived from `REASON_CODE_CHIP`, so the filter cannot disagree with the
    mapping. It follows `_review_reason`: a whole-range candidate reads the
    binding sentence whatever its flags say, so among the REASON chips it files
    under "range" only, and a document with no task or no flags reads
    NO_TASK_REASON and files under "items". A flag no chip claims lands in
    Other.

    "Expired" is not one of those: it is the document's own `validity_to`, not
    a reason, and it therefore crosses every reason chip -- a document with a
    flag is under its flag's chip and under Expired, and so is a whole-range
    candidate whose date has passed. That overlap is deliberate (fix round 2,
    m2): someone asking which documents have expired wants all of them, and a
    whole-range one that has expired is the most urgent of them. The row the
    chip returns carries the expired badge either way, so the list always says
    why it is there.
    """
    codes = {key: [c for c, k in REASON_CODE_CHIP.items() if k == key]
             for key in ("items", "manufacturer", "range")}
    if chip == "range":
        return "is_mfr_binding", ()
    if chip == "items":
        return ("NOT is_mfr_binding AND (task_id IS NULL OR cardinality(flags) = 0 "
                "OR flags && %s::text[])", (codes["items"],))
    if chip == "manufacturer":
        return "NOT is_mfr_binding AND flags && %s::text[]", (codes["manufacturer"],)
    if chip == "other":
        known = codes["items"] + codes["manufacturer"] + codes["range"]
        return ("NOT is_mfr_binding AND EXISTS (SELECT 1 FROM unnest(flags) f "
                "WHERE f <> ALL(%s::text[]))", (known,))
    if chip == "expired":
        # No `NOT is_mfr_binding` here, unlike the reason chips above: see the
        # docstring. Expired is a date question and crosses all of them.
        return "validity_to < %s", (today,)
    return "true", ()


def _staged_docs_page(
    conn, *, page: int = 0, size: int = STAGING_PAGE_SIZE, q: str = "",
    reason: str = "", today: date | None = None,
) -> tuple[list[dict], int]:
    """One page of `document` rows awaiting a human decision, as *summary*
    rows — doc_id, type, dates, link count, and which review flavour it is
    (plain staged / manual review / mfr-binding candidate, the last being a
    gate-manual `manual_task` whose payload carries `route: "mfr-binding"`).

    Deliberately not the evidence, links or decision form: those are
    `_staged_doc_detail`, fetched only when a reviewer expands the row. This
    used to issue three queries per document and render all of them expanded,
    which is how /staging grew to 7.8 MiB and stopped opening in a browser.

    Grouped by manufacturer and oldest first (`_REVIEW_ORDER`), narrowed by
    the search `q` and the reason chip `reason`. Each row carries its group's
    size over the whole filtered list (`group_total`) and its place in the
    group (`group_pos`), so a group the pager cuts still says how many
    documents it holds and the next page can say it continues.

    Returns `(rows, total)` — total is the unpaginated count, so the pager can
    say "51-100 of 5831" rather than guessing at the end of the list.
    """
    today = today or datetime.now(timezone.utc).date()
    chip_sql, chip_params = _reason_chip_sql(_review_chip(reason), today)
    ctes = _REVIEW_ROWS.format(search=_DOC_SEARCH, chip=chip_sql)
    params = _doc_search_params(q) + chip_params
    total = conn.execute(ctes + "SELECT count(*) AS n FROM picked", params).fetchone()["n"]
    rows = conn.execute(
        ctes + f"""
        SELECT p.*,
               count(*) OVER grp AS group_total,
               min(p.created_at) OVER grp AS group_oldest,
               row_number() OVER (grp ORDER BY p.created_at, p.doc_id) AS group_pos
          FROM picked p
        WINDOW grp AS (PARTITION BY p.group_name)
         ORDER BY {_REVIEW_ORDER}
         LIMIT %s OFFSET %s
        """,
        params + (size, max(page, 0) * size),
    ).fetchall()
    for r in rows:
        _decorate_review_row(r)
        _decorate_review_title(r)
    return rows, total


def _staged_doc_page(conn, doc_id: int, *, size: int = STAGING_PAGE_SIZE) -> int | None:
    """Which board page holds `doc_id`, or None when it is not awaiting review.

    Numbers the staged documents under `_staged_docs_page`'s own
    `_REVIEW_ORDER`, unfiltered, as the board a deep link opens is. The two
    must agree, or a deep link lands on a page that does not contain the row it
    promised. `None` is the answer for a document that was decided (or never
    existed) between the link being rendered and followed; the caller says so
    rather than showing page 0 silently.
    """
    row = conn.execute(
        _REVIEW_ROWS.format(search=_DOC_SEARCH, chip="true") + f"""
        SELECT pos FROM (
          SELECT doc_id, row_number() OVER (ORDER BY {_REVIEW_ORDER}) - 1 AS pos
            FROM (SELECT p.*, min(p.created_at) OVER (PARTITION BY p.group_name)
                         AS group_oldest
                    FROM picked p) ordered
        ) numbered WHERE doc_id = %s
        """,
        _doc_search_params("") + (doc_id,),
    ).fetchone()
    return None if row is None else row["pos"] // size


def _file_name(source_url: str | None, archive_url: str | None,
               content_hash: str | None) -> str | None:
    """The document's file name: the last segment of `source_url` (spec § 6).

    `source_url` has three shapes on dev: a corpus path from the SFTP drop, an
    http(s) URL, and `email:<hash>` for an attachment, which names no file at
    all. For that one, and for a row with no source, the archive's own name is
    the file name, with our `{hash12}__` prefix taken back off
    (`_download_name`). A URL's query and fragment are not part of the name,
    and its percent-escapes are decoded so a person reads "Cert EC 123.pdf".
    """
    src = (source_url or "").strip()
    parsed = urlsplit(src)
    if src.startswith("/"):
        name = src.rstrip("/").rsplit("/", 1)[-1]
    elif parsed.scheme in ("http", "https", "file"):
        name = unquote(parsed.path.rstrip("/").rsplit("/", 1)[-1])
    else:
        name = ""
    if name:
        return name
    archived = (archive_url or "").strip()
    if archived.startswith("file://"):
        archived = unquote(urlsplit(archived).path)
    tail = archived.rstrip("/").rsplit("/", 1)[-1]
    return _download_name(tail, content_hash) if tail else None


def _decorate_review_title(r: dict) -> dict:
    """The Review row's two lines (spec § 6): "⟨MANUFACTURER⟩ · ⟨type word⟩
    (⟨regulation⟩)", then "⟨file name⟩ · issued ⟨date⟩ · waiting since ⟨date⟩".

    The manufacturer is the row's group. Where there is none, the name the
    document printed stands in, marked as printed, since it is the one fact a
    reviewer needs to place the row; with neither, the type leads. A regulation
    of n.a. adds no brackets.

    The name is `words.doc_display_name`'s, not a second copy of its format
    string: the row and `/documents/{id}` are one click apart and must spell
    one document one way. It was built here from `_doc_type_word` -- the
    SENTENCE-SUBJECT form ("declaration of conformity"), written for the
    whole-range confirm's "This {word} says ..." -- so Review read "Ivoclar ·
    declaration of conformity (MDR)" and the page it opened read "Ivoclar ·
    Declaration of Conformity (MDR)". A row is a LABEL. The subject stays in
    the one sentence that needs one.
    """
    name = r.get("group_name")
    from_document = not name and bool(r.get("printed_manufacturer"))
    if from_document:
        name = r["printed_manufacturer"]
    if name:
        r["title"] = words.doc_display_name(
            {"manufacturer": name, "type": r.get("type"),
             "regulation": r.get("regulation")})
    else:
        # No name to lead: the label stands alone rather than being
        # sentence-cased into a third spelling of the same type.
        regulation = r.get("regulation")
        bracket = f" ({regulation})" if regulation in ("MDR", "MDD") else ""
        r["title"] = f"{words.doc_type_word(r.get('type'))}{bracket}"
    r["title_from_document"] = from_document
    # Linked items that belong to some other manufacturer than the row's own:
    # a document filed under one maker and linked to another's items.
    others = {n for n in (r.get("manufacturers") or []) if n and n != r.get("group_name")}
    r["title_extra"] = len(others) if r.get("group_name") else 0
    r["file_name"] = _file_name(r.get("source_url"), r.get("archive_url"),
                                r.get("content_hash"))
    # Dates a person reads, not ISO stamps (spec § 9, P7a). The subline is the
    # only place a date survived on the Review row after the grouped layout
    # dropped the "valid until" column.
    parts = [r["file_name"]] if r["file_name"] else []
    if r.get("validity_from"):
        parts.append(f"issued {words.day_text(r['validity_from'])}")
    if r.get("created_at"):
        parts.append(f"waiting since {words.day_text(r['created_at'])}")
    r["subline"] = " · ".join(parts)
    return r


def _basis_words(links: list[dict]) -> str:
    """How a set of links was made, in words, each basis once in order of
    first appearance ("Matched through ..." on the Review panel)."""
    seen = []
    for lk in links:
        words = BASIS_WORDS.get(lk["match_basis"], lk["match_basis"])
        if words not in seen:
            seen.append(words)
    return " and ".join(seen)


def _decorate_review_row(r: dict) -> dict:
    """Add the plain-language fields the review templates render.

    The manufacturer is derived from the linked items, not parsed out of the
    PDF: `item_mirror.manufacturer_raw` is a BC code that
    `manufacturer_alias` already maps to a canonical name, so for any
    item-scope document the catalogue is a more reliable source than the
    document text. Manufacturer-scope candidates have no links to derive from,
    which is exactly why they read "manufacturer unknown".
    """
    r["type_label"] = _doc_type_label(r.get("type"))
    r["regulation_label"] = REGULATION_LABELS.get(r.get("regulation") or "", r.get("regulation"))
    r["coverage_label"] = COVERAGE_LABELS.get(r.get("coverage_scope") or "", r.get("coverage_scope"))
    names = [n for n in (r.get("manufacturers") or []) if n]
    # Links first, then the name the document printed. CARL MARTIN doc 818
    # (2026-08-21) read "not known" on both review surfaces while `evidence`
    # held `Carl Martin GmbH` at page 1, T2, conf 0.98 -- a name the alias
    # table resolves. A document with no links has no better source, and it is
    # exactly the document being sent to a human to attribute; /manual already
    # fell back this way and /staging did not. Flagged rather than merged
    # silently: a name parsed off a PDF is weaker than a BC code somebody
    # curated, and the screen should say which one it is showing.
    #
    # `manufacturer_confirmed` is the name a HUMAN or the catalogue stands
    # behind -- a BC code somebody curated, or the subject of a binding task.
    # It is what the bind form may submit. The printed name is display only and
    # must never reach it: a manufacturer-scope approval writes links across a
    # whole catalogue, and "the model read this off page 1" is not that
    # decision. Conflating the two removed the picker from the very rows it
    # exists for and would have bound to an unchosen name.
    r["manufacturer_confirmed"] = names[0] if names else None
    r["manufacturer_from_document"] = not names and bool(r.get("printed_manufacturer"))
    if r["manufacturer_from_document"]:
        names = [r["printed_manufacturer"]]
    r["manufacturer_name"] = names[0] if names else None
    r["reason"] = _review_reason(
        list(r.get("flags") or []),
        is_mfr_binding=bool(r.get("is_mfr_binding")),
        has_task=r.get("task_id") is not None,
        task_reason=r.get("task_reason"),
    )
    return r


def _mfr_binding_options(conn) -> list[dict]:
    """EVERY canonical manufacturer in the catalogue, with the number of MD
    items a binding would link — zero included.

    This filtered on `md_flag IS TRUE` until 2026-08-31, which offered 9 of 366
    entities on live data. The reason was real at the time and is now gone:
    binding a manufacturer whose items all carry a blank BC device class linked
    nothing AND recorded nothing, so the reviewer's answer was discarded the
    moment they gave it. Migration 053 gives the decision somewhere to live, so
    the list opens: Denis, 2026-08-31 — the reviewer must be able to select any
    manufacturer, not only the ones whose items carry a device class.

    `[mfr-bind-empty-class]` is untouched. The LINKS still require
    `md_flag IS TRUE` — no MDR coverage is asserted over items nobody has
    classified — and only the OFFER changed.

    The count stays on every option, and stays a count of MD items rather than
    of all items, because it is the volume of what approving actually does:
    "approve for all IVOCLAR items" and "…1069 items" are the same decision
    stated two ways, and "…0 items" is the honest form of the 3Shape case.

    The count is `_mfr_bind_counts`, the one the button and the two-step
    confirm quote (fix round 1, M1, 2026-09-11). It was an exact alias join
    here until then, which disagreed with GATE wherever BC spelled one
    manufacturer two ways; the entity LIST still comes from that join.
    """
    names = [r["canonical_name"] for r in conn.execute(
        "SELECT DISTINCT COALESCE(a.canonical_name, m.manufacturer_raw) AS canonical_name "
        "FROM item_mirror m "
        "LEFT JOIN manufacturer_alias a ON a.raw_name = m.manufacturer_raw "
        "ORDER BY 1"
    ).fetchall()]
    counts = _mfr_bind_counts(conn, names)
    return [{"canonical_name": n, "md_items": counts[n] or 0} for n in names]


# `_mfr_unbindable_entities` lived here until 2026-08-31. It counted the
# entities the picker refused to offer, so the panel could explain a list that
# showed 9 of 366 rather than leave the reviewer concluding the UI had lost
# them. The picker now offers all of them (see `_mfr_binding_options`), so there
# is no exclusion left to count or apologise for.


def _mfr_bind_target(conn, name: str | None) -> dict | None:
    """What binding `name` would actually link, or None if a human must choose.

    Resolved exactly the way GATE will resolve it at apply time, because a
    button that promises one thing and a handler that does another is worse
    than no button: `resolve_canonicals` decides ambiguity (its list length is
    the guard C16 uses), `item_codes_for` does the alias -> canonical -> every
    BC code fan-out, and the count is over the MD items
    `_derive_mfr_scope_links` will actually link.

    None on any of: no name, more than one curated canonical (choosing between
    them is a coin flip over a manufacturer's whole range), a name that
    resolves to no codes, or codes holding no MD-flagged item.

    The count is on the LABEL, never on the payload's spelling. The label is
    what the button posts, what the confirm recounts, and what GATE resolves at
    apply time, so counting the other one made the button and the question
    disagree wherever the alias table folds a raw name to a label that maps
    somewhere else again: "ACME AG" fans out to {ACME AG, ACME} while "ACME",
    itself a vendor code under GLOBEX, fans out to {ACME, GLOBEX}. Same click,
    3 items on the button and 4 in the question.
    """
    if not name:
        return None
    canonicals = manufacturers.resolve_canonicals(conn, name)
    if len(canonicals) > 1:
        return None
    label = canonicals[0] if canonicals else name
    items = _mfr_bind_item_count(conn, label)
    if not items:
        return None
    return {"label": label, "items": items}


def _mfr_bind_counts(conn, names) -> dict[str, int | None]:
    """How many items a `bind-manufacturer` on each name would link: every MD
    item under every BC code the name fans out to, exactly as
    `gate._derive_mfr_scope_links` selects them. The codes come from
    `manufacturers.item_codes_resolver`, which is the implementation
    `item_codes_for` itself runs, read once for all the names.

    None where a name resolves to no BC code at all: GATE refuses that bind
    ([gate-bind-zero-links]), so it is not a count of zero. 0 where the codes
    exist but none of their items is a medical device: since 053 GATE records
    that binding and links nothing.

    The one count behind the picker's per-option figure, the one-click button
    (`bind_items`) and the two-step confirm's N, so no two of them can quote
    different numbers.
    """
    # Not `canonicals[0] or bust`: a manufacturer_raw with no alias row at all
    # is still bindable — `item_codes_for` keeps that direct-match path
    # deliberately, and dropping it here would make every entity missing from
    # `manufacturer_alias` unbindable through the UI while GATE binds it fine.
    codes_for = manufacturers.item_codes_resolver(conn)
    codes = {name: (codes_for(name) if name else []) for name in names}
    wanted = sorted({code for cs in codes.values() for code in cs})
    md = {r["manufacturer_raw"]: r["n"] for r in conn.execute(
        "SELECT manufacturer_raw, count(*) AS n FROM item_mirror "
        "WHERE md_flag IS TRUE AND manufacturer_raw = ANY(%s) "
        "GROUP BY manufacturer_raw",
        (wanted,),
    ).fetchall()} if wanted else {}
    return {name: (sum(md.get(code, 0) for code in cs) if cs else None)
            for name, cs in codes.items()}


def _mfr_bind_item_count(conn, name: str) -> int | None:
    """`_mfr_bind_counts` for one name: the items a bind on it links, or None
    when it resolves to no BC code at all."""
    return _mfr_bind_counts(conn, [name])[name]


def _doc_type_word(doc_type: str | None) -> str:
    """A document's type as it reads inside a sentence ("This {word} says …").

    `words.DOC_TYPE_SUBJECTS`, which spells each form out. It used to be
    derived by lower-casing `DOC_TYPE_LABELS` word by word, which produced two
    sentences nobody would write: "This instructions for use says …" (the one
    plural label, given a singular verb) and "MDR art. 22" (an abbreviation
    lower-cased because it is not `isupper()`).
    """
    return words.doc_type_subject(doc_type)


def _bind_confirm_lines(manufacturer: str, n: int, doc_type: str | None) -> list[str]:
    """Step one of a whole-range approval, in the spec's words (§ 3, P1a),
    except that the document is named by its own type. The spec said
    "declaration" throughout, and on dev 25 of the 34 waiting whole-range
    documents, and all 14 ever bound, are EC or ISO certificates (fix round 1,
    F1, controller ruling 2026-09-11).

    ", on screen and on their Business Central item card" is deliberately NOT
    appended to the third line. The spec makes it conditional on the BC card
    showing documents linked this way. The card's page (`/item/{ref}`,
    `web/item_docs.py`) does: it reads `item_document_production`, which has no
    `match_basis` filter, so an `mfr-scope` link on a production document is
    listed. But a BC card only links to that page once the BC push has written
    `pteWarehouseURL`, and on 2026-09-11 `bc_push_log` on dev held no rows.
    Add the phrase when pushes are live.
    """
    unit = "item" if n == 1 else "items"
    word = _doc_type_word(doc_type)
    # `words.num`, so the question, the button and the panel's own label all
    # read "1,069 items" -- one panel printing a count three ways is a panel
    # nobody can check at a glance.
    count = words.num(n)
    return [
        f"Approve for all {count} {manufacturer} {unit}?",
        f"This {word} says it covers everything {manufacturer} makes, so "
        f"approving it makes it count for every {manufacturer} item Business "
        f"Central marks as a medical device.",
        f"{count} {unit} will show this {word}.",
        f"It cannot be undone from this screen. A newer {word} can replace "
        f"it later.",
    ]


def _mfr_binding_suggestion(
    conn, evidence: list[dict], *, named: str | None = None
) -> dict | None:
    """What the document says its own manufacturer is, resolved to canonicals.

    The catalogue cannot answer this one: a manufacturer-scope document has no
    item links to derive a name from (`_decorate_review_row`), which is exactly
    why these rows read "manufacturer unknown". But EXTRACT usually read the
    name straight off the page — doc #1's `manufacturer` evidence is verbatim
    "This is to certify that: GC Europe N.V. …" — so the reviewer is being
    asked to supply something already sitting in the row's own evidence table.

    `resolve_canonicals` rather than `canonicalize`: its list length is the
    guard (0 unknown, 1 unambiguous, >1 a coin flip over every MD item of a
    manufacturer). Only a single unambiguous hit is preselected; the rest is
    shown as context beside a picker the human still has to operate.
    """
    # The task payload's name wins where there is one: VALIDATE canonicalizes
    # before it emits, so that string is at least as resolved as the raw
    # evidence value, and it is the string GATE itself judged (`gate.py`'s
    # `p.get("manufacturer") or _val(fields, "manufacturer")`) — the panel
    # should explain the same name the machine gave up on.
    raw = named or next(
        (e["value"] for e in evidence if e["field"] == "manufacturer" and e["value"]),
        None,
    )
    if not raw:
        return None
    canonicals = manufacturers.resolve_canonicals(conn, raw)
    preselect = canonicals[0] if len(canonicals) == 1 else None
    return {
        "raw": raw,
        "canonicals": canonicals,
        "preselect": preselect,
        # "GC EUROPE N.V." resolving to "GC EUROPE N.V." is a resolution the
        # reviewer does not need narrated at them; only a genuine rename
        # (Ivoclar Vivadent AG -> IVOCLAR) is worth the sentence.
        "same_spelling": bool(
            preselect
            and manufacturers.normalize(raw) == manufacturers.normalize(preselect)
        ),
    }


def _staged_doc_detail(conn, doc_id: int) -> dict | None:
    """Everything the summary row omits, for exactly one staged document:
    per-field evidence, item links, and the open gate-manual task explaining
    why it needs a human. None if the id is unknown or no longer staged —
    the row was decided in another tab and the expand should say so, not 500."""
    doc = conn.execute(
        "SELECT * FROM document WHERE doc_id=%s AND status='staged'", (doc_id,)
    ).fetchone()
    if doc is None:
        return None
    evidence = conn.execute(
        "SELECT field, value, tier, model_id, confidence, verbatim, page "
        "FROM evidence WHERE doc_id=%s ORDER BY field",
        (doc_id,),
    ).fetchall()
    # The product NAME, not just its ref: "003217" tells a reviewer nothing,
    # "G-CEM A2 50 KAPSUL" tells them whether this document belongs here.
    links = conn.execute(
        "SELECT l.item_ref, l.match_basis, l.status, m.name, "
        "  COALESCE(a.canonical_name, m.manufacturer_raw) AS manufacturer "
        "FROM item_document l "
        "LEFT JOIN item_mirror m ON m.item_ref = l.item_ref "
        "LEFT JOIN manufacturer_alias a ON a.raw_name = m.manufacturer_raw "
        "WHERE l.doc_id=%s AND l.status <> 'retracted' ORDER BY l.item_ref",
        (doc_id,),
    ).fetchall()
    # group_id is selected deliberately: the reject form pre-fills its
    # "retry this group" field from it, and the pre-pagination query omitted
    # the column, so that prefill was silently always empty.
    task = conn.execute(
        "SELECT id, kind, group_id, payload FROM manual_task "
        "WHERE doc_id=%s AND status='open' ORDER BY id LIMIT 1",
        (doc_id,),
    ).fetchone()
    payload = (task or {}).get("payload") or {}
    # The offer is a property of the DOCUMENT, not of how its task got labelled.
    # VALIDATE routes to `mfr-binding` only when a manufacturer-scope document
    # carries no ref_list AND no Basic UDI-DI (`validate.py:720`). Doc 657
    # (2026-08-21) has a Basic UDI-DI that matches no item in the catalogue, so
    # it was excluded from the route, then hit `manufacturer-unresolved` -- a
    # BLOCKING flag -- and became a plain gate-manual. Manufacturer scope, zero
    # links, and no way to say whose it is: Approve would write zero links and
    # Reject throws away a real certificate. Two documents sit in that state.
    #
    # Binding is meaningful for exactly this shape, whatever the task says, and
    # the server-side action is unchanged and still guarded: `gate.apply`
    # bind-manufacturer refuses a name resolving to no BC code, and only
    # entities holding an MD item are offered.
    is_mfr_binding = bool(
        (task and task["kind"] == "gate-manual"
         and payload.get("route") == "mfr-binding")
        or (doc["coverage_scope"] == "manufacturer" and not links)
    )
    row = {
        "doc": doc, "evidence": evidence, "links": links,
        "task": task, "is_mfr_binding": is_mfr_binding,
        "type": doc["type"], "regulation": doc["regulation"],
        "coverage_scope": doc["coverage_scope"],
        "flags": list(payload.get("flags") or []),
        "task_id": task["id"] if task else None,
        # Same field the board query reads, so the detail page and the row it
        # expands from cannot disagree about why this is here.
        "task_reason": payload.get("reason"),
        # mfr-binding candidates carry no item links, so the manufacturer can
        # only come from the task payload — which is null on every open one
        # today ([mfr-binding-null-manufacturer] in tasks/followups.md). Left
        # as None so the template says "unknown" and refuses to bind, rather
        # than binding to a manufacturer literally named "None".
        "manufacturers": (
            [payload["manufacturer"]] if is_mfr_binding and payload.get("manufacturer")
            else [lk["manufacturer"] for lk in links if lk["manufacturer"]]
        ),
        # Third source, and the ONLY one for a document with no links -- which
        # is precisely the document a human is being asked to attribute. Read
        # from the evidence already fetched above, so no extra query.
        "printed_manufacturer": next(
            (e["value"] for e in evidence
             if e["field"] == "manufacturer" and e["value"]), None),
    }
    # What approving does to the links (spec § 6, "Approving makes it count for
    # these N items"). A production link counts once the document does; a
    # staged one follows the document only on a publishing basis
    # (`PUBLISHING_BASES`, gate's `_promote_pending_links`). The rest stay
    # staged and each needs its own decision afterwards, so they are listed
    # apart and never promised. Rejected links count for nothing either way.
    row["approve_links"] = [
        lk for lk in links
        if lk["status"] == "production"
        or (lk["status"] == "staged" and lk["match_basis"] in PUBLISHING_BASES)]
    row["held_links"] = [
        lk for lk in links
        if lk["status"] == "staged" and lk["match_basis"] not in PUBLISHING_BASES]
    row["approve_basis"] = _basis_words(row["approve_links"])
    row["held_basis"] = _basis_words(row["held_links"])
    row["regulation_words"] = REGULATION_WORDS.get(doc["regulation"] or "",
                                                   doc["regulation"] or "Not stated")
    _decorate_review_row(row)
    # Binding candidates only, and every one of them: the queue exists to
    # decide which manufacturer a manufacturer-scope certificate covers, and
    # until now the panel could not express that decision. 14 of the 52
    # documents awaiting review carried no name at all and got a disabled
    # button with no way to change it, while /manual pointed here to resolve
    # them — a closed loop, not a queue.
    #
    # A name in the payload is NOT a decision either. C16 (2026-08-17) binds
    # every manufacturer-scope document whose name resolves to exactly one
    # curated canonical without asking anyone, so a candidate that still
    # reaches this queue is one whose name was absent, unknown or ambiguous —
    # and the payload carries the raw string the document printed, not a
    # canonical. Offering "Approve for all Ivoclar Vivadent AG items" on
    # that string would be a one-click coin flip over a manufacturer's whole
    # range, resolved by `item_codes_for` after the fact or silently binding
    # nothing. So the name is resolved HERE, and only an unambiguous, bindable
    # canonical keeps the one-click form; everything else gets the picker.
    if row["is_mfr_binding"]:
        # The CONFIRMED name, never the printed one. A binding candidate has no
        # links by definition, so the display fallback would hand this the name
        # the model read off page 1 -- and `_mfr_bind_target` would turn it into
        # a one-click "approve for all X items" over a manufacturer's whole
        # range. The printed name earns a PRESELECTION in the picker below
        # (`_mfr_binding_suggestion` reads the same evidence) and nothing more:
        # a person still confirms it.
        named = row["manufacturer_confirmed"]
        target = _mfr_bind_target(conn, named)
        if target:
            # Bind by the curated label rather than the document's spelling
            # where there is one: `_derive_mfr_scope_links` accepts both, but
            # the audit entry and the button should name the entity the
            # catalogue knows.
            row["manufacturer_name"] = target["label"]
            row["manufacturer_confirmed"] = target["label"]
            row["bind_items"] = target["items"]
            return row
        # Neither name nor decision survives an unresolvable payload string:
        # `manufacturer_name` so the panel stops asserting one, and
        # `manufacturer_confirmed` so the one-click bind form gives way to the
        # picker. They are cleared together because they were set together --
        # from a raw spelling the catalogue could not place.
        row["manufacturer_confirmed"] = None
        # The header keeps the printed name where there is one. Nulling it was
        # right while one value drove both the panel and the bind form; now
        # that `manufacturer_confirmed` guards the form, hiding the name only
        # costs the reviewer the one fact they came for -- doc 657 showed
        # "not known" over evidence reading `Inter-Med, Inc.`, page 1, 0.95.
        row["manufacturer_name"] = (
            row["printed_manufacturer"] if row.get("manufacturer_from_document") else None
        )
        options = _mfr_binding_options(conn)
        bindable = {o["canonical_name"]: o["md_items"] for o in options}
        suggestion = _mfr_binding_suggestion(conn, evidence, named=named)
        if suggestion and suggestion["preselect"]:
            # Every catalogue entity is offered since 2026-08-31, so a resolved
            # name is always in `bindable` and a zero-MD-item manufacturer
            # preselects like any other — the 3Shape case, which used to be
            # withheld from the list and explained in prose instead.
            #
            # `.get` still guards rather than indexes: a name resolving to an
            # entity the CATALOGUE does not hold (a playbook alias for a
            # manufacturer we stock nothing from) is genuinely absent from the
            # options, and must not be preselected into a list that lacks it.
            items = bindable.get(suggestion["preselect"])
            if items is None:
                suggestion["preselect"] = None
            else:
                suggestion["preselect_items"] = items
        row["mfr_options"] = options
        row["mfr_suggestion"] = suggestion
    return row


def _staged_links_on_production(
    conn, *, limit: int = STAGED_LINKS_LIMIT
) -> tuple[list[dict], int]:
    """C5: a production document may still carry staged (untrusted-basis)
    links to other items — visible here, distinct from the staged docs above.

    Capped rather than paginated: a flat table with no forms and nothing to
    expand. `(rows, total)` so the template can say what the cap hid instead
    of truncating silently (CLAUDE.md: skipped rows are counted, never silent).
    """
    total = conn.execute(
        "SELECT count(*) AS n FROM item_document lnk JOIN document d USING (doc_id) "
        "WHERE lnk.status = 'staged' AND d.status = 'production'"
    ).fetchone()["n"]
    rows = conn.execute(
        """
        SELECT lnk.item_ref, lnk.doc_id, lnk.match_basis, lnk.status,
               d.type, d.regulation, d.cert_number
        FROM item_document lnk JOIN document d USING (doc_id)
        WHERE lnk.status = 'staged' AND d.status = 'production'
        ORDER BY lnk.doc_id, lnk.item_ref
        LIMIT %s
        """,
        (limit,),
    ).fetchall()
    return rows, total


def _staged_links_on_unpublished(
    conn, *, limit: int = STAGED_LINKS_LIMIT
) -> tuple[list[dict], int]:
    """C17: staged links whose document reached a TERMINAL state without ever
    being published — `superseded` or `filed`. Read-only by ruling, not by
    omission.

    These look like the `_staged_links_on_production` queue and are the exact
    opposite of it. Nothing is waiting on a person: the document is not
    production and never will be, so `gate.apply`'s link decisions refuse them
    (C17 requires a production document) and no `approve` will ever run
    `_promote_pending_links` over them.

    They were briefly reported as a defect (17 `map-supplier` links under docs
    375/379) and the finding was WITHDRAWN the same day. Both are older DoCs the
    task-5 fast path auto-filed into the 375/379 -> 415 -> 377 chain: each was
    `filed` first -- C15 requires an empty `links` payload, so the links cannot
    have existed at that point -- and reached `superseded` on a later rev whose
    coverage-map payload carried them. Neither was EVER production. Promoting
    them would assert production-grade coverage under paperwork the registry
    never published, which is inventing history, so the status stays.

    What was actually wrong was that they were invisible: terminal dead state
    that no screen mentioned, which is indistinguishable from state nobody
    thought about. Hence this table -- legible, and deliberately without a form.
    """
    where = ("WHERE lnk.status = 'staged' AND d.status IN ('superseded', 'filed')")
    total = conn.execute(
        f"SELECT count(*) AS n FROM item_document lnk JOIN document d USING (doc_id) {where}"
    ).fetchone()["n"]
    rows = conn.execute(
        f"""
        SELECT lnk.item_ref, lnk.doc_id, lnk.match_basis, d.status AS doc_status,
               d.type, d.regulation, d.cert_number, d.superseded_by
        FROM item_document lnk JOIN document d USING (doc_id)
        {where}
        ORDER BY lnk.doc_id, lnk.item_ref
        LIMIT %s
        """,
        (limit,),
    ).fetchall()
    return rows, total


def _open_suggestions_page(
    conn, *, page: int = 0, size: int = STAGING_PAGE_SIZE, q: str = ""
) -> tuple[list[dict], int]:
    """One page of open RESOLVE T1 staging rows (migration 012, spec
    resolve.md §5), as summary rows: the item, its best candidate group, how
    many candidates there are, and whether T1 errored instead of adjudicating.
    The candidate table, the rationale and the assign form live in
    `/staging/suggestion/{id}/detail`.

    `candidates` is stored score-DESC by app/handlers/resolve.py, so index 0
    is genuinely the top candidate. Ordering is `created_at, id`: created_at
    alone is not a total order, and OFFSET paging over ties silently skips and
    repeats rows.

    `q` filters on item_ref — at 5.8k open suggestions, prev/next alone cannot
    reach a specific item in useful time.
    """
    like = f"%{_like_escape(q)}%" if q else ""
    total = conn.execute(
        "SELECT count(*) AS n FROM grouping_suggestion "
        "WHERE status='open' AND (%s = '' OR item_ref ILIKE %s)",
        (q, like),
    ).fetchone()["n"]
    # `item_name` is the item being merged; `top_sample_name` is the group it
    # would be merged INTO. Showing only the latter is how the live GC board
    # rendered `003234 ... FUJI PLUS A3 50 KAPSUL` for an item actually named
    # FUJI II LC KAPSULE A2 50KOS -- a different device, scoring 0.73 on shared
    # FUJI / A3 / 50 KAPSUL tokens -- with the assign form pre-filled to that
    # group. LEFT JOIN rather than JOIN only so the board cannot silently drop
    # rows: today `grouping_suggestion_item_ref_fkey` (ON DELETE NO ACTION)
    # guarantees the mirror row exists, and this does not depend on it holding.
    rows = conn.execute(
        "SELECT gs.id, gs.item_ref, gs.score, gs.created_at, im.name AS item_name, "
        "  jsonb_array_length(gs.candidates) AS n_candidates, "
        "  gs.candidates->0->>'group_id' AS top_group, "
        "  gs.candidates->0->>'sample_name' AS top_sample_name, "
        "  (gs.suggestion->>'t1_error') IS NOT NULL AS t1_failed "
        "FROM grouping_suggestion gs "
        "LEFT JOIN item_mirror im ON im.item_ref = gs.item_ref "
        "WHERE gs.status='open' AND (%s = '' OR gs.item_ref ILIKE %s) "
        "ORDER BY gs.created_at, gs.id LIMIT %s OFFSET %s",
        (q, like, size, max(page, 0) * size),
    ).fetchall()
    return rows, total


def _suggestion_detail(conn, suggestion_id: int) -> dict | None:
    """The full candidate list and T1 rationale for one open suggestion, or
    None if it is unknown or already resolved (another tab got there first)."""
    return conn.execute(
        "SELECT gs.id, gs.item_ref, gs.candidates, gs.suggestion, gs.score, "
        "       gs.created_at, im.name AS item_name "
        "FROM grouping_suggestion gs "
        "LEFT JOIN item_mirror im ON im.item_ref = gs.item_ref "
        "WHERE gs.id=%s AND gs.status='open'",
        (suggestion_id,),
    ).fetchone()


def _pager(page: int, size: int, total: int) -> dict:
    """Shared prev/next + "51-100 of 5831" state for the staging sections."""
    page = max(page, 0)
    start = page * size
    return {
        "page": page,
        "total": total,
        "first": start + 1 if total else 0,
        "last": min(start + size, total),
        "has_prev": page > 0,
        "has_next": start + size < total,
    }


def _manual_tasks(conn, q: str = "") -> list[dict]:
    """Open tasks, newest last. `q` searches the DOCUMENT each task is about,
    with the same predicate as /documents and /staging.

    A task with no `doc_id` -- `discovery-dead-end`, which is about a group and
    not a document -- can match only on its payload, so an unsearched board
    still shows it and a searched one does not. That is the honest behaviour:
    a person typing a manufacturer is asking about documents."""
    if not q:
        return conn.execute(
            "SELECT * FROM manual_task WHERE status='open' ORDER BY created_at"
        ).fetchall()
    like = f"%{_like_escape(q)}%"
    return conn.execute(
        "SELECT mt.* FROM manual_task mt "
        "JOIN document d ON d.doc_id = mt.doc_id "
        "WHERE mt.status='open' AND " + _DOC_SEARCH + " "
        "UNION "
        "SELECT mt.* FROM manual_task mt "
        "WHERE mt.status='open' AND mt.doc_id IS NULL "
        "  AND mt.payload::text ILIKE %s "
        "ORDER BY created_at",
        _doc_search_params(q) + (like,),
    ).fetchall()


def _describe_manual_docs(conn, tasks: list[dict]) -> None:
    """Give every manual task that names a document the same one-line headline
    the review board gives it, in place.

    Without it the board says "Task #3 — gate-manual" over forty lines of
    `tier_attempts` JSON, and a person has to read a model's confidence scores
    to learn they are looking at an IVOCLAR declaration. The queue's whole job
    is to say what is waiting; the kind of task is the least of it.

    Deliberately the SAME derivation as the review board (`_decorate_review_row`
    over the manufacturers the LINKED ITEMS carry), so one document does not
    describe itself two different ways on two screens. A manufacturer-binding
    candidate has no links by definition, so it falls back to the name the
    document printed — which the payload already carries, and which is the
    entire subject of that task.
    """
    doc_ids = sorted({t["doc_id"] for t in tasks if t.get("doc_id")})
    if not doc_ids:
        return
    facts = {
        r["doc_id"]: r
        for r in conn.execute(
            """
            SELECT d.doc_id, d.type, d.regulation, d.coverage_scope, d.status,
                   d.validity_to, d.cert_number,
                   (SELECT array_agg(DISTINCT COALESCE(a.canonical_name, m.manufacturer_raw))
                      FROM item_document l
                      JOIN item_mirror m ON m.item_ref = l.item_ref
                      LEFT JOIN manufacturer_alias a ON a.raw_name = m.manufacturer_raw
                     WHERE l.doc_id = d.doc_id AND l.status <> 'retracted') AS manufacturers,
                   (SELECT e.value FROM evidence e
                     WHERE e.doc_id = d.doc_id AND e.field='manufacturer' LIMIT 1) AS printed_manufacturer
              FROM document d
             WHERE d.doc_id = ANY(%s)
            """,
            (doc_ids,),
        ).fetchall()
    }
    for t in tasks:
        row = facts.get(t.get("doc_id"))
        if row is None:
            continue
        t["doc"] = _decorate_review_row(dict(row))
        # Three sources, best first. Linked items are the most reliable (a BC
        # code a human curated), then the name the binding task is ABOUT, then
        # the name the document itself printed. The last one is why 323 of 326
        # documents can be attributed at all, and a task that reaches it is
        # precisely the task with no links to derive from — the case the
        # heading was blank for.
        if not t["doc"]["manufacturer_name"]:
            t["doc"]["manufacturer_name"] = (
                (t["payload"] or {}).get("manufacturer") or row["printed_manufacturer"]
            )


def _canonical_pages_for(conn, names) -> dict[str, str]:
    """Map each spelling in `names` to the manufacturer page it can link to.

    `manufacturers.resolve_canonicals` answers this for ONE name and reads the
    whole alias table to do it, which is right for a detail page and wrong for
    a list: 50 rows would mean 50 full reads. This folds the same table once
    and answers the whole page from it.

    The semantics are resolve_canonicals', deliberately, so a name links to the
    same page in a list as it does on the document itself: match on EITHER side
    of the alias row (a document prints its legal entity, the catalogue holds a
    vendor code), and the count is the guard -- 0 is a miss and >1 is a fold
    two curated entries disagree about, and neither is a page we may link to.
    """
    wanted = {manufacturers.normalize(n) for n in names if n}
    wanted.discard("")
    if not wanted:
        return {}
    folded: dict[str, set[str]] = {}
    for r in conn.execute(
        "SELECT raw_name, canonical_name FROM manufacturer_alias"
    ).fetchall():
        for key in (manufacturers.normalize(r["raw_name"]),
                    manufacturers.normalize(r["canonical_name"])):
            if key in wanted:
                folded.setdefault(key, set()).add(r["canonical_name"])
    return {k: next(iter(v)) for k, v in folded.items() if len(v) == 1}


# How much of `last_error` decides that two dead jobs died of the same thing.
# The leading text of a Python traceback's final line is the exception class and
# its message; the tail is the row/id that happened to trip it. 120 characters
# keeps "CheckViolation ... violates check constraint
# "document_coverage_scope_vocabulary"" together while dropping the failing
# row's own values, which is exactly the cut that turns 47 cards into 3 causes.
_ERROR_SIGNATURE_CHARS = 120


def _error_headline(error: str | None) -> str:
    """The one line that names what killed a job.

    A dead card is a CAUSE, and its heading has to say which — four cards
    reading `gate.candidate`, `gate.candidate`, `extract.doc`, `extract.doc`
    look like an ungrouped list of job types, which is exactly how the first
    version read — the cards did not look grouped at all. The job type is
    secondary; two of them here died of completely different things.

    Postgres puts the useful half on the first line and the offending row on
    the `DETAIL:` line after it, so the first line alone is the cause and the
    rest is the instance.
    """
    if not error:
        return "no error recorded"
    first = error.strip().split("\n", 1)[0].strip()
    return first if len(first) <= 110 else first[:109].rstrip() + "…"


def _group_digest(job_type: str, signature: str) -> str:
    """Stable id for one (type, error signature) group.

    The form used to post the signature itself: 120 characters of raw error
    text, quotes and embedded newlines, through a hidden input. It round-trips
    today, but a browser normalising line endings on submit would make the
    server's re-derivation match zero jobs and report the group as vanished —
    a silent no-op dressed as a stale-board message. A digest cannot suffer
    that, and the server recomputes it over the live grouping anyway, so
    nothing trusts the client with the query.
    """
    return hashlib.sha256(f"{job_type}\x00{signature}".encode()).hexdigest()[:16]


def _dead_job_groups(conn, *, ids: list[int],
                     limit: int = DEFAULT_JOB_LIST_LIMIT) -> list[dict]:
    """Dead jobs collapsed to one row per (type, error signature).

    47 dead jobs on the live board are 3 failures: 29 share one CheckViolation
    on `document_coverage_scope_vocabulary`, 12 a missing `regulation` field, 6
    an API 400. Rendering them as 47 full-height cards buries that, and the
    reader's actual question — "what is broken, and did fixing it help" — is
    answered by the group, not by the 29th instance of it.

    Grouped in SQL so the counts are the whole of `ids`, not the page: a group
    that says 29 must mean 29, and `jobs` (capped) is what the expanded card
    lists. The representative error is `min(last_error)` rather than an
    arbitrary row's, so the same input always renders the same text.

    `ids` is the developer section of the Failed split (web/failures.py), and
    nothing else: a timeout or a dead address has its own button, and a dead
    row whose work was already re-run no longer counts, so re-running its
    group would do the same work twice. The board and `dead_rerun_group` both
    pass the split's ids, so a digest selects among the same groups at render
    and at click.
    """
    rows = conn.execute(
        """
        SELECT type,
               left(coalesce(last_error, ''), %s) AS signature,
               count(*)                           AS n,
               min(last_error)                    AS sample_error,
               max(finished_at)                   AS last_died,
               (array_agg(id ORDER BY id DESC))[1:%s] AS job_ids
        FROM job
        WHERE status = 'dead' AND id = ANY(%s::bigint[])
        -- ordinal, not the expression again: two placeholders holding the same
        -- value are still two different expressions to Postgres, so repeating
        -- the left(coalesce(...)) call here raises GroupingError. (Nor can a
        -- placeholder appear in this comment: psycopg counts those too.)
        GROUP BY type, 2
        ORDER BY count(*) DESC, type
        """,
        (_ERROR_SIGNATURE_CHARS, limit, ids),
    ).fetchall()
    for g in rows:
        g["headline"] = _error_headline(g["sample_error"])
        g["digest"] = _group_digest(g["type"], g["signature"])
    return rows


def _item_mirror_summary(conn) -> dict:
    """One total, not a breakdown. This used to GROUP BY `catalogue`, which
    holds one value, so the board rendered a one-row table under a "Catalogue"
    heading (2026-08-26)."""
    return conn.execute("SELECT count(*) AS n FROM item_mirror").fetchone()


# --------------------------------------------------------------------------- #
# KPI board v1 (S1.6, GAP G14) — one function per docs/specs/kpi.md metric
# (K1-K8), plus board counters that are not kpi.md metrics (e.g.
# `_kpi_sweep_due_count`, added for the sweep-due stat tile). Every K1-K8
# query reads item_document_production (or another view), never a raw
# document/item_document join — see that spec's §0 rule.
# --------------------------------------------------------------------------- #
def _kpi_coverage(conn) -> dict:
    """K1: coverage % — three lines over the whole mirror. `docs/specs/kpi.md`
    defines it per catalogue; there is one, so the grouping went with the
    dimension (2026-08-26) and this returns one row rather than a list of one.

    Two axes, not one. The DENOMINATOR varies because `md_flag` is tri-state
    (61% of the LJ export is NULL/unclassified) and a single number would have
    to silently pick one (kpi.md §3.1): hence strict vs processed-scope. The
    NUMERATOR varies because "we hold paper for this article" and "this
    article's device conformity is evidenced" are different questions that a
    quality-system certificate pulls apart: hence any-paper vs device."""

    #: Lapse changes this KPI and nothing else (Denis ruling 2026-08-24,
    #: [expiry-is-reported-never-enforced]): documents keep their status,
    #: links stay, the chase continues, but evidence past its effective
    #: expiry no longer counts as coverage. `expires` is the view's
    #: 027-derived value, so a stated date, a cert-inherited one and the
    #: five-year staleness horizon all disqualify alike -- the measured 85
    #: lapsed documents split 64 stated / 21 staleness and the ruling was
    #: made on the combined figure. Measured 2026-08-24: 40 of 4.598 covered
    #: items had no unexpired evidence at all; this line is what stops the
    #: headline overstating by those 40.
    UNEXPIRED = "(p.expires IS NULL OR p.expires >= CURRENT_DATE)"

    #: Any production document at all. This is what "covered" meant until
    #: 2026-08-21 and it is still worth reporting -- it answers "have we got
    #: paper for this article".
    ANY_PAPER = ("EXISTS (SELECT 1 FROM item_document_production p "
                 f"WHERE p.item_ref = m.item_ref AND {UNEXPIRED})")

    #: Production paper issued under a DEVICE regulation. An ISO 13485
    #: certificate is real, current and production, and it evidences the
    #: manufacturer's quality system -- never a given article's conformity.
    #: The client ruling of 2026-08-18 says non-MDR documents "bind to the
    #: manufacturer, exclude from device coverage"; until this line existed,
    #: nothing implemented the second half. CARL MARTIN's backfill landed
    #: 2026-08-21 and read 2.567/2.567 = 100% strict on ONE QMS certificate,
    #: with both of its MDR declarations still in staging contributing zero.
    #: Filtered on `regulation`, not `type`: the ruling is about which regime
    #: a document was issued under, and 258 items are covered by documents
    #: that are type ISO under regulation MDR, which do count.
    DEVICE_PAPER = ("EXISTS (SELECT 1 FROM item_document_production p "
                    "WHERE p.item_ref = m.item_ref "
                    f"AND p.regulation IN ('MDR', 'MDD') AND {UNEXPIRED})")

    #: Production paper that DECLARES the device conforms. `DEVICE_PAPER` above
    #: filters on `regulation`, and an MDR Annex IX quality-system certificate
    #: satisfies that -- it is issued under MDR and says nothing whatever about
    #: any article's conformity. So `device` still counts an item as evidenced
    #: on the strength of a certificate about the manufacturer's processes.
    #:
    #: A Declaration of Conformity is the manufacturer's own statement about the
    #: DEVICE, and it is the only production document that answers the question
    #: a client is actually asking. Measured 2026-09-03 over the 4.265 confirmed
    #: MD items: 4.254 hold some production paper (99,7%), 1.684 hold paper
    #: under a device regulation (39,5%), 1.461 hold a declaration of any age
    #: (34,3%), and 489 hold an UNEXPIRED one (11,5%). The gap between the last
    #: two is the 972-device expired-declaration pile.
    #:
    #: 99,7% was the headline until this line existed, and it is indefensible in
    #: front of Dentalia -- the 2026-08-31 delivery report had to quote
    #: article-level coverage and explain why, which was the workaround.
    DOC_PAPER = ("EXISTS (SELECT 1 FROM item_document_production p "
                 "WHERE p.item_ref = m.item_ref "
                 f"AND p.type = 'DoC' AND {UNEXPIRED})")

    def _row(where: str, covered: str) -> dict:
        return conn.execute(
            f"""
            SELECT count(*) FILTER (WHERE {covered}) AS covered,
                   count(*) AS total
            FROM item_mirror m
            WHERE {where}
            """
        ).fetchone()

    strict = _row("m.md_flag IS TRUE", ANY_PAPER)
    processed = _row("m.md_flag IS NOT FALSE", ANY_PAPER)
    # Same denominator as `strict` on purpose: strict and device differ only in
    # what counts as covered, so the pair reads as one number and its honest
    # subset rather than as two unrelated percentages.
    device = _row("m.md_flag IS TRUE", DEVICE_PAPER)
    # Same denominator again, and a strict subset of `device`: the four lines
    # differ only in what counts as covered, so they read as one number and its
    # progressively honest subsets rather than four unrelated percentages.
    declared = _row("m.md_flag IS TRUE", DOC_PAPER)

    def _pct(r):
        return round(r["covered"] / r["total"] * 100, 1) if r["total"] else None

    return {
        "strict_covered": strict["covered"], "strict_total": strict["total"],
        "strict_pct": _pct(strict),
        "processed_covered": processed["covered"], "processed_total": processed["total"],
        "processed_pct": _pct(processed),
        "device_covered": device["covered"], "device_total": device["total"],
        "device_pct": _pct(device),
        "doc_covered": declared["covered"], "doc_total": declared["total"],
        "doc_pct": _pct(declared),
    }


#: How a Declaration of Conformity is linked "at article level": by the
#: supplier's article number on the document (`ref-list`), our own item number
#: (`ref-item`), a Basic UDI-DI match, or the supplier's own coverage map
#: (`map-supplier`). AC1, the client acceptance metric (docs/decisions.md,
#: 2026-09-11, G15), names exactly these four. Left out on purpose: `mfr-scope`
#: (a whole-range declaration says nothing about which article) and, by the
#: same ruling, `manual`.
DECLARATION_BASES = ("ref-list", "ref-item", "basic-udi-di", "map-supplier")

#: "This item has a declaration on file", over `p`, a row of
#: `item_document_production` (document AND link both production). ONE
#: predicate, read by `coverage_headline` and by Coverage gaps' "No Declaration
#: of Conformity" list, so the headline's `total - covered` and that list's
#: count cannot drift apart. They did: the gaps page counted any production
#: DoC link, so a whole-range or hand-confirmed declaration closed a gap there
#: that the acceptance metric does not count. No expiry filter: AC1 counts
#: declarations on file, and states the unexpired share separately.
_HAS_DECLARATION = (
    "p.type = 'DoC' AND p.match_basis IN ("
    + ", ".join(f"'{b}'" for b in DECLARATION_BASES) + ")"
)


#: What each item holds, one row per item with any production link: one scan,
#: three answers. Coverage gaps' three lists and `coverage_headline` both run
#: it, so the headline's covered count and the gaps page's "No Declaration of
#: Conformity" count come out of the same SQL. The obvious shape -- a
#: correlated NOT EXISTS per gap -- measured 665ms against 14,7ms for this on
#: the same data, and returns identical counts. The same held for the
#: headline: a correlated EXISTS per device item cost the planner ~318k, over
#: `jit_above_cost`, so JIT compiled on every call (70-205 ms over three runs
#: on dev, 2026-09-11), where this plans at ~1k and ran in 22-33 ms, no JIT.
_COVERAGE_HELD = f"""
    WITH held AS (
      SELECT p.item_ref,
             bool_or({_HAS_DECLARATION})              AS has_doc,
             bool_or(d.regulation IN ('MDR','MDD'))   AS has_regime,
             array_agg(DISTINCT d.type)               AS types
      FROM item_document_production p
      JOIN document d ON d.doc_id = p.doc_id
      GROUP BY p.item_ref
    )
"""


def coverage_headline(conn) -> dict:
    """D4: "Declarations on file for X of Y medical-device items (Z%)".

    `total` is the items Business Central marks as medical devices
    (`md_flag IS TRUE`), `covered` those with a declaration on file
    (`_HAS_DECLARATION`), and `unclassified` the items BC has not classified
    at all (`md_flag IS NULL`), which the sentence names rather than folds in --
    the same scope and the same unclassified count Coverage gaps uses. `pct` is
    0-100 to one decimal, and 0.0 on an empty mirror so it stays a float.

    It replaced the 99.7% "any production paper" tile on 2026-09-11; the four
    `_kpi_coverage` lines stay one click down, each under its own name. Read
    over the production view like every K1 line (docs/specs/kpi.md §0), through
    `_COVERAGE_HELD`, the CTE Coverage gaps counts with.
    """
    row = conn.execute(
        _COVERAGE_HELD + """
        SELECT count(*) FILTER (WHERE im.md_flag IS TRUE)          AS total,
               count(*) FILTER (WHERE im.md_flag IS TRUE
                                  AND COALESCE(h.has_doc, false))  AS covered,
               count(*) FILTER (WHERE im.md_flag IS NULL)           AS unclassified
        FROM item_mirror im
        LEFT JOIN held h USING (item_ref)
        """
    ).fetchone()
    total, covered = row["total"], row["covered"]
    return {
        "covered": covered,
        "total": total,
        "pct": round(covered / total * 100, 1) if total else 0.0,
        "unclassified": row["unclassified"],
    }


#: One vocabulary (spec § 9, P7a): the month names, the day and the week now
#: live in `web/words.py` and every screen reads them from there. Re-exported
#: under the names this module already published so nothing that imports
#: `web.app.day_text` has to know the definition moved.
_MONTHS = words.MONTHS
day_text = words.day_text


def _review_summary(conn) -> dict:
    """Today's "Documents to review" list (spec § 4).

    `n` and `oldest` are the staging queue's own two figures (the same
    `document.status='staged'` predicate `/staging` pages over, so the number
    on Today is the number on Review). `top` names the three manufacturers
    with the most, which is what makes the list actionable before you open it.

    The manufacturer is derived exactly as `_decorate_review_row` derives it:
    the canonical name of the items the document is linked to first, and the
    name printed on the document only when it has no links. Copying that rule
    rather than calling it keeps Today to two queries instead of a page of
    decorated rows; the two must agree, and the test asserts they do.
    """
    row = conn.execute(
        "SELECT count(*) AS n, min(created_at) AS oldest "
        "FROM document WHERE status='staged'"
    ).fetchone()
    # Aggregate joins rather than two correlated subqueries per staged row:
    # neither `item_document` nor `evidence` has an index leading with
    # `doc_id`, so the correlated form ran a sequential scan of each PER
    # DOCUMENT -- 560ms over 236 staged documents on dev (2026-09-14), against
    # 20ms for this. Same rule, same answer, one scan of each table.
    top = conn.execute(
        """
        WITH staged AS (
          SELECT doc_id FROM document WHERE status = 'staged'
        ), linked AS (
          SELECT l.doc_id,
                 min(COALESCE(a.canonical_name, m.manufacturer_raw)) AS manufacturer
          FROM item_document l
          JOIN staged s USING (doc_id)
          JOIN item_mirror m ON m.item_ref = l.item_ref
          LEFT JOIN manufacturer_alias a ON a.raw_name = m.manufacturer_raw
          WHERE l.status <> 'retracted'
          GROUP BY l.doc_id
        ), printed AS (
          SELECT DISTINCT ON (e.doc_id) e.doc_id, e.value AS manufacturer
          FROM evidence e
          JOIN staged s USING (doc_id)
          WHERE e.field = 'manufacturer'
          ORDER BY e.doc_id
        )
        SELECT COALESCE(l.manufacturer, p.manufacturer) AS manufacturer,
               count(*) AS n
        FROM staged s
        LEFT JOIN linked l USING (doc_id)
        LEFT JOIN printed p USING (doc_id)
        WHERE COALESCE(l.manufacturer, p.manufacturer) IS NOT NULL
        GROUP BY 1 ORDER BY n DESC, 1 LIMIT 3
        """
    ).fetchall()
    oldest = row["oldest"]
    return {
        "n": row["n"],
        "oldest": oldest.date() if oldest is not None else None,
        "top": [(r["manufacturer"], r["n"]) for r in top],
    }


def _drafts_waiting(conn) -> int:
    """Renewal mails a person still has to send.

    `draft` and `ready`, not "everything that is not `sent`": a `cancelled`
    draft was deliberately dropped and is not work (migration 029 holds the
    four-value vocabulary). The list is headed "Renewal emails to send", so it
    counts the ones there are to send.
    """
    return conn.execute(
        "SELECT count(*) AS n FROM email_draft WHERE status IN ('draft','ready')"
    ).fetchone()["n"]


def _expiring_counts(conn) -> dict:
    """D7's two windows, as two numbers plus their total.

    One definition for the menu count, Today's Expiring list and `/expiry`'s
    two working tables: `_LAPSING_CTE` under `_LAPSING_GROUP_BY`, the literal
    text the board runs, over the board's own defaults. `soon` is
    `EXPIRING_WINDOW_DAYS` ahead, `recent` is `EXPIRED_LOOKBACK_DAYS` back,
    and `total` is what the old header strip counted (`len(recent) + len(soon)`
    on the default board, by construction). Long-expired is deliberately
    outside both: that backlog is standing exposure, not this week's work.
    """
    row = conn.execute(
        _LAPSING_CTE + """
        SELECT count(*) FILTER (WHERE days_left BETWEEN 0 AND %s
                                  AND NOT staleness)               AS soon,
               count(*) FILTER (WHERE days_left BETWEEN %s AND -1
                                  AND NOT staleness)               AS recent,
               count(*) FILTER (WHERE days_left < 0
                                  AND staleness)                   AS review
        FROM (
          SELECT (l.expires - current_date)::int AS days_left,
                 bool_or(l.basis = 'staleness')  AS staleness
          FROM lapsing l
          GROUP BY """ + _LAPSING_GROUP_BY + """
        ) t
        """,
        (EXPIRING_WINDOW_DAYS, -EXPIRED_LOOKBACK_DAYS),
    ).fetchone()
    return {"soon": row["soon"], "recent": row["recent"],
            "review": row["review"],
            "total": row["soon"] + row["recent"]}


#: `report-2026-W37.html`, the one shape `app/handlers/report.py` writes.
_REPORT_WEEK_RE = re.compile(r"^report-(\d{4})-W(\d{2})\.html$")


def report_week_label(name: str) -> str:
    """`report-2026-W37.html` -> "Week 37 (7-13 Sep)".

    A week is a week to the person reading it, not a file name. The span itself
    is `words.week_label`, the one definition of what a week is called on any
    screen (spec § 9, P7a); this function only turns a REPORT FILE NAME into a
    date to hand it. A name in any other shape is passed through with its
    extension dropped rather than guessed at.
    """
    m = _REPORT_WEEK_RE.match(name)
    if not m:
        return name.removesuffix(".html")
    year, week = int(m.group(1)), int(m.group(2))
    try:
        start = date.fromisocalendar(year, week, 1)
    except ValueError:                      # week 53 of a 52-week year
        return name.removesuffix(".html")
    return words.week_label(start)


#: `2026-W34`, the weekly cadence bucket `email_request.period_key` writes. The
#: other shape it can write is `2026-P1234` (a non-weekly cadence), which is not
#: a week and must not be dressed as one.
_PERIOD_WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")


def period_week_label(key: str | None) -> str:
    """`2026-W34` -> "Week 34 (17-23 Aug)". Anything else passes through.

    A renewal draft's period is a week, and it was printed as the bucket key on
    both the board and the draft -- a string only the cadence guard reads. Same
    span, same wording as every other week on any screen (`words.week_label`):
    a person comparing a draft to the weekly report must not have to translate
    between two spellings of one week.
    """
    if not key:
        return "—"
    m = _PERIOD_WEEK_RE.match(key)
    if not m:
        return key
    try:
        return words.week_label(date.fromisocalendar(int(m.group(1)),
                                                     int(m.group(2)), 1))
    except ValueError:                      # week 53 of a 52-week year
        return key


def _kpi_match_basis(conn) -> list[dict]:
    """K2: production-link match_basis distribution. Never shows name-family/
    fetch-context/ref-catalogue — the item_document_trusted_basis_ck CHECK
    forbids those at production (invariant 3 working correctly, not a gap)."""
    return conn.execute(
        "SELECT match_basis, count(*) AS n FROM item_document_production "
        "GROUP BY match_basis ORDER BY n DESC"
    ).fetchall()


def _kpi_missing_mfr_ref(conn) -> dict:
    """K3: the missing_mfr_ref rate. `docs/specs/kpi.md` defines it per
    catalogue; there is one, so the grouping is gone (2026-08-26) and this is
    the whole-mirror rate."""
    row = conn.execute(
        "SELECT count(*) FILTER (WHERE mfr_ref IS NULL) AS missing, "
        "count(*) AS total FROM item_mirror"
    ).fetchone()
    row["rate_pct"] = (round(row["missing"] / row["total"] * 100, 1)
                       if row["total"] else None)
    return row


def _kpi_staging_queue(conn) -> dict:
    """K4: staging queue size + oldest age, as three sub-counts (different
    tables, different review actions). item_document has no created_at column,
    so the staged-links sub-count never carries an age — not fabricated."""
    docs = conn.execute(
        "SELECT count(*) AS n, min(created_at) AS oldest FROM document WHERE status='staged'"
    ).fetchone()
    links = conn.execute(
        "SELECT count(*) AS n FROM item_document lnk JOIN document d USING (doc_id) "
        "WHERE lnk.status='staged' AND d.status='production'"
    ).fetchone()
    suggestions = conn.execute(
        "SELECT count(*) AS n, min(created_at) AS oldest FROM grouping_suggestion WHERE status='open'"
    ).fetchone()
    return {"staged_docs": docs, "staged_links_on_production": links, "open_suggestions": suggestions}


def _kpi_manual_by_kind(conn) -> list[dict]:
    """K5: open manual_task count by kind."""
    return conn.execute(
        "SELECT kind, count(*) AS n FROM manual_task WHERE status='open' GROUP BY kind ORDER BY kind"
    ).fetchall()


def _kpi_dead_job_count(conn) -> int:
    """K6: dead-job count."""
    return conn.execute("SELECT count(*) AS n FROM job WHERE status='dead'").fetchone()["n"]


def _kpi_sweep_due_count(conn) -> int:
    """EUDAMED manufacturers due for a sweep, not yet released (migration 044).

    Reads the same `eudamed_sweep_due` view as `registry.sweep_due_rows` --
    this is only the count, for the headline tile; the full list lives at
    `/manufacturers/sweep-due`."""
    return conn.execute("SELECT count(*) AS n FROM eudamed_sweep_due").fetchone()["n"]


def _kpi_jobs_by_type_status(conn) -> list[dict]:
    """K7: job counts by (type, status)."""
    return conn.execute(
        "SELECT type, status, count(*) AS n FROM job GROUP BY type, status ORDER BY type, status"
    ).fetchall()


def _kpi_spend(conn, budget) -> dict:
    """K8: sweep spend vs budget cap. USD totals from SQL; EUR conversion in
    Python at budget.eur_per_usd (never baked into the query), rendered next to
    the figure so a stale rate is visible. `extra_where` is a fixed literal
    (never request input) — safe to interpolate."""

    def _window(extra_where: str = "") -> dict:
        row = conn.execute(
            f"SELECT sum(cost_usd) AS usd, count(*) FILTER (WHERE cost_usd IS NULL) AS unpriced_calls "
            f"FROM extraction_cost {extra_where}"
        ).fetchone()
        usd = float(row["usd"] or 0)
        return {"usd": usd, "eur": round(usd * budget.eur_per_usd, 2),
                "unpriced_calls": row["unpriced_calls"]}

    return {
        "all_time": _window(),
        "sweep_cap_eur": budget.sweep_cap_eur,
        "trailing_30d": _window("WHERE at >= now() - interval '30 days'"),
        "monthly_cap_eur": budget.monthly_cap_eur,
        "eur_per_usd": budget.eur_per_usd,
        "breakdown": conn.execute("SELECT * FROM extraction_spend").fetchall(),
    }


def _kpi_board(conn, budget) -> dict:
    """All KPI board numbers in one dict — the single implementation shared by
    the HTML board (GET /) and the JSON read API (GET /api/kpi)."""
    return {
        "coverage": _kpi_coverage(conn),
        "match_basis": _kpi_match_basis(conn),
        "missing_mfr_ref": _kpi_missing_mfr_ref(conn),
        "staging": _kpi_staging_queue(conn),
        "manual_by_kind": _kpi_manual_by_kind(conn),
        "dead_jobs": _kpi_dead_job_count(conn),
        "sweep_due": _kpi_sweep_due_count(conn),
        "jobs_by_type_status": _kpi_jobs_by_type_status(conn),
        "spend": _kpi_spend(conn, budget),
    }


def _in_flight_docs(conn) -> int:
    """Documents being read: DISTINCT `content_hash` over pending and running
    jobs. One PDF can hold several jobs at once, and a job with no
    `content_hash` (a group-level job, a `scheduler.tick`) is not a document.

    The one definition behind the header strip, `/inflight` and the status
    board's "Documents being read" tile. The tile used to count every pending
    or running job instead -- on dev on 2026-09-11 that was 11, ten of them the
    perpetual `scheduler.tick` jobs, beside a strip reading 0."""
    return conn.execute(
        "SELECT count(DISTINCT payload->>'content_hash') AS n FROM job "
        "WHERE status IN ('pending','running') AND payload ? 'content_hash'"
    ).fetchone()["n"]


def _pulse_counts(conn) -> dict:
    """The queue counters the operator pages read: what is moving, what waits
    for a person, what gave up.

    These were the sticky header strip's five numbers until the office menu
    replaced the strip (spec § 7). `/pipeline` still shows three of them beside
    its own two, and the office menu's own set is `_menu_counts`, which is a
    different question ("what is MY work") and a different cost.

    Deliberately a different set from `_kpi_board`, not a subset call of it:
    these are cheap enough to run beside a page render. Measured on the dev
    database at ~16k jobs: four of them total ~13ms, while `_kpi_board` is
    ~478ms of which `_kpi_coverage` alone is ~294ms.

    `in_flight_docs` is `_in_flight_docs`, the one definition `/inflight` and
    the status board's tile also read, so the hub and the pages it links to
    cannot disagree. `expiring` is `_expiring_counts`, which is `/expiry`'s own
    two windows; it costs 39ms of the ~52ms this function spends (measured
    2026-09-03), paid deliberately -- a compliance count that is cheap and
    wrong is not a saving.
    """
    return {
        "in_flight_docs": _in_flight_docs(conn),
        "to_review": conn.execute(
            "SELECT count(*) AS n FROM document WHERE status='staged'"
        ).fetchone()["n"],
        "manual_open": conn.execute(
            "SELECT count(*) AS n FROM manual_task WHERE status='open'"
        ).fetchone()["n"],
        "dead_jobs": conn.execute(
            "SELECT count(*) AS n FROM job WHERE status='dead'"
        ).fetchone()["n"],
        "expiring": _expiring_counts(conn)["total"],
    }


def _menu_counts(conn) -> dict:
    """The four numbers the office menu carries, plus the one the health line
    needs (spec § 7).

    One query set per menu load, the cadence the header strip had: lazily
    fetched so no page waits on it, then polled so a long-lived tab stays
    honest. Each count is the count of the page the entry opens, read through
    the same helper that page reads it through, and each is ON that page --
    a number in the menu that a person cannot find after clicking it is a
    number they have to take on trust:

      * `to_review`  -- staged documents, the total `/staging`'s pager states;
      * `missing`    -- `missing_summary`, the open dead ends `/missing` counts
        above its chips;
      * `expiring`   -- `_expiring_counts`, the two windows `/expiry` heads its
        two working tables with;
      * `drafts`     -- `_drafts_waiting`, the renewal mails still to send,
        which `/drafts` leads with (`draft` + `ready`: a `cancelled` draft was
        deliberately dropped and is not work);
      * `timeouts`   -- `failures.timeout_count`, the website timeouts that
        still need someone, for the health line. Deliberately NOT every dead
        job: the strip counted all 94 on dev while 39 of them still needed
        anyone (spec § 4, "retried work stops counting").
    """
    return {
        "to_review": conn.execute(
            "SELECT count(*) AS n FROM document WHERE status='staged'"
        ).fetchone()["n"],
        "missing": missing_summary(conn)["n"],
        "expiring": _expiring_counts(conn)["total"],
        "drafts": _drafts_waiting(conn),
        "timeouts": failures.timeout_count(conn),
    }


def _health_line(services: list[dict], timeouts: int) -> dict:
    """Machine health as ONE line for the office (spec § 1, rule 8).

    `ok` is every beating service fresh. `off` -- a service that has never
    beaten at all -- is not a failure here for the same reason it is not red on
    the operator chips: a process that is legitimately absent on this machine
    would otherwise put a permanent alarm on every page, and a line that is
    always red is a line people stop reading. Only a service that beat and then
    stopped is a failure.

    The timeout count rides on the same line because it is the one failure an
    office login can act on (D6), and `details` is the only link out of it.
    """
    stale = [s["name"] for s in services if s["state"] == "down"]
    return {
        "ok": not stale,
        "text": "System working" if not stale else
                f"{', '.join(stale)} not responding",
        "timeouts": timeouts,
    }


#: Every service, and how its state is known. `beats` services write to
#: `service_heartbeat`; the rest are true by construction, because this response
#: was rendered by web, served through caddy and read out of Postgres. A page
#: that claimed its own server was unreachable would be lying, so those three
#: can never show `down`.
#:
#: There is no `scheduler` chip: the crons run as `scheduler.tick` jobs inside
#: `worker` (deleted 2026-09-02), so the worker's own chip already covers them
#: and a second chip would report a process that no longer exists. Whether each
#: cron is actually firing is a ledger question, answered at /scheduler.
_SERVICES = [
    ("worker", {"beats": True, "stale_after_s": 60}),
    ("web", {"beats": False}),
    ("caddy", {"beats": False}),
    ("db", {"beats": False}),
]


def _pulse_services(conn) -> list[dict]:
    """One chip per service for the header strip.

    Three states, and the third is the one that keeps the strip readable.
    `up` and `down` are the obvious pair; `off` is a service that has never
    beaten at all — the scheduler sits behind the `full` compose profile and is
    legitimately absent on most machines. Painting that red would put a
    permanent alert on every page, and a strip that is always red is a strip
    people stop reading (the same rule `_pulse.html` already applies to `dead`).

    Cheap by construction: one indexed read of a table that holds one row per
    running process, against the ~13ms budget the four counters share.
    """
    try:
        seen = heartbeat.read(conn)
    except Exception:  # noqa: BLE001 - the strip degrades, the page does not fail
        log.warning("heartbeat read failed; service chips omitted", exc_info=True)
        return []
    out = []
    for name, spec in _SERVICES:
        if not spec["beats"]:
            out.append({"name": name, "state": "up", "title": f"{name}: serving this page"})
            continue
        row = seen.get(name)
        if row is None:
            out.append({"name": name, "state": "off", "title": f"{name}: not running"})
            continue
        up = row["age_s"] <= spec["stale_after_s"]
        out.append({
            "name": name,
            "state": "up" if up else "down",
            "title": (f"{name}: last beat {int(row['age_s'])}s ago"
                      f" ({row['instances']} instance"
                      f"{'s' if row['instances'] != 1 else ''})"),
        })
    return out


#: What an error page says, by status code (spec § 9, P7a). A heading a person
#: can read and one sentence saying what to do about it. Anything not listed
#: falls back by band in `_error_words`, so a status nobody planned for still
#: gets words rather than a number.
ERROR_WORDS: dict[int, tuple[str, str]] = {
    404: ("Page not found",
          "That address is not a screen in this system. It may have been "
          "renamed, or the link may be out of date."),
    403: ("You cannot open this",
          "Your login does not reach this screen. Ask whoever set up your "
          "login whether it should."),
    401: ("You are not signed in",
          "This screen needs a login. Open the address your IT contact gave "
          "you and sign in there."),
    405: ("That is not something this screen does",
          "The address is real, but not for what was just asked of it. "
          "Nothing was changed."),
}


#: What a 500 tells the CALLER. Never `str(exc)`: an unhandled exception's text
#: can carry a query, a path or a connection string, and a browser is the one
#: place that must not see it. The real exception is logged with its traceback.
INTERNAL_ERROR_DETAIL = "internal server error"


def _error_words(status: int) -> tuple[str, str]:
    """The heading and the sentence for one status code."""
    if status in ERROR_WORDS:
        return ERROR_WORDS[status]
    if status < 500:
        return ("This did not go through",
                "The system refused it. Nothing was changed. Go back and try "
                "again.")
    return ("Something went wrong",
            "The system could not finish this. Nothing you did caused it. Try "
            "again, and tell a developer if it keeps happening.")


def _wants_html(request: Request) -> bool:
    """Whether this caller reads pages.

    `text/html` FIRST, and only then `*/*`. A browser sends
    `text/html,application/xhtml+xml,…,*/*;q=0.8`, and a JSON client very often
    sends `application/json, text/plain, */*` (axios's default, httpx's
    `Accept: */*`, jQuery's ajax) — testing `*/*` first handed that second
    client a page, which is the one thing an explicit `application/json` says
    it does not want.

    So: html if it is named, JSON if something else is named and html is not,
    and html when the caller expressed no preference at all (an absent header,
    or a bare `*/*`). Every browser sends one, so the absence is a script with
    no opinion, and a readable page costs it nothing.
    """
    accept = request.headers.get("accept", "").lower()
    if not accept:
        return True
    if "text/html" in accept:
        return True
    named = [part.split(";", 1)[0].strip() for part in accept.split(",")]
    return all(t in ("", "*/*") for t in named)


def create_app(web_cfg: Web | None = None) -> FastAPI:
    """Build the FastAPI app against `web_cfg` (defaults to `load_config().web`).

    Taking just the `Web` config section (not the whole `Config`) keeps this
    module decoupled from sections it has no business reading (gate
    thresholds, models, ...) and makes it trivial to point tests at the
    `dentalia_api` role on the test database.
    """
    cfg = web_cfg or load_config().web
    # Budget is not part of Web (it's shared with the worker's sweep-cap
    # enforcement) — read once at app creation, same cadence as `cfg` itself.
    budget_cfg = load_config().budget
    # Same cadence, same reason: the SCHEDULER panel names the cron cadences and
    # emission flags the scheduler process runs on. Compose fills both
    # containers' SCHEDULER_* from one .env, so the two cannot disagree.
    scheduler_cfg = load_config().scheduler
    # Global dependency, not middleware: middleware would force `async def`
    # and this codebase is sync by ruling. Runs for every matched route;
    # default-deny, so a route added later is staff-only until opened.
    # `docs_url`/`openapi_url`/`redoc_url` are None and re-registered below.
    # FastAPI adds its own docs routes with `add_route()` straight onto the
    # Starlette app, NOT through the APIRouter -- so app-level `dependencies`
    # never ran for them. Measured 2026-08-25: `/docs` and `/openapi.json`
    # answered 200 to no credential at all, while `allowed_classes()` claimed
    # staff-only. Caddy compounded it: it skips Basic for anything carrying an
    # `X-API-Key` header, valid or not, so the app was the only thing between a
    # bogus key and the schema listing, and it was not looking.
    app = FastAPI(
        title="Dentalia Compliance Registry",
        docs_url=None,
        openapi_url=None,
        redoc_url=None,
        dependencies=[Depends(access.guard(cfg))],
    )
    app.state.web_cfg = cfg

    # Registered through the router, so the global dependency DOES apply --
    # that is the entire point of not using FastAPI's built-ins here. Paths
    # unchanged, so `/api-reference`'s links to them stay live for staff.
    @app.get("/openapi.json", include_in_schema=False)
    def openapi_schema():
        return app.openapi()

    @app.get("/docs", include_in_schema=False)
    def swagger_ui():
        return get_swagger_ui_html(
            openapi_url="/openapi.json", title="Dentalia Compliance Registry"
        )

    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")
    templates = Jinja2Templates(directory=str(_HERE / "templates"))

    # Cache-bust every static asset by its own mtime.
    #
    # Starlette's StaticFiles sends ETag and Last-Modified but no Cache-Control,
    # so a browser is free to apply heuristic freshness and serve the old file
    # without revalidating. It does: a rebuilt image served a corrected
    # stylesheet for hours while browsers kept rendering the previous one, which
    # made a shipped CSS fix look like a broken CSS fix (2026-08-17, the `filed`
    # badge). Deploys are image rebuilds, so the mtime is fixed for a
    # container's whole life — stat once at startup, never per render, and a new
    # build yields a new URL that no cache can have.
    _asset_versions: dict[str, str] = {}

    def static_url(rel: str) -> str:
        if rel not in _asset_versions:
            try:
                _asset_versions[rel] = str(int((_HERE / "static" / rel).stat().st_mtime))
            except OSError:
                # A missing asset is a 404 to fix, not a 500 to raise here: the
                # unversioned URL still resolves if the file appears later.
                _asset_versions[rel] = ""
        version = _asset_versions[rel]
        return f"/static/{rel}?v={version}" if version else f"/static/{rel}"

    templates.env.globals["static_url"] = static_url

    def _duration(seconds: int | None) -> str:
        """Seconds as something a person reads at a glance.

        A job that took 4471 seconds is a job nobody can compare to one that
        took 90 without doing arithmetic in their head, and this column exists
        to be scanned. None (still running, or finished before migration 026)
        renders as an em dash rather than "0s", which would be a lie.
        """
        if seconds is None:
            return "—"
        if seconds < 60:
            return f"{seconds}s"
        if seconds < 3600:
            return f"{seconds // 60}m {seconds % 60}s"
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"

    templates.env.filters["duration"] = _duration
    # One vocabulary, four filters (spec § 9, P7a). Each is a pure function in
    # `web/words.py`, registered here so a template never spells a word, a date
    # or a separator for itself:
    #   `| word`        a stored value as the word on screen
    #   `| day`         "3 Sep 2026", not "2026-09-03"
    #   `| week_label`  "Week 37 (7–13 Sep)" for any date in that week
    #   `| num`         "4,265", not "4265"
    # `day` and `num` replace the per-template macros (`_ui.html`,
    # `today.html`) that were the documented stopgap until this slice.
    templates.env.filters["word"] = words.word
    templates.env.filters["day"] = words.day_text
    templates.env.filters["week_label"] = words.week_label
    templates.env.filters["num"] = words.num
    # Four more, added by P7b, each over its OWN namespace rather than folded
    # into `word` -- `filed` is both a document status and an audit event, and
    # `manual` is both a list and a discovery rung, so one flat table could not
    # hold them without one meaning eating the other:
    #   `| doc_type`      "DoC" as "Declaration of Conformity"
    #   `| audit_event`   "production-write" as "Published"
    #   `| rung`          "playbook" as "The manufacturer's known pages"
    #   `| rung_outcome`  "hit" as "Found something"
    templates.env.filters["doc_type"] = words.doc_type_word
    templates.env.filters["audit_event"] = words.audit_event
    templates.env.filters["rung"] = words.rung
    templates.env.filters["rung_outcome"] = words.rung_outcome
    # `| draft_status`: a renewal email's state. A filter rather than a context
    # key because the two screens that CROSS-LINK to /drafts -- /expiry and
    # /documents/{id} -- get their chase row from their own query and had no
    # map in scope, so both printed the stored value beside a link to the page
    # that prints the word.
    templates.env.filters["draft_status"] = words.draft_status
    # Rule 1 as a filter: a whole document row in, its name out. Same function
    # the Review board calls, so the two cannot name one document two ways.
    templates.env.filters["doc_name"] = words.doc_display_name
    # D7: every template that labels an expiring figure reads the two windows
    # from here, including the menu fragment, which has no route context.
    templates.env.globals["EXPIRING_WINDOW_DAYS"] = EXPIRING_WINDOW_DAYS
    templates.env.globals["EXPIRED_LOOKBACK_DAYS"] = EXPIRED_LOOKBACK_DAYS

    def _error_response(request: Request, status: int, detail, headers=None):
        """An error a person can get out of (spec § 9, P7a).

        The default was `{"detail": "unknown item"}` rendered as raw JSON in a
        browser window -- and, for an unhandled exception, Starlette's
        `Internal Server Error` as bare text: no menu, no way back, and a word
        ("detail") that means nothing to an office reader. Three answers now,
        decided in this order:

        * `/static/*`, `/api/*`, and any caller who is not a staff browser,
          keep the JSON body and the response's own headers. The webshop
          backend and the BC item-card link parse this; an HTML page would
          break them, and a menu is not something to hand an anonymous caller.
          `/static/*` is in that list because a missing stylesheet is a fetch
          by the browser, not a navigation: answering it with the whole menu
          page wastes a render and puts HTML where CSS was asked for.
        * An HTMX request gets the `_result.html` error partial, which is what
          every failed fragment on this site already renders. A boosted form
          gets it too, deliberately: `base.html` cancels the swap on a non-2xx
          and lifts the banner text out of `.result.error`, so a whole page
          here would leave the banner reading the page title. That partial
          carries the generic sentence for every status but 403, which carries
          its own `detail` -- see the note at the branch itself.
        * Everything else -- a person who typed, clicked or bookmarked -- gets
          `error.html`, which extends base.html and therefore carries the menu.

        `headers` carries the exception's own (a 405's `Allow`, a 401's
        `WWW-Authenticate`) onto whichever body is chosen, and a status that
        may not carry a body (204, 304) gets none, which is the guard FastAPI's
        own handler applies.

        The status code is never rewritten: a 404 stays a 404 whichever body it
        is wearing.
        """
        if not is_body_allowed_for_status_code(status):
            return Response(status_code=status, headers=headers)
        detail_text = detail if isinstance(detail, str) else None
        if (request.url.path.startswith("/api/")
                or request.url.path.startswith("/static/")
                or access.classify(request, cfg) != access.STAFF
                or not _wants_html(request)):
            return JSONResponse({"detail": detail}, status_code=status,
                                headers=headers)
        heading, explanation = _error_words(status)
        if request.headers.get("HX-Request"):
            # A 403 renders its OWN sentence, every other status the generic
            # one (fix round 1, 2026-09-15). Every button on this site posts
            # over HTMX, so the fragment is how a refusal actually reaches a
            # person -- and "Your login does not reach this screen" is the
            # wrong sentence for a person who is standing on the screen and
            # pressed a button on it. The page has always shown `detail`; this
            # only stops the fragment being less useful than the page.
            #
            # 403 ONLY, deliberately. Every 403 detail in this app is a fixed
            # string we wrote (`web/access.py`'s two, and the missing-header
            # one), never an exception's text. A 500's detail is fixed too, but
            # the rule stays narrow so that a status added later carrying
            # something from an exception cannot ride in on it.
            fragment = (detail_text if status == 403 and detail_text
                        else explanation)
            return templates.TemplateResponse(
                request, "_result.html",
                {"request": request, "error": fragment},
                status_code=status, headers=headers)
        return templates.TemplateResponse(
            request, "error.html",
            {"request": request, "heading": heading, "explanation": explanation,
             "status_code": status, "path": request.url.path,
             "detail": detail_text},
            status_code=status, headers=headers)

    @app.exception_handler(StarletteHTTPException)
    def _http_error(request: Request, exc: StarletteHTTPException):
        return _error_response(request, exc.status_code, exc.detail,
                               getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    def _unhandled_error(request: Request, exc: Exception):
        """A genuine 500 gets the same friendly page (controller ruling,
        2026-09-14). It is the case the page matters MOST for: a 404 is usually
        a stale link, while a 500 is the moment an office person is stuck and
        needs to be told plainly that nothing was changed and who to tell.

        Two things this must never do. It must not leak the exception to a
        browser -- the detail passed on is the fixed string below, never
        `str(exc)`, so a stack trace or a connection string cannot reach a page
        (the real exception is logged here, with its traceback passed in
        explicitly, which is where a developer reads it). And it must not recurse: if RENDERING the page
        fails too -- a template error, a dead database behind the menu's
        `is_operator` -- there is no second handler to catch that, so the
        fallback is a plain-text response, the same thing Starlette would have
        sent.

        Registered on `Exception`, which Starlette's `ServerErrorMiddleware`
        honours; it re-raises after the handler returns, so uvicorn still logs
        the 500 and `TestClient(raise_server_exceptions=True)` still raises.
        """
        # `exc_info=exc`, not a bare `log.exception`: Starlette runs a SYNC
        # `Exception` handler through `run_in_threadpool`, and `sys.exc_info()`
        # is thread-local, so the ambient lookup finds nothing here and writes
        # `NoneType: None` where the stack should be.
        log.exception("unhandled error on %s", request.url.path, exc_info=exc)
        try:
            return _error_response(request, 500, INTERNAL_ERROR_DETAIL)
        except Exception:               # pragma: no cover - the last resort
            log.exception("error page failed to render")
            return PlainTextResponse("Internal Server Error", status_code=500)

    def _conn():
        return db.connect(cfg.api_database_url)

    def _is_operator(request: Request) -> bool:
        """Whether the menu offers this login the operator group (spec § 2, D3).

        A Jinja global because base.html renders the menu for every route, and
        no route should have to put this in its context dict. It decides what
        is OFFERED and nothing else: every operator page stays reachable by
        URL, and the four risky writes grow their own refusals in slice P5b.

        With `web.operator_users` empty -- today, everywhere -- `is_operator`
        is True for every login, so the group is shown to everyone, collapsed.
        A caller with no credible proxy login (a `bc` item-card link, an API
        key) is not an operator and never sees it; `_authenticated_user` raises
        for that case rather than returning None, so it is caught here instead
        of turning a render into a 403.
        """
        try:
            user = _authenticated_user(request)
        except HTTPException:
            return False
        return access.is_operator(user, cfg.operator_users)

    templates.env.globals["is_operator"] = _is_operator

    def _authenticated_user(request: Request) -> str | None:
        """The proxy-authenticated username (G3 v0), or None when auth isn't
        required (local dev / tests — no proxy in front). A required-but-empty
        header means the request bypassed the proxy — reject it outright,
        never fall back to a client-supplied value for `decided_by`."""
        if not cfg.require_authenticated_user:
            return None
        # access.trusted_user, not a raw header read: an unresolved Caddy
        # placeholder is non-empty and would otherwise be recorded as the
        # person who made a gate decision (2026-08-25).
        user = access.trusted_user(request, cfg)
        if not user:
            # The header NAME does not go to the browser. Since P5b a 403's
            # detail renders in the page and in an HTMX fragment, and anyone
            # who reaches this line is talking to `web` directly, past Caddy:
            # in that position the name of the header is the whole of what
            # stands between them and forging an operator identity. It goes to
            # the log, where the person debugging the proxy will look anyway.
            log.warning("request without %s reached %s: it did not come "
                        "through the proxy", cfg.trusted_user_header,
                        request.url.path)
            raise HTTPException(
                status_code=403,
                detail="This request did not come through the sign-in proxy.",
            )
        return user

    # D3's four refused writes (spec § 7, P5b). Built here because it composes
    # `cfg` with `_authenticated_user`, and handed to the modules that own the
    # guarded routes -- the same shape `_authenticated_user` itself is passed
    # in, and for the same reason: one decision, not one per module.
    require_operator = access.operator_guard(cfg, _authenticated_user)

    # Registered AFTER `_authenticated_user` is defined: `registry` takes it as
    # an argument so there is one place that decides who the proxy says you are.
    registry.register_routes(app, templates, _conn, cfg.playbooks_dir or None,
                             authenticated_user=_authenticated_user,
                             default_user=DEFAULT_DECIDED_BY,
                             require_operator=require_operator)
    # After `registry`: the guided flow composes `start_playbook` and
    # `save_playbook_body` from it, and posts its probe to the routes registered
    # there. One implementation of each, so the flow and the expert page can
    # never show an operator different answers for the same recipe.
    onboarding.register_routes(app, templates, _conn, cfg.playbooks_dir or None,
                               authenticated_user=_authenticated_user,
                               default_user=DEFAULT_DECIDED_BY,
                               require_operator=require_operator)
    scheduler_view.register_routes(app, templates, _conn, scheduler_cfg,
                                   require_operator=require_operator)
    item_link.register_routes(app, templates, _conn, cfg)
    bc_push_view.register_routes(app, templates, _conn, cfg,
                                 require_operator=require_operator)
    catalogue.register_routes(app, _conn, cfg)

    @app.get("/healthz")
    def healthz():
        # Liveness only — deliberately DB-independent so the container
        # healthcheck doesn't flap with Postgres.
        return {"status": "ok"}

    @app.get("/archive/{path:path}")
    def archive_file(path: str):
        """Serve an archived document. The archive is hash-addressed and
        read-only here; `web` mounts the volume ro (invariant 1 is about the
        registry, but the same posture applies to bytes we did not fetch)."""
        root = pathlib.Path(cfg.archive_root).resolve()
        try:
            target = (root / path).resolve()
        except ValueError:
            # `%00` decodes to a real NUL, and resolve() reaches os.stat, whose C
            # boundary rejects it -- before the containment check below runs. This
            # is not a traversal bypass (absolute paths, encoded `..` and symlinks
            # are all rejected there, with tests); it is an input class the handler
            # never considered, and a malformed path deserves the same answer a
            # missing one gets rather than a 500 ([final-500s]).
            raise HTTPException(status_code=404, detail="not found") from None
        if not target.is_relative_to(root) or not target.is_file():
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(target)

    @app.get("/documents/{doc_id:int}/file")
    def document_file(doc_id: int):
        """Serve the archived PDF for a document.

        The link has to be *constructed*, never taken from the database:
        `document.archive_url` is a storage handle (a bare host path for
        backfilled corpus documents, a `file://` URI in local dev, an http URL
        only when `storage.base_url` is set). Templates used to drop it
        straight into an href, which produced a link the browser resolved
        against this origin and 404'd on — every "file" link in the UI was
        dead. Everything the reviewer sees now points here instead.

        Where the bytes may live comes from config, so moving the archive is
        an env change (WEB_ARCHIVE_ROOT / WEB_IMPORTS_DIR), not a code change.
        """
        with _conn() as conn:
            row = conn.execute(
                "SELECT archive_url, content_hash FROM document WHERE doc_id=%s", (doc_id,)
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="unknown document")
        archive_url = row["archive_url"] or ""
        if urlsplit(archive_url).scheme in ("http", "https"):
            # already client-reachable (storage.base_url configured) — hand the
            # browser the real location rather than proxying bytes through here
            return RedirectResponse(archive_url)
        target = _resolve_archive_path(
            archive_url, [cfg.archive_root, cfg.imports_dir],
            rewrites=_parse_path_rewrites(cfg.path_rewrites),
            content_hash=row["content_hash"],
        )
        if target is None:
            raise HTTPException(
                status_code=404,
                detail="archived file is not reachable from this server",
            )
        # inline, not attachment: a reviewer clicking "Open full size" wants to
        # LOOK at the PDF, and `attachment` downloads a fresh copy on every
        # click instead.
        #
        # Cached for a year, privately (office UI redesign, spec § 6). The
        # Review panel shows this file in an iframe every time a row is
        # opened, and no request ever got a 304, so every reopening
        # downloaded the whole file again (median 345 KB, largest 16 MB on
        # dev). The bytes behind a doc_id never change -- the archive is
        # hash-addressed and a new file is a new document -- so `immutable`
        # is true, and `private` keeps it out of shared caches, since this
        # route is staff-only. Only the file response carries it: every
        # refusal above is an HTTPException, and a 404 must stay uncached.
        # (Starlette's range answers copy these headers, so a 206 part of the
        # same bytes is cached the same way.)
        return FileResponse(target, media_type="application/pdf",
                            filename=_download_name(target.name, row["content_hash"]),
                            content_disposition_type="inline",
                            headers={"Cache-Control": DOCUMENT_FILE_CACHE})

    @app.get("/documents/{content_hash}/text", response_class=PlainTextResponse)
    def document_text(content_hash: str):
        """What EXTRACT actually read out of this PDF. Plain text so it can be
        searched, diffed and pasted without a viewer."""
        with _conn() as conn:
            row = conn.execute(
                "SELECT content, source FROM document_text WHERE content_hash=%s",
                (content_hash,),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="no stored text")
        if row["source"] == "none":
            return "(no text layer: this document is a scan)"
        return row["content"]

    # ----------------------------------------------------------------------- #
    # Registry visibility (S1.8 Task 6) — browse/search items and documents,
    # staged and production alike. The read API and item_document_production
    # only ever show a document for an item when BOTH the document and the
    # link are production; this UI deliberately shows more, because "why is
    # this not published yet" is the question a reviewer actually has.
    #
    # /documents/{doc_id:int} cannot shadow /documents/{content_hash}/text above
    # regardless of declaration order: the two path shapes have a different
    # segment count (/documents/<int> is two segments, /documents/<hash>/text
    # is three), so this is not an ordering-sensitive registration. The `:int`
    # converter's real job is narrower: it 404s a non-numeric doc_id cleanly
    # instead of letting it 422 through Pydantic path-parameter validation —
    # see test_document_id_route_rejects_non_numeric_id_with_404.
    # ----------------------------------------------------------------------- #
    @app.get("/items", response_class=HTMLResponse)
    def items(request: Request, q: str = "",
              page: int = Query(0, ge=0, le=MAX_PAGE)):
        """Browse the catalogue with its compliance coverage. Three buckets
        that partition every linked document: production (live, consumer
        visible), staged (found but not yet trusted enough to publish), and
        superseded (was production, since replaced — still on file, not
        "no coverage"). A link promoted to production keeps that link status
        forever; supersession only ever flips the document's own status
        (app/handlers/gate.py `_apply_supersession`), so a superseded
        document can still carry a production-status link — bucketed here by
        document status, not link status, so that row is never silently
        dropped from every count. next_expiry inherits through cert_doc_id
        the same way app/handlers/report.py's expiring_documents() does, so
        the board and that scan cannot disagree about when something
        expires.

        next_expiry counts only documents issued under MDR or MDD. This is
        the one place the board and that scan DIVERGE, deliberately, and the
        reason is the difference in what they are counting: the scan lists
        DOCUMENTS, and an ISO 13485 certificate genuinely expires and should
        genuinely be chased. This column is an ITEM's horizon, and a quality
        system certificate carries no claim about any article, so lending its
        date to one says something nobody said. CARL MARTIN arrived in exactly
        that state on 2026-08-21: one QMS certificate running to 2028-06-06,
        bound to 2.567 items by mfr-scope, every one of them reporting that
        date while both of its MDR declarations sat in staging. An item with
        no device document now shows no horizon rather than a borrowed one --
        a dash is the honest answer, and the coverage columns beside it
        already say the article has paper."""
        like = f"%{_like_escape(q)}%" if q else ""
        with _conn() as conn:
            # Same WHERE as the row query, without the document joins: those
            # multiply a row per linked document, and this must count items.
            total = conn.execute(
                "SELECT count(*) AS n FROM item_mirror m "
                "LEFT JOIN manufacturer_alias a ON a.raw_name = m.manufacturer_raw "
                "WHERE (%s = '' OR m.item_ref ILIKE %s OR m.name ILIKE %s "
                "       OR m.manufacturer_raw ILIKE %s OR a.canonical_name ILIKE %s)",
                (q, like, like, like, like),
            ).fetchone()["n"]
            rows = conn.execute(
                "SELECT m.item_ref, m.name, m.manufacturer_raw, a.canonical_name AS manufacturer_name, m.md_flag, "
                "  count(*) FILTER (WHERE d.status='production' AND id.status='production') AS production_docs, "
                "  count(*) FILTER (WHERE id.status='staged' AND d.status IN ('staged','production')) AS staged_docs, "
                "  count(*) FILTER (WHERE d.status='superseded' AND id.status <> 'retracted') AS superseded_docs, "
                "  min(e.expires) FILTER (WHERE d.status='production' AND id.status='production' "
                "                          AND d.regulation IN ('MDR','MDD')) AS next_expiry "
                "FROM item_mirror m "
                "LEFT JOIN manufacturer_alias a ON a.raw_name = m.manufacturer_raw "
                "LEFT JOIN item_document id ON id.item_ref = m.item_ref "
                "LEFT JOIN document d ON d.doc_id = id.doc_id "
                "LEFT JOIN document_effective_expiry e ON e.doc_id = d.doc_id "
                "WHERE (%s = '' OR m.item_ref ILIKE %s OR m.name ILIKE %s "
                "       OR m.manufacturer_raw ILIKE %s OR a.canonical_name ILIKE %s) "
                "GROUP BY m.item_ref, m.name, m.manufacturer_raw, a.canonical_name, m.md_flag "
                "ORDER BY m.item_ref LIMIT %s OFFSET %s",
                (q, like, like, like, like, ITEMS_PAGE_SIZE, page * ITEMS_PAGE_SIZE),
            ).fetchall()
        return templates.TemplateResponse(
            request, "items.html",
            {"rows": rows, "q": q, "page": page,
             "pager": _pager(page, ITEMS_PAGE_SIZE, total)},
        )

    # `:path`, not the default `[^/]+` converter: 1996 of 15958 live item_refs
    # contain a slash, and every one of them 404s at the router without it.
    # Percent-encoding the link does not help -- uvicorn decodes `%2F` back to
    # a literal slash before routing -- so the five templates that link here
    # are all fixed by this one converter, exactly as
    # `/manufacturers/{canonical_name:path}` already is. Nothing to shadow:
    # `/items` is the only sibling route and its literal prefix wins.
    @app.post("/items/{item_ref:path}/rediscover")
    def item_rediscover(item_ref: str):
        """Admin refetch ([admin-refetch-item]): re-run discovery for this
        item's group with the recency window bypassed (`ignore_recency:
        true` — DISCOVER/FETCH still run the conditional GET and hash
        dedupe, so a forced re-fetch of unchanged bytes links, never
        duplicates). Producer-only, same posture as every other POST here:
        resolves the group and enqueues `discover.group`, nothing else.

        Post-redirect-get: the outcome travels back as a query param (no
        flash-message store exists in this app) and item_detail renders the
        notice from it, reusing `_result.html`'s ok/error styling."""
        with _conn() as conn:
            row = conn.execute(
                "SELECT group_id FROM item_group_member WHERE item_ref=%s LIMIT 1",
                (item_ref,),
            ).fetchone()
            if row is None:
                return RedirectResponse(
                    f"/items/{item_ref}?rediscover=no-group", status_code=303
                )
            group_id = row["group_id"]
            # Postgres current_date, not Python's — avoids worker-clock skew,
            # same rationale as discover.py's _known_url_rows.
            today = conn.execute("SELECT current_date AS d").fetchone()["d"]
            dedupe_key = f"discover:refetch:{group_id}:{today.isoformat()}"
            jid = queue.enqueue(
                conn, "discover.group", {"group_id": group_id, "ignore_recency": True},
                dedupe_key, priority="interactive",
            )
            conn.commit()
        outcome = "queued" if jid is not None else "deduped"
        return RedirectResponse(f"/items/{item_ref}?rediscover={outcome}", status_code=303)

    @app.post("/documents/{doc_id:int}/eudamed")
    def document_eudamed(doc_id: int):
        """Look this document's Basic UDI-DI up in EUDAMED (S2.3 stage 1).

        Producer only, same posture as `item_rediscover`: enqueues
        `eudamed.sync` and nothing else. The handler writes `eudamed_mirror`
        and never a link -- `ref-eudamed` is capped at staged (Denis,
        2026-08-25) and no code creates one yet.

        Refused rather than enqueued when the document carries no Basic UDI-DI
        (301 of 769 do not): the job could only no-op, and a queue row that
        cannot do work is noise on the boards.

        Dedupe is per Basic UDI-DI per day, not per document: several documents
        can share one, and EUDAMED's answer is the same for all of them.
        """
        with _conn() as conn:
            row = conn.execute(
                "SELECT basic_udi_di FROM document WHERE doc_id=%s", (doc_id,)
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="unknown document")
            budi = (row["basic_udi_di"] or "").strip()
            if not budi:
                return RedirectResponse(
                    f"/documents/{doc_id}?eudamed=no-udi", status_code=303
                )
            # Postgres current_date, not Python's -- same worker-clock-skew
            # reasoning as item_rediscover.
            today = conn.execute("SELECT current_date AS d").fetchone()["d"]
            jid = queue.enqueue(
                conn, "eudamed.sync", {"basic_udi_di": budi},
                f"eudamed:{budi}:{today.isoformat()}", priority="interactive",
            )
            conn.commit()
        outcome = "queued" if jid is not None else "deduped"
        return RedirectResponse(
            f"/documents/{doc_id}?eudamed={outcome}", status_code=303
        )

    @app.get("/items/{item_ref:path}", response_class=HTMLResponse)
    def item_detail(request: Request, item_ref: str, rediscover: str = ""):
        """One item and every document linked to it, staged and production
        alike. A consumer sees a document only when BOTH the document and the
        link are production; this page shows the rest too, because 'why is
        this not published' is the question a reviewer actually has."""
        with _conn() as conn:
            item = conn.execute(
                "SELECT m.item_ref, m.name, m.manufacturer_raw, "
                "a.canonical_name AS manufacturer_name, m.mfr_ref, m.md_flag, "
                "m.product_class, m.catalogue FROM item_mirror m "
                "LEFT JOIN manufacturer_alias a ON a.raw_name = m.manufacturer_raw "
                "WHERE m.item_ref=%s",
                (item_ref,),
            ).fetchone()
            if item is None:
                raise HTTPException(status_code=404, detail="unknown item")
            # Expiry comes from `document_effective_expiry` (migration 027), the
            # one definition of when a document lapses -- not from a hand-rolled
            # COALESCE here. Two defects closed by the same join:
            #   * the raw `LEFT JOIN document c ON c.doc_id = d.cert_doc_id` this
            #     replaces carried no status filter, so a certificate a human
            #     REJECTED still lent this page its date ([final-cert-status-join]).
            #     The view restricts the citation to production/superseded exactly
            #     as validate._resolve_cited_certificate does on the way in.
            #   * it also knew nothing of the five-year review horizon, so a DoC
            #     with no date of its own read "-" here while /items -- already on
            #     the view -- showed a real date for the same document
            #     ([final-documents-page-parity]).
            # `basis` travels with `expires` so the template can say WHICH of the
            # three rules fired; a review horizon must never be rendered as though
            # the document claimed an expiry it does not have.
            docs = conn.execute(
                "SELECT d.doc_id, d.type, d.regulation, d.validity_from, d.validity_to, "
                "d.status AS doc_status, d.archive_url, d.content_hash, d.cert_number, "
                "d.cert_doc_id, e.expires, e.basis AS expiry_basis, "
                "id.status AS link_status, id.match_basis "
                "FROM item_document id JOIN document d ON d.doc_id = id.doc_id "
                "LEFT JOIN document_effective_expiry e ON e.doc_id = d.doc_id "
                "WHERE id.item_ref=%s AND id.status <> 'retracted' "
                "ORDER BY d.type, d.validity_from DESC NULLS LAST",
                (item_ref,),
            ).fetchall()
            # None for anything BC has not marked a medical device: the card
            # answers an MDR question, and a green card over a non-device
            # teaches the reader that green means nothing.
            completeness = registry.item_completeness(conn, item_ref)
            # "No documents linked to this item yet" was the same sentence
            # whether DISCOVER searched and found nothing or never looked, for
            # 3.987 of the 4.265 medical-device items (measured 2026-09-04).
            # The registry has always been able to tell them apart and no
            # screen read it. Shared with `/discovery` through `app.coverage`
            # so the two cannot drift.
            discovery = discovery_state(conn, item_ref)
        return templates.TemplateResponse(
            request, "item_detail.html",
            {"item": item, "docs": docs, "rediscover": rediscover,
             "bc_push": request.query_params.get("bc_push"),
             # Read per request, exactly as `web/bc_push_view.py` reads it, so
             # the two BC receipts can never disagree about whether sending is
             # on. The flash promised a send while writes were off until now.
             "bc_write_enabled": load_config().bc.write_enabled,
             "completeness": completeness, "discovery": discovery},
        )

    @app.get("/documents", response_class=HTMLResponse)
    def documents(
        request: Request, type: str = "", status: str = "", q: str = "",
        page: int = Query(0, ge=0, le=MAX_PAGE),
    ):
        """Every document — staged, production, filed, rejected, superseded
        alike — with how many items it covers.

        Paginated with a real total rather than the old bare `LIMIT 200`: the
        registry passed that cap while nothing on the page said so, and a
        registry page that silently stops is worse than a slow one — it answers
        "how many documents do we hold?" with a number it made up. The count
        repeats the row query's WHERE and nothing else; it must not join
        `item_document`, or a document covering 94 items would be counted 94
        times and the pager would promise pages that do not exist.
        """
        like = f"%{_like_escape(q)}%" if q else ""
        where = (
            "WHERE (%s = '' OR d.type::text = %s) AND (%s = '' OR d.status::text = %s) "
            "AND " + _DOC_SEARCH + " "
        )
        filters = (type, type, status, status) + _doc_search_params(q)
        with _conn() as conn:
            total = conn.execute(
                "SELECT count(*) AS n FROM document d " + where, filters
            ).fetchone()["n"]
            rows = conn.execute(
                "SELECT d.doc_id, d.type, d.regulation, d.validity_from, d.validity_to, "
                "d.status, d.cert_number, "
                # Split the way /items has always split, and for the same reason:
                # a rejected link is a human saying this document does NOT cover
                # that item, and counting it as coverage says the opposite. The
                # single count(id.item_ref) this replaces merged production,
                # staged and rejected into one number, so 13 of the 147 documents
                # carrying links (measured 2026-08-19) advertised coverage that
                # nobody had accepted. Production leads because it is the only
                # figure a consumer -- BC, /api/* -- ever sees.
                "count(*) FILTER (WHERE id.status='production') AS items_production, "
                "count(*) FILTER (WHERE id.status='staged')     AS items_staged, "
                "count(*) FILTER (WHERE id.status='rejected')   AS items_rejected, "
                # Whose document this is. Read off evidence, exactly as the
                # detail page does — the catalogue cannot answer it for a
                # manufacturer-scope document, which links no item at all.
                # Without this column the list answers every question about a
                # document except the first one a person asks, and 326 rows
                # have to be opened one at a time to find a manufacturer's own.
                # The subquery is correlated on the primary key, so GROUP BY
                # d.doc_id covers it.
                "(SELECT e.value FROM evidence e "
                "  WHERE e.doc_id = d.doc_id AND e.field='manufacturer' LIMIT 1) AS manufacturer, "
                # Expiry through document_effective_expiry (migration 027), not
                # the raw column this list used to print. A declaration carries
                # no expiry of its own by regulation, so the raw column read "-"
                # here while /items -- already on the view -- showed the real
                # date for the same document ([final-documents-page-parity]).
                # The view also holds the production/superseded bar on the cited
                # certificate, so a REJECTED one stops lending its date out here
                # too ([final-cert-status-join]). `x.expires` is grouped rather
                # than aggregated: the view is one row per doc_id, but Postgres
                # extends functional dependency only to the grouped table's own
                # columns, never through a join.
                "x.expires, x.basis AS expiry_basis "
                "FROM document d LEFT JOIN item_document id "
                "  ON id.doc_id = d.doc_id AND id.status <> 'retracted' "
                "LEFT JOIN document_effective_expiry x ON x.doc_id = d.doc_id "
                + where +
                "GROUP BY d.doc_id, x.expires, x.basis ORDER BY d.doc_id DESC LIMIT %s OFFSET %s",
                (*filters, DOCUMENTS_PAGE_SIZE, page * DOCUMENTS_PAGE_SIZE),
            ).fetchall()
            # One alias-table read for the page, not one per row.
            pages = _canonical_pages_for(conn, [r["manufacturer"] for r in rows])
        for r in rows:
            r["manufacturer_page"] = pages.get(
                manufacturers.normalize(r["manufacturer"])
            )
        return templates.TemplateResponse(
            request, "documents.html",
            {"rows": rows, "type": type, "status": status, "q": q,
             "pager": _pager(page, DOCUMENTS_PAGE_SIZE, total)},
        )

    @app.get("/documents/{doc_id:int}", response_class=HTMLResponse)
    def document_detail(request: Request, doc_id: int, eudamed: str = ""):
        """One document: the items it covers, its evidence, and both
        directions of its supersession chain."""
        with _conn() as conn:
            doc = conn.execute(
                "SELECT d.doc_id, d.type, d.regulation, d.validity_from, d.validity_to, "
                "d.status, d.cert_number, d.content_hash, d.archive_url, "
                "d.coverage_scope, d.supersedes, d.superseded_by, d.cert_doc_id, "
                # Provenance. Both columns have existed since migration 005 and
                # neither was ever rendered: the page could say what a document
                # claims but not where the file came from or when we took it,
                # which is the first thing an auditor asks and the one thing a
                # hash cannot answer. The manufacturer joins the same way it
                # does everywhere else (evidence, not the catalogue, because a
                # manufacturer-scope document has no item to derive it from).
                "d.source_url, d.created_at, d.basic_udi_di, "
                "(SELECT e.value FROM evidence e "
                "  WHERE e.doc_id = d.doc_id AND e.field='manufacturer' LIMIT 1) AS manufacturer, "
                # the cited certificate's own identity: "#1204" answers nothing,
                # and its validity_to is the DoC's real renewal date
                "c.type AS cert_type, c.cert_number AS cert_doc_number, "
                "c.validity_to AS cert_doc_validity_to, c.status AS cert_status, "
                # This document's own expiry comes from document_effective_expiry
                # (migration 027), never from the raw column: the raw column is
                # null on most declarations, which is how this page could read "-"
                # while /items -- already on the view -- showed a real date for the
                # same document ([final-documents-page-parity]). The `c` join stays
                # for the citation's IDENTITY only; `cert_status` travels with it
                # so the template can withhold a REJECTED certificate's date while
                # still naming what was cited ([final-cert-status-join]).
                "e.expires, e.basis AS expiry_basis "
                "FROM document d LEFT JOIN document c ON c.doc_id = d.cert_doc_id "
                "LEFT JOIN document_effective_expiry e ON e.doc_id = d.doc_id "
                "WHERE d.doc_id=%s",
                (doc_id,),
            ).fetchone()
            if doc is None:
                raise HTTPException(status_code=404, detail="unknown document")
            # The manufacturer link is offered only when a `manufacturer_alias`
            # row actually resolves the extracted spelling to a canonical the
            # /manufacturers page owns. `resolve_canonicals` returns [] on a
            # miss and >1 on an ambiguous fold, and neither is a page: linking
            # either would hand the reader a 404 or an arbitrary pick.
            doc["manufacturer_page"] = None
            if doc["manufacturer"]:
                canonicals = manufacturers.resolve_canonicals(conn, doc["manufacturer"])
                if len(canonicals) == 1:
                    doc["manufacturer_page"] = canonicals[0]
            doc["cert_type_label"] = (
                _doc_type_label(doc["cert_type"]) if doc["cert_doc_id"] else None
            )
            items_covered = conn.execute(
                "SELECT id.item_ref, id.match_basis, id.status, m.name "
                "FROM item_document id LEFT JOIN item_mirror m ON m.item_ref = id.item_ref "
                "WHERE id.doc_id=%s AND id.status <> 'retracted' ORDER BY id.item_ref",
                (doc_id,),
            ).fetchall()
            evidence = conn.execute(
                "SELECT field, value, page, verbatim, tier, model_id, confidence, "
                "extract_rev FROM evidence WHERE doc_id=%s ORDER BY extract_rev DESC, field",
                (doc_id,),
            ).fetchall()
            chain = conn.execute(
                "SELECT doc_id, type, regulation, validity_from, status FROM document "
                "WHERE doc_id IN (%s, %s) OR superseded_by = %s ORDER BY validity_from",
                (doc["supersedes"], doc["superseded_by"], doc_id),
            ).fetchall()
            # The other half of the /expiry cross-link: if a renewal mail is
            # already chasing this document, say so here. Otherwise a reviewer
            # looking at a lapsed document has no way to tell whether anyone has
            # asked the manufacturer for a replacement.
            chases = conn.execute(
                "SELECT d.id AS draft_id, d.status, d.kind, r.manufacturer, r.period_key "
                "FROM renewal_request_document rd "
                "JOIN renewal_request r ON r.id = rd.renewal_request_id "
                "JOIN email_draft d ON d.renewal_request_id = r.id "
                "WHERE rd.doc_id = %s ORDER BY d.created_at DESC", (doc_id,)
            ).fetchall()
            # What EUDAMED said about this document's device group, if the
            # button has ever been pressed for it. Keyed on the Basic UDI-DI,
            # not the doc: several documents can share one.
            eudamed_rows = conn.execute(
                "SELECT udi_di, coalesce(device_name, trade_name) AS device_name, "
                "       reference, synced_at "
                "FROM eudamed_mirror WHERE basic_udi_di = %s "
                "ORDER BY reference NULLS LAST, udi_di",
                (doc["basic_udi_di"],),
            ).fetchall() if doc["basic_udi_di"] else []
        return templates.TemplateResponse(
            request, "document_detail.html",
            {"doc": doc, "items": items_covered,
             "evidence": evidence, "chain": chain, "chases": chases,
             "eudamed": eudamed, "eudamed_rows": eudamed_rows},
        )

    #: The three states a group can be in with respect to DISCOVER, and the
    #: predicate that selects each. "Never looked" and "looked and found
    #: nothing" are different facts about our own diligence, and the registry
    #: has always been able to tell them apart -- `discovery_log` records a row
    #: per attempt with its source and outcome. Nothing read it.
    #: FOUR states since 2026-09-14 (F3). The manual rung used to log its
    #: handoff as a `hit`, so a group every rung had missed read "Searched,
    #: found something" -- 258 of them. It logs `handoff` now, which is neither
    #: a find nor a plain miss: we stopped looking AND opened a task, and the
    #: office needs the second half to know where the work went.
    _DISCOVERY_STATES = {
        "never": ("Never searched", "s.group_id IS NULL"),
        "empty": ("Searched, found nothing",
                  "s.group_id IS NOT NULL AND s.hits = 0 AND s.handoffs = 0"),
        "handoff": ("Searched, nothing found, sent to Missing documents",
                    "s.hits = 0 AND s.handoffs > 0"),
        "found": ("Searched, found something", "s.hits > 0"),
    }

    @app.get("/discovery", response_class=HTMLResponse)
    def discovery(request: Request, state: str = "never",
                  page: int = Query(0, ge=0, le=MAX_PAGE)):
        """Which groups DISCOVER has looked at, and which it has never touched.

        The coverage percentage on the status board answers "do we hold paper
        for this article". It cannot answer "did we ever go looking", and those
        read very differently side by side: measured 2026-09-03, **8.119 of
        8.208 groups have no `discovery_log` row at all**, and 4.256 of the
        4.265 medical-device items sit in one of them. A registry that is 99,8%
        covered and has searched 89 groups is describing where its documents
        came from -- the SFTP corpus -- not a search that found them.

        Three states, all reachable, because the middle one is the honest
        answer to "is this item uncovered because nobody looked, or because
        there is nothing to find?". It is empty today (every group ever
        searched got a hit), and that is worth being able to SEE rather than
        inferring from its absence.

        Ordered by medical-device items descending: the head of this list is
        where a discovery run buys the most coverage, and it is exactly what
        the manufacturer's own Search button walks through.
        """
        label, predicate = _DISCOVERY_STATES.get(
            state, _DISCOVERY_STATES["never"])
        if state not in _DISCOVERY_STATES:
            state = "never"
        counts_cte = """
            WITH counts AS (
              SELECT m.group_id,
                     count(*)                                   AS items,
                     count(*) FILTER (WHERE im.md_flag IS TRUE)  AS md_items
              FROM item_group_member m
              JOIN item_mirror im ON im.item_ref = m.item_ref
              GROUP BY m.group_id
            ), searched AS (
              SELECT group_id, max(at) AS last_at, count(*) AS attempts,
                     count(*) FILTER (WHERE outcome='hit') AS hits,
                     count(*) FILTER (WHERE outcome='handoff') AS handoffs
              FROM discovery_log GROUP BY group_id
            )
        """
        with _conn() as conn:
            summary = conn.execute(counts_cte + """
                SELECT
                  count(*)                                        AS groups,
                  count(*) FILTER (WHERE s.group_id IS NULL)       AS never,
                  count(*) FILTER (WHERE s.group_id IS NOT NULL
                                     AND s.hits = 0
                                     AND s.handoffs = 0)          AS empty,
                  count(*) FILTER (WHERE s.hits = 0
                                     AND s.handoffs > 0)          AS handoff,
                  count(*) FILTER (WHERE s.hits > 0)               AS found,
                  COALESCE(sum(c.md_items) FILTER (
                      WHERE s.group_id IS NULL), 0)               AS md_never,
                  COALESCE(sum(c.md_items), 0)                    AS md_total
                FROM item_group g
                LEFT JOIN counts c   ON c.group_id = g.group_id
                LEFT JOIN searched s ON s.group_id = g.group_id
            """).fetchone()
            total = conn.execute(counts_cte + """
                SELECT count(*) AS n FROM item_group g
                LEFT JOIN searched s ON s.group_id = g.group_id
                WHERE """ + predicate).fetchone()["n"]
            rows = conn.execute(counts_cte + """
                SELECT g.group_id, g.canonical_manufacturer, g.label,
                       COALESCE(c.md_items, 0) AS md_items,
                       COALESCE(c.items, 0)    AS items,
                       s.last_at,
                       COALESCE(s.attempts, 0) AS attempts,
                       COALESCE(s.hits, 0)     AS hits,
                       COALESCE(s.handoffs, 0) AS handoffs
                FROM item_group g
                LEFT JOIN counts c   ON c.group_id = g.group_id
                LEFT JOIN searched s ON s.group_id = g.group_id
                WHERE """ + predicate + """
                ORDER BY md_items DESC, items DESC, g.group_id
                LIMIT %s OFFSET %s
            """, (DOCUMENTS_PAGE_SIZE, page * DOCUMENTS_PAGE_SIZE)).fetchall()
        return templates.TemplateResponse(
            request, "discovery.html",
            {"rows": rows, "summary": summary, "state": state,
             "state_label": label,
             "states": [(k, v[0]) for k, v in _DISCOVERY_STATES.items()],
             "pager": _pager(page, DOCUMENTS_PAGE_SIZE, total)},
        )

    #: The gap this page is about, and the predicate that selects it, over the
    #: `held` CTE (`_COVERAGE_HELD`, module level, shared with the headline).
    #: Three separate questions rather than one number: "no declaration" is
    #: 2.805 items by document TYPE and 2.578 by REGULATION,
    #: both defensible, and picking one silently is how a board and an API come
    #: to disagree in front of a client. `docs/specs/kpi.md` K1 filters on
    #: regulation; that stays the KPI's business, and this page publishes no
    #: percentage of its own.
    #:
    #: `doc` is the complement of the status board's headline
    #: (`coverage_headline`), through the one `_HAS_DECLARATION` predicate:
    #: md items minus declarations on file. It counted any production DoC link
    #: until 2026-09-11 (2.804 on dev), one item fewer than the headline's
    #: complement (2.805) -- a DoC linked only by a person (`manual`).
    _COVERAGE_GAPS = {
        "none": ("No document at all",
                 "h.item_ref IS NULL",
                 "Nothing production-linked to this article. The smallest and "
                 "worst list."),
        "doc": ("No Declaration of Conformity",
                "NOT COALESCE(h.has_doc, false)",
                "No published declaration linked to this article by its item "
                "number, the supplier's article number, UDI or the supplier's "
                "coverage list. These are the items the status board counts "
                "as without a declaration. The article may still hold a "
                "certificate, an IFU, a declaration that covers its "
                "manufacturer's whole range, or one a person linked by hand."),
        "regime": ("No MDR or MDD document",
                   "NOT COALESCE(h.has_regime, false)",
                   "Nothing issued under a device regulation — a quality-system "
                   "certificate is a claim about the manufacturer, not about "
                   "this article."),
    }

    def _report_files() -> list[str]:
        """Every weekly report on disk, newest first.

        The period is in the file name by design (`report-2026-W36.html`), so a
        plain string sort is newest-first and needs no database read. Read
        through `load_config()` rather than `cfg`, the same source `/reports`
        has always used, so Today's link and the reports index can never point
        at two different directories.
        """
        d = load_config().web.reports_dir
        if not d or not pathlib.Path(d).is_dir():
            return []
        return sorted((f.name for f in pathlib.Path(d).glob("report-*.html")),
                      reverse=True)

    def _recent_weeks(limit: int = 2) -> list[dict]:
        """Today's weekly summary: the latest report and the one before it,
        each named as a week rather than as a file (spec § 4)."""
        return [{"name": f, "label": report_week_label(f), "href": f"/reports/{f}"}
                for f in _report_files()[:limit]]

    @app.get("/reports", response_class=HTMLResponse)
    def reports(request: Request):
        """The weekly reports the scheduler has written, newest first.

        `report.weekly` has produced a real snapshot every week since S1.5 and
        it lived in `job.result`: last week's was readable at `/scheduler`, the
        week before that was readable nowhere. These are files now, written by
        the worker into the archive volume, and this page is the index over
        them.

        Read-only and directory-scoped: filenames are matched against a fixed
        shape rather than trusted, so nothing outside `reports_dir` is
        reachable through this route (`/archive/{path}` learned the same lesson
        the hard way — it is served BY PATH and therefore staff-only).
        """
        d = load_config().web.reports_dir
        # Named as weeks, not as file names (spec § 9, P7b/c): "Week 36
        # (31 Aug-6 Sep)" is the same label Today puts on the same report, out
        # of the same helper. The file name stays the address in the link.
        weeks = [{"name": f, "label": report_week_label(f)}
                 for f in _report_files()]
        return templates.TemplateResponse(
            request, "reports.html",
            {"weeks": weeks, "configured": bool(d)})

    @app.get("/reports/{name}", response_class=HTMLResponse)
    def report_file(name: str):
        """One report, served as it was written.

        `name` is matched against the exact shape the writer produces and then
        joined and re-resolved under the root: a name is not a path, and this
        route must not become one.
        """
        d = load_config().web.reports_dir
        if not d or not _REPORT_NAME_RE.fullmatch(name):
            raise HTTPException(status_code=404, detail="unknown report")
        root = pathlib.Path(d).resolve()
        path = (root / name).resolve()
        if not path.is_file() or root not in path.parents:
            raise HTTPException(status_code=404, detail="unknown report")
        return HTMLResponse(path.read_text(encoding="utf-8"))

    @app.get("/coverage", response_class=HTMLResponse)
    def coverage(request: Request, gap: str = "doc",
                 page: int = Query(0, ge=0, le=MAX_PAGE)):
        """Which articles are missing which paperwork — counted, then listed.

        The status board answers "how covered are we" with a percentage. This
        answers "which ones, and what exactly is missing", which is the form
        the question takes when somebody is going to act on it.

        Scoped to `md_flag IS TRUE`. The 11.693 articles BC has not classified
        are counted and named on the page rather than folded in: we do not know
        whether they need a declaration, and a list that guesses would be a
        list nobody could work. That number is the real size of the question
        behind `[mfr-bind-empty-class]`.
        """
        if gap not in _COVERAGE_GAPS:
            gap = "doc"
        label, predicate, blurb = _COVERAGE_GAPS[gap]
        with _conn() as conn:
            summary = conn.execute(_COVERAGE_HELD + """
                SELECT count(*)                                              AS md_items,
                       count(*) FILTER (WHERE h.item_ref IS NULL)            AS none,
                       count(*) FILTER (WHERE NOT COALESCE(h.has_doc, false))    AS doc,
                       count(*) FILTER (WHERE NOT COALESCE(h.has_regime, false)) AS regime
                FROM item_mirror im
                LEFT JOIN held h ON h.item_ref = im.item_ref
                WHERE im.md_flag IS TRUE
            """).fetchone()
            unclassified = conn.execute(
                "SELECT count(*) AS n FROM item_mirror WHERE md_flag IS NULL"
            ).fetchone()["n"]
            total = conn.execute(_COVERAGE_HELD + f"""
                SELECT count(*) AS n FROM item_mirror im
                LEFT JOIN held h ON h.item_ref = im.item_ref
                WHERE im.md_flag IS TRUE AND {predicate}
            """).fetchone()["n"]
            rows = conn.execute(_COVERAGE_HELD + f"""
                SELECT im.item_ref, im.name,
                       a.canonical_name AS manufacturer,
                       COALESCE(h.types, ARRAY[]::text[]) AS types
                FROM item_mirror im
                LEFT JOIN held h ON h.item_ref = im.item_ref
                LEFT JOIN manufacturer_alias a ON a.raw_name = im.manufacturer_raw
                WHERE im.md_flag IS TRUE AND {predicate}
                ORDER BY a.canonical_name NULLS LAST, im.item_ref
                LIMIT %s OFFSET %s
            """, (DOCUMENTS_PAGE_SIZE, page * DOCUMENTS_PAGE_SIZE)).fetchall()
        return templates.TemplateResponse(
            request, "coverage.html",
            {"rows": rows, "summary": summary, "unclassified": unclassified,
             "gap": gap, "gap_label": label, "gap_blurb": blurb,
             "gaps": [(k, v[0]) for k, v in _COVERAGE_GAPS.items()],
             "pager": _pager(page, DOCUMENTS_PAGE_SIZE, total)},
        )

    @app.get("/audit", response_class=HTMLResponse)
    def audit(request: Request, event: str = "", q: str = "",
              page: int = Query(0, ge=0, le=MAX_PAGE)):
        """Who decided what, and when — `audit_log` on a screen.

        The trail has always been written (GATE, and the three repair CLIs) and
        was readable only over psql. For an MDR sign-off record that is the
        wrong place for it to live: "who approved this document, and on what
        evidence" is a question an auditor asks about one document, and until
        now answering it meant opening a database client.

        Read-only, like every other page in this role. Invariant 10 is what
        makes the page trustworthy on its own: each row is self-contained
        (`job_snapshot` is a copy, `via_job` a soft reference), so a row still
        reads correctly after its job has been deleted — which is why the job
        column links but is not required to resolve.

        The event filter is populated from the table's own DISTINCT values
        rather than a list mirrored here by hand. `audit_log.event` is plain
        text with no CHECK, so a hand-kept list in this module would be one
        more mirror to drift (see `tests/test_web.py`'s job-type lesson) and
        would silently hide any event a new writer introduces.
        """
        like = f"%{_like_escape(q)}%" if q else ""
        where = (
            "WHERE (%s = '' OR a.event = %s) "
            "AND (%s = '' OR a.decided_by ILIKE %s OR a.item_ref ILIKE %s "
            "     OR a.doc_id::text = %s) "
        )
        filters = (event, event, q, like, like, q)
        with _conn() as conn:
            total = conn.execute(
                "SELECT count(*) AS n FROM audit_log a " + where, filters
            ).fetchone()["n"]
            rows = conn.execute(
                "SELECT a.id, a.event, a.doc_id, a.item_ref, a.decided_by, "
                "       a.via_job, a.detail, a.at "
                "FROM audit_log a " + where +
                "ORDER BY a.at DESC, a.id DESC LIMIT %s OFFSET %s",
                (*filters, DOCUMENTS_PAGE_SIZE, page * DOCUMENTS_PAGE_SIZE),
            ).fetchall()
            events = conn.execute(
                "SELECT event, count(*) AS n FROM audit_log GROUP BY event "
                "ORDER BY event"
            ).fetchall()
        return templates.TemplateResponse(
            request, "audit.html",
            {"rows": rows, "events": events, "event": event, "q": q,
             "pager": _pager(page, DOCUMENTS_PAGE_SIZE, total)},
        )

    @app.get("/data-quality", response_class=HTMLResponse)
    def data_quality(request: Request, kind: str = "",
                     page: int = Query(0, ge=0, le=MAX_PAGE)):
        """Standing ledger of catalogue weirdness. This page is the question
        list for BC/IT: every row is something the export did that we could not
        interpret, counted rather than guessed at.

        `subject` is whatever the anomaly is about, and which table that is
        depends on the kind: an `item_ref` for the catalogue kinds (19.141 of
        19.147 rows live), a `content_hash` for `no_text_layer`. Both joins are
        against a unique key, so neither can multiply rows; whichever resolves
        decides what the subject links to and whose name is shown beside it. A
        subject that resolves to neither stays plain text — an href to a 404
        claims there is something behind it.

        The second block is a different question with the same purpose.
        `item_class_check` (033) puts BC's `product_class` beside the class the
        item's production documents state, so a blank BC class can be filled
        from evidence already held and a contradicted one can be asked about.
        Only `conflict` and `fillable` get rows — `agree` and `agree-family` are
        answers, not work. BC stays the source of truth either way: this page
        writes nothing, here or anywhere else.
        """
        with _conn() as conn:
            summary = conn.execute(
                "SELECT kind, count(*) AS subjects, sum(seen_count) AS observations "
                "FROM data_anomaly GROUP BY kind ORDER BY subjects DESC"
            ).fetchall()
            total = conn.execute(
                "SELECT count(*) AS n FROM data_anomaly WHERE (%s = '' OR kind = %s)",
                (kind, kind),
            ).fetchone()["n"]
            rows = conn.execute(
                "SELECT a.kind, a.subject, a.catalogue, a.detail, a.seen_count, "
                "  a.last_seen, m.name AS item_name, "
                "  (m.item_ref IS NOT NULL) AS is_item, "
                "  d.doc_id AS doc_id, d.type AS doc_type "
                "FROM data_anomaly a "
                "LEFT JOIN item_mirror m ON m.item_ref = a.subject "
                "LEFT JOIN document d ON d.content_hash = a.subject "
                "WHERE (%s = '' OR a.kind = %s) "
                # ties on last_seen are broken by id: a whole ingest run stamps
                # the same timestamp, and an unstable sort makes paging repeat
                # and drop rows
                "ORDER BY a.last_seen DESC, a.id DESC LIMIT %s OFFSET %s",
                (kind, kind, DATA_QUALITY_PAGE_SIZE, page * DATA_QUALITY_PAGE_SIZE),
            ).fetchall()
            # Every verdict is counted, including the two that are answers
            # rather than work: "1.540 agree" is the number that makes the
            # handful of conflicts worth reading.
            class_summary = conn.execute(
                "SELECT verdict, count(*) AS n FROM item_class_check "
                "GROUP BY verdict ORDER BY verdict"
            ).fetchall()
            # ...but only the ACTIONABLE two are listed by item. Capped with its
            # own total rather than paged: this is a worklist to be worked
            # through, not a ledger to be browsed, and the count says plainly
            # how much is not on screen.
            class_total = conn.execute(
                "SELECT count(*) AS n FROM item_class_check "
                "WHERE verdict IN ('conflict','fillable')"
            ).fetchone()["n"]
            class_rows = conn.execute(
                "SELECT item_ref, bc_class, doc_class, doc_id, type, verdict "
                "FROM item_class_check WHERE verdict IN ('conflict','fillable') "
                # conflict first: a contradiction is a question about data we
                # already hold, a blank is only an opportunity to fill one.
                "ORDER BY verdict, item_ref LIMIT %s",
                (CLASS_CHECK_LIMIT,),
            ).fetchall()
        for r in rows:
            r["subject_name"] = (
                r["item_name"] if r["is_item"]
                else _doc_type_label(r["doc_type"]) if r["doc_id"]
                else None
            )
            # Pretty-printed for the disclosure the template puts it behind: a
            # one-line jsonb dump is unreadable and was wide enough to set the
            # whole table's width.
            r["detail_pretty"] = (
                json.dumps(r["detail"], indent=2, ensure_ascii=False) if r["detail"] else ""
            )
        return templates.TemplateResponse(
            request, "data_quality.html",
            {"summary": summary, "rows": rows, "kind": kind,
             "pager": _pager(page, DATA_QUALITY_PAGE_SIZE, total),
             "class_summary": class_summary, "class_rows": class_rows,
             "class_total": class_total, "class_shown": CLASS_CHECK_LIMIT},
        )

    @app.get("/expiry", response_class=HTMLResponse)
    def expiry(
        request: Request,
        back: int = Query(EXPIRED_LOOKBACK_DAYS, ge=0, le=3650),
        ahead: int = Query(EXPIRING_WINDOW_DAYS, ge=0, le=3650),
    ):
        """What has lapsed and what is about to, as three separate questions.

        `ahead` defaults to `EXPIRING_WINDOW_DAYS` (D7, 30 days) and `back`
        to `EXPIRED_LOOKBACK_DAYS` (180), the two windows the header strip
        counts; each heading names its window.

        This board used to be one table under a single "horizon" filter that
        selected `expires <= current_date + days` — which silently includes
        everything already expired. On the live registry that was ALL of it:
        four rows reading 2237, 814, 814 and 106 days ago, under a heading
        saying "what is about to lapse" and a column headed "days left". A
        certificate that lapsed in May with 1.046 items on it is a different
        and worse problem than one lapsing next month, and one table cannot
        say so.

        So the same rows are partitioned rather than filtered:

          * **recently expired** (`back` days) — already broken, recently
            enough that a renewal is the live piece of work. Listed first
            because it is the only one of the three that is an emergency.
          * **expiring soon** (`ahead` days) — the horizon this board always
            claimed to be.
          * **long expired** — beyond `back`. Kept, collapsed: a lapsed
            certificate that still carries production links is exposure we
            have to be able to evidence on request, and hiding the row does
            not remove the exposure.

        Anything further ahead than `ahead` is COUNTED and named rather than
        dropped, with a link that widens the window (CLAUDE.md: skipped rows
        are reported, never silent).

        Both windows are bounded because they reach Postgres as
        `current_date + %s`: `date + bigint` has no operator, so an
        out-of-range value crashes the page with UndefinedFunction rather than
        returning an empty board. 0 to 3650 covers the ten-year retention
        window.

        Collapsed to one row per lapsing THING, not per document: 62 rows on the
        live board were a single IVOCLAR certificate (`G15 043306 0282 Rev. 00`,
        2026-05-04) repeated across the 62 declarations that cite it. That is
        one email and one renewal, and a board that lists it 62 times hides both
        the count and any second certificate among the copies. Each row carries
        what the decision actually needs: how many documents cite it, how many
        catalogue items lose coverage when it lapses, and how many days are
        left."""
        with _conn() as conn:
            rows = conn.execute(
                _LAPSING_CTE + """
                SELECT l.manufacturer, l.type, l.regulation, l.cert_number, l.expires,
                       bool_or(l.inherited)              AS inherited,
                       bool_or(l.basis = 'staleness')    AS staleness,
                       count(*)                          AS documents,
                       (array_agg(l.doc_id ORDER BY l.doc_id))[1] AS sample_doc_id,
                       (l.expires - current_date)::int   AS days_left,
                       -- items that lose coverage: production links on any of
                       -- the documents in this group, counted DISTINCT because
                       -- one item can hold several of them
                       (SELECT count(DISTINCT il.item_ref)
                          FROM item_document il
                         WHERE il.status='production'
                           AND il.doc_id IN (SELECT doc_id FROM lapsing l2
                                              WHERE l2.manufacturer = l.manufacturer
                                                AND l2.cert_number IS NOT DISTINCT FROM l.cert_number
                                                AND l2.expires = l.expires)) AS items_affected
                FROM lapsing l
                GROUP BY """ + _LAPSING_GROUP_BY + """
                ORDER BY l.expires, l.manufacturer
                """,
            ).fetchall()
            # Cross-link to the chase: a lapsing certificate whose manufacturer
            # already has a live renewal draft is work in progress, not work to
            # start. Keyed on manufacturer because that is the cadence unit
            # (spec §7.2) -- one mail covers every document below it.
            drafts_by_mfr = {
                r["manufacturer"]: r
                for r in conn.execute(
                    "SELECT DISTINCT ON (r.manufacturer) r.manufacturer, "
                    "       d.id AS draft_id, d.status, d.kind "
                    "FROM email_draft d JOIN renewal_request r ON r.id = d.renewal_request_id "
                    "WHERE d.status IN ('draft','ready') AND r.manufacturer IS NOT NULL "
                    "ORDER BY r.manufacturer, d.created_at DESC"
                ).fetchall()
            }
            # Task 6's four EUDAMED findings, whole-registry (no
            # canonical_name — the scoped version is on the manufacturer
            # page). Same connection, same transaction as everything above.
            cert_findings = registry.certificate_findings(conn)
        # Recently expired leads: it is the only one of the three that is a
        # live emergency. Within it the ordering flips to most-recent-first —
        # "expired last week" is more actionable than "expired in 2020", and
        # the SQL's ascending order puts the oldest on top.
        # F34: a declaration whose only date is Dentalia's own five-year review
        # horizon (`basis='staleness'`) is DUE A LOOK, not expired. The item
        # card has said so since 2026-08-24 (`app/compliance.py`) and this board
        # said the opposite about the same 21 documents, which is a house rule
        # reported as a regulatory breach -- the one error this page cannot
        # afford. Split out BEFORE `recent`, and `_expiring_counts` splits it
        # the same way, so the count in the menu and the rows on this page stay
        # the same rows.
        review = [r for r in rows if r["staleness"] and r["days_left"] < 0]
        # One ask per manufacturer, not one per row: the table groups by
        # (manufacturer, type, regulation, cert number, date), so a supplier with
        # four review-due declarations is four rows and one letter. Counted here
        # rather than in the template so the heading and the buttons cannot
        # disagree about how many there are.
        review_mfrs = sorted(
            {r["manufacturer"] for r in review if r["manufacturer"] != "unknown"})
        review_counts = {m: sum(1 for r in review if r["manufacturer"] == m)
                         for m in review_mfrs}
        recent = [r for r in rows
                  if not r["staleness"] and -back <= r["days_left"] < 0]
        recent.reverse()
        soon = [r for r in rows if 0 <= r["days_left"] <= ahead]
        # `old` keeps its meaning -- long-expired, real expiries -- so a review
        # row never lands there either, whatever its age.
        old = [r for r in rows if not r["staleness"] and r["days_left"] < -back]
        beyond = [r for r in rows if r["days_left"] > ahead]
        return templates.TemplateResponse(
            request, "expiry.html",
            {"recent": recent, "soon": soon, "old": old, "review": review,
             "review_mfrs": review_mfrs, "review_counts": review_counts,
             "beyond": len(beyond),
             "beyond_next": min((r["days_left"] for r in beyond), default=None),
             "back": back, "ahead": ahead, "drafts_by_mfr": drafts_by_mfr,
             "cert_findings": cert_findings},
        )

    @app.get("/search", response_class=HTMLResponse)
    def search(request: Request, q: str = ""):
        """One box over the three things people arrive holding.

        A cert number was findable only from `/documents`, an item ref only
        from `/items`, a manufacturer only from `/manufacturers` — so answering
        "what do we know about this?" meant knowing which screen owns the
        answer before asking the question. Everything here is a SELECT, capped,
        and each section links to the page that owns the full list.

        Deliberately narrow: exact-ish prefix/substring matching on the
        identifiers people actually paste (item ref, product name, cert number,
        manufacturer name or BC code) and a bare number treated as a document
        id. No fuzzy matching — this is navigation, and pg_trgm scoring belongs
        to the matching pipeline, where a wrong answer is a bug rather than a
        wasted click.
        """
        q = q.strip()
        ctx: dict = {"request": request, "q": q, "items": [], "documents": [],
                     "manufacturers": [], "doc_by_id": None}
        if not q:
            return templates.TemplateResponse(request, "search.html", ctx)
        like = f"%{_like_escape(q)}%"
        with _conn() as conn:
            if q.isdigit():
                ctx["doc_by_id"] = conn.execute(
                    "SELECT doc_id, type, regulation, status, cert_number "
                    "FROM document WHERE doc_id=%s", (int(q),),
                ).fetchone()
            ctx["items"] = conn.execute(
                "SELECT m.item_ref, m.name, m.catalogue, "
                "       COALESCE(a.canonical_name, m.manufacturer_raw) AS manufacturer "
                "FROM item_mirror m "
                "LEFT JOIN manufacturer_alias a ON a.raw_name = m.manufacturer_raw "
                "WHERE m.item_ref ILIKE %s OR m.name ILIKE %s OR m.mfr_ref ILIKE %s "
                "ORDER BY m.item_ref LIMIT %s",
                (like, like, like, SEARCH_SECTION_LIMIT),
            ).fetchall()
            ctx["documents"] = conn.execute(
                "SELECT doc_id, type, regulation, status, cert_number, validity_to "
                "FROM document WHERE cert_number ILIKE %s OR content_hash ILIKE %s "
                "ORDER BY doc_id DESC LIMIT %s",
                (like, like, SEARCH_SECTION_LIMIT),
            ).fetchall()
            ctx["manufacturers"] = conn.execute(
                "SELECT a.canonical_name, count(DISTINCT m.item_ref) AS items "
                "FROM manufacturer_alias a "
                "LEFT JOIN item_mirror m ON m.manufacturer_raw = a.raw_name "
                "WHERE a.canonical_name ILIKE %s OR a.raw_name ILIKE %s "
                "GROUP BY a.canonical_name ORDER BY a.canonical_name LIMIT %s",
                (like, like, SEARCH_SECTION_LIMIT),
            ).fetchall()
        return templates.TemplateResponse(request, "search.html", ctx)

    @app.get("/_pulse", response_class=HTMLResponse)
    def pulse(request: Request):
        """The office menu with its counts and its health line, as an htmx
        fragment (spec § 7).

        It carried the sticky header strip until 2026-09-14; the strip is gone
        and its counts moved into the menu entries they were always about.
        Same mechanism, same cadence: a fragment behind its own request rather
        than context on every route, because base.html is shared by ~30
        handlers that each build their own ctx dict, and threading these counts
        through all of them would couple every page to the queue. Lazily
        loaded, so a page never waits on it, then polled so a long-lived tab
        stays honest.

        base.html renders the SAME template with no counts, so with JS off (or
        before the first swap) the menu is still the menu and the counts read
        as not-loaded rather than as a silent zero.
        """
        with _conn() as conn:
            counts = _menu_counts(conn)
            services = _pulse_services(conn)
        return templates.TemplateResponse(
            request, "_pulse.html",
            {"pulse": counts, "services": services,
             "health": _health_line(services, counts["timeouts"])})

    #: The five pages folded behind `/pipeline`, in the order a person meets
    #: them: what is moving, what the catalogue got wrong, what needs a hand,
    #: what gave up, and what runs on a timer. `count` names the key in the
    #: context dict, or None where a bare count would mean nothing useful, and
    #: `unit` is what that count counts, rendered beside it.
    #:
    #: The unit exists because the column used to be headed "Waiting" for all
    #: five, and the Data quality figure is not work waiting for anyone. It is
    #: `count(*) FROM data_anomaly`, one row per (kind, subject): on dev on
    #: 2026-09-11, 19,345 rows, of which 19,142 are about catalogue items
    #: (11,693 with no device class in BC, 7,082 with no supplier article
    #: number, ...), 194 about documents (unresolved certificate references,
    #: scans with no text) and 9 whose subject is neither (T0 template misses
    #: keyed by manufacturer, and rows whose item or document is gone).
    #: Standing notes.
    _PIPELINE_PAGES = (
        ("/inflight", "Processing", "in_flight_docs", "documents being read",
         "Documents moving through the pipeline right now, counted by content "
         "hash rather than by job."),
        ("/data-quality", "Data quality", "anomalies", "notes on items and documents",
         "Everything the Business Central export did that we could not "
         "interpret, plus BC's device class beside the class our documents state."),
        ("/manual", "Manual", "manual_open", "open tasks",
         "Jobs that stopped and want a person. Read-only: the decisions "
         "themselves are taken on Review."),
        ("/dead", "Failed", "failed_actionable", "failed tasks",
         "Jobs that exhausted their retries with no newer attempt queued for "
         "them. Nothing here is lost -- a dead job is a job to re-enqueue, not "
         "work to redo."),
        ("/scheduler", "Scheduler", "crons_armed", "recurring checks scheduled",
         "The ten perpetual cron jobs, when each last ran and when it runs "
         "next. There is no scheduler process; the crons run on the queue."),
        ("/bc-push", "Business Central", None, None,
         "What we would tell Business Central about each article -- a valid "
         "declaration, a valid certificate, and a link back here. Shown before "
         "anything is sent."),
    )

    @app.get("/pipeline", response_class=HTMLResponse)
    def pipeline_hub(request: Request):
        """One door into the five pages that are about operating the machine.

        The sidebar carried all five plus Status, Weekly reports, Upload and
        Import under one PIPELINE label -- ten of 26 entries, most of which a
        compliance officer never opens (`[sidebar-is-26-entries]`, UI audit
        2026-09-04). Folding is a placement, not a permission: every page is
        still one click away and none of them changed.

        Carries the counts because a hub that is only a list of links is worse
        than the sidebar it replaced -- you would have to open all five to find
        the one with something in it. Three come from `_pulse_counts`, which
        outlived the header strip it was written for.
        """
        with _conn() as conn:
            counts = _pulse_counts(conn)
            counts["anomalies"] = conn.execute(
                "SELECT count(*) AS n FROM data_anomaly").fetchone()["n"]
            # Armed crons, not due ones: the question this page answers is
            # "is the timer running at all", which a zero here says loudly.
            counts["crons_armed"] = conn.execute(
                "SELECT count(*) AS n FROM job WHERE type = 'scheduler.tick' "
                "AND status IN ('pending','running')").fetchone()["n"]
            # The three sections `/dead` actually shows, not every row with
            # status='dead' (T4's ruling). A dead job with newer work already
            # queued for it drops out of the actionable totals on EVERY screen,
            # so a hub reading "94 failed tasks" over a page listing 39 is the
            # hub disagreeing with the page it links to.
            counts["failed_actionable"] = sum(failures.failed_counts(conn).values())
        pages = [{"href": h, "label": l, "count": counts.get(k), "unit": u,
                  "blurb": b}
                 for h, l, k, u, b in _PIPELINE_PAGES]
        return templates.TemplateResponse(
            request, "pipeline.html", {"request": request, "pages": pages})

    @app.get("/inflight", response_class=HTMLResponse)
    def inflight(request: Request):
        """Documents currently moving through the pipeline, counted by
        content hash rather than by job. One PDF can hold several jobs at
        once; the question 'how many certificates are being processed' is
        about documents, and the status board only ever answers the job
        question."""
        with _conn() as conn:
            stages = conn.execute(
                "SELECT type AS stage, "
                "count(DISTINCT payload->>'content_hash') AS documents, "
                "count(*) AS jobs "
                "FROM job WHERE status IN ('pending','running') "
                "AND payload ? 'content_hash' "
                "GROUP BY type ORDER BY type"
            ).fetchall()
            total = _in_flight_docs(conn)
        return templates.TemplateResponse(
            request, "inflight.html", {"stages": stages, "total": total},
        )

    @app.get("/emails", response_class=HTMLResponse)
    def emails(request: Request):
        """Inbound emails the S2.4 mailbox poll (`email.poll`) has processed:
        which carried a usable document (linked through to the registry document
        once GATE has created it), which were skipped and why. Read-only over
        the durable `email_poll_log` ledger — the web role has SELECT only, and
        the poll itself is a worker producer (invariant 1)."""
        with _conn() as conn:
            rows = conn.execute(
                "SELECT l.id, l.mailbox, l.message_id, l.from_addr, l.subject, "
                "l.received_at, l.processed_at, l.had_attachments, l.attachments, "
                # What the message ASKED FOR. The body is never stored (spec
                # §10, invariant 12) -- this is the cheap-tier summary made at
                # poll time, and `summary_status` is why there is not one when
                # `body_summary` is null (never-silent).
                "l.body_summary, l.summary_intent, l.summary_status, l.summary_model, "
                # Which chase it answers, as `email.poll` matched it (2026-09-11),
                # and the draft to open for it: the newest of that request's.
                "l.renewal_request_id, rr.manufacturer AS request_manufacturer, "
                "(SELECT max(d.id) FROM email_draft d "
                "  WHERE d.renewal_request_id = l.renewal_request_id) AS request_draft_id "
                "FROM email_poll_log l "
                "LEFT JOIN renewal_request rr ON rr.id = l.renewal_request_id "
                "ORDER BY l.id DESC LIMIT %s",
                (EMAILS_PAGE_LIMIT,),
            ).fetchall()
            total = conn.execute(
                "SELECT count(*) AS c FROM email_poll_log"
            ).fetchone()["c"]
            # Resolve the archived/deduped attachment hashes to their registry
            # document (if GATE has created one yet) in one query, so a useful
            # attachment links through to what it became.
            hashes = sorted({
                a["content_hash"]
                for r in rows for a in (r["attachments"] or [])
                if a.get("content_hash")
            })
            docs: dict[str, dict] = {}
            if hashes:
                # Type/regulation/expiry come along so the row can say whether
                # the attachment became a GOOD document, not merely that it
                # became one: a linked doc_id alone reads as success even when
                # the document is a 2024-lapsed MDD certificate. Expiry through
                # `document_effective_expiry` (migration 027), the same view
                # /items and /expiry use -- never raw validity_to, which is not
                # the authority on when a document lapses.
                for d in conn.execute(
                    "SELECT d.content_hash, d.doc_id, d.status, d.type, d.regulation, "
                    "       e.expires, e.basis AS expiry_basis "
                    "FROM document d "
                    "LEFT JOIN document_effective_expiry e ON e.doc_id = d.doc_id "
                    "WHERE d.content_hash = ANY(%s)", (hashes,)
                ).fetchall():
                    # The type as a word, built here rather than in the
                    # template: "DoC" is our abbreviation, and the page that
                    # tells an office reader what arrived should say what it
                    # is (spec § 9, P7c). Same map as the Review form's
                    # dropdown -- `web/words.py` -- so the two cannot drift.
                    d["type_label"] = words.doc_type_word(d["type"])
                    docs[d["content_hash"]] = d
            # Provenance: did we already hold these bytes, and by which route?
            # `email.poll` writes its own fetch_log row (source 'email') for
            # every attachment, so the interesting fact is whether a NON-email
            # source got there first -- that is why most inbound attachments
            # arrive already staged, and without saying so the page implies the
            # email is what produced the document.
            origins: dict[str, dict] = {}
            if hashes:
                for o in conn.execute(
                    "SELECT content_hash, "
                    "       array_agg(DISTINCT source) FILTER (WHERE source <> 'email') "
                    "         AS other_sources, "
                    "       min(fetched_at) FILTER (WHERE source <> 'email') AS first_seen "
                    "FROM fetch_log WHERE content_hash = ANY(%s) "
                    "GROUP BY content_hash", (hashes,)
                ).fetchall():
                    if o["other_sources"]:
                        origins[o["content_hash"]] = o
        return templates.TemplateResponse(
            request, "emails.html",
            {"rows": rows, "total": total, "shown": len(rows), "docs": docs,
             "origins": origins, "today": datetime.now(timezone.utc).date(),
             "disposition_words": ATTACHMENT_DISPOSITION_WORDS},
        )

    # ----------------------------------------------------------------- #
    # OUTBOUND drafts (S2.4). The system NEVER SENDS (spec §7.1): these pages
    # let a person read, edit and release a renewal mail, which they then send
    # by hand from mdr@dentalia.si. `ready` is therefore terminal here -- it
    # enqueues nothing, because there is nothing downstream to enqueue.
    #
    # This is the one place `web` writes something other than `upload_inbox`,
    # and the grant is narrow by design (migration 029: SELECT + UPDATE on
    # `email_draft`, no INSERT, no DELETE). Editing our own outgoing
    # correspondence creates no registry row, no link and no evidence, so
    # invariant 1 is untouched: `document` / `item_document` / `evidence` remain
    # GATE-only and this role still cannot write them.
    # ----------------------------------------------------------------- #
    def _draft_row(conn, draft_id: int):
        return conn.execute(
            "SELECT d.*, r.manufacturer, r.period_key, r.reason, r.state AS request_state "
            "FROM email_draft d LEFT JOIN renewal_request r ON r.id = d.renewal_request_id "
            "WHERE d.id = %s", (draft_id,)
        ).fetchone()

    def _draft_documents(conn, renewal_request_id):
        """What this one mail is chasing. Ordered by expiry so the reader sees
        the worst first -- the same ordering /expiry uses, for the same reason."""
        if renewal_request_id is None:
            return []
        return conn.execute(
            "SELECT d.doc_id, d.type, d.regulation, d.cert_number, "
            "       e.expires, e.basis AS expiry_basis, "
            "       count(DISTINCT id.item_ref) AS items "
            "FROM renewal_request_document rd "
            "JOIN document d ON d.doc_id = rd.doc_id "
            "LEFT JOIN document_effective_expiry e ON e.doc_id = d.doc_id "
            "LEFT JOIN item_document id ON id.doc_id = d.doc_id AND id.status='production' "
            "WHERE rd.renewal_request_id = %s "
            "GROUP BY d.doc_id, d.type, d.regulation, d.cert_number, e.expires, e.basis "
            "ORDER BY e.expires NULLS LAST, d.doc_id", (renewal_request_id,)
        ).fetchall()

    @app.get("/drafts", response_class=HTMLResponse)
    def drafts_board(request: Request, status: str | None = None):
        """Renewal mails waiting on a person. Grouped the way the client's
        cadence rule works (spec §7.2): one row per manufacturer per period,
        never one per expiring document."""
        where, params = "", []
        if status:
            where = "WHERE d.status = %s"
            params.append(status)
        with _conn() as conn:
            rows = conn.execute(
                "SELECT d.id, d.kind, d.status, d.subject, d.to_addrs, d.created_at, "
                "       d.edited_by, d.edited_at, d.released_at, "
                # Migration 057: a gap request has no `renewal_request` to
                # borrow a name from, so the join alone left the column empty
                # for exactly the drafts a person most needs to identify.
                # Join first, column second -- renewal drafts keep answering
                # through the request they belong to.
                "       COALESCE(r.manufacturer, d.manufacturer) AS manufacturer, "
                "       r.period_key, r.state AS request_state, "
                "       (SELECT count(*) FROM renewal_request_document rd "
                "         WHERE rd.renewal_request_id = d.renewal_request_id) AS documents "
                "FROM email_draft d "
                "LEFT JOIN renewal_request r ON r.id = d.renewal_request_id "
                f"{where} ORDER BY d.created_at DESC, d.id DESC LIMIT %s",
                (*params, DRAFTS_PAGE_LIMIT),
            ).fetchall()
            counts = {c["status"]: c["n"] for c in conn.execute(
                "SELECT status, count(*) AS n FROM email_draft GROUP BY status"
            ).fetchall()}
            # The menu entry "Renewal emails (n)" links here, so the number it
            # carries has to be ON this page (spec § 1, rule 7). Same helper,
            # not a second sum over `counts`: one definition of "to send".
            to_send = _drafts_waiting(conn)
        # The words, and the period as a week (spec § 9, P7c). Both pages read
        # the same two maps and the same week label, so a draft cannot read one
        # way on the board and another on itself.
        for r in rows:
            r["period_label"] = period_week_label(r["period_key"])
        return templates.TemplateResponse(
            request, "drafts.html",
            {"rows": rows, "counts": counts, "status": status,
             "to_send": to_send, "total": sum(counts.values()),
             "status_words": DRAFT_STATUS_WORDS, "kind_words": DRAFT_KIND_WORDS},
        )

    @app.get("/drafts/{draft_id}", response_class=HTMLResponse)
    def draft_detail(request: Request, draft_id: int):
        with _conn() as conn:
            draft = _draft_row(conn, draft_id)
            if draft is None:
                raise HTTPException(status_code=404, detail=f"no draft #{draft_id}")
            docs = _draft_documents(conn, draft["renewal_request_id"])
            for d in docs:
                d["type_label"] = words.doc_type_word(d["type"])
        return templates.TemplateResponse(
            request, "draft_detail.html",
            {"d": draft, "docs": docs, "today": datetime.now(timezone.utc).date(),
             "period_label": period_week_label(draft["period_key"]),
             "status_words": DRAFT_STATUS_WORDS, "kind_words": DRAFT_KIND_WORDS},
        )

    @app.post("/drafts/{draft_id}", response_class=HTMLResponse)
    def draft_save(request: Request, draft_id: int,
                   subject: str = Form(...), body: str = Form(...),
                   to_addrs: str = Form("")):
        """Save an edit. Only `draft` is editable -- once released, the text a
        person is about to send (or has sent) must not change under them."""
        user = _authenticated_user(request) or DEFAULT_DECIDED_BY
        recipients = [a.strip() for a in to_addrs.replace(";", ",").split(",") if a.strip()]
        ctx = {"request": request}
        if not subject.strip() or not body.strip():
            ctx["error"] = "subject and body cannot be empty"
            return templates.TemplateResponse(request, "_result.html", ctx, status_code=422)
        if not recipients:
            ctx["error"] = "at least one recipient is required"
            return templates.TemplateResponse(request, "_result.html", ctx, status_code=422)
        with _conn() as conn:
            row = conn.execute(
                "UPDATE email_draft SET subject=%s, body=%s, to_addrs=%s, "
                "  edited_by=%s, edited_at=now() "
                "WHERE id=%s AND status='draft' RETURNING id",
                (subject, body, recipients, user, draft_id),
            ).fetchone()
            conn.commit()
        if row is None:
            ctx["error"] = f"draft #{draft_id} is not editable (missing, or already released)"
            return templates.TemplateResponse(request, "_result.html", ctx, status_code=422)
        ctx["saved"] = f"draft #{draft_id} saved by {user}"
        return templates.TemplateResponse(request, "_result.html", ctx)

    @app.post("/drafts/{draft_id}/status", response_class=HTMLResponse)
    def draft_set_status(request: Request, draft_id: int, action: str = Form(...)):
        """`ready` = a person has approved the text and will send it by hand.
        `sent` = they have. `cancelled` = they will not. Nothing here sends, and
        a draft is never deleted -- the chase stays auditable either way."""
        allowed = {"ready": ("draft",), "sent": ("ready",), "cancelled": ("draft", "ready")}
        ctx = {"request": request}
        if action not in allowed:
            ctx["error"] = f"unknown action {action!r}"
            return templates.TemplateResponse(request, "_result.html", ctx, status_code=422)
        from_states = allowed[action]
        stamp = ", released_at=now()" if action == "ready" else ""
        with _conn() as conn:
            row = conn.execute(
                f"UPDATE email_draft SET status=%s{stamp} "
                "WHERE id=%s AND status = ANY(%s) RETURNING id, status",
                (action, draft_id, list(from_states)),
            ).fetchone()
            conn.commit()
        if row is None:
            ctx["error"] = (f"draft #{draft_id} cannot go to {action!r} "
                            f"(allowed only from {', '.join(from_states)})")
            return templates.TemplateResponse(request, "_result.html", ctx, status_code=422)
        ctx["saved"] = f"draft #{draft_id} is now {action}"
        return templates.TemplateResponse(request, "_result.html", ctx)

    @app.get("/", response_class=HTMLResponse)
    def today(request: Request):
        """Today: the office's first screen (spec § 4).

        Four lists of what is waiting for a person, the failures they can do
        something about, the coverage headline and the week's report. It
        replaced the KPI board here on 2026-09-14; the board moved to
        `/status`, where the operator group of the menu names it "System
        status".

        Every figure is the page's own figure, read through the helper that
        page reads it through -- `coverage_headline` (D4), `_expiring_counts`
        (D7), `missing_summary`, `failed_counts` -- so a number on Today and
        the list behind it cannot drift apart. Nothing here writes: the two
        Failed buttons post to the routes `/dead` already owns.
        """
        with _conn() as conn:
            ctx = {
                "request": request,
                "review": _review_summary(conn),
                "missing": missing_summary(conn),
                "expiring": _expiring_counts(conn),
                "drafts": _drafts_waiting(conn),
                "failed": failures.failed_counts(conn),
                "coverage": coverage_headline(conn),
                "weeks": _recent_weeks(),
            }
        return templates.TemplateResponse(request, "today.html", ctx)

    @app.get("/status", response_class=HTMLResponse)
    def status_board(
        request: Request,
        type: str | None = None,
        status: str | None = None,
        q: str | None = None,
    ):
        # Silently drop anything outside the closed enums rather than 4xx on a
        # hand-edited URL — this is a convenience filter, not a strict API.
        job_type = type if type in JOB_TYPES else None
        job_status = status if status in JOB_STATUS_ORDER else None
        search = q.strip() if q and q.strip() else None
        with _conn() as conn:
            ctx = {
                "request": request,
                "job_counts": _status_counts(conn),
                "recent_jobs": _recent_jobs(conn, job_type=job_type, job_status=job_status, q=search),
                "item_mirror": _item_mirror_summary(conn),
                "kpi": _kpi_board(conn, budget_cfg),
                # P6 (spec § 8): the headline is the acceptance metric, and
                # the in-flight tile is the strip's documents-being-read, not
                # every pending job (ten of which are perpetual crons).
                "coverage": coverage_headline(conn),
                "in_flight_docs": _in_flight_docs(conn),
                # The same actionable total `/dead` and `/pipeline` show. Kept
                # out of `kpi`, which is also the `/api/kpi` body: `dead_jobs`
                # there is a documented field over the raw column and stays
                # exactly what it says it is.
                "failed_actionable": sum(failures.failed_counts(conn).values()),
                # Rule 8 (spec § 1): the office reads one health line in the
                # sidebar, operators read the detail. The per-process chips
                # were on the header strip until it went; this is where they
                # live now, on the operator's own board.
                "services": _pulse_services(conn),
                "job_types": JOB_TYPES,
                "job_statuses": JOB_STATUS_ORDER,
                "stage_by_type": STAGE_BY_TYPE,
                "filter_type": type or "",
                "filter_status": status or "",
                "filter_q": q or "",
            }
        return templates.TemplateResponse(request, "status.html", ctx)

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_detail(request: Request, job_id: int):
        with _conn() as conn:
            job = _job_by_id(conn, job_id)
            if job is None:
                raise HTTPException(status_code=404, detail=f"job {job_id} not found")
            # `job.caused_by` (migration 037): a job page names the job whose
            # handler enqueued it, and lists its own direct children -- one
            # click each way walks the discover -> fetch -> extract ->
            # validate -> gate cascade without correlating hashes and
            # timestamps by hand. No recursive tree here on purpose (plan
            # 2026-08-24-job-lineage.md): parent + direct children per click
            # IS the walk.
            parent = (
                conn.execute(
                    "SELECT id, type, status FROM job "
                    "WHERE id = (SELECT caused_by FROM job WHERE id=%s)",
                    (job_id,),
                ).fetchone()
                if job["caused_by"] is not None
                else None
            )
            children = conn.execute(
                "SELECT id, type, status FROM job WHERE caused_by=%s ORDER BY id",
                (job_id,),
            ).fetchall()
        ctx = {
            "request": request,
            "job": job,
            "stage": STAGE_BY_TYPE.get(job["type"], job["type"]),
            "payload_pretty": json.dumps(job["payload"], indent=2, ensure_ascii=False),
            "parent": parent,
            "children": children,
        }
        return templates.TemplateResponse(request, "job_detail.html", ctx)

    @app.get("/ingest", response_class=HTMLResponse)
    def ingest_form(request: Request):
        # The form enqueued a job and handed back an id, and nothing on the page
        # ever said what previous runs did — "did last night's import work" was
        # answered by filtering the recent-jobs table by hand. The result
        # envelope was there the whole time (job.result, migration 019).
        with _conn() as conn:
            runs = _recent_runs(conn)
        for r in runs:
            r["result_pretty"] = json.dumps(r["result"], indent=2, ensure_ascii=False)
        ctx = {
            "request": request,
            "sources": SOURCES,
            "priorities": PRIORITIES,
            "import_files": _list_import_files(cfg.imports_dir),
            "imports_dir": cfg.imports_dir,
            "runs": runs,
        }
        return templates.TemplateResponse(request, "ingest.html", ctx)

    @app.post("/ingest", response_class=HTMLResponse,
              dependencies=[Depends(require_operator)])
    def ingest_submit(
        request: Request,
        source: str = Form(...),
        priority: str = Form("interactive"),
        csv_path_select: str = Form(""),
        csv_path_manual: str = Form(""),
        company: str = Form(""),
        delta_since: str = Form(""),
    ):
        # Not a form field: there is one Business Central and one article
        # numbering (Denis, 2026-08-19 closing PHASES.md G17/G11, restated
        # 2026-08-26). `item_mirror.catalogue` is NOT NULL and the column
        # stays, so a value is still written -- it is just not a question.
        catalogue = scheduler_cfg.ingest_catalogue

        error = None
        if source not in SOURCES:
            error = f"unknown source {source!r}"
        elif priority not in PRIORITIES:
            error = f"unknown priority {priority!r}"

        payload: dict | None = None
        ref_str = ""
        if error is None and source == "csv":
            ref = (csv_path_manual or csv_path_select).strip()
            if not ref:
                error = "a CSV/Excel path is required (pick one or type a path)"
            else:
                ref_str = ref
                payload = {"source": "csv", "ref": ref, "catalogue": catalogue}
        elif error is None and source == "bc_odata":
            company = company.strip()
            if not company:
                error = "company is required for a bc_odata ingest"
            else:
                try:
                    iso = delta_since if delta_since.endswith("Z") else delta_since + ":00Z"
                    # Validate shape without silently accepting garbage.
                    datetime.fromisoformat(iso.replace("Z", "+00:00"))
                except ValueError:
                    error = f"delta_since {delta_since!r} is not a valid datetime"
                else:
                    ref_str = f"{company}:{iso}"
                    payload = {
                        "source": "bc_odata",
                        "ref": {"company": company, "delta_since": iso},
                        "catalogue": catalogue,
                    }

        ctx = {"request": request}
        if error is not None:
            ctx["error"] = error
            return templates.TemplateResponse(request, "_result.html", ctx, status_code=422)

        dedupe_key = f"ingest.run:web:{catalogue}:{source}:{ref_str}"
        with _conn() as conn:
            jid = queue.enqueue(conn, "ingest.run", payload, dedupe_key, priority=priority)
            conn.commit()

        ctx["job_id"] = jid
        ctx["dedupe_key"] = dedupe_key
        ctx["receipt"] = words.receipt(
            "Import queued. The item list is read and the new items start "
            "looking for their documents.", jid)
        return templates.TemplateResponse(request, "_result.html", ctx)

    @app.get("/upload", response_class=HTMLResponse)
    def upload_form(request: Request, group_id: str = "", manual_task_id: str = ""):
        """Opened for one task (from Missing documents or /manual), the page
        names the item the document is for, and the group and the task travel
        as hidden inputs (spec § 5). "Target group id" and "Priority" left the
        form: `upload_submit` defaults the priority to `interactive`, which is
        what the form's preselected first option always sent. Without a task
        it is the plain upload form, and self-identifies by the REF list.
        A task that resolves decides the group posted, not the URL: the field
        is off screen, so nobody could correct a mismatch."""
        upload = {"subject": None, "group_id": group_id}
        if group_id.strip() or manual_task_id.strip():
            with _conn() as conn:
                upload = missing.upload_context(
                    conn, manual_task_id=manual_task_id, group_id=group_id)
        ctx = {"request": request, "subject": upload["subject"],
               "prefill_group_id": upload["group_id"],
               "prefill_manual_task_id": manual_task_id}
        return templates.TemplateResponse(request, "upload.html", ctx)

    @app.post("/upload", response_class=HTMLResponse)
    def upload_submit(
        request: Request,
        file: UploadFile = File(...),
        priority: str = Form("interactive"),
        target_group_id: str = Form(""),
        manual_task_id: str = Form(""),
    ):
        catalogue = scheduler_cfg.ingest_catalogue

        error = None
        if priority not in PRIORITIES:
            error = f"unknown priority {priority!r}"

        content = file.file.read()
        if error is None:
            is_pdf = (file.content_type == "application/pdf"
                      or (file.filename or "").lower().endswith(".pdf"))
            if not is_pdf:
                error = "only PDF uploads are accepted"
            elif not archiving.is_document(content):
                # The name and the browser's content-type both lie, and the
                # `.pdf` one lies in the direction that costs us: a saved web
                # page named `Declaration_of_Conformity.pdf` is exactly the file
                # that reached production in September 2026. Check the bytes
                # while the person is still here to pick a different file
                # (`[gate-covers-only-fetch]`).
                error = ("that file is named .pdf but its contents are not a PDF "
                         f"(it starts with {archiving.magic_of(content)!r}). "
                         "if you saved it from a website, download the file itself")
            elif len(content) > cfg.upload_max_mb * 1024 * 1024:
                error = f"file exceeds the {cfg.upload_max_mb} MB limit"

        gid = None
        if error is None and target_group_id.strip():
            try:
                gid = int(target_group_id)
            except ValueError:
                error = f"target_group_id {target_group_id!r} is not a number"

        mtid = None
        if error is None and manual_task_id.strip():
            try:
                mtid = int(manual_task_id)
            except ValueError:
                error = f"manual_task_id {manual_task_id!r} is not a number"

        ctx = {"request": request}
        if error is not None:
            ctx["error"] = error
            return templates.TemplateResponse(request, "_result.html", ctx, status_code=422)

        with _conn() as conn:
            # No `RETURNING id` here: migration 014 deliberately grants
            # `dentalia_api` INSERT-only on upload_inbox (it must never read
            # the spool back), and Postgres requires SELECT privilege on any
            # column named in RETURNING. `currval()` needs only the already-
            # granted sequence USAGE privilege and reflects the nextval() this
            # INSERT's default just consumed, in the same session.
            conn.execute(
                "INSERT INTO upload_inbox (filename, content, target_group_id, catalogue, uploaded_by) "
                "VALUES (%s,%s,%s,%s,%s)",
                (file.filename, content, gid, catalogue, _authenticated_user(request)),
            )
            uid = conn.execute("SELECT currval('upload_inbox_id_seq') AS id").fetchone()["id"]
            dedupe_key = f"upload:{uid}"
            payload = {"upload_id": uid}
            if mtid is not None:
                payload["manual_task_id"] = mtid
            jid = queue.enqueue(conn, "upload.ingest", payload, dedupe_key, priority=priority)
            conn.commit()

        ctx["job_id"] = jid
        ctx["dedupe_key"] = dedupe_key
        ctx["receipt"] = words.receipt(
            "Document received. The system reads it and it appears in Review "
            "within a few minutes, or on the item straight away if everything "
            "on it is clear.", jid)
        return templates.TemplateResponse(request, "_result.html", ctx)

    # ----------------------------------------------------------------------- #
    # /import — a BC export handed to us through the browser rather than
    # dropped under /imports (which is mounted read-only in both web and
    # worker). Two jobs, never one: a preview that writes nothing, then an
    # apply the operator confirms.
    #
    # NO catalogue picker. There is one Business Central, one article
    # numbering — the Zagreb operation adds items, not a second integration
    # (Denis, 2026-08-19, closing PHASES.md G17/G11). This route never offered
    # one, and since 2026-08-26 neither does /ingest or /upload: all three take
    # the catalogue from `scheduler.ingest_catalogue`. The column is kept and
    # NOT NULL, so a value is still written -- it is just not a question.
    # ----------------------------------------------------------------------- #
    @app.get("/import", response_class=HTMLResponse)
    def import_form(request: Request):
        with _conn() as conn:
            runs = _recent_runs(conn)
        for r in runs:
            r["result_pretty"] = json.dumps(r["result"], indent=2, ensure_ascii=False)
            # What the run WAS. The tag itself stays on the page, under the
            # run's Technical details -- this is the line a person scans.
            r["what"] = RUN_WORDS.get(r["type"], RUN_WORD_OTHER)
        return templates.TemplateResponse(request, "import.html", {
            "request": request,
            "priorities": PRIORITIES,
            "catalogue": scheduler_cfg.ingest_catalogue,
            "suffixes": ", ".join(IMPORT_SUFFIXES),
            "max_mb": cfg.upload_max_mb,
            "runs": runs,
        })

    @app.post("/import", response_class=HTMLResponse)
    def import_submit(
        request: Request,
        file: UploadFile = File(...),
        priority: str = Form("interactive"),
    ):
        error = None
        if priority not in PRIORITIES:
            error = f"unknown priority {priority!r}"

        filename = (file.filename or "").strip()
        content = file.file.read()
        if error is None:
            if pathlib.Path(filename).suffix.lower() not in IMPORT_SUFFIXES:
                error = (f"only BC export files are accepted "
                         f"({', '.join(IMPORT_SUFFIXES)}) — got {filename!r}")
            elif len(content) > cfg.upload_max_mb * 1024 * 1024:
                error = f"file exceeds the {cfg.upload_max_mb} MB limit"

        ctx = {"request": request}
        if error is not None:
            ctx["error"] = error
            return templates.TemplateResponse(request, "_result.html", ctx, status_code=422)

        catalogue = scheduler_cfg.ingest_catalogue
        with _conn() as conn:
            # No `RETURNING id`: migration 040 grants dentalia_api INSERT only
            # (it must never read the spool back), and Postgres requires SELECT
            # on any column named in RETURNING. `currval()` needs only the
            # already-granted sequence USAGE and reflects this INSERT's nextval.
            conn.execute(
                "INSERT INTO import_inbox (kind, filename, content, catalogue, uploaded_by) "
                "VALUES ('items', %s, %s, %s, %s)",
                (filename, content, catalogue, _authenticated_user(request)),
            )
            uid = conn.execute("SELECT currval('import_inbox_id_seq') AS id").fetchone()["id"]
            dedupe_key = f"ingest.run:upload:{uid}:preview"
            jid = queue.enqueue(
                conn, "ingest.run",
                {"source": "upload", "ref": {"upload_id": uid},
                 "catalogue": catalogue, "dry_run": True, "filename": filename},
                dedupe_key, priority=priority,
            )
            conn.commit()

        ctx["job_id"] = jid
        ctx["dedupe_key"] = dedupe_key
        ctx["receipt"] = words.receipt(
            "File received. It is being read; nothing is written until you "
            "see what would change and confirm it.", jid)
        # `queue.enqueue` returns None when an active job already holds the key
        # (C2). Only link onward to a preview that actually exists — otherwise
        # this renders `/import/None/preview`.
        if jid is not None:
            ctx["preview_url"] = f"/import/{jid}/preview"
        return templates.TemplateResponse(request, "_result.html", ctx)

    @app.post("/import/vendors", response_class=HTMLResponse)
    def vendor_import_submit(
        request: Request,
        file: UploadFile = File(...),
        priority: str = Form("interactive"),
    ):
        """The manufacturer master (`Proizvajalci.xlsx`), the sibling of the
        item export above. Same spool, same two-phase preview-then-apply, a
        different queue tag: a vendor import writes `vendor_master`, its diff
        has four categories rather than two, and one of them is refused unless
        the operator opts in."""
        error = None
        if priority not in PRIORITIES:
            error = f"unknown priority {priority!r}"

        filename = (file.filename or "").strip()
        content = file.file.read()
        if error is None:
            if pathlib.Path(filename).suffix.lower() not in IMPORT_SUFFIXES:
                error = (f"only BC export files are accepted "
                         f"({', '.join(IMPORT_SUFFIXES)}) — got {filename!r}")
            elif len(content) > cfg.upload_max_mb * 1024 * 1024:
                error = f"file exceeds the {cfg.upload_max_mb} MB limit"

        ctx = {"request": request}
        if error is not None:
            ctx["error"] = error
            return templates.TemplateResponse(request, "_result.html", ctx,
                                              status_code=422)

        with _conn() as conn:
            # No `RETURNING id`, same as the items route: migration 040 grants
            # dentalia_api INSERT only, and Postgres requires SELECT on any
            # column named in RETURNING. `currval()` needs only the
            # already-granted sequence USAGE.
            conn.execute(
                "INSERT INTO import_inbox (kind, filename, content, uploaded_by) "
                "VALUES ('vendors', %s, %s, %s)",
                (filename, content, _authenticated_user(request)),
            )
            uid = conn.execute(
                "SELECT currval('import_inbox_id_seq') AS id").fetchone()["id"]
            dedupe_key = f"vendor.import:upload:{uid}:preview"
            # Flat payload, not nested under `ref`: that shape belongs to
            # `ingest.run`. `allow_renames` rides from the start at false so
            # the field's absence never has to mean anything.
            jid = queue.enqueue(
                conn, "vendor.import",
                {"upload_id": uid, "filename": filename,
                 "dry_run": True, "allow_renames": False},
                dedupe_key, priority=priority,
            )
            conn.commit()

        ctx["job_id"] = jid
        ctx["dedupe_key"] = dedupe_key
        ctx["receipt"] = words.receipt(
            "Manufacturer list received. It is being read; nothing is written "
            "until you see what would change and confirm it.", jid)
        # `queue.enqueue` returns None when an active job already holds the key
        # (C2). Only link onward to a preview that exists.
        if jid is not None:
            ctx["preview_url"] = f"/import/{jid}/preview"
        return templates.TemplateResponse(request, "_result.html", ctx)

    def _is_appliable_preview(job: dict) -> bool:
        """Only a FINISHED upload DRY RUN can be applied. A running preview has
        no diff yet, a csv/odata run was never a preview, and a job whose
        payload already says dry_run=false is the apply itself.

        The upload-sourced test differs by tag and cannot be one expression:
        `ingest.run` also serves csv and bc_odata runs, so it must prove
        `source == "upload"`; `vendor.import` exists only for uploads and its
        payload carries no `source` at all, so demanding one would make every
        vendor preview unappliable."""
        payload = job["payload"] or {}
        if job["status"] != "done" or payload.get("dry_run") is not True:
            return False
        if job["type"] == "vendor.import":
            return True
        return bool(job["type"] == "ingest.run"
                    and payload.get("source") == "upload")

    @app.get("/import/{job_id:int}/preview", response_class=HTMLResponse)
    def import_preview(request: Request, job_id: int):
        with _conn() as conn:
            job = _job_by_id(conn, job_id)
            # An APPLY run carries the id of the preview it was confirmed
            # from, so this page can put the two count sets side by side.
            # Applying re-reads the export and re-diffs against the catalogue
            # as it is NOW, so the numbers are not guaranteed to match — and a
            # mismatch is the one thing worth surfacing, not smoothing over.
            preview_result = None
            if job is not None:
                prev_id = (job["payload"] or {}).get("preview_job_id")
                if prev_id is not None and not (job["payload"] or {}).get("dry_run"):
                    # Jobs are regenerable state and get pruned; a dangling
                    # back-reference degrades to the single-column report.
                    prev = _job_by_id(conn, prev_id)
                    if prev is not None:
                        preview_result = prev["result"] or {}
        if job is None:
            raise HTTPException(status_code=404, detail=f"job {job_id} not found")
        result = job["result"] or {}
        # Absent counter -> "" and not None: a report predating a counter (or
        # one a future handler stops emitting) must render as a blank cell, not
        # the literal string "None". `.get(key, "")` and not a falsy default,
        # because 0 is a real count and must still print as 0. A list-valued
        # field (`renamed`, `disappeared`) counts as its length — the cell says
        # how many, the blocks below say which.
        def _count(report, key):
            v = report.get(key, "")
            return len(v) if isinstance(v, (list, tuple)) else v

        counts = [
            {"label": label,
             "previewed": None if preview_result is None else _count(preview_result, key),
             "applied": _count(result, key),
             "differs": (preview_result is not None
                         and _count(preview_result, key) != _count(result, key))}
            for label, key in COUNT_ROWS_BY_TYPE.get(job["type"], IMPORT_COUNT_ROWS)
        ]
        # A bare fragment for htmx's own poll and Apply; a full page for a human
        # who bookmarked, reloaded, or middle-clicked the link `_result.html`
        # hands out. The partial cannot stand alone: htmx is loaded by
        # base.html, so served on its own it renders hx-* markup that nothing
        # interprets and the Apply button silently does nothing -- measured
        # against the running app 2026-08-26, the click enqueued no job.
        # One URL, two report shapes. A vendor run has four diff categories
        # and a rename opt-in; an item run has counts and a plain Apply.
        partial = ("_vendor_preview.html" if job["type"] == "vendor.import"
                   else "_import_preview.html")
        ctx = {
            "request": request,
            "job": job,
            "result": result,
            "result_pretty": json.dumps(result, indent=2, ensure_ascii=False),
            "appliable": _is_appliable_preview(job),
            "counts": counts,
            "compared": preview_result is not None,
            "partial": partial,
        }
        if request.headers.get("HX-Request"):
            return templates.TemplateResponse(request, partial, ctx)
        # A full page for a human who bookmarked, reloaded, or middle-clicked
        # the link `_result.html` hands out. The partial cannot stand alone:
        # htmx is loaded by base.html, so served on its own it renders hx-*
        # markup that nothing interprets and the Apply button silently does
        # nothing -- measured against the running app 2026-08-26, the click
        # enqueued no job. The wrapper includes whichever partial applies.
        return templates.TemplateResponse(request, "import_preview_page.html", ctx)

    @app.post("/import/{job_id:int}/apply", response_class=HTMLResponse)
    def import_apply(request: Request, job_id: int,
                     allow_renames: bool = Form(False)):
        with _conn() as conn:
            job = _job_by_id(conn, job_id)
            if job is None:
                raise HTTPException(status_code=404, detail=f"job {job_id} not found")
            if not _is_appliable_preview(job):
                return templates.TemplateResponse(
                    request, "_result.html",
                    {"request": request,
                     "error": f"job {job_id} is not a finished import preview "
                              f"(type={job['type']}, status={job['status']})"},
                    status_code=422)

            # A NEW payload, never the preview's mutated (invariant 9). The
            # operator's confirmation is what this second job carries — along
            # with a back-reference to the preview it confirmed, which is what
            # lets the confirm screen report previewed against applied. Neither
            # handler reads that field; only the web does.
            #
            # `allow_renames` is added for `vendor.import` ONLY. `ingest.run`
            # has no meaning for it, and payload fields are additive per tag,
            # not sprayed across every tag this route happens to serve.
            payload = {**job["payload"], "dry_run": False,
                       "preview_job_id": job_id}
            if job["type"] == "vendor.import":
                uid = job["payload"]["upload_id"]
                payload["allow_renames"] = bool(allow_renames)
            else:
                uid = job["payload"]["ref"]["upload_id"]
            dedupe_key = f"{job['type']}:upload:{uid}:apply"
            jid = queue.enqueue(
                conn, job["type"], payload, dedupe_key, priority="interactive")
            conn.commit()

        ctx = {"request": request, "job_id": jid, "dedupe_key": dedupe_key,
               "receipt": words.receipt(
                   "Import queued. The changes you just saw are written within "
                   "a minute.", jid)}
        if jid is not None:      # None means deduped — a double-clicked Apply
            ctx["preview_url"] = f"/import/{jid}/preview"
            # The write is already enqueued by the time this renders, so the
            # onward link reads as what it is, not as the preview's "would".
            ctx["preview_link_text"] = "See what this wrote"
        return templates.TemplateResponse(request, "_result.html", ctx)

    # ----------------------------------------------------------------------- #
    # /staging — the review queue. Split into a shell plus four partials
    # because the sections it renders grow without bound: the one-page version
    # of this board was 7.8 MiB of HTML (5,831 grouping suggestions, 5,888
    # forms) and no browser would open it. The shell renders page 1 of each
    # section as collapsed summary rows; paging and expanding are HTMX GETs
    # against the partials below, so the page never holds more than
    # STAGING_PAGE_SIZE rows plus whatever the reviewer opened.
    #
    # Route shapes do not collide: the list partials are two path segments,
    # /staging/{doc_id}/detail is three, /staging/suggestion/{id}/detail is
    # four. The `:int` converter on doc_id 404s a non-numeric id cleanly (same
    # reasoning as /documents/{doc_id:int}).
    #
    # All five are GETs. Nothing here writes — the POST routes that enqueue
    # gate.apply / resolve.group are unchanged below (invariant 1).
    # ----------------------------------------------------------------------- #
    def _docs_ctx(request: Request, page: int, focus_doc: int | None = None,
                  q: str = "", reason: str = "") -> dict:
        # One `today` for the Expired chip's filter and the rows' expired
        # badges, so a row the chip found always shows why.
        today = datetime.now(timezone.utc).date()
        reason = _review_chip(reason)
        with _conn() as conn:
            rows, total = _staged_docs_page(conn, page=page, q=q, reason=reason,
                                            today=today)
            # The heading counts the whole queue; the pager counts what the
            # search and the chip left, so a filter never reads as the queue
            # having shrunk.
            queue_total = conn.execute(
                "SELECT count(*) AS n FROM document WHERE status='staged'"
            ).fetchone()["n"]
        pager_params = ("reason=" + reason) if reason else ""
        return {"request": request, "docs": rows, "focus_doc": focus_doc,
                "focus_missing_doc": None, "docs_q": q, "docs_reason": reason,
                "reason_chips": REVIEW_REASON_CHIPS, "queue_total": queue_total,
                "today": today, "docs_pager_params": pager_params,
                "docs_pager": _pager(page, STAGING_PAGE_SIZE, total)}

    def _suggestions_ctx(request: Request, page: int, q: str) -> dict:
        with _conn() as conn:
            rows, total = _open_suggestions_page(conn, page=page, q=q)
        return {"request": request, "suggestions": rows, "q": q,
                "sugg_pager": _pager(page, STAGING_PAGE_SIZE, total)}

    @app.get("/staging", response_class=HTMLResponse)
    def staging_board(request: Request, doc: int | None = None, q: str = "",
                      reason: str = ""):
        """`?doc=N` is the deep link every other screen uses to hand a reviewer
        one specific row (`/documents/N`, the manual queue). The anchor alone
        cannot do it: the board renders one page of 50, grouped by
        manufacturer, so `#doc-N` resolves to nothing for every document past
        the first page. Resolve the page here, and let the template open that
        row. A deep link opens the unfiltered list, the order `_staged_doc_page`
        numbers; `reason` and `q` apply only without one."""
        page, focus_doc, focus_missing = 0, None, False
        if doc is not None:
            q, reason = "", ""
            with _conn() as conn:
                found = _staged_doc_page(conn, doc)
            if found is None:
                focus_missing = True
            else:
                page, focus_doc = found, doc
        with _conn() as conn:
            staged_links, staged_links_total = _staged_links_on_production(conn)
            dead_links, dead_links_total = _staged_links_on_unpublished(conn)
        ctx = {
            **_docs_ctx(request, page, focus_doc, q, reason),
            **_suggestions_ctx(request, 0, ""),
            "focus_missing_doc": doc if focus_missing else None,
            "dead_links": dead_links,
            "dead_links_total": dead_links_total,
            "staged_links": staged_links,
            "staged_links_total": staged_links_total,
            "staged_links_limit": STAGED_LINKS_LIMIT,
        }
        return templates.TemplateResponse(request, "staging.html", ctx)

    @app.get("/staging/docs", response_class=HTMLResponse)
    def staging_docs(request: Request, page: int = Query(0, ge=0, le=MAX_PAGE),
                     q: str = "", reason: str = ""):
        """One page of staged-document summary rows (HTMX target: the section
        container, so prev/next swaps the list without reloading the board).
        `q` searches the same fields as /documents (`_DOC_SEARCH`); `reason` is
        a reason chip (`REVIEW_REASON_CHIPS`), and anything else is all
        reasons."""
        return templates.TemplateResponse(
            request, "_staging_docs.html", _docs_ctx(request, page, None, q, reason)
        )

    @app.get("/staging/suggestions", response_class=HTMLResponse)
    def staging_suggestions(request: Request,
                            page: int = Query(0, ge=0, le=MAX_PAGE), q: str = ""):
        """One page of open grouping-suggestion summary rows, optionally
        filtered by item_ref. Both the search box and the pager target this."""
        return templates.TemplateResponse(
            request, "_staging_suggestions.html", _suggestions_ctx(request, page, q)
        )

    @app.get("/staging/{doc_id:int}/detail", response_class=HTMLResponse)
    def staging_doc_detail(request: Request, doc_id: int):
        """Evidence, item links and the decision form for one staged document
        — fetched when the reviewer expands its row."""
        with _conn() as conn:
            row = _staged_doc_detail(conn, doc_id)
        if row is None:
            return templates.TemplateResponse(
                request, "_result.html",
                {"request": request, "error": f"document #{doc_id} is no longer staged"},
                status_code=404,
            )
        if row["task"] and (row["task"]["payload"] or {}).get("tier_attempts"):
            row["tier_attempts_pretty"] = json.dumps(
                row["task"]["payload"]["tier_attempts"], indent=2, ensure_ascii=False
            )
        return templates.TemplateResponse(
            request, "_staging_doc_detail.html",
            {"request": request, "row": row,
             "type_choices": TYPE_CHOICES, "regulation_choices": REGULATION_CHOICES,
             "reject_reasons": REJECT_REASONS, "reject_note_max": REJECT_NOTE_MAX,
             "items_shown": REVIEW_ITEMS_SHOWN,
             "today": datetime.now(timezone.utc).date()},
        )

    @app.get("/staging/suggestion/{suggestion_id:int}/detail", response_class=HTMLResponse)
    def staging_suggestion_detail(request: Request, suggestion_id: int):
        """Candidate groups, T1 rationale and the assign form for one open
        grouping suggestion — fetched when the reviewer expands its row."""
        with _conn() as conn:
            s = _suggestion_detail(conn, suggestion_id)
        if s is None:
            return templates.TemplateResponse(
                request, "_result.html",
                {"request": request, "error": f"suggestion #{suggestion_id} is no longer open"},
                status_code=404,
            )
        s["suggestion_pretty"] = json.dumps(s["suggestion"], indent=2, ensure_ascii=False)
        return templates.TemplateResponse(
            request, "_staging_suggestion_detail.html", {"request": request, "s": s}
        )

    @app.post("/staging/suggestion/{suggestion_id}/assign", response_class=HTMLResponse)
    def staging_suggestion_assign(
        request: Request,
        suggestion_id: int,
        action: str = Form(...),
        group_id: str = Form(""),
    ):
        # Resolution loop, spec resolve.md §5: "assign" honors the human's chosen
        # candidate group_id; "new" is the other half — none of the candidates
        # fit, start a fresh singleton group (basis `manual` either way, human
        # is the authority). Both enqueue resolve.group, never a direct write.
        ctx = {"request": request}
        if action not in ("assign", "new"):
            ctx["error"] = f"unknown action {action!r}"
            return templates.TemplateResponse(request, "_result.html", ctx, status_code=422)

        with _conn() as conn:
            row = conn.execute(
                "SELECT item_ref FROM grouping_suggestion WHERE id=%s AND status='open'",
                (suggestion_id,),
            ).fetchone()
            if row is None:
                ctx["error"] = f"no open grouping suggestion #{suggestion_id}"
                return templates.TemplateResponse(request, "_result.html", ctx, status_code=422)
            item_ref = row["item_ref"]

            if action == "assign":
                if not group_id.strip().isdigit():
                    ctx["error"] = f"group_id must be an integer, got {group_id!r}"
                    return templates.TemplateResponse(request, "_result.html", ctx, status_code=422)
                gid = int(group_id.strip())
                payload = {"item_ref": item_ref, "group_id": gid}
                dedupe_key = f"resolve:manual:{item_ref}:{gid}"
            else:
                payload = {"item_ref": item_ref, "force_new_group": True}
                dedupe_key = f"resolve:manual:{item_ref}:new"

            jid = queue.enqueue(conn, "resolve.group", payload, dedupe_key, priority="interactive")
            conn.commit()

        ctx["job_id"] = jid
        ctx["dedupe_key"] = dedupe_key
        ctx["receipt"] = words.receipt(
            "Grouping recorded. The item joins the group within a minute and "
            "the documents held for it follow.", jid)
        return templates.TemplateResponse(request, "_result.html", ctx)

    @app.post("/staging/{doc_id}/apply", response_class=HTMLResponse)
    def staging_apply(
        request: Request,
        doc_id: int,
        decision: str = Form(...),
        decided_by: str = Form(""),
        edits: str = Form(""),
        group_id: str = Form(""),
        manufacturer: str = Form(""),
        # Structured replacements for the old raw-JSON `edits` textarea: the
        # review form now posts ordinary inputs and this route assembles the
        # dict. `edit_*` names are prefixed so they cannot collide with the
        # decision fields above, and `edit_baseline` carries what the form was
        # rendered with, so only fields the reviewer actually changed are sent.
        edit_type: str = Form(""),
        edit_regulation: str = Form(""),
        edit_validity_from: str = Form(""),
        edit_validity_to: str = Form(""),
        edit_cert_number: str = Form(""),
        edit_baseline: str = Form(""),
        # Office UI redesign P1a (spec § 3). `reason` is one of REJECT_REASONS
        # and required on a reject; `reason_note` is its optional free text.
        # `confirm` is the second step of a whole-range approval: without it a
        # bind-manufacturer writes nothing and answers with the count.
        reason: str = Form(""),
        reason_note: str = Form(""),
        confirm: str = Form(""),
    ):
        # Decisions per docs/dentalia-s0.3-ui-proposal.md (the real
        # handle_gate_apply contract) — "edit" is not a standalone decision;
        # edited fields ride along with "approve" as the optional `edits` dict.
        ctx = {"request": request}
        # `reopen` is the way back out of `rejected` and the only decision here
        # whose precondition is a status rather than a field. It is validated
        # in the handler, which raises when the document is not rejected --
        # a stale button must dead-letter visibly, not silently do nothing.
        if decision not in ("approve", "reject", "bind-manufacturer", "reopen"):
            ctx["error"] = f"unknown decision {decision!r}"
            return templates.TemplateResponse(request, "_result.html", ctx, status_code=422)
        # Reject asks why. Checked before anything else about the request so a
        # reason-less reject is refused whatever else it carries; the panel's
        # radios are `required`, so this is the backstop for any other client.
        note = None
        if decision == "reject":
            reason = reason.strip()
            if not reason:
                ctx["error"] = ("Choose a reason for rejecting this document. "
                                "Nothing was changed.")
                return templates.TemplateResponse(
                    request, "_result.html", ctx, status_code=422)
            if reason not in REJECT_REASONS:
                ctx["error"] = ("That reason is not on the list. Choose one of the "
                                "listed reasons. Nothing was changed.")
                return templates.TemplateResponse(
                    request, "_result.html", ctx, status_code=422)
            reason_note = reason_note.strip()
            if len(reason_note) > REJECT_NOTE_MAX:
                ctx["error"] = (f"The note is longer than {REJECT_NOTE_MAX} characters. "
                                "Shorten it and try again. Nothing was changed.")
                return templates.TemplateResponse(
                    request, "_result.html", ctx, status_code=422)
            note = f"{reason}: {reason_note}" if reason_note else reason
        # The raw form value, before the identity resolution below: the confirm
        # step posts it back and the second request resolves it again.
        decided_by_form = decided_by
        # G3 v0: when a proxy sits in front (compose sets require_authenticated_user),
        # the audit identity comes from the trusted header, never the form —
        # _authenticated_user raises 403 if the header is missing (bypassed the
        # proxy). Otherwise the form value still wins if one is supplied; with
        # no proxy and no form field (the simplified review page removed it) we
        # fall back to DEFAULT_DECIDED_BY. That constant is the single place to
        # change when real logins land.
        auth_user = _authenticated_user(request)
        decided_by = auth_user or decided_by.strip() or DEFAULT_DECIDED_BY

        payload = {"doc_id": doc_id, "decision": decision, "decided_by": decided_by}

        if edits.strip():
            try:
                edits_obj = json.loads(edits)
                if not isinstance(edits_obj, dict):
                    raise ValueError("edits must be a JSON object")
            except (json.JSONDecodeError, ValueError) as exc:
                ctx["error"] = f"invalid edits JSON: {exc}"
                return templates.TemplateResponse(request, "_result.html", ctx, status_code=422)
            payload["edits"] = edits_obj

        structured = _structured_edits(
            {"type": edit_type, "regulation": edit_regulation,
             "validity_from": edit_validity_from, "validity_to": edit_validity_to,
             "cert_number": edit_cert_number},
            edit_baseline,
        )
        if structured:
            # A raw-JSON `edits` body still wins where the two overlap: it is
            # the explicit, deliberate form and predates this one.
            payload["edits"] = {**structured, **payload.get("edits", {})}

        if decision == "reject" and group_id.strip():
            if not group_id.strip().isdigit():
                ctx["error"] = f"group_id must be an integer, got {group_id!r}"
                return templates.TemplateResponse(request, "_result.html", ctx, status_code=422)
            payload["group_id"] = int(group_id.strip())

        # Additive and optional (spec § 3): absent unless a reason was chosen,
        # so every consumer that predates it sees the payload it always saw.
        if note is not None:
            payload["note"] = note

        if decision == "bind-manufacturer":
            manufacturer = manufacturer.strip()
            if not manufacturer:
                ctx["error"] = "manufacturer is required for a bind-manufacturer decision"
                return templates.TemplateResponse(request, "_result.html", ctx, status_code=422)
            payload["manufacturer"] = manufacturer
            # Two-step on the server (spec § 3, P1a). A whole-range approval
            # links every medical-device item of a manufacturer, so the first
            # POST writes nothing and answers with the count; only the POST
            # carrying `confirm` enqueues. The confirm posts back exactly the
            # fields this request carried, so what is confirmed is what was
            # counted. It answers into the panel's own slot, never over the
            # row, so Cancel leaves the panel standing.
            if not confirm:
                with _conn() as conn:
                    n = _mfr_bind_item_count(conn, manufacturer)
                    doc = conn.execute(
                        "SELECT type FROM document WHERE doc_id=%s", (doc_id,)
                    ).fetchone()
                # A name folding to no BC code at all is the bind GATE refuses
                # ([gate-bind-zero-links]). Asking "Approve for all 0 X items?"
                # would put a question whose Yes can only dead-letter; refuse it
                # here instead (fix round 1, M4). A name with codes but no MD
                # items is different and is still asked: GATE records it.
                if n is None:
                    ctx["error"] = (
                        f"We hold no items from {manufacturer} in Business Central, "
                        "so this approval would reach nothing. Choose the "
                        "manufacturer from the list. Nothing was changed.")
                    return templates.TemplateResponse(
                        request, "_result.html", ctx, status_code=422)
                sent = {
                    "decided_by": decided_by_form, "edits": edits,
                    "edit_type": edit_type, "edit_regulation": edit_regulation,
                    "edit_validity_from": edit_validity_from,
                    "edit_validity_to": edit_validity_to,
                    "edit_cert_number": edit_cert_number,
                    "edit_baseline": edit_baseline,
                }
                ctx.update(
                    confirm_needed=_bind_confirm_lines(
                        manufacturer, n, doc["type"] if doc else None),
                    confirm_url=f"/staging/{doc_id}/apply",
                    # An int-built literal id, never user text: it is
                    # interpolated into the Cancel control's JS string.
                    confirm_target=f"bind-confirm-{doc_id}",
                    confirm_fields={
                        "decision": decision, "manufacturer": manufacturer,
                        **{k: v for k, v in sent.items() if v},
                    },
                    confirm_label=(f"Yes, approve for {words.num(n)} "
                                   f"{'item' if n == 1 else 'items'}"),
                )
                return templates.TemplateResponse(request, "_result.html", ctx)

        # Per the proposal's suggested key: one active gate.apply per (doc, decision)
        # regardless of who submits it — a second reviewer clicking the same
        # decision on the same doc should dedupe, not double-enqueue.
        dedupe_key = f"apply:{doc_id}:{decision}"
        with _conn() as conn:
            jid = queue.enqueue(conn, "gate.apply", payload, dedupe_key, priority="interactive")
            conn.commit()

        ctx["job_id"] = jid
        ctx["dedupe_key"] = dedupe_key
        # A receipt in words (spec § 9, P7a). The decision is RECORDED here,
        # not applied: `gate.apply` is what writes the registry, so the sentence
        # says what will happen rather than that it has.
        ctx["receipt"] = words.receipt(DECISION_RECEIPTS[decision], jid)
        response = templates.TemplateResponse(request, "_result.html", ctx)
        if decision == "bind-manufacturer":
            # The confirmed whole-range approval (only a POST with `confirm`
            # gets this far). Its form answers into the panel's slot, which
            # would leave the row live with Approve and Reject… still under
            # the receipt; replace the row instead, as Approve and Reject do,
            # so the queue shrinks (fix round 1, M2). `#doc-{id}` is the row
            # `_staging_docs.html` renders; Cancel never reaches the server.
            response.headers["HX-Retarget"] = f"#doc-{doc_id}"
            response.headers["HX-Reswap"] = "outerHTML"
        return response

    #: C17 link-level decisions the UI may submit. Kept as a literal beside the
    #: route (the pattern `staging_apply` already uses for its document
    #: decisions) rather than imported from `app.handlers.gate`: web/ is a
    #: producer and must not depend on handler internals, and an unknown value
    #: has to 422 here regardless of what the handler would do with it.
    LINK_DECISIONS = ("confirm-link", "reject-link", "reopen-link")

    @app.post("/links/{doc_id}/decide", response_class=HTMLResponse)
    def link_decide(
        request: Request,
        doc_id: int,
        item_ref: str = Form(...),
        decision: str = Form(...),
        decided_by: str = Form(""),
    ):
        """C17: one human ruling on ONE (doc_id, item_ref) link.

        `item_ref` arrives as a form field, not a path segment, deliberately:
        catalogue refs contain a literal `/` (see the note in
        item_detail.html), and a `{item_ref:path}` converter would put the
        decision before it in the path or force an encoding uvicorn undoes
        before routing anyway ([item-link-slash-404]).

        Producer only, like every other route here (inv. 1): this enqueues
        `gate.apply` and writes no registry row. The handler owns every
        precondition -- document must be production, link must be in the status
        the decision expects -- and raises where it is not, so a stale button on
        an already-decided row dead-letters visibly instead of silently doing
        nothing.
        """
        ctx = {"request": request}
        if decision not in LINK_DECISIONS:
            ctx["error"] = f"unknown link decision {decision!r}"
            return templates.TemplateResponse(request, "_result.html", ctx, status_code=422)
        item_ref = item_ref.strip()
        if not item_ref:
            ctx["error"] = "item_ref is required for a link decision"
            return templates.TemplateResponse(request, "_result.html", ctx, status_code=422)

        auth_user = _authenticated_user(request)
        decided_by = auth_user or decided_by.strip() or DEFAULT_DECIDED_BY
        payload = {"doc_id": doc_id, "item_ref": item_ref,
                   "decision": decision, "decided_by": decided_by}
        # Scoped to the link, not just the document: two different items under
        # one document are two different decisions and must not dedupe onto each
        # other. Same shape as `apply:{doc_id}:{decision}` otherwise, so a
        # double-click still collapses to one job.
        dedupe_key = f"apply:{doc_id}:{item_ref}:{decision}"
        with _conn() as conn:
            jid = queue.enqueue(conn, "gate.apply", payload, dedupe_key, priority="interactive")
            conn.commit()

        ctx["job_id"] = jid
        ctx["dedupe_key"] = dedupe_key
        ctx["receipt"] = words.receipt(LINK_RECEIPTS[decision], jid)
        return templates.TemplateResponse(request, "_result.html", ctx)

    @app.get("/manual", response_class=HTMLResponse)
    def manual_board(request: Request, q: str = ""):
        with _conn() as conn:
            tasks = _manual_tasks(conn, q)
            _describe_manual_docs(conn, tasks)
        for t in tasks:
            t["payload_pretty"] = json.dumps(t["payload"], indent=2, ensure_ascii=False)
        ctx = {"request": request, "tasks": tasks, "q": q}
        return templates.TemplateResponse(request, "manual.html", ctx)

    @app.get("/missing", response_class=HTMLResponse)
    def missing_board(request: Request, manufacturer: str = "", page: int = 0):
        """Missing documents (office UI redesign spec § 5): the open
        `discovery-dead-end` tasks, oldest first, 20 to a page, filterable by
        manufacturer. The office's half of /manual, which stays as it is for
        operators. Neither `_manual_tasks` nor `_describe_manual_docs` fits
        here: the first has no kind filter or paging and searches documents,
        the second describes tasks that carry a document, and a dead end is
        about an item group. The queries and the words live in
        web/missing.py."""
        with _conn() as conn:
            view = missing.missing_view(conn, manufacturer=manufacturer.strip(), page=page)
        ctx = {"request": request, **view,
               "pager": _pager(view["page"], missing.PAGE_SIZE, view["total"])}
        return templates.TemplateResponse(request, "missing.html", ctx)

    @app.post("/manual/{task_id}/resolve", response_class=HTMLResponse)
    def manual_task_resolve(request: Request, task_id: int):
        # Only discovery-dead-end has a resolve action here: it has no doc_id,
        # so gate.apply's resolve-by-doc_id (app/handlers/gate.py) can never
        # close it (migration 009's stated contract, unimplemented until now).
        # gate-manual resolves via the Staging tab (that's where the evidence
        # lives); dead-job-followup is raised by the dead-letter transition and
        # closed by the worker when the same work later succeeds, so it needs no
        # human resolve action here (both halves in app/queue.py).
        ctx = {"request": request}
        with _conn() as conn:
            task = conn.execute(
                "SELECT id, kind, group_id, status FROM manual_task WHERE id=%s", (task_id,)
            ).fetchone()
            if task is None:
                ctx["error"] = f"There is no task #{task_id}."
                return templates.TemplateResponse(request, "_result.html", ctx, status_code=422)
            if task["status"] != "open":
                # Someone uploaded a document for it, or a search found one,
                # since the page was drawn.
                ctx["error"] = (f"Task #{task_id} is already closed. "
                                "Reload the page to see the current list.")
                return templates.TemplateResponse(request, "_result.html", ctx, status_code=422)
            if task["kind"] != "discovery-dead-end":
                ctx["error"] = f"no resolve action for kind {task['kind']!r} here — use the Staging tab"
                return templates.TemplateResponse(request, "_result.html", ctx, status_code=422)
            if task["group_id"] is None:
                ctx["error"] = f"manual_task #{task_id} has no group_id"
                return templates.TemplateResponse(request, "_result.html", ctx, status_code=422)

            dedupe_key = f"discover:manual:{task_id}"
            jid = queue.enqueue(
                conn, "discover.group", {"group_id": task["group_id"]},
                dedupe_key, priority="interactive",
            )
            conn.commit()

        # A receipt in words (spec § 5, "Search again"), shared with /manual's
        # Resolve button, which posts here too. `None` is the dedupe: a search
        # for this task is already queued or running.
        #
        # `job_id` and `dedupe_key` travel WITH it: the Missing-documents slice
        # moved this receipt off the "Enqueued job #" line and dropped the
        # number entirely, and /manual is an operator screen where "which job
        # was that?" is a real question. `_result.html` files them under
        # "Technical details" (spec § 9, P7a).
        ctx["receipt"] = missing.search_again_receipt(jid is not None)
        ctx["job_id"] = jid
        ctx["dedupe_key"] = dedupe_key
        return templates.TemplateResponse(request, "_result.html", ctx)

    @app.get("/dead", response_class=HTMLResponse)
    def dead_board(request: Request):
        """The Failed split (office UI redesign spec § 4): websites that took
        too long, addresses that no longer work, and faults for the developer,
        in that order. Only what still needs someone is counted
        (web/failures.py); a dead job whose work is queued again shows as
        "trying again" or "searching again" instead.

        The developer section keeps the per-cause groups, one click down, and
        their re-run buttons render for operators only (D6). Office logins keep
        "Try the N again" and "Search again".
        """
        operator = access.is_operator(_authenticated_user(request), cfg.operator_users)
        with _conn() as conn:
            split = failures.failed_split(conn)
            developer_jobs = split[failures.DEVELOPER]["jobs"]
            groups = _dead_job_groups(conn, ids=[j["id"] for j in developer_jobs])
        jobs_by_id = {j["id"]: j for j in developer_jobs}
        for g in groups:
            # `job_ids` is capped per group, which is why the card's count
            # comes from the group query and not from len(jobs).
            g["jobs"] = [jobs_by_id[i] for i in g["job_ids"] if i in jobs_by_id]
        for section in split.values():
            for job in section["jobs"]:
                job["headline"] = _error_headline(job["last_error"])
        ctx = {"request": request, "split": split, "groups": groups, "operator": operator,
               "list_limit": DEFAULT_JOB_LIST_LIMIT}
        return templates.TemplateResponse(request, "dead.html", ctx)

    @app.post("/dead/retry-timeouts", response_class=HTMLResponse)
    def dead_retry_timeouts(request: Request):
        """Try every website timeout again ("Try the N again", spec § 4).

        Offered to every login (D6). The set is re-derived here from the same
        split the page counted, never taken from the form, so the button acts
        on what is still counted at click time. Each job is re-enqueued as
        `dead_rerun_group` does: same type, payload and dedupe key at
        interactive priority. A dead job never blocks the re-insert (invariant
        8), and the dead rows stay. The newer job, same key and same payload,
        is what takes the dead one out of the count. An ACTIVE job can still
        hold the key: for `fetch.url` that may be another group's fetch of the
        same address, so nothing is queued for this row and the receipt says so.
        """
        ctx = {"request": request}
        with _conn() as conn:
            section = failures.failed_split(conn)[failures.TIMEOUT]
            queued = 0
            for job in section["jobs"]:
                if queue.enqueue(conn, job["type"], job["payload"], job["dedupe_key"],
                                 priority="interactive") is not None:
                    queued += 1
            conn.commit()
        ctx["receipt"] = failures.retry_receipt(section, queued)
        return templates.TemplateResponse(request, "_result.html", ctx)

    @app.post("/dead/search-again", response_class=HTMLResponse)
    def dead_search_again(request: Request):
        """Search again behind every address that no longer works ("Search
        again for these N", spec § 4).

        Retrying a 404 fetches the same dead address, so this starts DISCOVER
        over instead: one `discover.group` per distinct item group among the
        counted dead addresses, re-derived here, at interactive priority. The
        dedupe key `discover:failed:{group_id}` makes a second click while the
        first search is still queued a no-op. A search created after the job
        died is what takes it out of the count; while it runs, the job shows
        as "searching again".
        """
        ctx = {"request": request}
        with _conn() as conn:
            section = failures.failed_split(conn)[failures.DEAD_ADDRESS]
            started = 0
            for group_id in section["group_ids"]:
                if queue.enqueue(conn, "discover.group", {"group_id": group_id},
                                 f"discover:failed:{group_id}",
                                 priority="interactive") is not None:
                    started += 1
            conn.commit()
        ctx["receipt"] = failures.search_receipt(section, started)
        return templates.TemplateResponse(request, "_result.html", ctx)

    # D6's other half (fix round 1, 2026-09-15). "Office logins get
    # 'took too long' only. Operators get everything" -- and `everything`
    # is these two. `/dead/retry-timeouts` and `/dead/search-again` stay
    # open to every login on purpose: Today posts to both, and clearing a
    # supplier timeout is the office's own work. What is refused here is
    # re-queueing work whose cause is still unfixed. `dead.html` has hidden
    # both controls from a non-operator since slice P5a; this is the half
    # that stops a typed URL doing what the hidden button would have.
    @app.post("/dead/rerun-group", response_class=HTMLResponse,
              dependencies=[Depends(require_operator)])
    def dead_rerun_group(request: Request, digest: str = Form(...)):
        """Re-run every dead job sharing one (type, error signature).

        The form posts only the group's digest. The membership is re-derived
        here, from the same grouping the board renders, rather than trusting a
        list of ids or a slab of error text from the client: the set can have
        grown between render and click (a sweep is still dying), a stale id list
        would silently re-run part of a cause and report success, and posted
        error text can be mangled in transit — a browser normalising line
        endings would match nothing and report the group as vanished.

        The grouping is over the developer section of the Failed split only
        (web/failures.py), exactly as the board renders it: a dead row whose
        work was already re-run is not in any group, so it is not re-run twice.

        Each re-enqueue goes through `queue.enqueue` exactly as the single-job
        button does — dead is terminal, so no dedupe key blocks the re-insert
        (invariant 8) — and the dead rows stay; a new job is queued beside them.
        """
        ctx = {"request": request}
        with _conn() as conn:
            developer_ids = [
                j["id"] for j in failures.failed_split(conn)[failures.DEVELOPER]["jobs"]
            ]
            group = next(
                (g for g in _dead_job_groups(conn, ids=developer_ids)
                 if g["digest"] == digest), None
            )
            if group is None:
                ctx["error"] = "no dead jobs match that group any more — reload the board"
                return templates.TemplateResponse(request, "_result.html", ctx, status_code=422)
            jobs = conn.execute(
                "SELECT id, type, payload, dedupe_key FROM job "
                "WHERE status='dead' AND id = ANY(%s::bigint[]) AND type=%s::job_type "
                "  AND left(coalesce(last_error, ''), %s) = %s "
                "ORDER BY id",
                (developer_ids, group["type"], _ERROR_SIGNATURE_CHARS, group["signature"]),
            ).fetchall()
            requeued = 0
            for job in jobs:
                if queue.enqueue(
                    conn, job["type"], job["payload"], job["dedupe_key"],
                    priority="interactive",
                ) is not None:
                    requeued += 1
            conn.commit()
        # A bulk re-run has one job per dead row, so there is no single id or
        # dedupe key to file under Technical details -- the count IS the
        # receipt, and it says what happens next rather than claiming a fix.
        # (An operator-only control, so "job" is the right word here; the
        # priority is machinery and left off the sentence.)
        ctx["receipt"] = (
            f"Re-queued {requeued} job{'' if requeued == 1 else 's'}. "
            "The failed rows stay until the new work succeeds."
        )
        return templates.TemplateResponse(request, "_result.html", ctx)

    @app.post("/dead/{job_id}/rerun", response_class=HTMLResponse,
              dependencies=[Depends(require_operator)])
    def dead_rerun(request: Request, job_id: int):
        ctx = {"request": request}
        with _conn() as conn:
            job = _job_by_id(conn, job_id)
            if job is None or job["status"] != "dead":
                ctx["error"] = f"job {job_id} is not a dead job"
                return templates.TemplateResponse(request, "_result.html", ctx, status_code=422)
            # dead is terminal -> the dedupe key never blocks this re-insert (invariant 8)
            jid = queue.enqueue(
                conn, job["type"], job["payload"], job["dedupe_key"], priority="interactive"
            )
            # The `dead-job-followup` alarm this job raised is NOT cleared here.
            # Two reasons, both deliberate: the web role holds no write grant on
            # manual_task (the producer boundary is enforced in the grant matrix,
            # not by convention), and re-queueing is not success -- a job that
            # dies again would have had its alarm cleared by the attempt. The
            # worker closes it on `finish` instead (app/queue.py), so the board
            # empties only when the work actually completes.
            conn.commit()
        ctx["job_id"] = jid
        ctx["dedupe_key"] = job["dedupe_key"]
        ctx["receipt"] = words.receipt(
            "Queued to run again. The failed row stays until the new attempt "
            "succeeds.", jid)
        return templates.TemplateResponse(request, "_result.html", ctx)

    @app.get("/api-reference", response_class=HTMLResponse)
    def api_reference(request: Request):
        """What this app exposes, to whom, with which credential, and what it
        actually returns.

        `/docs` and `/openapi.json` exist (FastAPI generates them) but answer
        "what are the parameters", not "which of these may the webshop call,
        what does it authenticate with, and what comes back". This page answers
        the second, and links to the first.

        Every response example here is RENDERED FROM THE REGISTRY at request
        time, by calling the same `item_documents()` the endpoints call -- not
        hand-written. A hand-written example is the thing that goes stale
        silently the next time a field changes, and a documented response that
        no longer matches the real one is worse than no example at all.

        `/api/kpi` is the one deliberate exception: `_kpi_board` measures ~478ms
        a render (`_kpi_coverage` alone ~294ms, see base.html), and this page is
        not worth half a second. It gets its shape described and a try-it link.
        """
        ctx = {
            "example_ref": None, "example_full": None,
            "example_customer": None, "example_doc_id": None,
            "example_manufacturer": None,
            "default_per_page": catalogue.DEFAULT_PER_PAGE,
            "max_per_page": catalogue.MAX_PER_PAGE,
            "max_batch_refs": catalogue.MAX_BATCH_REFS,
        }
        with _conn() as conn:
            row = conn.execute(
                "SELECT item_ref, doc_id FROM item_document_production "
                "ORDER BY item_ref LIMIT 1"
            ).fetchone()
            if row is not None:
                ctx["example_ref"] = row["item_ref"]
                ctx["example_doc_id"] = row["doc_id"]
                full = item_documents(conn, row["item_ref"],
                                      base_url=cfg.public_base_url)
                customer = item_documents(conn, row["item_ref"], view="customer",
                                          base_url=cfg.public_base_url)
                # default=str: dates are date objects, not JSON scalars.
                ctx["example_manufacturer"] = full.get("manufacturer")
                ctx["example_full"] = json.dumps(full, indent=2, default=str)
                ctx["example_customer"] = json.dumps(customer, indent=2, default=str)
        return templates.TemplateResponse(request, "api_reference.html", ctx)


    # ----------------------------------------------------------------------- #
    # JSON read API (S1.6) — production-visibility only (invariant "visibility
    # rule for ALL consumers"); staged data stays UI-only. Auth is the Caddy
    # reverse proxy in front of the whole app (G3 v0, docker-compose.yml) —
    # these routes carry no auth logic of their own.
    # ----------------------------------------------------------------------- #
    # Same converter gap as the HTML route: without `:path` every item_ref
    # containing a slash 404s here too, and the API would disagree with the
    # page about which items exist. The trailing `/documents` literal still
    # anchors the match, so the greedy `.*` backtracks to it.
    # Explicit column list, not `SELECT *`: a view drift can no longer silently
    # change what a consumer receives. `valid_to` is the EFFECTIVE expiry
    # (`expires`, migration 034) -- the one behavioral change a consumer can
    # observe versus the old raw-`validity_to` response (docs/specs/read-api.md).
    @app.get("/api/items/{item_ref:path}/documents")
    def api_item_documents(item_ref: str, view: str = "full",
                           include_superseded: bool = False):
        """Production documents for one item, plus the item's own identity.

        `view=customer` narrows the response to what the webshop may render to
        a clinic (spec §6). `include_superseded=true` widens the ROW SET to the
        documents this item's paperwork replaced (migration 070). Both default
        to today's behaviour, so this stays additive per
        `docs/specs/read-api.md` §5.

        The two are refused together, and `item_docs` owns that rule -- the
        route only turns its ValueError into the status code, so the batch
        endpoint in `web/catalogue.py` cannot decide the question differently.
        """
        with _conn() as conn:
            try:
                return item_documents(
                    conn, item_ref, view=view, base_url=cfg.public_base_url,
                    include_superseded=include_superseded,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/documents/{doc_id}")
    def api_document(doc_id: int):
        with _conn() as conn:
            # production + superseded, matching `item_document_history` (070).
            # A superseded document is reachable from the item endpoint under
            # `?include_superseded`, and a doc_id that list hands back must be
            # fetchable -- a payload citing ids that 404 is worse than no
            # history at all. `staged` / `rejected` / `filed` stay invisible:
            # they are not, and never were, a gated consumer-visible result.
            doc = conn.execute(
                "SELECT * FROM document WHERE doc_id=%s "
                "AND status IN ('production', 'superseded')", (doc_id,)
            ).fetchone()
            if doc is None:
                raise HTTPException(status_code=404, detail=f"production document {doc_id} not found")
            evidence = conn.execute(
                "SELECT field, value, tier, model_id, confidence, verbatim, page, archive_url, extracted_at "
                "FROM evidence WHERE doc_id=%s ORDER BY field",
                (doc_id,),
            ).fetchall()
        return {"document": doc, "evidence": evidence}

    @app.get("/api/kpi")
    def api_kpi():
        with _conn() as conn:
            return _kpi_board(conn, budget_cfg)

    return app


# Module-level instance for `python -m web.app` / `uvicorn web.app:app`.
app = create_app()


def _use_database_playbooks(cfg) -> None:
    """Read playbooks from rows rather than the bind-mounted directory.

    Over the `dentalia_api` role like every other read this process makes --
    049 grants it SELECT on the three identity tables, 050 on `playbook_epoch`.
    The directory it replaces was never in the image: `Dockerfile.web` copies
    only `app/` and `web/`, so `/app/playbooks` existed solely because of the
    compose mount, and the web view of playbook coverage could silently differ
    from the worker's.

    Called from `__main__`, NOT from `create_app` and not at import. It sets
    process-global state, and `create_app` is a factory the tests call once per
    fixture -- wiring it there repointed the loader for every test that ran
    afterwards. Import-time has the same problem: the test suite imports this
    module. `python -m web.app` is the container's actual CMD, which makes this
    the counterpart of `main()` in the worker and the CLI.
    """
    playbooks.set_source(
        lambda: db.connect(cfg.api_database_url, autocommit=True))


if __name__ == "__main__":
    import uvicorn

    cfg = app.state.web_cfg
    _use_database_playbooks(cfg)
    uvicorn.run(app, host=cfg.host, port=cfg.port)
