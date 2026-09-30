"""Printed names without a manufacturer (app/unmatched_names.py) and the
`revalidate --name` command (app/revalidate_name.py).

Spec: docs/superpowers/specs/2026-09-29-unmatched-manufacturer-names-design.md.
"""
from __future__ import annotations

import uuid

import pytest
from psycopg.types.json import Json

from app import playbooks, revalidate_name, unmatched_names as un
from app.extract.tiers import write_extraction_attempt


# --- seeding --------------------------------------------------------------------

def _mfr(conn, name, codes=(), slug=None):
    mid = conn.execute(
        "INSERT INTO manufacturer (canonical_name, slug, body) VALUES (%s,%s,%s) RETURNING id",
        (name, slug, "{}" if slug else None)).fetchone()["id"]
    for c in codes:
        conn.execute("INSERT INTO vendor_master (code_source, code, name, import_batch) "
                     "VALUES ('LJ',%s,%s,'t') ON CONFLICT DO NOTHING", (c, name))
        conn.execute("INSERT INTO manufacturer_bc_code (code_source, code, manufacturer_id, source) "
                     "VALUES ('LJ',%s,%s,'vendor-master')", (c, mid))
        conn.execute("INSERT INTO manufacturer_alias (raw_name, canonical_name, source) "
                     "VALUES (%s,%s,'vendor-master') ON CONFLICT DO NOTHING", (c, name))
    return mid


def _alias(conn, raw, canonical):
    conn.execute("INSERT INTO manufacturer_alias (raw_name, canonical_name, source) "
                 "VALUES (%s,%s,'playbook')", (raw, canonical))


def _items(conn, manufacturer, code, refs):
    gid = conn.execute("INSERT INTO item_group (canonical_manufacturer) VALUES (%s) "
                       "RETURNING group_id", (manufacturer,)).fetchone()["group_id"]
    for i, ref in enumerate(refs):
        item = f"{code}-{i}-{uuid.uuid4().hex[:6]}"
        conn.execute(
            "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, mfr_ref, md_flag, "
            "catalogue, updated_at) VALUES (%s,%s,%s,%s,true,'LJ',now())",
            (item, f"item {ref}", code, ref))
        conn.execute("INSERT INTO item_group_member (group_id, item_ref, mfr_ref, match_basis) "
                     "VALUES (%s,%s,%s,'singleton')", (gid, item, ref))
    return gid


def _doc(conn, printed, *, refs=(), status="staged", unresolved=True, task=True,
         job=True, group_id=None, doc_type="DoC"):
    h = uuid.uuid4().hex
    fields = {"type": {"value": doc_type, "conf": 0.9, "tier": "T1", "verbatim": "x", "page": 1},
              "manufacturer": {"value": printed, "conf": 0.9, "tier": "T1",
                               "verbatim": printed, "page": 1}}
    if refs:
        fields["ref_list"] = {"value": list(refs), "conf": 0.9, "tier": "T1",
                              "verbatim": "x", "page": 1}
    write_extraction_attempt(conn, h, ["T0", "T1"], fields)
    did = conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, archive_url, status) "
        "VALUES (%s,'MDR','group',%s,%s,%s) RETURNING doc_id",
        (doc_type, h, f"/archive/X/{h}.pdf", status)).fetchone()["doc_id"]
    flags = ["manufacturer-unresolved"] if unresolved else []
    if job:
        conn.execute(
            "INSERT INTO job (type, payload, dedupe_key, status, result) "
            "VALUES ('validate.doc', %s, %s, 'done', %s)",
            (Json({"content_hash": h, "group_id": group_id, "extract_rev": 1,
                   "archive_url": f"/archive/X/{h}.pdf"}),
             f"validate:{h}:1:{group_id}:{uuid.uuid4().hex}", Json({"flags": flags})))
    if task:
        conn.execute(
            "INSERT INTO manual_task (kind, doc_id, payload, status) "
            "VALUES ('gate-manual', %s, %s, 'open')",
            (did, Json({"flags": flags, "tier_attempts": fields})))
    return did


# --- eligible_refs matches VALIDATE's guard ----------------------------------------

@pytest.mark.parametrize("refs", [
    ["767171", "76", "UKINJENO!", "  760677AN ", None, "", "100"],
    [], None, ["123456"],
])
def test_eligible_refs_is_validates_guard(refs):
    from app.handlers.validate import _eligible_unscoped_refs
    assert un.eligible_refs(refs, 6) == _eligible_unscoped_refs(refs, 6)


# --- grouping ------------------------------------------------------------------------

def test_spelling_variants_of_one_name_group_together(conn):
    _doc(conn, "botiss biomaterials GmbH")
    _doc(conn, "BOTISS  Biomaterials GmbH")
    _doc(conn, "Someone Else Ltd")
    found = un.find(conn, min_ref_len=6).groups
    assert [(g.printed.casefold().split()[0], g.waiting) for g in found] == [
        ("botiss", 2), ("someone", 1)]


def test_only_documents_on_review_whose_latest_validation_flags_count(conn):
    _doc(conn, "Acme GmbH")
    _doc(conn, "Acme GmbH", status="filed")
    _doc(conn, "Acme GmbH", task=False)               # not on Review
    _doc(conn, "Acme GmbH", unresolved=False)
    _doc(conn, "Acme GmbH", status="production")
    _doc(conn, "Acme GmbH", group_id=7)
    [g] = un.find(conn, min_ref_len=6).groups
    assert g.waiting == 2


def test_a_resolved_revalidation_clears_the_row_though_the_old_task_stays_open(conn):
    """GATE never refreshes an open task's payload (`gate._push_manual`), so the
    card must read the latest validation, not the task."""
    did = _doc(conn, "Acme GmbH")
    h = conn.execute("SELECT content_hash FROM document WHERE doc_id=%s", (did,)).fetchone()["content_hash"]
    conn.execute(
        "INSERT INTO job (type, payload, dedupe_key, status, result) "
        "VALUES ('validate.doc', %s, 'later', 'done', %s)",
        (Json({"content_hash": h, "group_id": None, "extract_rev": 1}),
         Json({"flags": ["no-item-identifier"]})))
    assert conn.execute("SELECT payload->'flags' AS f FROM manual_task WHERE doc_id=%s",
                        (did,)).fetchone()["f"] == ["manufacturer-unresolved"]
    assert un.find(conn, min_ref_len=6).groups == []


def test_a_name_that_folds_to_nothing_is_counted_not_listed(conn):
    _doc(conn, "   ")
    found = un.find(conn, min_ref_len=6)
    assert found.groups == [] and found.unnamed == 1


def test_nothing_waiting_is_an_empty_list(conn):
    assert un.find(conn, min_ref_len=6).groups == []


# --- suggestion levels ----------------------------------------------------------------

def test_article_numbers_suggest_the_manufacturer_holding_them(conn):
    _mfr(conn, "BOTISS", ["10171"], slug="botiss")
    _items(conn, "BOTISS", "10171", ["BT1001", "BT1002", "BT1003"])
    _doc(conn, "botiss biomaterials GmbH", refs=["BT1001", "BT1002", "BT1003", "ZZ9999"])
    [g] = un.find(conn, min_ref_len=6).groups
    s = g.suggestion
    assert (s.level, s.manufacturer, s.slug) == ("refs", "BOTISS", "botiss")
    assert (s.held, s.matched, s.listed) == (3, 3, 4)


def test_fewer_than_three_refs_is_not_article_number_evidence(conn):
    _mfr(conn, "BOTISS", ["10171"], slug="botiss")
    _items(conn, "BOTISS", "10171", ["BT1001", "BT1002"])
    _doc(conn, "Lambda SpA", refs=["BT1001", "BT1002"])
    [g] = un.find(conn, min_ref_len=6).groups
    assert g.suggestion.level == "none"


def test_refs_both_manufacturers_hold_are_nobodys_evidence(conn):
    """A REF two manufacturers carry counts for neither: with every REF shared,
    the alphabetically first used to win at 100 %."""
    _mfr(conn, "HENRY SCHEIN", ["814"])
    _mfr(conn, "IVOCLAR", ["001"])
    _items(conn, "HENRY SCHEIN", "814", ["SH1001", "SH1002", "SH1003"])
    _items(conn, "IVOCLAR", "001", ["SH1001", "SH1002", "SH1003"])
    _doc(conn, "Some Depot GmbH", refs=["SH1001", "SH1002", "SH1003"])
    [g] = un.find(conn, min_ref_len=6).groups
    s = g.suggestion
    assert (s.level, s.manufacturer, s.matched, s.shared) == ("none", None, 3, 3)


def test_mostly_uncatalogued_refs_say_so(conn):
    _mfr(conn, "BOTISS", ["10171"], slug="botiss")
    _items(conn, "BOTISS", "10171", ["BT1001", "BT1002", "BT1003"])
    listed = ["BT1001", "BT1002", "BT1003"] + [f"XX{i:04d}" for i in range(10)]
    _doc(conn, "Depot Ltd", refs=listed)
    [g] = un.find(conn, min_ref_len=6).groups
    assert g.suggestion.level == "refs" and g.suggestion.few_known


def test_refs_split_between_two_manufacturers_is_not_evidence(conn):
    _mfr(conn, "BOTISS", ["10171"])
    _mfr(conn, "VOCO", ["200"])
    _items(conn, "BOTISS", "10171", ["BT1001", "BT1002", "BT1003"])
    _items(conn, "VOCO", "200", ["VC2001", "VC2002"])
    _doc(conn, "Dental Depot GmbH", refs=["BT1001", "BT1002", "BT1003", "VC2001", "VC2002"])
    [g] = un.find(conn, min_ref_len=6).groups
    assert g.suggestion.level == "none"          # 3 of 5 = 60 %, under 80 %


def test_refs_across_several_documents_of_one_name_add_up(conn):
    _mfr(conn, "BOTISS", ["10171"])
    _items(conn, "BOTISS", "10171", ["BT1001", "BT1002", "BT1003"])
    _doc(conn, "botiss biomaterials GmbH", refs=["BT1001", "BT1002"])
    _doc(conn, "botiss biomaterials GmbH", refs=["BT1003"])
    [g] = un.find(conn, min_ref_len=6).groups
    assert (g.suggestion.level, g.suggestion.held) == ("refs", 3)


def test_a_brand_in_the_name_is_only_a_hint(conn):
    _mfr(conn, "HAGER", ["300"], slug="hager")
    _doc(conn, "Hager Werken GmbH")
    [g] = un.find(conn, min_ref_len=6).groups
    assert (g.suggestion.level, g.suggestion.manufacturer) == ("name", "HAGER")


def test_a_name_that_already_resolves_needs_revalidating_not_an_alias(conn):
    _mfr(conn, "NEODENT", ["500"], slug="neodent")
    _alias(conn, "JJGC Indústria S/A", "NEODENT")
    _doc(conn, "JJGC Indústria S/A")
    [g] = un.find(conn, min_ref_len=6).groups
    assert (g.suggestion.level, g.suggestion.manufacturer) == ("alias", "NEODENT")
    assert g.suggestion.has_items is False          # NEODENT has no item_group here
    assert un.summary(un.find(conn, min_ref_len=6)) == {
        "names": 1, "documents": 1, "by_refs": 0, "alias_exists": 0,
        "alias_no_items": 1, "unnamed": 0}
    text = "\n".join(revalidate_name.render(revalidate_name.plan(conn, "JJGC Indústria S/A")))
    assert "NEODENT has no products in the catalogue" in text


def test_an_alias_whose_manufacturer_has_products_says_revalidate(conn):
    _mfr(conn, "NEODENT", ["500"], slug="neodent")
    _items(conn, "NEODENT", "500", ["ND1001"])
    _alias(conn, "JJGC Indústria S/A", "NEODENT")
    _doc(conn, "JJGC Indústria S/A")
    [g] = un.find(conn, min_ref_len=6).groups
    assert (g.suggestion.level, g.suggestion.has_items) == ("alias", True)


def test_the_shell_command_quotes_the_printed_name(conn):
    _doc(conn, 'Bob "Quote" $HOME GmbH')
    [g] = un.find(conn, min_ref_len=6).groups
    assert g.shell_name == """'Bob "Quote" $HOME GmbH'"""


def test_no_evidence_is_no_match(conn):
    _doc(conn, "Henry Schein Inc.", refs=["NOPE123"])
    [g] = un.find(conn, min_ref_len=6).groups
    assert (g.suggestion.level, g.suggestion.manufacturer, g.suggestion.listed) == ("none", None, 1)


# --- brand guard folds accents ----------------------------------------------------------

def test_brand_guard_refuses_an_unaccented_spelling_of_an_accented_brand():
    brands = {"MÖLNLYCKE": frozenset({"044"})}
    assert playbooks.brand_collision("Molnlycke Health Care AB", brands, claimed=frozenset()) \
        == ("MÖLNLYCKE", ("044",))
    assert playbooks.brand_collision("Mölnlycke Health Care AB", brands, claimed=frozenset()) \
        == ("MÖLNLYCKE", ("044",))


# --- revalidate --name ----------------------------------------------------------------

def _pending_validates(conn):
    return conn.execute("SELECT payload, dedupe_key FROM job "
                        "WHERE type='validate.doc' AND status='pending'").fetchall()


def test_revalidate_refuses_a_name_with_no_alias(conn):
    _doc(conn, "botiss biomaterials GmbH")
    p = revalidate_name.plan(conn, "botiss biomaterials GmbH")
    assert p.refusal and "not an alias" in p.refusal
    with pytest.raises(ValueError):
        revalidate_name.apply(conn, p)


def test_revalidate_refuses_a_name_resolving_to_two_manufacturers(conn):
    _mfr(conn, "A", ["1"])
    _mfr(conn, "B", ["2"])
    _alias(conn, "Shared GmbH", "A")
    _alias(conn, "shared gmbh", "B")
    assert "2 manufacturers" in revalidate_name.plan(conn, "Shared GmbH").refusal


def test_revalidate_sends_only_untouched_unresolved_documents(conn):
    _mfr(conn, "BOTISS", ["10171"], slug="botiss")
    _alias(conn, "botiss biomaterials GmbH", "BOTISS")
    n = "botiss biomaterials GmbH"
    send1 = _doc(conn, n)
    send2 = _doc(conn, "BOTISS  Biomaterials GmbH", status="filed", task=False)
    _doc(conn, n, status="production")
    _doc(conn, n, status="rejected")
    _doc(conn, n, status="superseded")
    _doc(conn, n, job=False)
    _doc(conn, n, group_id=7)
    edited = _doc(conn, n)
    conn.execute("INSERT INTO evidence (doc_id, field, value, archive_url, verbatim, tier, "
                 "confidence, extracted_at) VALUES (%s,'validity_from','2025-01-01','/a',"
                 "'x','T3',1,now())", (edited,))
    reopened = _doc(conn, n)
    conn.execute("INSERT INTO audit_log (event, doc_id, decided_by, job_snapshot) "
                 "VALUES ('reopen', %s, 'natasa', '{}')", (reopened,))
    _doc(conn, n, unresolved=False)
    pending = _doc(conn, n)
    conn.execute("UPDATE job SET status='pending', result=NULL "
                 "WHERE payload->>'content_hash' = (SELECT content_hash FROM document WHERE doc_id=%s)",
                 (pending,))
    _doc(conn, "Someone Else GmbH")

    p = revalidate_name.plan(conn, n)

    assert p.refusal is None
    assert [r["doc_id"] for r in p.send] == sorted([send1, send2])
    assert p.left_alone == {"production": 1, "rejected": 1, "superseded": 1,
                            "no_validate_job": 1, "grouped": 1, "edited": 1,
                            "reopened": 1, "pending": 1, "resolved": 1}


def test_revalidate_dry_run_queues_nothing_and_apply_queues_the_plan(conn):
    _mfr(conn, "BOTISS", ["10171"])
    _alias(conn, "botiss biomaterials GmbH", "BOTISS")
    did = _doc(conn, "botiss biomaterials GmbH")
    p = revalidate_name.plan(conn, "botiss biomaterials GmbH")
    assert _pending_validates(conn) == []

    assert revalidate_name.apply(conn, p) == {"queued": 1, "already_queued": 0}
    [job] = _pending_validates(conn)
    h = conn.execute("SELECT content_hash FROM document WHERE doc_id=%s", (did,)).fetchone()["content_hash"]
    assert job["payload"] == {"content_hash": h, "group_id": None, "extract_rev": 1,
                              "archive_url": f"/archive/X/{h}.pdf"}
    assert job["dedupe_key"] == f"validate:{h}:1:None"

    assert revalidate_name.apply(conn, p) == {"queued": 0, "already_queued": 1}


def test_revalidate_render_says_what_it_would_do(conn):
    _mfr(conn, "BOTISS", ["10171"])
    _alias(conn, "botiss biomaterials GmbH", "BOTISS")
    _doc(conn, "botiss biomaterials GmbH")
    text = "\n".join(revalidate_name.render(revalidate_name.plan(conn, "botiss biomaterials GmbH")))
    assert "resolves to: BOTISS" in text and "Would send back through validation: 1" in text


def test_after_the_alias_validate_resolves_the_document_revalidate_sent(conn):
    """End to end: flagged before the alias, resolved on the job revalidate queues."""
    from app.handlers import validate as vh
    _mfr(conn, "BOTISS", ["10171"])
    _items(conn, "BOTISS", "10171", ["BT1001", "BT1002", "BT1003"])
    h = uuid.uuid4().hex
    ev = lambda v: {"value": v, "conf": 0.95, "tier": "T1", "verbatim": str(v), "page": 1,
                    "model_id": "m"}
    write_extraction_attempt(conn, h, ["T1"], {
        "type": ev("DoC"), "regulation": ev("MDR"), "coverage_scope": ev("group"),
        "manufacturer": ev("botiss biomaterials GmbH"), "ref_list": ev(["BT1001"])})
    payload = {"content_hash": h, "group_id": None, "extract_rev": 1,
               "archive_url": f"/archive/X/{h}.pdf"}
    before = vh.handle_validate_doc(conn, {"id": 1, "type": "validate.doc", "payload": payload})
    assert "manufacturer-unresolved" in before["flags"]
    conn.execute("INSERT INTO document (type, regulation, coverage_scope, content_hash, "
                 "archive_url, status) VALUES ('DoC','MDR','group',%s,%s,'staged')",
                 (h, payload["archive_url"]))
    conn.execute("INSERT INTO job (type, payload, dedupe_key, status, result) "
                 "VALUES ('validate.doc', %s, 'v-before', 'done', %s)",
                 (Json(payload), Json(before)))

    _alias(conn, "botiss biomaterials GmbH", "BOTISS")
    revalidate_name.apply(conn, revalidate_name.plan(conn, "botiss biomaterials GmbH"))
    [job] = _pending_validates(conn)
    after = vh.handle_validate_doc(conn, {"id": 2, "type": "validate.doc",
                                          "payload": job["payload"]})
    assert "manufacturer-unresolved" not in (after.get("flags") or [])
    assert after.get("emitted") is True

