# Troubleshooting

Known failure modes, their actual causes, and fixes. Ordered roughly by where they bite: stack startup -> queue -> web -> tests -> extraction. If you hit and diagnose something new that costs more than a few minutes, add it here.

## Stack startup

**Postgres crash-loops or initdb fails (WSL2).**
`PGDATA_HOST` points at a Windows-mounted path (`/mnt/c/...`). Postgres does not work on the 9p filesystem. Use a WSL-native ext4 path (e.g. `$HOME/pgdata/dentalia`). A `lost+found` in the mount root is fine: data lives in a `pgdata/` subdirectory precisely so initdb's empty-dir check does not collide with it.

**Manual `docker build .` produces an image that runs pytest instead of the worker.**
The Dockerfile's last stage is `test`, and an untargeted build resolves to the last stage. Build with `--target app`. Compose already sets the target.

**`migrate` exits non-zero and worker/web never start.**
Worker and web gate on `service_completed_successfully`. Read `docker compose logs migrate`. Migrations are plain SQL applied in filename order by `app/db.py`, each tracked in `schema_migrations`; an applied file is skipped on re-run, so after fixing the broken SQL just run `docker compose up -d` again (or `docker compose run --rm migrate`). Never edit an already-applied migration to change schema; add a new numbered one.

## Queue

**Jobs sit in `pending` forever.**
Either no worker is running (`docker compose ps`, `docker compose logs worker`) or the jobs are in a backoff window after failures. Check `attempts` / `last_error` on `/jobs/{id}` or via `python -m app.cli inspect`.

**A job "disappeared".**
It dead-lettered after `max_attempts`. Dead jobs are listed on `/dead` and can be re-enqueued from there at interactive priority. Dead is terminal, so its dedupe key is reusable.

**Enqueue is rejected / silently deduped.**
Dedupe uniqueness is scoped to *active* jobs only (`pending`,`running`,`failed`; Invariant 8). If your enqueue does not land, an active job with the same dedupe key already exists; find it on the System status board (`/status`). Terminal (`done`/`dead`) jobs never block.

**A handler needs to emit a job type that does not exist.**
Stop. Job types are a closed enum (Invariant 7): new tag = PRD change + migration first. `001_queue.sql` created the enum but is no longer the whole list — tags have been added by `013`, `041`, `054` and `062`. Read the enum itself (`SELECT unnest(enum_range(NULL::job_type))`), or `app/handlers/noop.py`'s `JOB_TYPES`, which a test holds to it.

**A KOMET `backfill.scan` dead-letters and nothing files for the whole manufacturer.**
One of the playbook's declared `coverage_map.sources` index files (the xlsx or the phase-2 docx) is missing or unreadable in this dump — `backfill.scan` raises `BackfillError` before archiving or enqueuing anything, so a single absent index blocks all 102 of Komet's declarations, not only the coverage-map linking. Re-sync the corpus dump with both index files present and re-run the scan (see `docs/runbook.md`, "Komet coverage map"); the failed run never wrote to `fetch_log`, so there is nothing to clear first.

## Business Central

**`bc.push` reports `withheld` for everything and PATCHes nothing.**
Working as designed. `bc.write_enabled` defaults to false everywhere, and off means the handler still computes the diff and reports it as `would_send` — which is exactly what the bulk preview at `/bc-push` reads. Turning it on is a deliberate act on the one machine that runs it for real.

**Every item comes back `unprocessed`.**
`unprocessed` means the pipeline holds no `item_document` row for that `item_ref` at all, and an item we have never processed is never written — "we do not know" must not reach BC as "no". Check the item on `/items/{item_ref}` first; if it genuinely has no links, the fix is discovery, not the push.

**An item is `unchanged` but you expected a write.**
The diff is against the last **accepted** value per field in `bc_push_log`, not against BC. If the value truly is the same as the one BC took, there is nothing to send. If you believe BC has drifted underneath us, that is what the `bc.push-drift` cron does not catch — nothing reads the fields back, by design, so a value changed inside BC is invisible here.

**Everything comes back `absent` (404).**
The item is not on the `dataitems` page. That page is "a subset" and nobody has told us of what, which is why a 404 is counted and sampled separately rather than folded into failures — the count is the answer to that question. A 404 is not treated as accepted, so the value goes again if the page is ever widened.

**A BC request times out.**
BC is at `mail.dentalia.si:7048` and admits only the server's address (`91.98.42.140`); from anywhere else, a laptop included, the connection times out rather than being refused. `BC_BASE_URL` must use `mail.dentalia.si`: `denwebnav`, the host in b-s.si's original links, is incorrect from cw.

**A BC job fails with `BcAuthRejected`.**
BC refused `BC_USERNAME` / `BC_PASSWORD`. The worker process remembers the refusal and does not send the same pair again, so the domain account is not locked out by retries; every later BC job fails immediately with the same error until the value is fixed **and the worker restarted**. Check the username is `DOMAIN\user` and single-quoted in `.env`. A plain `401` from `curl -u` or `curl --ntlm` proves nothing: BC ignores both, it only accepts NTLM inside `Negotiate`.

**`bc.push` fails with `BC_WRITE_ENABLED is on but BC_BASE_URL is empty`.**
Writes were switched on without the endpoint. Set `BC_BASE_URL` in `.env` and `docker compose up -d worker`.

**The drift cron enqueues nothing although writes are on.**
It needs its own switch too: `SCHEDULER_BC_PUSH_DRIFT_ENABLED=true` on `worker` (and `web`, for the `/scheduler` panel). Deliberate: the first bulk apply is checked before BC is filled automatically.

**A boolean is `false` and the review card shows amber.**
Not a contradiction. `app/bc_fields.py` is deliberately not `compliance.cell_state`: the card's `expiring` and `review-due` are a person's "look at this", while BC gets a boolean an ERP acts on — a document inside its stated validity is `true` however soon it lapses. And a passed date falsifies only when `document_effective_expiry.basis` is `stated` or `inherited`; `staleness` is our own review horizon and never falsifies.

## Web UI

**Permission denied on INSERT/UPDATE from the web process.**
Working as designed, not a bug. The web container connects as `dentalia_api`, which has zero write grants on `document` / `item_document` / `evidence` (Invariant 1, migrations 007/008). Registry writes happen only via `gate.apply` jobs executed by a worker. If a new web feature needs a write, it needs a job, not a grant.

**Web unreachable from another machine.**
Host bind is `127.0.0.1:${API_PORT}` on the `caddy` service on purpose (G3 v0 — HTTP Basic in front of `web`; `web` itself publishes no port). Do not open the bind or publish `web`'s own port without moving the auth boundary with it. `WEB_HOST=0.0.0.0` inside the `web` container is a different thing (container-namespace bind so `caddy` can reach it) and is correct.

**401 on every page except `/healthz`.**
Expected — G3 v0. Set `DENTALIA_WEB_USER` / `DENTALIA_WEB_PASSWORD_HASH` in `.env` (generate the hash with `docker compose run --rm caddy caddy hash-password --plaintext '...'`) and authenticate with those credentials, not the Postgres ones.

**A button answers 403, "This action is for operators."**
Working as designed since 2026-09-15 (office UI redesign, spec § 7 P5b, D3).
The writes behind the Operator menu group are refused to a login outside
`WEB_OPERATOR_USERS` -- the playbook save, code claim, revert, probe and crawl,
every onboarding write, `POST /bc-push/apply`, the scheduler **Run now** and
**Arm**, the two `/dead` per-cause re-runs, and `POST /ingest`. The full table
is in `docs/dev/config-reference.md`. Their pages stay reachable; only those
presses are refused, and nothing is enqueued when one is, because the guard is a
route dependency that runs before the handler. `/import` apply,
`/dead/retry-timeouts` and `/dead/search-again` are never refused: they are the
office's own work. Two things to check if this fires on someone it should
not: is the login spelled EXACTLY as in `WEB_OPERATOR_USERS` (matched
case-sensitively against `X-Forwarded-User`, which is the Caddy username), and
is the key actually set on the **`web`** service (`docker compose exec web env |
grep WEB_OPERATOR`)? An EMPTY list makes everybody an operator, so this refusal
cannot appear at all until someone has named at least one person.

**`/item/{ref}?k=…` 404s, or `/api/*` answers 401, with a key you believe is right.**
Not a routing bug — the app's own caller-class policy (`web/access.py`), which Caddy no longer stands in front of for these two paths. Check, in order: is `WEB_BC_LINK_KEY` / `WEB_API_KEYS` actually set on the **`web`** service (`docker compose exec web env | grep WEB_`)? Empty disables the class outright, so `/item/*` 404s for every key and no `X-API-Key` is a service credential. Is the key you sent in the list — `WEB_API_KEYS` is comma-separated, and surrounding whitespace is stripped but nothing else is. `/item/*` answers **404 rather than 403** by design (a 403 would confirm the item exists), so "not found" here usually means "wrong key", not "wrong item". A `service` key reaching `/items`, `/staging` or `/archive/*` is a **403** and is also by design: those are staff-only. `docker compose up -d --build web` after changing the code, and remember compose env changes need `up -d`, not `restart`.

**A 404 or 403 comes back as an HTML page, not `{"detail": ...}`.**
Deliberate since 2026-09-14 (office UI redesign, spec § 9 P7a). `_error_response` in
`create_app` picks the body, for an `HTTPException` and for an unhandled 500
alike: `/api/*`, `/static/*` and any caller `web/access.py` does not classify as
`staff` still get the JSON and the response's own headers; a request carrying
`HX-Request` gets the `_result.html` error partial; everything else gets
`error.html` with the menu on it. A script outside `/api/*` that wants JSON
should send `Accept: application/json` — `_wants_html` checks `text/html`
first, so naming JSON is enough even alongside `*/*`; only an absent header or
a bare `*/*` is read as a browser. The status code is never rewritten, so
anything checking the code rather than the body is unaffected, and a 500's body
is always the fixed `{"detail": "internal server error"}` — the real exception
is in the `web` container's log, never on the page.

**`localhost:8000` does nothing but `127.0.0.1:8000` works (and `curl` works either way).**
The port was published on IPv4 only (`127.0.0.1:${API_PORT}:8000`), while Windows resolves `localhost` to **`::1` first**. The browser tried `[::1]:8000`, found nothing listening, and reported the site as down; `127.0.0.1` skipped the question. `curl` and PowerShell both worked throughout, because they fall back to IPv4 — which is exactly what made this look like an application or credentials problem rather than a bind. Fixed 2026-08-17 by publishing both loopback stacks in `docker-compose.yml`; `ss -ltn | grep 8000` should show `127.0.0.1:8000` **and** `[::1]:8000`. Still loopback-only, so the G3 posture is unchanged.

Diagnosed twice as auth before the bind was checked. The lesson generalises: when one client reaches a service and another does not, compare what each one *dialled* before theorising about what the service *did*.

**A login is refused in the browser but works from `curl`.**
Caddy access logging is on (`Caddyfile`), so every attempt prints a line with `status` and `user_id`. Reproduce the failure, then:

```
docker compose logs caddy --since 2m 2>&1 | grep "handled request"
```

Three outcomes, three different problems: `status=401` with an empty `user_id` means credentials arrived and were rejected (often a browser auto-filling a saved credential for `localhost:8000` from another dev app — try a private window, then clear saved localhost passwords); `status=200` means it is being served and the window is showing something stale; **no line at all** means the request never reached the proxy — check the address the client actually dialled (the IPv6 case above) and the scheme, since `https://localhost:8000` fails outright as Caddy serves plain HTTP here.

**Editing `Caddyfile` appears to do nothing (`config is unchanged` on reload).**
The file is a bind mount, and an editor that writes-and-renames replaces the inode, so the running container keeps reading the old one. `docker compose restart caddy` does not help and `caddy reload` reports `config is unchanged` — it is reading the stale file, truthfully. Recreate the container instead:

```
docker compose up -d --force-recreate caddy
docker compose exec caddy grep -c "output stdout" /etc/caddy/Caddyfile   # confirm it sees the edit
```

Same trap as the migrate image (`docs/runbook.md`): confirm the container sees the change, never assume it from the edit.

**Web login fails as `dentalia_api`.**
The dev password is set by migration 008 (`DENTALIA_API_PASSWORD`, default `dentalia_api`). If you changed the env var after the migrate ran, re-run migrate or ALTER ROLE manually.

**`/data-quality` counts keep climbing without new rows appearing.**
Expected. `data_anomaly` (migration 019) is a standing ledger keyed by `(kind, subject)`, not an event log: `record_anomalies()` (`app/results.py`) upserts on that conflict, bumping `seen_count` and `last_seen` instead of inserting a duplicate row, so the table stays the size of the problem rather than the size of the corpus. The kind vocabulary is closed (`ANOMALY_KINDS` in `app/results.py`) — any handler may write an anomaly (Invariant 1 only covers `document`/`item_document`/`evidence`), but only through `Result.anomaly()`, which raises on an unregistered kind rather than accepting a new string on the spot. Filter the page with `?kind=`.

**The document frame in an open Review row shows an error instead of the PDF.**
The frame is `/documents/{id}/file`, and a JSON `{"detail": "archived file is not reachable from this server"}` there is that route's 404: none of its three passes (the stored path, `WEB_PATH_REWRITES`, a hash-verified search under `WEB_ARCHIVE_ROOT` / `WEB_IMPORTS_DIR`) found the bytes. It is the same answer **Open full size** gets in a new tab, and it is never cached (only a served file carries `Cache-Control`). Check that the archive volume is mounted on `web` and that the document's `archive_url` (Technical details, Record) lies under one of those roots.

**A DoC shows no expiry date.**
Correct behaviour, not a bug. MDR Annex IV requires no expiry on a Declaration of Conformity itself — the real renewal date lives on the notified-body certificate it cites (Article 56's 5-year cap), inherited via `document.cert_doc_id` (Task 4: `app/handlers/validate.py` `_resolve_cited_certificate` + `app/handlers/gate.py` `_backresolve_citing_docs`). A Class I device has no notified-body certificate at all and legitimately never expires. Since 2026-08-19 every page that shows an expiry -- `/items`, `/items/{item_ref}`, `/documents`, `/documents/{doc_id}` and `/expiry` -- reads the one definition, the `document_effective_expiry` view (migration 027), and renders WHICH rule produced the date: `stated` (the document's own), `inherited` (the production or superseded certificate it cites -- a REJECTED one lends nothing, and the view enforces that on its join) or `staleness` (a DoC's five-year review horizon, Dentalia's rule, not an expiry the document claims). So a blank here now means all three produced nothing: no date of its own, no usable cited certificate, and no issue date to review from. Do not "fix" this by inventing an expiry on the declaration, and do not print a `staleness` date without saying it is one.

## Tests

**Corpus tests skip.**
Expected off Denis's machine: the local corpus (`imports/dentalia-sftp`) is gitignored. Committed-fixture tests (`tests/fixtures/corpus/`) still run and are the CI signal. Set `DENTALIA_CORPUS_ROOT` if the corpus lives elsewhere.

**`test_web.py` errors on import.**
The venv is missing the optional web extras. Install `pip install -e ".[dev,extract,web]"`, or use the in-container run, which installs everything.

**Host pytest cannot connect to Postgres.**
conftest defaults to `postgresql://dentalia:dentalia@localhost:5432`. Ensure the compose Postgres is up (it publishes 5432) or point `DENTALIA_TEST_ADMIN_URL` / `DENTALIA_TEST_URL` at your server.

**A previous run left `dentalia_test` behind.**
Harmless: conftest starts with `DROP DATABASE IF EXISTS dentalia_test WITH (FORCE)`. Manual drop is also safe; it is a throwaway.

**Known xfails.**
`test_t0_corpus.py`'s `KNOWN_T0_GAPS` list documents pinned, known-deterministic-gap extraction cases as xfail (not flaky tests) — reasons live alongside each entry. All four S0.4-era entries closed in S1.5 (layout-template engine + generic regex fixes; see `docs/specs/t0-layout.md` §0), so the list is currently empty — a future real gap re-populates it the same way.

## Extraction / LLM

**T1/T2 extraction does nothing.**
Live tier calls are gated on `ANTHROPIC_API_KEY`. Without it, only T0 runs; tests use recorded fixtures.

**Batch jobs seem stuck.**
Batch API state lives in the `batch_ref` side table (payloads are immutable, Invariant 9); the handler polls at `BATCH_POLL_INTERVAL_S` (default 900s), so "stuck" is often just the polling window. Check `batch_ref` rows and worker logs before suspecting a bug.

**Costs look wrong or a model prices as NULL.**
Per-call measured costs land in `extraction_cost` (migration 010, `extraction_spend` rollup view). An unknown model id is kept with NULL cost and a WARNING log; pricing constants live in `app/extract/economics.py`.

**A document was re-extracted with different values and the old ones survive.**
Working as designed: never downgrade, never delete (Invariant 4). Older candidates flag rather than overwrite; null dates are `downgrade-uncomparable`. Supersession decisions belong to VALIDATE/GATE, not to whoever noticed.

**`/documents/{content_hash}/text` shows "(no text layer: this document is a scan)".**
`app/extract/text_store.py`'s `store_text` writes `document_text.source='none'` for two different failures and the row itself is how you tell them apart. A genuine scan (PDF opens, `pdfutil.is_scan()` finds nothing extractable) writes a `document_text` row with `source='none'` and a real `pages` count, and `app/handlers/extract.py` then records the `no_text_layer` anomaly (`app/results.py`) once the job finishes — check `/data-quality?kind=no_text_layer`. A PDF that cannot be opened at all (corrupt file, wrong content-type, PyMuPDF exception) never gets a `document_text` row at all — the handler opens the archived file itself (one open per execution, the same document then feeds both `store_text` and the tier ladder), so an open failure returns straight from `handle_extract_doc` with a WARNING log and an `unreadable_pdf` count in the job result, deliberately kept outside the closed `ANOMALY_KINDS` vocabulary pending a decision on whether it deserves its own kind. Called on its own (not from the handler) `store_text` still opens for itself and returns `source='none'`/`pages=0` without writing a row. Either way nothing downstream (`validate.doc`) is emitted — there is nothing to validate. This is common, not exceptional: roughly 201 of the 405 PDFs in the DENSTPLY corpus folder are image-only scans.

**A DoC's certificate reference never resolves (`cert-unresolved` flag, document stuck at `staged`).**
`app/handlers/validate.py`'s `_resolve_cited_certificate` is best-effort: it looks for a certificate we already hold (`document.type IN ('EC','ISO')`) whose `cert_number` matches the DoC's own extracted `cert_number` or `referenced_docs`. No match — because we haven't fetched/gated that certificate yet, or the DoC cites no number at all — appends `cert-unresolved` to the candidate's flags. It is not in `gate.py`'s `BLOCKING_FLAGS`, so it does not force `/manual`; it just rules out `production` (gate's two-level disposition requires *no* flags for a document to land on `production`) and caps it at `staged`. Check whether the cited certificate is in the registry yet (`/documents?type=EC` or `?type=ISO`, search by cert number). Once that certificate is gated at any status, `_backresolve_citing_docs` retroactively fills `cert_doc_id` on every DoC that cited it and was still NULL — automatic, no re-validation needed — after which `/expiry` and `/documents/{doc_id}` start showing the inherited `validity_to`.
