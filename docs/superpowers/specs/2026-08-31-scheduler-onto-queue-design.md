# Move the cron scheduler onto the queue — design

**Date:** 2026-08-31
**Status:** proposed, awaiting Denis review
**Scope:** all three slices below. Missed-period catch-up is explicitly **out** (§10).
**Decided in brainstorm (Denis, 2026-08-31):** per-cron cadence over a single tick (§1);
one job type with the cron in the payload over seven tags (§1); bootstrap **and** a
visible re-arm surface over bootstrap alone (§3).

Normative sources this spec defers to and does not restate:
- CLAUDE.md invariants 7 (closed job-type enum), 8 (active-only dedupe), 9 (payload
  immutability), 10 (self-contained audit).
- PRD v3 §0 (dedupe-key table), §8 (peripheral producers).
- `docs/specs/scheduler.md` — the S1.5 unit spec. §2 and §5 are amended by this
  document; §1 (GAP A–F), §3 and §6 are untouched.
- `migrations/001_queue.sql` (job table, statuses, priorities, dedupe index),
  `migrations/013_scheduler.sql` (`scheduler_run` ledger).

## 0. The failure this fixes

Three symptoms, and they do not share one cause. Ordered by what is actually observed.

**A. Not running — the observed cause, and the simple one.** The `scheduler` service sits
behind `profiles: ["full"]` (`docker-compose.yml:199`), so `docker compose up -d` does not
start it. Verified 2026-08-31: `docker compose ps -a` lists caddy, migrate, postgres, test,
web and worker — **no scheduler container exists, running or stopped, and `docker ps -a`
finds none by that name either.** The service has never been instantiated on this machine.
`scheduler_run` does hold 11 firings across 5 crons on 2026-08-20/21, so the module has run
somehow; how is not recoverable — an exited `--rm` container leaves no trace, and the
`/scheduler` run-now route deliberately does not write the ledger (`web/scheduler_view.py`),
so it was not that. This spec does not guess.

The move fixes A by deletion: there is no separate service to forget to start. Crons ride
the worker, which is in the default `up` set and running now.

**B. Not persisting.** The cron *schedule* today exists only inside a live process's `while`
loop. `scheduler_run` persists what has fired; nothing persists what is *due*. After the
move the schedule is seven rows in `job` — durable across restarts, redeploys and reboots,
inspectable with `psql`, and visible on `/dead` when one breaks.

**C. Not triggering.** Every period key is `now`-derived, so a period nobody was up for is
skipped rather than deferred. On the queue, a tick job whose `run_after` is six days in the
past is claimable immediately, so a restart fires the current period at once instead of
waiting for the next boundary. (This is not catch-up of the *missed* periods — see §10.)

**D. The latent one: the loop cannot die, so nothing restarts it.** Not observed, and it
cannot have caused the current outage, because the process has not been running at all. It
is real in the code and would bite once the service is up. `run_forever` opens one
connection outside its loop (`app/scheduler.py:477`) and there is **no reconnect logic
anywhere in `app/`** (verified by grep for `reconnect` / `OperationalError`: zero hits).
Every exception path inside the loop is swallowed — `heartbeat.beat` by contract
(`app/heartbeat.py:52-56`), each cron by `tick()` (`app/scheduler.py:450-453`), and `tick()`
itself again (`app/scheduler.py:486-490`). So a dead connection makes the process spin every
300s forever, doing nothing, while `restart: unless-stopped` waits for an exit that never
comes and the 900s healthcheck goes unhealthy with nothing acting on it (plain Compose does
not restart on healthcheck failure; there is no autoheal service in the file).

**`app/workers/runner.py:162-185` has the identical shape and the same missing reconnect.**
That matters more after this move than before it: once crons ride the worker, a wedged
worker stops the crons too. §3.1 folds the guard in rather than deferring it.

**E. What the ledger actually proves.** Traced 2026-08-31 by joining `scheduler_run` to
`job`: of the 11 ledger rows, **exactly one carries a job id** — `report:2026-W34`, id
35287, and it dead-lettered after 5 attempts (`TypeError: Object of type date is not JSON
serializable`, PHASES.md:125). Every other row has `job_id NULL` because its emission is
config-gated off, by design. So the scheduler has produced exactly one job in its life and
that job died. Whatever replaces the loop must survive this, not amplify it — hence §3.

Two things this rules out, and one it proves:
- The cron *handlers* are not the problem. All ten cron-type jobs in the queue ran; seven
  carry hand-written dedupe keys (`manual-`, `shapes-`, `zip-expand`, `reseed-`) from CLI
  enqueues during development, and they reached `done`. `email.poll` ran seven times,
  `eudamed.certregister` once on 2026-08-27. **Only the trigger is missing.**
- PHASES.md:125's claim that the W34 report "is still lost" is **stale**: a replacement
  `report:2026-W34` (id 35532) ran to `done` on 2026-08-21 07:28. `dead` is terminal and
  frees the dedupe key, which is the same mechanism §3 relies on for re-arming. Correct
  PHASES.md when slice 1 lands.

## 1. Mechanism

One new job type (migration 053):

```sql
ALTER TYPE job_type ADD VALUE 'scheduler.tick';
```

Seven perpetual rows, one per cron, `priority = 'delta'`:

| payload | dedupe_key |
|---|---|
| `{cron: "ingest.monthly"}` | `scheduler.tick:ingest.monthly` |
| `{cron: "expiry-scan"}` | `scheduler.tick:expiry-scan` |
| `{cron: "failure-monitor"}` | `scheduler.tick:failure-monitor` |
| `{cron: "report.weekly"}` | `scheduler.tick:report.weekly` |
| `{cron: "email.poll"}` | `scheduler.tick:email.poll` |
| `{cron: "eudamed.certregister"}` | `scheduler.tick:eudamed.certregister` |
| `{cron: "eudamed.sweep-due"}` | `scheduler.tick:eudamed.sweep-due` |

**One tag, not seven.** The cron is data, exactly as `email.request` carries `state` in
its payload rather than splitting the tag. Seven enum values would make every future
cron a contract change; one makes it a row.

**`delta`, not `interactive` or `sweep`.** `job_priority` ordinals are
`interactive=0, delta=1, sweep=2` (`migrations/001_queue.sql:31`) and `claim` orders by
`priority, run_after`. `interactive` is reserved for human-triggered work; `sweep` would
let a daily scan starve for hours behind a backlog.

New handler `app/handlers/scheduler_tick.py`, roughly 30 lines: look up the cron by
payload key, call the existing `_tick_*` function, `queue.defer(conn, job["id"], poll_s)`,
return `{"_deferred": True, **result}`. The `_deferred` contract is honoured at
`app/workers/runner.py:81`; `queue.defer` refunds the claim's attempt increment
(`app/queue.py:247-255`), so a perpetual tick never burns retries.

The dispatch table is the one `tick()` already iterates, lifted out unchanged.

## 2. Cadence: `run_after` is a poll interval, not a fire time

This is the load-bearing decision and the reason the move is small.

`scheduler_run(name, period_key)` remains the **sole** authority on whether a period has
fired, exactly as today. `run_after` decides only how often a cron *asks*. Therefore:

- Defer precision does not matter; drift is harmless.
- **The move cannot change when anything fires.** It changes which process asks.
- Period keys stay `now`-derived (`app/periods.py`), untouched.

Poll intervals, all a fraction of their period:

| cron | period | poll |
|---|---|---|
| `ingest.monthly` | month | 6h |
| `expiry-scan` | day | 1h |
| `failure-monitor` | day | 1h |
| `report.weekly` | ISO week | 1h |
| `email.poll` | `email_poll_interval_hours` band | 15m |
| `eudamed.certregister` | `eudamed_certregister_interval_days` bucket | 6h |
| `eudamed.sweep-due` | none — RULING 45 | 1h |

`eudamed.sweep-due` deliberately has no ledger (`app/scheduler.py:343`, RULING 45) and is
an idempotent UPSERT gated on `due_at IS NULL`. Polling *is* its correctness model
already; nothing changes for it.

**Nested transactions.** Each `_tick_*` opens `with conn.transaction():`, and the runner
already holds one around the handler call. No handler in `app/handlers/` does this today,
so this design is the first. Verified empirically, not assumed: psycopg3 nests as
savepoints, an inner failure rolls back only its own writes and the outer transaction
survives. The inner blocks become redundant — their purpose was isolating crons inside one
loop, which separate jobs now provide — but they are harmless and leaving them in is what
keeps §7's 42 untouched tests green.

**Bound:** `claim`'s visibility timeout is 300s (`app/queue.py:116`), so a tick handler
running longer than that could be double-claimed. All seven are sub-second in-DB work.
This caps what a cron handler may ever do and should stay in the handler's docstring.

## 3. Failure containment

**Isolation becomes structural.** Today one cron's failure is contained by a `try/except`
inside `tick()`. With seven rows the runner provides it: separate jobs, separate
transactions, separate backoff. A crashing cron takes the normal path — backoff, 5
attempts, `/dead` — and stops exactly one cron. That is the visible death the current
design cannot produce.

**Bootstrap.** `runner.main()` enqueues all seven before `run_forever()`. `enqueue` is
`ON CONFLICT (dedupe_key) WHERE status IN ('pending','running','failed') DO NOTHING
RETURNING id` (`app/queue.py:98-109`), so it returns `None` and does nothing when a cron
is live. `dead` is a **distinct** status from `failed` (`migrations/001_queue.sql:30`) and
is not in that index, so a dead-lettered cron's key is free and any worker restart
re-arms it.

**Visible before a restart.** `/scheduler` gains a per-cron column: live job y/n, next
poll (`run_after`), last ledger period, and a re-arm button that re-enqueues. The existing
`_STALE_AFTER_PERIODS = 2` rule (`web/scheduler_view.py:38`) is unchanged.

Bootstrap alone was rejected: between a dead-letter and the next worker restart the cron
is silently stopped, and the only trace is a `/dead` row nobody is watching for.

### 3.1 The worker loop must be allowed to die

Folded in from what was a deferred item, because after this move it stops being a separate
concern: cron liveness becomes worker liveness, so the worker's wedge (§0 D) becomes the
single point of failure for every cron.

The fix is small and applies to `app/workers/runner.py:run_forever`. psycopg3 exposes
`conn.closed` and `conn.broken` on a live connection (verified 2026-08-31), so the loop can
check its own connection and exit rather than spin:

```python
if conn.closed or conn.broken:
    raise RuntimeError("database connection lost; exiting so the supervisor restarts us")
```

Placed at the top of the loop body, before the beat. Exiting is the correct behaviour
precisely because `restart: unless-stopped` is already configured and only fires on exit.
Nothing else about the loop changes — a raising *handler* is still caught and dead-lettered
as today; only a lost connection is fatal.

This is the one change in the design that touches the worker rather than the scheduler, and
it is what makes the answer to "will crons run" a yes rather than a yes-unless.

## 4. What is deleted

| Deleted | Note |
|---|---|
| `run_forever()`, `main()` in `app/scheduler.py` | ~40 lines. `_tick_*`, `already_ran`, `record_run` and the dispatch table stay — the module stops being an entrypoint and becomes a library the handler calls |
| `scheduler` service in `docker-compose.yml` | takes the `full` profile with it (sole member, `docker-compose.yml:199`) and its 900s healthcheck |
| `scheduler` entry in `_SERVICES` (`web/app.py:1703`) | it would read `off` forever. Cron liveness moves to §3's per-cron rows, which say strictly more |

`app/cli.py healthcheck` keeps working; only its `--service scheduler` use disappears.

Docs to update in the same slice: `docs/runbook.md`, `docs/architecture.md`,
`docs/code-map.md`, `docs/specs/scheduler.md` §2 and §5.

## 5. Contract delta

- **Migrations 053 and 054**, two files: `ALTER TYPE job_type ADD VALUE` cannot run in a
  transaction that also uses the new value, and `run_migrations` gives each file one
  transaction (`app/db.py:106-109`). 053 adds the value; 054 seeds the seven rows. This is
  the convention every prior enum migration in this repo follows — see 013's header.
- **PRD §0**: dedupe-key row `scheduler.tick → scheduler.tick:{cron}`.
- **PRD §8**: SCHEDULER's *Emits* column is unchanged. Its note changes from a process
  that owns crons to a job type that carries them; the "owns crons only, zero business
  logic" rule still holds, because the handler calls the same `_tick_*` functions.
- **Handbook §0**: new overview row and a `## scheduler.tick` section — payload,
  reads (`scheduler_run`), writes (`scheduler_run`), emits (whatever the cron emits).
- **Schema sketch §7**: `scheduler.tick` writes `scheduler_run` and `job`.

Invariant 1 is untouched — no registry write is added or moved.

## 6. UI

`web/scheduler_view.py` `_cron_specs()` currently lists **5 crons while `tick()` runs 7**:
both EUDAMED crons (added 2026-08-27) have no UI representation at all. Pre-existing drift,
folded into slice 3 because it is the same function being edited.

The move also removes an existing limitation the module documents at
`web/scheduler_view.py:11-13`: `expiry-scan` and `failure-monitor` are rendered
**unrunnable** because "there is no job type for them and the enum is closed". This design
creates one, so both become runnable. `ingest.monthly` also carries `job_type: None`
(`web/scheduler_view.py:62`) but stays unrunnable for an unrelated and deliberate reason —
the watch dir is not mounted in `web`.

`/scheduler/run/{cron}` keeps its current semantics: it enqueues what the cron would
enqueue and deliberately does **not** write `scheduler_run`.

## 7. Testing

**Unchanged: 42 of 44 in `tests/test_scheduler.py`.** They exercise `_tick_*`, the ledger,
and the period keys, none of which move.

**Rewritten: 2.** `test_tick_before_any_boundary_returns_zero_counts`
(`tests/test_scheduler.py:205`) and `test_tick_one_raising_cron_does_not_block_others`
(`:222`) both call `tick()` directly. The second is testing the isolation that moves to
the runner, so it is rewritten to assert isolation at the job level rather than deleted.

**New, in `tests/test_scheduler_tick.py`:**

| # | Case | Assert |
|---|---|---|
| 1 | Handler dispatches by payload key | each of the seven reaches its own `_tick_*` |
| 2 | Unknown cron key | raises; job fails loudly rather than silently no-opping |
| 3 | Handler self-defers | `status='pending'`, `run_after` advanced, `attempts` unchanged from before the claim |
| 4 | Dispatch through the real runner | registered in `HANDLERS`, no `LookupError` |
| 5 | One cron dead-letters | that row `dead`, the other six still `pending` and claimable |
| 6 | Bootstrap when live | returns `None`, row count stays 7 |
| 7 | Bootstrap after dead-letter | re-arms; a fresh `pending` row exists for that cron |
| 8 | Ledger still gates firing | two polls inside one period fire once |
| 9 | Old process and new job coexist (slice 1) | both run against one period, fires once |
| 10 | Migration seed | after `054`, seven `pending` rows exist with the right dedupe keys |
| 11 | Worker exits on a lost connection (§3.1) | `run_forever` raises rather than spinning when `conn.broken` |

Selection rule: `app/queue.py` is untouched, so this is `tests/test_scheduler*.py` plus
`tests/test_runner.py`, and `tests/test_web.py` for slice 3. Full suite before each commit.

## 8. Files, by slice

Eleven files total, so the work splits. Each slice ships independently.

**Slice 1 — tag, handler, bootstrap.** Crons run on the queue.
- `migrations/053_scheduler_tick.sql` (new) — adds the enum value, nothing else.
- `migrations/054_scheduler_tick_seed.sql` (new) — seeds the seven rows. **It must be a
  second file:** `run_migrations` applies each file whole inside one transaction
  (`app/db.py:106-109`), and `ALTER TYPE ... ADD VALUE` cannot be used in the same
  transaction that also uses the new value. Migration 013 hit this exact constraint and
  records it in its header. Seeding matters because an already-running worker never
  re-enters `main()`, so without it the crons would not exist until something recreated
  the container.
- `app/handlers/scheduler_tick.py` (new)
- `app/handlers/__init__.py` (register)
- `app/workers/runner.py` (bootstrap in `main()`, plus §3.1's connection guard)
- `tests/test_scheduler_tick.py` (new), `tests/test_scheduler.py` (2 rewrites)
- PRD §0/§8, handbook, schema sketch

The seed and the bootstrap are the same `enqueue` and are each other's backstop: the seed
covers first deploy, the bootstrap covers re-arming after a dead-letter. Both are no-ops
when a cron is live.

Safe to ship **with the old process still running**: both compute the same period key and
`already_ran` gates the second. No double-fire — test case 9 pins this.

**Slice 2 — delete the process.**
- `app/scheduler.py` (drop `run_forever`, `main`)
- `docker-compose.yml` (drop service + `full` profile)
- `web/app.py` (drop the chip)
- `docs/runbook.md`, `docs/architecture.md`, `docs/code-map.md`, `docs/specs/scheduler.md`

**Slice 3 — UI.**
- `web/scheduler_view.py` (per-cron rows, re-arm, the 2 missing EUDAMED crons)
- `web/templates/scheduler.html`
- `tests/test_scheduler_web.py`

## 9. Invariants

- **7 (closed enum):** honoured — one value, added by migration, PRD first.
- **8 (active-only dedupe):** load-bearing here rather than merely respected. The
  perpetual row holds its key while `pending`/`running`/`failed`; `dead` releases it,
  which is what makes §3's re-arm work.
- **9 (payload immutable):** the payload is `{cron}` and never rewritten. Cadence lives in
  `run_after`, which is queue state, not payload.
- **10 (audit self-contained):** unchanged. `scheduler_run` is not audit and nothing
  references it.
- **1 (registry writes):** untouched.

## 10. Deferred, deliberately

**Missed-period catch-up.** Every period key is `now`-derived, so a period the stack was
down for is still skipped — a week down still loses that week's report. The move shrinks
the outage window from "until someone notices" to "until a worker restarts", but it does
not add replay. Doing so needs the ledger to drive the key rather than `now`, plus a
per-cron ruling on whether a three-week-old weekly report is worth firing at all. Denis
chose liveness + per-cron cadence over this on 2026-08-31.

**Followup `[scheduler-report-imports]`** (`tasks/followups.md:208`) dissolves as a side
effect — `app/scheduler.py` importing from `app/handlers/report.py` stops being the
coupling direction §10 warns about once the tick is itself a handler. Close it when slice 2
lands rather than doing it separately.

## 11. Findings surfaced while writing this

Neither is caused by this work; both were verified this session.

1. **Migration numbering has a hole.** `047` and `048` exist neither on disk nor in
   `schema_migrations` (044, 045, 046, then 049). `053` is still the correct next number,
   but anyone later filling 047/048 trips `db._out_of_order`.
2. **`/scheduler` shows 5 of 7 crons** — see §6. Folded into slice 3.
