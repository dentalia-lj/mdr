# Single-catalogue collapse — Implementation Plan

> **STATUS: DONE 2026-08-26.** Shipped to `master` and pushed. One ZG string survives in the whole tree, deliberately, as history on the column itself in `migrations/002_ingest.sql`. Three one-row `GROUP BY` panels collapsed, not the two this plan found — K1 coverage was missed because its grouping is inside an f-string helper. Knock-on: `/api/kpi`'s `coverage` and `missing_mfr_ref` are now objects, not single-element arrays.

**Goal:** Remove the second-catalogue dimension from the code paths. There is one Business Central, one article numbering — the Zagreb operation adds items, not a second integration (Denis 2026-08-19 closing PHASES.md G17/G11, restated 2026-08-26: "there is no ZG catelogue anymore, it's all lj").

**Architecture:** A deletion, not a capability. Nothing here designs a new interface. Columns stay; what goes is every parameter, picker, flag, conflict key and fixture that offers or records a choice between catalogues.

**Spec:** none — this is a removal, planned directly against a verified site list (Denis, 2026-08-26: plan only).

**Lands before:** `docs/superpowers/plans/2026-08-26-bc-vendor-import-upload.md` (Slice B), which is written against the post-collapse state.

## Global Constraints

- **`catalogue` is overloaded in this codebase and only one meaning is in scope.** In scope: the BC-instance tag (`item_mirror.catalogue`, `BcCode.catalogue`, `vendor_master.code_source`, the pickers). **Out of scope and must not be touched:** the item catalogue itself — the `ref-catalogue` match basis, `web/catalogue.py` (the manufacturer→items map), "catalogue article number" in invariant 3, and every docstring using the word in that sense. 116 of the 171 hits are this second meaning.
- Columns stay. Denis, 2026-08-26: collapse the code paths, keep the columns. No column is dropped, no NOT NULL is relaxed.
- Invariant 3's REF gate wording ("catalogue article number") is the second meaning. Leave it.
- Run tests as `python -m pytest -q -n 4 --dist loadfile`. `--dist loadfile` is not optional. Full suite once before the final commit; per-task runs otherwise.
- Commit messages: lowercase `prefix: statement`.
- Shared checkout: check `git status` before every `git add` and stage only your own files.

## Rulings (decided here, not parked)

**`scheduler.ingest_catalogue` stays.** It is already a single `"LJ"` and it becomes the one source of the constant. Its value is baked into the persisted `scheduler_run` ledger key `ingest.monthly:LJ` — **verified: that row exists today**. Renaming the key orphans it and lets the monthly tick re-fire a period already recorded as done. Not worth it for a cosmetic win.

**The `ingest.run` payload keeps `catalogue`, and the source adapter keeps its ctor parameter.** `item_mirror.catalogue` is NOT NULL and the columns stay, so a writer must supply the value. It now comes from config in every path rather than from a picker, which is the actual fix — the payload field is plumbing for a kept column, not an offered choice.

**`app/results.py`'s `anomaly(catalogue=...)` kwarg stays.** `data_anomaly.catalogue` is nullable and descriptive, and 186 of its rows are legitimately NULL (anomalies raised outside an ingest) against 19,141 LJ. Dropping the kwarg would make every new row indistinguishable from those NULLs. It offers no choice to anyone, so it is not the drift.

**Playbook files are not edited.** Denis, 2026-08-26: make the key optional and ignore it. `BcCode` loses its `catalogue` field, the parser accepts and discards the key, and all 33 authored files stay byte-identical.

**Verified safe:** collapsing the playbook conflict key from `(catalogue, code)` to `code` changes no verdict. Across 33 files / 39 `bc_codes` entries, no code is claimed by more than one file under either key.

## Site list (verified 2026-08-26)

| Task | Files | Sites |
|---|---|---|
| 1 | `app/playbooks.py`, `app/reconcile.py`, `web/registry.py` | `BcCode.catalogue` and its 4 consumers |
| 2 | `app/vendor_master.py`, `app/reconcile.py`, `app/cli.py` | `code_source` parameter threading, `--source` flags |
| 3 | `web/app.py`, `web/templates/ingest.html`, `web/templates/upload.html` | `CATALOGUES`, two pickers, two `GROUP BY` panels |
| 4 | 6 test files | every remaining `ZG` fixture string |
| 5 | `docs/`, `tasks/followups.md` | runbook, code-map, schema sketch |

---

### Task 1: The playbook catalogue tag

**Files:**
- Modify: `app/playbooks.py` (`BcCode`, the parser at ~:210, the conflict key at ~:515)
- Modify: `app/reconcile.py` (the filter at :276)
- Modify: `web/registry.py` (:530)
- Test: `tests/test_playbooks.py`, `tests/test_playbooks_sync.py`, `tests/test_reconcile.py`

**Interfaces:**
- Produces: `BcCode(code: str)` — a one-field frozen dataclass. Every consumer constructs it positionally today (`BcCode("LJ", "035")`); after this, `BcCode("035")`.

- [x] **Step 1: Write the failing test**

```python
def test_a_playbook_still_loads_when_bc_codes_carry_a_catalogue_key(tmp_path):
    """The 33 authored playbooks all carry `catalogue` and are not being
    rewritten (Denis 2026-08-26: make the key optional, ignore it). The parser
    must accept and discard it, or every delivered file fails to load."""
    _write(tmp_path, "x.json", {"manufacturer": "X AG",
                                "bc_codes": [{"catalogue": "LJ", "code": "001"}]})
    pb = playbooks.load_playbooks(tmp_path)[0]
    assert pb.bc_codes == (playbooks.BcCode("001"),)
    assert not hasattr(pb.bc_codes[0], "catalogue")


def test_a_playbook_loads_with_no_catalogue_key_at_all(tmp_path):
    """New playbooks need not carry it."""
    _write(tmp_path, "y.json", {"manufacturer": "Y AG", "bc_codes": [{"code": "002"}]})
    assert playbooks.load_playbooks(tmp_path)[0].bc_codes == (playbooks.BcCode("002"),)


def test_one_code_claimed_by_two_playbooks_is_a_conflict_regardless_of_catalogue(tmp_path):
    """Previously keyed on (catalogue, code), so two files could claim `001`
    under different catalogue tags and pass validation. There is one catalogue,
    so that is one contested code. Verified against the delivered set: no code
    is claimed twice today, so this changes no real verdict."""
    _write(tmp_path, "a.json", {"manufacturer": "A AG",
                                "bc_codes": [{"catalogue": "LJ", "code": "001"}]})
    _write(tmp_path, "b.json", {"manufacturer": "B AG",
                                "bc_codes": [{"catalogue": "ZG", "code": "001"}]})
    with pytest.raises(ValueError, match="001"):
        playbooks.validate(playbooks.load_playbooks(tmp_path))
```

- [x] **Step 2: Run them and watch them fail**

Run: `python -m pytest -q tests/test_playbooks.py -k "catalogue_key or regardless_of_catalogue"`
Expected: FAIL — `BcCode.__init__` still requires two arguments.

- [x] **Step 3: Collapse `BcCode`**

`app/playbooks.py`:
```python
@dataclass(frozen=True)
class BcCode:
    code: str
```
Parser: `codes.append(BcCode(code=str(entry["code"])))` — read `entry["code"]` only. The `catalogue` key, if present, is ignored; say so in a comment naming the 2026-08-26 ruling so nobody re-adds it.

Conflict key at ~:515 becomes `bc.code`, and the message drops the `{catalogue}/` prefix.

- [x] **Step 4: Collapse the two consumers**

`app/reconcile.py:276` — `if bc.catalogue == code_source and bc.code not in vendor` loses its first clause. (`code_source` itself goes in Task 2; leave the parameter alone for now so this task stays reviewable.)

`web/registry.py:530` — `[f"{bc.catalogue}/{bc.code}" ...]` becomes `[bc.code for bc in pb.bc_codes]`. Check the template consuming `codes` still reads correctly with bare codes.

- [x] **Step 5: Fix the callers in tests**

`tests/test_reconcile.py:249`, `tests/test_playbooks.py:431`, `tests/test_playbooks_sync.py:193,194,221,222,241` construct `BcCode`/`bc_codes` with catalogues. Update to the one-field form. **Delete the `ZG` strings while you are there** — that is the point of the exercise, not a side errand.

`tests/test_playbooks_sync.py`'s "same code in both catalogues for the SAME manufacturer" test asserts a converged case that no longer has two catalogues to converge. Read it, and either re-express it as "the same code listed twice in one playbook is one row" or delete it with a note saying why. Do not leave it asserting a scenario the schema cannot express.

- [x] **Step 6: Run**

Run: `python -m pytest -q tests/test_playbooks.py tests/test_playbooks_sync.py tests/test_reconcile.py tests/test_web.py`
Expected: PASS.

- [x] **Step 7: Verify the 33 delivered playbooks still load and validate**

Run: `python -m app.cli playbooks validate`
Expected: exit 0, no conflicts. This is the check that matters — the unit tests use fixtures, this uses the real authored files.

- [x] **Step 8: Commit**

```bash
git commit -m "playbooks: a BC code is a code, not a catalogue and a code"
```

---

### Task 2: The `code_source` parameter

**Files:**
- Modify: `app/vendor_master.py` (`_existing`, `diff`, `apply`)
- Modify: `app/reconcile.py` (`load_vendor_master`, `build`)
- Modify: `app/cli.py` (`--source` on `vendor-master` and `reconcile`)
- Test: `tests/test_vendor_master.py`, `tests/test_reconcile.py`

**Interfaces:**
- Produces: `vendor_master.diff(conn, rows) -> Diff`, `vendor_master.apply(conn, rows, *, batch, allow_renames=False) -> dict`, `reconcile.load_vendor_master(conn)`, `reconcile.build(rows, playbooks, ...)` — all without `code_source`.

`DEFAULT_CODE_SOURCE = "LJ"` stays as the module constant the writes use. `vendor_master.code_source` stays in the schema — it is half the PK and already `DEFAULT 'LJ'`.

- [x] **Step 1: Write the failing test**

```python
def test_the_mirror_is_written_under_the_one_code_source(conn):
    """The column stays (it is half the PK and already DEFAULT 'LJ'); what goes
    is the caller's ability to choose. One BC issues these codes."""
    vm.apply(conn, (vm.VendorRow("001", "IVOCLAR"),), batch="b1")
    row = conn.execute("SELECT code_source, name FROM vendor_master").fetchone()
    assert row["code_source"] == vm.DEFAULT_CODE_SOURCE == "LJ"


def test_apply_and_diff_take_no_code_source(conn):
    import inspect
    for fn in (vm.diff, vm.apply):
        assert "code_source" not in inspect.signature(fn).parameters, fn.__name__
```

- [x] **Step 2: Run and watch fail**

Run: `python -m pytest -q tests/test_vendor_master.py -k "one_code_source or take_no_code_source"`
Expected: FAIL on the signature assertion.

- [x] **Step 3: Drop the keyword**

`_existing(conn)` selects `WHERE code_source=%s` with `DEFAULT_CODE_SOURCE`. `diff(conn, rows)` and `apply(conn, rows, *, batch, allow_renames=False)` lose the kwarg; `apply`'s INSERT supplies `DEFAULT_CODE_SOURCE`. Keep every existing docstring paragraph — especially `apply`'s note on why a disappeared code keeps its row.

- [x] **Step 4: Drop it from reconcile and the CLI**

`reconcile.load_vendor_master(conn)` and `reconcile.build(...)` lose `code_source`. `app/cli.py`: remove `--source` from both `vendor-master` and `reconcile`, and the `args.source` reads at :299, :354, :404, :421, :742, :760. The error message at :302 that names `code_source=` in its text needs rewording.

- [x] **Step 5: Fix the test callers, deleting ZG as you go**

`tests/test_vendor_master.py:153,160` and `tests/test_reconcile.py:337,339,341` pass `code_source="ZG"` to assert per-source isolation. That isolation no longer exists as a choice. Re-express each test against what it actually protects, or delete it with a written reason. `tests/test_reconcile.py:243-251` — the "claim for another catalogue" test — asserts a scenario that cannot occur; delete it and say why in the commit.

- [x] **Step 6: Run**

Run: `python -m pytest -q tests/test_vendor_master.py tests/test_reconcile.py tests/test_cli.py tests/test_playbooks_sync.py`
Expected: PASS.

- [x] **Step 7: Verify the CLI still works end to end**

Run: `python -m app.cli vendor-master --file "imports/Proizvajalci.xlsx"`
Expected: the dry-run diff prints, `added 0 · renamed 0 · disappeared 0 · unchanged 390` against the live mirror, and **nothing is written** (no `--apply`).

- [x] **Step 8: Commit**

```bash
git commit -m "vendor_master: one BC issues these codes, so stop asking which"
```

---

### Task 3: The web pickers and the one-row groupings

**Files:**
- Modify: `web/app.py` (`CATALOGUES` at ~:130, `/ingest` ctx and validation, `/upload` ctx and validation, the two `GROUP BY catalogue` panels at ~:1411 and ~:1514)
- Modify: `web/templates/ingest.html` (~:24), `web/templates/upload.html` (~:8)
- Test: `tests/test_web.py`

- [x] **Step 1: Write the failing test**

```python
def test_no_form_renders_a_catalogue_select(client):
    """One Business Central. `/import` never offered a picker; `/ingest` and
    `/upload` kept one until this change. The catalogue now comes from config
    in all three, which is the whole difference."""
    for path in ("/ingest", "/upload", "/import"):
        assert 'name="catalogue"' not in client.get(path).text, path


def test_ingest_still_enqueues_with_the_configured_catalogue(client, conn):
    """The payload field stays -- item_mirror.catalogue is NOT NULL and the
    columns are being kept. What goes is the operator choosing it."""
    resp = client.post("/ingest", data={"source": "csv", "priority": "interactive",
                                        "csv_path_manual": "/imports/Artikli.csv"})
    assert resp.status_code == 200
    payload = conn.execute(
        "SELECT payload FROM job WHERE type='ingest.run'").fetchone()["payload"]
    assert payload["catalogue"] == "LJ"


def test_the_status_board_reports_one_mirror_total_not_a_one_row_grouping(client, conn):
    from web.app import CATALOGUES  # noqa: F401  -- must be gone
```

The last one is a placeholder: replace the import with whatever assertion fits the panel you actually build, and delete `CATALOGUES` outright rather than reducing it further.

- [x] **Step 2: Run and watch fail**

Run: `python -m pytest -q tests/test_web.py -k "catalogue_select or configured_catalogue"`
Expected: FAIL — both forms still render the select.

- [x] **Step 3: Delete `CATALOGUES` and both pickers**

Remove the constant. `/ingest` and `/upload` stop taking `catalogue` as a `Form(...)` field and stop validating it; both read `scheduler_cfg.ingest_catalogue` the way `/import` already does. Drop the `<select name="catalogue">` blocks from both templates.

Any 422 path that reported `unknown catalogue` goes with it — check no test asserts that message except the one Task 4 removes.

- [x] **Step 4: Collapse the two status panels**

`web/app.py:1411` and `:1514` both `GROUP BY catalogue ORDER BY catalogue` over a single-valued column, producing a one-row breakdown. Replace with a plain total. Read each call site first — one feeds the status board, one feeds a KPI block, and they may render through different templates.

- [x] **Step 5: Run**

Run: `python -m pytest -q tests/test_web.py`
Expected: PASS, all of it.

- [x] **Step 6: Drive it in a browser**

Not optional. Start the app against a throwaway database, open `/ingest`, `/upload`, `/import` and the status board, and confirm each renders without a picker and without an empty breakdown table. Slice A shipped a page whose button was inert and every test passed.

- [x] **Step 7: Commit**

```bash
git commit -m "web: the catalogue is not a question, so stop asking it"
```

---

### Task 4: The last ZG strings

**Files:**
- Modify: `tests/test_web.py`, `tests/fixtures/seed_ui.py`, and whatever Tasks 1–2 left
- Modify: `web/app.py`, `migrations/002_ingest.sql` (comments that name ZG to explain its removal)

By now Tasks 1–3 should have removed most of them. This task is the sweep that proves it.

- [x] **Step 1: Find what is left**

Run:
```bash
grep -rn --include='*.py' --include='*.sql' --include='*.json' --include='*.html' -E '\bZG\b' app/ web/ tests/ migrations/ playbooks/ tools/
```

- [x] **Step 2: Judge each remaining hit, do not blanket-replace**

Three legitimate kinds may remain, and only these:
- `tests/test_web.py`'s ZG-refusal tests, if `CATALOGUES` still existed when they were written — Task 3 deletes the constant, so **these tests go too**; the thing they guarded no longer exists
- comments that name ZG to record that it was removed — keep at most one, in `migrations/002_ingest.sql`, and make it read as history
- `tests/test_web.py:599`'s `{"catalogue": "XX"}  # not in {LJ, ZG}` — the comment is stale even if the case survives

Everything else goes. A fixture spelling a retired value re-teaches it to every reader.

- [x] **Step 3: Re-run the sweep and confirm the count**

Expected: a small single-digit number, every one of them deliberate, and you can say what each is for.

- [x] **Step 4: Full suite**

Run: `python -m pytest -q -n 4 --dist loadfile`
Expected: 0 failures. Report the actual numbers.

- [x] **Step 5: Commit**

```bash
git commit -m "tests: stop teaching a catalogue that does not exist"
```

---

### Task 5: Documentation

**Files:**
- Modify: `docs/runbook.md`, `docs/code-map.md`, `docs/dentalia-schema-sketch.md`, `docs/dentalia-pipeline-contract-prd-v3.md`, `CLAUDE.md`, `tasks/followups.md`

- [x] **Step 1: Correct the claims this change falsified**

`docs/runbook.md:148` says "the LJ/ZG adapters" — there is one. The `/ingest` and `/upload` route rows describe a catalogue choice that is gone. `docs/code-map.md` describes `CATALOGUES`. `docs/specs/scheduler.md:19` says "Zagreb is S2.5".

Targeted corrections only. Do not rewrite adjacent sections.

- [x] **Step 2: Record the decision where the next reader will look**

`CLAUDE.md`'s opening paragraph already states there is no Zagreb system. Add one clause naming what the code now does about it, so the next session does not have to rediscover the collapse from git log.

- [x] **Step 3: Note the residue**

Append to `tasks/followups.md`: the five columns are kept and single-valued (`item_mirror.catalogue`, `upload_inbox.catalogue`, `data_anomaly.catalogue`, `import_inbox.catalogue`, `vendor_master.code_source`), the `scheduler.ingest_catalogue` config key stays because `scheduler_run` holds a live `ingest.monthly:LJ` row, and dropping any of it is a separate decision nobody needs today.

- [x] **Step 4: Full suite, then commit**

```bash
python -m pytest -q -n 4 --dist loadfile
git commit -m "docs: one catalogue, and what is deliberately left behind"
```
