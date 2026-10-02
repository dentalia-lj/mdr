"""Seeds for per-item currency (migration 071). Plain SQL, no GATE: these build
registry states directly so the view and its readers can be tested alone."""

from __future__ import annotations

import uuid


def _item(conn, item_ref, manufacturer="ACME"):
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, "
        "catalogue, mirror_rev, updated_at) VALUES (%s,'n',%s,TRUE,'LJ',1,now()) "
        "ON CONFLICT (item_ref) DO NOTHING",
        (item_ref, manufacturer),
    )


def seed_doc(conn, item_refs, *, doc_type="DoC", regulation="MDR",
             validity_from=None, status="production", link_status="production"):
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, coverage_scope, "
        "content_hash, archive_url, status) VALUES (%s,%s,%s::date,'group',%s,"
        "'/archive/currency.pdf',%s::doc_status) RETURNING doc_id",
        (doc_type, regulation, validity_from, f"cur-{uuid.uuid4().hex}", status),
    ).fetchone()["doc_id"]
    for ref in item_refs:
        _item(conn, ref)
        conn.execute(
            "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
            "VALUES (%s,%s,'ref-list',%s::link_status)",
            (ref, doc_id, link_status),
        )
    return doc_id


def newer_copy(conn, doc_id, *, days=365):
    """A newer production document of the same type and regulation, linked to
    every item `doc_id` holds a production link to; then `doc_id` is marked
    superseded by it, the way the old document-wide rule wrote it. It carries no
    expiry and no certificate number, but a DoC copy still gets a staleness
    expiry (validity_from + 5 years) through document_effective_expiry, so it can
    show on an expiry list; other types never do."""
    new_id = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, coverage_scope, "
        "content_hash, archive_url, status) "
        "SELECT type, regulation, COALESCE(validity_from, DATE '2020-01-01') + %s, "
        "       coverage_scope, %s, archive_url, 'production' "
        "  FROM document WHERE doc_id=%s RETURNING doc_id",
        (days, f"cur-newer-{uuid.uuid4().hex}", doc_id),
    ).fetchone()["doc_id"]
    conn.execute(
        "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
        "SELECT item_ref, %s, 'ref-list', 'production' FROM item_document "
        " WHERE doc_id=%s AND status='production'",
        (new_id, doc_id),
    )
    conn.execute(
        "UPDATE document SET status='superseded', superseded_by=%s WHERE doc_id=%s",
        (new_id, doc_id),
    )
    return new_id
