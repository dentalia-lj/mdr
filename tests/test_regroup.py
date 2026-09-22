"""Regroup (S1.8): dropping item groups so RESOLVE rebuilds them.

This is the mechanism that makes the first `manufacturer_alias` seed
reversible, so the tests that matter are the ones about what it REFUSES.
`item_group` has two FK children and three FK-free soft references; Postgres
will stop the first pair and silently allow the second, so every soft ref gets
a test proving it is caught in code instead.
"""

from __future__ import annotations

import pytest

from app import regroup


@pytest.fixture(autouse=True)
def _clean_registry(test_db_url):
    """The CLI tests must commit -- `cli.main` opens its own connection and
    cannot see an uncommitted write -- and a test that never opens a
    `connect_test` connection never fires conftest's registry truncate, so the
    rows would outlive it. This fixture also wipes on SETUP, which conftest
    deliberately does not. Autouse and function-scoped, so it tears down after
    `connect_test` has closed the test's own connections rather than blocking on
    their locks.
    """
    from app import db

    def wipe():
        with db.connect(test_db_url, autocommit=True) as c:
            c.execute(
                "TRUNCATE item_mirror, item_group, item_group_member, "
                "discovery_log, document, item_document, manual_task, "
                "grouping_suggestion CASCADE"
            )

    wipe()
    yield
    wipe()


# --- seed helpers (same shapes as tests/test_resolve_handler.py) ------------
def _seed_item(conn, item_ref, *, manufacturer_raw="ACME", rev=1):
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, "
        "catalogue, mirror_rev, updated_at) "
        "VALUES (%s,%s,%s,true,'LJ',%s,now()) ON CONFLICT (item_ref) DO NOTHING",
        (item_ref, f"item {item_ref}", manufacturer_raw, rev),
    )


def _seed_group(conn, *, canonical="ACME", label=None):
    return conn.execute(
        "INSERT INTO item_group (canonical_manufacturer, label) VALUES (%s,%s) "
        "RETURNING group_id",
        (canonical, label),
    ).fetchone()["group_id"]


def _seed_member(conn, group_id, item_ref, *, canonical="ACME", rev=1):
    _seed_item(conn, item_ref, rev=rev)
    conn.execute(
        "INSERT INTO item_group_member (group_id, item_ref, match_basis) "
        "VALUES (%s,%s,'name-family')",
        (group_id, item_ref),
    )


def _group_with_members(conn, *item_refs, canonical="ACME"):
    gid = _seed_group(conn, canonical=canonical)
    for ref in item_refs:
        _seed_member(conn, gid, ref, canonical=canonical)
    return gid


def _seed_document_link(conn, item_ref, *, content_hash="h1"):
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, "
        "archive_url, status) "
        "VALUES ('DoC','MDR','group',%s,'file:///d.pdf','production'::doc_status) "
        "RETURNING doc_id",
        (content_hash,),
    ).fetchone()["doc_id"]
    conn.execute(
        "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
        "VALUES (%s,%s,'ref-list','production'::link_status)",
        (item_ref, doc_id),
    )


def _jobs(conn, type_="resolve.group"):
    return conn.execute(
        "SELECT payload, dedupe_key, priority FROM job WHERE type=%s ORDER BY id",
        (type_,),
    ).fetchall()


def _kinds(sc):
    return {b.kind for b in sc.blockers}


# --------------------------------------------------------------------------- #
# selection
# --------------------------------------------------------------------------- #
def test_selection_by_manufacturer(conn):
    a = _group_with_members(conn, "i1", "i2", canonical="ACME")
    _group_with_members(conn, "i3", canonical="OTHER")

    sc = regroup.scope(conn, manufacturers=["ACME"])

    assert [g["group_id"] for g in sc.groups] == [a]
    assert sc.items == ("i1", "i2")


def test_selection_by_group_id(conn):
    _group_with_members(conn, "i1", canonical="ACME")
    b = _group_with_members(conn, "i2", canonical="OTHER")

    sc = regroup.scope(conn, group_ids=[b])

    assert [g["group_id"] for g in sc.groups] == [b]


def test_selection_by_all(conn):
    _group_with_members(conn, "i1", canonical="ACME")
    _group_with_members(conn, "i2", canonical="OTHER")

    assert len(regroup.scope(conn, all_groups=True).groups) == 2


def test_no_selection_selects_nothing(conn):
    """An empty selection must be empty, not everything."""
    _group_with_members(conn, "i1")

    assert regroup.scope(conn).groups == ()


def test_a_selection_matching_nothing_is_clear_and_empty(conn):
    sc = regroup.scope(conn, manufacturers=["NOBODY"])

    assert sc.groups == () and sc.clear
    assert regroup.apply(conn, sc) == {
        "groups": 0, "items": 0, "enqueued": 0, "already_queued": 0,
        "discovery_rows": 0, "suggestions": 0,
    }


# --------------------------------------------------------------------------- #
# refusals
# --------------------------------------------------------------------------- #
def test_a_group_whose_member_carries_a_document_is_blocked(conn):
    gid = _group_with_members(conn, "i1", "i2")
    _seed_document_link(conn, "i1")

    sc = regroup.scope(conn, group_ids=[gid])

    assert _kinds(sc) == {"has-documents"}
    assert not sc.clear
    with pytest.raises(ValueError):
        regroup.apply(conn, sc)


def test_a_blocked_apply_deletes_nothing(conn):
    gid = _group_with_members(conn, "i1")
    _seed_document_link(conn, "i1")

    sc = regroup.scope(conn, group_ids=[gid])
    with pytest.raises(ValueError):
        regroup.apply(conn, sc)

    assert conn.execute(
        "SELECT count(*) AS n FROM item_group WHERE group_id=%s", (gid,)
    ).fetchone()["n"] == 1
    assert _jobs(conn) == []


def test_one_blocked_group_refuses_the_whole_selection(conn):
    """Not "do the rest": a half-regrouped catalogue is worse than either end
    state, and the selection is the operator's stated intent."""
    clean = _group_with_members(conn, "i1", canonical="ACME")
    dirty = _group_with_members(conn, "i2", canonical="ACME")
    _seed_document_link(conn, "i2")

    sc = regroup.scope(conn, manufacturers=["ACME"])
    with pytest.raises(ValueError, match="refusing"):
        regroup.apply(conn, sc)

    assert conn.execute(
        "SELECT count(*) AS n FROM item_group WHERE group_id = ANY(%s)",
        ([clean, dirty],),
    ).fetchone()["n"] == 2


def test_an_open_manual_task_blocks(conn):
    """No FK on manual_task.group_id, so nothing but this check stops the
    delete from stranding the task on a dead id."""
    gid = _group_with_members(conn, "i1")
    conn.execute(
        "INSERT INTO manual_task (kind, group_id, payload) "
        "VALUES ('discovery-dead-end'::manual_kind, %s, '{}'::jsonb)",
        (gid,),
    )

    assert _kinds(regroup.scope(conn, group_ids=[gid])) == {"open-manual-task"}


def test_a_resolved_manual_task_does_not_block(conn):
    gid = _group_with_members(conn, "i1")
    conn.execute(
        "INSERT INTO manual_task (kind, group_id, payload, status, resolved_at) "
        "VALUES ('discovery-dead-end'::manual_kind, %s, '{}'::jsonb, "
        "'resolved'::manual_status, now())",
        (gid,),
    )

    assert regroup.scope(conn, group_ids=[gid]).clear


def _seed_suggestion(conn, item_ref, gid, *, status="open"):
    _seed_item(conn, item_ref)
    resolved = "now()" if status == "resolved" else "NULL"
    return conn.execute(
        "INSERT INTO grouping_suggestion (item_ref, candidates, status, resolved_at) "
        f"VALUES (%s, %s::jsonb, %s, {resolved}) RETURNING id",
        (item_ref, f'[{{"group_id": {gid}, "score": 0.8}}]', status),
    ).fetchone()["id"]


def test_an_open_grouping_suggestion_is_rebuilt_not_blocked(conn):
    """Derived state: `resolve.group` made it and will make it again. Blocking
    here made the tool useless on real data -- 26 of 141 groups unregroupable
    on a 400-row slice -- and the items holding these suggestions are exactly
    the ones whose grouping is in question."""
    gid = _group_with_members(conn, "i1")
    sid = _seed_suggestion(conn, "i9", gid)

    sc = regroup.scope(conn, group_ids=[gid])

    assert sc.clear
    assert sc.suggestions == (sid,)


def test_the_stranded_suggestion_item_is_re_resolved_too(conn):
    """i9 is in no group, so members alone would leave it with no group AND an
    open suggestion full of dead group ids."""
    gid = _group_with_members(conn, "i1")
    _seed_suggestion(conn, "i9", gid)

    sc = regroup.scope(conn, group_ids=[gid])
    assert sc.items == ("i1", "i9")

    stats = regroup.apply(conn, sc)

    assert stats["suggestions"] == 1
    assert [j["payload"]["item_ref"] for j in _jobs(conn)] == ["i1", "i9"]
    assert conn.execute(
        "SELECT count(*) AS n FROM grouping_suggestion"
    ).fetchone()["n"] == 0


def test_a_resolved_grouping_suggestion_is_left_alone(conn):
    """History, not a pending question."""
    gid = _group_with_members(conn, "i1")
    _seed_suggestion(conn, "i9", gid, status="resolved")

    sc = regroup.scope(conn, group_ids=[gid])
    assert sc.clear and sc.suggestions == ()

    regroup.apply(conn, sc)

    assert conn.execute(
        "SELECT count(*) AS n FROM grouping_suggestion"
    ).fetchone()["n"] == 1


def test_a_suggestion_offering_an_unselected_group_is_untouched(conn):
    """Its item is not being re-resolved and its candidates stay valid."""
    selected = _group_with_members(conn, "i1", canonical="ACME")
    other = _group_with_members(conn, "i2", canonical="OTHER")
    _seed_suggestion(conn, "i9", other)

    sc = regroup.scope(conn, group_ids=[selected])

    assert sc.suggestions == () and sc.items == ("i1",)


def test_an_undrained_upload_targeting_the_group_blocks(conn):
    gid = _group_with_members(conn, "i1")
    conn.execute(
        "INSERT INTO upload_inbox (filename, content, target_group_id, catalogue) "
        "VALUES ('d.pdf', '\\x00'::bytea, %s, 'LJ')",
        (gid,),
    )

    assert _kinds(regroup.scope(conn, group_ids=[gid])) == {"undrained-upload"}


# --------------------------------------------------------------------------- #
# apply
# --------------------------------------------------------------------------- #
def test_apply_deletes_the_group_and_its_fk_children(conn):
    gid = _group_with_members(conn, "i1", "i2")
    conn.execute(
        "INSERT INTO discovery_log (group_id, source, outcome) "
        "VALUES (%s,'search','miss')",
        (gid,),
    )

    stats = regroup.apply(conn, regroup.scope(conn, group_ids=[gid]))

    assert stats["groups"] == 1 and stats["items"] == 2
    assert stats["discovery_rows"] == 1
    for table in ("item_group", "item_group_member", "discovery_log"):
        assert conn.execute(
            f"SELECT count(*) AS n FROM {table} WHERE group_id=%s", (gid,)
        ).fetchone()["n"] == 0


def test_apply_keeps_the_mirror_rows(conn):
    """The catalogue is not the grouping. Items must survive to be re-resolved."""
    gid = _group_with_members(conn, "i1", "i2")

    regroup.apply(conn, regroup.scope(conn, group_ids=[gid]))

    assert conn.execute(
        "SELECT count(*) AS n FROM item_mirror"
    ).fetchone()["n"] == 2


def test_apply_re_enqueues_one_resolve_job_per_item(conn):
    gid = _group_with_members(conn, "i1", "i2")

    stats = regroup.apply(conn, regroup.scope(conn, group_ids=[gid]))

    jobs = _jobs(conn)
    assert stats["enqueued"] == 2
    assert [j["payload"]["item_ref"] for j in jobs] == ["i1", "i2"]
    assert [j["priority"] for j in jobs] == ["delta", "delta"]


def test_the_dedupe_key_matches_the_one_ingest_uses(conn):
    """Same `resolve:{item_ref}:{mirror_rev}` shape, so a still-active resolve
    job for this revision dedupes rather than stacking a second one."""
    gid = _seed_group(conn)
    _seed_member(conn, gid, "i1", rev=7)

    regroup.apply(conn, regroup.scope(conn, group_ids=[gid]))

    assert _jobs(conn)[0]["dedupe_key"] == "resolve:i1:7"


def test_an_already_active_resolve_job_is_counted_not_duplicated(conn):
    from app import queue

    gid = _seed_group(conn)
    _seed_member(conn, gid, "i1", rev=3)
    queue.enqueue(conn, "resolve.group", {"item_ref": "i1"}, dedupe_key="resolve:i1:3")

    stats = regroup.apply(conn, regroup.scope(conn, group_ids=[gid]))

    assert stats["enqueued"] == 0 and stats["already_queued"] == 1
    assert len(_jobs(conn)) == 1


def test_apply_at_a_chosen_priority(conn):
    gid = _group_with_members(conn, "i1")

    regroup.apply(conn, regroup.scope(conn, group_ids=[gid]), priority="sweep")

    assert _jobs(conn)[0]["priority"] == "sweep"


def test_render_names_the_blockers(conn):
    gid = _group_with_members(conn, "i1", canonical="ACME")
    _seed_document_link(conn, "i1")

    text = "\n".join(regroup.render(regroup.scope(conn, group_ids=[gid])))

    assert "BLOCKED" in text and "has-documents" in text and "ACME" in text


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_cmd_requires_a_selection(test_db_url, monkeypatch, capsys):
    from app import cli

    monkeypatch.setenv("DATABASE_URL", test_db_url)

    rc = cli.main(["regroup"])

    captured = capsys.readouterr()
    assert rc == 2
    assert "select something" in captured.err
    assert "Traceback" not in captured.err + captured.out


def test_cmd_dry_run_deletes_nothing(conn, connect_test, test_db_url,
                                     monkeypatch, capsys):
    from app import cli

    monkeypatch.setenv("DATABASE_URL", test_db_url)
    gid = _group_with_members(conn, "i1", canonical="ACME")
    conn.commit()

    rc = cli.main(["regroup", "--manufacturer", "ACME"])

    out = capsys.readouterr().out
    assert rc == 0 and "dry run" in out
    fresh = connect_test(autocommit=True)
    assert fresh.execute(
        "SELECT count(*) AS n FROM item_group WHERE group_id=%s", (gid,)
    ).fetchone()["n"] == 1


def test_cmd_apply_deletes_and_enqueues(conn, connect_test, test_db_url,
                                        monkeypatch, capsys):
    from app import cli

    monkeypatch.setenv("DATABASE_URL", test_db_url)
    gid = _group_with_members(conn, "i1", canonical="ACME")
    conn.commit()

    rc = cli.main(["regroup", "--manufacturer", "ACME", "--apply"])

    assert rc == 0 and "1 group(s)" in capsys.readouterr().out
    fresh = connect_test(autocommit=True)
    assert fresh.execute(
        "SELECT count(*) AS n FROM item_group WHERE group_id=%s", (gid,)
    ).fetchone()["n"] == 0
    assert fresh.execute(
        "SELECT count(*) AS n FROM job WHERE type='resolve.group'"
    ).fetchone()["n"] == 1


def test_cmd_exits_1_and_writes_nothing_when_blocked(conn, connect_test,
                                                     test_db_url, monkeypatch,
                                                     capsys):
    from app import cli

    monkeypatch.setenv("DATABASE_URL", test_db_url)
    gid = _group_with_members(conn, "i1", canonical="ACME")
    _seed_document_link(conn, "i1")
    conn.commit()

    rc = cli.main(["regroup", "--manufacturer", "ACME", "--apply"])

    out = capsys.readouterr().out
    assert rc == 1
    assert "blocker" in out and "Nothing was deleted" in out
    fresh = connect_test(autocommit=True)
    assert fresh.execute(
        "SELECT count(*) AS n FROM item_group WHERE group_id=%s", (gid,)
    ).fetchone()["n"] == 1
