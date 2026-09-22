# Test infrastructure

What the suite runs on, what each piece bought, and which pieces are worth
copying into another project. Written 2026-08-27, after the six commits listed
in §2.

The short version: nothing here made the tests themselves faster. It made the
fast path the only reachable path, and it let several worktrees run at once
without fighting. The one genuine speedup is on single-test runs, which no
longer pay for parallelism they cannot use.

---

## 1. Measured numbers

All three measured once each on 2026-08-27 through `./scripts/test.sh`, in the
`test` container.

| Run | Wall | pytest self-reported | Workers |
|---|---|---|---|
| One test id (`tests/test_queue.py::test_enqueue_inserts_pending_job`) | **2.69s** | 1.20s | `-n 0` |
| One file (`tests/test_queue.py`, 36 tests) | **5.66s** | 4.21s | `-n 2 --dist loadfile` |
| Full suite (2310 passed, 2 skipped) | **125.5s** | 123.89s | `-n 4 --dist loadfile` |

The gap between wall and self-reported is constant at ~1.45s in all three: that
is `docker compose exec` plus interpreter start plus the semaphore acquisition.
It is a fixed toll, which is why it dominates the single-id case (54% of that
run) and disappears into the noise on the suite (1%).

### Machine load

**Not idle.** Recorded `/proc/loadavg` immediately before each run on a 12-core
machine:

| Point | 1-min load |
|---|---|
| Before the id run | 4.67 |
| Before the file run | 3.45 |
| Before the suite | 3.26 |
| After the suite | 7.55 |

Three other development sessions were active throughout (~18% CPU on the parent
plus two extension hosts and a Playwright MCP server). No other pytest run was
in flight — all four semaphore slots were free at the start. A genuinely idle
measurement was not available; treat these as "typical working conditions on
this machine", which is the number that actually matters day to day.

### Against the recorded baselines

| Baseline | This run | Delta |
|---|---|---|
| ~490s single-process | 123.89s | 4.0x faster |
| 154s at `-n 4 --dist loadfile` (2026-08-26) | 123.89s | **19.6% faster** |

19.6% is inside the 20% band, so no divergence to explain — but the direction is
worth a note, because the comparison is not clean and a future session should not
read 123.89s as a like-for-like improvement:

- The 154s baseline was measured **host-side**, in the host venv on Python 3.11.
  This run is **in-container** on Python 3.12, against the container's own
  filesystem. Different interpreter, different I/O path.
- The suite grew. The baseline was 2301 tests; this run collected 2312. Other
  sessions added tests in between.
- Load differed. The baseline was taken on "a loaded machine" without a recorded
  figure; this run started at 3.26.

Any of the three could account for 20%. The honest claim is that in-container at
`-n 4 --dist loadfile` lands in the same 120-190s band as the host baseline, and
that both are ~4x better than single-process.

---

## 2. What each change bought

Three different kinds of change, and it is worth keeping them apart. Only one
row made anything faster.

### Made tests faster

| Date | Bought |
|---|---|
| 2026-08-27 | `-n 0` on a single test id: no xdist worker startup for a run that cannot use it. 2.69s, of which 1.2s is the test. |

### Made the fast path the default

| Date | Bought |
|---|---|
| 2026-08-27 | `./scripts/test.sh` picks `-n`/`--dist` from the argument shape, so the correct flags stop depending on anyone remembering them. |
| 2026-08-27 | CLAUDE.md points at the wrapper instead of at a flag string to hand-type. |
| 2026-08-27 | pyproject and runbook stopped claiming `--dist loadfile` was a correctness requirement, so the next session does not re-derive a rule that was already disproven. |

### Enforced it

| Date | Bought |
|---|---|
| 2026-08-27 | PreToolUse(Bash) hook exits 2 on any pytest that did not come through the wrapper — including `docker compose exec … pytest`, which reaches the right container but skips the semaphore. |

### Made concurrency possible at all

| Date | Bought |
|---|---|
| 2026-08-27 | Project name derived from directory, no `container_name`, no published Postgres port: two worktrees can run stacks simultaneously. Plus `cpus`/`mem_limit` so one run cannot starve the others. |
| 2026-08-27 | flock semaphore, 4 slots in `/tmp/pytest-slots`, shared across every checkout on the machine. |

### Correctness, not speed

| Date | Bought |
|---|---|
| 2026-08-27 | `--strict-markers` turns a typo'd mark from a silent no-op into a collection error; `xfail_strict` turns an xfail that starts passing into a failure. Neither is about time. |

---

## 3. Why `loadfile`, and the order-independence caveat

Measured back-to-back on one loaded machine, 2026-08-26, all 2301 green:

| Scheduler | Wall |
|---|---|
| `--dist loadfile` | **154s** |
| `--dist load` | 191s |
| `--dist worksteal` | 191s |
| single-process | ~490s |

`worksteal` was tried because xdist's own docs recommend it where test durations
differ a lot, which this suite's do. It did not win. That is written down here so
nobody spends the afternoon rediscovering it.

**The mechanism is fixture warmth.** `loadfile` keeps every test in a file on one
worker. This suite's fixtures are expensive and file-scoped in practice: each
xdist worker creates its own throwaway Postgres database (conftest folds
`PYTEST_XDIST_WORKER` into the name) and applies every migration to it once per
session, and the file-local fixtures then seed against that. Keeping a file's
tests together means one worker pays that setup and reuses it across the whole
file. `load` and `worksteal` scatter a file's tests across all four workers, so
each of them pays to warm up for a handful of tests and then moves on.

The corollary: `loadfile`'s advantage scales with how expensive per-worker setup
is relative to per-test work. In a suite of pure unit tests with no fixtures, it
would buy nothing and `worksteal` would win on balancing.

### The caveat

**`loadfile` is a wall-clock choice, not a correctness one.** `tests/test_web.py`
is *not* order-dependent. Verified four ways: reverse order, two shuffled seeds
(1337, 98765), `--dist load`, and `--dist worksteal` — 309/309 green each time,
and the full suite green under `--dist load`.

An older version of CLAUDE.md, pyproject and the runbook all claimed the
opposite: that `test_web.py` kept deliberate cross-test row-count state and that
`load` would produce a wall of failures. That was true once and became stale; it
was corrected on 2026-08-26 and 2026-08-27.

**What holds it up, and what would invalidate it.** Order-independence here is a
property of the current tree, not a law. Two things hold it:

1. Every seeding fixture takes `conn`, whose teardown runs conftest's
   `_RESET_SQL`. Writers clean up after themselves. The ~34 tests in
   `test_web.py` that skip `conn` never write, so they cannot leak.
2. `_RESET_TABLES` names every table anything writes. Six FK-less leaves
   (`audit_log`, `batch_ref`, `document_text`, `extraction_attempt`,
   `extraction_cost`, `robots_cache`) were missing until 2026-09-04 and the
   guard test below could not see them; they are named now, as seeds too.
   `schema_migrations` is the one FK-less table that must stay out.

**Both halves have failed before.** On 2026-08-26, `data_anomaly` and
`service_heartbeat` were each missing from that list and each produced real
order-dependent failures. The guard is
`tests/test_queue.py::test_reset_clears_every_table_the_old_cascade_reached`,
which re-derives the closure from the live schema — but it only reaches tables
linked by FK to its seed set, so a **new leaf table needs a `seeds` entry too**
or it slips past the guard and starts leaking rows into later tests.

So: a new fixture that seeds without `conn`, or a new table that nothing seeds,
invalidates the caveat. Re-verify with a shuffled run before assuming it still
holds.

---

## 4. Why coverage / import-graph selection was rejected

The obvious next move after a wrapper is test-impact analysis: run only the tests
affected by the diff (`pytest-testmon`, or a coverage-derived import graph).
Rejected here, deliberately.

**Import-graph selection reports false greens on this repo**, because two classes
of file are read at *runtime* and are invisible to any static import analysis:

- `migrations/*.sql` — applied by `app/db.py` at session start. A migration that
  adds a column, changes a constraint, or renames a table changes the behaviour
  of tests in files that import nothing new.
- `playbooks/*.json` — per-manufacturer identity, doc sources and T0 parse
  templates, read by `app/playbooks.py` and `app/extract/t0_layout.py`.

Neither is a Python import, so neither appears in a coverage map or a module
dependency graph. A selector fed a diff touching only `migrations/041_*.sql`
would conclude that no test is affected and report green, having run nothing that
exercises the change.

**What replaced it** is a path-based selection rule in CLAUDE.md, hand-maintained
and deliberately blunt: a short list of files that trigger the full suite
(`tests/conftest.py`, `migrations/`, `playbooks/`, `app/config.py`, `app/db.py`,
`app/queue.py`, `app/urls.py`, `app/playbooks.py`), and otherwise
`tests/test_<module>*.py` for each module touched, plus `tests/test_web.py` if
anything under `web/` changed. It is less precise than a real impact analysis and
it is correct in the cases where the analysis silently is not.

Markers (`unit`/`integration`/`slow`) were also considered and rejected: all 2312
tests hit a real Postgres, so a `unit` marker would be a lie, and a marker
taxonomy that nobody applies consistently is worse than none.

---

## 5. Portable vs project-specific

If you are copying this into another Python + Docker + worktrees project:

### Transfers unchanged

| Piece | Note |
|---|---|
| **flock semaphore** (`scripts/test.sh`) | Pure bash, fixed `/tmp` path, no project knowledge. The one genuinely universal piece: any machine where N checkouts each run a parallel test suite has this problem. Change `SLOTS`. |
| **PreToolUse hook** (`.claude/hooks/block-bare-pytest.sh`) | Command-position detection, env-assignment and wrapper stripping, `--collect-only` exemption. Change `MESSAGE` and the wrapper path. |
| **Argument-shape dispatch** (`::` → id, path → paths, nothing → suite) | The idea that `-n` should depend on what you asked for, not on a global default, is not Dentalia-specific. |
| **Worktree → container path mapping** | Resolving the main checkout via `git rev-parse --git-common-dir` and passing `docker compose exec -w` so a worktree runs *its own* tests. Any repo using worktrees plus a bind-mounted container needs exactly this. |
| **`xfail_strict = true`, `--strict-markers`** | Should be on in every pytest project. There is no argument against them. |
| **Dropping `container_name:`** | A fixed container name is daemon-global and collides across projects, always, everywhere. |
| **Omitting `name:` from compose** | Directory-derived project naming is the correct default for any repo used through worktrees. |

### Needs adapting

| Piece | What to reconsider |
|---|---|
| **`--dist loadfile`** | Right here because per-worker DB setup is expensive (§3). Re-measure `loadfile` vs `load` vs `worksteal` on your own suite. If your fixtures are cheap, `worksteal` probably wins. |
| **`-n 4` / `-n 2` / `-n 0`** | Tuned to 12 cores shared by several sessions. Scale to your core count and how many things share the machine. |
| **`cpus` / `mem_limit`** (4.0/2g postgres, 5.0/4g test) | The memory figures are sized for PyMuPDF page rasterisation, which is this suite's hungriest operation. Yours will differ. |
| **Ports in an overlay, not the base** | The *reason* is general (Compose merges `ports` additively and cannot subtract, so the base must be the restrictive one). Which ports and which services are yours. |
| **Path-based selection rule** | The specific file list is Dentalia's. The *method* — enumerate what fans out widely, full-suite on those, targeted otherwise — transfers. |

### Dentalia-only

| Piece | Why |
|---|---|
| **The rejection of import-graph selection** | Rests entirely on `migrations/*.sql` and `playbooks/*.json` being read at runtime (§4). A project without runtime-read non-Python assets should just use `testmon` and get a much better result than a hand-maintained path list. |
| **Per-run database + advisory lock in conftest** | Solves a specific collision: a cluster-global ROLE created by `migrations/007`/`008` while each run gets its own DATABASE. Reusable only if your migrations also touch cluster-global objects. |
| **`_RESET_TABLES` / `_RESET_SQL`** | `DELETE` beats `TRUNCATE` here because the tables hold a handful of rows per test and TRUNCATE's relfilenode churn dominates on a bind-mounted Postgres (350ms/test → 7ms/test). Inverts on a suite with large tables. |
| **`DENTALIA_TEST_API_URL`** | Exists so `test_web.py` can assert Invariant 1 structurally by connecting as the write-less `dentalia_api` role. |

---

## 6. Operational notes

### Host database access

The base compose file publishes **no** host port for Postgres. The suite does not
need one — `scripts/test.sh` execs inside the stack, where Postgres is
`postgres:5432` on the compose network — and a published port is a host-global
claim that stops a second worktree from starting.

For `psql`, a GUI client, or any host-side script, add to `.env`:

```
COMPOSE_FILE=docker-compose.yml:docker-compose.dev.yml
```

then `docker compose up -d`. The overlay binds `127.0.0.1:5432` only. Just one
checkout can hold 5432; give a second one a different `POSTGRES_PORT`.

### Manual image rebuild

`scripts/test.sh` never builds. The repo is bind-mounted at `/app`, so the
running container already sees uncommitted code. Rebuild by hand **only** when
the Dockerfile or the dependency set changes:

```bash
docker compose --profile test up -d --build test
```

This is not theoretical. On 2026-08-27 the `test` image was 7 days old and
predated `pytest-xdist` entering the `dev` extras, so every `-n` flag failed with
`unrecognized arguments: -n --dist loadfile`. If the wrapper suddenly rejects its
own flags, the image is stale.

### Container names changed

`container_name:` was removed from all six services that had one. Containers are
now `<project>-<service>-<n>`:

| Was | Is |
|---|---|
| `dentalia-postgres` | `dentalia-postgres-1` |
| `dentalia-web` | `dentalia-web-1` |
| `dentalia-caddy` | `dentalia-caddy-1` |

`docker exec dentalia-postgres …` now fails. Use `docker compose exec postgres …`,
which never depended on the name and works in every worktree. Image names
(`dentalia-app:latest`, `dentalia-test:latest`) are unchanged.

### Running from a worktree

There is one stack, owned by the main checkout, and one bind mount: the main
checkout at `/app`. A worktree under `.claude/worktrees/foo` therefore appears
inside the container at `/app/.claude/worktrees/foo`.

`scripts/test.sh` handles both halves of that itself:

- **Compose commands run from the main checkout.** Compose derives its project
  name from the working directory, so running it from a worktree would target
  project `foo`, which owns no containers — the stack check would report "not
  answering" even with everything healthy. The script resolves the main checkout
  with `git rev-parse --path-format=absolute --git-common-dir` and `cd`s there
  before talking to Compose.
- **pytest runs with `-w $CONTAINER_DIR`.** Without it, `exec` lands in the
  image's `WORKDIR` (`/app`) and a worktree invocation would run the **main
  checkout's** tests while reporting them as yours — a green that says nothing
  about the tree you are working in. The `[test.sh]` banner prints the resolved
  container directory on every run, so you can see which tree is being tested.

From the main checkout `CONTAINER_DIR` is `/app` and the behaviour is unchanged.
A checkout outside the main tree is refused rather than silently mis-run.

### The semaphore: 4 slots against 12 cores

`/tmp/pytest-slots` holds four flock'd files. Every invocation of
`scripts/test.sh`, in every checkout, takes one before running and releases it on
exit.

The arithmetic: the machine has 12 cores. A full-suite run takes `-n 4`. Four
concurrent full runs would be 16 pytest workers plus four parent processes plus
four Postgres containers, on 12 cores — which is how this machine thrashes.
Four slots is the point where a worst-case all-suite-runs situation stays roughly
at core count, and the common case (mixed single-test and single-file runs, which
take `-n 0` and `-n 2`) sits well under it.

The slots are deliberately **over**-subscribed rather than under: capping at 3
would idle the machine whenever the runs are small. Worst case is mild
oversubscription; the alternative is queuing behind runs that are using one core.

Properties worth knowing:

- The path is fixed and outside any checkout, so slots are shared across
  worktrees by construction. There is nothing to configure.
- The lock is an open file descriptor, so the kernel releases it when the process
  exits — including on `kill`. A crashed or interrupted run never leaks a slot.
- When all four are busy the wrapper prints `All 4 test slots busy` once and polls
  every 2s. Verified with five concurrent invocations: slots 1-4 taken, the fifth
  waited and picked up a released slot, all five green.
- To see what is held: `for i in 0 1 2 3; do flock -n /tmp/pytest-slots/slot.$i
  true && echo "slot.$i free" || echo "slot.$i HELD"; done`

---

## Related

- `CLAUDE.md` — the wrapper instruction and the path-based selection rule.
- `docs/runbook.md` — how to start the stack and run the suite operationally.
- `tests/conftest.py` — per-run database, advisory lock, `_RESET_TABLES`; the
  comments there carry the measurements behind each choice.
