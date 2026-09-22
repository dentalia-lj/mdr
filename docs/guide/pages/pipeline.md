# Queues & health

**In one sentence:** one door into the five screens about running the machine,
showing how much each one holds so you do not have to open all five.

**Status:** Live. Checked against the code on 2026-09-11.

---

## What this is

Until 4 September 2026 these five screens each had their own line in the menu.
Together with Status, Import, Upload and Weekly reports that made the Pipeline
section ten entries long — most of a menu, for work a compliance officer rarely
does. The five moved behind this page.

**Nothing was taken away.** Every screen is unchanged and still has its own
address; this page is where you now find them, and it tells you how much each
one holds before you click.

None of the five is a compliance decision. They are what the system is doing,
what it could not make sense of, and what it gave up on.

---

## The five

| Screen | What it holds |
|---|---|
| [Processing](processing.md) | Documents moving through the system right now |
| [Data quality](data-quality.md) | What the Business Central export did that we could not interpret |
| [Manual](manual.md) | Jobs that stopped and want a person |
| [Failed](failed.md) | Jobs that ran out of retries with no newer attempt queued for them |
| [Scheduler](scheduler.md) | The ten recurring checks, when each last ran and runs next |
| [Business Central](bc-push.md) | The items whose compliance fields in Business Central no longer match what we hold, and the two ways to send them |

The **How many** column is a live count, and each number says what it counts:
**documents being read**, **notes on items and documents**, **open tasks**,
**failed tasks** or **recurring checks scheduled**. A dash means zero. It is
written as a dash rather than a zero so that a row with something in it stands
out.

The Data quality number is the large one, in the tens of thousands, and it is
not work waiting for anyone. It counts standing notes: one for each thing the
Business Central export or a document did that we could not interpret, such as
an item with no device class in Business Central or no supplier article
number, or a scanned document with no text. Until 11 September 2026 the column
was headed **Waiting**, which made that number read as a backlog.

---

## What to do about a row

Open the screen. This page starts nothing and changes nothing.

One thing worth knowing: a **Manual** item is *read* on the Manual screen, but
the decision is taken on [Review](review.md), which is the screen that carries
the document and the evidence behind it. Manual tells you something is stuck;
Review is where you unstick it.

---

## Where the rest of Pipeline went

Four screens that used to sit under Pipeline are not machine operation, and
none of them was buried:

- [Import](import.md) — bringing in a product list from Business Central. In
  the menu, under **Add**.
- [Upload](upload.md) — adding a document by hand. In the menu, under **Add**.
- [Weekly reports](reports.md) — the report you send on. Reached from
  [Today](today.md), which links the latest two weeks.
- [System status](status.md) — the overview board. In the **Operator** block at
  the bottom of the menu, with this screen.

---

## See also

- [System status](status.md) — the overview these counts also appear on
- [Review](review.md) — where decisions are actually taken
