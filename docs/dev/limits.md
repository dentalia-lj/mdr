# Limits

**Status:** live. Every entry verified against the tree on 2026-08-31.

What the system does not do. Split into things that are **deliberately not built
yet**, things that are **built but not wired**, things that **look configurable
and are not**, and rules that are **enforced in code rather than by the
database** — the last group being where a schema-level assumption will bite you.

This file exists so a developer stops looking. If something here becomes untrue,
delete the row.

## Deliberately not built

| Thing | Where it stops | Note |
|---|---|---|
| `?include_superseded` has no real data behind it | `item_document_history` (migration 070), `web/item_docs.py` | Built and tested 2026-09-16, but **0 documents carry `status='superseded'`** in the database (measured that day: 439 filed, 244 staged, 204 production, 4 rejected, 0 superseded). The pipeline has not run in production, so GATE has never superseded anything — neither the task-5 auto-file path nor a human `approve` has fired. Every test seeds the chain by hand. The first real rows are also the first chance to find out whether the payload is what a consumer wanted. Delete this row once a production supersession exists |
| Drive archive | `GoogleDriveStore.put`, `app/adapters/storage.py:73`, raises | Blocked on gap **G7**: service-account vs OAuth and the root folder are client input. When G7 lands only this class changes — handler, path logic and tests are untouched (invariant 11) |
| BC OData ingest | `BcApiAdapter.read`, `app/adapters/source.py`, raises unless a client or records are injected | **Partly corrected 2026-09-14.** A real client IS built when `BC_BASE_URL` is set (`app/handlers/ingest.py`), so a CLI `enqueue ingest.run` whose `ref` is an absolute OData URL reaches the endpoint; with `BC_BASE_URL` empty it raises. Three things still block a cutover: (1) the `/ingest` FORM sends `ref` as `{company, delta_since}` and nothing turns that into a URL, so the browser option cannot work and dead-letters (`[ingest-bc-odata-always-fails]`); (2) `LJ_ODATA_PROFILE` was corrected on 2026-09-07 and five of six keys now match the captured payload, but `manufacturer_raw` reads `pteManufCodePrimary`, which is **present and empty** on all three records of `docs/samples/bc-api-2026-09-02-allitems.json` while `manufacturerCode` carries `011` — and the guard checks presence, not emptiness, so the wrong one mirrors a catalogue with no manufacturer and reports success (`[odata-manufacturer-property-disputed]`); (3) no `$filter` is ever built, so `delta_since` is inert and every run is a full read. The endpoint is also LAN-only over plain HTTP with an unverified auth scheme. See [`../2026-09-03-bc-api-integration.md`](../2026-09-03-bc-api-integration.md) and [deployment.md](deployment.md) § 6.4 |
| Live T1 name adjudication (RESOLVE) | `_default_adjudicator`, `app/handlers/resolve.py:262`, raises | Followup `resolve-t1-ranking`. A second, separate unwired ranker. The caller records `t1_error` and keeps the suggestion, so work is never lost — an un-adjudicated item stages its candidate cluster for a human instead |
| C4 manufacturer binding at RESOLVE time | `_apply_manufacturer_bindings`, `app/handlers/resolve.py:248`, is inert and returns 0 | Followup `resolve-c4`. **The job-type handbook describes this as built** — "a newly-resolved item receives the mfr-scope link at RESOLVE time" — and it is not. Invariant 1 forbids RESOLVE writing `item_document`, so the link write is a GATE responsibility. RESOLVE's real contribution to C4 is the canonicalization above it |
| Item links for a blank-class manufacturer | `_derive_mfr_scope_links` filters on `md_flag IS TRUE`, so an item whose BC device class is blank gets NO link | Ruling `[mfr-bind-empty-class]` (Denis, 2026-08-27): a blank class means UNKNOWN, not "not a device", so guessing a link is worse than writing none. Since migration 053 the bind itself still happens — `document.canonical_manufacturer` records the decision and the links follow when the class is filled in. Live at the time of writing: 4265 items `TRUE`, 11693 `NULL`, 0 `FALSE` |
| The archive gate skips `backfill.scan` | `archiving.is_document` is called by FETCH and UPLOAD (form + handler); `backfill.py`'s two `store.put` sites are not | Deliberate, 2026-09-02. `email_poll._is_pdf` already checks bytes on its own (a wider 1024-byte window, because a mail attachment may carry a preamble), so BACKFILL is the only unguarded writer left. Gating it fails 31 tests in `tests/test_backfill_handler.py`, which use opaque bodies (`b"AAAA"`) as corpus PDFs on purpose; the corpus is a trusted SFTP dump, not the open web, and EXTRACT refuses a non-document anyway. Followup `[gate-covers-only-backfill]` |
| Hiding operator PAGES from a non-operator | The menu drops the collapsed **Operator** block for a login outside `web.operator_users` (`web/templates/_pulse.html`, via the `is_operator` Jinja global), and no PAGE changes: `/status`, `/dead`, `/pipeline`, `/playbooks`, `/onboarding`, `/data-quality`, `/audit`, `/scheduler`, `/bc-push` and `/api-reference` all answer that login by URL. Their write routes do not | D3 as written: hidden from the menu, still reachable if you type the address. The risky writes it names ARE refused since P5b (2026-09-15), by `web/access.py::operator_guard` as a route dependency, and fix round 1 the same day widened that to the writes sitting beside them: the playbook revert, probe and crawl, the four onboarding writes, and the two `/dead` per-cause re-runs. That is the whole of the refusal: the pages themselves stay open on purpose. With `operator_users` empty — everywhere today — every login is an operator, sees the block and is refused nothing |
| Refusing the OFFICE's own failure buttons | `dead_retry_timeouts` and `dead_search_again` (`web/app.py`) answer any staff login, by design. The per-cause controls beside them do not: `dead_rerun_group` and `dead_rerun` carry `require_operator` since 2026-09-15, and `web/templates/dead.html` has hidden their buttons from a non-operator since P5a, so the page offers no button it would refuse | D6 as written: "office logins get 'took too long' only. Operators get everything." Trying a supplier timeout again and searching again for a dead address ARE the office's work, and Today posts to both of them, so a guard there would break the Failed split's whole point. Re-queueing work whose cause is still unfixed is the operator half, and it is now refused rather than only hidden |
| "Ask the manufacturer" from Missing documents | A `/missing` card offers upload, two outside links and **Search again**; nothing on it writes to a manufacturer | Out of scope of the office UI redesign (spec § 5, § 11): it needs a new `email.request` reason, which is a handler change. Before a task gets here, the DISCOVER `email` rung writes a request draft (`email.request`) only when `discovery.email_rung_enabled` is on and a contact is known; on dev every `email` rung logged for the groups of the 240 open dead ends reads `email-producer-disabled` (2026-09-11) |
| Sending email | Nothing anywhere sends | The system writes drafts; a person sends them and marks them `sent`. `email.send_policy` was meant to gate draft-vs-auto and is read by nothing |
| Unattended EUDAMED sweeps | `eudamed.sweep` never self-emits: `handle_eudamed_sweep` (`app/handlers/eudamed.py:730-874`) ends without an enqueue, `app/scheduler.py:435-442` says in its docstring that adding one there is the defect it exists to prevent, and the only enqueue site is the release button (`web/registry.py:1816`) | A human releases every run. Standing ruling, not an oversight |

## Built but not wired

| Thing | State |
|---|---|
| `fetch.robots_ttl_hours` | `app/robots.py::check` is wired since 2026-09-03 — called from the crawl rung (`app/handlers/discover.py:491`) and the probe (`app/handlers/playbook_probe.py:136`) — and neither of those passes `ttl_hours`, so for them the cache uses `robots.py`'s own 24-hour default. Since 2026-09-11 `fetch.url` consults it too and does pass the key (`app/handlers/fetch.py`), so the key now configures FETCH's reads and nothing else; the `refused_host` list remains the operator ruling that survives a permissive robots.txt |
| `ImapEmailAdapter` | Built at `app/adapters/email.py:250` and already the default selection (`adapters.email = "imap"`, `app/config.py:200`; `email_poll.py:296` constructs it). What is missing is credentials, gap **G8**: with the `IMAP_*` keys empty `fetch_new` raises `EmailNotConfigured`, reported as an outcome rather than a failure, and `scheduler.email_poll_enabled` defaults `False` on top. With credentials set it also needs `EMAIL_POLL_SINCE`, or it dead-letters rather than read the mailbox's history. No code change stands between the mailbox and this path |
| Five scheduler features | Default off: the email discovery rung, expiry emails, failure re-onboarding, mailbox polling, the EUDAMED certificate register. See [config-reference.md](config-reference.md#scheduler) |

## Looks configurable, is not

Nine config keys have no effect. Full detail and the two distinct failure modes
in [config-reference.md](config-reference.md#keys-that-do-nothing).

The one worth repeating here: **all four `queue.*` keys are shadowed.**
`app/queue.py` declares parameters with the same names and the same defaults, and
no caller passes the config section in. Setting `QUEUE_MAX_ATTEMPTS` or any
`QUEUE_*` variable changes nothing. It has never produced a visible bug because
the numbers happen to match — it would surface during an incident, which is the
worst time to discover it.

**Alerting reaches exactly one place.** `app/alerts.py` POSTs to
`alerts.webhook_url` when a job dead-letters, and nothing else calls it. There is
no alert for a worker that died, a stalled queue, or a cron that stopped firing —
those are still visible only by opening the UI. Added 2026-09-04; before that
nothing in `app/` or `web/` could reach a human at all.

**The spend caps are not caps.** `budget.sweep_cap_eur` (€100) and
`budget.monthly_cap_eur` (€40) are referenced only by the status board's display
dict at `web/app.py:1637,1639`. Nothing in `app/` stops, throttles or defers work
when spend passes either. Followup `[budget-caps-not-enforced]`.

## Enforced in code, not in the database

Assume the schema guarantees these and you will write a broken migration or a
broken repair script.

| Rule | Where it actually lives |
|---|---|
| **Invariant 5** — supersession only within identical `(coverage subject, type, regulation)`; MDR never supersedes MDD | The GATE handler only. `migrations/005_registry.sql:12,39` says so explicitly: it compares two rows and "cannot be expressed as a row CHECK". No trigger was added |
| **Invariant 2** — complete evidence on every production value | `_require_complete_evidence`, `app/handlers/gate.py:237`. The always-required columns are `NOT NULL`, which is the only database-level part |
| **The T1/T2-only page rule** | `evidence.page` is a plain nullable `int` (`migrations/005_registry.sql:70`) with no CHECK. The T0/T3-pageless carve-out is handler logic at `app/handlers/gate.py:262` |
| **The `match_basis` vocabulary** | Documented in comments only. The database enforces just the production cap — `item_document_trusted_basis_ck`, rewritten by migration 021 — not the list of legal values |
| **`data_anomaly.kind`** | Closed vocabulary in `app/results.py`; no DB CHECK |

Invariant 1 is the exception that *is* enforced structurally: `migrations/007_roles_grants.sql` never grants the web role write access to `document`, `item_document`, `evidence` or `audit_log`. Two ruled exceptions exist and neither is a queue handler — see [handlers.md](handlers.md#invariant-1-and-its-two-ruled-exceptions).

## Choices that are not going to change

Listed so nobody re-opens them without reading the reasoning first.

| Choice | Why | Where the reasoning lives |
|---|---|---|
| Sync, no asyncio | The workload is polite-rate fetch plus batched LLM plus cheap logic. Async buys nothing and costs debuggability | CLAUDE.md |
| Raw SQL, no ORM | — | CLAUDE.md |
| Hand-rolled queue, ~150 lines | `pgqueuer` is the documented escape hatch. Do not add it preemptively | `app/queue.py`, [dentalia-v3-v4-system-design.md](../dentalia-v3-v4-system-design.md) |
| `pg_trgm` then `rapidfuzz` | `splink` is the upgrade path, not now | CLAUDE.md |
| No Node, no build step in the UI | Fonts and assets are vendored under `web/static` | `web/static/css/style.css:1-4` |
| One Business Central, one catalogue | There is no separate Zagreb system. The five columns holding the tag are single-valued; do not reintroduce a choice over them | CLAUDE.md |
| `coverage_scope` has two values | `item` was retired 2026-08-12 and is unreachable on three levels | `migrations/022_coverage_scope_vocabulary.sql` |
| The Review panel's PDF is the browser's own viewer | No server-side rendering or thumbnail: the panel is an `<iframe>` of `/documents/{id}/file`, and `Dockerfile.web` stays slim with no PyMuPDF. The file route's year-long private cache is what makes reopening a row cheap | Office UI redesign spec § 6; `document_file` in `web/app.py`; the header of `Dockerfile.web` |

## Known drift in the UI

Not limits of the system, but things a developer should know are wrong before a
client asks. All logged in `tasks/followups.md`.

| What | Followup |
|---|---|
| Screens print internal vocabulary at the user — one button is literally labelled *"Enqueue ingest.run"* | `[ui-wording]` |
| Four screens instruct the reader to run shell commands they cannot run | `[shell-commands-on-client-screens]` |

## S2.2 — the onboarding wizard is half built (2026-09-03)

| What is missing | Where |
|---|---|
| ~~**No front door.**~~ **WRONG, corrected 2026-09-04.** `registry.start_playbook` + `POST /manufacturers/{name}/playbook` + the *Start a playbook* card on `manufacturer_detail.html` already create a playbook from the browser, and `tests/test_playbook_start.py` covers it. Measured: all **384** canonical manufacturers have a `manufacturer` row and all **346** without a playbook are startable in the browser today — the seed derives the row from `vendor_master`, so the CLI is not in this path. The spec's §5/§7 q4 wording predates that route and was carried in here unverified. The route through landed the same day: `web/onboarding.py`, `/onboarding`, four steps plus three recorded exits | `web/registry.py::start_playbook`, `web/onboarding.py` |
| **No suggestion engine.** The wizard does not propose candidate library URLs; an operator pastes one | spec §5 |
| **No authenticated portals.** Euronda's library is registration-gated; a probe behind a login is a manual task | crawl design §5 |
| **`pagination` cannot be authored from the browser.** The probe form writes `index_url`, `link_pattern`, `allow_hosts`, `max_links` and `doc_type_from`, and pins `same_host_only` True. A save PRESERVES `pagination` on a recipe that already has one (matched by `index_url`), so editing a paginated recipe through the UI does not silently break it — but creating one still needs the JSON file and `manufacturers seed`. `allow_hosts` came onto the form 2026-09-07: an empty box is unambiguous where an unticked `same_host_only` checkbox would not be, and Ultradent (library on `ultradent.com`, all 147 PDFs on `assets.ctfassets.net`) could not otherwise be authored at all | `crawl_from_form`, `web/registry.py` |
| **No suggestion engine.** Nothing proposes a candidate library URL; the operator pastes one. Step 3 of the approved mockup, deliberately out of the MVP | spec §5 |
| **The failure-monitor reonboard path is unbuilt.** `playbook.reonboard` serves the UI probe only; a scheduler-enqueued reonboard job raises rather than reporting success | `handlers.md` |

## Related

- [config-reference.md](config-reference.md) — the full key-by-key picture
- [handlers.md](handlers.md) — per-tag behaviour, including what dead-letters
- [PHASES.md](../../PHASES.md) — the live gap register; gaps get closed there, not here
- [dentalia-mdr-pipeline-ground-truth.md](../dentalia-mdr-pipeline-ground-truth.md) — options considered and rejected

## BC writeback — the stage is built, the client is not (2026-09-07)

| What is missing | Where |
|---|---|
| **The BC auth scheme is a guess.** `app/adapters/bc_client.py` sends Basic when credentials are set and nothing when they are not. BC on-premises also does NTLM; nobody has been able to try, because access is blocked. The first real connection may need a different scheme | `app/adapters/bc_client.py` |
| **`allmanufacturers` property names are unconfirmed.** `BC_MANUFACTURER_PROFILE` guesses `no` and `name` — Luka sent a URL, not a sample. The guard makes a wrong guess raise on the first record rather than importing 390 nameless codes, so this is safe, not silent | `app/vendor_master.py::read_odata` |
| **No HTTP client.** `bc.push` takes its BC client by injection and every test passes a fake. Nothing in the tree makes a real PATCH, deliberately: external access to `denwebnav:7048` is blocked at Dentalia's edge, so a real client could not be exercised and an unexercised one is worse than none | `app/handlers/bc_push.py::handle_bc_push` |
| **Nobody has confirmed what `dataitems` is a subset of.** If it is filtered, items we intend to write are simply absent and a bulk run skips them. Question for b-s.si; the preview's skip count is the symptom | design § 9 |

## Alerting — the last mile cannot be closed from inside (2026-09-07)

`app/health.py` and the `health-watch` cron now push a line off the machine when
a service stops beating, the queue stops moving, or a cron's perpetual tick dies.
One line per condition, a recovery line when it clears, suppression in
`alert_state`.

| What is still missing | Why it cannot be fixed here |
|---|---|
| **Nothing reports that the queue itself is dead.** The checks run as a cron ON the queue, so if every worker is gone, nothing evaluates them and the silence is indistinguishable from health | A process cannot report its own absence. This needs an external dead-man's switch — something that alerts when our ping *stops* arriving (healthchecks.io and similar; ntfy has no such feature). That is a new external account and a decision for Denis |
| **A wedged worker that still beats but never claims** is caught only by the stall check, and only once it has claimed at least one job in this database's life | `service_heartbeat` is written before each unit of work, so beating proves the loop turns, not that work moves. The stall check is the cover, and it deliberately treats a never-started queue as "not an incident" rather than alerting on every fresh install |
