"""`robots_refused.txt` becomes a table (slice 4, task 15, migration 051).

The reason this had to move with the directory, and not later: the list is
what `playbooks.validate()` checks a `kind:"direct"` source against, and since
slice 3b a non-dev authors direct sources in the UI. The web process could
read the file only because compose bind-mounted `./playbooks` into it. Drop
that mount and `load_robots_refused` finds nothing, returns an empty set BY
DESIGN -- absence must not raise, it is on DISCOVER's never-raises path -- and
the guard goes quietly inert in the one process where it now matters most.

A guard that fails open when a mount is removed is worse than no guard,
because nothing on screen would say so. These tests are the difference.
"""

from __future__ import annotations

import pytest

from app import playbooks as pb


@pytest.fixture(autouse=True)
def _no_source():
    pb.set_source(None)
    pb.clear_cache()
    yield
    pb.set_source(None)
    pb.clear_cache()


def _source(conn):
    class _Keep:
        def __enter__(self): return conn
        def __exit__(self, *a): return False
    return lambda: _Keep()


def _pb(slug, manufacturer, sources):
    return pb.Playbook(
        slug=slug, manufacturer=manufacturer,
        doc_sources=tuple(pb.DocSource(**s) for s in sources))


# --------------------------------------------------------------------------- #
# the migration
# --------------------------------------------------------------------------- #
def test_the_ruling_survived_the_move_with_its_reasons(conn):
    """Five hosts, four manufacturers, 822 items (Denis, 2026-08-20). The
    `note` is not decoration: it is the only record of WHY each host is
    listed, and a move that dropped it would leave five bare strings nobody
    can audit a year later."""
    rows = {r["host"]: r["note"] for r in conn.execute(
        "SELECT host, note FROM refused_host").fetchall()}

    assert set(rows) == {"kavo.widen.net", "media.amanngirrbach.com",
                         "pritidenta.com", "coltene.com", "media.coltene.com"}
    assert all(rows.values()), rows
    assert "Twitterbot" in rows["kavo.widen.net"]
    assert "ClaudeBot" in rows["coltene.com"]


def test_the_table_matches_the_file_it_replaced(conn):
    """The file stays in git as the authoring record, the same posture the 33
    playbook JSONs keep. While both exist they must not disagree."""
    from_file = pb.load_robots_refused(pb.PLAYBOOKS_DIR)
    from_db = {r["host"] for r in conn.execute(
        "SELECT host FROM refused_host").fetchall()}

    assert from_db == set(from_file)


def test_the_web_role_may_refuse_but_never_lift_a_refusal(conn, test_db_url):
    """SELECT only. Lifting a refusal means asking a manufacturer and getting
    a yes -- an operator ruling, not a click. Same posture as `vendor_master`."""
    import psycopg

    from app import db
    from tests.conftest import TEST_API_URL

    with db.connect(TEST_API_URL) as api:
        assert api.execute("SELECT count(*) AS n FROM refused_host").fetchone()["n"] == 5
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            api.execute("DELETE FROM refused_host WHERE host='coltene.com'")


# --------------------------------------------------------------------------- #
# the loader
# --------------------------------------------------------------------------- #
def test_with_a_source_the_list_comes_from_rows_not_the_filesystem(conn, monkeypatch):
    """The mount is gone; this is what replaces it."""
    monkeypatch.setattr(pb, "PLAYBOOKS_DIR", "/definitely/not/here")
    pb.set_source(_source(conn))

    assert "coltene.com" in pb.load_robots_refused()


def test_an_explicit_directory_still_wins_over_the_source(conn, tmp_path):
    """The same rule `load_playbooks` follows, and it is what lets
    `manufacturers seed` import files into the database it would otherwise be
    reading."""
    (tmp_path / pb.ROBOTS_REFUSED_FILE).write_text("only-in-the-file.example\n")
    pb.set_source(_source(conn))

    assert pb.load_robots_refused(tmp_path) == frozenset({"only-in-the-file.example"})


def test_a_direct_source_on_a_refused_host_is_still_refused_from_rows(conn):
    """End to end: the guard that would have gone inert when the mount was
    dropped, now armed from the table instead."""
    pb.set_source(_source(conn))
    bad = _pb("coltene", "COLTENE",
              [{"doc_type": "DoC", "kind": "direct",
                "url": "https://media.coltene.com/x.pdf"}])

    with pytest.raises(pb.RobotsRefused) as exc:
        pb.validate((bad,))

    assert "media.coltene.com" in str(exc.value)
    assert 'kind:"portal"' in str(exc.value)     # it names the way out


def test_a_portal_on_the_same_host_still_passes(conn):
    """The refusal is of FETCHING the host, not of naming it: a link a human
    clicks is exactly what the 2026-08-20 ruling permits."""
    pb.set_source(_source(conn))
    ok = _pb("coltene", "COLTENE",
             [{"doc_type": "DoC", "kind": "portal",
               "url": "https://media.coltene.com/x.pdf"}])

    pb.validate((ok,))


def test_a_subdomain_of_a_listed_host_is_refused(conn):
    pb.set_source(_source(conn))
    bad = _pb("kavo", "KAVO",
              [{"doc_type": "IFU", "kind": "direct",
                "url": "https://cdn.kavo.widen.net/x.pdf"}])

    with pytest.raises(pb.RobotsRefused):
        pb.validate((bad,))


def test_a_lookalike_host_is_not_refused(conn):
    """Anchored on a dot: `notcoltene.com` is a different company."""
    pb.set_source(_source(conn))
    ok = _pb("other", "OTHER",
             [{"doc_type": "IFU", "kind": "direct",
               "url": "https://notcoltene.com/x.pdf"}])

    pb.validate((ok,))


def test_a_dead_source_raises_rather_than_disarming_the_guard(monkeypatch):
    """The failure mode this whole file exists for, in one test. An empty set
    that means "the database is unreachable" reads identically to "nothing is
    refused" -- and here that difference is a fetch we ruled against. Same
    split `_read_rows` makes: a malformed ROW is skipped, a dead SOURCE
    raises."""
    class _Dead:
        def __enter__(self): raise RuntimeError("no connection")
        def __exit__(self, *a): return False

    pb.set_source(lambda: _Dead())

    with pytest.raises(RuntimeError):
        pb.load_robots_refused()
