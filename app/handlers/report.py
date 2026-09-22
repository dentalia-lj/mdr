"""`report.weekly` (S1.5) — handbook §9a. Read-only snapshot: no writes, no
downstream jobs (payload immutability/C9 — a report reflects "now", not
enqueue time). `expiring_documents` is shared with the scheduler's own
expiry-scan tick (docs/specs/scheduler.md §3) — both read the same production
visibility as everything else (PRD §9): `document.status = 'production'`.
"""

from __future__ import annotations

import datetime as dt
import html
import logging
import pathlib

from app.compliance import EXPIRY_HORIZON_DAYS
from app.config import load_config
from app.handlers import register

log = logging.getLogger("dentalia.handler.report")


def expiring_documents(conn, *, horizon_days: int, today: dt.date | None = None) -> list[dict]:
    """Production documents lapsing within `horizon_days`.

    When a document lapses is decided by `document_effective_expiry`
    (migration 027), not here -- three consumers share that view so the
    definition cannot drift again. All three grounds apply at once and the
    EARLIEST fires -- Dentalia ticked both the certificate rule and the
    five-year rule on 2026-08-18, and two rules that both apply means whichever
    comes first. `basis` names the one that won:

      stated     the document's own validity_to
      inherited  the production/superseded certificate it cites (a rejected
                 certificate lends nothing -- 2026-08-14)
      staleness  five years after issue, for a DoC. A REVIEW horizon, not a
                 legal expiry (MDR Annex IV requires no expiry on a declaration
                 at all; Article 19(1) requires it be kept continuously
                 updated). It fires even when the document states a LATER date
                 of its own -- "osvežite vsako izjavo, starejšo od 5 let" is
                 every declaration. Class I has no certificate to chase, so this
                 is the only trigger it will ever have.

    A NULL `expires` is nothing to chase and never enters the loop: a DoC with
    neither an expiry, a cited certificate, nor an issue date, or a certificate
    missing its own mandatory expiry (a data defect, which must surface as one
    rather than be papered over with a synthetic date).

    `validity_to` is kept as the output key so the report, the scheduler and the
    templates keep reading what they always read.

    `manufacturer` is the document's CONFIRMED manufacturer (migration 053), or,
    while that is still undecided, the first of the manufacturers its production
    links reach -- the path /expiry and the renewal chase address, picked the
    same deterministic way `email_request.manufacturers_for_docs` picks. None
    when neither exists. The weekly report read this key for its Manufacturer
    column from the day it was written, and the query never returned it, so the
    column was empty on every row (W36 and W37: 86 of 86).

    Every row carries `lapsed`: whether the date is already behind us. The
    horizon has no lower bound and must not grow one -- a certificate that
    lapsed needs chasing MORE than one lapsing next month, so dropping it from
    the scan would stop the chase exactly where it matters. What was wrong was
    calling it upcoming. Measured on the live registry 2026-08-18: a 30-day
    scan returned 61 documents and not one of them was in the future, the
    oldest lapsed 2237 days ago -- so with `expiry_email_enabled` on, all 61
    would have been sent "your certificate is about to expire". The flag was
    off and the scheduler was not running, so nothing fired; the label was
    still wrong. Callers decide what to do with each state; this function only
    refuses to conflate them (same split as `/expiry`, which partitions rather
    than filters for the same reason)."""
    if today is None:
        today = dt.date.today()
    rows = conn.execute(
        """
        SELECT d.doc_id, d.type, d.regulation,
               COALESCE(
                 d.canonical_manufacturer,
                 (SELECT min(g.canonical_manufacturer)
                    FROM item_document l
                    JOIN item_group_member gm ON gm.item_ref = l.item_ref
                    JOIN item_group g ON g.group_id = gm.group_id
                   WHERE l.doc_id = d.doc_id AND l.status = 'production')
               ) AS manufacturer,
               e.expires AS validity_to, e.inherited, e.basis,
               (e.expires < %s) AS lapsed
          FROM document d
          JOIN document_effective_expiry e USING (doc_id)
         WHERE d.status = 'production'
           AND e.expires IS NOT NULL
           AND e.expires <= %s
         ORDER BY e.expires
        """,
        (today, today + dt.timedelta(days=horizon_days)),
    ).fetchall()
    return list(rows)


def job_counts_by_type_status(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT type, status, count(*) AS count
          FROM job
         GROUP BY type, status
         ORDER BY type, status
        """
    ).fetchall()
    return list(rows)


def dead_job_count(conn) -> int:
    row = conn.execute("SELECT count(*) AS c FROM job WHERE status='dead'").fetchone()
    return row["c"]


def failure_spikes(
    conn,
    *,
    window_days: int,
    miss_rate_threshold: float,
    min_sample: int,
    now: dt.datetime,
) -> list[dict]:
    """Manufacturers whose `discovery_log` miss-rate over the trailing
    `window_days` exceeds `miss_rate_threshold`, gated by `min_sample` (avoids
    flagging a manufacturer off 1-2 groups). Shared with SCHEDULER's
    failure-monitor tick (docs/specs/scheduler.md §3) — same split as
    `expiring_documents`: the query lives here, the emit-or-not decision
    stays in the scheduler."""
    window_start = now - dt.timedelta(days=window_days)
    rows = conn.execute(
        """
        SELECT ig.canonical_manufacturer AS manufacturer,
               count(*) FILTER (WHERE dl.outcome = 'miss') AS misses,
               count(*) AS total
          FROM discovery_log dl
          JOIN item_group ig ON ig.group_id = dl.group_id
         WHERE dl.at >= %s
         GROUP BY ig.canonical_manufacturer
        """,
        (window_start,),
    ).fetchall()

    spikes = []
    for row in rows:
        if row["total"] < min_sample:
            continue
        miss_rate = row["misses"] / row["total"]
        if miss_rate <= miss_rate_threshold:
            continue
        spikes.append(
            {"manufacturer": row["manufacturer"], "miss_rate": miss_rate, "window_days": window_days}
        )
    return spikes


def handle_report_weekly(conn, job: dict) -> dict:
    cfg = load_config()
    period_key = job["payload"]["period_key"]
    # D7 (office UI redesign, 2026-09-11): one expiring window for every figure
    # that says "expiring" -- this report, the header strip and /expiry's
    # default. The web side names it `web.app.EXPIRING_WINDOW_DAYS`, which IS
    # this constant: the worker image carries no `web` package, and this one is
    # already guarded equal to the scheduler's renewal clock
    # (`tests/test_compliance.py`).
    horizon = EXPIRY_HORIZON_DAYS
    # Two states, counted apart. "61 expiring" was true of nothing on the live
    # registry: all 61 had already lapsed. `expiring` keeps its meaning and its
    # key (consumers read it), and `lapsed` is stated beside it rather than
    # hidden inside it.
    documents = expiring_documents(conn, horizon_days=horizon)
    # F34: three states, not two. A declaration past Dentalia's own five-year
    # review horizon (`basis='staleness'`) has not lapsed -- Article 19(1) sets
    # no validity period -- so reporting it as lapsed states a house rule as a
    # regulatory breach. `app/compliance.py` has drawn this line on the item
    # card since 2026-08-24 and `/expiry` draws it now; this is the third
    # surface. `lapsed` keeps its meaning and its key (consumers read it) and
    # loses only the rows that never belonged in it.
    lapsed = [d for d in documents
              if d["lapsed"] and d["basis"] != "staleness"]
    review_due = [d for d in documents
                  if d["lapsed"] and d["basis"] == "staleness"]
    result = {
        "period_key": period_key,
        "window_days": horizon,
        "expiring": [d for d in documents if not d["lapsed"]],
        "lapsed": lapsed,
        "review_due": review_due,
        "job_counts": job_counts_by_type_status(conn),
        "dead_jobs": dead_job_count(conn),
    }
    written = _write_html(cfg.scheduler.report_dir, result)
    if written:
        result["html_path"] = written

    log.info(
        "report.weekly %s: %d expiring within %dd, %d already lapsed, "
        "%d due review, %d dead jobs",
        period_key,
        len(result["expiring"]),
        horizon,
        len(lapsed),
        len(review_due),
        result["dead_jobs"],
    )
    return result


def _write_html(report_dir: str, result: dict) -> str | None:
    """Write the week's report as a file, and return where it went.

    The report has always existed and has never left `job.result`: to read last
    week's you opened `/scheduler`, and to read the week before that you did
    not. A file per period is the smallest thing that makes it durable and
    linkable, and it lands in the archive volume so it survives a container
    restart exactly as an archived document does.

    Empty `report_dir` writes nothing and says nothing -- the same
    inert-until-configured shape `ingest_watch_dir` uses, so a machine that has
    not been told where to put reports does not fail a cron over it.

    Deliberately hand-rolled HTML rather than a Jinja template: the worker image
    carries no templates (they live in `web/`), and a report the worker cannot
    render without the web package would couple the two for one page. Every
    interpolated value goes through `html.escape` -- document names come from
    PDFs we did not write.

    Rows read the keys `expiring_documents` returns. Until 2026-09-11 they read
    `manufacturer` (never selected) and `expires` (selected AS `validity_to`),
    and the one test fed the template the same wrong keys, so both columns were
    empty on every row while the suite stayed green. Each row links to the
    document: the file is served by `/reports/{name}`, so `/documents/{id}`
    resolves on the same host.
    """
    if not report_dir:
        return None
    out = pathlib.Path(report_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"report-{result['period_key']}.html"
    generated = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    # Older envelopes carry no `window_days`; the constant is what they used.
    window = result.get("window_days") or EXPIRY_HORIZON_DAYS
    expiring, lapsed = len(result["expiring"]), len(result["lapsed"])
    # `.get`, not `[...]`: a report re-rendered from an envelope written before
    # F34 has no such key, and an older report must still open.
    review = len(result.get("review_due") or [])
    dead = result.get("dead_jobs") or 0

    def rows(docs: list[dict]) -> str:
        if not docs:
            return '<tr><td colspan="4">Nothing.</td></tr>'
        return "".join(
            '<tr><td><a href="/documents/{id}">Document #{id}</a></td>'
            "<td>{}</td><td>{}</td><td>{}</td></tr>".format(
                html.escape(str(d.get("type") or "")),
                html.escape(str(d.get("manufacturer") or "not known")),
                html.escape(str(d.get("validity_to") or "")),
                id=html.escape(str(d.get("doc_id", ""))),
            )
            for d in docs
        )

    body = f"""<!doctype html>
<meta charset="utf-8">
<title>Weekly report {html.escape(result['period_key'])}</title>
<style>
 body {{ font: 14px system-ui, sans-serif; margin: 2rem; max-width: 60rem; }}
 table {{ border-collapse: collapse; width: 100%; margin: 0 0 2rem; }}
 th, td {{ border-bottom: 1px solid #ddd; padding: 4px 8px; text-align: left; }}
 .meta {{ color: #666; }}
</style>
<h1>Weekly report {html.escape(result['period_key'])}</h1>
<p class="meta">Generated {generated}.</p>
<p>Expiring in the next {window} days: {expiring} · Already expired: {lapsed}
 · Due review: {review}</p>
<p class="meta">The system has {dead} failed task{'' if dead == 1 else 's'}, listed
 under Failed tasks.</p>
<h2>Already expired ({lapsed})</h2>
<p class="meta">Every published document whose stated date has passed, however long ago.
 Declarations past Dentalia's own five-year review point are listed separately below.</p>
<table><tr><th>Document</th><th>Type</th><th>Manufacturer</th><th>Expired</th></tr>
{rows(result['lapsed'])}</table>
<h2>Expiring in the next {window} days ({expiring})</h2>
<table><tr><th>Document</th><th>Type</th><th>Manufacturer</th><th>Expires</th></tr>
{rows(result['expiring'])}</table>
<h2>Due review ({review})</h2>
<p class="meta">Five years past issue, with no expiry stated and no certificate to
 inherit one from. Nothing has lapsed: ask the manufacturer to confirm the declaration
 is still current, rather than to renew it.</p>
<table><tr><th>Document</th><th>Type</th><th>Manufacturer</th><th>Due since</th></tr>
{rows(result.get('review_due') or [])}</table>
"""
    path.write_text(body, encoding="utf-8")
    return str(path)


register("report.weekly", handle_report_weekly)
