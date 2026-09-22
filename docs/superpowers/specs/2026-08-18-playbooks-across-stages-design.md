# Playbooks across the pipeline — where they are, where they belong

Design proposal, 2026-08-18. **Nothing here is implemented.** Decision document
for approval; each item is independently approvable.

Verified against the code on 2026-08-18 (see §1 for what was read).

---

## 0. Decision summary

| # | Change | Stage | Cost | PRD change? | Rec |
|---|---|---|---|---|---|
| 1 | mtime-checked cache on playbook load | all | S | no | **do first** |
| 2 | Move `cfg.source_priority` into the playbook | DISCOVER | S | no | **do** |
| 3 | Emit `doc_sources[kind:"direct"]` as `fetch.url` | DISCOVER | S | no | **do** |
| 4 | `fetch_policy` block (playwright seed, politeness, UA, max_bytes) | FETCH | M | no | **do** |
| 5 | `rev` on the playbook + recorded on the extraction | EXTRACT | M | additive payload | **do** (prereq for 8) |
| 6 | `corpus_folders` supplier→manufacturer map | BACKFILL | S | no | **do** |
| 7 | Additive lexicons: `date_labels`, `type_markers`, `ref_pattern`, `cert_pattern` | T0 | M | no | **do** |
| 8 | `extract_hints` free text injected into T1/T2 | T1/T2 | M | no | **do, with guards** |
| 9 | ~~`known_representatives`~~ — **superseded 2026-08-18**, which solves it positionally in T0 instead. Reduced to: pass the same signal to T1/T2. | T1/T2 | S | no | fold into 8 |
| 10 | `mfr_ref_pattern` precision filter on link formation | VALIDATE | S | no | consider |
| 11 | Crawl recipe on the dead `playbook` DISCOVER rung | DISCOVER | L | spec first | **design next** |
| 12 | Real `playbook.reonboard` handler | SCHEDULER | M | no | design next |
| 13 | `gate.deny_auto` one-way strictness ratchet | GATE | S | no | consider |
| 14 | Per-manufacturer grouping thresholds | RESOLVE | M | no | **defer** |
| 15 | Per-manufacturer gate thresholds / calibration | GATE | — | — | **no** |
| 16 | Per-manufacturer supersession policy | VALIDATE/GATE | — | — | **no** |
| 17 | Per-manufacturer expiry horizon in the SQL view | REPORT | — | — | **no** (see §8) |
| 18 | FETCH emitting `fetch.url` (crawl inside FETCH) | FETCH | — | yes | **no** (see §4) |

---

## 1. What is true today (verified)

**Loading.** `playbooks/*.json` is read from disk **at runtime on every call** —
no cache anywhere (`app/playbooks.py:128`, `app/extract/t0_layout.py:45`).
`Dockerfile:41` COPYs the dir; `docker-compose.yml:183` bind-mounts it `:ro`, so
an edit takes effect on the next job with no rebuild. Two independent loaders
parse the same file: `load_playbooks()` (identity/URL view, never raises) and
`t0_layout.load_templates()` (parse view, never raises, silently skips a file
with neither `match` nor `ref_strategy`).

**Consumption today:**

| Stage | Playbook use |
|---|---|
| `ingest.run` | none |
| `resolve.group` | indirect — reads `manufacturer_alias`, seeded by `dentalia playbooks sync` |
| `discover.group` | `domains` → `site:` query restriction + post-filter; `doc_sources[kind=portal]` → manual-task prefill only |
| `fetch.url` | **none** |
| `extract.doc` T0 | `extract_manufacturer` (legal name + aliases vs. doc text, abstains on ≥2; since 2026-08-18 the confidence is positional — `MFR_TRUSTED_CONF` 0.97 outside a rep block, `MFR_REP_ONLY_CONF` 0.9 when a rep block is all it saw); `t0_layout.match` → REF parse strategy |
| `extract.doc` T1/T2 | **none** — prompts are 100 % generic |
| `validate.doc` | `ref_normalize` on both sides of the C1 REF comparison |
| `gate.*` | none |
| `backfill.scan` | none |
| `report.weekly` / scheduler | none (emits a no-op `playbook.reonboard`) |

**Coverage of the 10 files:** 5 carry a parse template, 2 carry `ref_normalize`,
3 carry `doc_sources` (4 entries, all `kind:"portal"`), 2 are identity-only stubs.

**Per-manufacturer knowledge living OUTSIDE playbooks (split brain):**
1. `cfg.source_priority` — TOML `[source_priority]`, `{manufacturer: [rungs]}`.
   `app/config.py:408` literally comments it *"Placeholder until playbooks land"*.
2. `domain_lease.needs_playwright` — DB, learned only **after** a 403 costs a
   failed job.
3. ~~Measured manufacturer knowledge encoded as **code comments** rather than data.~~
   **Corrected 2026-08-18:** the change ("trust the manufacturer T0 read,
   unless a rep block is all it saw") turned that finding into working code —
   `_REP_LABEL`, `_named_outside_rep_block`, `MFR_TRUSTED_CONF` (0.97) and
   `MFR_REP_ONLY_CONF` (0.9) in `app/extract/t0_templates.py`. It is *positional*
   (how far a name sits from an authorised-representative label), which is
   strictly better than the per-playbook name list this document originally
   proposed: the code's own comment shows why a name rule cannot work —
   `DENTSPLY IH AB` (Mölndal) and `DENTSPLY IH Inc.` (Waltham) are real
   certificate holders, so any `Dentsply IH` exclusion discards real
   manufacturers. **T1/T2 still get none of this**, which is the part item 8
   must carry.

---

## 2. INGEST — agree, no playbook

Input is a BC export; the manufacturer is not known until RESOLVE. Nothing to do.

**One adjacent case (item 6):** `backfill.scan`'s `_brand(folder)` buckets the
archive by the scan-root folder name, and the corpus folder is the **supplier**,
not the manufacturer — the `DENSTPLY` folder holds Maillefer, Sirona and VDW
documents (BC codes 022, 012, 010). A playbook `corpus_folders: ["DENSTPLY"]`
key lets BACKFILL express "documents here belong to one of these manufacturers"
and bucket correctly instead of by raw folder string.

- **Pro:** removes a known-wrong bucketing; cheap; feeds a prior downstream.
- **Con:** a folder claimed by two playbooks needs the same conflict check
  `validate()` already does for BC codes.
- **Edge:** the SFTP dump gets cleared and re-dumped with renamed folders; an
  unmapped folder must degrade to today's behaviour, never fail.

---

## 3. RESOLVE — reserve the keys, don't build yet

Today: global `cfg.name_accept` / `name_review`, pg_trgm candidate generation +
rapidfuzz `token_set_ratio`. Nothing manufacturer-aware except the
`manufacturer_alias` table playbooks seed.

Plausible keys: `grouping.name_accept` / `name_review` overrides,
`grouping.stopwords` (strip "GmbH", "refill", size tokens before scoring),
`item_ref_pattern`.

- **Pro:** Straumann's implant lines are long and near-identical; one global
  threshold cannot serve both them and a terse-named manufacturer.
- **Con:** per-manufacturer thresholds make grouping non-uniform and hard to
  reason about; a wrong override silently loses recall with no signal.
- **Verdict: defer.** Agreed with your read. Reserve the key names so a later
  addition is additive, but don't author values without a measurement.

---

## 4. FETCH — the biggest hole

Today FETCH receives `{url, domain, group_id, source_rank}` and performs a dumb
conditional GET. Everything it knows about a domain is
`domain_lease.needs_playwright`, and it only learns that **after** a 403 has
already cost a wasted request and a job failure. The user-agent is a module
constant (`app/adapters/fetcher.py:27`). Politeness is one global interval.

### 4a. `fetch_policy` block (item 4 — recommended)

```jsonc
"fetch_policy": {
  "needs_playwright": true,        // authored seed; domain_lease stays the learned fallback
  "politeness_ms": 5000,           // override cfg.fetch.politeness_ms for these domains
  "user_agent": "...",             // some portals 403 the bot UA and serve a normal one
  "accept_content_types": ["application/pdf"],
  "max_bytes": 20000000,
  "auth_ref": "STRAUMANN_PORTAL"   // NAME of a config/env key — never the secret
}
```

- **Pro:** each of these is a real failure you have already hit or will
  (Straumann's 143-page IFU already blew the T1 token wall at
  `app/extract/llm.py:142`). Authored up front, a bot-walled domain costs zero
  failed jobs instead of one.
- **Con:** policy now has two homes (playbook + `domain_lease`). Resolve by
  making the playbook a **seed** and the learned flag a one-way escalation:
  learned-true wins, learned-false never overrides authored-true.
- **Edge:** a domain shared by two manufacturers with different policies — key
  the policy on the domain, not the playbook, and let `validate()` refuse two
  playbooks authoring conflicting policy for one domain.
- **Hard rule:** playbooks are in git. `auth_ref` is a *reference*; never a token.

### 4b. The catalog / multi-URL problem

This is the structural gap you named. Today `fetch.url` = one URL = one document.
A manufacturer portal is a **listing page**: one URL yields N document links,
often paginated, often behind a filter form. There is nowhere in the model to
say that. `doc_sources[kind:"portal"]` records these URLs and then only ever
prefills a manual task — a human clicks them.

Three closures, increasing cost:

**(A) Static URL list — item 3. BUILT, and it has run.** `doc_sources`
supports `kind: "direct"`. When this was written `app/playbooks.py:38` parsed it
and nothing read it (`app/handlers/discover.py:252` filtered `kind == "portal"`
only). It is now a rung: `playbook` sits third in
`Discovery.default_source_priority` (`recency, known_url, playbook, eudamed,
search, email, manual`), and emitting `direct` sources as `fetch.url` reuses the
existing enum, handler, dedupe key and hash-dedupe exactly as predicted — zero
new machinery.
*Con:* hand-maintained, goes stale, doesn't scale past a few dozen URLs.
*Verdict:* done. **First live run 2026-08-24**, and it closed the whole spine:
six `discover.group` jobs terminated on the `playbook` rung with
`candidates_seen: 0` (the search rung was never reached), all six resolving the
same authored URL — Renfert's `CONF_6100x000.pdf` from `playbooks/renfert.json`.
One fetched 743.974 bytes over httpx, the rest deduped or hit
`recency-skip-linked`. It came out the far end as **document 843, DoC, MDR,
`production`, valid 2024-01-28 to 2027-01-27, two `item_document` links at
`production`** — the project's first document discovered and fetched from the
open web rather than the corpus.

**(B) `kind:"index"` + link harvest inside FETCH — rejected.** FETCH's PRD Emits
row (§4, outcome table) permits `extract.doc` and `validate.doc` only. FETCH
emitting `fetch.url` is a PRD change, and it puts crawl logic in the stage whose
entire design property is that it is dumb I/O under a ledger. **No.**

**(C) A real crawl recipe on the `playbook` DISCOVER rung — item 11, the answer.**
The rung already exists in the ladder and falls through as a no-op at
`app/handlers/discover.py:398` (marked Phase 2). DISCOVER's Emits row already
lists `fetch.url`. So the legal home already exists and is empty.

```jsonc
"crawl": {
  "index_url": "https://www.voco.dental/en/service/download/...",
  "link_pattern": "\\.pdf$",
  "doc_type_from": {"ifu": "IFU", "konformit": "DoC"},
  "pagination": {"param": "page", "max_pages": 20},
  "max_links": 500
}
```

- **Pro:** the one change that makes a manufacturer's whole document set
  reachable without a human clicking. Keeps FETCH dumb. No PRD change.
- **Con:** it is a crawler, with everything that implies.
- **Edges that need answering in the spec, not now:** a portal behind a POST/form
  filter; a `link_pattern` that matches the whole site; infinite pagination; the
  same PDF at N URLs (already handled — invariant 6 hash-dedupe + C3); **group
  attribution** (a crawled document has no requesting group → `group_id: null`,
  the same shape backfill already produces, so it is survivable); and
  **politeness starvation** — one domain lease serialising 500 URLs will block
  every other group behind that domain for hours.

---

## 5. EXTRACT T0 — mostly right, four additive lexicons (item 7)

Already playbook-driven: `match.anchors` → template selection, `ref_strategy` →
REF parse, playbook names → `extract_manufacturer`.

Hardcoded and manufacturer-blind (`app/extract/t0_templates.py`):

| Constant | What it is | Playbook key |
|---|---|---|
| `_TO_LABELS` / `_FROM_LABELS` (:90, :92) | date-label vocabulary, EN/DE/SL | `date_labels: {from: [], to: []}` |
| `_TYPE_MARKERS` (:47) | doc-type vocabulary | `type_markers: {DoC: [], EC: []}` |
| `_CERT_LABEL` (:129) | certificate-number shape | `cert_number_pattern` |
| `_looks_like_ref` / `_REF_CODE_LINE` (:394, :410) | generic REF shape filter | `ref_pattern` |

`ref_pattern` is the most valuable: Komet's dotted codes and Ivoclar's
letter-suffixed codes already need `ref_normalize` at **compare** time; a pattern
at **parse** time keeps junk out of `ref_list` in the first place and makes the
`t0_ref_template_miss` anomaly mean something.

- **Design rule, non-negotiable: merge, never replace.** A playbook that
  *replaces* the global lexicon is a playbook that silently loses a field the
  generic path would have found. Every addition is a union with the global list.
- **Edge:** two playbook labels that contradict (one manufacturer's "Datum" is a
  from-date, another's is a to-date) — the lexicon is scoped to the matched
  playbook, so this only bites if the manufacturer match was wrong. Which is
  exactly the risk §6 is about.

---

## 6. EXTRACT T1/T2 — you are right, this is the mistake

Verified: `_params(tier, models, doc, missing, max_tokens)` at
`app/extract/llm.py:191` carries **no manufacturer, no group, no playbook**. The
batch path `build_requests(content_hash, tier, doc, missing)` does not even have
`group_id`. Both prompts are fully generic.

Meanwhile the T0 code comment at `t0_templates.py:512-523` holds a measured,
document-specific fact — that `Dentsply IH Limited` (Stonehouse) is the UK
representative in all 41 files it appears in, while `DENTSPLY IH AB` (Mölndal)
and `DENTSPLY IH Inc.` (Waltham) are real certificate holders — that the model
reading those exact documents is never told.

### Proposal (item 8): `extract_hints`, free text, per manufacturer

```jsonc
"extract_hints": {
  "ref_list": "REF codes on this manufacturer's declarations are ISO 6360 bur codes printed FIGURE.SHANK.SIZE (e.g. H1.314.006). Return them exactly as printed; do not reorder.",
  "manufacturer": "Dentsply IH Limited (Stonehouse, UK) is the UK Authorised Representative, never the manufacturer. DENTSPLY IH AB (Mölndal) and DENTSPLY IH Inc. (Waltham) are real certificate holders.",
  "general": "..."
}
```

**What is available at escalation time** (T0 runs first, so the manufacturer is
usually already known):
- `group_id` present (fetch/discover path) → `item_group.canonical_manufacturer`
  is **authoritative**, established before extraction.
- `group_id` null (backfill/email) → only T0's own `manufacturer` field
  (conf 0.9) — a **guess**.

**Pros.** This is exactly the knowledge a generic prompt cannot carry. It is
cheap (a few hundred tokens). It is authored by a human who read the documents.
It turns each measured lesson into a durable artifact instead of a code comment.

**Cons and edge cases — these are the ones that decide the design:**

1. **Confirmation bias / circularity.** Telling the model "this is a Komet
   document" when T0 guessed wrong makes T1 *confirm* the wrong manufacturer at
   0.95 confidence. Mitigations, all four:
   - inject the full hint only when the manufacturer came from `group_id`;
     a T0-derived manufacturer gets a weaker, hedged hint or none;
   - never let a hint speak to the `manufacturer` field when `manufacturer` is
     in `missing` (i.e. when that is the very thing being asked);
   - standing instruction: *"If the document names a different manufacturer than
     this hint assumes, ignore the hint entirely and report what the document
     says."*;
   - record on the extraction that a hint was injected, so a bad hint is
     traceable rather than invisible.

2. **Evidence reproducibility — invariant 2.** Evidence carries
   `(archive_url, page, verbatim, tier, model_id, confidence, extracted_at)`. A
   hint-steered extraction is **not** reproducible from those. `extract_rev` does
   not cover this: it is a per-`content_hash` attempt counter
   (`app/extract/tiers.py:293`), nothing more. **You need `playbook_rev`
   (item 5) recorded alongside `model_id`** or "why did this document extract
   differently in March" becomes unanswerable. This is the single largest
   correctness cost of the whole idea and it is a migration plus a payload field.
   *Do item 5 before item 8.*

3. **Prompt caching.** No `cache_control` today. If it is ever added, a
   per-manufacturer block inside `system` fragments the cache across 10+
   variants. **Keep `T1_SYSTEM` byte-identical as the cache prefix**; put the
   hint in a second system block or the leading user text block.

4. **Sync/batch parity.** `_params` is deliberately shared so both transports
   submit byte-identical requests (`app/extract/llm.py:192`). Threading must go
   through `_params`, and `build_requests` must gain the same context — otherwise
   a batch and a sync extraction of the same document diverge silently.

5. **Hint drift.** Free text is unvalidated; a badly-worded hint degrades every
   document for that manufacturer and nothing catches it. **Make the corpus diff
   protocol mandatory for hint edits, same as a tier swap** — `tools/corpus.py`
   already exists for this.

6. **Token cost.** Negligible per document. On a 20-image T2 request it is noise.

### Item 9 — superseded in T0, still open for T1/T2

This document originally proposed a per-playbook list of authorised-representative
entity names. **Do not build that.** The 2026-08-18 change shipped the
better answer: T0 now scores a manufacturer match by its *position relative to a
representative role label* (`_named_outside_rep_block`), dropping to
`MFR_REP_ONLY_CONF` when every occurrence sits inside a rep block and rising to
`MFR_TRUSTED_CONF` otherwise. A name list would have been wrong on its own
evidence — `DENTSPLY IH AB` and `DENTSPLY IH Inc.` are genuine certificate
holders that a `Dentsply IH` exclusion would have discarded.

What remains: **T1 and T2 are still told nothing about the distinction.** When
T0 hands a rep-block-only read to T1 for confirmation, the prompt does not say
that is what happened, so the model re-derives from scratch the very ambiguity
T0 already detected. That is one sentence of context, and it belongs in item 8's
hint block rather than in a section of its own:

> "T0 found this manufacturer named only inside an authorised-representative
> block. Confirm from the document whether it is the manufacturer or the
> representative — the role label decides, never the company name."

Cost: nothing. Risk: it is a *hedge*, not an assertion, so the circularity
concern in item 8's con 1 does not apply — it tells the model to doubt, not to
confirm.

## 7. VALIDATE — one addition, one refusal

Today: `ref_normalize` only, applied to both sides of the C1 comparison
(`app/handlers/validate.py:368`), stored values never mutated. Correct as is.

- **Item 10, `mfr_ref_pattern`:** reject a candidate link whose REF could not
  possibly be this manufacturer's format. Cheap precision win on the unscoped
  path. *Con:* a pattern that is too tight silently drops real links — must be
  counted as a suppression, the way `validate` already counts what it suppresses.
- **Item 16, supersession policy: no.** Invariant 5 is regulatory
  (MDR never supersedes MDD, identical coverage subject/type/regulation), not
  per-manufacturer. Putting it in an authored file makes a legal rule
  editable by whoever is onboarding a vendor.

---

## 8. GATE, REPORT, SCHEDULER

**GATE — item 15, no.** Today: global `cfg.gate.high` / `.med` plus a
`calibration` map keyed `(model_id, field)`. Argument for per-manufacturer
thresholds: some manufacturers' documents are simply more legible. Argument
against, which wins: GATE is the invariant boundary, and a per-manufacturer
threshold means *"this manufacturer auto-writes at a bar nobody else clears"* —
precisely the quiet trust concentration that produces a production row you cannot
explain. Invariant 3's REF gate is already the manufacturer-aware part; the
confidence bar stays uniform. Calibration is per-model and unmeasurable
per-manufacturer at your volume.

**Item 13, the one exception worth considering:** `gate.deny_auto: true` — a
**one-way ratchet** that can only make a manufacturer *stricter* (force staged or
manual regardless of score). Safe by construction, useful for a manufacturer
whose documents you have found to be misleading. Never a knob that loosens.

**REPORT / expiry — item 17, no.** `document_effective_expiry` (migration 027)
hardcodes the 5-year DoC staleness horizon, and the entire point of that
migration was that three consumers share one definition so it *cannot drift
again*. A per-manufacturer horizon means either JSON read from SQL (never) or
moving the logic into Python (regression). If you genuinely need it later:
sync horizons into a table the view joins, exactly the way `manufacturer_alias`
works. Never read playbook JSON from SQL.

**Item 12, `playbook.reonboard`.** The scheduler already emits it on a
failure-rate spike (`app/scheduler.py:199`, default off) and
`app/handlers/noop.py` swallows it. Its natural job: *this manufacturer's
discovery/fetch keeps failing → the playbook is stale → open a manual task to
re-author it, naming which rungs failed and what the current `rev` is.* This is
the only mechanism that would make playbooks self-maintaining rather than
write-once. Needs item 5 (`rev`) to say what it is replacing.

---

## 9. Cross-cutting — read before approving anything above

1. **Cache first (item 1).** Today every consumer re-reads the whole directory
   from disk. `validate.py:465` calls `_ref_gate_for_group` once per candidate
   group in a loop, and each call re-reads and re-parses all 10 files to compute
   the *same* rule — every group in that loop carries the same
   `canonical_manufacturer` by construction. T0 does two full directory reads per
   PDF. This is correct but wasteful at 10 files, and every item above multiplies
   it. **Add an mtime-checked module cache before adding fields, not after.**

2. **Consolidate the two loaders.** `load_playbooks` and `load_templates` parse
   the same file independently, each skipping on its own criteria, each silently.
   Adding six sections makes this worse. One parse, one `Playbook` dataclass with
   optional sections, keeping the never-raise runtime contract and the loud
   `dentalia playbooks validate` operator path.

3. **Version the file (item 5).** `rev` integer, hand-bumped, or a content hash.
   Needed for evidence provenance (§6 con 2) and for reonboard (§8).

4. **Structured vs. textual rules.** Textual rules are fine — *only where a human
   or an LLM reads them*. A free-text rule a deterministic stage must interpret
   is a bug generator. **Rule: structured keys for FETCH / T0 / RESOLVE /
   VALIDATE / GATE; free text only for T1/T2 and the manual-task UI.**

5. **No secrets.** Playbooks are in git. Auth is a *reference* to a config key.

6. **Everything playbook-driven must be counted.** You already do this
   (`t0_ref_template_miss` on `Result`). Extend it: a hint injected, a fetch
   policy applied, a crawl that harvested N links, a `mfr_ref_pattern` that
   suppressed N links — all counted on the job result, per the never-silent rule.

7. **Schema growth is a compatibility surface.** Playbook fields must be
   additive-only and every consumer must tolerate their absence, the same rule
   payloads already follow. A playbook authored today must keep working when six
   new sections exist.

---

## 10. Suggested sequence

**Round 1 (foundations, no behaviour change visible):** 1, 5, plus the loader
consolidation from §9.2.

**Round 2 (free wins from data already authored):** 3, 2, 6.

**Round 3 (fetch reality):** 4, then 9 and 7.

**Round 4 (the AI half, once 5 is in):** 8 with all four guards, corpus-measured.

**Round 5 (design first, separate spec):** 11, then 12.

**Not doing:** 15, 16, 17, 18, and 14 until measured.
