"""The SCHEDULER panel (/scheduler) — producer-only, same boundary as the rest
of web/.

Reads `scheduler_run` (SELECT granted in migration 013) and the newest
`report.weekly` result envelope; the only writes it can make are job rows, and
only for the two crons whose product IS a job. Runs against the real test
Postgres; rows are committed by the owner connection because the TestClient
reads on its own `dentalia_api` connection.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Json

from app import periods
from app.config import Web
from tests.conftest import TEST_API_URL
from web.app import create_app


@pytest.fixture
def client(test_db_url, tmp_path):
    cfg = Web(api_database_url=TEST_API_URL, imports_dir=str(tmp_path))
    return TestClient(create_app(cfg))


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _seed_run(conn, *, name, period_key, ran_at=None, job_id=None):
    conn.execute(
        "INSERT INTO scheduler_run (name, period_key, ran_at, job_id) "
        "VALUES (%s, %s, COALESCE(%s, now()), %s)",
        (name, period_key, ran_at, job_id),
    )


# --------------------------------------------------------------------------- #
# every cron the tick runs is on the panel (2026-08-31)
# --------------------------------------------------------------------------- #
def test_the_panel_lists_every_cron_the_tick_runs():
    """It listed 5 of 7 for four days: both EUDAMED crons shipped 2026-08-27
    and `_cron_specs` was never extended, so they ran with no representation
    anywhere in the UI."""
    from app.config import load_config
    from app.handlers.scheduler_tick import POLL_SECONDS
    from web.scheduler_view import _cron_specs

    keys = {s["key"] for s in _cron_specs(load_config().scheduler)}
    assert keys == set(POLL_SECONDS)


def test_the_panel_says_whether_each_cron_is_armed(client, conn):
    """A cron with no live `scheduler.tick` row is stopped, and the panel is
    where that becomes visible before someone notices the work is missing."""
    from app.workers import runner

    runner.arm_crons(conn)
    conn.commit()

    body = client.get("/scheduler").text
    assert "eudamed.certregister" in body
    assert "eudamed.sweep-due" in body
    assert "armed" in body


def test_a_stopped_cron_is_shown_as_not_armed(client, conn):
    from app.workers import runner

    runner.arm_crons(conn)
    conn.execute(
        "UPDATE job SET status='dead' "
        " WHERE type='scheduler.tick' AND dedupe_key='scheduler.tick:report.weekly'")
    conn.commit()

    body = client.get("/scheduler").text
    assert "not armed" in body


def test_re_arming_a_dead_cron_puts_it_back_on_the_queue(client, conn):
    """`dead` is terminal and outside the active-only dedupe index, so the key
    is free and the same enqueue revives the cron -- without restarting a
    worker, which is the only other way back."""
    from app.workers import runner

    runner.arm_crons(conn)
    conn.execute(
        "UPDATE job SET status='dead' "
        " WHERE type='scheduler.tick' AND dedupe_key='scheduler.tick:report.weekly'")
    conn.commit()

    resp = client.post("/scheduler/arm/report.weekly")
    assert resp.status_code == 200

    row = conn.execute(
        "SELECT count(*) AS n FROM job WHERE type='scheduler.tick' "
        "   AND dedupe_key='scheduler.tick:report.weekly' AND status='pending'"
    ).fetchone()
    assert row["n"] == 1
    assert "not armed" not in client.get("/scheduler").text


def test_re_arming_a_live_cron_creates_nothing(client, conn):
    from app.workers import runner

    runner.arm_crons(conn)
    conn.commit()
    before = conn.execute(
        "SELECT count(*) AS n FROM job WHERE type='scheduler.tick'").fetchone()["n"]

    assert client.post("/scheduler/arm/report.weekly").status_code == 200

    after = conn.execute(
        "SELECT count(*) AS n FROM job WHERE type='scheduler.tick'").fetchone()["n"]
    assert after == before


def test_arming_an_unknown_cron_is_a_404(client):
    assert client.post("/scheduler/arm/not-a-cron").status_code == 404


def _seed_report_job(conn, *, period_key, result, status="done") -> int:
    return conn.execute(
        "INSERT INTO job (type, payload, dedupe_key, priority, status, result) "
        "VALUES ('report.weekly', %s, %s, 'sweep', %s, %s) RETURNING id",
        (Json({"period_key": period_key}), f"report:{period_key}", status, Json(result)),
    ).fetchone()["id"]


def _verdicts(html: str) -> dict[str, str]:
    """`{cron name: verdict}` read off the row's data attributes."""
    import re

    return dict(
        re.findall(r'data-cron="([^"]+)"[^>]*data-verdict="([^"]+)"', html)
    )


# --------------------------------------------------------------------------- #
# The panel
# --------------------------------------------------------------------------- #


def test_panel_lists_every_cron_the_scheduler_ticks(client):
    """Seven ticks run now; a panel that showed six would make a silent cron
    indistinguishable from one that does not exist. It showed five between
    2026-08-27 and 2026-08-31, which is exactly how the two EUDAMED crons ran
    unrepresented for four days."""
    html = client.get("/scheduler").text
    for name in ("ingest.monthly", "expiry-scan", "failure-monitor", "report.weekly",
                 "email.poll", "eudamed.certregister", "eudamed.sweep-due"):
        assert f'data-cron="{name}"' in html


def test_an_empty_ledger_says_never_run_rather_than_healthy(client, conn):
    conn.commit()  # nothing seeded — the live state on a DB the scheduler never reached
    resp = client.get("/scheduler")
    assert resp.status_code == 200

    verdicts = _verdicts(resp.text)
    # Every LEDGERED cron reads `never`. `eudamed.sweep-due` keeps no ledger at
    # all (RULING 45) -- it emits nothing and its due_at write is an idempotent
    # UPSERT, so "is this period recorded" is the wrong question for it and a
    # `never` there would be a lie rather than a warning. The armed column is
    # what reports its liveness.
    assert verdicts.pop("eudamed.sweep-due") == "unledgered"
    # `health-watch` keeps no ledger either, for a different reason: it is a
    # condition check rather than periodic work with an output, and its
    # suppression lives in `alert_state` keyed by the condition. "Is this
    # period recorded" says nothing about whether alerting is healthy.
    assert verdicts.pop("health-watch") == "unledgered"
    # `bc.push-drift` likewise: its day-bucket dedupe key IS the ledger.
    assert verdicts.pop("bc.push-drift") == "unledgered"
    assert set(verdicts.values()) == {"never"}
    assert "No cron has ever run against this database" in resp.text


def test_a_run_recorded_for_the_current_period_reads_ok(client, conn):
    _seed_run(conn, name="expiry-scan", period_key=periods.period_key_day(_now()))
    conn.commit()
    assert _verdicts(client.get("/scheduler").text)["expiry-scan"] == "ok"


def test_a_cron_that_missed_more_than_two_periods_reads_stale(client, conn):
    three_weeks_ago = _now() - dt.timedelta(weeks=3)
    _seed_run(
        conn,
        name="report.weekly",
        period_key=periods.period_key_isoweek(three_weeks_ago),
        ran_at=three_weeks_ago,
    )
    conn.commit()
    assert _verdicts(client.get("/scheduler").text)["report.weekly"] == "stale"


def test_the_previous_period_alone_reads_due_not_stale(client, conn):
    """A daily scan that ran yesterday and not yet today is a scheduler doing
    its job at 00:05, not a dead one. Calling that stale would cry wolf every
    single morning."""
    # Exactly one day, not "20 hours". Twenty hours before 20:25 UTC is 00:25
    # the SAME day, so this test asserted its own premise away for four hours
    # out of every twenty-four and failed at 2026-08-24T20:25Z on a codebase
    # nobody had changed. A full day back is the previous period key at any
    # hour, and 24h of age still clears the 48h (two periods) stale threshold.
    yesterday = _now() - dt.timedelta(days=1)
    _seed_run(
        conn,
        name="expiry-scan",
        period_key=periods.period_key_day(yesterday),
        ran_at=yesterday,
    )
    conn.commit()
    assert _verdicts(client.get("/scheduler").text)["expiry-scan"] == "due"


def test_a_recorded_run_links_the_job_it_enqueued(client, conn):
    period_key = periods.period_key_isoweek(_now())
    job_id = _seed_report_job(conn, period_key=period_key, result={"period_key": period_key})
    _seed_run(conn, name="report.weekly", period_key=period_key, job_id=job_id)
    conn.commit()
    html = client.get("/scheduler").text
    assert f'href="/jobs/{job_id}"' in html


def test_the_latest_weekly_report_is_rendered_from_its_result_envelope(client, conn):
    period_key = periods.period_key_isoweek(_now())
    _seed_report_job(
        conn,
        period_key=period_key,
        result={
            "period_key": period_key,
            "expiring": [
                {"doc_id": 7, "type": "EC-cert", "regulation": "MDR",
                 "validity_to": "2026-09-01", "basis": "stated",
                 "inherited": False, "lapsed": False},
            ],
            "lapsed": [
                {"doc_id": 9, "type": "DoC", "regulation": "MDD",
                 "validity_to": "2020-07-03", "basis": "staleness",
                 "inherited": False, "lapsed": True},
            ],
            "job_counts": [{"type": "fetch.url", "status": "done", "n": 12}],
            "dead_jobs": 3,
        },
    )
    conn.commit()
    html = client.get("/scheduler").text
    assert 'data-report-expiring="1"' in html
    assert 'data-report-lapsed="1"' in html
    assert 'data-report-dead-jobs="3"' in html
    assert "2020-07-03" in html


def test_the_scans_with_no_job_type_are_shown_as_unrunnable_from_here(client):
    """`expiry-scan` and `failure-monitor` execute inside the scheduler
    process — there is no job to enqueue, and the enum is closed (invariant 7).
    A button that pretended otherwise would be a lie with a 404 behind it."""
    html = client.get("/scheduler").text
    assert 'data-runnable="expiry-scan"' not in html
    assert 'data-runnable="failure-monitor"' not in html
    assert 'data-runnable="report.weekly"' in html
    assert 'data-runnable="email.poll"' in html


def test_the_panel_is_reachable_from_the_nav(client):
    """Reachable, not necessarily top-level. /scheduler moved behind the
    /pipeline hub on 2026-09-04 (`[sidebar-is-26-entries]`, 26 entries -> 21),
    so the nav reaches it in two hops instead of one. BOTH are asserted: a
    folded page and a hidden one differ by exactly the second link existing,
    and this test is the one that would notice the difference."""
    assert 'href="/pipeline"' in client.get("/").text
    assert 'href="/scheduler"' in client.get("/pipeline").text


# --------------------------------------------------------------------------- #
# Run now
# --------------------------------------------------------------------------- #


def test_running_the_weekly_report_uses_the_schedulers_own_dedupe_key(client, conn):
    """Byte-identical to `_tick_weekly_report`'s key, so a manual run and the
    cron collapse into one job instead of racing to produce two reports for the
    same week."""
    expected = f"report:{periods.period_key_isoweek(_now())}"
    resp = client.post("/scheduler/run/report.weekly")
    assert resp.status_code == 200
    row = conn.execute(
        "SELECT type, payload, dedupe_key, priority FROM job WHERE dedupe_key=%s", (expected,)
    ).fetchone()
    assert row is not None
    assert row["type"] == "report.weekly"
    assert row["payload"]["period_key"] == periods.period_key_isoweek(_now())
    assert row["priority"] == "sweep"


def test_running_the_mailbox_poll_uses_the_schedulers_own_dedupe_key(client, conn):
    cfg_hours = 6
    expected = f"email.poll:{periods.period_key_interval(_now(), cfg_hours)}"
    resp = client.post("/scheduler/run/email.poll")
    assert resp.status_code == 200
    row = conn.execute(
        "SELECT type, payload FROM job WHERE dedupe_key=%s", (expected,)
    ).fetchone()
    assert row is not None
    assert row["type"] == "email.poll"
    assert row["payload"]["mailbox"] == "INBOX"


def test_a_second_run_in_the_same_period_dedupes_instead_of_queueing_twice(client, conn):
    client.post("/scheduler/run/report.weekly")
    second = client.post("/scheduler/run/report.weekly")
    assert "Already in progress." in second.text
    n = conn.execute(
        "SELECT count(*) AS n FROM job WHERE type='report.weekly'"
    ).fetchone()["n"]
    assert n == 1


def test_a_cron_with_no_job_type_cannot_be_run_from_the_panel(client, conn):
    for name in ("expiry-scan", "failure-monitor", "ingest.monthly"):
        resp = client.post(f"/scheduler/run/{name}")
        assert resp.status_code == 404, name
    assert conn.execute("SELECT count(*) AS n FROM job").fetchone()["n"] == 0


def test_an_unknown_cron_name_is_refused(client, conn):
    assert client.post("/scheduler/run/gate.apply").status_code == 404
    assert conn.execute("SELECT count(*) AS n FROM job").fetchone()["n"] == 0


def test_running_a_cron_never_writes_the_ledger(client, conn):
    """`scheduler_run` is the scheduler's own restart-safety record. The UI
    enqueues the same job the tick would; it must not claim the period ran,
    or the scheduler would skip the period it was asked to help with."""
    client.post("/scheduler/run/report.weekly")
    assert conn.execute("SELECT count(*) AS n FROM scheduler_run").fetchone()["n"] == 0


def test_a_ledger_row_whose_job_was_pruned_says_so(client, conn):
    """`scheduler_run.job_id` is a SOFT reference (migration 013, C8 posture):
    queue rows may be pruned on any schedule without breaking the ledger. The
    panel must survive that, and say the job is gone rather than render an
    empty badge that reads as a status."""
    period_key = periods.period_key_isoweek(_now())
    job_id = _seed_report_job(conn, period_key=period_key, result={"period_key": period_key})
    _seed_run(conn, name="report.weekly", period_key=period_key, job_id=job_id)
    conn.execute("DELETE FROM job WHERE id=%s", (job_id,))
    conn.commit()
    html = client.get("/scheduler").text
    assert "badge-None" not in html
    assert f"#{job_id} (pruned)" in html


# --------------------------------------------------------------------------- #
# The EUDAMED certificate register had no Run now button.
#
# Its row carried `job_type: None`, which routes the template to "runs
# in-process — no job to enqueue". That is true of the expiry scan and the
# sweep-due tick; it is NOT true here. `eudamed.certregister` is in the closed
# job_type enum, has a handler, and is the ONLY way the drift and
# certificate-gap findings ever refresh -- and the cron that would fire it is
# off by default (`eudamed_certregister_enabled`) with the scheduler process
# not running at all. So the findings on /expiry silently aged: last pulled
# 2026-08-27, holding a real Ivoclar Rev. 01 -> Rev. 02 drift and 42
# certificates EUDAMED lists that we hold no copy of.
#
# The flag gates the CRON, not a person -- the same rule `email.poll`'s row
# already states in its own note.
# --------------------------------------------------------------------------- #

def test_the_certificate_register_can_be_run_by_hand(client, conn):
    expected = (f"eudamed.certregister:"
                f"{periods.period_key_days(_now(), 30)}")

    resp = client.post("/scheduler/run/eudamed.certregister")

    assert resp.status_code == 200
    row = conn.execute(
        "SELECT type::text AS t, payload, priority FROM job WHERE dedupe_key=%s",
        (expected,),
    ).fetchone()
    assert row is not None
    assert row["t"] == "eudamed.certregister"
    # The tick sends no manufacturer: the whole register is pulled and matched
    # locally.
    assert row["payload"] == {}
    assert row["priority"] == "sweep"


def test_running_the_register_by_hand_dedupes_with_the_cron(client, conn):
    """Byte-identical to `_tick_eudamed_certregister`'s key, so a hand-run and
    the cron collapse into one job rather than pulling the register twice."""
    client.post("/scheduler/run/eudamed.certregister")
    second = client.post("/scheduler/run/eudamed.certregister")
    assert "Already in progress." in second.text


def test_the_register_row_offers_a_button_rather_than_the_in_process_excuse(client):
    page = client.get("/scheduler").text
    assert "/scheduler/run/eudamed.certregister" in page


# --------------------------------------------------------------------------- #
# Every Run now / Re-arm button on /scheduler was inert.
#
# The target was built as `#run-result-{{ row.key }}` and every key contains a
# dot -- `report.weekly`, `email.poll`, `eudamed.certregister`. As a CSS
# selector `#run-result-report.weekly` reads "id run-result-report, class
# weekly", which matches nothing, so htmx raised `htmx:targetError` and ABORTED
# WITHOUT ISSUING THE REQUEST. Confirmed in a real browser 2026-09-02: clicking
# Run now on the weekly report enqueued no job at all, while the client guide
# documented the button as working.
#
# The route itself was always fine -- a direct POST enqueues correctly -- which
# is why the server-side tests above never caught it. This asserts the rendered
# selector, the only place the defect lived.
# --------------------------------------------------------------------------- #

def test_no_run_button_targets_a_selector_containing_a_dot(client):
    import re as _re

    page = client.get("/scheduler").text
    bad = [t for t in _re.findall(r'hx-target="(#[^"]+)"', page) if "." in t]
    assert bad == [], f"unusable CSS id selectors: {bad}"


def test_the_result_div_id_matches_the_target_selector(client):
    import re as _re

    page = client.get("/scheduler").text
    targets = {t.lstrip("#") for t in _re.findall(r'hx-target="(#[^"]+)"', page)}
    ids = set(_re.findall(r'id="(run-result-[^"]+)"', page))
    assert targets, "no run/arm forms rendered at all"
    assert targets <= ids, f"targets with no matching element: {targets - ids}"
