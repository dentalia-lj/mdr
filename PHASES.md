# PHASES.md — Build Plan & Status

v5, rebuilt 2026-09-16 after a full verification sweep. **The plan and the status
board.** One status per thing, stated once. Where any other file disagrees with
this one about status, this one wins; where the PRD disagrees about a contract,
the PRD wins.

**Precedence:** `docs/dentalia-pipeline-contract-prd-v3.md` is normative on
contracts. `CLAUDE.md` invariants override everything, including this file.

**This page points; it does not duplicate.** Each row carries status and a
pointer. The argument behind it lives where it belongs.

| For | Read |
|---|---|
| the rulings already made — **check before proposing** | [docs/decisions.md](docs/decisions.md) |
| per-criterion and per-finding state, with a box to tick | [docs/acceptance-and-findings.md](docs/acceptance-and-findings.md) |
| the measurements behind § 3 | [docs/state/2026-09-16.md](docs/state/2026-09-16.md) |
| why a shipped thing was built the way it was | [docs/build-log.md](docs/build-log.md) |
| what is deliberately not built | [docs/dev/limits.md](docs/dev/limits.md) |
| the working engineering queue | [tasks/followups.md](tasks/followups.md) |

**Reading order:** § 1 Progress → § 2 Open scope → [decisions.md](docs/decisions.md) → § 4 Gaps.

---

## 1. Progress

**Phase 0 done · Phase 1 built, eight of nine acceptance criteria met · Phase 2 at
three sessions of five.**

The pipeline runs end to end and has produced a registry of 891 documents over
15.958 catalogue items. **Every functional item the two delivery stages owe is
built.** What it has never done is run against a live BC feed, or have its output
reviewed anywhere near the rate it is produced.

| Phase | Scope | Status |
|---|---|---|
| **0** | spike: queue, EXTRACT, GATE, corpus run | **DONE** 2026-07-13 |
| **1** | INGEST → RESOLVE → DISCOVER → FETCH → EXTRACT → VALIDATE → GATE, review UI, KPI board | **BUILT, EIGHT OF NINE ACCEPTED** — every stage has run against real data. Measured 2026-09-16: AC1 met (1.571 of 4.265 = 36,8% against a 33% target), AC3 met by construction, AC6 met re-based (99,6%), AC2/AC4/AC5/AC7 met, AC9 measure-only. **AC8 is the single failure** and is one defect. Per criterion: [the register](docs/acceptance-and-findings.md) |
| **2** | playbooks, discovery, onboarding, EUDAMED, email, scale | **ALL BUILT** — S2.1 and S2.3 done, S2.4 built and waiting on the client's mailbox, S2.5's sweep hardening done. Still open: S2.2's agent half, and the scale work sized for the ZG connection G17 struck |

### Sessions

**Status is measured against what the stage owes.** DONE means the stage's
deliverable is delivered. Residue beyond it, and anything that waits on the client,
is named and does not hold a row open. Evidence for every verdict:
[state/2026-09-16.md](docs/state/2026-09-16.md) § 9.

| Session | Scope | Status | What is left |
|---|---|---|---|
| S0.1–S0.4 | scaffold · queue core · EXTRACT · GATE · corpus run | **DONE** by 2026-07-13 | — |
| S1.0 | VALIDATE, full v3 rule set | **DONE** 2026-07-13 | — |
| S1.1 | INGEST + item mirror | **DONE** 2026-07-15 | set-based rewrite before 100k — item 17 |
| S1.2 | RESOLVE | **DONE** 2026-07-15 | — |
| S1.3 | DISCOVER + SearchAdapter | **DONE** 2026-07-21 | — |
| S1.4 | FETCH + StorageAdapter + Playwright tier | **DONE** 2026-07-21 | Nothing of ours. Drive auth (G7) is the client's |
| S1.5 | SCHEDULER + T0 layout templates | **DONE** 2026-07-23 | Ten crons; two have ever emitted a job |
| S1.6 | review UI + read API + KPI board v1 | **DONE** 2026-07-23 | Nothing. One wording residue — F35, item 1 |
| S1.7 | per-manufacturer coverage | **DONE** 2026-09-16 | Nothing outstanding. P2 and P4 are beyond this stage; P4 is F33 |
| S1.8 | `upload.ingest` retrospective slot | **CLOSED — not work** | a naming collision; it shipped 2026-08-04 |
| S2.1 | playbook store + DISCOVER/EXTRACT routing | **DONE** 2026-09-16 | Nothing outstanding. Both crawl recipes verified live; not run — item 2 |
| S2.2 | onboarding | **PART** | the agent half: no library-URL suggestion, no self-test, no AI-tier diff |
| S2.3 | EUDAMED mirror | **DONE** 2026-08-27 | One finding type stops at display — item 10 |
| S2.4 | EMAIL in + out | **DONE, not connected** | Waiting on the client's mailbox (G8). Ours: a reply pausing its chase — F31 |
| S2.5 | scale hardening + KPI report v2 | **HALF DONE** | Sweep hardening shipped and ran. Still open: three scale hot spots + G10 — item 17 |
| **BC** | Business Central read and write | **HALF DONE** — the CSV import is live; the OData switch is the later half | the OData read adapter and a read-side UI entry point — item 4. Write-back was additional and shipped whole |

P3 (T1 rankers) has left S1.7's list: DISCOVER's ranker went live 2026-09-02, and
RESOLVE's was closed by ruling — a 2026-08-12 change collapsed the staging band
it existed to judge.

---

## 2. Open scope

Everything open, once. Engineering follow-ups live in
[tasks/followups.md](tasks/followups.md); findings and acceptance criteria live in
[the register](docs/acceptance-and-findings.md). Neither is repeated here.

### 2.1 Blockers — none, ruled 2026-09-16

All five were re-tested against one question, *does this stop us shipping?*, and
Denis ruled on each the same day. **Nothing here blocks delivery.** They stay
listed because they are real, and because a reader needs to know who owns them.

| # | What | Ruling |
|---|---|---|
| B1 | Nothing backs up the registry or the archive | **Someday, not today.** Not scheduled work; a disclosed operational risk, recorded so it is never mistaken for work in progress. F1 |
| B2 | Nothing can report that the queue itself is dead | **Accepted.** The in-queue half shipped 2026-09-07. The out-of-body check is missing and stays missing: Dentalia will run another app on the same server and will notice an outage that way. F16 |
| B3 | The catalogue is a 2026-08-10 snapshot | **Not a gap.** The CSV import is the first version and is how the snapshot and the SFTP corpus arrived. The monthly tick is built — it enqueues the newest file in `ingest_watch_dir` — and is inert only because that directory is unset, which is deployment config. OData is *kasneje*, item 4 |
| B4 | Acceptance: eight of nine met | **Sufficient.** AC1 1.571 of 4.265 = 36,8% against a 33% target. AC8's third clause closes with F23's rule change; the register tracks it |
| B5 | Review debt grows far faster than it empties | **Needs a named reviewer.** 1.199 open human items against 13 human `gate.apply` decisions, none since 2026-09-03. The queue drains at whatever rate review is staffed for. F7 |

### 2.2 Ours to build

Ranked. The tag in brackets is its `tasks/followups.md` entry where one exists.
**Item numbers are permanent** — a closed item keeps its number and is struck
here, so citations elsewhere stay valid.

**Added by the readiness audits, ahead of item 1:**

- **A narrower declaration supersedes a wider one outright** (`[supersession-ignores-coverage-subject]`). Invariant 5 and AC8 breached on live data. Ruled per-item 2026-09-14, [spec](docs/superpowers/specs/2026-09-15-per-item-currency-design.md) written 09-15 and awaiting review. The **data** was repaired 09-16 — zero superseded documents remain, AC1 moved 1.460 → 1.571 — but **the rule is unchanged** and reproduces on the next candidate pair. F23.
- ~~**The item page calls a dead end "found"**~~ **DONE 2026-09-15** (F3): four discovery states, the manual rung logs `handoff`, 274 historical rows repaired.
- **Four corpus folders carry their refusal only in the archive** (`[corpus-refusals-live-only-in-the-archive]`). DENSTPLY, DENTAURUM, PLANMECA and BREDENT were refused by the pre-flight and only `docs/build-log.md` says why; only NEODENT's refusal is machine-readable. All four hold **zero** `md_flag` items, so no scan of them can move AC1. F41 records the same thing and is withdrawn as a finding; this stays as a tidiness item.

**Closed 2026-09-11:** the `PGDATA_HOST` default, the reminder ladder's re-arm, robots.txt on direct FETCH (item 14).
**Closed 2026-09-14:** the deploy check's blindness to `web/` (F39), the BC push preview's unscoped certificate join (F40).
**Closed 2026-09-15:** F3 (item above), F37 (item 7), F38 (half of item 10), F32, F34, F20, F42, F43. See [the register](docs/acceptance-and-findings.md).

1. **The catalogue denominator, narrowed 2026-09-16.** `/status`'s Coverage tab already reports both — Strict 4.265 beside Processed scope 15.958, since 2026-08-26. What is left is only that the Today headline sentence quotes 4.265 alone, which is the ruled AC1 definition, so this is a wording question and not a missing measure. F35 is the related one: the board prints AC1's number without its 33% target.
2. **The two crawl recipes work. They have not been run in the pipeline, by choice.** Re-verified live 2026-09-16 with `tools.crawl_judge_dryrun`, which writes nothing: **NSK** serves 1.330 unique PDFs off one page, 0 off-host, 0 capped, ranked in 27 batches for $0,48 — **DoC 129, IFU 581, ISO 6, EC 2, other 612**; **ULTRADENT** serves 147, all of them via `allow_hosts: ["assets.ctfassets.net", "downloads.ctfassets.net"]`, **IFU 134, other 13, no declarations**. robots allows both. 3 of 38 playbooks carry a `crawl` block (4 recipes — EDENTA authors two). **Not run: a real run costs about $41 for NSK** (1.330 less the ~446 safety data sheets its `doc_type_from` excludes, at the measured $0,0465 a document) **and about $7 for Ultradent**, and lands up to 1.476 staged documents on a backlog nobody has reviewed since 2026-09-03 (B5). Dev database; the spend buys nothing a production rebuild would keep. (`[static-pdf-crawl-recipe-candidates]`)
   - **Edge case, NSK: a quality cliff as the library grows.** `ceiling = max(max_links, `discovery.crawl_rank_max`)` and both are 1.500. At 1.330 links the harvest is under the ceiling, so the survivors are ranked and declarations are fetched first. Past 1.500 the harvest hits the ceiling, ranking is **skipped** as `pattern-too-broad` and the rung falls back to page order — which the 2026-09-04 measurement put at 118 declarations and 60 junk against ranking's 129 and 0. Nothing warns except a `detail.rank.skipped` line in the log.
   - **Edge case, EDENTA: never verified live, and half its recipe is inert.** Both its recipes cap at `max_links: 50`, low for a whole library, and its `doc_type_from` is the POSITIVE form (`{"13485": "ISO", "zertifikat": "EC"}`), which `discover.py:775` reads only for `null` needles — a positive rule is a probe hint and does nothing at runtime. Dry-run it before trusting it.
3. **Discovery review capacity, not identity.** A reviewer is named (B5); what is missing is throughput — 0 `gate.apply` decisions since 2026-09-03. The 09-03 slice's rates put finishing the catalogue at ~2.000 staged links, ~1.650 staged documents and ~3.300 manual tasks against about 41 production links. Running discovery wider makes B5 and F7 worse. F15.
4. **Business Central, read side.** The switch was always the planned second version, and `make_source_adapter` is a config choice over a closed enum. **The PascalCase profile and the missing-key guard were fixed 2026-09-07**: the profile maps camelCase and `_assert_profile_matches` raises `UnknownOdataProperty`. Three residues remain, all in [limits.md](docs/dev/limits.md): `manufacturer_raw` reads `pteManufCodePrimary`, which is **present but empty** while `manufacturerCode` carries the value and the guard checks presence not emptiness (`[odata-manufacturer-property-disputed]`); the `/ingest` form cannot build a `ref` URL, so the browser option always dead-letters (`[ingest-bc-odata-always-fails]`); no `$filter` is built, so `delta_since` is inert. **Not provable until Dentalia's sysadmins open external access to `denwebnav:7048`.**
5. **Business Central, write side. Complete as of 2026-09-07** — the rule, the `bc.push` stage, the ledger, the item button, the bulk preview-then-apply, the `bc.push-drift` cron. What is left is not code: external access, and a decision to set `bc.write_enabled` — F8. Design: `docs/superpowers/specs/2026-09-07-bc-writeback-design.md`.
6. **`MAX_VISION_PAGES = 4`** (`app/extract/llm.py:44`). Blocks 187 of DENTSPLY's 405 documents; NEODENT's 26-page declaration returned 137 of 948 refs. The corpus is exhausted, so this is about the intake ahead.
7. ~~**`playbook.reonboard` is double-gated**~~ **DONE 2026-09-15** (F37): the failure-monitor tick resolves the crawl recipe and enqueues `slug` + `index_url`; no recipe is recorded as a skip. One gate left, `SCHEDULER_FAILURE_REONBOARD_ENABLED=false`, and one residue: a no-recipe spike opens no manual task (needs a new `manual_kind`).
8. **The EUDAMED SRN probe is a clean deadlock.** `probe_srn` runs only inside a sweep, and the release route refuses with 400 on the same empty-SRN condition — so GC EUROPE (356 MD items, zero certificates, no SRN row) cannot be bootstrapped by any UI or scheduler path. **Re-measured 2026-09-16: 28 auto, 15 pending, 1 confirmed** — the confirmed one is HAHNENKRATT via `register-fuzzy`, not one of the deadlocked. F18.
9. **The EUDAMED rung can never hit** — it matches on Basic UDI-DI and **0 of 8.208** groups carry one. 329 misses, zero hits, ever. `cert_refs` unpopulated on all 16.827 mirror rows is a second, independent reason. F17.
10. **One EUDAMED finding type stops at display** — `certificate_status_alert` (1 row) has no downstream action. **`certificate_gap` no longer does**: 44 rows, and the `cert-request` draft kind (migration 067, F38) shipped 2026-09-15 with a button on the manufacturer page.
11. **`[gate-calibration-map-absent]`** — `Config` has no `calibration` field, so `gate.py:867` and `:1030` always get `{}` and every confidence passes through uncalibrated. **This is why G2's "closed" is cosmetic.** Ruled 2026-09-16, unbuilt — F33.
12. **Expiry visibility** — `expiring_documents` (`report.py:71-91`) filters `d.status='production'` with no `item_document_production` join, so the list is not the list of items at risk. **6 production documents** would be reported at risk while covering zero items. F34 touched this file on 09-15 for the review-due split and did not change this.
13. **Named logins: the mechanism shipped 2026-09-15, the names have not.** `caddy/users/` holds only `.gitignore` and `README.md`, so all 22 human audit rows still read `admin`. One file of names and hashes, plus `WEB_OPERATOR_USERS`. **Deferred by Denis 2026-09-04** — F10.
14. ~~**`robots.txt` on direct FETCH**~~ **DONE 2026-09-11**. Residue, verified still open 2026-09-16: the crawl rung (`discover.py:617`) and the probe (`playbook_probe.py:136`) pass no TTL, and `robots.py:54` treats only 404/410 as "no robots.txt" where RFC 9309 treats any 4xx that way, so a 401/403 refuses. Denis's call, open.
15. **Line-split dates** (`[extract-t0]`) — `_LONG_DATE` landed; a date wrapping a line still returns None, because the regexes require contiguous digit runs (`t0_templates.py:240-263`). Verified by running it 2026-09-03, re-read 2026-09-16.
16. ~~**Reply matching**~~ **DONE 2026-09-11** (linking): drafts carry `[DENT-{id}]` in the subject, `email.poll` links a reply by that token or by a sender domain matching exactly one open request, and `/emails` shows the link. **A reply still closes no chase** (`[reply-closes-no-chase]`) — ruled 2026-09-16 that it should *pause* one, unbuilt. F31.
17. **The onboarding agent half** (S2.2) and **S2.5's three hot spots** — set-based INGEST (`[s1.1-ingest]`, still one round-trip per row), the REF-gate N+1 (`[validate-refgate-n1]`), `annotate_playbooks`' casefold coverage resolution (`[annotate-playbooks-coverage]`, `registry.py:133` still `.upper()` against `playbooks.py`'s `.casefold()`) — plus **G10** staging aging/SLA.
18. ~~**Append-only is a promise, not a mechanism**~~ **NOT A GAP — ruled by Denis 2026-09-16.** Ten-year retention is a *design* property, not a database feature: the system must be built so documents are never removed, and it is. Verified the same day: **no `DELETE FROM document | evidence | audit_log | item_document` exists anywhere** in `app/`, `web/` or `migrations/`. Supersession appends and retraction sets a status. Triggers on `evidence`/`audit_log` would be belt-and-braces, not the contract; whether to add them stays the 2026-07-31 audit question and nothing more.

### 2.3 Waiting on someone else

| Waiting on | What | Gap |
|---|---|---|
| **Client** | Manufacturer contacts. **7 of 384** carry one (Carl Martin added 09-14); **IVOCLAR alone holds 60 of the 88 expired production documents (68,2%)** and has no address anywhere in the corpus. Mining the documents was built and measured: it proposes zero (F42) | — |
| **Client** | Mailbox read access for `mdr@dentalia.si` — protocol, host, credentials. Drafts-only means send-as rights are not needed | **G8** |
| **Client / BC** | `mfr_ref` source-field confirmation, then the per-supplier map. `missing_mfr_ref` is 7.082/15.958 ≈ 44,4%, which bounds AC1 | **G4** |
| **Denis / client** | Drive auth mechanics + root folder. `GoogleDriveStore.put()` raises; `LocalFsStore` is live | **G7** |
| **Client** | External-AI-API policy, before any cheap-model tier swap | **G12** |
| **Denis** | One device-class residue: where a **proposed** class lives (proposed 2026-09-11: a derived view from each item's current DoC, never a column over BC's value). The 2026-09-16 ruling settled a different question — that a *blank* class is expected, not a defect (F9) — and does not touch this one | — |

**Retired, do not re-count:** the "six open client questions" figure (ruled
2026-09-03). **And the three S2.4 ratifications** — verified 2026-09-16: the
string `[NEEDS REVIEW]` appears in neither `CLAUDE.md` nor the PRD, invariant 12
reads "ratified by Denis the same day", and the PRD documents the `email.poll`
dedupe row and the interval-bucket cadence. The only place the claim survives is
`decisions.md:63`, which is a docs-consistency residue, not an open ask.

---

## 3. Current state

Headline numbers only. The reading behind them, with every measurement, its
method, and the verification sweep that produced this rebuild, is
[docs/state/2026-09-16.md](docs/state/2026-09-16.md). **When a newer reading is
taken it gets a new dated file and this section repoints; the numbers here are
never edited in place.**

| | |
|---|---|
| Catalogue | **15.958** items · **4.265** flagged a device by BC · mirror stamped 2026-08-10, no ingest since |
| Coverage | **99,8%** of BC-flagged devices hold a production document · **30,4%** of the catalogue does |
| Declarations | **36,8%** of flagged devices hold an article-level DoC (AC1: 1.571 of 4.265, **target 33%, met**); 511 of those unexpired · 2.158 items hold any DoC · 55,4% of covered items hold only a manufacturer-scope certificate · **0** hold only a superseded DoC |
| Registry | **891** documents (204 production, 244 staged, 439 filed, 4 rejected, 0 superseded) · 9.009 item links (8.194 production) · 7.562 evidence rows · 64 MB; archive 771,8 MB |
| Where it came from | **755** documents the hand-copied SFTP corpus (9 of 15 folders scanned), 122 live web, 14 email |
| Concentration | one ISO certificate (doc 816) carries **2.567 CARL MARTIN items**; only **12 of 384** manufacturers hold any production document |
| Discovery | **526 of 8.208** groups ever searched (6,4%): 250 found a candidate, 276 reached the manual handoff, 0 ended empty · coverage cron fired 7 of the last 10 days · 21 documents since 09-04, all staged |
| Review debt | **512** open tasks · **244** staged documents · 443 staged links · **13** human `gate.apply` decisions ever, none since 2026-09-03 |
| Expiry | **88** production documents past effective expiry (67 stated, 21 staleness), touching 1.115 items · 4 renewal chases stalled, 1 live |
| Running system | both images rebuilt and verified 2026-09-14 against **three** hashed roots (`app/`, `migrations/`, `web/`) · **2 of 10 crons** emit work · the Playwright tier has run 12 times |
| Spend | **$43,31** over 1.779 extraction calls, 77,9% on T2 |

## 4. Gap register

Open gaps in full. Closed gaps keep one line here and their full text in
[docs/build-log.md](docs/build-log.md).

| # | Gap | Phase | Fillable by |
|---|---|---|---|
| G4 | `mfr_ref` BC source field — mechanism landed (`Ingest.mfr_ref_source_by_code`, empty default); `missing_mfr_ref` = 7.082/15.958 ≈ 44,4%, which bounds AC1. **Open residue:** BC/IT confirmation of the code sweep, then populate the per-supplier map | S0.4 | **client** |
| G5 | HIGH/MED gate thresholds, NAME thresholds, calibration factors. G5a (RESOLVE name thresholds) defaults `name_accept=0.90` / `name_suggest=0.90` — collapsed to equal by the GC pilot's correctness fix, and the code default now enforces it so a stale `.env` cannot revert it | S0.4 → S1 | data (P4) |
| G7 | Drive auth mechanics + root folder. `GoogleDriveStore.put()` raises until answered; `LocalFsStore` is live. Fallback on record: S3ObjectStore becomes an S2.5 item if unanswered | S1.4 | **Denis/client** |
| G8 | Mailbox access — **narrowed 2026-08-20.** The mailbox is `mdr@dentalia.si`; send-as rights are no longer needed (drafts only). Still open: **read access** — protocol, host, `IMAP_USER`/`IMAP_PASSWORD`, all empty in the running worker. The IMAP path itself is proven (GreenMail, end to end). Poll stays gated off | S2.4 | **client** |
| G10 | Staging aging/SLA mechanism — unreviewed staged documents can sit indefinitely. Verified 2026-09-16: no age column, no weekly-report count, no escalation job anywhere. Design: one of those three | S2.5 | **agent** |
| G12 | External-AI-API policy (gates any cheap-model swap; a tier swap also requires the Phase 0 diff protocol re-run). No ruling exists | before any tier swap | **client** |
| G15 | Acceptance-criteria registry — enumerated 2026-07-29, targets set 2026-09-11 and revised 2026-09-14. **Measured 2026-09-16: eight of nine met. Open: AC8 only**, which closes with F23's rule change and not with the repair already done. The standing register, every criterion and every finding F1-F44 with what closes it, is [docs/acceptance-and-findings.md](docs/acceptance-and-findings.md) | S0.4 → S1 | **agent** |

**Closed:** G1 (prompt specs — **11 fields** since `stated_class` joined `TARGET`, migration 032) · G2 (calibration **mechanism** — the transform and both call sites exist, but `Config` has no field to load factors from, so it is a permanent no-op: see item 11 and F33) · G3 (UI auth, 2026-07-23) · G6 (HTML doc-library link extraction — `app/crawl.py`, 2026-09-03) · G9 (renewal horizons, 2026-08-18) · G11 (LJ ∩ ZG overlap — moot with G17) · G13 (manual-queue persistence, migration 009) · G14 (KPI definitions, `docs/specs/kpi.md`) · G16 (producer-UI reconciliation, 2026-07-23) · G17 (Zagreb, 2026-08-19).

**Still open from the 2026-07-31 audit:** whether to add append-only triggers on
`evidence`/`audit_log`, or to downgrade the schema sketch's claim to "by
convention" in writing. Not a contract gap — ruled 2026-09-16 that retention is a
design property and the design holds (item 18). Zero triggers and zero rules
exist; no code deletes. *(The other two items
of that paragraph were closed long ago and the board never said so: ingest
dedupe-prefix unification on 2026-08-14, and Article 22 Declaration of
Compatibility on 2026-08-18 as `SPP` — the registry holds 5 of them.)*

---

## 5. Decisions

**Every ruling is in [docs/decisions.md](docs/decisions.md) — 98 rows, and the
first place to look before proposing anything that sounds already-decided.**
Split out of this file 2026-09-04. The most recent, because they are the ones a
current session collides with:

| Date | Subject | Ruling |
|---|---|---|
| 2026-09-16 | The eight documents behind AC8 (F23) | Repaired through `gate.apply`, not by hand — seeded test files rejected, real documents restored. AC1 1.460 → 1.571 |
| 2026-09-16 | Confidence calibration (F33) | Keep the mechanism, ship every factor at 1,0, and **say on the page** that confidence is uncalibrated |
| 2026-09-16 | What a supplier reply does to a chase (F31) | It **pauses** the chase and puts it in front of a person. It never closes one |
| 2026-09-16 | Missing device class (F9) | Expected, not a defect — the system is eventually deterministic. Retires the 11.693 figure as a finding |
| 2026-09-15 | Currency of a document, per item | A document is current for the items it covers, and only those. The asking entity is the ITEM or the MANUFACTURER, never the document. History returned only on request |
| 2026-09-14 | Supersession semantics (C6, invariant 5, AC8) | **Per item, not per document** |
| 2026-09-14 | AC1 target · AC3 · AC6 | 33% article-level; AC3 met by construction (no 200-link audit); AC6 re-based on device groups |
| 2026-09-14 | Route to the coverage target | **Email first**, automatic retrieval second, manual last |
| 2026-09-14 | Who Dentalia writes to at a supplier | The **role mailbox**, not the named regulatory officer |
| 2026-09-04 | Crawl link triage | Deterministic triage first, then a link judge over the survivors. **A ranking, never a filter** |
| 2026-09-03 | Registry + archive backup | **Not scheduled work** — a standing operational risk |

---

## 6. Done

One line per shipped part. The retrospective for every one of them is in
[docs/build-log.md](docs/build-log.md), which also holds this table's former
per-row notes and, since 2026-09-16, the full text of every closed finding.

**Phase 0 + Phase 1 — the pipeline.** Queue core (claim/finish/fail/defer,
backoff, dead-letter, dedupe, domain lease) · `ingest.run` + CsvExportAdapter,
proven over the 19.091-row export · `resolve.group` · `discover.group` +
SearchAdapter · `fetch.url` + StorageAdapter + Playwright tier (`LocalFsStore`
live) · `extract.doc` T0/T1/T2 + the Batch API protocol · `validate.doc` C1–C7,
supersession, null-date, MDR/MDD · `gate.candidate` / `gate.apply` two-level
disposition · `backfill.scan` · `manual_task` persistence (G13) · SCHEDULER as
**ten** self-deferring `scheduler.tick` jobs · T0 layout-template engine · review
UI + read API + KPI board v1, behind Caddy basic auth (G3, G16).

**Phase 2.** Playbook store (**38** manufacturer files) + BC identity seed + DISCOVER
`site:` routing + T0 template move · the DISCOVER `playbook` rung, which fired
and produced the registry's first open-web document (843, `renfert.com`) ·
`app/crawl.py`, the HTML doc-library rung that closed G6 · the `/onboarding`
queue (migration 060) · EUDAMED stage 1 and 2 — `eudamed.sync`,
`eudamed.certregister` (whole register, 16 calls), `eudamed.sweep`
(human-released only), migrations 041-046, eleven views · EMAIL in and out —
`email.poll`, `email.request`, `email.reminder`, `/emails`, `/drafts`, first
addressed draft written 2026-09-04.

**Out of band, and each one changed something structural.** `upload.ingest` +
`/upload` (16th job type) · vendor master + alias provenance, resolving all 381
manufacturer codes · pre-import foundations (archive persistence, parsed-text
store, job results, anomaly ledger, cited-cert resolution, auto-supersession) ·
`/manufacturers` and `/playbooks` registry views · corpus-first cold start
(`discovery.hold`) + backfill matching · `ext.manufacturer` as a 10th extracted
field · the GC pilot, the first paid corpus run · C12 `ref-item` basis,
full-coverage linking, link retraction · C16 machine manufacturer-binding, now
the dominant basis · one definition of when a document lapses (G9) · Basic
UDI-DI check-character validation · T0 document-typing correctness ·
inbound-email hardening + body summarisation · `stated_class` as an 11th target
that never scores (G1 closed at 11) · playbook-steered extraction with
per-revision provenance · `robots.txt` obeyed, `Crawl-delay` carried into the
politeness lease · service heartbeat · job lineage (`caused_by`) ·
`item_document_production` and the unexpired-evidence KPI · the read API as a
written contract · C17 link-level human decisions · document→manufacturer
binding for every document, not only manufacturer-scope ones.

**Shipped 2026-09-15/16, the two densest days so far.** Ten findings closed
(F3, F20, F32, F34, F37, F38, F42, F43, F44 and the F23 repair) plus one
vocabulary for the whole office UI and named logins. One line each, with the
retrospectives, in [the register](docs/acceptance-and-findings.md) and
[build-log.md](docs/build-log.md).

**Also 2026-09-16.** The read API learned `?include_superseded` on
`/api/items/{ref}/documents` and the batch endpoint: the supersession chain for
a consumer reconciling older paperwork, via a second view
(`item_document_history`, migration 070) that widens the DOCUMENT status filter
and moves the LINK filter not at all. Refused with `view=customer`, unreachable
from the BC card. Nothing to show yet — 0 documents are `superseded`, because
GATE has never run in production ([limits.md](docs/dev/limits.md) carries the
row, to be deleted when the first real chain exists). It also settled a
misconception worth recording: **expired documents were never hidden.** Neither
view filters on expiry, so the 86 expired production documents reaching 1 115
items were always in the payload with their `valid_to`.

**Struck, not built.** The Zagreb export mapping and cross-catalogue dedupe
(G17, 2026-08-19: one BC, one numbering, one pipeline) · `ref-eudamed`
article-level linking (measured yield: 6 new items out of 116 reached) ·
RESOLVE's T1 adjudicator (a 2026-08-12 change collapsed the band it judged) · the single
governed Ljubljana sweep (replaced by the per-manufacturer pre-flight, which has
refused two folders).
