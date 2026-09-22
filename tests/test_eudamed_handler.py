"""`eudamed.sync` — the one EUDAMED call we make, and what it is allowed to do.

Scope ruling (Denis, 2026-08-25): **stage 1 only**. Fetch a Basic UDI-DI's
device group, store it, show it. This handler writes `eudamed_mirror` and
nothing else — no `item_document`, no `document`, no `evidence`. The
`ref-eudamed` match basis is capped at `staged` by the same ruling and no code
here creates one, so the cap is not yet exercised.

Why the mirror is worth filling at all: every EUDAMED device record carries
`reference`, its Reference/Catalogue number, populated on 100% of the 16.502
records swept on 2026-08-20. That field is the article number a document
actually covers, which is the article-level evidence 65,8% of our covered
devices lack.

Why it does NOT light up DISCOVER: that rung reads `cert_refs` — document URLs
— and the same research measured that EUDAMED returns none. Filling the mirror
leaves the rung missing exactly as before, and a test below pins that so nobody
reports it as a regression later.

The API is undocumented and unofficial. Parameters that look plausible are
silently ignored and return the unfiltered 3.19M-record count, so `basicUdi`
being the *working* one is a measured fact, not a guess, and
`test_the_request_uses_the_one_parameter_that_actually_filters` is what stops it
being quietly changed to `manufacturerName` or `deviceName`, both of which do
nothing.
"""

from __future__ import annotations

import json

import pytest

from app.adapters.fetcher import FetchResult
from app import config as _cfgmod
from app.config import load_config
from app.handlers import eudamed
from app.handlers.eudamed import MAX_DEVICES, MAX_PAGES, _is_last, handle_eudamed_sync

BUDI = "471070188CNQQ"


class FakeFetcher:
    """Records the URLs asked for and replays canned responses.

    A fake rather than the real transport: this suite must never reach
    ec.europa.eu. The shape it returns is `FetchResult`, the same dataclass
    `HttpxFetcher` produces, so the handler cannot tell the difference.
    """

    def __init__(self, *responses):
        self.calls: list[str] = []
        self._responses = list(responses)

    def get(self, url, *, etag=None, last_modified=None) -> FetchResult:
        self.calls.append(url)
        body = self._responses.pop(0) if self._responses else _page([])
        if isinstance(body, FetchResult):
            return body
        return FetchResult(
            status=200,
            body=json.dumps(body).encode(),
            etag=None,
            last_modified=None,
            content_type="application/json",
            final_url=url,
        )


def _device(udi_di: str, *, reference: str, name: str = "Widget",
            trade_name: str | None = None, srn: str = "SRN-1"):
    return {
        "uuid": f"uuid-{udi_di}",
        "primaryDi": udi_di,
        "basicUdi": BUDI,
        "reference": reference,
        "deviceName": name,
        "tradeName": trade_name,
        "manufacturerSrn": srn,
    }


def _sweepable(conn, canonical, srn):
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES (%s) "
                 "ON CONFLICT DO NOTHING", (canonical,))
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, "
        " status) VALUES (%s, %s, 'register-exact', 'auto') "
        "ON CONFLICT DO NOTHING", (canonical, srn))


def _seed_item(conn, *, item_ref, canonical, mfr_ref=None):
    """`item_mirror` row plus the `item_group` / `item_group_member` chain
    `probe_srn` joins through to find one of our own article numbers for a
    canonical manufacturer -- the same shape `seeded_doc` (conftest.py) builds,
    trimmed to the columns the probe's query actually touches. `md_flag` is
    TRUE because the probe only ever reads a device article."""
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, mfr_ref, "
        "md_flag, catalogue, updated_at) VALUES (%s,'Widget','test',%s,TRUE,'LJ',now())",
        (item_ref, mfr_ref),
    )
    group_id = conn.execute(
        "INSERT INTO item_group (canonical_manufacturer) VALUES (%s) "
        "RETURNING group_id", (canonical,),
    ).fetchone()["group_id"]
    conn.execute(
        "INSERT INTO item_group_member (group_id, item_ref, match_basis) "
        "VALUES (%s,%s,'manual')", (group_id, item_ref),
    )


#: Captured at import, before the autouse fixture below can replace it, so the
#: one test that exercises the real pause still can.
_REAL_PAUSE = eudamed._pause_between_pages


@pytest.fixture(autouse=True)
def _no_real_politeness_sleep(monkeypatch):
    """Paging is polite in production and instant in tests.

    `_pause_between_pages` spends `cfg.fetch.politeness_ms` (2s) between pages
    for real, which is the point of it -- but the walks exercised here page up
    to `MAX_PAGES` times, so the honest sleep turns this file into a
    seventeen-minute run. Neutralised for every test; the three that assert on
    the pause re-patch it themselves, and a later `monkeypatch.setattr` in the
    test body wins over this one."""
    monkeypatch.setattr(eudamed, "_pause_between_pages", lambda: None)


def _page(devices, *, total=None, last=True, number=0):
    """The Spring envelope EUDAMED serves. Reading `content` alone silently
    takes the first 20 of any larger group -- that shipped as a bug on
    2026-08-25 and was fixed the same day.

    `total` stays for the existing `eudamed.sync` tests (which page over
    device groups larger than one page); `number` is new for the certificate
    register tests below. Neither handler reads `number` or `totalPages` off
    the body -- both walk on `last` -- so the field is documentation, not a
    dependency."""
    return {
        "content": devices,
        "number": number,
        "size": len(devices),
        "totalElements": len(devices) if total is None else total,
        "totalPages": number + 1,
        "last": last,
    }


def _job(basic_udi_di=BUDI, **extra):
    return {"id": 1, "type": "eudamed.sync", "payload": {"basic_udi_di": basic_udi_di, **extra}}


def _mirror(conn):
    return conn.execute(
        "SELECT udi_di, basic_udi_di, device_name, trade_name, manufacturer_srn, "
        "reference, cert_refs "
        "FROM eudamed_mirror ORDER BY udi_di"
    ).fetchall()


# --------------------------------------------------------------------------- #
# the request
# --------------------------------------------------------------------------- #
def test_the_request_uses_the_one_parameter_that_actually_filters(conn):
    """`basicUdi` filters. `manufacturerName`, `manufacturerSrn`, `deviceName`
    and `nomenclatureCode` look plausible and are silently ignored — a request
    built on one of those returns the unfiltered 3.19M-record set and would read
    as a spectacular hit (measured 2026-08-20 §1)."""
    fetcher = FakeFetcher(_page([_device("D1", reference="7031")]))

    handle_eudamed_sync(conn, _job(), fetcher=fetcher)

    assert len(fetcher.calls) == 1
    assert "basicUdi=471070188CNQQ" in fetcher.calls[0]
    assert "/devices/udiDiData" in fetcher.calls[0]


def test_a_job_without_a_basic_udi_di_does_no_work_and_does_not_fail(conn):
    """Documents without one are the majority — 301 of 769. The button is not
    offered for them, but a hand-enqueued job must not dead-letter."""
    fetcher = FakeFetcher()

    out = handle_eudamed_sync(conn, _job(basic_udi_di=None), fetcher=fetcher)

    assert fetcher.calls == []
    assert out["counts"].get("skipped_no_basic_udi_di") == 1
    assert _mirror(conn) == []


# --------------------------------------------------------------------------- #
# what lands in the mirror
# --------------------------------------------------------------------------- #
def test_each_device_in_the_group_becomes_a_mirror_row(conn):
    fetcher = FakeFetcher(_page([
        _device("D1", reference="7031", name="Bur A"),
        _device("D2", reference="7024", name="Bur B"),
    ]))

    out = handle_eudamed_sync(conn, _job(), fetcher=fetcher)

    rows = _mirror(conn)
    assert [r["udi_di"] for r in rows] == ["D1", "D2"]
    assert [r["reference"] for r in rows] == ["7031", "7024"]
    assert [r["device_name"] for r in rows] == ["Bur A", "Bur B"]
    assert all(r["basic_udi_di"] == BUDI for r in rows)
    assert out["counts"]["devices"] == 2


# --------------------------------------------------------------------------- #
# paging
# --------------------------------------------------------------------------- #
# EUDAMED serves `/devices/udiDiData` as a Spring page: the body carries
# `content`, `number`, `size`, `totalElements`, `totalPages` and `last`, and the
# DEFAULT page size is 20. Measured live 2026-08-25 against the research doc's
# own worked example, `basicUdi=471070188CNQQ`: `totalElements=47`,
# `totalPages=3`, `size=20`. Reading only the first response therefore stored 20
# of 47 devices and reported `devices: 20` -- a silent 57% loss that looked like
# a clean hit. Both `page=` and `size=` were confirmed to work in the same probe
# (`size=100` returns all 47 in one call; `page=2` returns `number=2, last=true`),
# which matters because unknown parameters on this API are silently ignored.
def test_a_group_larger_than_one_page_is_read_to_the_end(conn):
    """The bug this section exists for. Three pages in, 47 rows out."""
    fetcher = FakeFetcher(
        _page([_device(f"D{i}", reference=str(i)) for i in range(20)],
              total=47, last=False),
        _page([_device(f"D{i}", reference=str(i)) for i in range(20, 40)],
              total=47, last=False),
        _page([_device(f"D{i}", reference=str(i)) for i in range(40, 47)],
              total=47, last=True),
    )

    out = handle_eudamed_sync(conn, _job(), fetcher=fetcher)

    assert out["counts"]["devices"] == 47
    assert len(_mirror(conn)) == 47
    assert len(fetcher.calls) == 3


def test_each_page_after_the_first_asks_for_its_own_page_number(conn):
    """`page` is 0-based and is one of the parameters that actually filters.
    Omitting it would re-read page 0 forever."""
    fetcher = FakeFetcher(
        _page([_device("D1", reference="1")], total=3, last=False),
        _page([_device("D2", reference="2")], total=3, last=False),
        _page([_device("D3", reference="3")], total=3, last=True),
    )

    handle_eudamed_sync(conn, _job(), fetcher=fetcher)

    assert "page=0" in fetcher.calls[0]
    assert "page=1" in fetcher.calls[1]
    assert "page=2" in fetcher.calls[2]


def test_the_request_asks_for_a_page_larger_than_the_default_twenty(conn):
    """One call instead of three for every group we have actually seen. The
    loop still runs -- `size` is a courtesy to EUDAMED, never the correctness
    argument."""
    fetcher = FakeFetcher(_page([_device("D1", reference="1")]))

    handle_eudamed_sync(conn, _job(), fetcher=fetcher)

    assert "size=100" in fetcher.calls[0]


def test_paging_stops_on_the_last_page_and_asks_for_no_more(conn):
    fetcher = FakeFetcher(
        _page([_device("D1", reference="1")], total=2, last=False),
        _page([_device("D2", reference="2")], total=2, last=True),
    )

    handle_eudamed_sync(conn, _job(), fetcher=fetcher)

    assert len(fetcher.calls) == 2


def test_an_empty_page_that_contradicts_its_envelope_stops_the_walk_loudly(conn):
    """`last` is the API's word, not ours, and a page with no content must
    still not page forever. Until 2026-09-02 this ended the walk SILENTLY and
    kept the one device already stored, on the reading that a malformed
    envelope is the only thing that produces it.

    That reading was wrong, and the shape below is the counter-example: an
    envelope claiming 99 elements and `last: false` that then hands over
    nothing is EUDAMED throttling, not EUDAMED malformed -- reproduced live
    against the real API. Ending quietly there is how a 38-page IVOCLAR sweep
    finished on page 0 having stored nothing and called it success.

    So the walk still stops -- the anti-infinite-paging intent is intact, and
    `test_a_server_that_never_says_last_raises_instead_of_looping_forever`
    covers the with-content case -- but it stops by raising, which fails the
    job, backs it off, and leaves the manufacturer on the due list. A partial
    result kept quietly is the outcome this must never produce again."""
    fetcher = FakeFetcher(
        _page([_device("D1", reference="1")], total=99, last=False),
        _page([], total=99, last=False),
    )

    with pytest.raises(RuntimeError, match="contradicts its own envelope"):
        handle_eudamed_sync(conn, _job(), fetcher=fetcher)

    assert len(fetcher.calls) == 2


def test_a_server_that_never_says_last_raises_instead_of_looping_forever(conn):
    """The parameter-silently-ignored failure mode with a loop attached: if
    `basicUdi` ever stops filtering, the walk would march through the whole
    3.19M-record set one page at a time. Bounded, and loud when it hits."""
    endless = [_page([_device(f"D{p}", reference=str(p))], total=3_196_505, last=False)
               for p in range(MAX_PAGES + 2)]
    fetcher = FakeFetcher(*endless)

    with pytest.raises(RuntimeError, match="stopped paging after"):
        handle_eudamed_sync(conn, _job(), fetcher=fetcher)

    assert len(fetcher.calls) == MAX_PAGES


def test_a_group_of_hundreds_is_stored_not_refused(conn):
    """The cap was set at 500 on the belief that 41 was a large group. Measured
    live 2026-08-25 on our own documents: Ivoclar's `76152082ACERA011ET` is 223
    devices and `76152082APROS004VZ` is **7.586**, every record carrying that
    exact `basicUdi` and one manufacturer. The 2026-08-20 sweep's "41 at its
    largest" was a property of the HIBCC-heavy codes it happened to hold, and it
    was the wrong thing to size a guard on."""
    pages = [_page([_device(f"D{p}-{i}", reference=f"{p}-{i}") for i in range(100)],
                   total=600, last=False) for p in range(6)]
    pages.append(_page([], total=600, last=True))
    fetcher = FakeFetcher(*pages)

    out = handle_eudamed_sync(conn, _job(), fetcher=fetcher)

    assert out["counts"]["devices"] == 600
    assert len(_mirror(conn)) == 600


def test_a_record_from_another_group_means_the_filter_stopped_and_raises(conn):
    """The guard that actually knows something. Unknown parameters on this API
    are silently ignored and return the unfiltered 3.19M set, and the only exact
    signal of that is records that are not in the group we asked for -- a size
    threshold can only guess, and guessed wrong once already."""
    fetcher = FakeFetcher(_page([
        _device("D1", reference="1"),
        {**_device("D2", reference="2"), "basicUdi": "SOMETHING-ELSE"},
    ]))

    with pytest.raises(RuntimeError, match="stopped filtering"):
        handle_eudamed_sync(conn, _job(), fetcher=fetcher)


def test_a_record_that_omits_its_basic_udi_is_taken_at_the_query_s_word(conn):
    """Absence is not contradiction. EUDAMED answered a filtered query; a record
    that simply does not echo the field back is not evidence the filter broke."""
    d = _device("D1", reference="1")
    d.pop("basicUdi")
    fetcher = FakeFetcher(_page([d]))

    handle_eudamed_sync(conn, _job(), fetcher=fetcher)

    assert _mirror(conn)[0]["basic_udi_di"] == BUDI


def test_the_device_cap_counts_the_whole_group_not_one_page(conn):
    """MAX_DEVICES is now only a backstop -- 7.586 is a real group, 3.19M is the
    failure -- but it still has to count the walk, not one page of it."""
    pages = [_page([_device(f"D{p}-{i}", reference=str(i)) for i in range(100)],
                   total=100_000, last=False)
             for p in range(MAX_DEVICES // 100 + 1)]
    fetcher = FakeFetcher(*pages)

    with pytest.raises(RuntimeError, match="past the plausible group size"):
        handle_eudamed_sync(conn, _job(), fetcher=fetcher)



def test_the_trade_name_is_stored_because_device_name_comes_back_null(conn):
    """EUDAMED's `deviceName` was null on 121 of 121 records across two
    unrelated groups (2026-08-25); `tradeName` carried the actual name. Reading
    only `deviceName` rendered a column of em-dashes on the document page while
    the name sat one key away."""
    fetcher = FakeFetcher(_page([
        {**_device("D1", reference="541435AN"), "deviceName": None,
         "tradeName": "SR Triplex Cold Standard Kit pink-V"},
    ]))

    handle_eudamed_sync(conn, _job(), fetcher=fetcher)

    row = _mirror(conn)[0]
    assert row["trade_name"] == "SR Triplex Cold Standard Kit pink-V"
    assert row["device_name"] is None


def test_both_name_fields_are_kept_apart(conn):
    """Stored beside each other, not coalesced into one column: the mirror is a
    mirror, and collapsing them loses which field answered."""
    fetcher = FakeFetcher(_page([
        _device("D1", reference="1", name="From deviceName", trade_name="From tradeName"),
    ]))

    handle_eudamed_sync(conn, _job(), fetcher=fetcher)

    row = _mirror(conn)[0]
    assert row["device_name"] == "From deviceName"
    assert row["trade_name"] == "From tradeName"

def test_a_second_run_updates_rather_than_duplicates(conn):
    """`udi_di` is the primary key. EUDAMED is the authority on its own record,
    so a re-run overwrites; it must not raise and must not fork the row."""
    handle_eudamed_sync(conn, _job(), fetcher=FakeFetcher(
        _page([_device("D1", reference="7031", name="Old name")])))
    handle_eudamed_sync(conn, _job(), fetcher=FakeFetcher(
        _page([_device("D1", reference="7031", name="New name")])))

    rows = _mirror(conn)
    assert len(rows) == 1
    assert rows[0]["device_name"] == "New name"


def test_cert_refs_stays_empty_because_eudamed_returns_no_document_urls(conn):
    """Pinned deliberately. DISCOVER's eudamed rung reads `cert_refs`, and the
    2026-08-20 research measured that EUDAMED publishes no document URLs. A
    future reader who sees that rung still missing should find this test and
    know it is the measured truth, not a bug in this handler."""
    handle_eudamed_sync(conn, _job(), fetcher=FakeFetcher(
        _page([_device("D1", reference="7031")])))

    assert _mirror(conn)[0]["cert_refs"] is None


def test_a_device_without_a_reference_is_stored_and_counted(conn):
    """`reference` was populated on 100% of records measured — which is a
    measurement, not a guarantee. A missing one is reported, never silently
    dropped (CLAUDE.md: skipped rows are counted and reported)."""
    fetcher = FakeFetcher(_page([
        _device("D1", reference="7031"),
        {"primaryDi": "D2", "basicUdi": BUDI, "deviceName": "No ref"},
    ]))

    out = handle_eudamed_sync(conn, _job(), fetcher=fetcher)

    rows = _mirror(conn)
    assert len(rows) == 2
    assert out["counts"]["missing_reference"] == 1


def test_a_device_without_a_udi_di_is_skipped_and_counted(conn):
    """`udi_di` is the primary key; a record without one cannot be stored."""
    fetcher = FakeFetcher(_page([
        _device("D1", reference="7031"),
        {"basicUdi": BUDI, "reference": "9999"},
    ]))

    out = handle_eudamed_sync(conn, _job(), fetcher=fetcher)

    assert len(_mirror(conn)) == 1
    assert out["counts"]["skipped_no_udi_di"] == 1


# --------------------------------------------------------------------------- #
# failure
# --------------------------------------------------------------------------- #
def test_a_non_200_response_raises_so_the_queue_retries(conn):
    """The queue owns backoff and the dead-letter. A handler that swallowed a
    503 would report success for a group it never read."""
    fetcher = FakeFetcher(FetchResult(
        status=503, body=b"", etag=None, last_modified=None,
        content_type=None, final_url="https://ec.europa.eu/x"))

    with pytest.raises(Exception):
        handle_eudamed_sync(conn, _job(), fetcher=fetcher)

    assert _mirror(conn) == []


def test_a_body_that_is_not_json_raises(conn):
    fetcher = FakeFetcher(FetchResult(
        status=200, body=b"<html>maintenance</html>", etag=None,
        last_modified=None, content_type="text/html",
        final_url="https://ec.europa.eu/x"))

    with pytest.raises(Exception):
        handle_eudamed_sync(conn, _job(), fetcher=fetcher)


def test_an_empty_result_is_a_miss_not_an_error(conn):
    """52,3% of our Basic UDI-DIs resolve to nothing in EUDAMED (measured
    2026-08-20). That is the common case, not a failure."""
    out = handle_eudamed_sync(conn, _job(), fetcher=FakeFetcher(_page([])))

    assert out["counts"].get("devices", 0) == 0
    assert _mirror(conn) == []


# --------------------------------------------------------------------------- #
# invariant 1 — this handler is not a registry writer
# --------------------------------------------------------------------------- #
def test_the_handler_writes_no_link_and_no_document(conn):
    """Stage 1 ruling. `ref-eudamed` is capped at staged and nothing here
    creates one; the day that changes, this test is what fails."""
    before_docs = conn.execute("SELECT count(*) AS n FROM document").fetchone()["n"]
    before_links = conn.execute("SELECT count(*) AS n FROM item_document").fetchone()["n"]

    handle_eudamed_sync(conn, _job(), fetcher=FakeFetcher(
        _page([_device("D1", reference="7031")])))

    assert conn.execute("SELECT count(*) AS n FROM document").fetchone()["n"] == before_docs
    assert conn.execute(
        "SELECT count(*) AS n FROM item_document").fetchone()["n"] == before_links


def test_the_tag_is_no_longer_a_noop(conn):
    """`eudamed.sync` was a placeholder that raised. The runner dispatches
    through HANDLERS, so a real module must have overridden it."""
    from app.handlers import HANDLERS

    assert HANDLERS["eudamed.sync"] is not None
    assert HANDLERS["eudamed.sync"].__module__ == "app.handlers.eudamed"


# --------------------------------------------------------------------------- #
# the button — producer only, same posture as every other POST in web/
# --------------------------------------------------------------------------- #
from fastapi.testclient import TestClient  # noqa: E402

from app.config import Web  # noqa: E402
from tests.conftest import TEST_API_URL as _API_URL  # noqa: E402
from tests.fixtures.seed_ui import seed_document  # noqa: E402
from web.app import create_app  # noqa: E402


@pytest.fixture
def ui(test_db_url, tmp_path):
    return TestClient(
        create_app(Web(api_database_url=_API_URL, imports_dir=str(tmp_path))),
        follow_redirects=False,
    )


def _seed_doc(conn, *, budi=BUDI, h="h-eu-1"):
    doc_id = seed_document(
        conn, content_hash=h, archive_url="file:///eu.pdf",
        doc_type="DoC", status="production", basic_udi_di=budi,
    )
    conn.commit()
    return doc_id


def test_the_button_enqueues_a_eudamed_sync_job(conn, ui):
    doc_id = _seed_doc(conn)

    resp = ui.post(f"/documents/{doc_id}/eudamed")

    assert resp.status_code == 303
    assert resp.headers["location"] == f"/documents/{doc_id}?eudamed=queued"
    job = conn.execute(
        "SELECT type, payload, priority FROM job WHERE type='eudamed.sync'"
    ).fetchone()
    assert job["payload"]["basic_udi_di"] == BUDI
    assert job["priority"] == "interactive"


def test_a_second_press_on_the_same_day_is_deduped_not_duplicated(conn, ui):
    doc_id = _seed_doc(conn)

    ui.post(f"/documents/{doc_id}/eudamed")
    resp = ui.post(f"/documents/{doc_id}/eudamed")

    assert resp.headers["location"] == f"/documents/{doc_id}?eudamed=deduped"
    n = conn.execute(
        "SELECT count(*) AS n FROM job WHERE type='eudamed.sync'"
    ).fetchone()["n"]
    assert n == 1


def test_a_document_without_a_basic_udi_di_cannot_queue_a_lookup(conn, ui):
    """301 of 769 documents are in this state. Nothing to look up, so the POST
    refuses rather than enqueueing a job that can only no-op."""
    doc_id = _seed_doc(conn, budi=None, h="h-eu-none")

    resp = ui.post(f"/documents/{doc_id}/eudamed")

    assert resp.headers["location"] == f"/documents/{doc_id}?eudamed=no-udi"
    assert conn.execute(
        "SELECT count(*) AS n FROM job WHERE type='eudamed.sync'"
    ).fetchone()["n"] == 0


def test_the_button_shows_only_when_the_document_has_a_basic_udi_di(conn, ui):
    with_udi = _seed_doc(conn, h="h-eu-yes")
    without = _seed_doc(conn, budi=None, h="h-eu-no")
    ui2 = TestClient(ui.app)

    # The BUTTON's own target, not the bare string "/eudamed": since
    # 2026-09-03 the sidebar carries a /manufacturers/eudamed link on every
    # page, which contains that substring, so a bare assertion would pass on
    # the nav and prove nothing about the document.
    assert f"/documents/{with_udi}/eudamed" in ui2.get(f"/documents/{with_udi}").text
    assert f"/documents/{without}/eudamed" not in ui2.get(f"/documents/{without}").text


def test_the_page_lists_what_eudamed_returned(conn, ui):
    """The catalogue numbers are the whole point of the call, so they are what
    the page shows."""
    doc_id = _seed_doc(conn, h="h-eu-show")
    handle_eudamed_sync(conn, _job(), fetcher=FakeFetcher(_page([
        _device("D1", reference="7031", name="Bur A"),
        _device("D2", reference="7024", name="Bur B"),
    ])))
    conn.commit()

    body = TestClient(ui.app).get(f"/documents/{doc_id}").text
    assert "7031" in body
    assert "7024" in body
    assert "Bur A" in body


# --------------------------------------------------------------------------- #
# eudamed.certregister -- the whole register, matched locally
# --------------------------------------------------------------------------- #
# 4.608 certificates, 16 calls at size=300 (measured 2026-08-26). The pull is
# unfiltered on purpose: actorSrn arrives on every record, so SRN discovery is
# a by-product rather than a prerequisite, and matching happens in
# app/eudamed_names.py rather than through the endpoint's substring matcher.
from app.handlers.eudamed import (  # noqa: E402
    CERT_PAGE_SIZE, EUDAMED_CERT_URL, handle_eudamed_certregister,
)


def _cert(number, *, revision="Rev. 01", actor="LI-MF-000000522",
          actor_name="Ivoclar Vivadent AG", status="issued",
          ctype="quality-management-system", expiry="2031-05-04T00:00:00",
          issue="2026-06-12T00:00:00", nb="0123", version=1):
    return {
        "certificateNumber": number,
        "revisionNumber": revision,
        "actorSrn": actor,
        "actorName": actor_name,
        "certificateType": {"code": f"refdata.certificate-mdr-type.{ctype}"},
        "certificateStatus": {"code": f"refdata.certificate-status.{status}"},
        "issueDate": issue,
        "startingValidityDate": issue,
        "expiryDate": expiry,
        "notifiedBodySrn": nb,
        "versionNumber": version,
    }


def _seed_alias(conn, canonical, *raw_names):
    # `source` defaults to 'self-seed' (migration 017's CHECK only accepts
    # 'self-seed' | 'vendor-master' | 'playbook' -- the brief's literal 'test'
    # value would violate that constraint, so it is omitted here rather than
    # supplied).
    conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES (%s) "
        "ON CONFLICT DO NOTHING", (canonical,)
    )
    for raw in raw_names:
        conn.execute(
            "INSERT INTO manufacturer_alias (raw_name, canonical_name) "
            "VALUES (%s, %s) ON CONFLICT DO NOTHING", (raw, canonical)
        )


def test_the_register_pull_asks_for_the_biggest_page_the_server_gives(conn):
    """size=300 is the server's cap -- asking for 1.000 returns 300 (measured
    2026-08-26). At 20, the default, the register would be 231 calls."""
    fetcher = FakeFetcher(_page([_cert("G15 043306 0282")], last=True))

    handle_eudamed_certregister(conn, {"payload": {}}, fetcher=fetcher)

    assert f"size={CERT_PAGE_SIZE}" in fetcher.calls[0]
    assert CERT_PAGE_SIZE == 300
    assert fetcher.calls[0].startswith(EUDAMED_CERT_URL.split("?")[0])


def test_every_certificate_is_stored_whether_or_not_it_is_ours(conn):
    """The whole register is mirrored, not only our manufacturers' rows: 4.608
    rows is nothing, and "did a supplier register their first certificate"
    becomes answerable without a fresh fetch (Denis, 2026-08-26)."""
    fetcher = FakeFetcher(_page([
        _cert("G15 043306 0282"),
        _cert("Z-25-052-S-IX-E", actor="DE-MF-000006413",
              actor_name="AIRAmed GmbH", revision=None),
    ], last=True))

    res = handle_eudamed_certregister(conn, {"payload": {}}, fetcher=fetcher)

    n = conn.execute(
        "SELECT count(*) c FROM eudamed_certificate"
    ).fetchone()["c"]
    assert n == 2
    assert res["counts"]["certificates"] == 2


def test_a_null_revision_stores_as_empty_string(conn):
    """481 of 4.608 records carry no revisionNumber. They must not be dropped by
    a NOT NULL primary key."""
    fetcher = FakeFetcher(_page([
        _cert("Z-25-052-S-IX-E", revision=None)], last=True))

    handle_eudamed_certregister(conn, {"payload": {}}, fetcher=fetcher)

    row = conn.execute(
        "SELECT revision_number FROM eudamed_certificate"
    ).fetchone()
    assert row["revision_number"] == ""


def test_the_status_and_type_codes_are_stored_bare(conn):
    """`refdata.certificate-status.suspended` is stored as `suspended`. The
    prefix is EUDAMED's reference-data namespace, not information."""
    fetcher = FakeFetcher(_page([
        _cert("X1", status="suspended", ctype="technical-documentation")],
        last=True))

    handle_eudamed_certregister(conn, {"payload": {}}, fetcher=fetcher)

    row = conn.execute(
        "SELECT certificate_status, certificate_type FROM eudamed_certificate"
    ).fetchone()
    assert row["certificate_status"] == "suspended"
    assert row["certificate_type"] == "technical-documentation"


def test_an_exact_actor_name_stores_an_auto_srn(conn):
    _seed_alias(conn, "IVOCLAR", "Ivoclar Vivadent AG")
    fetcher = FakeFetcher(_page([_cert("G15 043306 0282")], last=True))

    handle_eudamed_certregister(conn, {"payload": {}}, fetcher=fetcher)

    row = conn.execute(
        "SELECT srn, status, discovered_via FROM manufacturer_srn "
        "WHERE canonical_name='IVOCLAR'"
    ).fetchone()
    assert row["srn"] == "LI-MF-000000522"
    assert row["status"] == "auto"
    assert row["discovered_via"] == "register-exact"


def test_a_fuzzy_actor_name_is_queued_not_swept(conn):
    """A wrong attribution becomes a wrong e-mail to a supplier. Only `auto` and
    `confirmed` are ever swept.

    `actor_name="GC Europa NV"` (not the brief's literal "GC Europe NV"):
    `_SUFFIXES` (app/eudamed_names.py, task 3) already strips a trailing "NV"
    as a legal-entity suffix, so "GC Europe NV" normalises identically to the
    seeded "GC EUROPE N.V." and scores an EXACT match (100.0, register-exact)
    -- verified directly against app.eudamed_names.match_actor. The one-letter
    typo here keeps the match fuzzy (score ~88.9, above FUZZY_FLOOR=88.0) so
    the test actually exercises the pending path its docstring describes."""
    _seed_alias(conn, "GC EUROPE N.V.", "GC EUROPE N.V.")
    fetcher = FakeFetcher(_page([
        _cert("Y1", actor="BE-MF-000001234", actor_name="GC Europa NV")],
        last=True))

    handle_eudamed_certregister(conn, {"payload": {}}, fetcher=fetcher)

    row = conn.execute(
        "SELECT status, discovered_via, match_score FROM manufacturer_srn "
        "WHERE canonical_name='GC EUROPE N.V.'"
    ).fetchone()
    assert row["status"] == "pending"
    assert row["discovered_via"] == "register-fuzzy"
    assert row["match_score"] is not None


def test_an_unmatched_actor_stores_no_srn(conn):
    """3.196 distinct actors; almost none of them are ours."""
    _seed_alias(conn, "IVOCLAR", "Ivoclar Vivadent AG")
    fetcher = FakeFetcher(_page([
        _cert("Z1", actor="DE-MF-000099999", actor_name="PAUL HARTMANN AG")],
        last=True))

    res = handle_eudamed_certregister(conn, {"payload": {}}, fetcher=fetcher)

    assert conn.execute(
        "SELECT count(*) c FROM manufacturer_srn"
    ).fetchone()["c"] == 0
    assert res["counts"]["unmatched_actors"] >= 1


def test_a_human_decision_is_never_overwritten_by_a_later_pull(conn):
    """The register is re-pulled monthly. A rejected candidate that came back as
    `pending` every month would be a queue nobody can clear."""
    _seed_alias(conn, "GC EUROPE N.V.", "GC EUROPE N.V.")
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, "
        " status, decided_at, decided_by) "
        "VALUES ('GC EUROPE N.V.', 'BE-MF-000001234', 'register-fuzzy', "
        "        'rejected', now(), 'natasa')"
    )
    fetcher = FakeFetcher(_page([
        _cert("Y1", actor="BE-MF-000001234", actor_name="GC Europe NV")],
        last=True))

    handle_eudamed_certregister(conn, {"payload": {}}, fetcher=fetcher)

    row = conn.execute(
        "SELECT status FROM manufacturer_srn WHERE srn='BE-MF-000001234'"
    ).fetchone()
    assert row["status"] == "rejected"


def test_first_seen_survives_a_second_pull(conn):
    """A new revision is a new row, so a preserved first_seen IS the renewal
    signal. If ON CONFLICT stomped it, the certificate watch would go blind."""
    fetcher = FakeFetcher(_page([_cert("G15 043306 0282")], last=True))
    handle_eudamed_certregister(conn, {"payload": {}}, fetcher=fetcher)
    before = conn.execute(
        "SELECT first_seen FROM eudamed_certificate"
    ).fetchone()["first_seen"]

    fetcher2 = FakeFetcher(_page([_cert("G15 043306 0282")], last=True))
    handle_eudamed_certregister(conn, {"payload": {}}, fetcher=fetcher2)
    after = conn.execute(
        "SELECT first_seen, synced_at FROM eudamed_certificate"
    ).fetchone()

    assert after["first_seen"] == before
    assert after["synced_at"] >= before


def test_the_pull_walks_every_page(conn):
    fetcher = FakeFetcher(
        _page([_cert("A1")], last=False),
        _page([_cert("B1", actor="XX-MF-1")], last=True),
    )

    handle_eudamed_certregister(conn, {"payload": {}}, fetcher=fetcher)

    assert len(fetcher.calls) == 2
    assert "page=0" in fetcher.calls[0]
    assert "page=1" in fetcher.calls[1]
    assert conn.execute(
        "SELECT count(*) c FROM eudamed_certificate"
    ).fetchone()["c"] == 2


def test_a_non_200_raises_rather_than_reporting_success(conn):
    """The queue owns backoff and the dead-letter. A swallowed 503 would report
    a complete register read that never happened.

    `content_type` and `final_url` added to the brief's literal `FetchResult`
    call: the dataclass (app/adapters/fetcher.py) has no defaults for either,
    matching every other non-200 `FetchResult` already in this file."""
    fetcher = FakeFetcher(FetchResult(status=503, body=b"", etag=None,
                                      last_modified=None, content_type=None,
                                      final_url="https://ec.europa.eu/x"))

    with pytest.raises(RuntimeError, match="503"):
        handle_eudamed_certregister(conn, {"payload": {}}, fetcher=fetcher)


def test_the_register_pull_writes_no_registry_row(conn):
    """Invariant 1. Asserted, not assumed."""
    _seed_alias(conn, "IVOCLAR", "Ivoclar Vivadent AG")
    before = {
        t: conn.execute(f"SELECT count(*) c FROM {t}").fetchone()["c"]
        for t in ("document", "item_document", "evidence")
    }
    fetcher = FakeFetcher(_page([_cert("G15 043306 0282")], last=True))

    handle_eudamed_certregister(conn, {"payload": {}}, fetcher=fetcher)

    after = {
        t: conn.execute(f"SELECT count(*) c FROM {t}").fetchone()["c"]
        for t in ("document", "item_document", "evidence")
    }
    assert after == before


def test_a_canonical_name_known_only_via_alias_gets_a_manufacturer_row(conn):
    """RULING 8 (Denis). Migration 042's `manufacturer` seed is a one-shot that
    runs at migrate time against `manufacturer_alias`; nothing re-populates
    `manufacturer` for aliases RESOLVE adds afterwards. `manufacturer_srn.
    canonical_name` carries a FK to `manufacturer(canonical_name)`, so a
    canonical name known only through `manufacturer_alias` -- no `manufacturer`
    row seeded here, deliberately, unlike `_seed_alias` -- must not raise a
    foreign-key violation when this handler tries to record an SRN for it."""
    conn.execute(
        "INSERT INTO manufacturer_alias (raw_name, canonical_name) "
        "VALUES ('Ivoclar Vivadent AG', 'IVOCLAR') ON CONFLICT DO NOTHING"
    )
    fetcher = FakeFetcher(_page([_cert("G15 043306 0282")], last=True))

    handle_eudamed_certregister(conn, {"payload": {}}, fetcher=fetcher)

    assert conn.execute(
        "SELECT count(*) c FROM manufacturer WHERE canonical_name='IVOCLAR'"
    ).fetchone()["c"] == 1
    assert conn.execute(
        "SELECT count(*) c FROM manufacturer_srn WHERE canonical_name='IVOCLAR'"
    ).fetchone()["c"] == 1


def test_a_malformed_date_drops_the_field_not_the_register(conn):
    """One bad field must cost one field. `app/workers/runner.py` wraps the whole
    handler in a single transaction, so a value Postgres rejects would roll back
    every row the run had already written and dead-letter the entire pull."""
    fetcher = FakeFetcher(_page([
        _cert("GOOD1"),
        _cert("BAD1", expiry="not-a-real-date-value"),
    ], last=True))

    res = handle_eudamed_certregister(conn, {"payload": {}}, fetcher=fetcher)

    assert res["counts"]["certificates"] == 2
    assert res["counts"]["unparseable_date"] == 1
    row = conn.execute(
        "SELECT expiry_date FROM eudamed_certificate "
        "WHERE certificate_number = 'BAD1'").fetchone()
    assert row["expiry_date"] is None
    assert conn.execute(
        "SELECT count(*) c FROM eudamed_certificate").fetchone()["c"] == 2


# --------------------------------------------------------------------------- #
# eudamed.sweep -- one manufacturer's registered catalogue
# --------------------------------------------------------------------------- #
from app.handlers.eudamed import EUDAMED_SRN_URL, handle_eudamed_sweep


def test_the_sweep_uses_srn_which_cannot_return_another_company(conn):
    """`reference=` is a substring matcher and `actorName=` is too. `srn=` is
    neither: it returned exactly 3.594 devices for Carl Martin's
    DE-MF-000005066 (measured 2026-08-26), matching the independent count."""
    _sweepable(conn, "CARL MARTIN", "DE-MF-000005066")
    fetcher = FakeFetcher(_page([
        _device("D1", reference="1845", srn="DE-MF-000005066")], last=True))

    handle_eudamed_sweep(conn, {"payload": {"manufacturer": "CARL MARTIN"}},
                         fetcher=fetcher)

    assert "srn=DE-MF-000005066" in fetcher.calls[0]
    assert "reference=" not in fetcher.calls[0]


def test_a_record_carrying_someone_elses_srn_raises(conn):
    """Layer 3 of the wrong-attribution mitigation. If the filter stops holding,
    the sweep must fail loudly rather than mirror a stranger's catalogue."""
    _sweepable(conn, "CARL MARTIN", "DE-MF-000005066")
    fetcher = FakeFetcher(_page([
        _device("D1", reference="1845", srn="XX-MF-000000001")], last=True))

    with pytest.raises(RuntimeError, match="DE-MF-000005066"):
        handle_eudamed_sweep(conn, {"payload": {"manufacturer": "CARL MARTIN"}},
                             fetcher=fetcher)


def test_a_record_that_does_not_echo_the_srn_is_taken_at_the_querys_word(conn):
    """`_assert_filtered` treats an omitted `basicUdi` as absence rather than
    contradiction, and the design doc (§5.2) says this guard uses the same
    shape. It matters more here: runner.py wraps the handler in ONE
    transaction, so raising on a blank field would roll back the whole
    manufacturer's sweep -- every SRN, every page already fetched -- over one
    unpopulated field on an API that leaves `deviceName` null on 121 of 121
    records and `revisionNumber` null on 481 of 4.608. Counted, never silent."""
    _sweepable(conn, "CARL MARTIN", "DE-MF-000005066")
    d = _device("D1", reference="1845", srn="DE-MF-000005066")
    d.pop("manufacturerSrn")
    fetcher = FakeFetcher(_page([d], last=True))

    res = handle_eudamed_sweep(conn, {"payload": {"manufacturer": "CARL MARTIN"}},
                               fetcher=fetcher)

    row = conn.execute(
        "SELECT manufacturer_srn FROM eudamed_mirror WHERE udi_di='D1'"
    ).fetchone()
    assert row["manufacturer_srn"] == "DE-MF-000005066"
    assert res["counts"].get("srn_not_echoed") == 1


def test_a_pending_srn_is_not_swept(conn):
    """Only auto and confirmed attributions are trusted."""
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('PENDCO') "
                 "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, "
        " status) VALUES ('PENDCO','XX-MF-9','register-fuzzy','pending')")
    fetcher = FakeFetcher()

    res = handle_eudamed_sweep(conn, {"payload": {"manufacturer": "PENDCO"}},
                               fetcher=fetcher)

    assert fetcher.calls == []
    assert res["counts"].get("no_trusted_srn") == 1


def test_a_no_trusted_srn_exit_clears_a_stale_release(conn):
    """Final review, important #3: the only statement that ever cleared
    `released_at` was the successful-sweep exit at the end of this handler.
    A manufacturer released while it still held a trusted SRN, whose SRN is
    then rejected before the sweep runs, hits the `no_trusted_srn` exit --
    which left `released_at` set. `eudamed_sweep_due` and the scheduler's own
    tick both exclude `released_at IS NOT NULL`, so that row would vanish
    from the due list and never be proposed again, with no dead job and
    nothing to re-run. `srn_probed_at` is pre-stamped so the handler takes the
    direct `no_trusted_srn` exit rather than the article-probe branch, which
    is not what this test is about."""
    conn.execute(
        "INSERT INTO manufacturer (canonical_name, srn_probed_at) "
        "VALUES ('REJECTCO', now()) ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, "
        " status) VALUES ('REJECTCO', 'XX-MF-REJ', 'register-fuzzy', 'rejected')")
    conn.execute(
        "INSERT INTO eudamed_sweep_state (canonical_name, due_at, released_at, "
        " released_by) VALUES ('REJECTCO', now(), now(), 'ui')")

    res = handle_eudamed_sweep(conn, {"payload": {"manufacturer": "REJECTCO"}},
                               fetcher=FakeFetcher())

    assert res["counts"]["no_trusted_srn"] == 1
    row = conn.execute(
        "SELECT released_at, released_by, due_at FROM eudamed_sweep_state "
        "WHERE canonical_name = 'REJECTCO'").fetchone()
    assert row["released_at"] is None
    assert row["released_by"] is None
    # due_at is untouched -- the row must be visible again on the sweep-due
    # list, not merely un-stuck.
    assert row["due_at"] is not None


def test_first_seen_survives_a_second_sweep(conn):
    """The delta is the whole point of sweeping twice."""
    _sweepable(conn, "CARL MARTIN", "DE-MF-000005066")
    handle_eudamed_sweep(conn, {"payload": {"manufacturer": "CARL MARTIN"}},
                         fetcher=FakeFetcher(_page([
                             _device("D1", reference="1845",
                                     srn="DE-MF-000005066")], last=True)))
    before = conn.execute(
        "SELECT first_seen FROM eudamed_mirror WHERE udi_di='D1'"
    ).fetchone()["first_seen"]

    handle_eudamed_sweep(conn, {"payload": {"manufacturer": "CARL MARTIN"}},
                         fetcher=FakeFetcher(_page([
                             _device("D1", reference="1845",
                                     srn="DE-MF-000005066")], last=True)))

    after = conn.execute(
        "SELECT first_seen FROM eudamed_mirror WHERE udi_di='D1'"
    ).fetchone()["first_seen"]
    assert after == before


def test_the_device_status_is_stored(conn):
    _sweepable(conn, "CARL MARTIN", "DE-MF-000005066")
    fetcher = FakeFetcher(_page([{
        **_device("D1", reference="1845", srn="DE-MF-000005066"),
        "deviceStatusType": {"code": "refdata.device-status.on-the-market"},
    }], last=True))

    handle_eudamed_sweep(conn, {"payload": {"manufacturer": "CARL MARTIN"}},
                         fetcher=fetcher)

    row = conn.execute(
        "SELECT device_status_type FROM eudamed_mirror WHERE udi_di='D1'"
    ).fetchone()
    assert row["device_status_type"] == "on-the-market"


def test_a_finished_sweep_clears_the_release_and_stamps_the_state(conn):
    """Releasing is a one-shot. A sweep that left released_at set would never
    become due again."""
    _sweepable(conn, "CARL MARTIN", "DE-MF-000005066")
    conn.execute(
        "INSERT INTO eudamed_sweep_state (canonical_name, due_at, released_at, "
        " released_by) VALUES ('CARL MARTIN', now(), now(), 'ui') "
        "ON CONFLICT (canonical_name) DO UPDATE SET released_at = now()")

    handle_eudamed_sweep(conn, {"payload": {"manufacturer": "CARL MARTIN"}},
                         fetcher=FakeFetcher(_page([
                             _device("D1", reference="1845",
                                     srn="DE-MF-000005066")], last=True)))

    row = conn.execute(
        "SELECT last_swept_at, released_at, due_at FROM eudamed_sweep_state "
        "WHERE canonical_name = 'CARL MARTIN'").fetchone()
    assert row["last_swept_at"] is not None
    assert row["released_at"] is None



def test_an_empty_sweep_does_not_stamp_itself_as_swept(conn):
    """The live failure of 2026-09-02. Job 35679 swept IVOCLAR, stored NOTHING
    -- the 544 mirror rows still carried `synced_at` from 2026-08-25 -- and
    still finished `done` with an empty result and `last_swept_at` set to now.

    The path: page 0 came back with no content, so `_is_last` broke the loop
    before the body ran, and the stamp at the end fired unconditionally. The
    effect is worse than the failure it hides -- a stamped manufacturer drops
    off the due list for `eudamed_sweep_interval_days` (90), so a sweep that
    did nothing buys three months of silence.

    EUDAMED holding no devices for a trusted SRN is not a real outcome: the SRN
    was trusted because it came off a certificate or a gated article probe.
    Zero devices means the call failed in a way the status code did not show.
    """
    _sweepable(conn, "CARL MARTIN", "DE-MF-000005066")
    conn.execute(
        "INSERT INTO eudamed_sweep_state (canonical_name, due_at, released_at, "
        " released_by) VALUES ('CARL MARTIN', now(), now(), 'ui') "
        "ON CONFLICT (canonical_name) DO UPDATE SET released_at = now()")

    out = handle_eudamed_sweep(conn, {"payload": {"manufacturer": "CARL MARTIN"}},
                               fetcher=FakeFetcher(_page([], last=True)))

    row = conn.execute(
        "SELECT last_swept_at, due_at, released_at FROM eudamed_sweep_state "
        "WHERE canonical_name = 'CARL MARTIN'").fetchone()
    assert row["last_swept_at"] is None, "an empty sweep must not read as swept"
    assert row["due_at"] is not None, "it must stay on the due list"
    assert out["counts"].get("empty_sweep") == 1
    assert any("stored no devices" in n for n in out["notes"])


def test_an_empty_sweep_still_clears_the_release(conn):
    """The release is a one-shot regardless. Leaving `released_at` set would
    make the row look queued forever and the due-list tick would skip it."""
    _sweepable(conn, "CARL MARTIN", "DE-MF-000005066")
    conn.execute(
        "INSERT INTO eudamed_sweep_state (canonical_name, due_at, released_at, "
        " released_by) VALUES ('CARL MARTIN', now(), now(), 'ui') "
        "ON CONFLICT (canonical_name) DO UPDATE SET released_at = now()")

    handle_eudamed_sweep(conn, {"payload": {"manufacturer": "CARL MARTIN"}},
                         fetcher=FakeFetcher(_page([], last=True)))

    row = conn.execute(
        "SELECT released_at FROM eudamed_sweep_state "
        "WHERE canonical_name = 'CARL MARTIN'").fetchone()
    assert row["released_at"] is None


def test_a_sweep_that_stored_devices_still_stamps(conn):
    """The guard must not break the working path."""
    _sweepable(conn, "CARL MARTIN", "DE-MF-000005066")

    out = handle_eudamed_sweep(conn, {"payload": {"manufacturer": "CARL MARTIN"}},
                               fetcher=FakeFetcher(_page([
                                   _device("D1", reference="1845",
                                           srn="DE-MF-000005066")], last=True)))

    row = conn.execute(
        "SELECT last_swept_at FROM eudamed_sweep_state "
        "WHERE canonical_name = 'CARL MARTIN'").fetchone()
    assert row["last_swept_at"] is not None
    assert out["counts"]["devices"] == 1

def test_the_sweep_writes_no_registry_row(conn):
    """Invariant 1."""
    _sweepable(conn, "CARL MARTIN", "DE-MF-000005066")
    before = {
        t: conn.execute(f"SELECT count(*) c FROM {t}").fetchone()["c"]
        for t in ("document", "item_document", "evidence")
    }
    handle_eudamed_sweep(conn, {"payload": {"manufacturer": "CARL MARTIN"}},
                         fetcher=FakeFetcher(_page([
                             _device("D1", reference="1845",
                                     srn="DE-MF-000005066")], last=True)))
    after = {
        t: conn.execute(f"SELECT count(*) c FROM {t}").fetchone()["c"]
        for t in ("document", "item_document", "evidence")
    }
    assert after == before


# --------------------------------------------------------------------------- #
# Article-probe SRN fallback
# --------------------------------------------------------------------------- #
# `reference=` is a SUBSTRING match, measured live: `reference=64` returns
# 62.918 records, and an ungated sample returned Promedics Orthopaedics, PAUL
# HARTMANN and Cerascreen records for Dentalia article numbers. Bootstrapping an
# SRN off one of those would sweep a stranger's whole catalogue into the mirror
# and generate a gap list for articles that are not ours.
from app.handlers.eudamed import probe_srn  # noqa: E402


def test_the_probe_accepts_only_an_exact_reference_and_a_matching_name(conn):
    _seed_item(conn, item_ref="531505", canonical="IVOCLAR")
    _seed_alias(conn, "IVOCLAR", "Ivoclar Vivadent AG")
    fetcher = FakeFetcher(_page([
        {**_device("D0", reference="0531505"), "manufacturerName": "Someone Else",
         "manufacturerSrn": "XX-MF-000000001"},
        {**_device("D1", reference="531505"), "manufacturerName": "Ivoclar Vivadent AG",
         "manufacturerSrn": "LI-MF-000000522"},
    ], last=True))

    assert probe_srn(conn, "IVOCLAR", fetcher=fetcher) == "LI-MF-000000522"


def test_a_substring_hit_from_another_company_is_refused(conn):
    """The whole record set for `reference=64` is 62.918 rows."""
    _seed_item(conn, item_ref="64", canonical="CARL MARTIN")
    _seed_alias(conn, "CARL MARTIN", "Carl Martin GmbH")
    fetcher = FakeFetcher(_page([
        {**_device("D1", reference="00128-64"), "manufacturerName": "PAUL HARTMANN AG",
         "manufacturerSrn": "DE-MF-000099999"},
    ], last=True))

    assert probe_srn(conn, "CARL MARTIN", fetcher=fetcher) is None
    assert conn.execute(
        "SELECT count(*) c FROM manufacturer_srn").fetchone()["c"] == 0


def test_an_exact_reference_from_the_wrong_manufacturer_is_refused(conn):
    """Article numbers are not globally unique. Exactness alone is not enough."""
    _seed_item(conn, item_ref="1845", canonical="CARL MARTIN")
    _seed_alias(conn, "CARL MARTIN", "Carl Martin GmbH")
    fetcher = FakeFetcher(_page([
        {**_device("D1", reference="1845"), "manufacturerName": "PAUL HARTMANN AG",
         "manufacturerSrn": "DE-MF-000099999"},
    ], last=True))

    assert probe_srn(conn, "CARL MARTIN", fetcher=fetcher) is None


def test_a_probe_that_found_nothing_is_recorded_so_it_does_not_repeat(conn):
    """375 of our 384 canonical names are not device manufacturers and will
    never resolve. Without this stamp the fallback re-probes them forever."""
    _seed_item(conn, item_ref="9999", canonical="PAPERCO")
    _seed_alias(conn, "PAPERCO", "Paper Co")
    fetcher = FakeFetcher(_page([], last=True))

    probe_srn(conn, "PAPERCO", fetcher=fetcher)

    row = conn.execute(
        "SELECT srn_probed_at FROM manufacturer WHERE canonical_name='PAPERCO'"
    ).fetchone()
    assert row["srn_probed_at"] is not None


def test_the_article_that_produced_the_srn_is_recorded(conn):
    """A wrong bootstrap must be auditable after the fact, not mysterious."""
    _seed_item(conn, item_ref="531505", canonical="IVOCLAR")
    _seed_alias(conn, "IVOCLAR", "Ivoclar Vivadent AG")
    fetcher = FakeFetcher(_page([
        {**_device("D1", reference="531505"), "manufacturerName": "Ivoclar Vivadent AG",
         "manufacturerSrn": "LI-MF-000000522"},
    ], last=True))

    probe_srn(conn, "IVOCLAR", fetcher=fetcher)

    row = conn.execute(
        "SELECT srn, discovered_via, probe_ref, status FROM manufacturer_srn"
    ).fetchone()
    assert row["discovered_via"] == "article-probe"
    assert row["probe_ref"] == "531505"
    assert row["status"] == "auto"


def test_gate_2_passing_is_not_sufficient_without_an_exact_reference(conn):
    """The other three refusal tests above all pair their non-matching
    reference with a manufacturer name that ALSO fails gate 2 (a stranger's
    name), so none of them can tell gate 1 apart from gate 2 doing all the
    work alone. This one isolates gate 1: `manufacturerName` is an exact,
    normalised match for CARL MARTIN (clears gate 2 via EXACT_VIA on its own),
    but `reference="0064"` is only a substring hit against the seeded article
    `64`, not an equal one. Without gate 1, this record would be accepted."""
    _seed_item(conn, item_ref="64", canonical="CARL MARTIN")
    _seed_alias(conn, "CARL MARTIN", "Carl Martin GmbH")
    fetcher = FakeFetcher(_page([
        {**_device("D1", reference="0064"), "manufacturerName": "Carl Martin GmbH",
         "manufacturerSrn": "DE-MF-000005066"},
    ], last=True))

    assert probe_srn(conn, "CARL MARTIN", fetcher=fetcher) is None
    assert conn.execute(
        "SELECT count(*) c FROM manufacturer_srn").fetchone()["c"] == 0


# --------------------------------------------------------------------------- #
# wiring: the sweep falls back to the probe when nothing is trusted yet
# --------------------------------------------------------------------------- #
def test_the_sweep_probes_for_an_srn_when_none_is_trusted(conn):
    """GC EUROPE: 356 device articles, zero certificates. The probe is the only
    route left to a first sweep, so the sweep must try it before giving up."""
    _seed_item(conn, item_ref="531505", canonical="IVOCLAR")
    _seed_alias(conn, "IVOCLAR", "Ivoclar Vivadent AG")
    fetcher = FakeFetcher(
        _page([{**_device("D0", reference="531505"),
                 "manufacturerName": "Ivoclar Vivadent AG",
                 "manufacturerSrn": "LI-MF-000000522"}], last=True),
        _page([_device("D1", reference="1845", srn="LI-MF-000000522")], last=True),
    )

    res = handle_eudamed_sweep(conn, {"payload": {"manufacturer": "IVOCLAR"}},
                               fetcher=fetcher)

    assert "reference=531505" in fetcher.calls[0]
    assert "srn=LI-MF-000000522" in fetcher.calls[1]
    assert res["counts"]["devices"] == 1
    assert res["counts"]["probe_bootstrapped_srn"] == 1
    assert conn.execute(
        "SELECT count(*) c FROM manufacturer_srn WHERE canonical_name='IVOCLAR'"
    ).fetchone()["c"] == 1


def test_a_probe_that_finds_nothing_is_counted_and_not_repeated(conn):
    """Most canonical names hold no device catalogue at all. The stamp from
    the first probe must stop a second sweep from re-asking EUDAMED."""
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('PAPERCO') "
                 "ON CONFLICT DO NOTHING")
    _seed_item(conn, item_ref="9999", canonical="PAPERCO")
    fetcher = FakeFetcher(_page([], last=True))

    res = handle_eudamed_sweep(conn, {"payload": {"manufacturer": "PAPERCO"}},
                               fetcher=fetcher)

    assert res["counts"]["no_trusted_srn"] == 1
    assert res["counts"]["probe_found_nothing"] == 1
    assert len(fetcher.calls) == 1

    fetcher2 = FakeFetcher()
    res2 = handle_eudamed_sweep(conn, {"payload": {"manufacturer": "PAPERCO"}},
                                fetcher=fetcher2)

    assert fetcher2.calls == []
    assert res2["counts"]["no_trusted_srn"] == 1
    assert "probe_found_nothing" not in res2["counts"]


# --------------------------------------------------------------------------- #
# An empty page that contradicts its own envelope is a throttle, not an end.
#
# Reproduced live 2026-09-02 by interleaving the sweep's own URL across the
# host and the worker container: one round returned HTTP 200 with a valid
# Spring envelope whose `content` was `[]`, and the rounds after it returned
# HTTP 307 with an HTML "Network Error" page from BOTH positions in the same
# second. So EUDAMED degrades under load to an empty page before it degrades
# to a block, and `_is_last` read that empty page as a clean end of pagination
# -- which is how jobs 35679 and 35701 swept IVOCLAR, stored nothing, and
# reported success.
#
# The envelope carries enough to tell the two apart without guessing: a real
# last page agrees with itself (`last: true`, or a `number` that has reached
# `totalPages - 1`), while a throttled one says there are 11.366 elements
# across 38 pages and then hands over nothing.
# --------------------------------------------------------------------------- #

def test_an_empty_page_contradicting_the_envelope_is_an_error():
    body = {
        "content": [],
        "number": 0,
        "size": 300,
        "totalElements": 11366,
        "totalPages": 38,
        "last": False,
    }
    with pytest.raises(RuntimeError, match="empty"):
        _is_last(body, [])


def test_a_genuinely_empty_result_is_still_a_last_page():
    """`totalElements: 0` is a real answer -- a manufacturer with no devices
    registered. It must not raise, or a legitimately empty sweep dead-letters."""
    body = {"content": [], "number": 0, "size": 300,
            "totalElements": 0, "totalPages": 0, "last": True}
    assert _is_last(body, []) is True


def test_the_true_last_page_of_a_walk_is_still_last():
    body = {"content": [], "number": 37, "size": 300,
            "totalElements": 11366, "totalPages": 38, "last": True}
    assert _is_last(body, []) is True


def test_an_unreadable_body_still_ends_the_walk():
    """A bare list or a malformed envelope carries no `last` and no counts.
    That path predates this guard and must keep ending the walk rather than
    raising -- without it, a body we cannot read pages forever."""
    assert _is_last([], []) is True
    assert _is_last({"content": []}, []) is True


# --------------------------------------------------------------------------- #
# Paging must be polite. The sweep loop fetched its pages back to back with no
# delay at all -- 38 requests to `ec.europa.eu` as fast as the network allowed
# -- and on 2026-09-02 that host started answering HTTP 307 with an HTML
# "Network Error" page, to the host and the worker container alike, after a
# couple of clean rounds. It recovered on its own once the requests stopped,
# which is the signature of rate-based throttling rather than a block.
#
# `cfg.fetch.politeness_ms` is the project's existing answer to this; FETCH
# spends it through the domain lease. A multi-page walk inside ONE job cannot
# use the lease the same way (the lease path defers the job, and this handler
# runs its whole walk in one transaction), so the pause is taken in-process
# between pages.
# --------------------------------------------------------------------------- #

def test_the_sweep_pauses_between_pages(conn, monkeypatch):
    pauses = []
    monkeypatch.setattr(eudamed, "_pause_between_pages",
                        lambda: pauses.append(1))
    _sweepable(conn, "CARL MARTIN", "DE-MF-000005066")
    fetcher = FakeFetcher(
        _page([_device("D1", reference="1", srn="DE-MF-000005066")],
              total=3, last=False, number=0),
        _page([_device("D2", reference="2", srn="DE-MF-000005066")],
              total=3, last=False, number=1),
        _page([_device("D3", reference="3", srn="DE-MF-000005066")],
              total=3, last=True, number=2),
    )

    handle_eudamed_sweep(conn, {"payload": {"manufacturer": "CARL MARTIN"}},
                         fetcher=fetcher)

    assert len(fetcher.calls) == 3
    # Between pages, not before the first and not after the last.
    assert len(pauses) == 2


def test_a_single_page_sweep_does_not_pause(conn, monkeypatch):
    pauses = []
    monkeypatch.setattr(eudamed, "_pause_between_pages",
                        lambda: pauses.append(1))
    _sweepable(conn, "CARL MARTIN", "DE-MF-000005066")
    fetcher = FakeFetcher(_page(
        [_device("D1", reference="1", srn="DE-MF-000005066")], last=True))

    handle_eudamed_sweep(conn, {"payload": {"manufacturer": "CARL MARTIN"}},
                         fetcher=fetcher)

    assert pauses == []


def test_the_pause_is_the_configured_politeness(monkeypatch):
    """Not a hard-coded constant: the operator must be able to slow this down
    without a code change, which is the lever if the 307s come back."""
    slept = []
    monkeypatch.setattr(eudamed.time, "sleep", lambda s: slept.append(s))

    _REAL_PAUSE()

    assert slept and slept[0] == pytest.approx(
        load_config().fetch.politeness_ms / 1000.0)
