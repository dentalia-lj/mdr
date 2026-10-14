"""tools.prod_replay: Task 9's replay of the ladder on production's review queue.

What the report's numbers rest on: the sample grows without paying twice, the
SQL it prints is read-only and its REF gate agrees with VALIDATE's own on the
same catalogue rows, and the simulated disposition follows GATE's order.
"""

from __future__ import annotations

import pytest

from app.handlers import validate
from tools import ladder_quality as lq
from tools import prod_replay as pr

WRITES = ("INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "TRUNCATE", "GRANT", "COPY")


def _row(doc_id, selected_as, archive="/archive/ACME/unknown/x.pdf"):
    return {"doc_id": doc_id, "selected_as": selected_as, "archive_url": archive,
            "content_hash": f"{doc_id:064d}", "text_source": "pdf-text"}


def test_a_larger_sample_holds_a_smaller_one_and_keeps_every_gate_confidence_doc():
    rows = [_row(i, pr.GATE_CONF) for i in range(5)] + [_row(100 + i, pr.NII_TEXT) for i in range(40)]

    small, s_info = pr.choose(rows, 10, seed=7)
    large, l_info = pr.choose(rows, 25, seed=7)
    whole, w_info = pr.choose(rows, None, seed=7)

    nii = lambda xs: {r["doc_id"] for r in xs if r["selected_as"] == pr.NII_TEXT}  # noqa: E731
    assert nii(small) < nii(large) < nii(whole) and len(nii(small)) == 10
    assert all({r["doc_id"] for r in xs if r["selected_as"] == pr.GATE_CONF} == set(range(5))
               for xs in (small, large, whole))
    assert s_info["seed"] == 7 and len(s_info["trimmed_doc_ids"]) == 30
    assert w_info["seed"] is None and w_info["trimmed_doc_ids"] == []


def test_only_archive_paths_are_copied():
    assert pr._local("/archive/KOMET/unknown/a.pdf") == "pdfs/KOMET/unknown/a.pdf"
    with pytest.raises(SystemExit):
        pr._local("/imports/KOMET/a.pdf")


@pytest.mark.parametrize("sql", [pr.SELECT_SQL, pr.overlap_sql([
    {"doc_id": 1, "arm": "new", "group_id": None, "printed": "Acme", "refs": ["A-1"], "udi": None}])])
def test_every_statement_is_read_only(sql):
    lines = [x for x in sql.strip().splitlines() if x.strip()]
    assert lines[0] == "BEGIN READ ONLY;" and lines[-1] == "ROLLBACK;"
    body = sql.upper()
    assert not any(f"{w} " in body for w in WRITES)


def _run_overlap(conn, rows):
    sql = pr.overlap_sql(rows).strip().splitlines()
    assert sql[1].startswith("SET LOCAL statement_timeout")
    body = "\n".join(sql[2:-1]).rstrip().rstrip(";")
    return {(r["json_build_object"]["doc_id"], r["json_build_object"]["arm"]): r["json_build_object"]
            for r in conn.execute(body).fetchall()}


def _seed(conn):
    conn.execute("INSERT INTO manufacturer_alias (raw_name, canonical_name) VALUES "
                 "('Acme GmbH', 'ACME'), ('777', 'ACME'), ('001', 'IVOCLAR')")
    items = [("X1", "777", "A-1", None), ("X2", "777", None, None), ("X3", "777", "A-3", "0761-U"),
             ("Y1", "OTH", "A-1", None), ("V1", "001", "645986", None)]
    for ref, raw, mfr, udi in items:
        conn.execute("INSERT INTO item_mirror (item_ref, name, manufacturer_raw, mfr_ref, udi, "
                     "md_flag, catalogue, updated_at) VALUES (%s,'w',%s,%s,%s,TRUE,'LJ',now())",
                     (ref, raw, mfr, udi))
    gid = {}
    for name, canon, udi, members in (("g1", "ACME", None, ("X1", "X2")), ("g2", "ACME", "BUDI-1", ("X3",)),
                                      ("g3", "OTHER", None, ("Y1",)), ("g4", "IVOCLAR", None, ("V1",))):
        gid[name] = conn.execute("INSERT INTO item_group (canonical_manufacturer, basic_udi_di) "
                                 "VALUES (%s,%s) RETURNING group_id", (canon, udi)).fetchone()["group_id"]
        for m in members:
            mfr = {"X1": "A-1", "X3": "A-3", "Y1": "A-1", "V1": "645986"}.get(m)
            conn.execute("INSERT INTO item_group_member (group_id, item_ref, mfr_ref, match_basis) "
                         "VALUES (%s,%s,%s,'manual')", (gid[name], m, mfr))
    return gid


def test_the_ref_gate_in_sql_agrees_with_validate(conn):
    gid = _seed(conn)
    cases = [
        {"doc_id": 1, "arm": "new", "group_id": None, "printed": "acme   gmbh", "refs": ["A-1", "X2"], "udi": None},
        {"doc_id": 2, "arm": "new", "group_id": None, "printed": "Acme GmbH", "refs": [], "udi": "BUDI-1"},
        {"doc_id": 3, "arm": "new", "group_id": None, "printed": "Nobody Ltd", "refs": ["A-1"], "udi": None},
        {"doc_id": 4, "arm": "new", "group_id": None, "printed": "Acme GmbH", "refs": ["ZZZ"], "udi": None},
        {"doc_id": 5, "arm": "new", "group_id": None, "printed": "IVOCLAR", "refs": ["645986DC"], "udi": None},
    ]
    got = _run_overlap(conn, cases)

    for c in cases:
        canon = validate._canonicalize_manufacturer(conn, c["printed"])
        links, matched, found, _ = validate._links_for_manufacturer(conn, canon, c["refs"], c["udi"])
        o = got[(c["doc_id"], "new")]
        assert (o["items_matched"], o["groups_matched"], o["groups_found"]) == \
            (len(links), matched, found), c
    assert got[(1, "new")]["basis"] == {"ref-list": 1, "ref-item": 1}
    assert got[(1, "new")]["mirror_ref_items"] == 2           # X1, X2; never OTHER's Y1
    assert got[(2, "new")]["udi_group_match"] == 1
    assert got[(5, "new")]["strategy"] == "strip-trailing-letters"

    refs = ["A-1", "645986", "NOPE-00"]
    unscoped = _run_overlap(conn, [{"doc_id": 7, "arm": "new", "group_id": None, "printed": None,
                                    "refs": refs, "udi": None}])[(7, "new")]
    holders = validate._manufacturers_holding_refs(conn, validate._eligible_unscoped_refs(refs, 6))
    assert unscoped["canon"] is None and unscoped["unscoped_holders"] == len(holders) == 1
    assert bool(unscoped["unscoped_any"]) == (validate._resolve_group_unscoped(conn, refs, None)
                                             is not None)

    in_group = _run_overlap(conn, [{"doc_id": 6, "arm": "new", "group_id": gid["g1"],
                                    "printed": None, "refs": ["A-3", "A-1"], "udi": None}])[(6, "new")]
    assert in_group["canon"] == "ACME" and in_group["in_group_items"] == 1   # X1; X3 is g2's
    assert in_group["items_matched"] == 2


def _ev(value, conf=0.97, tier="T1"):
    return {"value": value, "conf": conf, "verbatim": f"says {value}", "tier": tier, "page": 1,
            "model_id": "claude-haiku-5-5"}


def _fields(scope="group", refs=("A-1",), conf=0.97, **extra):
    f = {"type": _ev("DoC", conf), "regulation": _ev("MDR", conf), "coverage_scope": _ev(scope, conf),
         "manufacturer": _ev("Acme GmbH")}
    if refs:
        f["ref_list"] = _ev(list(refs))
    f.update(extra)
    return f


def _sel(group_id=None, flags=()):
    return {"extract_group_id": group_id, "gate_result": {"flags": list(flags)}}


MATCH = {"canon": "ACME", "items_matched": 2, "groups_matched": 1, "groups_found": 2}


@pytest.mark.parametrize("fields, sel, ov, disposition", [
    (_fields(), _sel(), MATCH, "production"),
    (_fields(), _sel(), {**MATCH, "groups_matched": 2}, "production"),          # informational
    (_fields(conf=0.8), _sel(), MATCH, "staged"),
    (_fields(conf=0.6), _sel(), MATCH, "manual"),
    (_fields(), _sel(), {"canon": "ACME", "items_matched": 0, "groups_found": 2}, "filed"),
    (_fields(scope="manufacturer"), _sel(), {"canon": "ACME", "items_matched": 0, "groups_found": 2},
     "staged"),                                                                   # C15 spares it
    (_fields(), _sel(), {"canon": "NOBODY", "items_matched": 0, "groups_found": 0}, "manual"),
    (_fields(refs=()), _sel(), MATCH, "manual"),                                 # no-item-identifier
    (_fields(scope="manufacturer", refs=()), _sel(), None, "mfr-binding"),
    (_fields(), _sel(group_id=5), {"canon": "ACME", "in_group_items": 0}, "staged"),  # fetch-context
    (_fields(manufacturer=_ev(None)), _sel(), {"unscoped_holders": 1, "unscoped_any": 1}, "staged"),  # ref-catalogue
    (_fields(manufacturer=_ev(None)), _sel(), {"unscoped_holders": 2, "unscoped_any": 2}, "staged"),
    (_fields(manufacturer=_ev(None)), _sel(), {"unscoped_any": 1}, "staged"),              # ref-unscoped
    (_fields(manufacturer=_ev(None)), _sel(), {}, "filed"),                                # no-ref-overlap
    (_fields(), _sel(group_id=5), {"canon": "ACME", "in_group_items": 3}, "production"),
    (_fields(), _sel(flags=["older-than-current"]), MATCH, "manual"),            # carried over
    (_fields(), _sel(flags=["no-item-identifier", "no-ref-overlap"]), MATCH, "production"),
    (_fields(validity_to=_ev("2031-01-01")), _sel(), MATCH, "staged"),           # expiry-on-certless-doc
])
def test_the_simulated_disposition_follows_gate(fields, sel, ov, disposition):
    assert pr.outcome(fields, "DoC", sel, ov, lq.config())["disposition"] == disposition


def test_a_refused_document_and_an_empty_gate_field():
    cfg = lq.config()
    assert pr.outcome(_fields(), pr.tiers.NOT_A_DOCUMENT, _sel(), MATCH, cfg)["disposition"] == "refused"
    f = _fields()
    f["regulation"]["value"] = None
    assert pr.outcome(f, "DoC", _sel(), MATCH, cfg)["disposition"] == "no-candidate"
