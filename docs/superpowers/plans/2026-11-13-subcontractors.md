# Subcontractors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A document printed by a listed subcontractor never binds its brand's whole range and never publishes by itself. It waits on Review under its brand with the reason, pre-ticked items and a suggested fix.

**Architecture:**
- A `subcontractors` body key in brand playbooks, read through one module, `app/subcontractors.py`.
- T0 counts listed names, so a text naming both a brand and its subcontractor abstains.
- VALIDATE routes listed issuers to the listing brand's items, staged.
- GATE adds three checks to whole-range binding (type, issuer, human bind).
- The web picker, `check_items`, the link check and the data-quality page accept a listing brand's items.

**Tech Stack:** Python 3.12, psycopg 3, raw SQL, FastAPI + Jinja + HTMX, pytest against real Postgres via `./scripts/test.sh`.

**Spec:** `docs/superpowers/specs/2026-11-13-subcontractors-design.md` (approved by Denis 2026-11-13).

## Global Constraints

- Only `gate.candidate` / `gate.apply` write `document`, `item_document` and `evidence` (invariant 1). The web stays a job producer.
- No new job type, no migration, no new `match_basis`. `subcontractors` is a playbook **body** key (it survives the seed, a database reload and the web editor; spec F5).
- Tests run only through `./scripts/test.sh`. Never bare pytest.
- `playbooks/` changes in Task 8 require the **full suite** before commit.
- Commit messages: plain imperative, repo style (`area: what`), no mention of Claude or AI, no attribution lines.
- A UI change is done only when `docs/guide/pages/review.md` and `review.sl.md` match and `python3 scripts/build-guide.py` has been re-run and the bundles committed.
- Matching rule (spec § 4.2): a printed name matches a list entry when `manufacturers.normalize(printed) == normalize(entry)`, or when `entry` is in `manufacturers.resolve_canonicals(conn, printed)`.
- An entry that is another playbook's `manufacturer` (WOODPECKER, SHOFU, BECHT) means that manufacturer. Any other entry must not fold-equal any playbook's `names()`.
- Flag names, exactly: `subcontractor`, `issuer-not-brand`.
- Review wording, exactly:
  - certificate: "{maker} is a known subcontractor of {brand}. A subcontractor's certificate cannot cover all of {brand}. Change type to specific items, then tick the items {maker} makes."
  - declaration: "{maker} is a known subcontractor of {brand}. Check the ticked items and approve."

## Review Focus

1. **A printed name with different punctuation or case** ("CHIRANA T. Injecta, a.s." against "CHIRANA T. Injecta a.s."). Expected: matched through `normalize`, or both spellings listed. The test goes in Task 1.
2. **A subcontractor listed under two brands** (Woodpecker for Komet and possibly another). Expected: `listing_brands` returns both, sorted, GATE stores no manufacturer, and Review names both. Tests in Task 1 and Task 5.
3. **A brand's own certificate whose printed legal name resolves to nothing** (an unaliased spelling). Expected: binds as today; `issuer-not-brand` fires only for a *known* different manufacturer. Test in Task 5.
4. **A document that becomes a subcontractor's only on re-read** (an old extraction carries the brand's canonical from T0 through the old alias). Expected: no change until re-read, and repaired by hand (spec § 4.7). This is covered by Task 9's dry-run report, not by a unit test.
5. **Approving with a pre-tick unticked.** Expected: only the ticked items publish, because the picker sends `remove`. Test in Task 6.

---

### Task 1: The `subcontractors` list and its lookups

**Files:**
- Modify: `app/playbooks.py` (`Playbook` dataclass, `_parse`, `validate`)
- Create: `app/subcontractors.py`
- Test: `tests/test_subcontractors.py` (new), `tests/test_playbooks.py`

**Interfaces:**
- Produces:
  - `Playbook.subcontractors: tuple[str, ...] = ()`;
  - `app.subcontractors.listing_brands(conn, printed: str | None, playbooks=None) -> list[str]`: the sorted `pb.manufacturer` of every playbook whose list matches `printed` (Global Constraints matching rule);
  - `app.subcontractors.brands_listing(manufacturer: str | None, playbooks=None) -> list[str]`: the sorted brands whose list contains `manufacturer` as an entry (for WOODPECKER, SHOFU, BECHT);
  - `app.subcontractors.item_codes_with_listing_brands(conn, manufacturer: str | None, playbooks=None) -> list[str]`: the sorted union of `manufacturers.item_codes_for(conn, manufacturer)` and `item_codes_for(conn, b)` for each b in `brands_listing(manufacturer)`;
  - `app.subcontractors.moved_names(playbooks) -> set[str]`: the `normalize`d entries that are not any playbook's `manufacturer`. These are the names Task 2 removes from the name tables.

  `playbooks=None` means `playbooks_mod.load_playbooks()`.

- [ ] **Step 1: Write the failing tests** in `tests/test_subcontractors.py`, building playbooks in memory:
  - KOMET with `subcontractors=("Sure Dent Corporation", "WOODPECKER")`;
  - WOODPECKER with `aliases=("Guilin Woodpecker Medical Instrument Co.,Ltd",)`;
  - SANOLABOR with `("CHIRANA T. Injecta a.s.",)`.
  
  Seed `manufacturer_alias` rows for WOODPECKER's alias and code 430, and KOMET's code 077.
  - `test_listing_brands_matches_a_listed_name`: `listing_brands(conn, "SURE DENT CORPORATION", pbs) == ["KOMET"]`
  - `test_listing_brands_ignores_punctuation`: `listing_brands(conn, "CHIRANA T. Injecta, a.s.", pbs) == ["SANOLABOR"]`. This passes only if `normalize` strips the comma; if it fails, list both spellings in Task 8 and keep the test, asserting the list form.
  - `test_listing_brands_resolves_an_alias_to_a_listed_canonical`: `listing_brands(conn, "Guilin Woodpecker Medical Instrument Co.,Ltd", pbs) == ["KOMET"]`
  - `test_listing_brands_unknown_name`: `listing_brands(conn, "Ivoclar Vivadent AG", pbs) == []`; `listing_brands(conn, None, pbs) == []`
  - `test_listing_brands_two_brands_sorted`: add a second brand listing WOODPECKER, then `== ["AAA", "KOMET"]`
  - `test_brands_listing_woodpecker`: `brands_listing("WOODPECKER", pbs) == ["KOMET"]`; `brands_listing("SURE DENT", pbs) == []`
  - `test_item_codes_with_listing_brands`: `item_codes_with_listing_brands(conn, "WOODPECKER", pbs) == ["077", "430"]`, with item_mirror rows for both codes
  - `test_moved_names`: `moved_names(pbs) == {normalize("Sure Dent Corporation"), normalize("CHIRANA T. Injecta a.s.")}`. WOODPECKER is excluded.
  
  In `tests/test_playbooks.py`:
  - `test_subcontractors_parse`: `_parse("komet", {"manufacturer": "KOMET", "subcontractors": ["Sure Dent Corporation"]}).subcontractors == ("Sure Dent Corporation",)`
  - `test_subcontractor_that_is_an_alias_is_refused`: `validate` raises `PlaybookConflict` when KOMET lists "Guilin Woodpecker Medical Instrument Co.,Ltd" while WOODPECKER has it as an alias
  - `test_subcontractor_that_is_own_name_is_refused`: KOMET listing "KOMET" raises `PlaybookConflict`
  - `test_another_playbooks_canonical_is_allowed`: KOMET listing "WOODPECKER" passes

- [ ] **Step 2: Run them, expect failure**

Run: `./scripts/test.sh tests/test_subcontractors.py tests/test_playbooks.py`
Expected: FAIL (`ModuleNotFoundError: app.subcontractors`, `unexpected keyword 'subcontractors'`).

- [ ] **Step 3: Implement.**
  - Add the field to `Playbook`.
  - `_parse` reads `data.get("subcontractors", [])` and raises `ValueError` on a non-string or empty entry.
  - `validate` adds the two refusals inside the existing per-playbook loop, using `manufacturers.normalize` for comparison against every playbook's `names()`.
  - `app/subcontractors.py` has the five functions in Interfaces. Nothing else.

- [ ] **Step 4: Run, expect pass**

Run: `./scripts/test.sh tests/test_subcontractors.py tests/test_playbooks.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/playbooks.py app/subcontractors.py tests/test_subcontractors.py tests/test_playbooks.py
git commit -m "playbooks: a subcontractors list, never also a brand's name"
```

### Task 2: Seed and sync remove names that moved to a subcontractors list

**Files:**
- Modify: `app/cli.py` (`sync_aliases`, ~183-217), `app/manufacturer_seed.py` (`seed`, `_link_name` ~212-236, `SeedStats`)
- Test: `tests/test_playbooks_sync.py`, `tests/test_manufacturer_seed.py`

**Interfaces:**
- Consumes: `subcontractors.moved_names(loaded)`.
- Produces:
  - `sync_aliases(...)` stats gain `"removed": int` and `"removed_detail": list[str]` (the raw_names deleted);
  - `SeedStats.names_removed: int`.

- [ ] **Step 1: Write failing tests**
  - `test_playbooks_sync.py::test_sync_removes_an_alias_moved_to_subcontractors`: seed `manufacturer_alias('Sure Dent Corporation', 'KOMET', 'playbook')`. Run `sync_aliases` with KOMET listing "Sure Dent Corporation" in `subcontractors` and not in `aliases`. Assert the row is gone, `stats["removed"] == 1`, and `"Sure Dent Corporation" in stats["removed_detail"]`.
  - `test_sync_keeps_aliases_not_listed`: the other KOMET aliases stay.
  - `test_manufacturer_seed.py::test_seed_removes_a_name_moved_to_subcontractors`: a `manufacturer_name` row (kind `alias`, "Sure Dent Corporation", KOMET's id). After `_seed` with a KOMET playbook listing it as a subcontractor, the row is gone and `stats.names_removed == 1`.

- [ ] **Step 2: Run, expect failure**

Run: `./scripts/test.sh tests/test_playbooks_sync.py tests/test_manufacturer_seed.py`
Expected: FAIL.

- [ ] **Step 3: Implement.** In both writers, after validation and before the inserts, delete rows whose `normalize(raw_name)` / `name_folded` is in `moved_names(loaded)`, and count them. The dry-run paths (`manufacturers seed` without `--apply`) report them without committing, as they already do for inserts. Print `removed: N (…names)` in `cmd_playbooks` and the seed's report.

- [ ] **Step 4: Run, expect pass** (same command).

- [ ] **Step 5: Commit**

```bash
git add app/cli.py app/manufacturer_seed.py tests/test_playbooks_sync.py tests/test_manufacturer_seed.py
git commit -m "playbooks: seed and sync remove a name that moved to subcontractors"
```

### Task 3: T0 counts listed subcontractor names

**Files:**
- Modify: `app/extract/t0_templates.py` (`extract_manufacturer`, ~1314-1373)
- Test: `tests/test_t0.py`

**Interfaces:**
- Produces: `extract_manufacturer` may return the **listed entry itself** (e.g. "Sure Dent Corporation") as the manufacturer value. It abstains (`None`) when a brand name and a listed subcontractor name both occur. `steering_playbook` is unchanged: a subcontractor value has no playbook, so nothing steers.

- [ ] **Step 1: Write failing tests** in `tests/test_t0.py`, with in-memory playbooks as in Task 1:
  - `test_t0_identity_subcontractor_alone`: the text "Manufacturer: Sure Dent Corporation #809 …" → value "Sure Dent Corporation"
  - `test_t0_identity_brand_and_subcontractor_abstains`: the text contains "Sure Dent Corporation" and "KOMET" → `None`
  - `test_t0_identity_listed_canonical_not_double_counted`: KOMET lists WOODPECKER; a Woodpecker text containing only "Guilin Woodpecker Medical Instrument Co.,Ltd" → "WOODPECKER" (one hit, not two)
  - `test_t0_identity_brand_alone_unchanged`: "Gebr. Brasseler GmbH & Co. KG" → "KOMET", conf 0.97

- [ ] **Step 2: Run, expect failure**

Run: `./scripts/test.sh tests/test_t0.py`
Expected: the first two fail.

- [ ] **Step 3: Implement.** After the existing per-playbook loop, add hits for each `pb.subcontractors` entry that is not any playbook's `manufacturer` and is at least `_MIN_IDENTITY_LEN` long, found by `_fold` the same way. The hit value is the entry. `unique` and the confidence logic stay as they are.

- [ ] **Step 4: Run** `./scripts/test.sh tests/test_t0.py tests/test_t0_corpus.py`. Expected: PASS. If `test_t0_corpus` changes a count, fixtures without `subcontractors` cannot change, so investigate before touching it.

- [ ] **Step 5: Commit**

```bash
git add app/extract/t0_templates.py tests/test_t0.py
git commit -m "t0: a listed subcontractor's name is its own identity, never the brand's"
```

### Task 4: VALIDATE routes a listed issuer to its brand

**Files:**
- Modify: `app/handlers/validate.py`: the whole-range route (~875-905) and the no-group REF route (~950-983)
- Test: `tests/test_validate_handler.py`

**Interfaces:**
- Consumes: `subcontractors.listing_brands(conn, printed)`.
- Produces: `gate.candidate` payload additive fields:
  - `subcontractor_of: list[str]` (sorted listing brands), present only when non-empty;
  - flag `subcontractor`.

- [ ] **Step 1: Write failing tests** (table-driven, existing fixtures: a KOMET group with items `000 GPPR04 045` and `000 GPFQ04 020`, KOMET listing "Sure Dent Corporation"):
  - `test_whole_range_subcontractor_through_brand_group`: scope manufacturer, printed "Sure Dent Corporation", group KOMET → the candidate route is `mfr-binding`, flags contain `subcontractor`, `subcontractor_of == ["KOMET"]`, `manufacturer == "KOMET"`
  - `test_whole_range_subcontractor_no_group`: same without a group → `manufacturer == "KOMET"`, flag `subcontractor`
  - `test_whole_range_own_brand_shofu`: printed "SHOFU INC." resolving to SHOFU, SHOFU listed by KOMET, a SHOFU group → no `subcontractor` flag
  - `test_declaration_subcontractor_no_group_links_brand_items`: a declaration, printed "Sure Dent Corporation", ref_list `["GPPR04.000.045"]` (or the exact form the existing ref-item matcher accepts for `000 GPPR04 045`), no group → links contain `000 GPPR04 045`, flags contain `subcontractor` and **not** `manufacturer-unresolved`, `subcontractor_of == ["KOMET"]`
  - `test_declaration_subcontractor_through_group`: same through the KOMET group → flag `subcontractor`
  - `test_declaration_unlisted_unknown_name_unchanged`: printed "Nobody GmbH" → `manufacturer-unresolved`, as today

- [ ] **Step 2: Run, expect failure**

Run: `./scripts/test.sh tests/test_validate_handler.py`
Expected: FAIL.

- [ ] **Step 3: Implement.**
  - Compute `printed = _val(fields, "manufacturer")` and `brands = listing_brands(conn, printed)` once.
  - **Whole-range route:** if `brands`, and either there is no group or the group's canonical is in `brands`: add the `subcontractor` flag and `subcontractor_of`. When there is no group and `len(brands) == 1`, set `manufacturer = brands[0]`.
    - "The document reached B" is relative (spec § 4.4). A Shofu certificate through a Shofu group is not a subcontractor document even though KOMET lists SHOFU, because its group's canonical (SHOFU) is not in `brands`.
  - **No-group REF route:** when `canonical_manufacturer` finds no groups and `brands` is non-empty, run `_links_for_manufacturer(conn, b, ref_list, basic_udi_di, basis_override)` for each b. Merge the links, choose `resolved_group_id` from the first that linked, add `subcontractor`, set `subcontractor_of`, and do **not** add `manufacturer-unresolved`.
  - **With a group** whose canonical is in `brands`: add `subcontractor` and `subcontractor_of`.

- [ ] **Step 4: Run, expect pass** (same command).

- [ ] **Step 5: Commit**

```bash
git add app/handlers/validate.py tests/test_validate_handler.py
git commit -m "validate: a listed subcontractor's document is routed to its brand's items"
```

### Task 5: GATE holds subcontractor documents and guards whole-range binding

**Files:**
- Modify: `app/handlers/gate.py`:
  - `CAPPING_FLAGS` (~99);
  - `_handle_mfr_binding` (~1224-1272);
  - the REF route's `_bound_here` (~1630-1633);
  - `bind-manufacturer` (~2457-2482).
- Test: `tests/test_gate_candidate_handler.py`, `tests/test_gate_apply_handler.py`

**Interfaces:**
- Consumes: payload `subcontractor_of`, flag `subcontractor`; `subcontractors.listing_brands`.
- Produces:
  - `gate._issuer_not_brand(conn, printed: str | None, brand: str) -> bool`: True when `resolve_canonicals(conn, printed)` is non-empty and **none** of those canonicals shares a BC code with `brand` (`set(item_codes_for(c)) & set(item_codes_for(brand))` empty for every c). False when printed is empty or resolves to nothing;
  - flag `issuer-not-brand` on the staged task;
  - `gate.subcontractor_bind_refusal(conn, doc_id: int, manufacturer: str) -> str | None`: the refusal sentence when any `manufacturer` evidence value of the document lists `manufacturer` in `listing_brands`, else None. The web route uses it in Task 7.

- [ ] **Step 1: Write failing tests**
  - `test_gate_candidate_handler.py`:
    - `test_mfr_binding_subcontractor_stages_with_brand_stored`: mfr-binding payload, flags `["subcontractor"]`, `subcontractor_of ["KOMET"]`, manufacturer KOMET, confident fields → document `staged`, `canonical_manufacturer == "KOMET"`, 0 links, the manual task carries the flag
    - `test_mfr_binding_subcontractor_two_brands_stores_none`: `subcontractor_of ["AAA", "KOMET"]` → `canonical_manufacturer IS NULL`
    - `test_mfr_binding_ifu_never_binds`: fields type `IFU`, scope manufacturer, ULTRADENT → staged, 0 links
    - `test_mfr_binding_issuer_not_brand`: printed "Ivoclar Vivadent AG" (IVOCLAR alias), payload manufacturer KOMET (group inheritance) → staged, flag `issuer-not-brand`
    - `test_mfr_binding_unresolved_legal_name_binds_as_today`: printed "Unknown Legal Name GmbH", payload manufacturer KOMET → production, mfr-scope links
    - `test_mfr_binding_multicode_brand_binds`: printed "Ivoclar Vivadent AG", manufacturer IVOCLAR (codes 001/005/275) → production
    - `test_ref_route_subcontractor_links_staged_brand_stored`: REF payload, links `ref-item` on `000 GPPR04 045`, flags `["subcontractor"]`, `subcontractor_of ["KOMET"]`, high score → document `staged`, link status `staged`, `canonical_manufacturer == "KOMET"`
    - `test_ref_route_subcontractor_caps_map_supplier`: the same with basis `map-supplier` → link `staged`
  - `test_gate_apply_handler.py`:
    - `test_bind_manufacturer_refused_for_listed_subcontractor`: a staged whole-range document with evidence manufacturer "Sure Dent Corporation", `gate.apply` bind-manufacturer KOMET → raises `ValueError`, matching "known subcontractor of KOMET"; no links, status unchanged
    - `test_bind_manufacturer_brand_own_doc_unchanged`: evidence "Gebr. Brasseler GmbH & Co. KG" → binds as today

- [ ] **Step 2: Run, expect failure**

Run: `./scripts/test.sh tests/test_gate_candidate_handler.py tests/test_gate_apply_handler.py`
Expected: the new tests fail.

- [ ] **Step 3: Implement.**
  - Add `"subcontractor"` to `CAPPING_FLAGS`. The link status then follows the staged document (`gate.py:1697-1701`), which caps every basis.
  - In `_handle_mfr_binding`, extend the `bound = …` condition. It becomes None when any of these holds:
    - `"subcontractor" in flags`;
    - `_val(fields, "type") in ("IFU", "other")`;
    - `_issuer_not_brand(conn, _val(fields, "manufacturer"), canonicals[0])` (also appending `issuer-not-brand` to `flags`).
  - In the `bound is None` branch, pass `canonical_manufacturer=p["subcontractor_of"][0]` to `_upsert_document` when `len(p.get("subcontractor_of") or []) == 1`.
  - REF route: `_bound_here` falls back to that same single brand.
  - `bind-manufacturer` calls `subcontractor_bind_refusal(conn, doc_id, p.get("manufacturer"))` before `_promote` and raises `ValueError` with it.

- [ ] **Step 4: Run, expect pass** (same command, plus `./scripts/test.sh tests/test_gate_apply_items.py`).

- [ ] **Step 5: Commit**

```bash
git add app/handlers/gate.py tests/test_gate_candidate_handler.py tests/test_gate_apply_handler.py
git commit -m "gate: a subcontractor's document stays staged under its brand; no whole-range bind for manuals or another company's issuer"
```

### Task 6: Listing brands' items in the picker, item check, link check and data quality

**Files:**
- Modify:
  - `web/item_picker.py` (`_codes`, ~114);
  - `app/item_picks.py` (`check_items`, ~28);
  - `web/link_checks.py` (`check`, `owned[mfr]` ~79);
  - `app/unmatched_names.py` (the suggestion loop ~220-240, `Suggestion.level`).
- Test: `tests/test_web_item_picker.py`, `tests/test_item_picks.py`, `tests/test_web_today.py`, `tests/test_unmatched_names.py`

**Interfaces:**
- Consumes: `subcontractors.item_codes_with_listing_brands`, `subcontractors.listing_brands`.
- Produces: `Suggestion.level` gains the value `"subcontractor"`, with `Suggestion.manufacturer` set to the listing brands joined by ", ".

- [ ] **Step 1: Write failing tests**
  - `test_item_picks.py::test_check_items_accepts_listing_brand_items`: document manufacturer WOODPECKER, KOMET lists WOODPECKER, item `000 ENFILL` (code 077) → `check_items(...) == {}`
  - `test_web_item_picker.py::test_picker_search_offers_listing_brand_items`: picker search for "ENFILL" on a WOODPECKER document returns `000 ENFILL`
  - `test_web_item_picker.py::test_approve_publishes_only_ticked_pre_ticks`: a staged subcontractor document with staged links on A and B; POST approve ticking A, with `remove=B` as the picker sends it → after running the `gate.apply` job, A is production and B is not
  - `test_web_today.py::test_link_check_accepts_listing_brand_items`: a published WOODPECKER document linked to `000 ENFILL` → `crossing == []`
  - `test_unmatched_names.py::test_listed_subcontractor_is_not_an_alias_suggestion`: printed "Sure Dent Corporation" with exclusive KOMET refs → suggestion level `"subcontractor"`, manufacturer "KOMET", never `"refs"`

- [ ] **Step 2: Run, expect failure**

Run: `./scripts/test.sh tests/test_item_picks.py tests/test_web_item_picker.py tests/test_web_today.py tests/test_unmatched_names.py`

- [ ] **Step 3: Implement.**
  - `_codes`, `check_items` and the link check's `owned[mfr]` call `item_codes_with_listing_brands`.
  - In `unmatched_names`, a printed name with non-empty `listing_brands` gets level `"subcontractor"` before the refs and name checks run. The data-quality template shows "Known subcontractor of {brands}" for that level (`web/templates/` data-quality partial; follow the existing level rendering).

- [ ] **Step 4: Run, expect pass** (same command, plus `./scripts/test.sh tests/test_web.py`).

- [ ] **Step 5: Commit**

```bash
git add web/item_picker.py app/item_picks.py web/link_checks.py app/unmatched_names.py web/templates tests/test_item_picks.py tests/test_web_item_picker.py tests/test_web_today.py tests/test_unmatched_names.py
git commit -m "web: a subcontractor's brand items in the picker, the item check, the link check and data quality"
```

### Task 7: Review shows the reason and the fix; whole-range button guarded; guide

**Files:**
- Modify:
  - `web/app.py`: `FLAG_EXPLANATIONS` (~323), the staging detail context, and the bind-manufacturer branch of the apply route (~5806);
  - `web/templates/_staging_doc_detail.html` (the whole-range panel, ~60);
  - `docs/guide/pages/review.md`, `docs/guide/pages/review.sl.md`; the bundles via `scripts/build-guide.py`.
- Test: `tests/test_web_item_picker.py` (Review rendering lives beside the picker tests), `tests/test_docs_sets.py` (existing, unchanged)

**Interfaces:**
- Consumes: `gate.subcontractor_bind_refusal`, payload/flag `subcontractor`, `subcontractor_of`, flag `issuer-not-brand`.
- Produces: `FLAG_EXPLANATIONS["subcontractor"]` and `FLAG_EXPLANATIONS["issuer-not-brand"]`.

- [ ] **Step 1: Write failing tests**
  - `test_review_shows_subcontractor_reason_certificate`: a staged whole-range document with evidence "Sure Dent Corporation" and the flag → the page contains exactly the certificate sentence from Global Constraints with maker "Sure Dent Corporation" and brand "KOMET"; the whole-range approve button is absent; the "Change type" control is present
  - `test_review_shows_subcontractor_reason_declaration`: a group-scope document → the declaration sentence; the picker's manufacturer is KOMET
  - `test_apply_bind_manufacturer_refused_with_reason`: POST bind-manufacturer KOMET for that document → 422, the body contains "known subcontractor of KOMET", no job enqueued
  - `FLAG_EXPLANATIONS["subcontractor"]` = "Made by a subcontractor of the brand, so nothing was published by itself"
  - `FLAG_EXPLANATIONS["issuer-not-brand"]` = "Printed by another company than the brand it was found for, so it was not bound to that brand's whole range"

- [ ] **Step 2: Run, expect failure**

Run: `./scripts/test.sh tests/test_web_item_picker.py tests/test_docs_sets.py`
Expected: the new tests fail; `test_every_validate_flag_is_explained_on_the_review_page` fails once the two entries exist and the guide does not yet quote them.

- [ ] **Step 3: Implement.**
  - Add the two `FLAG_EXPLANATIONS` entries.
  - Build the reason sentence in the staging-detail context from the evidence manufacturer value and the stored `canonical_manufacturer`, or from `subcontractor_of` when there are several brands, by type: EC/ISO gets the certificate sentence, everything else the declaration sentence.
  - The template hides the whole-range approve button when the context carries a subcontractor reason.
  - The apply route calls `subcontractor_bind_refusal` before enqueueing and answers 422 with it plus " Nothing was changed."
  - Add a "Subcontractor documents" row to `review.md` and `review.sl.md`, quoting both explanation sentences, the two Review sentences, and the fix steps (Change type → specific items → tick → Approve). Then run `python3 scripts/build-guide.py`.

- [ ] **Step 4: Run, expect pass** (same command).

- [ ] **Step 5: Commit**

```bash
git add web/app.py web/templates/_staging_doc_detail.html docs/guide tests/test_web_item_picker.py
git commit -m "review: a subcontractor's document says why it waits and what to do"
```

### Task 8: Playbook data

**Files:**
- Modify: `playbooks/komet.json`, `playbooks/sanolabor.json`, `playbooks/euronda.json`, `playbooks/duerr-dental.json`, `playbooks/henry-schein.json`
- Test: full suite

- [ ] **Step 1: Edit the playbooks.** Remove from `aliases`, and add to `subcontractors`:

  | Playbook | Remove from `aliases` | `subcontractors` |
  |---|---|---|
  | komet.json | "Sure Dent Corporation" | "Sure Dent Corporation", "Meta Biomed Co., Ltd.", "META BIOMED CO., LTD.", "WOODPECKER", "SHOFU", "BECHT", "CMS Dental A/S", "HANS KOREA CO., LTD.", "SAVUNA GmbH", "Stoddard Manufacturing Company Limited", "P. L. Superior Dental Materials GmbH" |
  | sanolabor.json | "CHIRANA T. Injecta a.s." | "CHIRANA T. Injecta a.s.", "CHIRANA T. Injecta, a.s." |
  | euronda.json | "ALLE S.R.L." | "ALLE S.R.L.", "ADRANOX SRL" |
  | duerr-dental.json | "VATECH Co., Ltd." | "VATECH Co., Ltd." |
  | henry-schein.json | "ANGELUS INDÚSTRIA DE PRODUTOS ODONTOLÓGICOS S/A" | "ANGELUS INDÚSTRIA DE PRODUTOS ODONTOLÓGICOS S/A" |

  Drop a second spelling if Task 1's punctuation test proved `normalize` folds it. Bump each edited playbook's `rev` by 1.

- [ ] **Step 2: Validate**

Run: `docker compose exec -T worker python -m app.cli playbooks validate`
Expected: exit 0.

- [ ] **Step 3: Full suite**

Run: `./scripts/test.sh`
Expected: all pass, except the known `test_scheduler::test_the_sweep_tick_marks_due_and_emits_nothing` (fixed-date failure since 2026-11-10). Report any other failure; do not commit over it.

- [ ] **Step 4: Commit**

```bash
git add playbooks/
git commit -m "playbooks: Komet, Sanolabor, Euronda, Dürr and Henry Schein list their subcontractors"
```

### Task 9: Read-only dry run on production

**Files:**
- Create: `tools/subcontractor_dry_run.py` (dev-time, read-only, outside the pipeline, like the rest of `tools/`)

**Interfaces:**
- Consumes: `subcontractors.listing_brands`, `gate._issuer_not_brand`, and the playbooks from the working tree (passed explicitly, since production's database still holds the old ones).

- [ ] **Step 1: Write the script.**
  - It runs inside the worker container with `conn.read_only = True` and `statement_timeout 20s`.
  - For every document with `coverage_scope='manufacturer'` and status `production`/`superseded`/`staged`, it prints `doc_id`, type, canonical, printed manufacturer (non-T0 evidence first, else the T0 value), today's link count, and what the new rules decide:
    - `subcontractor` (listing brands contain the canonical);
    - `issuer-not-brand`;
    - `type-not-whole-range` (IFU/other);
    - `t0-only` (the only manufacturer evidence is T0 "playbook identity": cannot tell without a re-read);
    - `unchanged`.
  - It prints the same for the 25 subcontractor documents listed in spec F7/F9 by doc_id.
  - It writes nothing.

- [ ] **Step 2: Run it on the server** (read-only; copy the script and the edited playbooks into the worker container's `/tmp`; delete them afterwards). Ask Denis before running it, as for any server action.

- [ ] **Step 3: Check against the pass criterion** (spec § 7). Among the 42 linked whole-range documents, only 165, 1061 and 1063 may come out other than `unchanged`/`t0-only`. List every other change for Denis before Task 10.

- [ ] **Step 4: Commit** the script.

```bash
git add tools/subcontractor_dry_run.py
git commit -m "tools: read-only dry run of the subcontractor rules over whole-range documents"
```

### Task 10: Docs and the release

**Files:**
- Modify:
  - `docs/decisions.md`: one row: subcontractors design, supersedes ruling D3 of 2026-08-18, type rule for IFU/other, issuer rule, human-bind guard;
  - `docs/dentalia-pipeline-contract-prd-v3.md`: C4/C16 conditions and the two flags (targeted lines only);
  - `docs/dev/handlers.md`: VALIDATE and GATE paragraphs;
  - `docs/dev/playbook-authoring.md`: the `subcontractors` key, the matching rule, and the deploy order;
  - `docs/dev/limits.md`: rows for the residual risks in spec § 4.7;
  - `docs/vocabulary.md`: the two flags.
- Test: `./scripts/test.sh tests/test_docs_sets.py`, then the full suite

- [ ] **Step 1: Write the docs** (targeted edits, never rewriting unaffected sections). The deploy order goes in `playbook-authoring.md`:
  1. deploy the code;
  2. `manufacturers seed --apply --adopt komet --adopt sanolabor --adopt euronda --adopt duerr-dental --adopt henry-schein`, reading the dry run's `removed` list first;
  3. `playbooks sync`;
  4. check that `manufacturer_alias` holds none of the moved names.

  Back up the two name tables first. The removal is a delete on production.

- [ ] **Step 2: Run** `./scripts/test.sh tests/test_docs_sets.py`, then `./scripts/test.sh`. Expected: PASS, with the known scheduler failure only.

- [ ] **Step 3: Commit**

```bash
git add docs/
git commit -m "docs: subcontractors in the contract, decisions, handlers, playbook authoring and limits"
```

- [ ] **Step 4: Hand over to Denis:**
  - the dry-run list;
  - the deploy order above;
  - the one-time manual repairs from spec § 6: doc 165's 30 items, 1061/1063 reject, 458/383 ENFILL/ENPACK/ENGO, 390 `000 BCS1 000`.
  
  Nothing is pushed or deployed without his word.
