# Vendor master (`Proizvajalci.xlsx`) — analysis handoff

2026-08-05. Analysis session on master; **implementation belongs on `s1.7-playbooks`**.
Closes the ask in `docs/dentalia-manufacturer-code-sweep.md` §Recommendation 1.

## The artifact

`imports/Proizvajalci.xlsx` (gitignored), sheet `Proizvajalci`, columns `Šifra` / `Ime`.
390 rows · 390 unique codes · 387 unique names · 1 blank name (`10198`, unused in LJ).

Resolves **381 of 381** manufacturer codes in the LJ export — 19,089 / 19,091 rows
(2 rows carry no code). 9 master codes unused in LJ.

Validates the sweep's tiering: CONFIRMED 20/20 correct, LIKELY 12/12, WEAK 17/24.
Overturns two sweep conclusions:
- **Komet**: sweep said LJ "carries very little Komet stock" — wrong, code `077` is **399 items**.
- `10153` was labelled DENTSPLY; it is **ANKYLOS**. DENTSPLY is `035` (sweep left it unresolved).

## Why the playbook route alone cannot get there

| path | codes | items | coverage |
|---|---|---|---|
| seeded today (`gc` 008, `ivoclar` 001) | 2 | 1,335 | 7.0% |
| + `playbooks/README.md` pending list | 32 | 8,118 | 42.5% |
| **vendor master** | **381** | **19,089** | **100%** |

The master closes **10,971 items (57.5%)** that no playbook authoring reaches, and completes
already-authored playbooks: `ivoclar` missing `005`+`275` (468 items), `komet` `077` (399),
`voco` `041` (31), `dentsply-sirona` six codes `035/012/022/10153/10137/115` (188).

## Design

Two-source precedence. The master is the **floor**, not a replacement.

- **Playbook `bc_codes` win** — `gc.json` says `GC EUROPE N.V.`, master says `GC`. The playbook
  carries the legal entity, which is what invariant 3 compares against a DoC. Authored beats mirrored.
- **`vendor_master` fills the remaining ~350 codes** with BC's vendor label. Enough for discovery
  and grouping; caps at `staged` where the label is not the legal manufacturer.

Playbooks stop being required for *identity*; they remain required for authored config
(domains, `doc_sources`, T0 templates). README's "pending codes" section becomes mostly obsolete.

### Steps

1. **Merge master into `s1.7-playbooks`** — it is 21 behind and missing migrations 014/015
   (s1.7 tops out at 013, master at 015).
2. **Migration `016`** — `vendor_master` + explicit `GRANT SELECT ON vendor_master TO dentalia_api;`
   (007's blanket grant does **not** reach later tables; precedent 009/010/012/013).
3. **CLI import verb** — NOT a migration, see below.
4. **Extend `playbooks sync`** with the precedence + per-row skip-and-count.

### Decisions with evidence

- **Key it `(code_source, code)`, not `(catalogue, code)`.** Spec §3:127 and §6:153 contradict each
  other; §6 loses. §3: *"the key is which BC instance issued the code, so a Zagreb-warehouse item
  living in the Ljubljana BC keeps the default and resolves correctly."* Carry `catalogue` as a
  plain column.
- **CLI, not a data-bearing migration — structurally, not by preference.** `app/db.py:73` skips
  already-applied files, so a data migration can never be re-run, contradicting spec §6's
  "re-imported on every BC refresh". `db.py` executes raw SQL only (no openpyxl reachable),
  `imports/` is gitignored, and **zero** migrations in this repo contain `INSERT INTO`/`COPY`.
  openpyxl is already declared worker-only (`pyproject.toml:62`).
- **A separate table, not a `source` column on `manufacturer_alias`.** Alias is declared
  "regenerable, not truth" (spec §5). If the 390 rows live only in alias, alias *becomes* truth
  for them and cannot be regenerated once the xlsx is gone.
- **New tables do not need a PRD change** — that discipline binds job types only. Four tables
  (`manual_task`, `extraction_cost`, `grouping_suggestion`, `scheduler_run`) landed post-PRD with
  no PRD edit. The schema sketch and its §7 access matrix **do** need updating.
- **`sync_aliases` currently raises on any conflict, validating the whole map before writing.**
  Correct for 5 hand-authored playbooks (authoring error); wrong for a 390-row BC-sourced master,
  where a disagreement is a *data* fact. Needs per-row skip + count + report.

### Hard ordering constraint

**Step 4 must run before the first real ingest.** `item_group.canonical_manufacturer` is written
once at `resolve.py:179` and there is **no UPDATE of it anywhere in the codebase, and no
delete/regroup tooling**. A re-resolve does not repair it: `_existing_link` is the first ladder
rung (`resolve.py:312`), returns score 1.0, and short-circuits — `canonical` is computed but never
used on that path. Neither forced-`group_id` nor `force_new_group` rewrites it. The
`orphaned_groups` warning in `sync_aliases` has no supported remedy short of raw SQL.

Dev DB was `item_mirror`=3, `item_group`=0, `manufacturer_alias`=0 at time of writing — seeding
now is free.

### Known ceiling (not a defect)

**17.0% floor / 22.6% ceiling** of catalogued items sit behind a BC name that would not match the
legal manufacturer on a DoC — distributors (HENRY SCHEIN 1,115 items; SASU ACTEON DISTRIBUTION 184;
SANOLABOR 94; DENTALIA 19 = freight/labour lines), and product lines of other legal entities
(MIYO→Jensen Dental, ANKYLOS→Dentsply, HASSBIO, SUN, ROEKO→Coltène, KOMET→Gebr. Brasseler).
NEODENT (796 items, 4.2%) is unresolved and decides most of the spread.

Separately: **only 18 of 390 names carry a legal-form suffix** (`INSTITUT STRAUMANN AG`,
`3SHAPE TRIOS A/S`, `ASIGA Pty. Ltd.`…). The other ~13,200 items sit under bare brand short-forms
(`BEGO`, `VITA`, `EMS`, `W&H`) needing alias expansion before they can match a DoC string. So the
*practical* ceiling is worse than the structural one.

### Data-quality flags for BC/IT

- `10198` has a blank name.
- `CEFLA` is a literal non-numeric code string (363 items) alongside `10015` CEFLA (1,336).
- Six codes break the 3/5-digit pattern: `0459` BISCO, `101200` IRIDE, `6586` TAVOM,
  `6863` ANAXDENT, `6864` SCHMITZ, `CEFLA`.
- Codes collapse many-to-one onto names: `001`+`005`+`275` → IVOCLAR VIVADENT (1,422 items);
  `10015`+`CEFLA` → CEFLA (1,699). Both collapses look correct. The *inverse* is the real problem —
  one company under several names (Coltène+ROEKO, Kuraray+Noritake, Ustomed+Ulrich Storz,
  Hopf Ringleb+HORICO, Semperit+Sempermed, Dentsply Sirona across six codes, +8 more).
- Do **not** merge 3SHAPE `10005`/`10004`/`10003` — corpus holds three separate ISO 13485 certs.

## Independent INGEST findings (not S1.7 — separate slice)

These came out of the same analysis and are unrelated to the vendor master.

1. **`mfr_ref` fill-only fallback.** `Ingest.mfr_ref_source_by_code` would take
   `missing_mfr_ref` from **44.4% → 22.6%** (3,473 items gained) across 38 codes; `081` CARL MARTIN
   alone is 2,276 items = 32% of the hole. **BUT the mechanism is a *replace*, not a fill**
   (`source.py:119`, `mfr_ref = item_ref if source == "item_ref"` — unconditional). On the 38-code
   set that silently discards **156 real vendor values**, and they are exactly the ones that matter:
   Straumann pack variants where item_ref `061.7312` and `061.7314` both map to base article
   `061.7310` — the number the DoC will actually name. Needs a fill-only mode; **not config-only**.
   Circularity caveat: the evidence that item_ref is the vendor's article number is BC's own
   populated subset. Against pure auto-copy: copy rate varies sharply by vendor (100% Neodent,
   70% Komet, 0% SUN) and formats are vendor-native.
2. **Prose in `mfr_ref`.** ~400 populated values are Slovene remarks (`NE BO VEČ NA ZALOGI!` ×122,
   `OPERA!` ×48) currently written into `item_mirror.mfr_ref` and used as the REF-gate comparand.
   **Safe rule is `contains '!'` → 378 values, nothing cleverer.** 985 populated values contain a
   space and **652 of those are legitimate codes**, including Komet's ISO bur numbers
   (`104 H251EF 060`, `314 S6830L 016`) — a space/letter heuristic would destroy them.
3. **`Blokirano = 1` → out of scope** (Denis, 2026-08-05: not deliverable anymore). 678 rows,
   642 net new exclusions (36 are already-dropped `NI MP`). Note it removes **43 items BC has
   explicitly classified as devices** (26 `RAZRED IR`, 15 `IIA`, 2 `I`). **Cannot be a plain skip** —
   `ingest.py:115-132` shows why: an already-mirrored item that later gets blocked would sit active
   in the mirror forever. Needs the same reclassification branch the non-MD path uses.
   Caveat recorded: whether any blocked item was previously sold (MDR retention) is **not
   answerable from this export** — `Fakturirana količina` is populated on only 2,816/19,091 rows.

## Dead ends — do not build

- **Item category code (`Šifra kategorije artikla`, `imports/image002.png`).** Looks like a strong
  MD-gate prior; it is not. BC filled the device-class column **per vendor × product-family block**,
  so labels are not missing-at-random. 10,429 of 11,693 blanks sit in `(category, manufacturer)`
  cells with zero labels. Leave-one-manufacturer-out: `TEHPMA CC`→MD scores **0%**; `ORDINS`→MD is
  one vendor (`081`, 2,567 of 2,568 labels) and untestable; the only rule that survives
  (`RZDRDE`→NON-MD, 100%) has an **11-row** blank subset. Also: adding it to `LJ_CSV_PROFILE` makes
  the column mandatory for every future LJ export (`source.py:207` hard-fails on a missing profile
  column).
- `Št. dobavitelja` — no MD signal, not a manufacturer proxy, does not help `mfr_ref`.
- `Knjižna skupina izdelka za DDV` — no rule clears Wilson-95 at any threshold.
- `Osnovna merska enota` — 19,068/19,091 are `KOS`.
- Customs tariff headings 9018/9021 — the medical chapters are the *least* discriminating (62.9%
  and 40% MD). Headings 3926+7018 → NON-MD do validate cross-vendor but are worth only 303 items.

## Open, needs Denis

- **G17 LJ/ZG convergence.** `PHASES.md:229` is still *proposing* to record the statement, marked
  `[FILLED — needs review]`, owner "agent proposed → Denis confirms". The spec §3 deferral of alias
  namespacing rests on it. G11 (`PHASES.md:223`) is one line about overlap % and does not cover it.
- **Name-precedence rule across four authorities** — playbook `manufacturer`, `vendor_master.name`,
  `manufacturer.canonical_name`, `manufacturer_alias.canonical_name`. Nothing says which wins when
  Phase 2 populates `manufacturer`. Proposal: `manufacturer` rows are *derived from* vendor_master +
  playbooks, never hand-typed.

## Corrections to earlier notes in this repo

- `docs/dentalia-manufacturer-code-sweep.md:12` says "65 SFTP-known brand names". The corpus holds
  **12** brand folders; the 65 figure is not reproducible from the corpus and no brand-list artifact
  exists in `tools/`. Item-weighted corpus coverage is 5,282/19,089 = **27.7%** (26.5% after
  deducting BREDENT, which has one MSDS and no compliance doc, and LUMIWHITE, cosmetic).
- `docs/architecture.md:50` says "Thirteen migrations" and lists 001–013; `migrations/` holds 015.
  Stale independently of this work.
