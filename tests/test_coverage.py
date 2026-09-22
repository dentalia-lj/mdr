"""`app.coverage` — the one definition of "uncovered" and "searched".

Three callers ask the first question (the manufacturer button, the coverage
cron, any coverage figure) and two ask the second (`/discovery` and the item
page). They must not answer differently, which is why both live here.
"""

from app import coverage

# --------------------------------------------------------------------------- #
# per-item discovery state — F3, 2026-09-04
# --------------------------------------------------------------------------- #
# The registry could always tell "never looked" from "looked and found nothing"
# and no screen read it: for 3.987 of 4.265 medical-device items the item page
# rendered one unconditional "No documents linked to this item yet". These pin
# the three states and the fact that coverage and search are separate questions.

def _seed_group(conn, item_ref="I-DS-1", group_id=90001):
    """One item in one group. Mirrors conftest's own seeding so a schema change
    breaks both together rather than only here."""
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, catalogue, updated_at) "
        "VALUES (%s,'Probe','081','LJ', now()) ON CONFLICT DO NOTHING", (item_ref,))
    conn.execute(
        "INSERT INTO item_group (group_id, canonical_manufacturer, label) "
        "VALUES (%s,'CARL MARTIN','Probe group') ON CONFLICT DO NOTHING", (group_id,))
    conn.execute(
        "INSERT INTO item_group_member (group_id, item_ref, match_basis) "
        "VALUES (%s,%s,'manual') ON CONFLICT DO NOTHING", (group_id, item_ref))
    return item_ref, group_id


def test_a_group_with_no_discovery_log_row_is_never(conn):
    item_ref, _ = _seed_group(conn)
    st = coverage.discovery_state(conn, item_ref)
    assert st["state"] == coverage.NEVER
    assert st["attempts"] == 0 and st["hits"] == 0
    assert st["label_text"] == "Never searched"


def test_attempts_that_all_missed_are_empty_not_never(conn):
    """The distinction the whole finding is about."""
    item_ref, gid = _seed_group(conn, "I-DS-2", 90002)
    for src in ("known_url", "playbook", "search"):
        conn.execute("INSERT INTO discovery_log (group_id, source, outcome) "
                     "VALUES (%s,%s,'miss')", (gid, src))
    st = coverage.discovery_state(conn, item_ref)
    assert st["state"] == coverage.EMPTY
    assert st["attempts"] == 3 and st["hits"] == 0
    assert st["label_text"] == "Searched, found nothing"


def test_any_hit_makes_it_found(conn):
    item_ref, gid = _seed_group(conn, "I-DS-3", 90003)
    conn.execute("INSERT INTO discovery_log (group_id, source, outcome) "
                 "VALUES (%s,'known_url','miss')", (gid,))
    conn.execute("INSERT INTO discovery_log (group_id, source, outcome) "
                 "VALUES (%s,'search','hit')", (gid,))
    st = coverage.discovery_state(conn, item_ref)
    assert st["state"] == coverage.FOUND
    assert st["hits"] == 1


def test_the_rung_list_keeps_the_newest_attempt_per_source(conn):
    item_ref, gid = _seed_group(conn, "I-DS-4", 90004)
    conn.execute("INSERT INTO discovery_log (group_id, source, outcome, at) "
                 "VALUES (%s,'search','miss', now() - interval '2 days')", (gid,))
    conn.execute("INSERT INTO discovery_log (group_id, source, outcome, at) "
                 "VALUES (%s,'search','hit', now())", (gid,))
    st = coverage.discovery_state(conn, item_ref)
    sources = {r["source"]: r["outcome"] for r in st["rungs"]}
    assert sources == {"search": "hit"}, "a stale miss must not shadow the newer hit"


def test_an_item_in_no_group_returns_none_rather_than_guessing(conn):
    conn.execute("INSERT INTO item_mirror (item_ref, name, manufacturer_raw, catalogue, updated_at) "
                 "VALUES ('I-DS-orphan','Orphan','081','LJ', now()) ON CONFLICT DO NOTHING")
    assert coverage.discovery_state(conn, "I-DS-orphan") is None


def test_holding_a_document_does_not_make_a_group_searched(conn):
    """Coverage and diligence are separate questions: 755 of 870 documents
    arrived by backfill, into groups DISCOVER never touched."""
    item_ref, _ = _seed_group(conn, "I-DS-5", 90005)
    assert coverage.discovery_state(conn, item_ref)["state"] == coverage.NEVER


# --- the manual handoff is not a find (F3) ---------------------------------
#
# Every rung logs `hit` when it FOUND something. The manual rung logged one when
# it gave up, so 258 groups read "Searched, found something" on the screens that
# exist to say the opposite, and `EMPTY` became unreachable after any completed
# run.

def test_a_handoff_is_not_a_hit(conn):
    item_ref, gid = _seed_group(conn, "I-DS-10", 90010)
    conn.execute("INSERT INTO discovery_log (group_id, source, outcome) "
                 "VALUES (%s,'search','miss')", (gid,))
    conn.execute("INSERT INTO discovery_log (group_id, source, outcome) "
                 "VALUES (%s,'manual','handoff')", (gid,))

    st = coverage.discovery_state(conn, item_ref)

    assert st["state"] == coverage.HANDOFF
    assert st["hits"] == 0
    assert st["handoffs"] == 1


def test_the_handoff_label_names_the_screen_the_work_went_to(conn):
    """Not "Searched, found nothing" either: the office needs to know the work
    is already on a screen, and which one."""
    item_ref, gid = _seed_group(conn, "I-DS-11", 90011)
    conn.execute("INSERT INTO discovery_log (group_id, source, outcome) "
                 "VALUES (%s,'manual','handoff')", (gid,))

    st = coverage.discovery_state(conn, item_ref)

    assert st["label_text"] == "Searched, nothing found, sent to Missing documents"


def test_a_real_hit_still_wins_over_a_later_handoff(conn):
    """A group can be searched again after something was found. The find is what
    a reviewer needs to see."""
    item_ref, gid = _seed_group(conn, "I-DS-12", 90012)
    conn.execute("INSERT INTO discovery_log (group_id, source, outcome) "
                 "VALUES (%s,'search','hit')", (gid,))
    conn.execute("INSERT INTO discovery_log (group_id, source, outcome) "
                 "VALUES (%s,'manual','handoff')", (gid,))

    assert coverage.discovery_state(conn, item_ref)["state"] == coverage.FOUND


def test_empty_is_a_search_that_never_reached_the_manual_rung(conn):
    """`EMPTY` is the rare state now, not the common one: a ladder that runs to
    the end always reaches the manual rung, so a plain miss means the run
    stopped early -- every rung deferred, or the group re-queued mid-ladder."""
    item_ref, gid = _seed_group(conn, "I-DS-13", 90013)
    for src in ("search", "eudamed"):
        conn.execute("INSERT INTO discovery_log (group_id, source, outcome) "
                     "VALUES (%s,%s,'miss')", (gid, src))

    assert coverage.discovery_state(conn, item_ref)["state"] == coverage.EMPTY
