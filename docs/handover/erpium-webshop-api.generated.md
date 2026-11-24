---
title: "Dentalia compliance documents — webstore API"
subtitle: "For Erpium · version of 2026-11-24"
---

# What this is

Dentalia keeps a registry of the compliance documents for the products it
sells: declarations of conformity (DoC), CE certificates, instructions for use
(IFU) and ISO certificates. Every document in it has been read, checked, and
linked to the Dentalia articles (Business Central items) it covers.

This API gives the webstore three things:

1. **The manufacturer tree**: every manufacturer that has documents to show.
2. **A manufacturer's documents**: the ones covering products Dentalia sells,
   each with the products it covers, and separately the manufacturer's other
   documents.
3. **Documents per product, and search**: by Dentalia article number, the
   supplier's article number, a UDI, the product name or the manufacturer name.

Files are downloaded by the customer's browser straight from our server,
through a link your server asks for at the moment of the click.

Every example in this document is a real response, captured from production
data on 2026-11-24 with the code that will be deployed.

# Access

Base URL: `https://api.cw.dentalia.si`

Every call carries the key Dentalia gave you:

```
X-API-Key: <key>
```

**All calls are made by your server.** The key, and every URL this API
returns, must stay on your server and never appear in a page, a script, or a
browser request. The one thing a customer's browser ever receives from us is a
file, through the download flow described below.

All calls are `GET` and answer JSON (UTF-8), except the file download.

# What you can rely on

- You only receive documents a clinic may be shown: every one has been read
  and checked, and none is past its expiry. You do not need to filter anything.
- In `documents` and on product pages you get only the current version per
  product and document type. `other_documents` is kept as received and can hold
  more than one version of the same declaration (for example a 2008 and a 2026
  edition); use `valid_from` to order them.
- `doc_id`, the manufacturer `id` and `item_ref` are stable. The manufacturer
  `id` stays the same when a manufacturer is renamed; the name does not, so key
  your pages on `id`.
- Fields are only ever added, never removed or renamed. Ignore fields you do
  not know.

# 1. The manufacturer tree

`GET /api/manufacturers`

Every manufacturer with at least one document to show, largest catalogue
first. `?q=` filters by name (part of the name is enough).

```http
GET /api/manufacturers
X-API-Key: <key>
```

Response `200`:

```json
{
  "manufacturers": [
    {
      "id": 438,
      "canonical_name": "CARL MARTIN",
      "items": 2582,
      "documents": 2,
      "other_documents": 5
    },
    {
      "id": 383,
      "canonical_name": "IVOCLAR",
      "items": 1981,
      "documents": 81,
      "other_documents": 70
    },
    {
      "id": 460,
      "canonical_name": "CEFLA",
      "items": 1737,
      "documents": 0,
      "other_documents": 1
    }
  ],
  "total": 72
}
```

*Shortened for this document: `manufacturers`: first 3 of 72 shown.*

| Field | Meaning |
|---|---|
| `id` | Stable manufacturer id. Use it in your URLs. |
| `canonical_name` | Display name. |
| `items` | How many Dentalia articles this manufacturer has. |
| `documents` | Documents covering articles Dentalia sells. |
| `other_documents` | The manufacturer's other documents (see section 2). |

On 2026-11-24 the tree has 72 manufacturers. It changes slowly, so
cache it for hours.

`GET /api/manufacturers/{id}` returns one entry of the same shape:

```http
GET /api/manufacturers/438
X-API-Key: <key>
```

Response `200`:

```json
{
  "id": 438,
  "canonical_name": "CARL MARTIN",
  "items": 2582,
  "documents": 2,
  "other_documents": 5
}
```

# 2. A manufacturer's documents

`GET /api/manufacturers/{id}/documents`

Two lists:

- **`documents`**: documents covering articles Dentalia sells. Each says which
  articles (`items`), so you can link it from your product pages.
- **`other_documents`**: documents of this manufacturer that cover no article
  Dentalia sells. Examples are declarations for products outside the
  catalogue, and company-wide letters or certificates. Show them apart, for
  example under "Druga dokumentacija proizvajalca".

The example below is DENTAL WORLD: 15 documents and 4 other
documents, shortened to the first two of each list.

```http
GET /api/manufacturers/614/documents
X-API-Key: <key>
```

Response `200`:

```json
{
  "id": 614,
  "canonical_name": "DENTAL WORLD",
  "documents": [
    {
      "doc_id": 709,
      "type": "IFU",
      "regulation": "n.a.",
      "valid_from": "2023-06-01",
      "valid_to": null,
      "file_name": "Canal_Prep_IFU.pdf",
      "name": "DENTAL WORLD IFU",
      "url": "https://api.cw.dentalia.si/api/documents/709/link",
      "item_count": 1,
      "items": [
        {
          "item_ref": "0000177",
          "item_name": "CANAL PREP EDTA 3X6G"
        }
      ],
      "items_truncated": false
    },
    {
      "doc_id": 710,
      "type": "IFU",
      "regulation": "n.a.",
      "valid_from": "2024-03-01",
      "valid_to": null,
      "file_name": "Canal_Past_IFU.pdf",
      "name": "DENTAL WORLD IFU",
      "url": "https://api.cw.dentalia.si/api/documents/710/link",
      "item_count": 1,
      "items": [
        {
          "item_ref": "0000175",
          "item_name": "CANAL PASTE 2G"
        }
      ],
      "items_truncated": false
    }
  ],
  "other_documents": [
    {
      "doc_id": 721,
      "type": "DoC",
      "regulation": "MDR",
      "valid_from": "2026-01-12",
      "valid_to": null,
      "file_name": "ENG_Declaration_of_conformity_2026_FILL_ROOT_DISPOSABLE_NEEDLES.pdf",
      "name": "DENTAL WORLD S.R.L. DoC (MDR)",
      "url": "https://api.cw.dentalia.si/api/documents/721/link"
    },
    {
      "doc_id": 724,
      "type": "other",
      "regulation": "MDR",
      "valid_from": "2024-02-29",
      "valid_to": null,
      "file_name": "2024.02.29_Confirmation_Letter_EN_DENTAL_WORLD.pdf",
      "name": "DENTAL WORLD SRL other (MDR)",
      "url": "https://api.cw.dentalia.si/api/documents/724/link"
    }
  ]
}
```

*Shortened for this document: `documents`: first 2 of 15 shown; `other_documents`: first 2 of 4 shown.*

`items` lists at most 50 articles. When a document covers more (an ISO
certificate can cover thousands), `item_count` has the full number and
`items_truncated` is `true`. CARL MARTIN's two documents cover
2,581 and 1,336 articles.

The same `doc_id` can come back from several calls (a manufacturer page, a
product page, a search). Treat `doc_id` as the identity.

# 3. Documents per product

`GET /api/items/{item_ref}/documents`

`item_ref` is Dentalia's article number, the same as in Business Central.
URL-encode it.

```http
GET /api/items/0000177/documents
X-API-Key: <key>
```

Response `200`:

```json
{
  "item_ref": "0000177",
  "item_name": "CANAL PREP EDTA 3X6G",
  "manufacturer": "DENTAL WORLD",
  "mfr_ref": null,
  "documents": [
    {
      "doc_id": 709,
      "type": "IFU",
      "regulation": "n.a.",
      "valid_from": "2023-06-01",
      "valid_to": null,
      "file_name": "Canal_Prep_IFU.pdf",
      "name": "DENTAL WORLD IFU",
      "url": "https://api.cw.dentalia.si/api/documents/709/link"
    }
  ]
}
```

An article we do not know answers `200` with nulls and no documents, so a
missing article never breaks your page:

```http
GET /api/items/NO-SUCH-ITEM/documents
X-API-Key: <key>
```

Response `200`:

```json
{
  "item_ref": "NO-SUCH-ITEM",
  "item_name": null,
  "manufacturer": null,
  "mfr_ref": null,
  "documents": []
}
```

**Several articles in one call** (for a list or basket page, up to 100):
`GET /api/items/documents?ref=A&ref=B`. Entries come back in the order asked,
unknown ones as null entries:

```http
GET /api/items/documents?ref=0000177&ref=NO-SUCH-ITEM
X-API-Key: <key>
```

Response `200`:

```json
{
  "items": [
    {
      "item_ref": "0000177",
      "item_name": "CANAL PREP EDTA 3X6G",
      "manufacturer": "DENTAL WORLD",
      "mfr_ref": null,
      "documents": [
        {
          "doc_id": 709,
          "type": "IFU",
          "regulation": "n.a.",
          "valid_from": "2023-06-01",
          "valid_to": null,
          "file_name": "Canal_Prep_IFU.pdf",
          "name": "DENTAL WORLD IFU",
          "url": "https://api.cw.dentalia.si/api/documents/709/link"
        }
      ]
    },
    {
      "item_ref": "NO-SUCH-ITEM",
      "item_name": null,
      "manufacturer": null,
      "mfr_ref": null,
      "documents": []
    }
  ],
  "total": 2
}
```

# 4. Search

`GET /api/search?q=...`

One query box. `q` is 2 to 100 characters. It is matched, in this order,
against:

1. Dentalia's article number (exact, case ignored);
2. the supplier's article number (exact, case ignored);
3. a Basic UDI-DI printed on a document (spaces ignored). The articles covered
   by that document are returned;
4. the article name (approximate, best match first).

At most 20 articles come back, only articles that have documents, each with
`matched_on` saying which of `item_ref`, `mfr_ref`, `basic_udi_di` or `name`
found it, and each with its documents. Manufacturers whose name matches come
back separately under `manufacturers`, as tree entries. Their articles are not
listed (one manufacturer can have thousands).

By article number:

```http
GET /api/search?q=0000177
X-API-Key: <key>
```

Response `200`:

```json
{
  "query": "0000177",
  "manufacturers": [],
  "items": [
    {
      "item_ref": "0000177",
      "item_name": "CANAL PREP EDTA 3X6G",
      "manufacturer": "DENTAL WORLD",
      "mfr_ref": null,
      "documents": [
        {
          "doc_id": 709,
          "type": "IFU",
          "regulation": "n.a.",
          "valid_from": "2023-06-01",
          "valid_to": null,
          "file_name": "Canal_Prep_IFU.pdf",
          "name": "DENTAL WORLD IFU",
          "url": "https://api.cw.dentalia.si/api/documents/709/link"
        }
      ],
      "matched_on": "item_ref"
    }
  ]
}
```

By name ("creafill"), shortened to the first two of 8 articles:

```http
GET /api/search?q=creafill
X-API-Key: <key>
```

Response `200`:

```json
{
  "query": "creafill",
  "manufacturers": [],
  "items": [
    {
      "item_ref": "9000780",
      "item_name": "CREAFILL GEL ETCH 12G",
      "manufacturer": "DENTAL WORLD",
      "mfr_ref": "9000780",
      "documents": [
        {
          "doc_id": 713,
          "type": "IFU",
          "regulation": "n.a.",
          "valid_from": "2023-04-01",
          "valid_to": null,
          "file_name": "CreaFILL_Gel_Etch_Bio_IFU.pdf",
          "name": "DENTAL WORLD IFU",
          "url": "https://api.cw.dentalia.si/api/documents/713/link"
        }
      ],
      "matched_on": "name"
    },
    {
      "item_ref": "9000923",
      "item_name": "CREAFILL DESENSITISE 7ML",
      "manufacturer": "DENTAL WORLD",
      "mfr_ref": null,
      "documents": [
        {
          "doc_id": 711,
          "type": "IFU",
          "regulation": "n.a.",
          "valid_from": "2024-04-01",
          "valid_to": null,
          "file_name": "CreaFILL_Desensitise_IFU.pdf",
          "name": "DENTAL WORLD IFU",
          "url": "https://api.cw.dentalia.si/api/documents/711/link"
        }
      ],
      "matched_on": "name"
    }
  ]
}
```

*Shortened for this document: `items`: first 2 of 8 shown.*

By manufacturer name ("voco"). The approximate name match also returns
articles whose name contains the text, such as an article named "IVOCOLOR".
The example is shortened to the first two articles:

```http
GET /api/search?q=voco
X-API-Key: <key>
```

Response `200`:

```json
{
  "query": "voco",
  "manufacturers": [
    {
      "id": 417,
      "canonical_name": "VOCO",
      "items": 31,
      "documents": 25,
      "other_documents": 220
    }
  ],
  "items": [
    {
      "item_ref": "1061",
      "item_name": "GRANDIO SEAL NDT 2X2G VOCO",
      "manufacturer": "VOCO",
      "mfr_ref": "1061",
      "documents": [
        {
          "doc_id": 1686,
          "type": "DoC",
          "regulation": "MDR",
          "valid_from": "2026-01-14",
          "valid_to": "2031-01-21",
          "file_name": "VOCO_DoC_MDR_Grandio_Seal_2026-1-signed.pdf",
          "name": "VOCO DoC (MDR), valid to 2031-01-21",
          "url": "https://api.cw.dentalia.si/api/documents/1686/link"
        }
      ],
      "matched_on": "name"
    },
    {
      "item_ref": "9301",
      "item_name": "DISPENZER TWINKY STAR 9301 VOCO",
      "manufacturer": "VOCO",
      "mfr_ref": "9301",
      "documents": [
        {
          "doc_id": 1698,
          "type": "DoC",
          "regulation": "MDR",
          "valid_from": "2026-02-12",
          "valid_to": null,
          "file_name": "VOCO_DoC_MDR_Caps_Dispenser.pdf",
          "name": "VOCO GmbH DoC (MDR)",
          "url": "https://api.cw.dentalia.si/api/documents/1698/link"
        }
      ],
      "matched_on": "name"
    }
  ]
}
```

*Shortened for this document: `items`: first 2 of 20 shown.*

# 5. The document object

Every document, in every response above, looks like this:

| Field | Meaning |
|---|---|
| `doc_id` | Stable id of the document. |
| `type` | `DoC` declaration of conformity · `EC` CE certificate (notified body) · `IFU` instructions for use · `ISO` quality-system certificate · `SPP` system or procedure pack statement · `other` (e.g. a manufacturer's confirmation letter). |
| `regulation` | `MDR`, `MDD`, or `n.a.` (ISO certificates and most IFUs). |
| `valid_from` | Issue date, or null. |
| `valid_to` | The expiry the document or the certificate it cites states, or null when it states none. Null is common for declarations of conformity and IFUs, which often state no expiry. Never a date in the past. |
| `file_name` | The file name as we hold it. Characters other than letters, digits, `_`, `.` and `-` are replaced by `_`, so `CE - 6.6.2028.pdf` reads `CE_-_6.6.2028.pdf`. The file downloads under the same name. |
| `name` | A short English label built from the manufacturer as printed on the document, the type, the regulation and the expiry. It does **not** tell two documents of the same type apart (all of a manufacturer's IFUs can read the same), and the manufacturer part is as printed (e.g. "DENTAL WORLD S.R.L."). Use `file_name`, or build your own Slovenian label from `type`, `regulation` and `valid_to`. |
| `url` | **Not for a page.** Your server calls it when the customer clicks (section 6). |
| `item_count`, `items`, `items_truncated` | In `documents` on the manufacturer page only (section 2). |

# 6. Downloads

The customer's browser downloads straight from our server, but never sees the
key and never gets a link that keeps working:

1. Your page links to **your own** URL, for example `/dokumentacija/18`.
2. When the customer clicks it, your server calls the document's `url`, which
   is `GET /api/documents/{doc_id}/link`, with the key:

```http
GET /api/documents/18/link
X-API-Key: <key>
```

Response `200`:

```json
{
  "url": "https://api.cw.dentalia.si/d/18.1795515957.9JWvV3YGYpYeemH2ZGYw5yN0N-UrFsnFb4aCe9RuJtU",
  "expires_at": "2026-11-24T10:25:57+00:00"
}
```

3. Your server answers the browser with `302 Found` and `Location:` set to that
   `url`.
4. The browser downloads the file. It needs no key:

```http
GET /d/18.1795515957.9JWvV3YGYpYeemH2ZGYw5yN0N-UrFsnFb4aCe9RuJtU
```

Response `200`:

```http
Content-Type: application/pdf
Content-Disposition: inline; filename="CE_-_6.6.2028.pdf"
Content-Length: 453513
Cache-Control: private, max-age=31536000, immutable

<453513 bytes of PDF>
```

The link works for 5 minutes and opens that one document only. Ask for a new
one on every click and never store it. A page left open all day keeps working,
because the link is made at the click.

A changed or expired link answers:

```http
GET /d/18.1795515957.9JWvV3YGYpYeemH2ZGYw5yN0N-UrFsnFb4aCe9RuAAA
```

Response `410`:

```json
{
  "detail": "this download link has expired or is not valid; open the document again"
}
```

# 7. Errors

Every error is JSON with a `detail` text. The texts below are the real ones.

| Status | When | Example `detail` |
|---|---|---|
| `400` | A query or parameter that is not allowed | ``q` must be 2 to 100 characters` |
| `400` | | ``include_unlinked` is not available to API keys: it returns documents that are not published for any item.` |
| `401` | No key, or a wrong key | `valid X-API-Key required` |
| `404` | Unknown manufacturer | `unknown manufacturer` |
| `404` | A document that may not be shown, or unknown, when asking for a link | `document not available` |
| `410` | A download link that expired or was changed | `this download link has expired or is not valid; open the document again` |
| `429` | Over a limit (section 8). The `Retry-After` header gives seconds to wait. | `too many requests; retry after the number of seconds in Retry-After` |

A path that is not one of the calls above answers `404` with the plain text
`Not found`, not JSON.

# 8. Limits

Normal use never reaches them.

- **1,200 calls a minute** for your key, all calls together.
- **Downloads, per customer address: 30 different documents a minute and 100 a
  day.** Opening the same document again does not count.

Above a limit the answer is `429` with `Retry-After`.

# 9. Summary for your build

| Your page | Call |
|---|---|
| Manufacturer list | `/api/manufacturers` (cache it) |
| Manufacturer page | `/api/manufacturers/{id}/documents` → group `documents` by `type`; `other_documents` apart |
| Product page | `/api/items/{item_ref}/documents` (or the batch for lists) |
| Search box | `/api/search?q=` |
| Click on a document | your URL → your server calls `url` → `302` to the returned link |
