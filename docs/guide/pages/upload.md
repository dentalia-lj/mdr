# Upload

**In one sentence:** this is where you hand the system documents by hand, one or several at once.

## Status

Live. Checked against the code on 2026-11-09.

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
  uploaded from there is suggested for the wrong item, and every file you
  add on that page goes to that item. Nothing is deleted even then: rejecting
  it on **Review** fixes it.
- If a file's line shows an error, that file was not saved. The other files
  in the list are not affected.

## What this is

This is where you hand the system documents — declarations, certificates, or
instruction leaflets — that you found yourself, rather than the system finding
them on its own. You can add several PDFs at once; each one is read and
handled separately.

## When you use it

You have a document, from a supplier or an email or anywhere else, and you
want it read and matched to your products. Or you clicked **Upload what I
found** on a **Missing documents** card (or **Upload document** on the
**Manual** screen), because the system searched for one itself and found
nothing.

## Before you start

- You need the files saved on your computer or device.
- Each one must be a PDF. The system accepts a file only when its name ends in
  `.pdf`.
- Each one must be under 25 MB. That is today's default limit; a developer can
  raise it. If a file is refused, the message on screen states the actual
  limit in force.
- You can add up to 50 files at a time.
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
3. Drag your PDF files onto the page, or click **Choose files** and select
   one or several. Nothing is sent yet: the files appear in a list.
4. If a file should not be there, click **Remove** on its line.
5. Click **Upload N files**. The files go one after another; stay on the page
   until the list above the button is empty or shows only problems.

## What happens then

The page does not reload. Each file that arrives moves down to **Recent
uploads**, which says what became of it and updates by itself while a file is
still being read. It is still there if you leave and come back. It shows the
last 100 uploads, from everyone.

| Recent uploads says | It means | What you can do |
|---|---|---|
| Being read | The system is working on it. Usually a minute or so, longer if it is busy | Wait, or come back later |
| Waiting for review | A person has to look at it | **Open in Review** |
| Published | It counts | **Open document** |
| On file | Read correctly, but it covers nothing you sell | **Open document** |
| Replaced | An older version of a document you already have | **Open document** |
| Rejected | Someone rejected this file earlier | **Open document** |
| Nothing filed: Not a compliance document | It is not a declaration, certificate or instruction leaflet | Check you picked the right file |
| Nothing filed: Could not read enough | The system could not get the facts it needs from it | Try a clearer copy, or ask a developer |
| Nothing filed: Read before, no document came of it | This exact file came earlier and produced no document | Check you picked the right file |
| Failed | Something broke on the system's side | Ask a developer |
| No result | A day has passed with no answer | Ask a developer |

- Your file is saved permanently, whatever happens next.
- If this exact file was uploaded before, and the page did not name an item,
  nothing more happens: the system already has it.
- Otherwise the system reads it and tries to match it to your products, the
  same as anything it finds by itself. If it is confident, the document
  counts straight away. If not, it waits for you on **Review**.
- If you came from a **Missing documents** card, or a **Manual** request, it
  leaves that list once a document has come out of one of your files. A wrong
  file leaves the card where it is. If the document is rejected on **Review**,
  the card comes back.

## What can go wrong

| You see | It means | What to do | Good or bad |
|---|---|---|---|
| *only PDF uploads are accepted* | The file you picked is not a PDF, or its name does not end in `.pdf` | Save or export the document as a PDF, then try again | Bad — nothing was saved |
| *that file is named .pdf but its contents are not a PDF* | The file ends in `.pdf` but is not one inside. Almost always a web page saved from a browser: the page was saved, not the document it links to | Open the page again, right-click the document's own link and choose **Save link as**, then upload that file | Bad, and worth catching: before September 2026 a file like this was filed as a real declaration |
| *file exceeds the 25 MB limit* | Your file is bigger than the system currently allows | Ask a developer to raise the limit, or send a smaller file | Bad — nothing was saved |
| *Failed to send* with a **Try again** button | The file did not reach the system, usually a dropped connection | Click **Try again** on that line | Bad — nothing was saved for this file, the others are fine |
| *This is a folder* | You dropped a folder instead of files | Open the folder and drop the PDF files inside it | Bad — nothing was sent |
| *already added, not added again* | You added the same file twice | Nothing to do | OK |
| An error message you do not recognise | Something failed on the system's side | Nothing was saved. Try again, or ask a developer | Bad, but safe |

## Words the screen uses

| The screen says | It means |
|---|---|
| "A PDF you already have: the system reads it, and Recent uploads below says what came of it" | The line under the heading, saying what this screen does |
| "Uploading a document for …" | The item this upload is for, named from the **Missing documents** card you came from |
| "Every file below goes to this item" | You came from a **Missing documents** card: all files you add here are suggested for that one item |
| "Waiting", "Uploading..." | The file is in the list and has not been sent yet, or is being sent now |
| "Technical details" | Opens the job number behind your receipt. Only useful if you need to ask a developer about it |
| "Already in progress." | This exact upload is already being read. Your press changed nothing |

## Related

- [Review](review.md) — where an uploaded document usually needs your decision next
- [Missing documents](missing.md): where the **Upload what I found** button is
- [Manual](manual.md) — where the **Upload document** link comes from
- [Documents](documents.md) — where an approved upload appears
- [Glossary](../glossary.md) — every word in one place
