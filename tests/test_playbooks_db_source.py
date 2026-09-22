"""`app/playbooks.py` reading rows instead of files (slice 2, tasks 7 and 9).

The move's whole risk is that every consumer changes backing store at once and
nothing downstream is supposed to notice. So these are mostly *pins*: that the
public API did not change shape, that a failure fails the way the file loader
failed, and -- the one that actually proves the swap -- that a `Playbook` built
from a row equals the one built from its file.

The seed is the fixture here on purpose. Building rows by hand would test a
shape I invented; running the real import over the real 33 files tests the one
the pipeline will actually see.
"""
from __future__ import annotations

import inspect
import pathlib

import pytest

from app import manufacturer_seed
from app import playbooks as pb


@pytest.fixture(autouse=True)
def _clean(conn):
    """Entity tables are outside `_RESET_TABLES`' closure (see
    tests/test_manufacturer_seed.py), and the module-level source and caches
    are process state that must not leak into the next test."""
    def _wipe():
        conn.execute("DELETE FROM manufacturer_playbook_revision")
        conn.execute("DELETE FROM manufacturer_name")
        conn.execute("DELETE FROM manufacturer_bc_code")
        conn.execute("DELETE FROM manufacturer")
        conn.commit()

    _wipe()
    pb.set_source(None)
    pb.clear_cache()
    yield
    pb.set_source(None)
    pb.clear_cache()
    _wipe()


def _seed_real(conn):
    """The repo's 33 files, imported the way the CLI imports them."""
    raw = pb.load_raw()
    loaded = pb.load_playbooks()
    for code, name in sorted({(bc.code, p.manufacturer)
                              for p in loaded for bc in p.bc_codes}):
        conn.execute(
            "INSERT INTO vendor_master (code_source, code, name, import_batch) "
            "VALUES ('LJ',%s,%s,'db-source-test') ON CONFLICT DO NOTHING",
            (code, name))

    original = pb.load_robots_refused
    pb.load_robots_refused = lambda *a, **k: frozenset()
    try:
        stats = manufacturer_seed.seed(conn, loaded=loaded, raw=raw)
    finally:
        pb.load_robots_refused = original
    assert stats.clean, stats.conflicts
    conn.commit()
    return raw, loaded


def _source(conn):
    """A conn_factory over the test's own connection. `with` must not close it,
    so the context manager is a no-op wrapper."""
    class _Keep:
        def __enter__(self): return conn
        def __exit__(self, *a): return False
    return lambda: _Keep()


# --------------------------------------------------------------------------- #
# parity -- the test the move exists to pass

def test_every_playbook_parses_from_the_database_exactly_as_from_its_file(conn):
    """The claim the whole slice rests on: nine of twelve consuming modules are
    unchanged because what they receive is unchanged. That is a design
    intention everywhere except here, where it is measured.

    `bc_codes` and `aliases` compare as SETS: the database returns them sorted
    and the file lists them in authored order. Both are lookup sets, so the
    order carries no meaning -- see `_row_to_raw`."""
    _, from_files = _seed_real(conn)
    pb.set_source(_source(conn))

    from_db = pb.load_playbooks()
    assert {p.slug for p in from_db} == {p.slug for p in from_files}

    by_slug = {p.slug: p for p in from_db}
    for want in from_files:
        got = by_slug[want.slug]
        assert set(got.bc_codes) == set(want.bc_codes), want.slug
        assert set(got.aliases) == set(want.aliases), want.slug
        # Every other field compares directly, order included.
        for f in ("manufacturer", "domains", "doc_sources", "ref_normalize",
                  "companion", "rev", "date_labels", "type_markers",
                  "source_priority", "cert_number_pattern", "ref_pattern",
                  "extract_hints", "coverage_map", "exclude", "skip_backfill"):
            assert getattr(got, f) == getattr(want, f), f"{want.slug}.{f}"


def test_for_manufacturer_resolves_off_rows_including_aliases(conn):
    """`for_manufacturer` is the entry point DISCOVER, VALIDATE, EXTRACT and
    BACKFILL all use. Its signature is untouched; what changes underneath it
    must not."""
    _, from_files = _seed_real(conn)
    pb.set_source(_source(conn))

    with_alias = next(p for p in from_files if p.aliases)
    assert pb.for_manufacturer(with_alias.manufacturer).slug == with_alias.slug
    assert pb.for_manufacturer(with_alias.aliases[0]).slug == with_alias.slug
    assert pb.for_manufacturer("NO SUCH MANUFACTURER LTD") is None


# --------------------------------------------------------------------------- #
# the cache

def test_a_second_load_at_the_same_epoch_does_not_reparse(conn):
    _seed_real(conn)
    pb.set_source(_source(conn))

    first = pb.load_playbooks()
    assert pb.load_playbooks() is first        # identity: the cached tuple


def test_a_write_bumps_the_epoch_and_the_next_load_sees_it(conn):
    """The reason the epoch exists. An edit must land on the next job without
    anyone restarting a worker."""
    _seed_real(conn)
    pb.set_source(_source(conn))
    before = pb.load_playbooks()
    victim = before[0].slug

    conn.execute(
        """UPDATE manufacturer
              SET body = jsonb_set(body, '{domains}', '["changed.example"]')
            WHERE slug = %s""", (victim,))
    conn.commit()

    after = pb.load_playbooks()
    assert after is not before
    assert pb.for_manufacturer(
        next(p for p in after if p.slug == victim).manufacturer
    ).domains == ("changed.example",)


# --------------------------------------------------------------------------- #
# failure, split by kind (spec §3.5)

def test_one_malformed_row_is_skipped_and_the_rest_survive(conn):
    """Identical to the file loader's per-file behaviour. A typo in one
    playbook must not take DISCOVER down for every other manufacturer."""
    _seed_real(conn)
    pb.set_source(_source(conn))
    total = len(pb.load_playbooks())

    victim = pb.load_playbooks()[0].slug
    conn.execute(
        """UPDATE manufacturer
              SET body = jsonb_set(body, '{doc_sources}',
                                   '[{"kind": "not-a-kind", "url": "x",
                                      "doc_type": "DoC"}]')
            WHERE slug = %s""", (victim,))
    conn.commit()

    survivors = pb.load_playbooks()
    assert len(survivors) == total - 1
    assert victim not in {p.slug for p in survivors}


def test_a_dead_source_raises_rather_than_reporting_nothing_authored(conn):
    """Returning () on a dead database would strip every manufacturer's rules
    at once and look exactly like "nothing is authored". BACKFILL alone would
    then archive Neodent's 28 Dentalia invoices, because `pb is None` disables
    both `skip_backfill` and `exclude`."""
    class _Dead:
        def __enter__(self): raise RuntimeError("connection is closed")
        def __exit__(self, *a): return False

    pb.set_source(lambda: _Dead())
    with pytest.raises(RuntimeError):
        pb.load_playbooks()


def test_a_source_failure_does_not_poison_the_cache(conn):
    """A cached () would make one blip persist until the next epoch change --
    which, with nobody editing playbooks, could be days."""
    _seed_real(conn)
    live = _source(conn)
    pb.set_source(live)
    expected = len(pb.load_playbooks())
    pb.clear_cache()

    calls = {"n": 0}

    class _FlakyThenDead:
        def __enter__(self):
            calls["n"] += 1
            raise RuntimeError("connection is closed")
        def __exit__(self, *a): return False

    pb.set_source(lambda: _FlakyThenDead())
    with pytest.raises(RuntimeError):
        pb.load_playbooks()
    assert calls["n"] == 2, "one retry, then raise"

    pb.set_source(live)
    assert len(pb.load_playbooks()) == expected


# --------------------------------------------------------------------------- #
# the source switch itself

def test_an_explicit_directory_still_wins_over_a_configured_source(conn):
    """What keeps the seed honest: it imports FILES into the database it would
    otherwise be reading. Also what leaves every existing test that builds a
    fixture directory working unchanged."""
    _seed_real(conn)
    pb.set_source(_source(conn))

    from_dir = pb.load_playbooks(pb.PLAYBOOKS_DIR)
    assert len(from_dir) == len(pb.load_raw())


def test_with_no_source_set_the_loader_still_reads_the_directory(conn):
    """The swap has to be able to land incrementally: until `set_source` is
    wired into a process, that process reads files exactly as before."""
    pb.set_source(None)
    assert len(pb.load_playbooks()) == len(pb.load_raw())


# --------------------------------------------------------------------------- #
# the second loader (task 8)

def test_parse_templates_come_off_rows_identically_to_files(conn):
    """`t0_layout.load_templates` was a SECOND loader with its own glob and its
    own `json.loads` -- two views of one directory that could disagree about
    which files existed. It is now a projection over `load_raw`, so the store
    swap happened once and this followed."""
    from app.extract import t0_layout

    _seed_real(conn)
    from_files = t0_layout.load_templates(pb.PLAYBOOKS_DIR)
    assert len(from_files) == 6, "the repo has six authored parse templates (NEODENT joined 2026-09-11)"

    pb.set_source(_source(conn))
    from_db = t0_layout.load_templates()

    assert {t.slug for t in from_db} == {t.slug for t in from_files}
    by_slug = {t.slug: t for t in from_db}
    for want in from_files:
        assert by_slug[want.slug] == want, want.slug


def test_a_playbook_with_no_parse_section_is_skipped_silently(conn):
    """Most playbooks carry only identity and URLs. That is a complete, valid
    playbook -- not a broken one -- so it must not appear as a template and
    must not be logged as a failure."""
    from app.extract import t0_layout

    raw, _ = _seed_real(conn)
    pb.set_source(_source(conn))

    templates = {t.slug for t in t0_layout.load_templates()}
    without = {s for s, r in raw.items() if "ref_strategy" not in r and "match" not in r}
    assert without, "fixture assumption: some playbooks carry no parse section"
    assert not (templates & without)


def test_a_malformed_template_is_skipped_and_the_others_survive(conn):
    """Never-raises posture, preserved across the swap: templates are an
    enhancement, and an empty set degrades to T0's generic path rather than
    taking the worker down."""
    from app.extract import t0_layout

    _seed_real(conn)
    pb.set_source(_source(conn))
    before = len(t0_layout.load_templates())

    victim = t0_layout.load_templates()[0].slug
    conn.execute(
        """UPDATE manufacturer
              SET body = jsonb_set(body, '{ref_strategy}', '"not-a-strategy"')
            WHERE slug = %s""", (victim,))
    conn.commit()

    after = t0_layout.load_templates()
    assert len(after) == before - 1
    assert victim not in {t.slug for t in after}


# --------------------------------------------------------------------------- #
# what the web index owes the operator

def test_slugs_counts_what_exists_including_what_will_not_parse(conn):
    """`load_playbooks` and `load_raw` both drop what they cannot read, by
    contract, so neither can report that anything was dropped. `slugs()` is
    what makes the web index's "N playbook(s) failed to parse" answerable
    without globbing a directory."""
    _seed_real(conn)
    pb.set_source(_source(conn))
    total = len(pb.slugs())
    assert total == len(pb.load_playbooks())

    victim = pb.load_playbooks()[0].slug
    conn.execute(
        """UPDATE manufacturer
              SET body = jsonb_set(body, '{doc_sources}',
                                   '[{"kind": "not-a-kind", "url": "x",
                                      "doc_type": "DoC"}]')
            WHERE slug = %s""", (victim,))
    conn.commit()

    assert len(pb.slugs()) == total, "still present, just unreadable"
    assert len(pb.load_playbooks()) == total - 1
    assert pb.slugs() - {p.slug for p in pb.load_playbooks()} == {victim}


def test_reads_files_reports_which_store_a_call_would_use(conn):
    """The web index phrases a store-level failure differently per store:
    "directory not found" is real and reachable for files (the compose bind
    mount) and meaningless for rows, where an unreachable store raises."""
    assert pb.reads_files() is True
    pb.set_source(_source(conn))
    assert pb.reads_files() is False
    assert pb.reads_files(pb.PLAYBOOKS_DIR) is True, "explicit directory wins"


# --------------------------------------------------------------------------- #
# the one-way move (task 9)

def test_neither_loader_touches_the_filesystem_when_a_source_is_set(conn, monkeypatch):
    """The pin the "two sources of truth eats a week" trap demands. Both
    loaders used to glob a directory; if either still does, the store swap is
    only half done and the two halves can disagree about which playbooks
    exist -- silently, because both are never-raises by contract."""
    import os as _os
    from app.extract import t0_layout

    _seed_real(conn)
    pb.set_source(_source(conn))

    def _boom(*a, **k):
        raise AssertionError("filesystem touched with a source configured")

    monkeypatch.setattr(pathlib.Path, "glob", _boom)
    monkeypatch.setattr(pathlib.Path, "is_dir", _boom)
    monkeypatch.setattr(pathlib.Path, "is_file", _boom)
    monkeypatch.setattr(_os, "scandir", _boom)

    assert len(pb.load_playbooks()) > 0
    assert len(pb.load_raw()) > 0
    assert len(pb.slugs()) > 0
    assert len(t0_layout.load_templates()) == 6  # NEODENT joined 2026-09-11
    # The refused-host list joined this in slice 4 (migration 051), and it is
    # the one whose filesystem fallback fails OPEN: a missing file is an empty
    # set by design, so a third loader still reading the directory would
    # disarm the `kind:"direct"` guard the moment compose stopped mounting it.
    assert "coltene.com" in pb.load_robots_refused()


def test_the_seed_reads_files_even_with_a_source_configured(conn):
    """The direction of the import must not depend on process configuration.
    With a source set, a bare `load_playbooks()` returns rows -- so a seed that
    used one would read its own output, report a clean run, and never land a
    file edit. Not a crash: a silent no-op."""
    raw, loaded = _seed_real(conn)
    pb.set_source(_source(conn))

    # Change a row so files and rows now disagree.
    victim = loaded[0].slug
    conn.execute(
        """UPDATE manufacturer
              SET body = jsonb_set(body, '{domains}', '["drifted.example"]')
            WHERE slug = %s""", (victim,))
    conn.commit()
    assert pb.for_manufacturer(
        next(p.manufacturer for p in loaded if p.slug == victim)
    ).domains == ("drifted.example",)

    # The seed must SEE that disagreement -- which it can only do by reading
    # the file -- and refuse to overwrite, rather than reading the row back and
    # reporting everything unchanged.
    original = pb.load_robots_refused
    pb.load_robots_refused = lambda *a, **k: frozenset()
    try:
        stats = manufacturer_seed.seed(conn)
    finally:
        pb.load_robots_refused = original

    assert not stats.clean
    assert any(f"body {victim!r}" in c[0] for c in stats.conflicts)


def test_the_cli_wires_the_database_but_the_seed_still_imports_files(conn):
    """`main()` configures the source for every command; the two that exist to
    read the authored files pass an explicit directory, which wins."""
    import app.cli as cli

    assert "set_source" in inspect.getsource(cli.use_database_playbooks)
    seed_src = inspect.getsource(cli.cmd_manufacturers)
    assert "load_playbooks(playbooks.PLAYBOOKS_DIR)" in seed_src
    assert "dir_path=playbooks.PLAYBOOKS_DIR" in seed_src
    # `playbooks validate` was already explicit and must stay that way.
    assert "playbooks.PLAYBOOKS_DIR" in inspect.getsource(cli._cmd_playbooks_validate)


def test_entity_grouping_is_identical_from_rows_and_from_files(conn):
    """The plan listed `app/reconcile.py` as a file to modify. It is not
    modified, and this is why: it reads no files at all -- callers hand it
    `Playbook`s -- and its output is invariant to the ONE way row-loaded and
    file-loaded playbooks differ, which is that `bc_codes` and `aliases` come
    back sorted rather than in authored order.

    `_entities` sorts `codes`, `names`, `slugs` and the returned tuple, and
    `derive_aliases` decides conflicts with a set. Neither can see the
    difference. Asserted rather than argued, because "it sorts everything" is
    the kind of claim that stays true only until someone adds a `[0]`.
    """
    from app import reconcile

    raw, from_files = _seed_real(conn)
    vendor = {r.code: r.name for r in reconcile.load_vendor_master(conn)}

    pb.set_source(_source(conn))
    from_db = pb.load_playbooks()

    assert reconcile._entities(vendor, from_db) == reconcile._entities(vendor, from_files)

    # Same for the alias projection, including its skip-and-count guard.
    assert (reconcile.derive_aliases((), from_db)
            == reconcile.derive_aliases((), from_files))


def test_a_cache_hit_reads_the_epoch_and_nothing_else(conn):
    """The whole reason `playbook_epoch` exists. The first version of this
    loader fetched every row and THEN compared the counter, so the cache saved
    the parse but not the query -- and VALIDATE calls `for_manufacturer` once
    per group, which made it a full table read per group.

    Counts statements, not calls: the assertion is about what reaches Postgres.
    """
    _seed_real(conn)

    seen = []
    real_execute = conn.execute

    def _spy(q, *a, **k):
        seen.append(" ".join(str(q).split()))
        return real_execute(q, *a, **k)

    class _Spy:
        def __enter__(self):
            conn.execute = _spy
            return conn
        def __exit__(self, *a):
            conn.execute = real_execute
            return False

    pb.set_source(lambda: _Spy())

    pb.load_playbooks()                       # cold: epoch + rows
    cold = list(seen)
    assert any("playbook_epoch" in q for q in cold)
    assert any("FROM manufacturer m" in q for q in cold)

    seen.clear()
    pb.load_playbooks()
    pb.load_raw()
    pb.slugs()
    from app.extract import t0_layout
    t0_layout.load_templates()

    assert seen, "a cache hit still has to probe the epoch"
    assert all("playbook_epoch" in q for q in seen), seen
    assert not any("FROM manufacturer m" in q for q in seen), (
        "a cache hit must not re-read the rows")
