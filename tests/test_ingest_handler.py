"""ingest.run handler (S1.1, PRD §1 / handbook §1): parse -> normalize -> diff
against item_mirror -> upsert changed -> emit resolve.group per new/changed MD
item. No AI. Row anomalies + missing_mfr_ref reported on the job result, never
silent (CLAUDE.md).

Real Postgres, no mocks (CLAUDE.md). The `conn` fixture never commits, so
item_mirror writes roll back on close; a two-run test reuses one connection so
the second run sees the first's (uncommitted) mirror rows — the same visibility
the diff relies on in production.
"""

from __future__ import annotations

import dataclasses
import logging

import pandas as pd
import pytest

from app import queue
from app.config import Ingest, load_config
from app.handlers import ingest as ih
from app.workers import runner as runner_mod

# Real LJ export header row (subset the profile maps).
LJ_HEADERS = [
    "Št.", "Opis", "Dobaviteljeva št. artikla", "Opis za iskanje",
    "Šifra proizvajalca", "Razred medicinskega pripomočka",
]


def _lj(st, opis, dobav="", sifra="011", razred="RAZRED IIA", opis_isk=""):
    return {
        "Št.": st, "Opis": opis, "Dobaviteljeva št. artikla": dobav,
        "Opis za iskanje": opis_isk, "Šifra proizvajalca": sifra,
        "Razred medicinskega pripomočka": razred,
    }


def _write(path, rows):
    pd.DataFrame(rows, columns=LJ_HEADERS).to_csv(path, index=False)
    return str(path)


def _run(conn, ref, *, priority="delta", cfg=None, records=None, source="csv",
         catalogue="LJ"):
    job = {"id": 1, "payload": {"source": source, "ref": ref, "catalogue": catalogue},
           "priority": priority}
    return ih.handle_ingest_run(conn, job, cfg=cfg, records=records)


def _run_via_runner(conn, ref, *, priority="delta", cfg=None, records=None,
                     source="csv", catalogue="LJ", dry_run=None):
    """Like _run, but drives handle_ingest_run through the real claim -> handler
    -> finish loop (app/workers/runner.py, tests/test_runner.py's run_once
    pattern) instead of calling the handler function directly. Anomalies are
    persisted by the runner's generic post-finish hook only -- ingest.run no
    longer self-records (Task 3 fix round 1) -- so a test asserting on
    data_anomaly rows must go through the runner to see them, exactly as
    production does.

    `dry_run` defaults to None (omitted from the payload, same as every
    pre-existing caller) rather than False, so this still exercises the "no
    dry_run key at all" path for csv/bc_odata callers unchanged."""
    payload = {"source": source, "ref": ref, "catalogue": catalogue}
    if dry_run is not None:
        payload["dry_run"] = dry_run
    job_id = queue.enqueue(
        conn, "ingest.run", payload,
        dedupe_key=f"ingest-test:{source}:{ref}:{catalogue}", priority=priority,
    )

    def handler(c, job):
        return ih.handle_ingest_run(c, job, cfg=cfg, records=records)

    runner_mod.run_once(conn, "test-worker", handlers={"ingest.run": handler})
    return conn.execute("SELECT result FROM job WHERE id=%s", (job_id,)).fetchone()["result"]


def _mirror(conn, item_ref):
    return conn.execute(
        "SELECT * FROM item_mirror WHERE item_ref=%s", (item_ref,)
    ).fetchone()


def _resolve_jobs(conn):
    return conn.execute(
        "SELECT payload, dedupe_key, priority FROM job WHERE type='resolve.group' ORDER BY id"
    ).fetchall()


def _cfg(**ingest_over):
    base = load_config()
    return dataclasses.replace(base, ingest=Ingest(**ingest_over))


# --------------------------------------------------------------------------- #
def test_new_md_items_upsert_and_emit_resolve(conn, tmp_path):
    ref = _write(tmp_path / "lj.csv", [
        _lj("0.900.0001", "MOTOR KL703", "09000001"),
        _lj("A2", "SCALER TIP", "V2", razred="RAZRED IR"),
    ])
    rep = _run(conn, ref, priority="delta")

    assert rep["seen"] == 2 and rep["changed"] == 2
    m = _mirror(conn, "0.900.0001")
    assert m["name"] == "MOTOR KL703" and m["mfr_ref"] == "09000001"
    assert m["md_flag"] is True and m["product_class"] == "IIa" and m["catalogue"] == "LJ"

    jobs = _resolve_jobs(conn)
    assert [j["payload"]["item_ref"] for j in jobs] == ["0.900.0001", "A2"]
    # dedupe key carries the mirror_rev; priority inherited from the ingest job.
    assert all(j["dedupe_key"] == f"resolve:{j['payload']['item_ref']}:{_mirror(conn, j['payload']['item_ref'])['mirror_rev']}"
               for j in jobs)
    assert all(j["priority"] == "delta" for j in jobs)


def test_unchanged_reingest_emits_nothing(conn, tmp_path):
    ref = _write(tmp_path / "lj.csv", [_lj("0.900.0001", "MOTOR KL703", "09000001")])
    r1 = _run(conn, ref)
    rev1 = _mirror(conn, "0.900.0001")["mirror_rev"]
    r2 = _run(conn, ref)  # same file, same content

    assert r1["changed"] == 1
    assert r2["changed"] == 0 and r2["unchanged"] == 1
    assert _mirror(conn, "0.900.0001")["mirror_rev"] == rev1  # rev NOT bumped
    assert len(_resolve_jobs(conn)) == 1  # never re-emitted


def test_changed_field_bumps_rev_and_reemits(conn, tmp_path):
    r1 = _run(conn, _write(tmp_path / "a.csv", [_lj("X1", "OLD NAME", "R1")]))
    rev1 = _mirror(conn, "X1")["mirror_rev"]
    r2 = _run(conn, _write(tmp_path / "b.csv", [_lj("X1", "NEW NAME", "R1")]))
    rev2 = _mirror(conn, "X1")["mirror_rev"]

    assert r1["changed"] == 1 and r2["changed"] == 1
    assert rev2 > rev1
    assert _mirror(conn, "X1")["name"] == "NEW NAME"
    keys = {j["dedupe_key"] for j in _resolve_jobs(conn)}
    assert keys == {f"resolve:X1:{rev1}", f"resolve:X1:{rev2}"}


def test_non_md_item_excluded_and_counted(conn, tmp_path):
    ref = _write(tmp_path / "lj.csv", [
        _lj("MD1", "A DEVICE", "R1", razred="RAZRED IIA"),
        _lj("ND1", "A CONSUMABLE", "R2", razred="NI MP"),
    ])
    rep = _run(conn, ref)

    assert rep["changed"] == 1 and rep["non_md"] == 1
    assert _mirror(conn, "ND1") is None            # non-MD never enters the mirror
    assert [j["payload"]["item_ref"] for j in _resolve_jobs(conn)] == ["MD1"]


def test_md_to_non_md_reclassification_corrects_the_mirror(conn, tmp_path):
    # An item first ingested as MD is mirrored md_flag=TRUE and grouped downstream.
    r1 = _run(conn, _write(tmp_path / "a.csv", [_lj("Z1", "A DEVICE", "R1", razred="RAZRED IIA")]))
    assert _mirror(conn, "Z1")["md_flag"] is True and r1["changed"] == 1

    # BC declassifies it to NI MP. Re-ingest MUST persist md_flag=FALSE — a stale
    # TRUE would let gate mfr-scope (WHERE md_flag IS TRUE) auto-write a production
    # link to a non-MD item. No NEW resolve job for a now-non-MD item.
    r2 = _run(conn, _write(tmp_path / "b.csv", [_lj("Z1", "A DEVICE", "R1", razred="NI MP")]))
    assert _mirror(conn, "Z1")["md_flag"] is False
    assert r2["non_md"] == 1 and r2["reclassified_non_md"] == 1
    assert len(_resolve_jobs(conn)) == 1  # only the original from r1, none from r2
    # ...and it reaches the ledger, not only the job's counts
    # ([results-anomaly-kinds]). Every sibling branch in this handler raises an
    # anomaly beside its count; this one only counted, so /data-quality showed
    # nothing however many items BC declassified. A count lives and dies with
    # one job result; the anomaly is the standing, keyed record that says how
    # often this has happened and to which item.
    kinds = {(a["kind"], a["subject"]) for a in r2["anomalies"]}
    assert ("reclassified_non_md", "Z1") in kinds, r2["anomalies"]


def test_md_unknown_included_when_toggle_on(conn, tmp_path):
    ref = _write(tmp_path / "lj.csv", [_lj("U1", "UNCLASSIFIED", "R1", razred="")])
    rep = _run(conn, ref, cfg=_cfg(process_md_unknown=True))

    assert rep["md_unknown"] == 1 and rep["changed"] == 1
    assert _mirror(conn, "U1") is not None and _mirror(conn, "U1")["md_flag"] is None
    assert len(_resolve_jobs(conn)) == 1


def test_md_unknown_excluded_when_toggle_off(conn, tmp_path):
    ref = _write(tmp_path / "lj.csv", [_lj("U1", "UNCLASSIFIED", "R1", razred="")])
    rep = _run(conn, ref, cfg=_cfg(process_md_unknown=False))

    assert rep["md_unknown"] == 1 and rep["changed"] == 0
    assert _mirror(conn, "U1") is None             # held out of the pipeline
    assert _resolve_jobs(conn) == []


def test_null_mfr_ref_counted_not_skipped(conn, tmp_path):
    ref = _write(tmp_path / "lj.csv", [_lj("M1", "A DEVICE", dobav="")])  # blank mfr_ref
    rep = _run(conn, ref)

    assert rep["missing_mfr_ref"] == 1 and rep["changed"] == 1   # counted, NOT dropped (C1)
    assert _mirror(conn, "M1")["mfr_ref"] is None
    assert len(_resolve_jobs(conn)) == 1


def test_missing_item_ref_is_skipped_not_silent(conn, tmp_path):
    ref = _write(tmp_path / "lj.csv", [
        _lj("", "NO KEY ITEM", "R1"),
        _lj("OK1", "GOOD ITEM", "R2"),
    ])
    rep = _run(conn, ref)

    assert rep["seen"] == 2 and rep["changed"] == 1 and rep["skipped"] == 1
    assert rep["skipped_sample"][0]["reason"] == "missing item_ref"
    assert [j["payload"]["item_ref"] for j in _resolve_jobs(conn)] == ["OK1"]


def test_missing_name_is_skipped(conn, tmp_path):
    # name is NOT NULL in item_mirror; a row with neither Opis nor Opis za iskanje
    # can't be persisted, so it's a reported skip, not a crash.
    ref = _write(tmp_path / "lj.csv", [_lj("K1", "", "R1", opis_isk="")])
    rep = _run(conn, ref)

    assert rep["skipped"] == 1 and rep["skipped_sample"][0]["reason"] == "missing name"
    assert _mirror(conn, "K1") is None


def test_report_has_full_shape(conn, tmp_path):
    rep = _run(conn, _write(tmp_path / "lj.csv", [_lj("R1", "X", "V")]))
    for k in ("seen", "changed", "unchanged", "non_md", "reclassified_non_md",
              "md_unknown", "missing_mfr_ref", "mfr_ref_prose", "skipped",
              "skipped_sample"):
        assert k in rep


def test_invariant11_csv_and_odata_persist_identically(conn, tmp_path):
    # THE handler-level invariant-11 proof: the same catalogue data via a CSV file
    # vs via injected OData records must leave item_mirror and the resolve.group
    # payloads identical (mirror_rev/timestamps excluded — provenance, and the rev
    # sequence advances monotonically across runs).
    data = [
        _lj("0.900.0001", "MOTOR KL703", "09000001"),
        _lj("ND1", "CONSUMABLE", "R2", razred="NI MP"),
        _lj("U1", "UNCLASSIFIED", "R3", razred=""),
    ]
    ref = _write(tmp_path / "lj.csv", data)
    odata = [
        {"no": "0.900.0001", "description": "MOTOR KL703", "vendorItemNo": "09000001",
         "manufacturerCode": "011", "pteMedicalDeviceClass": "RAZRED IIA"},
        {"no": "ND1", "description": "CONSUMABLE", "vendorItemNo": "R2",
         "manufacturerCode": "011", "pteMedicalDeviceClass": "NI MP"},
        {"no": "U1", "description": "UNCLASSIFIED", "vendorItemNo": "R3",
         "manufacturerCode": "011", "pteMedicalDeviceClass": ""},
    ]

    def snapshot():
        cols = "item_ref,name,manufacturer_raw,mfr_ref,md_flag,product_class,udi,catalogue"
        mirror = conn.execute(
            f"SELECT {cols} FROM item_mirror ORDER BY item_ref"
        ).fetchall()
        resolves = [j["payload"] for j in _resolve_jobs(conn)]
        return [dict(r) for r in mirror], resolves

    r_csv = _run(conn, ref)
    mirror_csv, res_csv = snapshot()

    conn.execute("DELETE FROM job")
    conn.execute("DELETE FROM item_mirror")

    r_odata = _run(conn, "odata", source="bc_odata", records=odata)
    mirror_odata, res_odata = snapshot()

    assert mirror_csv == mirror_odata
    assert res_csv == res_odata
    assert r_csv["changed"] == r_odata["changed"]


def test_ingest_records_blank_device_class_as_an_anomaly(conn):
    records = [
        {"no": "U1", "description": "UNCLASSIFIED", "vendorItemNo": "R3",
         "manufacturerCode": "011", "pteMedicalDeviceClass": ""},
        {"no": "K1", "description": "KNOWN", "vendorItemNo": "R4",
         "manufacturerCode": "011", "pteMedicalDeviceClass": "RAZRED IIA"},
    ]
    result = _run_via_runner(conn, "odata", source="bc_odata", records=records)
    rows = conn.execute(
        "SELECT kind, subject FROM data_anomaly WHERE kind='md_class_blank'"
    ).fetchall()
    assert [r["subject"] for r in rows] == ["U1"]
    # job.result (migration 019) carries the same anomaly -- the runner's
    # generic hook persists it AND queue.finish persists the envelope.
    assert {"kind": "md_class_blank", "subject": "U1", "catalogue": "LJ", "detail": {}} \
        in result["anomalies"]


def test_ingest_records_prose_in_mfr_ref_as_an_anomaly(conn):
    records = [
        {"no": "P1", "description": "WIDGET", "vendorItemNo": "NE BO VEC NA ZALOGI!",
         "manufacturerCode": "011", "pteMedicalDeviceClass": "RAZRED IIA"},
        {"no": "P2", "description": "BUR", "vendorItemNo": "104 H251EF 060",
         "manufacturerCode": "011", "pteMedicalDeviceClass": "RAZRED IIA"},
    ]
    _run_via_runner(conn, "odata", source="bc_odata", records=records)
    rows = conn.execute(
        "SELECT subject FROM data_anomaly WHERE kind='mfr_ref_prose'"
    ).fetchall()
    assert [r["subject"] for r in rows] == ["P1"], (
        "P2 is a legitimate Komet ISO bur number containing spaces: a space or "
        "letter heuristic would destroy 652 real codes. Only '!' is safe."
    )


# --------------------------------------------------------------------------- #
# The prose scrub (2026-08-13). Flagging was never enough: the remark was still
# written to item_mirror.mfr_ref, materialized into item_group_member by RESOLVE
# and then compared against document REF codes under invariant 3.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value,kept", [
    # The real BC values, verbatim from the vendor-master analysis.
    ("NE BO VEČ NA ZALOGI!", False),   # x122
    ("OPERA!", False),                 # x48
    ("NI VEČ DOBAVLJIVO!", False),     # x27
    ("NI DOBAVLJIVO!!!", False),       # x26
    ("NE NAROČAJ!", False),            # x15
    ("samo po naročilu!", False),      # x11
    # Legitimate article numbers that any cleverer rule would destroy.
    ("104 H251EF 060", True),          # Komet ISO bur code: spaces are legitimate
    ("NI DOBAVLJIVO", True),           # no "!" -> not our call to make
    ("061.7310", True),
    ("645986", True),
])
def test_only_bang_values_are_scrubbed(conn, tmp_path, value, kept):
    ref = _write(tmp_path / "lj.csv", [_lj("S1", "A DEVICE", dobav=value)])
    rep = _run(conn, ref)

    stored = _mirror(conn, "S1")["mfr_ref"]
    if kept:
        assert stored == value, (
            "985 populated mfr_ref values contain a space and 652 of those are "
            "real codes: only the '!' rule is safe, nothing cleverer."
        )
        assert rep["mfr_ref_prose"] == 0
    else:
        assert stored is None, (
            "prose must be scrubbed to NULL, never to '' -- an empty string is "
            "compared as a code, a NULL means 'no article number'."
        )
        assert rep["mfr_ref_prose"] == 1
    assert rep["changed"] == 1                 # scrubbed, never skipped


def test_scrubbed_prose_is_counted_and_its_value_kept_on_the_anomaly(conn):
    """CLAUDE.md: missing mfr_ref is counted and reported, never silent. The
    original remark survives on the anomaly, so the scrub loses no evidence."""
    records = [
        {"no": "P1", "description": "WIDGET", "vendorItemNo": "NE BO VEČ NA ZALOGI!",
         "manufacturerCode": "011", "pteMedicalDeviceClass": "RAZRED IIA"},
    ]
    result = _run_via_runner(conn, "odata", source="bc_odata", records=records)

    assert result["mfr_ref_prose"] == 1
    assert result["missing_mfr_ref"] == 0      # a scrub is not a blank cell
    row = conn.execute(
        "SELECT detail FROM data_anomaly WHERE kind='mfr_ref_prose' AND subject='P1'"
    ).fetchone()
    assert row["detail"] == {"value": "NE BO VEČ NA ZALOGI!"}


def test_a_prose_value_already_in_the_mirror_is_healed_on_re_ingest(conn, tmp_path):
    """The scrub reaches rows ingested before it existed: the diff sees
    prose -> NULL as a change, upserts, and re-emits resolve.group, which
    refreshes item_group_member.mfr_ref (the REF-gate comparand)."""
    conn.execute(
        # Identical to what the export normalizes to EXCEPT the prose value, so
        # the scrub is the only thing the diff can be reacting to.
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, mfr_ref, "
        "md_flag, product_class, catalogue, updated_at) "
        "VALUES ('S9','A DEVICE','011','UKINJENO!',true,'IIa','LJ',now())"
    )
    ref = _write(tmp_path / "lj.csv", [_lj("S9", "A DEVICE", dobav="UKINJENO!")])
    rep = _run(conn, ref)

    assert rep["changed"] == 1 and rep["unchanged"] == 0
    assert _mirror(conn, "S9")["mfr_ref"] is None
    assert [j["payload"]["item_ref"] for j in _resolve_jobs(conn)] == ["S9"]


# --------------------------------------------------------------------------- #
# Task 4: dry_run and the upload source. A preview and an apply run the same
# code twice over the same spooled bytes -- see the module docstring.
# --------------------------------------------------------------------------- #
def _spool_items(conn, rows, *, filename="Artikli.csv", age_days=0):
    """Put a BC export into the import spool the way the web form does."""
    import io
    buf = io.StringIO()
    pd.DataFrame(rows, columns=LJ_HEADERS).to_csv(buf, index=False)
    return conn.execute(
        "INSERT INTO import_inbox (kind, filename, content, catalogue, created_at) "
        "VALUES ('items', %s, %s, 'LJ', now() - make_interval(days => %s)) RETURNING id",
        (filename, buf.getvalue().encode(), age_days),
    ).fetchone()["id"]


def _run_upload(conn, upload_id, *, dry_run, priority="interactive"):
    job = {"id": 1, "priority": priority, "payload": {
        "source": "upload", "ref": {"upload_id": upload_id},
        "catalogue": "LJ", "dry_run": dry_run}}
    return ih.handle_ingest_run(conn, job)


def test_dry_run_reports_the_diff_and_writes_nothing(conn):
    uid = _spool_items(conn, [_lj("A1", "Composite"), _lj("A2", "Bur")])
    before_mirror = conn.execute("SELECT count(*) c FROM item_mirror").fetchone()["c"]
    before_jobs = conn.execute("SELECT count(*) c FROM job").fetchone()["c"]

    res = _run_upload(conn, uid, dry_run=True)

    assert res["dry_run"] is True
    assert res["seen"] == 2
    assert res["changed"] == 2          # both are new against an empty mirror
    assert conn.execute("SELECT count(*) c FROM item_mirror").fetchone()["c"] == before_mirror
    assert conn.execute("SELECT count(*) c FROM job").fetchone()["c"] == before_jobs


def test_dry_run_leaves_the_spool_row_for_the_apply(conn):
    uid = _spool_items(conn, [_lj("A1", "Composite")])
    _run_upload(conn, uid, dry_run=True)
    assert conn.execute(
        "SELECT count(*) c FROM import_inbox WHERE id=%s", (uid,)).fetchone()["c"] == 1


def test_dry_run_routes_anomalies_away_from_the_standing_ledger(conn):
    """runner.py records data_anomaly whenever result["anomalies"] is non-empty.
    A preview that changed nothing must not write the ledger, or the apply
    double-counts every observation."""
    uid = _spool_items(conn, [_lj("A1", "Composite", dobav="")])   # missing mfr_ref
    res = _run_upload(conn, uid, dry_run=True)

    assert res["anomalies"] == []
    kinds = {a["kind"] for a in res["anomalies_preview"]}
    assert "mfr_ref_missing" in kinds


def test_apply_writes_the_mirror_emits_resolve_and_consumes_the_spool(conn):
    uid = _spool_items(conn, [_lj("A1", "Composite")])
    res = _run_upload(conn, uid, dry_run=False)

    assert res["dry_run"] is False
    assert res["changed"] == 1
    assert conn.execute(
        "SELECT count(*) c FROM item_mirror WHERE item_ref='A1'").fetchone()["c"] == 1
    assert conn.execute(
        "SELECT count(*) c FROM job WHERE type='resolve.group'").fetchone()["c"] == 1
    assert conn.execute(
        "SELECT count(*) c FROM import_inbox WHERE id=%s", (uid,)).fetchone()["c"] == 0


def test_apply_reports_anomalies_normally(conn):
    uid = _spool_items(conn, [_lj("A1", "Composite", dobav="")])
    res = _run_upload(conn, uid, dry_run=False)
    assert {a["kind"] for a in res["anomalies"]} >= {"mfr_ref_missing"}
    assert "anomalies_preview" not in res


def test_preview_then_apply_agree_on_an_unchanged_mirror(conn):
    """The number the operator confirmed is the number the apply produces,
    when nothing else moved in between."""
    uid = _spool_items(conn, [_lj("A1", "Composite"), _lj("A2", "Bur")])
    preview = _run_upload(conn, uid, dry_run=True)
    applied = _run_upload(conn, uid, dry_run=False)
    assert (preview["seen"], preview["changed"], preview["unchanged"]) == \
           (applied["seen"], applied["changed"], applied["unchanged"])


def test_a_delta_file_leaves_absent_items_untouched(conn):
    """An export trimmed to changed rows must not retire anything: INGEST has
    never deleted rows absent from a file, and that is what makes a delta
    upload structurally indistinguishable from a full one."""
    full = _spool_items(conn, [_lj("A1", "Composite"), _lj("A2", "Bur")])
    _run_upload(conn, full, dry_run=False)

    delta = _spool_items(conn, [_lj("A1", "Composite RENAMED")])
    res = _run_upload(conn, delta, dry_run=False)

    assert res["seen"] == 1
    assert res["changed"] == 1
    names = {r["item_ref"]: r["name"] for r in conn.execute(
        "SELECT item_ref, name FROM item_mirror").fetchall()}
    assert names == {"A1": "Composite RENAMED", "A2": "Bur"}


def test_upload_run_collects_abandoned_spool_rows(conn):
    from app import import_spool

    stale = _spool_items(conn, [_lj("OLD", "x")],
                         age_days=import_spool.SPOOL_RETENTION_DAYS + 1)
    uid = _spool_items(conn, [_lj("A1", "Composite")])
    res = _run_upload(conn, uid, dry_run=True)

    assert res["spool_gc"] == 1
    assert conn.execute(
        "SELECT count(*) c FROM import_inbox WHERE id=%s", (stale,)).fetchone()["c"] == 0


def test_a_missing_spool_row_dead_letters_rather_than_importing_nothing(conn):
    from app import import_spool

    with pytest.raises(import_spool.SpoolMissing):
        _run_upload(conn, 999999, dry_run=False)


def test_csv_path_runs_are_unaffected_and_default_to_writing(tmp_path, conn):
    """No dry_run key in the payload means the existing /ingest form and the
    scheduler keep their fire-and-report behaviour."""
    ref = _write(tmp_path / "e.csv", [_lj("A1", "Composite")])
    res = _run(conn, ref)
    assert res["dry_run"] is False
    assert conn.execute(
        "SELECT count(*) c FROM item_mirror WHERE item_ref='A1'").fetchone()["c"] == 1


def test_dry_run_does_not_write_a_reclassification_it_still_reports(conn):
    """The third of the handler's three write points: an already-mirrored MD
    item that BC has since declassified to NI MP. A preview must observe and
    report the reclassification -- counted, and anomalied -- without touching
    the mirror. Every other new fixture in this file uses _lj()'s default
    razred="RAZRED IIA", so this is the only test that reaches this branch."""
    mirrored = _spool_items(conn, [_lj("A1", "Composite", razred="RAZRED IIA")])
    _run_upload(conn, mirrored, dry_run=False)
    assert _mirror(conn, "A1")["md_flag"] is True

    declassified = _spool_items(conn, [_lj("A1", "Composite", razred="NI MP")])
    res = _run_upload(conn, declassified, dry_run=True)

    # Unchanged: the preview observed the reclassification but wrote nothing.
    assert _mirror(conn, "A1")["md_flag"] is True
    assert res["reclassified_non_md"] == 1
    assert res["anomalies"] == []
    kinds = {(a["kind"], a["subject"]) for a in res["anomalies_preview"]}
    assert ("reclassified_non_md", "A1") in kinds


def test_dry_run_through_the_runner_writes_no_anomaly_ledger_rows(conn):
    """The report-shape tests above (anomalies == [] / anomalies_preview
    populated) are one inference away from the actual hazard this task exists
    to prevent: a row landing in data_anomaly. Drive a dry run through the
    real claim -> handler -> finish loop -- the only path that writes
    data_anomaly, since ingest.run no longer self-records -- and prove the
    table gains nothing. Compared as a before/after count, not asserted at
    zero, so this stays honest if a fixture ever seeds the table."""
    uid = _spool_items(conn, [_lj("A1", "Composite", dobav="")])   # missing mfr_ref
    before = conn.execute("SELECT count(*) c FROM data_anomaly").fetchone()["c"]

    result = _run_via_runner(
        conn, {"upload_id": uid}, source="upload", catalogue="LJ", dry_run=True)

    after = conn.execute("SELECT count(*) c FROM data_anomaly").fetchone()["c"]
    assert after == before
    assert result["anomalies"] == []
    assert {a["kind"] for a in result["anomalies_preview"]} >= {"mfr_ref_missing"}


def test_log_line_names_whether_the_run_was_a_dry_run(conn, caplog):
    """'did last night's import actually write?' must be answerable from the
    summary log line alone -- a preview and a real ingest must not log
    identically."""
    uid = _spool_items(conn, [_lj("A1", "Composite")])
    with caplog.at_level(logging.INFO, logger="dentalia.handler.ingest"):
        _run_upload(conn, uid, dry_run=True)
    assert "dry_run=True" in caplog.text

    caplog.clear()
    uid2 = _spool_items(conn, [_lj("A2", "Bur")])
    with caplog.at_level(logging.INFO, logger="dentalia.handler.ingest"):
        _run_upload(conn, uid2, dry_run=False)
    assert "dry_run=False" in caplog.text
