"""ingest.run: parse a BC catalogue snapshot, diff it against item_mirror, and
emit resolve.group for every new or changed MD item. PRD §1 / handbook §1.

The topology is the enqueue graph: INGEST's whole job is to keep item_mirror
current and to fan out one resolve.group per changed item. No AI, no fetching.

Design notes (Phase 0 §G4 forced these):
  * item_mirror holds only MD-relevant items. An explicit non-MD row (device
    class `NI MP`) is counted and dropped before the upsert — it never needs a
    group. An UNCLASSIFIED row (blank class, 61% of the LJ export) is tri-state
    unknown; `Ingest.process_md_unknown` decides whether it flows on (default
    yes, counted either way — dropping a real-but-unclassified MD is the costlier
    compliance error, and the choice is reversible before the sweep).
  * mfr_ref may be null (C1). A null on a processed item is COUNTED in
    `missing_mfr_ref` (this bounds achievable AC1), never a reason to skip.
  * a mfr_ref that is a Slovene stock remark rather than an article number is
    SCRUBBED to null before the mirror write (`_is_prose_ref`), counted in
    `mfr_ref_prose` and reported as that anomaly with the original value. It
    would otherwise be materialized into item_group_member by RESOLVE and
    compared against document REF codes under invariant 3. Both counters feed
    the same K3 'no article number' rate on the KPI board, which reads
    `item_mirror.mfr_ref IS NULL` and so already includes the scrubbed rows.
  * A row that cannot be persisted at all (no item_ref, or no name — both are
    NOT NULL keys/fields) is a reported skip, never silent (CLAUDE.md).
  * mirror_rev advances via a sequence (migration 011) on every change; it is the
    version token in the resolve dedupe key, so a re-changed item enqueues a
    fresh resolve.group instead of colliding with a terminal one.

At-least-once delivery: a re-run over unchanged content upserts nothing and emits
nothing (the diff), and any resolve.group it would emit is dedupe-guarded — so a
crash-and-retry is safe.

Two payload shapes reach this handler. The scheduler and the /ingest form send
`{source: 'csv'|'bc_odata', ref, catalogue}` and it writes. The /import upload
form sends `{source: 'upload', ref: {upload_id}, catalogue, dry_run}` TWICE --
once with `dry_run: true` to report the diff and write nothing, then, after the
operator confirms, once with `dry_run: false` to write and consume the spool
row. Both runs diff against the live mirror, so the apply is always correct
against current state; what can go stale is only the number the operator looked
at, which is why both counts are reported rather than compared.

Known and deliberate: a dry run over an export containing the SAME item_ref
twice counts it changed twice, where an apply counts it changed once and then
unchanged, because the apply's first upsert is visible to the second row's diff
and a dry run writes nothing to see. A duplicated key in one export is a defect
in the export; the counts differing is how it becomes visible.
"""

from __future__ import annotations

import dataclasses
import logging

from app import import_spool
from app import queue
from app.adapters import source as src
from app.config import Config, load_config
from app.handlers import register
from app.results import Result

log = logging.getLogger("dentalia.handler.ingest")

# The mirror columns compared to decide "changed" (order matches _DIFF_SELECT).
# mirror_rev / updated_at are bookkeeping, never part of the diff.
_DIFF_COLS = ("name", "manufacturer_raw", "mfr_ref", "md_flag", "product_class",
              "udi", "catalogue")
_DIFF_SELECT = ", ".join(_DIFF_COLS)

# mirror_rev is omitted from the INSERT column list so the sequence is consumed
# exactly ONCE per write: on insert the column DEFAULT (migration 011) supplies
# nextval; on update the explicit SET does. Listing nextval() in VALUES too would
# burn a second value on every conflict-update and discard it.
_UPSERT = """
    INSERT INTO item_mirror
        (item_ref, name, manufacturer_raw, mfr_ref, md_flag, product_class,
         udi, catalogue, updated_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
    ON CONFLICT (item_ref) DO UPDATE SET
        name             = EXCLUDED.name,
        manufacturer_raw = EXCLUDED.manufacturer_raw,
        mfr_ref          = EXCLUDED.mfr_ref,
        md_flag          = EXCLUDED.md_flag,
        product_class    = EXCLUDED.product_class,
        udi              = EXCLUDED.udi,
        catalogue        = EXCLUDED.catalogue,
        mirror_rev       = nextval('item_mirror_rev_seq'),
        updated_at       = now()
    RETURNING mirror_rev
"""

def _is_prose_ref(value: str) -> bool:
    """Is this `mfr_ref` a stock remark rather than an article number?

    The rule is `contains "!"` and deliberately nothing cleverer. Per the
    vendor-master analysis (2026-08-13), 378 populated values contain "!" and
    all of them are stock remarks -- `NE BO VEC NA ZALOGI!` (x122), `OPERA!`
    (x48), `NI VEC DOBAVLJIVO!` (x27), `NI DOBAVLJIVO!!!` (x26), `NE NAROCAJ!`
    (x15), `samo po narocilu!` (x11) and the tail. 985 populated values contain
    a space and 652 of THOSE are legitimate article codes (Komet's ISO bur
    numbers, e.g. `104 H251EF 060`), so a whitespace, length or "looks like
    prose" heuristic silently destroys real catalogue data and breaks the REF
    gate for those items. The remaining ~20 remarks carry no "!"
    (`NI DOBAVLJIVO`) and are deliberately left alone: no safe rule separates
    them from a code. Dentalia have been asked to fix the column at source
    (docs/2026-08-13-vprasanja-za-dentalio.md, question 12); until they do, we
    scrub on our side."""
    return "!" in value


def _mirror_tuple(row: src.NormalizedRow):
    """The comparand tuple, in _DIFF_COLS order. manufacturer_raw coalesced to ''
    (schema NOT NULL) — a blank BC manufacturer code is stored empty and flagged
    downstream by RESOLVE, not skipped here (only ~2 rows in the real LJ export)."""
    return (row.name, row.manufacturer_raw or "", row.mfr_ref, row.md_flag,
            row.product_class, row.udi, row.catalogue)


def handle_ingest_run(conn, job: dict, cfg: Config | None = None,
                      *, records: list[dict] | None = None) -> dict:
    cfg = cfg or load_config()
    payload = job["payload"]
    priority = job.get("priority", "sweep")
    dry_run = bool(payload.get("dry_run"))

    # The factory has no connection, so an upload's bytes are injected the same
    # way the OData stub's `records` are.
    spool = None
    if payload.get("source") == "upload":
        spool = import_spool.read(conn, payload["ref"]["upload_id"])

    # The BC endpoint is the one source that needs a network client. Built here
    # rather than in the factory because the factory takes `cfg.ingest` and the
    # credentials live in `cfg.bc` -- and because a test injecting `records`
    # must never construct one.
    client = None
    if payload.get("source") == "bc_odata" and records is None and cfg.bc.base_url:
        from app.adapters.bc_client import BcClient

        client = BcClient(cfg.bc.base_url, username=cfg.bc.username,
                          password=cfg.bc.password)

    adapter = src.make_source_adapter(
        payload, cfg.ingest, imports_dir=cfg.web.imports_dir, records=records,
        client=client,
        spool=spool,
    )

    r = Result()
    # Pre-seed every counter this report has always carried, so the shape is
    # stable even when a run never hits a given branch (e.g. no skips).
    for key in ("seen", "changed", "unchanged", "non_md", "reclassified_non_md",
                "md_unknown", "missing_mfr_ref", "mfr_ref_prose", "skipped"):
        r.count(key, 0)

    def _skip(reason: str, raw: dict) -> None:
        r.count("skipped")
        r.sample("skipped", {"reason": reason, "raw": raw})

    def _current(item_ref: str):
        return conn.execute(
            f"SELECT {_DIFF_SELECT} FROM item_mirror WHERE item_ref = %s",
            (item_ref,),
        ).fetchone()

    for row in adapter.read():
        r.count("seen")

        # Structural skips: can't persist without a key or a name (both NOT NULL).
        if not row.item_ref:
            _skip("missing item_ref", row.raw)
            continue
        if not row.name:
            _skip("missing name", row.raw)
            continue

        # MD gate (tri-state). The mirror holds MD-relevant rows only, so an
        # explicit non-MD row does NOT create a mirror entry — EXCEPT when the
        # item is already mirrored (it was MD and BC has declassified it): then
        # the mirror must be corrected to md_flag=FALSE so `md_flag IS TRUE`
        # consumers (gate mfr-scope) stop treating a non-MD item as an MD. A
        # non-MD row never emits resolve.group.
        if row.md_flag is False:
            r.count("non_md")
            current = _current(row.item_ref)
            if current is None:
                continue  # brand-new non-MD item -> not mirrored
            new = _mirror_tuple(row)
            if tuple(current[c] for c in _DIFF_COLS) == new:
                r.count("unchanged")
                continue
            if not dry_run:
                conn.execute(_UPSERT, (row.item_ref, *new))
            # Counted AND raised, like every sibling branch below. The count
            # lives and dies with one job result; the anomaly is the standing
            # keyed row that says how often BC has declassified an item we
            # already mirror, and which ones -- the question /data-quality
            # exists to answer, and which it answered with silence until
            # 2026-08-19 ([results-anomaly-kinds]).
            r.count("reclassified_non_md")
            r.anomaly("reclassified_non_md", subject=row.item_ref,
                      catalogue=row.catalogue)
            continue

        # Unknown device class (blank) is config-gated for pipeline participation.
        # Flagged regardless of the toggle -- it is a data-quality fact about the
        # export, not a consequence of how we chose to process it.
        if row.md_flag is None:
            r.count("md_unknown")
            r.anomaly("md_class_blank", subject=row.item_ref, catalogue=row.catalogue)
            if not cfg.ingest.process_md_unknown:
                continue

        # C1: a processed item with no manufacturer article number is counted,
        # never dropped — this rate bounds achievable AC1.
        if not row.mfr_ref:
            r.count("missing_mfr_ref")
            r.anomaly("mfr_ref_missing", subject=row.item_ref, catalogue=row.catalogue)
        elif _is_prose_ref(row.mfr_ref):
            # Flagging alone was not enough: the remark still reached
            # item_group_member and was compared against document REF codes as
            # if it were one (invariant 3). Scrub it to NULL -- never to '',
            # which VALIDATE would compare as a code, whereas NULL already
            # means "no article number" everywhere downstream. The value is not
            # lost: it stays on the anomaly, and the BC export remains the
            # source of truth.
            r.count("mfr_ref_prose")
            r.anomaly("mfr_ref_prose", subject=row.item_ref, catalogue=row.catalogue,
                      detail={"value": row.mfr_ref})
            row = dataclasses.replace(row, mfr_ref=None)
        if not row.manufacturer_raw:
            r.anomaly("manufacturer_code_blank", subject=row.item_ref, catalogue=row.catalogue)

        new = _mirror_tuple(row)
        current = _current(row.item_ref)
        if current is not None and tuple(current[c] for c in _DIFF_COLS) == new:
            r.count("unchanged")
            continue

        if not dry_run:
            rev = conn.execute(_UPSERT, (row.item_ref, *new)).fetchone()["mirror_rev"]
            queue.enqueue(
                conn, "resolve.group",
                {"item_ref": row.item_ref},
                dedupe_key=f"resolve:{row.item_ref}:{rev}",
                priority=priority,
            )
        r.count("changed")

    if spool is not None and not dry_run:
        import_spool.consume(conn, payload["ref"]["upload_id"])

    out = r.as_dict()
    # Anomalies are NOT recorded here. app/workers/runner.py's generic
    # post-finish hook is the single mechanism that writes data_anomaly, for
    # every handler, uniformly -- that is the whole point of standardizing on
    # Result (Task 3). "anomalies" stays in the returned report so job.result
    # (migration 019) shows what this run found, same as any other handler.
    report = out["counts"]
    report["skipped_sample"] = out["samples"].get("skipped", [])
    report["dry_run"] = dry_run
    if dry_run:
        # The runner keys on exactly "anomalies". A preview changed nothing, so
        # it must not write the standing ledger -- otherwise the apply counts
        # every observation a second time. The findings are still shown.
        report["anomalies"] = []
        report["anomalies_preview"] = out["anomalies"]
    else:
        report["anomalies"] = out["anomalies"]

    if spool is not None:
        # Opportunistic GC of previews nobody applied. Here rather than in a
        # scheduler cron because app/scheduler.py's ticks enqueue and never
        # otherwise write, and this is a DELETE.
        report["spool_gc"] = import_spool.gc(conn)

    log.info(
        "ingest.run %s: seen=%d changed=%d unchanged=%d non_md=%d "
        "reclassified_non_md=%d md_unknown=%d missing_mfr_ref=%d "
        "mfr_ref_prose=%d skipped=%d dry_run=%s",
        payload.get("catalogue"), report["seen"], report["changed"],
        report["unchanged"], report["non_md"], report["reclassified_non_md"],
        report["md_unknown"], report["missing_mfr_ref"],
        report["mfr_ref_prose"], report["skipped"], dry_run,
    )
    return report


register("ingest.run", handle_ingest_run)
