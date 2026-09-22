# System status

**In one sentence:** this is the machine's own dashboard — most of the numbers on
it describe the system, not your documents.

**Status:** Live. Checked against the code on 2026-09-14.

---

## What this is

This screen is read-only. Nothing you do here changes anything.

It used to be the page you landed on. Since 14 September 2026 that is
[Today](today.md), and this board moved to its own address under **System
status**, in the **Operator** block at the bottom of the menu. One sentence
about declarations at the top, a chip per running service, seven numbers, and
four tabs underneath with more detail behind each one.

Only a few of the seven numbers are yours to act on. The rest describe how the
machine is doing, and exist mainly so a developer can be shown them.

---

## When you use it

- When someone asks "what has this cost us".
- When a developer asks you to read them a number.
- The compliance questions are on [Today](today.md), which carries the same
  declarations sentence.

---

## Before you start

Nothing. Being logged in is enough.

---

## What you do

1. Click **System status** in the **Operator** block at the bottom of the menu.
2. Read the declarations sentence at the top. Its link opens the items that
   have no declaration on file. It is the same sentence as on
   [Today](today.md).
3. Look at the chips under the heading: one per part of the system, with its
   last heartbeat in the tooltip. The sidebar's health line is the short
   version of the same reading.
4. Look at the seven tiles. If a tile has a number worth acting on, click it —
   it takes you straight to the matching screen.
5. Click **Coverage**, **Queue**, **Spend** or **Recent jobs** below the tiles
   for more detail. **Coverage** is the one worth your time; the other three
   describe the machine.

---

## What happens then

First, the sentence **Declarations on file for X of Y medical-device items
(Z%)**. It is the figure to quote to Dentalia, and it is **yours**:

- **Y** is every item Business Central marks as a medical device. Items with no
  device class in Business Central are not counted, and the line under the
  sentence says how many there are.
- **X** is the items among them that hold a published Declaration of
  Conformity linked to the item itself: by its item number, the supplier's
  article number, UDI or the supplier's coverage list. A declaration that
  covers a manufacturer's whole range does not count here, because it does not
  say which items it means. An expired declaration still counts as on file.
- The link **Show the N without a declaration** opens [Coverage
  gaps](coverage.md). The **No Declaration of Conformity** count there is the
  same N, always.

This sentence replaced a tile that read 99.7%. That tile counted any published
paper at all, including a supplier's quality-system certificate, which says
nothing about any one product.

Then the seven tiles, in the order they appear:

| Tile | What it shows | Whose problem |
|---|---|---|
| **missing mfr_ref** | Products where the supplier's own article number is not on file. Worth knowing, not a target to hit | **Yours**, low priority |
| **to review** | Same count as **Review** in the menu. Documents waiting for your decision | **Yours** |
| **manual queue** | Things the system is asking a person to handle. The office half of it is [Missing documents](missing.md) | **Yours**, mostly |
| **dead jobs** | Failed tasks that still need someone. A task somebody already sent back is not counted, so this tile and [Failed tasks](failed.md) read the same number | **A developer's** — see below |
| **sweep due** | Suppliers due a EUDAMED check. Nothing runs until you press **Start the check** | **Yours**, occasional |
| **Documents being read** | How many documents the system is reading right now | Informational |
| **the euro amount, and "30d of €…"** | What the system has spent in the last 30 days, against its monthly cap | Worth watching, rarely urgent |

**Documents being read** counts the same thing as the [Processing](processing.md)
screen: one per document, however many steps it is going through. Until 11 September
2026 this tile was called **in flight** and counted every waiting task of any
kind, including the ten recurring timers that are always waiting.

Below the tiles, four tabs:

- **Coverage**: four older ways of counting coverage, each under its own
  name, the missing-article figure, and how documents were matched to your
  products.
- **Queue** — how much is waiting, broken down by kind. Mostly for a developer.
- **Spend** — money. See below.
- **Recent jobs** — a raw list of everything the system has done recently.
  Built for a developer to search through, not for daily reading.

### Coverage, in detail

The **Coverage** tab keeps four other ways of counting coverage. None of them
is the declarations sentence at the top; each answers a different question:

| Column | What it counts |
|---|---|
| **Strict** | Only products Business Central clearly marked as medical devices |
| **Processed scope** | The same, but also counts products Business Central has not yet classified. A wider, more cautious count |
| **Device (MDR/MDD)** | The same narrow group as Strict, but only counts a document written for medical devices. A quality-system certificate alone does not count here — it says the supplier runs a proper quality system, not that this product meets the rules |
| **Declared (DoC)** | The same narrow group, but only counts a Declaration of Conformity that has not expired |

The first three count any current published paper; only **Declared (DoC)**
and the sentence at the top ask about declarations.

Under that, **Missing mfr_ref** repeats the tile as missing / total / rate, and
a further table shows how your published documents were tied to your products
— see the translation table below for what that section is called on screen,
and [How it was matched](../glossary.md#words-for-how-a-document-is-matched)
in the glossary for what each row means. Both are informational.

### Spend, in detail

The **30d of €…** tile is the amount spent in the last 30 days, against a
monthly cap. The **Spend** tab adds a second figure: the total ever spent,
against a separate all-time cap.

**Neither cap stops anything automatically.** Going over either one does not
block the system from working. It is a number to notice and mention to a
developer, not an alarm.

If the tab shows a note that some calls have no price recorded, that is a gap
in the system's own bookkeeping — a developer's problem, not yours.

---

## What can go wrong

| You see | It means | What to do | Good or bad |
|---|---|---|---|
| The declarations figure falling | Fewer of your medical-device items have a declaration on file | Worth asking about. Usually new items arrived from Business Central with no declaration yet | Bad, but not urgent |
| **to review** rising fast | The system is finding more than you can clear | Spend time in Review. If it keeps outpacing you, tell a developer — a supplier likely changed how they publish | Yours to work through |
| **manual queue** rising | More things need a person | Open [Manual](manual.md) and see which rows are actually yours | Yours to work through |
| **dead jobs** above zero | Some task failed repeatedly and gave up | Open [Failed tasks](failed.md). The first two sections have a button each and are yours; the third is a developer's, and its **Re-run all** is an operator control | A developer's, once re-running does not clear it |
| **sweep due** above zero | A supplier is due a EUDAMED check | Open the tile and press **Start the check** when you have a moment | Yours, not urgent |
| **Documents being read** high and not moving | The machine looks busy but nothing is finishing | Not something to diagnose yourself. Tell a developer | A developer's |
| 30-day spend near or over its cap | The system is costing more than usual this month | Mention it. Do not try to stop anything yourself | A developer's to size, yours to notice |

---

## Words the screen uses

| The screen says | It means |
|---|---|
| **System status** | The heading at the top of this page, and its name in the menu's Operator block. It read "Queue status" until 14 September 2026 |
| **mfr_ref** | The supplier's own article number for a product |
| **match_basis**, and rows like `ref-list`, `name-family` | How a document was tied to a product. Every other screen heads this column **How it was matched**. See the [glossary](../glossary.md#words-for-how-a-document-is-matched) |
| **staged links on production (C5)** | Connections between a document and a product that are still waiting for a person, even though the document itself already counts. Informational — there is nothing to click here. **C5** is an internal rule number; ignore it |
| **pending / running / done / failed / dead** | Stages of one piece of the system's own work. See the [glossary](../glossary.md#words-about-the-systems-own-work) |
| Raw task names like `ingest.run`, `fetch.url`, `extract.doc` | What kind of work one task is. Built for a developer; the plain-language stage name (INGEST, FETCH, EXTRACT…) next to it is the readable version |
| **dedupe key** | An internal code that stops the same task being started twice. Not something you need to read |
| **priority** — `interactive` / `delta` / `sweep` | Why a task exists: `interactive` means a person's click caused it right now; `delta` and `sweep` mean the system's own schedule did |
| **unpriced calls (pricing gap)** | Spend the system could not put a price on. A bookkeeping gap, not a cost you owe |

---

## Related

- [Today](today.md): the office's first screen, which carries the same declarations sentence
- [Processing](processing.md): the **Documents being read** number, by stage
- [Coverage gaps](coverage.md): the items behind the declarations sentence
- [Review](review.md) — where **to review** takes you
- [Manual](manual.md) — where **manual queue** takes you
- [Failed](failed.md) — where **dead jobs** takes you
- [Data quality](data-quality.md) — catalogue and device-class checks, not covered on this page
- [Glossary](../glossary.md) — every word in one place
