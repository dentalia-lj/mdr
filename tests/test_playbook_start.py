"""Starting a playbook from the UI (slice 4, the half the plan was missing).

`/manufacturers?no_playbook=1` has always listed who needs a playbook -- gaps
first, biggest first -- and until now clicking through offered nothing to
author: `manufacturer.slug` was written by `manufacturers seed` and nowhere
else (verified: one UPDATE and one INSERT, both in that module), so starting a
playbook meant hand-writing `playbooks/{slug}.json` in the repo and running an
import. That is the engineering bottleneck the whole move exists to remove, and
slice 4 was about to drop the directory without opening another way in.

The playbook starts EMPTY on purpose. Every Tier A and Tier B control already
built then applies, each with its own guard, rather than this route growing a
second authoring surface that would drift from them.
"""

from __future__ import annotations

import json

import pytest

from fastapi.testclient import TestClient

from app import manufacturer_seed
from app import playbooks as pb
from app.config import Web
from tests.conftest import TEST_API_URL
from web import registry
from web.app import create_app

USER = "user:tester"


@pytest.fixture
def client(test_db_url, tmp_path):
    return TestClient(create_app(Web(api_database_url=TEST_API_URL,
                                     imports_dir=str(tmp_path))))


@pytest.fixture(autouse=True)
def _clean(conn):
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


@pytest.fixture
def seeded(conn, tmp_path):
    """One manufacturer WITH a playbook and two without -- the state the
    onboarding worklist is looking at."""
    (tmp_path / "alpha.json").write_text(json.dumps({
        "manufacturer": "ALPHA GMBH",
        "bc_codes": [{"code": "001"}],
        "domains": ["alpha.example"],
    }))
    for code, name in (("001", "ALPHA GMBH"), ("002", "BETA AG"),
                       ("003", "GAMMA DENTAL S.R.L.")):
        conn.execute(
            "INSERT INTO vendor_master (code_source, code, name, import_batch) "
            "VALUES ('LJ',%s,%s,'start-test')", (code, name))
    conn.commit()

    original = pb.load_robots_refused
    pb.load_robots_refused = lambda *a, **k: frozenset()
    try:
        stats = manufacturer_seed.seed(conn, dir_path=tmp_path)
    finally:
        pb.load_robots_refused = original
    assert stats.clean, stats.conflicts
    conn.commit()
    return tmp_path


def _row(conn, canonical):
    return conn.execute(
        "SELECT id, slug, body, playbook_rev FROM manufacturer "
        "WHERE canonical_name=%s", (canonical,)).fetchone()


# --------------------------------------------------------------------------- #
# the suggestion
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name, expected", [
    ("BETA AG", "beta-ag"),
    ("GAMMA DENTAL S.R.L.", "gamma-dental-s-r-l"),
    ("3Shape TRIOS A/S", "3shape-trios-a-s"),
    ("  Voco  GmbH  ", "voco-gmbh"),
    ("", ""),
])
def test_slug_suggestions(name, expected):
    assert registry.slug_for(name) == expected


# --------------------------------------------------------------------------- #
# starting one
# --------------------------------------------------------------------------- #
def test_starting_a_playbook_attaches_a_slug_and_an_empty_body(conn, seeded):
    out = registry.start_playbook(conn, canonical_name="BETA AG",
                                  slug="beta", user=USER)

    assert out["slug"] == "beta"
    row = _row(conn, "BETA AG")
    assert row["slug"] == "beta"
    assert row["body"] == {}
    assert row["playbook_rev"] == 0


def test_it_writes_revision_zero_so_an_extraction_attempt_resolves(conn, seeded):
    """`extraction_attempt` carries `(playbook_slug, playbook_rev)`. A read
    steered by revision 0 must resolve to something, and here that something is
    "started empty, by a person, on this date"."""
    registry.start_playbook(conn, canonical_name="BETA AG", slug="beta", user=USER)

    rev = conn.execute(
        "SELECT r.rev, r.body, r.authored_by, r.note "
        "FROM manufacturer_playbook_revision r JOIN manufacturer m "
        "ON m.id = r.manufacturer_id WHERE m.slug='beta'").fetchone()
    assert rev["rev"] == 0 and rev["body"] == {}
    assert rev["authored_by"] == USER
    assert "BETA AG" in rev["note"]


def test_the_new_playbook_is_immediately_editable(conn, seeded):
    """The point of starting empty: the existing Tier A save path takes it from
    here, guards and all, with no second authoring surface."""
    registry.start_playbook(conn, canonical_name="BETA AG", slug="beta", user=USER)

    new_rev = registry.save_playbook_body(
        conn, slug="beta", body={"domains": ["beta.example"]},
        note="first authoring pass", user=USER, expected_rev=0,
        playbooks_dir=seeded)

    assert new_rev == 1
    assert _row(conn, "BETA AG")["body"] == {"domains": ["beta.example"]}


def test_identity_is_inherited_not_invented(conn, seeded):
    """The seed derived this entity from `vendor_master`; starting a playbook
    attaches to it and must not create a second one."""
    before = conn.execute("SELECT count(*) AS n FROM manufacturer").fetchone()["n"]

    registry.start_playbook(conn, canonical_name="BETA AG", slug="beta", user=USER)

    assert conn.execute(
        "SELECT count(*) AS n FROM manufacturer").fetchone()["n"] == before
    codes = [r["code"] for r in conn.execute(
        "SELECT c.code FROM manufacturer_bc_code c JOIN manufacturer m "
        "ON m.id = c.manufacturer_id WHERE m.slug='beta'").fetchall()]
    assert codes == ["002"]


def test_starting_records_who_did_it(conn, seeded):
    registry.start_playbook(conn, canonical_name="BETA AG", slug="beta", user=USER)

    assert _row(conn, "BETA AG")["slug"] == "beta"
    assert conn.execute(
        "SELECT updated_by FROM manufacturer WHERE slug='beta'"
    ).fetchone()["updated_by"] == USER


# --------------------------------------------------------------------------- #
# refusals
# --------------------------------------------------------------------------- #
def test_a_manufacturer_that_already_has_one_is_refused(conn, seeded):
    with pytest.raises(registry.SaveRefused, match="already has playbook"):
        registry.start_playbook(conn, canonical_name="ALPHA GMBH",
                                slug="alpha-2", user=USER)


def test_a_slug_another_manufacturer_holds_is_refused_naming_it(conn, seeded):
    with pytest.raises(registry.SaveRefused) as exc:
        registry.start_playbook(conn, canonical_name="BETA AG", slug="alpha",
                                user=USER)

    assert "ALPHA GMBH" in str(exc.value)


def test_a_manufacturer_with_no_row_is_refused(conn, seeded):
    with pytest.raises(registry.SaveRefused, match="manufacturers seed"):
        registry.start_playbook(conn, canonical_name="NOBODY LTD", slug="nobody",
                                user=USER)


def test_a_mixed_case_slug_is_folded_not_refused(conn, seeded):
    """Case is normalised rather than rejected: `Beta` and `beta` are the same
    page address, and refusing one of them teaches nothing."""
    out = registry.start_playbook(conn, canonical_name="BETA AG", slug="  Beta  ",
                                  user=USER)

    assert out["slug"] == "beta"
    assert _row(conn, "BETA AG")["slug"] == "beta"


@pytest.mark.parametrize("slug", [
    "", "   ", "beta ag", "beta_ag", "-beta", "beta-", "beta--ag",
    "beta/ag", "beta.json",
])
def test_unusable_slugs_are_refused(conn, seeded, slug):
    """A slug is a URL path segment and was a filename for all 33 delivered
    playbooks. Keeping the shape keeps the two eras one namespace."""
    with pytest.raises(registry.SaveRefused):
        registry.start_playbook(conn, canonical_name="BETA AG", slug=slug,
                                user=USER)


# --------------------------------------------------------------------------- #
# the page and the route
# --------------------------------------------------------------------------- #
def _alias(conn, code, canonical):
    """`/manufacturers/{name}` lists an entity's BC codes out of
    `manufacturer_alias`, which `playbooks sync` writes -- the seed does not
    touch it. Without a row the page 404s, so the onboarding worklist only
    reaches a manufacturer that has been synced."""
    conn.execute(
        "INSERT INTO manufacturer_alias (raw_name, canonical_name, source) "
        "VALUES (%s,%s,'vendor-master') ON CONFLICT (raw_name) DO NOTHING",
        (code, canonical))
    conn.commit()


def test_the_card_appears_only_where_there_is_no_playbook(conn, seeded, client,
                                                          monkeypatch):
    monkeypatch.setattr(pb, "PLAYBOOKS_DIR", seeded)
    _alias(conn, "002", "BETA AG")
    _alias(conn, "001", "ALPHA GMBH")

    without = client.get("/manufacturers/BETA AG").text
    with_one = client.get("/manufacturers/ALPHA GMBH").text

    assert "Start a playbook" in without
    assert 'value="beta-ag"' in without      # the suggestion is prefilled
    assert "Start a playbook" not in with_one


def test_route_starts_it_and_links_to_the_editor(conn, seeded, client, monkeypatch):
    monkeypatch.setattr(pb, "PLAYBOOKS_DIR", seeded)

    r = client.post("/manufacturers/BETA AG/playbook", data={"slug": "beta"})

    assert r.status_code == 200
    assert "/playbooks/beta" in r.text
    assert _row(conn, "BETA AG")["slug"] == "beta"


def test_route_falls_back_to_the_suggested_slug(conn, seeded, client, monkeypatch):
    monkeypatch.setattr(pb, "PLAYBOOKS_DIR", seeded)

    r = client.post("/manufacturers/BETA AG/playbook", data={"slug": ""})

    assert r.status_code == 200
    assert _row(conn, "BETA AG")["slug"] == "beta-ag"


def test_route_refusal_is_422_and_writes_nothing(conn, seeded, client, monkeypatch):
    monkeypatch.setattr(pb, "PLAYBOOKS_DIR", seeded)

    r = client.post("/manufacturers/BETA AG/playbook", data={"slug": "alpha"})

    assert r.status_code == 422
    assert _row(conn, "BETA AG")["slug"] is None
