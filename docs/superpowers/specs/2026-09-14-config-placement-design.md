# Where each config key belongs: env, TOML, database, or nowhere

**Status:** design, agreed in chat 2026-09-14. **DEFERRED by Denis the same day
— explicitly not today.** Nothing is broken and nothing is running on a wrong
value (§ 3). This is about what a client install can change *tomorrow*, without
a rebuild.
**Owner:** unowned. Pick it up before the first client install needs a value
changed, or when the BC access arrives and `BC_*` has to reach a container.
**Answers two open followups** filed the same day by the deployment-guide
session: `[compose-passes-almost-no-config]` (which asked for exactly this
decision: `env_file: .env` versus key-by-key `environment:` blocks — the answer
below is *neither*, because `env_file` hands every container every secret) and
`[env-example-drifted-from-the-code]`, whose two defects are both fixed as of
2026-09-14 (`RESOLVE_NAME_SUGGEST`, and `ALERTS_WEBHOOK_URL` missing from
`.env.example`).
**Blast radius:** compose + `.env.example` + one new TOML file. No code change,
no effective value change.

---

## 1. The finding

`app/config.py` reads **107 environment keys**. In the running stack, 37 reach
`worker` and 10 more (the `WEB_*` set plus `API_DATABASE_URL`) reach `web`.
**60 reach neither**, so they run on code defaults and editing `.env` changes
nothing.

The cause is not a bug, it is compose semantics that nothing wrote down:
**compose passes no `.env` into any container.** It reads that file for `${}`
interpolation only, and there is no `env_file:` in `docker-compose.yml` or
`docker-compose.dev.yml`. A key has to be named in a service's `environment:`
block to exist inside the process. The `docker-compose.prod.yml` proposed in
[`docs/dev/deployment.md`](../../dev/deployment.md) § 4 only remaps volumes and
does not change this.

`DENTALIA_CONFIG_TOML` is one of the 60. That closes the second override channel
too: **inside a container the code default is the only reachable value** for all
of them, and the five TOML-only settings (`ingest.mfr_ref_source_by_code`,
`mfr_binding.*`, `source_priority`, `discover.default_source_priority`,
`renewal.horizon_days`, `email.send_policy`) are equally unreachable.

### How to re-measure it

Read the containers, never the YAML — the YAML is what made this invisible for
months:

```bash
docker compose exec -T worker env | cut -d= -f1 | sort > /tmp/worker.env
docker compose exec -T web    env | cut -d= -f1 | sort > /tmp/web.env
# then diff against the keys app/config.py actually reads
docker compose exec -T worker python -c "
import os
for k in ('GATE_THRESHOLD_HIGH','MODELS_T2','BUDGET_MONTHLY_CAP_EUR','BC_BASE_URL','DENTALIA_CONFIG_TOML'):
    print(k, os.environ.get(k))"
```

## 2. The 60, grouped

| Group | n | Keys |
|---|---|---|
| BC integration | 5 | `BC_BASE_URL` `BC_USERNAME` `BC_PASSWORD` `BC_WRITE_ENABLED` `BC_DRIFT_CAP` |
| Email zip + summary | 9 | `EMAIL_ZIP_*` (5), `EMAIL_SUMMARY_*` (4) |
| Discover tuning | 8 | `DISCOVER_TOPK` `_RANK_THRESHOLD` `_RECENCY_DAYS` `_MAX_SEARCH_CANDIDATES` `_CRAWL_RANK_MAX` `_CRAWL_RANK_FLOOR` `_MANUFACTURER_BUTTON_CAP` `_EMAIL_RUNG_ENABLED` |
| Adapters + their keys | 5 | `SOURCE_ADAPTER` `STORAGE_ADAPTER` `SEARCH_ADAPTER` `SERPER_API_KEY` `STORAGE_GDRIVE_FOLDER_ID` |
| Models / tiers | 4 | `MODELS_T1` `_T2` `_RANK` `_EMAIL_SUMMARY` |
| Queue | 4 | `QUEUE_VISIBILITY_TIMEOUT_S` `_BACKOFF_BASE_S` `_BACKOFF_CAP_S` `_MAX_ATTEMPTS` |
| Budget | 3 | `BUDGET_SWEEP_CAP_EUR` `_MONTHLY_CAP_EUR` `_EUR_PER_USD` |
| Fetch | 3 | `FETCH_POLITENESS_MS` `_RECENCY_WINDOW_DAYS` `_ROBOTS_TTL_HOURS` |
| Resolve | 3 | `RESOLVE_NAME_ACCEPT` `_SUGGEST` `_CANDIDATE_K` |
| Renewal | 3 | `RENEWAL_REQUEST_CADENCE_DAYS` `_REMINDER_AFTER_DAYS` `_ESCALATE_AFTER_REMINDERS` |
| Gate | 2 | `GATE_THRESHOLD_HIGH` `_MED` |
| Web, in neither container | 4 | `WEB_OPERATOR_USERS` `WEB_PLAYBOOKS_DIR` `WEB_PORT` `WEB_TRUSTED_USER_HEADER` |
| Misc | 7 | `BATCH_POLL_INTERVAL_S` `INGEST_PROCESS_MD_UNKNOWN` `UPLOAD_MAX_MB` `VALIDATE_MIN_UNSCOPED_REF_LEN` `DENTALIA_CONFIG_TOML` `DENTALIA_LOG_LEVEL` `DENTALIA_POLL_INTERVAL_S` |

41 of the 60 are documented in `.env.example`; the other 19 appear nowhere and
are known only from `app/config.py`.

## 3. The sanity check — nothing is running wrong

Every documented value was injected into a config load **inside the running
worker** and the whole `Config` tree diffed against the live one:

```
documented keys injected: 41
settings that would CHANGE:  1
  resolve.name_suggest    running=0.9   documented=0.72
```

The runtime was the correct side. `.env.example` still carried the pre-ruling
`0.72`, and `name_suggest == name_accept == 0.90` is Denis's ruling of
2026-08-12 after the staging band hid 5.831 of 15.958 items from all
document linking. **Wiring these keys up with a `.env` copied from
`.env.example` would have reverted that ruling on the first deploy.**

Fixed 2026-09-14 in `.env.example`, `docs/specs/resolve.md` and
`docs/dentalia-job-type-handbook.md`, each with the ruling named so it is not
"restored". `PHASES.md` G5 and `docs/build-log.md` still say 0.72 and are
correct to: they are dated records that describe the override.

Every ruling living only in TOML-land also matches its code default, checked the
same way: `renewal.horizon_days (30,)`, `email.send_policy 'draft'`,
`gate 0.92 / 0.75`, `queue.visibility_timeout_s 1800`.

**So the exposure is not wrong values. It is that a client server cannot change
a value without a rebuild.**

## 4. The placement rule

| Home | For | Why |
|---|---|---|
| **Env var** | Per-installation facts and secrets: connections, credentials, adapter choice, paths, ports, "is this environment wired for X" | Set once at install, never by a user, often secret |
| **TOML overlay** | Policy and calibration: thresholds, tiers, caps, cadences, per-supplier maps | Changes rarely, must change without a rebuild, belongs in one reviewable file |
| **Database** | Anything scoped to an entity the system already tracks — a manufacturer, a host, a person | Needs an audit trail and a UI; the per-entity answer differs |
| **Code only** | Safety bounds against hostile input, and queue mechanics | Editability is a liability, not a feature |

The TOML layer is **already built** — `load_config()` composes env over TOML over
defaults, and five settings are TOML-only today. It is dead in containers only
because its pointer variable is not declared. One env line plus one volume mount
revives it and covers 29 keys, instead of 29 more lines of YAML.

### Assignment

**Env — 15.** `BC_BASE_URL` `BC_USERNAME` `BC_PASSWORD` `BC_WRITE_ENABLED` ·
`SOURCE_ADAPTER` `STORAGE_ADAPTER` `SEARCH_ADAPTER` `SERPER_API_KEY`
`STORAGE_GDRIVE_FOLDER_ID` · `DISCOVER_EMAIL_RUNG_ENABLED` (a feature flag whose
whole `SCHEDULER_*_ENABLED` family is already declared) · `UPLOAD_MAX_MB` (web
only) · `DENTALIA_LOG_LEVEL` `DENTALIA_POLL_INTERVAL_S` `DENTALIA_CONFIG_TOML` ·
`WEB_OPERATOR_USERS`.

**TOML — 29.** `GATE_THRESHOLD_HIGH/_MED` · `RESOLVE_NAME_ACCEPT/_SUGGEST/
_CANDIDATE_K` · `MODELS_*` (4) · `BUDGET_*` (3) · `RENEWAL_*` (3) ·
`DISCOVER_*` (7 tuning) · `FETCH_*` (3) · `BATCH_POLL_INTERVAL_S` ·
`VALIDATE_MIN_UNSCOPED_REF_LEN` · `INGEST_PROCESS_MD_UNKNOWN` · `BC_DRIFT_CAP`.
Plus the five that are TOML-only already and therefore equally dead — including
`ingest.mfr_ref_source_by_code`, which is exactly the per-supplier map gap
**G4** is waiting on, and `renewal.horizon_days`, which carries the client's
30-day ruling.

`MODELS_*` belongs here deliberately: a tier swap requires the Phase 0 diff
protocol re-run, so it should be a reviewed file edit and never a form field.

**Code only — 16, and delete them from `.env.example`.** `EMAIL_ZIP_*` (5) and
`EMAIL_SUMMARY_*` (4): the sender controls the bytes, so a zip-bomb guard an
operator can raise is a vulnerability, and the measured case was 42 KB → 4.5 GB.
`QUEUE_*` (4): reclaim semantics, changing them on a live queue changes the
rules mid-flight; `docker compose run -e` covers a one-off incident.
`WEB_PLAYBOOKS_DIR`: not a knob, a trap — silent dual-store drift; prefer
deleting the seam. `WEB_PORT` and `WEB_TRUSTED_USER_HEADER`: fixed by the
compose topology, and declaring them invites a mismatch with Caddy.

**Database — none of the 60**, but three things belong there and one already is:

- `domain_lease.min_politeness_ms` (migration 036) is a per-host floor, written
  by the robots reader from `Crawl-Delay`; the lease takes
  `GREATEST(caller, floor)`. "This supplier complained, slow them down" is a row
  write plus a small control, **not** a change to the fleet-wide
  `FETCH_POLITENESS_MS`.
- The per-manufacturer maps (`source_priority`, `mfr_ref_source_by_code`,
  `mfr_binding.per_manufacturer`) are per-manufacturer data, and playbooks have
  lived in `manufacturer.body` since slice 4 (2026-08-27). TOML makes them
  settable at all; the playbook row is the destination.
- `WEB_OPERATOR_USERS` is people data. Env until named logins (P5b), then a
  table with the audit trail, beside the Caddy credentials.

## 5. Traps for whoever implements this

1. **A local `environment:` block REPLACES the anchor's**, per YAML merge-key
   semantics. That is the trap that cost 79 of 149 extractions on 2026-08-12
   when `ANTHROPIC_API_KEY` never reached the container. Every key added to
   `worker` must be checked against `web` and the `*app` anchor by hand.
2. **`tests/test_compose_config.py` already enforces exactly this rule** for the
   `SCHEDULER_*` set across all three services. Extend it to the new list rather
   than writing a second mechanism.
3. **`budget.*` is displayed, never enforced** — see
   [`config-reference.md`](../../dev/config-reference.md) § Read this before
   changing anything. Moving it to TOML makes it *settable*, not *effective*.
   Wiring enforcement is separate work; do not let the move imply a spend brake
   that does not exist.
4. **`queue.max_attempts` is shadowed by a function default.** No caller passes
   `cfg.queue` into `queue.enqueue`, so declaring the key changes nothing until
   one does. The defaults happen to match, which is why it has never shown.
5. **`fetch.robots_ttl_hours` is read by `fetch.url` only**; the crawl rung and
   the probe pass nothing and get the module's own 24. **`scheduler.
   failure_monitor_interval_hours`, `email.send_policy` and `mfr_binding.*` are
   read by nothing at all.** Declaring a key that nothing consults is worse than
   leaving it out — it advertises a control that does not exist.
6. **Do not restore `RESOLVE_NAME_SUGGEST=0.72`** (§ 3).
7. Verify the result by reading container env, never `docker compose config`.

## 6. Acceptance

- `docker compose exec worker env` and `… web env` between them contain every
  key `app/config.py` reads that is not on the code-only list.
- `DENTALIA_CONFIG_TOML` points at a mounted file; `load_config()` inside the
  worker reflects a value changed in that file after a restart.
- `.env.example` lists only keys that work: 15 env, a TOML example block, no
  code-only keys, `RESOLVE_NAME_SUGGEST=0.90`.
- `tests/test_compose_config.py` fails if a key is added to one service and not
  the other.
- No effective value changes: the § 3 diff, re-run, reports 0 changes.

**Estimate: half a day.**

## 7. Why it was deferred, and what would end that

Deferred by Denis on 2026-09-14, the day it was found, because nothing is
running on a wrong value and the client install had not happened yet. What makes
it urgent: **BC access arriving** (`BC_*` cannot reach a container at all, so
the BC switch is blocked on this), a client asking to change a budget cap or a
model tier, or a supplier complaining about fetch rate — the last of which is a
`domain_lease` row, not this work, if the per-host control is built instead.
