"""EUDAMED enhancement path — the identity tables and the four views.

Spec: docs/superpowers/specs/2026-08-25-eudamed-enhancement-design.md

`manufacturer` was created by migration 006 as a *declared, not built* Phase 2
table and held 0 rows until this work (re-verified 2026-08-26). Seeding it here
makes it shared infrastructure -- `email.request` reads it for contacts, which
is why `email_request._contacts()` returns nothing today and the renewal mail
has no recipients -- not an EUDAMED detail.
"""

from __future__ import annotations

import datetime as dt
import uuid


def test_manufacturer_is_seeded_from_the_alias_table(conn):
    """One row per canonical name, so a foreign key from manufacturer_srn has
    something to point at. All 384, not only the nine with device articles
    (Denis, 2026-08-26): probing every supplier is a cross-check of our own
    medical-device flag, not wasted effort.

    The migration's seed (`INSERT ... SELECT DISTINCT canonical_name FROM
    manufacturer_alias ON CONFLICT DO NOTHING`) ran once, against production's
    already-populated `manufacturer_alias` (445 rows / 384 distinct
    canonical_name, measured 2026-08-26). A freshly created test database
    migrates against an empty `manufacturer_alias` -- there is nothing yet for
    RESOLVE to have aliased -- so asserting a fixed row count here would assert
    production data this database was never given. This test instead exercises
    the seed statement itself: several raw BC codes collapsing to one
    canonical name (IVOCLAR: 001 and 005), and a second seed of the same name
    changing nothing (ON CONFLICT DO NOTHING, idempotent)."""
    conn.execute(
        "INSERT INTO manufacturer_alias (raw_name, canonical_name) VALUES "
        "('001', 'IVOCLAR'), ('005', 'IVOCLAR'), ('077', 'GC')"
    )
    seed_sql = (
        "INSERT INTO manufacturer (canonical_name) "
        "SELECT DISTINCT canonical_name FROM manufacturer_alias "
        "ON CONFLICT (canonical_name) DO NOTHING"
    )
    conn.execute(seed_sql)
    conn.execute(seed_sql)  # idempotent: a second run adds nothing
    seeded = conn.execute(
        "SELECT count(*) c FROM manufacturer "
        "WHERE canonical_name IN ('IVOCLAR', 'GC')"
    ).fetchone()["c"]
    assert seeded == 2


def test_a_probe_that_found_nothing_is_distinguishable_from_no_probe(conn):
    """Without `srn_probed_at`, "probed, no SRN exists" and "never probed" are
    the same null, and the article-probe fallback re-runs against every
    unregistered supplier forever."""
    conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES ('NEVERPROBED') "
        "ON CONFLICT DO NOTHING"
    )
    conn.execute(
        "INSERT INTO manufacturer (canonical_name, srn_probed_at) "
        "VALUES ('PROBEDEMPTY', now()) ON CONFLICT DO NOTHING"
    )
    rows = {
        r["canonical_name"]: r["srn_probed_at"]
        for r in conn.execute(
            "SELECT canonical_name, srn_probed_at FROM manufacturer "
            "WHERE canonical_name IN ('NEVERPROBED','PROBEDEMPTY')"
        ).fetchall()
    }
    assert rows["NEVERPROBED"] is None
    assert rows["PROBEDEMPTY"] is not None


def test_one_manufacturer_may_hold_several_srns(conn):
    """`manufacturer_alias` already maps BC codes 001 and 005 both to IVOCLAR:
    our canonical name is a Dentalia-side grouping, not a legal entity, and a
    multinational registers per legal entity. One SRN per canonical name would
    report articles as unregistered when a sibling entity holds them -- and that
    false alarm becomes a wrong e-mail to a supplier."""
    conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES ('TESTCO') "
        "ON CONFLICT DO NOTHING"
    )
    for srn in ("LI-MF-000000522", "DE-MF-000000999"):
        conn.execute(
            "INSERT INTO manufacturer_srn "
            "(canonical_name, srn, discovered_via, status) "
            "VALUES ('TESTCO', %s, 'register-exact', 'auto')", (srn,)
        )
    n = conn.execute(
        "SELECT count(*) c FROM manufacturer_srn WHERE canonical_name='TESTCO'"
    ).fetchone()["c"]
    assert n == 2


def test_an_srn_candidate_defaults_to_pending(conn):
    """Only `auto` and `confirmed` rows are swept. A candidate that arrives
    without an explicit status must not be swept by accident."""
    conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES ('DEFAULTCO') "
        "ON CONFLICT DO NOTHING"
    )
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via) "
        "VALUES ('DEFAULTCO', 'XX-MF-000000001', 'register-fuzzy')"
    )
    row = conn.execute(
        "SELECT status FROM manufacturer_srn WHERE canonical_name='DEFAULTCO'"
    ).fetchone()
    assert row["status"] == "pending"


def test_several_revisions_of_one_certificate_coexist(conn):
    """EUDAMED serves each revision as its own record -- Carl Martin's
    `HZ 1594091-1` comes back twice, `Rev. 1` expiring 2026-06-29 and `Rev. 2`
    running to 2031-06-29. A key that collapsed them would destroy the signal
    this work exists for. The key (number, revision, actor) was tested against
    all 4.608 register rows on 2026-08-26 and collides zero times."""
    for rev, expiry in (("Rev. 1", "2026-06-29"), ("Rev. 2", "2031-06-29")):
        conn.execute(
            "INSERT INTO eudamed_certificate "
            "(certificate_number, revision_number, actor_srn, expiry_date, "
            " synced_at) "
            "VALUES ('HZ 1594091-1', %s, 'DE-MF-000005066', %s, now())",
            (rev, expiry)
        )
    n = conn.execute(
        "SELECT count(*) c FROM eudamed_certificate "
        "WHERE certificate_number='HZ 1594091-1'"
    ).fetchone()["c"]
    assert n == 2


def test_a_certificate_without_a_revision_still_stores(conn):
    """`revisionNumber` is null on 481 of 4.608 records (measured 2026-08-26).
    The empty-string default is what keeps those in a NOT NULL primary key."""
    conn.execute(
        "INSERT INTO eudamed_certificate "
        "(certificate_number, actor_srn, synced_at) "
        "VALUES ('Z-25-052-S-IX-E', 'DE-MF-000006413', now())"
    )
    row = conn.execute(
        "SELECT revision_number FROM eudamed_certificate "
        "WHERE certificate_number='Z-25-052-S-IX-E'"
    ).fetchone()
    assert row["revision_number"] == ""


# --------------------------------------------------------------------------- #
# The three certificate views
# --------------------------------------------------------------------------- #
# Ruling (Denis, 2026-08-26): the drift view JOINS ON BASELINE ONLY. Looser
# normalisations surface as `possible_match` for a human, never as a join. The
# 2026-08-19 _RCODE_SUFFIX ruling is untouched: `Rev. NN` is reported, never
# resolved.


def _cert_row(conn, number, revision, actor, **kw):
    conn.execute(
        "INSERT INTO eudamed_certificate (certificate_number, revision_number, "
        " actor_srn, actor_name, certificate_type, certificate_status, "
        " expiry_date, issue_date, notified_body_srn, synced_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,now()) "
        "ON CONFLICT DO NOTHING",
        (number, revision, actor, kw.get("actor_name", "Test AG"),
         kw.get("ctype", "quality-management-system"),
         kw.get("status", "issued"), kw.get("expiry"), kw.get("issue"),
         kw.get("nb", "0123")))


def _sweepable_srn(conn, canonical, srn):
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES (%s) "
                 "ON CONFLICT DO NOTHING", (canonical,))
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, "
        " status) VALUES (%s,%s,'register-exact','auto') "
        "ON CONFLICT DO NOTHING", (canonical, srn))


def test_drift_reports_both_revisions_and_resolves_neither(conn, seeded_doc):
    """Our cert_number embeds the revision (`G15 043306 0282 Rev. 00`);
    EUDAMED separates it. The view splits ours to join and shows both sides.
    96 declarations sit in that shape."""
    doc_id = seeded_doc(type="EC", cert_number="G15 043306 0282 Rev. 00",
                        canonical="IVOCLAR")
    _sweepable_srn(conn, "IVOCLAR", "LI-MF-000000522")
    _cert_row(conn, "G15 043306 0282", "Rev. 02", "LI-MF-000000522",
              status="supplemented", expiry=dt.date(2031, 5, 4))

    row = conn.execute(
        "SELECT * FROM certificate_drift WHERE doc_id = %s", (doc_id,)
    ).fetchone()

    assert row["our_revision"] == "Rev. 00"
    assert row["eudamed_revision"] == "Rev. 02"
    assert row["certificate_number"] == "G15 043306 0282"


def test_drift_writes_no_registry_column(conn, seeded_doc):
    """Widening the revision comparison into a supersession is exactly the
    assertion the 2026-08-19 ruling refused. The view reports; it decides
    nothing."""
    doc_id = seeded_doc(type="EC", cert_number="G15 043306 0282 Rev. 00",
                        canonical="IVOCLAR")
    _sweepable_srn(conn, "IVOCLAR", "LI-MF-000000522")
    _cert_row(conn, "G15 043306 0282", "Rev. 02", "LI-MF-000000522")

    conn.execute("SELECT count(*) FROM certificate_drift")

    row = conn.execute(
        "SELECT superseded_by, cert_doc_id, status FROM document "
        "WHERE doc_id = %s", (doc_id,)
    ).fetchone()
    assert row["superseded_by"] is None
    assert row["cert_doc_id"] is None


def test_a_matching_revision_is_not_drift(conn, seeded_doc):
    """Live pairing (design.md S1.6, 08-26): our extraction always keeps the
    `Rev` token (`held_certificate`'s regex), so we hold `Rev. 00` while
    EUDAMED serves this same certificate as `Rev.0` -- a different spelling of
    the same revision, not two different revisions. A byte-for-byte compare
    (the fixture this replaces used `Rev. 00` on both sides, which a naive
    compare already treats as equal and so never exercised the real mismatch)
    must not report that as drift."""
    seeded_doc(type="EC", cert_number="HZ 2020214-1 Rev. 00",
               canonical="SUREDENT")
    _sweepable_srn(conn, "SUREDENT", "KR-MF-000000001")
    _cert_row(conn, "HZ 2020214-1", "Rev.0", "KR-MF-000000001")

    n = conn.execute(
        "SELECT count(*) c FROM certificate_drift "
        "WHERE certificate_number = 'HZ 2020214-1'"
    ).fetchone()["c"]
    assert n == 0


def test_a_bare_digit_matches_the_same_revision_with_leading_zero(conn, seeded_doc):
    """Live pairing (design.md S1.3/S1.6): EUDAMED also serves a revision as a
    bare digit with no `Rev` token at all and no leading zero (`2` for
    ANGELUS's `C528661-PA-NoMA-BRA`). `Rev. 02` and `2` name the same
    revision; a digits-only compare that does not also strip the leading zero
    would still report this pairing as drift."""
    seeded_doc(type="EC", cert_number="C528661-PA-NoMA-BRA Rev. 02",
               canonical="ANGELUSCO")
    _sweepable_srn(conn, "ANGELUSCO", "BR-MF-000000001")
    _cert_row(conn, "C528661-PA-NoMA-BRA", "2", "BR-MF-000000001")

    n = conn.execute(
        "SELECT count(*) c FROM certificate_drift "
        "WHERE certificate_number = 'C528661-PA-NoMA-BRA'"
    ).fetchone()["c"]
    assert n == 0


def test_a_looser_match_is_a_possible_match_not_a_join(conn, seeded_doc):
    """`HZ 1470094-1 G` (36 documents) matches Brasseler only if a trailing
    single letter is stripped. That would assert two differently-printed strings
    name one certificate -- the same class of claim the Rev. NN ruling refused.
    It is shown, not joined."""
    doc_id = seeded_doc(type="EC", cert_number="HZ 1470094-1 G",
                        canonical="KOMET")
    _sweepable_srn(conn, "KOMET", "DE-MF-000000777")
    _cert_row(conn, "HZ 1470094-1", "Rev. 5", "DE-MF-000000777",
              status="supplemented", expiry=dt.date(2026, 2, 28))

    drift = conn.execute(
        "SELECT count(*) c FROM certificate_drift WHERE doc_id = %s", (doc_id,)
    ).fetchone()["c"]
    possible = conn.execute(
        "SELECT possible_match, possible_rule FROM certificate_drift_candidate "
        "WHERE doc_id = %s", (doc_id,)
    ).fetchone()

    assert drift == 0
    assert possible["possible_match"] == "HZ 1470094-1"
    assert possible["possible_rule"] == "trailing-letter"


def test_a_sx_hz_prefix_match_is_a_possible_match_not_a_join(conn, seeded_doc):
    """Ruling (Denis, 2026-08-26): `'^SX\\M'` must match `SX` only when it is
    followed by a word break, not when it is the start of a longer token
    (`SXA...`). Proved directly against Postgres (regexp_replace('SX 1',
    '^SX\\M', 'HZ') = 'HZ 1'; regexp_replace('SXA 1', ...) = 'SXA 1',
    unchanged) -- this test additionally proves the rule fires through the
    view itself, not just the raw regex, since the brief's only other
    looser-match test exercises trailing-letter, not this rule."""
    doc_id = seeded_doc(type="EC", cert_number="SX 043306 0282",
                        canonical="PREFIXCO")
    _sweepable_srn(conn, "PREFIXCO", "DE-MF-000000888")
    _cert_row(conn, "HZ 043306 0282", "Rev. 1", "DE-MF-000000888")

    drift = conn.execute(
        "SELECT count(*) c FROM certificate_drift WHERE doc_id = %s", (doc_id,)
    ).fetchone()["c"]
    possible = conn.execute(
        "SELECT possible_match, possible_rule FROM certificate_drift_candidate "
        "WHERE doc_id = %s", (doc_id,)
    ).fetchone()

    assert drift == 0
    assert possible["possible_match"] == "HZ 043306 0282"
    assert possible["possible_rule"] == "sx-hz-prefix"


def test_certificate_drift_candidate_is_distinct_per_bound_article(conn, seeded_doc):
    """`held_certificate` emits one row per (doc, production item_document
    link, item_group_member row) -- this branch's own migration comment
    records a manufacturer-scope certificate binding 2.567 articles at once.
    `certificate_drift` (its sibling view) guards against the resulting
    fan-out with `SELECT DISTINCT`; this near-miss view selects only doc- and
    certificate-level columns -- nothing that distinguishes the article -- and
    must be deduplicated the same way, or one near-miss renders once per
    bound article on an unpaginated panel."""
    doc_id = seeded_doc(type="EC", cert_number="HZ 1470094-1 G",
                        canonical="FANOUTCO")
    # A second production item_document link to the SAME document, through a
    # second item/group -- the shape a manufacturer-scope binding produces.
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, catalogue, "
        "updated_at) VALUES ('FANOUT-ITEM-2', 'Widget2', 'test', 'LJ', now())")
    gid2 = conn.execute(
        "INSERT INTO item_group (canonical_manufacturer) VALUES ('FANOUTCO') "
        "RETURNING group_id"
    ).fetchone()["group_id"]
    conn.execute(
        "INSERT INTO item_group_member (group_id, item_ref, match_basis) "
        "VALUES (%s, 'FANOUT-ITEM-2', 'manual')", (gid2,))
    conn.execute(
        "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
        "VALUES ('FANOUT-ITEM-2', %s, 'ref-list', 'production')", (doc_id,))
    _sweepable_srn(conn, "FANOUTCO", "DE-MF-000000FAN")
    _cert_row(conn, "HZ 1470094-1", "Rev. 5", "DE-MF-000000FAN",
              status="supplemented", expiry=dt.date(2026, 2, 28))

    rows = conn.execute(
        "SELECT * FROM certificate_drift_candidate WHERE doc_id = %s", (doc_id,)
    ).fetchall()
    assert len(rows) == 1


def test_status_alert_carries_only_the_adverse_four(conn):
    """87 withdrawn, 68 cancelled, 31 restricted, 18 suspended across the
    register. `supplemented` and `reissued` are ordinary lifecycle events and
    must not raise an alert."""
    _sweepable_srn(conn, "ALERTCO", "XX-MF-100")
    for status in ("withdrawn", "cancelled", "suspended", "restricted",
                   "issued", "supplemented", "reissued", "amended"):
        _cert_row(conn, f"C-{status}", "Rev. 1", "XX-MF-100", status=status)

    got = {r["certificate_status"] for r in conn.execute(
        "SELECT certificate_status FROM certificate_status_alert "
        "WHERE canonical_name = 'ALERTCO'").fetchall()}

    assert got == {"withdrawn", "cancelled", "suspended", "restricted"}


def test_a_certificate_we_hold_no_copy_of_is_a_gap(conn):
    """Carl Martin's HZ 1594091-1 is the worked example: two register rows, and
    no document we hold cites it."""
    _sweepable_srn(conn, "CARL MARTIN", "DE-MF-000005066")
    _cert_row(conn, "HZ 1594091-1", "Rev. 2", "DE-MF-000005066",
              expiry=dt.date(2031, 6, 29))

    row = conn.execute(
        "SELECT certificate_number FROM certificate_gap "
        "WHERE canonical_name = 'CARL MARTIN'").fetchone()
    assert row["certificate_number"] == "HZ 1594091-1"


def test_a_certificate_we_do_hold_is_not_a_gap(conn, seeded_doc):
    seeded_doc(type="EC", cert_number="HZ 9999999-1", canonical="HELDCO")
    _sweepable_srn(conn, "HELDCO", "XX-MF-200")
    _cert_row(conn, "HZ 9999999-1", "Rev. 1", "XX-MF-200")

    n = conn.execute(
        "SELECT count(*) c FROM certificate_gap "
        "WHERE certificate_number = 'HZ 9999999-1'").fetchone()["c"]
    assert n == 0


def test_a_doc_that_merely_cites_a_certificate_is_not_holding_it(conn, seeded_doc):
    """A declaration of conformity quotes the notified-body certificate number
    verbatim -- 96 of ours carry `G15 043306 0282 Rev. 00`. Counting that as
    holding the certificate suppressed certificate_gap while certificate_drift's
    type filter skipped it, so an EUDAMED-listed certificate we have never
    archived produced no finding anywhere. Measured on the dev database
    2026-08-26: 4 real (manufacturer, certificate) pairs."""
    seeded_doc(type="DoC", cert_number="HZ 8888888-1", canonical="CITECO")
    _sweepable_srn(conn, "CITECO", "XX-MF-900")
    _cert_row(conn, "HZ 8888888-1", "Rev. 1", "XX-MF-900")

    row = conn.execute(
        "SELECT certificate_number FROM certificate_gap "
        "WHERE canonical_name = 'CITECO'").fetchone()
    assert row is not None
    assert row["certificate_number"] == "HZ 8888888-1"


def test_a_pending_srn_contributes_to_no_view(conn):
    """Only `auto` and `confirmed` attributions are trusted. A pending guess
    must not raise an alert against a manufacturer it may not belong to."""
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES "
                 "('PENDINGCO') ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, "
        " status) VALUES ('PENDINGCO','XX-MF-300','register-fuzzy','pending')")
    _cert_row(conn, "C-pending", "Rev. 1", "XX-MF-300", status="withdrawn")

    n = conn.execute(
        "SELECT count(*) c FROM certificate_status_alert "
        "WHERE canonical_name = 'PENDINGCO'").fetchone()["c"]
    assert n == 0


def test_the_mirror_can_tell_a_new_device_from_an_old_one(conn):
    """Before this column, `synced_at` was stomped to now() on every upsert, so
    after one sweep a device registered last week and one registered in 2021
    were indistinguishable and NO delta was derivable at all."""
    conn.execute(
        "INSERT INTO eudamed_mirror (udi_di, basic_udi_di, reference, "
        " synced_at, first_seen) VALUES "
        "('D-OLD','BUDI-1','111', now(), now() - interval '400 days'),"
        "('D-NEW','BUDI-2','222', now(), now() - interval '2 days')")

    n = conn.execute(
        "SELECT count(*) c FROM eudamed_mirror "
        "WHERE first_seen > now() - interval '30 days' "
        "  AND udi_di IN ('D-OLD','D-NEW')").fetchone()["c"]
    assert n == 1


def test_first_seen_is_never_later_than_the_last_sync(conn):
    """`first_seen` answers "when did we first see this device". Migration 044's
    `DEFAULT now()` stamped every pre-existing row with the ALTER TABLE's own
    instant instead -- later than the sync that actually observed them (measured:
    591 rows at 2026-08-26 11:54:55 against synced_at of 2026-08-25 19:07). A
    first sighting cannot be after the most recent one. Migration 045's backfill
    (`UPDATE ... SET first_seen = synced_at WHERE synced_at < first_seen`) fixes
    exactly this population and is idempotent -- re-running it here proves the
    guard, not just a one-off assignment."""
    conn.execute(
        "INSERT INTO eudamed_mirror (udi_di, basic_udi_di, reference, synced_at, "
        " first_seen) VALUES ('D-BACKFILL','BUDI-B','1', "
        " now() - interval '2 days', now())")
    conn.execute("UPDATE eudamed_mirror SET first_seen = synced_at "
                 " WHERE synced_at < first_seen")
    row = conn.execute(
        "SELECT first_seen, synced_at FROM eudamed_mirror "
        " WHERE udi_di = 'D-BACKFILL'").fetchone()
    assert row["first_seen"] == row["synced_at"]


def test_a_manufacturer_becomes_due_after_the_interval(conn):
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('DUECO') "
                 "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO eudamed_sweep_state (canonical_name, last_swept_at, due_at) "
        "VALUES ('DUECO', now() - interval '100 days', now() - interval '1 day')")

    row = conn.execute(
        "SELECT canonical_name FROM eudamed_sweep_due "
        "WHERE canonical_name = 'DUECO'").fetchone()
    assert row is not None


def test_a_released_sweep_is_no_longer_listed_as_due(conn):
    """The scheduler proposes; a person releases. A manufacturer already
    released must not keep nagging."""
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('RELCO') "
                 "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO eudamed_sweep_state (canonical_name, last_swept_at, "
        " due_at, released_at, released_by) VALUES "
        "('RELCO', now() - interval '100 days', now() - interval '1 day', "
        " now(), 'ui')")

    n = conn.execute(
        "SELECT count(*) c FROM eudamed_sweep_due "
        "WHERE canonical_name = 'RELCO'").fetchone()["c"]
    assert n == 0


# --------------------------------------------------------------------------- #
# The declaration gap and the sweep delta (Task 13)
# --------------------------------------------------------------------------- #
# EUDAMED holds zero declarations itself -- it can only say what to go and ask
# for, and the ask is a Basic UDI-DI group, not an article (design doc §4.4).


def _seed_item_row(conn, *, item_ref, canonical):
    """`item_mirror` + group membership only -- no document, no link. Mirrors
    the join path `eudamed_declaration_gap` and `eudamed_gap_summary` read:
    item_mirror -> item_group_member -> item_group.canonical_manufacturer.
    `md_flag` TRUE: both views answer an MDR question, same restriction
    `web/registry.py`'s completeness card already applies."""
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, "
        " catalogue, updated_at) VALUES (%s,'Widget','test',TRUE,'LJ',now())",
        (item_ref,))
    group_id = conn.execute(
        "INSERT INTO item_group (canonical_manufacturer) VALUES (%s) "
        "RETURNING group_id", (canonical,)).fetchone()["group_id"]
    conn.execute(
        "INSERT INTO item_group_member (group_id, item_ref, match_basis) "
        "VALUES (%s,%s,'manual')", (group_id, item_ref))


def _seed_link(conn, *, item_ref, status):
    """A `DoC`-type document plus an `item_document` link at the given LINK
    status, for an `item_ref` already seeded by `_seed_item_row`. `type='DoC'`
    on purpose: the gap views count declarations specifically, not any
    document. The document itself is always `status='production'` -- C5's
    link-level gate is what this helper exercises, not the document's own
    lifecycle."""
    suffix = uuid.uuid4().hex[:12]
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, validity_from, validity_to, "
        " status, content_hash, archive_url, coverage_scope) VALUES "
        "('DoC','MDR','2022-01-01','2031-01-01','production',%s,"
        " '/archive/seed-link.pdf','group') RETURNING doc_id",
        (f"h-link-{suffix}",)).fetchone()["doc_id"]
    conn.execute(
        "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
        "VALUES (%s,%s,'ref-list',%s)", (item_ref, doc_id, status))
    return doc_id


def test_the_gap_groups_by_basic_udi_di(conn):
    """The registry's largest gap -- 2.569 Carl Martin reusable instruments with
    no declaration -- is a request list of 70 documents, because a declaration
    covers a Basic UDI-DI group, not an article.

    Paired with `test_a_retracted_link_does_not_hide_an_article` below: these
    three articles have NO `item_document` row at all, which is what actually
    exercises the JOIN-not-WHERE guard (a WHERE-based exclusion turns the LEFT
    join inner and drops every article with no link at all -- exactly these
    three). The paired test seeds an article that DOES have a link, just a
    retracted one, so between the two, both no-link and retracted-link are
    covered."""
    _sweepable_srn(conn, "CARL MARTIN", "DE-MF-000005066")
    for i, ref in enumerate(("1845", "1846", "1847")):
        conn.execute(
            "INSERT INTO eudamed_mirror (udi_di, basic_udi_di, reference, "
            " manufacturer_srn, synced_at) VALUES (%s,'BUDI-A',%s,"
            " 'DE-MF-000005066', now())", (f"D{i}", ref))
        _seed_item_row(conn, item_ref=ref, canonical="CARL MARTIN")

    row = conn.execute(
        "SELECT basic_udi_di, articles FROM eudamed_declaration_gap "
        "WHERE canonical_name = 'CARL MARTIN'").fetchone()
    assert row["basic_udi_di"] == "BUDI-A"
    assert row["articles"] == 3


def test_a_retracted_link_does_not_hide_an_article(conn):
    """Excluded on the JOIN, not in WHERE: a WHERE turns the LEFT join inner and
    drops every article with no link at all -- a bug already made and fixed once
    in web/registry.py.

    Paired with `test_the_gap_groups_by_basic_udi_di` above: this article HAS a
    link (just a retracted one), so this test alone does not distinguish
    JOIN-exclusion from WHERE-exclusion -- a WHERE-based exclusion still keeps
    a row with a real (if retracted) `item_document` match. What actually
    catches the WHERE bug is the paired test's three articles, which have no
    `item_document` row at all."""
    _sweepable_srn(conn, "RETRACTCO", "XX-MF-800")
    _seed_item_row(conn, item_ref="R1", canonical="RETRACTCO")
    conn.execute(
        "INSERT INTO eudamed_mirror (udi_di, basic_udi_di, reference, "
        " manufacturer_srn, synced_at) VALUES "
        "('DR1','BUDI-R','R1','XX-MF-800', now())")
    _seed_link(conn, item_ref="R1", status="retracted")

    row = conn.execute(
        "SELECT articles FROM eudamed_declaration_gap "
        "WHERE canonical_name = 'RETRACTCO'").fetchone()
    assert row["articles"] == 1


def test_two_mirror_rows_with_the_same_reference_do_not_double_count(conn):
    """Review finding, 2026-08-27: `eudamed_match`'s `SELECT DISTINCT` included
    `trade_name`, so two `eudamed_mirror` rows sharing one `reference` and one
    `basic_udi_di` but carrying different `trade_name` values did not
    collapse -- migration 039 documents this dataset's exact shape
    (`trade_name` is per-UDI-DI: one Ivoclar `basic_udi_di` group spans 74
    individual device records, each free to carry its own trade name) -- and
    the one physical article was counted twice in the request list an
    operator works from. None of this file's other tests seed two mirror rows
    for a single article, which is why the defect was invisible until now."""
    _sweepable_srn(conn, "DUPTRADECO", "XX-MF-806")
    _seed_item_row(conn, item_ref="DUP1", canonical="DUPTRADECO")
    conn.execute(
        "INSERT INTO eudamed_mirror (udi_di, basic_udi_di, reference, "
        " trade_name, manufacturer_srn, synced_at) VALUES "
        "('UDI-DUP-A','BUDI-DUP','DUP1','Trade A','XX-MF-806', now()), "
        "('UDI-DUP-B','BUDI-DUP','DUP1','Trade B','XX-MF-806', now())")

    row = conn.execute(
        "SELECT articles FROM eudamed_declaration_gap "
        "WHERE canonical_name = 'DUPTRADECO'").fetchone()
    assert row["articles"] == 1


def test_staged_is_counted_separately_from_absent(conn):
    """A gap list that counts a staged declaration as missing sends Nataša
    chasing a document she has."""
    _sweepable_srn(conn, "STAGEDCO", "XX-MF-801")
    _seed_item_row(conn, item_ref="S1", canonical="STAGEDCO")
    conn.execute(
        "INSERT INTO eudamed_mirror (udi_di, basic_udi_di, reference, "
        " manufacturer_srn, synced_at) VALUES "
        "('DS1','BUDI-S','S1','XX-MF-801', now())")
    _seed_link(conn, item_ref="S1", status="staged")

    row = conn.execute(
        "SELECT articles, staged FROM eudamed_declaration_gap "
        "WHERE canonical_name = 'STAGEDCO'").fetchone()
    assert row["staged"] == 1


def test_articles_eudamed_does_not_know_are_their_own_bucket(conn):
    """178 of 2.567 Carl Martin articles did not resolve, cause unknown. If the
    gap list treats "not in EUDAMED" as "no gap", the report overstates
    coverage. CLAUDE.md: skipped rows are counted and reported, never silent."""
    _sweepable_srn(conn, "UNKNOWNCO", "XX-MF-802")
    _seed_item_row(conn, item_ref="U1", canonical="UNKNOWNCO")

    row = conn.execute(
        "SELECT not_in_eudamed FROM eudamed_gap_summary "
        "WHERE canonical_name = 'UNKNOWNCO'").fetchone()
    assert row["not_in_eudamed"] == 1


def test_the_delta_lists_new_basic_udi_groups_and_status_changes(conn):
    """Ruled 2026-08-26: the delta unit is the Basic UDI-DI group and the device
    status transition. Individual new devices inside a group we already track
    are a count, not a list -- a sweep returns 11.364 rows for Ivoclar.

    Denis, 2026-08-27: the brief seeded DELTACO through `_sweepable_srn` alone,
    which writes `manufacturer` and `manufacturer_srn` only and never touches
    `eudamed_sweep_state` -- so `last_swept_at` is NULL and, under ruling 24
    below, the delta is empty and this test fails as written. The fix is the
    `eudamed_sweep_state` row: `last_swept_at` sits between the two seeded
    `first_seen` values (400 days and 2 days old) without being near either
    boundary."""
    _sweepable_srn(conn, "DELTACO", "XX-MF-803")
    conn.execute(
        "INSERT INTO eudamed_sweep_state (canonical_name, last_swept_at) "
        "VALUES ('DELTACO', now() - interval '30 days')")
    conn.execute(
        "INSERT INTO eudamed_mirror (udi_di, basic_udi_di, reference, "
        " manufacturer_srn, device_status_type, synced_at, first_seen) VALUES "
        "('DD1','BUDI-OLD','1','XX-MF-803','on-the-market', now(), "
        "  now() - interval '400 days'), "
        "('DD2','BUDI-NEW','2','XX-MF-803','on-the-market', now(), "
        "  now() - interval '2 days')")

    rows = conn.execute(
        "SELECT basic_udi_di FROM eudamed_sweep_delta "
        "WHERE canonical_name = 'DELTACO'").fetchall()
    assert [r["basic_udi_di"] for r in rows] == ["BUDI-NEW"]


def test_a_never_swept_manufacturer_has_an_empty_delta(conn):
    """RULING 24, Denis 2026-08-26, binding: no baseline means no delta -- not
    "everything is new". Both shapes of "no baseline" are covered: no
    `eudamed_sweep_state` row at all (NOROWCO), and a row whose `last_swept_at`
    is NULL (NULLSWEPTCO -- e.g. an SRN resolved before any sweep has ever
    run). Without this test the predicate (`last_swept_at IS NOT NULL AND
    first_seen > last_swept_at`) is only a comment: weakening it to treat a
    NULL baseline as "anything is newer" would put every device a
    manufacturer has ever registered in front of an operator on its first
    sweep -- 11.364 rows for Ivoclar -- which is exactly the noise this view
    exists to suppress, and nothing else in this file would catch the
    regression."""
    _sweepable_srn(conn, "NOROWCO", "XX-MF-804")
    conn.execute(
        "INSERT INTO eudamed_mirror (udi_di, basic_udi_di, reference, "
        " manufacturer_srn, device_status_type, synced_at, first_seen) VALUES "
        "('DN1','BUDI-A','9','XX-MF-804','on-the-market', now(), now())")

    _sweepable_srn(conn, "NULLSWEPTCO", "XX-MF-805")
    conn.execute(
        "INSERT INTO eudamed_sweep_state (canonical_name, last_swept_at) "
        "VALUES ('NULLSWEPTCO', NULL)")
    conn.execute(
        "INSERT INTO eudamed_mirror (udi_di, basic_udi_di, reference, "
        " manufacturer_srn, device_status_type, synced_at, first_seen) VALUES "
        "('DN2','BUDI-B','8','XX-MF-805','on-the-market', now(), now())")

    rows = conn.execute(
        "SELECT canonical_name FROM eudamed_sweep_delta "
        "WHERE canonical_name IN ('NOROWCO','NULLSWEPTCO')").fetchall()
    assert rows == []
