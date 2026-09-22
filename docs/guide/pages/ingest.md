# Ingest

**In one sentence:** this is a second, more direct way to bring in your
product list — it writes immediately, with no preview.

## Status

Partly live. Checked against the code on 2026-08-31. Loading a file already
on the server works. The Business Central live connection option does not —
it is not built yet, and always fails safely.

## Can I break anything here?

Yes, a little — read this before you use it.

- Unlike **Import**, this screen does not show you a preview. Pressing the
  button writes to your product list straight away.
- Nothing is ever deleted here either. Products missing from a file are
  simply left alone.
- But there is no safety net catching a wrong file before it writes. Point it
  at an old or wrong file, and your product list is updated to match it
  immediately. It stays that way until someone imports the right file.
- If something fails partway through, the system changes nothing at all. It
  either finishes cleanly or writes nothing.
- Choosing the Business Central connection option currently always fails —
  safely, writing nothing. It simply does not work yet.
- Most readers should use **Import** instead. It does the same thing and
  shows you what will change before it changes.

## What this is

A second way to load your product list from Business Central, alongside
**Import**. The difference: this one writes to your records the moment you
press the button, with no preview first.

## When you use it

Rarely, if ever, for most readers. Use it only when a developer or IT has
told you exactly which file to point it at. For a spreadsheet on your own
computer, use **Import** instead — same effect, but it shows you a preview
first.

## Before you start

- This screen does not let you choose a file from your own computer. It only
  reads a file already sitting on the Dentalia server, or — once built — a
  live connection to Business Central.
- You need the exact file location from IT or a developer. Typing the wrong
  one makes the import fail, but writes nothing.
- Do not choose **bc_odata** as the source. It is not built yet and will
  always fail.
- Under the title, the screen prints a paragraph of technical text written
  for developers, not you. Part of it is out of date. Ignore it.

## What you do

1. Open `/ingest` directly in your browser's address bar. **It is deliberately
   not in the menu on the left** — [Import](import.md) is the one to reach for,
   because it takes a file from your own machine and shows you the diff before
   anything is written. Use this page only when a developer has told you to.
2. Under **Source**, choose **csv**. Leave **bc_odata** alone; it does not
   work yet.
3. Either pick a file from the list under "Pick a file under…", or type the
   exact path a developer gave you under "Or type a path manually".
4. Leave **Priority** on **interactive**, unless told otherwise.
5. Click the button labelled **Enqueue ingest.run** — its own internal name
   for "start this import".

## What happens then

The page does not reload. A line appears: *Import queued. The item list is read
and the new items start looking for their documents.*

- There is no preview here. The work happens in the background.
- Once it finishes, it appears in the **Recent runs** list at the bottom of
  this page — as a block of raw technical detail behind **show**, not a
  friendly summary.
- If you want to know what actually changed, the safest place to check
  afterwards is **Items**.
- The **Recent runs** list is not only about your import. It shows any
  recent piece of work the whole system did, so most rows will not concern
  you.

## What can go wrong

| You see | It means | What to do | Good or bad |
|---|---|---|---|
| *a CSV/Excel path is required (pick one or type a path)* | You did not choose or type a file location | Pick one from the list, or type the exact path IT gave you | Bad — nothing written |
| *company is required for a bc_odata ingest* | You chose the Business Central connection but left **Company** blank | Use **csv** with a file instead — the connection option does not work yet | Bad — nothing written |
| *delta_since '' is not a valid datetime* | **Delta since** was left blank, or not filled in through the date picker | Use **csv** with a file instead | Bad — nothing written |
| *Import queued…* and then nothing else | Normal — the work happens in the background | Check **Recent runs** below in a moment | OK |
| A **Recent runs** row for your attempt shows *failed*, mentioning the connection is not built | You chose the Business Central connection option | Use **csv** with a file instead, or ask a developer | Bad, but safe — nothing was written |
| A block of raw text under a run's result | The system's own technical report for that piece of work, not specific to yours | Usually ignore it. A developer can read it if something looks wrong | Neutral |

## Words the screen uses

| The screen says | It means |
|---|---|
| csv / bc_odata (Source) | Where the product list comes from: a file (**csv**), or a live Business Central connection (**bc_odata** — not built yet) |
| **interactive** / **delta** / **sweep** (Priority) | How soon the system gets to this, compared with other waiting work. **interactive** goes first |
| Enqueue ingest.run | The button's internal name. It means "start this import" |
| dedupe key | A receipt number for this exact request |

## Related

- [Import](import.md) — the safer way in, with a preview before anything is written
- [Items](items.md) — your product list, to check what actually changed
- [Glossary](../glossary.md) — every word in one place
