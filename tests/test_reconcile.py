"""Playbook <-> vendor-master reconciliation (S1.8).

The report exists because the playbook->catalogue join is a string coincidence
today, not a key: the LJ export carries a manufacturer CODE, so `bc_codes` is
the only join that works and `manufacturer` is not one. Every test here is a
way that join can be silently absent or silently wrong.

Entity-level derivation is the load-bearing part. Per-code precedence would let
a playbook claiming `001` of IVOCLAR VIVADENT {001, 005, 275} split one
manufacturer into two disjoint `canonical_manufacturer` namespaces that
`_by_name_family` can never rejoin -- worse than either source alone.
"""

from __future__ import annotations

import pathlib

import pytest

from app import playbooks as pb_mod
from app import reconcile
from app.vendor_master import VendorRow


@pytest.fixture(autouse=True)
def _clean_registry(test_db_url):
    """`conftest`'s `connect_test` truncates the registry tables after each test
    that uses it. The CLI tests here must commit -- `cli.main` opens its own
    connection and cannot see an uncommitted write -- and some of them never open
    a `connect_test` connection at all, so that teardown does not fire and the
    rows would outlive the test. This fixture also wipes on SETUP, which conftest
    deliberately does not (it would double the truncate cost of every test).

    Autouse and function-scoped, so it is set up before `connect_test` and torn
    down after it: the truncate therefore runs once the test's own connections
    are already closed, instead of blocking on their locks.
    """
    from app import db

    def wipe():
        with db.connect(test_db_url, autocommit=True) as c:
            c.execute("TRUNCATE vendor_master, manufacturer_alias, item_mirror CASCADE")

    wipe()
    yield
    wipe()


def _pb(slug, manufacturer, codes=(), aliases=(), domains=(), doc_sources=()):
    return pb_mod.Playbook(
        slug=slug,
        manufacturer=manufacturer,
        bc_codes=tuple(pb_mod.BcCode(c) for c in codes),
        aliases=tuple(aliases),
        domains=tuple(domains),
        doc_sources=tuple(doc_sources),
    )


def _kinds(report, kind):
    return [f for f in report.findings if f.kind == kind]


# --------------------------------------------------------------------------- #
# entities
# --------------------------------------------------------------------------- #
def test_codes_sharing_a_master_name_are_one_entity():
    rows = (
        VendorRow("001", "IVOCLAR VIVADENT"),
        VendorRow("005", "IVOCLAR VIVADENT"),
        VendorRow("275", "IVOCLAR VIVADENT"),
        VendorRow("041", "VOCO"),
    )

    r = reconcile.build(rows, (), {}, {})

    entities = {e.codes: e for e in r.entities}
    assert ("001", "005", "275") in entities
    assert ("041",) in entities
    assert entities[("001", "005", "275")].canonical == "IVOCLAR VIVADENT"


def test_one_playbook_claiming_two_master_names_merges_them():
    """A legal entity BC issued two unrelated-looking names for is still one
    manufacturer if a human said so."""
    rows = (VendorRow("100", "ACME DENTAL"), VendorRow("200", "ACME GMBH"))

    r = reconcile.build(rows, (_pb("acme", "ACME", codes=("100", "200")),), {}, {})

    assert [e.codes for e in r.entities] == [("100", "200")]
    assert r.entities[0].canonical == "ACME"


def test_authored_name_wins_for_every_code_of_the_entity():
    """Not per code: that is exactly the split-brain this design rejects."""
    rows = (
        VendorRow("001", "IVOCLAR VIVADENT"),
        VendorRow("005", "IVOCLAR VIVADENT"),
    )

    r = reconcile.build(rows, (_pb("ivoclar", "IVOCLAR", codes=("001",)),), {}, {})

    assert {c.code: c.would_be for c in r.alias_diff} == {
        "001": "IVOCLAR",
        "005": "IVOCLAR",
    }
    assert {c.source for c in r.alias_diff} == {"playbook"}


def test_unclaimed_entity_falls_back_to_the_master_name():
    r = reconcile.build((VendorRow("077", "KOMET"),), (), {}, {})

    assert [(c.code, c.would_be, c.source) for c in r.alias_diff] == [
        ("077", "KOMET", "vendor-master")
    ]


def test_a_nameless_master_row_yields_no_alias():
    """One of the 390 delivered rows has no name; it must not become an alias
    to the empty string."""
    r = reconcile.build((VendorRow("999", None),), (), {}, {})

    assert r.alias_diff == ()
    assert [e.codes for e in r.entities] == [("999",)]
    assert r.entities[0].canonical is None


# --------------------------------------------------------------------------- #
# findings
# --------------------------------------------------------------------------- #
def test_playbook_with_no_codes_and_no_matching_name_is_dead():
    rows = (VendorRow("035", "DENTSPLY"),)
    pb = _pb("dentsply-sirona", "DENSTPLY", domains=("dentsplysirona.com",))

    r = reconcile.build(rows, (pb,), {}, {"035": 233})

    (found,) = _kinds(r, "dead-playbook")
    assert found.subject == "dentsply-sirona"
    assert found.blocking
    assert "DENTSPLY" in found.detail and "035" in found.detail   # the suggestion
    assert "233 item(s)" in found.detail


def test_dead_playbook_offers_no_suggestion_when_nothing_is_close():
    """A name legitimately absent from the master must not get a confident
    wrong hint; `GC EUROPE N.V.` tops out at 50% against the real 387 names."""
    rows = (VendorRow("500", "ALPHADENT N.V."), VendorRow("501", "EURONDA"))

    r = reconcile.build(rows, (_pb("gc", "GC EUROPE N.V."),), {}, {})

    (found,) = _kinds(r, "dead-playbook")
    assert "Nearest" not in found.detail


def test_playbook_with_no_codes_but_a_matching_name_names_the_code():
    rows = (VendorRow("077", "KOMET"),)

    r = reconcile.build(rows, (_pb("komet", "KOMET"),), {}, {"077": 412})

    (found,) = _kinds(r, "unjoined-playbook")
    assert found.blocking and found.items == 412
    assert "077" in found.detail
    assert not _kinds(r, "dead-playbook")


def test_an_alias_can_be_what_matches_the_master_name():
    rows = (VendorRow("008", "GC"),)

    r = reconcile.build(rows, (_pb("gc", "GC EUROPE N.V.", aliases=("GC",)),), {}, {})

    (found,) = _kinds(r, "unjoined-playbook")
    assert "008" in found.detail


def test_partial_claim_is_reported_with_the_codes_it_would_extend_to():
    rows = (
        VendorRow("001", "IVOCLAR VIVADENT"),
        VendorRow("005", "IVOCLAR VIVADENT"),
        VendorRow("275", "IVOCLAR VIVADENT"),
    )
    counts = {"001": 954, "005": 12, "275": 3}

    r = reconcile.build(rows, (_pb("ivoclar", "IVOCLAR", codes=("001",)),), {}, counts)

    (found,) = _kinds(r, "partial-claim")
    assert found.blocking
    assert "005" in found.detail and "275" in found.detail
    assert found.items == 15, "sized on the codes it would extend to, not the claim"


def test_a_fully_claimed_entity_is_not_a_partial_claim():
    rows = (VendorRow("001", "IVOCLAR VIVADENT"), VendorRow("005", "IVOCLAR VIVADENT"))

    r = reconcile.build(rows, (_pb("ivoclar", "IVOCLAR", codes=("001", "005")),), {}, {})

    assert not _kinds(r, "partial-claim")


def test_a_differing_master_name_is_info_not_blocking():
    """Authored-wins is the design, so a rename is a fact to show, not an error."""
    rows = (VendorRow("008", "GC"),)

    r = reconcile.build(rows, (_pb("gc", "GC EUROPE N.V.", codes=("008",)),), {}, {})

    (found,) = _kinds(r, "rename")
    assert not found.blocking
    assert r.blocking == ()


def test_an_alias_owned_by_a_different_entity_collides():
    rows = (VendorRow("008", "GC"), VendorRow("077", "KOMET"))
    pbs = (_pb("gc", "GC EUROPE N.V.", codes=("008",), aliases=("KOMET",)),)

    r = reconcile.build(rows, pbs, {}, {"077": 412})

    (found,) = _kinds(r, "alias-collision")
    assert found.blocking and found.items == 412
    assert "077" in found.detail


def test_an_alias_of_a_code_the_playbook_itself_claims_is_not_a_collision():
    rows = (VendorRow("008", "GC"),)
    pbs = (_pb("gc", "GC EUROPE N.V.", codes=("008",), aliases=("GC",)),)

    r = reconcile.build(rows, pbs, {}, {})

    assert not _kinds(r, "alias-collision")


def test_a_playbook_claiming_a_code_the_master_never_issued_is_blocking():
    """The mirror image of `uncovered-code`: a typo'd claim syncs an alias no
    item will ever carry, and leaves the playbook as unreachable as before."""
    rows = (VendorRow("035", "DENTSPLY"),)

    r = reconcile.build(rows, (_pb("d", "DENTSPLY", codes=("035", "999")),), {}, {})

    (found,) = _kinds(r, "unknown-code")
    assert found.blocking and "999" in found.detail and "035" not in found.detail


def test_a_code_in_the_catalogue_but_not_the_master_is_reported():
    r = reconcile.build((VendorRow("001", "IVOCLAR VIVADENT"),), (), {}, {"001": 9, "ZZZ": 4})

    assert r.uncovered == (("ZZZ", 4),)
    (found,) = _kinds(r, "uncovered-code")
    assert found.subject == "ZZZ" and found.blocking is False


# --------------------------------------------------------------------------- #
# alias diff
# --------------------------------------------------------------------------- #
def test_alias_diff_counts_items_not_groups():
    rows = (VendorRow("001", "IVOCLAR VIVADENT"), VendorRow("041", "VOCO"))

    r = reconcile.build(rows, (), {}, {"001": 954, "041": 106})

    assert {c.code: c.items for c in r.alias_diff} == {"001": 954, "041": 106}
    assert [c.code for c in r.alias_diff] == ["001", "041"], "largest blast radius first"


def test_an_alias_already_correct_is_not_a_change():
    rows = (VendorRow("041", "VOCO"), VendorRow("077", "KOMET"))
    aliases = {"041": "VOCO"}

    r = reconcile.build(rows, (), aliases, {})

    assert len(r.alias_diff) == 2
    assert [c.code for c in r.changes] == ["077"]


def test_an_alias_pointing_somewhere_else_shows_both_sides():
    rows = (VendorRow("001", "IVOCLAR VIVADENT"),)

    r = reconcile.build(rows, (_pb("ivoclar", "IVOCLAR", codes=("001",)),),
                        {"001": "SOMETHING ELSE"}, {})

    (change,) = r.changes
    assert (change.current, change.would_be) == ("SOMETHING ELSE", "IVOCLAR")


def test_render_shows_every_blocking_finding_and_the_diff():
    rows = (VendorRow("035", "DENTSPLY"), VendorRow("041", "VOCO"))

    r = reconcile.build(rows, (_pb("d", "DENSTPLY"),), {}, {"035": 233, "041": 106},
                        items_source="test")
    text = "\n".join(reconcile.render(r))

    assert "BLOCKING (1)" in text
    assert "dead-playbook" in text and "DENSTPLY" in text
    assert "would-be alias diff: 2 of 2 code(s) change" in text
    assert "339 item(s) affected" in text


def test_render_truncation_is_announced_not_silent():
    rows = tuple(VendorRow(f"{i:03d}", f"M{i}") for i in range(10))

    r = reconcile.build(rows, (), {}, {})
    text = "\n".join(reconcile.render(r, limit=3))

    assert "... 7 more" in text


# --------------------------------------------------------------------------- #
# DB-backed inputs + CLI
# --------------------------------------------------------------------------- #
def test_counts_from_mirror_groups_by_code(conn):
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, catalogue, "
        "mirror_rev, updated_at) VALUES "
        "('a','A','001','LJ',1,now()), ('b','B','001','LJ',1,now()), "
        "('c','C','041','LJ',1,now())"
    )

    assert reconcile.counts_from_mirror(conn) == {"001": 2, "041": 1}


def test_load_vendor_master_is_scoped_to_the_one_code_source(conn):
    """Same reasoning as `vendor_master`'s own reader: the column stays, so the
    filter stays, so a row under another value stays invisible."""
    from app import vendor_master as vm

    vm.apply(conn, (VendorRow("001", "THE MAKER"),), batch="b")
    conn.execute(
        "INSERT INTO vendor_master (code_source, code, name, import_batch) "
        "VALUES ('OTHER','002','INVISIBLE','b')")

    rows = reconcile.load_vendor_master(conn)

    assert [(r.code, r.name) for r in rows] == [("001", "THE MAKER")]


@pytest.fixture
def playbook_dir(tmp_path, monkeypatch):
    """A controlled playbook directory. The CLI tests must not assert on the
    authored tree: those files get fixed (that is the point of the report), and
    a test that fails when the repo improves is a test that gets deleted."""
    import json

    d = tmp_path / "playbooks"
    d.mkdir()
    monkeypatch.setattr(pb_mod, "PLAYBOOKS_DIR", d)

    def write(slug, **body):
        (d / f"{slug}.json").write_text(json.dumps({"manufacturer": slug.upper(), **body}))
        return d

    return write


def test_cmd_exits_1_on_blocking_findings_and_writes_nothing(
    conn, test_db_url, playbook_dir, monkeypatch, capsys
):
    from app import cli, vendor_master as vm

    monkeypatch.setenv("DATABASE_URL", test_db_url)
    playbook_dir("orphan", bc_codes=[])          # no join to anything
    vm.apply(conn, (VendorRow("035", "DENTSPLY"),), batch="b")
    conn.commit()

    rc = cli.main(["playbooks", "reconcile"])

    out = capsys.readouterr().out
    assert rc == 1
    assert "blocking finding" in out and "Nothing was written" in out
    assert conn.execute(
        "SELECT count(*) AS n FROM manufacturer_alias"
    ).fetchone()["n"] == 0


def test_cmd_exits_0_when_every_playbook_joins(
    conn, test_db_url, playbook_dir, monkeypatch, capsys
):
    from app import cli, vendor_master as vm

    monkeypatch.setenv("DATABASE_URL", test_db_url)
    playbook_dir("dentsply", bc_codes=[{"catalogue": "LJ", "code": "035"}])
    vm.apply(conn, (VendorRow("035", "DENTSPLY"),), batch="b")
    conn.commit()

    rc = cli.main(["playbooks", "reconcile"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "BLOCKING (0)" in out
    assert conn.execute(
        "SELECT count(*) AS n FROM manufacturer_alias"
    ).fetchone()["n"] == 0, "a clean report is still not a write"


def test_cmd_refuses_cleanly_when_the_master_is_empty(test_db_url, monkeypatch, capsys):
    from app import cli

    monkeypatch.setenv("DATABASE_URL", test_db_url)

    rc = cli.main(["playbooks", "reconcile"])

    captured = capsys.readouterr()
    assert rc == 2
    assert "vendor_master is empty" in captured.err
    assert "Traceback" not in captured.err + captured.out


def test_cmd_warns_when_it_has_no_item_counts_at_all(
    conn, test_db_url, playbook_dir, monkeypatch, capsys
):
    """Pre-ingest the mirror is empty, so every finding reads as 0 items. That
    is a property of the run, not of the playbooks, and must be said."""
    from app import cli, vendor_master as vm

    monkeypatch.setenv("DATABASE_URL", test_db_url)
    playbook_dir("dentsply", bc_codes=[{"catalogue": "LJ", "code": "035"}])
    vm.apply(conn, (VendorRow("035", "DENTSPLY"),), batch="b")
    conn.commit()

    cli.main(["playbooks", "reconcile"])

    assert "no item counts available" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# the real tree, against the real master
# --------------------------------------------------------------------------- #
def test_every_real_playbook_joins_to_the_real_master():
    """The regression guard the plan's step-4 criterion turned into.

    The report first flagged four blocking findings on the authored tree:
    `dentsply-sirona` dead (`DENSTPLY` matches no master name), `komet` and
    `voco` unjoined (no `bc_codes` at all), and `ivoclar` claiming only 001 of
    the 001/005/275 entity. All four are fixed; this keeps them fixed, and will
    fail the moment a new playbook is authored without the one field that makes
    it reachable.
    """
    from app import vendor_master as vm

    export = pathlib.Path("imports/Proizvajalci.xlsx")
    if not export.exists():
        pytest.skip("vendor master export not present")

    rows = vm.read_file(export)
    loaded = pb_mod.load_playbooks()
    r = reconcile.build(rows, loaded, {}, {})

    assert [f"{f.kind}:{f.subject}" for f in r.blocking] == []

    # Entity-level, not per code: the whole IVOCLAR VIVADENT entity resolves to
    # the authored name, which is what stops it splitting into two namespaces.
    (ivoclar,) = [e for e in r.entities if e.slug == "ivoclar"]
    assert ivoclar.codes == ("001", "005", "275")
    assert ivoclar.canonical == "IVOCLAR"

    claimed = {e.slug for e in r.entities if e.slug}
    assert claimed == {pb.slug for pb in loaded}, "every playbook claims a code"
