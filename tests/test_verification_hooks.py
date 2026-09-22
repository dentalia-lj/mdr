"""PRD §12 verification hooks for the Phase-0 spine.

The two-cycle simulation (C2/C3/AC5: second cycle ≈ 0 cost) is proven at the
producer in test_backfill_handler.py::test_backfill_second_cycle_emits_nothing.
Here is the QMS-cert binding path (§7b, C4): Ivoclar's manufacturer-level ISO
certificate must flow backfill -> extract -> validate -> gate.candidate and land
as exactly ONE staged binding candidate, not a per-item gate. Driven from the
real PDF with a stub LLM (no live spend); archive_url is resolved from the fetch
ledger (no payload threading).
"""

from __future__ import annotations

import shutil

from app.adapters.storage import LocalFsStore
from app.handlers import backfill as bh
from app.handlers import extract as eh
from app.handlers import gate as gh
from app.handlers import validate as vh

ISO_CERT = "IVOCLAR/Ivoclar ISO certifikat do 30_10_2027.pdf"


class QmsStubLlm:
    """A QMS/ISO cert carries no item-level identifiers. Fill escalated scalars
    but leave ref_list / basic_udi_di / referenced_docs null, so the C4 pre-rule
    (manufacturer scope, no REF/UDI) fires — as it must for a real ISO cert."""

    def extract(self, tier, doc, filename, missing):
        out = {}
        for f in missing:
            value = None if f in ("ref_list", "basic_udi_di", "referenced_docs") else "x"
            out[f] = {"value": value, "conf": 0.96, "tier": tier,
                      "verbatim": "x", "page": 1, "model_id": "stub"}
        return out


def _one(conn, job_type):
    return conn.execute(
        f"SELECT payload FROM job WHERE type='{job_type}'"
    ).fetchone()["payload"]


def test_qms_cert_binding_path(conn, fixture_pdf, tmp_path):
    shutil.copy(fixture_pdf(ISO_CERT), tmp_path / "iso.pdf")

    # backfill -> fetch_log (archive location) + one extract.doc. The store is
    # injected: left to the default it builds one from the real config, whose
    # `./archive` is relative, so the test archives a PDF into the developer's
    # working tree (and, in a container, onto the ephemeral layer).
    scan = bh.handle_backfill_scan(
        conn, {"payload": {"drive_folder": str(tmp_path)}},
        store=LocalFsStore(str(tmp_path / "archive")),
    )
    assert scan["emitted"] == 1
    extract_payload = _one(conn, "extract.doc")

    # extract (deterministic T0 gives coverage_scope=manufacturer; stub fills the rest)
    eh.handle_extract_doc(conn, {"id": 1, "payload": extract_payload}, llm=QmsStubLlm())
    validate_payload = _one(conn, "validate.doc")

    # validate -> C4 pre-rule -> mfr-binding candidate
    vres = vh.handle_validate_doc(conn, {"payload": validate_payload})
    assert vres["route"] == "mfr-binding"
    gate_payload = _one(conn, "gate.candidate")
    assert gate_payload["route"] == "mfr-binding"
    # archive_url is threaded the whole way now. This used to assert the
    # OPPOSITE ("resolved from the fetch ledger, not threaded") — and that
    # fallback is the corpus SOURCE path, which is how all 130 documents of the
    # GC pilot recorded an /imports/... handle no other process can open.
    assert gate_payload["archive_url"] == extract_payload["archive_url"]

    # gate.candidate -> exactly one staged binding entry
    gres = gh.handle_gate_candidate(conn, {"id": 2, "payload": gate_payload})
    assert gres["disposition"] == "mfr-binding"
    doc_id = gres["doc_id"]

    assert conn.execute("SELECT status FROM document WHERE doc_id=%s", (doc_id,)).fetchone()["status"] == "staged"
    assert conn.execute(
        "SELECT count(*) c FROM manual_task WHERE doc_id=%s AND kind='gate-manual'", (doc_id,)
    ).fetchone()["c"] == 1
    # evidence written, archive_url resolved from the fetch ledger (a local path)
    ev = conn.execute(
        "SELECT archive_url FROM evidence WHERE doc_id=%s LIMIT 1", (doc_id,)
    ).fetchone()
    assert ev is not None and ev["archive_url"].endswith("iso.pdf")
