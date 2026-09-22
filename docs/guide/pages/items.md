# Items

**In one sentence:** this is where you look up one product and see what compliance paperwork it has.

**Status:** Live. Checked against the code on 2026-09-15.

---

## Can I break anything here?

Mostly no.

- Nothing on this page deletes a document, and nothing changes just by looking.
- **Re-discover documents** only asks the system to search for paperwork again. It
  does not add, remove, or approve anything by itself. The browser asks you to
  confirm before it starts.
- **Covers this item** and **Does not** decide whether one document you can already
  see counts for this one product. They do not touch the document itself, or any
  other product it might cover.
- **Re-open** puts a document you rejected back in front of you for another look.
  Nothing is lost either way.
- **Update Business Central** sends this one product's three compliance fields
  back to Business Central, if they differ from what it already holds. The
  browser asks you to confirm before it queues anything. It never changes
  anything here, and while the writeback is switched off — which is the
  normal setting — it works out what would be sent and sends nothing.
- Every decision is saved with your name and the time.
- If a button gives you an error, nothing happened. Try again, or ask a developer.

---

## What this is

A list of every product in your catalogue, next to how much compliance paperwork
is held for it: how many documents count, how many are still waiting for a
decision, and how many have been replaced by a newer one.

Open one product and you see everything: its manufacturer, its supplier's own
article number, what it is required to carry as a distributor, and every document
linked to it — including documents a customer or Business Central cannot see yet,
because nobody has confirmed them.

Two different things can be "waiting" on one row: the document itself, and this
document's connection to this particular product. A document can already count
while its connection to one specific product still needs a person. That is what
the **Document** and **Link** badges next to each row are for — they can disagree.

---

## When you use it

- You need to check what paperwork covers one product — for example, before
  answering a customer or an auditor.
- You want an overview of which products still have gaps.
- A search from [Documents](documents.md) or [Manufacturers](manufacturers.md) led
  you here.

---

## Before you start

Nothing. Anyone who can log in can search and open a product's page. You only need
its article number, name, or manufacturer if you want to search for it — leave the
search box empty to see every product.

---

## What you do

1. Click **Items** in the menu on the left.
2. Under the heading is a row reading **Show: All items · Without a declaration ·
   Never searched**. The second opens [Coverage gaps](coverage.md), the articles
   with no declaration on file; the third opens [Discovery](discovery.md), the
   product families nobody has searched for yet. Both were menu entries until
   14 September 2026.
3. Type an article number, product name, or manufacturer name into the search box,
   and press **Filter**. Press **Clear** to start over.
4. Click a product's article number to open its page.
5. Read the compliance table near the top: what this product must carry as a
   distributor, and what is actually held for it. See
   [what you actually have to hold](../glossary.md#what-you-actually-have-to-hold)
   for what each row means.
6. Read **Linked documents** below it for the documents themselves, whatever stage
   each one is in.
7. Read **Have we searched for this?** below that. It tells you whether anyone has
   ever gone looking for this product's paperwork, which is a different question
   from whether we hold any. An empty document list under *Never searched* means
   nobody has looked yet — not that nothing exists.
8. Click a document's row to open its own page, **file** to see the actual PDF, or
   **text** to see exactly what the system read from it.
9. If you see **Covers this item** and **Does not** next to a row, and you are
   confident which is right, press one. These only appear once the document
   itself already counts — if the document is still waiting for a decision, deal
   with that first, on [Review](review.md).
10. If something looks wrong, out of date, or missing, press
   **Re-discover documents** to have the system search again.
11. If Business Central is showing the wrong compliance answer for this product,
    press **Update Business Central**. See [Business Central](bc-push.md) for what
    the three fields mean and when each one is true.

---

## What happens then

- **Re-discover documents** starts a fresh search in the background. A short
  message confirms it, and nothing else on the page changes right away — check
  back later.
- **Update Business Central** works the same way: it queues the update and
  confirms. The confirmation says which of the two is happening: *the worker
  sends it*, or *sending is switched off, so nothing will reach Business
  Central*. Pressing it twice on the same day does nothing the second time. If
  the product has never been through the system, nothing is sent at all — an
  answer we do not have must not reach Business Central as a "no".
- **Covers this item**, **Does not**, and **Re-open** are saved right away, with
  your name and the time attached. The badges on the page update shortly after;
  refresh if you do not see the change immediately.
- Nothing here removes a document. Pressing **Does not** only says this
  particular document does not cover this particular product — the document
  itself, and every other product it might cover, is untouched.

---

## What can go wrong

| You see | It means | What to do | Good or bad |
|---|---|---|---|
| *Re-discovery queued at interactive priority.* | The system will search for new documents for this product shortly | Nothing — check back later | Good |
| *Already queued today.* | You, or someone else, already asked today | Nothing — wait for it to finish | Good |
| *This item has no group yet — nothing was queued.* | The system has not worked out which product family this one belongs to, so it cannot search | Tell a developer | Bad |
| A short technical confirmation after pressing **Covers this item** / **Does not** / **Re-open**, with a reference number | Your decision was saved and is being recorded | Nothing — this is normal, even though the wording is technical | Good |
| *No documents linked to this item yet.* | Nobody has found paperwork for this product yet | Read **Have we searched for this?** below it before concluding anything | Depends — bad if the product needs a declaration and has none |
| **Never searched** | Nobody has ever gone looking for this product's paperwork. Most documents we hold arrived as a bulk delivery from the supplier, not from a search | Press **Re-discover documents** if this product matters | Not itself bad — but an empty document list here proves nothing |
| **Searched, found nothing** | We did go looking, and came back empty | Ask the supplier directly. Automatic search has done what it can | Bad if the product needs a declaration |
| **Searched, nothing found, sent to Missing documents** | We looked everywhere we know of, found nothing, and opened a task so a person can take it from here | Open [Missing documents](missing.md) — it is already on that list | Bad if the product needs a declaration, but the next step is already recorded |
| **Searched, found something** | A search found at least one document | Nothing — the documents are in the list above | Good |

---

## Words the screen uses

| The screen says | It means |
|---|---|
| **Item no.** | Dentalia's own number for the product, the one printed on the article |
| **Medical device** | Whether Business Central has marked this product as a medical device. *not stated* means Business Central left the field empty |
| **Published / Waiting for review / Replaced** | How many of this product's documents count, how many are still waiting for a decision, and how many have been replaced by a newer one |
| **Covers this item** | Whether this particular document counts for this particular product. A document can be published and still be waiting on this product's own link |
| The small coloured label next to a document (e.g. *Published*, *Waiting for review*) | See [document status](../glossary.md#document-status) |
| **Manufacturer's article no.** | The supplier's own article number for this product. See [REF, or product reference](../glossary.md#ref-or-product-reference) |
| **Where we looked** | Which places the search tried — *The manufacturer's known pages*, *An address we had before*, *The web*, *EUDAMED* |
| **Found something / Nothing found / Not tried** | Whether that place returned anything, returned nothing, or was never reached |
| **Technical details** | The stored values behind the screen: the catalogue tag, the Business Central manufacturer code, and how each document was matched to this product. See [how a document is matched](../glossary.md#words-for-how-a-document-is-matched). Safe to ignore day to day |

---

## Related

- [Documents](documents.md) — every document on its own, not grouped by product
- [Manufacturers](manufacturers.md) — one supplier's whole catalogue at once
- [Expiry](expiry.md) — what is lapsing across every product
- [Review](review.md) — where a document itself gets approved or rejected
- [Glossary](../glossary.md) — every word in one place
