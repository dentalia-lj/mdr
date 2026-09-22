"""P1b -- the smaller one-click writes get a confirm too.

Three shapes, all from the Task 0 contract or its "browser confirm" sibling:

* `/bc-push` says plainly whether sending is switched on, and moves its
  row-cap note to the top once there is more to hide than the cap shows --
  the note names the true number of DIFFERENCES, not the raw candidate pool,
  so it can never contradict the heading below it (fix round 1, finding I1).
* `/bc-push/apply` (the bulk Send) and `/playbooks/{slug}/codes` (Claim
  another BC code) are two-step: the first press prices what would happen and
  writes nothing, and only `confirm=1` acts -- same generic `_result.html`
  contract Task 0 gave Rename.
* SRN Confirm/Reject, Release, Take out of the queue, Update Business
  Central and Re-discover stay one-click, but now carry a real `hx-confirm`
  sentence naming what happens and to what, and `hx-boost="true"` -- the
  attribute that is what makes `hx-confirm` fire at all on a plain form.

Design: docs/superpowers/specs/2026-09-11-office-ui-redesign-design.md § 3 (P1b).
Fix round 1 (2026-09-11): controller review findings I1-I3, 4-11.
"""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app import manufacturer_seed
from app import playbooks as pb
from app.config import Web
from app.vendor_master import DEFAULT_CODE_SOURCE
from tests.conftest import TEST_API_URL as API_TEST_URL
from web import registry
from web.app import create_app


@pytest.fixture
def client(test_db_url, tmp_path):
    # `playbooks_dir=str(tmp_path)`: harmless for every test that never
    # writes a playbook file there (an empty directory is just an empty
    # playbook index), and it is what lets `two_playbooks` below write
    # `alpha.json` into this same directory and have `GET /playbooks/alpha`
    # find it -- the detail page reads playbooks from files or the database
    # depending on this key, never from the seed's own input directly
    # (`app/playbooks.py::load_playbooks`, `_SOURCE` unset in tests).
    return TestClient(create_app(Web(api_database_url=API_TEST_URL,
                                     imports_dir=str(tmp_path),
                                     playbooks_dir=str(tmp_path))))


def _jobs(conn):
    return conn.execute(
        "SELECT payload FROM job WHERE type='bc.push' ORDER BY id").fetchall()


def _seed_pushable_item(conn, suffix=None):
    """One item with a live group-scope declaration -- enough for `plan()` to
    offer it on `/bc-push` and for the item route to enqueue for it."""
    suffix = suffix or uuid.uuid4().hex[:12]
    item_ref = f"WS-{suffix}"
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, mfr_ref, "
        "md_flag, catalogue, updated_at) "
        "VALUES (%s,'Widget','t',%s,TRUE,'LJ',now())", (item_ref, f"R-{suffix}"))
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, validity_to, "
        "status, content_hash, archive_url, coverage_scope) "
        "VALUES ('DoC','MDR','2022-01-01','2031-01-01','production',%s,'/a.pdf',"
        "'group') RETURNING doc_id", (f"h-{suffix}",)).fetchone()["doc_id"]
    conn.execute(
        "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
        "VALUES (%s,%s,'ref-list','production')", (item_ref, doc_id))
    conn.commit()
    return item_ref


def _seed_due_sweep(conn, name):
    """A manufacturer with a trusted SRN and a sweep already due -- the state
    both `/manufacturers/sweep-due` and `/manufacturers/eudamed` render a
    Release button for. `trusted_manufacturer_srn` is a VIEW (migration 043)
    over `manufacturer_srn` where status IN ('auto','confirmed'); there is no
    table to insert into directly."""
    conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES (%s) "
        "ON CONFLICT (canonical_name) DO NOTHING", (name,))
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, status, "
        "  discovered_via) VALUES (%s,%s,'confirmed','register-exact')",
        (name, f"SRN-{name}"))
    conn.execute(
        "INSERT INTO eudamed_sweep_state (canonical_name, due_at) "
        "VALUES (%s, now() - interval '1 day')", (name,))
    conn.commit()


# --------------------------------------------------------------------------- #
# 1. the write-enabled banner
# --------------------------------------------------------------------------- #
def test_bc_push_says_sending_is_off_by_default(client):
    resp = client.get("/bc-push")

    assert "Sending to Business Central is switched off." in resp.text
    assert "switched on" not in resp.text


def test_bc_push_says_sending_is_on_when_the_config_key_is(
        test_db_url, tmp_path, monkeypatch):
    monkeypatch.setenv("BC_WRITE_ENABLED", "true")
    on_client = TestClient(create_app(Web(api_database_url=API_TEST_URL,
                                          imports_dir=str(tmp_path))))

    resp = on_client.get("/bc-push")

    assert "Sending to Business Central is switched on." in resp.text


# --------------------------------------------------------------------------- #
# 2. the row-cap note names the true differences, not the raw candidate pool
#    (fix round 1, finding I1)
# --------------------------------------------------------------------------- #
def test_row_cap_note_is_absent_under_the_limit(client, conn):
    _seed_pushable_item(conn)

    resp = client.get("/bc-push")

    assert "Showing the first" not in resp.text


def test_row_cap_note_counts_differences_not_candidates_and_agrees_with_the_heading(
        client, conn):
    """The review's own repro: a sample that resolves to fewer differences
    than candidates must not report the candidate count as though it were
    the difference count, and the heading, the row-cap note and the Send
    button must all name the SAME number of things that will actually move.

    1001 items are seeded with no `bc_push_log` row (every one a real, first-
    send difference) and 30 more are seeded ALREADY matching what `plan()`
    would compute for them (unchanged candidates) -- so the raw candidate
    pool is 1031 but the true difference count is 1001. The buggy version
    reported "the first 1.000 of 1.031 differences"; a fixed one must say
    1.001, matching `1001 to send` in the heading.
    """
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, validity_to, "
        "status, content_hash, archive_url, coverage_scope) "
        "VALUES ('DoC','MDR','2022-01-01','2031-01-01','production','h-rowcap',"
        "'/a.pdf','group') RETURNING doc_id").fetchone()["doc_id"]
    # 1001 real differences: no bc_push_log row, so every one is a first send.
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, mfr_ref, "
        "  md_flag, catalogue, updated_at) "
        "SELECT 'RC-' || g, 'Widget', 't', 'R-' || g, TRUE, 'LJ', now() "
        "  FROM generate_series(1, 1001) g")
    conn.execute(
        "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
        "SELECT 'RC-' || g, %s, 'ref-list', 'production' "
        "  FROM generate_series(1, 1001) g", (doc_id,))
    # 30 candidates that are NOT differences: bc_push_log already holds
    # exactly what `bc_fields.fields_for` would compute for them (a live
    # group-scope DoC, no EC cert, base_url="" and link_key="" -- the test
    # `Web` config's own defaults).
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, mfr_ref, "
        "  md_flag, catalogue, updated_at) "
        "SELECT 'UC-' || g, 'Widget', 't', 'R-uc-' || g, TRUE, 'LJ', now() "
        "  FROM generate_series(1, 30) g")
    conn.execute(
        "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
        "SELECT 'UC-' || g, %s, 'ref-list', 'production' "
        "  FROM generate_series(1, 30) g", (doc_id,))
    conn.execute(
        "INSERT INTO bc_push_log (item_ref, field, new_value, http_status) "
        "SELECT 'UC-' || g, 'pteValidDeclarationOfConformity', 'true', 200 "
        "  FROM generate_series(1, 30) g")
    conn.execute(
        "INSERT INTO bc_push_log (item_ref, field, new_value, http_status) "
        "SELECT 'UC-' || g, 'pteValidCECertificate', 'false', 200 "
        "  FROM generate_series(1, 30) g")
    conn.execute(
        "INSERT INTO bc_push_log (item_ref, field, new_value, http_status) "
        "SELECT 'UC-' || g, 'pteWarehouseURL', '/item/UC-' || g || '?k=', 200 "
        "  FROM generate_series(1, 30) g")
    conn.commit()

    resp = client.get("/bc-push")

    note = "Showing the first 1,000 of 1,001 differences."
    assert note in resp.text, resp.text[:2000]
    assert "1001 to send" in resp.text
    assert "30 already match" in resp.text
    # The button must offer exactly what Apply will actually enqueue: the
    # capped DISPLAY list (1000), never the true total (1001) it cannot all
    # send in one press.
    assert "Send 1000 to Business Central" in resp.text
    assert resp.text.index(note) < resp.text.index("<table>")


# --------------------------------------------------------------------------- #
# 3. bulk Send, two-step
# --------------------------------------------------------------------------- #
def test_bc_push_apply_first_post_prices_it_and_enqueues_nothing(client, conn):
    _seed_pushable_item(conn)

    resp = client.post("/bc-push/apply")

    assert resp.status_code == 200
    assert "Send 1 article" in resp.text
    assert _jobs(conn) == []


def test_bc_push_apply_names_whether_sending_is_really_switched_on(client, conn):
    _seed_pushable_item(conn)

    resp = client.post("/bc-push/apply")

    assert "Sending is switched off: this queues the job, but nothing will " \
           "reach Business Central." in resp.text
    # Finding 5: no implied later delivery -- the old wording ("...until it
    # is switched on") read as a promise this state will eventually send.
    assert "until it is switched on" not in resp.text


def test_bc_push_apply_confirmed_enqueues_as_today(client, conn):
    item_ref = _seed_pushable_item(conn)

    resp = client.post("/bc-push/apply", data={"confirm": "1"})

    assert resp.status_code == 200
    jobs = _jobs(conn)
    assert [j["payload"]["item_refs"] for j in jobs] == [[item_ref]]


def test_bc_push_apply_receipt_says_nothing_is_sent_while_writes_are_off(
        client, conn):
    """Finding 5: `bc.push` withholds every value while `bc.write_enabled`
    is false (`app/handlers/bc_push.py`) -- the receipt must not promise a
    send this run cannot make."""
    _seed_pushable_item(conn)

    resp = client.post("/bc-push/apply", data={"confirm": "1"})

    assert "Sending is switched off, so nothing will reach Business " \
           "Central" in resp.text
    assert "The worker sends them." not in resp.text


def test_bc_push_apply_receipt_says_the_worker_sends_them_while_writes_are_on(
        test_db_url, tmp_path, monkeypatch, conn):
    monkeypatch.setenv("BC_WRITE_ENABLED", "true")
    on_client = TestClient(create_app(Web(api_database_url=API_TEST_URL,
                                          imports_dir=str(tmp_path))))
    _seed_pushable_item(conn)

    resp = on_client.post("/bc-push/apply", data={"confirm": "1"})

    assert "The worker sends them." in resp.text
    assert "switched off" not in resp.text


def test_bc_push_apply_with_nothing_to_send_answers_plainly_with_no_yes_button(
        client, conn):
    """Finding 10: a confirm over zero articles is a Yes button that can
    only ever do nothing -- the first press must say so and stop there."""
    resp = client.post("/bc-push/apply")

    assert resp.status_code == 200
    assert "Nothing to send" in resp.text
    assert "Yes, send them" not in resp.text
    # the whole point: no confirm form at all, nothing left to press
    assert "<form" not in resp.text


# --------------------------------------------------------------------------- #
# 4 & 5. claim another BC code -- the empty option, and its two-step confirm
# --------------------------------------------------------------------------- #
@pytest.fixture
def two_playbooks(conn, tmp_path):
    """`alpha`/`ALPHA GMBH`, with its own claimed BC code, plus one more
    vendor code ("MV1") that no playbook claims. `manufacturer_seed.seed`
    still gives every vendor-master code SOME owner -- an entity of its own,
    named after the vendor's own name, with `source='vendor-master'` rather
    than `'playbook'` (`app/manufacturer_seed.py`, `_link_code`). That is
    the "owner, but not yet exclusive" state `claimable_codes` offers and a
    claim moves out of -- no need to fabricate it by hand.
    """
    (tmp_path / "alpha.json").write_text(json.dumps({
        "manufacturer": "ALPHA GMBH",
        "bc_codes": [{"code": "AV1"}],
        "domains": ["alpha.example"],
    }))
    for code, name in (("AV1", "ALPHA GMBH"), ("MV1", "MOVEABLE BRAND")):
        conn.execute(
            "INSERT INTO vendor_master (code_source, code, name, import_batch) "
            "VALUES (%s,%s,%s,'write-safety')", (DEFAULT_CODE_SOURCE, code, name))
    conn.commit()

    original = pb.load_robots_refused
    pb.load_robots_refused = lambda *a, **k: frozenset()
    try:
        stats = manufacturer_seed.seed(conn, dir_path=tmp_path)
    finally:
        pb.load_robots_refused = original
    assert stats.clean, stats.conflicts
    conn.commit()


def test_claim_code_select_has_an_empty_first_option_and_is_required(
        client, two_playbooks):
    resp = client.get("/playbooks/alpha")

    assert '<select name="code" required>' in resp.text
    assert '<option value="" selected disabled>Choose a code…</option>' in resp.text


def test_claim_code_first_post_names_code_owner_target_and_the_sync_step(
        client, conn, two_playbooks):
    """Finding I2: the confirm must also carry the `dentalia playbooks sync`
    note -- today's success message already does, and the un-confirmed
    press must not withhold information the confirmed one would have given."""
    resp = client.post("/playbooks/alpha/codes", data={"code": "MV1"})

    assert resp.status_code == 200
    assert "Move MV1 from MOVEABLE BRAND to ALPHA GMBH?" in resp.text
    assert "dentalia playbooks sync" in resp.text
    assert "orphaned_groups" in resp.text
    assert conn.execute(
        "SELECT m.canonical_name FROM manufacturer_bc_code c "
        "JOIN manufacturer m ON m.id = c.manufacturer_id "
        "WHERE c.code_source=%s AND c.code='MV1'",
        (DEFAULT_CODE_SOURCE,)).fetchone()["canonical_name"] == "MOVEABLE BRAND"


def test_claim_code_confirmed_moves_it_as_today(client, conn, two_playbooks):
    resp = client.post("/playbooks/alpha/codes",
                       data={"code": "MV1", "confirm": "1"})

    assert resp.status_code == 200
    assert "alpha now claims BC code MV1" in resp.text
    row = conn.execute(
        "SELECT m.canonical_name, c.source FROM manufacturer_bc_code c "
        "JOIN manufacturer m ON m.id = c.manufacturer_id "
        "WHERE c.code_source=%s AND c.code='MV1'",
        (DEFAULT_CODE_SOURCE,)).fetchone()
    assert row["canonical_name"] == "ALPHA GMBH"
    assert row["source"] == "playbook"


# --------------------------------------------------------------------------- #
# 6. the six one-click buttons: exact sentence, and hx-boost="true"
#    (fix round 1, finding 8 -- was count("hx-confirm=") >= 2)
# --------------------------------------------------------------------------- #
def test_srn_confirm_button_carries_its_exact_sentence_and_is_boosted(
        conn, client):
    conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES ('SRNCO') "
        "ON CONFLICT (canonical_name) DO NOTHING")
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, actor_name, "
        "  status, discovered_via) VALUES ('SRNCO','SRN-1','SRNCO EU',"
        "  'pending','register-fuzzy')")
    conn.commit()

    resp = client.get("/manufacturers/srn-queue")

    # One sentence, no leftover jargon ("...so its certificates become
    # sweepable") from the pre-fix-round wording -- checked as an exact
    # match on the attribute itself, not a page-wide word ban: the same
    # word still appears, correctly, in the row's own `title` tooltip.
    assert 'hx-confirm="Confirm this is SRNCO in EUDAMED?"' in resp.text
    # the Confirm form -- the one carrying this exact hx-confirm -- is boosted
    form_start = resp.text.index('hx-confirm="Confirm this is SRNCO in EUDAMED?"')
    form_open = resp.text.rindex("<form", 0, form_start)
    assert 'hx-boost="true"' in resp.text[form_open:form_start]


def test_srn_reject_button_carries_its_exact_sentence_and_is_boosted(
        conn, client):
    conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES ('SRNCO2') "
        "ON CONFLICT (canonical_name) DO NOTHING")
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, actor_name, "
        "  status, discovered_via) VALUES ('SRNCO2','SRN-9','SRNCO2 EU',"
        "  'pending','register-fuzzy')")
    conn.commit()

    resp = client.get("/manufacturers/srn-queue")

    # One sentence, no leftover "This is sticky and stays rejected" from the
    # pre-fix-round wording -- checked as an exact match on the attribute,
    # not a page-wide word ban: the intro prose still uses "sticky" to
    # explain the RULE, which is a different sentence in a different place.
    assert 'hx-confirm="Record that this SRN is not SRNCO2?"' in resp.text
    form_start = resp.text.index('hx-confirm="Record that this SRN is not SRNCO2?"')
    form_open = resp.text.rindex("<form", 0, form_start)
    assert 'hx-boost="true"' in resp.text[form_open:form_start]


def test_sweep_due_release_button_carries_its_exact_sentence_and_is_boosted(
        conn, client):
    _seed_due_sweep(conn, "SWEEPCO")

    resp = client.get("/manufacturers/sweep-due")

    needle = 'hx-confirm="Start the EUDAMED check for SWEEPCO now?"'
    assert needle in resp.text
    form_start = resp.text.index(needle)
    form_open = resp.text.rindex("<form", 0, form_start)
    assert 'hx-boost="true"' in resp.text[form_open:form_start]


def test_eudamed_page_release_button_carries_its_exact_sentence_and_is_boosted(
        conn, client):
    """The EUDAMED digest renders its OWN Release markup (`eudamed.html`),
    not the `sweep_due` macro -- the two must not drift apart."""
    _seed_due_sweep(conn, "SWEEPCO2")

    resp = client.get("/manufacturers/eudamed")

    needle = 'hx-confirm="Start the EUDAMED check for SWEEPCO2 now?"'
    assert needle in resp.text
    form_start = resp.text.index(needle)
    form_open = resp.text.rindex("<form", 0, form_start)
    assert 'hx-boost="true"' in resp.text[form_open:form_start]


def test_onboarding_skip_button_carries_its_exact_sentence_and_is_boosted(
        client, conn):
    conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES ('SKIPCO') "
        "ON CONFLICT (canonical_name) DO NOTHING")
    conn.commit()

    resp = client.get("/onboarding/SKIPCO/start")

    needle = ('hx-confirm="Take SKIPCO out of the onboarding queue with '
              'this reason?"')
    assert needle in resp.text
    form_start = resp.text.index(needle)
    form_open = resp.text.rindex("<form", 0, form_start)
    assert 'hx-boost="true"' in resp.text[form_open:form_start]


def test_item_detail_rediscover_button_carries_its_exact_sentence_and_is_boosted(
        client, conn):
    item_ref = _seed_pushable_item(conn)
    name = conn.execute(
        "SELECT name FROM item_mirror WHERE item_ref=%s", (item_ref,)
    ).fetchone()["name"]

    resp = client.get(f"/items/{item_ref}")

    # Finding 7: the item's NAME first, then its ref.
    needle = (f'hx-confirm="Search again for {name} ({item_ref})\'s '
              f'documents now, even if it was searched recently?"')
    assert needle in resp.text
    form_start = resp.text.index(needle)
    form_open = resp.text.rindex("<form", 0, form_start)
    assert 'hx-boost="true"' in resp.text[form_open:form_start]


def test_item_detail_update_bc_button_carries_its_exact_sentence_and_is_boosted(
        client, conn):
    item_ref = _seed_pushable_item(conn)
    name = conn.execute(
        "SELECT name FROM item_mirror WHERE item_ref=%s", (item_ref,)
    ).fetchone()["name"]

    resp = client.get(f"/items/{item_ref}")

    # Finding 7: name first, then ref, and never claims "to send" -- while
    # writes are off (the test default) that would be false.
    needle = (f'hx-confirm="Queue a Business Central update for {name} '
              f'({item_ref}) now?"')
    assert needle in resp.text
    assert "to send" not in resp.text
    form_start = resp.text.index(needle)
    form_open = resp.text.rindex("<form", 0, form_start)
    assert 'hx-boost="true"' in resp.text[form_open:form_start]
