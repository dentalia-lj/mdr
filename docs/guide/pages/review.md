# Review

**In one sentence:** this is where you decide whether a supplier document counts.

**Status:** Live. Checked against the code on 2026-09-11.

---

## Can I break anything here?

No.

- Nothing on this screen deletes a document. Ever. Every file the system has
  fetched stays on file, whatever you decide.
- **Approve** makes a document count. **Reject** means it does not count. That is
  the whole difference.
- You can only decide with a document open. A closed row has one button,
  **Open**, and nothing else.
- An approval that reaches a supplier's whole range asks you once first, and
  tells you how many items it will reach.
- Changed your mind? Open the document under **Documents** and decide again.
- Every decision is saved with your name and the time. A rejection also saves
  the reason you chose.

If a button gives you an error, nothing happened. The document is exactly as it
was. Try again, or ask a developer.

---

## What this screen is for

The system looks for supplier paperwork on its own — declarations of conformity,
certificates, instructions for use. Most of what it finds, it files by itself.

When it is not sure, it stops and puts the document here. It will not add anything
to your records until a person agrees.

So: an empty Review screen means the system is confident about everything it has
found. A full one means it needs you.

---

## The kinds of row

| What you see | What it is asking you |
|---|---|
| A **document** with an **Open** button, under its manufacturer's name | "I found this. Should it count?" |
| A **grouping suggestion** | "I think these two products are the same family. Am I right?" |
| **Links waiting for review on published documents**: an item and a document, with **Covers this item** and **Does not** | "This published document was matched to this item only weakly. Does it cover it?" |
| **Links waiting on documents that were never published** | Nothing. This list is for your information only. There is no button and nothing to decide |

Most days you will only touch the first kind.

---

## How to decide, in three questions

Open the PDF and ask yourself:

1. **Is this the right kind of document?** A declaration of conformity, a
   certificate, an instruction leaflet — and does it come from the supplier it
   claims to?
2. **Is it current?** Look at the dates. If you already hold a newer one for the
   same products, this one should not replace it.
3. **Does it cover the right products?** Either it names product numbers you
   recognise, or it says it covers the supplier's whole range.

Three yeses: approve. Any no: reject, or ask the supplier.

---

## Step by step

1. Click **Review** in the menu on the left.
2. The list is grouped by manufacturer. Each group starts with a line such as
   *GC 12 documents*, and the groups with the oldest waiting document come
   first. Documents nobody could put a manufacturer to come last, under
   **Manufacturer not known**. Inside a group, the oldest document is at the top.
   The number beside the title, *N waiting in all*, counts everything waiting,
   whatever you have filtered. When a reason button or a search is in force, a
   line above the list says how many of those it left, and the group counts and
   the page counter under the list count only those.
3. Each row names the document first, for example *GC · Declaration of
   Conformity (MDR)*. Under it: the file name, the date it was issued and the
   date it started waiting, then the grey sentence that says in one line why the
   system stopped. An expired document also says *expired* and the date. If some
   of the items linked to the document belong to a different supplier than the
   group it sits in, the row says so: *+1 other manufacturer among its items*.
4. Press **Open** on the row (or click anywhere on it). The row opens in two
   halves. On the left is page 1 of the PDF itself; **Open full size ↗** opens
   the whole document in a new tab. On the right is what the system read from
   it: **Manufacturer** (as printed on the document), **Rules** (MDR or MDD),
   **Issued**, **Valid until** and the **Basic UDI-DI**. A date the document
   does not give reads *Not stated*.
5. Above the buttons, the panel says what approving will do: *Approving makes it
   count for these N items*, followed by each item's number and name (the first
   ten; press **+ K more** for the rest) and how they were matched. For a
   document that covers a supplier's whole range it reads *…for all N
   ⟨supplier⟩ items*, or *…for the one ⟨supplier⟩ item* where the supplier has
   a single device item.
6. Compare the PDF with the facts beside it: the type of document, the dates,
   the items.
7. Press one button:

| Button | What it does |
|---|---|
| **Approve for these N items** (**Approve for this item** when there is one) | The document counts from now on, for exactly the items listed above the button. It appears under Documents, and each of those items now shows it |
| **Approve** | The same, where the approval reaches no item yet. The panel says why: nothing links the document to an item, or it is linked only by a weak match (see below) |
| **Reject…** | Asks why first. Pick one of the six reasons below, add a note if it helps, then press **Reject**. The document does not count. It stays on file, marked rejected, and can be reopened. Your name and the reason are recorded, and the reason shows on [Decisions](decisions.md). The system will keep looking for a better one. **Cancel** closes the reasons and changes nothing |
| **Correct a fact first** | Opens the fields for the document type, the rules, the dates and the certificate number. Change only what is wrong on the document; your corrections are saved when you approve. It is not offered on a whole-range document, where a correction could not be saved |
| **Approve for all N items** | Only appears when the document covers a supplier's whole range instead of named products. Choose the supplier from the list first, then press it. The list offers **every** supplier in the catalogue. Pressing it does not approve yet: the screen asks *Approve for all N ⟨supplier⟩ items?* and says what that means. Press **Yes, approve for N items** to go ahead, or **Cancel** to change nothing. If N is 0, it means every product we hold from that supplier has a blank device class in Business Central — approving still records the supplier, and the products attach themselves once someone fills that class in |

8. The counter at the top of the screen (*N waiting in all*) goes down by one.
   Move to the next row.

### When the panel says the items will not count yet

Some items are linked to a document only by a weak match: where the file was
found, a similar product name, or an article number with no manufacturer
confirmed. Approving the document does not make it count for those items. The
panel lists them apart and says *Approving publishes it, but it will not count
for any item yet* (or *It is also linked to N more items…* when some items do
count). After you approve, each of those items waits under **Links waiting for review on
published documents**, further down the Review screen: press **Covers this
item** or **Does not** for each one.

### Showing one kind of reason at a time

The buttons above the list show the documents waiting for one kind of reason.
**All reasons** shows everything again.

| Button | Shows the documents where |
|---|---|
| **Which items is unclear** | The system could not settle which of your items the document covers. This includes the documents whose grey sentence begins *Read without problems* |
| **Manufacturer unclear** | The system could not confirm whose document it is |
| **Covers a whole range** | The document covers everything a supplier makes |
| **Expired** | The *Valid until* date has passed |
| **Other** | Anything else: the kind of document, its dates, a certificate we do not hold |

A document with two messages can appear under two buttons. **Expired** is a
date and not a message, so it crosses all the others: an expired document is
under **Expired** as well as under the button its message belongs to, a
whole-range document included. A search in the box above keeps the button you
chose, and the button keeps your search.

### The six reasons for a rejection

| Reason | Use it when |
|---|---|
| **Wrong manufacturer** | The document comes from a different company than the row says |
| **Not one of our items** | It is a genuine document, but for products we do not stock |
| **Not a compliance document** | It is a brochure, a price list, a safety data sheet or anything else that is not a declaration, a certificate or instructions |
| **Out of date** | A newer version of it exists, or is already on file |
| **Duplicate of another document** | The same document is already on file |
| **Other** | None of the above. Say what in the note |

The note is optional and can be up to 500 characters long. Whatever you write
is saved next to the reason, for example *Out of date: newer 2025 version exists*.

---

## The messages you will see, and what to do

The grey sentence on each row is the system telling you why it stopped. There are
thirteen of them. They fall into three groups.

### Usually fine — read the PDF, then approve

| The screen says | What that means | What to do |
|---|---|---|
| *This replaces an older document already on file.* | The system has already worked out that this is the newer one | Approve. This is the system doing its job |
| *The document does not say which items it covers.* | Nothing on the page names a product number | Very common for range-wide declarations. Use **Approve for all N items** and pick the supplier |
| *This document names a certificate we do not have on file yet.* | It refers to a certificate we are not holding | Approve the document if it is sound. Chase the missing certificate separately |
| *This does not look like a medical device document, so it was held back from automatic filing.* | No MDR or MDD citation, and not a quality certificate — it may be a machinery, cosmetics or electrical declaration | Open the PDF. If it really is about a device, approve it; if not, file it so it stops appearing here |
| *The article numbers point at more than one group of items.* | One document covers products we keep in separate families | Approve if it genuinely covers them all |

### Look at the PDF carefully first

| The screen says | What that means | What to do |
|---|---|---|
| *We could not confirm which manufacturer issued this.* | The name on the document matches nothing in Business Central | Normal for a new supplier. Choose the right one from the list — it holds every supplier we know, so type into the box above it to narrow it — then approve. If the supplier is not in the list at all, add it under **Manufacturers** first |
| *Matched to your catalogue by article number alone.* | The product number matched, but the supplier was never confirmed | A product number on its own is not enough. Confirm the supplier before approving |
| *The expiry date is not written as an expiry anywhere on the page…* | A date was read as an expiry date, but the page never labels it one | Check the PDF. It may be a signing or issue date |
| *We cannot tell whether this is newer or older than the document on file.* | One of the two documents has no usable date | Compare the two PDFs yourself and decide |
| *This carries the same date as the document already on file for these items, so nothing says which one is current.* | Two documents for the same products carry the same issue date, usually two revisions signed on one day | Open both PDFs. Keep the later revision and reject the other; if both are genuinely needed, tell a developer |
| *The article numbers in the document do not match this group's items.* | The product numbers on the page belong to a different family | Do not approve without checking. It may have been filed against the wrong products |
| *The document lists article numbers, but we could not tie them to your catalogue.* | Product numbers were found, none of them ours | Check whether these are products you actually stock |
| *The article numbers belong to more than one manufacturer.* | The product numbers on the page span two suppliers | Unusual. Read the PDF before deciding |

### Usually reject

| The screen says | What that means | What to do |
|---|---|---|
| *This is older than the document already on file for these items.* | Its date is earlier than the one you already hold | Reject with the reason **Out of date**, unless you know the one on file is wrong. If this keeps happening, the source we are pulling from is stale — tell a developer |
| *The dates on this document do not look right.* | A date is impossible, or wildly out of range | Usually a badly scanned page. Do not approve without reading it |

---

## Words the screen uses

Some parts of this screen still show the words the system uses internally. Every
other screen says the word in the third column, and the
[glossary](../glossary.md#words-on-the-screen) carries both.

| The screen says | It means | Elsewhere |
|---|---|---|
| **Production** | Counts. Visible everywhere | **Published** |
| **Staged** | Waiting for a person. Does not count yet | **Waiting for review** |
| **Rejected** | Decided against. Kept on file, does not count | **Rejected** |
| **Superseded** | Replaced by a newer document. Kept, no longer current | **Replaced** |
| **Filed** | A genuine document from a known supplier that covers none of the items you stock | **On file** |
| **Match basis** (e.g. *name-family*) | How the system matched a document to an item. Some ways of matching are trusted enough to publish on their own; others always need a person | **How it was matched** |
| **A link** | The connection between one document and one item. A document can have many | |
| **A group** | Items we treat as one family, because one document usually covers all of them | |
| **C5**, **C17** and similar codes | Internal rule numbers. Ignore them | |

---

## What happens after you approve

- The document appears under **Documents** and counts towards your coverage figures.
- Every item listed above the button you pressed now shows it. Items linked
  only by a weak match wait under **Links waiting for review on published documents** for
  their own **Covers this item** or **Does not**.
- If it replaces an older one, the older one is marked *replaced by a newer one*.
  It is kept, not deleted — you can still see it, and an auditor can still be shown it.
- Every fact the system read off the page — the type, the dates, the certificate
  number, the product numbers — is stored together with the page it was read from.
  If an auditor asks where a date came from, you can show them.

---

## Where to go next

- [Missing documents](missing.md) — the second queue in your daily round: the items the
  system searched for and found nothing for
- [Failed](failed.md) — the third
- [Manual](manual.md) — the same work as an operator sees it
- [Documents](documents.md) — everything you have approved
- [Manufacturers](manufacturers.md) — add a supplier before binding a document to it
- [Expiry](expiry.md) — what happens as a document nears its end date
- [Glossary](../glossary.md) — every word in one place
