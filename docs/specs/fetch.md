# Unit spec — `fetch.url` (S1.4)

Normative sources (this spec adds only what they leave open — do not restate):
- Handbook §4 `docs/dentalia-job-type-handbook.md` (pseudo-code, outcome table, sibling producers).
- PRD v3 §4 `docs/dentalia-pipeline-contract-prd-v3.md` (Consumes/Emits/Writes/Politeness/AI, C3 hash-dedupe row) and §9 (archive layout).
- Schema: `migrations/003_discovery_fetch.sql` (`fetch_log`), `001_queue.sql` (`domain_lease` incl. `needs_playwright`), `004_extraction.sql` (`extraction_attempt`), `005_registry.sql` (`document` — for the C3 doc link).
- Queue API: `app/queue.py` (`try_domain_lease`, `enqueue`, `defer`, `finish`, `fail`); `app/extract/tiers.py::next_extract_rev` (C3 `latest_rev = MAX(extract_rev)`).
- Invariants: 6 (unchanged content never re-fetched — the ledger is the mechanism), 11 (nothing downstream knows which StorageAdapter/transport is live), 12 (**FETCH is AI:none — pure I/O**; LLMs never fetch).

FETCH is a producer only: it writes `fetch_log` + the archived file, and emits `extract.doc` / `validate.doc`. It never writes `document`/`item_document`/`evidence` (invariant 1).

## 1. Module layout & signatures

### `app/handlers/fetch.py`
```python
def handle_fetch_url(conn, job, *, fetcher=None, store=None) -> dict   # register("fetch.url", ...)

# ledger
def _ledger_get(conn, url_norm) -> dict | None                 # fetch_log row or None
def _ledger_upsert(conn, url_norm, *, etag, last_modified, content_hash, source) -> None
def _ledger_touch(conn, url_norm) -> None                      # 304: bump last_checked_at only
def _hash_extracted_rev(conn, content_hash) -> int | None      # latest extract_rev if an extraction exists, else None
def _link_existing_doc(conn, url_norm, content_hash) -> None   # best-effort: set fetch_log.doc_id from document.content_hash
def _checked_within(row, window_days) -> bool                  # last_checked_at >= now()-window

# transport selection / bot-wall
def _domain_needs_playwright(conn, domain) -> bool
def _flag_domain_playwright(conn, domain) -> None

# archive path (PRD §9)
def _manufacturer_for_group(conn, group_id) -> str             # item_group.canonical_manufacturer | "unknown"
def _archive_path(manufacturer, content_hash, url, content_type) -> str
def _guess_type_dir(url, content_type) -> str                  # coarse human bucket — see §3.4
def _filename(url, content_type) -> str
```
`handle_fetch_url(conn, job, *, fetcher=None, store=None)` — **fetcher/store injected for tests** (same pattern as `resolve`/`extract`/`gate` inject their LLM; Postgres is never mocked, network always is). Defaults build the live transport + `make_storage_adapter(cfg)` from config. Tests pass a `FakeFetcher` (canned responses) and a `LocalFsStore(tmp)`.

### `app/adapters/fetcher.py` (new — FETCH-internal transport, injectable)
```python
@dataclass(frozen=True)
class FetchResult:
    status: int                 # 200 | 304 | 403 | ...
    body: bytes | None          # None on 304 / bot-wall
    etag: str | None
    last_modified: str | None
    content_type: str | None
    final_url: str
    @property
    def bot_wall(self) -> bool  # status == 403 (see §3.3)

class Fetcher(Protocol):
    def get(self, url, *, etag=None, last_modified=None) -> FetchResult: ...

class HttpxFetcher:      ...   # conditional GET via httpx (If-None-Match / If-Modified-Since)
class PlaywrightFetcher: ...   # render tier; only for needs_playwright domains / bot-wall fallback
def make_fetcher(cfg) -> Fetcher            # httpx by default
def normalize_url(url: str) -> str          # SHARED with S1.3 dedupe key — see §3.5
```

### `app/adapters/storage.py` (new — StorageAdapter, invariant 11)
```python
class StorageAdapter(Protocol):
    def put(self, body: bytes, path: str) -> str: ...   # returns archive_url; idempotent

class LocalFsStore:      ...   # writes {root}/{path}; returns f"{base_url}/{path}"; idempotent (content-addressed)
class GoogleDriveStore:  ...   # SKELETON — __init__ ok, put() raises NotImplementedError("G7") — see §2
def make_storage_adapter(cfg) -> StorageAdapter    # cfg.adapters.storage: "local"|"gdrive"
```

## 2. `[FILLED — needs review]` G7 — Drive auth deferred; ship LocalFsStore + skeleton

G7 (service-account vs OAuth, root folder) is **client-gated** and stays open in the register. To let the FETCH handler, ledger, C3 dedupe, and Playwright tier land and be fully tested now, S1.4 ships:
- `StorageAdapter` protocol + `LocalFsStore` (real §9 path logic, used by tests and local dev) + `make_storage_adapter` factory.
- `GoogleDriveStore` as a **skeleton whose `put()` raises `NotImplementedError("G7: Drive auth unresolved")`** — mirrors S1.1's `BcApiAdapter` stub and S1.0/S1.2's inert hooks.

`cfg.adapters.storage` default stays `"gdrive"` (production intent, per config) but instantiating it is inert until G7; tests/dev set `"local"`. When G7 is answered, only `GoogleDriveStore` is implemented — the handler, path logic, and tests are untouched (invariant 11). Followup `fetch-gdrive` tracks the live impl.

## 3. What the handbook leaves implicit

### 3.1 Transport selection + fallback ladder (handbook `via=playwright if domain_flagged else httpx`)
1. `via = PlaywrightFetcher if _domain_needs_playwright(domain) else HttpxFetcher` (default `fetcher` injection resolves this; tests inject directly).
2. Fetch. If the httpx result is a **bot-wall** (§3.3) and the domain was **not** pre-flagged: retry the same URL once via Playwright **inline**.
   - Playwright succeeds → continue the normal flow **and** `_flag_domain_playwright(domain)` so future jobs skip httpx (persists with `finish`).
   - Playwright also bot-walls/errors → **raise `FetchError`** → queue `fail` → backoff → dead (poison guard). The URL surfaces as a dead job (manual tab). See §3.3 for why the flag is not written on the double-failure path.

### 3.2 Recency skip **must preserve the requesting group** (C3 parity — corrects the literal pseudo-code)
The handbook's `recency-skip` returns with **no emit**. Taken literally that orphans any *other* group that later discovers the **same URL** within the recency window — exactly the manufacturer-/QMS-level-cert case where one document covers many groups — re-creating the infinite-rediscovery loop C3 exists to kill. So:
- If `_ledger_get(url)` has a `content_hash`, was `_checked_within(recency_window_days)`, **and** `_hash_extracted_rev(hash)` is not None → **emit `validate.doc {content_hash, extract_rev: latest, group_id, archive_url?}`** (dedupe `validate:{hash}:{rev}:{group_id}`; `archive_url` = the stored copy's handle from `document` by `content_hash` when the registry holds one — additive 2026-08-24, so GATE never falls back to the remote source url) for the requesting group, then skip the network. Outcome `recency-skip-linked`.
- If no extraction exists yet (a same-cycle in-flight fetch) → plain `recency-skip`, no emit; self-heals next cycle (the group still lacks a doc → discover → fetch → now extracted → link). No work is permanently lost (registry-derives-queue).
- `group_id` null (not from `discover.group`) → plain recency-skip.

This unifies recency-skip with the C3 hash-dedupe path below. **Needs review + a one-line handbook clarification** (`recency-skip` → `recency-skip-linked` when an extraction exists).

### 3.3 Bot-wall definition + why the flag is success-only
- **Bot-wall = HTTP `403`.** `429` and `5xx` and timeouts are **transient** → `raise` → queue backoff/retry (not a Playwright trigger). A `200` JS-challenge body is not reliably detectable deterministically; deferred (followup `fetch-js-challenge`) rather than guessed. **Needs review.**
- `needs_playwright` is flagged **only on a successful Playwright fallback** (bytes obtained). On persistent double-failure the job dead-letters per-URL and the flag is *not* written — the runner rolls back a raising handler's writes, so a flag-then-raise in the same tx would be lost, and a side-channel autocommit write is deliberately avoided (don't gold-plate). Consequence: a bot-walled domain gets flagged after its **first** Playwright-crackable success; a domain Playwright also can't crack goes dead per-URL (correct — needs manual intervention). Followup `fetch-domain-flag-persist` if the sweep shows churn.

### 3.4 Archive path (PRD §9 `/{manufacturer_canonical}/{doc_type}/{hash[:12]}__{filename}`)
- **manufacturer**: `_manufacturer_for_group(group_id)` → `item_group.canonical_manufacturer` (RESOLVE set it); `"unknown"` if group_id null/absent. Filesystem-sanitized.
- **doc_type dir**: FETCH **cannot know** the authoritative type (DoC/EC/IFU/ISO) — that is EXTRACT/GATE's output. `_guess_type_dir` is the handbook's `guess_type_dir`: a **coarse human-facing bucket** from URL/anchor tokens (`ifu`→`ifu`, `declaration`/`doc`→`doc`, `cert`→`ec`, `iso`→`iso`, else `unknown`). The archive is content-addressed by the hash prefix, and `document.type` in the DB is authoritative; the dir is cosmetic and permanent (append-only, never re-filed). `[FILLED — needs review]`.
- **filename**: basename of the URL path, sanitized; extension from content-type when missing; falls back to `{hash[:12]}.pdf`.

### 3.5 URL normalization is shared with S1.3 (dedupe-key coupling)
`fetch_log.url_normalized` (PK, this handler) and the `fetch:{url_normalized}` dedupe key (set by the **DISCOVER** producer, S1.3) must use the **same** `normalize_url`, or the ledger and dedupe diverge. S1.4 defines `normalize_url` in `app/adapters/fetcher.py` (lowercase scheme+host, strip fragment, drop default ports, keep path+query). Followup `fetch-url-normalize-shared`: S1.3 imports this for its dedupe key. **Coordination point with the parked s1.3-discover worktree** (same class as the shared-httpx point).

## 4. Outcome → queue disposition (maps handbook/PRD §4 outcome table to the runner protocol)

Handlers return a dict; the runner `finish`es unless `{_deferred: True}` (handler already `defer`red) or the handler raises (`fail`→backoff→dead). No `job.finish()` self-call.

| Case | Ledger effect | Emits | Queue outcome |
|---|---|---|---|
| Domain lease not acquired (`try_domain_lease` False) | none | none | `queue.defer(politeness_ms/1000)`; return `{_deferred: True}` (politeness wait, no attempt penalty) |
| Recency hit + hash extracted (§3.2) | none (skip network) | `validate.doc {content_hash, extract_rev, group_id, archive_url?}` | `finish` — `recency-skip-linked` |
| Recency hit, no extraction yet / no group | none | none | `finish` — `recency-skip` |
| `304 Not Modified` (conditional GET) | `_ledger_touch` (last_checked_at) | none | `finish` — `not-modified` |
| **Same hash, new URL** (C3), extraction exists | `upsert` + `_link_existing_doc` | `validate.doc {content_hash, extract_rev: latest, group_id, archive_url?}` (basis `fetch-context` unless REF/UDI matches — GATE decides; caps at staging §7) | `finish` — `hash-dedupe` |
| New content hash | `upsert` + `store.put` archive | `extract.doc {archive_url, content_hash, group_id, source_url}` dedupe `extract:{hash}` | `finish` — `fetched` |
| Bot-wall (403), Playwright fallback succeeds | `upsert` (+archive or dedupe as above) + flag domain | per new-hash / C3 row | `finish` |
| Bot-wall persistent (both fail) | none | none | `raise FetchError` → `fail` → backoff → dead |
| `429`/`5xx`/timeout | none | none | `raise` → `fail` → backoff → retry/dead |

## 5. Config additions (`app/config.py`)
- `class Storage`: `local_root: str` (archive dir for `LocalFsStore`, default `./archive`), `base_url: str` (prefix for returned `archive_url`, default `file://{abspath}`). Drive fields (folder id, creds path) deferred to G7.
- `Adapters.storage` already exists (`"gdrive"`); factory adds `"local"`.
- Reuses existing `Fetch.politeness_ms` (lease) and `Fetch.recency_window_days` (recency). No new fetch keys.
- No new migration — `fetch_log`, `domain_lease.needs_playwright`, `extraction_attempt`, `document.content_hash` all exist.

## 6. Error taxonomy

| Situation | Handling | Queue outcome |
|---|---|---|
| Domain lease busy | `queue.defer(conn, id, politeness_s)`; return `{_deferred: True}` | pending, retried after politeness (no attempt penalty) |
| Persistent bot-wall (httpx 403 + Playwright fail) | `raise FetchError` | fail → backoff → dead (surfaces in manual/dead tab) |
| Transient HTTP (429/5xx/timeout/conn error) | propagate | fail → backoff → retry → dead |
| `GoogleDriveStore.put` before G7 | `NotImplementedError` propagates | fail → dead (only if misconfigured to `gdrive` in a live run; loud) |
| Malformed payload (missing `url`/`domain`) | `raise KeyError`/`ValueError` | fail → dead (bad producer; loud, never silent) |
| `store.put` idempotent re-run (same path exists) | no-op, return existing `archive_url` | success (at-least-once safe) |

**Idempotency (at-least-once):** ledger `upsert` is `ON CONFLICT (url_normalized) DO UPDATE`; `store.put` is content-addressed + idempotent; `extract`/`validate` emits are deduped by their keys; a re-delivered job that already recorded the hash re-enters the C3 path (hash now `hash_seen_before`) and emits `validate.doc` (deduped) rather than re-archiving.

**Result dict:** `{outcome: str, content_hash: str|None, archive_url: str|None, emitted: str|None, via: "httpx"|"playwright"|None, bytes: int|None, group_id: int|None}` — `outcome` ∈ {recency-skip, recency-skip-linked, not-modified, hash-dedupe, fetched, lease-wait}. Reported/logged like every handler.

## 7. Table-driven test cases (`tests/test_fetch_handler.py`, real Postgres, `FakeFetcher` + `LocalFsStore(tmp)`)

Seed helpers insert `item_group` (for manufacturer), `fetch_log`, `extraction_attempt`, `document` rows. `FakeFetcher` maps url→scripted `FetchResult` and records calls (assert no network on skip paths).

| # | Input state + payload | Expected writes | Expected emit / outcome |
|---|---|---|---|
| 1 | domain lease held by a prior fetch (busy) | none | `_deferred`; job re-pending; fetcher **not** called |
| 2 | ledger row checked 2 days ago, hash has extraction (rev 3); different `group_id` | none | `validate.doc {hash, extract_rev:3, group_id}`; outcome `recency-skip-linked`; fetcher not called |
| 3 | ledger row recent, **no** extraction for hash | none | no emit; outcome `recency-skip`; fetcher not called |
| 4 | fetcher returns `304` (ETag matched) | `fetch_log.last_checked_at` bumped, content_hash unchanged | none; outcome `not-modified` |
| 5 | fetcher returns `200` new body, hash unseen; group has canonical_manufacturer "Ivoclar" | `fetch_log` new row (etag/hash); archived file at `Ivoclar/<dir>/<hash12>__<name>` | `extract.doc {archive_url, content_hash, group_id, source_url}` dedupe `extract:{hash}`; outcome `fetched` |
| 6 | fetcher `200`, hash already in `fetch_log` on another URL **and** extraction exists (rev 2) | `fetch_log` new URL row, `doc_id` linked if a document with that hash exists | `validate.doc {hash, extract_rev:2, group_id}`; **no** archive, **no** extract; outcome `hash-dedupe` |
| 7 | httpx `403`; domain not flagged; injected Playwright fetcher returns `200` body | `fetch_log` row; archive; `domain_lease.needs_playwright=true` | `extract.doc`; `via=playwright`; outcome `fetched` |
| 8 | domain pre-flagged `needs_playwright=true` | Playwright fetcher used directly (httpx never called) | per body (new-hash → `extract.doc`) |
| 9 | httpx `403`, Playwright also `403` | none (rolled back) | `FetchError` raised → job fails; `needs_playwright` **not** set |
| 10 | fetcher `503` | none | raises → job fails (transient backoff) |
| 11 | new hash, `group_id` null (defensive) | archive; `fetch_log` | `extract.doc` with `group_id: null`; manufacturer dir `unknown` |
| 12 | payload missing `url` | none | `KeyError`/`ValueError` → job fails (loud) |
| 13 | same job delivered twice (at-least-once): first `fetched`, replay | second run: no duplicate archive; hash now seen | replay emits `validate.doc` (deduped) not a second `extract.doc`; idempotent |
| 14 | archive path/layout assertion | `LocalFsStore` file at `{manufacturer}/{type_dir}/{hash[:12]}__{filename}`; one physical file; `put` idempotent | — |
| 15 | two-cycle: fetch same URL, then re-run after ledger touch within recency | second run makes **no** network call, no re-archive | `recency-skip*`; spend/bytes delta 0 (AC5 parity) |

Covers every outcome-table row (2–10), lease/defer (1), C3 both entries (2 recency, 6 same-hash-URL), Playwright ladder (7,8,9), transient vs bot-wall split (9 vs 10), null/malformed edges (11,12), idempotency (13), §9 layout (14), two-cycle cheapness (15).
