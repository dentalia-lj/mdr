# Dentalia Pipeline — Job Type Handbook

v2 — 2026-07-05 · Companion to `dentalia-schema-sketch.md` (v2) and `dentalia-pipeline-contract-prd-v3.md`. Supersedes v1.
Format per job type: input payload variants → handler pseudo-code → outputs (writes + next jobs). Examples use real-shaped data (Ivoclar, Euronda) — values illustrative.

**Changelog v1 → v2 (sync to PRD v3):** `mfr_ref` through ingest/resolve/validate (C1) · fetch hash-dedupe emits `validate.doc` (C3) · batch_ref side-table protocol in extract (C9) · validate rules: scoped REF gate, mfr-scope pre-rule, C6 supersession identity, C7 null-date · gate: two-level disposition + §7b manufacturer-binding flow (C4/C5) · `gate.apply` gains `bind-manufacturer`.

**Sync 2026-07-31 (audit):** dedupe conventions ratified into PRD §0 (all 16 live shapes; DISCOVER email key → `email.request:group:{group_id}`) · gate link provenance immutable at production (§7 upsert) · reject → link cascade + `link-rejected` audit (§8) · `production-write` audited on human promote paths (§8) · mfr-binding resolves through `manufacturer_alias` (§8) · DISCOVER email rung producer-gated (`discovery.email_rung_enabled`, §3).
**Sync 2026-07-31 (Denis rulings — [gate-c6]/[gate-evidence]/[evidence-page]/[storage-default] closed):** C6 supersession persistence shipped — `document.supersedes` (015) sticky-staged, applied at production by either `gate.candidate` (machine) or `gate.apply approve` (human), C6 re-checked at write time (§7/§8) · evidence scoped per `extract_rev` (014), idempotency guard (doc_id, field, extract_rev) so a re-extraction never leaves a changed field pointing at stale evidence (§7) · evidence `page` required for T1/T2 only (§7) · `adapters.storage` default `local` (was `gdrive`, S1.4 note).
**Sync 2026-07-24 (drift-check):** ingest report shape matched to S1.1 code (tri-state `md_flag` counters, `skipped` count + bounded sample) · `email.request` dual payload shapes + disjoint dedupe namespaces (§0 row 12) · `resolve.group` `force_new_group` (S1.6 start-new-group) · `singleton` membership basis ratified · fetch outcome table gains `lease-wait` + `recency-skip-linked` rows (S1.4) · C3 fetch-context fallback links now implemented in VALIDATE (was doc-only).
**Sync 2026-08-26 (vendor.import, slice B):** new §1b — the BC manufacturer master upload, off the pipeline spine, writes `vendor_master` only and emits nothing (PRD §1b).
**Sync 2026-08-27 (EUDAMED S2.3 stage 2):** new §9b/§9c — `eudamed.certregister` (whole-register pull, scheduler cron, monthly, config-gated) and `eudamed.sweep` (per-manufacturer device catalogue; no scheduler and no handler ever enqueues it, the operator's release button is its sole producer). §0 table gains rows 17-18. Both write side tables only (`eudamed_certificate`, `manufacturer_srn`, `eudamed_mirror`, `eudamed_sweep_state`) and emit nothing — leaves, per PRD §7b. EUDAMED holds zero declarations of conformity; nothing here is evidence.

---

## 0. Overview — every job type, in and out

| # | queue tag | in (payload) | reads | writes | emits next |
|---|---|---|---|---|---|
| 1 | `ingest.run` | `{source, ref, catalogue}` | export file / BC OData | item_mirror | `resolve.group` ×N (changed items) |
| 2 | `resolve.group` | `{item_ref}` | item_mirror, manufacturer_alias, item_group | item_group(+member, incl. mfr_ref) | `discover.group` (if group lacks current doc) or staging suggestion |
| 3 | `discover.group` | `{group_id, ignore_recency?}` | fetch_log, eudamed_mirror, manufacturer, discovery config | discovery_log | `fetch.url` ×k \| **`validate.doc` (recency C3 — 2026-08-24)** \| `email.request` \| manual entry |
| 4 | `fetch.url` | `{url, domain, group_id, ignore_recency?}` | fetch_log, domain_lease | fetch_log, archive file | `extract.doc` (new hash) \| **`validate.doc` (hash-dedupe — C3)** \| nothing (304) |
| 5 | `backfill.scan` | `{drive_folder}` | Drive corpus | fetch_log, archive refs | `extract.doc` ×N |
| 6 | `email.poll` | `{mailbox, since}` | mailbox, email_poll_log (reprocess guard), extraction_attempt (seen-hash) | fetch_log, archive files, email_poll_log; renewal_request *(outbound reply-advance, next slice)* | `extract.doc` ×attachments *(`validate.doc` on a seen hash with a requesting group — dormant inbound, `group_id=None`)* |
| 7 | `extract.doc` | `{archive_url, content_hash, group_id}` | archive file, batch_ref | extraction_attempt, batch_ref | `validate.doc` |
| 8 | `validate.doc` | `{content_hash, extract_rev, group_id, archive_url?}` *(`archive_url` carried through to `gate.candidate`, not consumed; C3 producers attach it from `document` when held — 2026-08-24)* | extraction_attempt, item_group_refs, document | — (stateless) | `gate.candidate` |
| 9 | `gate.candidate` | write candidate | evidence bundle | **document, item_document, evidence, audit_log** | — (or staging/manual entry) |
| 10 | `gate.apply` | `{doc_id, decision, decided_by}`; C17 link decisions add `item_ref`; optional `note` (2026-09-11) | staged rows | same as 9 + audit | possibly `discover.group` (on reject+retry) |
| 11 | `eudamed.sync` | `{}` (cron) | EUDAMED bulk export | eudamed_mirror | — |
| 12 | `email.request` (P2) | `{doc_id}` renewal (SCHEDULER, dedupe **`email.request:mfr:{canonical_manufacturer}:{period_key}`** — changed 2026-08-20 by the client's cadence rule, one mail per manufacturer per week/fortnight; see spec §7.2. Was `email.request:doc:{doc_id}`, which measured out at 52 mails to IVOCLAR for a single expiry date) **or** `{group_id, manufacturer, contacts, reason}` discovery-exhausted (DISCOVER S1.3, dedupe `email.request:group:{group_id}` — `group:` namespace added 2026-07-31, symmetric with SCHEDULER's `doc:`) — consumer (S2.4) branches on payload shape. Both producers config-gated **off** until S2.4 exists (`discovery.email_rung_enabled` / `scheduler.expiry_email_enabled`) | document, manufacturer | renewal_request, email draft | `email.reminder` (delayed) |
| 13 | `email.reminder` (P2) | `{request_id}` | renewal_request | renewal_request | escalation entry |
| 14 | `playbook.reonboard` (P2) | `{manufacturer_id, reason}` | discovery_log stats | notification | (human/agent workflow) |
| 15 | `report.weekly` | `{period_key}` (cron, S1.5) | document, job | — | — |
| 16 | `upload.ingest` | `{upload_id, manual_task_id?}` (web `/upload`, dedupe `upload:{upload_id}`) | upload_inbox, item_group, extraction_attempt | fetch_log, archive file, upload_inbox (delete), manual_task (worker-side dead-end resolve) | `extract.doc` (new hash) \| `validate.doc` (seen hash + group — C3-style link) |
| 17 | `eudamed.certregister` | `{}` (scheduler cron, monthly, config-gated off) | EUDAMED certificate register (16 calls), manufacturer_alias, manufacturer | eudamed_certificate, manufacturer_srn, manufacturer (upsert) | — (leaf; never emits) |
| 18 | `eudamed.sweep` | `{manufacturer: canonical_name}` (no scheduler, no handler enqueues it — the operator's release button is its sole producer) | trusted_manufacturer_srn, manufacturer (srn_probed_at), item_mirror + item_group(_member) + manufacturer_alias (article-probe fallback candidates) | eudamed_mirror, eudamed_sweep_state, manufacturer_srn (probe bootstrap only) | — (leaf; never emits) |
| 19 | `scheduler.tick` | `{cron}` (one perpetual job per cron, dedupe `scheduler.tick:{cron}`) | scheduler_run | scheduler_run | whatever the named cron emits — never completes, self-defers via `queue.defer` |

---

## 1. `ingest.run`

### Variant A — CSV/Excel export (v1)

```json
{ "source": "csv",
  "ref": "/imports/2026-07/bc_export_LJ_2026-07-01.xlsx",
  "catalogue": "LJ" }
```

### Variant B — BC OData (v2)

```json
{ "source": "bc_odata",
  "ref": { "company": "Dentalia_SI",
           "delta_since": "2026-06-01T00:00:00Z" },
  "catalogue": "LJ" }
```

Same handler, different adapter. **Contract: both variants produce identical normalized rows** — nothing downstream may detect the source.

**`mfr_ref` (C1):** a logical field. The adapter populates it from whichever BC column actually holds the manufacturer article number — `item_ref` itself, the vendor item no. / Item Vendor Catalog field, or per-supplier mapping config if mixed. Downstream never reasons about its origin. Null `mfr_ref` on an MD item is **not skipped** — counted in `missing_mfr_ref` on the job result (bounds achievable AC1).

### Pseudo-code

```python
def handle_ingest_run(job):
    adapter = make_source_adapter(job.payload)        # CsvExportAdapter | BcApiAdapter
    rows = adapter.read()                             # normalized: item_ref, name,
                                                      # manufacturer_raw, mfr_ref,
                                                      # md_flag, product_class, udi, catalogue
    report = {"seen": 0, "changed": 0, "unchanged": 0, "non_md": 0,
              "reclassified_non_md": 0, "md_unknown": 0,
              "missing_mfr_ref": 0, "skipped": 0, "skipped_sample": []}
    for row in rows:
        report["seen"] += 1
        if not row.item_ref:
            report["skipped"] += 1                         # count, never silent;
            report["skipped_sample"].append(row.raw)       # bounded sample (≤50)
            continue
        if row.md_flag is False:
            report["non_md"] += 1; continue                # explicit NI MP dropped
        if row.md_flag is None:
            report["md_unknown"] += 1                      # tri-state (S1.1): blank class;
            if not cfg.ingest.process_md_unknown: continue # default processes them
        if row.mfr_ref is None:
            report["missing_mfr_ref"] += 1                 # counted, not dropped (C1)
        current = db.item_mirror.get(row.item_ref)
        if current is None or current.differs_from(row):
            rev = db.item_mirror.upsert(row)               # bumps mirror_rev
            enqueue("resolve.group",
                    payload={"item_ref": row.item_ref},
                    dedupe_key=f"resolve:{row.item_ref}:{rev}",
                    priority=job.priority)                 # sweep or delta, inherited
            report["changed"] += 1
    job.finish(result=report)                              # skipped + missing_mfr_ref visible
```

### Output

- `item_mirror` upserted; e.g. 16,214 seen / 312 changed / 3 skipped / 41 missing_mfr_ref. (`skipped` is a **count** + bounded `skipped_sample`; an already-mirrored item BC declassifies to `NI MP` is upserted to `md_flag=FALSE` and counted `reclassified_non_md`, never re-emitted — S1.1.)
- 312 × next job:

```json
{ "type": "resolve.group", "payload": {"item_ref": "IT-88412"},
  "dedupe_key": "resolve:IT-88412:57", "priority": "delta" }
```

---

## 1b. `vendor.import`

A BC manufacturer master handed to us through the web `/import` form (`POST /import/vendors`, then `POST /import/{job_id}/apply`) — never the scheduler; an operator uploads the file. The browser counterpart to `python -m app.cli vendor-master`; both go through `app/vendor_master.py` so the two paths cannot drift. Off the pipeline spine entirely: nothing here reads or writes `item_mirror` / `item_group` / the registry, and nothing is enqueued afterward (see Emits, below).

### Preview (`dry_run: true`)

```json
{ "upload_id": 501,
  "filename": "Proizvajalci.xlsx",
  "dry_run": true,
  "allow_renames": false }
```

dedupe_key: `vendor.import:upload:501:preview`

### Apply (`dry_run: false`)

```json
{ "upload_id": 501,
  "filename": "Proizvajalci.xlsx",
  "dry_run": false,
  "allow_renames": false,
  "preview_job_id": 9821 }
```

dedupe_key: `vendor.import:upload:501:apply`

Flat, not nested under `ref` — that nesting belongs to `ingest.run`. `preview_job_id` is set on the apply only; the handler itself never reads it, it only lets the confirm screen show previewed-against-applied.

**Two-phase spool, unlike `upload.ingest`.** `app/import_spool.py` backs one `import_inbox` row at `kind='vendors'` across BOTH jobs: `read()` returns the bytes and LEAVES the row, `consume()` deletes it. The preview job calls only `read`; the apply calls `read` again on the same row, then `consume` after a successful write. `upload_inbox` — the spool `upload.ingest` reads — is single-phase, where "the row is gone" already means "processed"; that is why the vendor upload gets its own table rather than reusing it.

### Pseudo-code

```python
def handle_vendor_import(job):
    p = job.payload
    dry_run, allow_renames = bool(p.get("dry_run")), bool(p.get("allow_renames"))

    spool = import_spool.read(p["upload_id"])        # raises SpoolMissing if gone —
    if spool.kind != "vendors":                       # never a silent 0-row "success"
        raise ValueError("wrong kind")                 # dead-letters: failed -> retry -> dead

    rows = vendor_master.read_bytes(spool.content, spool.filename)   # Šifra/Ime -> VendorRow
    d = vendor_master.diff(rows)                       # read-only: added/renamed/disappeared/unchanged

    report = {"seen": len(rows), "added": len(d.added),
              "renamed": [list(t) for t in d.renamed],      # [code, old_name, new_name] triples
              "disappeared": list(d.disappeared),            # codes, not counts
              "unchanged": d.unchanged,
              "dry_run": dry_run, "allow_renames": allow_renames,
              "written": False}
    if dry_run:
        job.finish(result=report); return              # spool untouched either way

    try:
        vendor_master.apply(rows, batch=p["filename"], allow_renames=allow_renames)
    except vendor_master.RenameRefused as exc:
        report["outcome"] = "rename-refused"             # caught, not raised — an expected outcome,
        report["error"] = str(exc)                        # not a failure
        job.finish(result=report); return                 # spool left intact; nothing written

    import_spool.consume(p["upload_id"])
    report["written"] = True
    report["spool_gc"] = import_spool.gc()                # opportunistic, not a scheduler tick
    job.finish(result=report)
```

### Output

Measured against the delivered `Proizvajalci.xlsx` (390 rows, of which exactly one carries a code with no name — kept, not dropped: a nameless code is still a real code that items point at). A fresh import against an empty `vendor_master`:

```json
{ "seen": 390, "added": 390, "renamed": [], "disappeared": [],
  "unchanged": 0, "dry_run": false, "allow_renames": false,
  "written": true, "spool_gc": 0 }
```

A later re-import of the identical file: `added: 0, unchanged: 390`. If BC has since renamed one code and dropped another (illustrative, not measured):

```json
{ "seen": 389, "added": 0,
  "renamed": [["045", "EURONDA SPA", "EURONDA S.P.A."]],
  "disappeared": ["277"], "unchanged": 388,
  "dry_run": false, "allow_renames": true, "written": true, "spool_gc": 0 }
```

`renamed` carries `[code, old_name, new_name]` triples and `disappeared` carries codes, not counts — the point of the preview is that the operator reads WHICH codes move and to what, not just how many. No `anomalies` key is ever present (not even `[]`): `app/workers/runner.py` gates `record_anomalies` on `result.get("anomalies")`, and this handler has none to report.

**A disappeared code is never deleted.** `277`'s row stays in `vendor_master` with a stale `import_batch`; it is a fact to report, and deleting it would strand any `manufacturer_alias` already derived from it.

### Failure paths

**Wrong `kind`.** A spool row at `kind='items'` (an item export, not a vendor one) raises rather than parsing — it has neither BC column this reader needs, so parsing it would import zero manufacturers and report success. `failed` → retry → `dead`, same as any unparseable INGEST file.

**Missing spool row.** `import_spool.read` raises `SpoolMissing` rather than returning nothing — every caller needs the bytes to do any work, so a retry after a committed `consume()` (or after the row ages out past `SPOOL_RETENTION_DAYS` = 7) is an actionable dead job, not a silent zero-row success.

**Rename refused — not a failure.** If the diff contains a rename and `allow_renames` is false, `vendor_master.apply` raises `RenameRefused` before writing anything. The handler catches it: the job finishes normally with `outcome: "rename-refused"`, `written: false`, and `error` naming the offending codes; the spool row is left in place so the same apply can be re-run once the box is ticked. Why guarded: `item_group.canonical_manufacturer` is effectively write-once, because RESOLVE's ladder short-circuits on `_existing_link` and never re-derives it for an item already grouped — so re-pointing a code whose old name is already baked into existing groups splits that manufacturer with no repair path. The refusal is all-or-nothing: a genuinely new code in the same file does not slip through alongside a refused rename.

### Emits nothing

No downstream job, ever — not even on a clean apply. A vendor import that adds or renames codes leaves `manufacturer_alias` stale, and the tempting move is to call `playbooks sync` here. It does not: `sync` reports `orphaned_groups` — `item_group` rows still carrying a `canonical_manufacturer` this import just re-pointed away from, which `sync` does NOT retro-fix — so a non-zero count is work a human must act on, and running it as a side effect would produce that count where nobody reads it. `sync` also raises `PlaybookConflict` on an authoring error that has nothing to do with the vendor file, which would dead-letter an otherwise-clean import. The confirm screen names what is now stale and the command to run by hand.

### Writes

`vendor_master` only — never `document` / `item_document` / `evidence` (invariant 1). `dentalia_api` holds `SELECT` on `vendor_master` and no write grant, so the web process structurally cannot do this itself; the worker does, the same separation every GATE write relies on. AI: none (invariant 12).

---

## 2. `resolve.group`

### Input

```json
{ "item_ref": "IT-88412" }
```

item_mirror row behind it: `name: "Tetric PowerFill A2 20×0.2g", manufacturer_raw: "Ivoclar Vivadent AG", mfr_ref: "613289", udi: null, product_class: "IIa"`.

### Pseudo-code

```python
def handle_resolve_group(job):
    item = db.item_mirror.get(job.payload["item_ref"])
    mfr  = alias_lookup(item.manufacturer_raw)             # "Ivoclar Vivadent AG" -> "Ivoclar"
                                                           # miss -> insert raw as canonical, flag
    # resolution ladder — stop at first hit
    group = (existing_link(item)                           # already a member? done
          or by_udi(item)                                  # UDI-DI -> eudamed_mirror -> basic_udi_di
          or by_basic_udi_di(item, mfr)
          or by_name_family(item, mfr))                    # pg_trgm candidates -> rapidfuzz score

    if group and group.score >= NAME_ACCEPT:               # deterministic accept
        db.item_group_member.insert(group.id, item.item_ref,
                                    mfr_ref=item.mfr_ref,  # C1: materialized here —
                                    match_basis=group.basis)  # group.member_mfr_refs is the gate comparand
        # C4: manufacturer has an approved binding -> NOTHING here today; the hook below is inert (see note under the table)
        apply_manufacturer_bindings(item, mfr)
        if not has_current_production_doc(group.id):
            enqueue("discover.group",
                    payload={"group_id": group.id},
                    dedupe_key=f"discover:{group.id}:{current_cycle()}")
    elif group and group.score >= NAME_SUGGEST:            # ambiguous middle
        suggestion = t1_llm_adjudicate(item, group.candidates)   # only AI in this stage
        push_staging_grouping_suggestion(item, suggestion)       # human approves in review UI
    else:
        db.item_group.create_singleton(item, mfr)          # own group; discover as usual
        enqueue("discover.group", ...)
```

### Combinations

| Situation | Path | Emits |
|---|---|---|
| Item already linked to current doc | existing_link hit, doc current | nothing (done) |
| UDI present, EUDAMED knows it | udi → basic_udi_di, deterministic | `discover.group` if no doc |
| Manufacturer has approved binding (C4) | nothing at RESOLVE — `_apply_manufacturer_bindings` is inert (`resolve.py:250-259`); the mfr-scope links are GATE writes made at bind time for the items that exist then, followup `resolve-c4` for later arrivals | `discover.group` for item-level docs if still needed |
| Name matches "Tetric PowerFill" family at 0.93 | rapidfuzz accept | `discover.group` |
| Name matches two families at 0.78/0.76 | below `name_suggest` (0.90) → singleton | `discover.group` |

**S1.2 implementation notes** (impl: `app/handlers/resolve.py`, spec: `docs/specs/resolve.md`; interpretive choices flagged `[needs review]` in PHASES.md S1.2):
- Thresholds (G5a) are config `resolve.name_accept` / `name_suggest` / `name_candidate_k` (defaults **0.90 / 0.90 / 20**; rapidfuzz `token_set_ratio/100`). No Phase-0 seed — recalibrate on S1.7. `name_suggest` equals `name_accept` by ruling (Denis, 2026-08-12): the staging band withheld group membership, and therefore all document linking, from 36,5% of the catalogue. The T1 adjudication row above is the design; with an empty band nothing reaches it.
- The staging path persists to a new **`grouping_suggestion`** table (schema §2), not `manual_task`. Resolution loop: review UI enqueues `resolve.group {item_ref, group_id}` (additive `group_id`) → forced assign basis `manual` → suggestion resolved; the start-new-group half (S1.6) enqueues `resolve.group {item_ref, force_new_group: true}` → singleton with basis `manual` → suggestion resolved. No new job type.
- `by_udi` / `by_basic_udi_di` are catalogue-local in Phase 1 (exact UDI equality; UDI = a group's Basic UDI-DI). Full `udi → basic_udi_di` via the EUDAMED mirror is S2.3.
- Singleton members get `match_basis = 'singleton'` (**ratified 2026-07-24**: joins `item_group_member.match_basis`'s documented set `existing-link|udi|basic-udi-di|name-family|manual|singleton` — group-membership basis only, never an `item_document` basis).
- `apply_manufacturer_bindings` (C4) is an **inert** hook: invariant 1 forbids RESOLVE writing `item_document`; the mfr-scope link for a newly-resolved item is a GATE write, deferred to followup `resolve-c4`. RESOLVE's C4 contribution is the canonicalization (`manufacturer_alias`), which unblocks `gate.apply(bind-manufacturer)` switching to canonical.

---

## 3. `discover.group`

### Input

```json
{ "group_id": 4407 }
```

group: Ivoclar / "Tetric PowerFill" family, basic_udi_di unknown, 14 member items, member_mfr_refs `["613289","613290",…]`.

### Pseudo-code

```python
def handle_discover_group(job):
    group = db.item_group.get(job.payload["group_id"])
    for source in source_priority(group.manufacturer):     # data, per manufacturer
        match source:
            case "recency":
                # C3 re-entry without the network (2026-08-24): everything the
                # group knows (known_urls + playbook `direct` URLs) is ledger-
                # fresh AND already extracted -> emit the same validate.doc
                # FETCH's hash-dedupe branch emits (incl. archive_url when the
                # registry holds the stored copy's handle), terminal. Fresh but
                # unextracted stays a logged skip; ignore_recency bypasses.
                if fetch_log.checked_recently(group.known_urls):
                    hashes = [h for h in fresh_ledgered_hashes(group) if latest_rev(h)]
                    if hashes and not p.get("ignore_recency"):
                        for h in hashes:
                            enqueue("validate.doc",
                                    payload={"content_hash": h, "extract_rev": latest_rev(h),
                                             "group_id": group.id,
                                             "archive_url": document.archive_url_for(h)},
                                    dedupe_key=f"validate:{h}:{latest_rev(h)}:{group.id}")
                        log("hit"); return
                    log("skipped"); continue
            case "known_url":
                urls = group.known_urls                     # from previous production docs
            case "playbook":                                # S2.1, shipped 2026-09-03
                # direct sources, then each crawl recipe: one lease per host
                # before any fetch, robots.txt, harvest, deterministic triage
                # (link_pattern / doc_type_from / max_links). Then, for a
                # collection of >= crawl_rank_floor links (2026-09-04): judge
                # each link's type + article numbers once per library version
                # (crawl_link_rank), order by our-article-match, then
                # DoC > EC > ISO > IFU > other, then confidence; spend the
                # budget in that order, never filter.
                urls = _direct_urls(pb) + _crawl_playbook(pb.crawl, group)
            case "eudamed":
                urls = eudamed_mirror.cert_lookup(group.basic_udi_di or group.udis)
            case "search":
                q = _query_for(group, pb)   # label never quoted, dropped when it is only
                                            # a category noun (2026-09-02, F13)
                candidates = search_adapter.query(q)         # Brave
                urls = threshold_topk(t1_rank(candidates, group), k=3)  # T1 scores, rule decides
            case "email":
                if not cfg.discovery.email_rung_enabled:     # producer gated off until the
                    log("email", "skipped"); continue        # S2.4 handler exists (no-op would
                if contact_known(group.manufacturer):        # swallow the job silently)
                    enqueue("email.request", ...); log("email"); return
            case "manual":
                push_manual_queue(group, prefilled_search_links(group)); return
        db.discovery_log.insert(group.id, source, "hit" if urls else "miss")
        if urls:
            for u in urls:
                enqueue("fetch.url",
                        payload={"url": u, "domain": domain_of(u),
                                 "group_id": group.id, "source_rank": source},
                        dedupe_key=f"fetch:{normalize(u)}")
            return
```

Note (C2): monthly re-checks of known URLs are ordinary `fetch.url` enqueues — the previous cycle's `done` job no longer blocks them; the fetch ledger's recency window keeps them cheap.

### Output examples

Search-path hit:

```json
{ "type": "fetch.url",
  "payload": { "url": "https://www.ivoclar.com/.../doc_tetric_powerfill_mdr.pdf",
               "domain": "ivoclar.com", "group_id": 4407, "source_rank": "search" },
  "dedupe_key": "fetch:ivoclar.com/.../doc_tetric_powerfill_mdr.pdf" }
```

All sources miss, no contact: manual-queue row with prefilled links — no job emitted, human is the next stage.

---

## 4. `fetch.url`

### Pseudo-code

```python
def handle_fetch_url(job):
    p = job.payload
    if not domain_lease.acquire(p["domain"]): job.defer(politeness_ms); return

    ledger = fetch_log.get(normalize(p["url"]))
    if ledger and ledger.checked_within(RECENCY_WINDOW):
        # recency-skip-linked (C3 parity, S1.4): if the ledger hash is already
        # extracted and a group_id is requesting, still link that group before
        # skipping the network — otherwise a group discovering a SHARED
        # (manufacturer-/QMS-level) cert URL within the window is orphaned and
        # re-discovers forever, the exact loop C3 closes.
        rev = extraction_attempt.latest_rev(ledger.content_hash) if ledger.content_hash else None
        if rev and p.get("group_id"):
            # + archive_url when the registry holds the stored copy's handle
            # (document by content_hash) — additive 2026-08-24: without it GATE
            # fell back to fetch_log's remote SOURCE url and refreshed
            # document/evidence.archive_url to it (measured RENFERT doc 843).
            enqueue("validate.doc",
                    payload={"content_hash": ledger.content_hash, "extract_rev": rev,
                             "group_id": p["group_id"],
                             "archive_url": document.archive_url_for(ledger.content_hash)},
                    dedupe_key=f"validate:{ledger.content_hash}:{rev}:{p['group_id']}")
            job.finish("recency-skip-linked"); return
        job.finish("recency-skip"); return

    resp = http_get(p["url"], if_none_match=ledger.etag if ledger else None,
                    via=playwright if domain_flagged(p["domain"]) else httpx)
    if resp.status == 304:
        fetch_log.touch(p["url"]); job.finish("not-modified"); return

    h = sha256(resp.body)
    fetch_log.upsert(url=p["url"], etag=resp.etag, content_hash=h, source="live")
    if fetch_log.hash_seen_before(h):                       # same doc, different URL
        fetch_log.link_existing_doc(p["url"], h)
        # C3: requesting group gets a link candidate against the ALREADY-extracted doc.
        # No re-fetch, no re-extract — closes the infinite-rediscovery loop.
        enqueue("validate.doc",
                payload={"content_hash": h,
                         "extract_rev": db.extraction_attempt.latest_rev(h),
                         "group_id": p["group_id"],
                         "archive_url": document.archive_url_for(h)},  # when held (2026-08-24)
                dedupe_key=f"validate:{h}:{latest_rev}:{p['group_id']}")
        job.finish("hash-dedupe"); return

    archive_url = storage_adapter.put(resp.body,            # Drive v1 / S3 v2
        path=f"{group.manufacturer}/{guess_type_dir(resp)}/{h[:12]}__{filename(p['url'])}")
    enqueue("extract.doc",
            payload={"archive_url": archive_url, "content_hash": h,
                     "group_id": p["group_id"], "source_url": p["url"]},
            dedupe_key=f"extract:{h}")
```

### Outcome contract (C3 changes the hash-dedupe row)

| Case | fetch_log | Emits |
|---|---|---|
| New content hash | new row + archive | `extract.doc` |
| 304 Not Modified | `last_checked_at` touched | nothing |
| **Same hash, new URL** | URL row linked to existing doc | **`validate.doc`** against latest extraction, for the requesting group. Link candidate carries `match_basis = fetch-context` **unless** the existing extraction's REF list / Basic UDI-DI independently matches the group (then the stronger basis applies). Fetch-context caps at staging (§7). |
| Bot wall (403/JS) | retry via Playwright; persistent → domain flagged | retry or `dead` |
| Domain lease contended (S1.4) | untouched | nothing — handler self-defers (`lease-wait`), politeness via `domain_lease` |
| Recency hit, extraction exists (S1.4) | untouched | `validate.doc` for the requesting group (`recency-skip-linked` — C3 parity without the network) |

### Sibling producers (enter the spine here)

**`backfill.scan`** `{"drive_folder": "Compliance/Ivoclar"}`:

```python
for f in drive.walk(folder):
    h = sha256(drive.read(f))
    fetch_log.upsert(url=f.drive_path, content_hash=h, source="backfill")
    if not fetch_log.hash_seen_before(h):
        enqueue("extract.doc", payload={"archive_url": f.drive_url, "content_hash": h,
                                        "group_id": None,       # coverage resolved from doc content
                                        "source_url": f.drive_path},
                dedupe_key=f"extract:{h}")
```

Note `group_id: None` — backfill docs must self-identify via REF list / Basic UDI-DI; that's why the extraction schema carries coverage. Their REF-gate lookup must be manufacturer-scoped (§6 rule 1).

**`email.poll`** (S2.4 inbound — spec `docs/specs/email.md`): the durable reprocess guard is `email_poll_log`, keyed by (mailbox, uid_validity, imap_uid) — the authority over the IMAP `\Seen` flag, so a recorded message is never re-polled. Per PDF attachment (non-PDFs and MSDS/business docs are skipped, counted, and recorded): archive → same `extract.doc` shape as backfill, `group_id=None`, `source_url="email:{message_id}"` (the `email_ref` role — the `upload.ingest` precedent uses the same `source_url` namespacing rather than a separate field); full provenance (from/subject/received/headers) lives durably in `email_poll_log`, not the payload. Matching reply threads advance `renewal_request` state in the outbound slice (P2), which is also what supplies the `group_id` that turns a seen-hash reply into a `validate.doc` C3 link.

**`upload.ingest`** `{"upload_id": 42, "manual_task_id": 7}` — a human handed us a PDF via the web `/upload` form (either standalone or prefilled from a `/manual` discovery-dead-end task). `manual_task_id` is optional and absent for standalone uploads. `app/handlers/archiving.py` holds the archive-path/ledger/emit helpers shared with `fetch.py`, so the two producers cannot diverge:

```python
def handle_upload_ingest(job):
    p = job.payload
    row = upload_inbox.get(p["upload_id"])
    if row is None:
        job.finish("already-processed"); return       # retry after a committed delete

    h = sha256(row.content)
    fetch_log.upsert(url=f"upload:{h}", content_hash=h, source="upload")
    rev = extraction_attempt.latest_rev(h)

    if rev is not None:                                # seen content
        if row.target_group_id is not None:
            fetch_log.link_existing_doc(f"upload:{h}", h)
            enqueue("validate.doc",
                    payload={"content_hash": h, "extract_rev": rev, "group_id": row.target_group_id},
                    dedupe_key=f"validate:{h}:{rev}:{row.target_group_id}")
        # else: duplicate content, no requesting group — nothing to link
    else:                                              # new content
        archive_url = storage_adapter.put(row.content,
            path=f"{manufacturer_for_group(row.target_group_id)}/{guess_type_dir(row.filename)}/{h[:12]}__{row.filename}")
        enqueue("extract.doc",
                payload={"archive_url": archive_url, "content_hash": h,
                         "group_id": row.target_group_id, "source_url": f"upload:{row.filename}"},
                dedupe_key=f"extract:{h}")

    upload_inbox.delete(p["upload_id"])

    if p.get("manual_task_id") is not None:
        # Worker-side only — dentalia_api is SELECT-only on manual_task. Guarded
        # so it is idempotent and only ever closes an open dead-end task.
        manual_task.update(where={"id": p["manual_task_id"], "kind": "discovery-dead-end", "status": "open"},
                            set={"status": "resolved", "resolved_by": "upload"})

    job.finish()
```

Note `group_id` may be `None` here too (standalone upload with no `target_group_id`) — same self-identification posture as `backfill.scan`.

---

## 5. `extract.doc`

### Input

```json
{ "archive_url": "drive://.../Ivoclar/DOC/3f9a1c2e0b77__DoC_Tetric_PowerFill_(MDR).pdf",
  "content_hash": "3f9a1c2e0b77…", "group_id": 4407,
  "source_url": "https://www.ivoclar.com/..." }
```

### Pseudo-code

```python
TARGET = ["type","regulation","validity_from","validity_to","coverage_scope",
          "ref_list","basic_udi_di","referenced_docs","cert_number",
          "manufacturer",     # tenth field, 2026-08-13 — identity, without which
                              # VALIDATE's scoped path can never fire
          "stated_class"]     # eleventh, 2026-08-21 — the class the document
                              # states. T0-only: in no _ESCALATE_* set (the
                              # referenced_docs mechanism), so it is captured
                              # opportunistically and never buys an LLM call.
                              # QA/display only; BC stays source of truth.

def handle_extract_doc(job):
    pdf = storage_adapter.get(job.payload["archive_url"])
    fields = {}

    fields |= t0_extract(pdf, job)          # filename conventions ("(MDR)"), playbook
                                            # layout templates, pdfplumber REF tables
                                            # every T0 hit: confidence 1.0 + evidence
    missing = low_confidence(fields, TARGET)
    if missing:
        fields |= run_llm_tier("T1", pdf, missing, job)     # batch-aware — see below
        missing = low_confidence(fields, TARGET)
    if missing or is_scan(pdf):
        fields |= run_llm_tier("T2", pdf, missing, job)
    # per-FIELD escalation: clean date + unreadable REF list -> only REF goes to T2

    db.extraction_attempt.insert(job.payload["content_hash"],
                                 tiers_used, fields, extract_rev)
    enqueue("validate.doc",
            payload={"content_hash": job.payload["content_hash"],
                     "group_id": job.payload["group_id"], "extract_rev": extract_rev},
            dedupe_key=f"validate:{hash}:{extract_rev}:{job.payload['group_id']}")
```

### Batch API protocol (C9 — payloads immutable, batch_ref side table)

```python
def run_llm_tier(tier, pdf, missing, job):
    h = job.payload["content_hash"]
    ref = db.batch_ref.get(h, tier)
    if ref is None:                                   # first pass for this hash+tier
        batch_id = llm.submit_batch(build_requests(pdf, missing, tier))
        db.batch_ref.insert(h, tier, batch_id)        # recorded BEFORE ack —
                                                      # crash-retry finds the row, never resubmits
        job.defer(run_after=now() + POLL_INTERVAL); raise Deferred
    if not llm.batch_done(ref.batch_id):
        job.defer(run_after=now() + POLL_INTERVAL); raise Deferred
    results = llm.batch_results(ref.batch_id)
    db.batch_ref.resolve(h, tier)
    return per_field_results(results)                 # conf + verbatim + page + model_id each
```

Self-rescheduling via `run_after`; the job payload is never mutated. A crash between submit and record resolves on retry via the `batch_ref` lookup — existing row for this `content_hash` + tier means never resubmit.

### Example extraction result (stored in extraction_attempt.fields)

```json
{ "type":          {"value":"DoC","conf":1.0,"tier":"T0","verbatim":"Declaration of Conformity","page":1},
  "regulation":    {"value":"MDR","conf":1.0,"tier":"T0","verbatim":"(EU) 2017/745","page":1},
  "validity_from": {"value":"2024-03-15","conf":0.97,"tier":"T1","verbatim":"Schaan, 15. März 2024","page":2},
  "validity_to":   {"value":null,"conf":0.95,"tier":"T1","verbatim":"<no expiry stated>","page":null},
  "coverage_scope":{"value":"group","conf":0.9,"tier":"T1"},
  "ref_list":      {"value":["613289","613290","613291"],"conf":1.0,"tier":"T0","page":3},
  "basic_udi_di":  {"value":"7612147BDTPFEW","conf":0.88,"tier":"T2","page":1},
  "cert_number":   {"value":null,"conf":0.9,"tier":"T1"},
  "stated_class":  {"value":"IIa","conf":0.95,"tier":"T0","verbatim":"Class: IIa","page":null} }
```

---

## 6. `validate.doc`

### Pseudo-code (pure rules — no AI, no writes)

```python
def handle_validate_doc(job):
    ext   = db.extraction_attempt.get(job.payload["content_hash"], job.payload["extract_rev"])
    group = db.item_group.get(job.payload["group_id"])      # may be None (backfill/email)

    # C4 pre-rule: manufacturer-scope doc without item-level identifiers
    # -> REF gate NOT APPLICABLE -> route to binding flow (§7b), one candidate total.
    # manufacturer hint = group.canonical_manufacturer (normal) or ext.manufacturer (backfill).
    if ext.coverage_scope == "manufacturer" and not ext.ref_list and not ext.basic_udi_di:
        # [qa-device-enumeration] (2026-08-25): a certificate whose own stored text
        # enumerates its covered devices (model/code annex, per-device class lines,
        # "valid only for the above mentioned" clause) is NOT manufacturer-wide —
        # flag rides the candidate, GATE's C16 machine bind refuses on it (§7b).
        flags = ["device-enumeration"] if enumerates_devices(document_text(hash)) else []
        enqueue("gate.candidate",
                payload={..., "route": "mfr-binding", "manufacturer": mfr_hint,  # mfr key omitted if unknown
                         "flags": flags},                                        # key omitted when empty
                dedupe_key=f"gate:{hash}:{extract_rev}:mfr-binding"); return

    flags, links = [], []

    # no-item-identifier ([validate-itemid]): a NON-manufacturer-scope doc that yields
    # no item id at all (no ref_list AND no basic_udi_di) cannot be linked to the
    # catalogue -> blocking flag -> GATE forces manual (never silently staged).
    if not ext.ref_list and not ext.basic_udi_di:
        flags.append("no-item-identifier")

    # C1: REF gate — comparand is always the PAIR (canonical_manufacturer, mfr_ref).
    # ref_gate_for_group returns (bool, links[]) where each link carries its match_basis.
    ref_ok, resolved_group = False, group
    if group:
        ref_ok, links = ref_gate_for_group(group, ext)      # group is manufacturer-scoped by construction
    else:                                                    # backfill: resolve coverage from doc
        if ext.manufacturer:
            group, n = resolve_group_scoped(ext.manufacturer, ext)  # ORDER BY group_id; counts matches
            if group:
                ref_ok, links = ref_gate_for_group(group, ext)
                resolved_group = group
                if n > 1:                                    # doc covers >1 group of this manufacturer
                    flags.append("multi-group-match")        # resolve to lowest id; caps at staging
            elif groups_found > 0:                           # we know whose it is; it covers nothing we stock
                flags.append("no-ref-overlap")               # was a silent fall-through before 2026-08-11
        elif ext.ref_list or ext.basic_udi_di:
            # No manufacturer at all. A REF match can still PROPOSE a link, at a basis
            # the item_document CHECK bars from production (C5). Guards first.
            eligible = [r for r in ext.ref_list                    # >=6 chars: `100`->INTERDENT is
                        if len(r) >= cfg.validate.min_unscoped_ref_len   # unambiguous AND worthless
                        and "!" not in r]                          # prose (mfr_ref_prose rule)
            holders = manufacturers_holding_refs(eligible)         # RAW refs: normalizing here
            if len(holders) == 1:                                  # raises collisions 12->18, adds 0 hits
                group, n = resolve_group_scoped(holders[0], eligible)
                if group:
                    _, links = ref_gate_for_group(group, eligible) # ref_normalize DOES apply now:
                    links = [l | {"match_basis": "ref-catalogue"}  # the manufacturer is pinned
                             for l in links]
                    if links:
                        resolved_group = group
                        flags.append("ref-catalogue")              # ref_ok stays False -> doc caps too
                        if n > 1: flags.append("multi-group-match")
            elif len(holders) > 1:                                 # e.g. `878115`: GC EUROPE | VOCO
                flags.append("multi-manufacturer-ref")             # flag and stage, NEVER choose
            if not links:
                if resolve_group_unscoped(ext):                    # a hit exists but failed a guard
                    flags.append("ref-unscoped")                   # untrustworthy -> never used
                elif ext.ref_list:
                    flags.append("no-ref-overlap")                 # compared, found nothing

    # Date sanity (rule 3): from <= to AND each date in a plausible year band [1990, 2100].
    if not date_sane(ext): flags.append("date-insane")

    # C7 (never-downgrade) / C6 (supersession) — keyed off the current PRODUCTION
    # document for the identical (coverage subject, type, regulation).
    supersedes = related_doc_id = superseded_by_doc_id = None
    if resolved_group and ext.type and ext.regulation:
        current = current_production_doc(resolved_group, ext.type, ext.regulation)
        if current:
            if ext.validity_from is None or current.validity_from is None:
                flags.append("downgrade-uncomparable")       # null never compared; caps at staging
            elif ext.validity_from < current.validity_from:
                # Both dates present + older (task 5, fix round 1): emit the
                # fast-path signal AND keep the safety-net flag -- both, not
                # either/or. GATE's own guard (blocking flags + validity_from
                # confidence) decides whether the fast path is actually taken;
                # "older-than-current" is what forces manual when it isn't.
                superseded_by_doc_id = current.doc_id
                flags.append("older-than-current")
                flags.append("auto-superseded")
            elif ext.validity_from > current.validity_from:
                supersedes = current.doc_id                  # C6 within identical subject/type/regulation
        related = related_production_doc(resolved_group, ext.type, ext.regulation)
        if related:
            related_doc_id = related.doc_id                  # MDR/MDD parallel chains; VALIDATE writes NOTHING,
                                                             # it emits the id for GATE to persist as `related`

    # Rule 6 (amended 2026-08-17): the issue-vs-expiry trap, tested directly.
    # Was `not ext.referenced_docs` -- a certificate-citation proxy that over-fired
    # (15 IVOCLAR docs at score 0.97, expiry printed plainly on the page) and
    # under-fired (GC doc 227 reached production with "Leuven, 12 February 2026",
    # a signing line, as its expiry -- it cited a cert, so the proxy passed it).
    # The field's own evidence verbatim answers the question. An unrecognised
    # phrase leaves the flag ON, so the list fails safe and grows per brand.
    if ext.type == "DoC" and ext.validity_to and not expiry_is_stated(ext.validity_to.verbatim):
        flags.append("expiry-on-certless-doc")      # name historical, see vocabulary.md

    # Rule 8 (task 4, migration 020): cited-certificate resolution. A DoC's real
    # renewal date lives on the notified-body certificate it cites (Annex IV item
    # 8), not on the declaration itself (Annex IV requires only a date of ISSUE;
    # Article 56 caps the certificate at five years). Best-effort: no held EC/ISO
    # document matches cert_number or any entry of referenced_docs -> cert_doc_id
    # stays None, flag cert-unresolved (non-blocking, self-corrects when GATE
    # later back-resolves the certificate, §7).
    cert_doc_id = None
    if ext.type == "DoC":
        cert_doc_id = resolve_cited_certificate(ext)          # cert_number + referenced_docs[]
                                                                # vs held EC/ISO document.cert_number
        if cert_doc_id is None and (ext.cert_number or ext.referenced_docs):
            flags.append("cert-unresolved")

    enqueue("gate.candidate",
            payload={"content_hash": ..., "extract_rev": ..., "group_id": resolved_group.id,
                     "ref_gate": ref_ok, "flags": flags, "links": links,
                     "supersedes": supersedes, "related_doc_id": related_doc_id,  # related_doc_id omitted if None
                     "cert_doc_id": cert_doc_id,
                     "superseded_by_doc_id": superseded_by_doc_id},               # task 5, always present (null if n/a)
            dedupe_key=f"gate:{hash}:{extract_rev}:{resolved_group.id}")           # keyed on the RESOLVED group
```

### Rule set (normative in PRD v3 §6)

1. **REF gate (C1):** pair-scoped, always. Bare article numbers collide across manufacturers. A member has **two** article numbers and both are comparands (**amended 2026-08-13**, client answer to C12): its `mfr_ref` (the supplier's — basis `ref-list`) and its `item_ref` (Dentalia's own, the number printed on the physical article — basis `ref-item`). `mfr_ref` is tested first, so the 7.450 of 15.958 members holding the same value in both columns record the stronger claim; a member matching only on `item_ref` records `ref-item`, which reaches the 7.082 rows carrying no `mfr_ref` at all. Both are production-capable *because this rule is the scoped path* — the manufacturer is established before any number is compared, so the number only picks items within a known manufacturer. Backfill/email docs with `group_id = null` must scope the index lookup by the document's extracted/known manufacturer. When there is no manufacturer to scope by, a guarded **unscoped** match may still link at `match_basis = 'ref-catalogue'` (added 2026-08-11) — capped at staging by the `item_document` CHECK, because the manufacturer was inferred from the article number rather than verified; `ref_gate` stays False. Guards: minimum REF length (`cfg.validate.min_unscoped_ref_len`, default 6), prose exclusion, and `multi-manufacturer-ref` instead of a choice when the REFs span several manufacturers. A hit that fails a guard flags `ref-unscoped` as before, and a document whose REFs match nothing flags `no-ref-overlap`. Emits `links[]` — one entry per covered item, each carrying its own `match_basis` (GATE gates every link independently, §7). When a doc matches **more than one** group of its manufacturer it links **every** matching member of **all** of them (**amended 2026-08-13**): coverage follows the document's REF list, not our clustering, and every group scanned carries the same `canonical_manufacturer` so the invariant-3 pair still holds for each link. An item reachable through two groups is linked once at the strongest basis (`item_document` is keyed `(item_ref, doc_id)`). The lowest matching `group_id` stays the document's group for provenance only; `multi-group-match` is still flagged with its count on the job result, now reporting breadth rather than marking dropped groups. Before this, resolving to the lowest group and linking only its members lost 228 of 323 reachable items across 23 GC documents.
2. **Manufacturer-scope pre-rule (C4):** REF gate not applicable → binding flow, not per-group gate failure. The candidate carries a `manufacturer` hint (group's canonical name, or `ext.manufacturer` for backfill) for the binding entry. **Device-enumeration guard (2026-08-25, Denis ruling 2026-08-24):** VALIDATE reads the stored `document_text` here; a QMS/QA certificate whose own text enumerates the devices it covers — model/code annex, repeated per-device risk-class lines, or the "valid only for the above mentioned Medical Devices" clause (sufficient alone; the others fire two-of-three) — adds flag `device-enumeration` to the binding candidate, and GATE's C16 machine bind refuses on it (staged + one review task; `gate.apply bind-manufacturer` unaffected). Designed from measured text: doc 310 (Kiwa Cermet MED 31385) enumerates three device types and was bound to 356 GC items; over all 809 stored texts the detector fires on exactly that document, while scope-only ISO 13485s (doc 816), device-family schedules (doc 138) and EMDN category lists (doc 420) stay bindable. Missing/scanned text (`source='none'`) is unmeasurable → no flag, today's behaviour.
3. Date sanity: `from ≤ to` **and** each date within a plausible year band (`[1990, 2100]` — a hallucinated `9999` or OCR-garbled 3-digit year is not a real date and must never drive supersession) → `date-insane` otherwise. "valid N years from issue" arithmetic only on high-confidence rule text (not implemented — the schema does not carry that rule text).
4. **Never-downgrade (C7):** null on either side → `downgrade-uncomparable`, no comparison, staging cap. Both present and equal → `same-date-revision` (blocking, ruled 2026-09-04): no ordering, neither supersedes, a person picks. Candidate older than current, **both dates present** → VALIDATE emits `superseded_by_doc_id = current.doc_id` and flag `auto-superseded` **in addition to** the existing blocking flag `older-than-current` (task 5, fix round 1 — both, not either/or). GATE (§7) only routes it straight to disposition `superseded` without a human when its own guard passes (no other blocking flag, `validity_from` confidence ≥ `cfg.gate.high`); otherwise `older-than-current` forces `manual` exactly as before task 5 — never onto production either way. Either outcome keeps the document archived and evidenced. This is the inverse of C6 (item 5 below): the incoming candidate is the *older* document, superseded by the existing production one, not the other way round.
5. **Supersession (C6):** identical `(coverage subject, type, regulation)` only. Cross-regulation → `related`: VALIDATE **writes nothing**, it emits `related_doc_id` on the candidate for GATE to persist as the related-pair (MDR/MDD parallel chains, never `superseded_by`).
6. Stated expiry (a `DoC` whose `validity_to` is not named as an expiry in its own evidence verbatim → `expiry-on-certless-doc`; flag name historical, rule amended 2026-08-17 from a certificate-citation proxy). No numeric confidence factor is applied — VALIDATE is zero-discretion; the flag alone keeps GATE off `production` (scoring/calibration is GATE's role).
7. **No-item-identifier ([validate-itemid]):** a non-manufacturer-scope doc yielding no `ref_list` and no `basic_udi_di` cannot be linked to the catalogue → flag `no-item-identifier` (blocking — GATE forces manual, never staged). The only legitimate no-item-id case is a manufacturer/catalogue-wide cert, already routed by C4 above.
8. **Cited-certificate resolution (task 4, migration 020):** for a DoC, match `cert_number` and each entry of `referenced_docs[]` against `cert_number` on a held `EC`/`ISO` document (`production` or `superseded`) → emit `cert_doc_id`. No match → `cert_doc_id = null`, flag `cert-unresolved`. Best-effort by design: Class I devices cite no certificate at all, and the cited one may simply not be held yet — GATE back-resolves it when it later arrives (§7).

**Flag disposition (enforced by GATE, §7):** *blocking* flags `older-than-current`, `same-date-revision`, `no-item-identifier`, `evidence-page-missing`, `date-insane` and `manufacturer-unresolved` force `manual`. `auto-superseded` is a fast-path signal, not a disposition-changing flag on its own: GATE takes the fast path to `superseded` only when ALL of (a) `superseded_by_doc_id` present, (b) no blocking flag *other than* `older-than-current` survives, (c) `validity_from` confidence ≥ `cfg.gate.high` hold (task 5, fix round 1). Fails any of the three → falls through to the ordinary chain, where `older-than-current` forces `manual`. **Amended 2026-08-13 (Denis's ruling) — three classes, not one list.** *Blocking* gains `date-insane` and `manufacturer-unresolved`. *Capping* (holds at `staged`, reviewer settles it): `ref-unscoped`, `ref-catalogue`, `multi-manufacturer-ref`, `downgrade-uncomparable`, `expiry-on-certless-doc`, `device-enumeration` (2026-08-25 — raised on `mfr-binding` candidates only, where the C16 machine bind refuses on it directly; classified so it caps if it ever rides a normal-route candidate). *Informational* (recorded and displayed, does not gate): `no-ref-overlap`, `multi-group-match`, `cert-unresolved`, `auto-superseded`. GATE tests membership of the informational set, so an unclassified future flag still gates.

Before the amendment every flag capped, and production required an empty flag list. On the GC corpus that left 43 documents holding all 309 live links staged on `cert-unresolved` / `multi-group-match` alone, while the 74 `no-ref-overlap` documents it also held back had zero links between them by definition of the flag.

`manufacturer-unresolved` ([validate-mfr-alias]) fires when a `group_id=null` backfill/email document's extracted manufacturer, after `manufacturer_alias` canonicalization and case/accent folding, matches no `item_group` at all. It is now *blocking*: the document is still real and still archived, but nothing can attribute it, so a human owns it rather than it resting in staging. `ref_gate` is False in that state anyway, so `production` was already unreachable. Such a candidate stages with zero links.

---

## 7. `gate.candidate`

### Pseudo-code — two-level disposition (C5)

```python
def handle_gate_candidate(job):
    p = job.payload
    if p.get("route") == "mfr-binding":
        return handle_mfr_binding_candidate(p)               # §7b — one staging entry total

    ext = db.extraction_attempt.get(p["content_hash"], p["extract_rev"])
    require_complete_evidence(ext)                # missing evidence -> hard error, never write

    # document level
    score = min(f.conf for f in ext.required_fields)     # REQUIRED_FIELDS only — validity_from NOT included
    auto_superseded = p.get("superseded_by_doc_id")
    # task 5, fix round 1: the fast path is NARROW — all three must hold, or
    # this falls through to the ordinary chain below, where "older-than-current"
    # (a BLOCKING flag again) forces manual exactly as it did pre-task-5.
    auto_supersede_ok = (
        auto_superseded is not None
        and not any(f in BLOCKING_FLAGS and f != "older-than-current" for f in p["flags"])
        and calibrated_confidence(ext, "validity_from") >= HIGH   # missing/unparseable -> fails closed
    )
    if auto_supersede_ok:
        doc_status = "superseded"
    elif score >= HIGH and p["ref_gate"] and not p["flags"]:
        doc_status = "production"
    elif score >= MED and not blocking(p["flags"]):
        doc_status = "staged"
    else:
        doc_status = "manual"

    with db.tx():                                 # single transaction: claim+write+audit
        doc = db.document.upsert_by_hash(ext, status=
                 {"production": "production", "superseded": "superseded"}.get(doc_status, "staged"),
                 supersedes=p["supersedes"],             # 015: sticky-staged regardless of
                 related_doc_id=p.get("related_doc_id"), # disposition (COALESCE on conflict —
                 cert_doc_id=p.get("cert_doc_id"))        # never erased by a later write that
                                                          # doesn't recompute one — 020 follows
                                                          # the same sticky pattern

        # task 5: candidate is OLDER than current production — the INVERSE of
        # C6 below, so this writes the chain pointer directly rather than via
        # apply_supersession (whose UPDATE targets the other document). Guarded
        # on auto_supersede_ok (not just superseded_by_doc_id's presence) — a
        # failed guard means `manual`, not `superseded`, and must not carry a
        # chain pointer. Audited the same idempotent way _apply_supersession is,
        # and C6-re-checked the same way too: the payload's target was resolved
        # by VALIDATE and can be re-typed while this job waits in the queue
        # (a re-extraction rewrites type/regulation on upsert; gate.apply
        # approve accepts human edits to both), so the identity is re-read LIVE
        # here in the writer, in the mirrored direction, and a mismatch raises.
        if auto_supersede_ok:
            assert_chain_identity(newer=auto_superseded, older=doc.doc_id)   # C6 re-checked
            if db.document.set_superseded_by_if_unset(doc.doc_id, auto_superseded):
                db.audit_log.append("auto-superseded", doc.doc_id,
                                    decided_by="gate", via_job=job.id,
                                    job_snapshot=snapshot(job))

        # task 4 back-resolution: this doc may itself be a certificate that
        # earlier-processed DoCs cited before we held it — order-independent.
        if ext.type in ("EC", "ISO"):
            db.document.update_many(
                where={"type": "DoC", "cert_doc_id": None, "cert_number": ext.cert_number},
                set={"cert_doc_id": doc.doc_id})

        # link level — each link gated INDEPENDENTLY by its own match_basis
        for item_ref, basis in covered_items_with_basis(ext, p["group_id"]):
            link_status = ("production"
                           if basis in ("ref-list","ref-item","map-supplier","udi","basic-udi-di","mfr-scope","manual")
                              and doc.status == "production" and score >= HIGH
                           else "staged")         # name-family, fetch-context, ref-catalogue: ALWAYS staged
            # ^ this tuple IS gate.TRUSTED_BASES, seven values. `map-supplier`
            #   (2026-08-19) is a REF list read out of the manufacturer's own
            #   article index rather than off the document: production-capable
            #   for the reason `ref-list` is, the manufacturer being fixed
            #   before any number is compared. It reached the PRD, the schema
            #   sketch and docs/vocabulary.md that day and this doc on 08-25.
            db.item_document.upsert(item_ref, doc.doc_id,   # a PRODUCTION link is immutable
                                    match_basis=basis,      # here: status/basis/udi keep their
                                    status=link_status)     # stored values (provenance, C5 CHECK)
        db.evidence.insert_all(doc.doc_id, p["extract_rev"], ext.fields)  # 014: evidence
                                                                          # scoped per rev
        if doc_status == "production":
            if p["supersedes"]:
                apply_supersession(p["supersedes"], by=doc.doc_id, via_job=job)  # C6 re-checked
                                                                                  # against a live
                                                                                  # read, not trusted
            db.audit_log.append("production-write", doc.doc_id,
                                decided_by="gate", via_job=job.id,
                                job_snapshot=snapshot(job))                 # C8
        elif doc_status == "manual":
            push_manual_queue(doc, ext, p["flags"])
```

**Implementation status:** shipped 2026-07-31 (`[gate-c6]` closed). VALIDATE emits `supersedes` and `related_doc_id` on the candidate (§6); `gate.candidate` persists `related_doc_id` to `document.referenced_doc_id` on every write regardless of disposition, and `supersedes` to `document.supersedes` the same way — but only *applies* a supersession (flips the OLD document to `superseded` + `superseded_by`) once the candidate itself reaches production. A candidate that lands as `staged` carries its `supersedes` value forward, inert, until a human `gate.apply approve` promotes it (§8) — a staged doc must never actually supersede anything (PRD §7: staged entities invisible to all consumers). `apply_supersession` re-reads both documents' `(type, regulation)` from the database before writing — it does not trust the payload — and raises rather than silently skipping on a mismatch (a hard error surfaces a real upstream bug instead of hiding it). The task-5 auto-file path shares that check (`assert_chain_identity`, mirrored direction), so **both** routes to a chain pointer are guarded identically: VALIDATE's scoping makes the payload correct when it is computed, but the job then waits in the queue, and either document's `type`/`regulation` can move underneath it — `document.upsert_by_hash` rewrites both columns unconditionally (only `status` is sticky), so a re-extraction re-types even a production document with no human involved, and `gate.apply approve` accepts human edits to both. Idempotent under at-least-once delivery (the UPDATE only fires, and only then is the `supersede` audit event appended, when it would actually change something).

**Cited-certificate persistence + back-resolution (task 4, migration 020):** `cert_doc_id` is persisted the same sticky way as `related_doc_id`/`supersedes` (`COALESCE(EXCLUDED.cert_doc_id, document.cert_doc_id)`) — a later re-delivery that doesn't recompute it never erases an already-resolved certificate. Independently, whenever the document GATE is writing is itself a certificate (`type ∈ {EC, ISO}`), GATE back-resolves: every held `DoC` with `cert_doc_id IS NULL` and a matching `cert_number` is updated to point at the newly-written certificate. This closes the fetch-order gap — declarations are frequently processed before the certificate they cite, and without back-resolution whether a DoC ever gets an expiry would depend on which arrived first.

Consumers see a document *for an item* only when **both** document and link are production (view `item_document_production`). A production document may carry a mix of production links (REF-matched) and staged links (name-matched stragglers) — the "name similarity never auto-writes" invariant lives per-link, structurally.

`blocking(flags)` — the flags that force `manual` (can never auto-write, not even to staging): **`older-than-current`, `no-item-identifier`, `evidence-page-missing`, `date-insane`, `manufacturer-unresolved`** (the last two added 2026-08-13), **`same-date-revision`** (added 2026-09-04). *Capping* flags (`ref-unscoped`, `ref-catalogue`, `multi-manufacturer-ref`, `downgrade-uncomparable`, `expiry-on-certless-doc`) cap the disposition at `staged` — a staged doc still writes, it is just invisible to consumers until reviewed. *Informational* flags (`no-ref-overlap`, `multi-group-match`, `cert-unresolved`, `auto-superseded`, `ref-list-possibly-truncated`) are recorded but do not gate at all, so a document carrying only these can reach `production` (`app/handlers/gate.py` `_gates_disposition`). `auto_supersede_ok` is checked *before* `blocking(flags)` — a candidate carrying `superseded_by_doc_id` routes straight to disposition `superseded` (task 5) **only when its own three-condition guard passes** (no blocking flag other than `older-than-current`, `validity_from` confidence ≥ `HIGH`); this is the only case where a candidate lands neither on production/staged/manual but is still fully archived and evidenced. The pointer it then writes is C6-re-checked against a live read of both documents, exactly as `apply_supersession` does on the forward route — a mismatch raises and the job dead-letters, it is never written and never skipped. Any guard failure falls through to `blocking(flags)` as normal, where `older-than-current` forces `manual` — the fix (round 1, 2026-08-07) that closes two holes the unconditional first draft had: an older doc with no catalogue link (`no-item-identifier`) auto-filing with zero links and no review, and a garbled OCR `validity_from` (a field outside `score`/`REQUIRED_FIELDS`) driving a permanent write with no confidence check at all. This split is set by VALIDATE's rule set (§6) and enforced here.

### Disposition examples

| score | basis | flags | doc | link |
|---|---|---|---|---|
| 0.97 | ref-list | — | production | production |
| 0.97 | name-family | — | production possible | **staged — always**, regardless of confidence |
| 0.97 | fetch-context (C3 path) | — | (existing doc) | staged |
| 0.83 | ref-list | — | staged | staged |
| 0.97 | ref-list | older-than-current + auto-superseded, `superseded_by_doc_id` set, validity_from conf ≥ HIGH | superseded — filed, no manual_task (task 5, guard passes) | staged |
| 0.97 | ref-list | older-than-current + auto-superseded, `superseded_by_doc_id` set, validity_from conf LOW | manual (guard fails — fix round 1) | staged |
| 0.97 | (none, no ref match) | older-than-current + auto-superseded + no-item-identifier | manual (guard fails — other blocking flag survives) | — |
| 0.97 | ref-list | downgrade-uncomparable | staged (cap) | staged |
| 0.97 | ref-list | multi-group-match | staged (cap) | staged |
| 0.97 | (none) | no-item-identifier | manual | — |
| 0.55 | any | any | manual with all tier attempts attached | — |

### 7b. Manufacturer-scope binding flow (C4)

A document with `coverage_scope = manufacturer` and no item-level identifiers (typical QMS-type certificate) produces **exactly one** review entry regardless of how many groups requested it:

```
gate.candidate(route=mfr-binding)
  → document written as staged, binding candidate doc → canonical_manufacturer
  → single review-queue entry
human approves (gate.apply, decision=bind-manufacturer):
  → document promoted to production
  → links derived to ALL MD items of that manufacturer, match_basis=mfr-scope, status=production
  → future items resolving to that manufacturer do NOT get the link today (RESOLVE's C4 hook is inert, followup `resolve-c4` / `[filed-relink]`); only items present at bind time are linked
  → binding = audit event; severable (unbind = reject + link cascade to rejected, append-only trail)
```

**C16 machine binding** (PRD §7, 2026-08-17) short-circuits the human approval above when the document's manufacturer string resolves through `manufacturer_alias` to exactly one canonical, that field's confidence clears `MED`, the stated scope clears `HIGH`, the document is a medical-device document at all (`_is_md_document`) — **and the candidate does not carry `device-enumeration`** (2026-08-25): a certificate whose own text enumerates its covered devices is refused by the machine and staged with one review task, because a line-wide bind would assert coverage the paper limits to a device list (doc 310: three enumerated device types, 356 items bound). The refusal is a review, not a rejection — `bind-manufacturer` stays available to the reviewer.

Manufacturer-level *product* certificates that do carry Basic UDI-DI lists (Annex XII style) pass the normal REF gate per group — they never use this flow. Config `mfr_binding.auto_link` (default on) is per-manufacturer overridable — a paranoid manufacturer can stay on per-group review even after binding.

## 8. `gate.apply` (human decisions)

```json
{ "doc_id": 9912, "decision": "approve", "decided_by": "user:marta",
  "edits": {"validity_to": "2029-03-14"} }
```

```json
{ "doc_id": 9912, "item_ref": "2222100", "decision": "confirm-link",
  "decided_by": "user:marta" }
```

```json
{ "doc_id": 9912, "decision": "reject", "decided_by": "user:marta",
  "note": "Out of date: newer 2025 version exists" }
```

`note` (optional, added 2026-09-11 by the office UI redesign, P1a) is the
reviewer's reason: `"<reason>"` or `"<reason>: <free text>"`. The review UI
requires one of six fixed reasons on a reject (`REJECT_REASONS` in
`web/app.py`), caps the free text at 500 characters (`REJECT_NOTE_MAX`), and
sends it on no other decision. The handler copies it into
the decision's own audit row, `detail.note`, and nowhere else; a payload
without it writes the audit row it always did. The dedupe key is unchanged.

```python
def handle_gate_apply(job):
    with db.tx():
        # C17 link decisions rule on ONE (doc_id, item_ref) and never touch the
        # document, so they take neither the manual-task resolution nor the
        # document-level audit below. They return first.
        if decision in ("confirm-link", "reject-link", "reopen-link"):
            return apply_link_decision(job, decision)
        match job.payload["decision"]:
            case "approve":
                apply_edits(job.payload)                     # edited values get tier=T3 evidence,
                promote(doc_id, to="production")             # verbatim = human input, conf = 1.0
                promote_pending_links(doc_id)                # trusted-basis links follow the doc
                supersedes = db.document.get(doc_id).supersedes   # C6 human path: apply whatever
                if supersedes:                                    # gate.candidate staged here,
                    apply_supersession(supersedes, by=doc_id,      # inert until this approve
                                       via_job=job, decided_by=decided_by)
            case "reject":
                db.document.set_status(doc_id, "rejected")
                for link in cascade_links_to_rejected(doc_id):   # §7: no link stays production/
                    db.audit_log.append("link-rejected", ...)    # staged under a rejected doc
                enqueue("discover.group", {"group_id": g},   # optional retry, interactive priority
                        priority="interactive")
            case "bind-manufacturer":                        # C4 — §7b
                promote(doc_id, to="production")
                derive_mfr_scope_links(doc_id, manufacturer) # all MD items of the CANONICAL
                                                             # manufacturer, resolved through
                                                             # manufacturer_alias; basis=mfr-scope
        db.audit_log.append(decision, doc_id, decided_by=job.payload["decided_by"],
                            via_job=job.id, job_snapshot=snapshot(job),   # C8
                            detail={"note": note} if note else None)      # reviewer's reason
        if decision in ("approve", "bind-manufacturer") and was_not_production:
            db.audit_log.append("production-write", doc_id, ...)  # §9: every staged->production
                                                                  # transition audited, human
                                                                  # paths included (once, idempotent)
```

### Link-level decisions (C17, 2026-08-24)

C5 gave every link its own status and its own gate, but no human verb.
`promote_pending_links` publishes only trusted-basis links and the
`item_document_trusted_basis_ck` CHECK bars `name-family` / `fetch-context` /
`ref-catalogue` from `production` outright — while `manual`, enumerated as a
production-capable basis since v3, **had no writer anywhere in the codebase**.
An untrusted link could therefore be neither published nor refused: it sat
`staged` under a `production` document forever, indistinguishable from one
nobody had reviewed. Approving the DOCUMENT did nothing to it and could not
(found on item 2222100 / doc 843, 2026-08-24).

```python
LINK_DECISIONS = {                # decision -> (required status, new status, audit event)
    "confirm-link": ("staged",   "production", "link-confirmed"),
    "reject-link":  ("staged",   "rejected",   "link-rejected"),
    "reopen-link":  ("rejected", "staged",     "link-reopened"),
}

def apply_link_decision(job, decision):
    want, new_status, event = LINK_DECISIONS[decision]
    require(job.payload.get("item_ref"))                 # raises; a link decision without a link
    require(db.document.get(doc_id).status == "production")
    #  ^ shared by all three. Confirming under an unvetted document publishes an
    #    unreviewed reading through the side door; re-opening under a rejected one
    #    recreates the staged-link-under-a-rejected-doc state the reject cascade kills.
    link = db.item_document.get(doc_id, item_ref)        # raises if absent
    if link.status == new_status:
        return {...}                                     # idempotent re-delivery, no second audit row
    require(link.status == want)                         # raises: never a silent no-op

    if decision == "confirm-link":
        db.item_document.set(doc_id, item_ref,           # basis + status in ONE statement, or the
                             match_basis="manual",       # C5 CHECK correctly rejects the row.
                             status="production")        # SOLE writer of match_basis='manual'.
    else:
        db.item_document.set_status(doc_id, item_ref, new_status)   # basis kept: the trail must
                                                                    # record how a refused link
                                                                    # was proposed
    db.audit_log.append(event, doc_id, item_ref=item_ref, decided_by=...,
                        detail={"match_basis_before": link.match_basis, ...})   # inv. 10: confirm
                                                                                # overwrites the basis
    if decision == "confirm-link":
        db.audit_log.append("production-write", doc_id, item_ref=item_ref, ...)  # §9, link-level
```

**Companion fix — `rejected` is sticky on link upsert.** `upsert_link`'s
`ON CONFLICT` treated only `production` as immutable, so any later candidate
naming the same `(item_ref, doc_id)` rewrote a `rejected` row back to `staged`
and resurrected a coverage claim a person had refused. The hole predates C17:
`gate.apply reject`'s link cascade has written `rejected` links since v3.
`STICKY_LINK_STATUSES = ("production", "rejected")` closes it. `retracted` is
deliberately excluded — it records "the current extraction stopped claiming
this", a statement about evidence rather than a decision, so a later extraction
claiming the link again SHOULD revive it.

`reopen-link` exists because of that stickiness. Locking machines out of
`rejected` would otherwise make a reviewer's mistake permanent, with a
hand-written UPDATE — an unaudited registry write by definition — as the only
remedy. The asymmetry is the point: **a machine may never move a link out of
`rejected`; a named person always may**, audited, and the link lands back in
`staged` to be confirmed again on the merits rather than springing to
production.

### Manual queue (`manual_task`, G13)

`push_manual_queue(...)` persists to `manual_task` (migration 009) — the durable
work queue for human-touch items:

- `gate-manual` — GATE manual disposition (§7 `else` branch): `{doc_id, group_id, payload={flags, tier_attempts}}`. The document is written `staged` in the same tx; the task points at it.
- `discovery-dead-end` — DISCOVER exhausted its sources (§3, S1.3): `{group_id, payload={prefilled_search_links}}`.
- `dead-job-followup` — a dead-lettered job needing human triage.

A task is WORK, never a queue bypass: resolving it **enqueues a job** (`gate.apply`,
`discover.group`, …) and a handler marks the task `resolved` (`resolved_by`,
`resolved_at`) — e.g. `gate.apply` on a gate-manual doc resolves its task. The
review API holds SELECT only: it renders the list and produces jobs, it never
writes the registry (or this table).

## 9. `eudamed.sync`

```python
def handle_eudamed_sync(job):
    export = download_bulk_json(EUDAMED_EXPORT_URL)          # fallback: unofficial API
    db.eudamed_mirror.replace(parse_devices(export))         # udi_di, basic_udi_di, cert refs
    job.finish(result={"devices": n, "matched_to_catalogue": m})   # m = the KPI number
```

No downstream jobs — DISCOVER simply finds a warmer mirror on its next pass.

## 9a. `report.weekly` (S1.5)

```python
def handle_report_weekly(job):
    period_key = job.payload["period_key"]                  # ISO year-week, e.g. "2026-W30"
    expiring = expiring_documents(horizon_days=EXPIRY_HORIZON_DAYS)   # D7: the one 30-day window
    counts = job_counts_by_type_status()
    dead = dead_job_count()
    log_report(period_key, expiring=expiring, job_counts=counts, dead_jobs=dead)
    job.finish()
```

The window is `app.compliance.EXPIRY_HORIZON_DAYS` (30) since 2026-09-11, not
`max(cfg.renewal.horizon_days)`: the office UI's decision D7 puts every
forward "expiring" figure (header strip, `/expiry`'s `ahead` default, this
report) on one window, and the web side's `EXPIRING_WINDOW_DAYS` is that same
constant. The report's already-expired list keeps no lower bound. Both were 30
before too; the difference is that a TOML override of the renewal horizon now
moves the scheduler's chase and not the report. Each row carries `manufacturer`
(the document's confirmed one, else its production items' group) and links to
`/documents/{doc_id}` in the HTML copy.

Read-only snapshot (payload immutability, C9 — a report reflects "now", not enqueue time). No writes, no downstream jobs. `expiring_documents` is the same query the expiry-horizon scan uses to decide whether to emit `email.request` — the scan and the report share the function but run on independent schedules (scan: `Scheduler.expiry_scan_interval_hours`; report: weekly). Plain text/email stub per PRD §4/§B — rendering is not the product. Full design: `docs/specs/scheduler.md`.

Since migration 020, `expiring_documents` also follows `document.cert_doc_id`: a `validity_to` of null is no longer unconditionally "never expiring" — a DoC that cites a held certificate inherits that certificate's `validity_to` (COALESCE), because Annex IV requires only a date of issue on a DoC while Article 56 caps a notified-body certificate at five years. A doc with no `cert_doc_id` at all (Class I, or the certificate isn't held yet) is unaffected and still never appears.

## 9b. `eudamed.certregister`

**EUDAMED holds zero declarations of conformity.** This handler and `eudamed.sweep`
below can only ever tell us a certificate exists, that its standing changed, or
that a device is registered — never supply the document. Both write side
tables only and neither emits a next job (invariant 1, asserted by test).

```python
def handle_eudamed_certregister(job):
    # payload: {}
    records = []
    for page in range(MAX_PAGES + 1):                        # 16 calls at size=300, ~4.608 rows
        body = fetcher.get(EUDAMED_CERT_URL.format(size=300, page=page)).json()
        records.extend(_devices(body))
        if _is_last(body, records[-len(_devices(body)):]): break

    for c in records:
        if not c["certificateNumber"] or not c["actorSrn"]:
            result.count("skipped_incomplete"); continue      # both are PK components
        db.eudamed_certificate.upsert(c)                      # keyed (certificate_number, revision_number, actor_srn)
        result.count("certificates")

    candidates = candidate_aliases()                          # manufacturer_alias + manufacturer, grouped by canonical_name
    for actor_srn, actor_name in distinct_actors(records):
        canonical, via, score = match_actor(actor_name, candidates)
        if canonical is None:
            result.count("unmatched_actors"); continue
        status = "auto" if via == EXACT_VIA else "pending"    # the SRN trust rule
        db.manufacturer.upsert(canonical_name=canonical)      # RULING 8: guarantee the FK target first
        db.manufacturer_srn.upsert(canonical, actor_srn, actor_name, via, status, score)
    job.finish(result)                                        # no downstream job — a leaf
```

**SRN discovery is a by-product of the certificate pull, not a prerequisite for
it** (measured 2026-08-26): `actorSrn` arrives on every certificate record, so
one 16-call whole-register pull both mirrors the register and attributes actors
in the same job.

**Example payload:** `{}` — no manufacturer, no filter. Dedupe key
`eudamed.certregister:{period_key}` (scheduler, monthly).

## 9c. `eudamed.sweep`

```python
def handle_eudamed_sweep(job):
    # payload: {"manufacturer": "IVOCLAR"}
    manufacturer = job.payload["manufacturer"]
    srns = trusted_srns_for(manufacturer)                     # status IN ('auto','confirmed') only

    if not srns:                                              # article-probe fallback (GC EUROPE: 0 certificates)
        if manufacturer_not_yet_probed(manufacturer):
            found = probe_srn(manufacturer)                   # one call, gate 1: exact reference, gate 2: exact manufacturer name
            srns = trusted_srns_for(manufacturer) if found else []
        else:
            result.count("no_trusted_srn"); job.finish(result); return

    for srn in srns:
        for page in range(SWEEP_MAX_PAGES + 1):                # ~38 pages at size=300 for Ivoclar
            body = fetcher.get(EUDAMED_SRN_URL.format(srn=srn, size=300, page=page)).json()
            devices = _devices(body)
            _assert_srn(devices, srn)                          # raises if a device belongs to a DIFFERENT srn
            for d in devices:
                if not d["primaryDi"]:
                    result.count("skipped_no_udi_di"); continue
                db.eudamed_mirror.upsert(d, manufacturer_srn=srn)  # first_seen absent from the upsert -- it IS the delta
            if _is_last(body, devices): break

    db.eudamed_sweep_state.upsert(manufacturer, last_swept_at=now(),
                                   due_at=None, released_at=None, released_by=None)
    job.finish(result)                                         # no downstream job — a leaf
```

**The SRN trust rule, stated once, here where a handler author will read it
before writing another consumer of `manufacturer_srn`:** only rows with
`status IN ('auto', 'confirmed')` are ever swept. `pending` (a fuzzy match
awaiting a human at `/manufacturers/srn-queue`) and `rejected` are never
trusted — attribution is a name match, and sweeping under a wrong SRN mirrors
another company's registered catalogue under our manufacturer's name.

**Enqueued exclusively by a human.** No scheduler and no handler ever enqueues
`eudamed.sweep`. The scheduler tick only marks `eudamed_sweep_state.due_at`;
the operator's release button (`POST /manufacturers/{canonical_name}/sweep`) is
its sole producer, dedupe key `eudamed.sweep:{canonical_name}`, refused with
400 when the manufacturer holds no trusted SRN.

**Example payload:** `{"manufacturer": "IVOCLAR"}`.

## 9d. `scheduler.tick` (2026-08-31)

The cron cadence as queue state. Seven perpetual rows, one per cron, seeded by
migration 055 and re-armed by every worker on start.

```python
def handle_scheduler_tick(conn, job):
    cron = job["payload"]["cron"]            # closed set, see CRONS below
    if cron not in CRONS:
        raise LookupError(...)               # loud: payload and table have drifted
    result = CRONS[cron](conn, load_config(), now_utc())
    queue.defer(conn, job["id"], POLL_SECONDS[cron])
    return {"_deferred": True, "cron": cron, **result}
```

**Payload** `{cron}` · **dedupe** `scheduler.tick:{cron}` · **reads/writes**
`scheduler_run` · **emits** whatever the named cron emits.

Four things about it that are not obvious:

1. **It never completes.** `queue.defer` puts it back to `pending` and refunds
   the attempt the claim charged, so the row is always in the active-only
   dedupe index and can never be duplicated — and a perpetual tick never walks
   its way to `dead` just by running.
2. **`run_after` is a poll interval, not a fire time.** `scheduler_run` remains
   the sole authority on whether a period has fired. Defer precision does not
   matter, drift is harmless, and a late poll fires the same period the old
   wall-clock loop would have. Two polls inside one period fire once.
3. **Isolation is structural.** One job per cron, so a raising cron backs off
   and dead-letters *itself* while the other six stay claimable. This replaces
   the per-cron `try/except` inside `app/scheduler.py`'s `tick()`.
4. **`dead` frees the key.** That is deliberate, not an accident of the index:
   it is what lets a worker restart revive a cron that dead-lettered
   (`runner.arm_crons`). A cron that stops is visible on `/dead` and recovers on
   the next restart.

**Bound:** `claim`'s visibility timeout is 300s, so no cron may take longer than
that or a second worker could claim the same tick. All seven are sub-second
in-DB work; that is a constraint on what a cron may grow into.

**Crons and their poll intervals** — each a fraction of its own period:
`ingest.monthly` 6h · `expiry-scan` 1h · `failure-monitor` 1h · `report.weekly`
1h · `email.poll` 15m · `eudamed.certregister` 6h · `eudamed.sweep-due` 1h.
`eudamed.sweep-due` has no ledger at all (RULING 45) and its `due_at` write is
an idempotent UPSERT, so polling *is* its correctness model.

---

## 10. Phase 2 tags (contract sketch only)

**`email.request`** `{doc_id}` — expiry within horizon → look up manufacturer contact → draft renewal mail (send policy: `draft-for-approval`) → `renewal_request(state=requested)` → enqueue `email.reminder` with `run_after = now()+N days`.

**`email.reminder`** `{request_id}` — state still `awaiting` → reminder or escalate to human. Loop closes when `email.poll` → `extract.doc` → gate promotes a superseding doc → request state `parsed`.

**`playbook.reonboard`** `{manufacturer_id, reason:"discovery-failure-spike"}` — notification + onboarding-agent workflow; human approves new playbook version in git.

---

## 11. Worked chain — one item, end to end

```
ingest.run(csv LJ export)                 mfr_ref "613289" mapped by adapter
  └─ resolve.group(IT-88412)             Ivoclar alias hit, name-family 0.93 → group 4407
       └─ discover.group(4407)           known-url miss → eudamed miss → search hit (2 urls)
            ├─ fetch.url(url_1)          new hash → archived
            │    └─ extract.doc          T0: type/regulation/REF list · T1: dates (Batch API,
            │         │                  batch_ref row, self-reschedule) · T2: basic_udi_di
            │         └─ validate.doc    REF pair-overlap ✓, dates sane, supersedes 8801
            │              │             (same subject, same type, same regulation — C6 ✓)
            │              └─ gate.candidate  score .88 → doc STAGED, links staged
            │                   └─ gate.apply(approve, marta) → doc PRODUCTION,
            │                        ref-list links → production; 8801 superseded; audit ×2
            │                        (each with job_snapshot — C8)
            └─ fetch.url(url_2)          304 → done
```

Result: 14 `item_document` rows → doc 9912 (ref-list links production), file at `/Ivoclar/DOC/3f9a1c2e0b77__DoC_Tetric_PowerFill_(MDR).pdf`, every field evidenced, chain 8801→9912 preserved. Next month: `discover:{4407}:{cycle+1}` enqueues normally (C2), recency window makes it free (AC5).
