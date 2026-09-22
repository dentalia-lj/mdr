"""komet-coverage: link a manufacturer's items from the index it ships.

Same shape as repair_ref_list -- plan / render / apply, dry run by default --
because it is the same operation: append an extraction_attempt at a new rev and
re-point the pending validate.doc at it, so the existing gate forms the links.
"""

from __future__ import annotations

import pytest

from app import komet_coverage as kc


KEY = "532624"


def _seed_index(conn, tmp_path, rows):
    """Archive an index file the way BACKFILL does, and ledger it. Returns the
    archive_url the CLI must reconstruct."""
    import hashlib, openpyxl
    from app.handlers import archiving

    src = tmp_path / "1-Where to find DoC (Medical Devices Only).xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Medical Devices Only - find DoC"
    ws.append(["Article no.", "Reference no.", "UMNDS",
               "Product family (Where to find DoC)"])
    for r in rows:
        ws.append(r)
    wb.save(str(src))
    body = src.read_bytes()
    content_hash = hashlib.sha256(body).hexdigest()
    rel = archiving.archive_path("KOMET", content_hash, src.name,
                                 "application/octet-stream")
    archive_root = tmp_path / "archive"
    dest = archive_root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    conn.execute(
        "INSERT INTO fetch_log (url_normalized, content_hash, source, fetched_at, "
        "last_checked_at) VALUES (%s, %s, 'backfill', now(), now())",
        (f"/imports/dentalia-sftp/KOMET/{src.name}", content_hash))
    return str(dest), archive_root


def _seed_document(conn, content_hash, archive_url, fields=None):
    """A document as GATE leaves it, plus the extraction it came from."""
    from app.extract import tiers
    fields = fields or {
        "type": {"value": "DoC", "conf": 0.99, "tier": "T0", "verbatim": "x", "page": 1},
        "regulation": {"value": "MDR", "conf": 1.0, "tier": "T0", "verbatim": "x", "page": 1},
        "coverage_scope": {"value": "group", "conf": 0.97, "tier": "T0",
                           "verbatim": "x", "page": 1},
    }
    tiers.write_extraction_attempt(conn, content_hash, ["T0"], fields, extract_rev=1)
    conn.execute(
        "INSERT INTO document (content_hash, archive_url, type, regulation, "
        "coverage_scope, status) VALUES (%s, %s, 'DoC', 'MDR', 'group', 'staged') "
        "ON CONFLICT (content_hash) DO NOTHING",
        (content_hash, archive_url))
    return fields


_DOCX_SOURCE = "Artikli, kjer je originalni proizvajalec drug kot Komet/SPECIFIKACIJA.docx"


def _seed_docx_index(conn, tmp_path, archive_root, rows):
    """Archive the phase-2 index the way BACKFILL does. Without this the docx
    is simply never archived, `plan` skips it for want of a ledger row, and a
    test claiming the PHASE guard suppressed it would pass for the wrong
    reason."""
    import hashlib, zipfile
    from app.handlers import archiving

    W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

    def row(cells):
        tcs = "".join(f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in cells)
        return f"<w:tr>{tcs}</w:tr>"

    grid = [row(["Article no (Komet)", "Reference no.", "Where to find DoC"])]
    grid += [row(r) for r in rows]
    xml = (f'<?xml version="1.0"?><w:document xmlns:w="{W}"><w:body>'
           f'<w:tbl>{"".join(grid)}</w:tbl></w:body></w:document>')

    src = tmp_path / "SPECIFIKACIJA.docx"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("word/document.xml", xml)
    body = src.read_bytes()
    content_hash = hashlib.sha256(body).hexdigest()
    dest = archive_root / archiving.archive_path(
        "KOMET", content_hash, _DOCX_SOURCE, "application/octet-stream")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    conn.execute(
        "INSERT INTO fetch_log (url_normalized, content_hash, source, fetched_at, "
        "last_checked_at) VALUES (%s, %s, 'backfill', now(), now())",
        (f"/imports/dentalia-sftp/KOMET/{_DOCX_SOURCE}", content_hash))


def test_plan_maps_a_document_to_the_articles_its_family_covers(conn, tmp_path, monkeypatch):
    _, archive_root = _seed_index(conn, tmp_path, [
        ["000085K3", "H1.314.006", "16-668", KEY],
        ["000086K3", "H1.314.007", "16-668", KEY],
        ["000090K2", "H1.314.012", "16-668", "999999"],
    ])
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(archive_root))
    _seed_document(conn, "hash-532624",
                   f"/archive/KOMET/doc/abcdef__{KEY}_RA_810_DoC_EU_MDR_SIGNED.pdf")

    plans, skipped = kc.plan(conn)

    assert len(plans) == 1
    p = plans[0]
    assert p["key"] == KEY
    assert p["content_hash"] == "hash-532624"
    assert sorted(p["ref_list"]) == ["H1.314.006", "H1.314.007"]   # not the 999999 row
    families = [s for s in skipped if "key" in s]     # not the phase-2 source skip
    assert [s["reason"] for s in families] == ["no document for family"]
    assert families[0]["key"] == "999999"


def test_plan_writes_nothing(conn, tmp_path, monkeypatch):
    """Dry run is the default and it must be observably free."""
    _, archive_root = _seed_index(conn, tmp_path, [["000085K3", "H1.314.006", "16-668", KEY]])
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(archive_root))
    _seed_document(conn, "hash-532624",
                   f"/archive/KOMET/doc/abcdef__{KEY}_RA_810_DoC_EU_MDR_SIGNED.pdf")
    before_att = conn.execute("SELECT count(*) c FROM extraction_attempt").fetchone()["c"]
    before_job = conn.execute("SELECT count(*) c FROM job").fetchone()["c"]

    kc.plan(conn)

    assert conn.execute("SELECT count(*) c FROM extraction_attempt").fetchone()["c"] == before_att
    assert conn.execute("SELECT count(*) c FROM job").fetchone()["c"] == before_job


def test_apply_appends_a_new_rev_and_re_emits_validate(conn, tmp_path, monkeypatch):
    """Same mechanism as repair_ref_list: the document's own fields survive,
    the map's ref_list joins them at rev+1, and validate.doc is re-pointed."""
    _, archive_root = _seed_index(conn, tmp_path, [["000085K3", "H1.314.006", "16-668", KEY]])
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(archive_root))
    _seed_document(conn, "hash-532624",
                   f"/archive/KOMET/doc/abcdef__{KEY}_RA_810_DoC_EU_MDR_SIGNED.pdf")

    stats = kc.apply(conn, kc.plan(conn)[0])

    assert stats["appended"] == 1
    assert stats["jobs_emitted"] == 1
    row = conn.execute(
        "SELECT extract_rev, fields FROM extraction_attempt "
        "WHERE content_hash='hash-532624' ORDER BY extract_rev DESC LIMIT 1").fetchone()
    assert row["extract_rev"] == 2
    assert row["fields"]["ref_list"]["value"] == ["H1.314.006"]
    assert row["fields"]["type"]["value"] == "DoC"          # the document's own fields survive
    job = conn.execute(
        "SELECT payload FROM job WHERE type='validate.doc' ORDER BY id DESC LIMIT 1").fetchone()
    assert job["payload"]["extract_rev"] == 2


def test_the_map_sourced_ref_list_carries_the_index_as_its_handle(conn, tmp_path, monkeypatch):
    """Invariant 2 asks where the value was READ. It was read from the index,
    not from the declaration, and the marker is what earns the map-supplier
    basis in VALIDATE."""
    index_path, archive_root = _seed_index(
        conn, tmp_path, [["000085K3", "H1.314.006", "16-668", KEY]])
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(archive_root))
    _seed_document(conn, "hash-532624",
                   f"/archive/KOMET/doc/abcdef__{KEY}_RA_810_DoC_EU_MDR_SIGNED.pdf")

    kc.apply(conn, kc.plan(conn)[0])

    ev = conn.execute(
        "SELECT fields FROM extraction_attempt WHERE content_hash='hash-532624' "
        "ORDER BY extract_rev DESC LIMIT 1").fetchone()["fields"]["ref_list"]
    assert ev["source"] == "coverage-map"
    assert ev["archive_url"].endswith(".xlsx")
    assert ev["tier"] == "T0"
    assert ev["page"] is None          # T0 evidence is legitimately pageless
    assert "532624" in ev["verbatim"]


def test_a_family_with_no_document_is_skipped_and_counted(conn, tmp_path, monkeypatch):
    """`533231` is named in SPECIFIKACIJA.docx and has no folder and no file
    anywhere in the corpus. Counted, never fatal, never silent."""
    _, archive_root = _seed_index(conn, tmp_path, [["043690K0", "9978.000.000", "", "533231"]])
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(archive_root))

    plans, skipped = kc.plan(conn)

    assert plans == []
    families = [s for s in skipped if "key" in s]     # not the phase-2 source skip
    assert families == [{"key": "533231", "articles": 1, "reason": "no document for family"}]
    assert any("533231" in line for line in kc.render(plans, skipped))


def test_applying_twice_is_idempotent(conn, tmp_path, monkeypatch):
    """A second run must not stack revisions or duplicate validate jobs: the
    ref_list it would write is already the document's current one."""
    _, archive_root = _seed_index(conn, tmp_path, [["000085K3", "H1.314.006", "16-668", KEY]])
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(archive_root))
    _seed_document(conn, "hash-532624",
                   f"/archive/KOMET/doc/abcdef__{KEY}_RA_810_DoC_EU_MDR_SIGNED.pdf")
    kc.apply(conn, kc.plan(conn)[0])

    plans, skipped = kc.plan(conn)

    assert plans == []
    families = [s for s in skipped if "key" in s]     # not the phase-2 source skip
    assert [s["reason"] for s in families] == ["already carries this list"]
    assert conn.execute(
        "SELECT max(extract_rev) m FROM extraction_attempt "
        "WHERE content_hash='hash-532624'").fetchone()["m"] == 2


def test_the_index_content_hash_is_reported(conn, tmp_path, monkeypatch):
    """The one survivor of the update discussion: a re-run against a changed
    index is a human decision, not a silent re-map, so the hash is printed."""
    _, archive_root = _seed_index(conn, tmp_path, [["000085K3", "H1.314.006", "16-668", KEY]])
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(archive_root))
    _seed_document(conn, "hash-532624",
                   f"/archive/KOMET/doc/abcdef__{KEY}_RA_810_DoC_EU_MDR_SIGNED.pdf")

    lines = kc.render(*kc.plan(conn))

    assert any("index" in l.lower() and len([t for t in l.split() if len(t) == 64]) == 1
               for l in lines), lines


def test_the_cross_manufacturer_index_is_deferred_not_linked(conn, tmp_path, monkeypatch):
    """`SPECIFIKACIJA.docx` names the articles whose original manufacturer is
    NOT Komet, against documents issued by Shofu, MetaBiomed, Becht, Stoddard
    and TUV SUD. Linking them here would assert coverage across a manufacturer
    boundary -- what invariant 3's overlap rule exists to prevent -- so the
    playbook marks that source `phase: 2` and phase 1 contributes no link from
    it. Deferred is not dropped: it is reported, like every other skip."""
    _, archive_root = _seed_index(conn, tmp_path, [["000085K3", "H1.314.006", "16-668", KEY]])
    _seed_docx_index(conn, tmp_path, archive_root,
                     [["000199K1", "H2.314.010", "533157"]])
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(archive_root))
    _seed_document(conn, "hash-532624",
                   f"/archive/KOMET/doc/abcdef__{KEY}_RA_810_DoC_EU_MDR_SIGNED.pdf")
    # a document under the deferred source's own folder, keyed like its rows:
    # with the PHASE guard removed this document IS matched and planned, which
    # is what makes the assertion below discriminate.
    _seed_document(conn, "hash-533157",
                   "/archive/KOMET/doc/beef01__533157_DoC.pdf")
    conn.execute("UPDATE document SET source_url=%s WHERE content_hash='hash-533157'",
                 ("Artikli, kjer je originalni proizvajalec drug kot Komet/533157/x.pdf",))

    plans, skipped = kc.plan(conn)

    assert [p["key"] for p in plans] == [KEY]          # 533157 contributes nothing
    deferred = [s for s in skipped if s.get("source", "").endswith("SPECIFIKACIJA.docx")]
    assert deferred and deferred[0]["reason"] == "deferred to phase 2"
    assert any("SPECIFIKACIJA.docx" in line for line in kc.render(plans, skipped))


def test_a_foreign_manufacturers_document_is_never_matched_by_a_family_key(
        conn, tmp_path, monkeypatch):
    """A family key is a bare six-digit number matched as a SUBSTRING. Another
    manufacturer's document carrying those digits must not become a Komet
    family match -- `apply` would append Komet's article list onto a foreign
    document's extraction at conf 1.0, asserting coverage nobody stated."""
    _, archive_root = _seed_index(conn, tmp_path, [["000085K3", "H1.314.006", "16-668", KEY]])
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", str(archive_root))
    _seed_document(conn, "hash-voco",
                   f"/archive/VOCO/doc/abcdef__{KEY}_RA_810_DoC_EU_MDR_SIGNED.pdf")

    plans, skipped = kc.plan(conn)

    assert plans == []
    assert [s["reason"] for s in skipped if "key" in s] == ["no document for family"]
