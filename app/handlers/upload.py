"""upload.ingest — a document handed to us via the web /upload form.

fetch.url minus the HTTP fetch: the bytes already sit in a transient
upload_inbox row, so this archives them, upserts the fetch ledger, and hands the
spine forward exactly like FETCH's post-download tail:

    new content              -> archive + extract.doc {archive_url, content_hash, group_id?, source_url}
    seen + extracted + group -> validate.doc (C3 link-candidate vs the existing doc)
    seen + extracted, no grp -> nothing to link, skip
    seen + not-yet-extracted -> extract.doc (carry the group)

When the upload originated from a `discovery-dead-end` manual task (an admin
clicked "Upload document" on the /manual dead-end), the payload carries
`manual_task_id` and this handler resolves that task worker-side — the web
role is SELECT-only on `manual_task`.

Producer only (invariant 1): writes fetch_log + the archived file, emits jobs;
never document/item_document/evidence. AI:none (invariant 12). Idempotent: the
spool row is deleted on success, so a retry after a committed delete is a no-op.
"""
from __future__ import annotations

import hashlib
import logging

from app.adapters.storage import make_storage_adapter
from app.config import load_config
from app.handlers import archiving, register

log = logging.getLogger("dentalia.handler.upload")


def handle_upload_ingest(conn, job, *, store=None):
    upload_id = job["payload"]["upload_id"]
    manual_task_id = job["payload"].get("manual_task_id")
    row = conn.execute(
        "SELECT filename, content, target_group_id, catalogue "
        "FROM upload_inbox WHERE id=%s", (upload_id,),
    ).fetchone()
    if row is None:
        return {"outcome": "already-processed", "upload_id": upload_id}

    group_id = row["target_group_id"]
    content_hash = hashlib.sha256(row["content"]).hexdigest()
    url_norm = f"upload:{content_hash}"

    # Backstop for the form's own byte check (`[gate-covers-only-fetch]`,
    # 2026-09-02). The web form refuses a non-document while the person is
    # still standing there, which is the useful place to say it; this is the
    # guard for anything that reaches upload_inbox by another path.
    #
    # Deliberately BEFORE the ledger: a refused upload is not "seen content",
    # and writing `upload:<hash>` for it would leave a fetch_log row pointing at
    # nothing. The spool row still goes, or the upload retries forever.
    if not archiving.is_document(row["content"]):
        magic = archiving.magic_of(row["content"])
        log.warning("upload: refusing non-document %r (magic=%r, %d bytes)",
                    row["filename"], magic, len(row["content"]))
        conn.execute("DELETE FROM upload_inbox WHERE id=%s", (upload_id,))
        # The manual task stays OPEN on purpose: nobody has supplied the
        # document it is waiting for.
        return {"outcome": "not-a-document", "upload_id": upload_id,
                "filename": row["filename"], "magic": magic,
                "bytes": len(row["content"]), "group_id": group_id}

    archiving.ledger_upsert(conn, url_norm, etag=None, last_modified=None,
                            content_hash=content_hash, source="upload")

    rev = archiving.hash_extracted_rev(conn, content_hash)
    if rev is not None:
        if group_id is not None:
            archiving.link_existing_doc(conn, url_norm, content_hash)
            archiving.emit_validate(conn, content_hash, rev, group_id)
            result = {"outcome": "linked", "content_hash": content_hash,
                      "emitted": "validate.doc", "group_id": group_id}
        else:
            result = {"outcome": "duplicate", "content_hash": content_hash, "group_id": None}
    else:
        if store is None:
            store = make_storage_adapter(load_config())
        manufacturer = archiving.manufacturer_for_group(conn, group_id)
        archive_url = store.put(
            row["content"],
            archiving.archive_path(manufacturer, content_hash, row["filename"], "application/pdf"),
        )
        archiving.emit_extract(conn, archive_url, content_hash, group_id,
                               source_url=f"upload:{row['filename']}")
        result = {"outcome": "archived", "content_hash": content_hash,
                  "archive_url": archive_url, "emitted": "extract.doc", "group_id": group_id}

    conn.execute("DELETE FROM upload_inbox WHERE id=%s", (upload_id,))

    if manual_task_id is not None:
        # Worker-side (dentalia_api is SELECT-only on manual_task): the upload
        # satisfied the discovery-dead-end that requested this doc. Mirrors
        # discover.group's resolve. Guarded so it is idempotent and only ever
        # closes an open dead-end task.
        conn.execute(
            "UPDATE manual_task SET status='resolved', resolved_by='upload', "
            "resolved_at=now() WHERE id=%s AND kind='discovery-dead-end' "
            "AND status='open'",
            (manual_task_id,),
        )

    return result


register("upload.ingest", handle_upload_ingest)
