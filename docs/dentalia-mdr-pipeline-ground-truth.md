# Dentalia — MDR Document Pipeline: Planning Ground Truth

v1 — 2026-07-03
Status: **decided** — V3 tiered script pipeline, evolving into V4 playbook-compiled.
Supersedes exploration in `dentalia-doc-pipeline-variants.md` (kept as reference/appendix).

---

## 1. Decision

Build a **script-first pipeline with tiered AI extraction (V3)**, evolving into a **playbook-compiled architecture (V4)** where per-manufacturer recipes progressively replace AI calls with deterministic crawls and template extraction.

Working label: **eventually deterministic** — deterministic-by-default, AI-on-exception. Every manufacturer starts on the probabilistic AI path; once its playbook is approved, it migrates permanently to the deterministic one. The deterministic share of the catalogue grows monotonically and is reportable as a monthly KPI.

Scope: replaces the manual workflow (Google → manufacturer site → download PDF → read validity → type link + date into DB) across both catalogues: **16k items Ljubljana, 100k items Zagreb**.

## 2. Chosen architecture

```
[1] READ        item DB / CSV → normalize → dedupe across catalogues → group → resolve manufacturer   (script)
[2] DISCOVER    known-URL recheck → playbook crawl → search API + LLM candidate ranking               (deterministic first)
[3] FETCH       queue: politeness, per-domain rate limits, hash, dedupe, archive, resumable           (script)
[4] EXTRACT     T0 regex/templates (free) → T1 text LLM → T2 vision LLM → human                       (tiered, escalate on low confidence)
[5] VALIDATE    REF-code match (hard requirement), date sanity, supersession check                    (script)
[6] WRITE       confidence-gated: auto-write + audit log | review queue | manual queue                (script)
```

### Non-negotiable design principles

1. **Change detection separates sweep from monitor.** Unchanged documents are never re-fetched or re-parsed (conditional GET / content hash / stored expiry windows). Monthly cost = f(delta), never f(catalogue).
2. **Confidence gating, not autonomy.** High → auto-write with audit entry; medium → review queue (one-click approve); low/not-found → manual queue with prefilled search links. No unattended correctness claims on compliance data.
3. **Evidence on every value.** Each written date carries: source URL, page number, verbatim date string read, extraction tier, confidence, timestamp. This is the audit trail and the product's core differentiator.
4. **REF-code match is a hard gate for auto-write.** Name similarity alone caps at review queue. Wrong-doc-high-confidence is the worst failure class in a medical catalogue.
5. **DB access behind an adapter interface.** Direct DB adapter *or* CSV import/export are interchangeable implementations; the project cannot be blocked by the incumbent vendor. Ljubljana and Zagreb may require separate adapters (unconfirmed whether same system).
6. **Source priority is data, not code.** Per item group: known URL → playbook → manufacturer search → vendor/distributor → EUDAMED → web archive (link-rot recovery only, flags for review) → manual. Order adapts per manufacturer/vendor from observed hit rates; vendor-first is legitimate where distributor doc libraries are stronger.
7. **Never downgrade.** A found document older than the one on file is flagged, never written.
8. **No date found → flag, never infer.** Expiry semantics are per document type (see §7.1); absence of a date can be correct (Class I).

### V4 evolution (phase 2, not a separate build)

Per-manufacturer **playbooks** (versioned JSON in git): document locations/URL patterns, naming conventions, REF formats, layout templates for date extraction, direct-crawl recipe. Built semi-automatically — a frontier agent explores each site once at onboarding, drafts the playbook, human approves. Playbook-covered manufacturers skip the search API and most AI entirely. Pareto: top 20–30 manufacturers likely cover the majority of 116k items. Site redesigns are detected via per-manufacturer failure-rate monitoring → re-run onboarding.

## 3. Evaluated and rejected options

### V1 — n8n orchestration — rejected
Suggested in the client meeting. Legitimate for 5-node linear automations; wrong here because:
- The workflow is not linear: tiered fallbacks, confidence routing, dedup, per-domain retry — as a visual graph this collapses into Code nodes: JavaScript in text boxes, no tests, no version control.
- No real queue semantics (partial retry, dead-letter, backoff); debugging a 15k-item run in the execution-log UI is impractical.
- **Changes nothing about cost** — the LLM node calls the same APIs at the same prices; n8n is orchestration, the cost lives in discovery + extraction. Self-hosting saves the subscription, not the AI bill. Cloud plans bill per execution and are strictly worse at this volume.
- Effort is deceptive: a demo is quick, a production system is not — V3 effort with worse maintainability and V2 running costs.
Diplomatic reuse: acceptable skin for the notification/reporting edges (weekly expiry-report email).

### V2 — full-AI agentic loop — rejected as product, retained as instrument
One frontier agent per item group (search + fetch + native PDF + extract in a single call). Rejected because:
- **Cost at confirmed scale:** sweep €1,400–9,000; steady state €90–450/month even with change detection (~€1,000+/month naive) — 10–50× the chosen design, forever.
- **Nondeterministic and unauditable:** same item can resolve differently across runs; "why this PDF" has no defensible answer for a compliance record.
- **Silent failure modes:** confidently records issue dates as expiry dates, wrong-variant documents — invisible in the output, discoverable only by human verification.
- Rate limits and politeness make a 15k-doc agentic sweep a multi-week affair anyway.
**Retained as:** (a) the Phase 0 spike — a one-sitting build over the ~100-item sample to learn the document landscape and produce the error catalogue that justifies scope; (b) the dev-time onboarding agent that drafts V4 playbooks. Frontier intelligence is spent once per manufacturer, not per document forever. Note: a properly built V2 shares ~40% of V3's skeleton (jobs table, grouping, page selection, validation gate), so the spike is not throwaway.

### Cowork — rejected
Wrong billing shape and wrong execution model: subscription quota in rolling 5-h windows, sessions are human-initiated and run only while the desktop app is open — no headless cron. Sweep would saturate a Max 20x plan for months; steady state ≈ $200/month plus ~5–10 h/month of human session-driving vs €10–40 unattended. Cowork prices human-driven sessions; this system's design goal is removing the human from ~98% of the loop.

### V5 — local models (self-hosted) — deferred, kept in back pocket
GPU/CPU box at €30–80/month roughly breaks even against the cheap-API T1 bill while adding quality-validation and maintenance burden. Revisit only if volume grows materially or Dentalia objects to external AI APIs processing catalogue data (open item #9). The tiered design makes T1 swappable without touching anything else.

### Full fixed-price / big-bang delivery — rejected
Phased delivery instead: Phase 0 → 1 → 2, each independently valuable, so Ljubljana is a self-contained proof before Zagreb is committed.

## 4. Cost baseline

Assumptions: est. 8,000–20,000 distinct documents after cross-catalogue dedupe; 400–800 manufacturers; delta 500–1,500 docs/month. FX ~1 USD ≈ €0.9. **All figures re-baseline after Phase 0 confirms real counts.**

| | Chosen (V3→V4) | V2 full-AI | n8n | Cowork |
|---|---|---|---|---|
| Relative build effort | baseline | ~⅓ of baseline | demo cheap, production dearer than baseline | n/a |
| Initial sweep | €250–850 (Claude tiers) / ~€20 (cheap-model tiers) | €1.4–9k | = V2 (same calls) | months of saturated $200 plan + operator time |
| Steady state / month | €10–40 → trending to €5–20 as playbook coverage grows / €2–8 on cheap tiers | €90–450 (with change detection) | = V2 + hosting | ~$200 + 5–10 h/month human driving |
| Audit trail | full, per value | poor | poor | none |

Wall-clock note: the initial sweep runs 1–2 weeks unattended at polite crawl rates. Stated up front, so nobody expects a same-day sweep.

## 5. Model strategy

Baseline (Anthropic, July 2026 pricing): Haiku 4.5 $1/$5 per MTok for discovery ranking + T1; Sonnet 5 $3/$15 for T2 vision; Opus/Sonnet for the dev-time onboarding agent. Batch API (−50%) for all sweep work — latency irrelevant.

Cheap-model swap, pending Phase 0 quality diff:
- **DeepSeek V4 Flash** ($0.14/$0.28) → discovery ranking + T1 text extraction (~10× cheaper than Haiku). Text-only.
- **Qwen3.7-Plus** ($0.32/$1.28) → T2 vision (only credible cheap vision option). Caveat: single-provider (Alibaba) — data-routing question for a medical client must be cleared with Dentalia first.
- **GLM-5.2 / DeepSeek V4 Pro / Qwen3.7-Max** → evaluated for the onboarding agent; rejected — savings are €20–80 one-time, not worth the eval effort. Keep Claude for dev-time agentic work.
- Open-weight models via OpenRouter with **pinned US/EU providers** (DeepInfra, Fireworks) to avoid routing catalogue data to Chinese endpoints; watch quantization variance (pin fp8, avoid silent fp4 routing).

Phase 0 protocol: run the same ~200-doc sample through Haiku, DeepSeek Flash, and Qwen-Plus; diff extractions against human-verified ground truth. Swap tiers only on demonstrated parity. Costs a few euros.

Architectural side effect of cheap tiers: T0 regex becomes optional on cost grounds — retained anyway for determinism and audit value on playbook-covered manufacturers, but exhaustive regex tuning is deprioritized (~5–10 dev hours saved).

## 6. Positioning (one-liners that survived the meetings)

- "It's ETL where the E has to be found, the T can be wrong, and the L can get you fined — everything beyond standard ETL exists for those three."
- "Eventually deterministic: the AI is scaffolding, not the engine. Every source it figures out gets compiled into a deterministic recipe; watch the percentage climb monthly."
- "Full-AI brute force costs about a thousand euros a month to run; built properly the smart parts are a one-off and the running cost is a tenner a month."
- On the one-sitting claim: "Right — and I'll bring that version to show you. It reproduces the manual process, including its mistakes, just faster. The work that follows is what makes the output trustworthy enough for an inspector." The build is cheap now; what's hired is knowing which outputs to distrust.
- At Zagreb scale the manual process has already silently failed — the pitch is provable coverage with an audit trail, not speed.

## 7. Key risks & domain traps (top of list; full catalogue in variants doc §6)

1. **DoC vs certificate semantics.** MDR Declarations of Conformity typically carry issue dates, not expiries; expiry lives on notified-body certificates (max 5 y); Class I devices have no certificate. Extraction schema: `doc_type`, `issue_date`, `expiry_date (nullable)`, `cert_number` + explicit no-expiry policy. **Audit what the existing "validity date" column actually contains** — likely a mix.

   **This trap MATERIALIZED on 2026-08-12** (GC pilot, found by Denis on document #239) and the entry above did not prevent it, because nothing in the pipeline encoded it. Chapter and verse, so the next person does not have to re-derive it:

   | Document | Dates it carries | Source |
   |---|---|---|
   | **DoC** | **Issue date only.** Annex IV point 10 requires "the place and date of issue of the declaration, name and function of the person who signed it… and signature". There is **no expiry element on a DoC at all** — it stays valid while the device and any cited certificate do. | MDR Art. 19 + Annex IV |
   | **EC certificate** | Date of issue **and** expiry; validity capped at 5 years, extendable in further ≤5-year periods. | MDR Art. 56 + Annex XII |
   | **ISO / QMS certificate** | Issue and expiry, often plus an "original certification" / "current issue" date. | observed in corpus (doc 119: `Current Issue Date` + `Expiry Date`) |

   So the expected dates are a **function of `document.type`**, which extraction already produces. The failure was that a DoC's Annex IV point-10 line (`Leuven, 12/02/2026`) was stored as `validity_to`, making three documents read as expired — 3 of the 5 rows on the expiry board were false alarms. Note the asymmetry that caused it: the T1 prompt warned against the signing date on `validity_from` and said nothing on `validity_to`, so the guard on one field pushed the error into its unguarded neighbour.

   Prompts tightened on 2026-08-12; the deterministic, type-conditional guard is followup `[validity-date-guard]`. Worth copying EUDAMED's own certificate business rules verbatim: *expiry must be after date of issue*, and *date of issue must not be in the future*. **Verbatim Annex IV/XII text was not retrieved** (three fetch attempts returned navigation-only pages or 404) — the wording above is consistent across secondary sources but should be confirmed against EUR-Lex before it is built into type-conditional validation.
2. **Matching:** multi-REF documents, REF format drift, private-label rebranding (manufacturer on doc ≠ brand in DB — the mapping may exist only in someone's head).

   **`mfr_ref` is not a pivot — closed by Denis, 2026-08-14.** The design opened with the REF gate keyed on `mfr_ref` (`Dobaviteljeva št. artikla`), which made its coverage a headline number: 44,4% of the 15.958 mirrored items carry none, and `Ingest.mfr_ref_source_by_code` existed to point the field at some other BC column and pull that down to 22,6%. **Do not build that.** The item-to-manufacturer relation is held by BC's own `artikli` table, not reconstructed from an article number, and `mfr_ref` was only ever a *check* on a relation we already have. Client ruling C12 (2026-08-13) had already made `item_ref` a first-class comparand at basis `ref-item`; this ruling finishes the job by removing the pressure to rescue the other column.

   Consequences, so nobody re-derives them:
   - `[ingest-mfr-ref-fallback]`'s fill-only mode is **not wanted**. The 156 Straumann pack variants it was designed to protect are moot.
   - "missing `mfr_ref` %" is a **descriptive** statistic, never an acceptance target. §9 AC1/AC3 must not be renegotiated around it, and the §7-item note in §1 C1 about "which BC field supplies it" stops being a blocking client question.
   - Invariant 3 is unchanged and still correct: the pair is `(canonical_manufacturer, catalogue article number)`, and `item_ref` satisfies it — better than `mfr_ref` does (0 cross-manufacturer collisions vs 34, 0 prose values vs 64, strictly larger reach). Nothing here loosens the pair requirement; it only settles which number carries it.
3. **Fetch reality:** bot walls (Playwright fallback tier), login portals (manual queue), scanned PDFs (T2 handles implicitly), site redesigns (failure-rate monitoring).
4. **Dates:** DD.MM.YYYY vs MM/DD, month names in 5 languages (EN/DE/IT/SI/HR), "valid 5 years from issue" arithmetic.
5. **DB access unverified** for both catalogues — mitigated by adapter interface (§2.5), but still the engagement's biggest external dependency.
6. **An early expectation that this is "one sitting" of work** — answered by the Phase 0 demo, whose error catalogue shows what the rest of the work is for.

## 8. Phasing

| Phase | Hours | Deliverable |
|---|---|---|
| **0 — Spike** | 10–14 | Agentic run over CSV samples (~100 items LJ + ~200 ZG). Findings memo: real counts, doc landscape, error catalogue, model quality diff, confirmed assumptions. Probes both DB-access gates via the export request. |
| **1 — Core pipeline, Ljubljana** | 45–65 | V3 on 16k catalogue, top ~20 manufacturers. Queue, tiers, review UI (one table page), CSV in/out, audit log. Demo: verified coverage numbers + live review queue. |
| **2 — Zagreb + playbooks** | 30–50 | Zagreb adapter, cross-catalogue dedupe (LJ results pre-warm ZG), playbook infra + top manufacturers, fallback tuning, multi-week sweep hardening. |
| **Ongoing** | continuous | Monitoring cron, review-queue support, playbook repairs, deterministic-coverage KPI report. Genuine monthly work at Zagreb volume. |

Roughly two months end to end, delivered phase by phase.

## 9. Acceptance criteria (registry — G15)

Measured on the Ljubljana pilot set. Quantitative targets come from sweep data rather than being fixed in advance, so "done" stays measurable instead of open-ended.

**S1.7 disposition (Denis, 2026-07-24 for AC1; extended to the set 2026-07-29): measure-only** for the quantitative ACs — the first sweep reports each number, no pass/fail target is set until BC/IT confirms the `mfr_ref` source (G4). The structural ACs are correctness, not tuning: they are pass/fail by construction and a failure is a bug, not a renegotiable target.

| AC | Criterion | Measured by | S1.7 disposition |
|----|-----------|-------------|------------------|
| AC1 | REF-gate auto-write coverage: MD items with a production document link | K1 (dual denominator: strict `md_flag IS TRUE` + processed-scope `IS NOT FALSE`) | **measure-only** — report vs both ceilings (~8% strict / ~25% per-supplier mapping, findings-phase0 §G4) |
| AC2 | Every auto-written value carries the full evidence tuple `(archive_url, page, verbatim, tier, model_id, confidence, extracted_at)` | GATE rejects candidates without it (invariant 2) | structural — 100%, pass/fail |
| AC3 | Audited error rate on a sample of auto-written links | human review of a sampled set | measure-only — report the rate; threshold X deferred |
| AC4 | Manufacturer-scope cert (QMS / `mfr-scope`, C4): one human binding → N derived links, no per-group staging flood | manual queue + K4 | structural — pass/fail |
| AC5 | Re-run idempotency: a second cycle over unchanged content re-emits 0 jobs and spends ≈ €0 | two-cycle simulation (C2/C3) | structural — pass/fail (proven at fixture scale: emitted 0 / seen 22, spend delta €0) |
| AC6 | Discovery reach: share of processed groups that reach a fetched candidate document | `discovery_log` + K1 funnel | measure-only |
| AC7 | Sweep cost within the expected envelope | K8 (spend vs cap, EUR via `Budget.eur_per_usd`) | measure-only — informational |
| AC8 | Invariant integrity: 0 `name-family`/`fetch-context` links written as `production`; 0 downgrades; supersession only within identical `(coverage subject, type, regulation)` | K2 + GATE audit trail (invariants 3/4/5) | structural — must be 0 |
| AC9 | Staging burden stays reviewable: manual-queue + staged-link volume does not swamp the reviewer | K4 / K6 | measure-only |

Each AC's number is read off the KPI board (`docs/specs/kpi.md`) or the GATE audit trail — never a raw table (G14). When BC/IT confirms the `mfr_ref` source, AC1/AC3 targets are renegotiated from the measured baseline and this table gains a target column.

## 10. Open items blocking planning finalization

> **Answered 2026-08-19 (Denis, emphatic; closes PHASES.md G17 and G11): there is no separate Zagreb.**
> One Business Central, one article numbering, one pipeline. More items still arrive from the Zagreb
> operation — the catalogue grows to roughly 100k on the same numbering — so the **scale** work is real
> and unchanged. What never existed is the *second system*: no ZG SourceAdapter, no LJ/ZG field mapping,
> no cross-catalogue dedupe, no LJ ∩ ZG overlap to measure. This ratifies the LJ/ZG-convergence statement
> of 2026-07-29, which sat recorded but unconfirmed for nineteen days while three followups went on sizing
> themselves against a second system. Items 1, 3 and 4's Zagreb clause are struck below; item 10 remains a
> commercial question, not a technical one.

1. ~~Cross-catalogue overlap % (LJ ∩ ZG) → real distinct-doc count~~ — **struck 2026-08-19**: one catalogue, nothing to overlap
2. Item:group ratio and manufacturer counts per catalogue
3. ~~Same system/DB for both markets, or two integrations? Is Zagreb on the Grails app?~~ — **answered 2026-08-19**: one system
4. Contents of the current "validity date" field (issue vs expiry vs mixed) ~~; is Zagreb's data maintained at all?~~ — the Zagreb clause is struck; the validity-date question stands
5. DB read/write access per catalogue, or CSV as v1 interface
6. Sample exports (~100 LJ + ~200 ZG) with current doc links → Phase 0 input
7. Named review-queue owner
8. Hosting: a VPS we run vs their own infrastructure
9. Policy on external AI APIs processing catalogue data (gates the cheap-model swap and V5)
10. Whether both entities are handled together or Zagreb separately — **technically moot; it changes nothing in the build**
