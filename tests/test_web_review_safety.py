"""P1a Review safety (spec § 3, decision D5).

Three rails on the one screen where a click writes the registry:

* A collapsed Review row offers **Open** and nothing else. Approve, Reject and
  the manufacturer binding exist only in the open panel, next to the document.
* A whole-range approval (`bind-manufacturer`) is two-step on the server,
  through Task 0's generic confirm. The first POST writes nothing and names the
  count; only the POST carrying `confirm=1` enqueues.
* Reject asks why. The reason is one of six fixed strings, travels on
  `gate.apply` as the optional `note`, and a reject without one is a 422.

The web process stays a producer throughout: every assertion about a write is
an assertion about a `job` row, never about the registry.
"""

from __future__ import annotations

import json
import pathlib
import re
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient
from markupsafe import escape

from app.config import Web
from tests.conftest import TEST_API_URL
from web.app import create_app

_TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "web" / "templates"

#: Spec § 3, in order. Restated here rather than imported so the test fails if
#: the module's tuple drifts from the spec, instead of agreeing with itself.
SPEC_REASONS = (
    "Wrong manufacturer",
    "Not one of our items",
    "Not a compliance document",
    "Out of date",
    "Duplicate of another document",
    "Other",
)
RECORDED_LINE = ("A rejected document stays on file and can be reopened. "
                 "Your name and the reason are recorded.")


@pytest.fixture
def client(test_db_url, tmp_path):
    return TestClient(create_app(Web(api_database_url=TEST_API_URL,
                                     imports_dir=str(tmp_path))))


def _jobs(conn, dedupe_key: str) -> list[dict]:
    return conn.execute(
        "SELECT * FROM job WHERE dedupe_key=%s ORDER BY id", (dedupe_key,)
    ).fetchall()


def _gate_apply_count(conn) -> int:
    return conn.execute(
        "SELECT count(*) AS n FROM job WHERE type='gate.apply'").fetchone()["n"]


def _demo(conn) -> dict:
    from tests.fixtures import seed_ui

    ids = seed_ui.seed_demo(conn)
    conn.commit()
    return ids


def _seed_whole_range_candidate(conn, doc_type: str = "DoC") -> int:
    """A manufacturer-scope binding candidate whose payload names a manufacturer
    the catalogue resolves, so the panel offers the one-click bind with a count.
    Three medical-device items count; the fourth is not a device and must not."""
    from tests.fixtures import seed_ui

    for ref in ("rsafe-1", "rsafe-2", "rsafe-3"):
        seed_ui.seed_item(conn, ref, manufacturer_raw="RSAFE", md_flag=True)
    seed_ui.seed_item(conn, "rsafe-4", manufacturer_raw="RSAFE", md_flag=False)
    conn.execute(
        "INSERT INTO manufacturer_alias (raw_name, canonical_name, source) "
        "VALUES ('RSAFE', 'RSAFE', 'vendor-master')")
    doc_id = seed_ui.seed_document(
        conn, content_hash="h-rsafe-1", archive_url="/archive/h-rsafe-1.pdf",
        doc_type=doc_type, regulation="MDR", coverage_scope="manufacturer",
        validity_to=None,
    )
    seed_ui.seed_manual_task(
        conn, kind="gate-manual", doc_id=doc_id, group_id=None,
        payload={"route": "mfr-binding", "manufacturer": "RSAFE"},
    )
    conn.commit()
    return doc_id


def _row(text: str, doc_id: int) -> str:
    """One review row, from its opening tag to the end of its disclosure."""
    m = re.search(rf'<div class="stage-row[^"]*" id="doc-{doc_id}">.*?</details>',
                  text, re.S)
    assert m, f"row for doc {doc_id} not rendered"
    return m.group(0)


# --------------------------------------------------------------------------- #
# D5: decide only with the document open
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", ["/staging", "/staging/docs"])
def test_a_collapsed_row_offers_open_and_no_decision(client, conn, path):
    ids = _demo(conn)
    bind_doc = _seed_whole_range_candidate(conn)
    docs = (ids["staged_doc"], ids["manual_doc"], ids["mfr_doc"], bind_doc)

    text = client.get(path).text

    for doc_id in docs:
        assert f'/staging/{doc_id}/apply' not in text
        assert re.search(r">\s*Open\s*<", _row(text, doc_id)), doc_id
    # Exact attribute values: `value="reject-link"` on the staged-links table
    # is a different decision on a different route and may stay.
    assert 'value="approve"' not in text
    assert 'value="reject"' not in text
    assert 'value="bind-manufacturer"' not in text


def test_the_open_panel_holds_approve_and_reject(client, conn):
    ids = _demo(conn)

    text = client.get(f"/staging/{ids['staged_doc']}/detail").text

    assert f'hx-post="/staging/{ids["staged_doc"]}/apply"' in text
    assert 'value="approve"' in text
    assert 'value="reject"' in text


def test_the_open_panel_holds_the_whole_range_approval_and_its_confirm_slot(
    client, conn
):
    doc_id = _seed_whole_range_candidate(conn)

    text = client.get(f"/staging/{doc_id}/detail").text

    assert 'value="bind-manufacturer"' in text
    assert 'value="reject"' in text
    # The confirm answers into a slot inside this panel, not over the whole
    # row: Cancel clears only the slot, so the panel survives a "no".
    assert f'id="bind-confirm-{doc_id}"' in text
    assert 'hx-target="next .bind-confirm"' in text


# --------------------------------------------------------------------------- #
# bind-manufacturer is two-step
# --------------------------------------------------------------------------- #
def test_whole_range_approval_first_post_returns_the_confirm_and_writes_nothing(
    client, conn
):
    doc_id = _seed_whole_range_candidate(conn)
    detail = client.get(f"/staging/{doc_id}/detail").text
    # N is whatever the panel's button already shows (`bind_items`), so the
    # question and the button can never quote two different counts.
    shown = re.search(r"Approve for all RSAFE items \((\d+) items?\)", detail)
    assert shown, "the panel's whole-range button should state its count"
    n = int(shown.group(1))
    assert n == 3           # the non-device item is not counted

    resp = client.post(f"/staging/{doc_id}/apply",
                       data={"decision": "bind-manufacturer", "manufacturer": "RSAFE"})

    assert resp.status_code == 200
    assert f"Approve for all {n} RSAFE items?" in resp.text
    assert f"Yes, approve for {n} items" in resp.text
    assert _jobs(conn, f"apply:{doc_id}:bind-manufacturer") == []
    assert _gate_apply_count(conn) == 0


@pytest.mark.parametrize("doc_type,word", [
    ("DoC", "declaration of conformity"),
    ("EC", "EC certificate"),
])
def test_whole_range_confirm_names_the_document_by_its_own_type(
    client, conn, doc_type, word
):
    """Fix round 1, F1 (controller ruling). The spec's copy said "declaration"
    whatever the document was, and on dev 25 of 34 waiting whole-range
    documents, and all 14 ever bound, are EC or ISO certificates. The word is
    the panel's own type label (`DOC_TYPE_LABELS`), set inside a sentence."""
    doc_id = _seed_whole_range_candidate(conn, doc_type=doc_type)

    text = client.post(f"/staging/{doc_id}/apply",
                       data={"decision": "bind-manufacturer",
                             "manufacturer": "RSAFE"}).text

    assert str(escape(
        f"This {word} says it covers everything RSAFE makes, so approving it "
        f"makes it count for every RSAFE item Business Central marks as a medical "
        f"device.")) in text
    assert f"3 items will show this {word}." in text
    assert ("It cannot be undone from this screen. "
            f"A newer {word} can replace it later.") in text
    if doc_type == "EC":
        assert "declaration" not in text


def test_whole_range_confirm_on_an_other_document_says_document(client, conn):
    doc_id = _seed_whole_range_candidate(conn, doc_type="other")

    text = client.post(f"/staging/{doc_id}/apply",
                       data={"decision": "bind-manufacturer",
                             "manufacturer": "RSAFE"}).text

    assert "This document says it covers everything RSAFE makes" in text


def test_whole_range_confirm_leaves_out_the_item_card_phrase(client, conn):
    doc_id = _seed_whole_range_candidate(conn)

    text = client.post(f"/staging/{doc_id}/apply",
                       data={"decision": "bind-manufacturer",
                             "manufacturer": "RSAFE"}).text

    # Left out on purpose (spec § 3 makes it conditional). The item-card page
    # does list manufacturer-bound declarations, but a BC card only links to it
    # once the BC push has written `pteWarehouseURL`, which has never happened
    # on dev. Flip this when it has.
    assert "item card" not in text


def test_whole_range_confirm_posts_back_the_same_decision_into_the_slot(
    client, conn
):
    doc_id = _seed_whole_range_candidate(conn)

    text = client.post(f"/staging/{doc_id}/apply",
                       data={"decision": "bind-manufacturer",
                             "manufacturer": "RSAFE"}).text

    assert f'hx-post="/staging/{doc_id}/apply"' in text
    assert f'hx-target="#bind-confirm-{doc_id}"' in text
    assert '<input type="hidden" name="decision" value="bind-manufacturer">' in text
    assert '<input type="hidden" name="manufacturer" value="RSAFE">' in text
    assert '<input type="hidden" name="confirm" value="1">' in text


def test_whole_range_approval_confirmed_enqueues_exactly_one_job(client, conn):
    doc_id = _seed_whole_range_candidate(conn)
    data = {"decision": "bind-manufacturer", "manufacturer": "RSAFE"}

    client.post(f"/staging/{doc_id}/apply", data=data)          # step one
    resp = client.post(f"/staging/{doc_id}/apply", data={**data, "confirm": "1"})
    # a double-clicked "Yes" dedupes onto the same active job
    client.post(f"/staging/{doc_id}/apply", data={**data, "confirm": "1"})

    assert resp.status_code == 200
    rows = _jobs(conn, f"apply:{doc_id}:bind-manufacturer")
    assert len(rows) == 1
    assert rows[0]["type"] == "gate.apply"
    assert rows[0]["payload"]["decision"] == "bind-manufacturer"
    assert rows[0]["payload"]["manufacturer"] == "RSAFE"
    assert "confirm" not in rows[0]["payload"]


def test_whole_range_count_matches_what_gate_will_link(client, conn):
    """N must be GATE's count, not a display count: the confirm is the one
    place the reviewer is told how many items the click reaches."""
    from app.handlers import gate as gh

    doc_id = _seed_whole_range_candidate(conn)
    data = {"decision": "bind-manufacturer", "manufacturer": "RSAFE"}
    client.post(f"/staging/{doc_id}/apply", data={**data, "confirm": "1"})
    job = _jobs(conn, f"apply:{doc_id}:bind-manufacturer")[0]

    gh.handle_gate_apply(conn, job)

    linked = conn.execute(
        "SELECT count(*) AS n FROM item_document WHERE doc_id=%s "
        "AND match_basis='mfr-scope' AND status='production'", (doc_id,)
    ).fetchone()["n"]
    conn.rollback()
    assert linked == 3


# --------------------------------------------------------------------------- #
# Reject asks why
# --------------------------------------------------------------------------- #
def test_reject_reasons_are_the_six_from_the_spec_in_order():
    from web.app import REJECT_REASONS

    assert REJECT_REASONS == SPEC_REASONS


def test_reject_in_the_open_panel_asks_why(client, conn):
    ids = _demo(conn)

    text = client.get(f"/staging/{ids['staged_doc']}/detail").text

    offered = re.findall(r'<input type="radio" name="reason" value="([^"]+)"', text)
    assert tuple(offered) == SPEC_REASONS
    assert 'name="reason_note"' in text
    assert RECORDED_LINE in text
    assert "Reject…" in text          # the control that reveals the reasons
    assert "Cancel" in text


@pytest.mark.parametrize("data", [
    {"decision": "reject"},
    {"decision": "reject", "reason": ""},
    {"decision": "reject", "reason": "   "},
    {"decision": "reject", "reason_note": "a note is not a reason"},
])
def test_reject_without_a_reason_is_refused_and_enqueues_nothing(client, conn, data):
    ids = _demo(conn)

    resp = client.post(f"/staging/{ids['staged_doc']}/apply", data=data)

    assert resp.status_code == 422
    assert "reason" in resp.text.lower()
    assert _gate_apply_count(conn) == 0


@pytest.mark.parametrize("reason", ["Because", "out of date", "Out of date: stale"])
def test_reject_with_a_reason_not_on_the_list_is_refused(client, conn, reason):
    ids = _demo(conn)

    resp = client.post(f"/staging/{ids['staged_doc']}/apply",
                       data={"decision": "reject", "reason": reason})

    assert resp.status_code == 422
    assert _gate_apply_count(conn) == 0


@pytest.mark.parametrize("note_in,expected", [
    ("newer 2025 version exists", "Out of date: newer 2025 version exists"),
    ("", "Out of date"),
    ("   ", "Out of date"),
])
def test_the_reason_travels_on_gate_apply_as_note(client, conn, note_in, expected):
    ids = _demo(conn)

    resp = client.post(f"/staging/{ids['staged_doc']}/apply",
                       data={"decision": "reject", "reason": "Out of date",
                             "reason_note": note_in})

    assert resp.status_code == 200
    # the dedupe key is unchanged by the new field
    rows = _jobs(conn, f"apply:{ids['staged_doc']}:reject")
    assert len(rows) == 1
    assert rows[0]["payload"]["note"] == expected


def test_a_reject_keeps_its_retry_group(client, conn):
    ids = _demo(conn)

    client.post(f"/staging/{ids['manual_doc']}/apply",
                data={"decision": "reject", "reason": "Wrong manufacturer",
                      "group_id": "4407"})

    rows = _jobs(conn, f"apply:{ids['manual_doc']}:reject")
    assert rows[0]["payload"]["group_id"] == 4407
    assert rows[0]["payload"]["note"] == "Wrong manufacturer"


def test_the_reject_form_carries_the_retry_group(client, conn):
    ids = _demo(conn)

    text = client.get(f"/staging/{ids['manual_doc']}/detail").text
    reject_form = re.search(r'<form class="reject-form".*?</form>', text, re.S)

    assert reject_form, "the reasons live in their own form"
    assert '<input type="hidden" name="group_id" value="4407">' in reject_form.group(0)


def test_an_approve_carries_no_note(client, conn):
    """`note` is the reject reason. A stray `reason` on another decision must not
    put words in the audit trail that nobody chose for it."""
    ids = _demo(conn)

    client.post(f"/staging/{ids['staged_doc']}/apply",
                data={"decision": "approve", "reason": "Other"})

    rows = _jobs(conn, f"apply:{ids['staged_doc']}:approve")
    assert len(rows) == 1
    assert "note" not in rows[0]["payload"]


# --------------------------------------------------------------------------- #
# The Decisions page shows it
# --------------------------------------------------------------------------- #
def test_decisions_page_shows_the_reason_a_rejection_recorded(client, conn):
    """End to end: the form's reason, through the job the route enqueued and
    the handler that ran it, to the Decisions page."""
    from app.handlers import gate as gh

    ids = _demo(conn)
    client.post(f"/staging/{ids['staged_doc']}/apply",
                data={"decision": "reject", "reason": "Out of date",
                      "reason_note": "newer 2025 version exists"})
    job = _jobs(conn, f"apply:{ids['staged_doc']}:reject")[0]
    gh.handle_gate_apply(conn, job)
    conn.commit()

    body = client.get("/audit").text

    assert re.search(
        r'class="audit-note"[^>]*>\s*Out of date: newer 2025 version exists\s*<', body)


# --------------------------------------------------------------------------- #
# Fix round 1
# --------------------------------------------------------------------------- #
#: Input types the HTML spec lists as "fields that block implicit submission".
#: Enter in one of them is how a form gets submitted without a click.
_IMPLICIT_SUBMIT_TYPES = frozenset({
    "text", "search", "tel", "url", "email", "password", "date", "month",
    "week", "time", "datetime-local", "number",
})


class _FormOwners(HTMLParser):
    """Form ownership the way the HTML spec assigns it: a control belongs to
    the form its `form` attribute names, else to its nearest enclosing
    <form>. There is no browser in these tests, so implicit submission is
    judged from ownership: Enter in a field clicks its form's default button
    (the first submit button it owns); a form with no submit button and more
    than one such field submits nothing at all."""

    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict] = []
        self._open: list[dict] = []
        self._by_id: dict[str, dict] = {}
        self._named: list[dict] = []

    def handle_starttag(self, tag, attrs):
        a = {k: (v if v is not None else "") for k, v in attrs}
        if tag == "form":
            form = {"attrs": a, "controls": []}
            self.forms.append(form)
            self._open.append(form)
            if "id" in a:
                self._by_id[a["id"]] = form
        elif tag in ("input", "select", "textarea", "button"):
            control = {"tag": tag, **a}
            if "form" in a:
                self._named.append(control)
            elif self._open:
                self._open[-1]["controls"].append(control)

    def handle_endtag(self, tag):
        if tag == "form" and self._open:
            self._open.pop()

    def close(self):
        super().close()
        for control in self._named:
            self._by_id[control["form"]]["controls"].append(control)


def _owners(html: str) -> _FormOwners:
    parser = _FormOwners()
    parser.feed(html)
    parser.close()
    return parser


def _is_submit_button(c: dict) -> bool:
    return ((c["tag"] == "button" and c.get("type", "submit") == "submit")
            or (c["tag"] == "input" and c.get("type") in ("submit", "image")))


def _blocks_implicit_submission(c: dict) -> bool:
    return c["tag"] == "input" and c.get("type", "text") in _IMPLICIT_SUBMIT_TYPES


@pytest.mark.parametrize("which", ["plain", "whole-range", "picker"])
def test_enter_in_a_correction_field_cannot_post_a_decision(client, conn, which):
    """Fix round 1, F2 (controller ruling). The correction fields sat inside the
    decision form, whose first submit button is the approval, so Enter in
    "Certificate number" approved the document. They now belong to a form of
    their own with no submit button and three text/date fields, where the spec's
    implicit submission does nothing; the decision form sends them with
    `hx-include`."""
    if which == "plain":
        doc_id = _demo(conn)["staged_doc"]
    elif which == "whole-range":
        doc_id = _seed_whole_range_candidate(conn)
    else:
        doc_id = _seed_picker_candidate(conn)
    text = client.get(f"/staging/{doc_id}/detail").text
    parsed = _owners(text)

    decision_forms = [
        f for f in parsed.forms
        if any(_is_submit_button(c) and c.get("name") == "decision"
               and c.get("value") in ("approve", "bind-manufacturer")
               for c in f["controls"])
    ]
    assert len(decision_forms) == 1
    decision = decision_forms[0]
    # Nothing Enter can submit from is owned by the decision form.
    assert [c for c in decision["controls"] if _blocks_implicit_submission(c)] == []

    edits = [(f, c) for f in parsed.forms for c in f["controls"]
             if c.get("name", "").startswith("edit_") and c.get("type") != "hidden"]
    if which != "plain":
        # A whole-range approval offers no corrections at all (Task 6,
        # controller ruling 2026-09-11): `gate.apply`'s bind path drops edits,
        # so a field here would take a correction and lose it in silence.
        assert edits == []
        return
    assert {c["name"] for _, c in edits} == {
        "edit_type", "edit_regulation", "edit_validity_from",
        "edit_validity_to", "edit_cert_number"}
    home = {id(f) for f, _ in edits}
    assert len(home) == 1
    corrections = edits[0][0]
    assert corrections is not decision
    assert not any(_is_submit_button(c) for c in corrections["controls"])
    assert sum(_blocks_implicit_submission(c) for c in corrections["controls"]) > 1
    # ...and the decision still carries them.
    assert decision["attrs"].get("hx-include") == f"#{corrections['attrs']['id']}"


def test_an_approval_still_carries_a_correction(client, conn):
    """What the panel's `hx-include` sends: the correction fields next to the
    decision, compared against the baseline the decision form carries."""
    ids = _demo(conn)
    doc_id = ids["staged_doc"]
    baseline = json.dumps({
        "type": "DoC", "regulation": "MDR", "validity_from": "2024-01-01",
        "validity_to": "2028-01-01", "cert_number": "CERT-STAGED-1"})

    client.post(f"/staging/{doc_id}/apply", data={
        "decision": "approve", "edit_baseline": baseline,
        "edit_type": "DoC", "edit_regulation": "MDR",
        "edit_validity_from": "2024-01-01", "edit_validity_to": "2029-03-14",
        "edit_cert_number": "CERT-STAGED-1"})

    rows = _jobs(conn, f"apply:{doc_id}:approve")
    assert rows[0]["payload"]["edits"] == {"validity_to": "2029-03-14"}


def _seed_picker_candidate(conn) -> int:
    """A binding candidate the catalogue cannot name, so the panel offers the
    picker. Its manufacturer is spelled two ways in BC: `PCASE` (aliased to the
    canonical `PCASE CO`) and `pcase` (no alias row). An exact alias join counts
    one item under `PCASE CO`; GATE's `item_codes_for` folds both spellings and
    links two."""
    from tests.fixtures import seed_ui

    seed_ui.seed_item(conn, "pcase-1", manufacturer_raw="PCASE", md_flag=True)
    seed_ui.seed_item(conn, "pcase-2", manufacturer_raw="pcase", md_flag=True)
    conn.execute(
        "INSERT INTO manufacturer_alias (raw_name, canonical_name, source) "
        "VALUES ('PCASE', 'PCASE CO', 'vendor-master')")
    doc_id = seed_ui.seed_document(
        conn, content_hash="h-pcase-1", archive_url="/archive/h-pcase-1.pdf",
        doc_type="EC", regulation="MDR", coverage_scope="manufacturer",
        validity_to=None,
    )
    seed_ui.seed_manual_task(
        conn, kind="gate-manual", doc_id=doc_id, group_id=None,
        payload={"route": "mfr-binding", "manufacturer": None},
    )
    conn.commit()
    return doc_id


def test_the_picker_count_and_the_confirm_count_are_one_number(client, conn):
    """Fix round 1, M1. The picker's per-option count came from an exact alias
    join and the confirm's N from GATE's `item_codes_for`; they disagreed
    whenever BC spelled a manufacturer two ways. Both now come from
    `_mfr_bind_counts`, which is `item_codes_for` for many names at once."""
    doc_id = _seed_picker_candidate(conn)
    detail = client.get(f"/staging/{doc_id}/detail").text
    option = re.search(r'value="PCASE CO" data-items="(\d+)"', detail)
    assert option, "the picker should offer PCASE CO with its count"
    shown = int(option.group(1))

    resp = client.post(f"/staging/{doc_id}/apply",
                       data={"decision": "bind-manufacturer", "manufacturer": "PCASE CO"})

    assert resp.status_code == 200
    asked = re.search(r"Approve for all (\d+) PCASE CO items?\?", resp.text)
    assert asked, resp.text
    assert int(asked.group(1)) == shown == 2


def test_the_picker_counts_what_gate_links(client, conn):
    from app.handlers import gate as gh

    doc_id = _seed_picker_candidate(conn)
    client.post(f"/staging/{doc_id}/apply",
                data={"decision": "bind-manufacturer", "manufacturer": "PCASE CO",
                      "confirm": "1"})
    gh.handle_gate_apply(conn, _jobs(conn, f"apply:{doc_id}:bind-manufacturer")[0])

    linked = conn.execute(
        "SELECT count(*) AS n FROM item_document WHERE doc_id=%s", (doc_id,)
    ).fetchone()["n"]
    conn.rollback()
    assert linked == 2


def test_a_confirmed_whole_range_approval_replaces_the_row(client, conn):
    """Fix round 1, M2. The confirm answers inside the panel, so without a
    retarget the receipt landed there and the row stayed live, Approve and
    Reject… still clickable. The confirmed POST now replaces the row the way
    Approve and Reject do, and the queue shrinks."""
    doc_id = _seed_whole_range_candidate(conn)
    data = {"decision": "bind-manufacturer", "manufacturer": "RSAFE"}

    first = client.post(f"/staging/{doc_id}/apply", data=data)
    confirmed = client.post(f"/staging/{doc_id}/apply", data={**data, "confirm": "1"})

    assert "HX-Retarget" not in first.headers      # the question stays in the slot
    assert confirmed.headers["HX-Retarget"] == f"#doc-{doc_id}"
    assert confirmed.headers["HX-Reswap"] == "outerHTML"
    # the selector names the row the list actually renders
    assert f'id="doc-{doc_id}"' in client.get("/staging/docs").text


def test_changing_the_manufacturer_withdraws_a_confirm_on_screen(client, conn):
    """Fix round 1, M3. A confirm is priced for the manufacturer chosen when it
    was asked; its hidden fields carry that name, so its Yes would bind it even
    after the picker moved on. The picker's change handler clears the panel's
    `.bind-confirm`. There is no browser here, so this pins the handler and the
    markup it relies on: the slot sits beside the decision form, in the same
    parent, which is where the handler looks."""
    src = (_TEMPLATES / "base.html").read_text(encoding="utf-8")
    handler = re.search(
        r'closest\("select\[data-mfr-picker\]"\).*?\n    \}\);', src, re.S)
    assert handler, "the picker's change handler moved; update this test"
    body = handler.group(0)
    assert 'form.parentNode.querySelector(".bind-confirm")' in body
    assert 'innerHTML = ""' in body

    doc_id = _seed_picker_candidate(conn)
    detail = client.get(f"/staging/{doc_id}/detail").text
    assert re.search(
        rf'</form>\s*<div class="bind-confirm" id="bind-confirm-{doc_id}"></div>', detail)


def test_a_name_with_no_bc_codes_is_refused_at_the_first_step(client, conn):
    """Fix round 1, M4. A name that folds to no BC code used to be asked about
    ("Approve for all 0 X items?") and then dead-lettered in GATE on Yes
    ([gate-bind-zero-links]). It is refused before the question now."""
    doc_id = _seed_whole_range_candidate(conn)

    resp = client.post(f"/staging/{doc_id}/apply",
                       data={"decision": "bind-manufacturer",
                             "manufacturer": "NOBODY GMBH"})

    assert resp.status_code == 422
    assert "NOBODY GMBH" in resp.text
    assert "Nothing was changed." in resp.text
    assert _gate_apply_count(conn) == 0


def test_a_manufacturer_with_codes_but_no_device_items_is_still_asked(client, conn):
    """The other nought, and a legitimate one: BC knows them but marks none of
    their items as a medical device. Since migration 053 GATE records the
    binding and links nothing, so the question is asked, with its honest 0."""
    from tests.fixtures import seed_ui

    seed_ui.seed_item(conn, "pzero-1", manufacturer_raw="PZERO", md_flag=False)
    conn.execute(
        "INSERT INTO manufacturer_alias (raw_name, canonical_name, source) "
        "VALUES ('PZERO', 'PZERO', 'vendor-master')")
    conn.commit()
    doc_id = _seed_whole_range_candidate(conn)

    resp = client.post(f"/staging/{doc_id}/apply",
                       data={"decision": "bind-manufacturer", "manufacturer": "PZERO"})

    assert resp.status_code == 200
    assert "Approve for all 0 PZERO items?" in resp.text
    assert "Yes, approve for 0 items" in resp.text


def test_a_reject_note_over_the_cap_is_refused(client, conn):
    """Fix round 1, M6. The note is free text a person types; 500 characters
    is room for a sentence or two, and the field says so with `maxlength`."""
    from web.app import REJECT_NOTE_MAX

    assert REJECT_NOTE_MAX == 500
    ids = _demo(conn)
    url = f"/staging/{ids['staged_doc']}/apply"

    long = client.post(url, data={"decision": "reject", "reason": "Other",
                                  "reason_note": "x" * 501})
    assert long.status_code == 422
    assert "500" in long.text and "Nothing was changed." in long.text
    assert _gate_apply_count(conn) == 0

    ok = client.post(url, data={"decision": "reject", "reason": "Other",
                                "reason_note": "x" * 500})
    assert ok.status_code == 200
    rows = _jobs(conn, f"apply:{ids['staged_doc']}:reject")
    assert rows[0]["payload"]["note"] == "Other: " + "x" * 500


def test_the_note_field_carries_the_cap(client, conn):
    ids = _demo(conn)

    text = client.get(f"/staging/{ids['staged_doc']}/detail").text

    assert re.search(r'name="reason_note"[^>]*maxlength="500"', text)


# --------------------------------------------------------------------------- #
# The button's number and the confirm's number are ONE number, on a folded name
# --------------------------------------------------------------------------- #
def _seed_folded_name_candidate(conn) -> int:
    """A payload name the alias table folds to a label that maps somewhere
    else again.

    `manufacturer_alias` here says "ACME AG" is ACME, and separately that ACME
    (as a BC vendor code) is GLOBEX. So the two spellings fan out to different
    sets of catalogue codes:

      * "ACME AG" -> {ACME AG, ACME}        = 2 + 1 = 3 medical-device items
      * "ACME"    -> {ACME, GLOBEX}         = 1 + 3 = 4 medical-device items

    `resolve_canonicals("ACME AG")` is exactly ["ACME"], so the one-click bind
    is still offered -- and ACME is the label the button posts and GATE
    resolves. The count therefore has to be ACME's.
    """
    from tests.fixtures import seed_ui

    conn.execute(
        "INSERT INTO manufacturer_alias (raw_name, canonical_name, source) "
        "VALUES ('ACME AG','ACME','vendor-master'), ('ACME','GLOBEX','vendor-master')")
    for ref in ("fold-ag-1", "fold-ag-2"):
        seed_ui.seed_item(conn, ref, manufacturer_raw="ACME AG", md_flag=True)
    seed_ui.seed_item(conn, "fold-acme-1", manufacturer_raw="ACME", md_flag=True)
    for ref in ("fold-gx-1", "fold-gx-2", "fold-gx-3"):
        seed_ui.seed_item(conn, ref, manufacturer_raw="GLOBEX", md_flag=True)
    doc_id = seed_ui.seed_document(
        conn, content_hash="h-fold-1", archive_url="/archive/h-fold-1.pdf",
        doc_type="DoC", regulation="MDR", coverage_scope="manufacturer",
        validity_to=None,
    )
    seed_ui.seed_manual_task(
        conn, kind="gate-manual", doc_id=doc_id, group_id=None,
        payload={"route": "mfr-binding", "manufacturer": "ACME AG"},
    )
    conn.commit()
    return doc_id


def test_the_button_and_the_confirm_count_the_same_name(client, conn):
    """P1a's whole point. `_mfr_bind_target` counted on the PAYLOAD name and
    returned the RESOLVED label, and the confirm recounted on the label it was
    handed back -- so the button said 3 and the question said 4 for the same
    click."""
    doc_id = _seed_folded_name_candidate(conn)

    detail = client.get(f"/staging/{doc_id}/detail").text
    button = re.search(r"Approve for all ([A-Z ]+?) items\s*\((\d+) items?\)", detail)
    assert button, detail
    label, shown = button.group(1), int(button.group(2))
    assert label == "ACME", "the button binds by the catalogue's label"

    resp = client.post(f"/staging/{doc_id}/apply",
                       data={"decision": "bind-manufacturer", "manufacturer": label})
    asked = re.search(r"Approve for all (\d+) ACME items?\?", resp.text)
    assert asked, resp.text

    assert shown == int(asked.group(1))


def test_the_count_is_what_gate_will_link_for_a_folded_name(client, conn):
    """And the one number both of them show is the one GATE writes."""
    from app.handlers import gate as gh

    doc_id = _seed_folded_name_candidate(conn)
    detail = client.get(f"/staging/{doc_id}/detail").text
    shown = int(re.search(r"Approve for all ACME items\s*\((\d+) items?\)",
                          detail).group(1))

    client.post(f"/staging/{doc_id}/apply",
                data={"decision": "bind-manufacturer", "manufacturer": "ACME",
                      "confirm": "1"})
    gh.handle_gate_apply(conn, _jobs(conn, f"apply:{doc_id}:bind-manufacturer")[0])
    linked = conn.execute(
        "SELECT count(*) AS n FROM item_document WHERE doc_id=%s", (doc_id,)
    ).fetchone()["n"]
    conn.rollback()

    assert shown == linked == 4


# --------------------------------------------------------------------------- #
# `note` is the REJECT reason, on a reject and nowhere else
# --------------------------------------------------------------------------- #
# `test_an_approve_carries_no_note` pinned one of the four decisions. The route
# builds `note` under `if decision == "reject"`, so the other two -- a
# whole-range bind and a reopen -- were unpinned: a stray `reason` on either
# would have put a word in the audit trail that nobody chose for it.
def test_a_whole_range_approval_carries_no_note(client, conn):
    doc_id = _seed_whole_range_candidate(conn)

    client.post(f"/staging/{doc_id}/apply",
                data={"decision": "bind-manufacturer", "manufacturer": "RSAFE",
                      "confirm": "1", "reason": "Other",
                      "reason_note": "not a reason for this decision"})

    rows = _jobs(conn, f"apply:{doc_id}:bind-manufacturer")
    assert len(rows) == 1
    assert "note" not in rows[0]["payload"]


def test_a_reopen_carries_no_note(client, conn):
    from tests.fixtures import seed_ui

    doc_id = seed_ui.seed_document(
        conn, content_hash="h-reopen-1", archive_url="/archive/h-reopen-1.pdf",
        doc_type="DoC", regulation="MDR", coverage_scope="group",
        status="rejected")
    conn.commit()

    client.post(f"/staging/{doc_id}/apply",
                data={"decision": "reopen", "reason": "Out of date",
                      "reason_note": "not a reason for this decision"})

    rows = _jobs(conn, f"apply:{doc_id}:reopen")
    assert len(rows) == 1
    assert "note" not in rows[0]["payload"]
