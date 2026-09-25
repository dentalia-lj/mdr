# Dentalia Compliance Pipeline

Compliance document registry for Dentalia's medical-device catalogue. It
discovers, fetches, extracts, validates and stores MDR/MDD documents
(declarations of conformity, EC certificates, IFUs, ISO certificates), and every
value it publishes carries evidence: the source URL, an archived copy, the page,
the verbatim string it was read from and a confidence.

It produces two things, and everything else serves them:

1. a database linking each Business Central item to the compliance documents
   that cover it, typed, dated and supersession-chained;
2. a deduplicated, hash-addressed archive of those documents under our control.

**This page is for whoever installs or updates the system on a server.**
Development is at the bottom.

---

## The stack

`docker compose up -d` brings up, in dependency order:

| Service | What it is |
|---|---|
| `postgres` | Queue, registry, fetch ledger and audit log. One database |
| `migrate` | One-shot. Applies pending `migrations/*.sql`, then exits 0 |
| `worker` | Claim, dispatch, finish. **Runs the scheduled jobs too** |
| `web` | The office UI and the read API. Connects as a role with no write grants on the registry |
| `caddy` | HTTP Basic reverse proxy. The only way in |

There is no separate scheduler. The ten recurring jobs run on the queue itself
and are re-armed every time the worker starts, so starting the worker starts
them.

`test` and `greenmail` are development-only and do not belong on a server.

## Prerequisites

- Linux with Docker and Compose v2, `git`, and `python3`. Nothing else runs on
  the host: no Postgres, no Node, no Python application. `python3` is needed
  only because `scripts/deploy.sh` hashes the checkout with it, standard library
  only.
- `docker-buildx`, which Compose v2 uses to build. Check with
  `dpkg-query -W docker-buildx` before you start.
- Outbound HTTPS to `api.anthropic.com`, and to the search API once discovery is
  switched on. **Nothing needs inbound internet.**
- Disk for the archive and the corpus beside it. The development registry is
  713 MB of archive against a 746 MB corpus; both grow with the catalogue.
- If Business Central OData is in use, the `worker` container must be able to
  reach the BC host.

## First install

Follow **[docs/dev/deployment.md](docs/dev/deployment.md)**. It owns the whole
procedure and it is not summarised here, because the order matters: the
manufacturer master has to land before the first catalogue ingest, and no later
import repairs it if it does not.

What that document covers, in order: what to collect beforehand, the `.env`
file, where Postgres and the archive live on the host, the first `up`, the
seven bring-up steps, ingress, the scheduled jobs, and the checks to run before
handing the system over.

## Getting the code onto the server

The system is deployed from a **git clone** of the GitHub repository, not from
an rsync or a shipped image. `scripts/deploy.sh` reads the checkout's git
metadata, so a copy without a `.git` directory fails at the first command.

The repository is private, and **the server stores no GitHub credential.** The
person deploying logs in with SSH agent forwarding (`ssh -A`), and `git` on the
server uses that person's own GitHub key through the connection. It works only
while they are logged in, which is when a deploy happens anyway. Anyone who
takes this over needs SSH access to the server and read access to the
repository, nothing else.

```bash
# your machine
eval "$(ssh-agent -s)"; ssh-add ~/.ssh/<your GitHub key>
ssh -A <user>@<server>

# on the server, once, as the user that runs the stack (not sudo)
ssh -T git@github.com         # first time: check GitHub's published host key fingerprint
git clone git@github.com:dentalia-lj/mdr.git /srv/compliance/app
```

Do not use `--depth`: a shallow clone cannot check out an earlier commit, which
is what rolling back needs. If a repository admin later adds a read-only deploy
key for the server, `git pull` works without anyone logged in and nothing else
changes; [deployment.md § 1.2](docs/dev/deployment.md) has the details and what
to check when a pull fails.

## Updating a running server

```bash
# your machine: an agent holding your GitHub key, forwarded with -A
eval "$(ssh-agent -s)"; ssh-add ~/.ssh/<your GitHub key>
ssh -A <user>@<server>

# on the server
cd /srv/compliance/app
git pull
./scripts/deploy.sh
```

`deploy.sh` never fetches, so pull first: it verifies the running images
against the checkout, and a stale checkout verifies green. It builds the images,
**dumps the database before it migrates**,
applies pending migrations, restarts `worker` and `web`, and then verifies that
what is running is what is in the checkout. That last step is the point: a
deploy that builds and restarts without checking has told you nothing.

```bash
./scripts/deploy.sh --check    # verify only, change nothing
```

Change code in the repository, never in the server's checkout: a local edit
there makes the next `git pull` refuse or merge.

It restarts `worker` and `web` only. A pull that touched `Caddyfile` needs
`docker compose up -d caddy`, and one that touched `playbooks/` needs the
database updated too, since the pipeline reads playbooks from there
([deployment.md § 5.1](docs/dev/deployment.md)).

Each deploy keeps the five most recent dumps in `backups/` and appends one line
to `backups/deploy.log` recording when it ran, from which commit, and whether
the verification passed. That line and that dump are the rollback; the
procedure is in [docs/runbook.md](docs/runbook.md) under *Rolling back*.
Restoring loses everything written after the dump was taken.

> The clone was made on the production server on 2026-09-25; the update loop
> has not yet run there. Expect to smooth something on the first run.

## What it does not do

Three limits that are deliberate, so nobody discovers them at the wrong moment.
The full list is [docs/dev/limits.md](docs/dev/limits.md).

- **Nothing backs up the registry or the archive.** Two host directories hold
  everything that cannot be regenerated: the Postgres data directory and the
  archive. Backing them up is the operator's, and it is not built in.
- **No TLS of its own.** Caddy binds to loopback and holds HTTP Basic auth. TLS
  is expected from a reverse proxy in front of it.
- **The review queue needs a person.** Documents the system cannot publish on
  the evidence it has are staged for a human decision. Nothing publishes them
  on its own, by design, and the queue grows until someone works it.

## Development

A local checkout runs the same stack.

```bash
cp .env.example .env
```

Four values have no default and compose refuses to start without them:

| Key | How to get it |
|---|---|
| `PGDATA_HOST` | A directory of its own for this checkout's Postgres data. On WSL2 it must be a native Linux path, not `/mnt/...` |
| `ANTHROPIC_API_KEY` | Your key. Every document that is not handled by a template escalates to a model |
| `DENTALIA_WEB_PASSWORD_HASH` | `docker run --rm -it caddy:2.8 caddy hash-password`, then type a password at the prompt. Do not pass it on the command line, where it lands in your shell history |
| `ARCHIVE_HOST` | Only when the production overlay is loaded. A local checkout leaves it unset |

Then:

```bash
docker compose up -d                    # postgres -> migrate -> worker -> web -> caddy
open http://127.0.0.1:8000              # the office UI

docker compose --profile test up -d test
./scripts/test.sh                       # the whole suite
./scripts/test.sh tests/test_queue.py   # one file
```

Tests always go through `./scripts/test.sh`, never a bare `pytest`: the wrapper
execs into the long-lived `test` container and holds a semaphore that stops
concurrent runs from fighting over the database.

## Where everything is

| You are | Read |
|---|---|
| installing or updating the server | [docs/dev/deployment.md](docs/dev/deployment.md), then [docs/runbook.md](docs/runbook.md) |
| going to change the code | [docs/README.md](docs/README.md) for the index, [CLAUDE.md](CLAUDE.md) for the invariants, [docs/architecture.md](docs/architecture.md) and [docs/code-map.md](docs/code-map.md) |
| using the screens | [docs/guide/](docs/guide/), in English and Slovene |
| asking what the system actually does | [docs/2026-08-24-what-the-system-does-en.md](docs/2026-08-24-what-the-system-does-en.md) ([SL](docs/2026-08-24-what-the-system-does-sl.md)) |
| wondering where the project stands | [PHASES.md](PHASES.md) |
