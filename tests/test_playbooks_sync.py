"""`dentalia playbooks sync`: projects playbook bc_codes/aliases into
manufacturer_alias. This is what turns item_group.canonical_manufacturer from
the bare BC code '001' into the manufacturer's real name.
"""

from __future__ import annotations

import json

import pytest

from app import cli, db, playbooks, reconcile
from app.handlers.resolve import _alias_lookup


def _write(dir_path, filename, data):
    (dir_path / filename).write_text(json.dumps(data))


class _RollbackSpy:
    """Wraps a real (not mocked) psycopg connection so a test can observe
    whether `.rollback()` was actually called on it, without faking Postgres
    itself. Every other attribute -- `execute`, `commit`, `__enter__`,
    `__exit__` -- is delegated straight through to the real connection, so the
    wrapped object behaves exactly like the one `cmd_playbooks` would have
    gotten from `db.connect()`; only `rollback()` is intercepted to flip a
    flag before doing the real rollback."""

    def __init__(self, real):
        self._real = real
        self.rollback_called = False

    def rollback(self):
        self.rollback_called = True
        return self._real.rollback()

    def __enter__(self):
        self._real.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._real.__exit__(exc_type, exc, tb)

    def __getattr__(self, name):
        return getattr(self._real, name)


IVOCLAR = {
    "manufacturer": "Ivoclar Vivadent AG",
    "aliases": ["IVOCLAR"],
    "bc_codes": [{"catalogue": "LJ", "code": "001"}],
}


def test_sync_seeds_bc_codes_and_document_facing_aliases(conn, tmp_path):
    """Both LJ adapter profiles supply a manufacturer CODE, never a name, so a
    row keyed on the raw string 'IVOCLAR' is unreachable by RESOLVE's
    `_alias_lookup` -- but VALIDATE's backfill path (docs/superpowers/plans/
    2026-08-10-backfill-matching.md, Blocker 1) looks manufacturer_alias up by
    the NAME a document prints, so `aliases` must land here too, keyed to the
    playbook's `manufacturer`, not to the BC code."""
    _write(tmp_path, "ivoclar.json", IVOCLAR)

    stats = cli.sync_aliases(conn, playbooks.load_playbooks(tmp_path))

    assert stats["inserted"] == 2
    rows = conn.execute(
        "SELECT raw_name, canonical_name, source FROM manufacturer_alias ORDER BY raw_name"
    ).fetchall()
    assert [(r["raw_name"], r["canonical_name"], r["source"]) for r in rows] == [
        ("001", "Ivoclar Vivadent AG", "playbook"),
        ("IVOCLAR", "Ivoclar Vivadent AG", "playbook"),
    ]


def test_sync_is_idempotent(conn, tmp_path):
    _write(tmp_path, "ivoclar.json", IVOCLAR)
    loaded = playbooks.load_playbooks(tmp_path)

    cli.sync_aliases(conn, loaded)
    second = cli.sync_aliases(conn, loaded)

    assert second["inserted"] == 0
    assert second["updated"] == 0
    assert second["unchanged"] == 2  # the BC code row and the alias row


def test_sync_writes_every_alias_of_a_playbook(conn, tmp_path):
    """A manufacturer can print more than one legal-entity name across its
    document set (subsidiaries, a group's manufacturing sites) -- every one of
    them must resolve back to the same canonical, not just the first."""
    _write(tmp_path, "ivoclar.json", dict(
        IVOCLAR,
        aliases=["IVOCLAR", "Ivoclar Vivadent Manufacturing GmbH"],
    ))

    stats = cli.sync_aliases(conn, playbooks.load_playbooks(tmp_path))

    assert stats["inserted"] == 3  # 1 BC code + 2 aliases
    rows = conn.execute(
        "SELECT raw_name, canonical_name FROM manufacturer_alias "
        "WHERE raw_name != '001' ORDER BY raw_name"
    ).fetchall()
    assert [(r["raw_name"], r["canonical_name"]) for r in rows] == [
        ("IVOCLAR", "Ivoclar Vivadent AG"),
        ("Ivoclar Vivadent Manufacturing GmbH", "Ivoclar Vivadent AG"),
    ]


def test_sync_repoints_an_alias_when_the_playbook_manufacturer_changes(conn, tmp_path):
    """Re-authoring `manufacturer` (a rename) must re-point every alias that
    already synced under the old canonical, the same as a BC code re-point --
    and report it through `orphaned_groups` for the same reason: a group
    resolved under the stale canonical is not retro-fixed."""
    _write(tmp_path, "ivoclar.json", IVOCLAR)
    cli.sync_aliases(conn, playbooks.load_playbooks(tmp_path))
    conn.execute(
        "INSERT INTO item_group (canonical_manufacturer, label) VALUES "
        "('Ivoclar Vivadent AG', 'old')"
    )

    _write(tmp_path, "ivoclar.json", dict(IVOCLAR, manufacturer="Ivoclar Vivadent Group AG"))
    stats = cli.sync_aliases(conn, playbooks.load_playbooks(tmp_path))

    assert stats["updated"] == 2  # the BC code row and the alias row
    row = conn.execute(
        "SELECT canonical_name FROM manufacturer_alias WHERE raw_name='IVOCLAR'"
    ).fetchone()
    assert row["canonical_name"] == "Ivoclar Vivadent Group AG"
    assert stats["orphaned_groups"] == 1


def test_sync_alias_source_is_playbook_even_with_a_vendor_master(conn, tmp_path):
    """An alias is authored data by definition -- unlike a BC code, it has no
    vendor-master equivalent, so its source is always 'playbook', never
    'vendor-master', regardless of what vendor_rows supplies for the code."""
    _write(tmp_path, "ivoclar.json", IVOCLAR)
    vendor = _vendor(("001", "IVOCLAR VIVADENT"))

    cli.sync_aliases(conn, playbooks.load_playbooks(tmp_path), vendor_rows=vendor)

    row = conn.execute(
        "SELECT canonical_name, source FROM manufacturer_alias WHERE raw_name='IVOCLAR'"
    ).fetchone()
    assert (row["canonical_name"], row["source"]) == ("Ivoclar Vivadent AG", "playbook")


def test_sync_writes_no_alias_rows_for_a_playbook_with_none_authored(conn, tmp_path):
    _write(tmp_path, "ivoclar.json", dict(IVOCLAR, aliases=[]))

    stats = cli.sync_aliases(conn, playbooks.load_playbooks(tmp_path))

    assert stats["inserted"] == 1  # the BC code row only
    rows = conn.execute("SELECT raw_name FROM manufacturer_alias").fetchall()
    assert [r["raw_name"] for r in rows] == ["001"]


def test_sync_overwrites_the_self_seeded_row(conn, tmp_path):
    """RESOLVE self-seeds ('001','001') on a miss; sync must correct it. This is
    the whole point of the command: it turns canonical_manufacturer from the
    bare BC code into the manufacturer's real name."""
    canonical, created = _alias_lookup(conn, "001")
    assert (canonical, created) == ("001", True)

    _write(tmp_path, "ivoclar.json", IVOCLAR)
    stats = cli.sync_aliases(conn, playbooks.load_playbooks(tmp_path))

    assert stats["updated"] == 1
    assert _alias_lookup(conn, "001") == ("Ivoclar Vivadent AG", False)


def test_sync_refuses_conflicting_playbooks_and_writes_nothing(conn, tmp_path):
    _write(tmp_path, "a.json", IVOCLAR)
    _write(tmp_path, "b.json", dict(IVOCLAR, manufacturer="Rival AG", aliases=[]))

    with pytest.raises(playbooks.PlaybookConflict):
        cli.sync_aliases(conn, playbooks.load_playbooks(tmp_path))

    assert conn.execute("SELECT count(*) AS n FROM manufacturer_alias").fetchone()["n"] == 0


def test_sync_refuses_one_code_claimed_by_two_playbooks(conn, tmp_path):
    """Until 2026-08-26 the conflict key carried a catalogue tag, so two files
    could claim `001` under different tags, pass `validate`, and be handled by
    `derive_aliases`' skip-and-count. With one catalogue that is simply one
    contested code, and `sync_aliases` validates first: it refuses and writes
    nothing."""
    _write(tmp_path, "ivoclar.json", IVOCLAR)
    _write(
        tmp_path,
        "other.json",
        {"manufacturer": "Other Maker d.o.o.",
         "bc_codes": [{"code": "001"}, {"code": "900"}]},
    )

    with pytest.raises(playbooks.PlaybookConflict, match="001"):
        cli.sync_aliases(conn, playbooks.load_playbooks(tmp_path))

    assert conn.execute(
        "SELECT count(*) AS n FROM manufacturer_alias").fetchone()["n"] == 0


def test_derive_aliases_still_drops_a_contested_code_without_the_gate(conn, tmp_path):
    """`sync_aliases` validates first, so the skip-and-count path above it is
    now unreachable -- but `derive_aliases` is callable without that gate, and
    what it prevents (two real manufacturers merged into one entity) is not
    something to leave to the caller. Tested at the level where it is live."""
    _write(tmp_path, "ivoclar.json", IVOCLAR)
    _write(
        tmp_path,
        "other.json",
        {"manufacturer": "Other Maker d.o.o.",
         "bc_codes": [{"code": "001"}, {"code": "900"}]},
    )

    intended, skipped = reconcile.derive_aliases(
        (), playbooks.load_playbooks(tmp_path))

    assert [code for code, _ in skipped] == ["001"]
    assert "001" not in intended
    assert "900" in intended


def test_a_dropped_code_does_not_merge_two_manufacturers(tmp_path):
    """The contested code is dropped BEFORE entities are built. Otherwise it
    would union two real manufacturers into one entity and hand both of them a
    single canonical name -- worse than dropping it.

    Against `derive_aliases` rather than `sync_aliases`: the latter validates
    first and now refuses this pair outright (see the test above), so the entity
    guard has to be exercised where it is still reachable."""
    _write(tmp_path, "a.json",
           {"manufacturer": "A AG", "bc_codes": [{"code": "001"}, {"code": "010"}]})
    _write(tmp_path, "b.json",
           {"manufacturer": "B AG", "bc_codes": [{"code": "001"}, {"code": "020"}]})

    intended, _ = reconcile.derive_aliases((), playbooks.load_playbooks(tmp_path))

    assert intended["010"][0] == "A AG"
    assert intended["020"][0] == "B AG"
    assert "001" not in intended


def test_sync_reports_orphaned_groups(conn, tmp_path):
    """Re-pointing an alias does not retro-fix groups already resolved under the
    old value. Sync must say so instead of leaving it silent."""
    conn.execute(
        "INSERT INTO item_group (canonical_manufacturer, label) VALUES ('001','old')"
    )
    _write(tmp_path, "ivoclar.json", IVOCLAR)

    stats = cli.sync_aliases(conn, playbooks.load_playbooks(tmp_path))

    assert stats["orphaned_groups"] == 1


def test_sync_reports_orphaned_groups_when_alias_target_changes(conn, tmp_path):
    """An alias that already mapped to one real manufacturer name and gets
    re-pointed to a DIFFERENT real name is not the RESOLVE self-seed case: the
    orphaned item_group row carries the OLD real name, not the raw BC code.
    Undercounting this contradicted the docstring's claim about re-pointed
    aliases (review finding 2)."""
    conn.execute(
        "INSERT INTO manufacturer_alias (raw_name, canonical_name) VALUES ('001','Old Name AG')"
    )
    conn.execute(
        "INSERT INTO item_group (canonical_manufacturer, label) VALUES ('Old Name AG','old')"
    )
    _write(tmp_path, "ivoclar.json", IVOCLAR)

    stats = cli.sync_aliases(conn, playbooks.load_playbooks(tmp_path))

    assert stats["updated"] == 1
    assert stats["orphaned_groups"] == 1


def test_cmd_playbooks_validate_clean_directory(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(playbooks, "PLAYBOOKS_DIR", tmp_path)
    _write(tmp_path, "ivoclar.json", IVOCLAR)

    rc = cli.main(["playbooks", "validate"])

    captured = capsys.readouterr()
    assert rc == 0
    assert "1 playbook(s) valid" in captured.out


def test_cmd_playbooks_validate_conflicting_directory(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(playbooks, "PLAYBOOKS_DIR", tmp_path)
    _write(tmp_path, "a.json", IVOCLAR)
    _write(tmp_path, "b.json", dict(IVOCLAR, manufacturer="Rival AG", aliases=[]))

    rc = cli.main(["playbooks", "validate"])

    captured = capsys.readouterr()
    assert rc != 0
    assert "playbook conflict" in captured.err
    assert "Traceback" not in captured.err + captured.out


def test_cmd_playbooks_no_playbooks_found(tmp_path, monkeypatch, capsys):
    """An empty playbooks/ directory must fail loudly, not silently no-op."""
    monkeypatch.setattr(playbooks, "PLAYBOOKS_DIR", tmp_path)

    rc = cli.main(["playbooks", "validate"])

    captured = capsys.readouterr()
    assert rc != 0
    assert "no playbooks found" in captured.err


def test_cmd_playbooks_validate_fails_on_invalid_json(tmp_path, monkeypatch, capsys):
    """Review finding 2: `load_playbooks` never raises -- a file that isn't
    even valid JSON is silently dropped before `validate()` ever sees it, so
    without this check the operator gate would print "1 playbook(s) valid"
    for a directory that actually has 2 files, one of them broken."""
    monkeypatch.setattr(playbooks, "PLAYBOOKS_DIR", tmp_path)
    _write(tmp_path, "ivoclar.json", IVOCLAR)
    (tmp_path / "broken.json").write_text("{not valid json")

    rc = cli.main(["playbooks", "validate"])

    captured = capsys.readouterr()
    assert rc != 0
    assert "failed to parse" in captured.err
    assert "broken.json" in captured.err
    assert "Traceback" not in captured.err + captured.out


def test_cmd_playbooks_validate_fails_on_broken_parse_section(tmp_path, monkeypatch, capsys):
    """Review finding 2: a typo'd `match` key (e.g. `matches`) parses fine as a
    `Playbook` (identity fields are untouched) but silently drops the T0
    template in `t0_layout.load_templates` with only a `log.warning` -- no
    exception, no non-zero exit. `validate()` alone doesn't catch this either,
    since it never inspects the parse section. This is the authoring mistake
    finding 2 says must fail the CLI gate loudly."""
    monkeypatch.setattr(playbooks, "PLAYBOOKS_DIR", tmp_path)
    _write(tmp_path, "ivoclar.json", dict(
        IVOCLAR,
        matches={"anchors": ["tefo-en-"]},   # typo: should be "match"
        ref_strategy="stitched-table",
    ))

    rc = cli.main(["playbooks", "validate"])

    captured = capsys.readouterr()
    assert rc != 0
    assert "T0 parse section" in captured.err
    assert "ivoclar.json" in captured.err
    assert "Traceback" not in captured.err + captured.out


def test_cmd_playbooks_sync_conflict_leaves_zero_committed_rows(
    test_db_url, connect_test, tmp_path, monkeypatch, capsys
):
    """End-to-end check that the conflict path leaves no committed row behind,
    observed from a SEPARATE connection so this exercises real commit
    behaviour, not just what is visible inside the still-open transaction
    that did the writing.

    This does NOT guard the `conn.rollback()` line in `cmd_playbooks`:
    `sync_aliases` raises `PlaybookConflict` before issuing any write, so on
    this path there is nothing pending to commit or roll back either way --
    deleting the rollback would not make this test fail.
    `test_cmd_playbooks_sync_conflict_calls_rollback` below is the test that
    actually observes the rollback call."""
    monkeypatch.setenv("DATABASE_URL", test_db_url)
    monkeypatch.setattr(playbooks, "PLAYBOOKS_DIR", tmp_path)
    _write(tmp_path, "a.json", IVOCLAR)
    _write(tmp_path, "b.json", dict(IVOCLAR, manufacturer="Rival AG", aliases=[]))

    rc = cli.main(["playbooks", "sync"])

    captured = capsys.readouterr()
    assert rc != 0
    assert "playbook conflict" in captured.err

    checker = connect_test(autocommit=True)
    n = checker.execute(
        "SELECT count(*) AS n FROM manufacturer_alias"
    ).fetchone()["n"]
    assert n == 0


def test_cmd_playbooks_sync_conflict_calls_rollback(
    test_db_url, tmp_path, monkeypatch, capsys
):
    """Regression test for review finding 1: `cmd_playbooks` must explicitly
    roll back the connection it obtained from `db.connect()` when
    `sync_aliases` raises `PlaybookConflict`, because psycopg 3 commits on a
    clean `with`-block exit regardless of what the function returns, and the
    exception is caught (not re-raised) inside that block.

    Wraps the real connection `cmd_playbooks` gets from `db.connect()` in a
    thin spy that records whether `.rollback()` is called on it, then
    delegates to the real method -- the database itself stays real (no
    mocking Postgres), only the call is observed. This fails if the
    `conn.rollback()` line in `cmd_playbooks` is removed, unlike the
    commits-nothing test above, which stays green either way because
    `sync_aliases` never has a pending write on the conflict path."""
    monkeypatch.setenv("DATABASE_URL", test_db_url)
    monkeypatch.setattr(playbooks, "PLAYBOOKS_DIR", tmp_path)
    _write(tmp_path, "a.json", IVOCLAR)
    _write(tmp_path, "b.json", dict(IVOCLAR, manufacturer="Rival AG", aliases=[]))

    real_connect = db.connect
    spies: list[_RollbackSpy] = []

    def spying_connect(*args, **kwargs):
        spy = _RollbackSpy(real_connect(*args, **kwargs))
        spies.append(spy)
        return spy

    monkeypatch.setattr(cli.db, "connect", spying_connect)

    rc = cli.main(["playbooks", "sync"])

    captured = capsys.readouterr()
    assert rc != 0
    assert "playbook conflict" in captured.err

    assert len(spies) == 1
    assert spies[0].rollback_called is True


# --------------------------------------------------------------------------- #
# S1.8: entity-level derivation from the vendor master, and provenance
# --------------------------------------------------------------------------- #
def _vendor(*pairs):
    from app.vendor_master import VendorRow

    return tuple(VendorRow(code, name) for code, name in pairs)


def test_vendor_master_codes_are_seeded_with_their_master_name(conn, tmp_path):
    stats = cli.sync_aliases(
        conn, (), vendor_rows=_vendor(("077", "KOMET"), ("041", "VOCO"))
    )

    rows = conn.execute(
        "SELECT raw_name, canonical_name, source FROM manufacturer_alias "
        "ORDER BY raw_name"
    ).fetchall()
    assert [(r["raw_name"], r["canonical_name"], r["source"]) for r in rows] == [
        ("041", "VOCO", "vendor-master"),
        ("077", "KOMET", "vendor-master"),
    ]
    assert stats["inserted"] == 2


def test_a_playbook_claiming_one_code_wins_for_the_whole_entity(conn, tmp_path):
    """The reason derivation is entity-level: per-code precedence would leave
    001 as IVOCLAR and 005/275 as IVOCLAR VIVADENT, two namespaces that
    RESOLVE's name-family rung can never rejoin."""
    _write(tmp_path, "ivoclar.json",
           {"manufacturer": "IVOCLAR", "bc_codes": [{"catalogue": "LJ", "code": "001"}]})
    vendor = _vendor(("001", "IVOCLAR VIVADENT"), ("005", "IVOCLAR VIVADENT"),
                     ("275", "IVOCLAR VIVADENT"))

    cli.sync_aliases(conn, playbooks.load_playbooks(tmp_path), vendor_rows=vendor)

    rows = conn.execute(
        "SELECT raw_name, canonical_name, source FROM manufacturer_alias "
        "ORDER BY raw_name"
    ).fetchall()
    assert [(r["raw_name"], r["canonical_name"]) for r in rows] == [
        ("001", "IVOCLAR"), ("005", "IVOCLAR"), ("275", "IVOCLAR"),
    ]
    assert {r["source"] for r in rows} == {"playbook"}


def test_sync_with_the_vendor_master_is_idempotent(conn, tmp_path):
    vendor = _vendor(("077", "KOMET"), ("041", "VOCO"))

    cli.sync_aliases(conn, (), vendor_rows=vendor)
    second = cli.sync_aliases(conn, (), vendor_rows=vendor)

    assert second["inserted"] == 0 and second["updated"] == 0
    assert second["unchanged"] == 2


def test_a_self_seeded_row_is_re_pointed_and_re_sourced(conn):
    """What the seed is FOR: RESOLVE self-seeds `001 -> 001` on a miss, and the
    master replaces it with a real name."""
    conn.execute(
        "INSERT INTO manufacturer_alias (raw_name, canonical_name) VALUES ('001','001')"
    )

    stats = cli.sync_aliases(conn, (), vendor_rows=_vendor(("001", "IVOCLAR VIVADENT")))

    row = conn.execute(
        "SELECT canonical_name, source FROM manufacturer_alias WHERE raw_name='001'"
    ).fetchone()
    assert (row["canonical_name"], row["source"]) == ("IVOCLAR VIVADENT", "vendor-master")
    assert stats["updated"] == 1


def test_a_nameless_master_row_is_not_projected(conn):
    """One of the 390 delivered rows has no name; an alias to nothing is worse
    than no alias."""
    cli.sync_aliases(conn, (), vendor_rows=_vendor(("999", None)))

    assert conn.execute(
        "SELECT count(*) AS n FROM manufacturer_alias"
    ).fetchone()["n"] == 0


def test_re_pointing_a_code_reports_orphaned_groups(conn):
    """The whole reason regroup exists: an alias fixed after the fact does not
    retro-fix groups already built under the old value."""
    conn.execute(
        "INSERT INTO item_group (canonical_manufacturer) VALUES ('001')"
    )

    stats = cli.sync_aliases(conn, (), vendor_rows=_vendor(("001", "IVOCLAR VIVADENT")))

    assert stats["orphaned_groups"] == 1
