# Coverage claims register — 2026-09-04

One row per claim that something is open, unbuilt, broken or unmeasured, as
asserted by the 2026-09-04 readiness audit or a tracker. Built 11:00;
**verdicts filled 2026-09-04 11:30–12:30**
by eight read-only verifiers, every load-bearing verdict re-checked by hand
before it changed a file.

**Rules.** A verdict comes from the tree at HEAD, a commit, or a `SELECT` on the
dev database — never from another tracker, and never from PHASES.md's State
block, which is a dated snapshot. Every verdict carries a `file:line`, a date, or
the query. Database figures are as of the verifier's read and are floors (job
rows were deleted by hand; see F12).

Verdict vocabulary: `OPEN` · `CLOSED by <sha>` · `PARTIAL` · `WRONG` (the claim
was never true) · `RULED-OUT by <ruling>` · `UNVERIFIED`.

Sources: **A** readiness audit 2026-09-04 (artifact `cbbfe033`) · **P** `PHASES.md`
State block / critical-path table / What is next · **G** `PHASES.md` §E ·
**L** `docs/dev/limits.md` · **T** `tasks/todo.md` (read at HEAD — another session
was rewriting the working copy during this pass) · **F** `tasks/followups.md` tag.

## Summary

| Verdict | Rows |
|---|---|
| CLOSED | F3, F6, F13, F14, S-G, P-10, P-16, T-2, T-4 (wrong, not closed), T-11, T-12, T-19, T-21 |
| PARTIAL | F2, F7 (composition right, figure moved), F16/L-12, G6, G10, L-10, L-13, P-8/L-9, S-A, T-5, T-13/P-11, T-17 |
| RULED-OUT | F5/L-3 (r2), F9/L-5 (r4), L-6 (deliberate 2026-09-02), P-15 (G6 fold), r14 |
| WRONG | L-11 (six flags, not five), T-4 |
| OPEN | F1, F4, F8, F10, F11, F12, F15, F17, F18, F19, F20, F21, S-B, S-C, S-F, P-1, P-2, P-3, P-4, P-5, P-6 (fact), P-7, P-9, P-12, P-13, P-14, L-1, L-2, L-4, L-7, L-8, T-8, T-9, T-10, T-16, T-18, T-20 |
| Client / decision, not code | F9 decisions, S-D, S-E, S-H, G4, G7, G8, G12, G15/P-1, T-1, T-7, T-14, T-15, T-22, T-23, T-24, r12, r13 |

**Tracker corrections made this pass** (`docs/`, PHASES.md, followups; todo.md left
to the session editing it): see §H.

## A. Audit findings and scenarios

| id | claim | also in | verdict | evidence |
|---|---|---|---|---|
| F1 | No backup of registry or archive | P table, F `[no-registry-or-archive-backup]` | OPEN, ruled separate order (r1) — cannot block ACCEPT | not re-run this pass; audit 09-04 `crontab -l`, `systemctl list-timers`, no `pg_dump` outside `schema_drift.py` |
| F2 | Coverage headline lacks the catalogue denominator | P next-1 | PARTIAL | the DoC line was added 2026-09-04; every `_kpi_board` denominator is still `md_flag` (`web/app.py:1604-1613`); whole-catalogue count is a separate raw stat (`_item_mirror_summary`, `web/app.py:1514-1518`, `status.html:133-134`), never a coverage % on the board or `/api/kpi` (`web/app.py:4272-4275`) |
| F3 | Item page cannot show never/empty/found | P table | CLOSED 2026-09-04 | `web/app.py:104` import, `:2279` call, `item_detail.html:150,153,159`; `tests/test_coverage.py` |
| F4 | 7 of 8 crons have never emitted a job | P para | OPEN, but the count moved | `scheduler_run`: only `report.weekly` has `job_id` set (2 of 2); **coverage-scan tick 35890 emitted 25 `discover.group` on 2026-09-04 08:15** (`caused_by` query → 25, was 0). Six of the remaining seven are flag-gated off in the worker env; `eudamed.sweep-due` never enqueues by design (`app/scheduler.py:435-471`); `report.weekly`'s zero is timing, not a flag |
| F5 | RESOLVE T1 adjudicator raises | L-3 | RULED-OUT by r2 | `app/handlers/resolve.py:263-273` raises, docstring cites `resolve-t1-ranking`; a 2026-08-12 change collapsed the band |
| F6 | Non-device doc can reach production via REF branch | T:97, F `[non-md-doc-reaches-production-via-ref]` | CLOSED 2026-09-03 | `gate.py:1032-1033` guard, `CAPPING_FLAGS` `:86-88`; `tests/test_gate_candidate_handler.py:2349-2384` three tests. **todo.md:97 still open — for the session editing that file** |
| F7 | Review debt 1.001 open human items, 13 `gate.apply` ever | P next-3 | OPEN (figure grows) | 228 staged docs + 412 staged links + 388 open manual tasks = **1.028** at 11:30; `gate.apply` 13, 2026-08-13 → 09-03 |
| F8 | INGEST never run on dev DB; mirror stamped 2026-08-10; OData profile wrong | P para, L-2, F `[odata-profile-is-guessed]` | OPEN | `job type='ingest.run'` → 0; `item_mirror` min=max `2026-08-10 13:14:08`, 15.958; `app/adapters/source.py` unchanged since `docs/2026-09-03-bc-api-integration.md`; profile keys (`No`, `Description`, `ManufacturerCode`, `VendorItemNo`, `MedicalDeviceClass`, `UDI`) do not match the observed API |
| F9 | 11.693 items blank device class | P next-6, F `[mfr-bind-empty-class]` | RULED-OUT by r4 (code); the three class decisions stay with Denis | `data_anomaly md_class_blank` 11.693; `gate.py:1306-1310` `WHERE md_flag IS TRUE` is the ruling, migration 053 |
| F10 | One Caddy credential, every human decision is `admin` | P table | OPEN | Caddyfile one pair, default user `admin` (`docker-compose.yml:349`); `audit_log` by `decided_by`: admin 22 · gate 677 · repair tools 1.461 |
| F11 | Staff screens print shell commands and internal vocabulary | L UI drift, F `[shell-commands-on-client-screens]` `[ui-wording]` `[drafts-empty-state-stale]` | OPEN | shell: `ingest.html:77`, `_staging_suggestion_detail.html:38`, `manufacturer_detail.html:74,89-90,109`, `playbook_detail.html:75`, `scheduler.html:29`, `drafts.html:51-53`; vocab: `api_reference.html:121,153`, `document_detail.html:206`, `item_detail.html:117`, `_staging_doc_detail.html:214`, `staging.html:19,26,37,80`, `status.html:117-122,156`. All behind Basic auth; the customer route `/item/{ref}` (`web/item_link.py:96`) is clean. `drafts.html:51-53` still calls `email.request`/`email.reminder` unbuilt — they are registered handlers (`email_request.py:859-860`) |
| F12 | Job ids missing; ~10.000 items have no surviving `resolve.group` row | P para | OPEN | `job` 16.901–37.973, 17.590 rows, **3.483 missing**; 10.039 of 15.958 `item_group_member` rows have no `resolve.group` job; no general prune tool, but three repair scripts delete pending `validate.doc` rows (`[repair-scripts-delete-pending-jobs]`) |
| F13 | Search rung returned no candidates | — | CLOSED 2026-09-02 | `discover.py:182-202` live `AnthropicRanker`; `:256-261` never quotes the label, drops pure-category labels |
| F14 | `visibility_timeout_s` never passed to `queue.claim` | P table, P para, P next-15 | CLOSED 2026-09-04 | `runner.py:68`; `tests/test_runner.py` |
| F15 | Discovery yields IFUs and staged links, not coverage | P next-14, F `[discovery-yield-is-mostly-ifus]` | OPEN (measured) | links from documents created ≥ 09-03: fetch-context staged 94 / retracted 12, ref-list production 2, ref-list staged 3, ref-item staged 1; document types: IFU 54, DoC 26, EC 4, ISO 2, other 2. Decision r12 open |
| F16 | No alert leaves this machine; heartbeat ghosts never pruned | P table, P para, L-12 | PARTIAL 2026-09-04 | `app/alerts.py`, only caller `runner.py:115`, `status=="dead"` only; **`ALERTS_WEBHOOK_URL` empty in the live worker**, so inert today (`[alerts-webhook-url-empty]`); two ghost heartbeat rows, no prune (`[heartbeat-ghost-rows]`) |
| F17 | EUDAMED rung can never hit: `basic_udi_di` NULL on all groups | P table | OPEN | `item_group` 0 of 8.208; nothing writes the column (`resolve.py:179` INSERT omits it, no UPDATE anywhere); `_eudamed_lookup` (`discover.py:161-167`) returns `[]` when falsy; `discovery_log source='eudamed'` → 354 miss, 0 else |
| F18 | SRN bootstrap probe unreachable | P table, F `[eudamed-srn-probe-is-unreachable]` | OPEN | `probe_srn` only inside `handle_eudamed_sweep` (`eudamed.py:733-766`); release route refuses 400 on empty trusted SRN (`web/registry.py:1807-1813`) and is the only enqueue site (`:1816`); no CLI or scheduler path |
| F19 | 58 of 203 production docs have no `validity_from` | F `[production-documents-with-no-issue-date]` | OPEN | DoC 53 · IFU 2 · ISO 2 · EC 1; `validate.py:1009-1010` |
| F20 | No multi-hop test; `POST /playbooks/{slug}` untested at HTTP; 5 silent skips; 8 tables outside reset | P para | OPEN (a, b), exact (c), **7 not 8** (d) | (a) every `run_once` test runs one job then asserts the next row exists (`test_discover_handler.py:597-608`, `test_resolve_handler.py:404-420`, …); (b) route `web/registry.py:2231`, all save tests call `save_playbook_body` directly (`tests/test_playbook_save.py:109-115`); (c) `test_komet_coverage_corpus.py:44,66`, `test_reconcile.py:423`, `test_vendor_master.py:304`, `test_llm_schema_vocabulary.py:148`; (d) `audit_log`, `batch_ref`, `document_text`, `extraction_attempt`, `extraction_cost`, `robots_cache`, `schema_migrations` absent from `_RESET_TABLES` (`conftest.py:247-286`), `[reset-tables-seven-fk-less]`; (e) **2.732 tests, 123 files** |
| F21 | 822 items behind honoured robots refusals | P para | OPEN, ceiling not defect | `refused_host` 5 rows: KAVO 331 + AMANN GIRRBACH 196 + PRITIDENTA 193 + COLTENE 102 = 822 |
| S-A | New BC item stops after grouping | P flags | PARTIAL, stale | `DISCOVER_HOLD=true` holds RESOLVE's own emission (`resolve.py:222-243`), but **coverage-scan is on** and bypasses the hold by design (`scheduler.py:236-249`), 25/day |
| S-B | Certificate lapse triggers nothing | P para | OPEN, deliberate | `_tick_expiry_scan` (`scheduler.py:126-225`) enqueues only behind `expiry_email_enabled` / `expiry_rediscover_enabled`, both false in the worker |
| S-C | Two same-day current declarations, no tie-break | — | OPEN, and a code gap | equal `validity_from` matches no branch at `validate.py:1009-1026`, no flag; three groups hold triplicate production DoCs on one date (`[validate-equal-date-falls-through]`); ruling r13 open |
| S-D / S-E / S-H | Class I contradiction · completeness matrix unvalidated · reviewer for discovery output | — | client / decision | — |
| S-F | Fresh environment seeds empty | — | OPEN, design fact | `fetch_log` backfill 862 · live 194 · email 17; by content hash 755 / 107 / 16 of 875 documents (86,3% corpus); no SFTP client anywhere in the tree |
| S-G | Bring-up never rehearsed | P para | CLOSED 2026-09-04 (throwaway DB) | `scripts/bringup-rehearsal.sh`; excludes live fetching and container start; dev DB still has no `ingest.run` |

## B. PHASES critical-path rows not covered above

| id | claim | verdict | evidence |
|---|---|---|---|
| P-1 | Acceptance thresholds N and X unset | OPEN | no value in `app/config.py`; `docs/specs/kpi.md:145` "needs Denis sign-off"; ground-truth §9 "measure-only" |
| P-2 | Calibration map loads 0 entries | OPEN, stronger | `Config` has **no `calibration` field**; `gate.py:292,856,1019` `getattr(cfg, "calibration", {})` always `{}` — `[gate-calibration-map-absent]` |
| P-3 | `expiring_documents` filters `d.status='production'` without the link join | OPEN | **`app/handlers/report.py:21-77`** (not `app/report.py:69`): joins `document_effective_expiry` only; `/expiry`'s own `_LAPSING_CTE` (`web/app.py:401-421`) does join `item_document`; feeds `report.weekly` and the expiry `email.request` emission |
| P-4 | EUDAMED `cert_refs` never written | OPEN, structural | only reads (`discover.py:169-175`), 0 of 16.827; `migrations/038_eudamed_reference.sql:9-13` says EUDAMED returns no document URLs |
| P-5 | Sweep has no per-page commit | OPEN, by invariant | no `commit()` in `eudamed.py`; page loop `:790-828`; mitigated by F14's 1800s |
| P-6 | `MAX_VISION_PAGES = 4` | fact confirmed | `app/extract/llm.py:44` |
| P-7 | Contacts 6 of 384; IVOCLAR 59 of 86 expired docs, no address | OPEN | `manufacturer.contact_emails` 6 (3SHAPE, DUERR DENTAL, EDENTA, HENRY SCHEIN, KOMET, VOCO); via `document_effective_expiry` IVOCLAR 59; 26 of 86 expired docs behind a contact |
| P-8 | `robots.check` has no caller; `fetch.robots_ttl_hours` inert | PARTIAL | callers `discover.py:491`, `playbook_probe.py:136` (2026-09-03 to 2026-09-04); neither passes `ttl_hours` → key still inert; live FETCH still ignores robots (`[robots-reader-unwired]`, narrowed) |
| P-9 | Line-split dates return None | OPEN | `re.search(_DATE_RE, '2031-02-\n28')` → None at HEAD; changes on 2026-09-03 and 2026-09-04 fixed column-boundary adjacency, a different defect |
| P-10 | Budget caps display-only | CLOSED 2026-09-03 (r3) | relabel is template-only, `status.html:56,213,217,220`; `web/app.py:1710,1712` are dict keys, unchanged and correct |
| P-11 | `playbook.reonboard` failure-monitor branch raises; scheduler payload omits `index_url` | OPEN, at HEAD | `playbook_probe.py:94-101` raises without `index_url`; `scheduler.py:342-355` payload has none. Not other-session work |
| P-12 | Five ISO-typed docs mis-typed, all staged | OPEN (data) | 209, 248, 260, 263, 316 all `ISO`/`staged` |
| P-13 | `[filed-relink]` deferred; filed count | OPEN | 439 filed; no relink code; ruled "buildable now" 2026-08-27 (`followups.md`), nothing since |
| P-14 | 47 mirror rows under an SRN with no `manufacturer_srn` row | OPEN | `TW-MF-000012356` 47 rows; `manufacturer_srn` empty for it |
| P-15 | `vendor` rung unreachable | RULED-OUT (G6 fold) | `discover.py:980-982` skips it explicitly; `default_source_priority` (`config.py:167-176`) never lists it; 0 rows |
| P-16 | `CLAUDE.md:65` describes a deleted `scheduler` service | CLOSED 2026-09-04 | but `docs/dev/00-orientation.md:37` and `docs/runbook.md:67` carried the same claim — corrected this pass |

## C. PHASES § 4 gap register

| id | verdict |
|---|---|
| G4, G7, G8, G12 | client |
| G5 | = P-2 |
| G6 | CLOSED 2026-09-04: code shipped (`app/crawl.py`, `_crawl_playbook` `discover.py:458/956`, 96 tests, `playbooks/edenta.json` authors one) as a DISCOVER rung, so the Emits delta and `hop` guard the gap asked Denis to approve never existed; PRD §3 and handbook rewritten to what shipped plus the 2026-09-04 triage ruling; build residue `[crawl-ai-link-triage]` |
| G10 | PARTIAL: `_kpi_staging_queue` (`web/app.py:1653-1667`) shows one "oldest" date per queue on the board; no per-row age, no SLA anywhere |
| G15 | = P-1 |

## D. limits.md rows

| id | verdict | evidence |
|---|---|---|
| L-1 | OPEN | `storage.py:72-76` raises, G7 |
| L-2 | OPEN | `source.py:279-309`; no `_checked` guard, unlike `_ExportAdapter:224-235`; silent `None` rows |
| L-3 | RULED-OUT r2 | = F5 |
| L-4 | OPEN | `resolve.py:250-259` inert; **handbook `:247`, `:267`, `:852` said the opposite — corrected this pass**; `dev/handlers.md:60`, `dev/01-lifecycle.md:281` were already right |
| L-5 | RULED-OUT r4 | `gate.py:1308` |
| L-6 | deliberate | `archiving.is_document` at `fetch.py:74,197`, `upload.py:55`; none in `backfill.py` |
| L-7 | OPEN | no `smtplib`/`sendmail`; `email.send_policy` defined (`config.py:296-305,861-864`), read by nothing |
| L-8 | OPEN, standing r9 | `eudamed.py:730-874` no enqueue; `scheduler.py:435-442`; row citation `:656` corrected |
| L-9 | = P-8 | row rewritten |
| L-10 | PARTIAL, wording | adapter is the default (`config.py:200`), constructed at `email_poll.py:296`; only credentials missing — row rewritten |
| L-11 | WRONG | six `False` flags in `config.py` (`:549,561,572,584,590,607`); `config-reference.md:326` corrected |
| L-12 | = F16 | |
| L-13 | PARTIAL, at HEAD | `web/onboarding.py` (2026-09-04) queue → who → domains → library → done; `suggest_slug` is a slugifier; no auth portals; `allow_hosts`/`pagination` not on the form (`web/registry.py:1553-1554`); § S2.2 of limits.md already accurate |

## E. tasks/todo.md open items (read at HEAD; the working copy is being rewritten by another session)

| id | line | verdict |
|---|---|---|
| T-1 | 22 | ruling r11, keep |
| T-2 | 25 | CLOSED 2026-09-04 — tick |
| T-3 | 48 | other session; `web/onboarding.py` landed |
| T-4 | 53 | WRONG — `start_playbook` `web/registry.py:904`, route `:2063-2075`, 2026-08-27, `tests/test_playbook_start.py` |
| T-5 | 92 | PARTIAL — the guard half is F6 (closed); the GC `mfr_ref` length measurement was never recorded (verifier ran it: of 359 GC items, 6-digit 207, 8-digit 7, 18-digit 3) |
| T-6 | 97 | CLOSED 2026-09-03 — tick |
| T-7, T-14, T-15, T-22, T-23 | | client |
| T-8 | 123 | OPEN — 42 dead `gate.candidate` (+22 `fetch.url`, 14 `extract.doc`, 1 each `discover.group`, `playbook.reonboard`, `report.weekly`); rerun routes exist (`/dead/{id}/rerun`, `/dead/rerun-group`) |
| T-9 | 131 | OPEN — `queue.claim(types=)` exists (`queue.py:112-137`); runner, CLI and compose never pass it |
| T-10 | 134 | OPEN — doc 526 `DoC`/staged, one attempt rev 1 from 2026-08-19, never re-extracted (`[type-fix-never-backfilled]`) |
| T-11 | 142 | CLOSED, superseded — the corpus-mining plan was abandoned for `manufacturer_srn` (2026-08-26 to 2026-08-27) |
| T-12 | 173 | CLOSED — `eudamed.sync` exists (`migrations/001_queue.sql:26`, `eudamed.py:226-309`); ruling 2026-08-27 was about `ref-eudamed`, not the job |
| T-13 | 186 | PARTIAL 2026-09-04 — same as P-11 |
| T-16 | 451 | = P-13, OPEN |
| T-17 | 472 | PARTIAL — superseded by the doc-index approach (`docs/superpowers/specs/2026-08-18-komet-coverage-map-design.md`), never closed; KOMET now 85 documents, todo says 117 |
| T-18 | 480 | OPEN — no `playbooks/dentaurum.json` (`[playbook-entity-rulings]` says the same) |
| T-19 | 485 | CLOSED — `playbooks/komet.json:3-4` alias present |
| T-20 | 489 | OPEN — `BrandCollision` guards it (`app/playbooks.py:93-108`), no parent model |
| T-21 | 543 | CLOSED — `ref_normalize` per playbook (`app/playbooks.py:219,374-376,712`; `ivoclar.json:20`, `komet.json:34-36`) |
| T-24 | 581 | = F7 |

## F. followups.md

218 `[ ]` open at HEAD before this pass (the other session moved ten stranded
entries back under Open, 2026-09-04). Eight new entries filed this pass:
`[validate-equal-date-falls-through]`, `[type-fix-never-backfilled]`,
`[repair-scripts-delete-pending-jobs]`, `[trusted-srns-never-swept]`,
`[heartbeat-ghost-rows]`, `[reset-tables-seven-fk-less]`,
`[alerts-webhook-url-empty]`, `[playbook-authoring-missing-keys]`. Already
tracked and left alone: `[no-alert-path-off-this-machine]`,
`[production-documents-with-no-issue-date]`, `[gate-calibration-map-absent]`,
`[robots-reader-unwired]` (narrowed 09-04), `[eudamed-srn-probe-is-unreachable]`,
`[playbook-entity-rulings]`, `[filed-relink]`, `[resolve-c4]`.

## G. Rulings applied

| # | ruling | date | source | applied to |
|---|---|---|---|---|
| r1 | Backup is a separate commercial order | 2026-09-03 | `docs/decisions.md` | F1 chips |
| r2 | RESOLVE T1 adjudicator superseded, not built | 2026-08-19 | `[resolve-t1-ranking]` | F5, L-3 |
| r3 | Budget caps: relabel, do not enforce | 2026-09-03 | the 2026-09-03 change | P-10 |
| r4 | Blank device class means UNKNOWN; write no link | 2026-08-27 | `[mfr-bind-empty-class]`, migration 053 | F9, L-5 |
| r5 | One BC, no Zagreb system | 2026-08-19 / 08-26 | CLAUDE.md | — |
| r6 | PPWR out of scope | 2026-08-26 | `[ppwr-in-scope]` | — |
| r7 | Migration collisions fixed at merge | 2026-08-27 | `[migration-041-collision]` | — |
| r8 | System never sends supplier email; operator alerts are a separate channel | 2026-08-20 / 09-04 | L-7, `app/alerts.py` | L-7, F16 |
| r9 | EUDAMED sweeps are human-released | standing | L-8, `scheduler.py:436-442` | L-8, F18, `[trusted-srns-never-swept]` |
| r10 | LLM may summarise inbound email (UI only) | 2026-08-20 | CLAUDE.md inv. 12 | — |
| r11 | Coverage-KPI fix is not new scope | 2026-09-03 | T:22 | T-1 |
| r12 | Keep coverage-scan at 25/day; naming the reviewer is the client's obligation (offer §7) | 2026-09-04 | `docs/decisions.md` | F15, S-H |
| r13 | Equal `validity_from`: flag `same-date-revision`, force manual, a person picks | 2026-09-04 | `docs/decisions.md` | S-C — code landed the same day |
| r14 | `ref-eudamed` stays unwritten | 2026-08-27 | `docs/decisions.md` | never in the vocabulary or the CHECK (`migrations/023:37-42`); 0 rows |
| r15 | T0/T3 evidence may be pageless | 2026-07-31 | CLAUDE.md inv. 2 | — |

## H. What this pass changed, and what it left

Corrected (committed 2026-09-04): `docs/dev/limits.md` rows for EUDAMED sweeps,
robots, IMAP · `docs/dev/config-reference.md:59,326` · `docs/dev/handlers.md:188`
· `docs/dev/playbook-authoring.md` crawl and reonboard paragraphs ·
`docs/dev/00-orientation.md:37` · `docs/runbook.md:67,120` ·
`docs/test-infra.md:159` · handbook C4 at `:247,:267,:852` · `docs/code-map.md`
rows for `app/crawl.py`, `app/coverage.py`, `app/alerts.py`, `app/contacts.py` ·
`CLAUDE.md` test count · `PHASES.md` cron paragraph, flags line, four table rows,
G6 wording.

Left for the session editing `tasks/todo.md`: tick 25 (T-2), 97 (T-6), 173
(T-12), 485 (T-19), 543 (T-21); withdraw 53 (T-4); mark 142 (T-11) and 472
(T-17) superseded; update 186 (T-13).

Slice 3, 2026-09-04 afternoon: equal-date flag, reset list, heartbeat pruning,
drafts wording, trackers. Crawl link triage ruled and built the same afternoon,
in three steps: a first cut, then judged type + article numbers in
our-article-first order, then judged once per library version (migration 061).
G6 closed without the Emits sign-off it asked for. `ALERTS_WEBHOOK_URL` is set
in `.env` to a placeholder topic; the real value and a worker restart are
Denis's. The todo.md ticks are moot: that file was rewritten the same
afternoon. Not yet measured: the crawl triage
on NSK or Ultradent, which have no `crawl` recipe.
