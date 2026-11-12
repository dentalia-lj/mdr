"""Change type on Review: the control, its two-step confirm and the route
(spec 2026-11-12 §3, §4, §5, §7; plan Task 6).

The web process stays a producer: step 1 writes nothing and answers with the
consequence list, step 2 (with the tick) enqueues one `gate.apply` decision
`retype`. Every assertion about a write is about a `job` row, except the end
to end tests, which run the enqueued job through GATE themselves.
"""

from __future__ import annotations

import html
import re
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app import queue
from app.config import Web
from app.handlers import gate as gh
from tests.conftest import TEST_API_URL
from tests.fixtures import seed_ui
from web import words
from web.app import MFR_BINDING_REASON, MFR_BINDING_UNKNOWN_CLASS_REASON, NO_TASK_REASON, create_app

NOTHING = "Nothing was changed."
LINE_6_GROUP = ("It stays on Review. Nothing is published. You approve it next, "
                "in the specific items panel.")
LINE_6_RANGE = ("It stays on Review. Nothing is published. You approve it next, "
                "in the whole range panel.")


@pytest.fixture
def client(test_db_url, tmp_path):
    return TestClient(create_app(Web(api_database_url=TEST_API_URL,
                                     imports_dir=str(tmp_path))))


def _gate_apply_count(conn) -> int:
    return conn.execute(
        "SELECT count(*) AS n FROM job WHERE type='gate.apply'").fetchone()["n"]


def _jobs(conn, dedupe_key: str) -> list[dict]:
    return conn.execute("SELECT * FROM job WHERE dedupe_key=%s ORDER BY id",
                        (dedupe_key,)).fetchall()


def _seed(conn, ch, *, doc_type="ISO", regulation="MDR", scope="manufacturer",
          cert_number=None, validity_to=None, task: dict | None = None,
          links: int = 0, status="staged", manufacturer="ZLATARNA") -> int:
    doc_id = seed_ui.seed_document(
        conn, content_hash=ch, archive_url=f"/archive/{ch}.pdf", doc_type=doc_type,
        regulation=regulation, coverage_scope=scope, status=status,
        validity_to=validity_to, cert_number=cert_number)
    for i in range(1, links + 1):
        ref = f"{ch}-{i:02d}"
        seed_ui.seed_item(conn, ref, manufacturer_raw=manufacturer)
        seed_ui.seed_link(conn, ref, doc_id, match_basis="ref-list", status="staged")
    if task is not None:
        seed_ui.seed_manual_task(conn, kind="gate-manual", doc_id=doc_id, payload=task)
    conn.commit()
    return doc_id


def _seed_831(conn, ch="r831") -> int:
    """Doc 831 as Review shows it: a declaration read as an ISO certificate,
    whole range, no number, no expiry, waiting on a blank device class."""
    return _seed(conn, ch, task={"route": "mfr-binding", "manufacturer": "ZLATARNA",
                                 "reason": "md-class-unknown"})


def _seed_832(conn, ch="r832") -> int:
    """Doc 832: the same misread, carrying the number it cites and an expiry."""
    return _seed(conn, ch, cert_number="028/MDR", validity_to="2027-05-25",
                 task={"route": "mfr-binding", "manufacturer": "ZLATARNA"})


def _step1(client, doc_id, **values):
    return client.post(f"/staging/{doc_id}/retype", data=values)


def _confirm(doc_id, *, type, regulation, coverage_scope, expected, tick=True, **extra):
    data = {"type": type, "regulation": regulation, "coverage_scope": coverage_scope,
            "expected_type": expected[0], "expected_regulation": expected[1],
            "expected_coverage_scope": expected[2], "confirm": "1", **extra}
    if tick:
        data["tick"] = "1"
    return data


def _lines(html: str) -> list[str]:
    """The confirm's consequence lines, in order."""
    block = re.search(r'<div class="result error">(.*?)<form', html, re.S)
    assert block, html
    return [re.sub(r"\s+", " ", p).strip()
            for p in re.findall(r"<p>(.*?)</p>", block.group(1), re.S)]


def _hidden(html: str) -> dict[str, str]:
    return dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', html))


# --------------------------------------------------------------------------- #
# step 1: the consequence list, nothing written
# --------------------------------------------------------------------------- #
def test_step_1_for_831_is_lines_1_and_6_and_enqueues_nothing(client, conn):
    doc_id = _seed_831(conn)
    before = _gate_apply_count(conn)

    r = _step1(client, doc_id, type="DoC", regulation="MDR", coverage_scope="group")

    assert r.status_code == 200
    assert _lines(r.text) == [
        "Type: ISO → DoC. Regulation: MDR (unchanged). Covers: whole range → specific items.",
        LINE_6_GROUP,
    ]
    assert _gate_apply_count(conn) == before
    assert "I have checked the document: it is a declaration of conformity, for specific items." \
        in r.text
    assert "Yes, change the type" in r.text
    # The confirm posts back the values it was computed from, read now.
    assert _hidden(r.text) == {
        "type": "DoC", "regulation": "MDR", "coverage_scope": "group",
        "expected_type": "ISO", "expected_regulation": "MDR",
        "expected_coverage_scope": "manufacturer", "confirm": "1"}
    assert f'hx-post="/staging/{doc_id}/retype"' in r.text
    assert 'name="tick"' in r.text and "required" in r.text


def test_step_1_for_832_names_the_number_and_the_expiry(client, conn):
    doc_id = _seed_832(conn)

    r = _step1(client, doc_id, type="DoC", regulation="MDR", coverage_scope="group")

    expiry = words.day_text(date(2027, 5, 25))
    assert _lines(r.text) == [
        "Type: ISO → DoC. Regulation: MDR (unchanged). Covers: whole range → specific items.",
        f"Certificate number 028/MDR will be read as the certificate this declaration cites. "
        f"Expiry {expiry} stays. Correct them under Correct a fact first when you approve "
        f"if they are wrong.",
        LINE_6_GROUP,
    ]


def test_line_1_names_only_what_changes():
    lines = words.retype_lines(_plan(before=("DoC", "MDD", "group"),
                                     after=("DoC", "MDR", "group")))
    assert lines[0] == "Type: DoC (unchanged). Regulation: MDD → MDR. Covers: specific items (unchanged)."


def _plan(*, before, after, **over):
    from app.retype import Plan

    f = ("type", "regulation", "coverage_scope")
    base = dict(before=dict(zip(f, before)), after=dict(zip(f, after)), links_off=[],
                citations_cleared=[], citations_set=[], clears_own_cert_doc=False,
                cert_number=None, validity_to=None, number_moves=False, panel=after[2])
    base.update(over)
    return Plan(**base)


def test_line_2_names_the_first_ten_links_and_counts_the_rest(client, conn):
    doc_id = _seed(conn, "r-l12", doc_type="DoC", scope="group", links=12)

    r = _step1(client, doc_id, type="DoC", regulation="MDR", coverage_scope="manufacturer")

    first = ", ".join(f"r-l12-{i:02d}" for i in range(1, 11))
    assert _lines(r.text) == [
        "Type: DoC (unchanged). Regulation: MDR (unchanged). Covers: specific items → whole range.",
        f"12 items now linked will be taken off: {first}, and 2 more.",
        LINE_6_RANGE,
    ]


def test_line_2_is_absent_with_no_links(client, conn):
    doc_id = _seed(conn, "r-l0", doc_type="DoC", scope="group")

    r = _step1(client, doc_id, type="DoC", regulation="MDR", coverage_scope="manufacturer")

    assert not any("taken off" in line for line in _lines(r.text))


def test_line_2_with_a_few_links_lists_them_all():
    lines = words.retype_lines(_plan(before=("DoC", "MDR", "group"),
                                     after=("DoC", "MDR", "manufacturer"),
                                     links_off=["A1"]))
    assert lines[1] == "1 item now linked will be taken off: A1."


def _citer(conn, ch, *, cert_doc_id=None, cert_number=None) -> int:
    doc_id = conn.execute(
        "INSERT INTO document (type, regulation, status, content_hash, archive_url, "
        "coverage_scope, cert_number, cert_doc_id) VALUES ('DoC','MDR','production',%s,"
        "'/archive/c.pdf','group',%s,%s) RETURNING doc_id",
        (ch, cert_number, cert_doc_id)).fetchone()["doc_id"]
    conn.commit()
    return doc_id


def test_line_3_lists_the_declarations_that_will_stop_citing_it(client, conn):
    doc_id = _seed(conn, "r-ec", doc_type="EC", cert_number="EC-1")
    a = _citer(conn, "r-ec-a", cert_doc_id=doc_id)
    b = _citer(conn, "r-ec-b", cert_doc_id=doc_id)

    r = _step1(client, doc_id, type="DoC", regulation="MDR", coverage_scope="group")

    assert (f"2 declarations cite this as their certificate and will no longer: {a}, {b}."
            in _lines(r.text))


def test_line_3_is_absent_with_no_citing_declaration(client, conn):
    doc_id = _seed(conn, "r-ec0", doc_type="EC")

    r = _step1(client, doc_id, type="DoC", regulation="MDR", coverage_scope="group")

    assert not any("cite this as" in line for line in _lines(r.text))


def test_line_4_counts_the_declarations_that_will_point_to_it(client, conn):
    doc_id = _seed(conn, "r-doc", doc_type="DoC", scope="group", cert_number="EC-9")
    _citer(conn, "r-doc-a", cert_number="EC-9")
    _citer(conn, "r-doc-b", cert_number="EC-9")

    r = _step1(client, doc_id, type="EC", regulation="MDR", coverage_scope="manufacturer")

    assert "2 declarations citing certificate EC-9 will point to it." in _lines(r.text)


def test_line_4_without_a_number_says_none_can_point_to_it(client, conn):
    doc_id = _seed(conn, "r-doc0", doc_type="DoC", scope="group")

    r = _step1(client, doc_id, type="EC", regulation="MDR", coverage_scope="manufacturer")

    assert ("No declaration can point to it until a certificate number is entered."
            in _lines(r.text))


# --------------------------------------------------------------------------- #
# refusals
# --------------------------------------------------------------------------- #
def test_unchanged_is_refused_in_those_words(client, conn):
    doc_id = _seed_831(conn)
    before = _gate_apply_count(conn)

    r = _step1(client, doc_id, type="ISO", regulation="MDR", coverage_scope="manufacturer")

    assert r.status_code == 422
    assert re.search(r'<div class="result error">\s*Nothing to change\. Nothing was changed\.'
                     r'\s*</div>', r.text)
    assert _gate_apply_count(conn) == before


def _make(conn, code) -> tuple[int, dict]:
    """A document that `retype.problems` refuses with `code`, and the posted
    values that reach it."""
    values = {"type": "DoC", "regulation": "MDR", "coverage_scope": "group"}
    if code == "not-found":
        return 987654, values
    if code == "not-staged":
        return _seed(conn, "rp-pub", status="production"), values
    doc_id = _seed(conn, f"rp-{code}")
    if code == "bad-type":
        values["type"] = "Brochure"
    elif code == "bad-regulation":
        values["regulation"] = "FDA"
    elif code == "bad-scope":
        values["coverage_scope"] = "item"
    elif code == "in-flight":
        queue.enqueue(conn, "extract.doc", {"content_hash": f"rp-{code}"}, f"x:{code}")
    elif code == "production-link":
        seed_ui.seed_item(conn, "rp-pl-1")
        seed_ui.seed_link(conn, "rp-pl-1", doc_id, match_basis="ref-list", status="production")
    elif code == "in-chain":
        older = _seed(conn, "rp-older", status="superseded")
        conn.execute("UPDATE document SET supersedes=%s WHERE doc_id=%s", (older, doc_id))
    conn.commit()
    return doc_id, values


REFUSALS = {
    "not-found": "This document is no longer on Review. Reload the page to see where it stands.",
    "not-staged": "This document is no longer on Review. Reload the page to see where it stands.",
    "bad-type": "That type is not on the list. Choose one of the listed types.",
    "bad-regulation": "Those rules are not on the list. Choose MDR, MDD or n.a.",
    "bad-scope": "Choose specific items or the manufacturer's whole range.",
    "in-flight": ("A reading of this document is in progress, or another decision on it "
                  "is waiting to apply. Try again later."),
    "production-link": ("This document already counts for an item, so its type cannot be "
                        "changed here. Ask the person who looks after the system."),
    "in-chain": ("This document is part of a chain of versions, so its type cannot be "
                 "changed here."),
}


@pytest.mark.parametrize("code", sorted(REFUSALS))
def test_each_problem_is_refused_and_enqueues_nothing(client, conn, code):
    doc_id, values = _make(conn, code)
    before = _gate_apply_count(conn)

    r = _step1(client, doc_id, **values)

    assert r.status_code == 422
    error = html.unescape(
        re.search(r'<div class="result error">(.*?)</div>', r.text, re.S).group(1).strip())
    assert error == f"{REFUSALS[code]} {NOTHING}"
    assert _gate_apply_count(conn) == before


def test_a_stale_confirm_is_refused(client, conn):
    doc_id = _seed_831(conn)
    before = _gate_apply_count(conn)

    r = client.post(f"/staging/{doc_id}/retype", data=_confirm(
        doc_id, type="DoC", regulation="MDR", coverage_scope="group",
        expected=("EC", "MDR", "manufacturer")))

    assert r.status_code == 422
    assert ("This document changed after you checked it. Reload the page and check "
            f"again. {NOTHING}") in r.text
    assert _gate_apply_count(conn) == before


def test_step_2_without_the_tick_is_refused(client, conn):
    doc_id = _seed_831(conn)
    before = _gate_apply_count(conn)

    r = client.post(f"/staging/{doc_id}/retype", data=_confirm(
        doc_id, type="DoC", regulation="MDR", coverage_scope="group",
        expected=("ISO", "MDR", "manufacturer"), tick=False))

    assert r.status_code == 422
    assert f"Tick the box to confirm. {NOTHING}" in r.text
    assert _gate_apply_count(conn) == before


# --------------------------------------------------------------------------- #
# step 2: one gate.apply, and the row gives way to the receipt
# --------------------------------------------------------------------------- #
def test_step_2_with_the_tick_enqueues_the_retype(client, conn):
    doc_id = _seed_831(conn)

    r = client.post(f"/staging/{doc_id}/retype", data=_confirm(
        doc_id, type="DoC", regulation="MDR", coverage_scope="group",
        expected=("ISO", "MDR", "manufacturer")))

    assert r.status_code == 200
    rows = _jobs(conn, f"apply:{doc_id}:retype")
    assert len(rows) == 1
    assert rows[0]["type"] == "gate.apply"
    assert rows[0]["payload"] == {
        "doc_id": doc_id, "decision": "retype", "decided_by": "user:admin",
        "type": "DoC", "regulation": "MDR", "coverage_scope": "group",
        "expected": {"type": "ISO", "regulation": "MDR", "coverage_scope": "manufacturer"}}
    assert r.headers["HX-Retarget"] == f"#doc-{doc_id}"
    assert r.headers["HX-Reswap"] == "outerHTML"
    assert ("The type change for this document is recorded. It comes back on Review in the "
            "specific items panel within a minute, and you approve it there. Nothing is "
            "published.") in r.text


def test_step_2_carries_a_note(client, conn):
    doc_id = _seed_831(conn)

    client.post(f"/staging/{doc_id}/retype", data=_confirm(
        doc_id, type="DoC", regulation="MDR", coverage_scope="group",
        expected=("ISO", "MDR", "manufacturer"), note="  read as ISO  "))

    assert _jobs(conn, f"apply:{doc_id}:retype")[0]["payload"]["note"] == "read as ISO"


def test_pressing_yes_twice_dedupes_then_goes_stale(client, conn):
    """Review Focus 2."""
    doc_id = _seed_831(conn)
    data = _confirm(doc_id, type="DoC", regulation="MDR", coverage_scope="group",
                    expected=("ISO", "MDR", "manufacturer"))

    client.post(f"/staging/{doc_id}/retype", data=data)
    second = client.post(f"/staging/{doc_id}/retype", data=data)

    assert second.status_code == 200
    assert '<div class="result ok">Already in progress.</div>' in second.text
    assert len(_jobs(conn, f"apply:{doc_id}:retype")) == 1

    job = _jobs(conn, f"apply:{doc_id}:retype")[0]
    gh.handle_gate_apply(conn, job)
    conn.execute("UPDATE job SET status='done' WHERE id=%s", (job["id"],))
    conn.commit()

    third = client.post(f"/staging/{doc_id}/retype", data=data)

    assert third.status_code == 422
    assert "This document changed after you checked it." in third.text
    assert len(_jobs(conn, f"apply:{doc_id}:retype")) == 1


# --------------------------------------------------------------------------- #
# render
# --------------------------------------------------------------------------- #
def _retype_form(html: str, doc_id: int) -> str:
    m = re.search(rf'<form class="retype-form"[^>]*hx-post="/staging/{doc_id}/retype".*?</form>',
                  html, re.S)
    assert m, "retype form not rendered"
    return m.group(0)


def _selected(form: str, name: str) -> str:
    sel = re.search(rf'<select[^>]*name="{name}".*?</select>', form, re.S).group(0)
    picked = re.findall(r'<option value="([^"]*)"[^>]*\bselected\b', sel)
    assert len(picked) == 1, sel
    return picked[0]


@pytest.mark.parametrize("which", ["staged_doc", "mfr_doc"])
def test_the_control_appears_on_a_staged_document_in_both_panels(client, conn, which):
    ids = seed_ui.seed_demo(conn)
    conn.commit()
    doc_id = ids[which]

    text = client.get(f"/staging/{doc_id}/detail").text

    assert re.search(r'<button type="button" class="btn-secondary"[^>]*>Change type\.\.\.</button>',
                     text)
    form = _retype_form(text, doc_id)
    # Under "Why it needs you", above the approval text and the decision bar.
    assert text.index('class="why"') < text.index("Change type...") \
        < text.index('id="approve-scope-') < text.index('class="panel-actions"')
    assert f'id="retype-confirm-{doc_id}"' in text
    assert "hidden" in form.split(">", 1)[0]


def test_the_form_is_prefilled_with_the_document_s_values(client, conn):
    ids = seed_ui.seed_demo(conn)
    conn.commit()

    form = _retype_form(client.get(f"/staging/{ids['mfr_doc']}/detail").text, ids["mfr_doc"])

    assert _selected(form, "type") == "ISO"
    assert _selected(form, "regulation") == "n.a."
    assert _selected(form, "coverage_scope") == "manufacturer"
    # The browser moves "What it covers" to the type's default (static/retype.js).
    assert 'data-retype-type' in form
    assert '<option value="EC" data-default-scope="manufacturer"' in form
    assert '<option value="DoC" data-default-scope="group"' in form


def test_the_control_is_on_no_other_status(client, conn):
    ids = seed_ui.seed_demo(conn)
    conn.commit()
    prod = ids["prod_doc"]

    detail = client.get(f"/staging/{prod}/detail")
    page = client.get(f"/documents/{prod}")

    assert detail.status_code == 404
    for text in (detail.text, page.text):
        assert f"/staging/{prod}/retype" not in text
        assert "Change type..." not in text


def test_retype_js_is_loaded(client):
    text = client.get("/staging").text
    assert re.search(r'<script src="/static/retype\.js[^"]*" defer></script>', text)


def test_the_board_reason_names_who_changed_the_type(client, conn):
    doc_id = _seed(conn, "r-board", doc_type="DoC", scope="group",
                   task={"retyped": {"by": "natasa", "at": "2026-11-12T10:15:00+00:00"}})

    board = client.get("/staging/docs").text
    detail = client.get(f"/staging/{doc_id}/detail").text

    said = ("Type changed by natasa on 12 Nov 2026. Choose the items this document covers, "
            "then approve.")
    assert said in board
    assert said in detail


def test_a_retyped_whole_range_reason_keeps_the_panel_s_sentence(client, conn):
    doc_id = _seed(conn, "r-board-r", task={
        "route": "mfr-binding", "manufacturer": "ZLATARNA", "reason": "md-class-unknown",
        "retyped": {"by": "natasa", "at": "2026-11-12T10:15:00+00:00"}})

    detail = html.unescape(client.get(f"/staging/{doc_id}/detail").text)

    assert (f"Type changed by natasa on 12 Nov 2026. {MFR_BINDING_UNKNOWN_CLASS_REASON}"
            in detail)


def test_a_group_document_with_a_binding_route_gets_the_specific_items_panel(client, conn):
    """Controller ruling (docs 592, 594): whole-range approval refuses group
    scope, so a group document never gets that panel, whatever its task says."""
    doc_id = _seed(conn, "r-592", doc_type="other", scope="group", links=2,
                   task={"route": "mfr-binding", "manufacturer": "CHIRANA"})

    board = html.unescape(client.get("/staging/docs").text)
    detail = client.get(f"/staging/{doc_id}/detail").text
    ranged = client.get("/staging/docs?reason=range").text

    row = re.search(rf'id="doc-{doc_id}">.*?</details>', board, re.S).group(0)
    assert NO_TASK_REASON in row
    assert MFR_BINDING_REASON not in row
    assert f'id="doc-{doc_id}"' not in ranged
    assert 'value="bind-manufacturer"' not in detail
    assert 'value="approve"' in detail


def test_retyped_to_whole_range_with_no_manufacturer_shows_unknown(client, conn):
    """Review Focus 4: the task gets the binding route and no manufacturer; the
    panel asks which manufacturer and offers no one-click binding."""
    seed_ui.seed_item(conn, "focus4-1", manufacturer_raw="SOMEONE")
    doc_id = _seed(conn, "r-focus4", doc_type="DoC", scope="group")
    client.post(f"/staging/{doc_id}/retype", data=_confirm(
        doc_id, type="EC", regulation="MDR", coverage_scope="manufacturer",
        expected=("DoC", "MDR", "group")))
    gh.handle_gate_apply(conn, _jobs(conn, f"apply:{doc_id}:retype")[0])
    conn.commit()

    detail = client.get(f"/staging/{doc_id}/detail").text
    board = html.unescape(client.get("/staging/docs").text)

    assert "Which manufacturer does this cover?" in detail
    assert '<input type="hidden" name="manufacturer"' not in detail
    assert "Type changed by user:admin on" in detail
    row = re.search(rf'id="doc-{doc_id}">.*?</details>', board, re.S).group(0)
    assert "Manufacturer not known" in board[:board.index(f'id="doc-{doc_id}"')]
    assert MFR_BINDING_REASON in row


def test_end_to_end_831_comes_back_in_the_specific_items_panel(client, conn):
    doc_id = _seed_831(conn)
    client.post(f"/staging/{doc_id}/retype", data=_confirm(
        doc_id, type="DoC", regulation="MDR", coverage_scope="group",
        expected=("ISO", "MDR", "manufacturer")))
    result = gh.handle_gate_apply(conn, _jobs(conn, f"apply:{doc_id}:retype")[0])
    conn.commit()
    assert result["decision"] == "retype"

    detail = client.get(f"/staging/{doc_id}/detail").text

    at = conn.execute("SELECT payload->'retyped'->>'at' AS at FROM manual_task "
                      "WHERE doc_id=%s AND status='open'", (doc_id,)).fetchone()["at"]
    day = words.day_text(datetime.fromisoformat(at).date())
    assert f"Type changed by user:admin on {day}." in detail
    assert 'value="bind-manufacturer"' not in detail
    assert 'value="approve"' in detail
    assert _selected(_retype_form(detail, doc_id), "type") == "DoC"
    assert _selected(_retype_form(detail, doc_id), "coverage_scope") == "group"


def test_correct_a_fact_first_has_no_type_field(client, conn):
    ids = seed_ui.seed_demo(conn)
    conn.commit()

    text = client.get(f"/staging/{ids['staged_doc']}/detail").text

    assert 'name="edit_type"' not in text
    assert 'name="edit_regulation"' in text
    corrections = re.search(r'<details class="corrections">.*?</details>', text, re.S).group(0)
    assert "Document type" not in corrections
    assert ">Rules<" in corrections
