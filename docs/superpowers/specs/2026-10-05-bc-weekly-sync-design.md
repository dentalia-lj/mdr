# Weekly catalogue sync from Business Central

**Date:** 2026-10-05 · **Status:** manual weekly runs ruled by Denis on
2026-10-05, manufacturers first; the cron in § 4 is **proposed, not built**, and waits on his
approval. · **Related:** [BC write-back design](2026-09-07-bc-writeback-design.md),
[runbook § Business Central](../../runbook.md#business-central-weekly-sync-and-the-first-write),
[deployment § 6.4](../../dev/deployment.md#64-step-5-the-catalogue).

## 1. Why

Our item table (`item_mirror`) is a copy of BC's catalogue, and until now it was
refreshed only from an Excel export (last one: `Artikli.xlsx`, 2026-09-25). BC
keeps growing. The first full read from BC's API (dry run, job 23729,
2026-10-02) found **780 items new or changed** against that export. An item we
do not hold gets no grouping, no documents, and no write-back to BC.

Denis, 2026-10-05: sync **weekly**, **by hand for now**, cron later. This
replaces the "OData later" half of the 2026-09-16 B3 ruling
([decisions.md](../../decisions.md)).

## 2. What one run does, whoever starts it

One `ingest.run` job, payload
`{"source": "bc_odata", "catalogue": "LJ", "ref": "<BC_BASE_URL>/allitems"}`.
Nothing new in the pipeline. The same handler runs whether the source is BC or a
file.

| Property | Value, and where it was measured |
|---|---|
| Read | Full catalogue, every run. There is no delta (`$filter` is never built). Pages of 1.000 (`Prefer: odata.maxpagesize`), 120 s timeout per page |
| Duration | **~3 min 50 s** for 20.027 items, 21 pages: first page ~60 s, then ~8 s a page (worker log, job 23729, 2026-10-02). The ~19 min in the 2026-10-02 commit was an extrapolation from unpaged timing and did not hold |
| Writes | Upserts `item_mirror`. **Never deletes**: an item that disappears from BC stays mirrored. A brand-new non-device item is not mirrored at all |
| Fan-out | One `resolve.group` per changed item. While `DISCOVER_HOLD=true` (the server today), RESOLVE groups and does not search the web |
| Fails safely on | an empty response (raises); a renamed property (`UnknownOdataProperty`, first record); a refused login (`BcAuthRejected`, sent once per process so the domain account cannot lock) |
| Cannot catch | a change in what a property *means* on BC's side (e.g. b-s.si repointing `manufacturerCode`). The run would succeed and regroup every affected item. This is the gap § 4.3 closes for unattended runs |

## 3. Manual, weekly (now)

The procedure is in the [runbook](../../runbook.md#business-central-weekly-sync-and-the-first-write):
dry run, read the counters, apply. A person is there for every run, so the
judgement in § 4.3 is made by eye.

## 4. The cron (proposed)

### 4.1 Shape

A new tick, `ingest.bc-weekly`, beside the ten existing ones. Ticks are already
a dispatch table, so adding one is a dictionary entry, not a new mechanism.

- `app/handlers/scheduler_tick.py`: an entry in `CRONS` and in `POLL_SECONDS`
  (21600, a 6 h poll, as `ingest.monthly` uses). `arm_crons()` arms every key in
  `POLL_SECONDS` on worker start, so **no migration**.
- `app/scheduler.py`: `_tick_ingest_bc_weekly(conn, cfg, now)`. Period is the ISO
  week (`periods.period_key_isoweek`, already used by `report.weekly`). The
  `scheduler_run` ledger stops a second fire in the same week.
- It enqueues the § 2 payload with priority `delta` and dedupe key
  `ingest.run:sched:LJ:bc:{period_key}`. This adds one row to the PRD dedupe
  table but no new job type, so the closed enum is untouched.
- **When it fires:** on the first poll of each ISO week, Monday between 00:00
  and 06:00 UTC (02:00 to 08:00 in Ljubljana), before the office opens.

### 4.1a Manufacturers before items

The same tick handles the manufacturers first (Denis approved the BC manufacturer
sync on 2026-10-05). RESOLVE turns a manufacturer code missing from
`manufacturer_alias` into its own canonical name, the bare code, and a group's
manufacturer is never re-derived afterwards. So an item whose manufacturer is new
to us must not be resolved before the master and the aliases know that
manufacturer.

1. `vendor.import` from BC: additions only, unattended. Any rename holds the whole
   weekly run for a person, because a rename is the write-once hazard the rename
   refusal exists for. Codes that disappeared are kept and reported, as today.
2. The alias refresh (`sync_aliases`). Today `vendor.import` deliberately does not
   run it, because it can report `orphaned_groups` that need a person. With
   additions only, no existing group can be orphaned, so the cron may run it; any
   non-zero `orphaned_groups` or a `PlaybookConflict` holds the run.
3. Only then the items `ingest.run`, enqueued with `run_after` an hour later, or
   emitted on success of step 2. Which of the two is open (§ 5).

### 4.2 Switches

- `SCHEDULER_BC_INGEST_ENABLED`, default `false`, passed to `worker` in
  `docker-compose.yml`. It fires only when this is true **and** `BC_BASE_URL` is
  set. Otherwise it records why on the `/scheduler` row, as the other ticks do.
- **Never both sources at once.** Config loading refuses
  `SCHEDULER_BC_INGEST_ENABLED=true` together with a non-empty
  `SCHEDULER_INGEST_WATCH_DIR`. An older export applied after a BC read would
  undo BC's newer values, and the two would keep reverting each other.

### 4.3 Brake (recommended)

An unattended run has no person to notice that a run changed the whole
catalogue. The proposal is a payload field `max_changed`, which the cron sets
and a manual run omits:

- The handler reads every row first (20.027 rows is a few MB), counts what would
  change, and writes only if `changed <= max_changed`.
- Above the limit, the run ends as a dry run with `held: true`. It shows on
  Today's Failed tasks and in the weekly report, and a person applies it by hand
  after looking.
- Proposed default: **2.000** (~10 % of the catalogue). The 2026-10-02 dry run
  counted 780 against a 10-day-old export. What a normal week changes is not
  known yet; the manual weeks will measure it, and the number should be set
  from that.
- `max_changed` is a new optional payload field, which is additive and so allowed
  once Phase 1 has started.

### 4.4 What else changes

- `/scheduler` gets the row through its cron list in `web/scheduler_view.py`
  (label "Weekly catalogue sync from Business Central"). The client guide's
  scheduler page (`docs/guide/pages/scheduler.md` and `.sl.md`) gains the line,
  and the bundles are rebuilt.
- `docs/dev/config-reference.md` (the new key and its blast radius),
  `docs/dev/handlers.md` (the tick), and `docs/dev/limits.md` (the row saying it
  is not built is deleted).

### 4.5 Tests (table-driven, real Postgres)

Disabled: nothing is enqueued. Enabled with an empty `BC_BASE_URL`: nothing is
enqueued, and the reason is recorded. Enabled: one job per ISO week, and a
second poll in the same week is a no-op. Payload and dedupe key are exact. Both
sources on: config refuses. Brake: `changed > max_changed` writes nothing and
reports `held`; `changed <= max_changed` writes.

### 4.6 Size and order

Three pieces that ship separately (CLAUDE.md three-file rule):

1. Tick, config, compose, tests. ~1,5 h
2. The brake in `app/handlers/ingest.py`, with tests. ~1,5 h
3. Docs and the guide in both languages. ~1 h

**Switch it on** after two or three clean manual weeks: set
`SCHEDULER_BC_INGEST_ENABLED=true` in `.env`, then `docker compose up -d worker`.

## 5. Open for Denis

1. Approve § 4 as drawn, or change it.
2. Fire time: Monday early morning UTC, or another slot.
3. Brake: yes or no, and at what number.
4. The file-based monthly tick: keep it as a fallback (it stays inert while
   `SCHEDULER_INGEST_WATCH_DIR` is unset), or delete it once the BC cron is on.
5. Sequencing manufacturers → items: a fixed one-hour gap (simple; items still run if step 1 or 2 of § 4.1a held), or the items job only on their success (safer; needs `vendor.import` to emit `ingest.run`, a PRD Emits change).
