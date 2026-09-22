"""The guided onboarding flow (S2.2).

Every control this flow uses was already built and already tested, so what
these cover is the thing that is actually new: the ROUTE between them, and the
queue's memory.

  * the queue offers work in the order it is worth doing, and only work that
    can be done -- a supplier with no articles has nothing for a recipe to
    cover;
  * a triaged-out supplier LEAVES the queue and stays out, which is the whole
    reason `onboarding_state` exists: measured 2026-09-04 the top of the queue
    by item count is SANOLABOR, a distributor, and a queue that offers the same
    dead end every morning is unusable by its second row;
  * a skip without a reason is refused -- months later "files nothing" and "we
    agreed it has nothing to file" look identical from the outside;
  * putting a supplier back KEEPS the note (060 grants no DELETE): who decided
    what, and who undid it, are both part of the trail;
  * the flow writes through the EXISTING save path, so a domain set here is a
    domain the playbook page can revert, and there is no second write path to
    drift.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Web
from tests.conftest import TEST_API_URL
from web import onboarding
from web.app import create_app


@pytest.fixture(autouse=True)
def _clean(conn):
    def wipe():
        conn.execute("DELETE FROM onboarding_state")
        conn.execute("DELETE FROM manufacturer_playbook_revision")
        conn.execute("DELETE FROM manufacturer_name")
        conn.execute("DELETE FROM manufacturer_bc_code")
        conn.execute("DELETE FROM manufacturer")
        conn.execute("DELETE FROM manufacturer_alias")
        conn.execute("DELETE FROM item_mirror")
        conn.commit()

    wipe()
    yield
    wipe()


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(Web(api_database_url=TEST_API_URL,
                                     imports_dir=str(tmp_path))))


def _supplier(conn, name, items=0, code=None, slug=None):
    mid = conn.execute(
        "INSERT INTO manufacturer (canonical_name, slug) VALUES (%s,%s) RETURNING id",
        (name, slug)).fetchone()["id"]
    conn.execute(
        "INSERT INTO manufacturer_alias (raw_name, canonical_name, source) "
        "VALUES (%s,%s,'vendor-master') ON CONFLICT (raw_name) DO NOTHING",
        (name, name))
    for i in range(items):
        conn.execute(
            "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, catalogue, updated_at) "
            "VALUES (%s,'x',%s,'LJ',now()) ON CONFLICT (item_ref) DO NOTHING",
            (f"{name}-{i}", name))
    if code:
        conn.execute(
            "INSERT INTO vendor_master (code_source, code, name, import_batch) "
            "VALUES ('LJ',%s,%s,'onb') ON CONFLICT DO NOTHING", (code, name))
        conn.execute(
            "INSERT INTO manufacturer_bc_code (manufacturer_id, code_source, code, source) "
            "VALUES (%s,'LJ',%s,'playbook')", (mid, code))
    conn.commit()
    return mid


# --- the queue -------------------------------------------------------------- #

def test_the_queue_offers_the_biggest_gap_first(conn):
    _supplier(conn, "SMALLCO", items=2)
    _supplier(conn, "BIGCO", items=9)
    _supplier(conn, "MIDCO", items=5)
    assert [r["canonical_name"] for r in onboarding.queue_rows(conn)] == [
        "BIGCO", "MIDCO", "SMALLCO"]


def test_a_supplier_with_a_recipe_is_not_in_the_queue(conn):
    _supplier(conn, "DONECO", items=9, slug="doneco")
    _supplier(conn, "TODOCO", items=2)
    assert [r["canonical_name"] for r in onboarding.queue_rows(conn)] == ["TODOCO"]


def test_a_supplier_with_no_articles_is_not_queued(conn):
    """It has nothing for a recipe to cover yet, so queueing it would offer
    work nobody can usefully do. Measured on the live database 2026-09-04: 345
    suppliers have no recipe and 328 of them have articles."""
    _supplier(conn, "EMPTYCO", items=0)
    _supplier(conn, "REALCO", items=1)
    assert [r["canonical_name"] for r in onboarding.queue_rows(conn)] == ["REALCO"]


def test_the_count_and_the_list_are_drawn_from_the_same_population(conn):
    """A count that outran its own list is how a worklist stops being
    believed."""
    for i in range(4):
        _supplier(conn, f"CO{i}", items=i + 1)
    _supplier(conn, "EMPTY", items=0)
    assert onboarding.queue_size(conn) == 4
    assert len(onboarding.queue_rows(conn)) == 4


# --- the exits -------------------------------------------------------------- #

def test_a_skipped_supplier_leaves_the_queue_and_stays_out(client, conn):
    """The reason the table exists. SANOLABOR sits at the top of the real queue
    by item count and is a DISTRIBUTOR; without this the flow offers the same
    dead end every morning."""
    _supplier(conn, "DISTCO", items=9)
    _supplier(conn, "REALCO", items=1)
    assert onboarding.queue_size(conn) == 2

    r = client.post("/onboarding/DISTCO/skip", follow_redirects=False,
                    data={"state": "not-a-manufacturer",
                          "reason": "distributor, sells other makers' products"})
    assert r.status_code == 303
    assert [x["canonical_name"] for x in onboarding.queue_rows(conn)] == ["REALCO"]
    assert onboarding.queue_size(conn) == 1


def test_a_skip_without_a_reason_is_refused(client, conn):
    _supplier(conn, "DISTCO", items=9)
    r = client.post("/onboarding/DISTCO/skip",
                    data={"state": "not-a-manufacturer", "reason": "   "})
    assert r.status_code == 422
    assert "say why" in r.text
    assert onboarding.queue_size(conn) == 1


def test_an_unknown_exit_reason_is_refused(client, conn):
    _supplier(conn, "DISTCO", items=9)
    r = client.post("/onboarding/DISTCO/skip",
                    data={"state": "because-i-said-so", "reason": "x"})
    assert r.status_code == 422
    assert onboarding.queue_size(conn) == 1


def test_skipping_twice_updates_the_reason_rather_than_failing(client, conn):
    """One ACTIVE note per supplier is a partial unique index; a second press
    must read as a correction, not a constraint violation on screen."""
    _supplier(conn, "DISTCO", items=9)
    client.post("/onboarding/DISTCO/skip", follow_redirects=False,
                data={"state": "no-website", "reason": "first answer"})
    r = client.post("/onboarding/DISTCO/skip", follow_redirects=False,
                    data={"state": "not-a-manufacturer", "reason": "second answer"})
    assert r.status_code == 303
    rows = onboarding.triaged_rows(conn)
    assert len(rows) == 1
    assert rows[0]["state"] == "not-a-manufacturer"
    assert rows[0]["reason"] == "second answer"


def test_putting_a_supplier_back_keeps_the_note(client, conn):
    """060 grants no DELETE, deliberately: who decided what, and who undid it,
    are both part of the trail."""
    _supplier(conn, "DISTCO", items=9)
    client.post("/onboarding/DISTCO/skip", follow_redirects=False,
                data={"state": "no-library", "reason": "no downloads page"})
    r = client.post("/onboarding/DISTCO/unskip", follow_redirects=False)
    assert r.status_code == 303

    assert onboarding.queue_size(conn) == 1            # back in the queue
    assert onboarding.triaged_rows(conn) == []         # no longer listed as out
    kept = conn.execute(
        "SELECT state, reason, cleared_by FROM onboarding_state "
        "WHERE canonical_name='DISTCO'").fetchall()
    assert len(kept) == 1
    assert kept[0]["reason"] == "no downloads page"
    assert kept[0]["cleared_by"]


def test_putting_back_a_supplier_that_is_in_the_queue_is_a_404(client, conn):
    _supplier(conn, "REALCO", items=1)
    assert client.post("/onboarding/REALCO/unskip").status_code == 404


# --- the steps -------------------------------------------------------------- #

def test_the_queue_page_names_who_is_next_rather_than_saying_next(client, conn):
    _supplier(conn, "BIGCO", items=9)
    body = client.get("/onboarding").text
    assert "Start with BIGCO" in body


def test_step_one_states_the_facts_before_asking_for_anything(client, conn):
    """A person deciding whether this is even a manufacturer needs the item
    count in front of them; asking for a name first and the facts never is how
    a distributor ends up with a playbook nobody wanted."""
    _supplier(conn, "BIGCO", items=3, code="901")
    body = client.get("/onboarding/BIGCO/start").text
    # "Items in Business Central": article, product and item are one word, and
    # it is Item (office UI redesign spec § 9, P7c).
    assert "Items in Business Central" in body
    assert "901" in body
    assert "nothing, which is why this supplier is in the queue" in body


def test_step_one_suggests_a_slug_and_the_start_creates_the_playbook(client, conn):
    _supplier(conn, "BIG CO GMBH", items=3)
    assert onboarding.suggest_slug("BIG CO GMBH") == "big-co-gmbh"

    r = client.post("/onboarding/BIG CO GMBH/start", follow_redirects=False,
                    data={"slug": "bigco"})
    assert r.status_code == 303
    assert r.headers["location"] == "/onboarding/bigco/domains"
    assert conn.execute(
        "SELECT slug, playbook_rev FROM manufacturer WHERE canonical_name='BIG CO GMBH'"
    ).fetchone()["slug"] == "bigco"


def test_a_refused_slug_comes_back_on_the_same_screen(client, conn):
    _supplier(conn, "ACO", items=3)
    _supplier(conn, "BCO", items=2, slug="taken")
    r = client.post("/onboarding/ACO/start", data={"slug": "taken"})
    assert r.status_code == 200
    assert "already" in r.text
    assert "Items in Business Central" in r.text        # still step 1, not a dead end


def test_starting_an_already_onboarded_supplier_goes_to_its_recipe(client, conn):
    _supplier(conn, "DONECO", items=3, slug="doneco")
    r = client.get("/onboarding/DONECO/start", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/playbooks/doneco"


def test_domains_write_through_the_existing_save_path(client, conn):
    """No second write path: a domain set here is a domain the playbook page
    can revert, and it lands as a numbered revision like any other save."""
    _supplier(conn, "BIGCO", items=3)
    client.post("/onboarding/BIGCO/start", follow_redirects=False, data={"slug": "bigco"})
    rev = conn.execute(
        "SELECT playbook_rev FROM manufacturer WHERE slug='bigco'").fetchone()["playbook_rev"]

    r = client.post("/onboarding/bigco/domains", follow_redirects=False,
                    data={"rev": rev, "domains": "bigco.example\nbigco.de"})
    assert r.status_code == 303
    assert r.headers["location"] == "/onboarding/bigco/library"

    row = conn.execute(
        "SELECT body, playbook_rev FROM manufacturer WHERE slug='bigco'").fetchone()
    assert row["body"]["domains"] == ["bigco.example", "bigco.de"]
    assert row["playbook_rev"] == rev + 1
    assert conn.execute(
        "SELECT count(*) AS n FROM manufacturer_playbook_revision r "
        "JOIN manufacturer m ON m.id=r.manufacturer_id WHERE m.slug='bigco'"
    ).fetchone()["n"] >= 2


def test_a_stale_domains_form_is_refused_and_says_so(client, conn):
    _supplier(conn, "BIGCO", items=3)
    client.post("/onboarding/BIGCO/start", follow_redirects=False, data={"slug": "bigco"})
    client.post("/onboarding/bigco/domains", follow_redirects=False,
                data={"rev": 0, "domains": "first.example"})
    r = client.post("/onboarding/bigco/domains", follow_redirects=False,
                    data={"rev": 0, "domains": "second.example"})
    assert r.status_code == 303
    assert "/domains?error=" in r.headers["location"]
    assert conn.execute(
        "SELECT body FROM manufacturer WHERE slug='bigco'"
    ).fetchone()["body"]["domains"] == ["first.example"]


def test_the_library_step_reuses_the_existing_probe_route(client, conn):
    """One probe implementation. Two would let this flow and the expert page
    show an operator different answers for the same recipe."""
    _supplier(conn, "BIGCO", items=3)
    client.post("/onboarding/BIGCO/start", follow_redirects=False, data={"slug": "bigco"})
    body = client.get("/onboarding/bigco/library").text
    assert 'hx-post="/playbooks/bigco/probe"' in body
    assert 'id="crawl-probe-form"' in body
    assert "downloads no" in body                      # it looks and reports


def test_the_last_step_does_not_promise_a_crawl_that_was_never_set(client, conn):
    """A recipe is optional and most suppliers will finish without one. Telling
    an operator their library will be crawled when no library was set is the
    kind of false receipt that makes a screen untrustworthy."""
    _supplier(conn, "BIGCO", items=3)
    client.post("/onboarding/BIGCO/start", follow_redirects=False, data={"slug": "bigco"})
    body = client.get("/onboarding/bigco/done").text
    assert "No library page was set" in body
    assert "Nothing is published without a" in body


def test_the_last_step_names_the_library_when_there_is_one(client, conn):
    _supplier(conn, "BIGCO", items=3)
    client.post("/onboarding/BIGCO/start", follow_redirects=False, data={"slug": "bigco"})
    conn.execute(
        "UPDATE manufacturer SET body = body || %s::jsonb WHERE slug='bigco'",
        ('{"crawl":[{"index_url":"https://bigco.example/docs",'
         '"link_pattern":"\\\\.pdf$"}]}',))
    conn.commit()
    body = client.get("/onboarding/bigco/done").text
    assert "https://bigco.example/docs" in body
    assert "fetched politely" in body
    assert "No library page was set" not in body


def test_every_step_carries_the_queue(client, conn):
    """The queue is the product: a step that dropped it would turn a shift back
    into a form."""
    _supplier(conn, "BIGCO", items=9)
    _supplier(conn, "NEXTCO", items=4)
    client.post("/onboarding/BIGCO/start", follow_redirects=False, data={"slug": "bigco"})
    for path in ("/onboarding/bigco/domains", "/onboarding/bigco/library",
                 "/onboarding/bigco/done"):
        body = client.get(path).text
        assert "NEXTCO" in body, path
        # § 9: a supplier's search recipe is a PLAYBOOK on every screen.
        assert "with no playbook" in body, path
