# Office UI redesign: design

**Status:** approved by Denis on 2026-09-11. He approved proposals P1 to P7 and asked
for them to be built. The seven decisions below take the defaults the
proposal offered.
**Sources:** the screen audit and the "Dentalia Office Redesign" proposal, both of
2026-09-11. They are local HTML files, not in the repo, because they carry client data.
**Audience:** office staff with no technical background. English UI (Denis's ruling).
Laptop screens, 768 px wide and up. No phone layout.

Figures quoted below are dev-database readings from 2026-09-11 and serve as examples
only. Every count on screen is computed live.

## 1. Eight rules for every screen

1. **Name first, number second.** A document, task or item is shown by its human
   name (manufacturer, product, file). The id is still there, but small.
2. **One word per thing.** The menu, the page title, the status pill, the button and the guide all use the
   same word, taken from the word list (§ 9), and the mapping lives in one place in the code.
3. **One sentence saying what the page is for.** Design notes move to `docs/dev/` or
   under a collapsed "Technical details".
4. **Every action says what it will do.** An action that touches many records, touches an
   outside system, or cannot be undone asks once, and the question states the count.
5. **Receipts in words, not job numbers.**
6. **A queue shows only what the person can act on.** It is grouped by the task,
   oldest first, 20 to a page (Review keeps its 50).
7. **Every number says what it counts.** A figure whose definition is not on screen is
   not shown.
8. **Machine health is one line for the office.** Operators see the detail.

## 2. Decisions (all at the proposal's default)

| # | Decision | Ruling |
|---|---|---|
| D1 | Word list | Adopt § 9 as written |
| D2 | Logins | One Caddy login per person, plus a short list of operator logins |
| D3 | Operator screens | Hidden from office logins, but a screen is still reachable if you type its URL. Four risky writes are refused for non-operators: playbook edits and code claims, BC push apply, scheduler run/arm, and `/ingest`. Office keeps `/import` |
| D4 | Coverage headline | "Declarations on file for X of Y medical-device items (Z%)" |
| D5 | Decide only with the document open | Yes. No Approve or Reject on a collapsed Review row |
| D6 | Who may re-run failures | Office logins get "took too long" only. Operators get everything |
| D7 | Expiring window | 30 days, labelled wherever an expiring figure appears |

**Operator identity until named logins exist.** `web.operator_users` (env
`WEB_OPERATOR_USERS`, comma-separated) lists the operator logins. While it is empty,
every login counts as an operator. That keeps today's single shared login working
unchanged. Slice P5b fills the list.

## 3. P1 Safety rails

### Shared: a generic two-step confirm
Today `_result.html`'s `confirm_needed` branch is hard-wired to Rename (hidden
`new_name` and `note`, button "Yes, rename it"). It becomes generic:
- `confirm_needed`: the lines to show.
- `confirm_url` and `confirm_target`: unchanged.
- `confirm_fields`: a dict rendered as one hidden input per key.
- `confirm_label`: the button text.
- A Cancel control clears the result area.

Rename keeps its exact wording and behaviour.

### P1a Review
- A collapsed Review row shows **Open** only. Approve, Reject and the manufacturer
  binding controls render only in the open detail panel (D5).
- **A whole-range approval** (`bind-manufacturer`) is two-step on the server. The first
  POST writes nothing and returns the confirm. Only the POST with `confirm=1`
  enqueues. The confirm names the count N, which comes from the same computation that
  fills `bind_items` today, and reads:
  - "Approve for all N ⟨MANUFACTURER⟩ items?"
  - "This declaration says it covers everything ⟨MANUFACTURER⟩ makes, so approving it
    makes it count for every ⟨MANUFACTURER⟩ item Business Central marks as a medical
    device."
  - "N items will show this declaration." Add ", on screen and on their Business
    Central item card" only if the implementer verifies that the BC item card shows
    documents linked this way.
  - "It cannot be undone from this screen. A newer declaration can replace it later."
  - Button: "Yes, approve for N items".
- **Reject asks why.** In the open panel, Reject reveals six reasons: Wrong manufacturer · Not one of
  our items · Not a compliance document · Out of date · Duplicate of another document · Other.
  It also shows an optional note field, the line "A rejected document stays on file and can be reopened. Your
  name and the reason are recorded.", and Reject / Cancel buttons. A reject POST without a reason gets
  422 with a plain message.
- **The reason travels on `gate.apply`** as a new optional payload field `note`, a string:
  `"<reason>"` or `"<reason>: <free text>"`. The change is additive, so consumers must
  tolerate its absence. The dedupe key stays `apply:{doc_id}:{decision}`. The `gate.apply`
  handler writes `note` into the decision's `audit_log.detail`, and the Decisions page
  shows it. The PRD's `gate.apply` payload row and the handbook gain the field. This is
  the only payload change in the whole redesign.

### P1b Other one-click writes
- **BC push.** The top of the page says whether sending is on (`bc.write_enabled`):
  - Off: "Sending to Business Central is switched off. This page shows what would change;
    nothing is sent."
  - On: "Sending to Business Central is switched on. Applying sends these changes."

  The row-cap note moves to the top ("Showing the first 1,000 of N differences." when
  N > 1,000). Apply is two-step, and the confirm names the count and says whether
  anything will actually be sent.
- **Claim another BC code** (playbook page). The select gets an empty first option
  "Choose a code…" and is required. Submitting is two-step, and the confirm names the
  current owner, for example "Move 10044 from FUTURA DENTAL to GC?". It also carries
  today's note about the playbooks sync.
- **Browser confirm.** SRN Confirm and Reject, Release (the EUDAMED check), Take out
  of the queue, Update Business Central and Re-discover each get an `hx-confirm` of one
  sentence that says what happens and to what.

## 4. P2 Failed split, and the Today page

### Failed split (P2-failed)
- **One classifier,** `classify_failure(job_type, last_error) -> "timeout" | "dead-address" |
  "developer"`, tested from a table:
  - `timeout`: the error is a timeout (`TimeoutError`, `ConnectTimeout`, `ReadTimeout`
    and the like) on a job type that contacts supplier websites.
  - `dead-address`: a `fetch.url` job that failed with HTTP 404 or 410, or whose host
    name did not resolve.
  - `developer`: everything else.

  The implementer derives the exact error patterns from the handlers' error formatting
  and from the live dead jobs (read-only).
- **`/dead` shows three sections, in this order:**
  - **Websites that took too long (N).** Its line reads "Usually temporary. Trying again
    often works." The section lists the domains and has a button "Try the N again". The
    button re-enqueues each job at interactive priority, the same way `dead_rerun_group`
    does.
  - **Addresses that no longer work (N).** It explains that the same address will fail
    again and that searching again looks for the new address. Entries are sub-grouped
    by error kind ("Page no longer exists", "Website not found"), each with its domains
    (the first 4, then "+k more"). The button "Search again for these N" enqueues one
    `discover.group` per distinct `group_id` found in those payloads.
  - **For the developer (N).** It explains that retrying will not help until the cause is
    fixed. For office logins it has no buttons (D6). Operators keep the existing
    per-group re-run controls. The per-cause detail sits under "Show technical details".
- **Retried work stops counting.** A dead job with newer work queued for it drops out of
  the actionable totals on every screen. For a re-run, that newer work is a job with the
  same dedupe key and a higher id. For a dead address, it is a `discover.group` for
  its `group_id` created after the job died. Such a job is shown as "trying again" or
  "searching again" instead.
- **Operator status** comes from `web.access.is_operator(user, operator_users)` (§ 2),
  with the config key introduced here.

### Today page (P2-today)
- **`/` becomes Today.** The KPI board moves to `/status`, labelled "System status" in the
  operator menu group, and the logo links to `/`.
- **Four lists,** each with a count, one sentence and one button, drawn as the Today mockup
  shows:
  1. **Documents to review**: the staged count, the oldest waiting date, the top three
     manufacturers with their counts, and the button "Start reviewing" (to `/staging`).
  2. **Missing documents**: the count of open `discovery-dead-end` tasks, the top three
     manufacturers, and "Open the list" (to `/missing`).
  3. **Expiring certificates**: the D7 figures with their window stated, and "See which"
     (to `/expiry`).
  4. **Renewal emails to send**: the count of drafts not marked sent, the line "Written by
     the system from what is expiring. It never sends them: read, edit, send from your own
     mailbox, then mark as sent.", and "Open drafts" (to `/drafts`).
- **Below the lists come three lines from the Failed split:** "N supplier websites took too long
  to answer… [Try the N again]", "N document addresses no longer work… [Search again]",
  and "N technical faults are waiting for the developer. Nothing to do here. [What are
  they?]". A line whose count is zero is hidden.
- **Next is the coverage headline** (D4, the helper from P6) with its definition line
  ("Counts the items Business Central marks as medical devices. Another K items have no
  device class in BC and are not counted.") and a link to the items that have no
  declaration.
- **Last is the weekly summary:** a link to the latest report, "Week 37 (7–13 Sep)",
  plus the previous week.

## 5. P3 Missing documents
- **A new office page `/missing`** lists open `discovery-dead-end` manual tasks. It is sorted
  oldest first, shows 20 to a page (the existing pager), and can be filtered by
  manufacturer: chips for the six manufacturers with the most tasks, then "+ K more".
  Review items (`gate-manual`) and `dead-job-followup` do not appear here.
- **Each card has:**
  - "waiting since ⟨date⟩" and the product name, taken from the task's `label`.
  - The manufacturer and item numbers from `item_group_member` / `item_mirror`: "item X"
    for one, the list for up to 3, and "N items: a, b, c, +k more" beyond that.
  - One sentence: "Searched ⟨sources in plain words⟩ on ⟨date⟩. Nothing found." Sources
    come from `sources_tried`.
  - Four actions:
    - **Upload what I found**, which opens the existing `/upload?group_id=…&manual_task_id=…`.
      The upload handler already closes the task on the worker side.
    - **⟨Manufacturer⟩ downloads ↗** and **Search the web ↗**, from `prefilled_search_links`
      or the playbook's document sources, and only when present.
    - **Search again**, the existing `POST /manual/{id}/resolve`, with a receipt in words.
- **The Upload page,** when opened for a task, names the item it is for ("Uploading a
  document for ⟨product⟩ (⟨manufacturer⟩, item ⟨ref⟩)"). The "Target group id" and
  "Priority" fields leave the form: the group and task travel as hidden inputs, and
  priority takes the value the form sends today by default. Upload without a task keeps
  working.
- **`/manual` stays** for operators, behind Queues & health, unchanged.
- **"Ask the manufacturer" is not in this slice.** It needs a new `email.request` reason,
  which is a handler change.

## 6. P4 Review with the document and its items in view
- **Grouping.** The Review list is grouped by manufacturer, and documents with no matched manufacturer form a
  "Manufacturer not known" group at the end. Groups and rows run oldest first. Each group
  header reads "⟨MANUFACTURER⟩ ⟨N⟩ documents". Reason filter chips are applied on the
  server: All reasons · Which items is unclear · Manufacturer unclear · Covers a whole range ·
  Expired · Other. Each existing review reason maps to one chip, and unmapped reasons go to
  Other. The pager stays at 50.
- **A row** reads "⟨MANUFACTURER⟩ · ⟨document type in words⟩ (⟨regulation⟩)". Below it: "⟨file
  name⟩ · issued ⟨date⟩ · waiting since ⟨date⟩", one plain sentence giving the review
  reason, and "Open". The file name is the last segment of `source_url`.
- **The open panel:**
  - **Left:** page 1 of the PDF in an `<iframe>` of `/documents/{id}/file`, plus
    "Open full size ↗" in a new tab. The iframe exists only in the opened panel, so the
    list itself loads no PDF bytes.
  - **Right:** the facts in plain words. "Manufacturer" (as printed on the document),
    "Rules" (MDR or MDD in words), "Issued", "Valid until" ("Not stated" when absent),
    and Basic UDI-DI. No new domain rules are invented for missing dates.
  - **Above the buttons:** "Approving makes it count for these N items", listing item
    number and name for each staged link (the first 10, then "+ K more", which
    expands). Under the list: "Matched through ⟨how it was matched in words⟩". For a
    whole-range document the line reads "…for all N ⟨MANUFACTURER⟩ items", and the P1a
    confirm still applies.
  - **The buttons:** "Approve for these N items" ("Approve for this item" when N = 1),
    "Reject…" (the P1a flow), and "Correct a fact first", which holds the existing edit
    fields, collapsed.
  - **Technical details** (evidence, match basis, the record) stay collapsed.
- **Performance** (measured 2026-09-11 on dev, 234 staged documents):
  - **PDFs** for staged documents: median 345 KB, p90 2.6 MB, largest 16 MB. Serving takes a
    median of 26 ms and 48 ms at p90. `Accept-Ranges` is already honoured, but no request
    ever gets a 304, so every reopening downloads the whole file again. Fix:
    `/documents/{doc_id}/file` adds
    `Cache-Control: private, max-age=31536000, immutable` to 200 responses only. The
    bytes are hash-addressed and never change for a document.
  - **No server-side rendering.** The browser's own PDF viewer draws the page, and
    `Dockerfile.web` stays slim with no PyMuPDF (deliberate, see its header).
  - **The items query** is a sequential scan of `item_document` by `doc_id` (no index
    leads with `doc_id`) and takes 0.7 ms over 8,729 links. It needs no index now.
  - **Budget:** `/staging` renders in under 300 ms on dev. The implementer measures it
    before and after and reports both numbers.

## 7. P5 Menus and logins

### Office menu (with the Today slice)
**Office groups.** Counts load the way `/_pulse` loads today, and the top pulse strip goes.

| Group | Entries |
|---|---|
| Your work | Today `/` · Review `/staging` (n) · Missing documents `/missing` (n) · Expiring `/expiry` (n) · Renewal emails `/drafts` (n) |
| Records | Items `/items` · Documents `/documents` · Manufacturers `/manufacturers` · EUDAMED checks `/manufacturers/eudamed` · Emails received `/emails` |
| Add | Upload a document `/upload` · Import from Business Central `/import` |

**Operator group,** a collapsed block at the bottom: System status `/status` · Failed tasks `/dead` ·
Queues & health `/pipeline` · Playbooks `/playbooks` · Data quality `/data-quality` ·
Decisions log `/audit` · Scheduler `/scheduler` · Business Central push (its existing
route) · API `/api-reference`.

**Pages that leave the menu stay reachable:**
- Coverage gaps and Discovery through a link row on Items: "Show: All items · Without
  a declaration · Never searched".
- SRN queue and Sweep due through links on the EUDAMED checks page.
- Onboard a supplier through a link on Playbooks.
- Weekly reports through Today.

Turning Coverage and Discovery into true Items filters, and building one EUDAMED checks
page with tabs, are later slices.

**Health line at the bottom of the sidebar:** "● System working", or the existing health
signal's failure state, plus "N websites timed out · details" when N > 0.

Until named logins exist, everyone sees the operator group, collapsed.

### P5b Named logins
- **Caddy `basic_auth`** takes one line per person, read from a users file mounted into
  the `caddy` container. The existing `DENTALIA_WEB_USER` / `DENTALIA_WEB_PASSWORD_HASH`
  pair keeps working. The syntax is verified against Caddy's documentation and
  `caddy validate` before it is relied on. Caddy passes the authenticated login in
  `X-Forwarded-User`.
- **Setting `WEB_OPERATOR_USERS` switches on two things:**
  - The operator menu group is hidden from everyone else.
  - The four D3 writes answer 403 with a plain HTML page for everyone else.

  Their pages stay reachable (D3).
- **The runbook** covers adding a person (`caddy hash-password`) and making them an
  operator. The config reference gains the key, and `docs/decisions.md` records the
  ruling.
- **Decisions** already record the proxy's user, so each person's login replaces the
  shared `admin`.
- **Who the office users are** is Denis's input. The slice ships the mechanism with
  today's login as the only operator.

## 8. P6 Figures that agree, and the weekly report fixed
- **Weekly report** (`app/handlers/report.py`). The template reads keys the query does not
  return (`manufacturer`; `expires` where the query returns `validity_to`), so both
  columns are always empty. The fix:
  - Join the document's manufacturer.
  - Read the date from `validity_to`.
  - Link each row to `/documents/{id}`.
  - Word the machine line plainly.
  - Add a test that asserts non-empty Manufacturer and Expires cells.

  This closes `[weekly-report-columns-always-empty]`.
- **The status board's in-flight tile** uses the pulse strip's definition (`in_flight_docs`,
  distinct documents being read) under the label "Documents being read". Today it
  counts every pending or running job, including the 10 perpetual `scheduler.tick` jobs.
- **Coverage headline (D4).** A helper `coverage_headline(conn) -> dict` with keys
  `covered`, `total`, `pct` and `unclassified`:
  - `total` = items BC marks as medical devices.
  - `covered` = those with at least one production declaration.
  - `total - covered` must equal the "no declaration" count on Coverage gaps.

  It replaces the 99.7% on the status board, and Today reuses it. The four existing
  coverage definitions stay one click down, each with its name.
- **Expiring (D7).** One window constant, 30 days, is shared by the pulse or menu count,
  the Expiry page default and the weekly report. Each figure states its window.
- **Queues & health.** The "19,345 waiting" tile says what it counts (the proposal found
  catalogue notes). The implementer verifies what it counts and names it.

## 9. P7 One vocabulary and a consistent look

### Word list (stored values unchanged, only the screen word)
| Today on screen | Word | Note |
|---|---|---|
| production (pill) | Published | Counts for its items (glossary: "It counts. Published") |
| staged | Waiting for review | |
| filed | On file | Genuine, but covers none of our items |
| superseded | Replaced | By a newer document |
| rejected | Rejected | |
| dead · dead jobs | Failed | |
| processing · in flight | Being read | Operators only |
| Manual (queue) | Missing documents | Office list of dead ends only |
| gate-manual · discovery-dead-end · dead-job-followup | (not shown) | Each lives on the page that resolves it |
| Drafts out · Renewal drafts · CHASE | Renewal emails | |
| Emails in · Emails | Emails received | |
| Status · Queue status · KPI board | Today (office) · System status (operator) | |
| Sweep · Release · sweep due | EUDAMED check · Start the check · Check due | |
| SRN | EUDAMED ID (SRN) | Spelled out on first use per page |
| recipe · playbook · slug | Playbook · Short name | |
| canonical entity · alias source · vendor-master | Manufacturer · Name in Business Central | |
| Item · article · product | Item | "Item no." for the ref |
| mfr_ref | Manufacturer's article no. | |
| match basis values | How it was matched | Technical details only |
| Target group id · Priority | (removed) | Upload names the item instead |

### Slices
- **P7a Foundations:**
  - `web/words.py` holds the mapping, exposed as a Jinja filter `word`. Unknown values
    pass through unchanged.
  - `doc_display_name(row)` returns "⟨Manufacturer⟩ · ⟨type word⟩ (⟨regulation⟩)". It
    falls back to the file name, then to "Document #id".
  - **Receipts in words.** Every job receipt in `_result.html` becomes a truthful
    sentence per action, and the job number moves under "Technical details". The work
    is queued, not done, so a receipt never claims it is done: "Approval recorded. The
    registry updates within a minute." "Deduped…" becomes "Already in progress."
  - **Error pages.** An HTML error page, with the menu, serves browser requests outside
    `/api/`. HTMX requests get the `_result.html` error partial. `/api/*` keeps JSON.
  - **Buttons.** Primary `#2F7A58` (5.19:1 on white), plus secondary and danger styles.
    The disabled state stays legible.
- **P7b Registry pages:** Items, the item card and detail, Documents, document detail,
  Manufacturers, manufacturer detail, the EUDAMED pages, Expiry, Decisions and Search.
  For each:
  - Apply the words and give it a one-sentence intro.
  - Show local timestamps without microseconds, and label weeks "Week 37 (7–13 Sep)".
  - Move "Catalogue LJ", model names and source keys under Technical details.
- **P7c Correspondence and add pages:** the same treatment for Emails, Drafts and the
  draft detail, Upload, Import and its previews, Onboarding, and Reports.
- **P7d Guide:** the glossary (EN and SL) becomes the other half of the word list.
  Every guide page is swept in both languages and the bundles are rebuilt.

Operator screens change only through shared components; redesigning them is out of scope.

## 10. Constraints on every slice
- **The `web` process stays a job producer.** It writes nothing to `document`,
  `item_document` or `evidence`. The four decisions still go through `gate.apply`.
- **No new job types and no migrations.** The only payload change is the additive `note` (§ 3).
- **No new dependencies,** in either `pyproject.toml` or the Dockerfiles.
- **Each slice ships with:**
  - its tests, run through `./scripts/test.sh` (a failing test first for behaviour);
  - its guide pages in EN and SL, with the bundles rebuilt by `python3 scripts/build-guide.py`;
  - the dev docs its change invalidates (CLAUDE.md, "Self-document on change").
- **Rendered-text tests are expected to change.** `tests/test_web.py` asserts rendered text
  throughout, so a changed word updates its test in the same slice.

## 11. Not in scope
- A Slovenian interface.
- A phone layout.
- Redesigning operator screens beyond moving them.
- Any change to matching rules or GATE semantics.
- "Ask the manufacturer" from Missing documents.
- True Items filters for Coverage and Discovery.
- The combined EUDAMED checks page.
- Adding the office users themselves (Denis's input).
