"""PDF text must be safe to store before it leaves this module.

Two GC pilot extractions died on `DataError: PostgreSQL text fields cannot
contain NUL (0x00) bytes` (2026-08-12). PDFs legitimately carry NUL in their
text streams — a malformed encoding, a padded font table — and PyMuPDF hands it
straight through. Everything downstream (evidence verbatim, the job result
jsonb, extraction candidates) is Postgres text, which rejects it outright.

Sanitising at each write site would mean finding every one of them and never
missing a new one. `get_text` is the single place the bytes enter the process,
so it is the one place that has to be right.

NUL is *dropped* rather than replaced: it carries no textual meaning, and a
substitute character would land in `verbatim` evidence that a human reads back
against the source document.
"""

from __future__ import annotations

from app.extract import pdf as pdfutil


class _FakePage:
    def __init__(self, text):
        self._text = text

    def get_text(self, _kind):
        return self._text

    def get_pixmap(self, **kw):                     # unused here
        raise NotImplementedError


class _FakeDoc:
    """Stands in for pymupdf.Document: iterable, indexable, has page_count."""

    def __init__(self, texts):
        self._pages = [_FakePage(t) for t in texts]

    def __iter__(self):
        return iter(self._pages)

    def __getitem__(self, i):
        return self._pages[i]

    @property
    def page_count(self):
        return len(self._pages)


def test_page_texts_drops_nul_bytes():
    doc = _FakeDoc(["clean page", "dirty\x00page"])

    out = pdfutil.page_texts(doc)

    assert out == ["clean page", "dirtypage"]
    assert not any("\x00" in t for t in out)


def test_first_page_text_drops_nul_bytes():
    doc = _FakeDoc(["EC \x00DECLARATION\x00 OF CONFORMITY"])

    assert pdfutil.first_page_text(doc) == "EC DECLARATION OF CONFORMITY"


def test_first_page_text_limit_applies_after_sanitising():
    # The limit is a budget on real characters. Counting NULs against it would
    # silently shorten the classification window on exactly the malformed
    # documents that need the most text.
    doc = _FakeDoc(["\x00\x00\x00ABCDE"])

    assert pdfutil.first_page_text(doc, limit=3) == "ABC"


def test_full_text_drops_nul_bytes_and_keeps_page_markers():
    doc = _FakeDoc(["a\x00b", "c\x00d"])

    out = pdfutil.full_text(doc)

    assert "\x00" not in out
    assert "[[page 1]]" in out and "[[page 2]]" in out
    assert "ab" in out and "cd" in out


def test_full_text_without_markers_is_also_clean():
    doc = _FakeDoc(["a\x00b"])

    assert pdfutil.full_text(doc, page_markers=False) == "ab"


def test_is_scan_counts_only_real_characters():
    # A page whose only "text" is NUL padding is an image, not a text page. If
    # NUL counted toward the threshold this would be misread as extractable text
    # and never reach the T2 vision tier.
    doc = _FakeDoc(["\x00" * 500])

    assert pdfutil.is_scan(doc) is True
