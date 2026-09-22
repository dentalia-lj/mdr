# Weekly reports

**In one sentence:** what the system reported each week, kept so you can read
last month's as easily as this week's.

**Status:** Live. Checked against the code on 2026-09-15.

**This page is read-only.** Nothing is sent from it.

---

## What this is

Once a week the system takes stock: what has already lapsed, what is about to,
and how the processing queue is doing. That report used to exist only inside the
job that produced it — you could see the most recent one on the
[Scheduler](scheduler.md) screen, and older ones nowhere at all.

Now each week is written to a file and kept. This page lists them newest
first, each named as the week it covers ("Week 37 (7–13 Sep)"), and
[Today](today.md) links the latest two under the same names, which is how you
reach this screen.

---

## Reading one

Each report carries the week it covers and when it was generated, then one
line with the two counts: how many documents expire in the next 30 days, and
how many have already expired. Under that is one plain line about the system
itself: how many of its tasks have failed. That line is for a developer, not a
compliance question.

Then two tables. **Already expired** lists every published document whose
date has passed, however long ago. **Expiring in the next 30 days** lists the
ones coming up. The 30 days is the same window the Expiry screen, the menu
count and Today use.

Each row names the document's manufacturer and its date, and the document
links to its own page. Open a report and you can print it or save it like any
other page.

The reports for weeks W36 and W37, written before this fix was deployed, show
empty Manufacturer and date columns. That was a fault in how the report was written, not missing data:
the information was there, and newer reports show it.

---

## What it does not do

It does not email anyone. The system writes drafts and reports; a person sends
them. If you want to forward a report, open it and send it yourself.

---

## If the list is empty

Either no report has run yet — the first appears after the weekly job runs — or
this machine has not been told where to keep them, in which case the page says
so and a developer needs to set it.

---

## See also

- [Scheduler](scheduler.md) — when the weekly report runs, and whether it ran
- [Expiry](expiry.md) — the live version of what the report describes
