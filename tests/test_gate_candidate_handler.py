"""gate.candidate — two-level disposition (handbook §7). ONE of only two registry
writers (inv. 1). Hand-crafted candidate payloads per the done-criterion.

Tests don't commit (conn fixture rolls back). Score comes from the seeded
extraction's (calibrated) confidences; ref_gate/flags/links come from the
payload. C6 supersession persistence ([gate-c6] ruling, 2026-07-31) is covered
near the bottom of this file — both the gate.candidate production path and the
gate.apply approve (human) path.
"""

from __future__ import annotations

import psycopg
import pytest

from app.extract import tiers
from app.extract.tiers import write_extraction_attempt
from app.handlers import gate as gh


def _ev(value, conf=0.97):
    return {"value": value, "conf": conf, "tier": "T1", "verbatim": f"v {value}",
            "page": 1, "model_id": "m"}


def _fields(conf=0.97, **overrides):
    base = {
        "type": _ev("DoC", conf),
        "regulation": _ev("MDR", conf),
        "coverage_scope": _ev("group", conf),
        "validity_to": _ev("2027-10-30", conf),
        "cert_number": _ev("ABC-1", conf),
        "basic_udi_di": _ev(None, conf),   # legitimately absent on this doc
    }
    base.update(overrides)
    return base


def _seed_ext(conn, content_hash, fields, rev=1):
    write_extraction_attempt(conn, content_hash, ["T0", "T1"], fields, extract_rev=rev)


def _seed_item(conn, item_ref, manufacturer="ACME"):
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, catalogue, mirror_rev, updated_at) "
        "VALUES (%s, 'n', %s, 'LJ', 1, now()) ON CONFLICT (item_ref) DO NOTHING",
        (item_ref, manufacturer),
    )


def _job(content_hash, *, rev=1, group_id=7, archive_url="file:///a.pdf", jid=1, **payload):
    p = {"content_hash": content_hash, "extract_rev": rev, "group_id": group_id,
         "archive_url": archive_url}
    p.update(payload)
    return {"id": jid, "type": "gate.candidate", "payload": p,
            "dedupe_key": f"gate:{content_hash}:{rev}:{group_id}"}


def _doc_status(conn, content_hash):
    r = conn.execute("SELECT status FROM document WHERE content_hash=%s", (content_hash,)).fetchone()
    return r["status"] if r else None


# --- disposition table (handbook §7) --------------------------------------------
DISPOSITIONS = [
    (0.97, True,  [],                          "production", "production"),
    (0.97, False, [],                          "staged",     "staged"),   # no ref gate
    (0.80, True,  [],                          "staged",     "staged"),   # >=MED, <HIGH
    (0.60, True,  [],                          "manual",     "staged"),   # <MED
    (0.97, True,  ["older-than-current"],      "manual",     "staged"),   # blocking flag again (fix round 1); auto-file fast path is covered separately below
    (0.97, True,  ["downgrade-uncomparable"],  "staged",     "staged"),   # non-blocking cap
    (0.97, True,  ["no-item-identifier"],      "manual",     "staged"),   # [validate-itemid]: never silently stage
]


@pytest.mark.parametrize("conf,ref_gate,flags,disp,doc_status", DISPOSITIONS)
def test_disposition(conn, conf, ref_gate, flags, disp, doc_status):
    ch = f"gc-{conf}-{ref_gate}-{'_'.join(flags) or 'none'}"
    _seed_ext(conn, ch, _fields(conf=conf))
    res = gh.handle_gate_candidate(conn, _job(ch, ref_gate=ref_gate, flags=flags, links=[]))
    assert res["disposition"] == disp
    assert _doc_status(conn, ch) == doc_status


@pytest.mark.parametrize("bad_value", ["2025", "2026-04", "12 February 2026", "n.a."])
def test_a_badly_shaped_date_cannot_kill_the_write(conn, bad_value):
    # Second line of defence behind VALIDATE's `date-insane`. GATE also runs on
    # the REPLAY path over `extraction_attempt` rows written before that rule
    # existed, and a candidate routed to `manual` is still INSERTED -- so the
    # flag alone never prevented this. Straumann IFU 7e33a160 returned
    # validity_from="2025" off a copyright line and killed the job with
    # `InvalidDatetimeFormat: invalid input syntax for type date: "2025"`,
    # then retried into it four more times and dead-lettered (2026-08-17).
    ch = f"gc-baddate-{bad_value}"
    _seed_ext(conn, ch, _fields(conf=0.97, validity_from=_ev(bad_value)))

    res = gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[]))

    assert res["doc_id"] is not None                      # the write SURVIVED
    row = conn.execute("SELECT validity_from, validity_to FROM document "
                       "WHERE content_hash=%s", (ch,)).fetchone()
    assert row["validity_from"] is None                   # refused, not coerced to junk
    assert row["validity_to"] is not None                 # the parseable one is untouched


def test_a_good_date_still_reaches_the_column(conn):
    # The mutation guard: returning None unconditionally would pass the test
    # above and silently empty every validity date in the registry.
    ch = "gc-gooddate"
    _seed_ext(conn, ch, _fields(conf=0.97, validity_from=_ev("2024-03-15")))

    gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[]))

    row = conn.execute("SELECT validity_from FROM document WHERE content_hash=%s",
                       (ch,)).fetchone()
    assert row["validity_from"].isoformat() == "2024-03-15"


def test_links_name_family_always_staged(conn):
    ch = "gc-links"
    _seed_ext(conn, ch, _fields(conf=0.97))
    _seed_item(conn, "IT-1")
    _seed_item(conn, "IT-2")
    res = gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[
        {"item_ref": "IT-1", "match_basis": "ref-list"},
        {"item_ref": "IT-2", "match_basis": "name-family"},
    ]))
    assert res["disposition"] == "production"
    links = {r["item_ref"]: r["status"] for r in conn.execute(
        "SELECT item_ref, status FROM item_document WHERE doc_id=%s", (res["doc_id"],)).fetchall()}
    assert links["IT-1"] == "production"   # trusted basis on a production doc, high score
    assert links["IT-2"] == "staged"       # name-family ALWAYS staged (C5), despite 0.97


def test_map_supplier_basis_reaches_production_at_link_level(conn):
    # Task 5 fix round 1 (CRITICAL): VALIDATE emits `map-supplier` for a
    # ref_list read off Komet's own coverage index rather than off the
    # document body. It is production-capable for the same structural reason
    # ref-list is (invariant 3) -- the manufacturer is fixed by the source
    # document before any article number is compared -- so a clean
    # map-supplier candidate must reach `production` AT THE LINK LEVEL, not
    # just at the document level.
    ch = "gc-map-supplier"
    _seed_ext(conn, ch, _fields(conf=0.97))
    _seed_item(conn, "IT-9")
    res = gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[
        {"item_ref": "IT-9", "match_basis": "map-supplier"},
    ]))
    assert res["disposition"] == "production"
    link = conn.execute(
        "SELECT status FROM item_document WHERE doc_id=%s AND item_ref='IT-9'",
        (res["doc_id"],)).fetchone()
    assert link["status"] == "production"


def test_reextraction_with_weaker_basis_never_poisons_a_production_link(conn):
    # inv. 3 provenance: once a link is production, a later candidate for the same
    # (item_ref, doc_id) carrying an untrusted basis (the REF list vanished at
    # rev 2, VALIDATE fell back to fetch-context) must keep the original basis.
    # Overwriting it would trip the item_document CHECK and dead-letter the job.
    ch = "gc-basis"
    _seed_ext(conn, ch, _fields(conf=0.97))
    _seed_item(conn, "IT-7")
    r1 = gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[
        {"item_ref": "IT-7", "match_basis": "ref-list"}]))

    _seed_ext(conn, ch, _fields(conf=0.97), rev=2)
    r2 = gh.handle_gate_candidate(conn, _job(ch, rev=2, ref_gate=False, flags=[], links=[
        {"item_ref": "IT-7", "match_basis": "fetch-context"}]))

    assert r2["doc_id"] == r1["doc_id"]
    link = conn.execute(
        "SELECT match_basis, status FROM item_document WHERE doc_id=%s AND item_ref='IT-7'",
        (r1["doc_id"],)).fetchone()
    assert link["status"] == "production"
    assert link["match_basis"] == "ref-list"


def test_reextraction_writes_new_evidence_at_new_rev_without_losing_old(conn):
    # [gate-evidence] ruling (Denis 2026-07-31): a rev-2 re-extraction that
    # corrects a field must not leave the (now-changed) document value backed
    # by rev-1 evidence. evidence.extract_rev (migration 014) lets both rows
    # exist; "current" evidence for a field is the row with MAX(extract_rev).
    ch = "gc-rev"
    _seed_ext(conn, ch, _fields(conf=0.97, validity_to=_ev("2027-10-30")), rev=1)
    r1 = gh.handle_gate_candidate(conn, _job(ch, rev=1, ref_gate=True, flags=[], links=[]))
    assert conn.execute("SELECT validity_to FROM document WHERE doc_id=%s",
                        (r1["doc_id"],)).fetchone()["validity_to"].isoformat() == "2027-10-30"

    _seed_ext(conn, ch, _fields(conf=0.97, validity_to=_ev("2028-01-15")), rev=2)
    r2 = gh.handle_gate_candidate(conn, _job(ch, rev=2, ref_gate=True, flags=[], links=[]))
    assert r2["doc_id"] == r1["doc_id"]

    doc = conn.execute("SELECT validity_to FROM document WHERE doc_id=%s",
                       (r1["doc_id"],)).fetchone()
    assert doc["validity_to"].isoformat() == "2028-01-15"   # current value is rev 2's

    rows = {r["extract_rev"]: r for r in conn.execute(
        "SELECT extract_rev, value, verbatim FROM evidence "
        "WHERE doc_id=%s AND field='validity_to' ORDER BY extract_rev",
        (r1["doc_id"],)).fetchall()}
    assert set(rows) == {1, 2}                         # rev 1's evidence is kept, not lost
    assert rows[1]["value"] == "2027-10-30"
    assert rows[2]["value"] == "2028-01-15"             # current doc value has matching evidence
    assert rows[2]["verbatim"] == "v 2028-01-15"


def test_c5_check_rejects_production_name_family_link(conn):
    # belt-and-braces: even a raw insert can't make a name-family link production.
    _seed_item(conn, "IT-9")
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, archive_url, status) "
        "VALUES ('DoC','MDR','group','h-c5','file:///x','production') RETURNING doc_id"
    ).fetchone()["doc_id"]
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
            "VALUES ('IT-9', %s, 'name-family', 'production')", (doc_id,))


def test_evidence_written_for_present_fields_only(conn):
    ch = "gc-ev"
    _seed_ext(conn, ch, _fields(conf=0.97))
    res = gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[]))
    ev = {r["field"]: r for r in conn.execute(
        "SELECT field, value, verbatim, tier, confidence, archive_url FROM evidence WHERE doc_id=%s",
        (res["doc_id"],)).fetchall()}
    assert ev["type"]["value"] == "DoC"
    assert ev["type"]["archive_url"] == "file:///a.pdf"
    assert ev["type"]["verbatim"] == "v DoC"
    assert "basic_udi_di" not in ev   # null value -> no evidence row (inv. 2 is about written values)


def test_stated_class_reaches_the_column_with_its_evidence(conn):
    """Migration 032. GATE re-reads `extraction_attempt` itself, so nothing on
    the candidate payload changes and VALIDATE is untouched -- the field simply
    rides inside `fields` and lands like any other written value."""
    ch = "gc-class"
    _seed_ext(conn, ch, _fields(
        conf=0.97,
        stated_class={"value": "IIa", "conf": 0.95, "tier": "T0",
                      "verbatim": "Class: IIa", "page": None}))

    res = gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[]))

    doc = conn.execute("SELECT stated_class FROM document WHERE content_hash=%s",
                       (ch,)).fetchone()
    assert doc["stated_class"] == "IIa"
    ev = conn.execute(
        "SELECT value, tier FROM evidence WHERE doc_id=%s AND field='stated_class'",
        (res["doc_id"],)).fetchone()
    assert ev["value"] == "IIa" and ev["tier"] == "T0"


def test_a_pageless_t0_stated_class_does_not_block_production(conn):
    """The [evidence-page] ruling covers T0, and `stated_class` is T0-only --
    so the page check that bars a pageless T1 value must not fire here. If it
    did, adding a display field would have quietly stopped every document
    carrying one from reaching production."""
    ch = "gc-class-nopage"
    _seed_ext(conn, ch, _fields(
        conf=0.97,
        stated_class={"value": "IIb", "conf": 0.95, "tier": "T0",
                      "verbatim": "Klasse IIb", "page": None}))

    res = gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[]))

    assert res["disposition"] == "production"
    assert _doc_status(conn, ch) == "production"


def test_stated_class_never_enters_the_score():
    """`_score` min()s over exactly `REQUIRED_FIELDS`. A display field inside
    that tuple would drag every document's score down to its own confidence and
    silently re-dispose the whole registry."""
    assert "stated_class" not in gh.REQUIRED_FIELDS


@pytest.mark.parametrize("conf,ref_gate,flags,disp,doc_status", DISPOSITIONS)
def test_stated_class_changes_no_disposition(conn, conf, ref_gate, flags, disp, doc_status):
    """The behavioural pin behind the assertion above: the whole disposition
    table again, with a stated_class present. Identical candidates with and
    without the field must dispose identically -- a QA value must never move a
    document between production, staged and manual."""
    ch = f"gc-cls-{conf}-{ref_gate}-{'_'.join(flags) or 'none'}"
    _seed_ext(conn, ch, _fields(
        conf=conf,
        stated_class={"value": "III", "conf": 0.95, "tier": "T0",
                      "verbatim": "Class III", "page": None}))

    res = gh.handle_gate_candidate(conn, _job(ch, ref_gate=ref_gate, flags=flags, links=[]))

    assert res["disposition"] == disp
    assert _doc_status(conn, ch) == doc_status


def test_a_re_extraction_corrects_a_stated_class(conn):
    """In the always-refresh group of the upsert, beside type/regulation: a
    later reading of the same bytes should correct the class, and the evidence
    rows are per-rev and append-only so the earlier reading is never lost."""
    ch = "gc-class-refresh"
    _seed_ext(conn, ch, _fields(conf=0.97, stated_class={
        "value": "IIa", "conf": 0.95, "tier": "T0", "verbatim": "Class: IIa", "page": None}))
    gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[]))

    _seed_ext(conn, ch, _fields(conf=0.97, stated_class={
        "value": "IIb", "conf": 0.95, "tier": "T0", "verbatim": "Class: IIb", "page": None}),
        rev=2)
    res = gh.handle_gate_candidate(conn, _job(ch, rev=2, ref_gate=True, flags=[], links=[]))

    doc = conn.execute("SELECT stated_class FROM document WHERE content_hash=%s",
                       (ch,)).fetchone()
    assert doc["stated_class"] == "IIb"
    values = {r["extract_rev"]: r["value"] for r in conn.execute(
        "SELECT extract_rev, value FROM evidence WHERE doc_id=%s AND field='stated_class'",
        (res["doc_id"],)).fetchall()}
    assert values == {1: "IIa", 2: "IIb"}     # append-only: rev 1 survives


def test_t1_evidence_without_page_blocks_production(conn):
    """CHANGED 2026-08-13; this test used to assert a hard error and no document.

    The [evidence-page] ruling (Denis 2026-07-31) stands: T1/T2 evidence must
    carry a page, T0/T3 are exempt. What changed is the CONSEQUENCE. Raising
    dead-lettered the whole document for one missing integer -- 2 of 130 GC
    documents, roughly 18 corpus-wide -- stranding the archived file and every
    other extracted field on the dead-jobs board. Invariant 2 governs what a
    PRODUCTION value must carry, so the gap bars production and routes to review
    instead of destroying the candidate."""
    ch = "gc-nopage"
    f = _fields(conf=0.97)
    f["regulation"] = {"value": "MDR", "conf": 0.97, "tier": "T1",
                       "verbatim": "v MDR", "model_id": "m"}   # T1, no page
    _seed_ext(conn, ch, f)
    res = gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[]))
    assert res["disposition"] == "manual"
    assert _doc_status(conn, ch) == "staged"   # archived and reviewable, not lost


def test_t0_evidence_without_page_is_fine(conn):
    ch = "gc-t0-nopage"
    f = _fields(conf=0.97)
    f["type"] = {"value": "DoC", "conf": 1.0, "tier": "T0",
                 "verbatim": "filename: DoC_x.pdf"}            # T0, pageless: OK
    _seed_ext(conn, ch, f)
    res = gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[]))
    assert res["disposition"] == "production"


def test_incomplete_evidence_hard_errors_and_writes_nothing(conn):
    ch = "gc-bad"
    f = _fields(conf=0.97)
    f["regulation"] = {"value": "MDR", "conf": 0.97, "page": 1}  # no verbatim/tier -> incomplete
    _seed_ext(conn, ch, f)
    with pytest.raises(ValueError):
        gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[]))
    assert _doc_status(conn, ch) is None   # nothing written


def test_manual_disposition_creates_manual_task(conn):
    ch = "gc-manual"
    _seed_ext(conn, ch, _fields(conf=0.60))   # below MED -> manual
    res = gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], group_id=88, links=[]))
    assert res["disposition"] == "manual"
    mt = conn.execute(
        "SELECT kind, doc_id, group_id, payload, status FROM manual_task WHERE doc_id=%s",
        (res["doc_id"],)).fetchone()
    assert mt["kind"] == "gate-manual"
    assert mt["group_id"] == 88
    assert mt["status"] == "open"
    assert _doc_status(conn, ch) == "staged"   # doc still written, as staged


def test_production_write_audits_with_self_contained_snapshot(conn):
    ch = "gc-audit"
    _seed_ext(conn, ch, _fields(conf=0.97))
    job = _job(ch, ref_gate=True, flags=[], links=[], jid=555)
    res = gh.handle_gate_candidate(conn, job)
    a = conn.execute(
        "SELECT event, decided_by, via_job, job_snapshot FROM audit_log WHERE doc_id=%s",
        (res["doc_id"],)).fetchone()
    assert a["event"] == "production-write"
    assert a["decided_by"] == "gate"
    assert a["via_job"] == 555
    assert a["job_snapshot"]["type"] == "gate.candidate"          # C8: self-contained
    assert a["job_snapshot"]["payload"]["content_hash"] == ch


def test_mfr_binding_produces_exactly_one_entry(conn):
    ch = "gc-mfr"
    _seed_ext(conn, ch, _fields(conf=0.97, coverage_scope=_ev("manufacturer"),
                                ref_list=_ev([]), basic_udi_di=_ev(None)))
    job = _job(ch, route="mfr-binding", manufacturer="ACME")
    r1 = gh.handle_gate_candidate(conn, job)
    r2 = gh.handle_gate_candidate(conn, job)   # re-delivery
    assert r1["doc_id"] == r2["doc_id"]
    tasks = conn.execute(
        "SELECT count(*) c FROM manual_task WHERE doc_id=%s AND kind='gate-manual'",
        (r1["doc_id"],)).fetchone()["c"]
    assert tasks == 1                                  # one review entry (§7b)
    assert _doc_status(conn, ch) == "staged"
    assert conn.execute("SELECT count(*) c FROM evidence WHERE doc_id=%s",
                        (r1["doc_id"],)).fetchone()["c"] >= 1


def _seed_alias(conn, raw_name, canonical_name):
    conn.execute(
        "INSERT INTO manufacturer_alias (raw_name, canonical_name) VALUES (%s, %s) "
        "ON CONFLICT (raw_name) DO NOTHING", (raw_name, canonical_name))


def _seed_md_item(conn, item_ref, manufacturer):
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, catalogue, mirror_rev, updated_at) "
        "VALUES (%s,'n',%s,TRUE,'LJ',1,now()) ON CONFLICT (item_ref) DO NOTHING",
        (item_ref, manufacturer))


def _seed_declassified_item(conn, item_ref, manufacturer):
    """`md_flag = FALSE`: BC said outright that this is not a medical device.

    Distinct from `_seed_unclassified_item` below, and the distinction is the
    whole of `[mfr-bind-empty-class]`. INGEST treats md_flag as tri-state
    (`app/handlers/ingest.py:164-170`) and only ever writes FALSE for an item
    BC has DECLASSIFIED -- a brand-new non-MD row is not mirrored at all."""
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, catalogue, mirror_rev, updated_at) "
        "VALUES (%s,'n',%s,FALSE,'LJ',1,now()) ON CONFLICT (item_ref) DO NOTHING",
        (item_ref, manufacturer))


def _seed_unclassified_item(conn, item_ref, manufacturer):
    """`md_flag IS NULL`: BC's device-class column is blank for this row.

    11.693 of the 15.958 mirrored items were in this state on 2026-08-27, and
    ZERO were FALSE -- so on today's data this is what "no MD item" always
    means."""
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, catalogue, mirror_rev, updated_at) "
        "VALUES (%s,'n',%s,NULL,'LJ',1,now()) ON CONFLICT (item_ref) DO NOTHING",
        (item_ref, manufacturer))


def _mfr_fields(name, conf=0.95):
    return _fields(conf=0.97, coverage_scope=_ev("manufacturer"), ref_list=_ev([]),
                   basic_udi_di=_ev(None), manufacturer=_ev(name, conf))


def _task_count(conn, doc_id):
    return conn.execute(
        "SELECT count(*) c FROM manual_task WHERE doc_id=%s AND kind='gate-manual' AND status='open'",
        (doc_id,)).fetchone()["c"]


def _links(conn, doc_id):
    return {(r["item_ref"], r["match_basis"], r["status"]) for r in conn.execute(
        "SELECT item_ref, match_basis, status FROM item_document WHERE doc_id=%s",
        (doc_id,)).fetchall()}


def test_mfr_binding_auto_binds_when_the_name_resolves_to_one_manufacturer(conn):
    # C4 §7b was human-gated end to end, which made the queue the bottleneck for
    # a decision with nothing in it to decide: an alias hit is a lookup in a
    # curated table, not a judgement. Denis's ruling 2026-08-17 -- "if we found
    # the manufacturer, the cert should relate to the manufacturer anyway".
    # Auto-bind fans out over EVERY code under the canonical (IVOCLAR is 001,
    # 005 and 275 live), which is the whole reason the name is resolved first.
    ch = "gc-bind-auto"
    _seed_ext(conn, ch, _mfr_fields("Ivoclar Vivadent AG", conf=0.95))
    _seed_alias(conn, "001", "IVOCLAR")
    _seed_alias(conn, "005", "IVOCLAR")
    _seed_alias(conn, "Ivoclar Vivadent AG", "IVOCLAR")
    _seed_alias(conn, "077", "KOMET")
    _seed_md_item(conn, "B-1", "001")
    _seed_md_item(conn, "B-2", "005")
    _seed_md_item(conn, "B-9", "077")

    r = gh.handle_gate_candidate(conn, _job(ch, route="mfr-binding"))

    assert r["disposition"] == "mfr-bound"
    assert _doc_status(conn, ch) == "production"
    assert _links(conn, r["doc_id"]) == {("B-1", "mfr-scope", "production"),
                                         ("B-2", "mfr-scope", "production")}
    assert _task_count(conn, r["doc_id"]) == 0        # nothing left for a human
    events = {a["event"] for a in conn.execute(
        "SELECT event FROM audit_log WHERE doc_id=%s", (r["doc_id"],)).fetchall()}
    assert "bind-manufacturer" in events and "production-write" in events


def test_auto_bind_refuses_a_manufacturer_scope_that_was_merely_defaulted(conn):
    """`derive_coverage_scope` returns manufacturer at confidence 0.6 for a DoC
    whose REF list T0 could not parse -- "T0 finding no REF list does NOT mean
    the document has none", with the comment already naming this exact risk: "a
    wrong `manufacturer` links the doc to the entire catalogue". A real ISO/EC
    scope arrives at 1.0, so the field's own confidence separates the two.

    KOMET is where this bites: its declarations put a product NAME in the column
    labelled REF, so T0 parses no codes, every document defaults to manufacturer
    scope, and C16 would bind each one to all 258 KOMET devices unreviewed."""
    ch = "gc-bind-defaulted-scope"
    f = _mfr_fields("Ivoclar Vivadent AG")
    f["type"] = _ev("DoC")
    f["coverage_scope"] = _ev("manufacturer", 0.6)      # the T0 default
    _seed_ext(conn, ch, f)
    _seed_alias(conn, "001", "IVOCLAR")
    _seed_alias(conn, "Ivoclar Vivadent AG", "IVOCLAR")
    _seed_md_item(conn, "D-1", "001")

    r = gh.handle_gate_candidate(conn, _job(ch, route="mfr-binding"))

    assert r["disposition"] == "mfr-binding"
    assert _doc_status(conn, ch) == "staged"
    assert _links(conn, r["doc_id"]) == set()
    assert _task_count(conn, r["doc_id"]) == 1


def test_auto_bind_holds_scope_to_high_not_med(conn):
    """Pins `high` rather than `med` for coverage_scope, which the 0.6 case
    above cannot do (0.6 fails both). 0.90 is a real reading -- a T1/T2 that
    re-decided the scope rather than a T0 fallback -- and it still does not bind,
    because the write it authorises is catalogue-wide and reversible only by a
    reject cascade. 0.90 is also exactly where the corpus's non-MD declarations
    sit, so the band between MED and HIGH is populated, not hypothetical."""
    ch = "gc-bind-scope-090"
    f = _mfr_fields("Ivoclar Vivadent AG")
    f["coverage_scope"] = _ev("manufacturer", 0.90)
    _seed_ext(conn, ch, f)
    _seed_alias(conn, "001", "IVOCLAR")
    _seed_alias(conn, "Ivoclar Vivadent AG", "IVOCLAR")
    _seed_md_item(conn, "H-1", "001")

    r = gh.handle_gate_candidate(conn, _job(ch, route="mfr-binding"))

    assert r["disposition"] == "mfr-binding"
    assert _links(conn, r["doc_id"]) == set()


def test_auto_bind_accepts_a_scope_the_document_actually_states(conn):
    # The ISO/EC path: derive_coverage_scope returns 1.0 on `type=ISO`, because
    # a QMS certificate IS manufacturer-wide by construction rather than by
    # failure to find something.
    ch = "gc-bind-real-scope"
    f = _mfr_fields("Ivoclar Vivadent AG")
    f["type"] = _ev("ISO")
    f["regulation"] = _ev("n.a.")
    f["coverage_scope"] = _ev("manufacturer", 1.0)
    _seed_ext(conn, ch, f)
    _seed_alias(conn, "001", "IVOCLAR")
    _seed_alias(conn, "Ivoclar Vivadent AG", "IVOCLAR")
    _seed_md_item(conn, "S-1", "001")

    r = gh.handle_gate_candidate(conn, _job(ch, route="mfr-binding"))

    assert r["disposition"] == "mfr-bound"
    assert len(_links(conn, r["doc_id"])) == 1


def test_auto_bind_refuses_a_document_carrying_no_medical_device_regulation(conn):
    """Doc 1494 live: a PrograMill PM5 MILLING UNIT declaration, cited under
    "2006/42/EG Machinery Directive, 2014/53/EU Radio Equipment", extracted at
    manufacturer scope -- so C16 bound it to all 1.069 IVOCLAR medical-device
    items at production, 2026-08-17 09:05:26.

    C16 checked that the MANUFACTURER resolves and never that the DOCUMENT is a
    medical-device document at all. A machinery or cosmetics declaration is a
    real document about a real product; it is simply not evidence of anything
    for a ceramic block, and manufacturer scope makes the mistake catalogue-wide
    rather than local."""
    ch = "gc-bind-nonmd"
    f = _mfr_fields("Ivoclar Vivadent AG")
    f["type"] = _ev("DoC")
    f["regulation"] = _ev("n.a.")
    _seed_ext(conn, ch, f)
    _seed_alias(conn, "001", "IVOCLAR")
    _seed_alias(conn, "Ivoclar Vivadent AG", "IVOCLAR")
    _seed_md_item(conn, "M-1", "001")

    r = gh.handle_gate_candidate(conn, _job(ch, route="mfr-binding"))

    assert r["disposition"] == "mfr-binding"          # review, not a bind
    assert _doc_status(conn, ch) == "staged"
    assert _links(conn, r["doc_id"]) == set()
    assert _task_count(conn, r["doc_id"]) == 1


@pytest.mark.parametrize("regulation", ["MDR", "MDD"])
def test_auto_bind_accepts_the_medical_device_regulations(conn, regulation):
    ch = f"gc-bind-{regulation}"
    f = _mfr_fields("Ivoclar Vivadent AG")
    f["regulation"] = _ev(regulation)
    _seed_ext(conn, ch, f)
    _seed_alias(conn, "001", "IVOCLAR")
    _seed_alias(conn, "Ivoclar Vivadent AG", "IVOCLAR")
    _seed_md_item(conn, f"R-{regulation}", "001")

    r = gh.handle_gate_candidate(conn, _job(ch, route="mfr-binding"))

    assert r["disposition"] == "mfr-bound"
    assert len(_links(conn, r["doc_id"])) == 1


def test_auto_bind_accepts_a_qms_certificate_with_no_regulation(conn):
    """The exemption that keeps C16 useful. ISO 13485 is a quality-management
    standard, not an instrument issued under MDR or MDD, so `n.a.` is the
    CORRECT reading -- and a QMS certificate genuinely does cover the
    manufacturer's whole line, which is the case manufacturer-scope binding
    exists for. Live examples: doc 1472 (IVOCLAR, 1.069 items) and doc 117 (GC,
    356), both correctly bound. Refusing every `n.a.` would have retired the
    feature on the day it shipped."""
    ch = "gc-bind-iso"
    f = _mfr_fields("Ivoclar Vivadent AG")
    f["type"] = _ev("ISO")
    f["regulation"] = _ev("n.a.")
    _seed_ext(conn, ch, f)
    _seed_alias(conn, "001", "IVOCLAR")
    _seed_alias(conn, "Ivoclar Vivadent AG", "IVOCLAR")
    _seed_md_item(conn, "Q-1", "001")

    r = gh.handle_gate_candidate(conn, _job(ch, route="mfr-binding"))

    assert r["disposition"] == "mfr-bound"
    assert len(_links(conn, r["doc_id"])) == 1


def test_auto_bind_refuses_an_ec_certificate_that_names_no_regulation(conn):
    """Narrower than the ISO exemption on purpose. An EC/UKCA certificate is
    issued UNDER a regime and should name it; one that does not has been read
    incompletely, and a reviewer can tell in seconds. Live: doc 116, GC's UKCA
    certificate under "Part II of The Medical Devices Regulations 2002" --
    genuinely a device regime, and exactly the judgement a person should make
    rather than a tuple."""
    ch = "gc-bind-ec-na"
    f = _mfr_fields("Ivoclar Vivadent AG")
    f["type"] = _ev("EC")
    f["regulation"] = _ev("n.a.")
    _seed_ext(conn, ch, f)
    _seed_alias(conn, "001", "IVOCLAR")
    _seed_alias(conn, "Ivoclar Vivadent AG", "IVOCLAR")
    _seed_md_item(conn, "E-1", "001")

    r = gh.handle_gate_candidate(conn, _job(ch, route="mfr-binding"))

    assert r["disposition"] == "mfr-binding"
    assert _task_count(conn, r["doc_id"]) == 1


def test_auto_bind_refuses_a_cert_that_enumerates_its_devices(conn):
    """Doc 310 live: Kiwa Cermet EC QA certificate MED 31385, whose technical
    annex names EXACTLY three device types with model codes and whose page 3
    reads "valid only for the above mentioned Medical Devices" -- C16
    machine-bound it to all 356 GC production items anyway (Denis ruling
    2026-08-24: "Guard + clean 310"). VALIDATE now reads the stored text and
    puts `device-enumeration` on the binding candidate; the machine bind must
    refuse it and leave the decision to a person. `gate.apply
    bind-manufacturer` is deliberately untouched -- a human may still bind an
    enumerating certificate if they judge the enumeration to be its whole
    catalogue."""
    ch = "gc-bind-enum"
    f = _mfr_fields("GC EUROPE N.V.")
    f["type"] = _ev("EC")
    f["regulation"] = _ev("MDD")
    _seed_ext(conn, ch, f)
    _seed_alias(conn, "008", "GC")
    _seed_alias(conn, "GC EUROPE N.V.", "GC")
    _seed_md_item(conn, "G-1", "008")

    r = gh.handle_gate_candidate(
        conn, _job(ch, route="mfr-binding", flags=["device-enumeration"]))

    assert r["disposition"] == "mfr-binding"          # review, not a bind
    assert _doc_status(conn, ch) == "staged"
    assert _links(conn, r["doc_id"]) == set()
    assert _task_count(conn, r["doc_id"]) == 1
    # the review task names the reason, so the queue explains itself
    task = conn.execute(
        "SELECT payload FROM manual_task WHERE doc_id=%s AND kind='gate-manual'",
        (r["doc_id"],)).fetchone()
    assert "device-enumeration" in task["payload"]["flags"]


def test_device_enumeration_caps_but_never_blocks_on_the_normal_route(conn):
    # Classified CAPPING: if the flag ever rides a normal-route candidate, it
    # holds the document at `staged` for review -- never production (the whole
    # point), never the manual queue (a reviewer can settle it).
    assert "device-enumeration" in gh.CAPPING_FLAGS
    assert "device-enumeration" not in gh.BLOCKING_FLAGS
    assert "device-enumeration" not in gh.INFORMATIONAL_FLAGS


def test_auto_bind_closes_the_review_task_it_no_longer_needs(conn):
    # Measured on the live registry after the C16 replay, 2026-08-17: 10 tasks
    # stayed OPEN against documents that had already left staging (6 production,
    # 4 filed). The registry was right and the queue lied -- which is the exact
    # problem C16 exists to remove.
    ch = "gc-bind-task"
    _seed_ext(conn, ch, _mfr_fields("Ivoclar Vivadent AG"))
    _seed_alias(conn, "001", "IVOCLAR")
    _seed_alias(conn, "Ivoclar Vivadent AG", "IVOCLAR")
    _seed_md_item(conn, "T-1", "001")

    r1 = gh.handle_gate_candidate(conn, _job(ch, route="mfr-binding"))   # -> review
    assert _task_count(conn, r1["doc_id"]) == 1 or r1["disposition"] == "mfr-bound"

    # A document already carrying an open task (staged on an earlier revision,
    # or by an earlier build) must have it closed when the machine binds it.
    conn.execute(
        "INSERT INTO manual_task (kind, doc_id, payload) VALUES ('gate-manual', %s, '{}')",
        (r1["doc_id"],))
    r2 = gh.handle_gate_candidate(conn, _job(ch, route="mfr-binding", jid=2))

    assert r2["disposition"] == "mfr-bound"
    assert _task_count(conn, r2["doc_id"]) == 0
    assert conn.execute(
        "SELECT resolved_by FROM manual_task WHERE doc_id=%s", (r2["doc_id"],)
    ).fetchone()["resolved_by"] == "gate"


def test_auto_bind_to_filed_also_closes_its_task(conn):
    ch = "gc-bind-task-filed"
    _seed_ext(conn, ch, _mfr_fields("3Shape A/S"))
    _seed_alias(conn, "10003", "3SHAPE A/S")
    # An explicitly DECLASSIFIED item, added 2026-08-27 with
    # `[mfr-bind-empty-class]`. This test is about closing the task on the
    # filed path, not about how filing is decided -- and since that ruling,
    # "no MD item" alone no longer files: an all-unclassified manufacturer
    # stages instead. Without this row the case under test stops existing.
    _seed_declassified_item(conn, "3S-NOT-MD", "10003")
    r = gh.handle_gate_candidate(conn, _job(ch, route="mfr-binding"))
    conn.execute(
        "INSERT INTO manual_task (kind, doc_id, payload) VALUES ('gate-manual', %s, '{}')",
        (r["doc_id"],))

    r2 = gh.handle_gate_candidate(conn, _job(ch, route="mfr-binding", jid=2))

    assert r2["disposition"] == "filed"
    assert _task_count(conn, r2["doc_id"]) == 0


def test_filing_an_out_of_catalogue_doc_closes_its_task_too(conn):
    # Pre-existing gap, NOT introduced by C16: handle_gate_candidate never
    # resolved a task on any route -- gate.apply was the only caller. A document
    # that landed `manual` and later files on replay kept its task forever.
    ch = "gc-filed-task"
    _seed_ext(conn, ch, _fields(conf=0.97, ref_list=_ev([]), basic_udi_di=_ev(None)))
    doc_id = gh.handle_gate_candidate(conn, _job(ch, flags=["no-ref-overlap"], links=[]))["doc_id"]
    conn.execute(
        "INSERT INTO manual_task (kind, doc_id, payload) VALUES ('gate-manual', %s, '{}')",
        (doc_id,))

    r = gh.handle_gate_candidate(conn, _job(ch, flags=["no-ref-overlap"], links=[], jid=2))

    assert r["disposition"] == "filed"
    assert _task_count(conn, doc_id) == 0


def test_a_staged_disposition_leaves_its_task_open(conn):
    # The resolver must be scoped to dispositions that need nobody. A staged
    # document is precisely the case a human still has to settle.
    ch = "gc-staged-task"
    _seed_ext(conn, ch, _fields(conf=0.80))
    doc_id = gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[]))["doc_id"]
    conn.execute(
        "INSERT INTO manual_task (kind, doc_id, payload) VALUES ('gate-manual', %s, '{}')",
        (doc_id,))

    r = gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[], jid=2))

    assert r["disposition"] == "staged"
    assert _task_count(conn, doc_id) == 1


def test_mfr_binding_is_idempotent_under_redelivery(conn):
    ch = "gc-bind-idem"
    _seed_ext(conn, ch, _mfr_fields("Ivoclar Vivadent AG"))
    _seed_alias(conn, "001", "IVOCLAR")
    _seed_alias(conn, "Ivoclar Vivadent AG", "IVOCLAR")
    _seed_md_item(conn, "I-1", "001")

    r1 = gh.handle_gate_candidate(conn, _job(ch, route="mfr-binding"))
    r2 = gh.handle_gate_candidate(conn, _job(ch, route="mfr-binding", jid=2))

    assert r1["doc_id"] == r2["doc_id"]
    assert len(_links(conn, r1["doc_id"])) == 1
    n = conn.execute(
        "SELECT count(*) c FROM audit_log WHERE doc_id=%s AND event='production-write'",
        (r1["doc_id"],)).fetchone()["c"]
    assert n == 1


def test_mfr_binding_files_a_known_manufacturer_we_stock_no_devices_from(conn):
    # 3SHAPE live: the name resolves cleanly, and the catalogue holds 197 of its
    # items with ZERO flagged as medical devices, so the bind is correct and
    # links nothing. Staging it parks an un-actionable row in the review queue
    # forever; C15's `filed` is exactly this shape -- read correctly, attributed
    # correctly, covering nothing we currently sell -- so it files instead.
    ch = "gc-bind-nodevices"
    _seed_ext(conn, ch, _mfr_fields("3Shape A/S"))
    _seed_alias(conn, "10003", "3SHAPE A/S")
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, catalogue, mirror_rev, updated_at) "
        "VALUES ('S-1','n','10003',FALSE,'LJ',1,now())")

    r = gh.handle_gate_candidate(conn, _job(ch, route="mfr-binding"))

    assert r["disposition"] == "filed"
    assert _doc_status(conn, ch) == "filed"
    assert _links(conn, r["doc_id"]) == set()
    assert _task_count(conn, r["doc_id"]) == 0
    assert conn.execute("SELECT count(*) c FROM evidence WHERE doc_id=%s",
                        (r["doc_id"],)).fetchone()["c"] >= 1    # still evidenced


def test_mfr_binding_reviews_a_name_that_resolves_to_nothing(conn):
    # No alias hit means we have NOT found the manufacturer, whatever the model's
    # confidence says. Falling back to the raw string here would bind on a
    # spelling nobody has ratified.
    ch = "gc-bind-unknown"
    _seed_ext(conn, ch, _mfr_fields("Nobody Dental GmbH"))
    _seed_alias(conn, "001", "IVOCLAR")
    _seed_md_item(conn, "N-1", "001")

    r = gh.handle_gate_candidate(conn, _job(ch, route="mfr-binding"))

    assert r["disposition"] == "mfr-binding"
    assert _doc_status(conn, ch) == "staged"
    assert _links(conn, r["doc_id"]) == set()
    assert _task_count(conn, r["doc_id"]) == 1


def test_mfr_binding_reviews_an_ambiguous_name(conn):
    # Normalization is what makes ambiguity reachable: two raw_names that fold to
    # the same key under DIFFERENT canonicals. Binding either one would be a
    # coin flip over every MD item of a manufacturer, so it goes to a human.
    ch = "gc-bind-ambig"
    _seed_ext(conn, ch, _mfr_fields("Acme AG"))
    _seed_alias(conn, "Acme AG", "ACME NORTH")
    _seed_alias(conn, "acme  ag", "ACME SOUTH")
    _seed_md_item(conn, "A-1", "Acme AG")

    r = gh.handle_gate_candidate(conn, _job(ch, route="mfr-binding"))

    assert r["disposition"] == "mfr-binding"
    assert _doc_status(conn, ch) == "staged"
    assert _links(conn, r["doc_id"]) == set()
    assert _task_count(conn, r["doc_id"]) == 1


def test_mfr_binding_reviews_when_the_name_was_read_with_low_confidence(conn):
    ch = "gc-bind-lowconf"
    _seed_ext(conn, ch, _mfr_fields("Ivoclar Vivadent AG", conf=0.40))
    _seed_alias(conn, "001", "IVOCLAR")
    _seed_alias(conn, "Ivoclar Vivadent AG", "IVOCLAR")
    _seed_md_item(conn, "L-1", "001")

    r = gh.handle_gate_candidate(conn, _job(ch, route="mfr-binding"))

    assert r["disposition"] == "mfr-binding"
    assert _doc_status(conn, ch) == "staged"
    assert _task_count(conn, r["doc_id"]) == 1


def test_mfr_binding_prefers_the_payload_name_over_the_extracted_one(conn):
    # VALIDATE canonicalizes before it emits; when it has done so its answer wins,
    # and the extracted string is the fallback for candidates whose payload
    # predates identity extraction (5 of the 14 live binding tasks carry no
    # manufacturer at all).
    ch = "gc-bind-payload"
    _seed_ext(conn, ch, _mfr_fields("Ivoclar Vivadent AG"))
    _seed_alias(conn, "077", "KOMET")
    _seed_alias(conn, "001", "IVOCLAR")
    _seed_md_item(conn, "P-1", "077")
    _seed_md_item(conn, "P-9", "001")

    r = gh.handle_gate_candidate(conn, _job(ch, route="mfr-binding", manufacturer="KOMET"))

    assert _links(conn, r["doc_id"]) == {("P-1", "mfr-scope", "production")}


def test_archive_url_sourced_from_fetch_log_when_absent_from_payload(conn):
    # The extract->validate->gate chain doesn't thread archive_url; the fetch
    # ledger (backfill writes it) is the source of truth for the stored copy.
    ch = "gc-fl"
    _seed_ext(conn, ch, _fields(conf=0.97))
    conn.execute(
        "INSERT INTO fetch_log (url_normalized, content_hash, source, fetched_at, last_checked_at) "
        "VALUES ('/corpus/x.pdf', %s, 'backfill', now(), now())", (ch,))
    job = {"id": 1, "type": "gate.candidate", "payload": {
        "content_hash": ch, "extract_rev": 1, "group_id": 7,
        "ref_gate": True, "flags": [], "links": []}}   # NB: no archive_url

    res = gh.handle_gate_candidate(conn, job)

    assert res["disposition"] == "production"
    assert conn.execute("SELECT archive_url FROM document WHERE doc_id=%s",
                        (res["doc_id"],)).fetchone()["archive_url"] == "/corpus/x.pdf"
    assert conn.execute("SELECT archive_url FROM evidence WHERE doc_id=%s AND field='type'",
                        (res["doc_id"],)).fetchone()["archive_url"] == "/corpus/x.pdf"


def test_a_candidate_without_a_handle_keeps_the_stored_local_archive_url(conn):
    # Measured on the RENFERT smoke test (2026-08-24): doc 843 was written with
    # our stored copy's handle (/archive/RENFERT/...), then a second group's C3
    # candidate for the same hash arrived with NO archive_url key. GATE's
    # fetch_log fallback served the remote SOURCE url and the upsert + evidence
    # heal refreshed document.archive_url AND every evidence row to
    # https://www.renfert.com/... — exactly what invariant 2 and end-state
    # goal 2 forbid. A candidate with no handle is not authority to refresh:
    # the registry's existing handle wins over the ledger fallback.
    ch = "gc-keep-handle"
    local = "/archive/RENFERT/unknown/d82e8f794d0c__CONF_6100x000.pdf"
    _seed_ext(conn, ch, _fields(conf=0.97))
    gh.handle_gate_candidate(conn, _job(ch, archive_url=local,
                                        ref_gate=True, flags=[], links=[]))
    # the live fetch ledgered the remote source url for the same content
    conn.execute(
        "INSERT INTO fetch_log (url_normalized, content_hash, source, fetched_at, last_checked_at) "
        "VALUES ('https://www.renfert.com/CONF_6100x000.pdf', %s, 'live', now(), now())", (ch,))

    job2 = _job(ch, jid=2, group_id=8, ref_gate=False, flags=[], links=[])
    del job2["payload"]["archive_url"]                      # C3 payload without a handle
    res = gh.handle_gate_candidate(conn, job2)

    assert conn.execute("SELECT archive_url FROM document WHERE doc_id=%s",
                        (res["doc_id"],)).fetchone()["archive_url"] == local
    urls = [r["archive_url"] for r in conn.execute(
        "SELECT archive_url FROM evidence WHERE doc_id=%s", (res["doc_id"],)).fetchall()]
    assert urls and all(u == local for u in urls)


def test_a_field_read_from_another_file_keeps_that_file_as_its_handle(conn):
    """Komet issues a declaration whose product list is a separate annex file,
    so the declaration's `ref_list` is read off `..._RA_812_Liste_DoC.pdf` while
    every other field is read off `..._RA_810_DoC_EU_SIGNED.pdf`. Invariant 2
    asks where a value was READ: sending a reviewer to the declaration to check
    a list it does not contain is a false handle, not a rounding error."""
    ch = "komet-companion"
    annex = "/archive/KOMET/doc/beef__532624_RA_812_Liste_DoC.pdf"
    fields = _fields(conf=0.97)
    fields["ref_list"] = {"value": ["H1.314.006"], "conf": 0.9, "tier": "T0",
                          "verbatim": "1 REF code", "page": 1,
                          "archive_url": annex}
    _seed_ext(conn, ch, fields)
    job = {"id": 1, "type": "gate.candidate", "payload": {
        "content_hash": ch, "extract_rev": 1, "group_id": 7,
        "archive_url": "/archive/KOMET/doc/dead__532624_RA_810_DoC_EU_SIGNED.pdf",
        "ref_gate": True, "flags": [], "links": []}}

    res = gh.handle_gate_candidate(conn, job)

    rows = {r["field"]: r["archive_url"] for r in conn.execute(
        "SELECT field, archive_url FROM evidence WHERE doc_id=%s", (res["doc_id"],)).fetchall()}
    assert rows["ref_list"] == annex
    assert rows["type"].endswith("_RA_810_DoC_EU_SIGNED.pdf")
    # the document itself is still the declaration, not the annex
    assert conn.execute("SELECT archive_url FROM document WHERE doc_id=%s",
                        (res["doc_id"],)).fetchone()["archive_url"].endswith(
                            "_RA_810_DoC_EU_SIGNED.pdf")


def test_replaying_a_companion_field_does_not_heal_it_to_the_document(conn):
    """The handle-healing UPDATE exists because 731 GC evidence rows pointed at
    a corpus source path. It rewrites a row's handle to the document's on every
    replay -- which would erase a companion handle on the second delivery of the
    same revision, silently, long after anyone was watching."""
    ch = "komet-replay"
    annex = "/archive/KOMET/doc/beef__532624_RA_812_Liste_DoC.pdf"
    fields = _fields(conf=0.97)
    fields["ref_list"] = {"value": ["H1.314.006"], "conf": 0.9, "tier": "T0",
                          "verbatim": "1 REF code", "page": 1,
                          "archive_url": annex}
    _seed_ext(conn, ch, fields)
    payload = {"content_hash": ch, "extract_rev": 1, "group_id": 7,
               "archive_url": "/archive/KOMET/doc/dead__532624_RA_810_DoC_EU_SIGNED.pdf",
               "ref_gate": True, "flags": [], "links": []}

    res = gh.handle_gate_candidate(conn, {"id": 1, "type": "gate.candidate", "payload": payload})
    gh.handle_gate_candidate(conn, {"id": 2, "type": "gate.candidate", "payload": payload})

    assert conn.execute(
        "SELECT archive_url FROM evidence WHERE doc_id=%s AND field='ref_list'",
        (res["doc_id"],)).fetchone()["archive_url"] == annex


# --------------------------------------------------------------------------- #
# C15 `filed` — covers nothing the catalogue holds
# --------------------------------------------------------------------------- #
def _ooc_job(ch, **over):
    p = {"content_hash": ch, "extract_rev": 1, "group_id": 7,
         "archive_url": "/archive/x.pdf", "ref_gate": False,
         "flags": ["no-ref-overlap"], "links": []}
    p.update(over)
    return {"id": 1, "type": "gate.candidate", "payload": p}


def test_an_all_unclassified_manufacturer_stages_rather_than_files(conn):
    """[mfr-bind-empty-class], Denis 2026-08-27: a blank BC device-class column
    means UNKNOWN, not "not a device", so the document must stay reviewable.

    Filing on a blank column is filing on absence of data, and on 2026-08-27 it
    was absence of MOST of the data: 11.693 of 15.958 mirrored items had a NULL
    md_flag and ZERO had FALSE. The case this protects is 3SHAPE -- 6 ISO/EC
    certificates against 197 items carrying no class at all -- and it grows
    with VOCO (292 PDFs), DENSTPLY (405) and STRAUMANN (13), every one of which
    has no MD-flagged item for the same reason.

    The manufacturer still RESOLVED; that is what makes this different from
    `test_an_unresolved_manufacturer_is_not_filed`. We know whose document it
    is and cannot yet say whether they sell us devices."""
    ch = "gc-bind-unclassified"
    _seed_ext(conn, ch, _mfr_fields("3Shape A/S"))
    _seed_alias(conn, "10003", "3SHAPE A/S")
    _seed_unclassified_item(conn, "3S-BLANK", "10003")

    r = gh.handle_gate_candidate(conn, _job(ch, route="mfr-binding"))

    assert r["disposition"] != "filed"
    assert _doc_status(conn, ch) == "staged"
    # A reviewer has to be told WHY this is in front of them, or the queue
    # grows by an outcome nobody can act on.
    assert _task_count(conn, r["doc_id"]) == 1


def test_a_manufacturer_bc_has_declassified_still_files(conn):
    """The other side of the same ruling, and the reason it is not simply
    "never file". `md_flag = FALSE` is BC saying outright that the item is not
    a medical device -- INGEST only ever writes it for an item BC has
    DECLASSIFIED (`ingest.py:164-170`), never for a blank. That is a positive
    statement, not a gap, and filing on it is correct.

    Zero rows are FALSE today, so this path is unreachable on live data and
    exists for the moment the class column is populated. Without it the ruling
    would retire C16 filing permanently rather than suspend it."""
    ch = "gc-bind-declassified"
    _seed_ext(conn, ch, _mfr_fields("3Shape A/S"))
    _seed_alias(conn, "10003", "3SHAPE A/S")
    _seed_declassified_item(conn, "3S-NO", "10003")

    r = gh.handle_gate_candidate(conn, _job(ch, route="mfr-binding"))

    assert r["disposition"] == "filed"
    assert _doc_status(conn, ch) == "filed"


def test_one_md_item_still_wins_over_any_number_of_unclassified_ones(conn):
    """Coverage is the deliverable: a single MD-flagged item is production, and
    the new staging branch must not shadow it. Mixed manufacturers are the
    normal case, not an edge one."""
    ch = "gc-bind-mixed"
    _seed_ext(conn, ch, _mfr_fields("3Shape A/S"))
    _seed_alias(conn, "10003", "3SHAPE A/S")
    _seed_unclassified_item(conn, "3S-BLANK-1", "10003")
    _seed_unclassified_item(conn, "3S-BLANK-2", "10003")
    _seed_md_item(conn, "3S-REAL", "10003")

    r = gh.handle_gate_candidate(conn, _job(ch, route="mfr-binding"))

    assert r["disposition"] == "mfr-bound"
    assert _doc_status(conn, ch) == "production"


def test_a_document_covering_nothing_we_stock_is_filed_not_staged(conn):
    """IVOCLAR's `Cention Forte, Cention Primer.pdf` names six REF codes, none
    of which exist in item_mirror by either column, with or without the WW
    market suffix -- Dentalia does not sell Cention. Across the backfill,
    Dentalia held 6 of the 1.605 codes the 181 staged documents named. `staged`
    means a human should confirm, and nobody can confirm coverage of a product
    the company does not sell."""
    ch = "gc-ooc"
    _seed_ext(conn, ch, _fields(conf=0.97))

    res = gh.handle_gate_candidate(conn, _ooc_job(ch))

    assert res["disposition"] == "filed"
    assert _doc_status(conn, ch) == "filed"


def test_filing_is_audited(conn):
    """Filing removes a document from human view, so "why is this not in the
    queue" must be answerable from the trail alone (invariant 10). The quietest
    outcome must not be the only unexplained one."""
    ch = "gc-ooc-audit"
    _seed_ext(conn, ch, _fields(conf=0.97))

    res = gh.handle_gate_candidate(conn, _ooc_job(ch))

    row = conn.execute(
        "SELECT event, decided_by FROM audit_log WHERE doc_id=%s", (res["doc_id"],)
    ).fetchone()
    assert (row["event"], row["decided_by"]) == ("filed", "gate")


def test_one_linkable_item_is_coverage_and_is_never_filed(conn):
    """Coverage is the deliverable. A single link means the document covers
    something we hold, and it must take the normal path -- 6 of those 1.605
    codes DID match, and filing their documents would lose real coverage."""
    ch = "gc-ooc-haslink"
    _seed_ext(conn, ch, _fields(conf=0.97))
    _seed_item(conn, "IT-9")

    res = gh.handle_gate_candidate(conn, _ooc_job(
        ch, links=[{"item_ref": "IT-9", "match_basis": "ref-list"}]))

    assert res["disposition"] != "filed"


def test_an_unresolved_manufacturer_is_not_filed(conn):
    """`no-ref-overlap` is the load-bearing signal: VALIDATE emits it only when
    the manufacturer DID resolve and no member matched. Empty links for any
    other reason means "we don't know what this is", which is a human's
    problem, not a filing."""
    ch = "gc-ooc-nomfr"
    _seed_ext(conn, ch, _fields(conf=0.97))

    res = gh.handle_gate_candidate(conn, _ooc_job(ch, flags=["manufacturer-unresolved"]))

    assert res["disposition"] != "filed"
    assert _doc_status(conn, ch) != "filed"


def test_a_manufacturer_scope_certificate_is_not_filed(conn):
    """ISO 13485 / QMS certificates name no item BY DESIGN and have C4's path:
    one-time human binding, then derivation at `mfr-scope`. Filing them would
    silently retire that queue -- and Denis ruled 2026-08-14 that brand-wide and
    company documents are kept and linked, not shelved."""
    ch = "gc-ooc-mfrscope"
    _seed_ext(conn, ch, _fields(conf=0.97, coverage_scope=_ev("manufacturer"),
                                type=_ev("ISO"), regulation=_ev("n.a.")))

    res = gh.handle_gate_candidate(conn, _ooc_job(ch))

    assert res["disposition"] != "filed"


def test_a_blocking_flag_still_wins_over_filing(conn):
    """Filing is for documents that are RIGHT and merely irrelevant to us. A
    blocking flag means something is wrong, and wrong outranks irrelevant."""
    ch = "gc-ooc-blocked"
    _seed_ext(conn, ch, _fields(conf=0.97))

    res = gh.handle_gate_candidate(conn, _ooc_job(
        ch, flags=["no-ref-overlap", "date-insane"]))

    assert res["disposition"] == "manual"


def test_a_badly_read_document_goes_to_a_human_not_to_the_shelf(conn):
    """Below MED the document is not trustworthy enough to call irrelevant --
    its REF list may be what was misread. Filing it would hide the failure."""
    ch = "gc-ooc-lowscore"
    _seed_ext(conn, ch, _fields(conf=0.10))

    res = gh.handle_gate_candidate(conn, _ooc_job(ch))

    assert res["disposition"] == "manual"


def test_a_filed_document_can_still_become_production(conn):
    """`filed` is terminal, not permanent: if the catalogue gains the item the
    document must be able to move up. Only production and superseded are
    protected from downgrade."""
    ch = "gc-ooc-promote"
    _seed_ext(conn, ch, _fields(conf=0.97))
    gh.handle_gate_candidate(conn, _ooc_job(ch))
    assert _doc_status(conn, ch) == "filed"
    _seed_item(conn, "IT-11")

    res = gh.handle_gate_candidate(conn, {"id": 2, "type": "gate.candidate", "payload": {
        "content_hash": ch, "extract_rev": 1, "group_id": 7,
        "archive_url": "/archive/x.pdf", "ref_gate": True, "flags": [],
        "links": [{"item_ref": "IT-11", "match_basis": "ref-list"}]}})

    assert res["disposition"] == "production"
    assert _doc_status(conn, ch) == "production"


# --------------------------------------------------------------------------- #
# a superseded reading writes nothing at all
# --------------------------------------------------------------------------- #
def test_a_stale_rev_does_not_overwrite_the_current_documents_fields(conn):
    """The link writes were already guarded by `_is_current_rev`; the document
    upsert was not, so rev N-1 could still rewrite type/regulation/dates through
    ON CONFLICT DO UPDATE -- the same corruption the link guard prevents, one
    table over."""
    ch = "gc-stale"
    _seed_ext(conn, ch, _fields(conf=0.97, type=_ev("DoC"), regulation=_ev("MDR")), rev=1)
    _seed_ext(conn, ch, _fields(conf=0.97, type=_ev("EC"), regulation=_ev("MDD")), rev=2)
    base = {"content_hash": ch, "group_id": 7, "archive_url": "/archive/x.pdf",
            "ref_gate": True, "flags": [], "links": []}
    current = gh.handle_gate_candidate(conn, {"id": 1, "type": "gate.candidate",
                                              "payload": {**base, "extract_rev": 2}})

    res = gh.handle_gate_candidate(conn, {"id": 2, "type": "gate.candidate",
                                          "payload": {**base, "extract_rev": 1}})

    assert res["skipped"] == "superseded-rev"
    row = conn.execute("SELECT type, regulation FROM document WHERE doc_id=%s",
                       (current["doc_id"],)).fetchone()
    assert (row["type"], row["regulation"]) == ("EC", "MDD"), "rev 1 overwrote rev 2"


def test_a_stale_rev_cannot_dead_letter_on_a_value_a_newer_rev_replaced(conn):
    """The permanent-failure loop this closes. Two GC safety data sheets held
    `coverage_scope: "item"` in a rev-1 extraction predating the API schema
    enum. Re-extraction appended a clean rev, but the stale candidate kept
    replaying the stored value into an INSERT the CHECK rejects -- failing on
    every attempt with no path to success (2026-08-14)."""
    ch = "gc-poison"
    _seed_ext(conn, ch, _fields(conf=0.97, coverage_scope=_ev("item")), rev=1)
    _seed_ext(conn, ch, _fields(conf=0.97, coverage_scope=_ev("group")), rev=2)
    payload = {"content_hash": ch, "extract_rev": 1, "group_id": 7,
               "archive_url": "/archive/x.pdf", "ref_gate": True,
               "flags": [], "links": []}

    res = gh.handle_gate_candidate(conn, {"id": 1, "type": "gate.candidate",
                                          "payload": payload})

    assert res["skipped"] == "superseded-rev"   # not a CheckViolation


def test_the_newest_rev_still_writes_normally(conn):
    """The guard must not turn every candidate into a no-op."""
    ch = "gc-newest"
    _seed_ext(conn, ch, _fields(conf=0.97), rev=1)
    res = gh.handle_gate_candidate(conn, {"id": 1, "type": "gate.candidate", "payload": {
        "content_hash": ch, "extract_rev": 1, "group_id": 7,
        "archive_url": "/archive/x.pdf", "ref_gate": True, "flags": [], "links": []}})

    assert "skipped" not in res
    assert res["disposition"] == "production"


# --------------------------------------------------------------------------- #
# source_url: the document's ORIGINAL filename, which nothing used to persist
# --------------------------------------------------------------------------- #
def test_source_url_is_persisted_with_its_original_punctuation(conn):
    """`document.source_url` had no writer, so it was NULL on all 141 live rows.

    That cost the true filename: the archive keeps only a sanitized form, and
    `gce_certification_MDR 778483.pdf` is stored as `..._MDR_778483.pdf`.
    Manufacturers identify their documents by exactly the space this test puts
    back, so the assertion is on the punctuation surviving, not merely on the
    column being non-null."""
    ch = "gc-src"
    _seed_ext(conn, ch, _fields(conf=0.97))
    conn.execute(
        "INSERT INTO fetch_log (url_normalized, content_hash, source, fetched_at, last_checked_at) "
        "VALUES ('/imports/GC/gce_certification_MDR 778483.pdf', %s, 'backfill', now(), now())",
        (ch,))
    job = {"id": 1, "type": "gate.candidate", "payload": {
        "content_hash": ch, "extract_rev": 1, "group_id": 7,
        "archive_url": "/archive/GC/abc__gce_certification_MDR_778483.pdf",
        "ref_gate": True, "flags": [], "links": []}}

    res = gh.handle_gate_candidate(conn, job)

    assert conn.execute("SELECT source_url FROM document WHERE doc_id=%s",
                        (res["doc_id"],)).fetchone()["source_url"] == \
        "/imports/GC/gce_certification_MDR 778483.pdf"


def test_a_document_with_no_ledger_row_still_gates(conn):
    """Denis's condition, 2026-08-14: provenance must never block a write.
    A document whose source we cannot name is still a document."""
    ch = "gc-src-none"
    _seed_ext(conn, ch, _fields(conf=0.97))
    job = {"id": 1, "type": "gate.candidate", "payload": {
        "content_hash": ch, "extract_rev": 1, "group_id": 7,
        "archive_url": "/archive/x.pdf",
        "ref_gate": True, "flags": [], "links": []}}   # no fetch_log row at all

    res = gh.handle_gate_candidate(conn, job)

    assert res["disposition"] == "production"
    assert conn.execute("SELECT source_url FROM document WHERE doc_id=%s",
                        (res["doc_id"],)).fetchone()["source_url"] is None


def test_a_redelivery_that_cannot_name_the_source_keeps_the_stored_one(conn):
    """Sticky on conflict, like supersedes/referenced_doc_id/cert_doc_id.
    The replay path re-runs GATE over stored extractions with no live ledger
    row guaranteed; it must not blank a name we already hold."""
    ch = "gc-src-sticky"
    _seed_ext(conn, ch, _fields(conf=0.97))
    conn.execute(
        "INSERT INTO fetch_log (url_normalized, content_hash, source, fetched_at, last_checked_at) "
        "VALUES ('/imports/GC/real name.pdf', %s, 'backfill', now(), now())", (ch,))
    job = {"id": 1, "type": "gate.candidate", "payload": {
        "content_hash": ch, "extract_rev": 1, "group_id": 7,
        "archive_url": "/archive/x.pdf", "ref_gate": True, "flags": [], "links": []}}
    first = gh.handle_gate_candidate(conn, job)

    conn.execute("DELETE FROM fetch_log WHERE content_hash=%s", (ch,))
    gh.handle_gate_candidate(conn, job)

    assert conn.execute("SELECT source_url FROM document WHERE doc_id=%s",
                        (first["doc_id"],)).fetchone()["source_url"] == \
        "/imports/GC/real name.pdf"


def test_the_first_place_we_saw_a_document_is_the_one_recorded(conn):
    """A hash may legitimately carry several ledger rows -- the same document
    re-dumped at a new path. Without an ORDER BY the answer is whatever the
    planner returns, so it can flip between runs on the same data."""
    ch = "gc-src-multi"
    _seed_ext(conn, ch, _fields(conf=0.97))
    conn.execute(
        "INSERT INTO fetch_log (url_normalized, content_hash, source, fetched_at, last_checked_at) "
        "VALUES ('/imports/GC/second.pdf', %s, 'backfill', now(), now()), "
        "       ('/imports/GC/first.pdf',  %s, 'backfill', now() - interval '1 day', now())",
        (ch, ch))
    job = {"id": 1, "type": "gate.candidate", "payload": {
        "content_hash": ch, "extract_rev": 1, "group_id": 7,
        "archive_url": "/archive/x.pdf", "ref_gate": True, "flags": [], "links": []}}

    res = gh.handle_gate_candidate(conn, job)

    assert conn.execute("SELECT source_url FROM document WHERE doc_id=%s",
                        (res["doc_id"],)).fetchone()["source_url"] == "/imports/GC/first.pdf"


def test_gate_candidate_without_any_archive_url_errors(conn):
    ch = "gc-noarch"
    _seed_ext(conn, ch, _fields(conf=0.97))
    job = {"id": 1, "type": "gate.candidate", "payload": {
        "content_hash": ch, "extract_rev": 1, "group_id": 7,
        "ref_gate": True, "flags": [], "links": []}}   # no archive_url, no fetch_log row
    with pytest.raises(ValueError):
        gh.handle_gate_candidate(conn, job)
    assert _doc_status(conn, ch) is None


def test_production_gate_is_idempotent(conn):
    ch = "gc-idem"
    _seed_ext(conn, ch, _fields(conf=0.97))
    _seed_item(conn, "IT-5")
    job = _job(ch, ref_gate=True, flags=[], links=[{"item_ref": "IT-5", "match_basis": "ref-list"}])
    r1 = gh.handle_gate_candidate(conn, job)
    r2 = gh.handle_gate_candidate(conn, job)   # at-least-once re-delivery
    assert r1["doc_id"] == r2["doc_id"]
    assert conn.execute("SELECT count(*) c FROM document WHERE content_hash=%s", (ch,)).fetchone()["c"] == 1
    assert conn.execute("SELECT count(*) c FROM item_document WHERE doc_id=%s", (r1["doc_id"],)).fetchone()["c"] == 1
    assert conn.execute("SELECT count(*) c FROM evidence WHERE doc_id=%s", (r1["doc_id"],)).fetchone()["c"] == 5
    assert conn.execute("SELECT count(*) c FROM audit_log WHERE doc_id=%s", (r1["doc_id"],)).fetchone()["c"] == 1


# --- C6 supersession persistence ([gate-c6] ruling, 2026-07-31) ------------------

def test_production_candidate_supersedes_prior_production_doc(conn):
    _seed_ext(conn, "gc-old", _fields(conf=0.97))
    r_old = gh.handle_gate_candidate(conn, _job("gc-old", ref_gate=True, flags=[], links=[]))

    _seed_ext(conn, "gc-new", _fields(conf=0.97))
    job_new = _job("gc-new", ref_gate=True, flags=[], links=[], jid=42, supersedes=r_old["doc_id"])
    r_new = gh.handle_gate_candidate(conn, job_new)

    old_doc = conn.execute(
        "SELECT status, superseded_by FROM document WHERE doc_id=%s", (r_old["doc_id"],)
    ).fetchone()
    assert old_doc["status"] == "superseded"
    assert old_doc["superseded_by"] == r_new["doc_id"]
    a = conn.execute(
        "SELECT decided_by, via_job, detail FROM audit_log "
        "WHERE doc_id=%s AND event='supersede'", (r_old["doc_id"],)
    ).fetchone()
    assert a["decided_by"] == "gate"
    assert a["via_job"] == 42
    assert a["detail"]["superseded_by"] == r_new["doc_id"]


def test_staged_candidate_does_not_supersede_until_gate_apply_approves(conn):
    _seed_ext(conn, "gc-old2", _fields(conf=0.97))
    r_old = gh.handle_gate_candidate(conn, _job("gc-old2", ref_gate=True, flags=[], links=[]))

    # score in [MED, HIGH) -> staged, not production
    _seed_ext(conn, "gc-new2", _fields(conf=0.80))
    r_new = gh.handle_gate_candidate(conn, _job(
        "gc-new2", ref_gate=True, flags=[], links=[], supersedes=r_old["doc_id"]))
    assert r_new["disposition"] == "staged"

    old_status = conn.execute(
        "SELECT status FROM document WHERE doc_id=%s", (r_old["doc_id"],)
    ).fetchone()["status"]
    assert old_status == "production"   # untouched while the new candidate is only staged
    stored_supersedes = conn.execute(
        "SELECT supersedes FROM document WHERE doc_id=%s", (r_new["doc_id"],)
    ).fetchone()["supersedes"]
    assert stored_supersedes == r_old["doc_id"]   # persisted, waiting for a human

    apply_job = {"id": 99, "type": "gate.apply", "payload": {
        "doc_id": r_new["doc_id"], "decision": "approve", "decided_by": "user:marta"}}
    gh.handle_gate_apply(conn, apply_job)

    old_doc = conn.execute(
        "SELECT status, superseded_by FROM document WHERE doc_id=%s", (r_old["doc_id"],)
    ).fetchone()
    assert old_doc["status"] == "superseded"
    assert old_doc["superseded_by"] == r_new["doc_id"]
    a = conn.execute(
        "SELECT decided_by, via_job FROM audit_log "
        "WHERE doc_id=%s AND event='supersede'", (r_old["doc_id"],)
    ).fetchone()
    assert a["decided_by"] == "user:marta"
    assert a["via_job"] == 99


def test_c6_mismatch_raises_and_leaves_old_doc_untouched(conn):
    _seed_ext(conn, "gc-old3", _fields(conf=0.97, regulation=_ev("MDR")))
    r_old = gh.handle_gate_candidate(conn, _job("gc-old3", ref_gate=True, flags=[], links=[]))

    # a payload claiming to supersede gc-old3 but with a different regulation —
    # should never happen from real VALIDATE output (it scopes its lookup by
    # (type, regulation)); simulates an upstream bug this belt-and-braces check
    # is meant to catch.
    _seed_ext(conn, "gc-new3", _fields(conf=0.97, regulation=_ev("MDD")))
    with pytest.raises(ValueError):
        gh.handle_gate_candidate(conn, _job(
            "gc-new3", ref_gate=True, flags=[], links=[], supersedes=r_old["doc_id"]))

    old_doc = conn.execute(
        "SELECT status, superseded_by FROM document WHERE doc_id=%s", (r_old["doc_id"],)
    ).fetchone()
    assert old_doc["status"] == "production"     # not corrupted
    assert old_doc["superseded_by"] is None
    assert conn.execute(
        "SELECT 1 FROM audit_log WHERE doc_id=%s AND event='supersede'", (r_old["doc_id"],)
    ).fetchone() is None


def test_supersession_redelivery_is_idempotent(conn):
    _seed_ext(conn, "gc-old4", _fields(conf=0.97))
    r_old = gh.handle_gate_candidate(conn, _job("gc-old4", ref_gate=True, flags=[], links=[]))
    _seed_ext(conn, "gc-new4", _fields(conf=0.97))
    job_new = _job("gc-new4", ref_gate=True, flags=[], links=[], supersedes=r_old["doc_id"])

    gh.handle_gate_candidate(conn, job_new)
    gh.handle_gate_candidate(conn, job_new)   # at-least-once re-delivery

    n = conn.execute(
        "SELECT count(*) c FROM audit_log WHERE doc_id=%s AND event='supersede'",
        (r_old["doc_id"],)
    ).fetchone()["c"]
    assert n == 1


def test_related_doc_id_persisted_regardless_of_disposition(conn):
    _seed_ext(conn, "gc-related-target", _fields(conf=0.97, regulation=_ev("MDD")))
    r_target = gh.handle_gate_candidate(conn, _job("gc-related-target", ref_gate=True, flags=[], links=[]))

    # staged (score in [MED, HIGH)) — related_doc_id (cross-regulation pair)
    # must still persist; only supersession itself waits for production.
    _seed_ext(conn, "gc-related", _fields(conf=0.80, regulation=_ev("MDR")))
    r = gh.handle_gate_candidate(conn, _job(
        "gc-related", ref_gate=True, flags=[], links=[], related_doc_id=r_target["doc_id"]))
    assert r["disposition"] == "staged"

    referenced = conn.execute(
        "SELECT referenced_doc_id FROM document WHERE doc_id=%s", (r["doc_id"],)
    ).fetchone()["referenced_doc_id"]
    assert referenced == r_target["doc_id"]


# --- task 5: auto-file unambiguously older documents ------------------------
# Fix round 1 (2026-08-07): the fast path to `superseded` is NARROW. VALIDATE
# emits superseded_by_doc_id AND keeps flag "older-than-current" (both, not
# either/or) whenever a candidate is unambiguously older. GATE only takes the
# fast path when THREE things hold: (1) superseded_by_doc_id present, (2) no
# OTHER blocking flag survives (no-item-identifier still forces manual — an
# old doc with no catalogue link is not "unambiguous", nothing established it
# covers the same subject at all), (3) validity_from's own calibrated
# confidence clears cfg.gate.high (0.92 by default) — validity_from isn't in
# REQUIRED_FIELDS/_score, so a garbled OCR year would otherwise sail through
# uncalibrated. Any guard failure falls through to the ORDINARY chain, where
# "older-than-current" (back in BLOCKING_FLAGS) forces `manual` exactly as it
# did before task 5 — never `production`, never a silent downgrade.
#
# This is the INVERSE of C6 (_apply_supersession): the incoming doc is OLDER,
# not newer, so the chain pointer is written directly rather than through that
# helper (which would re-check C6 in the wrong direction).

def _older_fields(validity_from_conf=0.97, **overrides):
    """Fields for an auto-supersede candidate: type/regulation/coverage_scope at
    the usual high confidence PLUS a validity_from field at the confidence the
    test wants to exercise (the field the round-1 guard actually reads)."""
    return _fields(validity_from=_ev("2020-01-01", validity_from_conf), **overrides)


def test_auto_superseded_candidate_files_without_a_manual_task(conn):
    newer = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, status, content_hash, "
        "archive_url, coverage_scope) VALUES ('DoC','MDR','2025-01-01','production',"
        "'h-newer','/archive/n.pdf','group') RETURNING doc_id"
    ).fetchone()["doc_id"]
    _seed_ext(conn, "h-older", _older_fields())
    out = gh.handle_gate_candidate(
        conn, _job("h-older", superseded_by_doc_id=newer,
                   flags=["older-than-current", "auto-superseded"], ref_gate=True, links=[]))
    assert out["disposition"] == "superseded"
    row = conn.execute(
        "SELECT status, superseded_by FROM document WHERE doc_id=%s", (out["doc_id"],)
    ).fetchone()
    assert row["status"] == "superseded"
    assert row["superseded_by"] == newer
    tasks = conn.execute(
        "SELECT count(*) AS n FROM manual_task WHERE doc_id=%s", (out["doc_id"],)
    ).fetchone()["n"]
    assert tasks == 0


def test_auto_superseded_still_writes_evidence(conn):
    newer = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, status, content_hash, "
        "archive_url, coverage_scope) VALUES ('DoC','MDR','2025-01-01','production',"
        "'h-newer2','/archive/n2.pdf','group') RETURNING doc_id"
    ).fetchone()["doc_id"]
    _seed_ext(conn, "h-older2", _older_fields())
    out = gh.handle_gate_candidate(
        conn, _job("h-older2", superseded_by_doc_id=newer,
                   flags=["older-than-current", "auto-superseded"], ref_gate=True, links=[]))
    n = conn.execute(
        "SELECT count(*) AS n FROM evidence WHERE doc_id=%s", (out["doc_id"],)
    ).fetchone()["n"]
    assert n > 0, "an auto-filed document is still archived and evidenced"


def test_auto_superseded_redelivery_is_idempotent(conn):
    newer = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, status, content_hash, "
        "archive_url, coverage_scope) VALUES ('DoC','MDR','2025-01-01','production',"
        "'h-newer3','/archive/n3.pdf','group') RETURNING doc_id"
    ).fetchone()["doc_id"]
    _seed_ext(conn, "h-older3", _older_fields())
    job = _job("h-older3", superseded_by_doc_id=newer,
               flags=["older-than-current", "auto-superseded"], ref_gate=True, links=[])
    out1 = gh.handle_gate_candidate(conn, job)
    gh.handle_gate_candidate(conn, job)   # at-least-once re-delivery
    n = conn.execute(
        "SELECT count(*) AS n FROM audit_log WHERE doc_id=%s AND event='auto-superseded'",
        (out1["doc_id"],)
    ).fetchone()["n"]
    assert n == 1, "redelivery is a no-op, same convention as _apply_supersession"
    row = conn.execute(
        "SELECT superseded_by FROM document WHERE doc_id=%s", (out1["doc_id"],)
    ).fetchone()
    assert row["superseded_by"] == newer


# --- fix round 1: the two safety holes, closed -------------------------------

def test_auto_superseded_still_blocked_by_no_item_identifier(conn):
    """CRITICAL 1: an OCR'd backfill doc can independently fail no-item-identifier
    (no ref_list/basic_udi_di extracted) AND be older than current. Before the
    fix, superseded_by_doc_id alone short-circuited straight past BLOCKING_FLAGS
    and filed it as `superseded` with zero links and no review — even though
    nothing ties it to the covered item at all. It must still go to `manual`."""
    newer = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, status, content_hash, "
        "archive_url, coverage_scope) VALUES ('DoC','MDR','2025-01-01','production',"
        "'h-newer4','/archive/n4.pdf','group') RETURNING doc_id"
    ).fetchone()["doc_id"]
    _seed_ext(conn, "h-older4", _older_fields())
    out = gh.handle_gate_candidate(
        conn, _job("h-older4", superseded_by_doc_id=newer,
                   flags=["older-than-current", "auto-superseded", "no-item-identifier"],
                   ref_gate=False, links=[]))
    assert out["disposition"] == "manual"
    row = conn.execute(
        "SELECT status, superseded_by FROM document WHERE doc_id=%s", (out["doc_id"],)
    ).fetchone()
    assert row["status"] != "superseded"
    assert row["superseded_by"] is None
    tasks = conn.execute(
        "SELECT count(*) AS n FROM manual_task WHERE doc_id=%s", (out["doc_id"],)
    ).fetchone()["n"]
    assert tasks == 1


def test_auto_superseded_blocked_by_low_validity_from_confidence(conn):
    """CRITICAL 2: validity_from drives an irreversible write but isn't in
    REQUIRED_FIELDS/_score, so nothing gated its own extraction quality. A
    garbled OCR year (low confidence) must not auto-file — it must go to
    `manual` like every other low-confidence candidate, so a human checks it."""
    newer = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, status, content_hash, "
        "archive_url, coverage_scope) VALUES ('DoC','MDR','2025-01-01','production',"
        "'h-newer5','/archive/n5.pdf','group') RETURNING doc_id"
    ).fetchone()["doc_id"]
    _seed_ext(conn, "h-older5", _older_fields(validity_from_conf=0.6))  # below cfg.gate.high (0.92)
    out = gh.handle_gate_candidate(
        conn, _job("h-older5", superseded_by_doc_id=newer,
                   flags=["older-than-current", "auto-superseded"], ref_gate=True, links=[]))
    assert out["disposition"] == "manual"
    row = conn.execute(
        "SELECT status, superseded_by FROM document WHERE doc_id=%s", (out["doc_id"],)
    ).fetchone()
    assert row["status"] != "superseded"
    assert row["superseded_by"] is None


def test_auto_superseded_missing_validity_from_confidence_also_blocks(conn):
    """The guard reads validity_from's own confidence directly (it's not in
    REQUIRED_FIELDS) — a payload that carries superseded_by_doc_id without a
    validity_from evidence entry at all must fail closed, not pass open."""
    newer = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, status, content_hash, "
        "archive_url, coverage_scope) VALUES ('DoC','MDR','2025-01-01','production',"
        "'h-newer6','/archive/n6.pdf','group') RETURNING doc_id"
    ).fetchone()["doc_id"]
    _seed_ext(conn, "h-older6", _fields())   # no validity_from key at all
    out = gh.handle_gate_candidate(
        conn, _job("h-older6", superseded_by_doc_id=newer,
                   flags=["older-than-current", "auto-superseded"], ref_gate=True, links=[]))
    assert out["disposition"] == "manual"


def test_auto_superseded_guard_failure_never_yields_production(conn):
    """Neither guard failure mode (blocking flag, or low/missing confidence) may
    ever let an older candidate land on `production` — that would be an actual
    downgrade, the exact thing invariant 4 forbids. manual is the only safe
    fallback, which is why older-than-current is back in BLOCKING_FLAGS."""
    for i, (extra_flags, conf) in enumerate([
        (["no-item-identifier"], 0.97),
        ([], 0.10),
    ]):
        newer = conn.execute(
            "INSERT INTO document (type, regulation, validity_from, status, content_hash, "
            "archive_url, coverage_scope) VALUES ('DoC','MDR','2025-01-01','production',"
            "%s,%s,'group') RETURNING doc_id",
            (f"h-newer-prod-guard-{i}", f"/archive/npg{i}.pdf"),
        ).fetchone()["doc_id"]
        ch = f"h-older-prod-guard-{i}"
        _seed_ext(conn, ch, _older_fields(validity_from_conf=conf))
        out = gh.handle_gate_candidate(
            conn, _job(ch, superseded_by_doc_id=newer,
                       flags=["older-than-current", "auto-superseded", *extra_flags],
                       ref_gate=True, links=[]))
        assert out["disposition"] != "production"
        assert _doc_status(conn, ch) != "production"


# --- cited-certificate resolution (task 4) ----------------------------------

def test_gate_persists_cert_doc_id(conn):
    cert_id = conn.execute(
        "INSERT INTO document (type, regulation, cert_number, status, content_hash, "
        "archive_url, coverage_scope) VALUES ('EC','MDR','C-1','production','h-c1',"
        "'/archive/c1.pdf','group') RETURNING doc_id"
    ).fetchone()["doc_id"]
    _seed_ext(conn, "h-d1", _fields(cert_number=_ev("C-1")))
    out = gh.handle_gate_candidate(
        conn, _job("h-d1", cert_doc_id=cert_id, ref_gate=True, links=[]))
    row = conn.execute(
        "SELECT cert_doc_id FROM document WHERE doc_id=%s", (out["doc_id"],)
    ).fetchone()
    assert row["cert_doc_id"] == cert_id


def test_new_certificate_backresolves_declarations_that_cited_it(conn):
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, cert_number, status, content_hash, "
        "archive_url, coverage_scope) VALUES ('DoC','MDR','C-9','staged','h-early',"
        "'/archive/e.pdf','group') RETURNING doc_id"
    ).fetchone()["doc_id"]
    _seed_ext(conn, "h-cert9", _fields(type=_ev("EC"), cert_number=_ev("C-9")))
    out = gh.handle_gate_candidate(
        conn, _job("h-cert9", jid=2, ref_gate=True, links=[]))
    row = conn.execute(
        "SELECT cert_doc_id FROM document WHERE doc_id=%s", (doc_id,)
    ).fetchone()
    assert row["cert_doc_id"] == out["doc_id"]


def test_a_new_certificate_backresolves_across_its_revision_suffix(conn):
    """The other direction of the 2026-08-19 ruling. Forward resolution
    (`validate._resolve_cited_certificate`) strips a trailing R-code from both
    sides; back-resolution has to strip it too, or the same pair matches or misses
    depending only on which document we happened to gate first -- the exact
    fetch-order dependence this back-resolution exists to remove."""
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, cert_number, status, content_hash, "
        "archive_url, coverage_scope) VALUES ('DoC','MDR','MDR 778483','staged',"
        "'h-early-rsuffix','/archive/e.pdf','group') RETURNING doc_id"
    ).fetchone()["doc_id"]
    _seed_ext(conn, "h-cert-rsuffix-gate",
              _fields(type=_ev("EC"), cert_number=_ev("MDR 778483 R000")))
    out = gh.handle_gate_candidate(
        conn, _job("h-cert-rsuffix-gate", jid=2, ref_gate=True, links=[]))
    row = conn.execute(
        "SELECT cert_doc_id FROM document WHERE doc_id=%s", (doc_id,)
    ).fetchone()
    assert row["cert_doc_id"] == out["doc_id"]


def test_a_certificate_backresolves_on_the_mfr_binding_route_too(conn):
    """The route every real certificate actually takes.

    The three tests above reach `_backresolve_citing_docs` through
    `handle_gate_candidate`'s MAIN path -- and no EC/ISO certificate has ever
    arrived that way. §7b routes a manufacturer-scope document (ISO 13485, EC
    certificate) to `_handle_mfr_binding`, and `handle_gate_candidate` returns
    there before it reaches the back-resolution call. So on live data the
    feature was dead: `cert_doc_id` stood at 2 of 769 documents, and both of
    those were set by VALIDATE's FORWARD resolver, never by back-resolution.

    Bound or unbound is irrelevant to this: holding the certificate is what
    answers the declaration that cited it, and `document_effective_expiry`
    already refuses to inherit from a certificate that is not
    production/superseded. So the binding decision must not gate it -- this
    test takes the UNBOUND path (no alias seeded, so `resolve_canonicals`
    finds nothing) precisely to pin that."""
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, cert_number, status, content_hash, "
        "archive_url, coverage_scope) VALUES ('DoC','MDR','MDR 778483','staged',"
        "'h-early-bindroute','/archive/e.pdf','group') RETURNING doc_id"
    ).fetchone()["doc_id"]
    f = _mfr_fields("GC EUROPE N.V.")
    f["type"] = _ev("EC")
    f["cert_number"] = _ev("MDR 778483 R000")
    _seed_ext(conn, "h-cert-bindroute", f)

    out = gh.handle_gate_candidate(
        conn, _job("h-cert-bindroute", jid=2, route="mfr-binding"))

    row = conn.execute(
        "SELECT cert_doc_id FROM document WHERE doc_id=%s", (doc_id,)
    ).fetchone()
    assert row["cert_doc_id"] == out["doc_id"]


def test_a_new_certificate_does_not_backresolve_across_a_revision_number(conn):
    """`Rev. 00` and `Rev. 01` stay distinct in this direction too."""
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, cert_number, status, content_hash, "
        "archive_url, coverage_scope) VALUES ('DoC','MDR','G15 043306 0282 Rev. 00',"
        "'staged','h-early-rev00','/archive/e.pdf','group') RETURNING doc_id"
    ).fetchone()["doc_id"]
    _seed_ext(conn, "h-cert-rev01-gate",
              _fields(type=_ev("EC"), cert_number=_ev("G15 043306 0282 Rev. 01")))
    gh.handle_gate_candidate(
        conn, _job("h-cert-rev01-gate", jid=2, ref_gate=True, links=[]))
    row = conn.execute(
        "SELECT cert_doc_id FROM document WHERE doc_id=%s", (doc_id,)
    ).fetchone()
    assert row["cert_doc_id"] is None


# --- final review: the two write-side holes found before the backfill --------

def test_auto_supersede_blocked_by_an_insane_date(conn):
    """`validity_from` is excluded from LLM escalation, so a T0 extraction hands
    the guard a flat high confidence no matter how wrong the value is. The only
    signal that a date-shaped string is not a plausible date is VALIDATE's
    `date-insane`, and it is deliberately not in BLOCKING_FLAGS. Before the fix
    a garbled OCR year auto-filed a document into the superseded chain
    permanently, with no human ever seeing it -- the exact failure a scanned
    corpus produces at scale."""
    newer = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, status, content_hash, "
        "archive_url, coverage_scope) VALUES ('DoC','MDR','2025-01-01','production',"
        "'h-newer-insane','/archive/ni.pdf','group') RETURNING doc_id"
    ).fetchone()["doc_id"]
    _seed_ext(conn, "h-older-insane", _older_fields())
    out = gh.handle_gate_candidate(
        conn, _job("h-older-insane", superseded_by_doc_id=newer,
                   flags=["older-than-current", "auto-superseded", "date-insane"],
                   ref_gate=True, links=[]))
    assert out["disposition"] == "manual", "an implausible date must reach a human"
    row = conn.execute(
        "SELECT status, superseded_by FROM document WHERE doc_id=%s", (out["doc_id"],)
    ).fetchone()
    assert row["status"] == "staged"
    assert row["superseded_by"] is None, "no chain pointer while awaiting review"


def test_auto_supersede_never_stamps_a_production_document(conn):
    """_upsert_document's status CASE is sticky, so a row already at
    `production` stays production even when this candidate computes
    `superseded`. Without the status predicate on the UPDATE the row ends up
    production AND carrying a chain pointer, which /api/documents/{doc_id}
    serves to external consumers as {"status": "production",
    "superseded_by": N} -- a self-contradicting record."""
    newer = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, status, content_hash, "
        "archive_url, coverage_scope) VALUES ('DoC','MDR','2025-01-01','production',"
        "'h-newer-prod','/archive/np.pdf','group') RETURNING doc_id"
    ).fetchone()["doc_id"]
    # the candidate's own row is ALREADY production from an earlier decision
    conn.execute(
        "INSERT INTO document (type, regulation, validity_from, status, content_hash, "
        "archive_url, coverage_scope) VALUES ('DoC','MDR','2020-01-01','production',"
        "'h-older-prod','/archive/op.pdf','group')"
    )
    _seed_ext(conn, "h-older-prod", _older_fields())
    out = gh.handle_gate_candidate(
        conn, _job("h-older-prod", superseded_by_doc_id=newer,
                   flags=["older-than-current", "auto-superseded"], ref_gate=True, links=[]))
    row = conn.execute(
        "SELECT status, superseded_by FROM document WHERE doc_id=%s", (out["doc_id"],)
    ).fetchone()
    assert row["status"] == "production", "sticky status is preserved, as designed"
    assert row["superseded_by"] is None, (
        "a production document must never carry a supersession pointer")
    assert out["supersede_skipped"], "the withheld write is reported, never silent"


@pytest.mark.parametrize("target_type,target_reg", [("DoC", "MDD"), ("EC", "MDR")])
def test_auto_supersede_rechecks_chain_identity_at_write_time(conn, target_type, target_reg):
    """[final-c6-recheck-race] The fast path writes the chain pointer from the
    payload's `superseded_by_doc_id`, which VALIDATE computed against the
    target's (type, regulation) as they were THEN. Both can change before this
    job is claimed: `_upsert_document`'s ON CONFLICT rewrites type/regulation
    unconditionally (only `status` is sticky), so a re-extraction of the
    target's own content_hash re-types a production document with no human
    involved, and `gate.apply approve` accepts human edits to both columns.
    Trusting the payload then writes a pointer between two documents that are no
    longer the same (subject, type, regulation) -- an MDR document filed into an
    MDD chain, which invariant 5 forbids outright.

    `_apply_supersession` re-reads BOTH documents live for exactly this reason
    and raises rather than skipping (a mismatch is an upstream bug, not a data
    condition). Both routes to a chain pointer must be guarded identically."""
    newer = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, status, content_hash, "
        "archive_url, coverage_scope) VALUES (%s,%s,'2025-01-01','production',%s,%s,'group') "
        "RETURNING doc_id",
        (target_type, target_reg, f"h-newer-drift-{target_type}-{target_reg}",
         f"/archive/nd-{target_type}-{target_reg}.pdf"),
    ).fetchone()["doc_id"]
    ch = f"h-older-drift-{target_type}-{target_reg}"
    _seed_ext(conn, ch, _older_fields())   # the candidate is DoC / MDR
    with pytest.raises(ValueError, match="C6 violation"):
        gh.handle_gate_candidate(
            conn, _job(ch, superseded_by_doc_id=newer,
                       flags=["older-than-current", "auto-superseded"],
                       ref_gate=True, links=[]))
    row = conn.execute(
        "SELECT doc_id, superseded_by FROM document WHERE content_hash=%s", (ch,)
    ).fetchone()
    if row is not None:      # partial write, rolled back by the worker on raise
        assert row["superseded_by"] is None
        assert conn.execute(
            "SELECT 1 FROM audit_log WHERE doc_id=%s AND event='auto-superseded'",
            (row["doc_id"],),
        ).fetchone() is None


def test_coverage_scope_vocabulary_check_rejects_item(conn):
    # `item` is retired (migration 022). Structural, not a property of
    # derive_coverage_scope: nothing -- a hand-written UPDATE, a future handler,
    # a re-introduced model answer -- can put it back. 37 of the first 57
    # registered documents carried it before the column was constrained.
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO document (type, regulation, coverage_scope, content_hash, "
            "archive_url, status) VALUES ('DoC','MDR','item','h-cov-ck','/a.pdf','staged')")


@pytest.mark.parametrize("scope", ["group", "manufacturer"])
def test_coverage_scope_vocabulary_check_allows_the_two_live_values(conn, scope):
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, "
        "archive_url, status) VALUES ('DoC','MDR',%s,%s,'/a.pdf','staged') RETURNING doc_id",
        (scope, f"h-cov-{scope}"),
    ).fetchone()["doc_id"]
    assert doc_id


# --------------------------------------------------------------------------- #
# Link retraction (2026-08-13). A link must not outlive the extraction that
# justified it.
#
# GATE upserted and never removed, so coverage only grew. Document 235: rev 1's
# REF list truncated to 40 codes wrote 19 links; rev 2, repaired to the real
# 1.121 codes, spans two manufacturers, correctly carries ZERO links -- and ran
# AFTER rev 1 with the 19 still standing. 134 rows registry-wide, 113 supported
# by the current rev, 21 stale.
#
# Only STAGED links are retracted. Production means a human approved the doc
# (gate.apply -> _promote_pending_links) or bound a manufacturer (mfr-scope,
# written production); auto-retracting one would undo a human decision and
# contradict "link provenance is immutable at production".
# --------------------------------------------------------------------------- #

def _link_rows(conn, content_hash):
    return {r["item_ref"]: r["status"] for r in conn.execute(
        "SELECT l.item_ref, l.status FROM item_document l "
        "JOIN document d USING (doc_id) WHERE d.content_hash=%s", (content_hash,)).fetchall()}


def _seed_group_with(conn, canonical, members):
    gid = conn.execute(
        "INSERT INTO item_group (canonical_manufacturer) VALUES (%s) RETURNING group_id",
        (canonical,)).fetchone()["group_id"]
    for item_ref in members:
        _seed_item(conn, item_ref, canonical)
        conn.execute(
            "INSERT INTO item_group_member (group_id, item_ref, match_basis) "
            "VALUES (%s,%s,'name-family')", (gid, item_ref))
    return gid


def test_a_group_scoped_candidate_never_retracts_another_groups_staged_link(conn):
    # Measured on the RENFERT smoke test (2026-08-24): doc 843's candidate for
    # group 1326 staged a link to item 18510000; the second candidate (group
    # 1641) staged 2222100 and RETRACTED 18510000. "Not claimed by THIS
    # candidate" is not "unsupported" — a group-scoped candidate only ever
    # (re-)evaluates links for members of ITS group (_ref_gate_for_group /
    # the fetch-context fallback), so N groups sharing one document thrashed
    # staged links until only the last claimant survived.
    g1 = _seed_group_with(conn, "RENFERT", ["IT-18510000"])
    g2 = _seed_group_with(conn, "RENFERT", ["IT-2222100"])
    _seed_ext(conn, "h-xgroup", _fields(), rev=1)
    gh.handle_gate_candidate(conn, _job("h-xgroup", group_id=g1, ref_gate=False, flags=[],
        links=[{"item_ref": "IT-18510000", "match_basis": "fetch-context"}]))
    gh.handle_gate_candidate(conn, _job("h-xgroup", jid=2, group_id=g2, ref_gate=False, flags=[],
        links=[{"item_ref": "IT-2222100", "match_basis": "fetch-context"}]))

    assert _link_rows(conn, "h-xgroup") == {"IT-18510000": "staged",
                                            "IT-2222100": "staged"}


def test_a_group_scoped_candidate_still_retracts_its_own_groups_dropped_member(conn):
    # The scoping must not blunt the legitimate purpose: a newer reading that
    # no longer supports a member of the SAME group still takes that link back.
    g = _seed_group_with(conn, "ACME", ["IT-KEEP-G", "IT-DROP-G"])
    _seed_ext(conn, "h-owngroup", _fields(), rev=1)
    gh.handle_gate_candidate(conn, _job("h-owngroup", group_id=g, ref_gate=False, flags=[], links=[
        {"item_ref": "IT-KEEP-G", "match_basis": "fetch-context"},
        {"item_ref": "IT-DROP-G", "match_basis": "fetch-context"}]))
    _seed_ext(conn, "h-owngroup", _fields(), rev=2)
    gh.handle_gate_candidate(conn, _job("h-owngroup", rev=2, jid=2, group_id=g, ref_gate=False,
        flags=[], links=[{"item_ref": "IT-KEEP-G", "match_basis": "fetch-context"}]))

    assert _link_rows(conn, "h-owngroup") == {"IT-KEEP-G": "staged",
                                              "IT-DROP-G": "retracted"}


def test_a_newer_candidate_retracts_a_staged_link_it_no_longer_supports(conn):
    # group_id=None: the doc-235 story is a backfill candidate (the document
    # self-identifies, VALIDATE evaluated the whole catalogue), which keeps the
    # full retraction scope. Group-scoped candidates are covered above.
    _seed_item(conn, "IT-KEEP"); _seed_item(conn, "IT-DROP")
    _seed_ext(conn, "h-retract", _fields(), rev=1)
    gh.handle_gate_candidate(conn, _job("h-retract", rev=1, group_id=None, ref_gate=True,
        flags=[], links=[
        {"item_ref": "IT-KEEP", "match_basis": "ref-catalogue"},
        {"item_ref": "IT-DROP", "match_basis": "ref-catalogue"}]))
    assert _link_rows(conn, "h-retract") == {"IT-KEEP": "staged", "IT-DROP": "staged"}

    _seed_ext(conn, "h-retract", _fields(), rev=2)
    gh.handle_gate_candidate(conn, _job("h-retract", rev=2, jid=2, group_id=None, ref_gate=True,
        flags=[], links=[{"item_ref": "IT-KEEP", "match_basis": "ref-catalogue"}]))

    assert _link_rows(conn, "h-retract") == {"IT-KEEP": "staged", "IT-DROP": "retracted"}


def test_retraction_is_a_status_change_not_a_delete(conn):
    # Invariant 4: append-only. The row must survive so a human can still see the
    # link existed and stopped being supported.
    _seed_item(conn, "IT-GONE")
    _seed_ext(conn, "h-nodel", _fields(), rev=1)
    gh.handle_gate_candidate(conn, _job("h-nodel", rev=1, group_id=None, ref_gate=True, flags=[],
        links=[{"item_ref": "IT-GONE", "match_basis": "ref-catalogue"}]))
    _seed_ext(conn, "h-nodel", _fields(), rev=2)
    gh.handle_gate_candidate(conn, _job("h-nodel", rev=2, jid=2, group_id=None, ref_gate=True,
        flags=[], links=[]))

    row = conn.execute(
        "SELECT l.status, l.match_basis FROM item_document l JOIN document d USING (doc_id) "
        "WHERE d.content_hash='h-nodel' AND l.item_ref='IT-GONE'").fetchone()
    assert row is not None, "the row must not be deleted"
    assert row["status"] == "retracted"
    assert row["match_basis"] == "ref-catalogue"      # provenance of how it was made stands


def test_a_production_link_is_never_auto_retracted(conn):
    """Production means a human approved it. Retracting would undo that decision
    silently, and would contradict the PRD's immutability-at-production rule."""
    _seed_item(conn, "IT-PROD")
    _seed_ext(conn, "h-prod", _fields(), rev=1)
    gh.handle_gate_candidate(conn, _job("h-prod", rev=1, group_id=None, ref_gate=True, flags=[],
        links=[{"item_ref": "IT-PROD", "match_basis": "ref-list"}]))
    conn.execute("UPDATE item_document SET status='production' WHERE item_ref='IT-PROD'")

    _seed_ext(conn, "h-prod", _fields(), rev=2)
    gh.handle_gate_candidate(conn, _job("h-prod", rev=2, jid=2, group_id=None, ref_gate=True,
        flags=[], links=[]))

    assert _link_rows(conn, "h-prod") == {"IT-PROD": "production"}


def test_a_stale_candidate_neither_writes_nor_retracts(conn):
    """Out-of-order delivery: rev 2 lands, then rev 1 arrives late. The
    superseded reading must not touch the registry's links in either direction."""
    _seed_item(conn, "IT-NEW"); _seed_item(conn, "IT-OLD")
    _seed_ext(conn, "h-stale", _fields(), rev=1)
    _seed_ext(conn, "h-stale", _fields(), rev=2)
    gh.handle_gate_candidate(conn, _job("h-stale", rev=2, ref_gate=True, flags=[],
        links=[{"item_ref": "IT-NEW", "match_basis": "ref-catalogue"}]))

    gh.handle_gate_candidate(conn, _job("h-stale", rev=1, jid=2, ref_gate=True, flags=[],
        links=[{"item_ref": "IT-OLD", "match_basis": "ref-catalogue"}]))

    assert _link_rows(conn, "h-stale") == {"IT-NEW": "staged"}


def test_retraction_writes_its_own_audit_event(conn):
    _seed_item(conn, "IT-AUD")
    _seed_ext(conn, "h-aud", _fields(), rev=1)
    gh.handle_gate_candidate(conn, _job("h-aud", rev=1, group_id=None, ref_gate=True, flags=[],
        links=[{"item_ref": "IT-AUD", "match_basis": "ref-catalogue"}]))
    _seed_ext(conn, "h-aud", _fields(), rev=2)
    gh.handle_gate_candidate(conn, _job("h-aud", rev=2, jid=2, group_id=None, ref_gate=True,
        flags=[], links=[]))

    ev = conn.execute(
        "SELECT event, item_ref FROM audit_log WHERE event='link-retracted'").fetchall()
    assert [(e["event"], e["item_ref"]) for e in ev] == [("link-retracted", "IT-AUD")]


# --------------------------------------------------------------------------- #
# A missing page cite must not destroy the document (2026-08-13).
#
# `required field 'regulation' is T1 without a page (inv. 2)` dead-lettered 2 of
# 130 GC documents -- roughly 18 across the full corpus -- because the model
# returned a value and a verbatim but no page number. Invariant 2 governs what a
# PRODUCTION value must carry: "Every production value carries complete
# evidence". A value with its verbatim and no page cite is an incomplete
# production candidate, not an unusable one, so it belongs in manual review with
# the document archived and evidenced -- not on the dead-jobs board with the
# whole document lost for one missing integer.
#
# A missing VALUE, verbatim, tier or confidence still raises: those leave
# nothing to review.
# --------------------------------------------------------------------------- #

def _ev_nopage(value, conf=0.97):
    return {"value": value, "conf": conf, "tier": "T1", "verbatim": f"v {value}",
            "page": None, "model_id": "m"}


def test_a_required_field_without_a_page_routes_to_manual_not_death(conn):
    _seed_ext(conn, "h-nopage", _fields(regulation=_ev_nopage("MDR")))
    res = gh.handle_gate_candidate(conn, _job("h-nopage", ref_gate=True, flags=[], links=[]))

    assert res["disposition"] == "manual"
    # The reviewer must be told WHY, so assert on the manual task they open,
    # not on the handler's return shape.
    task = conn.execute(
        "SELECT t.payload FROM manual_task t JOIN document d USING (doc_id) "
        "WHERE d.content_hash='h-nopage' AND t.kind='gate-manual'").fetchone()
    assert task is not None, "a pageless candidate must land in the manual queue"
    assert "evidence-page-missing" in (task["payload"].get("flags") or [])
    # The document is real and archived, not lost.
    assert _doc_status(conn, "h-nopage") is not None


def test_a_pageless_candidate_can_never_reach_production(conn):
    """The half of invariant 2 that must not soften: a production value carries
    a complete evidence bundle, page included."""
    _seed_ext(conn, "h-nopage-prod", _fields(regulation=_ev_nopage("MDR")))
    _seed_item(conn, "IT-NP")
    res = gh.handle_gate_candidate(conn, _job("h-nopage-prod", ref_gate=True, flags=[],
                                              links=[{"item_ref": "IT-NP", "match_basis": "ref-list"}]))
    assert res["disposition"] != "production"


def test_t0_and_t3_evidence_stay_legitimately_pageless(conn):
    """[evidence-page] ruling 2026-07-31: T0 filename-derived and T3 human
    evidence carry no page by nature and must not be flagged."""
    _seed_ext(conn, "h-t0page", _fields())          # _fields() is T1 with page=1
    conn.execute(
        "UPDATE extraction_attempt SET fields = jsonb_set(fields, '{regulation}', %s::jsonb) "
        "WHERE content_hash='h-t0page'",
        ('{"value":"MDR","conf":0.99,"tier":"T0","verbatim":"(EU) 2017/745","page":null}',))
    res = gh.handle_gate_candidate(conn, _job("h-t0page", ref_gate=True, flags=[], links=[]))
    assert "evidence-page-missing" not in res.get("flags", [])


def test_a_missing_value_or_verbatim_still_raises(conn):
    """Unchanged: those leave nothing for a human to review."""
    import pytest as _pytest
    _seed_ext(conn, "h-noval", _fields(regulation={"value": None, "conf": 0.9,
                                                    "tier": "T1", "verbatim": "x", "page": 1}))
    with _pytest.raises(ValueError, match="no value"):
        gh.handle_gate_candidate(conn, _job("h-noval", ref_gate=True, flags=[], links=[]))


# --------------------------------------------------------------------------- #
# flag classes (Denis's ruling 2026-08-13)
# --------------------------------------------------------------------------- #
# Until this ruling `gate.candidate` required an EMPTY flag list to reach
# production (`not flags`), so a flag describing CONTEXT gated the same as a
# flag describing a DEFECT. Measured cost on the GC corpus: 43 documents
# carrying all 309 live links sat staged on `cert-unresolved` /
# `multi-group-match` alone -- 100% of the registry's coverage. Both were
# documented as non-blocking where they are raised; only the disposition rule
# disagreed.
def test_an_informational_flag_does_not_block_production(conn):
    """`cert-unresolved` says the cited certificate is not in the registry YET.
    validate.py raises it with the words "caps at staged (non-blocking) and
    self-corrects once the certificate is gated" -- it is a statement about our
    ingest progress, not about this document's trustworthiness."""
    _seed_ext(conn, "h-info", _fields())
    _seed_item(conn, "IT-INFO")
    res = gh.handle_gate_candidate(conn, _job(
        "h-info", ref_gate=True, flags=["cert-unresolved"],
        links=[{"item_ref": "IT-INFO", "match_basis": "ref-list"}]))
    assert res["disposition"] == "production"


def test_multi_group_match_no_longer_caps(conn):
    """Its original reason is gone: since 2026-08-13 a document links every
    matching member across ALL groups under the pinned manufacturer, so nothing
    is arbitrary any more."""
    _seed_ext(conn, "h-multi", _fields())
    _seed_item(conn, "IT-MULTI")
    res = gh.handle_gate_candidate(conn, _job(
        "h-multi", ref_gate=True, flags=["multi-group-match", "cert-unresolved"],
        links=[{"item_ref": "IT-MULTI", "match_basis": "ref-list"}]))
    assert res["disposition"] == "production"


def test_a_suspected_ref_truncation_does_not_block_production(conn):
    """A list that came back at exactly the prompt cap MAY be partial. Every
    code it does name is still evidenced and still links its item, so gating on
    the suspicion would withhold real coverage -- the degradation Denis ruled
    out on 2026-08-13. It is context, recorded and shown, never a veto."""
    _seed_ext(conn, "h-trunc", _fields())
    _seed_item(conn, "IT-TRUNC")
    res = gh.handle_gate_candidate(conn, _job(
        "h-trunc", ref_gate=True, flags=[tiers.REF_TRUNCATION_FLAG],
        links=[{"item_ref": "IT-TRUNC", "match_basis": "ref-list"}]))
    assert res["disposition"] == "production"


def test_a_capping_flag_still_holds_a_document_at_staged(conn):
    """`downgrade-uncomparable` means we could not compare dates at all. That is
    a real unknown about supersession and a human still owns it."""
    _seed_ext(conn, "h-cap", _fields())
    _seed_item(conn, "IT-CAP")
    res = gh.handle_gate_candidate(conn, _job(
        "h-cap", ref_gate=True, flags=["downgrade-uncomparable"],
        links=[{"item_ref": "IT-CAP", "match_basis": "ref-list"}]))
    assert res["disposition"] == "staged"


def test_a_blocking_flag_still_forces_manual(conn):
    """Unchanged by the reclassification, and the reason the split is safe."""
    _seed_ext(conn, "h-block", _fields())
    res = gh.handle_gate_candidate(conn, _job(
        "h-block", ref_gate=True, flags=["no-item-identifier"], links=[]))
    assert res["disposition"] == "manual"


def test_same_date_revision_forces_manual(conn):
    """Two documents on one date have no order; a person picks (2026-09-04)."""
    _seed_ext(conn, "h-samedate", _fields())
    res = gh.handle_gate_candidate(conn, _job(
        "h-samedate", ref_gate=True, flags=["same-date-revision"], links=[]))
    assert res["disposition"] == "manual"


def test_every_flag_the_pipeline_emits_has_a_class(conn):
    """The vocabulary is closed on purpose. An unclassified flag would silently
    fall into "informational" and stop gating anything -- the failure mode this
    whole change is designed to make impossible to reach by accident."""
    emitted = _flags_emitted_by_the_pipeline()
    classified = set(gh.BLOCKING_FLAGS) | set(gh.CAPPING_FLAGS) | set(gh.INFORMATIONAL_FLAGS)
    assert emitted - classified == set(), (
        f"unclassified flags would stop gating silently: {sorted(emitted - classified)}")
    assert set(gh.BLOCKING_FLAGS) & set(gh.CAPPING_FLAGS) == set()
    assert set(gh.CAPPING_FLAGS) & set(gh.INFORMATIONAL_FLAGS) == set()
    assert set(gh.BLOCKING_FLAGS) & set(gh.INFORMATIONAL_FLAGS) == set()


def test_an_unrecognised_flag_gates_rather_than_slipping_through(conn):
    """The fail-safe direction. A flag added by a future VALIDATE rule and not
    yet classified must behave like the OLD conservative rule (hold at staged),
    never like an informational one. Getting this backwards would mean every new
    flag silently stops gating on the day it is introduced."""
    _seed_ext(conn, "h-unknown", _fields())
    _seed_item(conn, "IT-UNK")
    res = gh.handle_gate_candidate(conn, _job(
        "h-unknown", ref_gate=True, flags=["some-future-rule-nobody-classified"],
        links=[{"item_ref": "IT-UNK", "match_basis": "ref-list"}]))
    assert res["disposition"] == "staged"


def _flags_emitted_by_the_pipeline() -> set[str]:
    """Read the flag names out of the HANDLER SOURCE rather than restating them.

    The first version of this test hardcoded the list from memory and left out
    `multi-manufacturer-ref` (validate.py:741), so it asserted full coverage
    while a real flag was unclassified -- the precise failure it existed to
    prevent. A hand-copied vocabulary is the same drift risk as no vocabulary.

    `PAGE_MISSING_FLAG` is appended by name, not literal, so it is added
    explicitly; any further indirection of that kind will show up here as a
    literal this regex cannot see, which is why the count is asserted too."""
    import re
    from pathlib import Path

    root = Path(gh.__file__).resolve().parent
    found: set[str] = set()
    for mod in ("validate.py", "gate.py"):
        found |= set(re.findall(r'flags\.append\("([a-z][a-z-]+)"\)',
                                (root / mod).read_text(encoding="utf-8")))
    found.add(gh.PAGE_MISSING_FLAG)          # appended via the constant
    # VALIDATE also extends its list with tiers.integrity_flags(), whose flags
    # are constants rather than literals at the append site.
    found.add(tiers.REF_TRUNCATION_FLAG)
    assert len(found) >= 13, (
        f"only {len(found)} flags scraped from the handlers -- the append idiom "
        "changed and this guard has gone blind")
    return found


def test_the_shipped_high_threshold_is_the_ninety_two_denis_ruled():
    """[gate-thresholds]. 0.95 was the scaffold's placeholder and had never been
    changed since the S0.1 scaffold; Denis ruled 0.92 on 2026-08-19. Asserted through
    `load_config()` rather than the dataclass, for the reason
    `[renewal-horizon-default-ignored]` taught the same day: a loader that
    repeats a literal instead of reading the field's default will disagree with
    it silently, and a test on the dataclass would pass throughout.

    Still a ruling, not a measurement -- the calibration plan has not been run --
    so `[gate-thresholds]` stays open. This test pins what ships, not what is
    correct."""
    from app.config import load_config

    assert load_config().gate.high == 0.92
    assert load_config().gate.med == 0.75


# --------------------------------------------------------------------------- #
# 053: the CONFIRMED manufacturer
# --------------------------------------------------------------------------- #
def _seed_entity(conn, name):
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES (%s) "
                 "ON CONFLICT (canonical_name) DO NOTHING", (name,))


def _bound(conn, doc_id):
    return conn.execute(
        "SELECT canonical_manufacturer FROM document WHERE doc_id=%s", (doc_id,)
    ).fetchone()["canonical_manufacturer"]


def test_bindable_manufacturer_refuses_a_name_with_no_entity_row(conn):
    """The picker offers `COALESCE(a.canonical_name, m.manufacturer_raw)`, so it
    can offer a bare BC vendor code that has no `manufacturer` row. All 366
    offerable entities have one as of 2026-08-31, so this cannot fire on live
    data -- it exists because an unguarded write would raise ForeignKeyViolation
    inside the runner's transaction and take the whole gate decision down with
    it. Refusing to store a binding is recoverable; losing the decision is not.
    """
    _seed_entity(conn, "REAL CO")

    assert gh._bindable_manufacturer(conn, "REAL CO") == "REAL CO"
    assert gh._bindable_manufacturer(conn, "  REAL CO  ") == "REAL CO"
    assert gh._bindable_manufacturer(conn, "NO SUCH ENTITY") is None
    assert gh._bindable_manufacturer(conn, None) is None
    assert gh._bindable_manufacturer(conn, "   ") is None


def test_the_binding_survives_a_redelivery_that_cannot_recompute_it(conn):
    """Sticky, for the reason `supersedes` / `cert_doc_id` / `source_url` are: a
    re-delivery that happens not to carry the binding must not erase a decision
    a human already made."""
    _seed_entity(conn, "ACME AG")
    _seed_entity(conn, "OTHER AG")
    fields = {"type": _ev("ISO"), "regulation": _ev("n.a."),
              "coverage_scope": _ev("manufacturer")}

    doc_id = gh._upsert_document(conn, fields, "hash-sticky", "file:///a.pdf",
                                 "staged", canonical_manufacturer="ACME AG")
    assert _bound(conn, doc_id) == "ACME AG"

    again = gh._upsert_document(conn, fields, "hash-sticky", "file:///a.pdf",
                                "staged", canonical_manufacturer=None)
    assert again == doc_id
    assert _bound(conn, doc_id) == "ACME AG"

    # A NEW non-null binding still wins -- re-binding is the correction path.
    gh._upsert_document(conn, fields, "hash-sticky", "file:///a.pdf", "staged",
                        canonical_manufacturer="OTHER AG")
    assert _bound(conn, doc_id) == "OTHER AG"


def test_c16_records_the_binding_on_the_md_class_unknown_route(conn):
    """The staging branch resolved its manufacturer -- that is what separates it
    from `bound is None` -- so it must store it."""
    _seed_entity(conn, "KNOWN AG")
    _seed_alias(conn, "K1", "KNOWN AG")
    _seed_unclassified_item(conn, "K-BLANK", "K1")
    _seed_ext(conn, "c16-blank", _mfr_fields("KNOWN AG"))

    out = gh.handle_gate_candidate(conn, _job("c16-blank", route="mfr-binding"))

    assert out["disposition"] == "mfr-binding"
    assert out["reason"] == "md-class-unknown"
    assert _bound(conn, out["doc_id"]) == "KNOWN AG"


def test_c16_records_the_binding_when_it_files(conn):
    """`filed` is the branch that matters most: 443 of the 540 link-less
    documents are filed, and this is what makes them queryable at all."""
    _seed_entity(conn, "GONE AG")
    _seed_alias(conn, "G1", "GONE AG")
    _seed_declassified_item(conn, "G-NO", "G1")
    _seed_ext(conn, "c16-filed", _mfr_fields("GONE AG"))

    out = gh.handle_gate_candidate(conn, _job("c16-filed", route="mfr-binding"))

    assert out["disposition"] == "filed"
    assert _bound(conn, out["doc_id"]) == "GONE AG"


def test_a_group_scope_document_records_its_resolved_manufacturer(conn):
    _seed_entity(conn, "GRP AG")
    _seed_alias(conn, "Grp A.G.", "GRP AG")
    _seed_ext(conn, "grp-1", _fields(manufacturer=_ev("Grp A.G.")))

    out = gh.handle_gate_candidate(conn, _job("grp-1"))

    assert _bound(conn, out["doc_id"]) == "GRP AG"


def test_an_ambiguous_name_leaves_the_binding_null_rather_than_guessing(conn):
    """Two canonicals is the case a human must settle. Choosing one here would
    attach a company's certificate to another company's products."""
    _seed_entity(conn, "AMB ONE")
    _seed_entity(conn, "AMB TWO")
    # Two DISTINCT raw spellings that `normalize` folds to the same string --
    # case and internal whitespace fold, punctuation does NOT ('ambiguous ltd.'
    # stays distinct), so the collision has to be built from the parts that do.
    _seed_alias(conn, "Ambiguous Ltd", "AMB ONE")
    _seed_alias(conn, "AMBIGUOUS   LTD", "AMB TWO")
    _seed_ext(conn, "grp-amb", _fields(manufacturer=_ev("Ambiguous Ltd")))

    out = gh.handle_gate_candidate(conn, _job("grp-amb"))

    assert _bound(conn, out["doc_id"]) is None


# --------------------------------------------------------------------------- #
# the device-type guard on the REF branch
# --------------------------------------------------------------------------- #
# `_is_md_document` shipped 2026-08-17 with C16 and was wired to the mfr-scope
# binding path only (its own docstring says "Applied ONLY to the machine binding
# path"). The REF branch -- `ref-list` / `ref-item`, both in TRUSTED_BASES --
# asked whether the numbers matched and never whether the document was about a
# device at all. Measured 2026-09-03 before the fix: 4 production REF links
# across 3 documents (27, 50, 82, all DoC/n.a.) would be refused, out of 1.945.
# Small, which is exactly why it is a bug fix and not a C16-shaped ruling.

def test_a_non_device_document_cannot_reach_production_through_the_ref_branch(conn):
    # A machinery-directive declaration: read correctly, REF gate satisfied,
    # confidence high. Everything the old production branch asked for.
    ch = "gc-notmd-ref"
    _seed_ext(conn, ch, _fields(conf=0.97, regulation=_ev("n.a.", 0.97)))

    res = gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[]))

    assert res["disposition"] == "staged"          # a refusal is a review...
    assert _doc_status(conn, ch) == "staged"       # ...not a rejection
    assert "not-a-device-document" in res["flags"]


def test_the_guard_does_not_touch_a_real_device_document(conn):
    # The mutation guard. Refusing everything would pass the test above and
    # empty the registry's whole production path.
    ch = "gc-md-ref"
    _seed_ext(conn, ch, _fields(conf=0.97))        # regulation=MDR

    res = gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[]))

    assert res["disposition"] == "production"
    assert "not-a-device-document" not in res.get("flags", [])


def test_a_qms_certificate_without_a_regulation_still_passes_the_guard(conn):
    # `_is_md_document` accepts QMS_TYPES_WITHOUT_REGULATION precisely because
    # an ISO 13485 certificate carries no MDR/MDD citation by nature. C16 (doc
    # 816, one EN ISO 13485 over 2.567 CARL MARTIN items) depends on this.
    ch = "gc-qms-ref"
    _seed_ext(conn, ch, _fields(conf=0.97, type=_ev("ISO", 0.97),
                                regulation=_ev("n.a.", 0.97)))

    res = gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[]))

    assert "not-a-device-document" not in res.get("flags", [])


def test_the_adjacency_flag_does_not_gate_a_disposition():
    """`_gates_disposition` is a membership test, so an unclassified flag GATES
    -- which is the right default and is exactly why this needs pinning. The
    flag says T0 could not read a labelled date because of the PAGE'S LAYOUT.
    It says nothing about whether the value we hold is right: it came from a
    paid tier that read the page properly, or the field is absent and the
    existing missing-field rules already handle that. Gating would withhold
    evidenced coverage over a note about our own parser."""
    from app.extract import tiers as t
    assert t.DATE_LABEL_NOT_ADJACENT_FLAG in gh.INFORMATIONAL_FLAGS
    assert gh._gates_disposition([t.DATE_LABEL_NOT_ADJACENT_FLAG]) is False
    # ... and it still gates alongside a real one, rather than excusing it.
    assert gh._gates_disposition(
        [t.DATE_LABEL_NOT_ADJACENT_FLAG, "no-item-identifier"]) is True


# --- a re-delivery may never move a document backward ----------------------- #
#
# `_upsert_document` protected `production` and `superseded` only, so `filed`
# and `rejected` were writable by ANY re-delivery -- an ordinary re-extraction
# or one of the repair tools. Measured live 2026-09-04 by a 67-document
# re-emission: four moved, three `filed` -> `staged` and one a person had
# REJECTED (doc 837, `admin`, 2026-08-25) put back in the review queue, all of
# them silently. Followup [gate-upsert-resurrects-filed-and-rejected].

@pytest.mark.parametrize("existing, incoming, expected", [
    # terminal to a machine: only a person leaves these
    ("rejected",   "staged",     "rejected"),
    ("rejected",   "production", "rejected"),
    ("production", "staged",     "production"),
    ("superseded", "staged",     "superseded"),
    # never backward, but forward is still allowed -- a re-extraction that
    # finds items must still be able to promote a filed document
    ("filed",      "staged",     "filed"),
    ("filed",      "production", "production"),
    ("staged",     "filed",      "filed"),
    ("staged",     "production", "production"),
])
def test_a_redelivery_never_moves_a_document_backward(
        conn, existing, incoming, expected):
    from app.handlers import gate as g
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('MONO') "
                 "ON CONFLICT (canonical_name) DO NOTHING")
    fields = {
        "type": {"value": "DoC", "conf": 1.0, "tier": "T0", "verbatim": "DoC"},
        "regulation": {"value": "MDR", "conf": 1.0, "tier": "T0", "verbatim": "745"},
        "coverage_scope": {"value": "manufacturer", "conf": 1.0, "tier": "T0",
                           "verbatim": "qms"},
    }
    h = f"mono{existing}{incoming}".ljust(64, "0")
    doc_id = g._upsert_document(conn, fields, h, "/archive/x.pdf", existing,
                                canonical_manufacturer="MONO")
    again = g._upsert_document(conn, fields, h, "/archive/x.pdf", incoming,
                               canonical_manufacturer="MONO")
    assert again == doc_id, "the conflict clause did not fire"
    assert conn.execute("SELECT status FROM document WHERE doc_id=%s",
                        (doc_id,)).fetchone()["status"] == expected
