"""SCHEDULER (S1.5): period keys, restart-safety ledger, and the four cron
ticks (monthly ingest, expiry scan, failure monitor, weekly report).

`now` is always injected — no `datetime.now()` in the code under test (see
docs/specs/scheduler.md §5/§6). Real Postgres per CLAUDE.md.
"""

from __future__ import annotations

import datetime as dt
import itertools
import os

import pytest

from app import queue
from app.config import Scheduler


def test_period_key_month():
    from app.scheduler import period_key_month

    assert period_key_month(dt.datetime(2026, 7, 23, tzinfo=dt.timezone.utc)) == "2026-07"


def test_period_key_day():
    from app.scheduler import period_key_day

    assert period_key_day(dt.datetime(2026, 7, 23, tzinfo=dt.timezone.utc)) == "2026-07-23"


def test_period_key_isoweek():
    from app.scheduler import period_key_isoweek

    # 2026-07-23 is a Thursday in ISO week 30 of 2026.
    assert period_key_isoweek(dt.datetime(2026, 7, 23, tzinfo=dt.timezone.utc)) == "2026-W30"


def test_already_ran_false_when_no_ledger_row(conn):
    from app.scheduler import already_ran

    assert already_ran(conn, "ingest.monthly:LJ", "2026-07") is False


def test_record_run_then_already_ran_true(conn):
    from app.scheduler import already_ran, record_run

    jid = queue.enqueue(conn, "ingest.run", {}, "sched-test-1")
    record_run(conn, "ingest.monthly:LJ", "2026-07", jid)
    assert already_ran(conn, "ingest.monthly:LJ", "2026-07") is True
    # a different period for the same name is independent
    assert already_ran(conn, "ingest.monthly:LJ", "2026-08") is False


def test_record_run_accepts_null_job_id(conn):
    # a dedupe-suppressed enqueue (queue.enqueue returned None) must still be
    # recordable — the ledger gates on the period, not on a live job row.
    from app.scheduler import already_ran, record_run

    record_run(conn, "failure-monitor", "2026-07-23", None)
    assert already_ran(conn, "failure-monitor", "2026-07-23") is True


# --------------------------------------------------------------------------- #
# _tick_ingest_monthly (GAP D)
# --------------------------------------------------------------------------- #
NOW = dt.datetime(2026, 7, 23, tzinfo=dt.timezone.utc)


def test_ingest_tick_noop_when_watch_dir_unset(conn):
    from app.scheduler import _tick_ingest_monthly, already_ran

    cfg = Scheduler()  # default: ingest_watch_dir=""
    result = _tick_ingest_monthly(conn, cfg, NOW)
    assert result["fired"] is False
    assert result["job_id"] is None
    # ledger still written — a disabled/no-file month is a normal outcome,
    # not a retry-worthy failure (docs/specs/scheduler.md §5).
    assert already_ran(conn, "ingest.monthly:LJ", "2026-07") is True


def test_ingest_tick_enqueues_when_file_present(conn, tmp_path):
    from app.scheduler import _tick_ingest_monthly

    (tmp_path / "Artikli 3.7.2026.xlsx").write_bytes(b"stub")
    cfg = Scheduler(ingest_watch_dir=str(tmp_path))

    result = _tick_ingest_monthly(conn, cfg, NOW)

    assert result["fired"] is True
    row = conn.execute(
        "SELECT type, payload FROM job WHERE id=%s", (result["job_id"],)
    ).fetchone()
    assert row["type"] == "ingest.run"
    assert row["payload"]["source"] == "csv"
    assert row["payload"]["catalogue"] == "LJ"
    assert row["payload"]["ref"].endswith("Artikli 3.7.2026.xlsx")


def test_ingest_tick_picks_most_recently_modified_file(conn, tmp_path):
    # mtime, not filename sort — "zzz_old.xlsx" sorts lexicographically AFTER
    # "aaa_new.xlsx" despite being the older file; a filename-sort implementation
    # would wrongly pick it. mtime correctly picks the actually-newer file.
    import os
    import time

    from app.scheduler import _tick_ingest_monthly

    lexicographically_last_but_older = tmp_path / "zzz_old.xlsx"
    lexicographically_first_but_newer = tmp_path / "aaa_new.xlsx"
    lexicographically_last_but_older.write_bytes(b"stub")
    lexicographically_first_but_newer.write_bytes(b"stub")
    now_ts = time.time()
    os.utime(lexicographically_last_but_older, (now_ts - 100, now_ts - 100))
    os.utime(lexicographically_first_but_newer, (now_ts, now_ts))
    cfg = Scheduler(ingest_watch_dir=str(tmp_path))

    result = _tick_ingest_monthly(conn, cfg, NOW)

    row = conn.execute(
        "SELECT payload FROM job WHERE id=%s", (result["job_id"],)
    ).fetchone()
    assert row["payload"]["ref"].endswith("aaa_new.xlsx")


def test_ingest_tick_same_month_second_call_is_noop(conn, tmp_path):
    from app.scheduler import _tick_ingest_monthly

    (tmp_path / "a.xlsx").write_bytes(b"stub")
    cfg = Scheduler(ingest_watch_dir=str(tmp_path))

    first = _tick_ingest_monthly(conn, cfg, NOW)
    second = _tick_ingest_monthly(conn, cfg, NOW)

    assert first["fired"] is True
    assert second["fired"] is False
    count = conn.execute(
        "SELECT count(*) AS c FROM job WHERE type='ingest.run'"
    ).fetchone()["c"]
    assert count == 1


def test_ingest_tick_restart_safety_ledger_blocks_reenqueue(conn, tmp_path):
    # Simulate a prior process whose job already reached a terminal state
    # (freeing the C2 dedupe key) and whose ledger row was written. A fresh
    # tick must NOT re-enqueue — the ledger, not job status, gates it.
    from app.scheduler import _tick_ingest_monthly, record_run

    (tmp_path / "a.xlsx").write_bytes(b"stub")
    cfg = Scheduler(ingest_watch_dir=str(tmp_path))
    record_run(conn, "ingest.monthly:LJ", "2026-07", None)

    result = _tick_ingest_monthly(conn, cfg, NOW)

    assert result["fired"] is False
    count = conn.execute(
        "SELECT count(*) AS c FROM job WHERE type='ingest.run'"
    ).fetchone()["c"]
    assert count == 0


def test_ingest_tick_fires_again_next_month(conn, tmp_path):
    from app.scheduler import _tick_ingest_monthly

    (tmp_path / "a.xlsx").write_bytes(b"stub")
    cfg = Scheduler(ingest_watch_dir=str(tmp_path))

    first = _tick_ingest_monthly(conn, cfg, NOW)
    next_month = dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)
    second = _tick_ingest_monthly(conn, cfg, next_month)

    assert first["fired"] is True
    assert second["fired"] is True
    assert first["job_id"] != second["job_id"]


# --------------------------------------------------------------------------- #
# tick() — orchestrator
# --------------------------------------------------------------------------- #
def test_the_shipped_renewal_horizon_is_the_thirty_days_dentalia_ruled():
    """[renewal-horizon-default-ignored]. The ruling of 2026-08-18 ("bi rekla
    mesec dni prej") replaced the (90, 60, 30) placeholder, and the same day
    set the `Renewal` dataclass default to (30,) on the premise that "no TOML overlay
    is in use, so this dataclass default IS the live value". The premise was
    wrong: `load_config()` never constructs `Renewal()` bare -- it always passes
    `horizon_days=` explicitly, falling back to the placeholder when no TOML sets
    the key, and nothing in the tree sets it. So the default was dead code and
    the shipped horizon stayed 90 days.

    Asserted through `load_config()`, not against the dataclass, because that is
    exactly the gap: the dataclass has been right since 2026-08-18 and the process
    still ran on 90. The scheduler and the weekly report both scan at max(...),
    so this one number IS the horizon."""
    from app.config import load_config

    assert load_config().renewal.horizon_days == (30,)
    assert max(load_config().renewal.horizon_days) == 30


def test_tick_before_any_boundary_returns_zero_counts(conn):
    from app.config import load_config
    from app.scheduler import tick

    cfg = load_config()  # default Scheduler(): ingest disabled, both emit flags off
    result = tick(conn, cfg, NOW)

    assert result["ingest.monthly"]["fired"] is False
    assert result["report.weekly"]["fired"] is True  # weekly report always runs
    assert result["expiry-scan"]["fired"] is True
    assert result["failure-monitor"]["fired"] is True


def test_tick_one_raising_cron_does_not_block_others(conn, monkeypatch):
    import app.scheduler as sched
    from app.config import load_config

    def boom(*a, **kw):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(sched, "_tick_failure_monitor", boom)
    cfg = load_config()

    result = sched.tick(conn, cfg, NOW)

    # the raising cron's own result key is absent/errored, but the others ran
    assert result["report.weekly"]["fired"] is True
    assert result["ingest.monthly"]["fired"] is False
    assert sched.already_ran(conn, "report.weekly", "2026-W30") is True


def test_ingest_tick_missing_dir_is_a_logged_miss_not_an_error(conn):
    from app.scheduler import _tick_ingest_monthly, already_ran

    cfg = Scheduler(ingest_watch_dir="/no/such/path/at/all")
    result = _tick_ingest_monthly(conn, cfg, NOW)

    assert result["fired"] is False
    assert already_ran(conn, "ingest.monthly:LJ", "2026-07") is True


def test_ingest_tick_atomic_enqueue_and_ledger(conn, tmp_path, monkeypatch):
    # A failure after the enqueue but before the ledger write must roll back
    # both — otherwise a crash could leave a live job with no ledger row
    # (harmless re-fire next tick) or, worse, a ledger row with no job.
    import app.scheduler as sched

    (tmp_path / "a.xlsx").write_bytes(b"stub")
    cfg = Scheduler(ingest_watch_dir=str(tmp_path))

    def boom(*a, **kw):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(sched, "record_run", boom)

    with pytest.raises(RuntimeError):
        sched._tick_ingest_monthly(conn, cfg, NOW)
    conn.rollback()

    assert conn.execute(
        "SELECT count(*) AS c FROM job WHERE type='ingest.run'"
    ).fetchone()["c"] == 0
    assert conn.execute(
        "SELECT count(*) AS c FROM scheduler_run"
    ).fetchone()["c"] == 0


# --------------------------------------------------------------------------- #
# _tick_expiry_scan (GAP E)
# --------------------------------------------------------------------------- #
#: A counter, not `id(object())`: CPython reuses the id of an object it
#: collected on the same line, so two documents inserted in one loop could
#: collide on `content_hash UNIQUE` -- which they did, 2026-08-27.
_doc_seq = itertools.count()


def _insert_production_doc(conn, validity_to):
    """A production document with NO production link, so nothing resolves a
    manufacturer for it. That is the unaddressable case, not the normal one --
    use `_insert_linked_doc` for a document the renewal chase can key on."""
    row = conn.execute(
        """
        INSERT INTO document (type, regulation, coverage_scope, content_hash,
                               archive_url, status, validity_to)
        VALUES ('DoC', 'MDR', 'group', %s, 'file:///x', 'production', %s)
        RETURNING doc_id
        """,
        (f"hash-expiry-{os.getpid()}-{next(_doc_seq)}", validity_to),
    ).fetchone()
    return row["doc_id"]


def _insert_linked_doc(conn, validity_to, *, manufacturer, item_ref):
    """A document reached the way the renewal chase reaches it: through the
    items it covers and the group they belong to. Same path as
    `email_request.manufacturers_for_docs` and as `/expiry`."""
    doc_id = _insert_production_doc(conn, validity_to)
    gid = conn.execute(
        "INSERT INTO item_group (canonical_manufacturer, label) "
        "VALUES (%s, 'fam') RETURNING group_id", (manufacturer,),
    ).fetchone()["group_id"]
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, catalogue, updated_at) "
        "VALUES (%s,'thing','raw','LJ', now()) ON CONFLICT (item_ref) DO NOTHING",
        (item_ref,))
    conn.execute(
        "INSERT INTO item_group_member (group_id, item_ref, match_basis) "
        "VALUES (%s,%s,'mfr-scope') ON CONFLICT DO NOTHING", (gid, item_ref))
    conn.execute(
        "INSERT INTO item_document (item_ref, doc_id, status, match_basis) "
        "VALUES (%s,%s,'production','mfr-scope')", (item_ref, doc_id))
    return doc_id


def test_expiry_scan_gated_off_emits_nothing(conn):
    from app.scheduler import _tick_expiry_scan

    _insert_production_doc(conn, dt.date(2026, 7, 23) + dt.timedelta(days=5))
    cfg = Scheduler()  # default: expiry_email_enabled=False

    result = _tick_expiry_scan(conn, cfg, horizon_days=90, cadence_days=7, now=NOW)

    assert result["expiring_count"] == 1
    count = conn.execute(
        "SELECT count(*) AS c FROM job WHERE type='email.request'"
    ).fetchone()["c"]
    assert count == 0


def test_expiry_scan_gated_on_emits_email_request(conn):
    """The unaddressable fallback: no production link, so no manufacturer, so
    no cadence bucket to join. The doc-scoped key is kept for exactly this
    case -- collapsing these onto one `mfr:None:` key would drop every one but
    the first, and they are unrelated documents."""
    from app.scheduler import _tick_expiry_scan

    doc_id = _insert_production_doc(conn, dt.date(2026, 7, 23) + dt.timedelta(days=5))
    cfg = Scheduler(expiry_email_enabled=True)

    result = _tick_expiry_scan(conn, cfg, horizon_days=90, cadence_days=7, now=NOW)

    assert result["expiring_count"] == 1
    assert result["unaddressable_count"] == 1
    row = conn.execute(
        "SELECT payload, dedupe_key FROM job WHERE type='email.request'"
    ).fetchone()
    assert row["payload"]["doc_id"] == doc_id
    assert row["dedupe_key"] == f"email.request:doc:{doc_id}"


def test_expiry_scan_same_day_second_call_is_noop(conn):
    from app.scheduler import _tick_expiry_scan

    _insert_production_doc(conn, dt.date(2026, 7, 23) + dt.timedelta(days=5))
    cfg = Scheduler(expiry_email_enabled=True)

    first = _tick_expiry_scan(conn, cfg, horizon_days=90, cadence_days=7, now=NOW)
    second = _tick_expiry_scan(conn, cfg, horizon_days=90, cadence_days=7, now=NOW)

    assert first["fired"] is True
    assert second["fired"] is False
    count = conn.execute(
        "SELECT count(*) AS c FROM job WHERE type='email.request'"
    ).fetchone()["c"]
    assert count == 1


# --------------------------------------------------------------------------- #
# PRD §0's renewal key: manufacturer + cadence bucket, not the document.
# Client cadence rule 2026-08-20 (spec §7.2); the contract table was changed
# then and the producer was not, which is what these pin down.
# --------------------------------------------------------------------------- #
def test_expiry_scan_keys_by_manufacturer_and_cadence(conn):
    from app.handlers.email_request import period_key
    from app.scheduler import _tick_expiry_scan

    _insert_linked_doc(conn, dt.date(2026, 7, 23) + dt.timedelta(days=5),
                       manufacturer="IVOCLAR", item_ref="EXP-1")
    cfg = Scheduler(expiry_email_enabled=True)

    result = _tick_expiry_scan(conn, cfg, horizon_days=90, cadence_days=7, now=NOW)

    assert result["unaddressable_count"] == 0
    row = conn.execute(
        "SELECT dedupe_key FROM job WHERE type='email.request'").fetchone()
    assert row["dedupe_key"] == (
        f"email.request:mfr:IVOCLAR:{period_key(NOW.date(), 7)}")


def test_expiry_scan_collapses_one_manufacturers_documents(conn):
    """The measured case: 52 IVOCLAR documents share one expiry date, and the
    old per-document key would have made 52 jobs. The handler rebuilds the full
    list from `lapsing_for_manufacturer`, so one job still chases all of them."""
    from app.scheduler import _tick_expiry_scan

    for n in range(3):
        _insert_linked_doc(conn, dt.date(2026, 7, 23) + dt.timedelta(days=5),
                           manufacturer="IVOCLAR", item_ref=f"EXP-M{n}")
    cfg = Scheduler(expiry_email_enabled=True)

    result = _tick_expiry_scan(conn, cfg, horizon_days=90, cadence_days=7, now=NOW)

    assert result["expiring_count"] == 3
    assert result["request_jobs"] == 1
    count = conn.execute(
        "SELECT count(*) AS c FROM job WHERE type='email.request'"
    ).fetchone()["c"]
    assert count == 1


def test_expiry_scan_keeps_manufacturers_apart(conn):
    from app.scheduler import _tick_expiry_scan

    _insert_linked_doc(conn, dt.date(2026, 7, 23) + dt.timedelta(days=5),
                       manufacturer="IVOCLAR", item_ref="EXP-A")
    _insert_linked_doc(conn, dt.date(2026, 7, 23) + dt.timedelta(days=6),
                       manufacturer="KOMET", item_ref="EXP-B")
    cfg = Scheduler(expiry_email_enabled=True)

    result = _tick_expiry_scan(conn, cfg, horizon_days=90, cadence_days=7, now=NOW)

    assert result["request_jobs"] == 2
    keys = {r["dedupe_key"] for r in conn.execute(
        "SELECT dedupe_key FROM job WHERE type='email.request'").fetchall()}
    assert len(keys) == 2
    assert all(k.startswith("email.request:mfr:") for k in keys)


# --------------------------------------------------------------------------- #
# _tick_failure_monitor (GAP E, GAP F)
# --------------------------------------------------------------------------- #
def _seed_discovery_log(conn, manufacturer, outcomes):
    row = conn.execute(
        "INSERT INTO item_group (canonical_manufacturer) VALUES (%s) RETURNING group_id",
        (manufacturer,),
    ).fetchone()
    group_id = row["group_id"]
    for outcome in outcomes:
        conn.execute(
            "INSERT INTO discovery_log (group_id, source, outcome) VALUES (%s, 'search', %s)",
            (group_id, outcome),
        )
    return group_id


def test_failure_monitor_spike_detected(conn):
    from app.scheduler import _tick_failure_monitor

    _seed_discovery_log(conn, "ACME", ["miss"] * 4 + ["hit"] * 1)  # 5 samples, 80% miss
    cfg = Scheduler(failure_miss_rate_threshold=0.5, failure_min_sample=5)

    result = _tick_failure_monitor(conn, cfg, now=NOW)

    assert any(s["manufacturer"] == "ACME" for s in result["spikes"])


def test_failure_monitor_below_sample_floor_excluded(conn):
    from app.scheduler import _tick_failure_monitor

    _seed_discovery_log(conn, "SMALLCO", ["miss", "miss"])  # 2 samples, 100% miss
    cfg = Scheduler(failure_miss_rate_threshold=0.5, failure_min_sample=5)

    result = _tick_failure_monitor(conn, cfg, now=NOW)

    assert result["spikes"] == []


def test_failure_monitor_gated_off_emits_nothing(conn):
    from app.scheduler import _tick_failure_monitor

    _seed_discovery_log(conn, "ACME", ["miss"] * 5)
    cfg = Scheduler(failure_miss_rate_threshold=0.5, failure_min_sample=5)  # reonboard off

    _tick_failure_monitor(conn, cfg, now=NOW)

    count = conn.execute(
        "SELECT count(*) AS c FROM job WHERE type='playbook.reonboard'"
    ).fetchone()["c"]
    assert count == 0


def test_failure_monitor_emits_a_reonboard_the_handler_can_actually_run(conn):
    """F37. `playbook.reonboard` probes ONE index URL, and this tick used to
    enqueue a manufacturer name and nothing else -- so every spike it fired
    dead-lettered on the handler's NotImplementedError. The recipe is resolved
    here now, and the payload carries the slug and the URL.

    EDENTA is used because it is one of the three playbooks that authors a crawl
    recipe; the point is a real authored URL, not a fixture one."""
    from app.scheduler import _tick_failure_monitor

    _seed_discovery_log(conn, "EDENTA", ["miss"] * 5)
    cfg = Scheduler(
        failure_miss_rate_threshold=0.5,
        failure_min_sample=5,
        failure_reonboard_enabled=True,
    )

    _tick_failure_monitor(conn, cfg, now=NOW)

    row = conn.execute(
        "SELECT payload FROM job WHERE type='playbook.reonboard'"
    ).fetchone()
    assert row["payload"]["manufacturer"] == "EDENTA"
    assert row["payload"]["reason"] == "discovery-failure-spike"
    assert row["payload"]["slug"] == "edenta"
    assert row["payload"]["index_url"].startswith("https://")


def test_a_manufacturer_with_no_crawl_recipe_is_recorded_rather_than_enqueued(conn):
    """38 of 384 manufacturers have a playbook at all, so "no recipe" is an
    ordinary state and not an error. Enqueueing anyway is what filled the dead
    queue; the tick counts it and moves on."""
    from app.scheduler import _tick_failure_monitor

    _seed_discovery_log(conn, "ACME", ["miss"] * 5)
    cfg = Scheduler(
        failure_miss_rate_threshold=0.5,
        failure_min_sample=5,
        failure_reonboard_enabled=True,
    )

    result = _tick_failure_monitor(conn, cfg, now=NOW)

    assert result["skipped"] == ["ACME"]
    assert conn.execute(
        "SELECT count(*) c FROM job WHERE type='playbook.reonboard'"
    ).fetchone()["c"] == 0


# --------------------------------------------------------------------------- #
# _tick_weekly_report (GAP A)
# --------------------------------------------------------------------------- #
def test_weekly_report_fires_once_per_isoweek(conn):
    from app.scheduler import _tick_weekly_report

    result = _tick_weekly_report(conn, now=NOW)

    assert result["fired"] is True
    row = conn.execute(
        "SELECT type, payload, dedupe_key FROM job WHERE id=%s", (result["job_id"],)
    ).fetchone()
    assert row["type"] == "report.weekly"
    assert row["payload"]["period_key"] == "2026-W30"
    assert row["dedupe_key"] == "report:2026-W30"


def test_weekly_report_same_week_second_call_is_noop(conn):
    from app.scheduler import _tick_weekly_report

    first = _tick_weekly_report(conn, now=NOW)
    second = _tick_weekly_report(conn, now=NOW)

    assert first["fired"] is True
    assert second["fired"] is False
    count = conn.execute(
        "SELECT count(*) AS c FROM job WHERE type='report.weekly'"
    ).fetchone()["c"]
    assert count == 1


def test_weekly_report_fires_again_next_week(conn):
    from app.scheduler import _tick_weekly_report

    first = _tick_weekly_report(conn, now=NOW)
    next_week = NOW + dt.timedelta(days=7)
    second = _tick_weekly_report(conn, now=next_week)

    assert first["fired"] is True
    assert second["fired"] is True
    assert first["job_id"] != second["job_id"]


def test_expiry_scan_marks_a_lapsed_document_as_lapsed_and_still_chases_it(conn):
    """A lapsed certificate needs the chase more, not less — so it stays in the
    scan and still gets an `email.request`. What it must not do is arrive at
    S2.4 indistinguishable from an upcoming one: measured on the live registry
    2026-08-18, a 30-day scan returned 61 documents and every one had already
    lapsed, the oldest by 2237 days. Emitting those as "about to expire" is a
    letter to a manufacturer about a certificate that died in 2020."""
    from app.scheduler import _tick_expiry_scan

    upcoming = _insert_production_doc(conn, NOW.date() + dt.timedelta(days=5))
    gone = _insert_production_doc(conn, NOW.date() - dt.timedelta(days=800))
    cfg = Scheduler(expiry_email_enabled=True)

    result = _tick_expiry_scan(conn, cfg, horizon_days=90, cadence_days=7, now=NOW)

    assert result["expiring_count"] == 2      # both are scanned
    assert result["lapsed_count"] == 1        # counted apart

    states = {
        r["payload"]["doc_id"]: r["payload"]["state"]
        for r in conn.execute(
            "SELECT payload FROM job WHERE type='email.request'"
        ).fetchall()
    }
    assert states[upcoming] == "upcoming"
    assert states[gone] == "lapsed"


# --------------------------------------------------------------------------- #
# S2.4 email.poll tick (gated off until a mailbox is wired — GAP G8)
# --------------------------------------------------------------------------- #
def test_period_key_interval_bucket():
    from app.scheduler import period_key_interval

    morning = dt.datetime(2026, 8, 19, 4, tzinfo=dt.timezone.utc)
    later = dt.datetime(2026, 8, 19, 10, tzinfo=dt.timezone.utc)
    assert period_key_interval(morning, 6) == "2026-08-19:00"
    assert period_key_interval(later, 6) == "2026-08-19:01"


def test_email_poll_tick_disabled_records_run_but_emits_nothing(conn):
    from app.scheduler import _tick_email_poll, already_ran

    cfg = Scheduler()  # email_poll_enabled False by default
    result = _tick_email_poll(conn, cfg, now=NOW)

    assert result["fired"] is False
    assert result["job_id"] is None
    assert conn.execute(
        "SELECT count(*) c FROM job WHERE type='email.poll'"
    ).fetchone()["c"] == 0
    # the tick still records its run so a restart does not re-tick the window
    assert already_ran(conn, "email.poll", result["period_key"]) is True


def test_email_poll_tick_enabled_enqueues_with_conventional_dedupe_key(conn):
    from app.scheduler import _tick_email_poll

    cfg = Scheduler(email_poll_enabled=True, email_poll_interval_hours=6)
    result = _tick_email_poll(conn, cfg, now=NOW)

    assert result["fired"] is True
    row = conn.execute(
        "SELECT type, dedupe_key FROM job WHERE id=%s", (result["job_id"],)
    ).fetchone()
    assert row["type"] == "email.poll"
    assert row["dedupe_key"] == f"email.poll:{result['period_key']}"


def test_email_poll_tick_same_window_second_call_is_noop(conn):
    from app.scheduler import _tick_email_poll

    cfg = Scheduler(email_poll_enabled=True)
    first = _tick_email_poll(conn, cfg, now=NOW)
    second = _tick_email_poll(conn, cfg, now=NOW)

    assert first["fired"] is True
    assert second["fired"] is False
    assert conn.execute(
        "SELECT count(*) c FROM job WHERE type='email.poll'"
    ).fetchone()["c"] == 1


# --------------------------------------------------------------------------- #
# _tick_eudamed_certregister / _tick_eudamed_sweep_due
#
# Denis, 2026-08-20, restated 2026-08-26: "No sweep ever runs unattended -- an
# operator releases every run." certregister EMITS (carved out as the "bulk
# download" the same ruling prefers -- 16 calls against a public register);
# sweep-due only marks a manufacturer due and never enqueues.
# --------------------------------------------------------------------------- #
def test_the_certregister_tick_emits_because_it_is_a_bulk_read(conn):
    """Carved out of the no-unattended-sweep ruling (Denis, 2026-08-26): 16
    calls against a public register is the "prefer a bulk download to an API"
    the same ruling asks for, not the per-manufacturer sweep it forbids."""
    from app.scheduler import _tick_eudamed_certregister

    cfg = Scheduler(eudamed_certregister_enabled=True)

    _tick_eudamed_certregister(conn, cfg, now=NOW)

    n = conn.execute(
        "SELECT count(*) c FROM job WHERE type='eudamed.certregister'"
    ).fetchone()["c"]
    assert n == 1


def test_the_certregister_tick_is_off_by_default(conn):
    """Same footing as expiry_email_enabled / email_poll_enabled: the scan runs
    for real, the emission is gated."""
    from app.scheduler import _tick_eudamed_certregister

    cfg = Scheduler()
    assert cfg.eudamed_certregister_enabled is False

    _tick_eudamed_certregister(conn, cfg, now=NOW)

    assert conn.execute(
        "SELECT count(*) c FROM job WHERE type='eudamed.certregister'"
    ).fetchone()["c"] == 0


def _seed_trusted_manufacturer(conn, canonical_name: str, srn: str) -> None:
    conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES (%s) "
        "ON CONFLICT DO NOTHING", (canonical_name,))
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, "
        " status) VALUES (%s, %s, 'register-exact', 'auto')",
        (canonical_name, srn))


def test_the_sweep_tick_marks_due_and_emits_nothing(conn):
    """THE ruling this plan restores. Denis, 2026-08-20: "No sweep ever runs
    unattended... an operator releases every run." The first design draft
    proposed a self-emitting tick and contradicted it."""
    from app.scheduler import _tick_eudamed_sweep_due

    _seed_trusted_manufacturer(conn, "TICKCO", "XX-MF-6")
    conn.execute(
        "INSERT INTO eudamed_sweep_state (canonical_name, last_swept_at) "
        "VALUES ('TICKCO', now() - interval '200 days')")

    result = _tick_eudamed_sweep_due(conn, Scheduler(), now=NOW)

    assert conn.execute(
        "SELECT count(*) c FROM job WHERE type='eudamed.sweep'"
    ).fetchone()["c"] == 0
    row = conn.execute(
        "SELECT due_at FROM eudamed_sweep_state WHERE canonical_name='TICKCO'"
    ).fetchone()
    assert row["due_at"] is not None
    # Zero eudamed.sweep jobs is also what a tick doing NOTHING would produce
    # -- this proves the tick actually ran its marking half.
    assert result["marked_due"] == 1


def test_a_manufacturer_swept_recently_is_not_marked_due(conn):
    from app.scheduler import _tick_eudamed_sweep_due

    _seed_trusted_manufacturer(conn, "FRESHCO", "XX-MF-5")
    conn.execute(
        "INSERT INTO eudamed_sweep_state (canonical_name, last_swept_at) "
        "VALUES ('FRESHCO', now() - interval '3 days')")

    _tick_eudamed_sweep_due(conn, Scheduler(), now=NOW)

    row = conn.execute(
        "SELECT due_at FROM eudamed_sweep_state WHERE canonical_name='FRESHCO'"
    ).fetchone()
    assert row["due_at"] is None


def test_never_swept_manufacturer_with_no_state_row_is_due(conn):
    """RULING 42 (Denis): a manufacturer that has NEVER been swept, but holds
    a trusted SRN, IS due -- otherwise nothing ever proposes a first sweep and
    a manufacturer would need a `last_swept_at` before it could ever acquire
    one. Deliberately the OPPOSITE of ruling 24 (the sweep DELTA is empty for
    a never-swept manufacturer, because a delta needs a baseline) -- due-ness
    is about when we last looked, and never looking is the strongest possible
    reason to look. This shape: no `eudamed_sweep_state` row at all."""
    from app.scheduler import _tick_eudamed_sweep_due

    _seed_trusted_manufacturer(conn, "NEVERCO", "XX-MF-7")
    assert conn.execute(
        "SELECT count(*) c FROM eudamed_sweep_state WHERE canonical_name='NEVERCO'"
    ).fetchone()["c"] == 0

    result = _tick_eudamed_sweep_due(conn, Scheduler(), now=NOW)

    row = conn.execute(
        "SELECT due_at FROM eudamed_sweep_state WHERE canonical_name='NEVERCO'"
    ).fetchone()
    assert row is not None
    assert row["due_at"] is not None
    assert result["marked_due"] == 1


def test_never_swept_manufacturer_with_null_last_swept_at_is_due(conn):
    """RULING 42, second shape: an `eudamed_sweep_state` row exists but
    `last_swept_at` is NULL. Still due, for the same reason as the no-row
    shape above -- never having been looked at is the strongest reason to
    look, whichever way "never" happens to be represented on disk."""
    from app.scheduler import _tick_eudamed_sweep_due

    _seed_trusted_manufacturer(conn, "NULLCO", "XX-MF-8")
    conn.execute(
        "INSERT INTO eudamed_sweep_state (canonical_name, last_swept_at) "
        "VALUES ('NULLCO', NULL)")

    _tick_eudamed_sweep_due(conn, Scheduler(), now=NOW)

    row = conn.execute(
        "SELECT due_at FROM eudamed_sweep_state WHERE canonical_name='NULLCO'"
    ).fetchone()
    assert row["due_at"] is not None


def test_a_released_sweep_is_never_remarked_due(conn):
    """RULING 43 (Denis): never touch a row whose sweep is already released.
    A released sweep is either queued or running; re-marking it due would put
    it back on an operator's list as if nothing had happened. Seeded well past
    the staleness threshold and with no due_at of its own, so the ONLY thing
    that can be stopping a mark here is the released_at guard -- remove that
    guard and this row flips to due_at IS NOT NULL."""
    from app.scheduler import _tick_eudamed_sweep_due

    _seed_trusted_manufacturer(conn, "RELEASEDCO", "XX-MF-9")
    conn.execute(
        "INSERT INTO eudamed_sweep_state (canonical_name, last_swept_at, "
        " due_at, released_at, released_by) "
        "VALUES ('RELEASEDCO', now() - interval '200 days', NULL, now(), 'ui')")

    result = _tick_eudamed_sweep_due(conn, Scheduler(), now=NOW)

    row = conn.execute(
        "SELECT due_at, released_at FROM eudamed_sweep_state "
        "WHERE canonical_name='RELEASEDCO'"
    ).fetchone()
    assert row["due_at"] is None
    assert row["released_at"] is not None
    assert result["skipped_released"] == 1


def test_a_manufacturer_with_no_trusted_srn_is_never_marked_due(conn):
    """A manufacturer with NO trusted SRN is never marked due, however long
    ago it was swept -- there is nothing to sweep, and the UI's release route
    (web/registry.py `sweep_release`) already refuses it with a 400. Marking
    it due would build a list of rows that cannot be actioned. Seeded with a
    PENDING srn (present, but not trusted) so this fails loudly if the tick
    read `manufacturer_srn` directly instead of the `trusted_manufacturer_srn`
    view.

    No `result` counter distinguishes "excluded because untrusted" from
    "never considered": an untrusted manufacturer is filtered out of the base
    query entirely (`trusted_manufacturer_srn`), so it never becomes a
    candidate row for the tick to count in the first place -- there is
    nothing to attach a counter to without inventing one for a row the tick
    never sees. Proven instead with a control: TRUSTEDCONTROL is seeded
    alongside it, equally overdue, and IS expected to be marked. A tick that
    crashed, was never called, or excluded rows for some unrelated reason
    would leave BOTH untouched (`marked_due == 0`); only the real, correct
    behaviour marks the control and leaves the untrusted row alone."""
    from app.scheduler import _tick_eudamed_sweep_due

    conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES ('UNTRUSTEDCO') "
        "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, "
        " status) VALUES ('UNTRUSTEDCO', 'XX-MF-1', 'register-fuzzy', 'pending')")
    conn.execute(
        "INSERT INTO eudamed_sweep_state (canonical_name, last_swept_at) "
        "VALUES ('UNTRUSTEDCO', now() - interval '400 days')")

    _seed_trusted_manufacturer(conn, "TRUSTEDCONTROL", "XX-MF-2")
    conn.execute(
        "INSERT INTO eudamed_sweep_state (canonical_name, last_swept_at) "
        "VALUES ('TRUSTEDCONTROL', now() - interval '400 days')")

    result = _tick_eudamed_sweep_due(conn, Scheduler(), now=NOW)

    untrusted = conn.execute(
        "SELECT due_at FROM eudamed_sweep_state WHERE canonical_name='UNTRUSTEDCO'"
    ).fetchone()
    control = conn.execute(
        "SELECT due_at FROM eudamed_sweep_state WHERE canonical_name='TRUSTEDCONTROL'"
    ).fetchone()
    assert untrusted["due_at"] is None
    assert control["due_at"] is not None
    assert result["marked_due"] == 1


def test_certregister_tick_flipped_on_mid_window_stays_silent_until_next_period(conn):
    """`record_run` is called unconditionally in `_tick_eudamed_certregister`
    (app/scheduler.py), matching `_tick_email_poll`'s own shape -- so once a
    period's ledger row is written, ENABLING the flag mid-window does not
    retroactively fire it. This is real, documented behaviour with a real
    operational consequence: someone flips `eudamed_certregister_enabled` on
    and nothing happens for up to `eudamed_certregister_interval_days` days,
    until the next period boundary. Mirrors
    `test_email_poll_tick_same_window_second_call_is_noop`."""
    from app.scheduler import _tick_eudamed_certregister

    disabled = _tick_eudamed_certregister(conn, Scheduler(), now=NOW)
    assert disabled["fired"] is False

    enabled_cfg = Scheduler(eudamed_certregister_enabled=True)
    second = _tick_eudamed_certregister(conn, enabled_cfg, now=NOW)

    assert second["fired"] is False
    assert conn.execute(
        "SELECT count(*) c FROM job WHERE type='eudamed.certregister'"
    ).fetchone()["c"] == 0


def test_the_scheduler_module_is_a_library_not_an_entrypoint():
    """The cadence moved onto the queue (migrations 054/055,
    `app/handlers/scheduler_tick.py`). What is left here is the cron BODIES,
    which the tick handler calls; the loop that used to drive them is gone,
    and with it the separate process and its compose service.
    """
    import app.scheduler as sched

    assert not hasattr(sched, "run_forever"), "the cadence lives on the queue now"
    assert not hasattr(sched, "main")
    assert not hasattr(sched, "heartbeat_instance_for_this_process"), (
        "no process of its own means no heartbeat of its own"
    )
    # The tick functions and the ledger stay -- they are what the handler calls.
    assert hasattr(sched, "tick") and hasattr(sched, "already_ran")


# --------------------------------------------------------------------------- #
# _tick_expiry_scan: re-discovery on expiry
# --------------------------------------------------------------------------- #
#
# On expiry the scan used to draft a letter and nothing else -- it never went
# back to the manufacturer's own site to see whether a newer certificate had
# been published. And with `expiry_email_enabled` off, which is how it ships and
# how the live worker is configured, it counted documents and took no action at
# all.
#
# Measured on the live registry 2026-09-02: 86 production documents are expiring
# or already lapsed inside a 30-day horizon (all 86 lapsed; 65 on their own
# stated date, 21 on the five-year staleness review) across 6 manufacturers --
# and they reach **431 groups**, because one document covers many. That is the
# whole reason this is capped: uncapped, one daily tick would queue ~1.300
# fetches unattended.
#
# `DISCOVER_HOLD` does NOT gate this. The hold is read only in `resolve.py`, so
# a cron enqueueing `discover.group` bypasses it -- which is why the flag below
# exists and defaults off. Turning it on is what starts unattended discovery.

def test_expiry_rediscovery_is_off_by_default(conn):
    """The scan still counts, and still queues nothing. `DISCOVER_HOLD` cannot
    protect this path, so the default must."""
    from app.scheduler import _tick_expiry_scan

    _insert_linked_doc(conn, dt.date(2026, 7, 23) + dt.timedelta(days=5),
                       manufacturer="ACME", item_ref="RD-1")
    result = _tick_expiry_scan(conn, Scheduler(), horizon_days=90,
                               cadence_days=7, now=NOW)

    assert result["expiring_count"] == 1
    assert result["rediscover_jobs"] == 0
    assert conn.execute(
        "SELECT count(*) AS c FROM job WHERE type='discover.group'"
    ).fetchone()["c"] == 0


def test_expiry_rediscovery_enabled_queues_the_covering_groups(conn):
    from app.scheduler import _tick_expiry_scan

    _insert_linked_doc(conn, dt.date(2026, 7, 23) + dt.timedelta(days=5),
                       manufacturer="ACME", item_ref="RD-2")
    cfg = Scheduler(expiry_rediscover_enabled=True)

    result = _tick_expiry_scan(conn, cfg, horizon_days=90, cadence_days=7, now=NOW)

    assert result["rediscover_jobs"] == 1
    job = conn.execute(
        "SELECT payload, priority FROM job WHERE type='discover.group'"
    ).fetchone()
    # A lapsed certificate is the one case where the recency window is exactly
    # wrong: it says "we looked recently", and looking again is the point.
    assert job["payload"]["ignore_recency"] is True
    assert job["priority"] == "sweep"


def test_expiry_rediscovery_is_capped_per_tick(conn):
    from app.scheduler import _tick_expiry_scan

    for i in range(4):
        _insert_linked_doc(conn, dt.date(2026, 7, 23) + dt.timedelta(days=5),
                           manufacturer="ACME", item_ref=f"RD-CAP-{i}")
    cfg = Scheduler(expiry_rediscover_enabled=True, expiry_rediscover_cap=2)

    result = _tick_expiry_scan(conn, cfg, horizon_days=90, cadence_days=7, now=NOW)

    assert result["rediscover_jobs"] == 2
    assert result["rediscover_pending"] == 2      # never silent about the rest


def test_expiry_rediscovery_and_email_are_independent(conn):
    """Two separate decisions: chase by letter, look again ourselves. Turning on
    the letter must not start unattended fetching, and vice versa."""
    from app.scheduler import _tick_expiry_scan

    _insert_linked_doc(conn, dt.date(2026, 7, 23) + dt.timedelta(days=5),
                       manufacturer="ACME", item_ref="RD-3")
    cfg = Scheduler(expiry_email_enabled=True)   # letters on, re-look off

    result = _tick_expiry_scan(conn, cfg, horizon_days=90, cadence_days=7, now=NOW)

    assert result["request_jobs"] == 1
    assert result["rediscover_jobs"] == 0


def test_a_document_with_no_group_queues_no_rediscovery(conn):
    """Nothing to discover against -- the email path already reports it as
    unaddressable, and this must not invent a job."""
    from app.scheduler import _tick_expiry_scan

    _insert_production_doc(conn, dt.date(2026, 7, 23) + dt.timedelta(days=5))
    cfg = Scheduler(expiry_rediscover_enabled=True)

    result = _tick_expiry_scan(conn, cfg, horizon_days=90, cadence_days=7, now=NOW)

    assert result["expiring_count"] == 1
    assert result["rediscover_jobs"] == 0


# --------------------------------------------------------------------------- #
# _tick_coverage_scan
# --------------------------------------------------------------------------- #
#
# Nothing ever noticed "this group has no document". Seven crons were registered
# and none enqueued `discover.group`; the only occurrence of the word in this
# module was a failure-reason string. Coverage moved only when a person pressed
# something.
#
# Measured 2026-09-02: 6.732 groups hold no production document and 6.693 have
# never been discovered at all. At the shipped cap of 25 a day that is a nine-
# month backfill, which is why the ordering (never-discovered first, then least
# recently discovered) is load-bearing rather than cosmetic -- a stable ordering
# under a cap re-does the head of the list forever.
#
# `DISCOVER_HOLD` does NOT gate this (Denis's ruling 2026-09-02: keep the flags
# separate). The hold is read only in `resolve.py`. Stopping this cron means
# turning THIS flag off.

def _uncovered_group(conn, mfr="ACME", *, item_ref, discovered_days_ago=None):
    gid = conn.execute(
        "INSERT INTO item_group (canonical_manufacturer) VALUES (%s) RETURNING group_id",
        (mfr,),
    ).fetchone()["group_id"]
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, catalogue, updated_at) "
        "VALUES (%s,'thing','raw','LJ', now()) ON CONFLICT (item_ref) DO NOTHING",
        (item_ref,))
    conn.execute(
        "INSERT INTO item_group_member (group_id, item_ref, match_basis) "
        "VALUES (%s,%s,'udi')", (gid, item_ref))
    if discovered_days_ago is not None:
        conn.execute(
            "INSERT INTO discovery_log (group_id, source, outcome, at) "
            "VALUES (%s,'search','miss', now() - make_interval(days => %s))",
            (gid, discovered_days_ago))
    return gid


def _discover_jobs(conn):
    return conn.execute(
        "SELECT payload, priority FROM job WHERE type='discover.group' ORDER BY id"
    ).fetchall()


def test_coverage_scan_is_off_by_default(conn):
    from app.scheduler import _tick_coverage_scan

    _uncovered_group(conn, item_ref="CV-1")
    result = _tick_coverage_scan(conn, Scheduler(), now=NOW)

    assert result["fired"] is False
    assert _discover_jobs(conn) == []


def test_coverage_scan_enabled_queues_uncovered_groups(conn):
    from app.scheduler import _tick_coverage_scan

    gid = _uncovered_group(conn, item_ref="CV-2")
    cfg = Scheduler(coverage_scan_enabled=True)

    result = _tick_coverage_scan(conn, cfg, now=NOW)

    assert result["fired"] is True
    assert result["queued"] == 1
    jobs = _discover_jobs(conn)
    assert jobs[0]["payload"]["group_id"] == gid
    assert jobs[0]["priority"] == "sweep"


def test_coverage_scan_respects_its_cap_and_reports_the_remainder(conn):
    from app.scheduler import _tick_coverage_scan

    for i in range(5):
        _uncovered_group(conn, item_ref=f"CV-CAP-{i}")
    cfg = Scheduler(coverage_scan_enabled=True, coverage_scan_cap=2)

    result = _tick_coverage_scan(conn, cfg, now=NOW)

    assert result["queued"] == 2
    # Never silent about what it left (CLAUDE.md).
    assert result["uncovered_total"] == 5
    assert result["pending"] == 3


def test_coverage_scan_takes_the_never_discovered_first(conn):
    """The ordering is what makes a nine-month backfill finite."""
    from app.scheduler import _tick_coverage_scan

    recent = _uncovered_group(conn, item_ref="CV-R", discovered_days_ago=1)
    old = _uncovered_group(conn, item_ref="CV-O", discovered_days_ago=200)
    never = _uncovered_group(conn, item_ref="CV-N")
    cfg = Scheduler(coverage_scan_enabled=True, coverage_scan_cap=2)

    _tick_coverage_scan(conn, cfg, now=NOW)

    assert [j["payload"]["group_id"] for j in _discover_jobs(conn)] == [never, old]
    assert recent not in [j["payload"]["group_id"] for j in _discover_jobs(conn)]


def test_a_covered_group_is_never_queued(conn):
    from app.scheduler import _tick_coverage_scan

    gid = _uncovered_group(conn, item_ref="CV-COV")
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, "
        "archive_url, status) VALUES ('DoC','MDR','group','h-cov','/a/c.pdf','production') "
        "RETURNING doc_id").fetchone()["doc_id"]
    conn.execute(
        "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
        "VALUES ('CV-COV',%s,'ref-list','production')", (doc_id,))
    cfg = Scheduler(coverage_scan_enabled=True)

    result = _tick_coverage_scan(conn, cfg, now=NOW)

    assert result["queued"] == 0
    assert _discover_jobs(conn) == []


def test_coverage_scan_runs_once_a_day(conn):
    from app.scheduler import _tick_coverage_scan

    for i in range(4):
        _uncovered_group(conn, item_ref=f"CV-DAY-{i}")
    cfg = Scheduler(coverage_scan_enabled=True, coverage_scan_cap=1)

    first = _tick_coverage_scan(conn, cfg, now=NOW)
    second = _tick_coverage_scan(conn, cfg, now=NOW)

    assert first["queued"] == 1
    assert second["fired"] is False and second["queued"] == 0
    assert len(_discover_jobs(conn)) == 1
