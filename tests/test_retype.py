"""`app/retype.py`: the read-only half of Change type (spec 2026-11-12 §5, §6).

`problems` is the precondition list both the web route and GATE refuse on;
`plan` is what the confirm screen lists. Neither writes. Tests don't commit.
"""
from __future__ import annotations

import json

import pytest

from app import retype
from app.extract import checks

MFR = "ACME"


def _doc(conn, key, *, type_="ISO", regulation="MDR", scope="manufacturer",
         status="staged", cert_number=None, validity_to=None, cert_doc_id=None,
         canonical=None):
    return conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, archive_url, "
        "status, cert_number, validity_to, cert_doc_id, canonical_manufacturer) "
        "VALUES (%s,%s,%s,%s,'file:///a.pdf',%s,%s,%s,%s,%s) RETURNING doc_id",
        (type_, regulation, scope, f"rt-{key}", status, cert_number, validity_to,
         cert_doc_id, canonical)).fetchone()["doc_id"]


def _hash(conn, doc_id):
    return conn.execute("SELECT content_hash FROM document WHERE doc_id=%s",
                        (doc_id,)).fetchone()["content_hash"]


def _item(conn, ref):
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, catalogue, "
        "mirror_rev, updated_at) VALUES (%s,'n','001',NULL,'LJ',1,now()) "
        "ON CONFLICT (item_ref) DO NOTHING", (ref,))


def _link(conn, doc_id, ref, status="staged"):
    _item(conn, ref)
    conn.execute("INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
                 "VALUES (%s,%s,'ref-list',%s)", (ref, doc_id, status))


def _job(conn, type_, payload, status="pending"):
    return conn.execute(
        "INSERT INTO job (type, payload, dedupe_key, status) "
        "VALUES (%s::job_type, %s, %s, %s::job_status) RETURNING id",
        (type_, json.dumps(payload), f"t-{type_}-{json.dumps(payload)}-{status}",
         status)).fetchone()["id"]


BEFORE = {"type": "ISO", "regulation": "MDR", "coverage_scope": "manufacturer"}
TO_DOC = {"type": "DoC", "regulation": "MDR", "coverage_scope": "group"}


def test_vocabularies_match_checks():
    # Spelled out here, not imported: `app.extract.checks` pulls in PyMuPDF,
    # which the web image does not carry, and the web route imports this module.
    assert set(retype.TYPES) == checks.TYPES
    assert set(retype.REGULATIONS) == checks.REGULATIONS
    assert set(retype.SCOPES) == checks.SCOPES


@pytest.mark.parametrize("doc_type, scope", [
    ("DoC", "group"), ("IFU", "group"), ("SPP", "group"), ("other", "group"),
    ("EC", "manufacturer"), ("ISO", "manufacturer"),
])
def test_default_scope(doc_type, scope):
    assert retype.default_scope(doc_type) == scope


@pytest.mark.parametrize("doc_type, cls", [
    ("DoC", "cites"), ("EC", "own"), ("ISO", "own"),
    ("IFU", "none"), ("SPP", "none"), ("other", "none"),
])
def test_number_class(doc_type, cls):
    assert retype.number_class(doc_type) == cls


# (case id, setup(conn, doc_id) -> dict of overrides for problems(), expected codes)
def _missing(conn, d):
    return {"doc_id": d + 100000}


def _published(conn, d):
    conn.execute("UPDATE document SET status='production' WHERE doc_id=%s", (d,))
    return {}


def _job_on_hash(type_, status):
    def setup(conn, d):
        _job(conn, type_, {"content_hash": _hash(conn, d), "extract_rev": 1}, status)
        return {}
    return setup


def _other_apply(conn, d):
    _job(conn, "gate.apply", {"doc_id": d, "decision": "approve", "decided_by": "u"})
    return {}


def _own_apply(conn, d):
    jid = _job(conn, "gate.apply", {"doc_id": d, "decision": "retype", "decided_by": "u"},
               "running")
    return {"own_job_id": jid}


def _production_link(conn, d):
    _link(conn, d, "P1", "production")
    return {}


def _own_supersedes(conn, d):
    other = _doc(conn, "older")
    conn.execute("UPDATE document SET supersedes=%s WHERE doc_id=%s", (other, d))
    return {}


def _own_superseded_by(conn, d):
    other = _doc(conn, "newer")
    conn.execute("UPDATE document SET superseded_by=%s WHERE doc_id=%s", (other, d))
    return {}


def _pointed_at(conn, d):
    other = _doc(conn, "pointer")
    conn.execute("UPDATE document SET superseded_by=%s WHERE doc_id=%s", (d, other))
    return {}


def _pointed_at_by_supersedes(conn, d):
    other = _doc(conn, "pointer2")
    conn.execute("UPDATE document SET supersedes=%s WHERE doc_id=%s", (d, other))
    return {}


PROBLEM_CASES = [
    ("clean", lambda c, d: {}, []),
    ("missing doc", _missing, ["not-found"]),
    ("published", _published, ["not-staged"]),
    ("bad type", lambda c, d: {"after": {**TO_DOC, "type": "XX"}}, ["bad-type"]),
    ("bad regulation", lambda c, d: {"after": {**TO_DOC, "regulation": "IVDR"}},
     ["bad-regulation"]),
    ("bad scope", lambda c, d: {"after": {**TO_DOC, "coverage_scope": "item"}},
     ["bad-scope"]),
    ("unchanged", lambda c, d: {"after": dict(BEFORE)}, ["unchanged"]),
    ("stale", lambda c, d: {"expected": {**BEFORE, "type": "EC"}}, ["stale"]),
    ("no expected skips the stale check", lambda c, d: {"expected": None}, []),
    ("pending validate.doc", _job_on_hash("validate.doc", "pending"), ["in-flight"]),
    ("failed validate.doc", _job_on_hash("validate.doc", "failed"), ["in-flight"]),
    ("running extract.doc", _job_on_hash("extract.doc", "running"), ["in-flight"]),
    ("pending gate.candidate", _job_on_hash("gate.candidate", "pending"), ["in-flight"]),
    ("dead validate.doc", _job_on_hash("validate.doc", "dead"), []),
    ("done validate.doc", _job_on_hash("validate.doc", "done"), []),
    ("another gate.apply", _other_apply, ["in-flight"]),
    ("own gate.apply", _own_apply, []),
    ("production link", _production_link, ["production-link"]),
    ("own supersedes", _own_supersedes, ["in-chain"]),
    ("own superseded_by", _own_superseded_by, ["in-chain"]),
    ("pointed at by superseded_by", _pointed_at, ["in-chain"]),
    ("pointed at by supersedes", _pointed_at_by_supersedes, ["in-chain"]),
]


@pytest.mark.parametrize("case, setup, codes", PROBLEM_CASES, ids=[c[0] for c in PROBLEM_CASES])
def test_problems_table(conn, case, setup, codes):
    doc_id = _doc(conn, "p")
    kw = {"doc_id": doc_id, "after": dict(TO_DOC), "expected": dict(BEFORE),
          "own_job_id": None}
    kw.update(setup(conn, doc_id))
    assert retype.problems(conn, kw["doc_id"], kw["after"], kw["expected"],
                           own_job_id=kw["own_job_id"]) == codes


def test_problems_staged_link_is_no_problem(conn):
    doc_id = _doc(conn, "sl")
    _link(conn, doc_id, "S1", "staged")
    _link(conn, doc_id, "R1", "rejected")
    assert retype.problems(conn, doc_id, TO_DOC, BEFORE) == []


def test_retype_active(conn):
    doc_id = _doc(conn, "ra")
    assert retype.retype_active(conn, doc_id) is False
    _job(conn, "gate.apply", {"doc_id": doc_id, "decision": "approve", "decided_by": "u"})
    assert retype.retype_active(conn, doc_id) is False
    _job(conn, "gate.apply", {"doc_id": doc_id + 1, "decision": "retype", "decided_by": "u"})
    assert retype.retype_active(conn, doc_id) is False
    _job(conn, "gate.apply", {"doc_id": doc_id, "decision": "retype", "decided_by": "u"},
         "dead")
    assert retype.retype_active(conn, doc_id) is False
    _job(conn, "gate.apply", {"doc_id": doc_id, "decision": "retype", "decided_by": "u"},
         "failed")
    assert retype.retype_active(conn, doc_id) is True


def test_plan_whole_range_takes_staged_links_off(conn):
    doc_id = _doc(conn, "wr", type_="DoC", scope="group")
    _link(conn, doc_id, "B2")
    _link(conn, doc_id, "B1")
    _link(conn, doc_id, "B3", "rejected")
    p = retype.plan(conn, doc_id, {"type": "EC", "regulation": "MDR",
                                   "coverage_scope": "manufacturer"})
    assert p.links_off == ["B1", "B2"]
    assert p.panel == "manufacturer"
    assert p.before == {"type": "DoC", "regulation": "MDR", "coverage_scope": "group"}
    assert p.after == {"type": "EC", "regulation": "MDR", "coverage_scope": "manufacturer"}


def test_plan_group_scope_takes_no_link_off(conn):
    doc_id = _doc(conn, "gs")
    _link(conn, doc_id, "G1")
    p = retype.plan(conn, doc_id, TO_DOC)
    assert p.links_off == []
    assert p.panel == "group"


def test_plan_certificate_to_declaration_lists_citing_docs(conn):
    cert = _doc(conn, "cert", type_="EC", cert_number="028/MDR")
    a = _doc(conn, "cite-a", type_="DoC", scope="group", cert_doc_id=cert)
    b = _doc(conn, "cite-b", type_="DoC", scope="group", cert_doc_id=cert)
    _doc(conn, "cite-other", type_="DoC", scope="group")
    p = retype.plan(conn, cert, TO_DOC)
    assert p.citations_cleared == sorted([a, b])
    assert p.citations_set == []
    assert p.clears_own_cert_doc is False
    # EC -> ISO stays a certificate: nothing is cleared
    p2 = retype.plan(conn, cert, {"type": "ISO", "regulation": "MDR",
                                  "coverage_scope": "manufacturer"})
    assert p2.citations_cleared == []


def test_plan_declaration_to_certificate_lists_docs_it_would_answer(conn):
    doc_id = _doc(conn, "dc", type_="DoC", scope="group", cert_number="CE 607775")
    plain = _doc(conn, "ans-a", type_="DoC", scope="group", cert_number="CE 607775")
    suffixed = _doc(conn, "ans-b", type_="DoC", scope="group", cert_number="CE 607775 R000")
    other_cert = _doc(conn, "elsewhere", type_="EC", cert_number="X")
    _doc(conn, "already", type_="DoC", scope="group", cert_number="CE 607775",
         cert_doc_id=other_cert)
    _doc(conn, "not-doc", type_="IFU", scope="group", cert_number="CE 607775")
    _doc(conn, "other-no", type_="DoC", scope="group", cert_number="CE 1")
    p = retype.plan(conn, doc_id, {"type": "EC", "regulation": "MDR",
                                   "coverage_scope": "manufacturer"})
    # the document itself is a DoC with that number until the write, and is not listed
    assert p.citations_set == sorted([plain, suffixed])
    assert p.citations_cleared == []
    assert p.clears_own_cert_doc is True
    assert p.cert_number == "CE 607775"


def test_plan_becoming_a_certificate_without_a_number_answers_nobody(conn):
    doc_id = _doc(conn, "nn", type_="DoC", scope="group")
    _doc(conn, "nn-cite", type_="DoC", scope="group")
    p = retype.plan(conn, doc_id, {"type": "ISO", "regulation": "MDR",
                                   "coverage_scope": "manufacturer"})
    assert p.citations_set == []


@pytest.mark.parametrize("before_number, before_to, after_type, moves", [
    ("028/MDR", None, "DoC", True),
    (None, "2030-01-01", "DoC", True),
    ("028/MDR", None, "EC", False),
    (None, None, "DoC", False),
    ("028/MDR", None, "IFU", True),
])
def test_plan_number_moves_only_when_class_changes_and_a_value_is_set(
        conn, before_number, before_to, after_type, moves):
    doc_id = _doc(conn, "nm", cert_number=before_number, validity_to=before_to)
    p = retype.plan(conn, doc_id, {"type": after_type, "regulation": "MDR",
                                   "coverage_scope": retype.default_scope(after_type)})
    assert p.number_moves is moves
    assert p.cert_number == before_number
    assert (p.validity_to.isoformat() if p.validity_to else None) == before_to


def test_plan_and_problems_write_nothing(conn):
    doc_id = _doc(conn, "ro", cert_number="028/MDR")
    _link(conn, doc_id, "W1")

    def snap():
        return (conn.execute("SELECT * FROM document ORDER BY doc_id").fetchall(),
                conn.execute("SELECT * FROM item_document ORDER BY item_ref").fetchall(),
                conn.execute("SELECT count(*) c FROM audit_log").fetchone()["c"],
                conn.execute("SELECT count(*) c FROM evidence").fetchone()["c"])
    before = snap()
    retype.problems(conn, doc_id, TO_DOC, BEFORE)
    retype.plan(conn, doc_id, TO_DOC)
    assert snap() == before


def test_module_holds_no_write_statement():
    import inspect
    import re
    src = inspect.getsource(retype)
    assert not re.search(r"\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|FOR\s+UPDATE)\b",
                         src, re.IGNORECASE)
