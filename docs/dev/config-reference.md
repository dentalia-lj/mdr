# Config reference

**Status:** live. Every default, env var and TOML path below was extracted
mechanically from `app/config.py` on 2026-08-31, not transcribed by hand. The
"read by nothing" section was verified by searching all of `app/`, `web/` and
`tools/`.

`app/config.py` is the source of truth. This file exists because nothing else
reproduces it in one place, and because a handful of keys do not do what their
name implies.

## How a value is chosen

Env var **over** TOML overlay **over** the dataclass default. `load_config()`
composes it once; the result is frozen.

The TOML overlay is optional and named by **`DENTALIA_CONFIG_TOML`**. If that
variable is unset there is no overlay. If it is set and the file is missing or
broken, that is a hard error — a silently ignored config file is a footgun.

Defaults live on the dataclass fields and are deliberately never repeated inside
`load_config()`. The one time they were duplicated, the copy shipped a 90-day
renewal horizon against a 30-day ruling and left `storage` defaulting to an
adapter that raises.

## Read this before changing anything

### Keys that change what reaches production

| Key | Why it is dangerous |
|---|---|
| `gate.high` | One number gates four separate decisions, including whether a document is published without a human. Lowering it widens auto-publish |
| `validate.min_unscoped_ref_len` | Lowering it re-admits ambiguous short product references. 85 catalogue REFs are 1–3 characters |
| `resolve.name_suggest` | Equal to `name_accept` on purpose. Restoring a gap between them previously left 36.5% of the catalogue ungrouped, and an ungrouped item can hold no documents |
| `web.require_authenticated_user` | `False` means `gate.apply`'s `decided_by` comes from an untrusted form field instead of the proxy-verified header. Compose sets it `True`; dev and tests default `False` |
| `web.api_database_url` | Must stay the non-owner `dentalia_api` role. Pointing the UI at the owner role lets it write the registry directly, defeating invariant 1 |
| `discovery.hold` | A global kill switch on all new discovery. Easy to set for a cold start and forget |
| `ingest.process_md_unknown` | `False` silently drops rows with a blank device class — possibly medical devices — out of the pipeline |
| `email.zip_*` | Zip-bomb guards. Raising any of them re-opens a measured 42 KB → 4.5 GB expansion |
| `models.t1` / `models.t2` | A tier swap requires the Phase 0 diff protocol to be re-run |
| `adapters.storage` | `"gdrive"` makes every archive write raise until gap G7 lands |
| `storage.local_root` | A relative path means archived files are lost on container restart |
| `budget.*` | **Displayed, never enforced.** `sweep_cap_eur` and `monthly_cap_eur` appear only in the status board's display dict. No code in `app/` stops or throttles anything when spend passes either. They read like safety valves and are labels |
| `connection.anthropic_api_key` | Empty hard-fails worker startup, on purpose, since 2026-08-12 |
| `web.playbooks_dir` | A test seam. Setting it in production reads and saves against a second, staler copy instead of the database |

### Keys that cannot be set on a deployed stack

Separate from the two kinds below, and invisible to any grep of `app/`: a key
can be read by live code and still be unreachable in a container. **Compose
passes no `.env` into any service** — it reads that file for `${}` interpolation
only, and there is no `env_file:` anywhere — so a key has to be named in a
service's `environment:` block to exist inside the process.

Measured 2026-09-14 by reading the running containers: of the 107 keys
`app/config.py` consumes, **60 reach neither `worker` nor `web`** and therefore
run on code defaults, `.env` notwithstanding. `DENTALIA_CONFIG_TOML` is among
them, so the TOML overlay described above is dead in a container too — the code
default is the only reachable value.

No value is currently *wrong*: injecting every documented value into a config
load inside the worker changed exactly one setting, and `.env.example` was the
incorrect side (`RESOLVE_NAME_SUGGEST`, fixed the same day).

Full list, the placement rule, and the implementation traps:
[`2026-09-14-config-placement-design.md`](../superpowers/specs/2026-09-14-config-placement-design.md).
**Deferred by Denis 2026-09-14.** Read it before adding any key to compose.

### Keys that do nothing

Two kinds. The second kind is the dangerous one.

**Never referenced anywhere.** The identifier does not appear in `app/`, `web/`
or `tools/` at all.

| Key | Env var | Note |
|---|---|---|
| `email.send_policy` | *(TOML only)* | Intended to gate draft-versus-auto sending. The system never sends email at all, so nothing consults it |
| `mfr_binding.auto_link` | *(TOML only)* | — |
| `mfr_binding.per_manufacturer` | *(TOML only)* | — |
| `fetch.robots_ttl_hours` | `FETCH_ROBOTS_TTL_HOURS` | read by `fetch.url` only (since 2026-09-11): how long a host's robots.txt answer is reused before FETCH asks again. The crawl rung and the probe (`discover.py`, `playbook_probe.py`) still pass nothing and get the module's own `24`. Blast radius: longer means a host that tightens its robots.txt is obeyed later; a transport failure is cached 5 minutes regardless |
| `scheduler.failure_monitor_interval_hours` | `SCHEDULER_FAILURE_MONITOR_INTERVAL_HOURS` | The tick runs daily regardless |

**Shadowed by an identical function default.** The name exists in the code, so a
casual grep looks reassuring — but the config value never reaches it.

| Key | Env var | What actually applies |
|---|---|---|
| `queue.max_attempts` | `QUEUE_MAX_ATTEMPTS` | `queue.enqueue`'s own `max_attempts=5` |

No caller passes `cfg.queue` into `queue.enqueue`, so setting `QUEUE_MAX_ATTEMPTS`
still changes nothing. The defaults happen to be identical, which is why this has
never produced a visible bug.

**The other three were on this list until 2026-09-04 and are not any more.**
`queue.visibility_timeout_s`, `queue.backoff_base_s` and `queue.backoff_cap_s`
now reach the queue: `run_once` takes `queue_cfg` and threads it into
`queue.claim` and `queue.fail`, and `run_forever` passes `cfg.queue`
(`app/workers/runner.py:57-68,101`). This one mattered — the config default was
raised 300 -> 1800 on 2026-09-02 to stop a 38-page `eudamed.sweep` being
reclaimed mid-transaction, and for two days the running worker kept reclaiming at
300 because the value never left the config object.

`models.rank` was on that list until 2026-09-02 and no longer is: the live T1
candidate ranker is wired (`app/extract/ranking.py`), and `_default_rank` reads
this key to build its client. Blast radius: it selects the model DISCOVER pays
to score search candidates, at roughly $0.004 per group on the Haiku default.
Point it at a more expensive tier and every search-path group costs
proportionally more, with no other behaviour change — the fetch/no-fetch
decision stays `discovery.rank_threshold`, a rule the model never sees.

## Every key

Defaults are the dataclass values. Every row's TOML path is the section and field
name shown, unless noted.

### `gate` — GATE thresholds

| Key | Type | Default | Env var |
|---|---|---|---|
| `gate.high` | float | `0.92` | `GATE_THRESHOLD_HIGH` |
| `gate.med` | float | `0.75` | `GATE_THRESHOLD_MED` |

TOML paths are `gate.threshold_high` / `gate.threshold_med` — the only section
whose TOML names differ from its field names.

Not to be confused with the extraction escalation threshold, which is a hardcoded
`0.95` parameter of `tiers.run_extraction` and is not config at all.

### `resolve` — grouping

| Key | Type | Default | Env var |
|---|---|---|---|
| `resolve.name_accept` | float | `0.90` | `RESOLVE_NAME_ACCEPT` |
| `resolve.name_suggest` | float | `0.90` | `RESOLVE_NAME_SUGGEST` |
| `resolve.name_candidate_k` | int | `20` | `RESOLVE_NAME_CANDIDATE_K` |

### `discover` — the discovery ladder

| Key | Type | Default | Env var |
|---|---|---|---|
| `discovery.topk` | int | `3` | `DISCOVER_TOPK` |
| `discovery.rank_threshold` | float | `0.60` | `DISCOVER_RANK_THRESHOLD` |
| `discovery.recency_days` | int | `30` | `DISCOVER_RECENCY_DAYS` |
| `discovery.max_search_candidates` | int | `10` | `DISCOVER_MAX_SEARCH_CANDIDATES` |
| `discovery.crawl_rank_max` | int | `1500` | `DISCOVER_CRAWL_RANK_MAX` |
| `discovery.crawl_rank_floor` | int | `5` | `DISCOVER_CRAWL_RANK_FLOOR` |
| `discovery.email_rung_enabled` | bool | `False` | `DISCOVER_EMAIL_RUNG_ENABLED` |
| `discovery.manufacturer_button_cap` | int | `25` | `DISCOVER_MANUFACTURER_BUTTON_CAP` |
| `discovery.hold` | bool | `False` | `DISCOVER_HOLD` |
| `discovery.default_source_priority` | list | `recency, known_url, playbook, eudamed, search, email, manual` | *TOML only* |

### `adapters` — which implementation loads

| Key | Type | Default | Env var |
|---|---|---|---|
| `adapters.source` | str | `csv` | `SOURCE_ADAPTER` |
| `adapters.storage` | str | `local` | `STORAGE_ADAPTER` |
| `adapters.search` | str | `brave` | `SEARCH_ADAPTER` |
| `adapters.email` | str | `imap` | `EMAIL_ADAPTER` |

### `storage`

| Key | Type | Default | Env var |
|---|---|---|---|
| `storage.local_root` | str | `./archive` | `STORAGE_LOCAL_ROOT` |
| `storage.base_url` | str | `""` | `STORAGE_BASE_URL` |
| `storage.gdrive_folder_id` | str | `""` | `STORAGE_GDRIVE_FOLDER_ID` |

### `models`

| Key | Type | Default | Env var |
|---|---|---|---|
| `models.t1` | str | `claude-haiku-4-5` | `MODELS_T1` |
| `models.t2` | str | `claude-sonnet-5` | `MODELS_T2` |
| `models.rank` | str | `claude-haiku-4-5` | `MODELS_RANK` |
| `models.email_summary` | str | `claude-haiku-4-5` | `MODELS_EMAIL_SUMMARY` |

`email_summary` is its own key so a tier swap cannot drag it along with `rank`.
Invariant 12 caps it at the cheap tier.

### `batch` and `fetch`

| Key | Type | Default | Env var |
|---|---|---|---|
| `batch.poll_interval_s` | int | `900` | `BATCH_POLL_INTERVAL_S` |
| `fetch.politeness_ms` | int | `2000` | `FETCH_POLITENESS_MS` |
| `fetch.recency_window_days` | int | `21` | `FETCH_RECENCY_WINDOW_DAYS` |
| `fetch.robots_ttl_hours` | int | `24` | `FETCH_ROBOTS_TTL_HOURS` |

### `budget`

| Key | Type | Default | Env var |
|---|---|---|---|
| `budget.sweep_cap_eur` | float | `100.0` | `BUDGET_SWEEP_CAP_EUR` |
| `budget.monthly_cap_eur` | float | `40.0` | `BUDGET_MONTHLY_CAP_EUR` |
| `budget.eur_per_usd` | float | `0.92` | `BUDGET_EUR_PER_USD` |

`eur_per_usd` is a manual rate, not a live one. A stale value misreports EUR
spend on the status board.

### `renewal`

| Key | Type | Default | Env var |
|---|---|---|---|
| `renewal.horizon_days` | tuple | `(30,)` | *TOML only* |
| `renewal.request_cadence_days` | int | `7` | `RENEWAL_REQUEST_CADENCE_DAYS` |
| `renewal.reminder_after_days` | int | `14` | `RENEWAL_REMINDER_AFTER_DAYS` |
| `renewal.escalate_after_reminders` | int | `3` | `RENEWAL_ESCALATE_AFTER_REMINDERS` |

`horizon_days` must stay equal to `compliance.EXPIRY_HORIZON_DAYS`. A test
guards it: the manufacturer card would otherwise call a document fine on a day
the scheduler is already chasing it.

### `email`

| Key | Type | Default | Env var |
|---|---|---|---|
| `email.send_policy` | str | `draft` | *TOML only* — **read by nothing** |
| `email.imap_host` | str | `""` | `IMAP_HOST` |
| `email.imap_port` | int | `993` | `IMAP_PORT` |
| `email.imap_ssl` | bool | `True` | `IMAP_SSL` |
| `email.imap_folder` | str | `INBOX` | `IMAP_FOLDER` |
| `email.poll_since` | str | `""` | `EMAIL_POLL_SINCE` — ISO date, the first day of mail the poll reads (IMAP `SINCE`, the server's arrival date, the day itself included). **Required once the mailbox is configured**: empty or malformed with credentials set raises `ValueError`, so the poll dead-letters and alerts rather than reading the mailbox's whole history. **Blast radius:** moving it later re-reads nothing already in `email_poll_log` (the ledger filter runs first), but moving it EARLIER pulls every older message in, each with a summary call and its attachments archived and extracted. Set it once, to the day of the first production deploy (Denis, 2026-09-24) |
| `email.poll_max_messages` | int | `200` | `EMAIL_POLL_MAX_MESSAGES` — messages taken per poll. **Pacing, not a limit** since 2026-09-24: the ledger filter runs before the cap, so new mail past it waits for the next poll and is counted as `deferred_to_next_poll`, never lost. It bounds one job's work, which keeps a poll well inside the 30-minute visibility timeout |
| `email.zip_expand` | bool | `True` | `EMAIL_ZIP_EXPAND` |
| `email.zip_max_members` | int | `50` | `EMAIL_ZIP_MAX_MEMBERS` |
| `email.zip_max_member_mb` | int | `25` | `EMAIL_ZIP_MAX_MEMBER_MB` |
| `email.zip_max_total_mb` | int | `100` | `EMAIL_ZIP_MAX_TOTAL_MB` |
| `email.zip_max_ratio` | int | `200` | `EMAIL_ZIP_MAX_RATIO` |
| `email.summary_enabled` | bool | `True` | `EMAIL_SUMMARY_ENABLED` |
| `email.summary_min_chars` | int | `0` | `EMAIL_SUMMARY_MIN_CHARS` |
| `email.summary_max_chars` | int | `6000` | `EMAIL_SUMMARY_MAX_CHARS` |
| `email.summary_max_tokens` | int | `200` | `EMAIL_SUMMARY_MAX_TOKENS` |

### `mfr_binding`, `ingest`, `validate`

| Key | Type | Default | Env var |
|---|---|---|---|
| `mfr_binding.auto_link` | bool | `True` | *TOML only* — **read by nothing** |
| `mfr_binding.per_manufacturer` | dict | `{}` | *TOML only* — **read by nothing** |
| `ingest.process_md_unknown` | bool | `True` | `INGEST_PROCESS_MD_UNKNOWN` |
| `ingest.mfr_ref_source_by_code` | dict | `{}` | *TOML only* |
| `validate.min_unscoped_ref_len` | int | `6` | `VALIDATE_MIN_UNSCOPED_REF_LEN` |

`mfr_ref_source_by_code` overrides which physical column supplies `mfr_ref` per
BC code, and so feeds the invariant-3 REF identity.

### `queue` — three of four reach the queue

| Key | Type | Default | Env var |
|---|---|---|---|
| `queue.visibility_timeout_s` | int | `1800` | `QUEUE_VISIBILITY_TIMEOUT_S` |
| `queue.backoff_base_s` | int | `5` | `QUEUE_BACKOFF_BASE_S` |
| `queue.backoff_cap_s` | int | `3600` | `QUEUE_BACKOFF_CAP_S` |
| `queue.max_attempts` | int | `5` | `QUEUE_MAX_ATTEMPTS` |

`visibility_timeout_s`, `backoff_base_s` and `backoff_cap_s` are live since
2026-09-04. `max_attempts` is still shadowed by `queue.enqueue`'s own default —
see ["shadowed by an identical function default"](#keys-that-do-nothing) above.

### `alerts` — the only push notification in the system

| Key | Type | Default | Env var |
|---|---|---|---|
| `alerts.webhook_url` | str | `""` | `ALERTS_WEBHOOK_URL` |

Empty means alerting is off, and that is the right default everywhere except the
one machine that runs this for real — tests, CI and a developer laptop must not
post anywhere. Added 2026-09-04, when a measurement found zero hits for slack,
webhook, pagerduty, ntfy, telegram or any SMTP client across the tree: a job that
dead-lettered on a Friday evening sat unseen until somebody opened `/dead`.

**Blast radius.** One POST per dead-lettered job, from `app/workers/runner.py`
after the failure transaction commits. Nothing else calls it yet. `app/alerts.py`
swallows every failure and returns a bool, so an unreachable endpoint costs a log
line and up to 5 seconds on a path that was already failing.

**Shape.** A generic POST: the body is the message, `Title` and `Priority` ride in
headers. That is ntfy's contract. Slack and Discord read JSON and would need a
second shape behind the same function — never at the call sites.

**The URL is the whole credential**, so treat it like one. An ntfy topic is
readable AND writable by anyone who knows its name, and names are enumerable: a
short one leaks job failures and manufacturer names to whoever guesses it, and
lets a stranger post alerts that look like ours. Use a long random topic.

**Not the email feature.** `email.request` writes supplier drafts a person sends
by hand (ruling 2026-08-20). This is an operator alert to whoever runs the system.
Keep them apart: an alert must never become a channel for supplier mail.

### `connection` — secrets

| Key | Type | Default | Env var |
|---|---|---|---|
| `connection.database_url` | str | `postgresql://dentalia:dentalia@localhost:5432/dentalia` | `DATABASE_URL` |
| `connection.anthropic_api_key` | str | `""` | `ANTHROPIC_API_KEY` |
| `connection.brave_api_key` | str | `""` | `BRAVE_API_KEY` |
| `connection.serper_api_key` | str | `""` | `SERPER_API_KEY` |
| `connection.imap_user` | str | `""` | `IMAP_USER` |
| `connection.imap_password` | str | `""` | `IMAP_PASSWORD` |

### `web`

| Key | Type | Default | Env var |
|---|---|---|---|
| `web.host` | str | `127.0.0.1` | `WEB_HOST` |
| `web.port` | int | `8000` | `WEB_PORT` |
| `web.imports_dir` | str | `/imports` | `WEB_IMPORTS_DIR` |
| `web.upload_max_mb` | int | `25` | `UPLOAD_MAX_MB` |
| `web.api_database_url` | str | `postgresql://dentalia_api:dentalia_api@localhost:5432/dentalia` | `API_DATABASE_URL` |
| `web.require_authenticated_user` | bool | `False` | `WEB_REQUIRE_AUTHENTICATED_USER` |
| `web.trusted_user_header` | str | `X-Forwarded-User` | `WEB_TRUSTED_USER_HEADER` |
| `web.archive_root` | str | `./archive` | `WEB_ARCHIVE_ROOT` |
| `web.playbooks_dir` | str | `""` | `WEB_PLAYBOOKS_DIR` |
| `web.path_rewrites` | str | `""` | `WEB_PATH_REWRITES` |
| `web.api_keys` | str | `""` | `WEB_API_KEYS` |
| `web.bc_link_key` | str | `""` | `WEB_BC_LINK_KEY` |
| `web.public_base_url` | str | `""` | `WEB_PUBLIC_BASE_URL` |
| `web.operator_users` | tuple[str] | `()` | `WEB_OPERATOR_USERS` (comma-separated; whitespace stripped, empty entries dropped; a TOML array also works) |

Empty `api_keys` disables the service caller class entirely. It never means
"allow all". Empty `bc_link_key` makes `/item/*` return 404.

`operator_users` is the opposite way round, on purpose: **empty means every
login is an operator** (office UI redesign spec § 2), which keeps today's single
shared login working unchanged. A non-empty list is matched exactly,
case-sensitively, against the login in `trusted_user_header`, through
`web.access.is_operator`. Blast radius today, in two places:

- **The menu.** A login not in the list is not shown the collapsed **Operator**
  block at the bottom of the sidebar (System status, Failed tasks, Queues &
  health, Playbooks, Data quality, Decisions log, Scheduler, Business Central
  push, API). Hidden, not refused: every one of those pages still answers that
  login by URL (D3).
- **`/dead`.** A login not in the list loses the developer section's **Re-run
  all** and per-job **Re-run** buttons (D6); **Try the N again** and **Search
  again** stay for everyone, because Today posts to both and clearing a supplier
  timeout is the office's own work.
- **These writes are refused outright** (P5b 2026-09-15, widened by fix round 1
  the same day), with a 403 and the P7a error page reading *This action is for
  operators.*:

  | Route | Button |
  |---|---|
  | `POST /playbooks/{slug}` | Save on a playbook |
  | `POST /playbooks/{slug}/codes` | Claim a BC code |
  | `POST /playbooks/{slug}/revert` | Restore an earlier revision |
  | `POST /playbooks/{slug}/probe` | Look at a document library (enqueues a fetch) |
  | `POST /playbooks/{slug}/crawl` | Save the probed recipe |
  | `POST /onboarding/{name}/start` | Start a playbook from the guided flow |
  | `POST /onboarding/{slug}/domains` | Save the domains step |
  | `POST /onboarding/{name}/skip`, `/unskip` | Park a supplier, or bring it back |
  | `POST /bc-push/apply` | Send every difference to Business Central |
  | `POST /scheduler/run/{cron}` | Run now |
  | `POST /scheduler/arm/{cron}` | Arm |
  | `POST /dead/rerun-group` | Re-run every job behind one cause |
  | `POST /dead/{id}/rerun` | Re-run one dead job |
  | `POST /ingest` | The developer ingest form |

  `web/access.py::operator_guard` builds the dependency, `web/app.py` composes
  it with `_authenticated_user` and hands it to the modules that own those
  routes. It is a route dependency, so a refused request never reaches the
  handler and nothing is enqueued.

  **`web/onboarding.py` declares the guard itself**, and that is not
  belt-and-braces: it reaches `start_playbook` and `save_playbook_body` by
  IMPORTING them, not by posting to `/playbooks/{slug}`, so the guard on that
  route does nothing for the flow.

  **`/import` apply is deliberately not on the list.** It is the office's own
  work -- it is how the catalogue arrives -- and so is `POST /items/{ref}/
  bc-push`, which pushes one article a person is already looking at, and
  `/dead/retry-timeouts` and `/dead/search-again`.

Two cautions:

- It needs the proxy. With `require_authenticated_user` off there is no login,
  so a non-empty list makes **nobody** an operator.
- It is not a security boundary for PAGES, only for those six routes. D3 says
  so: an operator page typed as a URL still answers an office login.

### `scheduler`

| Key | Type | Default | Env var |
|---|---|---|---|
| `scheduler.tick_interval_s` | int | `300` | `SCHEDULER_TICK_INTERVAL_S` |
| `scheduler.ingest_watch_dir` | str | `""` | `SCHEDULER_INGEST_WATCH_DIR` |
| `scheduler.ingest_glob` | str | `*.xlsx` | `SCHEDULER_INGEST_GLOB` |
| `scheduler.ingest_catalogue` | str | `LJ` | `SCHEDULER_INGEST_CATALOGUE` |
| `scheduler.expiry_scan_interval_hours` | int | `24` | `SCHEDULER_EXPIRY_SCAN_INTERVAL_HOURS` |
| `scheduler.expiry_email_enabled` | bool | `False` | `SCHEDULER_EXPIRY_EMAIL_ENABLED` |
| `scheduler.expiry_rediscover_enabled` | bool | `False` | `SCHEDULER_EXPIRY_REDISCOVER_ENABLED` |
| `scheduler.expiry_rediscover_cap` | int | `25` | `SCHEDULER_EXPIRY_REDISCOVER_CAP` |
| `scheduler.coverage_scan_enabled` | bool | `False` | `SCHEDULER_COVERAGE_SCAN_ENABLED` |
| `scheduler.coverage_scan_cap` | int | `25` | `SCHEDULER_COVERAGE_SCAN_CAP` |
| `scheduler.report_dir` | str | `""` | `SCHEDULER_REPORT_DIR` — where `report.weekly` writes its HTML copy. Empty writes nothing, the same inert-until-configured shape `ingest_watch_dir` uses. Compose sets `/archive/reports`: inside the volume the worker already writes and `web` already mounts read-only, so a report survives a restart the way an archived document does. **Blast radius:** point it somewhere the worker cannot write and the weekly cron fails; point it somewhere `web` does not mount and `/reports` lists nothing while the files are being written fine |
| `web.reports_dir` | str | `""` | `WEB_REPORTS_DIR` — where `/reports` reads them from. Must name the same directory as `scheduler.report_dir` resolves to inside the web container. Empty makes the page say so rather than render an empty list that reads as "no reports exist" |
| `scheduler.failure_monitor_interval_hours` | int | `24` | `SCHEDULER_FAILURE_MONITOR_INTERVAL_HOURS` — **read by nothing** |
| `scheduler.failure_window_days` | int | `30` | `SCHEDULER_FAILURE_WINDOW_DAYS` |
| `scheduler.failure_miss_rate_threshold` | float | `0.5` | `SCHEDULER_FAILURE_MISS_RATE_THRESHOLD` |
| `scheduler.failure_min_sample` | int | `5` | `SCHEDULER_FAILURE_MIN_SAMPLE` |
| `scheduler.failure_reonboard_enabled` | bool | `False` | `SCHEDULER_FAILURE_REONBOARD_ENABLED` |
| `scheduler.email_poll_enabled` | bool | `False` | `SCHEDULER_EMAIL_POLL_ENABLED` |
| `scheduler.email_poll_interval_hours` | int | `6` | `SCHEDULER_EMAIL_POLL_INTERVAL_HOURS` |
| `scheduler.eudamed_certregister_enabled` | bool | `False` | `SCHEDULER_EUDAMED_CERTREGISTER_ENABLED` |
| `scheduler.eudamed_certregister_interval_days` | int | `30` | `SCHEDULER_EUDAMED_CERTREGISTER_INTERVAL_DAYS` |
| `scheduler.eudamed_sweep_interval_days` | int | `90` | `SCHEDULER_EUDAMED_SWEEP_INTERVAL_DAYS` |

Six scheduler flags default off: expiry emails, expiry re-discovery, the
coverage scan, re-onboarding, mail polling and the EUDAMED certificate
register (the email discovery rung is `discovery.email_rung_enabled`, above,
not a scheduler key). An empty
`ingest_watch_dir` makes the monthly ingest tick a no-op.

`scheduler.ingest_catalogue` defaults to `LJ`. There is one Business Central and
one catalogue; the tag is a leftover, single-valued, and not a choice.

### `source_priority`

`source_priority` (top level, dict of manufacturer → ladder, TOML only) is a
placeholder. A manufacturer's own playbook `source_priority` supersedes it.

## Related

- [handlers.md](handlers.md) — what consumes these at run time
- [architecture.md](../architecture.md) — where each process reads config
- [runbook.md](../runbook.md) — which variables the compose file sets
- PRD §11 — the normative list


### `discovery.manufacturer_button_cap`

How many groups one press of the manufacturer page's **Search for documents**
button queues. Blast radius is a click, not a cron: nothing reads this except
`POST /manufacturers/{name}/discover`.

25 is measured, not chosen. Against the live registry 2026-09-02: 365 of 367
manufacturers have uncovered groups, the median has **3**, and 84% have 25 or
fewer — so one press finishes most suppliers outright. The tail is what the cap
is for: HENRY SCHEIN has 809 and INSTITUT STRAUMANN AG 329, and at `discovery.topk`
of 3 an uncapped press on Henry Schein queues up to 2.400 fetches, most of them
against one domain at the 2s politeness interval — hours of serialised fetching
from a single click.

Raising it raises the cost and queue depth of one press proportionally. Lowering
it below the median (3) makes the button useless for most suppliers.


### `scheduler.expiry_rediscover_enabled` / `_cap`

On expiry, go back to the manufacturer's own sources and look for a newer
document instead of only drafting a letter.

**Blast radius: this is the switch that starts unattended fetching.**
`DISCOVER_HOLD` cannot protect this path — the hold is read only in
`app/handlers/resolve.py`, which gates RESOLVE from *emitting* `discover.group`.
A cron that enqueues `discover.group` directly bypasses it entirely. That is why
the flag defaults to `False` and why it is separate from
`expiry_email_enabled`: chasing by letter and looking again ourselves are two
decisions, and turning on one must not start the other.

The cap is per tick and measured. On 2026-09-02 the live registry held 86
production documents expiring or already lapsed inside a 30-day horizon — all 86
lapsed, 65 on their own stated date and 21 on the five-year staleness review —
across 6 manufacturers. Those 86 documents cover **431 groups**, because one
document covers many, so an uncapped daily tick would queue roughly 1.300
fetches. Groups are ordered never-discovered first then least recently
discovered, so consecutive ticks walk the backlog rather than repeating its
head; whatever the cap leaves behind is reported as `rediscover_pending` on the
tick result, never dropped silently.


### `scheduler.coverage_scan_enabled` / `_cap`

The coverage cron: groups holding no production document go through DISCOVER,
capped per day.

**`DISCOVER_HOLD` does not stop this, and that is a deliberate ruling** (Denis,
2026-09-02: keep the flags separate). The hold is read in exactly one place,
`app/handlers/resolve.py`, where it stops RESOLVE *emitting* `discover.group`. A
cron that enqueues one directly never consults it. There are therefore three
independent switches for automatic discovery and no master one:

| Switch | Stops |
|---|---|
| `DISCOVER_HOLD` | RESOLVE emitting discovery after an ingest |
| `scheduler.coverage_scan_enabled` | the daily coverage backfill |
| `scheduler.expiry_rediscover_enabled` | the re-look when a document lapses |

To stop all unattended fetching you must set all three. Neither button (per item,
per manufacturer) is affected by any of them — those are a person pressing.

25/day matches `discovery.manufacturer_button_cap` on purpose: the cron should
read as a trickle behind whatever someone is already doing by hand. Measured
2026-09-02, 6.732 groups hold no production document and 6.693 have never been
discovered at all, so 25/day is roughly a nine-month backfill. Raising it
shortens that proportionally and raises the daily fetch load; the politeness
lease (one fetch per domain per `fetch.politeness_ms`) is what actually bounds
throughput on any single manufacturer.

## `bc` — writing back into Business Central

| Key | Env | Default | Blast radius |
|---|---|---|---|
| `bc.write_enabled` | `BC_WRITE_ENABLED` | `false` | The only gate between this repo and data inside a system we do not own. False everywhere except the one machine that runs it for real: `bc.push` still computes and reports its diff, and sends nothing. Turning it on is what makes the writeback live |
| `bc.base_url` | `BC_BASE_URL` | `""` | The company-scoped OData root b-s.si issued. The writable fields are on its `dataitems` page; `allitems` is read-only and must never be the target. Empty means `ingest.run` with `source: bc_odata` gets no client and refuses rather than reading an empty catalogue |
| `bc.username` / `bc.password` | `BC_USERNAME` / `BC_PASSWORD` | `""` | **Auth scheme unverified.** BC on-premises commonly takes Basic or NTLM and b-s.si have not said which; access is blocked, so nobody has tried. Both empty sends no `Authorization` header at all — an empty Basic header is worse than none, because some servers read it as an anonymous identity rather than rejecting it |

| `bc.drift_cap` | `BC_DRIFT_CAP` | `1000` | Items the drift cron re-evaluates per run. A rolling window, not a full sweep: at 1000/day a 16k catalogue comes round about every 16 days. Cheap to be generous with — an unchanged item costs one diff query and no PATCH — but it bounds a mistake in the rule to one day's items |

`pteWarehouseURL` is built from `web.public_base_url` and `web.bc_link_key`, not
from anything here — it is the same link `web/item_link.py` serves and the same
key `web/access.py` checks.
