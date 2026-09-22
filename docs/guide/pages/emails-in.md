# Emails received

**In one sentence:** what has arrived by email, and what the system found attached.

**Status:** Partly live. The screen itself works and shows every message it is
given accurately. Whether it is given anything is a separate question:
automatic mailbox checking is switched off by default, and no mailbox is
connected today, so this list may stay empty until a developer turns it on.
Checked against the code on 2026-09-15.

---

This screen is read-only. There is nothing to press that changes anything —
every clickable thing on it is a link that takes you to another screen.

---

## What this is

A record of inbound email the system has looked at: who it was from, what it
seemed to be about, and whether a usable document came attached.

## When you use it

Open **Emails received** once a week, mostly to confirm a supplier's reply was
picked up.

## Before you start

Nothing. This is a read-only list — open it and read.

## What you do

There is nothing to do except read. If a document is shown with a coloured
status next to it, click through to see it — under [Documents](documents.md)
if it already counts, or on [Review](review.md) if it is still waiting on a
decision.

## What happens then

Nothing changes on this screen by reading it. Following a document link takes
you to that document; nothing on this page itself is affected either way.

## What can go wrong

| You see | It means | What to do | Good or bad |
|---|---|---|---|
| No emails at all | Either nothing has arrived, or nobody has switched on automatic mailbox checking yet | Ask a developer if you expected mail here | Informational |
| A short coloured tag and a one-line note under the subject | A machine-written note on what the message seemed to be about | Read it as a hint only, never as proof. Open the actual attachment, or ask the supplier again, before relying on it | Informational |
| No note under the subject at all | The message had no body worth summarising | Nothing to do | Informational |
| *"Reply to request #18, IVOCLAR"* under the subject | The message answers that renewal request. Every draft for a request carries its reference in the subject, for example `[DENT-18]`, and the supplier's reply kept it. Click it to open that request's draft | Nothing to do | Good |
| The same line ending *"(matched by sender address)"* | The subject carried no reference, so the system matched the sender's address to the one supplier with an open request. A likely match, not a certain one | Check that the message really is about that request before relying on it | Informational |
| *"summary failed — the body was read but not compressed"* | The note could not be produced this time | Open the message's attachment directly, if it has one | Informational |
| *"body below the configured summary floor"* | The message was too short to be worth summarising | Nothing to do | Informational |
| **Published**, **Waiting for review**, **On file** or **Replaced** next to an attachment | It became a document, and the word says where it stands. Click the document name to see it | Nothing to do | Good |
| **skipped**, with a short reason | The attachment was not a compliance document — a safety data sheet, an invoice, or a file the system could not open | Nothing to do, unless you believe it should have counted — then tell a developer | Usually fine |
| **Being read** | The system has the file and is still reading it | Check back later | Normal |
| "— already held via…" note under an attachment | This exact file arrived by another route before this email did | Nothing to do. The email confirmed a document you already had, it did not produce a new one | Informational |

## Words the screen uses

| The screen says | It means |
|---|---|
| **sends-documents** | The machine's guess: this message is delivering paperwork |
| **requests-info** | The machine's guess: this message is asking us for something |
| **acknowledges** | The machine's guess: a reply confirming receipt, a thank-you, an out-of-office |
| **other** | The machine could not place the message in any of the above |
| The one-line note itself | Written once, automatically, when the message arrived. The original email text is never kept — only this short note |
| "Written for us when the message arrived" | The note is ours, not the sender's. Which program wrote it is under **Technical details** under the note |
| **Already on file** | This exact file was already held from somewhere else. Nothing new to do |
| **Technical details** | Open it for the name of the program that wrote the note. Nothing in there needs a decision from you |

## Related

- [Documents](documents.md) — where a usable attachment ends up
- [Review](review.md) — if the document is still waiting on a decision
- [Your daily round](../01-daily-work.md) — where Emails received fits in your week
- [Glossary](../glossary.md) — every word in one place
