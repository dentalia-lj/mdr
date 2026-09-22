# Playbook Rules Into Extraction Implementation Plan

**Goal:** Let the authored playbook steer extraction — additive T0 lexicons for the three fields that actually drive escalation, and a per-manufacturer hint block that T1 and T2 currently never see.

**Architecture:** `t0_extract` resolves the playbook from its own identity match (backfill documents carry no `group_id`, so a group-derived manufacturer would help almost nothing today), then hands that playbook's lexicons to the date/cert/REF extractors — always **merged** with the global lists, never replacing them. The same playbook's free-text hints are threaded through `llm._params`, the single function both the sync and batch transports build their request from, so the two can never diverge. Every hint-steered extraction records the playbook revision that steered it.

**Tech Stack:** Python 3.12, psycopg 3 (raw SQL, no ORM), Anthropic Messages + Batch API, pytest against a real Postgres. No new dependencies.

**Verified against:** HEAD on 2026-08-18 — re-verified after the Komet
companion-annex feature landed. That feature added a 6th playbook key
(`companion`), a 4th `t0_extract` parameter (`companion_url`), a second
`extract_ref_list` call site (`_ref_from_companion`), and `import re` to
`app/playbooks.py`. Every task below accounts for all four.

**Spec:** `docs/superpowers/specs/2026-08-18-playbooks-across-stages-design.md` — items 5, 7, 8, and what survives of 9.

## Why these three fields, in this order

Measured on the live registry, 2026-08-18 (510 extraction attempts, $14.71 spent, T2 = 78% of the bill):

| Field | T0 | T1 | T2 | T0 share | Targeted by |
|---|---|---|---|---|---|
| `type` | 453 | 11 | 19 | 94% | — already fine |
| `coverage_scope` | 445 | 19 | 19 | 92% | — already fine |
| `regulation` | 423 | 25 | 35 | 88% | — already fine |
| `basic_udi_di` | 202 | 210 | 50 | 44% | — no per-manufacturer shape to author |
| `manufacturer` | 129 | 175 | 30 | 39% | solved positionally on 2026-08-18 |
| **`validity_to`** | 160 | 202 | **121** | 33% | **Task 3** `date_labels` |
| **`validity_from`** | 51 | 52 | **87** | 27% | **Task 3** `date_labels` |
| **`cert_number`** | 110 | 354 | 19 | 23% | **Task 4** `cert_number_pattern` |
| **`ref_list`** | 84 | 314 | 64 | 18% | **Task 5** `ref_pattern` |

**208 of the 239 vision calls are spent reading dates.** That is why Task 3 comes first.

**Honest caveat, to be resolved before Task 3 is accepted:** T2 fires wholesale when `pdf.is_scan(doc)` is true, dragging every field into vision regardless of whether T0 could have read it. Some unknown share of those 208 is scanned documents that no lexicon can help. Task 3 Step 0 measures that share and stops the task if it is dominant.

## Global Constraints

- **Lexicons MERGE, never replace.** A playbook that replaces a global list silently loses a field the generic path would have found. Every lexicon addition is a union with the global list, and the evidence `verbatim` records which label actually matched.
- **T0 stays deterministic and free.** No playbook key may make T0 call anything, or raise. A missing/malformed playbook degrades to today's exact behaviour.
- **Playbook fields are additive-only.** The 11 authored playbooks carry none of these keys and must keep working untouched.
- **Sync and batch must submit byte-identical requests.** They already share `llm._params`; every change goes through it. Divergence here is silent and unfixable after the fact.
- **`T1_SYSTEM` / `T2_SYSTEM` stay byte-identical.** Hints go in a *second* system block, never interpolated into the prompt file — that keeps the static prefix cacheable if prompt caching is ever added, and keeps the prompt files reviewable as prose.
- **A hint may never assert identity.** It hedges, it does not confirm. See Task 7's four guards.
- **Every production value carries complete evidence** (invariant 2). A hint-steered value additionally carries the playbook revision that steered it — that is Task 1, and it is a hard prerequisite for Task 7.
- Each task touches at most 3 files, tests excluded.
- **Tests run in an ISOLATED compose project.** Export this once per shell, before
  any test command in this plan:
  ```bash
  export WTC="COMPOSE_PROJECT_NAME=dentalia_wt POSTGRES_PORT=5433 PGDATA_HOST=/srv/pgdata/dentalia_wt docker compose -f docker-compose.yml -f .superpowers/sdd/2026-08-18-playbook-rules-into-extraction/wt-override.yml"
  ```
  Then every test command below is `$WTC --profile test run ...` — `$WTC`
  REPLACES the words `docker compose`, it is not a flag.

  **Four separate collisions make all of this necessary.** `docker-compose.yml`
  pins `name: dentalia`, so a project name must be forced; the postgres service
  bind-mounts a HOST path (`PGDATA_HOST`) that no project name scopes — two
  postgres on one PGDATA corrupts the cluster; it publishes host port 5432; and
  it pins explicit `container_name` values, which are GLOBAL in Docker and
  collide with the running stack before anything starts. The override file
  renames those containers and is kept in the git-ignored workspace so the
  branch diff stays clean.

  Known and accepted: `dentalia-test:latest` is a fixed image tag that no
  project name scopes, so `--build` rebuilds the tag the main checkout also
  uses. Harmless unless both trees run tests simultaneously — don't.

  A `.env` file is required (compose interpolates `ANTHROPIC_API_KEY` for
  `worker` even when only `test` is run). It is git-ignored, so it does NOT
  come across into a worktree — copy it from the main checkout.

  Purge when the branch is done:
  ```bash
  COMPOSE_PROJECT_NAME=dentalia_wt docker compose down -v
  rm -rf /srv/pgdata/dentalia_wt
  ```
- **The `docker compose exec -T postgres psql` measurement commands are the
  exception** — they read the LIVE registry and must be run from the main
  checkout `/srv/dentalia` with NO `$WTC`. The worktree's
  postgres is an empty throwaway and would report zero for everything.
- **There is no playbook parse cache, and this plan does not add one.**
  `playbooks.clear_cache()` belongs to `2026-08-18-playbook-fetch-reachability.md`
  Task 1, which is shelved and NOT part of this plan. `load_playbooks(dir)`
  re-reads from disk on every call today, so a test that points
  `PLAYBOOKS_DIR` at a `tmp_path` needs no cache reset. Do not call
  `clear_cache()` anywhere — it does not exist.
- Commit prefixes matching the log: `playbooks:`, `extract:`, `docs:`. No AI attribution.

---

### Task 1: Playbook revision, recorded on the extraction

Evidence carries `model_id` but nothing identifying the rules that shaped the read. `extract_rev` does not cover this — it is a per-`content_hash` attempt counter (`app/extract/tiers.py:293`). Without this, "why did this document extract differently in March" is unanswerable, and Task 7 would make that question routine.

**Files:**
- Create: `migrations/028_extraction_playbook_rev.sql`
- Modify: `app/playbooks.py`
- Modify: `app/extract/tiers.py`
- Test: `tests/test_playbooks.py`, `tests/test_extract_handler.py`

**Interfaces:**
- Produces: `Playbook.rev: int` (default `0` = unversioned); `tiers.write_extraction_attempt(..., playbook_slug: str | None = None, playbook_rev: int | None = None)`.

- [x] **Step 1: Write the failing tests**

```python
# tests/test_playbooks.py
def test_rev_parses_and_defaults_to_zero(tmp_path):
    """0 means 'unversioned'. Every playbook authored before this key existed
    reads as 0, which is honest -- it says 'nobody has stamped this yet'."""
    _write(tmp_path, "voco.json", VOCO)
    _write(tmp_path, "komet.json", {"manufacturer": "KOMET", "rev": 3})

    loaded = {pb.slug: pb for pb in playbooks.load_playbooks(tmp_path)}

    assert loaded["voco"].rev == 0
    assert loaded["komet"].rev == 3


def test_rev_must_be_an_integer(tmp_path):
    _write(tmp_path, "bad.json", {"manufacturer": "BAD AG", "rev": "3"})

    assert playbooks.load_playbooks(tmp_path) == ()
```

```python
# tests/test_extract_handler.py
def test_extraction_attempt_records_the_steering_playbook(conn):
    tiers.write_extraction_attempt(
        conn, "abc123", ["T0"], {"type": {"value": "DoC"}},
        extract_rev=1, playbook_slug="komet", playbook_rev=3,
    )

    row = conn.execute(
        "SELECT playbook_slug, playbook_rev FROM extraction_attempt "
        "WHERE content_hash=%s", ("abc123",),
    ).fetchone()
    assert (row["playbook_slug"], row["playbook_rev"]) == ("komet", 3)


def test_extraction_attempt_without_a_playbook_records_nulls(conn):
    """No playbook claimed this document -- the columns say so rather than
    guessing a slug."""
    tiers.write_extraction_attempt(conn, "def456", ["T0"], {}, extract_rev=1)

    row = conn.execute(
        "SELECT playbook_slug, playbook_rev FROM extraction_attempt "
        "WHERE content_hash=%s", ("def456",),
    ).fetchone()
    assert (row["playbook_slug"], row["playbook_rev"]) == (None, None)
```

- [x] **Step 2: Run to verify they fail**

Run: `$WTC --profile test run --rm test pytest tests/test_playbooks.py -k rev tests/test_extract_handler.py -k steering_playbook -v`
Expected: FAIL — `psycopg.errors.UndefinedColumn: column "playbook_slug" does not exist`

- [x] **Step 3: Migration**

Create `migrations/028_extraction_playbook_rev.sql`:

```sql
-- Which authored playbook shaped this extraction, and at what revision.
--
-- `model_id` already records WHICH MODEL read the document. From S1.7 onward a
-- playbook also shapes the read: its lexicons steer T0's date/cert/REF
-- extractors, and its `extract_hints` are appended to the T1/T2 system prompt.
-- Evidence that does not name the rules is not reproducible, and invariant 2
-- requires that a production value can be defended later.
--
-- `extract_rev` does NOT cover this: it is a per-content_hash attempt counter,
-- so it changes when a document is re-extracted for any reason and says nothing
-- about what the rules were at the time.
--
-- Nullable on purpose: most documents are claimed by no playbook, and NULL is
-- the honest answer for them. rev 0 means "claimed, but the file carries no
-- revision stamp" -- distinct from "no playbook at all".
ALTER TABLE extraction_attempt
  ADD COLUMN playbook_slug text,
  ADD COLUMN playbook_rev  int;
```

- [x] **Step 4: `rev` on the dataclass**

In `app/playbooks.py`, add to `Playbook` after `companion` (line 79 — the
last field, added by the companion feature):

```python
    # Hand-bumped revision. 0 = unversioned (every file authored before this key
    # existed). Recorded on `extraction_attempt` whenever this playbook steered a
    # read, so an extraction stays defensible after the file changes.
    rev: int = 0
```

In `_parse`, before the return:

```python
    rev = data.get("rev", 0)
    if not isinstance(rev, int) or isinstance(rev, bool) or rev < 0:
        raise ValueError("rev must be a non-negative integer")
```

and pass `rev=rev` in the `Playbook(...)` construction.

- [x] **Step 5: Record it**

In `app/extract/tiers.py`, replace `write_extraction_attempt`:

```python
def write_extraction_attempt(
    conn, content_hash: str, tiers_used: list[str], fields: dict,
    extract_rev: int = 1, model_id: str | None = None,
    playbook_slug: str | None = None, playbook_rev: int | None = None,
) -> int:
    """Insert one extraction_attempt row. Per-field tier/model_id/evidence live
    inside `fields`; the row's `tier` summarises the ladder ('T0+T1').

    `playbook_slug`/`playbook_rev` name the authored rules that shaped this read
    (migration 028) -- NULL when no playbook claimed the document, which is the
    common case. Without them a hint-steered extraction is not reproducible from
    its evidence, which invariant 2 requires."""
    return conn.execute(
        "INSERT INTO extraction_attempt (content_hash, tier, model_id, fields, "
        "extract_rev, playbook_slug, playbook_rev) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (content_hash, "+".join(tiers_used), model_id, Json(fields), extract_rev,
         playbook_slug, playbook_rev),
    ).fetchone()["id"]
```

- [x] **Step 6: Run to verify they pass**

Run: `$WTC --profile test run --rm --build test`
Expected: PASS. The rebuild is required — the migration runner applies 028 at session setup.

- [x] **Step 7: Commit**

```bash
git add migrations/028_extraction_playbook_rev.sql app/playbooks.py app/extract/tiers.py \
        tests/test_playbooks.py tests/test_extract_handler.py
git commit -m "extract: record which playbook revision shaped an extraction"
```

---

### Task 2: T0 resolves its own steering playbook

Prerequisite for Tasks 3-5, and a pure refactor: no lexicon changes yet, no behaviour change, one reorder.

`t0_extract` currently computes `cert_number` **before** `manufacturer`. Tasks 3-5 need the playbook chosen before those extractors run. The reorder is safe: `extract_cert_number(full)`, `extract_dates(full)` and `extract_manufacturer(full, playbooks)` each depend only on `full` and the playbook set — there is no data dependency between them.

Note the identity source. Every one of the 343 documents in the fetch ledger arrived via `backfill`, and BACKFILL emits `extract.doc` with `group_id: None` — so a group-derived manufacturer would steer almost nothing today. T0's own match is the real source; the optional caller override exists for the fetch path, which will carry a group.

**Files:**
- Modify: `app/extract/t0_templates.py`
- Test: `tests/test_t0.py`

**Interfaces:**
- Produces: `t0_extract(doc, filename="", result=None, playbook=None) -> tuple[str, dict]` — unchanged return type; `playbook` is an optional `Playbook` override.
- Produces: `t0_templates.steering_playbook(full_text, playbooks=None, override=None) -> Playbook | None`.

- [x] **Step 1: Write the failing tests**

```python
# tests/test_t0.py
def test_steering_playbook_is_the_one_the_document_names():
    from app import playbooks as pb_mod
    from app.playbooks import Playbook

    voco = Playbook(slug="voco", manufacturer="VOCO GmbH", aliases=("VOCO",))
    komet = Playbook(slug="komet", manufacturer="KOMET")

    got = t0.steering_playbook("Declaration by VOCO GmbH, Cuxhaven", (voco, komet))

    assert got.slug == "voco"


def test_steering_playbook_is_none_when_the_document_names_nobody():
    from app.playbooks import Playbook

    voco = Playbook(slug="voco", manufacturer="VOCO GmbH")

    assert t0.steering_playbook("Declaration by SOMEONE ELSE", (voco,)) is None


def test_steering_playbook_prefers_an_authoritative_override():
    """A group's canonical_manufacturer is established before extraction; T0's
    own text match is a guess. When the caller has the former, it wins."""
    from app.playbooks import Playbook

    voco = Playbook(slug="voco", manufacturer="VOCO GmbH")
    komet = Playbook(slug="komet", manufacturer="KOMET")

    got = t0.steering_playbook("Declaration by VOCO GmbH", (voco, komet), override=komet)

    assert got.slug == "komet"


def test_t0_extract_is_unchanged_by_the_reorder(fixture_pdf):
    """Pure refactor guard over a committed fixture (never skips, unlike
    `corpus_pdf`). Capture the CURRENT output before touching the code and
    paste it below — this test must pin today's behaviour, not an assumption
    about it."""
    path = fixture_pdf("GC/Fuji_Coat_LC_12022026.pdf")
    with pdf.open_doc(path) as doc:
        doc_class, fields = t0.t0_extract(doc, path)

    assert doc_class == "compliance"
    assert fields["type"]["value"] == "DoC"
    assert fields["manufacturer"]["value"] == "GC EUROPE N.V."
```

Capture the baseline first:

```bash
$WTC --profile test run --rm test python -c "
from app.extract import pdf, t0_templates as t0
p='tests/fixtures/corpus/GC/Fuji_Coat_LC_12022026.pdf'
with pdf.open_doc(p) as d: print(t0.t0_extract(d, p))
"
```

Paste what it prints into the assertions above, then make the change. If the
reorder alters a single value, the refactor is not behaviour-neutral and the
task is wrong.

- [x] **Step 2: Run to verify they fail**

Run: `$WTC --profile test run --rm test pytest tests/test_t0.py -k steering -v`
Expected: FAIL — `AttributeError: module 'app.extract.t0_templates' has no attribute 'steering_playbook'`

- [x] **Step 3: Implement**

In `app/extract/t0_templates.py`, add above `t0_extract`:

```python
def steering_playbook(full_text: str, playbooks=None, override=None):
    """The playbook whose lexicons and hints apply to this document.

    `override` is an authoritative manufacturer the CALLER established before
    extraction (a group's `canonical_manufacturer`); it always wins, because
    T0's own text match is a guess and the group's is not. With no override this
    falls back to the same identity match `extract_manufacturer` performs.

    Returns None when nothing claims the document -- the common case, and the
    one that must behave exactly as T0 did before playbook lexicons existed."""
    if override is not None:
        return override
    if playbooks is None:
        playbooks = playbooks_mod.load_playbooks()
    ev = extract_manufacturer(full_text, playbooks)
    if not ev:
        return None
    return playbooks_mod.for_manufacturer(ev["value"], playbooks)
```

Then in `t0_extract`, change the signature and the field block:

```python
def t0_extract(doc, filename: str = "", result=None,
               companion_url: str | None = None, playbook=None) -> tuple[str, dict]:
```

`companion_url` is the existing 4th parameter (Komet's separate product-list
annex). `playbook` goes AFTER it — both are keyword-passed by every caller, so
order is cosmetic, but appending keeps the diff to one line.

...adding to the docstring:

```
    `playbook` is an optional authoritative override (the requesting group's
    manufacturer). Absent it, T0 resolves the steering playbook from the
    document's own text -- which is the live path, because every backfilled
    document carries `group_id: None`.
```

Replace the identity block, hoisting the playbook resolution above the loop:

```python
    all_playbooks = playbooks_mod.load_playbooks()
    steering = steering_playbook(full, all_playbooks, override=playbook)

    fields: dict = {}
    # Identity is read from the document's OWN text, never the filename: the
    # corpus folder is the supplier, not the manufacturer. `manufacturer` is
    # computed first because `steering` depends on it and the lexicon-driven
    # extractors below depend on `steering`; the extractors are otherwise
    # order-independent (each reads only `full`).
    for name, ev in (
        ("manufacturer", extract_manufacturer(full, all_playbooks)),
        ("type", extract_type(full, filename)),
        ("regulation", extract_regulation(full, filename)),
        ("cert_number", extract_cert_number(full)),
        ("basic_udi_di", extract_basic_udi(full)),
    ):
        if ev:
            fields[name] = ev
    fields.update(extract_dates(full))
```

- [x] **Step 4: Run to verify they pass**

Run: `$WTC --profile test run --rm test pytest tests/test_t0.py tests/test_t0_layout.py tests/test_extract_handler.py -v`
Expected: PASS, with no change to any existing assertion — this task must be behaviour-neutral.

- [x] **Step 5: Commit**

```bash
git add app/extract/t0_templates.py tests/test_t0.py
git commit -m "extract: resolve the steering playbook before the field extractors"
```

---

### Task 3: `date_labels` — the 208 vision calls

Highest measured value in the plan. `_TO_LABELS` / `_FROM_LABELS` (`app/extract/t0_templates.py:90-94`) are a fixed EN/DE/SL list; a manufacturer that writes "Ausstellungsdatum" or "Data di emissione" is invisible to T0 and the document escalates — often all the way to vision.

**Files:**
- Modify: `app/extract/t0_templates.py`
- Modify: `playbooks/README.md`
- Test: `tests/test_t0.py`

**Interfaces:**
- Consumes: `steering_playbook` (Task 2).
- Produces: `extract_dates(text: str, playbook=None) -> dict`; `Playbook.date_labels: dict` (`{"from": [...], "to": [...]}`, empty by default) — parsed in `app/playbooks.py` the same way `ref_normalize` is.

- [x] **Step 0: Measure the scan share before building**

Run:

```bash
docker compose exec -T postgres psql -U dentalia -d dentalia -tAF' | ' -c "
SELECT a.tier, count(*)
FROM extraction_attempt a
WHERE a.fields->'validity_to'->>'tier' = 'T2'
   OR a.fields->'validity_from'->>'tier' = 'T2'
GROUP BY 1 ORDER BY 2 DESC;"
```

A row of `T0+T2` (T1 skipped) means the document was scan-forced into vision and
**no lexicon can help it**. A row of `T0+T1+T2` means T1 was tried and still left
the date open — which a label the lexicon is missing would explain.

If `T0+T2` dominates, **stop and re-scope**: the fix is scan handling, not
lexicons, and Tasks 4-5 become the priority instead. Record the numbers in the
commit message either way.

- [x] **Step 1: Write the failing tests**

```python
# tests/test_t0.py
def test_playbook_date_label_is_read_when_the_global_list_misses_it():
    """The measured driver: 208 of 239 vision calls went to dates. A label the
    global EN/DE/SL list does not carry sends the whole document to a paid tier
    for a date printed in plain text."""
    from app.playbooks import Playbook

    pb = Playbook(slug="acme", manufacturer="ACME",
                  date_labels={"from": ["ausstellungsdatum"]})
    text = "Konformitätserklärung\nAusstellungsdatum 12.02.2026\n"

    out = t0.extract_dates(text, pb)

    assert out["validity_from"]["value"] == "2026-02-12"


def test_playbook_labels_are_merged_with_the_globals_never_replacing_them():
    """A playbook that REPLACED the global list would silently lose every date
    the generic path already reads."""
    from app.playbooks import Playbook

    pb = Playbook(slug="acme", manufacturer="ACME",
                  date_labels={"from": ["ausstellungsdatum"]})
    text = "Date of issue 12.02.2026\n"

    out = t0.extract_dates(text, pb)

    assert out["validity_from"]["value"] == "2026-02-12"


def test_a_global_label_still_wins_when_both_match():
    """Determinism: the global list is tried first, so adding a playbook label
    can add a read but can never change an existing one."""
    from app.playbooks import Playbook

    pb = Playbook(slug="acme", manufacturer="ACME",
                  date_labels={"to": ["ablaufdatum"]})
    text = "Expiry date 01.01.2030\nAblaufdatum 02.02.2031\n"

    out = t0.extract_dates(text, pb)

    assert out["validity_to"]["value"] == "2030-01-01"


def test_no_playbook_leaves_date_extraction_exactly_as_it_was():
    text = "Date of issue 12.02.2026\nValid until 01.01.2030\n"

    assert t0.extract_dates(text) == t0.extract_dates(text, None)


def test_date_labels_must_be_an_object_of_lists(tmp_path):
    # tests/test_playbooks.py
    _write(tmp_path, "bad.json", {"manufacturer": "BAD AG", "date_labels": ["nope"]})

    assert playbooks.load_playbooks(tmp_path) == ()
```

- [x] **Step 2: Run to verify they fail**

Run: `$WTC --profile test run --rm test pytest tests/test_t0.py -k date_label -v`
Expected: FAIL — `TypeError: extract_dates() takes 1 positional argument but 2 were given`

- [x] **Step 3: Implement**

In `app/playbooks.py`, add to `Playbook` after `rev`:

```python
    # Additive date-label vocabulary for T0, {"from": [...], "to": [...]}.
    # MERGED with the global lists in `app/extract/t0_templates.py`, never
    # replacing them -- a replacement would silently lose every date the
    # generic path already reads.
    date_labels: dict = field(default_factory=dict)
```

(add `field` to the `dataclasses` import), and in `_parse`:

```python
    date_labels = data.get("date_labels") or {}
    if not isinstance(date_labels, dict):
        raise ValueError("date_labels must be an object")
    for key, vals in date_labels.items():
        if key not in ("from", "to"):
            raise ValueError(f"date_labels key must be 'from' or 'to', not {key!r}")
        if not isinstance(vals, list) or not all(isinstance(v, str) for v in vals):
            raise ValueError(f"date_labels[{key!r}] must be a list of strings")
```

then pass `date_labels=date_labels`.

In `app/extract/t0_templates.py`, replace the head of `extract_dates`:

```python
def extract_dates(text: str, playbook=None) -> dict:
    """Labelled validity dates, plus the place-and-date fallback for
    `validity_from`.

    `playbook.date_labels` EXTENDS the global label vocabulary; it never
    replaces it, and the globals are tried first so an authored label can add a
    read but never change one that already worked. Measured 2026-08-18: dates
    accounted for 208 of the 239 T2 vision calls on the corpus, which is what
    this key exists to reduce."""
    extra = (playbook.date_labels if playbook else None) or {}
    low = text.lower()
    out: dict = {}
    for field_name, labels in (
        ("validity_to", _TO_LABELS + [l.lower() for l in extra.get("to", [])]),
        ("validity_from", _FROM_LABELS + [l.lower() for l in extra.get("from", [])]),
    ):
        for lab in labels:
            i = low.find(lab)
            if i == -1:
                continue
            m = _DATE_RE.search(text[i : i + len(lab) + 30])
            if m:
                iso = _parse_date(m.group(1))
                if iso:
                    out[field_name] = field_ev(iso, 1.0, f"{text[i:i+len(lab)]} {m.group(1)}")
                    break
```

...leaving the `_PLACE_AND_DATE` fallback block below it untouched. Note the loop
variable rename from `field` to `field_name` — `field` is now the imported
dataclasses helper in `playbooks.py`, and shadowing it here would be confusing
even though the modules differ.

In `t0_extract`, pass the playbook through:

```python
    fields.update(extract_dates(full, steering))
```

- [x] **Step 4: Run to verify they pass**

Run: `$WTC --profile test run --rm test pytest tests/test_t0.py tests/test_playbooks.py -v`
Expected: PASS.

- [x] **Step 5: Document the key**

Add a row to the `## Fields` table in `playbooks/README.md`, after `ref_normalize`:

```markdown
| `date_labels` | no | `{"from": [...], "to": [...]}`. Extra date-label spellings for T0, MERGED with the global EN/DE/SL list. Read by `app/extract/t0_templates.py`. |
```

- [x] **Step 6: Commit**

Include the Step 0 numbers in the message body.

```bash
git add app/playbooks.py app/extract/t0_templates.py playbooks/README.md tests/test_t0.py tests/test_playbooks.py
git commit -m "extract: let a playbook add date labels the global list misses"
```

---

### Task 4: `cert_number_pattern`

T0 reads `cert_number` on 23% of documents; T1 answers 354. `_CERT_LABEL` / `_CERT_LABEL_BARE_NO` are two fixed regexes.

**Files:**
- Modify: `app/playbooks.py`
- Modify: `app/extract/t0_templates.py`
- Test: `tests/test_t0.py`

**Interfaces:**
- Consumes: `steering_playbook` (Task 2).
- Produces: `extract_cert_number(text: str, playbook=None) -> dict | None`; `Playbook.cert_number_pattern: str | None`.

- [x] **Step 1: Write the failing tests**

```python
def test_playbook_cert_pattern_reads_a_number_the_global_regexes_miss():
    from app.playbooks import Playbook

    pb = Playbook(slug="acme", manufacturer="ACME",
                  cert_number_pattern=r"Zertifikat-Kennung\s+([A-Z]{2}-\d{6})")
    text = "Zertifikat-Kennung DE-123456\n"

    ev = t0.extract_cert_number(text, pb)

    assert ev["value"] == "DE-123456"


def test_the_global_regexes_are_tried_first():
    """Additive, and deterministic: an authored pattern can add a read, never
    change one the generic path already made."""
    from app.playbooks import Playbook

    pb = Playbook(slug="acme", manufacturer="ACME",
                  cert_number_pattern=r"Kennung\s+(\S+)")
    text = "Certificate No. G1 234567 8901\nKennung WRONG-ONE\n"

    ev = t0.extract_cert_number(text, pb)

    assert ev["value"] != "WRONG-ONE"


def test_a_pattern_with_no_capture_group_is_a_malformed_playbook(tmp_path):
    # tests/test_playbooks.py -- caught at load, not at extract time, so a bad
    # pattern never reaches a document.
    _write(tmp_path, "bad.json", {"manufacturer": "BAD AG",
                                  "cert_number_pattern": r"no group here"})

    assert playbooks.load_playbooks(tmp_path) == ()


def test_an_uncompilable_pattern_is_a_malformed_playbook(tmp_path):
    _write(tmp_path, "bad.json", {"manufacturer": "BAD AG",
                                  "cert_number_pattern": r"([unclosed"})

    assert playbooks.load_playbooks(tmp_path) == ()


def test_no_playbook_leaves_cert_extraction_exactly_as_it_was():
    text = "Certificate No. G1 234567 8901\n"

    assert t0.extract_cert_number(text) == t0.extract_cert_number(text, None)
```

- [x] **Step 2: Run to verify they fail**

Run: `$WTC --profile test run --rm test pytest tests/test_t0.py -k cert_pattern tests/test_playbooks.py -k cert -v`
Expected: FAIL — `TypeError: extract_cert_number() takes 1 positional argument but 2 were given`

- [x] **Step 3: Implement**

In `app/playbooks.py`, add to `Playbook` immediately after `date_labels` (the field the previous task added — the five new keys land in task order, so append the validation block in `_parse` just above the `return Playbook(...)` too):

```python
    # Extra certificate-number regex for T0, tried AFTER the global patterns.
    # Must compile and must carry exactly one capture group (the number itself);
    # both are checked at load, so a bad pattern never reaches a document.
    cert_number_pattern: str | None = None
```

and in `_parse`:

```python
    cert_pattern = data.get("cert_number_pattern")
    if cert_pattern is not None:
        if not isinstance(cert_pattern, str):
            raise ValueError("cert_number_pattern must be a string")
        compiled = re.compile(cert_pattern)          # raises on a bad pattern
        if compiled.groups != 1:
            raise ValueError(
                f"cert_number_pattern must have exactly one capture group, "
                f"has {compiled.groups}"
            )
```

`import re` is already present (`app/playbooks.py:25`, added by the companion
feature) — do not add it twice. Then pass `cert_number_pattern=cert_pattern`.

In `app/extract/t0_templates.py`:

```python
def extract_cert_number(text: str, playbook=None) -> dict | None:
    """Certificate number. The two global label regexes are tried first, then a
    playbook's `cert_number_pattern` if it authored one -- additive, so an
    authored pattern can only add a read, never change one that already worked.

    The pattern compiled and its single capture group were both validated at
    playbook load, so this cannot raise on a bad authored regex."""
    m = _CERT_LABEL.search(text)
    if m:
        return field_ev(m.group(1).strip().rstrip("."), 1.0, m.group(0).strip())
    m = _CERT_LABEL_BARE_NO.search(text)
    if m:
        return field_ev(m.group(1).strip().rstrip("."), 1.0, m.group(0).strip())
    pattern = playbook.cert_number_pattern if playbook else None
    if pattern:
        m = re.search(pattern, text)
        if m:
            return field_ev(m.group(1).strip().rstrip("."), 1.0, m.group(0).strip())
    return None
```

and in `t0_extract`, change the tuple entry to `("cert_number", extract_cert_number(full, steering))`.

- [x] **Step 4: Run to verify they pass**

Run: `$WTC --profile test run --rm test pytest tests/test_t0.py tests/test_playbooks.py -v`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add app/playbooks.py app/extract/t0_templates.py tests/test_t0.py tests/test_playbooks.py
git commit -m "extract: a playbook may add a certificate-number pattern"
```

---

### Task 5: `ref_pattern`

T0's worst field: it reads `ref_list` on 18% of documents. `_looks_like_ref` (`app/extract/t0_templates.py:394`) is the single choke point all three parse strategies share, and it is a generic shape test — has a digit, under 30 chars, no prose word. A manufacturer with a known code shape can be exact instead.

Komet already proves the shape is knowable: `komet.json` authors
`ref_strategy_config.field_pattern` = `[A-Z0-9]{1,10}\.\d{3}\.\d{1,3}` for the
text-column strategy. `ref_pattern` generalises that to every strategy.

**Files:**
- Modify: `app/playbooks.py`
- Modify: `app/extract/t0_templates.py`
- Test: `tests/test_t0.py`

**Interfaces:**
- Consumes: `steering_playbook` (Task 2).
- Produces: `_looks_like_ref(val: str, pattern=None) -> bool`; `extract_ref_list(path, *, template=None, playbook=None)`; `Playbook.ref_pattern: str | None`.

- [x] **Step 1: Write the failing tests**

```python
def test_ref_pattern_rejects_a_code_that_is_not_this_manufacturers_shape():
    """The generic filter accepts anything with a digit and no prose. An
    authored shape turns that into an exact test -- which is what keeps page
    numbers, quantities and order codes out of ref_list."""
    assert t0._looks_like_ref("H1.314.006", r"^[A-Z0-9]{1,10}\.\d{3}\.\d{1,3}$")
    assert not t0._looks_like_ref("12", r"^[A-Z0-9]{1,10}\.\d{3}\.\d{1,3}$")


def test_ref_pattern_is_a_narrowing_never_a_widening():
    """A value the GENERIC filter rejects stays rejected even if the authored
    pattern would match it: the pattern removes false positives, it does not
    grant entry to prose."""
    assert not t0._looks_like_ref("see attachment 3", r".*3$")


def test_no_pattern_leaves_the_generic_filter_untouched():
    assert t0._looks_like_ref("1800") is t0._looks_like_ref("1800", None)


def test_ref_pattern_must_compile(tmp_path):
    # tests/test_playbooks.py
    _write(tmp_path, "bad.json", {"manufacturer": "BAD AG", "ref_pattern": "([unclosed"})

    assert playbooks.load_playbooks(tmp_path) == ()
```

- [x] **Step 2: Run to verify they fail**

Run: `$WTC --profile test run --rm test pytest tests/test_t0.py -k ref_pattern tests/test_playbooks.py -k ref_pattern -v`
Expected: FAIL — `TypeError: _looks_like_ref() takes 1 positional argument but 2 were given`

- [x] **Step 3: Implement**

In `app/playbooks.py`, add to `Playbook` immediately after `cert_number_pattern` (the field the previous task added — the five new keys land in task order, so append the validation block in `_parse` just above the `return Playbook(...)` too):

```python
    # Exact REF-code shape for this manufacturer, applied ON TOP of T0's generic
    # filter (`_looks_like_ref`) -- a narrowing only: a value the generic filter
    # already rejected stays rejected. Distinct from `ref_strategy_config.
    # field_pattern`, which selects a COLUMN for one parse strategy; this
    # validates a CODE for all of them.
    ref_pattern: str | None = None
```

and in `_parse`, beside the cert-pattern check:

```python
    ref_pattern = data.get("ref_pattern")
    if ref_pattern is not None:
        if not isinstance(ref_pattern, str):
            raise ValueError("ref_pattern must be a string")
        re.compile(ref_pattern)                      # raises on a bad pattern
```

then pass `ref_pattern=ref_pattern`.

In `app/extract/t0_templates.py`:

```python
def _looks_like_ref(val: str, pattern: str | None = None) -> bool:
    """Is this table cell a REF code? Values are kept verbatim (G5), so this
    only ever rejects — it never rewrites. The single choke point for all three
    strategies: `_ref_from_tables` here, plus `ref_from_stitched_tables` and
    `ref_from_text_columns` in `t0_layout`, which import it rather than
    redefining it.

    `pattern` is a playbook's authored `ref_pattern`, applied AFTER the generic
    tests and never instead of them: it can only narrow. Measured 2026-08-18,
    T0 read `ref_list` on 18% of corpus documents -- the worst field in the
    ladder, and the one an exact shape helps most."""
    if not re.search(r"\d", val) or len(val) > 30 or "\n" in val:
        return False
    if val.startswith(_UDI_PREFIX):
        return False
    if _PROSE_WORD.search(val):
        return False
    return re.search(pattern, val) is not None if pattern else True
```

Thread it through: `extract_ref_list(path, *, template=None, playbook=None)` passes
`playbook.ref_pattern if playbook else None` down to every `_looks_like_ref` call
site, including the two importers in `app/extract/t0_layout.py`
(`ref_from_stitched_tables`, `ref_from_text_columns`) — both gain a
`ref_pattern: str | None = None` keyword and forward it.

In `t0_extract`, pass it at the call:
`extract_ref_list(filename, template=template, playbook=steering)`.

**Second call site, added by the companion feature:** `_ref_from_companion`
(`app/extract/t0_templates.py:647`) calls `extract_ref_list(companion_url,
template=template)` for Komet's separate annex file. It needs the same
`playbook=` argument — the annex is the *same manufacturer's* document, so the
same REF shape applies, and omitting it would leave the annex path on the
generic filter while the primary path uses the authored one. Give
`_ref_from_companion(companion_url, playbook=None)` the parameter and forward it
from `t0_extract`.

- [x] **Step 4: Run to verify they pass**

Run: `$WTC --profile test run --rm test pytest tests/test_t0.py tests/test_t0_layout.py tests/test_playbooks.py -v`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add app/playbooks.py app/extract/t0_templates.py app/extract/t0_layout.py \
        tests/test_t0.py tests/test_t0_layout.py tests/test_playbooks.py
git commit -m "extract: an authored REF shape narrows T0's generic code filter"
```

---

### Task 6: Thread a hint block into T1 and T2

Plumbing only — no playbook key yet, no behaviour change. Split from Task 7 so the
transport-parity change can be reviewed on its own, which is where a silent
sync/batch divergence would hide.

**Files:**
- Modify: `app/extract/llm.py`
- Modify: `app/extract/tiers.py`
- Test: `tests/test_llm.py`

**Interfaces:**
- Produces:
  - `llm._params(tier, models, doc, missing, max_tokens, hints: str | None = None)`
  - `AnthropicTierClient.extract(tier, doc, filename, missing, hints=None)`
  - `AnthropicBatchClient.build_requests(content_hash, tier, doc, missing, hints=None)`
  - `tiers.run_extraction(doc, filename, llm, threshold=0.95, result=None, hints=None)`

- [x] **Step 1: Write the failing tests**

```python
# tests/test_llm.py -- reuses this file's existing `Models`, `StubClient`,
# `StubBatchSdk` and the `corpus_pdf` fixture with its TEXT_PDF constant.
def test_hints_ride_a_second_system_block_leaving_the_prompt_file_untouched(corpus_pdf):
    """T1_SYSTEM stays byte-identical so it remains a cacheable static prefix
    and the prompt file stays reviewable as prose."""
    doc = pdf.open_doc(corpus_pdf(TEXT_PDF))
    p = llm._params("T1", Models(), doc, ["validity_from"], 4096,
                    hints="REF codes are printed FIGURE.SHANK.SIZE.")

    assert isinstance(p["system"], list)
    assert p["system"][0]["text"] == llm.T1_SYSTEM
    assert "FIGURE.SHANK.SIZE" in p["system"][1]["text"]


def test_no_hints_leaves_the_request_exactly_as_it_was(corpus_pdf):
    """Every document without a playbook must produce the request T1 has always
    produced -- a plain string system prompt."""
    doc = pdf.open_doc(corpus_pdf(TEXT_PDF))
    p = llm._params("T1", Models(), doc, ["validity_from"], 4096)

    assert p["system"] == llm.T1_SYSTEM


def test_sync_and_batch_build_byte_identical_requests(corpus_pdf):
    """The one property that must never break: a divergence here is silent and
    unfixable after the fact. `build_requests` returns
    [{"custom_id": ..., "params": _params(...)}], so the comparison is exact."""
    doc = pdf.open_doc(corpus_pdf(TEXT_PDF))
    models, missing, hints = Models(), ["validity_to"], "A hint."

    sync = llm._params("T2", models, doc, missing,
                       llm.DEFAULT_MAX_TOKENS, hints=hints)
    batch = llm.AnthropicBatchClient(StubBatchSdk(), models).build_requests(
        "abc123", "T2", doc, missing, hints=hints)

    assert batch[0]["params"] == sync
```

- [x] **Step 2: Run to verify they fail**

Run: `$WTC --profile test run --rm test pytest tests/test_llm.py -k hints -v`
Expected: FAIL — `TypeError: _params() got an unexpected keyword argument 'hints'`

- [x] **Step 3: Implement**

In `app/extract/llm.py`:

```python
def _system(base: str, hints: str | None) -> str | list[dict]:
    """System prompt for one request.

    With no hints this returns the prompt string unchanged, so every document
    without a playbook produces byte-for-byte the request it always did. With
    hints it becomes two blocks: `base` FIRST and byte-identical (a stable,
    cacheable prefix and a prompt file that stays reviewable as prose), then the
    per-manufacturer block. The hints are never interpolated into the prompt
    file itself."""
    if not hints:
        return base
    return [
        {"type": "text", "text": base},
        {"type": "text", "text": hints},
    ]


def _params(tier: str, models, doc, missing: list[str], max_tokens: int,
            hints: str | None = None) -> dict:
    """The messages.create() body for a tier. Shared by the sync tier and the
    batch client so both submit byte-identical requests (only the transport
    differs). T1 = text content, T2 = page images."""
    if tier == "T1":
        model, system, content = models.t1, T1_SYSTEM, _text_content(doc, missing)
    else:
        model, system, content = models.t2, T2_SYSTEM, _vision_content(doc, missing)
    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": _system(system, hints),
        "messages": [{"role": "user", "content": content}],
        "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}},
    }
```

Add `hints: str | None = None` to `AnthropicTierClient.extract` and
`AnthropicBatchClient.build_requests`, forwarding it to `_params` in both.

In `app/extract/tiers.py`, add `hints: str | None = None` to `run_extraction` and
forward it on both `llm.extract(...)` calls.

- [x] **Step 4: Run to verify they pass**

Run: `$WTC --profile test run --rm test pytest tests/test_llm.py tests/test_batching.py tests/test_extract_handler.py tests/test_recorded_llm.py -v`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add app/extract/llm.py app/extract/tiers.py tests/test_llm.py
git commit -m "extract: carry an optional hint block into the T1/T2 request"
```

---

### Task 7: `extract_hints` — the playbook key and its four guards

The payload of the whole plan, and the only task that can make an extraction
*worse*. All four guards ship together; none is optional.

**Files:**
- Modify: `app/playbooks.py`
- Modify: `app/handlers/extract.py`
- Modify: `playbooks/README.md`
- Test: `tests/test_extract_handler.py`, `tests/test_playbooks.py`

**Interfaces:**
- Consumes: Task 1 (`rev`), Task 2 (`steering_playbook`), Task 6 (`hints=`).
- Produces: `Playbook.extract_hints: dict` (`{field_or_"general": str}`); `extract._hints_for(playbook, missing, authoritative: bool) -> str | None`.

**The four guards** (spec §6, con 1):

1. **Never assert identity.** The hint block is prefixed with an explicit override instruction.
2. **Never hint a field that is itself being asked.** If `manufacturer` is in `missing`, the `manufacturer` hint is withheld — hinting the answer to the question is the circularity.
3. **Weaken a guessed manufacturer.** With no `group_id` the manufacturer is T0's guess; the block says so.
4. **Record what steered it.** `playbook_slug`/`playbook_rev` (Task 1) go onto the attempt whenever a hint was injected.

- [x] **Step 1: Write the failing tests**

```python
# tests/test_playbooks.py
def test_extract_hints_parse(tmp_path):
    _write(tmp_path, "komet.json", {
        "manufacturer": "KOMET", "rev": 1,
        "extract_hints": {"ref_list": "Codes are FIGURE.SHANK.SIZE."},
    })

    (pb,) = playbooks.load_playbooks(tmp_path)

    assert pb.extract_hints["ref_list"] == "Codes are FIGURE.SHANK.SIZE."


def test_extract_hints_must_be_an_object_of_strings(tmp_path):
    _write(tmp_path, "bad.json", {"manufacturer": "BAD AG",
                                  "extract_hints": {"ref_list": ["a", "b"]}})

    assert playbooks.load_playbooks(tmp_path) == ()
```

```python
# tests/test_extract_handler.py
from app.handlers import extract as eh
from app.playbooks import Playbook

KOMET = Playbook(slug="komet", manufacturer="KOMET", rev=2, extract_hints={
    "ref_list": "Codes are FIGURE.SHANK.SIZE.",
    "manufacturer": "Gebr. Brasseler is the legal entity.",
})


def test_guard_1_every_hint_block_carries_an_override_instruction():
    """A hint must tell the model to DOUBT, never to confirm. Without this
    sentence a wrong T0 identity gets laundered into a 0.95 confidence."""
    out = eh._hints_for(KOMET, ["ref_list"], authoritative=True)

    assert "ignore" in out.lower()
    assert "document" in out.lower()


def test_guard_2_a_field_being_asked_is_never_itself_hinted():
    """Hinting the answer to the question is the circularity. `ref_list` is
    asked, so its hint is withheld; nothing else is."""
    out = eh._hints_for(KOMET, ["ref_list", "validity_to"], authoritative=True)

    assert "FIGURE.SHANK.SIZE" not in out
    assert "Gebr. Brasseler" in out


def test_guard_3_a_guessed_manufacturer_says_so():
    """group_id is None (every backfilled document): the manufacturer is T0's
    guess, and the prompt must not present it as established."""
    out = eh._hints_for(KOMET, ["validity_to"], authoritative=False)

    assert "may" in out.lower() or "appears" in out.lower()


def test_no_playbook_means_no_hint_block_at_all():
    assert eh._hints_for(None, ["validity_to"], authoritative=True) is None


def test_a_playbook_with_no_hints_authored_produces_no_block():
    bare = Playbook(slug="voco", manufacturer="VOCO")

    assert eh._hints_for(bare, ["validity_to"], authoritative=True) is None


def test_withholding_every_applicable_hint_produces_no_block():
    """All hints suppressed by guard 2 must yield None, not a block containing
    only the override boilerplate -- that would be tokens for nothing."""
    only_ref = Playbook(slug="k", manufacturer="K",
                        extract_hints={"ref_list": "Codes are dotted."})

    assert eh._hints_for(only_ref, ["ref_list"], authoritative=True) is None


def test_guard_4_a_hinted_extraction_records_the_playbook_revision(
        conn, fixture_pdf, monkeypatch, tmp_path):
    """Invariant 2: the evidence must name the rules that shaped it. Uses a real
    committed GC fixture and a playbooks dir containing only GC, so the steering
    playbook is unambiguous."""
    from app import playbooks as pb_mod

    (tmp_path / "gc.json").write_text(json.dumps({
        "manufacturer": "GC EUROPE N.V.", "rev": 4,
        "extract_hints": {"general": "Dates are written DD/MM/YYYY."},
    }))
    monkeypatch.setattr(pb_mod, "PLAYBOOKS_DIR", tmp_path)

    path = fixture_pdf("GC/Fuji_Coat_LC_12022026.pdf")
    content_hash = "gc-fixture-hash"
    job = {"id": 1, "payload": {"content_hash": content_hash,
                                "archive_url": path, "group_id": None}}

    eh.handle_extract_doc(conn, job, llm=_NoEscalationLLM())

    row = conn.execute(
        "SELECT playbook_slug, playbook_rev FROM extraction_attempt "
        "WHERE content_hash=%s", (content_hash,),
    ).fetchone()
    assert (row["playbook_slug"], row["playbook_rev"]) == ("gc", 4)


class _NoEscalationLLM:
    """Asserts the ladder never reaches a paid tier for this fixture, so the
    test measures provenance recording and not LLM behaviour."""

    usage_log = ()

    def extract(self, tier, doc, filename, missing, hints=None):
        raise AssertionError(f"unexpected {tier} call for {missing}")
```

Add `import json` to the test file if absent.

- [x] **Step 2: Run to verify they fail**

Run: `$WTC --profile test run --rm test pytest tests/test_extract_handler.py -k guard tests/test_playbooks.py -k extract_hints -v`
Expected: FAIL — `AttributeError: module 'app.handlers.extract' has no attribute '_hints_for'`

- [x] **Step 3: Implement the key**

In `app/playbooks.py`, add to `Playbook` immediately after `ref_pattern` (the field the previous task added — the five new keys land in task order, so append the validation block in `_parse` just above the `return Playbook(...)` too):

```python
    # Free-text, per-field guidance appended to the T1/T2 system prompt.
    # Keys are field names, or "general". This is the ONLY playbook value an
    # LLM ever sees; every other key drives deterministic code.
    extract_hints: dict = field(default_factory=dict)
```

and in `_parse`:

```python
    extract_hints = data.get("extract_hints") or {}
    if not isinstance(extract_hints, dict):
        raise ValueError("extract_hints must be an object")
    if not all(isinstance(v, str) for v in extract_hints.values()):
        raise ValueError("every extract_hints value must be a string")
```

then pass `extract_hints=extract_hints`.

- [x] **Step 4: Implement the guards**

In `app/handlers/extract.py`:

```python
#: Guard 1. Prefixed to every hint block. A hint is a prior, not a fact: the
#: manufacturer behind it is T0's own text match on all but the fetch path, and
#: a hint that ASSERTS identity turns a wrong guess into a confident wrong
#: answer. This sentence is what makes the block safe to send at all.
_HINT_OVERRIDE = (
    "The notes below describe how one manufacturer usually writes its "
    "documents. They are context, not fact. If this document disagrees with "
    "them in any way — a different company, a different format — ignore them "
    "entirely and report only what the document itself says."
)


def _hints_for(playbook, missing: list[str], authoritative: bool) -> str | None:
    """The hint block for this request, or None.

    Guard 2: a field in `missing` is a field being ASKED, so its own hint is
    withheld — telling the model what to answer is the circularity that makes
    hints dangerous. Other fields' hints still apply.

    Guard 3: `authoritative` is True only when the manufacturer came from the
    requesting group (established before extraction). False means T0 guessed it
    from the document text, and the block hedges accordingly.

    Returns None when nothing survives, rather than a block of boilerplate with
    no content in it."""
    if not playbook or not playbook.extract_hints:
        return None
    asked = set(missing)
    applicable = {k: v for k, v in playbook.extract_hints.items()
                  if k == "general" or k not in asked}
    if not applicable:
        return None

    if authoritative:
        lead = f"This document belongs to {playbook.manufacturer}."
    else:
        lead = (f"This document may be from {playbook.manufacturer} — that is "
                f"inferred from the text, not established.")

    body = "\n".join(f"- {k}: {v}" for k, v in sorted(applicable.items()))
    return f"{_HINT_OVERRIDE}\n\n{lead}\n\n{body}"
```

Then thread it. In `handle_extract_doc`, directly after the `r = Result()` line
and before the transport branch:

```python
    # The steering playbook, resolved ONCE per execution. `group_id` is the
    # authoritative source when present (the group's manufacturer was
    # established before extraction); every backfilled document has none, so
    # T0's own text match is the live path -- hence `authoritative` is what the
    # hint block hedges on, not a reason to skip hinting entirely.
    steering = t0_templates.steering_playbook(
        "\n".join(pdf.page_texts(doc)),
        override=(playbooks_mod.for_manufacturer(
            archiving.manufacturer_for_group(conn, group_id))
            if group_id is not None else None),
    )
    hints = _hints_for(steering, tiers.needs_escalation({}), authoritative=group_id is not None)
```

...where `tiers.needs_escalation({})` is the full pursued-field list (nothing is
extracted yet at this point, so every field is "asked" — guard 2 at its most
conservative). Pass `hints=hints` and `steering=steering` into `_handle_sync` /
`_handle_batch`, which forward `hints` to `tiers.run_extraction` /
`batch_client.build_requests` and `steering` to `_finalize`. Both handlers
already take a trailing `companion_url=None` (companion feature) — add the two
new keywords after it.

In `_finalize`, add the two parameters and record them only when a block was
actually built:

```python
def _finalize(conn, content_hash, group_id, doc_class, tiers_used, fields,
              usage_log=(), archive_url=None, steering=None, hinted=False) -> dict:
    ...
    rev = tiers.next_extract_rev(conn, content_hash)
    tiers.write_extraction_attempt(
        conn, content_hash, tiers_used, fields, extract_rev=rev,
        playbook_slug=steering.slug if steering else None,
        playbook_rev=steering.rev if steering else None,
    )
```

The slug is recorded whenever a playbook steered ANY part of the read — T0
lexicons count, not only hints — because Tasks 3-5 make T0 itself
playbook-dependent. `hinted` rides the job result for the KPI board, not the
attempt row.

Add the imports `t0_templates`, `playbooks as playbooks_mod` and `archiving` to
`app/handlers/extract.py` if absent.

- [x] **Step 5: Run to verify they pass**

Run: `$WTC --profile test run --rm --build test`
Expected: PASS.

- [ ] **Step 6: Measure before authoring a single real hint**

> **Still open, 2026-08-21.** The only unchecked step in this plan. No
> `playbooks/*.json` authors an `extract_hints` key yet, so there is nothing to
> measure and no hint has shipped. The machinery from Steps 1-5 is live but inert.

**No hint ships unmeasured.** Author one hint on one playbook, then run the
corpus diff — the same protocol a tier swap requires:

```bash
docker compose exec -T postgres psql -U dentalia -d dentalia -tAF' | ' -c "
SELECT f.key AS field, f.value->>'tier' AS tier, count(*)
FROM extraction_attempt a, jsonb_each(a.fields) f
WHERE a.playbook_slug = '<slug>'
  AND jsonb_typeof(f.value)='object' AND f.value ? 'tier'
GROUP BY 1,2 ORDER BY 1,3 DESC;"
```

Run it before authoring the hint, re-extract that manufacturer's documents
(`app/repair_ref_list.py`'s pattern: a repair lands as a NEW `extract_rev` and
never overwrites), run it again, and diff the T0/T1/T2 split. A hint that does not move a field back down the ladder is a hint
that costs tokens for nothing; a hint that moves `manufacturer` is a hint to
look at very hard before keeping.

- [x] **Step 7: Document the key**

Add to `playbooks/README.md` — a `## Fields` row plus its own section, because
this is the one key with a real trap:

```markdown
| `extract_hints` | no | Free-text guidance appended to the T1/T2 prompt. The ONLY playbook value an LLM ever sees. See below. |
```

```markdown
## `extract_hints` — the only key an LLM reads

Every other key drives deterministic code. This one is sent to a model, so it
carries risks none of the others do.

```json
"extract_hints": {
  "ref_list": "REF codes on this manufacturer's declarations are ISO 6360 bur codes printed FIGURE.SHANK.SIZE (e.g. H1.314.006). Return them exactly as printed; do not reorder.",
  "general": "..."
}
```

Rules:

- **Describe the document, never the answer.** "Dates are written DD.MM.YYYY" is
  a hint. "The expiry is usually 2030" is a defect.
- A hint for a field currently being asked is **withheld automatically**
  (guard 2) — you cannot use this to answer a question for the model.
- Every block is prefixed with an override instruction telling the model to
  ignore the hint if the document disagrees. That is what makes a hint from a
  *guessed* manufacturer safe.
- **Bump `rev` when you change a hint.** The revision is recorded on every
  extraction it steered (`extraction_attempt.playbook_rev`); without the bump,
  two different extractions claim the same provenance.
- **Measure before keeping one.** Run the corpus diff for that manufacturer. A
  hint that moves nothing is tokens for nothing.
```

- [x] **Step 8: Commit**

```bash
git add app/playbooks.py app/handlers/extract.py playbooks/README.md \
        tests/test_extract_handler.py tests/test_playbooks.py
git commit -m "extract: per-manufacturer hints for T1 and T2, behind four guards"
```

---

### Task 8: Docs

**Files:**
- Modify: `docs/code-map.md`
- Modify: `docs/architecture.md`
- Modify: `docs/dentalia-schema-sketch.md`

- [x] **Step 1: `docs/code-map.md`**

Extend the `app/playbooks.py` row (line 14) with the new keys: `rev`,
`date_labels`, `cert_number_pattern`, `ref_pattern`, `extract_hints` — noting
that `extract_hints` is the only one an LLM sees and the rest drive T0.

Extend the `app/extract/t0_templates.py` row with `steering_playbook` and the
fact that the date/cert/REF extractors now take an optional playbook whose
lexicons MERGE with the globals.

- [x] **Step 2: `docs/architecture.md`**

In the **Extraction tier ladder** section (line 84), add that T0's lexicons are
playbook-extendable and that T1/T2 receive an optional per-manufacturer hint
block as a second system message, with the four guards named.

- [x] **Step 3: `docs/dentalia-schema-sketch.md`**

Add `playbook_slug` / `playbook_rev` to the `extraction_attempt` table
description, with the one-line reason: evidence must name the rules that shaped
it, and `extract_rev` does not.

- [x] **Step 4: Full suite + vocabulary guard**

Run: `$WTC --profile test run --rm --build test`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add docs/code-map.md docs/architecture.md docs/dentalia-schema-sketch.md
git commit -m "docs: playbook-steered extraction and its provenance columns"
```

---

## What this does not cover

| Spec item | Where it goes |
|---|---|
| 1, 2, 3, 4 (cache, source_priority, direct sources, fetch_policy) | `2026-08-18-playbook-fetch-reachability.md` — written, verified, shelved until DISCOVER runs |
| 11 (crawl recipe) | needs its own spec; depends on the fetch plan |
| 12 (`playbook.reonboard`) | depends on Task 1's `rev` |
| 6, 10, 13 (corpus_folders, mfr_ref_pattern, gate.deny_auto) | small singles, batch into whichever plan lands next |
| 14–18 | refused in the spec, with reasons |

**Loader consolidation** (spec §9.2 — `load_playbooks` and `t0_layout.load_templates` parsing the same file independently) is deliberately NOT in this plan. It is a refactor with no measured payoff, and this plan adds five keys to only one of the two loaders' view, so it does not make the divergence worse. Revisit when a key needs to cross the boundary.
