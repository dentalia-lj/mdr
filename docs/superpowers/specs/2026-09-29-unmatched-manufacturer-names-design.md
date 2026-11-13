# Printed names without a manufacturer — design

Status: agreed with Denis 2026-09-29, from the mockups
(https://claude.ai/artifact/7f7EELwKLGGH5DxXHXDGL8). Built on branch
`unmatched-names`; §3 below is updated to what was built after two independent
reviews.

## 1. The problem

A document prints its manufacturer's legal name ("botiss biomaterials GmbH").
VALIDATE resolves it to a catalogue manufacturer through `manufacturer_alias`
(`app/manufacturers.canonicalize`). When no alias matches, VALIDATE flags
`manufacturer-unresolved` (`app/handlers/validate.py` ~947-955) and the
document waits on Review. Its REF list is never compared, because invariant 3
needs the manufacturer first. No reviewer button fixes it: `bind-manufacturer`
is offered only for whole-manufacturer documents (`is_mfr_binding`,
`web/app.py` ~1019), and the stuck documents are item-scoped (dev database
2026-09-29: all 50 unresolved documents are `coverage_scope = 'group'`).

The fix is an alias in the manufacturer's playbook. Wave 2 (2026-09-29) needed
six, found by hand; after they were added and 108 documents re-validated,
production went from 176 to 206 documents.

What is missing is **finding** these cases and saying what to do, not a way
to write aliases.

## 2. Decision

- **Aliases stay authored in playbook files**, reviewed in git, loaded by
  `manufacturers seed` + `playbooks sync`. An alias is a global identity rule:
  VALIDATE, GATE, and T0's identity search over every future document
  (`app/playbooks.py` `_DB_QUERY`, `app/extract/t0_templates.py`
  `extract_manufacturer`, conf 0.97) all read it. That stays behind review.
- **Rejected:** adding aliases from the web UI (branch `alias-action`, not
  merged). Two independent reviews found it unsafe: a UI alias silently becomes
  a T0 identity string for all future documents, and the "binds every item"
  confirmation could not be tied to what the job re-validated.
- **Built instead:** the system finds the cases, suggests the manufacturer with
  evidence, and says the action. A person adds the alias. A CLI command then
  re-validates the waiting documents.

## 3. What gets built

### 3.1 Grouping (read-only)

Waiting documents = staged or filed documents whose **latest** `validate.doc`
job (group_id null) flagged `manufacturer-unresolved`. Not the review task:
GATE never refreshes an open task's payload (`gate._push_manual` returns when a
task is open), so a task keeps the flag after a re-validation that resolved
the name. Printed name and REFs come from the latest `extraction_attempt`.
Group by `manufacturers.normalize(printed)`; show the most common spelling.
Blank names are counted, not listed.

### 3.2 Suggestion, three levels

| Level | Rule | Badge |
|---|---|---|
| Alias exists | The name already resolves to exactly one manufacturer (`resolve_canonicals`): the documents were validated before the alias. If that manufacturer has no `item_group`, the row says re-checking will not help | Alias exists |
| Article numbers | The documents' REFs (latest extraction), filtered like `_eligible_unscoped_refs`, looked up like `_manufacturers_holding_refs` (`mfr_ref` or `item_ref`, raw). **At least 3 REFs held by that manufacturer alone, making up at least 80% of the REFs the catalogue knows.** A REF several manufacturers hold counts for none (VALIDATE refuses that case as `multi-manufacturer-ref`). A warning when fewer than half the listed REFs are in the catalogue (distributor risk) | Article numbers |
| Name only | No REF evidence; the printed name contains a BC brand (`playbooks.brand_collision` against `vendor_master.brand_index`). A hint, never proof. | Name only |
| None | Neither. Possibly a distributor. | No match |

The 3 / 80% thresholds are measured read-only against the server's wave-2
documents before they are fixed in code.

The lookup lives in the import-free `app/unmatched_names.py` (the web image
cannot import `validate.py`, PyMuPDF). `validate.py` is unchanged; the REF
eligibility filter is duplicated there and a test holds the two equal.

### 3.3 Action sentence

- Suggested manufacturer has a playbook (`manufacturer.slug IS NOT NULL`):
  "Add to playbook `<slug>`".
- No playbook: "Start a playbook for <name>, with this name as its first alias".
- Name only: "Open one document and check the manufacturer. If right, add to
  playbook ...".
- No match: "Look at the documents. A distributor's name must not become an
  alias."

### 3.4 Data quality page

New card, **first on `/data-quality`, above "By kind"**; existing cards
unchanged. Columns: printed name (with its documents listed under it, linked),
waiting count, suggested manufacturer with badge and one evidence line, what to
do. Sorted by waiting count. Footer: totals, the rule for "Article numbers",
and the `revalidate` command to run after the alias is synced. Writes nothing;
no button adds an alias.

### 3.5 Weekly report

`report.weekly` (`app/handlers/report.py`) gets a result key and, in the HTML,
one summary line ("Printed names without a manufacturer: N names, M documents
waiting. K have a suggested manufacturer from article numbers.") and a short
table (name, waiting, suggested), in the report's existing hand-written style.

### 3.6 `revalidate --name` command

`app/cli.py`: `revalidate --name "<printed>"`, dry run by default, `--apply`
queues `validate.doc` (existing job type) with the payload and dedupe key the
pipeline already uses for `group_id` null
(`{content_hash, group_id: None, extract_rev, archive_url}`,
`validate:{hash}:{rev}:None`, as `extract.py` ~175).

- Refuses unless the name resolves to exactly one manufacturer
  (`manufacturers.resolve_canonicals`), i.e. the alias is already synced.
- Selection, taken from branch `alias-action` (`app/alias.py` `candidates`,
  reviewed): status staged or filed only; never production, rejected,
  superseded; not when the latest `validate.doc` had a `group_id`; not when no
  `validate.doc` exists; not when a reviewer edited it (T3 evidence); **new:**
  not when a reviewer reopened it (a `reopen` audit row); not when its last
  validation did not flag the name, or has not finished.
- Prints what it would queue and what it left alone, by reason.

### 3.7 Brand guard accent fix (separate, existing bug)

`playbooks._name_words` (~1090) casefolds but does not fold accents, so
"Molnlycke Health Care AB" passes against the BC brand MÖLNLYCKE, while
VALIDATE/GATE compare with `normalize`, which folds them. Fold with NFKD and drop
combining marks. Before merging, run `playbooks sync --dry-run` (or the guard
alone) over all playbooks against the server's vendor list, read-only: the fix
may newly refuse an existing file alias, which would then be a wrong alias we
already have.

## 4. Not built

- No alias writing from the web, no migration, no new job type, no new table.
- No automatic re-validation; the command is run by a person after the sync.

## 5. Tests

- Grouping: fold variants group together; documents without the flag, or with
  a closed task, are not counted.
- Suggestion: table-driven over the three levels, including the 3-REF minimum,
  the 80% threshold, a REF held by two manufacturers, and a document with no
  REFs.
- Data quality card and report render with and without rows; printed names
  with markup characters are escaped.
- `revalidate`: refuses an unresolved name; each exclusion reason; dry run
  queues nothing; `--apply` queues exactly the listed documents; end to end,
  a document flagged `manufacturer-unresolved` validates without the flag
  after the alias is added (pattern from `alias-action`'s
  `test_after_add_validate_resolves_the_printed_name`).
- Brand guard: accented brand against an unaccented name is refused.

## 6. Docs to update when built

Guide `data-quality.md` and `.sl.md` (then `scripts/build-guide.py`),
`docs/runbook.md` (the command), `docs/dev/playbook-authoring.md` (where to
find names that need an alias), `docs/code-map.md`, `tasks/followups.md`.
