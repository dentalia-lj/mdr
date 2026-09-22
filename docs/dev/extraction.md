# Extraction, as built: T0 → T1 → T2 → T3

**Status:** live. Measured against the tree at 2026-08-31.

Covers `app/extract/` and the parts of `app/handlers/extract.py` and
`app/handlers/gate.py` that finish what `extract/` starts. Does not restate
the tier ladder's *rationale* (`dentalia-job-type-handbook.md` §5,
`dentalia-mdr-pipeline-ground-truth.md` §7) — this is what the code actually
does, and where the surprises are.

## Idea

Four tiers read one document's fields, cheapest first, escalating per field
rather than per document: T0 is free deterministic parsing (regex, keyword
tables, pdfplumber tables, manufacturer-authored layout templates), T1/T2 are
Anthropic structured-output calls (text, then vision) gated by confidence and
scan detection, and T3 is a human correction made through `gate.apply` — it
never touches `app/extract/` at all. `extract.doc` (`app/handlers/extract.py`)
is the handler that drives the ladder and writes its two audit tables
(`extraction_attempt`, `extraction_cost`); GATE is what turns the ladder's
output into `evidence` rows, and constructs several fields of those rows
itself.

## Where

| Module | Lines | Owns |
|---|---|---|
| `app/extract/target.py` | 35 | `TARGET` — the 11-field schema, alone, importing nothing (see Gotchas) |
| `app/extract/t0_templates.py` | 1267 | T0: doc-class classification, keyword tables, REF-list extraction, `field_ev()` evidence shape |
| `app/extract/t0_layout.py` | 307 | The T0 REF-list layout-template engine (anchors + text-relative regions, read from playbooks) |
| `app/extract/udi.py` | 148 | Offline Basic UDI-DI check-character validation (HIBCC + GS1, MOD 1021/32) |
| `app/extract/tiers.py` | 353 | The ladder itself: `needs_escalation`, `merge`, `calibrate`, `run_extraction`, `extraction_attempt`/`extraction_cost` writers |
| `app/extract/llm.py` | 335 | T1/T2 Anthropic clients (sync `AnthropicLlm`, batch `AnthropicBatchClient`), the JSON schema, per-field evidence construction |
| `app/extract/batching.py` | 56 | Batch API state machine (`advance_batch`) over the `batch_ref` side table |
| `app/extract/economics.py` | 110 | Token usage → USD, per-model `PRICES`, `UnknownModelPricing` |
| `app/extract/pdf.py` | 137 | The only module touching PyMuPDF directly: text, rasterization, `is_scan`, `bounded_text` |
| `app/extract/text_store.py` | 77 | `document_text` sidecar — what was actually read, independent of the tier ladder |
| `app/handlers/extract.py` | 374 | `handle_extract_doc` — sync/batch dispatch, hint assembly, `_finalize` (writes + emits `validate.doc`) |
| `app/handlers/gate.py` | — | `_insert_evidence` (622-678), `_require_complete_evidence` (238-266), `_apply_edits` (1118-1139) — see Rules and Gotchas |

## Input → Output

| Direction | Shape |
|---|---|
| In | `extract.doc` payload: `content_hash`, `archive_url`, optional `group_id`, `companion_archive_url` (`docs/dev/handlers.md` `extract.doc` row) |
| In (steering) | An `extract_hints` block from the resolved manufacturer playbook, guarded and hedged (`_hints_for`, `app/handlers/extract.py:51-90`) |
| Out (DB) | One `extraction_attempt` row per revision (`tiers.write_extraction_attempt`, `tiers.py:315-333`), one `extraction_cost` row per priced LLM call (`tiers.py:336-353`), one `document_text` row (`text_store.store_text`) |
| Out (queue) | `validate.doc`, dedupe key `validate:{hash}:{rev}:{group_id}` (`app/handlers/extract.py:151-156`) |
| Out (evidence) | **Not written here.** `extract.doc` never inserts into `evidence` — it emits `validate.doc`, which emits `gate.candidate`, and GATE is what turns `extraction_attempt.fields` into `evidence` rows (see Rules) |

## Rules

**Escalation** (`app/extract/tiers.py:99-125`). `needs_escalation(fields,
threshold)` returns fields absent or below `threshold`. `_ESCALATE_ALWAYS`
(type, regulation, validity_from, validity_to, coverage_scope, cert_number,
manufacturer) always pursues; `_ESCALATE_ITEM_ID` (ref_list, basic_udi_di) is
suppressed once `coverage_scope` is confidently `manufacturer`
(`tiers.py:122-124`); `_ESCALATE_CERTIFICATE` (validity_from, validity_to,
cert_number) is suppressed once `type` is confidently one of
`_NO_CERTIFICATE_TYPES = ("IFU",)` (`tiers.py:75, 118-120`) — an IFU asked
for a validity date returns a fabricated one, not a blank (two STRAUMANN
cases, `tiers.py:57-69`). `referenced_docs` and `stated_class` never force
escalation.

**The ladder** (`run_extraction`, `tiers.py:265-303`): T0 always runs first;
business-doc/msds classes short-circuit with no LLM call at all
(`tiers.py:286-287`); T1 runs when anything is missing; T2 runs when
anything is *still* missing **or** `pdf.is_scan(doc)` is true
(`tiers.py:297`) — see Gotchas on what "forced" actually means here. The
batch transport (`_handle_batch`, `app/handlers/extract.py:178-221`)
re-implements the identical gating by hand per tier, one poll cycle per
tier-attempt, because the two transports cannot share the sync function's
control flow.

**Merge policy** (`merge`, `tiers.py:157-198`): a later tier wins for the
fields it *returned*; a blank later answer never erases an earlier hit
(`_is_blank`, `tiers.py:128-136`); one shrink guard applies only to
`_ENUMERATED_FIELDS = {"ref_list"}` (`tiers.py:142,145-154`) — a later tier
may not replace a longer list with a shorter one, because an LLM `ref_list`
is a bounded sample by construction (`REF_LIST_PROMPT_CAP = 150`,
`tiers.py:211`, injected into both prompts via `{{REF_LIST_CAP}}`,
`llm.py:29-38`) while T0's pdfplumber read is a full enumeration. Measured
loss from getting this wrong: 26 of 61 correct REF lists deleted by an
unconditional merge; 199 catalogue items of coverage lost to a truncated
LLM list overwriting a full T0 one (`tiers.py:167-182`). `integrity_flags`
(`tiers.py:224-247`) reports (never prunes) `ref-list-possibly-truncated`
when a T1/T2 list lands at exactly the cap — length is deterministic and
doesn't trust the model's own truncation self-report, which was measured
lying (doc 258: claimed "40 returned", actually 46 against a true 53).

**Calibration** (`calibrate`, `tiers.py:254-262`): `effective = raw *
factor(model_id, field)`, clamped `[0,1]`; T0 (`model_id is None`) is never
calibrated; factor defaults to 1.0 pending S0.4 calibration data.

**Batch state machine** (`app/extract/batching.py`). `advance_batch`
(`batching.py:42-56`) is a 3-state machine over `batch_ref`, keyed
`(content_hash, tier)`: no row → submit + record, return `"submitted"`;
row with `resolved_at` → idempotent re-fetch, return `"done"`; row without →
poll `batch_client.done()`, return `"pending"` or resolve + `"done"`. It
lives in a side table, never in the job payload, so a crash between submit
and record can never double-submit (Invariant 9) — `batch_ref` is
`(content_hash, tier)` PRIMARY KEY (`migrations/001_queue.sql:70-77`). The
handler surfaces a non-done state as `{"_deferred": True, "waiting_on":
tier, "state": state}` (`app/handlers/extract.py:224-248`), which the runner
reads to skip `finish` and re-poll later (`docs/dev/handlers.md`
cross-cutting mechanics).

**Cost ledger** (`app/extract/economics.py`, `extraction_cost` table,
`migrations/010_extraction_cost.sql`). `cost_usd(model_id, usage, batch)`
(`economics.py:78-94`) raises `UnknownModelPricing` for a model with no
`PRICES` entry rather than costing it 0 — the never-silent rule applied to
money. `CallCost.cost` is a property for exactly this reason
(`economics.py:97-110`): capturing a call's tokens must never fail, so
`write_extraction_cost` (`tiers.py:336-353`) catches the exception and
stores `cost_usd = NULL` while keeping every token count — `extraction_cost`
has `cost_usd numeric(12,6)` nullable by design
(`migrations/010_extraction_cost.sql:32`), and the `extraction_spend` view's
`unpriced_calls` column exists to find those rows (`010_extraction_cost.sql:
41-50`).

**T0 template engine** ("engine-agnostic data", `app/extract/t0_layout.py`).
A `Template` (`t0_layout.py:37-43`) is `slug`, `manufacturer`, `anchors`
(text to match, never pixel/bbox coordinates), `ref_strategy` (one of
`table` / `text-column` / `stitched-table`), `ref_strategy_config`. Templates
are read through `app.playbooks.load_raw` (`t0_layout.py:58`), the same
store the rest of a playbook comes from — not a second glob with its own
cache. A playbook with no parse section is valid and silently skipped; a
malformed one is logged and skipped, never crashes the worker
(`t0_layout.py:10-15`).

**Evidence field construction — where each piece actually comes from.**
T0's `field_ev()` (`t0_templates.py:37-38`) builds `{value, conf, tier="T0",
verbatim, page}` — no `model_id`, no `archive_url`, no `extracted_at`. T1/T2's
`_to_ev()` (`llm.py:136-144`) builds `{value, conf, tier, verbatim, page,
model_id}` — still no `archive_url`, no `extracted_at`. **Both**
`archive_url` and `extracted_at` on the `evidence` row are filled by GATE,
not by anything in `app/extract/`: `_insert_evidence`
(`app/handlers/gate.py:622-678`) computes `field_url = ev.get("archive_url")
or archive_url` (`gate.py:646`) and writes `extracted_at` as a bare `now()`
in the `INSERT` itself (`gate.py:652`). The one exception is a per-field
override, not the general case: `_ref_from_companion`
(`t0_templates.py:1089-1118`) sets `ev["archive_url"] = companion_url`
(`t0_templates.py:1116`) when a
manufacturer's REF list is read off a separate annex file (Komet:
declaration + `..._RA_812_Liste_DoC.pdf`) — GATE's `field_url = ev.get(...)
or ...` is what makes that override survive into the row instead of being
overwritten by the document's own handle.

**T3 is not a tier `app/extract/` implements.** A human correction through
`gate.apply` is written entirely inside `_apply_edits`
(`app/handlers/gate.py:1118-1139`): `tier='T3'`, `model_id=NULL`,
`confidence=1.0`, `page=NULL`, `verbatim` = the human's own input, stamped at
`extract_rev = MAX(extract_rev)+1` for that doc so "current" always resolves
to the human's value. `app/extract/` never sees a T3 evidence row.

**Evidence-page requirement** (Invariant 2's T1/T2-only page rule). Enforced
in `_require_complete_evidence` (`app/handlers/gate.py:238-266`): a required
field with `tier in ("T1","T2")` and `page is None` raises the blocking flag
`evidence-page-missing` (`PAGE_MISSING_FLAG`, `gate.py:235`); T0 and T3 are
exempt by construction (T0's filename-only path genuinely has no page, T3's
`page` is always `NULL`). This is **handler logic only** — `evidence.page`
is a nullable `int` with no `CHECK` distinguishing tiers
(`migrations/005_registry.sql:70`), so nothing in `app/extract/` or the
schema enforces it.

## Limits

- No config-driven escalation threshold — see Gotchas.
- `app/extract/pdf.py`'s `archive_url` handling is an explicit stopgap
  (`resolve_local`, `pdf.py:20-25`): a filesystem path or `file://` URL only,
  documented as standing in for the real `StorageAdapter` (`pdf.py:7-9`).
- `models.rank` (`app/config.py:206-216`) is defined but read by nothing —
  see Gotchas.
- The Batch API transport is batch-of-1 per `(content_hash, tier)`
  (`llm.py:302-305`); cross-document batching (the "sweep economy") is a
  later concern, not implemented here.

## Gotchas

- **The escalation threshold is a hardcoded default parameter, and it is
  NOT `cfg.gate.high`.** `run_extraction(..., threshold: float = 0.95, ...)`
  (`tiers.py:265`) is never called with an explicit `threshold=` anywhere in
  `app/handlers/extract.py` (`extract.py:169` for sync; the batch path calls
  `tiers.needs_escalation(fields)` directly at three call sites of its own,
  `extract.py:202, 209, 212`, same unpassed default). `cfg.gate.high` is a
  *separate* number, `0.92` (`app/config.py:57-70`), read only inside
  `app/handlers/gate.py` to gate four unrelated GATE-time decisions
  (coverage-scope confidence, the supersession fast path, document- and
  link-level production disposition). The two numbers *used* to be the same:
  `app/config.py:57-58` records that `gate.threshold.high` was `0.95` in the
  original scaffold and was moved to `0.92` by Denis's ruling of 2026-08-19 —
  `tiers.py`'s default was never updated to track that move. Conflating them
  is an easy, wrong assumption to make from the matching digits alone.
- **T2 is "forced" for a scan, but that only means the condition considers
  it — a vision call still doesn't fire on nothing.** `if missing or
  pdf.is_scan(doc):` (`tiers.py:297`) enters the T2 branch whenever the
  document is a scan even if T1 already satisfied every escalated field;
  inside that branch, `target_fields = missing or
  needs_escalation(fields, threshold)` (`tiers.py:298`) is recomputed, and
  the actual API call is skipped if that recomputation comes back empty
  (`tiers.py:299`, "don't pay for a vision call with nothing to extract").
  In practice a genuine scan rarely reaches that empty case, because T0/T1
  read almost nothing off an image. Pinned by
  `tests/test_tiers.py::test_scanned_doc_forces_t2` (`test_tiers.py:304-309`).
- **`models.rank` is read by nothing.** `app/config.py:206-216` defines
  `models.t1` / `.t2` / `.rank` / `.email_summary`; grepping `app/` for
  `.rank` (2026-08-31) turns up only that docstring and no code reading
  `cfg.models.rank`. The live T1 candidate ranker DISCOVER would use it for
  is not wired: `_default_rank` (`app/handlers/discover.py:167-170`) raises
  on every call, tracked as followup `discover-t1-ranking`, so a ranker
  failure degrades to a logged miss (`discover.py:203-204`) rather than
  reaching any model.
- **`archive_url` and `extracted_at` are not set anywhere in
  `app/extract/`** in the general case — both are GATE-constructed columns
  (`gate.py:646, 652`, detailed under Rules above). The one place
  `app/extract/` sets an `archive_url` at all is the companion-annex REF-list
  override (`t0_templates.py:1116`), and that is a per-field value GATE
  reads back, not the row's primary handle.
- **T3 evidence has no representation in `app/extract/` at all** — it is
  constructed entirely by `_apply_edits` in `app/handlers/gate.py:1118-1139`.
  A grep for `tier.*T3` inside `app/extract/` returns nothing.
- **`TARGET` lives in a leaf module for a reason that bit web, not
  extraction.** `app/extract/target.py` imports nothing on purpose: two
  callers outside the extractor (`app.playbooks._parse` and the playbook
  editor route in `web/registry.py`) need the 11 field names and run in the
  slim web image, which has no PyMuPDF. Importing `TARGET` from `tiers.py`
  (which chains to `app.extract.pdf` → `pymupdf`) broke every `/playbooks`
  row parse in that container on 2026-08-27; see `docs/dev/web.md` Gotchas
  for the user-visible failure. `tiers.py:29` re-exports `TARGET` so
  `tiers.TARGET` still works for the extractor's own callers.

## Tests

| File | Pins |
|---|---|
| `tests/test_tiers.py` | Escalation gating, `merge`/shrink-guard, `run_extraction`, `test_scanned_doc_forces_t2` |
| `tests/test_tiers_corpus.py` | Ladder behaviour against the seed corpus |
| `tests/test_t0.py`, `tests/test_t0_corpus.py` | `t0_templates.py` classification and field extraction |
| `tests/test_t0_layout.py` | The layout-template engine (`t0_layout.py`) |
| `tests/test_llm.py`, `tests/test_llm_schema_vocabulary.py`, `tests/test_llm_usage.py` | `llm.py` — schema shape, evidence construction, usage capture |
| `tests/test_llm_credentials.py` | Lazy client construction needs no key until a call is made |
| `tests/test_recorded_llm.py` | Replayed real API responses |
| `tests/test_batching.py` | `advance_batch` state machine |
| `tests/test_economics.py` | Pricing, `UnknownModelPricing`, batch discount |
| `tests/test_pdf.py`, `tests/test_pdf_text_sanitising.py` | `pdf.py` — `is_scan`, `bounded_text`, NUL sanitisation |
| `tests/test_udi_validation.py` | `udi.py` check-character algorithm against the HIBCC worked example |
| `tests/test_playbook_tier_a.py`, `tests/test_playbook_tier_b.py` | Playbook-steered extraction (hints, identity) |
| `tests/test_extract_handler.py` | `app/handlers/extract.py` — sync/batch dispatch, `_finalize`, defer signal |
| `tests/test_extract_cost.py` | `extraction_cost` writes from the handler |
| `tests/test_gate_candidate_handler.py` | GATE's `_insert_evidence`, `_require_complete_evidence`, `evidence-page-missing` |
| `tests/test_gate_apply_handler.py` | GATE's `_apply_edits` — T3 evidence construction |
