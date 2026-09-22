"""One-off repair: stamp `reason` onto mfr-binding review tasks raised before
the field existed.

`[mfr-bind-empty-class]` (Denis 2026-08-27) split the manufacturer-binding
review queue in two. A task whose manufacturer resolved but whose catalogue
items all carry a BLANK BC device class carries `reason: "md-class-unknown"`,
and `/staging` renders a different sentence for it -- because the standing one
("approving links it to every medical-device product from that manufacturer")
is false when there are no such products.

Tasks raised BEFORE that build carry no `reason` at all, so every one of them
renders the default sentence: exactly the wording the build was written to
replace, on exactly the documents it was written for. Measured on the live
registry 2026-08-31: **17 open mfr-binding tasks, all 17 with no reason**,
doc 2 (3Shape Poland, ISO MD 583446) among them.

The discriminator is the one `gate._handle_mfr_binding` uses, and it is read
from the same tri-state `md_flag` rather than re-derived:

  * no `TRUE` and no `FALSE` under the manufacturer's BC codes -> the class
    column is blank -> `md-class-unknown`.
  * anything else -> BC has said something, so the default sentence is honest
    and the task is left alone.

Only OPEN tasks on the `mfr-binding` route with no `reason` are considered.
A task that already carries one is never overwritten -- a second run plans
zero, which is the property that makes this safe to re-run.

Writing `manual_task` outside GATE is deliberate and narrow: this changes no
disposition, no link and no document, only the sentence a reviewer is shown.
`decided_by` records the tool, the same way `repair-mfr-overbind` records its
ruling.

Dry run by default:

    python -m app.cli repair-mfr-task-reason            # report only
    python -m app.cli repair-mfr-task-reason --apply
"""

from __future__ import annotations

from psycopg.types.json import Json

from app import manufacturers

EVENT = "task-reason-backfilled"
DECIDED_BY = "tool:repair-mfr-task-reason"
RULING = "[mfr-bind-empty-class] Denis 2026-08-27; backfill 2026-08-31"

#: The only reason this tool ever writes. A task whose manufacturer BC has
#: classified needs no amendment -- the default sentence is true for it.
UNKNOWN_CLASS = "md-class-unknown"


def _class_is_blank(conn, manufacturer: str | None) -> bool:
    """True when BC has recorded no device class either way for this
    manufacturer's items.

    Mirrors `gate._handle_mfr_binding`: `md_flag` is tri-state, `FALSE` means BC
    DECLASSIFIED the item (a positive statement) and `NULL` means the column is
    blank (a gap). Only the all-NULL case gets the amended sentence.
    """
    codes = manufacturers.item_codes_for(conn, manufacturer) if manufacturer else []
    if not codes:
        return False
    row = conn.execute(
        "SELECT count(*) FILTER (WHERE md_flag IS TRUE)  AS md, "
        "       count(*) FILTER (WHERE md_flag IS FALSE) AS declassified "
        "FROM item_mirror WHERE manufacturer_raw = ANY(%s)",
        (codes,),
    ).fetchone()
    return not row["md"] and not row["declassified"]


def plan(conn) -> list[dict]:
    """The tasks to stamp, as `{task_id, doc_id, manufacturer, reason}`.

    Idempotent by construction: `payload->>'reason' IS NULL` excludes anything
    already stamped, so a second run returns an empty list.
    """
    rows = conn.execute(
        "SELECT id, doc_id, payload->>'manufacturer' AS manufacturer "
        "FROM manual_task "
        "WHERE status='open' AND payload->>'route'='mfr-binding' "
        "  AND payload->>'reason' IS NULL "
        "ORDER BY id"
    ).fetchall()
    return [
        {"task_id": r["id"], "doc_id": r["doc_id"],
         "manufacturer": r["manufacturer"], "reason": UNKNOWN_CLASS}
        for r in rows if _class_is_blank(conn, r["manufacturer"])
    ]


def apply(conn, stamps: list[dict]) -> int:
    """Write each planned reason and audit it. The caller owns the transaction.

    Guarded UPDATE (the null-reason condition is re-checked in the WHERE) so a
    task stamped since planning is skipped, and skipped means un-audited: the
    trail records only what actually changed.
    """
    stamped = 0
    for s in stamps:
        changed = conn.execute(
            # `%s::jsonb`: psycopg adapts Json() as `json`, and there is no
            # `jsonb || json` operator. Merge rather than replace, so a payload
            # carrying `flags` keeps them.
            "UPDATE manual_task SET payload = payload || %s::jsonb "
            "WHERE id=%s AND status='open' AND payload->>'reason' IS NULL "
            "RETURNING id",
            (Json({"reason": s["reason"]}), s["task_id"]),
        ).fetchone()
        if changed is None:
            continue
        conn.execute(
            "INSERT INTO audit_log (event, doc_id, item_ref, decided_by, via_job, "
            "job_snapshot, detail) VALUES (%s, %s, NULL, %s, NULL, %s, %s)",
            (EVENT, s["doc_id"], DECIDED_BY,
             Json({"tool": "repair-mfr-task-reason", "ruling": RULING,
                   "task_id": s["task_id"], "manufacturer": s["manufacturer"]}),
             Json({"reason": s["reason"]})))
        stamped += 1
    return stamped


def render(stamps: list[dict], total_open: int) -> list[str]:
    """Report lines. What was left alone is printed, never silent (CLAUDE.md)."""
    out = [f"  task {s['task_id']:>5}  doc {s['doc_id']:>5}  "
           f"{s['manufacturer'] or '(unnamed)'} -> {s['reason']}" for s in stamps]
    out.append(f"\n{len(stamps)} task(s) to stamp, "
               f"{total_open - len(stamps)} left alone (BC has classified them, "
               f"so the standing sentence is already true)")
    return out
