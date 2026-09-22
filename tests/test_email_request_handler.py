"""email.request / email.reminder — S2.4 EMAIL outbound handlers.

Table-driven against real Postgres. The two client rulings this handler exists
to obey are asserted directly, because both are the kind of thing that silently
regresses into per-document mailing:

  spec §7.2  ONE request per manufacturer per cadence period, and a second
             trigger in the same period ADDS its documents rather than drafting
             a second mail
  spec §7.1  nothing sends; the chain ends at an `email_draft` row
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.handlers.email_request import (
    compose, compose_gap, drift_for_manufacturer, gap_groups_for_manufacturer,
    handle_email_reminder, handle_email_request, lapsing_for_manufacturer,
    period_key,
)

TODAY = dt.date(2026, 8, 20)


# --------------------------------------------------------------------------- #
# fixtures: a manufacturer with items, a group, and expiring documents
# --------------------------------------------------------------------------- #
def _seed_manufacturer(conn, name="IVOCLAR", contacts=None) -> int:
    gid = conn.execute(
        "INSERT INTO item_group (canonical_manufacturer, label) "
        "VALUES (%s, 'fam') RETURNING group_id", (name,),
    ).fetchone()["group_id"]
    if contacts is not None:
        conn.execute(
            "INSERT INTO manufacturer (canonical_name, contact_emails) VALUES (%s,%s) "
            "ON CONFLICT (canonical_name) DO UPDATE SET contact_emails = EXCLUDED.contact_emails",
            (name, contacts),
        )
    return gid


def _seed_expiring_doc(conn, gid, *, content_hash, expires, item_ref,
                       cert_number=None, type_="DoC", regulation="MDR") -> int:
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, "
        "  archive_url, status, validity_to, cert_number) "
        "VALUES (%s,%s,'group',%s,'file:///x','production',%s,%s) RETURNING doc_id",
        (type_, regulation, content_hash, expires, cert_number),
    ).fetchone()["doc_id"]
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, catalogue, updated_at) "
        "VALUES (%s,'thing','raw','LJ', now()) "
        "ON CONFLICT (item_ref) DO NOTHING", (item_ref,))
    conn.execute(
        "INSERT INTO item_group_member (group_id, item_ref, match_basis) "
        "VALUES (%s,%s,'mfr-scope') ON CONFLICT DO NOTHING", (gid, item_ref))
    conn.execute(
        "INSERT INTO item_document (item_ref, doc_id, status, match_basis) "
        "VALUES (%s,%s,'production','mfr-scope')", (item_ref, doc_id))
    return doc_id


# --------------------------------------------------------------------------- #
# the cadence bucket
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("day,cadence,expected_same", [
    ((2026, 8, 17), 7, (2026, 8, 20)),   # Mon and Thu of one ISO week
    ((2026, 8, 20), 7, (2026, 8, 23)),   # Thu and Sun, same week
])
def test_same_period_for_days_in_one_week(day, cadence, expected_same):
    a = period_key(dt.date(*day), cadence)
    b = period_key(dt.date(*expected_same), cadence)
    assert a == b


def test_next_week_is_a_different_period():
    assert period_key(dt.date(2026, 8, 20), 7) != period_key(dt.date(2026, 8, 27), 7)


# --------------------------------------------------------------------------- #
# §7.2 — one mail per manufacturer per period
# --------------------------------------------------------------------------- #
def test_one_request_covers_every_expiring_document(conn):
    gid = _seed_manufacturer(conn, contacts=["quality@ivoclar.example"])
    a = _seed_expiring_doc(conn, gid, content_hash="a" * 64,
                           expires="2026-05-04", item_ref="I1", cert_number="CERT-1")
    b = _seed_expiring_doc(conn, gid, content_hash="b" * 64,
                           expires="2024-05-26", item_ref="I2", regulation="MDD")
    conn.commit()

    out = handle_email_request(conn, {"id": 1, "type": "email.request",
                                      "payload": {"doc_id": a, "state": "lapsed"}})
    assert out["counts"]["requested"] == 1
    assert out["counts"]["documents_linked"] == 2
    assert out["counts"]["draft_created"] == 1

    linked = {r["doc_id"] for r in conn.execute(
        "SELECT doc_id FROM renewal_request_document").fetchall()}
    assert linked == {a, b}
    assert conn.execute("SELECT count(*) AS n FROM email_draft").fetchone()["n"] == 1


def test_second_trigger_in_the_same_period_adds_documents_not_a_second_mail(conn):
    gid = _seed_manufacturer(conn, contacts=["quality@ivoclar.example"])
    a = _seed_expiring_doc(conn, gid, content_hash="c" * 64,
                           expires="2026-05-04", item_ref="I3")
    conn.commit()
    handle_email_request(conn, {"id": 1, "type": "email.request", "payload": {"doc_id": a}})

    # a second document crosses the horizon later the same week
    b = _seed_expiring_doc(conn, gid, content_hash="d" * 64,
                           expires="2026-06-01", item_ref="I4")
    conn.commit()
    out = handle_email_request(conn, {"id": 2, "type": "email.request", "payload": {"doc_id": b}})

    assert out["counts"]["cadence_attached"] == 1
    assert out["counts"]["documents_linked"] == 1     # only the new one
    assert out["counts"]["draft_updated"] == 1        # regenerated, not stacked
    assert conn.execute("SELECT count(*) AS n FROM renewal_request").fetchone()["n"] == 1
    assert conn.execute("SELECT count(*) AS n FROM email_draft").fetchone()["n"] == 1
    body = conn.execute("SELECT body FROM email_draft").fetchone()["body"]
    assert "2026-06-01" in body and "2026-05-04" in body


def test_an_approved_draft_is_never_rewritten_and_never_doubled(conn):
    """A person has read that exact text and is about to send it by hand.

    Denis, 2026-09-03: once a draft has been approved, sent or archived, the
    system must not offer another one for it. This test used to assert the
    opposite -- `draft_created == 1`, "a new one, beside the approved one" --
    and that is what put two live reminder drafts on four requests. The newly
    expiring document is not dropped: it is attached to the same
    `renewal_request`, so the NEXT cadence period drafts one mail carrying
    both. One period, one mail, whoever touched it."""
    gid = _seed_manufacturer(conn, contacts=["q@x.example"])
    a = _seed_expiring_doc(conn, gid, content_hash="e" * 64,
                           expires="2026-05-04", item_ref="I5")
    conn.commit()
    handle_email_request(conn, {"id": 1, "type": "email.request", "payload": {"doc_id": a}})
    conn.execute("UPDATE email_draft SET status='ready', body='APPROVED TEXT'")
    conn.commit()

    b = _seed_expiring_doc(conn, gid, content_hash="f" * 64,
                           expires="2026-06-01", item_ref="I6")
    conn.commit()
    out = handle_email_request(conn, {"id": 2, "type": "email.request", "payload": {"doc_id": b}})

    assert out["counts"].get("draft_created") is None
    assert out["counts"]["draft_settled"] == 1
    assert conn.execute("SELECT count(*) AS n FROM email_draft").fetchone()["n"] == 1
    kept = conn.execute(
        "SELECT body FROM email_draft WHERE status='ready'").fetchone()["body"]
    assert kept == "APPROVED TEXT"
    # The new document is still on the request, so it is chased next period.
    assert conn.execute(
        "SELECT count(*) AS n FROM renewal_request_document WHERE doc_id=%s",
        (b,)).fetchone()["n"] == 1


@pytest.mark.parametrize("settled", ["ready", "sent", "cancelled"])
def test_a_settled_draft_stops_the_system_offering_another(conn, settled):
    """`cancelled` is the Archive button: "we do not need this one". Before
    this, archiving a draft and re-running produced a fresh one, so the
    decision could not stick -- observed live on the gap-request pair 25/26,
    2026-09-02."""
    gid = _seed_manufacturer(conn, contacts=["q@x.example"])
    a = _seed_expiring_doc(conn, gid, content_hash="a" * 64,
                           expires="2026-05-04", item_ref="S1")
    conn.commit()
    handle_email_request(conn, {"id": 1, "type": "email.request", "payload": {"doc_id": a}})
    conn.execute("UPDATE email_draft SET status=%s", (settled,))
    conn.commit()

    out = handle_email_request(conn, {"id": 2, "type": "email.request",
                                      "payload": {"doc_id": a}})
    assert out["counts"]["draft_settled"] == 1
    assert conn.execute("SELECT count(*) AS n FROM email_draft").fetchone()["n"] == 1


# --------------------------------------------------------------------------- #
# degradation: no manufacturer, no contacts, nothing expiring
# --------------------------------------------------------------------------- #
def test_no_manufacturer_writes_no_draft_and_says_so(conn):
    out = handle_email_request(conn, {"id": 1, "type": "email.request",
                                      "payload": {"doc_id": 999999}})
    assert out["counts"]["no_manufacturer"] == 1
    assert out["notes"]
    assert conn.execute("SELECT count(*) AS n FROM email_draft").fetchone()["n"] == 0


def test_missing_contacts_still_drafts_but_unaddressed(conn):
    """Ruling 2026-08-20: assume contacts will exist, but a missing one must not
    block the chase — under drafts-only a person addresses the mail, and the UI
    refuses to save a draft with no recipient."""
    # A name of its own: `manufacturer` rows survive between tests, so reusing
    # IVOCLAR here would silently pick up contacts another test inserted.
    gid = _seed_manufacturer(conn, name="NOCONTACT AG", contacts=None)
    a = _seed_expiring_doc(conn, gid, content_hash="1" * 64,
                           expires="2026-05-04", item_ref="I7")
    conn.commit()
    out = handle_email_request(conn, {"id": 1, "type": "email.request", "payload": {"doc_id": a}})
    assert out["counts"]["no_contact"] == 1
    assert out["counts"]["draft_created"] == 1
    assert conn.execute("SELECT to_addrs FROM email_draft").fetchone()["to_addrs"] == []


def test_nothing_expiring_writes_nothing(conn):
    gid = _seed_manufacturer(conn, contacts=["q@x.example"])
    _seed_expiring_doc(conn, gid, content_hash="2" * 64,
                       expires="2099-01-01", item_ref="I8")
    conn.commit()
    out = handle_email_request(conn, {"id": 1, "type": "email.request",
                                      "payload": {"manufacturer": "IVOCLAR"}})
    assert out["counts"]["nothing_expiring"] == 1
    assert conn.execute("SELECT count(*) AS n FROM renewal_request").fetchone()["n"] == 0


# --------------------------------------------------------------------------- #
# email.reminder
# --------------------------------------------------------------------------- #
def _make_request(conn) -> int:
    gid = _seed_manufacturer(conn, contacts=["q@x.example"])
    a = _seed_expiring_doc(conn, gid, content_hash="3" * 64,
                           expires="2026-05-04", item_ref="I9")
    conn.commit()
    handle_email_request(conn, {"id": 1, "type": "email.request", "payload": {"doc_id": a}})
    conn.commit()
    return conn.execute("SELECT id FROM renewal_request").fetchone()["id"]


def test_reminder_drafts_and_rearms(conn):
    """`_make_request` leaves an untouched request draft behind, so the
    reminder rewrites THAT one rather than adding a second unsent mail --
    `reminder_updated`, not `reminder_drafted`. The escalation is still
    stamped: this is the rung, whichever row carries the text.

    (Open question, filed rather than decided here: if the first mail was
    never sent, nobody has been asked yet, so calling the next rung an
    escalation is generous. See `[reminder-escalates-an-unsent-ask]`.)"""
    rid = _make_request(conn)
    out = handle_email_reminder(conn, {"id": 2, "type": "email.reminder",
                                       "payload": {"request_id": rid}})
    assert out["counts"]["reminder_updated"] == 1
    assert conn.execute(
        "SELECT count(*) AS n FROM email_draft WHERE renewal_request_id=%s",
        (rid,)).fetchone()["n"] == 1
    row = conn.execute(
        "SELECT state, escalation_count, last_reminder_at FROM renewal_request WHERE id=%s",
        (rid,)).fetchone()
    assert row["state"] == "awaiting"
    assert row["escalation_count"] == 1
    assert row["last_reminder_at"] is not None


def _reminder_jobs(conn, rid):
    return conn.execute(
        "SELECT id, status, run_after FROM job WHERE type='email.reminder' "
        "AND dedupe_key=%s ORDER BY id", (f"email.reminder:req:{rid}",)).fetchall()


def test_the_reminder_ladder_climbs_through_the_real_queue(conn):
    """The ladder, claimed and dispatched by the runner, not called by hand.

    Every reminder used to stop at its first rung: the re-arm enqueued under
    `email.reminder:req:{id}`, the key of the very job running it, which the
    runner had already committed as `running`, so the active-scope dedupe
    dropped it. Live on 2026-09-11: requests 17-20 each drafted one reminder on
    08-21 and nothing after. A direct handler call cannot see this -- there is
    no running row to collide with -- which is why the test above never did.
    """
    from app.workers import runner

    rid = _make_request(conn)
    handlers = {"email.reminder": handle_email_reminder}
    reminder_after = dt.timedelta(days=14)

    for rung in (1, 2, 3):
        jobs = _reminder_jobs(conn, rid)
        assert [j["status"] for j in jobs] == ["pending"], (rung, jobs)
        conn.execute("UPDATE job SET run_after = now() WHERE id=%s", (jobs[0]["id"],))
        conn.commit()
        # False is `run_once`'s answer for a self-defer ("no work moved
        # forward", so the loop sleeps), not a failure.
        assert runner.run_once(conn, "w", handlers=handlers) is False
        conn.commit()
        row = conn.execute("SELECT state, escalation_count FROM renewal_request "
                           "WHERE id=%s", (rid,)).fetchone()
        assert (row["state"], row["escalation_count"]) == ("awaiting", rung)
        waiting = _reminder_jobs(conn, rid)
        assert [j["status"] for j in waiting] == ["pending"], (rung, waiting)
        now = conn.execute("SELECT now() AS t").fetchone()["t"]
        assert waiting[0]["run_after"] > now + reminder_after - dt.timedelta(hours=1)

    # Rung four is the cap (`escalate_after_reminders = 3`): stop, say so, free the key.
    conn.execute("UPDATE job SET run_after = now() WHERE id=%s", (waiting[0]["id"],))
    conn.commit()
    assert runner.run_once(conn, "w", handlers=handlers) is True
    conn.commit()
    assert conn.execute("SELECT state FROM renewal_request WHERE id=%s",
                        (rid,)).fetchone()["state"] == "escalated"
    assert [j["status"] for j in _reminder_jobs(conn, rid)] == ["done"]


def test_a_second_reminder_regenerates_the_unsent_one_instead_of_stacking(conn):
    """Four requests on the live registry carried TWO unsent reminder drafts
    each, a day apart, because this path inserted unconditionally. Two unsent
    mails for one chase is one mail too many, whichever a person picks."""
    rid = _make_request(conn)
    handle_email_reminder(conn, {"id": 2, "type": "email.reminder",
                                 "payload": {"request_id": rid}})
    conn.execute("UPDATE renewal_request SET last_reminder_at = NULL WHERE id=%s", (rid,))
    conn.commit()
    out = handle_email_reminder(conn, {"id": 3, "type": "email.reminder",
                                       "payload": {"request_id": rid}})

    assert out["counts"]["reminder_updated"] == 1
    assert conn.execute(
        "SELECT count(*) AS n FROM email_draft WHERE renewal_request_id=%s",
        (rid,)).fetchone()["n"] == 1


def test_an_archived_reminder_stops_the_chase_for_that_request(conn):
    """Archive means "we are not sending this". The ladder must not step over
    that decision -- unlike `sent`, which is exactly when escalating is right."""
    rid = _make_request(conn)
    handle_email_reminder(conn, {"id": 2, "type": "email.reminder",
                                 "payload": {"request_id": rid}})
    conn.execute("UPDATE email_draft SET status='cancelled' WHERE renewal_request_id=%s",
                 (rid,))
    conn.execute("UPDATE renewal_request SET last_reminder_at = NULL WHERE id=%s", (rid,))
    conn.commit()

    out = handle_email_reminder(conn, {"id": 3, "type": "email.reminder",
                                       "payload": {"request_id": rid}})
    assert out["counts"]["reminder_archived"] == 1
    assert conn.execute(
        "SELECT count(*) AS n FROM email_draft WHERE renewal_request_id=%s",
        (rid,)).fetchone()["n"] == 1


def test_a_sent_reminder_still_escalates(conn):
    """The ladder exists because a supplier did not answer. A sent reminder is
    the reason to send the next one, not a reason to stop."""
    rid = _make_request(conn)
    handle_email_reminder(conn, {"id": 2, "type": "email.reminder",
                                 "payload": {"request_id": rid}})
    conn.execute("UPDATE email_draft SET status='sent' WHERE renewal_request_id=%s", (rid,))
    conn.execute("UPDATE renewal_request SET last_reminder_at = NULL WHERE id=%s", (rid,))
    conn.commit()

    out = handle_email_reminder(conn, {"id": 3, "type": "email.reminder",
                                       "payload": {"request_id": rid}})
    assert out["counts"]["reminder_drafted"] == 1
    assert conn.execute(
        "SELECT count(*) AS n FROM email_draft WHERE renewal_request_id=%s",
        (rid,)).fetchone()["n"] == 2


def test_reminder_stops_at_the_escalation_cap(conn):
    rid = _make_request(conn)
    conn.execute("UPDATE renewal_request SET escalation_count = 99 WHERE id=%s", (rid,))
    conn.commit()
    out = handle_email_reminder(conn, {"id": 2, "type": "email.reminder",
                                       "payload": {"request_id": rid}})
    assert out["counts"]["escalated"] == 1
    assert conn.execute(
        "SELECT state FROM renewal_request WHERE id=%s", (rid,)).fetchone()["state"] == "escalated"


def test_reminder_closes_when_nothing_is_outstanding(conn):
    rid = _make_request(conn)
    # the chased document is replaced: it stops being production
    conn.execute("UPDATE document SET status='superseded'")
    conn.commit()
    out = handle_email_reminder(conn, {"id": 2, "type": "email.reminder",
                                       "payload": {"request_id": rid}})
    assert out["counts"]["nothing_outstanding"] == 1
    # `received`, NOT `parsed`: the registry moved on, no reply of theirs closed it
    assert conn.execute(
        "SELECT state FROM renewal_request WHERE id=%s", (rid,)).fetchone()["state"] == "received"


def test_reminder_on_a_closed_request_does_nothing(conn):
    rid = _make_request(conn)
    conn.execute("UPDATE renewal_request SET state='parsed' WHERE id=%s", (rid,))
    conn.commit()
    out = handle_email_reminder(conn, {"id": 2, "type": "email.reminder",
                                       "payload": {"request_id": rid}})
    assert out["counts"]["already_closed"] == 1


def test_reminder_for_a_missing_request_is_not_a_crash(conn):
    out = handle_email_reminder(conn, {"id": 2, "type": "email.reminder",
                                       "payload": {"request_id": 999999}})
    assert out["counts"]["request_missing"] == 1


# --------------------------------------------------------------------------- #
# the copy (spec §7.1 / §7.3)
# --------------------------------------------------------------------------- #
def _group(items, expires, cert="CERT-1", type_="DoC", regulation="MDR"):
    return {"type": type_, "regulation": regulation, "cert_number": cert,
            "expires": expires, "doc_ids": [1], "documents": 1, "items": items}


def test_both_kinds_carry_RE_in_the_subject():
    _, s1, _ = compose("IVOCLAR", [_group(3, dt.date(2027, 1, 1))], TODAY)
    _, s2, _ = compose("IVOCLAR", [_group(3, dt.date(2020, 1, 1))], TODAY)
    assert s1 == s2 == "RE: MDR DOCUMENTS"


def test_anything_lapsed_makes_it_a_reminder():
    kind, _, _ = compose("IVOCLAR", [_group(3, dt.date(2020, 1, 1)),
                                     _group(3, dt.date(2027, 1, 1))], TODAY)
    assert kind == "reminder"


def test_nothing_lapsed_makes_it_a_request():
    kind, _, body = compose("IVOCLAR", [_group(3, dt.date(2027, 1, 1))], TODAY)
    assert kind == "request"
    assert "EACH ordered good" in body


def test_the_anchor_is_the_costliest_lapse_not_the_oldest():
    """A 2020 declaration covering 2 items is not the sentence to open with when
    a 2026 certificate has taken 1.046 items with it."""
    _, _, body = compose("IVOCLAR", [
        _group(2, dt.date(2020, 7, 3), cert="OLD-SMALL"),
        _group(1046, dt.date(2026, 5, 4), cert="G15 043306 0282"),
    ], TODAY)
    lead = body.split("Outstanding")[0]
    assert "G15 043306 0282" in lead
    assert "OLD-SMALL" not in lead


def test_generated_copy_does_not_reproduce_the_clients_typos():
    _, _, body = compose("IVOCLAR", [_group(3, dt.date(2020, 1, 1))], TODAY)
    for typo in ("becuase", "Would you ve"):
        assert typo not in body


def test_prose_wraps_but_the_document_list_does_not():
    _, _, body = compose("IVOCLAR", [
        _group(1046, dt.date(2026, 5, 4), cert="G15 043306 0282 Rev. 00")], TODAY)
    assert all(len(l) <= 80 for l in body.splitlines())
    assert "- MDR declaration of conformity, G15 043306 0282 Rev. 00" in body


# --------------------------------------------------------------------------- #
# Task 8 — EUDAMED drift cited in the renewal mail
# --------------------------------------------------------------------------- #
def test_a_drift_row_is_cited_in_the_renewal_mail():
    """The mail already says "these are expiring, please send renewals". With
    drift it can say which renewal exists and when it was issued -- a much
    harder mail to ignore. Measured live: Ivoclar's G15 043306 0282 is at
    Rev. 02, issued 2026-06-12, valid to 2031-05-04, while 96 of our
    declarations cite Rev. 00."""
    drift = [{
        "certificate_number": "G15 043306 0282",
        "our_revision": "Rev. 00",
        "eudamed_revision": "Rev. 02",
        "eudamed_issue_date": dt.date(2026, 6, 12),
        "eudamed_expiry_date": dt.date(2031, 5, 4),
        "notified_body_srn": "0123",
    }]

    _, _, body = compose("IVOCLAR", [], dt.date(2026, 8, 26), drift=drift)

    assert "G15 043306 0282" in body
    assert "Rev. 02" in body
    assert "2026-06-12" in body


def test_the_mail_is_unchanged_when_there_is_no_drift():
    """Additive only. Every existing caller passes no drift and must get
    byte-identical copy."""
    a = compose("IVOCLAR", [], dt.date(2026, 8, 26))
    b = compose("IVOCLAR", [], dt.date(2026, 8, 26), drift=())
    assert a == b


def test_drift_never_asserts_the_revision_supersedes_ours():
    """The mail reports what EUDAMED shows. It does not tell the supplier their
    old revision is void -- that is the assertion the 2026-08-19 ruling
    refused, and it would be wrong in a letter as well as in a column.

    Fix round 1: the first wording ("EUDAMED currently lists a NEWER revision
    ... please send the CURRENT revision") made exactly this assertion in
    softer words -- "newer"/"current" are a temporal-ordering claim between
    `our_revision` (parsed off our own document's suffix) and
    `eudamed_revision` (EUDAMED's own field), and nothing establishes the two
    are on a comparable ordering. This test bans that whole vocabulary, not
    just the three original words, and positively asserts the copy reports a
    DIFFERENCE rather than an ordering -- written so the wording that was just
    removed would fail it."""
    drift = [{
        "certificate_number": "G15 043306 0282",
        "our_revision": "Rev. 00",
        "eudamed_revision": "Rev. 02",
        "eudamed_issue_date": dt.date(2026, 6, 12),
        "eudamed_expiry_date": dt.date(2031, 5, 4),
        "notified_body_srn": "0123",
    }]

    _, _, body = compose("IVOCLAR", [], dt.date(2026, 8, 26), drift=drift)

    lowered = body.lower()
    # Whitespace-normalised: prose gets wrapped to 78 columns, so a phrase
    # spanning a wrap point ("...at a\ndifferent\nrevision...") must not read
    # as absent just because textwrap put a newline where a space was.
    flat = " ".join(lowered.split())
    forbidden = ("supersede", "no longer valid", "void", "newer", "latest",
                 "current revision", "out of date", "outdated", "replaced",
                 "obsolete")
    for word in forbidden:
        assert word not in flat, f"drift copy ranks the revisions via {word!r}"
    # Positive: it reports the revisions DIFFER, never which is later.
    assert "different revision" in flat


def test_several_eudamed_revisions_for_one_certificate_are_one_bullet():
    """`compose` must collapse rows the same way regardless of where they came
    from -- this exercises the render directly with two revision rows for one
    certificate (the `HZ 1594091-1` shape: Rev. 1 and Rev. 2 both live)."""
    drift = [
        {"certificate_number": "HZ 1594091-1", "our_revision": "Rev. 00",
         "eudamed_revision": "Rev. 1", "eudamed_issue_date": dt.date(2021, 6, 29),
         "eudamed_expiry_date": dt.date(2026, 6, 29), "notified_body_srn": None},
        {"certificate_number": "HZ 1594091-1", "our_revision": "Rev. 00",
         "eudamed_revision": "Rev. 2", "eudamed_issue_date": dt.date(2026, 6, 12),
         "eudamed_expiry_date": dt.date(2031, 6, 29), "notified_body_srn": None},
    ]

    _, _, body = compose("CARL MARTIN", [], dt.date(2026, 8, 26), drift=drift)

    bullets = [l for l in body.splitlines() if "HZ 1594091-1" in l]
    assert len(bullets) == 1
    assert "Rev. 1" in bullets[0] and "Rev. 2" in bullets[0]


def test_several_held_revisions_for_one_certificate_are_named_in_one_bullet():
    """Fix round 2, minor #6, reviewer-reproduced: `drift_for_manufacturer`
    returns one row per (held revision, EUDAMED revision) pair -- design.md
    S1.6's `G15 043306 0282` is genuinely held at both Rev. 00 (96 documents)
    and Rev. 01 (1 document), both against EUDAMED's single Rev. 02. Naming
    only the FIRST row's `our_revision` would silently drop Rev. 01 and repeat
    Rev. 02 once per held row instead of once."""
    drift = [
        {"certificate_number": "G15 043306 0282", "our_revision": "Rev. 00",
         "eudamed_revision": "Rev. 02", "eudamed_issue_date": dt.date(2026, 6, 12),
         "eudamed_expiry_date": dt.date(2031, 5, 4), "notified_body_srn": "0123"},
        {"certificate_number": "G15 043306 0282", "our_revision": "Rev. 01",
         "eudamed_revision": "Rev. 02", "eudamed_issue_date": dt.date(2026, 6, 12),
         "eudamed_expiry_date": dt.date(2031, 5, 4), "notified_body_srn": "0123"},
    ]

    _, _, body = compose("IVOCLAR", [], dt.date(2026, 8, 26), drift=drift)

    bullets = [l for l in body.splitlines() if "G15 043306 0282" in l]
    assert len(bullets) == 1
    assert "Rev. 00" in bullets[0] and "Rev. 01" in bullets[0]
    # The single EUDAMED revision is named once, not once per held row.
    assert bullets[0].count("Rev. 02") == 1


def test_drift_paragraph_has_no_triple_blank_line_with_empty_groups():
    """Minor, fix round 1: with drift present and `groups` empty (the exact
    shape the brief's own tests use), splicing must not compound the blank
    line an empty doc_list already leaves behind into three."""
    drift = [{
        "certificate_number": "G15 043306 0282",
        "our_revision": "Rev. 00",
        "eudamed_revision": "Rev. 02",
        "eudamed_issue_date": dt.date(2026, 6, 12),
        "eudamed_expiry_date": dt.date(2031, 5, 4),
        "notified_body_srn": "0123",
    }]

    _, _, body = compose("IVOCLAR", [], dt.date(2026, 8, 26), drift=drift)

    assert "\n\n\n" not in body


def test_drift_for_manufacturer_reads_the_certificate_drift_view(conn, seeded_doc):
    """`drift_for_manufacturer` is the reader this task adds: a thin SELECT
    over Task 6's `certificate_drift` view, filtered to one manufacturer --
    exercised through the real view rather than a hand-built dict, since a
    column rename or type change in migration 043 should fail here, not only
    silently drop a field from the mail."""
    seeded_doc(type="EC", cert_number="G15 043306 0282 Rev. 00", canonical="DRIFTCO")
    conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES ('DRIFTCO') "
        "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, status) "
        "VALUES ('DRIFTCO', 'LI-MF-000000522', 'register-exact', 'auto')")
    conn.execute(
        "INSERT INTO eudamed_certificate (certificate_number, revision_number, "
        "  actor_srn, actor_name, certificate_status, issue_date, expiry_date, "
        "  notified_body_srn, synced_at) "
        "VALUES ('G15 043306 0282', 'Rev. 02', 'LI-MF-000000522', 'Ivoclar', "
        "  'supplemented', '2026-06-12', '2031-05-04', '0123', now())")
    conn.commit()

    rows = drift_for_manufacturer(conn, "DRIFTCO")

    assert len(rows) == 1
    row = rows[0]
    assert row["certificate_number"] == "G15 043306 0282"
    assert row["our_revision"] == "Rev. 00"
    assert row["eudamed_revision"] == "Rev. 02"
    assert row["eudamed_issue_date"] == dt.date(2026, 6, 12)
    assert row["eudamed_expiry_date"] == dt.date(2031, 5, 4)
    assert row["notified_body_srn"] == "0123"


def test_drift_for_manufacturer_is_empty_for_an_unrelated_manufacturer(conn, seeded_doc):
    """Filtered per manufacturer, not global -- a drift row for IVOCLAR must
    never leak into GC's mail."""
    seeded_doc(type="EC", cert_number="HZ 1594091-1 Rev. 1", canonical="OTHERCO")
    conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES ('OTHERCO') "
        "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, status) "
        "VALUES ('OTHERCO', 'DE-MF-000005066', 'register-exact', 'auto')")
    conn.execute(
        "INSERT INTO eudamed_certificate (certificate_number, revision_number, "
        "  actor_srn, synced_at) "
        "VALUES ('HZ 1594091-1', 'Rev. 2', 'DE-MF-000005066', now())")
    conn.commit()

    assert drift_for_manufacturer(conn, "NOBODY-HOME") == []


def test_handle_email_request_counts_and_cites_drift_rows(conn, seeded_doc):
    """End to end: the handler fetches drift for the manufacturer it is
    drafting for, counts it on the result (never silently), and the persisted
    draft body carries the citation. The expiring document is a declaration
    (as in production); the drift row describes the EC certificate it quotes
    -- Task 8 note 2, deliberately not joined. The EC certificate itself
    (seeded via `seeded_doc`, separately from the expiring DoC) is what makes
    `held_certificate` see it at all -- that view is EC/ISO only."""
    gid = _seed_manufacturer(conn, contacts=["quality@ivoclar.example"])
    _seed_expiring_doc(conn, gid, content_hash="9" * 64, expires="2026-05-04",
                       item_ref="I-DRIFT", cert_number="G15 043306 0282 Rev. 00")
    seeded_doc(type="EC", cert_number="G15 043306 0282 Rev. 00", canonical="IVOCLAR")
    conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES ('IVOCLAR') "
        "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, status) "
        "VALUES ('IVOCLAR', 'LI-MF-000000522', 'register-exact', 'auto')")
    conn.execute(
        "INSERT INTO eudamed_certificate (certificate_number, revision_number, "
        "  actor_srn, certificate_status, issue_date, expiry_date, "
        "  notified_body_srn, synced_at) "
        "VALUES ('G15 043306 0282', 'Rev. 02', 'LI-MF-000000522', 'supplemented', "
        "  '2026-06-12', '2031-05-04', '0123', now())")
    conn.commit()

    out = handle_email_request(conn, {"id": 1, "type": "email.request",
                                      "payload": {"manufacturer": "IVOCLAR"}})

    assert out["counts"]["drift_rows"] == 1
    body = conn.execute("SELECT body FROM email_draft").fetchone()["body"]
    assert "G15 043306 0282" in body
    assert "Rev. 02" in body


def test_two_seeded_eudamed_revisions_for_one_certificate_render_one_bullet(
        conn, seeded_doc):
    """Fix round 1, important #1, reviewer-reproduced: migration 042 keys
    `eudamed_certificate` on `(certificate_number, revision_number, actor_srn)`
    by design -- Carl Martin's `HZ 1594091-1` is genuinely two live rows,
    Rev. 1 expiring 2026-06-29 and Rev. 2 running to 2031-06-29. Seeding both
    through `certificate_drift` (not a hand-built dict) proves `SELECT
    DISTINCT` in `drift_for_manufacturer` does NOT collapse them -- it returns
    two rows, as it must, because `eudamed_revision` is in the projected
    tuple -- and that `compose` still renders exactly one bullet naming both
    revisions rather than two bullets that read as contradicting each other."""
    seeded_doc(type="EC", cert_number="HZ 1594091-1 Rev. 00", canonical="CARLM")
    conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES ('CARLM') "
        "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, status) "
        "VALUES ('CARLM', 'DE-MF-000005066', 'register-exact', 'auto')")
    for rev, issue, expiry in (("Rev. 1", "2021-06-29", "2026-06-29"),
                               ("Rev. 2", "2026-06-12", "2031-06-29")):
        conn.execute(
            "INSERT INTO eudamed_certificate (certificate_number, revision_number, "
            "  actor_srn, issue_date, expiry_date, synced_at) "
            "VALUES ('HZ 1594091-1', %s, 'DE-MF-000005066', %s, %s, now())",
            (rev, issue, expiry))
    conn.commit()

    rows = drift_for_manufacturer(conn, "CARLM")
    assert len(rows) == 2   # the raw reader stays ungrouped, one row per revision

    _, _, body = compose("CARLM", [], dt.date(2026, 8, 26), drift=rows)
    bullets = [l for l in body.splitlines() if "HZ 1594091-1" in l]
    assert len(bullets) == 1
    assert "Rev. 1" in bullets[0] and "Rev. 2" in bullets[0]


def test_handle_email_reminder_also_counts_and_cites_drift_rows(conn, seeded_doc):
    """Fix round 1, important #2: the reminder is the mail that actually goes
    out once a supplier has not responded, so it must carry the same citation
    `handle_email_request` does -- a `{drift}` slot nothing ever fills is
    worse than no slot at all."""
    gid = _seed_manufacturer(conn, contacts=["quality@ivoclar.example"])
    a = _seed_expiring_doc(conn, gid, content_hash="8" * 64, expires="2026-05-04",
                           item_ref="I-DRIFT-REMIND", cert_number="G15 043306 0282 Rev. 00")
    seeded_doc(type="EC", cert_number="G15 043306 0282 Rev. 00", canonical="IVOCLAR")
    conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES ('IVOCLAR') "
        "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, status) "
        "VALUES ('IVOCLAR', 'LI-MF-000000522', 'register-exact', 'auto')")
    conn.execute(
        "INSERT INTO eudamed_certificate (certificate_number, revision_number, "
        "  actor_srn, certificate_status, issue_date, expiry_date, "
        "  notified_body_srn, synced_at) "
        "VALUES ('G15 043306 0282', 'Rev. 02', 'LI-MF-000000522', 'supplemented', "
        "  '2026-06-12', '2031-05-04', '0123', now())")
    conn.commit()
    handle_email_request(conn, {"id": 1, "type": "email.request", "payload": {"doc_id": a}})
    conn.commit()
    request_id = conn.execute("SELECT id FROM renewal_request").fetchone()["id"]

    out = handle_email_reminder(conn, {"id": 2, "type": "email.reminder",
                                       "payload": {"request_id": request_id}})

    assert out["counts"]["drift_rows"] == 1
    # Two `email_draft` rows can legitimately exist here: the first, from
    # `handle_email_request`, already has kind='reminder' because the
    # document is already lapsed by TODAY; the second is the one
    # `handle_email_reminder` itself just inserted. Take the latest.
    body = conn.execute(
        "SELECT body FROM email_draft ORDER BY id DESC LIMIT 1").fetchone()["body"]
    assert "G15 043306 0282" in body
    assert "Rev. 02" in body


# --------------------------------------------------------------------------- #
# gap-request: the first ask, for a manufacturer whose problem is that we hold
# NOTHING.
#
# `lapsing_for_manufacturer` reads documents we already have. CARL MARTIN has
# none -- measured 2026-09-02 after the EUDAMED sweep, 2.389 articles with no
# declaration across 70 Basic UDI-DI groups -- so the renewal path returns
# `nothing_expiring` and drafts nothing. That is the largest single gap in the
# registry and it had no path to a mail.
# --------------------------------------------------------------------------- #

def _seed_gap(conn, *, name="CARL MARTIN", srn="DE-MF-000005066",
              basic_udi_di="++ECMSBUDI0166Z", item_refs=("A1", "A2", "A3", "A4"),
              trade_name=None):
    """One manufacturer, one EUDAMED group, N of our articles inside it and no
    document anywhere. Seeds the tables `eudamed_article_status` is built on
    rather than the view, so the test exercises the real join."""
    gid = _seed_manufacturer(conn, name)
    conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES (%s) "
        "ON CONFLICT (canonical_name) DO NOTHING", (name,))
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, status) "
        "VALUES (%s,%s,'article-probe','confirmed') ON CONFLICT DO NOTHING", (name, srn))
    for i, ref in enumerate(item_refs):
        conn.execute(
            "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, "
            "  md_flag, catalogue, updated_at) "
            "VALUES (%s, %s, %s, TRUE, 'LJ', now()) "
            "ON CONFLICT (item_ref) DO NOTHING", (ref, f"item {ref}", name))
        conn.execute(
            "INSERT INTO item_group_member (group_id, item_ref, match_basis) "
            "VALUES (%s,%s,'manual')", (gid, ref))
        conn.execute(
            "INSERT INTO eudamed_mirror (udi_di, basic_udi_di, manufacturer_srn, "
            "  reference, trade_name, synced_at) "
            "VALUES (%s,%s,%s,%s,%s, now())",
            (f"UDI{i}{ref}", basic_udi_di, srn, ref, trade_name))
    return gid


def test_gap_groups_are_read_with_example_article_numbers(conn):
    _seed_gap(conn)

    rows = gap_groups_for_manufacturer(conn, "CARL MARTIN")

    assert len(rows) == 1
    assert rows[0]["basic_udi_di"] == "++ECMSBUDI0166Z"
    assert rows[0]["articles"] == 4
    # Three examples, not all four: the mail names enough to be concrete
    # without reprinting the catalogue.
    assert rows[0]["examples"] == ["A1", "A2", "A3"]


def test_a_manufacturer_with_no_gap_reads_empty(conn):
    _seed_manufacturer(conn, "IVOCLAR")
    assert gap_groups_for_manufacturer(conn, "IVOCLAR") == []


def test_the_gap_draft_lists_every_group_in_one_mail(conn):
    """One draft per manufacturer, not per group -- 70 groups is one email a
    person sends, not 70 items on the drafts board."""
    _seed_gap(conn, basic_udi_di="++G1", item_refs=("A1", "A2"))
    _seed_gap(conn, basic_udi_di="++G2", item_refs=("B1",))

    rows = gap_groups_for_manufacturer(conn, "CARL MARTIN")
    kind, subject, body = compose_gap("CARL MARTIN", rows)

    assert kind == "gap-request"
    assert "++G1" in body and "++G2" in body
    # Ordered by size, so the costliest ask leads.
    assert body.index("++G1") < body.index("++G2")
    assert "A1" in body


def test_the_gap_draft_carries_its_manufacturer(conn):
    """Migration 057: a gap request has no `renewal_request` to borrow a name
    from, so /drafts rendered an empty Manufacturer cell and nothing could ask
    "have we already written to these people"."""
    _seed_gap(conn)
    handle_email_request(conn, {"payload": {"manufacturer": "CARL MARTIN",
                                            "reason": "eudamed-gap"}})
    row = conn.execute("SELECT manufacturer FROM email_draft").fetchone()
    assert row["manufacturer"] == "CARL MARTIN"


@pytest.mark.parametrize("settled", ["draft", "ready", "sent", "cancelled"])
def test_a_gap_request_is_only_ever_asked_once(conn, settled):
    """Draft 25 was archived on 2026-09-02 and draft 26 was written anyway:
    the button's dedupe key is scoped to ACTIVE jobs, so once the job finished,
    pressing again produced a second letter. Denis, 2026-09-03: archived or
    sent means do not offer another one."""
    _seed_gap(conn)
    handle_email_request(conn, {"payload": {"manufacturer": "CARL MARTIN",
                                            "reason": "eudamed-gap"}})
    conn.execute("UPDATE email_draft SET status=%s", (settled,))
    conn.commit()

    out = handle_email_request(conn, {"payload": {"manufacturer": "CARL MARTIN",
                                                  "reason": "eudamed-gap"}})
    assert out["counts"]["gap_already_asked"] == 1
    assert conn.execute("SELECT count(*) AS n FROM email_draft").fetchone()["n"] == 1


def test_a_gap_request_for_a_different_manufacturer_is_not_blocked(conn):
    """The guard is per manufacturer, not global -- one archived letter must
    not silence every other supplier."""
    _seed_gap(conn)
    _seed_gap(conn, name="IVOCLAR", srn="DE-MF-000000001",
              basic_udi_di="++IVO1", item_refs=("V1",))
    handle_email_request(conn, {"payload": {"manufacturer": "CARL MARTIN",
                                            "reason": "eudamed-gap"}})
    conn.execute("UPDATE email_draft SET status='cancelled'")
    conn.commit()

    out = handle_email_request(conn, {"payload": {"manufacturer": "IVOCLAR",
                                                  "reason": "eudamed-gap"}})
    assert out["counts"]["gap_drafted"] == 1
    assert conn.execute("SELECT count(*) AS n FROM email_draft").fetchone()["n"] == 2


def test_the_gap_handler_writes_one_draft_and_no_renewal_request(conn):
    _seed_gap(conn)

    out = handle_email_request(
        conn, {"payload": {"manufacturer": "CARL MARTIN",
                           "reason": "eudamed-gap"}})

    draft = conn.execute(
        "SELECT kind, renewal_request_id, body FROM email_draft").fetchall()
    assert len(draft) == 1
    assert draft[0]["kind"] == "gap-request"
    # A gap request is not a renewal: it has no document to renew and must not
    # take a slot in the renewal cadence guard.
    assert draft[0]["renewal_request_id"] is None
    assert "++ECMSBUDI0166Z" in draft[0]["body"]
    assert out["counts"].get("gap_groups") == 1


def test_the_gap_handler_drafts_nothing_when_there_is_no_gap(conn):
    _seed_manufacturer(conn, "IVOCLAR")

    out = handle_email_request(
        conn, {"payload": {"manufacturer": "IVOCLAR", "reason": "eudamed-gap"}})

    assert conn.execute("SELECT count(*) AS n FROM email_draft").fetchone()["n"] == 0
    assert out["counts"].get("no_gap") == 1


def test_the_renewal_path_is_untouched_by_the_gap_branch(conn):
    """The existing behaviour must be byte-identical: a manufacturer with
    expiring documents and no `reason` still gets the renewal draft."""
    gid = _seed_manufacturer(conn, "IVOCLAR", contacts=["a@b.si"])
    _seed_expiring_doc(conn, gid, content_hash="h1",
                       expires=TODAY + dt.timedelta(days=10), item_ref="I1")

    out = handle_email_request(
        conn, {"payload": {"manufacturer": "IVOCLAR"}})

    row = conn.execute("SELECT kind FROM email_draft").fetchone()
    assert row["kind"] in ("request", "reminder")
    assert out["counts"].get("gap_groups") is None


# --------------------------------------------------------------------------- #
# Wrapping must not reflow a block that was authored as separate lines.
#
# `_wrap` exists to fix ONE thing: a substituted sentence naming a certificate
# and an item count runs to 190 columns. It re-joined every paragraph without a
# list marker, which silently ate the newline in the sign-off --
#
#     Nataša Palme
#     Trženje in prodaja | Marketing and sales
#
# -- and shipped it as one line in every request and reminder, not just the gap
# draft where it was noticed (live draft 25, 2026-09-02).
# --------------------------------------------------------------------------- #

def test_the_signature_keeps_its_line_break(conn):
    _seed_gap(conn)
    _, _, body = compose_gap("CARL MARTIN", gap_groups_for_manufacturer(
        conn, "CARL MARTIN"))
    assert "Nataša Palme\nTrženje in prodaja" in body


def test_the_renewal_mail_signature_keeps_its_line_break(conn):
    """The same defect, in the path that has been sending it for longer."""
    gid = _seed_manufacturer(conn, "IVOCLAR")
    _seed_expiring_doc(conn, gid, content_hash="sig1",
                       expires=TODAY + dt.timedelta(days=10), item_ref="S1")
    groups = lapsing_for_manufacturer(conn, "IVOCLAR", horizon_days=90,
                                      today=TODAY)
    _, _, body = compose("IVOCLAR", groups, TODAY)
    assert "Nataša Palme\nTrženje in prodaja" in body


def test_a_long_substituted_sentence_is_still_wrapped(conn):
    """The reason `_wrap` exists must survive the fix: a paragraph carrying an
    over-long line is still reflowed."""
    from app.handlers.email_request import _wrap

    long_line = "word " * 60
    assert max(len(l) for l in _wrap(long_line).split("\n")) <= 78


def test_one_article_is_not_one_articles(conn):
    _seed_gap(conn, basic_udi_di="++SOLO", item_refs=("Z1",))
    _, _, body = compose_gap("CARL MARTIN", gap_groups_for_manufacturer(
        conn, "CARL MARTIN"))
    assert "(1 article)" in body
    assert "1 articles" not in body


# --------------------------------------------------------------------------- #
# The request reference in the subject ([email-reply-matching], 2026-09-11).
# The system never sends (ruling 2026-08-20), so it never learns the Message-ID
# a reply would cite; a token the supplier's reply keeps in its subject is the
# one handle `email.poll` can match on.
# --------------------------------------------------------------------------- #
def _subject(conn, rid):
    return conn.execute(
        "SELECT subject FROM email_draft WHERE renewal_request_id=%s "
        "ORDER BY id DESC LIMIT 1", (rid,)).fetchone()["subject"]


def test_the_request_draft_carries_its_reference(conn):
    rid = _make_request(conn)

    assert _subject(conn, rid) == f"RE: MDR DOCUMENTS [DENT-{rid}]"


def test_the_reminder_keeps_the_same_reference(conn):
    rid = _make_request(conn)
    handle_email_reminder(conn, {"id": 2, "type": "email.reminder",
                                 "payload": {"request_id": rid}})

    assert _subject(conn, rid) == f"RE: MDR DOCUMENTS [DENT-{rid}]"
