# EUDAMED checks

**In one sentence:** what the European register says about all your suppliers on
one screen, instead of opening them one at a time.

**Status:** Live. Checked against the code on 2026-09-15.

**The only thing this page starts is a check**, on the rows where one is
already due. It writes no document and sends nothing.

---

## What this is

EUDAMED is the EU's own register of medical devices and the certificates behind
them. The system keeps a copy of what it says about your suppliers, and the
supplier's own page has always shown two things about that copy: what changed
since the last check, and which registered devices you hold no declaration for.

This page is those two things for every supplier at once. With more than 380
suppliers, the per-supplier version answers "what is new" only if you open all
of them.

It is **EUDAMED checks** in the menu, under Records. Two lists that used to be
menu entries of their own are reached from the row of links under its heading:
the **EUDAMED ID (SRN) queue**, where you confirm which EUDAMED identity belongs
to which supplier, and **Check due**, the suppliers whose check you can start
now. Both are described on [Manufacturers](manufacturers.md).

---

## Reading a row

| Column | Means |
|---|---|
| **New devices** | Devices EUDAMED has registered for this supplier that were not there at the last check you looked at |
| **Status changes** | Registrations whose state changed — a certificate suspended or withdrawn shows up here |
| **Missing** | Device families EUDAMED registers for which you hold no declaration at all |
| **Waiting for review** | Found, but waiting for your decision on the Review screen |
| **Covered** | Device families where you already hold the paperwork |
| **Last checked** | When this supplier was last checked. Blank means **never checked**, which is not the same as "nothing to find" |

A dash means zero. **New devices** and **Status changes** are counted against the
last check you actually looked at, so a row stops asking for attention once you
have been through it — not merely because time passed.

---

## What to do about a row

Each row is a link to the supplier. One action is on the row itself, the other
lives where it belongs:

- **Start the check** appears only on rows where a check is already due. A
  check is a real request to a European service, so it never starts by itself
  and a person always presses the button — and the browser asks you to confirm
  before it goes. A supplier whose identifier nobody has confirmed cannot be
  checked at all — the button is there but greyed out, and the reason is in its
  tooltip. **Check due** lists the same rows on their own if you would rather
  work through just those.
- To ask a supplier for declarations you have never had, open their page and use
  **Draft request**. It prepares a letter; it never sends one.

---

## When it looks empty

A blank or nearly blank page is the honest state on a new installation: nothing
has been checked yet. Suppliers appear here as their first check runs. If a
supplier you expect is absent entirely, the system has no EUDAMED registration
linked to it yet — that starts at the **EUDAMED ID (SRN) queue**, linked from
the top of this page.

---

## See also

- [Manufacturers](manufacturers.md) — one supplier at a time, with the buttons
- [Documents](documents.md) — what you actually hold
- [Expiry](expiry.md) — what lapses, including certificates EUDAMED lists
