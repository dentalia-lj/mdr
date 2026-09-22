"""The per-manufacturer discover button.

The registry already had a per-ITEM trigger (`POST /items/{ref}/rediscover`) and
no way to say "go look for everything this manufacturer is missing". This is
that, and it is capped: measured against the live registry 2026-09-02, 365 of
367 manufacturers have uncovered groups, the median has 3 and 84% have 25 or
fewer -- but HENRY SCHEIN has 809, and uncapped that is one click queueing up to
2.400 fetches at `topk` 3, most against one domain at a 2s politeness interval.

Producer only (invariant 1): the route enqueues `discover.group` and writes
nothing to `document` / `item_document` / `evidence`.

Real Postgres per CLAUDE.md.
"""

from __future__ import annotations

import pytest

from web import registry as reg


# --- seeds ------------------------------------------------------------------

def _alias(conn, mfr):
    """The manufacturer page renders off `manufacturer_alias`; without a row
    there `manufacturer_detail` returns None and the GET is a 404."""
    conn.execute(
        "INSERT INTO manufacturer_alias (raw_name, canonical_name) VALUES (%s,%s) "
        "ON CONFLICT DO NOTHING", (mfr, mfr),
    )


def _group(conn, mfr, *, covered=False, discovered_at=None):
    _alias(conn, mfr)
    gid = conn.execute(
        "INSERT INTO item_group (canonical_manufacturer) VALUES (%s) RETURNING group_id",
        (mfr,),
    ).fetchone()["group_id"]
    item_ref = f"IT-{gid}"
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, catalogue, "
        "mirror_rev, updated_at) VALUES (%s,%s,%s,'LJ',1,now())",
        (item_ref, f"item {gid}", mfr),
    )
    conn.execute(
        "INSERT INTO item_group_member (group_id, item_ref, match_basis) VALUES (%s,%s,'udi')",
        (gid, item_ref),
    )
    if covered:
        doc_id = conn.execute(
            "INSERT INTO document (type, regulation, coverage_scope, content_hash, "
            "archive_url, status) VALUES ('DoC','MDR','group',%s,%s,'production') "
            "RETURNING doc_id", (f"H{gid}", f"/archive/x/{gid}.pdf"),
        ).fetchone()["doc_id"]
        conn.execute(
            "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
            "VALUES (%s,%s,'ref-list','production')", (item_ref, doc_id),
        )
    if discovered_at is not None:
        conn.execute(
            "INSERT INTO discovery_log (group_id, source, outcome, at) "
            "VALUES (%s,'search','miss', now() - make_interval(days => %s))",
            (gid, discovered_at),
        )
    return gid


# --- the reader -------------------------------------------------------------

def test_only_groups_with_no_production_document_are_uncovered(conn):
    a = _group(conn, "ACME")
    _group(conn, "ACME", covered=True)
    _group(conn, "OTHER")            # a different manufacturer

    out = reg.uncovered_groups(conn, "ACME", limit=10)

    assert out["total"] == 1
    assert out["group_ids"] == [a]


def test_a_staged_link_does_not_count_as_covered(conn):
    """`staged` is the review queue, not coverage. A group whose only link is
    capped at staged still has no document a compliance officer can rely on,
    and is exactly what this button exists to chase."""
    gid = _group(conn, "ACME")
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, "
        "archive_url, status) VALUES ('DoC','MDR','group','HS','/a/s.pdf','staged') "
        "RETURNING doc_id"
    ).fetchone()["doc_id"]
    conn.execute(
        "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
        "VALUES (%s,%s,'fetch-context','staged')", (f"IT-{gid}", doc_id),
    )

    assert reg.uncovered_groups(conn, "ACME", limit=10)["total"] == 1


def test_never_discovered_groups_come_first_then_least_recent(conn):
    """Why the ordering matters: the button forces a re-look
    (`ignore_recency`), so without it every press would re-do the same 25 and
    a large manufacturer could never be walked through."""
    recent = _group(conn, "ACME", discovered_at=1)
    old = _group(conn, "ACME", discovered_at=90)
    never = _group(conn, "ACME")

    out = reg.uncovered_groups(conn, "ACME", limit=10)

    assert out["group_ids"] == [never, old, recent]


def test_the_limit_caps_the_ids_but_not_the_total(conn):
    """The button has to say "809 groups, this queues 25" -- so the count is
    the real one and only the work is capped."""
    for _ in range(5):
        _group(conn, "ACME")

    out = reg.uncovered_groups(conn, "ACME", limit=2)

    assert out["total"] == 5
    assert len(out["group_ids"]) == 2


def test_a_manufacturer_with_no_gaps_returns_nothing(conn):
    _group(conn, "ACME", covered=True)
    out = reg.uncovered_groups(conn, "ACME", limit=10)
    assert out["total"] == 0 and out["group_ids"] == []


# --- the route --------------------------------------------------------------

import pytest as _pytest
from fastapi.testclient import TestClient

from app.config import Web
from tests.conftest import TEST_API_URL as API_TEST_URL
from web.app import create_app


@_pytest.fixture
def client(test_db_url, tmp_path):
    return TestClient(create_app(Web(api_database_url=API_TEST_URL,
                                     imports_dir=str(tmp_path))))


def _queued(conn):
    return conn.execute(
        "SELECT payload, priority, dedupe_key FROM job WHERE type='discover.group' "
        "ORDER BY id"
    ).fetchall()


def test_a_press_queues_one_discover_job_per_uncovered_group(conn, client):
    gids = [_group(conn, "ACME") for _ in range(3)]
    conn.commit()

    resp = client.post("/manufacturers/ACME/discover", follow_redirects=False)

    assert resp.status_code == 303
    jobs = _queued(conn)
    assert sorted(j["payload"]["group_id"] for j in jobs) == sorted(gids)
    # Denis's ruling 2026-09-02: force a re-look, mirroring the item button.
    assert all(j["payload"]["ignore_recency"] is True for j in jobs)


def test_the_press_is_capped_and_says_how_many_it_queued(conn, client):
    for _ in range(4):
        _group(conn, "ACME")
    conn.commit()

    resp = client.post("/manufacturers/ACME/discover?cap=2", follow_redirects=False)

    assert len(_queued(conn)) == 2
    assert "discover=queued%3A2" in resp.headers["location"] \
        or "discover=queued:2" in resp.headers["location"]


def test_a_second_press_the_same_day_does_not_double_queue(conn, client):
    _group(conn, "ACME")
    conn.commit()

    client.post("/manufacturers/ACME/discover", follow_redirects=False)
    resp = client.post("/manufacturers/ACME/discover", follow_redirects=False)

    assert len(_queued(conn)) == 1
    assert "deduped" in resp.headers["location"]


def test_a_manufacturer_with_no_gaps_queues_nothing(conn, client):
    _group(conn, "ACME", covered=True)
    conn.commit()

    resp = client.post("/manufacturers/ACME/discover", follow_redirects=False)

    assert _queued(conn) == []
    assert "nothing-to-do" in resp.headers["location"]


def test_an_unknown_manufacturer_is_a_404(conn, client):
    assert client.post("/manufacturers/NOPE/discover",
                       follow_redirects=False).status_code == 404


def test_the_route_writes_no_registry_rows(conn, client):
    """Invariant 1: `web` is a producer. It may enqueue and nothing else."""
    _group(conn, "ACME")
    conn.commit()
    before = conn.execute("SELECT count(*) n FROM document").fetchone()["n"]

    client.post("/manufacturers/ACME/discover", follow_redirects=False)

    assert conn.execute("SELECT count(*) n FROM document").fetchone()["n"] == before
    assert conn.execute("SELECT count(*) n FROM item_document").fetchone()["n"] == 0


# --- what the page says -----------------------------------------------------

def test_the_page_counts_groups_not_articles(conn, client):
    """Two articles in one group is ONE thing to discover. The completeness
    card above this button counts articles; printing that figure here would
    name a number the press cannot act on."""
    gid = _group(conn, "ACME")
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, catalogue, "
        "mirror_rev, updated_at) VALUES ('IT-extra','second','ACME','LJ',1,now())"
    )
    conn.execute(
        "INSERT INTO item_group_member (group_id, item_ref, match_basis) "
        "VALUES (%s,'IT-extra','udi')", (gid,),
    )
    conn.commit()

    body = client.get("/manufacturers/ACME").text
    assert "<strong>1</strong>" in body
    assert "One press searches all of them." in body


def test_a_manufacturer_over_the_cap_is_told_to_press_again(conn, client):
    for _ in range(3):
        _group(conn, "ACME")
    conn.commit()

    body = client.get("/manufacturers/ACME?discover=queued%3A2").text
    assert "Queued discovery for 2 group(s)." in body


def test_a_fully_covered_manufacturer_offers_no_button(conn, client):
    _group(conn, "ACME", covered=True)
    conn.commit()

    body = client.get("/manufacturers/ACME").text
    assert "Search for documents" not in body
    # `production` reads as "Published" since P7b (§ 9); the sentence is the
    # same sentence, and it is still the empty-state the button's absence needs.
    assert "already has a published document" in body


def test_the_cap_query_param_can_only_lower_the_limit(conn, client):
    """A limit a caller can raise is not a limit. `?cap=` exists so a press can
    be made smaller; the configured cap is the ceiling."""
    for _ in range(30):
        _group(conn, "ACME")
    conn.commit()

    client.post("/manufacturers/ACME/discover?cap=1000", follow_redirects=False)

    # 25 is the configured default, not 30 and not 1000.
    assert len(_queued(conn)) == 25
