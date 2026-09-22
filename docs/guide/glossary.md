# Glossary

Every word you will meet, in one place. Split into three parts:

1. [Words from the rules](#words-from-the-rules) — the regulation's vocabulary.
2. [What you actually have to hold](#what-you-actually-have-to-hold) — which of
   those documents you are answerable for, and which are simply good to have.
3. [Words on the screen](#words-on-the-screen) — the system's own vocabulary,
   including the ones it has not yet been taught to say in plain language.

**Status:** Live. Checked against the code on 2026-09-15.

---

## Words from the rules

### MDR

The European Medical Device Regulation, **EU 2017/745**. The rulebook in force
today. Almost everything the system collects is an MDR document.

### MDD

The Medical Device Directive, **93/42/EEC**. The old rulebook, replaced by MDR.
Some products are still covered by MDD paperwork.

An MDD document and an MDR document are never treated as versions of each other.
A new MDR declaration does not replace an old MDD one — they are separate
histories, and the system keeps them separate on purpose.

### CE marking

The mark a manufacturer puts on a device to say it meets the European rules. It
is the manufacturer's claim, not ours. Your duty as a distributor is to check
the mark is there.

### Declaration of conformity — "DoC"

The manufacturer's signed statement that a device meets the rules. **This is the
document that matters most.** It is the one you are legally required to verify
has been drawn up, and it is the one the system chases hardest.

A declaration usually carries no expiry date of its own — see
[the 5-year rule](#the-5-year-rule).

### Instructions for use — "IFU"

The leaflet or manual that comes with the device. Also a document you are
required to check exists — with one exception, described
[below](#when-instructions-are-not-required).

### Notified body

An independent organisation, appointed by an EU country, that checks a
manufacturer's work. Each one has a **four-digit number**.

You do not have to hold the notified body's certificate. You do have to check
that its four-digit number appears on the declaration, where the paperwork
indicates a notified body was involved. That narrowing comes from the European
Court of Justice, case **C-10/24**, decided 4 June 2026.

### EC certificate

The certificate a notified body issues to the manufacturer. Good to hold — it is
useful evidence and useful leverage with a supplier — but **you are not
answerable for having it**. The system shows it and never marks you down for it.

### ISO 13485

The manufacturer's quality-management registration. Same standing as an EC
certificate: shown, never scored.

### Systems and procedure pack statement — "SPP"

A statement under **MDR Article 22**, for kits assembled from several CE-marked
devices. It is neither a declaration nor a certificate, and the system keeps it
as its own kind so a pack statement can never be mistaken for a declaration.

### Device class

How risky a device is considered. In rising order: **I**, **IIa**, **IIb**,
**III**.

Class I splits into four, and the split matters because it changes what you must
hold:

| Class | What it means |
|---|---|
| **I** | Plain class I. The manufacturer declares conformity alone; no notified body is involved |
| **Is** | Class I, supplied sterile. A notified body checks the sterility |
| **Im** | Class I, with a measuring function. A notified body checks the measuring |
| **Ir** | Class I, reusable surgical instrument. A notified body checks the reuse — **and the instructions that go with it** |

That last point is why Ir devices always need instructions on file, while plain
I and IIa devices sometimes do not.

This split comes from **MDR Article 52(7)**.

### REF, or product reference

The manufacturer's own article number, printed on the box. The system reads REFs
off a document to work out which of your products it covers.

Careful: your Business Central number and the supplier's number for the same
product are usually different. The system knows both.

### UDI, UDI-DI and Basic UDI-DI

**UDI** is the Unique Device Identification system — a code that identifies a
device worldwide.

- **UDI-DI** identifies one specific device model.
- **Basic UDI-DI** identifies a family of related models, and is the code a
  declaration usually quotes.

A UDI has to be assigned to the device — that is one of your four duties. The
system does not currently score it, because Business Central holds no UDIs at
all and a permanently red row teaches people to ignore the card.

### EUDAMED

The European database of medical devices. The system reads from it to
cross-check what manufacturers have registered.

### EUDAMED ID (SRN)

A manufacturer's identity in EUDAMED. **SRN** stands for Single Registration
Number, and that is what the European register calls it; the screens spell it
out as **EUDAMED ID (SRN)** the first time it appears on a page. One company
name can have more than one, which is why the system sometimes asks you to
confirm which one belongs to which supplier.

---

## What you actually have to hold

Dentalia is a **distributor**, not a manufacturer. That single fact decides
everything below.

**MDR Article 14(2)** gives a distributor four things to verify before selling a
device:

1. The device carries the CE mark and an EU declaration of conformity has been
   drawn up.
2. The device comes with the Article 10(11) information — the label and the
   instructions for use.
3. Where relevant, the importer has met its own obligations.
4. A UDI has been assigned to the device.

That is the whole list. **The notified body's certificate is not on it.**

### What the system marks you down for

Only three things:

| Row | Why |
|---|---|
| **Declaration of conformity** | Duty 1. Always required, for every class |
| **Instructions for use** | Duty 2 |
| **Notified-body number** | Duty 1, as narrowed by court case C-10/24: the four-digit number must appear where a notified body was involved |

### What is shown but never scored

| Row | Why |
|---|---|
| **EC certificate** | Not a distributor duty. Good to hold |
| **ISO 13485** | Not a distributor duty. Good to hold |

A missing EC certificate or ISO registration will never show as red. The worst it
can go is amber.

### By device class

| Class | Declaration | Instructions | Notified-body number |
|---|---|---|---|
| **I** | Required | Usually required | Not applicable |
| **Is** | Required | Usually required | Required |
| **Im** | Required | Usually required | Required |
| **Ir** | Required | **Always required** | Required |
| **IIa** | Required | Usually required | Required |
| **IIb** | Required | **Always required** | Required |
| **III** | Required | **Always required** | Required |
| *class not known* | Required | Cannot say | Cannot say |

When Business Central has not told us a product's class, the system says so
rather than guessing. The declaration duty does not depend on the class, so it
stands either way.

### When instructions are not required

**MDR Annex I, section 23.1(d)** allows a class I or IIa device to be supplied
without instructions, if it can be used safely without them and the manufacturer
has justified that in its technical documentation.

So "instructions missing" on a class I or IIa product is **amber, not red**. It
means: go and check whether that justification exists. It does not mean you are
in breach.

It never applies to Ir, IIb or III.

### The 5-year rule

A declaration of conformity carries no expiry date under MDR. That does not mean
it is good forever.

The system treats a declaration as due a fresh look **five years after it was
issued**. On screen that reads *"Due a review (5-year rule)"*. It is a prompt to
check with the supplier, not a breach.

### "Expires soon"

**30 days** before the date. That is Dentalia's own choice, made on 2026-08-18,
and it is deliberately the same clock the system uses to start chasing renewals
— so a document never reads as fine on a day the system is already chasing it.

---

## Words on the screen

### Document status

You will see these as coloured pills.

| Pill | Means | Stored as |
|---|---|---|
| **Published** | It counts. Visible everywhere | `production` |
| **Waiting for review** | Waiting for a person. Does not count yet | `staged` |
| **Rejected** | Someone decided against it. Kept on file, does not count | `rejected` |
| **Replaced** | A newer document has taken its place. Kept, no longer current | `superseded` |
| **On file** | A genuine document from a supplier we know, which covers none of the products you stock. Correct, complete, in nobody's queue | `filed` |

The **Stored as** column is what the database holds and what a filter on
[Documents](pages/documents.md) files by. You never have to type it: the filter
offers the words and files by the stored value for you.

Nothing is ever deleted. A rejected or superseded document stays on file, dated,
and can still be shown to an auditor.

### The screens, by the names in the menu

| Word | Means |
|---|---|
| **Today** | Where you land. What is waiting for you this morning, in the order to do it |
| **Review** | Documents waiting for your decision |
| **Missing documents** | What the system searched for and could not find. The office list of dead ends |
| **Expiring** | Documents whose date is close, or past |
| **Renewal emails** | Renewal requests the system has written for you to send |
| **Items** | Your products, as Business Central numbers them |
| **Documents** | Everything on file, whatever its status |
| **Manufacturers** | Your suppliers, one entry each |
| **EUDAMED checks** | What the European register says about every supplier, on one screen |
| **Emails received** | Messages the system read, and what it found attached |
| **System status** | The operator board: how the machine itself is doing. It read "Queue status" until 14 September 2026 |

### Other everyday words

| Word | Means |
|---|---|
| **Item** | One product, as Business Central holds it. Its number is labelled **Item no.** |
| **Manufacturer** | One supplier, as a single entity, however many names Business Central has for them |
| **Name in Business Central** | A name Business Central uses for that supplier. Several can point at one manufacturer |
| **Manufacturer's article no.** | The supplier's own number for a product, which is usually not the same as yours |
| **A link** | The connection between one document and one item. A document can have many |
| **A group** | Items treated as one family, because a single document usually covers all of them |
| **Coverage** | *a product group* means it covers a named family; *everything from this manufacturer* means it covers the supplier's whole range |
| **How it was matched** | Why the system tied a document to an item. Some ways are trusted enough to publish automatically; others always need a person |
| **Evidence** | For every fact the system stored, the page it read it from and the exact words. This is what you show an auditor |
| **Playbook** | What the system knows about one supplier: their website, where their documents live, how their paperwork is laid out |
| **Short name** | The lower-case, hyphenated name a playbook is filed under, like `ivoclar`. Chosen once and then left alone |
| **EUDAMED check** | A check of one manufacturer against EUDAMED. Never runs on its own |
| **Start the check** | The button that runs one. A person always presses it |
| **Check due** | The list of suppliers whose check you can start now |

### Words for how a document is matched

These are the values under the **How it was matched** column, and you will see
them in the small print. The distinction that matters: some let the system
publish on its own, others always stop for a person.

| How it was matched | Publishes on its own? |
|---|---|
| `ref-list`, `ref-item` | Yes — supplier's or your own product number, with the manufacturer already confirmed |
| `udi`, `basic-udi-di` | Yes |
| `mfr-scope` | Yes — the document covers the supplier's whole range |
| `manual` | Yes — a person decided it |
| `name-family`, `fetch-context`, `ref-catalogue` | **Never.** Always waits for a person |

### Words about the system's own work

These appear on System status, Processing, Manual and Failed. They describe the
machine, not your documents. Those four are operator screens, and a few of them
still carry the system's older spelling of a word; where they do, it is named
below so you can recognise it.

| Word | Means |
|---|---|
| **Being read** | The system holds the file and is working through it. The screen that lists these is still headed **Processing**, and its one figure still reads **documents in flight** |
| **to review** | Waiting for your decision. This is the Review screen |
| **Failed** | A task that failed repeatedly and gave up. Nothing is lost; it can be re-run. On System status the same thing is tiled as **dead jobs**, which counts every one of them including the ones already sent back |
| **pending / running / done / failed** | Stages of one piece of the system's own work |
| **Check due** | A supplier is due a EUDAMED check. It waits for you to start it. On System status the tile for it is still labelled **sweep due** |
| **ok / due / stale / never** | Whether a scheduled task has run recently enough |
| **Manual** | The operator's own list of open work items, folded under Queues & health. The office reads the part of it that is actionable as **Missing documents** |

### Words about emails

| Word | Means |
|---|---|
| **Emails received** | Messages the system read, and what it found attached |
| **Renewal emails** | Renewal requests the system has written for you to send |
| **Draft / Ready to send / Sent / Archived** | Stages of one of those drafts. Every screen that names one — Renewal emails, the draft itself, Expiry's **Chase** column and a document's **Renewal chase** — uses these four words |

**The system never sends email.** It writes the message; a person sends it and
then marks it *sent*.

### Codes you can ignore

`C5`, `C12`, `C17`, `G3`, `T0`, `T1`, `T2`, `T3`, `ref-list`, `name-family`,
migration numbers, job names with a dot in them like `gate.apply`.

These are internal labels. They are on screen because some parts of the system
were built for developers first. They never change what you should do. If a
screen shows you one and nothing else makes sense, that is a fault worth
reporting, not something you need to decode.

---

## Where to go next

- [Start here](00-getting-started.md) — logging in and finding your way around
- [Your daily round](01-daily-work.md) — what to do each morning
- [Review](pages/review.md) — the screen you will use most
