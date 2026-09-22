# Today

**In one sentence:** the screen you land on, listing everything waiting for you
right now and nothing else.

**Status:** Live. Checked against the code on 2026-09-15.

**This screen is read-only, with two exceptions**: the two buttons under
*Failed tasks*, which ask the system to try some work again. They are the only
buttons on the page, and that is deliberate: a button here means the press
changes something. Everything else is a link that just opens a list. Neither sends
anything to a supplier and neither changes a document.

---

## What this is

The first screen after you log in, and the one to come back to when you have
finished something. It answers one question: what is waiting for me?

It replaced the machine's own dashboard here on 14 September 2026. That board
still exists as [System status](status.md), in the **Operator** block at the
bottom of the menu, and almost nothing on it was ever your work.

Every number on this page is counted fresh each time you open it, and every
number is the same number the screen behind it shows. If Today says 34
documents are waiting for review, Review lists 34.

---

## When you use it

- First thing in the morning.
- After finishing a queue, to see what is next.
- When someone asks how coverage is doing: the sentence near the bottom is the
  figure to quote.

---

## Before you start

Nothing. Being logged in is enough.

---

## What you do

The four lists sit side by side at the top of the page, two across on a normal
screen and one across on a narrow window. Each has a count, one sentence saying
what it is, and one link that opens it.

| List | What is in it | Opens |
|---|---|---|
| **Documents to review** | Documents the system found and will not publish without your decision. Shows the date the oldest one has been waiting, and the three suppliers with the most | **Start reviewing** → [Review](review.md) |
| **Missing documents** | Items it searched for and found nothing for. Shows the oldest and the three suppliers with the most | **Open the list** → [Missing documents](missing.md) |
| **Expiring certificates** | Certificates that have lapsed or are about to, one line per certificate however many documents cite it. Both windows are named on the line: the next 30 days, and the last 180 | **See which** → [Expiry](expiry.md) |
| **Renewal emails to send** | Renewal mails the system wrote from what is expiring. It never sends them | **Open drafts** → [Renewal emails](drafts-out.md) |

A list with nothing in it says **Nothing waiting** and keeps its link.

---

## What happens then

### Failed tasks

Under the four lists, up to three lines about work that failed. **A line you
cannot see is a line whose count is zero** — the section disappears entirely
when nothing has failed.

| Line | What it means | What you do |
|---|---|---|
| **N supplier websites took too long to answer** | A supplier's site was slow or unreachable | Press **Try the N again**. It asks once, naming the number. Usually it works |
| **N document addresses no longer work** | The page a document used to live at is gone | Press **Search again**. The system looks for where each document lives now |
| **N technical faults are waiting for the developer** | Something broke that retrying will not fix | Nothing. **What are they?** opens [Failed tasks](failed.md) if you want to look |

These counts are only the failures that still need someone. Work you have
already sent back drops out of them and shows on the Failed screen as *trying
again* or *searching again*.

### The declarations sentence

**Declarations on file for X of Y medical-device items (Z%)** is the figure to
quote to Dentalia, and the line under it says exactly what it counts: items
Business Central marks as medical devices, and, where there are any, how many
items BC has not classified at all and so are not counted. The link opens
[Coverage gaps](coverage.md), where the **No Declaration of Conformity** count
is always the same number.

### The week's report

The last card links the latest [weekly report](reports.md) by name — *Week 37
(7–13 Sep)* — and the one before it, plus **All weekly reports** for the rest.
This is the only way into that screen from the menu.

---

## What can go wrong

| You see | It means | What to do | Good or bad |
|---|---|---|---|
| **Documents to review** climbing day after day | The system is finding more than you can clear | Spend time in Review. If it keeps outpacing you, tell a developer: usually one supplier changed how they publish | Yours to work through |
| The oldest waiting date is weeks old | Something at the bottom of the queue has been skipped every day | Open Review and work oldest first, which is the order it lists them in | Worth a morning |
| **N supplier websites took too long** every day, same number | Trying again is not fixing it | Stop pressing the button and tell a developer, naming the supplier | A developer's |
| The declarations figure falling | Fewer of your medical-device items have a declaration on file | Usually new items arrived from Business Central with no paperwork yet. Worth asking about | Bad, not urgent |
| **No weekly report yet** | The weekly report has not run on this machine | Tell a developer if it stays that way for more than a week | A developer's |

---

## Words the screen uses

| The screen says | It means |
|---|---|
| **Documents to review** | The Review queue. Same count as **Review** in the menu |
| **Missing documents** | Items searched for without finding anything. Same count as **Missing documents** in the menu |
| **Expiring certificates** | Certificates lapsing or lapsed inside the two named windows. Same count as **Expiring** in the menu |
| **Renewal emails to send** | Drafts nobody has marked *sent* or archived. Same count as **Renewal emails** in the menu |
| **Failed tasks** | Work the system tried several times and gave up on. See [Failed tasks](failed.md) |
| **Declarations on file** | A published Declaration of Conformity, linked to the item itself. See [Coverage gaps](coverage.md) |

---

## Related

- [Your daily round](../01-daily-work.md) — the order to work these lists in
- [Review](review.md) — where most of your time goes
- [System status](status.md) — the machine's own board, for operators
- [Glossary](../glossary.md) — every word in one place
