"""Migration 008 must not overwrite a password someone has set.

`dentalia_api` is a cluster-global role and 008 runs once per DATABASE, so every
fresh database migrated in the same cluster runs it again: `schema-drift` (on
every `scripts/deploy.sh` and `--check`), this suite's own test database, and
`scripts/bringup-rehearsal.sh`. Until 2026-09-18 it ran an unconditional
`ALTER ROLE ... PASSWORD 'dentalia_api'`, so on a client server the first deploy
after the operator set a real password put the published literal back and cut
`web` off. Measured on dev: the stored verifier changed across one
`schema-drift` run.

Both tests run 008's own text inside a transaction and roll it back, so the
shared role is never changed for the stack or for tests running alongside.
"""
from __future__ import annotations

import pathlib

import psycopg

_SQL = (pathlib.Path(__file__).resolve().parents[1]
        / "migrations" / "008_api_role_dev_password.sql").read_text()

_VERIFIER = "SELECT rolpassword FROM pg_authid WHERE rolname = 'dentalia_api'"


def test_a_password_that_is_already_set_survives_a_rerun(test_db_url):
    with psycopg.connect(test_db_url) as c:
        before = c.execute(_VERIFIER).fetchone()[0]
        assert before is not None
        c.execute(_SQL)
        after = c.execute(_VERIFIER).fetchone()[0]
        c.rollback()
    assert after == before


def test_a_role_without_a_password_gets_the_dev_literal(test_db_url):
    """The out-of-the-box path: 007 creates the role with no password, and a
    fresh cluster must still come up with a web role that can log in."""
    with psycopg.connect(test_db_url) as c:
        c.execute("ALTER ROLE dentalia_api PASSWORD NULL")
        c.execute(_SQL)
        after, can_login = c.execute(
            "SELECT rolpassword, rolcanlogin FROM pg_authid WHERE rolname = 'dentalia_api'"
        ).fetchone()
        c.rollback()
    assert after is not None and after.startswith("SCRAM-SHA-256$")
    assert can_login
