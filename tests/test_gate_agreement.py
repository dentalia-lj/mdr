"""GATE: two agreeing reads as confidence (PRD v3 section 7, amended 2026-10-12).

The extraction record's field evidence may carry, beside the stored read's own
`conf` / `model_id`:

* `agreed_model_id` + `agreed_conf`: a second read agreed on the same value;
* `proven: "citation"`: on `regulation` only, proven from its printed citation.

GATE combines at score time: `1 - (1 - own)(1 - agreed)` when both calibrated
numbers are at least `gate.agreement_min`, 1.0 for a citation-proven
regulation, the read's own number otherwise. T0 is never touched. The guards
that read ONE field (the manufacturer-scope auto-bind, the manufacturer-name
check and the supersede fast path's `validity_from`) keep the stored read's own
number: the agreement evidence was measured on the production disposition, not
on those writes.
"""

from __future__ import annotations

import pytest

from app.extract.tiers import write_extraction_attempt
from app.handlers import gate as gh

MIN = 0.70


def _ev(value, conf=0.97, model="m-first", **extra):
    return {"value": value, "conf": conf, "tier": "T1", "verbatim": f"v {value}",
            "page": 1, "model_id": model, **extra}


def _agreed(value, conf, agreed_conf, **extra):
    return _ev(value, conf, agreed_model_id="m-second", agreed_conf=agreed_conf, **extra)


def _t0(value, conf, **extra):
    return {"value": value, "conf": conf, "tier": "T0", "verbatim": f"v {value}",
            "model_id": None, **extra}


def _fields(conf=0.97, **overrides):
    base = {"type": _ev("DoC", conf), "regulation": _ev("MDR", conf),
            "coverage_scope": _ev("group", conf)}
    base.update(overrides)
    return base


def _seed(conn, ch, fields, rev=1):
    write_extraction_attempt(conn, ch, ["T0", "T1"], fields, extract_rev=rev)


def _job(ch, *, rev=1, group_id=7, jid=1, **payload):
    p = {"content_hash": ch, "extract_rev": rev, "group_id": group_id,
         "archive_url": "file:///a.pdf"}
    p.update(payload)
    return {"id": jid, "type": "gate.candidate", "payload": p,
            "dedupe_key": f"gate:{ch}:{rev}:{group_id}"}


def _seed_item(conn, item_ref, manufacturer="ACME", md=None):
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, catalogue, "
        "mirror_rev, updated_at) VALUES (%s,'n',%s,%s,'LJ',1,now()) "
        "ON CONFLICT (item_ref) DO NOTHING", (item_ref, manufacturer, md))


def _seed_alias(conn, raw_name, canonical_name):
    conn.execute(
        "INSERT INTO manufacturer_alias (raw_name, canonical_name) VALUES (%s, %s) "
        "ON CONFLICT (raw_name) DO NOTHING", (raw_name, canonical_name))


# --------------------------------------------------------------------------- #
# the rule, one field at a time
# --------------------------------------------------------------------------- #
COMBINE = [
    # id, field, evidence, expected
    ("both-at-the-floor-combine", "type", _agreed("DoC", 0.70, 0.70), 1 - 0.3 * 0.3),
    ("both-high-combine", "coverage_scope", _agreed("group", 0.85, 0.90), 1 - 0.15 * 0.10),
    ("own-just-under-keeps-own", "type", _agreed("DoC", 0.69, 0.95), 0.69),
    ("agreed-just-under-keeps-own", "type", _agreed("DoC", 0.90, 0.69), 0.90),
    ("no-agreement-keeps-own", "type", _ev("DoC", 0.85), 0.85),
    ("agreed-conf-without-model-keeps-own", "type",
     _ev("DoC", 0.85, agreed_conf=0.95), 0.85),
    ("agreed-model-without-conf-keeps-own", "type",
     _ev("DoC", 0.85, agreed_model_id="m-second"), 0.85),
    ("unparseable-agreed-conf-keeps-own", "type",
     _ev("DoC", 0.85, agreed_model_id="m-second", agreed_conf="sure"), 0.85),
    ("t0-is-never-combined", "type", _t0("DoC", 0.85, agreed_model_id="m-second",
                                          agreed_conf=0.95), 0.85),
    ("citation-proven-regulation-is-one", "regulation",
     _ev("MDR", 0.60, proven="citation"), 1.0),
    ("citation-proven-regulation-beats-agreement", "regulation",
     _agreed("MDR", 0.80, 0.80, proven="citation"), 1.0),
    ("proven-on-another-field-is-ignored", "type", _ev("DoC", 0.60, proven="citation"), 0.60),
    ("t0-regulation-is-unchanged-even-if-proven", "regulation",
     _t0("MDR", 0.80, proven="citation"), 0.80),
]


@pytest.mark.parametrize("field,ev,expected", [c[1:] for c in COMBINE],
                         ids=[c[0] for c in COMBINE])
def test_gate_confidence(field, ev, expected):
    got = gh._gate_confidence({field: ev}, field, {}, MIN)
    assert got == pytest.approx(expected)


def test_calibration_applies_to_each_read_before_combining():
    """A factor applies to each read under its own model, as `_score` always
    did for the first read; the floor is tested on the calibrated numbers."""
    calib = {"m-first": {"*": 0.5}}          # 0.9 -> 0.45 for the first read only
    ev = _agreed("DoC", 0.90, 0.90)
    assert gh._gate_confidence({"type": ev}, "type", calib, MIN) == pytest.approx(0.45)


# --------------------------------------------------------------------------- #
# what evidence stores: one decision with the score, the raw numbers
# --------------------------------------------------------------------------- #
STORED = [
    # id, field, evidence, calibration map, stored confidence
    ("agreement-stores-the-raw-combination", "type", _agreed("DoC", 0.85, 0.90), {},
     1 - 0.15 * 0.10),
    ("no-agreement-stores-own", "type", _ev("DoC", 0.85), {}, 0.85),
    ("under-the-floor-stores-own", "type", _agreed("DoC", 0.95, 0.60), {}, 0.95),
    ("citation-proven-regulation-stores-one", "regulation",
     _ev("MDR", 0.80, proven="citation"), {}, 1.0),
    ("t0-stores-own", "type", _t0("DoC", 0.85, agreed_model_id="m-second",
                                  agreed_conf=0.95), {}, 0.85),
    # F3: whether the agreement counts is the SCORE's decision, on calibrated
    # numbers (0.9 x 0.5 = 0.45, under the floor), so the row stores the
    # stored read's own raw number, never a combination the score refused.
    ("the-floor-is-tested-calibrated", "type", _agreed("DoC", 0.90, 0.90),
     {"m-first": {"*": 0.5}}, 0.90),
    # ... and when the calibrated numbers do clear it, the RAW pair is combined.
    ("calibrated-pass-combines-raw", "type", _agreed("DoC", 0.90, 0.90),
     {"m-first": {"*": 0.9}}, 1 - 0.1 * 0.1),
]


@pytest.mark.parametrize("field,ev,calib,expected", [c[1:] for c in STORED],
                         ids=[c[0] for c in STORED])
def test_stored_confidence(field, ev, calib, expected):
    assert gh._stored_confidence(ev, field, calib, MIN) == pytest.approx(expected)


AGREED_COLUMNS = [
    # id, evidence, (agreed_model_id, agreed_confidence) written
    ("both-halves-on-a-read", _agreed("DoC", 0.95, 0.60), ("m-second", 0.60)),
    ("conf-without-model", _ev("DoC", 0.9, agreed_conf=0.95), (None, None)),
    ("model-without-conf", _ev("DoC", 0.9, agreed_model_id="m-second"), (None, None)),
    ("unparseable-conf", _ev("DoC", 0.9, agreed_model_id="m-second", agreed_conf="0.9"),
     (None, None)),
    ("t0-row", _t0("DoC", 0.9, agreed_model_id="m-second", agreed_conf=0.95), (None, None)),
]


@pytest.mark.parametrize("ev,expected", [c[1:] for c in AGREED_COLUMNS],
                         ids=[c[0] for c in AGREED_COLUMNS])
def test_the_agreed_columns_need_both_halves_on_a_model_read(ev, expected):
    """F4: migration 071 and the schema sketch say T0, T3 and single-read rows
    hold NULL. Half a pair, or a pair on a row no model read, is not an
    agreeing read."""
    assert gh._agreed_columns(ev) == expected


@pytest.mark.parametrize("conf", ["0.97", "sure", True, None])
def test_a_confidence_that_is_not_a_number_is_incomplete_evidence(conf):
    """F6: a numeric string is not a number either. Accepting one in the score
    while the evidence insert passes it through as text is how a malformed
    record could score as if it were well formed."""
    fields = {"type": _ev("DoC", conf), "regulation": _ev("MDR"),
              "coverage_scope": _ev("group")}
    with pytest.raises(ValueError, match="evidence incomplete"):
        gh._require_complete_evidence(fields)


def test_an_integer_confidence_is_a_number():
    fields = {"type": _ev("DoC", 1), "regulation": _ev("MDR"), "coverage_scope": _ev("group")}
    assert gh._require_complete_evidence(fields) == []


def test_the_own_reader_never_sees_the_agreement():
    """`_field_confidence` is the one-field reader for the guards; it must stay
    the stored read's own number even when a second read agreed."""
    fields = {"coverage_scope": _agreed("manufacturer", 0.80, 0.95)}
    assert gh._field_confidence(fields, "coverage_scope", {}) == pytest.approx(0.80)


def test_score_is_the_minimum_of_the_combined_fields():
    fields = _fields(type=_agreed("DoC", 0.85, 0.85),
                     regulation=_ev("MDR", 0.80, proven="citation"),
                     coverage_scope=_agreed("group", 0.90, 0.80))
    assert gh._score(fields, {}, MIN) == pytest.approx(min(1 - 0.15 * 0.15, 1.0, 1 - 0.1 * 0.2))


# --------------------------------------------------------------------------- #
# the disposition and per-link production read the combined score
# --------------------------------------------------------------------------- #
def test_two_agreeing_reads_publish_what_one_read_would_stage(conn):
    ch = "agree-publish"
    _seed_item(conn, "AG-1")
    _seed(conn, ch, _fields(type=_agreed("DoC", 0.85, 0.85),
                            regulation=_agreed("MDR", 0.85, 0.85),
                            coverage_scope=_agreed("group", 0.85, 0.85)))
    r = gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[
        {"item_ref": "AG-1", "match_basis": "ref-list"}]))
    assert r["disposition"] == "production"
    assert r["score"] == pytest.approx(1 - 0.15 * 0.15, abs=1e-4)
    assert conn.execute("SELECT status FROM item_document WHERE doc_id=%s",
                        (r["doc_id"],)).fetchone()["status"] == "production"


def test_the_same_reads_without_agreement_stage(conn):
    """The control for the test above: the same own numbers, no second read."""
    ch = "agree-control"
    _seed_item(conn, "AG-2")
    _seed(conn, ch, _fields(conf=0.85))
    r = gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[
        {"item_ref": "AG-2", "match_basis": "ref-list"}]))
    assert r["disposition"] == "staged"
    assert conn.execute("SELECT status FROM item_document WHERE doc_id=%s",
                        (r["doc_id"],)).fetchone()["status"] == "staged"


def test_one_read_under_the_floor_still_holds_the_document(conn):
    ch = "agree-floor"
    _seed(conn, ch, _fields(type=_agreed("DoC", 0.69, 0.99),
                            regulation=_agreed("MDR", 0.85, 0.85),
                            coverage_scope=_agreed("group", 0.85, 0.85)))
    r = gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[]))
    assert r["score"] == pytest.approx(0.69)
    assert r["disposition"] == "manual"          # 0.69 is under med (0.75)


def test_a_citation_proven_regulation_does_not_hold_the_document(conn):
    ch = "agree-citation"
    _seed(conn, ch, _fields(conf=0.95, regulation=_ev("MDR", 0.80, proven="citation")))
    r = gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[]))
    assert r["disposition"] == "production"
    assert r["score"] == pytest.approx(0.95)


# --------------------------------------------------------------------------- #
# evidence: the agreeing read is named, the stored confidence is the combined one
# --------------------------------------------------------------------------- #
def _evidence(conn, doc_id):
    return {r["field"]: r for r in conn.execute(
        "SELECT field, model_id, confidence, agreed_model_id, agreed_confidence "
        "FROM evidence WHERE doc_id=%s", (doc_id,)).fetchall()}


def test_evidence_names_the_agreeing_read_and_stores_the_combined_confidence(conn):
    ch = "agree-evidence"
    _seed(conn, ch, _fields(type=_agreed("DoC", 0.85, 0.90),
                            regulation=_ev("MDR", 0.95),
                            coverage_scope=_ev("group", 0.95)))
    r = gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[]))
    ev = _evidence(conn, r["doc_id"])

    assert ev["type"]["model_id"] == "m-first"               # the first read stays the read
    assert float(ev["type"]["confidence"]) == pytest.approx(1 - 0.15 * 0.10)
    assert ev["type"]["agreed_model_id"] == "m-second"
    assert float(ev["type"]["agreed_confidence"]) == pytest.approx(0.90)

    assert float(ev["regulation"]["confidence"]) == pytest.approx(0.95)
    assert ev["regulation"]["agreed_model_id"] is None
    assert ev["regulation"]["agreed_confidence"] is None


def test_an_agreement_under_the_floor_is_recorded_but_not_combined(conn):
    """The second read is a fact about the document whatever its number; only
    the combination needs both reads at the floor."""
    ch = "agree-evidence-floor"
    _seed(conn, ch, _fields(conf=0.95, type=_agreed("DoC", 0.95, 0.60)))
    r = gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[]))
    ev = _evidence(conn, r["doc_id"])["type"]
    assert float(ev["confidence"]) == pytest.approx(0.95)
    assert ev["agreed_model_id"] == "m-second"
    assert float(ev["agreed_confidence"]) == pytest.approx(0.60)


def test_a_citation_proven_regulation_stores_the_one_it_scored(conn):
    """R3 (Denis, 2026-10-12): the stored number is the one GATE scored, so the
    row explains the disposition. The page note says a 1.0 can come from the
    printed citation."""
    ch = "agree-evidence-citation"
    _seed(conn, ch, _fields(conf=0.95, regulation=_ev("MDR", 0.80, proven="citation")))
    r = gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[]))
    assert float(_evidence(conn, r["doc_id"])["regulation"]["confidence"]) == pytest.approx(1.0)


def test_t0_evidence_is_stored_as_read(conn):
    ch = "agree-evidence-t0"
    _seed(conn, ch, _fields(conf=0.95, type=_t0("DoC", 0.85, agreed_model_id="m-second",
                                                    agreed_conf=0.95)))
    r = gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[]))
    ev = _evidence(conn, r["doc_id"])["type"]
    assert float(ev["confidence"]) == pytest.approx(0.85)
    # F4: no model read this row, so it names no agreeing read (migration 071)
    assert ev["agreed_model_id"] is None and ev["agreed_confidence"] is None


def test_a_human_edit_names_no_agreeing_read(conn):
    ch = "agree-edit"
    _seed(conn, ch, _fields(conf=0.85))
    r = gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[]))
    gh.handle_gate_apply(conn, {"id": 2, "type": "gate.apply", "payload": {
        "doc_id": r["doc_id"], "decision": "approve", "decided_by": "nat",
        "edits": {"validity_from": "2024-01-01"}}})
    t3 = conn.execute(
        "SELECT tier, confidence, agreed_model_id, agreed_confidence FROM evidence "
        "WHERE doc_id=%s AND field='validity_from'", (r["doc_id"],)).fetchone()
    assert t3["tier"] == "T3" and float(t3["confidence"]) == 1.0
    assert t3["agreed_model_id"] is None and t3["agreed_confidence"] is None


# --------------------------------------------------------------------------- #
# the guards that read ONE field stay on the stored read's own number
# --------------------------------------------------------------------------- #
def _mfr_case(conn, ch, *, scope, manufacturer):
    fields = {"type": _ev("EC", 0.97), "regulation": _ev("MDR", 0.97),
              "coverage_scope": scope, "manufacturer": manufacturer}
    _seed(conn, ch, fields)
    _seed_alias(conn, "001", "IVOCLAR")
    _seed_alias(conn, "Ivoclar Vivadent AG", "IVOCLAR")
    _seed_item(conn, f"{ch}-1", "001", md=True)
    return gh.handle_gate_candidate(conn, _job(ch, route="mfr-binding"))


def test_the_auto_bind_reads_the_scope_s_own_confidence(conn):
    """0.80 own plus an agreeing 0.95 combines to 0.99, past `high`; the
    catalogue-wide bind still refuses, because `scope_stated` reads 0.80."""
    r = _mfr_case(conn, "agree-scope",
                  scope=_agreed("manufacturer", 0.80, 0.95),
                  manufacturer=_ev("Ivoclar Vivadent AG", 0.95))
    assert r["disposition"] == "mfr-binding"
    assert conn.execute("SELECT count(*) c FROM item_document WHERE doc_id=%s",
                        (r["doc_id"],)).fetchone()["c"] == 0


def test_the_auto_bind_still_binds_on_a_stated_scope(conn):
    """The control: the same case with the scope's own number at `high`."""
    r = _mfr_case(conn, "agree-scope-control",
                  scope=_ev("manufacturer", 0.95),
                  manufacturer=_ev("Ivoclar Vivadent AG", 0.95))
    assert r["disposition"] == "mfr-bound"


def test_the_manufacturer_name_check_reads_its_own_confidence(conn):
    """0.70 own (under `med`, 0.75) plus an agreeing 0.95 does not make the
    name confident enough to bind."""
    r = _mfr_case(conn, "agree-name",
                  scope=_ev("manufacturer", 0.97),
                  manufacturer=_agreed("Ivoclar Vivadent AG", 0.70, 0.95))
    assert r["disposition"] == "mfr-binding"


def test_the_supersede_fast_path_reads_validity_from_s_own_confidence(conn):
    newer = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, status, content_hash, "
        "archive_url, coverage_scope) VALUES ('DoC','MDR','2025-01-01','production',"
        "'agree-newer','/archive/n.pdf','group') RETURNING doc_id").fetchone()["doc_id"]
    ch = "agree-older"
    _seed(conn, ch, _fields(validity_from=_agreed("2020-01-01", 0.85, 0.95)))
    r = gh.handle_gate_candidate(conn, _job(
        ch, superseded_by_doc_id=newer, flags=["older-than-current", "auto-superseded"],
        ref_gate=True, links=[]))
    assert r["disposition"] == "manual"          # 0.85 own, under high; not auto-filed
    row = conn.execute("SELECT status, superseded_by FROM document WHERE doc_id=%s",
                       (r["doc_id"],)).fetchone()
    assert row["status"] != "superseded" and row["superseded_by"] is None


# --------------------------------------------------------------------------- #
# a scope that follows the type (Denis, ruling D3, 2026-10-13)
# --------------------------------------------------------------------------- #
def _follows(value, conf, doc_type="EC"):
    """A `coverage_scope` the extraction derived from the type
    (`tiers._scope_from_type`): T0 evidence at the type's own number."""
    return _t0(value, conf, verbatim=f"type={doc_type}", page=None, follows="type")


CERT = {"type": _agreed("EC", 0.85, 0.85), "regulation": _ev("MDR", 0.80, proven="citation"),
        "coverage_scope": _follows("manufacturer", 0.85)}


def test_a_scope_that_follows_the_type_is_left_out_of_the_score():
    """The type is in the minimum already, combined when confirmed; the scope
    holds only the type's own number. Unmarked, the same scope holds it."""
    assert gh._score(CERT, {}, MIN) == pytest.approx(1 - 0.15 * 0.15)
    unmarked = dict(CERT, coverage_scope=_t0("manufacturer", 0.85, verbatim="type=EC"))
    assert gh._score(unmarked, {}, MIN) == pytest.approx(0.85)


def test_only_a_scope_marked_as_following_the_type_is_left_out():
    fields = dict(CERT, type=_ev("EC", 0.85, follows="type"))      # not a scope: scored
    assert gh._score(fields, {}, MIN) == pytest.approx(0.85)


def test_a_certificate_whose_type_a_second_read_confirmed_reaches_production(conn):
    """Type 0.85 confirmed by a second read at 0.85 combines to 0.9775; the
    scope derived from it at 0.85 no longer holds the document at `staged`."""
    ch = "follows-publish"
    _seed_item(conn, "FT-1")
    _seed(conn, ch, dict(CERT))
    r = gh.handle_gate_candidate(conn, _job(ch, ref_gate=True, flags=[], links=[
        {"item_ref": "FT-1", "match_basis": "ref-list"}]))
    assert r["disposition"] == "production"
    assert r["score"] == pytest.approx(1 - 0.15 * 0.15, abs=1e-4)
    scope = conn.execute("SELECT confidence FROM evidence WHERE doc_id=%s AND "
                         "field='coverage_scope'", (r["doc_id"],)).fetchone()
    assert float(scope["confidence"]) == pytest.approx(0.85)    # its own number, stored


@pytest.mark.parametrize("own, bound", [(0.85, False), (0.95, True)],
                         ids=["type-own-under-high", "control"])
def test_the_auto_bind_reads_the_own_number_of_a_scope_that_follows_the_type(conn, own, bound):
    """`scope_stated` reads the scope's own number, the type's own one, never
    the type's combination: at 0.85 the catalogue-wide bind refuses as before
    (the type combines to 0.9775); the control at 0.95 binds."""
    ch = f"follows-bind-{own}"
    _seed(conn, ch, dict(CERT, type=_agreed("EC", own, 0.85),
                         coverage_scope=_follows("manufacturer", own),
                         manufacturer=_ev("Ivoclar Vivadent AG", 0.95)))
    _seed_alias(conn, "001", "IVOCLAR")
    _seed_alias(conn, "Ivoclar Vivadent AG", "IVOCLAR")
    _seed_item(conn, f"{ch}-1", "001", md=True)
    r = gh.handle_gate_candidate(conn, _job(ch, route="mfr-binding"))
    assert r["disposition"] == ("mfr-bound" if bound else "mfr-binding")
    links = conn.execute("SELECT count(*) c FROM item_document WHERE doc_id=%s",
                         (r["doc_id"],)).fetchone()["c"]
    assert (links > 0) is bound
