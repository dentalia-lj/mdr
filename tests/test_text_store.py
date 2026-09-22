"""document_text: parsed-text capture per content hash."""
from app.extract.pdf import open_doc
from app.extract.text_store import store_text


def test_stores_text_for_a_text_layer_pdf(conn, fixture_pdf):
    path = fixture_pdf("KOMET/533068_RA_812_Liste_DoC.pdf")
    out = store_text(conn, "hash-komet", str(path))
    assert out["source"] == "pdf-text"
    assert out["chars"] > 0
    row = conn.execute(
        "SELECT source, pages, chars, content FROM document_text WHERE content_hash=%s",
        ("hash-komet",),
    ).fetchone()
    assert row["source"] == "pdf-text"
    assert row["pages"] >= 1
    assert "[[page 1]]" in row["content"]
    assert row["chars"] == len(row["content"])


def test_records_a_scan_as_source_none_rather_than_skipping(conn, fixture_pdf):
    path = fixture_pdf("DENTAURUM/TD_14_Konformitätserklärung_Kl. IIa_2021_05_25.pdf")
    out = store_text(conn, "hash-scan", str(path))
    assert out["source"] == "none"
    assert out["chars"] == 0
    row = conn.execute(
        "SELECT source, content FROM document_text WHERE content_hash=%s", ("hash-scan",)
    ).fetchone()
    assert row is not None, "a scan must leave a row, so we can count how many"
    assert row["content"] == ""


def test_is_idempotent_on_the_same_hash(conn, fixture_pdf):
    path = fixture_pdf("KOMET/533068_RA_812_Liste_DoC.pdf")
    store_text(conn, "hash-idem", str(path))
    store_text(conn, "hash-idem", str(path))
    n = conn.execute(
        "SELECT count(*) AS n FROM document_text WHERE content_hash=%s", ("hash-idem",)
    ).fetchone()["n"]
    assert n == 1


def test_reads_a_supplied_open_document_rather_than_opening_its_own(conn, fixture_pdf):
    """EXTRACT needs this same document for the tier ladder immediately after,
    and it is one immutable archived file, so it opens once and hands the
    document over. Proven the same way as the short-circuit above: the
    archive_url given here cannot be opened, so an open of our own would land
    in the failure branch and store nothing at all.

    The document stays open afterwards -- it belongs to the caller, which is
    about to run the tier ladder over it."""
    path = fixture_pdf("KOMET/533068_RA_812_Liste_DoC.pdf")
    doc = open_doc(path)
    try:
        out = store_text(conn, "hash-supplied", "/nonexistent/does-not-exist.pdf",
                         doc=doc)
        assert out["source"] == "pdf-text"
        assert out["chars"] > 0
        row = conn.execute(
            "SELECT chars, content FROM document_text WHERE content_hash=%s",
            ("hash-supplied",),
        ).fetchone()
        assert "[[page 1]]" in row["content"]
        assert row["chars"] == out["chars"]
        assert doc.is_closed is False
    finally:
        doc.close()


def test_second_call_short_circuits_and_does_not_reparse(conn, fixture_pdf):
    """The row is keyed on content_hash and the archived bytes are immutable,
    so an existing row is current by definition: a second call must return it
    without touching archive_url again. Proven by pointing the second call at
    a path that cannot be opened -- if the short-circuit did not fire, this
    would hit the open-failure branch and come back as a source='none'
    summary instead of the first call's real, already-stored one."""
    path = fixture_pdf("KOMET/533068_RA_812_Liste_DoC.pdf")
    first = store_text(conn, "hash-short-circuit", str(path))
    assert first["source"] == "pdf-text"
    assert first["chars"] > 0

    second = store_text(conn, "hash-short-circuit", "/nonexistent/does-not-exist.pdf")
    assert second == first

    n = conn.execute(
        "SELECT count(*) AS n FROM document_text WHERE content_hash=%s",
        ("hash-short-circuit",),
    ).fetchone()["n"]
    assert n == 1
