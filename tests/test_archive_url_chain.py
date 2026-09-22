"""archive_url must survive the whole chain, not be re-derived at the end.

`backfill.scan` archives through the StorageAdapter and puts the resulting
handle on the extract.doc payload. But `_finalize` built the validate.doc
payload from scratch (`content_hash`, `group_id`, `extract_rev` only), so the
handle died there — and GATE, finding nothing in its payload, fell back to
`fetch_log.url_normalized`, which backfill writes as the *source* path.

The result was the exact defect the StorageAdapter change was supposed to end:
every one of the 74 documents written during the GC pilot recorded
`/imports/dentalia-sftp/GC/...`, a path meaningful only on the scanning machine
and destroyed by the next corpus re-dump. Fixing the producer was not enough
while the value had no way to reach the consumer.

So archive_url rides the payload the whole way. The fetch_log fallback stays for
paths that genuinely have no payload value, but it must never be what a backfill
document lands on.
"""

from __future__ import annotations

import json

from app import queue
from app.handlers import extract as extract_mod


class _MemStore:
    def __init__(self):
        self.puts = []

    def put(self, body, path):
        self.puts.append((body, path))
        return f"/archive/{path}"


def _jobs(conn, tag):
    return [
        dict(r) for r in conn.execute(
            "SELECT payload FROM job WHERE type=%s ORDER BY id", (tag,)
        ).fetchall()
    ]


def test_finalize_carries_archive_url_into_validate(conn):
    # The extract.doc payload's archive_url has to appear on the validate.doc it
    # emits; dropping it here is what forced GATE to guess.
    extract_mod._finalize(
        conn, "hash-abc", None, "doc", ["T0"], {},
        archive_url="/archive/GC/doc/abc123__Fuji.pdf",
    )

    payloads = [j["payload"] for j in _jobs(conn, "validate.doc")]
    assert payloads, "no validate.doc emitted"
    assert payloads[0]["archive_url"] == "/archive/GC/doc/abc123__Fuji.pdf"


def test_finalize_without_an_archive_url_omits_the_key(conn):
    # Additive-only payloads: absent is fine (the fetch_log fallback still
    # applies), a null that overwrites a real value is not.
    extract_mod._finalize(conn, "hash-none", None, "doc", ["T0"], {})

    payloads = [j["payload"] for j in _jobs(conn, "validate.doc")]
    assert payloads
    assert payloads[0].get("archive_url") is None


def test_validate_carries_archive_url_into_gate(conn):
    # The second hop. Testing _finalize alone would have passed while the value
    # still died here, which is exactly how the GC pilot shipped 130 documents
    # with corpus paths.
    from app.handlers import validate as validate_mod

    conn.execute(
        "INSERT INTO extraction_attempt (content_hash, tier, fields, extract_rev) "
        "VALUES (%s, %s, %s, %s)",
        ("hash-chain", "T0", json.dumps({
            "type": {"value": "DoC", "confidence": 1.0, "tier": "T0",
                     "verbatim": "DECLARATION OF CONFORMITY", "page": None},
            "regulation": {"value": "MDR", "confidence": 1.0, "tier": "T0",
                           "verbatim": "(EU) 2017/745", "page": None},
            "coverage_scope": {"value": "manufacturer", "confidence": 1.0,
                               "tier": "T0", "verbatim": "type=DoC", "page": None},
        }), 1),
    )

    validate_mod.handle_validate_doc(conn, {"payload": {
        "content_hash": "hash-chain", "extract_rev": 1, "group_id": None,
        "archive_url": "/archive/GC/doc/abc123__Fuji.pdf",
    }})

    payloads = [j["payload"] for j in _jobs(conn, "gate.candidate")]
    assert payloads, "no gate.candidate emitted"
    assert payloads[0]["archive_url"] == "/archive/GC/doc/abc123__Fuji.pdf"


def test_gate_prefers_the_payload_over_the_fetch_ledger(conn):
    # The regression in one assertion: with both present, the archived handle
    # must win over the corpus path the ledger records.
    from app.handlers import gate as gate_mod

    conn.execute(
        "INSERT INTO fetch_log (url_normalized, content_hash, source) "
        "VALUES (%s, %s, %s)",
        ("/imports/dentalia-sftp/GC/DOC/Fuji.pdf", "hash-both", "backfill"),
    )

    url = gate_mod._archive_url(
        conn, {"archive_url": "/archive/GC/doc/abc123__Fuji.pdf"}, "hash-both"
    )

    assert url == "/archive/GC/doc/abc123__Fuji.pdf"
    assert not url.startswith("/imports/")
