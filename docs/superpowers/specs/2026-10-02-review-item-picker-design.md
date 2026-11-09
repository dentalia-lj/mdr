# Review item picker: the reviewer names the items a document covers

**Status:** design approved 2026-10-02; §6 rewritten 2026-10-05 so the picker
builds on main as it is. Nothing here is built.
**Asked by:** Dentalia (Natasa), call of 2026-10-02.
**Ruled:** 2026-10-02 (`docs/decisions.md`, row "Review: documents that name no
items"). **Mockup approved:** artifact `Dzfd2AXZ88xc8gA3ub33Zp`, the same day.
**Depends on:** nothing unbuilt. Per-item currency is separate work (§6).
**Amends:** PRD C17 (`manual` gains a second writer) and the `gate.apply approve`
payload (one additive field).

---

## 1. The problem, measured

A document that names no article numbers reaches Review with nothing linking it
to an item. Approving it publishes a document that counts for no item. The panel
says so: *"Nothing links it to an item yet, so approving it makes it count for
no items."* The reviewer often knows exactly which items it covers, and today
has no way to say so.

On the server, 2026-10-02:

| Review documents covering specific items (`coverage_scope = 'group'`) with no item link | 756 |
|---|---|
| of which the manufacturer is confirmed on the document | 445 |
| of which the printed manufacturer matches no known name | 303 |
| of which no manufacturer was read | 8 |
| of which the extracted text is stored (`document_text`) | 756 |

The worked example is doc 8, `navodila za uporabo varibase Straumann novo.pdf`:
Straumann's own Variobase IFU, 143 pages, 27 languages. It names no article
number. Straumann holds 914 items; **54 carry "Variobase" in the name, spread
over 24 catalogue groups**, so a catalogue group alone is far too narrow a unit.

## 2. What was ruled

- **Exact items, chosen by a person.** The reviewer ticks items, or every item
  of a catalogue group at once. Nothing is ambiguous and nothing is guessed.
- **No automatic linking.** The system suggests; it never links. A suggestion
  carries no weight until a person ticks it.
- **Linked only on approval.** Ticking changes nothing. Approve publishes the
  document and links the ticked items in one step; Reject ignores them.
- **A ticked item is a confirmed link**, recorded exactly as `confirm-link`
  records one: `match_basis = 'manual'`, `link-confirmed` under the reviewer's
  name.
- **Manufacturer locked.** Only that manufacturer's items can be found, through
  the BC code mapping (`manufacturers.item_codes_for`). Never another's.
- **Fuzzy search, capped.** Only close matches are listed.
- **Refused earlier** (a link to this document a person rejected) is shown and
  cannot be ticked.
- **Nothing pre-ticked**, including items already linked by a weak name match.
- **The button appears on every Review document that covers specific items**,
  not only on those with no link at all, so a reviewer can also add items to a
  declaration that matched 8.
- **A document with no issue date gets one from the reviewer** (ruled
  2026-10-02; §7 here).
- **Items can also be added later, to a document already published** (ruled
  2026-10-02): the same picker on the document's page (§3.1). A reviewer may
  approve now and add items when they are known, or when a new item arrives.

## 3. The flow on Review

As in the mockup.

1. The reviewer opens a Review row. Under "Approving makes it count for…", on a
   document that covers specific items (not a whole-range approval), a button:
   **Find items this document covers…**
2. It opens a modal (`<dialog>`) over the page.
   - **Manufacturer.** If the document has a confirmed manufacturer
     (`document.canonical_manufacturer`, which `gate.candidate` writes only when
     the printed name resolves to exactly one canonical), it is shown and
     locked. The Review row's `manufacturer_confirmed` is not this: it is read
     from the document's item links, so it is empty on every unlinked document. If not, the existing manufacturer list (`mfr_options`, with the
     printed-name preselect `mfr_suggestion`) comes first and the search waits
     for a choice. Today those two are built only for whole-range rows
     (`web/app.py`, "Binding candidates only"); the picker builds them for a
     document covering specific items too, from the same
     `_mfr_binding_options` / `_mfr_binding_suggestion`.
   - **Suggestions on opening** (§4.2), as clickable chips. The first word is put
     in the search box; nothing is ticked.
   - **Search** by item number or name.
   - **Results** grouped by catalogue group, closest first. An item in several
     groups (`item_group_member` allows it) is listed once, under its lowest
     `group_id`; **Tick all N** ticks the rows listed. Per group: the label,
     the count shown, **Tick all N**. Per row: tick box, item number, name,
     medical device (yes / no / not set in BC), the item's current documents by
     type (DoC / CE / IFU / ISO, filled when present), the item's newest
     published document **of this document's type** with its date and how it
     compares (§6), and **Similar**.
   - **Similar** on a row lists that manufacturer's items with names like it.
   - **Tray:** "N items ticked", each removable, **Clear all**.
   - **Done** closes the modal and keeps the ticks. **Cancel** restores the ticks
     the modal opened with.
3. The panel then reads "Approving makes it count for these N items", the list
   (first five, "+ K more" folded), a line on the documents of this type they already hold (§6), and
   **Change items…**. The Approve button says "Approve for these N items".
4. Approve sends the ticked items with the decision. Collapsing the row or
   reloading the page before Approve drops the ticks.

### 3.1 On a published document

The document page (`/documents/{id}`) of a `production` document covering
specific items carries the same button and the same picker. Its Done does not
wait for an Approve: the modal's footer has **Add these N items**, which sends
the `add-items` decision (§5.2) and shows the result in place. The manufacturer
is the document's confirmed one; a published document without one asks for it
first, as on Review. Items already linked are shown ticked and disabled.

### 3.2 Where the ticks live

The ticks live in the page until Approve, as hidden `items` inputs in the
panel's decision form. The modal's lists and the panel's summary are rendered by
the server (Jinja fragments over HTMX); the page script only opens and closes
the dialog, keeps the tick set, and copies it into the form on Done.

## 4. Search and suggestions

### 4.1 The candidate set

Every `item_mirror` row whose `manufacturer_raw` is one of
`manufacturers.item_codes_for(conn, manufacturer)`. Nothing outside it can be
returned by any of the routes below. The largest single BC code holds 2.581
items, so the set is never scanned without a filter or a cap.

### 4.2 Suggestions on opening

Two sources, both read-only and both shown as chips:

1. **Words from the document.** The file name and the first page of
   `document_text` (the stored text carries `[[page N]]` markers, so page 1 is
   everything before `[[page 2]]`), split into words, with the manufacturer's own name and the
   document-type vocabulary removed (izjava, skladnosti, navodila, uporabo,
   declaration, conformity, instructions, use, and the like). Each remaining word
   that matches at least one candidate item becomes a chip. For doc 8 that
   would be "varibase" from the file name and "Variobase", "Abutments" from page 1
   (`Straumann® Variobase® Abutments · 701593/M/12 · 04/26`).
2. **Published documents of the same manufacturer** whose file name shares a
   chip word. The chip lists the items that document covers. For doc 8: the two
   Variobase declarations, doc 1 (34 items) and doc 6 (16 items).

Items already linked to this document by a weak name match (`name-family`,
`fetch-context`, `ref-catalogue`, staged) are listed under their own heading,
not ticked.

### 4.3 Search

One box takes an item number or a name (ruled 2026-10-05, figures in
`docs/state/2026-10-05-picker-thresholds.md`).

- **Item number first.** The items whose number contains the typed text, those
  whose number starts with it first. Numbers are never matched fuzzily:
  010.6042 and 010.6043 can be unrelated items.
- **Nearby numbers.** Below the number matches, a separate list: the **10**
  items before and the 10 after, in the manufacturer's item-number order, around
  the matches that start with the typed text (or around the single match when
  only one contains it). Listed, never ticked, no "tick all": on the server the
  ±10 neighbours were the same family for 83% of E.MAX and 50% of Variobase
  numbers, and for none of bone level's 3.
- **Then names**, only when no number matches and the text has a letter:
  `greatest(word_similarity(q, name), word_similarity(q, name without
  punctuation))` at or above **0.5**, closest first. Ignoring punctuation is what
  makes "emax" find "E.MAX" (0.40 with the dot, every one of 431 items without
  it); "varibase" scores 0.58 against every Variobase name. At 0.4 "emax" also
  listed 50 Empress items.
- Text without a letter or digit lists nothing; text without a letter is never
  searched as a name.
- At most **200** rows. Above that, the count line says how many matched and asks
  for a narrower word.
- The trigram index on `item_mirror.name` exists (migration 012). A plain
  `word_similarity(...) >= t` does not use it; within one manufacturer (at most
  2.581 rows) the scan costs little, and the `<%` operator with
  `pg_trgm.word_similarity_threshold` is the index route if it ever shows.

### 4.4 Similar

`similarity(name, example.name)` over the candidate set, at or above **0.2**,
closest first, capped at 200, the example itself excluded. Similar is the
"enter one that fits, find the rest" path, so finding the family outranks a
short list: from one Variobase crown base, 0.2 lists 49 of the 53 other
Variobase items and 16 others; 0.4 listed only 17 (measured 2026-10-05).

### 4.5 Cost

The per-row document icons for all 914 Straumann items took **9 ms** on the
server (2026-10-02): production links joined to their documents, grouped by
type, one query. At ~100k catalogue items the `item_mirror` scan grows; an index
on `manufacturer_raw` is the remedy when it shows, not before.

## 5. What Approve writes

### 5.1 The request

`POST /staging/{doc_id}/apply` with `decision=approve` accepts repeated `items`
fields. The route refuses with 422 and "Nothing was changed" when:

- any item is outside the manufacturer's candidate set (§4.1);
- any item has a `rejected` link to this document;
- the document is a whole-range one (`is_mfr_binding`);
- items are sent and no manufacturer is confirmed or chosen;
- the document already has a confirmed manufacturer and a different one is sent.

### 5.2 The payload

`gate.apply` `approve` gains one optional field, **`items`**: a list of
`item_ref`. It reuses the existing optional `manufacturer` field for a document
with none. Additive (CLAUDE.md: payload fields additive-only); a payload
without `items` approves exactly as today.

For a published document (§3.1), one new `gate.apply` decision,
**`add-items`**, with the same `items` and `manufacturer` fields. It requires
the document to be `production` (raises otherwise) and runs §5.3 steps 1-5
without the promote. A new entry in the closed decision list, so a PRD change
(the job type is unchanged; `decision` is a payload value; `audit_log.event` is
text, so no migration). Its dedupe key carries the item set,
`apply:{doc_id}:add-items:{digest of the sorted items}`: the route's usual
`apply:{doc_id}:{decision}` would silently drop a second batch sent while the
first is still queued. Its audit row is
`add-items` with the count and the manufacturer, beside the per-link
`link-confirmed` and `production-write` rows.

### 5.3 The handler

In `handle_gate_apply`'s `approve` branch, in the same transaction as the
promote, and only when `items` is non-empty:

1. **Manufacturer.** The document's `canonical_manufacturer`, or
   `_bindable_manufacturer(payload.manufacturer)` when it has none. Neither →
   raise. A payload manufacturer different from a confirmed one → raise. A
   document without one gets it written.
2. **Check every item again** (the route checked first; the handler is the last
   word, against hand-built payloads and replays): exists, belongs to the
   manufacturer's codes, has no `rejected` link to this document. Any failure
   raises and names the offending items; nothing is written.
3. **Write each link** through `_upsert_link(item, doc, udi, 'manual',
   'production')`, `udi` being the existing link's (null for a new row): the
   upsert overwrites a non-sticky row's `udi` with what it is given. Its existing conflict rule does the right thing in every
   state: no row → new `manual` production link; `staged` (any basis) or
   `retracted` → `manual`, production; `production` → left as it is, basis kept
   (an article-number link is already trusted and is not rewritten).
4. **Audit, per link that changed:** `link-confirmed` with `match_basis_before`
   and `link_status_before` (null for a new row) and `via: approve`, then
   `production-write`: the same pair `confirm-link` writes. A link that was
   already production writes nothing, which also makes a re-delivery quiet.
5. The `approve` audit row's detail gains `items` (the count) and
   `manufacturer`.

`_promote_pending_links` and the supersession step run as today. The supersession
step reads `document.supersedes`, which VALIDATE wrote before any item was
ticked, so ticked items never add a supersession (§6).

### 5.4 The contract

PRD C17 states `confirm-link` is the **sole** writer of `match_basis = 'manual'`
(PRD lines 269, 320, 437). It becomes: `confirm-link` and `approve` with
`items`. The drift guard (PRD line 485: every `manual` link has a
`link-confirmed` entry naming its decider) holds unchanged, because step 4
writes one per link. `gate.py`'s "Sole writer" comment changes with it.

## 6. Items that already hold a document of this type

The picker row shows, per item, its newest published document of this type,
with its date and a label against this document: *older*, *newer*, *same date*,
*no date*, or nothing when the item holds none. It informs the reviewer; nothing
is blocked.

Approve retires nothing for the ticked items. The supersession step in
`approve` acts only on `document.supersedes`, which VALIDATE wrote before any
item was ticked; on the server on 2026-10-02 it was null on all 756 documents of
§1. An item that already holds an IFU holds two published IFUs after Approve, as
426 item/type pairs on the server already did that day. Which of them counts as
current for the item is the per-item currency work
(`2026-09-15-per-item-currency-design.md`): separate, not needed here.

The panel line after Done counts them: "3 of these items already hold an IFU
(2 older, 1 newer); this one is added beside it."

## 7. Undated documents on Review

Ruled 2026-10-02: the reviewer adds the date. When `validity_from` is null, the
panel's "Issued" fact reads "Not stated. Enter it before approving.", and
**Approve is refused until a date is entered** under "Correct a fact first"
(existing edit path, T3 human evidence). The route refuses with 422 and "Nothing
was changed"; the handler refuses too (raises), for `approve` and
`bind-manufacturer`, against a hand-built payload. `gate.candidate` is not
affected: this is a rule for human approval. Doc 8 shows why this is reachable:
its page 1 prints `04/26`, which the extraction did not read.

This applies to **every approval**, whole-range included (ruled 2026-10-02). On
the server that day: 45 undated documents on Review, 37 covering specific items
and 8 whole-range. The whole-range approval offers no corrections today: its
bind path drops edits (recorded in the template as a "controller ruling,
2026-09-11", from the office UI build). It gains the Issued date field, and only that one, and
`bind-manufacturer` accepts that single edit.

## 8. Documentation and guide

- PRD: C17 writer sentence and line 437; the `gate.apply` payload row (`items`);
  the closed decision list gains `add-items`.
- `docs/dev/handlers.md`: `gate.apply approve` with `items`.
- `docs/guide/pages/review.md` and `review.sl.md`: the button, the picker, the
  panel after Done, the undated rule; then `python3 scripts/build-guide.py` and
  the bundles.
- `docs/dev/web.md`: the new routes.

## 9. Out of scope

- **Automatic linking** of any kind, including from the suggestions' scores.
- **Group-level coverage** that follows a catalogue group as it changes. Groups
  are machine-built and re-form; the ticks are a snapshot of items.
- **Whole-range documents** (certificates, ISO): their approval is unchanged.

## 10. Testing

Table-driven, against a real Postgres (CLAUDE.md):

- **Gate, approve with items:**
  - links written `manual`/`production` only on approve, with one
    `link-confirmed` and one `production-write` per new or changed link;
  - an item of another manufacturer raises, nothing written (the promote rolls
    back with it);
  - an item with a `rejected` link raises;
  - no manufacturer anywhere raises; a conflicting one raises; a missing one is
    written;
  - a staged `name-family` link becomes `manual` production, its
    `match_basis_before` audited;
  - a production `ref-list` link is left alone, basis kept, no audit;
  - a re-delivered job writes no second audit row;
  - `reject` with `items` links nothing;
  - `approve` without `items` is byte-identical in effect to today;
  - `add-items` on a `production` document links and audits as approve does,
    and raises on a document that is not `production`.
- **Web:**
  - search returns only the manufacturer's items, capped at 200 with the count
    line;
  - item-number prefix search;
  - a refused-earlier row renders disabled;
  - each 422 of §5.1 says "Nothing was changed" and enqueues nothing;
  - the button is absent on a whole-range document;
  - the document page of a published document offers the picker and its
    **Add these N items** posts `add-items`;
  - the panel summary and the Approve label follow the ticks;
  - an undated document's Approve is refused until a date is entered.
- **§6:** approve with items supersedes exactly what approve without them
  supersedes; the row label per case (older, newer, same date, no date, none).

## 11. Open questions for review

1. ~~Undated documents, whole-range approvals~~ Ruled 2026-10-02: the date is
   required on every approval (§7).
2. ~~The two similarity thresholds~~ Measured and ruled 2026-10-05: names 0.5
   with punctuation ignored, Similar 0.2, nearby numbers ±10 (§4.3, §4.4).
