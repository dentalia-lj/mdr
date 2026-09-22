"""One-off repair: restore the REF lists an escalated tier erased.

Background. `extract_ref_list` stamps a flat 0.9 confidence, which can never
clear the 0.95 escalation threshold, so a *successful* T0 REF list escalated on
every document. `tiers.merge` was `{**base, **new}` — unconditional later-tier-
wins — so a vision tier reading GC's page-2 "Artikelliste: Gemäß Anhang" field
and answering `[]` in good faith deleted a correctly-parsed list. Measured over
the 61 corpus extractions: none carried a T0-sourced `ref_list`; 26 lost one.

`tiers.merge` is fixed going forward. This repairs the rows already persisted
WITHOUT a paid re-extraction: T0 is deterministic, free, and re-reads the same
archived bytes, so replaying it reproduces exactly what the fixed ladder would
have merged. The stored LLM answers are reused verbatim — nothing is re-inferred
and no API call is made. The field decision is delegated to `tiers.merge` itself
rather than reimplemented, so a repaired row means precisely what a freshly
extracted one means.

Append-only, matching migration 004's "every tier attempt kept — T3 humans see
the full ladder": a repair lands as a NEW `extract_rev` and never overwrites the
original. VALIDATE reads `(content_hash, extract_rev)` from its job payload and
payloads are immutable (invariant 9), so a stale `validate.doc` job pinned to the
old rev is deleted and re-emitted at the new one — CLAUDE.md's "drain or
delete-and-re-emit, never payload versioning". The queue is regenerable state;
the registry is truth.

Dry run by default, like `vendor-master` and `regroup`:

    python -m app.cli repair-ref-list            # report only
    python -m app.cli repair-ref-list --apply
"""

from __future__ import annotations

from app import queue
from app.extract import pdf as pdfutil
from app.extract import tiers
from app.extract.t0_templates import t0_extract


def _latest_attempts(conn) -> list[dict]:
    """One row per content_hash — its highest `extract_rev`, joined to the file
    the fetch ledger recorded. `DISTINCT ON` collapses the duplicate fetch_log
    rows that a re-fetch of identical bytes leaves behind."""
    return conn.execute(
        """
        SELECT DISTINCT ON (ea.content_hash)
               ea.content_hash, ea.extract_rev, ea.tier, ea.model_id, ea.fields,
               fl.url_normalized AS path
        FROM extraction_attempt ea
        JOIN fetch_log fl ON fl.content_hash = ea.content_hash
        ORDER BY ea.content_hash, ea.extract_rev DESC
        """
    ).fetchall()


def plan(conn) -> tuple[list[dict], list[dict]]:
    """`(repairs, skipped)`. A repair is an attempt whose stored fields differ
    from what the fixed merge rule yields over (replayed T0, stored answers).

    Diffing against the LATEST rev is what makes the repair re-runnable: once a
    repair is applied, its own output becomes the incumbent and plans empty.
    """
    repairs: list[dict] = []
    skipped: list[dict] = []
    for row in _latest_attempts(conn):
        try:
            doc = pdfutil.open_doc(row["path"])
            _, t0_fields = t0_extract(doc, row["path"])
        except Exception as exc:              # moved/unreadable corpus file
            skipped.append({**row, "reason": f"{type(exc).__name__}: {exc}"})
            continue
        # The stored `fields` ARE a merged ladder output, so replaying the fixed
        # rule as merge(T0, stored) restores exactly what a blank later answer
        # erased, and leaves every real later answer winning.
        repaired = tiers.merge(t0_fields, row["fields"])
        restored = [f for f in repaired if repaired[f] != row["fields"].get(f)]
        if restored:
            repairs.append({**row, "repaired": repaired, "restored": restored})
    return repairs, skipped


def apply(conn, repairs: list[dict]) -> dict:
    """Append each repair as a new `extract_rev` and re-point its `validate.doc`
    job at that rev. The caller owns the transaction."""
    deleted = emitted = 0
    for r in repairs:
        rev = tiers.next_extract_rev(conn, r["content_hash"])
        tiers.write_extraction_attempt(
            conn, r["content_hash"], [r["tier"]], r["repaired"],
            extract_rev=rev, model_id=r["model_id"],
        )
        # Only jobs not yet started: a running job is mid-flight and a terminal
        # one is history. Both are left exactly as they are.
        stale = conn.execute(
            "DELETE FROM job WHERE type='validate.doc' AND status='pending' "
            "AND payload->>'content_hash'=%s AND (payload->>'extract_rev')::int=%s "
            "RETURNING payload",
            (r["content_hash"], r["extract_rev"]),
        ).fetchall()
        deleted += len(stale)
        # Keep the scope the original job carried; dropping it would turn a
        # group-scoped match into an unscoped one.
        group_id = stale[0]["payload"].get("group_id") if stale else None
        if queue.enqueue(
            conn, "validate.doc",
            {"content_hash": r["content_hash"], "group_id": group_id, "extract_rev": rev},
            dedupe_key=f"validate:{r['content_hash']}:{rev}:{group_id}",
        ) is not None:
            emitted += 1
    return {"appended": len(repairs), "jobs_deleted": deleted, "jobs_emitted": emitted}


def render(repairs: list[dict], skipped: list[dict]) -> list[str]:
    """Report lines. Skips are printed, never silent (CLAUDE.md)."""
    out = []
    for r in repairs:
        before = (r["fields"].get("ref_list") or {}).get("value")
        after = (r["repaired"].get("ref_list") or {}).get("value")
        out.append(f"  {r['content_hash'][:12]}  rev{r['extract_rev']}  "
                   f"{','.join(r['restored'])}: {before!r} -> {len(after or [])} "
                   f"code(s)  {r['path'].split('/')[-1][:44]}")
    for s in skipped:
        out.append(f"  SKIPPED {s['content_hash'][:12]}  {s['reason']}")
    out.append(f"\n{len(repairs)} to repair, {len(skipped)} skipped")
    return out
