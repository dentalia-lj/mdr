# Unit spec — SCHEDULER (S1.5, unit A)

Handbook §0 row 11 (`eudamed.sync`, out of scope here — S2.3), PRD §8 "Peripheral producers" table, PRD §11 config surface, PHASES.md §B (KPI/reporting surface), PHASES.md S1.5 block. Read those first; this spec covers only what they leave open.

## 0. Scope boundary

S1.5 builds: monthly delta ingest, expiry-horizon scan, failure monitor, `report.weekly`. It does **not** build `eudamed.sync` (S2.3, Phase 2 — the PRD §8 row lists it as a SCHEDULER output but PHASES.md's S1.5 paragraph and its Phase-2 mirror (`eudamed.sync` bulk-JSON sync) place the mechanism itself in S2.3; SCHEDULER will call it once that job type does something).

## 1. GAP resolutions (all `[FILLED — needs review]`, applied to PHASES.md S1.5 block in this commit)

**GAP A — weekly report has no job type.** PRD §8 names "weekly report" as a SCHEDULER output but the closed `job_type` enum (`migrations/001_queue.sql`) has no such tag, and PRD §8 also says SCHEDULER "owns crons only, zero business logic" — computing the report in-process would violate that. **Resolution (approved):** add `report.weekly` to the enum (migration 013). PRD §0 dedupe-key table and §8 gain a row; handbook §0 overview table gains row 15. The handler (`app/handlers/report.py`) does the query + plain-text log; the scheduler process only enqueues it on a weekly tick — zero business logic stays true of the *scheduler process*, not of the pipeline as a whole (same split as every other producer).

`dedupe_key: report:{period_key}` where `period_key` is the ISO year-week (`%G-W%V`, e.g. `2026-W30`). Payload `{period_key}` — the handler re-derives everything else from the DB at run time (payload immutability, C9; a report is a snapshot of "now", not of enqueue time).

**GAP B — restart double-fire.** C2 dedupe is active-only (`migrations/001_queue.sql:52`): once a cron's job reaches `done`, its dedupe key is free, so a scheduler process restarting mid-period would re-enqueue. **Resolution (approved):** a `scheduler_run(name, period_key)` ledger table (migration 013, unique on `(name, period_key)`), written in the **same transaction** as the enqueue. A period is "already fired" iff its ledger row exists, independent of the job's own lifecycle.

**GAP C — T0 template ownership (Phase 1 vs Phase 2 inconsistency, followup `2026-07-06 [phases-audit]`).** Not this unit's concern structurally (see `docs/specs/t0-layout.md` unit B), but resolved as: S1.5 owns the engine + the JSON *shape*; the plan on record here was for S2.1 (Phase 2) to source the same shape from `playbooks/{mfr}.json` instead of `app/extract/t0_layout/{mfr}.json`. **Closed in S1.7, not S2.1**: `app/extract/t0_layout/` is deleted and the templates now live in `playbooks/{mfr}.json` alongside the identity/discovery config. No scope pulled forward beyond that — S2.1 still owns crawl recipes, quirks, and the onboarding-agent authoring flow.

**GAP D — monthly ingest has no live file source.** `ingest.run` variant A needs a concrete `ref` (file path); variant B (`BcApiAdapter`) raises `NotImplementedError` without injected records (`app/adapters/source.py:246`) — there is no live BC feed today (G4 residue). A cron literally cannot know which file "is" this month's export. **Resolution (approved, conservative default):** `Scheduler.ingest_watch_dir` defaults to `""` (disabled — mirrors `Storage.gdrive_folder_id`'s "inert until configured" convention). When set, the monthly tick globs `Scheduler.ingest_glob` (default `*.xlsx`) under that dir, takes the most-recently-modified match (`st_mtime`, not a filename sort — a lexicographic sort silently misranks across a digit-count boundary, e.g. "10" before "9"), and enqueues `ingest.run {source: csv, ref: <path>, catalogue: Scheduler.ingest_catalogue}` (catalogue `"LJ"`; there is one — Denis 2026-08-19 closing G17/G11, and the picker was removed from every form on 2026-08-26. The key stays because `scheduler_run` records the monthly ledger as `ingest.monthly:LJ`). No matching file this period → logged, not an error, ledger still written (a month with genuinely no new export is a normal outcome, not a failure).

**GAP E — expiry-horizon scan and failure monitor emit into not-yet-built consumers.** `email.request`'s only live consumer today is the S1.3 DISCOVER dead-end path (`dedupe_key: email.request:{group_id} (bare, S1.3)`, still dormant — followup `discover-email-dedupe`); `playbook.reonboard`'s only consumer is a human + the S2.2 onboarding agent (doesn't exist). **Resolution (approved — "scan for real, emit config-gated off"):** both scans run their real queries and populate the weekly report every period regardless of config. Emission of `email.request` / `playbook.reonboard` is gated by `Scheduler.expiry_email_enabled` / `Scheduler.failure_reonboard_enabled` (both default `False`). Flip when S2.4 / S2.1 land. This also sidesteps a real correctness gap: without S2.4's `renewal_request` state machine, nothing stops the scan from re-emitting the same `email.request:doc:{doc_id}` every week once its prior job reaches `done` (C2 frees the key on completion) — acceptable while the flag is off; tracked as a followup for S2.4 rather than solved here (building a Phase-1 dedup ledger for a Phase-2-only consumer would be speculative).

**GAP F — `playbook.reonboard` payload shape.** Handbook: `{manufacturer_id, reason}`. Phase 1 has no `manufacturer` table rows and no numeric id — `item_group.canonical_manufacturer` is the only identity that exists (text). **Resolution:** emit `{manufacturer: canonical_manufacturer, reason: "discovery-failure-spike", miss_rate, window_days}` (additive-payload-safe substitution of `manufacturer` for `manufacturer_id`; ratify with S2.1 when the `manufacturer` table is seeded — same posture as `resolve-c4`'s "document has no manufacturer column" deferral).

## 2. Module layout

```
app/scheduler.py          # cron bodies + dispatch table, injectable `now`. LIBRARY since 2026-09-02:
                          # the loop, `run_forever`/`main` and the compose service were deleted;
                          # app/handlers/scheduler_tick.py drives the cadence off the queue
app/handlers/report.py    # report.weekly handler (queue-dispatched, registered like every other handler)
migrations/013_scheduler.sql
```

`app/scheduler.py` does **not** go through `app/queue.claim` — it is a producer, not a worker; it calls `app.queue.enqueue` directly (same pattern DISCOVER/RESOLVE/etc. use to emit their *next* stage, just triggered by wall-clock instead of a claimed job).

**Amended 2026-08-31 (`docs/superpowers/specs/2026-08-31-scheduler-onto-queue-design.md`).** The wall-clock loop is replaced by seven perpetual `scheduler.tick` jobs (migrations 054/055, handler `app/handlers/scheduler_tick.py`). `run_forever`/`main` leave this module; the `_tick_*` functions, `already_ran`, `record_run` and the dispatch table stay exactly as specified below and are what the handler calls. Nothing in §1's GAP A–F resolutions, §3's signatures or §6's cases changes: the ledger still decides whether a period has fired, and `run_after` is only how often a cron asks. Why: the process was `--profile full`-gated and had never been started on any machine, and its loop swallowed every error so a lost connection wedged it where dying would have let Docker restart it.

## 3. Function signatures

```python
# app/scheduler.py
def period_key_month(now: datetime) -> str: ...       # "2026-07"
def period_key_isoweek(now: datetime) -> str: ...      # "2026-W30" (ISO %G-W%V)
def period_key_day(now: datetime) -> str: ...           # "2026-07-23"

def already_ran(conn, name: str, period_key: str) -> bool: ...
def record_run(conn, name: str, period_key: str, job_id: int | None) -> None: ...

def _tick_ingest_monthly(conn, cfg: Config, now: datetime) -> dict: ...
def _tick_expiry_scan(conn, cfg: Config, now: datetime) -> dict: ...
def _tick_failure_monitor(conn, cfg: Config, now: datetime) -> dict: ...
def _tick_weekly_report(conn, cfg: Config, now: datetime) -> dict: ...

def tick(conn, cfg: Config, now: datetime) -> dict:
    """Run every due cron once. Each _tick_* is independently wrapped in its
    own `with conn.transaction()` — one cron's failure must not roll back or
    block another's (mirrors runner.run_once's per-job isolation)."""

# def run_forever(...)  -- DELETED 2026-09-02; the cadence is app/handlers/scheduler_tick.py
def main() -> None: ...

# app/handlers/report.py
def handle_report_weekly(conn, job: dict) -> None: ...
def expiring_documents(conn, horizon_days: int) -> list[dict]: ...   # shared by expiry-scan and the report
def job_counts_by_type_status(conn) -> list[dict]: ...
def dead_job_count(conn) -> int: ...
```

`expiring_documents` is the one function both S1.5 routines share (DRY: the scan uses it to decide what to *emit on*, the report uses it to decide what to *list*). Every row it returns carries `lapsed` — whether the date is already behind us — because "within the horizon" says nothing about which side of today it fell on — lives in `report.py` since VALIDATE/GATE/report all read `document`, never write it here (read-only query, no invariant conflict).

**Inherited expiry (migration 020, task 4):** a null `validity_to` is no longer unconditionally "never expiring" — if `document.cert_doc_id` links to a held certificate, `expiring_documents` follows it and uses the certificate's `validity_to` instead (MDR Annex IV requires only a date of issue on a DoC; Article 56 caps the certificate at five years). Row 10 below still holds as stated for a document with **no** `cert_doc_id` — e.g. a Class I item, which has no certificate at all — but a DoC that cites a held certificate now surfaces via that certificate's date.

## 4. Config (new `Scheduler` dataclass, `app/config.py`)

```python
@dataclass(frozen=True)
class Scheduler:
    tick_interval_s: int = 300
    ingest_watch_dir: str = ""          # "" = disabled (GAP D)
    ingest_glob: str = "*.xlsx"
    ingest_catalogue: str = "LJ"
    expiry_scan_interval_hours: int = 24
    expiry_email_enabled: bool = False   # GAP E
    failure_monitor_interval_hours: int = 24
    failure_window_days: int = 30        # lookback for discovery_log miss-rate (added during implementation)
    failure_miss_rate_threshold: float = 0.5
    failure_min_sample: int = 5
    failure_reonboard_enabled: bool = False  # GAP E
```

Env vars: `SCHEDULER_TICK_INTERVAL_S`, `SCHEDULER_INGEST_WATCH_DIR`, `SCHEDULER_INGEST_GLOB`, `SCHEDULER_INGEST_CATALOGUE`, `SCHEDULER_EXPIRY_SCAN_INTERVAL_HOURS`, `SCHEDULER_EXPIRY_EMAIL_ENABLED`, `SCHEDULER_FAILURE_MONITOR_INTERVAL_HOURS`, `SCHEDULER_FAILURE_WINDOW_DAYS`, `SCHEDULER_FAILURE_MISS_RATE_THRESHOLD`, `SCHEDULER_FAILURE_MIN_SAMPLE`, `SCHEDULER_FAILURE_REONBOARD_ENABLED` — added to `.env.example` with the same tuning-keys posture as `RESOLVE`/`Discovery` (documented in this spec, kept out of `.env.example`'s "lists every key" claim only if genuinely a tuning knob; these are operational cadence knobs, not model-calibration ones, so they DO go in `.env.example`, closing rather than adding to the `env-example` followup).

Implementation note: `_tick_expiry_scan`/`_tick_failure_monitor` run at day granularity regardless of their `*_interval_hours` knob (no sub-day cadence is needed today). The knobs are honoured at whole-day resolution; true sub-day scheduling would need the ledger to key on elapsed time (`scheduler_run.ran_at`) rather than a calendar `period_key`.

## 5. Error taxonomy

| Condition | Behaviour |
|---|---|
| One `_tick_*` raises | **Amended 2026-08-31:** with one job per cron the runner provides this structurally — that cron's job backs off and, after `max_attempts`, dead-letters *itself* on `/dead` while the other six stay claimable. A worker restart re-arms it (`runner.arm_crons`), because `dead` is terminal and outside the active-only dedupe index. Previously: logged, the cron's transaction rolled back, other crons still ran inside the same loop. |
| `ingest_watch_dir` set but path doesn't exist | Logged as a miss (`0 files found`), ledger still written — a missing/unmounted dir this period is not fatal to future periods (it degrades identically to "no file yet"). |
| `ingest_watch_dir` set, glob matches ≥1 file | Enqueue `ingest.run`; if `queue.enqueue` dedupes (an interactive/manual ingest of the same file already active) `job_id` in the ledger row is `None` — that's fine, `already_ran` only cares about the ledger, not the job outcome. |
| `already_ran(name, period_key)` True | Tick is a no-op for that cron this call — not an error, not logged above DEBUG (every 5-minute tick would otherwise spam INFO). |
| SIGTERM/SIGINT | Same as `runner.py`: finish the in-flight tick, then exit; no mid-tick kill. |

## 6. Table-driven test cases

All against real Postgres, `now` injected (no `datetime.now()` in the code under test — matches the workflow-script constraint pattern already used across this codebase's tests).

| # | Case | Setup | Assert |
|---|---|---|---|
| 1 | Tick before any period boundary | fresh DB, `now` = mid-month, mid-week | `tick()` returns all-zero counts; `scheduler_run` empty |
| 2 | Monthly ingest fires once | `ingest_watch_dir` set to a temp dir with one `.xlsx`; `now` = any day | one `ingest.run` job (`source=csv`, correct `ref`/`catalogue`), one `scheduler_run` row `('ingest.monthly:LJ', '2026-07')` |
| 3 | Same month, second tick | continue from #2, same `now` month | zero new jobs, zero new ledger rows (idempotent) |
| 4 | Restart safety (GAP B) | ledger row pre-inserted directly (simulating a prior process's completed+freed job), job table has no active `ingest.run` | tick does **not** re-enqueue — ledger, not job status, gates it |
| 5 | Month rolls over | `now` advances one month | fires again, new `period_key`, second ledger row |
| 6 | Atomicity | force an exception after `queue.enqueue` succeeds inside `_tick_ingest_monthly` (monkeypatch `record_run` to raise) | transaction rolls back — neither the job nor the ledger row persists; next tick re-fires cleanly |
| 7 | No watch dir configured | default config (`ingest_watch_dir=""`) | tick is a documented no-op every period, ledger still written (so nothing "catches up" retroactively if configured later mid-month) |
| 8 | Expiry scan — production doc inside horizon | `document(status=production, validity_to=now+10d)` linked via a `production` `item_document` | appears in `expiring_documents(horizon=90)` |
| 9 | Expiry scan — excluded states | same but `status=staged` / `status=superseded` / `status=rejected` | absent from the list (production-only, matches the PRD §9 visibility rule) |
| 10 | Expiry scan — null validity_to (C7 parity) | `document(validity_to=NULL)` | absent (never a false "expiring soon") |
| 11 | Expiry scan — outside horizon | `validity_to=now+200d` | absent |
| 12 | Expiry emit gated off | default config, doc inside horizon | 0 `email.request` jobs; doc still appears in the report |
| 13 | Expiry emit gated on | `expiry_email_enabled=True`, doc inside horizon | one `email.request {doc_id, state}` job, `dedupe_key=email.request:doc:{doc_id}` (namespaced against S1.3's bare `email.request:{group_id}` — doc_id/group_id are independent sequences) |
| 13b | Expiry emit on an ALREADY-lapsed document | `expiry_email_enabled=True`, doc expired 800 days ago | still emitted — a lapsed certificate needs the chase more, not less — but carries `state: "lapsed"` rather than `"upcoming"`, and the report counts it under `lapsed` not `expiring`. The horizon has no lower bound and must not grow one: measured 2026-08-18, a 30-day scan returned 61 production documents and every one had already lapsed, the oldest by 2237 days, so the old untagged emit would have told 61 manufacturers their certificate was "about to expire" |
| 14 | Failure monitor — spike detected | `discovery_log` seeded with a manufacturer's group at miss-rate > threshold, sample ≥ `failure_min_sample` | appears in the report's failure list |
| 15 | Failure monitor — below sample floor | same miss-rate but sample < `failure_min_sample` | excluded (avoids flagging a manufacturer off 1-2 groups) |
| 16 | Failure monitor emit gated off | default config | 0 `playbook.reonboard` jobs |
| 17 | Failure monitor emit gated on | `failure_reonboard_enabled=True`, spike present | one `playbook.reonboard {manufacturer, reason, miss_rate, window_days}` job |
| 18 | Weekly report — fires once per ISO week | fresh DB | one `report.weekly {period_key}` job per distinct ISO week; a second tick same week is a no-op |
| 19 | Weekly report handler content | seed job counts, a dead job, an expiring doc | `handle_report_weekly` logs (or returns, for test assertion) a payload containing the expiring list + `job_counts_by_type_status` + `dead_job_count` |
| 20 | `report.weekly` dispatches through the real runner | enqueue directly, run via `runner.run_once` | handler registered in `HANDLERS`, executes without `LookupError` |
| 21 | One raising cron doesn't block others | monkeypatch `_tick_failure_monitor` to raise | `_tick_ingest_monthly`/`_tick_weekly_report` still ran (their ledger rows exist) |
| 22 | ~~SIGTERM~~ | **Obsolete 2026-09-02.** There is no loop to signal: a cron is a queue job, so shutdown is the worker's SIGTERM path (`app/workers/runner.py`) and an interrupted tick is simply reclaimed. | — |

## 7. Ratifications needed (non-blocking, same posture as prior sessions' `multi-group-match`/`singleton`)

- `report.weekly` as job-type #15 in handbook §0's overview table + a new `## 15. report.weekly` section (payload, reads, writes=none, emits=none).
- PRD §0 dedupe-key table gains `report.weekly → report:{period_key}`; §8 SCHEDULER row gains the tag explicitly instead of prose "weekly report".
