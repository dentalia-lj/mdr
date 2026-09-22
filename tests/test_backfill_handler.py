"""backfill.scan (handbook §sibling-producers): walk a local corpus, hash each
file, upsert fetch_log(source=backfill), emit extract.doc ONCE per unseen hash
(dedupe_key=extract:{h}, group_id=None). The hash-dedup IS the two-cycle property
(C2/C3/AC5): re-scanning the same content emits nothing.

Tests don't commit (conn fixture rolls back); the two-cycle case reuses one
connection so the second scan sees the first scan's (uncommitted) fetch_log rows,
which is the same visibility the dedup query relies on in production.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.storage import LocalFsStore
from app.extract.pdf import resolve_local
from app.handlers import backfill as bh


class _MemStore:
    """StorageAdapter stub recording (body, path) per put. Tests that need the
    archived bytes on a real disk pass a LocalFsStore rooted OUTSIDE the scanned
    folder instead — an archive under the corpus root would be re-scanned by the
    next cycle's rglob and double-counted."""

    def __init__(self):
        self.puts: list[tuple[bytes, str]] = []

    def put(self, body: bytes, path: str) -> str:
        self.puts.append((body, path))
        return f"/archive/{path}"


def _scan(conn, folder, store=None):
    return bh.handle_backfill_scan(
        conn, {"payload": {"drive_folder": str(folder)}}, store=store or _MemStore()
    )


def _extract_jobs(conn):
    return conn.execute(
        "SELECT payload, dedupe_key FROM job WHERE type='extract.doc' ORDER BY id"
    ).fetchall()


def test_backfill_emits_extract_per_unique_hash(conn, tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"AAAA")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.pdf").write_bytes(b"BBBB")

    res = _scan(conn, tmp_path)

    assert res["scanned"] == 2
    assert res["emitted"] == 2
    jobs = _extract_jobs(conn)
    assert len(jobs) == 2
    assert all(j["payload"]["group_id"] is None for j in jobs)         # backfill self-identifies
    assert all(j["payload"]["archive_url"] for j in jobs)
    assert all(j["payload"]["content_hash"] for j in jobs)
    assert all(j["dedupe_key"] == f"extract:{j['payload']['content_hash']}" for j in jobs)
    assert conn.execute(
        "SELECT count(*) c FROM fetch_log WHERE source='backfill'").fetchone()["c"] == 2


# --------------------------------------------------------------------------- #
# companion files: one manufacturer's document split across two PDFs
# --------------------------------------------------------------------------- #
def _komet(tmp_path):
    """A scan root named for the brand -- `_brand` is the folder name, and that
    is what resolves the playbook carrying the companion rule.

    komet.json also declares a `coverage_map` with two index sources (a
    spreadsheet, a Word table nested a subdirectory down), and Task 4 makes a
    declared source that is absent FAIL the whole scan -- not specific to
    coverage-map tests, since brand resolution is playbook-wide, not
    per-behavior. So every KOMET-named scan needs both present, and this
    shared fixture carries them so companion/canonical tests are not tripped
    by an invariant they are not testing."""
    folder = tmp_path / "KOMET"
    folder.mkdir()
    (folder / "1-Where to find DoC (Medical Devices Only).xlsx").write_bytes(b"XLSX-BYTES")
    docx_dir = folder / "Artikli, kjer je originalni proizvajalec drug kot Komet"
    docx_dir.mkdir(parents=True)
    (docx_dir / "SPECIFIKACIJA.docx").write_bytes(b"DOCX-BYTES")
    return folder


def test_an_annex_is_archived_and_ledgered_but_never_becomes_a_document(conn, tmp_path):
    """Komet's `_RA_812_Liste_DoC.pdf` reads as a Declaration of Conformity in
    its own right -- same manufacturer, group, regulation and dates as the
    declaration it annexes -- so filing both leaves two DoCs racing to supersede
    each other over one product list (invariant 5). It is still archived and
    still ledgered; it just gets no document."""
    folder = _komet(tmp_path)
    (folder / "532624_RA_810_DoC_EU_SIGNED.pdf").write_bytes(b"DECLARATION")
    (folder / "532624_RA_812_Liste_DoC.pdf").write_bytes(b"THE-LIST")
    store = _MemStore()

    res = bh.handle_backfill_scan(
        conn, {"payload": {"drive_folder": str(folder)}}, store=store)

    assert res["scanned"] == 2
    assert res["emitted"] == 1
    assert res["annexes"] == 1
    jobs = _extract_jobs(conn)
    assert len(jobs) == 1
    assert jobs[0]["payload"]["source_url"].endswith("_RA_810_DoC_EU_SIGNED.pdf")
    # archived and ledgered all the same -- suppressed as a document, not as
    # bytes. Membership, not an exact total: `_komet` also carries komet.json's
    # own coverage-map index files (archived and ledgered on every KOMET scan,
    # unrelated to annex suppression), so a total here would silently move
    # every time komet.json's source count changes.
    assert any(p.endswith("_RA_812_Liste_DoC.pdf") for _, p in store.puts)
    assert any(p.endswith("_RA_810_DoC_EU_SIGNED.pdf") for _, p in store.puts)
    assert conn.execute(
        "SELECT count(*) c FROM fetch_log WHERE source='backfill' "
        "AND url_normalized LIKE '%%_RA_812_Liste_DoC.pdf'").fetchone()["c"] == 1
    assert conn.execute(
        "SELECT count(*) c FROM fetch_log WHERE source='backfill' "
        "AND url_normalized LIKE '%%_RA_810_DoC_EU_SIGNED.pdf'").fetchone()["c"] == 1


def test_a_declaration_carries_the_handle_of_its_annex(conn, tmp_path):
    folder = _komet(tmp_path)
    (folder / "532624_RA_810_DoC_EU_SIGNED.pdf").write_bytes(b"DECLARATION")
    (folder / "532624_RA_812_Liste_DoC.pdf").write_bytes(b"THE-LIST")

    res = bh.handle_backfill_scan(
        conn, {"payload": {"drive_folder": str(folder)}}, store=_MemStore())

    assert res["companions_attached"] == 1
    payload = _extract_jobs(conn)[0]["payload"]
    assert payload["companion_archive_url"].endswith("__532624_RA_812_Liste_DoC.pdf")
    assert payload["companion_archive_url"] != payload["archive_url"]


def test_one_annex_serves_every_declaration_sharing_its_number(conn, tmp_path):
    """`532624` ships an MDD declaration and an MDR one over the same product
    list. The mapping is many-to-one: the first primary to claim the annex must
    not consume it."""
    folder = _komet(tmp_path)
    (folder / "532624_RA_810_DoC_EU_SIGNED.pdf").write_bytes(b"MDD-DECLARATION")
    (folder / "532624_RA_810_DoC_EU_MDR_SIGNED.pdf").write_bytes(b"MDR-DECLARATION")
    (folder / "532624_RA_812_Liste_DoC.pdf").write_bytes(b"THE-LIST")

    res = bh.handle_backfill_scan(
        conn, {"payload": {"drive_folder": str(folder)}}, store=_MemStore())

    assert res["emitted"] == 2
    assert res["companions_attached"] == 2
    urls = {j["payload"]["companion_archive_url"] for j in _extract_jobs(conn)}
    assert len(urls) == 1


def test_an_annex_seen_in_an_earlier_dump_still_hands_over_its_handle(conn, tmp_path):
    """The reason the annex takes no seen-before short-circuit. If the list
    arrived in an earlier scan and the declaration only turns up now, the
    hash-dedup would skip the annex before its handle was ever learned and the
    declaration would be enqueued listless -- with nothing in the result to say
    so."""
    folder = _komet(tmp_path)
    annex = folder / "532624_RA_812_Liste_DoC.pdf"
    annex.write_bytes(b"THE-LIST")
    bh.handle_backfill_scan(conn, {"payload": {"drive_folder": str(folder)}},
                            store=_MemStore())

    (folder / "532624_RA_810_DoC_EU_SIGNED.pdf").write_bytes(b"DECLARATION")
    res = bh.handle_backfill_scan(conn, {"payload": {"drive_folder": str(folder)}},
                                  store=_MemStore())

    assert res["companions_attached"] == 1
    assert _extract_jobs(conn)[-1]["payload"]["companion_archive_url"].endswith(
        "__532624_RA_812_Liste_DoC.pdf")


def test_a_key_claimed_by_two_annexes_pairs_nothing(conn, tmp_path):
    """Komet's `533173` ships its list twice, once per device class
    (`..._Liste_DoC_Klasse_I.pdf` / `..._Liste_DoC_Klasse_Is.pdf`), against three
    declarations. The leading number does not say which list belongs to which,
    and last-one-wins handed the Class Is list to the Class I declaration and
    filed it as evidence -- a confident wrong coverage claim, which is worse
    than no claim at all.

    So the key is dropped whole: the declarations keep no list and go to the
    manual queue, both annexes stay ordinary documents, and the count says so."""
    folder = _komet(tmp_path)
    (folder / "533173_RA_810_DoC_MDR_I_SIGNED.pdf").write_bytes(b"CLASS-I-DECLARATION")
    (folder / "533173_RA_810_DoC_MDR_Is_SIGNED.pdf").write_bytes(b"CLASS-IS-DECLARATION")
    (folder / "533173_RA_812_Liste_DoC_Klasse_I.pdf").write_bytes(b"CLASS-I-LIST")
    (folder / "533173_RA_812_Liste_DoC_Klasse_Is.pdf").write_bytes(b"CLASS-IS-LIST")

    res = bh.handle_backfill_scan(
        conn, {"payload": {"drive_folder": str(folder)}}, store=_MemStore())

    assert res["companion_ambiguous"] == 1
    assert res["companions_attached"] == 0
    assert res["annexes"] == 0
    assert res["emitted"] == 4          # nothing suppressed on an ambiguous key
    assert all("companion_archive_url" not in j["payload"] for j in _extract_jobs(conn))


def test_an_unambiguous_key_still_pairs_beside_an_ambiguous_one(conn, tmp_path):
    """The refusal is per key, not per scan: one bad number must not cost the
    other 38 their lists."""
    folder = _komet(tmp_path)
    (folder / "533173_RA_810_DoC_MDR_I_SIGNED.pdf").write_bytes(b"AMBIGUOUS-DECLARATION")
    (folder / "533173_RA_812_Liste_DoC_Klasse_I.pdf").write_bytes(b"CLASS-I-LIST")
    (folder / "533173_RA_812_Liste_DoC_Klasse_Is.pdf").write_bytes(b"CLASS-IS-LIST")
    (folder / "532624_RA_810_DoC_EU_SIGNED.pdf").write_bytes(b"CLEAN-DECLARATION")
    (folder / "532624_RA_812_Liste_DoC.pdf").write_bytes(b"CLEAN-LIST")

    res = bh.handle_backfill_scan(
        conn, {"payload": {"drive_folder": str(folder)}}, store=_MemStore())

    assert res["companion_ambiguous"] == 1
    assert res["companions_attached"] == 1
    assert res["annexes"] == 1
    paired = [j for j in _extract_jobs(conn) if "companion_archive_url" in j["payload"]]
    assert len(paired) == 1
    assert paired[0]["payload"]["source_url"].endswith("532624_RA_810_DoC_EU_SIGNED.pdf")


def test_a_manufacturer_without_the_rule_files_both_files_as_documents(conn, tmp_path):
    """The pairing is playbook-scoped. Identical filenames under a brand with no
    `companion` rule stay two ordinary documents -- nothing is suppressed on the
    strength of a name alone."""
    folder = tmp_path / "VOCO"
    folder.mkdir()
    (folder / "532624_RA_810_DoC_EU_SIGNED.pdf").write_bytes(b"DECLARATION")
    (folder / "532624_RA_812_Liste_DoC.pdf").write_bytes(b"THE-LIST")

    res = bh.handle_backfill_scan(
        conn, {"payload": {"drive_folder": str(folder)}}, store=_MemStore())

    assert res["emitted"] == 2
    assert res["annexes"] == 0
    assert res["companions_attached"] == 0
    assert all("companion_archive_url" not in j["payload"] for j in _extract_jobs(conn))


def test_the_merged_form_wins_and_the_bare_one_is_not_a_document(conn, tmp_path):
    """Komet publishes the MDR declaration bare AND merged with its list. Both
    would file as DoC/MDR over one group -- invariant 5's supersession triple,
    racing itself. The merged form is what Komet signed as one artifact, so it
    is the document and the bare form is kept only as bytes."""
    folder = _komet(tmp_path)
    (folder / "532797_RA_810_DoC_EU_MDR_List_SIGNED.pdf").write_bytes(b"MERGED")
    (folder / "532797_RA_810_DoC_EU_MDR_SIGNED.pdf").write_bytes(b"BARE-MDR")
    (folder / "532797_RA_810_DoC_EU_SIGNED.pdf").write_bytes(b"MDD")
    (folder / "532797_RA_812_Liste_DoC.pdf").write_bytes(b"LIST")

    res = bh.handle_backfill_scan(
        conn, {"payload": {"drive_folder": str(folder)}}, store=_MemStore())

    assert res["emitted"] == 2          # merged MDR + MDD declaration
    assert res["annexes"] == 1
    assert res["redundant"] == 1
    emitted = {j["payload"]["source_url"] for j in _extract_jobs(conn)}
    assert any(s.endswith("_MDR_List_SIGNED.pdf") for s in emitted)
    assert not any(s.endswith("_RA_810_DoC_EU_MDR_SIGNED.pdf") for s in emitted)


def test_a_family_with_no_merged_form_keeps_its_bare_declaration(conn, tmp_path):
    """11 families ship no merged file. Their declarations are the documents,
    and the annex mechanism supplies the list."""
    folder = _komet(tmp_path)
    (folder / "532624_RA_810_DoC_EU_MDR_SIGNED.pdf").write_bytes(b"BARE-MDR")
    (folder / "532624_RA_810_DoC_EU_SIGNED.pdf").write_bytes(b"MDD")
    (folder / "532624_RA_812_Liste_DoC.pdf").write_bytes(b"LIST")

    res = bh.handle_backfill_scan(
        conn, {"payload": {"drive_folder": str(folder)}}, store=_MemStore())

    assert res["emitted"] == 2
    assert res["redundant"] == 0


def test_two_merged_candidates_for_one_pair_is_a_refusal(conn, tmp_path):
    """Same discipline as the ambiguous annex key: when the rule cannot say
    which file is canonical, nothing is suppressed and the count says so.
    Guessing is what handed a Class Is list to a Class I declaration.

    A bare declaration for the SAME pair is the file that actually exposes a
    last-one-wins bug: without the ambiguous pair being dropped, the bare form
    gets silently marked redundant against whichever merged candidate happened
    to be seen last -- an arbitrary, confident, wrong pick, worse than filing
    it. So it must survive as its own document too."""
    folder = _komet(tmp_path)
    (folder / "532797_RA_810_DoC_EU_MDR_List_SIGNED.pdf").write_bytes(b"MERGED-A")
    (folder / "532797_RA_810_DoC_EU_MDR_DoC_List_SIGNED.pdf").write_bytes(b"MERGED-B")
    (folder / "532797_RA_810_DoC_EU_MDR_SIGNED.pdf").write_bytes(b"BARE-MDR")

    res = bh.handle_backfill_scan(
        conn, {"payload": {"drive_folder": str(folder)}}, store=_MemStore())

    assert res["canonical_ambiguous"] == 1
    assert res["redundant"] == 0
    assert res["emitted"] == 3
    emitted = {j["payload"]["source_url"] for j in _extract_jobs(conn)}
    assert any(s.endswith("_RA_810_DoC_EU_MDR_SIGNED.pdf") for s in emitted)


# --------------------------------------------------------------------------- #
# the coverage map's own index files: archived and ledgered, never documents
# --------------------------------------------------------------------------- #
def test_the_index_files_are_archived_and_ledgered(conn, tmp_path):
    """The CLI reads the ARCHIVED index, never the corpus path: the corpus is
    a dump that gets cleared, and a handle into it dies with the next one.

    komet.json declares TWO sources (the .xlsx and the docx nested a
    subdirectory below the brand folder, both created by `_komet`) -- both
    must be archived or the scan would silently short-circuit on whichever
    source ships first."""
    folder = _komet(tmp_path)
    (folder / "532624_RA_810_DoC_EU_SIGNED.pdf").write_bytes(b"DECLARATION")
    store = _MemStore()

    res = bh.handle_backfill_scan(
        conn, {"payload": {"drive_folder": str(folder)}}, store=store)

    assert res["index_archived"] == 2
    assert any(p.endswith(".xlsx") for _, p in store.puts)
    assert any(p.endswith(".docx") for _, p in store.puts)
    assert conn.execute(
        "SELECT count(*) c FROM fetch_log WHERE url_normalized LIKE '%%.xlsx'"
    ).fetchone()["c"] == 1
    assert conn.execute(
        "SELECT count(*) c FROM fetch_log WHERE url_normalized LIKE '%%.docx'"
    ).fetchone()["c"] == 1
    # and neither index file is a document
    assert all(not j["payload"]["source_url"].endswith((".xlsx", ".docx"))
               for j in _extract_jobs(conn))


def test_a_declared_index_file_that_is_missing_fails_the_scan(conn, tmp_path):
    """A playbook naming an index the corpus does not carry is an authoring or
    dump error. Reporting `scanned: N` and linking nothing would look like
    success, which is how a wrong path stayed invisible before."""
    folder = _komet(tmp_path)
    # remove the first declared source: simulates a corpus dump that does not
    # carry it (`_komet` creates both by default so other KOMET tests aren't
    # tripped by this same invariant)
    (folder / "1-Where to find DoC (Medical Devices Only).xlsx").unlink()
    (folder / "532624_RA_810_DoC_EU_SIGNED.pdf").write_bytes(b"DECLARATION")

    with pytest.raises(bh.BackfillError, match="index file"):
        bh.handle_backfill_scan(
            conn, {"payload": {"drive_folder": str(folder)}}, store=_MemStore())


def test_an_unreadable_index_file_fails_cleanly_not_with_a_raw_oserror(
        conn, tmp_path, monkeypatch):
    """`is_file()` says a declared source exists, but existence isn't
    readability -- a permission race or a file that vanishes between the check
    and the read still hits the filesystem. The PDF loop already turns that
    into a counted `errors += 1`; the index loop has no such counter (a missing
    index is fatal, not skippable), so it must still surface as the same clean
    `BackfillError` every other failure on this path raises, never a raw
    `OSError` that crashes the job outside the job's own error handling.

    The read is failed by patching, NOT by `chmod(0o000)`: this repo's `test`
    service declares no `USER`, so the suite runs as root, and mode bits do not
    stop root reading a file. A permission-based version of this test passes on
    a developer host and can never pass in the project's own container -- which
    is exactly how it shipped green and left master red. What is under test is
    the OSError-to-BackfillError contract, not the kernel's permission rules.
    """
    folder = _komet(tmp_path)
    (folder / "532624_RA_810_DoC_EU_SIGNED.pdf").write_bytes(b"DECLARATION")
    xlsx = folder / "1-Where to find DoC (Medical Devices Only).xlsx"

    real_read_bytes = Path.read_bytes

    def refusing_read(self, *args, **kwargs):
        if self == xlsx:
            raise PermissionError(13, "Permission denied")
        return real_read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", refusing_read)

    with pytest.raises(bh.BackfillError, match="cannot read"):
        bh.handle_backfill_scan(
            conn, {"payload": {"drive_folder": str(folder)}}, store=_MemStore())


def test_a_manufacturer_without_a_coverage_map_files_every_declaration(conn, tmp_path):
    folder = tmp_path / "VOCO"
    folder.mkdir()
    (folder / "532797_RA_810_DoC_EU_MDR_List_SIGNED.pdf").write_bytes(b"MERGED")
    (folder / "532797_RA_810_DoC_EU_MDR_SIGNED.pdf").write_bytes(b"BARE-MDR")

    res = bh.handle_backfill_scan(
        conn, {"payload": {"drive_folder": str(folder)}}, store=_MemStore())

    assert res["emitted"] == 2
    assert res["redundant"] == 0


def test_backfill_picks_up_uppercase_pdf_extension(conn, tmp_path):
    # The real corpus carries 4 files named *.PDF (two ISO 13485 certs, an MDR
    # DoC, an MSDS). A case-sensitive glob drops them silently — which both
    # loses compliance documents and breaks "nothing skipped is silent".
    (tmp_path / "lower.pdf").write_bytes(b"AAAA")
    (tmp_path / "UPPER.PDF").write_bytes(b"BBBB")
    (tmp_path / "Mixed.Pdf").write_bytes(b"CCCC")

    res = _scan(conn, tmp_path)

    assert res["scanned"] == 3
    assert res["emitted"] == 3
    assert len(_extract_jobs(conn)) == 3


def test_backfill_duplicate_content_emits_once(conn, tmp_path):
    (tmp_path / "one.pdf").write_bytes(b"SAME")
    (tmp_path / "two.pdf").write_bytes(b"SAME")   # identical bytes -> identical hash

    res = _scan(conn, tmp_path)

    assert res["scanned"] == 2
    assert res["emitted"] == 1        # one extraction for the shared content
    assert res["seen"] == 1
    assert len(_extract_jobs(conn)) == 1


def test_backfill_second_cycle_emits_nothing(conn, tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"AAAA")

    r1 = _scan(conn, tmp_path)
    r2 = _scan(conn, tmp_path)        # re-scan, unchanged content

    assert r1["emitted"] == 1
    assert r2["emitted"] == 0 and r2["seen"] == 1
    assert len(_extract_jobs(conn)) == 1   # never re-emitted (C2/C3/AC5)


def test_backfill_changed_content_re_emits(conn, tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"AAAA")
    r1 = _scan(conn, tmp_path)
    f.write_bytes(b"CHANGED")          # same path, new content -> new hash
    r2 = _scan(conn, tmp_path)

    assert r1["emitted"] == 1
    assert r2["emitted"] == 1          # a changed file is a new document
    assert len(_extract_jobs(conn)) == 2


def test_backfill_counts_unreadable_and_ignores_non_pdf(conn, tmp_path):
    (tmp_path / "good.pdf").write_bytes(b"X")
    (tmp_path / "notes.txt").write_bytes(b"ignored")   # not a pdf -> not scanned
    (tmp_path / "weird.pdf").mkdir()                    # dir matching *.pdf -> read fails

    res = _scan(conn, tmp_path)

    assert res["scanned"] == 2         # good.pdf + weird.pdf matched the glob; notes.txt did not
    assert res["emitted"] == 1
    assert res["errors"] == 1          # weird.pdf counted, not silently dropped


# --- archiving (option A) ---------------------------------------------------
# BACKFILL used to record the corpus path it walked as `archive_url`, which made
# the registry's only handle on a document a path on whichever machine ran the
# scan: unreadable from the worker/web containers (no imports mount, different
# mount point) and destroyed outright when the hand-managed SFTP dump is cleared
# and re-dumped. It now archives through the StorageAdapter like FETCH does, so
# the handle is content-addressed, deduplicated, and ours.

def test_backfill_archives_the_bytes_and_emits_the_archive_handle(conn, tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.pdf").write_bytes(b"AAAA")
    store = _MemStore()

    _scan(conn, corpus, store)

    assert len(store.puts) == 1
    body, path = store.puts[0]
    assert body == b"AAAA"                       # the bytes, not the path, are archived
    payload = _extract_jobs(conn)[0]["payload"]
    assert payload["archive_url"] == f"/archive/{path}"
    assert str(corpus) not in payload["archive_url"]   # never the corpus path again


def test_backfill_archive_survives_the_corpus_being_cleared(conn, tmp_path):
    # The corpus is a hand-managed SFTP dump that gets wiped and re-dumped. Once
    # a document is archived, the registry's handle must not depend on it: this
    # is end-state output 2 ("archive under our control").
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    src = corpus / "a.pdf"
    src.write_bytes(b"PDFBYTES")
    store = LocalFsStore(str(tmp_path / "archive"))

    _scan(conn, corpus, store)
    archive_url = _extract_jobs(conn)[0]["payload"]["archive_url"]
    src.unlink()                                  # imports cleared

    assert resolve_local(archive_url).read_bytes() == b"PDFBYTES"


def test_backfill_brand_is_the_scanned_folder_itself(conn, tmp_path):
    # The runbook mandates one brand folder per job (`/imports/.../GC`), so the
    # brand is the folder being scanned — NOT its first subdirectory. Deriving it
    # from `rel.parts[0]` filed the whole GC pilot under GC's own internal
    # subfolders: /archive/DOC/... and /archive/MSDS/... with the manufacturer
    # nowhere in the path (2026-08-12).
    brand_dir = tmp_path / "dentalia-sftp" / "GC"
    (brand_dir / "DOC").mkdir(parents=True)
    (brand_dir / "DOC" / "Unifast III.pdf").write_bytes(b"X")
    store = _MemStore()

    _scan(conn, brand_dir, store)

    _, path = store.puts[0]
    assert path.startswith("GC/")                 # the scanned folder IS the brand
    assert path.endswith("Unifast_III.pdf")       # original filename kept for humans


def test_backfill_brand_ignores_subfolder_depth(conn, tmp_path):
    # Two files under different subfolders of one brand must share the brand
    # bucket. Under the old rule these landed in two different buckets.
    brand_dir = tmp_path / "GC"
    (brand_dir / "DOC").mkdir(parents=True)
    (brand_dir / "MSDS").mkdir(parents=True)
    (brand_dir / "DOC" / "a.pdf").write_bytes(b"A")
    (brand_dir / "MSDS" / "b.pdf").write_bytes(b"B")
    store = _MemStore()

    _scan(conn, brand_dir, store)

    assert {p.split("/")[0] for _, p in store.puts} == {"GC"}


def test_backfill_file_at_the_scan_root_still_uses_the_folder_brand(conn, tmp_path):
    # A PDF sitting directly in the brand folder is the common case, not an edge:
    # it must get the same bucket as one nested in a subfolder.
    brand_dir = tmp_path / "VOCO"
    brand_dir.mkdir()
    (brand_dir / "loose.pdf").write_bytes(b"X")
    store = _MemStore()

    _scan(conn, brand_dir, store)

    assert store.puts[0][1].startswith("VOCO/")


def test_backfill_does_not_re_archive_a_hash_already_seen(conn, tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "one.pdf").write_bytes(b"SAME")
    (corpus / "two.pdf").write_bytes(b"SAME")     # identical bytes, second path
    store = _MemStore()

    res = _scan(conn, corpus, store)

    assert res["emitted"] == 1 and res["seen"] == 1
    assert len(store.puts) == 1                   # archived once, not once per path


def test_backfill_fetch_log_still_records_the_source_path(conn, tmp_path):
    # url_normalized is the SOURCE identity for the ledger, not the archive
    # handle: it answers "where did this come from", and the re-scan dedupe is
    # keyed on content_hash anyway (so a re-dump at a new path still skips).
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.pdf").write_bytes(b"AAAA")

    _scan(conn, corpus)

    row = conn.execute(
        "SELECT url_normalized FROM fetch_log WHERE source='backfill'"
    ).fetchone()
    assert row["url_normalized"] == str((corpus / "a.pdf").resolve())


# --- a scan that finds nothing is never silent -------------------------------
# "Skipped rows, missed fields ... counted and reported, never silent" applies
# hardest here: rglob on a missing directory yields nothing and raises nothing,
# so a mistyped drive_folder returned a clean `scanned: 0` that reads exactly
# like a corpus already fully ingested. The runbook's own kickoff example has
# carried the wrong payload key since 2026-07-29, which is precisely how a
# wrong path reaches this handler.

def test_backfill_raises_on_a_missing_folder(conn, tmp_path):
    with pytest.raises(bh.BackfillError, match="does not exist"):
        _scan(conn, tmp_path / "typo")


def test_backfill_raises_when_the_folder_holds_no_pdfs(conn, tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "notes.txt").write_bytes(b"not a pdf")

    with pytest.raises(bh.BackfillError, match="no PDFs"):
        _scan(conn, corpus)


def test_backfill_raises_before_writing_anything(conn, tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()

    with pytest.raises(bh.BackfillError):
        _scan(conn, corpus)

    assert conn.execute("SELECT count(*) c FROM fetch_log").fetchone()["c"] == 0
    assert _extract_jobs(conn) == []


def test_a_fully_ingested_corpus_is_not_an_error(conn, tmp_path):
    # The legitimate zero-EMISSION case: every file already seen. That must
    # still succeed -- it is the C2/C3 two-cycle property, not a failure.
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.pdf").write_bytes(b"AAAA")

    _scan(conn, corpus)
    res = _scan(conn, corpus)

    assert res["scanned"] == 1 and res["emitted"] == 0 and res["seen"] == 1


# --------------------------------------------------------------------------- #
# exclude: files in a corpus folder that are not documents at all
# --------------------------------------------------------------------------- #
def _excluding(monkeypatch, *patterns, reason="sales paperwork"):
    """Stub the playbook lookup so these test the MECHANISM, not the authored
    Neodent rule -- the rule is a separate change and must be free to move
    without breaking the guarantees below."""
    monkeypatch.setattr(bh, "_exclude_rule",
                        lambda brand: {"reason": reason, "filenames": list(patterns)})


def test_an_excluded_file_is_never_archived_never_ledgered_never_extracted(
        conn, tmp_path, monkeypatch):
    """The reason this key exists rather than a skip-after-archive like
    `companion`/`coverage_map`: Neodent's invoices carry its customers' names
    and addresses, so an excluded file must not reach the archive at all.
    Asserting only `emitted` would pass on an implementation that archived
    every invoice first."""
    _excluding(monkeypatch, r"^PDO\d")
    (tmp_path / "PDO26+05073.pdf").write_bytes(b"AN-INVOICE")
    (tmp_path / "declaration.pdf").write_bytes(b"A-DECLARATION")
    store = _MemStore()

    res = _scan(conn, tmp_path, store)

    assert res["excluded"] == 1
    assert res["scanned"] == 1          # the excluded file is not "scanned and skipped"
    assert res["emitted"] == 1
    assert [body for body, _ in store.puts] == [b"A-DECLARATION"]
    assert conn.execute(
        "SELECT count(*) c FROM fetch_log WHERE url_normalized LIKE '%%PDO26%%'"
    ).fetchone()["c"] == 0
    assert len(_extract_jobs(conn)) == 1


def test_exclusions_are_counted_per_pattern(conn, tmp_path, monkeypatch):
    """Silent over-matching is this key's failure mode -- a rule meant for
    invoices that also eats a declaration. A per-pattern count on the job
    result is what makes that visible without re-reading the corpus; a bare
    total would not distinguish which pattern fired."""
    _excluding(monkeypatch, r"^PDO\d", r"^dobavnica", r"^never-fires")
    (tmp_path / "PDO26+05073.pdf").write_bytes(b"1")
    (tmp_path / "PDO26+05166.pdf").write_bytes(b"2")
    (tmp_path / "dobavnica za DR. Celesnik.pdf").write_bytes(b"3")
    (tmp_path / "declaration.pdf").write_bytes(b"4")

    res = _scan(conn, tmp_path)

    assert res["excluded_by"] == {r"^PDO\d": 2, r"^dobavnica": 1, r"^never-fires": 0}
    assert res["excluded"] == 3


def test_exclude_matches_the_basename_not_the_path(conn, tmp_path, monkeypatch):
    """A corpus path carries the brand folder and the dump's own directory
    names. A pattern matched against the full path would fire on files it was
    never written for -- here, every PDF under a folder that happens to be
    named for an invoice batch.

    The pattern is deliberately UNANCHORED. An anchored `^PDO\\d` passes this
    test against the full path too, purely because an absolute tmp path starts
    with `/tmp`, so it demonstrates nothing about where the match is applied."""
    _excluding(monkeypatch, r"PDO\d")
    sub = tmp_path / "PDO26 batch"
    sub.mkdir()
    (sub / "declaration.pdf").write_bytes(b"A-DECLARATION")

    res = _scan(conn, tmp_path)

    assert res["excluded"] == 0
    assert res["emitted"] == 1


def test_exclude_matches_case_insensitively(conn, tmp_path, monkeypatch):
    # The corpus writes `.PDF` and `.pdf` both (see `_pdfs`); filename casing
    # is no more reliable than extension casing.
    _excluding(monkeypatch, r"^racun")
    (tmp_path / "RACUN PDO26+05193.pdf").write_bytes(b"AN-INVOICE")
    (tmp_path / "declaration.pdf").write_bytes(b"A-DECLARATION")

    res = _scan(conn, tmp_path)

    assert res["excluded"] == 1
    assert res["emitted"] == 1


def test_a_file_matching_two_patterns_is_counted_once(conn, tmp_path, monkeypatch):
    # Overlapping patterns are normal in a hand-authored list; `excluded` must
    # stay a file count, or it cannot be reconciled against `scanned`.
    _excluding(monkeypatch, r"^racun", r"PDO")
    (tmp_path / "racun PDO26+05193.pdf").write_bytes(b"AN-INVOICE")
    (tmp_path / "declaration.pdf").write_bytes(b"A-DECLARATION")

    res = _scan(conn, tmp_path)

    assert res["excluded"] == 1
    assert sum(res["excluded_by"].values()) == 1


def test_a_rule_that_excludes_the_whole_corpus_fails_the_scan(
        conn, tmp_path, monkeypatch):
    """Otherwise it reports `scanned: 0` -- indistinguishable from a corpus
    already fully ingested, which is the same silent failure the missing-folder
    guard exists to prevent."""
    _excluding(monkeypatch, r"\.pdf$")
    (tmp_path / "declaration.pdf").write_bytes(b"A-DECLARATION")

    with pytest.raises(bh.BackfillError, match="excluded by the playbook rule"):
        _scan(conn, tmp_path)


def test_a_manufacturer_without_an_exclude_rule_keeps_every_file(conn, tmp_path):
    """No-op default. Every corpus imported before Neodent (GC, IVOCLAR,
    KOMET, DENTSPLY, VOCO, STRAUMANN) holds compliance documents only, and
    none of them may start losing files because this key was added."""
    (tmp_path / "PDO26+05073.pdf").write_bytes(b"1")
    (tmp_path / "racun.pdf").write_bytes(b"2")

    res = _scan(conn, tmp_path)

    assert res["excluded"] == 0
    assert res["excluded_by"] == {}
    assert res["emitted"] == 2


# --------------------------------------------------------------------------- #
# skip_backfill: a corpus dump that is not fit to ingest at all
# --------------------------------------------------------------------------- #
def test_a_playbook_that_disowns_its_dump_fails_the_scan(conn, tmp_path, monkeypatch):
    """Neodent's client ruling, 2026-08-19: the folder is a mess, skip it.

    Fails rather than returning an empty result, for the same reason a missing
    folder does -- `scanned: 0` reads as "already ingested", which is how a
    scan nobody meant to run stays invisible."""
    monkeypatch.setattr(bh.playbooks, "for_manufacturer", lambda brand: type(
        "P", (), {"skip_backfill": {"reason": "the folder is a mess"}})())
    (tmp_path / "declaration.pdf").write_bytes(b"A-DECLARATION")
    store = _MemStore()

    with pytest.raises(bh.BackfillError, match="the folder is a mess"):
        _scan(conn, tmp_path, store)

    # Refused on identity, before anything in the folder was read.
    assert store.puts == []
    assert conn.execute(
        "SELECT count(*) c FROM fetch_log WHERE source='backfill'").fetchone()["c"] == 0


def test_the_refusal_precedes_the_folder_existing(conn, tmp_path, monkeypatch):
    """A disowned dump is refused on identity, not on anything found inside it,
    so a wrong path under a skipped brand reports the skip -- the actionable
    fact -- rather than "drive_folder does not exist"."""
    monkeypatch.setattr(bh.playbooks, "for_manufacturer", lambda brand: type(
        "P", (), {"skip_backfill": {"reason": "the folder is a mess"}})())

    with pytest.raises(bh.BackfillError, match="the folder is a mess"):
        _scan(conn, tmp_path / "nope")


def test_a_playbook_without_skip_backfill_scans_normally(conn, tmp_path):
    (tmp_path / "declaration.pdf").write_bytes(b"A-DECLARATION")

    res = _scan(conn, tmp_path)

    assert res["emitted"] == 1
