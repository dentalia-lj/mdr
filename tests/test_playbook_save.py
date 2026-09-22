"""Editing a playbook body through the web layer (slice 3a, task 10).

The save path is where a non-dev's edit meets a production pipeline, so what
these cover is mostly the REFUSALS. A save that goes through and turns out to
be wrong is discovered by RESOLVE weeks later, mapping items to the wrong
manufacturer; a save that is refused at 422 is discovered by the person who
made it, while they still remember what they meant.

The revert tests exist to pin one thing: history is append-only. `dentalia_api`
is granted neither UPDATE nor DELETE on `manufacturer_playbook_revision`
(migration 050), so a revert that tried to rewrite history would fail at the
grant -- but the code must not try, because the mistake and its undo are both
part of the record.
"""
from __future__ import annotations

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
    """A TestClient on the `dentalia_api` role, the same shape test_web.py
    uses. Local rather than shared because the routes under test are the only
    ones this module touches."""
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
    """Two playbooks in a test-owned directory, seeded into rows.

    Two, not one: `validate` is a whole-set check, and a single-playbook
    fixture cannot tell a set check from a per-playbook one.
    """
    import json

    (tmp_path / "alpha.json").write_text(json.dumps({
        "manufacturer": "ALPHA GMBH",
        "bc_codes": [{"code": "001"}],
        "aliases": ["Alpha Dental"],
        "domains": ["alpha.example"],
        "doc_sources": [{"doc_type": "DoC", "kind": "portal",
                         "url": "https://alpha.example/doc"}],
    }))
    (tmp_path / "beta.json").write_text(json.dumps({
        "manufacturer": "BETA AG",
        "bc_codes": [{"code": "002"}],
        "aliases": [],
        "domains": ["beta.example"],
    }))
    for code, name in (("001", "ALPHA GMBH"), ("002", "BETA AG")):
        conn.execute(
            "INSERT INTO vendor_master (code_source, code, name, import_batch) "
            "VALUES ('LJ',%s,%s,'save-test')", (code, name))
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


def _rev(conn, slug):
    return conn.execute(
        "SELECT playbook_rev FROM manufacturer WHERE slug=%s", (slug,)
    ).fetchone()["playbook_rev"]


def _body(conn, slug):
    return conn.execute(
        "SELECT body FROM manufacturer WHERE slug=%s", (slug,)).fetchone()["body"]


def _save(conn, seeded, slug="alpha", body=None, note="because", rev=None):
    return registry.save_playbook_body(
        conn, slug=slug,
        body=body if body is not None else {"domains": ["changed.example"]},
        note=note, user=USER,
        expected_rev=_rev(conn, slug) if rev is None else rev,
        playbooks_dir=seeded)


# --------------------------------------------------------------------------- #
# the happy path, once

def test_a_save_bumps_the_rev_and_records_who_and_why(conn, seeded):
    new = _save(conn, seeded, note="point at the new download host")
    conn.commit()

    assert new == 1 and _rev(conn, "alpha") == 1
    assert _body(conn, "alpha")["domains"] == ["changed.example"]

    row = conn.execute(
        "SELECT updated_by FROM manufacturer WHERE slug='alpha'").fetchone()
    assert row["updated_by"] == USER

    revs = registry.revisions(conn, "alpha")
    assert [r["rev"] for r in revs] == [1, 0]
    assert revs[0]["note"] == "point at the new download host"
    assert revs[0]["authored_by"] == USER


def test_identity_is_not_editable_through_the_body(conn, seeded):
    """`manufacturer`, `bc_codes` and `aliases` live in the constrained tables
    (049) and are Tier B/D. A body claiming them must not be able to smuggle a
    change past the constraints that exist to police exactly those three."""
    _save(conn, seeded, body={
        "domains": ["ok.example"],
        "manufacturer": "SOMEONE ELSE LTD",
        "bc_codes": [{"code": "999"}],
        "aliases": ["Hijacked"],
    })
    conn.commit()

    assert conn.execute(
        "SELECT canonical_name FROM manufacturer WHERE slug='alpha'"
    ).fetchone()["canonical_name"] == "ALPHA GMBH"
    assert set(_body(conn, "alpha")) == {"domains"}
    assert conn.execute(
        "SELECT count(*) n FROM manufacturer_bc_code WHERE code='999'"
    ).fetchone()["n"] == 0


# --------------------------------------------------------------------------- #
# the refusals

def test_a_save_without_a_note_is_refused(conn, seeded):
    """Denis, 2026-08-27. A revision list of timestamps and usernames does not
    make a revert obvious months later."""
    for empty in ("", "   ", None):
        with pytest.raises(registry.SaveRefused, match="note is required"):
            _save(conn, seeded, note=empty)
        conn.rollback()
    assert _rev(conn, "alpha") == 0


def test_a_body_that_does_not_parse_is_refused_not_silently_dropped(conn, seeded):
    """`load_playbooks` never raises -- it logs and skips. So a body saved
    without this check would vanish from the next job with nothing on screen
    to say so: the manufacturer would simply stop having a playbook."""
    with pytest.raises(registry.SaveRefused, match="doc_source kind"):
        _save(conn, seeded, body={"doc_sources": [
            {"doc_type": "DoC", "kind": "invented", "url": "https://x/"}]})
    conn.rollback()
    assert _rev(conn, "alpha") == 0


def test_an_uncompilable_regex_is_refused_at_save(conn, seeded):
    with pytest.raises(registry.SaveRefused, match="cert_number_pattern is not a valid regex"):
        _save(conn, seeded, body={"cert_number_pattern": "^([0-9"})
    conn.rollback()


def test_an_extract_hint_for_a_field_nobody_asks_for_is_refused(conn, seeded):
    """The typo that used to pass: a hint filed under a field the extractor is
    never asked about is silently ignored, and reads in the file as applied."""
    with pytest.raises(registry.SaveRefused, match="extract_hints"):
        _save(conn, seeded, body={"extract_hints": {"validity_form": "dd.mm.yyyy"}})
    conn.rollback()


def test_the_whole_set_is_validated_not_just_the_edited_playbook(conn, seeded):
    """The trap the spec names. `validate` checks for a name claimed twice --
    across playbooks. Validating the edited one alone would pass exactly the
    authoring mistake the check exists to catch, and RESOLVE would find it
    weeks later by scoping BETA's documents to ALPHA's groups."""
    with pytest.raises(registry.SaveRefused):
        # `alpha` cannot be saved with a doc_source on a host we may never
        # fetch -- a whole-set rule, checked against the refused list.
        original = pb.load_robots_refused
        pb.load_robots_refused = lambda *a, **k: frozenset({"blocked.example"})
        try:
            _save(conn, seeded, body={"doc_sources": [
                {"doc_type": "DoC", "kind": "direct",
                 "url": "https://blocked.example/doc.pdf"}]})
        finally:
            pb.load_robots_refused = original
    conn.rollback()
    assert _rev(conn, "alpha") == 0


def test_a_stale_form_is_refused_and_the_other_edit_survives(conn, seeded):
    """Two people with the page open. The second save must not silently
    overwrite the first -- `WHERE playbook_rev=%s` is what makes that
    structural rather than a race nobody notices."""
    _save(conn, seeded, body={"domains": ["first.example"]}, note="first")
    conn.commit()

    with pytest.raises(registry.StaleForm, match="changed while this form"):
        _save(conn, seeded, body={"domains": ["second.example"]},
              note="second", rev=0)          # the rev the stale form still holds
    conn.rollback()

    assert _body(conn, "alpha")["domains"] == ["first.example"]
    assert _rev(conn, "alpha") == 1


def test_saving_an_unknown_slug_is_refused(conn, seeded):
    with pytest.raises(registry.SaveRefused, match="no playbook"):
        registry.save_playbook_body(
            conn, slug="nope", body={}, note="x", user=USER,
            expected_rev=0, playbooks_dir=seeded)


# --------------------------------------------------------------------------- #
# revert -- append-only, always

def test_revert_writes_the_old_body_forward_as_a_new_revision(conn, seeded):
    _save(conn, seeded, body={"domains": ["v1.example"]}, note="one")
    conn.commit()
    _save(conn, seeded, body={"domains": ["v2.example"]}, note="two")
    conn.commit()

    new = registry.revert_playbook(conn, slug="alpha", to_rev=1, user=USER,
                                   expected_rev=_rev(conn, "alpha"),
                                   playbooks_dir=seeded)
    conn.commit()

    assert new == 3
    assert _body(conn, "alpha")["domains"] == ["v1.example"]
    # Nothing was deleted or rewritten: every revision is still there.
    assert [r["rev"] for r in registry.revisions(conn, "alpha")] == [3, 2, 1, 0]
    assert registry.revisions(conn, "alpha")[0]["note"] == "revert to revision 1"


def test_the_mistake_stays_readable_after_its_undo(conn, seeded):
    """The reason revert is append-only. An auditor asking "what did this
    playbook say when that document was extracted" must be able to read the
    bad revision, not just the corrected one."""
    _save(conn, seeded, body={"domains": ["mistake.example"]}, note="oops")
    conn.commit()
    registry.revert_playbook(conn, slug="alpha", to_rev=0, user=USER,
                             expected_rev=_rev(conn, "alpha"),
                             playbooks_dir=seeded)
    conn.commit()

    bad = next(r for r in registry.revisions(conn, "alpha") if r["rev"] == 1)
    assert bad["body"]["domains"] == ["mistake.example"]
    assert bad["note"] == "oops"


def test_reverting_to_a_revision_that_does_not_exist_is_refused(conn, seeded):
    with pytest.raises(registry.SaveRefused, match="no revision"):
        registry.revert_playbook(conn, slug="alpha", to_rev=99, user=USER,
                                 expected_rev=0, playbooks_dir=seeded)


def test_the_web_role_cannot_rewrite_or_delete_a_revision(conn, seeded):
    """Append-only is enforced by the GRANT, not by this module's good
    behaviour. Pinned here as well as in the migration test because slice 3a is
    where someone would be tempted to widen it to make revert simpler."""
    def _has(priv):
        return conn.execute(
            "SELECT has_table_privilege('dentalia_api', "
            "'manufacturer_playbook_revision', %s) AS ok", (priv,)).fetchone()["ok"]

    assert _has("INSERT") is True
    assert _has("UPDATE") is False
    assert _has("DELETE") is False


# --------------------------------------------------------------------------- #
# the loader sees it

def test_a_saved_edit_reaches_the_next_job_without_a_restart(conn, seeded):
    """The epoch trigger's whole purpose. A worker holding a cached playbook
    must pick this up on its next load, or the editor is a UI over nothing."""
    pb.set_source(lambda: _KeepOpen(conn))
    assert pb.for_manufacturer("ALPHA GMBH").domains == ("alpha.example",)

    _save(conn, seeded, body={"domains": ["edited.example"]}, note="edit")
    conn.commit()

    assert pb.for_manufacturer("ALPHA GMBH").domains == ("edited.example",)


class _KeepOpen:
    """A conn_factory over the test's connection; `with` must not close it."""

    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self._conn

    def __exit__(self, *a):
        return False


# --------------------------------------------------------------------------- #
# the page and its revert button

def test_the_detail_page_lists_the_history_and_says_where_playbooks_live(
        client, conn, seeded, monkeypatch):
    """The old page told the operator to edit `playbooks/{slug}.json` and run
    `playbooks sync`. Since slice 2 that is false -- the file is the authoring
    record and changes nothing until `manufacturers seed` imports it -- and a
    page that says otherwise sends someone to edit a file for an hour."""
    monkeypatch.setattr(pb, "PLAYBOOKS_DIR", seeded)
    _save(conn, seeded, body={"domains": ["v1.example"]}, note="widen the host")
    conn.commit()

    text = client.get("/playbooks/alpha").text
    # Scoped to the header, which is where the false instruction lived. The
    # page mentions `playbooks sync` further down for what it really does --
    # re-point `manufacturer_alias` after a BC code is claimed (task 14) --
    # and a blanket "not in text" would forbid that true sentence too.
    header = text.split("<h2>", 1)[0]
    assert "manufacturers seed" in header
    assert "changes nothing until that import runs" in header
    assert "playbooks sync" not in header
    assert "widen the host" in text          # the note is the point of the list
    assert "Restore" in text                 # rev 0 is restorable


def test_restoring_through_the_page_appends_rather_than_rewrites(
        client, conn, seeded, monkeypatch):
    monkeypatch.setattr(pb, "PLAYBOOKS_DIR", seeded)
    _save(conn, seeded, body={"domains": ["mistake.example"]}, note="oops")
    conn.commit()

    resp = client.post("/playbooks/alpha/revert", data={"to_rev": 0, "rev": 1})
    assert resp.status_code == 200, resp.text

    assert _body(conn, "alpha").get("domains") == ["alpha.example"]
    assert [r["rev"] for r in registry.revisions(conn, "alpha")] == [2, 1, 0]


def test_restoring_from_a_stale_page_is_refused_at_422(
        client, conn, seeded, monkeypatch):
    """Same hazard as a stale save: the page was rendered before someone
    else's edit, and its Restore button carries the revision it was rendered
    at. Acting on it would discard that edit without saying so."""
    monkeypatch.setattr(pb, "PLAYBOOKS_DIR", seeded)
    _save(conn, seeded, body={"domains": ["theirs.example"]}, note="theirs")
    conn.commit()

    resp = client.post("/playbooks/alpha/revert", data={"to_rev": 0, "rev": 0})
    assert resp.status_code == 422
    assert "changed while this form was open" in resp.text
    assert _body(conn, "alpha")["domains"] == ["theirs.example"]


def test_restoring_a_revision_that_does_not_exist_is_refused_at_422(
        client, conn, seeded, monkeypatch):
    monkeypatch.setattr(pb, "PLAYBOOKS_DIR", seeded)
    resp = client.post("/playbooks/alpha/revert", data={"to_rev": 99, "rev": 0})
    assert resp.status_code == 422
    assert "no revision 99" in resp.text
