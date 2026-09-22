# Processing

**In one sentence:** this screen answers one question — how much is the system
working on right now.

**Status:** Live. Checked against the running screen on 2026-08-31.

---

## What this is

This screen is read-only. Nothing you do here changes anything.

It counts documents the system is actively reading or checking at this moment,
and breaks that count down by what stage each one is at.

---

## When you use it

- The **Documents being read** figure on [System status](status.md) brought you
  here, and you want the detail behind it.
- You want to know whether the system is doing anything right now, or sitting
  idle.

---

## Before you start

Nothing. Being logged in is enough.

---

## What you do

1. Open **Queues & health** in the Operator block at the bottom of the menu,
   then **Processing**.
2. Read the total, and the breakdown table underneath it.

There is nothing to decide here. If the total is high, wait — it usually
settles on its own. If it stays high for a long time and never moves, tell a
developer rather than trying to fix it yourself.

---

## What happens then

You see one number — **documents in flight** — and a table with one row per
stage a document can be at:

| Column | Means |
|---|---|
| **Stage** | Which step of reading and checking a document is at |
| **Documents** | How many distinct PDFs are at that step |
| **Jobs** | How many pieces of work that represents |

**Documents** and **Jobs** are not the same number. One PDF can have more than
one piece of work running on it at once — a document being checked can already
have its filing decision started — so the Jobs column is often higher, and
the rows do not add up to the total at the top.

In practice you will only ever see a small handful of stages here — the
document is being **read** (its text and dates are being pulled out), being
**checked** (its dates and product references are being verified), or having
its **filing decision** made. Earlier steps, like looking for a document's web
page, never show up here, because this screen only counts documents that have
already been found and are now being worked on.

---

## What can go wrong

| You see | It means | What to do | Good or bad |
|---|---|---|---|
| A total of zero | Nothing is being processed right now | Normal, especially outside business hours | Fine |
| A steady, moving total | Documents are being read and checked as expected | Nothing | Fine |
| A high total that never changes across several checks | Something may be stuck | Tell a developer | A developer's problem |

---

## Words the screen uses

| The screen says | It means |
|---|---|
| **documents in flight** | The total this page is about. Everywhere else the same idea is called **Being read** |
| Raw stage names like `extract.doc`, `validate.doc` | The internal name for a step. `extract.doc` is reading a document; `validate.doc` is checking it |
| **Jobs** | Pieces of work. See [Words about the system's own work](../glossary.md#words-about-the-systems-own-work) in the glossary |

---

## Related

- [System status](status.md): its **Documents being read** tile is the same count as
  this page's total
- [Review](review.md) — where a document goes once the system has finished
  checking it and is not sure about it
- [Glossary](../glossary.md) — every word in one place
