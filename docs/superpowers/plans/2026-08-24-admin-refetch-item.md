# Admin Refetch-Item Implementation Plan

**Goal:** An admin who says "refetch documents for item 12345" gets a button on the item page and a CLI verb, both of which enqueue that item's group for discovery with the recency window bypassed — instead of today's SQL lookup + hand-built enqueue that the ledger then silently skips.

**Architecture:** One additive payload flag, `ignore_recency: true`, on `discover.group` and `fetch.url` — it bypasses ONLY the recency-window checks (DISCOVER's fresh/stale tagging and FETCH's `_fresh` skip); the conditional GET and the content-hash dedupe still run, so forcing when nothing changed costs one HTTP request and zero re-processing, and FETCH's existing `recency-skip-linked` C3 branch (spec §3.2) keeps linking behavior intact. Producers: a `POST /items/{item_ref}/rediscover` button (the web UI is already a contracted `discover.group` producer — PRD §0's dedupe table lists `discover:manual:{task_id}` for the manual-tab re-run) and a `discover-item` CLI verb; both resolve `item_ref → group_id` server-side. Ruled by Denis 2026-08-24 (`[admin-refetch-item]` followup; the payload-row PRD edit was signed off in the same exchange).

**Tech Stack:** Python 3.12, psycopg 3 raw SQL, FastAPI + Jinja, pytest against real Postgres. No migration — no schema change.

**Verified against:** master on 2026-08-24. Anchors: DISCOVER recency — `_known_url_rows` tags fresh/stale against `cfg.discovery.recency_days` (`app/handlers/discover.py:90-107`, consumed at `:347`, rung branch `if source == "recency"` at `:355`); FETCH recency skip — `_fresh` at `app/handlers/fetch.py:52`, the skip-plus-C3 branch at `:137-143` (`recency-skip-linked`); PRD payload rows — §3 `discover.group` consumes `{group_id, manufacturer_id, context}` (`docs/dentalia-pipeline-contract-prd-v3.md:116`), §4 `fetch.url` consumes `{url, domain, group_id, source_rank}` (`:130`); PRD §0 dedupe table rows for `discover.group` (`:62-64`); item page route `web/app.py:1789`; CLI enqueue pattern `app/cli.py:29-47`, parser registration around `:588`.

## Global Constraints

- Payload fields are ADDITIVE-ONLY: both handlers must treat a missing `ignore_recency` exactly as today (default false). No consumer may require it.
- The flag bypasses recency ONLY. Conditional GET (ETag), content-hash dedupe, politeness/domain lease, and the C3 link-on-dedupe branch are untouched — a test pins that a forced re-fetch of identical bytes still ends in `recency-skip-linked`-equivalent linking, not a duplicate document.
- Job types are a closed enum — nothing here adds one. The CLI verb and the button are producers of the existing `discover.group`.
- Dedupe key for both new producers: `discover:refetch:{group_id}:{YYYY-MM-DD}` (day bucket — button spam within a day dedupes; the date comes from Postgres `current_date`, not Python, matching how period keys are built elsewhere).
- Web writes stay producer-only: the route enqueues a job and touches nothing else. `dentalia_api` already holds the needed job INSERT (it enqueues `gate.apply` and the manual-tab `discover.group` today).
- Each task ≤3 files, tests excluded. Isolated test env, once per shell:
  ```bash
  export WT='COMPOSE_PROJECT_NAME=dentalia_wte POSTGRES_PORT=5438 PGDATA_HOST=/srv/pgdata/dentalia_wte'
  ```
  plus the out-of-tree container_name override (tracked compose hardcodes `dentalia-postgres`): create `/tmp/compose.wte.yml` with `services: { postgres: { container_name: dentalia-wte-postgres } }` and add `-f docker-compose.yml -f /tmp/compose.wte.yml` to every compose test command.

---

### Task 1: Contract first — PRD + handbook payload rows

**Files:**
- Modify: `docs/dentalia-pipeline-contract-prd-v3.md` (§3 :116, §4 :130, §0 dedupe table :62-64)
- Modify: `docs/dentalia-job-type-handbook.md` (the matching §3/§4 payload lines)

- [x] **Step 1:** §3 Consumes row becomes `{group_id, manufacturer_id, context, ignore_recency?}` with the note: *"`ignore_recency` (optional, default false, additive 2026-08-24): bypass the recency-window checks only — conditional GET and hash dedupe still apply; set by the admin refetch producers, propagated onto every `fetch.url` this run emits."* §4 row gains `ignore_recency?` with one clause pointing back to §3's note.
- [x] **Step 2:** §0 dedupe table: add the producer row `discover.group | web UI (/items re-discover) + CLI discover-item | discover:refetch:{group_id}:{date}`.
- [x] **Step 3:** Mirror both payload lines in the handbook's §3/§4 rows.
- [x] **Step 4: Commit** — `git commit -m "contract: an admin may bypass the recency window, additively"`

---

### Task 2: The flag through DISCOVER and FETCH

**Files:**
- Modify: `app/handlers/discover.py`
- Modify: `app/handlers/fetch.py`
- Test: `tests/test_discover_handler.py`, `tests/test_fetch_handler.py`

**Interfaces:**
- Produces: `discover.group` payload `ignore_recency: true` → recency rung yields no fresh-skips (all known URLs treated stale) and every emitted `fetch.url` payload carries `ignore_recency: true`; `fetch.url` with the flag skips the `_fresh` short-circuit (`fetch.py:137-143` branch condition gains `and not payload flag`) and proceeds to the conditional GET.

- [x] **Step 1: Write the failing tests** (mirror each file's existing arrangement style):

  ```python
  # discover: a ledger-fresh known URL is normally a recency skip; with the flag
  # it must be emitted as fetch.url, and that child payload must carry the flag.
  def test_ignore_recency_turns_a_fresh_url_into_a_fetch(...):
      ...
      assert emitted_fetch_payload["ignore_recency"] is True

  # discover: missing flag == today, byte-for-byte (additive-only pin)
  def test_missing_flag_behaves_exactly_as_before(...)

  # fetch: flag skips _fresh but the conditional GET/hash path still dedupes —
  # identical bytes re-fetched under force end linked, never duplicated.
  def test_forced_refetch_of_identical_bytes_links_and_does_not_duplicate(...)
  ```

- [x] **Step 2: Run to verify failure.**
- [x] **Step 3: Implement.** `discover.py`: read `p.get("ignore_recency", False)` once; where `_known_url_rows`' fresh/stale tagging feeds the recency rung (`:347`, `:355`), treat every row as stale under the flag; add the key to every `fetch.url` payload the handler builds. `fetch.py`: the `:137-143` skip condition gains `and not payload.get("ignore_recency")`. Nothing else — `_fresh` itself, ledger writes, C3 emission all unchanged.
- [x] **Step 4: Run both handler suites** — PASS, zero pre-existing test edits.
- [x] **Step 5: Commit** — `git commit -m "discover, fetch: ignore_recency bypasses the window, never the dedupe"`

---

### Task 3: CLI verb `discover-item`

**Files:**
- Modify: `app/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `python -m app.cli discover-item 12345 [--force]` — resolves `item_ref → group_id` via `item_group_member`, enqueues `discover.group` `{group_id, ignore_recency: <force>}` at `interactive`, dedupe `discover:refetch:{group_id}:{current_date}`. Unknown or ungrouped item → clear message, exit 2, nothing enqueued.

- [x] **Step 1: Failing tests:** known item enqueues with the right payload/dedupe/priority; `--force` sets the flag; unknown item exits 2 with a message naming the item_ref; same-day second call reports the dedupe (reuse `cmd_enqueue`'s dedupe-message behavior at `app/cli.py:46`).
- [x] **Step 2: Verify failure.**
- [x] **Step 3: Implement** `cmd_discover_item` following `cmd_enqueue`'s shape (`:29-47`); parser block beside `enqueue`'s (`:588`), args: `item_ref`, `--force` (default: the flag is still sent as false — the CLI is also the polite path).
- [x] **Step 4: Run** `tests/test_cli.py` — PASS.
- [x] **Step 5: Commit** — `git commit -m "cli: discover-item finds the group so the admin does not have to"`

---

### Task 4: The button

**Files:**
- Modify: `web/app.py` (route beside `item_detail`, `web/app.py:1789`)
- Modify: `web/templates/item_detail.html`
- Test: `tests/test_web.py`

**Interfaces:**
- Produces: `POST /items/{item_ref:path}/rediscover` — resolves the group, enqueues `discover.group` `{group_id, ignore_recency: true}` at `interactive`, dedupe `discover:refetch:{group_id}:{current_date}`, redirects back to the item page; the page shows a "Re-discover documents" button and, after redirect, the standard flash/notice the templates already use (find the existing flash idiom in `/staging` or `/manual` POSTs and reuse it — a deduped same-day click must say "already queued today", not pretend to enqueue).

- [x] **Step 1: Failing tests:** POST enqueues the job with exact payload/dedupe/priority and redirects 303 to the item page; an item with no group returns the page with a visible "no group" notice and enqueues nothing; second same-day POST enqueues nothing and says so; GET of the item page contains the button markup.
- [x] **Step 2: Verify failure.**
- [x] **Step 3: Implement** — route uses the `{item_ref:path}` converter (slash-bearing item_refs, same as `item_detail`); one `item_group_member` lookup; `queue.enqueue(...)` exactly as the manual-tab re-run does it (grep `discover:manual:` in `web/app.py` and mirror that call's shape and grants).
- [x] **Step 4: Run** the item-page and rediscover tests, then the full `tests/test_web.py` once.
- [x] **Step 5: Docs sync** — `docs/code-map.md` web row gains the route only if that row enumerates POST routes (grep first); `docs/runbook.md`: add the one-line "refetch an item" recipe (button, or `discover-item --force`) to whichever section documents operator re-runs (grep "re-run" / "manual"). *(Deviation: `docs/code-map.md`'s web/app.py row does not literally enumerate POST routes — grepped, no "POST" token anywhere in that row — so left untouched per the step's own condition; only `docs/runbook.md` was edited.)*
- [x] **Step 6: Commit** — `git commit -m "web: the item page can ask discovery to look again"`

---

## Self-review notes

- Deliberately out: bulk refetch (manufacturer-wide button), scheduler use of the flag, any ledger mutation. The flag never deletes or rewrites `fetch_log` — bypass, not amnesia.
- Interacts with `[discover-recency-rung-skips-c3-linking]`: NOT a dependency — under force, DISCOVER emits real `fetch.url` jobs, and FETCH's own dedupe branch already carries the C3 link, so the forced path links correctly even before that defect is fixed.
- Type consistency: `ignore_recency` (Tasks 1-4), `discover:refetch:{group_id}:{date}` (Tasks 1, 3, 4), `discover-item` / `--force` (Task 3), `POST /items/{item_ref}/rediscover` (Task 4).
