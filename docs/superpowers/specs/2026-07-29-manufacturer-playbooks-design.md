# Per-manufacturer playbooks + BC identity seed — design spec

- **Date:** 2026-07-29
- **Status:** approved design, pending spec review → implementation plan
- **Owner:** agent (brainstormed with Denis)
- **Normative change:** no. No new job type, no PRD §11 key moves, no stage emits a tag outside its Emits row. One migration (`manufacturer_alias` primary key).

## Problem

Per-manufacturer configuration is real and already in use, but it lives in four places keyed two different ways, and the bridge that makes three of them reachable is currently a no-op.

| Home | Key | Read by |
|---|---|---|
| `cfg.ingest.mfr_ref_source_by_code` ([config.py:213](../../../app/config.py)) | BC code (`001`) | INGEST |
| `cfg.source_priority` ([config.py:321](../../../app/config.py)) | canonical name | DISCOVER |
| `cfg.mfr_binding.per_manufacturer` ([config.py:187](../../../app/config.py)) | canonical name | nothing yet (declared only) |
| `app/extract/t0_layout/*.json` | manufacturer name | EXTRACT |

[source.py:145](../../../app/adapters/source.py) maps `manufacturer_raw` from `Šifra proizvajalca`, so it holds a bare BC code such as `001`. [`_alias_lookup`](../../../app/handlers/resolve.py) inserts raw as its own canonical on a miss. With `manufacturer_alias` unseeded, `item_group.canonical_manufacturer` is the literal string `001`, never `IVOCLAR`.

Consequences today, all currently live:

- Every name-keyed config map above can never match.
- [`_query_for`](../../../app/handlers/discover.py) emits the search query `001 declaration of conformity pdf` for a Ljubljana Ivoclar group.
- `_prefilled_search_links` builds manual-queue links from the same string.

There is also no home for manufacturer website or document-source URLs, so DISCOVER's search rung has no domain to anchor on.

This is needed **now**: S1.7 runs live discovery over the top ~20 manufacturers, and pre-S1.7 decision (1) already scopes md-unknown processing to the ~20 CONFIRMED codes, so that set is the operational unit of the sweep either way.

## Evidence base (what we can actually seed)

From [dentalia-manufacturer-code-sweep.md](../../dentalia-manufacturer-code-sweep.md): the LJ export ("Artikli 3.7.2026.xlsx", 19,091 rows) carries 381 distinct manufacturer codes and **no name field**. 20 CONFIRMED + 12 LIKELY resolve to a brand: 32 codes, ~8,100 items, 42% of the catalogue. 325 codes have no lead at all.

From [dentalia-imports-corpus-analysis.md](../../dentalia-imports-corpus-analysis.md): 12 brands characterised (scan rates, template consistency, contamination), 5 working T0 templates. URL data on hand is **one** URL, the Dentsply filtered download-centre link (§8).

So: enough to seed identity, parsing and quirks for ~20 to 32 manufacturers. Nothing to seed URLs with except manual per-manufacturer research.

**Out-of-band and higher leverage than anything in this spec:** ask Dentalia BC/IT for the vendor/manufacturer master table. That gives code→name at 100% coverage with zero ambiguity, versus our inferred 42%, and likely contact and website fields too. The sweep doc already recommends it. This design is built so that export slots in without re-authoring anything (see §6).

## Decisions (locked in brainstorming)

1. **Timing: seed now, inside S1.7.** Not deferred to S2.1.
2. **Granularity: domains plus per-doc-type URL templates**, not domains alone.
3. **Config home: git JSON is truth, DB holds derived state.** `playbooks/{slug}.json` owns authored config and absorbs the five `t0_layout` files. This is what PHASES.md GAP C already decided S2.1 would do.
4. **S1.7 scope: playbooks + alias seed only.** The `manufacturer` table stays untouched; its remaining columns (`contact_emails`, `eudamed_srn`, `hit_rate_stats`) all have Phase 2 consumers only.
5. **`doc_sources` consumption: author + prefill.** Domains restrict the search query; portal URLs are injected into dead-end manual tasks. No crawler, no new ladder rung, no HTML in the archive.
6. **`bc_codes` carries `{catalogue, code}` in the file**, so no playbook is re-authored whichever way G11 lands. The corresponding **database** namespacing is deferred (§3, revised 2026-07-29): Denis confirmed LJ and ZG are intended to converge into one BC, which makes warehouse-keyed aliases a fragmentation risk rather than a safeguard.
7. **`manufacturer` is the legal entity where documented, else the brand string**, with the file tracked in `playbooks/README.md` until corrected. Inventing an unverified legal name is worse than a tracked placeholder.

## 1. The artifact

`playbooks/{slug}.json`, one file per manufacturer, git-versioned. The slug is a short, stable brand handle chosen by the author (`voco.json`, `dentsply-sirona.json`, `jensen-dental.json`), **not** derived from the legal name: legal names change on acquisition, and a renamed file loses its git history. The `manufacturer` field inside the file is the authoritative value; the filename is only an identifier.

```json
{
  "manufacturer": "VOCO GmbH",
  "bc_codes": [{"catalogue": "LJ", "code": "062"}],
  "aliases": ["VOCO", "VOCO Dental"],
  "domains": ["voco.dental", "voco.de"],
  "doc_sources": [
    {"doc_type": "doc", "kind": "portal",
     "url": "https://www.voco.dental/...", "note": "filtered download centre"}
  ],
  "match": {"anchors": ["..."]},
  "ref_strategy": "text-column",
  "ref_strategy_config": {"header_anchors": ["ref"], "field_index": 2}
}
```

Field notes:

- **`manufacturer` is the legal manufacturer**, because the DoC names the legal entity and invariant 3's REF gate compares `canonical_manufacturer`. Product lines are `aliases`. The sweep found three CONFIRMED codes that are product lines, not companies: `10176` PANTHER is legally SUN Oberflächentechnik, `10127` MIYO is legally Jensen Dental, `10122` AMBER MILL is legally HASS Corporation. This does not disturb EXTRACT: `Template.manufacturer` is informational only, and [`t0_layout.match`](../../../app/extract/t0_layout.py) selects a template by `match.anchors` against page-1 text, never by comparing the name to `canonical_manufacturer`.
- **`bc_codes` is a list of `{catalogue, code}` objects.** A list because one legal manufacturer can hold several BC codes (several product lines, plus one per catalogue). Catalogue-scoped because BC codes are per-instance and LJ and ZG are separate companies with separate vendor numbering. See §3.
- **`doc_sources[].kind` is `portal` or `direct` from day one**, even though Phase 1 uses only `portal`. It is the field S2.1's crawler branches on; adding it later means re-authoring every file.
- **`match` / `ref_strategy` / `ref_strategy_config`** are the existing T0 template schema, unchanged.

Two properties make this cheap. [`load_templates`](../../../app/extract/t0_layout.py) reads only the keys it needs, so the new keys are additive and EXTRACT is unaffected by their presence. And the loader already takes a `dir_path`, so relocating from `app/extract/t0_layout/` to `playbooks/` is a default change plus a `git mv`.

One fix required there: a file with no `match`/`ref_strategy` section (most of the 20 will have none) must be a **silent skip**, not the current `log.warning("skipping malformed template")`, or every extraction logs ~15 warnings. Genuinely malformed files (bad JSON, unknown `ref_strategy`) keep warning.

## 2. Identity: the alias seed

A new CLI command, `dentalia playbooks sync`, upserts `manufacturer_alias` from every `bc_codes` and `aliases` entry across all playbook files. Idempotent, re-runnable.

This is the unblocker. It turns `item_group.canonical_manufacturer` from `001` into `VOCO GmbH`, which switches on `cfg.source_priority`, playbook lookup, the search query, and the manual-queue prefill, all at once. It ships value before a single URL is authored.

Three behaviours it must have rather than assume away:

- **Ordering constraint.** Re-pointing an alias does not retro-fix `item_group` rows already resolved under the old value. Sync runs **before** the S1.7 ingest. To keep a mistake visible rather than silent (house rule: skipped rows counted and reported, never silent), sync reports the count of existing `item_group` rows whose `canonical_manufacturer` it just orphaned, so the operator knows a re-resolve is needed.
- **Conflicts fail loudly.** Two playbooks claiming the same `(catalogue, code)` is an authoring error. Sync raises and writes nothing, rather than last-write-wins.
- **Fallback preserved.** RESOLVE keeps its existing self-seeding behaviour for codes no playbook covers, which is 349 of 381.

## 3. Catalogue namespacing — DEFERRED to G11 (revised 2026-07-29)

**This section's migration is not being built.** Decision reversed the same day, before implementation, on new information from Denis: Dentalia intend the Ljubljana BC to eventually carry every entry that exists in the Zagreb warehouse, so LJ and ZG converge into one system rather than staying two.

That inverts the risk. If there is one code namespace, keying `manufacturer_alias` on the warehouse tag **fragments** it: an item tagged `catalogue='ZG'` carrying an LJ-namespace code misses the seeded `('LJ','001')` row, self-seeds `('ZG','001','001')`, and lands back at a bare BC code as its `canonical_manufacturer`. The migration would then cause the exact failure it was written to prevent.

The premise was also overstated below. Whether the two markets share a system is not established, it is **open gap G11** (`PHASES.md`, owner: client, due pre-S2.5); [ground-truth.md §34](../../dentalia-mdr-pipeline-ground-truth.md) says "unconfirmed whether same system" and question 3 asks it outright.

Two further points make deferral cheap:

- Phase 1 ingests Ljubljana only, so the collision cannot occur before G11 is answered.
- `manufacturer_alias` is **regenerable**, not truth: it derives from playbook `bc_codes` plus RESOLVE's self-seeding, and `dentalia playbooks sync` rebuilds it. A later migration costs a migration plus a sync re-run, not a data migration. The original "do it now while the table is empty" argument was weaker than it appeared.

Revisit when G11 resolves. If the answer is "two systems", namespace on **which BC instance issued the code**, not on the warehouse tag, so converged items keep resolving.

The verified findings below still stand and are why this is worth revisiting rather than dropping.

Verified state, not assumed:

- [`manufacturer_alias`](../../../migrations/002_ingest.sql) is `raw_name text PRIMARY KEY`, no catalogue column.
- [`resolve.py`](../../../app/handlers/resolve.py) does not select `catalogue` from `item_mirror`, so the handler could not scope the lookup today even if it wanted to.
- [source.py](../../../app/adapters/source.py) has only LJ profiles (CSV + OData). The ZG profile is a comment marked S2.5, so we have no evidence ZG reuses LJ's namespace and every reason to expect it does not.

Failure mode if left alone: LJ `001` is seeded to IVOCLAR; a Zagreb item arrives carrying its own `001` and resolves to IVOCLAR silently. Wrong manufacturer means wrong group, which means the REF gate compares against the wrong `member_mfr_refs`. It fails as a bad auto-link, not as an error.

Migration, **if and only if G11 answers "two systems"**:

```sql
ALTER TABLE manufacturer_alias ADD COLUMN code_source text NOT NULL DEFAULT 'bc-lj';
ALTER TABLE manufacturer_alias DROP CONSTRAINT manufacturer_alias_pkey;
ALTER TABLE manufacturer_alias ADD PRIMARY KEY (code_source, raw_name);
```

Note `code_source`, not `catalogue`: the key is which BC instance issued the code, so a Zagreb-warehouse item living in the Ljubljana BC keeps the default and resolves correctly. `resolve.py` would then pass the ingesting adapter profile's code source, not `item_mirror.catalogue`.

Not built now. See the deferral note at the top of this section.

## 4. What Phase 1 consumes

| Consumer | Change |
|---|---|
| [`_query_for`](../../../app/handlers/discover.py) | Add `site:` restriction when `domains` is known. On zero results, one unrestricted retry. Both attempts logged to `discovery_log`. |
| [`_prefilled_search_links`](../../../app/handlers/discover.py) | Prepend the manufacturer's `portal` URLs, so a dead-end manual task opens the right download centre instead of a bare Google query. |
| EXTRACT | Template directory path change only. |

The zero-result fallback is not optional: compliance PDFs frequently sit on a CDN or a subsidiary domain, so an unconditional `site:` restriction would silently zero out real hits.

**Deliberately excluded: a host-match ranking prior.** It would perturb the candidate score distribution that pre-S1.7 decision (3) says gets hand-calibrated once the T1 ranker lands, and would need recalibrating immediately.

**Sequencing:** the `site:` restriction should land *before* the threshold-calibration session, so calibration sees the real candidate distribution rather than one it will never see again.

## 5. What stays out

- The `manufacturer` table: untouched, no rows, no columns added.
- `doc_sources`: authored and schema-validated, never fetched. No HTML enters the archive; no ladder rung is activated. The `playbook` rung stays tolerated-and-skipped as today.
- The other three config homes (`mfr_ref_source_by_code`, `source_priority`, `mfr_binding.per_manufacturer`) stay in TOML. Folding them into the playbook file is the right end state but is a PRD §11 change, so it is a separate proposal.

## 6. How the BC vendor master lands later

When BC/IT supply the vendor master, it is **mirrored source data, not authored config**, exactly like `item_mirror`. It therefore lands in a table keyed on `(catalogue, code)`, not in the playbook files.

The playbook's `bc_codes` is the join. That keeps the git-versus-DB split coherent: BC owns codes and official names, we own domains, `doc_sources` and parse config, and neither overwrites the other. The vendor master can be re-imported on every BC refresh without touching a single playbook file.

Because `bc_codes` already carries `{catalogue, code}`, no playbook is re-authored when that export arrives.

## 7. Task split

Seven files exceeds the three-file working rule, so this ships as four independent units.

| # | Unit | Touches | Ships value alone |
|---|---|---|---|
| A | Playbook store: schema, loader, authored files | `app/playbooks.py`, `playbooks/*.json` | No, but zero behaviour change |
| B | `playbooks sync` + alias seed (namespacing migration deferred, §3) | `app/cli.py` | **Yes**, fixes `001` → `VOCO GmbH` |
| C | DISCOVER domains + portal prefill | `app/handlers/discover.py` | Yes |
| D | EXTRACT template path move | `app/extract/t0_layout.py`, `git mv` | No, refactor only |

B is the highest value per line changed. A must precede B (sync reads playbook files). C and D depend on A only.

## 8. Testing

Table-driven, against real Postgres (no mocking Postgres).

**Sync / identity:** idempotent re-run writes nothing new · duplicate `(catalogue, code)` across two playbooks raises and writes nothing · alias upsert correctly overwrites a self-seeded `001 → 001` row · orphan count reported when an existing `item_group.canonical_manufacturer` is remapped · a code with no playbook still self-seeds via RESOLVE · same `code` under two catalogues produces two distinct alias rows (the namespacing regression test).

**Loader:** malformed JSON skipped with a warning · unknown `ref_strategy` skipped with a warning · **missing parse section skipped silently** · unknown top-level keys tolerated · empty `playbooks/` degrades to the pre-existing generic T0 path.

**DISCOVER:** `site:`-restricted query built when domains known · unrestricted query when not · zero-result fallback fires exactly once and both attempts appear in `discovery_log` · portal URLs prepended to prefill links in the documented order · a manufacturer with no playbook behaves exactly as today.

## 9. Edge cases carried into implementation

- A playbook whose `manufacturer` matches no BC code at all (authored ahead of catalogue data): valid, sync writes only its `aliases`.
- Two playbooks with the same `manufacturer` value: authoring error, fail loudly (same rule as duplicate codes).
- `domains` containing a bare hostname versus a URL: normalise to hostname at load; reuse `app/urls.py` rather than adding a second normaliser.
- A `doc_sources` entry with `kind: "direct"` in Phase 1: accepted and validated, ignored by every consumer. It must not silently become a fetch.
- Playbook files are re-read per call today (`load_templates` has no cache). 20 small files per document is acceptable for S1.7; revisit only if the sweep shows it in the profile.

## 10. Verification log (2026-07-29)

Every load-bearing claim above was executed, not inferred. Baseline before any change: **699 passed, 1 skipped** (`pytest -q`, 192s).

| # | Claim | Method | Result |
|---|---|---|---|
| 1 | Degraded search query | `_query_for(GroupFacts(manufacturer='001', ...))` | `'001 declaration of conformity pdf'`. Prefill links equally degraded. |
| 2 | Alias seed fixes it | same, `manufacturer='IVOCLAR'`, `label='IPS e.max'` | `'IVOCLAR "IPS e.max" declaration of conformity pdf'` |
| 3 | RESOLVE self-seeds raw as canonical | `_alias_lookup(conn,'001')` on empty table | `('001', True)`, then `('001', False)` on repeat |
| 4 | Sync overwrites the self-seeded row | UPDATE then re-lookup | `('IVOCLAR', False)` |
| 5 | **BC codes are not unique across namespaces** | second `001` insert on the current schema | `UniqueViolation` on `manufacturer_alias_pkey`, i.e. the table structurally cannot hold two meanings for one code |
| 6 | **and it fails silently, not loudly** | `_alias_lookup` for a ZG `001` when LJ `001` is seeded | returns `'IVOCLAR'`, `created=False`. No exception, no log. |
| 7 | New keys are additive | loader against a file with `bc_codes`/`aliases`/`domains`/`doc_sources` | loads cleanly |
| 8 | **Missing parse section is indistinguishable from broken** | loader against a URLs-only file and an unknown-`ref_strategy` file | both emit the identical `skipping malformed template` warning |
| 9 | Migration is free today | live dev DB row counts | `item_mirror` 3, `item_group` 0, `manufacturer_alias` 0, `manufacturer` 0, `discovery_log` 0 |
| 10 | Next migration number | `ls migrations/` | `013` is highest, so `014` |

Item 6 is the important one: the collision does not surface as the `UniqueViolation` of item 5, because `_alias_lookup` reads before it writes. Item 8 confirms §1's silent-skip fix is required rather than cosmetic.

## 11. Open items requiring Denis

1. **Authoring the ~20 files is manual research**, roughly 10 to 20 minutes per manufacturer to find the real DoC portal and confirm the domain: several hours that is not coding. Decide: Denis authors, or agent drafts all 20 from web research for Denis to correct.
2. **Send the BC/IT vendor master request** in parallel. It supersedes the inferred 42% coverage and is the single highest-leverage unblocker for this whole area.
