# EUDAMED Enhancement Path Implementation Plan

**Regenerated 2026-08-26.** The 2026-08-25 plan is superseded in full: the
brainstorm of 2026-08-26 inverted certificate acquisition, restored the
"no unattended sweep" ruling, and added two findings. Nine of its eleven tasks
changed at the root, so it was rebuilt rather than patched.

**Goal:** Use EUDAMED to detect certificate revision drift, adverse certificate
status, certificates we hold no copy of, and declarations we should be
requesting — as an enhancement path that nothing in the pipeline depends on.

**Architecture:** Two new queue tags. `eudamed.certregister` pulls the entire
4,608-row EU certificate register in 16 calls and attributes actors to our
canonical manufacturers locally; `eudamed.sweep` walks one manufacturer's
registered device catalogue per SRN and is released by a human, never by the
scheduler. All four findings are Postgres **views** over registry + mirror, never
stored rows. No registry column is written anywhere in this plan.

**Tech Stack:** Python 3.12 sync, psycopg 3 raw SQL, numbered `.sql` migrations
applied by `app/db.py`, FastAPI + Jinja + HTMX, rapidfuzz, pytest against a real
Postgres.

**Spec:** `docs/superpowers/specs/2026-08-25-eudamed-enhancement-design.md`
(revised 2026-08-26 — read the revision, not a cached memory of the draft).

## Global Constraints

- **Invariant 1.** No task writes `document`, `item_document` or `evidence`. Every handler task ends with a test asserting the row counts of all three are unchanged.
- **Invariant 7.** Job types are a closed enum. New tags arrive by migration; never a runtime string.
- **Invariant 11.** All HTTP through `app.adapters.fetcher.make_fetcher()`. Never `httpx` directly, never `urllib`.
- **Invariant 12.** No LLM call anywhere in this plan. Name matching is rapidfuzz, not a model.
- **Standing ruling, 2026-08-19 (Denis), `app/handlers/validate.py:711-745`:** a trailing R-code (` R000`, ` R7`) is stripped from certificate numbers; **`Rev. NN` is deliberately left alone.** No task may widen `_RCODE_SUFFIX`. Revision differences are *reported*, never resolved.
- **Ruling, 2026-08-26 (Denis):** the drift view **joins on baseline only**. Looser normalisations surface as a `possible_match` column for a human to confirm, never as a join. Join reach 107 of 391 documents; visibility 145.
- **Ruling, 2026-08-26 (Denis):** **no device sweep runs unattended.** The scheduler marks a manufacturer due; a person releases each run. `eudamed.certregister` is carved out and runs on the tick. Cadence: certificates monthly, device sweeps quarterly.
- **Ruling, 2026-08-25 (Denis):** EUDAMED is an enhancement, never a main path. `ref-eudamed` stays capped at `staged` and no task creates one.
- **Ruling, 2026-08-20 (Denis), `[manufacturer-contacts-empty]`:** seed `manufacturer` identity from the playbooks + `manufacturer_alias`; contacts live in the database, crawl URLs stay in the playbooks; **a playbook never contains an email address, the database never contains a crawl URL.** Task 2 does the seeding half, for all 384 canonical names (Denis, 2026-08-26).
- **`ALTER TYPE job_type ADD VALUE` cannot be used in the same transaction that uses the new value** (PG16). The migration runner applies each file in one transaction. Precedent and wording: `migrations/013_scheduler.sql:6-10`.
- **Migration numbering:** run `ls migrations/ | tail -3` before creating any file, every time. The last number was 039 at 11:00 on 2026-08-26 and **040 (`040_import_inbox.sql`) was claimed by another session within the hour**. Treat any number written in this plan as stale on sight; the `ls` is the only authority.
- **Test selection** (CLAUDE.md): `migrations/` touched → **full suite**, run as `./scripts/test.sh`. `--dist loadfile` is not optional. Every task adding a migration ends with a full-suite run; say which ran.
- **The API is undocumented.** Unknown query parameters are *silently ignored* and return the unfiltered 3.25M-record set rather than erroring. Confirmed filtering: `basicUdi`, `srn`, `reference`, `primaryDi`, `tradeName`, `riskClassCode`, `actorSrn`, `actorName`. Confirmed non-filtering: `manufacturerName`, `manufacturerSrn`, `deviceName`, `nomenclatureCode`. Max page size **300**.
- **No test may reach `ec.europa.eu`.** Every handler test uses the `FakeFetcher` already in `tests/test_eudamed_handler.py`.
- **Commit messages:** plain imperative subject, no AI/Claude attribution of any kind.

---

## File Structure

| File | Responsibility |
|---|---|
| `migrations/0NN_eudamed_job_types.sql` | **Only** the two `ALTER TYPE` statements. Nothing else. |
| `migrations/0NN_eudamed_manufacturer.sql` | Seed `manufacturer` (384); `manufacturer.srn_probed_at`; create `manufacturer_srn`, `eudamed_certificate`; grants. |
| `migrations/0NN_eudamed_cert_views.sql` | `certificate_drift`, `certificate_status_alert`, `certificate_gap`; grants. |
| `migrations/0NN_eudamed_sweep_state.sql` | `eudamed_mirror.first_seen` + `.device_status_type`; `eudamed_sweep_state`; grants. |
| `migrations/0NN_eudamed_gap_view.sql` | `eudamed_declaration_gap`; grants. |
| `app/eudamed_names.py` | **New, pure.** Actor-name normalisation and canonical-manufacturer matching. No IO, no database, no fetcher — so it is testable at speed and reusable by the confirm queue. |
| `app/handlers/eudamed.py` | Existing. Gains `handle_eudamed_certregister`, `handle_eudamed_sweep`, `srns_for`. All three share the paging walk and `_assert_filtered` already there. |
| `app/handlers/noop.py` | `JOB_TYPES` gains the two tags (16 → 18). |
| `app/handlers/email_request.py` | `compose()` gains the drift line. |
| `app/scheduler.py` | `_tick_eudamed_certregister` (emits), `_tick_eudamed_sweep_due` (marks due, emits nothing), both registered in `tick()`. |
| `app/config.py` | `Scheduler.eudamed_certregister_enabled`, `.eudamed_certregister_interval_days`, `.eudamed_sweep_interval_days`. |
| `web/registry.py` | `srn_confirm_rows`, `certificate_findings`, `declaration_gap_summary`, `sweep_due_rows`, and the POST routes for confirm/reject and sweep release. The manufacturer page route is registered here (`register_routes`, `web/registry.py:565`), not in `web/app.py`. |
| `web/app.py` | `/expiry` (`web/app.py:2345`) gains a certificate-findings section. Nothing else. |
| `web/templates/_ui.html` | `cert_findings()`, `srn_confirm()`, `sweep_due()`, `eudamed_gap()` macros. |
| `tests/test_eudamed_names.py` | **New.** Normalisation and matching, table-driven, no database. |
| `tests/test_eudamed_handler.py` | Existing, 29 tests. Gains certregister, sweep, bootstrap. |
| `tests/test_eudamed_views.py` | **New.** View semantics, table-driven. |
| `tests/test_scheduler.py` | Existing. Gains the two tick tests. |
| `tests/test_web.py` | Existing. Gains the render and POST tests. |
| `tests/test_email_request.py` | Existing. Gains the drift-line test. |

Everything for the handlers stays in `app/handlers/eudamed.py`: the paging walk,
the filter-integrity guard and the URL constants are already there and all three
job shapes need them. Splitting by tag would duplicate the walk. Name matching
is the exception — it is pure, has no reason to know about HTTP or Postgres, and
two callers need it.

---

## Task 1: Add the two job types

**Files:**
- Create: `migrations/0NN_eudamed_job_types.sql`
- Modify: `app/handlers/noop.py`
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: nothing.
- Produces: the enum values `'eudamed.certregister'` and `'eudamed.sweep'`, usable by every later task. `noop.JOB_TYPES` becomes an 18-tuple.

- [ ] **Step 1: Write the failing test**

In `tests/test_runner.py`, extend the existing unbuilt-stage test. It currently reads:

```python
    unbuilt = ["playbook.reonboard"]
```

Change it to:

```python
    # email.request / email.reminder left this list on 2026-08-20 when the S2.4
    # outbound handlers landed (app/handlers/email_request.py); eudamed.sync on
    # 2026-08-25 (app/handlers/eudamed.py). eudamed.certregister and
    # eudamed.sweep enter it here and leave it in tasks 4 and 10.
    unbuilt = ["eudamed.certregister", "eudamed.sweep", "playbook.reonboard"]
```

and in `test_every_built_stage_still_handles_without_raising`:

```python
    still_placeholder = [t for t in noop.JOB_TYPES if HANDLERS[t] is noop._noop]
    assert sorted(still_placeholder) == [
        "eudamed.certregister", "eudamed.sweep", "playbook.reonboard",
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./scripts/test.sh tests/test_runner.py`
Expected: FAIL — `KeyError: 'eudamed.certregister'`, because the tag is not in `JOB_TYPES` yet.

- [ ] **Step 3: Write the migration**

Check the next free number first: `ls migrations/ | tail -3`.

`migrations/0NN_eudamed_job_types.sql`:

```sql
-- Two new queue tags for the EUDAMED enhancement path
-- (docs/superpowers/specs/2026-08-25-eudamed-enhancement-design.md §4).
--
-- `eudamed.certregister` takes no manufacturer: the whole register is pulled in
-- 16 calls and matched locally, because actorSrn arrives on every certificate
-- record, so SRN discovery is a by-product of the pull rather than a
-- prerequisite for it (measured 2026-08-26).
--
-- ALTER TYPE ... ADD VALUE cannot be used in the same transaction that also
-- USES the new value (PG16), and the migration runner applies each file inside
-- one transaction. Migration 013 hit this and documents it. So this file adds
-- the values and NOTHING else -- the tables that reference them ship in the
-- next file, and nothing here inserts a job row.

ALTER TYPE job_type ADD VALUE 'eudamed.certregister';
ALTER TYPE job_type ADD VALUE 'eudamed.sweep';
```

- [ ] **Step 4: Extend the enum in code**

In `app/handlers/noop.py`, add to `JOB_TYPES` after `'eudamed.sync'`:

```python
    "eudamed.sync",
    "eudamed.certregister",
    "eudamed.sweep",
```

and update the module docstring's count from "all 16 job types" to "all 18 job types".

- [ ] **Step 5: Apply the migration and run the full suite**

```bash
docker compose build migrate && docker compose run --rm migrate
./scripts/test.sh
```

Expected: `applied 1 migration(s)`, then all tests pass. `migrations/` was touched, so the full suite is required — say so in the commit report.

- [ ] **Step 6: Commit**

```bash
git add migrations/0NN_eudamed_job_types.sql app/handlers/noop.py tests/test_runner.py
git commit -m "eudamed: add the certregister and sweep queue tags"
```

---

## Task 2: Manufacturer identity tables

**Files:**
- Create: `migrations/0NN_eudamed_manufacturer.sql`
- Test: `tests/test_eudamed_views.py` (new file, schema assertions only in this task)

**Interfaces:**
- Consumes: the enum values from Task 1 (not used here, but this file must come after).
- Produces: `manufacturer` rows keyed by `canonical_name` plus `manufacturer.srn_probed_at`; tables `manufacturer_srn(canonical_name, srn, actor_name, discovered_via, status, match_score, probe_ref, first_seen, decided_at, decided_by)` and `eudamed_certificate(certificate_number, revision_number, actor_srn, actor_name, certificate_type, certificate_status, issue_date, starting_validity, expiry_date, notified_body_srn, version_number, first_seen, synced_at)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_eudamed_views.py`:

```python
"""EUDAMED enhancement path — the identity tables and the four views.

Spec: docs/superpowers/specs/2026-08-25-eudamed-enhancement-design.md

`manufacturer` was created by migration 006 as a *declared, not built* Phase 2
table and held 0 rows until this work (re-verified 2026-08-26). Seeding it here
makes it shared infrastructure -- `email.request` reads it for contacts, which
is why `email_request._contacts()` returns nothing today and the renewal mail
has no recipients -- not an EUDAMED detail.
"""

from __future__ import annotations


def test_manufacturer_is_seeded_from_the_alias_table(conn):
    """One row per canonical name, so a foreign key from manufacturer_srn has
    something to point at. All 384, not only the nine with device articles
    (Denis, 2026-08-26): probing every supplier is a cross-check of our own
    medical-device flag, not wasted effort."""
    aliased = conn.execute(
        "SELECT count(DISTINCT canonical_name) c FROM manufacturer_alias"
    ).fetchone()["c"]
    seeded = conn.execute("SELECT count(*) c FROM manufacturer").fetchone()["c"]
    assert seeded == aliased
    assert seeded > 0


def test_a_probe_that_found_nothing_is_distinguishable_from_no_probe(conn):
    """Without `srn_probed_at`, "probed, no SRN exists" and "never probed" are
    the same null, and the article-probe fallback re-runs against every
    unregistered supplier forever."""
    conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES ('NEVERPROBED') "
        "ON CONFLICT DO NOTHING"
    )
    conn.execute(
        "INSERT INTO manufacturer (canonical_name, srn_probed_at) "
        "VALUES ('PROBEDEMPTY', now()) ON CONFLICT DO NOTHING"
    )
    rows = {
        r["canonical_name"]: r["srn_probed_at"]
        for r in conn.execute(
            "SELECT canonical_name, srn_probed_at FROM manufacturer "
            "WHERE canonical_name IN ('NEVERPROBED','PROBEDEMPTY')"
        ).fetchall()
    }
    assert rows["NEVERPROBED"] is None
    assert rows["PROBEDEMPTY"] is not None


def test_one_manufacturer_may_hold_several_srns(conn):
    """`manufacturer_alias` already maps BC codes 001 and 005 both to IVOCLAR:
    our canonical name is a Dentalia-side grouping, not a legal entity, and a
    multinational registers per legal entity. One SRN per canonical name would
    report articles as unregistered when a sibling entity holds them -- and that
    false alarm becomes a wrong e-mail to a supplier."""
    conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES ('TESTCO') "
        "ON CONFLICT DO NOTHING"
    )
    for srn in ("LI-MF-000000522", "DE-MF-000000999"):
        conn.execute(
            "INSERT INTO manufacturer_srn "
            "(canonical_name, srn, discovered_via, status) "
            "VALUES ('TESTCO', %s, 'register-exact', 'auto')", (srn,)
        )
    n = conn.execute(
        "SELECT count(*) c FROM manufacturer_srn WHERE canonical_name='TESTCO'"
    ).fetchone()["c"]
    assert n == 2


def test_an_srn_candidate_defaults_to_pending(conn):
    """Only `auto` and `confirmed` rows are swept. A candidate that arrives
    without an explicit status must not be swept by accident."""
    conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES ('DEFAULTCO') "
        "ON CONFLICT DO NOTHING"
    )
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via) "
        "VALUES ('DEFAULTCO', 'XX-MF-000000001', 'register-fuzzy')"
    )
    row = conn.execute(
        "SELECT status FROM manufacturer_srn WHERE canonical_name='DEFAULTCO'"
    ).fetchone()
    assert row["status"] == "pending"


def test_several_revisions_of_one_certificate_coexist(conn):
    """EUDAMED serves each revision as its own record -- Carl Martin's
    `HZ 1594091-1` comes back twice, `Rev. 1` expiring 2026-06-29 and `Rev. 2`
    running to 2031-06-29. A key that collapsed them would destroy the signal
    this work exists for. The key (number, revision, actor) was tested against
    all 4.608 register rows on 2026-08-26 and collides zero times."""
    for rev, expiry in (("Rev. 1", "2026-06-29"), ("Rev. 2", "2031-06-29")):
        conn.execute(
            "INSERT INTO eudamed_certificate "
            "(certificate_number, revision_number, actor_srn, expiry_date, "
            " synced_at) "
            "VALUES ('HZ 1594091-1', %s, 'DE-MF-000005066', %s, now())",
            (rev, expiry)
        )
    n = conn.execute(
        "SELECT count(*) c FROM eudamed_certificate "
        "WHERE certificate_number='HZ 1594091-1'"
    ).fetchone()["c"]
    assert n == 2


def test_a_certificate_without_a_revision_still_stores(conn):
    """`revisionNumber` is null on 481 of 4.608 records (measured 2026-08-26).
    The empty-string default is what keeps those in a NOT NULL primary key."""
    conn.execute(
        "INSERT INTO eudamed_certificate "
        "(certificate_number, actor_srn, synced_at) "
        "VALUES ('Z-25-052-S-IX-E', 'DE-MF-000006413', now())"
    )
    row = conn.execute(
        "SELECT revision_number FROM eudamed_certificate "
        "WHERE certificate_number='Z-25-052-S-IX-E'"
    ).fetchone()
    assert row["revision_number"] == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./scripts/test.sh tests/test_eudamed_views.py`
Expected: FAIL — `UndefinedTable: relation "manufacturer_srn" does not exist`, and the seeding test fails with `0 != 384`.

- [ ] **Step 3: Write the migration**

`migrations/0NN_eudamed_manufacturer.sql`:

```sql
-- Manufacturer identity for the EUDAMED enhancement path
-- (docs/superpowers/specs/2026-08-25-eudamed-enhancement-design.md §3).
--
-- `manufacturer` was created by 006 as a declared-not-built Phase 2 table and
-- nothing has ever written it: 0 rows, re-verified 2026-08-26. This is its
-- first writer. It is shared infrastructure, not an EUDAMED detail -- 006's own
-- comment says email.request reads it for contacts, and that is why
-- email_request._contacts() returns nothing today.
--
-- Seeding it is NOT a choice this migration makes. Denis, 2026-08-20
-- (tasks/followups.md [manufacturer-contacts-empty]): "Two jobs: seed
-- `manufacturer` identity from the playbooks + `manufacturer_alias`, and ask
-- the client for the regulatory contact per supplier." This is the first job.
-- All 384 canonical names, not only the nine with device articles (Denis,
-- 2026-08-26): probing every supplier cross-checks our own medical-device flag.

INSERT INTO manufacturer (canonical_name)
SELECT DISTINCT canonical_name FROM manufacturer_alias
ON CONFLICT (canonical_name) DO NOTHING;

-- Without this, "probed, no SRN exists" and "never probed" are the same null,
-- and the article-probe fallback re-runs against every unregistered supplier
-- forever. ~375 of the 384 are not device manufacturers and will never resolve.
ALTER TABLE manufacturer ADD COLUMN IF NOT EXISTS srn_probed_at timestamptz;

COMMENT ON COLUMN manufacturer.srn_probed_at IS
  'When the article-probe SRN fallback last ran for this manufacturer. NULL '
  'means never probed; a timestamp with no manufacturer_srn row means probed '
  'and nothing found. Do not conflate the two.';

-- One canonical name, many EUDAMED entities. `manufacturer_alias` maps BC
-- codes 001 AND 005 both to IVOCLAR, so our canonical name is a Dentalia-side
-- grouping and a multinational registers per legal entity. Storing one SRN and
-- sweeping it would report articles as unregistered when a sibling entity
-- holds them, and that false alarm becomes a wrong e-mail to a supplier.
--
-- `manufacturer.eudamed_srn` is deliberately left alone -- not repurposed, not
-- dropped. A column no code has ever written is not evidence of an intent.
CREATE TABLE manufacturer_srn (
  canonical_name text NOT NULL REFERENCES manufacturer(canonical_name),
  srn            text NOT NULL,
  actor_name     text,
  -- How this SRN was found. 'register-exact' and 'register-fuzzy' come from
  -- the whole-register pull; 'article-probe' from the device fallback.
  discovered_via text NOT NULL
    CHECK (discovered_via IN ('register-exact','register-fuzzy','article-probe')),
  -- Only 'auto' and 'confirmed' are swept. Attribution is a fuzzy name match
  -- and a wrong one produces a wrong e-mail to a supplier, so anything short
  -- of a normalised exact match waits for a person (spec §4.5).
  status         text NOT NULL DEFAULT 'pending'
    CHECK (status IN ('auto','pending','confirmed','rejected')),
  match_score    real,
  -- The article number, when discovered_via='article-probe'. A wrong bootstrap
  -- is then auditable rather than mysterious (spec §5.2).
  probe_ref      text,
  first_seen     timestamptz NOT NULL DEFAULT now(),
  decided_at     timestamptz,
  decided_by     text,
  PRIMARY KEY (canonical_name, srn)
);

CREATE INDEX manufacturer_srn_status_idx ON manufacturer_srn (status);

-- `eudamed_mirror` is device-shaped and cannot hold these. Keyed on
-- (number, revision, actor): EUDAMED serves each revision as its own record,
-- and that key was tested against all 4.608 register rows on 2026-08-26 with
-- zero collisions. (actor, number, versionNumber) collides 50 times -- do not
-- use it.
CREATE TABLE eudamed_certificate (
  certificate_number text NOT NULL,
  revision_number    text NOT NULL DEFAULT '',
  actor_srn          text NOT NULL,
  actor_name         text,
  certificate_type   text,
  -- Nine values, measured 2026-08-26: issued 2770, supplemented 1007,
  -- amended 422, reissued 197, withdrawn 87, cancelled 68, restricted 31,
  -- suspended 18, reinstated 8. The adverse four drive certificate_status_alert.
  certificate_status text,
  issue_date         date,
  starting_validity  date,
  expiry_date        date,
  notified_body_srn  text,
  version_number     integer,
  -- NEVER touched by ON CONFLICT. A new revision is a new row, so a preserved
  -- first_seen is what makes "a renewal appeared" detectable at all.
  first_seen         timestamptz NOT NULL DEFAULT now(),
  synced_at          timestamptz NOT NULL,
  PRIMARY KEY (certificate_number, revision_number, actor_srn)
);

CREATE INDEX eudamed_certificate_actor_idx ON eudamed_certificate (actor_srn);

GRANT SELECT ON manufacturer, manufacturer_srn, eudamed_certificate
  TO dentalia_api;
-- The confirm queue is a producer decision, not a registry write: the web
-- process may resolve an SRN candidate. It still holds zero write grants on
-- document / item_document / evidence.
GRANT INSERT, UPDATE ON manufacturer_srn TO dentalia_api;
```

- [ ] **Step 4: Apply and run the full suite**

```bash
docker compose build migrate && docker compose run --rm migrate
./scripts/test.sh
```

Expected: `applied 1 migration(s)`, full suite green.

- [ ] **Step 5: Commit**

```bash
git add migrations/0NN_eudamed_manufacturer.sql tests/test_eudamed_views.py
git commit -m "eudamed: manufacturer identity, one canonical name to many SRNs"
```

---

## Task 3: Actor-name normalisation and matching

**Files:**
- Create: `app/eudamed_names.py`
- Test: `tests/test_eudamed_names.py`

**Interfaces:**
- Consumes: nothing. Pure module — no database, no fetcher, no config.
- Produces:
  - `normalise(name: str) -> str` — casefolded, punctuation- and legal-suffix-stripped, whitespace-collapsed.
  - `match_actor(actor_name: str, candidates: dict[str, list[str]]) -> tuple[str | None, str, float]` — returns `(canonical_name | None, discovered_via, score)` where `discovered_via` is `'register-exact'` or `'register-fuzzy'`. `candidates` maps canonical name → the raw names that alias to it. Score is 100.0 for an exact normalised match.
  - `EXACT_VIA = "register-exact"`, `FUZZY_VIA = "register-fuzzy"`, `FUZZY_FLOOR = 88.0`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_eudamed_names.py`:

```python
"""Attributing an EUDAMED actor to one of our canonical manufacturers.

This is the step where a mistake becomes a wrong e-mail to a supplier, so the
module is pure and the rules are explicit. `actorName=` on the certificates
endpoint is a SUBSTRING match -- `Ivo` returns 4 records, `Ivoclar` returns 1
(measured 2026-08-26) -- which is exactly why attribution does not use a query
at all: the register is mirrored whole and matched here, locally.

Ruling (Denis, 2026-08-26): a normalised exact match auto-stores; anything else
is queued for a human. No LLM (Invariant 12) -- rapidfuzz only.
"""

from __future__ import annotations

import pytest

from app.eudamed_names import (
    EXACT_VIA, FUZZY_VIA, FUZZY_FLOOR, match_actor, normalise,
)

CANDIDATES = {
    "IVOCLAR": ["IVOCLAR VIVADENT", "Ivoclar Vivadent AG"],
    "CARL MARTIN": ["CARL MARTIN"],
    "KOMET": ["KOMET", "Gebr. Brasseler"],
    "GC EUROPE N.V.": ["GC EUROPE N.V."],
}


@pytest.mark.parametrize("raw,want", [
    ("Ivoclar Vivadent AG", "ivoclar vivadent"),
    ("IVOCLAR VIVADENT A.G.", "ivoclar vivadent"),
    ("Carl Martin GmbH", "carl martin"),
    ("GC EUROPE N.V.", "gc europe"),
    ("Gebr. Brasseler GmbH & Co. KG", "gebr brasseler"),
    ("  Institut   Straumann   AG  ", "institut straumann"),
])
def test_normalise_strips_case_punctuation_and_legal_suffixes(raw, want):
    assert normalise(raw) == want


def test_an_exact_normalised_match_is_auto():
    canonical, via, score = match_actor("Ivoclar Vivadent AG", CANDIDATES)
    assert canonical == "IVOCLAR"
    assert via == EXACT_VIA
    assert score == 100.0


def test_a_near_match_is_fuzzy_and_scored():
    """`GC EUROPE NV` without the dots is the same company. It is not an exact
    normalised match against every alias spelling we happen to hold, so it
    reaches a person rather than a sweep."""
    canonical, via, score = match_actor("GC Europe NV", CANDIDATES)
    assert canonical == "GC EUROPE N.V."
    assert via == FUZZY_VIA
    assert FUZZY_FLOOR <= score < 100.0


def test_an_unrelated_actor_matches_nothing():
    """3.196 distinct actors are in the register and 375 of our 384 canonical
    names are not device manufacturers. Most pairings must simply not match."""
    canonical, via, score = match_actor("PAUL HARTMANN AG", CANDIDATES)
    assert canonical is None


def test_a_substring_of_a_canonical_name_is_not_a_match():
    """`Ivo` returns 4 records from the API's substring matcher. Nothing that
    merely starts with our name may be attributed to us."""
    canonical, _, _ = match_actor("Ivo Medical Supplies Ltd", CANDIDATES)
    assert canonical is None


def test_the_second_alias_of_a_canonical_name_also_matches():
    """`manufacturer_alias` maps BC codes 001 and 005 both to IVOCLAR, and KOMET
    trades as Gebr. Brasseler. Matching must consider every alias, not the
    canonical string alone -- Brasseler is the actor name that carries KOMET's
    certificate HZ 1470094-1."""
    canonical, via, _ = match_actor("Gebr. Brasseler GmbH & Co. KG", CANDIDATES)
    assert canonical == "KOMET"
    assert via == EXACT_VIA


def test_matching_is_deterministic_when_two_canonicals_are_close():
    """Ties must resolve the same way on every run, or the confirm queue churns."""
    cands = {"ACME A": ["Acme Dental"], "ACME B": ["Acme Dental"]}
    first = match_actor("Acme Dental", cands)
    for _ in range(5):
        assert match_actor("Acme Dental", cands) == first
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./scripts/test.sh tests/test_eudamed_names.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.eudamed_names'`.

- [ ] **Step 3: Write the module**

`app/eudamed_names.py`:

```python
"""Attributing an EUDAMED actor to one of our canonical manufacturers.

Pure: no database, no fetcher, no config. Two callers need it -- the
`eudamed.certregister` handler and the confirm-queue UI -- and neither has any
business teaching this module about HTTP or Postgres.

Why matching happens here rather than in a query: `actorName=` on the
certificates endpoint filters, but as a SUBSTRING (`Ivo` -> 4 records,
`Ivoclar` -> 1, measured 2026-08-26). Querying per manufacturer would both
invite that trap and silently miss any manufacturer whose registered legal name
does not contain our canonical string -- `GC EUROPE N.V.` being the obvious
risk. The whole 4.608-row register is mirrored instead and matched locally.

Ruling (Denis, 2026-08-26): a normalised exact match auto-stores; anything else
is queued for a human, because a wrong attribution becomes a wrong e-mail to a
supplier.
"""

from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

EXACT_VIA = "register-exact"
FUZZY_VIA = "register-fuzzy"

#: Below this, nothing is offered at all -- not even to a human. Set so that
#: punctuation and spacing variants of one company clear it while two unrelated
#: dental companies do not. Raising it shrinks the confirm queue and loses
#: attributions; lowering it fills the queue with noise a person must reject.
FUZZY_FLOOR = 88.0

#: Legal-entity suffixes carry no identity: `Ivoclar Vivadent AG` and
#: `IVOCLAR VIVADENT` are one company. Stripped from both sides before any
#: comparison. Deliberately conservative -- an unknown suffix is left in place
#: rather than guessed at, because dropping a real word would merge two
#: companies.
_SUFFIXES = {
    "ag", "gmbh", "co", "kg", "nv", "bv", "sa", "srl", "spa", "ltd", "limited",
    "llc", "inc", "corp", "corporation", "plc", "oy", "ab", "as", "aps", "sas",
    "sarl", "sl", "kft", "doo", "sp", "zoo", "pte", "pty",
}

_PUNCT = re.compile(r"[^\w\s]+", re.UNICODE)
_SPACE = re.compile(r"\s+")


def normalise(name: str) -> str:
    """Casefold, strip accents and punctuation, drop legal suffixes, collapse
    whitespace. The result is a comparison key, never displayed."""
    if not name:
        return ""
    folded = unicodedata.normalize("NFKD", name)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = _PUNCT.sub(" ", folded.casefold())
    words = [w for w in _SPACE.split(folded) if w]
    while words and words[-1] in _SUFFIXES:
        words.pop()
    return " ".join(words)


def match_actor(actor_name, candidates):
    """Attribute one EUDAMED actor name to a canonical manufacturer.

    `candidates` maps canonical name -> every raw alias that resolves to it.
    Returns `(canonical_name | None, discovered_via, score)`.

    Sorted iteration and `>` rather than `>=` on the score make the outcome
    deterministic when two canonical names are equally close -- a confirm queue
    that reshuffles on every sweep is a confirm queue nobody clears.
    """
    key = normalise(actor_name)
    if not key:
        return None, FUZZY_VIA, 0.0

    for canonical in sorted(candidates):
        for alias in candidates[canonical]:
            if normalise(alias) == key:
                return canonical, EXACT_VIA, 100.0

    best_name, best_score = None, 0.0
    for canonical in sorted(candidates):
        for alias in candidates[canonical]:
            score = fuzz.token_sort_ratio(key, normalise(alias))
            if score > best_score:
                best_name, best_score = canonical, float(score)

    if best_score >= FUZZY_FLOOR:
        return best_name, FUZZY_VIA, best_score
    return None, FUZZY_VIA, best_score
```

- [ ] **Step 4: Confirm rapidfuzz is already a dependency**

Run: `grep -rn "rapidfuzz" pyproject.toml requirements*.txt 2>/dev/null`
Expected: present — CLAUDE.md names it as the scoring half of the fuzzy path. If it is absent, stop and flag; adding a dependency is not this task's call.

- [ ] **Step 5: Run test to verify it passes**

Run: `./scripts/test.sh tests/test_eudamed_names.py`
Expected: PASS, 8 tests.

- [ ] **Step 6: Commit**

```bash
git add app/eudamed_names.py tests/test_eudamed_names.py
git commit -m "eudamed: pure actor-name matching, exact auto and fuzzy queued"
```

---

## Task 4: `eudamed.certregister`

**Files:**
- Modify: `app/handlers/eudamed.py`
- Test: `tests/test_eudamed_handler.py`

**Interfaces:**
- Consumes: `manufacturer_srn` and `eudamed_certificate` from Task 2; `match_actor` from Task 3; `_devices`, `_is_last`, `PAGE_SIZE`, `MAX_PAGES` already in `app/handlers/eudamed.py`.
- Produces: `EUDAMED_CERT_URL`, `handle_eudamed_certregister(conn, job, *, fetcher=None) -> dict`, registered as `eudamed.certregister`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_eudamed_handler.py`:

```python
# --------------------------------------------------------------------------- #
# eudamed.certregister -- the whole register, matched locally
# --------------------------------------------------------------------------- #
# 4.608 certificates, 16 calls at size=300 (measured 2026-08-26). The pull is
# unfiltered on purpose: actorSrn arrives on every record, so SRN discovery is
# a by-product rather than a prerequisite, and matching happens in
# app/eudamed_names.py rather than through the endpoint's substring matcher.
from app.handlers.eudamed import (
    CERT_PAGE_SIZE, EUDAMED_CERT_URL, handle_eudamed_certregister,
)


def _cert(number, *, revision="Rev. 01", actor="LI-MF-000000522",
          actor_name="Ivoclar Vivadent AG", status="issued",
          ctype="quality-management-system", expiry="2031-05-04T00:00:00",
          issue="2026-06-12T00:00:00", nb="0123", version=1):
    return {
        "certificateNumber": number,
        "revisionNumber": revision,
        "actorSrn": actor,
        "actorName": actor_name,
        "certificateType": {"code": f"refdata.certificate-mdr-type.{ctype}"},
        "certificateStatus": {"code": f"refdata.certificate-status.{status}"},
        "issueDate": issue,
        "startingValidityDate": issue,
        "expiryDate": expiry,
        "notifiedBodySrn": nb,
        "versionNumber": version,
    }


def _seed_alias(conn, canonical, *raw_names):
    conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES (%s) "
        "ON CONFLICT DO NOTHING", (canonical,)
    )
    for raw in raw_names:
        conn.execute(
            "INSERT INTO manufacturer_alias (raw_name, canonical_name, source) "
            "VALUES (%s, %s, 'test') ON CONFLICT DO NOTHING", (raw, canonical)
        )


def test_the_register_pull_asks_for_the_biggest_page_the_server_gives(conn):
    """size=300 is the server's cap -- asking for 1.000 returns 300 (measured
    2026-08-26). At 20, the default, the register would be 231 calls."""
    fetcher = FakeFetcher(_page([_cert("G15 043306 0282")], last=True))

    handle_eudamed_certregister(conn, {"payload": {}}, fetcher=fetcher)

    assert f"size={CERT_PAGE_SIZE}" in fetcher.calls[0]
    assert CERT_PAGE_SIZE == 300
    assert fetcher.calls[0].startswith(EUDAMED_CERT_URL.split("?")[0])


def test_every_certificate_is_stored_whether_or_not_it_is_ours(conn):
    """The whole register is mirrored, not only our manufacturers' rows: 4.608
    rows is nothing, and "did a supplier register their first certificate"
    becomes answerable without a fresh fetch (Denis, 2026-08-26)."""
    fetcher = FakeFetcher(_page([
        _cert("G15 043306 0282"),
        _cert("Z-25-052-S-IX-E", actor="DE-MF-000006413",
              actor_name="AIRAmed GmbH", revision=None),
    ], last=True))

    res = handle_eudamed_certregister(conn, {"payload": {}}, fetcher=fetcher)

    n = conn.execute(
        "SELECT count(*) c FROM eudamed_certificate"
    ).fetchone()["c"]
    assert n == 2
    assert res["counts"]["certificates"] == 2


def test_a_null_revision_stores_as_empty_string(conn):
    """481 of 4.608 records carry no revisionNumber. They must not be dropped by
    a NOT NULL primary key."""
    fetcher = FakeFetcher(_page([
        _cert("Z-25-052-S-IX-E", revision=None)], last=True))

    handle_eudamed_certregister(conn, {"payload": {}}, fetcher=fetcher)

    row = conn.execute(
        "SELECT revision_number FROM eudamed_certificate"
    ).fetchone()
    assert row["revision_number"] == ""


def test_the_status_and_type_codes_are_stored_bare(conn):
    """`refdata.certificate-status.suspended` is stored as `suspended`. The
    prefix is EUDAMED's reference-data namespace, not information."""
    fetcher = FakeFetcher(_page([
        _cert("X1", status="suspended", ctype="technical-documentation")],
        last=True))

    handle_eudamed_certregister(conn, {"payload": {}}, fetcher=fetcher)

    row = conn.execute(
        "SELECT certificate_status, certificate_type FROM eudamed_certificate"
    ).fetchone()
    assert row["certificate_status"] == "suspended"
    assert row["certificate_type"] == "technical-documentation"


def test_an_exact_actor_name_stores_an_auto_srn(conn):
    _seed_alias(conn, "IVOCLAR", "Ivoclar Vivadent AG")
    fetcher = FakeFetcher(_page([_cert("G15 043306 0282")], last=True))

    handle_eudamed_certregister(conn, {"payload": {}}, fetcher=fetcher)

    row = conn.execute(
        "SELECT srn, status, discovered_via FROM manufacturer_srn "
        "WHERE canonical_name='IVOCLAR'"
    ).fetchone()
    assert row["srn"] == "LI-MF-000000522"
    assert row["status"] == "auto"
    assert row["discovered_via"] == "register-exact"


def test_a_fuzzy_actor_name_is_queued_not_swept(conn):
    """A wrong attribution becomes a wrong e-mail to a supplier. Only `auto` and
    `confirmed` are ever swept."""
    _seed_alias(conn, "GC EUROPE N.V.", "GC EUROPE N.V.")
    fetcher = FakeFetcher(_page([
        _cert("Y1", actor="BE-MF-000001234", actor_name="GC Europe NV")],
        last=True))

    handle_eudamed_certregister(conn, {"payload": {}}, fetcher=fetcher)

    row = conn.execute(
        "SELECT status, discovered_via, match_score FROM manufacturer_srn "
        "WHERE canonical_name='GC EUROPE N.V.'"
    ).fetchone()
    assert row["status"] == "pending"
    assert row["discovered_via"] == "register-fuzzy"
    assert row["match_score"] is not None


def test_an_unmatched_actor_stores_no_srn(conn):
    """3.196 distinct actors; almost none of them are ours."""
    _seed_alias(conn, "IVOCLAR", "Ivoclar Vivadent AG")
    fetcher = FakeFetcher(_page([
        _cert("Z1", actor="DE-MF-000099999", actor_name="PAUL HARTMANN AG")],
        last=True))

    res = handle_eudamed_certregister(conn, {"payload": {}}, fetcher=fetcher)

    assert conn.execute(
        "SELECT count(*) c FROM manufacturer_srn"
    ).fetchone()["c"] == 0
    assert res["counts"]["unmatched_actors"] >= 1


def test_a_human_decision_is_never_overwritten_by_a_later_pull(conn):
    """The register is re-pulled monthly. A rejected candidate that came back as
    `pending` every month would be a queue nobody can clear."""
    _seed_alias(conn, "GC EUROPE N.V.", "GC EUROPE N.V.")
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, "
        " status, decided_at, decided_by) "
        "VALUES ('GC EUROPE N.V.', 'BE-MF-000001234', 'register-fuzzy', "
        "        'rejected', now(), 'natasa')"
    )
    fetcher = FakeFetcher(_page([
        _cert("Y1", actor="BE-MF-000001234", actor_name="GC Europe NV")],
        last=True))

    handle_eudamed_certregister(conn, {"payload": {}}, fetcher=fetcher)

    row = conn.execute(
        "SELECT status FROM manufacturer_srn WHERE srn='BE-MF-000001234'"
    ).fetchone()
    assert row["status"] == "rejected"


def test_first_seen_survives_a_second_pull(conn):
    """A new revision is a new row, so a preserved first_seen IS the renewal
    signal. If ON CONFLICT stomped it, the certificate watch would go blind."""
    fetcher = FakeFetcher(_page([_cert("G15 043306 0282")], last=True))
    handle_eudamed_certregister(conn, {"payload": {}}, fetcher=fetcher)
    before = conn.execute(
        "SELECT first_seen FROM eudamed_certificate"
    ).fetchone()["first_seen"]

    fetcher2 = FakeFetcher(_page([_cert("G15 043306 0282")], last=True))
    handle_eudamed_certregister(conn, {"payload": {}}, fetcher=fetcher2)
    after = conn.execute(
        "SELECT first_seen, synced_at FROM eudamed_certificate"
    ).fetchone()

    assert after["first_seen"] == before
    assert after["synced_at"] >= before


def test_the_pull_walks_every_page(conn):
    fetcher = FakeFetcher(
        _page([_cert("A1")], last=False),
        _page([_cert("B1", actor="XX-MF-1")], last=True),
    )

    handle_eudamed_certregister(conn, {"payload": {}}, fetcher=fetcher)

    assert len(fetcher.calls) == 2
    assert "page=0" in fetcher.calls[0]
    assert "page=1" in fetcher.calls[1]
    assert conn.execute(
        "SELECT count(*) c FROM eudamed_certificate"
    ).fetchone()["c"] == 2


def test_a_non_200_raises_rather_than_reporting_success(conn):
    """The queue owns backoff and the dead-letter. A swallowed 503 would report
    a complete register read that never happened."""
    fetcher = FakeFetcher(FetchResult(status=503, body=b"", etag=None,
                                      last_modified=None))

    with pytest.raises(RuntimeError, match="503"):
        handle_eudamed_certregister(conn, {"payload": {}}, fetcher=fetcher)


def test_the_register_pull_writes_no_registry_row(conn):
    """Invariant 1. Asserted, not assumed."""
    _seed_alias(conn, "IVOCLAR", "Ivoclar Vivadent AG")
    before = {
        t: conn.execute(f"SELECT count(*) c FROM {t}").fetchone()["c"]
        for t in ("document", "item_document", "evidence")
    }
    fetcher = FakeFetcher(_page([_cert("G15 043306 0282")], last=True))

    handle_eudamed_certregister(conn, {"payload": {}}, fetcher=fetcher)

    after = {
        t: conn.execute(f"SELECT count(*) c FROM {t}").fetchone()["c"]
        for t in ("document", "item_document", "evidence")
    }
    assert after == before
```

Also extend the existing `_page` helper in that file so it can mark the last page:

```python
def _page(records, *, last=True, number=0):
    """The Spring envelope EUDAMED serves. Reading `content` alone silently
    takes the first 20 of any larger group -- that shipped as a bug on
    2026-08-25 and was fixed the same day."""
    return {
        "content": records,
        "number": number,
        "size": len(records),
        "totalElements": len(records),
        "totalPages": number + 1,
        "last": last,
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./scripts/test.sh tests/test_eudamed_handler.py -k certregister`
Expected: FAIL — `ImportError: cannot import name 'handle_eudamed_certregister'`.

- [ ] **Step 3: Write the handler**

In `app/handlers/eudamed.py`, add after the existing constants:

```python
#: Public EUDAMED certificate search. Unfiltered on purpose: the whole register
#: is 4.608 records in 16 calls (measured 2026-08-26), `actorSrn` arrives on
#: every one, and matching locally avoids the endpoint's substring matcher --
#: `actorName=Ivo` returns 4 records, `actorName=Ivoclar` returns 1.
EUDAMED_CERT_URL = (
    "https://ec.europa.eu/tools/eudamed/api/certificates/search/"
    "?size={size}&page={page}"
)

#: The server's cap. Asking for 1.000 returns 300. At the default 20 the
#: register would be 231 calls instead of 16.
CERT_PAGE_SIZE = 300

_CERT_UPSERT = """
INSERT INTO eudamed_certificate
  (certificate_number, revision_number, actor_srn, actor_name,
   certificate_type, certificate_status, issue_date, starting_validity,
   expiry_date, notified_body_srn, version_number, synced_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
ON CONFLICT (certificate_number, revision_number, actor_srn) DO UPDATE SET
    actor_name         = EXCLUDED.actor_name,
    certificate_type   = EXCLUDED.certificate_type,
    certificate_status = EXCLUDED.certificate_status,
    issue_date         = EXCLUDED.issue_date,
    starting_validity  = EXCLUDED.starting_validity,
    expiry_date        = EXCLUDED.expiry_date,
    notified_body_srn  = EXCLUDED.notified_body_srn,
    version_number     = EXCLUDED.version_number,
    synced_at          = now()
-- first_seen is deliberately absent: a new revision is a new row, so an
-- untouched first_seen IS the renewal signal.
"""

#: `status` and `decided_at` are absent from the DO UPDATE set on purpose: the
#: register is re-pulled monthly, and a rejected candidate that came back as
#: `pending` every month would be a queue nobody can clear.
_SRN_UPSERT = """
INSERT INTO manufacturer_srn
  (canonical_name, srn, actor_name, discovered_via, status, match_score)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (canonical_name, srn) DO UPDATE SET
    actor_name  = EXCLUDED.actor_name,
    match_score = EXCLUDED.match_score
"""


def _code(value):
    """EUDAMED wraps enums as {"code": "refdata.<namespace>.<value>"}. The
    namespace is reference data, not information."""
    if not isinstance(value, dict):
        return None
    code = value.get("code")
    return code.rsplit(".", 1)[-1] if code else None


def _day(value):
    """Dates arrive as `2031-05-04T00:00:00`. Postgres takes the date half."""
    return value[:10] if isinstance(value, str) and value else None


def _candidate_aliases(conn) -> dict:
    """canonical name -> every raw alias that resolves to it, plus the canonical
    string itself. KOMET trades as Gebr. Brasseler, which is the actor name
    carrying its certificate, so matching on the canonical string alone would
    miss it."""
    out: dict[str, list[str]] = {}
    for row in conn.execute(
        "SELECT canonical_name, raw_name FROM manufacturer_alias"
    ).fetchall():
        out.setdefault(row["canonical_name"], []).append(row["raw_name"])
    for row in conn.execute(
        "SELECT canonical_name FROM manufacturer"
    ).fetchall():
        out.setdefault(row["canonical_name"], []).append(row["canonical_name"])
    return out


def handle_eudamed_certregister(conn, job, *, fetcher=None) -> dict:
    """Mirror the whole EU certificate register, then attribute actors locally.

    Writes `eudamed_certificate` and `manufacturer_srn`. Writes no registry row
    -- Invariant 1, asserted by test.
    """
    r = Result()
    if fetcher is None:
        fetcher = make_fetcher()

    records: list[dict] = []
    for page in range(MAX_PAGES + 1):
        if page == MAX_PAGES:
            raise RuntimeError(
                "EUDAMED never reported a last page for the certificate "
                f"register -- stopped paging after {MAX_PAGES} pages"
            )
        resp = fetcher.get(EUDAMED_CERT_URL.format(
            size=CERT_PAGE_SIZE, page=page,
        ))
        if resp.status != 200:
            raise RuntimeError(
                f"EUDAMED returned {resp.status} for the certificate register"
            )
        try:
            body = json.loads(resp.body or b"")
        except (ValueError, TypeError) as exc:
            raise RuntimeError(
                "EUDAMED returned a non-JSON body for the certificate register"
            ) from exc

        got = _devices(body)
        records.extend(got)
        if _is_last(body, got):
            break

    for c in records:
        number = (c.get("certificateNumber") or "").strip()
        actor = (c.get("actorSrn") or "").strip()
        if not number or not actor:
            # Both are primary-key components; a record missing either cannot
            # be stored. Counted, never silent (CLAUDE.md).
            r.count("skipped_incomplete")
            continue
        conn.execute(_CERT_UPSERT, (
            number,
            (c.get("revisionNumber") or "").strip(),
            actor,
            (c.get("actorName") or "").strip() or None,
            _code(c.get("certificateType")),
            _code(c.get("certificateStatus")),
            _day(c.get("issueDate")),
            _day(c.get("startingValidityDate")),
            _day(c.get("expiryDate")),
            (c.get("notifiedBodySrn") or "").strip() or None,
            c.get("versionNumber"),
        ))
        r.count("certificates")

    candidates = _candidate_aliases(conn)
    seen: set[tuple[str, str]] = set()
    for c in records:
        actor = (c.get("actorSrn") or "").strip()
        actor_name = (c.get("actorName") or "").strip()
        if not actor or not actor_name or (actor, actor_name) in seen:
            continue
        seen.add((actor, actor_name))
        canonical, via, score = match_actor(actor_name, candidates)
        if canonical is None:
            r.count("unmatched_actors")
            continue
        status = "auto" if via == EXACT_VIA else "pending"
        conn.execute(_SRN_UPSERT, (
            canonical, actor, actor_name, via, status, score,
        ))
        r.count("srn_auto" if status == "auto" else "srn_pending")
        r.sample("srns", {"canonical": canonical, "srn": actor, "via": via})

    return r.as_dict()


register("eudamed.certregister", handle_eudamed_certregister)
```

and add to the imports at the top of the module:

```python
from app.eudamed_names import EXACT_VIA, match_actor
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./scripts/test.sh tests/test_eudamed_handler.py`
Expected: PASS — the 29 existing tests plus 12 new.

- [ ] **Step 5: Commit**

```bash
git add app/handlers/eudamed.py tests/test_eudamed_handler.py
git commit -m "eudamed: mirror the whole certificate register, attribute locally"
```

---

## Task 5: SRN confirm queue

**Files:**
- Modify: `web/registry.py`, `web/templates/_ui.html`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `manufacturer_srn` from Task 2, populated by Task 4.
- Produces: `srn_confirm_rows(conn) -> list[dict]`; routes `GET /manufacturers/srn-queue` and `POST /manufacturers/srn-queue/{canonical_name}/{srn}` taking form field `decision` ∈ `{confirm, reject}`; macro `srn_confirm(rows)` in `_ui.html`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_web.py`:

```python
# --------------------------------------------------------------------------- #
# EUDAMED SRN confirm queue
# --------------------------------------------------------------------------- #
# Attribution of a EUDAMED actor to one of our canonical manufacturers is a
# fuzzy name match, and a wrong one becomes a wrong e-mail to a supplier. Exact
# matches auto-store; everything else waits here (Denis, 2026-08-26).
def test_the_srn_queue_lists_only_pending_candidates(client, conn):
    conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES ('QUEUECO'), "
        "('AUTOCO') ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, actor_name, "
        " discovered_via, status, match_score) VALUES "
        "('QUEUECO','XX-MF-1','Queue Co NV','register-fuzzy','pending',91.0),"
        "('AUTOCO','XX-MF-2','Auto Co','register-exact','auto',100.0)")

    body = client.get("/manufacturers/srn-queue").text

    assert "XX-MF-1" in body
    assert "XX-MF-2" not in body


def test_confirming_a_candidate_makes_it_sweepable(client, conn):
    conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES ('CONFIRMCO') "
        "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, "
        " status) VALUES ('CONFIRMCO','XX-MF-3','register-fuzzy','pending')")

    client.post("/manufacturers/srn-queue/CONFIRMCO/XX-MF-3",
                data={"decision": "confirm"})

    row = conn.execute(
        "SELECT status, decided_at FROM manufacturer_srn WHERE srn='XX-MF-3'"
    ).fetchone()
    assert row["status"] == "confirmed"
    assert row["decided_at"] is not None


def test_rejecting_a_candidate_survives_the_next_register_pull(client, conn):
    """The register is re-pulled monthly. A rejection that reverted would be a
    queue nobody can clear."""
    conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES ('REJECTCO') "
        "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, "
        " status) VALUES ('REJECTCO','XX-MF-4','register-fuzzy','pending')")

    client.post("/manufacturers/srn-queue/REJECTCO/XX-MF-4",
                data={"decision": "reject"})

    row = conn.execute(
        "SELECT status FROM manufacturer_srn WHERE srn='XX-MF-4'"
    ).fetchone()
    assert row["status"] == "rejected"


def test_an_unknown_decision_is_refused(client, conn):
    conn.execute(
        "INSERT INTO manufacturer (canonical_name) VALUES ('BADCO') "
        "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, "
        " status) VALUES ('BADCO','XX-MF-5','register-fuzzy','pending')")

    resp = client.post("/manufacturers/srn-queue/BADCO/XX-MF-5",
                       data={"decision": "delete"})

    assert resp.status_code == 400
    row = conn.execute(
        "SELECT status FROM manufacturer_srn WHERE srn='XX-MF-5'"
    ).fetchone()
    assert row["status"] == "pending"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./scripts/test.sh tests/test_web.py -k srn`
Expected: FAIL — 404 on `/manufacturers/srn-queue`.

- [ ] **Step 3: Add the reader and the routes**

In `web/registry.py`, add above `register_routes`:

```python
def srn_confirm_rows(conn) -> list[dict]:
    """Candidates awaiting a human. `auto` never appears here -- a normalised
    exact match needs no confirmation -- and neither do decided rows."""
    return list(conn.execute(
        "SELECT s.canonical_name, s.srn, s.actor_name, s.match_score, "
        "       s.discovered_via, s.first_seen, "
        "       (SELECT count(*) FROM eudamed_certificate c "
        "         WHERE c.actor_srn = s.srn) AS certificates "
        "  FROM manufacturer_srn s "
        " WHERE s.status = 'pending' "
        " ORDER BY s.match_score DESC NULLS LAST, s.canonical_name"
    ).fetchall())
```

and inside `register_routes`, next to the existing `/manufacturers/{canonical_name:path}` route — **above** it, since a bare path segment would otherwise be swallowed by the `:path` converter:

```python
    @app.get("/manufacturers/srn-queue", response_class=HTMLResponse)
    def srn_queue(request: Request):
        with connect() as conn:
            rows = srn_confirm_rows(conn)
        return templates.TemplateResponse(
            "srn_queue.html", {"request": request, "rows": rows})

    @app.post("/manufacturers/srn-queue/{canonical_name}/{srn}")
    def srn_decide(canonical_name: str, srn: str, decision: str = Form(...)):
        # A closed set, checked here rather than trusted: this endpoint decides
        # which manufacturer's catalogue gets swept.
        if decision not in ("confirm", "reject"):
            raise HTTPException(status_code=400, detail="unknown decision")
        status = "confirmed" if decision == "confirm" else "rejected"
        with connect() as conn:
            conn.execute(
                "UPDATE manufacturer_srn SET status = %s, decided_at = now(), "
                "       decided_by = 'ui' "
                " WHERE canonical_name = %s AND srn = %s AND status = 'pending'",
                (status, canonical_name, srn),
            )
        return RedirectResponse("/manufacturers/srn-queue", status_code=303)
```

- [ ] **Step 4: Add the template**

Create `web/templates/srn_queue.html` following the existing page templates in that directory (extend the same base, use the same table classes). The body renders one row per candidate: canonical name, EUDAMED actor name, SRN, score, how many certificates that actor holds, and two buttons posting `decision=confirm` / `decision=reject`.

- [ ] **Step 5: Run test to verify it passes**

Run: `./scripts/test.sh tests/test_web.py`
Expected: PASS. Run the whole file rather than a bare `-k` for the final check — not because the file carries cross-test state (it does not; it is verified order-independent), but because a `-k` filter is easy to write too narrowly and report green on tests it never selected.

- [ ] **Step 6: Commit**

```bash
git add web/registry.py web/templates/srn_queue.html tests/test_web.py
git commit -m "eudamed: confirm queue for fuzzy SRN attributions"
```

---

## Task 6: The three certificate views

**Files:**
- Create: `migrations/0NN_eudamed_cert_views.sql`
- Test: `tests/test_eudamed_views.py`

**Interfaces:**
- Consumes: `eudamed_certificate`, `manufacturer_srn` from Task 2.
- Produces: views `certificate_drift`, `certificate_status_alert`, `certificate_gap`, all readable by `dentalia_api`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_eudamed_views.py`:

```python
# --------------------------------------------------------------------------- #
# The three certificate views
# --------------------------------------------------------------------------- #
# Ruling (Denis, 2026-08-26): the drift view JOINS ON BASELINE ONLY. Looser
# normalisations surface as `possible_match` for a human, never as a join. The
# 2026-08-19 _RCODE_SUFFIX ruling is untouched: `Rev. NN` is reported, never
# resolved.
import datetime as dt


def _cert_row(conn, number, revision, actor, **kw):
    conn.execute(
        "INSERT INTO eudamed_certificate (certificate_number, revision_number, "
        " actor_srn, actor_name, certificate_type, certificate_status, "
        " expiry_date, issue_date, notified_body_srn, synced_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,now()) "
        "ON CONFLICT DO NOTHING",
        (number, revision, actor, kw.get("actor_name", "Test AG"),
         kw.get("ctype", "quality-management-system"),
         kw.get("status", "issued"), kw.get("expiry"), kw.get("issue"),
         kw.get("nb", "0123")))


def _sweepable_srn(conn, canonical, srn):
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES (%s) "
                 "ON CONFLICT DO NOTHING", (canonical,))
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, "
        " status) VALUES (%s,%s,'register-exact','auto') "
        "ON CONFLICT DO NOTHING", (canonical, srn))


def test_drift_reports_both_revisions_and_resolves_neither(conn, seeded_doc):
    """Our cert_number embeds the revision (`G15 043306 0282 Rev. 00`);
    EUDAMED separates it. The view splits ours to join and shows both sides.
    96 declarations sit in that shape."""
    doc_id = seeded_doc(type="EC", cert_number="G15 043306 0282 Rev. 00",
                        canonical="IVOCLAR")
    _sweepable_srn(conn, "IVOCLAR", "LI-MF-000000522")
    _cert_row(conn, "G15 043306 0282", "Rev. 02", "LI-MF-000000522",
              status="supplemented", expiry=dt.date(2031, 5, 4))

    row = conn.execute(
        "SELECT * FROM certificate_drift WHERE doc_id = %s", (doc_id,)
    ).fetchone()

    assert row["our_revision"] == "Rev. 00"
    assert row["eudamed_revision"] == "Rev. 02"
    assert row["certificate_number"] == "G15 043306 0282"


def test_drift_writes_no_registry_column(conn, seeded_doc):
    """Widening the revision comparison into a supersession is exactly the
    assertion the 2026-08-19 ruling refused. The view reports; it decides
    nothing."""
    doc_id = seeded_doc(type="EC", cert_number="G15 043306 0282 Rev. 00",
                        canonical="IVOCLAR")
    _sweepable_srn(conn, "IVOCLAR", "LI-MF-000000522")
    _cert_row(conn, "G15 043306 0282", "Rev. 02", "LI-MF-000000522")

    conn.execute("SELECT count(*) FROM certificate_drift")

    row = conn.execute(
        "SELECT superseded_by, cert_doc_id, status FROM document "
        "WHERE doc_id = %s", (doc_id,)
    ).fetchone()
    assert row["superseded_by"] is None
    assert row["cert_doc_id"] is None


def test_a_matching_revision_is_not_drift(conn, seeded_doc):
    seeded_doc(type="EC", cert_number="HZ 2020214-1 Rev. 00",
               canonical="SUREDENT")
    _sweepable_srn(conn, "SUREDENT", "KR-MF-000000001")
    _cert_row(conn, "HZ 2020214-1", "Rev. 00", "KR-MF-000000001")

    n = conn.execute(
        "SELECT count(*) c FROM certificate_drift "
        "WHERE certificate_number = 'HZ 2020214-1'"
    ).fetchone()["c"]
    assert n == 0


def test_a_looser_match_is_a_possible_match_not_a_join(conn, seeded_doc):
    """`HZ 1470094-1 G` (36 documents) matches Brasseler only if a trailing
    single letter is stripped. That would assert two differently-printed strings
    name one certificate -- the same class of claim the Rev. NN ruling refused.
    It is shown, not joined."""
    doc_id = seeded_doc(type="EC", cert_number="HZ 1470094-1 G",
                        canonical="KOMET")
    _sweepable_srn(conn, "KOMET", "DE-MF-000000777")
    _cert_row(conn, "HZ 1470094-1", "Rev. 5", "DE-MF-000000777",
              status="supplemented", expiry=dt.date(2026, 2, 28))

    drift = conn.execute(
        "SELECT count(*) c FROM certificate_drift WHERE doc_id = %s", (doc_id,)
    ).fetchone()["c"]
    possible = conn.execute(
        "SELECT possible_match, possible_rule FROM certificate_drift_candidate "
        "WHERE doc_id = %s", (doc_id,)
    ).fetchone()

    assert drift == 0
    assert possible["possible_match"] == "HZ 1470094-1"
    assert possible["possible_rule"] == "trailing-letter"


def test_status_alert_carries_only_the_adverse_four(conn):
    """87 withdrawn, 68 cancelled, 31 restricted, 18 suspended across the
    register. `supplemented` and `reissued` are ordinary lifecycle events and
    must not raise an alert."""
    _sweepable_srn(conn, "ALERTCO", "XX-MF-100")
    for status in ("withdrawn", "cancelled", "suspended", "restricted",
                   "issued", "supplemented", "reissued", "amended"):
        _cert_row(conn, f"C-{status}", "Rev. 1", "XX-MF-100", status=status)

    got = {r["certificate_status"] for r in conn.execute(
        "SELECT certificate_status FROM certificate_status_alert "
        "WHERE canonical_name = 'ALERTCO'").fetchall()}

    assert got == {"withdrawn", "cancelled", "suspended", "restricted"}


def test_a_certificate_we_hold_no_copy_of_is_a_gap(conn):
    """Carl Martin's HZ 1594091-1 is the worked example: two register rows, and
    no document we hold cites it."""
    _sweepable_srn(conn, "CARL MARTIN", "DE-MF-000005066")
    _cert_row(conn, "HZ 1594091-1", "Rev. 2", "DE-MF-000005066",
              expiry=dt.date(2031, 6, 29))

    row = conn.execute(
        "SELECT certificate_number FROM certificate_gap "
        "WHERE canonical_name = 'CARL MARTIN'").fetchone()
    assert row["certificate_number"] == "HZ 1594091-1"


def test_a_certificate_we_do_hold_is_not_a_gap(conn, seeded_doc):
    seeded_doc(type="EC", cert_number="HZ 9999999-1", canonical="HELDCO")
    _sweepable_srn(conn, "HELDCO", "XX-MF-200")
    _cert_row(conn, "HZ 9999999-1", "Rev. 1", "XX-MF-200")

    n = conn.execute(
        "SELECT count(*) c FROM certificate_gap "
        "WHERE certificate_number = 'HZ 9999999-1'").fetchone()["c"]
    assert n == 0


def test_a_pending_srn_contributes_to_no_view(conn):
    """Only `auto` and `confirmed` attributions are trusted. A pending guess
    must not raise an alert against a manufacturer it may not belong to."""
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES "
                 "('PENDINGCO') ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, "
        " status) VALUES ('PENDINGCO','XX-MF-300','register-fuzzy','pending')")
    _cert_row(conn, "C-pending", "Rev. 1", "XX-MF-300", status="withdrawn")

    n = conn.execute(
        "SELECT count(*) c FROM certificate_status_alert "
        "WHERE canonical_name = 'PENDINGCO'").fetchone()["c"]
    assert n == 0
```

Add a `seeded_doc` fixture to `tests/conftest.py` if one does not already exist,
following the seeding helpers already there — it must insert a `document`, an
`item_document` at `status='production'`, an `item_group_member` and an
`item_group` carrying `canonical_manufacturer`, since that is the join path
`email_request.lapsing_for_manufacturer` uses and these views must use the same
one.

- [ ] **Step 2: Run test to verify it fails**

Run: `./scripts/test.sh tests/test_eudamed_views.py`
Expected: FAIL — `UndefinedTable: relation "certificate_drift" does not exist`.

- [ ] **Step 3: Write the migration**

`migrations/0NN_eudamed_cert_views.sql`:

```sql
-- The three certificate findings, as views
-- (docs/superpowers/specs/2026-08-25-eudamed-enhancement-design.md §4.1-4.3).
--
-- Views, not tables: the queue is regenerable state and the registry is truth;
-- a findings table is a third thing that can disagree with both.
--
-- STANDING RULING, Denis 2026-08-19 (app/handlers/validate.py:711-745):
-- `Rev. NN` is deliberately NOT resolved. "Rev. 00 and Rev. 01 may be a
-- supersession rather than a spelling, and resolving one to the other would
-- assert that silently." These views SPLIT our cert_number to join at all --
-- we store `G15 043306 0282 Rev. 00`, EUDAMED stores the number bare with the
-- revision in its own field -- and then REPORT both sides. Nothing here writes
-- superseded_by, cert_doc_id, or any registry column.
--
-- RULING, Denis 2026-08-26: join on baseline only. Looser normalisations are a
-- separate `possible_match` view for a human to confirm.

-- Our side, split once so three views agree on it.
CREATE VIEW held_certificate AS
SELECT d.doc_id,
       d.type,
       d.status,
       g.canonical_manufacturer AS canonical_name,
       d.cert_number AS raw_cert_number,
       -- Strip the trailing R-code (validate.py's existing, ruled-on rule),
       -- then split off `Rev. NN` -- which is REPORTED, never resolved.
       btrim(regexp_replace(
           regexp_replace(d.cert_number, '\s+R[0-9]+$', ''),
           '\s*Rev\.?\s*[0-9]+$', '', 'i')) AS base_cert_number,
       nullif(btrim(substring(d.cert_number from '(?i)Rev\.?\s*[0-9]+$')), '')
           AS our_revision
  FROM document d
  JOIN item_document idoc ON idoc.doc_id = d.doc_id
                         AND idoc.status = 'production'
  JOIN item_group_member gm ON gm.item_ref = idoc.item_ref
  JOIN item_group g ON g.group_id = gm.group_id
 WHERE d.cert_number IS NOT NULL AND d.cert_number <> ''
   AND g.canonical_manufacturer IS NOT NULL;

-- Only `auto` and `confirmed` attributions are trusted. A `pending` guess must
-- not raise an alert against a manufacturer it may not belong to.
CREATE VIEW trusted_manufacturer_srn AS
SELECT canonical_name, srn
  FROM manufacturer_srn
 WHERE status IN ('auto', 'confirmed');

CREATE VIEW certificate_drift AS
SELECT DISTINCT
       h.doc_id,
       h.canonical_name,
       h.base_cert_number AS certificate_number,
       h.our_revision,
       h.raw_cert_number,
       c.revision_number  AS eudamed_revision,
       c.certificate_status,
       c.certificate_type,
       c.issue_date       AS eudamed_issue_date,
       c.expiry_date      AS eudamed_expiry_date,
       c.notified_body_srn,
       c.actor_srn,
       c.actor_name
  FROM held_certificate h
  JOIN trusted_manufacturer_srn s ON s.canonical_name = h.canonical_name
  JOIN eudamed_certificate c ON c.actor_srn = s.srn
                            AND c.certificate_number = h.base_cert_number
 WHERE h.type IN ('EC', 'ISO')
   AND btrim(coalesce(h.our_revision, '')) IS DISTINCT FROM
       btrim(coalesce(c.revision_number, ''));

-- Shown, never joined. Each row names the rule that WOULD have matched it, so
-- a person sees what they are being asked to accept. Measured 2026-08-26:
-- baseline reaches 107 of 391 certificate-carrying documents, the trailing
-- letter takes it to 143, the SX->HZ prefix to 145.
CREATE VIEW certificate_drift_candidate AS
SELECT h.doc_id,
       h.canonical_name,
       h.raw_cert_number,
       c.certificate_number AS possible_match,
       c.revision_number    AS eudamed_revision,
       c.certificate_status,
       c.expiry_date        AS eudamed_expiry_date,
       CASE WHEN btrim(regexp_replace(h.base_cert_number, '\s+[A-Z]$', ''))
                 = c.certificate_number THEN 'trailing-letter'
            WHEN regexp_replace(h.base_cert_number, '^SX\M', 'HZ')
                 = c.certificate_number THEN 'sx-hz-prefix'
       END AS possible_rule
  FROM held_certificate h
  JOIN trusted_manufacturer_srn s ON s.canonical_name = h.canonical_name
  JOIN eudamed_certificate c ON c.actor_srn = s.srn
 WHERE h.type IN ('EC', 'ISO')
   AND c.certificate_number <> h.base_cert_number
   AND (btrim(regexp_replace(h.base_cert_number, '\s+[A-Z]$', ''))
            = c.certificate_number
     OR regexp_replace(h.base_cert_number, '^SX\M', 'HZ')
            = c.certificate_number);

-- The adverse four only. `supplemented`, `reissued` and `amended` are ordinary
-- lifecycle events -- 1.626 of the 4.608 records -- and alerting on them would
-- bury the 204 that matter.
CREATE VIEW certificate_status_alert AS
SELECT s.canonical_name,
       c.certificate_number,
       c.revision_number,
       c.certificate_status,
       c.certificate_type,
       c.expiry_date,
       c.notified_body_srn,
       c.actor_srn,
       c.actor_name,
       c.first_seen
  FROM trusted_manufacturer_srn s
  JOIN eudamed_certificate c ON c.actor_srn = s.srn
 WHERE c.certificate_status IN
       ('withdrawn', 'cancelled', 'suspended', 'restricted');

-- EUDAMED lists it, we hold no copy. The cheapest ask of the four: a named
-- number and issuing notified body, one line in an e-mail. The reverse case --
-- we hold a certificate EUDAMED does not list -- is expected for MDD-era
-- paperwork predating the register and is deliberately NOT a finding.
CREATE VIEW certificate_gap AS
SELECT s.canonical_name,
       c.certificate_number,
       c.revision_number,
       c.certificate_type,
       c.certificate_status,
       c.issue_date,
       c.expiry_date,
       c.notified_body_srn,
       c.actor_srn,
       c.actor_name
  FROM trusted_manufacturer_srn s
  JOIN eudamed_certificate c ON c.actor_srn = s.srn
 WHERE NOT EXISTS (
       SELECT 1 FROM held_certificate h
        WHERE h.canonical_name = s.canonical_name
          AND h.base_cert_number = c.certificate_number);

GRANT SELECT ON held_certificate, trusted_manufacturer_srn, certificate_drift,
                certificate_drift_candidate, certificate_status_alert,
                certificate_gap
  TO dentalia_api;
```

- [ ] **Step 4: Apply and run the full suite**

```bash
docker compose build migrate && docker compose run --rm migrate
./scripts/test.sh
```

Expected: `applied 1 migration(s)`, full suite green.

- [ ] **Step 5: Commit**

```bash
git add migrations/0NN_eudamed_cert_views.sql tests/test_eudamed_views.py tests/conftest.py
git commit -m "eudamed: drift, status alert and certificate gap views"
```

---

## Task 7: Certificate findings in the UI

**Files:**
- Modify: `web/registry.py`, `web/app.py:2345`, `web/templates/_ui.html`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: the four views from Task 6.
- Produces: `certificate_findings(conn, canonical_name=None) -> dict` with keys `drift`, `candidates`, `alerts`, `gaps`, `coverage`; macro `cert_findings(findings)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_web.py`:

```python
def test_expiry_shows_certificate_drift(client, conn, seeded_doc):
    doc_id = seeded_doc(type="EC", cert_number="G15 043306 0282 Rev. 00",
                        canonical="IVOCLAR")
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('IVOCLAR') "
                 "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, "
        " status) VALUES ('IVOCLAR','LI-MF-000000522','register-exact','auto') "
        "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO eudamed_certificate (certificate_number, revision_number, "
        " actor_srn, certificate_status, synced_at) "
        "VALUES ('G15 043306 0282','Rev. 02','LI-MF-000000522',"
        "        'supplemented', now()) ON CONFLICT DO NOTHING")

    body = client.get("/expiry").text

    assert "Rev. 02" in body
    assert "G15 043306 0282" in body


def test_the_findings_state_their_denominator(client, conn):
    """Reach is 107 of 391 certificate-carrying documents. Presented without a
    denominator it reads as "we checked our certificates" (spec §5.8)."""
    body = client.get("/expiry").text
    assert "matched" in body.lower()


def test_a_stale_mirror_says_so(client, conn):
    """EUDAMED is an enhancement, never a main path. Stale data must be visibly
    stale rather than silently wrong."""
    body = client.get("/expiry").text
    assert "synced" in body.lower() or "never synced" in body.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./scripts/test.sh tests/test_web.py -k certificate_drift`
Expected: FAIL — `Rev. 02` is not in the `/expiry` body.

- [ ] **Step 3: Add the reader**

In `web/registry.py`:

```python
def certificate_findings(conn, canonical_name: str | None = None) -> dict:
    """The three certificate findings plus their denominator.

    `coverage` exists because reach is 107 of 391 certificate-carrying
    documents (measured 2026-08-26) and a finding list without its denominator
    reads as a completed check. The unmatched bucket is our extraction quality,
    not EUDAMED's contents -- `0482`, `CE-0197`, `NB1639` and `Quality
    Management System` are sitting in `document.cert_number`.
    """
    where, args = "", ()
    if canonical_name:
        where, args = " WHERE canonical_name = %s", (canonical_name,)

    def rows(view):
        return list(conn.execute(f"SELECT * FROM {view}{where}", args).fetchall())

    held = conn.execute(
        "SELECT count(DISTINCT doc_id) c FROM held_certificate" + where, args
    ).fetchone()["c"]
    matched = conn.execute(
        "SELECT count(DISTINCT h.doc_id) c FROM held_certificate h "
        " JOIN trusted_manufacturer_srn s ON s.canonical_name = h.canonical_name "
        " JOIN eudamed_certificate c ON c.actor_srn = s.srn "
        "  AND c.certificate_number = h.base_cert_number" +
        (" WHERE h.canonical_name = %s" if canonical_name else ""), args
    ).fetchone()["c"]
    synced = conn.execute(
        "SELECT max(synced_at) s FROM eudamed_certificate"
    ).fetchone()["s"]

    return {
        "drift": rows("certificate_drift"),
        "candidates": rows("certificate_drift_candidate"),
        "alerts": rows("certificate_status_alert"),
        "gaps": rows("certificate_gap"),
        "coverage": {"held": held, "matched": matched,
                     "unmatched": held - matched},
        "synced_at": synced,
    }
```

- [ ] **Step 4: Render it**

Add a `cert_findings(findings)` macro to `web/templates/_ui.html` following the
macros already there. It renders, in this order: the status alerts (most urgent,
red), the drift table with both revisions side by side, the certificate gaps,
then a muted line reading `matched N of M certificate-carrying documents ·
EUDAMED synced <timestamp or "never">`. Near-miss candidates render collapsed
under the drift table with the rule name shown, never mixed into it.

Call it from `/expiry` in `web/app.py:2345` and from the manufacturer page in
`web/registry.py`, passing `canonical_name` on the latter.

- [ ] **Step 5: Run test to verify it passes**

Run: `./scripts/test.sh tests/test_web.py`
Expected: PASS — whole file, not `-k`.

- [ ] **Step 6: Commit**

```bash
git add web/registry.py web/app.py web/templates/_ui.html tests/test_web.py
git commit -m "eudamed: certificate findings on the expiry board and manufacturer page"
```

---

## Task 8: Drift lines in the renewal e-mail

**Files:**
- Modify: `app/handlers/email_request.py`
- Test: `tests/test_email_request.py`

**Interfaces:**
- Consumes: `certificate_drift` from Task 6.
- Produces: `drift_for_manufacturer(conn, manufacturer) -> list[dict]`; `compose(manufacturer, groups, today, *, drift=())` — the new argument defaults empty so every existing caller and test is unaffected.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_email_request.py`:

```python
def test_a_drift_row_is_cited_in_the_renewal_mail():
    """The mail already says "these are expiring, please send renewals". With
    drift it can say which renewal exists and when it was issued -- a much
    harder mail to ignore. Measured live: Ivoclar's G15 043306 0282 is at
    Rev. 02, issued 2026-06-12, valid to 2031-05-04, while 96 of our
    declarations cite Rev. 00."""
    drift = [{
        "certificate_number": "G15 043306 0282",
        "our_revision": "Rev. 00",
        "eudamed_revision": "Rev. 02",
        "eudamed_issue_date": dt.date(2026, 6, 12),
        "eudamed_expiry_date": dt.date(2031, 5, 4),
        "notified_body_srn": "0123",
    }]

    _, _, body = compose("IVOCLAR", [], dt.date(2026, 8, 26), drift=drift)

    assert "G15 043306 0282" in body
    assert "Rev. 02" in body
    assert "2026-06-12" in body


def test_the_mail_is_unchanged_when_there_is_no_drift():
    """Additive only. Every existing caller passes no drift and must get
    byte-identical copy."""
    a = compose("IVOCLAR", [], dt.date(2026, 8, 26))
    b = compose("IVOCLAR", [], dt.date(2026, 8, 26), drift=())
    assert a == b


def test_drift_never_asserts_the_revision_supersedes_ours():
    """The mail reports what EUDAMED shows. It does not tell the supplier their
    old revision is void -- that is the assertion the 2026-08-19 ruling
    refused, and it would be wrong in a letter as well as in a column."""
    drift = [{
        "certificate_number": "G15 043306 0282",
        "our_revision": "Rev. 00",
        "eudamed_revision": "Rev. 02",
        "eudamed_issue_date": dt.date(2026, 6, 12),
        "eudamed_expiry_date": dt.date(2031, 5, 4),
        "notified_body_srn": "0123",
    }]

    _, _, body = compose("IVOCLAR", [], dt.date(2026, 8, 26), drift=drift)

    lowered = body.lower()
    assert "supersede" not in lowered
    assert "no longer valid" not in lowered
    assert "void" not in lowered
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./scripts/test.sh tests/test_email_request.py -k drift`
Expected: FAIL — `TypeError: compose() got an unexpected keyword argument 'drift'`.

- [ ] **Step 3: Add the reader and extend compose**

In `app/handlers/email_request.py`:

```python
def drift_for_manufacturer(conn, manufacturer: str) -> list[dict]:
    """Certificate revisions EUDAMED shows ahead of ours, for one manufacturer.

    An enhancement, never a main path: if `certificate_drift` is empty because
    nothing has been synced, the mail is exactly the mail it was before.
    """
    return list(conn.execute(
        "SELECT DISTINCT certificate_number, our_revision, eudamed_revision, "
        "       eudamed_issue_date, eudamed_expiry_date, notified_body_srn "
        "  FROM certificate_drift WHERE canonical_name = %s "
        " ORDER BY certificate_number",
        (manufacturer,),
    ).fetchall())


def _render_drift(rows) -> str:
    """One paragraph, cited, asserting nothing.

    Deliberately NOT worded as a supersession: `Rev. 00` and `Rev. 02` may be a
    supersession or a spelling, and the 2026-08-19 ruling refused to resolve one
    to the other. Telling a supplier their revision is void would make that
    assertion in a letter instead of a column.
    """
    if not rows:
        return ""
    lines = ["",
             "The EU database (EUDAMED) currently lists a newer revision of "
             "the following certificate(s). If these apply to the documents "
             "above, please send the current revision:",
             ""]
    for r in rows:
        issued = r["eudamed_issue_date"]
        valid = r["eudamed_expiry_date"]
        lines.append(
            f"  - {r['certificate_number']}: we hold "
            f"{r['our_revision'] or 'no stated revision'}; EUDAMED lists "
            f"{r['eudamed_revision'] or 'no revision'}"
            + (f", issued {issued:%Y-%m-%d}" if issued else "")
            + (f", valid to {valid:%Y-%m-%d}" if valid else "")
            + (f" (notified body {r['notified_body_srn']})"
               if r.get("notified_body_srn") else "")
        )
    return "\n".join(lines) + "\n"
```

Change the `compose` signature to `def compose(manufacturer, groups, today, *, drift=()):`
and append `_render_drift(drift)` to the body immediately before the closing
salutation. In `handle_email_request`, fetch the rows and pass them:

```python
    drift = drift_for_manufacturer(conn, manufacturer)
    kind, subject, body = compose(manufacturer, groups, today, drift=drift)
```

and count them on the result: `r.count("drift_rows", len(drift))` if the
`Result` API takes a count argument, otherwise loop — check `app/results.py`
before writing this line rather than assuming.

- [ ] **Step 4: Run test to verify it passes**

Run: `./scripts/test.sh tests/test_email_request.py`
Expected: PASS, including every pre-existing test unchanged.

- [ ] **Step 5: Commit**

```bash
git add app/handlers/email_request.py tests/test_email_request.py
git commit -m "email: cite the EUDAMED revision in the renewal request"
```

---

## Task 9: Sweep state and the mirror delta

**Files:**
- Create: `migrations/0NN_eudamed_sweep_state.sql`
- Test: `tests/test_eudamed_views.py`

**Interfaces:**
- Consumes: `manufacturer` from Task 2, `eudamed_mirror` from migration 003.
- Produces: `eudamed_mirror.first_seen`, `eudamed_mirror.device_status_type`; table `eudamed_sweep_state(canonical_name, last_swept_at, due_at, released_at, released_by)`; view `eudamed_sweep_due`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_eudamed_views.py`:

```python
def test_the_mirror_can_tell_a_new_device_from_an_old_one(conn):
    """Before this column, `synced_at` was stomped to now() on every upsert, so
    after one sweep a device registered last week and one registered in 2021
    were indistinguishable and NO delta was derivable at all."""
    conn.execute(
        "INSERT INTO eudamed_mirror (udi_di, basic_udi_di, reference, "
        " synced_at, first_seen) VALUES "
        "('D-OLD','BUDI-1','111', now(), now() - interval '400 days'),"
        "('D-NEW','BUDI-2','222', now(), now() - interval '2 days')")

    n = conn.execute(
        "SELECT count(*) c FROM eudamed_mirror "
        "WHERE first_seen > now() - interval '30 days' "
        "  AND udi_di IN ('D-OLD','D-NEW')").fetchone()["c"]
    assert n == 1


def test_a_manufacturer_becomes_due_after_the_interval(conn):
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('DUECO') "
                 "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO eudamed_sweep_state (canonical_name, last_swept_at, due_at) "
        "VALUES ('DUECO', now() - interval '100 days', now() - interval '1 day')")

    row = conn.execute(
        "SELECT canonical_name FROM eudamed_sweep_due "
        "WHERE canonical_name = 'DUECO'").fetchone()
    assert row is not None


def test_a_released_sweep_is_no_longer_listed_as_due(conn):
    """The scheduler proposes; a person releases. A manufacturer already
    released must not keep nagging."""
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('RELCO') "
                 "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO eudamed_sweep_state (canonical_name, last_swept_at, "
        " due_at, released_at, released_by) VALUES "
        "('RELCO', now() - interval '100 days', now() - interval '1 day', "
        " now(), 'ui')")

    n = conn.execute(
        "SELECT count(*) c FROM eudamed_sweep_due "
        "WHERE canonical_name = 'RELCO'").fetchone()["c"]
    assert n == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./scripts/test.sh tests/test_eudamed_views.py -k sweep`
Expected: FAIL — `UndefinedColumn: column "first_seen" does not exist`.

- [ ] **Step 3: Write the migration**

`migrations/0NN_eudamed_sweep_state.sql`:

```sql
-- The sweep delta and the propose/release state
-- (docs/superpowers/specs/2026-08-25-eudamed-enhancement-design.md §3.4-3.5).
--
-- Before `first_seen`, the eudamed_mirror upsert set synced_at = now() on every
-- conflict, so after one sweep a device registered last week and one registered
-- in 2021 were indistinguishable. "What's new" was not derivable at all.
ALTER TABLE eudamed_mirror
  ADD COLUMN IF NOT EXISTS first_seen timestamptz NOT NULL DEFAULT now();

COMMENT ON COLUMN eudamed_mirror.first_seen IS
  'First time this udi_di was seen. NEVER updated by ON CONFLICT -- it is the '
  'whole basis of the sweep delta.';

-- Read for transitions, acted on nowhere. A withdrawn device does NOT leave the
-- declaration gap list: MDR retention runs ten years past the last device
-- placed, so the DoC is still owed. Any other retention rule is Dentalia's call.
ALTER TABLE eudamed_mirror
  ADD COLUMN IF NOT EXISTS device_status_type text;

-- RULING, Denis 2026-08-20, restated 2026-08-26: "No sweep ever runs
-- unattended... an operator releases every run." The scheduler writes due_at
-- and nothing else; released_at is written by a human clicking in the UI.
CREATE TABLE eudamed_sweep_state (
  canonical_name text PRIMARY KEY REFERENCES manufacturer(canonical_name),
  last_swept_at  timestamptz,
  due_at         timestamptz,
  released_at    timestamptz,
  released_by    text
);

-- Due and not yet released. Releasing clears nothing; the sweep handler resets
-- released_at when it finishes, which is what makes the next cycle possible.
CREATE VIEW eudamed_sweep_due AS
SELECT s.canonical_name,
       s.last_swept_at,
       s.due_at,
       (SELECT count(*) FROM trusted_manufacturer_srn t
         WHERE t.canonical_name = s.canonical_name) AS srns
  FROM eudamed_sweep_state s
 WHERE s.due_at IS NOT NULL
   AND s.due_at <= now()
   AND s.released_at IS NULL;

GRANT SELECT ON eudamed_sweep_state, eudamed_sweep_due TO dentalia_api;
-- Releasing a sweep is a producer decision, like enqueueing any other job.
GRANT INSERT, UPDATE ON eudamed_sweep_state TO dentalia_api;
```

- [ ] **Step 4: Apply and run the full suite**

```bash
docker compose build migrate && docker compose run --rm migrate
./scripts/test.sh
```

Expected: `applied 1 migration(s)`, full suite green.

- [ ] **Step 5: Commit**

```bash
git add migrations/0NN_eudamed_sweep_state.sql tests/test_eudamed_views.py
git commit -m "eudamed: mirror delta columns and the sweep release state"
```

---

## Task 10: `eudamed.sweep`

**Files:**
- Modify: `app/handlers/eudamed.py`
- Test: `tests/test_eudamed_handler.py`

**Interfaces:**
- Consumes: `trusted_manufacturer_srn` from Task 6, `eudamed_sweep_state` from Task 9.
- Produces: `EUDAMED_SRN_URL`, `handle_eudamed_sweep(conn, job, *, fetcher=None) -> dict`, registered as `eudamed.sweep`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_eudamed_handler.py`:

```python
# --------------------------------------------------------------------------- #
# eudamed.sweep -- one manufacturer's registered catalogue
# --------------------------------------------------------------------------- #
from app.handlers.eudamed import EUDAMED_SRN_URL, handle_eudamed_sweep


def test_the_sweep_uses_srn_which_cannot_return_another_company(conn):
    """`reference=` is a substring matcher and `actorName=` is too. `srn=` is
    neither: it returned exactly 3.594 devices for Carl Martin's
    DE-MF-000005066 (measured 2026-08-26), matching the independent count."""
    _sweepable(conn, "CARL MARTIN", "DE-MF-000005066")
    fetcher = FakeFetcher(_page([
        _device("D1", reference="1845", srn="DE-MF-000005066")], last=True))

    handle_eudamed_sweep(conn, {"payload": {"manufacturer": "CARL MARTIN"}},
                         fetcher=fetcher)

    assert "srn=DE-MF-000005066" in fetcher.calls[0]
    assert "reference=" not in fetcher.calls[0]


def test_a_record_carrying_someone_elses_srn_raises(conn):
    """Layer 3 of the wrong-attribution mitigation. If the filter stops holding,
    the sweep must fail loudly rather than mirror a stranger's catalogue."""
    _sweepable(conn, "CARL MARTIN", "DE-MF-000005066")
    fetcher = FakeFetcher(_page([
        _device("D1", reference="1845", srn="XX-MF-000000001")], last=True))

    with pytest.raises(RuntimeError, match="DE-MF-000005066"):
        handle_eudamed_sweep(conn, {"payload": {"manufacturer": "CARL MARTIN"}},
                             fetcher=fetcher)


def test_a_pending_srn_is_not_swept(conn):
    """Only auto and confirmed attributions are trusted."""
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('PENDCO') "
                 "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, "
        " status) VALUES ('PENDCO','XX-MF-9','register-fuzzy','pending')")
    fetcher = FakeFetcher()

    res = handle_eudamed_sweep(conn, {"payload": {"manufacturer": "PENDCO"}},
                               fetcher=fetcher)

    assert fetcher.calls == []
    assert res["counts"].get("no_trusted_srn") == 1


def test_first_seen_survives_a_second_sweep(conn):
    """The delta is the whole point of sweeping twice."""
    _sweepable(conn, "CARL MARTIN", "DE-MF-000005066")
    handle_eudamed_sweep(conn, {"payload": {"manufacturer": "CARL MARTIN"}},
                         fetcher=FakeFetcher(_page([
                             _device("D1", reference="1845",
                                     srn="DE-MF-000005066")], last=True)))
    before = conn.execute(
        "SELECT first_seen FROM eudamed_mirror WHERE udi_di='D1'"
    ).fetchone()["first_seen"]

    handle_eudamed_sweep(conn, {"payload": {"manufacturer": "CARL MARTIN"}},
                         fetcher=FakeFetcher(_page([
                             _device("D1", reference="1845",
                                     srn="DE-MF-000005066")], last=True)))

    after = conn.execute(
        "SELECT first_seen FROM eudamed_mirror WHERE udi_di='D1'"
    ).fetchone()["first_seen"]
    assert after == before


def test_the_device_status_is_stored(conn):
    _sweepable(conn, "CARL MARTIN", "DE-MF-000005066")
    fetcher = FakeFetcher(_page([{
        **_device("D1", reference="1845", srn="DE-MF-000005066"),
        "deviceStatusType": {"code": "refdata.device-status.on-the-market"},
    }], last=True))

    handle_eudamed_sweep(conn, {"payload": {"manufacturer": "CARL MARTIN"}},
                         fetcher=fetcher)

    row = conn.execute(
        "SELECT device_status_type FROM eudamed_mirror WHERE udi_di='D1'"
    ).fetchone()
    assert row["device_status_type"] == "on-the-market"


def test_a_finished_sweep_clears_the_release_and_stamps_the_state(conn):
    """Releasing is a one-shot. A sweep that left released_at set would never
    become due again."""
    _sweepable(conn, "CARL MARTIN", "DE-MF-000005066")
    conn.execute(
        "INSERT INTO eudamed_sweep_state (canonical_name, due_at, released_at, "
        " released_by) VALUES ('CARL MARTIN', now(), now(), 'ui') "
        "ON CONFLICT (canonical_name) DO UPDATE SET released_at = now()")

    handle_eudamed_sweep(conn, {"payload": {"manufacturer": "CARL MARTIN"}},
                         fetcher=FakeFetcher(_page([
                             _device("D1", reference="1845",
                                     srn="DE-MF-000005066")], last=True)))

    row = conn.execute(
        "SELECT last_swept_at, released_at, due_at FROM eudamed_sweep_state "
        "WHERE canonical_name = 'CARL MARTIN'").fetchone()
    assert row["last_swept_at"] is not None
    assert row["released_at"] is None


def test_the_sweep_writes_no_registry_row(conn):
    """Invariant 1."""
    _sweepable(conn, "CARL MARTIN", "DE-MF-000005066")
    before = {
        t: conn.execute(f"SELECT count(*) c FROM {t}").fetchone()["c"]
        for t in ("document", "item_document", "evidence")
    }
    handle_eudamed_sweep(conn, {"payload": {"manufacturer": "CARL MARTIN"}},
                         fetcher=FakeFetcher(_page([
                             _device("D1", reference="1845",
                                     srn="DE-MF-000005066")], last=True)))
    after = {
        t: conn.execute(f"SELECT count(*) c FROM {t}").fetchone()["c"]
        for t in ("document", "item_document", "evidence")
    }
    assert after == before
```

Add the two helpers this block needs, next to the existing `_device`:

```python
def _sweepable(conn, canonical, srn):
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES (%s) "
                 "ON CONFLICT DO NOTHING", (canonical,))
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, "
        " status) VALUES (%s, %s, 'register-exact', 'auto') "
        "ON CONFLICT DO NOTHING", (canonical, srn))
```

and extend `_device` to take `srn=None`, emitting `manufacturerSrn`.

- [ ] **Step 2: Run test to verify it fails**

Run: `./scripts/test.sh tests/test_eudamed_handler.py -k sweep`
Expected: FAIL — `ImportError: cannot import name 'handle_eudamed_sweep'`.

- [ ] **Step 3: Write the handler**

In `app/handlers/eudamed.py`:

```python
#: Per-manufacturer device catalogue. `srn=` is the one parameter here that
#: cannot return another company's devices -- `reference=` and `actorName=` are
#: both substring matchers. Confirmed exact 2026-08-26: srn=DE-MF-000005066
#: returns exactly 3.594 devices, matching the independent Carl Martin count.
EUDAMED_SRN_URL = (
    "https://ec.europa.eu/tools/eudamed/api/devices/udiDiData"
    "?srn={srn}&size={size}&page={page}"
)

#: The devices endpoint honours 300 too (measured 2026-08-26), which makes
#: Ivoclar ~38 pages instead of the 114 the first design draft assumed.
SWEEP_PAGE_SIZE = 300

_MIRROR_SWEEP_UPSERT = """
INSERT INTO eudamed_mirror (udi_di, basic_udi_di, device_name, trade_name,
                            manufacturer_srn, reference, device_status_type,
                            synced_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, now())
ON CONFLICT (udi_di) DO UPDATE SET
    basic_udi_di       = EXCLUDED.basic_udi_di,
    device_name        = EXCLUDED.device_name,
    trade_name         = EXCLUDED.trade_name,
    manufacturer_srn   = EXCLUDED.manufacturer_srn,
    reference          = EXCLUDED.reference,
    device_status_type = EXCLUDED.device_status_type,
    synced_at          = now()
-- first_seen absent on purpose: it is the sweep delta.
"""


def _assert_srn(devices, srn: str) -> None:
    """Layer 3 of the wrong-attribution mitigation (spec §5.2).

    Unknown parameters are silently ignored by this API and return the
    unfiltered 3,25M-record set rather than erroring, so a renamed parameter
    reads as a spectacular hit. Every record must carry the SRN we asked for.
    """
    for d in devices:
        got = (d.get("manufacturerSrn") or "").strip()
        if got != srn:
            raise RuntimeError(
                f"EUDAMED returned a device for {got or '<no srn>'} when asked "
                f"for {srn} -- the srn filter is not holding, refusing to store"
            )


def handle_eudamed_sweep(conn, job, *, fetcher=None) -> dict:
    """Mirror one canonical manufacturer's registered device catalogue.

    Released by a human, never self-emitted: Denis, 2026-08-20, restated
    2026-08-26 -- "no sweep ever runs unattended".
    """
    payload = job.get("payload") or {}
    manufacturer = (payload.get("manufacturer") or "").strip()
    r = Result()
    if not manufacturer:
        r.count("no_manufacturer")
        r.note("no manufacturer on the payload -- nothing to sweep")
        return r.as_dict()

    srns = [row["srn"] for row in conn.execute(
        "SELECT srn FROM trusted_manufacturer_srn WHERE canonical_name = %s "
        "ORDER BY srn", (manufacturer,)).fetchall()]
    if not srns:
        r.count("no_trusted_srn")
        r.note(f"{manufacturer}: no auto or confirmed SRN -- nothing swept")
        return r.as_dict()

    if fetcher is None:
        fetcher = make_fetcher()

    for srn in srns:
        for page in range(MAX_PAGES + 1):
            if page == MAX_PAGES:
                raise RuntimeError(
                    f"EUDAMED never reported a last page for {srn} -- "
                    f"stopped paging after {MAX_PAGES} pages"
                )
            resp = fetcher.get(EUDAMED_SRN_URL.format(
                srn=quote(srn, safe=""), size=SWEEP_PAGE_SIZE, page=page))
            if resp.status != 200:
                raise RuntimeError(f"EUDAMED returned {resp.status} for {srn}")
            try:
                body = json.loads(resp.body or b"")
            except (ValueError, TypeError) as exc:
                raise RuntimeError(
                    f"EUDAMED returned a non-JSON body for {srn}") from exc

            got = _devices(body)
            _assert_srn(got, srn)
            for d in got:
                udi_di = (d.get("primaryDi") or "").strip()
                if not udi_di:
                    r.count("skipped_no_udi_di")
                    continue
                reference = (d.get("reference") or "").strip() or None
                if reference is None:
                    r.count("missing_reference")
                conn.execute(_MIRROR_SWEEP_UPSERT, (
                    udi_di,
                    (d.get("basicUdi") or "").strip() or None,
                    (d.get("deviceName") or "").strip() or None,
                    (d.get("tradeName") or "").strip() or None,
                    srn,
                    reference,
                    _code(d.get("deviceStatusType")),
                ))
                r.count("devices")
            if _is_last(body, got):
                break

    conn.execute(
        "INSERT INTO eudamed_sweep_state (canonical_name, last_swept_at, "
        "  due_at, released_at, released_by) "
        "VALUES (%s, now(), NULL, NULL, NULL) "
        "ON CONFLICT (canonical_name) DO UPDATE SET "
        "  last_swept_at = now(), due_at = NULL, "
        "  released_at = NULL, released_by = NULL",
        (manufacturer,),
    )
    return r.as_dict()


register("eudamed.sweep", handle_eudamed_sweep)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./scripts/test.sh tests/test_eudamed_handler.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/handlers/eudamed.py tests/test_eudamed_handler.py
git commit -m "eudamed: per-manufacturer sweep, gated on a trusted SRN"
```

---

## Task 11: Article-probe SRN fallback

**Files:**
- Modify: `app/handlers/eudamed.py`
- Test: `tests/test_eudamed_handler.py`

**Interfaces:**
- Consumes: `manufacturer.srn_probed_at` from Task 2.
- Produces: `probe_srn(conn, canonical_name, *, fetcher) -> str | None`, called by `handle_eudamed_sweep` when no trusted SRN exists and `srn_probed_at` is null.

**Why this exists:** GC EUROPE has 356 device articles and **zero** certificates
in the register (measured 2026-08-26), so its SRN cannot come from the
certificate pull. This is the only route left for a device manufacturer that
holds no notified-body certificate.

- [ ] **Step 1: Write the failing test**

```python
# --------------------------------------------------------------------------- #
# Article-probe SRN fallback
# --------------------------------------------------------------------------- #
# `reference=` is a SUBSTRING match, measured live: `reference=64` returns
# 62.918 records, and an ungated sample returned Promedics Orthopaedics, PAUL
# HARTMANN and Cerascreen records for Dentalia article numbers. Bootstrapping an
# SRN off one of those would sweep a stranger's whole catalogue into the mirror
# and generate a gap list for articles that are not ours.
from app.handlers.eudamed import probe_srn


def test_the_probe_accepts_only_an_exact_reference_and_a_matching_name(conn):
    _seed_item(conn, item_ref="531505", canonical="IVOCLAR")
    _seed_alias(conn, "IVOCLAR", "Ivoclar Vivadent AG")
    fetcher = FakeFetcher(_page([
        {**_device("D0", reference="0531505"), "manufacturerName": "Someone Else",
         "manufacturerSrn": "XX-MF-000000001"},
        {**_device("D1", reference="531505"), "manufacturerName": "Ivoclar Vivadent AG",
         "manufacturerSrn": "LI-MF-000000522"},
    ], last=True))

    assert probe_srn(conn, "IVOCLAR", fetcher=fetcher) == "LI-MF-000000522"


def test_a_substring_hit_from_another_company_is_refused(conn):
    """The whole record set for `reference=64` is 62.918 rows."""
    _seed_item(conn, item_ref="64", canonical="CARL MARTIN")
    _seed_alias(conn, "CARL MARTIN", "Carl Martin GmbH")
    fetcher = FakeFetcher(_page([
        {**_device("D1", reference="00128-64"), "manufacturerName": "PAUL HARTMANN AG",
         "manufacturerSrn": "DE-MF-000099999"},
    ], last=True))

    assert probe_srn(conn, "CARL MARTIN", fetcher=fetcher) is None
    assert conn.execute(
        "SELECT count(*) c FROM manufacturer_srn").fetchone()["c"] == 0


def test_an_exact_reference_from_the_wrong_manufacturer_is_refused(conn):
    """Article numbers are not globally unique. Exactness alone is not enough."""
    _seed_item(conn, item_ref="1845", canonical="CARL MARTIN")
    _seed_alias(conn, "CARL MARTIN", "Carl Martin GmbH")
    fetcher = FakeFetcher(_page([
        {**_device("D1", reference="1845"), "manufacturerName": "PAUL HARTMANN AG",
         "manufacturerSrn": "DE-MF-000099999"},
    ], last=True))

    assert probe_srn(conn, "CARL MARTIN", fetcher=fetcher) is None


def test_a_probe_that_found_nothing_is_recorded_so_it_does_not_repeat(conn):
    """375 of our 384 canonical names are not device manufacturers and will
    never resolve. Without this stamp the fallback re-probes them forever."""
    _seed_item(conn, item_ref="9999", canonical="PAPERCO")
    _seed_alias(conn, "PAPERCO", "Paper Co")
    fetcher = FakeFetcher(_page([], last=True))

    probe_srn(conn, "PAPERCO", fetcher=fetcher)

    row = conn.execute(
        "SELECT srn_probed_at FROM manufacturer WHERE canonical_name='PAPERCO'"
    ).fetchone()
    assert row["srn_probed_at"] is not None


def test_the_article_that_produced_the_srn_is_recorded(conn):
    """A wrong bootstrap must be auditable after the fact, not mysterious."""
    _seed_item(conn, item_ref="531505", canonical="IVOCLAR")
    _seed_alias(conn, "IVOCLAR", "Ivoclar Vivadent AG")
    fetcher = FakeFetcher(_page([
        {**_device("D1", reference="531505"), "manufacturerName": "Ivoclar Vivadent AG",
         "manufacturerSrn": "LI-MF-000000522"},
    ], last=True))

    probe_srn(conn, "IVOCLAR", fetcher=fetcher)

    row = conn.execute(
        "SELECT srn, discovered_via, probe_ref, status FROM manufacturer_srn"
    ).fetchone()
    assert row["discovered_via"] == "article-probe"
    assert row["probe_ref"] == "531505"
    assert row["status"] == "auto"
```

`_seed_item` inserts an `item_mirror` row with `md_flag` true plus the
`item_group` / `item_group_member` rows carrying `canonical_manufacturer` — the
same join path the views use. Write it next to `_sweepable` if it is not already
in the file.

- [ ] **Step 2: Run test to verify it fails**

Run: `./scripts/test.sh tests/test_eudamed_handler.py -k probe`
Expected: FAIL — `ImportError: cannot import name 'probe_srn'`.

- [ ] **Step 3: Write it**

```python
def probe_srn(conn, canonical_name: str, *, fetcher) -> str | None:
    """Last resort: read an SRN off one of our own article numbers.

    Only reached for a device manufacturer holding no notified-body certificate
    -- GC EUROPE, 356 articles and zero certificates. Both gates are mandatory:
    `reference=` is a substring matcher (`reference=64` -> 62.918 records) and
    article numbers are not globally unique, so exactness alone is not enough
    either.
    """
    row = conn.execute(
        "SELECT im.item_ref, im.mfr_ref FROM item_mirror im "
        "  JOIN item_group_member gm ON gm.item_ref = im.item_ref "
        "  JOIN item_group g ON g.group_id = gm.group_id "
        " WHERE g.canonical_manufacturer = %s AND im.md_flag "
        " ORDER BY im.item_ref LIMIT 1", (canonical_name,)).fetchone()

    conn.execute(
        "UPDATE manufacturer SET srn_probed_at = now() WHERE canonical_name = %s",
        (canonical_name,))
    if not row:
        return None

    article = (row["mfr_ref"] or row["item_ref"] or "").strip()
    if not article:
        return None

    resp = fetcher.get(EUDAMED_DEVICE_URL.replace(
        "basicUdi={basic_udi}", "reference={reference}").format(
            reference=quote(article, safe=""), size=PAGE_SIZE, page=0))
    if resp.status != 200:
        raise RuntimeError(
            f"EUDAMED returned {resp.status} probing {canonical_name}")
    body = json.loads(resp.body or b"")

    candidates = _candidate_aliases(conn)
    for d in _devices(body):
        if (d.get("reference") or "").strip() != article:
            continue                      # gate 1: exact reference
        name = (d.get("manufacturerName") or "").strip()
        matched, via, _ = match_actor(name, {canonical_name:
                                             candidates.get(canonical_name, [])})
        if matched != canonical_name or via != EXACT_VIA:
            continue                      # gate 2: manufacturer corroborates
        srn = (d.get("manufacturerSrn") or "").strip()
        if not srn:
            continue
        conn.execute(
            "INSERT INTO manufacturer_srn (canonical_name, srn, actor_name, "
            "  discovered_via, status, probe_ref) "
            "VALUES (%s, %s, %s, 'article-probe', 'auto', %s) "
            "ON CONFLICT (canonical_name, srn) DO NOTHING",
            (canonical_name, srn, name, article))
        return srn
    return None
```

In `handle_eudamed_sweep`, before the `no_trusted_srn` early return, try the
probe once when `manufacturer.srn_probed_at` is null, and re-read `srns` if it
returns one.

- [ ] **Step 4: Run test to verify it passes**

Run: `./scripts/test.sh tests/test_eudamed_handler.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/handlers/eudamed.py tests/test_eudamed_handler.py
git commit -m "eudamed: article-probe SRN fallback for uncertificated manufacturers"
```

---

## Task 12: The due list and the release button

**Files:**
- Modify: `web/registry.py`, `web/templates/_ui.html`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `eudamed_sweep_due` from Task 9.
- Produces: `sweep_due_rows(conn) -> list[dict]`; route `POST /manufacturers/{canonical_name}/sweep` which stamps `released_at` **and** enqueues `eudamed.sweep`; macro `sweep_due(rows)`.

- [ ] **Step 1: Write the failing test**

```python
def test_releasing_a_sweep_enqueues_the_job(client, conn):
    """The scheduler proposes; this is the release. Denis, 2026-08-20 restated
    2026-08-26: an operator releases every run."""
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('SWEEPCO') "
                 "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, "
        " status) VALUES ('SWEEPCO','XX-MF-7','register-exact','auto')")
    conn.execute(
        "INSERT INTO eudamed_sweep_state (canonical_name, due_at) "
        "VALUES ('SWEEPCO', now()) ON CONFLICT (canonical_name) "
        "DO UPDATE SET due_at = now()")

    client.post("/manufacturers/SWEEPCO/sweep")

    job = conn.execute(
        "SELECT type, payload FROM job WHERE type = 'eudamed.sweep' "
        "ORDER BY id DESC LIMIT 1").fetchone()
    assert job["payload"]["manufacturer"] == "SWEEPCO"
    state = conn.execute(
        "SELECT released_at, released_by FROM eudamed_sweep_state "
        "WHERE canonical_name = 'SWEEPCO'").fetchone()
    assert state["released_at"] is not None


def test_a_manufacturer_with_no_trusted_srn_cannot_be_released(client, conn):
    """Releasing a sweep with nothing to sweep would burn the politeness lease
    and report success."""
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('NOSRNCO') "
                 "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO eudamed_sweep_state (canonical_name, due_at) "
        "VALUES ('NOSRNCO', now()) ON CONFLICT (canonical_name) "
        "DO UPDATE SET due_at = now()")

    resp = client.post("/manufacturers/NOSRNCO/sweep")

    assert resp.status_code == 400
    assert conn.execute(
        "SELECT count(*) c FROM job WHERE type='eudamed.sweep'"
    ).fetchone()["c"] == 0


def test_the_due_list_renders(client, conn):
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('DUEUI') "
                 "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO eudamed_sweep_state (canonical_name, due_at) "
        "VALUES ('DUEUI', now() - interval '1 day') "
        "ON CONFLICT (canonical_name) DO UPDATE SET due_at = now()")

    assert "DUEUI" in client.get("/manufacturers/sweep-due").text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./scripts/test.sh tests/test_web.py -k sweep`
Expected: FAIL — 404 on `/manufacturers/sweep-due`.

- [ ] **Step 3: Add the reader and routes**

In `web/registry.py`, `sweep_due_rows(conn)` selects from `eudamed_sweep_due`,
and the POST route — registered **above** `/manufacturers/{canonical_name:path}`
— refuses with 400 when `trusted_manufacturer_srn` holds nothing for that name,
otherwise stamps `released_at = now(), released_by = 'ui'` and enqueues
`eudamed.sweep` through the same enqueue helper the existing producer routes use
(find it in `web/` rather than writing raw SQL — the dedupe key and priority
belong to that helper).

- [ ] **Step 4: Render it**

`sweep_due(rows)` in `_ui.html`: manufacturer, last swept, due since, how many
SRNs, and a release button. Shown on the status board next to the other
exception queues.

- [ ] **Step 5: Run test to verify it passes**

Run: `./scripts/test.sh tests/test_web.py`
Expected: PASS — whole file.

- [ ] **Step 6: Commit**

```bash
git add web/registry.py web/templates/_ui.html tests/test_web.py
git commit -m "eudamed: sweep due list and the operator release"
```

---

## Task 13: `eudamed_declaration_gap` and the delta panel

**Files:**
- Create: `migrations/0NN_eudamed_gap_view.sql`
- Modify: `web/registry.py`, `web/templates/_ui.html`
- Test: `tests/test_eudamed_views.py`, `tests/test_web.py`

**Interfaces:**
- Consumes: `eudamed_mirror` (with `first_seen`, `device_status_type`) from Task 9.
- Produces: views `eudamed_declaration_gap`, `eudamed_sweep_delta`; `declaration_gap_summary(conn, canonical_name) -> dict`; macro `eudamed_gap(summary)`.

- [ ] **Step 1: Write the failing test**

```python
def test_the_gap_groups_by_basic_udi_di(conn):
    """The registry's largest gap -- 2.569 Carl Martin reusable instruments with
    no declaration -- is a request list of 70 documents, because a declaration
    covers a Basic UDI-DI group, not an article."""
    _sweepable_srn(conn, "CARL MARTIN", "DE-MF-000005066")
    for i, ref in enumerate(("1845", "1846", "1847")):
        conn.execute(
            "INSERT INTO eudamed_mirror (udi_di, basic_udi_di, reference, "
            " manufacturer_srn, synced_at) VALUES (%s,'BUDI-A',%s,"
            " 'DE-MF-000005066', now())", (f"D{i}", ref))
        _seed_item_row(conn, item_ref=ref, canonical="CARL MARTIN")

    row = conn.execute(
        "SELECT basic_udi_di, articles FROM eudamed_declaration_gap "
        "WHERE canonical_name = 'CARL MARTIN'").fetchone()
    assert row["basic_udi_di"] == "BUDI-A"
    assert row["articles"] == 3


def test_a_retracted_link_does_not_hide_an_article(conn):
    """Excluded on the JOIN, not in WHERE: a WHERE turns the LEFT join inner and
    drops every article with no link at all -- a bug already made and fixed once
    in web/registry.py."""
    _sweepable_srn(conn, "RETRACTCO", "XX-MF-800")
    _seed_item_row(conn, item_ref="R1", canonical="RETRACTCO")
    conn.execute(
        "INSERT INTO eudamed_mirror (udi_di, basic_udi_di, reference, "
        " manufacturer_srn, synced_at) VALUES "
        "('DR1','BUDI-R','R1','XX-MF-800', now())")
    _seed_link(conn, item_ref="R1", status="retracted")

    row = conn.execute(
        "SELECT articles FROM eudamed_declaration_gap "
        "WHERE canonical_name = 'RETRACTCO'").fetchone()
    assert row["articles"] == 1


def test_staged_is_counted_separately_from_absent(conn):
    """A gap list that counts a staged declaration as missing sends Nataša
    chasing a document she has."""
    _sweepable_srn(conn, "STAGEDCO", "XX-MF-801")
    _seed_item_row(conn, item_ref="S1", canonical="STAGEDCO")
    conn.execute(
        "INSERT INTO eudamed_mirror (udi_di, basic_udi_di, reference, "
        " manufacturer_srn, synced_at) VALUES "
        "('DS1','BUDI-S','S1','XX-MF-801', now())")
    _seed_link(conn, item_ref="S1", status="staged")

    row = conn.execute(
        "SELECT articles, staged FROM eudamed_declaration_gap "
        "WHERE canonical_name = 'STAGEDCO'").fetchone()
    assert row["staged"] == 1


def test_articles_eudamed_does_not_know_are_their_own_bucket(conn):
    """178 of 2.567 Carl Martin articles did not resolve, cause unknown. If the
    gap list treats "not in EUDAMED" as "no gap", the report overstates
    coverage. CLAUDE.md: skipped rows are counted and reported, never silent."""
    _sweepable_srn(conn, "UNKNOWNCO", "XX-MF-802")
    _seed_item_row(conn, item_ref="U1", canonical="UNKNOWNCO")

    row = conn.execute(
        "SELECT not_in_eudamed FROM eudamed_gap_summary "
        "WHERE canonical_name = 'UNKNOWNCO'").fetchone()
    assert row["not_in_eudamed"] == 1


def test_the_delta_lists_new_basic_udi_groups_and_status_changes(conn):
    """Ruled 2026-08-26: the delta unit is the Basic UDI-DI group and the device
    status transition. Individual new devices inside a group we already track
    are a count, not a list -- a sweep returns 11.364 rows for Ivoclar."""
    _sweepable_srn(conn, "DELTACO", "XX-MF-803")
    conn.execute(
        "INSERT INTO eudamed_mirror (udi_di, basic_udi_di, reference, "
        " manufacturer_srn, device_status_type, synced_at, first_seen) VALUES "
        "('DD1','BUDI-OLD','1','XX-MF-803','on-the-market', now(), "
        "  now() - interval '400 days'), "
        "('DD2','BUDI-NEW','2','XX-MF-803','on-the-market', now(), "
        "  now() - interval '2 days')")

    rows = conn.execute(
        "SELECT basic_udi_di FROM eudamed_sweep_delta "
        "WHERE canonical_name = 'DELTACO'").fetchall()
    assert [r["basic_udi_di"] for r in rows] == ["BUDI-NEW"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./scripts/test.sh tests/test_eudamed_views.py -k gap`
Expected: FAIL — `UndefinedTable: relation "eudamed_declaration_gap" does not exist`.

- [ ] **Step 3: Write the migration**

`migrations/0NN_eudamed_gap_view.sql` creates:

- `eudamed_declaration_gap` — our medical-device articles joined to
  `eudamed_mirror` on **exact** `reference`, restricted to a trusted SRN,
  grouped by `basic_udi_di`, with columns `canonical_name`, `basic_udi_di`,
  `articles`, `staged`, `mfr_scope_covered`, `trade_name`. Links are excluded on
  the JOIN (`LEFT JOIN item_document idoc ON idoc.item_ref = im.item_ref AND
  idoc.status <> 'retracted'`), never in `WHERE`.
- `eudamed_gap_summary` — per manufacturer: `covered`, `missing`, `staged`,
  `not_in_eudamed`. The last bucket is never folded into either of the others.
- `eudamed_sweep_delta` — `basic_udi_di` groups whose earliest `first_seen` is
  newer than the manufacturer's previous `last_swept_at`, plus rows whose
  `device_status_type` changed. Individual new devices inside a known group are
  aggregated into a count column, not listed.

Grant `SELECT` on all three to `dentalia_api`.

- [ ] **Step 4: Add the reader and the panel**

`declaration_gap_summary(conn, canonical_name)` returns the summary row plus the
gap rows; `eudamed_gap(summary)` renders the four buckets and the delta on the
manufacturer page. The panel states the sweep timestamp — an unswept
manufacturer must read "never swept", not "no gaps".

- [ ] **Step 5: Apply and run the full suite**

```bash
docker compose build migrate && docker compose run --rm migrate
./scripts/test.sh
```

- [ ] **Step 6: Commit**

```bash
git add migrations/0NN_eudamed_gap_view.sql web/registry.py web/templates/_ui.html tests/test_eudamed_views.py tests/test_web.py
git commit -m "eudamed: declaration gap by basic UDI-DI, and the sweep delta"
```

---

## Task 14: Scheduler ticks

**Files:**
- Modify: `app/scheduler.py`, `app/config.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `eudamed_sweep_state` from Task 9.
- Produces: `_tick_eudamed_certregister(conn, cfg, *, now) -> dict` (emits), `_tick_eudamed_sweep_due(conn, cfg, *, now) -> dict` (marks due, emits nothing); config keys `eudamed_certregister_enabled` (default False), `eudamed_certregister_interval_days` (default 30), `eudamed_sweep_interval_days` (default 90).

- [ ] **Step 1: Write the failing test**

```python
def test_the_certregister_tick_emits_because_it_is_a_bulk_read(conn):
    """Carved out of the no-unattended-sweep ruling (Denis, 2026-08-26): 16
    calls against a public register is the "prefer a bulk download to an API"
    the same ruling asks for, not the per-manufacturer sweep it forbids."""
    cfg = _cfg(eudamed_certregister_enabled=True)

    _tick_eudamed_certregister(conn, cfg.scheduler, now=_now())

    n = conn.execute(
        "SELECT count(*) c FROM job WHERE type='eudamed.certregister'"
    ).fetchone()["c"]
    assert n == 1


def test_the_certregister_tick_is_off_by_default(conn):
    """Same footing as expiry_email_enabled / email_poll_enabled: the scan runs
    for real, the emission is gated."""
    cfg = _cfg()
    assert cfg.scheduler.eudamed_certregister_enabled is False

    _tick_eudamed_certregister(conn, cfg.scheduler, now=_now())

    assert conn.execute(
        "SELECT count(*) c FROM job WHERE type='eudamed.certregister'"
    ).fetchone()["c"] == 0


def test_the_sweep_tick_marks_due_and_emits_nothing(conn):
    """THE ruling this plan restores. Denis, 2026-08-20: "No sweep ever runs
    unattended... an operator releases every run." The first design draft
    proposed a self-emitting tick and contradicted it."""
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('TICKCO') "
                 "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, "
        " status) VALUES ('TICKCO','XX-MF-6','register-exact','auto')")
    conn.execute(
        "INSERT INTO eudamed_sweep_state (canonical_name, last_swept_at) "
        "VALUES ('TICKCO', now() - interval '200 days')")

    _tick_eudamed_sweep_due(conn, _cfg().scheduler, now=_now())

    assert conn.execute(
        "SELECT count(*) c FROM job WHERE type='eudamed.sweep'"
    ).fetchone()["c"] == 0
    row = conn.execute(
        "SELECT due_at FROM eudamed_sweep_state WHERE canonical_name='TICKCO'"
    ).fetchone()
    assert row["due_at"] is not None


def test_a_manufacturer_swept_recently_is_not_marked_due(conn):
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES ('FRESHCO') "
                 "ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, "
        " status) VALUES ('FRESHCO','XX-MF-5','register-exact','auto')")
    conn.execute(
        "INSERT INTO eudamed_sweep_state (canonical_name, last_swept_at) "
        "VALUES ('FRESHCO', now() - interval '3 days')")

    _tick_eudamed_sweep_due(conn, _cfg().scheduler, now=_now())

    row = conn.execute(
        "SELECT due_at FROM eudamed_sweep_state WHERE canonical_name='FRESHCO'"
    ).fetchone()
    assert row["due_at"] is None
```

Use whatever config-building and clock helpers `tests/test_scheduler.py` already
has rather than the `_cfg` / `_now` placeholders above — read the file first.

- [ ] **Step 2: Run test to verify it fails**

Run: `./scripts/test.sh tests/test_scheduler.py -k eudamed`
Expected: FAIL — `ImportError: cannot import name '_tick_eudamed_certregister'`.

- [ ] **Step 3: Add the config keys**

In `app/config.py`, `class Scheduler`:

```python
    # EUDAMED. Certificates monthly, device sweeps quarterly (Denis,
    # 2026-08-26). The certregister tick EMITS -- 16 calls against a public
    # register is the bulk download the 2026-08-20 ruling prefers. The sweep
    # tick only marks a manufacturer due; a person releases every run.
    eudamed_certregister_enabled: bool = False
    eudamed_certregister_interval_days: int = 30
    eudamed_sweep_interval_days: int = 90
```

- [ ] **Step 4: Add the ticks**

Both follow the shape of `_tick_email_poll` — period key, ledger row, gated
emission. `_tick_eudamed_sweep_due` has no emission at all: it writes `due_at`
for every manufacturer holding a trusted SRN whose `last_swept_at` is older than
`eudamed_sweep_interval_days`, and returns the count for the weekly report.
Register both in `tick()`.

- [ ] **Step 5: Run test to verify it passes**

Run: `./scripts/test.sh tests/test_scheduler.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/scheduler.py app/config.py tests/test_scheduler.py
git commit -m "scheduler: emit the certificate register pull, propose sweeps only"
```

---

## Task 15: Contract and operational docs

**Files:**
- Modify: `docs/dentalia-pipeline-contract-prd-v3.md`, `docs/dentalia-job-type-handbook.md`, `docs/dentalia-schema-sketch.md`, `docs/README.md` (architecture, runbook, code-map), `PHASES.md`

**Interfaces:**
- Consumes: everything above.
- Produces: nothing executable. This task is what stops the docs going stale, per CLAUDE.md.

- [ ] **Step 1: PRD — the job-type enum**

Add `eudamed.certregister` and `eudamed.sweep` to the closed enum with their
Emits rows (both emit nothing — they are leaves). Note that no scheduler and no
handler ever enqueues `eudamed.sweep`; the operator's release button is its sole
producer.

> **Correction, 2026-08-27.** This step originally read "note that
> `eudamed.sweep` is the only job type in the system enqueued exclusively by a
> human action." That is false: `gate.apply`, `upload.ingest` and
> `vendor.import` are enqueued only from `web/app.py`, and `app/scheduler.py`
> references none of the three. The claim was written into the PRD and the
> job-type handbook in five places before a reviewer caught it, and was removed
> later. The sentence above is the true, narrower fact. Do not restore a
> superlative here — any comparative claim needs re-checking against every
> web-UI-only tag.

- [ ] **Step 2: Handbook — per-tag pseudo-code**

Add both handlers with payload examples, and state the SRN trust rule (`auto` /
`confirmed` only) once, where a handler author will read it.

- [ ] **Step 3: Schema sketch — the new objects**

`manufacturer.srn_probed_at`, `manufacturer_srn`, `eudamed_certificate`,
`eudamed_sweep_state`, `eudamed_mirror.first_seen`, `.device_status_type`, and
the seven views. Update the tag→table access matrix: `dentalia_api` gains
`INSERT/UPDATE` on `manufacturer_srn` and `eudamed_sweep_state` and **still holds
no write grant on `document` / `item_document` / `evidence`** — say so
explicitly, since this is the first time the web process gained any write grant
beyond `job`.

- [ ] **Step 4: Runbook — the operator's part**

How to release a sweep, how to clear the SRN confirm queue, what a
`certificate_status_alert` means and that it is **not** a document request, and
that a full Ivoclar sweep holds the `ec.europa.eu` lease for minutes.

- [ ] **Step 5: PHASES — close S2.3**

Move S2.3 from "RESEARCH DONE, blocked" to done, and record what stayed out:
`ref-eudamed` linking (rejected on measured yield), the declaration-gap e-mail
(deferred), the DISCOVER `eudamed` rung (still dark by measurement, not by
omission).

- [ ] **Step 6: Run the full suite and commit**

```bash
./scripts/test.sh
git add docs/ PHASES.md
git commit -m "docs: EUDAMED certificate register and sweep in the contracts"
```

---

## Self-Review

**Spec coverage.** Every spec section maps to a task: §3.1 → 2, §3.2 → 2, §3.3 →
2, §3.4 → 9, §3.5 → 9, §4 job types → 1, §4.1 → 6, §4.2 → 6, §4.3 → 6, §4.4 →
13, §4.5 → 3 + 5, §4.6 → 14, §5.1 → 6, §5.2 → 3 + 4 + 10 + 11, §5.3 → 13, §5.4 →
4 + 10, §5.5 → 12, §5.6 → 13, §5.7 → 9, §5.8 → 7, §7 invariants → asserted in 4,
10, and the Task 15 access matrix, §8 slices → tasks 1-14, §9 → the runbook in
15, §10 rulings → Global Constraints.

**Two things deliberately left as prose rather than code**, because writing them
blind would be worse than writing them with the file open: the Jinja macros in
Tasks 5, 7, 12, 13 (they must match the existing `_ui.html` conventions) and the
`eudamed_declaration_gap` SQL body in Task 13 (its exact columns depend on the
`item_group` join the conftest fixture establishes in Task 6). Both name their
required columns and behaviours, and both have tests written first.

**Known naming consistency:** `handle_eudamed_certregister`,
`handle_eudamed_sweep`, `probe_srn`, `match_actor`, `normalise`,
`certificate_findings`, `srn_confirm_rows`, `sweep_due_rows`,
`declaration_gap_summary`, `drift_for_manufacturer` — each defined once and
referenced under the same name everywhere.
