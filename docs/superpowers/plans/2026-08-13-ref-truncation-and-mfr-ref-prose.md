# Two data-integrity guards: silent REF truncation, and prose in the article-number column

> The two tasks are independent — either can be done alone.

**Goal:** make a truncated REF list impossible to mistake for a complete one, and stop Slovene stock remarks being compared against document article numbers as if they were codes.

**Tech stack:** Python 3.12, psycopg 3, raw SQL, pytest against a real Postgres.

## Global constraints

- `CLAUDE.md` invariants are binding. Invariant 3 (the REF gate) is what both tasks protect.
- **Denis's hard constraint on Task 1, 2026-08-13: "make sure the logic doesnt kill what t0 fetches - i want to avoid degradation."** The new guard must only REPORT a suspected truncation. It must never discard, shorten, or reject a stored `ref_list`.
- Tests run on the host: `.venv/bin/python -m pytest`. Per-PID test database. No external API, no spend: both tasks are $0.
- Do NOT edit `web/app.py`, `docs/code-map.md`, `docs/runbook.md`, `tasks/todo.md`, `tests/test_web.py`, `web/templates/*`, `web/static/*` — a parallel session owns those.
- Commit messages: plain imperative subject, explain why, no Claude/AI/agent mentions, no emojis, no em-dashes.

---

### Task 1: a REF list that came back at exactly the cap is a truncation suspect

**Files:**
- Modify: `app/extract/tiers.py` (the merge/escalation path that stores `ref_list`)
- Modify: `docs/vocabulary.md` §5 (new flag) and §8 if you choose the anomaly route
- Test: `tests/test_tiers.py`

**Background.** The prompt cap is 150 codes, chosen on the measured corpus distribution: median 4, top ten 53/56/67/67/84/130/133/150/150/**1121**. 150 covers 124 of the 125 GC documents that yield a list. Document 235 holds 1,121 codes; no prompt cap reaches it (~14KB of JSON). If T0 ever fails on a document of that shape, the stored list is silently partial and nothing says so.

**The model's own count claim is NOT usable as the signal.** Document 258 reported "total of 44 codes listed, first 40 returned" while returning 46 against a true 53. Use the deterministic signal instead: a non-T0 `ref_list` whose length equals the cap exactly.

Denis approved BOTH guards (option C): this one, and Task 2 below.

- [ ] **Step 1: write the failing test** — a T1 `ref_list` of exactly `cap` entries raises the flag; 149 and 151 do not; a **T0** list of exactly `cap` does NOT raise it (T0 enumerates deterministically and is not subject to the prompt cap).

```python
def test_a_capped_llm_ref_list_is_flagged_as_possibly_truncated():
    fields = {"ref_list": {"value": [f"R{i}" for i in range(150)],
                           "conf": 0.9, "tier": "T1", "verbatim": "...", "page": 1}}
    assert "ref-list-possibly-truncated" in tiers.integrity_flags(fields)

def test_a_t0_list_at_the_cap_is_not_flagged():
    fields = {"ref_list": {"value": [f"R{i}" for i in range(150)],
                           "conf": 1.0, "tier": "T0", "verbatim": "...", "page": None}}
    assert "ref-list-possibly-truncated" not in tiers.integrity_flags(fields)
```

- [ ] **Step 2: run them, confirm they fail** for the right reason (no such function).
- [ ] **Step 3: implement.** Read the cap from one place — do not hardcode 150 twice; it already lives in the prompts and must not drift. If it is only in the prompt text, lift it to a module constant and reference it from both.
- [ ] **Step 4: THE DEGRADATION GUARD — write this test and make it pass.** The flag must not change what is stored:

```python
def test_the_truncation_flag_never_shortens_the_stored_list():
    """Denis, 2026-08-13: the guard reports, it does not prune."""
    long_t0 = {"ref_list": {"value": [f"R{i}" for i in range(1121)], "conf": 1.0, "tier": "T0", ...}}
    capped_t1 = {"ref_list": {"value": [f"R{i}" for i in range(150)], "conf": 0.9, "tier": "T1", ...}}
    merged = tiers.merge(long_t0, capped_t1)
    assert len(merged["ref_list"]["value"]) == 1121   # shrink guard still wins
```

  The existing shrink guard (`_shrinks` / `_ENUMERATED_FIELDS` in `app/extract/tiers.py`) already prevents a shorter LLM list overwriting a longer T0 one. This test pins that the new flag does not weaken it.
- [ ] **Step 5: mutation-check** all three tests — break the fix, confirm red, restore. Report results.
- [ ] **Step 6: classify the new flag** in `app/handlers/gate.py`. It is **informational** — a suspected truncation is context, not a defect, and `tests/test_gate_candidate_handler.py` asserts every emitted flag has a class, so an unclassified one fails the suite.
- [ ] **Step 7: document it** in `docs/vocabulary.md` §5 and run `tests/test_vocabulary_doc.py`, which guards that page in both directions.
- [ ] **Step 8: commit.**

---

### Task 2: a T0 failure on a template-matched manufacturer is an anomaly, not a shrug

**Files:**
- Modify: `app/extract/t0_templates.py` or `app/handlers/extract.py` (wherever the T0 → LLM fall-through happens)
- Modify: `app/results.py` (`ANOMALY_KINDS` is a closed vocabulary — add the kind deliberately, and keep migration 019's comment in sync)
- Test: `tests/test_t0.py` or `tests/test_extract_handler.py`

When a manufacturer has a playbook with a REF template and T0 still extracts nothing, that is a template that stopped matching — a real regression in our own parsing, currently indistinguishable from "this document has no REF list". It falls through quietly to the LLM, which is both more expensive and less complete.

- [ ] **Step 1: failing test** — a document whose manufacturer has a matching playbook template, yielding an empty `ref_list`, reports the anomaly.
- [ ] **Step 2: run it, confirm it fails.**
- [ ] **Step 3: implement.** Report through the existing `Result.anomaly` envelope (`app/results.py`), so it surfaces on `/data-quality` like every other anomaly. Do not raise, do not block extraction — an anomaly is a count, not an exception.
- [ ] **Step 4: run tests, mutation-check, report.**
- [ ] **Step 5: document the new kind** in `docs/vocabulary.md` §8 and run `tests/test_vocabulary_doc.py`.
- [ ] **Step 6: commit.**

---

### Task 3: scrub Slovene remarks out of the article-number comparand

**Files:**
- Modify: `app/adapters/source.py` or `app/handlers/ingest.py` (wherever `mfr_ref` is written to `item_mirror`)
- Test: `tests/test_ingest.py`

**Background.** ~400 populated `Dobaviteljeva št. artikla` values are stock remarks, not article numbers: `NE BO VEČ NA ZALOGI!` (×122), `OPERA!` (×48), `NI VEČ DOBAVLJIVO!` (×27), `NI DOBAVLJIVO!!!` (×26), `NE NAROČAJ!` (×15), `samo po naročilu!` (×11). They are written straight into `item_mirror.mfr_ref` and compared against document REF codes under invariant 3.

**The safe scrub is `contains '!'` → 378 values, and NOTHING CLEVERER.** This is measured, not assumed: 985 populated values contain a space and **652 of those are legitimate codes**, including Komet's ISO bur numbers (`104 H251EF 060`). A whitespace or length heuristic destroys real data.

Denis, 2026-08-13: Dentalia said they will fix it in BC but gave no date, so we scrub on our side AND keep asking. It was question 12 in `docs/2026-08-13-vprasanja-za-dentalio.md`, **dropped from that doc on 2026-08-17** by Denis: the import-side scrub shipped, so nothing waits on the client. Re-ask only if the BC cleanup matters again.

- [ ] **Step 1: failing test** — with the exact real values:

```python
@pytest.mark.parametrize("value,kept", [
    ("NE BO VEČ NA ZALOGI!", False),
    ("OPERA!", False),
    ("samo po naročilu!", False),
    ("104 H251EF 060", True),     # Komet ISO bur code: spaces are legitimate
    ("061.7310", True),
    ("645986", True),
])
def test_only_bang_values_are_scrubbed(value, kept):
    ...
```

- [ ] **Step 2: run, confirm failure.**
- [ ] **Step 3: implement.** Scrub to NULL, never to empty string — `mfr_ref` NULL means "no article number", which downstream already handles; `''` would be compared as a code.
- [ ] **Step 4: count it, never drop it silently.** CLAUDE.md: "Skipped rows, missed fields, missing `mfr_ref` → counted and reported on job results, never silent." Report through `Result.counts` and the `mfr_ref_prose` anomaly kind, which already exists in `ANOMALY_KINDS`.
- [ ] **Step 5: run tests, mutation-check, report exact counts.**
- [ ] **Step 6: commit.**

---

## Self-review before finishing

1. Is the cap defined in exactly one place, or did you leave a second copy to drift?
2. Does any test in Task 1 assert a stored list got shorter? It must not — that is the degradation Denis named.
3. Are both new vocabulary entries (flag, anomaly kind) on `docs/vocabulary.md` and passing `tests/test_vocabulary_doc.py`?
4. Run `.venv/bin/python -m pytest -q` in full and report exact counts.
