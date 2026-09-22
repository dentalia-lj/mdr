# Vocabulary — every label the pipeline uses

One page for every closed vocabulary in the system: job tags, statuses, flags,
link bases, tiers, anomaly kinds. Written because these are spread across the
PRD, the handbook, the schema sketch and the code, and the cost of that showed
up on 2026-08-13 — a flag meaning "we have not ingested the cited certificate
yet" was gating production exactly as hard as a flag meaning "this evidence is
incomplete", and nothing on one page would have let those two sit side by side
looking equivalent.

**This page is descriptive, not normative.** On a conflict the PRD
(`dentalia-pipeline-contract-prd-v3.md`) wins on contracts and the handbook
(`dentalia-job-type-handbook.md`) wins on per-stage behaviour. What this page
adds is that every value is in ONE place, with what it means and what it does.

`tests/test_vocabulary_doc.py` asserts that every value below still exists in
the code or the database, and that every value in the code or database appears
below. A new flag or job tag fails that test until it is documented here.

---

## 1. The pipeline, in one line each

The topology **is** the enqueue graph — each handler ends by enqueueing the next
stage. There is no orchestrator.

```
ingest.run → resolve.group → discover.group → fetch.url → extract.doc → validate.doc → gate.candidate
                                   ↘ email.request        ↗ backfill.scan, email.poll enter here
                                   ↘ manual queue          ↖ fetch.url hash-dedupe re-enters at validate.doc
```

| tag | what it does | who enqueues it |
|---|---|---|
| `ingest.run` | Read the BC catalogue export into `item_mirror` | scheduler, web ingest form |
| `resolve.group` | Cluster items into `item_group` by manufacturer + name | INGEST |
| `discover.group` | Find candidate document URLs for a group | RESOLVE |
| `fetch.url` | Download and archive a document (httpx, Playwright fallback) | DISCOVER |
| `extract.doc` | T0 → T1 → T2 field extraction, writes `extraction_attempt` | FETCH, BACKFILL |
| `validate.doc` | Apply the rules, compute flags and links, emit a candidate | EXTRACT |
| `gate.candidate` | **The only writer of `document` / `item_document` / `evidence`** | VALIDATE |
| `gate.apply` | Apply a human decision (approve / reject / edit / bind-manufacturer) | review UI |
| `backfill.scan` | Ingest a local corpus folder, bypassing discovery and fetch | operator |
| `upload.ingest` | Ingest a document uploaded through the web UI | web upload form |
| `vendor.import` | Mirror a BC manufacturer master (`Proizvajalci.xlsx`) uploaded through the web UI into `vendor_master`; preview then apply, two jobs over one `import_inbox` row at `kind='vendors'`. Emits nothing | web import form |
| `email.request` | Ask a manufacturer for a document | DISCOVER, expiry scan |
| `email.poll` | Read the mailbox for replies | scheduler |
| `email.reminder` | Chase an unanswered request | scheduler |
| `eudamed.sync` | Reconcile against EUDAMED | scheduler |
| `eudamed.certregister` | Pull the whole EUDAMED certificate register, matched to canonical manufacturers locally | scheduler |
| `eudamed.sweep` | Walk one manufacturer's registered device catalogue | operator (release button); scheduler only marks a manufacturer due |
| `playbook.reonboard` | Re-derive a manufacturer playbook after repeated misses | failure monitor |
| `report.weekly` | Build the weekly KPI report | scheduler |
| `bc.push` | Write three compliance fields back into Business Central for a batch of items — a valid-declaration boolean, a valid-CE-certificate boolean, and the warehouse link. Sends only what changed since the last accepted write, never writes an item the pipeline has not processed, and is gated off by `bc.write_enabled` | web UI (item button, bulk apply), scheduler (drift) |
| `scheduler.tick` | One perpetual, self-deferring job per cron: runs that cron's tick, then `queue.defer`s itself. Never completes, so it never frees its dedupe key. `run_after` is a poll interval, not a fire time -- `scheduler_run` stays the sole authority on whether a period has fired | itself (seeded by migration 055, re-armed by the worker on start) |

The tag list is a **closed enum** (`job_type`). A new tag is a PRD change plus a
migration, never a string — invariant 7.

---

## 2. Job states

| `job_status` | meaning |
|---|---|
| `pending` | claimable |
| `running` | claimed by a worker |
| `done` | finished; **no longer blocks a re-enqueue of the same dedupe key** |
| `failed` | will retry with backoff |
| `dead` | exhausted `max_attempts`; visible on the dead-jobs board |

Dedupe is scoped to `pending` / `running` / `failed` only (invariant 8), so
re-emitting a completed job is always allowed — which is what makes the whole
queue regenerable from the registry.

| `job_priority` | meaning |
|---|---|
| `interactive` | a human is waiting (UI action) |
| `delta` | incremental, an update to something we hold |
| `sweep` | bulk backfill |

Priority is **work class, not urgency**. A backfill's gate job must not outrank
a human's ingest, which is why the breadth-first drain problem is fixed by stage
ranking rather than by promoting sweep jobs.

---

## 3. Document and link status

| `doc_status` | meaning |
|---|---|
| `staged` | written and archived, invisible to consumers, awaiting review |
| `production` | trusted and visible |
| `filed` | correct and complete, but covers no item in the catalogue — archived, evidenced, and in nobody's queue (migration 025). A re-delivery may still promote it to `production` when a re-extraction finds items; it may not fall back to `staged` |
| `rejected` | a human refused it. **Sticky against machines** since 2026-09-04, matching the link-level rule below: `_upsert_document` refuses every backward step (`staged < filed < production`, with `rejected` and `superseded` terminal), so no re-delivery can un-reject a document. It could before — measured live, a 67-document re-emission moved four, one of them out of a rejection a person had made, silently and with no audit row. `gate.apply reopen` is the only way back to `staged`, and it does **not** cascade the links back: each returns through `reopen-link` |
| `superseded` | replaced by a newer document in the same chain; still archived, never deleted. Invisible to consumers by default; a read-API caller can ask for the chain with `?include_superseded` (migration 070) |

**These are the STORED values, and they never change.** The UI shows a different
word for each of them — Published, Waiting for review, On file, Rejected,
Replaced — from the office UI redesign's word list (spec § 9), which lives in
`web/words.py` as `WORDS` and reaches templates as the Jinja filter `word`.
Nothing but the last step before a reader's eye is translated: every query,
payload, API response and migration in this document means the stored value.

`filed` is a disposition, not a judgement. A manufacturer publishes documents for
their whole product line and a distributor buys a slice of it, so a correctly
read document routinely covers nothing we sell: on the IVOCLAR backfill,
Dentalia stocked **6** of the 1.605 REF codes the staged documents named. Such a
document must not sit in `staged`, because `staged` means "a human should
confirm" and no human can confirm coverage of a product the company does not
sell — the queue would only ever be rejected through, hiding the genuine work.
Nothing is discarded (invariant 4): if the catalogue later gains the item, the
document is already held. Re-entry on that event is not built yet
(`[filed-relink]`).

Manufacturer-scope certificates (ISO 13485 / QMS) are **not** `filed`. They name
no item by design and have C4's path instead — one-time human binding, then
derivation at `mfr-scope`. Waiting on a decision is not the same as having none
to make.

| `link_status` | meaning |
|---|---|
| `staged` | proposed, not published. Either the document is not trusted yet, or the link's own basis is capped (§4) and needs a person |
| `production` | live coverage. Consumers require BOTH this and `document.status = 'production'` |
| `rejected` | a named person refused this link. **Sticky against machines** (C17): no candidate may rewrite it, and `gate.apply reopen-link` is the only way back to `staged` |
| `retracted` | the current extraction no longer supports it — machine-withdrawn, distinct from a human `rejected`, and deliberately NOT sticky: a later extraction that claims the link again revives it, and no human decision targets it |

Nothing is ever deleted: supersession is append-only with a 10-year retention
(invariant 4).

---

## 4. `match_basis` — why we believe a document covers an item

The trust boundary. **Production-capable** bases established the manufacturer
*before* comparing any article number:

| basis | formed when |
|---|---|
| `ref-list` | the document's REF list contains the item's `mfr_ref` (supplier's number) |
| `ref-item` | it contains the item's `item_ref` (Dentalia's own number — client ruling C12) |
| `map-supplier` | the REF list came from the manufacturer's OWN article index rather than from the document body, and that list contains the item's number. Production-capable for the same structural reason `ref-list` is: the manufacturer is fixed by the source document before any article number is compared |
| `udi` | UDI match |
| `basic-udi-di` | Basic UDI-DI match |
| `mfr-scope` | a manufacturer-wide certificate bound to the manufacturer's items |
| `manual` | a named person said so. Written by exactly one thing in the system, `gate.apply confirm-link` (C17) — it is the only escape from the capped bases below, and the reason it is production-capable is that a person's name is on it |

**Capped bases** — structurally barred from `production` by a database
constraint (`item_document_trusted_basis_ck`, migrations 005 and 021), not by
handler logic:

| basis | why it is capped |
|---|---|
| `ref-catalogue` | number matched, but on the unscoped path — the manufacturer was never established, and bare article numbers collide across manufacturers |
| `name-family` | items grouped by name similarity |
| `fetch-context` | inferred from where the file was found |

This is invariant 3, and it is the one guard that must never be relaxed to
raise a coverage number.

**Approving a document does not lift the cap, and no amount of confidence
does.** A capped link leaves `staged` by one route only: a person ruling on
that link with `gate.apply confirm-link`, which re-bases it to `manual` (C17).
The sibling decisions are `reject-link` (the link is refused; the basis is kept
so the trail records how it was proposed) and `reopen-link` (the only way out
of `rejected` — machines are locked out of that status, see the `link_status`
table above). All three act on ONE `(doc_id, item_ref)` pair and none of them
touches the document.

---

## 5. VALIDATE flags — three classes

**Amended 2026-08-13 (Denis's ruling).** Before that, `production` required an
empty flag list, so a flag describing *context* gated as hard as one describing
a *defect*.

### Blocking — force `manual`, can never auto-write

| flag | meaning |
|---|---|
| `older-than-current` | an older document than the one in production for the same subject |
| `same-date-revision` | the same `validity_from` as the document in production for the same subject; no ordering exists, so a person picks the current one (ruled 2026-09-04) |
| `no-item-identifier` | no REF list and no UDI: nothing can link it to the catalogue |
| `evidence-page-missing` | a T1/T2 value with no page cite — invariant 2 |
| `date-insane` | dates that cannot both be true, or a year outside the sane range |
| `manufacturer-unresolved` | the extracted manufacturer matches no `item_group` |

### Capping — hold at `staged`, a reviewer settles it

| flag | meaning |
|---|---|
| `downgrade-uncomparable` | one of the two dates is null, so no comparison is possible |
| `expiry-on-certless-doc` | a DoC whose expiry date is not stated as one in its own evidence verbatim. **Name is historical** — until 2026-08-17 the rule asked whether the document cited a certificate, which both over- and under-fired (see `app/handlers/validate.py` rule 6); rename tracked as `[expiry-flag-name]` |
| `ref-catalogue` | matched on the unscoped path (see §4) |
| `ref-unscoped` | matched a group under a manufacturer we could not confirm |
| `multi-manufacturer-ref` | the REF list spans more than one manufacturer — usually bad catalogue data, e.g. a distributor code recorded as the maker |
| `device-enumeration` | a QMS/QA-system certificate whose own stored text enumerates the devices it covers (model/code annex, per-device risk-class lines, or a "valid only for the above mentioned" clause). Raised by VALIDATE on `mfr-binding` candidates only; GATE's C16 machine bind refuses on it, so the document stages with one review task instead of fanning out manufacturer-wide (Denis ruling 2026-08-24 — doc 310, Kiwa Cermet MED 31385, enumerated three device types and was bound to 356 GC items). `gate.apply bind-manufacturer` (a person) is unaffected |
| `not-a-device-document` | the document is not evidence about a medical device at all: no MDR/MDD citation and not a QMS certificate — a machinery (2006/42/EC), cosmetics (1223/2009), low-voltage or EMC declaration. Raised by GATE (`_is_md_document`) on **every** candidate since 2026-09-03; before that the check ran on the mfr-scope binding path only, so a non-device declaration whose REF list happened to overlap the catalogue reached production on an article-level basis. Measured before the fix: 4 production REF links across 3 documents (27, 50, 82) of 1.945. Capping rather than blocking — a person can judge what the tuple cannot |

### Informational — recorded and displayed, **do not gate**

| flag | meaning |
|---|---|
| `no-ref-overlap` | the REF list matched no catalogue item. There is nothing to link, which is not a defect in the document |
| `multi-group-match` | the REF list spans several of our groups. Since 2026-08-13 every matching member is linked, so nothing is arbitrary |
| `cert-unresolved` | the cited certificate is not in the registry **yet**; self-corrects once it is ingested |
| `auto-superseded` | fast-path signal that this candidate files itself into the supersession chain |
| `ref-list-possibly-truncated` | a T1/T2 `ref_list` came back at exactly the prompt cap (`tiers.REF_LIST_PROMPT_CAP`), so it may be a partial reading of a longer list. It **reports**, it never prunes: the list is stored and linked in full, and a T0 or T3 list of the same length is not flagged because neither is subject to the prompt cap |
| `iso-without-standard-number` | `type` is `ISO` but no ISO standard number (13485 or 9001) appears in any of the document's evidence verbatims. It **reports**, it never re-types: the document keeps the type it was given and a reviewer sees that nothing supports it. Extends the 2026-08-20 ruling that `iso 13485` is the ONLY ISO signal from T0, where it was enforced, to whatever tier produced the type — doc 967 is an MDR Annex IX quality-management certificate T1 typed `ISO`. Deliberately **not** a `type`-versus-`regulation` check: a real ISO 13485 certificate may cite MDR in its scope, so that rule would fire on correct documents |
| `date-label-not-adjacent` | T0 found a date label, refused to pair it with the date that followed because real content sat between them, and then read no value for that field by any other route. It **reports**, it caps nothing and blocks nothing: it is a note about our parser, not about the document. Records that the page has a layout T0 cannot read — the signal that would justify authoring a `t0_layout` template, and the cost signal too, since the field either escalates to a paid tier or goes unread. A refusal a LATER LABEL then satisfied is never flagged (doc 317, bilingual, English label a bare heading and German label carrying the value): that is the fall-through working as designed, and a flag that fires on correct behaviour teaches people to ignore flags. Unlike every other flag here it cannot be computed from the stored fields — a refusal is a non-event — so `extract_dates` records it under `t0_templates.DATE_LABEL_REFUSED_KEY` and `tiers.integrity_flags` only reads it |

**An unrecognised flag gates.** `gate.py` `_gates_disposition` tests membership
of the *informational* set, so a flag a future rule adds and nobody classifies
behaves conservatively instead of silently ceasing to gate.

---

## 6. Disposition — what GATE decided

| disposition | document lands as |
|---|---|
| `production` | `production` — needs `score ≥ cfg.gate.high`, `ref_gate`, and no blocking or capping flag |
| `staged` | `staged` — `score ≥ cfg.gate.med` and no blocking flag |
| `manual` | `staged`, plus a `gate-manual` task for a human |
| `superseded` | `superseded` — the auto-file fast path, under its own three-condition guard |

Note `ref_gate` is a separate condition from the flags: a document that matched
no catalogue item has `ref_gate = False` and stays staged however clean its
flags are. That is deliberate — a document linking nothing is not "production".

---

## 7. Extraction vocabularies

| tier | what it is | cost |
|---|---|---|
| `T0` | deterministic: regex, pdfplumber tables, playbook templates | free |
| `T1` | text LLM (`models.t1`) | cheap |
| `T2` | vision LLM (`models.t2`), for scans | expensive |
| `T3` | a human, via `gate.apply` edits | — |

| `doc_class` | effect |
|---|---|
| `compliance-doc` | proceed with extraction |
| `business-doc` | short-circuit, no LLM (invoices, delivery notes) |
| `msds` | short-circuit, no LLM (safety data sheets) |
| `unknown` | proceed — a missed classification costs money, a wrong skip costs a document |

| `document.type` | `DoC` · `EC` · `ISO` · `IFU` · `SPP` · `other` |
|---|---|
| **`regulation`** | `MDR` · `MDD` · `n.a.` — `n.a.` is a real value meaning "not issued under a device regulation" (an ISO 13485 certificate), never "not found" |
| **`coverage_scope`** | `group` (covers a set of products) · `manufacturer` (company-wide QMS/ISO). There is no per-item value; it was retired and is barred by a CHECK constraint |

Extraction TARGET fields: `type`, `regulation`, `validity_from`, `validity_to`,
`coverage_scope`, `ref_list`, `basic_udi_di`, `referenced_docs`, `cert_number`,
`manufacturer`, `stated_class`.

**`stated_class`** is the MDR risk class the document states about *itself*
(Annex VIII): `I` · `Is` · `Im` · `Ir` · `IIa` · `IIb` · `III`. It is **not**
what class an item is — BC's `item_mirror.product_class` is the source of truth
for that, and nothing in the pipeline writes it from a document. This field
exists to cross-check BC and to fill its blanks under human review, via the
`item_class_check` view on `/data-quality`. T0-only: it appears in no
`_ESCALATE_*` set, so it is read when a document happens to print it and never
costs an LLM call. Null is ordinary — the document said nothing, or it named
several classes at once (a pre-printed form listing the whole ladder) and T0
abstained rather than pick one.

**Date fields.** `validity_from` is the date the document takes effect: an
explicit validity-period start if stated, otherwise the issue or signing date,
including an unlabelled place-and-date line ("Leuven, 12/02/2026"). A signing
date is **never** `validity_to`. `validity_to` requires an explicit expiry
phrase; a wrong expiry reads as an expired certificate and raises a false
compliance alarm.

---

## 8. Manual queue and anomalies

| `manual_kind` | raised when |
|---|---|
| `discovery-dead-end` | DISCOVER found nothing for a group |
| `gate-manual` | GATE's disposition was `manual` |
| `dead-job-followup` | a job exhausted its retries (`queue.fail`, every tag) |

`manual_status`: `open` · `resolved`. Resolution enqueues a job; a handler marks
the task resolved — never a queue bypass.

Who closes each kind differs, and the difference is deliberate:

| kind | resolved by |
|---|---|
| `discovery-dead-end` | `POST /manual/{id}/resolve` (Resolve on `/manual`, **Search again** on `/missing`), which enqueues `discover.group` that closes the task if a rung hits; or an upload from either page, which `upload.ingest` closes |
| `gate-manual` | `gate.apply`, by `doc_id` (the Staging tab carries the evidence) |
| `dead-job-followup` | `queue.finish`, when a later job with the SAME dedupe key succeeds |

The last one is not closed by the `/dead` re-run button on purpose. Re-queueing
is not success — a job that dies again would have had its alarm cleared by the
very attempt to fix it, which is the one case the alarm exists for. It is also
the worker that writes it: the web role holds no write grant on `manual_task`,
so the producer boundary is enforced by the grant matrix rather than by
convention.

**Anomaly kinds** (`app/results.py`, closed vocabulary, kept in sync with
migration 019) are counted on a job's result and surfaced on `/data-quality`:
`md_class_blank`, `mfr_ref_missing`, `mfr_ref_prose`, `manufacturer_code_blank`,
`reclassified_non_md`, `cert_reference_unresolved`, `no_text_layer`,
`t0_ref_template_miss`, `basic_udi_check_failed`.

`code_shape_unexpected` and `blocked_item` were removed on 2026-08-19: both had
no producer and no rule in any normative document to write one against, and
`blocked_item` waits on an unanswered client question about `Blokirano = 1`.
A closed vocabulary is a promise that each member can be reached.

An unrecognised kind raises rather than being silently accepted.

`basic_udi_check_failed` is the one that needs no external source to raise.
Both issuing agencies end a Basic UDI-DI with a MOD 1021,32 check pair, so
`app/extract/udi.py` verifies it offline, and the check set deliberately omits
`0`, `1`, `O` and `I` — the characters OCR confuses. Its `subject` is the
offending value, so `seen_count` is how many documents repeat the same misread
string. It exists because the failure is otherwise unattributable: a Basic
UDI-DI that no registry resolves looks the same whether the manufacturer never
registered it or our extraction corrupted it. Three of our 352 extracted codes
fail; 306 of 306 codes EUDAMED publishes pass.

`t0_ref_template_miss` is the one keyed to a **playbook** rather than to a
document: a REF template matched the document and still parsed no codes, so our
own parser broke. Its `subject` is the playbook slug and `seen_count` is how
many documents that break has cost, which is the number that decides whether the
template is worth re-authoring. Without it a broken template is invisible: the
document falls through to the LLM, which costs money and returns a capped sample
where T0 enumerates.

---

## 9. Where each vocabulary actually lives

| vocabulary | source of truth |
|---|---|
| job tags, statuses, priorities | Postgres enums (`job_type`, `job_status`, `job_priority`) |
| document / link status | Postgres enums (`doc_status`, `link_status`) |
| manual kinds and status | Postgres enums (`manual_kind`, `manual_status`) |
| `coverage_scope` | CHECK constraint, migration 022 |
| trusted vs capped `match_basis` | CHECK constraint, migrations 005 and 021 |
| flag classes | `app/handlers/gate.py` — `BLOCKING_FLAGS`, `CAPPING_FLAGS`, `INFORMATIONAL_FLAGS` |
| flags themselves | `app/handlers/validate.py`, emitted inline — except `ref-list-possibly-truncated` and `iso-without-standard-number`, which VALIDATE takes from `app/extract/tiers.py` `integrity_flags` because both are properties of how the extraction arrived |
| anomaly kinds | `app/results.py` `ANOMALY_KINDS` |
| extraction TARGET | `app/extract/tiers.py` `TARGET` |
