# Phase 0 Close-out + S1.0 VALIDATE Implementation Plan

**Goal:** Close Phase 0 (S0.4 done-criterion: persisted DB-proof spine run, Basic-UDI-DI grouping-signal check, G4 `mfr_ref` write-up, PHASES.md close-out) and implement S1.0 — the full VALIDATE v3 rule set replacing the S0.3 stub.

**Architecture:** Tasks 2 (live run), 3+4 (doc sections), and 6 (VALIDATE code) are independent and can be worked in parallel. Task 5 folds everything into PHASES.md/followups afterwards. Parallel work leaves its changes in the working tree; commits are made once, centrally.

**Tech Stack:** Python 3.12 target (host venv 3.11 acceptable for tests), psycopg 3 raw SQL, pytest against real compose Postgres, Anthropic API (sync transport) for the live run.

## Global Constraints

- CLAUDE.md invariants 1–12 apply to every task. Most load-bearing here: only GATE handlers write `document`/`item_document`/`evidence` (inv. 1); REF gate is pair-scoped `(canonical_manufacturer, mfr_ref)` or Basic UDI-DI match (inv. 3); never downgrade — null dates → `downgrade-uncomparable`, never compared (inv. 4); supersession only within identical (coverage subject, type, regulation), MDR never supersedes MDD (inv. 5); job payloads immutable, additive-only (inv. 9).
- S1.0 must not change the `validate.doc` Consumes/Emits shape — same payload keys in, `gate.candidate` out; new payload fields additive-only.
- Parallel work leaves its changes in the working tree; the commits are made centrally, in the logical units of Task 1.
- Parallel work does not edit `PHASES.md`, `tasks/followups.md`, or `docs/dentalia-findings-phase0.md` outside its assigned sections (conflict avoidance; ownership is listed per task).
- Live-spend guard (Task 2): abort and report if cumulative run spend exceeds $1.50.
- Docs edits are targeted corrections/additions — never rewrite unaffected sections.

---

### Task 1: Commit the pending follow-up batch (do this first, on its own)

**Files:** all currently-uncommitted changes; no new code.

Five logical units, in order:

- [ ] **Unit 1** — `git add app/cli.py tests/test_cli.py` → commit `CLI: clean error message for unknown job type (closed enum, exit 2)`
- [ ] **Unit 2** — `git add web/app.py tests/test_web.py Dockerfile.web` → commit `Web: LIKE-escape status-board search; add container healthcheck`
- [ ] **Unit 3** — `git add Dockerfile docker-compose.yml` → commit `In-container pytest: test image stage + compose test profile`
- [ ] **Unit 4** — `git add tests/test_extract_handler.py tests/test_tiers.py` → commit `Tests: extract-handler/tiers read committed corpus fixtures (CI-safe)`
- [ ] **Unit 5** — edit `tasks/followups.md`: move the five resolved items ([web-ui] ILIKE escape, [web-ui] healthcheck, [s0.1] CLI enum message, [s0.1] in-container pytest, [extract-tests] corpus_pdf→fixture_pdf) to `## Done` as `[x]`; then `git add PHASES.md tasks/followups.md` → commit `Status: PHASES.md session updates + followups housekeeping`

Precondition verified: full suite green (336 passed, 4 xfailed) on 2026-07-13.

---

### Task 2: DB-proof spine run (persisted, live LLM, ~$0.40)

**Files:**
- No product-code changes expected. If a crash bug blocks the run, the minimal fix + failing test is in scope; anything larger → stop and report.
- Output: DB rows + a results report (returned as the agent's final message, NOT written into the findings memo — Task 5 folds it in).

**Interfaces:**
- Consumes: committed corpus at `tests/fixtures/corpus/` (22 PDFs), handlers `backfill.scan → extract.doc → validate.doc → gate.candidate` already registered in `app/workers/runner.py`.
- Produces: rows in `fetch_log`, `extraction_attempt`, `extraction_cost`/`extraction_spend`, `document`, `evidence`, `item_document`, `manual_task`, `audit_log`; a numbers report for Task 5.

- [ ] **Step 1: Bring up Postgres + migrate.** `docker compose up -d postgres` (wait healthy), then on host: source `DATABASE_URL` from `.env` (points at localhost compose Postgres; never print secret values) and run `.venv/bin/python -m app.cli migrate`. Expected: migrations applied through 010.
- [ ] **Step 2: Sanity check.** `.venv/bin/pytest tests/test_backfill_handler.py tests/test_extract_handler.py -q` → all pass.
- [ ] **Step 3: Configure live run.** Export `ANTHROPIC_API_KEY` from `.env` (present there; not in shell by default). `DENTALIA_EXTRACT_MODE` stays `sync` (default). Models per config defaults: t1 `claude-haiku-4-5`, t2 `claude-sonnet-4-6`.
- [ ] **Step 4: Enqueue the scan.**
```bash
.venv/bin/python -m app.cli enqueue backfill.scan "backfill:corpus-dbproof-1" \
  --payload '{"drive_folder": "/srv/dentalia/tests/fixtures/corpus"}' \
  --priority interactive
```
- [ ] **Step 5: Run the worker until drained.** Start `.venv/bin/python -m app.workers.runner` in background; poll `SELECT count(*) FROM job WHERE status IN ('pending','running')` until 0 (timeout 30 min); stop worker.
- [ ] **Step 6: Verify persistence.** Run and record:
```sql
SELECT count(*) FROM fetch_log WHERE source='backfill';                        -- expect 22
SELECT tier, count(*) FROM extraction_attempt GROUP BY tier ORDER BY tier;
SELECT * FROM extraction_spend;
SELECT round(sum(cost_usd)::numeric,4) AS total_usd,
       count(*) FILTER (WHERE cost_usd IS NULL) AS unpriced FROM extraction_cost;
SELECT type, status, count(*) FROM job GROUP BY type, status ORDER BY type, status;
SELECT status, count(*) FROM document GROUP BY status;
SELECT count(*) FROM evidence;
SELECT kind, count(*) FROM manual_task WHERE status='open' GROUP BY kind;
SELECT event, count(*) FROM audit_log GROUP BY event;
SELECT id, type, last_error FROM job WHERE status='dead';
```
- [ ] **Step 7: Two-cycle check (C2/C3/AC5).** Enqueue again with dedupe key `backfill:corpus-dbproof-2`, drain, verify: job result reports `emitted=0, seen=22`; `extraction_cost` row count unchanged; spend delta $0.
- [ ] **Step 8: Report.** Total $, per-tier doc counts, disposition/status distributions, dead jobs with reasons (the truncated-JSON robustness issue is a KNOWN open followup — report occurrences, do not build the streaming fix here), two-cycle result.

---

### Task 3: Basic-UDI-DI grouping-signal check → findings memo section

**Files:**
- Modify: `docs/dentalia-findings-phase0.md` — insert new section `## Basic-UDI-DI grouping signal (S0.4 check)` between `## Verification hooks` and `## Open / hand-offs`; update the `## Open / hand-offs` bullet that lists the grouping-signal check as open.

**Interfaces:**
- Consumes: `tests/fixtures/corpus_manifest.py` ground truth; `docs/dentalia-workflow-structured-v2.md:129-131` (hypothesis); `tasks/followups.md:19` (BC export facts); memo per-field table (basic_udi_di 17/20 = 85%).
- Produces: a verdict section Task 5 references when closing S0.4.

- [ ] **Step 1:** Re-derive the doc-side numbers from `corpus_manifest.py` (do not trust this plan's cache): count fixtures with non-null `ground_truth["basic_udi_di"]` (expected 6/22: VOCO Ceramic Bond, GC New Metal Strips, PLANMECA Viso G1, KOMET 532862 + 533068, IVOCLAR IPS e.max Ceram) and cross-tab with `coverage_scope`.
- [ ] **Step 2:** Write the section covering: (a) hypothesis verbatim quote (workflow-structured v2: Basic UDI-DI = the group/family key certificates and DoCs reference); (b) doc-side evidence: ~27% of the committed compliance corpus states a Basic UDI-DI, extraction accuracy 85% (17/20), T0-only hit rate 9% (imports-corpus-analysis n=109) so it is a T1/T2 field; (c) catalogue-side: the real LJ export has NO UDI/Basic-UDI-DI column at all; (d) verdict: confirmed as a document-grouping key (docs referencing the same Basic UDI-DI can be chained/grouped and it feeds the C1 gate's second arm for doc-derived groups), NOT usable as an item→group resolution key until UDI data exists catalogue-side (EUDAMED mirror S2.3 or client data) — RESOLVE (S1.2) will lean on name/family + tracker seeding in Phase 1; (e) one-line implication for AC1/G15.
- [ ] **Step 3:** Verify: memo renders coherently (`head`-check the section), no other section modified.

---

### Task 4: G4 `mfr_ref` source write-up + AC1 ceiling → findings memo section

**Files:**
- Modify: `docs/dentalia-findings-phase0.md` — insert new section `## mfr_ref source identification (G4)` directly after Task 3's section; update the `## Open / hand-offs` bullet pointing at followups/sweep for mfr_ref.

**Interfaces:**
- Consumes: `tasks/followups.md:19-22` ([phase0-mfrref]/[phase0-mfrcode], verbatim numbers), `docs/dentalia-manufacturer-code-sweep.md` (coverage summary + recommendation), PRD v3 lines 20/78/272 (AC1 definition: auto-write ceiling bounded by missing_mfr_ref).
- Produces: the G4 deliverable + a concrete AC1 ceiling proposal Task 5 references.

- [ ] **Step 1:** Write the findings subsection: LJ export `Artikli 3.7.2026.xlsx` (19,091 rows): `mfr_ref` = "Dobaviteljeva št. artikla", 44.5% blank; of populated, ~85% are Dentalia's own `item_ref` reformatted; genuinely distinct vendor refs ≈ 8% of catalogue; zero exact/substring matches against real KOMET DoC REFs; manufacturer identity only as opaque code `Šifra proizvajalca` (382 distinct, no names); no UDI column. Key positive: for ~86% of the catalogue `item_ref` is plain-style and, for confirmed-Ivoclar items, IS the manufacturer's material number; tracker cross-ref method → 20 codes CONFIRMED (25% of items), 12 LIKELY (17%), 24 WEAK (26%), 325 UNRESOLVED (32%) — cite `dentalia-manufacturer-code-sweep.md`.
- [ ] **Step 2:** AC1 re-derivation subsection: strict reading (pair `(canonical_manufacturer, mfr_ref)` on the export as-is) → ceiling ≈ 8%. Recommended reading: per-supplier logical mapping (PRD already allows: "mfr_ref logical mapping, per-supplier config" in S1.1) treating `item_ref` as the manufacturer number for CONFIRMED plain-style manufacturers → ceiling ≈ 25% today, growing with each BC/IT-confirmed code. Propose AC1 be renegotiated in those terms (PRD:20 anticipates exactly this).
- [ ] **Step 3:** Client-action list: (a) send sweep doc to Dentalia BC/IT for confirm/correct against the vendor master; (b) request the vendor master table; (c) request UDI/Basic-UDI-DI columns if they exist anywhere in BC.
- [ ] **Step 4:** Verify: only the new section + the one Open/hand-offs bullet changed.

---

### Task 5: PHASES.md close-out + followups fold-in (orchestrator or small agent, AFTER 2–4)

**Files:**
- Modify: `PHASES.md` (S0.4 block, gap register rows G4/G15/G16), `tasks/followups.md`, `docs/dentalia-findings-phase0.md` (cost section: fold Task 2 measured run numbers; flip Open items).

- [ ] **Step 1:** PHASES.md S0.4: append an update line with Task 2's measured DB-proof numbers (rows persisted, $ total, two-cycle ≈ 0) + Tasks 3/4 section pointers; flip status to `DONE`. Mark the client-demo checkpoint met.
- [ ] **Step 2:** Gap register: G4 → filled (memo section; client confirmation pending), G15 → note AC1 proposal now exists in memo §G4, G16 → correct the stale claim (staging/manual/dead tabs EXIST in `web/`; remaining: route naming, auth, KPI board).
- [ ] **Step 3:** followups.md: `[extract-dbproof]` → Done; `[phase0-mfrref]`/`[phase0-mfrcode]` → annotate "folded into findings-phase0 §G4"; stale G16 note (line 3) → Done; add any new followups from Task 2's run report.
- [ ] **Step 4:** Verify: `git diff` review of all three files; no unrelated sections touched.

---

### Task 6: S1.0 — full VALIDATE v3 rule set (TDD)

**Files:**
- Modify: `app/handlers/validate.py` (replace stub internals; keep module path, handler registration, payload contract)
- Modify: `tests/test_validate_handler.py` (keep the 5 existing stub tests passing where semantics carry over; extend with the table below)
- Possibly modify: `app/handlers/gate.py` + its test file — ONLY if the handbook §7 disposition table maps flags to `manual` and `no-item-identifier` needs adding to that mapping. No other gate changes.

**Interfaces:**
- Consumes: `validate.doc` payload `{content_hash, extract_rev, group_id}` (unchanged); `extraction_attempt.fields` for the doc; `item_group` / `item_group_member` / `item_group_refs` view (`migrations/002_ingest.sql:33-56`); `document` table for current-doc comparisons (C6/C7).
- Produces: `gate.candidate` payload — existing keys `{content_hash, extract_rev, group_id, ref_gate, flags, supersedes, route?}` now carrying real values, plus additive key `links: [{item_ref, match_basis, udi?}]` (the shape `handle_gate_candidate` already consumes at `app/handlers/gate.py:234-239`).

**Normative sources — read BEFORE coding, they win over this plan:** `docs/dentalia-pipeline-contract-prd-v3.md:149-166` (rule set verbatim below), `docs/dentalia-job-type-handbook.md:351-413` (§6 pseudo-code + rule table) and §7 (disposition table + flag vocabulary), `tasks/followups.md:51` ([validate-itemid]).

PRD v3 rule set (verbatim, for reference):
1. REF gate (C1): `overlap(ext.ref_list, group.member_mfr_refs)` **or** `ext.basic_udi_di == group.basic_udi_di`. Matching key is always the pair `(canonical_manufacturer, mfr_ref)`. For backfill/email docs (`group_id = null`) resolving against the full index, the document's extracted/known manufacturer **must** scope the lookup — an unscoped REF hit is not a gate pass and caps at staging with flag `ref-unscoped`.
2. Manufacturer-scope pre-rule (C4): `coverage_scope = manufacturer` + no item-level identifiers → binding flow (§7), not per-group gate failure.
3. Date sanity (from ≤ to, plausible ranges); "valid N years from issue" arithmetic only on high-confidence rule text.
4. Never-downgrade (C7): candidate `validity_from` older than current → flag `older-than-current`, never write. If either date is null, no comparison — flag `downgrade-uncomparable`, caps disposition at staging.
5. Supersession (C6): only same (coverage subject, type, regulation). MDR never supersedes MDD — parallel chains; cross-regulation recorded as `related`, never `superseded_by`.
6. Type/regulation consistency (expiry on cert-less DoC → downgrade confidence).
Plus [validate-itemid]: non-manufacturer-scope doc with NO `ref_list` and NO `basic_udi_di` → flag `no-item-identifier`, route to manual review — must NOT silently stage.

**Parametrized test table (write ALL as failing tests first, handbook §6/§7 naming wins on flag names):**

| # | Case | Setup | Expected emit |
|---|---|---|---|
| 1 | C4 binding (regression) | scope=manufacturer, no ids | route=mfr-binding, dedupe `gate:{hash}:{rev}:mfr-binding` (existing test stays green) |
| 2 | no-item-identifier | scope=group, ref_list=[] , udi=null | flags ∋ `no-item-identifier`; disposition path = manual (manual_task via gate) |
| 3 | C1 pass via REF overlap | group members with mfr_refs; ext.ref_list overlaps | ref_gate=true; links only for overlapping members, match_basis=`ref-list` |
| 4 | C1 pass via Basic UDI-DI | group.basic_udi_di == ext.basic_udi_di | ref_gate=true; links for all members, match_basis=`basic-udi-di` |
| 5 | C1 fail | ids present, no overlap, udi mismatch | ref_gate=false; no trusted links; stages |
| 6 | Backfill scoped hit | group_id=null; ext.manufacturer set; matching group under that manufacturer | group resolved; ref_gate=true; links emitted |
| 7 | Backfill unscoped hit | group_id=null; NO ext.manufacturer; REF matches some group | ref_gate=false; flags ∋ `ref-unscoped` |
| 8 | Date sanity | validity_from > validity_to | date flag (per handbook vocab); staging cap |
| 9 | C7 older | current production doc same subject/type/reg, candidate validity_from older | flags ∋ `older-than-current`; supersedes=null |
| 10 | C7 null date | candidate or current validity_from null | flags ∋ `downgrade-uncomparable`; no comparison; staging cap |
| 11 | C6 supersedes | candidate newer, same (subject, type, regulation) | supersedes=<current doc_id> |
| 12 | C6 MDR vs MDD | MDR candidate, MDD current, same subject/type | supersedes=null; recorded as related per handbook (additive payload key if §6 pseudo-code says so) |
| 13 | Consistency | DoC without cert_number but with validity_to | confidence downgrade per handbook §6 rule 6 |

- [ ] **Step 1:** Read the four normative sources; extract the exact flag vocabulary and the §6 pseudo-code emit shape; note any naming conflicts with this plan (handbook wins).
- [ ] **Step 2:** Write the failing parametrized tests (extend `tests/test_validate_handler.py`, same real-Postgres fixtures/style as the existing five).
- [ ] **Step 3:** Run them: `.venv/bin/pytest tests/test_validate_handler.py -q` → new cases FAIL, old five PASS.
- [ ] **Step 4:** Implement `app/handlers/validate.py` rules in §6 pseudo-code order (C4 pre-rule → no-item-identifier → C1 incl. backfill scoping → date sanity → C7 → C6 → consistency → emit).
- [ ] **Step 5:** If handbook §7 requires it, add `no-item-identifier` to gate's flag→manual mapping + one gate test.
- [ ] **Step 6:** Full suite: `.venv/bin/pytest -q` → green (≥336 + new cases).
- [ ] **Step 7:** Report: rule→code map, every place the handbook was ambiguous and what was chosen (these become `[FILLED — needs review]` notes — do NOT edit PHASES.md yourself), edge cases NOT covered.

---

## Execution & Verification

- [ ] Task 1 commits → Tasks 2, 3+4 (the two doc sections together) and 6 in parallel, no commits → Task 5 after 2–4 → final: full suite, spot-check Task 2's DB claims with 2–3 SQL queries, review every diff, commit logical units, update `tasks/todo.md` checkboxes.
