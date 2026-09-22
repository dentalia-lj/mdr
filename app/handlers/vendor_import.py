"""vendor.import — a BC manufacturer master handed to us via the /import form.

The browser counterpart to `python -m app.cli vendor-master`. Thin by design:
`app/vendor_master.py` owns reading, diffing, applying and the rename refusal;
this module owns only the spool lifecycle and the report the confirm screen
renders.

Two jobs over ONE `import_inbox` row at `kind='vendors'` — the preview reads it
and LEAVES it, the apply reads the same bytes and consumes it. That is why the
spool is two-phase, unlike `upload_inbox` where one read is the whole lifecycle.

Writes `vendor_master` only — never document/item_document/evidence (invariant
1). `dentalia_api` holds SELECT on `vendor_master` and no write grant, so the
web structurally cannot do this; the worker does. AI:none (invariant 12).

Emits nothing, deliberately. A vendor import that adds codes leaves
`manufacturer_alias` stale, and the tempting move is to call `sync_aliases`
here. It does not: `sync` reports `orphaned_groups` — `item_group` rows still
carrying a `canonical_manufacturer` it has just re-pointed away from, which it
does NOT retro-fix — so a non-zero count is work a human must act on, and
running it as a side effect produces that count where nobody reads it. `sync`
also raises `PlaybookConflict` on an authoring error, which would dead-letter a
vendor import for a reason having nothing to do with the vendor file. The
confirm screen names what is stale and the command to run.

Idempotent: the spool row is deleted on success, so a retry after a committed
delete raises `SpoolMissing` with an actionable message rather than importing
zero manufacturers and reporting it as success.
"""
from __future__ import annotations

import logging

from app import import_spool, vendor_master
from app.handlers import register

log = logging.getLogger("dentalia.handler.vendor_import")


def handle_vendor_import(conn, job) -> dict:
    payload = job["payload"]
    upload_id = payload["upload_id"]
    dry_run = bool(payload.get("dry_run"))
    allow_renames = bool(payload.get("allow_renames"))

    spool = import_spool.read(conn, upload_id)
    if spool["kind"] != "vendors":
        raise ValueError(
            f"import_inbox row {upload_id} has kind={spool['kind']!r}, not "
            f"'vendors'. Refusing rather than parsing it: an item export has "
            f"neither BC column this reader needs, so the run would import zero "
            f"manufacturers and report it as success."
        )

    rows = vendor_master.read_bytes(spool["content"], spool["filename"])
    d = vendor_master.diff(conn, rows)

    # `renamed` and `disappeared` carry their members, not just counts: the
    # whole point of the preview is that the operator reads WHICH codes move
    # and to what. Tuples become lists so the report round-trips through
    # `job.result` jsonb unchanged.
    report = {
        "seen": len(rows),
        "added": len(d.added),
        "renamed": [list(t) for t in d.renamed],
        "disappeared": list(d.disappeared),
        "unchanged": d.unchanged,
        "dry_run": dry_run,
        "allow_renames": allow_renames,
        "written": False,
    }
    if dry_run:
        return report

    try:
        vendor_master.apply(conn, rows, batch=payload["filename"],
                            allow_renames=allow_renames)
    except vendor_master.RenameRefused as exc:
        # Expected, not exceptional. The operator previewed a clean diff and
        # something re-pointed a code in between — the same time-of-check gap
        # the item import reports as previewed-against-applied. Dead-lettering
        # would bury the reason and strand the spool row; report it, leave both
        # intact, and let the apply be repeated with the opt-in ticked.
        log.info("vendor.import %s refused: %s", upload_id, exc)
        report["outcome"] = "rename-refused"
        report["error"] = str(exc)
        return report

    import_spool.consume(conn, upload_id)
    report["written"] = True
    report["spool_gc"] = import_spool.gc(conn)
    return report


register("vendor.import", handle_vendor_import)
