# Extraction correctness before the paid backfill — implementation plan

**Goal:** Land every defect that changes what EXTRACT writes, before spending ~$56 backfilling the remaining 1.158 corpus PDFs — because re-extraction is paid and re-validation is free.

**Architecture:** Five independent fixes in the T0/tier layer plus one storage fix, each with its own failing test and mutation check. Nothing here changes the queue topology, the job-type enum, or the registry write path. Task 6 is a free measurement that decides a matching-key question and gates nothing.

**Tech Stack:** Python 3.12, pdfplumber/PyMuPDF, psycopg 3, pytest against a real Postgres, playbook JSON.

## Global Constraints

- **No new job types.** The enum is closed (invariant 7); a new tag is a PRD change + migration first.
- **Only `gate.candidate`/`gate.apply` write `document`/`item_document`/`evidence`** (invariant 1). Nothing in this plan writes them.
- **TDD, no exceptions.** Failing test first, watched fail, then minimal code. Mutation-test every guard: break it, confirm red, restore.
- **Baseline suite: 994 passed, 1 skipped** (`.venv/bin/python -m pytest -q`). Every task ends green with a higher count.
- **Do not start a worker** without checking the queue for paid tags first (`extract.doc`, `discover.group`). `queue.claim` has a `types` filter `runner.py:48` does not pass.
- **Rebuild images before running anything in Docker** — `docker compose up` reuses a stale image silently (`[stale-image-trap]`, cost us a full run on 2026-08-12).
- Verbatim REF values are never normalised at extraction time (G5). Guards reject; they do not rewrite.
- No em-dashes or emoji in code comments or commit messages; no Claude/AI attribution in commits.

## Why this order

Extraction output is what money buys. Matching, linking and validation all re-run for free over persisted extractions (proven 2026-08-12: `repair-ref-list` + re-emitted `validate.doc` took links 5 → 10 at zero cost). So **every defect that changes `extraction_attempt.fields` or `archive_url` must land before the backfill**; anything that only changes matching can follow it.

Tasks 1–5 gate the spend. Task 6 does not.

## File structure

| File | Responsibility | Task |
|---|---|---|
| `playbooks/gc.json` | GC identity + T0 parse strategy | 1 |
| `app/extract/t0_templates.py` | `_looks_like_ref` (single choke point for all three REF strategies), `derive_coverage_scope` | 2, 4 |
| `app/extract/llm.py` | `_NULLISH` sentinel coercion | 3 |
| `app/extract/prompts/t1_text.md`, `t2_vision.md` | tier prompts | 3 |
| `app/handlers/backfill.py` | corpus walker, `archive_url` | 5 |
| `tests/test_t0.py`, `test_t0_layout.py`, `test_tiers.py`, `test_llm.py`, `test_backfill_handler.py` | tests | all |
| `tools/` (new script) | one-off measurement, read-only | 6 |

---

### Task 1: GC REF lists are truncated at the first table page

**Measured:** `Unifast III_23042025.pdf` extracts 15 codes; its own text contains 55. The captured run `003464`–`003478` is contiguous — that is page 5's table only. `003479`–`003491` and a `002650`–`002686` block sit on later pages. Across the corpus, **42 of 53** documents with a REF list miss codes present in their text. Cost in links today is small (1: `003489`, which should make document #41 cover 2 items not 1) but the stored REF list is wrong, and it is the field the whole matching design rests on.

**Root cause:** `playbooks/gc.json` declares `"ref_strategy": "table"`, which routes to `_ref_from_tables` — per page, and a page whose table has no recognisable header is skipped (`_ref_column` returns `None`). GC's continuation pages repeat no header. `ref_from_stitched_tables` (`t0_layout.py:146`) exists for exactly this and already serves IVOCLAR.

**Verified before planning:** `ref_from_stitched_tables` on Unifast III returns **56** codes vs the table strategy's 15.

**Files:**
- Modify: `playbooks/gc.json` (`ref_strategy`)
- Test: `tests/test_t0_layout.py`

**Interfaces:**
- Consumes: `t0_layout.ref_from_stitched_tables(path, *, density_threshold=0.5) -> tuple[list[str], int | None]` (existing, unchanged)
- Produces: nothing new. GC documents route through the stitched strategy.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_t0_layout.py
def test_gc_multipage_article_table_is_stitched(fixture_pdf):
    """GC continuation pages repeat no header, so the per-page table strategy
    stops at the first page and silently drops the rest of the article list."""
    from app.extract import t0_layout
    from app.extract.t0_templates import extract_ref_list

    path = fixture_pdf("GC/Unifast_III_23042025.pdf")
    template = t0_layout.match("gc europe")
    assert template is not None and template.ref_strategy == "stitched-table"

    ev = extract_ref_list(path, template=template)
    codes = ev["value"]
    assert "003477" in codes            # page 5, already captured today
    assert "003489" in codes            # page 6 continuation, lost today
    assert len(codes) >= 50             # 55 six-digit codes live in this file
```

- [ ] **Step 2: Commit the fixture PDF**

The file is not in `tests/fixtures/corpus/GC/` yet. Copy it and register it, so the test does not depend on the gitignored corpus:

```bash
cp "imports/dentalia-sftp/GC/DOC/GC Europe_IIa/Unifast III_23042025.pdf" \
   tests/fixtures/corpus/GC/Unifast_III_23042025.pdf
```

Add to `tests/fixtures/corpus_manifest.py`, matching the shape of the existing GC entries:

```python
    {
        "manu": "GC",
        "name": "Unifast_III_23042025.pdf",
        "doc_class": "compliance-doc",
        "is_scan": False,
        "t0": {"type": "DoC", "regulation": "MDR", "coverage_scope": "group",
               "basic_udi_di": "++J022MD0102JT"},
        "t0_ref_min": 50,   # 55 six-digit codes across pages 5-6
        "ground_truth": {
            "type": "DoC", "regulation": "MDR",
            "validity_from": None, "validity_to": None,
            "coverage_scope": "group", "ref_list": "55 codes (pages 5-6, embedded)",
            "basic_udi_di": "++J022MD0102JT", "cert_number": "MDR 778483",
        },
        "notes": "Multi-page article table: the continuation page repeats no "
                 "header, so the per-page 'table' strategy captured only page 5's "
                 "15 codes. Exercises the stitched-table strategy for GC.",
    },
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_t0_layout.py::test_gc_multipage_article_table_is_stitched -v`
Expected: FAIL — `assert template.ref_strategy == "stitched-table"` fails with `'table' != 'stitched-table'`.

- [ ] **Step 4: Change the strategy**

`playbooks/gc.json`, one key:

```json
  "ref_strategy": "stitched-table"
```

- [ ] **Step 5: Run the test and the whole T0 suite**

Run: `.venv/bin/python -m pytest tests/test_t0.py tests/test_t0_layout.py tests/test_t0_corpus.py -q`
Expected: PASS, and the two existing GC fixtures (`everX_Posterior`, `New_Metal_Strips`) still meet their `t0_ref_min`. If either regresses, the stitched strategy's density gate is rejecting a page it should keep — do not weaken the assertion, investigate the gate.

- [ ] **Step 6: Validate the playbook**

Run: `.venv/bin/python -m app.cli playbooks validate`
Expected: exit 0. This is the operator gate — a `ref_strategy` value that fails to load as a T0 template exits 2 and names the file.

- [ ] **Step 7: Commit**

```bash
git add playbooks/gc.json tests/test_t0_layout.py tests/fixtures/corpus_manifest.py \
        tests/fixtures/corpus/GC/Unifast_III_23042025.pdf
git commit -m "extract: stitch GC's multi-page article table instead of stopping at page 5"
```

---

### Task 2: `_looks_like_ref` accepts Basic UDI-DIs and prose

**Measured:** `_looks_like_ref` accepts any value under 30 chars containing a digit. Today it lets through `'CE 01488 issued by BSI (2797)'` (a certificate number read out of a table on the GC confirmation letter) and, once Task 1 lands, `'++J022MD0102JT'` — the Basic UDI-DI, which `extract_basic_udi` already captures as its own field. Neither can match a catalogue value, so neither creates a bad link, but both are wrong stored data and both now survive: the 2026-08-12 merge fix means the LLM's empty answer no longer erases them.

**The constraint that makes this delicate:** `'104 H251EF 060'` is a real KOMET ISO bur code. Rejecting whitespace would destroy 20 legitimate REFs. The guard must reject prose without rejecting spaced codes.

**Files:**
- Modify: `app/extract/t0_templates.py:259`
- Test: `tests/test_t0.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `_looks_like_ref(val: str) -> bool` — same signature, stricter. It is the single choke point for all three REF strategies (`t0_templates._ref_from_tables`, `t0_layout.ref_from_stitched_tables:174`, `t0_layout.ref_from_text_columns:191`), so one change covers every manufacturer.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_t0.py
import pytest
from app.extract import t0_templates as t0


@pytest.mark.parametrize("value", [
    "003477",           # GC article code
    "104 H251EF 060",   # KOMET ISO bur code - spaces are LOAD BEARING
    "061.7310",         # STRAUMANN
    "9553.204.060",     # KOMET
    "645986WW",         # IVOCLAR with market suffix
    "1055",
])
def test_looks_like_ref_accepts_real_codes(value):
    assert t0._looks_like_ref(value) is True


@pytest.mark.parametrize("value", [
    "++J022MD0102JT",                   # Basic UDI-DI - extract_basic_udi owns this
    "CE 01488 issued by BSI (2797)",    # prose read out of a table cell
    "According to the Attachment",      # no digit today, but be explicit
    "Gemaess Anhang 1",
])
def test_looks_like_ref_rejects_udi_and_prose(value):
    assert t0._looks_like_ref(value) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_t0.py -k looks_like_ref -v`
Expected: the two rejection cases with digits FAIL (`++J022MD0102JT` and `CE 01488 ...` currently return `True`). The acceptance cases pass already — that is the point, they are the regression net.

- [ ] **Step 3: Implement the minimal guard**

Replace `app/extract/t0_templates.py:259-260`:

```python
#: A Basic UDI-DI as printed (GS1 "++" prefix). It is a real identifier, but it
#: is `extract_basic_udi`'s field, not a REF code, and it appears in the same
#: tables. Letting it into ref_list means matching a UDI against mfr_ref values.
_UDI_PREFIX = "++"

#: Two or more consecutive lowercase letters: a prose word. Deliberately NOT a
#: whitespace test - KOMET's ISO bur codes ("104 H251EF 060") contain spaces and
#: are legitimate REFs, so rejecting whitespace would drop 20 real codes.
_PROSE_WORD = re.compile(r"[a-z]{2,}")


def _looks_like_ref(val: str) -> bool:
    if not re.search(r"\d", val) or len(val) > 30 or "\n" in val:
        return False
    if val.startswith(_UDI_PREFIX):
        return False
    return not _PROSE_WORD.search(val)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_t0.py tests/test_t0_layout.py tests/test_t0_corpus.py -q`
Expected: PASS. `test_t0_corpus.py` asserts `t0_ref_min` for 7 fixtures across KOMET/IVOCLAR/VOCO/GC — if any drops, a real code is being rejected and the regex is too broad.

- [ ] **Step 5: Mutation-test the guard**

Break each clause, confirm its own test goes red, restore:

```bash
cp app/extract/t0_templates.py /tmp/t0.orig.py
# M1: drop the UDI check   -> test_looks_like_ref_rejects_udi_and_prose[++J022MD0102JT] red
# M2: drop the prose check -> the 'CE 01488 ...' case red
# M3: reject whitespace instead of prose -> test_looks_like_ref_accepts_real_codes[104 H251EF 060] red
cp /tmp/t0.orig.py app/extract/t0_templates.py
```

Record the three results in the commit message. M3 is the important one: it proves the test encodes the KOMET-safety requirement rather than merely asserting it in a comment.

- [ ] **Step 6: Commit**

```bash
git add app/extract/t0_templates.py tests/test_t0.py
git commit -m "extract: keep Basic UDI-DIs and prose out of ref_list"
```

---

### Task 3: an ISO certificate is rejected for having no regulation

**Measured:** 3 of 61 corpus documents. An ISO/QMS certificate has no MDR/MDD regulation. `prompts/t1_text.md:28` correctly tells the model to emit null; `llm.py:68` `_NULLISH` then coerces the string `"n.a."` to `None`; `validate.py:151` `REQUIRED_FIELDS = ("type", "regulation", "coverage_scope")` finds it missing and returns `incomplete-evidence` at `:504` — killing the document twelve lines before the C4 manufacturer-binding rule written for exactly this document type.

**Denis's ruling (2026-08-11):** emit `regulation='n.a.'` from extraction rather than making the field conditional. `migrations/005_registry.sql:23` already reserves the value (`regulation text NOT NULL, -- MDR|MDD|n.a.`).

**Why `_NULLISH` cannot simply lose `"n.a."`:** the comment at `llm.py:65-67` records that in S0.4, regulation `'n.a.'` was **6 of 20 false positives** — the model said "n.a." when the document did say MDR. So the coercion was earning its keep. The fix must make `'n.a.'` meaningful for `regulation` **and** tighten the prompts so it is only emitted for a genuinely regulation-free document.

**Files:**
- Modify: `app/extract/llm.py:65-74`
- Modify: `app/extract/prompts/t1_text.md`, `app/extract/prompts/t2_vision.md`
- Test: `tests/test_llm.py`, `tests/test_validate_handler.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `_norm_value(v, field: str | None = None)` — the field name is now passed so the coercion can be field-scoped. Callers in `_to_ev` must pass it.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_llm.py
from app.extract import llm


def test_na_is_preserved_for_regulation():
    """An ISO/QMS certificate has no MDR/MDD regulation. 'n.a.' is a real value
    (migrations/005_registry.sql reserves MDR|MDD|n.a.), not an absent one."""
    ev = llm._to_ev({"value": "n.a.", "confidence": 0.95, "verbatim": "ISO 13485"},
                    "T1", "stub-model")
    assert ev["value"] == "n.a."


def test_na_is_still_coerced_to_null_for_other_fields():
    """S0.4 measured 'n.a.' as a model tic. It stays a null sentinel everywhere
    except regulation, where the schema gives it meaning."""
    for field in ("cert_number", "type", "coverage_scope"):
        ev = llm._to_ev({"value": "n.a.", "confidence": 0.9, "verbatim": ""},
                        "T1", "stub-model", field=field)
        assert ev["value"] is None, field
```

```python
# tests/test_validate_handler.py
def test_iso_certificate_with_na_regulation_is_not_incomplete(conn):
    """The C4 manufacturer-binding path exists for exactly this document; it must
    be reachable, not killed by the completeness check twelve lines earlier."""
    fields = {
        "type": {"value": "ISO", "conf": 0.99, "tier": "T1", "verbatim": "ISO 13485"},
        "regulation": {"value": "n.a.", "conf": 0.95, "tier": "T1", "verbatim": "ISO 13485"},
        "coverage_scope": {"value": "manufacturer", "conf": 0.99, "tier": "T0",
                           "verbatim": "type=ISO"},
    }
    # seed an extraction_attempt + fetch_log, enqueue validate.doc, run the handler
    # (same seeding helpers the surrounding tests use)
    result = _run_validate(conn, fields)
    assert result.get("reason") != "incomplete-evidence"
    assert result["emitted"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_llm.py -k na_ tests/test_validate_handler.py -k iso_certificate -v`
Expected: `test_na_is_preserved_for_regulation` FAILS (`None != 'n.a.'`); `test_na_is_still_coerced_to_null_for_other_fields` FAILS with `TypeError: _to_ev() got an unexpected keyword argument 'field'`; the validate test FAILS with `reason == 'incomplete-evidence'`.

- [ ] **Step 3: Make the coercion field-scoped**

`app/extract/llm.py`:

```python
# Sentinel strings the model emits for an absent field instead of null. Treat as
# null so validate (C4) / gate (evidence) see a real absence (S0.4: regulation
# 'n.a.' was 6/20 false-positives).
_NULLISH = {"n.a.", "n/a", "na", "n.a", "none", "null", ""}

#: Fields where a sentinel is a REAL value rather than an absence. `regulation`
#: is MDR|MDD|n.a. in the schema (005_registry.sql): an ISO/QMS certificate has
#: no regulation by nature, and coercing it to null makes VALIDATE reject the
#: document as incomplete-evidence before the C4 binding rule can see it.
#: The S0.4 false-positive rate this coercion was protecting against is handled
#: in the prompts instead - they now demand the document say so explicitly.
_SENTINEL_IS_A_VALUE = {"regulation"}


def _norm_value(v, field: str | None = None):
    if field in _SENTINEL_IS_A_VALUE:
        return v
    if isinstance(v, str) and v.strip().lower() in _NULLISH:
        return None
    return v


def _to_ev(raw: dict, tier: str, model_id: str, field: str | None = None) -> dict:
    return {
        "value": _norm_value(raw.get("value"), field),
        ...
    }
```

Then find every `_to_ev(` call site and pass the field name:

```bash
grep -n "_to_ev(" app/extract/llm.py
```

- [ ] **Step 4: Tighten both prompts**

In `app/extract/prompts/t1_text.md` and `app/extract/prompts/t2_vision.md`, replace the regulation instruction with:

```markdown
- `regulation`: `MDR` if the document cites Regulation (EU) 2017/745, `MDD` if it
  cites Directive 93/42/EEC, or `n.a.` **only** when the document is a quality-
  management or standards certificate (ISO 13485, ISO 9001) that names no medical
  device regulation at all. Never answer `n.a.` because you could not find the
  regulation - if a device regulation is cited anywhere in the document, name it.
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_llm.py tests/test_validate_handler.py -q`
Expected: PASS.

- [ ] **Step 6: Note the re-extraction dependency in the commit**

This changes what the LLM tiers write, so it only takes effect for documents extracted **after** it lands. That is precisely why it is in the pre-spend set — the 3 affected documents today become roughly 60 across the full corpus, and fixing it after the backfill means paying for them twice.

- [ ] **Step 7: Commit**

```bash
git add app/extract/llm.py app/extract/prompts/t1_text.md app/extract/prompts/t2_vision.md \
        tests/test_llm.py tests/test_validate_handler.py
git commit -m "extract: let an ISO certificate carry regulation 'n.a.' instead of dying as incomplete"
```

---

### Task 4: `coverage_scope` is decided by a low-confidence guess over a deterministic fact

**Measured on document #41:** stored `coverage_scope = "item"` at confidence **0.4** from T2, verbatim *"Artikelliste / Article List: Gemäß Anhang / According to the Attachment"*. T0 had said `"group"` at **0.9**. The document lists 15 articles, so `group` is right and `item` is wrong — and the wrong answer won because `merge` lets a later tier's different non-empty value through (deliberate, and correct in general).

**Root cause:** `derive_coverage_scope` returns 0.9 for a DoC with a REF list, `needs_escalation`'s threshold is 0.95, so a scope that was *deduced from a REF list we already hold* escalates to a vision model every time and can be overwritten by a guess.

**The fix is to stop guessing at something we can derive.** Scope follows from the REF list: no REFs → the document does not enumerate items; exactly one → item scope; more than one → group scope. That is a deduction, not an estimate, so it deserves a confidence above the escalation threshold.

**Decision required before implementing.** This raises a T0 confidence above `cfg.gate.high`, which affects GATE dispositions, and it changes what "item" vs "group" means for a single-REF DoC. Confirm with Denis:
1. Is a DoC listing exactly one article `item` scope or `group` scope? The plan below assumes **`item`**.
2. Are we content that `coverage_scope` stops reaching T1/T2 for DoCs with a REF list? It saves a field on ~53 of 61 documents and removes this failure mode entirely.

If (1) is answered `group`, change one branch; the rest of the task is unaffected.

**Files:**
- Modify: `app/extract/t0_templates.py:229-235`
- Test: `tests/test_t0.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `derive_coverage_scope(type_value: str, has_ref_list: bool, ref_count: int = 0) -> dict | None` — third parameter added with a default, so the existing call site keeps working until updated in the same task.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_t0.py
def test_coverage_scope_from_ref_count_is_deduced_not_guessed():
    """Scope follows from the REF list we already hold, so it must not escalate
    to a model that can answer 'item' at 0.4 for a 15-article declaration."""
    from app.extract import tiers

    many = t0.derive_coverage_scope("DoC", True, ref_count=15)
    assert many["value"] == "group"
    assert many["conf"] >= 0.95          # above the escalation threshold

    one = t0.derive_coverage_scope("DoC", True, ref_count=1)
    assert one["value"] == "item"
    assert one["conf"] >= 0.95

    none = t0.derive_coverage_scope("DoC", False, ref_count=0)
    assert none["value"] == "manufacturer"
    assert none["conf"] < 0.95           # genuinely uncertain, still escalates

    # the whole point: a deduced scope no longer reaches the LLM
    assert "coverage_scope" not in tiers.needs_escalation({"coverage_scope": many})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_t0.py -k coverage_scope_from_ref_count -v`
Expected: FAIL — `derive_coverage_scope() got an unexpected keyword argument 'ref_count'`.

- [ ] **Step 3: Implement**

```python
def derive_coverage_scope(type_value: str, has_ref_list: bool, ref_count: int = 0) -> dict | None:
    """Scope is DEDUCED from the REF list, not estimated. A declaration that
    enumerates 15 articles is group-scope as a matter of fact, so this carries a
    confidence above the escalation threshold - otherwise a vision tier reading
    the 'Gemaess Anhang' field answers 'item' at 0.4 and wins the merge.
    With no REF list there is nothing to deduce from, so the old low confidence
    stands and the field still escalates."""
    if type_value in ("ISO", "EC"):
        return field_ev("manufacturer", 1.0, f"type={type_value}")
    if type_value == "DoC":
        if not has_ref_list:
            return field_ev("manufacturer", 0.6, "type=DoC ref=False")
        scope = "item" if ref_count == 1 else "group"
        return field_ev(scope, 0.97, f"type=DoC ref_count={ref_count}")
    return None
```

Update the single call site (`t0_extract`, around line 385):

```python
    if type_val:
        refs = (ref_ev or {}).get("value") or []
        cs = derive_coverage_scope(type_val, bool(refs), ref_count=len(refs))
        if cs:
            fields["coverage_scope"] = cs
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_t0.py tests/test_tiers.py tests/test_t0_corpus.py -q`
Expected: PASS. `corpus_manifest.py` entries assert `t0["coverage_scope"]` for several fixtures — a single-REF fixture that previously read `group` now reads `item` and its manifest entry must be corrected. **Correct the manifest, not the code**, and say so in the commit.

- [ ] **Step 5: Mutation-test**

Lower the deduced confidence back to 0.9, confirm the `needs_escalation` assertion goes red, restore. That is the assertion carrying the actual fix.

- [ ] **Step 6: Commit**

```bash
git add app/extract/t0_templates.py tests/test_t0.py tests/fixtures/corpus_manifest.py
git commit -m "extract: deduce coverage_scope from the REF count instead of escalating it"
```

---

### Task 5: every backfilled document's archive link 404s

**Verified 2026-08-12:** `document.archive_url` for doc #41 is `/srv/dentalia/imports/dentalia-sftp/GC/DOC/GC Europe_IIa/Unifast III_23042025.pdf` — a **host** path. `web` serves `/archive/{path}` from `WEB_ARCHIVE_ROOT=/archive` (the `archive_data` volume), and that path does not exist inside the container. So the "archived file" link is dead for all 57 documents. "stored text" works because it comes from the database.

**Root cause:** `backfill.py:86` sets `archive_url` to `str(path.resolve())` instead of archiving through `StorageAdapter` the way FETCH and UPLOAD do via `app/handlers/archiving.py`.

**Decision required before implementing.** Two options, and this is the open `[backfill-archive-url]` followup:

- **(A) Archive properly** — BACKFILL copies bytes through `StorageAdapter` into the hash-addressed archive, exactly like FETCH. `archive_url` becomes `/archive/<layout path>`, the link works, and CLAUDE.md end-state goal 2 ("hash-addressed file archive under our control") is met. Costs a copy of 1.307 files (~ a few GB) and needs the existing 57 rows repaired.
- **(B) Serve the corpus** — keep the source path but store it relative to the imports root, and resolve corpus-sourced URLs under `/imports`, which `web` **already mounts read-only** (`docker-compose.yml`: `${IMPORTS_HOST:-./imports}:/imports:ro`). Cheap, no copy, but leaves `archive_url` pointing at a gitignored hand-managed directory that the followup already warns "reorganising breaks every backfilled archive_url".

**Recommend (A).** It is the documented end state, and it must land before the backfill either way: running the full sweep under current code creates 1.158 more broken links.

The steps below implement **(A)**.

**Files:**
- Modify: `app/handlers/backfill.py:63-92`
- Test: `tests/test_backfill_handler.py`

**Interfaces:**
- Consumes: `archiving.archive_path(manufacturer, content_hash, url, content_type)` and the configured `StorageAdapter` (`app/adapters/storage.py`, `make_storage_adapter`), both existing.
- Produces: `extract.doc` payload `archive_url` is now a storage URL, not a filesystem path. No payload key changes — additive-only rule respected because the key already exists and consumers already treat it opaquely.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backfill_handler.py
def test_backfill_archives_through_storage_not_the_source_path(conn, tmp_path, monkeypatch):
    """A corpus path is not an archive: `web` serves archive_url from the archive
    root, and the gitignored imports directory is not mounted there."""
    corpus = tmp_path / "GC"
    corpus.mkdir()
    (corpus / "doc.pdf").write_bytes(b"%PDF-1.4 test")

    _run_backfill(conn, str(tmp_path))

    payload = conn.execute(
        "SELECT payload FROM job WHERE type='extract.doc'"
    ).fetchone()["payload"]
    assert not payload["archive_url"].startswith(str(tmp_path))
    assert payload["content_hash"] in payload["archive_url"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_backfill_handler.py -k archives_through_storage -v`
Expected: FAIL — `archive_url` is the source path and the hash is absent from it.

- [ ] **Step 3: Implement**

In `handle_backfill_scan`, after the seen-check and before the enqueue, put the bytes through the adapter — mirroring what `fetch.py` does through `archiving.py`:

```python
        stored_url = store.put(
            archiving.archive_path("backfill", content_hash, str(path), "application/pdf"),
            path.read_bytes(),
        )
        queue.enqueue(
            conn, "extract.doc",
            {"archive_url": stored_url, "content_hash": content_hash,
             "group_id": None, "source_url": str(path.relative_to(folder))},
            dedupe_key=f"extract:{content_hash}",
        )
```

`source_url` keeps the corpus-relative path, so provenance is not lost.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_backfill_handler.py -q`
Expected: PASS.

- [ ] **Step 5: Repair the 57 existing rows**

They carry host paths. Options in order of preference: re-run `backfill.scan` after deleting the 61 `fetch_log` rows (the `[backfill-not-regenerable]` procedure, which re-archives correctly), or a one-off that copies each file into the archive and updates `document.archive_url`. **Do not hand-edit `archive_url` without moving the bytes** — a URL that resolves to nothing is what we are fixing.

- [ ] **Step 6: Verify in the running UI**

```bash
docker compose build worker web && docker compose up -d --force-recreate worker web
curl -s -o /dev/null -w '%{http_code}\n' -u "$USER:$PASS" \
     "http://127.0.0.1:8000/documents/41"
```

Then open document #41 and click "archived file". Expected: the PDF renders. Anything else means the bytes are not where the URL says.

- [ ] **Step 7: Commit**

```bash
git add app/handlers/backfill.py tests/test_backfill_handler.py
git commit -m "backfill: archive corpus bytes through StorageAdapter so archive_url resolves"
```

---

### Task 6: does `item_ref` beat `mfr_ref` as the matching key? (free, gates nothing)

**Why:** the client's C12 answer said the supplier article number is *"ni tako pomembna"* and the primary item number is what is printed on the physical article. Measured against the live registry, she is right: `mfr_ref == item_ref` for 4.884 of 10.127 members (48%), 4.387 (43%) have **no** `mfr_ref` at all, and only 856 (8,5%) differ. Over the 505 REFs extracted from the 61 GC documents, `item_ref` reaches **24** items where `mfr_ref` reaches 13 — the 13 being a strict subset.

**This is a measurement, not a change.** It is read-only, costs nothing, and can be redone after any backfill. Do not ship a matching change on 24 items.

**Files:**
- Create: `tools/match_key_analysis.py` (read-only, dev-time, excluded from the wheel)

- [ ] **Step 1: Write the measurement**

Report, over the whole catalogue and per corpus brand:
1. Distinct `item_ref` values; how many collide across `canonical_manufacturer` (the `mfr_ref` equivalent was 24 of 5.511, 0,44%).
2. How many `item_ref` values are shorter than `cfg.validate.min_unscoped_ref_len`, or match the `mfr_ref_prose` rule.
3. Documents that would gain a link under `item_ref` matching, and any that would become **ambiguous**.
4. The overlap: `item_ref`-only, `mfr_ref`-only, both.

- [ ] **Step 2: Run it and write the numbers into the plan's Findings section**

Run: `PYTHONPATH=. .venv/bin/python -m tools.match_key_analysis`

- [ ] **Step 3: Decide, in writing, before touching `validate.py`**

The `ref-catalogue` guards (min length, prose exclusion, cross-manufacturer ambiguity) were calibrated on `mfr_ref` values. They **must be re-derived** over `item_ref`, not reused — `item_ref` is Dentalia's own numbering and its collision and length profiles are different. If the numbers support it, that change is its own plan.

#### Findings (run 2026-08-12, after the 148-document GC backfill)

`tools/match_key_analysis.py`, against the live registry. **The numbers support the client's ruling, and by a wider margin than the earlier 24-vs-13 sample suggested.**

| | `mfr_ref` | `item_ref` |
|---|---|---|
| distinct values | 8.330 | 15.958 |
| **collide across manufacturers** | 34 (0,41%) | **0 (0,00%)** |
| eligible after guards (min_len=6, prose) | 7.172 | 13.844 |
| too short | 1.094 | 2.114 |
| prose (`!`) | 64 | **0** |

Over the 1.502 distinct REFs extracted from the GC corpus: 69 reach an item through **both** keys, 56 through `item_ref` **only**, and — the decisive number — **`mfr_only` is 0**. `item_ref`'s reach is a strict SUPERSET of `mfr_ref`'s, so admitting it loses nothing. Catalogue items reached: 70 by `mfr_ref`, **125** by `item_ref`, 125 by either. Of the 90 documents holding a `ref_list` but no link today, **17 would gain one**.

Zero collisions is structural, not luck: `item_ref` is `item_mirror`'s PRIMARY KEY, so one value is one item is one manufacturer. That is a stronger guarantee than `mfr_ref` ever had.

**The residual risk is unchanged and is NOT measured by the above.** Catalogue-observed uniqueness is not global uniqueness: a manufacturer we do not carry may print an article number that coincides with a Dentalia `item_ref` belonging to someone else (found by hand: `2302` is a glove under code `333`). The existing cross-manufacturer ambiguity guard already contains this — `_manufacturers_holding_refs` must resolve the whole extracted `ref_list` to exactly ONE manufacturer or the document is flagged `multi-manufacturer-ref` and never linked. So a stray coincidence makes a document ambiguous, not wrongly linked.

**Recommendation:** admit `item_ref` alongside `mfr_ref` in the unscoped `ref-catalogue` path, keeping every existing guard and the `staged` cap. Whether it should also key the SCOPED path — which can reach `production` and whose gate invariant 3 defines as `(canonical_manufacturer, mfr_ref)` — is a PRD/invariant change and stays open for Denis.

#### Step 3 decision and result (2026-08-13)

**Decided: both paths.** Denis, restating the client's C12 ruling — "the client says mfr_ref is not as important, at least not now. item_ref is the key." Invariant 3's comparand became the pair `(canonical_manufacturer, catalogue article number)`, and `item_ref` matches on the scoped path carry their own production-capable basis `ref-item` (migration 023). The adapter route (`mfr_ref_source_by_code` → `item_ref`) was rejected: it replaces rather than fills, so it would overwrite 156 genuine vendor values and make `mfr_ref` hold a Dentalia number against its own contract.

**Applied to the live registry**, `validate.doc` re-emitted over all 148 extractions, queue drained, **$0** (`extraction_cost` unchanged at $5,4369, last call 2026-08-12 13:46 — no re-extraction, exactly as the "matching re-runs free" principle predicts):

| | before | after |
|---|---|---|
| links | 43 | **88** |
| documents with ≥1 link | 35 | **52** |
| distinct items covered | 43 | **88** |
| production links | 0 | **0** |

The +17 documents match this section's prediction exactly. Dead jobs stayed at the same 4 + 2 documents (the two known open defects re-attempted and re-died); nothing new was lost.

**The production half is built but unreachable today, and that is a separate blocker.** All 88 links are still `ref-catalogue`/`staged`, because `ref-item` only forms on the SCOPED path and no corpus document reaches it: `manufacturer` is not in `tiers.TARGET`, 0 of 159 `extraction_attempt` rows carry the field, and all 444 `validate.doc` payloads have `group_id = null`, so every backfill document falls to the unscoped branch where every basis is rewritten to the capped `ref-catalogue`. Production coverage therefore waits on manufacturer resolution (`[mfr-resolution-tightening]`, `[ext-manufacturer-p2]`), not on the matching key. The measured gain from this change is the doubled staged coverage above.

---

## Self-review

**Spec coverage:** GC truncation → Task 1. `[t0-ref-false-positive]` → Task 2. `[validate-iso-regulation]` → Task 3. Wrong `coverage_scope` → Task 4. `[backfill-archive-url]` → Task 5. `[match-on-item-ref]` → Task 6. All six raised items are covered.

**Two open decisions** are flagged inline rather than silently resolved: single-REF scope (Task 4) and archive strategy A vs B (Task 5).

**Not in this plan, deliberately:** `[worker-type-filter]` and `[stale-image-trap]` are operational and gate nothing; the full backfill itself (that is the spend, after Tasks 1–5); `ext.manufacturer` Phase 2; and the client-facing replies.

**Sequencing:** Tasks 1–5 before the backfill. Task 1 must precede Task 2 (Task 1 is what starts admitting the Basic UDI-DI that Task 2 rejects) — otherwise Task 2's UDI case has no live source. Task 6 is independent of all of them.
