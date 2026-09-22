"""Backfilling `stated_class` into the documents extracted before it existed.

The properties that keep this repair honest are not the same ones
`repair_ref_list` needs, because this repair deliberately does LESS:

  * append, never overwrite -- a new `extract_rev`, migration 004's contract;
  * the new rev keeps every key the old one had, at identical values, EXCEPT
    `stated_class` itself, which is the one key the repair owns: added when
    missing, corrected when the current parser disagrees, dropped when it
    abstains (that drop is how a wrong read is retracted append-only);
  * **no `validate.doc` and no job re-pointing**. `repair_ref_list` re-emits
    because it changes what a document COVERS, which has to be re-adjudicated.
    This one adds a display value, and re-running 768 settled dispositions to
    record one would risk far more than the value is worth. The 033 view reads
    the attempt jsonb instead, so history is compared without being re-decided;
  * nothing is silent: every scanned hash lands in exactly one count.
"""

from __future__ import annotations

import pytest

from app import repair_stated_class as rsc

HASH = "a" * 64
STATES_IIA = "EU Declaration of Conformity\nRegulation (EU) 2017/745\nClass: IIa Rule: 7\n"
STATES_NOTHING = "EU Declaration of Conformity for dental restoratives\n"
MULTI_CLASS = "EU Declaration of Conformity Class Is/Ir/IIa/llb/lll\n"


@pytest.fixture(autouse=True)
def _clean(test_db_url):
    from app import db

    def wipe():
        with db.connect(test_db_url, autocommit=True) as c:
            c.execute("TRUNCATE extraction_attempt RESTART IDENTITY CASCADE")
            c.execute("TRUNCATE document_text CASCADE")
            c.execute("TRUNCATE item_document, evidence, document RESTART IDENTITY CASCADE")

    wipe()
    yield
    wipe()


def _old_fields(**overrides):
    """A realistically-shaped pre-032 attempt: the fields a document already
    carried before `stated_class` was a target at all."""
    base = {
        "type": {"value": "DoC", "conf": 1.0, "tier": "T0", "verbatim": "DoC", "page": 1},
        "regulation": {"value": "MDR", "conf": 1.0, "tier": "T0",
                       "verbatim": "(EU) 2017/745", "page": 1},
        "ref_list": {"value": ["613289", "613290"], "conf": 0.9, "tier": "T0",
                     "verbatim": "2 codes", "page": 3},
        "validity_to": {"value": "2027-10-30", "conf": 0.97, "tier": "T1",
                        "verbatim": "valid until", "page": 1},
    }
    base.update(overrides)
    return base


def _seed(conn, *, content_hash=HASH, text=STATES_IIA, fields=None, rev=1,
          tier="T0+T1", with_document=True):
    """One registry document, its stored text (018), and its latest attempt."""
    from psycopg.types.json import Json

    if with_document:
        conn.execute(
            "INSERT INTO document (type, regulation, coverage_scope, content_hash, "
            "archive_url, status) VALUES ('DoC','MDR','group',%s,'file:///a.pdf','production')",
            (content_hash,),
        )
    conn.execute(
        "INSERT INTO document_text (content_hash, source, engine, pages, chars, content) "
        "VALUES (%s,'pdf-text','pymupdf',1,%s,%s)",
        (content_hash, len(text), text),
    )
    conn.execute(
        "INSERT INTO extraction_attempt (content_hash, tier, model_id, fields, extract_rev, "
        "playbook_slug, playbook_rev) VALUES (%s,%s,'claude-haiku-4-5',%s,%s,'voco',3)",
        (content_hash, tier, Json(_old_fields() if fields is None else fields), rev),
    )


def _revs(conn, content_hash=HASH):
    return {r["extract_rev"]: r["fields"] for r in conn.execute(
        "SELECT extract_rev, fields FROM extraction_attempt WHERE content_hash=%s",
        (content_hash,)).fetchall()}


# --------------------------------------------------------------------------- #
# plan + apply
# --------------------------------------------------------------------------- #
def test_repair_appends_a_rev_that_keeps_every_old_field(conn):
    """The [latest-extract-rev-can-be-the-poorest] guard: the new rev becomes
    the incumbent for every later reader, so it must carry everything the old
    one did. Adding a key is the ONLY difference allowed."""
    _seed(conn)
    repairs, counts = rsc.plan(conn)
    assert counts["stated"] == 1
    rsc.apply(conn, repairs)

    revs = _revs(conn)
    assert sorted(revs) == [1, 2]
    for key, value in revs[1].items():
        assert revs[2][key] == value, f"rev 2 changed {key}"
    assert revs[2]["stated_class"]["value"] == "IIa"
    assert revs[2]["stated_class"]["tier"] == "T0"


def test_the_new_rev_keeps_the_playbook_that_shaped_the_old_read(conn):
    """Invariant 2: a hint-steered extraction is not reproducible from its
    evidence without the playbook that steered it. The backfill re-reads none
    of that, so it must carry the attribution forward rather than blank it."""
    _seed(conn)
    rsc.apply(conn, rsc.plan(conn)[0])

    row = conn.execute(
        "SELECT playbook_slug, playbook_rev, tier FROM extraction_attempt "
        "WHERE content_hash=%s AND extract_rev=2", (HASH,)).fetchone()
    assert (row["playbook_slug"], row["playbook_rev"]) == ("voco", 3)
    assert row["tier"] == "T0+T1"


def test_repair_skips_a_hash_whose_key_agrees_with_the_current_read(conn):
    """Agreement is on VALUE, not verbatim: the stored verbatim differs from
    what today's parser would quote, and that alone must not churn a rev."""
    _seed(conn, fields=_old_fields(stated_class={
        "value": "IIa", "conf": 0.95, "tier": "T0", "verbatim": "Klasse IIa", "page": None}))

    repairs, counts = rsc.plan(conn)

    assert repairs == []
    assert counts["already_has_key"] == 1
    assert counts["stated"] == 0 and counts["corrected"] == 0


def test_a_stale_wrong_value_is_corrected(conn):
    """The first --apply ran with the pre-guard parser; a re-run must not wave
    its reads through unexamined. A stored value the current parser disagrees
    with earns a corrective rev carrying the fresh read."""
    _seed(conn, fields=_old_fields(stated_class={
        "value": "III", "conf": 0.95, "tier": "T0", "verbatim": "class III", "page": None}))

    repairs, counts = rsc.plan(conn)
    assert counts["corrected"] == 1 and counts["already_has_key"] == 0
    rsc.apply(conn, repairs)

    revs = _revs(conn)
    assert revs[2]["stated_class"]["value"] == "IIa"
    for key, value in revs[1].items():
        if key != "stated_class":
            assert revs[2][key] == value, f"rev 2 changed {key}"


def test_a_boilerplate_read_is_retracted_and_stays_retracted(conn):
    """Docs 389/791: the pre-guard parser read a class off regulation-quoting
    boilerplate. The current parser abstains, so the corrective rev carries NO
    stated_class key -- retraction under an append-only contract -- and a
    second plan reads the retraction as the abstention it is, not as work."""
    boilerplate = ("For marketing of class III devices an additional "
                   "Annex II (4) certificate is mandatory.\n")
    _seed(conn, text=boilerplate, fields=_old_fields(stated_class={
        "value": "III", "conf": 0.95, "tier": "T0", "verbatim": "class III", "page": None}))

    repairs, counts = rsc.plan(conn)
    assert counts["corrected"] == 1
    rsc.apply(conn, repairs)

    revs = _revs(conn)
    assert "stated_class" not in revs[2]
    repairs, counts = rsc.plan(conn)
    assert repairs == [] and counts["abstained_multi"] == 1


def test_repair_counts_the_abstains(conn):
    """A multi-class form is a parser DECLINING, not a document with nothing to
    give -- so it must not disappear into the same bucket as silence."""
    _seed(conn, text=MULTI_CLASS)

    repairs, counts = rsc.plan(conn)

    assert repairs == []
    assert counts["abstained_multi"] == 1
    assert counts["no_mention"] == 0


def test_repair_counts_a_document_that_says_nothing(conn):
    _seed(conn, text=STATES_NOTHING)

    repairs, counts = rsc.plan(conn)

    assert repairs == []
    assert counts["no_mention"] == 1
    assert counts["abstained_multi"] == 0


def test_every_scanned_hash_lands_in_exactly_one_count(conn):
    """CLAUDE.md: skipped rows are counted and reported, never silent. The
    arithmetic is the guard -- a sixth outcome added later without a counter
    would break this rather than vanish."""
    _seed(conn, content_hash="b" * 64, text=STATES_IIA)
    _seed(conn, content_hash="c" * 64, text=MULTI_CLASS)
    _seed(conn, content_hash="d" * 64, text=STATES_NOTHING)
    _seed(conn, content_hash="e" * 64, fields=_old_fields(stated_class={
        "value": "IIa", "conf": 0.95, "tier": "T0", "verbatim": "Class IIa", "page": None}))
    _seed(conn, content_hash="f" * 64, fields=_old_fields(stated_class={
        "value": "I", "conf": 0.95, "tier": "T0", "verbatim": "Class I", "page": None}))

    _, counts = rsc.plan(conn)

    assert counts["scanned"] == 5
    assert sum(counts[k] for k in rsc.OUTCOMES) == counts["scanned"]


def test_only_the_latest_rev_is_read(conn):
    """Selection is DISTINCT ON (content_hash) ORDER BY extract_rev DESC. A hash
    whose LATEST rev already has the key must be skipped even though an older
    rev does not -- otherwise a re-run would append forever."""
    _seed(conn, text=STATES_IIA)
    rsc.apply(conn, rsc.plan(conn)[0])

    repairs, counts = rsc.plan(conn)

    assert repairs == []
    assert counts["already_has_key"] == 1


def test_a_hash_the_registry_does_not_hold_is_not_scanned(conn):
    """An extraction whose document never reached the registry has nothing to
    compare against -- the 033 view joins `document`. Repairing it would append
    revs for rows no screen can ever read."""
    _seed(conn, with_document=False)

    repairs, counts = rsc.plan(conn)

    assert repairs == []
    assert counts["scanned"] == 0


def test_the_repair_emits_no_job(conn):
    """The load-bearing difference from `repair_ref_list`. Re-emitting
    `validate.doc` would re-adjudicate every historic disposition to record a
    display value -- the one thing this repair exists to avoid."""
    _seed(conn)
    rsc.apply(conn, rsc.plan(conn)[0])

    n = conn.execute("SELECT count(*) AS n FROM job").fetchone()["n"]
    assert n == 0


def test_the_repair_writes_no_registry_table(conn):
    """Invariant 1: only the GATE handlers write document/item_document/
    evidence. This CLI writes `extraction_attempt` and nothing else, so the
    historic `document.stated_class` columns stay NULL by design."""
    _seed(conn)
    rsc.apply(conn, rsc.plan(conn)[0])

    row = conn.execute("SELECT stated_class FROM document WHERE content_hash=%s",
                       (HASH,)).fetchone()
    assert row["stated_class"] is None
    assert conn.execute("SELECT count(*) AS n FROM evidence").fetchone()["n"] == 0


# --------------------------------------------------------------------------- #
# CLI — dry run by default, like repair-ref-list and regroup
# --------------------------------------------------------------------------- #
def test_cmd_dry_run_writes_nothing(conn, connect_test, test_db_url, monkeypatch, capsys):
    from app import cli

    monkeypatch.setenv("DATABASE_URL", test_db_url)
    _seed(conn)
    conn.commit()                      # cli.main opens its own connection

    rc = cli.main(["repair-stated-class"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "dry run" in out
    n = connect_test().execute(
        "SELECT count(*) AS n FROM extraction_attempt WHERE content_hash=%s", (HASH,)
    ).fetchone()["n"]
    assert n == 1                      # nothing appended


def test_cmd_apply_writes_and_reports_every_count(conn, connect_test, test_db_url,
                                                  monkeypatch, capsys):
    from app import cli

    monkeypatch.setenv("DATABASE_URL", test_db_url)
    _seed(conn, content_hash="b" * 64, text=STATES_IIA)
    _seed(conn, content_hash="c" * 64, text=MULTI_CLASS)
    _seed(conn, content_hash="d" * 64, text=STATES_NOTHING)
    conn.commit()

    rc = cli.main(["repair-stated-class", "--apply"])

    out = capsys.readouterr().out
    assert rc == 0
    for label in ("scanned", "stated", "corrected", "abstained_multi",
                  "no_mention", "already_has_key"):
        assert label in out
    rows = connect_test().execute(
        "SELECT extract_rev FROM extraction_attempt WHERE content_hash=%s "
        "ORDER BY extract_rev", ("b" * 64,)).fetchall()
    assert [r["extract_rev"] for r in rows] == [1, 2]


def test_cmd_on_a_clean_database_is_a_no_op(test_db_url, monkeypatch, capsys):
    from app import cli

    monkeypatch.setenv("DATABASE_URL", test_db_url)

    rc = cli.main(["repair-stated-class"])

    assert rc == 0
    assert "scanned 0" in capsys.readouterr().out
