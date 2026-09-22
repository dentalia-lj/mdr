# Playbooks into the database

> **SUPERSEDED 2026-08-27. Built.** This was the pick-up note; the design is
> `docs/superpowers/specs/2026-08-26-playbooks-into-the-database-design.md` and
> the build is `docs/superpowers/plans/2026-08-26-playbooks-into-the-database.md`
> (16 tasks, four slices, migrations 046/047/048). Kept for the code findings
> and the framing that led to the brainstorm — **not** as a source of truth on
> what was built: several of its readings were corrected during the work, and
> where the two disagree the spec wins. Do not plan from this file.

**Pick-up note for a fresh session. Written 2026-08-25. The code findings below
were read out of the tree that day; verify anything that looks load-bearing
before building on it.**

**Status: an eventual requirement (Denis, 2026-08-25). Planned, not started,
not scheduled. Needs a brainstorm before any code.**

> **`[eudamed-sweep-and-certificate-watch]` does NOT wait on this.** EUDAMED is
> implementable against the playbook files exactly as they are today. Neither
> workstream blocks the other; do not sequence them.

---

## 1. Why

Playbooks are per-manufacturer authored rules that steer DISCOVER, EXTRACT,
VALIDATE and BACKFILL. Today they are 33 hand-authored JSON files in
`playbooks/`, bind-mounted read-only, edited by an engineer in git.

That makes onboarding a manufacturer an engineering task, and it is the
bottleneck on the project's first end-state output — item_ref → evidenced
documents. More manufacturers onboarded means more documents found. It also
blocks three logged followups that all want the same thing:

- `[manufacturer-contacts-ui]` — nothing can edit `manufacturer.contact_emails`;
  the whole argument for keeping contacts in the database rather than the
  playbooks was that a non-engineer can fix one.
- `[ui-change-manufacturer]` — Denis, 2026-08-14: *"changing manufacturer should
  be possible in the web UI, probably need to plan it."* Explicitly wants its own
  plan, not a field.
- `[registry-design-spec-gaps]` — five design-spec items dropped with no ruling,
  including inline `validate()` conflict surfacing.

## 2. Current state, read from the tree 2026-08-25

**33 authored files** in `playbooks/`. `Playbook` is a frozen dataclass in
`app/playbooks.py` with **16 fields**: `slug`, `manufacturer`, `bc_codes`,
`aliases`, `domains`, `doc_sources`, `ref_normalize`, `companion`, `rev`,
`date_labels`, `type_markers`, `source_priority`, `cert_number_pattern`,
`ref_pattern`, `extract_hints`, `coverage_map`, `exclude`, `skip_backfill`.
Eight of them are free-form dicts that nothing queries into.

### The finding that makes this tractable — there are exactly TWO loaders

| Loader | Produces | Notes |
|---|---|---|
| `app/playbooks.py::load_playbooks(dir_path)` | `tuple[Playbook, ...]` | Cached per directory, invalidated by a name/mtime/size signature (`_dir_signature`). Never raises — a malformed file is logged and skipped so one bad file cannot take DISCOVER down. |
| `app/extract/t0_layout.py::load_templates(dir_path)` | `tuple[Template, ...]` | **A second, independent glob of the same directory** for T0 parse templates. Easy to miss. |

Plus one raw file read: `web/registry.py::playbook_detail` re-reads the JSON to
display it (`root / f"{slug}.json"`).

**18 modules import playbook functions** (`app/cli.py`, `app/config.py`,
`app/extract/t0_layout.py`, `app/extract/t0_templates.py`,
`app/handlers/{backfill,discover,email_request,extract,validate}.py`,
`app/komet_coverage.py`, `app/periods.py`, `app/playbooks.py`,
`app/reconcile.py`, `app/regroup.py`, `app/vendor_master.py`, `app/version.py`,
`web/app.py`, `web/registry.py`) — **and none of them opens a file.** They all go
through `load_playbooks` or `for_manufacturer`. So the store swap lands in two
modules, not eighteen.

**Test surface:** 18 test files mention playbooks; 7 touch `PLAYBOOKS_DIR`,
`playbooks_dir` or `load_playbooks` directly. `playbooks.clear_cache()` exists
specifically for tests that repoint the directory.

**Not stranded:** `[playbook-extraction-branch-stranded]` worried that 16
commits adding five playbook keys were unmerged. **They landed** — migration
`031_extraction_playbook_rev.sql` is on master and all five keys are in
`app/playbooks.py`. No rebase hazard.

## 3. Design sketch (not a decision — the starting point for the brainstorm)

**Store the authored body as `jsonb`, do not shred it into columns.**

```sql
playbook          (slug PK, rev, body jsonb, updated_at, updated_by)
playbook_revision (slug, rev, body jsonb, authored_at, authored_by, note)  -- append-only
```

Reasoning: `_parse()` already turns a dict into a `Playbook`; hand it a jsonb
body and it is unchanged. Eight fields are free-form dicts nothing queries into,
so shredding is a large migration that buys nothing. And the T0 templates live in
the same JSON body, so one row feeds both loaders.

**Do not thread a connection through the call sites.** `load_playbooks()` takes
no connection today and runs in VALIDATE's per-group hot loop; threading one is
exactly the change that touches everything. Instead give `app/playbooks.py` a
module-level source, set once at process start in `app/workers/runner.py`,
`web/app.py` and `app/cli.py`. Keep the cache; key it on a revision watermark
instead of a directory signature. Call sites unchanged.

**Editing UI.** A form on `/playbooks/{slug}`. Invariant 1 makes web a producer
with no write grants on `document` / `item_document` / `evidence`; `playbook` is
none of those, so a narrow grant is the established pattern, not an exception —
`dentalia_api` already holds `INSERT, UPDATE ON job` (007),
`INSERT ON upload_inbox` (014) and `SELECT, UPDATE ON email_draft` (029).

## 4. Traps found — the expensive ones to rediscover

**`playbook_rev` changes meaning, and it is already load-bearing.** It is
hand-bumped today and `0` on most files, and it is recorded on
`extraction_attempt` (migration 031) so an extraction stays defensible after the
file changes. The moment a non-engineer can save, `rev` must auto-increment and
old bodies must be retrievable — otherwise extraction provenance silently points
at a body that no longer exists. This is a behaviour change to a column with
live data.

**Two sources of truth is the failure that eats a week.** Files and DB both live
means drift. Make it one-way: seed once, then the directory is never read again,
and pin it with a test asserting the loader does not touch the filesystem.

**A validated edit can still be semantically wrong.**
`[brand-parent-aliases]` is the proof: `Maillefer Instruments Holding Sàrl` and
`Sirona Dental Systems GmbH` were authored as DENTSPLY aliases and synced live.
Both are their own BC brands (MAILLEFER 022, SIRONA 012), so the aliases would
have scoped 86 Maillefer documents to DENTSPLY's groups and risked attaching a
Maillefer certificate to a Dentsply item. Reverted the same session. Revision
history is what makes a bad edit one click to undo.

**`validate()` must run on the PROPOSED set, on write.** Two playbooks claiming
one BC code, or one manufacturer name, is an authoring error that silently maps
items to the wrong manufacturer. `dentalia playbooks sync` today validates first
and writes nothing on a file-level conflict; a UI save must be refused the same
way, not accepted and discovered later by RESOLVE.

**Keep the never-raises posture.** `load_playbooks` logs and skips a malformed
file rather than raising. A malformed DB body must behave identically.

**`playbooks sync` derives `manufacturer_alias` from the files** and would have
to read the DB instead. Its conflict semantics (raise, write nothing; skip and
count a cross-catalogue code collision) must survive intact.

**`robots_refused.txt` lives in `playbooks/` and is deliberately not `.json`**
(`load_playbooks` globs `*.json` and would parse it as a playbook). It needs a
home in any move.

**The `:ro` bind mount into `web`** can only be retired after nothing reads the
directory — not before.

## 5. Slices (sketch, for the brainstorm to accept or replace)

1. Tables + one-way seed from the 33 files + `validate()` on write. Nothing reads
   the DB yet.
2. Swap `load_playbooks` and `load_templates` to the DB source. **The risky
   one** — every consumer silently changes backing store at once.
3. Edit form on `/playbooks/{slug}` + revision history + revert.
4. Retire the mount and stop reading the files.

## 6. Open, needs Denis

1. **Do the JSON files stay in git once the DB is authoritative?** Recommendation
   is to keep them, dated and marked historical — deleting loses the authoring
   rationale carried in 33 files' worth of comments.
2. **Who may edit, and is an audit trail of *who* required?** `updated_by` is in
   the sketch on the assumption that it is.

## 7. Where to start tomorrow

1. Read `app/playbooks.py` end to end — the field comments carry most of the
   design rationale for the whole subsystem.
2. Read `app/extract/t0_layout.py::load_templates`. It is the loader that gets
   forgotten.
3. Brainstorm before writing a spec. The jsonb-vs-shredded question and the
   `playbook_rev` provenance question are the two that decide the shape.
