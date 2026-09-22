# Dentalia Manufacturer-Code Reverse-Engineering Sweep

2026-07-06 · Attempt to resolve `Šifra proizvajalca` (manufacturer code) in the real Ljubljana BC export ("Artikli 3.7.2026.xlsx", 19,091 rows, 381 distinct codes) to a real manufacturer name, since the export carries no name field for this column. Companion to [dentalia-imports-corpus-analysis.md](dentalia-imports-corpus-analysis.md) and `tasks/followups.md` [phase0-mfrcode].

> **Corrections, 2026-08-14** (`tasks/followups.md` `[sweep-doc-corrections]`). Four things below are wrong or since superseded; the tables are left as the 2026-07-06 record and annotated in place.
>
> 1. **"65 SFTP-known brand names"** (method step 2) is not reproducible. The local corpus holds **12** manufacturer folders under `imports/dentalia-sftp/`. The ~65 figure is the count of folders reported on the remote SFTP server (`mail.dentalia.si:2222`, of which 12 were mirrored and sampled — `tasks/followups.md` `[sftp-source]`), and the list of names actually fed to the text matcher was not recorded, so the step cannot be re-run as written.
> 2. **The KOMET conclusion is wrong.** "Ljubljana appears to carry very little Komet stock" was an artefact of the tracker join failing on numbering, not of the catalogue. Code **`077` is KOMET, 399 items** — it appears below under UNRESOLVED and should not. Established since via `playbooks/komet.json` (`bc_codes: [{LJ, 077}]`).
> 3. **`10153 → DENTSPLY` (WEAK) is wrong at code level.** `10153` is **ANKYLOS**. Dentsply Sirona is one *parent* over 7 BC codes — `010` VDW, `012` SIRONA, `022` MAILLEFER, `035` DENTSPLY, `115` RINN, `313` SCHICK, `10153` ANKYLOS — and a BC code is a brand, not a parent (ruling of 2026-08-06, `docs/superpowers/plans/2026-08-06-pre-import-foundations.md:1858`). The BC code for the DENTSPLY brand is `035`.
> 4. **`035` (53 items) is likewise no longer UNRESOLVED** — `playbooks/dentsply-sirona.json` claims it. The remaining 6 codes of that parent are still unclaimed.
>
> **Further correction, 2026-08-27.** 5. **The two CONFLICT annotations are superseded.** `010`'s "also claimed by KERR(19)" and `10015`'s "also claimed by CATTANI(6)" were text-frequency artefacts: both brands have a code of their own. The vendor master this document's bottom line asks for arrived on 2026-08-05 (`Proizvajalci.xlsx`, `vendor_master`, 390 codes) and gives **KERRHAWE = `021`** (40 items) and **CATTANI = `087`** (0 catalogue items today). Neither claims the code the sweep flagged. Authored as `playbooks/kerrhawe.json` and `playbooks/cattani.json`.

**Bottom line: this is a partial, evidence-graded inference, not ground truth.** The reliable fix is asking Dentalia's BC/IT for the actual vendor/manufacturer master table — that gives 100% coverage with zero ambiguity in a single export. This sweep exists to (a) get partial, immediately-usable coverage now, and (b) give BC/IT a checkable list to confirm/correct rather than starting from nothing.

## Method

Two evidence sources, combined per manufacturer code — never per brand, to avoid a brand's incidental secondary/coincidental hits polluting a code that really belongs to someone else (see the `081` case below):

1. **Tracker cross-reference** — for brands with their own item-tracking spreadsheet on the SFTP corpus (`imports/dentalia-sftp/<BRAND>/`), match their material/article number against BC `item_ref` (not `mfr_ref` — see [phase0-mfrref](followups.md) for why). Strongest evidence when available: IVOCLAR (2 tracker files, 1,052 matches), GC (1 tracker file, 492 matches via "Art code"). KOMET's tracker ("Where to find DoC.xlsx") barely matched anything (1–3 hits) — Dentalia's Ljubljana catalogue appears to carry very little Komet stock under Komet's own numbering.
2. **Description text-matching** — word-boundary search for each of the 65 SFTP-known brand names against BC `Opis` (item description), using only each brand's own *dominant* code as evidence (secondary/minority codes are discarded as likely coincidental digit collisions — a real one caught: GC's tracker put 39 items under code `081`, but `081`'s actual 2,567 items are dental extraction forceps/instruments (`KLEŠČE EKSTR...`), nothing to do with GC).

### Confidence tiers

| Tier | Criteria |
|---|---|
| **CONFIRMED** | Tracker cross-ref ≥20 matches at ≥60% concentration on its dominant code, OR text-match ≥10 hits at ≥85% concentration |
| **LIKELY** | ≥5 supporting items at ≥50% concentration, explaining ≥5% of the code's total volume |
| **WEAK** | Some signal exists but the sample is thin (<5 items) or explains almost none of the code's volume — a plausible lead, not a basis for auto-write |
| **UNRESOLVED** | No brand-name text match and no tracker-file coverage at all |

Even a CONFIRMED tier is an *inference about the whole code bucket* from a partial sample — e.g. NSK's 26 confirmed hits sit inside a 247-item code; we're assuming the other 221 are also NSK because BC codes appear to be consistent per-vendor buckets, not because we have direct evidence for each one.

## Coverage summary

| Tier | Codes | % of codes | Items | % of catalogue |
|---|---|---|---|---|
| CONFIRMED | 20 | 5% | 4777 | 25% |
| LIKELY | 12 | 3% | 3341 | 17% |
| WEAK | 24 | 6% | 4928 | 26% |
| UNRESOLVED | 325 | 85% | 6043 | 32% |
| **Total** | **381** | | **19089** | |

**~68% of the catalogue (CONFIRMED+LIKELY+WEAK) has at least a candidate manufacturer lead; ~32% (6,043 items, 325 codes) has none at all** — no brand name ever appears in those items' descriptions, and no SFTP tracker file covers them. Most of the unresolved codes are single- or few-item codes (long tail of minor suppliers) where no statistical method can work regardless of effort. Note that WEAK is 26% of that 68% — real candidate leads, but not solid enough to act on without confirmation.

## CONFIRMED + LIKELY (use these; still recommend spot-checking before wiring into `manufacturer_alias`)

| Code | Items | Brand | Confidence | Method | Coverage | Note |
|---|---|---|---|---|---|---|
| `001` | 954 | IVOCLAR | CONFIRMED | tracker | 86% |  |
| `002` | 919 | STRAUMANN | CONFIRMED | text | 3% | *resolved, not a conflict* — Emdogain is a Straumann product (made by Institut Straumann AG), not a separate manufacturer |
| `028` | 681 | PLANMECA | CONFIRMED | text | 14% | CONFLICT also claimed as dominant code by: RAY(6) — Ray Co. Ltd is a real, unrelated Korean CBCT/imaging manufacturer; too small a sample (6) to unseat Planmeca (681) |
| `008` | 381 | GC | CONFIRMED | tracker | 92% |  |
| `011` | 331 | KAVO | LIKELY | text | 15% |  |
| `099` | 247 | NSK | CONFIRMED | text | 11% |  |
| `036` | 224 | BREDENT | CONFIRMED | text | 16% |  |
| `10176` | 199 | PANTHER | CONFIRMED | text | 28% | legal manufacturer is **SUN Oberflächentechnik GmbH** — "Panther" is SUN's product line name (matches the SFTP folder name "PANTHER-SUN") |
| `10005` | 195 | 3SHAPE | CONFIRMED | text | 35% |  |
| `10084` | 193 | PRITIDENTA | CONFIRMED | text | 79% |  |
| `030` | 187 | MELAG | CONFIRMED | text | 5% |  |
| `010` | 165 | VDW | CONFIRMED | text | 15% | ~~CONFLICT also claimed as dominant code by: KERR(19)~~ — SUPERSEDED 2026-08-27: the vendor master gives KERRHAWE its own code `021`. See correction 5. |
| `10127` | 98 | MIYO | CONFIRMED | text | 77% | ~~legal manufacturer is **Jensen Dental**~~ — CORRECTED 2026-08-20: the legal manufacturer is **Chemichl AG** (Liechtenstein). "MiYO" is Chemichl's liquid-ceramic line; Jensen GmbH sells it, and is not its manufacturer. See `playbooks/chemichl.json`. |
| `003` | 88 | KULZER | LIKELY | text | 7% |  |
| `078` | 83 | ERKODENT | CONFIRMED | text | 18% |  |
| `168` | 79 | ULTRADENT | LIKELY | text | 6% |  |
| `067` | 72 | BEGO | LIKELY | text | 10% |  |
| `6586` | 67 | TAVOM | CONFIRMED | text | 16% |  |
| `10008` | 52 | ASIGA | CONFIRMED | text | 79% |  |
| `128` | 47 | RHEIN | LIKELY | text | 17% | CONFLICT also claimed as dominant code by: NEODENT(6) |
| `038` | 43 | BECHT | LIKELY | text | 16% |  |
| `055` | 38 | SCHEU | LIKELY | text | 13% |  |
| `10135` | 33 | SABANA | CONFIRMED | text | 33% |  |
| `042` | 32 | ASA | CONFIRMED | text | 88% |  |
| `246` | 26 | ECOLAB | LIKELY | text | 27% |  |
| `10122` | 24 | AMBER MILL | CONFIRMED | text | 92% | legal manufacturer is **HASS Corporation** (Korea) — "Amber Mill" is Hass's lithium-disilicate CAD/CAM block product line, not an independent company |
| `241` | 24 | DREVE | CONFIRMED | text | 42% |  |
| `037` | 22 | HAHNENKRAT | LIKELY | text | 27% |  |
| `100` | 20 | VITA | LIKELY | text | 50% |  |
| `10188` | 19 | ORDOLINE | CONFIRMED | text | 89% |  |
| `10192` | 8 | LUMIWHITE | LIKELY | text | 100% |  |
| `081` | 2567 | CARL MARTIN | LIKELY (upgraded) | text | 0.1% | Only 3 text-match hits, but real-world corroboration is strong: Carl Martin GmbH (Solingen, since 1916, one of Europe's largest dental instrument makers) sells exactly this product category — code `081`'s actual items are forceps/elevators/curettes (`KLEŠČE EKSTR...`). Still needs BC/IT confirmation given the tiny direct sample against 2,567 total items — this is the single biggest code in the whole catalogue. |

## WEAK (plausible leads only — do not use for auto-write; needs manual confirmation)

| Code | Items | Candidate brand | Method | Note |
|---|---|---|---|---|
| `10015` | 1336 | DURR | text | ~~CONFLICT also claimed as dominant code by: CATTANI(6)~~ — SUPERSEDED 2026-08-27: the vendor master gives CATTANI its own code `087`. See correction 5. |
| `004` | 1115 | HENRY SCHEIN | text |  |
| `016` | 416 | RENFERT | text |  |
| `CEFLA` | 363 | DÜRR | text |  |
| `053` | 333 | IMESICORE | text |  |
| `043` | 235 | INTERDENT | text |  |
| `159` | 196 | GIRRBACH | text |  |
| `10094` | 184 | CEFLA | text | CONFLICT also claimed as dominant code by: ACTEON(1) |
| `054` | 135 | POLIDENT | text |  |
| `10119` | 132 | USTOMED | text |  |
| `024` | 74 | HAGER | text | CONFLICT also claimed as dominant code by: WERKEN(1) |
| `10171` | 71 | BOTISS | text |  |
| `132` | 65 | ORANGE DENTAL | text |  |
| `397` | 64 | DENTAURUM | text |  |
| `052` | 63 | EURONDA | text |  |
| `164` | 56 | DETAX | text |  |
| `040` | 34 | ZHERMACK | text |  |
| `041` | 31 | VOCO | text |  |
| `029` | 9 | BAUSCH | text |  |
| `156` | 7 | HINRICHS | text |  |
| `10181` | 4 | PARKELL | text |  |
| `10155` | 2 | SCANTIST | text |  |
| `10153` | 2 | ~~DENTSPLY~~ **ANKYLOS** | text | code-level correction 2026-08-14, see the note at the top |
| `10197` | 1 | TUPEL | text |  |

## UNRESOLVED (no signal at all — 325 codes, 6,043 items)

Sorted by item count descending; only codes with 5+ items shown individually, the rest (mostly 1-2 item codes) summarized.

**`160` is resolved as of 2026-08-19.** This sweep looked for the manufacturer's
name in document *text* and found none for `160`, which is why it landed here.
The corroboration that settles it is stronger than a name match: **339 of the
code's 796 item numbers appear verbatim** in the declarations in
`imports/dentalia-sftp/NEODENT` (`114.093`, `109.943`, `140.979` and so on),
whose legal manufacturer is JJGC Indústria e Comércio de Materiais Dentários
S/A, Curitiba, trading as Neodent. The item names are its product lines (GM
Helix, CM Alvim, GM Titamax, CM Universal Abutment), and `Proizvajalci.xlsx`
carries the row `('160', 'NEODENT')`. An article-number match against the
manufacturer's own document is conclusive where a name search was simply
looking in the wrong place.

| Code | Items |
|---|---|
| `160` (**NEODENT**, correction 2026-08-19) | 796 |
| `077` (**KOMET** — correction 2026-08-14) | 399 |
| `005` | 277 |
| `275` | 191 |
| `009` | 170 |
| `278` | 157 |
| `10044` | 131 |
| `023` | 126 |
| `370` | 126 |
| `006` | 117 |
| `105` | 102 |
| `087` | 99 |
| `312` | 94 |
| `185` | 92 |
| `175` | 86 |
| `10085` | 83 |
| `10007` | 78 |
| `396` | 77 |
| `10028` | 77 |
| `10075` | 74 |
| `022` | 74 |
| `10170` | 70 |
| `10047` | 67 |
| `10082` | 64 |
| `500` | 59 |
| `192` | 59 |
| `012` | 57 |
| `035` (**DENTSPLY** — playbook claims it since) | 53 |
| `125` | 49 |
| `10138` | 48 |
| `020` | 46 |
| `118` | 43 |
| `10050` | 43 |
| `340` | 43 |
| `021` | 40 |
| `299` | 40 |
| `10128` | 40 |
| `258` | 40 |
| `10024` | 36 |
| `363` | 35 |
| `080` | 35 |
| `066` | 34 |
| `10169` | 32 |
| `10095` | 31 |
| `10026` | 29 |
| `10031` | 29 |
| `10038` | 29 |
| `10175` | 29 |
| `290` | 29 |
| `293` | 26 |
| `10096` | 25 |
| `10132` | 25 |
| `283` | 25 |
| `111` | 25 |
| `253` | 24 |
| `070` | 23 |
| `334` | 22 |
| `273` | 22 |
| `202` | 22 |
| `289` | 21 |
| `017` | 20 |
| `333` | 20 |
| `10042` | 20 |
| `10041` | 20 |
| `034` | 19 |
| `000` | 19 |
| `10184` | 19 |
| `271` | 18 |
| `10059` | 18 |
| `013` | 18 |
| `10016` | 18 |
| `209` | 18 |
| `10178` | 18 |
| `101` | 17 |
| `109` | 15 |
| `461` | 15 |
| `10126` | 15 |
| `10055` | 14 |
| `178` | 14 |
| `056` | 14 |
| `10081` | 13 |
| `10063` | 13 |
| `142` | 13 |
| `336` | 12 |
| `10117` | 12 |
| `10078` | 11 |
| `171` | 11 |
| `10100` | 11 |
| `285` | 11 |
| `10115` | 10 |
| `007` | 10 |
| `063` | 10 |
| `10193` | 10 |
| `085` | 10 |
| `027` | 10 |
| `328` | 10 |
| `236` | 10 |
| `10001` | 9 |
| `238` | 9 |
| `133` | 9 |
| `335` | 9 |
| `10037` | 9 |
| `10182` | 9 |
| `10076` | 8 |
| `10022` | 8 |
| `458` | 7 |
| `064` | 7 |
| `10173` | 7 |
| `10103` | 7 |
| `025` | 7 |
| `059` | 6 |
| `10010` | 6 |
| `10111` | 6 |
| `310` | 6 |
| `6864` | 6 |
| `10065` | 6 |
| `222` | 6 |
| `095` | 6 |
| `10019` | 6 |
| `10125` | 6 |
| `10052` | 6 |
| `467` | 6 |
| `10046` | 6 |
| `10131` | 6 |
| `031` | 5 |
| `10064` | 5 |
| `193` | 5 |
| `207` | 5 |
| `10068` | 5 |
| `10136` | 5 |
| `10097` | 5 |
| `10163` | 5 |
| `10040` | 5 |
| `330` | 5 |
| `10109` | 5 |
| `268` | 5 |
| `10034` | 5 |

Plus **188 more codes with fewer than 5 items each** (358 items total) — long-tail minor suppliers, not worth individual listing here.

## Web verification pass (2026-07-06)

Quick web search on the CONFIRMED/LIKELY brand names to check whether they're real, and whether the matched name is the actual legal manufacturer or just a product line.

- **Confirmed as real, independent manufacturers as named:** Pritidenta (German zirconia CAD/CAM, `10084`), Tavom S.p.A. (Italian dental furniture/sterilization, `6586`), SABANA Medizinbedarf GmbH (German sutures, `10135`), Ordoline (aligners, `10188`), ASA Dental S.p.A. (Italian instruments, `042`), Alfred Becht GmbH (German endo/prophylaxis, `038`), E. Hahnenkratt GmbH (German mouth mirrors since 1932, `037`), Carl Martin GmbH (see below).
- **Real, but the matched name is a product line, not the legal manufacturer** — worth using the corrected name if this table seeds `manufacturer_alias`:
  - `10127` MIYO → ~~**Jensen Dental**~~ **Chemichl AG** (CORRECTED 2026-08-20: Chemichl AG, Liechtenstein, is the legal manufacturer; Jensen GmbH is the seller. 42 of the 60 checked items match Chemichl's own article list exactly.)
  - `10122` AMBER MILL → **HASS Corporation** (Korea; Amber Mill is Hass's CAD/CAM block line)
  - `10176` PANTHER → **SUN Oberflächentechnik GmbH** (explains the SFTP folder name "PANTHER-SUN")
- **Resolved a false conflict:** Emdogain is confirmed as a Straumann product (made by Institut Straumann AG) — the `002` STRAUMANN/EMDOGAIN "conflict" flagged earlier isn't one; both are the same company.
- **Confirmed real but conflict stands:** Ray Co., Ltd. (Korean CBCT/imaging) is a genuine, unrelated company — its 6-item claim on code `028` is real but far too small to unseat Planmeca's 681. Rhein83 (Italian implant-attachment components since 1983) is real too; its conflict with Neodent on code `128` is plausible (both could legitimately supply implant-abutment components) but unresolved.
- **Corroborating evidence found for the biggest single code:** Carl Martin GmbH (Solingen, since 1916, one of Europe's largest dental instrument makers) sells exactly the product category found under code `081` (forceps, elevators, curettes) — upgraded from WEAK to LIKELY on that basis, even though the direct text-match sample is only 3 items against 2,567 total.
- **Could not confirm:** a Cefla/Dürr Dental distributor relationship in Slovenia (the `CEFLA`/`DÜRR` code attribution) — Cefla is a real, large Italian dental equipment group, but nothing found ties it to Dürr specifically. Left as-is (LIKELY).

None of this changes the core conclusion: web search can confirm a name is real and occasionally untangle a product-line-vs-manufacturer mixup, but it cannot resolve Dentalia's internal code-bucketing decisions (e.g. the `010` VDW-vs-Kerr or `028` Planmeca-vs-Ray splits) — only the BC vendor master table can do that.

## Recommendation

1. Send this table to Dentalia's BC/IT contact and ask them to confirm/correct it against the real vendor master data — turns a ~44%-partial, confidence-graded guess into a ~100%-complete, verified `manufacturer_alias` seed in one pass.
2. Until that happens, only the CONFIRMED tier (20 codes, 25% of the catalogue) is solid enough to seed `manufacturer_alias` directly. LIKELY and WEAK should stay out of any auto-write path (Invariant #3) — surface them as staged suggestions for human review at most.
3. The UNRESOLVED 32% is not a dead end so much as a sign this method has hit its ceiling — closing it needs either the master table (recommendation 1) or manufacturer-scoped tracker files we don't have yet for those codes' brands.
