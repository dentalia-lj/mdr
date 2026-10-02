# Per-item currency, measured on the server before merge (2026-10-02)

Read-only, against the server's registry (91.98.42.140), which does not have
migration 071. The 071 pick was computed inline (`tools/per_item_currency_measure.sql`)
and compared with today's `item_document_production`. Nothing was written.

| Measure | Today | After 071 |
|---|---|---|
| Rows in `item_document_production` | 11.037 | 10.697 |
| Items with a current DoC | 1.914 | **2.056** |
| Item/type pairs with more than one current document | 426 | 11 (same-date ties) |
| `superseded` documents that answer for some item | 0 (never read) | 1 (doc 29) |
| `production` documents that answer for no item | 0 (status decides) | 28 |

**142 items regain a declaration** (1.914 to 2.056): doc 29's, retired on
2026-09-28 by doc 275, which names none of them. This is the intended
direction, not a measurement error. Docs 199 and 208 stay history: every one
of their items is covered by a newer declaration.

**426 to 11:** today an item answers with every published document of a type;
after, with the current one (or the documents tied on its date). The 426
include the 358 items holding both an MDR and an MDD certificate.

**The 28 production documents that stop answering** are history for every item
they name. They leave the expiry board, the weekly report and the renewal
letters:

| Type | Regulation | Documents | Production links |
|---|---|---|---|
| DoC | MDD | 12 | 16 |
| DoC | MDR | 8 | 33 |
| ISO | n.a. | 5 | 15 |
| EC | MDD | 1 | 358 |
| EC | MDR | 1 | 3 |
| IFU | MDD | 1 | 3 |

The single MDD certificate on 358 links is the MDR-over-MDD rule at work: those
items hold an MDR certificate too.

## Query cost

| Query | Today | 071 pick |
|---|---|---|
| One item (`77871`, 12 documents), `item_document_production` | 0,26 ms | 0,25 ms |
| Whole view, `count(*)` | 3,7 ms | 28 ms |

The item filter reaches the window (`WindowAgg` over the item's 12 rows), so
the item page and the API stay index lookups. Whole-registry readers
(`document_current`, the board) pay about 25 ms more per query.

## Open before merge

Who reads `/api/items/{item_ref}/documents` today: asked of Denis 2026-10-02;
the default answer shrinks for the 426 item/type pairs above.
