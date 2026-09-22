# Office UI Redesign Implementation Plan

**Goal:** Make every office-facing screen usable by non-technical staff: safe actions, a Today page, a Missing documents list, Review with the document in view, figures that agree, one vocabulary, and named logins.

**Architecture:** FastAPI + Jinja + HTMX producer UI (`web/`). Each slice changes routes in `web/app.py` (or a focused helper module), its templates, its tests, and its guide pages. Only P1a touches a handler (`gate.apply` writes the new optional `note` into `audit_log.detail`).

**Tech Stack:** Python 3.12 sync, psycopg 3 raw SQL, Jinja2, HTMX, pytest against real Postgres via `./scripts/test.sh`.

**Spec:** `docs/superpowers/specs/2026-09-11-office-ui-redesign-design.md`. It is the binding authority. Section numbers below (§) refer to it.

**Format note:** these tasks are requirement briefs, not transcribed code. Each implementer reads the named code, writes the listed tests first (red), then implements (green). The spec and this plan fix the exact values, copy and interfaces. The implementer's judgement covers the rest, following the surrounding code's patterns.

## Global Constraints

- **Worktree only.** Work only in your assigned worktree and commit on its branch. Never merge, rebase, push, or touch another branch.
- **No `docker compose` from a worktree.** A second compose stack started there shares the Postgres data directory, which corrupted the database on 2026-09-11. Also never run these, from anywhere: `scripts/deploy.sh`, `docker compose up`, `down`, `build`, `stop`, `restart`, `run`.
- **Tests only through `./scripts/test.sh <paths>` run from your worktree.** It targets the main stack's `test` container and maps your worktree in.
- **Dev database: reads only.** Use `cd /srv/dentalia && docker compose exec -T postgres psql -U dentalia -d dentalia -Atc "SELECT …"`. Never write to it.
- **No installs.** No package installs, no new dependencies, no new CLIs. The web is for documentation lookups only.
- **Files that belong to other sessions or to the controller.** Do not edit `PHASES.md`, anything under `tasks/`, `docs/state/*`, `docs/decisions.md`, or `.claude/`.
- **Commit messages** follow the repo style `<area>: <plain sentence>`.
- **Invariants.** `web` stays a job producer and writes nothing to `document`, `item_document` or `evidence`. There are no new job types and no migrations. The only payload change is the optional `note` on `gate.apply` (Task 1).
- **Test selection** (CLAUDE.md): run `tests/test_<module>*.py` for each module you touch, plus `tests/test_web*.py` for anything under `web/`. Touching `app/config.py`, `tests/conftest.py`, `app/db.py`, `app/queue.py`, `migrations/` or `playbooks/` means running the full suite once before your final commit. Put new tests in a new file `tests/test_web_<slice>.py` where you can. When a changed word breaks an assertion in `tests/test_web.py`, update the assertion.
- **Guide.** For every screen you change, update both `docs/guide/pages/<screen>.md` and its `.sl.md`, in natural Slovenian matching the existing SL pages. Run `python3 scripts/build-guide.py` and commit the regenerated bundles. When a page is new, add it to the nav list in `scripts/build-guide.py`. `tests/test_docs_sets.py` must pass.
- **Dev docs.** Update what your change invalidates, per the "Self-document on change" table in `CLAUDE.md`. For example, a config key goes in `docs/dev/config-reference.md` and a payload field in the PRD and the handbook.
- **UI copy.** Where the spec gives copy, use it verbatim. Elsewhere write short, active sentences from the office person's side. No em dashes in new copy. Use `·` as the inline separator, as the mockups do.
- **Two-step confirms** use the generic confirm from Task 0. The first POST writes nothing. Only `confirm=1` acts.

## Execution waves

| Wave | Tasks | Mode |
|---|---|---|
| 0 | T0 | alone |
| A | T1, T2, T3, T4 | parallel worktrees, then merge, full suite, deploy, dev check |
| B | T5 and T6 in parallel, then T7 | then merge, full suite, deploy, dev check |
| C | T8, then T9 and T10 in parallel, then T12, then T11 (the guide sweep goes last) | then merge, full suite, deploy, dev check |

---

### Task 0: Generic two-step confirm

**Spec:** § 3, the shared part.

**Files:**
- Modify: `web/templates/_result.html`, the `confirm_needed` branch
- Modify: `web/registry.py`, the rename route around line 2204, which passes the new keys
- Test: `tests/test_web_confirm.py` (new)

**Interfaces:**
- Produces a template contract that later tasks rely on. The context keys are:
  - `confirm_needed: list[str]`
  - `confirm_url: str`
  - `confirm_target: str` (an element id, no `#`)
  - `confirm_fields: dict[str, str]`, where every pair becomes `<input type="hidden" name=k value=v>`
  - `confirm_label: str`, the button text

  `confirm=1` is always added. A Cancel button clears `#confirm_target` without a request, for example with `onclick` setting `innerHTML` to empty.

- [ ] Write the tests:
  1. Rendering `_result.html` with `confirm_fields={"a": "1", "b": "x y"}` and `confirm_label="Yes, do it"` produces both hidden inputs, the hidden `confirm=1`, and a button reading "Yes, do it".
  2. A Cancel control is present.
  3. The rename flow's existing tests stay green, still showing "Yes, rename it" and the same hidden values.
- [ ] Run them and see the new ones fail.
- [ ] Implement. Rename passes `confirm_fields={"new_name": …, "note": …}` and `confirm_label="Yes, rename it"`.
- [ ] Run `./scripts/test.sh tests/test_web_confirm.py tests/test_web.py`. Commit.

---

### Task 1: P1a Review safety

**Spec:** § 3, P1a.

**Files:**
- `web/templates/_staging_docs.html`, `_staging_decide.html`, `_staging_doc_detail.html`
- `web/app.py`: `staging_apply` (line 3976 onward) and the audit page query and template (`/audit`, `audit.html`)
- `app/handlers/gate.py`: the `gate.apply` handler, for `note` in `audit_log.detail`
- `docs/dentalia-pipeline-contract-prd-v3.md`: the gate.apply payload row
- `docs/dentalia-job-type-handbook.md`
- `docs/dev/handlers.md` (if it lists payload fields)
- `docs/guide/pages/review.md` and `.sl.md`, `docs/guide/pages/decisions.md` and `.sl.md`
- Tests: `tests/test_web_review_safety.py` (new), plus a gate handler test in the existing `tests/test_gate*.py` file that covers `gate.apply`

**Interfaces:**
- Consumes: the Task 0 confirm contract.
- Produces:
  - The `gate.apply` payload gains the optional `note: str`.
  - `staging_apply` accepts the new form fields `reason`, `reason_note` and `confirm`.
  - The reason set is a module-level tuple `REJECT_REASONS` in `web/app.py`, holding the six strings from § 3 in order.

- [ ] Write the tests:
  1. The collapsed list (`GET /staging`, and `/staging/docs` if that is the list partial) holds no approve, reject or bind-manufacturer form or button for a seeded staged doc, and does hold "Open".
  2. `GET /staging/{id}/detail` does hold approve and reject.
  3. `POST /staging/{id}/apply` with `decision=bind-manufacturer` and no `confirm` returns the confirm. It contains "Approve for all N" with N equal to the value `bind_items` shows, and "Yes, approve for N items". No `job` row exists with dedupe key `apply:{id}:bind-manufacturer`.
  4. The same POST with `confirm=1` enqueues exactly one such job.
  5. `decision=reject` without `reason` returns 422 and enqueues nothing.
  6. `decision=reject`, `reason="Out of date"`, `reason_note="newer 2025 version exists"` enqueues a payload whose `note` is `"Out of date: newer 2025 version exists"`. With an empty note, `note == "Out of date"`.
  7. A reason not in `REJECT_REASONS` returns 422.
  8. Handler: running `gate.apply` with `note` puts `detail->>'note' == note` on the audit row. Without `note` there is no `note` key and no error.
  9. `/audit` shows the note text for a decision that has one.
- [ ] Run them and see them fail.
- [ ] Implement:
  - Move the decision controls into the detail panel only.
  - Build the reject reason UI in the open panel: six radio buttons, a note input, the recorded-line copy, and Reject and Cancel.
  - Make bind-manufacturer two-step through Task 0's confirm. The confirm copy is in § 3.
  - Before adding "on their Business Central item card", verify in `web/item_link.py` / `web/item_docs.py` that a manufacturer-bound declaration appears on the BC item card. If it does not, leave the phrase out and say so in your report.
- [ ] Update the PRD row, the handbook and the guide pages (EN and SL), then rebuild the bundles.
- [ ] Run `./scripts/test.sh tests/test_web_review_safety.py tests/test_web.py tests/test_gate*.py`. Commit.

---

### Task 2: P6 Figures that agree, and the weekly report

**Spec:** § 8 and D4, D7.

**Files:**
- `app/handlers/report.py`: the row template at lines 197-207 and the query that feeds it
- `web/app.py`: `status_board` (line 3307 onward), `_pulse_counts` (line 1743 onward), the Expiry page query and default, and the Queues & health tile source
- `web/templates/status.html`: line 20 `in_flight`, and the coverage headline
- `web/templates/expiry.html`, `web/templates/_pulse.html`, `web/templates/pipeline.html`
- Guide: `status`, `expiry`, `reports` and `pipeline` pages, EN and SL
- Tests: `tests/test_web_figures.py` (new), and `tests/test_report*.py` if it exists, otherwise a new `tests/test_report_rows.py`

**Interfaces (produced):**
- In `web/app.py` at module level:
  - `coverage_headline(conn) -> dict` with keys `covered: int`, `total: int`, `pct: float` (0-100, one decimal) and `unclassified: int` (items with no device class).
  - `EXPIRING_WINDOW_DAYS = 30`.

  Task 7 imports both.

- [ ] Write the tests:
  1. Weekly report. Seed a production document with a manufacturer and `validity_to` inside the report window. The rendered row has a non-empty Manufacturer cell, the date in the Expires cell, and `href="/documents/{id}"`.
  2. In-flight tile. Seed 10 `scheduler.tick` pending jobs and one `extract.doc` running job for one content hash. The status board's "Documents being read" figure is 1, not 11.
  3. Coverage headline. Seed md items, some with a production declaration link and some without. `covered + gaps == total`, where gaps is the count Coverage gaps reports as having no declaration. The status board shows "Declarations on file for {covered} of {total} medical-device items ({pct}%)".
  4. One window. Seed one production document expiring in 20 days and one in 60 days. The pulse or menu count, the Expiry page's default view and the weekly report each count 1, and each rendered label names "30 days".
  5. The Queues & health tile's label names what it counts. Read the source first and assert its real meaning.
- [ ] Run them and see them fail.
- [ ] Implement:
  - Replace the 99.7% headline with `coverage_headline`, reusing Coverage gaps' own definition of "has a declaration" so the numbers agree.
  - Unify the three expiring cuts on `EXPIRING_WINDOW_DAYS`.
  - Word the report's machine line plainly.
- [ ] Update the guide pages (EN and SL), then rebuild the bundles.
- [ ] Run `./scripts/test.sh tests/test_web_figures.py tests/test_web.py tests/test_report*.py`. Commit.

---

### Task 3: P1b Other one-click writes

**Spec:** § 3, P1b.

**Files:**
- `web/bc_push_view.py`, `web/templates/bc_push.html`
- `web/registry.py`: the claim route around line 2331
- `web/templates/playbook_detail.html`: lines 55-70
- `web/templates/_ui.html`, `onboarding_who.html`, `item_detail.html`, `srn_queue.html`, `sweep_due.html`, `eudamed.html`, `manual.html`: wherever these buttons live (find each by its text): SRN Confirm, SRN Reject, Release, Take out of the queue, Update Business Central, Re-discover
- Guide: `bc-push`, `playbooks`, `eudamed`, `items` and `manufacturers` pages as they change, EN and SL
- Tests: `tests/test_web_write_safety.py` (new)

**Interfaces:**
- Consumes: the Task 0 confirm contract. `bc.write_enabled` is at `app/config.py:473`.

- [ ] Write the tests:
  1. With `bc.write_enabled` false, `/bc-push` (verify the route) shows "Sending to Business Central is switched off." With it true, the page shows "switched on".
  2. More than 1,000 differences puts "Showing the first 1,000 of N differences." near the top. Place it above the table.
  3. The first POST of BC push apply returns a confirm naming the count and enqueues no `bc.push`. With `confirm=1`, it enqueues as today.
  4. The claim form's `<select name="code">` has an empty first option "Choose a code…" and `required`.
  5. The first claim POST returns a confirm naming the code, its current owner and the target ("Move 10044 from FUTURA DENTAL to GC?", using the seeded names) and changes nothing. With `confirm=1`, it moves the code as today.
  6. Each of the six buttons renders with a non-empty `hx-confirm`.
- [ ] Run them and see them fail.
- [ ] Implement, then update the guide pages and rebuild the bundles.
- [ ] Run `./scripts/test.sh tests/test_web_write_safety.py tests/test_web.py tests/test_registry*.py tests/test_bc_push*.py`. Commit.

---

### Task 4: P2 Failed split, and operator identity

**Spec:** § 4 (Failed split), § 2 (operator identity), D6.

**Files:**
- Create: `web/failures.py`, holding the classifier and the split query as pure functions
- Modify: `web/app.py`: `dead_board` (line 4167 onward) and two new POST routes. `_dead_job_groups` (line 1479 onward) stays for the developer section.
- Modify: `web/templates/dead.html`
- Modify: `web/access.py`: add `is_operator`
- Modify: `app/config.py`: `Web.operator_users`, env `WEB_OPERATOR_USERS`
- Modify: `docs/dev/config-reference.md`
- Guide: `failed` and `01-daily-work` pages, EN and SL
- Tests: `tests/test_web_failed_split.py` (new) and `tests/test_config*.py` (add a case)

**Interfaces (produced):**
- In `web/failures.py`:
  - `classify_failure(job_type: str, last_error: str | None) -> str`, returning `"timeout"`, `"dead-address"` or `"developer"`.
  - `failed_split(conn) -> dict`, with a section per class carrying `n` and `domains`. The dead-address section also carries a `kinds` list and `group_ids`. Retried jobs are excluded, per § 4.
  - `failed_counts(conn) -> dict[str, int]`, keyed by the three classes. Task 7 uses it.
- In `web/access.py`: `is_operator(user: str | None, operator_users: tuple[str, ...]) -> bool`. It returns True when `operator_users` is empty, and otherwise `user in operator_users`.
- In `app/config.py`: `cfg.web.operator_users: tuple[str, ...] = ()`, parsed from a comma-separated env with whitespace stripped and empty entries dropped.
- Routes:
  - `POST /dead/retry-timeouts` re-enqueues every timeout job still counted, at interactive priority, and answers with a receipt in words.
  - `POST /dead/search-again` enqueues one `discover.group` per distinct `group_id` in the dead-address section. Its dedupe key is `discover:failed:{group_id}`, at interactive priority, with a receipt in words.

  Task 7's Today buttons post to both.

- [ ] Read-only first: list today's dead jobs by type and error class on the dev database. Also read `app/handlers/fetch.py` and `discover.py` to see how errors are formatted. Record the patterns in your report.
- [ ] Write the tests:
  1. A table-driven `classify_failure` test with at least 12 rows drawn from the real patterns: ConnectTimeout and ReadTimeout on `fetch.url` → timeout; 404, 410 and a DNS failure on `fetch.url` → dead-address; a 404 on a non-`fetch.url` type → developer; an extract traceback → developer; an empty error → developer.
  2. `/dead` renders the three section headings with their counts, in order.
  3. With `operator_users=("denis",)` and `X-Forwarded-User: office1` (use the existing header fixture pattern), the developer section has no re-run button. With `denis` it has one.
  4. `POST /dead/retry-timeouts` enqueues only the timeout jobs. Afterwards `failed_counts()["timeout"] == 0`, and the dead rows still exist.
  5. `POST /dead/search-again` enqueues one `discover.group` per distinct `group_id`. A second POST enqueues nothing new, because of the dedupe. Afterwards `failed_counts()["dead-address"] == 0`.
  6. `is_operator` truth table. Config parsing: `WEB_OPERATOR_USERS=" a, b ,"` gives `("a", "b")`.
- [ ] Run them and see them fail.
- [ ] Implement. The copy is in § 4 and the mockup wording. Keep "Show technical details" for the per-cause developer groups.
- [ ] Update the config reference and the guide pages, then rebuild the bundles.
- [ ] Run the full suite once, because `app/config.py` changed: `./scripts/test.sh`. Commit.

---

### Task 5: P3 Missing documents

**Spec:** § 5.

**Files:**
- Create: `web/templates/missing.html`
- Modify: `web/app.py`: the new `GET /missing`, next to `manual_board` (line 4121 onward); reuse `_manual_tasks` and `_describe_manual_docs` where they fit
- Modify: `web/templates/upload.html` and `upload_form` (line 3455 onward)
- Create: guide pages `docs/guide/pages/missing.md` and `.sl.md`, and add them to the nav in `scripts/build-guide.py`
- Modify: guide pages `upload` and `01-daily-work`, EN and SL
- Tests: `tests/test_web_missing.py` (new)

**Interfaces (produced):**
- In `web/app.py`, `missing_summary(conn) -> dict` with keys:
  - `n: int`: open `discovery-dead-end` tasks.
  - `top: list[tuple[str, int]]`: the top 3 manufacturers by task count, name first.
  - `oldest: date | None`.

  Task 7 uses it.
- Route `GET /missing?manufacturer=&page=`.

- [ ] Read-only first: look at 3 real `discovery-dead-end` payloads on the dev database to confirm the keys `label`, `manufacturer`, `sources_tried` and `prefilled_search_links`.
- [ ] Write the tests:
  1. Only open `discovery-dead-end` tasks are listed, oldest first. Seeded `gate-manual`, `dead-job-followup` and resolved tasks are absent.
  2. With 25 tasks, page 1 shows 20 and page 2 shows 5.
  3. A card with one item shows "item X". With three items it shows the three refs. With five it shows "5 items: a, b, c, +2 more".
  4. The sentence "Searched … Nothing found." uses plain words for each `sources_tried` value. Unknown values pass through.
  5. `?manufacturer=X` filters.
  6. The chips show the top six plus "+ K more".
  7. "Upload what I found" links to `/upload?group_id=…&manual_task_id=…`.
  8. The external links render only when present.
  9. "Search again" posts to `/manual/{id}/resolve`.
  10. `GET /upload?group_id=…&manual_task_id=…` names the product, manufacturer and item, and has no visible "Target group id" or "Priority" field. The hidden inputs carry the ids. `GET /upload` with no parameters still renders a working form.
  11. `missing_summary` values.
- [ ] Run them and see them fail.
- [ ] Implement, write the guide, and rebuild the bundles.
- [ ] Run `./scripts/test.sh tests/test_web_missing.py tests/test_web.py tests/test_upload*.py`. Commit.

---

### Task 6: P4 Review with the document and its items in view

**Spec:** § 6.

**Files:**
- `web/templates/_staging_docs.html`, `_staging_doc_detail.html`, `_staging_decide.html`, `staging.html`
- `web/app.py`: the staging list query and route (line 3843 onward), and `document_file` (line 2012 onward) for the Cache-Control header
- `web/static/css/style.css`: the two-column panel
- Guide: `review` page, EN and SL
- Tests: `tests/test_web_review_context.py` (new)

**Interfaces:**
- Consumes: Task 1's panel-only decision controls, and the reject flow and `REJECT_REASONS`, already merged.
- Produces: nothing new for later tasks.

- [ ] Measure `/staging` render time on dev before changing anything. Run 5 `curl` requests against `http://localhost:8000/staging` with `-u admin:dentalia` and take the median. This measures the currently deployed code; note that in your report.
- [ ] Write the tests:
  1. The list is grouped by manufacturer, with a header "{name} {n} documents". Documents with no manufacturer come last under "Manufacturer not known". Order is oldest first.
  2. The reason chips filter on the server. Every existing reason code maps to a chip, and an unknown code lands in Other.
  3. The row title is "{MFR} · {type word} ({regulation})", and the subline holds the file name from `source_url`.
  4. The detail panel has `<iframe` with `src="/documents/{id}/file…"` and "Open full size". It lists each staged link's item number and name from `item_mirror`. Twelve links show 10 plus "+ 2 more".
  5. The Approve button reads "Approve for these 3 items", and with one link "Approve for this item".
  6. A whole-range document shows "…for all N {MFR} items".
  7. `GET /documents/{id}/file` answers 200 with `Cache-Control` containing `immutable` and `private`. A 404 carries no such header.
  8. The list HTML has no `<iframe`.
- [ ] Run them and see them fail.
- [ ] Implement, then update the guide and rebuild the bundles.
- [ ] Run `./scripts/test.sh tests/test_web_review_context.py tests/test_web_review_safety.py tests/test_web.py`. Commit.
- [ ] In the report, give the local render time of the new list. Use the test client with the dev-sized data if possible; otherwise say what was measured.

---

### Task 7: Today page and the office menu

**Spec:** § 4 (Today) and § 7 (office menu).

**Files:**
- Create: `web/templates/today.html`
- Modify: `web/app.py`:
  - `GET /` renders Today.
  - The KPI board moves to `GET /status` (the `status_board` function, line 3307 onward).
  - The `_pulse` endpoint and template render the menu counts and the health line.
- Modify: `web/templates/base.html` (menu, logo link), `_pulse.html`, `items.html` (link row), `eudamed.html` (links to SRN queue and Sweep due), `playbooks.html` (link to onboarding)
- Guide: `00-getting-started`, `01-daily-work`, `status` and `pipeline` pages, EN and SL; the build-guide nav if names change
- Tests: `tests/test_web_today.py` (new), and update the tests that fetch `/` expecting the KPI board

**Interfaces:**
- Consumes:
  - `coverage_headline` and `EXPIRING_WINDOW_DAYS` (Task 2).
  - `failed_counts` and the POST routes `/dead/retry-timeouts` and `/dead/search-again` (Task 4).
  - `missing_summary` (Task 5).
  - `is_operator` (Task 4). It is used only for what the menu shows; everyone is an operator until Task 12.

- [ ] Write the tests:
  1. `GET /` has "Today" and the four list headings with live counts from seeded data.
  2. `GET /status` renders the KPI board, and every link to the old status board points at `/status`.
  3. The menu entries match the § 7 table exactly, in order, with the operator group inside a `<details>`.
  4. `/coverage`, `/discovery`, SRN queue, Sweep due, onboarding and `/reports` are each reachable from the page named in § 7.
  5. A failed line whose count is zero is not rendered.
  6. The coverage headline sentence and its definition line render.
  7. The sidebar shows "System working" and "N websites timed out" when N > 0.
  8. The top pulse strip is gone, and the menu counts come from the pulse endpoint.
- [ ] Run them and see them fail.
- [ ] Implement, then update the guide and rebuild the bundles.
- [ ] Run `./scripts/test.sh tests/test_web_today.py tests/test_web*.py`. Commit.

---

### Task 8: P7a Foundations (words, display names, receipts, error pages, buttons)

**Spec:** § 9 (the word list, and P7a).

**Files:**
- Create: `web/words.py`
- Modify: `web/app.py`: register the filter (next to the `duration` filter at line 1946), the exception handlers, and receipts at every `_result.html` caller
- Modify: `web/templates/_result.html`, `base.html`, and a new `error.html`
- Modify: `web/static/css/style.css`
- Guide: `00-getting-started` for the receipts and error pages, EN and SL
- Tests: `tests/test_web_words.py` (new)

**Interfaces (produced):**
- `web/words.py`:
  - `WORDS: dict[str, str]`.
  - `word(value: str | None) -> str`, which passes unknown values through and turns None into an empty string.
  - `doc_display_name(row: Mapping) -> str`.
  - `week_label(d: date) -> str`, which returns "Week 37 (7–13 Sep)" for 2026-09-10 (ISO week, Monday to Sunday, en dash, abbreviated month). When a week spans two months it reads "Week 36 (31 Aug–6 Sep)". Tasks 9 and 10 use it.
- The Jinja filters `word` and `week_label`.
- `_result.html` takes `receipt: str`, the sentence. `job_id` and `dedupe_key` stay available and render only under a collapsed "Technical details".

- [ ] Write the tests:
  1. `word()` for every row of the § 9 table, plus an unknown value passing through.
  2. `doc_display_name`: the full row, then no manufacturer (falls back to the file name), then nothing at all ("Document #id").
  3. The receipts for approve, reject, re-run and search again are sentences with no "Enqueued job #". A deduped enqueue reads "Already in progress."
  4. `GET /no-such-page` with `Accept: text/html` returns a 404 HTML page with the menu and a link to Today. `GET /api/no-such` returns JSON. An HTMX request gets the error partial.
  5. The primary button colour in `style.css` is `#2F7A58`.
  6. `week_label`: 2026-09-10 gives "Week 37 (7–13 Sep)", and 2026-09-01 gives "Week 36 (31 Aug–6 Sep)".
- [ ] Run them and see them fail.
- [ ] Implement, then update the guide and rebuild the bundles.
- [ ] Run `./scripts/test.sh tests/test_web_words.py tests/test_web*.py`. Commit.

---

### Task 9: P7b Registry pages vocabulary

**Spec:** § 9, P7b.

**Files:** templates `items.html`, `item_detail.html`, `item_card.html`, `documents.html`, `document_detail.html`, `manufacturers.html`, `manufacturer_detail.html`, `eudamed.html`, `srn_queue.html`, `sweep_due.html`, `expiry.html`, `audit.html`, `search.html`; their routes in `web/app.py`, `web/registry.py` and `web/item_docs.py` where a label is built in Python; guide pages for each screen, EN and SL. Tests: `tests/test_web_vocab_registry.py` (new), plus updated assertions in `tests/test_web.py`.

**Interfaces:**
- Consumes: the `word` filter and `doc_display_name` (Task 8).

- [ ] Write the tests. For each page, seed data and assert:
  1. Status pills show the § 9 words: "Published", "Waiting for review", "On file", "Replaced".
  2. There is a one-sentence intro.
  3. No timestamp matches `\d{2}:\d{2}:\d{2}\.\d+`.
  4. "Catalogue LJ", model ids (for example `claude-`) and source keys appear only inside "Technical details".
  5. Wherever a page labels a week, it uses `week_label` (Task 8).
- [ ] Run them and see them fail.
- [ ] Implement, then update the guide and rebuild the bundles.
- [ ] Run `./scripts/test.sh tests/test_web_vocab_registry.py tests/test_web*.py`. Commit.

---

### Task 10: P7c Correspondence and add pages vocabulary

**Spec:** § 9, P7c.

**Files:** templates `emails.html`, `drafts.html`, `draft_detail.html`, `upload.html`, `import.html`, `_import_preview.html`, `import_preview_page.html`, `onboarding*.html`, `_onboarding_*.html`, `reports.html`; their routes; guide pages `emails-in`, `drafts-out`, `upload`, `import`, `onboarding` and `reports`, EN and SL. Page titles follow the word list ("Emails received", "Renewal emails"). Tests: `tests/test_web_vocab_add.py` (new), plus updated assertions in `tests/test_web.py`.

**Interfaces:**
- Consumes: Task 8's `word` and `week_label` filters, and `doc_display_name`.

- [ ] Write the tests with the same assertions as Task 9, for these pages.
- [ ] Run them and see them fail.
- [ ] Implement, then update the guide and rebuild the bundles.
- [ ] Run the tests. Commit.

---

### Task 11: P7d Guide sweep and glossary

**Spec:** § 9, P7d.

**Files:** `docs/guide/glossary.md` and `glossary.sl.md`, every `docs/guide/pages/*.md` and `.sl.md` still using an old word, `docs/guide/00-getting-started*.md` and `01-daily-work*.md`, and the rebuilt bundles. Test: add a case to `tests/test_docs_sets.py` asserting that each word-list word in § 9's "Word" column (except "(not shown)" and "(removed)") appears in `glossary.md`.

- [ ] Write the glossary test and see it fail.
- [ ] Update the glossary and sweep the pages in EN and SL, then rebuild the bundles.
- [ ] Run `./scripts/test.sh tests/test_docs_sets.py`. Commit.

---

### Task 12: P5b Named logins and operator permissions

**Spec:** § 7 (P5b) and D2, D3.

**Files:**
- `Caddyfile`
- `docker-compose.yml`: the caddy service's users-file mount and `WEB_OPERATOR_USERS` passed to `web`
- `.env.example` (if present)
- `web/access.py`: the operator guard
- `web/app.py`, `web/registry.py`, `web/bc_push_view.py`, `web/scheduler_view.py`: the guarded routes
- `web/templates/base.html`: hide the operator group
- `docs/runbook.md`, `docs/dev/config-reference.md`
- Guide: `00-getting-started`, EN and SL
- Tests: `tests/test_web_operators.py` (new)

**Interfaces:**
- Consumes: `is_operator` and `cfg.web.operator_users` (Task 4).
- Produces: a FastAPI dependency `require_operator(request)` in `web/access.py` (or next to `_authenticated_user`). It raises 403. The HTML error page from Task 8 renders it with "This action is for operators."

- [ ] Verify against Caddy's documentation (WebFetch) how `basic_auth` takes several accounts and whether an `import`ed file works inside the block. Validate the new Caddyfile with the project's own caddy image: `docker run --rm -v <worktree>/Caddyfile:/etc/caddy/Caddyfile:ro -v <users file>:<mount>:ro <caddy image from docker-compose.yml> caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile`. This `docker run --rm` is the only docker command this task may run. Record the output.
- [ ] Write the tests:
  1. With `operator_users=("denis",)` and `X-Forwarded-User: office1`, each of the four D3 writes answers 403 with the HTML page. The four are the playbook edit and code claim POST routes, the BC push apply, the scheduler run and arm, and the `/ingest` POST.
  2. As `denis`, those same writes behave as before.
  3. `/import` apply stays allowed for `office1`.
  4. The menu hides the operator group for `office1` and shows it for `denis`.
  5. With `operator_users=()`, everything behaves as before for any user.
- [ ] Run them and see them fail.
- [ ] Implement. Update the runbook (adding a person with `caddy hash-password`, making them an operator), the config reference and the guide, then rebuild the bundles.
- [ ] Run `./scripts/test.sh tests/test_web_operators.py tests/test_web*.py`. Commit.
