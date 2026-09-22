# Expiry

**In one sentence:** what compliance paperwork has already lapsed, and what is
about to.

**Status:** Live. Checked against the code on 2026-09-15.

**This page is read-only.** Nothing on it writes anything, sends anything, or
changes any of your documents.

---

## What this is

Three lists, not one: what has already lapsed, what is lapsing soon, and — kept
out of the way, but not hidden — what lapsed longer ago than that. A single list
used to mix "already broken" in with "about to break", which buried the most
urgent rows.

Each row is one certificate or declaration's date, even when several of your
documents quote the same one, with how many documents cite it and how many
products lose their published coverage once it lapses.

The two windows start at **180 days back** and **30 days ahead**: expired in
the last 180 days, and expiring in the next 30 days. The 30 days ahead is the
one expiring window the whole system uses, and [Today](today.md), the weekly
report and this page all name it.

The number beside **Expiring** in the menu is the first two of those three
lists added together, at those two settings, counted the same way: one per
certificate, not one per document. So the menu, Today and this page can never
tell you different numbers. What lapsed long ago is deliberately left out of
that count: it is standing exposure to evidence on request, not this week's
work.

Below the three lists sits a comparison against EUDAMED: certificates that
database reports as withdrawn or suspended, certificates whose details disagree
with what you hold, and certificates EUDAMED lists that you hold no copy of at
all.

---

## When you use it

- Your weekly check of what needs chasing.
- Deciding which supplier to email next about a renewal.
- Checking whether a lapsed certificate already has a renewal request started
  for it.

---

## Before you start

Nothing.

---

## What you do

1. Click **Expiring** in the menu on the left, or **See which** on
   [Today](today.md).
2. Read **Review due** first if it is not empty. These have not expired: a
   declaration carries no expiry date of its own, so Dentalia lists it five
   years after it was issued. Nothing has lapsed and no product has lost its
   cover — ask the supplier to confirm the declaration is still current, rather
   than to renew it. Class I products have no certificate at all, so this is
   the only reminder they will ever produce.
3. Under **Review due** there is one **Ask** button per supplier. It writes a
   letter asking them to confirm the declarations are still current — never to
   renew them, because nothing has lapsed. One letter covers every declaration
   of theirs on the list. Nothing is sent: the draft waits on **Renewal emails**
   for you to read, address and send.
4. Read **Expired in the last 180 days** — this is live, unresolved work, and
   every date here is one the document or its certificate states.
5. Read **Expiring in the next 30 days** for what to chase before it becomes
   urgent. If it is empty, the line under it says how far away the next date is.
6. Change **Expired within** or **Expiring within** to 30, 90, 180 or 365 days
   and press **Filter** to widen or narrow either window.
7. Open **Long expired**, if you need to, for everything older than the first
   window covers.
8. Click **detail** on a row to open the document itself.
9. Look at the **Chase** column — a coloured label there means a renewal
   request for that supplier already exists; click it to see it.
10. Scroll down to the EUDAMED comparison for the same kind of check across
   every supplier at once, not just the ones with a date coming up.

---

## What happens then

Nothing changes on this page itself. It only tells you what needs chasing —
chasing itself happens by emailing the supplier, from
[Renewal emails](drafts-out.md) or by hand.

---

## What can go wrong

| You see | It means | What to do | Good or bad |
|---|---|---|---|
| A row shaded more strongly than the others | This one needs attention now — either already lapsed, or lapsing within the next 30 days | Look at it first | Bad, needs chasing |
| A **Documents** count higher than 1 on one row | Several of your documents quote the same certificate's date | Nothing extra to do — chasing the supplier once covers all of them | Neutral |
| A date marked *(review)* | Not a real expiry — see the five-year rule | Treat it as a prompt to check with the supplier, not a breach | Neutral |
| A date marked *(inherited)* | Not the declaration's own date — it is borrowed from the certificate it refers to | Nothing extra to do | Neutral |
| *N further certificates expire beyond this window* | More lapsing dates exist than the current filter shows | Click **Show everything** if you want to see them | Neutral |
| No coloured label in the **Chase** column | Nobody has started a renewal request for this supplier yet | Consider starting one from Renewal emails | Depends on how urgent the row is |

---

## Words the screen uses

| The screen says | It means |
|---|---|
| *(review)* / *(inherited)* next to a date | See [the 5-year rule](../glossary.md#the-5-year-rule) |
| **Type**, shown as *Declaration of Conformity*, *EC certificate*, *Instructions for use*, *ISO certificate* | What kind of document carries the lapsing date |
| **Documents** | How many of your held documents carry this lapsing date |
| **Items affected** | How many products lose their published coverage once this date passes |
| **Revision drift** | EUDAMED lists a different version number for a certificate than the one on the document you hold |
| **Certificate status alerts** | EUDAMED itself reports a certificate as withdrawn, cancelled, suspended or restricted |

---

## Related

- [Documents](documents.md) — open any document named on this board
- [Manufacturers](manufacturers.md) — the same EUDAMED comparison, one supplier at a time
- [Glossary](../glossary.md) — the 30-day and 5-year rules explained in full
