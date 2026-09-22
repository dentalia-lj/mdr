"""Tier B controls (slice 3b, task 14).

Tier B is not "harder to type" than Tier A, it is harder to be WRONG about, and
each control here earns its tier for a different reason:

* a `kind:"direct"` source is a fetch target -- the playbook rung enqueues
  `fetch.url` for every one -- so a mistake leaves our machine and lands on
  somebody's server. Refused when the host is on the ruled list.
* `skip_backfill` refuses a whole corpus folder, and a refusal nobody can
  account for later reads as a manufacturer that simply never files anything.
* `extract_hints` is the only playbook value an LLM ever sees, as a second
  system block under a 263-character preamble telling the model to ignore it
  where the document disagrees. A hint that dwarfs that preamble drowns it.
* `bc_codes` is identity, foreign-keyed to `vendor_master`, and lives in its
  own table -- so it is a picker on its own route, not a body field.

The fold tests need no database: folding a form into a body is a pure
function, and that is where the data-loss bug would be.
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


class _Form(dict):
    """Starlette's FormData surface, as much of it as the fold uses."""

    def getlist(self, key):
        v = self.get(key)
        return list(v) if isinstance(v, list) else ([v] if v is not None else [])


# --------------------------------------------------------------------------- #
# direct sources
# --------------------------------------------------------------------------- #
def test_direct_sources_are_rebuilt_from_the_form():
    body = {"doc_sources": [
        {"doc_type": "IFU", "kind": "portal", "url": "https://x.example/list"},
        {"doc_type": "DoC", "kind": "direct", "url": "https://x.example/old.pdf"},
    ]}
    form = _Form({
        "direct_count": "2",
        "direct.0.url": "https://x.example/new.pdf", "direct.0.doc_type": "DoC",
        "direct.0.note": "robots ALLOW 2026-08-27",
        "direct.1.url": "", "direct.1.doc_type": "IFU",
    })

    out = registry.tier_b_from_form(body, form)

    assert out["doc_sources"] == [
        {"doc_type": "IFU", "kind": "portal", "url": "https://x.example/list"},
        {"doc_type": "DoC", "kind": "direct", "url": "https://x.example/new.pdf",
         "note": "robots ALLOW 2026-08-27"},
    ]


def test_a_cleared_direct_url_removes_the_row():
    body = {"doc_sources": [
        {"doc_type": "DoC", "kind": "direct", "url": "https://x.example/a.pdf"}]}
    form = _Form({"direct_count": "1", "direct.0.url": "  "})

    assert "doc_sources" not in registry.tier_b_from_form(body, form)


def test_portal_sources_survive_a_direct_edit():
    """The mirror of Tier A's guard. Each fold owns its half of the list and
    carries the other through; a fold that rebuilt the whole list from its own
    rows would delete the other kind and report success."""
    body = {"doc_sources": [
        {"doc_type": "IFU", "kind": "portal", "url": "https://x.example/list"}]}
    form = _Form({"direct_count": "1",
                  "direct.0.url": "https://x.example/a.pdf",
                  "direct.0.doc_type": "DoC"})

    out = registry.tier_b_from_form(body, form)

    assert [s["kind"] for s in out["doc_sources"]] == ["portal", "direct"]


def test_tier_b_leaves_tier_c_keys_alone():
    """Komet's parse template and a `ref_normalize` rule are Tier C. A save
    that dropped them would look exactly like a working editor."""
    body = {"ref_strategy": "companion", "ref_normalize": {"strip": "-"},
            "match": {"anchors": ["REF"]}}

    out = registry.tier_b_from_form(body, _Form({}))

    assert out["ref_strategy"] == "companion"
    assert out["ref_normalize"] == {"strip": "-"}
    assert out["match"] == {"anchors": ["REF"]}


# --------------------------------------------------------------------------- #
# skip_backfill
# --------------------------------------------------------------------------- #
def test_ticking_skip_backfill_carries_the_reason():
    form = _Form({"skip_backfill": "1",
                  "skip_backfill_reason": "client said the folder is a mess"})

    out = registry.tier_b_from_form({}, form)

    assert out["skip_backfill"] == {"reason": "client said the folder is a mess"}


def test_unticking_skip_backfill_removes_it():
    body = {"skip_backfill": {"reason": "old"}}

    assert "skip_backfill" not in registry.tier_b_from_form(body, _Form({}))


def test_a_ticked_box_with_no_reason_is_refused_at_parse():
    """Not defaulted to an empty string: unticked and ticked-without-a-reason
    are different outcomes and the second is an error. `_parse` is where the
    rule lives, so the CLI and the seed refuse it identically."""
    out = registry.tier_b_from_form({}, _Form({"skip_backfill": "1",
                                               "skip_backfill_reason": "   "}))

    with pytest.raises(ValueError, match="non-empty reason"):
        pb._parse("x", dict(out, manufacturer="X"))


# --------------------------------------------------------------------------- #
# extract_hints
# --------------------------------------------------------------------------- #
def test_hints_are_collected_and_blanks_dropped():
    form = _Form({"hint.type": "the header says Konformitätserklärung",
                  "hint.validity_from": "   ",
                  "hint.general": "dates are dd.mm.yyyy"})

    out = registry.tier_b_from_form({}, form)

    assert out["extract_hints"] == {
        "type": "the header says Konformitätserklärung",
        "general": "dates are dd.mm.yyyy",
    }


def test_clearing_every_hint_removes_the_key():
    body = {"extract_hints": {"general": "old"}}

    assert "extract_hints" not in registry.tier_b_from_form(
        body, _Form({"hint.general": ""}))


def test_a_hint_longer_than_the_cap_is_refused():
    """Anchored on `_HINT_OVERRIDE`, the 263-character sentence that makes the
    block safe to send at all. A hint may be about as long as the sentence that
    neutralises it, never a wall of text under it."""
    long_hint = "x" * (pb.HINT_MAX_CHARS + 1)

    with pytest.raises(ValueError, match="capped at"):
        pb._parse("x", {"manufacturer": "X",
                        "extract_hints": {"general": long_hint}})


def test_many_short_hints_can_still_exceed_the_playbook_cap():
    """The per-value cap alone is not enough: eleven fields times the per-value
    cap is a system block several times the prompt it sits under, billed on
    every extraction for this manufacturer."""
    from app.extract.tiers import TARGET

    hints = {k: "y" * pb.HINT_MAX_CHARS for k in list(TARGET)[:8]}
    assert sum(len(v) for v in hints.values()) > pb.HINT_MAX_TOTAL

    with pytest.raises(ValueError, match="exceeds the"):
        pb._parse("x", {"manufacturer": "X", "extract_hints": hints})


def test_a_hint_at_exactly_the_cap_is_accepted():
    parsed = pb._parse("x", {"manufacturer": "X",
                             "extract_hints": {"general": "z" * pb.HINT_MAX_CHARS}})

    assert len(parsed.extract_hints["general"]) == pb.HINT_MAX_CHARS


def test_the_cap_leaves_room_for_the_safety_preamble_to_dominate():
    """The number is not arbitrary, and this pins the relation rather than the
    literal: one hint must not be able to out-talk the override sentence."""
    from app.handlers.extract import _HINT_OVERRIDE

    assert pb.HINT_MAX_CHARS >= len(_HINT_OVERRIDE) - 50
    assert pb.HINT_MAX_CHARS <= len(_HINT_OVERRIDE) + 50


# --------------------------------------------------------------------------- #
# bc_codes -- identity, its own route, needs a database
# --------------------------------------------------------------------------- #
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
    (tmp_path / "alpha.json").write_text(json.dumps({
        "manufacturer": "ALPHA GMBH",
        "bc_codes": [{"code": "001"}],
        "domains": ["alpha.example"],
    }))
    (tmp_path / "beta.json").write_text(json.dumps({
        "manufacturer": "BETA AG",
        "bc_codes": [{"code": "002"}],
    }))
    for code, name in (("001", "ALPHA GMBH"), ("002", "BETA AG"),
                       ("003", "UNCLAIMED BRAND")):
        conn.execute(
            "INSERT INTO vendor_master (code_source, code, name, import_batch) "
            "VALUES ('LJ',%s,%s,'tier-b')", (code, name))
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, catalogue, "
        "  updated_at) VALUES ('I3','an item','003','LJ',now())")
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


def _codes(conn, slug):
    return sorted(r["code"] for r in conn.execute(
        "SELECT c.code FROM manufacturer_bc_code c JOIN manufacturer m "
        "ON m.id = c.manufacturer_id WHERE m.slug=%s", (slug,)).fetchall())


def test_the_picker_offers_unclaimed_codes_with_their_item_counts(conn, seeded):
    offered = registry.claimable_codes(conn, "alpha")

    by_code = {c["code"]: c for c in offered}
    assert "003" in by_code and by_code["003"]["items"] == 1
    assert by_code["003"]["name"] == "UNCLAIMED BRAND"


def test_the_picker_never_offers_a_code_another_playbook_claims(conn, seeded):
    """One code, one playbook -- `validate` refuses the pair on the next load,
    so offering it would be offering a save that cannot succeed."""
    offered = {c["code"] for c in registry.claimable_codes(conn, "alpha")}

    assert "002" not in offered


def test_the_picker_never_offers_a_code_this_playbook_already_has(conn, seeded):
    offered = {c["code"] for c in registry.claimable_codes(conn, "alpha")}

    assert "001" not in offered


def test_claiming_a_code_attaches_it_and_marks_it_playbook_derived(conn, seeded):
    out = registry.claim_bc_code(conn, slug="alpha", code="003", user=USER,
                                 confirm=True)

    assert out["items"] == 1 and out["vendor_name"] == "UNCLAIMED BRAND"
    assert _codes(conn, "alpha") == ["001", "003"]
    assert conn.execute(
        "SELECT source FROM manufacturer_bc_code WHERE code='003'"
    ).fetchone()["source"] == "playbook"


def test_claiming_a_code_another_playbook_holds_is_refused_naming_it(conn, seeded):
    with pytest.raises(registry.SaveRefused) as exc:
        registry.claim_bc_code(conn, slug="alpha", code="002", user=USER, confirm=True)

    assert "beta" in str(exc.value) and "One code, one playbook" in str(exc.value)


def test_a_code_the_vendor_master_does_not_issue_is_refused(conn, seeded):
    """The FK already makes this impossible; saying so beats a constraint
    error, and it is the same refusal `reconcile`'s `unknown-code` reports."""
    with pytest.raises(registry.SaveRefused) as exc:
        registry.claim_bc_code(conn, slug="alpha", code="999", user=USER, confirm=True)

    assert "vendor master" in str(exc.value)


def test_claiming_the_same_code_twice_is_refused(conn, seeded):
    with pytest.raises(registry.SaveRefused, match="already claims"):
        registry.claim_bc_code(conn, slug="alpha", code="001", user=USER, confirm=True)


def test_claiming_records_who_did_it(conn, seeded):
    registry.claim_bc_code(conn, slug="alpha", code="003", user=USER, confirm=True)

    assert conn.execute(
        "SELECT updated_by FROM manufacturer WHERE slug='alpha'"
    ).fetchone()["updated_by"] == USER


def test_route_claims_and_names_the_sync_that_must_follow(conn, seeded, client):
    r = client.post("/playbooks/alpha/codes", data={"code": "003", "confirm": "1"})

    assert r.status_code == 200
    assert "003" in r.text and "playbooks sync" in r.text
    assert "orphaned_groups" in r.text
    assert _codes(conn, "alpha") == ["001", "003"]


def test_route_refusal_is_422(conn, seeded, client):
    r = client.post("/playbooks/alpha/codes", data={"code": "002"})

    assert r.status_code == 422
    assert _codes(conn, "alpha") == ["001"]


def test_each_offered_code_carries_what_the_filter_box_searches(
        conn, seeded, client, monkeypatch):
    """Denis, 2026-09-04: the picker must be searchable by name and by code.

    The filter box in base.html matches `data-search` when an option has one.
    The item COUNT is in the visible label and deliberately NOT in the
    attribute: without it, searching "94" returned the supplier that happens to
    hold 94 items rather than code 10194 (measured against the live picker,
    2026-09-04).
    """
    monkeypatch.setattr(pb, "PLAYBOOKS_DIR", seeded)

    r = client.get("/playbooks/alpha")

    assert r.status_code == 200
    assert 'data-search="003 UNCLAIMED BRAND"' in r.text
    # the count still shows -- it is the reason to pick one code over another
    assert "1 item" in r.text


# --------------------------------------------------------------------------- #
# the refused-host ruling reaches the editor
# --------------------------------------------------------------------------- #
def test_a_direct_source_on_a_refused_host_is_422_quoting_the_ruling(
        conn, seeded, monkeypatch):
    """`validate` already raises `RobotsRefused` for this and phrases it for a
    human; the save maps it to 422 verbatim rather than rewording it, so the
    UI and the CLI do not grow two vocabularies for one refusal.

    The ruled list is an OPERATOR DECISION (2026-08-20: 5 hosts, 4
    manufacturers, 822 items), not what a host's robots.txt says -- COLTENE's
    reads as permitted for our agent and is on the list anyway. So this cannot
    be re-derived at fetch time; it has to refuse here.
    """
    monkeypatch.setattr(pb, "load_robots_refused",
                        lambda *a, **k: frozenset({"coltene.com"}))
    body = registry.tier_b_from_form({}, _Form({
        "direct_count": "1",
        "direct.0.url": "https://www.coltene.com/doc.pdf",
        "direct.0.doc_type": "DoC"}))

    with pytest.raises(registry.SaveRefused) as exc:
        registry.save_playbook_body(
            conn, slug="alpha", body=body, note="add a fetch target",
            user=USER, expected_rev=0, playbooks_dir=seeded)

    msg = str(exc.value)
    assert "coltene.com" in msg
    assert "not permitted to fetch" in msg
    assert 'kind:"portal"' in msg            # it names the way out
    conn.rollback()


def test_the_same_url_as_a_portal_is_accepted(conn, seeded, monkeypatch):
    """The refusal is of FETCHING the host, not of naming it: a portal is a
    link a human clicks, which is exactly what the ruling permits."""
    monkeypatch.setattr(pb, "load_robots_refused",
                        lambda *a, **k: frozenset({"coltene.com"}))
    body = registry.tier_a_from_form({}, _Form({
        "doc_source_count": "1",
        "doc_source.0.url": "https://www.coltene.com/doc.pdf",
        "doc_source.0.doc_type": "DoC"}))

    new_rev = registry.save_playbook_body(
        conn, slug="alpha", body=body, note="a human opens this one",
        user=USER, expected_rev=0, playbooks_dir=seeded)

    assert new_rev == 1


# --------------------------------------------------------------------------- #
# A code this playbook's OWN manufacturer holds only from the vendor master.
# `validate` counts only `source='playbook'` rows as claimed, so such a code
# raises BrandCollision on every save of every playbook -- and until 2026-09-11
# the picker hid it and `claim_bc_code` refused it as "already claims", so the
# escape the refusal names could not be taken. Live: futura-dental / 10044.
# --------------------------------------------------------------------------- #
def _hold_from_vendor_master(conn, slug, code):
    conn.execute(
        "INSERT INTO manufacturer_bc_code (code_source, code, manufacturer_id, source) "
        "SELECT 'LJ', %s, id, 'vendor-master' FROM manufacturer WHERE slug=%s "
        "ON CONFLICT (code_source, code) DO UPDATE "
        "SET manufacturer_id = excluded.manufacturer_id, source = 'vendor-master'",
        (code, slug))


def test_the_picker_offers_a_code_this_manufacturer_holds_only_from_the_vendor_master(
        conn, seeded):
    _hold_from_vendor_master(conn, "alpha", "003")

    offered = {c["code"]: c for c in registry.claimable_codes(conn, "alpha")}

    assert "003" in offered and offered["003"]["claimed_as"] == "vendor-master"


def test_claiming_promotes_this_manufacturers_own_vendor_master_code(conn, seeded):
    _hold_from_vendor_master(conn, "alpha", "003")

    out = registry.claim_bc_code(conn, slug="alpha", code="003", user=USER,
                                 confirm=True)

    assert out["moved_from"] is None
    assert conn.execute(
        "SELECT source FROM manufacturer_bc_code WHERE code='003'"
    ).fetchone()["source"] == "playbook"


def test_the_claim_confirm_for_its_own_vendor_master_code_names_no_other_owner(
        conn, seeded):
    # The two-step confirm reads `current_owner`; for a code this manufacturer
    # already holds from the vendor master it must not ask "move 003 from
    # ALPHA to ALPHA", so the owner is None and the question is "claim".
    _hold_from_vendor_master(conn, "alpha", "003")

    out = registry.claim_bc_code(conn, slug="alpha", code="003", user=USER,
                                 confirm=False)

    assert out["applied"] is False and out["current_owner"] is None
