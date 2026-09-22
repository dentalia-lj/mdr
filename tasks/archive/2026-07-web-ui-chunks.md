# Archived — historical chunk log, 2026-07-06 → 2026-07-24

Moved out of `tasks/todo.md` on 2026-08-13. It was 435 of that file's 616 lines and
carried 34 unchecked boxes that were never open work — `todo.md`'s own header had said
so since July, but a stale checkbox reads as a task no matter what the header says.
Kept whole for the record; nothing here is actionable.

---

# HISTORICAL CHUNK LOG (2026-07-06 → 2026-07-24)

Completed work, kept for the record. Unchecked boxes below are stale.

# Standalone Dentalia-branded producer UI

Plan: kept outside the repo.

## Chunk A — standalone web skeleton + status board
- [x] `pyproject.toml`: `web` optional-deps group (fastapi, jinja2, uvicorn[standard], python-multipart); add `web` to wheel packages
- [x] `app/config.py`: add `Web` section (host, port, imports_dir, api_database_url)
- [x] Vendor brand assets: Sora + Work Sans woff2, both dentalia logos, htmx.min.js -> `web/static/`
- [x] `web/app.py`: FastAPI app factory, `GET /healthz`, `GET /` status board (job counts + recent jobs + item_mirror summary), connects as dentalia_api
- [x] `web/templates/base.html` + `status.html`: brand CSS (mint palette, Sora/Work Sans, logo)
- [x] `Dockerfile.web`: slim image, own deps, `python -m web.app`
- [x] `migrations/008_api_role_dev_password.sql`: idempotent ALTER ROLE dentalia_api password
- [x] `docker-compose.yml`: repoint `api`->`web` service, own Dockerfile, dentalia_api conn, 127.0.0.1 bind, ungate from `full`, depends only on postgres+migrate
- [x] Verify A: compose up, curl /healthz and / both 200, branded

## Chunk B — ingest form + generic enqueue
- [x] `GET/POST /ingest`: source(csv|bc_odata), catalogue(LJ|ZG), priority, path-picker/free-text or company+delta_since -> enqueue ingest.run
- [x] `GET/POST /enqueue`: closed job-type dropdown, JSON payload, dedupe key, priority -> generic enqueue
- [x] Templates: `ingest.html`, `enqueue.html`, `_result.html` (HTMX partials)
- [x] Verify B: submit ingest -> job row correct payload/dedupe/priority; no-op worker drains it; redup -> deduped; generic enqueue valid/invalid tag

## Chunk C — tests + docs notes
- [x] `tests/test_web.py`: status 200, ingest valid/invalid+dedupe, enqueue valid/invalid, dentalia_api permission-denied on document insert
- [x] `PHASES.md`: `[FILLED — needs review]` note recording the standalone-container deviation from "one image" (+ G16 gap row)
- [x] Run full test suite, confirm green (94 passed, incl. concurrent EXTRACT work)

## Post-implementation (per CLAUDE.md rule 3)
- [x] List edge cases + suggested test cases actually covered vs not (see below / final summary)

## Chunk D — queue detail/filter (follow-up request)
- [x] `_recent_jobs`: filter by exact type/status (validated against the closed enums before
      reaching SQL) + search by dedupe_key substring (ILIKE) or exact numeric id
- [x] `GET /jobs/{id}`: full job detail — stage (job-type -> pipeline-stage label), status,
      priority, attempts/max_attempts, dedupe_key, timestamps, claimed_by/at, last_error,
      pretty-printed payload (Jinja-escaped, not `|safe` - payload is arbitrary data); 404 on
      unknown id
- [x] `status.html`: GET filter form (type/status/q, bookmarkable via query string) + Stage
      column + id links to the detail page
- [x] Tests: filter by type/status (incl. negative), search by substring/id, bogus filter
      values silently ignored (no 500), detail page content, 404 on unknown id
- [x] Verify: full suite green (246 passed, 4 xfailed)

## Chunk E — staging / manual / dead-jobs tabs (gate.apply producer)
- [x] `tests/fixtures/seed_ui.py`: hand-seeded staged/manual-review/mfr-binding docs +
      evidence + links + manual_task rows + a dead job (the exact file PHASES.md's S1.6
      section already named for this purpose)
- [x] `GET /staging` + `POST /staging/{doc_id}/apply`: staged docs (plain/manual/mfr-binding)
      w/ per-field evidence + item links; staged links on production docs (C5) shown
      separately; decision form -> enqueues `gate.apply`
- [x] `GET /manual`: open `manual_task` list (all kinds); gate-manual resolves via Staging
- [x] `GET /dead` + `POST /dead/{id}/rerun`: dead jobs -> re-enqueue at interactive priority
      (dead is terminal, dedupe key reusable)
- [x] Read `docs/dentalia-s0.3-ui-proposal.md` (written by the S0.3 session for this exact
      handoff) and corrected against the REAL `handle_gate_apply` contract: removed "edit" as
      a standalone decision (it's not one - edits ride with "approve"); added required
      `manufacturer` field for bind-manufacturer; added optional `group_id` for reject;
      switched dedupe key to their suggested `apply:{doc_id}:{decision}` (one active job per
      doc+decision regardless of reviewer)
- [x] Tests: 20 new cases (staging list content, approve/reject/bind-manufacturer payloads,
      edits, invalid decision/decided_by/edits/group_id all rejected w/ no job, manual list,
      dead list + rerun same type/payload/dedupe_key, rerun rejects non-dead)
- [x] **Live-verified against the REAL `gate.apply` handler** (landed mid-session): approve ->
      staged to production; bind-manufacturer -> production + `mfr-scope` production links
      derived to all MD items of that manufacturer (§7b, confirmed via real seeded Ivoclar
      items); reject -> rejected + manual_task resolved + interactive `discover.group` retry
      enqueued from `group_id`. Re-seeded fresh demo data afterward.
- [x] Verify: full suite green (329 passed, 4 xfailed, 0 errors - confirmed the one bad run
      was a harness command-timeout artifact, not a code regression)

---

# EXTRACT-tier test suite (T0 real-corpus + T1/T2 scaffold)

Goal: confidently test the extract/parse worker per tier against the ACTUAL corpus
(`imports/dentalia-sftp`, 1307 PDFs / 13 manufacturers). Decisions (2026-07-06):
T0 now + T1/T2 scaffold · recorded LLM fixtures + fake client · commit a small labeled
fixture set into the repo.

Corpus facts driving the design (measured, probe in scratchpad):
- 299/1307 (23%) are scanned (page-1 <20 chars) -> T2. Concentrated: DENSTPLY/DOC
  196/355, DENTAURUM/DOC 30/30, VOCO/Certificates 8/12, IVOCLAR/IVAG Technical 18/63.
- T0 field hit (n=109): type 81%, coverage_scope 78%, regulation 46%, validity_to 22%,
  cert_number 18%, ref_list 16%, validity_from 9%, basic_udi_di 9%. Dates/ref/udi = T1/T2.
- Two T0 gaps to pin: (a) KOMET `*_Liste_DoC` REF list is a text column, not a pdfplumber
  table -> extract_ref_list returns 0, coverage_scope wrongly = manufacturer; (b) VOCO
  DoCs in the MDR folder that cite EU2023/1542 -> T0 correctly abstains on regulation.

## Chunk T-A — committed fixture set + labeled manifest + conftest wiring
- [ ] Curate ~22 real PDFs -> `tests/fixtures/corpus/<MANU>/<file>.pdf`, covering the
      matrix: text DoC (VOCO MDR/MDD, GC I+IIa, STRAUMANN SL, PLANMECA, NEODENT SL,
      KOMET signed), REF-list DoC (IVOCLAR IVAG), text-col REF gap (KOMET Liste_DoC),
      scanned DoC (DENSTPLY, DENTAURUM DE), ISO/QMS cert (IVOCLAR ISO, DENSTPLY CE,
      3SHAPE ISO), scanned EC cert (IVOCLAR MDR cert, VOCO 93-42 Zertifikat),
      reg-abstain (VOCO Celalux 2023/1542), non-MD cosmetics (LUMIWHITE), MSDS/IFU skip
      (GC MSDS). EXCLUDE anything with third-party PII (NEODENT dobavnica Dr. Čelesnik).
- [ ] `tests/fixtures/corpus_manifest.py`: one entry per fixture — `path`, `doc_class`,
      `is_scan`, `t0` (expected DETERMINISTIC output, asserted exactly now), `ground_truth`
      (full 8-field human-read values, for future T1/T2 accuracy), `notes`. Labels proposed
      from doc content -> flagged for Denis review.
- [ ] `tests/conftest.py`: add `fixture_pdf(relpath)` resolving `tests/fixtures/corpus/`
      (committed, always present -> runs in CI), and a `corpus_manifest` loader. Leave the
      existing local-only `corpus_pdf` fixture untouched.
- [ ] Verify T-A: manifest imports, every referenced fixture file exists, `is_scan` in
      manifest matches `pdf.is_scan()` for each.

## Chunk T-B — T0 real-corpus table-driven tests
- [ ] `tests/test_t0_corpus.py`: parametrized over the manifest — assert `doc_class`,
      `is_scan` routing, and each expected T0 field (value + tier=='T0' + conf) per fixture.
- [ ] Gap regressions as explicit cases: KOMET Liste_DoC -> ref_list currently empty
      (xfail w/ reason + followup, NOT a silent pass); VOCO Celalux -> regulation is None;
      misfiled/non-compliance -> doc_class skip; scanned cert -> is_scan True + rasterizable.
- [ ] Keep existing `tests/test_t0.py` pure-function cases; move the two hard-coded corpus
      paths (ISO_CERT, VOCO_DOC) to read from the committed fixture set so they run in CI.
- [ ] Verify T-B: `pytest tests/test_t0_corpus.py tests/test_t0.py tests/test_pdf.py` green.

## Chunk T-C — T1/T2 scaffold (activates when S0.2 lands the tiers)
- [ ] `tests/fixtures/llm/`: recorded-response JSON format (keyed by content-hash + tier +
      field) + a `FakeLLMClient` reading them; README stating how to record real responses.
- [ ] `tests/test_extract_tiers.py`: guarded by `pytest.importorskip("app.extract.tiers")`
      so it SKIPS cleanly until the tier orchestrator exists, then activates. Cover:
      per-field escalation (T0 hit not re-asked; only missing fields go to T1, then T2);
      evidence completeness (every produced value has archive_url/page/verbatim/tier/
      model_id/confidence/extracted_at — gate rejects otherwise); batch_ref idempotency
      (crash between submit and record -> no resubmit); scanned doc routes straight to T2.
- [ ] Document the opt-in live-accuracy harness as a spec stub (deferred): would run tiers
      against `ground_truth` labels and report per-field precision. Not built now.
- [ ] Verify T-C: suite green with tier tests SKIPPED (modules absent); fake-client unit
      tests for the harness itself pass.

## Chunk T-D — close-out
- [ ] `tasks/followups.md`: KOMET text-column REF parser (T0 template / S1.5 scope);
      DENTAURUM "varnostni listi" file typed IFU vs MSDS — confirm label.
- [ ] Run full `pytest`, confirm green (corpus + committed fixtures).
- [ ] Edge-case list: what the fixture matrix covers vs not (per CLAUDE.md rule 3).

## Open review gates (need Denis)
- [ ] Eyeball the committed fixture PDFs before commit — confirm none are client-confidential
      beyond public regulatory declarations/certs.
- [ ] Review proposed `ground_truth` labels in the manifest (I fill from doc content).

## Status (2026-07-06) — DONE, with a mid-task pivot
- [x] T-A: 22 committed fixtures (`tests/fixtures/corpus/<MANU>/`) + labeled `corpus_manifest.py`
      (t0 verified vs real extraction, ground_truth, KNOWN_T0_GAPS) + `fixture_pdf` in conftest.
- [x] T-B: `test_t0_corpus.py` (table-driven over the manifest; 4 known gaps as xfail); migrated
      `test_t0.py` + `test_pdf.py` corpus refs to committed fixtures so they run in CI.
- [x] T-C (PIVOT): partway through, the concurrent S0.2 work landed real `app/extract/{tiers,llm,
      batching}.py` + unit tests (`test_tiers/llm/batching/extract_handler.py`). So the "importorskip
      scaffold" premise was void. Reworked to the REAL interface: `RecordedLlm` (recorded-fixture
      stub at `extract(tier, doc, filename, missing)`) + `test_tiers_corpus.py` (real
      `run_extraction` ladder over all 22 fixtures, CI-safe). Dropped batch-idempotency test
      (already covered by `test_batching.py`). `tests/fixtures/evidence.py` holds the shared
      evidence contract.
- [x] T-D: extract-tier findings appended to `followups.md`; full suite green
      (227 passed, 4 xfailed; `test_web.py` excluded — needs uninstalled optional `web` deps).
- Edge-case coverage summary delivered in the session response (CLAUDE.md rule 3).

---

# EXTRACT economics logging (measured $/extraction)

Goal: replace the chars÷3.7 cost ESTIMATE with a MEASURED ledger. `resp.usage` is
already returned on every LLM call and currently discarded (llm.py:98-101). Capturing
it is free (no extra API call). Feeds S0.4 findings (per-model cost, two-cycle ≈0 check)
and the S1.6 KPI board "sweep spend vs budget cap". Awaiting Denis approval + stop-point.

## Slice 1 — pure pricing core (2 files, no key, no DB)
- [x] `app/extract/economics.py`: `Usage` dataclass + `Price`/`PRICES` (haiku 1/5, sonnet
      3/15; cache_read 0.1x, cache_write 1.25x) + `cost_usd(...)` (batch 0.5x) + `CallCost`.
      Unknown model_id raises `UnknownModelPricing`. `Usage.from_message` defensive reader.
- [x] `tests/test_economics.py`: 12 tests — exact $ for known token counts (proves the
      $0.004 / $0.037 figures), batch halving, cache discount, unknown-model raises. Landed 2026-07-06.

## Slice 2 — usage capture at the two API boundaries (2 files, key-free via stub .usage)
- [x] `app/extract/llm.py`: both clients accumulate `self.usage_log` + emit a per-call cost
      log line via `_capture`. `extract()` still returns a plain dict — ladder + RecordedLlm
      stub untouched. Unpriced model -> WARNING + tokens kept.
- [x] `tests/test_llm_usage.py`: stub client with fake `.usage`; accumulator + cost + batch
      rate + unpriced-warns + missing-usage-is-zero. Landed 2026-07-06.

## Slice 3 — persist + surface the ledger (4 files, one cohesive unit)
- [x] `migrations/010_extraction_cost.sql`: `extraction_cost` table + `extraction_spend`
      rollup view, both GRANT SELECT to dentalia_api. Idempotency UNIQUE(content_hash,tier,rev).
- [x] `app/extract/tiers.py`: `write_extraction_cost` (idempotent, null cost for unpriced).
- [x] `app/handlers/extract.py`: SYNC drains usage_log at _finalize; BATCH writes per-tier at
      the `done` branch of `_advance_or_defer` (same rev; stable pre-finalize).
- [x] `tests/test_extract_cost.py`: 6 tests vs real Postgres — sync 1 row/tier, MSDS 0 rows,
      unpriced null cost, batch at batch-rate, batch 2-tier idempotent, spend view sums. Landed 2026-07-06.

## Status (2026-07-06) — DONE, all 3 slices
- [x] Full suite green: 292 passed, 4 xfailed (test_web.py excluded — needs optional web deps).
- [x] Follow-ups logged (PHASES.md S0.4 pointer deferred — file had concurrent edits + open in IDE;
      KPI-board wiring; config-driven `[pricing]`; measure the QMS-cert T2 escalation waste).

## Explicitly NOT done (anti-gold-plate)
- No config-driven price overrides yet (constants in economics.py; followup logged).
- No spend dashboard (KPI board owns that, S1.6). No per-request cost alerting.

---

# Phase 0 close-out + S1.0 VALIDATE (2026-07-13)

Plan: docs/superpowers/plans/2026-07-13-phase0-close-s1_0-validate.md

- [x] T1 commit pending follow-up batch (5 logical units)
- [x] T2 DB-proof spine run: backfill.scan -> extract -> validate -> gate, persisted + two-cycle check (~$0.40)
- [x] T3 Basic-UDI-DI grouping-signal section in findings-phase0.md
- [x] T4 G4 mfr_ref write-up + AC1 ceiling re-derivation in findings-phase0.md
- [x] T5 PHASES.md close-out (S0.4 -> DONE, G4/G15/G16 register) + followups fold-in
- [x] T6 S1.0 VALIDATE full v3 rule set (C1-C7 + no-item-identifier), TDD per handbook §6
- [ ] Final verify (suite + DB spot-checks + diff review) and commit logical units

---

# Project docs structure (2026-07-13)

Operational docs alongside the existing design docs; design docs stay in place (paths are referenced by CLAUDE.md/PHASES.md).

- [x] docs/README.md: index of all docs (operational + design), precedence, reading order
- [x] docs/architecture.md: system shape, processes, enqueue graph, tiers, roles, invariants pointer
- [x] docs/runbook.md: compose stack, web UI, CLI, config surface, tests (container + host), corpus
- [x] docs/troubleshooting.md: known failure modes and fixes, grounded in compose/conftest/migrations
- [x] docs/code-map.md: path -> purpose -> tests map of the repo
- [x] README.md: replace stale "Nothing runs yet" with quickstart + docs pointer
- [x] CLAUDE.md: docs-freshness rule + docs/README.md row in the document map

# Pre-S1.7 readiness (2026-07-24)

Decisions from the 2026-07-24 interview (recorded in PHASES.md S1.7 block):
md-unknown = CONFIRMED-mfrs middle path · K1 keeps both denominators · thresholds
tuned from samples before the sweep · AC1 measure-only until BC/IT confirms G4 ·
gate mfr-scope predicate stays `md_flag IS TRUE`.

## Session P1 — ops unblock (mechanical)
- [x] Generate `DENTALIA_WEB_PASSWORD_HASH` + set in `.env` — DONE 2026-08-03. Dev password `dentalia-dev`; hash generated via `docker run --rm caddy:2.8 caddy hash-password` (bypasses compose, which could not parse until the var existed). `$` escaped as `$$` in `.env`; verified the container receives the correct unescaped hash. This unblocked ALL compose commands, not just the web UI — compose interpolates the whole file before running anything.
- [x] Migrate the real `dentalia` DB — DONE 2026-08-03, 010 -> **015** (not 013; 014/015 landed with the audit rulings). pg_dump taken first. Verified row counts identical pre/post, new objects present, existing evidence backfilled to `extract_rev = 1`. Closes [s1.6-migrate-real-db].
- [x] `STORAGE_ADAPTER` — DONE 2026-08-03: Denis ruled the **config default** flips to `local` (`app/config.py` + `.env.example`), so no per-env override is needed. Closes [storage-default].
- [x] `BRAVE_API_KEY` — already present in `.env` (verified 2026-08-03). Note it is only needed for the DISCOVER search rung, which also needs the T1 ranker (P3) before it does anything useful.

## Session P2 — ingest md-unknown middle path (TDD)
- [ ] Config: per-code unknown-processing list (global `process_md_unknown` boolean stays as the master switch); seed from the 20 CONFIRMED codes in `docs/dentalia-manufacturer-code-sweep.md`
- [ ] Handler: unknown-class row processed iff its manufacturer code is listed; excluded unknowns counted on the job report (never silent)
- [ ] Table-driven tests: confirmed-code unknown in, unlisted-code unknown out+counted, `RAZRED`/`NI MP` behavior unchanged

## Session P3 — T1 rankers (TDD, recorded LLM fixtures)
- [ ] [discover-t1-ranking]: ranking prompt + `models.rank` structured-output call injected at `_default_rank` (app/handlers/discover.py)
- [ ] [resolve-t1-ranking]: adjudicator prompt + call at `_default_adjudicator` (app/handlers/resolve.py) — chosen group_id | new | unsure + rationale
- [ ] If room: [extract-manufacturer] — give `ext.manufacturer` a producer so backfill docs stop taking VALIDATE's unscoped branch

## Session P4 — threshold calibration (per the tune-from-samples decision)
- [ ] RESOLVE: hand-label sample name pairs from the LJ export, score rapidfuzz, set `name_accept`/`name_suggest`/`name_candidate_k` (export data only — can run before/parallel with P3)
- [ ] Discovery: run sample groups through live search + the wired ranker, set `rank_threshold`/`topk` (requires P3 + search key)
- [ ] Record chosen values + method (closes G5a "needs review" + [discover-thresholds])

Then S1.7 proper opens with budget-cap wiring (`budget.sweep_cap_eur` pause mechanism — an S1.7 deliverable, currently display-only in web/app.py).

---

# Drift-check + C3 fix + docs sync (2026-07-24)

Scope approved by Denis: Slice 1 (C3 fix) + Slice 3 (docs sync). Slice 2 (evidence-vs-rev) and Slice 4 (rulings) logged in followups.md, not built.

## Slice 1 — C3 fetch-context, TDD
- [x] RED: validate tests — REF-gate miss with requesting group -> fetch-context links for all members; rule-7 no-identifier stays link-less; backfill (group_id=null) unaffected
- [x] RED: resolve tests — staged doc+link suppresses discover; rejected doc or link never suppresses (parametrized)
- [x] GREEN: validate.py fallback links on REF-gate miss (C5 caps at staged structurally)
- [x] GREEN: resolve.py `_has_current_doc_or_candidate` (staged counts, rejected/superseded excluded)
- [x] docs/specs/resolve.md updated for the rename + widened semantics
- [x] Full suite: 586 passed / 1 skipped (recorded in PHASES.md S1.0 block)

## Slice 3 — docs sync (drift-check findings)
- [x] CLAUDE.md: queue/runner line counts, UI scope, scheduler built, G16 closed, extract/ file list, templates note, urls.py
- [x] schema-sketch: report.weekly enum + dedupe row, extraction_cost/spend (verified against 010), mirror_rev sequence (011), scheduler_run §5c (verified against 013), C5 CHECK live, matrix rows, dedupe-key extensions note
- [x] handbook: ingest report shape (real S1.1 counters), email.request dual shapes + namespaces, force_new_group, singleton ratified, fetch outcome rows (lease-wait, recency-skip-linked), sync changelog
- [x] runbook: .env.example claim scoped (tuning keys deliberately code-default-only)
- [x] architecture.md queue line count; code-map 12 manufacturers + prompts/ row; compose web comment
- [x] followups.md: 5 new ruling entries + [gate-evidence] upgraded + [resolve-singleton-basis] closed

---

# /staging pagination + lazy detail (2026-08-12)

Measured problem: `GET /staging` returned 7.83 MiB of HTML — 7.58 MiB of it the
grouping-suggestions section (5,831 open rows, 5,888 `<form>`, 5,861 `<pre>`,
~19,900 `<tr>`). Server render was only 0.39s; the freeze was browser-side DOM
construction plus HTMX scanning every form on load. Root cause: no LIMIT in
`_grouping_suggestions` and every row rendered fully expanded.

Approved by Denis: paginate + lazy detail, search by item_ref, one pass.

- [x] `_staged_docs_page` / `_open_suggestions_page` — single bounded query + count each
      (docs query kills today's 3-per-doc N+1: 171 round-trips -> 2)
- [x] Suggestions order `created_at, id` (created_at alone is not a total order —
      OFFSET paging over ties skips and repeats rows)
- [x] `STAGING_PAGE_SIZE = 50`
- [x] Routes: `GET /staging` shell; `/staging/docs`, `/staging/suggestions` list
      partials; `/staging/{doc_id:int}/detail`, `/staging/suggestion/{sid:int}/detail`
      detail partials. Both POST routes unchanged (invariant 1)
- [x] Collapse via native `<details>` + `hx-trigger="toggle once"`, no JS
- [x] Search box on suggestions: `item_ref ILIKE`, `q` carried through pager links
- [x] Staged-links-on-production: LIMIT 200 + explicit "showing N of M" (no pager —
      flat table, no forms, nothing to expand)
- [x] Retarget 3 existing tests whose assertions move into the detail partials
- [x] New tests: page bounds, no overlap across pages, search filter + count,
      both detail partials, 404 on unknown doc_id
- [x] docs/runbook.md + docs/code-map.md

## Slice 2 — simplify the review page for the client (2026-08-12)

Denis: "too complicated for them ... they should know immediately what it means,
single button to apply." Answers: keep English (i18n later), Approve/Reject both
on the row and in the panel, one-click reject with no reason, real form fields
instead of the JSON edits box, `user:admin` until logins land.

- [x] Plain-language vocabulary: DOC_TYPE_LABELS / REGULATION_LABELS /
      COVERAGE_LABELS / FLAG_EXPLANATIONS; unmapped flag degrades to a visible
      sentence, never to silence
- [x] Manufacturer derived from the linked item (BC code -> manufacturer_alias),
      not parsed from the PDF — Denis's option (b)
- [x] Approve/Reject as submit buttons carrying `decision`, placed OUTSIDE
      <details> so a click decides without toggling the row
- [x] Panel: what-this-is, products WITH names, open-the-PDF, corrections,
      decision; everything engineer-facing behind "Technical details"
- [x] Structured edits + `edit_baseline` so only changed fields reach `edits`
      (echoing unchanged values would write T3 human evidence over machine
      extraction and permanently outrank it)
- [x] DEFAULT_DECIDED_BY = "user:admin"; the empty-decided_by 422 case retired
- [x] mfr-binding with no manufacturer: Approve disabled, never bound to "None"

## Slice 3 — the file link (2026-08-12, reported broken by Denis)

- [x] Root cause: templates rendered `document.archive_url` into an href, but
      that column is a storage handle (host path / file:// URI), never a URL —
      every "file" link in the UI was dead
- [x] `GET /documents/{doc_id}/file`, constructed link, containment-checked
- [x] Relocation is config, not code: `WEB_PATH_REWRITES` (+ IMPORTS_HOST_ABS in
      compose) and `WEB_ARCHIVE_ROOT`/`WEB_IMPORTS_DIR`
- [x] Hash-verified fallback: the stored path is a HOST path that does not exist
      in the container, so tails are searched under each root and accepted only
      when sha256 == content_hash (a wrong document must be impossible)
- [x] Visible failure for lazily-loaded panels: a 5xx/network error now writes a
      message into the row instead of leaving "Loading..." forever

# Matching correctness after the GC pilot (2026-08-13)

All $0: matching and validation re-run free over persisted extractions, so
nothing here needs a paid re-extraction. Spend stayed at $5,4369 throughout,
last LLM call 2026-08-12 13:46.

## Slice 1 — item_ref is a REF-gate comparand (client C12, Denis 2026-08-13)

Client: the supplier article number is not the important one, the primary item
number is, because that is what is printed on the physical article.

- [x] PRD v3 C1 + §6 rule 1 + §9, CLAUDE.md invariant 3, migration 023
- [x] `_member_article_basis` — mfr_ref first (7.450 members hold both equal, so
      the stronger claim wins), else item_ref at the new `ref-item` basis
- [x] `_manufacturers_holding_refs` searches both columns — it is the AMBIGUITY
      guard, and a guard seeing fewer holders than the linker opens the hole it
      exists to close
- [x] `gate.TRUSTED_BASES` gains `ref-item` (the DB CHECK is only half the
      permission; a basis missing from the tuple stages silently)
- [x] Guards re-derived over item_ref rather than reused, per plan Task 6
- [x] Companion docs: schema sketch, handbook, code map
- [x] Applied: validate.doc re-emitted over 148 extractions, 43 -> 88 links
- [x] Recorded that the production half is unreachable until the manufacturer
      resolves — `ref-item` needs the SCOPED path

## Slice 2 — a capped LLM sample must not replace a T0 enumeration

Denis, from document #258's review page: "ref list has 7 items, but evidence
shows even more". T0 reads 53 codes; T1's capped 46 was stored over it.

- [x] Root cause, three links: `extract_ref_list` stamps 0.9 against a 0.95
      threshold so T0's list escalates on EVERY document; the prompt caps the
      LLM and says a deterministic parser owns the full list; `merge` lets a
      different non-empty later value win. The premise was never enforced.
- [x] `merge` shrink guard scoped to `_ENUMERATED_FIELDS`; longer wins whichever
      tier found it (KOMET needs the LLM to be able to add); never unioned,
      since evidence carries one verbatim/page/tier per field
- [x] Cap 40 -> 150 on the measured distribution, not on the largest number seen
- [x] NOT done deliberately: raising T0's ref_list confidence. With the shrink
      guard, escalation can only ADD codes, so it is a free safety net.
- [x] `repair-ref-list --apply`: 13 documents, codes 1.502 -> 3.118, $0
- [x] Logged that no cap covers the tail — one document holds 1.121 codes

## Slice 3 — a document covers every item it names, not one group's (IN PROGRESS)

Denis: "all items must get the document attached if a document covers multiple
items". Measured after slice 2: 3.118 codes reach 323 items, 96 links written,
**228 items missed** across 23 documents. Document 204 reaches 20 items across 5
groups — all the SAME manufacturer — and links 1.

- [x] PRD §6 rule 1 + handbook: coverage follows the REF list, not our grouping
- [x] `_links_for_manufacturer` replaces resolve-then-gate at both call sites;
      `_manufacturers_holding_refs`' exactly-one-holder guard untouched
- [x] Lowest matching group stays the document's primary, for provenance
- [x] Dedupe an item appearing in two groups, strongest basis wins
- [x] Mutation M3 (scan every group) was initially GREEN — the existing
      cross-manufacturer test only covers the UNSCOPED path, where the handler
      bails before any group is scanned. Added the scoped test; M3 now red.
- [x] Applied: links supported by the current rev 96 -> 113
- [ ] `multi-group-match` loses its reason to cap — decide when it goes live,
      everything caps at `ref-catalogue` until the manufacturer resolves anyway
- [ ] TWO measurement errors of mine to carry forward, both now logged:
      (a) the "228 items missed" estimate ignored the cross-manufacturer guard;
      3 documents hold 210 of those items and correctly link none.
      (b) my ad-hoc re-emit re-ran STALE revs (the dedupe key carries the rev),
      which surfaced `[gate-never-retracts-a-link]`: 134 rows in item_document,
      113 supported by the current rev, 21 stale. Re-emit the latest rev only.
      `repair-ref-list` does this correctly; my throwaway script did not.

## Slice 4 — a link must not outlive the extraction that justified it

- [x] Migration 024: `retracted` link status, distinct from `rejected`
- [x] GATE retracts STAGED links the current rev omits; production never
      auto-retracted (approving is what promotes, so staged == undecided)
- [x] `_is_current_rev` stops a superseded candidate writing at all
- [x] Mutations M1/M2/M3 each red on their own test
- [x] Applied: 21 retracted, 113 live, 113 supported by the current rev, $0

## Next, not started

- [ ] `ext.manufacturer` Phase 2 — the production-links unlock AND pre-spend
- [ ] T1 required-field-without-page (2 documents dead per 130) — pre-spend
- [ ] Re-extract the 4 coverage_scope + 3 bad-date documents (~$0,36)
- [ ] Queue stage-rank ordering (breadth-first drain)
- [ ] The remaining 1.158 corpus PDFs (~$46 sync, ~$26 batch — batch never
      tested against the real API, 0 of 231 calls)
