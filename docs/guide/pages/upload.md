# Upload

**In one sentence:** this is where you hand the system one document by hand.

## Status

Live. Checked against the code on 2026-09-15.

## Can I break anything here?

No.

- Nothing here deletes a file. Once you upload a document, it stays on file —
  whatever the system decides about it afterwards.
- Uploading the exact same file twice does not create a second copy.
- Your document does not count towards anything by itself. Either the system
  is confident and it counts right away, or it waits for a person on
  **Review** — the same rule as any document the system finds on its own.
- The one thing worth care: when you come from a **Missing documents** card,
  the page is set to the item on that card. A document for a different item
  uploaded from there is suggested for the wrong item. Nothing is deleted
  even then: rejecting it on **Review** fixes it.
- If the button gives you an error, nothing was saved. Try again, or ask a
  developer.

## What this is

This is where you hand the system one document — a declaration, a
certificate, or an instruction leaflet — that you found yourself, rather than
the system finding it on its own. One PDF at a time.

## When you use it

You have a document, from a supplier or an email or anywhere else, and you
want it read and matched to your products. Or you clicked **Upload what I
found** on a **Missing documents** card (or **Upload document** on the
**Manual** screen), because the system searched for one itself and found
nothing.

## Before you start

- You need the file saved on your computer or device.
- It must be a PDF. The system accepts a file only when its name ends in
  `.pdf`.
- It must be under 25 MB. That is today's default limit; a developer can
  raise it. If a file is refused, the message on screen states the actual
  limit in force.
- You do not need to know which item the document covers. The system works
  that out by reading the item numbers printed in the PDF.
- When you come from a **Missing documents** card, the page already knows the
  item: two hidden fields carry the item and the card. You never fill them in
  yourself.

## What you do

1. Click **Upload a document** in the menu on the left, or **Upload what I found** on a
   **Missing documents** card.
2. If you came from a card, the line under the heading names the item, for
   example "Uploading a document for FUJI PLUS CAPSULES (GC, item 003234)".
   Check that it is the item your document is for.
3. Under **PDF file**, choose the document from your computer.
4. Click **Upload**.

## What happens then

The page does not reload. A line appears below the button: *Document received.
The system reads it and it appears in Review within a few minutes, or on the
item straight away if everything on it is clear.* That is your receipt.

- Your file is saved permanently, whatever happens next.
- If this exact file was uploaded before, and the page did not name an item,
  nothing more happens: the system already has it.
- Otherwise the system reads it and tries to match it to your products, the
  same as anything it finds by itself. If it is confident, the document
  counts straight away. If not, it waits for you on **Review**.
- If you came from a **Missing documents** card, or a **Manual** request, it
  leaves that list as soon as the system has taken the file in.
- This screen does not tell you which of these happened. Check **Review** or
  **Documents** afterwards if you want to know.

## What can go wrong

| You see | It means | What to do | Good or bad |
|---|---|---|---|
| *only PDF uploads are accepted* | The file you picked is not a PDF, or its name does not end in `.pdf` | Save or export the document as a PDF, then try again | Bad — nothing was saved |
| *that file is named .pdf but its contents are not a PDF* | The file ends in `.pdf` but is not one inside. Almost always a web page saved from a browser: the page was saved, not the document it links to | Open the page again, right-click the document's own link and choose **Save link as**, then upload that file | Bad, and worth catching: before September 2026 a file like this was filed as a real declaration |
| *file exceeds the 25 MB limit* | Your file is bigger than the system currently allows | Ask a developer to raise the limit, or send a smaller file | Bad — nothing was saved |
| *Document received…* and then nothing more | Normal. The system works on it in the background | Check **Review** or **Documents** later if you want to know what happened | OK |
| An error message you do not recognise | Something failed on the system's side | Nothing was saved. Try again, or ask a developer | Bad, but safe |

## Words the screen uses

| The screen says | It means |
|---|---|
| "A PDF you already have: the system reads it, and it appears in Review or on the item itself" | The line under the heading, saying what this screen does |
| "Uploading a document for …" | The item this upload is for, named from the **Missing documents** card you came from |
| "Technical details" | Opens the job number behind your receipt. Only useful if you need to ask a developer about it |
| "Already in progress." | This exact upload is already being read. Your press changed nothing |

## Related

- [Review](review.md) — where an uploaded document usually needs your decision next
- [Missing documents](missing.md): where the **Upload what I found** button is
- [Manual](manual.md) — where the **Upload document** link comes from
- [Documents](documents.md) — where an approved upload appears
- [Glossary](../glossary.md) — every word in one place
