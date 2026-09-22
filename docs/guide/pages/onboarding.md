# Onboard a supplier

**In one sentence:** the queue of suppliers the system is still searching for
blindly, and the four short steps that fix one.

**Status:** Live. Checked against the code on 2026-09-15.

---

## Can I break anything here?

Almost nothing, and nothing that cannot be undone.

- **Nothing is fetched from anybody's website** until you press **Probe**, and
  a probe reads one page: it downloads no documents, saves nothing and changes
  no settings.
- Nothing here publishes a document. Everything the system finds afterwards
  still waits for a person on [Review](review.md).
- Every setting you enter is saved as a numbered version with your name on it,
  and can be changed or put back on the supplier's own
  [playbook page](playbooks.md).
- **Change with care:** taking a supplier *out of the queue* is a decision
  other people will rely on. If you mark a real manufacturer as "not a
  manufacturer", nobody will look for its paperwork again until somebody puts
  it back. That is why a reason is required.

---

## What this is

A supplier with no **playbook** is one the system looks for blindly: every
search it runs is unrestricted, and it knows of no page that lists that
supplier's documents. Most suppliers are in that state: 328 of them at the
time of writing, against 39 that have a playbook.

This screen is the queue of those suppliers and the job of clearing it. It puts
the four things that make up a playbook in order, on one screen each, so you
never have to know which page to go to next.

The queue is ordered by how many items each supplier has, because that is where
a playbook is worth the most. A supplier with no items is not queued at all:
there is nothing for a playbook to cover yet.

---

## When you use it

- Regularly, as a job you pick up and put down. The count in the corner going
  down is the point of it.
- When a new supplier appears in Business Central and starts showing up with no
  paperwork.
- When somebody asks why the system has found nothing for a particular
  supplier. Very often the answer is that it has no playbook.

---

## Before you start

Nothing. Everything you need is on the screen.

It helps to have the supplier's website open in another tab, because step 2
asks for it and step 3 asks for their downloads page.

---

## What you do

1. Open **Playbooks** in the **Operator** block at the bottom of the menu, then
   **Onboard a supplier**.
2. Press **Start with …** to take the supplier at the top, or click any name in
   the list.
3. **Step 1, Who.** Read what the system already knows: how many items this
   supplier has, its supplier codes, and how many documents we hold. Then give
   the playbook a short name (the suggestion is usually right) and press
   **Start & continue**.
4. **Step 2, Their websites.** Type the supplier's own web addresses, one per
   line. Getting this wrong costs nothing: if a restricted search finds
   nothing, the system tries again without the restriction. Leave it empty if
   you do not know, and press **Continue**.
5. **Step 3, Their library.** Many manufacturers publish one page listing all
   their declarations and instructions. Paste that page's address and press
   **Probe**. The system reads that one page and tells you what it found: see
   [Playbooks](playbooks.md) for how to read the result. When it looks right,
   press **Save this recipe**. What it saves is a **crawl recipe**: the
   instruction for turning that one page into every document on it.
6. If they have no such page, press **They have no library page, continue**.
   That is a normal outcome, and the system will still search for their
   documents one item at a time.
7. **Step 4, Done.** Read what will happen next, then press **Take the next
   supplier**.

**If this supplier does not belong in the queue at all**, use the box at the
bottom of step 1: say whether it is not a manufacturer, has no website, or has
no library page, write a short reason, and press **Take out of the queue**.
The browser asks you to confirm before it removes the supplier from the
queue.

---

## What happens then

- The supplier's items go into the next search run, restricted to the
  websites you entered.
- If you saved a library page, the documents on it are fetched politely (one
  request at a time, spaced out so nobody's website is overloaded) and read.
- Anything the system is unsure about waits for a person on
  [Review](review.md). **Nothing is published without a person.**
- A supplier you took out of the queue is listed at the bottom of the queue
  page in the words you chose ("issues no declarations of its own", "has no
  site we can search", "has a site but no page listing documents"), with your
  own reason and who gave it. **Put back** returns it, and the original note is
  kept either way.

---

## What can go wrong

| You see | It means | What to do | Good or bad |
|---|---|---|---|
| *… is not a usable slug* | The short name has a capital letter, a space or a symbol in it | Use lower-case letters, digits and single hyphens | Neutral |
| *… already has playbook …* | Somebody onboarded this supplier while you were looking at it | Open the playbook it names and carry on there | Neutral |
| A message that the settings changed while your form was open | Somebody else saved this supplier at the same time | Reload and enter your change again | Neutral — nothing is lost, it just needs redoing |
| *say why in a few words* | You tried to take a supplier out of the queue without a reason | Write one. Months later, "files nothing" and "we agreed it has nothing to file" look identical | Neutral |
| **Refused** on a probe | That website's own rules say we may not read that page automatically | Nothing to fix. Continue without a library page | Neutral — the system obeying a rule, not a fault |
| **Nothing matched** on a probe | Either the address is not the list page, or the site holds its links in a way the system cannot follow | Open the page yourself and check the documents are ordinary links | Neutral |

---

## Words the screen uses

| The screen says | It means |
|---|---|
| **Playbook** | Everything the system knows about where to look for one supplier's paperwork |
| **Waiting** | How many suppliers still have no playbook |
| **Short name** | The playbook's own name, and its page address |
| **Their library** | The supplier's page that *lists* documents, not a document itself |
| **Probe** | A look at that page that reports what it found and changes nothing |
| **Take out of the queue** | Record that this supplier does not need a playbook, and why |

---

## Related

- [Playbooks](playbooks.md) — the full playbook page, where anything set here can be changed
- [Manufacturers](manufacturers.md) — everything else about one supplier
- [Review](review.md) — where what the system finds waits for a person
- [Glossary](../glossary.md) — every word in one place
