# Acceptance criteria and findings — the standing register

**What this is.** One permanent list of the nine acceptance criteria (ground truth
§9, gap G15) and every finding the readiness audits have raised, each with what
closes it. Tick a box when the thing is actually done, and write the date.

**The rules that make it worth keeping.**

- **Numbers are permanent.** F3 is F3 for life. A closed finding keeps its number
  and stays on the page, ticked; a wrong one is marked WITHDRAWN in place. Nothing
  is ever deleted, and a new finding takes the next free number (F45 next).
- **A tick means verified, not believed.** Date it and name the evidence —
  a commit, a query, a route. `/readiness-audit` re-measures rather than
  re-reads, and this file is what it reconciles against.
- **This page points; it does not duplicate.** Rulings live in
  [decisions.md](decisions.md), status in [PHASES.md](../PHASES.md), measurements
  in [state/](state/), and the retrospective of a closed finding in
  [build-log.md](build-log.md). Each entry below carries what is true *now* and a
  pointer to where the argument is written out.
- **Each measurement appears once.** Where two entries share a reading, one holds
  it and the other points at it. A number printed twice drifts twice.

Readings below were measured **2026-09-16** unless the entry says otherwise.

---

## Part 1 — Acceptance criteria (G15)

Eight of nine are met. AC8 is the single failure, and it is one defect.
Targets and their rationale: [decisions.md](decisions.md), rows of 2026-09-11 and
2026-09-14.

- [x] **AC1 — article-level coverage.** Target **33%** of BC-flagged device items
      holding a production DoC linked at article level (`ref-list`, `ref-item`,
      `basic-udi-di`, `map-supplier`). **Met: 1.571 of 4.265 = 36,8%** (it read
      1.460 = 34,2% on 2026-09-14, before the F23 repair returned 111 items whose
      declaration was hidden behind a wrong supersession). Residue: F35.
- [x] **AC2 — complete evidence on every auto-written value.** Structural,
      pass/fail. **Met: 7.562 evidence rows, none missing `archive_url`,
      `verbatim` or `tier`.**
- [x] **AC3 — audited error rate.** **Met by construction** — the rate was driven
      down by fixing its causes one at a time, each with a test, and the 200-link
      sample is explicitly not to be built. One consequence stays open as F33.
- [x] **AC4 — manufacturer-scope binding.** One human binding, N derived links, no
      per-group staging flood. **Met: 5.948 production links from 12 documents on
      14 human bindings** (2026-09-14).
- [x] **AC5 — re-run idempotency.** A second cycle over unchanged content emits 0
      jobs and spends about €0. **Met at fixture scale**, and re-proven through the
      real queue 2026-09-15 by
      `test_chain.py::test_a_second_drain_over_unchanged_content_moves_nothing`
      (F20).
- [x] **AC6 — discovery reach.** **Re-based and met**: the share of BC-flagged
      device groups holding a production document by any route. **1.391 of 1.396 =
      99,6%** (2026-09-14). The old whole-catalogue reading (227 of 8.208) is
      retired and must not be re-quoted.
- [x] **AC7 — sweep cost inside the envelope.** **Met: $42,02 over 1.753 calls**
      (2026-09-14), against a €10-40/month steady-state target.
- [ ] **AC8 — invariant integrity.** Three clauses, each must be zero.
      - [x] No `name-family` / `fetch-context` link written as `production` — **0**,
            enforced by a DB CHECK, not only by code.
      - [x] No downgrades — **0**.
      - [ ] Supersession only within an identical coverage subject. **The registry
            holds zero superseded documents** — the four wrong ones were undone
            2026-09-16 — but the RULE that made them is unchanged and reproduces on
            the next candidate pair. **This box and F23 tick together, and neither
            ticks on a repair alone.**
- [x] **AC9 — staging burden stays reviewable.** Measure-only, no target set, so it
      cannot fail. The burden is real and is counted in F7.

---

## Part 2 — Findings

### Blocked on Denis

- [ ] **F23 — a newer declaration naming fewer articles retires a wider one
      outright.** The group is the wrong coverage subject 68% of the time (41
      candidate pairs, 28 sharing no article at all). **Ruled per-item 2026-09-14,
      clarified 2026-09-15** — decisions.md rows of both dates.
      **Spec written and awaiting review:**
      [`2026-09-15-per-item-currency-design.md`](superpowers/specs/2026-09-15-per-item-currency-design.md)
      — latest-wins computed in the view, the persisted link-status variant
      recorded there as the rejected option, the 41 pairs as the regression
      fixture, three open questions.
      **The repair is done, ahead of the mechanism** (2026-09-16, seven
      `gate.apply` jobs, 14 audit rows, so invariant 1 holds): three seeded test
      files rejected, four real documents restored, AC1 1.460 → 1.571. **What is
      still open is the rule.** Nothing is built.
- [ ] **F2 — one supplier holds 95,3% of the declaration gap, and it is the only
      route above 40%.** 2.694 flagged device items hold no article-level
      declaration; **CARL MARTIN is 2.567 of them**, everything else totals 127
      (KOMET 56, GC 40, IVOCLAR 16, RENFERT 6). Without that supplier the ceiling
      is 1.698 of 4.265 = **39,8%**. Route ruled 2026-09-14: email first,
      automatic retrieval second, manual last.
      - [x] The gap-request letter exists — draft 26, 70 Basic UDI-DI groups
            covering 2.389 articles, three of our own article numbers under each.
      - [x] A recipient exists: `info@carlmartin.de`, authored in
            [`playbooks/carl-martin.json`](../playbooks/carl-martin.json) and
            written to `manufacturer.contact_emails`.
      - [ ] **The letter is sent by a person from `mdr@dentalia.si`.**
      - [ ] The return path is connected: `IMAP_HOST` / `IMAP_USER` are empty and
            `SCHEDULER_EMAIL_POLL_ENABLED=false`, so a reply is picked up by
            nobody. Until then attachments arrive through `imports/` or the Upload
            form by hand.
      - [ ] A decision on the automatic rung, worth taking only after one real
            reply: their two declarations are scans whose vision read returned no
            article list, and the older states coverage as ranges (`1-9999`) the
            REF gate cannot match.
- [ ] **F10 — one shared login; every decision is signed `admin`.** The ruled
      operator split exists as a mechanism and nothing uses it. **Resolution:** one
      Caddy credential per person plus the operator list. **Needs the names.**
- [ ] **F36 — onboarding suggests no library URL, cannot author pagination, and
      does not support portals.** The probe must paginate before the form can offer
      it. **Deferred by Denis.**

### Mine to build

- [ ] **F31 — a supplier reply closes nothing; four chases are stopped for good.**
      **Ruled 2026-09-16: a reply pauses the chase and puts it in front of a
      person; it never closes one** (decisions.md, same date, with the invariant-12
      reason). **To build:** a reply linked to a live `renewal_request` stops the
      reminder ladder and marks the request as waiting on a person; the four
      chases stopped at the escalation cap are then re-armed.
- [ ] **F33 — model confidence is never calibrated.** **Ruled 2026-09-16: keep the
      mechanism, ship every factor at 1,0, and say on the page that confidence is
      the model's own number and uncalibrated** (decisions.md, same date — the
      wording is not optional). **To build:** the config field, the factors, and
      the sentence.
- [ ] **F19 — 53 GC declarations have no issue date.** **Resolution:** author
      `date_labels` in GC's playbook (the mechanism has existed since 2026-08-19)
      and re-extract. 32 of the 53 carry the date in text; 21 need the vision tier.
- [ ] **F35 — the board prints AC1's number without its 33% target**
      ([`_ui.html:40`](../web/templates/_ui.html)), so the page cannot show pass or
      fail. The AC3 half of this finding closed with the 2026-09-14 ruling.

### Another session's

- [ ] **F11 — five operator screens still print shell commands.** **Owner: the
      office-UI session**, told 2026-09-15. Reopened by Denis that day — *"this is
      incorrect, office-ui must solve this"* — which supersedes the office-UI
      spec § 11 ruling that operator screens were deferred.
      Left: `_vendor_preview`, `playbook_detail`, `scheduler`, `_probe`,
      `playbooks` (measured with the finding's own
      `grep -l 'docker compose\|python -m app' web/templates/*.html`). The sharpest
      is `scheduler.html`, which tells the reader to run `docker compose up -d`
      when the worker is stopped. The office half is done: [`web/words.py`](../web/words.py)
      landed on 2026-09-14 and the receipt now carries the job number behind a
      disclosure.

### Waiting on someone outside

- [ ] **F7 — the review queue has no named reviewer.** **512 manual tasks, 244
      documents and 443 links open; 13 decisions ever.** **No code fix exists** — it
      needs a reviewer, or a narrower discovery rate. This is the one place the review burden is counted;
      AC9 and F15 point here.
- [ ] **F15 — automated discovery produces review debt, not coverage:** 13
      documents since 2026-09-04, every one staged. **Resolution:** hold the rate
      where it is until someone reviews, which is F7.
- [ ] **F8 — Business Central is unreachable in both directions.** The rule, the
      `bc.push` stage, the ledger, the buttons and the cron all shipped 2026-09-07.
      What is left is not code: external access, and a decision to set
      `bc.write_enabled`.
- [ ] **F26 — the pipeline has no host of its own.** Waiting on Dentalia IT.
- [ ] **F18 — 357 of 384 manufacturers have no trusted SRN**, and the probe that
      could find one has never run. 15 SRNs await a click.
- [ ] **F16 — alerting cannot report a dead database or a dead worker.** Needs an
      external account for a dead-man's switch.
- [ ] **F4 — eight of ten crons have never put a job on the queue.** Six are off on
      purpose until the environment is real; this closes with F26 and F8.
- [ ] **F1 — nothing backs up the registry or the archive.** Ruled **not scheduled
      work** (2026-09-03): a disclosed standing operational risk, recorded so it is
      never mistaken for work in progress. **Do not re-file it as engineering work.**

### No viable fix known

- [ ] **F17 — no catalogue group carries a Basic UDI-DI**, so three matching rungs
      can never fire. Needs a UDI source we do not have; EUDAMED's own reach here
      is 2 declarations. Half a day to wire, and it would unlock two matching rungs
      and a discovery rung that has never produced a hit. **Not recommended.**

### Closed — do not re-file

The full retrospective of each 2026-09-15/16 closure is archived in
[build-log.md](build-log.md) § *Findings closed 2026-09-15 and 2026-09-16*. One
line each here, because the number and the tick are what this page is for.

- [x] **F43** — four registry POST routes matched `{canonical_name}` while the GET
      used `{canonical_name:path}`, so the button rendered and the POST 404'd for
      the three canonical names carrying a slash. All four moved to `:path`; 9
      tests. **DONE 2026-09-15.**
- [x] **F42** — `app/contacts.py` had classified addresses since 2026-09-03 and
      nothing called it. [`app/mine_contacts.py`](../app/mine_contacts.py) +
      `dentalia mine-contacts` now do, 12 tests — **and the answer is that mining
      does not solve the address problem: 93 documents carry an address and it
      proposes zero.** The addresses are not in our documents. **WIRED 2026-09-15.**
- [x] **F44** — 97 jobs were dead; 56 re-enqueued, **52 recovered for $0,81**. The
      4 that failed again carry payloads written before `archive_url` was threaded
      through and cannot be replayed; nothing emits that shape any more. 41 dead
      `fetch.url` remain and are ordinary supplier-site failures, which is F7.
      **RE-RUN 2026-09-16.**
- [x] **F9** — 11.693 items carry no device class. **NOT A DEFECT, ruled
      2026-09-16**: the system is eventually deterministic and the class arrives
      with the documents. Stays a measurement (`data_anomaly` kind
      `md_class_blank`), stops being work. Does not retire F34's class I handling.
- [x] **F37** — the failure-monitor tick enqueued a `playbook.reonboard` payload
      the handler could not run, so every spike dead-lettered. The tick resolves
      the authored crawl recipe now and records "no recipe" as an ordinary skip.
      **DONE 2026-09-15.**
- [x] **F32** — **the registered fix was the wrong one.** KOMET's own fold moves
      the EUDAMED match count from 0 to 0; the difference is a **fourth segment**
      EUDAMED prints on 1.586 of 1.810 rows. Migration 068 folds and trims for a
      manufacturer whose playbook declares the strategy — KOMET alone — and
      **KOMET now matches 108 of its 258 flagged device items**. 19 tests including
      a SQL/Python drift guard. **DONE 2026-09-15.**
- [x] **F34** — the item card called a lapsed five-year clock "review due" while
      three other surfaces called the same documents expired, reporting a house
      rule as a regulatory breach. All four draw the line in the same place now,
      and the fifth draft kind `review-confirm` (migration 069) asks the
      manufacturer to **confirm the declaration is still current** rather than to
      renew it. 12 figure tests plus 8 for the draft; both guides. **DONE 2026-09-15.**
- [x] **F3** — DISCOVER logged a manual handoff as `outcome='hit'`, so a group that
      found nothing read **"Searched, found something"**. The manual rung logs
      `handoff` now and there is a fourth state, **"Searched, nothing found, sent
      to Missing documents"**; **274 historical rows repaired**. 10 tests; both
      guides. **DONE 2026-09-15.** Unblocked F37.
- [x] **F38** — 44 certificate gaps rendered with no button. Fourth draft kind
      `cert-request` (migration 067) writes one letter naming every certificate
      EUDAMED records they hold and we do not — the only ask this system makes
      that names the **document** rather than the articles. 14 tests; both guides.
      **DONE 2026-09-15.** 2 of those 25 manufacturers have an address — F42.
- [x] **F20** — no test carried a document through two stages of the real queue.
      [`tests/chain.py`](../tests/chain.py) drains the real `runner.run_once`;
      [`tests/test_chain.py`](../tests/test_chain.py) walks a declaration through
      backfill.scan → extract.doc → validate.doc → gate.candidate. Five tests, one
      of which is **AC5 re-proven with the queue in the loop**. **DONE 2026-09-15.**
- [x] **F5** — RESOLVE's T1 adjudicator raises **by ruling, not by defect.**
- [x] **F6** — a non-device document could reach production through the REF branch.
      Fixed 2026-09-03.
- [x] **F12** — **WITHDRAWN 2026-09-14.** `item_group_member.match_basis` records
      the grouping decision for all 15.958 items. What was pruned is the score and
      the runners-up, an audit-trail nicety.
- [x] **F13** — the search rung could not return a candidate. Fixed 2026-09-03.
- [x] **F14** — the queue's visibility timeout reached the claim. Fixed 2026-09-04.
- [x] **F21** — **NOT A DEFECT 2026-09-14.** Five hosts refuse robots and obeying
      them is the design; where a manufacturer walls us out, the route is manual.
- [x] **F22** — any worktree's stack attached to the registry's data directory.
      Fixed 2026-09-11.
- [x] **F24** — the BC writeback read certificate status from any company's
      certificate. Fixed 2026-09-11.
- [x] **F25** — the renewal reminder ladder could never climb past its first rung.
      Fixed 2026-09-11.
- [x] **F27** — re-validating a production document compared it with itself. Fixed
      2026-09-11.
- [x] **F28** — no playbook could be saved in the editor. Fixed 2026-09-11.
- [x] **F29** — NEODENT's declarations were read four pages deep. Fixed 2026-09-11.
- [x] **F30** — FETCH never read `robots.txt`. Fixed 2026-09-11.
- [x] **F39** — the deploy check hashed only `app/` and `migrations/`, so it
      reported green over a stale `web` image. Fixed 2026-09-14
      ([`app/version.py`](../app/version.py), [`scripts/deploy.sh`](../scripts/deploy.sh)).
- [x] **F40** — the BC push preview read certificate status from any company's
      certificate while the push itself was SRN-scoped, so the page could show one
      answer and the run send the opposite. Fixed 2026-09-14
      ([`bc_push.HOLDINGS_FOR_MANY_SQL`](../app/handlers/bc_push.py)).
- [x] **F41** — **WITHDRAWN 2026-09-14.** Four unscanned corpus folders do have a
      recorded reason: the S1.7 go/no-go pre-flight in
      [build-log.md](build-log.md). All four brands hold zero `md_flag` items.
