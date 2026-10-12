# Extraction, as built: T0 → T1 → T2 → T3

**Status:** live. Measured against the tree at 2026-08-31; the scan ladder
(a document with no text page) added 2026-10-12, extraction-ladder plan Task 3.

Covers `app/extract/` and the parts of `app/handlers/extract.py` and
`app/handlers/gate.py` that finish what `extract/` starts. Does not restate
the tier ladder's *rationale* (`dentalia-job-type-handbook.md` §5,
`dentalia-mdr-pipeline-ground-truth.md` §7) — this is what the code actually
does, and where the surprises are.

## Idea

Four tiers read one document's fields, cheapest first, escalating per field
rather than per document: T0 is free deterministic parsing (regex, keyword
tables, pdfplumber tables, manufacturer-authored layout templates), T1/T2 are
Anthropic structured-output calls (text, then vision) gated by confidence, and
T3 is a human correction made through `gate.apply` — it never touches
`app/extract/` at all. A document with no text page at all (a scan) skips T1
and takes its own ladder of image reads: a cheap read, a second cheap read for
what that leaves open, Sonnet only for what the two do not settle. `extract.doc` (`app/handlers/extract.py`)
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
| `app/extract/tiers.py` | 830 | The ladder itself: `needs_escalation`, `merge` (stronger read prevails), `calibrate`, `run_extraction` (text ladder), `run_scan_ladder` (scans), `read_disagreements`, `extraction_attempt`/`extraction_cost` writers |
| `app/extract/checks.py` | 323 | Evidence checks, pure: `settled` / `null_settled` / `both_null_settled` against the text layer; for scans `well_formed`, `normalized` and `agree` (shape and agreement, no text needed) |
| `app/extract/llm.py` | 718 | T1/T2 Anthropic clients (sync `AnthropicLlm`, batch `AnthropicBatchClient`), `READ_STEPS`, the JSON schema, per-field evidence construction, the doc-class calls (`classify` on page 1-2 text, `classify_image` on page 1-2 images) |
| `app/extract/batching.py` | 56 | Batch API state machine (`advance_batch`) over the `batch_ref` side table |
| `app/extract/economics.py` | 110 | Token usage → USD, per-model `PRICES`, `UnknownModelPricing` |
| `app/extract/pdf.py` | 240 | The only module touching PyMuPDF directly: text, rasterization, `is_scan`, `bounded_text`, per-page classes (`page_classes`; `has_text_page` decides text ladder or scan ladder) |
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

**The ladder** (`run_extraction`): T0 always runs first; business-doc/msds
classes short-circuit with no LLM call at all. Then `pdf.has_text_page(doc)`
splits it: a document with any text page (an OCR layer over a scan counts, so
does a mixed PDF) takes the **text ladder** — T1 runs when anything is
missing; T2 (Sonnet) runs when anything is *still* missing **or**
`pdf.is_scan(doc)` is true, which on this branch now means a mixed PDF whose
page 1 has no text (see Gotchas). The batch transport (`_handle_batch`,
`app/handlers/extract.py`) re-implements the text gating by hand per tier, one
poll cycle per tier-attempt. A document with no text page takes the **scan
ladder** below, on both transports through the same function.

**The scan ladder, S-2** (`run_scan_ladder`; PRD v3 §5 amended 2026-10-12).
Steps and labels from `llm.READ_STEPS`:

1. T0 left the file `unknown`: the doc-class call on the page 1-2 images
   (`classify_image`, `doc-class-image`, `models.vision_first`, thinking off,
   256 tokens). `not-a-compliance-document` is terminal, as for text. The text
   doc-class call is never made on a scan: it saw no text and refused the file
   without a call, which is how 281 production scans were never read.
2. `T2-first` (`models.vision_first`, pages 1-4) for every pursued field T0
   has not settled (T0 at 0.95 or more). A field it answers **well-formed**
   (`checks.well_formed`: an ISO date that is a real day, `type` / `coverage_scope`
   / `regulation` from their vocabularies, a Basic UDI-DI whose check pair is
   not invalid, a `cert_number` that is not a notified-body number, a list with
   a code) at `extract.image_settle` (0.85) or above is settled.
3. `T2-second` (`models.second_read`, thinking off) for what is open. Settled
   when both reads agree (`checks.agree`: the same well-formed value after
   `checks.normalized` — dates as ISO, codes compacted, lists as sets — each read
   at least `gate.agreement_min`, 0.70) or both are empty and
   `checks.both_null_settled` allows it (never a Basic UDI-DI on a scan).
4. A scan longer than the four pages the image reads see: a field both cheap
   reads left empty is **unread**, not settled and not sent to Sonnet (who
   sees the same pages). Recorded under `_pages_unread`; VALIDATE raises the
   informational flag `pages-unread` from it (`tiers.integrity_flags`).
5. Type to scope: once the type is known to be EC, ISO or IFU, the scope follows
   from it (`t0_templates.derive_coverage_scope`) and replaces a scope that
   disagrees or is empty, as T0 evidence ("type=EC") at the type's confidence
   where lower. Runs after each read, so a type the first read settles spares
   the second read the scope. Measured: two cheap reads agreed on `group` for an
   MDD certificate in the eval; this caught it.
6. `T2` (`models.t2`, Sonnet) for what is still open, never when nothing is.
   Its answer closes what it was asked; type to scope runs once more.

The pursued set is `needs_escalation`'s, keyed to what is KNOWN (T0 at 0.95 or
settled by a read) rather than to a value, so it only shrinks: a known IFU
stops the certificate fields, a known manufacturer-wide scope stops the item
identifiers. T0's own scope on a scan is mostly a guess (a DoC typed off its
file name gets `manufacturer` at 0.6), and by value it would have kept the item
identifiers from ever being asked.

Every read is kept: `_reads` on the attempt's `fields`, a list in read order of
`{step, model_id, fields: {field: {value, conf, page}}}` (None for a field
asked and not answered); a list because jsonb sorts an object's keys. Every
field two reads answered differently is on the job result as `disagreements`
(`{field, steps, values}`) and counted as `counts.read_disagreements`
(`tiers.read_disagreements`); disagreements are counted, not sent to review
(Denis, 2026-10-12). A scan field's evidence carries `step` (its label), which
GATE ignores and `merge` ranks by.

**Merge policy** (`merge`): the **stronger** read wins for the fields it
*returned* (rulings 2026-10-12): Sonnet's image read over a cheap image read
(`T2-first`, and below it `T2-second`, which only confirms) over T1 over a T0
guess under 0.95; T0 at 0.95 or more is never overwritten; equal strength,
later wins. On the text ladder each read is stronger than the one before, so
there it is exactly the old later-wins rule. A blank later answer never erases
an earlier hit
(`_is_blank`, `tiers.py:128-136`); one shrink guard applies only to
`_ENUMERATED_FIELDS = {"ref_list"}` (`tiers.py:142,145-154`) — a later tier
may not replace a longer list with a shorter one, because an LLM `ref_list`
is a bounded sample by construction (`REF_LIST_PROMPT_CAP = 150`,
`tiers.py:211`, injected into both prompts via `{{REF_LIST_CAP}}`,
`llm.py:29-38`) while T0's pdfplumber read is a full enumeration. For a list
the length decides before the strength: a longer list wins from any read
(except over T0 at 0.95), and only two lists of one length are settled by
strength. Keys that are not evidence (`_date_label_refused`, `_reads`,
`_pages_unread`) are copied as given. `merge(..., by_strength=False)` is the
pre-2026-10-12 rule, kept for `app/repair_ref_list.py`, which replays today's
T0 under stored answers and must not re-derive the fields T0 now reads at 0.95. Measured
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
`(content_hash, tier)`, where `tier` is the read's label (so two reads of one
kind never share a row): no row → submit + record, return `"submitted"`;
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

**One row per paid call, keyed by the read's label** (since 2026-10-12). The
ledger is unique on `(content_hash, tier, extract_rev)`, and its `tier` column
holds the call's label from `llm.READ_STEPS` (`doc-class`, `doc-class-image`,
`T1`, `T1-second`, `T2-first`, `T2-second`, `T2`), not the evidence tier. Before
that the doc-class call (`AnthropicLlm.classify`) and the T1 read both logged
`T1`, so on every document T0 could not type the conflict clause dropped one of
the two rows without a word. `/status` → **Spend** shows the breakdown per
model, label and transport. The text ladder sends `doc-class`, `T1` and `T2`;
the scan ladder `doc-class-image`, `T2-first`, `T2-second` and `T2`;
`T1-second` belongs to plan Task 5. A doc-class answer cut at `max_tokens`
raises (`llm._classify_answer`) instead of reading as a refusal; so does a
batch `doc-class-image` result that carries no answer
(`app/handlers/extract.py` `_batch_classifier`).

**Read steps** (`app/extract/llm.py`, `READ_STEPS` / `resolve_read`). A read is
a label plus a kind (text / image), a model, a thinking setting and, for an
image read, an explicit page list (default pages 1-4, at most
`MAX_VISION_PAGES`). The request is routed by kind, never by label: until
2026-10-12 `_params` sent every label but `T1` to `models.t2`. The kind also
stamps `evidence.tier` (`T1` text, `T2` image), so GATE's page rule and the
REF-cap flag keep seeing exactly those two values. A label in `READ_STEPS`
takes its kind and model from its row, and naming either is refused: a
different read gets its own label, so it never shares a ledger row with the
real one. A label with no row must name both. `thinking=None` sends no
thinking key even where the row names one. With an explicit page list (sync
path only), the page the vision prompt cites (an image index) is translated
back to the document page. A cite outside the images sent becomes `None`, keeps
its value and quote, and is counted: the extract handler puts the total on the
job result as `counts.cited_page_outside_images`. GATE then raises
`evidence-page-missing` (blocking, manual review) only when the field is
`type`, `regulation` or `coverage_scope`; any other field is stored without a
page. The options reach `extract` / `build_requests` only as keyword
arguments, so every stub implementing `extract(tier, doc, filename, missing)`
keeps working. `AnthropicBatchClient.build_requests` refuses a page list until
`batch_ref` stores the pages sent ([limits.md](limits.md#built-but-not-wired));
`results` maps each result's label back to the read it was built from that
poll, else to the label's `READ_STEPS` row, and a label with no row raises
instead of being priced as `models.t2`, the old fallback. One extraction sends
each label at most once: `_finalize` in `app/handlers/extract.py` fails the
job before writing anything when a label repeats, since the ledger would keep
one of the two rows.

**Haiku 5.5 is priced by prompt length** (`economics.PRICES`, read off the
pricing page 2026-10-12): $0.10 / $0.50 per MTok up to 100,000 prompt tokens,
$0.50 / $2.50 over, for the whole call. Prompt length counts cache reads and
writes. Sonnet 5.5 reads its cache at 0.05x, not the default 0.1x.

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
- **A scan is "no text page", not "page 1 has no text".** Since 2026-10-12
  the ladder routes on `pdf.has_text_page` (`page_classes`: a page of 200+
  characters, or an OCR layer), and a document with none takes the scan
  ladder: no T1 read of empty text, no forced T2. `pdf.is_scan` (page 1 under
  20 characters) still has two callers: `text_store.store_text`, whose
  `document_text.source = 'none'` (and so the `no_text_layer` anomaly and
  `reextract --dropped-scans`) still means "page 1 has no text"; and the text
  ladder's `if missing or pdf.is_scan(doc):`, which now fires only on a mixed
  PDF (an image page 1, text later) and still skips the call when nothing is
  left to ask. A document whose every page is short (`sparse`, under 200
  characters) has no text page either, so it is read off its images too.
- **`T0` scope on a filename-typed scan.** `derive_coverage_scope` gives EC and
  ISO `manufacturer` at 1.0 whatever the type's confidence, so a scan T0 typed
  `EC` off its file name ("...Certificate...") carries a KNOWN manufacturer
  scope and its item identifiers are never asked, unless a read types it
  otherwise and type to scope replaces the scope. Unchanged from before the
  scan ladder; noted for plan Tasks 4-5.
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
| `tests/test_tiers.py` | Escalation gating, `merge`/shrink-guard, `run_extraction`, `test_a_scan_takes_the_image_ladder_and_never_a_text_read` |
| `tests/test_tiers_scan.py` | The scan ladder: routing (a mixed PDF stays text), the image doc-class call, 1/3/6-page scans and `pages-unread`, settle at 0.85, agreement at 0.70, both-null except the UDI, type to scope, Sonnet only for what is open, the reads record, disagreements, `merge` by strength |
| `tests/test_checks.py` | `checks.py` — the text checks, page classes, and the scan shape / agreement checks |
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
