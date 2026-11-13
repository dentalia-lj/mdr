# Subcontractor rule: draft plan (2026-11-12, not approved, not committed)

## Why

Some items are sold under a brand (KOMET, GC, SANOLABOR, DUERR DENTAL) but made by
another company, whose name is the one printed on the documents. Nataša wants
those documents filed under the brand's items. Today the only tool is a name
alias (subcontractor name -> brand), and it is unsafe:

- **Doc 165**, Sure Dent Corporation's MDR QMS certificate (HZ 2020214-1), is
  bound to **all 262 Komet MD items** since 2026-10-15. Cause: `komet.json`
  aliases "Sure Dent Corporation" to KOMET; T0's playbook identity
  (`t0_templates.py:1345`) found that name on the page and returned KOMET; GATE's
  §7b machine bind (`_handle_mfr_binding`) linked every Komet MD item. Not pushed
  to BC (0 `bc_push_log` rows). For 2 items it is the only EC certificate.
- **Docs 587, 588**, CHIRANA T. Injecta EC certificates, are staged only because
  the alias `CHIRANA T. Injecta a.s.` lacks the comma the page prints. Fixing that
  spelling would bind them to every SANOLABOR MD item.
- Other aliases that are other companies: `VATECH Co., Ltd.` -> DUERR DENTAL,
  `ANGELUS ...` -> HENRY SCHEIN, `ALLE S.R.L.` -> EURONDA, `LAMBDA SpA` ->
  DOCTOR SMILE, `PANTHER` -> SUN. Audit needed: alias of a brand name, or a
  contract maker?

A name alias says "this company IS the brand". A subcontractor says "this company
makes THESE articles for the brand". The second needs an article list.

## Rule (proposal)

1. A brand's playbook gets a `subcontractors` section. Each entry has the company's
   printed names, its EUDAMED SRN when known (Sure Dent KR-MF-000018662, Woodpecker
   CN-MF-000009139, D.V. IT-MF-000044674), and an **explicit article list**: BC
   item numbers, or a pointer to a coverage-map source (Komet's SPECIFIKACIJA.docx,
   39 rows, 11 folder keys).
2. A subcontractor name is **not** an alias: it never makes T0's playbook identity
   or `resolve_canonicals` answer with the brand on its own.
3. A document whose printed manufacturer is a subcontractor of brand B:
   - **declaration (group scope):** may link only to articles on B's list for that
     subcontractor. Printed numbers overlapping the list form links as today.
   - **certificate (whole-range scope):** linked to the listed articles only, basis
     `mfr-scope`, never to B's whole range.
   - `canonical_manufacturer` = B, so the picker and the Komet pages work; the
     printed name stays in evidence and on Review.
4. A company that is also a manufacturer in its own right (Woodpecker, BC 430):
   its own playbook keeps its own range; the subcontractor entry adds only the
   listed brand articles. A whole-range certificate covers both.
5. Validation at `playbooks sync`: refuse a subcontractor name that is an alias of
   another entity; count listed articles missing from BC (Komet 533208, CMS Dental
   obturators 9996.000.0xx: no BC item found), never silent.
6. Manual choice stays (Nataša, 2026-11-12: "for now I'll let you also have that
   option").

## Tasks (estimate: human / Claude Code, tests and docs included)

| # | Task | Human | CC |
|---|---|---|---|
| 1 | Audit the aliases listed above; list docs and links each would affect | 1 h | 0.3 h |
| 2 | Spec; PRD amendment (inv. 3 pair, §7b bind, T0 identity); decisions | 2 h | 0.75 h |
| 3 | Playbook schema + validator + migration (subcontract table) + sync | 4 h | 1.5 h |
| 4 | Resolution: T0 identity, `resolve_canonicals`, VALIDATE REF gate, GATE §7b and the human bind capped to the list, picker scope | 8 h | 3 h |
| 5 | Data: komet.json (Sure Dent out of aliases, SPECIFIKACIJA in), the others from the audit | 2 h | 1 h |
| 6 | Server: revalidate the affected docs (no LLM), repair doc 165 (`repair-mfr-overbind`) | 1.5 h | 0.5 h |
| 7 | Docs: playbook-authoring, guide playbooks EN/SL, handlers, limits | 2 h | 0.75 h |
| 8 | Full suite, deploy | 1 h | 0.5 h |
| | **Total** | **21.5 h** | **8.3 h** |

## Review findings (independent reviewer, checked in code 2026-11-12)

The estimate above is too low (reviewer: 35-45 h / 14-18 h). What the draft missed:

1. VALIDATE's whole-range route takes the GROUP's manufacturer and never reads the
   printed name (`validate.py:876-879`). Removing the alias alone does not stop a
   Sure Dent certificate fetched or uploaded for a Komet group binding Komet's range.
2. T0 stores the playbook key, not the printed name ("playbook identity: KOMET",
   conf 0.97, no escalation): "printed name stays in evidence" is false for T0 reads.
   Komet's templates also steer the parse of a Sure Dent document.
3. `playbooks sync` only inserts aliases (`cli.py:186-217`); deleting the line from
   komet.json leaves the DB row. Needs an UPDATE path (a `kind='subcontractor'`).
4. Reverses ruling D3 (`docs/superpowers/specs/2026-08-18-komet-coverage-map-design.md:74`:
   re-bind cross-maker items to the real issuer). Needs a decision row; PRD §7 C4/C16
   (not §7b, which is EUDAMED).
5. No human path to a list-capped certificate: picks and removals are refused on a
   whole-range document, bind-manufacturer always means the whole range. Needs a new
   `gate.apply` branch. Rule 6 (manual whole-range bind stays) conflicts with capping.
6. Basis `mfr-scope` means "whole range" on Review, compliance and registry: use
   `map-supplier` or a new trusted basis (TRUSTED_BASES, PUBLISHING_BASES, words, PRD).
7. `repair-mfr-overbind` refuses doc 165 (it requires the text to enumerate devices,
   `repair_mfr_overbind.py:89-92`). Needs a ruled one-off: retract 262, relink the
   listed Sure Dent articles in the same transaction (2 items keep their only EC).
8. EUDAMED attribution goes through `manufacturer_alias` (`eudamed.py:407-421`): a
   subcontractor's SRN would be attributed to the brand and its certificates reported
   as the brand's gaps. Not happened yet (0 rows for the three SRNs).
9. Rules 4 and 5 contradict each other for Woodpecker (its name is its own alias), and
   `canonical_manufacturer` holds one manufacturer.
10. komet_coverage replaces `ref_list` with the whole folder list: folder 533213 holds
    separate GP and PP declarations, each would get the other's articles. Declarations
    need printed list ∩ index list.
11. Minor: PANTHER is a SUN brand (correct alias). Other readers of names: reconcile.py,
    registry.py, unmatched_names, GATE REF route. Renewal mails follow the items, so
    either canonical sends them to Komet.

Reviewer's verdict: keep canonical = brand (the picker needs the brand's BC codes).

## Options

- **B, containment (about 15.5 h / 5.5 h).** Subcontractor names become `kind=subcontractor`
  rows: never a machine whole-range bind, never inherited from the group on that route,
  never EUDAMED attribution; T0 reports the printed name and escalates. Their documents
  stage for Nataša, who links them by hand (picker under KOMET, works today for DoCs).
  D3 reversal ruled. Doc 165 repaired by a ruled one-off. Sure Dent, Chirana, and the
  audit's confirmed cases converted.
- **C, B plus Komet automation (about 30 h / 12 h).** Komet's SPECIFIKACIJA drives links
  (phase 2, declarations intersected, `map-supplier`), plus the list-capped `gate.apply`
  branch for certificates.
- **A, full rule for every brand (35-45 h / 14-18 h).** C, plus an article list per
  subcontractor in every brand's playbook and the picker scoped to it.

## Advisor (Fable) and BC finding, 2026-11-12 — replaces options A/B/C

BC already records the maker per item: "Šifra proizvajalca (primarni)"
(`pteManufCodePrimary`, not mirrored). July export: 16 items, all KOMET: 14 Sure
Dent gutta-percha points (GPPR/PPPR) carry 077-A, ENFILL and ENPACK carry 077-B.
077-A and 077-B are not in vendor_master (392 codes, synced 2026-11-06), so their
names are unknown. Nataša 2026-11-12: makers are never independent in BC, they belong
under the core manufacturer, only a link is needed; "this field" already holds Guilin
Woodpecker (screenshot not readable here).

Design: mirror `maker_code`; brand playbooks get `contract_makers` (code, printed
names, SRN; no article lists); a whole-range certificate machine-binds only when its
PRINTED name is the brand's own identity (never the group's manufacturer); a maker's
certificate links only items carrying its maker code (new basis `maker-scope`), and
stages "fill the primary code in BC" when none do; declarations unchanged. Woodpecker:
own identity binds code 430, the same certificate also links items whose maker code is
Woodpecker. Estimate 24 h / 9.5 h; later 10 h / 4 h.

Verified after the advice: the sync does re-point an alias (`cli.py:193-198`, UPDATE).
Doc 165's 2 sole-EC items are `109 2311` ENDOPILOT APEX CABLE SET and `109 2312`
ENDOPILOT CABLE FOR FILE CLAMP: not Sure Dent products, so the repair removes a false
certificate from them; it relinks only the 077-A items.

Open: names of 077-A/077-B in BC; how BC codes Woodpecker there; how many whole-range
binds relied on the group's manufacturer (count before shipping); who creates maker
codes in BC for the other brands.

## Live BC read, 2026-11-12 (GET only, allitems + allmanufacturers, from the server)

- 20,368 items. `pteManufCodePrimary` is filled on 18, all under brand 077 KOMET, all MD-classed:
  077-A on 16 Procodile gutta-percha and paper points (GPPR/PPPR, including the two `S1`
  assortments that the July export did not have), 077-B on `000 ENFILL` and `000 ENPACK`.
- `allmanufacturers` has 392 codes and neither 077-A nor 077-B: BC holds no name behind them.
  The playbook would have to carry "077-A = <maker>" itself.
- Woodpecker is a brand of its own: code 430 WOODPECKER, 4 items (WACENDO3, WAPEXVBL, WP-Q3,
  WP-Q7), none with an MD class in BC; plus E14D under 080 EMS. Doc 385 has no manufacturer
  only because the printed "Co., Ltd." does not equal the alias "Co.,Ltd".
- Doc 390 (Meta Biomed, CeraSeal): no BC item description contains "CeraSeal".
- Our mirror matches BC on counts (20,357 vs 20,368; Komet 403 = 403; Woodpecker 4 = 4).

## Nataša's answers, 2026-11-12 14:24 (mail "sem šla čez")

- 077-A = Sure Dent Corporation (confirmed). Doc 165 should cover only the Procodile points.
- 077-B = GUILIN WOODPECKER MEDICAL INSTRUMENT CO. BC does hold names for these codes, in a
  secondary maker list ("sekundaren seznam"), not on `allmanufacturers`. Only these two exist.
- Woodpecker is both its own brand (430) and a contract maker for others. E14D moved to 430.
- Agreed: a contract maker's certificate attaches only to items carrying its code.
- Dentalia has no data to fill the code for Komet's other contract makers.
- Woodpecker's devices are MD; class unknown to them. CeraSeal: not opened yet, will be under 077.

Production read after her answer (read-only): docs 458 (Woodpecker DoC, "Fi-P (EnPack)",
"Fi-G (EnFill)") and 383 (Woodpecker DoC, "Ai-Motor (EnGo)") are `filed` under WOODPECKER
with no links: the items are Komet's (000 ENPACK, 000 ENFILL, 000 ENGO13 000, all 077, IIa).
000 ENGO13 000 has no primary code. Doc 385 has no stored text. No Woodpecker document we hold
states a class. Design consequence: the resolver of a manufacturer's items must include items
whose primary code is that manufacturer's, so GATE, the picker and the Today link check agree.
