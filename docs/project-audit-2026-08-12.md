# Project audit — 2026-08-12

> **CLOSED — dated snapshot, do not treat as current state.** Audited against the tree of 2026-08-12; superseded within hours by a concurrent session and closed the same day. Several items below were true when written and were fixed the same day, and §9's "confirmed open" list went stale before it was published. One claim was simply wrong: the todo.md "four of 28" bullet, since removed. Current build state lives in `PHASES.md`; the live defect register is `tasks/followups.md`. Kept for the method record and the trajectory analysis in §2, not as a to-do list.


Full-project drift and readiness audit: goals vs build, code vs normative contracts, and what blocks the paid backfill / corpus import. Produced by a fanned-out verification workflow (4 survey readers, 4 code-drift checkers, a backfill-blocker analyst, an in-container test run) with an adversarial verify pass over every finding: each claim below marked **confirmed** was independently re-reproduced at the cited location by a second agent; two findings were refuted during verification (they had landed as commits mid-audit) and are excluded; one finding lost its verdict in transit and was re-verified by hand before inclusion.

**Snapshot audited:** branch `corpus-cold-start` as of 2026-08-12 plus the uncommitted working tree (the /staging pagination rework). A concurrent session was committing while the audit ran — four commits landed mid-run; everything below was re-checked against the last of them afterwards. Line numbers cite that state.

**Landed after this audit was written** (verified present in git, not re-audited here), all on 2026-08-12: backfill now archives corpus bytes through the storage adapter, a repair pass fixes the 57 documents that pointed at a corpus path (plan Task 5), and coverage_scope is deduced deterministically (plan Task 4). The batch-merge blocker in §5-C was fixed in this session. Together these close every gating task: **the code gate on the paid backfill is clear.** Items are struck through in place below; the rest of the report stands as written against the audited tree. Two of the closures need a ruling on the record rather than in a commit body — see §8.

**Trust notes.** Test suite: `docker compose --profile test run --rm --build test` → **1030 passed, 1 skipped, 0 failed** (247 s, run this day against the dirty tree). Registry-state figures (57 staged docs, 12 links, 33 manual tasks, 5,648 groups) come from PHASES.md/todo.md logs dated 2026-08-11/12, **not** re-read from the live database — re-verify before enqueueing the sweep.

---

## 1. Verdict

1. **The architecture contract holds.** Every load-bearing invariant was verified in code, most of them structurally, not just by convention: registry writes confined to `gate.py` (role grants in migration 007 + denial test), untrusted match bases barred from production by DB CHECK (migration 021), active-scope dedupe partial index, payload immutability, fetch-ledger no-refetch on all three producers, LLM containment (LLMs never fetch), closed 16-value job-type enum consistent in both directions, all 16 dedupe-key shapes string-exact to PRD §0, every handler's emits within its PRD Emits row. No blocker-level contract violation exists in the pipeline spine.
2. **Progress claims are honest.** PHASES.md tracks git faithfully (including self-reported incidents); Phase 0 and Phase 1 S1.0–S1.6 are built as claimed, S1.7 is part-done (playbooks yes, sweep no, P2/P3/P4 pre-work no), Phase 2 is unstarted. There was no 4-week July gap — the repo shows 16 working days across 5.5 weeks; the stale impression comes from origin/master, unpushed since 2026-07-21.
3. **Drift is real but clustered**, not systemic: (a) `archive_url` provenance — three ingestion paths write non-archive locations into the column defined as "our stored copy", weakening end-state output 2; (b) two supersession/gate.apply edge-case holes; (c) economics is measured but unenforced. Details in §5.
4. **The paid backfill is not ready.** Of the extraction-correctness plan's five gating tasks, three are landed (two of them today, mid-audit); Tasks 4 and 5 are blocked on two rulings only Denis can give; and the audit found one blocker the plan does not list (batch-path merge bypass). Plus a set of operational traps that have each already fired once. Details in §7.

---

## 2. Progress vs plan

| Phase / session | Claimed | Verified |
|---|---|---|
| Phase 0 (S0.1–S0.4) | DONE 2026-07-13 | Commits check out; corpus test bed + findings memo exist |
| S1.0 VALIDATE full rules | DONE 2026-07-13 | Built; 43K-line test file, real Postgres |
| S1.1 INGEST / S1.2 RESOLVE | DONE 2026-07-15 | Built; proven on the 19,091-row LJ export |
| S1.3 DISCOVER / S1.4 FETCH | DONE 2026-07-21 | Built; **no live search run yet** (Brave key untested, discovery held) |
| S1.5 SCHEDULER / S1.6 UI+KPI | DONE 2026-07-23 | Built; scheduler runs only under compose profile `full` |
| S1.7 playbooks slice | DONE, merged | Verified merged; **sweep not started**; P2 (md-unknown gating), P3 (T1 rankers), P4 (threshold calibration) not built |
| Out-of-band slices (upload, vendor master, pre-import foundations, registry views, playbook enrichment, corpus cold start) | DONE | All verified merged into master 2026-08-04..11 |
| Phase 2 (S2.1–S2.5: playbook routing, onboarding, EUDAMED, EMAIL, Zagreb) | unstarted | Confirmed — enum values + no-op handlers only |

**Actual trajectory (git):** 253 commits over 16 working days, 2026-07-06 → 2026-08-12. Bootstrap sprint (45 commits, 07-06/07), stage-per-session mid-July, spec week late July, August intensification (37-commit day 08-10, three merges 08-11, extraction fixes 08-12). All four non-current branches are fully merged and deletable; `corpus-cold-start` is 10+ ahead / 4 behind master (behind by merge commits + one docs commit only).

**Registry state (from logs, not live-verified):** first real corpus run, GC only — 57 docs all staged, 0 production, 12 ref-catalogue links (staged-capped by design), 33 open gate-manual tasks; 87 GC extract jobs deliberately deleted (~$4 to re-enqueue); the other 11 corpus brands not backfilled.

**Risk worth acting on:** origin/master has received nothing since 2026-07-21 — local master is **157 commits ahead of the remote**. Three weeks of work exist on one WSL disk.

---

## 3. Goal coverage — owner's stated goal vs build

| Goal | Status | Notes |
|---|---|---|
| BC `item_ref` → N evidenced documents | **built** | item_document M:N + per-field evidence rows, wired end-to-end, tested; archive_url provenance caveat (§5-A) |
| Cert scope levels: brand / group / item | **built** | `coverage_scope` + C4 mfr-binding flow + REF-gate per-item links; `item` scope has no deterministic producer and no CHECK constraint (low) |
| Validity capture + expiry detection | **built** | validity dates, cert-inherited expiry (COALESCE through `cert_doc_id`), daily scan, /expiry page |
| Renewal request flow ("request updated documents") | **designed-only** | `renewal_request` has zero code writers; email.request/poll are no-ops, correctly config-gated off; scheduled S2.4. Registry currency is manual until then. Also unresolved at goal level: DoCs carry no expiry by regulation (E22/E23/E24 open) |
| Ingest paths | **partial** | csv ingest, backfill.scan, web upload: end-to-end. fetch.url: code-complete, never live-run. email.*: absent (Phase 2). bc_odata: stub, yet selectable in the web form (dead-letters loudly) |
| Reference matching | **partial** | (manufacturer, mfr_ref) REF gate + fuzzy resolve ladder: built. Basic UDI-DI: code-complete, data-inert (nothing populates it). LLM ranking: unwired on both RESOLVE and DISCOVER (raise NotImplementedError; must land before the live sweep, not before the corpus backfill) |
| Hash-addressed archive under our control | **partial** | LocalFsStore + volume + read-only serving: built. But backfill bypasses it entirely and live-fetch/upload paths record wrong archive_url (§5-A); GoogleDriveStore is a G7-gated skeleton |
| Supersession chains, append-only, 10-y | **built** | Writer-enforced C6, sticky status CASEs, no registry DELETEs anywhere, audited; two edge-case holes (§5-B) |
| Review UI (staging, manual, dead, KPI) | **built** | All boards exist and are tested (~90 web tests); the /staging pagination rework is finished but **uncommitted** |

---

## 4. What passed clean (checked, no drift)

Queue/job contracts came back clean across all six checks: emits conformance (26 enqueue sites traced), payload immutability + batch_ref side-table discipline, dedupe keys string-exact, closed enum both directions, active-scope dedupe, web-as-producer-only. The only queue findings are prose/doc items. Fetch-ledger discipline (ETag/304/recency/hash-dedupe) holds on all three producers. T0 templates are engine-agnostic playbook data with a key-whitelist test. Economics measurement is honest (unknown model raises, unpriced calls surfaced on the KPI board). The sync-path tier-merge fix (2026-08-11) is present and mutation-tested.

---

## 5. Confirmed code-vs-contract drift

All findings below survived adversarial verification. Format follows the drift-check protocol. No fixes have been applied.

### A. archive_url provenance (end-state output 2)

| Where | Guard violated | Severity | Fix direction |
|---|---|---|---|
| `app/handlers/gate.py:72-88` | Invariant 2 archive_url = "our stored copy" | **high** | GATE's fallback reads `fetch_log.url_normalized`; VALIDATE never threads archive_url, so live-fetch docs record the **source URL** and uploads record the sentinel `upload:{hash}` as document/evidence archive_url. The `[gate-threading]` followup closure (followups.md:136) is **incorrect** — the payload value dies at extract; reopen it. Mechanism choice needed: extraction_attempt column vs payload threading vs fetch_log archive column |
| ~~`app/handlers/backfill.py:86`~~ | End-state output 2, hash-addressed archive | **LANDED 2026-08-12** (= plan Task 5) | backfill.scan wrote the raw host path under `imports/dentalia-sftp`; all 57 existing docs' archive links 404'd. Option A was taken: bytes now go through the storage adapter, and a repair pass fixes the 57 existing rows. Landed by a concurrent session after this audit; not re-audited here |

### B. Supersession / GATE edge cases

| Where | Guard violated | Severity | Fix direction |
|---|---|---|---|
| `app/handlers/gate.py:494-495, 535-575` | Invariant 4 append-only; comment at gate.py:399-405 | **medium** | `gate.apply` approve/bind-manufacturer promotes unconditionally — a later-superseded (or rejected) doc can be resurrected to production with `superseded_by` still set, via a still-open manual task or stale re-delivery. Require status='staged' as precondition, loud otherwise |
| `app/handlers/gate.py:406-411` | Invariant 5 / PRD §10 C6 | **medium** | The task-5 auto-supersede fast path trusts VALIDATE's payload `superseded_by_doc_id` without re-checking the target's (type, regulation, status) at write time — the target can drift between validate and gate (`_upsert_document` mutates type/regulation on conflict). Add the SELECT + fall back to manual on mismatch |
| `app/handlers/gate.py:214-226` | CLAUDE.md invariant 5 wording (and PRD C6 line 181/248) vs PRD §10 | low | Writer re-checks only (type, regulation); coverage-subject identity lives in VALIDATE only, and group membership is mutable. Either extend the writer check or record a ruling that subject identity is VALIDATE's job and align the wording |
| `app/handlers/gate.py:56, 91-104, 263-264` | Invariant 2 completeness | **medium** | Enforcement covers only the 3 REQUIRED_FIELDS; other written values (validity_from/to, cert_number, ref_list evidence) accept silently-defaulted empty verbatim/tier, and `model_id` is never checked even for T1/T2 (which also silently skips calibration). Structural gap — today's producers always emit full bundles. Also: `extracted_at` is stamped at gate-write time, not extraction time |

### C. Economics / extraction

| Where | Guard violated | Severity | Fix direction |
|---|---|---|---|
| ~~`app/handlers/extract.py:133`~~ | The 2026-08-11 sync fix; "Batch API for all sweep work" | **FIXED 2026-08-12** | The batch path merged with a raw `fields.update(...)`, bypassing `tiers.merge` — a batch T1/T2 empty answer erased a T0 hit, the exact 26-of-61 GC data loss the sync fix addressed. Was absent from the plan and from followups; batch tests only stubbed non-empty answers, so nothing was red. Now merges via `tiers.merge`; regression test `test_batch_path_empty_answer_never_erases_the_t0_ref_list` reproduced the erasure first (`'T2' == 'T0'`) and passes after. Residual, logged as `[batch-empty-target-t2]`: the batch T2 branch still lacks the sync path's empty-target guard (cost only) |
| `app/config.py:184-186` | S1.7 "budget caps pause sweep"; €10–40/mo target | **high** | sweep_cap_eur/monthly_cap_eur are display-only — zero enforcement consumers anywhere in app/. Known open item ([budget-caps-unenforced]); decide: enforce before the sweep, or ratify display-only in the PRD |
| ~~`app/extract/t0_templates.py:229-235`~~ | Tier-ladder economics; T0-as-enumerator design | **LANDED 2026-08-12** (= plan Task 4) | coverage_scope deduced at 0.9 always escalated and a 0.4 T2 guess overwrote the deterministic deduction. Closed by resolving the open ruling the *opposite* way from the plan: rather than assigning single-REF DoCs `item`, `item` is now never produced. Evidence: 37 of 57 registered docs carried `item`, which the ground truth never assigns, and two structurally identical 42-REF GC DoCs came back one `item`, one `group` at equal confidence. 52 of 57 stop escalating. Existing rows keep their stored value until re-extracted |
| `app/extract/t0_templates.py:362` + `tiers.py:56,93-101` | T0-as-full-list-enumerator (tiers.py:40) | **medium** | ref_list's flat 0.9 always escalates (paid calls for an answered field), and a non-empty ≤40-code LLM answer replaces a longer T0 enumeration on both transports |
| `app/handlers/extract.py:202-205` + `llm.py` batch client | "Batch API for all sweep work" | **medium** | Transport is a per-worker env var defaulting to **sync** — an operator running the backfill without `DENTALIA_EXTRACT_MODE=batch` silently forfeits the 50% discount; batches are batch-of-1; the batch T2 branch lacks the sync path's empty-target guard |

### D. Registry write confinement

**RESOLVED 2026-08-12 — rejected, module deleted.** Denis's ruling: rather than ratify an invariant-1 exception, remove the module. It existed for 57 rows the registry wipe erased, and the producer bug is fixed at source, so no foreign handle can be written again. Invariant 1 stays absolute. Original finding: `app/repair_archive_url.py` wrote `document.archive_url` and `evidence.archive_url` from outside `gate.py`. The module excepts invariant 1 deliberately, "on a migration's footing" — operator-run, dry-run by default, changing only the storage handle and never a value, link, status or supersession, every change landing as an archive-repair audit row — and explicitly flags itself for review. This audit verified invariant 1 was structurally enforced (role grants + denial test), so a knowing exception belongs in a recorded ruling.

Otherwise clean, with one placement flag: `tools/cold_start_reset.sql` TRUNCATEs 23 tables including the three registry tables from the directory CLAUDE.md documents as read-only (low; deliberate one-off, unwired, but no in-script guard against production rows).

### E. Review-flow defect (verified by hand — its workflow verdict was lost in transit)

| Where | Guard violated | Severity | Fix direction |
|---|---|---|---|
| `validate.py:517-527` → `gate.py:314-315` → `staging.html:110` (HEAD) / `_staging_doc_detail.html:88` (working tree) → `web/app.py:1170-1175` | C4 binding flow usability + wrong-write guard | **high** | Backfill mfr-binding candidates carry manufacturer=None (TARGET has no manufacturer field); the form renders the literal string "None" into a readonly required input; the server rejects only the empty string — one click enqueues a bind to a manufacturer named "None" and promotes the doc. 3 open tasks today; Task 3's landing means every backfilled ISO cert (~60) takes this path. Reject missing/"None" manufacturer server-side; mark the task unactionable when manufacturer is unknown. Already logged as [mfr-binding-null-manufacturer] |

### F. Queue/doc hygiene (all low, all confirmed)

- PRD §0 envelope omits the `result jsonb` column added by migration 019 — one-line PRD edit.
- `dentalia_api` holds an unused blanket UPDATE grant on `job` (payload immutability is convention-only for the web role) — revoke or column-scope.
- `noop.py:3,17` prose says "15 job types" / "sync with 001" while correctly registering 16.

---

## 6. Doc-vs-doc and doc-vs-reality drift

- **ext.manufacturer status conflict:** PHASES.md says "PLANNED, NOT BUILT"; followups [ext-manufacturer-p2] says "Phase 1 shipped 2026-08-11". Code agrees with followups (fold-at-match-time landed; the extraction field did not). PHASES.md also has zero mentions of the ref-catalogue work ([phases-refcatalogue]).
- **Extraction-correctness plan checkboxes are stale:** Tasks 1–3 are committed (2026-08-12) but every plan checkbox is unchecked.
- **[gate-threading] followup is closed incorrectly** — see §5-A; the payload value never reaches GATE.
- **PHASES.md:198** still says the upload slice is "tasks 1–3 of 4… unmerged"; it merged complete 2026-08-04 (contradicts line 184).
- **Archive location has three coexisting states:** workflow-v2 says Drive, config default is local (2026-08-03 ruling), client question F27/G7 still open. One ruling needed before backfill outputs accumulate.
- **Ground-truth downgrade wording** ("flagged, never written") was never updated to invariant 4's "never made current, not never stored" + guarded auto-supersede.
- **C12 ruling vs invariant 3 (strategic):** the client says the primary item number — `item_ref`, printed on articles — is what matters; invariant 3 keys auto-writes on `mfr_ref` (48% identical, 43% of members lack mfr_ref; item_ref reaches 24 GC items vs mfr_ref's 13). Adopting item_ref is a PRD/invariant change with re-derived collision guards — correctly parked as an evaluation task, but this is the largest open assumption under the matching design.
- **Renewal goal vs regulation:** "request updates when a deadline approaches" cannot apply to DoCs (no expiry by regulation) or Class I (no certificate). E22/E23/E24 open.
- Contract-internal ambiguities the audit hit (each needs a one-line ratification): gate.candidate dedupe `{group_id}` = resolved vs consumed group; ingest dedupe prefix divergence (web vs scheduler); handbook's incrementing discover cycle vs PRD's constant 0; PRD §7's production qualifier scope across trusted bases; schema access-matrix row for email.poll missing renewal_request.

---

## 7. Paid-backfill readiness — the gate, ranked

The plan's own criterion: everything that changes `extraction_attempt.fields` or `archive_url` must land before the ~$56 sweep, because re-extraction is paid and re-validation is free.

**Landed (verified against the 2026-08-12 tree):**
1. ~~Task 1~~ GC stitched tables (fixture-tested)
2. ~~Task 2~~ `_looks_like_ref` UDI/prose guard
3. ~~Task 3~~ regulation "n.a." survives for ISO certs
4. ~~Batch merge bypass~~ — `extract.py:133` now merges via `tiers.merge` (TDD: erasure reproduced, then fixed; full suite 1049 passed / 1 skipped). Uncommitted at time of writing.

5. ~~Task 5~~ — backfill archiving through StorageAdapter + repair of the 57 rows: landed as two changes the same day (option A).
6. ~~Task 4~~ — coverage_scope deduced deterministically, `item` retired: landed the same day.

**Still blocking — nothing in code.** Every gating task is in. Three non-code items remain before the spend: budget approval (~$56 sweep + ~$4 GC re-enqueue + the re-extractions); a paid re-extraction of rows holding pre-fix values, now including the `coverage_scope` values Task 4 supersedes; and the CHECK constraint plus row rewrite that the Task 4 change names as a separate task.

**Will corrupt or waste paid work if skipped (high):**
7. Worker container cannot see the corpus (`docker-compose.yml`: no /imports mount on worker) and backfill.scan returns a clean `scanned: 0` on a missing path — silent no-op, violates the never-silent rule. Mount + make zero-scan loud.
8. Stale-image trap — already fired once (61 jobs "green" through outdated code). No build stamp exists in either image. Bake the git SHA in, log at startup, add rebuild to the backfill runbook step.
9. mfr-binding "None" form (§5-E) — becomes load-bearing the moment backfilled ISO certs hit review.
10. Budget caps unenforced (§5-C) — decide enforce-vs-ratify before the spend.

**Operational traps (medium):**
11. No worker job-type filter (`runner.py:48` never passes `queue.claim`'s `types`) — a started worker immediately runs any pending paid work; the plan's own precondition is operator discipline. Small flag.
12. Deleting a pending extract.doc job strands its document permanently (fetch_log seen-check); the recovery procedure lives only in a followup entry, not the runbook — and queue pruning is exactly the workaround #11 pushes operators toward.
13. Runbook's backfill.scan example uses the wrong payload key (`root` vs `drive_folder`) — following the runbook dead-letters the kickoff command. Known since 2026-07-29.
14. Dentaurum playbook unauthored though the A2 ruling landed today — 37 corpus PDFs (89% scanned → T2, the expensive tier) would extract without an identity and all land manual. Author before paying.
15. The 61 extracted GC docs carry pre-fix values: 3 ISO docs (regulation nulled) and doc #41 (wrong scope) need a paid re-extraction; 87 deleted GC jobs await re-enqueue (~$4). Budget alongside the sweep.

**Low:** playbooks sync pending for the four 08-11 playbooks; host venv is 3.11 vs requires-python ≥3.12 (use the in-container suite as authoritative — it is green); staging rework should be committed as its own unit; `.github/` untracked.

**Suggested sequence:** fix #4 → obtain the three rulings (#5, #6, archive location) → land Tasks 4+5 → fix #7, #9, #13 → stamp images (#8) + worker types flag (#11) + runbook pruning note (#12) → author dentaurum.json (#14) → commit staging unit, push to origin → re-enqueue GC under fixed code, then the remaining 11 brands.

---

## 8. Decisions needed

**From Denis (block Tasks 4/5 or are pre-spend):**
1. ~~Single-REF DoC: `item` or `group`?~~ **Answered in code on 2026-08-12, not on the record:** `item` is retired entirely. That changes the `coverage_scope` vocabulary — a contract-level decision — so it wants explicit ratification rather than a commit-message one.
2. ~~Archive strategy A vs B?~~ **A was taken** (2026-08-12). Still interacts with where the archive ultimately lives (F27/G7, open with the client).
2b. ~~ratify or reject the invariant-1 exception in `app/repair_archive_url.py`~~ **Rejected 2026-08-12 — module deleted**, invariant 1 stays absolute. See §5-D.
3. Budget caps: enforce in code before the sweep, or ratify display-only in the PRD?
4. Approve the spend: ~$56 sweep + ~$4 GC re-enqueue + the affected-doc re-extractions.
5. gate.apply status precondition (approve only from `staged`) — confirm the fix direction for the resurrect hole.
6. archive_url threading mechanism for live-fetch/upload chains (reopen [gate-threading]).
7. Push master to origin (157 commits unpushed); delete the four merged branches.

**From the client (gate scope, not code):** 3SHAPE entity ruling; A9 CARL MARTIN (code 081 = 60% of confirmed devices); D16 corpus completeness (is 1,307 PDFs everything?); archive location (F27/G7); renewal model E22/E23/E24; B10 device-class fill timeline; F26 Zagreb same-or-separate BC. 24 of 28 open questions remain unanswered.

---

*Method note: findings marked confirmed cite the exact file:line reproduced by two independent agents; severity was re-calibrated during verification where the checker over- or under-stated. Two checker findings (Task 2 uncommitted, Task 3 unimplemented) were refuted because commits landed mid-audit — evidence the verify layer filters stale claims. This report supersedes docs/status-snapshot-2026-07-15.md as the current dated status document.*

---

## 9. Post-audit re-verification — 2026-08-12

Six read-only agents re-checked every finding against the working tree after the concurrent session's commits landed. **Corrections to this report first**, then the confirmed open list. Each line was reproduced at the cited location.

### Findings this report got wrong or that have since closed

| Item | Report said | Actually |
|---|---|---|
| mfr-binding "None" form (§5-E) | **high**, "one click binds to a manufacturer named None" | **Mostly closed.** `web/app.py` now has `_decorate_review_row` (~:504-525) deriving the manufacturer from linked items; `_staging_doc_detail.html:105` only renders the input when a name resolved, and `_staging_decide.html:11-25` renders a **disabled** Approve button explaining the manufacturer is unknown. Residue: the handler (~:1558) still accepts the literal string `"None"`, unreachable from the form. Low, defence-in-depth |
| Silent `scanned: 0` (§7) | high, "silent no-op" | **Closed.** `app/handlers/backfill.py:95-99` raises `BackfillError` on a missing *or* empty folder |
| Worker `/imports` mount (§7) | high | **Closed** on 2026-08-12; `docker-compose.yml:84-92` mounts `${IMPORTS_HOST:-./imports}:/imports:ro` |
| todo.md header "4 of 28" (§6) | drift — file records six | **Wrong claim, removed.** Four questions carry `[x]` (A1, A2, A4, B10); C12/C14 are recorded as design inputs, not answered questions |
| `[backfill-bytes-not-archived]` | open | now `[x]` in followups |

### Confirmed still open

**Registry / GATE** — all reproduced in `app/handlers/gate.py`: archive_url still resolves to the source URL (live fetch) or `upload:{hash}` (uploads), untouched by the backfill work (**high**); `_promote` (:494) has no status precondition, so a superseded/rejected doc can be promoted (**medium**); the auto-supersede fast path (:406-411) still does not re-SELECT the target's type/regulation/status — though VALIDATE does check at :654-673, so the exposure is drift between the two, not an unguarded write (**medium**); evidence completeness covers only the 3 required fields and never validates `model_id` (**medium**); `_apply_supersession` (:222) checks type/regulation but not coverage subject (**low**).

**Economics / extraction** — budget caps still have zero enforcement consumers (**high**); `extract_ref_list` still stamps 0.9 (now `t0_templates.py:402`, moved by the 2026-08-12 Task 4 change, which changed only `derive_coverage_scope`) and both prompts still cap at 40 codes (**medium**); `DENTALIA_EXTRACT_MODE` still defaults to sync and is set in neither compose nor `.env.example` (**medium**); batch is still batch-of-1 (`llm.py:229`) (**medium**); the batch T2 branch still lacks the sync path's empty-target guard (**medium**, logged `[batch-empty-target-t2]`).

**Operational** — no git SHA or build stamp in either image, and the runbook still does not tell the operator to rebuild the *worker* (**high**); `runner.py:48` still calls `claim()` without `types` (**medium**); the runbook's backfill example still says `"root"` where the handler reads `"drive_folder"` (**medium**); scheduler still gated behind compose profile `full` (**low**); `playbooks/dentaurum.json` still absent with `[dentaurum-playbook]` open (**medium**).

**Hygiene** — `dentalia_api` keeps its unused `UPDATE ON job` grant (**low**); `tools/cold_start_reset.sql` still truncates the registry with no production-row guard (**low**); `bc_odata` still selectable in the ingest form (**low**).

**Docs** — PHASES.md:41 still says ext.manufacturer is "PLANNED, NOT BUILT" against followups' "Phase 1 shipped" (its line 198 web-upload contradiction is partly reconciled at line 200); every checkbox in the extraction-correctness plan is still unchecked though all six tasks landed; `[gate-threading]` is still marked `[x]` and has not been reopened despite §5-A.

### New gap found by this pass

The Task 4 change of 2026-08-12 defers a `coverage_scope` CHECK constraint and the rewrite of existing rows to "the separate CHECK-constraint task" — **no such task exists in any tracked file** (searched `tasks/`, `docs/`, `migrations/`). Since 37 of 57 registered documents hold the now-retired `item` value, this needs logging before the re-extraction is planned.

### Re-confirmed intact

Invariant 1 holds with no exceptions as of 2026-08-12: the one deliberate exception this audit found (`app/repair_archive_url.py`) was deleted rather than ratified. The migration-021 CHECK still bars all three untrusted bases from production, matching gate's `TRUSTED_BASES`. `web/` is still a producer only — the sole non-job write is `INSERT INTO upload_inbox` — despite roughly 700 lines added there this session.
