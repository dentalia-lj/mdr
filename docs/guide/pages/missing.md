# Missing documents

**In one sentence:** items the system searched for without finding a
document, so that you can find one and upload it.

**Status:** Live. Checked against the code on 2026-09-14.

---

## Can I break anything here?

No.

- Nothing on this screen deletes anything.
- **Upload what I found** only opens the Upload page. Nothing happens until
  you upload a file there.
- **Search again** asks the system to look for this item's document once
  more. It does not touch any document you already hold. Your browser asks
  you to confirm first.
- Pressing **Search again** twice does no harm. A search that is already
  waiting is not added a second time.
- The buttons marked ↗ open another website in a new tab. They change
  nothing here.
- If a button gives you an error, nothing happened. Try again, or ask a
  developer.

---

## What this is

A list of items the system searched for and found no document for: no
declaration, certificate or instructions it could use. Each card is one
missing document, for one item or for a set of items with the same name. The
oldest card is at the top.

Each card says where the system looked before it gave up, but only where its
log shows it really looked:

| The card says | Where the system looked |
|---|---|
| **the manufacturer's known pages** | The manufacturer's download pages a developer has set the system up to read, when it read at least one of them |
| **the web** | A web search |

The system also checks its own files, earlier addresses and EUDAMED where it
can, but its log does not show whether there was anything there to check, so
the card does not name them. When neither place above was searched, the card
says only "Nothing found" and the date.

## When you use it

Open **Missing documents** as the second step of your daily round, after
Review.

## Before you start

Nothing. Knowing the manufacturer only helps if you want to see one
manufacturer's items.

## What you do

1. Open **Missing documents**. Its address ends in `/missing`.
2. To see one manufacturer's items, click its name in the row of names at
   the top. The six with the most missing documents come first, and
   **+ N more** opens the rest. **All** shows everything again.
3. Read a card: the item's name, the manufacturer, the item numbers, and
   when the system last searched.
4. Look for the document yourself. The **downloads ↗** button, named after
   the manufacturer (for example **KAVO DENTAL downloads ↗**), opens the
   manufacturer's own download page, when the system knows one. **Search the
   web ↗** opens a web search already filled in with the manufacturer and the
   item's name. Both open in a new tab.
5. If you find the document, save the PDF to your computer, click **Upload
   what I found**, and upload it. The Upload page already knows which item
   it is for.
6. If you cannot find it, but think the manufacturer may have published it
   since the date on the card, click **Search again** and confirm.
7. The list shows 20 cards at a time. Use **Next** and **Previous** at the
   bottom.

## What happens then

- After an upload, the card leaves this list as soon as the system has taken
  the file in. The system then reads it and matches it to the item. If it is
  confident, the document counts straight away. If not, it waits for you on
  [Review](review.md).
- After **Search again**, a line appears under the card: "The system will
  search again for this item." If the search finds something to try, the card
  leaves this list at once, before anyone knows whether it is the right
  document. What it found then goes through the usual checks, so it may turn
  up on Review. If it is not the document, the item does not come back to
  this list straight away: open it under [Items](items.md) and press
  **Re-discover documents**. If the search finds nothing, the card stays and
  shows the new date.
- Nothing on this screen asks the manufacturer by email. That is not built
  yet.

## What can go wrong

| You see | It means | What to do | Good or bad |
|---|---|---|---|
| "No items are waiting for a document." | Nothing the system searched for is missing right now | Nothing to do | Good |
| "No missing documents for …" | The manufacturer you picked has none right now | Click **Show all** | Fine |
| A card with no **downloads ↗** button | The system does not know the manufacturer's download page | Use **Search the web ↗**, or go to the manufacturer's website yourself | Normal |
| "Already in progress." after **Search again** | A search for this item is already waiting or running | Nothing. Check back later | Normal |
| "Task #… is already closed." after **Search again** | Since you opened the page, someone uploaded a document for it, or a search found something to try | Reload the page | Fine |
| A card that says only "Nothing found on …" | The system tried on that date, but its log names no place it searched, for example because web search is not set up | Look for the document yourself. If every card says this, tell a developer | Needs you |
| The same card, days after **Search again** | The system looked again and still found nothing | Find the document yourself, or ask the manufacturer for it | Needs you |
| You uploaded the wrong file from a card | The card has left the list anyway | Reject the document on [Review](review.md). To have the item searched for again, open it under [Items](items.md) and press **Re-discover documents**. If that finds nothing, the item comes back here | Fixable |
| An error you do not recognise | Something failed on the system's side, and nothing happened | Try again, or ask a developer | Try again |

## Words the screen uses

| The screen says | It means |
|---|---|
| **waiting since** | The date the system first gave up on this item |
| **Searched … on 3 Sep 2026. Nothing found.** | Where the system looked (see the table above) and when it last looked |
| **Nothing found on 3 Sep 2026.** | The system tried on that date, but can show no place it looked |
| **item 12345** / **items …** / **5 items: …, +2 more** | The item numbers the missing document is for, as in Business Central. Past three, the first three are shown and the rest are counted |
| **#455** | The card's own number. Useful only if you need to ask a developer about it |
| **+ N more** | The other manufacturers with missing documents, in alphabetical order |

## Related

- [Upload](upload.md): where **Upload what I found** takes you
- [Review](review.md): where an uploaded or found document may wait for you next
- [Manual](manual.md): the operators' full list, which also holds these cards
- [Your daily round](../01-daily-work.md): where Missing documents fits in your morning
- [Glossary](../glossary.md): every word in one place
