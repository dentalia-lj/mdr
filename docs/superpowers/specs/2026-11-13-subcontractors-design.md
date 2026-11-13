# Subcontractors: design

Date: 2026-11-13. Status: draft for Denis, not approved, not committed.
History: `tasks/2026-11-12-subcontractor-rule-draft.md` (options, BC finding, Nataša's answers). A full automated design (27 h Claude Code) was written and reviewed the same day, then rejected as out of proportion: subcontractors are an exception (about ten makers, about 60 items, nearly all Komet). It is summarised in § 9.

## 1. The problem

Some BC items are sold under a brand (e.g. 077 KOMET) but made by another company, a subcontractor, whose name is printed on its documents. Today the only tool is a name alias in the brand's playbook. An alias says "this company **is** the brand", so a subcontractor's document is treated as the brand's:

- **Doc 165**, Sure Dent Corporation's MDR certificate, was bound to all 262 KOMET medical items. Sure Dent makes about 30 of them.
- **Currency is per item and type** (MDR first, then the newest date). Both 165 and Komet's own certificate 172 are MDR EC certificates. 172 is newer today, so it is current on 260 items. A Sure Dent renewal would become the current EC certificate on all 262 Komet items. That is the overtake Denis ruled out (2026-11-13).

**The rule:** a subcontractor's document is never treated as the brand's. Nothing from a subcontractor publishes by itself. It waits on Review with the reason and a suggested fix, and a person links it to the items the subcontractor makes.

## 2. Scope

In
- A `subcontractors` list in the brand's playbook.
- One publishing check for whole-range binding.
- Review shows the reason and a suggested fix.
- Picker help.
- The five wrong aliases moved out.
- A read-only dry run before shipping.

Out (deliberately)
- Automatic linking from the BC primary maker code or from Komet's SPECIFIKACIJA.
- Maker entities and EUDAMED changes.
- Group companies (DeguDent under DENTSPLY and the like): they stay aliases.

Anti-goals
- Any path where a subcontractor's document binds a brand's whole range.
- A guard that only holds while nobody re-adds an alias.

## 3. Facts this rests on (verified 2026-11-13, code at `main` 271faa2, production read-only)

- F1. VALIDATE's whole-range route takes the **group's** manufacturer when the document came through a group, and ignores the printed name (`app/handlers/validate.py:875-881`). GATE prefers it (`gate.py:1224`).
- F2. T0 "playbook identity" returns a manufacturer at 0.97, with no escalation, when exactly one playbook's name appears **anywhere** in the text (`app/extract/t0_templates.py:1314-1373`). Doc 2192 (ADRANOX SRL's certificate) prints "EURONDA" 4 times, so T0 would call it Euronda's. Doc 165's text names only Sure Dent.
- F3. The machine whole-range bind (`gate.py:1239-1241`) links every medical-device item of the manufacturer, basis `mfr-scope`. It is written once, at bind time.
- F4. Nothing deletes a name from `manufacturer_alias` or `manufacturer_name` (`app/cli.py:183-213`). Removing an alias from a playbook has no effect in production today.
- F5. Unknown playbook body keys survive the seed, a database reload and the web editor (spike test 2026-11-13).
- F6. The picker is free when a document has no manufacturer, and locked to the document's manufacturer when it has one (`web/app.py:1679-1687`). `check_items` refuses items outside that manufacturer's BC codes (`app/item_picks.py:15-49`).
- F7. Wrong today:
  - 165 (Sure Dent → KOMET, 262 links);
  - 1061 and 1063: Acteon user manuals bound to Ultradent's 3 VALO lamps through one mention of "Ultradent".
  
  The other linked whole-range documents read name the brand's own company. Latent, with 0 links only because the brand has no medical-device items in BC: 587 and 588 (Chirana under SANOLABOR), 2199 (ALLE) and 2192 (ADRANOX), both under EURONDA.
- F8. Wrong aliases in live playbooks:
  - KOMET ← Sure Dent Corporation;
  - SANOLABOR ← CHIRANA T. Injecta a.s.;
  - EURONDA ← ALLE S.R.L.;
  - DUERR DENTAL ← VATECH Co., Ltd.;
  - HENRY SCHEIN ← ANGELUS.
- F9. Subcontractor declarations that link by printed numbers are correct: Sure Dent 384/387/434 (14 Komet items each, including GPFQ/PPFQ) and Chirana 591/597 (17 Sanolabor items). Woodpecker declarations 458 and 383 are filed under WOODPECKER (its own brand, BC 430) with no links, because the products are Komet items (000 ENFILL, 000 ENPACK, 000 ENGO13 000).
- F10. Volume: 25 subcontractor documents in a registry of 2,111. 21 arrived in September (the initial import) and 4 in October.

## 4. Design

### 4.1 The playbook list

The brand's playbook gets a body key `subcontractors`: the printed names of companies that make products for this brand.

```json
"subcontractors": ["Sure Dent Corporation", "Meta Biomed Co., Ltd.",
                   "Guilin Woodpecker Medical Instrument Co.,Ltd", "SHOFU INC.", "Alfred Becht GmbH",
                   "CMS Dental A/S", "HANS KOREA CO., LTD.", "SAVUNA GmbH",
                   "Stoddard Manufacturing Company Limited", "P. L. Superior Dental Materials GmbH"]
```

The lists, from Komet's SPECIFIKACIJA, the makers' own documents, and Denis and Nataša (2026-11-12/13):

| Brand | Subcontractors | Source |
|---|---|---|
| KOMET | Sure Dent, Meta Biomed (CeraSeal, sold as KometBioSeal), Woodpecker, Shofu, Becht, CMS Dental (obturators), Hans Korea (NiTi brushes), SAVUNA (Sterisafe containers), Stoddard (prophy brushes), P.L. Superior (Dentin Build/Bond) | SPECIFIKACIJA folders, BC primary codes 077-A/077-B, Nataša |
| SANOLABOR | Chirana T. Injecta | Denis |
| EURONDA | ALLE S.R.L. (sterile sets, the Alle brand), ADRANOX SRL | Denis, Euronda's own PDFs; Adranox from its certificate (doc 2192) only |
| DUERR DENTAL | VATECH Co., Ltd. (VistaPano) | Denis, FDA 510(k) K240040 |
| HENRY SCHEIN | Angelus | Angelus is an independent Brazilian manufacturer, not a Henry Schein company; no BC item carries its name; its declaration (450) is filed under Henry Schein. Listed as a subcontractor so it can never be taken as Henry Schein |

- A name that is an existing manufacturer's canonical name (WOODPECKER, SHOFU, BECHT) means that manufacturer. These are brands of their own (BC 430, 175, 038) that also make for Komet.
- **Validation** (sync and web save): a subcontractor name may not be an alias or canonical name of the same brand, and may not be listed under two brands' aliases. The five aliases in F8 move to their brand's `subcontractors`.
- **Sync and the entity seed** (production loads playbook files with `manufacturers seed --apply`, plus `--adopt <slug>` for a changed body, then `playbooks sync`; runbook § 579) remove a name from `manufacturer_alias` and `manufacturer_name` when it has moved to `subcontractors`, and report it (F4). Narrow by design: only names that moved. On production the dry run lists the rows first and a backup is taken.

### 4.2 Matching a name to the list

- The list holds the **printed legal names** as documents print them: "Guilin Woodpecker Medical Instrument Co.,Ltd", "SHOFU INC.", "Alfred Becht GmbH", "ANGELUS INDÚSTRIA DE PRODUTOS ODONTOLÓGICOS S/A".
- A printed name matches when it equals a listed name after `normalize`, or when it resolves (through `manufacturer_alias`) to a canonical name that is listed. That second route covers WOODPECKER, SHOFU and BECHT and their own aliases.
- **Validation:** a listed name may not be an alias or canonical name of **any** brand, except a canonical that is listed deliberately as "this manufacturer" (WOODPECKER, SHOFU, BECHT).

### 4.3 T0 sees subcontractors

- T0's identity match (`t0_templates.py:1356-1373`) counts listed subcontractor names as hits, attributed to the subcontractor rather than the brand.
- A text that names both the brand and a listed subcontractor therefore has two different hits. T0 abstains, and T1 reads the printed issuer by role.
- Without this, removing the alias would leave T0 calling a Sure Dent document KOMET's whenever the text prints "Komet" anywhere outside a representative block, at 0.97, on every route (review 2026-11-13 finding 2).

### 4.4 A subcontractor's document for brand B

A document is **a subcontractor's document** when its printed issuer matches a name on some brand's list (§ 4.2). For each brand B whose list it is on:

- **Declarations (REF route):** VALIDATE runs the existing brand-scoped linker `_links_for_manufacturer(conn, B, …)` (`validate.py:957-959`) for each listing brand, adds flag `subcontractor`, and writes the matches **staged**. Today a name that resolves to nothing gets `manufacturer-unresolved`, which is blocking and links nothing (`validate.py:970-977`, `gate.py:75-76`). With this, Sure Dent's declarations keep the correct links F9 credits them with, now as pre-ticks.
- **Certificates (whole-range route):** never bound to B. They stage with flag `subcontractor`.
- **Relative to B.** Shofu's own certificate for Shofu's own items is processed as today. The rule applies only when the document lands on a brand that lists Shofu: through B's group, or through a printed number matching B's items.
- **GATE stores B as the document's manufacturer at staging** when exactly one brand lists the issuer. Otherwise none is stored, and Review names every listing brand. Today the publishing path stores the manufacturer only from the printed name (`gate.py:1630-1633`), which resolves to nothing after the move. An approval of the pre-ticks alone would then publish a document with no manufacturer.
- `subcontractor` is a staging flag, not a blocking one. It caps every link at staged, `map-supplier` included (komet_coverage can attach Komet's list to a file under `/KOMET/`, `komet_coverage.py:161-178`).
- **Approval** publishes exactly the ticked items: the picker sends unticked pre-ticks in `remove` (`item_picker.js:125,168`, `web/app.py:5899`).

### 4.5 Publishing check for every whole-range bind

1. **Type:** IFU and "other" never machine-bind a whole range. This stops 1061/1063. Declarations keep today's behaviour (ruling C16, 2026-08-17), so no ruling is reversed.
2. **Issuer:** when the printed issuer resolves to a known manufacturer whose BC codes do not overlap the brand being bound, or is a listed subcontractor, the document stages with `issuer-not-brand`. This closes the group-inheritance hole (F1).
   - The comparison is by code-set overlap, `item_codes_for(issuer) ∩ item_codes_for(brand)`, so multi-code brands such as IVOCLAR (001/005/275) and legal-name aliases pass.
   - **An issuer that resolves to nothing binds as today.** Unaliased legal names are common (the data-quality page exists for them), and holding all of them would flood Review.
3. **Human bind:** `gate.apply bind-manufacturer` (`gate.py:2457-2482`) and its web route refuse to bind a brand's range when the document's printed issuer is on that brand's list, and the whole-range button is hidden for such a document. The reason shown is "Change type to specific items". Without this, the same button the reviewer uses for any whole-range certificate would bind Sure Dent to KOMET (review finding 1).

### 4.6 Review, picker, data quality

- **Review reason and fix:**
  - Certificate: "Sure Dent is a known subcontractor of KOMET. A subcontractor's certificate cannot cover all of KOMET. Change type to *specific items*, then tick the items Sure Dent makes."
  - Declaration: "Sure Dent is a known subcontractor of KOMET. Check the ticked items and approve."
- **Picker:** opens on B, because B is stored (§ 4.4). For a document whose manufacturer is a listed subcontractor that is also its own brand (Woodpecker's 458/383), the picker's search scope (`web/item_picker.py:115`), `check_items` and Today's link check also accept the listing brands' items.
- **Data-quality page:** `unmatched_names._by_refs` (`unmatched_names.py:169-182`) would suggest "add alias Sure Dent → KOMET" from the Komet article numbers. A listed name is shown as "known subcontractor of KOMET" instead, never as an alias suggestion.

### 4.7 Accepted residual risks

- **On the items a person ticks,** the subcontractor's certificate competes with the brand's by date. Those are items the subcontractor makes, never the whole brand. Who the legal manufacturer is (Komet or Sure Dent) is Nataša's regulatory call, recorded in limits.
- **An unlisted subcontractor** (unknown to us) whose printed name resolves to nothing and that arrives through a brand's group can still bind that brand's range, as today. Adding it to the list closes it.
- **A group company aliased under its brand** binds the brand's range as today. None does wrongly today.
- **Stored extractions are not re-run.** The 25 existing subcontractor documents are repaired by hand (§ 6). The rule applies to new and re-read documents.

## 5. Work

Revised after the second independent review (§ 10).

| # | What | Human | CC |
|---|---|---|---|
| 1 | Publishing check (§ 4.5 items 1-2): type rule, issuer rule by code-set overlap, flag, table tests | 4 h | 1.5 h |
| 2 | T0 counts listed subcontractor names (§ 4.3), tests | 2 h | 0.75 h |
| 3 | `subcontractors` list: parse, matching rule, validation against every brand's names, body round-trip; data: Komet's ten makers, Sanolabor, Euronda, Dürr, Henry Schein, the five aliases moved | 3 h | 1 h |
| 4 | Seed and sync remove moved names from both name tables, with a report | 2 h | 0.75 h |
| 5 | Subcontractor routing (§ 4.4): brand-scoped linker per listing brand, staged links incl. `map-supplier`, manufacturer stored at staging; human-bind guard and hidden button (§ 4.5 item 3) | 6.5 h | 2.5 h |
| 6 | Review reason and fix; picker scope, `check_items` and link check for listing brands; data-quality page (§ 4.6) | 4 h | 1.5 h |
| 7 | Dry run on production, read-only, on stored evidence: the new checks over the 182 whole-range documents and the 25 subcontractor documents; T0-only documents reported as "cannot tell without a re-read" | 2 h | 0.75 h |
| 8 | Docs: decisions, PRD C4/C16 and flags, Review guide EN and SL with bundles, `test_docs_sets` flag vocabulary, handlers, limits | 2.5 h | 1 h |
| 9 | Full suite, deploy, post-deploy check | 1 h | 0.5 h |
| | **Total** | **27 h** | **10.25 h** |

## 6. Manual work for Nataša

One-time (UI only, no code):

| Task | Time |
|---|---|
| Doc 165: Reject, Reopen, Change type to specific items, tick Sure Dent's ~30 items (GPPR, PPPR, GPFQ, PPFQ) | ~10 min |
| Docs 1061, 1063: Reject | ~2 min |
| Docs 458, 383: tick 000 ENFILL, 000 ENPACK, 000 ENGO13 000 under KOMET | ~5 min |
| Doc 390 (CeraSeal = Komet BioSeal): tick 000 BCS1 000 | ~2 min |
| Optional: Chirana, ALLE and Adranox certificates 587, 588, 2199, 2192: Change type, tick items | ~20 min |
| **Total** | **~40 min** |

Ongoing:
- Subcontractor documents: a few a month (F10), 2-10 minutes each. About 15-30 minutes a month.

Doc 165 and the certificates depend on Change type (separate session, `docs/superpowers/specs/2026-11-12-change-type-design.md`).

## 7. Testing

- Table-driven tests on the real cases:
  - 165, a Sure Dent certificate through a Komet group: stages, `subcontractor`, KOMET stored, never bound;
  - a person sending bind-manufacturer KOMET for 165: refused;
  - 1061, an IFU read as whole range: never binds;
  - 2192, Adranox printing EURONDA: T0 abstains (two hits); with a T1 issuer ADRANOX it is a subcontractor document;
  - 172, Komet's own certificate: binds as today;
  - a brand certificate whose printed legal name resolves to nothing, through its own group: binds as today;
  - 284, IVOCLAR's legal name (multi-code brand): binds as today;
  - a Shofu certificate (issuer SHOFU, no Komet group): binds Shofu's range, no flag;
  - a Sure Dent declaration without a group: links to the Komet items it prints, staged, `subcontractor`, KOMET stored;
  - approving with some pre-ticks unticked publishes exactly the ticked ones;
  - 458, a Woodpecker declaration: the picker searches and accepts ENFILL and ENPACK;
  - the data-quality page shows Sure Dent as a known subcontractor, not an alias suggestion.
- Playbook: a listed name that is an alias of any brand is refused; a moved name is removed by seed and sync and reported.
- **Dry run pass criterion,** decided before running: among the 42 linked whole-range documents, only 165, 1061 and 1063 would fail. Every other failure is listed and reviewed before shipping.
- Full suite before commit (`playbooks/` changes).

## 8. Decisions needed

- **D1 (Denis):** this design and its estimate.
- **D2: answered.**
  - Chirana, ALLE and Vatech are subcontractors (Denis, 2026-11-13; Vatech also by FDA K240040).
  - Angelus is an independent manufacturer, not part of Henry Schein. It is listed as a subcontractor for safety (§ 4.1).
  - Adranox is a Euronda maker by its own certificate, not found online. Nataša can confirm when convenient.
- **D3: answered.** CeraSeal was renamed KometBioSeal by Komet; Meta Biomed makes it (zm-online, Dental Tribune). BC item `000 BCS1 000`.

## 9. The rejected full design (for the record)

The full design modelled each maker as its own manufacturer entity, linked to the brand by a relation, with:
- canonical = issuer;
- an `affiliate` name kind;
- relation-aware readers (EUDAMED, BC push, catalogue);
- automatic links from the BC maker code and from SPECIFIKACIJA;
- a currency rule and a repair module.

A Fable consultation and an independent review shaped it, and the review put it at 68 h human / 27 h Claude Code. It was rejected on proportion. It would save about 40 minutes once and less than half an hour a month of Nataša's time.

What carried over from that work into this design:
- the issuer check relative to the brand being bound (Woodpecker, Shofu and Becht keep binding their own ranges);
- T0's "anywhere" match being unsafe;
- the type guard;
- the sync not deleting names.

## 10. Second independent review (2026-11-13, lean version)

A fresh reviewer checked the lean spec against `main` b637b34. Its findings, all taken in:
1. A person could still bind a subcontractor's certificate to the brand through bind-manufacturer: guard and hidden button (§ 4.5 item 3).
2. T0 outputs only canonicals, so after the move it could call a subcontractor's document the brand's at 0.97: T0 counts listed names (§ 4.3). The planned "holder label" check is dropped, since T0 cannot see a moved name at all.
3. The issuer rule was undefined for names that resolve to nothing: they now bind as today, and the comparison is by code-set overlap (§ 4.5 item 2).
4. Declarations without a group would lose their links (`manufacturer-unresolved`, blocking): a brand-scoped linker for listed names (§ 4.4).
5. The data-quality page would suggest re-adding the alias (§ 4.6).
6. The staged cap must include `map-supplier`, and GATE must store B at staging (§ 4.4).
7. Removing names is structurally safe: no foreign keys, RESOLVE re-inserts only BC codes, groups carry code-derived names.
8. The list must hold the printed legal names, with an explicit matching rule (§ 4.2).
9. The type guard would have reversed ruling C16 for declarations: narrowed to IFU and other (§ 4.5 item 1).

Its estimate was 11.75 h Claude Code. Dropping the holder-label check brings it to 10.25 h (§ 5).
