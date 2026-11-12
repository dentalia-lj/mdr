"""Change type on Review: the checks and the consequence list (spec
2026-11-12 §5, §6; PRD v3 `gate.apply` decision `retype`).

Read-only, shared by the two places that must agree: the web route, which
refuses and shows the confirm screen before anything is enqueued, and GATE
(`app/handlers/gate.py` `_apply_retype`), which locks the row, refuses on the
same list and then writes. GATE does the writing; nothing here writes.

Kept free of `app.handlers.gate` and `app.extract.*` on purpose: the web image
has no PyMuPDF, and both of those pull it in (pattern: `app/item_picks.py`).
The vocabularies are therefore spelled out here; `tests/test_retype.py` holds
them equal to `app/extract/checks.py`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

FIELDS = ("type", "regulation", "coverage_scope")
TYPES = ("DoC", "EC", "ISO", "IFU", "SPP", "other")
REGULATIONS = ("MDR", "MDD", "n.a.")
SCOPES = ("group", "manufacturer")
CERT_TYPES = frozenset({"EC", "ISO"})

#: Jobs that would land a machine reading on the old routing, or a second
#: person's decision on the old row. `pending/running/failed` is the active set
#: the queue's dedupe index uses (invariant 8).
_ACTIVE = ("pending", "running", "failed")
_READING_JOBS = ("extract.doc", "validate.doc", "gate.candidate")


def default_scope(doc_type: str) -> str:
    """What a type covers unless the person says otherwise (spec §4): a
    certificate the manufacturer's whole range, anything else specific items.
    An EC certificate with group scope exists (doc 394); the form lets her
    override, and the server takes what is posted."""
    return "manufacturer" if doc_type in CERT_TYPES else "group"


def number_class(doc_type: str) -> str:
    """What `cert_number` means on a document of this type: on a DoC the
    certificate it cites (ruling 2026-10-12), on EC/ISO the certificate's own
    number, on anything else nothing in particular."""
    if doc_type == "DoC":
        return "cites"
    if doc_type in CERT_TYPES:
        return "own"
    return "none"


def _row(conn, doc_id: int):
    return conn.execute(
        "SELECT doc_id, status, type, regulation, coverage_scope, content_hash, "
        "       supersedes, superseded_by, cert_number, validity_to "
        "  FROM document WHERE doc_id=%s", (doc_id,)).fetchone()


def problems(conn, doc_id: int, after: dict, expected: dict | None,
             own_job_id: int | None = None) -> list[str]:
    """Why `doc_id` cannot be retyped to `after`, as codes in a fixed order;
    [] when it can (spec §6 preconditions).

    `expected` is the before-values the confirm screen showed; None skips the
    stale check (step 1 has shown nothing yet). `own_job_id` is the GATE job
    asking, so it does not count itself as a decision in flight. The caller
    locks the row first when it is about to write (GATE does).
    """
    row = _row(conn, doc_id)
    if row is None:
        return ["not-found"]
    out: list[str] = []
    if row["status"] != "staged":
        out.append("not-staged")
    if after.get("type") not in TYPES:
        out.append("bad-type")
    if after.get("regulation") not in REGULATIONS:
        out.append("bad-regulation")
    if after.get("coverage_scope") not in SCOPES:
        out.append("bad-scope")
    if all(after.get(f) == row[f] for f in FIELDS):
        out.append("unchanged")
    if expected is not None and any(expected.get(f) != row[f] for f in FIELDS):
        out.append("stale")
    # `doc_id` is a JSON integer in every producer; `->>` renders it as text.
    if conn.execute(
            "SELECT 1 FROM job WHERE status::text = ANY(%s) AND ("
            "   (type::text = ANY(%s) AND payload->>'content_hash' = %s)"
            "   OR (type = 'gate.apply' AND payload->>'doc_id' = %s"
            "       AND id IS DISTINCT FROM %s)) LIMIT 1",
            (list(_ACTIVE), list(_READING_JOBS), row["content_hash"], str(doc_id),
             own_job_id)).fetchone():
        out.append("in-flight")
    if conn.execute("SELECT 1 FROM item_document WHERE doc_id=%s AND status='production' "
                    "LIMIT 1", (doc_id,)).fetchone():
        out.append("production-link")
    # Refused, never cleared: supersession is append-only (Denis, 2026-11-12).
    if (row["supersedes"] is not None or row["superseded_by"] is not None
            or conn.execute("SELECT 1 FROM document WHERE supersedes=%s OR superseded_by=%s "
                            "LIMIT 1", (doc_id, doc_id)).fetchone()):
        out.append("in-chain")
    return out


def retype_active(conn, doc_id: int) -> bool:
    """Whether a `retype` for this document is queued, running or failed. The
    web route refuses Approve, Reject and the whole-range approval meanwhile
    (spec §7): they were confirmed from a screen the retype is about to move."""
    return conn.execute(
        "SELECT 1 FROM job WHERE type='gate.apply' AND status::text = ANY(%s) "
        "AND payload->>'decision' = 'retype' AND payload->>'doc_id' = %s LIMIT 1",
        (list(_ACTIVE), str(doc_id))).fetchone() is not None


@dataclass(frozen=True)
class Plan:
    """What a retype will do, read from the registry now (spec §5 lines 1-6)."""
    before: dict
    after: dict
    links_off: list[str]          # staged item_refs taken off (scope after = manufacturer)
    citations_cleared: list[int]  # DoCs citing it, when it stops being EC/ISO
    citations_set: list[int]      # DoCs it would answer, when it becomes EC/ISO
    clears_own_cert_doc: bool     # was a DoC, is no longer
    cert_number: str | None
    validity_to: date | None
    number_moves: bool            # the number's meaning changes and a value is set
    panel: str                    # "group" | "manufacturer"


def plan(conn, doc_id: int, after: dict) -> Plan:
    """The consequences of retyping `doc_id` to `after`. Read-only. Raises
    LookupError for a missing document; `problems` is the place to ask first."""
    row = _row(conn, doc_id)
    if row is None:
        raise LookupError(f"retype: no document {doc_id}")
    before = {f: row[f] for f in FIELDS}
    after = {f: after.get(f) for f in FIELDS}
    old_t, new_t = before["type"], after["type"]

    links_off: list[str] = []
    if after["coverage_scope"] == "manufacturer":
        links_off = [r["item_ref"] for r in conn.execute(
            "SELECT item_ref FROM item_document WHERE doc_id=%s AND status='staged' "
            "ORDER BY item_ref", (doc_id,)).fetchall()]

    citations_cleared: list[int] = []
    if old_t in CERT_TYPES and new_t not in CERT_TYPES:
        citations_cleared = [r["doc_id"] for r in conn.execute(
            "SELECT doc_id FROM document WHERE cert_doc_id=%s ORDER BY doc_id",
            (doc_id,)).fetchall()]

    citations_set: list[int] = []
    if new_t in CERT_TYPES and old_t not in CERT_TYPES and row["cert_number"]:
        # The same predicate as `app/handlers/gate.py` `_backresolve_citing_docs`
        # (R-code stripped on both sides); keep the two identical. The one
        # addition, `doc_id <> self`: GATE writes the new type before it
        # back-resolves, so the document itself is no longer a DoC by then.
        citations_set = [r["doc_id"] for r in conn.execute(
            "SELECT doc_id FROM document "
            "WHERE type='DoC' AND cert_doc_id IS NULL "
            "  AND regexp_replace(cert_number, '\\s+R[0-9]+$', '') "
            "      = regexp_replace(%s, '\\s+R[0-9]+$', '') "
            "  AND doc_id <> %s ORDER BY doc_id",
            (row["cert_number"], doc_id)).fetchall()]

    return Plan(
        before=before, after=after, links_off=links_off,
        citations_cleared=citations_cleared, citations_set=citations_set,
        clears_own_cert_doc=old_t == "DoC" and new_t != "DoC",
        cert_number=row["cert_number"], validity_to=row["validity_to"],
        number_moves=(number_class(old_t) != number_class(new_t)
                      and (bool(row["cert_number"]) or row["validity_to"] is not None)),
        panel=after["coverage_scope"],
    )
