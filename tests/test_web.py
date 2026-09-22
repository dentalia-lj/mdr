"""Standalone producer UI: Today + the status board, ingest form, staging
(incl. grouping suggestions), manual queue, dead jobs, JSON read API.

Every case here exercises the same boundary: the web app is a job PRODUCER
ONLY (Invariant 1). It must (a) insert correctly-shaped `job` rows through the
same `app.queue.enqueue` path the CLI uses, honoring active-scope dedupe, and
(b) be structurally unable to write `document`/`item_document`/`evidence` —
proven here by connecting as the real `dentalia_api` role, not by trusting the
app code's intentions.

Runs against the real test Postgres (tests/conftest.py — no mocking). The
`dentalia_api` role/grants (migration 007) and its dev password (008) must
already be applied to the test database, which `test_db_url` guarantees by
running every migration before yielding.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import date

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Json

from app import playbooks as playbooks_mod
from app.config import Web
from tests.conftest import TEST_API_URL as API_TEST_URL
from web import registry
from web.app import DOCUMENTS_PAGE_SIZE, create_app


@pytest.fixture
def client(test_db_url, tmp_path):
    """A TestClient wired to the `dentalia_api` role on the real test DB."""
    cfg = Web(api_database_url=API_TEST_URL, imports_dir=str(tmp_path))
    return TestClient(create_app(cfg))


def _rendered(text: str) -> str:
    """Jinja-autoescaped form of `text`, for asserting UI copy against HTML.

    Prose written for humans contains apostrophes, which Jinja escapes to
    `&#39;` — a plain `in resp.text` on such a string fails for a reason that
    has nothing to do with the behaviour under test.
    """
    from markupsafe import escape

    return str(escape(text))


def _seed_gap_manufacturer(conn, *, name="CARL MARTIN", srn="DE-MF-000005066",
                           basic_udi_di="++ECMSBUDI0166Z",
                           item_refs=("A1", "A2")):
    """A manufacturer with a real EUDAMED declaration gap: a trusted SRN, our
    medical-device articles, EUDAMED rows for them, and no document anywhere.

    Seeds the base tables `eudamed_declaration_gap` is built on rather than the
    view, so the button test exercises the same join the handler will."""
    gid = conn.execute(
        "INSERT INTO item_group (canonical_manufacturer, label) "
        "VALUES (%s, 'fam') RETURNING group_id", (name,),
    ).fetchone()["group_id"]
    conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES (%s) "
        "ON CONFLICT (canonical_name) DO NOTHING", (name,))
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, "
        "  status) VALUES (%s,%s,'article-probe','confirmed') "
        "ON CONFLICT DO NOTHING", (name, srn))
    for i, ref in enumerate(item_refs):
        conn.execute(
            "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, "
            "  md_flag, catalogue, updated_at) "
            "VALUES (%s,%s,%s,TRUE,'LJ',now()) "
            "ON CONFLICT (item_ref) DO NOTHING", (ref, f"item {ref}", name))
        conn.execute(
            "INSERT INTO item_group_member (group_id, item_ref, match_basis) "
            "VALUES (%s,%s,'manual')", (gid, ref))
        conn.execute(
            "INSERT INTO eudamed_mirror (udi_di, basic_udi_di, "
            "  manufacturer_srn, reference, synced_at) "
            "VALUES (%s,%s,%s,%s, now())",
            (f"UDI{i}{ref}", basic_udi_di, srn, ref))
    conn.commit()
    return gid


def _jobs(conn, dedupe_key: str) -> list[dict]:
    return conn.execute(
        "SELECT * FROM job WHERE dedupe_key=%s ORDER BY id", (dedupe_key,)
    ).fetchall()


# --------------------------------------------------------------------------- #
# status board
# --------------------------------------------------------------------------- #
def test_healthz_is_db_independent(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_status_board_reflects_seeded_job(client, conn):
    from app import queue

    queue.enqueue(conn, "backfill.scan", {}, "status-board-probe")
    conn.commit()

    resp = client.get("/status")
    assert resp.status_code == 200
    assert "status-board-probe" in resp.text
    assert "backfill.scan" in resp.text


def test_status_board_shows_when_a_job_ran_and_how_long_it_took(client, conn):
    """The recent-jobs table had no time column at all, so "did last night's
    sweep finish, and how long did it take" was unanswerable from the UI.
    `finished_at` (migration 026) is stamped on the terminal transitions only,
    and duration is derived from it."""
    from app import queue

    jid = queue.enqueue(conn, "backfill.scan", {}, "timing-probe")
    conn.execute(
        "UPDATE job SET status='running', claimed_by='w1', claimed_at=now() - interval '95 seconds' "
        "WHERE id=%s", (jid,)
    )
    queue.finish(conn, jid, {"ok": True})
    conn.commit()

    row = conn.execute("SELECT finished_at FROM job WHERE id=%s", (jid,)).fetchone()
    assert row["finished_at"] is not None

    text = client.get("/status?tab=jobs").text
    assert "<th>Finished</th>" in text and "<th>Took</th>" in text
    # 95 seconds reads as "1m 35s", not as 95 or as a raw interval
    assert "1m 35s" in text


def test_a_job_still_running_reports_no_finish_time_rather_than_a_made_up_one(client, conn):
    from app import queue

    jid = queue.enqueue(conn, "backfill.scan", {}, "unfinished-probe")
    conn.commit()
    row = conn.execute("SELECT finished_at FROM job WHERE id=%s", (jid,)).fetchone()
    assert row["finished_at"] is None
    # and a retry is not a finish: `fail` below its max attempts leaves it null
    queue.fail(conn, jid, "transient")
    conn.commit()
    row = conn.execute("SELECT status, finished_at FROM job WHERE id=%s", (jid,)).fetchone()
    assert row["status"] == "failed" and row["finished_at"] is None


def test_status_board_filter_by_type_and_status(client, conn):
    from app import queue

    queue.enqueue(conn, "backfill.scan", {}, "filter-probe-backfill")
    queue.enqueue(conn, "eudamed.sync", {}, "filter-probe-eudamed")
    conn.commit()

    resp = client.get("/status", params={"type": "backfill.scan"})
    assert "filter-probe-backfill" in resp.text
    assert "filter-probe-eudamed" not in resp.text

    resp = client.get("/status", params={"status": "pending"})
    assert "filter-probe-backfill" in resp.text
    assert "filter-probe-eudamed" in resp.text

    resp = client.get("/status", params={"status": "done"})
    assert "filter-probe-backfill" not in resp.text


def test_status_board_search_by_dedupe_key_or_id(client, conn):
    from app import queue

    jid = queue.enqueue(conn, "backfill.scan", {}, "search-probe-unique-xyz")
    conn.commit()

    resp = client.get("/status", params={"q": "unique-xyz"})
    assert "search-probe-unique-xyz" in resp.text

    resp = client.get("/status", params={"q": str(jid)})
    assert "search-probe-unique-xyz" in resp.text

    resp = client.get("/status", params={"q": "no-such-key"})
    assert "No jobs match this filter" in resp.text


def test_status_board_search_treats_wildcards_literally(client, conn):
    # `%` and `_` in q are LIKE metacharacters - a user typing them expects a
    # literal substring match, not wildcard behavior.
    from app import queue

    queue.enqueue(conn, "backfill.scan", {}, "wild-probe-a_x")
    queue.enqueue(conn, "backfill.scan", {}, "wild-probe-aZx")
    conn.commit()

    resp = client.get("/status", params={"q": "a_x"})
    assert "wild-probe-a_x" in resp.text
    assert "wild-probe-aZx" not in resp.text

    resp = client.get("/status", params={"q": "%"})
    assert "No jobs match this filter" in resp.text


def test_status_board_ignores_out_of_enum_filter_values(client, conn):
    # A hand-edited URL with a bogus type/status must not 500 or leak an
    # unfiltered result silently mislabeled as filtered - it should just
    # behave as if the filter weren't set.
    resp = client.get("/status", params={"type": "not.a.real.type", "status": "not-a-status"})
    assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# KPI board v1 (S1.6, GAP G14) — table-driven per docs/specs/kpi.md §5
# --------------------------------------------------------------------------- #
def test_kpi_board_renders_on_empty_database(client):
    # Phase 1 start state — the first thing a fresh `docker compose up` sees.
    # No division-by-zero, no exception.
    resp = client.get("/status")
    assert resp.status_code == 200
    assert "KPI board" in resp.text


def test_kpi_coverage_strict_vs_processed_scope(client, conn):
    from tests.fixtures import seed_ui

    # 2 covered (one by an MDR DoC, one by a QMS certificate
    # only) + 3 uncovered, of which 1 has md_flag NULL.
    seed_ui.seed_kpi_demo(conn)
    conn.commit()

    from web.app import _kpi_coverage
    # One row for the whole mirror since 2026-08-26: K1 was "per catalogue"
    # and there is one catalogue. The per-test reset clears item_mirror, so the
    # seed is the only data these numbers can come from.
    kpi = _kpi_coverage(conn)
    assert kpi["strict_covered"] == 2 and kpi["strict_total"] == 4      # md_flag=TRUE only
    assert kpi["processed_covered"] == 2 and kpi["processed_total"] == 5  # + the NULL item
    assert kpi["strict_pct"] == 50.0
    assert kpi["processed_pct"] == 40.0

    resp = client.get("/status")
    assert resp.status_code == 200
    assert "Coverage" in resp.text


def test_kpi_coverage_counts_device_evidence_separately_from_any_paper(client, conn):
    """K1 must answer two different questions, because they diverge.

    "Do we hold any production document for this item" and "is this item's
    DEVICE conformity evidenced" are not the same question, and an ISO 13485
    certificate is exactly where they part: it is real, current, production
    paper that says nothing about any article. CARL MARTIN landed on
    2026-08-21 reading 2.567/2.567 = 100% on one QMS certificate, while both
    of its MDR declarations sat in staging contributing nothing."""
    from tests.fixtures import seed_ui

    seed_ui.seed_kpi_demo(conn)
    conn.commit()

    from web.app import _kpi_coverage
    kpi = _kpi_coverage(conn)
    # both items hold production paper ...
    assert kpi["strict_covered"] == 2
    # ... but only the one with an MDR declaration is device-evidenced.
    assert kpi["device_covered"] == 1 and kpi["device_total"] == 4
    assert kpi["device_pct"] == 25.0

    resp = client.get("/status")
    assert "MDR/MDD" in resp.text


def test_kpi_coverage_reports_declarations_separately_from_certificates(client, conn):
    """K1's fourth line, and the only one that answers the client's question.

    `device` filters on `regulation IN ('MDR','MDD')`, which an MDR Annex IX
    QUALITY-SYSTEM certificate satisfies -- so it counts an item as
    device-evidenced on paper that says nothing about that article's
    conformity. A Declaration of Conformity is the manufacturer's statement
    about the DEVICE, and it is the only production document that answers "is
    this article's conformity declared".

    Measured on the dev registry 2026-09-03: 4.254 of 4.265 confirmed MD items
    hold some production paper (99,7%), 1.684 hold paper under a device
    regulation (39,5%), and 489 hold an unexpired Declaration of Conformity
    (11,5%). Reporting 99,7% to a client is indefensible against that."""
    from tests.fixtures import seed_ui

    seed_ui.seed_kpi_demo(conn)
    conn.commit()

    from web.app import _kpi_coverage
    kpi = _kpi_coverage(conn)
    # The same denominator as strict/device: the three differ only in what
    # counts as covered, so they read as one number and its honest subsets.
    assert kpi["doc_total"] == kpi["strict_total"]
    # A DoC is a strict subset of device-regulation paper.
    assert kpi["doc_covered"] <= kpi["device_covered"]
    assert kpi["doc_pct"] is not None


def test_kpi_coverage_doc_line_ignores_a_quality_system_certificate(conn):
    """The ISO certificate that makes `device` overstate must not move `doc`."""
    from tests.fixtures import seed_ui

    seed_ui.seed_kpi_demo(conn)
    conn.commit()
    from web.app import _kpi_coverage

    before = _kpi_coverage(conn)["doc_covered"]
    # An ISO 13485 certificate issued under MDR: real, current, production, and
    # silent about any article's conformity.
    conn.execute("UPDATE document SET type='ISO' WHERE type='DoC'")
    conn.commit()
    after = _kpi_coverage(conn)["doc_covered"]
    assert after < before or before == 0


def test_kpi_coverage_does_not_count_lapsed_evidence(client, conn):
    """Denis ruling 2026-08-24 ([expiry-is-reported-never-enforced]): lapse
    changes the coverage KPI and nothing else -- documents keep their status,
    links stay, the chase continues, but an item whose only production
    evidence is past its effective expiry is not "covered". Measured before
    the ruling: 85 lapsed production documents held 1.451 production links and
    the KPI counted every one; 40 items had no unexpired evidence at all, so
    the headline overstated by exactly 40."""
    from tests.fixtures import seed_ui

    seed_ui.seed_kpi_demo(conn)
    # Covered ONLY by a lapsed declaration: real production doc, real
    # production link, expired 2020. Must not count as covered on any line.
    seed_ui.seed_item(conn, "kpi-item-lapsed-only", name="KPI Lapsed Item",
                      manufacturer_raw="ACME")
    lapsed = seed_ui.seed_document(
        conn, content_hash="kpi-hash-lapsed", archive_url="local://kpi/lapsed.pdf",
        doc_type="DoC", regulation="MDR", coverage_scope="group",
        status="production", validity_to="2020-01-01",
    )
    seed_ui.seed_link(conn, "kpi-item-lapsed-only", lapsed,
                      match_basis="ref-list", status="production")
    # A lapsed document BESIDE a current one must not uncover the item: most
    # of the measured 1.451 lapsed links sat on items that also held current
    # paper, and only the 40 without any were overstated.
    seed_ui.seed_link(conn, "kpi-item-covered", lapsed,
                      match_basis="ref-list", status="production")
    conn.commit()

    from web.app import _kpi_coverage
    kpi = _kpi_coverage(conn)
    # +1 to every total for the new item; covered counts unchanged.
    assert kpi["strict_covered"] == 2 and kpi["strict_total"] == 5
    assert kpi["device_covered"] == 1 and kpi["device_total"] == 5
    assert kpi["processed_covered"] == 2 and kpi["processed_total"] == 6


def test_kpi_missing_mfr_ref_rate(client, conn):
    from tests.fixtures import seed_ui

    seed_ui.seed_kpi_demo(conn)   # 3 of 4 seeded items have no mfr_ref
    conn.commit()

    from web.app import _kpi_missing_mfr_ref
    # One row for the whole mirror: K3 was "per catalogue" until 2026-08-26,
    # and there is one catalogue. The seed is the only ingested data here, so
    # the whole-mirror rate is the rate it seeded.
    kpi = _kpi_missing_mfr_ref(conn)
    assert kpi["missing"] == 4 and kpi["total"] == 5
    assert kpi["rate_pct"] == 80.0

    resp = client.get("/status")
    assert resp.status_code == 200
    assert "Missing mfr_ref" in resp.text


def test_kpi_match_basis_excludes_untrusted_bases(client, conn):
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)   # includes a name-family staged link on a production doc
    seed_ui.seed_kpi_demo(conn)     # includes a ref-list production link
    conn.commit()

    resp = client.get("/status")
    assert resp.status_code == 200
    assert "ref-list" in resp.text
    # name-family never appears at production (item_document_trusted_basis_ck) —
    # can't assert its literal absence robustly (the word appears elsewhere on
    # the page), so assert via the underlying function instead.
    from web.app import _kpi_match_basis
    bases = {r["match_basis"] for r in _kpi_match_basis(conn)}
    assert "name-family" not in bases and "fetch-context" not in bases
    assert "ref-list" in bases


def test_kpi_staging_queue_counts(client, conn):
    from tests.fixtures import seed_ui

    seed_ui.seed_demo(conn)   # 1 staged doc, 1 staged link on production, 1 manual-doc
    conn.execute(
        "INSERT INTO grouping_suggestion (item_ref, candidates, score) VALUES (%s,%s,0.8)",
        ("seed-item-1", '[{"group_id": 1, "score": 0.8, "sample_name": "X"}]'),
    )
    conn.commit()

    from web.app import _kpi_staging_queue
    staging = _kpi_staging_queue(conn)
    assert staging["staged_docs"]["n"] >= 2          # staged_doc + manual_doc
    assert staging["staged_links_on_production"]["n"] == 1
    assert staging["open_suggestions"]["n"] == 1
    assert staging["staged_docs"]["oldest"] is not None
    assert staging["open_suggestions"]["oldest"] is not None


def test_kpi_manual_by_kind_and_dead_jobs(client, conn):
    from tests.fixtures import seed_ui

    seed_ui.seed_demo(conn)
    conn.commit()

    resp = client.get("/status")
    assert resp.status_code == 200
    assert "gate-manual" in resp.text
    assert "discovery-dead-end" in resp.text

    from web.app import _kpi_dead_job_count
    assert _kpi_dead_job_count(conn) == 1


def test_kpi_jobs_by_type_status(client, conn):
    from app import queue

    queue.enqueue(conn, "backfill.scan", {}, "kpi-jobs-probe-1")
    conn.commit()

    from web.app import _kpi_jobs_by_type_status
    rows = _kpi_jobs_by_type_status(conn)
    assert any(r["type"] == "backfill.scan" and r["status"] == "pending" for r in rows)


def test_kpi_spend_converts_and_surfaces_unpriced_calls(client, conn):
    from tests.fixtures import seed_ui
    from app.config import Budget

    seed_ui.seed_kpi_demo(conn)   # one priced + one unpriced extraction_cost row
    conn.commit()

    from web.app import _kpi_spend
    spend = _kpi_spend(conn, Budget(eur_per_usd=0.5))
    # extraction_cost/extraction_spend are global (not catalogue-scoped) and
    # other test files leave their own rows for the whole session (no per-test
    # rollback, see conftest.py) — assert our contribution is present and
    # correctly reflected, never an exact session-wide total.
    assert spend["all_time"]["usd"] >= 0.01234 - 1e-9
    assert spend["all_time"]["eur"] == round(spend["all_time"]["usd"] * 0.5, 2)   # rounds to cents
    assert spend["all_time"]["unpriced_calls"] >= 1
    assert spend["eur_per_usd"] == 0.5

    priced = next(row for row in spend["breakdown"]
                  if row["model_id"] == "claude-haiku-4-5" and row["tier"] == "T1")
    assert priced["calls"] >= 1
    unpriced = next(row for row in spend["breakdown"] if row["model_id"] == "claude-sonnet-4-6")
    assert unpriced["unpriced_calls"] >= 1

    resp = client.get("/status")
    assert "pricing gap" in resp.text

    resp = client.get("/status")
    assert "pricing gap" in resp.text


def test_job_detail_shows_stage_status_and_payload(client, conn):
    from app import queue

    jid = queue.enqueue(conn, "extract.doc", {"doc_id": 42}, "detail-probe")
    conn.commit()

    resp = client.get(f"/jobs/{jid}")
    assert resp.status_code == 200
    assert "EXTRACT" in resp.text  # stage label for extract.doc
    assert "detail-probe" in resp.text
    # Jinja HTML-escapes the payload block (correct - it's arbitrary data),
    # so the quotes come through as &#34; rather than raw ".
    assert "doc_id" in resp.text and "42" in resp.text


def test_job_detail_404_for_unknown_id(client):
    resp = client.get("/jobs/999999999")
    assert resp.status_code == 404


def test_job_detail_shows_the_cascade(client, conn):
    """`job.caused_by` (migration 035) lets a job page name its parent and
    list its direct children, so a discover->fetch->extract->validate->gate
    cascade can be walked one click at a time instead of reconstructed by
    correlating hashes and timestamps."""
    from app import queue

    parent = queue.enqueue(conn, "discover.group", {"group_id": 9}, "lineage-web-p")
    queue.current_job_id = parent
    try:
        child = queue.enqueue(conn, "fetch.url", {"url": "z"}, "lineage-web-c")
    finally:
        queue.current_job_id = None
    conn.commit()

    resp = client.get(f"/jobs/{child}")
    assert resp.status_code == 200
    assert f'href="/jobs/{parent}"' in resp.text  # child names its parent

    resp = client.get(f"/jobs/{parent}")
    assert resp.status_code == 200
    assert f'href="/jobs/{child}"' in resp.text  # parent lists the child
    assert "fetch.url" in resp.text
    assert "pending" in resp.text  # the child's status


def test_job_detail_root_shows_no_parent(client, conn):
    from app import queue

    jid = queue.enqueue(conn, "ingest.run", {}, "lineage-web-root")
    conn.commit()

    resp = client.get(f"/jobs/{jid}")
    assert resp.status_code == 200
    assert "Caused by" in resp.text
    assert "Caused by</th><td>—" in resp.text  # no parent to link


# --------------------------------------------------------------------------- #
# The office menu (GET /_pulse) — the menu itself, with the four counts a
# person acts on, lazily loaded into base.html's sidebar and then polled.
#
# It was a sticky header strip until 2026-09-14 (office UI redesign spec § 7):
# the counts were always about four menu entries, and a strip repeating them
# above every page was a second place to read the same number. The endpoint,
# the cadence and the reason for both are unchanged — deliberately NOT the
# status board's headline, whose `_kpi_coverage` alone measured 294ms of a
# 478ms render.
#
# `_pulse_counts` outlives the strip: `/pipeline` shows three of its five.
# --------------------------------------------------------------------------- #
def test_pulse_counts_documents_in_flight_not_jobs(client, conn):
    """"Processing now" answers "how many documents are moving", so two jobs
    over one PDF count once. Same definition as /inflight, which is what the
    strip's Processing link goes to — the two must never disagree."""
    from app import queue

    queue.enqueue(conn, "extract.doc", {"content_hash": "pulse-hash-a"}, "pulse-a-1")
    queue.enqueue(conn, "validate.doc", {"content_hash": "pulse-hash-a"}, "pulse-a-2")
    queue.enqueue(conn, "extract.doc", {"content_hash": "pulse-hash-b"}, "pulse-b-1")
    # No content_hash at all: a group-level job is not a document in flight.
    queue.enqueue(conn, "resolve.group", {"group_id": 1}, "pulse-nohash-1")
    conn.commit()

    from web.app import _pulse_counts
    counts = _pulse_counts(conn)
    assert counts["in_flight_docs"] == 2


def test_pulse_counts_review_manual_and_dead(client, conn):
    from tests.fixtures import seed_ui

    seed_ui.seed_demo(conn)   # 1 staged doc + 1 manual-doc, manual tasks, 1 dead job
    conn.commit()

    from web.app import _pulse_counts
    counts = _pulse_counts(conn)
    assert counts["to_review"] >= 2
    assert counts["manual_open"] >= 1
    assert counts["dead_jobs"] == 1


def test_pulse_fragment_renders_the_menu_with_its_queues(client, conn):
    from tests.fixtures import seed_ui

    seed_ui.seed_demo(conn)
    conn.commit()

    resp = client.get("/_pulse")
    assert resp.status_code == 200
    # A fragment, not a page — htmx swaps it into base.html's sidebar.
    assert "<html" not in resp.text
    for href in ('href="/"', 'href="/staging"', 'href="/missing"',
                 'href="/expiry"', 'href="/drafts"'):
        assert href in resp.text
    assert "Review" in resp.text


def test_pulse_renders_on_empty_database(client):
    # Same start state as the KPI board test: a fresh `docker compose up`.
    resp = client.get("/_pulse")
    assert resp.status_code == 200
    assert "Missing documents" in resp.text


def test_the_health_line_is_quiet_when_nothing_is_wrong(client, conn):
    """The line is on every page, so it must be quiet when nothing is wrong —
    a permanently-red sidebar is a sidebar people stop reading. A dead job on
    its own is not the alarm: the line names website timeouts, which is what
    an office login can act on (D6)."""
    from tests.fixtures import seed_ui

    clean = client.get("/_pulse")
    assert "nav-health-bad" not in clean.text
    assert "System working" in clean.text

    seed_ui.seed_demo(conn)   # 1 dead job, an httpx.ConnectError
    conn.commit()

    resp = client.get("/_pulse")
    assert "System working" in resp.text


def test_every_page_carries_the_menu(client):
    """base.html, so the menu and its counts are app furniture rather than a
    status-page feature. Asserted on a registry page far from the queues."""
    resp = client.get("/items")
    assert resp.status_code == 200
    assert 'hx-get="/_pulse"' in resp.text
    assert 'href="/staging"' in resp.text


# --------------------------------------------------------------------------- #
# ingest form -> ingest.run
# --------------------------------------------------------------------------- #
def test_ingest_csv_valid_enqueues_contract_payload(client, conn):
    resp = client.post(
        "/ingest",
        data={
            "source": "csv",
            "catalogue": "LJ",
            "priority": "interactive",
            "csv_path_manual": "/imports/2026-07/bc_export_LJ.xlsx",
        },
    )
    assert resp.status_code == 200

    rows = _jobs(conn, "ingest.run:web:LJ:csv:/imports/2026-07/bc_export_LJ.xlsx")
    assert len(rows) == 1
    row = rows[0]
    assert row["type"] == "ingest.run"
    assert row["priority"] == "interactive"
    assert row["payload"] == {
        "source": "csv",
        "ref": "/imports/2026-07/bc_export_LJ.xlsx",
        "catalogue": "LJ",
    }


def test_ingest_csv_resubmit_dedupes_active_job(client, conn):
    payload = {
        "source": "csv",
        "catalogue": "LJ",
        "priority": "interactive",
        "csv_path_manual": "/imports/dup.xlsx",
    }
    first = client.post("/ingest", data=payload)
    second = client.post("/ingest", data=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    # The receipt in words (spec § 9, P7a): a deduped enqueue says so plainly
    # instead of naming the key it collided on.
    assert "Already in progress." in second.text
    assert len(_jobs(conn, "ingest.run:web:LJ:csv:/imports/dup.xlsx")) == 1


def test_ingest_bc_odata_ref_is_nested_object_not_string(client, conn):
    """The subject here is the SHAPE of `ref` for a bc_odata run. The values
    below are fixture; the form stopped taking a catalogue on 2026-08-26 and
    the payload now carries the configured one."""
    resp = client.post(
        "/ingest",
        data={
            "source": "bc_odata",
            "catalogue": "LJ",
            "priority": "delta",
            "company": "Dentalia",
            "delta_since": "2026-06-01T00:00",
        },
    )
    assert resp.status_code == 200

    rows = conn.execute(
        "SELECT payload FROM job WHERE type='ingest.run' AND payload->>'source'='bc_odata'"
    ).fetchall()
    assert len(rows) == 1
    payload = rows[0]["payload"]
    assert payload["catalogue"] == "LJ"
    assert isinstance(payload["ref"], dict)
    assert payload["ref"]["company"] == "Dentalia"
    assert payload["ref"]["delta_since"] == "2026-06-01T00:00:00Z"


@pytest.mark.parametrize(
    "overrides",
    [
        # No {"catalogue": ...} case: /ingest stopped taking the field on
        # 2026-08-26, so a posted value is ignored rather than validated. The
        # protection is structural now, and stronger than a whitelist was.
        {"source": "ftp"},  # not in {csv, bc_odata}
        {"csv_path_manual": ""},  # csv with no path at all
    ],
)
def test_ingest_invalid_rejected_and_no_job_enqueued(client, conn, overrides):
    before = conn.execute("SELECT count(*) AS n FROM job").fetchone()["n"]
    data = {
        "source": "csv",
        "catalogue": "LJ",
        "priority": "interactive",
        "csv_path_manual": "/imports/whatever.xlsx",
        **overrides,
    }
    resp = client.post("/ingest", data=data)
    assert 400 <= resp.status_code < 500
    after = conn.execute("SELECT count(*) AS n FROM job").fetchone()["n"]
    assert after == before


def test_ingest_bc_odata_bad_delta_since_rejected(client, conn):
    before = conn.execute("SELECT count(*) AS n FROM job").fetchone()["n"]
    resp = client.post(
        "/ingest",
        data={
            "source": "bc_odata",
            "catalogue": "LJ",
            "priority": "delta",
            "company": "Dentalia_SI",
            "delta_since": "not-a-date",
        },
    )
    assert 400 <= resp.status_code < 500
    after = conn.execute("SELECT count(*) AS n FROM job").fetchone()["n"]
    assert after == before


def test_enqueue_route_removed(client):
    # G16: the generic /enqueue form was a debug tool the CLI already covers
    # (app/cli.py `enqueue`) — dropped in S1.6 rather than growing it further.
    resp = client.get("/enqueue")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Invariant 1 — structural enforcement, not just app-level intent
# --------------------------------------------------------------------------- #
def test_dentalia_api_role_cannot_write_registry(test_db_url):
    with psycopg.connect(API_TEST_URL) as api_conn:
        with pytest.raises(psycopg.Error, match="permission denied"):
            api_conn.execute(
                "INSERT INTO document "
                "(type, regulation, coverage_scope, content_hash, archive_url) "
                "VALUES ('DoC', 'MDR', 'group', 'test-web-permcheck', 'gdrive://x')"
            )
        api_conn.rollback()


# --------------------------------------------------------------------------- #
# staging / manual / dead — read against tests/fixtures/seed_ui.py rows
# (gate.apply's handler doesn't exist yet, so these prove the UI renders and
# enqueues correctly; they don't prove GATE's eventual disposition logic)
# --------------------------------------------------------------------------- #
def test_staging_board_lists_staged_manual_and_mfr_binding_docs(client, conn):
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)
    conn.commit()

    resp = client.get("/staging")
    assert resp.status_code == 200
    assert f'id="doc-{ids["staged_doc"]}"' in resp.text
    assert f'id="doc-{ids["manual_doc"]}"' in resp.text
    # the certificate number is NOT on the collapsed row: the row is a fixed
    # grid of the five facts a reviewer scans by (document, regulation,
    # manufacturer, coverage, expiry), and the cert number reads in the
    # expanded panel where it is checked against the document itself.
    assert "CERT-STAGED-1" not in resp.text
    assert "CERT-STAGED-1" in client.get(
        f"/staging/{ids['staged_doc']}/detail").text
    # type codes are spelled out — a reviewer should not have to know "DoC".
    # Since the office UI redesign (spec § 6) the row names the document as
    # "⟨manufacturer⟩ · ⟨type word⟩ (⟨regulation⟩)" — `words.doc_display_name`,
    # the same name `/documents/{id}` gives it one click away.
    assert "Ivoclar · Declaration of Conformity (MDR)" in resp.text
    assert "ISO certificate" in resp.text
    # every row says, in words, why it is waiting: the manual doc's flag, and
    # the manufacturer-binding candidate's own explanation. Compared through
    # markupsafe.escape because Jinja autoescapes the apostrophes in this copy
    # (a raw `in resp.text` silently fails on any sentence containing one).
    from web.app import FLAG_EXPLANATIONS, MFR_BINDING_REASON
    assert _rendered(FLAG_EXPLANATIONS["no-item-identifier"]) in resp.text
    assert _rendered(MFR_BINDING_REASON) in resp.text
    # no decision on a collapsed row (spec § 3, D5, 2026-09-11): the row offers
    # Open, and Approve/Reject live in the expanded panel beside the document
    assert 'value="approve"' not in resp.text
    assert 'value="reject"' not in resp.text
    # staged link on the production doc shows up in its own section
    assert "seed-item-3" in resp.text
    # ...but the per-document evidence and correction form do NOT: rows are
    # collapsed and fetch their body on expand. This is the whole point of the
    # split (the one-page board was 7.8 MiB); assert the absence so a
    # regression that re-inlines detail fails here.
    assert "What this is" not in resp.text
    assert "Technical details" not in resp.text


def test_staging_row_reason_is_plain_language_never_a_raw_flag(client, conn):
    """A reviewer must never be shown a bare slug like `cert-unresolved`."""
    from tests.fixtures import seed_ui
    from web.app import NO_TASK_REASON

    ids = seed_ui.seed_demo(conn)
    conn.commit()

    resp = client.get("/staging")
    assert resp.status_code == 200
    # the plain staged doc has no open task at all — it still gets a sentence
    assert NO_TASK_REASON in resp.text
    # the raw flag slug stays out of the row (it lives under Technical details)
    assert "no-item-identifier" not in resp.text
    assert ids["staged_doc"] is not None


def test_unmapped_flag_still_renders_a_sentence_not_silence(client, conn):
    """An unknown flag must degrade to visible text, never to an empty reason
    — a blank explanation reads as "nothing is wrong", the one thing it never
    means."""
    from web.app import _review_reason

    assert _review_reason(["brand-new-flag"], is_mfr_binding=False, has_task=True) == (
        "Flagged as brand-new-flag."
    )


def test_an_unknown_class_binding_does_not_promise_links_it_cannot_make(client, conn):
    """`[mfr-bind-empty-class]`, Denis 2026-08-27. Both cases take the
    mfr-binding route, and the default sentence would be a lie on one of them.

    The standing sentence says approving "links it to every medical-device
    product from that manufacturer". When GATE staged the document because
    every item we hold from them has a BLANK device class, there are no such
    products and approving links none.

    Amended 2026-08-31: this sentence used to end "leave this until the export
    is populated", which Denis reversed -- the reviewer may bind now, and since
    migration 053 the binding is recorded even though it links nothing. It also
    used to claim `gate.apply` refused such a bind outright; it never did, since
    that guard fires on a name resolving to no BC codes and these names resolve
    fine."""
    from web.app import (MFR_BINDING_REASON, MFR_BINDING_UNKNOWN_CLASS_REASON,
                         _review_reason)

    unknown = _review_reason([], is_mfr_binding=True, has_task=True,
                             task_reason="md-class-unknown")
    assert unknown == MFR_BINDING_UNKNOWN_CLASS_REASON
    assert "blank device class" in unknown
    # It must no longer send the reviewer away to wait on Business Central.
    assert "leave this until" not in unknown
    assert "records the manufacturer" in unknown

    # The ordinary binding keeps the sentence it always had, and a task written
    # before this existed carries no `reason` at all -- so the default must
    # survive both None and an unrecognised value.
    assert _review_reason([], is_mfr_binding=True, has_task=True) == MFR_BINDING_REASON
    assert _review_reason([], is_mfr_binding=True, has_task=True,
                          task_reason=None) == MFR_BINDING_REASON
    assert _review_reason([], is_mfr_binding=True, has_task=True,
                          task_reason="something-later") == MFR_BINDING_REASON


def test_staging_doc_detail_carries_evidence_links_and_decision_form(client, conn):
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)
    conn.commit()

    resp = client.get(f"/staging/{ids['manual_doc']}/detail")
    assert resp.status_code == 200
    assert "seed-item-2" in resp.text                 # item link
    assert "name-family" in resp.text                 # link match basis (technical)
    assert "no-item-identifier" in resp.text          # raw flag (technical)
    assert 'value="approve"' in resp.text             # one-click decision
    assert 'value="reject"' in resp.text
    # the product NAME, not just its code — "seed-item-2" means nothing alone
    assert "Seed Composite B" in resp.text
    # a working link to the file, constructed rather than the stored handle
    assert f'/documents/{ids["manual_doc"]}/file' in resp.text
    # the reject payload carries the retry group from manual_task.group_id —
    # the pre-pagination query never selected that column, so it was always blank
    assert 'value="4407"' in resp.text
    # the raw-JSON edits textarea is gone; corrections are ordinary inputs
    assert 'name="edits"' not in resp.text
    assert 'name="edit_validity_to"' in resp.text
    assert 'name="edit_baseline"' in resp.text


def test_apply_without_decided_by_falls_back_to_the_default_identity(client, conn):
    """The simplified form posts no `decided_by`. With no proxy in front, the
    audit identity must still be a real, recorded value — not blank, and not a
    422 the reviewer cannot act on."""
    from tests.fixtures import seed_ui
    from web.app import DEFAULT_DECIDED_BY

    ids = seed_ui.seed_demo(conn)
    conn.commit()

    resp = client.post(f"/staging/{ids['staged_doc']}/apply", data={"decision": "approve"})
    assert resp.status_code == 200
    rows = _jobs(conn, f"apply:{ids['staged_doc']}:approve")
    assert len(rows) == 1
    assert rows[0]["payload"]["decided_by"] == DEFAULT_DECIDED_BY == "user:admin"


def test_apply_structured_edits_send_only_changed_fields(client, conn):
    """The correction form renders every field pre-filled, so an untouched
    form posts them all back. Only genuine changes may reach `edits` — each one
    writes T3 human evidence that permanently outranks the extraction
    (gate.py `_apply_edits`), so echoing unchanged values would silently
    overwrite machine provenance with a human's fingerprint."""
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)
    conn.commit()
    doc_id = ids["staged_doc"]
    baseline = json.dumps({
        "type": "DoC", "regulation": "MDR",
        "validity_from": "2024-01-01", "validity_to": "2028-01-01",
        "cert_number": "CERT-STAGED-1",
    })

    # untouched form: every field echoed back unchanged -> no edits at all
    client.post(f"/staging/{doc_id}/apply", data={
        "decision": "approve", "edit_baseline": baseline,
        "edit_type": "DoC", "edit_regulation": "MDR",
        "edit_validity_from": "2024-01-01", "edit_validity_to": "2028-01-01",
        "edit_cert_number": "CERT-STAGED-1",
    })
    rows = _jobs(conn, f"apply:{doc_id}:approve")
    assert len(rows) == 1
    assert "edits" not in rows[0]["payload"]

    # one changed field -> exactly that field
    conn.execute("DELETE FROM job WHERE dedupe_key=%s", (f"apply:{doc_id}:approve",))
    conn.commit()
    client.post(f"/staging/{doc_id}/apply", data={
        "decision": "approve", "edit_baseline": baseline,
        "edit_type": "DoC", "edit_regulation": "MDR",
        "edit_validity_from": "2024-01-01", "edit_validity_to": "2029-03-14",
        "edit_cert_number": "CERT-STAGED-1",
    })
    rows = _jobs(conn, f"apply:{doc_id}:approve")
    assert rows[0]["payload"]["edits"] == {"validity_to": "2029-03-14"}


def test_structured_edits_ignore_a_missing_or_broken_baseline(client, conn):
    """No trustworthy baseline means no comparison is possible, so nothing is
    treated as changed — a broken form must never rewrite the registry."""
    from web.app import _structured_edits

    submitted = {"cert_number": "NEW-1", "validity_to": "2030-01-01"}
    assert _structured_edits(submitted, "") == {}
    assert _structured_edits(submitted, "{not json") == {}
    assert _structured_edits(submitted, "[1,2,3]") == {}
    # a field absent from the baseline was never rendered — ignore it too
    assert _structured_edits(submitted, json.dumps({"cert_number": "OLD-1"})) == {
        "cert_number": "NEW-1"
    }


def test_staging_doc_detail_offers_bind_manufacturer_only_on_mfr_candidate(client, conn):
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)
    conn.commit()

    mfr = client.get(f"/staging/{ids['mfr_doc']}/detail")
    assert mfr.status_code == 200
    assert "Ivoclar" in mfr.text
    assert 'value="bind-manufacturer"' in mfr.text

    plain = client.get(f"/staging/{ids['staged_doc']}/detail")
    assert plain.status_code == 200
    assert 'value="bind-manufacturer"' not in plain.text


@pytest.fixture
def picker_rows(conn):
    """Undo what the manufacturer-picker tests seed.

    `item_mirror`, `manufacturer_alias` and `document` are deliberately NOT
    truncated between tests here (see `_seed_item_with_doc`), and several
    suites in this file assert counts over them — so a test that leaves rows
    behind does not fail itself, it fails somebody else fifty tests later. The
    first version of these tests did exactly that: 26 unrelated failures, all
    of them counting rows these cases had added.

    Everything below is namespaced (`pick-` / `PICK` / `h-pick-`) so the
    teardown can be exact rather than a blanket TRUNCATE that would take other
    tests' fixtures with it.
    """
    yield
    conn.execute(
        "DELETE FROM evidence WHERE doc_id IN "
        "(SELECT doc_id FROM document WHERE content_hash LIKE %s)", ("h-pick-%",)
    )
    conn.execute(
        "DELETE FROM manual_task WHERE doc_id IN "
        "(SELECT doc_id FROM document WHERE content_hash LIKE %s)", ("h-pick-%",)
    )
    conn.execute(
        "DELETE FROM item_document WHERE doc_id IN "
        "(SELECT doc_id FROM document WHERE content_hash LIKE %s)", ("h-pick-%",)
    )
    conn.execute("DELETE FROM document WHERE content_hash LIKE %s", ("h-pick-%",))
    conn.execute("DELETE FROM item_mirror WHERE item_ref LIKE %s", ("pick-%",))
    conn.execute("DELETE FROM manufacturer_alias WHERE canonical_name LIKE %s", ("PICK%",))
    conn.commit()


def _seed_binding_candidate_without_manufacturer(
    conn, *, content_hash: str, evidence_manufacturer: str | None = None
) -> int:
    """A manufacturer-scope candidate whose task payload names no manufacturer.

    This is the real shape of every open binding candidate on the live board
    ([mfr-binding-null-manufacturer]) and the one `seed_demo` does not cover —
    its `mfr_doc` carries `{"manufacturer": "Ivoclar"}`, so it exercises the
    branch where the name is already known and never the branch where a human
    has to supply one.
    """
    from tests.fixtures import seed_ui

    fields = None
    if evidence_manufacturer:
        fields = [
            {"field": "manufacturer", "value": evidence_manufacturer,
             "verbatim": f"Legal manufacturer {evidence_manufacturer}",
             "tier": "T1", "model_id": "claude-haiku-4-5", "confidence": 0.9, "page": 2},
        ]
    doc_id = seed_ui.seed_document(
        conn, content_hash=content_hash, archive_url=f"/archive/{content_hash}.pdf",
        doc_type="ISO", regulation="n.a.", coverage_scope="manufacturer",
        evidence_fields=fields,
    )
    seed_ui.seed_manual_task(
        conn, kind="gate-manual", doc_id=doc_id, group_id=None,
        payload={"route": "mfr-binding", "manufacturer": None},
    )
    return doc_id


def test_binding_candidate_without_manufacturer_offers_a_picker(
    client, conn, picker_rows
):
    """The blocked-queue bug: 14 of 52 staged documents were manufacturer-scope
    candidates the catalogue could not name, and the panel answered with a
    disabled button and no input — while /manual pointed here to resolve them.
    """
    from tests.fixtures import seed_ui

    seed_ui.seed_item(conn, "pick-item-1", manufacturer_raw="PICKCO", md_flag=True)
    _seed_manufacturer(conn, "PICKCO", ["PICKCO"])
    doc_id = _seed_binding_candidate_without_manufacturer(conn, content_hash="h-pick-1")
    conn.commit()

    resp = client.get(f"/staging/{doc_id}/detail")
    assert resp.status_code == 200
    assert 'name="manufacturer"' in resp.text and "data-mfr-picker" in resp.text
    # the decision is reachable, not merely explained
    assert 'value="bind-manufacturer"' in resp.text
    assert "PICKCO" in resp.text
    # and it still cannot be submitted blind
    assert "data-mfr-approve" in resp.text
    assert "disabled" in resp.text


def test_picker_offers_every_manufacturer_including_those_with_no_md_items(
    client, conn, picker_rows
):
    """Denis, 2026-08-31: the reviewer must be able to select any manufacturer,
    not only the ones whose items carry a device class.

    A blank BC device class is a catalogue gap, not grounds to refuse the
    reviewer a decision. Withholding those entities made the picker show 9 of
    366 on live data and, worse, discarded the answer: before migration 053
    nothing recorded a binding that linked no items, so the choice had nowhere
    to go. It does now, so the list opens.

    `[mfr-bind-empty-class]` is untouched -- the LINKS still require
    `md_flag IS TRUE`; only the OFFER changed.
    """
    from tests.fixtures import seed_ui

    seed_ui.seed_item(conn, "pick-md", manufacturer_raw="PICKHASMD", md_flag=True)
    seed_ui.seed_item(conn, "pick-notmd", manufacturer_raw="PICKNOMD", md_flag=False)
    seed_ui.seed_item(conn, "pick-unknown", manufacturer_raw="PICKBLANK", md_flag=None)
    _seed_manufacturer(conn, "PICKHASMD", ["PICKHASMD"])
    _seed_manufacturer(conn, "PICKNOMD", ["PICKNOMD"])
    _seed_manufacturer(conn, "PICKBLANK", ["PICKBLANK"])
    doc_id = _seed_binding_candidate_without_manufacturer(conn, content_hash="h-pick-2")
    conn.commit()

    text = client.get(f"/staging/{doc_id}/detail").text
    assert 'value="PICKHASMD" data-items="1"' in text
    assert 'value="PICKNOMD" data-items="0"' in text
    assert 'value="PICKBLANK" data-items="0"' in text
    # The count is still shown, because it is the volume of what approving does.
    # (The template line-wraps inside the phrase, so match only the head of it.)
    assert "(0 medical-device" in text
    # The restriction prose described a rule that no longer exists.
    assert "leaves" not in text


def test_picker_preselects_the_name_the_document_gave_for_itself(
    client, conn, picker_rows
):
    """The name is usually already in the row's own evidence, in the
    manufacturer's own spelling. `resolve_canonicals` bridges the two (the
    catalogue knows a BC code, the document prints a legal entity), and only an
    unambiguous single hit is preselected."""
    from tests.fixtures import seed_ui

    seed_ui.seed_item(conn, "pick-ivo", manufacturer_raw="PICK001", md_flag=True)
    _seed_manufacturer(conn, "PICKIVO", ["PICK001"])
    _seed_manufacturer(conn, "PICKIVO", ["Pickivo Vivadent AG"], source="playbook")
    doc_id = _seed_binding_candidate_without_manufacturer(
        conn, content_hash="h-pick-3", evidence_manufacturer="Pickivo Vivadent AG",
    )
    conn.commit()

    text = client.get(f"/staging/{doc_id}/detail").text
    assert 'value="PICKIVO" data-items="1"' in text
    assert "selected" in text
    # the button names the consequence, and the count is part of it
    assert "Approve for all PICKIVO items" in text
    assert "(1 item)" in text and "(1 items)" not in text


def test_a_resolvable_manufacturer_with_no_md_items_is_offered_and_preselected(
    client, conn, picker_rows
):
    """The 3Shape shape, in the UI. Read correctly, in the catalogue, and every
    item it holds carries a blank device class.

    This used to be withheld from the list and explained in prose instead --
    the reviewer was told the name they wanted was real but unofferable. Since
    053 the binding has somewhere to live, so the document's own manufacturer
    is offered and preselected like any other, and approving records it while
    linking nothing."""
    from tests.fixtures import seed_ui

    seed_ui.seed_item(conn, "pick-blank", manufacturer_raw="PICKNOCLASS", md_flag=None)
    _seed_manufacturer(conn, "PICKNOCLASS", ["PICKNOCLASS"])
    doc_id = _seed_binding_candidate_without_manufacturer(
        conn, content_hash="h-pick-4", evidence_manufacturer="PICKNOCLASS",
    )
    conn.commit()

    text = client.get(f"/staging/{doc_id}/detail").text
    assert 'value="PICKNOCLASS" data-items="0"' in text
    assert "selected" in text
    # The prose that withheld it is gone; the data-quality link stays, because
    # it is what explains a count of nought.
    assert "not offered below" not in text
    assert "md_class_blank" in text


def test_a_payload_name_that_resolves_keeps_the_one_click_bind(
    client, conn, picker_rows
):
    """C16 auto-binds an unambiguous name, so a candidate reaching this queue
    with one in its payload got there for another reason (confidence below
    `gate.med`, or a candidate written before C16). The panel keeps the
    one-click form for it, but binds by the CURATED canonical rather than the
    document's own spelling, and says how many items that is."""
    from tests.fixtures import seed_ui

    seed_ui.seed_item(conn, "pick-res-1", manufacturer_raw="PICKRES", md_flag=True)
    seed_ui.seed_item(conn, "pick-res-2", manufacturer_raw="PICKRES", md_flag=True)
    _seed_manufacturer(conn, "PICKRES", ["PICKRES"])
    _seed_manufacturer(conn, "PICKRES", ["Pickres Dental GmbH"], source="playbook")
    doc_id = seed_ui.seed_document(
        conn, content_hash="h-pick-6", archive_url="/archive/h-pick-6.pdf",
        doc_type="ISO", regulation="n.a.", coverage_scope="manufacturer",
    )
    seed_ui.seed_manual_task(
        conn, kind="gate-manual", doc_id=doc_id, group_id=None,
        payload={"route": "mfr-binding", "manufacturer": "Pickres Dental GmbH"},
    )
    conn.commit()

    text = client.get(f"/staging/{doc_id}/detail").text
    assert "Approve for all PICKRES items (2 items)" in text
    assert 'name="manufacturer" value="PICKRES"' in text
    assert "data-mfr-picker" not in text          # nothing left to choose


def test_a_payload_name_that_stays_ambiguous_falls_back_to_the_picker(
    client, conn, picker_rows
):
    """Two curated entities fold to one spelling: C16 refuses to guess and so
    must the panel. Offering "Approve for all <raw name> items" here would
    be a one-click coin flip over a manufacturer's entire range."""
    from tests.fixtures import seed_ui

    seed_ui.seed_item(conn, "pick-amb-1", manufacturer_raw="PICKAMBA", md_flag=True)
    seed_ui.seed_item(conn, "pick-amb-2", manufacturer_raw="PICKAMBB", md_flag=True)
    # One spelling, two canonicals. `normalize` casefolds, so these two alias
    # rows fold to the same key; it does NOT drop a trailing period, which is
    # what an earlier version of this test assumed and why it proved nothing.
    _seed_manufacturer(conn, "PICKAMB ONE", ["PICKAMBA", "Pick Amb S.p.A."])
    _seed_manufacturer(conn, "PICKAMB TWO", ["PICKAMBB", "PICK AMB S.P.A."])
    doc_id = seed_ui.seed_document(
        conn, content_hash="h-pick-7", archive_url="/archive/h-pick-7.pdf",
        doc_type="ISO", regulation="n.a.", coverage_scope="manufacturer",
    )
    seed_ui.seed_manual_task(
        conn, kind="gate-manual", doc_id=doc_id, group_id=None,
        payload={"route": "mfr-binding", "manufacturer": "Pick Amb S.p.A."},
    )
    conn.commit()

    text = client.get(f"/staging/{doc_id}/detail").text
    assert "data-mfr-picker" in text
    assert "Approve for all Pick Amb" not in text
    assert "PICKAMB ONE" in text and "PICKAMB TWO" in text


def test_collapsed_row_never_offers_a_bind_it_cannot_submit(client, conn, picker_rows):
    """The row form carried no manufacturer field, so every bind posted from it
    was a guaranteed 422 — and C16 made that the common shape by putting the
    extracted name into every mfr-binding payload, which is what made the row
    render an enabled button.

    Since 2026-09-11 (spec § 3, D5) the collapsed row carries no form at all:
    it offers Open, and every decision, Reject included, is in the panel."""
    from tests.fixtures import seed_ui

    seed_ui.seed_item(conn, "pick-row2", manufacturer_raw="PICKROW2", md_flag=True)
    _seed_manufacturer(conn, "PICKROW2", ["PICKROW2"])
    doc_id = seed_ui.seed_document(
        conn, content_hash="h-pick-8", archive_url="/archive/h-pick-8.pdf",
        doc_type="ISO", regulation="n.a.", coverage_scope="manufacturer",
    )
    seed_ui.seed_manual_task(
        conn, kind="gate-manual", doc_id=doc_id, group_id=None,
        payload={"route": "mfr-binding", "manufacturer": "PICKROW2"},
    )
    conn.commit()

    row = next(
        r for r in re.findall(r'<div class="stage-row.*?\n  </div>',
                              client.get("/staging/docs").text, re.S)
        if f'id="doc-{doc_id}"' in r
    )
    assert "<form" not in row
    assert 'value="bind-manufacturer"' not in row
    assert 'value="reject"' not in row
    assert re.search(r">\s*Open\s*<", row)


def test_collapsed_row_sends_the_binding_decision_to_the_expanded_panel(
    client, conn, picker_rows
):
    """Choosing a manufacturer from a collapsed summary would be a blind click
    over every MD item that manufacturer holds. The row says where the decision
    lives instead: Open (it said "Open to approve" on a disabled button until
    2026-09-11, when every row lost its buttons, spec § 3 D5)."""
    from tests.fixtures import seed_ui

    seed_ui.seed_item(conn, "pick-row", manufacturer_raw="PICKROW", md_flag=True)
    _seed_manufacturer(conn, "PICKROW", ["PICKROW"])
    _seed_binding_candidate_without_manufacturer(conn, content_hash="h-pick-5")
    conn.commit()

    text = client.get("/staging/docs").text
    assert '<span class="c-open">Open</span>' in text
    assert "data-mfr-picker" not in text


def test_staging_shows_the_manufacturer_the_document_printed_when_it_has_no_links(client, conn):
    """CARL MARTIN doc 818, 2026-08-21: the review row read "not known" while
    `evidence` held `manufacturer = Carl Martin GmbH`, page 1, T2, conf 0.98.

    /staging derives the manufacturer from LINKED ITEMS, which is the better
    source when links exist -- a BC code a human curated beats a name parsed
    off a PDF. A document with zero links has no such source, and that is
    exactly the document a human is being asked to attribute. /manual already
    falls back to the printed name for this reason (`printed_manufacturer`);
    /staging never did."""
    suffix = uuid.uuid4().hex[:12]
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, validity_to, status, "
        "content_hash, archive_url, coverage_scope) VALUES ('DoC','MDR','2024-11-26',"
        "'2026-12-31','staged',%s,'/archive/CARL_MARTIN/doc/x.pdf','group') RETURNING doc_id",
        (f"h-printed-{suffix}",),
    ).fetchone()["doc_id"]
    conn.execute(
        "INSERT INTO evidence (doc_id, field, value, archive_url, page, verbatim, tier, "
        "model_id, confidence, extracted_at) VALUES (%s,'manufacturer','Carl Martin GmbH',"
        "'/archive/CARL_MARTIN/doc/x.pdf',1,'Carl Martin GmbH Neuenkamper Str. 80-86',"
        "'T2','claude-sonnet-5',0.98, now())",
        (doc_id,),
    )
    conn.commit()

    resp = client.get(f"/staging/{doc_id}/detail")
    assert resp.status_code == 200
    assert "Carl Martin GmbH" in resp.text
    assert "not known" not in resp.text


def _seed_searchable_doc(conn, *, status="staged", mfr="Carl Martin GmbH",
                         fname="DOC_-_31.12.2026.pdf"):
    """A document findable by nothing but its filename and printed manufacturer:
    Class I self-declared, so it has no certificate number and never will."""
    suffix = uuid.uuid4().hex[:12]
    url = f"/archive/CARL_MARTIN/doc/{suffix}__{fname}"
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, validity_to, status, "
        "content_hash, archive_url, coverage_scope) VALUES ('DoC','MDR','2024-11-26',"
        "'2026-12-31',%s,%s,%s,'group') RETURNING doc_id",
        (status, f"h-search-{suffix}", url),
    ).fetchone()["doc_id"]
    conn.execute(
        "INSERT INTO evidence (doc_id, field, value, archive_url, page, verbatim, tier, "
        "model_id, confidence, extracted_at) VALUES (%s,'manufacturer',%s,%s,1,%s,"
        "'T2','claude-sonnet-5',0.98, now())",
        (doc_id, mfr, url, mfr),
    )
    conn.commit()
    return doc_id


@pytest.mark.parametrize("term", ["Carl Martin", "CARL_MARTIN", "31.12.2026"])
def test_documents_search_finds_a_document_with_no_certificate_number(client, conn, term):
    """/documents searched `cert_number` and nothing else, so a Class I
    declaration — which has no notified body and therefore never has a
    certificate number — was unfindable by any term a person would type.
    CARL MARTIN 818/819, 2026-08-21: of that manufacturer's four documents the
    only findable one was the ISO certificate, the one you least want."""
    doc_id = _seed_searchable_doc(conn, status="production")
    resp = client.get("/documents", params={"q": term})
    assert resp.status_code == 200
    assert f"/documents/{doc_id}" in resp.text


def test_documents_search_finds_a_document_by_its_id(client, conn):
    doc_id = _seed_searchable_doc(conn, status="production")
    resp = client.get("/documents", params={"q": str(doc_id)})
    assert f"/documents/{doc_id}" in resp.text


def test_documents_search_still_excludes_what_does_not_match(client, conn):
    doc_id = _seed_searchable_doc(conn, status="production")
    resp = client.get("/documents", params={"q": "voco"})
    assert f"/documents/{doc_id}" not in resp.text


def test_staging_board_searches_by_manufacturer_and_filename(client, conn):
    """130 staged documents is past what anyone scans by eye, and this was the
    page with no search at all."""
    wanted = _seed_searchable_doc(conn)
    other = _seed_searchable_doc(conn, mfr="VOCO GmbH", fname="voco.pdf")
    resp = client.get("/staging", params={"q": "Carl Martin"})
    assert resp.status_code == 200
    assert f"doc-{wanted}" in resp.text
    assert f"doc-{other}" not in resp.text


def test_manual_board_searches_by_the_document_it_is_about(client, conn):
    wanted = _seed_searchable_doc(conn)
    other = _seed_searchable_doc(conn, mfr="VOCO GmbH", fname="voco.pdf")
    for d in (wanted, other):
        conn.execute(
            "INSERT INTO manual_task (kind, status, doc_id, payload) "
            "VALUES ('gate-manual','open',%s,'{}'::jsonb)", (d,))
    conn.commit()
    resp = client.get("/manual", params={"q": "Carl Martin"})
    assert resp.status_code == 200
    assert f"/staging?doc={wanted}" in resp.text
    assert f"/staging?doc={other}" not in resp.text


def test_a_printed_manufacturer_is_shown_but_never_submitted_as_a_binding(
    client, conn, picker_rows
):
    """Displaying a name and binding to it are different acts.

    Showing the reviewer what the document printed is help. Putting that string
    in the bind form would write mfr-scope links across a manufacturer's whole
    catalogue on the strength of "the model read this off page 1" -- for CARL
    MARTIN that is 2.567 items from one hidden input. Adding the display
    fallback did exactly that for a moment, and removed the picker from the
    rows that exist to ask the question; this pins both halves."""
    from tests.fixtures import seed_ui

    seed_ui.seed_item(conn, "pin-ivo", manufacturer_raw="PIN001", md_flag=True)
    _seed_manufacturer(conn, "PINIVO", ["PIN001"])
    _seed_manufacturer(conn, "PINIVO", ["Pinivo Vivadent AG"], source="playbook")
    doc_id = _seed_binding_candidate_without_manufacturer(
        conn, content_hash="h-pin-1", evidence_manufacturer="Pinivo Vivadent AG",
    )
    conn.commit()

    text = client.get(f"/staging/{doc_id}/detail").text
    # shown ...
    assert "Pinivo Vivadent AG" in text
    # ... never submitted, and the question is still asked
    assert '<input type="hidden" name="manufacturer" value="Pinivo Vivadent AG">' not in text
    assert "Which manufacturer does this cover?" in text


def test_a_manufacturer_scope_document_with_no_links_gets_the_picker_without_the_route(
    client, conn, picker_rows
):
    """Doc 657, 2026-08-21: manufacturer-scope, zero links, and no way to say
    whose it is.

    VALIDATE takes the binding route only when `coverage_scope == manufacturer
    AND not ref_list AND not basic_udi_di` (`validate.py:720`). 657 carries a
    Basic UDI-DI, so it was excluded on the theory that the UDI would find
    items -- and it matched none (`item_mirror.udi` holds no row for
    `081820702TF116PP13L`). It then hit `manufacturer-unresolved`, a BLOCKING
    flag, and became a plain `gate-manual` with no route. The screen decides
    whether to offer the picker from that route, so the one document that most
    needs a human to name a manufacturer was the one that could not be given
    one: Approve would write zero links, Reject throws away a real certificate.

    The offer is a property of the DOCUMENT -- manufacturer scope, nothing
    linked -- not of how the task happened to be labelled."""
    from tests.fixtures import seed_ui

    seed_ui.seed_item(conn, "route-ivo", manufacturer_raw="ROUTE001", md_flag=True)
    _seed_manufacturer(conn, "ROUTEIVO", ["ROUTE001"])
    doc_id = seed_ui.seed_document(
        conn, content_hash="h-noroute-1", archive_url="/archive/h-noroute-1.pdf",
        doc_type="ISO", regulation="MDR", coverage_scope="manufacturer",
        evidence_fields=[{"field": "manufacturer", "value": "Inter-Med, Inc.",
                          "verbatim": "Inter-Med, Inc. located at 2200 South St",
                          "tier": "T1", "model_id": "claude-haiku-4-5",
                          "confidence": 0.95, "page": 1}],
    )
    # a plain gate-manual: no `route`, exactly as manufacturer-unresolved leaves it
    seed_ui.seed_manual_task(conn, kind="gate-manual", doc_id=doc_id, group_id=None,
                             payload={"flags": ["manufacturer-unresolved"]})
    conn.commit()

    text = client.get(f"/staging/{doc_id}/detail").text
    assert "Which manufacturer does this cover?" in text
    assert 'value="ROUTEIVO"' in text


def test_staging_doc_detail_404_for_unknown_or_no_longer_staged_doc(client, conn):
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)
    conn.commit()

    assert client.get("/staging/999999/detail").status_code == 404
    # prod_doc exists but is not staged — expanding a row someone else already
    # decided must say so, not render a decision form for a live document
    assert client.get(f"/staging/{ids['prod_doc']}/detail").status_code == 404
    # non-numeric id is rejected by the :int converter, not passed through
    assert client.get("/staging/not-a-number/detail").status_code == 404


def test_staging_apply_approve_enqueues_gate_apply_payload(client, conn):
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)
    conn.commit()

    resp = client.post(
        f"/staging/{ids['staged_doc']}/apply",
        data={"decided_by": "user:marta", "decision": "approve", "edits": ""},
    )
    assert resp.status_code == 200

    rows = _jobs(conn, f"apply:{ids['staged_doc']}:approve")
    assert len(rows) == 1
    assert rows[0]["type"] == "gate.apply"
    assert rows[0]["priority"] == "interactive"
    assert rows[0]["payload"] == {
        "doc_id": ids["staged_doc"], "decision": "approve", "decided_by": "user:marta",
    }


def test_staging_apply_with_edits_included_in_payload(client, conn):
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)
    conn.commit()

    resp = client.post(
        f"/staging/{ids['staged_doc']}/apply",
        data={
            "decided_by": "user:marta",
            "decision": "approve",
            "edits": '{"validity_to": "2029-03-14"}',
        },
    )
    assert resp.status_code == 200
    rows = _jobs(conn, f"apply:{ids['staged_doc']}:approve")
    assert rows[0]["payload"]["edits"] == {"validity_to": "2029-03-14"}


def test_staging_apply_reject_with_group_id_enqueues_retry_hint(client, conn):
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)
    conn.commit()

    # A reject needs a reason since 2026-09-11 (spec § 3, P1a); the retry hint
    # is independent of it.
    resp = client.post(
        f"/staging/{ids['staged_doc']}/apply",
        data={"decided_by": "user:marta", "decision": "reject", "group_id": "4407",
              "reason": "Wrong manufacturer"},
    )
    assert resp.status_code == 200
    rows = _jobs(conn, f"apply:{ids['staged_doc']}:reject")
    assert rows[0]["payload"]["group_id"] == 4407


def test_staging_apply_bind_manufacturer_on_mfr_candidate(client, conn):
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)
    conn.commit()

    # The confirmed second step: a whole-range approval without `confirm`
    # writes nothing and asks first (tests/test_web_review_safety.py).
    resp = client.post(
        f"/staging/{ids['mfr_doc']}/apply",
        data={"decided_by": "user:marta", "decision": "bind-manufacturer",
              "manufacturer": "Ivoclar", "confirm": "1"},
    )
    assert resp.status_code == 200
    rows = _jobs(conn, f"apply:{ids['mfr_doc']}:bind-manufacturer")
    assert rows[0]["payload"]["decision"] == "bind-manufacturer"
    assert rows[0]["payload"]["manufacturer"] == "Ivoclar"


def test_staging_apply_bind_manufacturer_without_manufacturer_rejected(client, conn):
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)
    conn.commit()
    before = conn.execute("SELECT count(*) AS n FROM job WHERE type='gate.apply'").fetchone()["n"]

    resp = client.post(
        f"/staging/{ids['mfr_doc']}/apply",
        data={"decided_by": "user:marta", "decision": "bind-manufacturer"},
    )
    assert resp.status_code == 422
    after = conn.execute("SELECT count(*) AS n FROM job WHERE type='gate.apply'").fetchone()["n"]
    assert after == before


@pytest.mark.parametrize(
    "overrides",
    [
        {"decision": "not-a-real-decision"},
        # "edit" was removed as a standalone decision — handle_gate_apply only
        # implements approve/reject/bind-manufacturer (docs/dentalia-s0.3-ui-proposal.md).
        {"decision": "edit"},
        # {"decided_by": ""} was here: an empty decided_by used to be a 422.
        # It now falls back to DEFAULT_DECIDED_BY, because the simplified
        # review form has no such field — see the fallback test below.
        {"edits": "{not json"},
        {"decision": "reject", "group_id": "not-a-number"},
    ],
)
def test_staging_apply_invalid_rejected_and_no_job_enqueued(client, conn, overrides):
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)
    conn.commit()
    before = conn.execute("SELECT count(*) AS n FROM job WHERE type='gate.apply'").fetchone()["n"]

    data = {"decided_by": "user:marta", "decision": "approve", "edits": "", **overrides}
    resp = client.post(f"/staging/{ids['staged_doc']}/apply", data=data)
    assert 400 <= resp.status_code < 500
    after = conn.execute("SELECT count(*) AS n FROM job WHERE type='gate.apply'").fetchone()["n"]
    assert after == before


# --------------------------------------------------------------------------- #
# grouping suggestions (S1.6 staging tab, resolve.md §5)
# --------------------------------------------------------------------------- #
def _seed_suggestion(conn, *, item_ref="seed-item-1", candidates=None, suggestion=None,
                     score=0.8, item_name="Seed Composite A"):
    from psycopg.types.json import Json
    from tests.fixtures import seed_ui

    seed_ui.seed_item(conn, item_ref, name=item_name, manufacturer_raw="Ivoclar")
    candidates = candidates or [{"group_id": 1, "score": 0.8, "sample_name": "Sample A"}]
    return conn.execute(
        "INSERT INTO grouping_suggestion (item_ref, candidates, suggestion, score) "
        "VALUES (%s,%s,%s,%s) RETURNING id",
        (item_ref, Json(candidates), Json(suggestion) if suggestion is not None else None, score),
    ).fetchone()["id"]


def test_suggestion_summary_row_shows_item_score_and_top_candidate(client, conn):
    # Unique item_ref per suggestion test — the `conn` fixture only truncates
    # job/domain_lease between tests (see conftest.py), so grouping_suggestion
    # rows persist across the session and the partial unique index (one OPEN
    # suggestion per item_ref) would collide if tests shared an item_ref.
    # Filtered by q rather than read off page 1 for the same reason: rows from
    # earlier tests accumulate and would eventually push this one off the page.
    sid = _seed_suggestion(
        conn, item_ref="gs-item-list",
        candidates=[{"group_id": 42, "score": 0.81, "sample_name": "Tetric PowerFill A2"}],
        suggestion={"action": "assign", "group_id": 42, "rationale": "same shade family"},
        score=0.81,
    )
    conn.commit()

    resp = client.get("/staging/suggestions", params={"q": "gs-item-list"})
    assert resp.status_code == 200
    assert f'id="suggestion-{sid}"' in resp.text
    assert "gs-item-list" in resp.text
    assert "top group 42" in resp.text
    assert "Tetric PowerFill A2" in resp.text
    # the rationale is detail, not summary — it stays out of the list
    assert "same shade family" not in resp.text


def test_suggestion_detail_carries_candidates_rationale_and_form(client, conn):
    sid = _seed_suggestion(
        conn, item_ref="gs-item-detail",
        candidates=[
            {"group_id": 42, "score": 0.81, "sample_name": "Tetric PowerFill A2"},
            {"group_id": 77, "score": 0.62, "sample_name": "Tetric EvoFlow A2"},
        ],
        suggestion={"action": "assign", "group_id": 42, "rationale": "same shade family"},
        score=0.81,
    )
    conn.commit()

    resp = client.get(f"/staging/suggestion/{sid}/detail")
    assert resp.status_code == 200
    assert "Tetric EvoFlow A2" in resp.text          # every candidate, not just the top
    assert "same shade family" in resp.text          # T1 rationale
    assert "Enqueue resolve.group" in resp.text      # assign form


def test_t1_error_suggestion_is_flagged_in_list_and_readable_in_detail(client, conn):
    sid = _seed_suggestion(
        conn, item_ref="gs-item-t1-error", suggestion={"t1_error": "RuntimeError: t1 down"}
    )
    conn.commit()

    listing = client.get("/staging/suggestions", params={"q": "gs-item-t1-error"})
    assert listing.status_code == 200
    assert "T1 error" in listing.text        # visible without expanding the row

    detail = client.get(f"/staging/suggestion/{sid}/detail")
    assert detail.status_code == 200
    assert "t1_error" in detail.text
    assert "t1 down" in detail.text


def test_suggestion_detail_404_once_resolved(client, conn):
    sid = _seed_suggestion(conn, item_ref="gs-item-detail-resolved")
    conn.execute(
        "UPDATE grouping_suggestion SET status='resolved', resolved_by='test', resolved_at=now() "
        "WHERE id=%s", (sid,),
    )
    conn.commit()

    assert client.get(f"/staging/suggestion/{sid}/detail").status_code == 404
    assert client.get("/staging/suggestion/999999/detail").status_code == 404


# --------------------------------------------------------------------------- #
# pagination — the reason this board was split (5,831 open suggestions rendered
# in one page was 7.8 MiB of HTML and 5,888 forms; no browser would open it)
# --------------------------------------------------------------------------- #
@pytest.fixture
def many_suggestions(conn):
    """Seed enough open suggestions to overflow one page, then remove them.

    The only fixture here that cleans up after itself, deliberately: overflowing
    a page needs one item_mirror row per suggestion (the partial unique index
    allows a single open suggestion per item), and the session has no per-test
    rollback — left behind, 60+ items would push other boards' fixtures off
    *their* first page. Suggestions go first; grouping_suggestion FKs item_mirror.
    """
    created: list[str] = []

    def _seed(prefix: str, n: int) -> None:
        for i in range(n):
            ref = f"{prefix}{i:03d}"
            _seed_suggestion(conn, item_ref=ref, score=0.5)
            created.append(ref)
        conn.commit()

    yield _seed

    if created:
        conn.execute("DELETE FROM grouping_suggestion WHERE item_ref = ANY(%s)", (created,))
        conn.execute("DELETE FROM item_mirror WHERE item_ref = ANY(%s)", (created,))
        conn.commit()


def test_suggestions_page_is_capped_and_pages_do_not_overlap(client, many_suggestions):
    from web.app import STAGING_PAGE_SIZE

    n = STAGING_PAGE_SIZE + 10
    many_suggestions("gs-page-", n)

    first = client.get("/staging/suggestions", params={"q": "gs-page-", "page": 0})
    second = client.get("/staging/suggestions", params={"q": "gs-page-", "page": 1})
    assert first.status_code == 200 and second.status_code == 200

    ids_1 = re.findall(r'id="suggestion-(\d+)"', first.text)
    ids_2 = re.findall(r'id="suggestion-(\d+)"', second.text)
    assert len(ids_1) == STAGING_PAGE_SIZE
    assert len(ids_2) == n - STAGING_PAGE_SIZE
    # created_at ties are broken by id, so paging neither repeats nor drops rows
    assert not set(ids_1) & set(ids_2)
    assert len(set(ids_1) | set(ids_2)) == n

    assert f"1–{STAGING_PAGE_SIZE} of {n}" in first.text
    assert f"{STAGING_PAGE_SIZE + 1}–{n} of {n}" in second.text


def test_staging_board_shell_renders_only_one_page_of_suggestions(client, many_suggestions):
    from web.app import STAGING_PAGE_SIZE

    many_suggestions("gs-shell-", STAGING_PAGE_SIZE + 10)

    resp = client.get("/staging")
    assert resp.status_code == 200
    assert len(re.findall(r'id="suggestion-(\d+)"', resp.text)) == STAGING_PAGE_SIZE


@pytest.fixture
def many_staged_docs(conn):
    """Seed enough staged documents to overflow one board page, then remove them.

    Cleans up after itself for the same reason `many_suggestions` does: the
    session has no per-test rollback, and 60 leftover staged documents would
    push every other board fixture off page 0. Fresh doc_ids are the highest in
    the table and the board orders by doc_id, so these land on the LAST page —
    which is precisely the shape a deep link has to survive.
    """
    created: list[int] = []

    def _seed(n: int) -> list[int]:
        for i in range(n):
            created.append(
                conn.execute(
                    "INSERT INTO document (type, regulation, coverage_scope, "
                    "content_hash, archive_url, status) VALUES "
                    "('DoC','MDR','group',%s,'file:///deep.pdf','staged') "
                    "RETURNING doc_id",
                    (f"h-staged-deep-{i:03d}",),
                ).fetchone()["doc_id"]
            )
        conn.commit()
        return created

    yield _seed

    if created:
        conn.execute("DELETE FROM document WHERE doc_id = ANY(%s)", (created,))
        conn.commit()


def _focused_row(text: str, doc_id: int) -> str:
    m = re.search(rf'id="doc-{doc_id}">\s*(<details.*?</details>)', text, re.S)
    assert m, f"row for doc {doc_id} not rendered"
    return m.group(1)


def test_staging_deep_link_lands_on_the_page_that_holds_the_document(
    client, many_staged_docs
):
    """`/staging#doc-N` on its own is a dead link for every document past the
    first page — the board renders page 0 only, so the anchor resolves to
    nothing and the reviewer is dropped on a list that silently does not
    contain their row. `?doc=N` has to find the page."""
    from web.app import STAGING_PAGE_SIZE

    ids = many_staged_docs(STAGING_PAGE_SIZE + 10)
    target = ids[-1]

    board = client.get("/staging")
    assert board.status_code == 200
    assert f'id="doc-{target}"' not in board.text

    resp = client.get("/staging", params={"doc": target})
    assert resp.status_code == 200
    assert f'id="doc-{target}"' in resp.text


def test_staging_deep_link_row_arrives_open_and_fetches_its_own_body(
    client, many_staged_docs
):
    """Arriving at the row is not enough: the body loads on `toggle`, an event
    a server-rendered `open` never fires, so a linked row would sit on
    "Loading…" forever. The focused row asks for its own detail; every other
    row keeps the collapse-until-asked behaviour the 7.8 MiB board bought."""
    from web.app import STAGING_PAGE_SIZE

    ids = many_staged_docs(STAGING_PAGE_SIZE + 10)
    target = ids[-1]

    resp = client.get("/staging", params={"doc": target})
    row = _focused_row(resp.text, target)
    assert re.match(r"<details[^>]*\bopen\b", row)
    assert 'hx-trigger="load"' in row

    assert len(re.findall(r"<details[^>]*\bopen\b", resp.text)) == 1
    assert 'hx-trigger="toggle once"' in resp.text


def test_staging_deep_link_to_a_decided_document_says_so_on_the_board(client, conn):
    """A document decided between the link being rendered and followed must
    say what happened. Rendering page 0 with no notice reads as "your row is
    right here" and sends the reviewer hunting."""
    resp = client.get("/staging", params={"doc": 2_000_000_000})
    assert resp.status_code == 200
    assert "no longer awaiting review" in resp.text
    assert "Documents to review" in resp.text


def test_document_detail_links_a_staged_document_to_its_review_row(client, conn):
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)
    conn.commit()
    staged = ids["staged_doc"]

    resp = client.get(f"/documents/{staged}")
    assert resp.status_code == 200
    assert f'href="/staging?doc={staged}#doc-{staged}"' in resp.text


def test_documents_list_links_a_staged_row_to_its_review(client, conn):
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)
    conn.commit()
    staged = ids["staged_doc"]

    resp = client.get("/documents", params={"status": "staged"})
    assert resp.status_code == 200
    assert f'href="/staging?doc={staged}#doc-{staged}"' in resp.text


def test_documents_list_offers_no_review_link_for_decided_rows(client, conn):
    from tests.fixtures import seed_ui

    seed_ui.seed_demo(conn)
    conn.commit()

    resp = client.get("/documents", params={"status": "production"})
    assert resp.status_code == 200
    assert "/staging?doc=" not in resp.text


def test_document_detail_names_the_cited_certificate_it_points_at(client, conn):
    """"Document #1204" is not an answer to "which certificate?". The row
    carries the cited document's own type, number and expiry — the expiry
    especially, since a DoC's real renewal date lives there."""
    doc_id, cert_id, cert_number = _seed_doc_citing_cert(conn, cert_day_offset=15)

    resp = client.get(f"/documents/{doc_id}")
    assert resp.status_code == 200
    assert f'href="/documents/{cert_id}"' in resp.text
    assert cert_number in resp.text
    assert "EC certificate" in resp.text


def test_document_detail_does_not_claim_a_rejected_certs_expiry_is_real(client, conn):
    """[final-cert-status-join] on the document page. The row used to print the
    cited certificate's date followed by "its own validity_to is the real
    expiry" — a sentence that is false the moment a human rejects that
    certificate. Identity is still shown (what was cited is a fact); only the
    date claim is withdrawn, and the DoC falls back to its own review horizon."""
    doc_id, cert_id, cert_number = _seed_doc_citing_cert(
        conn, cert_day_offset=15, cert_status="rejected"
    )
    cert_date = conn.execute(
        "SELECT validity_to FROM document WHERE doc_id=%s", (cert_id,)
    ).fetchone()["validity_to"].isoformat()

    resp = client.get(f"/documents/{doc_id}")
    assert resp.status_code == 200
    assert f'href="/documents/{cert_id}"' in resp.text
    assert cert_number in resp.text
    assert cert_date not in resp.text
    assert "2029-01-01" in resp.text          # validity_from 2024-01-01 + 5 years


def test_document_detail_shows_the_same_expiry_the_items_board_does(client, conn):
    """[final-documents-page-parity]: the document page showed the raw column,
    so a DoC inheriting its date read "—" here and a real date on /items."""
    doc_id, cert_id, _ = _seed_doc_citing_cert(conn, cert_day_offset=15)
    cert_date = conn.execute(
        "SELECT validity_to FROM document WHERE doc_id=%s", (cert_id,)
    ).fetchone()["validity_to"].isoformat()

    resp = client.get(f"/documents/{doc_id}")
    assert resp.status_code == 200
    # The "Valid to" row itself, not merely somewhere on the page: the cited
    # certificate's own row has always printed that date, which is exactly how
    # the page could show a document's expiry twice and its headline field "—".
    cell = re.search(r"<th>Valid to</th><td>(.*?)</td>", resp.text, re.S)
    assert cell, resp.text
    assert cert_date in cell.group(1)


def test_document_detail_offers_no_review_link_for_a_decided_document(client, conn):
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)
    conn.commit()
    prod = ids["prod_doc"]

    resp = client.get(f"/documents/{prod}")
    assert resp.status_code == 200
    assert f"/staging?doc={prod}" not in resp.text


def test_manual_queue_doc_link_carries_the_page_hint_not_a_bare_anchor(client, conn):
    """Same dead anchor, older instance: the manual queue has always linked
    `/staging#doc-N`."""
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)
    conn.commit()
    manual_doc = ids["manual_doc"]

    resp = client.get("/manual")
    assert resp.status_code == 200
    assert f'href="/staging?doc={manual_doc}#doc-{manual_doc}"' in resp.text
    assert f'href="/staging#doc-{manual_doc}"' not in resp.text


def test_suggestions_search_filters_rows_and_the_count(client, conn):
    _seed_suggestion(conn, item_ref="gs-find-me")
    _seed_suggestion(conn, item_ref="gs-not-this-one")
    conn.commit()

    resp = client.get("/staging/suggestions", params={"q": "gs-find-me"})
    assert resp.status_code == 200
    assert "gs-find-me" in resp.text
    assert "gs-not-this-one" not in resp.text
    assert "1–1 of 1" in resp.text
    # q survives into the pager links so paging a filtered list stays filtered
    assert "No open suggestions match" not in resp.text

    empty = client.get("/staging/suggestions", params={"q": "gs-no-such-item"})
    assert empty.status_code == 200
    assert "No open suggestions match" in empty.text


def test_suggestions_search_treats_wildcards_literally(client, conn):
    _seed_suggestion(conn, item_ref="gs-literal-pct")
    conn.commit()

    resp = client.get("/staging/suggestions", params={"q": "%"})
    assert resp.status_code == 200
    # a bare % must not match everything — it is escaped to a literal
    assert "gs-literal-pct" not in resp.text


def test_suggestion_assign_enqueues_forced_resolve_group(client, conn):
    sid = _seed_suggestion(conn, item_ref="gs-item-assign")
    conn.commit()

    resp = client.post(
        f"/staging/suggestion/{sid}/assign", data={"action": "assign", "group_id": "42"}
    )
    assert resp.status_code == 200
    rows = _jobs(conn, "resolve:manual:gs-item-assign:42")
    assert len(rows) == 1
    assert rows[0]["type"] == "resolve.group"
    assert rows[0]["priority"] == "interactive"
    assert rows[0]["payload"] == {"item_ref": "gs-item-assign", "group_id": 42}


def test_suggestion_start_new_group_enqueues_force_new_group(client, conn):
    sid = _seed_suggestion(conn, item_ref="gs-item-new")
    conn.commit()

    resp = client.post(f"/staging/suggestion/{sid}/assign", data={"action": "new"})
    assert resp.status_code == 200
    rows = _jobs(conn, "resolve:manual:gs-item-new:new")
    assert len(rows) == 1
    assert rows[0]["payload"] == {"item_ref": "gs-item-new", "force_new_group": True}


def test_suggestion_assign_resubmit_dedupes(client, conn):
    sid = _seed_suggestion(conn, item_ref="gs-item-dedupe")
    conn.commit()

    client.post(f"/staging/suggestion/{sid}/assign", data={"action": "assign", "group_id": "42"})
    client.post(f"/staging/suggestion/{sid}/assign", data={"action": "assign", "group_id": "42"})

    rows = _jobs(conn, "resolve:manual:gs-item-dedupe:42")
    assert len(rows) == 1   # active-scope dedupe, C2


def test_suggestion_assign_without_group_id_rejected(client, conn):
    sid = _seed_suggestion(conn, item_ref="gs-item-invalid")
    conn.commit()
    before = conn.execute("SELECT count(*) AS n FROM job WHERE type='resolve.group'").fetchone()["n"]

    resp = client.post(f"/staging/suggestion/{sid}/assign", data={"action": "assign", "group_id": ""})
    assert resp.status_code == 422
    after = conn.execute("SELECT count(*) AS n FROM job WHERE type='resolve.group'").fetchone()["n"]
    assert after == before


def test_suggestion_assign_unknown_id_rejected(client, conn):
    resp = client.post("/staging/suggestion/999999/assign", data={"action": "assign", "group_id": "1"})
    assert resp.status_code == 422


def test_resolved_suggestion_disappears_from_staging_board(client, conn):
    sid = _seed_suggestion(conn, item_ref="gs-item-resolved")
    conn.execute(
        "UPDATE grouping_suggestion SET status='resolved', resolved_by='test', resolved_at=now() "
        "WHERE id=%s", (sid,),
    )
    conn.commit()

    resp = client.get("/staging")
    assert resp.status_code == 200
    # Other tests in this session may leave other suggestions open (no
    # per-test rollback — see conftest.py), so check precisely via the query
    # layer rather than asserting the whole "no open suggestions" empty state.
    from web.app import _open_suggestions_page
    rows, total = _open_suggestions_page(conn, q="gs-item-resolved")
    assert (rows, total) == ([], 0)


def test_manual_board_lists_open_tasks(client, conn):
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)
    conn.commit()

    resp = client.get("/manual")
    assert resp.status_code == 200
    assert f"Task #{ids['manual_task']}" in resp.text
    assert f"Task #{ids['mfr_task']}" in resp.text
    assert "mfr-binding" in resp.text
    assert f"Task #{ids['dead_end_task']}" in resp.text
    assert "discovery-dead-end" in resp.text
    assert f'/staging?doc={ids["manual_doc"]}#doc-{ids["manual_doc"]}' in resp.text


def test_manual_resolve_dead_end_enqueues_discover_group(client, conn):
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)
    conn.commit()

    resp = client.post(f"/manual/{ids['dead_end_task']}/resolve")
    assert resp.status_code == 200
    rows = _jobs(conn, f"discover:manual:{ids['dead_end_task']}")
    assert len(rows) == 1
    assert rows[0]["type"] == "discover.group"
    assert rows[0]["priority"] == "interactive"
    assert rows[0]["payload"] == {"group_id": 9001}


def test_manual_resolve_gate_manual_task_rejected(client, conn):
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)
    conn.commit()

    resp = client.post(f"/manual/{ids['manual_task']}/resolve")
    assert resp.status_code == 422
    assert conn.execute(
        "SELECT count(*) AS n FROM job WHERE type='discover.group'"
    ).fetchone()["n"] == 0


def test_manual_resolve_unknown_task_rejected(client, conn):
    resp = client.post("/manual/999999/resolve")
    assert resp.status_code == 422


def test_manual_resolve_already_resolved_task_rejected(client, conn):
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)
    conn.execute(
        "UPDATE manual_task SET status='resolved', resolved_by='test', resolved_at=now() "
        "WHERE id=%s", (ids["dead_end_task"],),
    )
    conn.commit()

    resp = client.post(f"/manual/{ids['dead_end_task']}/resolve")
    assert resp.status_code == 422


def test_dead_board_shows_last_error(client, conn):
    """The error is the point of this board. It moved from a per-job heading to
    the group card when 47 dead jobs turned out to be 3 causes (2026-08-17), so
    this asserts the error and the route to the job rather than the old
    "Job #N" heading the per-job cards carried."""
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)
    conn.commit()

    resp = client.get("/dead")
    assert resp.status_code == 200
    assert "ConnectError" in resp.text
    assert f'href="/jobs/{ids["dead_job"]}"' in resp.text


def test_dead_board_links_each_job_to_its_own_page(client, conn):
    """`/jobs/{id}` carries the payload and the full error; the failed-jobs
    board printed the id as text, so the only way there was to type the URL."""
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)
    conn.commit()

    resp = client.get("/dead")
    assert resp.status_code == 200
    assert f'href="/jobs/{ids["dead_job"]}"' in resp.text


def _seed_dead(conn, *, job_type: str, error: str, key: str) -> int:
    """One dead job with a chosen error, for the grouping cases below."""
    return conn.execute(
        "INSERT INTO job (type, payload, dedupe_key, status, priority, attempts, "
        "                 max_attempts, last_error, finished_at) "
        "VALUES (%s::job_type, '{}'::jsonb, %s, 'dead', 'sweep', 5, 5, %s, now()) "
        "RETURNING id",
        (job_type, key, error),
    ).fetchone()["id"]


def test_dead_board_groups_jobs_that_died_of_the_same_thing(client, conn):
    """47 dead jobs on the live board were 3 causes: 29 sharing one
    CheckViolation, 12 a missing field, 6 an API 400. One card per job buries
    that, and the 29th copy of an error teaches nothing the first did not."""
    same = ('CheckViolation: new row for relation "document" violates check '
            'constraint "document_coverage_scope_vocabulary" DETAIL: Failing row ')
    for i in range(3):
        # identical up to the signature cut, divergent after it — the tail is
        # the row that tripped it, which must not split the group
        _seed_dead(conn, job_type="gate.candidate", error=f"{same}contains ({i}, other, ...)",
                   key=f"grp-a-{i}")
    _seed_dead(conn, job_type="gate.candidate", error="ValueError: required field 'regulation'",
               key="grp-b-0")
    conn.commit()

    text = client.get("/dead").text
    assert "gate.candidate" in text
    # the group states its own size, and the divergent tails did not split it
    assert re.search(r"Re-run all 3 \(interactive priority\)", text), text[:600]
    assert "Re-run all 1 (interactive priority)" in text
    # the heading names the CAUSE — four cards headed by their job type read as
    # an ungrouped list, which is how the first version of this page landed
    assert "<h3 class=\"dead-cause\">CheckViolation" in text
    assert _rendered("ValueError: required field 'regulation'") in text
    # a group whose job list is complete says so plainly; the capped case is
    # covered below, because "the 28 jobs behind this" under a heading of 29
    # reads as an off-by-one rather than as a cap
    assert "The 3 jobs behind this" in text


def test_a_dead_group_says_when_its_job_list_is_capped(client, conn):
    """`n` counts the whole dead set while the expanded list is what the page
    fetched. Once the row cap bites the two differ, and an unexplained gap
    between them reads as a bug in the count."""
    from web.app import DEFAULT_JOB_LIST_LIMIT

    error = "OverflowError: too many of one thing"
    for i in range(DEFAULT_JOB_LIST_LIMIT + 2):
        _seed_dead(conn, job_type="fetch.url", error=error, key=f"grp-cap-{i}")
    conn.commit()

    text = client.get("/dead").text
    assert f"Re-run all {DEFAULT_JOB_LIST_LIMIT + 2} (interactive priority)" in text
    assert f"of the {DEFAULT_JOB_LIST_LIMIT + 2} jobs behind this" in text
    assert "past this page&#39;s row cap" in text or "past this page's row cap" in text


def _group_digest_of(client, needle: str) -> str:
    """The digest the board rendered for the group whose card mentions `needle`.

    Read off the page rather than recomputed in the test: that is what a browser
    posts, so a mismatch between what the board renders and what the route
    accepts shows up here instead of in production.

    Split on the cause heading rather than on `<div class="card">`: each group
    card opens with one, and the card contains nested `<details>` blocks that
    defeat any non-greedy match for its closing tag.
    """
    chunks = client.get("/dead").text.split('<h3 class="dead-cause">')[1:]
    card = next(c for c in chunks if needle in c)
    return re.search(r'name="digest" value="([0-9a-f]+)"', card).group(1)


def test_dead_group_rerun_requeues_every_job_in_the_group(client, conn):
    """The bulk action re-derives its group server-side from a digest rather
    than trusting an id list or a slab of error text from the form: the set can
    grow between render and click, and a stale list would re-run part of a cause
    while reporting success.

    The error here carries a newline on purpose. Posting the raw signature (the
    first version) put 120 characters of error text through a hidden input, and
    a browser normalising line endings on submit would have matched nothing and
    called the group vanished — a silent no-op wearing a stale-board message.
    """
    error = ("BadRequestError: Error code: 400 - schema rejected\n"
             "DETAIL:  the model returned a field the tool does not declare")
    ids = [_seed_dead(conn, job_type="extract.doc", error=error, key=f"grp-c-{i}")
           for i in range(3)]
    other = _seed_dead(conn, job_type="extract.doc", error="TimeoutError: read timed out",
                       key="grp-d-0")
    conn.commit()
    before = conn.execute("SELECT count(*) AS n FROM job").fetchone()["n"]

    digest = _group_digest_of(client, "BadRequestError")
    resp = client.post("/dead/rerun-group", data={"digest": digest})
    assert resp.status_code == 200
    assert "Re-queued 3 jobs" in resp.text

    after = conn.execute("SELECT count(*) AS n FROM job").fetchone()["n"]
    assert after == before + 3            # the other cause was left alone
    for jid in ids:
        # dead is terminal and never mutated; the re-run is a new row beside it
        assert conn.execute("SELECT status FROM job WHERE id=%s", (jid,)).fetchone()["status"] == "dead"
    assert conn.execute(
        "SELECT count(*) AS n FROM job WHERE priority='interactive' AND status='pending'"
    ).fetchone()["n"] >= 3
    assert conn.execute("SELECT status FROM job WHERE id=%s", (other,)).fetchone()["status"] == "dead"


def test_dead_group_rerun_rejects_a_digest_that_matches_no_group(client, conn):
    """A digest is not a query: it selects among groups the server derived, so
    an unknown or stale one can only ever mean "reload", never a partial
    re-run. Nothing is enqueued on the way out."""
    before = conn.execute("SELECT count(*) AS n FROM job").fetchone()["n"]

    for bad in ("deadbeefdeadbeef", "not-a-digest", "' OR 1=1 --"):
        resp = client.post("/dead/rerun-group", data={"digest": bad})
        assert resp.status_code == 422, bad
        assert "reload the board" in resp.text

    # a missing field is FastAPI's own 422 before the route runs, which is a
    # different (and correct) answer from "that group is gone"
    assert client.post("/dead/rerun-group", data={}).status_code == 422

    assert conn.execute("SELECT count(*) AS n FROM job").fetchone()["n"] == before


def test_dead_rerun_enqueues_same_type_payload_and_dedupe_key(client, conn):
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)
    conn.commit()
    dead_id = ids["dead_job"]
    dead_row = conn.execute("SELECT dedupe_key, type, payload FROM job WHERE id=%s", (dead_id,)).fetchone()

    resp = client.post(f"/dead/{dead_id}/rerun")
    assert resp.status_code == 200

    rows = conn.execute(
        "SELECT * FROM job WHERE dedupe_key=%s ORDER BY id", (dead_row["dedupe_key"],)
    ).fetchall()
    assert len(rows) == 2  # the original dead row + the new re-run
    rerun = rows[-1]
    assert rerun["id"] != dead_id
    assert rerun["type"] == dead_row["type"]
    assert rerun["payload"] == dead_row["payload"]
    assert rerun["priority"] == "interactive"
    # the original dead row is untouched (dead is terminal, never mutated)
    assert conn.execute("SELECT status FROM job WHERE id=%s", (dead_id,)).fetchone()["status"] == "dead"


def test_dead_rerun_rejects_non_dead_job(client, conn):
    from app import queue

    jid = queue.enqueue(conn, "backfill.scan", {}, "not-dead-probe")
    conn.commit()

    resp = client.post(f"/dead/{jid}/rerun")
    assert resp.status_code == 422


def test_dead_rerun_leaves_the_alarm_open_until_the_work_succeeds(client, conn):
    """Re-queueing is not success, so the button must NOT clear the alarm --
    a job that dies again would have had its alarm cleared by the very attempt
    to fix it, which is the one case the alarm exists for. `queue.finish` closes
    it when the work completes (covered in tests/test_queue.py).

    This also pins the producer boundary: the web role holds no write grant on
    manual_task, so an attempt to resolve here is a 500, not a design choice."""
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)
    dead_id = ids["dead_job"]
    conn.execute(
        "INSERT INTO manual_task (kind, payload) VALUES ('dead-job-followup', %s)",
        (Json({"job_id": dead_id, "job_type": "extract.doc", "error": "boom"}),),
    )
    conn.commit()

    assert client.post(f"/dead/{dead_id}/rerun").status_code == 200

    assert conn.execute(
        "SELECT status FROM manual_task WHERE payload->>'job_id' = %s", (str(dead_id),)
    ).fetchone()["status"] == "open"


# --------------------------------------------------------------------------- #
# JSON read API (S1.6) — production-visibility only
# --------------------------------------------------------------------------- #
def test_api_item_documents_returns_production_only(client, conn):
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)   # seed-item-1: production link + a staged (name-family) one
    conn.commit()

    resp = client.get("/api/items/seed-item-1/documents")
    assert resp.status_code == 200
    body = resp.json()
    assert body["item_ref"] == "seed-item-1"
    doc_ids = {d["doc_id"] for d in body["documents"]}
    assert ids["prod_doc"] in doc_ids
    assert ids["staged_doc"] not in doc_ids


def test_api_item_documents_empty_for_unknown_item(client):
    resp = client.get("/api/items/does-not-exist/documents")
    assert resp.status_code == 200
    body = resp.json()
    assert body["documents"] == []
    # An item this system never mirrored: identity is null across the board.
    # That is the only signal separating "no such item" from "no documents
    # yet" -- the distinction the endpoint deliberately refuses to make with
    # a 404 (docs/specs/read-api.md §2).
    assert body["item_name"] is None
    assert body["manufacturer"] is None
    assert body["manufacturer_code"] is None
    assert body["mfr_ref"] is None


def test_api_document_includes_complete_evidence(client, conn):
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)
    conn.commit()

    resp = client.get(f"/api/documents/{ids['prod_doc']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["document"]["doc_id"] == ids["prod_doc"]
    fields = {e["field"] for e in body["evidence"]}
    assert {"type", "regulation", "coverage_scope"} <= fields
    for e in body["evidence"]:
        assert e["tier"] and e["verbatim"] and e["confidence"] is not None


def test_api_document_404_for_staged_doc(client, conn):
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)
    conn.commit()

    resp = client.get(f"/api/documents/{ids['staged_doc']}")
    assert resp.status_code == 404


def test_api_document_404_for_unknown_doc(client):
    resp = client.get("/api/documents/999999")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# ?include_superseded — the supersession chain (migration 070)
# --------------------------------------------------------------------------- #
def _seed_superseded_pair_web(conn, item_ref):
    """As GATE writes it: supersession flips `document.status` and sets
    `superseded_by`, never touching the link (gate.py::_supersede)."""
    old = _seed_item_with_doc(conn, item_ref, validity_to="2025-01-01")
    new = _seed_item_with_doc(conn, item_ref, validity_to="2027-01-01")
    conn.execute(
        "UPDATE document SET status='superseded', superseded_by=%s WHERE doc_id=%s",
        (new, old),
    )
    conn.commit()
    return old, new


def test_api_item_documents_hides_superseded_without_the_flag(client, conn):
    old, new = _seed_superseded_pair_web(conn, "API-SUP-OFF")
    body = client.get("/api/items/API-SUP-OFF/documents").json()
    assert {d["doc_id"] for d in body["documents"]} == {new}


def test_api_item_documents_include_superseded_returns_the_chain(client, conn):
    old, new = _seed_superseded_pair_web(conn, "API-SUP-ON")
    body = client.get(
        "/api/items/API-SUP-ON/documents?include_superseded=true").json()
    by_id = {d["doc_id"]: d for d in body["documents"]}
    assert set(by_id) == {old, new}
    assert by_id[old]["status"] == "superseded"
    assert by_id[old]["superseded_by"] == new


def test_api_item_documents_customer_view_refuses_the_flag(client, conn):
    """400, not a silent drop: the caller asked for something we will not do."""
    _seed_superseded_pair_web(conn, "API-SUP-CUST")
    resp = client.get(
        "/api/items/API-SUP-CUST/documents?view=customer&include_superseded=true")
    assert resp.status_code == 400
    assert "customer view" in resp.json()["detail"]


def test_api_document_serves_a_superseded_document(client, conn):
    """Otherwise every doc_id the history list hands back 404s on fetch."""
    old, _ = _seed_superseded_pair_web(conn, "API-SUP-FETCH")
    resp = client.get(f"/api/documents/{old}")
    assert resp.status_code == 200
    assert resp.json()["document"]["status"] == "superseded"


def test_api_kpi_matches_underlying_function(client, conn):
    from tests.fixtures import seed_ui

    seed_ui.seed_kpi_demo(conn)
    conn.commit()

    resp = client.get("/api/kpi")
    assert resp.status_code == 200
    body = resp.json()
    kpi = body["coverage"]
    assert kpi["strict_total"] == 4 and kpi["processed_total"] == 5
    # global aggregate, not catalogue-scoped — see the equivalent note in
    # test_kpi_spend_converts_and_surfaces_unpriced_calls
    assert body["spend"]["all_time"]["unpriced_calls"] >= 1


def test_production_view_serves_the_effective_expiry(conn):
    """[task4-cert-view]: the view (and therefore /api) served the DoC's raw
    `validity_to` -- NULL here -- while the UI pages already followed
    `document_effective_expiry` (027) via their own direct joins. The API and
    the UI disagreed about the same document. `expires`/`expiry_basis` close
    that: `validity_to` keeps its raw meaning (additive-only view change,
    migration 034), `expires` carries the effective date."""
    doc_id, cert_id = _seed_item_with_doc_citing_cert(conn, "expiry-view-1", "2027-05-01")
    row = conn.execute(
        "SELECT validity_to, expires, expiry_basis FROM item_document_production "
        "WHERE doc_id = %s", (doc_id,)).fetchone()
    assert row["validity_to"] is None                # raw meaning unchanged
    assert row["expires"] == date(2027, 5, 1)         # inherited from the cited cert
    assert row["expiry_basis"] == "inherited"


def test_api_documents_carry_name_and_effective_expiry(client, conn):
    """The response is a contract, not `SELECT *`: an explicit column list plus
    a derived `name` (latest-rev manufacturer evidence + type + regulation +
    effective valid-to). `valid_to` is the effective expiry (`expires`), not
    the DoC's own raw NULL -- the same fact Task 1 put on the view."""
    doc_id, cert_id = _seed_item_with_doc_citing_cert(conn, "voco-item-1", "2027-05-01")
    # rev 1 got the manufacturer name wrong; rev 2 re-extracted it correctly.
    # `name` must read the LATEST revision, per extract_rev (014).
    conn.execute(
        "INSERT INTO evidence (doc_id, field, value, archive_url, page, verbatim, "
        "tier, model_id, confidence, extracted_at, extract_rev) VALUES "
        "(%s, 'manufacturer', 'WRONG GmbH', '/archive/doc.pdf', 1, 'WRONG GmbH', "
        "'T1', 'claude-haiku-4-5', 0.9, now(), 1)",
        (doc_id,),
    )
    conn.execute(
        "INSERT INTO evidence (doc_id, field, value, archive_url, page, verbatim, "
        "tier, model_id, confidence, extracted_at, extract_rev) VALUES "
        "(%s, 'manufacturer', 'VOCO GmbH', '/archive/doc.pdf', 1, 'VOCO GmbH', "
        "'T1', 'claude-haiku-4-5', 0.9, now(), 2)",
        (doc_id,),
    )
    conn.commit()

    resp = client.get("/api/items/voco-item-1/documents")
    assert resp.status_code == 200
    body = resp.json()
    d = body["documents"][0]
    assert set(d) == {"doc_id", "name", "type", "regulation", "match_basis",
                       "valid_from", "valid_to", "expiry_basis", "url"}
    assert d["valid_to"] == "2027-05-01"                  # effective, not the raw NULL
    assert d["name"].startswith("VOCO GmbH DoC (MDR)")    # latest-rev evidence wins
    assert "WRONG" not in d["name"]


def test_api_carries_enough_item_identity_to_confirm_the_article(client, conn):
    """`item_ref` alone does not let a consumer confirm it asked about the
    article it meant. Name, canonical manufacturer, the BC vendor code and
    `mfr_ref` travel with the documents; the manufacturer is resolved through
    `manufacturer_alias` (the seed's `manufacturer_raw` is the BC code `077`),
    never a second mapping."""
    _seed_item_with_doc_citing_cert(conn, "identity-item-1", "2027-05-01")
    conn.execute(
        "INSERT INTO manufacturer_alias (raw_name, canonical_name) "
        "VALUES ('077','VOCO GmbH') ON CONFLICT (raw_name) DO NOTHING"
    )
    conn.commit()

    body = client.get("/api/items/identity-item-1/documents").json()
    assert body["item_ref"] == "identity-item-1"
    assert body["item_name"] == "Widget"
    assert body["manufacturer"] == "VOCO GmbH"       # alias-resolved, not the code
    assert body["manufacturer_code"] == "077"        # the code still travels
    assert body["mfr_ref"] == "W-1"


def test_api_manufacturer_falls_back_to_the_bc_code_with_no_alias(client, conn):
    """No alias row yet -> the raw BC code, never null: a mirrored item always
    reports a manufacturer, even before anyone canonicalised the code."""
    # `manufacturer_alias` is truncated per test (conftest), so '077' is
    # unaliased here without deleting anything.
    _seed_item_with_doc_citing_cert(conn, "no-alias-item-1", "2027-05-01")

    body = client.get("/api/items/no-alias-item-1/documents").json()
    assert body["manufacturer"] == "077"
    assert body["manufacturer_code"] == "077"


def test_api_url_is_constructed_never_the_stored_handle(client, conn):
    """`url` must be the `/documents/{doc_id}/file` route, NOT `archive_url`:
    the handle is storage-internal, and pre-fix backfill rows hold `/imports`
    corpus paths nothing serves (measured 2026-08-24: 137 of 193 production
    documents 404'd through the raw handle). Seed one such row and assert the
    API hands out the constructed link regardless."""
    doc_id, cert_id = _seed_item_with_doc_citing_cert(conn, "imports-item-1", "2027-01-01")
    conn.execute(
        "UPDATE document SET archive_url='/imports/dentalia-sftp/GC/DOC/x.pdf' "
        "WHERE doc_id=%s", (doc_id,))
    conn.commit()

    d = client.get("/api/items/imports-item-1/documents").json()["documents"][0]
    assert d["url"] == f"/documents/{doc_id}/file"


def test_api_name_survives_a_document_with_no_manufacturer_evidence(client, conn):
    """No manufacturer evidence at all -- e.g. a T0 template that never
    extracted one -- must degrade gracefully to "DoC (MDR), valid to ..."
    rather than a KeyError or a literal "None DoC"."""
    doc_id, cert_id = _seed_item_with_doc_citing_cert(conn, "no-mfr-item-1", "2026-09-09")
    resp = client.get("/api/items/no-mfr-item-1/documents")
    assert resp.status_code == 200
    d = resp.json()["documents"][0]
    assert d["name"] == "DoC (MDR), valid to 2026-09-09"


# --------------------------------------------------------------------------- #
# G3 v0 auth — proxy-trusted header (docker-compose.yml `caddy` service)
# --------------------------------------------------------------------------- #
def test_healthz_unauthenticated_even_when_auth_required(tmp_path, test_db_url):
    cfg = Web(api_database_url=API_TEST_URL, imports_dir=str(tmp_path),
              require_authenticated_user=True)
    auth_client = TestClient(create_app(cfg))
    resp = auth_client.get("/healthz")
    assert resp.status_code == 200


def test_staging_apply_requires_trusted_header_when_auth_enabled(tmp_path, test_db_url, conn):
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)
    conn.commit()
    cfg = Web(api_database_url=API_TEST_URL, imports_dir=str(tmp_path),
              require_authenticated_user=True)
    auth_client = TestClient(create_app(cfg))

    resp = auth_client.post(
        f"/staging/{ids['staged_doc']}/apply",
        data={"decision": "approve", "decided_by": "someone-typed-this-in", "edits": ""},
    )
    assert resp.status_code == 403
    assert conn.execute("SELECT count(*) AS n FROM job WHERE type='gate.apply'").fetchone()["n"] == 0


def test_staging_apply_uses_trusted_header_for_decided_by(tmp_path, test_db_url, conn):
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)
    conn.commit()
    cfg = Web(api_database_url=API_TEST_URL, imports_dir=str(tmp_path),
              require_authenticated_user=True)
    auth_client = TestClient(create_app(cfg))

    resp = auth_client.post(
        f"/staging/{ids['staged_doc']}/apply",
        data={"decision": "approve", "decided_by": "someone-typed-this-in", "edits": ""},
        headers={"X-Forwarded-User": "user:marta"},
    )
    assert resp.status_code == 200
    rows = _jobs(conn, f"apply:{ids['staged_doc']}:approve")
    assert rows[0]["payload"]["decided_by"] == "user:marta"   # header wins over the form field


# --------------------------------------------------------------------------- #
# closed-enum sync (Invariant 7)
# --------------------------------------------------------------------------- #
def test_job_type_lists_match_db_enum(conn):
    """JOB_TYPES / STAGE_BY_TYPE mirror the closed job_type enum by hand; the
    real enum (migrations 001 + 013) is the truth. A tag missing here is
    silently un-filterable on the status board (how report.weekly went missing
    after migration 013), so drift fails this test, not a user's filter."""
    from web.app import JOB_TYPES, STAGE_BY_TYPE

    enum_values = {r["v"] for r in conn.execute(
        "SELECT unnest(enum_range(NULL::job_type))::text AS v").fetchall()}
    assert set(JOB_TYPES) == enum_values
    assert set(STAGE_BY_TYPE) == enum_values


def test_priorities_match_the_db_enum(conn):
    """`PRIORITIES` is the other hand-maintained mirror on this page, and it is
    load-bearing: the ingest and upload forms refuse anything not in it with a
    422 (`web/app.py:2966`). A fourth priority added to `job_priority` would be
    enqueueable by the CLI and silently rejected by every form.

    Of the seven enums in this database, `job_type` and `job_priority` are the
    two with a Python list mirroring them; the rest appear only as SQL literals,
    where Postgres rejects a typo itself. Audited 2026-09-02.
    """
    from web.app import PRIORITIES

    enum_values = {r["v"] for r in conn.execute(
        "SELECT unnest(enum_range(NULL::job_priority))::text AS v").fetchall()}
    assert set(PRIORITIES) == enum_values


# --------------------------------------------------------------------------- #
# web document upload — POST /upload inserts one upload_inbox row (bytes +
# metadata) and enqueues upload.ingest; the worker (Task 3) does the rest.
# --------------------------------------------------------------------------- #
_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


def test_upload_valid_inserts_spool_and_enqueues(client, conn):
    resp = client.post(
        "/upload",
        files={"file": ("cert.pdf", _PDF, "application/pdf")},
        data={"catalogue": "LJ", "priority": "interactive"},
    )
    assert resp.status_code == 200

    spool = conn.execute(
        "SELECT id, filename, catalogue, target_group_id FROM upload_inbox").fetchall()
    assert len(spool) == 1
    assert spool[0]["filename"] == "cert.pdf"
    assert spool[0]["target_group_id"] is None

    job = conn.execute("SELECT payload, dedupe_key FROM job WHERE type='upload.ingest'").fetchone()
    assert job["payload"] == {"upload_id": spool[0]["id"]}
    assert job["dedupe_key"] == f"upload:{spool[0]['id']}"


def test_upload_with_target_group_is_recorded(client, conn):
    conn.execute("INSERT INTO item_group (group_id, canonical_manufacturer) VALUES (9, 'ACME')")
    conn.commit()
    resp = client.post(
        "/upload",
        files={"file": ("d.pdf", _PDF, "application/pdf")},
        data={"catalogue": "LJ", "priority": "interactive", "target_group_id": "9"},
    )
    assert resp.status_code == 200
    assert conn.execute(
        "SELECT target_group_id FROM upload_inbox").fetchone()["target_group_id"] == 9


def test_upload_rejects_non_pdf(client, conn):
    resp = client.post(
        "/upload",
        files={"file": ("notes.txt", b"hello", "text/plain")},
        data={"catalogue": "LJ", "priority": "interactive"},
    )
    assert resp.status_code == 422
    assert conn.execute("SELECT count(*) c FROM upload_inbox").fetchone()["c"] == 0


def test_upload_rejects_html_wearing_a_pdf_name(client, conn):
    """The name and the browser's content-type both say PDF and both lie.

    This is the bredent file that reached production in September 2026: a saved
    web page called `Declaration_of_Conformity.pdf`. The extension check above
    passes it; only the bytes catch it (`[gate-covers-only-fetch]`).
    """
    resp = client.post(
        "/upload",
        files={"file": ("Declaration_of_Conformity.pdf",
                        b"<!doctype html><html>bredent</html>", "application/pdf")},
        data={"catalogue": "LJ", "priority": "interactive"},
    )
    assert resp.status_code == 422
    assert conn.execute("SELECT count(*) c FROM upload_inbox").fetchone()["c"] == 0
    # The message has to tell the person what to do differently, not just refuse.
    body = resp.text.lower()
    assert "not a pdf" in body
    assert "download the file itself" in body


def test_upload_refusal_names_the_leading_bytes(client, conn):
    resp = client.post(
        "/upload",
        files={"file": ("x.pdf", b"<?xml version='1.0'?><rss/>", "application/pdf")},
        data={"catalogue": "LJ", "priority": "interactive"},
    )
    assert resp.status_code == 422
    # Jinja escapes it, which is correct: the bytes come from an uploaded file
    # and must not be able to inject markup into the page that refuses them.
    assert "&lt;?xml" in resp.text


def test_upload_accepts_a_pdf_with_no_leading_whitespace_games(client, conn):
    # The gate must not have narrowed what a real upload may look like.
    resp = client.post(
        "/upload",
        files={"file": ("real.pdf", _PDF, "application/pdf")},
        data={"catalogue": "LJ", "priority": "interactive"},
    )
    assert resp.status_code == 200
    assert conn.execute("SELECT count(*) c FROM upload_inbox").fetchone()["c"] == 1


def test_upload_from_dead_end_passes_manual_task_id_in_payload(client, conn):
    conn.execute("INSERT INTO item_group (group_id, canonical_manufacturer) VALUES (12, 'ACME')")
    tid = conn.execute(
        "INSERT INTO manual_task (kind, group_id, payload, status) "
        "VALUES ('discovery-dead-end', 12, '{}'::jsonb, 'open') RETURNING id"
    ).fetchone()["id"]
    conn.commit()

    resp = client.post(
        "/upload",
        files={"file": ("d.pdf", _PDF, "application/pdf")},
        data={"catalogue": "LJ", "priority": "interactive",
              "target_group_id": "12", "manual_task_id": str(tid)},
    )
    assert resp.status_code == 200
    job = conn.execute("SELECT payload FROM job WHERE type='upload.ingest'").fetchone()
    assert job["payload"]["manual_task_id"] == tid
    assert "upload_id" in job["payload"]


def test_upload_rejects_oversize(client, conn):
    big = b"%PDF" + b"0" * (26 * 1024 * 1024)   # > 25 MB default
    resp = client.post(
        "/upload",
        files={"file": ("big.pdf", big, "application/pdf")},
        data={"catalogue": "LJ", "priority": "interactive"},
    )
    assert resp.status_code == 422
    assert conn.execute("SELECT count(*) c FROM upload_inbox").fetchone()["c"] == 0


# --------------------------------------------------------------------------- #
# archive route (S1.8 Task 1): serves files under Web.archive_root, the
# durable volume-backed home for fetch.url's output (docs/architecture.md).
# --------------------------------------------------------------------------- #
def _archive_client(tmp_path):
    """TestClient whose archive root is a temp dir. Mirrors the module's
    `client` fixture, which injects Web(...) directly rather than via env."""
    root = tmp_path / "archive"
    root.mkdir(parents=True, exist_ok=True)
    cfg = Web(api_database_url=API_TEST_URL, imports_dir=str(tmp_path),
              archive_root=str(root))
    return TestClient(create_app(cfg)), root


def test_archive_route_serves_an_archived_file(test_db_url, tmp_path):
    client, root = _archive_client(tmp_path)
    (root / "VOCO" / "doc").mkdir(parents=True)
    (root / "VOCO" / "doc" / "abc__x.pdf").write_bytes(b"%PDF-1.4 hello")
    resp = client.get("/archive/VOCO/doc/abc__x.pdf")
    assert resp.status_code == 200
    assert resp.content == b"%PDF-1.4 hello"


def test_archive_route_rejects_path_traversal(test_db_url, tmp_path):
    # A literal `..` never reaches `archive_file` at all: httpx normalizes
    # dot-segments client-side before the request is sent, so `/archive/../
    # secret.txt` becomes a request for `/secret.txt` (confirmed via
    # resp.request.url) and 404s via Starlette's router on an unmatched path,
    # not via this route's own guard. A percent-encoded `..` (`%2e%2e`)
    # survives that client-side normalization and does reach the handler, so
    # this is the assertion that actually exercises
    # `target.is_relative_to(root)` in `archive_file`.
    client, root = _archive_client(tmp_path)
    (tmp_path / "secret.txt").write_text("do not serve me")
    resp = client.get("/archive/%2e%2e/secret.txt")
    assert resp.status_code == 404


def test_archive_route_literal_dotdot_is_normalized_before_it_arrives(test_db_url, tmp_path):
    # Companion to the test above: the plain `../` form a user might actually
    # type. Kept as a regression check on the overall endpoint behavior for
    # that input, but it does not exercise `archive_file`'s own guard (see the
    # comment above) - the percent-encoded test is the one that proves the
    # guard works.
    client, root = _archive_client(tmp_path)
    (tmp_path / "secret.txt").write_text("do not serve me")
    resp = client.get("/archive/../secret.txt")
    assert resp.status_code == 404


def test_archive_route_404s_on_an_encoded_null_byte(client):
    """[final-500s]. `%00` decodes to a real NUL in the path parameter, and
    `pathlib.resolve()` reaches os.stat, whose C boundary raises
    `ValueError: embedded null byte` -- before the containment check on the next
    line ever runs. Nothing caught it, so Starlette answered 500. Not a traversal
    bypass (the tests above cover that); an input class the handler never
    considered, and a malformed path deserves the same 404 a missing one gets."""
    resp = client.get("/archive/%00")
    assert resp.status_code == 404


def test_list_pages_reject_a_page_number_that_would_overflow_the_offset(client):
    """[final-500s]. `page` was an unbounded int multiplied into OFFSET. Python
    ints are arbitrary-precision, so a large enough one reaches Postgres as a
    value outside bigint and the query raises, uncaught, as a 500 -- on four
    public list pages, from a query string. `/expiry` was given this treatment on
    2026-08-10 after the same class of report; these had not been."""
    for path in ("/items", "/documents", "/data-quality", "/staging/docs"):
        resp = client.get(f"{path}?page=99999999999999999999")
        assert resp.status_code == 422, f"{path} answered {resp.status_code}"


def test_archive_route_404s_on_missing_file(test_db_url, tmp_path):
    client, _ = _archive_client(tmp_path)
    assert client.get("/archive/nope.pdf").status_code == 404


# --------------------------------------------------------------------------- #
# document file route: the UI's "file" links used to point at `archive_url`
# directly, which is a storage handle and not a URL — every one of them 404'd.
# The link is constructed here instead, and the roots it will serve from come
# from config so relocating the archive is an env change, not a code change.
# --------------------------------------------------------------------------- #
def _seed_doc_with_archive_url(conn, archive_url: str, content_hash: str) -> int:
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, coverage_scope, "
        "content_hash, archive_url, status) VALUES ('DoC','MDR','2024-01-01','group',"
        "%s,%s,'staged') RETURNING doc_id",
        (content_hash, archive_url),
    ).fetchone()["doc_id"]
    conn.commit()
    return doc_id


def test_document_file_route_serves_a_bare_absolute_path(test_db_url, tmp_path, conn):
    """The shape every backfilled corpus document carries: `archive_url` is an
    absolute host path under the imports mount, not a URL."""
    client, _ = _archive_client(tmp_path)
    pdf = tmp_path / "corpus.pdf"
    pdf.write_bytes(b"%PDF-1.4 corpus")
    doc_id = _seed_doc_with_archive_url(conn, str(pdf), "h-file-abs")

    resp = client.get(f"/documents/{doc_id}/file")
    assert resp.status_code == 200
    assert resp.content == b"%PDF-1.4 corpus"


def test_document_file_route_opens_inline_and_keeps_the_manufacturers_filename(
    test_db_url, tmp_path, conn
):
    """Two properties a reviewer depends on: clicking OPENS the PDF rather than
    downloading a copy every time, and the name that reaches them is the
    manufacturer's own filename (their identifier for the document), not
    something the browser invents because we sent no name."""
    client, _ = _archive_client(tmp_path)
    original = "gce_certification_MDR 778483.pdf"    # spaces and all
    pdf = tmp_path / original
    pdf.write_bytes(b"%PDF-1.4 named")
    doc_id = _seed_doc_with_archive_url(conn, str(pdf), "h-file-named")

    resp = client.get(f"/documents/{doc_id}/file")
    assert resp.status_code == 200
    disposition = resp.headers["content-disposition"]
    assert disposition.startswith("inline"), disposition
    # percent-encoded RFC 5987 form, because the name contains spaces
    assert "gce_certification_MDR%20778483.pdf" in disposition
    assert resp.headers["content-type"] == "application/pdf"


def test_download_name_strips_the_archives_hash_prefix(test_db_url, tmp_path, conn):
    """The store names files `{hash12}__{original}` to stay collision-free.
    That prefix is ours, not the manufacturer's — they identify documents BY
    the filename, so a reviewer must not be handed `a1b2c3__cert.pdf`."""
    import hashlib

    client, root = _archive_client(tmp_path)
    body = b"%PDF-1.4 archived"
    content_hash = hashlib.sha256(body).hexdigest()
    (root / "GC" / "cert").mkdir(parents=True)
    archived = root / "GC" / "cert" / f"{content_hash[:12]}__gce_certification_MDR_778483.pdf"
    archived.write_bytes(body)
    doc_id = _seed_doc_with_archive_url(conn, str(archived), content_hash)

    resp = client.get(f"/documents/{doc_id}/file")
    assert resp.status_code == 200
    disposition = resp.headers["content-disposition"]
    assert "gce_certification_MDR_778483.pdf" in disposition
    assert content_hash[:12] not in disposition


def test_download_name_leaves_an_unprefixed_name_alone(test_db_url):
    """Matched on the document's real hash, not a `^[0-9a-f]{12}__` pattern:
    a manufacturer's own filename may contain `__`, and guessing would
    silently truncate it."""
    from web.app import _download_name

    h = "a1b2c3d4e5f6" + "0" * 52
    assert _download_name(f"{h[:12]}__real name.pdf", h) == "real name.pdf"
    # legitimately contains __ but is not our prefix — untouched
    assert _download_name("EU__declaration.pdf", h) == "EU__declaration.pdf"
    # a different document's prefix is not ours to strip
    assert _download_name("ffffffffffff__x.pdf", h) == "ffffffffffff__x.pdf"
    # no hash to compare against -> pass through
    assert _download_name("plain.pdf", None) == "plain.pdf"
    # degenerate: prefix only, nothing after it -> keep what we have
    assert _download_name(f"{h[:12]}__", h) == f"{h[:12]}__"


def test_document_file_route_serves_a_file_uri(test_db_url, tmp_path, conn):
    """LocalFsStore's dev default returns a `file://` URI."""
    client, root = _archive_client(tmp_path)
    pdf = root / "fetched.pdf"
    pdf.write_bytes(b"%PDF-1.4 fetched")
    doc_id = _seed_doc_with_archive_url(conn, pdf.resolve().as_uri(), "h-file-uri")

    resp = client.get(f"/documents/{doc_id}/file")
    assert resp.status_code == 200
    assert resp.content == b"%PDF-1.4 fetched"


def test_document_file_route_redirects_when_the_archive_is_remote(test_db_url, tmp_path, conn):
    """With `storage.base_url` configured the bytes are already client
    reachable — hand over the real location instead of proxying them."""
    client, _ = _archive_client(tmp_path)
    doc_id = _seed_doc_with_archive_url(
        conn, "https://archive.example/doc/abc.pdf", "h-file-remote"
    )

    resp = client.get(f"/documents/{doc_id}/file", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "https://archive.example/doc/abc.pdf"


def test_document_file_route_refuses_a_path_outside_every_configured_root(
    test_db_url, tmp_path, conn
):
    """The containment check is what stops this route being an arbitrary-file
    reader: a document whose archive_url points anywhere else is a 404, even
    though the file exists and is readable."""
    client, _ = _archive_client(tmp_path)
    outside = tmp_path.parent / "outside-the-roots.pdf"
    outside.write_bytes(b"%PDF-1.4 secret")
    doc_id = _seed_doc_with_archive_url(conn, str(outside), "h-file-outside")

    assert client.get(f"/documents/{doc_id}/file").status_code == 404


def test_document_file_route_404s_on_unknown_doc_and_missing_file(
    test_db_url, tmp_path, conn
):
    client, _ = _archive_client(tmp_path)
    assert client.get("/documents/999999/file").status_code == 404
    gone = _seed_doc_with_archive_url(conn, str(tmp_path / "never-written.pdf"), "h-file-gone")
    assert client.get(f"/documents/{gone}/file").status_code == 404


def test_document_file_route_roots_come_from_config_not_code(test_db_url, tmp_path, conn):
    """Relocating the archive must be an env change. Same document, two apps:
    the one whose configured root contains the file serves it, the other 404s."""
    pdf = tmp_path / "relocated" / "doc.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4 relocated")
    doc_id = _seed_doc_with_archive_url(conn, str(pdf), "h-file-relocated")

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    wrong = TestClient(create_app(Web(
        api_database_url=API_TEST_URL, imports_dir=str(elsewhere),
        archive_root=str(elsewhere),
    )))
    right = TestClient(create_app(Web(
        api_database_url=API_TEST_URL, imports_dir=str(elsewhere),
        archive_root=str(pdf.parent),
    )))
    assert wrong.get(f"/documents/{doc_id}/file").status_code == 404
    assert right.get(f"/documents/{doc_id}/file").status_code == 200


def test_document_file_route_follows_a_configured_path_rewrite(
    test_db_url, tmp_path, conn
):
    """The deployment case: `archive_url` is the HOST path backfill recorded,
    and the container mounts those same bytes somewhere else. WEB_PATH_REWRITES
    declares the mapping, so relocating is an env change, not a code change."""
    mounted = tmp_path / "mounted-imports"
    (mounted / "GC" / "CE").mkdir(parents=True)
    pdf = mounted / "GC" / "CE" / "cert.pdf"
    pdf.write_bytes(b"%PDF-1.4 mounted")
    # what the DB holds: a path that does not exist on this machine at all
    doc_id = _seed_doc_with_archive_url(
        conn, "/host/somewhere/imports/GC/CE/cert.pdf", "h-file-rewrite"
    )

    client = TestClient(create_app(Web(
        api_database_url=API_TEST_URL, imports_dir=str(mounted),
        archive_root=str(tmp_path / "empty-archive"),
        path_rewrites=f"/host/somewhere/imports={mounted}",
    )))
    resp = client.get(f"/documents/{doc_id}/file")
    assert resp.status_code == 200
    assert resp.content == b"%PDF-1.4 mounted"


def test_document_file_route_finds_a_relocated_file_by_content_hash(
    test_db_url, tmp_path, conn
):
    """With no rewrite configured the route still resolves, by searching tails
    of the stored path under each root and verifying the sha256 against the
    registry — so the link works out of the box in the container."""
    import hashlib

    mounted = tmp_path / "imports"
    (mounted / "GC" / "CE").mkdir(parents=True)
    pdf = mounted / "GC" / "CE" / "cert.pdf"
    body = b"%PDF-1.4 relocated-by-hash"
    pdf.write_bytes(body)
    doc_id = _seed_doc_with_archive_url(
        conn, "/host/elsewhere/imports/GC/CE/cert.pdf", hashlib.sha256(body).hexdigest()
    )

    client = TestClient(create_app(Web(
        api_database_url=API_TEST_URL, imports_dir=str(mounted),
        archive_root=str(tmp_path / "empty"),
    )))
    resp = client.get(f"/documents/{doc_id}/file")
    assert resp.status_code == 200
    assert resp.content == body


def test_hash_verified_search_refuses_a_same_named_file_with_other_content(
    test_db_url, tmp_path, conn
):
    """The search matches on path tails, so a decoy sharing the tail is
    reachable — serving it would hand a reviewer the WRONG compliance
    document. The content hash is what authorises the read, so the decoy is
    refused even though its path matches perfectly."""
    mounted = tmp_path / "imports"
    (mounted / "GC" / "CE").mkdir(parents=True)
    (mounted / "GC" / "CE" / "cert.pdf").write_bytes(b"%PDF-1.4 A DIFFERENT DOCUMENT")
    doc_id = _seed_doc_with_archive_url(
        conn, "/host/elsewhere/imports/GC/CE/cert.pdf", "0" * 64,   # hash of neither
    )

    client = TestClient(create_app(Web(
        api_database_url=API_TEST_URL, imports_dir=str(mounted),
        archive_root=str(tmp_path / "empty"),
    )))
    assert client.get(f"/documents/{doc_id}/file").status_code == 404


def test_path_rewrite_spec_tolerates_typos(test_db_url):
    """A malformed env var must not take the UI down — the rewrite is an
    optimisation over the hash-verified search, never the only route to a file."""
    from web.app import _parse_path_rewrites

    assert _parse_path_rewrites("") == []
    assert _parse_path_rewrites("=/imports") == []       # no source
    assert _parse_path_rewrites("/host=") == []          # no destination
    assert _parse_path_rewrites("garbage-no-equals") == []
    assert _parse_path_rewrites("/a=/b, /c=/d") == [("/a", "/b"), ("/c", "/d")]


def test_registry_pages_link_to_the_file_route_not_the_raw_archive_url(
    test_db_url, tmp_path, conn
):
    """Regression guard on the original bug: `archive_url` must never be
    rendered into an href again."""
    client, _ = _archive_client(tmp_path)
    pdf = tmp_path / "linked.pdf"
    pdf.write_bytes(b"%PDF-1.4 linked")
    doc_id = _seed_doc_with_archive_url(conn, str(pdf), "h-file-linked")
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, catalogue, "
        "mirror_rev, updated_at) VALUES ('file-item','Linked product','008',true,'LJ',1,now()) "
        "ON CONFLICT (item_ref) DO NOTHING"
    )
    conn.execute(
        "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
        "VALUES ('file-item',%s,'ref-list','staged')", (doc_id,),
    )
    conn.commit()

    for url in (f"/documents/{doc_id}", "/items/file-item"):
        resp = client.get(url)
        assert resp.status_code == 200, url
        assert f'href="/documents/{doc_id}/file"' in resp.text, url
        assert f'href="{pdf}"' not in resp.text, url


# --------------------------------------------------------------------------- #
# document text route (S1.8 Task 2): plain-text view of what EXTRACT read out
# of the archived PDF (app/extract/text_store.py, migration 018).
# --------------------------------------------------------------------------- #
def test_document_text_route_renders_stored_text(client, conn):
    conn.execute(
        "INSERT INTO document_text (content_hash, source, engine, pages, chars, content) "
        "VALUES ('h-web','pdf-text','pymupdf',1,11,'[[page 1]] hi')"
    )
    conn.commit()
    resp = client.get("/documents/h-web/text")
    assert resp.status_code == 200
    assert "[[page 1]] hi" in resp.text


def test_document_text_route_404s_on_unknown_hash(client):
    assert client.get("/documents/nope/text").status_code == 404


# --------------------------------------------------------------------------- #
# data quality (S1.8 Task 3): standing anomaly ledger (app/results.py,
# migration 019) surfaced for BC/IT, grouped by kind and filterable.
# --------------------------------------------------------------------------- #
def test_data_quality_page_lists_anomalies_by_kind(client, conn):
    conn.execute(
        "INSERT INTO data_anomaly (kind, subject, catalogue, seen_count) VALUES "
        "('md_class_blank','A1','LJ',3), ('mfr_ref_missing','A2','LJ',1)"
    )
    conn.commit()
    resp = client.get("/data-quality")
    assert resp.status_code == 200
    assert "md_class_blank" in resp.text
    assert "A1" in resp.text


def test_data_quality_filters_by_kind(client, conn):
    conn.execute(
        "INSERT INTO data_anomaly (kind, subject, catalogue) VALUES "
        "('md_class_blank','B1','LJ'), ('mfr_ref_missing','B2','LJ')"
    )
    conn.commit()
    resp = client.get("/data-quality?kind=mfr_ref_missing")
    assert "B2" in resp.text
    assert "B1" not in resp.text


def test_data_quality_item_subject_is_a_link_and_carries_the_item_name(client, conn):
    """`subject` is an item_ref for 19.141 of the 19.147 rows in the live
    ledger. Printed bare it is a dead end: the reader cannot tell what the
    product is, and cannot get to the item without retyping the ref into
    another page's search box."""
    _seed_item(conn, "dq-item-1", "10170")
    conn.execute(
        "INSERT INTO data_anomaly (kind, subject, catalogue) "
        "VALUES ('md_class_blank','dq-item-1','LJ') "
        "ON CONFLICT (kind, subject) DO NOTHING"
    )
    conn.commit()

    resp = client.get("/data-quality", params={"kind": "md_class_blank"})
    assert resp.status_code == 200
    assert 'href="/items/dq-item-1"' in resp.text
    assert "item dq-item-1" in resp.text


def test_data_quality_hash_subject_links_to_the_document(client, conn):
    """`no_text_layer` is the one kind whose subject is a content hash, not an
    item — 6 rows live. It gets the document, not a broken item link."""
    doc_id = _seed_doc_with_archive_url(conn, "file:///dq.pdf", "dq-hash-1")
    conn.execute(
        "INSERT INTO data_anomaly (kind, subject) VALUES ('no_text_layer','dq-hash-1') "
        "ON CONFLICT (kind, subject) DO NOTHING"
    )
    conn.commit()

    resp = client.get("/data-quality", params={"kind": "no_text_layer"})
    assert resp.status_code == 200
    assert f'href="/documents/{doc_id}"' in resp.text


def _seed_class_check(conn, item_ref, bc_class, doc_class, *, on_column=False,
                      doc_status="production", link_status="production"):
    """One item with a BC class, one production document stating a class, linked.

    `on_column` chooses WHERE the document's class lives: `document.stated_class`
    (what GATE writes from now on) or only the latest extraction attempt (what
    the 768 historic documents have, because the backfill deliberately emits no
    `validate.doc` and so never re-runs GATE for them). The view must find both.
    """
    from psycopg.types.json import Json

    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, product_class, "
        "catalogue, updated_at) VALUES (%s,%s,'077',%s,'LJ',now()) "
        "ON CONFLICT (item_ref) DO UPDATE SET product_class = EXCLUDED.product_class",
        (item_ref, f"item {item_ref}", bc_class),
    )
    content_hash = f"cls-{item_ref}-{uuid.uuid4().hex[:10]}"
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, coverage_scope, "
        "content_hash, archive_url, status, stated_class) "
        "VALUES ('DoC','MDR','2024-01-01','group',%s,'/archive/c.pdf',%s::doc_status,%s) "
        "RETURNING doc_id",
        (content_hash, doc_status, doc_class if on_column else None),
    ).fetchone()["doc_id"]
    conn.execute(
        "INSERT INTO extraction_attempt (content_hash, tier, fields, extract_rev) "
        "VALUES (%s,'T0',%s,1)",
        (content_hash, Json({"stated_class": {
            "value": doc_class, "conf": 0.95, "tier": "T0",
            "verbatim": f"Class {doc_class}", "page": None}})),
    )
    conn.execute(
        "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
        "VALUES (%s,%s,'ref-list',%s::link_status)",
        (item_ref, doc_id, link_status),
    )
    conn.commit()
    return doc_id


def _verdicts(conn):
    return {r["item_ref"]: r["verdict"] for r in conn.execute(
        "SELECT item_ref, verdict FROM item_class_check").fetchall()}


def test_item_class_check_names_each_kind_of_disagreement(client, conn):
    """The four verdicts, on the four shapes that produce them. `agree-family`
    is the one that needs saying out loud: documents almost never print the
    class I subclasses (10 of 768 measured), so BC `Ir` against a document's
    `I` is the document being less specific, not the two disagreeing."""
    _seed_class_check(conn, "cls-family", "Ir", "I")
    _seed_class_check(conn, "cls-fill", None, "IIa")
    _seed_class_check(conn, "cls-conflict", "IIa", "I")
    _seed_class_check(conn, "cls-agree", "IIb", "IIb")

    got = _verdicts(conn)
    assert got["cls-family"] == "agree-family"
    assert got["cls-fill"] == "fillable"
    assert got["cls-conflict"] == "conflict"
    assert got["cls-agree"] == "agree"


def test_item_class_check_reads_a_historic_document_from_its_attempt(client, conn):
    """The 768 documents extracted before migration 032 carry the value only in
    `extraction_attempt` — the backfill appends a rev and emits no `validate.doc`,
    so GATE never re-runs and the column stays NULL. If the view read only the
    column, the whole point of the backfill would be invisible."""
    _seed_class_check(conn, "cls-historic", "IIa", "IIa", on_column=False)
    _seed_class_check(conn, "cls-current", "IIa", "IIa", on_column=True)

    assert _verdicts(conn) == {"cls-historic": "agree", "cls-current": "agree"}


def test_item_class_check_ignores_anything_not_in_production(client, conn):
    """Same visibility rule as every other consumer: a staged document or a
    staged link asserts nothing about an item, so it must not raise a conflict
    a reviewer would then chase."""
    _seed_class_check(conn, "cls-stagedoc", "IIa", "I", doc_status="staged")
    _seed_class_check(conn, "cls-stagelink", "IIa", "I", link_status="staged")

    assert _verdicts(conn) == {}


def test_data_quality_shows_the_device_class_section(client, conn):
    _seed_class_check(conn, "cls-web-conflict", "IIa", "I")
    _seed_class_check(conn, "cls-web-fill", None, "IIb")
    _seed_class_check(conn, "cls-web-agree", "IIb", "IIb")

    resp = client.get("/data-quality")

    assert resp.status_code == 200
    assert "Device class vs BC" in resp.text
    assert "conflict" in resp.text and "fillable" in resp.text
    # the two ACTIONABLE verdicts are listed by item; `agree` is a count only
    assert 'href="/items/cls-web-conflict"' in resp.text
    assert 'href="/items/cls-web-fill"' in resp.text
    assert 'href="/items/cls-web-agree"' not in resp.text


def test_data_quality_unresolvable_subject_stays_plain_text(client, conn):
    """A subject matching neither an item nor a document must not be dressed
    as a link — an href to a 404 is worse than plain text, because it claims
    there is something behind it."""
    conn.execute(
        "INSERT INTO data_anomaly (kind, subject) VALUES ('mfr_ref_prose','dq-orphan-1') "
        "ON CONFLICT (kind, subject) DO NOTHING"
    )
    conn.commit()

    resp = client.get("/data-quality", params={"kind": "mfr_ref_prose"})
    assert resp.status_code == 200
    assert "dq-orphan-1" in resp.text
    assert 'href="/items/dq-orphan-1"' not in resp.text
    assert "/documents/dq-orphan-1" not in resp.text


@pytest.fixture
def many_anomalies(conn):
    """Overflow one page of the anomaly ledger, then remove the rows.

    Its own kind, so the fixture cannot be perturbed by (or perturb) the other
    data-quality tests, which share `md_class_blank`.
    """
    kind = "dq_pager_probe"

    def _seed(n: int) -> str:
        for i in range(n):
            conn.execute(
                "INSERT INTO data_anomaly (kind, subject) VALUES (%s, %s) "
                "ON CONFLICT (kind, subject) DO NOTHING",
                (kind, f"dq-pager-{i:03d}"),
            )
        conn.commit()
        return kind

    yield _seed

    conn.execute("DELETE FROM data_anomaly WHERE kind = %s", (kind,))
    conn.commit()


def test_data_quality_paginates_instead_of_truncating_in_silence(client, many_anomalies):
    """The ledger rendered 500 rows of 19.147 and said nothing about the rest,
    which reads as "here is the list". Every row has to be reachable, and the
    count has to be on the page."""
    from web.app import DATA_QUALITY_PAGE_SIZE as SIZE

    n = SIZE + 10
    kind = many_anomalies(n)

    first = client.get("/data-quality", params={"kind": kind, "page": 0})
    second = client.get("/data-quality", params={"kind": kind, "page": 1})
    assert first.status_code == 200 and second.status_code == 200

    subs_1 = re.findall(r"dq-pager-(\d+)", first.text)
    subs_2 = re.findall(r"dq-pager-(\d+)", second.text)
    assert len(subs_1) == SIZE
    assert len(subs_2) == n - SIZE
    # last_seen ties are broken by id, so paging neither repeats nor drops rows
    assert not set(subs_1) & set(subs_2)
    assert len(set(subs_1) | set(subs_2)) == n

    assert f"1–{SIZE} of {n}" in first.text
    assert f"{SIZE + 1}–{n} of {n}" in second.text


def test_data_quality_pager_keeps_the_kind_filter(client, many_anomalies):
    """Paging out of the filter would silently widen the question the reader
    asked."""
    from web.app import DATA_QUALITY_PAGE_SIZE as SIZE

    kind = many_anomalies(SIZE + 10)
    resp = client.get("/data-quality", params={"kind": kind, "page": 0})
    assert f"/data-quality?kind={kind}&amp;page=1" in resp.text


# --------------------------------------------------------------------------- #
# registry visibility (S1.8 Task 6): browse items and documents, staged and
# production alike (app/CLAUDE.md's UI-scope rule was amended in this same
# task to cover this — see CLAUDE.md's "Don't gold-plate" line).
# --------------------------------------------------------------------------- #
def _seed_item_with_doc(conn, item_ref="X1", link_status="production",
                        doc_status="production", validity_to="2026-12-01",
                        manufacturer_raw="077"):
    # Two deviations from the brief's snippet, both required to make the
    # insert succeed against the actual schema/fixtures rather than the
    # brief's assumptions:
    #  1. item_mirror.updated_at is NOT NULL with no default (migration
    #     002/011 — only mirror_rev picked up a sequence default); every
    #     other seed helper in this repo supplies it explicitly (see
    #     test_reconcile.py, test_gate_apply_handler.py).
    #  2. document/item_mirror are NOT truncated between tests (conftest.py
    #     only resets job/domain_lease/scheduler_run/upload_inbox), and two
    #     tests below reuse item_ref "X1" — a content_hash of exactly
    #     ``'h-' || item_ref`` would collide with document_content_hash_key
    #     on the second call. A uuid suffix keeps every seeded document
    #     unique regardless of how many tests reuse the same item_ref.
    # `manufacturer_raw` defaults to the historical hardcoded "077" so every
    # existing call site is unaffected; it's overridable so a caller can seed
    # documents under a manufacturer_alias code it controls without colliding
    # with "077", which `_seed_grouped_expiring_doc` below also hardcodes.
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, mfr_ref, md_flag, "
        "catalogue, updated_at) VALUES (%s,'Widget',%s,'W-1',TRUE,'LJ',now()) "
        "ON CONFLICT (item_ref) DO NOTHING",
        (item_ref, manufacturer_raw),
    )
    content_hash = f"h-{item_ref}-{uuid.uuid4().hex[:12]}"
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, validity_to, status, "
        "content_hash, archive_url, coverage_scope) VALUES ('DoC','MDR','2024-01-01',"
        "%s,%s,%s,'/archive/x.pdf','group') RETURNING doc_id",
        (validity_to, doc_status, content_hash),
    ).fetchone()["doc_id"]
    conn.execute(
        "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
        "VALUES (%s,%s,'ref-list',%s)",
        (item_ref, doc_id, link_status),
    )
    conn.commit()
    return doc_id


def test_items_page_lists_items_with_document_counts(client, conn):
    _seed_item_with_doc(conn, "X1")
    resp = client.get("/items")
    assert resp.status_code == 200
    assert "X1" in resp.text


def test_items_page_links_the_manufacturer_to_its_page(client, conn):
    """The catalogue shows the canonical name now; `/manufacturers/<name>` is
    the page that owns it, and it is one click away or it is not there."""
    _seed_manufacturer(conn, "DENTAL WORLD", ["10170"])
    _seed_item(conn, "mfr-link-1", "10170")
    conn.commit()

    resp = client.get("/items", params={"q": "mfr-link-1"})
    assert resp.status_code == 200
    assert 'href="/manufacturers/DENTAL%20WORLD"' in resp.text

    detail = client.get("/items/mfr-link-1")
    assert 'href="/manufacturers/DENTAL%20WORLD"' in detail.text


def test_items_page_leaves_an_unmapped_manufacturer_code_unlinked(client, conn):
    """No alias means no canonical name and no manufacturer page: the BC code
    is all we have, and it stays plain text rather than linking to a 404."""
    _seed_item(conn, "mfr-link-2", "999999-no-alias")
    conn.commit()

    resp = client.get("/items", params={"q": "mfr-link-2"})
    assert resp.status_code == 200
    assert "999999-no-alias" in resp.text
    assert "/manufacturers/999999-no-alias" not in resp.text


def test_items_page_searches_by_item_ref(client, conn):
    _seed_item_with_doc(conn, "X1")
    _seed_item_with_doc(conn, "Y2")
    resp = client.get("/items?q=Y2")
    assert "Y2" in resp.text
    assert "X1" not in resp.text


def test_items_page_counts_superseded_documents_separately_from_no_coverage(client, conn):
    # A link promoted to production keeps that link status forever;
    # _apply_supersession (app/handlers/gate.py) only ever flips the
    # document's own status to 'superseded'. The old two-bucket count
    # (production: doc+link both production; staged: link staged) has no
    # bucket for doc.status='superseded' with link.status still
    # 'production' — that row used to collapse silently to 0/0, rendering
    # as if the item had no coverage at all. This reproduces exactly that
    # shape and checks the third, superseded, bucket now catches it.
    old_id = _seed_item_with_doc(conn, "X8")
    new_id = _seed_item_with_doc(conn, "X9")
    conn.execute(
        "UPDATE document SET superseded_by=%s, status='superseded' WHERE doc_id=%s",
        (new_id, old_id),
    )
    conn.commit()
    resp = client.get("/items?q=X8")
    assert resp.status_code == 200
    row = re.search(r"<tr>\s*<td><a href=\"/items/X8\">X8</a></td>.*?</tr>", resp.text, re.S)
    assert row, resp.text
    cells = re.findall(r"<td>(.*?)</td>", row.group(0), re.S)
    # item_ref, name, manufacturer, md_class, production_docs, staged_docs, superseded_docs, next_expiry
    assert len(cells) == 8, cells
    assert cells[4].strip() == "0"  # production_docs
    assert cells[5].strip() == "0"  # staged_docs
    assert cells[6].strip() == "1"  # superseded_docs — the row is not invisible


def test_item_detail_shows_the_linked_certificate_and_its_validity(client, conn):
    _seed_item_with_doc(conn, "X3", validity_to="2027-05-05")
    resp = client.get("/items/X3")
    assert resp.status_code == 200
    assert "2027-05-05" in resp.text
    # The type reads as the words, not the code: "DoC" is what is stored and
    # what the filter files by, never what an office reader is shown
    # (office UI redesign spec § 9, P7b).
    assert "Declaration of Conformity" in resp.text


def test_item_detail_shows_staged_links_distinctly_from_production(client, conn):
    _seed_item_with_doc(conn, "X4", link_status="staged", doc_status="staged")
    resp = client.get("/items/X4")
    # The bare word "staged" also appears in the page's static explanatory
    # <p class="hint">, which renders unconditionally regardless of row
    # content — assert on the actual differentiator instead: the row-staged
    # CSS class, which only appears when a row's document/link isn't fully
    # production, co-occurring with the staged badge text.
    assert "row-staged" in resp.text
    assert "badge-staged" in resp.text


def test_item_detail_does_not_mark_fully_production_rows_as_staged(client, conn):
    _seed_item_with_doc(conn, "SCRATCH3", link_status="production", doc_status="production")
    resp = client.get("/items/SCRATCH3")
    assert resp.status_code == 200
    assert "row-staged" not in resp.text
    assert "badge-staged" not in resp.text


def test_api_item_documents_reaches_an_item_ref_containing_a_slash(client, conn):
    # The HTML route and the JSON API must agree about which items exist; fixing
    # only the page would leave the API 404ing on the same ~12.5% of refs.
    _seed_item_with_doc(conn, "441/24")
    resp = client.get("/api/items/441/24/documents")
    assert resp.status_code == 200
    assert resp.json()["item_ref"] == "441/24"


def test_item_detail_reaches_an_item_ref_containing_a_slash(client, conn):
    # 1996 of 15958 live item_refs contain `/` ([item-link-slash-404]), and
    # Starlette's default path converter is `[^/]+`, so every one of them 404s
    # at the router before item_detail runs. Percent-encoding cannot rescue it:
    # uvicorn decodes `%2F` back to a literal slash before routing. Five
    # templates link here with the raw ref, so the route is the defect, not the
    # templates — same mechanism and same fix as
    # `/manufacturers/{canonical_name:path}` (2026-08-11).
    _seed_item_with_doc(conn, "440/24")
    resp = client.get("/items/440/24")
    assert resp.status_code == 200
    assert "440/24" in resp.text


def test_item_detail_404s_on_unknown_item(client):
    assert client.get("/items/NOPE").status_code == 404


def test_item_page_links_the_api_json(client, conn):
    """A human clicks this and sees exactly what a consumer receives -- the
    item page's one-click way to eyeball the JSON contract (docs/specs/read-api.md)."""
    item_ref = "X9"
    _seed_item_with_doc(conn, item_ref)
    html = client.get(f"/items/{item_ref}").text
    assert f"/api/items/{item_ref}/documents" in html


def _seed_group_member(conn, item_ref, canonical="ACME"):
    """Item resolved to a group -- item_group + item_group_member, no
    document. Distinct from _seed_item_with_doc (document/link only, no
    group) and _seed_item (item_mirror only): the rediscover route resolves
    item_ref -> group_id via item_group_member alone."""
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, catalogue, updated_at) "
        "VALUES (%s,'Widget','011','LJ',now()) ON CONFLICT (item_ref) DO NOTHING",
        (item_ref,),
    )
    group_id = conn.execute(
        "INSERT INTO item_group (canonical_manufacturer) VALUES (%s) RETURNING group_id",
        (canonical,),
    ).fetchone()["group_id"]
    conn.execute(
        "INSERT INTO item_group_member (group_id, item_ref, match_basis) VALUES (%s,%s,'udi')",
        (group_id, item_ref),
    )
    conn.commit()
    return group_id


def test_item_detail_shows_rediscover_button(client, conn):
    _seed_item_with_doc(conn, "RD-BTN-1")

    resp = client.get("/items/RD-BTN-1")

    assert resp.status_code == 200
    assert "/items/RD-BTN-1/rediscover" in resp.text
    assert "Re-discover documents" in resp.text


def test_item_rediscover_enqueues_discover_group_and_redirects(client, conn):
    g = _seed_group_member(conn, "RD-1")

    resp = client.post("/items/RD-1/rediscover", follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/items/RD-1?rediscover=queued"
    rows = conn.execute(
        "SELECT payload, dedupe_key, priority FROM job WHERE type='discover.group'"
    ).fetchall()
    assert len(rows) == 1
    today = conn.execute("SELECT current_date AS d").fetchone()["d"]
    assert rows[0]["payload"] == {"group_id": g, "ignore_recency": True}
    assert rows[0]["dedupe_key"] == f"discover:refetch:{g}:{today.isoformat()}"
    assert rows[0]["priority"] == "interactive"


def test_item_rediscover_no_group_enqueues_nothing(client, conn):
    _seed_item(conn, "RD-NOGROUP", "011")
    conn.commit()

    resp = client.post("/items/RD-NOGROUP/rediscover", follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/items/RD-NOGROUP?rediscover=no-group"
    assert conn.execute(
        "SELECT count(*) AS n FROM job WHERE type='discover.group'"
    ).fetchone()["n"] == 0

    page = client.get(resp.headers["location"])
    assert page.status_code == 200
    assert "no group" in page.text.lower()


def test_item_rediscover_same_day_second_post_dedupes(client, conn):
    _seed_group_member(conn, "RD-2")

    first = client.post("/items/RD-2/rediscover", follow_redirects=False)
    second = client.post("/items/RD-2/rediscover", follow_redirects=False)

    assert first.headers["location"] == "/items/RD-2?rediscover=queued"
    assert second.headers["location"] == "/items/RD-2?rediscover=deduped"
    assert conn.execute(
        "SELECT count(*) AS n FROM job WHERE type='discover.group'"
    ).fetchone()["n"] == 1

    page = client.get(second.headers["location"])
    assert "already queued today" in page.text.lower()


def _seed_item_with_doc_citing_cert(conn, item_ref, cert_validity_to,
                                    cert_status="production"):
    """Seed an item linked to a DoC with a null validity_to that cites a
    certificate (cert_doc_id) whose own validity_to is set — the shape Task 4
    exists for: MDR Annex IV requires no expiry on a DoC, the real renewal
    date lives on the notified-body certificate it cites.

    `cert_status` defaults to the historical hardcoded 'production' so every
    existing call site is unaffected; it is overridable so a caller can seed
    the rejected certificate [final-cert-status-join] is about."""
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, mfr_ref, md_flag, "
        "catalogue, updated_at) VALUES (%s,'Widget','077','W-1',TRUE,'LJ',now()) "
        "ON CONFLICT (item_ref) DO NOTHING",
        (item_ref,),
    )
    suffix = uuid.uuid4().hex[:12]
    cert_id = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, validity_to, status, "
        "content_hash, archive_url, coverage_scope) VALUES ('EC','MDR','2022-01-01',"
        "%s,%s,%s,'/archive/cert.pdf','group') RETURNING doc_id",
        (cert_validity_to, cert_status, f"h-cert-{suffix}"),
    ).fetchone()["doc_id"]
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, validity_to, status, "
        "content_hash, archive_url, coverage_scope, cert_doc_id) VALUES ('DoC','MDR',"
        "'2024-01-01',NULL,'production',%s,'/archive/doc.pdf','group',%s) RETURNING doc_id",
        (f"h-doc-{suffix}", cert_id),
    ).fetchone()["doc_id"]
    conn.execute(
        "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
        "VALUES (%s,%s,'ref-list','production')",
        (item_ref, doc_id),
    )
    conn.commit()
    return doc_id, cert_id


def test_item_detail_shows_cert_inherited_expiry_when_docs_own_date_is_null(client, conn):
    doc_id, cert_id = _seed_item_with_doc_citing_cert(conn, "X11", "2027-01-01")
    resp = client.get("/items/X11")
    assert resp.status_code == 200
    assert "2027-01-01" in resp.text
    assert f'href="/documents/{cert_id}"' in resp.text


def test_item_detail_does_not_inherit_a_rejected_certs_expiry(client, conn):
    # [final-cert-status-join]: a human rejected that certificate, so its date is
    # evidence of nothing. The forward path (validate._resolve_cited_certificate)
    # and document_effective_expiry both restrict the citation to
    # production/superseded; this page hand-rolled its own unfiltered join and
    # handed the date out regardless. What replaces it is not "-" but the DoC's
    # own five-year review horizon, which is what the view says is true.
    doc_id, cert_id = _seed_item_with_doc_citing_cert(
        conn, "XR1", "2027-03-03", cert_status="rejected"
    )
    resp = client.get("/items/XR1")
    assert resp.status_code == 200
    assert "2027-03-03" not in resp.text
    assert "2029-01-01" in resp.text          # validity_from 2024-01-01 + 5 years
    # The citation itself still renders: an auditor needs to see what was cited
    # and follow it to the rejected document. Only the DATE is withheld.
    assert f'href="/documents/{cert_id}"' in resp.text


def test_item_detail_and_items_board_agree_on_a_certless_docs_review_date(client, conn):
    # [final-documents-page-parity]: /items reads document_effective_expiry and
    # shows the five-year review horizon for a DoC carrying no date of its own;
    # /items/{ref} read the raw column and showed "-" for the same document.
    # Three pages, two answers - the parity this asserts away.
    _seed_item_with_doc(conn, "XS1", validity_to=None)
    board = client.get("/items?q=XS1")
    row = re.search(r'<tr>\s*<td><a href="/items/XS1">XS1</a></td>.*?</tr>', board.text, re.S)
    assert row, board.text
    assert "2029-01-01" in row.group(0)
    detail = client.get("/items/XS1")
    assert detail.status_code == 200
    assert "2029-01-01" in detail.text


def test_items_page_next_expiry_inherits_through_cited_certificate(client, conn):
    _seed_item_with_doc_citing_cert(conn, "X12", "2027-02-02")
    resp = client.get("/items?q=X12")
    assert resp.status_code == 200
    row = re.search(r"<tr>\s*<td><a href=\"/items/X12\">X12</a></td>.*?</tr>", resp.text, re.S)
    assert row, resp.text
    assert "2027-02-02" in row.group(0)


def _seed_item_with_docs(conn, item_ref, docs):
    """Seed one item linked (production) to each (type, regulation, validity_to)
    in `docs`. Every document and link is production, so the only thing that
    varies is the regime the document was issued under."""
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, mfr_ref, md_flag, "
        "catalogue, updated_at) VALUES (%s,'Widget','081','W-1',TRUE,'LJ',now()) "
        "ON CONFLICT (item_ref) DO NOTHING",
        (item_ref,),
    )
    for doc_type, regulation, validity_to in docs:
        suffix = uuid.uuid4().hex[:12]
        doc_id = conn.execute(
            "INSERT INTO document (type, regulation, validity_from, validity_to, status, "
            "content_hash, archive_url, coverage_scope) "
            "VALUES (%s,%s,'2024-01-01',%s,'production',%s,'/archive/d.pdf','manufacturer') "
            "RETURNING doc_id",
            (doc_type, regulation, validity_to, f"h-{suffix}"),
        ).fetchone()["doc_id"]
        conn.execute(
            "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
            "VALUES (%s,%s,'mfr-scope','production')",
            (item_ref, doc_id),
        )
    conn.commit()


def _items_row(text, item_ref):
    return re.search(
        rf"<tr>\s*<td><a href=\"/items/{item_ref}\">{item_ref}</a></td>.*?</tr>", text, re.S
    )


def test_items_board_does_not_take_its_horizon_from_a_quality_system_certificate(client, conn):
    """An ISO 13485 certificate expires, but not on any article's behalf.

    CARL MARTIN, 2026-08-21: one QMS certificate running to 2028-06-06 bound to
    2.567 items, and every one of them reported that date as its next expiry.
    Seeded here the sharp way round -- the certificate lapses BEFORE the
    declaration -- so `min()` cannot pass by accident."""
    _seed_item_with_docs(conn, "CM1", [
        ("ISO", "n.a.", "2026-01-01"),      # quality system, earlier
        ("DoC", "MDR", "2027-06-01"),       # the device evidence, later
    ])
    row = _items_row(client.get("/items?q=CM1").text, "CM1")
    assert row, "item row not rendered"
    assert "2027-06-01" in row.group(0)
    assert "2026-01-01" not in row.group(0)


def test_items_board_offers_no_horizon_when_only_non_device_paper_covers_the_item(client, conn):
    """No device document means no device horizon, and the honest answer is a
    dash. Showing the QMS certificate's date instead reads as "this article is
    covered until then", which is the claim nobody made."""
    _seed_item_with_docs(conn, "CM2", [("ISO", "n.a.", "2028-06-06")])
    row = _items_row(client.get("/items?q=CM2").text, "CM2")
    assert row, "item row not rendered"
    assert "2028-06-06" not in row.group(0)


def test_documents_page_shows_the_inherited_expiry_not_the_raw_column(client, conn):
    """[final-documents-page-parity]: /documents read `d.validity_to` raw with no
    join at all, so a declaration whose renewal date lives on the certificate it
    cites read "—" here and a real date on /items. Same document, two answers."""
    doc_id, cert_id, _ = _seed_doc_citing_cert(conn, cert_day_offset=20)
    cert_date = conn.execute(
        "SELECT validity_to FROM document WHERE doc_id=%s", (cert_id,)
    ).fetchone()["validity_to"].isoformat()

    resp = client.get("/documents")
    assert resp.status_code == 200
    row = re.search(rf'<tr>\s*<td><a href="/documents/{doc_id}">.*?</tr>', resp.text, re.S)
    assert row, resp.text
    assert cert_date in row.group(0)


def test_documents_page_does_not_inherit_a_rejected_certs_expiry(client, conn):
    """The same join that closes the parity gap must carry the status bar with
    it ([final-cert-status-join]) — which it does, because the view holds both."""
    doc_id, cert_id, _ = _seed_doc_citing_cert(
        conn, cert_day_offset=25, cert_status="rejected"
    )
    cert_date = conn.execute(
        "SELECT validity_to FROM document WHERE doc_id=%s", (cert_id,)
    ).fetchone()["validity_to"].isoformat()

    resp = client.get("/documents")
    row = re.search(rf'<tr>\s*<td><a href="/documents/{doc_id}">.*?</tr>', resp.text, re.S)
    assert row, resp.text
    assert cert_date not in row.group(0)
    assert "2029-01-01" in row.group(0)       # validity_from 2024-01-01 + 5 years


def test_documents_page_does_not_count_rejected_links_as_coverage(client, conn):
    """[final-documents-page-parity], remaining half. "Items covered" counted
    every link that was not retracted, so a rejected link -- a human saying this
    document does NOT cover that item -- was indistinguishable from a production
    one. Measured 2026-08-19: 13 of the 147 documents carrying links have zero
    production links, so the number was wrong on 9% of them. /items has split
    production from staged since it was built; this column had not."""
    doc_id = _seed_item_with_doc(conn, "COV1", link_status="production",
                                 doc_status="production")
    for ref in ("COV2", "COV3"):
        conn.execute(
            "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, mfr_ref, md_flag, "
            "catalogue, updated_at) VALUES (%s,'Widget','077','W-1',TRUE,'LJ',now()) "
            "ON CONFLICT (item_ref) DO NOTHING",
            (ref,),
        )
        conn.execute(
            "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
            "VALUES (%s,%s,'ref-list','rejected')",
            (ref, doc_id),
        )
    conn.commit()

    resp = client.get("/documents")
    row = re.search(rf'<tr>\s*<td><a href="/documents/{doc_id}">.*?</tr>', resp.text, re.S)
    assert row, resp.text
    covered = re.findall(r"<td>(.*?)</td>", row.group(0), re.S)[-1]
    assert "3" not in covered, covered      # what the unsplit count said
    assert "1" in covered, covered          # the production links, alone


def test_documents_page_lists_documents_with_item_counts(client, conn):
    doc_id = _seed_item_with_doc(conn, "X5")
    resp = client.get("/documents")
    assert resp.status_code == 200
    # "DoC" alone is satisfied by the type-filter <option>DoC</option>, which
    # renders regardless of whether any row matched — assert on something
    # only the table body emits: the row's own doc_id link, and that row's
    # item_count cell (last column) showing the one item seeded.
    assert f'href="/documents/{doc_id}"' in resp.text
    row = re.search(rf'<tr>\s*<td><a href="/documents/{doc_id}">.*?</tr>', resp.text, re.S)
    assert row, resp.text
    cells = re.findall(r"<td>(.*?)</td>", row.group(0), re.S)
    assert cells[-1].strip() == "1"  # item_count


def test_documents_page_states_the_total_and_pages_rather_than_truncating(
    client, conn, monkeypatch
):
    """The list was a bare `LIMIT 200` with no total and no controls: at 314
    documents it rendered 200 of them and said nothing about the rest, which
    answers "how many documents do we hold?" with a number it made up.

    The page size is monkeypatched rather than out-seeded: proving the contract
    needs one row more than a page, not 200 rows of it, and `document` is not
    truncated between tests here — 200 rows would land on every later suite
    that counts them.
    """
    monkeypatch.setattr("web.app.DOCUMENTS_PAGE_SIZE", 2)
    _seed_item_with_doc(conn, "PG1", manufacturer_raw="PGCO")
    _seed_item_with_doc(conn, "PG2", manufacturer_raw="PGCO")
    _seed_item_with_doc(conn, "PG3", manufacturer_raw="PGCO")
    total = conn.execute("SELECT count(*) AS n FROM document").fetchone()["n"]

    first = client.get("/documents")
    assert f"of {total}" in first.text
    assert "page=1" in first.text

    second = client.get("/documents?page=1")
    assert second.status_code == 200
    # a real offset, not the same rows re-rendered
    ids = lambda t: set(re.findall(r'href="/documents/(\d+)"', t))
    assert len(ids(first.text)) == 2
    assert ids(first.text) & ids(second.text) == set()


def test_documents_pagination_carries_the_filter_into_the_next_page(
    client, conn, monkeypatch
):
    """A Next that drops the filter silently widens the question the reader
    asked, and page two would answer a different one."""
    monkeypatch.setattr("web.app.DOCUMENTS_PAGE_SIZE", 1)
    _seed_item_with_doc(conn, "PGF1", doc_status="staged", link_status="staged",
                        manufacturer_raw="PGCO")
    _seed_item_with_doc(conn, "PGF2", doc_status="staged", link_status="staged",
                        manufacturer_raw="PGCO")

    resp = client.get("/documents?status=staged&type=DoC")
    assert resp.status_code == 200
    total = conn.execute(
        "SELECT count(*) AS n FROM document WHERE status='staged' AND type='DoC'"
    ).fetchone()["n"]
    assert f"of {total}" in resp.text
    pager_links = re.findall(r'href="/documents\?([^"]+)"', resp.text)
    assert pager_links, resp.text
    for link in pager_links:
        assert "status=staged" in link and "type=DoC" in link


def test_every_page_carries_a_favicon_link_so_nothing_requests_a_404(client):
    """`/favicon.ico` 404'd on every page load — the only console error in the
    app. An explicit icon link is also what stops the request."""
    text = client.get("/documents").text
    assert re.search(r'<link rel="icon" href="/static/img/favicon\.svg\?v=\d+"', text)
    assert client.get("/static/img/favicon.svg").status_code == 200


def test_search_finds_an_item_a_document_and_a_manufacturer_from_one_box(client, conn):
    """A cert number was findable only from /documents, an item ref only from
    /items: answering "what do we know about this?" meant knowing which screen
    owns the answer before asking."""
    from tests.fixtures import seed_ui

    seed_ui.seed_item(conn, "SRCH-ITEM-1", name="Searchable Composite", manufacturer_raw="SRCHCO")
    _seed_manufacturer(conn, "SRCHCO", ["SRCHCO"])
    doc_id = seed_ui.seed_document(
        conn, content_hash="h-srch-1", archive_url="/archive/h-srch-1.pdf",
        cert_number="SRCH 909 R00", status="production",
    )
    conn.commit()

    by_item = client.get("/search", params={"q": "SRCH-ITEM-1"})
    assert by_item.status_code == 200
    assert 'href="/items/SRCH-ITEM-1"' in by_item.text

    by_name = client.get("/search", params={"q": "Searchable Composite"})
    assert 'href="/items/SRCH-ITEM-1"' in by_name.text

    by_cert = client.get("/search", params={"q": "SRCH 909"})
    assert f'href="/documents/{doc_id}"' in by_cert.text

    by_mfr = client.get("/search", params={"q": "SRCHCO"})
    assert "SRCHCO" in by_mfr.text

    # a bare number is also read as a document id
    by_id = client.get("/search", params={"q": str(doc_id)})
    assert f'href="/documents/{doc_id}"' in by_id.text

    # and the box itself is on every page, not only this one
    assert 'action="/search"' in client.get("/items").text

    conn.execute("DELETE FROM evidence WHERE doc_id=%s", (doc_id,))
    conn.execute("DELETE FROM document WHERE doc_id=%s", (doc_id,))
    conn.execute("DELETE FROM item_mirror WHERE item_ref='SRCH-ITEM-1'")
    conn.execute("DELETE FROM manufacturer_alias WHERE canonical_name='SRCHCO'")
    conn.commit()


def test_empty_search_asks_rather_than_listing_the_whole_registry(client):
    resp = client.get("/search")
    assert resp.status_code == 200
    assert 'name="q"' in resp.text
    assert "<h2>Items" not in resp.text


def test_ingest_page_reports_what_previous_runs_did(client, conn):
    """The form enqueued a job and handed back an id; nothing said what earlier
    runs had done. The result envelope was stored the whole time (job.result,
    migration 019) and never read."""
    from app import queue

    jid = queue.enqueue(conn, "backfill.scan", {"catalogue": "LJ"}, "run-history-probe")
    queue.finish(conn, jid, {"scanned": 173, "archived": 12, "skipped": 4})
    conn.commit()

    text = client.get("/ingest").text
    assert "Recent runs" in text
    assert f'href="/jobs/{jid}"' in text
    # the handler's own numbers, not an interpretation of them
    assert "&#34;scanned&#34;: 173" in text or '"scanned": 173' in text


def test_static_assets_are_versioned_so_a_deploy_reaches_the_browser(client):
    """StaticFiles sends ETag/Last-Modified but no Cache-Control, so a browser
    may serve the previous file without revalidating — and did: a corrected
    stylesheet sat on the server for hours while every page kept rendering the
    old one, which makes a shipped fix indistinguishable from a broken fix.

    The version is the asset's own mtime, so it changes exactly when the file
    does and a rebuilt image cannot be answered from any existing cache.
    """
    text = client.get("/documents").text
    match = re.search(r'href="/static/css/style\.css\?v=(\d+)"', text)
    assert match, text[:400]
    assert int(match.group(1)) > 0
    # every asset the page links, not just the stylesheet
    assert re.search(r'src="/static/htmx\.min\.js\?v=\d+"', text)
    assert re.search(r'src="/static/img/dentalia-logo\.png\?v=\d+"', text)
    # and the versioned URL must actually serve the file
    assert client.get(match.group(0).split('"')[1]).status_code == 200


def test_documents_filter_offers_the_filed_disposition(client):
    """`filed` (migration 025) is the largest status in the live registry and
    was missing from the dropdown, reachable only by typing a query string."""
    assert '<option value="filed"' in client.get("/documents").text


def test_items_page_states_the_total_matching_the_search(client, conn):
    _seed_item_with_doc(conn, "PGI1", manufacturer_raw="PGCO")
    resp = client.get("/items?q=PGI1")
    assert resp.status_code == 200
    assert "1–1 of 1" in resp.text
    # the old control inferred "there is more" from a full page and could offer
    # a Next onto an empty one; with a real total there is nothing to offer here
    assert "page=1" not in resp.text


def test_document_detail_shows_covered_items_and_supersession(client, conn):
    old_id = _seed_item_with_doc(conn, "X6")
    new_id = _seed_item_with_doc(conn, "X7")
    conn.execute("UPDATE document SET superseded_by=%s, status='superseded' "
                 "WHERE doc_id=%s", (new_id, old_id))
    conn.commit()
    resp = client.get(f"/documents/{old_id}")
    assert resp.status_code == 200
    assert "X6" in resp.text
    assert str(new_id) in resp.text


def test_document_detail_states_where_the_file_came_from_and_when(client, conn):
    """`source_url` and `created_at` have existed since migration 005 and were
    never rendered: the page could say what a document claims but not where the
    file came from or when we took it, which is the first thing an auditor asks
    and the one thing a content hash cannot answer."""
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, coverage_scope, "
        "content_hash, archive_url, source_url, status) "
        "VALUES ('DoC','MDR','2024-01-01','group','h-prov-1','/archive/h-prov-1.pdf',"
        "'https://example.test/certs/doc.pdf','production') RETURNING doc_id"
    ).fetchone()["doc_id"]
    conn.execute(
        "INSERT INTO evidence (doc_id, field, value, archive_url, page, verbatim, tier, "
        "                      model_id, confidence, extracted_at) "
        "VALUES (%s,'manufacturer','Provenance Dental AG','/archive/h-prov-1.pdf',2,"
        "        'Legal manufacturer Provenance Dental AG','T1','claude-haiku-4-5',0.9,now())",
        (doc_id,),
    )
    conn.commit()

    text = client.get(f"/documents/{doc_id}").text
    assert "https://example.test/certs/doc.pdf" in text
    assert "First recorded" in text
    # read off the document, not the catalogue — a manufacturer-scope document
    # has no item link to derive it from
    assert "Provenance Dental AG" in text


def test_document_detail_says_plainly_when_there_is_no_source_url(client, conn):
    """Blank would read as missing data; the page says which it is."""
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, coverage_scope, "
        "content_hash, archive_url, status) "
        "VALUES ('DoC','MDR','2024-01-01','group','h-prov-2','/archive/h-prov-2.pdf',"
        "'production') RETURNING doc_id"
    ).fetchone()["doc_id"]
    conn.commit()
    text = client.get(f"/documents/{doc_id}").text
    assert "no source recorded" in text


def test_document_detail_does_not_link_a_corpus_path_as_a_web_address(client, conn):
    """Most documents here came from the supplier's SFTP drop, so `source_url`
    is a filesystem path. An href on that resolves against THIS origin and
    404s — the same mistake every `archive_url` link in this UI used to make."""
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, coverage_scope, "
        "content_hash, archive_url, source_url, status) "
        "VALUES ('DoC','MDR','2024-01-01','group','h-prov-3','/archive/h-prov-3.pdf',"
        "'/imports/dentalia-sftp/STRAUMANN/CE in DOC/izjava.pdf','production') "
        "RETURNING doc_id"
    ).fetchone()["doc_id"]
    conn.commit()

    text = client.get(f"/documents/{doc_id}").text
    assert "/imports/dentalia-sftp/STRAUMANN" in text
    assert 'href="/imports/dentalia-sftp' not in text
    assert "corpus path, not a web address" in text


def test_document_detail_404s_on_unknown_doc(client):
    assert client.get("/documents/999999").status_code == 404


def test_document_routes_each_resolve_to_their_own_handler(client, conn):
    """/documents/{doc_id:int} (this task) and Task 2's
    /documents/{content_hash}/text are structurally non-overlapping
    regardless of declaration order — /documents/<int> is two path
    segments, /documents/<hash>/text is three — so this is not an
    ordering proof, just a sanity check both resolve correctly using a
    purely-numeric-looking content_hash as the sharpest case."""
    conn.execute(
        "INSERT INTO document_text (content_hash, source, engine, pages, chars, content) "
        "VALUES ('123456','pdf-text','pymupdf',1,2,'numeric-hash-ok')"
    )
    conn.commit()
    resp = client.get("/documents/123456/text")
    assert resp.status_code == 200
    assert "numeric-hash-ok" in resp.text
    # No document with doc_id=123456 exists, so the int route 404s cleanly —
    # confirms it isn't shadowed by the text route, not that ordering matters.
    assert client.get("/documents/123456").status_code == 404


def test_document_id_route_rejects_non_numeric_id_with_404(client):
    """The `:int` path converter's actual load-bearing job: a non-numeric
    doc_id 404s cleanly here. Without the converter, FastAPI would still
    coerce the `doc_id: int` parameter via Pydantic, but a non-numeric
    segment would then fail validation with a 422, not a clean 404."""
    resp = client.get("/documents/not-a-number")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# expiry board (S1.8 Task 7): what is about to lapse, grouped by manufacturer
# — the operational front end of the renewal loop. Dates below are always
# seeded as `current_date + <offset>` (never a hardcoded literal date), and
# assertions anchor on a doc_id or a distinctive cert_number rather than a
# rendered date string, so the suite does not silently rot as the calendar
# moves past a fixed date.
# --------------------------------------------------------------------------- #
def _seed_grouped_expiring_doc(conn, *, manufacturer, day_offset, doc_status="production",
                                link_status="production", doc_type="EC"):
    """A document linked to a real item_group (so manufacturer grouping is
    actually exercised, not the 'unknown' fallback), with validity_to =
    current_date + day_offset and its own distinctive cert_number."""
    item_ref = f"EXP-{uuid.uuid4().hex[:10]}"
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, mfr_ref, md_flag, "
        "catalogue, updated_at) VALUES (%s,'Widget','077','W-1',TRUE,'LJ',now())",
        (item_ref,),
    )
    group_id = conn.execute(
        "INSERT INTO item_group (canonical_manufacturer) VALUES (%s) RETURNING group_id",
        (manufacturer,),
    ).fetchone()["group_id"]
    conn.execute(
        "INSERT INTO item_group_member (group_id, item_ref, match_basis) VALUES (%s,%s,'manual')",
        (group_id, item_ref),
    )
    cert_number = f"CERT-{uuid.uuid4().hex[:10]}"
    content_hash = f"h-expiry-{uuid.uuid4().hex[:12]}"
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, validity_to, status, "
        "content_hash, archive_url, coverage_scope, cert_number) VALUES "
        "(%s,'MDR','2022-01-01', current_date + %s, %s, %s, '/archive/exp.pdf', 'group', %s) "
        "RETURNING doc_id",
        (doc_type, day_offset, doc_status, content_hash, cert_number),
    ).fetchone()["doc_id"]
    conn.execute(
        "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
        "VALUES (%s,%s,'ref-list',%s)",
        (item_ref, doc_id, link_status),
    )
    conn.commit()
    return doc_id, cert_number


def test_expiry_board_groups_by_manufacturer(client, conn):
    manufacturer = f"ExpMfr-{uuid.uuid4().hex[:8]}"
    doc_id, cert_number = _seed_grouped_expiring_doc(conn, manufacturer=manufacturer, day_offset=10)
    resp = client.get("/expiry?days=90")
    assert resp.status_code == 200
    assert cert_number in resp.text
    assert f'href="/documents/{doc_id}"' in resp.text
    # Not just present anywhere on the page — actually grouped under this
    # manufacturer's own heading, which is what makes the board answer "who
    # do I email" rather than just "what expires".
    assert resp.text.index(manufacturer) < resp.text.index(cert_number)


def test_expiry_board_excludes_documents_outside_the_horizon(client, conn):
    manufacturer = f"ExpFar-{uuid.uuid4().hex[:8]}"
    _doc_id, cert_number = _seed_grouped_expiring_doc(conn, manufacturer=manufacturer, day_offset=3650)
    resp = client.get("/expiry?days=30")
    assert resp.status_code == 200
    assert cert_number not in resp.text


def test_the_gap_button_says_when_it_has_already_asked(client, conn):
    """The press enqueues; the handler declines a second ask. Without an
    outcome on the redirect the page came back identical and the button read
    as broken."""
    # A real gap, seeded through the tables the view is built on: the route
    # refuses with 400 when there is nothing to ask for, and that check
    # deliberately comes first.
    from tests.test_email_request_handler import _seed_gap

    _seed_gap(conn, name="GapSaid", srn="DE-MF-GAPSAID", basic_udi_di="++GAPSAID",
              item_refs=("GS1", "GS2"))
    conn.execute(
        "INSERT INTO email_draft (kind, manufacturer, to_addrs, subject, body, status) "
        "VALUES ('gap-request','GapSaid','{}','s','b','cancelled')")
    conn.commit()

    resp = client.post("/manufacturers/GapSaid/gap-request", follow_redirects=False)
    assert resp.status_code == 303
    assert "gap=asked:" in resp.headers["location"]


def test_the_drafts_board_names_the_supplier_of_a_gap_request(client, conn):
    """Migration 057. The board joins `renewal_request` for this column and a
    gap request has none, so the cell was empty for exactly the drafts a person
    most needs to identify."""
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('GapNamed')")
    conn.execute(
        "INSERT INTO email_draft (kind, manufacturer, to_addrs, subject, body, status) "
        "VALUES ('gap-request','GapNamed','{}','subj','body','draft')")
    conn.commit()

    assert "GapNamed" in client.get("/drafts").text


def _seed_covered_item(conn, ref, *, doc_type=None, regulation=None, mfr="CovMfr"):
    """One device article, optionally with one production document on it."""
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, "
        "catalogue, updated_at) VALUES (%s,%s,%s,TRUE,'LJ',now())",
        (ref, f"widget {ref}", mfr))
    if doc_type is None:
        return None
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, status, content_hash, archive_url, "
        "  coverage_scope) VALUES (%s,%s,'production',%s,'/a.pdf','group') "
        "RETURNING doc_id",
        (doc_type, regulation, f"h-cov-{ref}"),
    ).fetchone()["doc_id"]
    conn.execute(
        "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
        "VALUES (%s,%s,'ref-list','production')", (ref, doc_id))
    return doc_id


def test_reports_page_lists_written_reports_newest_first(client, tmp_path, monkeypatch):
    """The weekly report has always existed and lived in `job.result`, where
    last week's was readable and the week before's was not."""
    (tmp_path / "report-2026-W34.html").write_text("<h1>34</h1>")
    (tmp_path / "report-2026-W36.html").write_text("<h1>36</h1>")
    import web.app as wa
    cfg = wa.load_config()
    monkeypatch.setattr(
        wa, "load_config",
        lambda: cfg.__class__(**{**cfg.__dict__,
                                 "web": type(cfg.web)(**{**cfg.web.__dict__,
                                                         "reports_dir": str(tmp_path)})}))

    body = client.get("/reports").text
    assert body.index("2026-W36") < body.index("2026-W34")

    assert "<h1>36</h1>" in client.get("/reports/report-2026-W36.html").text


def test_a_report_name_is_a_name_not_a_path(client, tmp_path, monkeypatch):
    """`/archive/{path}` is served by path and is staff-only for that reason.
    This route takes a NAME, matched against the one shape the writer produces,
    so it can never become a second file-serving surface."""
    import web.app as wa
    cfg = wa.load_config()
    monkeypatch.setattr(
        wa, "load_config",
        lambda: cfg.__class__(**{**cfg.__dict__,
                                 "web": type(cfg.web)(**{**cfg.web.__dict__,
                                                         "reports_dir": str(tmp_path)})}))
    for bad in ("../../etc/passwd", "report-x.html/../../etc/passwd", "notes.txt"):
        assert client.get(f"/reports/{bad}").status_code == 404


def test_reports_page_says_so_when_nothing_is_configured(client):
    """A machine told nowhere to write reports must say that, not render an
    empty list that reads as "no reports exist"."""
    body = client.get("/reports").text
    assert "No report location is configured" in body or "No report has been written" in body


def test_coverage_counts_the_three_gaps_apart(client, conn):
    """"No declaration" means one thing by document TYPE and another by the
    REGULATION it was issued under. W11 exists because the board publishes a
    percentage and neither list."""
    _seed_covered_item(conn, "COV-NONE")                                  # nothing
    _seed_covered_item(conn, "COV-ISO", doc_type="ISO", regulation="n.a.")  # QMS only
    _seed_covered_item(conn, "COV-EC", doc_type="EC", regulation="MDR")     # regime, no DoC
    _seed_covered_item(conn, "COV-FULL", doc_type="DoC", regulation="MDR")  # covered
    conn.commit()

    body = client.get("/coverage?gap=none").text
    assert "COV-NONE" in body and "COV-ISO" not in body

    doc_gap = client.get("/coverage?gap=doc").text
    for ref in ("COV-NONE", "COV-ISO", "COV-EC"):
        assert ref in doc_gap
    assert "COV-FULL" not in doc_gap

    regime = client.get("/coverage?gap=regime").text
    assert "COV-ISO" in regime          # a QMS certificate is not device evidence
    assert "COV-EC" not in regime       # an EC certificate under MDR is


def test_coverage_names_the_unclassified_rather_than_folding_them_in(client, conn):
    """11.693 articles carry no BC device class. Counting them as gaps would
    invent work; hiding them would hide the size of the real question."""
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, "
        "catalogue, updated_at) VALUES ('COV-UNK','unknown','x',NULL,'LJ',now())")
    conn.commit()

    body = client.get("/coverage").text
    assert "no device class" in body
    assert "COV-UNK" not in body


def test_coverage_shows_what_an_article_does_hold(client, conn):
    """"No DoC" reads very differently when the article holds an EC certificate
    than when it holds nothing at all."""
    _seed_covered_item(conn, "COV-EC2", doc_type="EC", regulation="MDR")
    conn.commit()

    row = client.get("/coverage?gap=doc").text
    assert "COV-EC2" in row
    assert "EC" in row


def test_coverage_falls_back_on_an_unknown_gap(client, conn):
    resp = client.get("/coverage?gap=nonsense")
    assert resp.status_code == 200
    assert "No Declaration of Conformity" in resp.text


def test_audit_page_shows_a_decision_with_who_and_when(client, conn):
    """The trail was always written and only psql could read it. W10."""
    # `job_snapshot` is NOT NULL: invariant 10 enforced in the schema, not by
    # convention -- an audit row that could not be read without its job would
    # not be an audit row.
    conn.execute(
        "INSERT INTO audit_log (event, doc_id, item_ref, decided_by, detail, job_snapshot) "
        "VALUES ('approve', NULL, %s, %s, %s, '{}')",
        ("AUD-1", "user:tester", '{"note": "looked at the pdf"}'),
    )
    conn.commit()

    body = client.get("/audit").text
    assert "user:tester" in body
    assert "approve" in body


def test_audit_page_filters_by_event_and_by_who(client, conn):
    conn.execute(
        "INSERT INTO audit_log (event, item_ref, decided_by, job_snapshot) VALUES "
        "('approve', 'AUD-A', 'user:alice', '{}'), "
        "('reject', 'AUD-B', 'user:bob', '{}')"
    )
    conn.commit()

    only_reject = client.get("/audit?event=reject").text
    assert "AUD-B" in only_reject
    assert "AUD-A" not in only_reject

    only_alice = client.get("/audit?q=alice").text
    assert "AUD-A" in only_alice
    assert "AUD-B" not in only_alice


def test_audit_row_survives_the_job_it_came_from(client, conn):
    """Invariant 10: `via_job` is a soft reference. The row must still render
    when the job is gone — the link may 404, the page may not."""
    conn.execute(
        "INSERT INTO audit_log (event, item_ref, decided_by, via_job, job_snapshot) "
        "VALUES ('filed', 'AUD-ORPHAN', 'worker', 999999999, '{}')"
    )
    conn.commit()

    resp = client.get("/audit")
    assert resp.status_code == 200
    assert "AUD-ORPHAN" in resp.text


def test_discovery_separates_never_searched_from_searched_and_empty(client, conn):
    """The three states are different facts about our own diligence, and W12
    exists because nothing showed them. A group with no discovery_log row is
    "never looked"; one whose every attempt missed is "looked, found
    nothing" — and conflating them makes a coverage number unreadable."""
    never = conn.execute(
        "INSERT INTO item_group (canonical_manufacturer) VALUES ('DiscNever') "
        "RETURNING group_id").fetchone()["group_id"]
    empty = conn.execute(
        "INSERT INTO item_group (canonical_manufacturer) VALUES ('DiscEmpty') "
        "RETURNING group_id").fetchone()["group_id"]
    hit = conn.execute(
        "INSERT INTO item_group (canonical_manufacturer) VALUES ('DiscHit') "
        "RETURNING group_id").fetchone()["group_id"]
    conn.execute(
        "INSERT INTO discovery_log (group_id, source, outcome) VALUES "
        "(%s,'playbook','miss'), (%s,'search','miss'), (%s,'playbook','hit')",
        (empty, empty, hit),
    )
    conn.commit()

    assert "DiscNever" in client.get("/discovery?state=never").text
    empty_page = client.get("/discovery?state=empty").text
    assert "DiscEmpty" in empty_page
    assert "DiscNever" not in empty_page
    found_page = client.get("/discovery?state=found").text
    assert "DiscHit" in found_page
    assert "DiscEmpty" not in found_page


def test_discovery_renders_the_item_count_not_a_dict_method(client, conn):
    """`{{ r.items }}` on a psycopg row resolves dict.items and renders
    "<built-in method items of dict>" into the page. Caught on the live site,
    not by a status-code assertion — which is the point of this test."""
    gid = conn.execute(
        "INSERT INTO item_group (canonical_manufacturer) VALUES ('DiscCount') "
        "RETURNING group_id").fetchone()["group_id"]
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, "
        "catalogue, updated_at) VALUES ('DISC-1','Widget','x',TRUE,'LJ',now())")
    conn.execute(
        "INSERT INTO item_group_member (group_id, item_ref, match_basis) "
        "VALUES (%s,'DISC-1','manual')", (gid,))
    conn.commit()

    body = client.get("/discovery?state=never").text
    assert "built-in method" not in body
    assert "DiscCount" in body


#: Column names that are also `dict` methods. Jinja resolves `row.name` as an
#: attribute first, so on a psycopg row -- which is a dict -- any of these
#: renders the bound method instead of the value.
_SHADOWED_BY_DICT = (
    "items", "keys", "values", "get", "copy", "update", "pop", "clear",
    "setdefault",
)


def test_no_template_reads_a_row_column_that_dict_shadows():
    """The rendered-page assertion above only covers pages a test opens.

    This one reads the templates instead, so it covers every page including
    the ones no test renders. It has caught two real occurrences:
    `discovery.html` (2026-09-03, found on the live site) and
    `playbook_detail.html`, where the BC-code picker printed
    "<built-in method items of dict object at 0x...>" 343 times in place of
    each code's item count, and the plural test `c.items == 1` compared a
    method to an integer and so was never true.

    A trailing `(` is allowed: `d.get(k)` and `d.items()` are calls, and a
    call is what the method is for.
    """
    import re
    from pathlib import Path

    attr = re.compile(
        r"\b[A-Za-z_][A-Za-z0-9_]*\.(?:" + "|".join(_SHADOWED_BY_DICT) + r")\b(?!\s*\()"
    )
    expr = re.compile(r"\{\{(.*?)\}\}|\{%(.*?)%\}", re.S)

    offenders = []
    root = Path(__file__).resolve().parent.parent / "web" / "templates"
    for path in sorted(root.rglob("*.html")):
        text = path.read_text()
        for block in expr.finditer(text):
            for hit in attr.finditer(block.group(1) or block.group(2) or ""):
                line = text[: block.start()].count("\n") + 1
                offenders.append(f"{path.name}:{line} {hit.group(0)}")

    assert not offenders, (
        "template reads a column that dict shadows; subscript it instead "
        "(row['items'], not row.items): " + ", ".join(offenders)
    )


def test_discovery_defaults_to_never_searched_on_a_bad_state(client, conn):
    """An unknown ?state= falls back rather than 500s or renders an empty
    table that reads as "nothing to do"."""
    resp = client.get("/discovery?state=nonsense")
    assert resp.status_code == 200
    assert "Never searched" in resp.text


def test_eudamed_digest_lists_a_never_swept_manufacturer_as_never_swept(client, conn):
    """`None` is a fact, not a zero. A manufacturer due for its first sweep has
    to read "never swept" rather than "no gaps" -- the same distinction ruling
    24 protects inside the per-manufacturer card."""
    from web.registry import eudamed_digest

    name = f"EuNever-{uuid.uuid4().hex[:8]}"
    # `eudamed_sweep_state.canonical_name` is an FK onto `manufacturer`: a
    # sweep can only ever be due for an entity the registry knows.
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES (%s)", (name,))
    conn.execute(
        "INSERT INTO eudamed_sweep_state (canonical_name, due_at) "
        "VALUES (%s, now())", (name,),
    )
    conn.commit()

    row = next(r for r in eudamed_digest(conn) if r["canonical_name"] == name)
    assert row["last_swept_at"] is None
    assert row["missing"] == 0

    resp = client.get("/manufacturers/eudamed")
    assert resp.status_code == 200
    assert name in resp.text
    # "sweep" became "EUDAMED check" on screen (§ 9); the absence it marks is
    # the same fact it always was.
    assert "never checked" in resp.text


def test_eudamed_digest_renders_on_empty_database(client):
    """A fresh machine has swept nothing. The page must say so rather than 500
    on an empty union."""
    resp = client.get("/manufacturers/eudamed")
    assert resp.status_code == 200
    assert "EUDAMED" in resp.text


def test_pulse_expiring_counts_the_two_windows_the_board_treats_as_work(client, conn):
    """The strip's `expiring` number is `len(recent) + len(soon)` on /expiry.

    Both run `_LAPSING_CTE` under `_LAPSING_GROUP_BY`, so this asserts the
    shared definition actually holds rather than that two similar queries
    happen to agree today. Four documents, one per bucket: lapsed inside the
    back window, lapsing inside the ahead window, lapsed long ago, and far
    future. Only the first two are work."""
    mfr = f"ExpPulse-{uuid.uuid4().hex[:8]}"
    _seed_grouped_expiring_doc(conn, manufacturer=mfr, day_offset=-10)     # recent
    _seed_grouped_expiring_doc(conn, manufacturer=mfr, day_offset=10)      # soon
    _seed_grouped_expiring_doc(conn, manufacturer=mfr, day_offset=-3000)   # long expired
    _seed_grouped_expiring_doc(conn, manufacturer=mfr, day_offset=3000)    # beyond
    conn.commit()

    from web.app import _pulse_counts
    assert _pulse_counts(conn)["expiring"] == 2


def test_pulse_expiring_collapses_a_certificate_cited_many_times(client, conn):
    """One row per lapsing THING, not per document -- the live board's 62
    IVOCLAR declarations behind one certificate. The counter must collapse
    them the same way, or the strip says 62 and the page shows 1."""
    mfr = f"ExpCollapse-{uuid.uuid4().hex[:8]}"
    _seed_grouped_expiring_doc(conn, manufacturer=mfr, day_offset=20)
    _seed_grouped_expiring_doc(conn, manufacturer=mfr, day_offset=20)
    conn.commit()

    from web.app import _pulse_counts
    # Two documents, two distinct cert_numbers -> two things. Same manufacturer
    # and same date is NOT enough to collapse, and must not be.
    assert _pulse_counts(conn)["expiring"] == 2


def test_expiry_board_rejects_an_out_of_range_horizon(client, conn):
    """The window reaches Postgres as `current_date + %s`, and `date + bigint`
    has no operator, so an unbounded value crashed the page with
    UndefinedFunction instead of returning a board. FastAPI must reject it at
    the boundary.

    The single `days` horizon became `back`/`ahead` when the board split into
    already-expired and about-to-expire (2026-08-18); the bound is the reason
    this test exists and it applies to both."""
    manufacturer = f"ExpRange-{uuid.uuid4().hex[:8]}"
    _doc_id, cert_number = _seed_grouped_expiring_doc(conn, manufacturer=manufacturer, day_offset=10)
    for param in ("back", "ahead"):
        assert client.get(f"/expiry?{param}=3000000000").status_code == 422
        assert client.get(f"/expiry?{param}=-1").status_code == 422
    ok = client.get("/expiry?back=3650&ahead=3650")
    assert ok.status_code == 200
    assert cert_number in ok.text


def test_expiry_board_excludes_staged_documents(client, conn):
    manufacturer = f"ExpStaged-{uuid.uuid4().hex[:8]}"
    _doc_id, cert_number = _seed_grouped_expiring_doc(
        conn, manufacturer=manufacturer, day_offset=10, doc_status="staged", link_status="staged",
    )
    resp = client.get("/expiry?days=90")
    assert resp.status_code == 200
    assert cert_number not in resp.text


def test_expiry_board_excludes_superseded_documents(client, conn):
    manufacturer = f"ExpSup-{uuid.uuid4().hex[:8]}"
    doc_id, cert_number = _seed_grouped_expiring_doc(conn, manufacturer=manufacturer, day_offset=10)
    conn.execute("UPDATE document SET status='superseded' WHERE doc_id=%s", (doc_id,))
    conn.commit()
    resp = client.get("/expiry?days=90")
    assert resp.status_code == 200
    assert cert_number not in resp.text


def _seed_doc_citing_cert(conn, *, cert_day_offset, cert_status="production"):
    """A production DoC with a null validity_to that cites a certificate
    (cert_doc_id) whose own validity_to is set — the Task 4 inheritance case.
    MDR Annex IV requires only an issue date on a DoC; the real renewal date
    lives on the notified-body certificate it names, capped at five years by
    Article 56.

    `cert_status` defaults to 'production' so every existing call site is
    unaffected; overriding it seeds the rejected certificate that must not
    lend its date out ([final-cert-status-join])."""
    suffix = uuid.uuid4().hex[:12]
    cert_number = f"CERT-{uuid.uuid4().hex[:10]}"
    cert_id = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, validity_to, status, "
        "content_hash, archive_url, coverage_scope, cert_number) VALUES "
        "('EC','MDR','2022-01-01', current_date + %s, %s, %s, "
        "'/archive/cert.pdf', 'group', %s) RETURNING doc_id",
        (cert_day_offset, cert_status, f"h-cert-{suffix}", cert_number),
    ).fetchone()["doc_id"]
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, validity_to, status, "
        "content_hash, archive_url, coverage_scope, cert_doc_id) VALUES "
        "('DoC','MDR','2024-01-01', NULL, 'production', %s, '/archive/doc.pdf', 'group', %s) "
        "RETURNING doc_id",
        (f"h-doc-{suffix}", cert_id),
    ).fetchone()["doc_id"]
    conn.commit()
    return doc_id, cert_id, cert_number


def test_expiry_board_collapses_one_certificate_into_one_row(client, conn):
    """62 rows on the live board were a single IVOCLAR certificate repeated
    across the 62 declarations citing it. That is one renewal and one email,
    and listing it 62 times hides both the count and any second certificate
    among the copies."""
    from tests.fixtures import seed_ui

    seed_ui.seed_item(conn, "exp-collapse-1", manufacturer_raw="EXPCO", md_flag=True)
    doc_ids = []
    for i in range(3):
        doc_ids.append(seed_ui.seed_document(
            conn, content_hash=f"h-expc-{i}", archive_url=f"/archive/h-expc-{i}.pdf",
            doc_type="DoC", regulation="MDR", status="production",
            validity_to="2026-09-01", cert_number="EXP 001 SAME",
        ))
    for d in doc_ids:
        seed_ui.seed_link(conn, "exp-collapse-1", d, match_basis="ref-list", status="production")
    conn.commit()

    text = client.get("/expiry?days=3650").text
    rows = [r for r in re.findall(r"<tr[^>]*>.*?</tr>", text, re.S) if "EXP 001 SAME" in r]
    assert len(rows) == 1, f"expected one collapsed row, got {len(rows)}"
    cells = re.findall(r"<td[^>]*>(.*?)</td>", rows[0], re.S)
    # the three documents are stated as a count, and the item that loses
    # coverage is counted once however many documents carry the date
    assert "3" in cells, cells
    assert "1" in cells, cells
    try:
        conn.execute("DELETE FROM item_document WHERE item_ref='exp-collapse-1'")
        conn.execute("DELETE FROM evidence WHERE doc_id = ANY(%s)", (doc_ids,))
        conn.execute("DELETE FROM document WHERE doc_id = ANY(%s)", (doc_ids,))
        conn.execute("DELETE FROM item_mirror WHERE item_ref='exp-collapse-1'")
        conn.commit()
    except Exception:  # pragma: no cover - cleanup must not mask a failure
        conn.rollback()
        raise


def test_expiry_board_flags_doc_expiry_inherited_from_cited_certificate(client, conn):
    doc_id, cert_id, cert_number = _seed_doc_citing_cert(conn, cert_day_offset=15)
    resp = client.get("/expiry?days=90")
    assert resp.status_code == 200
    # Split into individual rows first, then pick out the one that carries
    # each doc_id's link — a lazy `<tr>.*?href=...` pattern searched from
    # scratch would match starting at the FIRST `<tr>` in the whole page
    # (the table header) and swallow every row before the target one.
    # `<tr[^>]*>`, not `<tr>`: rows inside 30 days carry class="row-urgent"
    # since the board started sorting by urgency (2026-08-17), and a literal
    # `<tr>` silently skips exactly the rows the board most wants read.
    all_rows = re.findall(r"<tr[^>]*>.*?</tr>", resp.text, re.S)
    doc_row = next((r for r in all_rows if f'href="/documents/{doc_id}"' in r), None)
    assert doc_row, resp.text
    # The DoC's own cert_number is null (that's the whole point of citing a
    # certificate instead of carrying its own), so this row must carry the
    # inherited marker rather than the literal word "inherited" anywhere on
    # the page — the intro copy (Step 5) says that word unconditionally.
    assert "badge-inherited" in doc_row
    assert cert_number not in doc_row
    # The cited certificate is itself a production doc with its own
    # validity_to inside the horizon, so it also renders its own row — which
    # must NOT be flagged inherited, proving the flag actually distinguishes
    # the two rather than being on for every row.
    cert_row = next((r for r in all_rows if f'href="/documents/{cert_id}"' in r), None)
    assert cert_row, resp.text
    assert "badge-inherited" not in cert_row
    assert cert_number in cert_row


# --------------------------------------------------------------------------- #
# in-flight view (S1.8 Task 7): documents currently moving through the
# pipeline, counted by distinct content_hash rather than by job row — the
# status board answers "how many jobs", this answers "how many documents".
# job/domain_lease/scheduler_run/upload_inbox are truncated per test
# (conftest.py), so plain dedupe keys are safe to reuse across these tests.
# --------------------------------------------------------------------------- #
def test_inflight_counts_distinct_content_hashes_not_job_rows(client, conn):
    conn.execute(
        "INSERT INTO job (type, payload, dedupe_key, status, priority) VALUES "
        "('extract.doc','{\"content_hash\":\"h-a\"}'::jsonb,'k1','pending','sweep'), "
        "('validate.doc','{\"content_hash\":\"h-a\"}'::jsonb,'k2','pending','sweep'), "
        "('extract.doc','{\"content_hash\":\"h-b\"}'::jsonb,'k3','running','sweep')"
    )
    conn.commit()
    resp = client.get("/inflight")
    assert resp.status_code == 200
    # Total: 2 distinct content_hash values (h-a, h-b) across the 3 job rows
    # above — a `data-` attribute on the headline number, not a bare digit
    # assertion (the digit 2 also appears in nav markup, ids and CSS).
    assert 'data-total-documents="2"' in resp.text
    # Per-stage row: extract.doc spans h-a and h-b (2 documents, 2 jobs);
    # validate.doc spans only h-a (1 document, 1 job) — pairs the stage name
    # with its own counts rather than trusting an isolated digit. Rows are
    # split first, then filtered by stage name — a lazy `<tr>.*?stage.*?</tr>`
    # searched from scratch matches starting at the first `<tr>` in the page
    # (the table header) and swallows every row before the target one.
    all_rows = re.findall(r"<tr>.*?</tr>", resp.text, re.S)
    extract_row = next((r for r in all_rows if "extract.doc" in r), None)
    assert extract_row, resp.text
    cells = re.findall(r"<td>(.*?)</td>", extract_row, re.S)
    assert cells == ["extract.doc", "2", "2"], cells
    validate_row = next((r for r in all_rows if "validate.doc" in r), None)
    assert validate_row, resp.text
    cells = re.findall(r"<td>(.*?)</td>", validate_row, re.S)
    assert cells == ["validate.doc", "1", "1"], cells


def test_inflight_ignores_jobs_with_no_content_hash_in_payload(client, conn):
    conn.execute(
        "INSERT INTO job (type, payload, dedupe_key, status, priority) VALUES "
        "('resolve.group','{\"group_id\":1}'::jsonb,'k1','pending','sweep')"
    )
    conn.commit()
    resp = client.get("/inflight")
    assert resp.status_code == 200
    assert 'data-total-documents="0"' in resp.text
    assert "resolve.group" not in resp.text


# --------------------------------------------------------------------------- #
# registry views: manufacturers
# --------------------------------------------------------------------------- #
def _seed_manufacturer(conn, canonical: str, codes: list[str], source: str = "vendor-master"):
    for code in codes:
        conn.execute(
            "INSERT INTO manufacturer_alias (raw_name, canonical_name, source) "
            "VALUES (%s, %s, %s) ON CONFLICT (raw_name) DO NOTHING",
            (code, canonical, source),
        )


def _seed_item(conn, item_ref: str, manufacturer_raw: str, catalogue: str = "LJ"):
    # DO UPDATE, not DO NOTHING (finding 5, 2026-08-11 review): item_mirror
    # isn't truncated between tests in this file, and "LJ-1" is seeded by two
    # different tests. DO NOTHING made that agreement invisible — they happen
    # to pass the same manufacturer_raw today, but if either test ever
    # changes it, the conflict clause would silently keep the other test's
    # value instead of erroring, surfacing later as a confusing count
    # assertion far from the actual mismatch.
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, catalogue, updated_at) "
        "VALUES (%s, %s, %s, %s, now()) ON CONFLICT (item_ref) "
        "DO UPDATE SET manufacturer_raw = EXCLUDED.manufacturer_raw",
        (item_ref, f"item {item_ref}", manufacturer_raw, catalogue),
    )


@pytest.fixture
def playbooks_dir(monkeypatch, tmp_path):
    """Point `app.playbooks.PLAYBOOKS_DIR` at an empty, test-owned directory.

    `web/app.py` registers `/manufacturers` with `playbooks_dir=None`, which
    falls through to the real, git-committed `playbooks/` — and that directory
    contains `ivoclar.json` (IVOCLAR, 001/005/275) and `voco.json` (VOCO), the
    exact names these tests seed. Without this fixture every HTTP-driven
    manufacturers test silently reads production playbook coverage instead of
    anything the test controls, leaving `no_playbook=1` and the playbook-gap
    half of the sort order completely untested (fix-round-1 finding).
    """
    monkeypatch.setattr(playbooks_mod, "PLAYBOOKS_DIR", tmp_path)
    return tmp_path


def _write_playbook(dir_path, slug: str, manufacturer: str, codes: list[str] = ()):
    """Minimal valid playbook JSON — just enough for `app.playbooks._parse`
    (only `manufacturer` is required; `bc_codes` covers the code-based match
    in `registry.annotate_playbooks`)."""
    (dir_path / f"{slug}.json").write_text(json.dumps({
        "manufacturer": manufacturer,
        "bc_codes": [{"catalogue": "LJ", "code": c} for c in codes],
    }))


def test_manufacturers_groups_multi_code_entity_into_one_row(client, conn, playbooks_dir):
    _seed_manufacturer(conn, "IVOCLAR", ["001", "005", "275"])
    conn.commit()

    resp = client.get("/manufacturers")
    assert resp.status_code == 200
    assert resp.text.count(">IVOCLAR<") == 1
    for code in ("001", "005", "275"):
        assert code in resp.text


def test_manufacturers_counts_items_per_entity(client, conn):
    # Exact-equality on `codes`, so this test depends on the registry being empty
    # at entry — `connect_test`'s teardown truncates manufacturer_alias (and the
    # rest of the registry) after every test in this file, which is what makes
    # that true regardless of what the prior test seeded.
    _seed_manufacturer(conn, "IVOCLAR", ["001", "005"])
    _seed_manufacturer(conn, "VOCO", ["042"])
    _seed_item(conn, "LJ-1", "001")
    _seed_item(conn, "LJ-2", "005")
    _seed_item(conn, "LJ-3", "042")
    conn.commit()

    rows = {r["canonical_name"]: r for r in registry.manufacturer_rows(conn)}
    assert rows["IVOCLAR"]["items"] == 2
    assert rows["VOCO"]["items"] == 1
    assert sorted(rows["IVOCLAR"]["codes"]) == ["001", "005"]


def test_manufacturers_renders_when_no_manufacturers_exist(client, conn, playbooks_dir):
    # Renamed from test_manufacturers_renders_on_empty_database, which did not
    # test what its name claimed: it ran after a prior test's IVOCLAR/VOCO rows
    # were already committed and left in place (manufacturer_alias isn't
    # truncated between tests — see the comment above), and its only assertion
    # was that "Manufacturers" appears, which the static page header guarantees
    # regardless of what's in the database. Truncate first so this genuinely
    # exercises the zero-manufacturers render path, and assert the actual
    # empty-state copy the template renders rather than an always-true string.
    conn.execute("TRUNCATE manufacturer_alias")
    conn.commit()
    resp = client.get("/manufacturers")
    assert resp.status_code == 200
    assert "No manufacturers match this filter." in resp.text


def test_manufacturers_shows_zero_item_entity_instead_of_filtering_it_out(client, conn, playbooks_dir):
    """The LEFT JOINs in `_MANUFACTURER_ROWS_SQL` exist so an entity with no
    items still renders with a 0 rather than disappearing — the actual steady
    state today (item_mirror has real rows, item_document_production has
    none). Never exercised by a test until fix-round-1."""
    _seed_manufacturer(conn, "ZEROCO", ["801"])
    conn.commit()

    resp = client.get("/manufacturers")
    assert resp.status_code == 200
    row = re.search(
        r'<tr>\s*<td><a href="/manufacturers/ZEROCO">ZEROCO</a></td>.*?</tr>',
        resp.text, re.S,
    )
    assert row, resp.text
    cells = re.findall(r"<td>(.*?)</td>", row.group(0), re.S)
    # Entity, BC codes, Items, Production docs, Alias source, Playbook
    assert cells[2].strip() == "0", cells


def test_manufacturers_no_playbook_filter_excludes_covered_entities(client, conn, playbooks_dir):
    """The whole point of `?no_playbook=1` is to isolate triage targets — an
    entity a playbook already claims must drop out, and one that doesn't must
    stay. Previously untestable: with the real playbooks/ directory in play,
    every seeded IVOCLAR/VOCO row already had a playbook_slug for reasons
    unrelated to this test."""
    _seed_manufacturer(conn, "COVEREDCO", ["802"])
    _seed_manufacturer(conn, "GAPCO", ["803"])
    conn.commit()
    _write_playbook(playbooks_dir, "covered-co", "COVEREDCO", codes=["802"])

    resp = client.get("/manufacturers?no_playbook=1")
    assert resp.status_code == 200
    assert ">GAPCO<" in resp.text
    assert ">COVEREDCO<" not in resp.text

    # Sanity check the playbook actually attached absent the filter — otherwise
    # this test would pass even if annotate_playbooks silently matched nothing.
    unfiltered = client.get("/manufacturers")
    assert ">COVEREDCO<" in unfiltered.text


def test_manufacturers_no_playbook_filter_points_at_the_onboarding_queue(client, conn):
    """`?no_playbook=1` and /onboarding answer the same question, and the filter
    is the weaker of the two -- a flat list, no ordering by what the gap costs,
    no memory of entities already triaged out. It should hand off to the queue
    rather than compete with it (`[no-playbook-filter-competes-with-the-queue]`,
    UI audit 2026-09-04).

    Conditional on purpose: the pointer belongs to somebody who has ASKED the
    question, not on every visit to /manufacturers, where it would be one more
    line of chrome above a list they came to read.
    """
    _seed_manufacturer(conn, "GAPCO", ["804"])
    conn.commit()

    # Assert on the hint's own words, not on `href="/onboarding"`: the sidebar
    # carries that link on EVERY page, so the loose form passes for the wrong
    # reason on the positive case and can never pass on the negative one.
    hint = "To work through these rather than just list them"

    filtered = client.get("/manufacturers?no_playbook=1")
    assert filtered.status_code == 200
    assert hint in filtered.text
    assert 'href="/onboarding">Onboard a supplier</a>' in filtered.text

    unfiltered = client.get("/manufacturers")
    assert unfiltered.status_code == 200
    assert hint not in unfiltered.text


def test_manufacturers_sorts_playbook_gap_before_larger_covered_entity(client, conn, playbooks_dir):
    """Triage order (`filter_and_sort`'s sort key) puts ANY playbook-less
    entity ahead of ANY playbook-covered one, regardless of item count — the
    whole reason the sort key is a tuple and not a bare `-items`. A covered
    entity with more items must not bury an uncovered one. Previously
    untestable for the same reason as the no_playbook test above."""
    _seed_manufacturer(conn, "BIGCOVERED", ["911"])
    _seed_manufacturer(conn, "SMALLGAP", ["912"])
    _seed_item(conn, "BC-1", "911")
    _seed_item(conn, "BC-2", "911")
    _seed_item(conn, "BC-3", "911")
    _seed_item(conn, "SG-1", "912")
    conn.commit()
    _write_playbook(playbooks_dir, "big-covered", "BIGCOVERED", codes=["911"])

    resp = client.get("/manufacturers")
    assert resp.status_code == 200
    order = re.findall(r'/manufacturers/([A-Za-z0-9]+)">\1</a>', resp.text)
    ours = [name for name in order if name in ("BIGCOVERED", "SMALLGAP")]
    assert ours == ["SMALLGAP", "BIGCOVERED"], order


def test_manufacturers_search_matches_name_or_code(client, conn, playbooks_dir):
    _seed_manufacturer(conn, "IVOCLAR", ["001"])
    _seed_manufacturer(conn, "VOCO", ["042"])
    conn.commit()

    by_name = client.get("/manufacturers?q=ivocl")
    assert "IVOCLAR" in by_name.text and "VOCO" not in by_name.text

    by_code = client.get("/manufacturers?q=042")
    # Bare "IVOCLAR" also occurs in the page's static explanatory <p class="hint">
    # (renders unconditionally, regardless of filter results) — assert on the
    # row-rendered form instead, same fix as test_item_detail_shows_staged_links_
    # distinctly_from_production uses for the identical class of false positive.
    assert ">VOCO<" in by_code.text and ">IVOCLAR<" not in by_code.text


def test_manufacturers_search_treats_wildcards_literally(client, conn, playbooks_dir):
    _seed_manufacturer(conn, "IVOCLAR", ["001"])
    conn.commit()
    resp = client.get("/manufacturers?q=%25")
    # See the row-rendered-form note above — the static hint always contains
    # the bare word "IVOCLAR".
    assert ">IVOCLAR<" not in resp.text


# --------------------------------------------------------------------------- #
# registry views: manufacturer detail
# --------------------------------------------------------------------------- #
def test_manufacturer_detail_lists_codes_and_items(client, conn, playbooks_dir):
    _seed_manufacturer(conn, "IVOCLAR", ["001", "005"])
    _seed_item(conn, "LJ-1", "001")
    conn.commit()

    resp = client.get("/manufacturers/IVOCLAR")
    assert resp.status_code == 200
    assert "001" in resp.text and "005" in resp.text
    assert "LJ-1" in resp.text


def test_manufacturer_detail_404_for_unknown_entity(client, playbooks_dir):
    assert client.get("/manufacturers/NOPE-DOES-NOT-EXIST").status_code == 404


def test_manufacturer_detail_handles_spaces_in_name(client, conn, playbooks_dir):
    _seed_manufacturer(conn, "3M ESPE", ["118"])
    conn.commit()

    resp = client.get("/manufacturers/3M%20ESPE")
    assert resp.status_code == 200
    assert "3M ESPE" in resp.text


def test_manufacturer_detail_handles_slash_in_name(client, conn, playbooks_dir):
    # Finding 1 (2026-08-11 review): FastAPI's default path converter is
    # `[^/]+`, so a name containing `/` 404s regardless of encoding — uvicorn
    # decodes a percent-encoded `%2F` back to a literal `/` before routing, so
    # percent-encoding cannot rescue it either. Four live entities hit this
    # today (3SHAPE A/S, 3SHAPE MEDICAL A/S, 3SHAPE TRIOS A/S, ELOS MEDTECH
    # PINOL A/S). The URL below is exactly what the template's
    # `{{ row.canonical_name | urlencode }}` produces for this name: Jinja's
    # `urlencode` quotes with `safe="/"`, so the space becomes `%20` and the
    # slash is left untouched.
    _seed_manufacturer(conn, "3SHAPE A/S", ["119"])
    conn.commit()

    resp = client.get("/manufacturers/3SHAPE%20A/S")
    assert resp.status_code == 200
    assert "3SHAPE A/S" in resp.text


def test_manufacturer_detail_shows_no_playbook_copy_when_uncovered(client, conn, playbooks_dir):
    _seed_manufacturer(conn, "GAPCO2", ["807"])
    conn.commit()

    resp = client.get("/manufacturers/GAPCO2")
    assert resp.status_code == 200
    assert "No playbook authored for this manufacturer." in resp.text


def test_manufacturer_detail_does_not_claim_no_playbook_when_index_is_broken(conn, tmp_path):
    """Finding 2 (2026-08-11 review), reproduced with the exact scenario named
    in the finding: `playbooks/ivoclar.json` fails to parse. `/manufacturers`
    correctly names it as broken (test_playbooks_page_survives_a_malformed_file
    below); `/manufacturers/IVOCLAR` must not affirmatively state that no
    playbook was ever authored for the entity — that is a false statement, and
    CLAUDE.md requires skipped items to be reported, never silent."""
    pb_dir = tmp_path / "playbooks"
    pb_dir.mkdir()
    (pb_dir / "ivoclar.json").write_text("{ not json")
    cfg = Web(api_database_url=API_TEST_URL, imports_dir=str(tmp_path),
              playbooks_dir=str(pb_dir))
    broken_client = TestClient(create_app(cfg))

    _seed_manufacturer(conn, "IVOCLAR", ["808"])
    conn.commit()

    resp = broken_client.get("/manufacturers/IVOCLAR")
    assert resp.status_code == 200
    assert "No playbook authored for this entity." not in resp.text
    assert "Playbooks could not be read" in resp.text
    assert "ivoclar.json" in resp.text


def test_manufacturer_detail_truncates_items_but_states_true_total(
    client, conn, playbooks_dir, monkeypatch
):
    """Regression test for the real-catalogue bug found driving the app:
    CARL MARTIN has 2567 items behind the detail page's LIMIT 500, and the
    header used to read `Items ({{ items | length }})` — the length of the
    already-truncated list, so it echoed "Items (500)" and contradicted the
    manufacturers list page that linked here showing 2567. Patch the cap down
    to 3 instead of seeding hundreds of rows to keep this fast."""
    monkeypatch.setattr(registry, "_DETAIL_LIST_CAP", 3)
    _seed_manufacturer(conn, "BIGCO", ["950"])
    for i in range(5):
        _seed_item(conn, f"BIG-{i}", "950")
    conn.commit()

    resp = client.get("/manufacturers/BIGCO")
    assert resp.status_code == 200
    # The count moved into the tab label when the three sections became tabs
    # (2026-08-18); the contract it protects did not — the number stated is the
    # TRUE total, and the cap is declared separately rather than by quietly
    # showing a smaller number.
    assert "Items (5)" in resp.text
    assert "Showing the first 3 of 5 items" in resp.text
    # exactly the capped 3 rows render, not all 5
    assert resp.text.count('href="/items/BIG-') == 3


def test_manufacturer_detail_no_truncation_notice_at_or_below_cap(
    client, conn, playbooks_dir, monkeypatch
):
    """The flip side of the truncation notice: when the true count fits under
    the cap the page must read naturally ("Items (3)"), never a "showing 3 of
    3" notice that would be true but pointless."""
    monkeypatch.setattr(registry, "_DETAIL_LIST_CAP", 3)
    _seed_manufacturer(conn, "SMALLCO", ["951"])
    for i in range(3):
        _seed_item(conn, f"SMALL-{i}", "951")
    conn.commit()

    resp = client.get("/manufacturers/SMALLCO")
    assert resp.status_code == 200
    assert "Items (3)" in resp.text
    assert "Items —" not in resp.text
    # The tab is labelled with the § 9 word for `production`.
    assert "Published documents (0)" in resp.text
    assert "Published documents —" not in resp.text


def test_manufacturer_detail_truncates_production_docs_but_states_true_total(
    client, conn, playbooks_dir, monkeypatch
):
    """Same LIMIT 500 + `| length` header bug applies to the production
    documents list. It reads 0 today only because `item_document_production`
    is empty everywhere in the real catalogue — the same bug is waiting to
    happen the day documents start landing, so it gets the identical fix and
    the identical test.

    Seeds under code "952", not the helper's default "077": `_seed_grouped_
    expiring_doc` above (used by the expiry-board tests) also hardcodes "077"
    for its item_mirror rows, and since item_mirror/manufacturer_alias are not
    truncated between tests in this file, mapping "077" to DOCCO here would
    silently pull those unrelated documents into this entity's count too."""
    monkeypatch.setattr(registry, "_DETAIL_LIST_CAP", 2)
    _seed_manufacturer(conn, "DOCCO", ["952"])
    conn.commit()
    for i in range(4):
        _seed_item_with_doc(conn, f"DOC-{i}", manufacturer_raw="952")

    resp = client.get("/manufacturers/DOCCO")
    assert resp.status_code == 200
    assert "Published documents (4)" in resp.text
    assert "Showing the first 2 of 4 documents" in resp.text


# --------------------------------------------------------------------------- #
# registry views: playbooks
# --------------------------------------------------------------------------- #
@pytest.fixture
def playbook_client(test_db_url, tmp_path):
    """A client whose playbook directory is a throwaway fixture dir."""
    pb_dir = tmp_path / "playbooks"
    pb_dir.mkdir()
    (pb_dir / "voco.json").write_text(json.dumps({
        "manufacturer": "VOCO",
        "aliases": ["VOCO GmbH"],
        "bc_codes": [{"catalogue": "LJ", "code": "042"}],
        "domains": ["voco.dental"],
        "doc_sources": [{"doc_type": "DoC", "kind": "portal", "url": "https://voco.dental/doc"}],
    }))
    cfg = Web(
        api_database_url=API_TEST_URL,
        imports_dir=str(tmp_path),
        playbooks_dir=str(pb_dir),
    )
    return TestClient(create_app(cfg))


def test_playbooks_lists_authored_files(playbook_client):
    resp = playbook_client.get("/playbooks")
    assert resp.status_code == 200
    assert "voco" in resp.text
    assert "VOCO" in resp.text


def test_playbook_detail_renders_sources_and_codes(playbook_client):
    resp = playbook_client.get("/playbooks/voco")
    assert resp.status_code == 200
    assert "voco.dental" in resp.text
    assert "042" in resp.text
    assert "https://voco.dental/doc" in resp.text


def test_playbook_detail_404_for_unknown_slug(playbook_client):
    assert playbook_client.get("/playbooks/nope").status_code == 404


# --------------------------------------------------------------------------- #
# the drift banner: does the repo file still say what the database holds?
#
# The web process CANNOT see the playbook files in the deployed configuration
# -- `Dockerfile.web` copies only `app/` and `web/`. Compose bind-mounts the
# directory read-only for development, which is where playbooks are authored
# and therefore where they drift. The banner has to be silent in the first case
# and accurate in the second, and both halves are tested here.
# --------------------------------------------------------------------------- #
def _pb_dir(tmp_path):
    """The directory `playbook_client` authored into. Same `tmp_path`."""
    return tmp_path / "playbooks"


def _pb_body(tmp_path, slug="voco"):
    raw = json.loads((_pb_dir(tmp_path) / f"{slug}.json").read_text())
    return playbooks_mod.body_of(raw)


def _import_playbook(conn, slug, body, rev=0):
    """A manufacturer row as `manufacturers seed` would leave it: the body, the
    revision it came in at, and revision 0 recording the file's provenance."""
    mid = conn.execute(
        "INSERT INTO manufacturer (canonical_name, slug, body, playbook_rev) "
        "VALUES (%s,%s,%s,%s) RETURNING id",
        (slug.upper(), slug, Json(body), rev)).fetchone()["id"]
    conn.execute(
        "INSERT INTO manufacturer_playbook_revision "
        "  (manufacturer_id, rev, body, authored_by, note) "
        "VALUES (%s,0,%s,'seed','seeded')", (mid, Json(body)))
    return mid


def _save_revision(conn, mfr_id, rev, body):
    """A save through the editor: the row moves forward, the file does not."""
    conn.execute("UPDATE manufacturer SET body=%s, playbook_rev=%s WHERE id=%s",
                 (Json(body), rev, mfr_id))
    conn.execute(
        "INSERT INTO manufacturer_playbook_revision "
        "  (manufacturer_id, rev, body, authored_by, note) "
        "VALUES (%s,%s,%s,'user:admin','edited')", (mfr_id, rev, Json(body)))


def test_playbooks_drift_banner_says_so_when_file_and_row_agree(
        playbook_client, conn, tmp_path):
    _import_playbook(conn, "voco", _pb_body(tmp_path))
    conn.commit()
    resp = playbook_client.get("/playbooks")
    assert "drift-ok" in resp.text
    assert "Repo files and the database agree" in resp.text


def test_playbooks_drift_banner_names_a_stale_file_and_the_command(
        playbook_client, conn, tmp_path):
    """The case the whole feature exists for: someone saved in the editor, the
    file kept saying what it said, and nothing told anyone."""
    body = _pb_body(tmp_path)
    mid = _import_playbook(conn, "voco", body)
    _save_revision(conn, mid, 1, dict(body, date_labels={"from": ["Date:"]}))
    conn.commit()

    resp = playbook_client.get("/playbooks")
    assert "drift-warn" in resp.text
    assert "stale file" in resp.text
    # The specific revisions, because "something is stale" is not actionable.
    assert "revision 1" in resp.text and "revision 0" in resp.text
    assert "db has +date_labels" in resp.text
    assert "playbooks drift --apply" in resp.text


def test_playbooks_drift_banner_refuses_to_offer_apply_for_a_divergence(
        playbook_client, conn, tmp_path):
    """A file that matches no revision was edited outside the editor, so
    regenerating it would destroy that edit. The banner must send the operator
    to the form, and must NOT advertise the command that rewrites files."""
    mid = _import_playbook(conn, "voco", {"domains": ["something-else.example"]})
    _save_revision(conn, mid, 1, {"domains": ["third-thing.example"]})
    conn.commit()

    resp = playbook_client.get("/playbooks")
    assert "drift-bad" in resp.text
    assert "diverged" in resp.text
    assert "--apply" not in resp.text
    assert "/playbooks/voco" in resp.text


def test_playbooks_drift_banner_reports_a_file_with_no_row(
        playbook_client, tmp_path):
    """No `manufacturers seed` has run. That is a real state with a real fix,
    not an error -- the page still lists the playbook."""
    resp = playbook_client.get("/playbooks")
    assert "a file with no row yet" in resp.text
    assert "manufacturers seed" in resp.text


def test_playbooks_drift_banner_reports_a_row_whose_file_is_gone(
        playbook_client, conn, tmp_path):
    _import_playbook(conn, "vanished", {"domains": ["gone.example"]})
    conn.commit()
    resp = playbook_client.get("/playbooks")
    # Phrased for both causes: `load_raw` skips a malformed file rather than
    # raising, so a JSON typo arrives here looking exactly like a deletion.
    assert "no readable file" in resp.text


def test_playbooks_drift_banner_is_absent_without_a_playbooks_directory(
        test_db_url, tmp_path, monkeypatch):
    """The DEPLOYED configuration. `Dockerfile.web` ships no `playbooks/`, so
    there are no files to be stale -- and `drift()` on a missing directory
    reports every row as `db-only`, which would be a page full of alarm about
    nothing. `None` is the only honest answer, and it renders nothing."""
    missing = tmp_path / "not-here"
    monkeypatch.setattr(playbooks_mod, "PLAYBOOKS_DIR", missing)
    client = TestClient(create_app(Web(
        api_database_url=API_TEST_URL, imports_dir=str(tmp_path))))
    resp = client.get("/playbooks")
    assert resp.status_code == 200
    assert "drift-" not in resp.text


def test_playbook_drift_returns_none_when_the_directory_is_missing(conn, tmp_path):
    assert registry.playbook_drift(conn, str(tmp_path / "not-here")) is None


def test_playbook_drift_reads_files_not_rows(conn, tmp_path, monkeypatch):
    """The bug that cost a session on 2026-08-31, at the layer that repeats it.

    `load_raw(None)` returns ROWS once a source is set, and `web/app.py` sets
    one in production (`_use_database_playbooks`). A drift check that let the
    directory default would compare the database against itself and report
    everything in sync forever. Every path through `playbook_drift` has to pass
    the directory EXPLICITLY, which is what this asserts: with a source set and
    a file that differs, the difference is still seen."""
    pb_dir = tmp_path / "playbooks"
    pb_dir.mkdir()
    (pb_dir / "acme.json").write_text(json.dumps(
        {"manufacturer": "ACME", "domains": ["acme.example"]}))
    _import_playbook(conn, "acme", {"domains": ["acme.example"]})
    conn.execute("UPDATE manufacturer SET body=%s, playbook_rev=1 WHERE slug='acme'",
                 (Json({"domains": ["moved-on.example"]}),))
    conn.execute(
        "INSERT INTO manufacturer_playbook_revision "
        "  (manufacturer_id, rev, body, authored_by, note) "
        "SELECT id, 1, %s, 'user:admin', 'edited' FROM manufacturer "
        " WHERE slug='acme'", (Json({"domains": ["moved-on.example"]}),))
    conn.commit()

    monkeypatch.setattr(playbooks_mod, "_SOURCE", lambda: conn)
    try:
        out = registry.playbook_drift(conn, str(pb_dir))
    finally:
        monkeypatch.setattr(playbooks_mod, "_SOURCE", None)
    assert [d["slug"] for d in out["stale"]] == ["acme"]


def test_manufacturer_row_links_its_playbook(playbook_client, conn):
    _seed_manufacturer(conn, "VOCO", ["042"])
    conn.commit()
    resp = playbook_client.get("/manufacturers")
    assert "/playbooks/voco" in resp.text


def test_manufacturer_detail_links_its_playbook(playbook_client, conn):
    # Finding 6 (2026-08-11 review): no test asserted the detail page — as
    # opposed to the list page above — actually renders the `/playbooks/{slug}`
    # link, which is exactly the gap that let finding 2 (the false "no
    # playbook authored" claim) survive four review rounds unnoticed.
    _seed_manufacturer(conn, "VOCO", ["042"])
    conn.commit()
    resp = playbook_client.get("/manufacturers/VOCO")
    assert resp.status_code == 200
    assert 'href="/playbooks/voco"' in resp.text


def test_playbooks_page_survives_a_malformed_file(test_db_url, tmp_path):
    """`load_playbooks` never raises on a bad file — it logs and drops it — so
    the only way an operator learns a playbook is broken (as opposed to never
    authored) is if this page says so. A valid playbook alongside the broken
    one must still render normally, and the broken file must be named in
    visible output, not just swallowed behind a 200."""
    pb_dir = tmp_path / "playbooks"
    pb_dir.mkdir()
    (pb_dir / "broken.json").write_text("{ not json")
    (pb_dir / "voco.json").write_text(json.dumps({"manufacturer": "VOCO"}))
    cfg = Web(
        api_database_url=API_TEST_URL,
        imports_dir=str(tmp_path),
        playbooks_dir=str(pb_dir),
    )
    client = TestClient(create_app(cfg))
    resp = client.get("/playbooks")
    assert resp.status_code == 200
    assert ">voco<" in resp.text
    assert "broken.json" in resp.text


def test_playbook_index_reports_a_missing_directory(tmp_path):
    """Finding 3 (2026-08-11 review): `load_playbook_index`'s discrepancy
    detector was guarded behind `if dir_path.is_dir():` with no else branch, so
    a non-existent directory returned no playbooks AND no error — identical to
    "no playbooks authored". Reachable in the shipped configuration:
    `Dockerfile.web` copies only `app/` and `web/`, so `/app/playbooks` exists
    solely because of the compose bind mount; drop the mount and the triage
    signal silently inverts with no warning."""
    missing = tmp_path / "does-not-exist"
    pbs, error = registry.load_playbook_index(str(missing))
    assert pbs == ()
    assert error is not None
    assert str(missing) in error


# --------------------------------------------------------------------------- #
# the item's OWN name on the grouping board
# --------------------------------------------------------------------------- #
# The board asks a human to approve a merge, so it has to show what is being
# merged. It showed only `candidates->0->>'sample_name'`, so the live GC data
# rendered `003234 ... FUJI PLUS A3 50 KAPSUL` -- while 003234 is FUJI II LC
# KAPSULE A2 50KOS, a different device (luting cement vs light-cured
# restorative), scoring 0.73 purely on the shared FUJI / A3 / 50 KAPSUL tokens.
# The assign form pre-fills that same group, so one click linked FUJI PLUS's
# DoC to FUJI II LC items. RESOLVE was right to refuse the auto-join at 0.90;
# the page made a correct refusal look like a failed match.

_MISLEADING = {
    "item_ref": "gs-fuji-ii-lc",
    "item_name": "FUJI II LC KAPSULE A2 50KOS",
    "candidates": [{"group_id": 96, "score": 0.7347, "sample_name": "FUJI PLUS A3 50 KAPSUL"}],
    "suggestion": {"t1_error": "ranking not wired"},
    "score": 0.7347,
}


def test_suggestion_row_shows_the_items_own_name(client, conn):
    _seed_suggestion(conn, **_MISLEADING)
    conn.commit()

    resp = client.get("/staging/suggestions", params={"q": "gs-fuji-ii-lc"})

    assert resp.status_code == 200
    assert "FUJI II LC KAPSULE A2 50KOS" in resp.text   # what would move
    assert "FUJI PLUS A3 50 KAPSUL" in resp.text        # where it would go


def test_suggestion_detail_shows_the_items_own_name(client, conn):
    sid = _seed_suggestion(conn, **{**_MISLEADING, "item_ref": "gs-fuji-ii-lc-detail"})
    conn.commit()

    resp = client.get(f"/staging/suggestion/{sid}/detail")

    assert resp.status_code == 200
    assert "FUJI II LC KAPSULE A2 50KOS" in resp.text


# --------------------------------------------------------------------------- #
# A retracted link is not coverage (2026-08-13, migration 024).
#
# GATE now retracts staged links the current extraction no longer supports, but
# every coverage read joined item_document without filtering status, so a
# retracted link still rendered as a covered item and still counted. Document
# 235 is the live case: 19 retracted links, zero live, and the detail panel
# listed all 19. The audit_log keeps the history; these surfaces assert
# CURRENT coverage and must not.
# --------------------------------------------------------------------------- #

def test_retracted_links_are_not_coverage_anywhere(client, conn):
    doc_id = _seed_item_with_doc(conn, "RETR-1", link_status="staged",
                                 doc_status="staged")
    conn.execute("UPDATE item_document SET status='retracted' WHERE doc_id=%s", (doc_id,))
    conn.commit()

    # /documents list: the row's item_count cell must read 0, not 1.
    resp = client.get("/documents")
    row = re.search(rf'<tr>\s*<td><a href="/documents/{doc_id}">.*?</tr>', resp.text, re.S)
    assert row, resp.text
    assert re.findall(r"<td>(.*?)</td>", row.group(0), re.S)[-1].strip() == "0"

    # /documents/{id}: the item must not appear in Covered items. Assert on the
    # row's own link, not the bare ref -- the content_hash is `h-RETR-1-<uuid>`,
    # so a substring check passes for the wrong reason.
    assert 'href="/items/RETR-1"' not in client.get(f"/documents/{doc_id}").text

    # /items/{ref}: the document must not appear among the item's documents.
    assert f'href="/documents/{doc_id}"' not in client.get("/items/RETR-1").text

    # The JSON API needs no assertion: /api/items/{ref}/documents reads
    # item_document_production -- or item_document_history under
    # ?include_superseded, which widens the DOCUMENT status filter and leaves
    # `id.status = 'production'` exactly where it was (migration 070) -- so a
    # retracted link is unreachable there by construction, flag or no flag.


def test_a_live_link_alongside_a_retracted_one_still_counts(client, conn):
    """The filter must remove the retracted link, not the document's coverage."""
    doc_id = _seed_item_with_doc(conn, "RETR-KEEP", link_status="staged",
                                 doc_status="staged")
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, mfr_ref, md_flag, "
        "catalogue, updated_at) VALUES ('RETR-GONE','Widget','077','W-2',TRUE,'LJ',now()) "
        "ON CONFLICT (item_ref) DO NOTHING")
    conn.execute(
        "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
        "VALUES ('RETR-GONE', %s, 'ref-catalogue', 'retracted')", (doc_id,))
    conn.commit()

    body = client.get(f"/documents/{doc_id}").text
    assert "RETR-KEEP" in body
    assert "RETR-GONE" not in body


def test_manufacturer_page_reaches_the_documents_no_item_link_can(client, conn):
    """The production list on a manufacturer page joins through item links, so
    a `filed` document -- which by definition links no item -- could not appear
    on its own manufacturer's page. 74 of GC's 131 were in that state on
    2026-08-17, reachable only by knowing to type ?status=filed into
    /documents."""
    from tests.fixtures import seed_ui

    _seed_manufacturer(conn, "PICKATTR", ["PICKATTR"])
    seed_ui.seed_item(conn, "pick-attr-1", manufacturer_raw="PICKATTR", md_flag=True)
    filed = seed_ui.seed_document(
        conn, content_hash="h-pick-attr-1", archive_url="/archive/h-pick-attr-1.pdf",
        status="filed",
        evidence_fields=[{"field": "manufacturer", "value": "PICKATTR",
                          "verbatim": "Legal manufacturer PICKATTR", "tier": "T1",
                          "model_id": "claude-haiku-4-5", "confidence": 0.9, "page": 1}],
    )
    conn.commit()

    text = client.get("/manufacturers/PICKATTR").text
    assert f'href="/documents/{filed}"' in text
    assert "badge-filed" in text
    # and the count is broken out by status, so "filed" is a number you can see.
    # The section became the page's default tab on 2026-08-18, so its heading is
    # now the tab label; what it must still prove is that the section exists,
    # names a true count, and is the one that opens.
    assert "All documents (1)" in text
    assert re.search(r'id="tab-mfr-all"[^>]*\bchecked\b', text)

    conn.execute("DELETE FROM evidence WHERE doc_id=%s", (filed,))
    conn.execute("DELETE FROM document WHERE doc_id=%s", (filed,))
    conn.execute("DELETE FROM item_mirror WHERE item_ref='pick-attr-1'")
    conn.execute("DELETE FROM manufacturer_alias WHERE canonical_name='PICKATTR'")
    conn.commit()


def test_document_page_links_its_manufacturer_only_when_the_page_exists(client, conn):
    """item -> document -> manufacturer -> that manufacturer's other documents
    is the journey this registry exists to support, and the document page was
    the dead end in the middle of it. The link appears only when the spelling
    resolves to exactly one canonical, so it never points at a 404."""
    from tests.fixtures import seed_ui

    _seed_manufacturer(conn, "PICKLINK", ["PICKLINK"])
    known = seed_ui.seed_document(
        conn, content_hash="h-pick-link-1", archive_url="/a/1.pdf", status="production",
        evidence_fields=[{"field": "manufacturer", "value": "PICKLINK",
                          "verbatim": "PICKLINK", "tier": "T1",
                          "model_id": "claude-haiku-4-5", "confidence": 0.9, "page": 1}],
    )
    unknown = seed_ui.seed_document(
        conn, content_hash="h-pick-link-2", archive_url="/a/2.pdf", status="production",
        evidence_fields=[{"field": "manufacturer", "value": "Nobody Dental GmbH",
                          "verbatim": "Nobody Dental GmbH", "tier": "T1",
                          "model_id": "claude-haiku-4-5", "confidence": 0.9, "page": 1}],
    )
    conn.commit()

    assert 'href="/manufacturers/PICKLINK"' in client.get(f"/documents/{known}").text
    other = client.get(f"/documents/{unknown}").text
    assert "Nobody Dental GmbH" in other
    assert 'href="/manufacturers/Nobody' not in other

    conn.execute("DELETE FROM evidence WHERE doc_id = ANY(%s)", ([known, unknown],))
    conn.execute("DELETE FROM document WHERE doc_id = ANY(%s)", ([known, unknown],))
    conn.execute("DELETE FROM manufacturer_alias WHERE canonical_name='PICKLINK'")
    conn.commit()


def test_documents_list_names_and_links_the_manufacturer(client, conn):
    """The list answers "whose document is this?" without opening the row.

    326 documents and no manufacturer column meant "everything IVOCLAR issued"
    could only be answered one row at a time, though `/manufacturers/{name}`
    answers it directly. Linked under the same guard the detail page uses, so
    a name that resolves to no canonical (or to two) stays plain text rather
    than pointing at a 404 or an arbitrary pick.
    """
    from tests.fixtures import seed_ui

    _seed_manufacturer(conn, "LISTLINK", ["LISTLINK"])
    known = seed_ui.seed_document(
        conn, content_hash="h-doclist-1", archive_url="/a/dl1.pdf", status="production",
        evidence_fields=[{"field": "manufacturer", "value": "LISTLINK",
                          "verbatim": "LISTLINK", "tier": "T1",
                          "model_id": "claude-haiku-4-5", "confidence": 0.9, "page": 1}],
    )
    unknown = seed_ui.seed_document(
        conn, content_hash="h-doclist-2", archive_url="/a/dl2.pdf", status="production",
        evidence_fields=[{"field": "manufacturer", "value": "Unlisted Dental GmbH",
                          "verbatim": "Unlisted Dental GmbH", "tier": "T1",
                          "model_id": "claude-haiku-4-5", "confidence": 0.9, "page": 1}],
    )
    conn.commit()

    text = client.get(f"/documents?q=&status=production&page=0").text
    assert "<th>Manufacturer</th>" in text
    assert 'href="/manufacturers/LISTLINK"' in text
    assert "Unlisted Dental GmbH" in text
    assert 'href="/manufacturers/Unlisted' not in text

    conn.execute("DELETE FROM evidence WHERE doc_id = ANY(%s)", ([known, unknown],))
    conn.execute("DELETE FROM document WHERE doc_id = ANY(%s)", ([known, unknown],))
    conn.execute("DELETE FROM manufacturer_alias WHERE canonical_name='LISTLINK'")
    conn.commit()


def test_manual_task_is_headed_by_its_document_not_by_its_kind(client, conn):
    """A queue has to say what is waiting.

    "Task #3 — gate-manual" over forty lines of `tier_attempts` JSON made a
    person read model confidences to learn they were looking at a declaration.
    The kind and the task id survive as a subheading; the payload moves behind
    the same disclosure every other board uses.
    """
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)
    conn.commit()

    text = client.get("/manual").text
    assert "Declaration of Conformity" in text
    # the id is still stated, just no longer the headline
    assert f"Task #{ids['manual_task']}" in text
    # the JSON is present but folded away
    assert "<summary>Payload</summary>" in text
    assert "Open this document in Review" in text


def test_manufacturer_detail_opens_on_documents_not_on_the_item_dump(
    client, conn, playbooks_dir
):
    """The compliance documents are what this page is for.

    Measured 2026-08-18 before the tabs: /manufacturers/IVOCLAR was 23.065px
    tall — 14,5 viewports — because it printed 500 item rows before reaching
    "Production documents" at 15.541px and the attributed list at 17.737px. All
    three sections still exist; only one is open at a time, and the default is
    the superset that includes `filed`.
    """
    _seed_manufacturer(conn, "TABCO", ["953"])
    _seed_item(conn, "TAB-1", "953")
    conn.commit()

    resp = client.get("/manufacturers/TABCO")
    assert resp.status_code == 200
    # the "all documents" radio carries `checked`, the other two do not
    assert re.search(r'id="tab-mfr-all"[^>]*\bchecked\b', resp.text)
    assert not re.search(r'id="tab-mfr-items"[^>]*\bchecked\b', resp.text)
    # every section is still on the page, just behind a tab
    assert 'id="panel-mfr-all"' in resp.text
    assert 'id="panel-mfr-production"' in resp.text
    assert 'id="panel-mfr-items"' in resp.text


def test_manufacturer_detail_tab_is_selectable_and_falls_back(
    client, conn, playbooks_dir
):
    """`?tab=` makes each section linkable; an unknown value is not an error,
    it just opens the default — a deep link is not worth a 422."""
    _seed_manufacturer(conn, "TABPICK", ["954"])
    conn.commit()

    picked = client.get("/manufacturers/TABPICK?tab=items")
    assert re.search(r'id="tab-mfr-items"[^>]*\bchecked\b', picked.text)
    assert not re.search(r'id="tab-mfr-all"[^>]*\bchecked\b', picked.text)

    junk = client.get("/manufacturers/TABPICK?tab=nonsense")
    assert junk.status_code == 200
    assert re.search(r'id="tab-mfr-all"[^>]*\bchecked\b', junk.text)


def test_document_detail_scrolls_its_covered_items_instead_of_printing_them_all(
    client, conn
):
    """Document 310 covers 356 items; printing them inline pushed the evidence
    table — the thing an auditor asks for — to 11.798px down the page. The rows
    stay and the count is stated; the box scrolls."""
    doc_id = _seed_item_with_doc(conn, "SCROLL-1")
    conn.commit()

    text = client.get(f"/documents/{doc_id}").text
    assert "Covered items (1)" in text
    assert '<div class="table-wrap tall">' in text


def test_expiry_board_separates_already_lapsed_from_about_to_lapse(client, conn):
    """The split this board exists for.

    One table under a single horizon selected `expires <= current_date + days`,
    which silently includes everything already expired — and on the live
    registry that was all of it: four rows reading 2237, 814, 814 and 106 days
    ago under a heading saying "what is about to lapse". A certificate that
    lapsed in May is a different problem from one lapsing next month.
    """
    lapsed_mfr = f"ExpPast-{uuid.uuid4().hex[:8]}"
    future_mfr = f"ExpFuture-{uuid.uuid4().hex[:8]}"
    _, lapsed_cert = _seed_grouped_expiring_doc(
        conn, manufacturer=lapsed_mfr, day_offset=-20
    )
    _, future_cert = _seed_grouped_expiring_doc(
        conn, manufacturer=future_mfr, day_offset=20
    )

    text = client.get("/expiry").text
    # D7 (2026-09-11) fixed the forward window at 30 days; the lookback kept
    # its 180 (controller ruling the same day)
    assert "Expired in the last 180 days" in text
    assert "Expiring in the next 30 days" in text
    # both rows render, and the lapsed one comes FIRST — it is the emergency
    assert lapsed_cert in text and future_cert in text
    assert text.index(lapsed_cert) < text.index(future_cert)
    # and the already-lapsed row is not described as having days left
    assert "20 days ago" in text


def test_expiry_board_folds_long_expired_away_without_dropping_it(client, conn):
    """A lapsed certificate that still carries production links is exposure we
    have to be able to evidence on request. Hiding the row does not remove the
    exposure, so it is collapsed rather than filtered out."""
    old_mfr = f"ExpOld-{uuid.uuid4().hex[:8]}"
    _, old_cert = _seed_grouped_expiring_doc(
        conn, manufacturer=old_mfr, day_offset=-800
    )

    text = client.get("/expiry").text
    assert "Long expired" in text
    assert old_cert in text                      # kept
    assert "800 days ago" in text
    # behind a disclosure, not in the emergency table
    fold = text.index("Long expired")
    assert fold < text.index(old_cert)
    assert "<details" in text[fold:text.index(old_cert)]

    # a wider "recent" window promotes it out of the fold and into the
    # emergency table, and the fold disappears with nothing left to hold
    wide = client.get("/expiry?back=900").text
    assert "Expired in the last 900 days" in wide
    assert old_cert in wide
    assert "Long expired" not in wide


def test_expiry_board_counts_what_is_beyond_the_window_instead_of_dropping_it(
    client, conn
):
    """A board that shows nothing beyond its window and says nothing about it
    reads as "all clear" (CLAUDE.md: skipped rows are counted and reported)."""
    far_mfr = f"ExpBeyond-{uuid.uuid4().hex[:8]}"
    _, far_cert = _seed_grouped_expiring_doc(
        conn, manufacturer=far_mfr, day_offset=900
    )

    text = client.get("/expiry?ahead=30").text
    assert far_cert not in text                  # genuinely not listed
    assert "beyond this window" in text          # but named
    assert 'href="/expiry?back=180&amp;ahead=3650"' in text


# --------------------------------------------------------------------------- #
# service chips on the System status board
#
# They were the header strip's second row until 2026-09-14 (office UI redesign
# spec § 7, rule 8): the office reads one health line in the sidebar, and the
# per-process detail moved to the operator's own board.

def test_the_status_board_shows_a_chip_per_service(client, conn):
    """One chip per service. The question it answers is the one nothing could
    answer before: is the worker still turning over, or has it been dead since
    lunch?"""
    from app import heartbeat

    heartbeat.beat(conn, "worker", "worker-1@abc")
    conn.commit()

    text = client.get("/status").text
    assert "worker" in text
    for implied in ("web", "caddy", "db"):
        assert implied in text


def test_the_status_board_marks_a_stale_service_down(client, conn):
    """Only a real problem colours a chip, so `down` has to mean down -- a
    worker whose last beat aged past its window. The sidebar's health line
    says the same thing in words (tests/test_web_today.py)."""
    from app import heartbeat

    heartbeat.beat(conn, "worker", "worker-1@abc")
    conn.execute("UPDATE service_heartbeat SET last_seen = now() - interval '10 m'")
    conn.commit()

    assert "svc-down" in client.get("/status").text


def test_a_never_started_service_is_off_not_down(client, conn):
    """A service that has never beat is `off`, not `down`.

    Painting "not started here" red would put a permanent alert on the board,
    which is how a signal stops being read. This used to assert on the
    `scheduler` chip, which was profile-gated and so legitimately absent on
    most machines; that service was deleted 2026-09-02 and `worker` is now the
    only beating service, so the case is made by not beating it at all.
    """
    text = client.get("/status").text
    assert "svc-off" in text          # worker, never beat
    assert "svc-down" not in text     # absent is not dead


def test_the_services_the_page_proves_are_never_down(client, conn):
    """`web`, `caddy` and `db` are true by construction: this response was
    rendered by web, served through caddy and read out of Postgres. They carry
    no heartbeat and must never show as down -- a page that could tell you its
    own server was unreachable would be lying."""
    text = client.get("/status").text
    assert text.count("svc-down") == 0


def test_the_menu_still_carries_its_four_counts(client, conn):
    """Regression guard: the health line is added BESIDE the menu counts, not
    in place of them."""
    text = client.get("/_pulse").text
    for label in ("Review", "Missing documents", "Expiring", "Renewal emails"):
        assert label in text
    assert text.count('<span class="nav-n">') == 4


# ---------------------------------------------------------------------------
# C17 — link-level decisions (producer side). The UI enqueues gate.apply and
# writes nothing; the handler owns every precondition (tests/test_gate_apply_handler.py).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("decision", ["confirm-link", "reject-link", "reopen-link"])
def test_link_decide_enqueues_gate_apply_scoped_to_the_link(client, conn, decision):
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)
    conn.commit()

    resp = client.post(
        f"/links/{ids['prod_doc']}/decide",
        data={"item_ref": "seed-item-3", "decision": decision, "decided_by": "user:marta"},
    )
    assert resp.status_code == 200

    # dedupe key carries the item_ref: two items under one document are two
    # decisions and must not collapse onto each other
    rows = _jobs(conn, f"apply:{ids['prod_doc']}:seed-item-3:{decision}")
    assert len(rows) == 1
    assert rows[0]["type"] == "gate.apply"
    assert rows[0]["priority"] == "interactive"
    assert rows[0]["payload"] == {
        "doc_id": ids["prod_doc"], "item_ref": "seed-item-3",
        "decision": decision, "decided_by": "user:marta",
    }


def test_link_decide_does_not_write_the_registry(client, conn):
    """inv. 1: web is a producer. The link must be untouched until a worker runs."""
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)
    conn.commit()

    client.post(f"/links/{ids['prod_doc']}/decide",
                data={"item_ref": "seed-item-3", "decision": "confirm-link"})

    row = conn.execute(
        "SELECT status, match_basis FROM item_document WHERE doc_id=%s AND item_ref=%s",
        (ids["prod_doc"], "seed-item-3")).fetchone()
    assert row["status"] == "staged" and row["match_basis"] == "name-family"


@pytest.mark.parametrize("data", [
    {"item_ref": "seed-item-3", "decision": "approve"},        # document decision, wrong route
    {"item_ref": "seed-item-3", "decision": "delete-link"},    # not a decision at all
    {"item_ref": "  ", "decision": "confirm-link"},            # blank item_ref
])
def test_link_decide_rejects_bad_input_and_enqueues_nothing(client, conn, data):
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)
    conn.commit()
    before = conn.execute("SELECT count(*) AS n FROM job").fetchone()["n"]

    resp = client.post(f"/links/{ids['prod_doc']}/decide", data=data)

    assert resp.status_code == 422
    assert conn.execute("SELECT count(*) AS n FROM job").fetchone()["n"] == before


def test_staging_renders_link_decision_buttons_on_staged_links(client, conn):
    from tests.fixtures import seed_ui

    seed_ui.seed_demo(conn)
    conn.commit()

    text = client.get("/staging").text
    assert 'hx-post="/links/' in text
    assert 'value="confirm-link"' in text and 'value="reject-link"' in text
    # the sentence the reviewer needed and did not have
    assert "Approving the document does not publish them" in text


def test_item_page_offers_a_decision_only_where_one_can_be_taken(client, conn):
    """The surface where the gap was hit: production document, staged link."""
    from tests.fixtures import seed_ui

    seed_ui.seed_demo(conn)
    conn.commit()

    # seed-item-3: production doc + staged name-family link -> decidable
    text = client.get("/items/seed-item-3").text
    assert 'value="confirm-link"' in text and 'value="reject-link"' in text
    assert "reopen-link" not in text

    # seed-item-1: same document, link already production -> nothing to decide
    assert "confirm-link" not in client.get("/items/seed-item-1").text


def test_item_page_offers_reopen_only_on_a_rejected_link(client, conn):
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)
    seed_ui.seed_link(conn, "seed-item-3", ids["prod_doc"],
                      match_basis="name-family", status="rejected")
    conn.commit()

    text = client.get("/items/seed-item-3").text
    assert 'value="reopen-link"' in text
    assert "confirm-link" not in text and "reject-link" not in text


def test_staged_links_on_a_terminal_document_are_listed_but_not_decidable(client, conn):
    """The withdrawn '17': staged links under a document that reached a terminal
    state without ever being production. Correct as they stand, so read-only —
    but visible, because invisible dead state reads as state nobody considered."""
    from tests.fixtures import seed_ui

    seed_ui.seed_demo(conn)
    dead_doc = seed_ui.seed_document(
        conn, content_hash="c17-superseded", archive_url="local://c17/old.pdf",
        status="superseded", cert_number="CERT-OLD")
    seed_ui.seed_link(conn, "seed-item-2", dead_doc,
                      match_basis="map-supplier", status="staged")
    conn.commit()

    text = client.get("/staging").text
    assert "Links waiting on documents that were never published" in text
    assert "Nothing to decide here" in text
    assert f'href="/documents/{dead_doc}"' in text
    # and no form for it — the macro refuses a non-production document
    assert f'hx-post="/links/{dead_doc}/decide"' not in text


# --------------------------------------------------------------------------- #
# API reference page (slice 3): what this app exposes, to whom, and with which
# credential -- the orientation /docs and /openapi.json cannot give.
# --------------------------------------------------------------------------- #
def test_api_reference_page_lists_the_endpoints(client, conn):
    _seed_item_with_doc(conn, "REF-1")
    resp = client.get("/api-reference")
    assert resp.status_code == 200
    for path in ("/api/items/", "/item/", "/api/kpi", "/openapi.json"):
        assert path in resp.text


def test_api_reference_examples_use_a_real_item(client, conn):
    """A documented example that 404s is worse than none. The page reads an
    item_ref out of the database rather than hardcoding one."""
    _seed_item_with_doc(conn, "REF-REAL")
    resp = client.get("/api-reference")
    assert "REF-REAL" in resp.text or "no items mirrored yet" in resp.text


def test_api_reference_is_in_the_nav(client):
    resp = client.get("/items")
    assert "/api-reference" in resp.text


def test_long_pickers_ship_a_type_to_filter_box(client):
    """Denis, 2026-09-04, on the BC-code picker: a long picker needs a filter
    that searches by name as well as by code.

    Attached to the LIST rather than to either page: the playbook picker offers
    every vendor Business Central knows (389) and the Review picker every
    manufacturer we hold, and both are scroll-only without it. Fragments
    swapped in by htmx are scanned too, or Review's picker -- which does not
    exist at DOMContentLoaded -- would never get one.

    A server-side test can only prove the enhancement SHIPS. That it filters on
    code and name, keeps `data-items` intact for the approve-button handler,
    and does not claim a code when Enter is pressed in the box was verified in
    a real browser against the running stack, 2026-09-04.
    """
    resp = client.get("/items")
    assert "SELECT_FILTER_MIN" in resp.text
    assert "dentaliaScanSelects" in resp.text
    assert "htmx:afterSwap" in resp.text


# --------------------------------------------------------------------------- #
# /import — a BC export handed to us through the browser, previewed before
# anything is written (Task 5).
# --------------------------------------------------------------------------- #
_XLSX_NAME = "Artikli 3.7.2026.xlsx"


def _export_csv_bytes():
    """A minimal BC item export, as the bytes a browser would post."""
    import io
    import pandas as pd
    return pd.DataFrame([{
        "Št.": "A1", "Opis": "Composite", "Dobaviteljeva št. artikla": "0450",
        "Opis za iskanje": "", "Šifra proizvajalca": "011",
        "Razred medicinskega pripomočka": "RAZRED IIA",
    }]).to_csv(index=False).encode()


def test_no_form_renders_a_catalogue_select(client):
    """One Business Central, one article numbering. `/import` never offered a
    picker; `/ingest` and `/upload` kept a one-option one until 2026-08-26. All
    three now take the catalogue from config, which is the whole difference --
    `item_mirror.catalogue` is NOT NULL and the column stays, so a value is
    still written, it is just not a question put to the operator."""
    for path in ("/ingest", "/upload", "/import"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert 'name="catalogue"' not in resp.text, path


def test_ingest_still_enqueues_with_the_configured_catalogue(client, conn):
    resp = client.post("/ingest", data={"source": "csv", "priority": "interactive",
                                        "csv_path_manual": "/imports/Artikli.csv"})
    assert resp.status_code == 200
    payload = conn.execute(
        "SELECT payload FROM job WHERE type='ingest.run'").fetchone()["payload"]
    assert payload["catalogue"] == "LJ"


def test_web_app_has_no_catalogues_list(client):
    """Deleted outright rather than reduced to one entry: a one-option list is
    still a list, and it was the validation whitelist as well as the render
    list."""
    import web.app
    assert not hasattr(web.app, "CATALOGUES")


def test_the_status_board_reports_one_mirror_total(client, conn):
    """Both panels used to GROUP BY a single-valued column, rendering a
    one-row breakdown with 'Catalogue' as its first heading."""
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, catalogue, "
        "mirror_rev, updated_at, md_flag) "
        "VALUES ('A1','Composite','011','LJ',1,now(),true)")
    conn.commit()
    page = client.get("/status").text
    assert "<th>Catalogue</th>" not in page


def test_import_spools_the_file_and_enqueues_a_preview(client, conn):
    resp = client.post(
        "/import",
        files={"file": ("Artikli.csv", _export_csv_bytes(), "text/csv")},
        data={"priority": "interactive"},
    )
    assert resp.status_code == 200

    spool = conn.execute(
        "SELECT id, kind, filename, catalogue FROM import_inbox").fetchall()
    assert len(spool) == 1
    assert spool[0]["kind"] == "items"
    assert spool[0]["filename"] == "Artikli.csv"
    assert spool[0]["catalogue"] == "LJ"

    job = conn.execute(
        "SELECT payload, dedupe_key, type FROM job WHERE type='ingest.run'").fetchone()
    # The filename is written to import_inbox.filename above, but dentalia_api
    # has no SELECT on that table (migration 040) -- the payload is the only
    # place the web process can read it back from, for the confirm screen.
    assert job["payload"] == {
        "source": "upload", "ref": {"upload_id": spool[0]["id"]},
        "catalogue": "LJ", "dry_run": True, "filename": "Artikli.csv"}
    assert job["dedupe_key"] == f"ingest.run:upload:{spool[0]['id']}:preview"


def test_import_rejects_a_pdf(client, conn):
    resp = client.post(
        "/import",
        files={"file": ("cert.pdf", b"%PDF-1.4", "application/pdf")},
        data={"priority": "interactive"},
    )
    assert resp.status_code == 422
    assert conn.execute("SELECT count(*) c FROM import_inbox").fetchone()["c"] == 0


def test_import_rejects_an_oversized_file(client, conn):
    from app.config import Web
    from fastapi.testclient import TestClient
    from web.app import create_app

    small = TestClient(create_app(Web(api_database_url=API_TEST_URL, upload_max_mb=0)))
    resp = small.post(
        "/import",
        files={"file": ("Artikli.csv", _export_csv_bytes(), "text/csv")},
        data={"priority": "interactive"},
    )
    assert resp.status_code == 422
    assert conn.execute("SELECT count(*) c FROM import_inbox").fetchone()["c"] == 0


def test_import_rejects_an_unknown_priority(client, conn):
    resp = client.post(
        "/import",
        files={"file": ("Artikli.csv", _export_csv_bytes(), "text/csv")},
        data={"priority": "urgent"},
    )
    assert resp.status_code == 422
    assert conn.execute("SELECT count(*) c FROM import_inbox").fetchone()["c"] == 0


def test_import_accepts_every_suffix_the_adapter_reads(client, conn):
    """The form's accepted set and the adapter's must be the same set, or the
    web rejects files the worker would have read."""
    from app.adapters.source import EXPORT_SUFFIXES
    from web.app import IMPORT_SUFFIXES

    assert set(IMPORT_SUFFIXES) == set(EXPORT_SUFFIXES)


def _seed_preview(conn, *, status="done", result=None, dry_run=True,
                  source="upload", upload_id=None, filename="Artikli.csv"):
    """A finished preview job, as the worker would have left it. `filename=None`
    simulates a job enqueued before the payload carried it, for the backward-
    compat rendering test -- the preview page must not fall over on one."""
    if upload_id is None:
        upload_id = conn.execute(
            "INSERT INTO import_inbox (kind, filename, content, catalogue) "
            "VALUES ('items','Artikli.csv','\\x00','LJ') RETURNING id").fetchone()["id"]
    payload = {"source": source, "ref": {"upload_id": upload_id},
               "catalogue": "LJ", "dry_run": dry_run}
    if filename is not None:
        payload["filename"] = filename
    jid = conn.execute(
        "INSERT INTO job (type, payload, dedupe_key, status, result) "
        "VALUES ('ingest.run', %s, %s, %s, %s) RETURNING id",
        (Json(payload), f"ingest.run:upload:{upload_id}:preview", status,
         Json(result if result is not None else
              {"seen": 2, "changed": 1, "unchanged": 1, "dry_run": True})),
    ).fetchone()["id"]
    conn.commit()
    return jid, upload_id


def test_preview_renders_the_diff(client, conn):
    jid, _ = _seed_preview(conn, result={
        "seen": 19091, "changed": 12, "unchanged": 19079, "dry_run": True})
    resp = client.get(f"/import/{jid}/preview")
    assert resp.status_code == 200
    assert "19091" in resp.text or "19,091" in resp.text
    assert "12" in resp.text


def test_preview_of_an_unfinished_job_says_so_and_offers_no_apply(client, conn):
    jid, _ = _seed_preview(conn, status="running", result=None)
    resp = client.get(f"/import/{jid}/preview")
    assert resp.status_code == 200
    assert "/apply" not in resp.text


def test_preview_of_an_unknown_job_is_404(client):
    assert client.get("/import/999999/preview").status_code == 404


def test_preview_names_the_uploaded_file(client, conn):
    """The confirm screen must say which file the operator is authorizing --
    `import_inbox.filename` is unreadable from the web role (migration 040),
    so job.payload.filename is the only source the page has."""
    jid, _ = _seed_preview(conn, filename="Artikli 3.7.2026.xlsx")
    resp = client.get(f"/import/{jid}/preview")
    assert resp.status_code == 200
    assert "Artikli 3.7.2026.xlsx" in resp.text


def test_preview_renders_cleanly_without_a_filename_in_the_payload(client, conn):
    """A job enqueued before the payload carried `filename` (or any future
    ingest.run without it) must not render the literal string "None"."""
    jid, _ = _seed_preview(conn, filename=None)
    resp = client.get(f"/import/{jid}/preview")
    assert resp.status_code == 200
    assert "None" not in resp.text


def test_preview_page_works_standalone_not_only_embedded_in_import(client, conn):
    """_result.html hands the operator a bare <a href> to this URL, which
    invites a bookmark, a reload, or a middle-click -- none of which land on
    /import, so `#result` (which exists only there) must not be the target of
    anything on this page. Otherwise the Apply button fires at a target that
    does not exist: htmx logs htmx:targetError and nothing happens, silently."""
    jid, _ = _seed_preview(conn)
    resp = client.get(f"/import/{jid}/preview")
    assert resp.status_code == 200
    assert "#result" not in resp.text
    assert "Apply" in resp.text
    assert 'hx-target="closest .import-preview"' in resp.text
    assert 'hx-swap="outerHTML"' in resp.text


def test_apply_enqueues_the_write_run_from_the_previews_payload(client, conn):
    jid, uid = _seed_preview(conn)
    resp = client.post(f"/import/{jid}/apply")
    assert resp.status_code == 200

    job = conn.execute(
        "SELECT payload, dedupe_key FROM job WHERE dedupe_key=%s",
        (f"ingest.run:upload:{uid}:apply",)).fetchone()
    assert job is not None
    assert job["payload"] == {"source": "upload", "ref": {"upload_id": uid},
                              "catalogue": "LJ", "dry_run": False,
                              "filename": "Artikli.csv", "preview_job_id": jid}


def test_apply_confirmation_copy_differs_from_the_preview_confirmation_copy(client, conn):
    """_result.html's onward link reads 'See what this would change' after the
    PREVIEW enqueue, where nothing has been written yet -- that copy is wrong
    once reached from the APPLY enqueue, where the write is already queued."""
    jid, _ = _seed_preview(conn)
    apply_resp = client.post(f"/import/{jid}/apply")
    assert "See what this wrote" in apply_resp.text
    assert "would change" not in apply_resp.text

    preview_resp = client.post(
        "/import",
        files={"file": ("Artikli.csv", _export_csv_bytes(), "text/csv")},
        data={"priority": "interactive"},
    )
    assert "See what this would change" in preview_resp.text


def test_apply_does_not_mutate_the_preview_job(client, conn):
    """Invariant 9: payloads are immutable after enqueue. The apply is a second
    job, never the preview rewritten."""
    jid, uid = _seed_preview(conn)
    client.post(f"/import/{jid}/apply")
    preview = conn.execute("SELECT payload FROM job WHERE id=%s", (jid,)).fetchone()
    assert preview["payload"]["dry_run"] is True


def test_apply_refuses_a_job_that_is_not_a_finished_upload_preview(client, conn):
    running, _ = _seed_preview(conn, status="running")
    assert client.post(f"/import/{running}/apply").status_code == 422

    already_applied, _ = _seed_preview(conn, dry_run=False)
    assert client.post(f"/import/{already_applied}/apply").status_code == 422

    path_run, _ = _seed_preview(conn, source="csv")
    assert client.post(f"/import/{path_run}/apply").status_code == 422


def test_apply_twice_dedupes_on_the_active_job(client, conn):
    """A double-clicked Apply must not enqueue two writing runs."""
    jid, uid = _seed_preview(conn)
    client.post(f"/import/{jid}/apply")
    client.post(f"/import/{jid}/apply")
    assert conn.execute(
        "SELECT count(*) c FROM job WHERE dedupe_key=%s",
        (f"ingest.run:upload:{uid}:apply",)).fetchone()["c"] == 1


def _seed_applied(conn, *, preview_result=None, applied_result=None,
                  preview_job_id="link", upload_id=None):
    """A finished preview and the finished apply that followed it, as the
    worker would have left the pair. `preview_job_id="link"` wires the apply's
    payload back at the preview (what /import/{id}/apply now does);
    `preview_job_id=None` omits the field (a job enqueued before this slice),
    and an int points it at whatever id is given (including one that no longer
    exists)."""
    prev_jid, uid = _seed_preview(
        conn, upload_id=upload_id,
        result=preview_result if preview_result is not None else
        {"seen": 3, "changed": 2, "unchanged": 1, "dry_run": True})
    payload = {"source": "upload", "ref": {"upload_id": uid},
               "catalogue": "LJ", "dry_run": False, "filename": "Artikli.csv"}
    if preview_job_id == "link":
        payload["preview_job_id"] = prev_jid
    elif preview_job_id is not None:
        payload["preview_job_id"] = preview_job_id
    apply_jid = conn.execute(
        "INSERT INTO job (type, payload, dedupe_key, status, result) "
        "VALUES ('ingest.run', %s, %s, 'done', %s) RETURNING id",
        (Json(payload), f"ingest.run:upload:{uid}:apply",
         Json(applied_result if applied_result is not None else
              {"seen": 3, "changed": 2, "unchanged": 1, "dry_run": False})),
    ).fetchone()["id"]
    conn.commit()
    return apply_jid, prev_jid, uid


def test_apply_carries_the_preview_job_id_so_the_write_can_be_compared(client, conn):
    """The apply payload is what tells the confirm screen which preview to
    compare against -- the two jobs share a dedupe_key stem but nothing else,
    and dentalia_api cannot read import_inbox to find the pair another way."""
    jid, uid = _seed_preview(conn)
    client.post(f"/import/{jid}/apply")
    payload = conn.execute(
        "SELECT payload FROM job WHERE dedupe_key=%s",
        (f"ingest.run:upload:{uid}:apply",)).fetchone()["payload"]
    assert payload["preview_job_id"] == jid


def test_applied_page_shows_previewed_against_applied_counts(client, conn):
    """The whole point of preview-then-confirm: the operator sees what we said
    would happen next to what happened, rather than being told to trust it."""
    apply_jid, _, _ = _seed_applied(
        conn,
        preview_result={"seen": 19091, "changed": 15958, "unchanged": 0,
                        "dry_run": True},
        applied_result={"seen": 19091, "changed": 15958, "unchanged": 0,
                        "dry_run": False})
    resp = client.get(f"/import/{apply_jid}/preview")
    assert resp.status_code == 200
    assert "Previewed" in resp.text
    assert "Applied" in resp.text
    assert resp.text.count("15958") >= 2


def test_applied_page_flags_a_count_that_moved_since_the_preview(client, conn):
    """Applying re-reads the export and re-diffs against the catalogue as it is
    NOW. If the catalogue moved under us between preview and apply, the numbers
    differ -- and that must be visible, not averaged away."""
    apply_jid, _, _ = _seed_applied(
        conn,
        preview_result={"seen": 10, "changed": 7, "unchanged": 3, "dry_run": True},
        applied_result={"seen": 10, "changed": 5, "unchanged": 5, "dry_run": False})
    resp = client.get(f"/import/{apply_jid}/preview")
    assert resp.status_code == 200
    assert "moved since the preview" in resp.text
    assert "New or changed" in resp.text


def test_applied_page_says_so_when_every_count_matches(client, conn):
    apply_jid, _, _ = _seed_applied(
        conn,
        preview_result={"seen": 10, "changed": 7, "unchanged": 3, "dry_run": True},
        applied_result={"seen": 10, "changed": 7, "unchanged": 3, "dry_run": False})
    resp = client.get(f"/import/{apply_jid}/preview")
    assert resp.status_code == 200
    assert "matches the preview" in resp.text
    assert "moved since the preview" not in resp.text


def test_applied_page_renders_when_the_preview_job_is_gone(client, conn):
    """Jobs are regenerable state and get pruned. A dangling preview_job_id
    must degrade to the single-column report, never to a 500."""
    apply_jid, _, _ = _seed_applied(conn, preview_job_id=999999)
    resp = client.get(f"/import/{apply_jid}/preview")
    assert resp.status_code == 200
    assert "Previewed" not in resp.text


def test_applied_page_renders_without_a_preview_job_id_in_the_payload(client, conn):
    """An apply enqueued before this slice carries no back-reference."""
    apply_jid, _, _ = _seed_applied(conn, preview_job_id=None)
    resp = client.get(f"/import/{apply_jid}/preview")
    assert resp.status_code == 200
    assert "Previewed" not in resp.text


def test_preview_page_has_no_applied_column(client, conn):
    """Nothing has been written yet, so there is nothing to compare against."""
    jid, _ = _seed_preview(conn)
    resp = client.get(f"/import/{jid}/preview")
    assert resp.status_code == 200
    assert "Previewed" not in resp.text


def _vendor_xlsx_bytes(rows=(("001", "IVOCLAR"),)):
    """A real .xlsx: the spooled filename picks the parser, so the bytes must
    actually be that format."""
    import io

    import pandas as pd
    buf = io.BytesIO()
    pd.DataFrame([{"Šifra": c, "Ime": n} for c, n in rows]).to_excel(buf, index=False)
    return buf.getvalue()


def _seed_vendor_preview(conn, *, result=None, status="done", dry_run=True,
                         upload_id=None):
    """A finished `vendor.import` preview, as the worker would have left it."""
    if upload_id is None:
        upload_id = conn.execute(
            "INSERT INTO import_inbox (kind, filename, content) "
            "VALUES ('vendors','Proizvajalci.xlsx',%s) RETURNING id",
            (_vendor_xlsx_bytes(),)).fetchone()["id"]
    payload = {"upload_id": upload_id, "filename": "Proizvajalci.xlsx",
               "dry_run": dry_run, "allow_renames": False}
    suffix = "preview" if dry_run else "apply"
    jid = conn.execute(
        "INSERT INTO job (type, payload, dedupe_key, status, result) "
        "VALUES ('vendor.import', %s, %s, %s, %s) RETURNING id",
        (Json(payload), f"vendor.import:upload:{upload_id}:{suffix}", status,
         Json(result if result is not None else
              {"seen": 1, "added": 1, "renamed": [], "disappeared": [],
               "unchanged": 0, "dry_run": dry_run, "written": False})),
    ).fetchone()["id"]
    conn.commit()
    return jid


def test_vendor_upload_spools_at_kind_vendors_and_enqueues_a_preview(client, conn):
    resp = client.post(
        "/import/vendors",
        files={"file": ("Proizvajalci.xlsx", _vendor_xlsx_bytes(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"priority": "interactive"},
    )
    assert resp.status_code == 200

    spool = conn.execute("SELECT kind, filename FROM import_inbox").fetchone()
    assert spool["kind"] == "vendors"
    job = conn.execute("SELECT type, payload, dedupe_key FROM job").fetchone()
    assert job["type"] == "vendor.import"
    assert job["dedupe_key"].endswith(":preview")
    assert job["payload"]["dry_run"] is True
    assert job["payload"]["allow_renames"] is False
    assert job["payload"]["filename"] == "Proizvajalci.xlsx"
    # Flat, not nested under `ref` -- that shape belongs to ingest.run.
    assert "ref" not in job["payload"]


def test_vendor_upload_rejects_a_pdf(client, conn):
    resp = client.post(
        "/import/vendors",
        files={"file": ("Proizvajalci.pdf", b"%PDF-1.4", "application/pdf")},
        data={"priority": "interactive"},
    )
    assert resp.status_code == 422
    assert conn.execute("SELECT count(*) c FROM import_inbox").fetchone()["c"] == 0


def test_the_preview_page_dispatches_on_job_type(client, conn):
    """One URL, two report shapes. A vendor job rendered through the item
    template would show blank counts and an Apply button for the wrong thing."""
    jid = _seed_vendor_preview(conn, result={
        "seen": 390, "added": 4, "renamed": [], "disappeared": [],
        "unchanged": 386, "dry_run": True, "written": False})
    resp = client.get(f"/import/{jid}/preview")
    assert resp.status_code == 200
    assert "390" in resp.text
    assert "Rows in file" not in resp.text        # the item template's row label


def test_the_rename_checkbox_appears_only_when_there_are_renames(client, conn):
    clean = _seed_vendor_preview(conn, result={
        "seen": 1, "added": 1, "renamed": [], "disappeared": [],
        "unchanged": 0, "dry_run": True, "written": False})
    assert 'name="allow_renames"' not in client.get(f"/import/{clean}/preview").text

    dirty = _seed_vendor_preview(conn, result={
        "seen": 1, "added": 0, "renamed": [["001", "OLD", "NEW"]],
        "disappeared": [], "unchanged": 0, "dry_run": True, "written": False})
    page = client.get(f"/import/{dirty}/preview").text
    assert 'name="allow_renames"' in page
    assert "OLD" in page and "NEW" in page and "001" in page


def test_ticking_the_box_sets_allow_renames_on_the_apply_payload(client, conn):
    jid = _seed_vendor_preview(conn, result={
        "seen": 1, "added": 0, "renamed": [["001", "OLD", "NEW"]],
        "disappeared": [], "unchanged": 0, "dry_run": True, "written": False})

    client.post(f"/import/{jid}/apply", data={"allow_renames": "on"})

    payload = conn.execute(
        "SELECT payload FROM job WHERE dedupe_key LIKE '%%:apply'").fetchone()["payload"]
    assert payload["allow_renames"] is True
    assert payload["dry_run"] is False
    assert payload["preview_job_id"] == jid


def test_not_ticking_the_box_leaves_allow_renames_false(client, conn):
    """The opt-in must never be inferred. An unticked checkbox posts nothing at
    all, which must read as false rather than as absent-so-default-true."""
    jid = _seed_vendor_preview(conn, result={
        "seen": 1, "added": 0, "renamed": [["001", "OLD", "NEW"]],
        "disappeared": [], "unchanged": 0, "dry_run": True, "written": False})

    client.post(f"/import/{jid}/apply")

    payload = conn.execute(
        "SELECT payload FROM job WHERE dedupe_key LIKE '%%:apply'").fetchone()["payload"]
    assert payload["allow_renames"] is False


def test_an_item_apply_never_grows_an_allow_renames_field(client, conn):
    """`ingest.run` has no meaning for it, and payload fields are additive per
    tag, not sprayed across every tag the route happens to serve."""
    jid, _ = _seed_preview(conn)
    client.post(f"/import/{jid}/apply", data={"allow_renames": "on"})
    payload = conn.execute(
        "SELECT payload FROM job WHERE type='ingest.run' "
        "AND dedupe_key LIKE '%%:apply'").fetchone()["payload"]
    assert "allow_renames" not in payload


def test_the_disappeared_block_says_the_rows_are_kept(client, conn):
    """"1 disappeared" unlabelled reads as "1 deleted". Nothing is ever deleted
    -- the row keeps its stale import_batch."""
    jid = _seed_vendor_preview(conn, result={
        "seen": 1, "added": 0, "renamed": [], "disappeared": ["277"],
        "unchanged": 1, "dry_run": True, "written": False})
    page = client.get(f"/import/{jid}/preview").text
    assert "277" in page
    assert "kept" in page.lower()


def test_a_refused_rename_is_shown_with_the_codes_that_caused_it(client, conn):
    jid = _seed_vendor_preview(conn, dry_run=False, result={
        "seen": 1, "added": 0, "renamed": [["001", "OLD", "NEW"]],
        "disappeared": [], "unchanged": 0, "dry_run": False, "written": False,
        "outcome": "rename-refused",
        "error": "1 code(s) would be re-pointed to a different manufacturer"})
    page = client.get(f"/import/{jid}/preview").text
    assert "refused" in page.lower()
    assert "001" in page


def test_the_vendor_preview_page_works_standalone(client, conn):
    """Slice A shipped an Apply button that was inert when the page was opened
    directly: the partial does not extend base.html, and base.html is what
    loads htmx. Same wrapper, same trap."""
    jid = _seed_vendor_preview(conn)
    page = client.get(f"/import/{jid}/preview").text
    assert "htmx" in page
    assert "#result" not in page


def test_the_item_preview_page_still_works_standalone(client, conn):
    """The wrapper is now parameterised over which partial it includes; the
    item path must not have been broken by that."""
    jid, _ = _seed_preview(conn)
    page = client.get(f"/import/{jid}/preview").text
    assert "htmx" in page
    assert "Rows in file" in page


def test_a_vendor_preview_is_appliable_despite_carrying_no_source_field(client, conn):
    """`_is_appliable_preview` gated on `payload["source"] == "upload"`, which a
    vendor payload does not carry: it is upload-sourced by construction."""
    jid = _seed_vendor_preview(conn)
    assert client.post(f"/import/{jid}/apply").status_code == 200


def test_an_unfinished_vendor_preview_offers_no_apply(client, conn):
    jid = _seed_vendor_preview(conn, status="running", result=None)
    resp = client.get(f"/import/{jid}/preview")
    assert resp.status_code == 200
    assert "/apply" not in resp.text


def test_the_applied_vendor_page_shows_previewed_against_applied(client, conn):
    """Spec §4.5: the same guarantee the item import got. Applying re-reads the
    file and re-diffs against the master as it stands at write time, so the
    numbers may move -- and a move is the one thing worth surfacing."""
    uid = conn.execute(
        "INSERT INTO import_inbox (kind, filename, content) "
        "VALUES ('vendors','Proizvajalci.xlsx',%s) RETURNING id",
        (_vendor_xlsx_bytes(),)).fetchone()["id"]
    prev = _seed_vendor_preview(conn, upload_id=uid, result={
        "seen": 390, "added": 4, "renamed": [], "disappeared": [],
        "unchanged": 386, "dry_run": True, "written": False})
    payload = {"upload_id": uid, "filename": "Proizvajalci.xlsx",
               "dry_run": False, "allow_renames": False, "preview_job_id": prev}
    applied = conn.execute(
        "INSERT INTO job (type, payload, dedupe_key, status, result) "
        "VALUES ('vendor.import', %s, %s, 'done', %s) RETURNING id",
        (Json(payload), f"vendor.import:upload:{uid}:apply",
         Json({"seen": 390, "added": 6, "renamed": [], "disappeared": [],
               "unchanged": 384, "dry_run": False, "written": True})),
    ).fetchone()["id"]
    conn.commit()

    page = client.get(f"/import/{applied}/preview").text
    assert "Previewed" in page and "Applied" in page
    assert "moved since the preview" in page
    assert "New codes" in page


def test_a_vendor_preview_lists_its_counts_at_all(client, conn):
    """The count table is shared with the item path but keyed on the vendor
    report's own fields -- `changed`/`non_md`/`missing_mfr_ref` do not exist on
    a vendor result, so reusing the item rows renders a table of blanks."""
    jid = _seed_vendor_preview(conn, result={
        "seen": 390, "added": 4, "renamed": [["001", "OLD", "NEW"]],
        "disappeared": ["277"], "unchanged": 385, "dry_run": True,
        "written": False})
    page = client.get(f"/import/{jid}/preview").text
    for label in ("Rows in master", "New codes", "Renamed", "Unchanged"):
        assert label in page, label
    assert "390" in page and "385" in page


def test_a_vendor_count_row_holding_a_list_renders_its_length(client, conn):
    """`renamed` and `disappeared` are lists on the report, not integers. The
    count column has to show how many, while the blocks below show which."""
    jid = _seed_vendor_preview(conn, result={
        "seen": 3, "added": 0,
        "renamed": [["001", "A", "B"], ["002", "C", "D"]],
        "disappeared": [], "unchanged": 1, "dry_run": True, "written": False})
    page = client.get(f"/import/{jid}/preview").text
    assert "[['001'" not in page and "&#39;001&#39;, &#39;A&#39;" not in page


def test_both_upload_sections_are_on_the_import_page(client):
    page = client.get("/import").text
    assert 'action="/import"' in page or 'hx-post="/import"' in page
    assert "/import/vendors" in page


def test_the_pipeline_hub_lists_all_five_folded_pages(client):
    """The five machine-operation pages moved behind /pipeline so the sidebar
    could go 25 -> 21 (`[sidebar-is-26-entries]`, UI audit 2026-09-04). Folding
    is a placement, not a permission -- if a page is not on the hub AND not in
    the nav, it has been hidden rather than moved."""
    resp = client.get("/pipeline")
    assert resp.status_code == 200
    for href in ("/inflight", "/data-quality", "/manual", "/dead", "/scheduler"):
        assert f'href="{href}"' in resp.text, href


def test_the_folded_pages_left_the_office_menu(client):
    """The other half: they are on the hub AND out of the office half of the
    menu. Asserted on a page that is not one of them, since each folded page
    still links to itself.

    `/dead`, `/data-quality` and `/scheduler` are in the menu again since
    2026-09-14, but inside the collapsed operator block (office UI redesign
    spec § 7), so what this guards is that none of them is in the three office
    groups above it. `/inflight` is behind the hub as before."""
    import re
    sidebar = re.search(r"<aside\b.*?</aside>", client.get("/items").text, re.S).group(0)
    office = sidebar.split("<details")[0]
    for href in ("/inflight", "/data-quality", "/dead", "/scheduler"):
        assert f'href="{href}"' not in office, href
    assert 'href="/pipeline"' in sidebar


def test_the_pages_that_are_not_machine_operation_stayed_reachable(client):
    """The first cut of the 2026-09-04 fold would have buried all ten Pipeline
    entries, four of which are not machine operation. Three of those four are
    still menu entries; `/reports` left the menu on 2026-09-14 and is reached
    from Today, which is the page it belongs to (office UI redesign spec § 7).
    Burying any of them is the regression this test exists to prevent."""
    nav = client.get("/items").text
    for href in ("/upload", "/import"):
        assert f'href="{href}"' in nav, href
    assert 'href="/reports"' in client.get("/").text


def test_import_is_in_the_nav(client):
    resp = client.get("/items")
    assert '/import' in resp.text


def test_missing_documents_is_in_the_nav(client):
    """It shipped on 2026-09-11 reachable only by typing the URL: no sidebar
    entry, and not on the `/pipeline` hub either, so neither half of the
    "folded, not hidden" rule above covered it. A screen the office is meant to
    work from, that the office cannot find, has not shipped.

    Beside Review rather than behind the hub on purpose: `/missing` is the
    office's own queue (the articles we looked for and could not find), while
    `/manual` is the operator's view of every task kind.
    """
    nav = client.get("/items").text
    assert 'href="/missing"' in nav


def test_ingest_is_reachable_but_not_offered_in_the_nav(client):
    """/ingest is the developer path: a file already inside the container, a
    hand-typed server-side path, and a `bc_odata` source that always
    dead-letters. /import does the same job from an upload with a dry-run diff,
    so listing both only ever routed people to the wrong one
    (`[ingest-page-says-the-import-does-nothing]`, UI audit 2026-09-04).

    Both halves matter. Removing the invitation must not remove the page --
    `dentalia enqueue` is not the only caller, the guide still documents the
    route, and anyone holding the URL keeps it.
    """
    assert client.get("/ingest").status_code == 200
    assert 'href="/ingest"' not in client.get("/items").text
# /api-reference as real documentation (2026-08-25, Denis: "like the api
# documentation that deserves to be read -- which endpoint, what it returns,
# examples").
#
# The response examples are RENDERED FROM THE REGISTRY, not hand-written: the
# page calls the same `item_documents()` the endpoints call. A hand-written
# example is the thing that goes stale silently the next time a field changes,
# and a documented response that no longer matches is worse than none.
# --------------------------------------------------------------------------- #


def test_api_reference_shows_a_real_response_body(client, conn):
    _seed_item_with_doc(conn, "DOC-LIVE")
    resp = client.get("/api-reference")
    assert resp.status_code == 200
    # the real item's real values, not a placeholder
    assert "DOC-LIVE" in resp.text
    assert _rendered("Widget") in resp.text


def test_api_reference_contrasts_full_and_customer_views(client, conn):
    """The whole point of view=customer is which fields disappear. A reader
    must be able to see the difference, not be told it exists."""
    _seed_item_with_doc(conn, "DOC-VIEWS")
    text = client.get("/api-reference").text
    assert "match_basis" in text          # present in the full example
    assert "view=customer" in text
    assert "manufacturer_code" in text    # named as one of the dropped fields


def test_api_reference_documents_every_endpoint(client, conn):
    _seed_item_with_doc(conn, "DOC-ALL")
    text = client.get("/api-reference").text
    for path in (
        "/item/{item_ref}",
        "/item/{item_ref}/documents.zip",
        "/api/items/{item_ref}/documents",
        "/api/documents/{doc_id}",
        "/api/kpi",
        "/documents/{doc_id}/file",
    ):
        assert _rendered(path) in text, f"{path} undocumented"


def test_api_reference_carries_copyable_curl_for_each_credential(client, conn):
    _seed_item_with_doc(conn, "DOC-CURL")
    text = client.get("/api-reference").text
    assert "X-API-Key:" in text
    assert "curl" in text
    assert "?k=" in text


def test_api_reference_explains_why_item_404s(client, conn):
    """The 404-not-403 choice is deliberate and a consumer will otherwise
    report it as a bug."""
    _seed_item_with_doc(conn, "DOC-404")
    text = client.get("/api-reference").text
    assert "404" in text and "403" in text


def test_api_reference_survives_an_empty_registry(client, conn):
    """Cold start: no production links at all. The page must still render."""
    conn.execute("DELETE FROM item_document")
    conn.commit()
    resp = client.get("/api-reference")
    assert resp.status_code == 200
    assert "no items mirrored yet" in resp.text.lower() or "DOC-" in resp.text


# --------------------------------------------------------------------------- #
# renewal contacts (migration 049)
#
# `manufacturer.contact_emails` was declared in 006 and never got a writer: 0
# rows live, so `email_request._contacts` returned [] for every manufacturer and
# every renewal draft was unaddressed. The 2026-08-20 ruling said contacts live
# in the database so a non-engineer can fix one -- these cover the half of that
# ruling that was missing.
# --------------------------------------------------------------------------- #
def _seed_manufacturer_row(conn, canonical: str, emails=None):
    return conn.execute(
        "INSERT INTO manufacturer (canonical_name, contact_emails) VALUES (%s,%s) "
        "ON CONFLICT (canonical_name) DO UPDATE SET contact_emails=EXCLUDED.contact_emails "
        "RETURNING id",
        (canonical, emails),
    ).fetchone()["id"]


def test_contacts_save_is_readable_by_the_handler_that_needs_it(client, conn):
    """End-to-end on the actual bug: what the UI writes is what `email.request`
    reads. Asserting the column would prove the write; asserting through
    `_contacts` proves the drafts get addressed."""
    from app.handlers.email_request import _contacts

    _seed_manufacturer(conn, "CONTACT CO", ["90001"])
    _seed_item(conn, "contact-item-1", "90001")
    _seed_manufacturer_row(conn, "CONTACT CO")
    conn.commit()
    assert _contacts(conn, "CONTACT CO") == []          # the bug, before

    resp = client.post("/manufacturers/CONTACT CO/contacts",
                       data={"contact_emails": "qa@example.com; regulatory@example.com"})

    assert resp.status_code == 200
    assert "2 contact(s) saved" in resp.text
    assert _contacts(conn, "CONTACT CO") == ["qa@example.com", "regulatory@example.com"]


def test_contacts_save_records_who_changed_them(client, conn):
    _seed_manufacturer_row(conn, "AUDIT CO")
    conn.commit()

    client.post("/manufacturers/AUDIT CO/contacts", data={"contact_emails": "a@b.com"})

    row = conn.execute(
        "SELECT updated_by, updated_at FROM manufacturer WHERE canonical_name='AUDIT CO'"
    ).fetchone()
    assert row["updated_by"]
    assert row["updated_at"] is not None


def test_contacts_refuses_a_malformed_address_and_writes_nothing(client, conn):
    _seed_manufacturer_row(conn, "BAD ADDR CO", ["keep@me.com"])
    conn.commit()

    resp = client.post("/manufacturers/BAD ADDR CO/contacts",
                       data={"contact_emails": "not-an-address, ok@example.com"})

    assert resp.status_code == 422
    assert "not an email address: not-an-address" in resp.text
    row = conn.execute(
        "SELECT contact_emails FROM manufacturer WHERE canonical_name='BAD ADDR CO'"
    ).fetchone()
    assert row["contact_emails"] == ["keep@me.com"]     # untouched


def test_contacts_may_be_cleared(client, conn):
    """An empty list is legal, not an error: a draft with no recipient is
    addressed by hand, and the drafts page refuses to release one."""
    _seed_manufacturer_row(conn, "CLEARABLE CO", ["old@example.com"])
    conn.commit()

    resp = client.post("/manufacturers/CLEARABLE CO/contacts",
                       data={"contact_emails": "  "})

    assert resp.status_code == 200
    assert "cleared" in resp.text
    row = conn.execute(
        "SELECT contact_emails FROM manufacturer WHERE canonical_name='CLEARABLE CO'"
    ).fetchone()
    assert row["contact_emails"] == []


def test_contacts_on_an_unseeded_manufacturer_says_to_seed(client, conn):
    """No row means the seed has not run. Creating one here would invent an
    entity `reconcile._entities` never derived."""
    resp = client.post("/manufacturers/NEVER SEEDED CO/contacts",
                       data={"contact_emails": "a@b.com"})

    assert resp.status_code == 422
    assert "manufacturers seed" in resp.text
    assert conn.execute(
        "SELECT count(*) AS n FROM manufacturer WHERE canonical_name='NEVER SEEDED CO'"
    ).fetchone()["n"] == 0


def test_manufacturer_page_says_when_there_is_nowhere_to_save_contacts(client, conn):
    _seed_manufacturer(conn, "UNSEEDED VIEW CO", ["90002"])
    _seed_item(conn, "unseeded-view-1", "90002")
    conn.commit()

    text = client.get("/manufacturers/UNSEEDED VIEW CO").text

    assert "dentalia manufacturers seed" in text
    assert 'name="contact_emails"' not in text


# --------------------------------------------------------------------------- #
# EUDAMED SRN confirm queue
# --------------------------------------------------------------------------- #
# Attribution of a EUDAMED actor to one of our canonical manufacturers is a
# fuzzy name match, and a wrong one becomes a wrong e-mail to a supplier. Exact
# matches auto-store; everything else waits here (Denis, 2026-08-26).
def test_the_srn_queue_lists_only_pending_candidates(client, conn):
    conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES ('QUEUECO'), "
        "('AUTOCO') ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, actor_name, "
        " discovered_via, status, match_score) VALUES "
        "('QUEUECO','XX-MF-1','Queue Co NV','register-fuzzy','pending',91.0),"
        "('AUTOCO','XX-MF-2','Auto Co','register-exact','auto',100.0)")
    conn.commit()

    body = client.get("/manufacturers/srn-queue").text

    assert "XX-MF-1" in body
    assert "XX-MF-2" not in body


def test_confirming_a_candidate_makes_it_sweepable(client, conn):
    conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES ('CONFIRMCO') "
        "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, "
        " status) VALUES ('CONFIRMCO','XX-MF-3','register-fuzzy','pending')")
    conn.commit()

    client.post("/manufacturers/srn-queue/CONFIRMCO/XX-MF-3",
                data={"decision": "confirm"})

    row = conn.execute(
        "SELECT status, decided_at FROM manufacturer_srn WHERE srn='XX-MF-3'"
    ).fetchone()
    assert row["status"] == "confirmed"
    assert row["decided_at"] is not None


def test_rejecting_a_candidate_survives_the_next_register_pull(client, conn):
    """The register is re-pulled monthly. A rejection that reverted would be a
    queue nobody can clear."""
    conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES ('REJECTCO') "
        "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, "
        " status) VALUES ('REJECTCO','XX-MF-4','register-fuzzy','pending')")
    conn.commit()

    client.post("/manufacturers/srn-queue/REJECTCO/XX-MF-4",
                data={"decision": "reject"})

    row = conn.execute(
        "SELECT status FROM manufacturer_srn WHERE srn='XX-MF-4'"
    ).fetchone()
    assert row["status"] == "rejected"


def test_an_unknown_decision_is_refused(client, conn):
    conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES ('BADCO') "
        "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, "
        " status) VALUES ('BADCO','XX-MF-5','register-fuzzy','pending')")
    conn.commit()

    resp = client.post("/manufacturers/srn-queue/BADCO/XX-MF-5",
                       data={"decision": "delete"})

    assert resp.status_code == 400
    row = conn.execute(
        "SELECT status FROM manufacturer_srn WHERE srn='XX-MF-5'"
    ).fetchone()
    assert row["status"] == "pending"


# --------------------------------------------------------------------------- #
# EUDAMED sweep due list and the operator release
# --------------------------------------------------------------------------- #
# Ruling, Denis 2026-08-20, restated 2026-08-26: "No sweep ever runs
# unattended -- an operator releases every run." The scheduler proposes by
# writing `due_at` (migration 044); everything here exercises the one path to
# `released_at`.
def test_releasing_a_sweep_enqueues_the_job(client, conn):
    """The scheduler proposes; this is the release. Denis, 2026-08-20 restated
    2026-08-26: an operator releases every run."""
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('SWEEPCO') "
                 "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, "
        " status) VALUES ('SWEEPCO','XX-MF-7','register-exact','auto')")
    conn.execute(
        "INSERT INTO eudamed_sweep_state (canonical_name, due_at) "
        "VALUES ('SWEEPCO', now()) ON CONFLICT (canonical_name) "
        "DO UPDATE SET due_at = now()")
    conn.commit()

    client.post("/manufacturers/SWEEPCO/sweep")

    job = conn.execute(
        "SELECT type, payload FROM job WHERE type = 'eudamed.sweep' "
        "ORDER BY id DESC LIMIT 1").fetchone()
    assert job["payload"]["manufacturer"] == "SWEEPCO"
    state = conn.execute(
        "SELECT released_at, released_by FROM eudamed_sweep_state "
        "WHERE canonical_name = 'SWEEPCO'").fetchone()
    assert state["released_at"] is not None


def test_the_digest_offers_release_on_a_due_row_and_returns_to_itself(client, conn):
    """The digest is the page that SHOWS what needs doing; before 2026-09-04 it
    was deliberately read-only and sent you to /manufacturers/sweep-due to act.
    Same route, same guard, same releasable set -- only the page you land back
    on differs (`[eudamed-digest-cannot-act]`, UI audit 2026-09-04).
    """
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('DIGESTCO') "
                 "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, "
        " status) VALUES ('DIGESTCO','XX-MF-9','register-exact','auto')")
    conn.execute(
        "INSERT INTO eudamed_sweep_state (canonical_name, due_at) "
        "VALUES ('DIGESTCO', now()) ON CONFLICT (canonical_name) "
        "DO UPDATE SET due_at = now()")
    conn.commit()

    page = client.get("/manufacturers/eudamed")
    assert page.status_code == 200
    assert 'action="/manufacturers/DIGESTCO/sweep"' in page.text
    assert 'name="back" value="/manufacturers/eudamed"' in page.text

    resp = client.post("/manufacturers/DIGESTCO/sweep",
                       data={"back": "/manufacturers/eudamed"},
                       follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/manufacturers/eudamed"
    assert conn.execute(
        "SELECT count(*) c FROM job WHERE type='eudamed.sweep'"
    ).fetchone()["c"] == 1


def test_a_sweep_release_never_redirects_somewhere_the_caller_named(client, conn):
    """`back` arrives in a form post, so echoing it into a Location header would
    be an open redirect. Unrecognised values fall back to the board every
    release went to before the digest grew its own button."""
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('REDIRCO') "
                 "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, "
        " status) VALUES ('REDIRCO','XX-MF-8','register-exact','auto')")
    conn.execute(
        "INSERT INTO eudamed_sweep_state (canonical_name, due_at) "
        "VALUES ('REDIRCO', now()) ON CONFLICT (canonical_name) "
        "DO UPDATE SET due_at = now()")
    conn.commit()

    resp = client.post("/manufacturers/REDIRCO/sweep",
                       data={"back": "https://evil.example/steal"},
                       follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/manufacturers/sweep-due"


def test_the_digest_shows_no_release_button_on_a_row_that_is_not_due(client, conn):
    """A row the scheduler has NOT proposed carries no button at all, not a
    disabled one: 27 rows of greyed-out controls is noise, and the ruling is
    that a person releases what the scheduler proposed."""
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('NOTDUECO') "
                 "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO eudamed_sweep_state (canonical_name, due_at) "
        "VALUES ('NOTDUECO', now() + interval '30 days') "
        "ON CONFLICT (canonical_name) DO UPDATE SET due_at = now() + interval '30 days'")
    conn.commit()

    page = client.get("/manufacturers/eudamed")
    assert "NOTDUECO" in page.text
    assert 'action="/manufacturers/NOTDUECO/sweep"' not in page.text


def test_a_manufacturer_with_no_trusted_srn_cannot_be_released(client, conn):
    """Releasing a sweep with nothing to sweep would burn the politeness lease
    and report success."""
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('NOSRNCO') "
                 "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO eudamed_sweep_state (canonical_name, due_at) "
        "VALUES ('NOSRNCO', now()) ON CONFLICT (canonical_name) "
        "DO UPDATE SET due_at = now()")
    conn.commit()

    resp = client.post("/manufacturers/NOSRNCO/sweep")

    assert resp.status_code == 400
    assert conn.execute(
        "SELECT count(*) c FROM job WHERE type='eudamed.sweep'"
    ).fetchone()["c"] == 0


def test_the_due_list_renders(client, conn):
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('DUEUI') "
                 "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO eudamed_sweep_state (canonical_name, due_at) "
        "VALUES ('DUEUI', now() - interval '1 day') "
        "ON CONFLICT (canonical_name) DO UPDATE SET due_at = now()")
    conn.commit()

    assert "DUEUI" in client.get("/manufacturers/sweep-due").text


def test_releasing_an_already_queued_sweep_does_not_restamp(client, conn):
    """`queue.enqueue` returns None when an active eudamed.sweep job already
    holds the dedupe key -- the run underway was released by the FIRST click,
    and a second release must not overwrite that audit with this one's
    timestamp/identity, even though both clicks "succeed" from the caller's
    point of view."""
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('DEDUPECO') "
                 "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, "
        " status) VALUES ('DEDUPECO','XX-MF-8','register-exact','auto')")
    conn.execute(
        "INSERT INTO eudamed_sweep_state (canonical_name, due_at) "
        "VALUES ('DEDUPECO', now()) ON CONFLICT (canonical_name) "
        "DO UPDATE SET due_at = now()")
    conn.commit()

    client.post("/manufacturers/DEDUPECO/sweep")

    first = conn.execute(
        "SELECT released_at, released_by FROM eudamed_sweep_state "
        "WHERE canonical_name = 'DEDUPECO'").fetchone()
    assert first["released_at"] is not None
    assert conn.execute(
        "SELECT count(*) c FROM job WHERE type = 'eudamed.sweep' "
        "AND payload->>'manufacturer' = 'DEDUPECO'"
    ).fetchone()["c"] == 1

    # The job from the first release is untouched -- still 'pending', nothing
    # has claimed it -- so it still holds the dedupe key (C2: dedupe is
    # scoped to status IN ('pending','running','failed')). This second
    # release must be a no-op against eudamed_sweep_state.
    client.post("/manufacturers/DEDUPECO/sweep")

    second = conn.execute(
        "SELECT released_at, released_by FROM eudamed_sweep_state "
        "WHERE canonical_name = 'DEDUPECO'").fetchone()
    assert second["released_at"] == first["released_at"]
    assert second["released_by"] == first["released_by"]
    assert conn.execute(
        "SELECT count(*) c FROM job WHERE type = 'eudamed.sweep' "
        "AND payload->>'manufacturer' = 'DEDUPECO'"
    ).fetchone()["c"] == 1


# --------------------------------------------------------------------------- #
# Certificate findings (Task 6's six views, on screen)
# --------------------------------------------------------------------------- #
# Task 6 shipped `held_certificate`, `trusted_manufacturer_srn`,
# `certificate_drift`, `certificate_drift_candidate`, `certificate_status_alert`
# and `certificate_gap`. This puts the four findings on screen: a section on
# /expiry and a canonical_name-scoped panel on the manufacturer page.
#
# seeded_doc builds through its own `conn` and is read back inside the same
# transaction with no commit needed -- but every test here that also drives
# the TestClient hits a SEPARATE connection, so every one of those must
# `conn.commit()` before calling `client.get(...)` or the app sees nothing.
def test_expiry_shows_certificate_drift(client, conn, seeded_doc):
    seeded_doc(type="EC", cert_number="G15 043306 0282 Rev. 00",
               canonical="IVOCLAR")
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('IVOCLAR') "
                 "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, "
        " status) VALUES ('IVOCLAR','LI-MF-000000522','register-exact','auto') "
        "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO eudamed_certificate (certificate_number, revision_number, "
        " actor_srn, certificate_status, synced_at) "
        "VALUES ('G15 043306 0282','Rev. 02','LI-MF-000000522',"
        "        'supplemented', now()) ON CONFLICT DO NOTHING")
    conn.commit()

    body = client.get("/expiry").text

    assert "Rev. 02" in body
    assert "G15 043306 0282" in body


def test_the_findings_state_their_denominator(client, conn):
    """A finding list without its denominator reads as "we checked our
    certificates" when reach is a fraction of the corpus (spec S5.8)."""
    body = client.get("/expiry").text
    assert "matched" in body.lower()


def test_a_stale_mirror_says_so(client, conn):
    """EUDAMED is an enhancement, never a main path. Stale data must be visibly
    stale rather than silently wrong."""
    body = client.get("/expiry").text
    assert "synced" in body.lower() or "never synced" in body.lower()


def test_certificate_alerts_render_before_drift_and_gaps(client, conn):
    """Order of urgency: a withdrawn/suspended certificate is the sharpest
    signal on this board and is NEVER a document request -- it renders before
    the drift table and before the certificate gaps, even with nothing
    seeded."""
    body = client.get("/expiry").text
    alerts_pos = body.find("Certificate status alert")
    drift_pos = body.find("Revision drift")
    gaps_pos = body.find("hold no copy")
    assert -1 not in (alerts_pos, drift_pos, gaps_pos)
    assert alerts_pos < drift_pos < gaps_pos


def test_near_miss_candidates_render_separately_and_collapsed(client, conn, seeded_doc):
    """A looser spelling match is shown for a human to confirm, never mixed
    into the drift table the way a real join would be (standing ruling,
    2026-08-26). Same fixture data as
    test_a_looser_match_is_a_possible_match_not_a_join in
    tests/test_eudamed_views.py -- known to trigger the trailing-letter rule
    and nothing stronger."""
    seeded_doc(type="EC", cert_number="HZ 1470094-1 G", canonical="NEARCO")
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('NEARCO') "
                 "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, "
        " status) VALUES ('NEARCO','XX-MF-NEAR','register-exact','auto')")
    conn.execute(
        "INSERT INTO eudamed_certificate (certificate_number, revision_number, "
        " actor_srn, certificate_status, synced_at) VALUES "
        "('HZ 1470094-1','Rev. 00','XX-MF-NEAR','issued', now())")
    conn.commit()

    body = client.get("/expiry").text

    # our raw number appears exactly once, and only inside the collapsed
    # candidates block -- never asserted as a join, never duplicated into the
    # drift table above it. Anchored on the candidates summary text rather
    # than the first "<details" on the page: `ui.intro()` already opens one
    # of those above this section (the page's own explanatory disclosure).
    assert body.count("HZ 1470094-1 G") == 1
    summary_pos = body.find("possible match")
    candidate_pos = body.find("HZ 1470094-1 G")
    details_close_pos = body.find("</details>", summary_pos)
    assert summary_pos != -1 and summary_pos < candidate_pos < details_close_pos


def test_manufacturer_page_scopes_its_own_certificate_gap(client, conn, playbooks_dir):
    """`cert_findings` is scoped by canonical_name on this page -- a gap that
    belongs to a different manufacturer must not bleed onto this one's, and
    the manufacturer page must actually render the section at all."""
    _seed_manufacturer(conn, "GAPMFR", ["GAPMFR"])
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('GAPMFR') "
                 "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, "
        " status) VALUES ('GAPMFR','XX-MF-GAP','register-exact','auto')")
    conn.execute(
        "INSERT INTO eudamed_certificate (certificate_number, revision_number, "
        " actor_srn, certificate_status, synced_at) VALUES "
        "('GAP-CERT-1','Rev. 00','XX-MF-GAP','issued', now())")
    conn.commit()

    resp = client.get("/manufacturers/GAPMFR")

    assert resp.status_code == 200
    assert "GAP-CERT-1" in resp.text


def test_the_coverage_counts_documents_it_cannot_attribute(conn, seeded_doc):
    """An EC certificate with no production item link cannot be attributed to a
    manufacturer, so it never reaches held_certificate -- and certificate_gap
    would then report it missing and generate a request for a document we
    already hold. Measured 2026-08-26, EC/ISO documents carrying a
    cert_number only: 43 carry one, 34 have no production item link
    (unattributable), 9 reach held_certificate -- 34 + 9 = 43."""
    # held, and attributable: the ordinary path, with a production item link
    seeded_doc(type="EC", cert_number="ATTR-001", canonical="ATTRCO")
    # an EC document carrying a cert_number but NO item_document row at all --
    # cannot reach a canonical_manufacturer through any join, so it can never
    # appear in held_certificate, matched, or unmatched
    conn.execute(
        "INSERT INTO document (type, regulation, validity_from, validity_to, "
        "status, content_hash, archive_url, coverage_scope, cert_number) "
        "VALUES ('EC','MDR','2022-01-01','2031-01-01','production',"
        "'h-unattr-1','/archive/unattr.pdf','group','UNATTR-001')"
    )

    findings = registry.certificate_findings(conn)

    assert findings["coverage"]["unattributable"] == 1


def test_manufacturer_page_shows_its_eudamed_declaration_gap(client, conn, playbooks_dir):
    """The panel this whole feature exists for: EUDAMED says a manufacturer
    registered a device we hold no declaration for. This manufacturer has
    never been swept, so the panel must read "never swept" -- not "no gaps",
    which is a different fact ruling 24 (migration 046) exists to keep from
    being conflated."""
    canonical = f"GAPPANEL-{uuid.uuid4().hex[:8]}"
    srn = f"XX-MF-{uuid.uuid4().hex[:6]}"
    item_ref = f"GP-{uuid.uuid4().hex[:10]}"

    _seed_manufacturer(conn, canonical, [canonical])
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, "
        "catalogue, updated_at) VALUES (%s,'Widget',%s,TRUE,'LJ',now())",
        (item_ref, canonical),
    )
    group_id = conn.execute(
        "INSERT INTO item_group (canonical_manufacturer) VALUES (%s) RETURNING group_id",
        (canonical,),
    ).fetchone()["group_id"]
    conn.execute(
        "INSERT INTO item_group_member (group_id, item_ref, match_basis) "
        "VALUES (%s,%s,'manual')", (group_id, item_ref),
    )
    conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES (%s) ON CONFLICT DO NOTHING",
        (canonical,),
    )
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, status) "
        "VALUES (%s, %s, 'register-exact', 'auto')", (canonical, srn),
    )
    conn.execute(
        "INSERT INTO eudamed_mirror (udi_di, basic_udi_di, reference, "
        " manufacturer_srn, synced_at) VALUES (%s,%s,%s,%s, now())",
        (f"UDI-{item_ref}", "BUDI-PANEL", item_ref, srn),
    )
    conn.commit()

    resp = client.get(f"/manufacturers/{canonical}")

    assert resp.status_code == 200
    assert "BUDI-PANEL" in resp.text
    # RULING 24's distinction is unchanged; only the word is. "sweep" reads as
    # "EUDAMED check" since P7b (§ 9), so a manufacturer nobody has compared
    # says "never checked" rather than a zero.
    assert "never checked" in resp.text
def test_preview_visited_directly_is_a_full_page_that_can_run_its_own_controls(client, conn):
    """The preview URL is handed to the operator as an `href` by `_result.html`,
    so it must survive a bookmark, a reload, or a middle-click.

    Asserting the hx-* attributes are present is not enough and was the gap that
    let this ship: htmx itself is loaded by `base.html`, so a bare partial
    renders `hx-post` markup that nothing on the page interprets and the Apply
    button silently does nothing. Measured against the running app 2026-08-26 —
    the click enqueued no job. Pin the transport, not just the attributes.
    """
    jid, _ = _seed_preview(conn)

    direct = client.get(f"/import/{jid}/preview")
    assert direct.status_code == 200
    assert "htmx" in direct.text, "direct visit must load htmx or its controls are inert"
    assert "/apply" in direct.text

    # The HTMX-driven fetch still gets the bare fragment -- swapping a whole
    # page into `#result` would nest a second <html> inside the current one.
    fragment = client.get(f"/import/{jid}/preview", headers={"HX-Request": "true"})
    assert fragment.status_code == 200
    assert "htmx" not in fragment.text
    assert "/apply" in fragment.text


# --------------------------------------------------------------------------- #
# The gap-request button. A producer: it enqueues `email.request` with
# `reason='eudamed-gap'` and writes no draft itself (invariant 1).
# --------------------------------------------------------------------------- #

def test_gap_request_button_enqueues_and_writes_no_draft(client, conn):
    _seed_gap_manufacturer(conn)

    resp = client.post("/manufacturers/CARL MARTIN/gap-request",
                       follow_redirects=False)

    assert resp.status_code == 303
    job = conn.execute(
        "SELECT type::text AS t, payload FROM job "
        "WHERE type::text = 'email.request'").fetchone()
    assert job["payload"]["reason"] == "eudamed-gap"
    assert job["payload"]["manufacturer"] == "CARL MARTIN"
    # The button never writes the registry or the draft -- the handler does.
    assert conn.execute(
        "SELECT count(*) AS n FROM email_draft").fetchone()["n"] == 0


def test_gap_request_button_refuses_when_there_is_no_gap(client, conn):
    """Enqueueing a job that can only no-op reads as success and teaches the
    operator to distrust the button -- the same reason the sweep button refuses
    an untrusted SRN."""
    resp = client.post("/manufacturers/IVOCLAR/gap-request",
                       follow_redirects=False)

    assert resp.status_code == 400
    assert conn.execute(
        "SELECT count(*) AS n FROM job WHERE type::text='email.request'"
    ).fetchone()["n"] == 0


def test_the_gap_request_button_renders_only_when_there_is_a_gap(client, conn):
    """The button is inside `{% if summary.gaps %}`, so it is absent for the
    manufacturers that have nothing to ask for -- which is most of them, and
    is what makes "no button" look like a bug when it is the design.

    Asserts the rendered HTML rather than the route, because the failure this
    guards against is a template that stops emitting the form while the POST
    handler goes on working perfectly."""
    _seed_gap_manufacturer(conn)
    # `manufacturer_detail` returns None -- and the route 404s -- unless the
    # name has at least one alias. The page is keyed on the BC code, not on the
    # group.
    conn.execute(
        "INSERT INTO manufacturer_alias (canonical_name, raw_name, source) "
        "VALUES ('CARL MARTIN','022','playbook') ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO manufacturer_alias (canonical_name, raw_name, source) "
        "VALUES ('KOMET','099','playbook') ON CONFLICT DO NOTHING")
    conn.commit()

    with_gap = client.get("/manufacturers/CARL MARTIN").text
    assert 'action="/manufacturers/CARL MARTIN/gap-request"' in with_gap
    assert "Draft request for these 1 group(s)" in with_gap

    conn.execute(
        "INSERT INTO item_group (canonical_manufacturer, label) "
        "VALUES ('KOMET', 'fam')")
    conn.commit()
    without = client.get("/manufacturers/KOMET").text
    assert "gap-request" not in without


# --------------------------------------------------------------------------- #
# Three assertions nothing made before: the active menu entry
# --------------------------------------------------------------------------- #
# `_pulse.html` widens Today to `/reports` and Items to `/coverage` and
# `/discovery`, because those three left the menu (spec § 7) and are reached
# from the entry that lights for them. `grep -rn 'class="active"' tests/` found
# nothing, so a page could quietly lose its visible parent.
@pytest.mark.parametrize("path,entry,label", [
    ("/", "/", "Today"),
    ("/reports", "/", "Today"),
    ("/items", "/items", "Items"),
    ("/coverage", "/items", "Items"),
    ("/discovery", "/items", "Items"),
])
def test_a_page_lights_the_menu_entry_it_is_reached_from(client, path, entry, label):
    text = client.get(path).text

    active = re.findall(r'<a href="([^"]+)" class="[^"]*\bactive\b[^"]*">([^<]*)',
                        text)
    assert len(active) == 1, f"{path} lights {active}"
    assert active[0][0] == entry
    assert active[0][1].strip().startswith(label)
