# Start here

**In one sentence:** this system collects your suppliers' compliance paperwork,
files it against your products, and tells you what is missing.

**Status:** Live. Checked against the running screen on 2026-09-14.

---

## What the system does for you

You sell medical devices you did not make. For each one, the law says you have to
check the manufacturer's paperwork is in order — the declaration of conformity,
the instructions for use, the notified-body number. Doing that by hand for
sixteen thousand products is not possible.

So the system does it. It:

1. Reads your product list out of Business Central.
2. Goes looking for each supplier's documents — on their website, in EUDAMED, in
   your mailbox, in the archive you already have.
3. Reads each document and works out what it is, when it was issued, and which of
   your products it covers.
4. Files the ones it is confident about, by itself.
5. **Stops and asks you** about the ones it is not confident about.

Step 5 is your job. Everything in this guide is about doing step 5 well.

---

## What it will never do

- **It never deletes anything.** Every document it has fetched stays on file,
  whatever anyone decides about it.
- **It never sends an email.** It writes renewal requests for you; a person sends
  them.
- **It never publishes a document you rejected.**
- **It never changes a decision you made.** Your name and the time are recorded
  against every one.

---

## Logging in

Open the address your IT contact gave you. Your browser will ask for a username
and password before you see anything.

That is the only door in. You have your own username, not a shared one, so the
system records your name against every decision you make.

If you can see the screens, you can do everything in this guide. A few presses
are reserved for whoever runs the machine, and they are all on screens this
guide does not ask you to use. If you land on one, see **If a screen says
something went wrong** below.

Two other things use the same system without a login, and you may hear them
mentioned:

- **The link on a Business Central item card.** Opens a read-only card showing
  what is held for that one product.
- **The webshop.** Reads published documents automatically so it can show them
  to customers.

Neither can change anything.

---

## Finding your way around

### The menu on the left

Three groups, in this order, and a fourth you can ignore.

| Group | What is in it |
|---|---|
| **Your work** | The four queues, and the screen that lists them: **Today**, Review, Missing documents, Expiring, Renewal emails. Each of the four carries its own count |
| **Records** | What is held, for looking things up: Items, Documents, Manufacturers, EUDAMED checks, Emails received |
| **Add** | Upload a document, Import from Business Central |
| **Operator** | A collapsed block at the bottom: System status, Failed tasks, Queues & health, Playbooks, Data quality, Decisions log, Scheduler, Business Central push, API. Running the machine, not doing the job. It is there only if your login runs the machine, so you may not see it at all |

The short version: **the first three groups are your work. The Operator block
is the machine's.** Most days you only need **Today** and what it sends you to.

Six screens are not in the menu and are reached from the screen they belong
to: **Coverage gaps** and **Discovery** from the row of links on Items, the
**EUDAMED ID (SRN) queue** and **Check due** from EUDAMED checks, **Onboard a
supplier** from Playbooks, and **Weekly reports** from Today.

### The search box

Above the menu. Type a product reference, a certificate number, or a supplier
name. It searches products, documents and manufacturers at once and shows you the
first few of each.

It does not guess or correct spelling. If you get nothing, try a shorter piece of
the number.

### The counts in the menu

Four entries under **Your work** carry a number, and each is the count of the
screen it opens:

| Entry | Means |
|---|---|
| **Review** | Documents waiting for your decision |
| **Missing documents** | Items the system searched for and found nothing for |
| **Expiring** | Certificates that lapsed in the last 180 days or lapse in the next 30. One count per certificate, not per document |
| **Renewal emails** | Renewal mails written for you and not yet sent |

They load a moment after the page and refresh themselves every few seconds, so
a screen left open all morning still shows today's numbers. A dash means they
have not arrived yet.

### The health line at the bottom of the menu

One line: **● System working**, or the parts of the system that have stopped
answering, by name. If it is not the first of those, the system is only half
working — tell a developer. It is not something you can fix from these screens.

When supplier websites have timed out, the line says so and offers
**details**, which opens [Failed tasks](pages/failed.md). That one you can act
on: it is the button Today offers you too.

---

## Today

The first screen you land on, and the one to come back to. Four lists, each
with its count, one sentence and one button: documents to review, missing
documents, expiring certificates, and renewal emails to send. Under them, up to
three lines about work that failed, two of which you can retry with one press.
Then the sentence **Declarations on file for X of Y medical-device items
(Z%)**, the figure to quote to Dentalia, with the line under it saying exactly
what it counts. Last, the latest two weekly reports.

Full instructions: [Today](pages/today.md).

The machine's own dashboard is still there as
[System status](pages/status.md), in the Operator block at the bottom of the
menu. Almost nothing on it is your work.

---

## When you press something

Every button on these screens hands its work to the system and answers you with
one sentence. The sentence says what **will** happen, not that it is done —
approving a document puts the decision in the queue, and the registry catches up
within a minute:

| You pressed | It answers |
|---|---|
| **Approve** | *Approval recorded. The registry updates within a minute.* |
| **Reject** | *Rejection recorded. The document stays on file and can be reopened from its own page.* |
| **Search again** | *The system will search again for this item…* |
| **Upload** | *Document received. The system reads it…* |

Two things worth knowing about those answers:

- **"Already in progress."** means the same work was already queued — your press
  changed nothing because there was nothing to change. Pressing twice is safe
  everywhere in this system.
- **"Technical details"** under an answer opens the job number behind it. It is
  there for a developer to quote. You never need it.

---

## If a screen says something went wrong

You get a page, not a wall of code. It says what happened in one line, keeps the
menu on the left, and offers **Go to Today** so you are never stuck.

| What it says | What to do |
|---|---|
| **Page not found** | The address is wrong or out of date. Press **Go to Today** and get there through the menu |
| **You cannot open this** | That screen, or that button, is not part of your login. Nothing is broken. If it says *This action is for operators*, ask whoever runs the machine to do it |
| **Something went wrong** | Nothing you did caused it, and nothing was changed. Try again; if it keeps happening, tell a developer which screen and which button |

Whatever the page says, **nothing was written**. A refused action is a refused
action, not a half-done one.

---

## What a bad day looks like

You do not have to diagnose anything. But it helps to know which of these is
yours and which is a developer's.

| What you see | Whose problem |
|---|---|
| Lots of documents in Review | **Yours.** The system found things and needs decisions |
| Things in Missing documents | **Yours**, mostly. Upload what you find, or have it search again |
| Lines under *Failed tasks* on Today | The first two are **yours**, one button each. The third, *waiting for the developer*, is not |
| The health line naming a part of the system | A developer's. Report it |
| Coverage falling | Worth asking about. Usually a supplier stopped publishing, or a new batch of products arrived with no paperwork yet |
| A screen showing words like `C5` or `gate.apply` | Cosmetic. Ignore them — see the [glossary](glossary.md#codes-you-can-ignore) |

---

## Where to go next

- [Today](pages/today.md) — the screen you land on
- [Your daily round](01-daily-work.md) — what to do each morning
- [Review](pages/review.md) — the screen you will use most
- [Glossary](glossary.md) — every word in one place
