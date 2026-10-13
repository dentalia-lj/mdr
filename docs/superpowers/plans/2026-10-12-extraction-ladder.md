# Extraction ladder: Haiku 5.5, evidence checks, agreement (plan)

**Status:** draft v3 for Denis's approval, 2026-10-12. Nothing is built. v2 folded in
three read-only reviews (decisions vs talks, document cases, code feasibility) and
Denis's four answers of the same day; v3 makes recognition quality and documents
decided automatically the primary goal (Denis: "the number of automatically parsed
files is more important than the number of cents saved") and adds Task 9.
**Branch / worktree:** `extraction-ladder` at `.claude/worktrees/extraction-ladder`, from `main` 5cef711.
**Evidence:** `docs/private/2026-10-08-extraction-ladder-research.md` (main checkout, gitignored),
sections 6, 12-15; eval runs and scripts under `eval/` in the main checkout.

## Goal

**Primary:** more documents decided automatically (production or filed) with no
wrong field, and recognition at least as good as today, field by field.
**Secondary:** call Sonnet only where a cheaper read cannot settle a field.
A quality regression blocks the deploy whatever it saves (Task 8).

Quality measured on the eval set (19 labelled text documents, 4 labelled scans):

| | today | this plan |
|---|---|---|
| T1 field accuracy, 130 labelled fields | 87% (Haiku 4.5) | 90% (Haiku 5.5) |
| REF lists right | 5 of 8 | 8 of 8 |
| DoC certificate numbers right (ruling A) | 8 of 15 | 11 of 15 |
| quote on the cited page, `manufacturer` | 66% | 99% |
| final values vs Sonnet reading every open field | | text: 5 differ, 3 better, 2 convention, 0 clearly worse; scans: 4 differ, 1 better, 1 worse (fixed by the type-to-scope rule), 2 unlabelled |
| wrong gate fields in production | 0 of 19 | 0 of 19 text; scans 1 of 4, likely a label error |

The labelled set is small: this shows no loss, it does not prove none. Production's
review queue (1,039 on 2026-10-12) is held mostly by matching, not reading: no REF or
UDI found 527, printed manufacturer unresolved 309, certificates awaiting binding 211,
gate confidence 105. Task 9 measures how many of these the new ladder clears.

Routing measured on the eval set (cached reads, 79 text documents, 34 scans):

| | today | this plan |
|---|---|---|
| text documents reaching Sonnet | 55 of 79 | 7 of 79 |
| scans reaching Sonnet | 34 of 34 (281 production scans never read at all) | 19 of 34 |
| text gate, production share | 73% (simulated) | 84%, 0 wrong gate fields of 19 labelled |
| scan gate, production share | 1 of 34 | 24 of 34, 1 wrong of 4 labelled (likely a label error) |
| Sonnet share, production mix (69% text, 31% scans) | about 79% of read documents | about 24% |

## Rulings this implements

From `docs/decisions.md` (2026-10-12) and the research doc:

1. Haiku 5.5 at T1; Sonnet 5.5 stays the last read.
2. Haiku 5.5 image read before Sonnet image (T-3 for text, S-2 for scans).
3. Evidence checks replace the 0.95 self-reported-confidence escalation rule.
4. Agreement settles a field. Text: T1 and the Haiku image read give the same value, or
   both null (as measured, no per-read minimum). Scans: two Haiku image reads give the
   same value with each at least 0.70, or both null (never `basic_udi_di`).
5. A DoC's `cert_number` holds the certificate it cites, in every tier.
6. Gate: two agreeing reads combine to `1 - (1 - c1)(1 - c2)` when each is at least
   0.70; a regulation proven by its printed citation counts 1.0; T0 unchanged;
   thresholds 0.92 / 0.75 unchanged; second read Haiku 5.5 thinking off; no factor.
7. The 79 + 34 evaluation stands in for the Phase 0 diff protocol for this swap.
8. Stronger read prevails (Sonnet over Haiku image over T1).

**Answered 2026-10-12 (Denis):** disagreements are counted on the job result, not
sent to review; evidence gets the agreeing read through a migration; multi-UDI lists
still go to Sonnet; scans get a doc-class call on their page images, and on scans
longer than 4 pages an empty field is marked unread instead of settled.
Also answered (Denis, after the Task 3 review): on a scan, the item identifiers
(REF list, Basic UDI-DI) are skipped only when the `manufacturer` scope is known
(T0 at 0.95 or more, or settled by the reads), not when it is T0's guess from the
file name; up to 8 more Sonnet calls per 34 scans (about 24 instead of 19).
After Task 5a (Denis): the confidence note under Technical details as worded; a
citation-proven `regulation` stores 1.0 in `evidence.confidence`, and the note says so.
After the Task 5a review (Denis): the guard covers superseded documents too; a kept
candidate still stages its links; fix now the three existing gate problems the review
confirmed (a production re-read changing type or regulation inside a supersession
chain, the reviewer-edit revision clash, no stale-revision check on the binding route).
2026-10-13 (Denis): a rejected document is final for the machine; a production reading
of a superseded document is kept; a confirmed EC/ISO certificate's scope carries the
type's confidence (Task 4 departure 2). Task 5b also: `tiers.next_extract_rev` counts
evidence revisions too (the shared counter the 5a review found); on text, which read is
"first" when the image read agrees with T1.

**Defaults taken (no ruling, say if wrong):** mixed PDFs take the text branch; a
scan is a document with no text page at all; T0 fields under 0.95 count as settled
when they pass the checks, and today's IFU and manufacturer-scope suppressions stay;
the manufacturer auto-bind keeps reading the first read's own scope confidence; the
2026-09-16 confidence note goes on the two confidence tables and the guide's review page.

## Considered and left out

- **Rejected by measurement:** Sonnet reading the whole text before images (T-2: 77% of
  an image call, closes 22% of documents); per-model factor 1.08; higher effort.
- **10-08 rulings deferred by Denis (2026-10-12):** review when reads disagree on gate
  fields, validity dates or 3+ fields; Sonnet receiving settled facts and where to look.
  Disagreements are counted (Task 4) so both can be decided on production numbers.
- **Out of scope:** unresolved manufacturer names (other session); `no-item-identifier`
  on scanned DoCs (a matching problem); reading more than 4 pages; T0 templates for the
  top six manufacturers; docx/xlsx sources; a multi-value `basic_udi_di`.

## Read steps and their labels

One table, used by sync and batch alike. `evidence.tier` stays T1 (text) or T2
(image) with the real `model_id`; the label exists only for the cost ledger and
`batch_ref`, so two reads of one kind never share a key.

| step | reads | model | label |
|---|---|---|---|
| doc-class, text | page 1-2 text | models.t1 | `doc-class` |
| doc-class, scan | page 1-2 images | models.vision_first | `doc-class-image` |
| T1 | full text (360k-char budget) | models.t1 | `T1` |
| second text read (gate) | full text | models.second_read, thinking off | `T1-second` |
| first image read | pages 1-4 | models.vision_first | `T2-first` |
| second image read (scans) | pages 1-4 | models.second_read, thinking off | `T2-second` |
| Sonnet image read | pages 1-4 | models.t2 | `T2` |

## Progress (updated as tasks land)

| task | state | where |
|---|---|---|
| 0 contract (PRD, design doc) | done, approved | 4811151 |
| 1 read plumbing, ledger | done: built (Opus), reviewed and fixed (Opus), merged; full suite 4674 passed, 5 skipped | merge of f843948 |
| 2 checks, page classes | done: built (Sonnet), reviewed and fixed (Opus), merged | b9cd734 |
| 3 scans | done: built (Opus), reviewed and fixed (Opus), merged; full suite 4820 passed, 5 skipped; decision 3 ruled by Denis (item identifiers asked on filename-typed scanned DoCs) | merge 0c891c8 |
| 4 text ladder | review fixes in (6c5c592, 10f6537, 67f630a; full suite 4960 passed; agree replay 7 of 79 = measured); IFU drop on scans running, then Opus check | worktree el-text |
| 5a gate side: combined score, migration, published guard, wording | done: built (Opus), 3 Opus review rounds, merged; full suite 4921 passed, 5 skipped | merge 7b9abc4 |
| 5b extraction side: second reads stamp the contract keys | waiting for 4 and 5a | |
| 6 prompt caching | done: built (Sonnet), reviewed and fixed (Opus), merged; cache hits to confirm on the first real run | merge of bbc461d |
| 7 re-extract command | done: built (Sonnet), reviewed and fixed (Opus), merged | c39bb77 |
| 8 quality and parity | waiting for 1-6 | |
| 9 production replay | waiting for 1-6 | |

## Who builds what, and in parallel (Denis, 2026-10-12)

Sonnet builds the tasks that are self-contained and fully specified; every Sonnet
task is reviewed by Opus before it merges into `extraction-ladder`. Parallel tasks
run in their own worktrees under `.claude/worktrees/`, branched from
`extraction-ladder`, and merge back after review.

| task | builds | review | can run alongside |
|---|---|---|---|
| 0 contract | Opus | Denis | 7 |
| 1 read plumbing, ledger | Opus | Opus | 2, 7 |
| 2 checks, page classes | Sonnet | Opus | 1, 7 |
| 3 scans | Opus | Opus | 7 (not 4: same functions) |
| 4 text ladder | Opus | Opus | 6, 7 |
| 5 gate, migration | Opus | Opus | 6, 7 |
| 6 prompt caching | Sonnet | Opus | after 1; alongside 4, 5 |
| 7 re-extract command | Sonnet | Opus | anything (own files) |
| 8 quality and parity | Opus | Denis | 9 |
| 9 production replay | Opus | Denis | 8 |

## Tasks

Each task ships on its own: tests first, its module tests green, docs in the same
task, one commit unit. Full suite once before the branch merges.
Hours: human (by hand, tests and docs included) / Claude Code (wall-clock, same scope).

### Task 0. Contract first (docs only) - 3 h / 0.3 h

- [x] PRD v3 section 5: lines 206 (tier ladder) and 210 ("escalation on low per-field
      confidence only") to escalation on failed evidence checks and disagreement; the
      ladder of the steps table above. Flag list: add informational `pages-unread`.
      Evidence tuple: the agreeing read's model and confidence.
- [x] `docs/dentalia-v3-v4-system-design.md` tier table (lines 73-80).
      `docs/dev/extraction.md` describes the code as built, so it changes with Tasks 3
      and 4 (lines 1, 67, 194-197 describe T2 as forced for scans), not here.
- **Done when:** Denis has approved the PRD wording. Tasks 1-5 start only after that.

### Task 1. Read plumbing and the ledger - 10 h / 1.0 h

- [x] `app/extract/llm.py`: a read names its kind (text / image), model, thinking and,
      for image reads, an explicit page list (default pages 1-4, still at most 4);
      `_params` routes by kind, not by label (today any label but `T1` goes to
      `models.t2`, line 218). Overrides pass as conditional kwargs, like `hints`, so
      the protocol stubs (`extract(tier, doc, filename, missing)`) keep working.
- [x] Cost ledger: one label per call (steps table). Fixes today's silent loss: the
      doc-class call and T1 both log `T1`, and `extraction_cost`'s
      `ON CONFLICT (content_hash, tier, extract_rev) DO NOTHING` drops the second row.
- [x] Batch: `AnthropicBatchClient.results` maps label to model (today `tier == "T1"`
      else t2, line 395); labels use hyphens only (`_custom_id` splits on `_`).
- [x] `app/extract/economics.py`: PRICES row for `claude-haiku-5-5`, checked against
      the price page first (the eval table says batch 0.05/0.25, sync 0.10/0.50 per M).
- [x] `app/config.py`: `models.vision_first`, `models.second_read` (both
      `claude-haiku-5-5`), an `extract` section (`image_settle` 0.85,
      `agreement_min` 0.70): dataclass, load path, `_str` fallbacks, test_config.
- [x] Tests: `tests/test_llm.py`, `tests/test_economics.py`, `tests/test_extract_cost.py`,
      `tests/test_config*.py`; `tests/fixtures/llm` RecordedLlm keyed by the new labels.
- [x] Docs: `docs/dev/config-reference.md` (blast radius), status board note that
      spend rows by label appear on `/status`.
- **Done when:** every paid call writes its own cost row; no behaviour change yet.

### Task 2. Evidence checks and page classes (pure functions) - 12 h / 1.2 h

- [x] `app/extract/pdf.py`: per-page classes (text / image / OCR layer over a page
      image / sparse), ported from `tools/ladder_probe.py` `page_classes`; `is_scan`
      becomes "no text page at all".
- [x] New `app/extract/checks.py`, `settled(field, evidence, ctx)`:
      quote on the cited page; value literally in the text; dates parsed from the quote;
      REF codes 95% present; Basic UDI-DI check pair not invalid, and a check-valid
      UDI from an image read settles even when the text layer differs;
      `cert_number` never a 4-digit notified-body number (`0482`, `CE-0197`);
      regulation: `MDR` / `MDD` proven only when that citation alone is printed,
      `n.a.` only when neither is, never proven when both are printed.
      Null rule: settled only when no label for the field occurs (UDI label as a word,
      not inside "including"), no page is image, OCR-layer or unread, and the text
      was not truncated.
- [x] `app/extract/t0_templates.py` `_DATE_RE` / `_parse_date`: "25. 08. 2025",
      "30-Apr-2025", "June 24th, 2021", "fev. 18, 2026", "Jul 14, 2025".
- [x] Tests: `tests/test_checks.py` (new), `tests/test_pdf*.py`, `tests/test_t0_templates*.py`,
      table-driven from the eval cases (the 10 dates, `0482`, Ceramill `OOIYC`,
      "including", both-citations, a mixed PDF, a truncated text).

### Task 3. Scans: S-2 - 16 h / 1.6 h

- [x] `app/extract/tiers.py` scan branch (no text page): doc-class on the page 1-2
      images (`not-a-compliance-document` is terminal, as for text); first image read;
      a field at or above `image_settle` settles; second image read (thinking off) for
      what is open; agreement settles (rule 4); type to scope by
      `derive_coverage_scope` for EC / ISO / IFU; Sonnet image for what remains.
- [x] Scans over 4 pages: an empty field after both Haiku reads is not settled and not
      sent to Sonnet (Sonnet sees the same pages); it is marked unread and the document
      carries the informational flag `pages-unread` (`gate.INFORMATIONAL_FLAGS`).
- [x] `merge`: stronger read prevails; blank and enumeration guards unchanged.
- [x] `app/handlers/extract.py` batch path: the same steps with the labels above. The
      batch path makes no doc-class call at all today (found in Task 1), so `unknown`
      documents skip it in batch mode: add it here for scans and in Task 4 for text.
- [x] Tests: `tests/test_tiers*.py`, `tests/test_extract*.py`, `tests/test_gate*.py`
      (flag class), `tests/test_docs_sets.py` (flag vocabulary): blank page, scanned
      invoice, 1 / 3 / 6-page scans, settle at 0.85, agreement, UDI never on nulls,
      Sonnet only for what stays open.
- [x] Docs: `docs/dev/handlers.md`, `docs/dev/01-lifecycle.md`, `docs/vocabulary.md`
      (flag), `docs/dev/limits.md` (row for scans dropped as not-a-document, if one
      exists: check before deleting).
- **Done when:** a scan with an empty text layer reaches the gate with fields; no
  doc-class call is ever made on empty text.

### Task 4. Text ladder: T-3 with Haiku 5.5 - 27 h / 2.7 h

- [ ] `app/extract/prompts/t1_text.md`: the ruling-A `cert_number` line, verbatim as
      measured (`tools/haiku_compare.py` PROMPT_VARIANTS `ruling-a`); update the
      prompt pins in `tests/test_llm.py` and `tests/test_llm_schema_vocabulary.py`.
- [ ] `app/config.py`: `models.t1` default `claude-haiku-5-5` (dataclass default and
      `_str` fallback; test_config guards both). This also moves the text doc-class
      call to Haiku 5.5: cover it (256 tokens, adaptive thinking) in tests.
- [ ] `llm.extract` returns every parsed field, not only the asked ones (line 313
      filters today), into the reads pool; `stated_class` stays T0-only (PRD).
- [ ] `run_extraction` text branch: T0 -> T1 -> `checks.settled` (T0 fields under 0.95
      included; IFU and manufacturer-scope suppressions kept) -> open fields to the
      first image read -> settled by check, check-valid UDI or agreement with T1 ->
      Sonnet image for what remains (multi-UDI lists included). Image pages of a
      mixed PDF and text beyond the budget count as unread for the null rule.
- [ ] Page-aware image reads (Denis, 2026-10-12): the image read renders, in this
      order, the pages T1 cited for the open fields, the pages where an open field's
      label occurs, the image pages of a mixed PDF, then page 1 onward, at most 4.
      Cost-neutral. Measured on the eval: 1 of 72 open fields pointed past page 4
      (neodent `cert_number`, page 26). Sonnet on the whole text was not reintroduced
      (T-2: 77% of an image call, closed 22% of documents, 0 of 72 answers past page 4).
- [ ] Hints go to every read, as today's T1 and T2 get them.
- [ ] From the Task 1 review: the text doc-class call on Haiku 5.5 (256 tokens, adaptive
      thinking) must send thinking off or treat a `max_tokens` stop as a failure, since a
      truncated answer becomes `not-a-compliance-document` and drops the file for good;
      page-aware selection clamps to the page count (`_vision_content` raises past the
      end); RecordedLlm accepts read options; `.env.example` `MODELS_T1` updated.
- [ ] From the Task 3 review: Sonnet's explicit null closes an asked field (a T1 value
      must not survive it); a failed read is never an empty answer and is counted;
      `derive_coverage_scope` gives the EC / ISO / IFU branches the type's confidence, as
      the DoC branch does (today a filename-typed EC or ISO at 0.6 gets a `manufacturer`
      scope at 1.0, `t0_templates.py:946`, so item IDs are never asked if reads retype it).
- [ ] Disagreements counted: the extract job result lists each field where reads
      disagreed (field, steps, values), and the extraction record keeps every read.
- [ ] Batch path: same steps.
- [ ] Tests: `tests/test_tiers*.py`, `tests/test_extract*.py`, `tests/test_llm*.py`.
- [ ] Docs: `docs/dev/handlers.md`, `docs/dev/extraction.md`, `docs/architecture.md`,
      `docs/runbook.md` (MODELS_T1), `docs/code-map.md`, `docs/vocabulary.md`,
      `docs/dev/deployment.md` (EXTRACT_MODE, cost), `CLAUDE.md` models line,
      `docs/dentalia-job-type-handbook.md` extract section.
- **Done when:** on the eval set the built ladder sends 7 +/- 1 of 79 text documents
  to Sonnet (Task 8); 7-10 if the image read disagrees on a both-citations regulation
  (3 Ivoclar documents; kept unproven by Denis's no-fabrication rule). The cached image reads were not asked `type`, `coverage_scope`
  or a both-citations `regulation`, so this cannot be confirmed from the cache; Task 8
  measures it with real reads (Task 2 review, 2026-10-12).
- Settle rules ruled 2026-10-12: `type` and `coverage_scope` settle on a T1 quote found
  on the cited page; two empty reads settle any field except a Basic UDI-DI with a UDI
  label, never on unread pages or truncated text (PRD corrected the same day).

### Task 5. Gate: agreement as confidence - 21 h / 2.1 h

Split 2026-10-12 so the GATE half runs alongside Task 4: **5a** (GATE: combined score,
migration, published-document guard, 2026-09-16 wording) and **5b** (extraction: the
second reads that stamp the keys). Evidence contract between them, on `fields[f]`:
`conf` stays the first read's own raw confidence; `agreed_model_id` / `agreed_conf`
(raw) when a second read agreed on the same well-formed value; `proven: "citation"` on
a `regulation` the checks proved. GATE calibrates both and combines when each is at
least `gate.agreement_min`; `scope_stated`, the supersede guard and the manufacturer
guard read `conf` alone.

- [ ] Second read only where a gate field is neither T0 nor citation-proven: text
      documents get `T1-second`; scans ask the unproven gate fields in the second image
      read (Task 3 asks it only open fields, so a gate field settled at 0.85-0.92 is
      added here).
- [ ] From the Task 3 review: the 24 of 34 scan gate figure holds only if the unproven
      gate fields are asked in `T2-second` even where `T2-first` settled every open field
      (Task 3 makes no second call then); Task 3 alone gives 7 production.
- [ ] One call per label per extraction: the unproven gate fields ride in the same
      `T2-second` call as Task 3's open fields, or that call's cost row is lost.
- [ ] Combined confidence `1 - (1 - c1)(1 - c2)` when both reads agree and each is at
      least `agreement_min`; citation-proven regulation 1.0.
- [ ] Migration 071 (or the next free number at merge; other worktrees add
      migrations): `evidence.agreed_model_id`, `evidence.agreed_confidence`, nullable.
      `gate._write_evidence` writes them; the stored `confidence` is the combined one.
- [ ] `app/handlers/gate.py`: the manufacturer auto-bind (`scope_stated`, line 880)
      reads the first read's own scope confidence, not the combined one; per-link
      production and `_score` read the combined one; the task-5 supersede guard reads
      `validity_from` (never combined).
- [ ] Published-document guard (Denis, 2026-10-12): when a document already in
      production is read again, GATE replaces its fields only if the new read itself
      reaches production; otherwise the stored values stay, no review task opens (Review
      never lists tasks on published documents), and the difference is counted on the job
      result. Today `_upsert_document` overwrites the fields whatever the new disposition.
      134 of the 654 DoCs to re-read are published. Update the `reextract` runbook section
      ("GATE has no guard for this yet") and `docs/troubleshooting.md` re-read entry.
- [ ] The 2026-09-16 ruling's wording was never built: a note on the confidence columns
      of `web/templates/_staging_doc_detail.html` and `document_detail.html` (the
      model's own number, uncalibrated, may combine two agreeing reads), the guide's
      review page EN + SL, `python3 scripts/build-guide.py`, both bundles committed.
- [ ] Tests: `tests/test_gate*.py` (0.70 boundary, 0.69 does not combine, disagreement
      keeps the lower read, T0 untouched, auto-bind unboosted), migration test,
      `tests/test_web.py`, `tests/test_docs_sets.py`. Full suite (migration).
- [ ] Docs: `docs/dentalia-schema-sketch.md` (evidence), `docs/dev/changing-things.md`.
- **Done when:** on the eval set the text gate puts 84% +/- 2 points into production
  with 0 wrong gate fields of 19 labelled, and scans 24 +/- 1 of 34 (Task 8).

### Task 6. Prompt caching - 5 h / 0.5 h

- [x] `cache_control` on the first system block; without hints `_system` returns a
      bare string today, so it becomes a one-block list (update the pin
      `test_no_hints_leaves_the_request_exactly_as_it_was`).
- [x] Check Haiku 5.5's minimum cacheable prefix first: the image prefix is 3,696
      tokens and may fall under it. Cache read and write prices are already in
      `economics.py` (verify).
- **Done when:** the first real run shows `cache_read_input_tokens` above 0 on the
  calls that can cache.

### Task 7. Re-extract command - 6 h / 0.6 h

- [x] `python -m app.cli reextract` for a selector: DoCs whose `cert_number` came
      from T1 and is null; scans whose last result was `not-a-compliance-document` on
      empty text; or a list of content hashes. Dry run by default, counts printed,
      `--apply` enqueues `extract.doc` (terminal jobs do not block re-enqueue).
- [x] Sync mode only: in batch mode `batch_ref` has no revision, so a resolved row
      returns the old results; the command refuses there.
- [x] Tests: `tests/test_cli*.py`. Docs: `docs/runbook.md`.

### Task 8. Quality and parity check against the evaluation - 8 h / 0.8 h

- [ ] `tools/haiku_compare.py`: drop the 7 `cert_number` entries of `LABEL_OVERRIDES`
      (ruling A restores those labels); add 533068 `basic_udi_di` `++E2265330681KSCH`.
- [ ] Run the built ladder on the same 79 text documents and 34 scans. New request
      shapes miss the eval cache, so this calls the API (under $1, Batch API, capped);
      approved by Denis 2026-10-13 at under $1.
- [ ] Quality first, per field, on the labelled documents: accuracy of the new ladder
      against today's ladder (Haiku 4.5 + Sonnet, from the stored and cached reads) and
      against Sonnet reading every open field; every field that ends differently
      listed with its quote.
- [ ] Then routing and gate: documents decided automatically, wrong gate fields,
      Sonnet calls, disagreement counts, against section 15.
- **Blocks the deploy:** any gate field (`type`, `regulation`, `coverage_scope`) or
  `ref_list` / `basic_udi_di` less accurate than today's ladder; any wrong gate field
  among the 19 labelled; documents decided automatically below today's.
- **Done when:** a short report sits next to the research doc.

### Task 9. Replay on production's review queue - 8 h / 0.8 h

- [ ] Read-only selection on the server: the 105 documents held by gate confidence and
      the 129 text documents held by `no-item-identifier` (2026-10-12 reading; re-read
      the same day as the replay). Copy their PDFs read-only (`rsync` from
      `/srv/compliance/archive`) into a gitignored local folder; delete it afterwards.
- [ ] Run the built ladder on them (Batch API, capped below $1: approved by Denis
      2026-10-13 at under $1; trim the set to fit if the estimate exceeds it). Nothing is
      written to the server: no jobs, no registry, no `fetch_log`.
- [ ] Per document: does the gate confidence now clear 0.92; is a REF list or UDI now
      found, and does it overlap the catalogue for that manufacturer (read-only query);
      which fields changed from the stored value, with quotes.
- **Report:** how many would leave review, how many stay and on what, any field the
  new ladder reads worse than the stored one. Deploy gate together with Task 8.

## After deploy (server, Denis's go for each step)

1. Deploy; `MODELS_T1` on the server follows the new default; `EXTRACT_MODE` stays sync.
2. `reextract --doc-cert-null --include-published` the 654 DoCs T1 read under the old
   prompt (293 filed, 226 staged, 134 published, 1 superseded on 2026-10-12); the
   published ones only because Task 5's guard exists.
3. The 281 dropped scans: `reextract --dropped-scans --limit 40 --apply` first to measure
   how many end in review (today 95% of read scans do), then the rest.
4. Read the review queue before and after, against the 2026-10-12 reading (1,039).

## Risks

- No `MODELS_*` key reaches the server's containers, and none is added (Denis,
  2026-10-12): the new ladder's settle and agreement thresholds were measured on Haiku
  5.5, so switching T1 back to Haiku 4.5 alone would run an unmeasured flow. Rolling
  back means redeploying the previous commit.

- The scan gate rests on 4 labelled scans, one probable label error among them.
- Batch mode is off on the server (`EXTRACT_MODE=sync`, 2026-09-29); the batch path is
  built and tested but not exercised in production.
- Haiku 5.5 prices and cache minimum are unverified until Tasks 1 and 6.
- Migration number collisions with other worktrees at merge.
- Server cost doubles against batch figures; still well under today's.

## Total

| | human h | Claude Code h |
|---|---|---|
| Tasks 0-9 | 116 | 11.6 |

Neither column covers Denis's review, the deploy, or the post-deploy runs.
Order: 0, then 1, 2, then 3 and 4 (both need 1 and 2), then 5 (needs 3 and 4), 6, 7,
then 8 and 9 (the deploy gate).
