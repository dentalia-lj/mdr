# Coverage gaps

**In one sentence:** which items are missing which paperwork — counted, and
then listed so you can work them.

**Status:** Live. Checked against the code on 2026-09-11.

**This page is read-only.** It starts nothing and changes nothing.

---

## What this is

[Today](today.md) tells you how many medical-device items have a declaration on
file. It cannot tell you *which* articles are short, or *what* is missing from
them. This page answers both.

---

## Three questions, not one

"No declaration" means two different things, and both are worth seeing:

| Gap | Means |
|---|---|
| **No document at all** | Nothing published is linked to this article. The smallest list, and the worst |
| **No Declaration of Conformity** | No published declaration linked to this article by its item number, the supplier's article number, UDI or the supplier's coverage list. The article may still hold a certificate, an instruction leaflet, a declaration that covers its manufacturer's whole range, or one a person linked by hand |
| **No MDR or MDD document** | Nothing issued under a device regulation. A quality-system certificate says something about the manufacturer, not about this article |

An article can sit in the second list and not the third, or the other way round.
That is not a contradiction — it is the difference between "what type of paper do
we hold" and "under which law was it issued".

The **What it does hold** column is why the lists are readable: "no declaration"
reads very differently when the article holds an EC certificate than when it
holds nothing. It can even show *DoC*: that is a declaration for the
manufacturer's whole range, or one a person linked by hand, which the
acceptance metric does not count.

The **No Declaration of Conformity** count is always Today's medical-device
items minus its declarations on file. Both screens count with the same rule,
so the two numbers cannot disagree.

---

## Articles with no device class

Underneath the counts you will see a number of articles that are **not counted at
all**, because Business Central has not said whether they are medical devices.

They are named rather than hidden. We cannot say what paperwork they need until
that class is filled in, and counting them as gaps would invent work that may not
exist. If that number looks too large, it is the same problem as the one on the
[Data quality](data-quality.md) screen.

---

## What to do with a row

Click the article to see everything linked to it, or the manufacturer to see what
we hold from that supplier and to start a search or a request.

---

## See also

- [Items](items.md) — the whole catalogue rather than the gaps
- [Discovery](discovery.md) — whether anyone ever went looking for these
- [Today](today.md): the declarations sentence whose missing items are the
  **No Declaration of Conformity** list
