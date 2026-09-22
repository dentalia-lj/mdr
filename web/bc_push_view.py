"""The two ways a person reaches the Business Central writeback.

Producer only, like every other POST in this app: both routes enqueue `bc.push`
and neither opens a connection to BC. An outbound call inside a request handler
would sit outside the domain lease and the retry ladder, and would make a page's
response time depend on somebody else's ERP.

The bulk flow is preview-then-apply, the shape `/import` already uses. The
preview is a QUERY, not a spool: a stored preview can go stale between the two
presses, and applying a stale diff writes yesterday's answer. Recomputing costs
one pass and cannot be wrong.
"""

from __future__ import annotations

import datetime as dt

from fastapi import Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import bc_fields
from app import queue
from app.config import load_config
from app.handlers import bc_push

#: The preview never offers more than the drift cron would take in a day. A
#: page that lists 16.000 rows is not a decision aid, and the cron covers the
#: rest on its own schedule.
PREVIEW_LIMIT = 1000

#: No LIMIT -- review finding I1: an SQL-limited sample diffed only ITS 1.000
#: rows made "changing"/"unchanged" and the row-cap note disagree (a sample
#: could be "3 to send" while the note claimed "the first 1.000 of 1.247
#: differences"). Measured against the live catalogue 2026-09-11 (4.701
#: candidates, the whole item_mirror/item_document join, no bc_push_log
#: rows -- writes have never run): candidate list 16ms, holdings join 73ms,
#: last-sent join 9ms, the Python diff loop 31ms, 128ms end to end -- inside
#: the controller's 1s line by 8x. `plan()` diffs everything and caps only
#: the DISPLAY list, in Python, after the diff, so the heading and the
#: row-cap note are computed from the same number and can never disagree.
#: Revisit this if the item_document-linked slice of the catalogue grows to
#: something like 10x today's -- the 1.000 cap stays a display/apply-batch
#: limit either way, this comment is only about whether the diff itself
#: stays cheap enough to compute in full on every page load.
_CANDIDATES = """
SELECT m.item_ref
  FROM item_mirror m
  JOIN item_document d ON d.item_ref = m.item_ref
  LEFT JOIN bc_push_log l ON l.item_ref = m.item_ref
 GROUP BY m.item_ref
 ORDER BY max(l.pushed_at) ASC NULLS FIRST, m.item_ref
"""

#: **The stage's own query, not a second one.** A preview that computes the rule
#: differently from the job is worse than no preview: it is a wrong answer an
#: operator acts on. This one used to join `eudamed_certificate` on the bare
#: `cert_number`, unscoped by SRN and unfiltered by revision, while
#: `bc.push` had gone SRN-scoped on 2026-09-11 — so for 55 of the
#: 123 production documents carrying a certificate number (640 production-linked
#: items, measured 2026-09-14) the page could show a foreign company's
#: withdrawal and the run would send the opposite. See `bc_push._HOLDINGS_SELECT`.
_HOLDINGS = bc_push.HOLDINGS_FOR_MANY_SQL

_LAST_SENT = """
SELECT DISTINCT ON (item_ref, field) item_ref, field, new_value
  FROM bc_push_log
 WHERE item_ref = ANY(%s) AND http_status BETWEEN 200 AND 299
 ORDER BY item_ref, field, pushed_at DESC, id DESC
"""


def _wire(value) -> str:
    return "true" if value is True else "false" if value is False else str(value)


def plan(conn, cfg, *, limit: int = PREVIEW_LIMIT) -> dict:
    """What a bulk run would send, without sending it.

    Diffs the WHOLE candidate pool, not a sample -- see the note on
    `_CANDIDATES`. `limit` caps only the DISPLAY list, applied here in
    Python after the diff, so `counts["changing"]` (the heading) and
    `total_differences` (the row-cap note) are always the same number.
    """
    refs = [r["item_ref"] for r in conn.execute(_CANDIDATES).fetchall()]
    if not refs:
        return {"rows": [], "counts": {"changing": 0, "unchanged": 0},
                "total_differences": 0}

    holdings: dict[str, list] = {ref: [] for ref in refs}
    for r in conn.execute(_HOLDINGS, (refs,)).fetchall():
        holdings[r["item_ref"]].append(r)

    last: dict[str, dict[str, str]] = {}
    for r in conn.execute(_LAST_SENT, (refs,)).fetchall():
        last.setdefault(r["item_ref"], {})[r["field"]] = r["new_value"]

    today = dt.date.today()
    rows, unchanged = [], 0
    for ref in refs:
        fields = bc_fields.fields_for(
            ref, holdings[ref], processed=bool(holdings[ref]), today=today,
            base_url=cfg.public_base_url, link_key=cfg.bc_link_key,
        )
        if fields is None:
            continue
        sent = last.get(ref, {})
        changed = {k: v for k, v in fields.items() if sent.get(k) != _wire(v)}
        if not changed:
            unchanged += 1
            continue
        rows.append({"item_ref": ref, "changed": changed,
                     "first_send": not sent})
    return {"rows": rows[:limit],
            "counts": {"changing": len(rows), "unchanged": unchanged},
            "total_differences": len(rows)}


def register_routes(app, templates, conn_factory, cfg, batch: int = 200,
                    require_operator=None) -> None:
    """`require_operator` is `web/access.py::operator_guard`, built in
    `web/app.py` where `cfg` lives. Only the bulk apply is a D3 write:
    it is the one that reaches an outside system with every difference at
    once. The preview page stays open, and so does the single-item push
    from an item page, which is one article a person is already looking at.

    None means no route-level guard, for callers that build these routes
    directly in a test."""

    def _operator_only():
        return [Depends(require_operator)] if require_operator else []

    @app.post("/items/{item_ref:path}/bc-push")
    def item_bc_push(item_ref: str):
        """Push this one item now. Enqueues; never writes.

        Dedupe is per item per day, the same shape the re-discover button uses:
        pressing twice while one is waiting is not two pushes, and a terminal
        job never blocks tomorrow's.
        """
        with conn_factory() as conn:
            today = conn.execute("SELECT current_date AS d").fetchone()["d"]
            jid = queue.enqueue(
                conn, "bc.push",
                {"run_id": f"item:{item_ref}", "item_refs": [item_ref]},
                f"bc.push:item:{item_ref}:{today.isoformat()}",
                priority="interactive",
            )
            conn.commit()
        outcome = "queued" if jid is not None else "deduped"
        return RedirectResponse(f"/items/{item_ref}?bc_push={outcome}",
                                status_code=303)

    @app.get("/bc-push", response_class=HTMLResponse)
    def bc_push_preview(request: Request):
        with conn_factory() as conn:
            result = plan(conn, cfg)
        total = result["total_differences"]
        row_cap_note = (
            f"Showing the first {PREVIEW_LIMIT:,} of {total:,} differences."
            if total > PREVIEW_LIMIT else None
        )
        return templates.TemplateResponse(
            request, "bc_push.html",
            {"rows": result["rows"], "counts": result["counts"],
             "row_cap_note": row_cap_note,
             "write_enabled": load_config().bc.write_enabled},
        )

    @app.post("/bc-push/apply", response_class=HTMLResponse,
              dependencies=_operator_only())
    def bc_push_apply(request: Request, confirm: str = Form("")):
        """Two-step (Task 0 contract): the first press prices the batch and
        writes nothing; only `confirm=1` enqueues.

        Recomputes rather than replaying the preview shown a moment earlier —
        a stale diff confirmed is yesterday's answer applied today.
        """
        write_enabled = load_config().bc.write_enabled
        with conn_factory() as conn:
            result = plan(conn, cfg)
            refs = [r["item_ref"] for r in result["rows"]]

            if not confirm:
                if not refs:
                    # Finding 10: nothing to price, so no Yes button to price
                    # it with -- a confirm over zero articles is a button
                    # that can only ever do nothing.
                    return templates.TemplateResponse(request, "_result.html", {
                        "request": request,
                        "receipt": "Nothing to send: every article already "
                                   "matches Business Central.",
                    })
                lines = [f"Send {len(refs)} article(s) to Business Central?"]
                if write_enabled:
                    lines.append("Sending is switched on: this will send real "
                                 "changes to Business Central.")
                else:
                    lines.append("Sending is switched off: this queues the "
                                 "job, but nothing will reach Business "
                                 "Central.")
                return templates.TemplateResponse(request, "_result.html", {
                    "request": request,
                    "confirm_needed": lines,
                    "confirm_url": "/bc-push/apply",
                    "confirm_target": "bc-push-result",
                    "confirm_fields": {},
                    "confirm_label": "Yes, send them",
                })

            today = conn.execute("SELECT current_date AS d").fetchone()["d"]
            run = f"bulk:{today.isoformat()}"
            enqueued = 0
            for i in range(0, len(refs), batch):
                chunk = refs[i:i + batch]
                jid = queue.enqueue(
                    conn, "bc.push", {"run_id": run, "item_refs": chunk},
                    f"bc.push:bulk:{run}:{i // batch}", priority="delta",
                )
                if jid is not None:
                    enqueued += len(chunk)
            conn.commit()
        # Finding 5: `bc.push` itself withholds every value while writes are
        # off (`app/handlers/bc_push.py`) -- "the worker sends them" is false
        # in that state, not just optimistic, so this branches on the same
        # key the handler reads instead of promising a send that cannot
        # happen.
        msg = f"Queued {enqueued} article(s) for Business Central."
        if write_enabled:
            msg += " The worker sends them."
        else:
            msg += (" Sending is switched off, so nothing will reach "
                    "Business Central -- the job only records what would "
                    "be sent.")
        return templates.TemplateResponse(request, "_result.html", {
            "request": request,
            "receipt": msg,
        })
