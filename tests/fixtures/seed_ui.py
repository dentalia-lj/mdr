"""Hand-seeded registry rows for the review UI — staging/manual/dead tabs.

PHASES.md S1.6 names this exact file/purpose: "Build against a seed-fixture
script (tests/fixtures/seed_ui.py: staged docs, staged links, binding
candidate, manual_task rows, dead jobs) — no live handlers required." The
gate.apply handler doesn't exist yet (S0.3 is still building it), so this is
the only way to exercise the review UI today; once it lands, these rows are
indistinguishable from ones GATE would have written itself (same tables, same
shapes) - the UI needs no changes.

Every function does a plain INSERT and returns the new row's id(s); nothing
here goes through app.queue or a handler, so callers commit explicitly.
"""

from __future__ import annotations

from psycopg.types.json import Json


def seed_item(
    conn,
    item_ref: str,
    *,
    name: str = "Seed item",
    manufacturer_raw: str = "Seed Manufacturer",
    catalogue: str = "LJ",
    md_flag: bool | None = True,
    mfr_ref: str | None = None,
    product_class: str | None = None,
) -> str:
    conn.execute(
        """
        INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag,
                                 catalogue, mfr_ref, product_class, mirror_rev,
                                 updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 1, now())
        ON CONFLICT (item_ref) DO NOTHING
        """,
        (item_ref, name, manufacturer_raw, md_flag, catalogue, mfr_ref, product_class),
    )
    return item_ref


def _evidence_row(field: str, value: str, *, tier="T1", conf=0.9, page=1, verbatim=None):
    return {
        "field": field,
        "value": value,
        "verbatim": verbatim or value,
        "tier": tier,
        "model_id": "claude-haiku-4-5" if tier != "T0" else None,
        "confidence": conf,
        "page": page,
    }


def seed_document(
    conn,
    *,
    content_hash: str,
    archive_url: str,
    doc_type: str = "DoC",
    regulation: str = "MDR",
    coverage_scope: str = "group",
    status: str = "staged",
    validity_to: str | None = "2028-01-01",
    cert_number: str | None = None,
    basic_udi_di: str | None = None,
    evidence_fields: list[dict] | None = None,
) -> int:
    """Insert one `document` row + its per-field `evidence`. Returns doc_id."""
    doc_id = conn.execute(
        """
        INSERT INTO document (type, regulation, validity_from, validity_to, coverage_scope,
                              basic_udi_di, cert_number, content_hash, archive_url, status)
        VALUES (%s, %s, %s::date, %s::date, %s, %s, %s, %s, %s, %s::doc_status)
        ON CONFLICT (content_hash) DO UPDATE SET status = EXCLUDED.status
        RETURNING doc_id
        """,
        (doc_type, regulation, "2024-01-01", validity_to, coverage_scope,
         basic_udi_di, cert_number, content_hash, archive_url, status),
    ).fetchone()["doc_id"]

    fields = evidence_fields or [
        _evidence_row("type", doc_type, tier="T0", conf=1.0),
        _evidence_row("regulation", regulation, tier="T0", conf=1.0),
        _evidence_row("coverage_scope", coverage_scope, tier="T0", conf=1.0),
    ]
    if validity_to:
        fields.append(_evidence_row("validity_to", validity_to))
    if cert_number:
        fields.append(_evidence_row("cert_number", cert_number))

    for f in fields:
        conn.execute(
            """
            INSERT INTO evidence (doc_id, field, value, archive_url, page, verbatim,
                                  tier, model_id, confidence, extracted_at)
            SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, now()
            WHERE NOT EXISTS (SELECT 1 FROM evidence WHERE doc_id=%s AND field=%s)
            """,
            (doc_id, f["field"], f["value"], archive_url, f["page"], f["verbatim"],
             f["tier"], f["model_id"], f["confidence"], doc_id, f["field"]),
        )
    return doc_id


def seed_link(conn, item_ref: str, doc_id: int, *, match_basis: str, status: str) -> None:
    conn.execute(
        """
        INSERT INTO item_document (item_ref, doc_id, match_basis, status)
        VALUES (%s, %s, %s, %s::link_status)
        ON CONFLICT (item_ref, doc_id) DO UPDATE SET status = EXCLUDED.status
        """,
        (item_ref, doc_id, match_basis, status),
    )


def seed_manual_task(
    conn, *, kind: str, doc_id: int | None = None, group_id: int | None = None,
    payload: dict, status: str = "open",
) -> int:
    return conn.execute(
        """
        INSERT INTO manual_task (kind, doc_id, group_id, payload, status)
        VALUES (%s::manual_kind, %s, %s, %s, %s::manual_status)
        RETURNING id
        """,
        (kind, doc_id, group_id, Json(payload), status),
    ).fetchone()["id"]


def seed_dead_job(conn, *, type: str, payload: dict, dedupe_key: str, error: str) -> int:
    """A job that exhausted its retries — max_attempts=1 so one `fail()` call
    (simulated here directly, since this is a fixture, not a real failure)
    dead-letters it immediately."""
    return conn.execute(
        """
        INSERT INTO job (type, payload, dedupe_key, status, priority, attempts, max_attempts, last_error)
        VALUES (%s::job_type, %s, %s, 'dead', 'sweep', 1, 1, %s)
        RETURNING id
        """,
        (type, Json(payload), dedupe_key, error),
    ).fetchone()["id"]


def seed_extraction_cost(
    conn, *, content_hash: str, tier: str = "T1", extract_rev: int = 1,
    model_id: str = "claude-haiku-4-5", batch: bool = False,
    input_tokens: int = 100, output_tokens: int = 50,
    cost_usd: float | None = 0.01,
) -> int | None:
    """Idempotent on (content_hash, tier, extract_rev) — like every other seed
    helper here, callers may re-seed across tests without colliding (the `conn`
    fixture only truncates `job`/`domain_lease` between tests, never the
    registry tables). Returns None (not an id) when the row already existed."""
    row = conn.execute(
        """
        INSERT INTO extraction_cost (content_hash, tier, extract_rev, model_id, batch,
                                     input_tokens, output_tokens, cost_usd)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (content_hash, tier, extract_rev) DO NOTHING
        RETURNING id
        """,
        (content_hash, tier, extract_rev, model_id, batch, input_tokens, output_tokens, cost_usd),
    ).fetchone()
    return row["id"] if row else None


def seed_kpi_demo(conn) -> dict:
    """Additional fixtures for the KPI board (S1.6, docs/specs/kpi.md) — a
    production doc+link (coverage), an md_flag-NULL item (K1 processed-scope),
    an item with a non-null mfr_ref (K3 contrast), and extraction_cost rows
    incl. one with no price (K8 pricing-gap alert). Independent of seed_demo();
    call alongside it or alone. Items take the default catalogue: the KPI
    missing-mfr_ref counts are exact and order-independent."""
    seed_item(conn, "kpi-item-covered", name="KPI Covered Item", manufacturer_raw="ACME")
    seed_item(conn, "kpi-item-uncovered", name="KPI Uncovered Item", manufacturer_raw="ACME")
    seed_item(conn, "kpi-item-unclassified", name="KPI Unclassified Item",
              manufacturer_raw="ACME", md_flag=None)
    seed_item(conn, "kpi-item-with-ref", name="KPI Ref Item", manufacturer_raw="ACME",
              mfr_ref="REF-1")

    prod_doc = seed_document(
        conn, content_hash="kpi-hash-production", archive_url="local://kpi/production.pdf",
        doc_type="DoC", regulation="MDR", coverage_scope="group", status="production",
        cert_number="CERT-KPI-PROD",
    )
    seed_link(conn, "kpi-item-covered", prod_doc, match_basis="ref-list", status="production")

    # Covered by a NON-DEVICE document only: an ISO 13485 quality-system
    # certificate, regulation `n.a.`. It is a real production document and a
    # real production link, so K1's "any paper" line must count it -- and the
    # device line must not, because a QMS certificate evidences the
    # manufacturer's process, never a given article's conformity. CARL MARTIN
    # arrived in exactly this state on 2026-08-21: 2.567 items, 100% covered,
    # zero device documents.
    seed_item(conn, "kpi-item-paper-only", name="KPI Paper Only Item",
              manufacturer_raw="ACME")
    qms_doc = seed_document(
        conn, content_hash="kpi-hash-qms", archive_url="local://kpi/qms.pdf",
        doc_type="ISO", regulation="n.a.", coverage_scope="manufacturer",
        status="production", cert_number="CERT-KPI-QMS",
    )
    seed_link(conn, "kpi-item-paper-only", qms_doc, match_basis="mfr-scope",
              status="production")

    seed_extraction_cost(conn, content_hash="kpi-hash-production", cost_usd=0.01234)
    seed_extraction_cost(conn, content_hash="kpi-hash-production", tier="T2",
                          model_id="claude-sonnet-4-6", cost_usd=None)   # pricing gap

    return {"prod_doc": prod_doc, "qms_doc": qms_doc}


def seed_demo(conn) -> dict:
    """One representative row of each kind the review UI needs to show.
    Returns the ids so tests can assert against them."""
    seed_item(conn, "seed-item-1", name="Seed Composite A", manufacturer_raw="Ivoclar")
    seed_item(conn, "seed-item-2", name="Seed Composite B", manufacturer_raw="Ivoclar")
    seed_item(conn, "seed-item-3", name="Seed Bur C", manufacturer_raw="Komet")

    # 1. plain staged doc (score in [MED,HIGH) or missing ref_gate) — no manual_task
    staged_doc = seed_document(
        conn, content_hash="seed-hash-staged-1", archive_url="local://seed/staged-1.pdf",
        doc_type="DoC", regulation="MDR", coverage_scope="group",
        cert_number="CERT-STAGED-1",
    )
    seed_link(conn, "seed-item-1", staged_doc, match_basis="ref-list", status="staged")

    # 2. manual-disposition doc (low score / blocking flag) — has an open gate-manual task
    manual_doc = seed_document(
        conn, content_hash="seed-hash-manual-1", archive_url="local://seed/manual-1.pdf",
        doc_type="DoC", regulation="MDD", coverage_scope="group",
        cert_number="CERT-MANUAL-1",
        evidence_fields=[
            _evidence_row("type", "DoC", tier="T0", conf=1.0),
            _evidence_row("regulation", "MDD", tier="T1", conf=0.6),
            _evidence_row("coverage_scope", "group", tier="T1", conf=0.55),
        ],
    )
    seed_link(conn, "seed-item-2", manual_doc, match_basis="name-family", status="staged")
    manual_task = seed_manual_task(
        conn, kind="gate-manual", doc_id=manual_doc, group_id=4407,
        payload={"flags": ["no-item-identifier"], "tier_attempts": {
            "regulation": {"tier": "T1", "conf": 0.6}, "coverage_scope": {"tier": "T1", "conf": 0.55},
        }},
    )

    # 3. manufacturer-scope binding candidate (C4 / §7b)
    mfr_doc = seed_document(
        conn, content_hash="seed-hash-mfrbind-1", archive_url="local://seed/mfr-iso.pdf",
        doc_type="ISO", regulation="n.a.", coverage_scope="manufacturer",
        validity_to=None, cert_number="ISO-13485-IVOCLAR",
    )
    mfr_task = seed_manual_task(
        conn, kind="gate-manual", doc_id=mfr_doc, group_id=None,
        payload={"route": "mfr-binding", "manufacturer": "Ivoclar"},
    )

    # 4. production doc carrying one production link + one staged (name-family) straggler
    prod_doc = seed_document(
        conn, content_hash="seed-hash-production-1", archive_url="local://seed/production-1.pdf",
        doc_type="DoC", regulation="MDR", coverage_scope="group", status="production",
        cert_number="CERT-PROD-1",
    )
    seed_link(conn, "seed-item-1", prod_doc, match_basis="ref-list", status="production")
    seed_link(conn, "seed-item-3", prod_doc, match_basis="name-family", status="staged")

    # 5. peripheral manual_task kinds (no producer handler yet — display only)
    dead_end_task = seed_manual_task(
        conn, kind="discovery-dead-end", group_id=9001,
        payload={"prefilled_search_links": ["https://www.google.com/search?q=Komet+DoC"]},
    )

    # 6. a dead job
    dead_job = seed_dead_job(
        conn, type="fetch.url", payload={"url": "https://example.com/broken.pdf"},
        dedupe_key="seed:dead:broken-fetch",
        error="httpx.ConnectError: [Errno -2] Name or service not known",
    )

    return {
        "staged_doc": staged_doc,
        "manual_doc": manual_doc,
        "manual_task": manual_task,
        "mfr_doc": mfr_doc,
        "mfr_task": mfr_task,
        "prod_doc": prod_doc,
        "dead_end_task": dead_end_task,
        "dead_job": dead_job,
    }
