# Runbook

How to run, inspect, and test the stack locally. When something breaks, see [troubleshooting.md](troubleshooting.md).

## Prerequisites

- Docker + Docker Compose (the stack is compose-first; nothing needs to run on the host).
- **`PGDATA_HOST` is required** (no default since 2026-09-11; compose refuses to start without it). The main checkout's `.env` names the registry, e.g. `$HOME/pgdata/dentalia`. **A worktree's `.env` must name a directory of its own**; when copying the main `.env` into a worktree, change this line. The compose project name does not move the bind, and a worktree that inherited the old default ran a second Postgres on the registry's files from 08-27 to 09-09 ([state](state/2026-09-11.md)). On WSL2 the path must be WSL-native ext4; a Windows `/mnt/...` path breaks Postgres (9p filesystem).
- `.env` in the repo root, copied from [.env.example](../.env.example). Everything else has a dev default, but **`ANTHROPIC_API_KEY` is required: the worker refuses to start without it** and compose fails fast (`${ANTHROPIC_API_KEY:?}`) rather than starting a worker that cannot extract. It was previously described as "only needed for live T1/T2 calls", which was true of the code and false of the deployment — the key was never passed into the container at all, so the first containerised run failed 79 of 149 extractions one job at a time. Only `doc_class` `msds` and `business-doc` finish without an LLM call; every in-scope document escalates past T0 and needs the key.

## Start the stack

```bash
docker compose up -d
```

Brings up, in dependency order: `postgres` (healthcheck-gated) -> `migrate` (one-shot, applies pending `migrations/*.sql`, exits 0) -> `worker` and `web`. There is no `scheduler` service: the crons run on the queue as ten perpetual `scheduler.tick` jobs claimed by `worker` (migrations 054/055, then 064 and 065), so starting the worker starts the crons. The standalone process was deleted 2026-09-02; it had needed `--profile full` until 2026-08-31, did not get it, and no cron ran between 2026-08-21 and then.

Variants:

```bash
docker compose up -d --scale worker=4     # more competing workers
docker compose logs -f worker             # follow a service's logs
docker compose down                       # stop; DB data survives in PGDATA_HOST
```

If you build the worker image manually, target the runtime stage explicitly: `docker build --target app .` (the Dockerfile ends with a `test` stage, which an untargeted build would resolve to; compose sets the target for you).

## Bring up a fresh environment (`scripts/bringup-rehearsal.sh`)

> **Installing on a client server, not a dev checkout?** Read
> [dev/deployment.md](dev/deployment.md) instead. It owns the same order plus
> what this section assumes away: where the data comes from when it is not a
> file, persistence, ingress, the `dentalia_api` password, and the corpus load.

```bash
./scripts/bringup-rehearsal.sh          # throwaway database, dropped after
./scripts/bringup-rehearsal.sh --keep   # leave it, to poke at
```

**The order below is not optional, and two of its steps were not written down
anywhere until the rehearsal was run.** Against an empty database:

| # | Step | Why it is where it is |
|---|---|---|
| 1 | `migrate` | — |
| 2 | `vendor-master --file imports/Proizvajalci.xlsx --apply` | **`manufacturer_bc_code` is foreign-keyed to `vendor_master`.** Seeding the playbooks first dies with `ForeignKeyViolation: Key (code_source, code)=(LJ, 001) is not present in table "vendor_master"` |
| 3 | `manufacturers seed --apply` | Writes `manufacturer`, its BC codes and the playbook bodies |
| 4 | `playbooks sync` | **The seed writes NO aliases** — 381 manufacturers and 0 alias rows. Without this RESOLVE matches nothing and every group comes out unnamed, while every step still reports success |
| 5 | `enqueue ingest.run` + drain | — |

Verified end to end 2026-09-03: 390 vendor codes, 381 manufacturers, 445
aliases, 15.958 items mirrored, 4.265 medical-device items, 273 groups all
carrying a canonical manufacturer, no failed or dead jobs.

`pending` jobs at the end are expected: the drain is capped and INGEST fans out
one `resolve.group` per item. The assertion is that nothing FAILED.

**Not covered**, so a pass is not read as more than it is: no live fetching, so
the archive and `archive_url` rewrite path is not rehearsed; and it exercises
the code the containers run rather than starting containers. Use
`scripts/deploy.sh` for the image and schema half.

## Deploy the working tree (`scripts/deploy.sh`)

```bash
./scripts/deploy.sh            # build, dump, migrate, restart, then verify
./scripts/deploy.sh --check    # verify only, change nothing
```

It needs `python3` on the host (any 3.x the tree's `app/version.py` runs on;
standard library only) to hash the checkout. Until 2026-09-18 that hash was
computed inside the `test` container, which a client server does not run; the
two give the same digests from the same files, checked per root that day.

**Every deploy dumps the database before it migrates**, to `backups/` in the
checkout (gitignored, and kept out of the image build by `.dockerignore`), and
keeps the newest five. `pg_dump` reads a snapshot: the stack keeps serving and
the workers keep claiming jobs while it runs; only DDL would wait, and the
migration starts after the dump finishes. A dump that fails, or does not
restore into a scratch database, stops the deploy before anything migrates. Measured 2026-09-18: the 64 MB dev database dumps to
7.1 MB. Each run then appends one line to `backups/deploy.log`:

```
2026-09-18T10:21:03Z commit=<sha> dirty=5 previous=none dump=backups/20260918T102045Z-ran-none.dump result=verified
```

`commit` is what was deployed, `dirty` how many tracked files differed from it,
`previous` the commit that was running when the dump was taken. `--check`
prints the last line. The dump is a rollback point on the same disk, not a
backup: backup stays out of scope (`[no-registry-or-archive-backup]`).

### Rolling back

Restoring loses everything written after the dump: jobs, reviews, imports.

```bash
tail -3 backups/deploy.log                       # pick the dump and its `previous` commit
docker compose stop worker web
docker compose exec -T postgres psql -U dentalia -d postgres \
  -c "DROP DATABASE dentalia WITH (FORCE)" -c "CREATE DATABASE dentalia"
docker compose exec -T postgres pg_restore -U dentalia -d dentalia < backups/<the dump>
git checkout <previous>
./scripts/deploy.sh
```

`deploy.sh` has already restored each dump once before keeping it: it restores
into a scratch database in the same cluster with `--exit-on-error`, drops it,
and refuses to migrate if that fails (the `.partial` file is left for
inspection). A table of contents was not enough: on 2026-09-18 the dev dump
listed cleanly and did not restore, on four orphaned array types
(`_eudamed_article_status`, `_eudamed_declaration_gap`, `_eudamed_gap_summary`,
`_eudamed_sweep_delta`) whose element types no longer existed. They were catalog
debris, dropped from the dev database the same day. Before that, a restore
without `--exit-on-error` completed with 8 errors ignored and identical
`document`, `item_mirror`, `evidence` and `audit_log` counts, `schema-drift`
clean, in 4 seconds. The `DROP DATABASE` step above was not rehearsed on a live
database.

**`docker compose up -d` does not pick up your code.** Only the `test` service
bind-mounts the repo; `worker` and `web` run baked images, so a
commit changes the tree and not the running system. This script closes that,
and — more importantly — **proves** it closed: it compares each running image's
per-root source digest against the tree's, then runs `schema-drift` against the
database. It exits 1 and says `DEPLOY NOT VERIFIED` if anything disagrees.

Verification is the point. Nothing previously made a rebuild happen: on
2026-09-03 the worker carried current code only because an unrelated session
had rebuilt it minutes before, and the running web image was two files behind
the tree with nothing anywhere reporting it.

It compares **per root** (`app/`, `migrations/`, `web/`), never the combined
digest, because no image carries all three: `Dockerfile.web` copies no
`migrations/` and the worker's `app` stage copies no `web/`. So `ABSENT` for a
root is correct and is not counted as staleness, while a root that IS present
and differs is. An image too old to answer at all is reported as stale rather
than crashing the run, since that is exactly the condition being tested for,
and an image whose own `version.py` does not know a root (`UNKNOWN`) is stale
by definition — it predates the root being added.

**`web/` joined the fingerprint on 2026-09-14**, covering `*.py`, `*.html`,
`*.css` and `*.js` under it. Until then only `app/` and `migrations/` were
hashed, and the web image carries neither exclusively — so that morning
`--check` printed "Deployed and verified" while the running `web` container had
no `web/missing.py`, a different `web/app.py`, and answered 404 on `/missing`.
A template counts as behaviour here for the same reason the missing route did:
what the page says is what the user gets. Binary assets under `web/static`
(fonts, images) are excluded — they change with neither code nor copy.

## Web UI (producer only)

At `http://127.0.0.1:8000` (host port `API_PORT`), behind the `caddy` reverse proxy — HTTP Basic auth on every route except `/healthz` and the two exclusions below (S1.6, gap G3 v0). `web` itself connects as the write-less `dentalia_api` role and can only read + enqueue jobs; it publishes no port of its own, so `caddy` is the only way in.

Two non-browser caller classes now also reach it, and Caddy does **not** demand Basic of them: `/item/*` (the BC item-card link, which authenticates with `?k=`) and any request carrying an `X-API-Key` header (the webshop backend). `basic_auth` cannot express "Basic **or** a key", so those two are excluded from the `@protected` matcher and **the app is the authority on their credentials** — a request with a bogus `X-API-Key` reaches the app and is rejected there. `/archive/*` is deliberately not excluded: it serves the archive by path, which is walkable, so it stays staff-only at both layers. The whole policy is one table in `web/access.py`, installed as a sync FastAPI dependency (not middleware, which would force `async def`) and **default-deny**, so a route added later is staff-only until someone opens it. See `/api-reference` in the UI, and `docs/superpowers/specs/2026-08-25-item-document-access-design.md`.

**The API name.** On the client server two names reach this one proxy: `cw.dentalia.si` for the office and `api.cw.dentalia.si` for machines. On the API name the `Caddyfile`'s `@api_host_refused` rule answers a plain-text `Not found` (404) to every path except `/api/*`, `/documents/{id}/file`, `/item/*`, `/static/*` and `/healthz`, which are exactly the paths `web/access.py` opens to a non-staff caller; `tests/test_access.py` keeps the two lists equal. Those five then get the same auth as on any other name. The office name, and `127.0.0.1:8000` in dev, still serve everything. A JSON `{"detail": ...}` body means the app answered; plain `Not found` means the rule did.

First-time setup — set the fallback account in `.env`:

```bash
docker compose run --rm caddy caddy hash-password --plaintext 'your-password'
# put the output in .env:
#   DENTALIA_WEB_USER=your-username
#   DENTALIA_WEB_PASSWORD_HASH=<the bcrypt hash>
```

### Adding a person

One login per person (spec § 2, D2), so the audit trail names a person rather
than `admin`. Caddy's one `basic_auth` block takes a `<username> <bcrypt hash>`
line each, and the `Caddyfile` pulls them in with
`import /etc/caddy/users/*.caddy` from `./caddy/users`, mounted read-only into
the container. That directory is committed; its `*.caddy` files are gitignored,
because the hashes are the credential.

```bash
docker compose run --rm caddy caddy hash-password --plaintext 'their-password'
# append one line to caddy/users/people.caddy (create it if it is not there):
#   maja $2a$14$...
docker compose up -d caddy          # picks up the new file
```

Verified with the project's own image on 2026-09-15: `import` is spliced in at
parse time, so those lines really are accounts of that block, and a glob that
matches nothing is a warning rather than an error, so an empty `caddy/users/`
and the `.env` pair alone is a working proxy.

```bash
docker compose run --rm --no-deps -T caddy \
  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

Validate through compose, not a bare `docker run` fed by `set -a; . ./.env`.
That was the snippet here until 2026-09-18, and it fails on the usual `.env`:
a bcrypt hash written unquoted (`$2a$14$...`) is expanded by bash into
garbage, so the pair arrives empty and the parse fails on `username and
password cannot be empty or missing` whatever your file says. Compose reads
`.env` literally and mounts exactly what the running proxy mounts; `--no-deps`
keeps it from starting `web`. A genuinely malformed account line fails with the
same message but names the file and line it came from, which is how you tell
the two apart.

Removing a person is deleting their line and `docker compose up -d caddy`.

### Making a person an operator

`WEB_OPERATOR_USERS` in `.env`, comma-separated, matched exactly against the
Caddy login (spec § 2, D3):

```
WEB_OPERATOR_USERS=denis,maja
```

Then `docker compose up -d web`. It does two things. The collapsed **Operator**
menu group hides itself from everyone else, and the writes behind it answer
everyone else 403 with *This action is for operators.*:

- the playbook **Save**, **Claim a BC code**, **Restore** an earlier revision,
  and the crawl **probe** and its **save** (those two spend money);
- every write in the guided onboarding flow (start, domains, skip, unskip);
- **Send to Business Central** on `/bc-push`;
- **Run now** and **Arm** on `/scheduler`;
- **Re-run all** and per-job **Re-run** in the developer section of Failed;
- `POST /ingest`, the developer ingest form.

Their pages stay reachable by URL on purpose: this hides work that is not the
office's and refuses the presses that are not either. `docs/dev/config-reference.md`
carries the route-by-route table.

**Empty (the default) means every login is an operator and nothing is refused.**
That is what keeps a running system working before anyone has configured a name,
so leaving it empty is a valid state, not an unfinished one.

Three things are deliberately never refused, because they are the office's own
work: `/import` apply, which is how the catalogue arrives, and **Try the N
again** and **Search again for these N** on Failed, which Today also offers.

| Route | What |
|---|---|
| `/` | **Today**, the office's first screen (office UI redesign spec § 4): four lists of what is waiting for a person — documents to review, missing documents, expiring certificates, renewal emails to send — each with its count, its oldest waiting date and one button; then the Failed split's three lines with the two buttons an office login may press (a line whose count is zero is not rendered); then the coverage headline with its definition (`coverage_headline`, D4); then the latest two weekly reports, named as weeks. Every figure is read through the helper the page behind it reads, so Today and that page cannot disagree. Writes nothing |
| `/status` | Status board + **KPI board** (coverage, match_basis distribution, missing_mfr_ref rate, staging queue, manual/dead counts, jobs by type+status, spend vs budget cap — `docs/specs/kpi.md`); recent jobs with type/status/dedupe-key filters, item-mirror summary. The jobs table states when each was queued, when it finished and how long it took (`finished_at`, migration 026, stamped on the terminal transitions only — a retry is not a finish, so a `failed` row keeps an em dash). It answered on `/` until 2026-09-14; the office UI redesign (spec § 4) put **Today** there and moved the board here, named **System status** in the menu's collapsed operator block. It also carries the per-service health chips that used to sit in the header strip |
| `/_pulse` | The **office menu** itself (`base.html`'s sidebar), with the four counts a person acts on — Review, Missing documents, Expiring, Renewal emails — and the health line under it: "● System working", or the services that stopped beating, plus "N websites timed out · details" when `failed_counts`'s timeout class is non-zero. An HTMX fragment on `load, every 15s` rather than context threaded through ~30 handlers, so no page waits on it and a tab left open stays honest without a reload; `base.html` renders the SAME template with no counts, so with JS off the menu is still a menu and the counts read as not-loaded rather than as a silent zero. It was a sticky strip of five counters above every page's content until 2026-09-14 (spec § 7): the counts were always about four menu entries, and a strip repeating them was a second place to read the same number. `expiring` is `_expiring_counts`, which renders `web/app.py`'s `_LAPSING_CTE` under `_LAPSING_GROUP_BY` — the literal text `/expiry` runs — counting recently-lapsed plus lapsing-soon, one row per CERTIFICATE, so the menu and the board cannot disagree. 39ms of the ~60 is that one query, paid deliberately: the cheap variant that drops the manufacturer joins costs 5ms and silently merges two manufacturers whenever `cert_number` is NULL. `_pulse_counts` outlives the strip and is what `/pipeline` shows |
| `/search` | One box over items, documents and manufacturers, reachable from the sidebar on every page. Matches item ref / product name / supplier article number, certificate number / content hash, and manufacturer name / BC code; a bare number is also read as a document id. Ten rows a section, each linking to the list page that owns the full answer. Substring matching only — no fuzzy scoring, since this is navigation and pg_trgm belongs to the matching pipeline |
| `/jobs/{id}` | Full job detail: payload, attempts, timestamps, last error. Linked from the System status board's job table (`/status`) and from every row on `/dead` |
| `/items` | Browse the catalogue with its compliance coverage (production/staged/superseded document counts, next expiry); search by item ref, name or manufacturer. **Next expiry counts only documents issued under MDR or MDD** and shows a dash when none covers the item — an ISO 13485 certificate expires, but not on any article's behalf, and lending its date to one claims coverage nobody asserted. This is the one place the board diverges from `/expiry` and the renewal scan, which list DOCUMENTS and correctly chase a lapsing QMS certificate. Applied 2026-08-21 after CARL MARTIN's backfill bound one certificate to 2.567 items and every one reported its 2028 date, 100 rows a page with the true match count. The manufacturer column shows the canonical name over its BC code (`manufacturer_alias`, which resolves all 15.958 items) and links to `/manufacturers/<name>`; an unmapped code has no page behind it and stays plain text |
| `/items/{item_ref}` | One item and every document linked to it, staged and production alike |
| `/documents` | Every document — staged, production, filed, rejected, superseded alike — filterable by type/status/cert number, with how many items each covers, 50 rows a page with the true total. A `staged` badge links to that document's review row (`/staging?doc=N`). Paged rather than capped, for the reason `/data-quality` was: it used to render `LIMIT 200` with no total and no controls, and the registry passed 200 documents while the page went on claiming to be the list |
| `/documents/{doc_id}` | One document: the items it covers, its evidence, its supersession chain. The cited certificate is named rather than numbered (its own type, cert number and expiry, since that expiry is a DoC's real renewal date), and a staged document links to its review row (`/staging?doc=N`). Provenance sits above the fold — manufacturer (read off the document's own evidence, not the catalogue), Basic UDI-DI, **where the file came from** (`source_url`) and **when we first recorded it** (`created_at`). A `source_url` is only rendered as a link when it is an http(s) URL; most are corpus paths from the supplier's SFTP drop, and an href on one of those resolves against this origin and 404s |
| `/manufacturers` | Canonical manufacturer entities: BC codes, item/published-document counts, and playbook coverage; search by name or code, `?no_playbook=1` to isolate entities with no authored playbook. The alias-source column left this list in P7b (2026-09-15): `manufacturer_alias.source` is per CODE, and the finer per-code answer reads under Technical details on `/manufacturers/{name}` |
| `/manufacturers/srn-queue` (linked from `/manufacturers/eudamed`, labelled **EUDAMED ID (SRN) queue** since P7b, 2026-09-15; the stored vocabulary below is unchanged) | The SRN confirm queue — `manufacturer_srn` rows at `status='pending'`: a fuzzy (not exact) name match between a EUDAMED `actorName` and one of our canonical manufacturers. `auto` rows (exact normalised match) never appear here at all — nothing to confirm. Each row carries **Confirm** / **Reject** buttons (`POST /manufacturers/srn-queue/{canonical_name}/{srn}`, `decision=confirm\|reject`), guarded by `AND status = 'pending'` so a double-submit or a stale form cannot flip an already-decided row. Confirming is the only way a fuzzy match ever becomes sweepable — **only `status IN ('auto','confirmed')` is ever swept**, because attribution is a name match and a wrong one mirrors another company's registered catalogue under our manufacturer's name. Registered above `/manufacturers/{canonical_name:path}` so its `:path` converter cannot swallow `srn-queue` as a canonical name |
| `/audit` (nav: **Decisions**) | The audit trail on a screen, added 2026-09-03 (W10). `audit_log` paginated on the `page+total` convention, filterable by `event` and searchable over `decided_by` / `item_ref` / `doc_id`. The event `<select>` is built from the table's own `DISTINCT event` -- the column is plain text with no CHECK, so a list mirrored in `web/app.py` would drift and would hide any event a new writer introduces. A row whose `via_job` no longer resolves still renders: invariant 10 makes the row self-contained (`job_snapshot` is `NOT NULL`), and the job link is a soft reference that may 404. Read-only; `dentalia_api` holds SELECT on `audit_log` and nothing more |
| `/reports`, `/reports/{name}` (nav: **Weekly reports**) | The weekly report as a durable file, added 2026-09-03. `report.weekly` has produced a real snapshot every week since S1.5 into `job.result`, where the most recent was readable at `/scheduler` and older ones nowhere. The handler now also writes `report-<period_key>.html` into `scheduler.report_dir` (compose: `/archive/reports`, the volume the worker writes and `web` mounts `:ro`), and these two routes index and serve them newest-first. `{name}` is matched against `_REPORT_NAME_RE` and re-resolved under the root -- a name, never a path, so this does not become a second `/archive/{path}`. Empty config makes the page say it is unconfigured rather than render an empty list. Nothing is emailed: spec 7.1 stands |
| `/coverage` (nav: **Coverage gaps**) | Which articles are missing which paperwork, added 2026-09-03 (W11). Three gaps counted apart over one `held` CTE -- no production document at all (8), none of type `DoC` (2.804), none under MDR/MDD (2.578), measured over the 4.265 `md_flag IS TRUE` articles on 2026-09-03 -- each with a paginated list carrying **what the article does hold**. Publishes NO percentage: the headline coverage number is `_kpi_board`'s and `[kpi-coverage-denominator]` owns its definition. The 11.693 unclassified articles are named on the page and excluded from every count. `_COVERAGE_GAPS` maps gap to predicate; an unknown `?gap=` falls back to `doc`. One scan by design: the correlated `NOT EXISTS` shape measured 665ms against 14,7ms |
| `/discovery` (nav: **Discovery**) | Which groups DISCOVER has looked at, added 2026-09-03 (W12). Three states over `discovery_log` -- never searched / searched and found nothing / searched and found something -- one row per `item_group`, ordered by medical-device items descending, paginated. Measured 2026-09-03: 8.119 of 8.208 groups have no `discovery_log` row and 4.256 of the 4.265 device items sit in one of them, which is the context the status board's coverage % lacks. The empty middle state is rendered rather than hidden: "found nothing" and "never looked" are different facts. An unknown `?state=` falls back to `never`. Read-only -- searching starts from the manufacturer page |
| `/manufacturers/eudamed` (nav: **EUDAMED**) | The cross-manufacturer digest, added 2026-09-03 (W7 in [officer-scenarios.md](officer-scenarios.md)). One row per manufacturer with any EUDAMED footprint: new devices and status changes since the last **reviewed** sweep, the declaration gap (`missing`/`staged`/`covered`), and `last_swept_at` where blank renders **never checked** rather than a zero. `registry.eudamed_digest` is the same four views the per-manufacturer card reads (`eudamed_gap_summary`, `eudamed_sweep_delta`, `eudamed_sweep_state`, `eudamed_sweep_due`) minus their `WHERE canonical_name`, so a gap has one definition. It also carries the same **Start the check** button on rows that are due; the gap request stays on the manufacturer's page. ~238ms over 27 swept manufacturers, measured 2026-09-03 |
| `/manufacturers/sweep-due` (linked from `/manufacturers/eudamed`, labelled **Check due** since P7b, 2026-09-15; its button reads **Start the check**, the stored vocabulary below is unchanged) | Manufacturers whose device sweep is due (`eudamed_sweep_state.due_at <= now()`) and not yet released — the scheduler's quarterly staleness tick writes `due_at`, nothing else. Each row's **Start the check** button (`POST /manufacturers/{canonical_name}/sweep`) enqueues `eudamed.sweep` and stamps `released_at`/`released_by`; refused with **400** when the manufacturer holds no trusted SRN at all (nothing to sweep, and releasing anyway would spend the `ec.europa.eu` round-trip for a guaranteed no-op). A double release on an already-queued sweep does not re-stamp the audit columns — `queue.enqueue`'s dedupe key (`eudamed.sweep:{canonical_name}`) returns `None` for the still-pending job, and re-stamping would overwrite the record of who actually released the run in flight. Also registered above the `:path` catch-all |
| `/manufacturers/{canonical_name}` | One entity: its BC codes (with the vendor-master name), items, production documents, and its linked playbook if one covers it. Below the completeness card, an **EUDAMED certificate findings** panel scoped to this manufacturer alone (`web/registry.py`'s `certificate_findings`, `canonical_name=` set) — see the `/expiry` row below for what it shows. Below that, the **declaration gap and sweep delta** (`declaration_gap_summary`): the Basic UDI-DI groups EUDAMED lists for this manufacturer with no article-level DoC (`eudamed_declaration_gap`), the covered/staged/missing/`not_in_eudamed` denominator (`eudamed_gap_summary`), and what changed since the last reviewed sweep (`eudamed_sweep_delta`) — a manufacturer never swept renders "never swept", not a zero-row "no gaps"; `swept_at` is read straight off `eudamed_sweep_state`, not inferred from row presence |
| `/playbooks` | The authored playbooks: slug, manufacturer, aliases, BC codes, domains, document-source count. Read from the database since slice 2 (2026-08-27), not the bind mount — which this image never carried anyway. One that fails to parse is never silently dropped: `load_playbooks` drops it by contract, `playbooks.slugs()` still counts it, and the difference is named in a visible warning here |
| `/playbooks/{slug}` | One playbook, and since slices 3a/3b the place it is **edited**. Tier A fields (contacts, domains, portal sources, date labels, type markers, source priority) and Tier B (direct sources, `skip_backfill` with a mandatory reason, `extract_hints` under a key allowlist and a length cap) are forms; Tier C (the regexes, `companion`, `coverage_map`, `exclude`, `match.anchors`, `ref_strategy`) renders read-only, by design. Every save requires a note, writes a `manufacturer_playbook_revision` row and bumps `playbook_rev`, which doubles as the optimistic lock -- a second save from a form opened before the first is refused at 422 rather than silently clobbering it. Revert writes the old body FORWARD as a new revision; nothing is ever rewritten. BC codes have their own add-only picker (`POST /playbooks/{slug}/codes`) over `vendor_master`, since 049 grants no DELETE. Identity edits are refused on a `BrandCollision` -- a name containing another BC brand the playbook does not claim |
| `/drafts`, `/drafts/{id}` | **Renewal mails waiting on a person.** The system never sends (client ruling 2026-08-20, spec §7.1): it drafts, you review, you send by hand from `mdr@dentalia.si`. One draft per manufacturer per cadence period listing every document expiring for that manufacturer (spec §7.2) — never one mail per document, which measured out at 52 mails to IVOCLAR for the single expiry date 2026-05-04. Detail page edits subject/body/recipients while `draft`, then `ready` records approval (and freezes the text — a person is about to send exactly that), `sent` records that they did, `cancelled` retires it. Nothing here queues a send because there is nothing downstream to queue. The chase's documents are listed worst-expiry-first. **This is the only web surface that writes outside `upload_inbox`** (migration 029: SELECT + UPDATE on `email_draft`, deliberately no INSERT/DELETE); invariant 1 is untouched and asserted in `tests/test_drafts_web.py`. Drafts are produced by `email.request` (**built 2026-08-20**, `app/handlers/email_request.py`), armed by the SCHEDULER expiry scan once `SCHEDULER_EXPIRY_EMAIL_ENABLED=true`, or by hand: `docker compose run --rm --no-deps worker python -m app.cli enqueue email.request 'email.request:manual:ACME' --payload '{"manufacturer":"ACME"}'`. Cadence and follow-up are `RENEWAL_REQUEST_CADENCE_DAYS` (7), `RENEWAL_REMINDER_AFTER_DAYS` (14) and `RENEWAL_ESCALATE_AFTER_REMINDERS` (3). **A draft is addressed only where `manufacturer.contact_emails` is populated** — seven on 2026-09-15 (3SHAPE, CARL MARTIN, DUERR DENTAL, EDENTA, HENRY SCHEIN, KOMET, VOCO — authored in the playbook `contacts` key and seeded into the column; CARL MARTIN's was found on the manufacturer's own website, not in a document), the other 377 are not; the seed itself writes no contacts, so until someone enters them on `/manufacturers/{name}` or in the playbook a person still fills the recipient in; the UI refuses to save a draft without one |
| `/expiry` | What has already lapsed and what is about to, as **three sections** rather than one horizon: expired in the last N days (default 180, `EXPIRED_LOOKBACK_DAYS` — already broken, so this is renewal work and it leads), expiring in the next N days (default 30, `EXPIRING_WINDOW_DAYS`, decision D7 of 2026-09-11, the same window the strip and the weekly report use), and long expired (collapsed, kept because a lapsed certificate still carrying production links is exposure we must be able to evidence). Windows via `?back=` / `?ahead=`; anything further ahead is counted and named rather than dropped. **One row per lapsing certificate** rather than per document citing it — 62 rows here were a single IVOCLAR certificate repeated across the 62 declarations that inherit its date, which is one renewal and one email. Each row carries the document count, **how many catalogue items lose production coverage**, and days left or days since. A DoC with no expiry of its own shows the date inherited from the certificate it cites (Task 4, `cert_doc_id`), marked `inherited`; a DoC with no expiry and no citation is listed five years after issue, marked `review`. Below the three lapse sections, an **EUDAMED certificate findings** card (migration 043's four views, read by `web/registry.py`'s `certificate_findings`): status alerts first (a certificate EUDAMED itself reports `withdrawn`/`cancelled`/`suspended`/`restricted` — a live change in standing, never a document request), then **revision drift** (a held certificate whose revision disagrees with EUDAMED's, both sides reported, neither resolved), near-miss possible matches from a looser spelling rule shown collapsed underneath and never joined, then **certificates we hold no copy of**. Closes with the denominator — `matched N of M` certificates held against EUDAMED's own listing, an `unattributable` count for EC/ISO documents that hold a `cert_number` but carry no production item link and so cannot be attributed to any manufacturer at all (invisible to every finding above, kept out of `matched`/`unmatched` on purpose — Ruling 19, 2026-08-26), and the mirror's own `EUDAMED synced <timestamp or "never">` freshness. The same panel, scoped to one manufacturer, renders on `/manufacturers/{canonical_name}` |
| `/inflight` | Documents currently moving through the pipeline, counted by distinct `content_hash` per stage — a different question from the System status board's job counts |
| `/scheduler` | **What the cron producer has actually done.** One row per cron -- ten of them as of 2026-09-07 (monthly ingest, expiry scan, failure monitor, coverage scan, weekly report, mailbox poll, two EUDAMED ticks, health watch, BC push drift), read off the `scheduler_run` ledger: the period it is currently in, the last period recorded, when that ran, and the job it enqueued. The verdict is *whether the current period is recorded* — `ok` / `due` (not yet, previous run recent) / `stale` (nothing for more than two of that cron's periods) / `never` — because a daily cron writes the ledger once a day however often it ticks, so freshness alone cannot prove the process is alive. Below the table, **the latest weekly report** rendered from its own `job.result` envelope (expiring / already lapsed / dead jobs, worst first) — it was being written and displayed nowhere. **Run now** is offered only for `report.weekly` and `email.poll`, the two ticks whose product IS a job, and enqueues under the scheduler's own dedupe key so a hand-run and the next tick collapse into one job; it never writes `scheduler_run` (that would make the scheduler skip the period you just helped with). `expiry-scan` and `failure-monitor` run their scans inside the tick itself — no job type exists for them and the enum is closed (invariant 7) — so they are labelled unrunnable rather than given a button with a 404 behind it. The **Armed** column says whether each cron still has a live `scheduler.tick` row, with a re-arm button when it does not: a cron that failed five times dead-letters, and `dead` sits outside the active-only dedupe index, so it stays stopped until a worker restart or that button revives it. The page shows the emission flags from **this container's** `SCHEDULER_*` env, so any such var must be declared on both the `web` and `worker` services in compose or the panel states a code default while the crons run on something else |
| `/bc-push` (on the **Queues & health** hub) | **Bulk writeback preview.** Every mirrored item whose three Business Central fields differ from the last value BC accepted, with the values that would be sent. Preview then apply, the `/import` shape -- but the preview is a **query, not a spool**: nothing is stored between the two presses, because a plan stored now and applied ten minutes later writes an answer the registry has already moved past. Apply enqueues `bc.push` in batches; it does not write. While `bc.write_enabled` is false the page still lists what would go, and nothing is sent |
| `POST /items/{item_ref}/bc-push` | The same write for one item, a button on `/items/{item_ref}`. Deduped per item per day, so a second click the same day is a no-op rather than a second job |
| `/data-quality` | Standing ledger of catalogue anomalies (`data_anomaly`, migration 019), filterable by kind, 50 rows a page. `subject` is an `item_ref` for the catalogue kinds (19.141 of 19.147 rows) and a `content_hash` for `no_text_layer` (6); whichever resolves decides where it links and whose name shows beside it, and a subject resolving to neither stays plain text. Paged rather than capped: it used to render `LIMIT 500` with no total and no controls, which reads as "this is the list". A second block, **Device class vs BC** (`item_class_check`, migration 033), puts BC's `product_class` beside the class the item's production documents state: `fillable` is a blank BC class a held document could fill, `conflict` is one the document contradicts, `agree-family` is BC's `Is`/`Im`/`Ir` against a document's less specific `I` and is not a disagreement (documents almost never print the class I subclasses — 10 of 768). All four verdicts are counted; only `conflict` and `fillable` are listed by item, capped at 50 with the true total printed beside it. **BC stays the source of truth** — this is evidence for a person, and nothing writes `item_mirror.product_class` |
| `/ingest` | Enqueue an `ingest.run` (csv or bc_odata source, catalogue, priority), plus **Recent runs**: the last 25 jobs that finished carrying a result envelope (`job.result`, migration 019), with finish time, duration and the handler's own numbers. Not filtered to `ingest.run` deliberately — no import has run on this database yet (the catalogue arrived by backfill), so an ingest-only table would be empty while the work that does run stayed invisible |
| `/staging` | The review queue, **searchable** over the same fields as `/documents` (manufacturer as printed, filename, cert number, Basic UDI-DI, doc id) — 130 staged rows is past what anyone scans by eye. Written for compliance staff rather than the pipeline team: each row says in plain words what the document is and why it is waiting (type codes spelled out, VALIDATE flags rendered as sentences), and offers **Open** and nothing else: since 2026-09-11 (office UI redesign, decision D5) Approve, Reject and the manufacturer binding render only in the expanded panel, next to the document, so nothing is decided from a summary line. Since the same redesign's P4 (spec § 6) the list is **grouped by manufacturer** -- the document's confirmed manufacturer (`document.canonical_manufacturer`), else its linked items' -- groups and rows oldest first, "Manufacturer not known" last, each header counting its whole group under the current filter; a row reads "⟨MANUFACTURER⟩ · ⟨type word⟩ (⟨regulation⟩)" over "⟨file name⟩ · issued ⟨date⟩ · waiting since ⟨date⟩", the file name being the last segment of `source_url` (the archive's own name for an `email:` source). Six **reason chips** filter on the server (`REVIEW_REASON_CHIPS`): every review reason code maps to one chip through `REASON_CODE_CHIP`, held against GATE's three flag tuples by `tests/test_web_review_context.py`, and a code nobody mapped lands in Other; Expired is `validity_to` before today, and alone among the six it carries no `NOT is_mfr_binding`: it asks a date question, not a reason question, so it crosses all the others and an expired whole-range candidate is under both **Covers a whole range** and **Expired** (deliberate, fix round 2). The heading counts the WHOLE queue whatever the filter and labels itself so ("234 waiting in all"); a filter in force says above the list how much of the queue it left, because the group headers and the pager count only that. The list is one set-based query (`_REVIEW_ROWS`), not per-row subqueries: grouping needs every staged document's manufacturer before the page is cut. Expanding shows **page 1 of the PDF in an iframe** of `/documents/{id}/file` beside the facts in words (manufacturer as printed, rules, issued, valid until -- "Not stated" when absent -- Basic UDI-DI), with **Open full size** for a new tab; the list itself carries no iframe and loads no PDF bytes. Above the buttons the panel lists the items an approval makes the document count for (first 10, then "+ K more") and how they were matched, and the button names the count ("Approve for these N items"). Only links on a publishing basis are promised (`PUBLISHING_BASES`, a mirror of gate's `TRUSTED_BASES`): a `fetch-context` / `name-family` / `ref-catalogue` link stays staged on approval, so it is listed apart as not counting yet, and a document with only such links offers a plain **Approve** -- 53 of 234 waiting documents on dev on 2026-09-11. **Correct a fact first** holds the optional correction form with ordinary date/text/select inputs, and a whole-range panel does not offer it, because `gate.apply`'s bind path drops edits; everything engineer-facing (evidence tiers, hashes, raw flags) is folded into "Technical details". A **manufacturer-scope candidate the catalogue cannot name** (no item links to derive one from, `{"manufacturer": null}` in its task payload — 14 of 52 open rows) gets a picker in the expanded panel: it offers every manufacturer in the catalogue (since 2026-08-31), each with the number of medical-device items a binding would link, zero included, since migration 053 records a binding that links nothing. The count is `_mfr_bind_counts`, the same one GATE's `item_codes_for` fan-out produces and the same one the button and the confirm quote. The one the document names for itself is preselected when it resolves unambiguously. The printed name is now also SHOWN on any row the catalogue cannot name (marked "read off the document") — CARL MARTIN 818 read "not known" while `evidence` held `Carl Martin GmbH` at page 1, T2, conf 0.98 — but it is display only: it never fills the bind form and never removes the picker, because a name a model read is not a decision to write links across a manufacturer's whole catalogue. The approve button names the consequence, not the verb ("Approve for all IVOCLAR products (1069 items)"), and pressing it writes nothing: the server answers with a confirm in the panel ("Approve for all 1069 IVOCLAR items?", the count from the same `_mfr_bind_counts` that fills the button, and the document named by its own type, "This EC certificate says…") and only its **Yes, approve for 1069 items**, which posts `confirm=1`, enqueues and replaces the row (`HX-Retarget`), as Approve and Reject do. A name that resolves to no BC code at all is refused at the first step (422), since GATE would refuse the bind; changing the picker withdraws a confirm already on screen. **Reject…** asks why: one of six fixed reasons (`REJECT_REASONS`) plus an optional note of at most 500 characters (`REJECT_NOTE_MAX`), sent on `gate.apply` as `note` and shown on `/audit`. A reject without a reason, or with a longer note, is a 422 and enqueues nothing. The correction fields belong to a form of their own (`form=` attribute, sent with `hx-include`), so Enter in one of them cannot submit the approval. Also **grouping suggestions** (RESOLVE's T1 staging path) -> `resolve.group`. Both sections paginate at 50 rows and render collapsed — inline rendering was 7.8 MiB of HTML at 5.8k open suggestions and no browser would open the page. `?doc=N` is the deep link other screens use to hand a reviewer one row (`/documents/N`, `/manual`): it resolves which page holds that document, renders it already expanded, and pairs with the `#doc-N` anchor for the scroll. The anchor alone is not enough — the board renders page 0, so a bare `/staging#doc-N` silently resolves to nothing for every document past the first 50 (it did for 8 of the 13 open manual tasks). A document decided since the link was made renders the board plus a "no longer awaiting review" notice, never a silent page 0. Decisions record `user:admin` unless a proxy supplies an identity (`DEFAULT_DECIDED_BY`, one line to change when logins land). Below both queues sits **Staged links on production documents** — the C17 surface, and the one thing on this page that is NOT a document decision. Approving a document publishes only its trusted-basis links; a `fetch-context` / `name-family` / `ref-catalogue` link is capped at staged by a database CHECK and no approval will ever lift it, so each row carries its own **Covers this item / Does not** buttons. Confirming re-bases the link to `manual` and publishes it; refusing marks it `rejected`, which machines cannot undo (only **Re-open** can). Below that again, and deliberately formless, **Staged links on unpublished documents**: links whose document was superseded or filed without ever reaching production. Nothing there is waiting on anyone — it is listed so terminal dead state is legible rather than merely absent |
| `/documents/{doc_id}/file` | The archived PDF. **Always link here, never to `document.archive_url`** — that column is a storage handle (a host path for backfilled corpus documents, a `file://` URI in local dev), so rendering it into an href produces a dead link, which is what every "file" link in the UI used to be. Resolution order: the stored path as-is — the normal path now that `backfill.scan` archives through the StorageAdapter and `archive_url` is a uniform `/archive/{brand}/{type}/{hash12}__{name}.pdf` — then `WEB_PATH_REWRITES`, then a search of path tails under `WEB_ARCHIVE_ROOT`/`WEB_IMPORTS_DIR` accepted only when the file's sha256 matches the registry's `content_hash`. The last two are a safety net for rows written before that fix, not the working path. Served `inline` (clicking opens the PDF; `attachment` downloaded a fresh copy per click) and the archive's `{hash12}__` prefix is stripped from the download name, since manufacturers identify documents by their own filenames. Redirects instead of proxying when `storage.base_url` makes the archive already client-reachable. A served file carries `Cache-Control: private, max-age=31536000, immutable` (`DOCUMENT_FILE_CACHE`, 2026-09-11): the bytes behind a doc_id never change, and the Review panel's iframe re-reads the file every time a row is opened, which without it downloaded the whole PDF again each time. Starlette's range answers (206) copy the header; a 404 carries none |
| `/staging/docs?page=&q=&reason=`, `/staging/suggestions?page=&q=` | HTMX partials: one page of summary rows for each section. `reason` is a reason chip (`REVIEW_REASON_CHIPS`); an unknown value is all reasons. `q` filters suggestions by `item_ref` — with thousands open, prev/next alone cannot reach a specific item. Also usable by hand to check what a page holds without loading the board |
| `/staging/{doc_id}/detail`, `/staging/suggestion/{id}/detail` | HTMX partials: the expanded body of one row, including its decision form. 404 once the row is decided or resolved (someone got there first in another tab) |
| `POST /links/{doc_id}/decide` | C17 link decisions: `confirm-link` / `reject-link` / `reopen-link` on one `(doc_id, item_ref)` pair, `item_ref` as a form field (catalogue refs contain literal slashes). Enqueues `gate.apply` at interactive priority and writes nothing — the handler owns every precondition and raises where it is not met, so a stale button on an already-decided row dead-letters visibly instead of silently doing nothing. Dedupe key `apply:{doc_id}:{item_ref}:{decision}`: two items under one document are two decisions and must not collapse. Reachable from `/staging` and from `/items/{item_ref}`'s **Decide** column |
| `/manual` | Open `manual_task` rows; **searchable** by the document a task is about (manufacturer as printed, filename, cert number, Basic UDI-DI, doc id) — a `discovery-dead-end` task is about a group rather than a document and matches only on its payload; `discovery-dead-end` tasks resolve here (re-enqueues `discover.group`) or via an "Upload document" link to `/upload` (prefilled group + task id); `gate-manual` resolves via Staging |
| `/missing` (added 2026-09-11, office UI redesign spec § 5) | **Missing documents**, the office's half of `/manual`: open `discovery-dead-end` tasks only, oldest first, 20 a page (`?page=`), filterable by `?manufacturer=` with chips for the six manufacturers holding the most and the rest behind "+ K more". `gate-manual` and `dead-job-followup` are not listed; `/manual` keeps all three. Each card: the task's `label`, the group's canonical manufacturer, its item numbers from `item_group_member`, and "Searched … on ⟨date⟩. Nothing found." read off the group's LAST run in `discovery_log` (the rows sharing the `at` of its latest `manual` row; one run is one transaction), not off `sources_tried`, which also lists rungs that skipped or had nothing to look at. Only two rungs can be named: `search` (a hit or miss row) and `playbook` (a crawl row with `pages_fetched` >= 1); `recency`, `known_url` and `eudamed` are never named, because their rows cannot show they looked at anything (`web/missing.py::NEVER_NAMED`), and a run that proves no place reads "Nothing found on ⟨date⟩." A repeat search that also finds nothing moves the date and the places. Actions: **Upload what I found** (`/upload?group_id=&manual_task_id=`), **⟨Manufacturer⟩ downloads ↗** and **Search the web ↗** from `prefilled_search_links` (split by host; the playbook's first `portal` source when the payload has none), and **Search again**, which is `POST /manual/{id}/resolve`; its receipt says the card leaves the list as soon as the search finds something to try, before the document is checked, because DISCOVER closes the task on any rung hit. Read-only: the page closes nothing; DISCOVER closes a task when a search finds something, `upload.ingest` when an upload is taken in. `missing_summary(conn)` gives the count, the top three manufacturers and the oldest date for Today |
| Refetch one item | An admin can re-run discovery for a single item's group with the recency window bypassed ([admin-refetch-item], added 2026-08-24): the "Re-discover documents" button on `/items/{item_ref}` (`POST .../rediscover`), or `python -m app.cli discover-item <item_ref> --force` from the shell. Both enqueue `discover.group` at `interactive` priority, dedupe `discover:refetch:{group_id}:{date}` (same-day repeat is a no-op) — `--force`/the button set `ignore_recency: true`; without `--force` the CLI does an ordinary re-run. The flag only bypasses the recency-window checks — conditional GET and content-hash dedupe still run, so a forced refetch of unchanged bytes links rather than duplicating a document |
| `/upload` | Spool a PDF (`GET` form / `POST` submit) -> `upload_inbox` row -> enqueue `upload.ingest`. PDF-only, size-capped at `UPLOAD_MAX_MB`; optional `target_group_id` / `manual_task_id` (the latter resolved worker-side, never by this route). Since 2026-09-11 the form has no "Target group id" or "Priority" field: opened as `/upload?group_id=&manual_task_id=` (from `/missing` or `/manual`) it names the item ("Uploading a document for ⟨label⟩ (⟨manufacturer⟩, item ⟨ref⟩)") and carries both ids as hidden inputs, the group being the task's own whenever the task resolves (not the URL's, which nobody can see to correct), and the priority is the route's default, `interactive`. The POST still accepts `priority` |
| `/import` (added 2026-08-25, BC import upload slice A) | Spool a BC catalogue export (`GET` form / `POST` submit, accepts `.xlsx`/`.xlsm`/`.xls`/`.csv`, size-capped at `UPLOAD_MAX_MB`) -> `import_inbox` row -> enqueue `ingest.run` with `source: "upload"`, `dry_run: true` — a **preview**, never a write. **No catalogue picker**: there is one Business Central (Denis 2026-08-19, closing G17/G11). This route never offered one, and since 2026-08-26 neither does `/ingest` or `/upload` — all three take the catalogue from `scheduler.ingest_catalogue`. `/import/{job_id}/preview` renders the finished dry run's diff (`job.result`) and an Apply button, shown only once the job is a finished upload dry run. `POST /import/{job_id}/apply` enqueues a **second** `ingest.run` job carrying the same payload with `dry_run: false` and `preview_job_id` set to the preview it was confirmed from (invariant 9 — the operator's confirmation is a new job, never a payload mutated in place) under dedupe key `ingest.run:upload:{upload_id}:apply`; the applied run's page reads that back and renders **Previewed against Applied** as two columns, flagging any counter that moved between the two runs — the write re-diffs against the catalogue as it stands at write time, so they are allowed to disagree, and a disagreement must be visible rather than assumed away; a double-clicked Apply dedupes and `queue.enqueue`'s `None` return is guarded before linking onward, rather than rendering `/import/None/preview` |
| `POST /import/vendors` (added 2026-08-26, BC vendor import upload slice B) | The second section of the same `/import` page, for `Proizvajalci.xlsx` -> `vendor_master`. Same spool (`import_inbox` at `kind='vendors'`, no schema change — 040 already CHECKs it), same two-phase preview-then-apply, a different queue tag: **`vendor.import`** (migration 041). Payload is flat — `{upload_id, filename, dry_run, allow_renames, preview_job_id?}` — not nested under `ref`, which is `ingest.run`'s shape. The preview page reports four categories (new codes / renamed / gone from the file / unchanged) previewed against applied, and renders the renamed triples and the absent codes in full rather than as counts. **A rename needs a checkbox.** `vendor_master.apply` refuses all-or-nothing unless `allow_renames` is set, because `item_group.canonical_manufacturer` is effectively write-once (RESOLVE's ladder short-circuits on `_existing_link`), so re-pointing a code after its items are grouped splits that manufacturer with no repair path. The refusal is **not** a failed job: the handler catches it, reports `outcome: "rename-refused"` with the offending codes, writes nothing and leaves the spool row, so the same apply can be re-run with the box ticked. A code absent from the file is **kept** with a stale `import_batch`, never deleted |
| `/dead` (nav: **Failed**) | Dead-lettered jobs **split into three sections by what a person can do** (`web/failures.py`, office UI redesign spec § 4, 2026-09-11): **Websites that took too long** (a timeout on `fetch.url`, `discover.group` or `playbook.reonboard`), **Addresses that no longer work** (`fetch.url` with HTTP 404/410 or a host name that does not resolve, sub-grouped "Page no longer exists" / "Website not found" with the first four domains each) and **For the developer** (everything else). **Only what still needs someone counts**: a dead job drops out once a job with the same dedupe key and a higher id exists (for `fetch.url`, one carrying the same `group_id`, because its key is the URL alone and another group's fetch of the same address is not work for this one), or, for a dead address, once a `discover.group` for its `group_id` was created after it died; while that newer work is active it shows as "trying again" / "searching again", and once a newer job has finished that job speaks for it. `POST /dead/retry-timeouts` re-enqueues every counted timeout (same type, payload and dedupe key, interactive); `POST /dead/search-again` enqueues one `discover.group` per distinct `group_id` among the counted dead addresses, dedupe key `discover:failed:{group_id}`, interactive. Both re-derive their set server-side from the same split and answer with a receipt in words. The developer section keeps the older view one click down, under **Show technical details**: groups by (type, first 120 chars of `last_error`), each with its count, the representative error, **Re-run all N** at interactive priority, and its jobs in a disclosure with their own re-run and a link to `/jobs/{id}`. Those re-run buttons render for operators only (`web.operator_users`, D6; empty = everyone). `/dead/rerun-group` re-derives its group from the developer section rather than trusting an id list, so an already-re-run row is never re-run twice. Dead rows are never mutated — a re-run is a new job beside them (invariant 8) |
| `/archive/{path}` | Serve an archived document from `Web.archive_root` (the `archive_data` volume, mounted read-only on `web`); 404 outside the root or on a missing file |
| `/documents/{content_hash}/text` | Plain text of what EXTRACT read out of that PDF (`document_text`, migration 018); a placeholder line if the document is a scan, 404 if no row exists |
| `/item/{item_ref}?k=` | **The BC item-card link.** One item and its production documents, for a caller that is not a staff browser: a support rep clicking the computed hyperlink from BC gets a standalone page (manufacturer identity first, since that is what is printed on the box), the webshop backend gets the same payload as JSON on `Accept: application/json` or `?format=json` — BC's HTTP client is not guaranteed to send a useful `Accept`, and a rep must never land on a raw JSON blob. `&view=customer` narrows it to what the shop may render to a clinic (drops `manufacturer_code`, `match_basis`, `expiry_basis` — `docs/specs/read-api.md`). The key is `WEB_BC_LINK_KEY`, shared and static because a BC hyperlink is a computed field built by string concatenation and cannot compute a signature; a wrong or missing key is a **404, never a 403**, since a 403 confirms the item exists. Reachable by the `bc`, `service` and `staff` classes only. Staff browsing the registry use `/items/{item_ref}` instead |
| `/item/{item_ref}/documents.zip?k=` | Every production document for that item in one archive — the "papers" a client asks for on the phone are plural. Built in memory; a document whose bytes are unreachable is skipped, and an item with none is a 404 rather than an empty zip. Registered *before* `/item/{item_ref}` because the `:path` converter is greedy |
| `/api-reference` (nav: **Developer → API**) | What this app exposes, to whom, and with which credential. The three caller classes (`staff` Basic auth, `service` `X-API-Key`, `bc` `?k=`), which paths each may reach, and an endpoint table — with **live example links built from an item_ref read out of the database**, so a documented example cannot drift into 404ing. Links out to FastAPI's generated `/docs` and `/openapi.json`, which answer "what are the parameters" but not "which of these may the webshop call" |
| `/api/items/{item_ref}/documents` | JSON: the item's identity (`item_name`, `manufacturer`, `manufacturer_code`, `mfr_ref` — all null for an unmirrored item) plus its production document links. `?view=customer` narrows it to the customer-safe projection; `?include_superseded=true` widens the ROW SET to the documents this item's paperwork replaced, adding `status` and `superseded_by` (migration 070). Both default to today's behaviour. The two cannot be combined — `400`, because a superseded document must never reach a clinic as coverage. Shares one implementation with `/item/{item_ref}` (`web/item_docs.py`) so the two cannot disagree about what an item's paperwork is |
| `/api/documents/{doc_id}` | JSON: a production **or superseded** document + its evidence. Superseded since 2026-09-16, so the `doc_id`s `?include_superseded` hands back are fetchable; staged, rejected and filed stay `404` |
| `/api/kpi` | JSON: the same numbers as the `/status` KPI board |
| `/healthz` | Liveness — the one route Caddy leaves unauthenticated |

`docker compose up -d` does NOT rebuild a changed image. After editing `web/`
or `app/`, use `docker compose up -d --build web`, or the container keeps
serving the old code — a stale `dentalia-web` image will 404 on routes that
exist in the source while still reporting healthy.

**The same is true of `worker`, and it is easier to miss.** Neither `worker`
nor `web` bind-mounts the source: `worker` mounts only `archive_data` and
`/imports`, and both images COPY `app/` at build time. Only the `test` service
sees the working tree, which is why the suite can go green on a change the
running pipeline does not have. Rebuild with `docker compose up -d --build
worker`.

Missing this is silent rather than loud — a worker running old code reports
healthy, claims jobs, and finishes them `done`, because the healthcheck is
liveness and never looks at the code or the config. Hit live 2026-09-02: job
35701 (`eudamed.sweep`, IVOCLAR) ran against a worker built before that
morning's empty-sweep guard, so it took the pre-guard path and stamped itself
swept after storing nothing. To check what a running worker actually has,
grep the container rather than the tree:

```
docker compose exec worker grep -c empty_sweep /app/app/handlers/eudamed.py
```

**`migrate` is the same trap with a worse failure mode.** It runs off the same
`dentalia-app` image and therefore the same COPYed `migrations/`, so a migration
added since the last build is not in the container at all — and the runner's
answer to "nothing pending" is the same `up to date` as a genuinely current
database. It exits 0 and the stack comes up on a schema that is missing a
table, a column or a view. Hit live 2026-09-16: the image held nothing past
`068`, so `docker compose up migrate` printed `up to date` while `070` was
unapplied and the route that needed the new view would have 500'd.

Check the database, never the exit code:

```
docker compose exec -T postgres psql -U dentalia -d dentalia \
  -c "SELECT filename FROM schema_migrations ORDER BY filename DESC LIMIT 3;"
ls migrations | tail -3      # the two lists must agree
```

Two ways out. Rebuild, which is correct but slow (`docker compose build migrate
&& docker compose up migrate`); or run the migration from the `test` container,
which is the one service that bind-mounts the working tree, and so is always
current without a build:

```
docker compose exec -T test python -m app.cli migrate
```

Same code path, same bookkeeping row, same `DATABASE_URL` — the test service
points at the dev database, not the per-worker test ones the suite creates.

### EUDAMED certificate register and sweep operations (S2.3 stage 2)

**EUDAMED holds zero declarations of conformity.** Everything below is a
request list or an alert for a person, never a document and never a registry
write — nothing here touches `document` / `item_document` / `evidence`.

- **Releasing a sweep.** Go to `/manufacturers/sweep-due`, find the row, click
  **Start the check** (the screen word for this since P7b; the stored tag is
  still `eudamed.sweep`). That enqueues `eudamed.sweep` and stamps who released
  it and when. The scheduler only ever *proposes* a sweep (writes `due_at` on a
  quarterly staleness check) — it never enqueues one itself, so a manufacturer
  can sit on this list indefinitely with no automatic consequence. The button
  is refused with a 400 if the manufacturer holds no trusted SRN at all (see
  the confirm queue below) — there is nothing to sweep, and releasing anyway
  would spend a real network round-trip on a guaranteed no-op.
- **A full sweep is not instant.** It runs as one job that calls
  `ec.europa.eu` sequentially, page by page, for every trusted SRN the
  manufacturer holds — Ivoclar is ~38 pages at 300 devices each, Carl Martin
  12. Expect a full Ivoclar sweep to occupy that job, and hold the same host
  the certificate register pull and the per-document `eudamed.sync` button
  also talk to, for **several minutes**. Releasing several large sweeps back
  to back, or releasing one while the monthly certificate register pull is
  scheduled, queues real wall-clock time against the same external,
  unofficial API — not a registry risk, just something to expect rather than
  interpret as the button having done nothing.
- **Clearing the SRN confirm queue.** Go to `/manufacturers/srn-queue`. Every
  row there is a *fuzzy* name match between a EUDAMED `actorName` and one of
  our canonical manufacturers — an exact normalised match never appears here,
  it is written `auto` and swept without asking. Read the two names side by
  side and **Confirm** or **Reject**. Confirming is the only way a fuzzy match
  ever becomes sweepable: only `manufacturer_srn` rows at
  `status IN ('auto', 'confirmed')` are ever read by a sweep or a finding
  view, because a wrong confirmation mirrors another company's registered
  catalogue under our manufacturer's name.
- **What a certificate status alert means.** The certificate findings panel
  (`/expiry` and each manufacturer's page) leads with **status alerts** —
  a certificate EUDAMED itself now reports `withdrawn`, `cancelled`,
  `suspended` or `restricted`. This is **not** a document request: we may
  already hold a perfectly good copy of that certificate on file. It is a
  change in the certificate's own standing, asserted by EUDAMED itself rather
  than read off a PDF, and it is worth a person's attention regardless of
  whether the document on file needs replacing.
- **What the drift and gap findings actually reach.** The certificate views
  are scoped to `EC`/`ISO` documents only — a declaration of conformity that
  merely *cites* a certificate number is never counted as holding it. Of the
  43 EC/ISO certificates we hold that carry a certificate number, 34 have no
  production `item_document` link and so cannot be attributed to any
  manufacturer at all (`unattributable` — a data-quality finding in its own
  right, distinct from anything EUDAMED reports); the remaining 9 are what
  `certificate_drift` and `certificate_gap` actually see. A raw
  certificate-number string match against the whole corpus reaches many more
  documents than that, but most of them are declarations these views were
  never scoped to cover.

## CLI

`python -m app.cli`, inside any app-image container:

```bash
docker compose run --rm worker python -m app.cli inspect --limit 20
docker compose run --rm worker python -m app.cli migrate
```

### Does this database match the tree? (`schema-drift`)

```bash
docker compose exec -T worker python -m app.cli schema-drift
```

Builds the tree's schema in a scratch database, compares columns, views,
indexes, constraints, enums and grants object by object, drops the scratch
database, and exits 0 when they agree. Takes about 10 seconds.

**Run it after every deploy, and after correcting any applied migration.** The
test suite cannot answer this question — it builds the schema from scratch on
every run, so a database that has drifted stays green. That is not theoretical:
`043_eudamed_cert_views.sql` was applied 2026-08-26 and edited in place the
next day; because `schema_migrations` is keyed on filename the edit never
re-ran, and this database served the pre-fix views for 8 days while the test
pinning the corrected shape passed throughout. `/expiry` was serving 1.43 MiB
of duplicated rows as a result. Repaired by `058_cert_view_drift_repair.sql`.

Output names each object and how it differs: `changed` (same name, different
definition — the 043 case), `missing` (the tree has it, this database does
not), `extra` (this database has it and the tree does not — a hand-run
statement in psql, which is how three stray `tmp_*` tables were found here).
`--keep` leaves the scratch database for inspection.

It also reports, **advisory and never fatal**, any migration this database
applied at a different position than a fresh one would. Measured here
2026-09-03: 11 of them, in four inversions (`014_upload` after
`015_supersession`; `032`/`033` after `034`; `041_vendor_import` after
`042`/`043`; `053` after `054`/`055`), the routine cost of parallel branches
landing numbers below master's maximum. This cannot be fixed — you cannot
re-apply in a different order — and it is reported because it means the two
databases took different paths, which is exactly why the schema comparison
above is worth running. `app/db.py`'s `_out_of_order` is the matching
*prospective* guard: it warns before applying a pending file that sorts below
one already applied, and refuses under `strict=True` (conftest and CI).

### Migration ordering: what the `migrate` warning means

`migrate` prints a `WARNING: migration(s) X sort below Y` when a pending file
sorts below one this database has already applied. It still applies them --
warn, not refuse, because a branch landing a lower number is the routine cost
of parallel sessions here (Denis, 2026-08-27: "we fix it when merging") and it
is fixed at merge time, which is exactly when `migrate` still has to run.

What it is telling you: **a fresh database will apply those files in a
different relative order than this one did.** That is the one divergence a
numbered-migration convention exists to prevent. Usually harmless -- the two
`041`s are independent `ALTER TYPE job_type ADD VALUE` statements -- but it is
never harmless *by construction*, so read the pair and decide.

It is not a duplicate-number check, and duplicate numbers are not the point.
Measured on the dev database 2026-08-27, three episodes were already present:
`014_upload` after `015_supersession`, `041_vendor_import` after
`043_eudamed_cert_views`, and -- with no collision anywhere in sight --
`032_stated_class` + `033_item_class_check` after
`034_item_document_production_expiry`. A prefix check would have missed the
third and, since 18 of the repo's 20 branches carry the unrenameable
`014_evidence_rev` / `014_upload` pair, would have refused to run at all.

`db.migrate(..., strict=True)` turns the warning into `OutOfOrderMigration` and
refuses **before applying anything**. Use it where a database cannot
legitimately be out of order -- one built from scratch. `tests/conftest.py`
does exactly that, so CI fails loudly on an out-of-order file; see the whole
argument in `tests/test_db.py`'s module docstring.

To see the current state, compare applied order against disk order:

```sql
WITH applied AS (SELECT filename, row_number() OVER (ORDER BY applied_at, filename) rn
                 FROM schema_migrations),
     disk    AS (SELECT filename, row_number() OVER (ORDER BY filename) rn
                 FROM schema_migrations)
SELECT a.filename, a.rn AS applied_pos, d.rn AS disk_pos
FROM applied a JOIN disk d USING (filename) WHERE a.rn <> d.rn ORDER BY a.rn;
```

```bash
docker compose run --rm worker python -m app.cli enqueue backfill.scan scan:gc \
    --payload '{"drive_folder": "/imports/dentalia-sftp/GC"}' --priority interactive
docker compose run --rm worker python -m app.cli playbooks validate
docker compose run --rm worker python -m app.cli playbooks reconcile --export "imports/Artikli 3.7.2026.xlsx"
docker compose run --rm worker python -m app.cli playbooks sync
docker compose run --rm worker python -m app.cli playbooks drift     # report only
docker compose run --rm worker python -m app.cli playbooks drift --apply
docker compose run --rm worker python -m app.cli vendor-master            # dry run
docker compose run --rm worker python -m app.cli vendor-master --apply
docker compose run --rm worker python -m app.cli manufacturers seed            # dry run
docker compose run --rm worker python -m app.cli manufacturers seed --apply
docker compose run --rm worker python -m app.cli regroup --manufacturer IVOCLAR   # dry run
docker compose run --rm worker python -m app.cli regroup --manufacturer IVOCLAR --apply
docker compose run --rm worker python -m app.cli mine-srn                 # dry run
docker compose run --rm worker python -m app.cli mine-srn --apply
docker compose run --rm worker python -m app.cli mine-contacts            # dry run
docker compose run --rm worker python -m app.cli mine-contacts --apply
docker compose run --rm worker python -m app.cli repair-ref-list           # dry run
docker compose run --rm worker python -m app.cli repair-ref-list --apply
docker compose run --rm worker python -m app.cli repair-stated-class       # dry run
docker compose run --rm worker python -m app.cli repair-stated-class --apply
docker compose run --rm worker python -m app.cli repair-mfr-overbind 310   # dry run
docker compose run --rm worker python -m app.cli repair-mfr-overbind 310 --apply
docker compose run --rm worker python -m app.cli repair-document-manufacturer         # dry run
docker compose run --rm worker python -m app.cli repair-document-manufacturer --apply
docker compose run --rm worker python -m app.cli repair-mfr-task-reason    # dry run
docker compose run --rm worker python -m app.cli repair-mfr-task-reason --apply
docker compose run --rm worker python -m app.cli komet-coverage            # dry run
docker compose run --rm worker python -m app.cli komet-coverage --apply
```

`enqueue` takes a queue tag (must be a registered `job_type`), an active-scope dedupe key, `--payload` (JSON object), and `--priority {interactive,delta,sweep}` (default sweep).

`backfill.scan`'s payload key is **`drive_folder`**, and the path is the one the *worker container* sees (`/imports/...`, the read-only corpus mount) — not a host path. Scan one brand folder per job rather than the corpus root: cost and failure isolate per brand, and a brand you are not ready for (an unauthored playbook, say) is simply never enqueued. A folder that does not exist, or holds no PDFs, now **fails the job** rather than reporting `scanned: 0` — that number used to be indistinguishable from a corpus already fully ingested, which is exactly how a wrong path stayed invisible. Zero *emitted* is still success: that is the two-cycle property.

**Not every file in a corpus folder is a document, and not every folder is worth scanning.** Two playbook keys cover this. `skip_backfill` refuses the whole dump: `backfill.scan` raises and the job lands in dead-jobs, checked before the folder is even required to exist, so a disowned dump is refused on identity rather than on anything found inside it. **NEODENT carries one** (client ruling 2026-08-19: the folder is a mess, skip it) and is the only manufacturer that does. `exclude` is the narrower tool for a dump that is otherwise sound: it drops the files its patterns name before hashing, archiving or ledgering them, and reports per-pattern hit counts as `excluded_by` on the job result. Check that number after a scan, since a pattern firing more often than the folder holds invoices is eating declarations too; a rule that excludes every PDF fails the job, for the same reason an empty folder does. No playbook authors an `exclude` rule today. Every corpus other than NEODENT (GC, IVOCLAR, KOMET, DENTSPLY, VOCO, STRAUMANN) holds compliance documents only and is scanned unfiltered.

The archive buckets under **the scanned folder's own name**, so `/imports/dentalia-sftp/GC` files to `/archive/GC/...`. This follows the one-brand-per-job rule above and is why that rule is not optional: the bucket used to be the first path component *below* the scan root, which turned a per-brand scan into `/archive/DOC/...` and `/archive/MSDS/...` (GC's internal subfolders) with the manufacturer nowhere in the path. Pointing a scan at the corpus root instead buckets everything under that root's name.

### Mirroring the site publication (`scripts/mirror-mdr-site.py`)

The client publishes the same documents the SFTP drop holds at
[www.dentalia.si/mdr-dokumentacija](https://www.dentalia.si/mdr-dokumentacija/).
The page is an empty JS shell; the tree comes from one public JSON endpoint,
`GET /api/data`, and every file is a plain `GET /api/files/<BRAND>/...` carrying
`ETag` and `Last-Modified` with the **source file's own mtime preserved**
(measured 2026-09-21: dates span 2008 to three weeks prior). Read 2026-09-21:
**76 brand folders, 2771 files, 2721 of them PDF, about 2 GB.**

That is a better corpus source than SFTP, and the script is why the import path
needs nothing new: it lands the tree in the shape `backfill.scan` already walks,
one directory per manufacturer.

```bash
./scripts/mirror-mdr-site.py --dry-run              # inventory, one request
./scripts/mirror-mdr-site.py                        # first run, ~2 GB
./scripts/mirror-mdr-site.py --brand GC -v          # one brand, per-file lines
```

- **Runs on the host, not in a container.** The worker mounts `/imports`
  read-only on purpose. Stdlib only, so no image rebuild and no dependency.
- **Destination** is `$IMPORTS_HOST/dentalia-web/<BRAND>/...` (override with
  `--dest`), kept separate from `dentalia-sftp` so `source_url` still says which
  source a document came from.
- **Re-runs are conditional.** The local file's mtime goes out as
  `If-Modified-Since`; a 304 costs one round trip and no bytes. The filesystem
  is the only state, so an interrupted run resumes by being run again. The mtime
  is written only after a complete body lands.
- **Nothing is ever deleted.** A file that has left the index is reported;
  `--prune --yes` moves it to `.removed/<date>/`, still on disk. The index
  itself is kept per run under `.index/api-data-<date>.json`; both dot
  directories are invisible to a per-brand scan.
- **Politeness** is one request at a time with `--delay` (default 0.25 s)
  between them, 304s included. It is the client's production web server, and
  `robots.txt` disallows `/mdr-dokumentacija` (the page) while saying nothing
  about `/api/*` — ask before a full pull.
- The run ends by printing the `backfill.scan` command for every brand it
  touched, with the path the **worker** sees, derived from `IMPORTS_HOST`.

Two things the mirror cannot fix. `backfill.scan` walks `*.pdf`, so the 50
non-PDF files (docx, xlsx, zip, and three 3M declarations whose extension is
literally `.pdf_`) are mirrored but never imported; the script lists them.
And `skip_backfill` is keyed on the folder name, so **NEODENT is refused from
this source too** — the 2026-08-19 ruling stands until someone rules on the
site's copy, which holds 5 files where SFTP held 34.

### Komet coverage map (`komet-coverage`)

Komet's compliance bundle ships no parseable per-article REF list on its PDFs, but it ships two indexes of its own — a spreadsheet and a Word table — mapping article numbers to the declaration that covers them. `backfill.scan` archives both alongside every PDF; `komet-coverage` reads the archived copies and links items from them instead of paying an LLM to parse 186 PDFs. Run in order:

```bash
# 1. scan (archives PDFs + the two index files, files one doc per family+regulation)
docker compose run --rm worker python -m app.cli enqueue backfill.scan \
    backfill:KOMET:<date> --payload '{"drive_folder": "/imports/dentalia-sftp/KOMET"}'

# 2. let extract -> validate -> gate drain

# 3. dry run, and READ IT
docker compose run --rm worker python -m app.cli komet-coverage

# 4. apply
docker compose run --rm worker python -m app.cli komet-coverage --apply
```

A re-scan of step 1 emits nothing new until the KOMET rows are cleared from `fetch_log` first — the same hash dedupe every corpus scan gets (C2/C3 two-cycle). The index `content_hash` printed at the end of step 3's dry run is what tells you the index itself changed since the last run: `komet-coverage` is one-shot by design (`app/komet_coverage.py` module docstring — steady-state refresh is `doc_sources[kind:"direct"]` → `fetch.url`), so a changed index is a human decision to re-run, never an automatic re-link.

**A partial KOMET dump fails the whole scan, not just the missing file.** `backfill.scan` raises (`BackfillError`, job lands in dead-jobs) if any `coverage_map.sources` entry the playbook declares — currently both the xlsx and the phase-2 docx — cannot be read, and it checks this *before* archiving or enqueuing anything else in the scan. A KOMET dump missing one index file therefore blocks **all 102** of that manufacturer's declarations from being filed, PDFs included, not only the coverage-map linking. Symptom: the KOMET `backfill.scan` job lands in dead-jobs and nothing files for the manufacturer at all — reads like a mysterious total outage. Cause: one declared `coverage_map.sources` file (spreadsheet or Word table) is missing or unreadable in that dump. Fix: get the corpus dump re-synced with both index files present, then re-run step 1 — the failed run wrote nothing to `fetch_log`, so there is nothing to clear first.

The three `playbooks` actions run in that order, and the order is the point: `validate` asks whether the files are well-formed, `reconcile` asks whether they join to anything BC actually emits, and `sync` is the first write.

`playbooks validate` is the strict operator gate (unlike the runtime loader, which never raises): it fails loudly and exits 2, naming the offending file(s), on a file that isn't valid JSON or fails to parse as a playbook, on a `match`/`ref_strategy` parse section that fails to load as a T0 template (e.g. a typo'd key), and on BC-code/manufacturer-name conflicts across files; on success it never writes. `playbooks sync` seeds `manufacturer_alias` from BC **codes** *and* from each playbook's `aliases` list (`app/cli.py:166-168`), and reports an `orphaned_groups` count. The code-keyed rows are what the BC export adapter resolves, since it keys manufacturer by BC code and not by name; the name-keyed rows exist for the paths that only ever see a name — VALIDATE's backfill comparison chief among them (`app/cli.py:87-99`). An earlier version of this paragraph said sync writes codes only, which stopped being true when backfill matching landed. It derives at **entity level** from `vendor_master` plus the playbooks: every code the master issues gets its master name (`source='vendor-master'`), and a playbook claiming any code of an entity wins for *every* code of that entity (`source='playbook'`), which is what stops a multi-code manufacturer splitting into two namespaces. With an empty `vendor_master` it degrades to the playbook-only projection, so it is safe to run before the master is imported. A code claimed by two playbooks refuses everything: that is an authoring error, and `validate` — which `sync` runs first — is the gate for it. Since 2026-08-27 `sync` hands `validate` the vendor-master **brand map**, which turns on one more refusal: `BrandCollision`, raised when a playbook's canonical name or any alias *contains* the master name of a BC code it does not claim. That is the `[brand-parent-aliases]` failure — `Sirona Dental Systems GmbH` authored as a DENTSPLY alias while SIRONA is code 012 — and it is containment rather than equality precisely because the master carries bare brand labels (`SIRONA`) and playbooks carry legal names. The message names the brand, its codes, and the two ways out: claim the code, or drop the name. `playbooks validate` on its own stays a pure file check and does **not** raise this — it opens no connection — so the ladder is `validate` (files well-formed) → `reconcile` (joins, reports the collision, exit 1) → `sync` (the write, refuses). Until 2026-08-26 the conflict key carried a catalogue tag, so a cross-catalogue collision was legal as files and was instead skipped and **counted** by `derive_aliases` rather than blocking the other 388. There is one catalogue, so no such case exists and `skipped` is always 0 on this path; `derive_aliases` keeps the guard for callers that skip the gate, because what it prevents is two real manufacturers merged into one entity. On the delivered data (measured 2026-08-20, after the 33-playbook sweep): 445 alias rows — 350 `vendor-master` (all code-keyed), 94 `playbook` (38 code-keyed, 56 name-keyed) and 1 `self-seed`. **Run `sync` before an ingest, not after**: re-pointing an alias does not retro-fix `item_group` rows already resolved under the old value, so a non-zero `orphaned_groups` count means those items need re-resolving.

`playbooks reconcile` is read-only and reports whether the authored playbooks join to the catalogue at all. The LJ export carries a manufacturer **code**, so `bc_codes` is the only join key that exists and `manufacturer` is not one: a playbook with no `bc_codes` is unreachable however good its `domains` and `doc_sources` are, and nothing else says so (DISCOVER's `for_manufacturer` just returns `None` and the group falls through to an unrestricted search). It groups codes into **entities** first (same vendor-master name, or co-claimed by one playbook) because per-code precedence would split one manufacturer into two `canonical_manufacturer` namespaces that RESOLVE's name-family rung can never rejoin. Findings: `dead-playbook` (no codes, name matches no master name, with a fuzzy "did you mean"), `unjoined-playbook` (no codes but the name **is** a master name, so the fix is the code it names), `partial-claim` (claims a subset of an entity, so sync would extend the authored name to the rest), `alias-collision` (a name the playbook claims **contains** another entity's master name — widened from string equality on 2026-08-27, which never matched a legal name against a bare brand label and so was blind to the failure it existed for; it is the same function `sync` refuses on, so the report and the write cannot disagree), `unknown-code` (claims a code the master does not issue, checked only for codes whose `catalogue` matches `--source`), `uncovered-code` (info: a code items carry that the master is missing), and `rename` (info: the authored name differs from BC's and wins). Exit 1 on blocking findings, 2 on operational errors. Pass `--export <BC item export>` to size every finding in items; without it the counts come from `item_mirror`, which is empty before the first ingest, and the report says so rather than printing a silent wall of zeros.

`vendor-master` mirrors BC's manufacturer export (`imports/Proizvajalci.xlsx`, columns `Šifra` + `Ime`, 390 rows) into `vendor_master`. It resolves all 381 manufacturer codes in the LJ item export, covering 19,089 of 19,089 coded rows. **Dry run by default** — it prints added / renamed / disappeared / unchanged and writes nothing until `--apply`. A code whose name changed is reported as a `RENAME` and **refused** unless `--allow-renames` is also given, because `item_group.canonical_manufacturer` is effectively write-once (RESOLVE's ladder short-circuits on `_existing_link`), so re-pointing a code after items are grouped splits that manufacturer in two with no repair path. A code that disappears from the export keeps its row with a stale `import_batch` rather than being deleted. `--source` records which BC instance issued the codes, which is deliberately not the same field as the warehouse tag (gap G11).

`playbooks drift` (2026-08-31) answers a question nothing could answer between migration 050 and that date: **is `playbooks/<slug>.json` still what the database holds?** 050 made the database authoritative and left the files as the authoring record, and nothing kept the two honest — a file edited after the import went nowhere, silently, because the only writers of `manufacturer.body` are the seed's first-import path (`body IS NULL` only) and the editor's save route. `manufacturers seed` did detect a difference, but two differing bodies carry no direction, so all it could say was *"resolve by hand"*.

`manufacturer_playbook_revision` supplies the direction, and that is the whole idea: a file matching an **older** revision is `FILE STALE` and can be regenerated mechanically, because the database provably holds everything that file had plus what came after; a file matching **no** revision has `DIVERGED` — both sides were edited, the file carries something that exists nowhere else, and regenerating it would destroy that edit. So stale files are offered, divergences are refused and handed to `/playbooks/<slug>` (Denis, 2026-08-31). It also reports `NOT IMPORTED` (no row yet — the seed's job) and `DB ONLY` (the file was deleted).

**Reports by default and writes nothing**; `--apply` rewrites only the stale files, never a divergence. The regeneration preserves the file's own key order recursively, including inside `doc_sources` entries: Postgres normalises `jsonb` key order, so rebuilding in the database's order turned a one-key change into a twelve-line diff, which defeats keeping these files in git to be read. Identity keys (`manufacturer`, `bc_codes`, `aliases`, `rev`) are carried from the file — they live in their own tables and are not part of the body. Note the command reads the **directory** explicitly: every process that runs it has a DB source set, and `load_raw` with no `dir_path` returns rows in that case, which compared the database against itself and reported everything in sync.

**`/playbooks` shows the same answer without being asked** (2026-09-02). The banner at the top of the page is `registry.playbook_drift`, calling the same `manufacturer_seed.drift` this command calls — not a second implementation, because the banner's entire value is that it agrees with the command it tells you to run. It names each stale file with both revision numbers and the key-level delta, sends a divergence to `/playbooks/<slug>` instead of offering `--apply`, and draws a green edge with "all N in sync" when there is nothing to say: a check that renders only on failure is indistinguishable from a check that stopped running.

It needs the files, and the web process does not normally have them — `Dockerfile.web` copies only `app/` and `web/`. Compose therefore bind-mounts `./playbooks` into `web` **read-only**, for this one caller. That reverses the slice-4 removal narrowly and deliberately: slice 4 was about which store an edit LANDS in, and `:ro` settles that permanently, while a comparison needs both sides by definition. `playbook_drift` passes the directory explicitly on every path — `load_raw(None)` returns rows once `set_source` is set, so a defaulted argument would compare the database against itself here exactly as it did in the command. Where the directory is absent the banner renders **nothing at all**, which is the deployed configuration: no files, so nothing can be stale, and `drift()` would otherwise report all 38 rows as `db-only`. That absence is also why the banner does not count against `[shell-commands-on-client-screens]` — it names a shell command, and no client can ever see it.

`manufacturers seed` (migration 049, 2026-08-26) turns the manufacturer **entity** — until now a value `reconcile._entities` computed inside `playbooks sync` and threw away — into rows: one `manufacturer` per named entity, one `manufacturer_bc_code` per BC code (foreign-keyed to `vendor_master`, so a code BC does not issue is refused rather than silently accepted, which nothing checked before), and one `manufacturer_name` per canonical name and authored alias, keyed on the **casefolded** name so it cannot disagree with `for_manufacturer`'s comparison the way case-sensitive `manufacturer_alias.raw_name` can. Additive and idempotent — a second run reports everything `unchanged` — and **dry run by default** anyway, `--apply` to write. Not because it can do damage (it cannot: no renames, and it refuses to write at all on a conflict) but so that every manufacturer-touching command in this CLI behaves the same way and nobody has to remember which one is the exception (Denis, 2026-08-26). The dry run is also when you read the orphan report. It writes **nothing** on a conflict (a code or name already pointing at a different manufacturer, a body edited since the last import, or — since 2026-08-27 — a `canonical_name` that has been renamed while the file still says the old one): that is a decision someone took, and an import must not half-apply around it. The entity is found by **`slug` first** and only then by canonical name, because the name is not stable across a rename: a canonical-only lookup matched nothing after one and fell through to an INSERT, which `slug`'s UNIQUE constraint turned into a failure of the entire import rather than a duplicate row. `manufacturer_alias` and `vendor_master` are untouched — the alias table stays the runtime projection RESOLVE and GATE read, and `vendor_master` stays BC's mirror that this only points at. It also refuses on a `BrandCollision`, for the same reason and through the same `validate` call `sync` uses — the seed is a write, and a partial identity import is the state nothing downstream knows how to read. On the delivered data (measured 2026-08-26, over the 33 playbooks of that date): 384 entities → 383 rows (one entity is nameless and skipped, as is any empty canonical), 389 `manufacturer_bc_code` rows (39 `playbook`, 350 `vendor-master`), 438 `manufacturer_name` rows (383 canonical, 55 alias), 33 rows carrying a `slug`. Two playbooks claimed a second BC code on 2026-08-27 to clear the collisions the new guard found (`ustomed` ← 10111, `kuraray-dental` ← 10071), which merges two entities into two existing ones: **384 → 382 entities, 383 → 381 named, 44 → 46 codes under a playbook**, measured with the same file set on both sides so the figure is that change alone. The report ends with every `item_group.canonical_manufacturer` that has **no** `manufacturer` row. Those are for `regroup` to fix; the seed names them rather than inventing rows to make them fit. **Read it knowing it under-reports, and why.** Migration 042 (EUDAMED, S2.3 stage 2) seeds a `manufacturer` row for every distinct `manufacturer_alias.canonical_name`, so the question this join asks quietly changed from *"does an entity cover this"* to *"does any row exist at all"*, which is now nearly always yes. Measured on the live database 2026-08-27, both joins on the same data: against `manufacturer` it reports **1 value / 3 groups** (`3SHAPE MEDICAL A/S`), against `manufacturer_name` — the 437 rows the seed actually derives from entities — it reports **4 values / 11 groups** (`ULRICH STORZ GMBH & CO. KG` 5, `3SHAPE MEDICAL A/S` 3, `''` 2, `NORITAKE` 1). The second is what this report meant before 042 existed. Until the join is changed, `playbooks sync`'s own `orphaned_groups` warning is the reliable signal for an alias repoint — it is what caught the ULRICH STORZ / NORITAKE pair on 2026-08-27 while this report stayed silent about them.

**Since slice 2 (2026-08-27) this seed is a PREREQUISITE, not a convenience.**
The worker, the web app and the CLI read playbooks from `manufacturer.body` and
the 049 identity tables, not from `playbooks/*.json` -- so on a database that
has never been seeded, `for_manufacturer` returns `None` for every manufacturer
and DISCOVER falls through to an unrestricted search, BACKFILL stops honouring
`skip_backfill` and `exclude`, and T0 loses all six parse templates. Nothing
errors; it just behaves as though nobody ever authored anything. Run
`manufacturers seed --apply` after `vendor-master --apply`, and again after
editing any file in `playbooks/`.

The seed reads FILES, always, even in a process configured to read rows -- it
passes an explicit directory for exactly that reason. Otherwise the import
would read its own output, report a clean run, and never land the edit. It also
writes revision 0 of each body to `manufacturer_playbook_revision`, which is
what keeps `extraction_attempt.(playbook_slug, playbook_rev)` resolvable, and
it REFUSES to overwrite a body that differs from the file's -- that is an edit
someone made in the UI, and an import must not silently revert it.

**Renewal contacts** are edited on `/manufacturers/{canonical_name}` and need this seed to have run — the form says so when the row is missing, rather than rendering an input that silently does nothing. `manufacturer.contact_emails` was declared in migration 006 and never had a writer, so `discover._contact_known` and `email_request._contacts` returned `[]` for every manufacturer and every renewal draft was unaddressed. Saving records `updated_by` from the proxy-authenticated user, the same value a gate decision records as `decided_by`. An empty list is legal and is not an error: a draft with no recipient is addressed by hand, and `/drafts` refuses to release one until it has an address.

**Importing an item catalogue export without shell access** (`/import`, BC import upload slice A, 2026-08-25) is the browser-side counterpart to `vendor-master`'s CLI dry-run-then-apply shape, for the export that feeds `item_mirror` rather than `vendor_master`: upload the file, read the **preview**, then **apply**. The upload spools into `import_inbox` (migration 040, a two-phase table — a preview job reads the row and leaves it, only the apply job's read consumes it) and enqueues `ingest.run` with `dry_run: true`; the handler diffs against the live mirror and reports counts and anomalies same-shaped as a CLI ingest, but performs no upsert and emits no `resolve.group` — the standing `data_anomaly` ledger stays untouched, the findings sit under `anomalies_preview` on the job result instead — the counters below (`seen`, `changed`, `missing_mfr_ref`, and the rest) are reported the same way on both runs. Reading the preview and clicking Apply enqueues a second `ingest.run` job, the same payload with `dry_run: false` plus `preview_job_id`, which re-diffs against current state and writes — and its page then shows the previewed counts beside the applied ones, so "we said 15,958 and wrote 15,958" is on screen rather than taken on trust. A row that moved is highlighted and named. A **delta file** — a partial export naming only what changed — is safe to import this way: an item absent from the file is simply never mentioned, and INGEST only upserts what it reads, so nothing is retired for being missing. Abandoned previews (uploaded, never applied) age out of `import_inbox` after `SPOOL_RETENTION_DAYS` (7), collected opportunistically by the next import job rather than a scheduler tick.

**Importing the manufacturer master without shell access** (`/import`, BC vendor import upload slice B, 2026-08-26) is the same shape for `imports/Proizvajalci.xlsx`, the file `python -m app.cli vendor-master` reads — both go through `app/vendor_master.py`, so the browser and the CLI cannot drift. Upload it in the page's second section, read the preview, tick the box if it re-points any code, apply. What differs from the item import: the diff has **four** categories rather than two, one of them (**renamed**) is refused unless you opt in, a **disappeared** code is kept rather than deleted, and the handler **emits nothing at all** — no `resolve.group`, no alias sync.

**Run `playbooks sync` yourself afterwards, and before the next item ingest.** A vendor import that adds or re-points codes leaves `manufacturer_alias` stale, and the confirm screen says so rather than fixing it: `sync` reports `orphaned_groups` — `item_group` rows still carrying a `canonical_manufacturer` it has just re-pointed away from, which it does **not** retro-fix — so a non-zero count is work a human has to act on, and running it as a side effect of an upload would produce that count where nobody reads it. `sync` also refuses outright on a playbook authoring conflict, which would dead-letter an otherwise clean vendor import for a reason having nothing to do with the vendor file.

**Starting a playbook from the UI** (2026-08-27). `/manufacturers?no_playbook=1` lists every entity with no playbook, gaps first and biggest first; each one's page now has a **Start a playbook** card that attaches a slug and an empty body at revision 0. That is the whole creation path — identity is inherited from the row the seed already derived from `vendor_master`, and authoring continues on `/playbooks/{slug}` with the controls below. Before this, `manufacturer.slug` was written only by `dentalia manufacturers seed`, so starting a playbook meant hand-writing `playbooks/{slug}.json` in the repo. That file route still works and is still how the 33 delivered playbooks got in; it is no longer the only one.

**Editing a playbook from the UI** (`/playbooks/{slug}`, slices 3a/3b). Tier A — `domains`, portal `doc_sources`, `date_labels`, `type_markers`, `source_priority` — are plain controls. Tier B added 2026-08-27: `doc_sources` kind=`direct` (fetch targets; a host on `playbooks/robots_refused.txt` is refused at 422 quoting the ruling, and the same URL authored as a `portal` is accepted, because the refusal is of fetching the host, not of naming it), `skip_backfill` (checkbox plus a reason that `_parse` requires), and `extract_hints` (keys fixed to the extractor's own target list plus `general`, values capped at 300 characters each and 1500 per playbook — anchored on the 263-character safety preamble the block sits under, since a hint that dwarfs it drowns it, and it is billed on every extraction for that manufacturer). Tier C — the regexes, the T0 parse template, `coverage_map`, `exclude` — stays read-only. Every save is refused unless it parses, passes `playbooks.validate` on the whole proposed set, and matches the revision the form was opened at; each one appends to the history card and is restorable from it. **`bc_codes` is a picker, never free text**, and is add-only: `dentalia_api` has no DELETE on `manufacturer_bc_code`, so un-claiming a code stays CLI work. Claiming one re-points its alias on the next `playbooks sync` — run that afterwards and read its `orphaned_groups` count.

**Renaming a manufacturer from the UI** (`/manufacturers/{name}`, 2026-08-27) is a two-step action, and the first step writes nothing: it reports how many `item_group` rows still carry the old name (they do not follow a rename — RESOLVE short-circuits on `_existing_link`), how many of those can never be regrouped because a member already carries an `item_document` link, and the exact `dentalia regroup` line. Confirming writes **only the identity half** — `manufacturer.canonical_name` and the `manufacturer_name` rows, with the old name demoted to an alias so documents printing it still resolve. It does **not** re-point `manufacturer_alias` and does **not** re-resolve: the web runs as `dentalia_api`, which is SELECT-only on `manufacturer_alias`, `item_group` and `item_group_member`, so both of those stay CLI work and the confirmation message names them. Run `playbooks sync` first, then `regroup`. A rename is refused when the new name already belongs to another manufacturer, or when it contains a BC brand this manufacturer does not claim (the same `BrandCollision` guard, with a bigger blast radius — this name is what every future fetch files under).

`regroup` drops item groups so RESOLVE rebuilds them, and is the reason a wrong alias seed is recoverable. `item_group.canonical_manufacturer` is effectively write-once (RESOLVE's ladder short-circuits on `_existing_link`), so an alias corrected after the fact does not retro-fix groups already built under the old value -- that is what `playbooks sync` reports as `orphaned_groups`. Select by `--manufacturer` (repeatable, the unit an alias correction moves), `--group`, or `--all`; **dry run by default**. Note the split: a group whose manufacturer is merely *wrong* can be fixed in place with an `UPDATE`, and only a group whose *membership* is wrong needs this.

It refuses the **whole** selection if any part is blocked, rather than doing the rest -- a half-regrouped catalogue is worse than either end state. Blockers: `has-documents` (a member carries an `item_document` link; regrouping would discard document-to-item links nothing rebuilds), `open-manual-task`, and `undrained-upload`. The latter two are FK-free soft references that Postgres would happily leave dangling, and neither is reproducible: a manual task can hold a human's in-progress context, and an `upload_inbox` row holds file bytes that exist nowhere else. Open `grouping_suggestion` rows are the exception -- they are derived state that `resolve.group` regenerates, so they are deleted and **their items joined to the re-enqueue set**, which matters because those items belong to no group and would otherwise be stranded holding a candidate list of dead group ids.

Verified end to end on a 400-row slice of the LJ export: 141 groups / 194 membership rows, regroup (141 groups and 54 suggestions deleted, 248 items re-enqueued), re-resolve, and the grouping comes back identical -- same group count and the same item-to-item pooling. Group **ids** are a `bigserial` and are expected to differ.

`mine-contacts` does the same thing for supplier e-mail addresses, and exists for the same reason: `manufacturer.contact_emails` is what a letter is addressed to and what `/drafts` refuses to release a letter without, and **7 of 384 manufacturers carry one** (2026-09-15). It reads `document_text`, classifies every address through `app/contacts.py`, and writes **only a role mailbox on the document's own manufacturer's domain**. Four things it refuses, each for its own reason: a notified body or regulator (a renewal chase sent to the certifier looks right and is wrong), another company's domain (the document is misfiled or there is an unrecorded brand-parent relation — both are findings, not contacts), a named individual (the column may hold personal data, but a machine proposing to write to a person it found in a PDF is a different act from a person choosing to), and an address on the right domain that is not a role mailbox (`marketing@`, `incident@` — reported first, because every entry in that pile is already known to belong to the right company). It never overwrites a column somebody has filled, and re-checks that at apply time. **Measured on the live registry 2026-09-15: 93 documents carry an address and it proposes zero** — the addresses in our documents are notified bodies (13), named individuals (4), other companies (1) and domains no playbook authors (8). Its yield is gated on authored `domains`: a role mailbox on an unauthored domain is reported with a note saying so, and authoring that one playbook field converts it. **Dry run by default.**

`mine-srn` reads EUDAMED SRNs off documents already in the archive and proposes them to the review queue at `/manufacturers/srn-queue`. It costs **nothing** — no model call and no network call, because the text is already parsed and stored in `document_text`. It exists because the two paths that fill `manufacturer_srn` both need EUDAMED to know the manufacturer already: the register pull (`eudamed.certregister`) sees only actors holding certificates, and the article probe that exists for exactly that gap is unreachable — `probe_srn` runs only inside a sweep, and `sweep_release` refuses with 400 on the same empty-SRN condition. GC EUROPE sat in that deadlock with 356 device articles, zero certificates, and its SRN printed in 126 documents we hold. **Every row it writes is `pending`, never `auto`.** The pattern matches an importer's or a notified body's SRN quoted inside somebody else's document just as happily as the manufacturer's own, and confirming the wrong one makes the WRONG manufacturer's whole catalogue sweepable — so a person decides, on the evidence, with `source_doc_id` naming the page to open. Skips any manufacturer already holding a trusted SRN, and skips any pair already decided, so a re-run after `--apply` proposes zero. **Dry run by default.** Confirming a row does not itself sweep anything: that is still the release button.

`repair-ref-list` restores REF lists that the pre-fix `tiers.merge` erased, and is a **one-off for data extracted before 2026-08-11** -- on a clean database it plans zero repairs and is a no-op. The bug: `extract_ref_list` stamps a flat `0.9` confidence that can never clear the `0.95` escalation threshold, so a *successful* T0 REF list escalated on every document, and the old unconditional `{**base, **new}` merge let a vision tier's empty answer overwrite it (measured: 0 of 61 GC corpus attempts carried a T0-sourced `ref_list`, 26 had lost one). It costs **nothing** -- T0 is deterministic and re-reads the same archived bytes, the stored LLM answers are reused verbatim, and no API call is made -- so this is not a re-extraction. **Dry run by default.**

It **appends** a new `extract_rev` rather than overwriting, keeping the original ladder auditable per migration 004. Because VALIDATE reads `(content_hash, extract_rev)` straight from its job payload and payloads are immutable (invariant 9), a *pending* `validate.doc` job pinned to the old rev is deleted and re-emitted at the new one -- the "delete-and-re-emit, never payload versioning" rule; `running` and terminal jobs are never touched, and the replaced job's `group_id` is carried over so a group-scoped match does not silently become unscoped. The field decision is delegated to `tiers.merge` itself rather than reimplemented, so a repaired row means exactly what a freshly extracted one means. A corpus file that has moved or will not open is **reported** as a skip with its reason, never dropped silently. Re-runnable: it diffs against the newest rev, so a second run plans nothing.

`repair-stated-class` is the same shape for a different one-off: it records the risk class already-held documents state, for everything extracted before `stated_class` became a target (migration 032, 2026-08-21). Also free and also model-free — T0 reads the class off the text already stored in `document_text` (018), so no archived file is reopened and no API call is made. **Dry run by default**, re-runnable, and a no-op once every latest attempt carries the key.

Its one load-bearing difference from `repair-ref-list`: it **emits no `validate.doc` and re-points no job**. `repair-ref-list` re-emits because it changes what a document COVERS, which has to be re-adjudicated; this changes nothing a disposition is made of (`stated_class` is in no VALIDATE rule, no GATE flag, and not in `REQUIRED_FIELDS`), so putting 768 settled dispositions back through the gate to record a display value would risk far more than the value is worth. The consequence is intended: historic documents carry the value in `extraction_attempt` and **not** in `document.stated_class`, which only a GATE write can fill (invariant 1) — so the `item_class_check` view (033) reads `COALESCE(document.stated_class, latest attempt)` and compares history without re-deciding it. Every scanned hash is reported under exactly one of `stated` / `abstained_multi` / `no_mention` / `already_has_key`; an abstain (a document printing several classes at once) is a finding, not a shrug.

`repair-mfr-overbind <doc_id>` retracts the `mfr-scope` production links of a QA certificate whose own text enumerates the devices it covers — the doc-310 cleanup (Kiwa Cermet MED 31385: three enumerated device types, machine-bound to 356 GC items; Denis ruling 2026-08-24, "Guard + clean 310"). It re-verifies the basis itself with the same detector the `device-enumeration` VALIDATE flag uses, and **refuses** (exit 1, nothing planned) when the stored text does not enumerate, when there is no usable `document_text` (a scan), or when the latest `bind-manufacturer` audit event was a human's — the guard never barred human bindings and ops tooling does not undo them. Retraction is a status flip to `retracted` with `match_basis` kept (append-only), one `audit_log` entry per link (`decided_by ops:repair-mfr-overbind`, the recorded invariant-1 exception); the document row itself stays untouched. **Dry run by default**, re-runnable — retracted links stop being selected, so a second run plans zero. Takes the doc_id as an argument; nothing is hardcoded to 310.

`repair-document-manufacturer` fills `document.canonical_manufacturer` (migration 053) from the `manufacturer` evidence each document already carries. 053 is additive, so every document written before it holds NULL — correct but useless, since the column exists precisely for the **540 documents that reach no manufacturer through item links** (443 of them `filed`, which is every filed document by definition). It resolves the latest-rev evidence string through `manufacturers.resolve_canonicals` and then `gate._bindable_manufacturer`, so the tool and the handler cannot disagree about what the FK will accept. **Declines rather than guesses:** a spelling folding to more than one curated canonical is skipped (that choice is a coin flip over a manufacturer's whole range), and a name with no curated alias is left NULL rather than minting a `manufacturer` row — that would seed the registry from OCR, which is what VALIDATE's read-only alias lookup exists to prevent. Both counts print. An existing binding is never overwritten (a human decision, or C16's, outranks a string read off a page), which is what makes a second run plan zero. Dry run by default. **Measured 2026-08-31, before the column existed:** 531 evidence strings → 481 resolve to exactly one canonical, 0 ambiguous, 50 unresolved.

`repair-mfr-task-reason` stamps `reason: md-class-unknown` onto open `mfr-binding` review tasks raised before that field existed. Without it those tasks render the **default** review sentence — "approving links it to every medical-device product from that manufacturer" — which is false exactly when every item we hold from that manufacturer carries a blank BC device class, and that is the case `[mfr-bind-empty-class]` raised them for. 17 open tasks were in that state on 2026-08-31, doc 2 (3Shape Poland, ISO MD 583446) among them. It discriminates on the same tri-state `md_flag` the handler uses (no `TRUE` and no `FALSE` means BC has said nothing), changes no disposition, link or document — only the sentence a reviewer sees — and leaves already-stamped tasks alone. Dry run by default.

### Running an ingest, and reading what it reports

Two ways in, one handler. The UI form (`/ingest`) is the normal one; the CLI is
for a path the form does not offer or a run you want to script:

```bash
docker compose run --rm worker python -m app.cli enqueue ingest.run \
    'ingest.run:web:LJ:csv:/imports/Artikli 3.7.2026.xlsx' \
    --payload '{"source": "csv", "ref": "/imports/Artikli 3.7.2026.xlsx", "catalogue": "LJ"}' \
    --priority interactive
```

The scheduler emits its own monthly run from `INGEST_WATCH_DIR` under
`ingest.run:sched:{catalogue}:{period_key}`; the two key shapes are deliberate
and documented in the PRD's dedupe table — they are not meant to dedupe against
each other.

The handler returns a `Result` envelope, persisted to `job.result` and rendered
on `/ingest` under **Recent runs** (and readable with
`select result from job where type='ingest.run' order by id desc limit 1;`).
Every counter is pre-seeded, so a key missing from the report is a bug, not a
zero:

| Count | What it means |
|---|---|
| `seen` | Rows read from the export that reached the MD gate |
| `changed` | Rows whose mirrored values actually differ — the only ones that bump `mirror_rev` and emit `resolve.group` |
| `unchanged` | Rows already mirrored identically; no job emitted (invariant 6 in the catalogue) |
| `non_md` | Explicitly non-MD rows. A brand-new one is not mirrored at all |
| `reclassified_non_md` | Was MD, BC has declassified it: the mirror row is corrected to `md_flag=FALSE` so mfr-scope consumers stop treating it as a device |
| `md_unknown` | Blank device class. Counted whether or not `process_md_unknown` lets it through — it is a fact about the export, not about our config |
| `missing_mfr_ref` | Processed rows with no supplier article number. This rate bounds achievable AC1; it is descriptive, never a target |
| `mfr_ref_prose` | Article-number fields holding a Slovene stock remark (`NE BO VEČ NA ZALOGI!`), scrubbed to NULL so the REF gate never compares one as a code. The value survives on the anomaly and in BC |
| `skipped` | Rows the reader could not use at all, with a bounded `skipped` sample beside the counts |

`anomalies` in the same envelope feed the standing ledger on `/data-quality`.

## Configuration

All config loads through [app/config.py](../app/config.py): environment variables first, then an optional TOML overlay named by `DENTALIA_CONFIG_TOML` (a set-but-unreadable file is a hard error by design), then defaults. The key surface is PRD v3 §11; [.env.example](../.env.example) lists every operational key with its dev default; tuning thresholds (`RESOLVE_NAME_*`, `DISCOVER_*`, `QUEUE_*`) are deliberately code-default-only until the S1.7 sweep recalibration (PHASES S1.3 posture: don't surface tuning keys prematurely). Highlights:

- `DATABASE_URL`: worker-side connection (schema owner). Compose overrides it to reach Postgres by service name.
- `API_DATABASE_URL`: web-side connection as `dentalia_api`. Never the owner (Invariant 1).
- `MODELS_T1` / `MODELS_T2` / `MODELS_RANK`: extraction tier models. Swapping a tier requires the Phase 0 diff protocol re-run.
- `GATE_THRESHOLD_HIGH` / `_MED`, `FETCH_POLITENESS_MS`, `FETCH_RECENCY_WINDOW_DAYS`, `BUDGET_*_CAP_EUR`, `BATCH_POLL_INTERVAL_S`: pipeline tuning.
- `WEB_REQUIRE_AUTHENTICATED_USER` / `WEB_TRUSTED_USER_HEADER`: G3 v0 auth — compose sets the former true so `web` trusts `caddy`'s forwarded header for `decided_by`; leave false for bare host-side runs. It is also what arms the caller-class policy in `web/access.py`: false means "no proxy in front", so every caller is `staff` and the guard is a no-op (local dev, tests).
- `WEB_API_KEYS` / `WEB_BC_LINK_KEY`: **secrets** — top of `.env` beside `IMAP_USER` / `IMAP_PASSWORD`, never a runbook tuning key. `WEB_API_KEYS` is a comma-separated list (a second consumer, or a rotation, is an env change rather than a downtime swap) authenticating the `service` class via `X-API-Key`; `WEB_BC_LINK_KEY` is the single shared value BC concatenates into `/item/{ref}?k=…`. **Empty disables the class it names** — empty never means "allow everyone": with no `WEB_API_KEYS` no key is a service credential, and with no `WEB_BC_LINK_KEY` the `/item/*` routes 404 outright.
- `WEB_PUBLIC_BASE_URL` (default empty): absolute base prefixed onto the `url` of each document in the item-documents payload. Empty leaves those URLs relative, which is right for same-origin browser use and wrong for a webshop backend embedding our links in its own pages.
- `STORAGE_LOCAL_ROOT` / `STORAGE_BASE_URL` (S1.8 Task 1): `LocalFsStore`'s archive root and served-URL prefix. Compose mounts the `archive_data` volume at `/archive` on `worker` and sets both to that absolute path, so `archive_url` survives a container restart and resolves to `/archive/{layout_path}` instead of an unopenable `file://` URI. A relative `STORAGE_LOCAL_ROOT` (the code default, `./archive`) logs a startup warning, since it means archived files live on the container's ephemeral layer. `backfill.scan` archives through this same adapter, so the corpus mount (`/imports`, read-only on both `worker` and `web`) is a *source*, never the archive.
- `EXTRACT_MODE` (compose) → `DENTALIA_EXTRACT_MODE`: extraction transport, `sync` (default) or `batch`. `batch` uses the Anthropic Batch API and halves the LLM bill, which is what the PRD asks for on sweep work — but every extraction run so far has been sync (all 112 rows in the pre-wipe cost ledger carried `batch=false`), so the batch path has not met the real API. Pilot it on one brand and check `extraction_cost.batch` is actually `true` before running the rest of the corpus on it. Running a sweep without setting it silently pays double.
- `WEB_ARCHIVE_ROOT` (S1.8 Task 1): the same `archive_data` volume, mounted read-only on `web`, backing `GET /archive/{path}`.
- `WEB_PLAYBOOKS_DIR` (**leave unset**): the one switch that can send the UI back to reading playbook JSON off a disk. Since slice 4 (2026-08-27) playbooks live in `manufacturer.body` and the refused-host list in `refused_host`; `web` reads both from the database and the `./playbooks:/app/playbooks:ro` bind mount is gone from compose. Empty — the compose default, and no such env var is set — means **rows**. Setting it makes `/manufacturers` and `/playbooks` read that directory instead, because an explicit directory always beats the configured source (the same rule that lets `manufacturers seed` import into the database it would otherwise be reading).

  **Do not set it in production.** It fails silently in the worst direction: the UI would show, and *save against*, a second and staler copy of every playbook, while the workers went on reading rows. There is no error, no warning, and no divergence report — the two stores just drift. It exists as a test/dev seam, for reproducing a malformed-file bug against the real wiring (`tests/test_web.py`), and nothing else.

  Verified 2026-08-27, on the live containers rather than from the code: `/app/playbooks` **does not exist** in `dentalia-web-1` (`Dockerfile.web` copies only `app/` and `web/`), so web has no files to fall back to even by accident. The worker image *does* still carry the 38 JSON files — the seed needs them — so presence there proves nothing; it was checked by running that image with an empty tmpfs over `/app/playbooks`, where all 38 playbooks, 5 refused hosts and 5 T0 templates still resolved and `playbooks.reads_files(None)` returned `False`.

  `app.playbooks.reads_files(dir_path)` is the programmatic form of that question, and it is what the "playbooks directory not found" error is now gated on: that message can only appear when a directory is genuinely in use, so it no longer fires against a healthy row-backed deployment.
- `SCHEDULER_INGEST_WATCH_DIR` (empty by default — the monthly ingest tick is a no-op until set, no live BC feed exists yet), `SCHEDULER_EXPIRY_EMAIL_ENABLED` / `SCHEDULER_FAILURE_REONBOARD_ENABLED` (both default off — gate `email.request` / `playbook.reonboard` emission until S2.4/S2.1 land). `DISCOVER_EMAIL_RUNG_ENABLED` (default off, added 2026-07-31) gates DISCOVER's email rung the same way — disabled, a known contact logs `skipped` and the group falls through to manual instead of emitting an `email.request` the no-op would swallow.
  The `/scheduler` panel renders these flags, so they are declared on the **`web`** service as well as `scheduler` (docker-compose.yml). A `SCHEDULER_*` var set on only one of the two makes the panel disagree with the process it describes.
- **EUDAMED (S2.3 stage 2).** `SCHEDULER_EUDAMED_CERTREGISTER_ENABLED` (default off) gates the monthly whole-register pull (`eudamed.certregister`, 16 calls) — an operator turns this on deliberately, not because a downstream consumer is missing. `SCHEDULER_EUDAMED_CERTREGISTER_INTERVAL_DAYS` (default `30`) and `SCHEDULER_EUDAMED_SWEEP_INTERVAL_DAYS` (default `90`) size the two cadences (certificates monthly, device sweeps quarterly, Denis 2026-08-26 — certificates carry five-year validity and drift slowly, device catalogues move slower still than the ~38 pages it costs to re-read one). There is no enable flag for marking a sweep due: the scheduler always computes `eudamed_sweep_state.due_at` when a manufacturer holds a trusted SRN and has gone stale — it never enqueues `eudamed.sweep`, which only a human release button can do (see below). **Unlike the ticks above, neither shows up on the `/scheduler` panel** — that page's tick list (`web/scheduler_view.py`) was not extended for either; the due list itself (`/manufacturers/sweep-due`) is the only UI visibility this cadence has today.
- `UPLOAD_MAX_MB` (default `25`): size cap enforced by `POST /upload` before the file is spooled into `upload_inbox`.
- `DISCOVER_HOLD` (default off): corpus-first cold start. RESOLVE builds groups but emits no `discover.group`, so a first ingest does not web-search for documents already sitting in `imports/dentalia-sftp`. Held emissions are counted on the job result as `discover_held` (the settled "already covered" suppression leaves it 0, so the two are distinguishable). Unwind by dropping the variable and running `python -m app.cli regroup --manufacturer NAME --apply` — discovery then fires only for the groups the corpus did not cover. See PRD v3 §11.
- **EMAIL inbound (`email.poll`, S2.4).** `SCHEDULER_EMAIL_POLL_ENABLED` (default off) gates emission of the mailbox poll — leave off until the client provisions the compliance inbox (GAP G8); enabling it without credentials is harmless (`ImapEmailAdapter` raises and the rung is a clean skip, never a dead job). `SCHEDULER_EMAIL_POLL_INTERVAL_HOURS` (default `6`) sizes the poll window (`dedupe_key email.poll:{YYYY-MM-DD:HH-band}`). Mailbox: `EMAIL_ADAPTER` (`imap` default | `fake` for tests), `IMAP_HOST` / `IMAP_PORT` (993) / `IMAP_SSL` (true) / `IMAP_FOLDER` (INBOX), `EMAIL_POLL_MAX_MESSAGES` (200). Credentials `IMAP_USER` / `IMAP_PASSWORD` are secrets (top of `.env`, alongside the API keys), never surfaced as tuning keys. The poll **expands archives** before anything else (2026-08-20: a supplier sending `documents.zip` is sending documents; the magic-byte gate refuses the archive itself, so without expansion the PDFs inside were counted `non-pdf` and lost). One level only, bounded by `EMAIL_ZIP_*`; a zip-bomb is refused on declared sizes before decompression; nested archives are refused; members are recorded as `documents.zip!inner.pdf`. It then archives PDF attachments (skipping MSDS/business noise and non-PDFs, all counted) and enters the spine at `extract.doc`; processed emails are visible read-only at `/emails`. **Body summarisation** (`EMAIL_SUMMARY_ENABLED`, default on; `EMAIL_SUMMARY_MIN_CHARS` **0**, `EMAIL_SUMMARY_MAX_CHARS` 6000, `EMAIL_SUMMARY_MAX_TOKENS` 200, model `MODELS_EMAIL_SUMMARY`): the body is read at poll time, compressed by the cheap tier, and dropped — only the summary reaches `email_poll_log` (migration 030, invariant 12). One call per message, ever: the UID guard means a message is never polled twice, and a failed summary still records its row rather than retrying. **If there is a body it gets a summary, however short** (Denis, 2026-08-21): the floor was 120, then 40, on the theory that a one-liner is already its own summary — but the body is never stored, so skipping the call left the row blank, and half the fixture mailbox rendered emptier than the long messages beside it. `EMAIL_SUMMARY_MIN_CHARS` survives as a throttle for a noisy mailbox, so `too-short` stays in the vocabulary; nothing sets it. `summary_status` says why a summary is missing (`disabled` / `no-body` / `too-short` / `llm-error`), and the measured tokens and USD sit next to it — so "the summaries are negligible" stays a checkable claim — **measured 2026-08-21** against the GreenMail mailbox: 6 messages in, **6 summarised**, every one carrying an intent badge. The prior reading (2026-08-20, floor 40) was 10 in / 5 summarised for $0.003243 total (~$0.00065 each, 471-506 in / 30-36 out). Turning it off records `disabled` and makes no call. Only the INBOUND half exists — the outbound flow is the next slice (spec `docs/specs/email.md`), and per the client's 2026-08-20 ruling it will **draft only, never send** (spec §7.1). Mailbox named by the client: `mdr@dentalia.si`.

- **Testing the mailbox without a real account (fake IMAP).** The IMAP wire path cannot be covered by unit tests (`FakeEmailAdapter` never opens a socket), so a GreenMail server stands in. It is in-memory: restarting it empties the mailbox, and `tools/seed_fake_mailbox.py` is the source of truth for what it holds.

  ```
  docker compose --profile email-dev up -d greenmail
  python tools/seed_fake_mailbox.py            # six fixtures: DoC, MSDS, scan, non-PDF trap, no-attachment, duplicate
  python tools/seed_fake_mailbox.py --fresh    # mint new Message-IDs so an already-polled mailbox reprocesses
  python tools/seed_fake_mailbox.py --shapes   # reply / forward / ZIP / oversize — NOTE: generated PDFs, new hash every run,
                                               # so each re-seed archives four more documents and COSTS extraction (~$0.09)
  python tools/seed_fake_mailbox.py --extra PATH.pdf   # add a PDF the archive has never seen
  ```

  Then point a run at it and process exactly one job, so an emitted `extract.doc` is NOT claimed and nothing is spent:

  ```
  docker compose run --rm --no-deps worker \
    python -m app.cli enqueue email.poll 'email.poll:manual' --payload '{"mailbox":"INBOX","since":"manual"}'
  docker compose run --rm --no-deps -e IMAP_HOST=greenmail -e IMAP_PORT=3143 -e IMAP_SSL=false \
    -e IMAP_USER=dokumentacija@dentalia.si -e IMAP_PASSWORD=dentalia worker \
    python -c 'from app import db; from app.config import load_config; from app.workers.runner import run_once; cfg=load_config();\
               conn=db.connect(cfg.connection.database_url).__enter__(); print(run_once(conn, "manual"))'
  ```

  `IMAP_SSL=false` on port 3143 is deliberate: GreenMail's IMAPS certificate is self-signed and `imaplib.IMAP4_SSL` verifies, so the plain port keeps the exercise about IMAP rather than about certificates. The login is the FULL address because the service sets `-Dgreenmail.users.login=email`, matching how the real account will behave. Verified end to end 2026-08-20: login, SELECT, UIDVALIDITY, `UID SEARCH UNSEEN`, `UID FETCH`, both reprocess guards (the Message-ID guard caught six re-delivered messages under new UIDs), archive, `extract.doc` → document 814.

## Tests

Canonical run, in-container at the real 3.12 runtime, against the compose Postgres:

Start the stack once, then run everything through the wrapper:

```bash
docker compose up -d
docker compose --profile test up -d test   # long-lived; add --build when deps change
./scripts/test.sh                          # whole suite
./scripts/test.sh tests/test_web.py        # one file
./scripts/test.sh tests/test_web.py::test_kpi_spend_converts_and_surfaces_unpriced_calls
./scripts/test.sh --heavy                  # machine is loaded
```

`scripts/test.sh` picks the parallelism from the shape of the arguments: `-n 4
--dist loadfile` for the whole suite, `-n 2 --dist loadfile` for paths, `-n 0`
for a single test id (xdist's worker setup costs more than the test). It execs
into the already-running `test` container, never `run` and never `--build`, and
refuses to start the stack for you. A flock semaphore in `/tmp/pytest-slots`
caps concurrent runs at 4 across every worktree on the machine.

`--dist loadfile` is a **wall-clock** choice, not a correctness one. Measured
back-to-back 2026-08-26, all 2301 green: loadfile 154s, load 191s, worksteal
191s, ~490s single-process. `loadfile` wins because it keeps a file's tests on
one worker and so reuses that worker's warm fixtures.

`tests/test_web.py` is **not** order-dependent — verified under reverse order,
two shuffled seeds, `--dist load` and `--dist worksteal`. That is a property of
the current tree rather than a law: it holds only while every seeding fixture
takes `conn` (whose teardown runs conftest's `_RESET_SQL`) and `_RESET_TABLES`
names every table anything writes. Both halves have failed before — on
2026-08-26 `data_anomaly` and `service_heartbeat` were each missing from that
list and each produced real order-dependent failures.
`tests/test_queue.py::test_reset_clears_every_table_the_old_cascade_reached` is
the guard, and it only reaches tables linked by FK to its seed set, so a new
leaf table needs a `seeds` entry too. conftest folds `PYTEST_XDIST_WORKER` into
the database name, so each worker gets its own.

The repo is bind-mounted into the test container, so uncommitted changes are tested without an image rebuild. `tests/conftest.py` creates a throwaway database (drop + create with FORCE), applies all migrations once per session, clears the registry and queue tables between tests, and drops the database at session end. It never touches the real `dentalia` database.

The database name is **per-run**: `dentalia_test_{pid}` by default, overridable with `DENTALIA_TEST_DB`. Every connection URL (`DENTALIA_TEST_URL`, `DENTALIA_TEST_API_URL`) is derived from that one name, so the URL a test connects to and the database the fixture drops can never disagree. That is one of the two things that let two suites run at once — two shells, or two git worktrees. The other is an advisory lock: a per-run database is not enough on its own, because a ROLE is cluster-global and `migrations/007`/`008` create and alter `dentalia_api`, so two runs migrating at the same instant collide with `tuple concurrently updated` and one of them loses its session fixture (measured 2026-08-25: 4 of 12 concurrent runs failed; 0 of 16 with the lock). conftest takes `pg_advisory_lock` on the ADMIN connection — not the test database's, since advisory locks are scoped per database — around create-and-migrate. Before it, the name was a hardcoded constant that only the connection URLs could override, so a second run would `DROP ... WITH (FORCE)` the first run's database mid-flight and produce `ERROR at setup` plus phantom failures indistinguishable from a real regression.

Host-side runs also work if you have a venv with `pip install -e ".[dev,extract,resolve,ingest,discover,fetch,web]"` (the same extras the test image installs — `Dockerfile:27` + `:57`; `playwright`'s browser binaries are *not* needed, handler tests inject a fake fetcher) and a Postgres reachable at the conftest defaults (`postgresql://dentalia:dentalia@localhost:5432`, i.e. the compose Postgres). Point elsewhere with `DENTALIA_TEST_ADMIN_URL` / `DENTALIA_TEST_URL`. The container path is what counts for green/red, since it runs the pinned runtime.

Test data comes in two layers:

- **Committed fixtures** (`tests/fixtures/corpus/`, ~22 labeled real PDFs + `corpus_manifest.py`): always present, run everywhere including CI.
- **Local corpus** (`imports/dentalia-sftp`, 1307 PDFs, gitignored): corpus-dependent tests skip cleanly when it is absent. Override the location with `DENTALIA_CORPUS_ROOT`.

LLM-dependent tests use recorded fixtures and a fake client; live API calls are gated on `ANTHROPIC_API_KEY` being set.

## Client guide (docs/guide)

The compliance guide Dentalia reads. Markdown is the source; the HTML is
generated and must never be hand-edited.

```
python3 scripts/build-guide.py              # both languages, both output shapes
python3 scripts/build-guide.py --lang sl    # one language
python3 scripts/build-guide.py --check      # parse only, write nothing
```

No dependencies and no stack — it is plain Python over the markdown, so it runs
in a bare checkout.

**To change the guide:** edit the `.md` file, edit its `.sl.md` counterpart, run
the script, commit both the markdown and the regenerated bundles. A page changed
in one language only fails `tests/test_docs_sets.py`.

| Output | Committed | What it is |
|---|---|---|
| `docs/guide/**/*.md` | yes | The source. English and Slovene side by side |
| `docs/guide/dentalia-guide-EN.html` · `-SL` | yes | The whole guide in one offline file. **This is what gets sent to the client** |
| `docs/guide/html/{en,sl}/` | no, gitignored | One page per file sharing `assets/`. Copies the fonts out of `web/static`, so it is regenerated rather than stored |

Styling is lifted from `web/static/css/style.css` — same palette, same fonts,
same sidebar. If the app's look changes, the CSS block in `scripts/build-guide.py`
is the one place to update.

## Analysis tooling (dev-time, read-only)

`python -m tools <cmd>` runs reproducible, zero-cost analysis over `imports/` using the pipeline's own code — no DB, no network, no LLM. Output lands in `analysis-out/` (gitignored) as a dated CSV/JSON dataset + markdown report.

```bash
python -m tools corpus    --root imports/dentalia-sftp             # T0 coverage over the PDF corpus
python -m tools claims     --root imports/dentalia-sftp            # doc's documented numbers vs measured
python -m tools catalogue  --export "imports/Artikli 3.7.2026.xlsx"  # BC export profile (md_flag, mfr_ref, codes)
python -m tools crossref   --root imports/dentalia-sftp --export "imports/Artikli 3.7.2026.xlsx"  # code→manufacturer candidates
```

Needs the `extract` extras for `corpus`/`claims` and `ingest` extras for `catalogue`/`crossref` (both already in the worker image). `crossref` emits candidates only — a human promotes CONFIRMED codes into `Ingest` config.

## Database access

```bash
docker compose exec postgres psql -U dentalia -d dentalia
```

Useful checks: `select type, status, count(*) from job group by 1,2 order by 1,2;` and `select * from extraction_spend;` (the cost rollup view).

### Resetting the registry

**A registry TRUNCATE takes `fetch_log` with it, whatever your KEEP list says.**
`fetch_log.doc_id` has a foreign key to `document`, so

```sql
TRUNCATE document, item_document, evidence, manual_task, audit_log RESTART IDENTITY CASCADE;
```

empties `fetch_log` too — measured 2026-08-17, 330 rows gone moments after being
listed as KEEP. That table is the one that makes a re-derivation free: it is the
ledger invariant 6 reads to decide that a file has not changed, so without it
every document is re-fetched and re-parsed.

Dump it before any reset, and dump `document_text` with it (parsed text per
content hash — regenerable only by re-opening every PDF):

```bash
docker compose exec postgres pg_dump -U dentalia -d dentalia \
    -t fetch_log -t document_text -t extraction_attempt -t extraction_cost \
    --data-only > reset-keep-$(date +%F).sql
```

The 2026-08-17 recovery cost nothing only because `imports/` was still on disk
and a backfill ledger is derivable from the files themselves (`url_normalized`
= `str(path.resolve())`, `content_hash` = `sha256(bytes)`, `source='backfill'`);
a re-hash of 330 PDFs reproduced 330 rows and 328 distinct hashes exactly. That
route does not exist for anything fetched over the network.
