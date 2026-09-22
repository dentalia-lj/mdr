"""One-off repair: retract the mfr-scope production links of an enumerating
QA certificate that C16 machine-bound manufacturer-wide.

Doc 310 — Kiwa Cermet EC Quality Assurance System certificate MED 31385
(93/42/EEC Annex V) — enumerates EXACTLY three device types with model codes
in its own technical annex ("valid only for the above mentioned Medical
Devices that are subject to survey"), and C16 machine binding fanned it
across 356 GC production items on 2026-08-17. The [qa-device-enumeration]
guard (VALIDATE flag `device-enumeration`, same session) stops NEW bindings
of that shape; this tool retracts the links already written. The basis is
over-binding — the registry asserting coverage the paper itself limits to a
device list — NOT the certificate's expiry: a lapse changes only the
coverage KPI, never bindings (separate ruling, same day).

Per document (doc_id is an argument, never hardcoded): the stored
`document_text` must exist and the part-(a) detector itself
(`validate._enumerates_devices`) must say it enumerates devices — the tool
and the guard cannot disagree, they are one function. The binding must have
been MACHINE-made (latest `bind-manufacturer` audit event decided_by
'gate'): a human `gate.apply bind-manufacturer` was never barred by the
guard and is not undone by ops tooling. Only links at
`(match_basis='mfr-scope', status='production')` are selected; everything
else is counted and reported, never touched. Retraction mirrors
`gate._retract_unsupported_links`' conventions: a status flip to
`retracted` (append-only — the row and its `match_basis` survive as the
record of HOW the link was proposed), never a delete, one self-contained
`audit_log` entry per flipped link. The document row itself is deliberately
untouched: the certificate is real, archived and evidenced — it just stops
asserting item coverage.

Writing the registry outside GATE is an invariant-1 exception, ruled by
Denis 2026-08-24 ("Guard + clean 310") — `decided_by` records it, the same
way `repair-archive-urls` records its 2026-08-24 ruling.

Dry run by default, like `repair-archive-urls`:

    python -m app.cli repair-mfr-overbind 310            # report only
    python -m app.cli repair-mfr-overbind 310 --apply
"""

from __future__ import annotations

from psycopg.types.json import Json

from app.handlers import validate as validate_mod

DECIDED_BY = "ops:repair-mfr-overbind"
#: Same event `gate._retract_unsupported_links` writes — a retraction is a
#: retraction; `decided_by` is what distinguishes the ops path in the trail.
EVENT = "link-retracted"
RULING = "Denis 2026-08-24"


def _latest_bind_decided_by(conn, doc_id: int) -> str | None:
    row = conn.execute(
        "SELECT decided_by FROM audit_log "
        "WHERE doc_id=%s AND event='bind-manufacturer' ORDER BY id DESC LIMIT 1",
        (doc_id,),
    ).fetchone()
    return row["decided_by"] if row else None


def plan(conn, doc_id: int) -> tuple[list[dict], dict]:
    """`(retractions, report)`. Retractions carry the links the part-(a)
    guard would no longer allow; `report["blockers"]` carries every reason
    the whole document is refused (any blocker means an empty plan), and
    `report["untouched_links"]` counts the document's other links, reported
    so their exclusion is visible. Re-runnable: retracted links stop being
    selected, so a second run plans zero."""
    report: dict = {"blockers": [], "untouched_links": 0}

    doc = conn.execute(
        "SELECT doc_id, content_hash, type, coverage_scope, status "
        "FROM document WHERE doc_id=%s",
        (doc_id,),
    ).fetchone()
    if doc is None:
        report["blockers"].append({"reason": f"no document with doc_id {doc_id}"})
        return [], report

    # The guard's own verdict, from the guard's own function — never a
    # reimplementation that could drift. Missing text refuses: a repair that
    # cannot re-verify the basis for the retraction must not act on memory.
    text = validate_mod._stored_text(conn, doc["content_hash"])
    if text is None:
        report["blockers"].append({"reason": (
            "no stored text (document_text missing or source='none') — "
            "cannot verify the enumeration; refuse rather than trust a note")})
    elif not validate_mod._enumerates_devices(text):
        report["blockers"].append({"reason": (
            "stored text does not enumerate devices — the "
            "[qa-device-enumeration] guard would still allow this binding")})

    bound_by = _latest_bind_decided_by(conn, doc_id)
    if bound_by != "gate":
        report["blockers"].append({"reason": (
            f"binding was not machine-made (latest bind-manufacturer event: "
            f"{bound_by!r}) — a human decision is not undone by ops tooling")})

    links = conn.execute(
        "SELECT item_ref, match_basis, status FROM item_document "
        "WHERE doc_id=%s ORDER BY item_ref",
        (doc_id,),
    ).fetchall()
    selected = [{"item_ref": l["item_ref"], "match_basis": l["match_basis"],
                 "status": l["status"]}
                for l in links
                if l["match_basis"] == "mfr-scope" and l["status"] == "production"]
    report["untouched_links"] = len(links) - len(selected)

    if report["blockers"]:
        return [], report
    return selected, report


def apply(conn, doc_id: int, retractions: list[dict]) -> dict:
    """Flip each planned link to `retracted` and audit it. The caller owns
    the transaction. Guarded UPDATE (status/basis re-checked in the WHERE) so
    a row that moved since planning is skipped, and skipped means un-audited:
    the trail records only what actually changed."""
    retracted = 0
    for r in retractions:
        changed = conn.execute(
            "UPDATE item_document SET status='retracted' "
            "WHERE doc_id=%s AND item_ref=%s "
            "  AND status='production' AND match_basis='mfr-scope' "
            "RETURNING item_ref",
            (doc_id, r["item_ref"]),
        ).fetchone()
        if changed is None:
            continue
        conn.execute(
            "INSERT INTO audit_log (event, doc_id, item_ref, decided_by, via_job, "
            "job_snapshot, detail) VALUES (%s, %s, %s, %s, NULL, %s, %s)",
            (EVENT, doc_id, r["item_ref"], DECIDED_BY,
             Json({"tool": "repair-mfr-overbind", "ruling": RULING,
                   "reason": "device-enumeration", "doc_id": doc_id,
                   "match_basis": "mfr-scope"}),
             Json({"link_status_before": "production"})))
        retracted += 1
    return {"links_retracted": retracted}


def render(doc_id: int, retractions: list[dict], report: dict) -> list[str]:
    """Report lines. Blockers and untouched links are printed, never silent
    (CLAUDE.md)."""
    out = []
    for b in report["blockers"]:
        out.append(f"  REFUSED doc {doc_id}: {b['reason']}")
    for r in retractions:
        out.append(f"  {r['item_ref']:>12}  {r['match_basis']}/{r['status']} -> retracted")
    out.append(f"\n{len(retractions)} link(s) to retract on doc {doc_id}, "
               f"{report['untouched_links']} other link(s) untouched")
    return out
