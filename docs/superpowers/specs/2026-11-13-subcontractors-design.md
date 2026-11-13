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
"subcontractors": ["Sure Dent Corporation", "Meta Biomed Co., Ltd.", "WOODPECKER", "SHOFU", "BECHT",
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

### 4.2 Detection

A document is **a subcontractor's document for brand B** when its printed issuer is on B's `subcontractors` list (read by T1/T2, or by T0 at a manufacturer or holder label), **and** it reached B: it came through one of B's item groups, or its printed article numbers match B's items.

- **Relative, not global.** Shofu's own certificate for Shofu's own items is Shofu's document, processed as today, even though Shofu is on Komet's list. Only when the same company's document lands on Komet does the subcontractor rule apply.
- **A listed subcontractor's document that reaches no brand:** an upload whose printed name resolves to no manufacturer, such as a Sure Dent certificate dropped on Import, also waits on Review. The list names every brand it makes for as the suggestion, and the picker is free. There are no pre-ticks, because nothing matched.
- **A document whose manufacturer is a listed subcontractor with its own brand** (Woodpecker's 458/383, filed under WOODPECKER): processed as today. The picker and `check_items` additionally offer the items of the brands that list it (§ 4.4), so Nataša can add the Komet items.

### 4.3 Publishing check (all documents, not only subcontractors)

A **machine whole-range bind** happens only when all three hold:
1. the type is an EC or ISO certificate. Never IFU, DoC or other. This stops 1061/1063. Brand-wide declarations stop binding by machine as well and go to Review; the dry run counts them;
2. the printed issuer resolves to the brand being bound. A different company stages with `issuer-not-brand`, so a document fetched for a Komet group but printed by Sure Dent no longer inherits Komet (F1);
3. the issuer was read by T1/T2, or by T0 at a manufacturer or holder label. T0's "name anywhere in the text" alone stages with `issuer-unconfirmed` (F2, doc 2192). A person approves with one click.

New VALIDATE/GATE flags: `issuer-not-brand`, `issuer-unconfirmed`, `subcontractor`. This is a PRD change to C4/C16.

### 4.4 What happens to a subcontractor's document

- **Never publishes by itself.** Links it would get from printed article numbers are written **staged**, as pre-ticked suggestions, not production. The document waits on Review.
- **Review shows the reason and the fix:**
  - Certificate: "Sure Dent is a known subcontractor of KOMET. A subcontractor's certificate cannot cover all of KOMET. Change type to *specific items*, then tick the items Sure Dent makes."
  - Declaration: "Sure Dent is a known subcontractor of KOMET. Check the ticked items and approve."
- **Picker:** opens on the brand (KOMET). For a document whose manufacturer is a listed subcontractor that is also its own brand (Woodpecker's 458/383), the picker offers the brand's items as well. `check_items` and Today's link check accept items of a brand that lists the document's manufacturer as a subcontractor.
- When a person approves, the document's manufacturer is the brand (as for 384/387 today), and its links cover only the ticked items.

### 4.5 Accepted residual risks

- On the items a person ticks, the subcontractor's certificate competes with the brand's by date. Those are items the subcontractor makes, never the whole brand. Who the legal manufacturer is (Komet or Sure Dent) is Nataša's regulatory call, recorded in limits.
- A group company's certificate, aliased under its brand, can still bind the brand's range, if a paid tier read the issuer. None does wrongly today. Revisit if one appears.
- A subcontractor nobody has listed (Adranox) still stages under check 2. It just lacks the "known subcontractor" wording.

## 5. Work

| # | What | Human | CC |
|---|---|---|---|
| 1 | Publishing check (§ 4.3): T0 records whether its identity match sits at a manufacturer or holder label (beside `_named_outside_rep_block`); VALIDATE passes the printed issuer and its source next to the group's manufacturer; GATE's three conditions; flags; table tests | 6 h | 2.5 h |
| 2 | `subcontractors` list: parse, validate, body round-trip; data: the five aliases moved, Komet's list (Sure Dent, Meta Biomed, Woodpecker) | 3 h | 1 h |
| 3 | Sync removes the moved names, with a report | 1.5 h | 0.5 h |
| 4 | Detection (§ 4.2), staged pre-ticks, Review reason and suggested fix, picker opening on the brand, Woodpecker's brand items in picker, `check_items` and link check | 5 h | 2 h |
| 5 | Dry run on production, read-only: the new check over all 182 whole-range documents and the 25 subcontractor documents, reviewed before shipping | 1.5 h | 0.5 h |
| 6 | Docs: decisions (incl. superseding ruling D3 of 2026-08-18), PRD C4/C16 and flags, Review guide EN and SL, playbook authoring note, limits | 2 h | 0.75 h |
| 7 | Full suite, deploy, post-deploy check | 1 h | 0.5 h |
| | **Total** | **20 h** | **7.75 h** |

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
- Brand certificates now held by check 3: one click each, a few a month.

Doc 165 and the certificates depend on Change type (separate session, `docs/superpowers/specs/2026-11-12-change-type-design.md`).

## 7. Testing

- Table-driven tests on the real cases:
  - 165, a Sure Dent certificate through a Komet group: stages, `subcontractor` + `issuer-not-brand`, KOMET suggested;
  - 1061, an IFU: never binds;
  - 2192, issuer known only from T0 "anywhere": `issuer-unconfirmed`;
  - 172, Komet's own certificate with a T1 issuer: binds as today;
  - 384, a Sure Dent declaration: links staged and pre-ticked, not production;
  - 458, a Woodpecker declaration: the picker offers ENFILL and ENPACK, and `check_items` accepts them;
  - 587, a Chirana certificate under Sanolabor: stages.
- Shofu's own certificate (issuer SHOFU, no Komet group): binds Shofu's range as today, no subcontractor flag.
- A Sure Dent certificate uploaded without a group: waits on Review, KOMET suggested, no pre-ticks.
- Playbook: a subcontractor name that is also an alias is refused; a moved name is removed by sync and by the entity seed, and reported.
- **Dry run pass criterion,** decided before running: among the 42 linked whole-range documents, only 165, 1061 and 1063 would fail. Every other failure, and every brand certificate that check 3 would now hold, is listed and reviewed before shipping.
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
- T0's "anywhere" match not being enough;
- the type guard;
- the sync not deleting names.
