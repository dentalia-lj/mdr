"""resolve.group — S1.2 RESOLVE (spec: docs/specs/resolve.md).

Manufacturer canonicalization (manufacturer_alias) + the resolution ladder
(existing-link -> udi -> basic-udi-di -> name-family via pg_trgm+rapidfuzz),
C1 mfr_ref materialization, the T1 staging path (grouping_suggestion), the
singleton fallback, and the forced-assign resolution loop.

Real Postgres (CLAUDE.md: no mocking). The `conn` fixture rolls back on
teardown and reads queued jobs within the same tx (queue.enqueue is visible to
the writing connection). The T1 adjudicator is injected so no live LLM call
happens — a stub for the assign path, a raiser to prove work isn't lost.
"""

from __future__ import annotations

import pytest
from psycopg.types.json import Json

from app import queue
# Import runner FIRST: it imports the no-op placeholders then the real handlers
# in the order that makes real win (last registration wins). Importing a real
# handler module standalone before runner would let runner's later no-op import
# clobber it (the real module is already cached, so runner's re-import is a
# no-op and never re-registers). Production always enters through runner.
from app.workers import runner
from app.handlers import HANDLERS
from app.handlers import resolve as rh


# --- seed helpers -----------------------------------------------------------

def _seed_item(conn, item_ref, *, name="Widget", manufacturer_raw="ACME",
               mfr_ref=None, md_flag=True, udi=None, product_class="IIa",
               catalogue="LJ"):
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, mfr_ref, "
        "md_flag, product_class, udi, catalogue, mirror_rev, updated_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1,now()) ON CONFLICT (item_ref) DO NOTHING",
        (item_ref, name, manufacturer_raw, mfr_ref, md_flag, product_class, udi, catalogue),
    )


def _seed_group(conn, *, canonical="ACME", basic_udi_di=None, label=None):
    return conn.execute(
        "INSERT INTO item_group (canonical_manufacturer, basic_udi_di, label) "
        "VALUES (%s,%s,%s) RETURNING group_id",
        (canonical, basic_udi_di, label),
    ).fetchone()["group_id"]


def _seed_member(conn, group_id, item_ref, *, mfr_ref=None, basis="manual", **item_kw):
    _seed_item(conn, item_ref, mfr_ref=mfr_ref, **item_kw)
    conn.execute(
        "INSERT INTO item_group_member (group_id, item_ref, mfr_ref, match_basis) "
        "VALUES (%s,%s,%s,%s)",
        (group_id, item_ref, mfr_ref, basis),
    )


def _seed_alias(conn, raw, canonical):
    conn.execute(
        "INSERT INTO manufacturer_alias (raw_name, canonical_name) VALUES (%s,%s)",
        (raw, canonical),
    )


def _seed_prod_doc(conn, group_id, content_hash="h_doc", *,
                   doc_status="production", link_status="production",
                   basis="ref-list"):
    """A document reachable by a link from a member of group_id. Defaults make
    _has_current_doc_or_candidate(group_id) true via the production pair."""
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, "
        "archive_url, status) VALUES ('DoC','MDR','group',%s,'file:///d.pdf',%s::doc_status) "
        "RETURNING doc_id",
        (content_hash, doc_status),
    ).fetchone()["doc_id"]
    member = conn.execute(
        "SELECT item_ref FROM item_group_member WHERE group_id=%s LIMIT 1", (group_id,)
    ).fetchone()["item_ref"]
    conn.execute(
        "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
        "VALUES (%s,%s,%s,%s::link_status)",
        (member, doc_id, basis, link_status),
    )
    return doc_id


# --- query + call helpers ---------------------------------------------------

def _job(item_ref, group_id=None, force_new_group=None):
    p = {"item_ref": item_ref}
    if group_id is not None:
        p["group_id"] = group_id
    if force_new_group:
        p["force_new_group"] = True
    return {"id": 1, "type": "resolve.group", "payload": p}


def _discover_jobs(conn):
    return conn.execute(
        "SELECT payload, dedupe_key FROM job WHERE type='discover.group' ORDER BY id"
    ).fetchall()


def _members(conn, item_ref):
    return conn.execute(
        "SELECT group_id, mfr_ref, match_basis FROM item_group_member "
        "WHERE item_ref=%s ORDER BY group_id",
        (item_ref,),
    ).fetchall()


def _suggestions(conn, item_ref):
    return conn.execute(
        "SELECT candidates, suggestion, score, status FROM grouping_suggestion "
        "WHERE item_ref=%s",
        (item_ref,),
    ).fetchall()


def _assign_first(item, candidates):
    return {"group_id": candidates[0]["group_id"], "action": "assign", "rationale": "stub"}


def _boom(item, candidates):
    raise RuntimeError("t1 down")


# --- ladder rungs -----------------------------------------------------------

def test_existing_link_with_doc_emits_nothing(conn):
    g = _seed_group(conn)
    _seed_member(conn, g, "IT-1", mfr_ref="R1", basis="udi")
    _seed_prod_doc(conn, g)

    res = rh.handle_resolve_group(conn, _job("IT-1"))

    assert res["resolved"] is True
    assert res["basis"] == "existing-link"
    assert res["group_id"] == g
    assert res["emitted_discover"] is False
    assert _discover_jobs(conn) == []
    rows = _members(conn, "IT-1")
    assert len(rows) == 1 and rows[0]["match_basis"] == "udi"   # original basis preserved


@pytest.mark.parametrize("doc_status,link_status,basis,expect_discover", [
    # PRD §4 (C3): a staged candidate awaiting review suppresses re-discovery,
    # same as a production doc — otherwise a group served only via the C3
    # fetch-context path (staged forever, C5) re-triggers search every cycle.
    ("staged", "staged", "fetch-context", False),
    ("production", "production", "ref-list", False),
    # Rejected rows never suppress: the candidate is dead, discovery must resume.
    ("rejected", "staged", "fetch-context", True),
    ("staged", "rejected", "fetch-context", True),
    # Superseded never suppresses either (its replacement carries its own links;
    # in isolation the group has no current doc). C6 writes nothing yet, but the
    # state is representable, so the guard must handle it.
    ("superseded", "production", "ref-list", True),
])
def test_staged_candidate_suppresses_discover(conn, doc_status, link_status,
                                              basis, expect_discover):
    g = _seed_group(conn)
    _seed_member(conn, g, "IT-1", mfr_ref="R1", basis="udi")
    _seed_prod_doc(conn, g, doc_status=doc_status, link_status=link_status,
                   basis=basis)

    res = rh.handle_resolve_group(conn, _job("IT-1"))

    assert res["emitted_discover"] is expect_discover
    assert bool(_discover_jobs(conn)) is expect_discover


def test_existing_link_without_doc_emits_discover(conn):
    g = _seed_group(conn)
    _seed_member(conn, g, "IT-1", mfr_ref="R1", basis="udi")

    res = rh.handle_resolve_group(conn, _job("IT-1"))

    assert res["emitted_discover"] is True
    jobs = _discover_jobs(conn)
    assert len(jobs) == 1
    assert jobs[0]["payload"]["group_id"] == g
    assert jobs[0]["dedupe_key"] == f"discover:{g}:0"


def test_by_udi_joins_group(conn):
    g = _seed_group(conn)
    _seed_member(conn, g, "IT-existing", mfr_ref="RE", basis="manual", udi="UDI-XYZ")
    _seed_item(conn, "IT-new", manufacturer_raw="ACME", mfr_ref="RN", udi="UDI-XYZ")

    res = rh.handle_resolve_group(conn, _job("IT-new"))

    assert res["basis"] == "udi" and res["group_id"] == g
    rows = _members(conn, "IT-new")
    assert rows[0]["group_id"] == g and rows[0]["mfr_ref"] == "RN"
    assert rows[0]["match_basis"] == "udi"


def test_by_basic_udi_di_joins_group(conn):
    g = _seed_group(conn, basic_udi_di="BUDI-123")
    _seed_member(conn, g, "IT-anchor", basis="manual")            # anchor has no udi
    _seed_item(conn, "IT-new", manufacturer_raw="ACME", udi="BUDI-123", mfr_ref="RN")

    res = rh.handle_resolve_group(conn, _job("IT-new"))

    assert res["basis"] == "basic-udi-di" and res["group_id"] == g


def test_name_family_accept_joins_group(conn):
    g = _seed_group(conn, canonical="Ivoclar")
    _seed_member(conn, g, "IT-anchor", name="Tetric PowerFill A2 20x0.2g",
                 manufacturer_raw="Ivoclar", basis="manual")
    _seed_item(conn, "IT-new", name="PowerFill Tetric A2",
               manufacturer_raw="Ivoclar", mfr_ref="RN")

    # accept path is deterministic — must NOT consult T1 (adjudicator would raise)
    res = rh.handle_resolve_group(conn, _job("IT-new"), adjudicate=_boom)

    assert res["basis"] == "name-family" and res["group_id"] == g
    rows = _members(conn, "IT-new")
    assert rows[0]["match_basis"] == "name-family" and rows[0]["mfr_ref"] == "RN"


# --- staging + singleton branches -------------------------------------------

def test_name_family_suggest_stages_no_write(conn, monkeypatch):
    # The staging band is OFF by default (name_suggest == name_accept), so this
    # exercises the mechanism with the band explicitly restored -- it must keep
    # working, since the collapse is a threshold choice and not a deletion.
    monkeypatch.setenv("RESOLVE_NAME_SUGGEST", "0.72")
    g = _seed_group(conn, canonical="Ivoclar")
    _seed_member(conn, g, "IT-anchor", name="IPS Empress Direct Dentin",
                 manufacturer_raw="Ivoclar", basis="manual")
    _seed_item(conn, "IT-new", name="IPS Empress Direct Enamel",
               manufacturer_raw="Ivoclar", mfr_ref="RN")

    res = rh.handle_resolve_group(conn, _job("IT-new"), adjudicate=_assign_first)

    assert res["basis"] is None                 # not accepted
    assert res["grouping_suggestions"] == 1
    assert res["emitted_discover"] is False
    assert _members(conn, "IT-new") == []       # NO membership written (invariant 3)
    assert _discover_jobs(conn) == []
    s = _suggestions(conn, "IT-new")
    assert len(s) == 1 and s[0]["status"] == "open"
    assert s[0]["suggestion"]["action"] == "assign"
    assert s[0]["candidates"][0]["group_id"] == g


def test_no_match_creates_singleton_and_discovers(conn):
    _seed_item(conn, "IT-lonely", name="Completely Unrelated Gizmo",
               manufacturer_raw="ACME", mfr_ref="RL")

    res = rh.handle_resolve_group(conn, _job("IT-lonely"))

    assert res["basis"] is None and res["group_id"] is not None
    rows = _members(conn, "IT-lonely")
    assert len(rows) == 1 and rows[0]["match_basis"] == "singleton" and rows[0]["mfr_ref"] == "RL"
    g = rows[0]["group_id"]
    canon = conn.execute(
        "SELECT canonical_manufacturer FROM item_group WHERE group_id=%s", (g,)
    ).fetchone()["canonical_manufacturer"]
    assert canon == "ACME"
    jobs = _discover_jobs(conn)
    assert len(jobs) == 1 and jobs[0]["dedupe_key"] == f"discover:{g}:0"


# --- canonicalization + reporting -------------------------------------------

def test_alias_miss_inserts_raw_as_canonical_and_counts(conn):
    _seed_item(conn, "IT-x", name="Foo", manufacturer_raw="Weird Mfr GmbH", mfr_ref="R")

    res = rh.handle_resolve_group(conn, _job("IT-x"))

    assert res["aliases_created"] == 1
    row = conn.execute(
        "SELECT canonical_name FROM manufacturer_alias WHERE raw_name=%s", ("Weird Mfr GmbH",)
    ).fetchone()
    assert row["canonical_name"] == "Weird Mfr GmbH"


def test_alias_hit_canonicalizes_group(conn):
    _seed_alias(conn, "Ivoclar Vivadent AG", "Ivoclar")
    _seed_item(conn, "IT-x", name="Solo Product",
               manufacturer_raw="Ivoclar Vivadent AG", mfr_ref="R")

    res = rh.handle_resolve_group(conn, _job("IT-x"))

    canon = conn.execute(
        "SELECT canonical_manufacturer FROM item_group WHERE group_id=%s", (res["group_id"],)
    ).fetchone()["canonical_manufacturer"]
    assert canon == "Ivoclar" and res["aliases_created"] == 0


def test_null_mfr_ref_counted_and_excluded_from_comparand(conn):
    g = _seed_group(conn, canonical="ACME")
    _seed_member(conn, g, "IT-anchor", mfr_ref="RA", name="Alpha Widget",
                 manufacturer_raw="ACME", basis="manual")
    _seed_item(conn, "IT-new", name="Alpha Widget", manufacturer_raw="ACME", mfr_ref=None)

    res = rh.handle_resolve_group(conn, _job("IT-new"), adjudicate=_boom)   # identical name -> accept

    assert res["missing_mfr_ref"] == 1
    assert _members(conn, "IT-new")[0]["mfr_ref"] is None
    refs = conn.execute(
        "SELECT member_mfr_refs FROM item_group_refs WHERE group_id=%s", (g,)
    ).fetchone()["member_mfr_refs"]
    assert "RA" in refs and None not in refs


# --- resolution loop + error taxonomy ---------------------------------------

def test_forced_group_assigns_manual_and_resolves_suggestion(conn):
    g = _seed_group(conn, canonical="ACME")
    _seed_member(conn, g, "IT-anchor", basis="manual")
    _seed_item(conn, "IT-new", name="X", manufacturer_raw="ACME", mfr_ref="RN")
    conn.execute(
        "INSERT INTO grouping_suggestion (item_ref, candidates, score) VALUES (%s,%s,0.8)",
        ("IT-new", Json([{"group_id": g, "score": 0.8, "sample_name": "X"}])),
    )

    res = rh.handle_resolve_group(conn, _job("IT-new", group_id=g))

    assert res["basis"] == "manual" and res["group_id"] == g
    rows = _members(conn, "IT-new")
    assert rows[0]["group_id"] == g and rows[0]["match_basis"] == "manual" and rows[0]["mfr_ref"] == "RN"
    assert _suggestions(conn, "IT-new")[0]["status"] == "resolved"


def test_force_new_group_creates_singleton_manual_and_resolves_suggestion(conn):
    g = _seed_group(conn, canonical="Ivoclar")
    _seed_member(conn, g, "IT-anchor", name="IPS Empress Direct Dentin",
                 manufacturer_raw="Ivoclar", basis="manual")
    _seed_item(conn, "IT-new", name="IPS Empress Direct Enamel",
               manufacturer_raw="Ivoclar", mfr_ref="RN")
    conn.execute(
        "INSERT INTO grouping_suggestion (item_ref, candidates, score) VALUES (%s,%s,0.8)",
        ("IT-new", Json([{"group_id": g, "score": 0.8, "sample_name": "X"}])),
    )

    res = rh.handle_resolve_group(conn, _job("IT-new", force_new_group=True))

    assert res["basis"] == "manual" and res["group_id"] != g   # a NEW group, not the candidate's
    rows = _members(conn, "IT-new")
    assert rows[0]["group_id"] == res["group_id"]
    assert rows[0]["match_basis"] == "manual" and rows[0]["mfr_ref"] == "RN"
    assert _suggestions(conn, "IT-new")[0]["status"] == "resolved"
    canon = conn.execute(
        "SELECT canonical_manufacturer FROM item_group WHERE group_id=%s", (res["group_id"],)
    ).fetchone()["canonical_manufacturer"]
    assert canon == "Ivoclar"


def test_force_new_group_without_open_suggestion_still_resolves(conn):
    # UI could in principle call this without a prior suggestion — must not error.
    _seed_item(conn, "IT-solo", name="Standalone Widget", manufacturer_raw="ACME", mfr_ref="R1")

    res = rh.handle_resolve_group(conn, _job("IT-solo", force_new_group=True))

    assert res["resolved"] is True and res["basis"] == "manual"
    assert _members(conn, "IT-solo")[0]["match_basis"] == "manual"


def test_redelivery_after_force_new_group_hits_existing_link_no_duplicate_suggestion(conn):
    g = _seed_group(conn, canonical="Ivoclar")
    _seed_member(conn, g, "IT-anchor", name="IPS Empress Direct Dentin",
                 manufacturer_raw="Ivoclar", basis="manual")
    _seed_item(conn, "IT-new", name="IPS Empress Direct Enamel",
               manufacturer_raw="Ivoclar", mfr_ref="RN")

    first = rh.handle_resolve_group(conn, _job("IT-new", force_new_group=True))
    second = rh.handle_resolve_group(conn, _job("IT-new"), adjudicate=_boom)   # plain redelivery

    assert second["basis"] == "existing-link" and second["group_id"] == first["group_id"]
    assert _suggestions(conn, "IT-new") == []   # no suggestion ever staged on redelivery


def test_missing_item_raises_lookup(conn):
    with pytest.raises(LookupError):
        rh.handle_resolve_group(conn, _job("IT-nope"))


def test_t1_failure_persists_suggestion_without_losing_work(conn, monkeypatch):
    # Band explicitly restored: off by default, but the never-lose-work
    # behaviour must survive for anyone who turns it back on.
    monkeypatch.setenv("RESOLVE_NAME_SUGGEST", "0.72")
    g = _seed_group(conn, canonical="Ivoclar")
    _seed_member(conn, g, "IT-anchor", name="IPS Empress Direct Dentin",
                 manufacturer_raw="Ivoclar", basis="manual")
    _seed_item(conn, "IT-new", name="IPS Empress Direct Enamel",
               manufacturer_raw="Ivoclar", mfr_ref="RN")

    res = rh.handle_resolve_group(conn, _job("IT-new"), adjudicate=_boom)   # SUGGEST band, T1 raises

    assert res["grouping_suggestions"] == 1
    s = _suggestions(conn, "IT-new")
    assert len(s) == 1 and s[0]["status"] == "open"
    assert s[0]["suggestion"]["t1_error"]        # error recorded, candidates preserved
    assert _members(conn, "IT-new") == []        # still no auto-write


def test_resolve_group_through_the_runner_loop(conn):
    """End-to-end: a real resolve.group job dispatched by run_once (claim ->
    handler -> finish) with the live HANDLERS registry — proves the job-envelope
    wiring, not just the direct call. Same-conn uncommitted visibility keeps it
    inside the fixture's rolled-back transaction."""
    _seed_item(conn, "IT-e2e", name="Runner Smoke Product",
               manufacturer_raw="SmokeCo", mfr_ref="RS")
    jid = queue.enqueue(conn, "resolve.group", {"item_ref": "IT-e2e"}, "resolve:IT-e2e:1")

    assert runner.run_once(conn, "w-e2e", handlers=HANDLERS) is True

    assert conn.execute(
        "SELECT status FROM job WHERE id=%s", (jid,)
    ).fetchone()["status"] == "done"
    rows = _members(conn, "IT-e2e")
    assert len(rows) == 1 and rows[0]["match_basis"] == "singleton"   # singleton fallback
    jobs = _discover_jobs(conn)
    assert any(j["payload"]["group_id"] == rows[0]["group_id"] for j in jobs)


# --- corpus-first cold start (discover.hold) ---------------------------------

def test_hold_defers_discovery_and_says_so(conn, monkeypatch):
    """`discover.hold` builds the group but emits no discover.group.

    The point of the flag: RESOLVE has to run before BACKFILL (it builds the
    groups a backfill document resolves against), so the C3 "already covered"
    suppression cannot fire yet and every group would web-search for documents
    already on disk. Held is DEFERRED, not decided, so it must be distinguishable
    in the report from the C3 case — both leave emitted_discover False, and only
    `discover_held` tells them apart (CLAUDE.md: nothing skipped is silent).
    """
    monkeypatch.setenv("DISCOVER_HOLD", "true")
    _seed_item(conn, "IT-hold", name="Held Product",
               manufacturer_raw="HoldCo", mfr_ref="RH")

    res = rh.handle_resolve_group(conn, _job("IT-hold"))

    assert res["resolved"] is True                 # grouping still happens
    assert res["group_id"] is not None
    assert res["emitted_discover"] is False
    assert res["discover_held"] == 1               # the reason, not just the absence
    assert _discover_jobs(conn) == []


def test_c3_suppression_is_not_reported_as_held(conn):
    """The hold is OFF here: a group that already has a current doc is suppressed
    by C3, and `discover_held` must stay 0. Guards the inverse of the test above —
    without this, setting discover_held unconditionally would still pass."""
    g = _seed_group(conn)
    _seed_member(conn, g, "IT-1", mfr_ref="R1", basis="udi")
    _seed_prod_doc(conn, g)

    res = rh.handle_resolve_group(conn, _job("IT-1"))

    assert res["emitted_discover"] is False
    assert res["discover_held"] == 0


def test_hold_off_by_default_still_discovers(conn):
    """Steady state is unchanged: no DISCOVER_HOLD in the environment means the
    flag defaults False and discovery is emitted exactly as before."""
    _seed_item(conn, "IT-nohold", name="Normal Product",
               manufacturer_raw="NormalCo", mfr_ref="RN")

    res = rh.handle_resolve_group(conn, _job("IT-nohold"))

    assert res["emitted_discover"] is True
    assert res["discover_held"] == 0
    assert len(_discover_jobs(conn)) == 1


def test_the_staging_band_is_closed_by_default(conn):
    # A staged item gets NO item_group_member row, and every REF-gate path in
    # validate.py joins through that table -- so an item in the band was
    # invisible to document linking entirely (measured: 5.831 of 15.958 items,
    # 36,5% of the catalogue). With name_suggest == name_accept the same input
    # falls through to a singleton and stays reachable. Perversely, before this
    # a WORSE name match (below suggest) got a group and a better one did not.
    g = _seed_group(conn, canonical="Ivoclar")
    _seed_member(conn, g, "IT-band-anchor", name="IPS Empress Direct Dentin",
                 manufacturer_raw="Ivoclar", basis="manual")
    _seed_item(conn, "IT-band", name="IPS Empress Direct Enamel",
               manufacturer_raw="Ivoclar", mfr_ref="RB")

    res = rh.handle_resolve_group(conn, _job("IT-band"), adjudicate=_assign_first)

    assert res["grouping_suggestions"] == 0          # nothing staged
    assert _suggestions(conn, "IT-band") == []
    assert res["resolved"] is True and res["basis"] is None   # singleton fallback
    assert _members(conn, "IT-band") != []           # reachable by the REF gate


def _open_suggestion(conn, item_ref, group_id):
    from psycopg.types.json import Json
    conn.execute(
        "INSERT INTO grouping_suggestion (item_ref, candidates, score) VALUES (%s,%s,0.8)",
        (item_ref, Json([{"group_id": group_id, "score": 0.8, "sample_name": "S"}])),
    )


def test_joining_a_family_resolves_a_stale_open_suggestion(conn):
    # An open suggestion asks "which group should this item join?". Once RESOLVE
    # puts the item in a group, that question is answered and the row is stale
    # review noise. It mattered the moment the staging band closed: 5.831 items
    # carried an open suggestion, and re-resolving them grouped every one while
    # leaving every suggestion open.
    g = _seed_group(conn, canonical="Ivoclar")
    _seed_member(conn, g, "IT-stale-anchor", name="IPS Empress Direct Dentin",
                 manufacturer_raw="Ivoclar", basis="manual")
    _seed_item(conn, "IT-stale", name="IPS Empress Direct Dentin",
               manufacturer_raw="Ivoclar", mfr_ref="RS")
    _open_suggestion(conn, "IT-stale", g)

    res = rh.handle_resolve_group(conn, _job("IT-stale"))

    assert res["resolved"] is True and res["group_id"] == g   # accepted into the family
    assert _suggestions(conn, "IT-stale")[0]["status"] == "resolved"


def test_falling_back_to_a_singleton_resolves_a_stale_open_suggestion(conn):
    g = _seed_group(conn, canonical="Ivoclar")
    _seed_item(conn, "IT-stale-solo", name="Completely Unrelated Gizmo",
               manufacturer_raw="ACME", mfr_ref="RS2")
    _open_suggestion(conn, "IT-stale-solo", g)

    res = rh.handle_resolve_group(conn, _job("IT-stale-solo"))

    assert res["resolved"] is True and res["basis"] is None   # singleton
    assert _suggestions(conn, "IT-stale-solo")[0]["status"] == "resolved"
