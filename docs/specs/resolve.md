# Unit spec — `resolve.group` (S1.2)

Normative sources (this spec adds only what they leave open — do not restate):
- Handbook §2 `docs/dentalia-job-type-handbook.md` (pseudo-code, combinations table).
- PRD v3 §2 `docs/dentalia-pipeline-contract-prd-v3.md` (Consumes/Emits/Writes, C1 `mfr_ref` materialization, staging path).
- Schema `migrations/002_ingest.sql` (`item_mirror`, `manufacturer_alias`, `item_group`, `item_group_member`, `item_group_refs`).
- Invariants: 1 (only GATE writes `document`/`item_document`/`evidence`), 3 (name-family never auto-writes a production *link*), 7 (closed job enum), 8 (active-scope dedupe), 12 (LLM = ranking/parsing only).

## 1. Module layout & signatures — `app/handlers/resolve.py`

```python
@dataclass(frozen=True)
class Match:
    group_id: int
    score: float          # 1.0 for deterministic rungs; rapidfuzz [0,1] for name-family
    basis: str            # existing-link | udi | basic-udi-di | name-family
    candidates: list[dict]  # [{group_id, score, sample_name}] — only populated by name-family

def handle_resolve_group(conn, job) -> dict            # entry; register("resolve.group", ...)

# manufacturer canonicalization
def _alias_lookup(conn, raw) -> tuple[str, bool]       # (canonical, was_created)

# resolution ladder — stop at first hit
def _existing_link(conn, item_ref) -> Match | None
def _by_udi(conn, item) -> Match | None
def _by_basic_udi_di(conn, item) -> Match | None
def _by_name_family(conn, item, canonical) -> Match | None   # pg_trgm candidates -> rapidfuzz

# writes / emits
def _ensure_member(conn, group_id, item_ref, mfr_ref, basis) -> None
def _create_singleton(conn, item, canonical) -> int
def _push_grouping_suggestion(conn, item, candidates, suggestion, score) -> None
def _resolve_open_suggestion(conn, item_ref, decided_by) -> None
def _has_current_doc_or_candidate(conn, group_id) -> bool
def _maybe_emit_discover(conn, group_id) -> bool
def _apply_manufacturer_bindings(conn, item, canonical) -> int   # C4 hook — see §5, deferred write
```

**T1 adjudicator injection** (invariant 12, testability — same pattern as `extract`/`gate` inject their LLM): `handle_resolve_group(conn, job, *, adjudicate=None)`. `adjudicate(item, candidates) -> dict` returns `{group_id?: int, action: "assign"|"new"|"unsure", rationale: str}`. Default builds the live T1 caller from `config.models.rank` (Haiku 4.5). Tests pass a stub; no live call.

## 2. Resolution ladder — what the handbook leaves implicit

Order (first hit wins): `existing_link -> by_udi -> by_basic_udi_di -> by_name_family`. Deterministic rungs return `score = 1.0` (always ≥ `NAME_ACCEPT`).

- **`_existing_link`**: `item_ref` already in `item_group_member` -> that `group_id`, basis `existing-link`. Makes the handler idempotent: a re-delivered job (or a singleton created on a prior attempt) short-circuits here, never duplicating a group.
- **`_by_udi`**: `item.udi` non-null, exact-equal to another catalogue member's `item_mirror.udi` that is already grouped -> that group, basis `udi`. Catalogue-local only in Phase 1. *Upgrade path (S2.3):* `udi -> eudamed_mirror -> basic_udi_di` resolution once the EUDAMED mirror exists; noted, not built here.
- **`_by_basic_udi_di`**: `item.udi` exact-equal to an existing `item_group.basic_udi_di` -> that group, basis `basic-udi-di`. (The strongest group key when a group already carries one.)
- **`_by_name_family`**: candidate generation in-DB via pg_trgm, scoring in Python via rapidfuzz (PRD: "pg_trgm candidates -> rapidfuzz score"; CLAUDE.md tooling row).
  ```sql
  SELECT gm.group_id, m.name, similarity(m.name, %(name)s) AS sim
  FROM item_group g
  JOIN item_group_member gm ON gm.group_id = g.group_id
  JOIN item_mirror m        ON m.item_ref  = gm.item_ref
  WHERE g.canonical_manufacturer = %(canonical)s   -- scoped: never cross-manufacturer
    AND m.name %% %(name)s                          -- trgm % (GIN index, recall)
  ORDER BY sim DESC LIMIT %(k)s;                     -- k = config resolve.name_candidate_k (default 20)
  ```
  Score per candidate group = **max** `rapidfuzz.fuzz.token_set_ratio(item.name, member.name) / 100` over that group's returned members. `Match.score` = best group's score; `candidates` = the distinct candidate groups (for the suggestion payload).

## 3. Disposition rules (bands from G5a §4)

| Condition | Action | Writes | Emits |
|---|---|---|---|
| `payload.group_id` present (suggestion resolution) | forced assign, basis `manual` | `item_group_member`; resolve open suggestion | `discover.group` if group lacks current doc |
| hit, `score ≥ NAME_ACCEPT` | accept | `item_group_member` (mfr_ref materialized, C1); `_apply_manufacturer_bindings` | `discover.group` if group lacks current doc |
| name-family, `NAME_SUGGEST ≤ score < NAME_ACCEPT` | T1 adjudicate -> stage | `grouping_suggestion` (open) | nothing (no membership, no discover — invariant 3) |
| no hit, or name-family `score < NAME_SUGGEST` | singleton | new `item_group` + member basis `singleton` | `discover.group` (always — new group has no doc) |

- `discover.group` payload `{group_id}`, dedupe_key `discover:{group_id}:0`. Cycle is fixed `0` ("initial/resolve cycle") in Phase 1; SCHEDULER (S1.5) owns monotonic cycle values. Active-scope dedupe (invariant 8) means a completed discover never blocks a later re-check.
- `_has_current_doc_or_candidate(group_id)`: EXISTS a `document.status IN ('staged','production')` reached by an `item_document` link of the same statuses from any group member. Wider than `validate._current_production_doc` on both axes: un-scoped by type/regulation (RESOLVE only asks "does this group still need discovery at all") and counting staged candidates (PRD §4/C3 — "or a staged candidate is true on the next cycle"; a fetch-context link is staged forever under C5, so requiring production would re-trigger search every cycle). Rejected/superseded rows never suppress; gate-reject re-enqueues discovery itself at interactive priority.
- **C1 materialization**: `_ensure_member` always writes `mfr_ref = item.mfr_ref` (may be NULL). NULL is counted in `missing_mfr_ref` on the result, never skipped (PRD §2/C1). `item_group_refs` already filters NULLs out of the comparand.
- `match_basis='singleton'` extends the `item_group_member.match_basis` documented value set (text column; comment lists existing-link|udi|basic-udi-di|name-family|manual) — non-blocking, needs a one-line handbook ratification, same posture as S1.0's `multi-group-match`.

## 4. `[FILLED — needs review]` G5a — NAME thresholds (config keys)

PHASES says "seed from Phase 0", but Phase 0 measured *document extraction*, not catalogue name-clustering — **no empirical seed exists**. Conservative defaults, config-overridable, to be recalibrated on the S1.7 sweep:

| key (`config.py` `ResolveThresholds`) | env | default | meaning |
|---|---|---|---|
| `resolve.name_accept` | `RESOLVE_NAME_ACCEPT` | `0.90` | rapidfuzz `token_set_ratio/100` ≥ -> deterministic group join |
| `resolve.name_suggest` | `RESOLVE_NAME_SUGGEST` | `0.90` | in `[suggest, accept)` -> T1 + staging; below -> singleton. **Equal to `name_accept` since 2026-08-12, so the band is empty** |
| `resolve.name_candidate_k` | `RESOLVE_NAME_CANDIDATE_K` | `20` | pg_trgm candidate cap |

Mirrors `GateThresholds`. Rationale: 0.90 token_set_ratio tolerates word-order/pack-size noise ("Tetric PowerFill A2" vs "PowerFill Tetric 20x") while staying above cross-family collisions.

**The 0.72 suggest floor this section was written around is gone** (Denis, 2026-08-12). A banded item got no `item_group_member` row, and every REF-gate path in `validate.py` joins through that table, so the band hid 5.831 of 15.958 items (36,5%) from document linking entirely — while a *worse* match below the floor stayed reachable as a singleton. `name_suggest` is now equal to `name_accept`: an item either joins a family or becomes a singleton. Everything below describing band behaviour is the design as specified, not what runs. Reversible by lowering `name_suggest` and re-running `regroup`; reopening the band is what would revive `[resolve-t1-ranking]`. **Real numbers still land in S1.7.**

## 5. `[FILLED — needs review]` grouping-suggestion persistence (unflagged gap)

No table/kind existed for a RESOLVE T1 suggestion (`manual_task` kinds are discovery/gate/dead-job; the staging tab holds docs/links/binding-candidates). New table in migration 012, matching the PRD's "-> staging" wording and keeping `manual_task` = work-items only:

```sql
CREATE TABLE grouping_suggestion (
  id          bigserial PRIMARY KEY,
  item_ref    text        NOT NULL REFERENCES item_mirror,
  candidates  jsonb       NOT NULL,            -- [{group_id, score, sample_name}]
  suggestion  jsonb,                           -- T1 output, or null on T1 error (with t1_error)
  score       real,                            -- best candidate score (the middle-band value)
  status      text        NOT NULL DEFAULT 'open',   -- open | resolved
  created_at  timestamptz NOT NULL DEFAULT now(),
  resolved_by text,
  resolved_at timestamptz
);
CREATE UNIQUE INDEX grouping_suggestion_open_item_idx ON grouping_suggestion (item_ref) WHERE status='open';
GRANT SELECT ON grouping_suggestion TO dentalia_api;   -- API renders staging; owner writes
```

**Resolution loop** (closes the feature; UI wiring is S1.6): the review UI enqueues `resolve.group {item_ref, group_id}` (additive `group_id`, payload-additive rule) at interactive priority. RESOLVE honors the forced `group_id` (basis `manual`, human is authority), writes the member, and marks the open suggestion resolved. No new job type — stays in the closed enum (invariant 7). The forced-assign branch is implemented and tested in S1.2 even though the UI is not.

**Start-new-group (S1.6 addition):** the other half of the loop — the human decides none of the candidates fit. The review UI enqueues `resolve.group {item_ref, force_new_group: true}` (additive key, mutually exclusive with `group_id`) at interactive priority. RESOLVE creates a singleton via `_create_singleton(..., basis="manual")` — same mechanism as the automatic no-match fallback, but basis `manual` (not `singleton`) since a human made the call — resolves the open suggestion, and emits `discover.group` if the new group lacks a production doc. Works even with no open suggestion (no-ops the resolve step) so the UI action never errors on an already-resolved or missing suggestion.

## 6. `[FILLED — needs review]` C4 binding hook — deferred write (invariant 1)

Handbook's `apply_manufacturer_bindings` says new items "receive the [mfr-scope] link at RESOLVE time". But **invariant 1 forbids RESOLVE writing `item_document`** — only GATE may — and `gate.candidate` cannot take a link-only candidate (it requires an extraction: `gate.py:_read_ext`). Additionally there is no queryable manufacturer->binding association yet (`document` has no manufacturer column; `gate.apply` audits bind events without the manufacturer in `detail`). So the per-new-item link write is **not wireable in S1.2 without new plumbing**.

Decision (mirrors S1.0 leaving C6 inert until a GATE write path lands): S1.2 delivers only the **canonicalization** the binding path needs — `manufacturer_alias` populated + `item_group.canonical_manufacturer` set — which directly unblocks the existing `[gate-binding]` followup (switch `_derive_mfr_scope_links` to canonical) and `[validate-canon]`. `_apply_manufacturer_bindings` is an explicit inert hook (returns 0, documented) with a new followup `resolve-c4` for the GATE-mediated write path. **RESOLVE writes no `item_document` row.**

## 7. Error taxonomy

| Situation | Handling | Queue outcome |
|---|---|---|
| `item_ref` absent from `item_mirror` | raise `LookupError` (INGEST commits the mirror row before emitting `resolve.group`; absence is a real fault, surfaced loudly not silently) | fail -> retry -> dead |
| DB / constraint / connection error | propagate | fail -> retry -> dead (queue backoff) |
| T1 adjudicator raises (SUGGEST path) | catch; persist `grouping_suggestion` with `suggestion=null`, `t1_error` recorded; do **not** raise | job succeeds; work not lost (human still adjudicates) |
| `forced group_id` not an existing `item_group` | raise `LookupError` | fail -> dead (bad UI payload; loud) |
| alias miss | insert raw as canonical, count in `aliases_created` | success |

**Idempotency (at-least-once):** alias insert `ON CONFLICT DO NOTHING`; member insert `ON CONFLICT (group_id,item_ref) DO UPDATE` (re-materialize mfr_ref/basis); singleton self-heals via `_existing_link` on retry; `grouping_suggestion` guarded by the partial-unique open-index (re-delivery no-ops); discover emit deduped by key.

**Result dict:** `{resolved: bool, basis: str|None, group_id: int|None, emitted_discover: bool, aliases_created: int, missing_mfr_ref: int, grouping_suggestions: int, bindings_applicable: int}` — missing `mfr_ref` and created aliases are reported, never silent (CLAUDE.md).

## 8. Table-driven test cases (`tests/test_resolve_handler.py`, real Postgres)

Seed helpers insert `item_mirror` / `item_group(+member)` / `manufacturer_alias` / `document(+item_document)` rows.

| # | Input state + payload | Expected writes | Expected emit |
|---|---|---|---|
| 1 | item already a member; group has production doc | member unchanged (idempotent) | none |
| 2 | item already a member; group has no doc | member unchanged | `discover.group` `discover:{gid}:0` |
| 3 | item.udi == grouped member's udi | member added, basis `udi`, mfr_ref materialized | discover if no doc |
| 4 | item.udi == existing `item_group.basic_udi_di` | member basis `basic-udi-di` | discover if no doc |
| 5 | name matches family, score ≥ 0.90 | member basis `name-family`, mfr_ref materialized | `discover.group` |
| 6 | name score in [suggest, accept); stub adjudicator | `grouping_suggestion` open row; **no** member; **no** discover | none — *unreachable while suggest == accept* |
| 7 | name score < suggest (0.90) | new singleton group + member basis `singleton` | `discover.group` |
| 8 | manufacturer_raw not in alias | `manufacturer_alias` row inserted; `aliases_created=1` | (per resulting band) |
| 9 | payload `{item_ref, group_id}` (forced) + open suggestion exists | member basis `manual`; suggestion -> resolved | discover if group lacks doc |
| 10 | `item_ref` not in `item_mirror` | — | `LookupError` (job fails) |
| 11 | accept path, item.mfr_ref NULL | member with mfr_ref NULL; `missing_mfr_ref=1`; `item_group_refs` excludes it | discover if no doc |
| 12 | SUGGEST band, adjudicator raises | suggestion persisted `suggestion=null`+`t1_error`; job succeeds | none |
| 13 | canonicalization: alias hit maps raw->canonical | singleton's `item_group.canonical_manufacturer` = canonical (not raw) | — |

Every ladder rung (1-5,7) and both staging/singleton branches (6,7) covered, plus null/edge (10,11,12) and the C1 comparand (11) and canonicalization (13).
