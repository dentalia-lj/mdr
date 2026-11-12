# Change type on Review: design

Date: 2026-11-12. Status: draft, waiting on Denis. Not committed.
Source: tasks/handoff-2026-11-12-change-type.md (direction approved by Denis in chat).

## 1. Why

Doc 831 (ZLATARNA CELJE, "Bazne plošče Izjava EU o skladnosti 2026 izdaja 4.pdf")
is a declaration of conformity that was read as an ISO certificate. Because it
is ISO, it carries `coverage_scope = manufacturer` and sits on Review in the
whole-range panel, which accepts only the issue date as a correction
(`gate.py` bind-manufacturer raises on anything else). Nataša has no way to say
"this is a declaration, for specific items".

The group panel's "Correct a fact first" can change `type` at approval, but not
`coverage_scope`, and only as part of approving. Changing what a document IS
changes which panel it belongs in, which links it may hold, and which other
documents point to it. That is a decision of its own, taken before approving,
with every consequence shown first. Denis asked for a double confirmation.

The misread itself (T0 marker "izjava o skladnosti" missing "Izjava EU o
skladnosti") is a separate bug fix that ships first (section 10).

## 2. Goals, non-goals, anti-goals

Goals
- On a document waiting on Review, a person can change its type, regulation and
  what it covers (specific items / the manufacturer's whole range).
- Before anything is written she sees every consequence, then confirms twice:
  a second server step and a tick box.
- Afterwards the document is still on Review, in the panel that fits its new
  scope, and she approves (or rejects) it there as usual.

Non-goals
- Published, rejected, superseded or filed documents. For a published one the
  path is Reject, Reopen, Change type, Approve.
- Changing any other field (dates, certificate number, Basic UDI-DI): those stay
  in "Correct a fact first".
- Re-reading the document, running VALIDATE, or any LLM call.
- Choosing items. That stays in the item picker at approval.
- Bulk change across documents.
- Hardening T0 beyond the measured marker (section 10).

Anti-goals (a failure even if it works)
- Anything published, or any link made production, as a side effect.
- Links disappearing without an audit row each.
- A declaration left pointing at a document that is no longer a certificate.
- The document landing in the wrong panel because the task still carries the
  old route.
- A later machine reading quietly undoing her change.

## 3. Where it lives

The Review row detail (`web/templates/_staging_doc_detail.html`), for documents
with `status = 'staged'` only, both panels (group and whole range). A
"Change type..." control, visually separate from Approve and Reject, opens a
small form. Mockup first, approved by Denis before any template edit (memory:
mockups before web UI).

## 4. The form

Pre-filled with the document's current values.

| Field | Values | Notes |
|---|---|---|
| Type | DoC, EC, ISO, IFU, SPP, other | `app/extract/checks.py` `TYPES` |
| Regulation | MDR, MDD, n.a. | `checks.REGULATIONS` |
| What it covers | Specific items (`group`), The manufacturer's whole range (`manufacturer`) | migration 022 vocabulary |

When she changes Type, "What it covers" moves to the type's default: DoC, IFU,
SPP, other -> specific items; EC, ISO -> whole range. She can override it (EC
certificates with group scope exist, doc 394). The default is set in the
browser; the server takes what is posted.

Submitting with nothing changed is refused: "Nothing to change. Nothing was
changed."

## 5. Two-step confirmation

Same pattern as the whole-range approval (`staging_apply`, `confirm_needed` in
`_result.html`), on its own route `POST /staging/{doc_id}/retype`.

Step 1 (no `confirm`): writes nothing. Validates the values and the
preconditions in section 6, then answers into the panel's slot with the
consequence list, computed from the registry now:

1. "Type: ISO -> DoC. Regulation: MDR (unchanged). Covers: whole range ->
   specific items." Only changed lines say "->".
2. Scope becomes whole range and the document has staged links: "N items now
   linked will be taken off: <first 10 item refs>, and N-10 more." (Zero ->
   line omitted.)
3. Stops being a certificate (old type EC/ISO, new type not) and declarations
   cite it: "N declarations cite this as their certificate and will no longer:
   <doc ids>."
4. Becomes a certificate (new type EC/ISO, old not): if it has a certificate
   number, "N declarations citing certificate <no.> will point to it"; with no
   number, "No declaration can point to it until a certificate number is
   entered."
5. The type moves between DoC, EC/ISO and IFU/SPP/other, and a certificate
   number or expiry is set (write 1a): "Certificate number <no.> will be read as
   <the certificate this declaration cites | this certificate's own number>.
   Expiry <date> stays. Correct them under Correct a fact first when you approve
   if they are wrong." Only the values that are set are named.
6. "It stays on Review. Nothing is published. You approve it next, in the
   <specific items | whole range> panel."

Below the list: a required tick box "I have checked the document: it is a
<type name>, for <scope words>." and "Yes, change the type" / Cancel. Cancel
never reaches the server.

Step 2 (`confirm` and the tick posted back): the route repeats the checks and
enqueues `gate.apply` with decision `retype`. Without the tick it refuses (422,
"Tick the box to confirm. Nothing was changed."). The confirm posts back the
before-values it was computed from (`expected_*`), so what is confirmed is what
was shown.

Dedupe key: `apply:{doc_id}:retype` (PRD section 3 shape).

## 6. GATE: `gate.apply` decision `retype`

PRD change first (decision list, payload, writes), plus a `docs/decisions.md`
row. `retype` is a new value of the existing `decision` field, not a new job type.

Payload (additive): `doc_id`, `decision = 'retype'`, `decided_by`, `type`,
`regulation`, `coverage_scope`, `expected: {type, regulation, coverage_scope}`,
optional `note`.

Preconditions. Each raises (the job dead-letters visibly, as C17 decisions do);
the route checks the same ones first so a refusal normally never reaches the
queue:
- The document is `staged`.
- Every value is in its vocabulary, and at least one differs from the stored row.
- `expected` equals the stored row (otherwise the screen she confirmed is stale).
- No active job (`pending`, `running`, `failed`) for the document's
  `content_hash` in `extract.doc`, `validate.doc`, `gate.candidate`, and no
  other active `gate.apply` for the document. A reading in flight would land on
  the old routing.
- The document has no `production` link (a staged document should have none;
  if one exists something is already wrong and a person should look).
- The document is in no supersession chain: its own `supersedes` and
  `superseded_by` are NULL and no row points at it through either. Refused, not
  cleared, since supersession is append-only (Denis, 2026-11-12). Production
  2026-11-12: zero staged documents are in a chain. Step 1 shows the refusal.
- Order: the document row is locked `FOR UPDATE` first, and every check above
  runs after the lock.

The no-active-job check is best-effort: `fetch.url`, `upload.ingest`,
`email.poll`, `discover.group` and `backfill.scan` carry no `content_hash` and
can enqueue a reading later. Route-held (section 9) is the real guard, so both
ship together. A reading held in batch mode can refuse a retype for hours; the
message says a reading is in progress.

Writes, one transaction:
1. `type` and `coverage_scope` ALWAYS, `regulation` when changed, through
   `_apply_edits` (T3 evidence, confidence 1.0, held against machine writes from
   then on by `_reviewer_held`). Both are written even when only one changed,
   because the tick confirms both, and a type-only retype would otherwise leave
   the scope unheld and route-held (section 9) would never fire. They stay held
   until the next manual retype; if one happens, the latest retype prevails
   (its T3 rows carry the higher `extract_rev`) (Denis, 2026-11-12).
1a. `cert_number` and `validity_to` are KEPT (Denis, 2026-11-12, reversing a
   clear-on-crossing rule after the production sample). On a misread the
   number on the page does not change meaning, only our reading of it did:
   docs 832-834 and 836 hold `028/MDR`, printed "Številka EU certifikata:
   028/MDR" with notified body 0051, which is exactly what a DoC's
   `cert_number` holds (the certificate it cites, ruling 2026-10-12); 1375's
   "CE 607775" likewise. Clearing would have deleted a correct value on 5 of the
   9 documents. Retype writes neither field; the confirm names them with their
   new meaning (section 5, line 5) and she corrects them under "Correct a fact
   first" at approval if they are wrong.
2. Scope after the retype is `manufacturer` (changed or not): every `staged`
   link -> `rejected`, one `link-rejected` audit row each with `detail.via =
   'retype'`, and confirm line 2 lists them. Scope `group` touches no link: she
   picks items at approval. (my call)
3. Old type EC/ISO, new type not: `UPDATE document SET cert_doc_id = NULL WHERE
   cert_doc_id = <doc_id>`. The cleared ids go into the audit detail.
4. New type EC/ISO, old type not: `_backresolve_here`. The ids it set go into
   the audit detail.
5. Old type DoC, new type not: the document's own `cert_doc_id` is cleared (only
   a declaration cites a certificate).
6. The open `gate-manual` task (that kind only; other open tasks on the
   document are left alone) follows the new scope, so the panel and the reason
   match:
   - Scope `manufacturer`: `payload.route = 'mfr-binding'`, `manufacturer` from
     `document.canonical_manufacturer` (or the task's existing one), `reason`
     recomputed: `md-class-unknown` when that manufacturer holds no MD item,
     otherwise none. (my call)
   - Scope `group`: `route` and `reason` removed.
   - In both cases `flags` are dropped (they came from the old reading and would
     mislead "Why it needs you" and the board chips), `payload.retyped = {by,
     at}` is set, and Review shows "Type changed by <name> on <date>. <the
     panel's usual instruction>." `_review_reason` gets its own branch for it,
     in the board query and the detail, so a flagless group task does not fall
     back to the no-task sentence. (my call)
   If the document has no open task, one is opened with the same payload.
7. Audit row `retype`: `detail = {before, after, links_off, citations_cleared,
   citations_set, note}`.

Never: a status change, a promote, a `production-write`, a link made production,
or any job enqueued. `_resolve_manual_tasks` is not called (the document is
still waiting on her).

Result: `{decision, doc_id, links_off, citations_cleared, citations_set}`.

## 7. Afterwards

- Step 2's response replaces the row with a receipt, as the whole-range
  approval does (`web/app.py` after `bind-manufacturer`), so no old button stays
  live. `gate.apply` runs asynchronously; the row comes back in its new panel on
  the next load. (my call)
- The document renders in the panel for its new scope (`is_mfr_binding` reads
  the task route and `coverage_scope`; step 6 keeps them in agreement).
- `bind-manufacturer` gains a guard: it raises unless `coverage_scope =
  'manufacturer'`, and the route checks the same. Without it a whole-range
  approval confirmed from a screen loaded before a retype would bind a
  declaration to every MD item. Approve with items already refuses a
  whole-range document (`gate.py` `_picked_items`). (my call)
- "Correct a fact first" loses its Type field (Denis, 2026-11-12): every type
  change goes through Change type. Regulation stays there. The route refuses a
  CHANGED type on approve (an unchanged `edit_type` posted by a page loaded
  before the deploy still passes), and refuses `type` and `coverage_scope` in
  the raw `edits` field; GATE's `approve` refuses both edits, so an old form or
  a hand-built payload cannot bypass the consequences (Denis, 2026-11-12).
  Refused, never silently dropped: dropping would publish under the type she
  tried to change. Production 2026-11-12: no queued approval carries a type edit.
- Every `gate.apply` document decision locks the document row `FOR UPDATE`
  before its first read (Denis, 2026-11-12). This also closes the race logged in
  `docs/dev/handlers.md` (gate.apply read the status unlocked). `approve` and
  `bind-manufacturer` carry `expected_scope`, the scope the screen showed, and
  GATE refuses if the row's differs: a stale group-panel Approve with no items
  would otherwise publish a just-retyped whole-range document with zero links.
  `staging_apply` refuses Approve, Reject and the whole-range approval while a
  `retype` job for the document is active.
- Docs 831-837: once retyped a document holds T3 evidence, and `reextract`
  leaves edited documents alone (`app/reextract.py` `LEFT_ALONE`). So the order
  is T0 fix, `reextract` 831-837, then Change type for what is left (section 10).
- Approve or Reject as today. For a declaration that now covers specific items,
  that means ticking items in the picker (for 831: candidates 6050541,
  60505415Z, 6050541Z).
- Reopen after a reject brings back the retyped document: held fields stay held.
  Reject sets every link `rejected` and Reopen opens no task, and today the
  panel rule counts rejected links as links (`web/app.py` link query filters
  only `retracted`), so a whole-range document that had links would come back
  in the group panel. The panel rule's "no links" half counts only links that
  are not `rejected`, in both queries (`_staged_doc_detail` and the board).
  (my call)

## 8. Docs and tests

- PRD v3 (gate.apply decisions), `docs/decisions.md`, handbook, `docs/dev/handlers.md`.
- Guide `docs/guide/pages/review.md` and `review.sl.md`, then
  `python3 scripts/build-guide.py`, bundles committed.
- `docs/dev/limits.md`: what is not done (section 9).
- Table-driven GATE tests: each precondition; each from/to class (cert -> non-cert,
  non-cert -> cert, scope both ways, regulation only); links off with audit;
  citations cleared and set; task payload rewritten or opened; nothing published.
- Route tests: step 1 writes nothing and lists the consequences; step 2 without
  the tick is refused; stale `expected`; non-staged document refused.

## 9. Open and left to the builder

Open, for Denis:
- **A later machine reading of a retyped document.** Type, regulation and scope
  are held, so the columns keep her values. But VALIDATE never reads T3: it
  routes by the reading, and GATE picks the whole-range path off VALIDATE's
  `route`. A re-read (re-extraction, reextract, backfill) of a retyped 831 would
  still come in as `mfr-binding` and could reopen a whole-range task over a
  declaration. `reextract` and `revalidate` skip edited documents, but that is
  not the only way in: a same-hash re-fetch re-enters `validate.doc`
  (`fetch.py` recency skip, and discover, upload and email), for a document in
  ANY status, with or without an open task. On a published document with a held
  `group` scope, a confident `mfr-binding` reading today is not kept
  (`_keeps('production', True)` is False): it links every MD item at
  production, and writes no audit row because the document was already
  production. (Corrected 2026-11-12 after the Fable review; this said "only
  after Reject then Reopen".)
  **Ruled (Denis, 2026-11-12): build it, at every status.** One branch in
  `_gate_candidate`, before the `route == 'mfr-binding'` dispatch so the two
  routes cannot drift. Order: the superseded-reading no-op (`_is_current_rev`)
  first, then lock the row, then `_reviewer_held`. When `coverage_scope` is held:
  - `production`, `superseded`, `rejected`, `filed`: `_kept(...)` with
    `disposition = 'route-held'`. No links at all, including the staged
    proposals a kept production document gets today (R1); a person adds items
    with add-items. No task.
  - `staged`: the usual reading checks, then `_upsert_document(status='staged',
    held=held)` with no `supersedes`, `related_doc_id` (column `referenced_doc_id`), `cert_doc_id` or
    `canonical_manufacturer` from the reading, `_insert_evidence(held=held)`,
    `_backresolve_here`, and the task for the held scope's panel through the
    helper shared with retype write 6, the reading's flags dropped. Never
    `_retract_unsupported_links`, `_upsert_link` or `_resolve_manual_tasks`.
  Result `disposition = 'route-held'`. Returning there also pre-empts the
  auto-supersede fast path and the chain-identity check. A held scope that
  agrees with the route takes the same branch: a document a person retyped
  waits for that person.

Left to the builder:
- Exact wording of the consequence lines and the tick sentence, in EN and SL,
  following `web/words.py`.
- The document's own forward `cert_doc_id` when it becomes a DoC is not
  resolved (that is VALIDATE's forward resolver, not run here). Write it in
  limits.md.

## 10. T0 marker fix (ships first, on its own)

Measured on production 2026-11-12, read-only, 1,559 documents holding text,
first 4,000 characters, whitespace folded.

- The hole: `izjava o skladnosti` (`app/extract/t0_templates.py` `_TYPE_MARKERS`)
  misses the infixed "Izjava **EU** o skladnosti". With no declaration marker
  on the page, `iso 13485` from the standards list is the only family, and
  `_titled_certificate` passes on "Številka EU certifikata" (the line where the
  declaration cites its certificate), so ISO scores 1.0.
- The fix: one marker, `izjava eu o skladnosti` -> DoC. Once a declaration
  marker hits, ISO already drops out (`extract_type`), so the certificate-word
  check needs no change for this case.
- Measured: 12 candidate spellings were tested (sl infix, hr, it/es/pt infix,
  nl, pl, cs, hu, sv, da, "conformance"). Among documents carrying no current
  declaration marker, only the sl infix hits, on exactly 831-837, all ISO,
  all staged, all Zlatarna, all wrong. Nothing else moves. The other spellings
  are not added (zero hits; no speculative markers).
- Not changed: `_titled_certificate`. It is fooled by a citation line on 146
  documents that also say "declaration". 139 of them are typed DoC (the
  declaration marker won). The other 7 are typed EC or ISO (2196, 2197 staged;
  87, 88, 861, 1164 production; 1732 rejected) and are not checked by hand:
  they may be real certificates that mention a declaration. Looking at them is
  a follow-up, not part of this fix.
- Then, after Denis approves a server job: `reextract` 831-837. This re-reads
  type at T0 1.0 as DoC. Scope will likely still come out `manufacturer` (a DoC
  with no REF list T0 can parse) and the open task keeps `route: mfr-binding`,
  so Change type is still how she sets "specific items". Re-extracting first
  matters because `reextract` skips a document once it has been retyped.
