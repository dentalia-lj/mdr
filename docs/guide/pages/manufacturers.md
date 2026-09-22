# Manufacturers

**In one sentence:** your suppliers, one page each — what they are called, what
paperwork you hold for them, and the tools to keep that in order.

**Status:** Live. Checked against the code on 2026-09-15.

---

## Can I break anything here?

Mostly no, but two of the buttons are worth understanding before you press them.

- Nothing here deletes a document.
- **Start it** (starting a playbook) creates an empty starting point for a new
  supplier. Safe, there is nothing in it yet to get wrong. It is an operator
  action, so you only see it if your login is an operator.
- **Save contacts** only records who future renewal emails are addressed to. It
  sends nothing itself, and you can change it again any time.
- **Check what a rename would change** writes nothing by itself — it only shows
  you what would happen. The second button, **Yes, rename it**, is the one that
  actually changes the name. Even then, the old name is kept as a second name
  rather than deleted, so documents that already print it still resolve, and you
  can rename back if you make a mistake. If you change your mind after reading
  what it would change, **Cancel** clears the question without writing anything.
- **Confirm** and **Reject** on the EUDAMED ID (SRN) queue decide whether one
  EUDAMED registration belongs to one of your suppliers. Both ask the browser to
  confirm before they act. Neither has an undo button on this screen.
  **Confirm** only makes that registration's certificates available
  to compare — nothing is filed automatically. **Reject** is meant to be final:
  the same list is checked again every month, so a decision that quietly
  reverted would mean this queue could never be cleared.
- **Start the check** on the checks-due page starts a real check against a
  European database, after the browser asks you to confirm. It does not touch any of
  your documents — it only compares what that database has registered
  against what you already hold.
- **Draft request for these N group(s)** writes one email asking the supplier
  for the declarations you have never had. It only prepares the letter: nothing
  is sent, and nothing about your documents changes. The draft waits on the
  **Renewal emails** screen for you to read, address and send.
- If a button gives you an error, nothing happened. Try again, or ask a
  developer.

---

## What this is

Four related screens.

- **Manufacturers** — every supplier you deal with, one row each, with how many
  products and documents you hold for them, and whether the system has a
  **playbook** for them yet.
- **One manufacturer's page** — everything about a single supplier: every name
  and code it is known by, how much of its catalogue has the paperwork it needs,
  its documents, its products, and — where EUDAMED knows it — a comparison
  against that database's own records.
- **EUDAMED ID (SRN) queue** — a short worklist confirming which EUDAMED
  registration belongs to which of your suppliers.
- **Check due** — suppliers due a fresh check against EUDAMED, waiting for you
  to start it.

---

## When you use it

- You want to see everything held for one supplier in one place.
- A new supplier needs to be added before the system can start looking for its
  paperwork.
- A supplier's name on a certificate does not match what you call them, and it
  needs fixing.
- You need to tell the system who to address a renewal request to.
- Your EUDAMED ID (SRN) queue or checks-due count is not at zero.

---

## Before you start

For browsing, nothing. To rename a supplier, add contacts for one, or start a
playbook for one, that supplier must already exist in the system's own supplier
list — a brand new supplier sometimes does not yet, and the page says so plainly
when that is the case.

---

## What you do

**The manufacturers list**

1. Click **Manufacturers** in the menu on the left.
2. Search by name or Business Central code, or tick
   **Only without a playbook** to see suppliers nobody has written a playbook
   for yet. That gives you the list; to actually work through it, follow
   the link to [Onboard a supplier](onboarding.md), which puts the same
   suppliers in order of how many items each one leaves uncovered and remembers
   the ones you have already ruled out.
3. Click a supplier's name to open its page.

**One manufacturer's page**

4. Read **Names in Business Central** for every name and code this supplier is
   known by. Which spelling came from where is under **Technical details**
   beneath it.
5. Read the compliance summary for how much of this supplier's catalogue has
   what it needs, and what is missing.
6. Under **Find missing documents** you are told how many product groups from
   this supplier hold no approved document. Press **Search for documents** and
   the system goes looking for them on the web. If the supplier has more groups
   than one press covers, the screen says so — press again to carry on through
   the rest. Each press starts with the groups nobody has looked at for the
   longest, so pressing repeatedly works through the whole supplier rather
   than repeating the same ones.
7. Use the tabs — **All documents**, **Published documents**, **Items** — to
   browse the supplier's paperwork and products.
8. If nothing is authored for this supplier yet, and you are an operator, type
   a short address under **Start a playbook** and press **Start it**. An office
   login sees a line saying this one is for operators instead.
9. Under **Renewal contacts**, type one or more email addresses, separated by
   commas or semicolons, and press **Save contacts**.
10. To fix how a supplier's name is spelled, type the new name and a reason under
   **Rename**, then press **Check what a rename would change**. Read what it
   says, and press **Yes, rename it** only once you are sure, or **Cancel** to
   back out without changing anything.

**EUDAMED ID (SRN) queue**

11. Open [EUDAMED checks](eudamed.md) and follow **EUDAMED ID (SRN) queue**.
12. For each row, check whether the EUDAMED entry really is that supplier, then
    press **Confirm** or **Reject**.

**Check due**

13. Open [EUDAMED checks](eudamed.md) and follow **Check due**.
14. For each supplier due a check, press **Start the check**. The button is
    only available once a supplier's EUDAMED identity has been confirmed on the
    EUDAMED ID (SRN) queue.

---

## What happens then

- Starting a playbook creates an empty one immediately. You, or a developer, can
  fill in detail on [Playbooks](playbooks.md) afterwards.
- Saving contacts takes effect immediately — the next renewal request the system
  writes will be addressed to them.
- **Check what a rename would change** writes nothing. It tells you how many
  product families still carry the old name and cannot be moved automatically,
  and whether any of them already hold documents. Only **Yes, rename it**
  changes the name, and it takes effect immediately. The confirmation may
  mention a follow-up step a developer still needs to run — if so, pass it on.
- **Confirm** on the EUDAMED ID (SRN) queue makes that EUDAMED registration's
  certificates available to compare against this supplier's catalogue.
  **Reject** records that they are not the same supplier.
- **Start the check** starts a real check against EUDAMED in the background. The
  row leaves the checks-due list as soon as you start it, not once the check has
  actually finished.
- **Draft request for these N group(s)** appears under "Groups to request",
  once a supplier has been checked against EUDAMED and something is missing.
  One press writes ONE draft covering every group listed, not one email per
  group — a supplier missing seventy declarations is still one letter. Each
  line names the group and a few of your own article numbers as examples, so
  the supplier can recognise the goods without looking the identifier up.
  The button does nothing if the list is empty.
- **Ask for these N certificate(s)** appears under "Certificates we hold no
  copy of". It is the same kind of letter and a stronger one: the European
  database names the certificate the supplier holds — its number, its revision,
  when it was issued and until when, and which notified body issued it — and
  the letter quotes all of that back. The supplier does not have to work out
  which document you mean, and the letter says the list came from their own
  entry, so it reads as a comparison rather than a demand. One press writes ONE
  draft covering every certificate listed. The button does nothing if the list
  is empty, and asks only once per supplier: if a draft already exists, the
  page says so and links to it instead of writing a second letter. The button
  appears only on a supplier's own page — the **Expiry** screen shows the same
  list for every supplier at once and deliberately carries no button there,
  because one press would mean a letter to each of them.

---

## What can go wrong

| You see | It means | What to do | Good or bad |
|---|---|---|---|
| A message saying this supplier has no record set up yet, on Rename or Renewal contacts | Initial setup for this supplier has not run | Tell a developer | Neutral — normal for a brand-new supplier |
| A message after **Check what a rename would change**, listing product families that "will keep" the old name | Some of this supplier's products were already grouped under the old name and a rename cannot move them by itself | Read it before confirming. If it says documents are already attached, a developer needs to finish the move | Depends — read carefully before confirming |
| *… contains the BC brand … which this manufacturer does not claim* | The new name overlaps with a Business Central brand that belongs to a different supplier | Choose a different name, or ask a developer to sort out the code first | Bad — do not force it |
| *… already belongs to …* | Another supplier already has that exact name | Choose a different name | Bad |
| The **Start the check** button is greyed out | Nobody has confirmed a EUDAMED identity for this supplier yet | Work through the EUDAMED ID (SRN) queue first | Neutral |
| *Nothing waiting* on the EUDAMED ID (SRN) queue | Every uncertain EUDAMED identity has already been decided | Nothing to do | Good |
| *Nothing due right now* on Check due | Every supplier's next check is either not due yet, or already started | Nothing to do | Good |

---

## Words the screen uses

| The screen says | It means |
|---|---|
| **Playbook** | What the system knows about one supplier: their website, where their documents live, how their paperwork reads |
| **Short name** | The address a playbook lives at, in lower-case letters and digits — `ivoclar`, not the full legal name |
| **Names in Business Central** | Every code and spelling Business Central holds for this supplier |
| **Name in EUDAMED** | The name that European database has on file for this registration |
| **EUDAMED ID (SRN)** | The supplier's single identity number in EUDAMED. See [EUDAMED ID (SRN)](../glossary.md#eudamed-id-srn) |
| **How close the names are** | How closely the EUDAMED name resembles your supplier's name. Read the two names yourself rather than trusting the number alone |
| **EUDAMED check** | A check of one supplier against EUDAMED |
| **Start the check** | Starts one, for one supplier |
| "Attributed by what each document says about itself" | Documents matched to this supplier by their own wording, not by a product link |
| The small coloured label next to a document (*Published*, *Waiting for review*, *On file*, *Replaced*, *Rejected*) | See [document status](../glossary.md#document-status) |
| **Technical details** | The stored keys behind the screen — where each spelling came from, how a EUDAMED candidate was found. Safe to ignore day to day |

---

## Related

- [Playbooks](playbooks.md) — writing the playbook a supplier's page links to
- [Documents](documents.md) — a supplier's paperwork, searchable across every supplier at once
- [Items](items.md) — one product at a time
- [Expiry](expiry.md) — the same EUDAMED comparison, for every supplier at once
- [Glossary](../glossary.md) — every word in one place
