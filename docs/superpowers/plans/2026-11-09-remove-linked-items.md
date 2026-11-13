# Remove Linked Items Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A person can take a wrong item off a document, on Review and after publishing, and reverse a refusal in the picker.

**Architecture:** A removed link becomes `item_document.status='rejected'`. `gate.apply approve` gains `remove` and, with `add-items`, `reopen`; a new `remove-link` link decision covers published documents. The web stays a producer.

**Tech Stack:** Python 3.12, psycopg 3, FastAPI, Jinja, HTMX, plain JS, pytest through `./scripts/test.sh`.

**Spec:** `docs/superpowers/specs/2026-11-09-remove-linked-items-design.md`. Executors read it; mockup artifact `AZ8mZcg4GBhcnoUt7aeCPQ` v4 is the layout reference.

## Global Constraints

- Work in worktree `.claude/worktrees/remove-linked-items`, branch `remove-linked-items`, from `main`.
- **No commits until Denis says so.** Neither this plan nor the spec is committed unasked.
- No migration. No new job type. Payload fields additive only.
- Only GATE writes `item_document` (inv. 1). Web routes enqueue.
- Every failure of a person's decision raises before any write. Nothing silent.
- `decided_by` comes from `_authenticated_user(request) or DEFAULT_DECIDED_BY`.
- Tests only through `./scripts/test.sh`. Bring the container up first: `docker compose --profile test up -d test`.

## Review Focus

1. Item numbers with `/` and spaces (`AB/1 2`, `314 368 023`) through hidden inputs, the summary URL and the two-step confirm. Expect them to work. Test in Tasks 4 and 6.
2. Every system item removed and nothing ticked. Expect "Nothing links it to an item yet" wording and a plain "Approve" label, and Approve still works. Test in Task 4.
3. A stale tab: the link was already removed or reopened when Remove or Yes is pressed. Expect a refusal in words, 422, no job, no dead-letter. Test in Task 6.
4. A stale tab on Review: the document was decided elsewhere when Remove is pressed. Expect the existing "no longer on Review" 404 message. Test in Task 4.
5. Approve re-delivered after a removal and a re-tick. Expect no second audit row. Test in Tasks 1 and 2.

---

### Task 1: `reopen` in `check_items` and GATE

**Files:** Modify `app/item_picks.py`, `app/handlers/gate.py` (`_picked_items`, new `_reopen_links`, `handle_gate_apply`). Test `tests/test_gate_apply_items.py`, `tests/test_item_picks*.py` if present, else the former.

**Interfaces:**
- Produces: `check_items(conn, doc_id, manufacturer, refs, reopen: Collection[str] = ()) -> dict[str, list[str]]`. `_reopen_links(conn, doc_id, refs, decided_by, job, via) -> None`. Payload `reopen: list[str]`, subset of `items`.

- [ ] **Step 1: Write failing tests**

`test_a_refused_item_in_items_alone_still_raises`: rejected link, `_job(items=["A1"])` raises `ValueError` matching `refused`, link stays `rejected`.
`test_reopen_reverses_a_refusal_on_approve`: rejected link basis `ref-list`, `_job(items=["A1"], reopen=["A1"])`; link `production`/`manual`; `_trail` holds `link-reopened`, then `link-confirmed`, `production-write` for A1; `_detail(conn, doc, "link-reopened")["via"] == "approve"`.
`test_reopen_works_on_add_items`: production doc, same result, `via == "add-items"`.
`test_reopen_must_be_a_subset_of_items`: `items=["A1"], reopen=["A2"]` raises before writing.
`test_a_reopened_trusted_link_is_confirmed_not_promoted`: `ref-list` rejected; after approve basis `manual`, one `link-confirmed`.
`test_a_redelivered_reopen_writes_nothing_more`: run the job twice; one `link-reopened`.
`check_items(conn, d, MFR, ["A1"], reopen=["A1"]) == {}` and without `reopen` gives `{"refused": ["A1"]}`.

- [ ] **Step 2: Run, expect FAIL.** `./scripts/test.sh tests/test_gate_apply_items.py`

- [ ] **Step 3: Implement.** `check_items` skips `refused` for refs in `reopen`. `_picked_items` reads `p.get("reopen") or []`, raises `ValueError` if not list-of-str or not subset of `items`, passes it on, and returns it as a third tuple element. `_reopen_links`: `UPDATE ... SET status='staged' WHERE doc_id=%s AND item_ref=ANY(%s) AND status='rejected' RETURNING item_ref, match_basis`; one `link-reopened` audit each with `detail={"match_basis_before", "link_status_before": "rejected", "via"}`. In `approve` call it after `_promote_pending_links`, before `_write_items`; in `add-items` before `_write_items`. Update both `_write_items(*picked, ...)` calls for the new tuple.

- [ ] **Step 4: Run, expect PASS.** Same command, plus `tests/test_gate_apply_handler.py`.

### Task 2: `remove` on Approve

**Files:** Modify `app/handlers/gate.py`. Test `tests/test_gate_apply_items.py`.

**Interfaces:**
- Consumes: Task 1's `_picked_items`.
- Produces: `_removal_targets(conn, doc_id, p, picked_refs) -> list[str]`; `_remove_links(conn, doc_id, refs, decided_by, job) -> dict` returning `{"removed": int, "remove_skipped": int}`. Payload `remove: list[str]` on `approve`.

- [ ] **Step 1: Write failing tests**

`test_approve_remove_refuses_the_link_and_publishes_the_rest`: two `ref-list` staged links, `remove=["A1"]`; A1 `rejected` basis `ref-list`, A2 `production`; `_detail(link-rejected)` has `match_basis_before="ref-list"`, `link_status_before="staged"`, `via="approve"`; `_detail(approve)["removed"] == 1`.
`@pytest.mark.parametrize` per spec § 4.6: start `staged` becomes `rejected` audited; `production` becomes `rejected` audited; `rejected` no audit row; `retracted` unchanged and result `remove_skipped == 1`; no row raises `LookupError`.
`test_the_same_item_in_remove_and_items_raises`; `test_remove_on_a_whole_range_document_raises`; both leave the document staged.
`test_a_redelivered_approve_with_remove_writes_no_second_audit_row`.
`test_a_re_proposal_after_removal_stays_rejected`: `_upsert_link(conn, "A1", doc, None, "ref-list", "staged")` after approve leaves `rejected`.

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Implement.** `_removal_targets` runs with the other pre-write checks only on `approve`; returns sorted unique refs; raises `ValueError` on non-list, a whole-range document, overlap with `items`; `LookupError` on a ref with no row. `_remove_links` runs after `_promote`, before `_promote_pending_links`, updating only `staged`/`production` rows and auditing each `link-rejected`. Add counts to `items_detail` and to the return value.

- [ ] **Step 4: Run, expect PASS.**

### Task 3: `remove-link` decision

**Files:** Modify `app/handlers/gate.py` (`LINK_DECISIONS`, `_apply_link_decision`). Test `tests/test_gate_apply_handler.py`.

**Interfaces:** Produces decision `remove-link` with payload `{doc_id, item_ref, decision, decided_by}`.

- [ ] **Step 1: Write failing tests**

`remove-link` on production link under production doc: `rejected`, basis kept, one `link-rejected` with `link_status_before="production"`. Staged link raises. Staged document raises. `coverage_scope='manufacturer'` raises. Second delivery returns `idempotent: True` with no second audit row. After removal, `reopen-link` then `confirm-link` end `production`/`manual`.

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Implement.** Row `"remove-link": ("production", "rejected", "link-rejected")`. In `_apply_link_decision`, for this decision only, read `coverage_scope` and raise `ValueError` unless `group`, after the document gate.

- [ ] **Step 4: Run, expect PASS.**

### Task 4: Review panel

**Files:** Modify `web/app.py` (`staging_apply`, `picker_summary`), `web/templates/_approve_scope.html`, `web/templates/_staging_doc_detail.html`, `web/static/item_picker.js`, `web/static/css/style.css`. Test `tests/test_web_item_picker.py`.

**Interfaces:**
- Consumes: payload `remove` from Task 2.
- Produces: form field `remove`, `GET /picker/{id}/summary?remove=...`, container `#picker-removed-{doc_id}`, template var `removed_links`.

- [ ] **Step 1: Write failing tests**

`test_approve_with_remove_enqueues_it`: POST approve with `remove=["A1"]`, payload `remove == ["A1"]`, key `apply:{id}:approve`.
`test_a_bad_remove_is_refused_and_nothing_is_enqueued`, parametrized: no such link; same item in `items`; document not on Review; 422 containing "Nothing was changed."
`test_the_summary_moves_a_removed_item_and_recounts`: 2 system links + `remove=A1`: text has "these" or "this item" for 1, "Will not count (1)", "Undo"; Approve label "Approve for this item".
`test_remove_is_offered_only_on_what_approve_publishes`: a `fetch-context` row has no `data-remove`; a `ref-list` row has one; whole-range panel has none.
`test_removing_everything_reads_as_no_items`: Review Focus 2; "Nothing links it to an item yet"; label "Approve".
`test_summary_with_ticks_and_removal`: ticked item tagged "you chose", without `data-remove`.
`test_a_slashed_item_number_survives_the_summary`: item `AB/1 2` URL-encoded.
`test_the_summary_says_when_the_document_left_review`: 404, existing message.

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Implement routes.** `staging_apply` gains `remove: list[str] = Form([])`; when `decision == "approve"` and any, validate against the same `_picker_doc` (`status == "staged"`) and `item_document` rows, set `payload["remove"]`. `picker_summary` gains `remove: list[str] = Query([])`, splits `row["approve_links"]` into kept and `removed_links` (ignoring refs not in it), recomputes `approve_basis`, and excludes removed from `approve_count`.

- [ ] **Step 4: Implement the template.** `item_list` takes a `removable` flag; rows carry `data-remove="{{ ref }}"` on a `type="button"` Remove and a tag from `BASIS_WORDS` (add a `basis_words` per link in the view). Block "Will not count (N)" with `data-undo`, shown in both the `picked` and plain branches. Copy per spec § 5.1. Add `<div id="picker-removed-{{ doc_id }}" hidden></div>` next to `#picker-items-{{ doc_id }}`.

- [ ] **Step 5: Implement the script.** Extract `summarise(docId)` that reads both containers and the manufacturer, and does the existing `htmx.ajax` to `/picker/{id}/summary`. `done()` empties only items, and removes any `remove` input whose ref is ticked. A delegated click on `[data-remove]` adds the input and deletes any `items` input of that ref; `[data-undo]` deletes the input; both call `summarise`. Panel buttons carry `data-doc`.

- [ ] **Step 6: Run, expect PASS.** `./scripts/test.sh tests/test_web_item_picker.py`

### Task 5: Picker re-tick

**Files:** Modify `web/templates/_item_picker_rows.html`, `web/templates/_item_picker.html`, `web/static/item_picker.js`, `web/app.py` (`staging_apply`, `document_add_items`). Test `tests/test_web_item_picker.py`.

**Interfaces:** Consumes payload `reopen` and `check_items(..., reopen=)` from Task 1. Produces form field `reopen`.

- [ ] **Step 1: Write failing tests**

Replace `test_a_refused_row_cannot_be_ticked` with `test_a_refused_row_can_be_ticked_on_purpose`: row has `data-refused`, no `disabled`, keeps "Refused earlier".
`test_tick_all_counts_leave_refused_rows_out`: group of 3 with 1 refused; label after sync is JS, so assert `data-refused` on exactly that input.
`test_approve_with_reopen_enqueues_it`; `test_add_items_with_reopen_enqueues_it`; `test_a_refused_item_without_reopen_is_refused` (422).

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Implement.** Rows drop `disabled` for refused, add `data-refused`. In the script, the tick-all selector and `boxes` add `:not([data-refused])`; on `done()` and `fillAdd()`, each ticked ref whose row was refused also gets a `reopen` hidden input (keep a `refusedRefs` Set per dialog; on `open()` rebuild from `input[name=reopen]`). Show the note from spec § 5.3 in the tray for each refused tick, "when you approve" in review mode, "when you add it" otherwise. Both routes read `reopen: list[str] = Form([])` and pass it to `check_items` and the payload.

- [ ] **Step 4: Run, expect PASS.**

### Task 6: Published document Remove

**Files:** Modify `web/app.py` (`LINK_DECISIONS` tuple, `LINK_RECEIPTS`, `link_decide`, `document_detail` context), `web/templates/document_detail.html`, `web/templates/_link_decide.html`. Test `tests/test_web.py`.

**Interfaces:** Consumes decision `remove-link` (Task 3). Produces form field `confirm`, `row` on `/links/{doc_id}/decide`; template var `can_remove`.

- [ ] **Step 1: Write failing tests**

`test_remove_link_first_step_asks_and_writes_nothing`: POST without `confirm`; "Remove {item} from this document?", "Yes, remove {item}", 0 jobs.
`test_remove_link_warns_only_when_it_is_the_last`: "will have no IFU left" present with no other IFU, absent with another published IFU.
`test_remove_link_with_confirm_enqueues`: key `apply:{d}:{item}:remove-link`; `HX-Retarget` is `#link-cell-{d}-{row}`.
`test_remove_link_target_is_the_row_not_the_item_number`: item `AB/1 2`; `hx-target="#link-slot-{d}-{row}"` and the item number is absent from every id.
`test_remove_link_refused_on_a_stale_page`: link already `rejected`; both steps 422, "Nothing was changed.", 0 jobs. Same for a staged document and whole-range.
`test_covered_items_shows_actions`: production row has Remove; rejected row Re-open; staged row "Covers this item"; whole-range page has no action column.

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Implement.** In `link_decide`, for `remove-link` read document and link; refuse per the test unless document `production`, scope `group`, link `production`. Without `confirm`, return `_result.html` with `confirm_needed` per spec § 5.2, `confirm_target=f"link-slot-{doc_id}-{row}"`, `confirm_url`, `confirm_fields` (`item_ref`, `decision`, `row`), `confirm_label`. With `confirm`, enqueue, set `HX-Retarget: #link-cell-{doc_id}-{row}`, `HX-Reswap: innerHTML`. Reject a non-integer `row` with 422. Add a macro `remove_button(doc_id, item_ref, row)` to `_link_decide.html`, separate from `buttons`, so the item page is unchanged.

- [ ] **Step 4: Implement the page.** `can_remove = doc status production and scope group`. Add the column when `can_remove`: `<td id="link-cell-{doc}-{loop.index}">`. Remove on production rows, the existing `buttons` macro on others. Below each removable row a `<tr><td colspan="4" id="link-slot-{doc}-{loop.index}"></td></tr>`, with CSS hiding an empty one.

- [ ] **Step 5: Run, expect PASS.** `./scripts/test.sh tests/test_web.py`

### Task 7: Displays that count a refused link

**Files:** Modify `web/registry.py` (`_HOLDINGS_JOINS` and comment), `web/app.py` (`superseded_docs`), `web/templates/document_detail.html` (heading). Test `tests/test_compliance.py`, `tests/test_web.py`.

- [ ] **Step 1: Write failing tests.** Completeness: DoC link `rejected` on a production document, nothing else: row state `missing`, `doc_id` None. Heading: 3 rows, 1 rejected, text "Covered items (2)". `/items`: rejected link to a superseded document leaves `superseded_docs` 0.

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Implement.** `AND l.status NOT IN ('retracted', 'rejected')`; `id.status NOT IN ('retracted','rejected')`; heading counts `items | rejectattr("status", "eq", "rejected")`.

- [ ] **Step 4: Run, expect PASS.** Fix any test that pinned the old `review` state on a refused link, stating why.

### Task 8: Contract, records, guide

**Files:** Modify every file in spec § 8.

- [ ] **Step 1:** PRD C17, the bug-by-definition line, the `gate.apply` line, per spec § 8.
- [ ] **Step 2:** `docs/decisions.md` row dated 2026-11-09; `handlers.md`, `01-lifecycle.md`, handbook, `code-map.md`, `vocabulary.md`, `limits.md`.
- [ ] **Step 3:** Guide `review` and `documents`, both `.md` and `.sl.md`; run `python3 scripts/build-guide.py`.
- [ ] **Step 4:** `./scripts/test.sh tests/test_docs_sets.py`, expect PASS.

### Task 9: Verify the whole

- [ ] **Step 1:** Full suite: `./scripts/test.sh`. Expect all green; report the count.
- [ ] **Step 2:** Walk spec § 2-§ 7 against the diff, bullet by bullet.
- [ ] **Step 3:** Browser walk on a local copy of the worktree's web app: Remove, Undo, Change items and back, tick a removed item, "Tick all" over a refused row, Approve, two-step Remove with Cancel and Yes.
- [ ] **Step 4:** One fresh reviewer over the whole diff.
- [ ] **Step 5:** Report to Denis. Commit and deploy only on his word.
