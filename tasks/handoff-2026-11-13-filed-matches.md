# Handoff: Filed, but maybe ours (from the 2026-11-13 session)

Start a new session in /home/denis/projects/dentalia and say:
"Read tasks/handoff-2026-11-13-filed-matches.md and continue from Next steps."

## What Denis asked

Filed documents (status `filed`, no item link) are seen only on /documents?status=filed.
He wanted the ones that may be ours surfaced, each labelled by how close the match is.
Per-manufacturer number formats: deferred.

## Done

- **Re-check on the server (write, Denis's go):** 33 filed documents whose exact BC
  match appeared after filing were re-queued through `validate.doc`. 28 published,
  5 stayed filed (1130 Ray not a device document; 1917 Solventum still no overlap;
  1873, 1942, 1952 old 2021 MDD DoCs, now with open gate-manual tasks 1184-1186 that
  no screen shows). Script: scratchpad `recheck_apply.py`, payload as
  `app/revalidate_name.apply`.
- **Branch `filed-matches`** (worktree `.claude/worktrees/filed-matches`), 6 commits on
  main 271faa2, NOT merged, NOT deployed:
  - `web/filed_matches.py`: three kinds (same number once separators go, leading
    zeros, a prefix), `find` (Today) and `for_document` (one document), `explain`
    (which part matched, shown bold). Only filed, group-scope, no-link documents.
  - Today card "Filed, but maybe ours" under Link checks: coloured labels (green
    same number, amber prefix, blue leading zeros), 12 rows then "See all N" in place,
    "no issue date" note.
  - Document page box "Close to a Business Central number" above "Add items this
    document covers…"; item picker section "Close to a number this document prints"
    first, unticked, Tick all. Shared markup `web/templates/_filed_match.html`.
  - Undated filed document: picker Add stays disabled (`data-blocked`,
    `static/item_picker.js`) with the reason shown.
  - Guide EN+SL (today, documents), bundles rebuilt, `docs/dev/web.md` row.
- Mockup approved by Denis (all three artboards): https://claude.ai/artifact/79z3oCs6VhqurbV5dz66Fh
- Production numbers 2026-11-13: 38 rows (12 / 18 / 8), about 130 ms per Today load.
  2 rows undated (Orangedental 780, 781: the PDFs print no date).

## Tests

Last run on c6ee2dc's tree: 704 passed (test_web, item picker x2, today,
filed_matches, vocab registry, import closure, docs sets). The full suite ran on an
earlier commit of the branch: 5847 passed, 1 failed
(`test_scheduler::test_the_sweep_tick_marks_due_and_emits_nothing`, fails on main too).

## Next steps

1. Full suite once on the branch head (`./scripts/test.sh`).
2. Re-time `find` on the server read-only (the group-scope filter came after the
   last timing): pipe `web/filed_matches.py` plus a timing harness into
   `docker compose exec -T worker python -`, as before.
3. Denis: merge `filed-matches` to main, push, deploy.
4. After deploy: check Today, doc 641 (Ultradent, 5 prefix items) and doc 831
   (Zlatarna) on the live site.

## Local preview

Container `filed-preview` (branch web app on http://localhost:8099, local database
through the read-only `dentalia_api` role). Restart after code changes:
`docker restart filed-preview`. Stop when done: `docker stop filed-preview`.

## Waiting on Denis

- Merge and deploy (above).
- Commit `tasks/followups.md` (4 new items: filed-recheck-orphan-tasks,
  dotted-ref-no-match, filed-1917-no-overlap, filed-undated-date-field),
  `tasks/lessons.md` (1 lesson) and this file on main?
- Tasks 1184-1186: close, or leave until we decide where tasks on filed documents show.
- Date field for undated filed documents: deferred ("we will handle those separately").
