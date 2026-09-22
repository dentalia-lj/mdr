# Phase 0 findings — extraction accuracy, error catalogue, calibration

Status: v2, S0.4 complete (2026-07-13). **Client demo checkpoint.** Covers the
*extraction* findings (accuracy, error catalogue, per-field calibration signal,
cost), the persisted DB-proof spine run, the Basic-UDI-DI grouping-signal check,
and the `mfr_ref` source identification (G4) incl. the AC1 ceiling proposal —
the latter two in-memo below, raw data in `tasks/followups.md` (`phase0-mfrref` /
`phase0-mfrcode`) + `docs/dentalia-manufacturer-code-sweep.md`. Cost-curve detail
and the multi-model tier diff are the economics session's to finish (see Open).

## Method

Live tier ladder (T0 deterministic → T1 Haiku text → T2 Sonnet vision, per-field
escalation, threshold 0.95) run over the 22 committed fixtures
(`tests/fixtures/corpus/`), compared field-by-field to the human-verified
`corpus_manifest.ground_truth` (8 target fields). Single model per tier
(`claude-haiku-4-5` / `claude-sonnet-4-6`), sync path. 20/22 docs completed; 2
failed hard (below). Harness: `scratchpad/corpus_run.py` (opt-in, not committed —
spends live tokens).

## Headline

- **Field accuracy: 117/154 = 76%** across the 20 completed docs.
- **Cost: $0.567 total, $0.0283/doc** (T1 Haiku ~$0.005, +T2 Sonnet vision ~$0.02-0.03).
- **Escalation: 18/20 docs reached T2 (vision).** Only one stopped at T1, one at T0 (the MSDS short-circuit). This is the dominant cost driver and the single biggest lever.

## Per-field accuracy

| field | correct | accuracy | miss | false-positive | notes |
|---|---|---|---|---|---|
| validity_to | 18/20 | **90%** | 1 | 1 | most reliable date |
| ref_list | 12/14 | 86% | 2 | 0 | 6 n/a (external/text-list markers) |
| type | 17/20 | 85% | 3 | 0 | EC-vs-ISO/QMS on scanned certs |
| basic_udi_di | 17/20 | 85% | 1 | 2 | |
| cert_number | 16/20 | 80% | 3 | 1 | format/label variants |
| coverage_scope | 14/20 | 70% | 6 | 0 | item vs manufacturer vs group |
| **regulation** | 13/20 | **65%** | 1 | **6** | `'n.a.'` string emitted instead of null |
| **validity_from** | 10/20 | **50%** | 1 | **9** | **worst field** — issue/signing date returned as validity_from |

## Error catalogue (ranked by impact)

1. **`validity_from` false-positives — the #1 accuracy problem (9/20).** The model
   returns the document's *issue/signing date* as `validity_from` when the doc has
   no validity-period start (`2026-01-14`, `2025-09-22`, …). Systematic across DoCs.
   *Fix:* prompt must distinguish "issue/signature date" from "validity start";
   and calibration should heavily discount `validity_from` confidence (see below).

2. **`regulation = 'n.a.'` instead of null (6/20).** On docs with no MDR/MDD
   (ISO/QMS certs, battery/cosmetic DoCs) the model emits the string `"n.a."`.
   Truth is `null` (correctly abstain). *Fix:* normalize `{'n.a.','n/a','none',''}
   → null` in `_fields_from_json`, and/or prompt "use null, never 'n.a.'". Cheap,
   removes ~all 6 false-positives at once.

3. **`coverage_scope` (6 miss, 70%).** Single-device DoCs guessed `item` where
   truth is `manufacturer`; external-attachment DoCs guessed `manufacturer` where
   truth is `group`. The item/group/manufacturer call needs the REF-list/attachment
   signal the model often can't see — partly a T0 concern (see followups).

4. **Near-universal T2 escalation (18/20) — the cost driver.** Almost every doc
   escalates to vision because *some* field stays under 0.95 — often a field that
   is *legitimately absent* (`validity_from`, `ref_list`), so the model reports
   low confidence and the ladder escalates to chase a value that doesn't exist.
   Fixing #1/#2 (so "absent" is a confident answer) is what cuts T2 traffic.

5. **Two hard failures — truncated structured output (2/20).** NEODENT (26-page
   DoC, at T1) and DENTAURUM (scanned, at T2) both returned **invalid JSON** —
   `Unterminated string` — because the response hit `max_tokens=2048` mid-object.
   `_response_json` then throws and the whole extraction dies; in production the
   job dead-letters. *Fix:* raise `max_tokens` for the structured schema, stream
   to `get_final_message`, and/or repair/retry on truncated JSON. Robustness bug,
   not an accuracy one.

6. **Deterministic T0 gaps** are separately catalogued in `tasks/followups.md`
   (`extract-t0`): multi-page REF truncation (206→46), line-split dates, `(GMN)` /
   hyphen UDI-label variants, bare-`No.` cert labels, KOMET text-column REF lists.
   Those are the deterministic misses T1/T2 currently backfill (at T2 cost).

## Cost & economics

- **$0.0283/doc measured**, dominated by the T2 vision tier (18/20 docs). T1-only
  docs cost ~$0.005; the vision surcharge is ~5×.
- **Levers, in order:** (a) fix #1/#2 so legitimately-absent fields don't force
  escalation → fewer T2 calls → the biggest cut; (b) Batch API (50% off, already
  built) for the sweep; (c) bulk batch-of-N (deferred from S0.2) to amortize.
- The €10-40/month steady-state target is about *re-*extraction volume, and the
  fetch-ledger two-cycle property (below) makes steady-state ≈ 0 for unchanged
  docs — the real spend is the one-time backfill. Precise curves + the
  Haiku-vs-DeepSeek/Qwen tier diff (ground-truth §5, gated by G12) are the
  economics session's to finish; this memo supplies the per-doc baseline and the
  escalation histogram.

## Proposed thresholds & calibration factors (G2/G5 — needs review)

The model is **over-confident**: it self-reports ~0.95-0.97 on most fields while
actual accuracy is 76%, so raw confidence is a poor gate input. Apply the G2
gate-time transform `effective = raw × factor(model_id, field)` with factors
seeded from the accuracy above (Haiku T1; refine per-tier later):

| field | factor | rationale |
|---|---|---|
| validity_to | 0.90 | reliable |
| type | 0.85 | |
| basic_udi_di | 0.85 | |
| cert_number | 0.80 | |
| coverage_scope | 0.70 | item/group/manufacturer hard |
| regulation | 0.80 | **after** the `'n.a.'→null` normalization; without it, ~0.55 |
| validity_from | **0.30** | 50% + 9 false-positives — trust it least |

- **Gate thresholds:** keep HIGH at 0.90 and MED at 0.70 **on the calibrated
  (effective) confidence**. With the factors above, a field only reaches
  production-eligible (≥0.90 effective) if the model is near-certain on a reliable
  field — which is the intent.
- These are proposals from a 20-doc, single-model sample. Re-fit after the
  `'n.a.'` normalization and a larger run.

## Verification hooks (PRD §12) — passing

- **Two-cycle (C2/C3/AC5):** `backfill.scan` re-scanning unchanged content emits 0
  new `extract.doc` (hash-dedup). Proven in `tests/test_backfill_handler.py`.
- **QMS-cert binding path (§7b/C4):** Ivoclar ISO cert flows backfill → extract →
  validate → gate.candidate as exactly one staged binding candidate. Proven in
  `tests/test_verification_hooks.py`.

## DB-proof spine run (S0.4 done-criterion) — 2026-07-13

Persisted end-to-end run — `backfill.scan → extract.doc (live sync) → validate.doc
→ gate.candidate` — over the 22 committed fixtures against the compose Postgres
(`dentalia` DB), via the real worker loop. Replaces the in-memory harness above as
the done-criterion evidence.

- **22/22 docs extracted, 0 dead-lettered.** The truncated-JSON hard failures
  (error catalogue #5) did **not** reproduce after the robustness fixes
  (2026-07-07).
- **Cost: $0.6091 total, $0.0277/doc** — corroborates the $0.0283 in-memory
  estimate. 21 T1 (Haiku) + 17 T2 (Sonnet vision) calls, 0 unpriced. Tier mix:
  1 T0-only, 4 T0+T1, 17 T0+T1+T2.
- **Persisted (deltas):** `fetch_log` +22, `document` +15 (11 DoC / 4 EC, all
  `staged`, none auto-promoted), `evidence` +82, `manual_task` +3,
  `item_document` +0 (no item groups seeded — the REF gate had nothing to match
  against; link derivation is exercised in the S1.0 test suite instead).
- **VALIDATE suppressed 7/22** — 6 docs with no `regulation` after the
  `'n.a.'→null` normalization (ISO/QMS certs + non-MD DoCs, i.e. the evidence-
  presence rule doing its job) and 1 MSDS. Correct behavior, but the drops are
  not counted on the job result — reporting-gap followup logged
  (`validate-report`).
- **Two-cycle (C2/C3/AC5), proven on the persisted spine:** re-running
  `backfill.scan` over unchanged content returned `{scanned: 22, emitted: 0,
  seen: 22, errors: 0}`; spend delta **$0.00**.

## Basic-UDI-DI grouping signal (S0.4 check)

**Hypothesis** (`docs/dentalia-workflow-structured-v2.md:129-131`): Basic UDI-DI is
the *group/family* key — same intended purpose, risk class, essential design — and
"is what certificates and DoCs reference." UDI-DI (the per-device GTIN) is the
item-level key; the proposed mapping is `document.basic_udi_di` = group reference,
`item.udi` = per-item UDI-DI.

**Doc-side signal rate** (re-derived from `tests/fixtures/corpus_manifest.py`): 6/22
committed fixtures carry a non-null ground-truth `basic_udi_di` (27%) — VOCO Ceramic
Bond, GC New Metal Strips, PLANMECA Viso G1, KOMET 532862, KOMET 533068, IVOCLAR IPS
e.max Ceram. Cross-tabbed against `coverage_scope`: 5/6 are `group`-scope docs, 1/6
(PLANMECA Viso G1, a single-device DoC) is `manufacturer`-scope — consistent with
Basic UDI-DI marking a family rather than a single item.

- Extraction accuracy on this field: **85% (17/20)** — see "Per-field accuracy" above.
- T0-only (deterministic regex) hit rate: **9%**, measured over a 109-document sample
  of the real corpus (`tasks/todo.md:82-83`) — confirms this is a T1/T2 field, not a
  T0 one. (`docs/dentalia-imports-corpus-analysis.md` §3.4/§5.2 carries an earlier,
  smaller-sample estimate of ~25% T0 yield for this field, from a 30-PDF stratified
  sample — the two disagree; the 109-doc figure is the larger, actually-measured run
  and is used here. Flagged for reconciliation, not resolved in this memo.)

**Catalogue-side:** the real Ljubljana export (`Artikli 3.7.2026.xlsx`) has **no UDI
or Basic UDI-DI column at all** (`tasks/followups.md` `[phase0-mfrref]`). There is nothing on the BC
side to join a document's `basic_udi_di` against today.

**Verdict:** confirmed as a **document-grouping key** — documents stating the same
Basic UDI-DI can be chained/grouped, and it is literally the second arm of the REF
gate: `overlap(ext.ref_list, group.member_mfr_refs) or ext.basic_udi_di ==
group.basic_udi_di` (`docs/dentalia-pipeline-contract-prd-v3.md:160`), with
`match_basis = basic-udi-di` eligible for full auto-write
(`docs/dentalia-pipeline-contract-prd-v3.md:183`). It is **not** usable as an
item→group *resolution* key today — there is no catalogue-side UDI to resolve
against — until UDI data exists on the BC side (client-provided) or via the EUDAMED
mirror (S2.3), or a group's `basic_udi_di` gets planted by a prior doc-derived
assignment. RESOLVE (S1.2) will lean on name/family heuristics + tracker seeding in
Phase 1, not Basic UDI-DI.

**AC1/G15 implication:** Basic UDI-DI does not move the AC1 (REF-gate auto-write)
ceiling today — it is a second gate arm that stays empty until a group's
`basic_udi_di` is populated some other way. Treat it as a Phase 1/2 hedge
(EUDAMED-driven), not a Phase 0 lever.

## mfr_ref source identification (G4)

**LJ export facts** (`Artikli 3.7.2026.xlsx`, 19,091 rows; `tasks/followups.md`
`[phase0-mfrref]`/`[phase0-mfrcode]`, `docs/dentalia-manufacturer-code-sweep.md`):

- `mfr_ref` ("Dobaviteljeva št. artikla"): **44.5% blank**. Of the populated 55.5%,
  **~85% are Dentalia's own `item_ref` reformatted** (punctuation-only difference) —
  genuinely distinct vendor values are **~8% of the full catalogue**
  (55.5% × 15% ≈ 8.3%).
- Zero exact or substring matches against real KOMET DoC REFs (`196.644.050` etc.,
  pulled from the downloaded SFTP PDFs), in either `item_ref` or `mfr_ref`.
- Manufacturer identity in the export is an opaque internal code (`Šifra
  proizvajalca`) with **no name field** — 381 distinct codes
  (`docs/dentalia-manufacturer-code-sweep.md:3`, confirmed by summing its coverage
  table below; `tasks/followups.md` `[phase0-mfrref]` states 382 — a stale count from
  before the full sweep, superseded here). No UDI/Basic UDI-DI column exists (confirmed above).
- Positive signal: for **~86% of the catalogue**, `item_ref` is "plain"-style (vs.
  14% Dentalia-internal "dotted" style), and for confirmed-Ivoclar items it **is**
  the manufacturer's own material number (1,052-row tracker cross-ref match) —
  `tasks/followups.md` `[phase0-mfrcode]`.

**Manufacturer-code sweep** (`docs/dentalia-manufacturer-code-sweep.md` coverage
table — tracker cross-ref + word-boundary-corrected description matching,
dominant-code-only):

| tier | codes | % of catalogue (items) |
|---|---|---|
| CONFIRMED | 20 | 25% |
| LIKELY | 12 | 17% |
| WEAK | 24 | 26% |
| UNRESOLVED | 325 | 32% |

(`tasks/followups.md` `[phase0-mfrcode]`'s narrative summary — "11 more LIKELY, 25 WEAK" — predates
the full sweep and differs slightly from the sweep doc's own final coverage table
above; the table is used here as the authoritative, later count.)

Recommendation in that doc: send the sweep table to Dentalia's BC/IT to confirm/
correct against the real vendor master; only CONFIRMED is solid enough to seed
`manufacturer_alias` directly today — LIKELY/WEAK must stay staged-only (Invariant #3).

### AC1 ceiling re-derivation

AC1 = REF-gate auto-write coverage, bounded by `missing_mfr_ref`
(`docs/dentalia-pipeline-contract-prd-v3.md:78`: "this number directly bounds
achievable AC1"; `:272`: "the missing_mfr_ref rate ... directly sets the achievable
AC1 ceiling"). PRD v3:20 already anticipates this outcome: "If no source is
populated for a material share of items, AC1 target must be renegotiated — the
REF gate has no key without it."

- **Strict reading** — pair `(canonical_manufacturer, mfr_ref)` on the export exactly
  as it stands: ceiling ≈ **8%** (the genuinely-distinct-`mfr_ref` share above). This
  treats BC `mfr_ref` as the only source, unmapped.
- **Recommended reading** — apply the per-supplier logical mapping the contract
  already allows (`docs/dentalia-pipeline-contract-prd-v3.md:78` "per-supplier
  mapping config if mixed"; `PHASES.md:99` "mfr_ref logical mapping (per-supplier
  config if Phase 0 found mixed)"): for the CONFIRMED-manufacturer, plain-style share
  of the catalogue, treat `item_ref` itself as the manufacturer material number.
  Ceiling ≈ **25%** today (the CONFIRMED tier), rising with every code BC/IT upgrades
  out of LIKELY/WEAK/UNRESOLVED.

**Proposal for renegotiation** (not a unilateral change — needs Denis sign-off, and
is exactly the case PRD v3:20 anticipates): adopt the per-supplier mapping reading
and renegotiate AC1 against the ~25% CONFIRMED baseline — a number with a clear path
to climb as BC/IT confirms more codes — rather than the ~8% strict baseline, which
has no realistic path to improve without new data.

### Client actions

1. Send `docs/dentalia-manufacturer-code-sweep.md` to Dentalia's BC/IT contact to
   confirm/correct against the real vendor master.
2. Request the vendor master table itself (code → manufacturer name) — resolves the
   32% UNRESOLVED tier in one pass.
3. Request UDI / Basic UDI-DI columns, if they exist anywhere in BC outside this
   export.

## Open / hand-offs

- **Prompt + parsing fixes** (owner: EXTRACT): `'n.a.'→null` normalization;
  issue-date-vs-validity_from prompt clarity; `max_tokens` / truncated-JSON
  robustness. Followups logged.
- **Cost curves + multi-model tier diff** (owner: economics session; gated by
  G12 external-model policy).
- **Calibration factors** above are proposals — wire into the config `[calibration]`
  map and re-measure.
- **Larger corpus** — 22 fixtures is a spike sample; the real LJ backfill will
  refine every number here.
- **Basic-UDI-DI grouping signal** — closed in Phase 0 (see above): confirmed as a
  document-grouping key (REF gate's second arm), not yet usable as a catalogue
  resolution key (no UDI data in the LJ export). RESOLVE (S1.2) should not plan on it
  for Phase 1; revisit once the EUDAMED mirror (S2.3) or client UDI data lands.
- **`mfr_ref` source identification (G4)** — closed in Phase 0 (see above),
  superseding the pointer to `tasks/followups.md` in the header above. AC1 ceiling
  re-derived at ≈8% (strict) / ≈25% (recommended, per-supplier mapping) — **needs
  Denis sign-off** before either becomes the Phase 1 target, plus the three client
  actions listed in that section (sweep doc to BC/IT, vendor master request, UDI
  column request).
