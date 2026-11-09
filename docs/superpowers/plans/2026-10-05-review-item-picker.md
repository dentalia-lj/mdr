# Review Item Picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On Review, a reviewer finds and ticks the exact items a document covers; Approve publishes the document and writes those items as confirmed (`manual`) links under the reviewer's name. The same picker adds items to a published document (`add-items`).

**Architecture:** One shared check (`app/item_picks.py`) used by both the web route (422 before enqueue) and GATE (raise before any write). GATE's `gate.apply` gains an optional `items` field on `approve` and a new `add-items` decision. The web side adds a read-only module (`web/item_picker.py`: search, similar, suggestions, row decoration), server-rendered fragments over htmx, and one small page script that keeps the tick set and copies it into the decision form on Done. No migration.

**Tech Stack:** Python 3.12, FastAPI + Jinja + htmx, psycopg 3, Postgres `pg_trgm` (`word_similarity`, `similarity`), pytest against real Postgres via `./scripts/test.sh`.

**Spec:** `docs/superpowers/specs/2026-10-02-review-item-picker-design.md` (same worktree). **Mockup (approved 2026-10-02):** `docs/superpowers/specs/2026-10-02-review-item-picker-mockup.html`, open it in a browser; the UI tasks match it.

## Global Constraints

- Worktree `.claude/worktrees/review-item-picker`, branch `review-item-picker`, from main `4bd2c65`. Never work on main.
- Invariant 1: only `gate.candidate` / `gate.apply` write `document`, `item_document`, `evidence`. The web only enqueues `gate.apply`.
- Payload fields additive only: a `gate.apply approve` payload without `items` behaves exactly as today.
- No automatic linking of any kind. Nothing is pre-ticked. A suggestion never links.
- The manufacturer is locked: no route and no handler path may return or link an item outside `manufacturers.item_codes_for(conn, manufacturer)`.
- Search and Similar are capped at **200** rows (`CAP = 200`).
- Every route refusal is a 422 whose message ends "Nothing was changed." and enqueues nothing.
- UI copy: "items", never "products"; counts through the `num` filter; dates through the `day` filter; no em dashes in new copy.
- Every test run goes through `./scripts/test.sh` (a bare `pytest` is blocked). The stack must be up: `docker compose up -d` then `docker compose --profile test up -d test`.
- Test selection: `tests/test_<module>*.py` per module touched, plus `tests/test_web.py` when anything under `web/` changed; the full suite once before the final commit.
- Commit messages: plain, no mention of Claude or AI, no `Co-Authored-By` line. Commit by path (`git add <paths>`), never `git add -A`.
- The server (91.98.42.140) is read-only, and only with Denis's go. Never put a password on a command line.
- Docs ship with the code: a screen change updates `docs/guide/pages/<screen>.md` and `.sl.md`, then `python3 scripts/build-guide.py`, and commits the bundles too.

## Review Focus

1. **Search text that is only spaces, punctuation or LIKE metacharacters** (`""`, `"   "`, `"%"`, `"_"`), or a number that matches nothing: the picker lists nothing; `%` is never a wildcard, and a number is never searched as a name. Pinned in Task 2.
2. **A manufacturer that resolves to no BC code**: every list is empty and says so; the route refuses items; GATE raises. Pinned in Tasks 2 and 1.
3. **A link refused between the picker's render and Approve** (a `reject-link` lands first): GATE raises naming the item and writes nothing, not even the promote. Pinned in Task 1.
4. **The same item ticked twice** (from a search and from Similar, or sent twice in a hand-built payload): one link, one `link-confirmed` + `production-write` pair. Pinned in Tasks 1 and 4.
5. **A ticked item that left the catalogue** (BC sync deleted it from `item_mirror`) before Approve: GATE raises naming it as unknown. Pinned in Task 1.

---

## Before execution: thresholds (done 2026-10-05)

Measured on the server with `tools/picker_thresholds.sql` and two follow-up
queries; figures and Denis's rulings in `docs/state/2026-10-05-picker-thresholds.md`,
spec §4.3-4.4 updated. **Names: 0.5 with punctuation ignored. Similar: 0.2.
Nearby numbers: ±10.** Commit `tools/picker_thresholds.sql` and the state file
with Task 2.

---

### Task 1: GATE: `approve` with `items`, and `add-items`

**Files:**
- Create: `app/item_picks.py`
- Modify: `app/handlers/gate.py` (`handle_gate_apply` at ~1443; new helpers beside `_apply_link_decision`)
- Modify: `web/words.py:140-160` (`AUDIT_EVENT_WORDS`)
- Modify: `docs/dentalia-pipeline-contract-prd-v3.md` (lines ~269, ~320, ~337, ~437)
- Modify: `docs/dentalia-job-type-handbook.md` §8 (`gate.apply`, ~869)
- Modify: `docs/dev/handlers.md:116`
- Modify: `docs/decisions.md` (4 rows), `CLAUDE.md:23` (row count), `tasks/followups.md`
- Test: `tests/test_gate_apply_items.py` (new), `tests/test_item_picks.py` (new)

**Interfaces:**
- Produces: `app.item_picks.check_items(conn, doc_id: int, manufacturer: str | None, refs: list[str]) -> dict[str, list[str]]`, keys among `"unknown"`, `"other-manufacturer"`, `"refused"`, each a sorted list of item refs; `{}` when the tick set is clean.
- Produces: `gate.apply` payload `items?: list[str]` on `approve` and `add-items`; `manufacturer?: str` (canonical name) used when the document has none. Decision `add-items` (document must be `production`). Audit: per changed link `link-confirmed` (detail `match_basis_before`, `link_status_before`, `via`) then `production-write`; the decision row's detail gains `{"items": <count>, "manufacturer": <name>}`.

- [ ] **Step 1: Write the failing tests for `check_items`** in `tests/test_item_picks.py`:

```python
"""The picker's tick-set check, shared by the route and GATE (spec §5.1, §5.3)."""
from __future__ import annotations

from app import item_picks


def _item(conn, ref, raw):
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, catalogue, "
        "mirror_rev, updated_at) VALUES (%s,'n',%s,NULL,'LJ',1,now())", (ref, raw))


def _doc(conn):
    return conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, archive_url, status) "
        "VALUES ('IFU','MDR','group','picks-1','file:///a.pdf','staged') RETURNING doc_id"
    ).fetchone()["doc_id"]


def test_a_clean_tick_set_has_no_problems(conn):
    _item(conn, "A1", "ACME")
    assert item_picks.check_items(conn, _doc(conn), "ACME", ["A1"]) == {}


def test_each_kind_of_problem_is_named(conn):
    doc_id = _doc(conn)
    _item(conn, "A1", "ACME")
    _item(conn, "B1", "OTHER")
    _item(conn, "R1", "ACME")
    conn.execute("INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
                 "VALUES ('R1', %s, 'name-family', 'rejected')", (doc_id,))
    got = item_picks.check_items(conn, doc_id, "ACME", ["A1", "B1", "R1", "GONE", "A1"])
    assert got == {"unknown": ["GONE"], "other-manufacturer": ["B1"], "refused": ["R1"]}


def test_a_manufacturer_with_no_codes_owns_no_item(conn):
    _item(conn, "A1", "ACME")
    got = item_picks.check_items(conn, _doc(conn), "NOBODY", ["A1"])
    assert got == {"other-manufacturer": ["A1"]}
```

- [ ] **Step 2: Run them to see them fail**

Run: `./scripts/test.sh tests/test_item_picks.py`
Expected: FAIL, `ImportError: cannot import name 'item_picks'`.

- [ ] **Step 3: Write `app/item_picks.py`**

```python
"""The Review item picker's tick-set check (spec 2026-10-02 §5.1, §5.3).

One function, read-only, shared by the two places that must refuse a bad tick
set: the web route, which answers 422 before enqueueing, and GATE, which is the
last word against hand-built payloads and replays and raises before writing.
"""
from __future__ import annotations

from app import manufacturers


def check_items(conn, doc_id: int, manufacturer: str | None,
                refs: list[str]) -> dict[str, list[str]]:
    """What is wrong with ticking `refs` for `doc_id` under `manufacturer`.

    `unknown`: not in the catalogue mirror (never synced, or BC deleted it).
    `other-manufacturer`: not under any BC code the manufacturer fans out to
    (`item_codes_for`); a name with no codes owns nothing, so every item is
    this. `refused`: a person rejected this item's link to this document
    (C17); it comes back only through `reopen-link`. Empty dict when clean.
    """
    codes = set(manufacturers.item_codes_for(conn, manufacturer)) if manufacturer else set()
    rows = conn.execute(
        "SELECT t.item_ref, m.manufacturer_raw, l.status "
        "FROM unnest(%s::text[]) AS t(item_ref) "
        "LEFT JOIN item_mirror m ON m.item_ref = t.item_ref "
        "LEFT JOIN item_document l ON l.item_ref = t.item_ref AND l.doc_id = %s "
        "ORDER BY t.item_ref",
        (sorted(set(refs)), doc_id),
    ).fetchall()
    problems: dict[str, list[str]] = {}
    for r in rows:
        if r["manufacturer_raw"] is None:
            kind = "unknown"
        elif r["manufacturer_raw"] not in codes:
            kind = "other-manufacturer"
        elif r["status"] == "rejected":
            kind = "refused"
        else:
            continue
        problems.setdefault(kind, []).append(r["item_ref"])
    return problems
```

- [ ] **Step 4: Run the `check_items` tests to see them pass**

Run: `./scripts/test.sh tests/test_item_picks.py`
Expected: 3 passed.

- [ ] **Step 5: Write the failing GATE tests** in `tests/test_gate_apply_items.py`:

```python
"""gate.apply `approve` with `items`, and `add-items` (picker spec §5).

A ticked item becomes a confirmed link exactly as `confirm-link` records one:
`manual`, production, `link-confirmed` + `production-write` under the
reviewer's name. Failures raise before any write. Tests don't commit.
"""
from __future__ import annotations

import pytest

from app.handlers import gate as gh

MFR = "ACME"


def _mfr(conn, name=MFR):
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES (%s) "
                 "ON CONFLICT (canonical_name) DO NOTHING", (name,))


def _item(conn, ref, raw=MFR):
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, catalogue, "
        "mirror_rev, updated_at) VALUES (%s,'n',%s,NULL,'LJ',1,now()) "
        "ON CONFLICT (item_ref) DO NOTHING", (ref, raw))


def _doc(conn, key, *, status="staged", canonical=MFR, scope="group"):
    if canonical:
        _mfr(conn, canonical)
    return conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, archive_url, "
        "status, validity_from, canonical_manufacturer) "
        "VALUES ('IFU','MDR',%s,%s,'file:///a.pdf',%s,'2026-04-01',%s) RETURNING doc_id",
        (scope, f"pick-{key}", status, canonical)).fetchone()["doc_id"]


def _job(doc_id, decision="approve", jid=31, **extra):
    payload = {"doc_id": doc_id, "decision": decision, "decided_by": "user:natasa", **extra}
    return {"id": jid, "type": "gate.apply", "payload": payload,
            "dedupe_key": f"apply:{doc_id}:{decision}"}


def _link(conn, doc_id, ref):
    return conn.execute(
        "SELECT status, match_basis, udi FROM item_document WHERE doc_id=%s AND item_ref=%s",
        (doc_id, ref)).fetchone()


def _trail(conn, doc_id):
    return [(r["event"], r["item_ref"]) for r in conn.execute(
        "SELECT event, item_ref FROM audit_log WHERE doc_id=%s ORDER BY id", (doc_id,))]


def _detail(conn, doc_id, event):
    return conn.execute("SELECT detail FROM audit_log WHERE doc_id=%s AND event=%s",
                        (doc_id, event)).fetchone()["detail"]


def _doc_status(conn, doc_id):
    return conn.execute("SELECT status FROM document WHERE doc_id=%s",
                        (doc_id,)).fetchone()["status"]


def test_approve_links_ticked_items_as_confirmed_links(conn):
    doc_id = _doc(conn, "ok")
    _item(conn, "A1"); _item(conn, "A2")

    gh.handle_gate_apply(conn, _job(doc_id, items=["A2", "A1"]))

    assert _doc_status(conn, doc_id) == "production"
    for ref in ("A1", "A2"):
        assert dict(_link(conn, doc_id, ref)) == {"status": "production", "match_basis": "manual", "udi": None}
    assert _trail(conn, doc_id) == [
        ("link-confirmed", "A1"), ("production-write", "A1"),
        ("link-confirmed", "A2"), ("production-write", "A2"),
        ("approve", None), ("production-write", None)]
    assert _detail(conn, doc_id, "approve") == {"items": 2, "manufacturer": MFR}
    assert _detail(conn, doc_id, "link-confirmed")["via"] == "approve"


def test_approve_without_items_is_unchanged(conn):
    doc_id = _doc(conn, "plain")
    gh.handle_gate_apply(conn, _job(doc_id))
    assert _doc_status(conn, doc_id) == "production"
    assert _trail(conn, doc_id) == [("approve", None), ("production-write", None)]
    assert _detail(conn, doc_id, "approve") is None


def test_duplicate_ticks_link_once(conn):
    doc_id = _doc(conn, "dup")
    _item(conn, "A1")
    gh.handle_gate_apply(conn, _job(doc_id, items=["A1", "A1"]))
    assert [e for e in _trail(conn, doc_id) if e[1] == "A1"] == [
        ("link-confirmed", "A1"), ("production-write", "A1")]
    assert _detail(conn, doc_id, "approve")["items"] == 1


@pytest.mark.parametrize("bad, raw, kind", [
    ("B1", "OTHER", "other-manufacturer"),   # another manufacturer's item
    ("GONE", None, "unknown"),               # left the catalogue (Review Focus 5)
])
def test_a_bad_item_raises_and_writes_nothing(conn, bad, raw, kind):
    doc_id = _doc(conn, f"bad-{bad}")
    _item(conn, "A1")
    if raw:
        _item(conn, bad, raw)
    with pytest.raises(ValueError, match=rf"{kind}: {bad}"):
        gh.handle_gate_apply(conn, _job(doc_id, items=["A1", bad]))
    assert _doc_status(conn, doc_id) == "staged"       # raised before the promote
    assert _link(conn, doc_id, "A1") is None
    assert _trail(conn, doc_id) == []


def test_a_link_refused_meanwhile_raises(conn):
    """Review Focus 3: a reject-link landed between render and Approve."""
    doc_id = _doc(conn, "refused")
    _item(conn, "R1")
    conn.execute("INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
                 "VALUES ('R1', %s, 'name-family', 'rejected')", (doc_id,))
    with pytest.raises(ValueError, match="refused: R1"):
        gh.handle_gate_apply(conn, _job(doc_id, items=["R1"]))
    assert _doc_status(conn, doc_id) == "staged"
    assert _link(conn, doc_id, "R1")["status"] == "rejected"


def test_no_manufacturer_anywhere_raises(conn):
    doc_id = _doc(conn, "nomfr", canonical=None)
    _item(conn, "A1")
    with pytest.raises(ValueError, match="no manufacturer"):
        gh.handle_gate_apply(conn, _job(doc_id, items=["A1"]))


def test_a_manufacturer_we_do_not_hold_counts_as_none(conn):
    doc_id = _doc(conn, "unheld", canonical=None)
    _item(conn, "A1")
    with pytest.raises(ValueError, match="no manufacturer"):
        gh.handle_gate_apply(conn, _job(doc_id, items=["A1"], manufacturer="NOT A ROW"))


def test_the_payload_manufacturer_is_written_when_the_document_has_none(conn):
    doc_id = _doc(conn, "setmfr", canonical=None)
    _mfr(conn)
    _item(conn, "A1")
    gh.handle_gate_apply(conn, _job(doc_id, items=["A1"], manufacturer=MFR))
    assert conn.execute("SELECT canonical_manufacturer FROM document WHERE doc_id=%s",
                        (doc_id,)).fetchone()["canonical_manufacturer"] == MFR


def test_a_conflicting_manufacturer_raises(conn):
    doc_id = _doc(conn, "conflict")
    _mfr(conn, "GLOBEX")
    _item(conn, "A1")
    with pytest.raises(ValueError, match="GLOBEX"):
        gh.handle_gate_apply(conn, _job(doc_id, items=["A1"], manufacturer="GLOBEX"))


def test_a_whole_range_document_takes_no_items(conn):
    doc_id = _doc(conn, "range", scope="manufacturer")
    _item(conn, "A1")
    with pytest.raises(ValueError, match="whole range"):
        gh.handle_gate_apply(conn, _job(doc_id, items=["A1"]))


def test_a_staged_weak_link_becomes_manual_and_keeps_its_udi(conn):
    doc_id = _doc(conn, "weak")
    _item(conn, "N1")
    conn.execute("INSERT INTO item_document (item_ref, doc_id, udi, match_basis, status) "
                 "VALUES ('N1', %s, '0761234', 'name-family', 'staged')", (doc_id,))
    gh.handle_gate_apply(conn, _job(doc_id, items=["N1"]))
    assert dict(_link(conn, doc_id, "N1")) == {
        "status": "production", "match_basis": "manual", "udi": "0761234"}
    d = _detail(conn, doc_id, "link-confirmed")
    assert d["match_basis_before"] == "name-family" and d["link_status_before"] == "staged"


def test_a_trusted_link_is_left_alone(conn):
    """A staged ref-list link follows the document (`_promote_pending_links`);
    ticking it as well rewrites nothing and audits nothing extra."""
    doc_id = _doc(conn, "trusted")
    _item(conn, "T1")
    conn.execute("INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
                 "VALUES ('T1', %s, 'ref-list', 'staged')", (doc_id,))
    gh.handle_gate_apply(conn, _job(doc_id, items=["T1"]))
    assert dict(_link(conn, doc_id, "T1")) == {
        "status": "production", "match_basis": "ref-list", "udi": None}
    assert ("link-confirmed", "T1") not in _trail(conn, doc_id)


def test_a_redelivered_approve_writes_no_second_pair(conn):
    doc_id = _doc(conn, "again")
    _item(conn, "A1")
    gh.handle_gate_apply(conn, _job(doc_id, items=["A1"]))
    gh.handle_gate_apply(conn, _job(doc_id, items=["A1"]))
    assert [e for e in _trail(conn, doc_id) if e[1] == "A1"] == [
        ("link-confirmed", "A1"), ("production-write", "A1")]


def test_reject_ignores_items(conn):
    doc_id = _doc(conn, "rej")
    _item(conn, "A1")
    gh.handle_gate_apply(conn, _job(doc_id, decision="reject", items=["A1"]))
    assert _link(conn, doc_id, "A1") is None


def test_add_items_links_on_a_published_document(conn):
    doc_id = _doc(conn, "pub", status="production")
    _item(conn, "A1")
    gh.handle_gate_apply(conn, _job(doc_id, decision="add-items", items=["A1"]))
    assert _link(conn, doc_id, "A1")["match_basis"] == "manual"
    assert _trail(conn, doc_id) == [
        ("link-confirmed", "A1"), ("production-write", "A1"), ("add-items", None)]
    assert _detail(conn, doc_id, "add-items") == {"items": 1, "manufacturer": MFR}
    assert _detail(conn, doc_id, "link-confirmed")["via"] == "add-items"


def test_add_items_refuses_a_document_that_is_not_published(conn):
    doc_id = _doc(conn, "notpub")
    _item(conn, "A1")
    with pytest.raises(ValueError, match="not 'production'"):
        gh.handle_gate_apply(conn, _job(doc_id, decision="add-items", items=["A1"]))


def test_add_items_without_items_raises(conn):
    doc_id = _doc(conn, "empty", status="production")
    with pytest.raises(ValueError, match="items"):
        gh.handle_gate_apply(conn, _job(doc_id, decision="add-items"))
```

- [ ] **Step 6: Run them to see them fail**

Run: `./scripts/test.sh tests/test_gate_apply_items.py`
Expected: FAIL; the approve tests link nothing, `add-items` raises "unknown decision".

- [ ] **Step 7: Implement in `app/handlers/gate.py`.** Change the import `from app import manufacturers, queue` to `from app import item_picks, manufacturers, queue`. Add after `_apply_link_decision`:

```python
def _picked_items(conn, doc_id, p) -> tuple[str, list[str]] | None:
    """The reviewer's ticks on `approve` / `add-items` (picker spec §5.3 steps
    1-2): (manufacturer, sorted unique item refs), or None when there are none.

    Runs BEFORE any write, so a refused payload leaves the registry as it was
    even outside the runner's rollback. Every failure raises and names what
    failed: these are a person's ticks, and a partial link would read to them
    as the decision being ignored (C17).
    """
    items = p.get("items")
    if not items:
        return None
    decision = p["decision"]
    if not isinstance(items, list) or not all(isinstance(i, str) and i.strip() for i in items):
        raise ValueError(f"gate.apply {decision}: items must be a list of item numbers")
    doc = conn.execute(
        "SELECT coverage_scope, canonical_manufacturer FROM document WHERE doc_id=%s",
        (doc_id,)).fetchone()
    if doc is None:
        raise LookupError(f"gate.apply {decision}: no document {doc_id}")
    if doc["coverage_scope"] != "group":
        raise ValueError(f"gate.apply {decision}: document {doc_id} covers a whole range; "
                         f"items cannot be chosen for it")
    confirmed = doc["canonical_manufacturer"]
    asked = (p.get("manufacturer") or "").strip() or None
    if confirmed and asked and asked != confirmed:
        raise ValueError(f"gate.apply {decision}: document {doc_id} is {confirmed}'s; "
                         f"the payload names {asked}")
    manufacturer = confirmed or _bindable_manufacturer(conn, asked)
    if manufacturer is None:
        raise ValueError(f"gate.apply {decision}: document {doc_id} has no manufacturer "
                         f"and the payload names none we hold")
    refs = sorted(set(items))
    problems = item_picks.check_items(conn, doc_id, manufacturer, refs)
    if problems:
        raise ValueError(f"gate.apply {decision}: document {doc_id}: " + "; ".join(
            f"{kind}: {', '.join(found)}" for kind, found in problems.items()))
    return manufacturer, refs


def _write_items(conn, doc_id, manufacturer, refs, decided_by, job, via) -> None:
    """Picker spec §5.3 steps 1, 3, 4: record the manufacturer on a document
    that had none, then link each ticked item the way `confirm-link` does.

    The link rows are read HERE, after `_promote_pending_links`, not when the
    ticks were checked: a staged trusted-basis link has just followed the
    document to production, and must be skipped rather than re-based and
    audited a second time. `_upsert_link` keeps `production` and `rejected`
    sticky; `rejected` was refused before any write. The existing `udi` is
    passed back because the upsert overwrites a non-sticky row's `udi`.
    """
    conn.execute(
        "UPDATE document SET canonical_manufacturer=%s "
        "WHERE doc_id=%s AND canonical_manufacturer IS NULL", (manufacturer, doc_id))
    current = {r["item_ref"]: r for r in conn.execute(
        "SELECT item_ref, status, match_basis, udi FROM item_document "
        "WHERE doc_id=%s AND item_ref = ANY(%s)", (doc_id, refs)).fetchall()}
    for ref in refs:
        link = current.get(ref)
        if link and link["status"] == "production":
            continue  # already trusted: basis kept, nothing to audit, re-delivery is quiet
        _upsert_link(conn, ref, doc_id, link["udi"] if link else None, "manual", "production")
        _audit(conn, "link-confirmed", doc_id, decided_by, job, item_ref=ref,
               detail={"match_basis_before": link["match_basis"] if link else None,
                       "link_status_before": link["status"] if link else None,
                       "via": via})
        _audit(conn, "production-write", doc_id, decided_by, job, item_ref=ref)
```

Then in `handle_gate_apply`, after `prev_status = _doc_status(conn, doc_id)`:

```python
    # Picker spec §5: the reviewer's ticks, checked before anything is written.
    if decision == "add-items" and prev_status != "production":
        raise ValueError(f"gate.apply add-items: document {doc_id} is {prev_status!r}, "
                         f"not 'production' -- items are added to a published document")
    picked = _picked_items(conn, doc_id, p) if decision in ("approve", "add-items") else None
    if decision == "add-items" and picked is None:
        raise ValueError("gate.apply add-items: items is required")
    items_detail = None
```

In the `approve` branch, directly after `_promote_pending_links(conn, doc_id)`:

```python
        if picked:
            _write_items(conn, doc_id, *picked, decided_by, job, via="approve")
            items_detail = {"items": len(picked[1]), "manufacturer": picked[0]}
```

Add a branch before the final `else`:

```python
    elif decision == "add-items":
        # Picker spec §3.1: the approve link steps on a published document,
        # without the promote. Ticks were checked above.
        _write_items(conn, doc_id, *picked, decided_by, job, via="add-items")
        items_detail = {"items": len(picked[1]), "manufacturer": picked[0]}
```

Replace the decision's own audit line:

```python
    detail = _note_detail(p)
    if items_detail:
        detail = {**(detail or {}), **items_detail}
    _audit(conn, decision, doc_id, decided_by, job, detail=detail)
```

Update the comment in `_apply_link_decision` that reads "Sole writer of match_basis='manual' in the codebase" to: "One of the two writers of match_basis='manual': this and `_write_items` (the picker's ticks on approve / add-items, PRD C17 as amended 2026-10-05). Both write the same link-confirmed + production-write pair."

- [ ] **Step 8: Run the GATE tests to see them pass**

Run: `./scripts/test.sh tests/test_gate_apply_items.py tests/test_gate_apply_handler.py tests/test_item_picks.py`
Expected: all pass. `test_confirm_link_is_the_only_writer_of_manual_basis` keeps passing (it asserts what `confirm-link` writes, not exclusivity); if it asserts exclusivity by grep, update its docstring and assertion to name both writers.

- [ ] **Step 9: Audit wording.** In `web/words.py` `AUDIT_EVENT_WORDS`, under "a person's four decisions", rename the comment to "a person's decisions" and add `"add-items": "Items added",`.

- [ ] **Step 10: PRD, handbook, handlers doc.**
  - PRD ~269: the `manual` row becomes `| manual | production (human is the authority) — written **only** by a person's decision: \`gate.apply confirm-link\` (C17), or \`approve\` / \`add-items\` carrying \`items\` (picker, 2026-10-05) |`
  - PRD ~320: replace "`confirm-link` is the **sole writer** of `match_basis = 'manual'`;" with "`confirm-link` and the picker's `items` on `approve` / `add-items` (2026-10-05, spec `docs/superpowers/specs/2026-10-02-review-item-picker-design.md`) are the **only writers** of `match_basis = 'manual'`, and both write the same `link-confirmed` + `production-write` pair per link;"
  - PRD ~337: the decision list becomes `approve | reject | bind-manufacturer | reopen | add-items | confirm-link | reject-link | reopen-link`, the field list gains `items?`, and append: "*`items?` (2026-10-05, picker spec §5.2): a list of `item_ref` on `approve` or `add-items`; each becomes a `manual` production link, checked first against the document's manufacturer (`canonical_manufacturer`, or the payload's `manufacturer` when it has none) and against refused links, any failure raising before a write. `add-items` requires a `production` document and runs the same link steps without the promote; its web dedupe key is `apply:{doc_id}:add-items:{digest of the sorted items}`.*"
  - PRD ~437: "`match_basis = 'manual'` has exactly one writer, `gate.apply confirm-link`." becomes "`match_basis = 'manual'` is written only by a person's decision: `gate.apply confirm-link`, or `approve` / `add-items` carrying `items`."
  - Handbook §8: add a payload example `{ "doc_id": 8, "decision": "approve", "decided_by": "user:natasa", "items": ["010.6042", "022.0026"] }` and one paragraph stating the §5.3 steps.
  - `docs/dev/handlers.md:116`: consumes gains `items?`; document decisions gain `add-items`.

- [ ] **Step 11: Rulings.** Append to `docs/decisions.md` the four 2026-10-02 rows from `git show 3765aed:docs/decisions.md` whose titles are "A document that states no issue date", "Review: documents that name no items (client request, Natasa, call 2026-10-02)", "Issue date required before approval", "Adding items to a document already published". In the picker row, replace "the per-item rule ships first, because Approve relies on it for items that already hold a document of the same type." with "Approve retires nothing for the ticked items: an item that already holds a document of the type holds both (spec §6, amended 2026-10-05); per-item currency is separate work." Change `CLAUDE.md:23` "102 rows" to the new count (`grep -c '^| 20' docs/decisions.md`). In `tasks/followups.md`, add under the existing `[review-docs-without-article-numbers]` item: "Ruled 2026-10-02: the Review item picker (spec `docs/superpowers/specs/2026-10-02-review-item-picker-design.md`); closes when it ships."

- [ ] **Step 12: Commit**

```bash
git add app/item_picks.py app/handlers/gate.py web/words.py tests/test_item_picks.py \
  tests/test_gate_apply_items.py docs/dentalia-pipeline-contract-prd-v3.md \
  docs/dentalia-job-type-handbook.md docs/dev/handlers.md docs/decisions.md CLAUDE.md tasks/followups.md
git commit -m "gate: approve links the items a reviewer ticked; add-items for published documents"
```

---

### Task 2: The picker's reads (`web/item_picker.py`)

**Files:**
- Create: `web/item_picker.py`
- Test: `tests/test_item_picker.py` (new)

**Interfaces:**
- Consumes: `app.manufacturers.item_codes_for(conn, name) -> list[str]`.
- Produces (all read-only, `doc` is a dict with `doc_id`, `type`, `validity_from`, `content_hash`, `file_name`):
  - `CAP = 200`, `SEARCH_THRESHOLD`, `SIMILAR_THRESHOLD`, `DOC_TYPES = ("DoC", "EC", "IFU", "ISO")`, `WEAK_BASES = ("name-family", "fetch-context", "ref-catalogue")`
  - `@dataclass Row(item_ref, name, md_flag, group_id, group_label, score, has: dict[str,bool], same_type: dict | None, refused: bool, linked: bool, proposed: str | None)`; `same_type` = `{"doc_id", "validity_from", "compare"}`, `compare` in `"older" | "newer" | "same date" | "no date"`
  - `@dataclass Group(group_id, label, rows: list[Row])`
  - `@dataclass Result(groups: list[Group], shown: int, total: int, capped: bool, mode: str, query: str = "", example: str = "", nearby: Group | None = None)`
  - `search(conn, doc, manufacturer, q) -> Result`: mode `"number"` when item numbers match (with `nearby` set), else mode `"search"` (names)
  - `NEARBY = 10`, `NAME_SCORE` (SQL fragment, parameter `%(q)s`)
  - `similar(conn, doc, manufacturer, item_ref) -> Result` (mode `"similar"`, `example` = the item ref)
  - `items_of(conn, doc, manufacturer, source_doc_id: int) -> Result` (mode `"source"`)
  - `proposed(conn, doc, manufacturer) -> Result` (mode `"proposed"`)
  - `selected(conn, doc, manufacturer, refs: list[str]) -> list[Row]`
  - `suggestions(conn, doc, manufacturer, name_of: Callable[[dict], str | None]) -> {"words": list[str], "docs": list[dict]}`; each doc dict has `doc_id`, `file_name`, `items`
  - `compare(theirs: date | None, ours: date | None) -> str`

- [ ] **Step 1: Write the failing tests** in `tests/test_item_picker.py`:

```python
"""The picker's reads (spec §4): one manufacturer's items, fuzzy and capped."""
from __future__ import annotations

from datetime import date

import pytest

from web import item_picker as ip

S = "STRAU"


def _item(conn, ref, name, raw=S, md=None):
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, catalogue, "
        "mirror_rev, updated_at) VALUES (%s,%s,%s,%s,'LJ',1,now())", (ref, name, raw, md))


def _group(conn, label, refs):
    gid = conn.execute("INSERT INTO item_group (canonical_manufacturer, label) "
                       "VALUES (%s,%s) RETURNING group_id", (S, label)).fetchone()["group_id"]
    for r in refs:
        conn.execute("INSERT INTO item_group_member (group_id, item_ref, match_basis) "
                     "VALUES (%s,%s,'name-family')", (gid, r))
    return gid


def _document(conn, key, *, doc_type="IFU", status="staged", issued=None, archive=None):
    return conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, archive_url, "
        "status, validity_from) VALUES (%s,'MDR','group',%s,%s,%s,%s) RETURNING doc_id",
        (doc_type, f"ip-{key}", archive or f"file:///{key}.pdf", status, issued),
    ).fetchone()["doc_id"]


def _link(conn, ref, doc_id, status="production", basis="ref-list"):
    conn.execute("INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
                 "VALUES (%s,%s,%s,%s::link_status)", (ref, doc_id, basis, status))


def _doc(conn, key="this", issued=date(2026, 4, 1), file_name="varibase novo.pdf"):
    doc_id = _document(conn, key, issued=issued)
    return {"doc_id": doc_id, "type": "IFU", "validity_from": issued,
            "content_hash": f"ip-{key}", "file_name": file_name}


def _refs(result):
    return [r.item_ref for g in result.groups for r in g.rows]


def test_search_lists_only_the_manufacturers_items(conn):
    _item(conn, "010.6042", "NC VARIOBASE FOR CROWN AS")
    _item(conn, "X-1", "NC VARIOBASE FOR CROWN AS", raw="OTHER")
    res = ip.search(conn, _doc(conn), S, "variobase")
    assert _refs(res) == ["010.6042"]


def test_search_finds_a_misspelling(conn):
    _item(conn, "010.6042", "NC VARIOBASE FOR CROWN AS")
    _item(conn, "011.1", "BONE LEVEL IMPLANT")
    assert _refs(ip.search(conn, _doc(conn), S, "varibase")) == ["010.6042"]


def test_an_item_number_prefix_returns_those_items(conn):
    _item(conn, "010.6042", "A")
    _item(conn, "010.6054", "B")
    _item(conn, "022.0000", "C 010.6")
    res = ip.search(conn, _doc(conn), S, "010.60")
    assert res.mode == "number"
    assert _refs(res) == ["010.6042", "010.6054"]


def test_a_number_found_inside_is_listed_after_those_that_start_with_it(conn):
    _item(conn, "6042.1", "A")
    _item(conn, "010.6042", "B")
    assert _refs(ip.search(conn, _doc(conn), S, "6042")) == ["6042.1", "010.6042"]


def test_nearby_numbers_are_the_ten_before_and_after(conn):
    for n in range(1, 31):
        _item(conn, f"100.{n:03d}", f"PART {n}")
    _item(conn, "100.0155", "OTHER MAKER", raw="OTHER")
    res = ip.search(conn, _doc(conn), S, "100.015")
    assert _refs(res) == ["100.015"]
    assert [r.item_ref for r in res.nearby.rows] == (
        [f"100.{n:03d}" for n in range(5, 15)] + [f"100.{n:03d}" for n in range(16, 26)])
    assert res.nearby.label == "Nearby item numbers"


def test_no_nearby_numbers_without_a_number_match(conn):
    _item(conn, "010.6042", "NC VARIOBASE")
    assert ip.search(conn, _doc(conn), S, "variobase").nearby is None


def test_a_number_that_matches_nothing_is_not_searched_as_a_name(conn):
    _item(conn, "010.6042", "KIT 999.9 PARTS")
    res = ip.search(conn, _doc(conn), S, "999.9")
    assert (res.mode, res.total) == ("number", 0)


def test_punctuation_in_names_is_ignored(conn):
    """Measured 2026-10-05: "emax" scored 0.40 against "E.MAX", the same as
    against "EMPRESS"; without the dot it finds every E.MAX item and no other."""
    _item(conn, "596839", "E.MAX CERAM ZIRLINER 5G 1")
    _item(conn, "554034", "EMPRESS KIVETE 100G")
    assert _refs(ip.search(conn, _doc(conn), S, "emax")) == ["596839"]


def test_search_is_capped_and_says_how_many_matched(conn):
    for n in range(ip.CAP + 5):
        _item(conn, f"V{n:04d}", f"VARIOBASE {n}")
    res = ip.search(conn, _doc(conn), S, "variobase")
    assert (res.shown, res.total, res.capped) == (ip.CAP, ip.CAP + 5, True)


@pytest.mark.parametrize("q", ["", "   ", "%", "_", "%%", "-.-"])
def test_text_without_a_word_lists_nothing(conn, q):
    """Review Focus 1: never the whole catalogue."""
    _item(conn, "010.6042", "NC VARIOBASE")
    res = ip.search(conn, _doc(conn), S, q)
    assert (res.shown, res.total) == (0, 0)


def test_a_percent_sign_is_not_a_wildcard(conn):
    _item(conn, "50%-A", "X")
    _item(conn, "50-B", "Y")
    assert _refs(ip.search(conn, _doc(conn), S, "50%")) == ["50%-A"]


def test_a_manufacturer_with_no_codes_lists_nothing(conn):
    """Review Focus 2."""
    _item(conn, "010.6042", "NC VARIOBASE")
    assert ip.search(conn, _doc(conn), "NOBODY", "variobase").total == 0


def test_an_item_in_two_groups_is_listed_once_under_the_lower_one(conn):
    _item(conn, "A1", "VARIOBASE A")
    low = _group(conn, "LOW", ["A1"])
    _group(conn, "HIGH", ["A1"])
    res = ip.search(conn, _doc(conn), S, "variobase")
    assert [(g.group_id, g.label, [r.item_ref for r in g.rows]) for g in res.groups] == [
        (low, "LOW", ["A1"])]


def test_rows_carry_device_flag_documents_and_the_same_type_comparison(conn):
    _item(conn, "A1", "VARIOBASE A", md=True)
    _item(conn, "A2", "VARIOBASE B", md=None)
    doc = _doc(conn)
    old_ifu = _document(conn, "old", status="production", issued=date(2021, 3, 2))
    new_ifu = _document(conn, "new", status="production", issued=date(2025, 7, 14))
    a_doc = _document(conn, "doc", doc_type="DoC", status="production", issued=date(2024, 1, 18))
    _link(conn, "A1", old_ifu)
    _link(conn, "A1", new_ifu)
    _link(conn, "A1", a_doc)
    rows = {r.item_ref: r for g in ip.search(conn, doc, S, "variobase").groups for r in g.rows}
    a1, a2 = rows["A1"], rows["A2"]
    assert a1.md_flag is True and a2.md_flag is None
    assert a1.has == {"DoC": True, "EC": False, "IFU": True, "ISO": False}
    assert a1.same_type == {"doc_id": new_ifu, "validity_from": date(2025, 7, 14), "compare": "older"}
    assert a2.same_type is None


@pytest.mark.parametrize("theirs, ours, word", [
    (date(2021, 1, 1), date(2026, 4, 1), "older"),
    (date(2027, 1, 1), date(2026, 4, 1), "newer"),
    (date(2026, 4, 1), date(2026, 4, 1), "same date"),
    (None, date(2026, 4, 1), "no date"),
    (date(2021, 1, 1), None, "no date"),
])
def test_compare(theirs, ours, word):
    assert ip.compare(theirs, ours) == word


def test_refused_linked_and_proposed_rows(conn):
    for ref in ("R1", "L1", "P1"):
        _item(conn, ref, f"VARIOBASE {ref}")
    doc = _doc(conn)
    _link(conn, "R1", doc["doc_id"], status="rejected", basis="name-family")
    _link(conn, "L1", doc["doc_id"], status="production", basis="ref-list")
    _link(conn, "P1", doc["doc_id"], status="staged", basis="name-family")
    rows = {r.item_ref: r for g in ip.search(conn, doc, S, "variobase").groups for r in g.rows}
    assert (rows["R1"].refused, rows["L1"].linked, rows["P1"].proposed) == (True, True, "name-family")
    assert _refs(ip.proposed(conn, doc, S)) == ["P1"]


def test_similar_excludes_the_example_and_other_manufacturers(conn):
    _item(conn, "A1", "RB/WB VARIOBASE XC FOR CROWN AS 4.5")
    _item(conn, "A2", "RB/WB VARIOBASE XC FOR CROWN 3.8")
    _item(conn, "A3", "BONE LEVEL IMPLANT")
    _item(conn, "X1", "RB/WB VARIOBASE XC FOR CROWN 3.8", raw="OTHER")
    res = ip.similar(conn, _doc(conn), S, "A1")
    assert _refs(res) == ["A2"] and res.example == "A1"


def test_items_of_a_published_document(conn):
    _item(conn, "A1", "X")
    _item(conn, "A2", "Y")
    src = _document(conn, "src", doc_type="DoC", status="production")
    _link(conn, "A1", src)
    assert _refs(ip.items_of(conn, _doc(conn), S, src)) == ["A1"]


def test_selected_returns_the_ticked_rows_in_item_order(conn):
    _item(conn, "B1", "Y")
    _item(conn, "A1", "X")
    assert [r.item_ref for r in ip.selected(conn, _doc(conn), S, ["B1", "A1"])] == ["A1", "B1"]


def test_suggestions_come_from_the_file_name_and_page_one(conn):
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('STRAUMANN')")
    conn.execute("INSERT INTO manufacturer_alias (raw_name, canonical_name, source) "
                 "VALUES (%s, 'STRAUMANN', 'vendor-master')", (S,))
    _item(conn, "010.6042", "NC VARIOBASE FOR CROWN AS")
    _item(conn, "011.1", "ABUTMENTS SET")
    doc = _doc(conn, file_name="navodila za uporabo varibase Straumann novo.pdf")
    conn.execute(
        "INSERT INTO document_text (content_hash, source, pages, chars, content) "
        "VALUES (%s, 'pdf-text', 2, 60, %s)",
        (doc["content_hash"], "Straumann Variobase Abutments 701593/M/12 [[page 2]] Implantat"))
    src = _document(conn, "decl", doc_type="DoC", status="production",
                    archive="file:///izjava o skladnosti za varibase XC novo.pdf")
    _link(conn, "010.6042", src)
    got = ip.suggestions(conn, doc, "STRAUMANN", lambda d: d["archive_url"].rsplit("/", 1)[-1])
    # "navodila", "uporabo", "straumann" are stop words or the manufacturer;
    # "implantat" is on page 2; every chip matches at least one item.
    assert got["words"] == ["varibase", "variobase", "abutments"]
    assert got["docs"] == [{"doc_id": src, "file_name": "izjava o skladnosti za varibase XC novo.pdf",
                            "items": 1}]
```

- [ ] **Step 2: Run them to see them fail**

Run: `./scripts/test.sh tests/test_item_picker.py`
Expected: FAIL, `ImportError: cannot import name 'item_picker'`.

- [ ] **Step 3: Write `web/item_picker.py`**

```python
"""The Review item picker's reads (spec 2026-10-02-review-item-picker-design.md §4).

Read-only, on the web's `dentalia_api` connection. The candidate set is one
manufacturer's items, through `manufacturers.item_codes_for`; nothing outside
it is ever returned (§4.1). Lists are capped at CAP rows and count what they
did not show, so the reviewer is told to narrow the word rather than shown a
silently clipped list.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from app import manufacturers

CAP = 200
#: Measured on the server before the picker was built (spec §4.4; the plan's
#: "Before execution" step, figures in docs/state/2026-10-05-picker-thresholds.md).
SEARCH_THRESHOLD = 0.5
SIMILAR_THRESHOLD = 0.2
#: Items listed before and after a number match, in the manufacturer's
#: item-number order (§4.3): the same family for 83% of E.MAX and 50% of
#: Variobase numbers at ±10.
NEARBY = 10
#: A name's score against the query, punctuation ignored on the name's side:
#: "E.MAX" splits on its dot, so "emax" scored 0.40 against it, the same as
#: against "EMPRESS" (measured 2026-10-05). `%(q)s` is the query.
NAME_SCORE = ("greatest(word_similarity(%(q)s, m.name), word_similarity(%(q)s, "
              "regexp_replace(m.name, '[^[:alnum:][:space:]]', '', 'g')))")
DOC_TYPES = ("DoC", "EC", "IFU", "ISO")
#: The capped bases (PRD inv. 3): a link on one of these is a proposal.
WEAK_BASES = ("name-family", "fetch-context", "ref-catalogue")
#: Words that say what kind of document it is, not what it covers (§4.2).
STOP_WORDS = frozenset({
    "izjava", "skladnosti", "navodila", "uporabo", "uporaba", "certifikat", "novo",
    "declaration", "conformity", "instructions", "instruction", "certificate",
    "with", "from", "for", "the", "and", "use", "pdf", "eu", "ec", "mdr", "mdd",
})
WORDS_TRIED = 40    # first words of the file name and page 1 looked up at all
WORDS_SHOWN = 8
DOCS_SHOWN = 5

_WORD = re.compile(r"[^\W\d_]{4,}")
_HAS_WORD = re.compile(r"[^\W_]")
_HAS_LETTER = re.compile(r"[^\W\d_]")


@dataclass
class Row:
    item_ref: str
    name: str
    md_flag: bool | None
    group_id: int | None
    group_label: str | None
    score: float
    has: dict[str, bool] = field(default_factory=dict)
    same_type: dict | None = None
    refused: bool = False
    linked: bool = False
    proposed: str | None = None


@dataclass
class Group:
    group_id: int | None
    label: str
    rows: list[Row]


@dataclass
class Result:
    groups: list[Group]
    shown: int
    total: int
    capped: bool
    mode: str
    query: str = ""
    example: str = ""
    nearby: Group | None = None


_ROWS = """
SELECT m.item_ref, m.name, m.md_flag, g.group_id, g.label AS group_label,
       {score} AS score, count(*) OVER () AS total
  FROM item_mirror m
  LEFT JOIN LATERAL (
       SELECT ig.group_id, ig.label
         FROM item_group_member gm JOIN item_group ig ON ig.group_id = gm.group_id
        WHERE gm.item_ref = m.item_ref
        ORDER BY ig.group_id LIMIT 1) g ON true
 WHERE m.manufacturer_raw = ANY(%(codes)s) AND {where}
 ORDER BY score DESC, m.item_ref
 LIMIT %(cap)s
"""


def compare(theirs: date | None, ours: date | None) -> str:
    """How an item's document of this type stands against this one (§6)."""
    if theirs is None or ours is None:
        return "no date"
    if theirs < ours:
        return "older"
    if theirs > ours:
        return "newer"
    return "same date"


def _codes(conn, manufacturer: str | None) -> list[str]:
    return manufacturers.item_codes_for(conn, manufacturer) if manufacturer else []


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _run(conn, codes, score: str, where: str, params: dict, cap: int = CAP):
    """`score` and `where` are fixed SQL fragments from this module, never input."""
    rows = conn.execute(_ROWS.format(score=score, where=where),
                        {"codes": codes, "cap": cap, **params}).fetchall()
    total = rows[0]["total"] if rows else 0
    return [Row(item_ref=r["item_ref"], name=r["name"], md_flag=r["md_flag"],
                group_id=r["group_id"], group_label=r["group_label"],
                score=float(r["score"])) for r in rows], total


def _decorate(conn, doc: dict, rows: list[Row]) -> None:
    """Per row: which document types the item already has published, its newest
    published document of this one's type, and its link to this document."""
    refs = [r.item_ref for r in rows]
    if not refs:
        return
    held = defaultdict(list)
    for h in conn.execute(
            "SELECT l.item_ref, d.type, d.doc_id, d.validity_from "
            "FROM item_document l JOIN document d ON d.doc_id = l.doc_id "
            "WHERE l.item_ref = ANY(%s) AND l.status = 'production' "
            "AND d.status = 'production' AND d.doc_id <> %s",
            (refs, doc["doc_id"])).fetchall():
        held[h["item_ref"]].append(h)
    own = {r["item_ref"]: r for r in conn.execute(
        "SELECT item_ref, status, match_basis FROM item_document "
        "WHERE doc_id = %s AND item_ref = ANY(%s)", (doc["doc_id"], refs)).fetchall()}
    for r in rows:
        mine = held[r.item_ref]
        r.has = {t: any(h["type"] == t for h in mine) for t in DOC_TYPES}
        same = [h for h in mine if h["type"] == doc["type"]]
        if same:
            newest = max(same, key=lambda h: (h["validity_from"] is not None,
                                              h["validity_from"] or date.min, h["doc_id"]))
            r.same_type = {"doc_id": newest["doc_id"], "validity_from": newest["validity_from"],
                           "compare": compare(newest["validity_from"], doc["validity_from"])}
        link = own.get(r.item_ref)
        r.refused = bool(link and link["status"] == "rejected")
        r.linked = bool(link and link["status"] == "production")
        r.proposed = (link["match_basis"] if link and link["status"] == "staged"
                      and link["match_basis"] in WEAK_BASES else None)


def _result(conn, doc, rows, total, **meta) -> Result:
    _decorate(conn, doc, rows)
    groups: dict = {}
    for r in rows:
        if r.group_id not in groups:
            label = r.group_label or (f"Group {r.group_id}" if r.group_id else "No catalogue group")
            groups[r.group_id] = Group(r.group_id, label, [])
        groups[r.group_id].rows.append(r)
    return Result(list(groups.values()), len(rows), total, total > len(rows), **meta)


def _empty(mode: str, **meta) -> Result:
    return Result([], 0, 0, False, mode, **meta)


def search(conn, doc: dict, manufacturer: str | None, q: str) -> Result:
    """One box, an item number or a name (§4.3, ruled 2026-10-05).

    Item numbers first: those containing the text, those starting with it
    first, never fuzzy, plus the nearby numbers. Only when no number matches
    and the text has a letter: names, NAME_SCORE at or above SEARCH_THRESHOLD,
    closest first."""
    q = (q or "").strip()
    codes = _codes(conn, manufacturer)
    if not codes or not _HAS_WORD.search(q):
        return _empty("search", query=q)
    esc = _like_escape(q)
    rows, total = _run(conn, codes,
                       "CASE WHEN m.item_ref ILIKE %(prefix)s THEN 2.0 ELSE 1.0 END",
                       "m.item_ref ILIKE %(inside)s",
                       {"prefix": esc + "%", "inside": "%" + esc + "%"})
    if rows:
        result = _result(conn, doc, rows, total, mode="number", query=q)
        result.nearby = _nearby(conn, doc, codes, rows, q)
        return result
    if not _HAS_LETTER.search(q):
        return _empty("number", query=q)
    rows, total = _run(conn, codes, NAME_SCORE, f"{NAME_SCORE} >= %(t)s",
                       {"q": q, "t": SEARCH_THRESHOLD})
    return _result(conn, doc, rows, total, mode="search", query=q)


def _nearby(conn, doc: dict, codes: list[str], matched: list[Row], q: str) -> Group | None:
    """NEARBY items before the lowest and after the highest anchor, in the
    database's own `item_ref` order (the order the measurement used). The
    anchors are the matches whose number starts with the text, or the single
    match when only one contains it; scattered "contains" hits get no
    neighbours, which would mean nothing around them."""
    starts = [r.item_ref for r in matched if r.item_ref.lower().startswith(q.lower())]
    anchors = starts or ([matched[0].item_ref] if len(matched) == 1 else [])
    if not anchors:
        return None
    near = [r["item_ref"] for r in conn.execute(
        "WITH a AS (SELECT min(x) AS lo, max(x) AS hi FROM unnest(%(anchors)s::text[]) AS x) "
        "(SELECT m.item_ref FROM item_mirror m, a WHERE m.manufacturer_raw = ANY(%(codes)s) "
        "   AND m.item_ref < a.lo ORDER BY m.item_ref DESC LIMIT %(n)s) "
        "UNION ALL "
        "(SELECT m.item_ref FROM item_mirror m, a WHERE m.manufacturer_raw = ANY(%(codes)s) "
        "   AND m.item_ref > a.hi ORDER BY m.item_ref LIMIT %(n)s)",
        {"anchors": anchors, "codes": codes, "n": NEARBY}).fetchall()]
    shown = {r.item_ref for r in matched}
    near = [ref for ref in near if ref not in shown]
    if not near:
        return None
    rows, _ = _run(conn, codes, "1.0", "m.item_ref = ANY(%(refs)s)", {"refs": near},
                   cap=len(near))
    _decorate(conn, doc, rows)
    return Group(None, "Nearby item numbers", rows)


def similar(conn, doc: dict, manufacturer: str | None, item_ref: str) -> Result:
    """The manufacturer's items whose names are like `item_ref`'s (§4.4)."""
    codes = _codes(conn, manufacturer)
    example = conn.execute("SELECT name FROM item_mirror WHERE item_ref = %s AND "
                           "manufacturer_raw = ANY(%s)", (item_ref, codes)).fetchone()
    if not codes or example is None:
        return _empty("similar", example=item_ref)
    rows, total = _run(conn, codes, "similarity(m.name, %(name)s)",
                       "similarity(m.name, %(name)s) >= %(t)s AND m.item_ref <> %(ref)s",
                       {"name": example["name"], "t": SIMILAR_THRESHOLD, "ref": item_ref})
    return _result(conn, doc, rows, total, mode="similar", example=item_ref)


def items_of(conn, doc: dict, manufacturer: str | None, source_doc_id: int) -> Result:
    """The manufacturer's items a published document covers (§4.2, source 2)."""
    codes = _codes(conn, manufacturer)
    if not codes:
        return _empty("source")
    rows, total = _run(conn, codes, "1.0",
                       "m.item_ref IN (SELECT l.item_ref FROM item_document l "
                       "JOIN document d ON d.doc_id = l.doc_id WHERE l.doc_id = %(src)s "
                       "AND l.status = 'production' AND d.status = 'production')",
                       {"src": source_doc_id})
    return _result(conn, doc, rows, total, mode="source")


def proposed(conn, doc: dict, manufacturer: str | None) -> Result:
    """Items linked to this document only by a weak match (§4.2), never ticked."""
    codes = _codes(conn, manufacturer)
    if not codes:
        return _empty("proposed")
    rows, total = _run(conn, codes, "1.0",
                       "m.item_ref IN (SELECT item_ref FROM item_document WHERE "
                       "doc_id = %(doc_id)s AND status = 'staged' AND match_basis = ANY(%(weak)s))",
                       {"doc_id": doc["doc_id"], "weak": list(WEAK_BASES)})
    return _result(conn, doc, rows, total, mode="proposed")


def selected(conn, doc: dict, manufacturer: str | None, refs: list[str]) -> list[Row]:
    """The ticked items, decorated, in item order: what the panel summarises."""
    refs = sorted(set(refs))
    codes = _codes(conn, manufacturer)
    if not codes or not refs:
        return []
    rows, _ = _run(conn, codes, "1.0", "m.item_ref = ANY(%(refs)s)", {"refs": refs},
                   cap=len(refs))
    _decorate(conn, doc, rows)
    return sorted(rows, key=lambda r: r.item_ref)


def _words(text: str | None) -> list[str]:
    return [w.lower() for w in _WORD.findall(text or "")]


def suggestions(conn, doc: dict, manufacturer: str | None,
                name_of: Callable[[dict], str | None]) -> dict:
    """Chips on opening (§4.2): words from the file name and page 1 that match
    at least one of the manufacturer's items, and the manufacturer's published
    documents whose file name carries one of those words."""
    codes = _codes(conn, manufacturer)
    if not codes:
        return {"words": [], "docs": []}
    stop = STOP_WORDS | set(_words(manufacturer))
    text = conn.execute("SELECT content FROM document_text WHERE content_hash = %s",
                        (doc["content_hash"],)).fetchone()
    page1 = (text["content"] if text else "").split("[[page 2]]", 1)[0]
    tried: list[str] = []
    for w in _words(doc.get("file_name")) + _words(page1):
        if w not in stop and w not in tried:
            tried.append(w)
    tried = tried[:WORDS_TRIED]
    hits = {r["q"] for r in conn.execute(
        "SELECT q FROM unnest(%(tried)s::text[]) AS q WHERE EXISTS (SELECT 1 FROM item_mirror m "
        "WHERE m.manufacturer_raw = ANY(%(codes)s) AND "
        + NAME_SCORE.replace("%(q)s", "q") + " >= %(t)s)",
        {"tried": tried, "codes": codes, "t": SEARCH_THRESHOLD}).fetchall()} if tried else set()
    words = [w for w in tried if w in hits][:WORDS_SHOWN]
    docs = []
    if words:
        for d in conn.execute(
                "SELECT d.doc_id, d.source_url, d.archive_url, d.content_hash, "
                "       count(*) AS items "
                "  FROM document d JOIN item_document l ON l.doc_id = d.doc_id "
                "   AND l.status = 'production' "
                "  JOIN item_mirror m ON m.item_ref = l.item_ref "
                "   AND m.manufacturer_raw = ANY(%s) "
                " WHERE d.status = 'production' AND d.doc_id <> %s "
                " GROUP BY d.doc_id ORDER BY d.doc_id", (codes, doc["doc_id"])).fetchall():
            name = name_of(d) or ""
            if set(_words(name)) & set(words):
                docs.append({"doc_id": d["doc_id"], "file_name": name, "items": d["items"]})
            if len(docs) == DOCS_SHOWN:
                break
    return {"words": words, "docs": docs}
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `./scripts/test.sh tests/test_item_picker.py`
Expected: all pass. If `test_search_finds_a_misspelling` or the suggestions test fails on the threshold, read the measured value from the state file (the "Before execution" step) rather than lowering it to fit the test; fix the fixture if its names are unrealistic.

- [ ] **Step 5: Commit**

```bash
git add web/item_picker.py tests/test_item_picker.py tools/picker_thresholds.sql \
  docs/state/2026-10-05-picker-thresholds.md
git commit -m "web: the item picker's reads, one manufacturer's items, fuzzy and capped"
```

---

### Task 3: Picker fragments (open, results)

**Files:**
- Modify: `web/app.py` (new module-level `_picker_doc`; new routes beside `staging_doc_detail` ~5179)
- Create: `web/templates/_item_picker.html`, `web/templates/_item_picker_results.html`, `web/templates/_item_picker_rows.html`
- Test: `tests/test_web_item_picker.py` (new)

**Interfaces:**
- Consumes: Task 2's `web.item_picker` API; `_mfr_binding_options(conn)`, `_mfr_binding_suggestion(conn, evidence, named=None)`, `_file_name(source_url, archive_url, content_hash)` in `web/app.py`.
- Produces:
  - `_picker_doc(conn, doc_id) -> dict | None`: `doc_id, type, regulation, validity_from, status, coverage_scope, canonical_manufacturer, content_hash, archive_url, source_url, file_name`; None when unknown, not `coverage_scope='group'`, or status not in (`staged`, `production`).
  - `GET /picker/{doc_id}?manufacturer=` → `_item_picker.html` (the dialog's body). `mode` is `"review"` for a staged document, `"published"` for a production one.
  - `GET /picker/{doc_id}/results?manufacturer=&q=` | `&similar=<ref>` | `&source_doc=<id>` → `_item_picker_results.html`.
  - Template context keys: `doc`, `manufacturer`, `locked`, `mode`, `result`, `proposed`, `suggestions`, `mfr_options`, `mfr_suggestion`, `results_url`, `doc_types`, `type_short`, `compare_words`.

- [ ] **Step 1: Write the failing tests** in `tests/test_web_item_picker.py`:

```python
"""The Review item picker over HTTP (spec §3, §4, §5.1)."""
from __future__ import annotations

import html
import re

import pytest
from fastapi.testclient import TestClient

from app.config import Web
from tests.conftest import TEST_API_URL
from web.app import create_app

M = "STRAUMANN"


@pytest.fixture
def client(test_db_url, tmp_path):
    return TestClient(create_app(Web(api_database_url=TEST_API_URL, imports_dir=str(tmp_path))))


def _text(markup: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", markup))).strip()


def _mfr(conn, name=M, raw=None):
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES (%s) ON CONFLICT DO NOTHING", (name,))
    conn.execute("INSERT INTO manufacturer_alias (raw_name, canonical_name, source) "
                 "VALUES (%s,%s,'vendor-master') ON CONFLICT (raw_name) DO NOTHING", (raw or name, name))


def _item(conn, ref, name, raw=M):
    conn.execute("INSERT INTO item_mirror (item_ref, name, manufacturer_raw, md_flag, catalogue, "
                 "mirror_rev, updated_at) VALUES (%s,%s,%s,NULL,'LJ',1,now())", (ref, name, raw))


def _doc(conn, key, *, status="staged", scope="group", canonical=M, issued="2026-04-01"):
    return conn.execute(
        "INSERT INTO document (type, regulation, coverage_scope, content_hash, archive_url, status, "
        "validity_from, canonical_manufacturer) VALUES ('IFU','MDR',%s,%s,%s,%s,%s,%s) "
        "RETURNING doc_id",
        (scope, f"wip-{key}", f"file:///archive/{key}__varibase.pdf", status, issued, canonical),
    ).fetchone()["doc_id"]


def _jobs(conn):
    return conn.execute("SELECT payload, dedupe_key FROM job WHERE type='gate.apply' ORDER BY id").fetchall()


def test_the_picker_opens_locked_on_the_documents_manufacturer(client, conn):
    _mfr(conn)
    _item(conn, "010.6042", "NC VARIOBASE FOR CROWN AS")
    doc_id = _doc(conn, "lock")
    conn.commit()
    text = _text(client.get(f"/picker/{doc_id}").text)
    assert f"Manufacturer: {M} locked, from the document. Only its items can be found." in text
    assert "Done" in text and "Cancel" in text


def test_the_picker_asks_for_a_manufacturer_when_the_document_has_none(client, conn):
    _mfr(conn)
    _item(conn, "010.6042", "NC VARIOBASE")
    doc_id = _doc(conn, "nomfr", canonical=None)
    conn.commit()
    body = client.get(f"/picker/{doc_id}").text
    assert "Which manufacturer made these items?" in _text(body)
    assert 'name="q"' not in body          # no search before a choice
    chosen = _text(client.get(f"/picker/{doc_id}", params={"manufacturer": M}).text)
    assert f"Manufacturer: {M} chosen by you." in chosen


def test_a_whole_range_document_has_no_picker(client, conn):
    _mfr(conn)
    doc_id = _doc(conn, "range", scope="manufacturer")
    conn.commit()
    assert client.get(f"/picker/{doc_id}").status_code == 404


def test_results_search_and_cap(client, conn):
    _mfr(conn)
    for n in range(205):
        _item(conn, f"V{n:04d}", f"VARIOBASE {n}")
    doc_id = _doc(conn, "cap")
    conn.commit()
    text = _text(client.get(f"/picker/{doc_id}/results",
                            params={"manufacturer": M, "q": "variobase"}).text)
    assert "205 items match. Showing the closest 200; use a narrower word." in text


def test_a_number_search_lists_nearby_numbers_without_tick_all(client, conn):
    _mfr(conn)
    for n in range(1, 31):
        _item(conn, f"100.{n:03d}", f"PART {n}")
    doc_id = _doc(conn, "near")
    conn.commit()
    body = client.get(f"/picker/{doc_id}/results", params={"manufacturer": M, "q": "100.015"}).text
    text = _text(body)
    assert '1 item number contains "100.015".' in text
    assert "Nearby item numbers" in text and "100.005" in text and "100.025" in text
    nearby = body[body.index("Nearby item numbers"):]
    assert "data-picker-tick-all" not in nearby


def test_a_refused_row_cannot_be_ticked(client, conn):
    _mfr(conn)
    _item(conn, "R1", "VARIOBASE R")
    doc_id = _doc(conn, "ref")
    conn.execute("INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
                 "VALUES ('R1', %s, 'name-family', 'rejected')", (doc_id,))
    conn.commit()
    body = client.get(f"/picker/{doc_id}/results", params={"manufacturer": M, "q": "variobase"}).text
    assert re.search(r'<input type="checkbox" data-pick="R1"[^>]*disabled', body)
    assert "Refused earlier" in body


def test_the_manufacturer_param_cannot_unlock_a_locked_picker(client, conn):
    _mfr(conn)
    _mfr(conn, "OTHER")
    _item(conn, "X1", "VARIOBASE X", raw="OTHER")
    doc_id = _doc(conn, "unlock")
    conn.commit()
    body = client.get(f"/picker/{doc_id}/results", params={"manufacturer": "OTHER", "q": "variobase"}).text
    assert "X1" not in body
```

- [ ] **Step 2: Run them to see them fail**

Run: `./scripts/test.sh tests/test_web_item_picker.py`
Expected: FAIL with 404 on `/picker/...`.

- [ ] **Step 3: Add `_picker_doc` near `_staged_doc_detail` in `web/app.py`**

```python
def _picker_doc(conn, doc_id: int) -> dict | None:
    """The document the item picker works on (picker spec §3): one covering
    specific items, on Review (`staged`) or published (`production`). None for
    anything else, so a whole-range document never gets a picker."""
    doc = conn.execute(
        "SELECT doc_id, type, regulation, validity_from, status, coverage_scope, "
        "canonical_manufacturer, content_hash, archive_url, source_url "
        "FROM document WHERE doc_id = %s", (doc_id,)).fetchone()
    if doc is None or doc["coverage_scope"] != "group" or doc["status"] not in ("staged", "production"):
        return None
    doc["file_name"] = _file_name(doc["source_url"], doc["archive_url"], doc["content_hash"])
    return doc


def _picker_manufacturer(conn, doc: dict, asked: str) -> tuple[str | None, bool]:
    """(manufacturer, locked). The document's confirmed one always wins and is
    locked; otherwise a chosen name counts only if it is a catalogue entity."""
    if doc["canonical_manufacturer"]:
        return doc["canonical_manufacturer"], True
    asked = (asked or "").strip()
    if asked and asked in {o["canonical_name"] for o in _mfr_binding_options(conn)}:
        return asked, False
    return None, False
```

Add module constants beside `REVIEW_ITEMS_SHOWN`:

```python
#: The picker's row vocabulary (spec §3): document type chips and how an
#: item's document of this type compares with the one being decided.
PICKER_TYPE_SHORT = {"DoC": "DoC", "EC": "CE", "IFU": "IFU", "ISO": "ISO"}
PICKER_COMPARE_WORDS = {"older": "older than this one", "newer": "newer than this one",
                        "same date": "same date", "no date": "cannot compare, no date"}
```

- [ ] **Step 4: Add the two routes** after `staging_doc_detail`:

```python
    def _picker_ctx(request, conn, doc, manufacturer, locked) -> dict:
        from urllib.parse import quote
        return {
            "request": request, "doc": doc, "manufacturer": manufacturer, "locked": locked,
            "mode": "review" if doc["status"] == "staged" else "published",
            "results_url": f"/picker/{doc['doc_id']}/results?manufacturer={quote(manufacturer or '')}",
            "doc_types": item_picker.DOC_TYPES, "type_short": PICKER_TYPE_SHORT,
            "compare_words": PICKER_COMPARE_WORDS,
        }

    @app.get("/picker/{doc_id:int}", response_class=HTMLResponse)
    def picker_open(request: Request, doc_id: int, manufacturer: str = ""):
        """The item picker's body, loaded into the panel's <dialog> (spec §3)."""
        with _conn() as conn:
            doc = _picker_doc(conn, doc_id)
            if doc is None:
                return templates.TemplateResponse(
                    request, "_result.html",
                    {"request": request, "error": "Items can be chosen only for a document "
                     "that covers specific items. Nothing was changed."}, status_code=404)
            mfr, locked = _picker_manufacturer(conn, doc, manufacturer)
            ctx = _picker_ctx(request, conn, doc, mfr, locked)
            if mfr is None:
                evidence = conn.execute(
                    "SELECT field, value FROM evidence WHERE doc_id=%s", (doc_id,)).fetchall()
                ctx.update(mfr_options=_mfr_binding_options(conn),
                           mfr_suggestion=_mfr_binding_suggestion(conn, evidence))
            else:
                sugg = item_picker.suggestions(
                    conn, doc, mfr,
                    lambda d: _file_name(d["source_url"], d["archive_url"], d["content_hash"]))
                first = sugg["words"][0] if sugg["words"] else ""
                ctx.update(suggestions=sugg, first_word=first,
                           result=item_picker.search(conn, doc, mfr, first),
                           proposed=item_picker.proposed(conn, doc, mfr))
        return templates.TemplateResponse(request, "_item_picker.html", ctx)

    @app.get("/picker/{doc_id:int}/results", response_class=HTMLResponse)
    def picker_results(request: Request, doc_id: int, manufacturer: str = "", q: str = "",
                       similar: str = "", source_doc: int | None = None):
        """One list in the picker: a search, Similar on a row, or a published
        document's items (spec §4). Never outside the manufacturer."""
        with _conn() as conn:
            doc = _picker_doc(conn, doc_id)
            if doc is None:
                return templates.TemplateResponse(
                    request, "_result.html", {"request": request, "error": "Not found."},
                    status_code=404)
            mfr, locked = _picker_manufacturer(conn, doc, manufacturer)
            if similar:
                result = item_picker.similar(conn, doc, mfr, similar)
            elif source_doc is not None:
                result = item_picker.items_of(conn, doc, mfr, source_doc)
            else:
                result = item_picker.search(conn, doc, mfr, q)
            ctx = _picker_ctx(request, conn, doc, mfr, locked)
            ctx["result"] = result
        return templates.TemplateResponse(request, "_item_picker_results.html", ctx)
```

Import at the top of `web/app.py`: `from web import item_picker` (match how `web/app.py` imports `words`).

- [ ] **Step 5: Write `web/templates/_item_picker_results.html`** (rows as in mockup 2 and 4):

```jinja
{# One answer of the item picker (spec §4): a count line, then the rows grouped
   by catalogue group. Ticks are kept by static/item_picker.js, which re-applies
   them after every swap; nothing here is ticked except an item already linked. #}
{% set type_word = type_short.get(doc.type, doc.type) %}
<p class="count-line">
{%- if result.mode == "number" and result.total == 0 %}No {{ manufacturer }} item number contains "{{ result.query }}".
{%- elif result.mode == "number" %}{{ result.total | num }} item {{ "number contains" if result.total == 1 else "numbers contain" }} "{{ result.query }}".
{%- elif result.mode == "similar" %}{{ result.total | num }} items with names like {{ result.example }}, closest first.
{%- elif result.mode == "source" %}{{ result.total | num }} items that document covers.
{%- elif result.mode == "proposed" %}{{ result.total | num }} items linked to this document only by a name match. Tick the ones it covers.
{%- elif not manufacturer %}Choose the manufacturer first.
{%- elif not result.query %}Type an item number or a word from the name.
{%- elif result.total == 0 %}No {{ manufacturer }} item matches "{{ result.query }}". Try another word or an item number.
{%- elif result.capped %}{{ result.total | num }} items match. Showing the closest {{ result.shown | num }}; use a narrower word.
{%- else %}{{ result.total | num }} items match, closest first.{% endif %}
</p>
{% for g in result.groups %}
<div class="grp" data-picker-group>
  <div class="grp-head">
    <span class="g-label">{{ g.label }}</span>
    <span class="hint">{{ g.rows|length | num }} {{ "item" if g.rows|length == 1 else "items" }}</span>
    <button type="button" class="linkbtn" data-picker-tick-all>Tick all {{ g.rows|length | num }}</button>
  </div>
  <div class="table-wrap">
  <table class="picker-table">
    <thead><tr><th></th><th>Item</th><th>Name</th><th>Medical device</th>
      <th>Documents now</th><th>Current {{ type_word }}</th><th></th></tr></thead>
    <tbody>
    {% for r in g.rows %}
    <tr class="{{ 'refused' if r.refused }}">
      <td><input type="checkbox" data-pick="{{ r.item_ref }}" data-name="{{ r.name }}"
                 aria-label="Tick {{ r.item_ref }}"
                 {{- " disabled" if r.refused or r.linked }}{{ " checked" if r.linked }}></td>
      <td class="ref"><a href="/items/{{ r.item_ref }}" target="_blank" rel="noopener">{{ r.item_ref }}</a></td>
      <td>{{ r.name }}</td>
      <td>{% if r.md_flag %}yes{% elif r.md_flag is sameas false %}no{% else %}<span class="hint">not set in BC</span>{% endif %}</td>
      <td><span class="dchips">{% for t in doc_types %}<span class="dchip{{ ' on' if r.has[t] }}">{{ type_short[t] }}</span>{% endfor %}</span></td>
      <td>{% if r.same_type %}{{ r.same_type.validity_from | day if r.same_type.validity_from else "no date" }}
          <span class="pill">{{ compare_words[r.same_type.compare] }}</span>
          {%- else %}<span class="hint">none</span>{% endif %}</td>
      <td>{% if r.refused %}<span class="pill pill-refused">Refused earlier</span>
          {%- elif r.linked %}<span class="hint">already linked</span>
          {%- else %}<button type="button" class="linkbtn"
                  hx-get="{{ results_url }}&amp;similar={{ r.item_ref | urlencode }}"
                  hx-target="#picker-results-{{ doc.doc_id }}">Similar</button>{% endif %}</td>
    </tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
</div>
{% endfor %}
{% if result.nearby %}
{# Nearby numbers (§4.3): often the same family, never certain, so no
   "tick all" -- each one is the reviewer's call. #}
<div class="grp" data-picker-group>
  <div class="grp-head">
    <span class="g-label">{{ result.nearby.label }}</span>
    <span class="hint">the {{ result.nearby.rows|length | num }} around it, often the same family. Check each one.</span>
  </div>
  {% with g = result.nearby %}{% include "_item_picker_rows.html" %}{% endwith %}
</div>
{% endif %}
```

To render the nearby rows with the same row markup, move the `<div class="table-wrap">…</div>` block above into `web/templates/_item_picker_rows.html` (it reads `g`, `doc`, `doc_types`, `type_short`, `compare_words`, `results_url`) and include it in both places: `{% include "_item_picker_rows.html" %}` inside the `for g` loop.

- [ ] **Step 6: Write `web/templates/_item_picker.html`** (mockup 2's head, tools, body, foot):

```jinja
{# The item picker's body (spec §3, mockup 2), loaded into the panel's <dialog>.
   Server-rendered; static/item_picker.js keeps the tick set and the tray. #}
<div class="m-head">
  <h3>Items this document covers</h3>
  <p class="hint">{{ doc.type | doc_type }} · {{ doc.file_name or ("document #" ~ doc.doc_id) }} ·
    Issued: {{ doc.validity_from | day if doc.validity_from else "not stated" }}</p>
  {% if manufacturer and locked %}
  <p class="lock">Manufacturer: <b>{{ manufacturer }}</b>
    <span class="hint">locked, from the document. Only its items can be found.</span></p>
  {% elif manufacturer %}
  <p class="lock">Manufacturer: <b>{{ manufacturer }}</b>
    <span class="hint">chosen by you. Only its items can be found.</span>
    <button type="button" class="linkbtn" hx-get="/picker/{{ doc.doc_id }}"
            hx-target="closest .picker-body">Change</button></p>
  {% endif %}
</div>

{% if not manufacturer %}
<div class="m-tools">
  <label for="picker-mfr-{{ doc.doc_id }}">Which manufacturer made these items?</label>
  {% if mfr_suggestion %}<p class="hint">The document names <strong>{{ mfr_suggestion.raw }}</strong>.</p>{% endif %}
  <select id="picker-mfr-{{ doc.doc_id }}" name="manufacturer"
          hx-get="/picker/{{ doc.doc_id }}" hx-target="closest .picker-body" hx-trigger="change">
    <option value="">(choose a manufacturer)</option>
    {% for opt in mfr_options %}
    <option value="{{ opt.canonical_name }}">{{ opt.canonical_name }}</option>
    {% endfor %}
  </select>
  {% if mfr_suggestion and mfr_suggestion.preselect %}
  <button type="button" class="btn-find"
          hx-get="/picker/{{ doc.doc_id }}?manufacturer={{ mfr_suggestion.preselect | urlencode }}"
          hx-target="closest .picker-body">Use {{ mfr_suggestion.preselect }}</button>
  {% endif %}
</div>
{% else %}
<div class="m-tools">
  <div class="searchrow">
    <label for="picker-q-{{ doc.doc_id }}" class="hint">Search</label>
    <input id="picker-q-{{ doc.doc_id }}" type="search" name="q" value="{{ first_word }}"
           autocomplete="off" placeholder="Item number or name"
           hx-get="{{ results_url }}" hx-trigger="input changed delay:300ms, search"
           hx-target="#picker-results-{{ doc.doc_id }}">
  </div>
  {% if suggestions.words or suggestions.docs %}
  <div class="seed">
    {% if suggestions.words %}<span>Suggested from the document:</span>
      {% for w in suggestions.words %}<button type="button" class="linkbtn" data-picker-q="{{ w }}">{{ w }}</button>{% endfor %}
    {% endif %}
    {% if suggestions.docs %}<span>Published {{ manufacturer }} documents with that word:</span>
      {% for d in suggestions.docs %}<button type="button" class="linkbtn"
          hx-get="{{ results_url }}&amp;source_doc={{ d.doc_id }}"
          hx-target="#picker-results-{{ doc.doc_id }}">{{ d.file_name }} ({{ d.items | num }} items)</button>{% endfor %}
    {% endif %}
  </div>
  {% endif %}
</div>
<div class="m-body">
  <div class="picker-results" id="picker-results-{{ doc.doc_id }}">{% include "_item_picker_results.html" %}</div>
  {% if proposed.shown %}
  <h4>Linked only by a name match</h4>
  {% with result = proposed %}{% include "_item_picker_results.html" %}{% endwith %}
  {% endif %}
</div>
{% endif %}

<div class="m-foot">
  <input type="hidden" name="manufacturer" value="{{ manufacturer or '' }}" data-picker-manufacturer>
  <div class="picker-tray" aria-live="polite"></div>
  {% if mode == "review" %}
  <div class="foot-actions">
    <p class="hint">Nothing is ticked for you. Close with Done, then approve.</p>
    <button type="button" class="btn-clear" data-picker-cancel>Cancel</button>
    <button type="button" class="btn-approve" data-picker-done>Done</button>
  </div>
  {% else %}
  <form class="foot-actions" hx-post="/documents/{{ doc.doc_id }}/items"
        hx-target="next .picker-result" hx-swap="innerHTML">
    <div class="picker-add-items" hidden></div>
    <p class="hint">Nothing is ticked for you. Each item you add becomes a confirmed link under your name.</p>
    <button type="button" class="btn-clear" data-picker-cancel>Cancel</button>
    <button type="submit" class="btn-approve" data-picker-add disabled>Add these 0 items</button>
  </form>
  <div class="picker-result"></div>
  {% endif %}
</div>
```

- [ ] **Step 6b: Run the tests to see them pass**

Run: `./scripts/test.sh tests/test_web_item_picker.py`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add web/app.py web/templates/_item_picker.html web/templates/_item_picker_results.html \
  web/templates/_item_picker_rows.html tests/test_web_item_picker.py
git commit -m "web: item picker fragments, locked to the document's manufacturer"
```

---

### Task 4: Decisions: Approve with items, the panel summary, Add items

**Files:**
- Modify: `web/app.py` (`staging_apply` ~5268; new routes `picker_summary`, `document_add_items`; `DECISION_RECEIPTS` ~494)
- Create: `web/templates/_approve_scope.html` (the panel's "Approving makes it count for" section, moved out of `_staging_doc_detail.html` lines 23-41 and 77-123)
- Modify: `web/templates/_staging_doc_detail.html`, `web/templates/_staging_decide.html:70-74`
- Test: `tests/test_web_item_picker.py`

**Interfaces:**
- Consumes: `app.item_picks.check_items` (Task 1); `item_picker.selected` (Task 2); `_picker_doc`, `_picker_manufacturer` (Task 3); `_staged_doc_detail(conn, doc_id)`.
- Produces:
  - `POST /staging/{doc_id}/apply` accepts repeated `items`; on `approve` they ride in the payload as sorted unique `items`, plus `manufacturer` when the document has none.
  - `GET /picker/{doc_id}/summary?manufacturer=&items=...` → `_approve_scope.html` with `picked`, plus an out-of-band `<button id="approve-{doc_id}">`.
  - `POST /documents/{doc_id}/items` (form `items`, `manufacturer`) → enqueues `add-items` with dedupe key `apply:{doc_id}:add-items:{sha1 of ",".join(sorted items)}[:12]`.
  - The panel's scope section has `id="approve-scope-{doc_id}"`; the Approve button has `id="approve-{doc_id}"`; the decision form holds `<div id="picker-items-{doc_id}" hidden>`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_web_item_picker.py`):

```python
def test_approve_with_items_enqueues_them(client, conn):
    _mfr(conn)
    _item(conn, "A1", "VARIOBASE A")
    _item(conn, "A2", "VARIOBASE B")
    doc_id = _doc(conn, "appr")
    conn.commit()
    r = client.post(f"/staging/{doc_id}/apply",
                    data={"decision": "approve", "items": ["A2", "A1", "A2"]})
    assert r.status_code == 200
    (job,) = _jobs(conn)
    assert job["payload"]["items"] == ["A1", "A2"]
    assert "manufacturer" not in job["payload"]           # the document's own is locked


def test_approve_with_items_sends_the_chosen_manufacturer_when_the_document_has_none(client, conn):
    _mfr(conn)
    _item(conn, "A1", "VARIOBASE A")
    doc_id = _doc(conn, "chosen", canonical=None)
    conn.commit()
    client.post(f"/staging/{doc_id}/apply",
                data={"decision": "approve", "items": ["A1"], "manufacturer": M})
    (job,) = _jobs(conn)
    assert job["payload"]["manufacturer"] == M


@pytest.mark.parametrize("case", ["other", "refused", "range", "nomfr", "conflict", "unknown"])
def test_a_bad_tick_set_is_refused_and_nothing_is_enqueued(client, conn, case):
    _mfr(conn)
    _mfr(conn, "OTHER")
    _item(conn, "A1", "VARIOBASE A")
    _item(conn, "X1", "VARIOBASE X", raw="OTHER")
    doc_id = _doc(conn, f"bad-{case}",
                  scope="manufacturer" if case == "range" else "group",
                  canonical=None if case == "nomfr" else M)
    data = {"decision": "approve", "items": ["A1"]}
    if case == "other":
        data["items"] = ["X1"]
    if case == "unknown":
        data["items"] = ["GONE"]
    if case == "refused":
        conn.execute("INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
                     "VALUES ('A1', %s, 'name-family', 'rejected')", (doc_id,))
    if case == "conflict":
        data["manufacturer"] = "OTHER"
    conn.commit()
    r = client.post(f"/staging/{doc_id}/apply", data=data)
    assert r.status_code == 422
    assert "Nothing was changed." in _text(r.text)
    assert _jobs(conn) == []


def test_reject_carries_no_items(client, conn):
    _mfr(conn)
    _item(conn, "A1", "VARIOBASE A")
    doc_id = _doc(conn, "rej")
    conn.commit()
    client.post(f"/staging/{doc_id}/apply",
                data={"decision": "reject", "reason": "Other", "items": ["A1"]})
    (job,) = _jobs(conn)
    assert "items" not in job["payload"]


def test_the_summary_follows_the_ticks_and_relabels_approve(client, conn):
    _mfr(conn)
    _item(conn, "A1", "VARIOBASE A")
    _item(conn, "A2", "VARIOBASE B")
    doc_id = _doc(conn, "sum")
    old = _doc(conn, "older-ifu", status="production", issued="2021-03-02")
    conn.execute("INSERT INTO item_document (item_ref, doc_id, match_basis, status) "
                 "VALUES ('A1', %s, 'ref-list', 'production')", (old,))
    conn.commit()
    body = client.get(f"/picker/{doc_id}/summary",
                      params={"manufacturer": M, "items": ["A1", "A2"]}).text
    text = _text(body)
    assert "Approving makes it count for these 2 items" in text
    assert "1 of them already holds an IFU (1 older); this one is added beside it." in text
    assert re.search(rf'<button[^>]*id="approve-{doc_id}"[^>]*hx-swap-oob="true"', body)
    assert "Approve for these 2 items" in text


def test_add_items_enqueues_with_a_key_per_item_set(client, conn):
    _mfr(conn)
    for ref in ("A1", "A2"):
        _item(conn, ref, f"VARIOBASE {ref}")
    doc_id = _doc(conn, "add", status="production")
    conn.commit()
    client.post(f"/documents/{doc_id}/items", data={"items": ["A1"]})
    client.post(f"/documents/{doc_id}/items", data={"items": ["A2"]})   # a second batch, still queued
    client.post(f"/documents/{doc_id}/items", data={"items": ["A1"]})   # the same batch again
    jobs = _jobs(conn)
    assert [j["payload"]["items"] for j in jobs] == [["A1"], ["A2"]]
    assert all(j["payload"]["decision"] == "add-items" for j in jobs)
    assert len({j["dedupe_key"] for j in jobs}) == 2


def test_add_items_refuses_a_document_on_review(client, conn):
    _mfr(conn)
    _item(conn, "A1", "VARIOBASE A")
    doc_id = _doc(conn, "addstaged")
    conn.commit()
    r = client.post(f"/documents/{doc_id}/items", data={"items": ["A1"]})
    assert r.status_code == 422 and _jobs(conn) == []


def test_the_review_panel_offers_the_picker_only_for_specific_items(client, conn):
    _mfr(conn)
    group_doc = _doc(conn, "panel-g")
    range_doc = _doc(conn, "panel-r", scope="manufacturer")
    conn.commit()
    g = client.get(f"/staging/{group_doc}/detail").text
    assert "Find items this document covers" in _text(g)
    assert f'id="picker-items-{group_doc}"' in g and f'id="approve-{group_doc}"' in g
    assert "Find items this document covers" not in _text(client.get(f"/staging/{range_doc}/detail").text)
```

- [ ] **Step 2: Run them to see them fail**

Run: `./scripts/test.sh tests/test_web_item_picker.py`
Expected: the new tests FAIL (items not in payload, 404 on summary and add routes, no button).

- [ ] **Step 3: `staging_apply` accepts `items`.** Add the parameter `items: list[str] = Form([]),` after `confirm`. Directly before the `# Per the proposal's suggested key` block (after the bind two-step), add:

```python
        # Picker spec §5.1: the items a reviewer ticked, on Approve only. The
        # route refuses what GATE would refuse, so a bad set never reaches the
        # queue; GATE checks again (`item_picks.check_items`) as the last word.
        if decision == "approve" and any(i.strip() for i in items):
            picked = sorted({i.strip() for i in items if i.strip()})
            with _conn() as conn:
                doc = _picker_doc(conn, doc_id)
                refusal = None
                if doc is None or doc["status"] != "staged":
                    refusal = ("Items can be chosen only for a document on Review that "
                               "covers specific items.")
                else:
                    asked = manufacturer.strip()
                    confirmed = doc["canonical_manufacturer"]
                    if confirmed and asked and asked != confirmed:
                        refusal = f"This document is {confirmed}'s, not {asked}'s."
                    else:
                        mfr, _ = _picker_manufacturer(conn, doc, asked)
                        if mfr is None:
                            refusal = "Choose the manufacturer before ticking items."
                        else:
                            problems = item_picks.check_items(conn, doc_id, mfr, picked)
                            if problems:
                                refusal = _picker_refusal(problems)
                            elif not confirmed:
                                payload["manufacturer"] = mfr
            if refusal:
                ctx["error"] = f"{refusal} Nothing was changed."
                return templates.TemplateResponse(request, "_result.html", ctx, status_code=422)
            payload["items"] = picked
```

And a module-level helper next to `_picker_doc`:

```python
def _picker_refusal(problems: dict[str, list[str]]) -> str:
    """`check_items`'s findings as one sentence per kind, naming the items."""
    said = {"unknown": "not in the catalogue",
            "other-manufacturer": "made by another manufacturer",
            "refused": "refused earlier for this document"}
    return " ".join(f"{', '.join(refs)}: {said[kind]}." for kind, refs in problems.items())
```

Import `from app import item_picks` where `web/app.py` imports `manufacturers`, and `Query` from `fastapi` if it is not imported yet (the summary route uses it).

- [ ] **Step 4: Move the scope section into `_approve_scope.html`.** Cut the `item_list` macro (lines 23-41) and the body of `<section class="approve-scope">` (lines 78-122) from `_staging_doc_detail.html` into `web/templates/_approve_scope.html`, unchanged, wrapped as:

```jinja
{# What approving makes the document count for (spec § 6), and, on a document
   covering specific items, the item picker's button (picker spec §3). Rendered
   in the open Review panel and again by `/picker/{id}/summary` after Done, with
   `picked` set to the ticked rows. #}
{% macro item_list(links, shown) %}...unchanged...{% endmacro %}
{% set offer_picker = not row.is_mfr_binding and row.coverage_scope == "group" %}
{% if picked %}
  {% set already = row.approve_links | map(attribute="item_ref") | list %}
  {% set extra = picked | rejectattr("item_ref", "in", already) | list %}
  {% set n = already|length + extra|length %}
  <p class="approve-line">Approving makes it count for
    {% if n == 1 %}this item{% else %}these {{ n | num }} items{% endif %}</p>
  {{ item_list(row.approve_links + extra, items_shown) }}
  <p class="hint">You chose {{ picked|length | num }} of them. Each becomes a confirmed link
    under your name when you approve.</p>
  {% if held_line %}<p>{{ held_line }}</p>{% endif %}
{% else %}
  ...the existing if/elif chain, unchanged...
{% endif %}
{% if offer_picker %}
<button type="button" class="btn-find" data-picker-open="picker-{{ row.doc.doc_id }}"
        data-picker-url="/picker/{{ row.doc.doc_id }}">
  {{- "Change items…" if picked else "Find items this document covers…" }}</button>
<p class="hint">Search {{ row.manufacturer_confirmed or row.doc.canonical_manufacturer or "the manufacturer's" }}
  items and tick the ones it covers. Nothing is linked until you approve.</p>
{% endif %}
```

In `_staging_doc_detail.html`, replace the removed section with:

```jinja
    <section class="approve-scope" id="approve-scope-{{ doc_id }}">{% include "_approve_scope.html" %}</section>
    {% if not row.is_mfr_binding and row.coverage_scope == "group" %}
    <dialog class="item-picker" id="picker-{{ doc_id }}" aria-label="Items this document covers"
            data-items-into="picker-items-{{ doc_id }}"
            data-summary-url="/picker/{{ doc_id }}/summary"
            data-summary-into="approve-scope-{{ doc_id }}">
      <div class="picker-body"></div>
    </dialog>
    {% endif %}
```

and, inside the decision `<form>`, after the `manufacturer` hidden input block: `<div id="picker-items-{{ doc_id }}" hidden></div>`. Pass `picked=None` (Jinja's undefined is falsy, so no route change is needed for the detail route; set `"picked": None` explicitly in `staging_doc_detail`'s context for clarity). The `item_list` macro is no longer defined in `_staging_doc_detail.html`; its other two uses (`held_links` lists) move with the section.

In `_staging_decide.html:70`, the approve button gains `id="approve-{{ doc_id }}"`.

- [ ] **Step 5: The summary route** after `picker_results`:

```python
    @app.get("/picker/{doc_id:int}/summary", response_class=HTMLResponse)
    def picker_summary(request: Request, doc_id: int, manufacturer: str = "",
                       items: list[str] = Query([])):
        """The panel's scope section after Done (picker spec §3 step 3, §6), and
        the Approve label to match, swapped out of band."""
        with _conn() as conn:
            row = _staged_doc_detail(conn, doc_id)
            doc = _picker_doc(conn, doc_id)
            if row is None or doc is None:
                return templates.TemplateResponse(
                    request, "_result.html",
                    {"request": request, "error": f"document #{doc_id} is no longer on Review"},
                    status_code=404)
            mfr, _ = _picker_manufacturer(conn, doc, manufacturer)
            picked = item_picker.selected(conn, doc, mfr, items)
        held = [r for r in picked if r.same_type]
        held_line = None
        if held:
            counts = {}
            for r in held:
                counts[r.same_type["compare"]] = counts.get(r.same_type["compare"], 0) + 1
            parts = ", ".join(f"{counts[k]} {k}" for k in ("older", "newer", "same date", "no date")
                              if k in counts)
            word = PICKER_TYPE_SHORT.get(doc["type"], doc["type"])
            verb = "holds" if len(held) == 1 else "hold"
            held_line = (f"{len(held)} of them already {verb} an {word} ({parts}); "
                         f"this one is added beside it.")
        n = len({lk["item_ref"] for lk in row["approve_links"]} | {r.item_ref for r in picked})
        return templates.TemplateResponse(
            request, "_picker_summary.html",
            {"request": request, "row": row, "picked": picked, "held_line": held_line,
             "items_shown": REVIEW_ITEMS_SHOWN, "doc_id": doc_id, "approve_count": n})
```

`web/templates/_picker_summary.html`:

```jinja
{% include "_approve_scope.html" %}
<button type="submit" name="decision" value="approve" class="btn-approve"
        id="approve-{{ doc_id }}" hx-swap-oob="true">
  {%- if approve_count == 1 %}Approve for this item
  {%- elif approve_count %}Approve for these {{ approve_count | num }} items
  {%- else %}Approve{% endif -%}
</button>
```

"an IFU" reads right for IFU and ISO; for DoC and CE write `a`: use `article = "an" if word in ("IFU", "ISO") else "a"` and put it in the sentence instead of the fixed "an".

- [ ] **Step 6: The Add items route** beside `document_detail` (~3446):

```python
    @app.post("/documents/{doc_id:int}/items", response_class=HTMLResponse)
    def document_add_items(request: Request, doc_id: int,
                           items: list[str] = Form([]), manufacturer: str = Form("")):
        """Picker spec §3.1: add ticked items to a published document."""
        ctx = {"request": request}
        picked = sorted({i.strip() for i in items if i.strip()})
        with _conn() as conn:
            doc = _picker_doc(conn, doc_id)
            refusal = None
            if doc is None or doc["status"] != "production":
                refusal = "Items can be added only to a published document that covers specific items."
            elif not picked:
                refusal = "Tick at least one item."
            else:
                confirmed = doc["canonical_manufacturer"]
                asked = manufacturer.strip()
                mfr, _ = _picker_manufacturer(conn, doc, asked)
                if confirmed and asked and asked != confirmed:
                    refusal = f"This document is {confirmed}'s, not {asked}'s."
                elif mfr is None:
                    refusal = "Choose the manufacturer before ticking items."
                elif (problems := item_picks.check_items(conn, doc_id, mfr, picked)):
                    refusal = _picker_refusal(problems)
            if refusal:
                ctx["error"] = f"{refusal} Nothing was changed."
                return templates.TemplateResponse(request, "_result.html", ctx, status_code=422)
            decided_by = _authenticated_user(request) or DEFAULT_DECIDED_BY
            payload = {"doc_id": doc_id, "decision": "add-items", "decided_by": decided_by,
                       "items": picked}
            if not doc["canonical_manufacturer"]:
                payload["manufacturer"] = mfr
            # The usual `apply:{doc}:{decision}` would drop a second batch sent
            # while the first is still queued (picker spec §5.2).
            digest = hashlib.sha1(",".join(picked).encode()).hexdigest()[:12]
            dedupe_key = f"apply:{doc_id}:add-items:{digest}"
            jid = queue.enqueue(conn, "gate.apply", payload, dedupe_key, priority="interactive")
            conn.commit()
        ctx["job_id"] = jid
        ctx["dedupe_key"] = dedupe_key
        ctx["receipt"] = words.receipt(DECISION_RECEIPTS["add-items"], jid)
        return templates.TemplateResponse(request, "_result.html", ctx)
```

Add to `DECISION_RECEIPTS`: `"add-items": "Items recorded. They show this document within a minute.",`. Import `hashlib` at the top if absent. Check `_result.html` renders `receipt` without `job_id` being set (a deduped enqueue returns None); if `words.receipt` breaks on None, follow what `staging_apply` does.

- [ ] **Step 7: Run the tests**

Run: `./scripts/test.sh tests/test_web_item_picker.py tests/test_web.py tests/test_web_review_context.py tests/test_web_review_safety.py`
Expected: all pass. The scope-section move must not change any existing Review copy; if a review-context test fails, the move changed markup and must be corrected, not the test.

- [ ] **Step 8: Commit**

```bash
git add web/app.py web/templates/_approve_scope.html web/templates/_picker_summary.html \
  web/templates/_staging_doc_detail.html web/templates/_staging_decide.html tests/test_web_item_picker.py
git commit -m "web: approve sends the ticked items; add items to a published document"
```

---

### Task 5: The page: dialog script, styles, document page, guide

**Files:**
- Create: `web/static/item_picker.js`
- Modify: `web/templates/base.html` (one `<script>`), `web/static/css/style.css`
- Modify: `web/templates/document_detail.html` (items section), `web/app.py` `document_detail` (pass `offer_picker`)
- Modify: `docs/guide/pages/review.md`, `review.sl.md`, `documents.md`, `documents.sl.md`; run `python3 scripts/build-guide.py`; `docs/dev/web.md`
- Test: `tests/test_web_item_picker.py`

**Interfaces:**
- Consumes: the dialog's data attributes from Task 4 (`data-items-into`, `data-summary-url`, `data-summary-into`), the fragments' `data-pick`, `data-name`, `data-picker-group`, `data-picker-tick-all`, `data-picker-q`, `data-picker-cancel`, `data-picker-done`, `data-picker-add`, `data-picker-manufacturer`, `.picker-tray`, `.picker-add-items`.

- [ ] **Step 1: Write the failing tests** (append):

```python
def test_the_page_loads_the_picker_script(client, conn):
    assert "item_picker.js" in client.get("/staging").text


def test_a_published_document_offers_add_items(client, conn):
    _mfr(conn)
    pub = _doc(conn, "page-pub", status="production")
    rng = _doc(conn, "page-rng", status="production", scope="manufacturer")
    conn.commit()
    page = client.get(f"/documents/{pub}").text
    assert "Add items this document covers" in _text(page)
    assert f'data-picker-url="/picker/{pub}"' in page
    assert "Add items this document covers" not in _text(client.get(f"/documents/{rng}").text)
    body = _text(client.get(f"/picker/{pub}").text)
    assert "Add these 0 items" in body and "Done" not in body
```

- [ ] **Step 2: Run them to see them fail**

Run: `./scripts/test.sh tests/test_web_item_picker.py`
Expected: the two new tests FAIL.

- [ ] **Step 3: Write `web/static/item_picker.js`**

```javascript
/* The Review item picker's page script (picker spec §3.2). It opens and closes
   the dialog, keeps the tick set, draws the tray, and on Done copies the set
   into the decision form and asks the server for the panel's summary. The lists
   are server-rendered over htmx. Delegated on document, like base.html's
   handlers, so fragments swapped in later work without re-binding. Cancel (and
   Esc) just closes: the set is rebuilt from the form's hidden inputs on every
   open, so what Done last kept is what comes back. */
(function () {
  "use strict";
  var sets = new WeakMap();

  function dialogOf(el) { return el && el.closest ? el.closest("dialog.item-picker") : null; }
  function ticks(d) { if (!sets.has(d)) { sets.set(d, new Map()); } return sets.get(d); }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) { e.className = cls; }
    if (text !== undefined) { e.textContent = text; }
    return e;
  }

  function sync(d) {
    var t = ticks(d);
    d.querySelectorAll("input[data-pick]").forEach(function (cb) {
      if (!cb.disabled) { cb.checked = t.has(cb.dataset.pick); }
      cb.closest("tr").classList.toggle("picked", cb.checked && !cb.disabled);
    });
    d.querySelectorAll("[data-picker-group]").forEach(function (g) {
      var boxes = Array.prototype.slice.call(g.querySelectorAll("input[data-pick]:not(:disabled)"));
      var btn = g.querySelector("[data-picker-tick-all]");
      if (!btn) { return; }
      btn.hidden = boxes.length === 0;
      var all = boxes.length > 0 && boxes.every(function (b) { return b.checked; });
      btn.textContent = (all ? "Untick all " : "Tick all ") + boxes.length;
    });
    tray(d);
  }

  function tray(d) {
    var t = ticks(d), box = d.querySelector(".picker-tray");
    if (!box) { return; }
    box.textContent = "";
    box.append(el("strong", "t-n", t.size === 1 ? "1 item ticked" : t.size + " items ticked"));
    if (!t.size) { box.append(el("span", "hint", "Tick items or a whole group.")); }
    t.forEach(function (name, ref) {
      var chip = el("span", "tchip", ref + " ");
      chip.title = name;
      var x = el("button", "", "×");
      x.type = "button";
      x.dataset.untick = ref;
      x.setAttribute("aria-label", "Remove " + ref);
      chip.append(x);
      box.append(chip);
    });
    if (t.size) {
      var clear = el("button", "linkbtn", "Clear all");
      clear.type = "button";
      clear.dataset.clearTicks = "";
      box.append(clear);
    }
    var add = d.querySelector("[data-picker-add]");
    if (add) {
      add.disabled = t.size === 0;
      add.textContent = t.size === 1 ? "Add this item" : "Add these " + t.size + " items";
    }
  }

  function open(btn) {
    var d = document.getElementById(btn.dataset.pickerOpen);
    if (!d) { return; }
    var t = ticks(d), into = document.getElementById(d.dataset.itemsInto || "");
    t.clear();
    if (into) {
      into.querySelectorAll("input[name=items]").forEach(function (i) { t.set(i.value, i.dataset.name || ""); });
    }
    d.showModal();
    htmx.ajax("GET", btn.dataset.pickerUrl, { target: d.querySelector(".picker-body"), swap: "innerHTML" });
  }

  function hidden(name, value, label) {
    var i = el("input");
    i.type = "hidden";
    i.name = name;
    i.value = value;
    if (label) { i.dataset.name = label; }
    return i;
  }

  function done(d) {
    var t = ticks(d), into = document.getElementById(d.dataset.itemsInto);
    var mfr = d.querySelector("[data-picker-manufacturer]");
    var params = new URLSearchParams();
    into.textContent = "";
    t.forEach(function (name, ref) { into.append(hidden("items", ref, name)); params.append("items", ref); });
    if (mfr && mfr.value) { into.append(hidden("manufacturer", mfr.value)); params.append("manufacturer", mfr.value); }
    d.close();
    htmx.ajax("GET", d.dataset.summaryUrl + "?" + params.toString(),
              { target: "#" + d.dataset.summaryInto, swap: "innerHTML" });
  }

  function fillAdd(d) {
    var box = d.querySelector(".picker-add-items"), mfr = d.querySelector("[data-picker-manufacturer]");
    box.textContent = "";
    ticks(d).forEach(function (name, ref) { box.append(hidden("items", ref)); });
    if (mfr && mfr.value) { box.append(hidden("manufacturer", mfr.value)); }
  }

  document.addEventListener("click", function (e) {
    var hit = e.target.closest("[data-picker-open]");
    if (hit) { open(hit); return; }
    var d = dialogOf(e.target);
    if (!d) { return; }
    var t = ticks(d);
    if (e.target.closest("[data-picker-cancel]")) { d.close(); return; }
    if (e.target.closest("[data-picker-done]")) { done(d); return; }
    if (e.target.closest("[data-picker-add]")) { fillAdd(d); return; }   // the form then submits
    if ((hit = e.target.closest("[data-picker-tick-all]"))) {
      var boxes = Array.prototype.slice.call(
        hit.closest("[data-picker-group]").querySelectorAll("input[data-pick]:not(:disabled)"));
      var all = boxes.every(function (b) { return t.has(b.dataset.pick); });
      boxes.forEach(function (b) { if (all) { t.delete(b.dataset.pick); } else { t.set(b.dataset.pick, b.dataset.name || ""); } });
      sync(d);
      return;
    }
    if ((hit = e.target.closest("[data-untick]"))) { t.delete(hit.dataset.untick); sync(d); return; }
    if (e.target.closest("[data-clear-ticks]")) { t.clear(); sync(d); return; }
    if ((hit = e.target.closest("[data-picker-q]"))) {
      var q = d.querySelector("input[name=q]");
      q.value = hit.dataset.pickerQ;
      htmx.trigger(q, "search");
    }
  });

  document.addEventListener("change", function (e) {
    var cb = e.target.closest ? e.target.closest("input[data-pick]") : null;
    var d = dialogOf(cb);
    if (!d) { return; }
    if (cb.checked) { ticks(d).set(cb.dataset.pick, cb.dataset.name || ""); } else { ticks(d).delete(cb.dataset.pick); }
    sync(d);
  });

  document.addEventListener("htmx:afterSwap", function (e) {
    var d = dialogOf(e.target);
    if (d) { sync(d); }
  });
})();
```

- [ ] **Step 4: Load it** in `web/templates/base.html`, after the htmx script tag: `<script src="{{ static_url('item_picker.js') }}" defer></script>`.

- [ ] **Step 5: Styles.** Append to `web/static/css/style.css`. Check the variable names against the file's own `:root` first and use those; the mockup's names were copied from it.

```css
/* Item picker (picker spec §3, mockup 2) */
dialog.item-picker { width: min(980px, calc(100vw - 32px)); max-height: calc(100vh - 48px);
  padding: 0; border: 1px solid var(--border); border-radius: 10px; }
dialog.item-picker::backdrop { background: rgba(30, 36, 34, .35); }
.item-picker .picker-body { display: grid; grid-template-rows: auto auto minmax(0, 1fr) auto;
  max-height: calc(100vh - 50px); }
.item-picker .m-head, .item-picker .m-tools, .item-picker .m-foot { padding: 10px 14px; }
.item-picker .m-head { border-bottom: 1px solid var(--border); }
.item-picker .m-tools { border-bottom: 1px solid var(--border); display: grid; gap: 8px; }
.item-picker .m-body { overflow: auto; padding: 4px 14px 10px; min-height: 0; }
.item-picker .m-foot { border-top: 1px solid var(--border); display: grid; gap: 8px; }
.item-picker .searchrow { display: flex; gap: 8px; align-items: center; }
.item-picker .searchrow input { flex: 1 1 260px; min-width: 0; }
.item-picker .seed { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; font-size: 12px; }
.item-picker .grp-head { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap;
  padding: 5px 0; border-bottom: 1px solid var(--border); }
.item-picker .grp-head [data-picker-tick-all] { margin-left: auto; }
.item-picker td.ref { font-family: ui-monospace, Menlo, Consolas, monospace; white-space: nowrap; }
.item-picker tr.picked td { background: var(--surface-2); }
.item-picker tr.refused td { color: var(--muted); }
.linkbtn { background: none; border: 0; padding: 0; color: var(--action); cursor: pointer;
  font: inherit; text-decoration: underline; text-underline-offset: 2px; }
.dchips { display: inline-flex; gap: 3px; white-space: nowrap; }
.dchip { font-size: 10.5px; font-weight: 600; border-radius: 4px; padding: 0 4px;
  border: 1px solid var(--border); color: var(--muted); }
.dchip.on { background: var(--action); color: #fff; border-color: var(--action); }
.pill { display: inline-block; padding: 1px 7px; border-radius: 999px; font-size: 11px;
  font-weight: 600; white-space: nowrap; background: var(--surface-2); }
.pill-refused { background: #fdecea; color: #b03a3a; }
.picker-tray { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; max-height: 64px; overflow: auto; }
.tchip { display: inline-flex; gap: 4px; align-items: center; font-size: 11.5px;
  border: 1px solid var(--border); border-radius: 999px; padding: 1px 4px 1px 8px; }
.tchip button { background: none; border: 0; cursor: pointer; color: var(--muted); }
.item-picker .foot-actions { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; align-items: center; }
.item-picker .foot-actions .hint { margin: 0 auto 0 0; }
.btn-find { margin-top: 8px; }
```

- [ ] **Step 6: The document page.** In `document_detail` (`web/app.py` ~3446) add to the template context `offer_picker = doc["status"] == "production" and doc["coverage_scope"] == "group"`. In `document_detail.html`, at the end of the items-covered section:

```jinja
{% if offer_picker %}
<button type="button" class="btn-find" data-picker-open="picker-{{ doc.doc_id }}"
        data-picker-url="/picker/{{ doc.doc_id }}">Add items this document covers…</button>
<p class="hint">Find this manufacturer's items and tick the ones the document covers. Each becomes a
  confirmed link under your name.</p>
<dialog class="item-picker" id="picker-{{ doc.doc_id }}" aria-label="Items this document covers">
  <div class="picker-body"></div>
</dialog>
{% endif %}
```

- [ ] **Step 7: Run the tests**

Run: `./scripts/test.sh tests/test_web_item_picker.py tests/test_web.py`
Expected: all pass.

- [ ] **Step 8: Check it in a browser.** `docker compose up -d web`, open `/staging`, expand a document covering specific items, and walk mockups 1 to 3: open the picker, search, Similar, a published-document chip, Tick all, untick from the tray, Cancel (ticks from the last Done come back), Done (panel and Approve label change), Change items…; on `/documents/{id}` of a published one, add an item. Compare against `docs/superpowers/specs/2026-10-02-review-item-picker-mockup.html`. Report what you checked; a JS path no test covers is not done until it was clicked.

- [ ] **Step 9: Guide and developer docs.**
  - `docs/guide/pages/review.md`, new section after the approve section:

```markdown
## Choosing the items a document covers

Some documents, often instructions for use, name no article numbers, so nothing
links them to an item. Open the row and press **Find items this document
covers…**.

- The manufacturer is locked to the document's. If the document has none
  confirmed, choose it first; only its items can be found.
- The search starts with a word from the file name. Type an item number or a
  word from the name. A number lists the items whose number contains it, and
  below them the 10 numbers before and after it, which are often the same
  family: check each one. A word finds close spellings too ("varibase",
  "emax" for E.MAX). At most 200 items are listed; if more match, use a
  narrower word.
- **Similar** on any row lists the manufacturer's items with names like it. A
  published document of the same manufacturer, offered under the search box,
  lists the items it covers.
- Tick items one by one, or **Tick all** in a catalogue group. Nothing is ticked
  for you. An item refused earlier for this document cannot be ticked.
- Each row shows whether Business Central marks the item as a medical device,
  which documents it already has (DoC, CE, IFU, ISO), and its newest document of
  this type with its date.
- **Done** closes the picker and the panel lists what approving will make the
  document count for. **Cancel** keeps what you had before.

Nothing is linked until you approve. Each ticked item then becomes a confirmed
link under your name. An item that already has a document of the same type
keeps it; the new one is added beside it.
```

  - `docs/guide/pages/review.sl.md`, the same section in Slovenian:

```markdown
## Izbira artiklov, ki jih dokument pokriva

Nekateri dokumenti, pogosto navodila za uporabo, ne navajajo kataloških številk,
zato jih nič ne poveže z artiklom. Odprite vrstico in pritisnite **Poišči
artikle, ki jih dokument pokriva…**.

- Proizvajalec je zaklenjen na proizvajalca dokumenta. Če ta ni potrjen, ga
  najprej izberite; najti je mogoče samo njegove artikle.
- Iskanje se začne z besedo iz imena datoteke. Vpišite številko artikla ali
  besedo iz imena. Številka prikaže artikle, katerih številka jo vsebuje, pod
  njimi pa 10 številk pred njo in 10 za njo, ki so pogosto iz iste družine:
  preverite vsako. Beseda najde tudi podobno zapisane ("varibase", "emax" za
  E.MAX). Prikaže največ 200 artiklov; če jih ustreza več, uporabite ožjo
  besedo.
- **Podobni** v katerikoli vrstici prikaže artikle istega proizvajalca s
  podobnim imenom. Objavljen dokument istega proizvajalca, ponujen pod iskalnim
  poljem, prikaže artikle, ki jih pokriva.
- Artikle označite enega za drugim ali z **Označi vse** v katalogni skupini.
  Ničesar ne označimo namesto vas. Artikla, ki je bil za ta dokument že
  zavrnjen, ni mogoče označiti.
- Vsaka vrstica pokaže, ali ga Business Central vodi kot medicinski pripomoček,
  katere dokumente že ima (DoC, CE, IFU, ISO) in njegov najnovejši dokument te
  vrste z datumom.
- **Končano** zapre izbirnik, plošča pa pokaže, za katere artikle bo dokument
  veljal po odobritvi. **Prekliči** ohrani prejšnjo izbiro.

Dokler ne odobrite, ni povezano nič. Nato vsak označeni artikel postane potrjena
povezava pod vašim imenom. Artikel, ki že ima dokument iste vrste, ga obdrži;
novi se doda zraven.
```

  The Slovenian guide names buttons as the English screen does today; check how `review.sl.md` already refers to on-screen labels (English label in bold, or the Slovenian word) and follow that convention for **Find items…**, **Similar**, **Tick all**, **Done**, **Cancel**.
  - `docs/guide/pages/documents.md` / `.sl.md`: one paragraph each on **Add items this document covers…** on a published document (same rules; **Add these N items** sends them at once).
  - Run `python3 scripts/build-guide.py`.
  - `docs/dev/web.md`: the routes `GET /picker/{id}`, `/picker/{id}/results`, `/picker/{id}/summary`, `POST /documents/{id}/items`, and `items` on `POST /staging/{id}/apply`; `web/item_picker.py` and `static/item_picker.js` in its file list.

- [ ] **Step 10: Run the docs test and commit**

Run: `./scripts/test.sh tests/test_docs_sets.py tests/test_web_item_picker.py`
Expected: pass.

```bash
git add web/static/item_picker.js web/templates/base.html web/static/css/style.css \
  web/templates/document_detail.html web/app.py tests/test_web_item_picker.py \
  docs/guide docs/dev/web.md
git commit -m "web: the item picker dialog on Review and on a published document's page"
```

(`docs/guide` includes the built bundles; check `git status` shows only files this task produced before committing.)

---

### Task 6: An issue date before every approval

Separate from the picker's mechanics and shippable on its own (ruled 2026-10-02, spec §7). It touches every approval, so most of its work is giving existing test documents a date.

**Files:**
- Modify: `app/handlers/gate.py` (`approve` and `bind-manufacturer` branches)
- Modify: `web/app.py` (`staging_apply`)
- Modify: `web/templates/_staging_doc_detail.html` (Issued fact; whole-range approval gets the Issued field only)
- Modify: `docs/guide/pages/review.md` / `.sl.md` (+ build)
- Test: `tests/test_gate_apply_items.py`, `tests/test_web_item_picker.py`; existing approve tests that seed undated documents

**Interfaces:**
- Produces: `gate._require_issue_date(conn, doc_id, decision) -> None` (raises `ValueError` when `validity_from` is null). `bind-manufacturer` accepts `edits` with the single key `validity_from`; any other key raises.

- [ ] **Step 1: Write the failing tests.** In `tests/test_gate_apply_items.py`:

```python
def _undated(conn, key, scope="group"):
    doc_id = _doc(conn, key, scope=scope)
    conn.execute("UPDATE document SET validity_from = NULL WHERE doc_id=%s", (doc_id,))
    return doc_id


def test_approve_refuses_a_document_without_an_issue_date(conn):
    doc_id = _undated(conn, "nodate")
    with pytest.raises(ValueError, match="no issue date"):
        gh.handle_gate_apply(conn, _job(doc_id))


def test_an_entered_date_lets_approve_through(conn):
    doc_id = _undated(conn, "dated")
    gh.handle_gate_apply(conn, _job(doc_id, edits={"validity_from": "2026-04-01"}))
    assert _doc_status(conn, doc_id) == "production"


def test_bind_takes_the_date_and_only_the_date(conn):
    doc_id = _undated(conn, "bind", scope="manufacturer")
    _item(conn, "A1")   # a name with BC codes; GATE refuses a bind that reaches none
    with pytest.raises(ValueError, match="only the issue date"):
        gh.handle_gate_apply(conn, _job(doc_id, decision="bind-manufacturer", manufacturer=MFR,
                                        edits={"validity_from": "2026-04-01", "type": "EC"}))
    with pytest.raises(ValueError, match="no issue date"):
        gh.handle_gate_apply(conn, _job(doc_id, decision="bind-manufacturer", manufacturer=MFR))
    gh.handle_gate_apply(conn, _job(doc_id, decision="bind-manufacturer", manufacturer=MFR,
                                    edits={"validity_from": "2026-04-01"}))
    assert _doc_status(conn, doc_id) == "production"
```

In `tests/test_web_item_picker.py`:

```python
def test_approve_is_refused_until_a_date_is_entered(client, conn):
    _mfr(conn)
    doc_id = _doc(conn, "web-nodate", issued=None)
    conn.commit()
    r = client.post(f"/staging/{doc_id}/apply", data={"decision": "approve"})
    assert r.status_code == 422 and "Nothing was changed." in _text(r.text)
    assert _jobs(conn) == []
    ok = client.post(f"/staging/{doc_id}/apply", data={
        "decision": "approve", "edit_validity_from": "2026-04-01",
        "edit_baseline": '{"validity_from": ""}'})
    assert ok.status_code == 200 and len(_jobs(conn)) == 1


def test_the_panel_says_the_date_is_needed(client, conn):
    _mfr(conn)
    doc_id = _doc(conn, "web-fact", issued=None)
    conn.commit()
    assert "Not stated. Enter it before approving." in _text(client.get(f"/staging/{doc_id}/detail").text)
```

- [ ] **Step 2: Run them to see them fail**

Run: `./scripts/test.sh tests/test_gate_apply_items.py tests/test_web_item_picker.py`
Expected: the new tests FAIL.

- [ ] **Step 3: GATE.** Add beside `_picked_items`:

```python
def _require_issue_date(conn, doc_id, decision) -> None:
    """Every approval needs an issue date (ruled 2026-10-02, picker spec §7):
    the reviewer enters one for a document that states none. A person's rule,
    so `gate.candidate` is not subject to it. Raises, never skips."""
    row = conn.execute("SELECT validity_from FROM document WHERE doc_id=%s", (doc_id,)).fetchone()
    if row is None or row["validity_from"] is None:
        raise ValueError(f"gate.apply {decision}: document {doc_id} states no issue date; "
                         f"the reviewer enters one before approving")
```

In `approve`, right after `_apply_edits(conn, doc_id, p.get("edits"))`: `_require_issue_date(conn, doc_id, decision)`. In `bind-manufacturer`, first:

```python
        edits = p.get("edits") or {}
        if set(edits) - {"validity_from"}:
            raise ValueError("gate.apply bind-manufacturer: only the issue date may be "
                             "corrected on a whole-range approval")
        _apply_edits(conn, doc_id, edits or None)
        _require_issue_date(conn, doc_id, decision)
```

- [ ] **Step 4: Route.** In `staging_apply`, after the structured edits are merged and before the bind two-step:

```python
        # Every approval needs an issue date (picker spec §7, ruled 2026-10-02).
        # A whole-range approval carries that one correction and no other.
        if decision in ("approve", "bind-manufacturer"):
            if decision == "bind-manufacturer" and "edits" in payload:
                kept = {k: v for k, v in payload["edits"].items() if k == "validity_from"}
                if kept:
                    payload["edits"] = kept
                else:
                    del payload["edits"]
            if not (payload.get("edits") or {}).get("validity_from"):
                with _conn() as conn:
                    stored = conn.execute("SELECT validity_from FROM document WHERE doc_id=%s",
                                          (doc_id,)).fetchone()
                if stored is None or stored["validity_from"] is None:
                    ctx["error"] = ('This document states no issue date. Enter it under '
                                    '"Correct a fact first" before approving. Nothing was changed.')
                    return templates.TemplateResponse(request, "_result.html", ctx, status_code=422)
```

- [ ] **Step 5: Template.** In `_staging_doc_detail.html`, the Issued fact reads `Not stated. Enter it before approving.` when `row.doc.validity_from` is null. Change `offer_corrections` so a whole-range approval also gets the corrections form, and inside it render only the Issued field when `row.is_mfr_binding` (the other four fields keep `{% if not row.is_mfr_binding %}`); its `edit_baseline` carries only `validity_from` on that path. Update the template comment that cites the "controller ruling, 2026-09-11" to say the ruling now keeps one correction, the issue date (2026-10-02).

- [ ] **Step 6: Existing tests.** Run the whole set that approves:

Run: `./scripts/test.sh tests/test_gate_apply_handler.py tests/test_gate_apply_items.py tests/test_web.py tests/test_web_review_safety.py tests/test_web_review_context.py tests/test_web_words.py tests/test_gate_candidate_handler.py tests/test_review_confirm.py tests/test_web_confirm.py`

Every failure that is an approval of a document seeded without `validity_from` is fixed by giving that seed a date (for example `_seed_staged_doc` in `tests/test_gate_apply_handler.py` gains `validity_from='2024-01-01'`). Never weaken the rule to make a test pass. Count the tests you changed and put the number in the report. Also grep `app/` for other producers of `gate.apply` approve (`grep -rn '"approve"' app/ web/`) and list them in the report.

- [ ] **Step 7: Guide.** In `review.md` / `review.sl.md`, one paragraph: a document that states no issue date cannot be approved until you enter the date under "Correct a fact first" (whole-range approvals included, where it is the only correction). Rebuild the guide.

- [ ] **Step 8: Full suite, then commit**

Run: `./scripts/test.sh`
Expected: all green; report the count.

```bash
git add app/handlers/gate.py web/app.py web/templates/_staging_doc_detail.html \
  tests/ docs/guide
git commit -m "review: every approval needs an issue date"
```

(`tests/` here means only the test files this task changed; list them explicitly rather than adding the directory.)
