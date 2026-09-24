# Deploying to a client server

**Status:** live, written 2026-09-14. Owns **first install on a machine that is
not a dev checkout**: what to collect beforehand, the bring-up order, every
combination of where the catalogue and the manufacturer master come from, how
the first documents get onto the server, and what to prove before handing over.

[runbook.md](../runbook.md) owns local development and day-to-day operation.
Where the two touch, this file names the runbook section instead of restating
it. If they disagree, the runbook wins on anything about a dev checkout and
this file wins on anything about a client server.

**Posture taken 2026-09-14 (Denis):** we install, with shell access to the
server. Postgres data and the document archive are bind-mounted to host paths.
Item and manufacturer masters come from files; the BC-direct route is usable
from the CLI with the warnings in § 6.4 understood.

---

## 0. Collect these before you touch the server

| Thing | Why it blocks | Where it goes |
|---|---|---|
| A read-only deploy key on the GitHub repository | The server installs from a git clone, and `scripts/deploy.sh` reads its git metadata (§ 1.2) | GitHub repo settings, Deploy keys |
| Two host directories: Postgres data, archive | Compose refuses to start without the first; the second decides whether `down -v` can destroy the archive | `PGDATA_HOST`, the prod overlay in § 4 |
| A host directory for the corpus and the BC exports | The worker mounts it read-only; `backfill.scan` and `/ingest` both read it | `IMPORTS_HOST` |
| `ANTHROPIC_API_KEY` | Compose fails fast without it; every in-scope document escalates past T0 | `.env` |
| A web username + bcrypt hash | Caddy is the sole ingress and refuses to start without the hash | `.env` |
| Search key (`BRAVE_API_KEY`) | Only needed once discovery is unheld (§ 6.7). Inert while `DISCOVER_HOLD` is on | `.env` |
| Which source for **items**: file or BC OData | Decides § 6.4 | — |
| Which source for **manufacturers**: file or BC | Decides § 6.2. Today only the file route is reachable | — |
| How the corpus reaches the server | Decides § 6.6. Nothing in the tree speaks FTP or SFTP | — |
| Who reaches the UI, from where | Caddy binds loopback only out of the box (§ 7) | — |
| A webhook URL for failure alerts | The only mechanism that tells anyone a job dead-lettered without someone opening `/dead`. Compose passes it to `worker` and `web` | `ALERTS_WEBHOOK_URL` |

Two documents answer "what is this system" for the client: [what it does, EN](../2026-08-24-what-the-system-does-en.md) and [SL](../2026-08-24-what-the-system-does-sl.md). This file is for whoever runs the install.

---

## 1. Prerequisites on the server

- Linux host with Docker and Compose v2, git, and `python3`. Nothing of the
  system runs on the host: no Postgres, no Node, no app. `python3` is there
  only because `scripts/deploy.sh` hashes the checkout with it (standard
  library, any 3.x the tree runs on).
- `docker-buildx`, which Compose v2 builds through. Check with
  `dpkg-query -W docker-buildx`; on Ubuntu, `sudo apt install docker-buildx`
  in an interactive session if absent.
- Outbound HTTPS to `api.anthropic.com`, and to the search API once discovery is
  unheld. Nothing needs **inbound** internet.
- Disk: the archive grows with the corpus. The dev registry is 713 MB of archive
  against a 746 MB corpus ([state 2026-09-04](../state/2026-09-04.md)); budget
  for both, since the corpus stays on disk as the backfill's source.
- If BC OData is in play, the worker container must reach the BC host.
  Measured 2026-09-24 on the Dentalia server: BC answers at
  `mail.dentalia.si:7048` (`89.212.58.227`, IPv4 only, no AAAA), **plain HTTP**,
  reachable from the server and filtered from elsewhere, so it is allowlisted to
  the server's address. A container leaves through the host's NAT with the same
  source IPv4, so the allowlist admits it too. Not yet exercised from inside a
  container. **Plain HTTP is accepted** (Denis, 2026-09-24,
  [decisions](../decisions.md)): the allowlist is the protection relied on, and
  the credentials cross between the two sites unencrypted.

### 1.1 Directories

Follow the host's own convention rather than inventing one. On the Dentalia
server the application directories under `/srv` belong to the `deploy` group
(their own app lives in `/srv/dentalia`, `deploy:deploy`), so ours does too:

```bash
sudo usermod -aG deploy "$USER"              # then log out and back in
sudo mkdir -p /srv/compliance/{pgdata,archive,imports}
sudo chown -R "$USER":deploy /srv/compliance
sudo chmod -R g+rwX /srv/compliance
sudo find /srv/compliance -type d -exec chmod g+s {} +
```

The setgid bit makes everything created later inherit the group, so whoever
takes this over gets access by being added to `deploy`, with no `chown` and no
downtime. Owning it as one person instead means every host-side action,
including reading a backup, needs `sudo` for anyone else. Two residues: the
Postgres data directory is written by the postgres container as its own uid,
and both images run as root, so container-written files land root-owned either
way. The group buys the directories a person actually works in: `imports` and
`archive`.

### 1.2 Getting the code there

The server runs from a **git clone of `git@github.com:dentalia-lj/mdr.git`**, not
an rsync and not shipped images. `scripts/deploy.sh` reads the checkout's git
metadata unconditionally, so a tree without `.git` stops at its first command,
and `--check` included.

A read-only deploy key, generated on the server:

```bash
ssh-keygen -t ed25519 -C "dentalia-server" -f ~/.ssh/id_ed25519_mdr -N ""
cat ~/.ssh/id_ed25519_mdr.pub
# GitHub: repo Settings -> Deploy keys -> Add. Leave "Allow write access" OFF.
```

GitHub refuses one deploy key on two repositories, so this key is new, not one
borrowed from elsewhere. And ssh does not offer a key under a non-default name
on its own, so name it for this host:

```
# ~/.ssh/config
Host github-mdr
    HostName github.com
    User git
    IdentityFile ~/.ssh/id_ed25519_mdr
    IdentitiesOnly yes
```

```bash
git clone github-mdr:dentalia-lj/mdr.git /srv/compliance/app
```

Two traps:

- **No `--depth`.** A shallow clone cannot check out an earlier commit, and the
  rollback (runbook § Deploy the working tree, "Rolling back") does exactly that.
- **Do not clone with `sudo`.** A root-owned checkout makes git refuse it as your
  user ("dubious ownership"), and `deploy.sh` dies on it before printing
  anything. If it has already happened:
  `git config --global --add safe.directory /srv/compliance/app`.

## 2. What the stack is

`docker compose up -d` brings up, in dependency order:

| Service | What | Image |
|---|---|---|
| `postgres` | Queue + registry + fetch ledger + audit, one database | `postgres:16` |
| `migrate` | One-shot, applies pending `migrations/*.sql`, exits 0 | worker image |
| `worker` | Claim → dispatch → finish loop. **Runs the crons too** | `Dockerfile` |
| `web` | Producer UI + read API, connects as the write-less `dentalia_api` role | `Dockerfile.web` |
| `caddy` | HTTP Basic reverse proxy, the sole ingress | `caddy:2.8` |

**There is no `scheduler` service.** The ten crons run on the queue as perpetual
self-deferring `scheduler.tick` jobs, re-armed on every worker start by
`arm_crons` ([app/workers/runner.py](../../app/workers/runner.py)). Starting the
worker starts the crons.

`test` (profile `test`) and `greenmail` (profile `email-dev`) are development
services. Neither belongs on a client server.

## 3. `.env`

Copy [.env.example](../../.env.example) and work through it. Three groups.

**First, the thing that makes most of that file a lie on a server.**
`docker-compose.yml` declares **no `env_file:`**, so `.env` is used only to
interpolate `${VAR}` into the compose file. A key reaches a container **only if
compose names it in that service's `environment:` block**. Most of
`app/config.py`'s surface is not named there: verified 2026-09-14, `BC_BASE_URL`,
`BC_USERNAME`, `BC_PASSWORD`, `BC_WRITE_ENABLED`, `UPLOAD_MAX_MB`,
`GATE_THRESHOLD_HIGH`/`MED`, `MODELS_*`, `RESOLVE_NAME_*`, `DISCOVER_*`,
`FETCH_*`, `BUDGET_*`, `QUEUE_*`, `STORAGE_ADAPTER` and `SOURCE_ADAPTER` appear
zero times in it. Setting any of those in `.env` changes nothing; the dataclass
default in `app/config.py` wins. What IS wired: `DATABASE_URL`,
`ANTHROPIC_API_KEY`, `BRAVE_API_KEY`, `ALERTS_WEBHOOK_URL`, `DISCOVER_HOLD`,
`EXTRACT_MODE`, `STORAGE_LOCAL_ROOT`, `STORAGE_BASE_URL`, every `SCHEDULER_*`
key (a test pins that one), the `WEB_*` keys and the `IMPORTS_HOST` /
`PGDATA_HOST` / `API_PORT` interpolations. `tests/test_compose_config.py` guards
only the `SCHEDULER_*` half. So **treat `.env` as the wired list, not as
`app/config.py`**: if a client install genuinely needs one of the unwired keys,
add it to that service's `environment:` block in the same change -- do not put it
in `.env` and assume.

Three groups of what you do set:

### 3.1 Compose refuses to start without these

| Key | Note |
|---|---|
| `PGDATA_HOST` | Host directory for Postgres data. No default since 2026-09-11 |
| `ANTHROPIC_API_KEY` | `${ANTHROPIC_API_KEY:?}` in the worker env |
| `DENTALIA_WEB_PASSWORD_HASH` | `docker run --rm -it caddy:2.8 caddy hash-password`, then type the password at the prompt. Not `--plaintext`, which leaves it in shell history and `ps`; and not `docker compose run caddy`, which needs this very hash to start and pulls up the whole stack behind it |

### 3.2 Set these or regret it later

| Key | Default | Why it matters on a client server |
|---|---|---|
| `IMPORTS_HOST` | `./imports` | Where the corpus and BC exports live on the host. Mounted read-only into `worker` and `web` |
| `IMPORTS_HOST_ABS` | empty | Absolute host path of the same directory; compose turns it into `WEB_PATH_REWRITES` so document links resolve without a per-request hash |
| `POSTGRES_PASSWORD` | `dentalia` | Only takes effect on the **first** `up` (initdb). Change it before the stack ever starts |
| `DENTALIA_API_PASSWORD` | `dentalia_api` | **Read § 3.3 before changing this** |
| `DENTALIA_WEB_USER` | `admin` | The Basic auth login |
| `API_PORT` | `8000` | Host port Caddy publishes on |
| `BRAVE_API_KEY` | empty | Discovery's search credential. Inert while `DISCOVER_HOLD` is on |
| `WEB_API_KEYS`, `WEB_BC_LINK_KEY` | empty | **Empty disables the caller class it names.** No `WEB_BC_LINK_KEY` means `/item/*` 404s outright, so the BC item-card hyperlink does not work |
| `WEB_PUBLIC_BASE_URL` | empty | Absolute base prefixed onto document URLs in the item payload. Required if the webshop backend embeds our links |
| `BC_BASE_URL`, `BC_USERNAME`, `BC_PASSWORD` | empty | Only for the BC routes (§ 6.2, § 6.4). **Not passed by compose** -- see the warning above and § 6.4 Route C |
| `ALERTS_WEBHOOK_URL` | empty | Wired into `worker` and `web`. Empty means a dead-lettered job is announced nowhere and somebody has to open `/dead` |
| `SCHEDULER_INGEST_WATCH_DIR` | empty | Empty makes the monthly ingest a documented no-op (§ 8) |

Everything else in `.env.example` is tuning with a working default. Do not
change thresholds at install time; [config-reference.md](config-reference.md)
carries the blast radius of each key.

### 3.3 The `dentalia_api` password trap

[migrations/008_api_role_dev_password.sql](../../migrations/008_api_role_dev_password.sql)
gives `dentalia_api` the literal password `dentalia_api` when the role has none,
so that `docker compose up` works out of the box. `DENTALIA_API_PASSWORD` in
`.env` changes only the **connection string** compose hands to `web`. Set it
without touching the role and `web` cannot connect; leave both alone and the UI
runs on a password that is published in this repository.

On a client server, after `migrate` has run and before anyone uses the UI:

```bash
docker compose exec -T postgres psql -U dentalia -d dentalia \
  -c "ALTER ROLE dentalia_api PASSWORD 'the-real-one'"
# then put the same value in .env as DENTALIA_API_PASSWORD, and:
docker compose up -d web
```

**It stays set.** `dentalia_api` is a cluster-global role while 008 runs once
per database, so every fresh database migrated in the same cluster runs 008
again: `schema-drift` on every `scripts/deploy.sh` and `--check`, the test
suite's database, `scripts/bringup-rehearsal.sh`. Until 2026-09-18 it did so
with an unconditional `ALTER ROLE ... PASSWORD`, which put the literal back over
a real password on the next deploy (measured: the stored verifier changed
across one `schema-drift` run). 008 was edited in place that day, by ruling, to
set the literal only when the role has no password; a set password is left
alone, pinned by `tests/test_migration_api_role.py`.

## 4. Persistence

Postgres data is already a host bind (`PGDATA_HOST`). The archive is **not**:
`docker-compose.yml` declares it as the named volume `archive_data`, which
`docker compose down -v` deletes and which a host backup tool will not see as a
directory. On a client server, bind both.

[docker-compose.prod.yml](../../docker-compose.prod.yml) ships in the repository
(since 2026-09-18). Turn it on in the server's `.env`:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml
ARCHIVE_HOST=/srv/compliance/archive
```

It does three things, each explained in its header: binds the archive to
`ARCHIVE_HOST` for `worker` (read-write) and `web` (read-only); caps `worker`
at `mem_limit: 3g`, because the first server has no swap and shares its kernel
with Dentalia's production app, and an OOM kill should land inside our
container rather than wherever the kernel picks; and rotates every default
service's json-file log at 10 MB x 5. The 3g is sized from that host's free
memory, not from a measured worker peak: read
`docker compose exec worker cat /sys/fs/cgroup/memory.peak` after the first
backfill and move it with evidence.

Compose is documented to merge a service's `volumes` by **target path**, so
the overlay's binds replace the `archive_data` mounts rather than adding to
them. Checked with Compose v5.3.0 on dev; **prove it on the server's own
Compose before the first `up`**, because an additive merge would leave the
archive in the volume and nothing would say so:

```bash
docker compose config | grep -i archive
```

Expect the host path twice and the string `archive_data` nowhere. The overlay does not
restate the `/imports` mounts: those already follow `IMPORTS_HOST`.

**Backup is not part of this system** and was ruled a separate order
(2026-09-03, `[no-registry-or-archive-backup]`). Two directories now hold
everything that is not re-derivable — `PGDATA_HOST` and `ARCHIVE_HOST` — so
tell their IT exactly that, in writing, and let them decide.

## 5. First `up`

```bash
docker compose up -d            # postgres -> migrate -> worker, web, caddy
docker compose ps               # every service healthy, `migrate` exited 0
```

`migrate`'s log warns that `storage.local_root './archive' is relative` and that
archived files are "lost on container restart". Ignore it: `migrate` never
archives anything, and `worker`, the service that does, runs with
`STORAGE_LOCAL_ROOT=/archive` (seen on the 2026-09-18 fresh-cluster rehearsal,
§ 9).

`docker compose up -d` does **not** pick up code changes on a running stack;
only the `test` service bind-mounts the repo. Use
[scripts/deploy.sh](../../scripts/deploy.sh) for every update after the first
install: it builds, dumps the database to `backups/`, migrates, restarts, then
**proves** the running images match the tree and the database matches
`migrations/`. Each run leaves a line in `backups/deploy.log` naming the commit,
the one it replaced and the dump taken before it; that line is the rollback
(runbook § Deploy the working tree, "Rolling back").

### 5.1 Updating a running server

```bash
cd /srv/compliance/app
git pull
./scripts/deploy.sh
```

`deploy.sh` never fetches: it verifies the images against **the checkout**, so
a forgotten `git pull` still prints "Deployed and verified", against old code.
Pull first, every time.

Two kinds of change it does not carry, because they live outside what it
rebuilds and restarts:

- **`Caddyfile` or `caddy/users/`.** `deploy.sh` restarts `worker` and `web`
  only. After a pull that touched either, `docker compose up -d caddy`.
- **`playbooks/*.json`.** The running pipeline reads playbooks from the
  database, not the files (§ 6.3), so a pulled JSON change sits unused.
  `docker compose run --rm worker python -m app.cli playbooks drift` shows what
  differs, and § 6.3 applies it.

Not yet exercised on the Dentalia server: at the time of writing no clone exists
there. The loop is what the tooling is built for and is run daily in
development.

## 6. Bring-up order

Steps 1, 3, 4 and 7 never change. Only 2, 5 and 6 branch on where the data comes
from. The order itself is not negotiable: two of these steps were discovered by
running [scripts/bringup-rehearsal.sh](../../scripts/bringup-rehearsal.sh)
against an empty database, and both fail *silently* when skipped.

| # | Step | Branches? |
|---|---|---|
| 1 | `migrate` (automatic, the `migrate` service) | no |
| 2 | **Manufacturer master** into `vendor_master` | yes, § 6.2 |
| 3 | `manufacturers seed --apply` | no, § 6.3 |
| 4 | `playbooks sync` | no, § 6.3 |
| 5 | **Items** into `item_mirror` | yes, § 6.4 |
| 6 | **Documents** into the archive | yes, § 6.6 |
| 7 | Unhold discovery | no, § 6.7 |

### 6.1 Why the order is what it is

- `manufacturer_bc_code` is foreign-keyed to `vendor_master`, so the playbook
  seed cannot run before step 2. **The readable refusal only covers a totally
  empty master**: the guard in `app/manufacturer_seed.py` is `if not
  vendor_rows`, so a master that loaded but is missing even one code a playbook
  claims still dies on the raw `ForeignKeyViolation: Key (code_source,
  code)=(LJ, 001)`. That is a live risk with a client-supplied export, not a
  theoretical one -- the 38 playbooks in the tree claim **46 distinct BC codes**,
  and the last recorded check that every claimed code is in the master covered 39
  codes on 2026-08-26 (migration 049's own comment). If step 3 dies on an FK, the
  fix is the missing vendor row, not the playbook.
- `manufacturers seed` writes **no aliases**: 381 manufacturers and 0 alias
  rows. `playbooks sync` writes them. Without it RESOLVE matches nothing and
  every group comes out unnamed, while every step reports success.
- `item_group.canonical_manufacturer` is effectively write-once (RESOLVE
  short-circuits on an existing link). A vendor master landing *after* the first
  ingest produces a split-brain no later import repairs; `regroup --apply` is the
  only way back.

### 6.2 Step 2: the manufacturer master

**Route A — file (the working route).** `Proizvajalci.xlsx`, columns `Šifra` and
`Ime`, 390 rows on the last delivery.

In the browser: `/import` → the vendors lane → dry-run preview naming every
added, renamed and disappeared code → Apply. This is `vendor.import`
([app/handlers/vendor_import.py](../../app/handlers/vendor_import.py)) and the
worker does the write; the web role holds no grant on `vendor_master`.

From the shell, same reader, same diff, same rename refusal:

```bash
docker compose run --rm worker python -m app.cli vendor-master \
    --file /imports/Proizvajalci.xlsx              # dry run
docker compose run --rm worker python -m app.cli vendor-master \
    --file /imports/Proizvajalci.xlsx --apply
```

A vendor import deliberately emits nothing, so **a later re-import that adds
codes leaves `manufacturer_alias` stale** until `playbooks sync` runs again; the
confirm screen says which and names the command. At install time step 4 covers it.

A code re-pointed at a different manufacturer is **refused** unless you pass
`--allow-renames`. That refusal is the guard against the write-once problem
above; do not reach for the flag without reading what it would move.

**Route B — BC `allmanufacturers`: NOT REACHABLE TODAY.**
[app/vendor_master.py](../../app/vendor_master.py)`::read_odata` is written and
tested against the endpoint b-s.si added on 2026-09-07, and it returns exactly
what the file reader returns, so `diff`, `apply`, the rename refusal and the
two-phase preview would all work unchanged. **It has no caller**: no CLI flag,
no handler, no route. Reaching it needs (a) a `--from-bc` path on the
`vendor-master` verb that builds a `BcClient` from `cfg.bc` and pages the
endpoint, and (b) one sampled record, because `BC_MANUFACTURER_PROFILE`'s `no`
and `name` are guessed from BC's other pages. The guard raises on the first
record if they are wrong, so a bad guess fails loudly rather than importing 390
nameless codes.

Until then: **every combination hand-feeds `Proizvajalci.xlsx`**, and that is
the documented fallback rather than a blocker — 390 rows that change rarely.

### 6.3 Steps 3 and 4: playbooks (no branch)

The 38 playbook JSON files ship in the repository, so they exist on a fresh
server whatever the data sources are. They are the seed; after import the
database holds the bodies (migration 050) and the UI edits them.

```bash
docker compose run --rm worker python -m app.cli manufacturers seed          # dry run, read the orphan report
docker compose run --rm worker python -m app.cli manufacturers seed --apply
docker compose run --rm worker python -m app.cli playbooks sync
```

Expected on a fresh database, verified 2026-09-03: 390 vendor codes → 381
manufacturers → 445 aliases. **Zero aliases after step 4 means step 4 did not
run**; nothing else reports it.

**Step 4 lands more than aliases, which is why step 6 depends on it.** The
playbook bodies in the database are what `backfill.scan` reads for
`skip_backfill`, `companion`, `exclude` and `coverage_map`, and what T0 reads for
its parse templates (`app/extract/t0_layout.py`, `app/playbooks.py::load_raw`,
both through the database once a source is set). None of those raise when
absent; they degrade to "no rule". Scan a corpus before step 4 and NEODENT is not
refused, Komet's coverage map is not read, and T0 falls back to generic
extraction -- silently, and at full LLM price.

New manufacturers are authored in the browser afterwards (`/onboarding`, or
*Start a playbook* on `/manufacturers/{name}`), not by editing files on the
server. See [playbook-authoring.md](playbook-authoring.md).

### 6.4 Step 5: the catalogue

**Route A — upload, in the browser.** `/import` → items lane → the job runs as a
**dry run** and returns a preview → Apply. Nothing is written until Apply. Limits: `.xlsx`,
`.xlsm`, `.xls`, `.csv` (`EXPORT_SUFFIXES`), and `UPLOAD_MAX_MB` (25 by default).

**Route B — a file already on the server.** `/ingest` lists `.csv`/`.xlsx` under
the imports mount (two levels deep, 200 entries) and also accepts a typed path.
This is the route for a file that arrived over SFTP: drop it in `IMPORTS_HOST`
and pick it in the browser. A relative path resolves against the imports
directory, an absolute one is used as-is.

Or from the shell:

```bash
docker compose run --rm worker python -m app.cli enqueue ingest.run \
    "ingest:$(date +%F)" \
    --payload '{"source":"csv","ref":"Artikli 3.7.2026.xlsx","catalogue":"LJ"}' \
    --priority interactive
```

**Route C — BC OData, from the CLI only.** Usable today with four caveats, all
verified 2026-09-14. `app/handlers/ingest.py` builds an HTTP client only when
`cfg.bc.base_url` is non-empty, and with it empty the adapter raises
`NotImplementedError` rather than reading anything -- **and `BC_BASE_URL` is one
of the keys compose does not pass** (§ 3), so putting it in `.env` is not enough.
Either add the three `BC_*` keys to `worker`'s `environment:` block, or pass them
on the one command that needs them:

```bash
docker compose run --rm \
    -e BC_BASE_URL=http://denwebnav:7048 -e BC_USERNAME=... -e BC_PASSWORD=... \
    worker python -m app.cli enqueue ingest.run \
    "ingest:bc:$(date +%F)" \
    --payload '{"source":"bc_odata","catalogue":"LJ","ref":"http://denwebnav:7048/proddentalia-NAS/api/dentalia/api/v1.0/companies(25ccc3d7-63f8-ec11-9e03-00155d012200)/allitems"}' \
    --priority interactive
```

> **`ref` must be an absolute URL string.** `BcApiAdapter._pages()` hands
> `self.ref` straight to httpx and follows `@odata.nextLink` from there. The
> `bc_odata` option on the `/ingest` **form** sends `{"company", "delta_since"}`
> instead, and nothing converts that into a URL, so the browser route cannot
> work. Use the CLI.

> **The manufacturer property is disputed, and getting it wrong is silent.**
> `LJ_ODATA_PROFILE` maps `manufacturer_raw` to `pteManufCodePrimary`
> ([app/adapters/source.py](../../app/adapters/source.py)), on an answer from
> b-s.si dated 2026-09-07. In the only payload we hold
> ([docs/samples/bc-api-2026-09-02-allitems.json](../samples/bc-api-2026-09-02-allitems.json))
> that property is **present but empty on all three records**, while
> `manufacturerCode` carries `"011"`. `_assert_profile_matches` checks presence,
> not emptiness, so a wrong choice mirrors the whole catalogue with no
> manufacturer code: no group is named, no playbook binds, and the REF gate can
> never establish a manufacturer. **Before any BC cutover, pull one live record
> and compare the two properties.** `curl` the endpoint with `?$top=1`, save it
> under `docs/samples/`, and run `python -m tools bc-api`.

> **There is no delta.** Nothing builds an OData `$filter`; `delta_since` is read
> by the web form and by nothing else. Every OData run is a full catalogue read.
> When delta lands it must key on `systemModifiedAt`, not
> `lastDateTimeModified` — they are two years apart on the sampled item.

`catalogue` in the payload must be exactly `LJ`. Nothing validates it: there is
no CHECK on `item_mirror.catalogue` and INGEST compares it to nothing, so a slip
tags every mirrored row under a value no report looks for.

Whatever the route, INGEST reports counters rather than going quiet: `seen`,
`changed`, `unchanged`, `non_md`, `md_unknown`, `missing_mfr_ref`,
`mfr_ref_prose`, `skipped`. Read them on `/ingest` or in `job.result`. An empty
BC response raises rather than reporting "every item unchanged".

### 6.5 What a first ingest does next

INGEST fans out one `resolve.group` per changed item, so a first run leaves
thousands of pending jobs. That is expected. `DISCOVER_HOLD` defaults to
**true** in compose (`${DISCOVER_HOLD:-true}`), which is what you want here:
RESOLVE builds the groups and emits no `discover.group`, so nothing web-searches
for documents that are about to be loaded from the corpus.

Two things will look wrong and are not:

- **The Documents page stays empty for a long time.** The queue drains
  breadth-first, so every `extract.doc` finishes before the first `validate.doc`
  runs, and only GATE writes documents (`[queue-breadth-first-stalls-output]`).
- **`pending` never reaches zero.** Ten of those pending jobs are the perpetual
  crons. Judge a run by `failed`/`dead` being zero, not by an empty queue.

### 6.6 Step 6: the documents

Two doors, and only two:

| Door | Volume | Who |
|---|---|---|
| `/upload` | One PDF per submit, 25 MB, magic-byte checked | Office staff, for a document that arrives by mail or from a supplier |
| `backfill.scan` | A whole folder | Us, at install, over the shell |

**Nothing in the tree speaks FTP or SFTP** (no `paramiko`, `pysftp` or `ftplib`
anywhere in `app/`). The pipeline never pulls from a supplier's server: the
files have to be placed on this server's filesystem first, by whatever means
their IT prefers — `rsync` over ssh, an sftp client, or a mount. Put them under
`IMPORTS_HOST`, one directory per manufacturer, and they appear to the worker as
`/imports/...`.

The straightest way to fill that directory is
[`scripts/mirror-mdr-site.py`](../../scripts/mirror-mdr-site.py), which pulls the
client's own publication at `www.dentalia.si/mdr-dokumentacija` (76 brand
folders, 2771 files, ~2 GB read 2026-09-21) into
`$IMPORTS_HOST/dentalia-web/<BRAND>/...`. It runs on the host, needs nothing but
python3, re-runs conditionally on `Last-Modified`, deletes nothing, and prints
the `backfill.scan` commands when it finishes. Runbook § *Mirroring the site
publication* has the flags and the caveats.

Then one job per brand folder:

```bash
docker compose run --rm worker python -m app.cli enqueue backfill.scan \
    "backfill:GC:$(date +%F)" \
    --payload '{"drive_folder": "/imports/dentalia-sftp/GC"}' --priority interactive
```

Rules that are not optional, each of which has cost someone a day:

- The path is the one the **worker container** sees (`/imports/...`), never a
  host path.
- **One brand folder per job.** The archive buckets under the scanned folder's
  own name, so a scan pointed at the corpus root files everything under that
  root's name with no manufacturer in the path. Cost and failure also isolate
  per brand.
- Only `*.pdf` is walked (case-insensitively). Word, Excel and image files in
  those folders are not picked up, with the one exception of Komet's two
  declared index files.
- A folder that does not exist, or holds no PDFs, **fails the job** rather than
  reporting `scanned: 0`.
- A playbook may carry `skip_backfill`, which refuses the folder outright and
  dead-letters on purpose. NEODENT carries one (client ruling 2026-08-19) and is
  the only one that does.
- Re-scanning is nearly free: a `content_hash` already in `fetch_log` is never
  re-emitted. The corollary is in § 10.

Expect this to cost money: a fresh database dedupes against nothing, so the
whole corpus is extracted at full price. Measured $0.041 per document **in sync
mode**, so a 1.300-PDF corpus is roughly $40-55. The budget keys are **display
only** -- they show on the KPI board and stop nothing.

`EXTRACT_MODE=batch` (compose default `sync`) roughly halves that through the
Batch API, and it is the obvious lever to reach for once you see the estimate.
**It has never run against the real API** -- every row in the cost ledger so far
carried `batch=false`, per `docker-compose.yml`'s own comment -- so flipping it on
a client's first backfill makes that install the first live exercise of the
self-defer and poll path. If you want it, prove it on one brand folder first.

Keep the catalogue ahead of the corpus for a second reason beyond § 6.1:
`backfill.scan` enqueues its `extract.doc` at the queue's bare `sweep` default
while the documented ingest runs `interactive`, so ingest and its `resolve.group`
fan-out win the claim. That ordering is what lets a backfilled document match
items at all -- VALIDATE resolves a `group_id`-null document against `item_group`
live, so a document extracted before its manufacturer's items are grouped has
nothing to match and stages instead.

### 6.7 Step 7: unhold discovery

Once the corpus has drained and the registry knows what it already holds:

```bash
# in .env: DISCOVER_HOLD=false, then
docker compose up -d worker
docker compose run --rm worker python -m app.cli regroup --manufacturer IVOCLAR            # dry run
docker compose run --rm worker python -m app.cli regroup --manufacturer IVOCLAR --apply
```

Per manufacturer, deliberately. Discovery fires only for the groups the corpus
did not cover. Discovery produces review debt rather than coverage — measured,
47% of groups produce a candidate and 4 of 69 documents cleared without a human
— so widen it as fast as someone is reviewing, and no faster.

## 7. Ingress

Caddy publishes on **loopback only**, both stacks:
`127.0.0.1:${API_PORT}` and `[::1]:${API_PORT}`. On a server that means the UI
is reachable from the server itself and nowhere else, which is almost never what
the office needs. `web` publishes no port of its own and must not be given one:
Caddy holds the Basic auth, and `web/access.py` is default-deny behind it.

Three ways to open it, in descending order of what we control:

1. **Publish Caddy on the LAN interface too**, from the prod overlay
   (`- "10.0.0.5:8000:8000"`). Basic auth over plain HTTP on their LAN.
   Simplest; no certificate. Note what an overlay can and cannot do here:
   **compose merges `ports` additively and offers no subtraction**, so this ADDS
   a publish and the two loopback binds stay (harmless). Dropping them means
   editing `docker-compose.yml` itself, which is why `docker-compose.dev.yml`
   exists in the first place -- see its header comment.
2. **Their reverse proxy in front** of Caddy, terminating TLS on a hostname
   their staff already trust. Keep Caddy: it is the authority on the two
   non-browser caller classes (`/item/*?k=`, `X-API-Key`).
3. **Tunnel or VPN**, no published port at all.

`/archive/*` is deliberately staff-only at both layers. Do not exclude it.

**The Dentalia server takes option 2, with two names** (2026-09-18,
[server audit](../2026-09-18-server-audit.md) § 6.1). Their host Caddy sends
`cw.dentalia.si` (office) and `api.cw.dentalia.si` (webshop backend, BC item
link, document files) to `127.0.0.1:8000`. Our `Caddyfile` narrows the API
name to the machine paths (runbook § Web UI, "The API name"). The API name is
hardcoded in our `Caddyfile`; renaming it means editing that rule and the test
beside it.

Set `WEB_PUBLIC_BASE_URL=https://api.cw.dentalia.si` **before the first BC
push**. That base is written into the links `bc.push` stores in Business
Central, so changing it afterwards leaves every link already pushed pointing at
the old name.

## 8. The automatic stages

Crons arm themselves on every worker start. `/scheduler` shows each one's
**Armed** state, when it last ran, and a **Re-arm** button for one that
dead-lettered. Note that `coverage-scan` is armed by `arm_crons` alone -- no
migration seeds it, unlike the other nine -- so a database that never started a
worker has nine.

**Run-now exists for three crons, and one of them does real work regardless of
its flag.** `web/scheduler_view.py::_run_now_payload` answers `report.weekly`,
`email.poll` **and `eudamed.certregister`**, and the certregister button enqueues
a pull whether or not `SCHEDULER_EUDAMED_CERTREGISTER_ENABLED` is set,
deliberately ("the flag gates the cron, not a person"). So a click while poking
around during § 9 fires a real multi-call fetch of the public EUDAMED register.
Know that before you hand the screen to anyone.

| Cron | Poll | Live on a fresh install? |
|---|---|---|
| `ingest.monthly` | 6h | **No** — a documented no-op until `SCHEDULER_INGEST_WATCH_DIR` is set |
| `expiry-scan` | 1h | Yes. Email drafting gated by `SCHEDULER_EXPIRY_EMAIL_ENABLED` (off) |
| `failure-monitor` | 1h | Yes. Re-onboarding gated by `SCHEDULER_FAILURE_REONBOARD_ENABLED` (off) |
| `coverage-scan` | 1h | **No** — gated by `SCHEDULER_COVERAGE_SCAN_ENABLED` (off). This is the one that sends uncovered groups through DISCOVER unattended, capped by `SCHEDULER_COVERAGE_SCAN_CAP`. `DISCOVER_HOLD` does **not** gate it |
| `report.weekly` | 1h | Yes |
| `email.poll` | 15m | **No** — needs IMAP credentials, `EMAIL_POLL_SINCE` (the deploy day; without it the poll dead-letters rather than read the mailbox's history) and `SCHEDULER_EMAIL_POLL_ENABLED`. Read-only: it never marks, moves or deletes mail |
| `eudamed.certregister` | 6h | **No** — gated by `SCHEDULER_EUDAMED_CERTREGISTER_ENABLED` (off). The tick runs and emits nothing |
| `eudamed.sweep-due` | 1h | Yes. It only marks manufacturers due (`due_at`); it emits no sweep |
| `health-watch` | 5m | Yes |
| `bc.push-drift` | 1h | **Unreachable**, not merely off: emission is gated by `BC_WRITE_ENABLED`, which compose does not pass (§ 3), so no `.env` value enables it |

Turning a gated cron on is a spend decision, not a config tidy-up: `coverage-scan` fetches and extracts, `eudamed.certregister` reaches a public register, and `email.poll` reads a mailbox. Turn them on one at a time, with someone watching `/scheduler` and the KPI board's spend tile.

To automate the monthly catalogue refresh, point
`SCHEDULER_INGEST_WATCH_DIR` at the directory their export lands in and set
`SCHEDULER_INGEST_GLOB`. The tick takes the lexicographically latest match once
per month. That is the hands-off route for a client who drops a new
`Artikli.xlsx` on the server every month.

## 9. Prove it before handing over

`deploy.sh --check` hashes the checkout with the host's `python3` (since
2026-09-18; before that it needed the dev-only `test` container):

```bash
./scripts/deploy.sh --check
docker compose exec -T worker python -m app.cli healthcheck --service worker
docker compose exec -T postgres psql -U dentalia -d dentalia -At -c "
  SELECT 'vendors', count(*) FROM vendor_master
  UNION ALL SELECT 'manufacturers', count(*) FROM manufacturer
  UNION ALL SELECT 'aliases', count(*) FROM manufacturer_alias
  UNION ALL SELECT 'items', count(*) FROM item_mirror
  UNION ALL SELECT 'groups', count(*) FROM item_group
  UNION ALL SELECT 'groups not named after a manufacturer',
         count(*) FROM item_group g
         WHERE g.canonical_manufacturer <> ''
           AND NOT EXISTS (SELECT 1 FROM manufacturer m
                           WHERE m.canonical_name = g.canonical_manufacturer)
  UNION ALL SELECT 'documents', count(*) FROM document
  UNION ALL SELECT 'failed jobs', count(*) FROM job WHERE status IN ('failed','dead')"
```

Every line must be non-zero except **groups not named after a manufacturer**
and **failed jobs**, which must be zero -- and **documents**, which stays zero
until step 6 has drained.

**Why that group query.** `canonical_manufacturer` is `text NOT NULL`
(migration 002) and RESOLVE's `_alias_lookup` self-seeds a miss by inserting the
raw BC code as its own canonical name, so a group RESOLVE could not name is
called `011`, never null. A NULL check cannot fail, and
`scripts/bringup-rehearsal.sh`'s own step-8 assertion has that hole. The query
asks whether any group carries a name that is not a manufacturer's
`canonical_name`. Groups with an empty name are excluded: they hold catalogue
rows with no manufacturer code at all (INGEST counts them as
`manufacturer_code_blank`), 2 groups from `Artikli 3.7.2026.xlsx`, and no import
order can name them.

Until 2026-09-18 this line asked whether a group was named by a code present in
`vendor_master`, and said to expect 0. That cannot hold: CEFLA's BC code is the
string `CEFLA`, its own name, so its 226 correctly named groups matched on the
fresh install and 220 on dev.

A non-zero count means step 4 did not run, or the vendor master landed after the
first ingest. `regroup` refuses a group that already carries a document link, so
this gets harder to repair the longer it runs.

**Rehearsed 2026-09-18 on an empty cluster**, as a throwaway compose project on
dev with this overlay on, the real `Proizvajalci.xlsx` and
`Artikli 3.7.2026.xlsx`, discovery held and a deliberately invalid Anthropic key
(so any LLM call in this path would have failed visibly; none did). All 70
migrations applied; `web` came up on 008's literal; steps 2-5 gave 390 vendor
codes, 381 manufacturers (38 with a playbook body), 445 aliases then 446 after
ingest (the self-seed of the empty code), 15.958 items and 8.427 groups; the
ingest reported `seen` 19.091, `non_md` 3.133, `md_unknown` 11.693,
`missing_mfr_ref` 7.082, `mfr_ref_prose` 364; the 15.958 `resolve.group` jobs
drained in about 8 minutes with one worker; 0 failed or dead, 0 `discover.group`,
10 scheduler ticks, no drift. Office pages answered 200 as staff through the web
role; a real `dentalia_api` password set per § 3.3 survived `schema-drift` and a
`web` restart. Not exercised: documents (step 6) and discovery (step 7).

**Failed or dead jobs need reading, not just counting.** GATE raises rather than
stages when a required field has no value, or when its evidence carries no
verbatim, tier or confidence (`app/handlers/gate.py::_require_complete_evidence`),
so a heterogeneous first backfill can dead-letter a document whose archived file
and other fields are perfectly good. Open each one at `/jobs/{id}`.

Then, in the browser, and as staff rather than over psql -- the counts above run
as the owner role and prove nothing about the web role's grants, and
`schema-drift` cannot either, since it diffs against a scratch database built
from the same migrations: the status board, `/staging`, `/manual`, `/documents`,
`/manufacturers`, `/scheduler` (ten armed crons) and `/emails` each render, a
document opens its PDF from `/archive/...`, and `/upload` accepts one file end to
end.

## 10. Failure modes, ranked by how likely they are to bite

| Symptom | Cause | Fix |
|---|---|---|
| `web` unhealthy, cannot connect to Postgres | `DENTALIA_API_PASSWORD` changed without the `ALTER ROLE`, or the role reset by a 008 older than 2026-09-18 (a stale worker image still carries one) | § 3.3 |
| A key set in `.env` has no effect | Compose declares no `env_file`, and most of `app/config.py` is in no `environment:` block | § 3: add the key to that service, or pass `-e` |
| Compose refuses to start | `PGDATA_HOST`, `ANTHROPIC_API_KEY` or `DENTALIA_WEB_PASSWORD_HASH` missing | § 3.1 |
| Every group unnamed, playbooks bind nothing | `playbooks sync` skipped, or the vendor master landed after the first ingest | `playbooks sync`, then `regroup --apply` per manufacturer |
| `manufacturers seed` refuses | `vendor_master` empty | Step 2 first |
| BC ingest mirrors items with no manufacturer | The `pteManufCodePrimary` / `manufacturerCode` question in § 6.4 | Pull a live record before cutover |
| `backfill.scan` dead-letters | Wrong path, host path instead of container path, empty folder, or a `skip_backfill` playbook | § 6.6 |
| A deleted `extract.doc` job strands its document forever | `fetch_log` already records the hash, so a re-scan never re-emits it | Delete the `fetch_log` rows with the jobs, and only while no `document` references them (`fetch_log.doc_id`) |
| The archive vanishes | `docker compose down -v` on a named-volume install | § 4, bind it |
| The office cannot reach the UI | Caddy is loopback-only | § 7 |
| The registry looks empty mid-run | Breadth-first drain | § 6.5, wait |

## 11. Not covered here

- **Backup and restore.** Not scheduled work (2026-09-03). § 4 names the two
  directories that hold everything.
- **TLS.** The v0 posture is Basic auth on loopback. § 7 lists the options; none
  is implemented in the repo.
- **Real logins.** Decisions record `user:admin` unless a trusted proxy sets
  `WEB_TRUSTED_USER_HEADER`. See `WEB_REQUIRE_AUTHENTICATED_USER`.
- **BC write-back.** The stage is built and gated off (`BC_WRITE_ENABLED`);
  nothing in the tree has ever made a real PATCH, and the auth scheme is a
  guess. [limits.md](limits.md) carries the detail.
- **A second Business Central.** There is one BC, one article numbering, one
  pipeline. Do not reintroduce a catalogue choice.

## Related

- [runbook.md](../runbook.md) — local development, the full CLI, route-by-route UI
- [troubleshooting.md](../troubleshooting.md) — known failure modes at runtime
- [config-reference.md](config-reference.md) — every key, default and blast radius
- [limits.md](limits.md) — what the system will not do, and why
- [2026-09-03-bc-api-integration.md](../2026-09-03-bc-api-integration.md) — the BC endpoints, field map and open questions
- [scripts/bringup-rehearsal.sh](../../scripts/bringup-rehearsal.sh) — the bring-up order, executable
