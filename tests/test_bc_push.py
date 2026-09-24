"""`bc.push` — what actually reaches Business Central, and what does not.

The rule this stage applies lives in `app/bc_fields.py` and is tested there.
This file is about the stage: it sends only what changed, it records what it
sent, and it never writes an item the pipeline has not processed.

Design: `docs/superpowers/specs/2026-09-07-bc-writeback-design.md`.
"""

from __future__ import annotations

import uuid

import pytest

from app.handlers.bc_push import handle_bc_push


class FakeBc:
    """Records PATCHes instead of making them. The real client is the only
    thing between this handler and someone else's ERP, so every test injects
    this and no test is one config key away from a live write."""

    def __init__(self, status: int = 204):
        self.calls: list[tuple[str, dict]] = []
        self.status = status

    def patch_item(self, item_ref: str, fields: dict) -> tuple[int, str]:
        self.calls.append((item_ref, dict(fields)))
        return self.status, ""

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def writes_enabled(monkeypatch):
    """Every test in this file states the write gate rather than inheriting it.

    The default is OFF (`app/config.py::Bc`), so a test that exercises the write
    path has to say so -- and the two that assert the gate itself override this
    back to false. `load_config()` is not cached, so the env var takes effect on
    the handler's next call.
    """
    monkeypatch.setenv("BC_WRITE_ENABLED", "true")


@pytest.fixture
def seed_item(conn):
    """An item, its group, and optionally a document covering it."""

    def _seed(*, doc_type="DoC", coverage_scope="group", status="production",
              link_status="production", validity_to="2031-01-01", with_doc=True):
        suffix = uuid.uuid4().hex[:12]
        item_ref = f"BC-{suffix}"
        conn.execute(
            "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, mfr_ref, "
            "md_flag, catalogue, updated_at) "
            "VALUES (%s,'Widget','test',%s,TRUE,'LJ',now())",
            (item_ref, f"REF-{suffix}"),
        )
        group_id = conn.execute(
            "INSERT INTO item_group (canonical_manufacturer) VALUES ('ACME') "
            "RETURNING group_id"
        ).fetchone()["group_id"]
        conn.execute(
            "INSERT INTO item_group_member (group_id, item_ref, match_basis) "
            "VALUES (%s,%s,'manual')",
            (group_id, item_ref),
        )
        if with_doc:
            doc_id = conn.execute(
                "INSERT INTO document (type, regulation, validity_from, validity_to, "
                "status, content_hash, archive_url, coverage_scope) "
                "VALUES (%s,'MDR','2022-01-01',%s,%s,%s,'/archive/x.pdf',%s) "
                "RETURNING doc_id",
                (doc_type, validity_to, status, f"h-bc-{suffix}", coverage_scope),
            ).fetchone()["doc_id"]
            conn.execute(
                "INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
                "VALUES (%s,%s,'ref-list',%s)",
                (item_ref, doc_id, link_status),
            )
        return item_ref

    return _seed


def _job(item_refs, run_id="r1"):
    return {"id": 1, "type": "bc.push",
            "payload": {"run_id": run_id, "item_refs": list(item_refs)}}


def test_a_first_push_sends_all_three_fields(conn, seed_item):
    item_ref = seed_item()
    bc = FakeBc()

    handle_bc_push(conn, _job([item_ref]), client=bc)

    assert len(bc.calls) == 1
    sent_ref, fields = bc.calls[0]
    assert sent_ref == item_ref
    assert fields["pteValidDeclarationOfConformity"] is True
    assert set(fields) == {
        "pteValidDeclarationOfConformity",
        "pteValidCECertificate",
        "pteWarehouseURL",
    }


def test_a_second_push_sends_nothing(conn, seed_item):
    """Idempotent by construction: recompute, diff against the ledger, send the
    difference. A re-run job, a duplicated job and a lost job all cost the same."""
    item_ref = seed_item()
    bc = FakeBc()

    handle_bc_push(conn, _job([item_ref]), client=bc)
    result = handle_bc_push(conn, _job([item_ref], run_id="r2"), client=bc)

    assert len(bc.calls) == 1
    assert result["counts"]["unchanged"] == 1


def test_only_the_field_that_changed_is_sent(conn, seed_item):
    item_ref = seed_item()
    bc = FakeBc()
    handle_bc_push(conn, _job([item_ref]), client=bc)

    conn.execute(
        "UPDATE item_document SET status='retracted' WHERE item_ref=%s", (item_ref,)
    )
    handle_bc_push(conn, _job([item_ref], run_id="r2"), client=bc)

    assert list(bc.calls[1][1]) == ["pteValidDeclarationOfConformity"]
    assert bc.calls[1][1]["pteValidDeclarationOfConformity"] is False


def test_an_unprocessed_item_is_skipped_and_counted(conn, seed_item):
    """Never assert anything about an item the pipeline has not reached."""
    item_ref = seed_item(with_doc=False)
    bc = FakeBc()

    result = handle_bc_push(conn, _job([item_ref]), client=bc)

    assert bc.calls == []
    assert result["counts"]["unprocessed"] == 1


def test_the_ledger_records_what_was_sent(conn, seed_item):
    item_ref = seed_item()
    handle_bc_push(conn, _job([item_ref]), client=FakeBc())

    rows = conn.execute(
        "SELECT field, old_value, new_value, http_status, via_job "
        "FROM bc_push_log WHERE item_ref=%s ORDER BY field", (item_ref,)
    ).fetchall()

    assert len(rows) == 3
    assert {r["http_status"] for r in rows} == {204}
    assert {r["via_job"] for r in rows} == {1}
    assert all(r["old_value"] is None for r in rows), "first send replaces nothing"


def test_a_refused_patch_is_counted_as_failed_not_sent(conn, seed_item):
    """A 500 from BC is not a write. Counting it as one would make the ledger
    claim BC holds a value it rejected, and the next run would then send
    nothing because the diff looks clean."""
    item_ref = seed_item()
    bc = FakeBc(status=500)

    result = handle_bc_push(conn, _job([item_ref]), client=bc)

    assert result["counts"]["failed"] == 1
    assert result["counts"]["sent"] == 0


def test_a_refused_patch_is_resent_next_run(conn, seed_item):
    """The consequence of the rule above, and the reason it matters."""
    item_ref = seed_item()
    handle_bc_push(conn, _job([item_ref]), client=FakeBc(status=500))

    ok = FakeBc()
    handle_bc_push(conn, _job([item_ref], run_id="r2"), client=ok)

    assert len(ok.calls) == 1, "a rejected value must not look already-sent"


def test_one_item_failing_does_not_stop_the_batch(conn, seed_item):
    class Flaky(FakeBc):
        def patch_item(self, item_ref, fields):
            self.calls.append((item_ref, dict(fields)))
            return (500, "boom") if len(self.calls) == 1 else (204, "")

    first, second = seed_item(), seed_item()
    bc = Flaky()

    result = handle_bc_push(conn, _job([first, second]), client=bc)

    assert len(bc.calls) == 2
    assert result["counts"]["failed"] == 1
    assert result["counts"]["sent"] == 1


# --------------------------------------------------------------------------- #
# the write gate
# --------------------------------------------------------------------------- #
def test_writing_is_off_unless_it_is_turned_on(conn, seed_item, monkeypatch):
    """`bc.write_enabled` defaults false. Nothing in this repo may reach the
    client's ERP because a test, a rehearsal or a stray enqueue happened to run
    -- turning it on is a deliberate act with a name."""
    monkeypatch.setenv("BC_WRITE_ENABLED", "false")
    item_ref = seed_item()
    bc = FakeBc()

    result = handle_bc_push(conn, _job([item_ref]), client=bc)

    assert bc.calls == []
    assert result["counts"]["withheld"] == 1
    assert conn.execute(
        "SELECT count(*) AS n FROM bc_push_log WHERE item_ref=%s", (item_ref,)
    ).fetchone()["n"] == 0, "a withheld push is not a ledger entry"


def test_a_withheld_run_still_reports_what_it_would_have_sent(conn, seed_item,
                                                              monkeypatch):
    """Off must still be useful: this is what the bulk preview reads."""
    monkeypatch.setenv("BC_WRITE_ENABLED", "false")
    item_ref = seed_item()

    result = handle_bc_push(conn, _job([item_ref]), client=FakeBc())

    assert result["counts"]["would_send"] == 1


# --------------------------------------------------------------------------- #
# what `dataitems` is a subset of
# --------------------------------------------------------------------------- #
def test_a_404_is_counted_as_absent_not_as_a_failure(conn, seed_item):
    """The correspondence with b-s.si describes `dataitems` as a subset, without
    saying a subset of what. If it is filtered, the items we mean to write are
    simply not there -- and a 404 folded into generic failures would hide that behind
    retries. Counted separately, the first real run answers the question that
    was never asked."""
    item_ref = seed_item()
    bc = FakeBc(status=404)

    result = handle_bc_push(conn, _job([item_ref]), client=bc)

    assert result["counts"]["absent"] == 1
    assert result["counts"]["failed"] == 0
    assert result["counts"]["sent"] == 0


def test_an_absent_item_is_named_in_the_result(conn, seed_item):
    """A count says how many; the sample says which, so the pattern is
    readable without a database query."""
    item_ref = seed_item()

    result = handle_bc_push(conn, _job([item_ref]), client=FakeBc(status=404))

    assert any(s["item_ref"] == item_ref
               for s in result["samples"]["absent"])


def test_an_absent_item_is_retried_next_run(conn, seed_item):
    """404 is not acceptance. If b-s.si widen the page later, the value must
    still go -- so it must not look already-sent."""
    item_ref = seed_item()
    handle_bc_push(conn, _job([item_ref]), client=FakeBc(status=404))

    ok = FakeBc()
    handle_bc_push(conn, _job([item_ref], run_id="r2"), client=ok)

    assert len(ok.calls) == 1


# --------------------------------------------------------------------------- #
# The EUDAMED certificate status behind `pteValidCECertificate`. It used to be
# joined on the certificate number alone, the one EUDAMED read in the tree not
# scoped to the manufacturer's trusted SRN (`certificate_drift` and friends all
# are, migration 043/058). Found by the 2026-09-11 readiness audit.
# --------------------------------------------------------------------------- #
def _certify(conn, item_ref, cert_number):
    conn.execute(
        "UPDATE document SET cert_number=%s WHERE doc_id IN "
        "(SELECT doc_id FROM item_document WHERE item_ref=%s)",
        (cert_number, item_ref),
    )


def _eudamed_cert(conn, number, srn, status, revision=""):
    conn.execute(
        "INSERT INTO eudamed_certificate (certificate_number, revision_number, "
        "actor_srn, certificate_status, synced_at) VALUES (%s,%s,%s,%s,now())",
        (number, revision, srn, status),
    )


def _trust(conn, name="ACME"):
    srn = f"DE-MF-{uuid.uuid4().int % 10**9:09d}"
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES (%s) "
                 "ON CONFLICT DO NOTHING", (name,))
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, status) "
        "VALUES (%s,%s,'register-exact','auto')", (name, srn),
    )
    return srn


def _valid_ce(conn, item_ref):
    bc = FakeBc()
    handle_bc_push(conn, _job([item_ref]), client=bc)
    return bc.calls[0][1]["pteValidCECertificate"]


def test_another_manufacturers_certificate_cannot_falsify_valid_ce(conn, seed_item):
    """Live on 2026-09-11: doc 443's `HZ 2158053-1` also names a certificate of
    Guilin Woodpecker's (`CN-MF-000009139`). A number collision is not evidence;
    only a certificate under the item's manufacturer's trusted SRN is."""
    item_ref = seed_item(doc_type="EC")
    _certify(conn, item_ref, "HZ 2158053-1")
    _eudamed_cert(conn, "HZ 2158053-1", "CN-MF-000009139", "withdrawn")

    assert _valid_ce(conn, item_ref) is True


def test_our_manufacturers_withdrawn_certificate_falsifies_despite_the_rev_suffix(
        conn, seed_item):
    """EUDAMED stores the base number; documents print `Rev. 2`. Stripped the
    way `held_certificate` strips it, or a withdrawal is never seen."""
    item_ref = seed_item(doc_type="EC")
    _certify(conn, item_ref, "G1 123 Rev. 2")
    _eudamed_cert(conn, "G1 123", _trust(conn), "withdrawn", revision="2")

    assert _valid_ce(conn, item_ref) is False


def test_the_latest_revision_decides_not_any_revision(conn, seed_item):
    """One certificate, two EUDAMED revisions: a join that returns both lets the
    older `issued` row carry `any()` past the newer withdrawal (docs 420 and
    925 fan out to two rows each on the live registry)."""
    item_ref = seed_item(doc_type="EC")
    _certify(conn, item_ref, "G1 124")
    srn = _trust(conn)
    _eudamed_cert(conn, "G1 124", srn, "issued", revision="1")
    _eudamed_cert(conn, "G1 124", srn, "withdrawn", revision="2")

    assert _valid_ce(conn, item_ref) is False


# --------------------------------------------------------------------------- #
# the real client, built from config
# --------------------------------------------------------------------------- #
@pytest.fixture
def built(monkeypatch):
    """Stands in for `BcClient` so a test can see the handler build one from
    config without any test being able to reach a real server."""
    made = []

    class Recorder(FakeBc):
        def __init__(self, base_url, *, username="", password=""):
            super().__init__(status=200)
            self.args = (base_url, username, password)
            self.closed = False
            made.append(self)

    import app.adapters.bc_client as bc_client
    monkeypatch.setattr(bc_client, "BcClient", Recorder)
    monkeypatch.setenv("BC_BASE_URL", "http://bc/api/companies(1)")
    monkeypatch.setenv("BC_USERNAME", "DOM\\svc")
    monkeypatch.setenv("BC_PASSWORD", "pw")
    return made


def test_without_an_injected_client_it_builds_one_from_config(conn, seed_item, built):
    """The running handler is never given a client; until 2026-09-24 it would
    have called `patch` on `None` the first time writes were switched on."""
    item_ref = seed_item()

    result = handle_bc_push(conn, _job([item_ref]))

    assert result["counts"]["sent"] == 1
    assert [b.args for b in built] == [("http://bc/api/companies(1)", "DOM\\svc", "pw")]
    assert built[0].calls[0][0] == item_ref
    assert built[0].closed, "a client this handler built is closed by it"


def test_a_withheld_run_never_logs_in(conn, seed_item, built, monkeypatch):
    """The bulk preview runs with writes off; it must not cost a BC login."""
    monkeypatch.setenv("BC_WRITE_ENABLED", "false")

    handle_bc_push(conn, _job([seed_item()]))

    assert built == []


def test_writes_on_without_a_base_url_fail_the_job_by_name(conn, seed_item, built,
                                                           monkeypatch):
    monkeypatch.setenv("BC_BASE_URL", "")

    with pytest.raises(RuntimeError, match="BC_BASE_URL"):
        handle_bc_push(conn, _job([seed_item()]))


def test_a_refused_login_stops_the_batch(conn, seed_item):
    """One refusal is one failed domain login. Carrying on would make it one
    per item in the batch -- 200 -- and lock the account."""
    from app.adapters.bc_client import BcAuthRejected

    class Refusing(FakeBc):
        def patch_item(self, item_ref, fields):
            self.calls.append((item_ref, dict(fields)))
            raise BcAuthRejected("refused")

    bc = Refusing()
    with pytest.raises(BcAuthRejected):
        handle_bc_push(conn, _job([seed_item(), seed_item()]), client=bc)

    assert len(bc.calls) == 1
