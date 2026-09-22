"""validate.doc — full VALIDATE v3 rule set (S1.0).

C1 REF gate (incl. backfill manufacturer-scoping), C4 manufacturer-scope
pre-rule, [validate-itemid] no-item-identifier, date sanity, C7 never-downgrade,
C6 supersession (same coverage-subject/type/regulation; MDR/MDD parallel chains
recorded as `related`, never `superseded_by`), and rule 6 type/regulation
consistency (expiry on a DoC that references no certificate).

Tests don't commit (conn fixture rolls back on teardown), and read jobs within
the same tx (queue.enqueue writes are visible to the same connection).
"""

from __future__ import annotations

import pytest

from app.extract import tiers
from app.extract.tiers import write_extraction_attempt
from app.handlers import validate as vh


def _ev(value, conf=0.9):
    return {"value": value, "conf": conf, "tier": "T1", "verbatim": "x",
            "page": 1, "model_id": "m"}


def _fields(**overrides):
    base = {
        "type": _ev("DoC"),
        "regulation": _ev("MDR"),
        "coverage_scope": _ev("group"),
        "ref_list": _ev(["196.644.050"]),
        "basic_udi_di": _ev(None),
    }
    base.update(overrides)
    return base


def _seed(conn, content_hash, fields, extract_rev=1):
    write_extraction_attempt(conn, content_hash, ["T0", "T1"], fields, extract_rev=extract_rev)


def _gate_jobs(conn, content_hash):
    return conn.execute(
        "SELECT payload, dedupe_key FROM job WHERE type='gate.candidate' "
        "AND payload->>'content_hash' = %s ORDER BY id",
        (content_hash,),
    ).fetchall()


# --- group/registry fixtures ----------------------------------------------

def test_doc_citing_a_held_certificate_emits_cert_doc_id(conn):
    cert_id = conn.execute(
        "INSERT INTO document (type, regulation, cert_number, status, content_hash, "
        "archive_url, coverage_scope) VALUES ('EC','MDR','G1 082649 0002','production',"
        "'h-cert','/archive/c.pdf','group') RETURNING doc_id"
    ).fetchone()["doc_id"]
    _seed(conn, "h-doc", _fields(cert_number=_ev("G1 082649 0002"),
                                 referenced_docs=_ev(["G1 082649 0002"])))
    vh.handle_validate_doc(
        conn, {"id": 1, "payload": {"content_hash": "h-doc", "extract_rev": 1,
                                    "group_id": None}})
    payload = _payload(conn, "h-doc")
    assert payload["cert_doc_id"] == cert_id


def test_a_citation_without_the_revision_suffix_still_finds_the_certificate(conn):
    """Denis's ruling, 2026-08-19. Measured on the live registry that day: 80 of
    the 306 declarations cite `MDR 778483` while the certificate is filed as
    `MDR 778483 R000` (EC, production, GC EUROPE N.V., valid to 2030-04-16).
    Exact-string matching missed every one, which is why `cert_doc_id` was NULL
    on all 326 documents and the whole inheritance path had never executed once.

    A trailing R-code is a revision marker on the same certificate, so it is
    stripped from both sides before comparing. `Rev. NN` is deliberately NOT
    normalised: `Rev. 00` and `Rev. 01` may be a supersession rather than a
    spelling, which is a different question with a different answer."""
    cert_id = conn.execute(
        "INSERT INTO document (type, regulation, cert_number, status, content_hash, "
        "archive_url, coverage_scope) VALUES ('EC','MDR','MDR 778483 R000','production',"
        "'h-cert-rsuffix','/archive/c.pdf','group') RETURNING doc_id"
    ).fetchone()["doc_id"]
    _seed(conn, "h-doc-rsuffix", _fields(cert_number=_ev("MDR 778483"),
                                         referenced_docs=_ev(["MDR 778483"])))
    vh.handle_validate_doc(
        conn, {"id": 1, "payload": {"content_hash": "h-doc-rsuffix", "extract_rev": 1,
                                    "group_id": None}})
    payload = _payload(conn, "h-doc-rsuffix")
    assert payload["cert_doc_id"] == cert_id
    assert "cert-unresolved" not in payload["flags"]


def test_a_revision_number_is_not_normalised_away(conn):
    """The other half of the same ruling, asserted so nobody widens it later
    without deciding: a declaration citing `Rev. 00` does NOT resolve to the
    `Rev. 01` we hold. Those are candidates for a supersession relationship, and
    treating them as one certificate would assert that silently. 96 declarations
    on the live registry are in exactly this shape; all of them already carry
    their own expiry, so nothing is lost by refusing."""
    conn.execute(
        "INSERT INTO document (type, regulation, cert_number, status, content_hash, "
        "archive_url, coverage_scope) VALUES ('EC','MDR','G15 043306 0282 Rev. 01',"
        "'production','h-cert-rev01','/archive/c.pdf','group')"
    )
    _seed(conn, "h-doc-rev00", _fields(cert_number=_ev("G15 043306 0282 Rev. 00"),
                                       referenced_docs=_ev(["G15 043306 0282 Rev. 00"])))
    vh.handle_validate_doc(
        conn, {"id": 1, "payload": {"content_hash": "h-doc-rev00", "extract_rev": 1,
                                    "group_id": None}})
    assert _payload(conn, "h-doc-rev00")["cert_doc_id"] is None


def test_an_exact_certificate_match_wins_over_a_suffix_stripped_one(conn):
    """Both spellings can be in the registry at once. The exact hit is the one
    the declaration named, so it must be preferred rather than left to doc_id
    order."""
    conn.execute(
        "INSERT INTO document (type, regulation, cert_number, status, content_hash, "
        "archive_url, coverage_scope) VALUES ('EC','MDR','QS 111222 R007','production',"
        "'h-cert-loose','/archive/c.pdf','group')"
    )
    exact_id = conn.execute(
        "INSERT INTO document (type, regulation, cert_number, status, content_hash, "
        "archive_url, coverage_scope) VALUES ('EC','MDR','QS 111222','production',"
        "'h-cert-exact','/archive/c.pdf','group') RETURNING doc_id"
    ).fetchone()["doc_id"]
    _seed(conn, "h-doc-both", _fields(cert_number=_ev("QS 111222"),
                                      referenced_docs=_ev(["QS 111222"])))
    vh.handle_validate_doc(
        conn, {"id": 1, "payload": {"content_hash": "h-doc-both", "extract_rev": 1,
                                    "group_id": None}})
    assert _payload(conn, "h-doc-both")["cert_doc_id"] == exact_id


def test_doc_citing_an_unheld_certificate_resolves_to_null_and_does_not_fail(conn):
    _seed(conn, "h-doc2", _fields(cert_number=_ev("NOT-IN-REGISTRY-1"),
                                  referenced_docs=_ev(["NOT-IN-REGISTRY-1"])))
    vh.handle_validate_doc(
        conn, {"id": 1, "payload": {"content_hash": "h-doc2", "extract_rev": 1,
                                    "group_id": None}})
    payload = _payload(conn, "h-doc2")
    assert payload["cert_doc_id"] is None
    assert "cert-unresolved" in payload["flags"]


def test_an_unresolved_citation_reaches_the_anomaly_ledger_too(conn):
    """[results-anomaly-kinds] / [task4-cert-anomaly]. `cert_reference_unresolved`
    has sat in ANOMALY_KINDS since Task 3 with no producer, while VALIDATE
    appended only the FLAG `cert-unresolved`. They are not duplicates of each
    other: the flag is per-candidate and steers GATE's disposition
    (INFORMATIONAL_FLAGS, so it caps rather than blocks); the anomaly is a
    standing keyed row that answers "how often do we cite certificates we do not
    hold, and which ones" on /data-quality. One detection, two consumers -- and
    only one of them was being told."""
    _seed(conn, "h-doc-anom", _fields(cert_number=_ev("NOT-HELD-9"),
                                      referenced_docs=_ev(["NOT-HELD-9"])))
    out = vh.handle_validate_doc(
        conn, {"id": 1, "payload": {"content_hash": "h-doc-anom", "extract_rev": 1,
                                    "group_id": None}})
    assert "cert-unresolved" in _payload(conn, "h-doc-anom")["flags"]
    kinds = {(a["kind"], a["subject"]) for a in out["anomalies"]}
    assert ("cert_reference_unresolved", "h-doc-anom") in kinds, out["anomalies"]


def _seed_item(conn, item_ref, manufacturer="ACME", md_flag=True):
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, "
        "catalogue, mirror_rev, updated_at) "
        "VALUES (%s, 'n', %s, %s, 'LJ', 1, now()) ON CONFLICT (item_ref) DO NOTHING",
        (item_ref, manufacturer, md_flag),
    )


def _seed_group(conn, canonical_manufacturer="ACME", basic_udi_di=None, label=None):
    return conn.execute(
        "INSERT INTO item_group (canonical_manufacturer, basic_udi_di, label) "
        "VALUES (%s, %s, %s) RETURNING group_id",
        (canonical_manufacturer, basic_udi_di, label),
    ).fetchone()["group_id"]


def _seed_member(conn, group_id, item_ref, mfr_ref=None, match_basis="manual"):
    _seed_item(conn, item_ref)
    conn.execute(
        "INSERT INTO item_group_member (group_id, item_ref, mfr_ref, match_basis) "
        "VALUES (%s, %s, %s, %s)",
        (group_id, item_ref, mfr_ref, match_basis),
    )


def _seed_current_doc(conn, content_hash, item_refs, *, doc_type="DoC",
                       regulation="MDR", validity_from=None, coverage_scope="group",
                       status="production", link_status="production"):
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, coverage_scope, "
        "content_hash, archive_url, status) "
        "VALUES (%s, %s, %s::date, %s, %s, 'file:///current.pdf', %s::doc_status) "
        "RETURNING doc_id",
        (doc_type, regulation, validity_from, coverage_scope, content_hash, status),
    ).fetchone()["doc_id"]
    for item_ref in item_refs:
        conn.execute(
            "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
            "VALUES (%s, %s, 'ref-list', %s::link_status)",
            (item_ref, doc_id, link_status),
        )
    return doc_id


def _links(conn, content_hash):
    return _gate_jobs(conn, content_hash)[0]["payload"].get("links", [])


def _payload(conn, content_hash):
    return _gate_jobs(conn, content_hash)[0]["payload"]


# --- existing S0.3 stub tests (semantics carry over) -----------------------

def test_validate_emits_normal_gate_candidate(conn):
    # Full rule set: a real group with an overlapping mfr_ref now genuinely
    # passes C1 (the S0.3 stub always emitted ref_gate=False conservatively;
    # S1.0 supplies the real gate, so this legitimately flips to True).
    group_id = _seed_group(conn)
    _seed_member(conn, group_id, "IT-1", mfr_ref="196.644.050")
    _seed(conn, "vh-1", _fields())  # coverage_scope=group, ref_list=["196.644.050"]
    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-1", "extract_rev": 1, "group_id": group_id}})

    assert res["emitted"] is True and res["route"] == "normal"
    jobs = _gate_jobs(conn, "vh-1")
    assert len(jobs) == 1
    assert jobs[0]["dedupe_key"] == f"gate:vh-1:1:{group_id}"
    p = jobs[0]["payload"]
    assert p["ref_gate"] is True
    assert p["links"] == [{"item_ref": "IT-1", "match_basis": "ref-list"}]
    assert "route" not in p  # normal path is not a binding candidate


def test_validate_missing_required_evidence_suppresses(conn):
    fields = _fields()
    del fields["regulation"]  # a required field with no evidence
    _seed(conn, "vh-2", fields)

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-2", "extract_rev": 1, "group_id": 7}})

    assert res["emitted"] is False
    assert res["reason"] == "incomplete-evidence"
    assert "regulation" in res["missing"]
    assert _gate_jobs(conn, "vh-2") == []  # nothing emitted


def test_iso_certificate_with_na_regulation_reaches_the_binding_rule(conn):
    """An ISO/QMS certificate has no MDR/MDD regulation, so extraction emits
    `n.a.` (schema: MDR|MDD|n.a.). It must survive the completeness check and
    reach C4's manufacturer-binding route, which exists for exactly this
    document type -- it used to die twelve lines earlier as incomplete-evidence
    (3 of 61 corpus documents, Denis ruling 2026-08-11)."""
    # Built through the REAL tier parser, not hand-written evidence: the bug
    # was in llm._norm_value, so a test that seeds `fields` directly bypasses
    # the very code under test and passes either way.
    from app.extract import llm

    parsed = llm._fields_from_json(
        {"type": {"value": "ISO", "confidence": 0.99, "verbatim": "ISO 13485", "page": 1},
         "regulation": {"value": "n.a.", "confidence": 0.95,
                        "verbatim": "ISO 13485:2016", "page": 1},
         "coverage_scope": {"value": "manufacturer", "confidence": 0.99,
                            "verbatim": "QMS certificate", "page": 1}},
        "T1", "stub-model",
    )
    fields = _fields(**parsed, ref_list=_ev([]))
    assert fields["regulation"]["value"] == "n.a."   # the coercion is off for this field
    _seed(conn, "vh-iso-na", fields)

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-iso-na", "extract_rev": 1, "group_id": None}})

    assert res.get("reason") != "incomplete-evidence"
    assert res["emitted"] is True
    assert _gate_jobs(conn, "vh-iso-na") != []


def test_validate_null_valued_required_field_suppresses(conn):
    # a field present but with a null value (model "confidently absent") is not
    # evidence for a required field.
    _seed(conn, "vh-3", _fields(coverage_scope=_ev(None)))
    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-3", "extract_rev": 1, "group_id": 7}})
    assert res["emitted"] is False
    assert "coverage_scope" in res["missing"]


def test_validate_mfr_scope_routes_to_binding(conn):
    # manufacturer scope + no ref_list + no basic_udi_di -> binding flow (§7b, C4).
    _seed(conn, "vh-4", _fields(
        coverage_scope=_ev("manufacturer"),
        ref_list=_ev([]),
        basic_udi_di=_ev(None),
    ))
    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-4", "extract_rev": 1, "group_id": 7}})

    assert res["emitted"] is True and res["route"] == "mfr-binding"
    jobs = _gate_jobs(conn, "vh-4")
    assert len(jobs) == 1
    assert jobs[0]["payload"]["route"] == "mfr-binding"
    # group-independent dedupe: one entry regardless of requesting group (§7b)
    assert jobs[0]["dedupe_key"] == "gate:vh-4:1:mfr-binding"


def test_validate_mfr_binding_dedupes_across_groups(conn):
    _seed(conn, "vh-5", _fields(
        coverage_scope=_ev("manufacturer"), ref_list=_ev([]), basic_udi_di=_ev(None)))
    vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-5", "extract_rev": 1, "group_id": 11}})
    vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-5", "extract_rev": 1, "group_id": 22}})  # different group

    assert len(_gate_jobs(conn, "vh-5")) == 1  # exactly one candidate for N groups


# --- Case 1: C4 binding regression (table row 1) ---------------------------
# Same as test_validate_mfr_scope_routes_to_binding above — no separate test
# needed; the table's row 1 IS that existing test.


# --- Case 2: no-item-identifier ([validate-itemid]) ------------------------

def test_no_item_identifier_flags_and_routes_manual(conn):
    group_id = _seed_group(conn)
    _seed_member(conn, group_id, "IT-1", mfr_ref="SOME-REF")
    _seed(conn, "vh-noid", _fields(
        coverage_scope=_ev("group"), ref_list=_ev([]), basic_udi_di=_ev(None)))

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-noid", "extract_rev": 1, "group_id": group_id}})

    assert res["emitted"] is True
    p = _payload(conn, "vh-noid")
    assert "no-item-identifier" in p["flags"]
    assert p["ref_gate"] is False
    assert p["links"] == []


# --- a REF list that arrived at exactly the prompt cap ---------------------

def test_a_capped_llm_ref_list_rides_the_candidate_flagged_but_intact(conn):
    """Denis, 2026-08-13: the truncation logic must not discard what T0 read.

    The flag REPORTS a suspected truncation. The list is still used in full —
    every code it does name still matches its item — and the flag is
    informational, so nothing about the disposition changes."""
    from app.extract import tiers as tiers_mod

    codes = [f"R{i}" for i in range(tiers_mod.REF_LIST_PROMPT_CAP)]
    group_id = _seed_group(conn)
    # the LAST code in the capped list: if anything pruned the list, this link
    # is the first thing to disappear.
    _seed_member(conn, group_id, "IT-CAPPED", mfr_ref=codes[-1])
    _seed(conn, "vh-capped", _fields(ref_list=_ev(codes)))   # _ev is tier T1

    vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-capped", "extract_rev": 1, "group_id": group_id}})

    p = _payload(conn, "vh-capped")
    assert "ref-list-possibly-truncated" in p["flags"]
    assert p["ref_gate"] is True
    assert p["links"] == [{"item_ref": "IT-CAPPED", "match_basis": "ref-list"}]


def test_a_t0_ref_list_at_the_cap_reaches_gate_unflagged(conn):
    from app.extract import tiers as tiers_mod

    codes = [f"R{i}" for i in range(tiers_mod.REF_LIST_PROMPT_CAP)]
    group_id = _seed_group(conn)
    _seed_member(conn, group_id, "IT-T0", mfr_ref=codes[0])
    _seed(conn, "vh-t0cap", _fields(ref_list={**_ev(codes), "tier": "T0"}))

    vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-t0cap", "extract_rev": 1, "group_id": group_id}})

    assert "ref-list-possibly-truncated" not in _payload(conn, "vh-t0cap")["flags"]


# --- Case 3: C1 pass via REF overlap ---------------------------------------

def test_c1_pass_via_ref_overlap(conn):
    group_id = _seed_group(conn)
    _seed_member(conn, group_id, "IT-1", mfr_ref="613289")
    _seed_member(conn, group_id, "IT-2", mfr_ref="613290")  # no overlap
    _seed(conn, "vh-refoverlap", _fields(ref_list=_ev(["613289", "999999"])))

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-refoverlap", "extract_rev": 1, "group_id": group_id}})

    assert res["emitted"] is True
    p = _payload(conn, "vh-refoverlap")
    assert p["ref_gate"] is True
    assert p["links"] == [{"item_ref": "IT-1", "match_basis": "ref-list"}]


# --- Case 4: C1 pass via Basic UDI-DI --------------------------------------

def test_c1_pass_via_basic_udi_di(conn):
    group_id = _seed_group(conn, basic_udi_di="07640169310123")
    _seed_member(conn, group_id, "IT-1", mfr_ref="AAA")
    _seed_member(conn, group_id, "IT-2", mfr_ref="BBB")
    _seed(conn, "vh-udi", _fields(
        ref_list=_ev([]), basic_udi_di=_ev("07640169310123")))

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-udi", "extract_rev": 1, "group_id": group_id}})

    assert res["emitted"] is True
    p = _payload(conn, "vh-udi")
    assert p["ref_gate"] is True
    links = {l["item_ref"]: l for l in p["links"]}
    assert set(links) == {"IT-1", "IT-2"}
    assert all(l["match_basis"] == "basic-udi-di" for l in links.values())


# --- Case 5: C1 fail ---------------------------------------------------------

def test_c1_fail_no_overlap_falls_back_to_fetch_context(conn):
    # C3 (PRD §4, handbook §4): the doc carries item identifiers but none match
    # the requesting group — the only tie is the group's discovery context, so
    # every member gets a fetch-context link (staged-capped by C5, structurally).
    group_id = _seed_group(conn, basic_udi_di="07640169310123")
    _seed_member(conn, group_id, "IT-1", mfr_ref="AAA")
    _seed_member(conn, group_id, "IT-2", mfr_ref="BBB")
    _seed(conn, "vh-c1fail", _fields(
        ref_list=_ev(["ZZZ"]), basic_udi_di=_ev("DIFFERENT-UDI")))

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-c1fail", "extract_rev": 1, "group_id": group_id}})

    assert res["emitted"] is True
    p = _payload(conn, "vh-c1fail")
    assert p["ref_gate"] is False
    assert sorted(p["links"], key=lambda l: l["item_ref"]) == [
        {"item_ref": "IT-1", "match_basis": "fetch-context"},
        {"item_ref": "IT-2", "match_basis": "fetch-context"},
    ]


def test_no_item_identifier_gets_no_fetch_context_links(conn):
    # Rule 7: a non-mfr-scope doc with no identifiers at all "cannot be linked
    # to the catalogue" — blocking manual, and the fallback must NOT fire.
    group_id = _seed_group(conn)
    _seed_member(conn, group_id, "IT-1", mfr_ref="AAA")
    _seed(conn, "vh-noid-fc", _fields(ref_list=_ev([]), basic_udi_di=_ev(None)))

    vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-noid-fc", "extract_rev": 1, "group_id": group_id}})

    p = _payload(conn, "vh-noid-fc")
    assert "no-item-identifier" in p["flags"]
    assert p["links"] == []


def test_backfill_without_group_gets_no_fetch_context_links(conn):
    # fetch-context needs a requesting group by definition; the backfill path
    # (group_id=None) never fabricates one.
    _seed(conn, "vh-bf-fc", _fields(ref_list=_ev(["ZZZ"])))

    vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-bf-fc", "extract_rev": 1, "group_id": None}})

    p = _payload(conn, "vh-bf-fc")
    assert p["ref_gate"] is False
    assert p["links"] == []


# --- Case 6: Backfill scoped hit --------------------------------------------

def test_backfill_scoped_hit_resolves_group(conn):
    group_id = _seed_group(conn, canonical_manufacturer="ACME")
    _seed_member(conn, group_id, "IT-1", mfr_ref="REF-1")
    _seed(conn, "vh-bfscoped", _fields(
        ref_list=_ev(["REF-1"]), manufacturer=_ev("ACME")))

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-bfscoped", "extract_rev": 1, "group_id": None}})

    assert res["emitted"] is True
    jobs = _gate_jobs(conn, "vh-bfscoped")
    assert len(jobs) == 1
    # dedupe key keys on the RESOLVED group id, not the original None consumed
    # group_id (handbook §6 emit pseudo-code: dedupe_key=f"gate:{hash}:{rev}:{group.id}").
    assert jobs[0]["dedupe_key"] == f"gate:vh-bfscoped:1:{group_id}"
    p = jobs[0]["payload"]
    assert p["group_id"] == group_id  # resolved
    assert p["ref_gate"] is True
    assert p["links"] == [{"item_ref": "IT-1", "match_basis": "ref-list"}]
    assert "ref-unscoped" not in p["flags"]  # scoped path never mis-flags


# --- Case 6a-alias: Backfill scoped hit via manufacturer_alias --------------
# [validate-mfr-alias] (2026-08-10 backfill-matching plan, task 1):
# item_group.canonical_manufacturer holds a BC-vendor-code name; a backfill
# document self-identifies with the manufacturer's own spelling. These tests
# exercise the canonicalization that bridges the two.

def test_backfill_scoped_hit_resolves_group_via_manufacturer_alias(conn):
    # Realistic case: alias row maps the document-facing name to the
    # canonical name the group actually carries (mirrors the corpus finding:
    # 194 Ivoclar groups all carry canonical_manufacturer='IVOCLAR', but a
    # document says "Ivoclar Vivadent AG").
    conn.execute(
        "INSERT INTO manufacturer_alias (raw_name, canonical_name) "
        "VALUES ('Ivoclar Vivadent AG', 'IVOCLAR')"
    )
    group_id = _seed_group(conn, canonical_manufacturer="IVOCLAR")
    _seed_member(conn, group_id, "IT-IVO", mfr_ref="REF-IVO")
    _seed(conn, "vh-bfalias", _fields(
        ref_list=_ev(["REF-IVO"]), manufacturer=_ev("Ivoclar Vivadent AG")))

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-bfalias", "extract_rev": 1, "group_id": None}})

    assert res["emitted"] is True
    p = _payload(conn, "vh-bfalias")
    assert p["group_id"] == group_id  # resolved through the alias
    assert p["ref_gate"] is True
    assert p["links"] == [{"item_ref": "IT-IVO", "match_basis": "ref-list"}]
    # a resolved manufacturer must never carry the unresolved flag
    assert "manufacturer-unresolved" not in p["flags"]


def test_backfill_alias_lookup_is_case_and_whitespace_insensitive(conn):
    conn.execute(
        "INSERT INTO manufacturer_alias (raw_name, canonical_name) "
        "VALUES ('Ivoclar Vivadent AG', 'IVOCLAR')"
    )
    group_id = _seed_group(conn, canonical_manufacturer="IVOCLAR")
    _seed_member(conn, group_id, "IT-IVO2", mfr_ref="REF-IVO2")
    # differs from the stored raw_name only in case and surrounding whitespace
    _seed(conn, "vh-bfalias-ci", _fields(
        ref_list=_ev(["REF-IVO2"]), manufacturer=_ev("  ivoclar VIVADENT ag  ")))

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-bfalias-ci", "extract_rev": 1, "group_id": None}})

    assert res["emitted"] is True
    p = _payload(conn, "vh-bfalias-ci")
    assert p["group_id"] == group_id
    assert p["ref_gate"] is True

    # the stored alias row itself must not have been mutated to match
    row = conn.execute(
        "SELECT raw_name FROM manufacturer_alias WHERE canonical_name='IVOCLAR'"
    ).fetchone()
    assert row["raw_name"] == "Ivoclar Vivadent AG"


def test_backfill_unresolved_manufacturer_flags_without_self_seeding(conn):
    # No alias row and no group under this manufacturer at all: the doc
    # cannot link, and that must be visible on the job result (never inferred
    # silently from an empty link list) -- and the miss must NOT mint a
    # manufacturer_alias row. Self-seeding raw->raw is RESOLVE's job for a
    # genuinely new BC vendor code; it is wrong here, where the "raw" value is
    # one document's OCR/LLM-extracted spelling.
    before = conn.execute(
        "SELECT count(*) AS n FROM manufacturer_alias"
    ).fetchone()["n"]
    _seed(conn, "vh-bfunres", _fields(
        ref_list=_ev(["REF-NOPE"]), manufacturer=_ev("Totally Unknown Mfr Ltd")))

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-bfunres", "extract_rev": 1, "group_id": None}})

    assert res["emitted"] is True
    assert "manufacturer-unresolved" in res["flags"]
    p = _payload(conn, "vh-bfunres")
    assert "manufacturer-unresolved" in p["flags"]
    assert p["group_id"] is None
    assert p["links"] == []

    after = conn.execute(
        "SELECT count(*) AS n FROM manufacturer_alias"
    ).fetchone()["n"]
    assert after == before  # read-only: no row minted for the miss
    assert conn.execute(
        "SELECT 1 FROM manufacturer_alias WHERE raw_name='Totally Unknown Mfr Ltd'"
    ).fetchone() is None


# --- Case 6c: manufacturer name normalization (backfill-matching plan, task 3,
# Part A / [validate-mfr-normalize]) -----------------------------------------
#
# These exercise the FALLBACK path specifically: NO manufacturer_alias row
# exists, so `_canonicalize_manufacturer` returns the raw extracted string
# unchanged, and it is `_resolve_group_scoped`'s GROUP lookup itself that must
# tolerate the difference. This is exactly how the live bug shipped: every
# pre-existing test exercised the alias-hit path only.

def test_normalize_manufacturer_folds_case_whitespace_accents_and_punct_spacing():
    # live bug, measured against the real database: 'GC Europe N.V.' (doc)
    # vs 'GC EUROPE N.V.' (95 groups) resolved to 0 groups before this fix.
    assert vh._normalize_manufacturer("GC Europe N.V.") == vh._normalize_manufacturer("GC EUROPE N.V.")
    # whitespace: repeated/irregular spacing, leading/trailing
    assert vh._normalize_manufacturer("  Ivoclar   Vivadent  AG ") == \
        vh._normalize_manufacturer("Ivoclar Vivadent AG")
    # punctuation-adjacent spacing (OCR word-spacing noise)
    assert vh._normalize_manufacturer("GC Europe N . V .") == vh._normalize_manufacturer("GC Europe N.V.")
    # accents: justified by two playbook aliases (Dentsply) that exist only
    # to spell the same name with and without diacritics
    assert vh._normalize_manufacturer("Indústria e Comércio") == \
        vh._normalize_manufacturer("Industria e Comercio")
    # legal-form tokens are folded, never deleted -- distinct forms stay distinct
    assert vh._normalize_manufacturer("DENTSPLY Implants N.V.") != \
        vh._normalize_manufacturer("DENTSPLY Implants Manufacturing GmbH")


def test_backfill_group_lookup_fallback_is_case_insensitive_without_alias(conn):
    # Live GC bug, reproduced directly: no alias row for 'GC Europe N.V.'
    # (GC's real alias table only carries 'GC' and BC code '008'), so
    # canonicalization falls back to the raw string -- which the GROUP lookup
    # must still resolve against a differently-cased canonical_manufacturer.
    group_id = _seed_group(conn, canonical_manufacturer="GC EUROPE N.V.")
    _seed_member(conn, group_id, "IT-GC", mfr_ref="REF-GC")
    _seed(conn, "vh-gc-case", _fields(
        ref_list=_ev(["REF-GC"]), manufacturer=_ev("GC Europe N.V.")))

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-gc-case", "extract_rev": 1, "group_id": None}})

    assert res["emitted"] is True
    p = _payload(conn, "vh-gc-case")
    assert p["group_id"] == group_id
    assert p["ref_gate"] is True
    assert "manufacturer-unresolved" not in p["flags"]

    # no alias row was minted or otherwise touched by the fallback
    assert conn.execute(
        "SELECT 1 FROM manufacturer_alias WHERE raw_name='GC Europe N.V.'"
    ).fetchone() is None


def test_backfill_group_lookup_fallback_tolerates_whitespace_and_punctuation(conn):
    group_id = _seed_group(conn, canonical_manufacturer="GC EUROPE N.V.")
    _seed_member(conn, group_id, "IT-GC2", mfr_ref="REF-GC2")
    _seed(conn, "vh-gc-punct", _fields(
        ref_list=_ev(["REF-GC2"]),
        manufacturer=_ev("  GC   Europe N . V .  ")))  # OCR-ish spacing

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-gc-punct", "extract_rev": 1, "group_id": None}})

    p = _payload(conn, "vh-gc-punct")
    assert p["group_id"] == group_id
    assert p["ref_gate"] is True


def test_backfill_group_lookup_fallback_is_accent_insensitive_without_alias(conn):
    # Isolates the accent dimension from case (both sides already same case).
    group_id = _seed_group(conn, canonical_manufacturer="Dentsply Indústria e Comércio Ltda.")
    _seed_member(conn, group_id, "IT-ACC", mfr_ref="REF-ACC")
    _seed(conn, "vh-accent", _fields(
        ref_list=_ev(["REF-ACC"]),
        manufacturer=_ev("Dentsply Industria e Comercio Ltda.")))  # no diacritics

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-accent", "extract_rev": 1, "group_id": None}})

    p = _payload(conn, "vh-accent")
    assert p["group_id"] == group_id
    assert p["ref_gate"] is True


def test_group_lookup_normalization_does_not_merge_distinct_legal_forms(conn):
    # Guard against over-normalization: two DIFFERENT legal entities that
    # share a name stem but differ by legal-form suffix must NOT collapse
    # into one group -- normalization folds case/whitespace/accents, it never
    # deletes the words that carry legal-entity identity.
    nv_group = _seed_group(conn, canonical_manufacturer="DENTSPLY Implants N.V.")
    _seed_member(conn, nv_group, "IT-NV", mfr_ref="REF-NV")
    gmbh_group = _seed_group(conn, canonical_manufacturer="DENTSPLY Implants Manufacturing GmbH")
    _seed_member(conn, gmbh_group, "IT-GMBH", mfr_ref="REF-GMBH")

    _seed(conn, "vh-legalform", _fields(
        ref_list=_ev(["REF-NV"]), manufacturer=_ev("dentsply implants n.v.")))

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-legalform", "extract_rev": 1, "group_id": None}})

    p = _payload(conn, "vh-legalform")
    assert p["group_id"] == nv_group  # the N.V. group, not the GmbH one
    assert p["links"] == [{"item_ref": "IT-NV", "match_basis": "ref-list"}]


# --- Case 3b: per-manufacturer REF normalization (backfill-matching plan,
# task 3, Part B / [validate-ref-normalize]) ---------------------------------

def test_reorder_folds_both_spellings_of_one_bur_code_to_the_same_key():
    """Dentalia writes SHANK FIGURE SIZE, Komet prints FIGURE.SHANK.SIZE. The
    rule exists so the two meet; measured on the 186 Komet corpus PDFs, it
    takes catalogue codes found in the documents from 0 to 98."""
    rule = {"strategy": "reorder-shank-figure-size"}

    assert vh._normalize_ref("314 H1 006", rule) == "H1.314.006"
    assert vh._normalize_ref("H1.314.006", rule) == "H1.314.006"
    # already-dotted and spaced spellings of one code must be EQUAL, which is
    # the only property the REF gate actually depends on
    assert vh._normalize_ref("314 H1 006", rule) == vh._normalize_ref("H1.314.006", rule)


def test_reorder_handles_the_letterless_and_short_size_spellings():
    """Komet's older list layout prints '1.204.005' with no figure letters, and
    the catalogue writes some sizes unpadded."""
    rule = {"strategy": "reorder-shank-figure-size"}

    assert vh._normalize_ref("204 1 005", rule) == "1.204.005"
    assert vh._normalize_ref("1.204.005", rule) == "1.204.005"
    assert vh._normalize_ref("204 1 5", rule) == "1.204.005", "size zero-padded to 3"
    assert vh._normalize_ref("000 GPFQ04 020", rule) == "GPFQ04.000.020"


def test_reorder_leaves_a_packaging_suffix_alone():
    """Seven Komet items carry a Dentalia packaging suffix and two of them would
    fold onto an unsuffixed sibling's key -- one document naming the base code
    would then link both articles. Invariant 3 says no: they stay unfolded and
    unmatched until a document names them."""
    rule = {"strategy": "reorder-shank-figure-size"}

    assert vh._normalize_ref("104 H219A 023-1", rule) == "104 H219A 023-1"
    assert vh._normalize_ref("104 H219A 023", rule) == "H219A.104.023"
    assert vh._normalize_ref("104 H219A 023-1", rule) != vh._normalize_ref("104 H219A 023", rule)


def test_reorder_leaves_everything_that_is_not_a_bur_code_untouched():
    """Same safety property as the Ivoclar rule: the shape check is what makes
    a misapplied rule harmless."""
    rule = {"strategy": "reorder-shank-figure-size"}

    for ref in ("LS SFQ2008", "645986DC", "645986",
                "++E2265330513W", "STEEL BUR", ""):
        assert vh._normalize_ref(ref, rule) == ref
    assert vh._normalize_ref(None, rule) is None


def test_a_shankless_code_still_folds_because_komet_prints_it_that_way_too():
    """'000 SFD7 1' and '000 GPFQ04 020' look like non-burs -- shank 000 means
    there is no shank -- but Komet's own declarations print exactly this family
    in dotted form ('.000.020' appears across the gutta-percha declarations), so
    folding them is the same code in the other order, not an invented shape."""
    rule = {"strategy": "reorder-shank-figure-size"}

    assert vh._normalize_ref("000 SFD7 1", rule) == "SFD7.000.001"
    assert vh._normalize_ref("SFD7.000.001", rule) == "SFD7.000.001"


def test_the_komet_playbook_carries_the_reorder_rule():
    """The rule is playbook-scoped by design -- the client ruled 2026-08-18 that
    Komet is the only manufacturer whose codes are written in another order, so
    no other playbook may pick this up by accident."""
    from app import playbooks

    assert playbooks.for_manufacturer("KOMET").ref_normalize == {
        "strategy": "reorder-shank-figure-size"
    }
    ivoclar = playbooks.for_manufacturer("IVOCLAR")
    assert (ivoclar.ref_normalize or {}).get("strategy") != "reorder-shank-figure-size"


def test_normalize_ref_strip_trailing_letters_only_matches_the_authored_shape():
    rule = {"strategy": "strip-trailing-letters", "max_letters": 3}
    assert vh._normalize_ref("645986DC", rule) == "645986"
    assert vh._normalize_ref("645986", rule) == "645986"  # already base, unchanged
    assert vh._normalize_ref("104 H251EF 060", rule) == "104 H251EF 060"  # Komet-shaped
    assert vh._normalize_ref("645986WXYZ", rule) == "645986WXYZ"  # 4 letters > max_letters
    assert vh._normalize_ref("REF-1", None) == "REF-1"  # no rule -> identity
    assert vh._normalize_ref(None, rule) is None


def test_ref_normalization_strips_ivoclar_market_suffix(conn):
    # playbooks/ivoclar.json authors
    # ref_normalize={"strategy": "strip-trailing-letters", "max_letters": 3}
    # -- measured over 174 Ivoclar corpus PDFs (Blocker 2): the catalogue
    # stores the base article number, the DoC enumerates market variants
    # (e.g. 645986 -> 16 variants including 645986DC).
    group_id = _seed_group(conn, canonical_manufacturer="IVOCLAR")
    _seed_member(conn, group_id, "IT-IVOREF", mfr_ref="645986")  # base number, as BC stores it
    _seed(conn, "vh-ivo-ref", _fields(
        ref_list=_ev(["645986DC"]), manufacturer=_ev("IVOCLAR")))  # market variant, as the DoC reads

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-ivo-ref", "extract_rev": 1, "group_id": group_id}})

    assert res["emitted"] is True
    p = _payload(conn, "vh-ivo-ref")
    assert p["ref_gate"] is True
    assert p["links"] == [{"item_ref": "IT-IVOREF", "match_basis": "ref-list"}]

    # comparison-time only: the stored mfr_ref is never rewritten
    row = conn.execute(
        "SELECT mfr_ref FROM item_group_member WHERE item_ref='IT-IVOREF'"
    ).fetchone()
    assert row["mfr_ref"] == "645986"


def test_ref_normalization_does_not_apply_without_playbook_rule(conn):
    # A manufacturer with no playbook (or a playbook with no ref_normalize
    # key) must keep EXACT REF comparison. Normalization is per-manufacturer,
    # never global -- the reason is Komet's space-containing ISO bur codes
    # (104 H251EF 060), where trailing letters are load-bearing, not a market
    # suffix; applying Ivoclar's rule everywhere would corrupt them.
    group_id = _seed_group(conn, canonical_manufacturer="ACME")
    _seed_member(conn, group_id, "IT-ACME", mfr_ref="123456")
    _seed(conn, "vh-acme-ref", _fields(ref_list=_ev(["123456DC"])))

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-acme-ref", "extract_rev": 1, "group_id": group_id}})

    assert res["emitted"] is True
    p = _payload(conn, "vh-acme-ref")
    assert p["ref_gate"] is False
    # falls through to the C3 fetch-context fallback, the documented
    # behaviour for a requesting-group doc whose ref_list doesn't overlap
    assert p["links"] == [{"item_ref": "IT-ACME", "match_basis": "fetch-context"}]


# --- Case 6b: Backfill scoped hit, multiple groups match --------------------

def test_backfill_scoped_multi_group_match_flags_but_no_longer_caps(conn):
    # Two groups under the SAME manufacturer both genuinely pass C1 via ref
    # overlap. Deterministic resolution: lowest group_id wins, but the other
    # match is counted and flagged, never silently dropped.
    from app.handlers import gate as gh

    group_a = _seed_group(conn, canonical_manufacturer="ACME")
    _seed_member(conn, group_a, "IT-A", mfr_ref="REF-MULTI")
    group_b = _seed_group(conn, canonical_manufacturer="ACME")
    _seed_member(conn, group_b, "IT-B", mfr_ref="REF-MULTI")
    lower_group_id = min(group_a, group_b)

    _seed(conn, "vh-bfmulti", _fields(
        type=_ev("DoC", 0.97), regulation=_ev("MDR", 0.97), coverage_scope=_ev("group", 0.97),
        ref_list=_ev(["REF-MULTI"]), manufacturer=_ev("ACME")))

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-bfmulti", "extract_rev": 1, "group_id": None}})

    assert res["emitted"] is True
    assert res.get("multi_group_matches") == 2

    jobs = _gate_jobs(conn, "vh-bfmulti")
    assert len(jobs) == 1
    assert jobs[0]["dedupe_key"] == f"gate:vh-bfmulti:1:{lower_group_id}"
    p = jobs[0]["payload"]
    assert p["group_id"] == lower_group_id
    assert "multi-group-match" in p["flags"]
    assert p["ref_gate"] is True

    # Disposition, AMENDED 2026-08-13 (Denis's ruling): `multi-group-match` is
    # now INFORMATIONAL and no longer caps. Its original reason was that we
    # resolved to one group arbitrarily and the other matches were dropped --
    # but a document now links every matching member across ALL groups under
    # the pinned manufacturer, so nothing is arbitrary and there is nothing for
    # a reviewer to settle. The flag is still emitted and still shown; it just
    # does not gate. VALIDATE's half of this test (the flag IS raised, the
    # lowest group_id IS the primary, both matches ARE counted) is unchanged
    # above, which is the part that matters for provenance.
    gate_job = {"id": 1, "type": "gate.candidate",
                "payload": {**p, "archive_url": "file:///vh-bfmulti.pdf"}}
    gres = gh.handle_gate_candidate(conn, gate_job)
    assert gres["disposition"] == "production"


# --- Case 7: Backfill unscoped hit ------------------------------------------

def test_backfill_unscoped_hit_flags_ref_unscoped(conn):
    # A group matching this REF exists, but under an manufacturer we don't know
    # (ext carries no manufacturer) -> the hit is untrustworthy (bare article
    # numbers collide across manufacturers).
    group_id = _seed_group(conn, canonical_manufacturer="SOME-OTHER-MFR")
    _seed_member(conn, group_id, "IT-1", mfr_ref="REF-2")
    _seed(conn, "vh-bfunscoped", _fields(ref_list=_ev(["REF-2"])))  # no manufacturer field

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-bfunscoped", "extract_rev": 1, "group_id": None}})

    assert res["emitted"] is True
    p = _payload(conn, "vh-bfunscoped")
    assert p["ref_gate"] is False
    assert "ref-unscoped" in p["flags"]
    assert p["group_id"] is None  # not trusted/resolved
    assert p["links"] == []


# --- Case 8: Date sanity -----------------------------------------------------

def test_date_sanity_from_after_to_flags(conn):
    group_id = _seed_group(conn)
    _seed_member(conn, group_id, "IT-1", mfr_ref="196.644.050")
    _seed(conn, "vh-datebad", _fields(
        validity_from=_ev("2027-01-01"), validity_to=_ev("2020-01-01")))

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-datebad", "extract_rev": 1, "group_id": group_id}})

    assert res["emitted"] is True
    p = _payload(conn, "vh-datebad")
    assert "date-insane" in p["flags"]


@pytest.mark.parametrize("bad_date", ["9999-01-01", "0201-01-01"])
def test_date_sanity_implausible_year_flags(conn, bad_date):
    # PRD rule 3 "plausible ranges": a hallucinated (9999) or OCR-garbled
    # (3-digit) year is not a real date and must be flagged, not used to drive
    # supersession. from <= to still holds, so this exercises the range check.
    group_id = _seed_group(conn)
    _seed_member(conn, group_id, "IT-1", mfr_ref="196.644.050")
    _seed(conn, "vh-dateyear", _fields(
        validity_from=_ev("2020-01-01"), validity_to=_ev(bad_date)))

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-dateyear", "extract_rev": 1, "group_id": group_id}})

    assert res["emitted"] is True
    p = _payload(conn, "vh-dateyear")
    assert "date-insane" in p["flags"]


@pytest.mark.parametrize("bad_value", [
    "2025",                 # the real one: a copyright year, Straumann IFU
    "2026-04",              # year-month, no day
    "12 February 2026",     # prose the model did not normalise
    "n.a.",                 # the model's own "no value" sentinel, leaked as text
])
def test_date_sanity_unparseable_value_flags(conn, bad_value):
    # The regression: `_to_date` returns None BOTH for "no date stated" and for
    # "not a date at all", so an unparseable value matched neither existing
    # branch and reached GATE, whose insert casts it with `%s::date`. Straumann
    # IFU 7e33a160 returned validity_from="2025" off "(c) Institut Straumann AG,
    # 2025. All rights reserved." and died on `InvalidDatetimeFormat: invalid
    # input syntax for type date: "2025"` (2026-08-17).
    group_id = _seed_group(conn)
    _seed_member(conn, group_id, "IT-1", mfr_ref="196.644.050")
    _seed(conn, "vh-dateparse", _fields(validity_from=_ev(bad_value)))

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-dateparse", "extract_rev": 1, "group_id": group_id}})

    assert res["emitted"] is True
    p = _payload(conn, "vh-dateparse")
    assert "date-insane" in p["flags"]


def test_date_sanity_absent_date_is_not_insane(conn):
    # The other side of the same coin, and the reason the check tests the RAW
    # value rather than the parsed one: a document that simply states no date is
    # ordinary, extremely common, and must stay unflagged. Testing `parsed is
    # None` alone would flag every one of them.
    group_id = _seed_group(conn)
    _seed_member(conn, group_id, "IT-1", mfr_ref="196.644.050")
    _seed(conn, "vh-datenone", _fields(validity_from=_ev(None)))

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-datenone", "extract_rev": 1, "group_id": group_id}})

    assert res["emitted"] is True
    assert "date-insane" not in _payload(conn, "vh-datenone")["flags"]


# --- Case 9: C7 unambiguously older -> auto-superseded (fast path) ----------
# [task-5, fix round 1]: both dates present + older than current now emits
# BOTH superseded_by_doc_id/"auto-superseded" (the fast-path signal) AND keeps
# "older-than-current" (the safety net). GATE only takes the fast path to
# `superseded` when its own guard passes (no other blocking flag, validity_from
# confidence >= HIGH); otherwise "older-than-current" forces `manual` exactly
# as before task 5 (see test_gate_candidate_handler.py for GATE's side).

def test_c7_older_than_current_auto_supersedes(conn):
    group_id = _seed_group(conn)
    _seed_member(conn, group_id, "IT-1", mfr_ref="196.644.050")
    current_id = _seed_current_doc(conn, "vh-current-9", ["IT-1"], doc_type="DoC",
                                    regulation="MDR", validity_from="2024-01-01")
    _seed(conn, "vh-c7older", _fields(validity_from=_ev("2020-01-01")))

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-c7older", "extract_rev": 1, "group_id": group_id}})

    assert res["emitted"] is True
    p = _payload(conn, "vh-c7older")
    assert p["superseded_by_doc_id"] == current_id
    assert "auto-superseded" in p["flags"]
    assert "older-than-current" in p["flags"]   # kept, not replaced — GATE's safety net
    assert p["supersedes"] is None


def test_c7_equal_dates_flag_same_date_revision_and_supersede_nothing(conn):
    """Denis, 2026-09-04: two documents for one subject, type and regulation
    issued on the SAME day carry no order the dates can express, so neither
    may supersede by machine. The flag is blocking (GATE forces manual). Until
    this rule the equal case matched no branch and the second document landed
    as an unrelated production document -- three groups held three each."""
    group_id = _seed_group(conn)
    _seed_member(conn, group_id, "IT-1", mfr_ref="196.644.050")
    _seed_current_doc(conn, "vh-current-eq", ["IT-1"], doc_type="DoC",
                      regulation="MDR", validity_from="2024-01-01")
    _seed(conn, "vh-c7equal", _fields(validity_from=_ev("2024-01-01")))

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-c7equal", "extract_rev": 1, "group_id": group_id}})

    assert res["emitted"] is True
    p = _payload(conn, "vh-c7equal")
    assert "same-date-revision" in p["flags"]
    assert "older-than-current" not in p["flags"]
    assert "auto-superseded" not in p["flags"]
    assert p.get("superseded_by_doc_id") is None
    assert p.get("supersedes") is None


def test_revalidating_the_current_document_is_not_a_revision_of_itself(conn):
    """A production document re-validated at a new `extract_rev` (a repair, a
    re-extract, a template authored later) found ITSELF as the group's current
    document, matched its own date and flagged `same-date-revision`, so GATE
    sent it to manual. Live 2026-09-11: NEODENT doc 521, re-read with its new
    template, held all 241 of its new links behind a manual task. The tie rule
    compares two documents; one document is not a tie."""
    group_id = _seed_group(conn)
    _seed_member(conn, group_id, "IT-1", mfr_ref="196.644.050")
    _seed_current_doc(conn, "vh-self", ["IT-1"], doc_type="DoC",
                      regulation="MDR", validity_from="2024-01-01")
    _seed(conn, "vh-self", _fields(validity_from=_ev("2024-01-01")), extract_rev=2)

    vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-self", "extract_rev": 2, "group_id": group_id}})

    p = _payload(conn, "vh-self")
    assert "same-date-revision" not in p["flags"]
    assert "older-than-current" not in p["flags"]
    assert p.get("supersedes") is None
    assert p.get("superseded_by_doc_id") is None


def test_unambiguously_older_candidate_emits_superseded_by(conn):
    gid = _seed_group(conn)
    _seed_item(conn, "I1")
    _seed_member(conn, gid, "I1", mfr_ref="R1")
    current = _seed_current_doc(conn, "h-new", ["I1"], doc_type="DoC",
                               validity_from="2025-01-01")
    _seed(conn, "h-old", _fields(type=_ev("DoC"), regulation=_ev("MDR"),
                                 validity_from=_ev("2020-01-01"), ref_list=_ev(["R1"])))
    vh.handle_validate_doc(
        conn, {"id": 1, "payload": {"content_hash": "h-old", "extract_rev": 1,
                                    "group_id": gid}})
    payload = _gate_jobs(conn, "h-old")[0]["payload"]
    assert payload["superseded_by_doc_id"] == current
    assert "auto-superseded" in payload["flags"]
    assert "older-than-current" in payload["flags"]


def test_null_dated_candidate_still_caps_at_staged_and_is_not_auto_filed(conn):
    gid = _seed_group(conn)
    _seed_item(conn, "I2")
    _seed_member(conn, gid, "I2", mfr_ref="R2")
    _seed_current_doc(conn, "h-cur2", ["I2"], doc_type="DoC", validity_from="2025-01-01")
    _seed(conn, "h-nulldate", _fields(type=_ev("DoC"), regulation=_ev("MDR"),
                                      validity_from=_ev(None), ref_list=_ev(["R2"])))
    vh.handle_validate_doc(
        conn, {"id": 1, "payload": {"content_hash": "h-nulldate", "extract_rev": 1,
                                    "group_id": gid}})
    payload = _gate_jobs(conn, "h-nulldate")[0]["payload"]
    assert payload.get("superseded_by_doc_id") is None
    assert "downgrade-uncomparable" in payload["flags"]


# --- Case 10: C7 null date ---------------------------------------------------
# Brief case 10: "candidate OR current validity_from null" -> both directions
# get the same expectations (either side null -> no comparison -> uncomparable).

@pytest.mark.parametrize("current_validity_from,candidate_validity_from", [
    (None, "2025-01-01"),   # current-null
    ("2020-01-01", None),  # candidate-null
])
def test_c7_null_date_is_uncomparable(conn, current_validity_from, candidate_validity_from):
    group_id = _seed_group(conn)
    _seed_member(conn, group_id, "IT-1", mfr_ref="196.644.050")
    _seed_current_doc(conn, "vh-current-10", ["IT-1"], doc_type="DoC",
                       regulation="MDR", validity_from=current_validity_from)
    _seed(conn, "vh-c7null", _fields(validity_from=_ev(candidate_validity_from)))

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-c7null", "extract_rev": 1, "group_id": group_id}})

    assert res["emitted"] is True
    p = _payload(conn, "vh-c7null")
    assert "downgrade-uncomparable" in p["flags"]
    assert "older-than-current" not in p["flags"]
    assert p["supersedes"] is None


# --- Case 11: C6 supersedes --------------------------------------------------

def test_c6_supersedes_same_subject_type_regulation(conn):
    group_id = _seed_group(conn)
    _seed_member(conn, group_id, "IT-1", mfr_ref="196.644.050")
    current_id = _seed_current_doc(conn, "vh-current-11", ["IT-1"], doc_type="DoC",
                                    regulation="MDR", validity_from="2020-01-01")
    _seed(conn, "vh-c6super", _fields(validity_from=_ev("2025-06-01")))

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-c6super", "extract_rev": 1, "group_id": group_id}})

    assert res["emitted"] is True
    p = _payload(conn, "vh-c6super")
    assert p["supersedes"] == current_id
    assert "older-than-current" not in p["flags"]


# --- Case 12: C6 MDR vs MDD (parallel chains) -------------------------------

def test_c6_mdr_vs_mdd_never_supersedes_records_related(conn):
    group_id = _seed_group(conn)
    _seed_member(conn, group_id, "IT-1", mfr_ref="196.644.050")
    mdd_id = _seed_current_doc(conn, "vh-current-12", ["IT-1"], doc_type="DoC",
                               regulation="MDD", validity_from="2020-01-01")
    _seed(conn, "vh-c6mdrmdd", _fields(
        regulation=_ev("MDR"), validity_from=_ev("2025-06-01")))

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-c6mdrmdd", "extract_rev": 1, "group_id": group_id}})

    assert res["emitted"] is True
    p = _payload(conn, "vh-c6mdrmdd")
    assert p["supersedes"] is None
    assert p.get("related_doc_id") == mdd_id


# --- Case 13: Type/regulation consistency (rule 6) --------------------------

def test_consistency_expiry_on_certless_doc_flags(conn):
    group_id = _seed_group(conn)
    _seed_member(conn, group_id, "IT-1", mfr_ref="196.644.050")
    _seed(conn, "vh-certless", _fields(
        validity_to=_ev("2030-01-01"), cert_number=_ev(None), referenced_docs=_ev([])))

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-certless", "extract_rev": 1, "group_id": group_id}})

    assert res["emitted"] is True
    p = _payload(conn, "vh-certless")
    assert "expiry-on-certless-doc" in p["flags"]


def _stated(value, phrase):
    ev = _ev(value)
    ev["verbatim"] = phrase
    return ev


def test_a_stated_expiry_does_not_flag_even_with_no_certificate_cited(conn):
    """The over-fire half. The rule used to ask whether the declaration cited a
    certificate, reasoning from MDR Annex IV that a DoC has no expiry element at
    all so an uncited one must be a misread. Manufacturers print them anyway:
    measured 2026-08-17 over every registry document carrying a validity_to,
    156 of 156 label the date in their own evidence verbatim. Fifteen IVOCLAR
    documents sat at `staged` with a 0.97 score and a passing REF gate purely
    for naming no certificate."""
    group_id = _seed_group(conn)
    _seed_member(conn, group_id, "IT-1", mfr_ref="196.644.050")
    _seed(conn, "vh-stated", _fields(
        validity_to=_stated("2026-05-04", "Valid until 2026-05-04"),
        cert_number=_ev(None), referenced_docs=_ev([])))

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-stated", "extract_rev": 1, "group_id": group_id}})

    assert res["emitted"] is True
    assert "expiry-on-certless-doc" not in _payload(conn, "vh-stated")["flags"]


def test_an_unstated_expiry_flags_even_when_a_certificate_is_cited(conn):
    """The under-fire half, and the defect the rule was built for. GC doc 227
    reached `production` carrying "Leuven, 12 February 2026" -- a city and a
    SIGNING date -- as its expiry, because it named cert `MDR 778483` and so
    passed the certificate test untouched. Citing a certificate says nothing
    about whether the date on this page is an expiry."""
    group_id = _seed_group(conn)
    _seed_member(conn, group_id, "IT-1", mfr_ref="196.644.050")
    _seed(conn, "vh-signing-line", _fields(
        validity_to=_stated("2026-02-12", "Leuven, 12 February 2026"),
        cert_number=_ev("MDR 778483"), referenced_docs=_ev([])))

    vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-signing-line", "extract_rev": 1, "group_id": group_id}})

    assert "expiry-on-certless-doc" in _payload(conn, "vh-signing-line")["flags"]


def test_an_expiry_with_no_evidence_verbatim_flags(conn):
    """A date nobody can point at on the page is exactly what this rule catches,
    so absent evidence fails closed rather than open."""
    group_id = _seed_group(conn)
    _seed_member(conn, group_id, "IT-1", mfr_ref="196.644.050")
    ev = _ev("2030-01-01")
    ev.pop("verbatim")
    _seed(conn, "vh-noverbatim", _fields(
        validity_to=ev, cert_number=_ev("EC 1"), referenced_docs=_ev([])))

    vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-noverbatim", "extract_rev": 1, "group_id": group_id}})

    assert "expiry-on-certless-doc" in _payload(conn, "vh-noverbatim")["flags"]


@pytest.mark.parametrize("phrase", [
    "Valid until 2030-01-01",
    "Valid until: 2030-01-01",
    "Expiry Date 2030-01-01",
    "Expiry Date: 2030-01-01",
    "Scadenza / Valid until 2030-01-01",
    "This declaration is valid until: 15. April 2029",
    "Gültig bis 01.01.2030",
    "Velja do 1.1.2030",
])
def test_expiry_phrases_across_the_corpus_languages(conn, phrase):
    """The first six are every distinct verbatim shape in the registry on
    2026-08-17; the last two are extrapolation from corpus brands' home markets.
    Extrapolating is safe here ONLY because an unrecognised phrase leaves the
    flag ON -- it costs a human a look, never a wrong auto-write -- so the list
    may be extended per brand without ever having been unsafe while short."""
    group_id = _seed_group(conn)
    _seed_member(conn, group_id, "IT-1", mfr_ref="196.644.050")
    ch = f"vh-phrase-{abs(hash(phrase))}"
    _seed(conn, ch, _fields(
        validity_to=_stated("2030-01-01", phrase),
        cert_number=_ev(None), referenced_docs=_ev([])))

    vh.handle_validate_doc(conn, {"payload": {
        "content_hash": ch, "extract_rev": 1, "group_id": group_id}})

    assert "expiry-on-certless-doc" not in _payload(conn, ch)["flags"]


# --- Case 14: Unscoped REF linking ([validate-ref-catalogue]) ---------------
#
# A backfill/corpus document that carries NO manufacturer can still identify
# itself by article number. Measured 2026-08-11: of 5.511 distinct catalogue
# REFs only 24 collide across manufacturers (12 prose, 12 genuine article
# numbers), so a REF match alone is strong enough to PROPOSE a link. It is
# never strong enough to write one — catalogue-observed uniqueness is not
# global uniqueness — hence the capped `ref-catalogue` basis.

def test_unscoped_ref_links_at_capped_ref_catalogue_basis(conn):
    group_id = _seed_group(conn, canonical_manufacturer="LONE-MFR")
    _seed_member(conn, group_id, "IT-RC", mfr_ref="698946")
    _seed_member(conn, group_id, "IT-RC-OTHER", mfr_ref="111222")
    _seed(conn, "vh-refcat", _fields(ref_list=_ev(["698946"])))  # no manufacturer

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-refcat", "extract_rev": 1, "group_id": None}})

    assert res["emitted"] is True
    p = _payload(conn, "vh-refcat")
    # Linked, and scoped to the overlapping member only.
    assert [l["item_ref"] for l in p["links"]] == ["IT-RC"]
    assert p["links"][0]["match_basis"] == "ref-catalogue"
    assert p["group_id"] == group_id
    assert "ref-catalogue" in p["flags"]
    # C1 was NOT passed: the pair (canonical_manufacturer, mfr_ref) was never
    # verified, only the bare number. ref_gate stays False so the document
    # itself can never reach production either.
    assert p["ref_gate"] is False


def test_unscoped_short_ref_never_links(conn):
    # `100` -> INTERDENT, `102` -> SANOLABOR, `158` -> ULTRADENT: 85 catalogue
    # REFs are 1-3 characters. Each is "unambiguous" and worthless as identity.
    group_id = _seed_group(conn, canonical_manufacturer="INTERDENT")
    _seed_member(conn, group_id, "IT-SHORT", mfr_ref="100")
    _seed(conn, "vh-refshort", _fields(ref_list=_ev(["100"])))

    vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-refshort", "extract_rev": 1, "group_id": None}})

    p = _payload(conn, "vh-refshort")
    assert p["links"] == []
    assert p["group_id"] is None
    assert "ref-catalogue" not in p["flags"]
    assert "ref-unscoped" in p["flags"]  # the hit is still reported, just not used


def test_unscoped_prose_ref_never_links(conn):
    # 49 distinct catalogue values containing "!" are remarks, not codes
    # (`UKINJENO!`, `NI VEC DOBAVLJIVO!`). INGEST flags them `mfr_ref_prose`
    # and now scrubs them to NULL, but a member row keeps the value until its
    # item is re-ingested, so they stay matchable unless excluded here.
    group_id = _seed_group(conn, canonical_manufacturer="PROSE-MFR")
    _seed_member(conn, group_id, "IT-PROSE", mfr_ref="UKINJENO!")
    _seed(conn, "vh-refprose", _fields(ref_list=_ev(["UKINJENO!"])))

    vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-refprose", "extract_rev": 1, "group_id": None}})

    p = _payload(conn, "vh-refprose")
    assert p["links"] == []
    assert p["group_id"] is None
    assert "ref-catalogue" not in p["flags"]


def test_unscoped_ambiguous_ref_flags_instead_of_choosing(conn):
    # Measured live: `878115` is held by BOTH GC EUROPE N.V. and VOCO. Picking
    # one would attach a certificate to another company's product.
    group_a = _seed_group(conn, canonical_manufacturer="GC EUROPE N.V.")
    _seed_member(conn, group_a, "IT-GC", mfr_ref="878115")
    group_b = _seed_group(conn, canonical_manufacturer="VOCO")
    _seed_member(conn, group_b, "IT-VOCO", mfr_ref="878115")
    _seed(conn, "vh-refambig", _fields(ref_list=_ev(["878115"])))

    vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-refambig", "extract_rev": 1, "group_id": None}})

    p = _payload(conn, "vh-refambig")
    assert p["links"] == []
    assert p["group_id"] is None
    assert "multi-manufacturer-ref" in p["flags"]
    assert "ref-catalogue" not in p["flags"]


def test_unscoped_ref_spanning_two_groups_of_one_manufacturer_flags_multi_group(conn):
    # Same manufacturer, two groups: deterministic lowest-group_id resolution,
    # but the residual is counted and flagged exactly as the scoped path does.
    group_a = _seed_group(conn, canonical_manufacturer="ONE-MFR")
    _seed_member(conn, group_a, "IT-G1", mfr_ref="654321")
    group_b = _seed_group(conn, canonical_manufacturer="ONE-MFR")
    _seed_member(conn, group_b, "IT-G2", mfr_ref="654321")
    _seed(conn, "vh-refcat-mg", _fields(ref_list=_ev(["654321"])))

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-refcat-mg", "extract_rev": 1, "group_id": None}})

    p = _payload(conn, "vh-refcat-mg")
    assert p["group_id"] == min(group_a, group_b)
    assert "multi-group-match" in p["flags"]
    assert res.get("multi_group_matches") == 2


def test_ref_catalogue_link_cannot_be_written_as_production(conn):
    # C5 / invariant 3 enforced by the DB, not only by gate.py's TRUSTED_BASES:
    # no writer, not even a hand-written UPDATE, can promote this basis.
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, status, content_hash, archive_url, "
        "coverage_scope) VALUES ('DoC','MDR','production','h-rc-ck','/a.pdf','group') "
        "RETURNING doc_id"
    ).fetchone()["doc_id"]
    _seed_item(conn, "IT-CK")
    with pytest.raises(Exception) as exc:
        conn.execute(
            "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
            "VALUES ('IT-CK', %s, 'ref-catalogue', 'production')",
            (doc_id,),
        )
    assert "item_document_trusted_basis_ck" in str(exc.value)


def test_gate_stages_a_ref_catalogue_link_even_at_top_confidence(conn):
    from app.handlers import gate as gh

    group_id = _seed_group(conn, canonical_manufacturer="STAGED-MFR")
    _seed_member(conn, group_id, "IT-STG", mfr_ref="777888")
    _seed(conn, "vh-refcat-gate", _fields(
        type=_ev("DoC", 0.99), regulation=_ev("MDR", 0.99),
        coverage_scope=_ev("group", 0.99), ref_list=_ev(["777888"])))

    vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-refcat-gate", "extract_rev": 1, "group_id": None}})
    p = _payload(conn, "vh-refcat-gate")

    gres = gh.handle_gate_candidate(conn, {"id": 1, "type": "gate.candidate",
        "payload": {**p, "archive_url": "file:///vh-refcat-gate.pdf"}})

    assert gres["disposition"] == "staged"
    link = conn.execute(
        "SELECT status, match_basis FROM item_document WHERE item_ref='IT-STG'"
    ).fetchone()
    assert link["match_basis"] == "ref-catalogue"
    assert link["status"] == "staged"


# --- Case 15: the previously silent state ([validate-ref-catalogue]) --------

def test_known_manufacturer_with_no_ref_overlap_says_so(conn):
    # Manufacturer resolved, groups found, but the document covers nothing we
    # stock. Used to fall through with no flag and no count at all — roughly
    # 120 of 174 Ivoclar corpus documents land here.
    group_id = _seed_group(conn, canonical_manufacturer="ACME")
    _seed_member(conn, group_id, "IT-NO", mfr_ref="999999")
    _seed(conn, "vh-norefoverlap", _fields(
        ref_list=_ev(["000000"]), manufacturer=_ev("ACME")))

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-norefoverlap", "extract_rev": 1, "group_id": None}})

    p = _payload(conn, "vh-norefoverlap")
    assert "no-ref-overlap" in p["flags"]
    assert "manufacturer-unresolved" not in p["flags"]  # we DO know whose it is
    assert p["links"] == []
    assert res["emitted"] is True


# --- Case 16: item_ref is a REF-gate comparand (C12, 2026-08-13) -----------
#
# The client's ruling: the supplier article number is not the important one,
# the primary item number is, "ker je ta tudi na samem fizicnem artiklu
# navedena". So the comparand is the pair (canonical_manufacturer, catalogue
# article number) and the number is mfr_ref OR item_ref.
#
# Measured 2026-08-12 (tools/match_key_analysis.py): item_ref collides across
# manufacturers 0 times against mfr_ref's 34 -- structurally, since item_ref is
# item_mirror's PRIMARY KEY -- carries 0 prose values against 64, and its reach
# strictly contains mfr_ref's (mfr_only was 0 over 1.502 extracted REFs).

def test_scoped_item_ref_match_links_at_ref_item(conn):
    # 7.082 of 15.958 catalogue rows carry NO mfr_ref at all (44,4%). Before
    # this, every one of them was unreachable by the REF gate however clearly
    # the document named it.
    group_id = _seed_group(conn)
    _seed_member(conn, group_id, "0.900.0001", mfr_ref=None)
    _seed_member(conn, group_id, "0.032.1704", mfr_ref=None)  # no overlap
    _seed(conn, "vh-itemref", _fields(ref_list=_ev(["0.900.0001", "999999"])))

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-itemref", "extract_rev": 1, "group_id": group_id}})

    assert res["emitted"] is True
    p = _payload(conn, "vh-itemref")
    assert p["ref_gate"] is True
    assert p["links"] == [{"item_ref": "0.900.0001", "match_basis": "ref-item"}]


def test_mfr_ref_keeps_the_basis_when_both_numbers_match(conn):
    # 7.450 of 15.958 rows have mfr_ref == item_ref. The link must record the
    # stronger claim -- the supplier's own number matched -- not the weaker one,
    # or `ref-list` stops meaning anything.
    group_id = _seed_group(conn)
    _seed_member(conn, group_id, "613289", mfr_ref="613289")
    _seed(conn, "vh-bothmatch", _fields(ref_list=_ev(["613289"])))

    vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-bothmatch", "extract_rev": 1, "group_id": group_id}})

    p = _payload(conn, "vh-bothmatch")
    assert p["links"] == [{"item_ref": "613289", "match_basis": "ref-list"}]


def test_supplier_number_still_wins_when_the_two_disagree(conn):
    # A member whose mfr_ref matches and whose item_ref does not is unchanged
    # behaviour: the regression net for the existing path.
    group_id = _seed_group(conn)
    _seed_member(conn, group_id, "IT-DIFF", mfr_ref="613289")
    _seed(conn, "vh-suppwins", _fields(ref_list=_ev(["613289"])))

    vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-suppwins", "extract_rev": 1, "group_id": group_id}})

    p = _payload(conn, "vh-suppwins")
    assert p["links"] == [{"item_ref": "IT-DIFF", "match_basis": "ref-list"}]


def test_ref_item_link_may_be_written_as_production(conn):
    # The counterpart of test_ref_catalogue_link_cannot_be_written_as_production.
    # Migration 023 deliberately leaves the CHECK alone: ref-item is scoped, so
    # it carries the same verified pair ref-list does.
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, status, content_hash, archive_url, "
        "coverage_scope) VALUES ('DoC','MDR','production','h-ri-ck','/a.pdf','group') "
        "RETURNING doc_id"
    ).fetchone()["doc_id"]
    _seed_item(conn, "IT-RI-CK")
    conn.execute(
        "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
        "VALUES ('IT-RI-CK', %s, 'ref-item', 'production')",
        (doc_id,),
    )
    row = conn.execute(
        "SELECT status FROM item_document WHERE item_ref='IT-RI-CK'").fetchone()
    assert row["status"] == "production"


def test_gate_writes_a_ref_item_link_to_production(conn):
    # The DB permitting it is not enough: gate.py's TRUSTED_BASES is the other
    # half, and a basis missing from that tuple stages silently at any
    # confidence.
    from app.handlers import gate as gh

    group_id = _seed_group(conn, canonical_manufacturer="ACME")
    _seed_member(conn, group_id, "0.032.9001", mfr_ref=None)
    _seed(conn, "vh-refitem-gate", _fields(
        type=_ev("DoC", 0.99), regulation=_ev("MDR", 0.99),
        coverage_scope=_ev("group", 0.99), ref_list=_ev(["0.032.9001"])))

    vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-refitem-gate", "extract_rev": 1, "group_id": group_id}})
    p = _payload(conn, "vh-refitem-gate")

    gres = gh.handle_gate_candidate(conn, {"id": 1, "type": "gate.candidate",
        "payload": {**p, "archive_url": "file:///vh-refitem-gate.pdf"}})

    assert gres["disposition"] == "production"
    link = conn.execute(
        "SELECT status, match_basis FROM item_document WHERE item_ref='0.032.9001'"
    ).fetchone()
    assert link["match_basis"] == "ref-item"
    assert link["status"] == "production"


def test_unscoped_item_ref_match_still_caps_at_ref_catalogue(conn):
    # The ruling widens the comparand, NOT the trust model. With no
    # manufacturer the number is what INFERS the manufacturer, so
    # catalogue-observed uniqueness is doing the work and the cap must hold.
    group_id = _seed_group(conn, canonical_manufacturer="LONE-MFR")
    _seed_member(conn, group_id, "0.032.7788", mfr_ref=None)
    _seed(conn, "vh-itemref-unscoped", _fields(ref_list=_ev(["0.032.7788"])))

    vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-itemref-unscoped", "extract_rev": 1, "group_id": None}})

    p = _payload(conn, "vh-itemref-unscoped")
    assert [l["item_ref"] for l in p["links"]] == ["0.032.7788"]
    assert p["links"][0]["match_basis"] == "ref-catalogue"
    assert p["ref_gate"] is False
    assert "ref-catalogue" in p["flags"]


def test_unscoped_item_ref_ambiguity_guard_still_fires(conn):
    # Two manufacturers, one number. The guard that contains the residual
    # global-uniqueness risk must see item_ref values too, or admitting them
    # would open exactly the hole the guard exists to close.
    group_a = _seed_group(conn, canonical_manufacturer="MFR-A")
    _seed_member(conn, group_a, "0.032.5555", mfr_ref=None)
    group_b = _seed_group(conn, canonical_manufacturer="MFR-B")
    _seed_member(conn, group_b, "IT-B", mfr_ref="0.032.5555")
    _seed(conn, "vh-itemref-ambig", _fields(ref_list=_ev(["0.032.5555"])))

    vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-itemref-ambig", "extract_rev": 1, "group_id": None}})

    p = _payload(conn, "vh-itemref-ambig")
    assert p["links"] == []
    assert "multi-manufacturer-ref" in p["flags"]


def test_unscoped_short_item_ref_never_links(conn):
    # The guards were calibrated on mfr_ref and Task 6 required them re-derived
    # over item_ref rather than reused. They hold: 2.114 of 15.958 item_refs are
    # under 6 characters, and `1055` identifying a document would be an accident.
    group_id = _seed_group(conn, canonical_manufacturer="SHORT-MFR")
    _seed_member(conn, group_id, "1055", mfr_ref=None)
    _seed(conn, "vh-itemref-short", _fields(ref_list=_ev(["1055"])))

    vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-itemref-short", "extract_rev": 1, "group_id": None}})

    p = _payload(conn, "vh-itemref-short")
    assert p["links"] == []
    assert p["group_id"] is None
    assert "ref-catalogue" not in p["flags"]


# --- Case 17: a document covers every item it names (Denis, 2026-08-13) ----
#
# "all items must get the document attached if a document covers multiple
# items". Both paths used to resolve to ONE group (lowest group_id) and link
# only its members, flagging `multi-group-match` and capping at staged. The flag
# marked the loss instead of preventing it.
#
# Measured after the ref_list repair: 3.118 extracted codes reach 323 catalogue
# items and 96 links were written -- 228 items missed across 23 documents.
# Document 204's REFs reach 20 items across 5 groups, ALL under the same
# canonical_manufacturer, and it linked 1. The pair (manufacturer, article
# number) holds for all 20, so there was never a trust reason to drop 19.
# item_group is OUR clustering for discovery, not the manufacturer's statement
# of what the declaration covers.

def _three_groups_one_manufacturer(conn, mfr="ACME"):
    a = _seed_group(conn, canonical_manufacturer=mfr)
    _seed_member(conn, a, "IT-A1", mfr_ref="700001")
    b = _seed_group(conn, canonical_manufacturer=mfr)
    _seed_member(conn, b, "IT-B1", mfr_ref="700002")
    _seed_member(conn, b, "IT-B2", mfr_ref="700003")
    c = _seed_group(conn, canonical_manufacturer=mfr)
    _seed_member(conn, c, "IT-C1", mfr_ref="700004")
    _seed_member(conn, c, "IT-C2", mfr_ref="999999")   # not named by the doc
    return a, b, c


def test_scoped_document_links_every_group_it_names(conn):
    a, b, c = _three_groups_one_manufacturer(conn)
    _seed(conn, "vh-mg-scoped", _fields(
        ref_list=_ev(["700001", "700002", "700003", "700004"]),
        manufacturer=_ev("ACME")))

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-mg-scoped", "extract_rev": 1, "group_id": None}})

    assert res["emitted"] is True
    p = _payload(conn, "vh-mg-scoped")
    assert sorted(l["item_ref"] for l in p["links"]) == ["IT-A1", "IT-B1", "IT-B2", "IT-C1"]
    assert p["ref_gate"] is True
    # The lowest group stays the document's primary, for provenance only.
    assert p["group_id"] == a


def test_unscoped_document_links_every_group_it_names(conn):
    a, b, c = _three_groups_one_manufacturer(conn, mfr="LONE-MFR")
    _seed(conn, "vh-mg-unscoped", _fields(          # no manufacturer extracted
        ref_list=_ev(["700001", "700002", "700003", "700004"])))

    vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-mg-unscoped", "extract_rev": 1, "group_id": None}})

    p = _payload(conn, "vh-mg-unscoped")
    assert sorted(l["item_ref"] for l in p["links"]) == ["IT-A1", "IT-B1", "IT-B2", "IT-C1"]
    # Still the capped basis: with no manufacturer the number INFERRED it.
    assert {l["match_basis"] for l in p["links"]} == {"ref-catalogue"}
    assert p["ref_gate"] is False


def test_spanning_groups_is_still_counted_and_flagged(conn):
    # The residual is reported, never inferred from an unremarkable link list.
    _three_groups_one_manufacturer(conn)
    _seed(conn, "vh-mg-count", _fields(
        ref_list=_ev(["700001", "700002", "700004"]), manufacturer=_ev("ACME")))

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-mg-count", "extract_rev": 1, "group_id": None}})

    p = _payload(conn, "vh-mg-count")
    assert "multi-group-match" in p["flags"]
    assert res.get("multi_group_matches") == 3


def test_an_item_in_two_groups_is_linked_once(conn):
    # item_group_member is keyed per group, so one item can sit in several.
    # A duplicate link would violate item_document's (item_ref, doc_id) PK at GATE.
    a = _seed_group(conn, canonical_manufacturer="DUP-MFR")
    b = _seed_group(conn, canonical_manufacturer="DUP-MFR")
    _seed_member(conn, a, "IT-DUP", mfr_ref="700010")
    _seed_member(conn, b, "IT-DUP", mfr_ref="700010")
    _seed(conn, "vh-mg-dup", _fields(
        ref_list=_ev(["700010"]), manufacturer=_ev("DUP-MFR")))

    vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-mg-dup", "extract_rev": 1, "group_id": None}})

    p = _payload(conn, "vh-mg-dup")
    assert [l["item_ref"] for l in p["links"]] == ["IT-DUP"]


def test_cross_manufacturer_ambiguity_guard_is_untouched(conn):
    # The one guard containing the global-uniqueness risk. Linking across groups
    # must NOT become linking across manufacturers.
    ga = _seed_group(conn, canonical_manufacturer="MFR-ONE")
    _seed_member(conn, ga, "IT-ONE", mfr_ref="700020")
    gb = _seed_group(conn, canonical_manufacturer="MFR-TWO")
    _seed_member(conn, gb, "IT-TWO", mfr_ref="700020")
    _seed(conn, "vh-mg-xmfr", _fields(ref_list=_ev(["700020"])))

    vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-mg-xmfr", "extract_rev": 1, "group_id": None}})

    p = _payload(conn, "vh-mg-xmfr")
    assert p["links"] == []
    assert "multi-manufacturer-ref" in p["flags"]


def test_scoped_linking_never_crosses_into_another_manufacturer(conn):
    """The manufacturer filter inside `_links_for_manufacturer` is what keeps
    "link every group" from meaning "link every group in the catalogue".

    `test_cross_manufacturer_ambiguity_guard_is_untouched` does NOT cover this:
    it exercises the UNSCOPED path, where `_manufacturers_holding_refs` returns
    two holders and the handler bails before any group is scanned. Here the
    manufacturer IS known, so that guard never runs and this filter is the only
    thing standing between a GC declaration and a VOCO item that happens to
    print the same article number (measured live: `878115` is held by both).
    """
    ours = _seed_group(conn, canonical_manufacturer="ACME")
    _seed_member(conn, ours, "IT-ACME", mfr_ref="700030")
    theirs = _seed_group(conn, canonical_manufacturer="OTHER-CO")
    _seed_member(conn, theirs, "IT-OTHER", mfr_ref="700030")
    _seed(conn, "vh-mg-xscope", _fields(
        ref_list=_ev(["700030"]), manufacturer=_ev("ACME")))

    vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-mg-xscope", "extract_rev": 1, "group_id": None}})

    p = _payload(conn, "vh-mg-xscope")
    assert [l["item_ref"] for l in p["links"]] == ["IT-ACME"]


def test_a_rejected_cited_certificate_resolves_to_nothing_and_is_flagged(conn):
    """Pins behaviour that already holds, because it is load-bearing and easy to
    break: `_resolve_cited_certificate` restricts to production/superseded, so a
    DoC citing a certificate a human REJECTED resolves to None and raises
    `cert-unresolved` rather than silently inheriting a withdrawn date.

    Written while fixing the READ side (`report.py`), which followed
    `cert_doc_id` unfiltered and did inherit it. The forward path was already
    right; nothing asserted it, so nothing would have caught a regression that
    made the two disagree again."""
    conn.execute(
        "INSERT INTO document (type, regulation, cert_number, validity_to, status, "
        "content_hash, archive_url, coverage_scope) VALUES ('EC','MDR','REJ-1',"
        "'2027-01-01','rejected','h-rej-cert','/archive/rej.pdf','group')")
    _seed(conn, "vh-rejcert", _fields(
        type=_ev("DoC", 0.97), regulation=_ev("MDR", 0.97),
        coverage_scope=_ev("group", 0.97), cert_number=_ev("REJ-1")))

    vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-rejcert", "extract_rev": 1, "group_id": None}})

    p = _gate_jobs(conn, "vh-rejcert")[0]["payload"]
    assert p["cert_doc_id"] is None, "a rejected certificate must not be resolved"
    assert "cert-unresolved" in p["flags"]


# --- Case 18: map-supplier, the basis that names what matched (spec §9a) ---
#
# A `ref_list` evidence dict carrying `source: coverage-map` was read out of
# the manufacturer's own article -> document index, not off the document
# itself. `match_basis` is what the registry keeps as provenance, so a link
# formed from such a list must say so. Universal and data-driven: the rule
# triggers on the marker alone, never on a manufacturer, and is inert for
# every document already in the registry because none carries the marker.

def test_a_ref_list_from_the_coverage_map_names_its_own_basis(conn):
    """match_basis is what the registry keeps as provenance, so it must name
    the thing that actually matched. A list read out of the manufacturer's
    index is not a list read off the document."""
    group_id = _seed_group(conn, canonical_manufacturer="KOMET")
    _seed_member(conn, group_id, "314 H1 006")
    fields = _fields()
    fields["ref_list"] = {"value": ["H1.314.006"], "conf": 0.95, "tier": "T0",
                          "verbatim": "row 3 of the index", "page": None,
                          "archive_url": "/archive/KOMET/idx/beef__index.xlsx",
                          "source": "coverage-map"}
    _seed(conn, "komet-map", fields)

    vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "komet-map", "extract_rev": 1, "group_id": group_id}})

    p = _payload(conn, "komet-map")
    assert [l["match_basis"] for l in p["links"]] == ["map-supplier"]


def test_an_ordinary_ref_list_keeps_the_basis_it_always_had(conn):
    """Inert for every document in the registry: none carries the marker."""
    group_id = _seed_group(conn, canonical_manufacturer="KOMET")
    _seed_member(conn, group_id, "314 H1 006")
    fields = _fields()
    fields["ref_list"] = {"value": ["H1.314.006"], "conf": 0.9, "tier": "T0",
                          "verbatim": "1 REF code", "page": 1}
    _seed(conn, "komet-parsed", fields)

    vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "komet-parsed", "extract_rev": 1, "group_id": group_id}})

    p = _payload(conn, "komet-parsed")
    assert [l["match_basis"] for l in p["links"]] == ["ref-item"]


def test_a_basic_udi_match_is_not_relabelled_by_the_marker(conn):
    """The marker describes where the REF LIST came from. A Basic UDI-DI match
    is a different key and keeps its own, stronger basis."""
    group_id = _seed_group(conn, canonical_manufacturer="KOMET",
                           basic_udi_di="++E2265330681")
    _seed_member(conn, group_id, "314 H1 006")
    fields = _fields()
    fields["basic_udi_di"] = {"value": "++E2265330681", "conf": 0.99, "tier": "T0",
                              "verbatim": "x", "page": 1}
    fields["ref_list"] = {"value": ["H1.314.006"], "conf": 0.95, "tier": "T0",
                          "verbatim": "row 3", "page": None, "source": "coverage-map"}
    _seed(conn, "komet-udi", fields)

    vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "komet-udi", "extract_rev": 1, "group_id": group_id}})

    p = _payload(conn, "komet-udi")
    assert [l["match_basis"] for l in p["links"]] == ["basic-udi-di"]


# --------------------------------------------------------------------------- #
# Run-level reporting ([validate-report]). "Skipped rows, missed fields ...
# counted and reported, never silent" (CLAUDE.md) applied to VALIDATE: a
# suppression used to leave `reason` in the result and nothing countable, so
# "7 of 22 documents were suppressed, 6 for no regulation and 1 an MSDS" could
# only be reconstructed by reading every job by hand.
# --------------------------------------------------------------------------- #

def test_suppressed_for_missing_evidence_counts_the_field(conn):
    _seed(conn, "h-noreg", _fields(regulation=_ev(None)))
    out = vh.handle_validate_doc(
        conn, {"id": 1, "payload": {"content_hash": "h-noreg", "extract_rev": 1,
                                    "group_id": None}})

    assert out["emitted"] is False
    assert out["counts"]["suppressed"] == 1
    assert out["counts"]["suppressed_incomplete_evidence"] == 1
    assert out["counts"]["suppressed_missing_regulation"] == 1
    assert "regulation" in out["notes"][0]


def test_suppressed_without_an_extraction_is_counted(conn):
    out = vh.handle_validate_doc(
        conn, {"id": 1, "payload": {"content_hash": "h-nothing", "extract_rev": 1,
                                    "group_id": None}})

    assert out["counts"]["suppressed"] == 1
    assert out["counts"]["suppressed_no_extraction"] == 1


def test_emitted_candidate_counts_its_flags(conn):
    # group-scope doc with no ref_list and no basic_udi_di -> no-item-identifier
    _seed(conn, "h-flagged", _fields(ref_list=_ev([]), basic_udi_di=_ev(None)))
    out = vh.handle_validate_doc(
        conn, {"id": 1, "payload": {"content_hash": "h-flagged", "extract_rev": 1,
                                    "group_id": None}})

    assert out["emitted"] is True
    assert out["counts"]["emitted"] == 1
    assert out["counts"]["flagged"] == 1
    assert out["counts"]["flag_no_item_identifier"] == 1
    assert "no-item-identifier" in out["notes"][0]


# --------------------------------------------------------------------------- #
# C4 device-enumeration guard (Denis ruling 2026-08-24, "Guard + clean 310").
# A QMS/QA-system certificate whose own text enumerates the devices it covers
# is not manufacturer-wide, whatever its coverage_scope field says: doc 310
# (Kiwa Cermet MED 31385) names exactly three device types with model codes
# and was C16-machine-bound to 356 GC items anyway. VALIDATE reads the stored
# document_text and flags the binding candidate; GATE refuses the machine
# bind on that flag (tests in test_gate_candidate_handler.py).
# --------------------------------------------------------------------------- #

from tests.fixtures import qa_cert_texts as qa  # noqa: E402


def _seed_text(conn, content_hash, content, source="pdf-text"):
    conn.execute(
        "INSERT INTO document_text (content_hash, source, engine, pages, chars, content) "
        "VALUES (%s, %s, 'pymupdf', 3, %s, %s)",
        (content_hash, source, len(content or ""), content or ""))


def _mfr_scope_fields():
    return _fields(coverage_scope=_ev("manufacturer"), ref_list=_ev([]),
                   basic_udi_di=_ev(None))


# Every shape here is a MEASURED stored text (tests/fixtures/qa_cert_texts.py
# names the live doc_id each came from). The detector was designed from these,
# and the blast radius over all 809 stored texts on 2026-08-25 was exactly one
# document: doc 310. These rows pin that boundary.
ENUMERATION_SHAPES = [
    # doc 310 annex: model labels + code lists + per-device class lines + the
    # restriction clause — the defect shape, must cap.
    ("kiwa-enumerating-annex", qa.KIWA_ENUMERATING, True),
    # the restriction clause alone still asserts the limitation, even if the
    # annex pages lost their text layer.
    ("kiwa-restriction-only", qa.KIWA_RESTRICTION_ONLY, True),
    # doc 816: scope paragraph only — the genuinely manufacturer-wide shape.
    ("carl-martin-scope-only", qa.CARL_MARTIN_SCOPE_ONLY, False),
    # doc 138: device FAMILIES with one Risk Classification header, no codes —
    # a line-wide QA certificate legitimately printing its schedule.
    ("gc-device-family-schedule", qa.GC_DEVICE_SCHEDULE, False),
    # doc 420: EMDN CATEGORY codes cover whole families, not models.
    ("komet-emdn-categories", qa.KOMET_EMDN_PRODUCTS, False),
    ("no-text", "", False),
    ("none", None, False),
]


@pytest.mark.parametrize("name,text,fires",
                         ENUMERATION_SHAPES, ids=[s[0] for s in ENUMERATION_SHAPES])
def test_enumeration_detector_pins_the_measured_corpus_shapes(name, text, fires):
    assert vh._enumerates_devices(text) is fires


def test_an_enumerating_qa_cert_flags_its_binding_candidate(conn):
    _seed(conn, "vh-enum", _mfr_scope_fields())
    _seed_text(conn, "vh-enum", qa.KIWA_ENUMERATING)

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-enum", "extract_rev": 1, "group_id": None}})

    assert res["emitted"] is True and res["route"] == "mfr-binding"
    p = _payload(conn, "vh-enum")
    assert p["route"] == "mfr-binding"
    assert "device-enumeration" in p.get("flags", [])
    assert res["counts"]["flag_device_enumeration"] == 1


def test_a_scope_only_qms_cert_binds_unflagged(conn):
    # The Carl Martin shape keeps today's behaviour: no enumeration, no flag.
    _seed(conn, "vh-scope-only", _mfr_scope_fields())
    _seed_text(conn, "vh-scope-only", qa.CARL_MARTIN_SCOPE_ONLY)

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-scope-only", "extract_rev": 1, "group_id": None}})

    assert res["route"] == "mfr-binding"
    assert "device-enumeration" not in _payload(conn, "vh-scope-only").get("flags", [])


# --------------------------------------------------------------------------- #
# Extraction-shape flags on the manufacturer-binding route.
#
# `tiers.integrity_flags` was computed AFTER the mfr-binding return, so neither
# flag it raises could ever reach a manufacturer-scope candidate. That is the
# entire population `iso-without-standard-number` was written for: a QMS or
# notified-body certificate is manufacturer-scope by definition, and docs 248,
# 260 and 316 -- the three the 2026-09-03 design named as ISO/MDR
# contradictions -- carry `"flags": []` on their stored validate.doc results
# because of it. `ref-list-possibly-truncated` was unreachable there for the
# same reason. Both stay informational (gate.INFORMATIONAL_FLAGS); this only
# makes them countable. `[iso-flag-unreachable-on-mfr-binding]`.
# --------------------------------------------------------------------------- #


def _iso_no_standard_fields():
    """Manufacturer-scope ISO whose evidence never names 13485 or 9001.

    Doc 316's real shape: a notified body's "EU Quality Management System
    Certificate (MDR)", typed ISO by T1, carrying an MDR regulation and no ISO
    standard number anywhere.
    """
    return _fields(
        type={"value": "ISO", "conf": 0.95, "tier": "T1",
              "verbatim": "EU Quality Management System Certificate (MDR)",
              "page": 1, "model_id": "m"},
        regulation=_ev("MDR"),
        coverage_scope=_ev("manufacturer"),
        ref_list=_ev([]),
        basic_udi_di=_ev(None),
    )


def test_the_iso_flag_reaches_a_manufacturer_scope_candidate(conn):
    _seed(conn, "vh-iso-bind", _iso_no_standard_fields())

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-iso-bind", "extract_rev": 1, "group_id": None}})

    assert res["emitted"] is True and res["route"] == "mfr-binding"
    assert tiers.ISO_WITHOUT_STANDARD_FLAG in res["flags"]
    p = _payload(conn, "vh-iso-bind")
    assert tiers.ISO_WITHOUT_STANDARD_FLAG in p.get("flags", [])
    assert res["counts"]["flag_iso_without_standard_number"] == 1


def test_a_manufacturer_scope_iso_naming_its_standard_binds_unflagged(conn):
    # The other side of the same rule: a real ISO 13485 certificate names its
    # standard, so nothing fires and the binding candidate is unchanged.
    fields = _iso_no_standard_fields()
    fields["type"] = {"value": "ISO", "conf": 0.95, "tier": "T1",
                      "verbatim": "Certificate of Registration -- ISO 13485:2016",
                      "page": 1, "model_id": "m"}
    _seed(conn, "vh-iso-real", fields)

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-iso-real", "extract_rev": 1, "group_id": None}})

    assert res["route"] == "mfr-binding"
    assert tiers.ISO_WITHOUT_STANDARD_FLAG not in res["flags"]
    assert tiers.ISO_WITHOUT_STANDARD_FLAG not in _payload(
        conn, "vh-iso-real").get("flags", [])


def test_binding_route_keeps_both_flag_families_together(conn):
    # A manufacturer-scope doc can carry an extraction-shape flag AND the
    # scope-specific one at once; neither displaces the other.
    fields = _iso_no_standard_fields()
    _seed(conn, "vh-iso-enum", fields)
    _seed_text(conn, "vh-iso-enum", qa.KIWA_ENUMERATING)

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-iso-enum", "extract_rev": 1, "group_id": None}})

    flags = _payload(conn, "vh-iso-enum").get("flags", [])
    assert "device-enumeration" in flags
    assert tiers.ISO_WITHOUT_STANDARD_FLAG in flags


def test_a_binding_candidate_with_no_stored_text_stays_unflagged(conn):
    # No document_text row at all: nothing to measure, keep today's behaviour
    # (fail-open, documented — the guard is text-based by design).
    _seed(conn, "vh-notext", _mfr_scope_fields())

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-notext", "extract_rev": 1, "group_id": None}})

    assert res["route"] == "mfr-binding"
    assert "device-enumeration" not in _payload(conn, "vh-notext").get("flags", [])


def test_a_scanned_cert_with_no_text_layer_stays_unflagged(conn):
    # source='none' (a scan): the stored content is empty by contract, and an
    # empty text must never read as an enumeration. Live example: doc 255.
    _seed(conn, "vh-scan", _mfr_scope_fields())
    _seed_text(conn, "vh-scan", "", source="none")

    res = vh.handle_validate_doc(conn, {"payload": {
        "content_hash": "vh-scan", "extract_rev": 1, "group_id": None}})

    assert res["route"] == "mfr-binding"
    assert "device-enumeration" not in _payload(conn, "vh-scan").get("flags", [])
