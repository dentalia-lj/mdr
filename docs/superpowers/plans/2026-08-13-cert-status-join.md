# A rejected certificate must not hand out its expiry date

**Goal:** stop a DoC inheriting its renewal date from a certificate a human rejected, and make the resulting missing date legible instead of silent.

**Why this is not just a filter:** with the filter alone, "this document has no expiry" and "this document's expiry comes from a certificate we refuse to trust" both render as `—`. A withdrawn notified-body certificate is exactly the case a reviewer must be told about. Denis, 2026-08-13: *"i think your solution is correct but think about it and all implications."*

**Tech stack:** Python 3.12, psycopg 3, raw SQL, FastAPI + Jinja + HTMX, pytest against a real Postgres.

## Global constraints

- `CLAUDE.md` invariants are binding. Invariant 1 in particular: the FastAPI process is a **job producer only** and has zero write grants on `document` / `item_document` / `evidence`. Everything here is read-side except the VALIDATE flag in Task 2.
- **`web/app.py` is contested.** A parallel session had uncommitted changes to it on 2026-08-13. Before editing, run `git status --porcelain web/app.py`; if it is dirty from another session, coordinate rather than committing over it.
- Tests run on the host: `.venv/bin/python -m pytest`. Per-PID test database, so parallel runs are safe. Never call an external API; this whole task is $0.
- Commit messages: plain imperative subject, explain why, no mention of Claude/AI/agents, no emojis, no em-dashes.

---

### Task 1: filter the four read paths

**Files:**
- Modify: `app/handlers/report.py:36` (weekly report + scheduler expiry scan)
- Modify: `web/app.py:501` (`/items` next_expiry), `:533` (`/items/{ref}`), `:638` (`/expiry`)
- Test: `tests/test_report.py`, `tests/test_web.py`

The forward path is already right: `_resolve_cited_certificate` (`app/handlers/validate.py:219`) restricts to `production` / `superseded`. Every read surface that follows `cert_doc_id` joins it unfiltered. Make the reads match the write.

Reachability is the normal path, not a corner case: an EC certificate with no REF list gets `no-item-identifier` and lands `staged` by default.

**Do not** fix this handler-side by gating back-resolution on status. That was considered and rejected: it destroys fetch-order independence, because the certificate shape that most needs back-resolution is exactly the one that lands staged.

- [ ] **Step 1: failing test** — seed a DoC citing a certificate, set the certificate `status='rejected'`, assert `/expiry` and the weekly report do not show its date.

```python
def test_a_rejected_certificate_does_not_lend_its_expiry(conn):
    cert = _seed_doc(conn, type="EC", validity_to="2027-01-01", status="rejected")
    doc = _seed_doc(conn, type="DoC", validity_to=None, cert_doc_id=cert)
    rows = report.expiring_documents(conn, days=3650)
    assert all(r["doc_id"] != doc for r in rows)
```

- [ ] **Step 2: run it, confirm it fails** — `.venv/bin/python -m pytest tests/test_report.py -k rejected_certificate -v`. Expected: FAIL, the row is present.
- [ ] **Step 3: add `AND c.status IN ('production','superseded')`** to all four joins.
- [ ] **Step 4: run the test, confirm it passes**, then run `tests/test_report.py tests/test_web.py` whole.
- [ ] **Step 5: mutation-check** — revert one of the four conditions, confirm a test goes red, restore. If no test covers a given site, that site has no guard: add one before moving on.
- [ ] **Step 6: commit.**

---

### Task 2: say why the date is missing

**Files:**
- Modify: `app/handlers/validate.py` (`_resolve_cited_certificate` and the `cert-unresolved` flag site, around line 806-815)
- Modify: `docs/vocabulary.md` §5 (the flag's meaning changes)
- Test: `tests/test_validate_handler.py`

Today `cert-unresolved` means "the cited certificate is not in the registry". Widen it to also cover "the cited certificate is here but rejected", so the UI can explain the missing date.

`cert-unresolved` is an **informational** flag (`app/handlers/gate.py` `INFORMATIONAL_FLAGS`), so this does not gate production — deliberately. The document is fine; our knowledge of its renewal date is not.

- [ ] **Step 1: failing test** — a DoC citing a rejected certificate raises `cert-unresolved`.
- [ ] **Step 2: run it, confirm it fails.**
- [ ] **Step 3: implement** — distinguish "no such certificate" from "found but not trusted" in `_resolve_cited_certificate`, and raise the flag in both cases.
- [ ] **Step 4: run tests, confirm pass.**
- [ ] **Step 5: update `docs/vocabulary.md` §5** — the `cert-unresolved` row. `tests/test_vocabulary_doc.py` guards this page in both directions; run it.
- [ ] **Step 6: mutation-check and commit.**

---

## Self-review before finishing

1. Does any read path still follow `cert_doc_id` unfiltered? `grep -rn "cert_doc_id" app/ web/` and check each hit.
2. Does `item_document_production` (the view in `docs/dentalia-schema-sketch.md`) follow the inheritance? See open followup `[task4-cert-view]` — related, and may be the fifth site.
3. Run `.venv/bin/python -m pytest -q` in full and report exact counts.
