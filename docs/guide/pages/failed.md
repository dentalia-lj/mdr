# Failed

**In one sentence:** work the system tried several times and could not finish,
sorted by what you can do about it.

**Status:** Live. Checked against the code on 2026-09-11.

---

## Can I break anything here?

No.

- Nothing here is deleted. A failed task stays on record even after it is
  sorted out.
- **Try the N again** and **Search again for these N** only ask the system to do
  work again. They do not change any document.
- Pressing a button twice does no harm. Work that is already waiting in the
  queue is not added a second time.
- If a button gives you an error, nothing happened. Try again, or ask a developer.

---

## What this is

A list of tasks the system tried repeatedly and could not complete, in three
sections:

| Section | What went wrong | What you can do |
|---|---|---|
| **Websites that took too long** | A supplier's website did not answer in time | Try again. It usually works |
| **Addresses that no longer work** | The web address the system had for a document is gone: the page no longer exists, or the website itself cannot be found | Search again. The system looks for the document's new address |
| **For the developer** | Something in the system itself needs fixing | Nothing. Retrying will not help until the cause is fixed |

Each heading shows how many tasks still need someone. A task you have already
sent back stops counting, and shows as **trying again** or **searching again**
until that work finishes.

## When you use it

Open **Failed** as the third step of your daily round, after Review and
Missing documents.

## Before you start

Nothing. This screen needs no preparation.

## What you do

1. Click **Failed tasks** in the **Operator** block at the bottom of the menu.
2. Under **Websites that took too long**, press **Try the N again**. The button
   names how many tasks it covers. Your browser asks you to confirm; press
   **OK**.
3. Under **Addresses that no longer work**, press **Search again for these N**.
   The confirmation also says how many searches it will start. Press **OK**.
4. Leave **For the developer** alone. It has no buttons for you. If you do see
   **Re-run** buttons under its **Show technical details**, your login is set up
   as an operator's. You do not need them for your daily round.
5. Check back later. A task that worked disappears. A task that fails again
   comes back into its section.

## What happens then

- After **Try the N again**, the tasks go back into the queue straight away. The
  section's count drops to zero, and the page shows them as **trying again**.
  They run ahead of routine background work.
- After **Search again**, the system searches for the documents of each item
  again, the same way it did the first time. What it finds goes through the
  usual checks, so it may turn up in Review. If it finds nothing, the item
  appears under [Missing documents](missing.md).
- A task under **For the developer** is waiting for a fix to the system. It is
  also listed under Manual, as a task that failed for good.

## What can go wrong

| You see | It means | What to do | Good or bad |
|---|---|---|---|
| Every section shows (0) | Nothing is waiting on anyone | Nothing to do | Good |
| **trying again** or **searching again** under a section | Work you sent back is still running | Nothing. Check back later | Normal |
| A website comes back under **Websites that took too long** after you tried it again | The site is still slow or down | Try once more the next day. If it keeps coming back, tell a developer and name the website | Needs a developer if it repeats |
| An address comes back under **Addresses that no longer work** after a search | The search found the same dead address again | Tell a developer and name the website | Needs a developer |
| *"…not linked to an item, so there is nothing to search for"* | The failed address belongs to no item the system knows, so there is no **Search again** for it | Tell a developer | Needs a developer |
| *"Already in progress"* or *"Nothing new queued"* after pressing a button | That work is already in the queue or running. Nothing was added twice | Nothing. Check back later | Normal |
| An error message after pressing a button | Nothing happened | Try again, or ask a developer | Try again |

## Words the screen uses

| The screen says | It means |
|---|---|
| **Page no longer exists** | The website is there, but the page with the document has gone. The website answered "404" or "410" |
| **Website not found** | The website's name no longer exists, so there is nothing to connect to |
| The names under a heading (for example `device.report`) | The websites involved, the most frequent first. After four, the rest are counted as "+N more" |
| **trying again** | Tasks you sent back that are still running. They do not count until they finish |
| **searching again** | Addresses whose item is being searched for again |
| **Show technical details** | The individual tasks and their error messages, for a developer. Open it only if asked to |
| **Re-run all…** and **Re-run**, inside the developer section's technical details | Operator controls. Only operator logins see them, and only they may use them. **Try the N again** and **Search again** above are yours |
| *"…interactive priority"* | This retry jumps ahead of routine background work, so it runs sooner |

## Related

- [Missing documents](missing.md): the second queue in your daily round
- [Review](review.md) — the first, and the one that matters most
- [Your daily round](../01-daily-work.md) — where Failed fits in your morning
- [Glossary](../glossary.md) — every word in one place, including the codes on this screen
