# Dentalia — Document Retrieval & Compliance Warehouse: Workflow Ground Truth

v2 — 2026-07-03 (decisions round + UDI/EUDAMED research)
Supersedes v1. Companion to `dentalia-mdr-pipeline-ground-truth.md`; architecture decisions there hold.

---

## 0. Decision log (2026-07-03)

| # | Question (v1 §7) | Decision |
|---|---|---|
| 1 | MD flag in BC | **Assume present.** Working assumption, not confirmed — keep as a listed assumption so it is revisited rather than assumed if it turns out to live in side spreadsheets. |
| 2 | BC access | **Exports first (CSV/Excel), API later.** Source must be swappable via config — adapter interface is now a hard requirement, not a mitigation. |
| 3 | Item grouping | Client asserts grouping details exist **in the documents**. Action: examine sample docs. Likely candidate: Basic UDI-DI (see §6) — the regulation-defined group key printed on newer certs/DoCs. |
| 4 | Product class | **In BC.** Mirror it; source of truth stays BC. |
| 5 | Archive location | **Google Drive for now.** Evaluate S3/S3-compatible in parallel (§4.3). Design storage behind the same adapter pattern as the DB source. |
| 6 | Document URLs | **Both:** `source_url` (manufacturer origin) + `archive_url` (our served copy). Plus **ETag / content-hash tracking** — see §4.2. |
| 7 | Email channel | **Dedicated mailbox exists.** Two automated flows: inbound AI classification of attachments (escalate to human on low confidence) and **outbound automated renewal requests before expiry**. §2.1. |
| 8 | Backfill | **Full corpus in scope.** Baseline: the manual process costs about two days a month of one proficient person. Target is "smart and clean", not "manageable". |
| 9 | Warehouse role | **System of record for compliance. Trust is the product.** No writeback to BC except possibly file references. Both integration directions (push refs to BC / BC pulls from us) must remain architecturally open — expose a read API from day one, wire nothing yet. |
| 10 | UDI | Researched — findings in §6. Partial adoption today, but mandatory EUDAMED registration is closing the gap on a legal timetable through Nov 2026 / May 2027. |

## 1. System landscape

```
[Supplier price lists / availability catalogues (Excel)]
    → Excel prep → Business Central (item master: IDs, VAT, class, MD flag*) → webshop

[Compliance warehouse (this system)]
    SourceAdapter  ── CsvExportAdapter (v1) ──┐
                   ── BcApiAdapter    (v2) ──┤→ pipeline → registry (system of record)
                                              │
    StorageAdapter ── GoogleDriveStore (v1) ──┤
                   ── S3ObjectStore   (v2) ──┘
```

\* MD flag assumed present in BC — listed assumption.

Adapters are config-switched; nothing downstream of READ or FETCH may know which implementation is live. This was a resilience principle in the architecture doc; it is now a stated client requirement.

## 2. Acquisition channels

| # | Channel | Mode |
|---|---|---|
| 1 | Manufacturer website | Automated (playbooks, tiered discovery) |
| 2 | EUDAMED | Deterministic where registered — coverage growing on a regulatory clock (§6) |
| 3 | Email | Semi-automated agent, both directions (§2.1) |

### 2.1 Email agent (expanded scope)

**Inbound:** AI reads attachments arriving at the dedicated mailbox → extracts through the same tier stack (T0/T1/T2) → classifies (type, coverage, validity) → confidence-gated like every other source: high → registry candidate with evidence; low → human queue with the email + extraction attempt attached. The mailbox is just another FETCH source; no special pipeline.

**Outbound:** scheduled job scans `validity_to` horizon (e.g. 90/60/30 days before expiry) → drafts renewal-request email to the manufacturer contact → state machine per request:

```
due → requested → awaiting → received → (parsed → superseding doc linked)
                          ↘ no-response after N days → reminder → human escalation
```

Send policy (auto-send vs. draft-for-approval) is a config flag — start with draft-for-approval to build trust, flip later. Manufacturer contact addresses become registry data (per-manufacturer record, alongside playbooks).

This closes the loop the manual process never had: today expiry is discovered when someone looks; here the system requests replacements before expiry without being asked.

## 3. Parsing requirements

Unchanged from v1 (coverage / validity / type incl. MDD-MDR / format / cross-references), with one addition:

- **Grouping resolution order:** REF list → Basic UDI-DI (when printed on the doc) → name/family match → human. Client says grouping details are in the documents; Basic UDI-DI is the standardized form of exactly that claim. **Action: pull 5–10 sample docs across manufacturers and verify which grouping signals actually appear** — this calibrates the matcher before any code.

## 4. Fetch, dedup, storage

### 4.1 Linking (unchanged)
One document ↔ N items; all covered items reference the same stored file; supersession chain append-only, 10-year retention.

### 4.2 Fetch ledger (new — ETag / hash discipline)

Every fetch attempt is recorded in a `fetch_log` keyed by normalized URL:

| Field | Purpose |
|---|---|
| `url_normalized` | dedupe key |
| `etag` / `last_modified` | conditional GET (`If-None-Match`) — never re-download unchanged files |
| `content_hash` (sha256) | true identity of the payload; detects same doc under different URLs |
| `fetched_at`, `last_checked_at` | recency window: "checked this URL 3 weeks ago" → skip |
| `doc_id` | link to registry entry, nullable |

Two payoffs beyond bandwidth:

1. **Recency skip:** the fetcher consults the ledger before any network call — a URL checked within the window is not touched. Monthly cost stays f(delta).
2. **Orphan connection:** when a document doesn't state which items it covers, but the *same URL or same content hash* was previously fetched in the context of item X, that context is evidence for linking. `match_basis` gains a value: `fetch-context`. Kept below the auto-write gate (goes to review queue) — contextual, not documentary, evidence.

### 4.3 Storage: Drive now, object storage evaluated

Drive stays v1 (client-owned, zero migration). Evaluation criteria for v2, in order of importance for a compliance system of record:

- **Immutability:** S3 Object Lock / versioning gives a *provable* append-only guarantee; Drive can only promise it by policy. For a 10-year retention duty defended in front of an inspector, WORM-capable storage is a materially better story.
- **Programmatic control:** presigned URLs for `archive_url` serving, lifecycle rules, hash verification on upload.
- **EU data residency:** Hetzner Object Storage (Falkenstein/Helsinki), Scaleway (Paris), Cloudflare R2 (EU jurisdiction option), AWS S3 eu-central-1. At realistic volume (tens of GB) cost is €1–5/month everywhere — the decision is residency + immutability, not price.
- **Migration cost:** behind StorageAdapter, moving Drive → S3 later is a copy job, not a redesign.

Recommendation to carry: start Drive, present the immutability argument when the "system of record" framing lands with the client — it sells itself.

## 5. Target data model (v2)

**`document`** — unchanged from v1 plus:
| Field | Notes |
|---|---|
| `basic_udi_di` | nullable — group key when printed on the doc (§6) |
| `content_hash` | sha256, dedupe identity |

**`item_document`** — unchanged (`match_basis` gains `fetch-context`).

**`fetch_log`** — new, per §4.2.

**`manufacturer`** — promoted to entity: contact email(s), playbook ref, EUDAMED actor SRN when known, observed hit-rate stats.

**`renewal_request`** — new: `doc_id`, state (§2.1), timestamps, email thread ref.

## 6. UDI / EUDAMED — research findings (2026-07)

The regulatory ground shifted **five weeks ago** and directly benefits this system:

- **Mandatory since 28 May 2026:** Commission Decision (EU) 2025/2371 (OJEU 27 Nov 2025) declared the first four EUDAMED modules functional; per Regulation (EU) 2024/1860's 6-month transition, **Actor registration, UDI/Devices, Notified Bodies & Certificates, and Market Surveillance are mandatory to use as of 28 May 2026.**
- **New devices:** must be registered in UDI/Devices *before* being placed on the EU market (from 28 May 2026).
- **Legacy devices** (on market before 28 May 2026, still sold after): must be registered by **28 November 2026** (EUDAMED-DI/EUDAMED-ID scheme for pre-MDR devices).
- **Certificates:** notified bodies must register newly issued MDR/IVDR certificates from 28 May 2026 and upload **existing certificates by ~27 May 2027**.

**Identifier semantics** (answers the client's confusion):
- **Basic UDI-DI** = the *group/family* key: same intended purpose, risk class, essential design. It is what certificates and DoCs reference. This is almost certainly the "grouping in the documents" the client mentioned.
- **UDI-DI** = per-device identifier (the GTIN on the package) — the item-level key, EUDAMED-searchable.
- Mapping to our model: `item.udi` should store UDI-DI; `document.basic_udi_di` stores the group reference; EUDAMED lookup resolves UDI-DI → Basic UDI-DI → certificate records.

**Machine access reality:**
- No official public API expected before ~2027. Official M2M access points exist but are built for manufacturers submitting data and require applying through national authorities — overkill for read access.
- Practical channels today: **bulk JSON export** via the public UI (structured, machine-readable, suitable for periodic sync into a local mirror) and the **unofficial API** documented by OpenRegulatory (github.com/openregulatory/eudamed-api; they run BEUDAMED on it). Treat unofficial endpoints as best-effort with a fallback to bulk sync.

**Strategic consequence:** the "eventually deterministic" thesis gets a regulatory tailwind. Every manufacturer is legally forced to populate exactly the database we want to read, on deadlines inside this project's Phase 1–2 window (Nov 2026, May 2027). Design move: keep a **local EUDAMED mirror** (periodic bulk sync) as a first-class DISCOVER source; its coverage % becomes a KPI that climbs without us spending a cent. Pitch line: *"Part of the discovery problem is being solved for us by EU law — the system is built to absorb that as it happens."*

**Caveat for expectations:** adoption lag is real (bulk upload is burdensome, backlogs expected around the Nov 2026 legacy deadline), and dental consumables skew Class I/IIa where enforcement attention arrives last. EUDAMED is a growing channel, not a today-channel — the pipeline's manufacturer-site path remains the workhorse through Phase 1.

## 7. Remaining open items

1. Examine 5–10 sample documents for actual grouping signals (REF lists vs Basic UDI-DI vs prose) — **next concrete action, feeds matcher design**
2. BC export format: which fields come out in the v1 CSV/Excel export (need: item id, name, manufacturer, MD flag, product class, UDI if present)
3. Mailbox access mechanics: IMAP/Graph API? Send-as rights for outbound?
4. Renewal-request lead times per document type (90/60/30 is a placeholder)
5. Drive folder write conventions if system writes into existing structure (or does the system own a new subtree?)
6. LJ ∩ ZG overlap and per-catalogue counts (carried from architecture doc §10)

## 8. Next steps

1. **Sample doc examination** (item 1 above) — cheap, unblocks matcher design, verifies the Basic UDI-DI hypothesis.
2. **Phase 0 corpus spike** as proposed in v1 §8 — Euronda + Ivoclar folders through the extraction tiers; now additionally verifies grouping signals and MDD/MDR detection on real files.
3. Stand up the **EUDAMED bulk-sync experiment** (few hours): pull the JSON export, check how many of Dentalia's manufacturers/devices already appear — gives a hard number for the "regulatory tailwind" claim instead of an assertion.
