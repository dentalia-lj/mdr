"""Pytest fixtures: a real Postgres test database (CLAUDE.md: no mocking Postgres).

Creates a throwaway, per-run database in the already-running container, applies
all migrations once per session, and hands tests connections against it. The
queue tables are truncated after each test for isolation.

Point at a different server with DENTALIA_TEST_ADMIN_URL / DENTALIA_TEST_URL.
"""

from __future__ import annotations

import os
import pathlib
import re
import secrets
import uuid
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

from app import db

# Local seed corpus (gitignored). Corpus-dependent tests skip when it's absent
# (e.g. CI), so the suite stays green off Denis's machine.
CORPUS_ROOT = pathlib.Path(
    os.environ.get("DENTALIA_CORPUS_ROOT", "imports/dentalia-sftp")
)


@pytest.fixture
def corpus_pdf():
    """Return an absolute path to a corpus PDF, or skip if the corpus is absent."""

    def _get(relpath: str) -> str:
        p = (CORPUS_ROOT / relpath).resolve()
        if not p.exists():
            pytest.skip(f"corpus file not present: {p}")
        return str(p)

    return _get


# Committed fixture subset (tests/fixtures/corpus/, tracked in git) — always
# present, so the extract-tier tests run in CI, not just on Denis's machine.
FIXTURE_ROOT = pathlib.Path(__file__).parent / "fixtures" / "corpus"


@pytest.fixture
def fixture_pdf():
    """Return an absolute path to a committed fixture PDF (manifest entry or
    '<MANU>/<name>' relpath). Unlike ``corpus_pdf`` this never skips — the files
    are checked into the repo."""

    def _get(entry_or_relpath) -> str:
        if isinstance(entry_or_relpath, dict):
            p = FIXTURE_ROOT / entry_or_relpath["manu"] / entry_or_relpath["name"]
        else:
            p = FIXTURE_ROOT / entry_or_relpath
        p = p.resolve()
        if not p.exists():
            raise FileNotFoundError(f"committed fixture missing: {p}")
        return str(p)

    return _get

# --------------------------------------------------------------------------- #
# Committed-corpus scans, cached for the whole session.
#
# `tools.corpus.scan()` re-parses all 24 committed fixture PDFs (13 MB) and costs
# ~12s a call. Three modules were paying it eleven times over -- 141s in
# tests/test_tools_cli.py alone, and 179s across the three, which was 24% of the
# suite's wall clock for scans that are identical by construction: same root, no
# other input, and `scan` is asserted deterministic in test_tools_corpus.py.
#
# Session scope, not module: the whole point is sharing ACROSS the three modules.
# The determinism tests deliberately do NOT take these fixtures -- a cached value
# cannot demonstrate that two independent runs agree, which is the property they
# exist to check.
#
# `corpus_rows` hands out one shared mutable list. Every consumer reads it; none
# may mutate it. That constraint is not new (the module-scoped `rows` fixture in
# test_tools_corpus.py already shared one across 25 tests), only wider.
# --------------------------------------------------------------------------- #

# The `--date` every cached CLI run is stamped with. Exposed through the
# fixtures rather than repeated per module, so an output filename asserted in a
# test cannot drift from the run that produced it.
CLI_RUN_DATE = "2026-07-24"


@pytest.fixture(scope="session")
def corpus_rows():
    """`tools.corpus.scan()` over the committed fixture corpus, scanned once."""
    from tools import corpus

    return corpus.scan(FIXTURE_ROOT)


@pytest.fixture(scope="session")
def corpus_summary(corpus_rows):
    """`tools.corpus.aggregate()` over `corpus_rows`, aggregated once."""
    from tools import corpus

    return corpus.aggregate(corpus_rows)


@pytest.fixture(scope="session")
def corpus_support():
    """`tools.corpus.support_files()` over the committed fixture corpus."""
    from tools import corpus

    return corpus.support_files(FIXTURE_ROOT)


def _cli_run(subcommand: str, out_dir) -> dict:
    from tools.__main__ import main

    code = main([subcommand, "--root", str(FIXTURE_ROOT), "--out", str(out_dir),
                 "--date", CLI_RUN_DATE])
    return {"code": code, "out": out_dir, "date": CLI_RUN_DATE}


@pytest.fixture(scope="session")
def corpus_cli_run(tmp_path_factory):
    """One `tools corpus` CLI invocation; its exit code and output directory."""
    return _cli_run("corpus", tmp_path_factory.mktemp("corpus-cli"))


@pytest.fixture(scope="session")
def claims_cli_run(tmp_path_factory):
    """One `tools claims` CLI invocation; its exit code and output directory."""
    return _cli_run("claims", tmp_path_factory.mktemp("claims-cli"))


ADMIN_URL = os.environ.get(
    "DENTALIA_TEST_ADMIN_URL",
    "postgresql://dentalia:dentalia@localhost:5432/dentalia",
)

# Per-run database name — the single source of truth for what the DROP/CREATE
# below targets and what every connection URL points at. A fixed name here
# (formerly the literal "dentalia_test") meant two concurrent pytest processes
# both landed on the same database: the second run's session-scope
# `DROP ... WITH (FORCE)` forcibly terminates the first run's connections and
# recreates the database out from under it mid-suite, producing "ERROR at
# setup" or a mid-run failure indistinguishable from a real regression.
#
# That was first fixed with `os.getpid()`, which DID separate two shells on the
# host and DID NOT separate anything in Docker: `docker compose run test` makes
# pytest PID 1 in its own namespace, so every containerized run — the documented
# way to run this suite — was back on one shared name, `dentalia_test_1`.
# Measured 2026-08-17: a full run reported 19 failed / 526 errors, every one of
# them `FATAL: database "dentalia_test_1" does not exist`, while each failing
# file passed alone. Two runs of identical code disagreed (23, then 28, then 14
# failures), which is the tell — but the numbers look exactly like a real
# regression, and cost most of a session to chase.
#
# A random suffix has no namespace to be flattened by. The pid stays in the name
# purely because it is useful when reading `\l` output on a leaked database;
# uniqueness rests entirely on the random half. `PYTEST_XDIST_WORKER` is folded
# in so xdist workers — separate processes, each running the session-scoped
# fixture below — get their own databases rather than fighting over one.
# DENTALIA_TEST_DB still overrides when a fixed, predictable name is wanted;
# setting it in a shared environment re-creates the collision by hand.
_XDIST_WORKER = os.environ.get("PYTEST_XDIST_WORKER", "")
TEST_DB = os.environ.get(
    "DENTALIA_TEST_DB",
    f"dentalia_test_{_XDIST_WORKER}{'_' if _XDIST_WORKER else ''}"
    f"{os.getpid()}_{secrets.token_hex(4)}",
)
if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", TEST_DB):
    raise ValueError(
        f"DENTALIA_TEST_DB={TEST_DB!r} must be a valid unquoted Postgres "
        "identifier (letters, digits, underscore; not starting with a digit) "
        "-- it is interpolated directly into DROP/CREATE DATABASE"
    )


def _with_db(url: str, dbname: str) -> str:
    """Rebuild `url` with its database segment replaced by `dbname`.

    Every connection URL below is built through this from TEST_DB, so the
    DROP/CREATE target and every URL a test connects through are structurally
    unable to disagree -- the two used to be independently defaulted (one a
    module constant, one an env-var default), which is exactly what let them
    drift apart.
    """
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{dbname}"))


TEST_URL = _with_db(
    os.environ.get(
        "DENTALIA_TEST_URL",
        "postgresql://dentalia:dentalia@localhost:5432/dentalia_test",
    ),
    TEST_DB,
)

# The write-less `dentalia_api` role's URL against the same per-run database
# (compose sets DENTALIA_TEST_API_URL for the `test` service; test_web.py uses
# it to exercise that role -- Invariant 1 is asserted structurally there).
# Derived the same way as TEST_URL so it can never point at a different
# database.
TEST_API_URL = _with_db(
    os.environ.get(
        "DENTALIA_TEST_API_URL",
        "postgresql://dentalia_api:dentalia_api@localhost:5432/dentalia_test",
    ),
    TEST_DB,
)


# --------------------------------------------------------------------------- #
# Per-test reset.
#
# This runs after all but a handful of tests, so its cost is the suite's cost.
# It used to be six `TRUNCATE ... CASCADE` statements and measured 320-400 ms a
# test on a bind-mounted Postgres -- ~350s of a 761s run, the single largest
# line item in the suite. The cause is not commit or WAL (batching the six into
# one transaction changed nothing, and so did `synchronous_commit=off`):
# TRUNCATE recreates a relfilenode per relation, and 20 tables plus their
# indexes is ~40 file unlink/create pairs against the data directory. On tmpfs
# the same statement costs 19 ms, which is the tell.
#
# DELETE does the same job here for 7 ms. The tables hold a handful of rows per
# test, so the usual TRUNCATE-is-faster reasoning is inverted; and there are no
# user triggers in this schema, so the TRUNCATE/DELETE trigger distinction
# cannot bite.
#
# The table list is NOT the old TRUNCATE list. `CASCADE` silently also cleared
# every table with an FK into one of them, and every FK here is ON DELETE NO
# ACTION, so DELETE must name them: `evidence`, `fetch_log`, `renewal_request`,
# `renewal_request_document` and `email_draft` were reached implicitly before
# and are listed explicitly now. The order is children-before-parents, derived
# from pg_constraint rather than by hand; `document` is self-referencing (the
# supersession chain), which is safe because NO ACTION is checked at end of
# statement and one DELETE removes every row at once.
#
# `test_reset_clears_every_table_the_old_cascade_reached` in tests/test_queue.py
# re-derives this closure from the live schema, so a migration that adds a table
# referencing one of these fails there rather than leaking rows into later tests.
# --------------------------------------------------------------------------- #

# Children first. Regenerate with the query in the test named above.
_RESET_TABLES = (
    "data_anomaly", "discovery_log", "domain_lease", "email_draft", "email_poll_log",
    "evidence", "fetch_log", "grouping_suggestion", "item_document",
    "item_group_member", "job", "manual_task", "manufacturer_alias",
    # Before `vendor_master`: it FKs to it (migration 049), so deleting the
    # mirror first would fail on any row a seed test left behind.
    "manufacturer_bc_code",
    "renewal_request_document", "scheduler_run", "service_heartbeat",
    "import_inbox", "upload_inbox",
    "vendor_master", "item_group", "item_mirror", "renewal_request",
    # EUDAMED (S2.3 stage 2). Added when the SRN confirm queue's route tests
    # became the first in this suite to COMMIT rows into these tables: the
    # TestClient holds a separate connection, so a rolled-back seed is
    # invisible to the app. `manufacturer_srn` before `manufacturer` -- these
    # are plain DELETEs in list order and the child carries the FK.
    #
    # It also has to precede `document`, which is why it moved ABOVE it on
    # 2026-09-09: migration 066 gave it `source_doc_id`, an FK to `document`
    # for text-mined SRNs, so it now has TWO parents in this list and must be
    # deleted before both. `test_reset_clears_every_table_the_old_cascade_reached`
    # caught the ordering the same run the column landed -- a new FK on an
    # EXISTING table moves it in this list exactly as a new table would add to
    # it, which is the case CLAUDE.md's "new leaf table" warning does not name.
    "manufacturer_srn",
    "document",
    "eudamed_certificate", "eudamed_mirror",
    # `eudamed_sweep_state` carries an FK to `manufacturer` (task 9), so it
    # must precede it here the same way `manufacturer_srn` does above.
    "eudamed_sweep_state",
    # The playbook half of the same parent (049/050). It used to be excluded on
    # the grounds that the closure could not reach `manufacturer` -- true only
    # while `manufacturer` was not itself a seed. Migration 042 made it one, so
    # every one of its children is now IN the closure, and leaving these two out
    # both fails the guard test and makes the `DELETE FROM manufacturer` below
    # raise on the first committed name row (neither FK cascades).
    "manufacturer_name", "manufacturer_playbook_revision",
    "manufacturer",
    # `playbook_probe` (059): a LEAF -- `slug` and `via_job` are both soft refs,
    # deliberately, so a probe outlives a rename and a purged job. That means no
    # FK closure can ever reach it and it has to be named here explicitly, the
    # same reason `data_anomaly` and `service_heartbeat` are. It is also a seed
    # in test_queue's guard for that reason; CLAUDE.md warns that a new leaf
    # table slips past the guard without one.
    "playbook_probe",
    # `onboarding_state` (060): a leaf for the same reason -- `canonical_name`
    # is a SOFT ref so a triage note outlives a rename, which is exactly what
    # puts it out of every FK closure's reach.
    "onboarding_state",
    # Six FK-less leaves written by extraction, batching, GATE's audit and the
    # robots reader, absent from this list until the 2026-09-04 verification
    # pass counted them: nothing references them and they reference nothing
    # (`audit_log` by design, invariant 10), so no closure could ever reach
    # them and only naming them here clears them. `schema_migrations` is the
    # one FK-less table that must NOT appear here.
    "audit_log", "batch_ref", "document_text", "extraction_attempt",
    "extraction_cost", "robots_cache",
    # `crawl_link_rank` (061): a leaf keyed on a URL and a hash, no FK either
    # way, same reason as the six above.
    "crawl_link_rank",
    # `bc_push_log` (063): a leaf for the same reason -- `via_job` is a soft ref
    # by invariant 10 and `item_ref` is plain text, so nothing links it to the
    # closure and only naming it here clears what a push test wrote.
    "bc_push_log",
    # `alert_state` (064): a leaf keyed on a condition string, no FK either
    # way -- an open alert must outlive the job and the service it names.
    "alert_state",
)

# The three tables whose ids tests assert on directly; `RESTART IDENTITY` used
# to do this as part of the TRUNCATE.
_RESET_SEQUENCES = ("job", "import_inbox", "upload_inbox", "email_poll_log")

_RESET_SQL = "; ".join(
    [f"DELETE FROM {t}" for t in _RESET_TABLES]
    + [f"ALTER SEQUENCE {t}_id_seq RESTART" for t in _RESET_SEQUENCES]
)


# Serialises CREATE DATABASE + migrate across every concurrent pytest run on one
# Postgres cluster. Arbitrary constant; any run using this conftest agrees on it.
_MIGRATE_LOCK = 4_871_209_331


@pytest.fixture(scope="session")
def test_db_url() -> str:
    """Fresh test DB with all migrations applied; dropped at session end.

    The create-and-migrate is taken under a cluster-shared advisory lock. Each
    run already gets its own DATABASE, but a ROLE is cluster-global, and
    `migrations/007_roles_grants.sql` creates `dentalia_api` while
    `008_api_role_dev_password.sql` ALTERs its password -- so two runs migrating
    at the same instant collide in pg_authid. Observed 2026-08-25 as
    `psycopg.errors.InternalError_: tuple concurrently updated` out of
    `db.migrate`, which kills the session fixture and turns into ~1100 errors in
    a suite that is green when run alone: a failure that reads exactly like a
    real regression and belongs to neither run.

    The lock is held on the ADMIN connection, not the test database's -- advisory
    locks are scoped per database, so a lock taken inside a per-run database
    would exclude nothing. ADMIN_URL is the one database every run on this
    cluster shares. Runs pointed at different clusters (a tmpfs override on
    another port) never contend in the first place, and take their own lock
    there harmlessly. Cost when uncontended is one round trip; when contended,
    the ~1s another run needs to finish migrating.
    """
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute("SELECT pg_advisory_lock(%s)", (_MIGRATE_LOCK,))
        try:
            admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
            admin.execute(f"CREATE DATABASE {TEST_DB}")
            # strict: this database is built from scratch every session, so a
            # migration sorting below an already-applied one cannot be a merge
            # in progress -- it is a defect. tests/test_db.py asserts this stays.
            db.migrate(TEST_URL, strict=True)
        except Exception:
            # A migration failure raises before yield, so the teardown below
            # never runs — drop the half-built database here or it leaks.
            admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
            raise
        finally:
            admin.execute("SELECT pg_advisory_unlock(%s)", (_MIGRATE_LOCK,))
    try:
        yield TEST_URL
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")


@pytest.fixture
def connect_test(test_db_url):
    """Factory opening connections to the test DB (for multi-worker cases).

    All opened connections are closed and the queue tables truncated after the
    test, so each test starts from an empty queue.
    """
    opened: list[psycopg.Connection] = []

    def _open(*, autocommit: bool = False) -> psycopg.Connection:
        c = db.connect(test_db_url, autocommit=autocommit)
        opened.append(c)
        return c

    yield _open

    for c in opened:
        try:
            c.close()
        except Exception:
            pass
    with db.connect(test_db_url, autocommit=True) as cleaner:
        cleaner.execute(_RESET_SQL)


@pytest.fixture
def conn(connect_test):
    """A single caller-controlled-transaction connection (autocommit off)."""
    return connect_test(autocommit=False)


@pytest.fixture
def seeded_doc(conn):
    """Factory building the full join path the certificate views rely on:

        document -> item_document (status='production') -> item_group_member
        -> item_group.canonical_manufacturer

    That is the same path `email_request.lapsing_for_manufacturer` uses, and
    `held_certificate` (migration 043) reaches the manufacturer through it too
    -- so a test seeding through anything shorter (e.g. `document` alone)
    would not exercise the join the views actually perform.

    Mirrors `_seed_grouped_expiring_doc` in tests/test_web.py (item_mirror /
    item_group / item_group_member / document / item_document column sets),
    trimmed to keyword arguments the caller actually varies. Returns the new
    `doc_id`. Uses the same `conn` the test itself queries with, so no commit
    is needed for visibility -- inserts are read back inside the same
    transaction and rolled back by connection close like every other `conn`
    fixture in this suite.
    """

    def _seed(*, type="EC", cert_number=None, canonical, status="production",
              link_status="production", regulation="MDR",
              validity_from="2022-01-01", validity_to="2031-01-01"):
        suffix = uuid.uuid4().hex[:12]
        item_ref = f"SEED-{suffix}"
        conn.execute(
            "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, mfr_ref, "
            "md_flag, catalogue, updated_at) VALUES (%s,'Widget','test',%s,TRUE,'LJ',now())",
            (item_ref, f"REF-{suffix}"),
        )
        group_id = conn.execute(
            "INSERT INTO item_group (canonical_manufacturer) VALUES (%s) "
            "RETURNING group_id",
            (canonical,),
        ).fetchone()["group_id"]
        conn.execute(
            "INSERT INTO item_group_member (group_id, item_ref, match_basis) "
            "VALUES (%s,%s,'manual')",
            (group_id, item_ref),
        )
        content_hash = f"h-seed-{suffix}"
        doc_id = conn.execute(
            "INSERT INTO document (type, regulation, validity_from, validity_to, "
            "status, content_hash, archive_url, coverage_scope, cert_number) "
            "VALUES (%s,%s,%s,%s,%s,%s,'/archive/seed.pdf','group',%s) "
            "RETURNING doc_id",
            (type, regulation, validity_from, validity_to, status, content_hash,
             cert_number),
        ).fetchone()["doc_id"]
        conn.execute(
            "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
            "VALUES (%s,%s,'ref-list',%s)",
            (item_ref, doc_id, link_status),
        )
        return doc_id

    return _seed
