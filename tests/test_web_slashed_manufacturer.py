"""A manufacturer whose name contains a slash can still be acted on (F43).

Three canonical names in the live registry carry a slash, across 102 item
groups. Four POST routes matched `{canonical_name}` with the plain converter,
which cannot match one, while their four siblings -- `contacts`, `playbook`,
`rename` and the detail GET -- have always used `{canonical_name:path}`. For
those three manufacturers the button rendered and the POST returned 404.

Nothing was broken at the time it was found: zero of the 44 EUDAMED certificate
gaps belonged to a slashed name. It would have broken silently the first time
one did, which is why these tests assert REACHABILITY rather than success -- a
400 "nothing to request" proves the route was reached, and a 404 proves it was
not.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Web
from tests.conftest import TEST_API_URL
from web.app import create_app

SLASHED = "BIEN-AIR / NSK"
PLAIN = "SLASHTEST PLAIN"


@pytest.fixture
def client(test_db_url, tmp_path):
    return TestClient(create_app(Web(api_database_url=TEST_API_URL,
                                     imports_dir=str(tmp_path))))


@pytest.fixture(autouse=True)
def _seed(conn):
    """Both manufacturers get an `item_group`, because `discover` answers 404 for
    a manufacturer that has none -- a business 404, not a routing one, and a
    test that could not tell them apart would prove nothing."""
    for i, name in enumerate((SLASHED, PLAIN)):
        conn.execute("INSERT INTO manufacturer (canonical_name) VALUES (%s) "
                     "ON CONFLICT DO NOTHING", (name,))
        conn.execute("INSERT INTO manufacturer_alias (raw_name, canonical_name, source) "
                     "VALUES (%s,%s,'self-seed') ON CONFLICT DO NOTHING",
                     (f"99{i}", name))
        conn.execute("INSERT INTO item_group (canonical_manufacturer, label) "
                     "VALUES (%s,'Slash probe')", (name,))
    conn.commit()
    yield
    for name in (SLASHED, PLAIN):
        conn.execute("DELETE FROM item_group WHERE canonical_manufacturer=%s", (name,))
        conn.execute("DELETE FROM manufacturer_alias WHERE canonical_name=%s", (name,))
        conn.execute("DELETE FROM manufacturer WHERE canonical_name=%s", (name,))
    conn.commit()


@pytest.mark.parametrize("action", ["sweep", "discover", "gap-request", "cert-request"])
def test_the_four_buttons_reach_a_slashed_name(client, action):
    """404 is the failure this guards. Any other status means the route matched
    and the handler ruled on it -- 400 for "nothing to request", 303 for a
    redirect -- which is all this test claims."""
    r = client.post(f"/manufacturers/{SLASHED}/{action}", follow_redirects=False)
    assert r.status_code != 404, r.text[:200]


def test_the_detail_page_reaches_it_too(client):
    """The GET has always used `:path`; asserted here so the pair cannot drift
    apart again in the other direction."""
    assert client.get(f"/manufacturers/{SLASHED}").status_code != 404


@pytest.mark.parametrize("action", ["sweep", "discover", "gap-request", "cert-request"])
def test_an_ordinary_name_is_unchanged(client, action):
    """The converter change must not alter routing for the 381 names without a
    slash."""
    r = client.post(f"/manufacturers/{PLAIN}/{action}", follow_redirects=False)
    assert r.status_code != 404, r.text[:200]
