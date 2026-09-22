# Scheduler Live Implementation Plan

**Goal:** Get the cron scheduler actually running and configurable before next week's deploy, then move it onto the queue so its schedule is durable state rather than a process nobody starts.

**Architecture:** Tasks 1-2 fix the two live defects — the service is profile-gated so `docker compose up -d` skips it, and 14 of its 16 config keys never reach the container. Tasks 3-8 replace the wall-clock loop with seven perpetual self-deferring `scheduler.tick` jobs; the `scheduler_run` ledger stays the sole authority on what has fired, so *when* anything fires does not change.

**Tech Stack:** Python 3.12, psycopg 3, raw SQL, pytest against real Postgres, Docker Compose, FastAPI + Jinja + HTMX.

**Spec:** `docs/superpowers/specs/2026-08-31-scheduler-onto-queue-design.md`

## Global Constraints

- **Every test run goes through `./scripts/test.sh`.** A PreToolUse hook blocks bare `pytest`. The stack must already be up (`docker compose up -d`, then `docker compose --profile test up -d test`).
- **Job types are a closed enum (invariant 7).** `scheduler.tick` requires the PRD change in Task 6 and the migration in Task 3 before any code uses it.
- **`ALTER TYPE ... ADD VALUE` cannot run in a transaction that also uses the new value.** `run_migrations` gives each file one transaction (`app/db.py:106-109`), so the enum add and the seed are two files.
- **Dedupe is active-only** (`job_dedupe_active_uq`, `status IN ('pending','running','failed')`). `dead` is a distinct terminal status and frees the key — this is what makes re-arming work.
- **Handlers must not commit.** The worker owns the transaction. A handler that self-defers returns `{"_deferred": True}` and the runner skips `finish`.
- **No Claude/AI attribution in commit messages.**
- **Selection rule:** touching `app/config.py`, `app/db.py`, `app/queue.py`, `migrations/` or `tests/conftest.py` means the **full suite**. Otherwise `tests/test_<module>*.py` plus `tests/test_web.py` if `web/` changed. Say which subset ran; a subset is never evidence the change is green. Full suite before every commit.

## Phase gates

| Tasks | Phase | Gate |
|---|---|---|
| 1-2 | **Deploy blockers** | must land before next week's deploy |
| 3-6 | Queue move (spec slice 1) | ships safely alongside the running process |
| 7 | Delete the process | only after 3-6 verified live |
| 8 | UI | independent, any time after 3-6 |

## File structure

| File | Responsibility | Task |
|---|---|---|
| `docker-compose.yml` | un-gate `scheduler`; declare all 16 `SCHEDULER_*` on `scheduler`, `web`, later `worker` | 1, 5, 7 |
| `tests/test_compose_config.py` (new) | drift guard: every key `app/config.py` reads is declared on every service that needs it | 1 |
| `app/workers/runner.py` | connection guard; handler import; bootstrap in `main()` | 2, 4, 5 |
| `app/scheduler.py` | connection guard; later loses `run_forever`/`main` and becomes a library | 2, 7 |
| `tests/test_loop_liveness.py` (new) | both loops exit on a lost connection | 2 |
| `migrations/054_scheduler_tick.sql` (new) | enum value only | 3 |
| `migrations/055_scheduler_tick_seed.sql` (new) | the seven rows | 3 |
| `app/handlers/scheduler_tick.py` (new) | dispatch one cron, then self-defer | 4 |
| `tests/test_scheduler_tick.py` (new) | handler behaviour, isolation, re-arm | 4, 5 |
| `web/scheduler_view.py`, `web/templates/scheduler.html` | per-cron live-job column, re-arm, the 2 missing EUDAMED crons | 8 |

---

### Task 1: Un-gate the scheduler and make its config reachable

**Files:**
- Create: `tests/test_compose_config.py`
- Modify: `docker-compose.yml:5` (header comment), `:199` (drop `profiles`), `:214-219` (scheduler `environment`), `:270-271` (web `environment`)
- Modify: `docs/runbook.md:23`, `docs/architecture.md:33`

**Interfaces:**
- Consumes: nothing.
- Produces: a running `scheduler` service on plain `docker compose up -d`, and 16 working `SCHEDULER_*` env keys. Task 5 reuses the same key list for the `worker` service.

**Why this task exists.** Verified 2026-08-31 inside the running worker:

```
SCHEDULER_TICK_INTERVAL_S=[]
SCHEDULER_EXPIRY_EMAIL_ENABLED=[]
```

There is no `env_file:` anywhere in `docker-compose.yml`. Compose reads `.env` for `${}` interpolation only — it does not inject it into containers. A variable reaches a container only if listed under `environment:`. `scheduler` and `web` each list 2 of the 16 keys `app/config.py` reads.

- [ ] **Step 1: Write the failing test**

Create `tests/test_compose_config.py`:

```python
"""docker-compose.yml declares every config key the code reads.

Compose reads `.env` for ${} interpolation only; it does NOT inject it into
containers (there is no `env_file:` in this file). A key absent from a
service's `environment:` block is unsettable, silently, and the process runs
on its code default. Verified live 2026-08-31: 14 of the 16 SCHEDULER_* keys
were unreachable on both `scheduler` and `web`.
"""
from __future__ import annotations

import pathlib
import re

import pytest

yaml = pytest.importorskip("yaml")

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _keys_config_reads(prefix: str) -> set[str]:
    src = (_ROOT / "app" / "config.py").read_text()
    return set(re.findall(rf'"({prefix}[A-Z_]+)"', src))


def _keys_service_declares(service: str, prefix: str) -> set[str]:
    compose = yaml.safe_load((_ROOT / "docker-compose.yml").read_text())
    env = compose["services"][service].get("environment") or {}
    return {k for k in env if k.startswith(prefix)}


@pytest.mark.parametrize("service", ["scheduler", "web"])
def test_every_scheduler_key_reaches_the_container(service):
    missing = _keys_config_reads("SCHEDULER_") - _keys_service_declares(service, "SCHEDULER_")
    assert missing == set(), (
        f"{service} cannot be configured for: {sorted(missing)}. "
        "Compose does not inject .env; add them to that service's environment: block."
    )


def test_the_scheduler_starts_without_a_profile_flag():
    compose = yaml.safe_load((_ROOT / "docker-compose.yml").read_text())
    assert "profiles" not in compose["services"]["scheduler"], (
        "a profile-gated scheduler is skipped by `docker compose up -d`; "
        "it produced no cron run between 2026-08-21 and 2026-08-31"
    )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./scripts/test.sh tests/test_compose_config.py`
Expected: 3 FAIL — two with `cannot be configured for: [...14 keys...]`, one with `profiles` still present.

- [ ] **Step 3: Drop the profile and the header line**

`docker-compose.yml:199` — delete the whole line:

```yaml
    profiles: ["full"]
```

`docker-compose.yml:5` — replace:

```yaml
# start with `docker compose --profile full up`.
```

with:

```yaml
# The scheduler starts with everything else; it is not profile-gated. It was,
# until 2026-08-31, and the consequence was 10 days with no cron run at all.
```

- [ ] **Step 4: Declare all 16 keys on the scheduler service**

Replace the `environment:` block at `docker-compose.yml:214-219` with:

```yaml
    environment:
      DATABASE_URL: postgresql://${POSTGRES_USER:-dentalia}:${POSTGRES_PASSWORD:-dentalia}@postgres:5432/${POSTGRES_DB:-dentalia}
      # ALL SCHEDULER_* keys, not a subset. Compose reads .env only for ${}
      # interpolation and injects nothing, so a key missing here is a key that
      # cannot be set at all -- the process silently takes its code default.
      # Measured 2026-08-31: 14 of 16 were missing, including
      # SCHEDULER_INGEST_WATCH_DIR, which is PHASES.md's GAP D "needs Denis".
      # tests/test_compose_config.py keeps this list honest.
      SCHEDULER_TICK_INTERVAL_S: ${SCHEDULER_TICK_INTERVAL_S:-300}
      SCHEDULER_INGEST_WATCH_DIR: ${SCHEDULER_INGEST_WATCH_DIR:-}
      SCHEDULER_INGEST_GLOB: ${SCHEDULER_INGEST_GLOB:-*.xlsx}
      SCHEDULER_INGEST_CATALOGUE: ${SCHEDULER_INGEST_CATALOGUE:-LJ}
      SCHEDULER_EXPIRY_SCAN_INTERVAL_HOURS: ${SCHEDULER_EXPIRY_SCAN_INTERVAL_HOURS:-24}
      SCHEDULER_EXPIRY_EMAIL_ENABLED: ${SCHEDULER_EXPIRY_EMAIL_ENABLED:-false}
      SCHEDULER_FAILURE_MONITOR_INTERVAL_HOURS: ${SCHEDULER_FAILURE_MONITOR_INTERVAL_HOURS:-24}
      SCHEDULER_FAILURE_WINDOW_DAYS: ${SCHEDULER_FAILURE_WINDOW_DAYS:-30}
      SCHEDULER_FAILURE_MISS_RATE_THRESHOLD: ${SCHEDULER_FAILURE_MISS_RATE_THRESHOLD:-0.5}
      SCHEDULER_FAILURE_MIN_SAMPLE: ${SCHEDULER_FAILURE_MIN_SAMPLE:-5}
      SCHEDULER_FAILURE_REONBOARD_ENABLED: ${SCHEDULER_FAILURE_REONBOARD_ENABLED:-false}
      # S2.4: gates EMISSION of email.poll only -- the handler is the worker's.
      SCHEDULER_EMAIL_POLL_ENABLED: ${SCHEDULER_EMAIL_POLL_ENABLED:-false}
      SCHEDULER_EMAIL_POLL_INTERVAL_HOURS: ${SCHEDULER_EMAIL_POLL_INTERVAL_HOURS:-6}
      SCHEDULER_EUDAMED_CERTREGISTER_ENABLED: ${SCHEDULER_EUDAMED_CERTREGISTER_ENABLED:-false}
      SCHEDULER_EUDAMED_CERTREGISTER_INTERVAL_DAYS: ${SCHEDULER_EUDAMED_CERTREGISTER_INTERVAL_DAYS:-30}
      SCHEDULER_EUDAMED_SWEEP_INTERVAL_DAYS: ${SCHEDULER_EUDAMED_SWEEP_INTERVAL_DAYS:-90}
```

Every default above is copied from the `Scheduler` dataclass (`app/config.py:500-535`). Do not invent values.

- [ ] **Step 5: Mirror the same 16 onto `web`**

At `docker-compose.yml:270-271`, replace the two `SCHEDULER_EMAIL_POLL_*` lines with the identical 16-line block from Step 4 (without `DATABASE_URL`). The file's own comment there requires it: the `/scheduler` panel states these flags from its own env, so a key on one service and not the other makes the panel describe a process that is running on something else.

- [ ] **Step 6: Run the test to verify it passes**

Run: `./scripts/test.sh tests/test_compose_config.py`
Expected: 3 PASS.

- [ ] **Step 7: Update the two docs that say `--profile full`**

`docs/runbook.md:23` — replace:

```
docker compose --profile full up -d       # also start the scheduler (cron producer, S1.5)
```

with:

```
docker compose up -d                      # includes the scheduler (cron producer, S1.5)
```

`docs/architecture.md:33` — in the `scheduler` row, replace `gated behind \`--profile full\`` with `starts with the stack (was \`--profile full\`-gated until 2026-08-31)`.

- [ ] **Step 8: Bring it up and verify a real tick**

```bash
docker compose up -d
sleep 20
docker compose logs --tail=20 scheduler
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT name, period_key, ran_at FROM scheduler_run ORDER BY ran_at DESC LIMIT 8;"'
```

Expected: new rows dated today. `report.weekly` fires for the **current** ISO week; the last recorded was `2026-W34` and today is `2026-W36`, so W35 is permanently skipped — that is the no-catch-up limitation (spec §10), not a bug. Confirm the `scheduler` chip on the web header strip is green.

- [ ] **Step 9: Full suite, then commit**

```bash
./scripts/test.sh
git add tests/test_compose_config.py docker-compose.yml docs/runbook.md docs/architecture.md
git commit -m "compose: the scheduler starts with the stack, and its config can finally be set

Profile-gated since S0.1, so `docker compose up -d` skipped it: no cron ran
between 2026-08-21 and 2026-08-31. Compose also injects no .env, so 14 of the
16 SCHEDULER_* keys never reached the container and every flag silently took
its code default. Both services now declare all 16, and a test keeps the list
honest against app/config.py."
```

---

### Task 2: Neither loop may wedge

**Files:**
- Create: `tests/test_loop_liveness.py`
- Modify: `app/workers/runner.py:169-174`, `app/scheduler.py:480-484`

**Interfaces:**
- Consumes: nothing.
- Produces: both `run_forever` functions raise `RuntimeError` on a lost connection instead of spinning. No signature changes.

**Why.** Both loops swallow every exception — `heartbeat.beat` by contract (`app/heartbeat.py:52-56`), each cron by `tick()`, and `tick()` again by `run_forever`. There is no reconnect logic anywhere in `app/`. So a dead connection makes the process spin forever, `restart: unless-stopped` waits for an exit that never comes, and the healthcheck goes unhealthy with nothing acting on it (plain Compose does not restart on healthcheck failure; there is no autoheal service). Exiting is correct **because** the restart policy is already configured.

This matters more after Task 5: once crons ride the worker, a wedged worker stops every cron.

- [ ] **Step 1: Write the failing test**

Create `tests/test_loop_liveness.py`:

```python
"""A process that cannot reach the database must die, not spin.

Both loops swallow every exception by design, so a lost connection leaves them
alive and useless -- and `restart: unless-stopped` only fires on exit. Compose
does not restart on a failing healthcheck, so nothing else would recover them.
"""
from __future__ import annotations

import pytest

from app import scheduler as sched
from app.workers import runner


class _BrokenConn:
    """What psycopg hands back after the server goes away."""

    closed = False
    broken = True

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Cfg:
    class connection:
        anthropic_api_key = "sk-ant-present"
        database_url = "postgresql://unused/unused"

    class scheduler:
        tick_interval_s = 300


def test_worker_exits_when_its_connection_is_gone(monkeypatch):
    monkeypatch.setattr(runner, "load_config", lambda: _Cfg())
    monkeypatch.setattr(runner.db, "connect", lambda url: _BrokenConn())

    def _never(*a, **kw):
        raise AssertionError("claimed a job on a dead connection")

    monkeypatch.setattr(runner, "run_once", _never)

    with pytest.raises(RuntimeError) as exc:
        runner.run_forever(worker_id="test-worker", poll_interval_s=0.01)

    assert "connection" in str(exc.value).lower()


def test_scheduler_exits_when_its_connection_is_gone(monkeypatch):
    monkeypatch.setattr(sched, "load_config", lambda: _Cfg())
    monkeypatch.setattr(sched.db, "connect", lambda url: _BrokenConn())

    def _never(*a, **kw):
        raise AssertionError("ticked on a dead connection")

    monkeypatch.setattr(sched, "tick", _never)

    with pytest.raises(RuntimeError) as exc:
        sched.run_forever(poll_interval_s=0.01)

    assert "connection" in str(exc.value).lower()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./scripts/test.sh tests/test_loop_liveness.py`
Expected: 2 FAIL — `Failed: DID NOT RAISE <class 'RuntimeError'>`, because both loops currently sleep past a broken connection.

- [ ] **Step 3: Guard the worker loop**

In `app/workers/runner.py`, inside `while not stopping["flag"]:` and **before** the `heartbeat.beat(...)` call at line 172:

```python
            # Exit rather than spin. Every error path below this line is
            # swallowed (beat by contract, run_once by the try), so a dead
            # connection would otherwise leave a live process doing nothing
            # forever -- and `restart: unless-stopped` only fires on exit,
            # while Compose does nothing about a failing healthcheck. There is
            # no reconnect anywhere in app/, so dying IS the recovery path.
            if conn.closed or conn.broken:
                raise RuntimeError(
                    "database connection lost; exiting so the supervisor restarts us"
                )
```

- [ ] **Step 4: Guard the scheduler loop**

In `app/scheduler.py`, inside `while not stopping["flag"]:` and before the `heartbeat.beat(...)` call, insert the identical block (same comment, same message).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `./scripts/test.sh tests/test_loop_liveness.py tests/test_llm_credentials.py tests/test_runner.py tests/test_scheduler.py`
Expected: all PASS. `test_llm_credentials.py::test_worker_starts_when_the_key_is_present` must stay green — its fake `_Conn` has neither attribute, so add `closed = False` / `broken = False` to that fake if it raises `AttributeError`.

- [ ] **Step 6: Full suite, then commit**

```bash
./scripts/test.sh
git add tests/test_loop_liveness.py app/workers/runner.py app/scheduler.py
git commit -m "loops: a lost connection kills the process instead of wedging it

Both run_forever loops swallow every exception, so a dead connection left a
live process spinning and doing nothing -- and restart: unless-stopped only
fires on exit, while Compose ignores a failing healthcheck. With no reconnect
anywhere in app/, exiting is the recovery path."
```

---

### Task 3: The `scheduler.tick` job type and its seven rows

**Files:**
- Create: `migrations/054_scheduler_tick.sql`, `migrations/055_scheduler_tick_seed.sql`
- Test: `tests/test_scheduler_tick.py` (created here, extended in Tasks 4-5)

**Interfaces:**
- Consumes: nothing.
- Produces: enum value `'scheduler.tick'`; seven `job` rows with `payload->>'cron'` in `{ingest.monthly, expiry-scan, failure-monitor, report.weekly, email.poll, eudamed.certregister, eudamed.sweep-due}` and `dedupe_key = 'scheduler.tick:' || cron`. Task 4's handler reads `job["payload"]["cron"]`.

**Note.** `053` is NOT free: the `document-manufacturer-binding` worktree wrote `migrations/053_document_manufacturer.sql` on 2026-08-31, so this work takes **054/055**. Separately, `047` and `048` exist neither on disk nor in `schema_migrations` — do not backfill that hole either, since a file sorting below the highest applied one trips `db._out_of_order`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_scheduler_tick.py`:

```python
"""scheduler.tick — one perpetual job per cron."""
from __future__ import annotations

CRONS = [
    "ingest.monthly",
    "expiry-scan",
    "failure-monitor",
    "report.weekly",
    "email.poll",
    "eudamed.certregister",
    "eudamed.sweep-due",
]


def test_the_seed_arms_every_cron(conn):
    """Runs 055's own SQL rather than trusting migration state.

    `job` is in conftest's _RESET_TABLES, so rows the migration inserted are
    deleted before every test. Executing the shipped file is what actually
    tests the shipped file.
    """
    import pathlib

    sql = (pathlib.Path(__file__).resolve().parents[1]
           / "migrations" / "055_scheduler_tick_seed.sql").read_text()
    conn.execute(sql)

    rows = conn.execute(
        "SELECT payload->>'cron' AS cron, dedupe_key, status, priority "
        "  FROM job WHERE type='scheduler.tick' ORDER BY 1"
    ).fetchall()

    assert sorted(r["cron"] for r in rows) == sorted(CRONS)
    for r in rows:
        assert r["dedupe_key"] == f"scheduler.tick:{r['cron']}"
        assert r["status"] == "pending"
        # interactive is for human-triggered work; sweep would let a daily scan
        # starve for hours behind a backlog.
        assert r["priority"] == "delta"


def test_the_seed_is_safe_to_run_twice(conn):
    import pathlib

    sql = (pathlib.Path(__file__).resolve().parents[1]
           / "migrations" / "055_scheduler_tick_seed.sql").read_text()
    conn.execute(sql)
    conn.execute(sql)

    n = conn.execute(
        "SELECT count(*) AS n FROM job WHERE type='scheduler.tick'").fetchone()["n"]
    assert n == len(CRONS)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./scripts/test.sh tests/test_scheduler_tick.py`
Expected: FAIL — `invalid input value for enum job_type: "scheduler.tick"`.

- [ ] **Step 3: Add the enum value**

Create `migrations/054_scheduler_tick.sql`:

```sql
-- 054_scheduler_tick.sql
-- The cron cadence becomes queue state. One tag, with the cron in the payload:
-- the cron is data, exactly as email.request carries `state`, so a future cron
-- is another row rather than another contract change.
--
-- ALTER TYPE ... ADD VALUE cannot be used in the same transaction that also
-- USES the new value, and run_migrations gives each file one transaction
-- (app/db.py:106-109). This file therefore adds the value and nothing else;
-- 055 seeds the rows. Migration 013 hit the same constraint for report.weekly.

ALTER TYPE job_type ADD VALUE 'scheduler.tick';
```

- [ ] **Step 4: Seed the seven rows**

Create `migrations/055_scheduler_tick_seed.sql`:

```sql
-- 055_scheduler_tick_seed.sql
-- Arms every cron at migrate time rather than on the next worker restart: an
-- already-running worker never re-enters main(), so bootstrap alone would leave
-- the crons non-existent until something recreated the container.
--
-- ON CONFLICT DO NOTHING against the active-only dedupe index makes this a
-- no-op for any cron already armed, so re-running it is safe. `dead` is not in
-- that index, so a dead-lettered cron is re-armed by this same statement.
--
-- run_after = now(): the ledger (scheduler_run), not run_after, decides whether
-- a period has fired. run_after is only how often a cron ASKS.

INSERT INTO job (type, payload, dedupe_key, priority, run_after)
SELECT 'scheduler.tick',
       jsonb_build_object('cron', cron),
       'scheduler.tick:' || cron,
       'delta',
       now()
  FROM (VALUES
          ('ingest.monthly'),
          ('expiry-scan'),
          ('failure-monitor'),
          ('report.weekly'),
          ('email.poll'),
          ('eudamed.certregister'),
          ('eudamed.sweep-due')
       ) AS c(cron)
ON CONFLICT (dedupe_key) WHERE status IN ('pending','running','failed')
DO NOTHING;
```

- [ ] **Step 5: Apply and run the test**

```bash
docker compose run --rm migrate
./scripts/test.sh tests/test_scheduler_tick.py
```
Expected: 2 PASS. The tests execute `054`'s SQL themselves — `job` is in conftest's `_RESET_TABLES`, so migration-inserted rows never survive to test time.

- [ ] **Step 6: Full suite, then commit**

```bash
./scripts/test.sh
git add migrations/054_scheduler_tick.sql migrations/055_scheduler_tick_seed.sql tests/test_scheduler_tick.py
git commit -m "queue: a job type for the cron cadence, and the seven rows that carry it"
```

---

### Task 4: The handler — run one cron, then self-defer

**Files:**
- Create: `app/handlers/scheduler_tick.py`
- Modify: `app/workers/runner.py:31` (add the import beside the other handler imports)
- Test: `tests/test_scheduler_tick.py`

**Interfaces:**
- Consumes: `job["payload"]["cron"]` from Task 3; `app.scheduler`'s `_tick_*` functions, unchanged.
- Produces: `handle_scheduler_tick(conn, job) -> dict` returning `{"_deferred": True, "cron": str, **tick_result}`. Registered as `HANDLERS["scheduler.tick"]`.

**Poll intervals** (spec §2) — each a fraction of its period, because the ledger decides firing and `run_after` only decides asking:

| cron | poll seconds |
|---|---|
| `ingest.monthly` | 21600 |
| `expiry-scan` | 3600 |
| `failure-monitor` | 3600 |
| `report.weekly` | 3600 |
| `email.poll` | 900 |
| `eudamed.certregister` | 21600 |
| `eudamed.sweep-due` | 3600 |

- [ ] **Step 1: Write the failing test**

Append to `tests/test_scheduler_tick.py`:

```python
import pytest

from app import queue
from app.handlers import HANDLERS
from app.handlers import scheduler_tick as tick_mod


def test_every_cron_is_dispatchable(conn):
    assert "scheduler.tick" in HANDLERS
    assert set(tick_mod.POLL_SECONDS) == set(CRONS)


def test_the_handler_runs_the_named_cron_and_defers(conn, monkeypatch):
    seen = {}
    monkeypatch.setitem(
        tick_mod.CRONS, "report.weekly",
        lambda conn, cfg, now: seen.setdefault("ran", True) or {"fired": True},
    )
    job_id = queue.enqueue(
        conn, "scheduler.tick", {"cron": "report.weekly"},
        "scheduler.tick:report.weekly-test", priority="delta",
    )
    job = conn.execute("SELECT * FROM job WHERE id=%s", (job_id,)).fetchone()

    result = tick_mod.handle_scheduler_tick(conn, job)

    assert seen["ran"] is True
    assert result["_deferred"] is True
    row = conn.execute("SELECT status, run_after FROM job WHERE id=%s", (job_id,)).fetchone()
    assert row["status"] == "pending"


def test_an_unknown_cron_fails_loudly(conn):
    job_id = queue.enqueue(
        conn, "scheduler.tick", {"cron": "not-a-cron"},
        "scheduler.tick:not-a-cron", priority="delta",
    )
    job = conn.execute("SELECT * FROM job WHERE id=%s", (job_id,)).fetchone()

    with pytest.raises(LookupError):
        tick_mod.handle_scheduler_tick(conn, job)


def test_it_dispatches_through_the_real_runner(conn):
    """Registered for real -- not just importable."""
    from app.workers import runner

    queue.enqueue(conn, "scheduler.tick", {"cron": "report.weekly"},
                  "scheduler.tick:report.weekly", priority="delta")
    conn.commit()

    assert runner.run_once(conn, "test-worker") is False  # a self-defer moved nothing

    row = conn.execute(
        "SELECT status FROM job WHERE dedupe_key='scheduler.tick:report.weekly'"
    ).fetchone()
    assert row["status"] == "pending"  # deferred, not finished


def test_one_broken_cron_does_not_stop_the_others(conn, monkeypatch):
    """Isolation is structural now: separate jobs, separate backoff.

    This is what `tick()`'s try/except used to provide inside one loop.
    """
    from app.workers import runner

    def _boom(conn, cfg, now):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(tick_mod.CRONS, "failure-monitor", _boom)
    for cron in ("failure-monitor", "report.weekly"):
        queue.enqueue(conn, "scheduler.tick", {"cron": cron},
                      f"scheduler.tick:{cron}", priority="delta")
    conn.commit()

    runner.run_once(conn, "test-worker")
    runner.run_once(conn, "test-worker")

    rows = {
        r["dedupe_key"]: r["status"]
        for r in conn.execute(
            "SELECT dedupe_key, status FROM job WHERE type='scheduler.tick'"
        ).fetchall()
    }
    # the broken one backed off; the healthy one deferred and is still armed
    assert rows["scheduler.tick:failure-monitor"] in ("failed", "dead")
    assert rows["scheduler.tick:report.weekly"] == "pending"


def test_the_ledger_still_gates_firing(conn):
    """Two polls inside one period fire once. `run_after` is a poll interval,
    not a fire time -- scheduler_run remains the authority."""
    job_id = queue.enqueue(conn, "scheduler.tick", {"cron": "report.weekly"},
                           "scheduler.tick:report.weekly", priority="delta")
    job = conn.execute("SELECT * FROM job WHERE id=%s", (job_id,)).fetchone()

    first = tick_mod.handle_scheduler_tick(conn, job)
    second = tick_mod.handle_scheduler_tick(conn, job)

    assert first["fired"] is True
    assert second["fired"] is False

    n = conn.execute(
        "SELECT count(*) AS n FROM scheduler_run WHERE name='report.weekly'"
    ).fetchone()["n"]
    assert n == 1


def test_the_old_process_and_the_new_job_do_not_double_fire(conn):
    """Tasks 3-5 ship while the Task 1 scheduler process is still running.

    Both compute the same period key and both consult the same ledger, so
    whichever gets there first fires and the other is a no-op. This is what
    makes the migration safe to deploy without a maintenance window.
    """
    import datetime as dt

    from app.config import load_config
    from app import scheduler as sched

    now = dt.datetime.now(dt.timezone.utc)
    cfg = load_config()

    from_process = sched._tick_weekly_report(conn, now=now)

    job_id = queue.enqueue(conn, "scheduler.tick", {"cron": "report.weekly"},
                           "scheduler.tick:report.weekly", priority="delta")
    job = conn.execute("SELECT * FROM job WHERE id=%s", (job_id,)).fetchone()
    from_queue = tick_mod.handle_scheduler_tick(conn, job)

    assert from_process["fired"] is True
    assert from_queue["fired"] is False

    n = conn.execute(
        "SELECT count(*) AS n FROM scheduler_run WHERE name='report.weekly'"
    ).fetchone()["n"]
    assert n == 1
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./scripts/test.sh tests/test_scheduler_tick.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.handlers.scheduler_tick'`.

- [ ] **Step 3: Write the handler**

Create `app/handlers/scheduler_tick.py`:

```python
"""scheduler.tick — the cron cadence as queue state.

One perpetual job per cron. The handler runs that cron's existing `_tick_*`
function and self-defers; the job never completes, so it never frees its
dedupe key and can never be duplicated.

`run_after` is a POLL interval, not a fire time. `scheduler_run` remains the
sole authority on whether a period has fired, so defer precision does not
matter, drift is harmless, and moving the cadence here cannot change WHEN
anything fires -- only which process asks.

Isolation is structural rather than a try/except: one cron per job means one
cron's failure dead-letters one cron and leaves the other six claimable.

Bound: `claim`'s visibility timeout is 300s, so nothing here may take longer
than that or a second worker could claim the same tick.
"""
from __future__ import annotations

import datetime as dt
import logging

from app import queue, scheduler
from app.config import load_config
from app.handlers import register

log = logging.getLogger("dentalia.handlers.scheduler_tick")

#: cron key -> the function that runs it. Same dispatch table `scheduler.tick`
#: iterated, one entry per closed-enum payload value.
CRONS = {
    "ingest.monthly": lambda conn, cfg, now: scheduler._tick_ingest_monthly(
        conn, cfg.scheduler, now),
    "expiry-scan": lambda conn, cfg, now: scheduler._tick_expiry_scan(
        conn, cfg.scheduler,
        horizon_days=max(cfg.renewal.horizon_days),
        cadence_days=cfg.renewal.request_cadence_days,
        now=now),
    "failure-monitor": lambda conn, cfg, now: scheduler._tick_failure_monitor(
        conn, cfg.scheduler, now=now),
    "report.weekly": lambda conn, cfg, now: scheduler._tick_weekly_report(
        conn, now=now),
    "email.poll": lambda conn, cfg, now: scheduler._tick_email_poll(
        conn, cfg.scheduler, now=now),
    "eudamed.certregister": lambda conn, cfg, now: scheduler._tick_eudamed_certregister(
        conn, cfg.scheduler, now=now),
    "eudamed.sweep-due": lambda conn, cfg, now: scheduler._tick_eudamed_sweep_due(
        conn, cfg.scheduler, now=now),
}

#: How often each cron ASKS. A fraction of its period in every case -- the
#: ledger, not this number, decides whether the period actually fires.
POLL_SECONDS = {
    "ingest.monthly": 21600,
    "expiry-scan": 3600,
    "failure-monitor": 3600,
    "report.weekly": 3600,
    "email.poll": 900,
    "eudamed.certregister": 21600,
    "eudamed.sweep-due": 3600,
}


def handle_scheduler_tick(conn, job: dict) -> dict:
    cron = job["payload"].get("cron")
    if cron not in CRONS:
        raise LookupError(
            f"unknown cron {cron!r} on scheduler.tick job {job['id']}; "
            f"known: {sorted(CRONS)}"
        )
    now = dt.datetime.now(dt.timezone.utc)
    result = CRONS[cron](conn, load_config(), now)
    queue.defer(conn, job["id"], POLL_SECONDS[cron])
    log.debug("scheduler.tick %s -> %s", cron, result)
    return {"_deferred": True, "cron": cron, **(result or {})}


register("scheduler.tick", handle_scheduler_tick)
```

- [ ] **Step 4: Register it with the worker**

In `app/workers/runner.py`, after line 31 (`import app.handlers.eudamed`):

```python
import app.handlers.scheduler_tick  # noqa: F401 - scheduler.tick (cron cadence)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `./scripts/test.sh tests/test_scheduler_tick.py tests/test_scheduler.py tests/test_runner.py`
Expected: all PASS.

- [ ] **Step 6: Full suite, then commit**

```bash
./scripts/test.sh
git add app/handlers/scheduler_tick.py app/workers/runner.py tests/test_scheduler_tick.py
git commit -m "scheduler: each cron becomes a job that reschedules itself"
```

---

### Task 5: Re-arm on restart, and give the worker the config

**Files:**
- Modify: `app/workers/runner.py` (`main()`), `docker-compose.yml` (worker `environment`)
- Test: `tests/test_scheduler_tick.py`, `tests/test_compose_config.py`

**Interfaces:**
- Consumes: Task 4's `CRONS` and `POLL_SECONDS`.
- Produces: `runner.arm_crons(conn) -> int` returning how many rows it created (0 when all are live).

**Why the worker needs the env block.** Once crons run on the worker, `SCHEDULER_*` is read in the worker's process. The worker service currently declares **zero** of the 16, so every cron would run on hardcoded defaults with no way to change them.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_scheduler_tick.py`:

```python
def test_arming_is_a_noop_when_every_cron_is_live(conn):
    from app.workers import runner

    runner.arm_crons(conn)
    before = conn.execute(
        "SELECT count(*) AS n FROM job WHERE type='scheduler.tick'").fetchone()["n"]

    assert runner.arm_crons(conn) == 0

    after = conn.execute(
        "SELECT count(*) AS n FROM job WHERE type='scheduler.tick'").fetchone()["n"]
    assert after == before


def test_a_dead_cron_is_rearmed(conn):
    from app.workers import runner

    runner.arm_crons(conn)
    conn.execute(
        "UPDATE job SET status='dead' "
        " WHERE type='scheduler.tick' AND dedupe_key='scheduler.tick:report.weekly'")

    # `dead` is terminal and is NOT in the active-only dedupe index, so the key
    # is free and the same enqueue re-arms the cron.
    assert runner.arm_crons(conn) == 1

    row = conn.execute(
        "SELECT count(*) AS n FROM job "
        " WHERE type='scheduler.tick' AND status='pending' "
        "   AND dedupe_key='scheduler.tick:report.weekly'").fetchone()
    assert row["n"] == 1
```

And in `tests/test_compose_config.py`, extend the parametrize list:

```python
@pytest.mark.parametrize("service", ["scheduler", "web", "worker"])
```

- [ ] **Step 2: Run to verify both fail**

Run: `./scripts/test.sh tests/test_scheduler_tick.py tests/test_compose_config.py`
Expected: `AttributeError: module 'app.workers.runner' has no attribute 'arm_crons'`, plus one new compose failure naming 16 missing keys on `worker`.

- [ ] **Step 3: Add `arm_crons`**

In `app/workers/runner.py`, above `main()`:

```python
def arm_crons(conn) -> int:
    """Ensure one live `scheduler.tick` job per cron. Returns rows created.

    A no-op when every cron is live: `enqueue` returns None against the
    active-only dedupe index. `dead` is terminal and outside that index, so a
    cron that dead-lettered is re-armed here -- which is the whole point. The
    054 seed covers first deploy; this covers every restart after one broke.
    """
    from app.handlers.scheduler_tick import POLL_SECONDS

    created = 0
    for cron in sorted(POLL_SECONDS):
        if queue.enqueue(
            conn, "scheduler.tick", {"cron": cron},
            dedupe_key=f"scheduler.tick:{cron}", priority="delta",
        ) is not None:
            created += 1
    if created:
        log.info("armed %d scheduler.tick cron(s)", created)
    return created
```

Call it in `main()`, after `playbooks.set_source(...)` and before `run_forever()`:

```python
    with db.connect(autocommit=True) as conn:
        arm_crons(conn)
```

- [ ] **Step 4: Give the worker all 16 keys**

In `docker-compose.yml`, inside the `worker` service's `environment:` block, add the identical 16-line `SCHEDULER_*` block from Task 1 Step 4 (without `DATABASE_URL`, which the worker already restates), preceded by:

```yaml
      # The worker RUNS the crons now (scheduler.tick), so every SCHEDULER_*
      # key is read in this process. Compose injects no .env, so a key missing
      # here is a cron running on a hardcoded default forever.
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `./scripts/test.sh tests/test_scheduler_tick.py tests/test_compose_config.py tests/test_runner.py`
Expected: all PASS.

- [ ] **Step 6: Full suite, then commit**

```bash
./scripts/test.sh
git add app/workers/runner.py docker-compose.yml tests/test_scheduler_tick.py tests/test_compose_config.py
git commit -m "scheduler: workers re-arm the crons on start, and can finally configure them"
```

---

### Task 6: The contract catches up

**Files:**
- Modify: `docs/dentalia-pipeline-contract-prd-v3.md` (§0 dedupe table, §8 SCHEDULER row), `docs/dentalia-job-type-handbook.md` (§0 overview table + new section), `docs/dentalia-schema-sketch.md` (§7 access matrix), `docs/specs/scheduler.md` (§2, §5)

**Interfaces:**
- Consumes: the tag and payload shape from Tasks 3-4.
- Produces: no code. Invariant 7 requires the contract to name every job type.

- [ ] **Step 1: PRD §0 dedupe table**

Add a row: `scheduler.tick` → `scheduler.tick:{cron}`, with the note "one perpetual job per cron; the key is held for the job's whole life because it never completes."

- [ ] **Step 2: PRD §8 SCHEDULER row**

Leave the *Emits* column exactly as it is. Change the note's opening from "Owns crons only, zero business logic" to "Owns crons only, zero business logic; the cadence is carried by seven perpetual `scheduler.tick` jobs rather than a separate process (2026-08-31)." Do not touch the `eudamed` sentences — the no-unattended-sweep ruling is unchanged.

- [ ] **Step 3: Handbook §0 + new section**

Add the overview row, then a `## scheduler.tick` section stating: payload `{cron}`; reads `scheduler_run`; writes `scheduler_run`; emits whatever the named cron emits; never completes (self-defers via `queue.defer`); the visibility-timeout bound of 300s.

- [ ] **Step 4: Schema sketch §7**

Add a row: `scheduler.tick` writes `scheduler_run` and `job`.

- [ ] **Step 5: `docs/specs/scheduler.md`**

Amend §2 (module layout — the tick is a handler, not a process) and §5 (error taxonomy — a raising cron dead-letters that cron alone). Leave §1, §3 and §6 untouched; GAP A-F resolutions still stand.

- [ ] **Step 6: Commit**

```bash
git add docs/dentalia-pipeline-contract-prd-v3.md docs/dentalia-job-type-handbook.md docs/dentalia-schema-sketch.md docs/specs/scheduler.md
git commit -m "contract: scheduler.tick joins the closed job-type enum"
```

---

### Task 7: Delete the process

**Do not start until Tasks 3-6 have been verified running live for at least one full day**, so every daily cron has fired from the queue at least once. Confirm with:

```bash
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT name, max(ran_at) FROM scheduler_run GROUP BY 1 ORDER BY 2 DESC;"'
```

**Files:**
- Modify: `app/scheduler.py` (drop `run_forever`, `main`, the `signal`/`time`/`os` imports they alone use), `docker-compose.yml` (drop the `scheduler` service), `web/app.py:1703` (drop the chip), `tests/test_loop_liveness.py` (drop the scheduler half)
- Modify: `docs/runbook.md`, `docs/architecture.md`, `docs/code-map.md`, `docs/specs/scheduler.md`

**Interfaces:**
- Consumes: a verified-live Task 4 handler.
- Produces: `app/scheduler.py` as a pure library. **Nothing in `app/` or `web/` imports it** — verified 2026-08-31, only `tests/` do, and `app/periods.py` / `web/scheduler_view.py` mention it in docstrings only. So no import breaks.

- [ ] **Step 1: Write the failing test**

In `tests/test_scheduler.py`:

```python
def test_the_scheduler_module_is_a_library_not_an_entrypoint():
    import app.scheduler as sched

    assert not hasattr(sched, "run_forever"), "the cadence lives on the queue now"
    assert not hasattr(sched, "main")
    # The tick functions and the ledger stay -- they are what the handler calls.
    assert hasattr(sched, "tick") and hasattr(sched, "already_ran")
```

- [ ] **Step 2: Run to verify it fails**

Run: `./scripts/test.sh tests/test_scheduler.py::test_the_scheduler_module_is_a_library_not_an_entrypoint`
Expected: FAIL on the first assert.

- [ ] **Step 3: Delete the loop**

Remove `run_forever()` and `main()` from `app/scheduler.py`, plus the `if __name__ == "__main__":` block and any now-unused imports. Replace the module docstring's "Not a worker: does not go through `app.queue.claim`" paragraph with a line naming `app/handlers/scheduler_tick.py` as the caller.

- [ ] **Step 4: Delete the service and the chip**

Remove the entire `scheduler:` service from `docker-compose.yml` (lines ~195-224), which takes the `full` profile with it. In `web/app.py`, delete line 1703:

```python
    ("scheduler", {"beats": True, "stale_after_s": 900}),
```

- [ ] **Step 5: Drop the scheduler half of the liveness test**

Remove `test_scheduler_exits_when_its_connection_is_gone` from `tests/test_loop_liveness.py`. The worker test stays and now covers the crons too.

- [ ] **Step 6: Run the tests**

Run: `./scripts/test.sh tests/test_scheduler.py tests/test_loop_liveness.py tests/test_web.py tests/test_scheduler_web.py`
Expected: all PASS.

- [ ] **Step 7: Update four docs**

`docs/runbook.md` (drop the scheduler service from the start-the-stack section), `docs/architecture.md:33` (the row becomes a job type, not a service), `docs/code-map.md` (`app/scheduler.py` is a library; add `app/handlers/scheduler_tick.py`), `docs/specs/scheduler.md` §2. Also correct **PHASES.md:125** — its claim that the W34 report "is still lost" is stale; `report:2026-W34` id 35532 ran to `done` on 2026-08-21 07:28.

- [ ] **Step 8: Full suite, then commit**

```bash
./scripts/test.sh
git add -A app/scheduler.py docker-compose.yml web/app.py tests/ docs/ PHASES.md
git commit -m "scheduler: the process is gone, the crons stayed"
```

---

### Task 8: `/scheduler` tells the truth about all seven crons

**Files:**
- Modify: `web/scheduler_view.py` (`_cron_specs`, the run-now route), `web/templates/scheduler.html`
- Test: `tests/test_scheduler_web.py`

**Interfaces:**
- Consumes: Task 3's dedupe-key convention `scheduler.tick:{cron}`.
- Produces: a page row per cron carrying `live_job_id: int | None` and `next_poll: datetime | None`.

**Two defects fixed here.** `_cron_specs` lists **5 crons while the tick runs 7** — both EUDAMED crons, added 2026-08-27, have no UI representation at all. And `expiry-scan` / `failure-monitor` carry `job_type: None` and are rendered unrunnable because, in the module's own words, "there is no job type for them and the enum is closed". Task 3 created one. (`ingest.monthly` stays unrunnable for an unrelated, deliberate reason: `web` does not mount the watch dir.)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_scheduler_web.py`:

```python
def test_the_panel_lists_every_cron_the_tick_runs():
    from app.handlers.scheduler_tick import POLL_SECONDS
    from web.scheduler_view import _cron_specs
    from app.config import load_config

    keys = {s["key"] for s in _cron_specs(load_config().scheduler)}
    assert keys == set(POLL_SECONDS)


def test_the_panel_shows_whether_each_cron_is_armed(client, conn):
    from app.workers import runner

    runner.arm_crons(conn)
    conn.commit()

    body = client.get("/scheduler").text
    assert "eudamed.sweep-due" in body
    assert "eudamed.certregister" in body
```

- [ ] **Step 2: Run to verify it fails**

Run: `./scripts/test.sh tests/test_scheduler_web.py`
Expected: FAIL — the key set is 5, not 7.

- [ ] **Step 3: Add the two missing crons to `_cron_specs`**

Append two entries after the `email.poll` entry, matching the existing dict shape:

```python
        {
            "key": "eudamed.certregister",
            "ledger": "eudamed.certregister",
            "label": "EUDAMED certificate register pull",
            "cadence": "monthly",
            "cadence_hours": 24 * sched.eudamed_certregister_interval_days,
            # Bucket key, not a calendar month: see _period_key_days.
            "period": lambda now: _period_key_days(
                now, sched.eudamed_certregister_interval_days),
            "job_type": "scheduler.tick",
        },
        {
            # RULING 45: no ledger by design -- it emits nothing and its due_at
            # write is an idempotent UPSERT, so it runs on every poll.
            "key": "eudamed.sweep-due",
            "ledger": None,
            "label": "EUDAMED device sweep — mark due",
            "cadence": "quarterly",
            "cadence_hours": 24 * sched.eudamed_sweep_interval_days,
            "period": None,
            "job_type": "scheduler.tick",
        },
```

Import `_period_key_days` from `app.scheduler` is **not** allowed (`web/` may not import `app.scheduler`, which pulls `app.handlers`). Move `_period_key_days` to `app/periods.py` first and import it from there, exactly as the other period keys already are.

- [ ] **Step 4: Give the other crons a job type**

Change `"job_type": None` to `"job_type": "scheduler.tick"` for `expiry-scan` and `failure-monitor`. Leave `ingest.monthly` at `None` and keep its existing comment.

- [ ] **Step 5: Show whether each cron is armed**

In the view function, for each spec look up its live job:

```python
    live = {
        r["dedupe_key"]: r
        for r in conn.execute(
            "SELECT dedupe_key, id, run_after, status FROM job "
            " WHERE type='scheduler.tick' AND status IN ('pending','running','failed')"
        ).fetchall()
    }
```

and add `row["live_job_id"]` / `row["next_poll"]` from `live.get(f"scheduler.tick:{spec['key']}")`. In `web/templates/scheduler.html`, add a column rendering `armed` / `next poll {{ row.next_poll }}` when present, and a **Re-arm** button posting to the existing run-now route when absent.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `./scripts/test.sh tests/test_scheduler_web.py tests/test_web.py tests/test_periods.py`
Expected: all PASS.

- [ ] **Step 7: Full suite, then commit**

```bash
./scripts/test.sh
git add web/scheduler_view.py web/templates/scheduler.html app/periods.py tests/
git commit -m "scheduler panel: all seven crons, and whether each one is armed"
```

---

## Out of scope

- **Missed-period catch-up.** Period keys stay `now`-derived, so a period the stack was down for is skipped. Task 1 will demonstrate this: `report.weekly` last recorded `2026-W34`, today is `2026-W36`, and W35 is gone for good. Adding replay needs the ledger to drive the key plus a per-cron ruling on whether a stale report is worth firing.
- **The `047`/`048` migration hole.** Pre-existing. Do not backfill.
- **`[scheduler-report-imports]`** (`tasks/followups.md:208`) dissolves when Task 7 lands — close it there rather than doing it separately.
