# Data quality

**In one sentence:** three separate boards — one lists the manufacturer names
printed on waiting documents that the system does not know yet, one lists odd
things in your product list, and one compares Business Central's device class
against what your documents actually say.

**Status:** Live. Checked against the running screen on 2026-08-31; the first
board added on 2026-09-29.

---

## What this is

This screen is read-only. It writes nothing, here or anywhere else — Business
Central always stays the record of truth.

The first board, **Printed names without a manufacturer**, lists documents
waiting on Review because the manufacturer name they print is not one the
system knows. A supplier often prints its full legal name (for example
"botiss biomaterials GmbH") where Business Central has a short label
("BOTISS"). Until that name is added to the supplier's playbook, the system
cannot tell whose document it is, so it cannot check the article numbers
either. The board groups the documents by the name they print and suggests
the manufacturer, with the evidence for it.

The second board is a running list of things the system could not make sense of
while reading your product list out of Business Central. Most of it is a
question for whoever manages that export, not for you.

The third board is different, and matters more to you: it puts Business
Central's device class for a product beside the class your held documents
actually state, so the two can be checked against each other.

---

## When you use it

- Weekly, when the weekly report says names are waiting: the first board is
  where you find which ones and why.
- Monthly, or when a developer or your Business Central contact asks you to
  look at something here.
- When you want to fill in a product's device class using paperwork you
  already hold, instead of guessing.
- When you want to check whether a product's recorded class actually matches
  its declaration.

---

## Before you start

Nothing. Being logged in is enough.

---

## What you do

1. Click **Data quality** in the **Operator** block at the bottom of the menu.
2. On **Printed names without a manufacturer**, read each row: the name, how
   many documents wait on it (click the count to list them), the suggested
   manufacturer and **What to do**. Pass the name and the suggestion to your
   developer, who adds it to the playbook. Nothing on this board changes
   anything itself.
3. On the **By kind** board, click a kind to filter the list below it to just
   that kind.
4. On the device-class board, read the **To look at** table. Each row is one
   product whose Business Central class needs a look.
5. Open the product or the document from the row, decide what is right, and
   pass the correction to whoever maintains Business Central. This screen
   cannot make the change itself.

---

## What happens then

### Board 1 — printed names without a manufacturer

Once your developer adds the name to the playbook and re-checks the waiting
documents, each one is matched again. A document whose article numbers match
your products is published when the reading is confident, and its Review task
closes; if something else about it is doubtful, it stays on Review. One whose
article numbers are all products you do not stock is filed, when the reading
is otherwise sound; one that lists no article numbers at all stays on Review. A
document that stays on Review may still show its old reason there. The row
leaves this board once the document is matched. Future documents printing
that name are matched without anyone doing anything.

### Board 2 — oddities in your product list

Every row is something the system read from Business Central and could not
interpret. A repeated oddity bumps a counter rather than being listed again,
so this list stays the size of the problem, not the size of your catalogue.

### Board 3 — device class vs Business Central

Every row compares one product's Business Central class against what a
document you hold for that product actually states. Nothing here changes
Business Central. It only tells you where the two agree, where one is blank,
and where they genuinely disagree.

---

## What can go wrong

### Board 1 — what each suggestion means

| You see | It means | What to do |
|---|---|---|
| **Alias exists** | The name is already known; these documents were checked before it was. If the manufacturer has no products in the catalogue, the row says so and there is nothing to re-check | Ask your developer to re-check them (one command) |
| **Article numbers** | At least 3 of the documents' article numbers belong only to one manufacturer's products, and they are at least 80 % of those found in the catalogue. If most of what the documents list is not in the catalogue, the row warns you | Pass the name and the suggested playbook to your developer. Heed the warning: a distributor lists many manufacturers' articles |
| **Name only** | The name contains a Business Central brand, but the article numbers do not confirm it. A brand can also be a distributor | Open one document and check the manufacturer before passing it on |
| **No match** | Neither the article numbers nor the name point to one manufacturer | Look at the documents. It may be a distributor, whose name must never be added as a manufacturer's |

### Board 2 — what each kind means

The **Kind** column shows one of these internal names. This is the one place
on this page where the raw name is unavoidable — there is no pill or plain
label standing in for it.

| You see | It means | What to do | Whose problem |
|---|---|---|---|
| `md_class_blank` | Business Central has no risk class recorded for this product | Worth flagging if you notice a pattern | Business Central's own data |
| `mfr_ref_missing` | The supplier's own article number is not on file for this product | Nothing to chase — this is descriptive, not a target | Informational |
| `mfr_ref_prose` | The field that should hold the supplier's article number has a sentence in it instead | Worth flagging to whoever enters Business Central data | Business Central's own data |
| `manufacturer_code_blank` | Business Central has no supplier code for this product | Worth flagging | Business Central's own data |
| `reclassified_non_md` | The system once tracked this product as a device, and Business Central has since said it is not | Informational, unless it changes what you expect to see on Status | Informational |
| `cert_reference_unresolved` | A declaration mentions a certificate the system does not hold | Chase the certificate from the supplier if you need it | Yours to chase |
| `no_text_layer` | A document is an image with no text the system can pull out | Ask the supplier for a proper file if this keeps happening for them | A developer's, unless it is one supplier repeatedly |
| `basic_udi_check_failed` | A device-identifier code on a document failed its own internal check | A developer's | A developer's |
| `t0_ref_template_miss` | The system's reading rules for one supplier's paperwork stopped working, because the supplier changed how their page looks | A developer's — nothing for you to fix here | A developer's |

### Board 3 — what each verdict means

| You see | It means | What to do | Good or bad |
|---|---|---|---|
| **agree** | Business Central's class and the document's class match | Nothing. Counted, not listed | Good |
| **agree-family** | Business Central recorded a more specific class — sterile, measuring, or reusable-surgical — and the document just says the plain, general class. Declarations almost never spell out that detail, so this is not a real disagreement | Nothing. Counted, not listed | Good |
| **fillable** | Business Central has no class recorded, and a document you hold states one | Pass the class on to whoever maintains Business Central, quoting the document it came from | An opportunity, not a problem |
| **conflict** | Business Central's class and the document's class genuinely disagree | Open the document and check it is filed against the right product. If it is, this is worth raising — either Business Central's record or the document needs a second look | Worth investigating |

Only **fillable** and **conflict** rows are listed by product, because those two
are the ones with something to do. **agree** and **agree-family** are counted
in the summary above the list and nothing more.

---

## Words the screen uses

| The screen says | It means |
|---|---|
| **Printed name** | The manufacturer name exactly as the document prints it |
| **Waiting** | How many documents on Review print this name |
| **Kind** | Which of the nine known oddities a row is |
| **Subjects** / **Observations** | How many distinct products or documents hit this oddity, and how many times it has been seen in total |
| `data_anomaly`, `item_class_check`, migration numbers | Internal table and file names, visible if you open the small arrow next to the page's summary line. Ignore them |
| **BC class** / **Document states** | Business Central's recorded class, and the class your held document states |
| **Verdict** | One of `agree`, `agree-family`, `fillable`, `conflict` — see the table above |

---

## Related

- [System status](status.md) — **missing mfr_ref** also appears there as a headline tile
- [Items](items.md) — open a specific product to see its full record
- [Documents](documents.md) — open a specific document to see what it states
- [Glossary](../glossary.md#device-class) — device class, and why class I splits
  into four
