# Playbook-Driven Fetch Reachability Implementation Plan

**Goal:** Make more manufacturer documents actually reach the archive, by letting the authored playbook — not a 403 and not a code constant — decide how and which URLs FETCH and DISCOVER go after.

**Architecture:** Three additive playbook keys (`fetch_policy`, `doc_sources[kind:"direct"]`, `source_priority`) read by two existing handlers. No new job type, no migration, no PRD change: DISCOVER's Emits row already permits `fetch.url`, and its `playbook` ladder rung already exists as a logged no-op — this plan gives that rung its first real behaviour. A parse cache lands first because this plan turns FETCH into a per-job playbook consumer.

**Tech Stack:** Python 3.12, psycopg 3 (raw SQL, no ORM), pytest against a real Postgres, no new dependencies.

**Verified against:** HEAD on 2026-08-18. Every line reference and quoted
snippet below was re-checked at that commit. `app/playbooks.py`, `app/handlers/fetch.py`,
`app/handlers/discover.py` and `app/config.py` were untouched by the 11 commits that
landed while this plan was written; `docs/code-map.md` and `playbooks/README.md` were
touched and the instructions below match their current text.

**Spec:** `docs/superpowers/specs/2026-08-18-playbooks-across-stages-design.md` — items 1, 2, 3, 4. Items 5–18 are out of scope for this plan; see §10 of the spec for the follow-on plans.

## Global Constraints

- **The runtime playbook path never raises.** `load_playbooks` / `for_manufacturer` / `for_domain` log and degrade; only `validate()` (operator path, `dentalia playbooks validate`) raises. A malformed playbook must never dead-letter a fetch or discover job.
- **Playbook fields are additive-only.** Every consumer tolerates a missing section. The 11 playbooks authored today carry none of the keys this plan adds and must keep working untouched.
- **No secrets in playbooks** — they are in git. This plan adds no auth key for that reason.
- **A stage may only emit the job types in its PRD Emits row.** DISCOVER may emit `fetch.url` (PRD §3). FETCH may **not** emit `fetch.url` — nothing in this plan makes it do so.
- **Nothing skipped is silent.** Every playbook-driven decision is logged to `discovery_log` or returned on the job result.
- **Each task touches at most 3 files** (CLAUDE.md working rule 4), tests excluded from the count.
- **Tests run in an ISOLATED compose project.** Export this once per shell, before
  any test command in this plan:
  ```bash
  export WT='COMPOSE_PROJECT_NAME=dentalia_wt POSTGRES_PORT=5433 PGDATA_HOST=/srv/pgdata/dentalia_wt'
  ```
  `$WT` is a command PREFIX, not a flag: `$WT docker compose --profile test run ...`.

  **All three variables are required.** `COMPOSE_PROJECT_NAME` alone is NOT
  enough — `docker-compose.yml` pins `name: dentalia`, the postgres service
  bind-mounts a HOST path (`PGDATA_HOST`, default `/srv/pgdata/dentalia`)
  and publishes host port 5432. A project name scopes neither. Two postgres
  instances pointed at one PGDATA corrupts the cluster; the port collides.

  Known and accepted: `dentalia-test:latest` is a fixed image tag that no project
  name scopes, so `--build` here rebuilds the tag the main checkout also uses.
  Harmless unless both trees run tests simultaneously — don't.

  Purge when the branch is done:
  ```bash
  COMPOSE_PROJECT_NAME=dentalia_wt docker compose down -v
  rm -rf /srv/pgdata/dentalia_wt
  ```
- **The `docker compose exec -T postgres psql` measurement commands are the
  exception** — they read the LIVE registry and must be run from the main
  checkout `/srv/dentalia` with NO `$WT` prefix. The worktree's
  postgres is an empty throwaway and would report zero for everything.
- Commit messages: imperative, lower-case scope prefix matching the existing log (`playbooks:`, `fetch:`, `discover:`). No AI/Claude attribution.

---

### Task 1: Parse cache on the playbook load

> **SHIPPED 2026-08-21, as written.** Verified for leakage the only
> way global state can be — a full suite run, not the task's own file.

Today every consumer re-reads and re-parses the whole directory. `app/handlers/validate.py:465` calls `_ref_gate_for_group` once per candidate group in a loop, and each call re-reads all 11 files to compute the *same* rule — every group in that loop carries the same `canonical_manufacturer` by construction. This plan adds FETCH as a per-job consumer, so the cache lands first.

The cache is signature-based, not load-once: `playbooks/` is bind-mounted `:ro` into the worker (`docker-compose.yml:183`) and is hand-authored live — two files changed during the session that produced this plan. An edit must take effect on the next job with no restart.

**Files:**
- Modify: `app/playbooks.py`
- Test: `tests/test_playbooks.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `playbooks.clear_cache() -> None` — used by every later task's tests that point `PLAYBOOKS_DIR` at a `tmp_path`.

- [x] **Step 1: Write the failing tests**

Add to `tests/test_playbooks.py`:

```python
def test_load_does_not_reparse_an_unchanged_directory(tmp_path, monkeypatch):
    """The measured waste: validate.doc re-reads the whole directory once per
    candidate group in a loop (validate.py:465), for the same rule every time."""
    _write(tmp_path, "voco.json", VOCO)
    playbooks.clear_cache()

    parsed = []
    real_parse = playbooks._parse

    def counting_parse(slug, data):
        parsed.append(slug)
        return real_parse(slug, data)

    monkeypatch.setattr(playbooks, "_parse", counting_parse)

    playbooks.load_playbooks(tmp_path)
    playbooks.load_playbooks(tmp_path)

    assert parsed == ["voco"]


def test_load_reparses_when_a_file_changes(tmp_path):
    """playbooks/ is bind-mounted :ro and hand-authored live -- an edit must
    take effect on the next job, not the next restart. The two spellings differ
    in length, so the signature changes on size as well as mtime."""
    _write(tmp_path, "voco.json", VOCO)
    playbooks.clear_cache()
    assert playbooks.load_playbooks(tmp_path)[0].manufacturer == "VOCO GmbH"

    _write(tmp_path, "voco.json", {**VOCO, "manufacturer": "VOCO GmbH & Co. KG"})

    assert playbooks.load_playbooks(tmp_path)[0].manufacturer == "VOCO GmbH & Co. KG"


def test_load_sees_a_file_added_after_the_first_load(tmp_path):
    _write(tmp_path, "voco.json", VOCO)
    playbooks.clear_cache()
    assert len(playbooks.load_playbooks(tmp_path)) == 1

    _write(tmp_path, "komet.json", {"manufacturer": "KOMET"})

    assert len(playbooks.load_playbooks(tmp_path)) == 2


def test_load_sees_a_file_removed_after_the_first_load(tmp_path):
    _write(tmp_path, "voco.json", VOCO)
    _write(tmp_path, "komet.json", {"manufacturer": "KOMET"})
    playbooks.clear_cache()
    assert len(playbooks.load_playbooks(tmp_path)) == 2

    (tmp_path / "komet.json").unlink()

    assert len(playbooks.load_playbooks(tmp_path)) == 1


def test_two_directories_do_not_share_a_cache_entry(tmp_path):
    """The cache is keyed on the resolved directory: the web viewer's
    WEB_PLAYBOOKS_DIR and the pipeline's PLAYBOOKS_DIR are different dirs in
    the same process during tests."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    _write(a, "voco.json", VOCO)
    _write(b, "komet.json", {"manufacturer": "KOMET"})
    playbooks.clear_cache()

    assert playbooks.load_playbooks(a)[0].slug == "voco"
    assert playbooks.load_playbooks(b)[0].slug == "komet"
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `$WT docker compose --profile test run --rm test pytest tests/test_playbooks.py -k "reparse or unchanged or added_after or removed_after or share_a_cache" -v`
Expected: FAIL — `AttributeError: module 'app.playbooks' has no attribute 'clear_cache'`

- [x] **Step 3: Implement the cache**

In `app/playbooks.py`, add `import os` to the imports, then add above `load_playbooks`:

```python
#: Parse cache, keyed on the resolved directory. The value's first element is a
#: signature of the directory's *.json entries; a mismatch reparses. Keyed on
#: the directory rather than held as one module global because the web viewer
#: (`WEB_PLAYBOOKS_DIR`) and the pipeline read different directories in the same
#: process under test.
_CACHE: dict[pathlib.Path, tuple[frozenset, tuple["Playbook", ...]]] = {}


def _dir_signature(dir_path: pathlib.Path) -> frozenset:
    """Cheap change-detector: one scandir, no reads, no JSON. Name + mtime_ns +
    size together catch every edit the authoring flow produces (the directory is
    bind-mounted :ro and hand-edited, so an in-place rewrite to the identical
    size within the same nanosecond is not a case that occurs)."""
    out = set()
    with os.scandir(dir_path) as it:
        for entry in it:
            if entry.name.endswith(".json") and entry.is_file():
                st = entry.stat()
                out.add((entry.name, st.st_mtime_ns, st.st_size))
    return frozenset(out)


def clear_cache() -> None:
    """Drop the parse cache. For tests that repoint `PLAYBOOKS_DIR`; the runtime
    never needs it -- an edit is detected by signature."""
    _CACHE.clear()
```

Replace the body of `load_playbooks` (keep its docstring, append the cache sentence):

```python
def load_playbooks(dir_path: pathlib.Path | str | None = None) -> tuple[Playbook, ...]:
    """Every readable playbook, sorted by slug. Never raises: a malformed file
    is logged and skipped, so one bad file cannot take DISCOVER down. Use
    `validate()` when the caller is an operator who wants to be told.

    Cached per directory and invalidated by a name/mtime/size signature, so a
    hot loop (VALIDATE's per-group REF gate) pays one scandir instead of N
    reads, while a live edit to the bind-mounted directory still lands on the
    next job."""
    dir_path = pathlib.Path(dir_path if dir_path is not None else PLAYBOOKS_DIR)
    if not dir_path.is_dir():
        log.warning("playbooks: directory %s does not exist", dir_path)
        return ()

    key = dir_path.resolve()
    sig = _dir_signature(key)
    cached = _CACHE.get(key)
    if cached is not None and cached[0] == sig:
        return cached[1]

    out = []
    for f in sorted(dir_path.glob("*.json")):
        try:
            out.append(_parse(f.stem, json.loads(f.read_text())))
        except Exception:
            log.warning("playbooks: skipping malformed playbook %s", f, exc_info=True)
    loaded = tuple(out)
    _CACHE[key] = (sig, loaded)
    return loaded
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `$WT docker compose --profile test run --rm test pytest tests/test_playbooks.py -v`
Expected: PASS, including every pre-existing test in the file.

- [x] **Step 5: Run the full suite — the cache is global state**

Run: `$WT docker compose --profile test run --rm --build test`
Expected: PASS. `tests/test_web.py`, `tests/test_validate_handler.py` and `tests/test_t0.py` all load playbooks; a cache that leaks across tests shows up here, not in step 4.

- [x] **Step 6: Commit**

```bash
git add app/playbooks.py tests/test_playbooks.py
git commit -m "playbooks: cache the parse, keyed on a directory signature"
```

---

### Task 2: `fetch_policy` on the playbook, and a domain lookup

FETCH's payload carries `domain` and may carry no group at all, so the policy is keyed on the **host**, not the company. Two keys only — `user_agent`, `max_bytes` and `auth_ref` appear in the spec but no task here needs them, and authoring a key no consumer reads is how `kind:"direct"` came to be parsed-and-ignored in the first place.

**Files:**
- Modify: `app/playbooks.py`
- Modify: `playbooks/README.md`
- Test: `tests/test_playbooks.py`

**Interfaces:**
- Consumes: `playbooks.clear_cache()` (Task 1).
- Produces:
  - `playbooks.FetchPolicy(needs_playwright: bool = False, politeness_ms: int | None = None)` — frozen dataclass.
  - `Playbook.fetch_policy: FetchPolicy` — always present, defaults to `FetchPolicy()`.
  - `playbooks.for_domain(domain: str, playbooks: tuple[Playbook, ...] | None = None) -> Playbook | None`.
  - `validate()` additionally raises `PlaybookConflict` when two playbooks claim one domain.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_playbooks.py`:

```python
def test_fetch_policy_parses(tmp_path):
    _write(tmp_path, "walled.json", {
        "manufacturer": "WALLED AG",
        "domains": ["walled.example"],
        "fetch_policy": {"needs_playwright": True, "politeness_ms": 8000},
    })
    playbooks.clear_cache()

    (pb,) = playbooks.load_playbooks(tmp_path)

    assert pb.fetch_policy.needs_playwright is True
    assert pb.fetch_policy.politeness_ms == 8000


def test_fetch_policy_absent_is_the_neutral_default(tmp_path):
    """The 11 playbooks authored today carry no fetch_policy and must keep
    behaving exactly as they do now (additive-only rule)."""
    _write(tmp_path, "voco.json", VOCO)
    playbooks.clear_cache()

    (pb,) = playbooks.load_playbooks(tmp_path)

    assert pb.fetch_policy.needs_playwright is False
    assert pb.fetch_policy.politeness_ms is None


def test_negative_politeness_is_a_malformed_file_not_a_negative_lease(tmp_path):
    """A negative interval would make try_domain_lease grant a lease in the
    past, i.e. no politeness at all. Skipped like any malformed playbook --
    load_playbooks never raises."""
    _write(tmp_path, "bad.json", {
        "manufacturer": "BAD AG",
        "fetch_policy": {"politeness_ms": -1},
    })
    playbooks.clear_cache()

    assert playbooks.load_playbooks(tmp_path) == ()


def test_for_domain_matches_a_claimed_host(tmp_path):
    _write(tmp_path, "voco.json", VOCO)
    playbooks.clear_cache()
    loaded = playbooks.load_playbooks(tmp_path)

    assert playbooks.for_domain("voco.dental", loaded).slug == "voco"
    assert playbooks.for_domain("VOCO.DENTAL", loaded).slug == "voco"
    assert playbooks.for_domain("https://voco.dental/x", loaded).slug == "voco"
    assert playbooks.for_domain("other.example", loaded) is None
    assert playbooks.for_domain("", loaded) is None


def test_for_domain_does_not_match_a_subdomain_it_was_not_given(tmp_path):
    """straumann.json claims BOTH straumann.com and ifu.straumann.com because a
    host match is exact -- a policy authored for the IFU portal must not leak
    onto the corporate site."""
    _write(tmp_path, "voco.json", VOCO)
    playbooks.clear_cache()
    loaded = playbooks.load_playbooks(tmp_path)

    assert playbooks.for_domain("downloads.voco.dental", loaded) is None


def test_validate_rejects_two_playbooks_claiming_one_domain(tmp_path):
    """Same reason a duplicate BC code is refused: whichever file sorted first
    would silently own the other's fetch policy."""
    _write(tmp_path, "a.json", {"manufacturer": "A AG", "domains": ["shared.example"]})
    _write(tmp_path, "b.json", {"manufacturer": "B AG", "domains": ["shared.example"]})
    playbooks.clear_cache()

    with pytest.raises(playbooks.PlaybookConflict, match="shared.example"):
        playbooks.validate(playbooks.load_playbooks(tmp_path))

```

No test is added for "the real playbooks/ must have no domain conflict":
`tests/test_playbooks.py:222` `test_shipped_playbooks_are_valid` already calls
`playbooks.validate(playbooks.load_playbooks())`, so adding the domain check to
`validate()` extends that existing guard to domains for free. Verified
conflict-free across all 11 authored files at HEAD.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `$WT docker compose --profile test run --rm test pytest tests/test_playbooks.py -k "fetch_policy or for_domain or politeness or one_domain or domain_conflict" -v`
Expected: FAIL — `AttributeError: module 'app.playbooks' has no attribute 'for_domain'`

- [ ] **Step 3: Implement `FetchPolicy`, parsing, `for_domain`, and the conflict check**

In `app/playbooks.py`, add above `class Playbook`:

```python
@dataclass(frozen=True)
class FetchPolicy:
    """Authored transport policy for the domains this playbook claims.

    Both keys are SEEDS, not overrides of learned state. `domain_lease.
    needs_playwright` escalates on its own when a 403 is met, and never
    de-escalates because a file says `false` -- an authored `false` is the
    absence of a seed, not an assertion that the host is open. `politeness_ms`
    is the one exception the other way: an authored interval REPLACES
    `cfg.fetch.politeness_ms` for these hosts, because the global default is a
    guess and the authored value is a measurement."""

    needs_playwright: bool = False
    politeness_ms: int | None = None


#: Shared because FetchPolicy is frozen: a dataclass default may be a shared
#: instance only when the instance cannot be mutated.
_NO_FETCH_POLICY = FetchPolicy()
```

Add the field to `Playbook`, after `ref_normalize`:

```python
    # Authored transport policy for `domains` (S1.7 expansion, 2026-08-18).
    # Read by `app/handlers/fetch.py` via `for_domain(payload["domain"])` --
    # keyed on the host, not the manufacturer, because a fetch payload carries
    # a domain and may carry no group at all.
    fetch_policy: FetchPolicy = _NO_FETCH_POLICY
```

In `_parse`, before the `return Playbook(...)`:

```python
    fp = data.get("fetch_policy") or {}
    if not isinstance(fp, dict):
        raise ValueError("fetch_policy must be an object")
    politeness = fp.get("politeness_ms")
    if politeness is not None and (not isinstance(politeness, int)
                                   or isinstance(politeness, bool)
                                   or politeness < 0):
        raise ValueError("fetch_policy.politeness_ms must be a non-negative integer")
    fetch_policy = FetchPolicy(
        needs_playwright=bool(fp.get("needs_playwright", False)),
        politeness_ms=politeness,
    )
```

and pass `fetch_policy=fetch_policy` in the `Playbook(...)` construction.

In `validate()`, add alongside the existing `seen_codes` / `seen_names` loops:

```python
    seen_domains: dict[str, str] = {}
```

and inside the `for pb in playbooks:` body:

```python
        for d in pb.domains:
            low = d.casefold()
            if low in seen_domains:
                raise PlaybookConflict(
                    f"domain {d!r} claimed by both {seen_domains[low]!r} and {pb.slug!r}"
                )
            seen_domains[low] = pb.slug
```

Add at the end of the module:

```python
def for_domain(
    domain: str, playbooks: tuple[Playbook, ...] | None = None
) -> Playbook | None:
    """Playbook claiming `domain`, exact case-folded match on the normalized
    hostname. Accepts a bare host or a full URL (both go through
    `_normalize_domain`, the same normalizer FETCH and DISCOVER use).

    Exact, never suffix: `straumann.json` claims `straumann.com` AND
    `ifu.straumann.com` precisely so a policy authored for the IFU portal does
    not leak onto the corporate site. A host that should share a policy is
    authored, not inferred."""
    if not domain:
        return None
    if playbooks is None:
        playbooks = load_playbooks()
    low = _normalize_domain(domain).casefold()
    if not low:
        return None
    for pb in playbooks:
        if any(d.casefold() == low for d in pb.domains):
            return pb
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `$WT docker compose --profile test run --rm test pytest tests/test_playbooks.py -v`
Expected: PASS.

- [ ] **Step 5: Verify the operator path is still green on the real files**

Run: `$WT docker compose --profile test run --rm test python -m app.cli playbooks validate`
Expected: exit 0. A non-zero exit means two authored playbooks share a domain — fix the files, not the check.

- [ ] **Step 6: Document the key**

`playbooks/README.md` has a `## Fields` summary table (line 9) and gives a
dedicated `##` section to any key with a real trap in it (`ref_normalize`,
line 21). `fetch_policy` gets both.

Add a row to the `## Fields` table, directly after the `ref_normalize` row:

```markdown
| `fetch_policy` | no | Transport policy for the hosts in `domains`, read by `app/handlers/fetch.py`. See below. |
```

Then add a section after the `ref_normalize` one (before `## Still to author`):

```markdown
## `fetch_policy` — a seed, not an override

Read by FETCH through `playbooks.for_domain(payload["domain"])` — keyed on the
HOST, not the manufacturer, because a fetch payload carries a domain and may
carry no group at all.

```json
"fetch_policy": { "needs_playwright": true, "politeness_ms": 8000 }
```

| Key | Meaning |
|---|---|
| `needs_playwright` | Seed the render tier for these hosts. Saves the first document on a known bot-walled host from spending a 403 and a failed job to rediscover it. A **seed**: `domain_lease.needs_playwright` still escalates on its own when a 403 is met, and an authored `false` never de-escalates a learned `true`. |
| `politeness_ms` | Replaces `cfg.fetch.politeness_ms` for these hosts. Non-negative integer. This one IS an override — the global 2s default is a blanket guess, an authored interval is a measurement. `domain_lease.politeness_ms` records which value each lease was granted under. |

Two playbooks claiming one domain is refused by `dentalia playbooks validate`,
for the same reason a duplicate BC code is: whichever file sorted first would
silently own the other's transport policy.

Never author credentials here. `playbooks/` is in git.
```

- [ ] **Step 7: Commit**

```bash
git add app/playbooks.py playbooks/README.md tests/test_playbooks.py
git commit -m "playbooks: fetch_policy and a domain-keyed lookup"
```

---

### Task 3: FETCH honours the `needs_playwright` seed

Today a bot-walled host costs one wasted httpx 403 plus one job failure before `domain_lease.needs_playwright` is learned — and it is learned only on the *success* path (`app/handlers/fetch.py:107`), so a host where Playwright also fails never records anything at all.

**Files:**
- Modify: `app/handlers/fetch.py`
- Test: `tests/test_fetch_handler.py`

**Interfaces:**
- Consumes: `playbooks.for_domain(domain)`, `Playbook.fetch_policy.needs_playwright`, `playbooks.clear_cache()` (Task 2).
- Produces: no new callable. `_domain_needs_playwright(conn, domain)` keeps its signature and return type (`bool`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_fetch_handler.py`. The file already has `_job`, `_resp`, `FakeFetcher`, `BoomFetcher`, `_seed_group`, `_seed_lease`, `_needs_pw`, `_jobs` and imports `LocalFsStore` — reuse them; add `import json` and `from app import playbooks` to the imports if absent.

```python
def _write_playbook(dir_path, slug, data):
    (dir_path / f"{slug}.json").write_text(json.dumps(data))


def test_authored_playwright_seed_skips_the_wasted_403(conn, tmp_path, monkeypatch):
    """An operator who already knows a host is bot-walled should not have to pay
    a 403 and a failed job for the pipeline to find out."""
    pb_dir = tmp_path / "playbooks"
    pb_dir.mkdir()
    _write_playbook(pb_dir, "walled", {
        "manufacturer": "WALLED AG",
        "domains": ["seeded.example"],
        "fetch_policy": {"needs_playwright": True},
    })
    monkeypatch.setattr(playbooks, "PLAYBOOKS_DIR", pb_dir)
    playbooks.clear_cache()

    g = _seed_group(conn)
    pw_f = FakeFetcher(_resp(body=b"pdf", final_url="https://seeded.example/d.pdf"))

    res = fh.handle_fetch_url(
        conn, _job("https://seeded.example/d.pdf", domain="seeded.example", group_id=g),
        fetcher=BoomFetcher(), playwright_fetcher=pw_f, store=LocalFsStore(str(tmp_path)),
    )

    assert res["via"] == "playwright"
    assert len(_jobs(conn, "extract.doc")) == 1


def test_learned_flag_still_wins_when_the_playbook_says_nothing(conn, tmp_path, monkeypatch):
    """The seed is additive: an unclaimed domain behaves exactly as before."""
    pb_dir = tmp_path / "playbooks"
    pb_dir.mkdir()
    monkeypatch.setattr(playbooks, "PLAYBOOKS_DIR", pb_dir)
    playbooks.clear_cache()

    g = _seed_group(conn)
    _seed_lease(conn, "learned.example", needs_playwright=True)

    res = fh.handle_fetch_url(
        conn, _job("https://learned.example/d.pdf", domain="learned.example", group_id=g),
        fetcher=BoomFetcher(),
        playwright_fetcher=FakeFetcher(_resp(final_url="https://learned.example/d.pdf")),
        store=LocalFsStore(str(tmp_path)),
    )

    assert res["via"] == "playwright"


def test_authored_false_never_de_escalates_a_learned_true(conn, tmp_path, monkeypatch):
    """A stale `false` in a file must not undo what a real 403 taught us."""
    pb_dir = tmp_path / "playbooks"
    pb_dir.mkdir()
    _write_playbook(pb_dir, "open", {
        "manufacturer": "OPEN AG",
        "domains": ["both.example"],
        "fetch_policy": {"needs_playwright": False},
    })
    monkeypatch.setattr(playbooks, "PLAYBOOKS_DIR", pb_dir)
    playbooks.clear_cache()

    g = _seed_group(conn)
    _seed_lease(conn, "both.example", needs_playwright=True)

    res = fh.handle_fetch_url(
        conn, _job("https://both.example/d.pdf", domain="both.example", group_id=g),
        fetcher=BoomFetcher(),
        playwright_fetcher=FakeFetcher(_resp(final_url="https://both.example/d.pdf")),
        store=LocalFsStore(str(tmp_path)),
    )

    assert res["via"] == "playwright"


def test_a_malformed_playbook_directory_does_not_fail_the_fetch(conn, tmp_path, monkeypatch):
    """Runtime contract: a fat-fingered playbook degrades to 'no seed', never to
    a dead-lettered fetch job."""
    pb_dir = tmp_path / "playbooks"
    pb_dir.mkdir()
    (pb_dir / "broken.json").write_text("{not json")
    monkeypatch.setattr(playbooks, "PLAYBOOKS_DIR", pb_dir)
    playbooks.clear_cache()

    g = _seed_group(conn)

    res = fh.handle_fetch_url(
        conn, _job("https://plain.example/d.pdf", domain="plain.example", group_id=g),
        fetcher=FakeFetcher(_resp(final_url="https://plain.example/d.pdf")),
        playwright_fetcher=BoomFetcher(), store=LocalFsStore(str(tmp_path)),
    )

    assert res["via"] == "httpx"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `$WT docker compose --profile test run --rm test pytest tests/test_fetch_handler.py -k "seed or de_escalate or malformed_playbook or learned_flag" -v`
Expected: FAIL — `test_authored_playwright_seed_skips_the_wasted_403` raises `AssertionError: network transport must not be called on a skip path` from `BoomFetcher`, because httpx is still tried first.

- [ ] **Step 3: Implement**

In `app/handlers/fetch.py`, add to the imports:

```python
from app import playbooks as playbooks_mod
```

Replace `_domain_needs_playwright`:

```python
def _domain_needs_playwright(conn, domain):
    """Learned flag OR authored seed.

    The learned flag is a one-way escalation: a 403 sets it (success path only,
    `_flag_domain_playwright`) and nothing clears it. The playbook seed is what
    saves the FIRST document on a known bot-walled host from spending a 403 and
    a failed job to rediscover what an operator already knew. An authored
    `false` is the absence of a seed, never a de-escalation -- hence the `or`,
    not a precedence rule.

    `for_domain` never raises (`load_playbooks`'s contract), so a missing or
    malformed playbooks/ directory degrades to 'no seed' here, never to a failed
    fetch job."""
    row = conn.execute(
        "SELECT needs_playwright FROM domain_lease WHERE domain=%s", (domain,)
    ).fetchone()
    if row and row["needs_playwright"]:
        return True
    pb = playbooks_mod.for_domain(domain)
    return bool(pb and pb.fetch_policy.needs_playwright)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `$WT docker compose --profile test run --rm test pytest tests/test_fetch_handler.py -v`
Expected: PASS, including the pre-existing `test_preflagged_domain_uses_playwright` and `test_bot_wall_playwright_fallback`.

- [ ] **Step 5: Commit**

```bash
git add app/handlers/fetch.py tests/test_fetch_handler.py
git commit -m "fetch: seed the render tier from the playbook, not from a 403"
```

---

### Task 4: FETCH honours the authored politeness interval

`domain_lease` already stores `politeness_ms` per domain (`app/queue.py:222-227` writes it on every lease) — the column exists and is currently always overwritten with the one global value. This task chooses the value passed in; no schema change.

**Files:**
- Modify: `app/handlers/fetch.py`
- Test: `tests/test_fetch_handler.py`

**Interfaces:**
- Consumes: `playbooks.for_domain(domain)`, `Playbook.fetch_policy.politeness_ms` (Task 2).
- Produces: `fetch._politeness_ms(cfg, domain) -> int` — used by nothing else in this plan; kept module-private.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_fetch_handler.py`:

```python
def test_authored_politeness_replaces_the_global_default(conn, tmp_path, monkeypatch):
    """The lease row records the interval it was granted under, so an authored
    value is visible in the DB, not just in the branch that chose it."""
    pb_dir = tmp_path / "playbooks"
    pb_dir.mkdir()
    _write_playbook(pb_dir, "slow", {
        "manufacturer": "SLOW AG",
        "domains": ["slow.example"],
        "fetch_policy": {"politeness_ms": 9000},
    })
    monkeypatch.setattr(playbooks, "PLAYBOOKS_DIR", pb_dir)
    playbooks.clear_cache()

    g = _seed_group(conn)
    fh.handle_fetch_url(
        conn, _job("https://slow.example/d.pdf", domain="slow.example", group_id=g),
        fetcher=FakeFetcher(_resp(final_url="https://slow.example/d.pdf")),
        playwright_fetcher=BoomFetcher(), store=LocalFsStore(str(tmp_path)),
    )

    row = conn.execute(
        "SELECT politeness_ms FROM domain_lease WHERE domain=%s", ("slow.example",)
    ).fetchone()
    assert row["politeness_ms"] == 9000


def test_unclaimed_domain_keeps_the_configured_default(conn, tmp_path, monkeypatch):
    pb_dir = tmp_path / "playbooks"
    pb_dir.mkdir()
    monkeypatch.setattr(playbooks, "PLAYBOOKS_DIR", pb_dir)
    playbooks.clear_cache()

    g = _seed_group(conn)
    fh.handle_fetch_url(
        conn, _job("https://plain2.example/d.pdf", domain="plain2.example", group_id=g),
        fetcher=FakeFetcher(_resp(final_url="https://plain2.example/d.pdf")),
        playwright_fetcher=BoomFetcher(), store=LocalFsStore(str(tmp_path)),
    )

    row = conn.execute(
        "SELECT politeness_ms FROM domain_lease WHERE domain=%s", ("plain2.example",)
    ).fetchone()
    assert row["politeness_ms"] == 2000    # app/config.py Fetch.politeness_ms default


def test_the_deferral_matches_the_authored_interval(conn, tmp_path, monkeypatch):
    """A lease-wait must back off by the interval that host actually wants,
    not by the global one -- otherwise a slow host is polled at the fast rate."""
    pb_dir = tmp_path / "playbooks"
    pb_dir.mkdir()
    _write_playbook(pb_dir, "slow", {
        "manufacturer": "SLOW AG",
        "domains": ["busy2.example"],
        "fetch_policy": {"politeness_ms": 9000},
    })
    monkeypatch.setattr(playbooks, "PLAYBOOKS_DIR", pb_dir)
    playbooks.clear_cache()

    deferred = []
    monkeypatch.setattr(fh.queue, "defer",
                        lambda conn, jid, secs: deferred.append(secs))

    from app import queue as q
    q.try_domain_lease(conn, "busy2.example", 9000)     # lease already held

    res = fh.handle_fetch_url(
        conn, _job("https://busy2.example/d.pdf", domain="busy2.example"),
        fetcher=BoomFetcher(), playwright_fetcher=BoomFetcher(),
        store=LocalFsStore(str(tmp_path)),
    )

    assert res["outcome"] == "lease-wait"
    assert deferred == [9]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `$WT docker compose --profile test run --rm test pytest tests/test_fetch_handler.py -k "politeness or deferral" -v`
Expected: FAIL — `assert 2000 == 9000` in the first test.

- [ ] **Step 3: Implement**

In `app/handlers/fetch.py`, add below `_flag_domain_playwright`:

```python
def _politeness_ms(cfg, domain) -> int:
    """The interval this host is fetched at: the playbook's authored value if it
    claims the domain, else the global default.

    Unlike the Playwright seed this IS an override: `cfg.fetch.politeness_ms` is
    a blanket guess (2s) and an authored interval is a measurement of what one
    host tolerates. `try_domain_lease` persists whichever value it was granted
    under onto `domain_lease.politeness_ms`, so the effective rate is
    inspectable in the DB."""
    pb = playbooks_mod.for_domain(domain)
    if pb and pb.fetch_policy.politeness_ms is not None:
        return pb.fetch_policy.politeness_ms
    return cfg.fetch.politeness_ms
```

In `handle_fetch_url`, replace the politeness block:

```python
    # 1. politeness lease — one fetch per domain per interval
    politeness_ms = _politeness_ms(cfg, domain)
    if not queue.try_domain_lease(conn, domain, politeness_ms):
        queue.defer(conn, job["id"], max(1, politeness_ms // 1000))
        return {"outcome": "lease-wait", "_deferred": True, "group_id": group_id}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `$WT docker compose --profile test run --rm test pytest tests/test_fetch_handler.py -v`
Expected: PASS, including the pre-existing lease-wait test.

- [ ] **Step 5: Commit**

```bash
git add app/handlers/fetch.py tests/test_fetch_handler.py
git commit -m "fetch: take the politeness interval from the playbook when authored"
```

---

### Task 5: The `playbook` DISCOVER rung emits authored direct URLs

> **SHIPPED 2026-08-21, with one deviation that mattered.** Step 5's
> expectation — "all authored doc_sources are `kind:"portal"`, so this ships
> inert" — was **false by the time it ran**: 8 `direct` entries existed and only
> 2 were documents. The other six were HTML listing pages, and since FETCH has
> no content-type gate they would have been archived and died silently at
> EXTRACT as `unreadable_pdf` on a job that finishes `done`. They were
> re-authored `portal` in the same commit. The lesson generalises: a plan's
> "confirm nothing is authored yet" step is a **measurement, not a formality** —
> when it disagrees with the plan, the plan is out of date.

`doc_sources` supports `kind: "direct"`. `app/playbooks.py` parses it and **nothing reads it** — `app/handlers/discover.py:252` filters `kind == "portal"` for the manual-task prefill only. Meanwhile the ladder rung named `playbook` logs `skipped/deferred` at `discover.py:398`. This task connects the two: authored direct URLs become `fetch.url` jobs on the rung that exists for exactly that.

`discovery_log.source` is free text (`migrations/003_discovery_fetch.sql:13` lists `playbook` in its comment) — no migration.

**Files:**
- Modify: `app/handlers/discover.py`
- Modify: `app/config.py`
- Test: `tests/test_discover_handler.py`

**Interfaces:**
- Consumes: `Playbook.doc_sources` (already exists), `playbooks.for_manufacturer` (already called at `discover.py:322`).
- Produces: `discover._direct_urls(playbook) -> list[str]`. The rung returns `_result(..., terminal_source="playbook", outcome="fetch")`.

- [x] **Step 1: Write the failing tests**

Add to `tests/test_discover_handler.py`, following the existing fixture style in that file:

```python
def test_playbook_rung_emits_authored_direct_urls(conn):
    """kind:"direct" was parsed and read by nothing. It is the cheapest possible
    discovery: an operator already wrote down where the document lives."""
    from app.playbooks import DocSource, Playbook

    pb = Playbook(
        slug="voco", manufacturer="VOCO",
        doc_sources=(
            DocSource(doc_type="DoC", kind="direct", url="https://voco.dental/a.pdf"),
            DocSource(doc_type="IFU", kind="portal", url="https://voco.dental/downloads"),
        ),
    )

    assert dh._direct_urls(pb) == ["https://voco.dental/a.pdf"]


def test_portal_sources_are_not_fetched(conn):
    """A portal is a listing page, not a document. Fetching one archives HTML."""
    from app.playbooks import DocSource, Playbook

    pb = Playbook(
        slug="voco", manufacturer="VOCO",
        doc_sources=(DocSource(doc_type="IFU", kind="portal",
                               url="https://voco.dental/downloads"),),
    )

    assert dh._direct_urls(pb) == []


def test_no_playbook_yields_no_direct_urls():
    assert dh._direct_urls(None) == []
```

Then the rung's end-to-end test, matching how the other rung tests in the file seed a group and monkeypatch `playbooks_mod.for_manufacturer` (see the existing `test_search_rung_*` cases around `tests/test_discover_handler.py:578`):

```python
def test_playbook_rung_is_terminal_and_logs_a_hit(conn, monkeypatch):
    from app import playbooks as playbooks_mod
    from app.playbooks import DocSource, Playbook

    gid = _seed_group(conn, canonical="VOCO")
    pb = Playbook(
        slug="voco", manufacturer="VOCO",
        doc_sources=(DocSource(doc_type="DoC", kind="direct",
                               url="https://voco.dental/a.pdf"),),
    )
    monkeypatch.setattr(playbooks_mod, "for_manufacturer", lambda *_a, **_k: pb)
    monkeypatch.setattr(dh, "_source_priority",
                        lambda cfg, m, pb=None: ["playbook", "manual"])

    res = dh.handle_discover_group(conn, {"payload": {"group_id": gid}})

    assert res["terminal_source"] == "playbook"
    assert res["outcome"] == "fetch"
    assert res["emitted_fetch"] == 1
    row = conn.execute(
        "SELECT source, outcome FROM discovery_log WHERE group_id=%s ORDER BY id DESC LIMIT 1",
        (gid,),
    ).fetchone()
    assert (row["source"], row["outcome"]) == ("playbook", "hit")


def test_playbook_rung_falls_through_when_nothing_is_authored(conn, monkeypatch):
    """A miss must continue down the ladder, never terminate discovery."""
    from app import playbooks as playbooks_mod

    gid = _seed_group(conn, canonical="VOCO")
    monkeypatch.setattr(playbooks_mod, "for_manufacturer", lambda *_a, **_k: None)
    monkeypatch.setattr(dh, "_source_priority",
                        lambda cfg, m, pb=None: ["playbook", "manual"])

    res = dh.handle_discover_group(conn, {"payload": {"group_id": gid}})

    assert res["terminal_source"] == "manual"
    assert "playbook" in res["sources_tried"]
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `$WT docker compose --profile test run --rm test pytest tests/test_discover_handler.py -k "direct_urls or playbook_rung or portal_sources" -v`
Expected: FAIL — `AttributeError: module 'app.handlers.discover' has no attribute '_direct_urls'`

- [x] **Step 3: Implement**

In `app/handlers/discover.py`, add below `_prefilled_search_links`:

```python
def _direct_urls(playbook=None) -> list[str]:
    """Authored `kind:"direct"` doc_sources -- URLs an operator has already
    confirmed point at a document.

    `kind:"portal"` is deliberately excluded: a portal is a LISTING page, and
    fetching one archives HTML that no extractor can read. Turning a portal into
    documents is the crawl recipe (spec item 11), not this rung."""
    if not playbook:
        return []
    return [s.url for s in playbook.doc_sources if s.kind == "direct"]
```

Replace the dead-rung branch at `discover.py:398`:

```python
        if source == "playbook":
            urls = _direct_urls(playbook)
            if urls:
                _log(conn, group_id, "playbook", "hit", {"urls": urls})
                n = _emit_fetch(conn, facts, urls, "playbook")
                _resolve_dead_end_task(conn, group_id)
                return _result(group_id, "playbook", "fetch", sources_tried,
                               emitted_fetch=n, candidates_seen=candidates_seen)
            _log(conn, group_id, "playbook", "miss")
            continue

        if source == "vendor":   # folded into search (G6)
            _log(conn, group_id, source, "skipped", {"deferred": True})
            continue
```

In `app/config.py`, add `"playbook"` to `Discovery.default_source_priority`, directly after `known_url`:

```python
    default_source_priority: list[str] = field(
        default_factory=lambda: [
            "recency",
            "known_url",
            "playbook",
            "eudamed",
            "search",
            "email",
            "manual",
        ]
    )
```

and update the docstring paragraph at `app/config.py:111-114` to say that `playbook` now serves authored `kind:"direct"` sources and only the crawl recipe remains Phase 2, while `vendor` stays folded into `search` per G6.

- [x] **Step 4: Run the tests to verify they pass**

Run: `$WT docker compose --profile test run --rm test pytest tests/test_discover_handler.py -v`
Expected: FAIL on one pre-existing test, which is asserting behaviour this task
deliberately removes. Fix it in this same commit:

`tests/test_discover_handler.py:412` `test_configured_playbook_and_vendor_rungs_are_skipped`
asserts `("playbook", "skipped") in logs`. Narrow it to the rung that still
defers:

```python
def test_configured_vendor_rung_is_skipped(conn, monkeypatch):
    """`vendor` stays folded into `search` (G6). `playbook` is no longer a skip —
    it serves authored direct sources (test_playbook_rung_is_terminal_and_logs_a_hit)."""
    base = load_config()
    cfg = dataclasses.replace(base, source_priority={"PBco": ["vendor", "manual"]})
```

...keeping the rest of that test's body, and asserting
`("vendor", "skipped") in logs` with the `playbook` assertion dropped.

Also correct the module docstring at `tests/test_discover_handler.py:6`, which
reads "recency/playbook/vendor are skips" — `playbook` is no longer one.

Then re-run: expected PASS.

- [x] **Step 5: Confirm nothing is authored yet that this would fetch**

Run: `python3 -c "import json,glob; print([(f,s['url']) for f in glob.glob('playbooks/*.json') for s in json.load(open(f)).get('doc_sources',[]) if s.get('kind')=='direct'])"`
Expected: `[]` — all five authored doc_sources across the 11 playbooks are `kind:"portal"` at HEAD. This rung ships inert and turns on the first time someone authors a direct URL, which is the safe order.

- [x] **Step 6: Commit**

```bash
git add app/handlers/discover.py app/config.py tests/test_discover_handler.py
git commit -m "discover: the playbook rung fetches authored direct sources"
```

---

### Task 6: `source_priority` moves into the playbook

> **SHIPPED 2026-08-21, in the same change as Task 5.** One adaptation:
> Step 5 says to put the README row "after the `fetch_policy` row added in Task
> 2", and Task 2 is unbuilt, so it went after `type_markers` instead.

`cfg.source_priority` is a `{manufacturer: [rung]}` map in the TOML overlay whose own comment reads *"Placeholder until playbooks land (Phase 2)"* (`app/config.py:407-410`). It is per-manufacturer data living outside the per-manufacturer file. Config stays as the fallback so an existing TOML keeps working.

**Files:**
- Modify: `app/playbooks.py`
- Modify: `app/handlers/discover.py`
- Modify: `playbooks/README.md`
- Test: `tests/test_discover_handler.py`

**Interfaces:**
- Consumes: `playbooks.for_manufacturer` (already called in the handler).
- Produces: `Playbook.source_priority: tuple[str, ...]` — empty tuple when unauthored. `_source_priority(cfg, manufacturer, playbook=None)` gains a third parameter, defaulting to `None` so existing call sites and tests keep working.

- [x] **Step 1: Write the failing tests**

```python
def test_playbook_source_priority_wins_over_config(conn):
    from app.playbooks import Playbook

    cfg = load_config()
    pb = Playbook(slug="voco", manufacturer="VOCO",
                  source_priority=("known_url", "manual"))

    assert dh._source_priority(cfg, "VOCO", pb) == ["known_url", "manual"]


def test_config_map_still_serves_a_manufacturer_with_no_playbook_entry(conn):
    from app.playbooks import Playbook

    cfg = dataclasses.replace(load_config(),
                              source_priority={"VOCO": ["eudamed", "manual"]})
    pb = Playbook(slug="voco", manufacturer="VOCO")     # no source_priority authored

    assert dh._source_priority(cfg, "VOCO", pb) == ["eudamed", "manual"]


def test_default_ladder_when_neither_names_the_manufacturer(conn):
    cfg = load_config()

    assert dh._source_priority(cfg, "UNKNOWN AG", None) == \
        cfg.discovery.default_source_priority
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `$WT docker compose --profile test run --rm test pytest tests/test_discover_handler.py -k "source_priority" -v`
Expected: FAIL — `TypeError: _source_priority() takes 2 positional arguments but 3 were given`

- [x] **Step 3: Implement**

In `app/playbooks.py`, add to `Playbook` after `fetch_policy`:

```python
    # Per-manufacturer discovery ladder. Supersedes the TOML `[source_priority]`
    # map, which app/config.py has always described as a placeholder for this.
    # Empty tuple means "unauthored" -- config, then the default ladder.
    source_priority: tuple[str, ...] = ()
```

In `_parse`, add before the return and pass it through:

```python
    source_priority = tuple(str(s) for s in data.get("source_priority", []))
```

In `app/handlers/discover.py`, replace `_source_priority`:

```python
def _source_priority(cfg, manufacturer, playbook=None) -> list[str]:
    """Per-manufacturer ladder. Playbook first (the per-manufacturer file is the
    home for per-manufacturer data), then the TOML `[source_priority]` map that
    predates playbooks and still serves any manufacturer without one, then the
    Phase-1 default."""
    if playbook and playbook.source_priority:
        return list(playbook.source_priority)
    return cfg.source_priority.get(manufacturer, cfg.discovery.default_source_priority)
```

and update the call site at `discover.py:323` — note it must come **after** the `playbook = playbooks_mod.for_manufacturer(...)` line, which it already does:

```python
    ladder = _source_priority(cfg, facts.manufacturer, playbook)
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `$WT docker compose --profile test run --rm test pytest tests/test_discover_handler.py -v`
Expected: PASS.

- [x] **Step 5: Document the key**

Add a row to the `## Fields` table in `playbooks/README.md`, after the
`fetch_policy` row added in Task 2:

```markdown
| `source_priority` | no | DISCOVER ladder for this manufacturer, most-preferred rung first. Overrides the TOML `[source_priority]` map. |
```

And a short section after `## fetch_policy`:

```markdown
## `source_priority` — the ladder for one manufacturer

```json
"source_priority": ["known_url", "playbook", "manual"]
```

Omit it to inherit the TOML `[source_priority]` map, then the default ladder
(`recency, known_url, playbook, eudamed, search, email, manual`).

Rungs: `recency` (guard, never emits), `known_url`, `playbook` (authored
`kind:"direct"` doc_sources), `eudamed`, `search`, `email` and `manual`
(terminal), `vendor` (folded into `search` per G6, logs `skipped`). An unknown
rung name fails the job loudly rather than being ignored.
```

- [x] **Step 6: Commit**

```bash
git add app/playbooks.py app/handlers/discover.py playbooks/README.md tests/test_discover_handler.py
git commit -m "discover: read source_priority from the playbook, config as fallback"
```

---

### Task 7: Specs and operational docs

CLAUDE.md working rule: if a change invalidates what a doc states, the same
session updates it. Tasks 3-6 invalidate four concrete statements.

`docs/architecture.md` is **not** one of them — it documents the extraction tier
ladder and the migration list, never the DISCOVER source ladder. The ladder lives
in `docs/specs/discover.md`.

**Files:**
- Modify: `docs/specs/discover.md`
- Modify: `docs/code-map.md`
- Modify: `docs/troubleshooting.md`

- [ ] **Step 1: Correct the ladder spec**

In `docs/specs/discover.md`, line 55 currently ends:

> `email`/`manual` are **terminal returns**; `recency`/`playbook`/`vendor` are **guards/skips** that never emit and always continue.

Replace that clause with:

> `email`/`manual` are **terminal returns**; `recency`/`vendor` are **guards/skips** that never emit and always continue; `playbook` emits and returns on a hit, and continues on a miss, like `known_url`.

Replace line 58 entirely:

```markdown
- **`playbook`** — emits `fetch.url` for every authored `kind:"direct"` doc_source
  on the playbook claiming this manufacturer (`_direct_urls`). A `kind:"portal"`
  source is a LISTING page and is deliberately not fetched — turning a portal
  into documents is the crawl recipe, still Phase 2 (S2.1). No playbook, or none
  with a direct source, logs `miss` and the ladder continues.
```

In the coverage table, replace row 13:

```markdown
| 13 | configured `[source_priority]` names `vendor` | logs `skipped`; ladder proceeds | per resulting rung |
| 13a | `playbook` rung, direct sources authored | emits one `fetch.url` per URL, returns `terminal_source="playbook"` | `test_playbook_rung_is_terminal_and_logs_a_hit` |
| 13b | `playbook` rung, nothing authored | logs `miss`, ladder continues | `test_playbook_rung_falls_through_when_nothing_is_authored` |
```

and in line 150's rung list, move `playbook` out of the `playbook/vendor skip` group.

Also record the ladder order change: `Discovery.default_source_priority` now
carries `playbook` between `known_url` and `eudamed`.

- [ ] **Step 2: Update `docs/code-map.md`**

Line 14, the `app/playbooks.py` row — append to the parenthesised surface list,
after ``` `ref_strategy`'s parsing-time concern) ```:

```markdown
; `fetch_policy` (authored `needs_playwright` seed + `politeness_ms`, read by FETCH through `for_domain`, which keys on the HOST because a fetch payload carries a domain and may carry no group); `source_priority` (per-manufacturer DISCOVER ladder, superseding the TOML `[source_priority]` map). The parse is cached per directory behind a name/mtime/size signature, so a hot loop pays one scandir and a live edit to the bind-mounted directory still lands on the next job
```

Line 36, the `app/handlers/fetch.py` row — append before the closing `|`:

```markdown
; transport selection consults the playbook (`fetch_policy.needs_playwright` seeds the render tier before a 403 has to teach it; the learned `domain_lease` flag still escalates on its own and an authored `false` never de-escalates it) and so does the politeness interval (`fetch_policy.politeness_ms` replaces `cfg.fetch.politeness_ms` for claimed hosts, and `domain_lease.politeness_ms` records which value each lease was granted under)
```

Line 82, the `playbooks/` row — after "identity + BC codes + discovery config + T0 parse templates", insert:

```markdown
 + fetch policy
```

- [ ] **Step 3: Add the two troubleshooting entries**

Append to `docs/troubleshooting.md`:

```markdown
### A fetch keeps dead-lettering with "bot-wall persists"

Both transports were refused. The learned `domain_lease.needs_playwright` flag
is set only on the *success* path (`app/handlers/fetch.py`), so a host where
Playwright also fails records nothing, and every retry repeats the same two
wasted requests.

Author the host in its playbook instead:

```json
"fetch_policy": { "needs_playwright": true }
```

That skips the httpx attempt on every future job for that host. It does not fix
a host that refuses Playwright too — that one belongs in the manual queue.

### A domain is fetched faster or slower than expected

`domain_lease.politeness_ms` records the interval each lease was granted under.
If it does not match `cfg.fetch.politeness_ms`, a playbook claims that domain and
authored its own:

```sql
SELECT domain, politeness_ms, needs_playwright, leased_until
  FROM domain_lease ORDER BY domain;
```

Which playbook claims it:

```bash
python -m app.cli playbooks validate    # refuses two playbooks claiming one domain
grep -l '"<the-domain>"' playbooks/*.json
```
```

- [ ] **Step 4: Full suite**

Run: `$WT docker compose --profile test run --rm --build test`
Expected: PASS. `tests/test_vocabulary_doc.py` is the drift guard that fails when
a code value is missing from `docs/vocabulary.md`; discovery rung names are not
currently in that page, so it is not expected to flag this change — run it to
confirm that assumption rather than to satisfy it.

- [ ] **Step 5: Commit**

```bash
git add docs/specs/discover.md docs/code-map.md docs/troubleshooting.md
git commit -m "docs: playbook fetch policy, and the discover playbook rung going live"
```

---

## Out of scope — the follow-on plans

| Plan | Spec items | Depends on |
|---|---|---|
| Playbook store foundations & provenance | 5 (playbook `rev` recorded on the extraction), loader consolidation (spec §9.2) | this plan |
| Extraction accuracy | 7 (T0 lexicons), 8 (T1/T2 hints, all four guards, now carrying item 9's rep-block signal — see the spec's revised §6) | foundations — hints without `rev` break invariant 2 |
| Crawl recipe | 11 — spec first, then plan | this plan (the `playbook` rung exists and works) |
| Reonboard | 12 | foundations (`rev`) |
| Small, homeless singles — batch them into whichever plan lands first | 6 (`corpus_folders` for BACKFILL's archive bucketing), 10 (`mfr_ref_pattern` link-precision filter for VALIDATE), 13 (`gate.deny_auto` one-way strictness ratchet) | none — each is independent |

Refused in the spec and not planned: 14 (per-manufacturer grouping thresholds, until measured), 15 (per-manufacturer gate thresholds), 16 (per-manufacturer supersession), 17 (per-manufacturer expiry horizon in SQL), 18 (crawling inside FETCH).
