"""Shell CLI: enqueue / inspect jobs, run migrations. `python -m app.cli ...`.

A thin operator tool over the queue. `enqueue` rejects unknown job types at the
database (the closed job_type enum — invariant 7), so typos fail loudly.
"""

from __future__ import annotations

import argparse
import functools
import json
import pathlib
import sys

from psycopg.errors import InvalidTextRepresentation

from app import (
    db, komet_coverage, manufacturer_seed, mine_contacts, mine_srn, playbooks,
    queue, reconcile,
    regroup,
    repair_archive_urls, repair_document_manufacturer, repair_label_dates,
    repair_mfr_overbind,
    repair_mfr_task_reason, repair_ref_list, repair_stated_class,
    vendor_master, version,
)
from app.extract import t0_layout


def cmd_migrate(args: argparse.Namespace) -> int:
    applied = db.migrate()
    print(f"applied {len(applied)} migration(s): {applied}" if applied else "up to date")
    return 0


def cmd_enqueue(args: argparse.Namespace) -> int:
    payload = json.loads(args.payload) if args.payload else {}
    try:
        with db.connect() as conn:
            jid = queue.enqueue(
                conn, args.type, payload, args.dedupe_key, priority=args.priority
            )
            conn.commit()
    except InvalidTextRepresentation:
        print(
            f"unknown job type {args.type!r} — job_type is a closed enum "
            "(PRD job-type table); check for a typo",
            file=sys.stderr,
        )
        return 2
    if jid is None:
        print(f"deduped: an active job already holds dedupe_key {args.dedupe_key!r}")
    else:
        print(f"enqueued job {jid} ({args.type}, priority={args.priority})")
    return 0


def cmd_discover_item(args: argparse.Namespace) -> int:
    """Admin refetch ([admin-refetch-item]): resolve item_ref -> group_id and
    enqueue discover.group at interactive priority, recency window bypassed
    only with --force. The CLI's polite counterpart to the item-page button —
    without --force it is an ordinary discover.group re-run (still useful:
    it re-tries any rung that was miss-only, e.g. a contact added since)."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT group_id FROM item_group_member WHERE item_ref=%s LIMIT 1",
            (args.item_ref,),
        ).fetchone()
        if row is None:
            print(
                f"discover-item: {args.item_ref!r} is unknown or has no group "
                "— nothing enqueued",
                file=sys.stderr,
            )
            return 2
        group_id = row["group_id"]
        # Postgres current_date, not Python's — avoids worker-clock skew, same
        # rationale as discover.py's _known_url_rows recency comparison.
        today = conn.execute("SELECT current_date AS d").fetchone()["d"]
        dedupe_key = f"discover:refetch:{group_id}:{today.isoformat()}"
        payload = {"group_id": group_id, "ignore_recency": bool(args.force)}
        jid = queue.enqueue(conn, "discover.group", payload, dedupe_key, priority="interactive")
        conn.commit()

    if jid is None:
        print(f"deduped: an active job already holds dedupe_key {dedupe_key!r}")
    else:
        print(f"enqueued job {jid} (discover.group, group {group_id}, priority=interactive)")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    with db.connect() as conn:
        counts = conn.execute(
            "SELECT status, count(*) AS n FROM job GROUP BY status ORDER BY status"
        ).fetchall()
        print("job counts by status:")
        for r in counts or []:
            print(f"  {r['status']:>10}: {r['n']}")
        if not counts:
            print("  (queue empty)")
        recent = conn.execute(
            "SELECT id, type, status, priority, attempts, dedupe_key "
            "FROM job ORDER BY id DESC LIMIT %s",
            (args.limit,),
        ).fetchall()
        if recent:
            print(f"\nlast {len(recent)} job(s):")
            for r in recent:
                print(
                    f"  #{r['id']} {r['type']} [{r['status']}] "
                    f"prio={r['priority']} attempts={r['attempts']} key={r['dedupe_key']}"
                )
    return 0


def sync_aliases(conn, loaded, *, vendor_rows=()) -> dict:
    """Project BC codes AND playbook `aliases` into manufacturer_alias.

    Two distinct sources feed the same table, at two different scopes:

    * BC codes, entity-level. With `vendor_rows` (BC's manufacturer master)
      every code it issues gets its master name, and an authored playbook wins
      for every code of the entity it claims -- not just the codes it named.
      Without them, this degrades to the playbook-only projection it was
      before, which is what makes it safe to run before the master is
      imported.
    * Playbook `aliases`, playbook-level. These are the names a manufacturer
      prints on its OWN documents (a DoC signs with a legal entity name, never
      a BC vendor code), keyed straight from `Playbook.aliases` to
      `Playbook.manufacturer` with source='playbook' always -- an alias is by
      definition authored, never vendor-master-derived. This is what lets
      VALIDATE's backfill path (docs/superpowers/plans/2026-08-10-backfill-
      matching.md, Blocker 1) resolve an extracted "Ivoclar Vivadent AG" back
      to the BC-code-derived `canonical_manufacturer` a group already carries.
      Previously this projection did not exist at all -- both LJ adapter
      profiles supply a manufacturer CODE, never a name
      (app/adapters/source.py), so a row keyed on a document-facing name could
      never be hit by RESOLVE's `_alias_lookup`; only a NAME-keyed reader
      (VALIDATE's backfill lookup) makes writing it worthwhile.

    Validates first and writes nothing on a FILE-level conflict: two playbooks
    claiming one code (or one name -- `playbooks.validate` checks
    `Playbook.names()`, manufacturer plus aliases) is an authoring error that
    would silently map items to the wrong manufacturer. That now covers every
    contested code: until 2026-08-26 the conflict key carried a catalogue tag,
    so a cross-catalogue collision was legal as files and reached
    `derive_aliases`' skip-and-count instead. With one catalogue no such case
    exists, and `skipped` is empty on this path -- the counter stays because
    `derive_aliases` keeps its own guard.

    Reports `orphaned_groups` -- item_group rows still carrying a
    canonical_manufacturer value this sync has just re-pointed away from: the
    raw code itself (a fresh insert, or the RESOLVE self-seed case), or the
    alias's PRIOR canonical_name (an existing alias re-pointed from one real
    manufacturer name to another). Re-pointing an alias does NOT retro-fix
    groups already resolved under the old value, so run sync BEFORE the
    ingest, and re-resolve if this count is non-zero.
    """
    # The brand map turns on the `BrandCollision` guard, and this is the path
    # `[brand-parent-aliases]` went down: two aliases that were other BC
    # brands' names were authored, synced, and only caught by a human reading
    # the diff. `reconcile` reports the same collision, but `reconcile` is a
    # separate command an operator can skip -- this one cannot be skipped,
    # because it is the write.
    playbooks.validate(loaded, brands=vendor_master.brand_index(conn))

    intended, skipped = reconcile.derive_aliases(vendor_rows, loaded)

    stats = {"inserted": 0, "updated": 0, "unchanged": 0, "skipped": len(skipped),
             "skipped_detail": skipped, "orphaned_groups": 0}
    # Values an item_group.canonical_manufacturer might still carry that this
    # sync has just moved an alias away from: the raw code (insert / self-seed
    # case) or the alias's prior canonical_name (re-point to a different real
    # name -- see finding 2, an alias already resolved to one manufacturer name
    # going stale when sync repoints it to another).
    stale_values: set[str] = set()

    def write(raw: str, canonical: str, source: str) -> None:
        row = conn.execute(
            "SELECT canonical_name, source FROM manufacturer_alias WHERE raw_name=%s",
            (raw,),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO manufacturer_alias (raw_name, canonical_name, source) "
                "VALUES (%s,%s,%s)",
                (raw, canonical, source),
            )
            stats["inserted"] += 1
            stale_values.add(raw)
        elif row["canonical_name"] != canonical:
            conn.execute(
                "UPDATE manufacturer_alias SET canonical_name=%s, source=%s "
                "WHERE raw_name=%s",
                (canonical, source, raw),
            )
            stats["updated"] += 1
            stale_values.add(raw)
            stale_values.add(row["canonical_name"])
        else:
            # Same name; the provenance may still be stale (a self-seeded
            # `001 -> 001` that the master now confirms, say).
            if row["source"] != source:
                conn.execute(
                    "UPDATE manufacturer_alias SET source=%s WHERE raw_name=%s",
                    (source, raw),
                )
            stats["unchanged"] += 1

    for raw, (manufacturer, source) in sorted(intended.items()):
        write(raw, manufacturer, source)

    for pb in sorted(loaded, key=lambda p: p.slug):
        for alias in pb.aliases:
            write(alias, pb.manufacturer, "playbook")

    if stale_values:
        row = conn.execute(
            "SELECT count(*) AS n FROM item_group WHERE canonical_manufacturer = ANY(%s)",
            (list(stale_values),),
        ).fetchone()
        stats["orphaned_groups"] = row["n"]

    return stats


def _cmd_playbooks_validate() -> int:
    """The operator gate: unlike `load_playbooks` (never-raises runtime path),
    this must fail loudly and non-zero on real authoring errors, naming the
    offending file(s) -- review finding 2. Two failure modes `load_playbooks`
    alone can't surface because it silently drops what it can't parse:

    1. A file that isn't valid JSON, or fails `_parse` (e.g. missing/blank
       `manufacturer`) -- never makes it into `loaded` at all, so a naive
       `len(loaded)` count can't tell "5 files, 5 valid" from "6 files, 1
       silently dropped". Caught by comparing the directory listing against
       `loaded`'s slugs.
    2. A file that parses as a `Playbook` fine (identity fields untouched) but
       carries a broken T0 parse section -- e.g. `match` typo'd as `matches`,
       or an unknown `ref_strategy` -- so `t0_layout.load_templates` silently
       drops the template with only a `log.warning` (invisible on a CLI run).
       Caught by cross-checking `t0_layout.load_templates` against every file
       that *looks* like it's attempting a parse section (carries a `match`
       or `ref_strategy` key), the same test `load_templates` itself uses to
       decide "no template attempted" (legitimate) vs "attempted and broken".
    """
    dir_path = playbooks.PLAYBOOKS_DIR
    files = sorted(dir_path.glob("*.json")) if dir_path.is_dir() else []
    if not files:
        print(f"no playbooks found in {dir_path}", file=sys.stderr)
        return 2

    loaded = playbooks.load_playbooks(dir_path)
    loaded_stems = {pb.slug for pb in loaded}
    unparsed = [f.name for f in files if f.stem not in loaded_stems]

    template_stems = {t.slug for t in t0_layout.load_templates(dir_path)}
    broken_parse = []
    for f in files:
        if f.stem not in loaded_stems or f.stem in template_stems:
            continue   # already reported as unparsed, or has a working template
        try:
            data = json.loads(f.read_text())
        except Exception:
            continue   # can't happen (f.stem would not be in loaded_stems)
        if "match" in data or "ref_strategy" in data:
            broken_parse.append(f.name)

    errors = []
    if unparsed:
        errors.append(
            f"{len(unparsed)} of {len(files)} file(s) failed to parse as a "
            f"playbook: {', '.join(unparsed)}"
        )
    if broken_parse:
        errors.append(
            f"{len(broken_parse)} file(s) carry a T0 parse section that failed "
            f"to load as a template (check `match`/`ref_strategy`): "
            f"{', '.join(broken_parse)}"
        )

    try:
        playbooks.validate(loaded)
    except playbooks.PlaybookConflict as exc:
        errors.append(f"playbook conflict: {exc}")

    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        return 2

    print(f"{len(loaded)} playbook(s) valid")
    return 0


def _cmd_playbooks_reconcile(args: argparse.Namespace) -> int:
    """Read-only: do the authored playbooks actually join to what BC emits?

    Sits between `validate` (files well-formed) and `sync` (first write), and
    that ordering is the whole point -- `item_group.canonical_manufacturer` is
    effectively write-once, so a code synced to the wrong name before an ingest
    has no repair path. Exits 1 on blocking findings so it can gate a run;
    2 only for operational errors, as elsewhere in this CLI.
    """
    loaded = playbooks.load_playbooks()
    if not loaded:
        print(f"no playbooks found in {playbooks.PLAYBOOKS_DIR}", file=sys.stderr)
        return 2

    with db.connect() as conn:
        vendor_rows = reconcile.load_vendor_master(conn)
        if not vendor_rows:
            print(
                "vendor_master is empty; run `vendor-master --apply` first.",
                file=sys.stderr,
            )
            return 2
        aliases = reconcile.load_aliases(conn)
        if args.export:
            try:
                counts = reconcile.counts_from_export(args.export, args.catalogue)
            except (FileNotFoundError, ValueError) as exc:
                print(f"reconcile: {exc}", file=sys.stderr)
                return 2
            source = args.export
        else:
            counts = reconcile.counts_from_mirror(conn)
            source = "item_mirror"

    report = reconcile.build(
        vendor_rows, loaded, aliases, counts,
        items_source=source,
    )
    for line in reconcile.render(report, limit=args.limit):
        print(line)

    if not counts:
        print(
            "\nNOTE: no item counts available (item_mirror is empty and no "
            "--export given), so every finding reads as 0 items. Pass "
            "--export <BC export> to size them before the first ingest."
        )
    if report.blocking:
        print(
            f"\n{len(report.blocking)} blocking finding(s). Fix the playbooks, "
            "then re-run. Nothing was written."
        )
        return 1
    print("\nNothing was written. `playbooks sync` is the write.")
    return 0


def _reorder(old, new):
    """`new`'s content in `old`'s key order, recursively.

    Postgres normalises `jsonb` key order, so a body read back from the
    database has lost the ordering the file was authored in -- `doc_sources`
    entries came back as url/kind/note/doc_type against the authored
    doc_type/kind/url/note, and a one-key change produced a twelve-line diff of
    reordering. These files are kept in git to be READ (2026-08-26 ruling), and
    a regeneration that churns them defeats that.

    Lists are matched positionally: the database preserves list order (only
    OBJECT keys are normalised), so element i in both is the same entry. A list
    that changed length falls back to the database's own value, since nothing
    here can say which element moved.
    """
    if isinstance(old, dict) and isinstance(new, dict):
        out = {k: _reorder(old[k], new[k]) for k in old if k in new}
        out.update({k: v for k, v in new.items() if k not in out})
        return out
    if isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        return [_reorder(a, b) for a, b in zip(old, new)]
    return new


def _cmd_playbooks_drift(args: argparse.Namespace) -> int:
    """Report where `playbooks/*.json` and the database disagree.

    The database is authoritative (migration 050); the files are the authoring
    record. Nothing kept the two honest, so a file edit after the import went
    nowhere and nobody was told -- `manufacturers seed` refuses on a conflict
    but cannot say which side moved, so its advice could only be "resolve by
    hand".

    Reports by default and writes NOTHING. `--apply` regenerates the files that
    match an older revision, because for those the database provably holds
    everything the file had. It never rewrites a DIVERGED file: that body
    matches no revision, so the edit in it exists nowhere else and regenerating
    would destroy it (Denis, 2026-08-31).
    """
    import json

    with db.connect() as conn:
        rows = manufacturer_seed.drift(conn)

    stale = [r for r in rows if r[1] == manufacturer_seed.FILE_STALE]
    diverged = [r for r in rows if r[1] == manufacturer_seed.DIVERGED]
    other = [r for r in rows
             if r[1] in (manufacturer_seed.NOT_IMPORTED, manufacturer_seed.DB_ONLY)]
    synced = sum(1 for r in rows if r[1] == manufacturer_seed.IN_SYNC)

    print(f"{len(rows)} playbook(s): {synced} in sync, {len(stale)} file-stale, "
          f"{len(diverged)} diverged, {len(other)} other")

    for slug, state, d in stale:
        print(f"\n  FILE STALE  {slug}")
        print(f"     db at rev {d['rev']}, file matches rev {d['matches_rev']}")
        print(f"     {d['delta']}")
    for slug, state, d in diverged:
        print(f"\n  DIVERGED    {slug}")
        print(f"     db at rev {d['rev']}, file matches no revision")
        print(f"     {d['delta']}")
        print(f"     resolve in the editor: /playbooks/{slug}")
    for slug, state, _ in other:
        print(f"\n  {state.upper():<11} {slug}")
    if other:
        print("\n  not-imported is `manufacturers seed`'s job; db-only means the "
              "file was deleted.")

    if not stale and not diverged:
        return 0
    if not args.apply:
        if stale:
            print(f"\n{len(stale)} stale file(s) can be regenerated with --apply.")
        if diverged:
            print(f"{len(diverged)} divergence(s) need a human; --apply will not "
                  "touch them.")
        print("nothing written")
        return 0

    for slug, _, d in stale:
        path = playbooks.PLAYBOOKS_DIR / f"{slug}.json"
        raw = json.loads(path.read_text())
        # Rebuild IN THE FILE'S OWN KEY ORDER. A dict built from the row and
        # then updated reorders every key, so a regeneration that changed one
        # value produced a 14-line diff of pure churn -- which defeats keeping
        # these files in git to be read (Denis's 2026-08-26 ruling). Identity
        # keys are carried from the file: they live in their own tables and
        # are not part of the body at all.
        db_body = d["db_body"]
        rebuilt = {}
        for k, v in raw.items():
            if k in playbooks.BODY_EXCLUDED_KEYS:
                rebuilt[k] = v                          # identity, untouched
            elif k in db_body:
                rebuilt[k] = _reorder(v, db_body[k])    # db value, file's order
            # a key the database dropped is dropped here too
        for k, v in db_body.items():                    # anything db added
            if k not in rebuilt:
                rebuilt[k] = v
        path.write_text(json.dumps(rebuilt, indent=2, ensure_ascii=False) + "\n")
        print(f"  rewrote playbooks/{slug}.json from db rev {d['rev']}")
    print(f"\n{len(stale)} file(s) regenerated. "
          f"{len(diverged)} divergence(s) left untouched.")
    return 0


def cmd_playbooks(args: argparse.Namespace) -> int:
    if args.action == "validate":
        return _cmd_playbooks_validate()
    if args.action == "reconcile":
        return _cmd_playbooks_reconcile(args)
    if args.action == "drift":
        return _cmd_playbooks_drift(args)

    loaded = playbooks.load_playbooks()
    if not loaded:
        print(f"no playbooks found in {playbooks.PLAYBOOKS_DIR}", file=sys.stderr)
        return 2

    with db.connect() as conn:
        vendor_rows = reconcile.load_vendor_master(conn)
        try:
            stats = sync_aliases(
                conn, loaded, vendor_rows=vendor_rows
            )
        except playbooks.PlaybookConflict as exc:
            # psycopg 3 commits on a clean `with` exit -- returning here without
            # an exception propagating would commit any write already issued.
            # sync_aliases finishes conflict detection before its first write
            # today, but that's an internal invariant, not something this
            # function can rely on; roll back explicitly so the conflict path
            # is safe by construction, not by accident.
            conn.rollback()
            print(f"playbook conflict: {exc}", file=sys.stderr)
            return 2
        conn.commit()

    print(
        f"aliases: {stats['inserted']} inserted, {stats['updated']} updated, "
        f"{stats['unchanged']} unchanged"
        + (f" (from {len(vendor_rows)} vendor-master code(s))" if vendor_rows
           else " (playbooks only — vendor_master is empty)")
    )
    for code, reason in stats["skipped_detail"]:
        print(f"  SKIPPED {code}: {reason}")
    if stats["orphaned_groups"]:
        print(
            f"WARNING: {stats['orphaned_groups']} item_group row(s) still carry a "
            "canonical_manufacturer this sync re-pointed. Re-resolve those items, "
            "or run sync before the next ingest."
        )
    return 0


def cmd_manufacturers(args: argparse.Namespace) -> int:
    """Seed the manufacturer entity tables from vendor_master + playbooks.

    **Dry run by default**, `--apply` to write. Not because this command can do
    damage -- it is additive, idempotent, and refuses to write anything on a
    conflict -- but because every other manufacturer-touching command in this
    CLI (`vendor-master`, `regroup`) is dry-run-first, and a reader should not
    have to remember which one is the exception (Denis, 2026-08-26). The dry run
    is also when you want to read the orphan report.

    Writes nothing on a conflict, the same posture as `playbooks sync`: a code
    or a name already pointing at a different manufacturer is a decision someone
    took, and an import must not half-apply around it.
    """
    dry_run = not args.apply
    # `--adopt <slug>`: declare the FILE the intended version for these
    # playbooks. Needed because `_write_body` refuses a body that differs from
    # the database -- it cannot tell an authored file edit from a UI edit -- and
    # a conflict aborts the whole import, so one diverged playbook blocks every
    # other change in the run. Per-slug and explicit: a blanket "files win"
    # would revert every UI edit in the tree.
    adopt = frozenset(args.adopt or ())
    # FILES, explicitly. This command's whole job is to import them, and with a
    # source configured a bare `load_playbooks()` would return what is already
    # in the database -- the import would read its own output, report a clean
    # run, and never land a file edit.
    loaded = playbooks.load_playbooks(playbooks.PLAYBOOKS_DIR)
    if not loaded:
        print(f"no playbooks found in {playbooks.PLAYBOOKS_DIR}", file=sys.stderr)
        return 2

    with db.connect() as conn:
        try:
            stats = manufacturer_seed.seed(conn, adopt=adopt, loaded=loaded,
                                           dir_path=playbooks.PLAYBOOKS_DIR)
        except playbooks.PlaybookConflict as exc:
            # psycopg 3 commits on a clean `with` exit; roll back explicitly so
            # the conflict path is safe by construction, not by accident.
            conn.rollback()
            print(f"playbook conflict: {exc}", file=sys.stderr)
            return 2
        except manufacturer_seed.MissingVendorMaster as exc:
            # A precondition, not a conflict: on a fresh database this used to
            # escape as a raw ForeignKeyViolation naming a BC code, which said
            # nothing about the import that was missing.
            conn.rollback()
            print(f"cannot seed yet: {exc}", file=sys.stderr)
            return 2

        if dry_run or not stats.clean:
            conn.rollback()
        else:
            conn.commit()

    print("\n".join(manufacturer_seed.render(stats, dry_run=dry_run)))
    if not stats.clean:
        print("\nnothing written — resolve the conflicts above and re-run",
              file=sys.stderr)
        return 2
    if dry_run:
        print("\n(dry run — nothing written; re-run with --apply)")
    return 0


def cmd_vendor_master(args: argparse.Namespace) -> int:
    """Mirror BC's manufacturer master into `vendor_master`.

    Dry-run by default: it prints what would change and writes nothing. That is
    deliberate rather than cautious-by-habit — `item_group.
    canonical_manufacturer` is effectively write-once (RESOLVE's ladder
    short-circuits on `_existing_link`), so a code re-pointed here after items
    are grouped produces a split-brain no later import can repair.
    """
    try:
        rows = vendor_master.read_file(args.file)
    except (FileNotFoundError, ValueError) as exc:
        print(f"vendor-master: {exc}", file=sys.stderr)
        return 2

    with db.connect() as conn:
        d = vendor_master.diff(conn, rows)

        print(f"{len(rows)} row(s) in {args.file}")
        print(
            f"  added {len(d.added)} · renamed {len(d.renamed)} · "
            f"disappeared {len(d.disappeared)} · unchanged {d.unchanged}"
        )
        for code, old, new in d.renamed:
            print(f"  RENAME {code}: {old!r} -> {new!r}")
        for code in d.disappeared:
            print(f"  GONE   {code} (row kept, import_batch left stale)")

        if not args.apply:
            print("dry run — nothing written. Re-run with --apply to write.")
            return 0

        try:
            stats = vendor_master.apply(
                conn,
                rows,
                batch=args.batch or pathlib.Path(args.file).name,
                allow_renames=args.allow_renames,
            )
        except vendor_master.RenameRefused as exc:
            print(f"vendor-master: {exc}", file=sys.stderr)
            return 2
        conn.commit()

    print(
        f"written: {stats['added']} added, {stats['renamed']} renamed, "
        f"{stats['unchanged']} unchanged"
    )
    return 0


def cmd_regroup(args: argparse.Namespace) -> int:
    """Drop groups so RESOLVE rebuilds them. Dry run by default.

    The reversibility mechanism for the alias seed: `canonical_manufacturer` is
    write-once in practice, so without this a wrong seed is permanent. Refuses
    the whole selection if any part of it is blocked -- a half-regrouped
    catalogue is worse than either end state.
    """
    if not (args.manufacturer or args.group or args.all):
        print(
            "regroup: select something -- --manufacturer NAME (repeatable), "
            "--group ID (repeatable), or --all",
            file=sys.stderr,
        )
        return 2

    with db.connect() as conn:
        sc = regroup.scope(
            conn,
            manufacturers=args.manufacturer,
            group_ids=args.group,
            all_groups=args.all,
        )
        for line in regroup.render(sc, limit=args.limit):
            print(line)

        if not sc.groups:
            return 0
        if sc.blockers:
            print(
                f"\n{len(sc.blockers)} blocker(s). Nothing was deleted. Narrow "
                "the selection, or resolve the blockers first."
            )
            return 1
        if not args.apply:
            print("\ndry run — nothing deleted. Re-run with --apply.")
            return 0

        stats = regroup.apply(conn, sc, priority=args.priority)
        conn.commit()

    print(
        f"\nregrouped: {stats['groups']} group(s), {stats['discovery_rows']} "
        f"discovery_log row(s) and {stats['suggestions']} open grouping "
        f"suggestion(s) deleted, {stats['enqueued']} resolve.group job(s) "
        f"enqueued at {args.priority} priority"
        + (f", {stats['already_queued']} already active"
           if stats["already_queued"] else "")
    )
    return 0


def cmd_repair_ref_list(args: argparse.Namespace) -> int:
    """Restore REF lists an escalated tier erased. Dry run by default.

    A one-off for data extracted before the `tiers.merge` fix, which let a later
    tier's empty answer delete a correct T0 REF list. Costs nothing: T0 is
    deterministic and re-reads the same archived bytes, and the stored LLM
    answers are reused as-is, so this is a replay and not a re-extraction. On a
    database with no pre-fix rows it plans zero and writes nothing.
    """
    with db.connect() as conn:
        repairs, skipped = repair_ref_list.plan(conn)
        for line in repair_ref_list.render(repairs, skipped):
            print(line)
        if not repairs:
            return 0
        if not args.apply:
            print("dry run — nothing written. Re-run with --apply.")
            return 0

        stats = repair_ref_list.apply(conn, repairs)
        conn.commit()

    print(f"\nrepaired: {stats['appended']} extraction_attempt row(s) appended, "
          f"{stats['jobs_deleted']} stale validate.doc job(s) deleted, "
          f"{stats['jobs_emitted']} re-emitted at the new rev")
    return 0


def cmd_repair_archive_urls(args: argparse.Namespace) -> int:
    """Re-home /imports corpus handles into the hash-addressed archive.

    One-off for backfill rows written before the archive fix: verifies each
    file's sha256 against the stored content_hash, archives it through the
    same StorageAdapter layout FETCH uses, re-points document + evidence
    archive_url, and writes one audit_log entry per document (invariant-1
    exception, Denis ruling 2026-08-24). On a repaired database it plans zero.
    """
    from app.adapters.storage import LocalFsStore
    from app.config import load_config

    cfg = load_config()
    store = LocalFsStore(cfg.storage.local_root, cfg.storage.base_url)
    prefix = (cfg.storage.base_url or "/archive").rstrip("/") + "/"
    # store_root lets the resolver find a fetched copy by its hash-prefixed
    # name when the stored handle is a remote URL (the C3 clobber shape).
    resolver = functools.partial(repair_archive_urls.default_resolver,
                                 store_root=cfg.storage.local_root)
    with db.connect() as conn:
        repairs, skipped = repair_archive_urls.plan(conn, prefix=prefix, resolver=resolver)
        for line in repair_archive_urls.render(repairs, skipped):
            print(line)
        if not repairs:
            return 0
        if not args.apply:
            print("dry run — nothing written. Re-run with --apply.")
            return 0

        stats = repair_archive_urls.apply(conn, store, repairs)
        conn.commit()

    print(f"\nre-homed: {stats['documents']} document(s), "
          f"{stats['evidence_rows']} evidence row(s) re-pointed")
    return 0


def cmd_repair_mfr_overbind(args: argparse.Namespace) -> int:
    """Retract an enumerating QA certificate's mfr-scope production links.

    One-off for links C16 machine-bound before the [qa-device-enumeration]
    guard existed: the certificate's own text limits it to a device list, and
    the registry asserted manufacturer-wide coverage anyway (doc 310, Denis
    ruling 2026-08-24 — "Guard + clean 310"). Refuses (exit 1) rather than
    plans when the stored text does not enumerate, when the text is missing,
    or when the binding was a human decision. Status flip to `retracted`,
    append-only, one audit_log entry per link (invariant-1 exception,
    recorded via decided_by). On a repaired document it plans zero.
    """
    with db.connect() as conn:
        retractions, report = repair_mfr_overbind.plan(conn, args.doc_id)
        for line in repair_mfr_overbind.render(args.doc_id, retractions, report):
            print(line)
        if report["blockers"]:
            return 1
        if not retractions:
            return 0
        if not args.apply:
            print("dry run — nothing written. Re-run with --apply.")
            return 0

        stats = repair_mfr_overbind.apply(conn, args.doc_id, retractions)
        conn.commit()

    print(f"\nretracted: {stats['links_retracted']} mfr-scope production link(s) "
          f"on doc {args.doc_id}")
    return 0


def cmd_repair_document_manufacturer(args: argparse.Namespace) -> int:
    """Bind existing documents to the manufacturer their evidence already names.

    Migration 053 is additive, so every document written before it carries a
    NULL binding — correct but useless, since the column exists precisely for
    the 540 documents that reach no manufacturer through item links. 531 hold a
    `manufacturer` evidence row and 481 of those resolve to exactly one curated
    canonical (measured 2026-08-31). The rest stay NULL: an ambiguous spelling
    belongs to a human, and minting entities for unresolved names would seed the
    registry from OCR. Never overwrites an existing binding, so a second run
    plans zero.
    """
    with db.connect() as conn:
        planned = repair_document_manufacturer.plan(conn)
        for line in repair_document_manufacturer.render(planned, verbose=args.verbose):
            print(line)
        if not planned["resolved"]:
            return 0
        if not args.apply:
            print("dry run — nothing written. Re-run with --apply.")
            return 0

        written = repair_document_manufacturer.apply(conn, planned["resolved"])
        conn.commit()

    print(f"\nbound: {written} document(s)")
    return 0


def cmd_repair_mfr_task_reason(args: argparse.Namespace) -> int:
    """Stamp `reason` onto mfr-binding review tasks raised before it existed.

    Those tasks render the DEFAULT review sentence -- "approving links it to
    every medical-device product from that manufacturer" -- which is false when
    every item we hold from them carries a blank BC device class, and that is
    the case `[mfr-bind-empty-class]` raised them for. 17 open tasks were in
    that state on 2026-08-31, doc 2 (3Shape Poland) among them. Changes no
    disposition, no link and no document, only the sentence a reviewer sees.
    Plans zero on a second run.
    """
    with db.connect() as conn:
        total_open = conn.execute(
            "SELECT count(*) AS n FROM manual_task "
            "WHERE status='open' AND payload->>'route'='mfr-binding'"
        ).fetchone()["n"]
        stamps = repair_mfr_task_reason.plan(conn)
        for line in repair_mfr_task_reason.render(stamps, total_open):
            print(line)
        if not stamps:
            return 0
        if not args.apply:
            print("dry run — nothing written. Re-run with --apply.")
            return 0

        stamped = repair_mfr_task_reason.apply(conn, stamps)
        conn.commit()

    print(f"\nstamped: {stamped} task(s)")
    return 0


def cmd_repair_stated_class(args: argparse.Namespace) -> int:
    """Record the class already-held documents state. Dry run by default.

    A one-off for documents extracted before `stated_class` was a target
    (migration 032). Costs nothing and calls no model: T0 reads the class off
    `document_text`, which is already stored. Appends a new `extract_rev` and
    deliberately emits NO `validate.doc` -- this records a display value and
    must not re-adjudicate 768 settled dispositions to do it. On a database
    where every latest attempt already carries the key it writes nothing.
    """
    with db.connect() as conn:
        repairs, counts = repair_stated_class.plan(conn)
        for line in repair_stated_class.render(repairs, counts):
            print(line)
        if not repairs:
            return 0
        if not args.apply:
            print("dry run — nothing written. Re-run with --apply.")
            return 0

        stats = repair_stated_class.apply(conn, repairs)
        conn.commit()

    print(f"\nrepaired: {stats['appended']} extraction_attempt row(s) appended, "
          f"0 jobs emitted (by design — nothing is re-adjudicated)")
    return 0


def cmd_mine_srn(args: argparse.Namespace) -> int:
    """Read EUDAMED SRNs off documents we already hold. Dry run by default.

    Costs nothing: no model call and no network call, because the SRNs are in
    text that is already parsed and stored. It exists because the two paths
    that fill `manufacturer_srn` both need EUDAMED to already know the
    manufacturer -- the register pull sees only actors holding certificates,
    and the article probe is unreachable through any UI or scheduler path. A
    manufacturer with no certificates therefore has no SRN and cannot be swept,
    which is GC EUROPE: 356 device articles, zero certificates, and its SRN
    printed in 126 documents in our own archive.

    Every row lands `pending`, never `auto`. A confirmed SRN makes that
    manufacturer's whole catalogue sweepable against EUDAMED, and the pattern
    matches an importer's or notified body's SRN quoted in somebody else's
    document just as happily as the manufacturer's own. The decision is a
    person's, at `/manufacturers/srn-queue`.
    """
    with db.connect() as conn:
        candidates, counts = mine_srn.plan(conn)
        if args.apply and candidates:
            stats = mine_srn.apply(conn, candidates)
            conn.commit()
            print(mine_srn.render(candidates, counts, apply=True))
            print(f"\ninserted {stats['inserted']} of {stats['proposed']} proposed")
            return 0
        print(mine_srn.render(candidates, counts, apply=False))
    return 0


def cmd_mine_contacts(args: argparse.Namespace) -> int:
    """Read supplier contact addresses off documents we already hold. Dry run
    by default.

    Costs nothing -- no model call, no network call: the addresses sit in text
    that is already parsed and stored. It exists because `contact_emails` is
    what a letter is addressed to and what the drafts page refuses to release a
    letter without, and 7 of 384 manufacturers carry one. Every gap request,
    certificate request and renewal chase this system composes for the other
    377 lands unsendable.

    It writes only a ROLE mailbox on the document's own manufacturer's domain.
    A certifier, another company's domain, an unrecognised one and a named
    individual are all reported for a person and never written -- see
    `app/mine_contacts.py` for why each of those four is refused separately. It
    never overwrites a column somebody has already filled.
    """
    with db.connect() as conn:
        writable, reported, counts = mine_contacts.plan(conn)
        if args.apply and writable:
            stats = mine_contacts.apply(conn, writable)
            conn.commit()
            print(mine_contacts.render(writable, reported, counts, apply=True))
            print(f"\nwrote {stats['written']} of {stats['proposed']} proposed")
            return 0
        print(mine_contacts.render(writable, reported, counts, apply=False))
    return 0


def cmd_repair_label_dates(args: argparse.Namespace) -> int:
    """Replace the manufactured date verbatims with the real span. Dry run by
    default.

    Costs no model call: T0 reads the dates off the archived PDF, which is
    already held. Measured 2026-09-04 the whole population is 0 value changes
    and 85 verbatim changes, so this corrects what a reviewer is shown and
    moves no compliance date -- and a value disagreement is REFUSED rather
    than applied, because rewriting a date is not this tool's call.

    Unlike `repair-stated-class` it DOES re-emit `validate.doc`: only GATE may
    write `evidence` (invariant 1), and a corrected quote that never reaches
    the `evidence` table has repaired nothing.
    """
    with db.connect() as conn:
        repairs, counts, skipped = repair_label_dates.plan(conn)
        for line in repair_label_dates.render(repairs, counts, skipped):
            print(line)
        if not repairs:
            return 0
        if not args.apply:
            print("dry run — nothing written. Re-run with --apply.")
            return 0

        stats = repair_label_dates.apply(conn, repairs)
        conn.commit()

    print(f"\nrepaired: {stats['appended']} extraction_attempt row(s) appended, "
          f"{stats['jobs_deleted']} stale validate.doc deleted, "
          f"{stats['jobs_emitted']} emitted")
    return 0


def cmd_komet_coverage(args: argparse.Namespace) -> int:
    """Link items from a manufacturer's own document index. Dry run by default."""
    with db.connect() as conn:
        plans, skipped = komet_coverage.plan(conn)
        for line in komet_coverage.render(plans, skipped):
            print(line)
        if not plans:
            return 0
        if not args.apply:
            print("dry run — nothing written. Re-run with --apply.")
            return 0
        stats = komet_coverage.apply(conn, plans)
        conn.commit()
    print(f"\napplied: {stats['appended']} extraction_attempt row(s) appended, "
          f"{stats['jobs_deleted']} stale validate.doc job(s) deleted, "
          f"{stats['jobs_emitted']} re-emitted at the new rev")
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    """Print the source fingerprint. Run it on the host and in the image and
    compare: different values mean the image is stale and a run would execute
    code you are not looking at.

        python -m app.cli version
        docker compose run --rm worker python -m app.cli version
    """
    parts = version.fingerprint_parts()
    print(f"combined  {version.source_fingerprint()}")
    for part in parts:
        print(part)
    if any(not p.present for p in parts):
        print("\nA root missing here is missing from the IMAGE, not from the repo:\n"
              "the combined digest cannot be compared against a side that has it.\n"
              "Compare the per-root lines instead — those are like for like.")
    return 0


def cmd_healthcheck(args: argparse.Namespace) -> int:
    """Exit 0 if THIS container's own heartbeat is fresh, 1 otherwise.

    What each long-running service's Docker HEALTHCHECK runs. Per-instance on
    purpose: a container asking whether its SERVICE is up would pass while
    wedged, because a healthy replica answers for it.

    Any failure -- unreachable database, missing table, no row -- is unhealthy.
    A worker that cannot reach Postgres can do no work, so reporting it healthy
    would be a lie that costs an operator the restart.

        python -m app.cli healthcheck --service worker
    """
    from app import heartbeat

    instance = args.instance
    if instance is None:
        from app.workers.runner import heartbeat_instance
        instance = heartbeat_instance(args.worker_id or f"{args.service}-1")
    try:
        with db.connect() as conn:
            ok = heartbeat.check(conn, args.service, instance,
                                 stale_after_s=args.stale_after)
    except Exception as exc:  # noqa: BLE001 - a probe reports, it does not raise
        print(f"unhealthy: {type(exc).__name__}: {exc}")
        return 1
    print(f"{'healthy' if ok else 'unhealthy'}: {args.service}/{instance}")
    return 0 if ok else 1


def cmd_schema_drift(args: argparse.Namespace) -> int:
    """Exit 0 if this database matches what the tree's migrations would build.

    Builds the schema in a scratch database and compares object by object. The
    test suite cannot do this -- it builds from scratch every run, so it only
    ever sees the tree's version, and a database that drifted stays green.

        python -m app.cli schema-drift

    Run it after deploying and whenever a migration is corrected. See
    `app/schema_drift.py` for why a per-migration content hash was rejected in
    favour of comparing the schemas themselves.
    """
    from app import schema_drift

    report = schema_drift.check(keep=args.keep)
    if args.keep:
        print(f"scratch database kept: {report.scratch}")

    if report.order:
        print(f"{len(report.order)} migration(s) were applied in a different order "
              f"than a fresh database would apply them:\n")
        for o in report.order:
            print(f"  {o}")
        print("\nHistory, not a defect — they cannot be re-applied in another\n"
              "order. It matters only because the two databases took different\n"
              "paths, so the schema comparison below is what shows whether they\n"
              "still arrived at the same place.\n")

    if report.ok:
        print("no drift: the database matches the migrations in the tree")
        return 0
    print(f"{len(report.drift)} object(s) differ from the tree:\n")
    for d in report.drift:
        print(f"  {d}")
    print("\n`extra` exists here and not in the tree (a hand-run statement, or a\n"
          "migration corrected in place); `missing` and `changed` mean this\n"
          "database never received something the tree describes. Correcting an\n"
          "applied migration takes a NEW numbered file -- see\n"
          "docs/dev/changing-things.md.")
    return 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dentalia", description="Dentalia pipeline CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser(
        "version", help="source fingerprint (compare host vs image to catch a stale build)")
    v.set_defaults(func=cmd_version)

    hc = sub.add_parser(
        "healthcheck",
        help="exit 0 if this container's own heartbeat is fresh (Docker HEALTHCHECK)")
    hc.add_argument("--service", required=True,
                    help="worker | scheduler — the service this container runs")
    hc.add_argument("--instance", default=None,
                    help="override the instance key (default: the same one the "
                         "process beats under, worker_id@hostname)")
    hc.add_argument("--worker-id", default=None,
                    help="the process's worker id, if it is not the default")
    hc.add_argument("--stale-after", type=int, default=60,
                    help="seconds before a beat counts as dead (default 60; a "
                         "scheduler ticking once a minute needs more)")
    hc.set_defaults(func=cmd_healthcheck)

    m = sub.add_parser("migrate", help="apply pending migrations")
    m.set_defaults(func=cmd_migrate)

    sd = sub.add_parser(
        "schema-drift",
        help="exit 0 if this database matches what the tree's migrations build")
    sd.add_argument("--keep", action="store_true",
                    help="do not drop the scratch database (to inspect it)")
    sd.set_defaults(func=cmd_schema_drift)

    e = sub.add_parser("enqueue", help="enqueue a job")
    e.add_argument("type", help="queue tag, e.g. ingest.run (must be a valid job_type)")
    e.add_argument("dedupe_key", help="active-scope dedupe key")
    e.add_argument("--payload", default=None, help="payload as a JSON object")
    e.add_argument(
        "--priority", choices=["interactive", "delta", "sweep"], default="sweep"
    )
    e.set_defaults(func=cmd_enqueue)

    di = sub.add_parser(
        "discover-item",
        help="re-run discovery for one item's group (admin refetch)",
    )
    di.add_argument("item_ref", help="Dentalia item_ref (BC's own number)")
    di.add_argument(
        "--force", action="store_true",
        help="bypass the recency window (ignore_recency) — without it, an "
             "ordinary discover.group re-run",
    )
    di.set_defaults(func=cmd_discover_item)

    i = sub.add_parser("inspect", help="show queue status")
    i.add_argument("--limit", type=int, default=10, help="recent jobs to list")
    i.set_defaults(func=cmd_inspect)

    pb = sub.add_parser(
        "playbooks", help="validate, reconcile, sync, or drift-check playbooks/")
    pb.add_argument("action", choices=["validate", "reconcile", "sync", "drift"])
    pb.add_argument(
        "--export",
        default=None,
        help="reconcile: size findings from a BC item export instead of "
             "item_mirror (the useful case pre-ingest, when the mirror is empty)",
    )
    pb.add_argument(
        "--catalogue", default="LJ", help="reconcile: catalogue tag of --export"
    )
    pb.add_argument(
        "--limit", type=int, default=40, help="reconcile: alias-diff rows to print"
    )
    pb.add_argument(
        "--apply", action="store_true",
        help="drift: regenerate the files reported FILE STALE from the "
             "database body. Never touches a DIVERGED file",
    )
    pb.set_defaults(func=cmd_playbooks)

    mf = sub.add_parser(
        "manufacturers",
        help="seed the manufacturer entity tables from vendor_master + playbooks",
    )
    mf.add_argument("action", choices=["seed"])
    # No `--source`: one Business Central, one article numbering, so the
    # `code_source` this seeds from is a constant and not the caller's choice.
    mf.add_argument(
        "--adopt",
        action="append",
        metavar="SLUG",
        help="declare the FILE the intended version for this playbook, "
             "overwriting the database body. Repeatable. Needed when a "
             "playbook was edited in the file rather than the editor: the "
             "import cannot tell an authored change from a UI edit, so it "
             "refuses, and one diverged playbook otherwise blocks the whole "
             "run. `dentalia playbooks drift` names the diverged slugs",
    )
    mf.add_argument(
        "--apply",
        action="store_true",
        help="actually write. Without it this is a dry run that reports what "
             "would be written (including the orphan report) and writes nothing",
    )
    mf.set_defaults(func=cmd_manufacturers)

    vm = sub.add_parser(
        "vendor-master", help="mirror BC's manufacturer master (dry run by default)"
    )
    vm.add_argument(
        "--file",
        default="imports/Proizvajalci.xlsx",
        help="BC manufacturer export (.xlsx/.csv), columns Šifra + Ime",
    )
    vm.add_argument("--batch", default=None, help="import batch label (default: filename)")
    vm.add_argument("--apply", action="store_true", help="write; without it, dry run")
    vm.add_argument(
        "--allow-renames",
        action="store_true",
        help="permit re-pointing a code to a different manufacturer",
    )
    vm.set_defaults(func=cmd_vendor_master)

    rg = sub.add_parser(
        "regroup",
        help="drop item groups so RESOLVE rebuilds them (dry run by default)",
    )
    rg.add_argument(
        "--manufacturer", action="append", default=None,
        help="select by canonical_manufacturer (repeatable) — the unit an alias "
             "correction moves",
    )
    rg.add_argument(
        "--group", action="append", default=None, help="select by group id (repeatable)"
    )
    rg.add_argument("--all", action="store_true", help="select every group")
    rg.add_argument(
        "--priority", choices=["interactive", "delta", "sweep"],
        default=regroup.DEFAULT_PRIORITY,
        help="priority of the re-enqueued resolve.group jobs",
    )
    rg.add_argument("--limit", type=int, default=20, help="rows to print")
    rg.add_argument("--apply", action="store_true", help="delete; without it, dry run")
    rg.set_defaults(func=cmd_regroup)

    rr = sub.add_parser(
        "repair-ref-list",
        help="restore REF lists an escalated tier erased (dry run by default)",
    )
    rr.add_argument("--apply", action="store_true", help="write; without it, dry run")
    rr.set_defaults(func=cmd_repair_ref_list)

    rau = sub.add_parser(
        "repair-archive-urls",
        help="re-home /imports archive handles into the hash-addressed archive "
             "(dry run by default)",
    )
    rau.add_argument("--apply", action="store_true", help="write; without it, dry run")
    rau.set_defaults(func=cmd_repair_archive_urls)

    rsc = sub.add_parser(
        "repair-stated-class",
        help="record the class already-held documents state (dry run by default)",
    )
    rsc.add_argument("--apply", action="store_true", help="write; without it, dry run")
    rsc.set_defaults(func=cmd_repair_stated_class)

    ms = sub.add_parser(
        "mine-srn",
        help="read EUDAMED SRNs off documents we already hold "
             "(dry run by default; every row lands pending)",
    )
    ms.add_argument("--apply", action="store_true", help="write; without it, dry run")
    ms.set_defaults(func=cmd_mine_srn)

    mc = sub.add_parser(
        "mine-contacts",
        help="read supplier contact addresses off documents we already hold "
             "(dry run by default; role mailboxes only, never overwrites)",
    )
    mc.add_argument("--apply", action="store_true", help="write; without it, dry run")
    mc.set_defaults(func=cmd_mine_contacts)

    rld = sub.add_parser(
        "repair-label-dates",
        help="replace the manufactured date verbatims with the real span "
             "(dry run by default)",
    )
    rld.add_argument("--apply", action="store_true", help="write; without it, dry run")
    rld.set_defaults(func=cmd_repair_label_dates)

    rmo = sub.add_parser(
        "repair-mfr-overbind",
        help="retract an enumerating QA certificate's mfr-scope production links "
             "(dry run by default)",
    )
    rmo.add_argument("doc_id", type=int,
                     help="the over-bound document (e.g. 310, Kiwa Cermet MED 31385)")
    rmo.add_argument("--apply", action="store_true", help="write; without it, dry run")
    rmo.set_defaults(func=cmd_repair_mfr_overbind)

    rdm = sub.add_parser(
        "repair-document-manufacturer",
        help="bind documents to the manufacturer their evidence names "
             "(dry run by default)",
    )
    rdm.add_argument("--apply", action="store_true", help="write; without it, dry run")
    rdm.add_argument("--verbose", action="store_true",
                     help="list every binding, not just the declined ones")
    rdm.set_defaults(func=cmd_repair_document_manufacturer)

    rmt = sub.add_parser(
        "repair-mfr-task-reason",
        help="stamp `reason` on mfr-binding review tasks raised before it "
             "existed (dry run by default)",
    )
    rmt.add_argument("--apply", action="store_true", help="write; without it, dry run")
    rmt.set_defaults(func=cmd_repair_mfr_task_reason)

    kcv = sub.add_parser(
        "komet-coverage",
        help="link items from the manufacturer's own document index (dry run by default)",
    )
    kcv.add_argument("--apply", action="store_true", help="write; without it, dry run")
    kcv.set_defaults(func=cmd_komet_coverage)
    return p


def use_database_playbooks() -> None:
    """Read playbooks from rows rather than the directory, for every command
    EXCEPT the two that exist to read the authored files: `manufacturers seed`
    (which imports them) and `playbooks validate` (which checks them). Both
    pass an explicit directory, which always wins over this.

    Short-lived and autocommit -- nothing here writes playbooks, so the loader
    never joins a caller's transaction.

    Called from `__main__`, not from `main()`. It sets process-global state,
    and eight test modules call `main()` directly; wiring it there repointed
    the loader for every test that ran afterwards.
    """
    playbooks.set_source(lambda: db.connect(autocommit=True))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    use_database_playbooks()
    sys.exit(main())
