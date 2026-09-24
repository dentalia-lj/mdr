# Scheduler

**In one sentence:** this screen shows the ten tasks the system runs on its
own schedule, whether each one has run recently, whether each is still switched
on, and lets you start three of them early.

**Status:** Live. Checked against the running screen on 2026-09-07.

---

## Can I break anything here?

No.

- **Run now** exists on three of the ten rows. Pressing it starts that
  task early — it does not skip, cancel, or duplicate anything. If the same
  task is about to run on its own schedule anyway, the two collapse into one
  run rather than doing the work twice.
- Pressing it does real work, so do not press it repeatedly while a run is
  already going. One press is enough.
- Nothing here can be deleted or undone. If a button gives you an error,
  nothing happened — try again, or ask a developer.

---

## What this is

Ten tasks run by themselves on a schedule, without anyone clicking anything.
This screen shows all ten: when each last ran, whether it is on time,
whether it is still **armed**, and what the most recent weekly report said.

**Armed** means the system still holds a live instruction to keep running that
task. A task that has failed repeatedly stops, and its row will say *not
armed* with a **Re-arm** button beside it. Pressing that is safe — it puts the
task back on the schedule and does nothing else. If several rows say *not
armed* at once, that is a developer's question rather than yours.

---

## When you use it

- To check whether last night's or last week's automatic run actually
  happened.
- To read the latest weekly summary of what is expiring and what has lapsed.
- To start the weekly report or the mailbox check early, without waiting for
  its own schedule.

---

## Before you start

Nothing. Being logged in is enough.

---

## What you do

1. Click **Scheduler** in the **Operator** block at the bottom of the menu.
2. Read the **State** column for each of the ten tasks — see the verdict
   table below.
3. Read the **Armed** column. Every row should say *armed*. A row saying *not
   armed* has stopped and needs the **Re-arm** button next to it.
4. If a row has a **Run now** button and you want that task done early,
   press it.
5. Scroll down to **Latest weekly report** for the most recent summary of
   what is expiring and what has lapsed.

---

## What happens then

The ten tasks, and whether each one has a working **Run now** button:

| Task | What it does | **Run now** button? |
|---|---|---|
| **Monthly catalogue ingest** | Brings in the newest product list from Business Central | No — start an import yourself from [Ingest](ingest.md) instead |
| **Expiry scan** | Checks every document's dates and updates what is expiring or has lapsed. If switched on, it also goes looking for a newer version of anything that has lapsed | No |
| **Coverage scan** | Looks for documents for product groups that have none yet, a small batch each day | No |
| **Discovery failure monitor** | Watches for a supplier whose documents have stopped being found, so it can flag them for a second look | No |
| **Weekly report** | Builds the expiring/lapsed summary shown at the bottom of this page | **Yes** |
| **Mailbox poll** | Checks the inbox for supplier replies and attachments | **Yes** |
| **EUDAMED certificate register pull** | Downloads the EU certificate register in one go and matches it to our manufacturers | **Yes** — and this is the only way it ever refreshes, because its own schedule is switched off |
| **EUDAMED device sweep — mark due** | Marks manufacturers as due for a device check. It never starts one: a person presses **Start the check** for each supplier, from [Manufacturers](manufacturers.md) | No |
| **Health watch** | Sends one line to the alert channel when a service stops reporting in, the queue stops moving, or one of these tasks dies. Once per problem, with a second line when it clears | No |
| **Business Central — re-push what drifted** | Sends the three compliance fields back to Business Central for the items whose answer has changed, oldest first. Needs two switches: the writeback itself, and a second one for this task alone, so a first bulk send can be checked before this starts filling Business Central on its own. Both are off, which is the normal setting | No |

The seven without a button run inside the system itself — there is nothing
separate to start by hand. The screen says so directly, in the small print
under each of those rows. See the translation table below for exactly what it
says.

---

## What can go wrong

| You see | It means | What to do | Good or bad |
|---|---|---|---|
| **ok** | This task has already run for the current period | Nothing | Good |
| **due** | Not run for the current period yet, but the last run was recent enough that this is normal | Nothing, unless it stays **due** far longer than its own schedule | Fine, for now |
| **stale** | This task has not run in a long time — longer than two of its own cycles | Tell a developer | A developer's problem |
| **never** | This task has no recorded run at all | Tell a developer — the scheduler itself may not be switched on | A developer's problem |
| **unledgered** | Only on *EUDAMED device sweep — mark due*. That task keeps no run record on purpose, so there is no period to judge | Nothing. Check its **Armed** column instead | Normal |
| **not armed** in the Armed column | That task has stopped and will not run again until it is put back | Press **Re-arm** next to it. If it keeps stopping, tell a developer | Yours to press, a developer's if it repeats |
| The banner *"The scheduler has never run against this database"* | The whole scheduler is not running | A developer's problem — it needs starting | A developer's problem |
| **dead jobs** on the weekly report tile, above zero | Some piece of the system's own work failed and gave up, as of that report | Open [Failed](failed.md) | A developer's, once re-running does not clear it |
| **already lapsed** above zero on the weekly report | Documents have passed their date with nothing newer on file | Open [Expiry](expiry.md) and chase the renewal | Yours |

---

## Words the screen uses

| The screen says | It means |
|---|---|
| **Cron** | One of the ten tasks that run on their own schedule |
| **Cadence** | How often a task is supposed to run — monthly, daily, weekly, or every so many hours |
| **Current period** / **Last recorded** | An internal label for "this month" or "this week" — used to tell whether the task has already run for now |
| **Ledger** (the code shown under each task's name) | An internal record name. Ignore it |
| **runs in-process — no job to enqueue** | This task has no separate button because it is not a standalone piece of work you can start on its own |
| **armed** / **next poll** | The system still holds a live instruction to keep running this task, and the time it will next check |
| **not armed** / **Re-arm** | This task has stopped. **Re-arm** puts it back on the schedule |
| **Job** column, and codes like `#412` | The internal work item behind a run, with a link to its detail page. Built for a developer |
| Emission flags (text mentioning `SCHEDULER_…` and ON/OFF) | Switches a developer can flip to turn part of a task on or off. Not something you change here |
| **expiring ahead** / **already lapsed** | Same meaning as on [Expiry](expiry.md) |

---

## Related

- [Expiry](expiry.md) — the full list behind the weekly report's figures
- [Failed](failed.md) — where **dead jobs** takes you
- [Ingest](ingest.md) — start a catalogue import by hand
- [Glossary](../glossary.md#words-about-the-systems-own-work) — **ok / due /
  stale / never**, defined once
