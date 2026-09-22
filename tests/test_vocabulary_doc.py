"""`docs/vocabulary.md` must match the code and the database, in both directions.

A reference page of closed vocabularies is worth exactly as much as its
accuracy. Left to prose it drifts silently, and a stale one is worse than none
because it is believed: this repo has already shipped a docs table listing a
flag class the code did not have (`multi-manufacturer-ref`, 2026-08-13), and the
test meant to catch that had the vocabulary hand-copied into it, so it passed.

So the assertions here read the vocabularies from their real sources -- Postgres
enums, CHECK constraints, and the handler modules -- and never restate them.
Adding a flag, a job tag or an anomaly kind fails this test until it appears in
the doc.

The DB-backed checks skip rather than fail when Postgres is absent, so the file
still runs in a bare checkout; the code-backed ones always run.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from app.handlers import gate as gh
from app.results import ANOMALY_KINDS
from app.extract.tiers import REF_TRUNCATION_FLAG, TARGET

DOC = pathlib.Path(__file__).resolve().parents[1] / "docs" / "vocabulary.md"


@pytest.fixture(scope="module")
def doc() -> str:
    assert DOC.exists(), f"{DOC} is missing -- the vocabulary reference was deleted"
    return DOC.read_text(encoding="utf-8")


def _missing(doc: str, values) -> list[str]:
    """Values the doc never names, where "names" means inside backticks.

    A plain substring test is too weak to be worth running: renaming a doc entry
    from `cert-unresolved` to `cert-unresolvedX` left the old string present as a
    prefix and the check passed. Mutation-caught 2026-08-13. Requiring the exact
    backticked token is both stricter and how every value is written on the
    page, so it costs nothing in false alarms."""
    return sorted(v for v in values if f"`{v}`" not in doc)


# --------------------------------------------------------------------------- #
# code-backed — always run
# --------------------------------------------------------------------------- #
def test_every_flag_class_is_documented(doc):
    for name, flags in (("BLOCKING_FLAGS", gh.BLOCKING_FLAGS),
                        ("CAPPING_FLAGS", gh.CAPPING_FLAGS),
                        ("INFORMATIONAL_FLAGS", gh.INFORMATIONAL_FLAGS)):
        assert not _missing(doc, flags), (
            f"{name} values missing from docs/vocabulary.md: {_missing(doc, flags)}")


def test_every_emitted_flag_is_documented(doc):
    """Not just the classified ones: a flag VALIDATE emits but nobody classified
    must still be visible on the page, because that is precisely the state that
    needs finding."""
    root = pathlib.Path(gh.__file__).resolve().parent
    emitted = set()
    for mod in ("validate.py", "gate.py"):
        emitted |= set(re.findall(r'flags\.append\("([a-z][a-z-]+)"\)',
                                  (root / mod).read_text(encoding="utf-8")))
    emitted.add(gh.PAGE_MISSING_FLAG)
    # VALIDATE extends its flag list with tiers.integrity_flags(), so this one
    # is emitted through a constant the regex above cannot see either.
    emitted.add(REF_TRUNCATION_FLAG)
    assert len(emitted) >= 13, "the flags.append idiom changed; this guard is blind"
    assert not _missing(doc, emitted), (
        f"flags emitted but undocumented: {_missing(doc, emitted)}")


def test_the_doc_invents_no_flags(doc):
    """The other direction. Every `flag-looking` name in the flags section must
    be one the pipeline can actually emit -- a documented flag that does not
    exist sends a reader hunting for behaviour that was removed."""
    section = doc.split("## 5. VALIDATE flags")[1].split("## 6.")[0]
    named = set(re.findall(r"^\| `([a-z][a-z-]+)` \|", section, re.MULTILINE))
    real = set(gh.BLOCKING_FLAGS) | set(gh.CAPPING_FLAGS) | set(gh.INFORMATIONAL_FLAGS)
    assert named <= real, f"documented but not a real flag: {sorted(named - real)}"


def test_anomaly_kinds_and_target_fields_are_documented(doc):
    assert not _missing(doc, ANOMALY_KINDS), (
        f"anomaly kinds missing: {_missing(doc, ANOMALY_KINDS)}")
    assert not _missing(doc, TARGET), (
        f"extraction TARGET fields missing: {_missing(doc, TARGET)}")


def test_trusted_bases_are_documented(doc):
    assert not _missing(doc, gh.TRUSTED_BASES)
    # and the capped ones, which are the trust boundary worth spelling out
    assert not _missing(doc, ("ref-catalogue", "name-family", "fetch-context"))


# --------------------------------------------------------------------------- #
# database-backed — need Postgres
# --------------------------------------------------------------------------- #
def test_every_postgres_enum_value_is_documented(conn, doc):
    rows = conn.execute("""
        SELECT t.typname, e.enumlabel
        FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid
        WHERE t.typname IN ('job_type','job_status','job_priority',
                            'doc_status','link_status','manual_kind','manual_status')
    """).fetchall()
    assert rows, "no enums found -- migrations did not run"
    missing = sorted({f"{r['typname']}.{r['enumlabel']}" for r in rows
                      if r["enumlabel"] not in doc})
    assert not missing, f"enum values missing from docs/vocabulary.md: {missing}"


def test_the_job_tag_table_lists_every_tag(conn, doc):
    """The tag table is the page's map of the pipeline. A tag added to the enum
    without a row here leaves a stage nobody can look up."""
    tags = [r["enumlabel"] for r in conn.execute(
        "SELECT e.enumlabel FROM pg_type t JOIN pg_enum e ON e.enumtypid=t.oid "
        "WHERE t.typname='job_type'").fetchall()]
    section = doc.split("## 1. The pipeline")[1].split("## 2.")[0]
    missing = sorted(t for t in tags if f"`{t}`" not in section)
    assert not missing, f"job tags absent from the tag table: {missing}"
