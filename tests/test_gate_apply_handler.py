"""gate.apply — human decisions (handbook §8). The other registry writer (inv. 1).

approve: promote doc + pending trusted-basis links (name-family stays staged, C5),
apply edits as T3 evidence (verbatim=human input, conf=1.0), resolve the task.
reject: mark rejected, enqueue an interactive discover.group retry, resolve task.
bind-manufacturer (C4 §7b): promote + derive mfr-scope links to all MD items.
All audited. Tests don't commit (conn fixture rolls back).
"""

from __future__ import annotations

import pytest

from app.handlers import gate as gh


def _seed_staged_doc(conn, content_hash, archive="file:///a.pdf"):
    return conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, archive_url, status) "
        "VALUES ('DoC','MDR','group',%s,%s,'staged') RETURNING doc_id",
        (content_hash, archive),
    ).fetchone()["doc_id"]


def _seed_item(conn, item_ref, manufacturer="ACME", md_flag=True):
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, catalogue, mirror_rev, updated_at) "
        "VALUES (%s,'n',%s,%s,'LJ',1,now()) ON CONFLICT (item_ref) DO NOTHING",
        (item_ref, manufacturer, md_flag),
    )


def _seed_link(conn, item_ref, doc_id, basis, status="staged", manufacturer="ACME"):
    _seed_item(conn, item_ref, manufacturer)
    conn.execute(
        "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
        "VALUES (%s,%s,%s,%s::link_status)", (item_ref, doc_id, basis, status))


def _seed_manual(conn, doc_id):
    conn.execute(
        "INSERT INTO manual_task (kind, doc_id, payload) VALUES ('gate-manual', %s, '{}')", (doc_id,))


def _apply(doc_id, decision, decided_by="user:marta", jid=9, **extra):
    payload = {"doc_id": doc_id, "decision": decision, "decided_by": decided_by}
    payload.update(extra)
    return {"id": jid, "type": "gate.apply", "payload": payload,
            "dedupe_key": f"apply:{doc_id}:{decision}"}


def _status(conn, doc_id):
    return conn.execute("SELECT status FROM document WHERE doc_id=%s", (doc_id,)).fetchone()["status"]


def _link_status(conn, doc_id, item_ref):
    r = conn.execute("SELECT status FROM item_document WHERE doc_id=%s AND item_ref=%s",
                     (doc_id, item_ref)).fetchone()
    return r["status"] if r else None


def test_approve_promotes_doc_trusted_links_and_applies_edits(conn):
    doc_id = _seed_staged_doc(conn, "ga-appr")
    _seed_link(conn, "IT-1", doc_id, "ref-list", "staged")
    _seed_link(conn, "IT-2", doc_id, "name-family", "staged")
    _seed_manual(conn, doc_id)

    gh.handle_gate_apply(conn, _apply(doc_id, "approve", edits={"validity_to": "2029-03-14"}))

    assert _status(conn, doc_id) == "production"
    assert _link_status(conn, doc_id, "IT-1") == "production"   # trusted basis follows the doc
    assert _link_status(conn, doc_id, "IT-2") == "staged"       # name-family never auto-promotes (C5)

    doc = conn.execute("SELECT validity_to FROM document WHERE doc_id=%s", (doc_id,)).fetchone()
    assert str(doc["validity_to"]) == "2029-03-14"              # edit applied to the column
    ev = conn.execute(
        "SELECT tier, confidence, verbatim FROM evidence WHERE doc_id=%s AND field='validity_to'",
        (doc_id,)).fetchone()
    assert ev["tier"] == "T3" and float(ev["confidence"]) == 1.0 and ev["verbatim"] == "2029-03-14"

    mt = conn.execute("SELECT status, resolved_by FROM manual_task WHERE doc_id=%s", (doc_id,)).fetchone()
    assert mt["status"] == "resolved" and mt["resolved_by"] == "user:marta"
    events = {r["event"] for r in conn.execute(
        "SELECT event FROM audit_log WHERE doc_id=%s", (doc_id,)).fetchall()}
    assert events == {"approve", "production-write"}   # §9: every production write audited


def test_reject_marks_rejected_and_enqueues_interactive_discover(conn):
    doc_id = _seed_staged_doc(conn, "ga-rej")
    _seed_manual(conn, doc_id)

    gh.handle_gate_apply(conn, _apply(doc_id, "reject", decided_by="user:x", group_id=55))

    assert _status(conn, doc_id) == "rejected"
    j = conn.execute(
        "SELECT payload, priority FROM job WHERE type='discover.group'").fetchone()
    assert j["payload"]["group_id"] == 55
    assert j["priority"] == "interactive"
    assert conn.execute("SELECT status FROM manual_task WHERE doc_id=%s", (doc_id,)).fetchone()["status"] == "resolved"
    assert conn.execute("SELECT event FROM audit_log WHERE doc_id=%s", (doc_id,)).fetchone()["event"] == "reject"


def test_bind_manufacturer_derives_mfr_scope_links(conn):
    doc_id = _seed_staged_doc(conn, "ga-bind")
    _seed_manual(conn, doc_id)
    _seed_item(conn, "M-1", "ACME", md_flag=True)
    _seed_item(conn, "M-2", "ACME", md_flag=True)
    _seed_item(conn, "M-3", "ACME", md_flag=False)   # not an MD item -> excluded
    _seed_item(conn, "M-9", "OTHER", md_flag=True)   # different manufacturer -> excluded

    gh.handle_gate_apply(conn, _apply(doc_id, "bind-manufacturer", manufacturer="ACME"))

    assert _status(conn, doc_id) == "production"
    linked = {r["item_ref"]: (r["match_basis"], r["status"]) for r in conn.execute(
        "SELECT item_ref, match_basis, status FROM item_document WHERE doc_id=%s", (doc_id,)).fetchall()}
    assert linked["M-1"] == ("mfr-scope", "production")
    assert linked["M-2"] == ("mfr-scope", "production")
    assert "M-3" not in linked and "M-9" not in linked
    events = {r["event"] for r in conn.execute(
        "SELECT event FROM audit_log WHERE doc_id=%s", (doc_id,)).fetchall()}
    assert events == {"bind-manufacturer", "production-write"}
    assert conn.execute("SELECT status FROM manual_task WHERE doc_id=%s", (doc_id,)).fetchone()["status"] == "resolved"


def test_bind_manufacturer_refuses_a_name_that_links_nothing(conn):
    """[gate-bind-zero-links]. `_derive_mfr_scope_links` used to return silently
    when its name resolved to no BC codes, so the apply promoted the document to
    production, linked zero items, and wrote bind-manufacturer + production-write
    audit entries that read exactly like a success.

    The name here is UNATTRIBUTABLE -- it resolves to no BC codes at all. That
    is the whole of what this guard covers, and the docstring said otherwise
    until 2026-08-31: a name that resolves fine but whose items carry no MD flag
    never reached it (`3SHAPE TRIOS A/S` -> ['10004','10005']), and since
    migration 053 that case is a legitimate binding rather than a fault.

    C16's machine path cannot reach this (it counts items before choosing
    production over `filed`), and the picker no longer narrows it either -- it
    offers every catalogue entity now -- but `POST /staging/{doc_id}/apply`
    validates only that the string is non-empty, so a hand-built request, a
    replayed job or a future producer still walks straight in.

    Raise, do not return: a job that fails is visible, a bind that links nothing
    is not. The handler runs inside `with conn.transaction()`, so the promote and
    both audit rows roll back with it -- asserted below, because a raise that
    left the document on production would be the worse bug."""
    doc_id = _seed_staged_doc(conn, "ga-bind-zero")
    _seed_manual(conn, doc_id)
    _seed_item(conn, "MZ-1", "SOMEONE-ELSE", md_flag=True)

    with pytest.raises(ValueError, match="no BC"):
        with conn.transaction():
            gh.handle_gate_apply(
                conn, _apply(doc_id, "bind-manufacturer", manufacturer="NOBODY-HOLDS-THIS"))

    assert _status(conn, doc_id) == "staged", "the promote must roll back"
    assert conn.execute(
        "SELECT count(*) AS n FROM item_document WHERE doc_id=%s", (doc_id,)
    ).fetchone()["n"] == 0
    assert conn.execute(
        "SELECT count(*) AS n FROM audit_log WHERE doc_id=%s", (doc_id,)
    ).fetchone()["n"] == 0, "no audit entry may claim a bind that did not happen"


def test_reject_cascades_link_statuses_with_audit(conn):
    # §7: un-bind = reject + link cascade. Links must not stay production/staged
    # under a rejected doc; each cascaded link gets its own audit row.
    doc_id = _seed_staged_doc(conn, "ga-rej-casc")
    _seed_link(conn, "RC-1", doc_id, "ref-list", "production")
    _seed_link(conn, "RC-2", doc_id, "name-family", "staged")

    gh.handle_gate_apply(conn, _apply(doc_id, "reject"))

    assert _status(conn, doc_id) == "rejected"
    assert _link_status(conn, doc_id, "RC-1") == "rejected"
    assert _link_status(conn, doc_id, "RC-2") == "rejected"
    cascaded = {r["item_ref"] for r in conn.execute(
        "SELECT item_ref FROM audit_log WHERE doc_id=%s AND event='link-rejected'",
        (doc_id,)).fetchall()}
    assert cascaded == {"RC-1", "RC-2"}


def test_approve_of_already_production_doc_emits_no_second_production_write(conn):
    # idempotent under at-least-once delivery: promotion audit fires only on the
    # staged->production transition, mirroring gate.candidate's prev_status check.
    doc_id = _seed_staged_doc(conn, "ga-idem-pw")
    gh.handle_gate_apply(conn, _apply(doc_id, "approve", jid=11))
    gh.handle_gate_apply(conn, _apply(doc_id, "approve", jid=12))   # re-delivery
    n = conn.execute(
        "SELECT count(*) c FROM audit_log WHERE doc_id=%s AND event='production-write'",
        (doc_id,)).fetchone()["c"]
    assert n == 1


def test_bind_manufacturer_reaches_items_via_alias(conn):
    # [gate-binding]: the payload carries the CANONICAL manufacturer; catalogue
    # rows spell it raw. The alias table (populated by RESOLVE) bridges the two.
    doc_id = _seed_staged_doc(conn, "ga-alias")
    _seed_manual(conn, doc_id)
    _seed_item(conn, "A-1", "Acme Dental GmbH & Co. KG", md_flag=True)   # raw BC spelling
    _seed_item(conn, "A-2", "ACME", md_flag=True)                        # raw == canonical
    conn.execute(
        "INSERT INTO manufacturer_alias (raw_name, canonical_name) "
        "VALUES ('Acme Dental GmbH & Co. KG', 'ACME')")

    gh.handle_gate_apply(conn, _apply(doc_id, "bind-manufacturer", manufacturer="ACME"))

    linked = {r["item_ref"]: (r["match_basis"], r["status"]) for r in conn.execute(
        "SELECT item_ref, match_basis, status FROM item_document WHERE doc_id=%s",
        (doc_id,)).fetchall()}
    assert linked["A-1"] == ("mfr-scope", "production")   # reached through the alias
    assert linked["A-2"] == ("mfr-scope", "production")


def test_bind_manufacturer_accepts_an_alias_not_only_the_canonical(conn):
    # The binding name comes off the DOCUMENT ("Ivoclar Vivadent AG"), which is a
    # raw_name whose canonical is "IVOCLAR" -- the alias table exists precisely
    # because manufacturers print their legal entity, not the catalogue's label.
    # Looking the document's spelling up as a canonical_name finds nothing and
    # links ZERO items in silence. Measured on the live registry 2026-08-17:
    # "Ivoclar Vivadent AG" -> 0 items, where resolving raw->canonical first
    # reaches all 1069.
    doc_id = _seed_staged_doc(conn, "ga-alias-rev")
    _seed_manual(conn, doc_id)
    _seed_item(conn, "R-1", "001", md_flag=True)
    _seed_item(conn, "R-2", "005", md_flag=True)
    _seed_item(conn, "R-3", "001", md_flag=False)    # not an MD item -> excluded
    _seed_item(conn, "R-9", "077", md_flag=True)     # another manufacturer -> excluded
    conn.execute(
        "INSERT INTO manufacturer_alias (raw_name, canonical_name) VALUES "
        "('001','IVOCLAR'),('005','IVOCLAR'),"
        "('Ivoclar Vivadent AG','IVOCLAR'),('077','KOMET')")

    gh.handle_gate_apply(
        conn, _apply(doc_id, "bind-manufacturer", manufacturer="Ivoclar Vivadent AG"))

    linked = {r["item_ref"] for r in conn.execute(
        "SELECT item_ref FROM item_document WHERE doc_id=%s", (doc_id,)).fetchall()}
    assert linked == {"R-1", "R-2"}    # every BC code under the resolved canonical


def test_bind_manufacturer_matches_case_and_accent_insensitively(conn):
    # The catalogue shouts ("3SHAPE TRIOS A/S"), the PDF does not ("3Shape TRIOS
    # A/S"). An exact comparison misses 194 live rows on that pair alone. Both
    # sides fold through the same normalizer VALIDATE already uses.
    doc_id = _seed_staged_doc(conn, "ga-alias-case")
    _seed_manual(conn, doc_id)
    _seed_item(conn, "C-1", "10005", md_flag=True)
    conn.execute(
        "INSERT INTO manufacturer_alias (raw_name, canonical_name) "
        "VALUES ('10005','3SHAPE TRIOS A/S')")

    gh.handle_gate_apply(
        conn, _apply(doc_id, "bind-manufacturer", manufacturer="3Shape TRIOS A/S"))

    assert {r["item_ref"] for r in conn.execute(
        "SELECT item_ref FROM item_document WHERE doc_id=%s", (doc_id,)).fetchall()} == {"C-1"}


def test_bind_manufacturer_on_an_unknown_name_links_nothing(conn):
    # The fallback must stay narrow: an unresolvable name binds NOTHING rather
    # than widening to whatever else is in the alias table. That half is
    # unchanged and is what this case exists for -- U-1 sits under a canonical
    # this name does not resolve to, and must not be swept in.
    #
    # What changed on 2026-08-19 ([gate-bind-zero-links]): the handler used to
    # reach that outcome by returning silently, having already promoted the
    # document and written its audit entries. Linking nothing is still correct;
    # doing it quietly, behind a production-write record, was not.
    doc_id = _seed_staged_doc(conn, "ga-alias-miss")
    _seed_manual(conn, doc_id)
    _seed_item(conn, "U-1", "001", md_flag=True)
    conn.execute(
        "INSERT INTO manufacturer_alias (raw_name, canonical_name) VALUES ('001','IVOCLAR')")

    with pytest.raises(ValueError, match="no BC"):
        with conn.transaction():
            gh.handle_gate_apply(
                conn, _apply(doc_id, "bind-manufacturer", manufacturer="Nobody Dental GmbH"))

    assert conn.execute(
        "SELECT count(*) c FROM item_document WHERE doc_id=%s", (doc_id,)).fetchone()["c"] == 0


def test_approve_rejects_unknown_edit_field(conn):
    doc_id = _seed_staged_doc(conn, "ga-bad")
    with pytest.raises(ValueError):
        gh.handle_gate_apply(conn, _apply(doc_id, "approve", edits={"content_hash": "hacked"}))
    assert _status(conn, doc_id) == "staged"   # nothing changed


# ---------------------------------------------------------------------------
# C17 — link-level human decisions (confirm-link / reject-link / reopen-link)
#
# C5 gave the link its own status but no human verb: `_promote_pending_links`
# publishes only trusted-basis links and the item_document CHECK forbids
# name-family / fetch-context / ref-catalogue at production, so an untrusted
# link could be neither published nor refused. `manual` was enumerated as a
# production-capable basis from v3 and nothing ever wrote it. Found on item
# 2222100: document production, link staged at fetch-context, approval inert.
# ---------------------------------------------------------------------------

def _seed_production_doc(conn, content_hash, archive="file:///c17.pdf"):
    return conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, archive_url, status) "
        "VALUES ('DoC','MDR','group',%s,%s,'production') RETURNING doc_id",
        (content_hash, archive),
    ).fetchone()["doc_id"]


def _link(conn, doc_id, item_ref):
    return conn.execute(
        "SELECT status, match_basis FROM item_document WHERE doc_id=%s AND item_ref=%s",
        (doc_id, item_ref)).fetchone()


def _events(conn, doc_id, item_ref=None):
    if item_ref is None:
        rows = conn.execute(
            "SELECT event FROM audit_log WHERE doc_id=%s ORDER BY id", (doc_id,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT event FROM audit_log WHERE doc_id=%s AND item_ref=%s ORDER BY id",
            (doc_id, item_ref)).fetchall()
    return [r["event"] for r in rows]


def test_confirm_link_is_the_only_writer_of_manual_basis(conn):
    """The bug this closes: approve promoted the doc and left the link staged
    with no way to ever change that."""
    doc_id = _seed_production_doc(conn, "c17-confirm")
    _seed_link(conn, "IT-FC", doc_id, "fetch-context", "staged")

    out = gh.handle_gate_apply(conn, _apply(doc_id, "confirm-link", item_ref="IT-FC"))

    row = _link(conn, doc_id, "IT-FC")
    assert row["status"] == "production"
    assert row["match_basis"] == "manual"        # re-based; C5 CHECK would reject fetch-context
    assert out["match_basis_before"] == "fetch-context"
    # inv. 10: the row no longer holds the original basis, so the trail must
    assert _events(conn, doc_id, "IT-FC") == ["link-confirmed", "production-write"]
    detail = conn.execute(
        "SELECT detail FROM audit_log WHERE doc_id=%s AND event='link-confirmed'",
        (doc_id,)).fetchone()["detail"]
    assert detail["match_basis_before"] == "fetch-context"


def test_confirm_link_leaves_the_document_and_sibling_links_alone(conn):
    doc_id = _seed_production_doc(conn, "c17-scope")
    _seed_link(conn, "IT-A", doc_id, "fetch-context", "staged")
    _seed_link(conn, "IT-B", doc_id, "name-family", "staged")

    gh.handle_gate_apply(conn, _apply(doc_id, "confirm-link", item_ref="IT-A"))

    assert _status(conn, doc_id) == "production"           # untouched
    assert _link(conn, doc_id, "IT-B")["status"] == "staged"   # one link, one decision


def test_reject_link_keeps_the_basis_that_proposed_it(conn):
    doc_id = _seed_production_doc(conn, "c17-reject")
    _seed_link(conn, "IT-R", doc_id, "name-family", "staged")

    gh.handle_gate_apply(conn, _apply(doc_id, "reject-link", item_ref="IT-R"))

    row = _link(conn, doc_id, "IT-R")
    assert row["status"] == "rejected"
    assert row["match_basis"] == "name-family"   # provenance of a refused link survives
    assert _events(conn, doc_id, "IT-R") == ["link-rejected"]   # no production-write


def test_rejected_link_survives_a_later_machine_candidate(conn):
    """The resurrection hole. Predates C17: only `production` was sticky, so the
    next candidate naming the same pair rewrote a human's refusal to staged."""
    doc_id = _seed_production_doc(conn, "c17-sticky")
    _seed_link(conn, "IT-S", doc_id, "name-family", "staged")
    gh.handle_gate_apply(conn, _apply(doc_id, "reject-link", item_ref="IT-S"))

    gh._upsert_link(conn, "IT-S", doc_id, None, "ref-list", "production")

    row = _link(conn, doc_id, "IT-S")
    assert row["status"] == "rejected"           # machine locked out
    assert row["match_basis"] == "name-family"


def test_retracted_is_not_sticky_so_new_evidence_revives_it(conn):
    """The deliberate asymmetry: `retracted` is a machine statement about
    evidence, not a decision, so a candidate claiming the link again wins."""
    doc_id = _seed_production_doc(conn, "c17-retracted")
    _seed_link(conn, "IT-T", doc_id, "ref-list", "retracted")

    gh._upsert_link(conn, "IT-T", doc_id, None, "ref-list", "staged")

    assert _link(conn, doc_id, "IT-T")["status"] == "staged"


def test_reopen_link_returns_a_rejected_link_to_staged_not_production(conn):
    doc_id = _seed_production_doc(conn, "c17-reopen")
    _seed_link(conn, "IT-O", doc_id, "fetch-context", "staged")
    gh.handle_gate_apply(conn, _apply(doc_id, "reject-link", item_ref="IT-O"))

    gh.handle_gate_apply(conn, _apply(doc_id, "reopen-link", item_ref="IT-O"))

    row = _link(conn, doc_id, "IT-O")
    assert row["status"] == "staged"             # re-decided on the merits, never straight to production
    assert row["match_basis"] == "fetch-context"
    assert _events(conn, doc_id, "IT-O") == ["link-rejected", "link-reopened"]


@pytest.mark.parametrize("decision,seed_status", [
    ("confirm-link", "production"),   # already published — nothing to do
    ("reject-link", "rejected"),
    ("reopen-link", "staged"),
])
def test_link_decision_is_idempotent_under_redelivery(conn, decision, seed_status):
    doc_id = _seed_production_doc(conn, f"c17-idem-{decision}")
    basis = "manual" if seed_status == "production" else "fetch-context"
    _seed_link(conn, "IT-I", doc_id, basis, seed_status)

    out = gh.handle_gate_apply(conn, _apply(doc_id, decision, item_ref="IT-I"))

    assert out["idempotent"] is True
    assert _link(conn, doc_id, "IT-I")["status"] == seed_status
    assert _events(conn, doc_id, "IT-I") == []   # no second audit row


@pytest.mark.parametrize("decision,seed_status", [
    ("confirm-link", "rejected"),    # must be re-opened first
    ("reopen-link", "production"),
    # ("reject-link", "rejected") belongs to the idempotency test above, not
    # here: re-rejecting a rejected link is the decision already holding.
    ("confirm-link", "retracted"),   # never reachable by any human decision
    ("reject-link", "retracted"),
    ("reopen-link", "retracted"),
])
def test_link_decision_raises_on_the_wrong_link_status(conn, decision, seed_status):
    doc_id = _seed_production_doc(conn, f"c17-bad-{decision}-{seed_status}")
    basis = "manual" if seed_status == "production" else "fetch-context"
    _seed_link(conn, "IT-X", doc_id, basis, seed_status)

    with pytest.raises(ValueError, match="expected"):
        gh.handle_gate_apply(conn, _apply(doc_id, decision, item_ref="IT-X"))


@pytest.mark.parametrize("doc_status", ["staged", "filed", "superseded", "rejected"])
@pytest.mark.parametrize("decision", ["confirm-link", "reject-link", "reopen-link"])
def test_link_decision_requires_a_production_document(conn, doc_status, decision):
    """Confirming under an unvetted document would publish an unreviewed reading
    through the side door; re-opening under a rejected one recreates the state
    the reject cascade exists to eliminate. Raises — never a silent skip."""
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, archive_url, status) "
        "VALUES ('DoC','MDR','group',%s,'file:///d.pdf',%s::doc_status) RETURNING doc_id",
        (f"c17-{doc_status}-{decision}", doc_status),
    ).fetchone()["doc_id"]
    seed = "rejected" if decision == "reopen-link" else "staged"
    _seed_link(conn, "IT-D", doc_id, "fetch-context", seed)

    with pytest.raises(ValueError, match="not 'production'"):
        gh.handle_gate_apply(conn, _apply(doc_id, decision, item_ref="IT-D"))

    assert _link(conn, doc_id, "IT-D")["status"] == seed


def test_link_decision_requires_item_ref(conn):
    doc_id = _seed_production_doc(conn, "c17-noitem")
    with pytest.raises(ValueError, match="item_ref is required"):
        gh.handle_gate_apply(conn, _apply(doc_id, "confirm-link"))


def test_link_decision_raises_on_an_unknown_link(conn):
    doc_id = _seed_production_doc(conn, "c17-nolink")
    with pytest.raises(LookupError, match="no link"):
        gh.handle_gate_apply(conn, _apply(doc_id, "confirm-link", item_ref="IT-NOPE"))


def test_confirmed_link_is_immune_to_retraction_by_a_later_candidate(conn):
    """A confirmed link is production, and `_retract_unsupported_links` is
    staged-only — a re-extraction that lost the item must not undo a person."""
    doc_id = _seed_production_doc(conn, "c17-retract")
    _seed_link(conn, "IT-K", doc_id, "fetch-context", "staged")
    gh.handle_gate_apply(conn, _apply(doc_id, "confirm-link", item_ref="IT-K"))

    gh._retract_unsupported_links(conn, doc_id, [], _apply(doc_id, "approve"))

    assert _link(conn, doc_id, "IT-K")["status"] == "production"


# --------------------------------------------------------------------------- #
# 053: the binding is recorded even when it links nothing
# --------------------------------------------------------------------------- #
def _seed_entity(conn, name):
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES (%s) "
                 "ON CONFLICT (canonical_name) DO NOTHING", (name,))


def _bound(conn, doc_id):
    return conn.execute(
        "SELECT canonical_manufacturer FROM document WHERE doc_id=%s", (doc_id,)
    ).fetchone()["canonical_manufacturer"]


def test_bind_manufacturer_records_the_binding_with_no_md_items(conn):
    """The 3Shape case, end to end (Denis 2026-08-31).

    Every item Dentalia holds from 3Shape carries a BLANK BC device class, so
    zero links are written -- `[mfr-bind-empty-class]` stands and no MDR
    coverage is asserted over items nobody has classified. But the DECISION is
    now recorded, which is the whole of migration 053: before it, approving
    this discarded the reviewer's answer the moment they gave it.
    """
    _seed_entity(conn, "3SHAPE TRIOS A/S")
    conn.execute("INSERT INTO manufacturer_alias (raw_name, canonical_name) "
                 "VALUES ('10005','3SHAPE TRIOS A/S')")
    doc_id = _seed_staged_doc(conn, "ga-3shape")
    _seed_manual(conn, doc_id)
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, "
        "catalogue, mirror_rev, updated_at) "
        "VALUES ('S-1','n','10005',NULL,'LJ',1,now()), "
        "       ('S-2','n','10005',NULL,'LJ',1,now())")

    gh.handle_gate_apply(
        conn, _apply(doc_id, "bind-manufacturer", manufacturer="3SHAPE TRIOS A/S"))

    assert _bound(conn, doc_id) == "3SHAPE TRIOS A/S"
    assert conn.execute(
        "SELECT count(*) c FROM item_document WHERE doc_id=%s", (doc_id,)
    ).fetchone()["c"] == 0
    assert _status(conn, doc_id) == "production"


def test_bind_manufacturer_still_refuses_an_unresolvable_name(conn):
    """`[gate-bind-zero-links]` is CORRECTED here, not reversed.

    The raise guards a name resolving to no BC codes, which is still a real
    bug. It never guarded a resolvable name holding no MD item -- three places
    in the tree claimed it did, and 3SHAPE TRIOS A/S resolving to
    ['10004','10005'] is the counter-example."""
    _seed_entity(conn, "GHOST AG")
    doc_id = _seed_staged_doc(conn, "ga-ghost")
    _seed_manual(conn, doc_id)

    with pytest.raises(ValueError, match="resolves to no BC"):
        gh.handle_gate_apply(
            conn, _apply(doc_id, "bind-manufacturer", manufacturer="GHOST AG"))


# --- reopen: the way back out of `rejected` --------------------------------- #
#
# Until 2026-09-04 a rejected document was un-rejected by ACCIDENT: any
# re-delivery overwrote the status through `_upsert_document`, silently and
# with no audit row. Closing that hole removed the only escape with it, so this
# is the deliberate one. `reopen-link` set the shape for links; this is its
# document-level counterpart.

def _rejected(conn, h):
    doc_id = _seed_staged_doc(conn, h)
    conn.execute("UPDATE document SET status='rejected' WHERE doc_id=%s", (doc_id,))
    return doc_id


def test_reopen_returns_a_rejected_document_to_review(conn):
    doc_id = _rejected(conn, "reopen-1".ljust(64, "0"))
    gh.handle_gate_apply(conn, _apply(doc_id, "reopen"))
    assert _status(conn, doc_id) == "staged"


def test_reopen_leaves_the_rejected_links_alone(conn):
    """`reject` cascades links so none is left staged or production under a
    rejected document. The reverse is NOT symmetric: re-staging them would
    recreate exactly the state that cascade exists to prevent. Each link comes
    back through `reopen-link`, one human decision at a time."""
    doc_id = _rejected(conn, "reopen-2".ljust(64, "0"))
    _seed_link(conn, "IT-REOPEN", doc_id, "ref-list", status="rejected")
    gh.handle_gate_apply(conn, _apply(doc_id, "reopen"))
    assert _link_status(conn, doc_id, "IT-REOPEN") == "rejected"


@pytest.mark.parametrize("status", ["staged", "production", "filed"])
def test_reopen_refuses_a_document_that_is_not_rejected(conn, status):
    """Raises rather than skipping, like every link decision: a reopen that
    quietly did nothing would read to the reviewer as their click being
    ignored, which is the failure C17 was written to end."""
    doc_id = _seed_staged_doc(conn, f"reopen-{status}".ljust(64, "0"))
    conn.execute("UPDATE document SET status=%s::doc_status WHERE doc_id=%s",
                 (status, doc_id))
    with pytest.raises(ValueError, match="not 'rejected'"):
        gh.handle_gate_apply(conn, _apply(doc_id, "reopen"))


def test_reopen_is_audited_under_the_name_that_asked_for_it(conn):
    doc_id = _rejected(conn, "reopen-3".ljust(64, "0"))
    gh.handle_gate_apply(conn, _apply(doc_id, "reopen", decided_by="denis"))
    row = conn.execute(
        "SELECT event, decided_by FROM audit_log WHERE doc_id=%s AND event='reopen'",
        (doc_id,)).fetchone()
    assert row is not None and row["decided_by"] == "denis"


# ---------------------------------------------------------------------------
# `note` (office UI redesign P1a, 2026-09-11): the reviewer's reason, optional
# and additive. Written into the decision's own audit row so the Decisions page
# can show it; a payload without it must behave exactly as before.
# ---------------------------------------------------------------------------

def _decision_detail(conn, doc_id, event):
    return conn.execute(
        "SELECT detail FROM audit_log WHERE doc_id=%s AND event=%s",
        (doc_id, event)).fetchone()["detail"]


def test_a_note_is_written_to_the_decision_audit_row(conn):
    doc_id = _seed_staged_doc(conn, "ga-note")
    note = "Out of date: newer 2025 version exists"

    gh.handle_gate_apply(conn, _apply(doc_id, "reject", note=note))

    got = conn.execute(
        "SELECT detail->>'note' AS note FROM audit_log WHERE doc_id=%s AND event='reject'",
        (doc_id,)).fetchone()["note"]
    assert got == note


def test_a_decision_without_a_note_records_no_note_key(conn):
    doc_id = _seed_staged_doc(conn, "ga-no-note")

    gh.handle_gate_apply(conn, _apply(doc_id, "reject"))

    detail = _decision_detail(conn, doc_id, "reject")
    assert detail is None or "note" not in detail
    assert _status(conn, doc_id) == "rejected"


def test_a_note_rides_only_on_the_decision_row_not_the_cascade(conn):
    """The cascaded `link-rejected` rows are consequences of the decision, not
    decisions of their own; the reason belongs to the one the person made."""
    doc_id = _seed_staged_doc(conn, "ga-note-casc")
    _seed_link(conn, "NC-1", doc_id, "ref-list", "staged")

    gh.handle_gate_apply(conn, _apply(doc_id, "reject", note="Wrong manufacturer"))

    cascaded = conn.execute(
        "SELECT detail FROM audit_log WHERE doc_id=%s AND event='link-rejected'",
        (doc_id,)).fetchone()["detail"]
    assert cascaded is None


def test_a_link_decision_keeps_its_detail_and_adds_the_note(conn):
    doc_id = _seed_production_doc(conn, "ga-note-link".ljust(64, "0"))
    _seed_link(conn, "NL-1", doc_id, "name-family", "staged")

    gh.handle_gate_apply(conn, _apply(doc_id, "reject-link", item_ref="NL-1",
                                      note="Not one of our items"))

    detail = conn.execute(
        "SELECT detail FROM audit_log WHERE doc_id=%s AND event='link-rejected'",
        (doc_id,)).fetchone()["detail"]
    assert detail["note"] == "Not one of our items"
    assert detail["match_basis_before"] == "name-family"
