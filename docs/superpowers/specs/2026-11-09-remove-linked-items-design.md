# Remove linked items: a person can take a wrong item off a document

**Status:** built 2026-11-09.
**Asked by:** Dentalia (Nataša Palme), feedback of 2026-11-06, must-fix. Followup `[review-remove-linked-items]`.
**Mockup:** artifact `AZ8mZcg4GBhcnoUt7aeCPQ`, version 4, sections 1, 2 and 3 accepted by Denis 2026-11-09.
**Amends:** PRD C17 and its `gate.apply` line; reverses one clause of the 2026-10-02 picker ruling (§ 8).
**Migration:** none.

---

## 1. The problem

GATE links items by finding their numbers in a document. A number can be printed
in a document that does not cover that item.

The case: doc 788, DETAX FREEPRINT temp IFU (`Freeprint-dental-temp-22593-USA-BPZ-V4-230116.pdf`),
on Review. Its "Ordering information" table lists the whole product family. The
stored `ref_list` evidence (T1, page 1) reads: *"Ordering information section lists
catalogue numbers for FREEPRINT temp 385 (...), FREEPRINT tray 385 (04086),
FREEPRINT ortho 385 (04095), FREEPRINT splint 2.0 385 (02080, 02076)"*. So:

| Item | Name | Link | Should it count |
|---|---|---|---|
| 02076 | FREEPRINT SPLINT 2.0 1000G | `ref-list`, staged | No |
| 02080 | FREEPRINT SPLINT 2.0 500G | `ref-list`, staged | No |
| 04086 | FREEPRINT TRAY 1000G | `ref-item`, staged | No |

The temp numbers it prints (02014, 02108, ...) match no catalogue item, so the
six items it really covers (04058-04064) had to be ticked by hand. The Review
panel then said "Approving makes it count for these 9 items", and there was no
way to take the 3 off. Nataša sent a screenshot with them crossed out in red.

Why there is no way today:

- `gate.apply approve` publishes every trusted staged link (`_promote_pending_links`).
- The picker only adds. On Review it does not mark a staged trusted link at all
  (weak ones get their own "Linked only by a name match" list); a `production`
  link shows ticked and disabled.
- `reject-link` needs a published document and a `staged` link. So a trusted link
  cannot be refused before Approve (the document is staged) or after it (the link
  is production).

### Measured on the server, 2026-11-09 (read-only, deployed commit 1cc1274)

| Measure | Value |
|---|---|
| Documents on Review | 490 |
| of which have links Approve would publish | 20 (52 number links) |
| of which would retire an older document on Approve | 0 |
| Published documents with number links | 423 |
| of which published by the machine, no person | 385 |
| Whole-range links, all on `coverage_scope = 'manufacturer'` documents | 9.264 on 28 documents |
| Refused (`rejected`) links | 0 |
| Items ever pushed to BC successfully | 7 |

---

## 2. What we want

**Goals**

1. On Review, a reviewer can take off any item that Approve would publish.
2. On a published document covering specific items, a person can take off any
   item, after confirming.
3. A refusal can be reversed in the picker, on purpose, one item at a time.
4. A refused item stops looking like an open question on the completeness card.

**Non-goals**

- Stopping wrong matches at the source. No extraction or GATE rule changes. A
  blanket "IFU number matches wait for Review" cap was considered and dropped:
  on the server 1 IFU with number links published itself, people approved 12.
- Removal on whole-range certificates (`coverage_scope = 'manufacturer'`).
- Any Business Central work. See § 7.
- Per-item currency. That is the next project (`item-currency` branch), and it
  retires the document-level supersession this spec works around.
- Remove on weak links (`fetch-context`, `name-family`, `ref-catalogue`) on
  Review. Approve does not publish them, and "Does not" already exists after it.

**Anti-goals: it failed if**

- A refusal gets reversed without a person meaning to (a stale page, "Tick all").
- A removed link comes back after a re-read.
- A removal silently does nothing. A person's decision raises, or it holds.
- Link or audit history is lost. Nothing is deleted.

---

## 3. The rulings this builds on

Denis, 2026-11-09:

- Bring Remove first; `item-currency` right after, as its own project.
- BC can wait while the automatic push is off.
- No current document left: **warn only**, never block.
- Removal on a published document needs a **double confirmation**, using something
  the app already has.
- No warning on Review. Mockup section 1a was dropped: it could fire on 0 of 490
  documents today and is dead code once per-item currency merges.
- Whole-range documents are out.

Standing rules this must keep: only GATE writes the registry (inv. 1); job types
are a closed enum, and this adds no job type; payload fields are additive; every
link refusal is audited with the basis it had (inv. 10).

---

## 4. How it works

### 4.1 One state: `rejected`

A removed link becomes `item_document.status = 'rejected'`. It already means "a
person said this document does not cover this item". Nothing new is needed:

- Machines cannot change it (`STICKY_LINK_STATUSES`).
- `item_document_production`, `item_document_history`, the BC rule and the picker
  already treat it as not coverage. The three displays that do not are fixed in § 6.
- Re-open already exists on the item page.
- `match_basis` is left alone. The row and its provenance stay.

The audit event is the existing `link-rejected`, with `detail` carrying
`match_basis_before`, `link_status_before` and, on Approve, `via: "approve"`.

### 4.2 On Review: a `remove` list on Approve

`gate.apply approve` gains an optional `remove: [item_ref, ...]`. It is an
**explicit list of what to take off, never a "final set"**, so an item the
reviewer never saw (a list cut short, a new proposal) is untouched.

Nothing is written until Approve. Closing the panel loses the removals, as it
loses ticks today.

### 4.3 On a published document: a `remove-link` decision

A new row in `LINK_DECISIONS`, both in `app/handlers/gate.py` and in the literal
tuple in `web/app.py`:

| decision | requires link | sets | audit event |
|---|---|---|---|
| `remove-link` | `production` | `rejected` | `link-rejected` |

It runs through `_apply_link_decision`, so it shares the document-must-be-published
precondition, idempotence on re-delivery and the audit detail. It also raises if
`coverage_scope` is not `group`. Dedupe key, as for the other link decisions:
`apply:{doc_id}:{item_ref}:remove-link`.

### 4.4 Reversing a refusal: `reopen` beside `items`

`approve` and `add-items` gain an optional `reopen: [item_ref, ...]`, which must be
a subset of `items`. It names the items the person ticked **despite** the refusal.

- `item_picks.check_items` still reports `refused`, except for refs in `reopen`.
  So a refused item that arrives in `items` alone is still refused, on the route
  and in GATE. A stale page or a script error cannot reverse a refusal.
- GATE moves each listed link `rejected -> staged` with its own `link-reopened`
  audit entry (`detail.via` = the decision), then `_write_items` confirms it as
  today (`manual`, `link-confirmed`, `production-write`).
- `reopen` is what lets the link leave `rejected`, because `_upsert_link` keeps
  `rejected` sticky.

### 4.5 Order inside `approve`, and why

```
validate items, reopen, remove      (before any write; raises)
_apply_edits, _require_issue_date
_promote
remove the `remove` list            (before the promote, or they would publish)
_promote_pending_links
reopen the `reopen` list            (after the promote, see below)
_write_items
_apply_supersession                 (unchanged)
```

Reopen must follow the promote. Run the other way round, a reopened trusted link
is published by `_promote_pending_links` with its old basis and **no
`link-confirmed` entry**. Measured: `production` / `map-supplier`, 0 confirm rows.

### 4.6 What `remove` accepts

| Link status at Approve | Result |
|---|---|
| `staged` or `production` | becomes `rejected`, audited |
| `rejected` | no change, no audit row (re-delivery) |
| `retracted` | no change, counted as `remove_skipped` in the job result. PRD C17: a decision never targets `retracted` |
| no row | raises |

Also raises, on the route (422, "Nothing was changed.") and in GATE before writing:

- the same item in `remove` and in `items`
- `coverage_scope` is not `group`
- on the route only: the document is not on Review

The `approve` audit row's `detail` gains `removed: <count>`, next to `items`.

---

## 5. What the person sees

The mockup is the reference for layout and wording. Parts outlined in it are new.

### 5.1 Review panel (`_approve_scope.html`)

Where the list "Approving makes it count for these N items" already is.

- Each row of `row.approve_links` gets **Remove**, only where the picker is offered
  (`offer_picker`). A tag says how it got there: the `BASIS_WORDS` text for a
  system match, "you chose" for a tick. Ticked items have no Remove; "Change
  items" remains their way out.
- Removed items move to a block **"Will not count (N)"**, struck through, each
  with **Undo**, and this text: *"Recorded as not covered, under your name, when
  you approve. A later re-read of this document will not link them again."*
- The count line and the Approve button label follow what is left.
- Weak links in the "It is also linked to N more items" block do not change.

**State.** The removals live in hidden `remove` inputs in a new container
`#picker-removed-{doc_id}`, inside the decision form, beside `#picker-items-{doc_id}`
and outside the swapped section. `done()` in `item_picker.js` empties the items
container today, which is why the removals need their own.

Remove, Undo and picker Done all call the existing `GET /picker/{id}/summary`,
which gains `remove: list[str]`. The server redraws the list, count and Approve
label, in one path, with or without ticks. The `{% if picked %}` split in the
template has to show the block in both branches, and `approve_count` must leave
out the removed.

**Last action wins, in the browser.** Ticking an item in the picker clears its
removal. Remove on an item also clears its tick. So the UI cannot send both. A
`production` link under a staged document renders ticked and disabled in the
picker, so for that row Undo is the only way back.

Remove and Undo are `type="button"` and sit **outside** the decision form: the
panel section is rendered above it (`_staging_doc_detail.html`, section at line 59,
form from line 84). The script reaches `#picker-removed-{doc_id}` by id.

### 5.2 Published document (`document_detail.html`, card "Covered items")

Only when `doc.status == 'production'` and `doc.coverage_scope == 'group'`. A new
last column.

| Row status | Action |
|---|---|
| `production` | **Remove** |
| `rejected` | **Re-open** (existing macro) |
| `staged` | **Covers this item** / **Does not** (existing macro) |

The Re-open path is the existing one: it returns to Waiting for review and must
be confirmed. It is not a one-click undo.

Double confirmation uses the `_result.html` two-step (`confirm_needed`,
`confirm_url`, `confirm_target`, `confirm_fields`, `confirm_label`), the one behind
"Yes, approve for N items". `POST /links/{doc_id}/decide` gains `confirm`:

- `remove-link` without `confirm`: write nothing, answer with the question. Lines:
  *"Remove {item} from this document?"*, *"The document stops counting for this
  item right away."* and, only when true, *"{item} will have no {type} left. This
  is the only current one it has."* The button reads *"Yes, remove {item}"*.
- "Only when true" is computed at this step: no row in
  `item_document_production` for this item and this document's `type` with another
  `doc_id`.
- With `confirm`: enqueue, show the receipt in the slot, and empty the action cell
  out of band (`hx-swap-oob`), so neither the question nor a live Remove stays
  behind.

**`confirm_target` is built from the row number, never the item number.** It lands
in `hx-target="#..."` as a CSS selector, and item numbers contain `/` and spaces
(`314 368 023`).

Cancel only empties the slot, so the slot must be a separate element from the
Remove button. Mockup section 2 shows the question full-width under the row.

Receipt: add `remove-link` to `LINK_RECEIPTS`.

### 5.3 Picker (`_item_picker_rows.html`, `item_picker.js`)

- A refused row is no longer `disabled`. It keeps "Refused earlier" and gains
  `data-refused`.
- **"Tick all" skips `data-refused`**, in its action, its count and its label.
- Ticking one shows: *"{item} was refused for this document. Ticking it reverses
  that under your name, when you approve."* On a published document: "...when you
  add it."
- Done and the add-items form send the item in `items` and in `reopen`.

---

## 6. The completeness card

`app/compliance.py` calls any held row that is not fully production "a decision
someone still owes". `_HOLDINGS_JOINS` in `web/registry.py` excludes only
`retracted`, so a refused link is picked as the item's holding when nothing better
exists. Measured: after refusing a link, the DoC row became `review` / `warn`
pointing at the refused document, and was `expired` / `bad` before.

Fix: `AND l.status NOT IN ('retracted', 'rejected')` on that join, and update its
comment. The card then says `missing`. Unseen today only because the server holds
0 refused links; the first Remove would surface it.

Two more displays count a refused link, and both get the same correction:

- The heading "Covered items (N)" in `document_detail.html` counts every row
  returned, so it would still include the item just removed. Count the rows that
  are not `rejected`. The refused rows themselves stay, for Re-open.
- `superseded_docs` in the `/items` query (`web/app.py`) filters only `retracted`.
  Use `NOT IN ('retracted', 'rejected')`.

The item page's own document list keeps showing refused rows. That is where
Re-open is.

---

## 7. Business Central

No work. A removed link keeps its row, so `bc.push` computes `false` for it where
nothing else covers the item, the bulk preview lists the difference, and the drift
selection includes it. Both verified on 2026-11-09 by running the real code. IFUs
do not feed the BC fields at all.

Until a push runs, BC keeps the old value. Accepted by Denis while the push is off.

---

## 8. Contract and record changes (same change as the code)

**PRD `docs/dentalia-pipeline-contract-prd-v3.md`**

- C17 table: add the `remove-link` row. Say it requires `production`, and
  `coverage_scope = 'group'`.
- Rewrite "`confirm-link` / `reject-link` require `staged` (`production` is an
  idempotent no-op ...)": `reject-link` on a production link raises today.
- Bug-by-definition line: "Any link that moves out of `rejected` other than through
  a named person's `gate.apply reopen-link` or the `reopen` list of `approve` /
  `add-items`, each writing `link-reopened`, is a bug by definition".
- `gate.apply` payload line: add `remove-link`, `remove?`, `reopen?`. `item_ref` is
  required by the four link-level decisions.

**`docs/decisions.md`:** a row for 2026-11-09. It records removal, `rejected`
reuse, warn-only, the IFU cap considered and dropped, and that it **reverses "an
item refused earlier cannot be ticked"** from the 2026-10-02 picker ruling.

**Also:** `docs/dev/handlers.md`, `docs/dev/01-lifecycle.md` if the flow text
moved, `docs/dentalia-job-type-handbook.md`, `docs/code-map.md`, `docs/vocabulary.md`,
`docs/dev/limits.md` (whole-range links cannot be removed).

**Client guide, English and Slovene, then `python3 scripts/build-guide.py`:**
`docs/guide/pages/review.md` (line 146 says a refused item cannot be ticked),
`docs/guide/pages/documents.md` (line 19). Commit the bundles.

---

## 9. Tests

Through `./scripts/test.sh`. No migration or `app/queue.py` change, but
`app/handlers/gate.py` and the web layer are touched; run the covering files in the
loop and the full suite once before commit.

Table-driven in `tests/test_gate_apply_items.py` / `tests/test_gate_apply_handler.py`:

- `approve` + `remove`: removed link `rejected`, basis kept, audited, the others publish.
- each row of the § 4.6 table; same item in both lists raises; whole-range raises.
- re-delivered `approve` is quiet: no second audit row.
- a re-proposal after removal leaves `rejected`.
- `remove-link`: production to rejected; staged raises; staged document raises;
  whole-range raises; idempotent.
- `reopen`: refused item in `items` alone raises; with `reopen` ends `manual` /
  `production` with `link-reopened` and `link-confirmed`. `reopen` not a subset raises.
- the order in § 4.5: a reopened trusted link gets `link-confirmed`.

`tests/test_item_picker.py`, `tests/test_web_item_picker.py`, `tests/test_web.py`:

- `check_items` with and without `reopen`.
- the summary route with `remove`: count, label, block, with and without ticks.
- refused row renders enabled with `data-refused`.
- remove-link step one writes nothing and shows the warning only when true; with
  `confirm` it enqueues. An item number with `/` and a space works.
- the action column is absent on whole-range documents.
- completeness: refused link reads `missing`.

`item_picker.js` has no unit tests. Walk it in a browser on dev: Remove, Undo,
Change items and back, tick a removed item, "Tick all" over a refused row, publish.

---

## 10. Left to the builder

- Helper names and where they sit in `gate.py`.
- Exact markup of the "Will not count" block and the confirm row, within the mockup.
- Whether `_decorate` should also mark system links in the picker. Not required.

## 11. Known limits, stated

- Approve can still retire a whole older document, including for an item removed
  from the new one. 0 such documents are on Review today. `item-currency` removes it.
- Doc 29's 142 items have no current DoC (retired 2026-09-28 by doc 275). Separate:
  `item-currency`. **Do not turn on the daily BC push or press the bulk push before
  that repair**, or those items are sent `false`.
- Decisions are recorded under the shared login, `admin`, until named logins exist.
  "Under your name" is existing wording that is only true after that.
