"""`docs/guide/` and `docs/dev/` must keep up with the code.

Same reasoning as `tests/test_vocabulary_doc.py`, applied to the two doc sets
added 2026-08-31: a reference page is worth exactly its accuracy, and a stale
one is worse than none because it is believed. So every assertion here reads the
vocabulary from its real source -- the Postgres enums, `web/app.py`'s explanation
maps -- and never restates it.

Adding a job tag, a document status or a VALIDATE flag fails this test until the
docs name it.

The client guide is checked in both languages: the English and the Slovene are a
pair, and a value documented in only one of them is a half-answer for whichever
reader gets the other file.

DB-backed checks skip rather than fail when Postgres is absent, so the file still
runs in a bare checkout.
"""

from __future__ import annotations

import pathlib
import re

import pytest

DOCS = pathlib.Path(__file__).resolve().parents[1] / "docs"
GUIDE = DOCS / "guide"
DEV = DOCS / "dev"

#: Words a screen shows that are not a translation of a stored value, so they
#: are not in `WORDS` and cannot be read off it. "Today" is the front page's
#: name; `web/words.py` says so where it defines the rest.
_SCREEN_ONLY = ("Today",)


def word_list() -> list[str]:
    """Every word a screen puts in front of a reader, read from `web/words.py`.

    Read from the SHIPPED vocabulary, not from § 9's table in the design doc.
    The design doc is a dated snapshot of an intention: a word changed in
    `web/words.py` without touching that archive would leave this test still
    asserting the OLD word is in the glossary and never noticing the new one is
    absent, which is precisely the drift the test below exists to catch. The
    chain has to be words.py -> glossary with nothing archival in between.

    § 9's own exclusions come out for free here rather than needing a parser:
    the three `manual_task` kinds it marks "(not shown)" and the two Upload
    fields it marks "(removed)" are not in `WORDS`, because nothing renders
    them.
    """
    from web import words

    found = sorted(set(words.WORDS.values()) | set(_SCREEN_ONLY))
    assert len(found) > 15, f"the word list did not load: {found}"
    return found


def _read(path: pathlib.Path) -> str:
    assert path.exists(), f"{path} is missing"
    return path.read_text()


@pytest.fixture(scope="module")
def glossary_en() -> str:
    return _read(GUIDE / "glossary.md")


@pytest.fixture(scope="module")
def glossary_sl() -> str:
    return _read(GUIDE / "glossary.sl.md")


@pytest.fixture(scope="module")
def handlers_doc() -> str:
    return _read(DEV / "handlers.md")


# --------------------------------------------------------------------------- #
# the developer index must list every queue tag
# --------------------------------------------------------------------------- #

def test_every_job_tag_is_in_the_handler_index(conn, handlers_doc):
    """`docs/dev/handlers.md` is the map from tag to code. A tag added to the
    enum without a row leaves a stage nobody can look up."""
    tags = [r["enumlabel"] for r in conn.execute(
        "SELECT e.enumlabel FROM pg_type t JOIN pg_enum e ON e.enumtypid=t.oid "
        "WHERE t.typname='job_type'").fetchall()]
    assert tags, "no job_type enum found -- migrations did not run"
    missing = sorted(t for t in tags if f"`{t}`" not in handlers_doc)
    assert not missing, f"job tags absent from docs/dev/handlers.md: {missing}"


def test_the_handler_index_invents_no_tags(conn, handlers_doc):
    """The reverse direction. A tag removed from the enum must lose its row, or
    the index sends a reader looking for a handler that is not there."""
    tags = {r["enumlabel"] for r in conn.execute(
        "SELECT e.enumlabel FROM pg_type t JOIN pg_enum e ON e.enumtypid=t.oid "
        "WHERE t.typname='job_type'").fetchall()}
    assert tags, "no job_type enum found -- migrations did not run"
    index = handlers_doc.split("## Index")[1].split("## Per tag")[0]
    claimed = set(re.findall(r"\| `([a-z]+\.[a-z]+)` \|", index))
    assert not claimed - tags, (
        f"docs/dev/handlers.md lists tags the enum does not have: "
        f"{sorted(claimed - tags)}")


# --------------------------------------------------------------------------- #
# the client glossary must explain every word the screens can print
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("lang", ["en", "sl"])
def test_every_document_status_is_in_the_glossary(conn, glossary_en, glossary_sl, lang):
    """A reader meets these as coloured pills. An undocumented one is a word on
    screen with nowhere to look it up."""
    doc = glossary_en if lang == "en" else glossary_sl
    values = [r["enumlabel"] for r in conn.execute(
        "SELECT e.enumlabel FROM pg_type t JOIN pg_enum e ON e.enumtypid=t.oid "
        "WHERE t.typname='doc_status'").fetchall()]
    assert values, "no doc_status enum found -- migrations did not run"
    missing = sorted(v for v in values if v not in doc)
    assert not missing, f"doc statuses missing from the {lang} glossary: {missing}"


@pytest.mark.parametrize("lang", ["en", "sl"])
def test_every_word_list_word_is_in_the_glossary(glossary_en, glossary_sl, lang):
    """§ 9 gave every screen one word. The glossary is the other half of it.

    `web/words.py` decides what a screen says; this decides that a reader who
    meets one of those words has somewhere to look it up. Without it the two
    halves drift the way they already did once: the pills read "Published" and
    "Waiting for review" while the glossary still explained "production" and
    "staged" and nothing failed.

    Checked in both languages. The interface is English -- a Slovenian one is
    out of scope (spec § 11) -- so the Slovene glossary carries the same
    English screen words and explains them in Slovene. A word missing there is
    a word a Slovene reader meets on screen and cannot look up in their own
    guide.
    """
    doc = glossary_en if lang == "en" else glossary_sl
    # Bolded, not a bare substring: "Manufacturer" is inside "Manufacturer's
    # article no.", "Item" inside "Item no.", "Failed" inside "Failed tasks",
    # "EUDAMED check" inside "EUDAMED checks" -- four of the words passed on
    # another word's entry and would have passed with no entry of their own.
    missing = sorted({w for w in word_list()
                      if f"**{w}**" not in doc and f"### {w}" not in doc})
    assert not missing, (
        f"§ 9 word-list words absent from the {lang} glossary: {missing}")


@pytest.mark.parametrize("lang", ["en", "sl"])
def test_every_validate_flag_is_explained_on_the_review_page(lang):
    """`web/app.py` turns each flag into a sentence for the reviewer; the guide
    turns that sentence into an action. A flag with neither leaves the reviewer
    reading a slug."""
    from web.app import FLAG_EXPLANATIONS

    name = "review.md" if lang == "en" else "review.sl.md"
    page = _read(GUIDE / "pages" / name)
    # The page quotes the explanation sentences, not the slugs -- that is the
    # point of it. Match on a distinctive opening fragment of each sentence.
    missing = []
    for flag, sentence in FLAG_EXPLANATIONS.items():
        head = sentence.split(",")[0][:40].rstrip(".")
        if head not in page:
            missing.append(flag)
    assert not missing, (
        f"VALIDATE flags with no row on the {lang} review page: {sorted(missing)}")


# --------------------------------------------------------------------------- #
# the two languages are a pair
# --------------------------------------------------------------------------- #

def test_every_english_guide_page_has_a_slovene_one():
    en = {p for p in GUIDE.rglob("*.md") if not p.name.endswith(".sl.md")}
    missing = sorted(p.name for p in en
                     if not p.with_name(p.name[:-3] + ".sl.md").exists())
    assert not missing, f"guide pages with no Slovene counterpart: {missing}"


def test_no_orphan_slovene_page():
    sl = list(GUIDE.rglob("*.sl.md"))
    assert sl, "no Slovene pages found"
    missing = sorted(p.name for p in sl
                     if not p.with_name(p.name.replace(".sl.md", ".md")).exists())
    assert not missing, f"Slovene pages with no English source: {missing}"


def test_handler_index_line_citations_land_on_a_def():
    """`docs/dev/handlers.md` cites `handlers/<file>:<line>` per tag, and a line
    number is the first thing a refactor invalidates.

    Thirteen of twenty-one were wrong when this was written -- pointing into the
    middle of a docstring, a dict literal, an `if` body. A citation that lands
    somewhere plausible is worse than none: it sends a reader to the wrong
    function and reads as authoritative. The assertion is deliberately weak (the
    line must START a `def`), because pinning the exact function name here would
    just be the same mirror one level down.
    """
    import re
    doc = (DEV / "handlers.md").read_text()
    cited = re.findall(r"`handlers/([\w.]+\.py):(\d+)`", doc)
    assert cited, "no handler line citations found -- did the format change?"

    wrong = []
    for name, lineno in cited:
        path = DEV.parents[1] / "app" / "handlers" / name
        if not path.exists():
            wrong.append(f"{name}:{lineno} (no such file)")
            continue
        lines = path.read_text().splitlines()
        n = int(lineno)
        if n > len(lines) or not lines[n - 1].startswith("def "):
            found = lines[n - 1].strip()[:50] if n <= len(lines) else "<past EOF>"
            wrong.append(f"{name}:{lineno} -> {found!r}")
    assert not wrong, f"handler citations not on a def line: {wrong}"


def test_every_guide_page_is_in_the_sidebar():
    """A page the builder renders but the sidebar never lists is unreachable.

    `build-guide.py`'s `sources()` globs `pages/*.md`, so a new page lands in
    the bundle whether or not anyone adds it to `NAV` -- which is why this went
    unnoticed: `pages/bc-push` was written, built into both bundles and listed
    in neither sidebar, and the only way to reach it was a cross-link from
    another page. The bundle is the whole product for a reader who has no repo.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "build_guide", DOCS.parent / "scripts" / "build-guide.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    listed = {stem for _, entries in mod.NAV for _, stem in entries}
    on_disk = {f"pages/{p.stem}" for p in (GUIDE / "pages").glob("*.md")
               if not p.name.endswith(".sl.md")}

    assert not on_disk - listed, (
        f"guide pages missing from the sidebar: {sorted(on_disk - listed)}")
    assert not {s for s in listed if s.startswith("pages/")} - on_disk, (
        f"sidebar lists pages that do not exist: "
        f"{sorted({s for s in listed if s.startswith('pages/')} - on_disk)}")


def test_every_guide_page_declares_its_status():
    """The Status line is what keeps a switched-off feature from being described
    as working. A page without one has quietly dropped that promise."""
    pages = sorted((GUIDE / "pages").glob("*.md"))
    assert pages, "no guide pages found"
    missing = [p.name for p in pages
               if not re.search(r"(?m)^(\*\*Stanje:\*\*|\*\*Status:\*\*|## Status|## Stanje)",
                                p.read_text())]
    assert not missing, f"guide pages with no Status line: {missing}"


# --------------------------------------------------------------------------- #
# internal links
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("root", ["guide", "dev"])
def test_no_broken_internal_links(root):
    base = DOCS / root
    broken = []
    for f in base.rglob("*.md"):
        for _, href, _ in re.findall(r"\[([^\]]+)\]\(([^)#]*\.md)(#[^)]*)?\)",
                                     f.read_text()):
            if not (f.parent / href).resolve().exists():
                broken.append(f"{f.name} -> {href}")
    assert not broken, f"broken links under docs/{root}/: {sorted(set(broken))}"


def test_slovene_pages_link_to_slovene_pages():
    """A Slovene page linking to an English one drops the reader out of their
    language mid-sentence."""
    wrong = []
    for f in GUIDE.rglob("*.sl.md"):
        for _, href, _ in re.findall(r"\[([^\]]+)\]\(([^)#]*\.md)(#[^)]*)?\)",
                                     f.read_text()):
            if not href.endswith(".sl.md"):
                wrong.append(f"{f.name} -> {href}")
    assert not wrong, f"Slovene pages linking to English: {sorted(set(wrong))}"


# --------------------------------------------------------------------------- #
# the committed bundles must agree with the markdown they were built from
# --------------------------------------------------------------------------- #
def _build_guide_module():
    """`scripts/build-guide.py` is a script, not a package, so it is loaded by
    path. Only `render` is called here, which writes nothing."""
    import importlib.util

    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "build-guide.py"
    spec = importlib.util.spec_from_file_location("build_guide", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("lang", ["en", "sl"])
def test_the_committed_bundle_matches_the_markdown(lang):
    """A UI change is not done until the guide matches it, and the bundle is
    the only artefact a reader without the repo gets.

    On 2026-09-15 a slice edited two guide pages after its last rebuild and
    shipped bundles that still carried the old page names. The whole suite was
    green: nothing here read `dentalia-guide-*.html` at all, so the sidebar
    said "Renewal emails" while the daily-round page said "Drafts out".

    The bundle concatenates each page's rendered body verbatim, so every body
    rendered from today's markdown must appear in it. This calls `render`
    only -- it builds nothing and writes nothing."""
    bundle = (GUIDE / f"dentalia-guide-{lang.upper()}.html").read_text(encoding="utf-8")
    render = _build_guide_module().render

    stale = []
    for page in sorted(GUIDE.rglob("*.md")):
        is_sl = page.name.endswith(".sl.md")
        if is_sl != (lang == "sl"):
            continue
        body, _ = render(page.read_text(encoding="utf-8"), [])
        if body not in bundle:
            stale.append(str(page.relative_to(GUIDE)))

    assert stale == [], (
        f"dentalia-guide-{lang.upper()}.html is older than these pages -- run "
        "`python3 scripts/build-guide.py` and commit the bundles: "
        + ", ".join(stale))
