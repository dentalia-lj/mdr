"""The D1 rename action (slice 3b, task 13).

A rename is an ACTION with a confirmation step, not a form field, because the
canonical name is effectively write-once downstream: `resolve.py`'s ladder is
`_existing_link(...) or _by_udi(...) or ...` and short-circuits, so an item
already in a group never re-derives its manufacturer. Groups built under the
old name keep it, and a rename cannot reach them.

What it CAN write is narrower than the plan assumed, and that is the invariant
working rather than a shortcut: `dentalia_api` is SELECT-only on `item_group`,
`item_group_member` and `manufacturer_alias`, so the re-resolve (which DELETEs
groups) belongs to `dentalia regroup` in a worker. These tests pin that the
action reports the work it could not do instead of pretending it did it.
"""

from __future__ import annotations

import json

import pytest

from fastapi.testclient import TestClient

from app import manufacturer_seed
from app import playbooks as pb
from app import regroup
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
        # The S2.3 children, which key on the canonical NAME rather than the id
        # -- see the cascade tests at the bottom of this file.
        conn.execute("DELETE FROM manufacturer_srn")
        conn.execute("DELETE FROM eudamed_sweep_state")
        # 053 made `document` a child of `manufacturer` too. Un-REFERENCE rather
        # than delete: the binding is the only thing tying them, and the
        # documents themselves belong to whatever seeded them, not to this
        # fixture. ON DELETE is deliberately NO ACTION (052), so without this
        # the wipe raises instead of cascading.
        conn.execute("UPDATE document SET canonical_manufacturer = NULL "
                     "WHERE canonical_manufacturer IS NOT NULL")
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
    (tmp_path / "alpha.json").write_text(json.dumps({
        "manufacturer": "ALPHA GMBH",
        "bc_codes": [{"code": "001"}],
        "aliases": ["Alpha Dental"],
        "domains": ["alpha.example"],
    }))
    for code, name in (("001", "ALPHA GMBH"), ("002", "BETA AG"),
                       ("003", "SIRONA")):
        conn.execute(
            "INSERT INTO vendor_master (code_source, code, name, import_batch) "
            "VALUES ('LJ',%s,%s,'rename-test')", (code, name))
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


def _group(conn, manufacturer, *, item_ref, with_document=False):
    """One item_group carrying `manufacturer`, with one member."""
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, catalogue, "
        "  updated_at) VALUES (%s,%s,'001','LJ',now())",
        (item_ref, f"item {item_ref}"))
    gid = conn.execute(
        "INSERT INTO item_group (canonical_manufacturer, label) "
        "VALUES (%s,%s) RETURNING group_id",
        (manufacturer, f"grp {item_ref}")).fetchone()["group_id"]
    conn.execute(
        "INSERT INTO item_group_member (group_id, item_ref, match_basis) "
        "VALUES (%s,%s,'ref-item')", (gid, item_ref))
    if with_document:
        doc = conn.execute(
            "INSERT INTO document (type, regulation, coverage_scope, "
            "  content_hash, archive_url, status) "
            "VALUES ('DoC','MDR','group',%s,'file:///d.pdf',"
            "  'production'::doc_status) RETURNING doc_id",
            (f"hash-{item_ref}",)).fetchone()["doc_id"]
        conn.execute(
            "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
            "VALUES (%s,%s,'ref-list','production'::link_status)",
            (item_ref, doc))
    return gid


def _rename(conn, old, new, *, note="because BC spells it differently",
            confirm=False):
    return registry.rename_manufacturer(
        conn, canonical_name=old, new_name=new, note=note, user=USER,
        confirm=confirm)


def _names(conn, canonical):
    rows = conn.execute(
        "SELECT n.name, n.kind FROM manufacturer_name n JOIN manufacturer m "
        "ON m.id = n.manufacturer_id WHERE m.canonical_name=%s "
        "ORDER BY n.name_folded", (canonical,)).fetchall()
    return {r["name"]: r["kind"] for r in rows}


# --------------------------------------------------------------------------- #
# the preview, and that it agrees with regroup
# --------------------------------------------------------------------------- #
def test_rename_impact_agrees_with_regroup_scope(conn, seeded):
    """`rename_impact` is a deliberate SUBSET of `scope()` -- it drops the
    `upload_inbox` query, which the web role may not read. The two numbers it
    does report must be the same numbers, or the UI prices a rename the CLI
    then disagrees with."""
    _group(conn, "ALPHA GMBH", item_ref="A1")
    _group(conn, "ALPHA GMBH", item_ref="A2", with_document=True)
    _group(conn, "BETA AG", item_ref="B1")

    impact = regroup.rename_impact(conn, "ALPHA GMBH")
    sc = regroup.scope(conn, manufacturers=["ALPHA GMBH"])

    assert impact.groups == len(sc.groups) == 2
    assert impact.items == len(sc.items) == 2
    assert impact.with_documents == len(
        [b for b in sc.blockers if b.kind == "has-documents"]) == 1


def test_rename_impact_is_empty_when_no_group_carries_the_name(conn, seeded):
    impact = regroup.rename_impact(conn, "ALPHA GMBH")

    assert impact.clean and impact.groups == 0
    assert 'ALPHA GMBH' in impact.command


def test_rename_impact_counts_the_documents_the_cascade_will_rewrite(conn, seeded):
    """053 put an `ON UPDATE CASCADE` FK on `document.canonical_manufacturer`,
    so a rename now silently rewrites a set of rows this report could not
    previously see. Understating its own blast radius is the exact failure
    migration 052 was written to close."""
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('ALPHA GMBH') "
                 "ON CONFLICT (canonical_name) DO NOTHING")
    for h in ("ri-1", "ri-2"):
        conn.execute(
            "INSERT INTO document (type, regulation, coverage_scope, content_hash, "
            "archive_url, status, canonical_manufacturer) "
            "VALUES ('ISO','n.a.','manufacturer',%s,'file:///r.pdf','filed','ALPHA GMBH')",
            (h,))

    assert regroup.rename_impact(conn, "ALPHA GMBH").bound_documents == 2


def test_bound_documents_are_counted_even_when_no_group_carries_the_name(conn, seeded):
    """The count must survive the early return. A manufacturer whose groups
    were all regrouped away can still hold filed documents -- 443 of the 540
    link-less documents are filed -- and renaming still rewrites them."""
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('ALPHA GMBH') "
                 "ON CONFLICT (canonical_name) DO NOTHING")
    conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, "
        "archive_url, status, canonical_manufacturer) "
        "VALUES ('ISO','n.a.','manufacturer','ri-3','file:///r.pdf','filed','ALPHA GMBH')")

    impact = regroup.rename_impact(conn, "ALPHA GMBH")

    assert impact.groups == 0
    assert impact.bound_documents == 1
    # `clean` is about STRANDED groups, which is still nothing here: a cascaded
    # document follows the rename correctly and strands nobody.
    assert impact.clean


# --------------------------------------------------------------------------- #
# the two steps
# --------------------------------------------------------------------------- #
def test_without_confirmation_nothing_is_written(conn, seeded):
    _group(conn, "ALPHA GMBH", item_ref="A1")

    out = _rename(conn, "ALPHA GMBH", "ALPHA DENTAL GMBH")

    assert out["applied"] is False
    assert out["impact"].groups == 1
    assert conn.execute(
        "SELECT canonical_name FROM manufacturer WHERE slug='alpha'"
    ).fetchone()["canonical_name"] == "ALPHA GMBH"
    assert _names(conn, "ALPHA GMBH") == {"ALPHA GMBH": "canonical",
                                          "Alpha Dental": "alias"}


def test_confirming_renames_and_keeps_the_old_name_as_an_alias(conn, seeded):
    """The old name is demoted, never deleted. Documents already printed it --
    that is what a legal name on a certificate is -- and dropping the row would
    make every one of them stop resolving."""
    out = _rename(conn, "ALPHA GMBH", "ALPHA DENTAL GMBH", confirm=True)

    assert out["applied"] is True and out["old_name"] == "ALPHA GMBH"
    assert conn.execute(
        "SELECT canonical_name FROM manufacturer WHERE slug='alpha'"
    ).fetchone()["canonical_name"] == "ALPHA DENTAL GMBH"
    assert _names(conn, "ALPHA DENTAL GMBH") == {
        "ALPHA DENTAL GMBH": "canonical",
        "ALPHA GMBH": "alias",
        "Alpha Dental": "alias",
    }


def test_the_renamed_playbook_loads_with_the_old_name_among_its_aliases(conn, seeded):
    """End to end through the loader: the demoted name lands back in
    `aliases`, which is what makes `for_manufacturer` still find it."""
    _rename(conn, "ALPHA GMBH", "ALPHA DENTAL GMBH", confirm=True)
    conn.commit()

    class _Keep:                       # the test's own connection, not closed
        def __enter__(self): return conn
        def __exit__(self, *a): return False

    pb.set_source(lambda: _Keep())
    pb.clear_cache()
    loaded = pb.load_playbooks()

    alpha = next(p for p in loaded if p.slug == "alpha")
    assert alpha.manufacturer == "ALPHA DENTAL GMBH"
    assert "ALPHA GMBH" in alpha.aliases
    assert pb.for_manufacturer("ALPHA GMBH", loaded) is alpha


def test_a_case_only_rename_does_not_collide_with_its_own_row(conn, seeded):
    """`manufacturer_name`'s PK is the CASEFOLDED name, so `ALPHA GMBH` ->
    `Alpha GmbH` is one row, not two. Demote-then-upsert is the order that
    survives it."""
    out = _rename(conn, "ALPHA GMBH", "Alpha GmbH", confirm=True)

    assert out["applied"] is True
    assert _names(conn, "Alpha GmbH") == {"Alpha GmbH": "canonical",
                                          "Alpha Dental": "alias"}


def test_updated_by_records_who_renamed_it(conn, seeded):
    _rename(conn, "ALPHA GMBH", "ALPHA DENTAL GMBH", confirm=True)

    row = conn.execute(
        "SELECT updated_by FROM manufacturer WHERE slug='alpha'").fetchone()
    assert row["updated_by"] == USER


# --------------------------------------------------------------------------- #
# refusals -- all reachable from the UN-confirmed step
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("new_name, note, fragment", [
    ("", "why", "a new name is required"),
    ("   ", "why", "a new name is required"),
    ("ALPHA GMBH", "why", "already the name"),
    ("ALPHA DENTAL GMBH", "", "a note is required"),
    ("ALPHA DENTAL GMBH", "   ", "a note is required"),
])
def test_refusals(conn, seeded, new_name, note, fragment):
    with pytest.raises(registry.SaveRefused) as exc:
        _rename(conn, "ALPHA GMBH", new_name, note=note)
    assert fragment in str(exc.value)


def test_renaming_onto_another_manufacturers_name_is_refused_naming_it(conn, seeded):
    with pytest.raises(registry.SaveRefused) as exc:
        _rename(conn, "ALPHA GMBH", "BETA AG")

    assert "BETA AG" in str(exc.value)
    assert "One name, one manufacturer" in str(exc.value)


def test_renaming_into_another_bc_brand_is_refused(conn, seeded):
    """D2 with a bigger blast radius than an alias: this name is what every
    future fetch files under."""
    with pytest.raises(registry.SaveRefused) as exc:
        _rename(conn, "ALPHA GMBH", "Sirona Dental Systems GmbH")

    assert "SIRONA" in str(exc.value) and "003" in str(exc.value)


def test_a_manufacturer_with_no_row_is_refused_not_created(conn, seeded):
    with pytest.raises(registry.SaveRefused) as exc:
        _rename(conn, "NOBODY LTD", "SOMEBODY LTD")

    assert "manufacturers seed" in str(exc.value)


def test_a_refusal_is_reachable_before_confirming(conn, seeded):
    """`confirm` is checked LAST. Ticking the box must not be treated as an
    answer to a question the operator was never asked."""
    with pytest.raises(registry.SaveRefused):
        _rename(conn, "ALPHA GMBH", "BETA AG", confirm=True)


# --------------------------------------------------------------------------- #
# the route
# --------------------------------------------------------------------------- #
def test_route_first_post_prices_it_and_writes_nothing(conn, seeded, client):
    _group(conn, "ALPHA GMBH", item_ref="A1", with_document=True)
    conn.commit()

    r = client.post("/manufacturers/ALPHA GMBH/rename",
                    data={"new_name": "ALPHA DENTAL GMBH", "note": "why"})

    assert r.status_code == 200
    assert "1 item group" in r.text
    assert "CANNOT be regrouped" in r.text
    assert "dentalia regroup" in r.text
    assert "Yes, rename it" in r.text
    assert conn.execute(
        "SELECT canonical_name FROM manufacturer WHERE slug='alpha'"
    ).fetchone()["canonical_name"] == "ALPHA GMBH"


def test_route_confirmed_post_renames_and_names_the_remaining_work(conn, seeded, client):
    _group(conn, "ALPHA GMBH", item_ref="A1")
    conn.commit()

    r = client.post("/manufacturers/ALPHA GMBH/rename",
                    data={"new_name": "ALPHA DENTAL GMBH", "note": "why",
                          "confirm": "1"})

    assert r.status_code == 200
    assert "playbooks sync" in r.text and "dentalia regroup" in r.text
    assert "kept as an alias" in r.text
    assert conn.execute(
        "SELECT canonical_name FROM manufacturer WHERE slug='alpha'"
    ).fetchone()["canonical_name"] == "ALPHA DENTAL GMBH"


def test_route_refusal_is_422_and_writes_nothing(conn, seeded, client):
    r = client.post("/manufacturers/ALPHA GMBH/rename",
                    data={"new_name": "BETA AG", "note": "why", "confirm": "1"})

    assert r.status_code == 422
    assert "BETA AG" in r.text
    assert conn.execute(
        "SELECT canonical_name FROM manufacturer WHERE slug='alpha'"
    ).fetchone()["canonical_name"] == "ALPHA GMBH"


# --------------------------------------------------------------------------- #
# the EUDAMED children, which key on the NAME (migration 052)
# --------------------------------------------------------------------------- #
# S2.3 stage 2 gave `manufacturer.canonical_name` two FK children --
# `manufacturer_srn` (042) and `eudamed_sweep_state` (044) -- on a branch that
# could not see this rename, which was being built on a branch that could not
# see them. Neither declared `ON UPDATE`, so the default NO ACTION turned the
# confirmed rename into a `ForeignKeyViolation` out of a route whose entire
# purpose is to refuse cleanly instead. 052 makes both cascade. Found when the
# two branches merged, 2026-08-27.
def _srn(conn, canonical, srn="XX-MF-9"):
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, actor_name, "
        "  discovered_via, status, match_score) "
        "VALUES (%s,%s,'Alpha Gmbh','register-fuzzy','confirmed',91.0)",
        (canonical, srn))


def _sweep_state(conn, canonical):
    conn.execute(
        "INSERT INTO eudamed_sweep_state (canonical_name, due_at) "
        "VALUES (%s, now())", (canonical,))


def test_rename_carries_an_srn_attribution_with_it(conn, seeded):
    """An SRN is an attribute of the named entity, not a record that merely
    cites it: renaming does not invalidate the attribution, it renames the
    thing attributed. Before 052 this raised ForeignKeyViolation."""
    _srn(conn, "ALPHA GMBH")
    conn.commit()

    _rename(conn, "ALPHA GMBH", "ALPHA DENTAL GMBH", confirm=True)
    conn.commit()

    rows = conn.execute(
        "SELECT canonical_name, status FROM manufacturer_srn").fetchall()
    assert [(r["canonical_name"], r["status"]) for r in rows] == [
        ("ALPHA DENTAL GMBH", "confirmed")]


def test_rename_carries_the_sweep_schedule_with_it(conn, seeded):
    """Same argument for the quarterly sweep state, and it matters more: a
    dropped row would silently un-schedule the manufacturer rather than error."""
    _sweep_state(conn, "ALPHA GMBH")
    conn.commit()

    _rename(conn, "ALPHA GMBH", "ALPHA DENTAL GMBH", confirm=True)
    conn.commit()

    assert conn.execute(
        "SELECT canonical_name FROM eudamed_sweep_state").fetchall() == [
        {"canonical_name": "ALPHA DENTAL GMBH"}]


def test_the_route_renames_cleanly_with_both_children_present(conn, seeded, client):
    """Through HTTP as `dentalia_api`, which is where the 500 would have
    surfaced -- the role holds UPDATE on `manufacturer` but the cascade is the
    database's work, not the role's."""
    _srn(conn, "ALPHA GMBH")
    _sweep_state(conn, "ALPHA GMBH")
    conn.commit()

    r = client.post("/manufacturers/ALPHA GMBH/rename",
                    data={"new_name": "ALPHA DENTAL GMBH", "note": "why",
                          "confirm": "1"})

    assert r.status_code == 200, r.text
    assert conn.execute(
        "SELECT canonical_name FROM manufacturer_srn").fetchone()[
        "canonical_name"] == "ALPHA DENTAL GMBH"


def test_deleting_a_manufacturer_is_still_refused(conn, seeded):
    """052 widens ON UPDATE only. A DELETE that would strand an SRN must still
    raise -- cascading that too would silently discard an attribution."""
    import psycopg

    _srn(conn, "ALPHA GMBH")
    conn.commit()

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute("DELETE FROM manufacturer WHERE canonical_name='ALPHA GMBH'")
    conn.rollback()
