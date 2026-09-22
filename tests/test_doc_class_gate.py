"""The doc-class gate: T0 declines, the LLM answers, and "not a compliance
document" is terminal.

Why this file exists. On 2026-09-02 the DISCOVER search rung ran in production
for the first time and produced four documents. All four were web pages:
bredent.com answers `HTTP 200 text/html` at a URL ending `.pdf`, PyMuPDF
paginates HTML, and T0 typed the site's navigation menu `IFU` at confidence
1.00. GATE refused all four to `manual` -- and a human then approved one into
`production`, because the metadata looked real. The machine's refusal was
correct and useless; the harm was manufacturing something plausible enough to
defeat review.

The evidence was already there and unread: the T1 and T2 field calls on that
document said "It is a corporate website landing page" (conf 0.95) and "page
content is a website product category navigation list" (conf 0.90). Nothing
asked the one question those answers were begging.

Real Postgres per CLAUDE.md; the LLM is stubbed -- no test here makes a call.
"""

from __future__ import annotations

import types

import pytest

from app.extract import tiers
from app.extract import llm as llm_mod


# --- stubs ------------------------------------------------------------------

class _Doc:
    """Enough of a PyMuPDF document for the ladder: page texts and no scan."""

    def __init__(self, pages):
        self._pages = pages

    def __len__(self):
        return len(self._pages)

    @property
    def page_count(self):
        return len(self._pages)

    @staticmethod
    def _page(text):
        # PyMuPDF is called as `page.get_text("text")`, and `is_scan` also asks
        # for images -- match both signatures rather than the happy path only.
        return types.SimpleNamespace(
            get_text=lambda *a, **k: text,
            get_images=lambda *a, **k: [],
            get_drawings=lambda *a, **k: [],
        )

    def __iter__(self):
        return iter([self._page(t) for t in self._pages])

    def __getitem__(self, i):
        return self._page(self._pages[i])


class _Llm:
    """Records what it was asked. `answer=None` means no `classify` at all,
    which is the pre-2026-09-02 protocol every older stub implements."""

    def __init__(self, answer="DoC", quote="Declaration of Conformity", extract_fields=None):
        self.answer, self.quote = answer, quote
        self.extract_calls, self.classify_calls = [], []
        self._fields = extract_fields or {}
        if answer is not None:
            self.classify = self._classify

    def _classify(self, doc, filename):
        self.classify_calls.append(filename)
        return self.answer, self.quote

    def extract(self, tier, doc, filename, missing, **kw):
        self.extract_calls.append(tier)
        return {f: v for f, v in self._fields.items() if f in missing}


#: The real Solventum shape: menus and boilerplate for pages, the phrase
#: "instructions for use" far in -- character 19.160 of 30.791 in the original,
#: inside the marketing sentence "Access essential healthcare information,
#: including instructions for use, regulatory details".
#:
#: Note this is now DEFENCE IN DEPTH: since 2026-09-02 FETCH refuses a non-PDF
#: body outright, so an HTML page no longer reaches extraction at all. The case
#: is kept because the same shape arrives as a real PDF -- a portal that renders
#: its own listing page to PDF, which the corpus already contains.
WEBSITE = ["Resources\nProducts for professionals and consumers\nCookie settings\n",
           "Shop by category\nOral care\nFiltration\nPersonal safety\n",
           "Access essential healthcare information, including instructions for use\n"]
DECLARATION = ["EU Declaration of Conformity\nRegulation (EU) 2017/745\nREF 12345\n"]
COVER_CERT = ["Certificate\nHolder of Certificate:\nIvoclar Vivadent AG\n",
              "EN ISO 13485:2016\n"]


def _run(doc, llm, filename="d.pdf"):
    return tiers.run_extraction(doc, filename, llm=llm, threshold=0.95)


# --- the refusal is terminal ------------------------------------------------

def test_a_refused_file_returns_the_terminal_class_and_no_fields():
    llm = _Llm(answer=llm_mod.NOT_A_DOCUMENT, quote="cookie settings")
    doc_class, fields, used = _run(_Doc(WEBSITE), llm)

    assert doc_class == tiers.NOT_A_DOCUMENT
    assert fields == {}
    # And it stops there: no field extraction is paid for on a web page.
    assert llm.extract_calls == []


def test_a_refused_file_emits_no_validate_job(conn):
    """The part that actually protects the registry. `doc_class` is not
    persisted and neither VALIDATE nor GATE reads it, so suppressing the emit is
    the only thing that keeps a refused file out of the review queue."""
    from app.handlers import extract as eh

    res = eh._finalize(conn, "HASH_REFUSED", None, tiers.NOT_A_DOCUMENT,
                       ["T0", "T1"], {}, archive_url="/archive/x/junk.pdf")

    assert res["doc_class"] == tiers.NOT_A_DOCUMENT
    assert res["emitted"] is None
    assert conn.execute(
        "SELECT count(*) AS n FROM job WHERE type='validate.doc'"
    ).fetchone()["n"] == 0


def test_an_accepted_file_still_emits_validate(conn):
    from app.handlers import extract as eh

    eh._finalize(conn, "HASH_OK", None, "compliance-doc", ["T0"],
                 {"type": {"value": "DoC"}}, archive_url="/archive/x/d.pdf")

    assert conn.execute(
        "SELECT count(*) AS n FROM job WHERE type='validate.doc'"
    ).fetchone()["n"] == 1


# --- T0 keeps its free path -------------------------------------------------

def test_a_document_t0_can_type_never_reaches_the_classifier():
    """88% of the real corpus, measured. Paying for those would be waste."""
    llm = _Llm(answer="DoC")
    doc_class, _, used = _run(_Doc(DECLARATION), llm)

    assert doc_class == "compliance-doc"
    assert llm.classify_calls == []


def test_a_cover_page_certificate_is_typed_by_t0_from_page_two():
    llm = _Llm(answer="ISO")
    doc_class, _, _ = _run(_Doc(COVER_CERT), llm)

    assert doc_class == "compliance-doc"
    assert llm.classify_calls == []


# --- the classifier's answer becomes evidence -------------------------------

def test_an_accepted_class_is_recorded_as_a_typed_field():
    llm = _Llm(answer="EC", quote="EC Certificate 0123")
    doc_class, fields, _ = _run(_Doc(["scanned image, no text markers here"]), llm)

    assert doc_class == "compliance-doc"
    assert fields["type"]["value"] == "EC"
    assert fields["type"]["tier"] == "T1"
    assert fields["type"]["verbatim"] == "EC Certificate 0123"


def test_the_classifier_never_overwrites_a_t0_type():
    """T0 read the header; the classifier was not consulted. Belt and braces --
    if the ordering ever changes, T0's read still wins."""
    llm = _Llm(answer="IFU", extract_fields={})
    _, fields, _ = _run(_Doc(DECLARATION), llm)

    assert fields["type"]["value"] == "DoC"
    assert fields["type"]["tier"] == "T0"


# --- backwards compatibility ------------------------------------------------

def test_an_llm_without_classify_behaves_exactly_as_before():
    """The injected llm protocol is `extract(...)` and every stub predates this
    call. An older one must not crash, and an unknown document must extract
    normally, which is what it did before 2026-09-02."""
    llm = _Llm(answer=None)
    assert not hasattr(llm, "classify")

    doc_class, _, used = _run(_Doc(["no markers anywhere in this text"]), llm)

    assert doc_class == "unknown"
    assert "T1" in used          # the normal ladder still ran


def test_business_and_msds_still_short_circuit_before_the_classifier():
    llm = _Llm(answer="DoC")
    doc_class, fields, _ = _run(_Doc(["Safety Data Sheet\nSection 1: Identification"]), llm)

    assert doc_class == "msds"
    assert llm.classify_calls == []
