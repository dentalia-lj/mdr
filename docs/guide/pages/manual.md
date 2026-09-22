# Manual

**In one sentence:** work the system could not finish on its own, waiting for a person.

**Status:** Live. Checked against the code on 2026-08-31.

---

## Can I break anything here?

No.

- Nothing on this screen deletes anything.
- The only button that changes anything is **Resolve — retry discovery**. All it
  does is ask the system to search again for one supplier's documents. It does
  not touch any document you already hold.
- Searching or clearing your search only changes what you see on the screen.
- If a button gives you an error, nothing happened. Try again, or ask a developer.

---

## What this is

A list of everything the system tried to do on its own and could not finish.
Three different kinds of task land here, and they are not all yours to act on —
one kind is decided elsewhere, and one has no action at all yet.

## When you use it

This is the operators' list. In the daily round, office staff use
[Missing documents](missing.md) instead: it shows only the searches that
found nothing, the one kind of card that is yours to act on, with the
manufacturer's download page and a web search one click away.

## Before you start

Nothing. You do not need anything ready before opening this screen. Knowing a
manufacturer name, filename, certificate number or document number is only
useful if you want to search for one particular task.

## What you do

1. Open **Queues & health** in the **Operator** block at the bottom of the
   menu, then **Manual**.
2. To find one task, type into **Search** — manufacturer, filename, certificate
   number, UDI, or document number — and press **Filter**. Press **Clear** to
   see everything again.
3. Read each card. The heading tells you what document or product it is about;
   the text under it tells you what kind of task it is.
4. Only one kind of card asks you to act right here: if it offers
   **Resolve — retry discovery**, press it to have the system search again, or
   follow the **Upload document** link to add the file yourself.
5. If a card instead says **Open this document in Review**, click through. The
   decision happens on the [Review](review.md) screen, not here.
6. If a card has no button and no link, there is nothing to do today.

## What happens then

- Pressing **Resolve — retry discovery** asks the system to look for that
  supplier's documents again, and a line appears under the button: "The
  system will search again for this item." If the search finds something to
  try, the card leaves this list at once, before anyone knows whether it is
  the right document. If it finds nothing, the card stays until you get the
  document yourself.
- Opening a document in Review does nothing on this screen. The task here
  closes itself once you approve or reject the document there — it is not a
  separate step.
- The third kind of card never changes on its own. It stays until a developer
  builds a way to act on it.

## What can go wrong

| You see | It means | What to do | Good or bad |
|---|---|---|---|
| A card with **Resolve — retry discovery** and an **Upload document** link | The system searched everywhere for this supplier and found nothing | Press **Resolve** to try again, or get the document yourself and upload it | Needs you |
| A card that says **Open this document in Review** | A document is waiting on a decision | Click through and decide on the [Review](review.md) screen | Needs you, but not here |
| A card with no button, just a note that there is no action here yet | A task failed for good, and nobody has built a way to fix it from this screen | Nothing to do today. If it looks urgent, tell a developer | Informational only |
| No cards at all | Nothing is waiting on a person | Nothing to do | Good |
| *"No open manual task matches…"* after searching | Your search did not match any open task | Try a different word, or press **Clear** | Try again |
| An error after pressing **Resolve** | Nothing happened. The task is exactly as it was | Try again, or ask a developer | Try again |

## Words the screen uses

Each card also shows some of the system's own labels. Here is what they mean.

| The screen says | It means |
|---|---|
| **discovery-dead-end** | The "searched everywhere, found nothing" kind. Resolved here. |
| **gate-manual** | The "needs a decision" kind. Resolved on [Review](review.md), not here. |
| **mfr-binding** | A **gate-manual** task where the system also is not sure which supplier the document belongs to. |
| **dead-job-followup** | The "failed for good" kind. No action exists for it here yet. |
| **Payload** | Technical detail behind the card, in the system's own format. Open it only if a developer asks you to. |
| *"Re-enqueues `discover.group` at interactive priority."* | In plain words: pressing **Resolve** asks the system to search for this supplier's documents again, ahead of routine background work. |

## Related

- [Missing documents](missing.md): the office's list of the searches that found nothing
- [Review](review.md) — where the tasks needing a decision are actually decided
- [Failed](failed.md) — the third queue in your daily round
- [Your daily round](../01-daily-work.md): the office's morning, which uses Missing documents instead of this screen
- [Glossary](../glossary.md) — every word in one place
