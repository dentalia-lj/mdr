"""Shared FETCH/UPLOAD archive + emit helpers (no HTTP, no LLM)."""
from __future__ import annotations

from app.handlers import archiving


def test_archive_path_is_hash_addressed_and_sanitized():
    p = archiving.archive_path("VOCO GmbH", "abcdef0123456789", "cert.pdf", "application/pdf")
    assert p.startswith("VOCO_GmbH/")
    assert "abcdef012345__" in p          # 12-char hash prefix
    assert p.endswith("cert.pdf")


def test_emit_extract_uses_content_hash_dedupe_key(conn):
    archiving.emit_extract(conn, "file:///a/x.pdf", "hh", 7, "upload:x.pdf")
    row = conn.execute(
        "SELECT type, payload, dedupe_key FROM job WHERE type='extract.doc'").fetchone()
    assert row["dedupe_key"] == "extract:hh"
    assert row["payload"] == {"archive_url": "file:///a/x.pdf", "content_hash": "hh",
                              "group_id": 7, "source_url": "upload:x.pdf"}


def test_emit_validate_key_includes_rev_and_group(conn):
    archiving.emit_validate(conn, "hh", 3, 7)
    row = conn.execute(
        "SELECT dedupe_key FROM job WHERE type='validate.doc'").fetchone()
    assert row["dedupe_key"] == "validate:hh:3:7"
