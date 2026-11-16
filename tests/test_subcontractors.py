"""Who a listed subcontractor makes for (spec 2026-11-13-subcontractors-design.md § 4.1-4.2).

Playbooks are built in memory and passed explicitly, so these tests never read
the repository's `playbooks/` directory.
"""
from __future__ import annotations

from app import subcontractors
from app.manufacturers import normalize
from app.playbooks import BcCode, Playbook

KOMET = Playbook(slug="komet", manufacturer="KOMET", bc_codes=(BcCode("077"),),
                 subcontractors=("Sure Dent Corporation", "WOODPECKER"))
WOODPECKER = Playbook(slug="woodpecker", manufacturer="WOODPECKER", bc_codes=(BcCode("430"),),
                      aliases=("Guilin Woodpecker Medical Instrument Co.,Ltd",))
SANOLABOR = Playbook(slug="sanolabor", manufacturer="SANOLABOR", bc_codes=(BcCode("312"),),
                     subcontractors=("CHIRANA T. Injecta a.s.", "CHIRANA T. Injecta, a.s."))
PBS = (KOMET, WOODPECKER, SANOLABOR)


def _alias(conn, raw, canonical):
    conn.execute("INSERT INTO manufacturer_alias (raw_name, canonical_name, source) "
                 "VALUES (%s,%s,'playbook')", (raw, canonical))


def _item(conn, ref, raw):
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, catalogue, "
        "mirror_rev, updated_at) VALUES (%s,'n',%s,TRUE,'LJ',1,now())", (ref, raw))


def _seed(conn):
    _alias(conn, "077", "KOMET")
    _alias(conn, "430", "WOODPECKER")
    _alias(conn, "Guilin Woodpecker Medical Instrument Co.,Ltd", "WOODPECKER")


def test_listing_brands_matches_a_listed_name(conn):
    _seed(conn)
    assert subcontractors.listing_brands(conn, "SURE DENT CORPORATION", PBS) == ["KOMET"]


def test_listing_brands_matches_the_comma_spelling(conn):
    _seed(conn)
    assert subcontractors.listing_brands(conn, "CHIRANA T. Injecta, a.s.", PBS) == ["SANOLABOR"]


def test_listing_brands_resolves_an_alias_to_a_listed_canonical(conn):
    _seed(conn)
    assert subcontractors.listing_brands(
        conn, "Guilin Woodpecker Medical Instrument Co.,Ltd", PBS) == ["KOMET"]


def test_listing_brands_unknown_name(conn):
    _seed(conn)
    assert subcontractors.listing_brands(conn, "Ivoclar Vivadent AG", PBS) == []
    assert subcontractors.listing_brands(conn, None, PBS) == []
    assert subcontractors.listing_brands(conn, "", PBS) == []


def test_listing_brands_two_brands_sorted(conn):
    _seed(conn)
    other = Playbook(slug="aaa", manufacturer="AAA", subcontractors=("WOODPECKER",))
    assert subcontractors.listing_brands(conn, "WOODPECKER", PBS + (other,)) == ["AAA", "KOMET"]


def test_brands_listing_woodpecker():
    assert subcontractors.brands_listing("WOODPECKER", PBS) == ["KOMET"]
    assert subcontractors.brands_listing("SURE DENT", PBS) == []
    assert subcontractors.brands_listing(None, PBS) == []


def test_item_codes_with_listing_brands(conn):
    _seed(conn)
    _item(conn, "000 ENFILL", "077")
    _item(conn, "WACENDO3", "430")
    assert subcontractors.item_codes_with_listing_brands(conn, "WOODPECKER", PBS) == ["077", "430"]
    assert subcontractors.item_codes_with_listing_brands(conn, "KOMET", PBS) == ["077"]
    assert subcontractors.item_codes_with_listing_brands(conn, None, PBS) == []


def test_moved_names():
    assert subcontractors.moved_names(PBS) == {
        normalize("Sure Dent Corporation"),
        normalize("CHIRANA T. Injecta a.s."),
        normalize("CHIRANA T. Injecta, a.s."),
    }


# --------------------------------------------------------------------------- #
# Relative to the brand a document reached (final review C1-C3): a maker with a
# canonical of its own (WOODPECKER) is a subcontractor only for a brand that
# lists it; a pure subcontractor name (Sure Dent) is one wherever it lands.
# --------------------------------------------------------------------------- #
GUILIN = "Guilin Woodpecker Medical Instrument Co.,Ltd"


def test_own_makers(conn):
    _seed(conn)
    assert subcontractors.own_makers(conn, GUILIN, PBS) == ["WOODPECKER"]
    assert subcontractors.own_makers(conn, "WOODPECKER", PBS) == ["WOODPECKER"]
    assert subcontractors.own_makers(conn, "Sure Dent Corporation", PBS) == []


def test_own_makers_ignores_a_kept_alias_to_the_listing_brand(conn):
    """Code deployed before seed and sync: Sure Dent still resolves to KOMET."""
    _seed(conn)
    _alias(conn, "Sure Dent Corporation", "KOMET")
    assert subcontractors.own_makers(conn, "Sure Dent Corporation", PBS) == []


def test_subcontracted_for_a_pure_subcontractor_name(conn):
    _seed(conn)
    assert subcontractors.subcontracted_for(conn, "Sure Dent Corporation", "KOMET", PBS) == ["KOMET"]
    assert subcontractors.subcontracted_for(conn, "Sure Dent Corporation", None, PBS) == ["KOMET"]
    assert subcontractors.subcontracted_for(conn, "Sure Dent Corporation", "ULTRADENT", PBS) == []


def test_subcontracted_for_a_maker_in_its_own_right(conn):
    _seed(conn)
    assert subcontractors.subcontracted_for(conn, GUILIN, "KOMET", PBS) == ["KOMET"]
    assert subcontractors.subcontracted_for(conn, GUILIN, "WOODPECKER", PBS) == []
    assert subcontractors.subcontracted_for(conn, GUILIN, None, PBS) == []


def test_subcontracted_for_unlisted_name(conn):
    _seed(conn)
    assert subcontractors.subcontracted_for(conn, "Ivoclar Vivadent AG", None, PBS) == []
    assert subcontractors.subcontracted_for(conn, None, "KOMET", PBS) == []
