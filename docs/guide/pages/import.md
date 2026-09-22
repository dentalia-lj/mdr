# Import from Business Central

**In one sentence:** this is where you bring in Business Central's product
list and supplier list, and nothing is written until you say so.

## Status

Live. Checked against the code on 2026-09-15.

## Can I break anything here?

No. This screen is built so you cannot write anything by accident.

- Neither form on this screen writes a single row until you press **Apply**
  on its preview. Choosing a file and pressing **Upload and preview** only
  shows you what would happen.
- Products or supplier codes missing from a file are never deleted or
  retired. A file trimmed to just the rows that changed is safe to upload.
- A supplier rename needs a second, deliberate tick before it is allowed —
  see **What happens then** below. Without that tick, nothing at all is
  written, not even the new codes.
- If a preview or an Apply fails, nothing is written. Your file stays
  uploaded, so you can try again.
- Applying after someone else has already imported the same file is safe —
  the screen tells you the counts moved, rather than leaving you guessing.

## What this is

This is where you bring in the two files Business Central exports: your
product list (**Artikli**) and your supplier list (**Proizvajalci**). Each
shows you exactly what will change before anything is written.

## When you use it

Whenever your Business Central product list or supplier list has changed,
and you want the system's copy brought up to date. You do not need the whole
file — an export trimmed to just the changed rows works too.

## Before you start

- You need the export file saved on your computer: `.xlsx`, `.xlsm`, `.xls`
  or `.csv`, under 25 MB. That is today's default limit; a developer can
  raise it.
- Know which of the two you are uploading. The product list and the supplier
  list are two separate forms on this one screen, each with its own
  **Upload and preview** button.
- If you are not sure why a supplier code's name changed, check with
  whoever manages your Business Central data before ticking the rename box.
- There is a second, similarly named screen, **Ingest**, that does something
  close but writes immediately, with no preview. For a file on your own
  computer, **Import** — this screen — is the one to use.

## What you do

1. Click **Import from Business Central** in the menu on the left.
2. Under **Item catalogue**, choose your product export and click its
   **Upload and preview**. Or, under **Manufacturer master**, choose your
   supplier export and click that form's own **Upload and preview**.
3. Wait. The preview is headed with your file's name and says "Reading the
   export…" (or "Reading the manufacturer master…") until it is ready. It
   checks itself every couple of seconds, so you do not need to reload the
   page.
4. Read the counts. See **What happens then** below for what each one means.
5. If a box offers to re-point some codes, read it carefully before ticking
   it — see **What happens then**.
6. Click **Apply**. Its exact wording shows how many rows will be written.

## What happens then

The product list preview shows these counts:

| You see | It means |
|---|---|
| Rows in file | How many product rows the file contained |
| New or changed | Rows the system will add or update once you press Apply |
| Unchanged | Rows exactly the same as what the system already holds |
| Not a medical device | Rows marked as not a medical device, left out of the medical-device work |
| Device class missing | Rows where the file did not say how risky the product is. Still imported, just flagged |
| No manufacturer's article no. | Rows with no supplier product number. Still imported, just flagged: matching documents to these later is harder |
| Skipped | Rows too incomplete to save at all (no product number, or no name) |

The supplier list preview shows different counts:

| You see | It means |
|---|---|
| Rows in master | How many supplier rows the file contained |
| New codes | Supplier codes appearing for the first time |
| Renamed | Codes whose name has changed since last time |
| Gone from the file | Codes that used to be in your supplier list but are not in this file. Kept exactly as they are — never deleted |
| Unchanged | Codes the file agrees with the system on already |

**About renames.** A "renamed" code is a supplier code whose name in the file
differs from the name the system already holds. Ticking the box to allow it
does not move your existing products onto the new name — they stay linked to
the old one. That can split one supplier into two in your records, with no
automatic way to undo it. Only tick the box once you know which products are
affected.

Press **Apply** and the counts are written for real. A second column then
shows what was actually written. Any drift between the preview and the write
is visible rather than hidden.

After a supplier import that added or renamed codes, the screen may show a
short paragraph about running a command. That text is for a developer, not
for you — mention it to whoever maintains the system rather than acting on
it yourself.

There is also a **Recent runs** list at the bottom of this page. It shows
any recent piece of work across the whole system, not just your imports: each
row is a date, what it was ("Item catalogue import", "Manufacturer master
import", or "Background task" for everything else) and how it ended. Open a
row, then its **Technical details**, for the raw report.

## What can go wrong

### Normal — carry on

| You see | It means | What to do | Good or bad |
|---|---|---|---|
| *only BC export files are accepted (.xlsx, .xlsm, .xls, .csv) — got '…'* | The file is not one of the accepted export types | Pick the correct export from Business Central | Bad — nothing uploaded |
| *file exceeds the 25 MB limit* | Your file is bigger than the system currently allows | Ask a developer, or trim the file to just the changed rows | Bad — nothing uploaded |
| "Reading the export…" / "Reading the manufacturer master…" | Normal — the system is reading your file | Wait a moment; it refreshes itself | OK |
| A table of counts and an **Apply** button | Normal — this is the preview | Read the counts, then press Apply if they look right | OK, nothing written yet |
| "Applied. N item(s) written…" / "Applied. N new code(s)…" | Success | Nothing — the numbers are your receipt | Good |

### Look closer before continuing

| You see | It means | What to do | Good or bad |
|---|---|---|---|
| A box: *Re-point N code(s) to a different manufacturer…* | Some supplier codes changed name since the last import | Check which codes, and whether any of your products already use the old name, before ticking | Look closer |
| *N count(s) moved since the preview* | Your catalogue changed between the preview and pressing Apply — likely someone else imported meanwhile | Check the new numbers; re-open the preview if unsure | Look closer |

### Refused or failed — nothing written

| You see | It means | What to do | Good or bad |
|---|---|---|---|
| *The preview failed.* / *The import failed.* with a technical message | Something went wrong reading the file | Nothing was written. Check the file, or ask a developer | Bad, but safe |
| *Refused — nothing was written.* | The file contained a supplier rename and the tick box was not ticked | Go back to **Import**, reopen the preview, tick the box only if you are sure, then Apply again | Bad, but safe |
| *Already in progress.* | You, or someone else, already started this exact upload and it is still running | Wait for it to finish; do not resubmit | OK |

## Words the screen uses

| The screen says | It means |
|---|---|
| "Technical details" on an upload form | The catalogue code this import writes, and the queue priority. Neither is a question for you: there is one Business Central and one numbering, and an import from this screen is always something somebody is waiting on |
| **interactive** / **delta** / **sweep** (Queue priority) | How soon the system gets to this, compared with other waiting work. **interactive** goes first, and is what this screen sends unless a developer changes it |
| "abandoned upload(s) collected" | Old previews nobody applied, cleared away automatically — nothing to do with your own file |
| dedupe key | A receipt number for this exact request |

## Related

- [Ingest](ingest.md) — the other way in, without a preview; mostly for developers
- [Manufacturers](manufacturers.md) — where supplier names live day to day
- [Items](items.md) — your product list
- [Glossary](../glossary.md) — every word in one place
