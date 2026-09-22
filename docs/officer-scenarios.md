# Officer scenarios - the comparison list

**What this is:** the fixed set of things a Dentalia compliance officer should be
able to do, each with a verdict, the route and `file:line` that covers it, and a
one-line command to re-check it. It exists so that "is this covered?" is answered
by running a check, not by remembering.

**Origin:** Denis stated these in the 2026-09-02 readiness session, in his own
words - "ideally when a dentalia officer opens admin, they would see how many
items are imported from bc, they would see how many manufacturers, they could
open status for any item ... notification for expired, notification for newly
discovered on eudamed, fetch on demand, fetch automatical?". They were answered
them with a verdict table that was never written down; this file is that table,
re-verified against the tree and made permanent.

**How to use it:** re-verify before you quote it. Every verdict below carries a
recheck command; run the ones you care about, correct the row, and change the
date in the heading. **Do not quote a verdict without re-running its check** -
this file goes stale exactly the way `PHASES.md`'s state block does, and the
2026-09-02 pass was already wrong about W8 within twelve hours.

**Verdicts:** `COVERED` (an officer can do this today) / `PARTIAL` (part of it
works, the gap is named) / `MISSING` (nothing does this).

**Last verified:** 2026-09-03 against master.

---

## The list

| # | The officer wants to | Verdict | Where |
|---|---|---|---|
| W1 | See how many items came from Business Central | **COVERED** | `GET /status` [web/app.py:2863](../web/app.py#L2863) (it was `GET /` until 2026-09-14; `/` is Today) |
| W2 | See how many manufacturers, and browse them | **COVERED** | `GET /manufacturers` [web/registry.py:1646](../web/registry.py#L1646) |
| W3 | Open one item and see whether we hold a document for it | **COVERED** | `GET /items/{item_ref}` [web/app.py:2117](../web/app.py#L2117) |
| W4 | See a manufacturer's ISO and other certificates | **COVERED** | `GET /manufacturers/{name}` [web/registry.py:1669](../web/registry.py#L1669) |
| W5 | See what EUDAMED says, including certificates we do not hold | **COVERED** | `certificate_findings` [web/registry.py:1302](../web/registry.py#L1302) |
| W6 | Be notified about expiring certificates, and chase them by email | **PARTIAL** | `/expiry` + a header counter; nobody to write to |
| W7 | Be notified about what is newly discovered on EUDAMED | **COVERED** | `GET /manufacturers/eudamed`, the cross-manufacturer digest |
| W8 | Press a button and make the system go look now | **COVERED** | ten controls, enumerated below |
| W9 | Have it happen automatically, without a human | **PARTIAL** | built, and seven switches ship off |
| W10 | See who decided what, and when | **COVERED** | `GET /audit`, nav **Decisions** |
| W11 | List the items holding no Declaration of Conformity | **COVERED** | `GET /coverage`, nav **Coverage gaps** |
| W12 | List the items we have never looked for | **COVERED** | `GET /discovery`, nav **Discovery** |
| W13 | Recover from losing the disk | **MISSING, deferred** | not part of the build; revisit when maintenance starts |

Denis's own three examples map onto this list: *"do we have a doc for a needle by
Ivoclar"* is **W3**, *"manufacturer coverage for Ivoclar"* is **W4 + W5**, and
*"send emails to companies whose certificates are expiring soon"* is **W6** - the
weakest row here, and the one to read first.

---

## W1. How many items came from Business Central

**COVERED.** The status board reads `count(*) FROM item_mirror` through
`_item_mirror_summary` ([web/app.py:1462](../web/app.py#L1462)) and renders it as
"N item(s) mirrored" ([web/templates/status.html:132](../web/templates/status.html#L132)).

```bash
grep -n "_item_mirror_summary" web/app.py && grep -n "mirrored" web/templates/status.html
```

## W2. How many manufacturers, and a browsable list

**COVERED.** `GET /manufacturers` renders a sortable, searchable table with the
count above it ([web/templates/manufacturers.html:33](../web/templates/manufacturers.html#L33),
"N entities"), ordered for triage: no playbook first, then largest catalogue
footprint.

```bash
grep -n "entit" web/templates/manufacturers.html
```

## W3. One item, and whether we hold anything for it

**COVERED**, and it is the deepest page in the UI. Per linked document it renders
type, regulation (MDR/MDD/n.a.), valid-from and valid-to with an explicit
**expiry basis** label (`stated` / `inherited` / `staleness`, so the five-year
Dentalia review horizon is never presented as a real expiry,
[web/templates/item_detail.html:90-100](../web/templates/item_detail.html#L90-L100)),
document status and link status as **separate** badges (a superseded document can
still carry a production link, and that is shown rather than merged), `match_basis`
(article-level `ref-item` / `ref-list` versus `mfr-scope`), a decide column, and
file/text links. Above it, `item_completeness`
([web/registry.py:460](../web/registry.py#L460)) scores DoC / IFU / notified-body
number per MDR compliance row.

```bash
grep -n "expiry_basis\|match_basis\|doc_status\|link_status" web/templates/item_detail.html | head
```

## W4. A manufacturer's certificates

**COVERED.** `GET /manufacturers/{name}` gives three tabs (all documents,
production documents, items) plus completeness
([web/registry.py:483](../web/registry.py#L483)), certificate findings and the
EUDAMED gap card.

```bash
grep -n "def manufacturer_completeness\|def certificate_findings" web/registry.py
```

## W5. What EUDAMED says, including what we do not hold

**COVERED.** `certificate_findings` ([web/registry.py:1302](../web/registry.py#L1302))
produces status alerts, revision drift, and a **"certificates we hold no copy of"**
block, which is exactly the "EUDAMED lists it, we do not have the file" case.
`declaration_gap_summary` ([web/registry.py:1414](../web/registry.py#L1414)) adds
the per-Basic-UDI-DI gap list and the sweep delta. Document-level EUDAMED device
lookup also sits on the document detail page.

```bash
grep -n "def certificate_findings\|def declaration_gap_summary" web/registry.py
```

## W6. Expiry notification, and chasing it by email

**PARTIAL, and this is the weakest row.** What exists: `/expiry`
([web/app.py:2437](../web/app.py#L2437)) is a full page with recently-expired /
expiring-soon / long-expired buckets, cross-linked to renewal drafts, and
`report.weekly` computes expiring and lapsed documents
([app/handlers/report.py:19](../app/handlers/report.py#L19)).

What is missing, in the order it bites:

1. ~~**It is pull-only. No badge anywhere.**~~ **Closed 2026-09-03.** The header
   strip carries a fifth counter, `expiring`, on every screen
   (`_pulse_counts`, [web/app.py](../web/app.py)), linking to `/expiry`. It
   counts recently-lapsed plus lapsing-soon, one per certificate rather than per
   document, and runs the literal `_LAPSING_CTE` / `_LAPSING_GROUP_BY` that
   `/expiry` runs, so the strip and the board cannot report different numbers.
   The long-expired backlog is deliberately outside it. Still pull in the sense
   that nothing reaches the officer who never opens the app.
2. **The weekly report is never emailed** — there is no `smtplib`, no `SMTP`,
   no `send_email` anywhere in `app/` or `web/`, and spec 7.1 says there never
   will be. **Softened 2026-09-03:** it is no longer invisible either.
   `report.weekly` now writes `report-<period_key>.html` into
   `scheduler.report_dir` and `/reports` lists them newest-first, so last
   month's report is as readable as this week's. It is still pull, and
   forwarding one is still a person opening it and sending it themselves.
3. ~~**The system offers a second draft after you have dealt with one.**~~
   **Closed 2026-09-03.** All three writers of `email_draft` now respect a
   settled draft. `email.request` treats `ready` / `sent` / archived as done
   for that period (it used to insert a twin beside an approved one, which is
   what stacked them); `email.reminder` regenerates an untouched draft instead
   of adding a second, and stops entirely on an archived one, while a *sent*
   reminder still escalates because that is what the ladder is for; the
   gap-request asks a manufacturer once, keyed on `email_draft.manufacturer`
   (migration 057) because a gap request has no `renewal_request` to hang a
   key on. Observed before the fix: four requests carrying two unsent reminder
   drafts each, and gap draft 25 archived on 2026-09-02 with draft 26 written
   anyway. The Cancel button is now labelled **Archive** and says what it now
   means.

4. **The chase mail is a draft, never a send.** `email.request` composes an
   `email_draft` row and stops there, by design (spec 7.1: the chain ends at a
   draft a person releases from `mdr@dentalia.si`). Its scheduler trigger is
   `SCHEDULER_EXPIRY_EMAIL_ENABLED`, **default `false`**
   ([docker-compose.yml:135](../docker-compose.yml#L135) and
   [:269](../docker-compose.yml#L269)).

5. **There is nobody to write to.** Measured live 2026-09-03: **384
   manufacturers, 0 with any `contact_emails`.** Outbound is built end to end
   and cannot address a single message. This is client input (gap G8), not
   engineering, and it is what actually keeps this row open.

So "send emails to companies whose certificates are expiring soon" today means:
see the `expiring` count in the strip, open `/expiry`, follow a draft link, copy
the text, and send it by hand from a mail client - once somebody supplies the
addresses. Nothing pushes, and nothing sends.

```bash
grep -rn "smtplib\|SMTP\|send_email" app/ web/ --include="*.py"    # expect: no hits
grep -n "EXPIRY_EMAIL_ENABLED" docker-compose.yml
sed -n '/def _pulse_counts/,/^def /p' web/app.py | grep -o '"[a-z_]*":'   # expect: expiring among them
docker compose exec -T postgres psql -U dentalia -d dentalia -At -c \
 "SELECT count(*) FILTER (WHERE contact_emails IS NOT NULL
     AND array_length(contact_emails,1) > 0), count(*) FROM manufacturer;"
```

## W7. New since the last EUDAMED sweep

**COVERED since 2026-09-03.** `GET /manufacturers/eudamed` (`eudamed_digest`,
[web/registry.py](../web/registry.py)) is the cross-manufacturer digest: one row
per manufacturer with an EUDAMED footprint, carrying new devices and status
changes since the last **reviewed** sweep, the declaration gap, and an explicit
**never swept** state rather than a zero. It reads `eudamed_gap_summary`,
`eudamed_sweep_delta`, `eudamed_sweep_state` and `eudamed_sweep_due` - the same
views the per-manufacturer card reads, minus their `WHERE canonical_name`, so
there is no second definition of a gap to keep in sync.

Read-only by design: releasing a sweep stays on `/manufacturers/sweep-due` and
the gap request stays on the manufacturer's page, each one row-linked.

Residue, not a gap in W7 as asked: `report.weekly` still contains the string
"eudamed" zero times, so none of this reaches anyone who does not open the app.
Measured 2026-09-03: 238ms over 27 swept manufacturers.

```bash
grep -n "def eudamed_digest" web/registry.py
grep -c -i eudamed app/handlers/report.py     # still 0 - the weekly report carries none of it
```

## W8. Fetch on demand

**COVERED**, and wider than it was: two manufacturer-level buttons landed on
2026-09-02 after the original audit ran.

| Control | Route | Enqueues | Gate |
|---|---|---|---|
| Re-discover, on the item page | `POST /items/{item_ref}/rediscover` [web/app.py:2043](../web/app.py#L2043) | `discover.group` (`ignore_recency`) | item must be in a resolved group |
| **Search a whole supplier** | `POST /manufacturers/{name}/discover` [web/registry.py:1546](../web/registry.py#L1546) | `discover.group` per uncovered group | capped at `discovery.manufacturer_button_cap`, default 25 ([app/config.py:166](../app/config.py#L166)) |
| **Ask for missing declarations** | `POST /manufacturers/{name}/gap-request` [web/registry.py:1609](../web/registry.py#L1609) | `email.request` (`reason='eudamed-gap'`) | draft only, never sends |
| Look up in EUDAMED | `POST /documents/{doc_id}/eudamed` [web/app.py:2077](../web/app.py#L2077) | `eudamed.sync` | document needs a Basic UDI-DI |
| Release a EUDAMED sweep | `POST /manufacturers/{name}/sweep` [web/registry.py:1507](../web/registry.py#L1507) | `eudamed.sweep` | reachable only from `/manufacturers/sweep-due`; refused without a trusted SRN |
| Re-run a dead job / group | `POST /dead/{job_id}/rerun`, `/dead/rerun-group` [web/app.py:3773](../web/app.py#L3773), [:3733](../web/app.py#L3733) | the original job type, `interactive` | job must be `dead`; the group is the developer section only, and the buttons render for operators only (`web.operator_users`) |
| **Try the N again** (Failed, websites that took too long) | `POST /dead/retry-timeouts` (`web/app.py`, `dead_retry_timeouts`) | the original job type, `interactive`, one per counted timeout | every login; the set is re-derived from `web/failures.py` |
| **Search again for these N** (Failed, addresses that no longer work) | `POST /dead/search-again` (`web/app.py`, `dead_search_again`) | `discover.group` per distinct `group_id`, key `discover:failed:{group_id}`, `interactive` | every login; a dead address with no `group_id` is counted and named, not searched |
| Retry discovery from Manual | `POST /manual/{task_id}/resolve` [web/app.py:3684](../web/app.py#L3684) | `discover.group` | only `kind='discovery-dead-end'` |
| Run now, on Scheduler | `POST /scheduler/run/{cron}` [web/scheduler_view.py:401](../web/scheduler_view.py#L401) | `report.weekly`, `email.poll`, `eudamed.certregister` | 404 for any other cron |
| Re-arm a stopped cron | `POST /scheduler/arm/{cron}` [web/scheduler_view.py:421](../web/scheduler_view.py#L421) | `scheduler.tick` | no-op if already armed |
| BC import / PDF upload / vendor master | `POST /ingest`, `/upload`, `/import`, `/import/vendors` | `ingest.run`, `upload.ingest`, `vendor.import` | imports are preview-then-apply |

Still absent: nothing re-checks **all** items past their recency window in one
press. The manufacturer button is the closest thing and it is capped at 25 groups
per press; the scheduled equivalents (W9) are capped the same way and default off.

```bash
grep -n "@app\.post(" web/app.py web/registry.py web/scheduler_view.py
```

## W9. Automatic fetch

**PARTIAL, and the reason is a switch, not a hole.** Eight crons run in
[app/scheduler.py:520-541](../app/scheduler.py#L520-L541): `ingest.monthly`,
`expiry-scan`, `failure-monitor`, `coverage-scan`, `report.weekly`, `email.poll`,
`eudamed.certregister`, `eudamed.sweep-due`. `/scheduler` labels each ON or OFF
with an armed column.

**Two of them do fetch documents on a schedule, and both are off by default:**

- `coverage-scan` ([app/scheduler.py:236](../app/scheduler.py#L236)) sends groups
  holding no production document through `discover.group`, capped at 25 a day
  (`SCHEDULER_COVERAGE_SCAN_ENABLED`, default `false`,
  [docker-compose.yml:138](../docker-compose.yml#L138)). Measured 2026-09-02:
  6.732 groups hold no production document, so at the cap this is a ~9-month
  backfill.
- The expiry re-look inside `expiry-scan` enqueues `discover.group` for expiring
  documents' groups (`expiry_rediscover_enabled`,
  [app/config.py:535](../app/config.py#L535), default `false`, reachable as
  `SCHEDULER_EXPIRY_REDISCOVER_ENABLED` at
  [docker-compose.yml:136](../docker-compose.yml#L136) and
  [:270](../docker-compose.yml#L270)).

Off by default alongside them: `SCHEDULER_INGEST_WATCH_DIR` (empty, so the
monthly BC re-ingest is a no-op), `SCHEDULER_EXPIRY_EMAIL_ENABLED`,
`SCHEDULER_FAILURE_REONBOARD_ENABLED`, `SCHEDULER_EMAIL_POLL_ENABLED`,
`SCHEDULER_EUDAMED_CERTREGISTER_ENABLED`. Seven switches in all, every one of
them an env var on `worker` - which is where the crons run since the standalone
`scheduler` service was deleted 2026-09-02 - so this is a
`.env` decision about pace and spend, not an engineering task.

What runs today is measurement only: the expiry scan's count, the failure
monitor, the weekly report, and "mark EUDAMED sweep due". So the honest verdict
is that **automatic document fetching is built and disabled**, not absent.

```bash
grep -n "SCHEDULER_.*ENABLED\|SCHEDULER_INGEST_WATCH_DIR" docker-compose.yml
grep -n "coverage_scan_enabled\|expiry_rediscover_enabled" app/config.py
sed -n '519,542p' app/scheduler.py    # the eight crons
```

---

## W10. Who decided what, and when

**COVERED since 2026-09-03.** `GET /audit` (nav **Decisions**) renders
`audit_log` with a filter on event and a search over `decided_by`, `item_ref`
and `doc_id`, paginated on the repo's `page+total` convention. 2.144 rows, 12
event kinds at the time of writing.

Two things it gets right rather than incidentally: the event filter is built
from the table's own `DISTINCT event` instead of a list mirrored in the module,
because `audit_log.event` is plain text with no CHECK and a hand-kept list would
be one more thing to drift. And a row whose `via_job` no longer resolves still
renders - invariant 10 says the row is self-contained, `job_snapshot` is
`NOT NULL` in the schema to enforce it, and a test asserts the page survives an
orphaned job reference.

```bash
grep -n "def audit" web/app.py
```

## W11. Which items hold no Declaration of Conformity

**COVERED since 2026-09-03.** `GET /coverage` (nav **Coverage gaps**) counts
three gaps apart and lists the articles behind each, paginated:

```
4.265 device articles
    8  no document at all
2.804  no Declaration of Conformity   (by document type)
2.578  no MDR or MDD document         (by the regulation it was issued under)
```

**Showing all three is what unblocked it.** The reason this row stayed MISSING
after the rest were built is that "no declaration" has two defensible readings
and picking one silently is how a board and an API come to disagree in front of
a client. The page publishes **no percentage of its own** - the headline number
remains `[kpi-coverage-denominator]`'s business, and counts-with-lists do not
collide with it.

Each row carries **what the article does hold**, because "no DoC" reads very
differently against an EC certificate than against nothing. The 11.693 articles
with no BC device class are named on the page and deliberately excluded from
every count: we cannot say what they need, and guessing would invent work.

One scan, not three - the obvious correlated `NOT EXISTS` per gap measured
665ms against **14,7ms** for the `held` CTE, same numbers.

```bash
grep -n "_COVERAGE_GAPS" web/app.py
```

## W12. Which items we have never looked for

**COVERED since 2026-09-03.** `GET /discovery` (nav **Discovery**) shows the
three states as a filter - never searched, searched and found nothing, searched
and found something - over `discovery_log`, one row per group, ordered by
medical-device items descending so the head of the list is where a search buys
the most. The headline is the number that reframes coverage:

```
4.256 of 4.265 medical-device items have never been searched
8.208 groups · 8.119 never searched · 0 searched and found nothing · 89 searched and found something
```

The middle state is empty today and is still rendered, deliberately: "we found
nothing" and "nobody looked" are different facts about our own diligence, and a
page that only showed the states with rows in them would hide exactly that.
An unknown `?state=` falls back to `never` rather than rendering an empty table
that reads as nothing to do.

Read-only. Searching starts from the manufacturer's own page, which walks the
same list in the same order.

```bash
grep -n "_DISCOVERY_STATES" web/app.py
```

## W13. Recover from losing the disk

**MISSING, and deliberately not being built.** Denis, 2026-09-03: backup was
not part of the build, so it waits for the maintenance phase. This row
stays on the list because the exposure is real and someone will otherwise
re-derive it as a finding; it is not open work, and it is not to be quoted
as new scope without that conversation.

**The exposure, unchanged.** Neither `backup` nor `pg_dump` appears anywhere in
`docker-compose.yml` or `scripts/`. Nothing is scheduled, and there is no restore
procedure to rehearse. What exists is a **partial, manual** dump documented at
[docs/runbook.md:551](runbook.md#L551) - `fetch_log`, `document_text`,
`extraction_attempt`, `extraction_cost`, data-only - written to make a database
*reset* cheap, not to survive losing the volume. The registry itself and the
`archive_url` file tree are unprotected.

Ten-year retention (invariant 4) and an append-only supersession record are
compliance promises. Today both live on one unbacked Docker volume.

```bash
grep -rin "backup\|pg_dump" docker-compose.yml scripts/    # expect: no hits
```

---

## Two gaps that sit underneath the list

Both were found by the same audit, both still hold on 2026-09-03, and neither is
any single W row.

**There is no "items with no document" list.** `/items` takes `q` and `page` and
nothing else ([web/app.py:1971](../web/app.py#L1971)): it shows a
`production_docs` count per row but offers no filter and no sort for "count = 0".
`manufacturer_completeness` reports the number per manufacturer as a plain cell,
not a link, so there is no click-through to *which* items. The KPI board gives a
registry-wide percentage. The one question an officer most wants to act on - show
me what is uncovered - is answerable only by scanning.

**One shared login.** `Caddyfile:75-76` defines exactly one `basic_auth` pair.
`decided_by` on every GATE decision is the Caddy username, so the plumbing is
right, but with one credential every reviewer records the same author. Two-person
accountability on MDR sign-off needs distinct Caddy accounts, which is a Caddyfile
edit, not code.

```bash
sed -n '1971,1974p' web/app.py; grep -n "basic_auth" -A 2 Caddyfile
```

---

## What changed since the first pass

Kept so the list's own drift is visible.

| Date | Row | Change |
|---|---|---|
| 2026-09-02 | W8 | Original audit said "no button re-runs discovery for a whole manufacturer". Wrong within hours: two changes that day added the capped supplier-wide discover button and the gap-request draft |
| 2026-09-02 | W8/W9 | A change that day fixed every Run now and Re-arm button (they were inert); another added Run now for the certificate register, making the runnable set three crons, not two. [web/scheduler_view.py:11-18](../web/scheduler_view.py#L11-L18) still says "the two crons" and is stale |
| 2026-09-03 | W9 | First wording of this row said no cron re-checks a manufacturer for documents and that the always-running ticks are measurement. Both wrong: `coverage-scan` (a capped daily `discover.group` producer) was wired on 2026-09-02 and `expiry-scan` carries a re-look of its own. Corrected the same day - the gap is that seven switches ship off, not that the automation is unbuilt |
| 2026-09-03 | W10-W13 | Four scenarios added from the candidate sweep of the web UI, the queue and the PHASES map: the audit trail, the no-DoC list, the never-searched list, and backup. All four measured or grepped the day they were added |
| 2026-09-03 | W9 | Second correction the same day: I wrote that `expiry_rediscover` was not in `docker-compose.yml`. It is, on both services - I had grepped the lower-case config key against a file that spells it `SCHEDULER_EXPIRY_REDISCOVER_ENABLED`. All seven switches are reachable by env var |
| 2026-09-03 | W6, W7 | Built. W6 lost its "no badge" leg (the `expiring` counter, sharing /expiry's own query so they cannot drift) and stays PARTIAL on the other two. W7 closed with `/manufacturers/eudamed`, the cross-manufacturer digest |
| 2026-09-03 | W10, W12 | Built. Both needed no migration - `dentalia_api` already held SELECT on `audit_log` and `discovery_log`, which is what made them an afternoon rather than a session |
| 2026-09-03 | W13 | Ruled deferred by Denis: not part of the build, revisit when maintenance starts. Kept on the list so the exposure stays visible, moved out of open work |
| 2026-09-03 | W6 | An archived or sent draft now stops the system offering another (Denis: "the system may not offer a new one if this is marked as archive, or sent"). Three writers fixed, migration 057 gives a gap request its own manufacturer. W6 stays PARTIAL on contacts alone |
| 2026-09-03 | W11 | Built as three counts with three lists rather than one contested number, which is what let it ship without settling `[kpi-coverage-denominator]` first (Denis: "counting this specifically, then for each type of no document, listing all items") |
| 2026-09-03 | W6 | The weekly report became a durable timestamped file with a page over it (Denis: "create a dump - html in a dedicated location, then add a web view that read them"). W6 is now PARTIAL on one leg only: nobody to write to, which is G8 |

## Adding a scenario

Add a `W<n>` row to the table, a section with the same four parts (verdict, what
exists with `file:line`, what is missing, a recheck command), and a line in the
change table. Keep the numbering stable, retire a row by marking it rather than
renumbering - PHASES.md, followups and past reports cite these ids.
