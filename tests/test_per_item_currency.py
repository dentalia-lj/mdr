"""Per-item currency (spec 2026-09-15, rulings 2026-10-02): which published
document answers for an item, per type. The view decides; nothing is stored."""

from __future__ import annotations

import uuid

import pytest

from tests.currency_helpers import newer_copy, seed_doc


def _ref(tag):
    return f"PIC-{tag}-{uuid.uuid4().hex[:8]}"


def _current(conn, item_ref, doc_type="DoC"):
    return {r["doc_id"] for r in conn.execute(
        "SELECT doc_id FROM item_document_production WHERE item_ref=%s AND type=%s",
        (item_ref, doc_type)).fetchall()}


def test_the_newer_document_answers_only_for_the_items_it_names(conn):
    one, two, three = _ref("a"), _ref("b"), _ref("c")
    old = seed_doc(conn, [one, two, three], validity_from="2020-01-01")
    new = seed_doc(conn, [one, two], validity_from="2024-01-01")
    assert _current(conn, one) == {new}
    assert _current(conn, two) == {new}
    assert _current(conn, three) == {old}          # a retired article keeps its document


@pytest.mark.parametrize("docs, winners", [
    # (regulation, validity_from) per document; winners are indexes into docs
    ([("MDR", "2024-01-01"), ("MDR", "2024-01-01")], {0, 1}),      # same date: all current
    ([("MDR", "2021-01-01"), ("MDD", "2024-01-01")], {0}),         # MDR over a newer MDD
    ([("MDD", "2021-01-01"), ("n.a.", "2024-01-01")], {1}),        # n.a. vs MDD: by date
    ([("MDD", "2024-01-01"), ("n.a.", "2021-01-01")], {0}),
    ([("MDR", "2020-01-01"), ("n.a.", "2025-01-01")], {0}),        # n.a. never beats MDR
    ([("MDR", None), ("MDR", "2020-01-01")], {1}),                 # undated below dated
    ([("MDR", None), ("MDR", None)], {0, 1}),                      # all undated: all current
    ([("MDR", None), ("MDD", "2024-01-01")], {0}),                 # MDR first, even undated
])
def test_the_pick(conn, docs, winners):
    item = _ref("pick")
    ids = [seed_doc(conn, [item], regulation=reg, validity_from=vf) for reg, vf in docs]
    assert _current(conn, item) == {ids[i] for i in winners}


def test_a_superseded_document_nothing_newer_names_still_answers(conn):
    """The server's doc 29: retired by a document that names none of its
    articles. The old flag is not read for currency."""
    item = _ref("doc29")
    doc = seed_doc(conn, [item], validity_from="2023-01-01", status="superseded")
    assert _current(conn, item) == {doc}


def test_an_older_superseded_document_is_history(conn):
    item = _ref("hist")
    old = seed_doc(conn, [item], validity_from="2020-01-01")
    new = newer_copy(conn, old)
    assert _current(conn, item) == {new}


def test_types_are_picked_independently(conn):
    item = _ref("types")
    doc = seed_doc(conn, [item], doc_type="DoC", validity_from="2020-01-01")
    ifu = seed_doc(conn, [item], doc_type="IFU", validity_from="2025-01-01")
    assert _current(conn, item, "DoC") == {doc}
    assert _current(conn, item, "IFU") == {ifu}


@pytest.mark.parametrize("status, link_status", [
    ("staged", "production"), ("rejected", "production"), ("filed", "production"),
    ("production", "staged"), ("production", "rejected"), ("production", "retracted"),
])
def test_only_published_documents_on_production_links_compete(conn, status, link_status):
    item = _ref("vis")
    live = seed_doc(conn, [item], validity_from="2020-01-01")
    seed_doc(conn, [item], validity_from="2025-01-01", status=status, link_status=link_status)
    assert _current(conn, item) == {live}


def test_history_view_flags_which_row_is_current(conn):
    item = _ref("flag")
    old = seed_doc(conn, [item], validity_from="2020-01-01")
    new = newer_copy(conn, old)
    rows = {r["doc_id"]: r for r in conn.execute(
        "SELECT doc_id, current, doc_status, superseded_by FROM item_document_history "
        "WHERE item_ref=%s", (item,)).fetchall()}
    assert rows[new]["current"] is True
    assert rows[old]["current"] is False
    assert rows[old]["doc_status"] == "superseded" and rows[old]["superseded_by"] == new


def test_document_current(conn):
    a, b = _ref("dc-a"), _ref("dc-b")
    half = seed_doc(conn, [a, b], validity_from="2020-01-01")
    seed_doc(conn, [a], validity_from="2024-01-01")              # newer for a only
    gone = seed_doc(conn, [a], validity_from="2019-01-01")       # history everywhere
    unlinked = seed_doc(conn, [], validity_from="2020-01-01")    # a binding with no items
    unlinked_superseded = seed_doc(conn, [], status="superseded")
    current = {r["doc_id"] for r in conn.execute("SELECT doc_id FROM document_current")}
    assert half in current
    assert gone not in current
    assert unlinked in current
    assert unlinked_superseded not in current


# --- a misread issue date in the future must not win (2026-11-09: docs 481, 483) ---

def _future():
    from datetime import date, timedelta
    return (date.today() + timedelta(days=400)).isoformat()


def test_a_future_issue_date_does_not_outrank_a_real_one(conn):
    item = _ref("future")
    real = seed_doc(conn, [item], validity_from="2025-09-18")
    seed_doc(conn, [item], validity_from=_future())
    assert _current(conn, item) == {real}


def test_a_future_dated_document_still_answers_when_it_is_the_only_one(conn):
    item = _ref("futonly")
    only = seed_doc(conn, [item], validity_from=_future())
    assert _current(conn, item) == {only}


def test_a_future_dated_document_ranks_as_undated(conn):
    item = _ref("futnull")
    a = seed_doc(conn, [item], validity_from=_future())
    b = seed_doc(conn, [item], validity_from=None)
    assert _current(conn, item) == {a, b}


# --- the ranking runs once per query, never once per row (2026-11-12) ---------
#
# `item_document_production` ranks every production link with a window
# partitioned by item. A `doc_id` filter cannot be applied below that window,
# so any correlated subquery over it re-ranks the whole registry per outer row.
# On the server that was 1,959 re-rankings of 12.5k links: the sidebar's
# expiring count took 28 s and /expiry 35 s. The guard is structural, not
# timed: no WindowAgg may sit under a SubPlan, whichever subplan strategy the
# planner picks on this database's statistics.

class _Recorder:
    """Delegates to a real connection and keeps every (sql, params) executed."""

    def __init__(self, conn):
        self._conn = conn
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return self._conn.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _windows_under_subplans(node, under_subplan=False):
    under_subplan = under_subplan or node.get("Parent Relationship") == "SubPlan"
    hits = 1 if under_subplan and node["Node Type"] == "WindowAgg" else 0
    return hits + sum(_windows_under_subplans(child, under_subplan)
                      for child in node.get("Plans", []))


def _assert_ranked_once(conn, sql, params=None):
    plan = conn.execute("EXPLAIN (FORMAT JSON) " + sql, params).fetchone()
    root = next(iter(plan.values()))[0]["Plan"]
    assert _windows_under_subplans(root) == 0


def test_the_menu_expiring_count_ranks_once(conn):
    from web.app import _expiring_counts
    rec = _Recorder(conn)
    _expiring_counts(rec)
    (sql, params), = rec.calls
    _assert_ranked_once(conn, sql, params)


def test_the_expiry_board_ranks_once(conn):
    from web.app import _EXPIRY_BOARD_SQL
    _assert_ranked_once(conn, _EXPIRY_BOARD_SQL)


def test_the_renewal_letter_ranks_once(conn):
    from datetime import date
    from app.handlers.email_request import lapsing_for_manufacturer
    rec = _Recorder(conn)
    lapsing_for_manufacturer(rec, "ACME", horizon_days=30, today=date.today())
    (sql, params), = rec.calls
    _assert_ranked_once(conn, sql, params)


#: 072's definition, verbatim. The view was rewritten for its plan (074); the
#: set it returns must not move.
_DOCUMENT_CURRENT_072 = """
  SELECT d.doc_id
    FROM document d
   WHERE EXISTS (SELECT 1 FROM item_document_production p WHERE p.doc_id = d.doc_id)
      OR (d.status = 'production'
          AND NOT EXISTS (SELECT 1 FROM item_document l
                           WHERE l.doc_id = d.doc_id AND l.status = 'production'))
"""


def test_document_current_returns_what_072_returned(conn):
    a, b = _ref("eq-a"), _ref("eq-b")
    seed_doc(conn, [a, b], validity_from="2020-01-01")
    seed_doc(conn, [a], validity_from="2024-01-01")
    seed_doc(conn, [a], validity_from="2019-01-01")
    seed_doc(conn, [], validity_from="2020-01-01")
    seed_doc(conn, [], status="superseded")
    staged_links_only = seed_doc(conn, [a], link_status="staged")
    staged_doc = seed_doc(conn, [b], status="staged")
    current = {r["doc_id"] for r in conn.execute("SELECT doc_id FROM document_current")}
    before = {r["doc_id"] for r in conn.execute(_DOCUMENT_CURRENT_072)}
    assert current == before
    assert staged_links_only in current      # production, no production link
    assert staged_doc not in current         # not published
