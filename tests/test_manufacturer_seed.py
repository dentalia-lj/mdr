"""Seeding the manufacturer ENTITY tables (migration 049).

The seed's job is to make `reconcile._entities` -- until now a value computed
inside one CLI command and thrown away -- into rows. What these tests protect is
mostly the *refusals*: the constraints exist so that an authoring mistake is a
write that fails, not a wrong link discovered months later by RESOLVE. A test
that only proved the happy path would miss the entire point of shredding
identity into columns.
"""

from __future__ import annotations

import psycopg
import pytest

from app import manufacturer_seed
from app.playbooks import BcCode, Playbook

REFUSED: frozenset = frozenset()


@pytest.fixture(autouse=True)
def _clear_entity_tables(conn):
    """`manufacturer` and `manufacturer_name` are deliberately NOT in
    conftest's `_RESET_TABLES` -- that list mirrors the FK closure of the old
    cascade, and these are outside it (see the assertion in
    `test_reset_clears_every_table_the_old_cascade_reached`, which checks the
    set matches in BOTH directions).

    So rows survive between test FILES, and every assertion below that counts
    `manufacturer` globally would otherwise be reading whatever
    `tests/test_web.py`'s contact tests left behind. That is not hypothetical:
    it failed as `assert 'CONTACT CO' == 'IVOCLAR VIVADENT AG'` under
    `-n 4 --dist loadfile`, and passed on earlier runs only because the workers
    happened to schedule the two files differently.

    Clearing here rather than widening `_RESET_TABLES` keeps the closure
    invariant intact and puts the cleanup next to the tests that need it.
    """
    # Children first: `manufacturer_playbook_revision` FKs to `manufacturer`
    # (migration 050), so the parent delete fails on any revision left behind.
    conn.execute("DELETE FROM manufacturer_playbook_revision")
    conn.execute("DELETE FROM manufacturer_name")
    conn.execute("DELETE FROM manufacturer_bc_code")
    conn.execute("DELETE FROM manufacturer")
    conn.commit()
    yield


def _vendor(conn, rows, code_source="LJ"):
    for code, name in rows:
        conn.execute(
            "INSERT INTO vendor_master (code_source, code, name, import_batch) "
            "VALUES (%s,%s,%s,'test')",
            (code_source, code, name),
        )


def _pb(slug, manufacturer, codes=(), aliases=()):
    return Playbook(
        slug=slug,
        manufacturer=manufacturer,
        bc_codes=tuple(BcCode(code=c) for c in codes),
        aliases=tuple(aliases),
    )


def _seed(conn, loaded=(), raw=None):
    # `refused` is passed explicitly so validate() does not read the repo's real
    # robots_refused.txt from a unit test. `raw` defaults to {} rather than None
    # for the same reason: None would send the seed to the real playbooks/.
    import app.playbooks as playbooks_mod

    original = playbooks_mod.load_robots_refused
    playbooks_mod.load_robots_refused = lambda *a, **k: REFUSED
    try:
        return manufacturer_seed.seed(conn, loaded=tuple(loaded),
                                      raw=raw if raw is not None else {})
    finally:
        playbooks_mod.load_robots_refused = original


# --------------------------------------------------------------------------- #
# what it writes
# --------------------------------------------------------------------------- #
def test_unclaimed_vendor_codes_become_one_entity_per_name(conn):
    _vendor(conn, [("001", "IVOCLAR"), ("005", "IVOCLAR"), ("012", "SIRONA")])

    stats = _seed(conn)

    assert stats.named == 2                      # IVOCLAR (2 codes) + SIRONA
    assert stats.manufacturers_inserted == 2
    assert stats.codes_inserted == 3
    # canonical names only -- no playbook, so no aliases
    assert stats.names_inserted == 2
    rows = conn.execute(
        "SELECT m.canonical_name, count(*) AS codes "
        "FROM manufacturer m JOIN manufacturer_bc_code c ON c.manufacturer_id = m.id "
        "GROUP BY 1 ORDER BY 1"
    ).fetchall()
    assert [(r["canonical_name"], r["codes"]) for r in rows] == [("IVOCLAR", 2), ("SIRONA", 1)]


def test_authored_codes_are_marked_playbook_derived_ones_are_not(conn):
    """The distinction migration 017 draws for `manufacturer_alias.source`, and
    for the same reason: a BC refresh may re-point a derived code and must never
    re-point one a human named."""
    _vendor(conn, [("001", "IVOCLAR"), ("005", "IVOCLAR")])

    # The playbook names ONE code; the union-find pulls in the second because it
    # shares a vendor-master name.
    _seed(conn, [_pb("ivoclar", "IVOCLAR VIVADENT AG", codes=["001"])])

    rows = conn.execute(
        "SELECT code, source FROM manufacturer_bc_code ORDER BY code"
    ).fetchall()
    assert [(r["code"], r["source"]) for r in rows] == [
        ("001", "playbook"),
        ("005", "vendor-master"),
    ]


def test_playbook_name_wins_over_the_vendor_master_name(conn):
    _vendor(conn, [("001", "IVOCLAR")])

    _seed(conn, [_pb("ivoclar", "IVOCLAR VIVADENT AG", codes=["001"])])

    row = conn.execute("SELECT canonical_name, slug FROM manufacturer").fetchone()
    assert row["canonical_name"] == "IVOCLAR VIVADENT AG"
    assert row["slug"] == "ivoclar"


def test_aliases_are_written_casefolded_alongside_the_canonical(conn):
    _vendor(conn, [("001", "IVOCLAR")])

    _seed(conn, [_pb("ivoclar", "IVOCLAR VIVADENT AG", codes=["001"],
                     aliases=["Ivoclar Vivadent", "IVOCLAR AG"])])

    rows = conn.execute(
        "SELECT name_folded, name, kind FROM manufacturer_name ORDER BY name_folded"
    ).fetchall()
    assert [(r["name_folded"], r["kind"]) for r in rows] == [
        ("ivoclar ag", "alias"),
        ("ivoclar vivadent", "alias"),
        ("ivoclar vivadent ag", "canonical"),
    ]
    # the display spelling is preserved, only the key is folded
    assert {r["name"] for r in rows} >= {"Ivoclar Vivadent", "IVOCLAR AG"}


def test_seed_is_idempotent(conn):
    _vendor(conn, [("001", "IVOCLAR"), ("012", "SIRONA")])
    first = _seed(conn, [_pb("ivoclar", "IVOCLAR", codes=["001"], aliases=["Ivoclar AG"])])
    assert first.manufacturers_inserted == 2

    second = _seed(conn, [_pb("ivoclar", "IVOCLAR", codes=["001"], aliases=["Ivoclar AG"])])

    assert second.manufacturers_inserted == 0
    assert second.codes_inserted == 0
    assert second.names_inserted == 0
    assert second.manufacturers_unchanged == 2
    assert second.clean


def test_a_playbook_authored_later_attaches_its_slug_to_the_existing_row(conn):
    _vendor(conn, [("001", "IVOCLAR")])
    _seed(conn)                                   # seeded before anyone authored
    assert conn.execute("SELECT slug FROM manufacturer").fetchone()["slug"] is None

    stats = _seed(conn, [_pb("ivoclar", "IVOCLAR", codes=["001"])])

    assert stats.manufacturers_updated == 1
    assert conn.execute("SELECT slug FROM manufacturer").fetchone()["slug"] == "ivoclar"


# --------------------------------------------------------------------------- #
# what it skips, and says so
# --------------------------------------------------------------------------- #
def test_a_nameless_unclaimed_code_produces_no_row(conn):
    """`vendor_master.name` is nullable -- one of BC's 390 delivered rows has no
    name. A row for it would take the canonical_name slot for 'unknown'."""
    _vendor(conn, [("001", "IVOCLAR"), ("999", None)])

    stats = _seed(conn)

    assert stats.skipped_unnamed == 1
    assert stats.named == 1
    assert conn.execute("SELECT count(*) AS n FROM manufacturer").fetchone()["n"] == 1


def test_an_empty_canonical_name_produces_no_row(conn):
    _vendor(conn, [("001", "")])

    stats = _seed(conn)

    assert stats.skipped_unnamed == 1
    assert conn.execute("SELECT count(*) AS n FROM manufacturer").fetchone()["n"] == 0


def test_orphan_groups_are_reported_never_invented(conn):
    """A group resolved under a name no entity carries -- the live
    `3SHAPE MEDICAL A/S` case, 3 groups stranded by an alias repoint that never
    re-resolved. The seed must name them, not create rows to make them fit."""
    _vendor(conn, [("001", "IVOCLAR")])
    conn.execute(
        "INSERT INTO item_group (canonical_manufacturer) VALUES ('3SHAPE MEDICAL A/S'), "
        "('3SHAPE MEDICAL A/S'), ('')"
    )

    stats = _seed(conn)

    assert dict(stats.orphan_groups) == {"3SHAPE MEDICAL A/S": 2, "": 1}
    assert conn.execute(
        "SELECT count(*) AS n FROM manufacturer WHERE canonical_name = '3SHAPE MEDICAL A/S'"
    ).fetchone()["n"] == 0


# --------------------------------------------------------------------------- #
# the refusals -- the reason identity is shredded into columns at all
# --------------------------------------------------------------------------- #
def test_a_bc_code_vendor_master_does_not_issue_is_refused(conn):
    """No check for this exists today: a playbook may claim a code that does not
    exist and nothing notices. The FK is the check."""
    _vendor(conn, [("001", "IVOCLAR")])
    mfr = conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES ('X') RETURNING id"
    ).fetchone()["id"]

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO manufacturer_bc_code (code_source, code, manufacturer_id, source) "
            "VALUES ('LJ','99999',%s,'playbook')",
            (mfr,),
        )


def test_one_bc_code_cannot_belong_to_two_manufacturers(conn):
    _vendor(conn, [("001", "IVOCLAR")])
    a = conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES ('A') RETURNING id"
    ).fetchone()["id"]
    b = conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES ('B') RETURNING id"
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO manufacturer_bc_code VALUES ('LJ','001',%s,'playbook')", (a,)
    )

    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO manufacturer_bc_code VALUES ('LJ','001',%s,'playbook')", (b,)
        )


def test_one_name_cannot_belong_to_two_manufacturers_even_across_case(conn):
    """`playbooks.validate()` compares casefolded, `manufacturer_alias.raw_name`
    does not. The PK is keyed on the folded form so the two cannot disagree."""
    a = conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES ('A') RETURNING id"
    ).fetchone()["id"]
    b = conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES ('B') RETURNING id"
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO manufacturer_name VALUES ('sirona','SIRONA',%s,'canonical')", (a,)
    )

    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO manufacturer_name VALUES ('sirona','Sirona',%s,'alias')", (b,)
        )


def test_an_alias_claiming_another_bc_brands_name_refuses_the_whole_seed(conn):
    """The `[brand-parent-aliases]` shape: `SIRONA` authored as a DENTSPLY
    alias while SIRONA is its own BC brand.

    Until task 12 this was reported and skipped -- the rest of the seed
    applied, and an operator reading only the exit code saw a write that
    worked. `playbooks.validate` now sees it, because the seed feeds it the
    vendor-master brand map, so the refusal comes BEFORE any row is written.
    Refusing the whole import is the point: a partial identity import is the
    state nothing downstream knows how to interpret.
    """
    _vendor(conn, [("012", "SIRONA"), ("022", "DENTSPLY")])

    with pytest.raises(manufacturer_seed.playbooks_mod.BrandCollision) as exc:
        _seed(conn, [_pb("dentsply", "DENTSPLY", codes=["022"], aliases=["SIRONA"])])

    assert "SIRONA" in str(exc.value) and "012" in str(exc.value)
    conn.rollback()
    assert conn.execute("SELECT count(*) AS n FROM manufacturer").fetchone()["n"] == 0


def test_a_name_another_manufacturer_already_holds_is_still_reported(conn):
    """`_write_name`'s conflict path survives the guard above, and is not
    redundant with it: `validate` only sees the FILES it was handed, while
    this sees the DATABASE. A name can be owned by another manufacturer
    because an earlier import wrote it, or because someone edited it in the
    UI -- neither is visible to a file-level check.
    """
    _vendor(conn, [("100", "ALPHA"), ("200", "BETA")])
    _seed(conn, [_pb("alpha", "ALPHA", codes=["100"], aliases=["Shared Name Ltd"])])

    # A second import of a DIFFERENT file set: nothing in it collides with
    # anything else in it, and no BC brand is called `Shared Name Ltd`.
    stats = _seed(conn, [_pb("beta", "BETA", codes=["200"], aliases=["Shared Name Ltd"])])

    assert not stats.clean
    assert any("Shared Name Ltd" in subject for subject, _, _ in stats.conflicts)
    owner = conn.execute(
        "SELECT m.canonical_name FROM manufacturer_name n "
        "JOIN manufacturer m ON m.id = n.manufacturer_id "
        "WHERE n.name_folded='shared name ltd'"
    ).fetchone()
    assert owner["canonical_name"] == "ALPHA"


def test_source_and_kind_vocabularies_are_closed(conn):
    _vendor(conn, [("001", "IVOCLAR")])
    mfr = conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES ('X') RETURNING id"
    ).fetchone()["id"]

    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO manufacturer_bc_code VALUES ('LJ','001',%s,'invented')", (mfr,)
        )
    conn.rollback()

    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO manufacturer_name VALUES ('x','X',%s,'invented')", (mfr,)
        )


# --------------------------------------------------------------------------- #
# the behaviour half (migration 050)
# --------------------------------------------------------------------------- #
def _body(conn, canonical):
    return conn.execute(
        "SELECT body, playbook_rev FROM manufacturer WHERE canonical_name=%s",
        (canonical,)).fetchone()


def test_the_body_is_the_file_minus_what_049_shredded(conn):
    """`manufacturer`, `bc_codes` and `aliases` live in constrained tables now.
    A copy in the blob would be a second truth for the one thing the migration
    exists to constrain."""
    _vendor(conn, [("001", "VOCO")])
    raw = {"manufacturer": "VOCO", "bc_codes": [{"code": "001"}],
           "aliases": ["VOCO GmbH"], "domains": ["voco.dental"],
           "doc_sources": [{"kind": "portal", "url": "https://voco.dental/x"}]}
    _seed(conn, [_pb("voco", "VOCO", ["001"], ["VOCO GmbH"])], raw={"voco": raw})

    body = _body(conn, "VOCO")["body"]
    assert set(body) == {"domains", "doc_sources"}
    assert body["domains"] == ["voco.dental"]


def test_the_t0_parse_keys_survive_into_the_body(conn):
    """`match`, `ref_strategy` and `ref_strategy_config` are NOT `Playbook`
    fields -- `t0_layout` reads them straight off the file. Building the body
    from a parsed `Playbook` would drop the parse template of all five
    playbooks that carry one, and nothing downstream would say so."""
    _vendor(conn, [("021", "KOMET")])
    raw = {"manufacturer": "KOMET", "bc_codes": [{"code": "021"}], "aliases": [],
           "match": {"anchors": ["Artikelnummer"]}, "ref_strategy": "column",
           "ref_strategy_config": {"field_pattern": "^[0-9]{6}$"}}
    _seed(conn, [_pb("komet", "KOMET", ["021"])], raw={"komet": raw})

    body = _body(conn, "KOMET")["body"]
    assert body["ref_strategy"] == "column"
    assert body["match"]["anchors"] == ["Artikelnummer"]
    assert body["ref_strategy_config"]["field_pattern"] == "^[0-9]{6}$"


def test_rev_is_promoted_to_a_column_and_kept_out_of_the_body(conn):
    """Two copies of a version number is one copy too many: the revision table
    keys on `manufacturer.playbook_rev`, so a stale `rev` inside the blob would
    be a second answer to the same question."""
    _vendor(conn, [("001", "VOCO")])
    raw = {"manufacturer": "VOCO", "bc_codes": [{"code": "001"}], "aliases": [],
           "rev": 3, "domains": ["voco.dental"]}
    _seed(conn, [_pb("voco", "VOCO", ["001"])], raw={"voco": raw})

    row = _body(conn, "VOCO")
    assert row["playbook_rev"] == 3
    assert "rev" not in row["body"]


def test_seeding_writes_revision_zero_as_the_files_provenance(conn):
    """`extraction_attempt` has carried `(playbook_slug, playbook_rev)` since
    031. Without this row those columns name a version nobody can read back."""
    _vendor(conn, [("001", "VOCO")])
    raw = {"manufacturer": "VOCO", "bc_codes": [{"code": "001"}], "aliases": [],
           "domains": ["voco.dental"]}
    _seed(conn, [_pb("voco", "VOCO", ["001"])], raw={"voco": raw})

    rows = conn.execute(
        "SELECT r.rev, r.body, r.authored_by, r.note "
        "FROM manufacturer_playbook_revision r "
        "JOIN manufacturer m ON m.id = r.manufacturer_id "
        "WHERE m.canonical_name = 'VOCO'").fetchall()
    assert len(rows) == 1
    assert rows[0]["rev"] == 0
    assert rows[0]["note"] == "seeded from playbooks/voco.json"
    assert rows[0]["body"]["domains"] == ["voco.dental"]


def test_reseeding_the_same_files_adds_no_revision_and_reports_unchanged(conn):
    """The import is re-runnable. A second run that appended a duplicate
    revision 0 would make the history unreadable, and the table grants no
    UPDATE to fix it afterwards."""
    _vendor(conn, [("001", "VOCO")])
    raw = {"voco": {"manufacturer": "VOCO", "bc_codes": [{"code": "001"}],
                    "aliases": [], "domains": ["voco.dental"]}}
    loaded = [_pb("voco", "VOCO", ["001"])]

    first = _seed(conn, loaded, raw=raw)
    second = _seed(conn, loaded, raw=raw)

    assert first.bodies_inserted == 1 and first.bodies_unchanged == 0
    assert second.bodies_inserted == 0 and second.bodies_unchanged == 1
    assert second.clean
    assert conn.execute(
        "SELECT count(*) n FROM manufacturer_playbook_revision").fetchone()["n"] == 1


def test_a_body_edited_since_the_seed_is_reported_never_reverted(conn):
    """The failure this refuses: someone fixes a playbook in the UI (slice 3a),
    a later import runs, and the fix silently becomes whatever the file still
    says. Same posture as the slug conflict -- report, write nothing."""
    _vendor(conn, [("001", "VOCO")])
    raw = {"voco": {"manufacturer": "VOCO", "bc_codes": [{"code": "001"}],
                    "aliases": [], "domains": ["voco.dental"]}}
    loaded = [_pb("voco", "VOCO", ["001"])]
    _seed(conn, loaded, raw=raw)

    conn.execute(
        """UPDATE manufacturer
              SET body = '{"domains": ["edited-in-the-ui.example"]}',
                  playbook_rev = 1
            WHERE canonical_name = 'VOCO'""")

    stats = _seed(conn, loaded, raw=raw)

    assert not stats.clean
    assert any("body 'voco'" in c[0] for c in stats.conflicts)
    row = _body(conn, "VOCO")
    assert row["body"]["domains"] == ["edited-in-the-ui.example"]
    assert row["playbook_rev"] == 1


def test_a_canonical_name_renamed_since_the_seed_is_reported_never_duplicated(conn):
    """The prerequisite for the D1 rename action (slice 3b task 13).

    The entity lookup was keyed on `canonical_name`, so a row renamed after the
    import -- which is exactly what a UI rename does -- was invisible to the
    next run: the file still says the old name, no row matches it, and the
    seed INSERTs. `slug` is UNIQUE (migration 049), so that INSERT does not
    quietly duplicate the manufacturer, it raises and takes the WHOLE import
    down. Either way a rename bricks re-seeding until someone edits the file.

    `slug` is the stable identity here -- the playbook's own name for itself,
    and the only key both sides agree on across a rename. Found by lookup, the
    divergence is a conflict, which is the same posture as an edited body and
    a re-pointed slug: report, write nothing, let a human decide which side is
    right.
    """
    _vendor(conn, [("001", "VOCO")])
    loaded = [_pb("voco", "VOCO", ["001"])]
    _seed(conn, loaded)

    conn.execute("UPDATE manufacturer SET canonical_name='VOCO GMBH' WHERE slug='voco'")

    stats = _seed(conn, loaded)

    assert not stats.clean
    assert any("voco" in c[0] and "canonical" in c[0] for c in stats.conflicts), \
        stats.conflicts
    # one row, still carrying the rename
    rows = conn.execute(
        "SELECT canonical_name FROM manufacturer WHERE slug='voco'").fetchall()
    assert [r["canonical_name"] for r in rows] == ["VOCO GMBH"]


def test_a_manufacturer_with_no_playbook_keeps_a_null_body(conn):
    """~350 of the 384 rows. `body IS NULL` is the "nothing authored" state and
    is what the onboarding worklist filters on."""
    _vendor(conn, [("001", "IVOCLAR")])
    _seed(conn)

    assert _body(conn, "IVOCLAR")["body"] is None


def test_every_real_playbook_seeds_a_body_that_parses_back(conn):
    """The repo's actual 33 files, not fixtures. This is the check that the
    body is a faithful carrier: seed each file, read the row back, and confirm
    the six parse templates and every authored key survived the round trip.

    Slice 2 task 9 adds the stronger version of this -- that a `Playbook` built
    from the row EQUALS the one built from the file. This is the cheaper
    precondition: nothing was dropped on the way in."""
    from app import playbooks as playbooks_mod

    real_raw = playbooks_mod.load_raw()
    real_loaded = playbooks_mod.load_playbooks()
    assert len(real_raw) == len(real_loaded) > 0

    # vendor_master is the FK target for every authored code.
    _vendor(conn, sorted({(bc.code, pb.manufacturer)
                          for pb in real_loaded for bc in pb.bc_codes}))

    stats = _seed(conn, real_loaded, raw=real_raw)
    assert stats.clean, stats.conflicts
    assert stats.bodies_inserted == len(real_raw)

    rows = {r["slug"]: r for r in conn.execute(
        "SELECT slug, body, playbook_rev FROM manufacturer WHERE body IS NOT NULL")}
    assert set(rows) == set(real_raw)

    for slug, file_json in real_raw.items():
        expected = {k: v for k, v in file_json.items()
                    if k not in playbooks_mod.BODY_EXCLUDED_KEYS}
        assert rows[slug]["body"] == expected, slug
        assert rows[slug]["playbook_rev"] == int(file_json.get("rev", 0) or 0)

    with_template = [s for s, r in rows.items() if "ref_strategy" in r["body"]]
    # 6 since 2026-09-11: NEODENT ([neodent-ref-list-needs-t0]).
    assert len(with_template) == 6, with_template


# --------------------------------------------------------------------------- #
# Bring-up preconditions. Both found by scripts/bringup-rehearsal.sh on a real
# empty database, not by any test — the fixtures here have always seeded
# vendor_master, so the fresh-install order was never exercised.
# --------------------------------------------------------------------------- #
def test_seeding_before_the_vendor_master_refuses_instead_of_fk_crashing(conn):
    """`manufacturer_bc_code` is FK'd to `vendor_master`, so this used to die
    mid-write with a raw ForeignKeyViolation naming code `001` — a message
    about the symptom, saying nothing about the import that was missing."""
    loaded = [_pb("ivoclar", "IVOCLAR VIVADENT AG", codes=["001"])]

    with pytest.raises(manufacturer_seed.MissingVendorMaster) as exc:
        manufacturer_seed.seed(conn, loaded=loaded, raw={})

    # The remedy is IN the message: the whole defect was an ordering that
    # existed only in someone's head.
    assert "vendor-master" in str(exc.value)
    assert "001" in str(exc.value)


def test_the_refusal_writes_nothing(conn):
    loaded = [_pb("ivoclar", "IVOCLAR VIVADENT AG", codes=["001"])]
    with pytest.raises(manufacturer_seed.MissingVendorMaster):
        manufacturer_seed.seed(conn, loaded=loaded, raw={})
    conn.rollback()

    n = conn.execute("SELECT count(*) AS n FROM manufacturer").fetchone()["n"]
    assert n == 0


def test_the_guard_is_the_crash_condition_not_an_empty_vendor_master(conn):
    """The guard fires on "empty master AND a playbook names a code", not on
    "master is empty". A playbook claiming no code has nothing to foreign-key,
    so it must not be refused.

    It also seeds nothing: entities are derived from vendor-master codes, so
    with no master and no claimed codes there is no entity to write. The point
    here is the ABSENCE of the refusal, not a write.
    """
    stats = manufacturer_seed.seed(conn, loaded=[_pb("acme", "ACME AG")], raw={})

    assert stats.entities == 0


def test_a_seed_that_wrote_no_aliases_says_so_and_names_the_next_command(conn):
    """381 manufacturers and 0 aliases was a silent, successful-looking dead
    end: RESOLVE reads the alias projection, so every group came out unnamed
    while every line of this command's output said it had worked."""
    _vendor(conn, [("001", "IVOCLAR")])
    stats = manufacturer_seed.seed(
        conn, loaded=[_pb("ivoclar", "IVOCLAR VIVADENT AG", codes=["001"])], raw={})
    assert stats.alias_rows == 0

    body = "\n".join(manufacturer_seed.render(stats))
    assert "manufacturer_alias is EMPTY" in body
    assert "playbooks sync" in body


def test_the_alias_warning_is_silent_once_the_projection_exists(conn):
    """Steady state must stay quiet, or the line becomes noise people skip."""
    _vendor(conn, [("001", "IVOCLAR")])
    stats = manufacturer_seed.seed(
        conn, loaded=[_pb("ivoclar", "IVOCLAR VIVADENT AG", codes=["001"])], raw={})
    conn.execute(
        "INSERT INTO manufacturer_alias (raw_name, canonical_name, source) "
        "VALUES ('ivoclar','IVOCLAR VIVADENT AG','playbook')")
    stats.alias_rows = conn.execute(
        "SELECT count(*) AS n FROM manufacturer_alias").fetchone()["n"]

    assert "manufacturer_alias is EMPTY" not in "\n".join(
        manufacturer_seed.render(stats))
