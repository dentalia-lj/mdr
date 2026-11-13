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

### Link checks

Every time the screen opens it checks every published link against Business
Central. Normally the card says **All N published links match the manufacturer
Business Central gives each item**, which means nothing to do. When it finds
something, the heading shows **N to look at** and the card names each document:

| Line | What it means | What you do |
|---|---|---|
| **N published links reach an item another manufacturer makes in Business Central** | A published document counts for an item that BC files under a different manufacturer. The line says which, and how many items | Open the document. If the item is wrong, use **Remove** on it. If BC files the item under the wrong manufacturer, tell whoever keeps BC |
| **N published links sit under a document with no confirmed manufacturer** | A published document counts for items but no manufacturer was ever confirmed for it | Open the document and tell a developer |

It cannot see a document from the right brand but the wrong maker, such as a
contract maker's certificate counted for the brand's whole range. That needs
Business Central's maker field, which the system does not read yet.

### Filed, but maybe ours

A document is **On file** when none of the article numbers it prints is a
Business Central number of its manufacturer. It stays in the registry but counts
for no item, and no other screen lists it. This card looks again, more loosely,
and lists the documents On file that print a number the same as, or close to, a
number of the same manufacturer. It only suggests: nothing changes until you add
an item. When there is nothing to suggest, the card does not show.

Each row says what was printed, the Business Central item it is close to (with
**+N more** when several items match the same way), and how close, in colour:

| Label | What it means | How sure |
|---|---|---|
| **Same number** (green) | The same number once spaces, dots and dashes are dropped: `606.20.110` and `60620110` | Very likely ours. A number that is a word, such as `S-tip`, can match by chance |
| **Prefix** (amber) | One number is the other with something in front: `0541` and `6050541`, `9331` and `UP-9331` | Probably ours: check the item name |
| **Leading zeros** (blue) | The numbers differ only by zeros in front: `000529397` and `529397` | Probably ours. A short number such as `017` matches many items: check carefully |

The first 12 rows show, in the order of the labels above; **See all N** opens the rest in place.
Press **Open**, check the document, and use **Add items this document covers…**
on its page (see [Documents](documents.md)). Adding an item publishes the
document, and it leaves the card. Rejecting it also takes it off.

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
| **Filed, but maybe ours** | Documents On file, counting for no item, that print a number close to one of their manufacturer's Business Central numbers |
| **Declarations on file** | A published Declaration of Conformity, linked to the item itself. See [Coverage gaps](coverage.md) |

---

## Related

- [Your daily round](../01-daily-work.md) — the order to work these lists in
- [Review](review.md) — where most of your time goes
- [System status](status.md) — the machine's own board, for operators
- [Glossary](../glossary.md) — every word in one place
