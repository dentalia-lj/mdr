# Compliance Warehouse — status

**Dentalia d.o.o. · 25 August 2026 · status report**

Working copy. The Slovene version is the one to send. All figures read from the live system on 25 August 2026.

---

## 1. Built and running

- Registry: 4,265 devices, 769 documents, 8,563 article-document links.
- Archive: 769 files. Each stored once. Duplicates are not stored.
- Deletion is not possible. 10-year retention.
- Read API. Business Central reads directly.
- 194 of 194 published documents carry evidence. No exceptions.
- 16,221 jobs finished. None lost.
- Catalogue: Ljubljana. One Business Central, one article numbering.

## 2. What replaced the folders

| Folder | System |
|---|---|
| Folder per supplier | Page per supplier: all documents, all articles |
| Subfolder per type | Filter by type: declaration, certificate, instructions, ISO |
| Date in the file name | Date read from the document, system watches it |
| Opening a PDF | One click, original file |
| Searching by hand | One search box: articles, documents, certificates, suppliers |
| Knowing what is missing | Table on every page (section 3) |

No PDF is lost.

## 3. Completeness check

The system knows what each article requires, from its device class.

| Required | For |
|---|---|
| Declaration of conformity | Every device |
| Instructions for use | Every device, narrow exemption for lower classes |
| Certifying body named | Above the simplest class |

Shown, never marked red: EC certificate, ISO 13485. Dentalia is a distributor, not a manufacturer.

Colours: green = held and current. Amber = look. Red = missing or expired.

Location: every supplier page, every article page.

## 4. Cost and automation

| | Offer | Today |
|---|---|---|
| Share without AI | rises with recipes | **62.2%** (3,822 of 6,144 values) |
| First full sweep | 250–850 € | **$35.76 total** |
| Steady state | 10–40 €/month | below the lower bound |

## 5. Data

| | |
|---|---|
| Articles from Business Central | 15,958 |
| Medical devices | 4,265 |
| Documents | 769 |
| Published | 194 |
| Awaiting review | 128 |
| Proven values | 6,144 |
| Article-document links | 8,563 |

**Coverage — two figures:**

- 99.7% (4,253) of devices hold some document.
- 34.2% (1,460) hold a document naming that article.

The difference: a certificate issued to the manufacturer applies to all its products. It speaks about the manufacturer, not the article.

**Declarations of conformity:**

| Class | Articles | Current | Expired | Replaced | Awaiting | None |
|---|---:|---:|---:|---:|---:|---:|
| Ir | 2,569 | 0 | 0 | 0 | 0 | **2,569** |
| IIa | 1,594 | 444 | 941 | 76 | 48 | 85 |
| I | 69 | 44 | 0 | 0 | 0 | 25 |
| IIb | 33 | 0 | **31** | 0 | 0 | 2 |
| **Total** | **4,265** | **488** | **972** | **76** | **48** | **2,681** |

Instructions for use: **0**, in every class.

## 6. Findings

- 2,569 reusable instruments (Ir) hold no declaration of their own. One ISO 13485 certificate covers them, valid to 2028. It speaks about the manufacturer.
- The same articles hold no reprocessing instructions. For reusable instruments these are mandatory.
- 972 declarations have expired. They are not lost. A current version is needed from the supplier.
- 31 of 33 class IIb articles hold expired declarations. Highest class, smallest group.
- No instructions for use anywhere in the registry.
- 128 documents and 103 items await approval.

## 7. EUDAMED

- Measured 20 August 2026: 47.7% of our Basic UDI-DIs resolve in EUDAMED.
- EUDAMED has no bulk export and returns no document URLs.
- The API is unofficial and undocumented. It works, but they can change it without notice.
- A button is built: look up a Basic UDI-DI from the document page, click by click.
- It returns the devices in the group and their catalogue numbers.
- There is no automatic mirror of the whole database. No bulk export exists.
- Offline Basic UDI-DI check-character validation is built. No network. 306 of 306 known-good codes accepted. 3 of our 352 rejected, all three genuinely corrupt.

## 8. Not built

- Automatic searching of manufacturer sites is built but has delivered no documents yet.
- Document sources today: 862 from the archive, 17 from e-mail, 1 from the web.

## 9. Open on your side

| Item | Status |
|---|---|
| Person for the review queue | Nataša |
| Supplier contacts | On the FTP. Server needs restarting |
| Device classes | Missing for some articles. Where the class is absent the system does not score the row |
| Internal policy | If stricter than the law (e.g. always keeping the EC certificate), it becomes a red row |

## Notes

- The e-mail agent sends nothing. It prepares a draft. A person sends it.
