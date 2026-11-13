# Handoff: Change type on Review (from the 2026-11-12 session)

Start a new session in /home/denis/projects/dentalia and say:
"Read tasks/handoff-2026-11-12-change-type.md and continue from Next step."

## Ask (Nataša, via Denis)

Doc 831 (ZLATARNA CELJE, "Bazne plošče Izjava EU o skladnosti 2026 izdaja 4.pdf")
is a declaration that was read as an ISO certificate. She needs a way to change the
type. She must confirm knowingly; Denis wants a double confirmation.

## Verified facts (production, read-only, 2026-11-12, server on b7668de)

- 831 is `staged`, ISO, MDR, `coverage_scope=manufacturer`, issued 2026-01-19,
  task 389 open (route mfr-binding, md-class-unknown). She believes she rejected
  it: no reject audit row and no `gate.apply` job exist. Tell Denis.
- Root cause: T0 type marker "izjava o skladnosti" misses "Izjava EU o skladnosti"
  (`app/extract/t0_templates.py:55`); "iso 13485" in the standards list then wins
  at conf 1.0. Both extraction revs carry ISO from T0; ref_list and UDI are empty.
  The page prints article 0541 and Basic UDI-DI 38310707AURODENT02XL. BC candidates:
  6050541, 60505415Z, 6050541Z (BC code 340, all 43 Zlatarna items class blank).
- Human edits: T3 evidence and held against machine writes (`gate._reviewer_held`).
  VALIDATE never reads T3. Production has 13 T3 edits, none of `type`.
- Review panel is chosen by `coverage_scope` (`_staging_doc_detail.html:60`,
  `is_mfr_binding`), not by type. The group panel's "Correct a fact first" already
  changes type at approval; the whole-range panel takes only `validity_from`
  (`gate.py` bind-manufacturer raises on anything else).
- EC certificates with group scope exist (doc 394), so scope is its own choice.
- 42 DoCs cite a staged EC/ISO through `cert_doc_id` (37 EC, 5 ISO).
- `reextract` and `revalidate` CLI commands exist (`app/cli.py`).

## Design agreed in chat (Denis approved the direction, not yet a spec)

- "Change type..." on Review, separate from Approve, for documents waiting on Review
  only. Published: Reject, Reopen (exists on the document page), Change type, Approve.
- She sets type, rules, and what it covers (default from the type: DoC/IFU/other ->
  specific items, EC/ISO -> whole range; editable).
- Two-step server confirm listing every consequence, plus a tick box. The document
  stays staged; approving is still a separate decision.
- New `gate.apply` decision `retype` (PRD change): T3 type, scope, regulation; staged
  links off (audited) when the scope becomes whole range; citing DoCs' `cert_doc_id`
  cleared when a certificate stops being one, `_backresolve_here` when a document
  becomes one; task reason refreshed; audit with before/after; refused while a job
  for the document's content hash is in flight. Never publishes, no re-read, no
  VALIDATE, no LLM cost.

## Plan and estimate (human / Claude Code, tests and docs included)

| # | Task | Human | CC |
|---|---|---|---|
| 0 | T0 marker fix, measured on the corpus; `reextract` 831 after Denis approves | 2 h | 0.75 h |
| 1 | PRD + decisions: `retype` decision | 1 h | 0.3 h |
| 2 | GATE handler + table-driven tests | 5 h | 1.5 h |
| 3 | Mockup first (memory: mockups before web UI), then Review control, confirm, route tests | 4.5 h | 1.75 h |
| 4 | Guide EN + SL (`docs/guide/pages/review*.md`, `scripts/build-guide.py`), dev docs, limits | 2 h | 0.75 h |
| 5 | Full suite, deploy when Denis asks | 1 h | 0.5 h |
| | Total | 15.5 h | 5.5 h |

## Next step

Write the spec (`docs/superpowers/specs/2026-11-12-change-type-design.md`, do not
commit without asking), get Denis's approval, then superpowers:writing-plans.
Task 0 can go first on its own: it is a bug fix.

## Boundaries

Production reads: `ssh denis@91.98.42.140`, `cd /srv/compliance/app`,
`docker compose exec -T postgres psql -U dentalia -d dentalia` with
`SET default_transaction_read_only=on`. Ask before any write or job on the server.
Tests only through `./scripts/test.sh`.

## When Change type is merged: tell Denis

Doc 165 (Sure Dent QMS certificate, bound to all 262 KOMET MD items) waits for this
feature. Repair path agreed 2026-11-12: Reject (reason wrong manufacturer), Reopen,
Change type to "specific items", tick the 16 Sure Dent items (BC primary code 077-A, live BC 2026-11-12; the July export had 14,
the GPPR/PPPR gutta-percha points). Remind Denis as soon as Change type is deployed.

## Status 2026-11-12 (execution started)

Spec and plan committed on main (2c1ee0b). Execution is subagent-driven in
worktree `.claude/worktrees/change-type` (branch `change-type`). Progress ledger:
`.claude/worktrees/change-type/.superpowers/sdd/2026-11-12-change-type/progress.md`.
To resume in a new session: "Continue the subagent-driven plan
docs/superpowers/plans/2026-11-12-change-type.md from its ledger" — tasks with a
`Task N: complete` line are done; resume at the first one without.

## Status 2026-11-12 (end of day)

All plan tasks done on branch `change-type` (worktree `.claude/worktrees/change-type`, head 5421ab3,
main merged in up to 777e2df). Full suite on the merged tree: 5803 passed, 1 failed
(test_scheduler clock test, fails on main too). Final review + re-review: all findings addressed.
NOT merged, NOT deployed (Denis, keep as-is). Before merging: browser click-through of Change type
on a throwaway local database (plan in this session: scratch DB dentalia_retype_demo, branch web app
in the test container, headless Chromium, screenshots), then merge to main, then deploy on Denis's go.
Rulings to confirm: 10 (confirm box look), 15 (retype retracts, not rejects), 16 (hand-set whole-range
documents keep approval-day items). Full rulings list: the ledger
`.claude/worktrees/change-type/.superpowers/sdd/2026-11-12-change-type/progress.md`.
