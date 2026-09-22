# Documents

**In one sentence:** every document the system has found or been given, in one
place, whatever stage it is in.

**Status:** Live. Checked against the code on 2026-09-15.

---

## Can I break anything here?

Almost nothing — with one exception, and it is reversible.

- Nothing here deletes a document, approves it, or publishes it. Approving
  happens on [Review](review.md).
- **The exception: Reopen this document.** It appears only on a document
  somebody has already rejected, and it puts that document back into
  [Review](review.md) as waiting. It does **not** approve it and it does not
  bring back the products it was linked to — those stay rejected until each is
  reopened on its own. If you reopen one by mistake, reject it again on Review.
  You are asked for your name because undoing somebody else's decision is
  recorded.
- **Look up in EUDAMED** only asks a European database what it knows about this
  document's device. It does not change anything about the document itself.
- If a button gives you an error, nothing happened. Try again, or ask a
  developer.

---

## What this is

Every document the system holds — counted and published, waiting for a decision,
rejected, replaced by a newer one, or genuinely from a supplier but covering
nothing you stock. Nothing is ever removed from this list, even a document
someone rejected.

Open one document and you see: what it claims to be, which products it covers,
where the file came from, every fact the system read off it and the page it read
it from, and — if it has lapsed or is about to — whether anyone is already
chasing the supplier for a replacement.

---

## When you use it

- You want to check one specific document — its dates, what it covers, or where
  it came from.
- An auditor asks where a fact came from. Every value here carries the exact page
  it was read from.
- You are browsing by manufacturer, document type, or status rather than by
  product.

---

## Before you start

Nothing. You only need a document number, manufacturer name, certificate number,
or part of a filename if you want to search for one — leave the search box empty
to browse everything.

---

## What you do

1. Click **Documents** in the menu on the left.
2. Narrow the list with **Type** or **Status**, or type a manufacturer name,
   filename, certificate number, UDI, or document number into **Search**, then
   press **Filter**. Press **Clear** to start over.
3. Click a document's number to open its page.
4. Read the identity table: what it is, which regulation it falls under, its
   dates, and where it was fetched from.
5. Click **archived file** to open the actual PDF, or **stored text** to see
   exactly what the system read out of it.
6. Look at **Covered items** for which products it applies to, and open
   **Technical details** under *Where every value came from* for every fact the
   system extracted, with the exact page and wording it came from.
7. If the document is still waiting for a decision, click
   **review this document** to go straight to it on [Review](review.md).
8. If the document names a Basic UDI-DI and you want a quick cross-check, press
   **Look up in EUDAMED**.

---

## What happens then

- **Look up in EUDAMED** runs in the background. A message confirms it was
  queued, or that it was already checked today. Its result appears as a new
  table on this same page once it is done — refresh to see it.
- Everything else on this page is for reading only. Nothing you click here
  changes a document's status.

---

## What can go wrong

| You see | It means | What to do | Good or bad |
|---|---|---|---|
| *EUDAMED lookup queued.* | The system will check this document's device against EUDAMED shortly | Nothing — check back later | Good |
| *Already looked up today.* | This document was already checked today | Nothing — wait for it to finish | Good |
| *This document carries no Basic UDI-DI — nothing to look up.* | The document does not name a code EUDAMED can be searched by | Nothing to do — this is common and not an error | Neutral |
| A document's manufacturer shows as plain text, not a link | The name printed on the document could not be matched to exactly one of your suppliers | Nothing to do here — this is informational | Neutral |
| A document shows *— (none extracted)* for manufacturer | The system could not read a manufacturer name off the page at all | Open the PDF and check it is legible | Neutral, unless it should be a real document |

---

## Words the screen uses

| The screen says | It means |
|---|---|
| The small coloured label next to a document (*Published*, *Waiting for review*, *On file*, *Replaced*, *Rejected*) | See [document status](../glossary.md#document-status) |
| **Type**, shown as *Declaration of Conformity*, *EC certificate*, *Instructions for use*, *ISO certificate* | What kind of document it is. The short codes the system stores are not shown |
| **Reopen this document** | Put a rejected document back into Review. The only way to undo a rejection |
| **Coverage scope**, shown as *group* | This document covers one named family of products |
| **Coverage scope**, shown as *manufacturer* | This document covers everything you stock from that supplier |
| **Cited certificate** | The notified-body certificate this declaration refers to, if any |
| A date marked *(review)* or *(inherited)* | Not a real expiry printed on the document — see [the 5-year rule](../glossary.md#the-5-year-rule) |
| **Where every value came from** | The evidence behind each fact, folded under **Technical details** along with the file's fingerprint and how each product link was made. Open it when an auditor asks; ignore it day to day |

---

## Related

- [Items](items.md) — the same documents, grouped by product instead
- [Review](review.md) — where a waiting document gets approved or rejected
- [Manufacturers](manufacturers.md) — a supplier's whole document history
- [Expiry](expiry.md) — which documents are lapsing or have lapsed
- [Glossary](../glossary.md) — every word in one place
