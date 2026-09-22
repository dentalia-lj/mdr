"""Does this database match what the migrations in the tree would build?

A long-lived database and the tree drift apart silently, and the test suite
cannot see it: tests build the schema from scratch every run, so they only ever
exercise the tree's version. `043_eudamed_cert_views.sql` was applied
2026-08-26 and edited in place the next day; `schema_migrations` is keyed on
FILENAME, so the edit never re-ran and the dev database served the pre-fix views
for 8 days while the test pinning the corrected shape passed the whole time.

The obvious guard -- record a content hash per migration at apply time and
compare -- was considered and rejected. It only sees ONE cause (a file edited
after it was applied), it cannot be backfilled honestly (hashing the files as
they stand today would record the CORRECTED text against an already-drifted
database and launder exactly the bug that motivated it), and it is blind to a
hand-run `ALTER`, a partly-restored dump, or an object created directly in
psql. This check compares the schemas themselves, so it does not care how they
came to differ.

Cost is one scratch database, created and dropped per run (~10s for 58
migrations). It is an on-demand check and a deploy step, not something in the
request path.

`pg_dump --schema-only | diff` is NOT a substitute and was measured: it reports
~100 lines of difference between two identical schemas, because it orders
objects by dependency and stamps a per-session `\\restrict` token. The
comparison has to be per-object and keyed by name, which is what this does.
"""

from __future__ import annotations

import dataclasses
import pathlib
import re
import secrets
import urllib.parse

import psycopg

from app import db

#: A name-keyed structural fingerprint of the `public` schema. Every row is
#: `kind|name|md5(definition)`, so two databases join on (kind, name) and
#: compare on the hash -- ordering-independent and OID-independent, which a
#: textual dump is not. Columns are compared individually rather than as a
#: whole table so a report can name the column that differs.
FINGERPRINT_SQL = """
WITH cols AS (
  SELECT 'column' AS kind,
         c.relname || '.' || a.attname AS name,
         format_type(a.atttypid, a.atttypmod)
           || CASE WHEN a.attnotnull THEN ' NOT NULL' ELSE '' END
           || COALESCE(' DEFAULT ' || pg_get_expr(d.adbin, d.adrelid), '') AS def
    FROM pg_attribute a
    JOIN pg_class c ON c.oid = a.attrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
   WHERE n.nspname = 'public' AND c.relkind IN ('r','p') AND a.attnum > 0
     AND NOT a.attisdropped
), views AS (
  SELECT 'view', c.relname, pg_get_viewdef(c.oid, true)
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = 'public' AND c.relkind = 'v'
), idx AS (
  SELECT 'index', indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'
), cons AS (
  SELECT 'constraint', conrelid::regclass::text || '.' || conname,
         pg_get_constraintdef(oid)
    FROM pg_constraint WHERE connamespace = 'public'::regnamespace
), enums AS (
  SELECT 'enum', t.typname, string_agg(e.enumlabel, ',' ORDER BY e.enumsortorder)
    FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid
    JOIN pg_namespace n ON n.oid = t.typnamespace
   WHERE n.nspname = 'public' GROUP BY t.typname
), grants AS (
  SELECT 'grant', table_name || '.' || grantee,
         string_agg(privilege_type, ',' ORDER BY privilege_type)
    FROM information_schema.table_privileges
   WHERE table_schema = 'public' GROUP BY table_name, grantee
)
SELECT kind, name, md5(def) AS fingerprint
  FROM (SELECT * FROM cols UNION ALL SELECT * FROM views UNION ALL SELECT * FROM idx
        UNION ALL SELECT * FROM cons UNION ALL SELECT * FROM enums
        UNION ALL SELECT * FROM grants) x(kind, name, def)
 ORDER BY 1, 2
"""

#: Scratch database names are interpolated into CREATE/DROP DATABASE, which
#: take no parameters. Generated here and never taken from input, but validated
#: anyway so that stays true if someone adds a flag for it.
_SAFE_DB_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


#: Where this database's apply order diverged from the order a fresh database
#: would use. `_out_of_order` in `app/db.py` is the PROSPECTIVE guard -- it
#: refuses (or warns about) a pending file that sorts below one already
#: applied. Nothing looked backwards at what had already happened, which is
#: the question this answers.
APPLIED_ORDER_SQL = """
WITH o AS (
  SELECT filename,
         row_number() OVER (ORDER BY applied_at, filename) AS applied_pos,
         row_number() OVER (ORDER BY filename)             AS disk_pos
    FROM schema_migrations)
SELECT filename, applied_pos, disk_pos
  FROM o WHERE applied_pos <> disk_pos ORDER BY disk_pos
"""


@dataclasses.dataclass(frozen=True)
class OutOfOrder:
    """One migration this database applied at a different position than a fresh
    database would. History, not a defect to fix: you cannot re-apply in a
    different order. It matters because it means the two databases took
    different paths, so the schema comparison is the only thing that can show
    whether they still arrived at the same place."""

    filename: str
    applied_pos: int
    disk_pos: int

    def __str__(self) -> str:
        return (f"{self.filename:<45} applied at position {self.applied_pos}, "
                f"disk position {self.disk_pos}")


def applied_out_of_order(conn: psycopg.Connection) -> list[OutOfOrder]:
    """Migrations whose apply position differs from their filename position."""
    return [OutOfOrder(r["filename"], r["applied_pos"], r["disk_pos"])
            for r in conn.execute(APPLIED_ORDER_SQL).fetchall()]


@dataclasses.dataclass(frozen=True)
class Drift:
    """One object that differs. `status` is from the LIVE database's point of
    view: `extra` exists here and not in the tree, `missing` is the reverse."""

    kind: str
    name: str
    status: str  # "changed" | "missing" | "extra"

    def __str__(self) -> str:
        return f"{self.status:>7}  {self.kind:<10} {self.name}"


def fingerprint(conn: psycopg.Connection) -> dict[tuple[str, str], str]:
    """`(kind, name) -> md5(definition)` for every object in `public`."""
    return {
        (r["kind"], r["name"]): r["fingerprint"]
        for r in conn.execute(FINGERPRINT_SQL).fetchall()
    }


def compare(live: dict[tuple[str, str], str],
            tree: dict[tuple[str, str], str]) -> list[Drift]:
    """Pure set/hash comparison, so the reporting is testable without a database.

    Sorted by (kind, name) rather than by status: an operator reading this is
    looking for a particular object, not triaging by category.
    """
    out: list[Drift] = []
    for key in sorted(set(live) | set(tree)):
        kind, name = key
        if key not in tree:
            out.append(Drift(kind, name, "extra"))
        elif key not in live:
            out.append(Drift(kind, name, "missing"))
        elif live[key] != tree[key]:
            out.append(Drift(kind, name, "changed"))
    return out


def _admin_url(url: str, database: str) -> str:
    """The same connection, pointed at another database."""
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(parts._replace(path=f"/{database}"))


@dataclasses.dataclass(frozen=True)
class Report:
    """What one run found. `drift` decides the exit code; `order` is advisory --
    a divergent apply order is unfixable history, and reporting it as a failure
    forever would train an operator to ignore the command."""

    drift: list[Drift]
    order: list[OutOfOrder]
    scratch: str

    @property
    def ok(self) -> bool:
        return not self.drift


def check(url: str | None = None,
          migrations_dir: pathlib.Path | None = None,
          *, keep: bool = False) -> Report:
    """Build the tree's schema in a scratch database and diff it against `url`.

    The scratch database is dropped unless `keep`, including when the
    comparison raises -- a leaked database would be picked up as clutter by the
    very next run of this check.
    """
    if url is None:
        from app.config import load_config
        url = load_config().connection.database_url

    scratch = f"dentalia_schema_check_{secrets.token_hex(6)}"
    assert _SAFE_DB_NAME.match(scratch), scratch  # generated, never input

    admin = _admin_url(url, "postgres")
    with psycopg.connect(admin, autocommit=True) as adm:
        adm.execute(f"CREATE DATABASE {scratch}")

    try:
        with db.connect(_admin_url(url, scratch), autocommit=True) as fresh:
            db.run_migrations(fresh, migrations_dir)
            tree = fingerprint(fresh)
        with db.connect(url) as live_conn:
            live = fingerprint(live_conn)
            order = applied_out_of_order(live_conn)
        return Report(compare(live, tree), order, scratch)
    finally:
        if not keep:
            with psycopg.connect(admin, autocommit=True) as adm:
                adm.execute(f"DROP DATABASE IF EXISTS {scratch} WITH (FORCE)")
