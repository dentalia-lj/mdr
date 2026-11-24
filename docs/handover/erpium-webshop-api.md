# Dentalia compliance documents: webstore API

For the webstore backend. Everything here is called **server to server**, with
the API key in a header. Nothing here may be called from a customer's browser,
and neither the key nor any URL we return may appear in a page.

Base URL: `https://api.cw.dentalia.si`. Every call carries:

```
X-API-Key: <the key Dentalia gave you>
```

## What you get

Only documents a clinic may be shown: published, current for the item, and not
past their expiry. You never need to filter.

### Manufacturer tree: `GET /api/manufacturers`

Every manufacturer that has at least one document to show. Load it once and
cache it as long as you like. `?q=` filters by name.

```json
{
  "manufacturers": [
    {"id": 41, "canonical_name": "IVOCLAR", "items": 1981, "documents": 87}
  ],
  "total": 58
}
```

Use `id` in your URLs. It stays the same when a manufacturer is renamed; the
name does not.

### One manufacturer: `GET /api/manufacturers/{id}`

`{"id", "canonical_name", "items", "documents"}`. `404` for an unknown id.

### Documents per manufacturer: `GET /api/manufacturers/{id}/documents`

Each document once, even when it covers hundreds of items (an ISO certificate
usually does).

```json
{"id": 41, "canonical_name": "IVOCLAR", "documents": [Document, ...]}
```

### Documents per item: `GET /api/items/{item_ref}/documents`

`item_ref` is Dentalia's article number, as in Business Central. It may contain
`/`; send it as a path, URL-encoding the other special characters.

```json
{
  "item_ref": "081-1",
  "item_name": "KLEŠČE EKSTR. ZG. INCISAL 1",
  "manufacturer": "CARL MARTIN",
  "mfr_ref": null,
  "documents": [Document, ...]
}
```

An item we do not know answers `200` with `item_name: null` and no documents.
An empty `documents` list means we hold no documentation for it yet.

### Several items at once: `GET /api/items/documents?ref=A&ref=B`

Up to 100 refs. The answer keeps your order and keeps unknown refs as
null entries, so you can draw one row per line.

```json
{"items": [ItemDocuments, ...], "total": 2}
```

### The document object

```json
{
  "doc_id": 19,
  "type": "EC",
  "regulation": "MDR",
  "valid_from": "2026-06-25",
  "valid_to": "2031-06-29",
  "name": "CARL MARTIN EC (MDR), valid to 2031-06-29",
  "url": "https://api.cw.dentalia.si/api/documents/19/link"
}
```

| Field | Values |
|---|---|
| `type` | `DoC` (declaration of conformity), `EC` (CE certificate), `IFU` (instructions for use), `ISO` (quality-system certificate), `SPP` (system or procedure pack statement), `other` |
| `regulation` | `MDR`, `MDD`, `n.a.` (ISO certificates and some instructions) |
| `valid_to` | The expiry the document or its certificate states. `null` when there is none, which is normal for a class I declaration of conformity |
| `name` | A display label in **English**. Build your own Slovenian label from `type`, `regulation` and `valid_to` |
| `url` | Not for a page. See downloads below |

## Downloads: on the customer's click

1. Your page links to **your own** URL, for example `/dokumentacija/19`.
2. When the customer clicks it, your server calls
   `GET /api/documents/19/link` with the key.
3. We answer `{"url": "https://api.cw.dentalia.si/d/19.1763....", "expires_at": "..."}`.
4. Your server answers the customer's browser with `302 Location: <url>`.
5. The browser downloads the PDF straight from us. It needs no key.

The link works for 5 minutes and opens that one document only. Ask for a new
one on every click; never store or cache it. A page left open all day still
works, because the link is made at the click.

## Limits

Normal use never reaches them.

- **1,200 calls a minute** for your key, all calls together. Past it: `429`.
- Per customer address on downloads: **30 different documents a minute and
  100 a day**. Opening the same document again does not count.

A `429` carries `Retry-After` (seconds).

## Errors

All errors are `{"detail": "..."}`.

| Status | Meaning |
|---|---|
| `400` | A parameter you may not use (`include_unlinked`, `include_superseded`, an empty batch, more than 100 refs) |
| `401` | No key, or a wrong one |
| `404` | Unknown manufacturer, a document that may not be shown (also on the link), or a path that is not one of the routes above. For an unknown path the body is plain text `Not found`, not JSON |
| `410` | The download link expired or was changed: ask for a new one |
| `429` | Over a limit, see `Retry-After` |
