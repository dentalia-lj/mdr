# Playbooks

**In one sentence:** what the system knows about one supplier: where their
documents live, and how it reads their paperwork.

**Status:** Live. Checked against the code on 2026-09-11.

---

## Can I break anything here?

Some fields here are safe to change freely. A few are not — read this before you
touch anything beyond the ones marked safe.

- Nothing here deletes a document, and nothing here is sent to a supplier.
- Every save is recorded as a new numbered version. Nothing is overwritten — you
  can always go back with **Restore**.
- **Safe to change freely:** the domains list, the portal document sources
  (pages a person opens by hand), the date and document-type phrases, and the
  search order. Getting one of these wrong at worst means the system searches
  less well — it never sends a request anywhere by itself because of these, and
  it never publishes a wrong document because of them.
- **Safe, and worth using first:** **Probe** under *Crawl recipes*. It opens
  one page you name, counts the links on it and shows you a sample. It saves
  nothing, downloads no documents and changes no settings — it is how you check
  a recipe is right *before* you save it. It does ask the supplier's own server
  for that one page, so it waits its turn and it stops if that site's rules say
  we may not look.
- **Change with care:** a saved **crawl recipe** turns a page that *lists*
  documents into every document on it — so a recipe that is too broad has the
  system fetching hundreds of files that are not compliance paperwork. Probe
  first, look at what "reads as" nothing, and narrow the pattern until it is
  mostly documents. The "direct" document sources are different again — every
  address listed there is fetched by the system automatically, with nobody
  checking first. A wrong one is a real request sent to somebody's server. So is
  ticking the box to skip a supplier's whole backlog of old paperwork — a
  reason is required, because months later nobody will remember why. So are the
  extraction hints — they are the only playbook text an AI model actually reads.
- **Not editable here at all:** the supplier's name (that is a confirmed action
  on its own [manufacturer page](manufacturers.md)) and its other known names.
  One wrong alternative name could pull another supplier's certificates onto
  this one's products, which is why there is deliberately no editor for it here.
- If a save is refused, nothing was written. Fix what it says and try again, or
  ask a developer.

---

## What this is

Per-supplier settings that steer how the system looks for a supplier's paperwork
and reads it once found: which websites to search, which pages or files to
fetch, which words on the page mean "declaration" or a particular date, and
short notes an AI model reads alongside each document from that supplier.

Every playbook keeps a change history further down its page, with who changed
what and why, and a button to bring back an earlier version.

---

## When you use it

- A new supplier has just been added and needs a playbook before the
  system can go looking for its paperwork.
- The system keeps missing an obvious document for one supplier, or is reading
  its dates or document types wrong.
- You want to see, or bring back, an earlier version of a supplier's settings.

---

## Before you start

The supplier must already have a page under [Manufacturers](manufacturers.md).
If nobody has started a playbook for it yet, do that from the supplier's own
page first.

If a playbook has not yet been brought fully into the system, this page shows
it read-only, with every field disabled and a message saying so.

---

## What you do

1. Click **Playbooks** in the menu, or open one from a supplier's own page.
2. Click a supplier's short name to open its settings.
3. Check **Identity** for the supplier's known names and codes — these are not
   editable here.
4. If it appears, use **Claim another BC code** to attach one more Business
   Central code to this supplier. The list holds every code Business Central
   knows and not claimed by another supplier's playbook, starting on
   **Choose a code…** so you cannot claim one by accident; type into the box
   above it to narrow the list — a code or part of a supplier's name both
   work, and the line underneath says how many are left. Clear the box to see
   the whole list again. Pick one and press **Claim it**: it tells you the
   code and who holds it today, if anyone, and asks you to confirm before it
   moves. Press **Yes, claim it** to go ahead, or **Cancel** to back out
   without changing anything.
5. Under **Official domains**, list the supplier's own websites, one per line.
6. Under **Document sources — portals**, list pages a person would open by hand
   to find documents. Under **Document sources — direct**, list exact web
   addresses the system should fetch automatically — only put a real document
   file there, never a page that just lists documents.
7. Tick **Skip this manufacturer's corpus folder** only if you have agreed with
   someone that this supplier's old paperwork should not be scanned, and say
   why in the box next to it.
8. Under **Extraction hints**, add a short note for a specific field if the
   system keeps misreading something for this supplier.
9. Under **Date labels** and **Document-type markers**, add the exact words
   this supplier prints for a date or a document type, one per line.
10. Under **Discovery ladder**, tick which search methods to try and in what
    order — or leave all unticked to use the system's own default order.
11. Type a short note saying what you changed and why, then press **Save**.
12. To undo a change, find the version you want under **History** and press
    **Restore**.

To teach the system a supplier's whole download library at once, use **Crawl
recipes**, near the bottom of the page:

1. Paste the address of the page that *lists* the supplier's documents into
   **Library page** — the list, not one document.
2. Leave **Link pattern** as `\.pdf$` unless you know better. It decides which
   links on that page count.
3. Leave **Other hosts** empty for now. It matters only when a supplier lists
   its documents on one address and serves the files from another — see step 6.
4. Optionally, under **Type rules**, write one `word=TYPE` per line — the word
   is any part of the address, and `TYPE` is one of DoC, EC, IFU, ISO. Writing
   a rule with nothing after the `=` means "count these but never file them",
   which is how you keep a supplier's safety data sheets out of the register.
5. Press **Probe** and wait. It usually takes a second or two; a page the
   system has to open in a browser takes about half a minute.
6. Read the result from the top: first whether we were allowed to look, then
   how many links matched, then a sample of about twenty with what each one
   *reads as*. If the result says links were **off-host** — they matched but
   point somewhere other than this site — that is the case **Other hosts** is
   for: open one of those documents in your browser, copy the part of the
   address between `https://` and the next `/`, put it on its own line under
   **Other hosts**, and probe again.
7. If the pattern is too broad — a large **unclassified** count, or a sample
   full of price lists — change it above and press **Probe again**.
8. When the sample looks right, type a short note and press **Save this
   recipe**.

---

## What happens then

- Saving writes a new numbered version straightaway, and the page shows it.
- A save without a note is refused — the note is what makes the history useful
  later.
- If someone else saved this supplier's settings while your page was open, your
  save is refused rather than silently overwriting theirs. Reload the page and
  make your change again.
- **Restore** does not delete anything either — it writes the old settings
  forward as a brand new version, so both the mistake and the fix stay visible
  in the history.
- Saving a crawl recipe **stores** it; it does not run it. The system uses it
  the next time it goes looking for that supplier's documents.
- Saving a recipe for a library page you already have a recipe for **replaces**
  that one rather than adding a second.
- Claiming a Business Central code writes nothing until you confirm; once you
  do, it takes effect immediately. Codes cannot be removed from this screen —
  that is deliberately left to a developer.

---

## What can go wrong

| You see | It means | What to do | Good or bad |
|---|---|---|---|
| *a note is required: say what you changed and why* | You pressed Save without typing a reason | Type a short reason and save again | Neutral |
| A message saying this supplier's settings changed while your form was open | Someone else saved this supplier's playbook after you opened the page | Reload the page and make your change again | Neutral — no work is lost, just needs redoing |
| A refusal naming a web address as not one the system may fetch automatically | That address is on a list of sites the system must never request directly | Move it into Document sources — portals instead, so a person opens it by hand | Bad if repeated — use the portal list |
| *This playbook is not in the database yet, so it cannot be edited* | Nobody has fully brought this supplier's settings into the system yet | Ask a developer | Neutral |
| A message saying a code already belongs to a different supplier's playbook | That code is claimed elsewhere | Confirm which supplier really issues that code before doing anything | Bad if wrong — investigate first |
| **Refused.** … Nothing was fetched | That site's own rules say we may not read that page automatically | Nothing to fix — use Document sources — portals so a person opens it by hand | Neutral — this is the system obeying a rule, not a fault |
| **Could not ask** | We never reached that site's `robots.txt`, so we had no permission to go further. Our side, not a refusal — a DNS failure or a dropped connection | Probe again. If it keeps happening, tell a developer | Neutral — worth one retry |
| **Could not read it** | We were allowed to look, but the site did not answer | Check the address in your own browser, then probe again | Neutral — usually the site, briefly |
| **Nothing matched**, but the page had links on it | Either the pattern is wrong, or this library holds its addresses in a way the system cannot follow — some sites build the list with JavaScript and put each address in a table row rather than in a link | Open the page yourself and check whether the documents are ordinary links before you change the pattern | Neutral |
| **Nothing matched** and the page had no links at all | The list is probably drawn by JavaScript, or this is not the library page | Try the actual library page; if it is right, ask a developer | Neutral |
| A large **unclassified** count | The pattern is catching files that are not compliance documents | Narrow the pattern, or add type rules | Bad if saved as-is — the system would fetch all of them |
| A count of links that are **off-host** | The supplier lists on one address and serves the files from another — common, and not a fault | Put that other address under **Other hosts**, one host per line, then probe again | Bad if ignored — those are usually the documents you came for |
| *…carries a path. An extra host is matched whole* | Under **Other hosts** you pasted a whole document address rather than just the host | Keep only the part between `https://` and the next `/`; the message shows you what to write | Neutral — nothing was saved |
| A line saying links **were dropped at the cap** | The library has more matching links than the cap allows | Raise **Max links**, or narrow the pattern | Bad if ignored — you would only get part of the library |
| *This playbook is not in the database yet, so there is nothing to save into* | You probed a supplier whose settings are not fully in the system | The probe result is still valid — ask a developer to import the playbook first | Neutral |

---

## Words the screen uses

| The screen says | It means |
|---|---|
| **Playbook** | Everything the system knows about this supplier |
| **Aliases** | Every other name this supplier is known by. Not editable here |
| **BC codes** | The Business Central supplier codes this playbook claims |
| **Revision** | A numbered, dated version of this supplier's settings |
| **Raw JSON** | The settings exactly as the system stores them — for a developer to read, not something to edit here |
| **Crawl recipe** | A saved instruction for turning one page that lists documents into every document on it |
| **Probe** | A look at that page that reports what it found and changes nothing |
| **Library page** | The supplier's page that lists its documents — not a document itself |
| **Link pattern** | Which links on that page count. `\.pdf$` means "addresses ending in .pdf" |
| **Other hosts** | Extra addresses the documents may be served from, when they are not on the library page's own site. One per line, host only, no `/…` after it |
| **Reads as** | What a link *looks like* from its address alone. A guess, never a reading of the file |
| **Unclassified** | Links that matched but that no type rule recognised. Always shown, never hidden |

---

## Related

- [Onboard a supplier](onboarding.md) — the guided way to write a first
  playbook, linked from the top of this screen
- [Manufacturers](manufacturers.md) — where a new supplier gets its first playbook
- [Documents](documents.md) — see what the system has actually found for this supplier
- [Glossary](../glossary.md) — every word in one place
