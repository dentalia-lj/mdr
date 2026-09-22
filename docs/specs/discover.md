# Unit spec — `discover.group` (S1.3)

Normative sources (this spec adds only what they leave open — do not restate):
- Handbook §3 `docs/dentalia-job-type-handbook.md` (pseudo-code, source ladder, output examples).
- PRD v3 §3 `docs/dentalia-pipeline-contract-prd-v3.md` (Consumes/Emits/Writes/AI rows) + §4 (the `fetch.url` payload DISCOVER produces).
- Schema `migrations/003_discovery_fetch.sql` (`discovery_log`, `fetch_log`, `eudamed_mirror`), `002_ingest.sql` (`item_group`, `item_group_member`), `006_phase2_declared.sql` (`manufacturer`), `009_manual_task.sql` (`manual_task`, kind `discovery-dead-end`).
- Invariants: 6 (unchanged content never re-fetched — the fetch ledger is the guard), 7 (closed job enum), 8 (active-scope dedupe), 11 (nothing downstream knows which adapter is live — the SearchAdapter), 12 (LLM = ranking only; the fetch/no-fetch decision is a threshold rule).
- PHASES.md **G6** (HTML doc-library) is already `[FILLED — needs review]` — this spec **does not re-decide it**; it records only how the S1.3 MVP consumes that decision (§7: HTML-library tiers are FETCH/S1.4 + a deferred DISCOVER `landing_page` source; the S1.3 ladder ships without them).

## 1. Module layout & signatures — `app/handlers/discover.py`

```python
@dataclass(frozen=True)
class GroupFacts:
    group_id: int
    manufacturer: str            # item_group.canonical_manufacturer (handbook's group.manufacturer)
    label: str | None            # item_group.label
    basic_udi_di: str | None
    member_mfr_refs: list[str]   # for prefilled-link context + future ranking signal
    known_urls: list[str]        # url_normalized of every prior fetch of a doc linked to a group member

def handle_discover_group(conn, job, *, search_adapter=None, rank=None) -> dict   # entry; register("discover.group", ...)

def _load_group(conn, group_id) -> GroupFacts                       # raises LookupError if group absent

# ladder (each returns list[str] of candidate URLs, or [] for a miss)
def _source_priority(cfg, manufacturer) -> list[str]                # config per-mfr, else Discovery.default_source_priority
def _stale_known_urls(conn, known_urls, cfg) -> list[str]           # subset NULL/older-than recency_days (recency guard)
def _eudamed_lookup(conn, facts) -> list[str]                       # eudamed_mirror.cert_lookup; empty mirror in Phase 1
def _search(conn, facts, cfg, search_adapter, rank) -> list[str]    # adapter.query -> threshold_topk(rank(...))
def _contact_known(conn, manufacturer) -> list[str]                 # manufacturer.contact_emails (empty in Phase 1)

# writes / emits
def _log(conn, group_id, source, outcome, detail=None) -> None      # discovery_log insert (outcome: hit|miss|skipped)
def _emit_fetch(conn, facts, urls, source_rank) -> int              # one fetch.url per url; returns count
def _emit_email_request(conn, facts, contacts) -> None
def _push_manual(conn, facts, sources_tried) -> bool                # manual_task discovery-dead-end (guarded); False if one already open
def _prefilled_search_links(facts) -> list[str]
```

**Shared URL util — new `app/urls.py`** (DISCOVER is the first emitter of real web URLs; FETCH/S1.4 consumes the same `fetch:{url_normalized}` dedupe key). A mismatched normalization between the two would double-fetch the same document (breaks invariant 6/8), so the normalizer must be shared, not re-implemented per handler:

```python
def normalize_url(url: str) -> str   # lowercase scheme+host, strip default port + fragment, keep path+query verbatim, drop a lone trailing "/"
def domain_of(url: str) -> str       # lowercased host (the fetch.url `domain` field / domain-lease key)
```
Conservative on purpose — query strings stay (a `/download?id=` link is identity-bearing), no `utm_*` stripping in the MVP (followup `url-normalize-refine`). FETCH must import these, not re-derive them.

**SearchAdapter + ranker injection** (invariants 11 + 12, testability — same seam pattern as `resolve`/`extract`/`gate`): `handle_discover_group(conn, job, *, search_adapter=None, rank=None)`.
- `search_adapter.query(q) -> list[SearchCandidate]` — default `make_search_adapter(cfg)` (Brave; §`app/adapters/search.py`). Tests inject `FakeSearchAdapter`; no live call in tests.
- `rank(facts, candidates) -> list[tuple[SearchCandidate, float]]` — the **T1 candidate ranking** (PRD §3 AI row). Default is the **deferred** ranker (raises `NotImplementedError`, followup `discover-t1-ranking`), mirroring RESOLVE's deferred adjudicator. See §4 for the degrade-to-manual behaviour.

## 2. The source ladder — what the handbook leaves implicit

Order comes from `_source_priority(cfg, manufacturer, playbook)`: the playbook's own `source_priority` key first, then config `[source_priority]` per canonical manufacturer, then `Discovery.default_source_priority`. Loop semantics: a **miss** logs + continues; a **hit** logs + emits `fetch.url` per URL + **returns** (stop ladder); `email`/`manual` are **terminal returns**; `vendor` is a **skip** that never emits; `recency` is a guard that can also **C3-link** (2026-08-24 defect fix): when everything the group knows is ledger-fresh AND already extracted it emits `validate.doc` and returns, otherwise it logs `skipped`/nothing and continues; `playbook` emits like any other rung (2026-08-21).

Phase-1 default ladder = `["recency", "known_url", "eudamed", "search", "email", "manual"]`. Two PRD ladder rungs are **intentionally omitted from the default** (still tolerated if a config list names them — they log `skipped`):
- **`playbook`** — when the playbook authors a `crawl` block, the rung takes the crawl path instead: robots check → domain lease (new for DISCOVER; a held lease defers the whole `discover.group` job, exactly as FETCH defers its own) → fetch the index page (+ paginate if authored) through the existing `Fetcher` → `app.crawl.harvest` → emit `fetch.url` per surviving link → one `discovery_log` row with the full count breakdown (`docs/superpowers/specs/2026-08-21-playbook-crawl-recipe-design.md` §3/§4.6). Otherwise it emits `fetch.url` for every authored `kind:"direct"` doc_source and returns; logs `miss` and continues when the manufacturer has no playbook, no `direct` source, and no `crawl` block. It never fetches a `kind:"portal"` source: a portal is a listing page, and that exclusion is also what enforces the 2026-08-20 robots ruling for the four refused manufacturers. The crawl recipe is wired as of this slice; no delivered playbook authors a `crawl` block yet (that is slice 5), so the branch is inert in production today.
- **`vendor`/`distributor`** — folded into `search` per the G6 decision (distributor certs surface in unrestricted SERP under `source_rank="search"`; no separate fetch/extract mechanism in the MVP). A configured `vendor` rung logs `skipped`. Promotion to a pinned-domain `known_url` variant is deferred until a distributor-only manufacturer appears (PHASES G6 open item).

Per-rung Phase-1 behaviour:

| rung | Phase-1 realized behaviour |
|---|---|
| `recency` | Guard first, C3 producer second (2026-08-24). All known rows fresh (vacuously true when the group has no linked docs) and **not** `ignore_recency` → collect fresh ledgered hashes from the known rows plus the playbook's `direct` URLs; every hash with an extraction gets `validate.doc {content_hash, extract_rev, group_id, archive_url?}` (via `archiving.emit_validate` — same emission as FETCH's hash-dedupe branch, incl. the stored-copy handle), log `hit`, resolve any open dead-end task, **return** (outcome `validate`). Nothing extracted → log `skipped` (when known rows exist), continue — the fetch ledger stays the real re-fetch guard (invariant 6). Before this the rung never emitted, and a group whose document was first fetched for another group ended its ladder silently (measured: RENFERT groups 3138/3139/3140/5254 never linked to doc 843 — the playbook rung's `fetch.url` deduped against another group's in-flight fetch). |
| `known_url` | `_stale_known_urls` (URLs never checked or older than `recency_days`) → `fetch.url` each, `source_rank="known_url"`. Empty in early backfill-first Phase 1. |
| `eudamed` | `eudamed_mirror.cert_lookup(basic_udi_di or member udis)`. Mirror is empty until `eudamed.sync` (S2.3) → deterministic `miss`. Wired now so the ladder is complete. |
| `search` | Query `f'{manufacturer} "{label}" declaration of conformity pdf'` → `adapter.query` (cap `max_search_candidates`) → `threshold_topk(rank(...), k=topk)` → `fetch.url` each, `source_rank="search"`. Ranker deferred → §4. |
| `email` | `_contact_known` non-empty → emit `email.request`, log, **return**. Phase-1 `manufacturer` table empty → never fires. |
| `manual` | Terminal. `_push_manual` (kind `discovery-dead-end`, `payload={prefilled_search_links, manufacturer, label, sources_tried}`), log, **return**. **No job emitted** — the human is the next stage (handbook §3). |

## 3. Emitted jobs & writes

- **`fetch.url`** `{url, domain, group_id, source_rank}`, dedupe_key `fetch:{normalize_url(url)}`, default (normal) priority. `domain = domain_of(url)`. One per URL (≤ `topk` on the search path). Two groups discovering the same URL dedupe to a single fetch (invariant 8, active-scope) — FETCH's C3 hash-dedupe then re-enters `validate.doc` for the second group. **This is the payload FETCH consumes (PRD §4) — field names are frozen by that contract.**
- **`email.request`** `{group_id, manufacturer, contacts, reason:"discovery-exhausted"}`, dedupe_key `email.request:{group_id}`. Consumer is S2.4 (noop until then); DISCOVER only produces it, and only when contact is known. Payload is additive — S2.4 owns the final shape.
- **`manual_task`** row (not a job): `INSERT (kind='discovery-dead-end', group_id, payload)`; guarded by a "no open `discovery-dead-end` for this group" check (mirrors `gate._push_manual`), so a re-delivered job never stacks duplicate rows.
- **`manual_task` resolution (S1.6 addition):** migration 009's contract — "resolving one enqueues a job and a handler marks the task resolved" — was unimplemented for this kind until S1.6 wired a manual-tab resolve action. Any terminal outcome that is **not** `manual` (recency C3/known_url/eudamed/search/email hit) now resolves the group's open `discovery-dead-end` task (`_resolve_dead_end_task`), covering the case where a human re-enqueues `discover.group` from the manual tab and a different rung now hits. An outcome that lands on `manual` again leaves the existing open task alone (or creates a fresh one per the guard above) — never resolves it.
- **`discovery_log`**: one row per rung tried — `(group_id, source, outcome ∈ hit|miss|skipped, detail jsonb)`. `detail` carries `{n_candidates, n_ranked}` for search, `{urls}` for hits, `{prefilled}` for manual, `{recency_days}` for a recency skip, `{recency_days, content_hashes, urls}` for a recency C3 hit. Feeds the hit-rate stats + failure monitor (PRD §3 Writes). `detail` shape is additive.

## 4. T1 ranker — live seam, degrade-to-manual

PRD §3: "AI: T1 candidate ranking only; the fetch/no-fetch decision is a threshold rule." Both halves ship: `threshold_topk` is the rule, and the live T1 call landed 2026-09-02 in `app/extract/ranking.py` (`AnthropicRanker`, `models.rank`, structured output). RESOLVE's `resolve-t1-ranking` was closed as superseded rather than built, so this is now the only live ranker in the tree. Split of failure handling:

- **Search not configured** (`SearchNotConfigured` — the selected adapter has no API key) → **caught in the handler**; `discovery_log` search row logged `skipped`; ladder continues → `manual`. This is distinct from an HTTP failure: in Phase-1 backfill-first with no Brave key set, the whole catalogue flows to `manual` instead of a wall of dead jobs. Enabling search = setting the key.
- **`search_adapter.query()` HTTP/network error** (key present, provider down/4xx/5xx) → **propagate** → queue backoff (fail → retry → dead). A real transient fault, handled like any DB/connection error.
- **`rank()` error** (no API key, HTTP failure, a response so truncated it will not parse) → **caught in `_search`**; `discovery_log` search row logged `miss` with `detail.rank_error`; treated as a search miss → ladder continues → `manual`. Work is never lost. This is the same degrade path the deferred `NotImplementedError` used to take, kept deliberately: wiring the ranker can cost a group its search rung, never the group.

`threshold_topk` keeps candidates with `score ≥ discover.rank_threshold`, best `topk` first.

**Scores are matched by index, never zipped.** The model answers `{index, score, reason}` per candidate rather than a bare score list, and `_scores_by_index` drops out-of-range, duplicate and non-numeric indices, clamps to `[0, 1]`, and leaves an unanswered candidate at `0.0` without removing it from the returned list. A model that returned nine scores for ten candidates would otherwise shift every score onto the wrong URL, and a shifted `0.95` is a fetch of the wrong document — the one failure here that spends money.

**Measured live 2026-09-02**, two real doc-less groups, one Brave call plus one Haiku call each:

| Group | Candidates | Above threshold | Cost |
|---|---|---|---|
| 497 DUERR DENTAL `VC 65 SURGICAL SUCTION SYSTEM` | 10 | 1 (a `.pdf` at 0.65) | $0.0042 |
| 74 HENRY SCHEIN `ALPHASIL PERFECT ACTIVATOR 60ML` | 10 | 0 | $0.0035 |

Both are the right answer. In 497 the general catalogues and brochures on `pdf.medicalexpo.com` scored `0.00` and the download-centre and product pages `0.10–0.20`, exactly as the prompt directs; in 74 nothing cleared, because Henry Schein is a distributor and Alphasil's legal manufacturer is someone else. **~$0.004 per group is the planning number**: ranking every one of the 8.208 groups once is roughly $33, against a €10–40/month steady-state target, so a full sweep is a one-off budget decision and not a recurring cost.

## 5. `[FILLED — needs review]` — recency `continue` vs `return`

Handbook: `case "recency": if fetch_log.checked_recently(...): log("skipped"); continue`. Literal `continue` (not `return`) means recency never skips the whole cycle — it only avoids re-emitting freshly-checked URLs, and the ladder still falls through to `search` for a doc-less group whose known URLs went stale/dead. This spec implements exactly that: `recency` is a guard that, when all known URLs are fresh, logs `skipped` and continues (amended 2026-08-24: unless the fresh content is already extracted — then it C3-links via `validate.doc` and returns, see §2); the `known_url` rung then emits only the **stale** subset (`_stale_known_urls`). Either reading is correct under invariant 6 (FETCH's ledger skips a fresh URL regardless), but `continue` + stale-filtering is strictly cheaper (no redundant enqueue) and preserves discovery for the dead-known-URL case. Needs one-line handbook ratification.

## 6. `[FILLED — needs review]` — Discovery config keys (no Phase-0 seed)

Phase 0 measured document extraction, not discovery — **no empirical seed exists**. Conservative, config-overridable, recalibrated on the S1.7 sweep. New `Discovery` dataclass in `config.py` (mirrors `ResolveThresholds`); `Connection.serper_api_key` added; `Adapters.search` already exists (`"brave"`).

| key (`config.py` `Discovery`) | env | default | meaning |
|---|---|---|---|
| `discover.topk` | `DISCOVER_TOPK` | `3` | max `fetch.url` emitted from the search rung (handbook `k=3`) |
| `discover.rank_threshold` | `DISCOVER_RANK_THRESHOLD` | `0.60` | min T1 score to fetch a search candidate |
| `discover.recency_days` | `DISCOVER_RECENCY_DAYS` | `30` | known-URL re-check window (the recency guard) |
| `discover.max_search_candidates` | `DISCOVER_MAX_SEARCH_CANDIDATES` | `10` | cap on candidates pulled from the adapter before ranking |
| `discover.default_source_priority` | (TOML only) | `["recency","known_url","eudamed","search","email","manual"]` | fallback ladder when a manufacturer is absent from `[source_priority]` |
| `connection.serper_api_key` | `SERPER_API_KEY` | `""` | Serper fallback adapter key (selected via `adapters.search="serper"`) |

**Needs Denis sign-off; real numbers land in S1.7.**

## 7. G6 consumption (no new decision — records the MVP boundary)

Per the `[FILLED — needs review]` G6 block in PHASES.md: the deterministic HTML-doc-library tiers (D0/D/R, BeautifulSoup link enumeration) live in **FETCH (S1.4)**, and the LLM `landing_page` tier-A source lives in DISCOVER but is **deferred** (MVP routes inconclusive → `manual_task`). Therefore **S1.3 ships the classic source ladder only** — no BS4, no markitdown, no `landing_page` source, no `hop` counter. Those, plus the FETCH `Emits += fetch.url/discover.group` deltas, remain gated on Denis's sign-off and are not touched here. This spec's `manual` terminal **is** the MVP's inconclusive-path sink that G6 specifies.

## 8. Error taxonomy

| Situation | Handling | Queue outcome |
|---|---|---|
| `group_id` absent from `item_group` | raise `LookupError` (RESOLVE/GATE commit the group before emitting; absence is a real fault) | fail → retry → dead |
| search adapter has no key (`SearchNotConfigured`) | catch; log search `skipped`; continue → manual | job succeeds; group → manual |
| `search_adapter.query()` HTTP/network error (key present) | propagate | fail → retry → dead (backoff) |
| `rank()` raises (incl. deferred default) | catch; log search `miss` + `detail.rank_error`; continue → manual | job succeeds; work not lost |
| DB / constraint / connection error | propagate | fail → retry → dead |
| `eudamed_mirror` empty / no match | deterministic `miss` | success (continue ladder) |
| all rungs miss, no contact | `manual_task` written, no job emitted | success |

**Idempotency (at-least-once):** `fetch.url` deduped by `fetch:{url_normalized}`; `manual_task` guarded by the open-task check; `discovery_log` is append-only telemetry (duplicate rows on retry are acceptable and expected — it records *attempts*, not state). A re-delivered `discover.group` re-walks the ladder; because emits are deduped and the manual insert is guarded, the only visible effect of re-delivery is extra `discovery_log` attempt rows.

**Result dict:** `{group_id, terminal_source: str, outcome: "fetch"|"validate"|"email"|"manual"|"skipped", emitted_fetch: int, emitted_email: bool, manual_task: bool, sources_tried: list[str], candidates_seen: int, emitted_validate: int}` — every rung tried and every emit counted, never silent (CLAUDE.md). (`validate` outcome + `emitted_validate` added with the 2026-08-24 recency-C3 fix.)

## 9. Table-driven test cases (`tests/test_discover_handler.py`, real Postgres)

Seed helpers insert `item_group(+member)`, `document(+item_document)` + `fetch_log(doc_id)` for known-URL cases, `eudamed_mirror`, `manufacturer` (contact) rows. `FakeSearchAdapter(candidates)` and a stub `rank` injected where the path needs them.

| # | Input state + payload | Expected writes | Expected emit |
|---|---|---|---|
| 1 | group with a stale known_url (last_checked NULL) | `discovery_log` known_url `hit` | `fetch.url` `{source_rank:"known_url"}`, dedupe `fetch:{norm}` |
| 2 | group known_urls all checked < `recency_days` ago, no other source hits | `discovery_log` recency `skipped` + known_url `miss` + eudamed `miss` + search `miss` + manual | `manual_task` (dead-end); **no fetch.url** |
| 3 | eudamed_mirror seeded with the group's `basic_udi_di` | eudamed `hit` | `fetch.url` `{source_rank:"eudamed"}` |
| 4 | search + injected `rank` returning 2 over-threshold, 1 under | search `hit` (`n_ranked=2`) | 2× `fetch.url` `{source_rank:"search"}` (≤ topk) |
| 5 | search + default (deferred) rank; FakeSearchAdapter returns candidates | search `miss` + `detail.rank_error`; then `manual_task` | no fetch.url; manual_task written |
| 6 | all rungs miss, `manufacturer` has `contact_emails` | email `hit` | `email.request` `{group_id,...}`, dedupe `email.request:{gid}`; **no manual_task** |
| 7 | all rungs miss, no contact | manual `hit` | `manual_task` dead-end, prefilled links in payload; no job |
| 8 | `group_id` not in `item_group` | — | `LookupError` (job fails) |
| 9 | payload missing `manufacturer_id`/`context` (RESOLVE emits bare `{group_id}`) | handler loads facts from DB, runs normally | per resulting rung (tolerates missing fields, invariant/PRD additive rule) |
| 10 | re-delivered job after case 7 already left an open dead-end task | no second `manual_task` (guard); extra `discovery_log` attempt rows only | no duplicate manual_task |
| 11 | `search_adapter.query` raises (fake set to raise) | propagates | job fails → retry/dead |
| 12 | two groups emit the same search URL | second `fetch.url` deduped by key | single `fetch.url` row (active-scope dedupe) |
| 13 | configured ladder names `vendor` | logs `skipped`; ladder proceeds | per resulting rung |
| 13a | `playbook` rung, manufacturer has authored `kind:"direct"` sources | logs `hit`; one `fetch.url` per URL; returns | `test_playbook_rung_emits_authored_direct_urls` |
| 13b | `playbook` rung, only `kind:"portal"` authored (or no playbook) | logs `miss`; ladder proceeds | `test_the_playbook_rung_never_fetches_a_portal` |
| 14 | end-to-end through the runner (`run_once`): seed group, enqueue `discover.group`, claim+dispatch | job `done`; `discovery_log` + emit present | as per the seeded rung |

Every rung (known_url, recency, playbook, eudamed, search, email, manual, vendor skip) covered, both search branches (ranked hit / deferred→manual), null/edge (8, 9), idempotency (10, 12), infra-error (11), and the runner path (14).
