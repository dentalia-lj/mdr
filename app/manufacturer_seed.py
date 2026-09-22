"""Seed the manufacturer ENTITY tables from `vendor_master` + authored playbooks.

One-way and explicit (`dentalia manufacturers seed`), never automatic. The
entity grouping is `reconcile._entities`, reused rather than re-derived: that
function is the one place that knows `001`, `005` and `275` are all IVOCLAR
VIVADENT, and a second implementation of it would drift silently. It is private
to `reconcile` because nothing outside the report needed it until now; this
module is the second caller, and it deliberately calls the same function rather
than copying the union-find.

WHAT THIS WRITES

  manufacturer            one row per NAMED entity
  manufacturer_bc_code    one row per BC code of that entity, FK'd to vendor_master
  manufacturer_name       canonical + every authored alias, casefolded

WHAT IT DOES NOT WRITE

  `manufacturer_alias` -- untouched. That stays the runtime projection RESOLVE
  and GATE read, written by `dentalia playbooks sync`. This module adds the
  entity tables beside it; it does not replace or re-derive it.

  `vendor_master` -- untouched. It is BC's mirror (016) and
  `manufacturer_bc_code` merely points at it.

IDEMPOTENT BY CONSTRUCTION. Every write is `ON CONFLICT DO NOTHING` plus an
explicit check of what the existing row says, so a second run reports
`unchanged` rather than churning, and a row that disagrees is REPORTED rather
than overwritten -- an existing link is a decision someone took, and an import
should not quietly reverse it (same posture as `vendor_master`'s
`RenameRefused`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from psycopg.types.json import Json

from app import playbooks as playbooks_mod
from app import reconcile
from app.vendor_master import DEFAULT_CODE_SOURCE

# `code_source` survives as a COLUMN (it is half of `vendor_master`'s primary
# key) but is no longer a parameter anywhere: there is one Business Central and
# one article numbering, so the value is a constant, not a choice the caller
# makes. Imported from `app.vendor_master` rather than redeclared, so the seed
# cannot drift from the table it foreign-keys into.


class MissingVendorMaster(RuntimeError):
    """The BC manufacturer master has not been imported yet.

    A precondition, raised before anything is written, and presented by the CLI
    the same way `playbooks.PlaybookConflict` is: roll back, say what to run,
    exit 2. The message carries the remedy, because the whole defect was that
    the ordering existed only in someone's head.
    """


@dataclass
class SeedStats:
    entities: int = 0
    named: int = 0
    # Entities with no vendor-master name and no playbook claim. `''` counts
    # here too: an empty canonical is not a manufacturer, and a row for it would
    # take the `canonical_name` UNIQUE slot for a value that means "unknown".
    skipped_unnamed: int = 0

    manufacturers_inserted: int = 0
    manufacturers_updated: int = 0      # slug attached to a pre-existing row
    manufacturers_unchanged: int = 0

    codes_inserted: int = 0
    codes_unchanged: int = 0

    names_inserted: int = 0
    names_unchanged: int = 0

    # Role mailboxes copied from a playbook's `contacts` into
    # `manufacturer.contact_emails`, which is what `email.request` reads.
    contacts_seeded: int = 0
    contacts_unchanged: int = 0

    #: Bodies overwritten because the operator named the playbook in
    #: `--adopt`, declaring the FILE the intended version.
    bodies_adopted: int = 0

    # The behaviour half (migration 050). Only the 33 slugs with a playbook
    # have one; the other ~350 manufacturer rows keep `body IS NULL`, which is
    # what `/manufacturers?no_playbook=1` lists.
    bodies_inserted: int = 0
    bodies_unchanged: int = 0

    #: Rows the seed refused to touch because an existing row says something
    #: different. Never silently resolved. (subject, existing, intended)
    conflicts: list[tuple[str, str, str]] = field(default_factory=list)

    #: `item_group.canonical_manufacturer` values with no `manufacturer` row
    #: after this seed, with the number of groups carrying each. Expected to be
    #: non-empty on live data -- `''` and any value stranded by an alias repoint
    #: that never re-resolved (`playbooks sync`'s `orphaned_groups`).
    orphan_groups: list[tuple[str, int]] = field(default_factory=list)

    #: Rows in `manufacturer_alias` AFTER this seed. This command does not
    #: write that table -- `playbooks sync` does -- and on a fresh database
    #: the seed therefore lands 381 manufacturers and 0 aliases while
    #: reporting success at every line. RESOLVE then matches nothing and
    #: every group comes out unnamed. Counted so `render` can say so.
    alias_rows: int = 0

    @property
    def clean(self) -> bool:
        return not self.conflicts


def _upsert_manufacturer(conn, stats: SeedStats, canonical: str, slug: str | None) -> int:
    # SLUG FIRST, canonical name second. The name is not a stable key: a UI
    # rename (slice 3b) changes it while the file still says the old one, and
    # a canonical-only lookup then matches nothing and INSERTs -- which does
    # not silently duplicate the manufacturer, because `slug` is UNIQUE (049),
    # it raises and takes the whole import down. The slug is what both sides
    # agree on across a rename: the playbook's own name for itself.
    row = None
    if slug is not None:
        row = conn.execute(
            "SELECT id, slug, canonical_name FROM manufacturer WHERE slug = %s",
            (slug,),
        ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT id, slug, canonical_name FROM manufacturer WHERE canonical_name = %s",
            (canonical,),
        ).fetchone()
    if row is not None and row["canonical_name"] != canonical:
        # Found by slug under a different name: someone renamed it after the
        # import. Report, write nothing -- the same posture as an edited body
        # and a re-pointed slug. Which side is right is a human's call, and
        # renaming back would undo a decision silently.
        stats.conflicts.append(
            (f"manufacturer canonical name {slug!r}",
             f"canonical_name={row['canonical_name']!r}",
             f"canonical_name={canonical!r}")
        )
        stats.manufacturers_unchanged += 1
        return row["id"]
    if row is None:
        new = conn.execute(
            "INSERT INTO manufacturer (canonical_name, slug) VALUES (%s,%s) RETURNING id",
            (canonical, slug),
        ).fetchone()
        stats.manufacturers_inserted += 1
        return new["id"]

    if row["slug"] == slug:
        stats.manufacturers_unchanged += 1
    elif row["slug"] is None and slug is not None:
        # A playbook was authored for an entity seeded before it existed. That
        # is the normal onboarding direction, so attach it.
        conn.execute("UPDATE manufacturer SET slug=%s WHERE id=%s", (slug, row["id"]))
        stats.manufacturers_updated += 1
    else:
        # Re-pointing an entity at a DIFFERENT playbook is not an import's call.
        stats.conflicts.append(
            (f"manufacturer {canonical!r}", f"slug={row['slug']!r}", f"slug={slug!r}")
        )
        stats.manufacturers_unchanged += 1
    return row["id"]


def _link_code(conn, stats, code_source: str, code: str, mfr_id: int, source: str) -> None:
    row = conn.execute(
        "SELECT manufacturer_id, source FROM manufacturer_bc_code "
        "WHERE code_source=%s AND code=%s",
        (code_source, code),
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO manufacturer_bc_code (code_source, code, manufacturer_id, source) "
            "VALUES (%s,%s,%s,%s)",
            (code_source, code, mfr_id, source),
        )
        stats.codes_inserted += 1
        return
    if row["manufacturer_id"] != mfr_id:
        stats.conflicts.append(
            (f"bc_code {code_source}/{code}",
             f"manufacturer_id={row['manufacturer_id']}",
             f"manufacturer_id={mfr_id}")
        )
    stats.codes_unchanged += 1


def _link_name(conn, stats, name: str, mfr_id: int, kind: str) -> None:
    folded = name.casefold()
    row = conn.execute(
        "SELECT manufacturer_id, name FROM manufacturer_name WHERE name_folded=%s",
        (folded,),
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO manufacturer_name (name_folded, name, manufacturer_id, kind) "
            "VALUES (%s,%s,%s,%s)",
            (folded, name, mfr_id, kind),
        )
        stats.names_inserted += 1
        return
    if row["manufacturer_id"] != mfr_id:
        # This is the `[brand-parent-aliases]` shape: one name claimed by two
        # manufacturers. `playbooks.validate()` catches it between playbooks;
        # this catches it between a playbook alias and another entity's
        # vendor-master name, which validate() cannot see.
        stats.conflicts.append(
            (f"name {name!r}",
             f"manufacturer_id={row['manufacturer_id']}",
             f"manufacturer_id={mfr_id}")
        )
    stats.names_unchanged += 1


def _orphan_groups(conn) -> list[tuple[str, int]]:
    rows = conn.execute(
        "SELECT g.canonical_manufacturer AS name, count(*) AS n "
        "FROM item_group g "
        "LEFT JOIN manufacturer m ON m.canonical_name = g.canonical_manufacturer "
        "WHERE m.id IS NULL "
        "GROUP BY 1 ORDER BY 2 DESC, 1"
    ).fetchall()
    return [(r["name"], r["n"]) for r in rows]



def _write_contacts(conn, stats: "SeedStats", mfr_id: int, slug: str,
                    raw: dict, adopt: frozenset[str] | None = None) -> None:
    """Seed `manufacturer.contact_emails` from the playbook's `contacts`.

    `contact_emails` stays the single READ path -- `email.request` and
    DISCOVER's e-mail rung both take it from the column, and the review UI
    edits it there. This only SEEDS it, so an authored playbook makes a
    manufacturer addressable without anyone retyping addresses into a form.

    Refuses to overwrite a column a person has already filled, for the same
    reason `_write_body` refuses: after slice 3a the UI can edit contacts, and
    re-running the import would otherwise revert that edit to whatever the file
    said. A difference is reported and the row left alone.
    """
    authored = list(raw.get("contacts") or [])
    if not authored:
        return
    authored = [str(a).strip().lower() for a in authored]

    row = conn.execute(
        "SELECT contact_emails FROM manufacturer WHERE id=%s", (mfr_id,)
    ).fetchone()
    current = list(row["contact_emails"] or []) if row else []

    if not current:
        conn.execute(
            "UPDATE manufacturer SET contact_emails=%s WHERE id=%s",
            (authored, mfr_id),
        )
        stats.contacts_seeded += 1
    elif current == authored:
        stats.contacts_unchanged += 1
    elif adopt and slug in adopt:
        conn.execute("UPDATE manufacturer SET contact_emails=%s WHERE id=%s",
                     (authored, mfr_id))
        stats.contacts_seeded += 1
    else:
        stats.conflicts.append(
            (f"contacts {slug!r}",
             f"{current} in the database",
             f"{authored} in playbooks/{slug}.json"),
        )
        stats.contacts_unchanged += 1

def _write_body(conn, stats: SeedStats, mfr_id: int, slug: str, raw: dict,
                adopt: frozenset[str] | None = None) -> None:
    """The behaviour half, plus revision 0 as its provenance.

    Refuses to overwrite a body that differs from the file's. After slice 3a a
    body can have been edited in the UI, and re-running this import would
    otherwise silently revert that edit to whatever the file said -- the exact
    failure `_upsert_manufacturer` refuses for slugs. A conflict is reported and
    the row left alone.
    """
    body = playbooks_mod.body_of(raw)
    rev = int(raw.get("rev", 0) or 0)
    encoded = Json(body)

    row = conn.execute(
        "SELECT body, playbook_rev FROM manufacturer WHERE id=%s", (mfr_id,)
    ).fetchone()

    if row["body"] is None:
        conn.execute(
            "UPDATE manufacturer SET body=%s, playbook_rev=%s WHERE id=%s",
            (encoded, rev, mfr_id),
        )
        stats.bodies_inserted += 1
    elif row["body"] == body and row["playbook_rev"] == rev:
        stats.bodies_unchanged += 1
    elif adopt and slug in adopt:
        # The missing verb. The refusal below exists because this function
        # cannot tell an authored file edit from someone's UI edit -- so the
        # operator names the playbook and says which one it was. Explicit and
        # per-slug on purpose: a blanket "files win" would silently revert
        # every UI edit in the tree, which is the failure the refusal prevents.
        conn.execute(
            "UPDATE manufacturer SET body=%s, playbook_rev=%s WHERE id=%s",
            (encoded, rev, mfr_id),
        )
        stats.bodies_adopted += 1
    else:
        stats.conflicts.append(
            (f"body {slug!r}",
             f"rev={row['playbook_rev']} in the database",
             f"rev={rev} in playbooks/{slug}.json"),
        )
        stats.bodies_unchanged += 1
        return

    # Revision 0 is the file's own provenance: what `extraction_attempt`
    # (playbook_slug, playbook_rev) resolved to before anyone edited anything.
    # `ON CONFLICT DO NOTHING` keeps the import idempotent without granting it
    # the UPDATE the table deliberately withholds.
    conn.execute(
        "INSERT INTO manufacturer_playbook_revision "
        "  (manufacturer_id, rev, body, authored_by, note) "
        "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (manufacturer_id, rev) DO NOTHING",
        (mfr_id, rev, encoded, "seed",
         f"seeded from playbooks/{slug}.json"),
    )


#: What a file's body is, relative to the row that is now authoritative.
#: `manufacturer_playbook_revision` is what makes a DIRECTION knowable at all:
#: without it two differing bodies are just "not equal", which is why the seed
#: could only ever say "resolve by hand" (2026-08-31).
IN_SYNC = "in-sync"
FILE_STALE = "file-stale"          # file matches an OLDER revision: db moved ahead
DIVERGED = "diverged"              # file matches NO revision: both sides edited
NOT_IMPORTED = "not-imported"      # no row yet; the seed's insert path owns it
DB_ONLY = "db-only"                # a row whose file is gone


def body_delta(file_body: dict, db_body: dict) -> str:
    """Key-level difference, db relative to file. Keys only, never values: a
    body holds regexes and URL lists, and a diff that printed them would be
    unreadable in a terminal and would leak the whole playbook into a log."""
    f, d = set(file_body or {}), set(db_body or {})
    parts = []
    if d - f:
        parts.append("db has +" + ",".join(sorted(d - f)))
    if f - d:
        parts.append("file has +" + ",".join(sorted(f - d)))
    both = {k for k in f & d if (file_body or {})[k] != (db_body or {})[k]}
    if both:
        parts.append("differs: " + ",".join(sorted(both)))
    return "; ".join(parts)


def classify_drift(conn, slug: str, file_body: dict) -> tuple[str, dict]:
    """`(state, detail)` for one playbook. Reads only; writes nothing.

    The three interesting states are distinguished by the revision history, not
    by the bodies alone. A file that matches an older revision is STALE and can
    be regenerated mechanically, because the database holds everything the file
    once did plus whatever came after. A file that matches no revision was
    edited independently of the database and regenerating it would DESTROY that
    edit -- so it is refused and handed to a human, every time.
    """
    row = conn.execute(
        "SELECT id, playbook_rev, body FROM manufacturer WHERE slug=%s",
        (slug,)).fetchone()
    if row is None or row["body"] is None:
        return NOT_IMPORTED, {}
    db_body = row["body"]
    if db_body == file_body:
        return IN_SYNC, {"rev": row["playbook_rev"]}

    hist = conn.execute(
        "SELECT rev, body FROM manufacturer_playbook_revision "
        "WHERE manufacturer_id=%s ORDER BY rev", (row["id"],)).fetchall()
    matched = [h["rev"] for h in hist if h["body"] == file_body]
    detail = {
        "rev": row["playbook_rev"],
        "delta": body_delta(file_body, db_body),
        "db_body": db_body,
    }
    if matched:
        detail["matches_rev"] = matched[0]
        return FILE_STALE, detail
    return DIVERGED, detail


def drift(conn, *, dir_path=None) -> list[tuple[str, str, dict]]:
    """`(slug, state, detail)` for every playbook file, plus any DB row whose
    file has gone. Sorted, so two runs on an unchanged tree read identically."""
    # The DIRECTORY, explicitly, never `dir_path=None`. Every caller of this
    # runs with a source set (`set_source`, once per process), and `load_raw`
    # with no dir_path returns ROWS in that case -- so a None default compared
    # the database against itself and reported everything in sync, which is
    # exactly the silence this command exists to break (caught 2026-08-31 by
    # editing a file and being told nothing had changed).
    files = playbooks_mod.load_raw(
        dir_path=dir_path if dir_path is not None else playbooks_mod.PLAYBOOKS_DIR)
    out = [(slug, *classify_drift(conn, slug, playbooks_mod.body_of(raw)))
           for slug, raw in sorted(files.items())]
    known = {slug for slug, _, _ in out}
    rows = conn.execute(
        "SELECT slug FROM manufacturer "
        " WHERE slug IS NOT NULL AND body IS NOT NULL ORDER BY slug").fetchall()
    out.extend((r["slug"], DB_ONLY, {}) for r in rows if r["slug"] not in known)
    return out


def seed(conn, *, loaded=None, raw=None, dir_path=None,
         adopt: frozenset[str] | None = None) -> SeedStats:
    """Write the entity tables. Caller owns the transaction (and the rollback,
    for a dry run).

    Validates the playbooks FIRST and writes nothing on a conflict, for the same
    reason `sync_aliases` does: two playbooks claiming one code or one name is an
    authoring error that would map items to the wrong manufacturer, and it must
    not be half-applied.

    **This reads FILES, always, and that is deliberate.** From slice 2 on,
    `load_playbooks()` with a source set returns what is already in the
    database, so a seed that called it would read its own output: the import
    would report a clean run and a file edit would never land -- a silent no-op,
    not a crash. `dir_path` is threaded to both loaders so the direction of this
    import stays files -> database no matter what the process has configured.
    """
    # An EXPLICIT directory, never None: both loaders take None to mean "use
    # the configured source", which after `set_source` is the database this
    # import writes to.
    from_dir = dir_path if dir_path is not None else playbooks_mod.PLAYBOOKS_DIR
    if loaded is None:
        loaded = playbooks_mod.load_playbooks(from_dir)
    if raw is None:
        raw = playbooks_mod.load_raw(from_dir)
    vendor_rows = reconcile.load_vendor_master(conn)
    # A fresh database has no `vendor_master`, and `manufacturer_bc_code` is
    # foreign-keyed to it (049), so seeding here would die mid-write with a raw
    # ForeignKeyViolation naming a code rather than the missing import. Found
    # by `scripts/bringup-rehearsal.sh` on its first run: nothing anywhere said
    # the master had to load first, because every test seeds it in a fixture.
    #
    # Refusal, not recovery, and the alternatives were weighed. Importing the
    # master here would bypass its own gate -- that command is dry-run-first
    # and needs `--allow-renames`, because re-pointing a code after its items
    # are grouped splits a manufacturer with no repair path. Inventing rows
    # would fabricate BC data inside BC's mirror. Skipping the unbacked codes
    # would seed manufacturers that match nothing and report success, which is
    # the failure this guard exists to prevent.
    if not vendor_rows:
        claimed = sorted({bc.code for pb in loaded for bc in pb.bc_codes})
        if claimed:
            raise MissingVendorMaster(
                f"vendor_master is empty and {len(claimed)} playbook BC code(s) "
                f"reference it ({', '.join(claimed[:5])}"
                f"{', ...' if len(claimed) > 5 else ''}). Import BC's "
                "manufacturer master first:\n"
                "  dentalia vendor-master --file imports/Proizvajalci.xlsx --apply"
            )
    vendor = {r.code: r.name for r in vendor_rows}
    brands: dict[str, frozenset[str]] = {}
    for code, name in vendor.items():
        if name:
            brands[name] = brands.get(name, frozenset()) | {code}
    playbooks_mod.validate(loaded, brands=brands)

    entities = reconcile._entities(vendor, loaded)

    by_slug = {pb.slug: pb for pb in loaded}
    stats = SeedStats(entities=len(entities))

    for e in entities:
        if not e.canonical:                      # None, or the empty string
            stats.skipped_unnamed += 1
            continue
        stats.named += 1

        mfr_id = _upsert_manufacturer(conn, stats, e.canonical, e.slug)
        pb = by_slug.get(e.slug) if e.slug else None

        # `source='playbook'` marks the codes a HUMAN named, not every code the
        # union-find pulled into the entity. A BC refresh may re-point the
        # derived ones; it may never re-point an authored one. Same distinction
        # `manufacturer_alias.source` draws (017).
        authored = {bc.code for bc in pb.bc_codes} if pb else set()
        for code in e.codes:
            _link_code(conn, stats, DEFAULT_CODE_SOURCE, code, mfr_id,
                       "playbook" if code in authored else "vendor-master")

        _link_name(conn, stats, e.canonical, mfr_id, "canonical")
        if pb:
            for alias in pb.aliases:
                _link_name(conn, stats, alias, mfr_id, "alias")
            if e.slug in raw:
                _write_body(conn, stats, mfr_id, e.slug, raw[e.slug], adopt)
                _write_contacts(conn, stats, mfr_id, e.slug, raw[e.slug], adopt)

    stats.orphan_groups = _orphan_groups(conn)
    stats.alias_rows = conn.execute(
        "SELECT count(*) AS n FROM manufacturer_alias").fetchone()["n"]
    return stats


def render(stats: SeedStats, *, dry_run: bool = False) -> list[str]:
    head = "would write" if dry_run else "wrote"
    out = [
        f"entities: {stats.entities} ({stats.named} named, "
        f"{stats.skipped_unnamed} unnamed and skipped)",
        f"{head}: manufacturer {stats.manufacturers_inserted} new / "
        f"{stats.manufacturers_updated} slug attached / "
        f"{stats.manufacturers_unchanged} unchanged",
        f"{head}: bc_code {stats.codes_inserted} new / {stats.codes_unchanged} unchanged",
        f"{head}: name {stats.names_inserted} new / {stats.names_unchanged} unchanged",
        f"{head}: body {stats.bodies_inserted} new / "
        f"{stats.bodies_unchanged} unchanged",
        f"{head}: contacts {stats.contacts_seeded} seeded / "
        f"{stats.contacts_unchanged} unchanged",
        *([f"{head}: body {stats.bodies_adopted} ADOPTED from file"]
          if stats.bodies_adopted else []),
    ]
    if stats.conflicts:
        out.append(f"\nCONFLICTS ({len(stats.conflicts)}) -- left untouched, resolve by hand:")
        for subject, existing, intended in stats.conflicts:
            out.append(f"  {subject}: has {existing}, seed wanted {intended}")
    # The bring-up half-step. This command writes the ENTITY tables and no
    # aliases; `playbooks sync` writes the projection RESOLVE and GATE read. On
    # a fresh database the two are done in sequence, and stopping here leaves a
    # registry that reports success everywhere and matches nothing -- measured
    # 2026-09-03 by `scripts/bringup-rehearsal.sh`: 381 manufacturers, 0
    # aliases, every group unnamed. Only shown at zero, so a steady-state run
    # stays quiet.
    if stats.named and not stats.alias_rows:
        out.append(
            "\nmanufacturer_alias is EMPTY — this command does not write it.\n"
            "  RESOLVE and GATE read that projection, so until it is written "
            "every group resolves unnamed:\n"
            "    dentalia playbooks sync"
        )
    if stats.orphan_groups:
        total = sum(n for _, n in stats.orphan_groups)
        out.append(
            f"\n{len(stats.orphan_groups)} item_group manufacturer value(s) have no "
            f"manufacturer row ({total} group(s)):"
        )
        for name, n in stats.orphan_groups:
            out.append(f"  {name!r}: {n} group(s)")
        out.append(
            "  These are groups resolved under a name no entity carries -- an "
            "empty manufacturer, or an alias repoint that never re-resolved "
            "(`playbooks sync` reports the same thing as orphaned_groups). "
            "Fix with `dentalia regroup`, not by inventing rows here."
        )
    return out
