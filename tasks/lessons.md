# Lessons

## 2026-09-29 — Reviews that check code against my own plan inherit my plan's errors

**What happened.** The manufacturer-alias feature (branch `alias-action`) was
specified, planned and reviewed task by task. The spec and plan keyed the
certificate second-confirm on document type (EC/ISO). GATE decides the
automatic bind-every-item by coverage scope (`validate.py` C4). Three task
reviewers compared code with the plan and passed it; only the final reviewer,
reading the pipeline code, caught it. A 30-minute fix wave followed on a
"simple" feature, and Denis rightly distrusted the result.

**Rule.** When a feature's risk lives in existing pipeline behaviour, write the
spec's guard conditions FROM that code (cite file:line of the rule being
guarded), and give reviewers the goal and the pipeline code as ground truth,
never the plan alone. Also: offer the smallest safe version before letting
rulings grow the scope.

## 2026-09-30 — A mockup for a menu link was ceremony, not care

**What happened.** Denis asked to put the existing EN and SL guide files in the
app's menu, "low below down". I started building a mockup artifact because the
memory rule said every web UI change gets one first. He stopped it: the files
existed, the placement was given, and a link styled like its neighbours has
nothing for a mockup to decide.

**Rule.** A mockup is for a new screen, card, layout or visual treatment, where
seeing it changes the decision. An entry added to an existing menu or list, in
the existing style, at a place Denis named, gets one line of stated placement
and then the build.

## 2026-10-01 — I called an approved cost figure a flat fee without asking what it covered

**What happened.** Denis said Dentalia approved 40 € to re-invoice AI tokens,
then showed the Anthropic console at $35.98 for 30 days on one key. I treated
the gap as a markup and told him to label the line "pavšal po dogovoru". He
corrected it: the 40 € is the realistic cost, sized so he does not lose as
usage grows. I compared a figure he negotiated against a filtered dashboard
reading and drew a conclusion about his intent from the difference.

**Rule.** When a figure Denis agreed with a client differs from one I measured,
ask what the agreed figure covers before characterising it. A filtered reading
(one key, one window) is not the whole cost, and the label I propose for an
invoice line carries a claim about his honesty.

## 2026-10-05 - A new config key is not done until it reaches the container

**What happened.** I added `IMAP_LOGIN` to `app/config.py`, the adapter, tests
and docs, ran the full suite, and reported the change ready to deploy. Denis
asked "are you sure it is ok?". Only then did I check `docker-compose.yml`:
the worker's `environment:` block names every key one by one and compose
does not inject `.env`, so the value Denis was about to put on the server
would never have reached the worker. The suite was green because nothing
guarded `IMAP_*`.

**Rule.** A new env-read key in `app/config.py` is finished only when the
service that reads it declares it in `docker-compose.yml`, and a
`tests/test_compose_config.py` guard covers its prefix.

**How to apply.** After adding a key, grep `docker-compose.yml` for a sibling
key of the same group and add the new one beside it in every service that
runs the code. If its prefix has no `_keys_config_reads` guard, add one in the
same commit.

## 2026-10-05 - Status replies are two short lists

**What happened.** Asked what was left on the BC sync, I answered with a
paragraph per item: what I built, why the order matters, a placement proposal
with its own question, and six numbered asks. Denis: "shorter. what was done
what needs my attention".

**Rule.** A status or wrap-up reply is "Done" and "Needs you", one line per
item. Reasons, measurements and design detail go in the docs I wrote, not in
the reply.

**How to apply.** Before sending a reply that closes a chunk of work, cut every
sentence that is not an outcome or an ask. If a point needs explaining, link
the doc section where it is explained.

## 2026-10-05 - Never extract .env values with tr or cut

**What happened.** To enqueue a BC dry run on the server I pulled
`BC_BASE_URL` out of `.env` with `tr -d "\x27\""` to strip its quotes. `tr`
does not read `\x27` as a quote: it deleted every `x`, `2` and `7`, so the
job's URL was mangled and it died on all five attempts. The same pipeline
mangled the BC link key in an earlier test, and I reported a 404 on the BC
link as a real finding before finding the cause.

**Rule.** Read a configured value where the app reads it: inside the container,
from its environment (`sh -c '... $BC_BASE_URL ...'`) or via `load_config()`.
Assert the value's shape before using it.

**How to apply.** Any remote command that needs a URL, key or path from
config runs inside `docker compose exec`, and prints a masked shape check
(prefix, length) before acting on it.

## 2026-10-08 — I tested a URL bug from a URL the consumer never holds

**What happened.** I reported that the BC card's relative "Download all" link
was broken for 1.742 items with a `/` in the number, from a request to
`/item/1000/1`. Denis said the zips worked. BC's stored link comes from
`app/bc_fields.py::warehouse_url`, which encodes the slash as `%2F`, and from
that URL the relative link resolved correctly. The break was real only for a
hand-typed URL. Re-testing from BC's own URL then found the bug users would
actually hit: a 500 on every item named with Č, Š or Ž.

**Rule.** Reproduce a link bug from the exact URL the consumer holds, built by
the code that builds it, before sizing it.

**How to apply.** For anything reached from BC, build the entry URL with
`warehouse_url`, and sweep real data, not a hand-picked item, before quoting
a count.

## 2026-10-08 — Effort estimates came out as developer-day ranges

**What happened.** Asked how much the extraction-ladder options would take, I gave
"4-6 days" style ranges, then a per-block table split into code / tests / docs
hours. Denis: tests and docs are part of the work Claude Code does, and a human
estimate of 56-85 hours would have been almost the whole quoted project.

**Rule.** Effort is two columns: human hours (a developer doing it by hand, tests
and docs included) and Claude Code hours (wall-clock, tests, docs, test runs and any
verification batch included). Say what neither column covers (Denis's review,
deploy, re-extraction). Never split tests and docs into their own columns.

## 2026-10-12 — A simulation caveat stayed in an output file

**What happened.** The ladder gate comparison simulated only the confidence test; the
REF gate and VALIDATE flags were left out. The caveat was printed in `gate.md` but
reached the diagram page only as a footnote, never the research doc or my answers, which quoted 79% vs
71% production as if they were registry outcomes. Denis asked what happened with the
validator. The real outcome of the same 48 documents: 10 production, 26 filed, 10
staged; the confidence gap was 2 linked documents, not 8 points.

**Rule.** A number from a simplified model carries its simplification wherever it is
quoted: in the doc, the page and the chat answer, not only in the run's output file.
Before comparing a simulated gate to "today", read the real outcome from the registry
(document status, manual_task reasons) and say which stages the simulation skips.

## 2026-10-12 — Progress not shown during a multi-agent build

**What happened.** While several agents built tasks of the extraction-ladder plan in
parallel, I reported each agent's result but never showed the plan as a whole, and the
plan's checkboxes stayed unticked after Tasks 0 and 7 were done. Denis asked to see
progress every so often and to tick tasks as they finish.

**Rule.** In a multi-task build, keep a progress table at the top of the plan file and
tick each task's boxes in the same commit that lands it; lead every status message with
the whole table, not only the task that just changed.

## 2026-10-13 · Report an overrun when it happens, and estimate the waiting

**What happened:** Task 9 was estimated at 0.8 h. It ran 1.5 h, stopped at its cost cap, and found regressions that needed new work; I only gave a new figure (2 to 3 h) when Denis was stepping away, and he had to point out the gap himself.
**Why:** the estimate counted work, not Batch API queue time (5 to 15 min a round, 3 to 5 rounds), and the simulation that sized the cap underestimated Sonnet reads on unseen documents (29 predicted, 60 needed). I treated the cap stop and the regressions as news to relay, not as a change to the estimate.
**Rule:** when a task passes its estimate, say so in the next status message with the new figure and the reason. Estimates for paid runs include the batch waits. Run independent halves in parallel (finishing a replay while fixes are built) instead of in sequence.

## 2026-10-14 · Ticking slipped again; and a blocked step was asked for in jargon

**What happened:** (1) Task 9's progress row said "re-run done" while its three checkboxes stayed unticked; Denis found them. The 2026-10-12 rule above already covered this. (2) When the auto-mode check blocked a local step, I asked Denis to "allow the overlap step" with file names and four shell commands, while 15 ladder branches sat unmerged; he could not tell what was being asked or where the work stood.
**Why:** I updated the table row and treated that as ticking. I wrote the ask from inside the work (tool names, file names) instead of from where Denis stands (what is done, what is blocked, what one decision unblocks it).
**Rule:** before any "done" or status message, `grep -n -- '- \[ \]'` the plan and tick or annotate every box the step covered. A permission or decision ask states, in plain words: what is finished, what is blocked and why, the one thing needed from Denis, and what happens after; branch status comes from `git` in the same message.

## 2026-10-15 · A backup status left out the restore test that had passed

**What happened:** Asked to check production backups, I listed the § 5.4 restore (from a Hetzner box snapshot under `.zfs`) as "never run" and did not mention the restore from the Storage Box that passed on 2026-09-30 (decisions.md row 115, backup.md § 9). Denis read it as "no restore from the box was ever done".
**Why:** "box snapshot" (Hetzner's ZFS copy) and "snapshot on the box" (restic's) are two things with nearly the same name, and I reported only the open item, not the closed one next to it.
**Rule:** a backup status names the last passed restore test (date, source, record) before any restore that is still open, and calls the two sources "restic repository on the Storage Box" and "Hetzner's own box snapshots", never just "box snapshot".

## 2026-10-15 · A worklog billed waiting time and led with a by-hand figure

**What happened:** Asked for October's hours as "human vs Claude Code", I gave 52 h of session wall-clock and a ~280 h by-hand equivalent. Denis: two weeks part-time cannot hold that; at most 80-90 h full time, and the Haiku 5.5 work was approved at 10 h.
**Why:** I took wall-clock blocks as worked time, including agents running alone for hours and sessions idle waiting for him, and I carried the forward-estimate "human h by hand" column into a retrospective, where it reads as a billable figure.
**Rule:** worklog hours are active time: Claude's work counts only up to 30 min after Denis's last message; a session waiting for him counts only when he replied within 30 min; longer waits are dropped. Parallel sessions count once. The by-hand figure is a reference column, never the headline.

## 2026-10-15 · Estimate a paid run from the population it runs on

**What happened:** I estimated the 965-document review re-read at $7-10 from yesterday's pilots ($0.0064 per DoC). It cost $12.47: Sonnet was asked on 425 calls, because the review queue holds the hard documents (scans, low-confidence reads), not the pilot's mostly filed DoCs.
**Rule:** size a paid run from a measurement on the same population (here the Task 9 replay of this very queue, about $0.017 per document at sync price), and say which base the estimate uses.

## 2026-11-06 · Our mirror is not BC: "087 holds 0 items" was a mirror reading
- **Rule:** A count from `item_mirror` describes what INGEST kept, not what BC holds. Say "we hold 0", never "BC has 0", unless BC itself was read.
- **Why:** I told Denis CATTANI 087 held no items. BC had 99, all `NI MP`, and INGEST dropped them on purpose.
- **How to apply:** before any claim about BC's catalogue, check INGEST's filters (md_flag, skipped rows), or read BC directly.

## 2026-11-06 · Give the documented command, not an improvised one
- **Rule:** For server operations, quote the runbook or deployment.md form verbatim (Route C for the BC sync), with literal values filled in.
- **Why:** I offered a `docker compose exec ... sh -c "python ..."` form. Denis rejected it as non-standard, because the documented form already existed and he had run it before.
- **How to apply:** grep runbook.md and deployment.md for the operation first, and cite the section along with the command.

## 2026-11-09 · A pasted task list is not current state
- **Rule:** Before repeating an item from a pasted or carried-over list as still open, check it or say it is unchecked.
- **Why:** I kept listing the doc 894 CATTANI check as waiting on Denis. It was a different task and already handled.
- **How to apply:** in "still waiting on you" lists, name only items verified this session or mark them unverified.

## 2026-11-10 · Read the code or the rule before answering, every time
- **Rule:** Before stating how anything works, or that an analysis is complete, read the function bodies and the rulings it depends on. A grep hit, a function name or a line number is a lead, not evidence.
- **Why:** I told Denis a wrong-product certificate would leave its Missing documents card open, and presented the side-effect list as complete, after grepping GATE and VALIDATE without reading them. Reading them showed the card's group is fixed, reopening needed its own code, and a planned `doc_id` would have re-closed the card. Even the follow-up table cited `queue.py:251` for dead-job retries from a grep line; that line is `defer`.
- **How to apply:** for each claim in a reply, name the body I read. If it was not read, read it first; if it cannot be read, say so in those words. Never offer a design or an edge-case list from names alone.

## 2026-11-10 · Estimate Claude Code hours from measured time
- **Rule:** Base the Claude Code column on measured agent time for comparable work, and size the work to what it is.
- **Why:** I quoted 11.5 hours for a private checkup skill by rounding nine rows up to half an hour or more and pricing it like production code. The transcript showed the whole investigation, all its scripts included, took 30 agent minutes.
- **How to apply:** before quoting, read the transcript timestamps or a comparable commit, and say what the figure is based on.

## 2026-11-12 · "Commit, then I'll deploy" includes the push
- **Rule:** When Denis says he will deploy after a commit, push (or ask once, in the same message) before handing over.
- **Why:** I committed three units and wrote "no push"; he deployed, the server restarted on the old commit b7668de, and the date fix was not live.
- **How to apply:** deploy pulls `origin/main`, so check `git status -sb` for "ahead" before saying a change can be deployed.

## 2026-11-12 · Run the checks, don't list them
- **Rule:** When a check is read-only and within reach (a server SELECT, a replay over stored text), run it before replying; do not hand back a "still to check" list.
- **Why:** On the Chirana ISO typing fix I ended with two open checks I could have run myself; Denis had to say "check what is still to check, come on". Running them found seven more mistyped documents and the ligature case.
- **How to apply:** before writing "still to check", ask whether I can run it now. If a permission block stops me, retry once in its own call or name the block; otherwise run it.

## 2026-11-12 · A denial is one call, not a standing block
- **Rule:** An auto-mode denial covers the call it denied. Before saying a tool or host is "blocked", retry once in its own call; never carry an earlier denial forward as current state.
- **Why:** The first SSH to production was denied by the classifier; two turns later I wrote "SSH to the server is still blocked" without retrying, and handed Denis a command list. The next attempt connected normally. Same failure as "run the checks, don't list them".
- **How to apply:** reach production with `ssh -A dentalia` (alias in ~/.ssh/config, repo at /srv/compliance/app, compose `-f docker-compose.yml -f docker-compose.prod.yml`). Read-only queries there are mine to run; writes still need Denis.

## 2026-11-12 · Toggl entries follow the real working blocks, not a packed day
- **Rule:** Place Toggl entries on the clock where the work actually happened: merge the active spans into the day's real blocks (a short gap inside a block, such as a 40-minute pause between 11:28 and 12:11, stays inside it), round each block to 30 minutes. Do not pack the active total from the day's start with a lunch pause.
- **Why:** I computed 267 active minutes and laid them out as 10:00-12:30 and 13:00-15:00. Denis corrected it to 10:00-14:00 and 15:30-16:30: the spans were 10:19-14:21 and 15:26-16:52, and the packed layout put the second block at a time he was not working.
- **How to apply:** compute the active spans as before, then show them as clock blocks (start of first span to end of last span per block, gaps under ~45 min absorbed) and propose those times; let Denis adjust before or after creating.
