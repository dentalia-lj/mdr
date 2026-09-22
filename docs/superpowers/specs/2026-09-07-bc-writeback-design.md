# BC writeback — three fields, per item, from our warehouse

**Status:** design, approved in chat 2026-09-07. Not built.
**Owner:** unassigned — this is scope that arrived after the ladder was
drawn. It belongs beside S2.4 (the other outbound direction) rather than inside
S2.5. `PHASES.md` § 1 gains a row when the first slice lands, not before.
**Needs a new job type, so the PRD §0 change and the migration come first**
(invariant 7).

Business Solutions (Luka Vidmar, b-s.si) added three writable fields to the
`dataitems` endpoint of Dentalia's Business Central. This is the design for
filling them from our registry.

```
pteValidDeclarationOfConformity   boolean
pteValidCECertificate             boolean
pteWarehouseURL                   text[250]
```

All three also appear on `allitems`, read-only there. **We write to `dataitems`
and never to `allitems`.**

Read first: [`2026-09-03-bc-api-integration.md`](../../2026-09-03-bc-api-integration.md)
for what the API is and what it costs. Two of its claims are stale and this
document corrects them; see § 8.

---

## 1. What each field means

Ruled by Denis, 2026-09-07: *"valid DoC means the article, the item has a valid
DoC describing the item itself or a group of items the item belongs to. Having
a relation to manufacturer alone is not enough — must cover the item. And be
within valid datetime."*

That maps onto the schema without inventing a concept: `document.coverage_scope`
is already the closed pair `group | manufacturer` (migration 022).

### `pteValidDeclarationOfConformity`

True iff the item has at least one `item_document` row where **all** hold:

| Condition | Column |
|---|---|
| the document is a declaration | `document.type = 'DoC'` |
| the document is production | `document.status = 'production'` |
| the link is production | `item_document.status = 'production'` |
| it covers the article or its group, not the manufacturer | `document.coverage_scope = 'group'` |
| it has not lapsed on a legal basis | see below |

**Lapse is basis-sensitive, and this is the subtle half.**
`document_effective_expiry` (migration 027) returns `basis` as one of four
values, and they do not mean the same thing:

| `basis` | Meaning | Effect on the boolean |
|---|---|---|
| `stated` | the document's own `validity_to` | **false** once passed |
| `inherited` | the expiry of the notified-body certificate the DoC cites; MDR Art. 56 caps that at five years | **false** once passed — the declaration's basis is gone |
| `staleness` | Dentalia's own five-year review horizon on a DoC that states no expiry | **true** — a house review rule is not a legal fact about the article |
| `NULL` | no date of any kind | **true** — MDR Annex IV requires only an issue date on a declaration |

The `staleness` row is a deliberate divergence from the reviewer card, ruled by
Denis 2026-09-07. Migration 027 already says it in words: *"a review horizon,
not a legal expiry"*. Reporting a house rule as a breach, in the client's own
ERP, is the one error this field cannot afford. 139 declarations lapse on that
basis (measured 2026-08-24) and every one of them writes `true`.

### `pteValidCECertificate`

Same rule, with two differences.

**`type = 'EC'` only, never `ISO`.** ISO 13485 is a voluntary quality-standard
certificate, not an MDR conformity certificate. It is also the document type
carrying the single largest concentration in the registry — one certificate over
2.567 CARL MARTIN articles — so conflating the two would put the exact trap
`app/compliance.py` was written to prevent into the client's ERP.

**Adverse EUDAMED status falsifies it.** A notified body can suspend, restrict,
withdraw or cancel a certificate without its expiry date moving. An unexpired but
suspended certificate reading `true` in BC is the worst failure this field has.

Why `coverage_scope = 'group'` is right here too, despite the intuition that a
certificate is a manufacturer-level thing: **MDR Annex XII** requires every
notified-body certificate to identify the devices it covers. A technical
documentation assessment or type-examination certificate names the device — name,
model, type, Basic UDI-DI, risk class. A quality management system certificate
(Annex IX Ch. I) names *device identification or device groups*. In both cases
the notified body must be able to demonstrate on request which individual devices
a certificate covers. A CE certificate therefore always has a determinate device
scope; the QMS one simply prints it as groups. Our `coverage_scope = 'group'`
is that distinction.

**Expected effect, stated honestly:** this field reads `false` for almost every
item today, and that is a coverage gap rather than a modelling error. Of the 43
`EC`/`ISO` documents carrying a `cert_number`, **34 have no production item link
at all** (measured 2026-09-04); only ~9 are reachable by any of this. Deepening the EC corpus is what
moves this number, not a change to the rule.

### `pteWarehouseURL`

```
https://api.cw.dentalia.si/item/{item_ref}?k={cfg.web.bc_link_key}
```

Fits 250 characters with room. The shape is the 2026-08-25 item-document access
spec's, and **the route is live** — `web/item_link.py:96`, plus a
`/item/{item_ref}/documents.zip` sibling, both landed 2026-08-25.
BC builds hyperlinks by string concatenation and cannot compute
anything, which is why the key is in the URL and the access policy is in
`web/access.py` (`bc` caller class).

### Never write an unprocessed item

Ruled by Denis 2026-09-07. A boolean has no *not checked yet*, and 3.987 of
4.265 flagged devices have never been searched (measured 2026-09-04). Writing `false` for those would
assert "we looked and there is none" about an item nobody has looked at.

**Processed** = the item's group has at least one `discovery_log` row, **or** the
item holds any `item_document` row of any status. The second half matters: items
covered from the SFTP corpus were never touched by DISCOVER, and 86,8% of the
registry came that way. Unprocessed items are skipped, counted, and reported —
never written. BC keeps its own default `false`, which is at least not our claim.

---

## 2. Where the rule lives

**`app/bc_fields.py`** — a new module. One pure function, an item's registry rows
in, the three values out.

It does **not** call `compliance.cell_state`, and the divergence is the reason.
`cell_state` answers *what does a reviewer need to look at*; this answers *what do
we assert to the client's ERP*. They disagree on two states by design:

| `cell_state` | reviewer card | BC boolean |
|---|---|---|
| `expiring` (valid today, lapses within 30 days) | amber, needs attention | **true** — it is valid today |
| `review-due` (staleness horizon passed) | amber, due a look | **true** — not a legal expiry |

Sharing the function would force one of those two consumers to be wrong. The
module carries a comment naming this and pointing at `app/compliance.py`, so the
next reader does not "fix" the duplication.

`app/compliance.py`'s own doctrine is left alone: `ROW_EC` stays in
`EVIDENCE_ROWS`, unscored, because MDR Art. 14(2) makes no notified-body
certificate a distributor's duty and ECJ C-10/24 narrowed that duty further.
Reporting what we hold to BC is not the same act as scoring Dentalia against it.

---

## 3. The job type

**`bc.push`** — new, so per invariant 7 this is a PRD §0 change plus a migration
(`ALTER TYPE job_type ADD VALUE 'bc.push'`), never a string.

It gets the same contract row every other tag has (PRD §0 shape), because a tag
without one is a string:

| | |
|---|---|
| **Produced by** | web UI (item button · bulk apply) · SCHEDULER (drift cron) |
| **Logic** | For each `item_ref`: compute the three values from the registry → read the last sent values from `bc_push_log` → PATCH `dataitems` with **only** the fields that differ → write one ledger row per field sent. Unprocessed items are skipped and counted, never written. |
| **Emits** | nothing. `bc.push` is a leaf, like `report.weekly`. |
| **Writes** | `bc_push_log` only. Never `document`, `item_document` or `evidence` — invariant 1 is untouched because this stage reads the registry and writes an external system. |
| **Failure path** | A PATCH failure fails that item's row, the job takes the existing backoff, and a dead job raises the ordinary dead-letter alert. The registry is never touched, so a failed push loses nothing but a retry. |
| **AI** | None. |

Payload: `{run_id, item_refs: [...]}`, batches of ~200.

**dedupe_key**, in the shape PRD §0's table already uses:

| producer | dedupe_key |
|---|---|
| web UI (item button) | `bc.push:item:{item_ref}:{date}` — the `discover:refetch:{group_id}:{date}` precedent for an admin re-run |
| web UI (bulk apply) | `bc.push:bulk:{run_id}:{batch_no}` |
| SCHEDULER (drift cron) | `bc.push:drift:{period_key}:{batch_no}` |

`docs/dev/handlers.md` gains the tag in the same commit as the migration —
`tests/test_docs_sets.py::test_every_job_tag_is_in_the_handler_index` fails
otherwise, and it is right to.

**Idempotent by construction.** The handler recomputes every value from the
registry and PATCHes only fields that differ from the last value in the ledger.
A lost job, a re-run job and a duplicated job all cost the same: nothing. This
is what satisfies "any job whose loss loses work is a design bug" without a
resume protocol.

Emits: nothing. It is a leaf.

---

## 4. The ledger

Migration 062, `bc_push_log`:

```
item_ref, field, old_value, new_value, http_status, response, pushed_at, via_job
```

Two jobs at once:

1. **Invariant 10.** Writing into someone else's ERP without a self-contained
   record leaves "why does BC say true?" unanswerable.
2. **It is the diff source.** The cron asks the ledger what we last sent rather
   than reading BC back, so drift detection costs no API calls.

---

## 5. Two entry points

**Per item.** A button on the item page. It shows the three computed values and,
on confirm, **enqueues a `bc.push` job** — it does not write. The web process is
a job producer only; an outbound HTTP call inside a request handler would also
sit outside the domain lease and outside the retry ladder.

**Bulk, preview then apply.** The preview is a query — *N turning true, M turning
false, K unchanged and skipped, U unprocessed and skipped* — and apply recomputes
the same query before enqueueing. Deliberately **not** `import_spool`: that stores
uploaded bytes keyed by `upload_id` and does not fit, and a recomputed preview
cannot go stale between the two presses. The flow mirrors `/import`'s two-phase
shape so an operator meets something they already know.

**Drift.** A ninth cron, daily, config-gated off by default: one more perpetual
self-deferring `scheduler.tick` row seeded the way migration 055 seeds the other
eight, with its body in `app/scheduler.py`. It diffs computed-against-ledger and
enqueues `bc.push` only for items that changed. Without it BC goes stale the first
time a document lapses, and **86 production documents were already past expiry** (measured 2026-09-04) —
stale `true` is worse than never having written.

---

## 6. Failure containment

- **`bc.write_enabled` defaults false.** Nothing can write until it is turned on.
- Base URL and credentials in config, never in a payload (invariant 9).
- A PATCH failure fails that item and takes the existing backoff. It never
  touches the registry and never blocks a stage.
- Rate limiting through the existing domain lease, the same mechanism FETCH uses.
- No writeback path is reachable from a stage handler. GATE does not know this
  exists — coupling registry writes to b-s.si's availability was considered and
  rejected 2026-09-07.

---

## 7. Testing

Real Postgres, BC stubbed at the httpx boundary.

- **Table-driven over the rule**: every `basis` value × `coverage_scope` × document
  status × link status, with the two divergences from the reviewer card asserted
  by name so a future "simplification" onto `cell_state` fails loudly.
- **`inherited` expiry falsifies**, `staleness` does not. One test each, both
  citing migration 027.
- **`ISO` never satisfies `pteValidCECertificate`**, using a CARL MARTIN-shaped
  fixture.
- **Suspended certificate falsifies**, via the `cert_number` join, revision-blind.
- **Idempotency**: a second run PATCHes nothing.
- **Unprocessed items are skipped**, counted, and named in the job result.
- **Preview then apply** as a web test, including a registry change between the
  two presses.

---

## 8. Corrections to the 2026-09-03 BC document

Both found by re-checking on 2026-09-07, both in our favour.

1. **§2 says `/item/{item_ref}` is not built and the URL would 404.** It is built,
   and was already built when that was written — `web/item_link.py:96`, 2026-08-25.
   The grep behind that claim covered `web/app.py` only.
2. **§3.3 says manufacturers cannot be synced from this API at all.** b-s.si
   delivered `allmanufacturers` on 2026-09-07, which is exactly what that section
   asked for. Together with `pteManufCodePrimary` in the field inventory, §3.1's
   one unresolved key closes too.

---

## 9. Open, and not ours to close

- **Is `dataitems` filtered?** The correspondence with b-s.si describes it as a
  subset, without saying a subset of what. If it is filtered to medical devices,
  items we intend to write are simply absent and a bulk run skips them silently.
  **Ask b-s.si before the first bulk run**; the preview's skip count is the
  symptom to watch for.
- **External access is blocked.** Per b-s.si it works locally, outside is blocked,
  Dentalia's sysadmins must open it and `denwebnav` will likely become an IP.
  Everything here is codeable and testable against a stub without it; nothing is
  provable with it until that lands.
- **Scope.** Denis has confirmed the three writable fields exist to be used.
