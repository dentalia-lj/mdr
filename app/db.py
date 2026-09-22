"""Database access + migration runner (psycopg 3, sync, raw SQL — no ORM).

One Postgres holds queue + registry + fetch ledger + audit log, so the same
connection can claim a job, write its result, and append audit in one
transaction (CLAUDE.md architecture). This module owns two things only:

  * `connect()`        — the connection helper (dict rows, explicit tx by default)
  * `run_migrations()` — apply the numbered `migrations/*.sql` files in order,
                         each in its own transaction, tracked in `schema_migrations`.

The migration files themselves are pure domain schema; the tracking table lives
here on purpose, and no migration creates it.
"""

from __future__ import annotations

import pathlib

import psycopg
from psycopg.rows import dict_row

from app.config import load_config

# migrations/ sits next to app/ at the repo root.
MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "migrations"


def connect(url: str | None = None, *, autocommit: bool = False) -> psycopg.Connection:
    """Open a psycopg 3 connection with dict rows.

    Defaults to the configured DATABASE_URL. `autocommit=False` (the default)
    leaves transaction control to the caller — queue claim/finish/fail wrap
    their work in a single transaction. The migration runner uses
    `autocommit=True` so its per-file `transaction()` blocks are the only
    transactions in play.
    """
    if url is None:
        url = load_config().connection.database_url
    return psycopg.connect(url, autocommit=autocommit, row_factory=dict_row)


class OutOfOrderMigration(RuntimeError):
    """A pending migration sorts below one already applied — raised only under
    `run_migrations(strict=True)`; otherwise the same condition is a warning."""


def _migration_files(migrations_dir: pathlib.Path) -> list[pathlib.Path]:
    """The `*.sql` files, ascending by filename (001_, 002_, ...)."""
    return sorted(p for p in migrations_dir.glob("*.sql") if p.is_file())


def _out_of_order(disk: list[str], done: set[str]) -> list[str]:
    """Pending migrations that sort BELOW the highest already-applied one.

    Such a file is applied here after migrations it precedes on disk, so a
    fresh database will apply it in a different relative order than this one
    did — the one divergence a numbered-migration convention exists to prevent.

    Deliberately NOT a duplicate-prefix check. Ordering is the property that
    matters and a duplicate number is only one way to break it: measured on the
    dev database 2026-08-27, `032_stated_class` and `033_item_class_check` were
    applied after `034_item_document_production_expiry` with no collision
    anywhere in sight, while 18 of the repo's 20 branches carry the
    unrenameable `014_evidence_rev` / `014_upload` pair that a prefix check
    would refuse forever.

    Empty when nothing is pending (a fully-migrated database has nothing left
    to judge) and when nothing is applied (a fresh database defines the order).
    The bar comes from `done`, not from disk, so an unapplied high number never
    makes lower pending files look late; an applied file since deleted from
    disk still counts, since the database applied it. Returns disk order, not
    set order, because a human reads this to decide what to renumber.
    """
    if not done:
        return []
    bar = max(done)
    return [name for name in disk if name not in done and name < bar]


def _ensure_tracking(conn: psycopg.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename   text        PRIMARY KEY,
            applied_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def applied_migrations(conn: psycopg.Connection) -> set[str]:
    """Filenames already applied (creates the tracking table if absent)."""
    _ensure_tracking(conn)
    rows = conn.execute("SELECT filename FROM schema_migrations").fetchall()
    return {r["filename"] for r in rows}


def run_migrations(
    conn: psycopg.Connection,
    migrations_dir: pathlib.Path | None = None,
    *,
    strict: bool = False,
) -> list[str]:
    """Apply pending migrations in ascending filename order.

    Each file is applied whole (multi-statement, no parameters) inside one
    transaction together with its `schema_migrations` bookkeeping row, so a
    file either fully applies or not at all. Already-applied files are skipped,
    making this idempotent. Requires an autocommit connection (see `connect`).

    `strict` decides what an out-of-order pending file means (`_out_of_order`).
    Default False — warn and apply. A branch landing a migration numbered below
    master's maximum is the routine cost of parallel sessions in this repo
    (Denis, 2026-08-27: "we fix it when merging"), and it is fixed at merge
    time, which is precisely when `migrate` still has to run. Pass True for a
    database that cannot legitimately be out of order — a fresh one, as in
    `tests/conftest.py` and CI — where the condition is a real defect. Strict
    refuses before applying anything, so a rejected run leaves no partial state.

    Returns the filenames applied by this call (empty if already up to date).
    """
    migrations_dir = migrations_dir or MIGRATIONS_DIR
    done = applied_migrations(conn)
    files = _migration_files(migrations_dir)

    late = _out_of_order([p.name for p in files], done)
    if late:
        detail = (
            f"migration(s) {', '.join(late)} sort below {max(done)}, which this "
            f"database has already applied: a fresh database would apply them "
            f"in a different order than this one did"
        )
        if strict:
            raise OutOfOrderMigration(detail)
        print(f"WARNING: {detail}")

    newly: list[str] = []
    for path in files:
        if path.name in done:
            continue
        sql = path.read_text()
        with conn.transaction():
            conn.execute(sql)
            conn.execute(
                "INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,)
            )
        newly.append(path.name)
    return newly


def migrate(
    url: str | None = None,
    migrations_dir: pathlib.Path | None = None,
    *,
    strict: bool = False,
) -> list[str]:
    """Open an autocommit connection, run pending migrations, close.

    `strict` is passed straight through to `run_migrations` — see there for
    when a caller should set it."""
    with connect(url, autocommit=True) as conn:
        return run_migrations(conn, migrations_dir, strict=strict)


if __name__ == "__main__":  # pragma: no cover
    applied = migrate()
    print(f"applied {len(applied)} migration(s): {applied}" if applied else "up to date")
