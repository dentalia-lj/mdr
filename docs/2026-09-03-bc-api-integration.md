# Business Central REST API — what exists, what it maps to, and what is new work

**Date:** 2026-09-03
**Status:** working document. The endpoint is LAN-only; we have not called it.
**Source:** Luka Vidmar (b-s.si) to Nataša Palme and Denis, 2026-09-02 14:13, subject
the BC API thread. First read from screenshots; **replaced 2026-09-03
with the real JSON b-s.si sent** (`AllItems.txt`, `DataItems.txt`), saved verbatim to
[`samples/`](samples/). Everything below is now measured against that payload, not
inferred. The screenshot-era hypotheses that turned out wrong are marked where they
stood, because the corrections are the useful part.

Related: [`superpowers/specs/2026-08-25-item-document-access-design.md`](superpowers/specs/2026-08-25-item-document-access-design.md)
(the BC-facing link this API now has a field for), [`dev/limits.md`](dev/limits.md)
(the `bc_odata` stub), PHASES.md gap **G4** (`mfr_ref` source field, owner: client).

---

## 1. What b-s.si built

Two custom API pages on Dentalia's **on-premise** BC, publisher `dentalia`, group
`api`, version `v1.0`, single company `25ccc3d7-63f8-ec11-9e03-00155d012200`:

| Endpoint | Access | Purpose |
|---|---|---|
| `.../companies({id})/dataitems` | item no. + description read-only, three `pte*` fields **writable** | the compliance write-back surface |
| `.../companies({id})/allitems` | read-only, every item field | the ingest read surface |

Base observed: `http://denwebnav:7048/proddentalia-NAS/api/dentalia/api/v1.0/`.

**Observed facts about the transport**, all from the screenshots:

- Plain HTTP on port 7048. Chrome shows "Ni varno". There is no TLS today.
- `denwebnav` is an internal hostname. Luka states external access is blocked and
  that Dentalia's IT must open it, probably as an IP rather than the name.
- `$top=10` was accepted on `allitems`, so OData query options are live.
- Every record carries a real `@odata.etag`, so `If-Match` conditional writes are
  available on `dataitems`.
- `allitems` returns **242 properties per item**, including BC's system columns:
  `systemId`, `systemCreatedAt`, `systemModifiedAt`, `systemModifiedBy`.
- **Use `systemModifiedAt` for delta, not `lastDateTimeModified`.** They disagree
  badly: item `0.900.0001` reads `lastDateTimeModified` `2024-03-18` against
  `systemModifiedAt` `2026-06-02`, two years apart. `lastDateTimeModified` is an
  application field somebody has to maintain; `systemModifiedAt` is BC's own and
  cannot go stale. (`lastDateModified`/`lastTimeModified` are the same value split,
  and local time rather than UTC — ignore both.)
- **`systemId` is a stable GUID per item.** `no` is the business key and stays our
  `item_ref`, but a renumbering would be invisible to us today and `systemId` is what
  would make it visible. Worth storing.

## 2. The three writable fields

| Field | Type | Value on every row seen |
|---|---|---|
| `pteValidDeclarationOfConformity` | boolean | `false` |
| `pteValidCECertificate` | boolean | `false` |
| `pteWarehouseURL` | text, 250 | `""` |

All three also appear on `allitems`, read-only there.

**`pteWarehouseURL` already has a design.** The 2026-08-25 item-document access spec
specifies exactly this link, for exactly this reason (BC builds hyperlinks by string
concatenation and cannot compute anything):

```
https://api.cw.dentalia.si/item/{item_ref}?k={bc_link_key}
```

That fits 250 characters with room to spare. The access policy for it is **built**
(`web/access.py`, `bc` caller class, `/item/*` row; `cfg.web.bc_link_key` exists and
is wired through `access.guard`). The **route itself is not built** — grep of
`web/app.py` on 2026-09-03 finds `/items/{item_ref}` (staff HTML) and
`/api/items/{item_ref}/documents` (API key) but no `/item/{item_ref}`. So the URL we
would write into BC currently 404s.

**The two booleans have a modelling problem worth settling before they go live.** A
boolean has no "not checked yet". Today most items have no document at all, so every
row would read `false`, which a sales rep will read as "we checked and there is
none". Three ways out, cheapest first: accept it and say so in the guide; add a third
`pte*` text field carrying a status word; or leave the booleans empty until the item
has been through the pipeline, which BC's boolean type does not allow. This is a
Dentalia decision, not b-s.si's.

## 3. Field inventory, and how it maps to ours

Transcribed from the `allitems?$top=10` screenshot, **one fully visible record**
(`no: "0.900.0001"`, HANDPIECE MOTOR, LED, WITH TUBING). Confirm against a
live payload or `$metadata` before writing code against it.

Identity-relevant properties seen: `no`, `no2`, `description`, `searchDescription`,
`description2`, `type`, `baseUnitOfMeasure`, `inventoryPostingGroup`, `shelfNo`,
`itemDiscGroup`, `unitPrice`, `unitCost`, `vendorNo`, `vendorItemNo`, `tariffNo`,
`countryRegionOfOriginCode`, `gtin`, `itemCategoryCode`, `vatProdPostingGroup`,
`blocked`, `salesBlocked`, `purchasingBlocked`, `lastDateTimeModified`, `pteOldNo`,
`pteVendorItemNo`, `pteVendorItemDesc`, `pteMedicalDeviceClass`,
`pteManufCodePrimary`, `pteClass`, the three compliance fields, and a second
extension prefix `bsl*` (`bslRetailItem`, `bslRetailItemGroupNo`, ...).

**The record is the same item as the Excel export we ingest today**, cross-checked
2026-09-03 against `imports/Artikli 3.7.2026.xlsx`: `shelfNo` = `Št. police` = `9/P`;
`itemCategoryCode` = `Šifra kategorije artikla` = `ORDMAP`; `unitPrice` = `Cena
enote` = `2085`; `unitCost` = `Strošek enote` = `91.11`; `vatProdPostingGroup` =
`Knjižna skupina izdelka za DDV` = `B10`. The CSV export is a projection of
`allitems`, which is the good news: the API is a superset, not a different catalogue.

### 3.1 `LJ_ODATA_PROFILE` is wrong on all six keys — **FIXED 2026-09-07**

> Corrected in `app/adapters/source.py` together with `UnknownOdataProperty`, the
> guard that makes the next wrong name raise on the first record instead of
> yielding a catalogue of nulls. `manufacturer_raw` resolved to
> `pteManufCodePrimary`; `udi` was dropped rather than mapped to `gtin`, which is
> not a Basic UDI-DI. The section below is kept as the record of what was wrong.

`app/adapters/source.py:159` guesses PascalCase property names. BC v1.0 API pages
return camelCase. Nothing has ever run against a live endpoint, so the guess was
never tested.

| logical field | CSV column (live) | `LJ_ODATA_PROFILE` today | observed API property |
|---|---|---|---|
| `item_ref` | `Št.` | `No` | `no` **confirmed** |
| `name` | `Opis` | `Description` | `description` **confirmed** |
| `name_fallback` | `Opis za iskanje` | *absent* | `searchDescription` **confirmed** |
| `manufacturer_raw` | `Šifra proizvajalca` | `ManufacturerCode` | **unresolved**, see below |
| `mfr_ref` | `Dobaviteljeva št. artikla` | `VendorItemNo` | `vendorItemNo` **confirmed** |
| `md_class` | `Razred medicinskega pripomočka` | `MedicalDeviceClass` | `pteMedicalDeviceClass` **hypothesis** |
| `udi` | *(no column)* | `UDI` | `gtin` only candidate, see below |

`mfr_ref` is confirmed by value: the export's `Dobaviteljeva št. artikla` for
`0.900.0001` is `09000001`, and the API's `vendorItemNo` for the same item is
`09000001`. Note the API also carries an empty `pteVendorItemNo`, a second field with
the same meaning in its name. Ask which one is authoritative going forward.

**`manufacturer_raw` is settled: the property is `manufacturerCode`.** It is in the
payload, value `"011"`, on all three records. The screenshot-era guesses were both
wrong and are withdrawn:

| candidate | verdict |
|---|---|
| `manufacturerCode` | **correct.** Present, populated, matches the export's `Šifra proizvajalca` |
| `pteManufCodePrimary` | wrong. It is `Šifra proizvajalca (primarni)`: 16 of 19.091 rows, two values (`077-A`, `077-B`), an abandoned sub-brand refinement |
| `itemDiscGroup` | wrong. It reads `011` on these rows because these items happen to sit in discount group 011; it is not the manufacturer field |

That failure would be silent, which is a second, separate defect: `BcApiAdapter` calls
`_raw_from_profile` directly and skips the missing-column check `_ExportAdapter._checked`
applies to the CSV path (`app/adapters/source.py:229`). A wrong profile key yields
`None`, not an error. **Whatever else happens, the OData adapter needs that same
check before it is allowed to run.**

`udi`: `gtin` exists and is empty on all three records. Under GS1 the GTIN is the
UDI-DI carrier, but **Basic UDI-DI is a different identifier and this is not it**, so
`gtin` does not satisfy the Basic UDI-DI branch of the REF gate on its own. Treat it
as a new field worth having, not as the `udi` we specified.

### 3.1.1 Proof, run end to end

`BcApiAdapter` fed the three real records, against `CsvExportAdapter` reading the same
three items out of `imports/Artikli 3.7.2026.xlsx`:

```
A) with the CURRENT LJ_ODATA_PROFILE (PascalCase guess):
   item_ref=None name=None mfr=None mfr_ref=None md_flag=None      x3, no error raised

B) with the corrected profile:
   0.900.0001  (011, '09000001', md_flag=None)   IDENTICAL to xlsx
   0.900.0002  (011, '09000002', md_flag=None)   IDENTICAL to xlsx
   0.900.0003  (011, None, md_flag=False)       IDENTICAL to xlsx
```

Both halves matter. The current profile does not fail, it returns a catalogue of
nulls; and the corrected one reproduces the CSV path exactly, `md_flag=False`
included. That is the byte-identity contract invariant 11 depends on, demonstrated
rather than asserted.

**`pteMedicalDeviceClass` carries the export's own vocabulary**, `"NI MP"` and
`RAZRED <class>`, so `_parse_device_class` already handles it unchanged
(`NI MP -> (False, None)`, verified). Note what that implies for row counts: the
export holds 3.133 `NI MP` rows, and `app/handlers/ingest.py` drops `md_flag is False`
before the upsert as a counted `non_md`, deliberately. An OData switch inherits that
behaviour for free — but only because the vocabulary is identical. If b-s.si ever
normalises these values, the filter silently stops matching.

### 3.2 What this says about gap G4

G4 (owner: client) is "which BC field carries the manufacturer's catalogue number",
and the measured consequence on record is `missing_mfr_ref` = 7,082 / 15,958 ≈ 44.4%.
Measured on the export 2026-09-03, the number is worse than that fraction suggests:

- `Dobaviteljeva št. artikla` is non-empty on **10,587 of 19,091** rows (55.5%).
- Of those, **7,180 (67.8%) are exactly the item number with the dots removed**
  (`0.900.0001` → `09000001`).
- On the one row cross-checked against the API, that same value also appears as
  **`pteOldNo`** — BC's *old item number* field.

So for two thirds of the populated rows, the "supplier article number" is Dentalia's
own legacy item number, not the manufacturer's. Two readings, and they need different
answers: either this is client ruling **C12** working as intended (the primary item
number *is* what is printed on the article, `match_basis` `ref-item`), or it is legacy
fill and those rows carry no supplier REF at all. `pteOldNo` agreeing exactly points
at the second. That leaves roughly **3,407 rows (17.8%)** with a vendor number that is
demonstrably not the item number. Worth resolving with b-s.si, because it moves AC1.

### 3.3 Manufacturers cannot be synced from this API at all

> **Superseded in part, 2026-09-07.** b-s.si added an `allmanufacturers` page
> after this was written, and `app/vendor_master.py::read_odata` reads it into
> the same `VendorRow` tuple the file reader produces. **It has no caller** —
> no CLI flag, no handler, no route — and its two property names are still
> guessed, so the hand-fed spreadsheet remains the only reachable route.
> See [dev/deployment.md](dev/deployment.md) § 6.2.

`vendor_master` (migration 016) is `(code_source, code, name)` and is loaded from
`imports/Proizvajalci.xlsx`: **390 rows, columns `Šifra` + `Ime`**, resolving all 381
codes the item export uses. It is what turns `011` into `IVOCLAR VIVADENT`.

**Both endpoints b-s.si built are item pages. Neither exposes the manufacturer
master.** `allitems` can at best give the code per item; the code -> name mapping
lives in a separate BC table with no API page over it. So after a full OData switch we
would still be loading manufacturer names from a hand-exported spreadsheet.

That is not cosmetic. Without the name, `resolve.group` falls back to a bare BC code
as `canonical_manufacturer` - the exact failure the 2026-07-29 playbooks spec was
written to prevent - and playbooks, DISCOVER's `doc_sources` and the REF gate all key
on the canonical name.

**Ask for a third API page over BC's Manufacturer table (Code + Name).** Two fields
is the whole of it -- `VendorRow` is `(code, name)` and nothing else
(`app/vendor_master.py`). It is the same work Luka has already done twice, read-only,
and needs no ruling from anyone.

**Severity: convenience, not blocker.** `POST /import/vendors` (`web/app.py:3220`) is
live -- an operator uploads `Proizvajalci.xlsx` in the browser and gets the same
two-phase preview-then-apply the item import has. 390 rows that change rarely. So if
b-s.si decline, the item sync still automates fully and the manufacturer master stays
a hand-fed step. Worth asking for; not worth blocking on.


## 4. Scope: what is already promised, and what is new work

| Work | Status |
|---|---|
| Reading items from BC over OData instead of the Excel export | **Already promised.** The CSV import was always the first version, with the OData switch to follow as a configuration change |
| A read API for a possible later BC integration | **Already promised, and delivered** |
| **Writing back into BC** — the two booleans and the warehouse URL | **New work.** A read API built *for* a possible later integration is not the integration |
| The `/item/{item_ref}?k=` page BC's link points at | **New work.** Specified 2026-08-25, not built; it exists only to serve the BC link |

Ground truth decision 9 (`dentalia-workflow-structured-v2.md:20`) is the intent side
of the same line: "**No writeback to BC except possibly file references.** Both
integration directions must remain architecturally open — expose a read API from day
one, **wire nothing yet**." The email is Dentalia asking to wire it.

Note that the write half is not just "call PATCH". Writing a boolean means deciding
what makes it true, which is a policy question about the completeness rules in
`app/compliance.py`, and it means a new job type, which per invariant 7 is a PRD
change plus a migration, never a string.

**What the write half actually involves**, in dependency order:

| Piece | Depends on |
|---|---|
| `/item/{item_ref}` route + HTML/JSON page + "download all" | spec is written and approved-in-part; access policy already built |
| Populate `pteWarehouseURL` on every item | needs the route above and a write path |
| Write path itself: new job type, PRD row, migration, handler, ETag conditional PATCH, retry/backoff, audit | auth and external access resolved; a non-production company to test against |
| Decide and implement what makes each boolean true, incl. the tri-state question | **a Dentalia ruling** — nothing can be sized until it exists |

The OData read switch is separate and much smaller: correcting the profile, adding
the missing-column guard, paging, delta filter and live fetch.

## 5. Open questions

### To b-s.si (Luka)

1. **Auth.** On-prem BC on 7048: Entra ID / OAuth2 client credentials, or
   NavUserPassword plus a web service access key? We need a dedicated service account
   either way.
2. **TLS and external access.** HTTPS, not `http://…:7048`. An FQDN plus a firewall
   allowlist for our server, or a VPN. Credentials over plain HTTP across the public
   internet is not acceptable, and neither is a client certificate we cannot pin.
3. **`$metadata` or a full sample payload** for both endpoints, so §3 stops being a
   transcription.
4. ~~Which property carries the manufacturer code~~ — **answered by the payload:
   `manufacturerCode`.** Nothing to ask.
5. **A third API page over the Manufacturer table (Code + Name)**, so `vendor_master`
   stops depending on a hand-exported `Proizvajalci.xlsx`. Read-only, 390 rows, and
   the same work as the two pages already built. **This is now the only thing
   blocking a complete sync.**
5. **`vendorItemNo` vs `pteVendorItemNo`** — which is authoritative, and is
   `vendorItemNo` knowingly holding `pteOldNo` values on ~7,180 rows?
6. **Paging and `$select`.** Does `allitems` return `@odata.nextLink`, what is the
   page size, and is `$select` supported? Measured on the real payload, a record is
   **6.172 bytes** across its 242 properties, so a full sweep is **112 MB today and
   589 MB at ~100k items**. `$select` of the ten fields we actually use drops that to
   **5,6 MB / 29 MB**, a 20x reduction. This is the cheapest question on the list and
   the one with the largest effect on their server.
7. **Delta.** Can we `$filter` on `lastDateTimeModified`, and is it maintained on
   every write path (including the three `pte*` fields, which would otherwise make our
   own writes look like item changes)?
8. **Writes.** PATCH with `If-Match` on `dataitems`, confirmed? Rate limit? Does a
   write to `pteWarehouseURL` touch `lastDateTimeModified`?
9. **A non-production company.** `proddentalia` is production. We will not develop
   writes against it.
10. **Scope of `dataitems`** — all 19k items, or a filtered subset?

### To Dentalia (Nataša, Robert)

11. Who sees the three fields, on which BC page, and what should a rep conclude from
    them? Specifically the `false` vs "not yet checked" question in §2.
12. Confirm `pteWarehouseURL` is meant to be one link to a per-item page listing all
    that item's documents, not a link to a single file.
13. Write cadence: on every registry change, or a nightly sweep?
14. Is the intent that BC becomes the display surface, or the system of record? Ground
    truth decision 9 says the warehouse is the system of record for compliance and BC
    only mirrors. Confirm that still holds.

## 6. Before any code

In order, and none of it is optional:

1. External HTTPS access plus credentials exist, and we can `GET allitems?$top=1`
   from our server.
2. ~~Full field list captured from `$metadata`~~ — **done**, the 242-property payload
   is in [`samples/`](samples/) and §3 is measured against it.
3. `LJ_ODATA_PROFILE` rewritten to the real property names (known — see §3.1), and the
   missing-key guard added to `BcApiAdapter` so a wrong key fails loudly. §3.1.1 shows
   what happens without it: three records in, three all-null rows out, no error.
4. A read-only full sweep compared row-for-row against
   `imports/Artikli 3.7.2026.xlsx`: same row count, same `item_ref` set, same
   manufacturer distribution. The byte-identity contract between the two adapters is
   the thing that makes the config switch safe, and it is testable before anything is
   written back.
5. Only then, and only once the write half is agreed, the write path.
