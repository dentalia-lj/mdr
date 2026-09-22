# Renewal emails

**In one sentence:** renewal request emails the system has written for you — which you must send yourself.

**Status:** Partly live. Reading, editing and marking a draft's status all work
today. Whether new drafts appear here automatically is switched off by
default, so this list may show only what already exists, or nothing at all,
until a developer turns that on. Checked against the code on 2026-09-15.

---

## Can I break anything here?

No, and one fact matters more than any button on this screen: **the system
never sends email.** Every button here only changes a label: Draft, Ready to
send, Sent, Archived. None of them puts a message in anyone's inbox but yours,
and only after you send it yourself.

- **Save edit** only changes the text stored here.
- **Mark ready to send** only records that you approved the text. It does not
  send anything.
- **I have sent this** only records that you already sent it by hand. Pressing
  it does not send anything either.
- **Archive this draft** keeps the draft, marked archived. Nothing is deleted, and
  the system will not write another one for the same chase.
- If a button gives you an error, nothing happened. Try again, or ask a developer.

---

## What this is

A draft is a renewal request the system has written on your behalf, asking a
supplier for paperwork that is about to lapse, or that could not be found any
other way. **You send it. The system never does.**

## When you use it

Open **Renewal emails** once a week, after checking [Expiry](expiry.md). Also
check it whenever [Manual](manual.md) tells you the system gave up searching
for a supplier's documents — that can produce a draft here too.

## Before you start

Nothing, to read a draft. To actually send one, you need your own email
account — the draft suggests who to write to and what to say, but sending
happens outside this system, from your own mailbox.

## What you do

1. Click **Renewal emails** in the menu on the left, or **Open drafts** on
   [Today](today.md).
2. Optional: click one of the counts at the top (Draft, Ready to send, Sent,
   Archived) to see just that group.
3. Click a subject line to open one email.
4. Check the **To**, **Subject** and **Body** fields. Edit any of them while
   the email is still marked **Draft**: all three stay editable until then.
5. If the **To** field is empty, fill it in yourself. The system does not
   always have an address on file for a supplier.
6. Press **Save edit** to keep your changes.
7. When the text is right, press **Mark ready to send**.
8. Copy the text into your own email program and send it, to the address you
   checked in step 5.
9. Come back here and press **I have sent this**.
10. If you decide not to chase this one, press **Archive this draft** instead,
    at any point before you send it.

## What happens then

- **Save edit** updates the stored text only. Nothing is sent, and the email
  stays marked **Draft**.
- **Mark ready to send** locks the To, Subject and Body fields so they cannot
  change under you, and records who approved it and when. Still nothing is sent.
- **I have sent this** records that you sent the message. This is bookkeeping
  only — it does not send anything, because nothing here ever does.
- **Archive this draft** marks it archived and locks it. It stays visible, so
  the chase for this supplier stays on record.

## What can go wrong

| You see | It means | What to do | Good or bad |
|---|---|---|---|
| No drafts listed | Nobody has anything to send right now, or a developer has not switched on automatic requests yet | Ask a developer if you expected to see something here | Informational |
| The **To** field is empty | The system has no email address on file for this supplier | Fill it in yourself before marking the draft ready | Needs you |
| "Not editable once it is…" instead of the form | The email is already marked Ready to send, Sent, or Archived | Nothing to do. This is deliberate, so the text you are about to send, or already sent, cannot change under you | Good |
| An error after **Save edit** | Your edit was not saved. The Subject and Body cannot be empty, and at least one recipient is required | Fill in what is missing and try again | Needs you |
| An error after **Mark ready to send**, **I have sent this** or **Archive this draft** | Nothing happened. The draft is exactly as it was | Try again, or ask a developer | Try again |
| Manufacturer or Week shown as "—" | This email has no request record attached to it | Nothing to do, the text itself is still valid | Informational |

## Words the screen uses

| The screen says | It means |
|---|---|
| **Draft** | Written by the system, not yet approved |
| **Ready to send** | You approved the text. Still not sent |
| **Sent** | You told the system you sent it by hand |
| **Archived** | You decided not to send this one. Kept, not deleted |
| **Week** | The week this one covers, for example *Week 34 (17–23 Aug)*. One email per manufacturer per week |
| **What it asks:** First ask | A fresh request, nothing it lists has lapsed yet |
| **What it asks:** Reminder | A more urgent tone: either a repeat, or something it lists has already lapsed |
| **Why:** expiry | A document is due to lapse soon |
| **Why:** discovery-exhausted | The system searched everywhere and found nothing, so it is asking the supplier directly |
| **Technical details** | The email's own number, its stored state and where this request stands. Nothing in there needs a decision from you |

## Related

- [Expiry](expiry.md) — what triggers most drafts
- [Manual](manual.md) — the other trigger, when discovery gives up
- [Documents](documents.md) — the documents one email asks for
- [Your daily round](../01-daily-work.md) — where Renewal emails fits in your week
- [Glossary](../glossary.md) — every word in one place
