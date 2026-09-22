"""P7b: the registry pages speak one vocabulary (office UI redesign spec § 9).

Thirteen screens carry the registry: Items, the item card and detail,
Documents and document detail, Manufacturers and manufacturer detail, the
three EUDAMED pages, Expiry, Decisions and Search. P7a built the word list
(`web/words.py`); this slice is the word list actually reaching the page.

Five properties, one per section below, asserted against the RENDERED page
rather than the template source -- a filter that is written but not applied
renders the stored value, and only the rendered text can tell the difference:

1. A status pill says the § 9 word. `production` never reaches a person's eye
   as "production"; it reaches them as "Published".
2. Every page opens with ONE sentence saying what it is for.
3. No timestamp carries microseconds. `2026-09-14 10:22:31.884213` is a
   database value printed at a person, not a time anybody reads.
4. The catalogue tag, model ids and the internal source keys (match bases,
   alias sources, audit events) are available but not in the way: they live
   under "Technical details", never in the body of a table an office reader
   scans.
5. A week is labelled the way `words.week_label` labels one. No registry page
   names a week today, so this is the guard that none starts printing a raw
   ISO week key instead.

Plus the contrast carry from P7a: links and the Review "Open" control were
still painted in the brand mint, which is 2.64:1 on white and unreadable.
Section 6 pins the ratios by computing them, not by trusting a comment.
"""

from __future__ import annotations

import html as html_mod
import pathlib
import re
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Json

from app.config import Web
from tests.conftest import TEST_API_URL as API_TEST_URL
from web import words
from web.app import create_app

STYLE = (pathlib.Path(__file__).resolve().parents[1]
         / "web" / "static" / "css" / "style.css")

#: The five stored document statuses, exactly as the database spells them.
#: A pill carrying any of these is the bug this slice closes.
STORED_STATUSES = {"production", "staged", "filed", "superseded", "rejected"}

#: What § 9 says each one reads as.
STATUS_WORDS = {
    "production": "Published",
    "staged": "Waiting for review",
    "filed": "On file",
    "superseded": "Replaced",
    "rejected": "Rejected",
}

#: Values that are machine-side facts: true, needed for an audit, and noise on
#: a page an office reader scans. Each must be reachable only under a
#: "Technical details" disclosure.
TECHNICAL_VALUES = (
    "LJ",              # the catalogue tag, single-valued since 2026-08-26
    "claude-",         # any model id
    "ref-list",        # a match basis
    "name-family",     # a match basis
    "vendor-master",   # an alias source
    "production-write",  # an audit event
)


@pytest.fixture
def client(test_db_url, tmp_path):
    cfg = Web(api_database_url=API_TEST_URL, imports_dir=str(tmp_path))
    return TestClient(create_app(cfg))


# --------------------------------------------------------------------------- #
# reading a rendered page
# --------------------------------------------------------------------------- #
def _visible(page: str) -> str:
    """The page as a reader sees it: tags dropped, entities decoded,
    whitespace folded.

    Tags are dropped rather than kept because every assertion here is about
    what a person READS. A CSS class (`badge-production`), an href
    (`?status=filed`) and a form value (`value="DoC"`) are all machine-side by
    construction and must not fail a test about words on a screen.
    """
    t = re.sub(r"<[^>]+>", " ", page)
    return re.sub(r"\s+", " ", html_mod.unescape(t))


_TECHNICAL = re.compile(
    r'<details\b[^>]*class="[^"]*\btechnical\b[^"]*"[^>]*>(.*?)</details>', re.S)


def _outside_technical(page: str) -> str:
    """The page with every "Technical details" disclosure cut out.

    Non-greedy, which is correct only while no technical block contains a
    nested `<details>`; `test_no_technical_block_nests_a_disclosure` is the
    guard that keeps that true, so this helper cannot quietly start leaving
    half a block in.
    """
    return _visible(_TECHNICAL.sub(" ", page))


def _pills(page: str) -> list[str]:
    """The text of every status pill on the page."""
    return [_visible(inner).strip() for inner in
            re.findall(r'<span class="badge[^"]*">(.*?)</span>', page, re.S)]


def _intro(page: str) -> str | None:
    """The one sentence a page opens with, or None if it has no intro.

    Two shapes, both carrying `page-intro`: the office pages fold their prose
    behind the sentence (`_ui.html`'s `intro` macro, a `<details>`), and the
    customer-facing item card, which has no menu and no prose to fold, carries
    the sentence on its own in a `<p>`.
    """
    m = re.search(r'class="page-intro"[^>]*>\s*<summary[^>]*>(.*?)</summary>',
                  page, re.S)
    if m is None:
        m = re.search(r'<p class="page-intro"[^>]*>(.*?)</p>', page, re.S)
    return _visible(m.group(1)).strip() if m else None


# --------------------------------------------------------------------------- #
# one registry, seeded once, read by every test below
# --------------------------------------------------------------------------- #
CANONICAL = "VOCABCO GMBH"


@pytest.fixture
def registry(conn):
    """One manufacturer carrying a document in every status, one item linked
    to two of them, evidence naming a model, an audit row, and an EUDAMED
    identity waiting on a person.

    Committed, not rolled back: the TestClient holds its own connection and a
    seed inside this transaction would be invisible to the app.
    """
    tag = uuid.uuid4().hex[:8]
    item_ref = f"VOCAB-{tag}"
    conn.execute(
        "INSERT INTO manufacturer_alias (raw_name, canonical_name, source) "
        "VALUES (%s,%s,'vendor-master')", (f"VC{tag}", CANONICAL))
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, mfr_ref, "
        "md_flag, catalogue, updated_at) "
        "VALUES (%s,'Vocab widget',%s,%s,TRUE,'LJ',now())",
        (item_ref, f"VC{tag}", f"MREF-{tag}"))
    group_id = conn.execute(
        "INSERT INTO item_group (canonical_manufacturer) VALUES (%s) "
        "RETURNING group_id", (CANONICAL,)).fetchone()["group_id"]
    conn.execute(
        "INSERT INTO item_group_member (group_id, item_ref, match_basis) "
        "VALUES (%s,%s,'ref-list')", (group_id, item_ref))

    docs = {}
    for i, status in enumerate(sorted(STORED_STATUSES)):
        docs[status] = conn.execute(
            "INSERT INTO document (type, regulation, validity_from, validity_to, "
            "status, content_hash, archive_url, coverage_scope, cert_number) "
            "VALUES ('DoC','MDR',%s,'2031-01-01',%s,%s,"
            "'/archive/vocab.pdf','group',%s) RETURNING doc_id",
            (date(2024, 1, i + 1), status, f"h-vocab-{tag}-{status}",
             f"CERT-{tag}-{i}"),
        ).fetchone()["doc_id"]
    # Two links, two bases: `ref-list` is production-capable, `name-family` is
    # structurally capped at staged (invariant 3). Both are match bases, and
    # both belong under Technical details.
    conn.execute(
        "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
        "VALUES (%s,%s,'ref-list','production')", (item_ref, docs["production"]))
    conn.execute(
        "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
        "VALUES (%s,%s,'name-family','staged')", (item_ref, docs["staged"]))
    # Evidence, including the model that produced it: the manufacturer name is
    # what attributes every one of these documents to the entity page.
    for doc_id in docs.values():
        conn.execute(
            "INSERT INTO evidence (doc_id, field, value, verbatim, page, tier, "
            "model_id, confidence, extracted_at, extract_rev, archive_url) "
            "VALUES (%s,'manufacturer',%s,%s,1,'T2','claude-haiku-4-5',0.94,"
            "now(),1,'/archive/vocab.pdf')", (doc_id, CANONICAL, CANONICAL))
    conn.execute(
        "INSERT INTO audit_log (event, doc_id, item_ref, decided_by, "
        "job_snapshot, detail) VALUES ('production-write',%s,%s,'gate',%s,%s)",
        (docs["production"], item_ref, Json({"type": "gate.candidate"}),
         Json({"note": "looks right"})))

    # A renewal chase on the production document. Without this the document
    # page renders no "Renewal chase" card at all, and every assertion about
    # what that card prints -- the period key above all -- passes vacuously.
    # That is exactly how a raw ISO week key survived fix round 0.
    rr = conn.execute(
        "INSERT INTO renewal_request (doc_id, state, manufacturer, period_key) "
        "VALUES (%s,'open',%s,'2026-W37') RETURNING id",
        (docs["production"], CANONICAL)).fetchone()["id"]
    conn.execute(
        "INSERT INTO renewal_request_document (renewal_request_id, doc_id) "
        "VALUES (%s,%s)", (rr, docs["production"]))
    conn.execute(
        "INSERT INTO email_draft (renewal_request_id, kind, to_addrs, subject, "
        "body, status, manufacturer) VALUES (%s,'request','{a@example.com}',"
        "'Renewal','Please send the current declaration.','draft',%s)",
        (rr, CANONICAL))

    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES (%s)",
                 (CANONICAL,))
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, actor_name, "
        "discovered_via, status, match_score) "
        "VALUES (%s,%s,%s,'register-fuzzy','pending',0.82)",
        (CANONICAL, f"SRN-{tag}", "Vocabco AG"))
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, actor_name, "
        "discovered_via, status) VALUES (%s,%s,%s,'register-exact','confirmed')",
        (CANONICAL, f"SRN-{tag}-OK", "Vocabco GmbH"))
    conn.execute(
        "INSERT INTO eudamed_sweep_state (canonical_name, due_at, last_swept_at) "
        "VALUES (%s, now() - interval '1 day', now() - interval '100 days')",
        (CANONICAL,))
    conn.commit()
    return {"item_ref": item_ref, "docs": docs, "group_id": group_id}


def _pages(client, registry) -> dict[str, str]:
    """Every registry screen, rendered. The key is the name used in failures."""
    item = registry["item_ref"]
    doc = registry["docs"]["production"]
    urls = {
        "items": f"/items?q={item}",
        "item detail": f"/items/{item}",
        "item card": f"/item/{item}",
        "documents": "/documents?q=VOCABCO",
        "document detail": f"/documents/{doc}",
        "manufacturers": "/manufacturers?q=VOCABCO",
        "manufacturer detail": f"/manufacturers/{CANONICAL.replace(' ', '%20')}",
        "eudamed": "/manufacturers/eudamed",
        "srn queue": "/manufacturers/srn-queue",
        "checks due": "/manufacturers/sweep-due",
        "expiry": "/expiry",
        "decisions": "/audit",
        "search": f"/search?q={item}",
    }
    out = {}
    for name, url in urls.items():
        resp = client.get(url)
        assert resp.status_code == 200, f"{name} ({url}) answered {resp.status_code}"
        out[name] = resp.text
    return out


# --------------------------------------------------------------------------- #
# 1. status pills say the § 9 word
# --------------------------------------------------------------------------- #
def test_no_status_pill_shows_a_stored_value(client, registry):
    """The one property that makes the word list real: whatever a pill says,
    it is never the database's own spelling."""
    for name, page in _pages(client, registry).items():
        # Case-sensitively: § 9 spells `rejected` as "Rejected", so the word
        # and the stored value differ by exactly one capital letter and a
        # case-folding comparison would flag the correct answer.
        stored = [p for p in _pills(page) if p in STORED_STATUSES]
        assert not stored, f"{name} still shows raw status pills: {stored}"


@pytest.mark.parametrize("status,word", sorted(STATUS_WORDS.items()))
def test_the_documents_list_names_every_status_in_words(client, registry,
                                                        status, word):
    """One document per status is seeded, so every row of § 9's status block
    has to appear on this page in its word."""
    page = client.get("/documents?q=VOCABCO").text
    assert word in _pills(page), f"{status} should read {word!r}: {_pills(page)}"


def test_the_status_filter_offers_the_words_and_files_by_the_stored_value(
        client, registry):
    """The dropdown is where the two spellings meet: a person picks
    "Waiting for review" and the query string still carries `staged`, because
    the filter runs against the column."""
    page = client.get("/documents").text
    options = re.findall(r'<option value="([^"]*)"[^>]*>(.*?)</option>', page)
    by_value = {v: _visible(label).strip() for v, label in options}
    for status, word in STATUS_WORDS.items():
        assert by_value.get(status) == word, by_value


def test_an_item_shows_the_word_for_both_the_document_and_the_link(
        client, registry):
    """`/items/{ref}` is the one page carrying two statuses per row -- the
    document's and this item's link to it -- and both are pills."""
    page = client.get(f"/items/{registry['item_ref']}").text
    pills = _pills(page)
    assert "Published" in pills
    assert "Waiting for review" in pills


# --------------------------------------------------------------------------- #
# 2. one sentence at the top
# --------------------------------------------------------------------------- #
#: Abbreviations this vocabulary uses that end in a full stop and are NOT the
#: end of a sentence. "Item no." is the § 9 label for a REF, standardised by
#: this very slice, so a naive `". " in intro` would read one sentence as two.
ABBREVIATIONS = ("no", "art", "e.g", "i.e", "etc", "cf", "approx")


def _sentences(text: str) -> list[str]:
    """Split on a full stop that really ends a sentence."""
    out, start = [], 0
    for m in re.finditer(r"\.\s+", text):
        word = re.search(r"([\w.]+)$", text[start:m.start()])
        if word and word.group(1).lower() in ABBREVIATIONS:
            continue
        out.append(text[start:m.end()].strip())
        start = m.end()
    if text[start:].strip():
        out.append(text[start:].strip())
    return out


def test_the_sentence_splitter_does_not_break_on_this_slices_own_label():
    """The guard's own guard: "Item no." must not read as a sentence end."""
    assert len(_sentences("Every item no. we hold, with its papers.")) == 1
    assert len(_sentences("One thing. Then another.")) == 2


def test_every_registry_page_opens_with_exactly_one_sentence(client, registry):
    for name, page in _pages(client, registry).items():
        intro = _intro(page)
        assert intro, f"{name} has no page intro"
        assert intro.endswith("."), f"{name}: intro does not end in a full stop: {intro!r}"
        sentences = _sentences(intro)
        assert len(sentences) == 1, (
            f"{name}: intro is {len(sentences)} sentences: {intro!r}")


# --------------------------------------------------------------------------- #
# 3. no timestamp printed at a person
# --------------------------------------------------------------------------- #
MICROSECONDS = re.compile(r"\d{2}:\d{2}:\d{2}\.\d+")


def test_no_registry_page_prints_a_microsecond_timestamp(client, registry):
    for name, page in _pages(client, registry).items():
        hit = MICROSECONDS.search(_visible(page))
        assert not hit, f"{name} prints {hit.group(0)!r}"


# --------------------------------------------------------------------------- #
# 4. the machine-side facts live under Technical details
# --------------------------------------------------------------------------- #
def test_no_technical_block_nests_a_disclosure(client, registry):
    """Guard for `_outside_technical`, which matches non-greedily."""
    for name, page in _pages(client, registry).items():
        for inner in _TECHNICAL.findall(page):
            assert "<details" not in inner, f"{name} nests a disclosure"


@pytest.mark.parametrize("needle", TECHNICAL_VALUES)
def test_machine_side_values_stay_under_technical_details(client, registry, needle):
    for name, page in _pages(client, registry).items():
        body = _outside_technical(page)
        assert needle not in body, (
            f"{name} shows {needle!r} in the body of the page; it belongs "
            f"under Technical details")


def test_the_values_are_still_there_to_be_read(client, registry):
    """Moved, not deleted. Every one of the six is still on the page that owns
    it -- an auditor asking "on what evidence?" must be able to answer.

    All six, not the four that were easy: a needle asserted absent from the
    body and never asserted present anywhere is a needle that could be DELETED
    with both tests still green, which tests nothing at all."""
    found = set()
    pages = {
        "items/{ref}": client.get(f"/items/{registry['item_ref']}").text,
        "documents/{id}": client.get(
            f"/documents/{registry['docs']['production']}").text,
        "manufacturers": client.get("/manufacturers?q=VOCABCO").text,
        "manufacturers/{name}": client.get(
            f"/manufacturers/{CANONICAL.replace(' ', '%20')}").text,
        "audit": client.get("/audit").text,
    }
    for page in pages.values():
        found |= {n for n in TECHNICAL_VALUES if n in _visible(page)}
    assert found == set(TECHNICAL_VALUES), (
        f"never rendered anywhere: {sorted(set(TECHNICAL_VALUES) - found)}")


# --------------------------------------------------------------------------- #
# 5. a week reads as a week
# --------------------------------------------------------------------------- #
ISO_WEEK_KEY = re.compile(r"\b\d{4}-W\d{2}\b")


def test_no_registry_page_names_a_week_by_its_key(client, registry):
    """No registry page labels a week today. The one that starts to must do it
    through `words.week_label` -- "Week 37 (7-13 Sep)" -- and never by printing
    the period key the report files itself under."""
    for name, page in _pages(client, registry).items():
        hit = ISO_WEEK_KEY.search(_outside_technical(page))
        assert not hit, (
            f"{name} names a week as {hit.group(0)!r}; use the week_label filter")


def test_week_label_is_what_a_week_label_looks_like():
    assert words.week_label(date(2026, 9, 10)) == "Week 37 (7–13 Sep)"


def test_a_renewal_chase_keeps_its_period_key_out_of_the_office_line(
        client, registry):
    """`renewal_request.period_key` is "2026-W37" whenever the renewal cadence
    is weekly, which is the default. It is a real key an engineer matches a
    draft by, and it is not a date anybody reads, so it belongs under
    Technical details and nowhere else."""
    page = client.get(f"/documents/{registry['docs']['production']}").text
    assert "Renewal chase" in page, "the fixture should render a chase card"
    assert "2026-W37" not in _outside_technical(page)
    assert "2026-W37" in _visible(page), "the key is moved, not deleted"


#: Stored words that § 9 replaced. Each is a whole word, case-insensitively:
#: "sweeps" and "swept" are the same stored idea as "sweep" and left the screen
#: with it. `production` is here because it is the one whose replacement,
#: "Published", the glossary now promises the reader by name.
#:
#: "article" and "product" joined them on 2026-09-15. They are § 9's own first
#: row -- "Item · article · product" -> "Item" -- and the sweep reached
#: whichever page a slice owned, never the shared macro: `_ui.html`'s
#: completeness card said "articles" on both the item and the manufacturer
#: page, and the Review panel said "product" two lines under a comment
#: forbidding it. Unlike the other five they are ORDINARY ENGLISH in two other
#: senses, so `_retired_pattern` below does not ban them outright.
RETIRED_WORDS = ("staged", "sweep", "sweeps", "swept", "production",
                 "article", "product")

#: The two senses of "article"/"product" that are correct English here and must
#: keep passing:
#:
#: * the LEGAL article -- "Article 19(1)", "MDR Article 14(2)". Always followed
#:   by its number. ("Art." is a different token and never matches at all.)
#: * the MANUFACTURER'S article number -- § 9's own label for `mfr_ref`
#:   ("Manufacturer's article no.", "by exact article number", "one article
#:   numbering"). Always followed by "no." or "number(ing)".
#:
#: So the catalogue sense -- the one § 9 replaced -- is the word followed by
#: NEITHER a digit NOR one of those two nouns. "product name" and "product
#: group" are deliberately NOT exempted: both name a thing in the catalogue,
#: which is the sense § 9 ruled on.
_SENSE_SENSITIVE = {"article", "product"}
_NOT_THE_CATALOGUE_SENSE = r"(?!\s+(?:no\b|numbers?\b|numbering\b|\d))"


def _retired_pattern(retired: str) -> re.Pattern:
    """`retired` as a whole word, case-insensitively, minus the senses that are
    not the one § 9 retired."""
    if retired in _SENSE_SENSITIVE:
        return re.compile(rf"\b{retired}s?\b{_NOT_THE_CATALOGUE_SENSE}", re.I)
    return re.compile(rf"\b{retired}\b", re.I)


def test_the_guard_can_tell_the_two_senses_of_article_apart():
    """The guard's own guard. A pattern that banned the legal article, or § 9's
    own label for `mfr_ref`, would be a pattern somebody exempts a whole file
    from within a week."""
    catches = _retired_pattern("article")
    assert catches.search("No article has this gap.")
    assert catches.search("Device articles in total")
    assert catches.search("not per article.")
    assert not catches.search("review — MDR Article 14(2) asks that")
    assert not catches.search("Article 19(1) asks that a declaration be kept")
    assert not catches.search("the manufacturer's article number")
    assert not catches.search("article no. 4407")
    assert not catches.search("one Business Central and one article numbering")
    products = _retired_pattern("product")
    assert products.search("every medical-device product of the manufacturer")
    assert products.search("Paste an item number, a product name")


@pytest.mark.parametrize("retired", RETIRED_WORDS)
def test_no_registry_page_still_uses_a_retired_word(client, registry, retired):
    """The word list is not applied until the OLD word is gone. A page that
    renders "Waiting for review" in its pills and "Staged" in a column heading
    three lines down has not adopted a vocabulary, it has added one."""
    pattern = _retired_pattern(retired)
    for name, page in _pages(client, registry).items():
        hit = pattern.search(_outside_technical(page))
        assert not hit, f"{name} still says {hit.group(0)!r}"


# --------------------------------------------------------------------------- #
# 6. contrast (the P7a carry)
# --------------------------------------------------------------------------- #
def _relative_luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    channels = []
    for i in (0, 2, 4):
        c = int(h[i:i + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    """WCAG 2.1 contrast ratio. Computed, not quoted: a number in a comment is
    a claim, and this file is where it becomes a fact."""
    la, lb = _relative_luminance(a), _relative_luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _var(css: str, name: str) -> str:
    m = re.search(rf"--{name}:\s*(#[0-9a-fA-F]{{6}})", css)
    assert m, f"--{name} is not defined"
    return m.group(1)


def _rule(css: str, selector: str) -> str:
    m = re.search(re.escape(selector) + r"\s*\{[^}]*\}", css)
    assert m, f"no rule for {selector}"
    return m.group(0)


def test_the_mint_fails_aa_which_is_why_nothing_is_written_in_it():
    css = STYLE.read_text()
    assert contrast(_var(css, "dentalia-primary-strong"), "#ffffff") < 4.5


def test_links_are_painted_in_the_accessible_green():
    """`a { color: var(--dentalia-primary-strong) }` was 2.64:1 on the page
    background -- every link on every screen, below AA."""
    css = STYLE.read_text()
    assert "var(--dentalia-action)" in _rule(css, "\na"), _rule(css, "\na")
    assert contrast(_var(css, "dentalia-action"), "#ffffff") >= 4.5


def test_the_review_open_control_is_readable():
    """"Open" is the only thing a collapsed Review row offers, and it was
    outlined and lettered in the same mint."""
    css = STYLE.read_text()
    rule = _rule(css, ".stage-row .c-open")
    assert "color: var(--dentalia-action)" in rule, rule
    assert "--dentalia-primary-strong" not in rule, rule


MINTS = ("dentalia-primary", "dentalia-primary-strong")


def _declared_vars(css: str) -> dict[str, str]:
    """`--name: #rrggbb` from anywhere in the sheet."""
    return {m.group(1): m.group(2)
            for m in re.finditer(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})\b", css)}


def _resolve(value: str, variables: dict[str, str]) -> str | None:
    """A CSS colour as a hex, or None when it is not one this test can read
    (a gradient, `transparent`, `inherit`, an undeclared variable)."""
    value = value.strip().rstrip(";").strip()
    m = re.fullmatch(r"var\(--([\w-]+)\)", value)
    if m:
        return variables.get(m.group(1))
    if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        return value
    if re.fullmatch(r"#[0-9a-fA-F]{3}", value):
        return "#" + "".join(c * 2 for c in value[1:])
    return {"white": "#ffffff", "black": "#000000"}.get(value.lower())


def _colour_pairs(css: str):
    """(selector, foreground, background) for every rule that names BOTH.

    The background falls back to the page ground, which `body` paints white:
    a rule that letters text and leaves the ground alone is read on white.
    """
    variables = _declared_vars(css)
    for m in re.finditer(r"([^{}]+)\{([^}]*)\}", css):
        selector, body = m.group(1).strip(), m.group(2)
        if selector.startswith("@") or ":root" in selector:
            continue
        fg = re.search(r"(?<![-\w])color:([^;}]+)", body)
        bg = re.search(r"(?<![-\w])background(?:-color)?:([^;}]+)", body)
        fg_hex = _resolve(fg.group(1), variables) if fg else None
        bg_hex = _resolve(bg.group(1), variables) if bg else None
        if fg_hex is None:
            continue
        yield selector, fg_hex, (bg_hex or "#ffffff")


def test_nothing_is_read_against_the_mint():
    """Both halves of the rule the palette comment claims, not one.

    Round 0 checked only `color: var(--dentalia-primary-strong)`. That cannot
    see white text on a mint BACKGROUND, which is how
    `.sidebar nav a.active` -- the active item of the menu, on every office
    screen -- sat at 1.92:1 under a comment asserting nothing did.
    """
    css = STYLE.read_text()
    variables = _declared_vars(css)
    mint_hexes = {variables[name] for name in MINTS if name in variables}
    assert mint_hexes, "the mint variables should still be declared"

    bad = []
    for selector, fg, bg in _colour_pairs(css):
        if fg not in mint_hexes and bg not in mint_hexes:
            continue
        if contrast(fg, bg) < 4.5:
            bad.append(f"{selector}: {fg} on {bg} = {contrast(fg, bg):.2f}:1")
    assert not bad, "unreadable against the mint:\n  " + "\n  ".join(bad)


@pytest.mark.parametrize("selector", [".badge-production", ".badge-done",
                                      ".badge-verdict-ok", ".sev-ok",
                                      ".reason-chip.is-active"])
def test_a_white_on_green_pill_passes_aa(selector):
    """Every one of these paints white text on a solid green. At #56b088 that
    was 2.64:1; a pill nobody can read is not a status."""
    css = STYLE.read_text()
    rule = _rule(css, selector)
    assert "--dentalia-primary-strong" not in rule, rule
    assert contrast("#ffffff", _var(css, "dentalia-action")) >= 4.5


#: The thirteen registry templates, by the file the route renders. Kept beside
#: `_pages`, which renders the same screens through HTTP.
REGISTRY_TEMPLATES = (
    "items.html", "item_detail.html", "item_card.html", "documents.html",
    "document_detail.html", "manufacturers.html", "manufacturer_detail.html",
    "eudamed.html", "srn_queue.html", "sweep_due.html", "expiry.html",
    "audit.html", "search.html",
)

#: Text a template may carry that `_outside_technical` would strip from a
#: rendered page: a Jinja comment, and the `technical` disclosure itself.
_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)
_TECHNICAL_BLOCK = re.compile(
    r'<details class="technical".*?</details>', re.S | re.I)
_JINJA_CODE = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)
_TAG = re.compile(r"<[^>]*>", re.S)


def _reader_text(src: str) -> str:
    """What is left of a template once everything a reader cannot see is gone.

    Jinja expressions go first: they carry stored values as comparison keys
    (`doc.status == 'production'`) and lookup keys on their way through the
    word list (`{{ "production" | word }}`), neither of which a reader meets.
    Then the tags, because class names, element ids and `for=` targets live in
    attributes and are equally invisible. What remains is the copy."""
    src = _JINJA_COMMENT.sub("", src)
    src = _TECHNICAL_BLOCK.sub("", src)
    src = _JINJA_CODE.sub(" ", src)
    return _TAG.sub(" ", src)


#: The office templates outside the registry set that the two sense-sensitive
#: words are guarded over.
#:
#: Every one of them is copy an office login reads, and every one of them sat
#: outside the round-0 sweep because it belongs to no single page: `_ui.html`
#: is the shared macro file (the completeness card renders on BOTH the item and
#: the manufacturer page), `coverage.html` and `discovery.html` left the menu
#: and are reached from Items, and the three `_staging_*` partials are the
#: Review panel. Operator screens are NOT here -- `bc_push.html`,
#: `manual.html`, `status.html`, `pipeline.html`, `dead.html`, `playbooks*`,
#: `onboarding*`, `scheduler.html`, `job_detail.html` keep their own vocabulary
#: (spec § 11).
#:
#: ALL the retired words are guarded over this set as of 2026-09-15. They were
#: not, and the comment here used to say why: `/staging` headed two sections
#: "Staged links on production documents (C5)" and "Staged links on unpublished
#: documents", copy with real semantics in it, and widening the guard would
#: have failed on those sentences and nothing else. They now read "Links
#: waiting for review on published documents" and "Links waiting on documents
#: that were never published", so the exemption has nothing left to protect.
SHARED_TEMPLATES = (
    "_ui.html", "coverage.html", "discovery.html", "missing.html",
    "_pager.html", "_pulse.html", "_result.html", "today.html",
    "staging.html", "_staging_docs.html", "_staging_doc_detail.html",
    "_staging_decide.html", "drafts.html", "draft_detail.html",
    "emails.html", "upload.html", "import.html",
)


@pytest.mark.parametrize("retired", RETIRED_WORDS)
def test_no_registry_template_carries_a_retired_word(retired):
    """The rendered check above cannot see a branch no fixture reaches.

    `manufacturer_detail.html` kept "more production documents" through a whole
    slice because it sits behind `{% if docs_total > docs | length %}`, and the
    cap is 500 documents: no test seeds enough rows to render it, so both the
    round-0 grep and the rendered check called the page clean. Reading the
    source covers every branch, including ones written after this test.

    This is the same reason `tests/test_htmx_targets.py` reads template source
    rather than rendered pages."""
    templates = pathlib.Path(__file__).resolve().parents[1] / "web" / "templates"
    pattern = _retired_pattern(retired)

    # Every retired word over both sets. "article" and "product" needed the
    # shared templates from the start; the older five were held back only by
    # the /staging headings, which now use the office words.
    names = REGISTRY_TEMPLATES + SHARED_TEMPLATES

    bad = []
    for name in names:
        text = _reader_text((templates / name).read_text(encoding="utf-8"))
        for line_no, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                bad.append(f"{name}:{line_no} {line.strip()[:70]}")
    assert bad == [], (
        f"a registry template still writes {retired!r} where a reader sees it: "
        + "; ".join(bad))
