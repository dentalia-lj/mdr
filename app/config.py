"""Composed runtime configuration for the Dentalia pipeline.

Covers the full PRD v3 §11 config surface plus one operational `queue` section
(implementation detail, not in the PRD). Sync, stdlib-only — no pydantic, no
`toml`/`tomli` (tomllib is stdlib on 3.11+).

Precedence, highest first:
    1. environment variables  (primary; names match .env.example exactly)
    2. TOML overlay           (path from env var DENTALIA_CONFIG_TOML)
    3. code defaults          (the values baked in below)

`load_config()` is the single entrypoint; it returns a frozen `Config`.
"""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

__all__ = [
    "Config",
    "GateThresholds",
    "ResolveThresholds",
    "Discovery",
    "Adapters",
    "Models",
    "Batch",
    "Fetch",
    "Budget",
    "Renewal",
    "Email",
    "MfrBinding",
    "Ingest",
    "Validate",
    "Queue",
    "Connection",
    "Web",
    "Scheduler",
    "load_config",
]

# Env var that points at an optional TOML overlay file.
CONFIG_TOML_ENV = "DENTALIA_CONFIG_TOML"


# --------------------------------------------------------------------------- #
# Config sections (PRD v3 §11 unless noted)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class GateThresholds:
    """gate.threshold.high / gate.threshold.med

    `high` is 0.92 by Denis's ruling of 2026-08-19, replacing the 0.95 the
    scaffold shipped. Recorded as a ruling, not a measurement: no calibration
    artefact exists in the tree -- `docs/superpowers/plans/2026-08-13-calibrate-
    gate-thresholds.md` is still at 0 of 10 steps and `tools/calibration.py` was
    never written -- so `[gate-thresholds]` stays open for the measurement that
    would confirm or move it.

    It is one number gating four separate decisions in `gate.py`, which is what
    makes a change here wider than it looks: `coverage_scope` confidence, the
    supersession fast path's `validity_from`, the document-level production
    disposition, and per-link production. `med` is untouched.
    """

    high: float = 0.92
    med: float = 0.75


@dataclass(frozen=True)
class ResolveThresholds:
    """resolve.name_accept / resolve.name_suggest / resolve.name_candidate_k (G5a).

    Name-family scoring is rapidfuzz token_set_ratio/100 in [0,1]. score >= accept
    -> deterministic group join; [suggest, accept) -> T1 + staging; < suggest ->
    singleton. No Phase-0 seed exists (Phase 0 measured document extraction, not
    name clustering) — these are conservative defaults, recalibrated on the S1.7
    sweep. candidate_k caps the pg_trgm candidate set.

    `name_suggest` is set EQUAL to `name_accept`, which collapses the staging
    band to nothing: an item either joins a matched family or becomes a
    singleton (Denis's ruling, 2026-08-12). The band was not a review queue, it
    was a hole in the registry. A staged item gets NO `item_group_member` row,
    and every REF-gate path joins through that table
    (`_manufacturers_holding_refs`, `_scoped_groups`, `_resolve_group_unscoped`
    in validate.py) — so a staged item is invisible to document linking
    entirely. Measured on the live catalogue: 15.958 items, 10.127 grouped,
    5.831 staged and ungrouped. 36,5% of the catalogue no document could reach,
    and perversely a WORSE name match (< suggest) got you a singleton and stayed
    reachable while a better one did not.

    What the band bought was product-family pooling, which serves DISCOVER (one
    web search per family instead of per SKU) — and DISCOVER is held for the
    corpus-first cold start. So the cost is deferred and the benefit immediate.
    Reversible: lower `name_suggest` and re-run `regroup`. Note the
    `item_group_member` PK is (group_id, item_ref), so a singleton does not
    prevent the item joining a real family later.

    Linking never depended on this. Documents reach items through the REF gate
    on (manufacturer, mfr_ref) or Basic UDI-DI; names only ever decided grouping."""

    name_accept: float = 0.90
    name_suggest: float = 0.90
    name_candidate_k: int = 20


@dataclass(frozen=True)
class Discovery:
    """DISCOVER source-ladder tunables (S1.3). No Phase-0 seed exists (Phase 0
    measured document extraction, not discovery) — conservative defaults,
    recalibrated on the S1.7 sweep. See docs/specs/discover.md §6.

    `topk` caps fetch.url emitted from the search rung (handbook k=3);
    `rank_threshold` is the min T1 score to fetch a search candidate (the
    "threshold rule" of PRD §3 — the fetch/no-fetch decision, not the LLM);
    `recency_days` is the known-URL re-check window (the recency guard — the
    fetch ledger stays the real invariant-6 guard); `max_search_candidates`
    caps how many results are pulled from the adapter before ranking.

    `default_source_priority` is the fallback ladder for manufacturers absent
    from BOTH the playbook `source_priority` key and the top-level
    `[source_priority]` map (the playbook wins; this map predates it and still
    serves any manufacturer without one). `playbook` sits third: it emits the
    authored `kind:"direct"` doc_sources, which is the cheapest discovery there
    is, and costs nothing for a manufacturer that has authored none. Only the
    crawl recipe over `kind:"portal"` remains Phase 2. `vendor` stays folded
    into `search` per G6 -- omitted from the default, tolerated (log `skipped`)
    if a config list names it.

    `email_rung_enabled` gates the PRODUCER side of the email rung: until the
    S2.4 EMAIL handler exists, an emitted `email.request` lands on the no-op and
    completes silently — so the rung logs `skipped` and falls through to manual
    instead. Flip on with S2.4.

    `hold` is the corpus-first cold start. RESOLVE must run before BACKFILL (it
    is what builds the groups a backfill document resolves against), but at that
    moment nothing is covered yet, so the C3 "already has a doc/candidate"
    suppression in resolve.py cannot fire and every group would web-search for
    documents already sitting in the local corpus. With `hold` on, RESOLVE builds
    groups and emits no discovery; the corpus is mined first; discovery then
    decides against a registry that knows what it already has. Reversible by
    construction — the discover dedupe key carries a fixed `_RESOLVE_CYCLE` and
    dedupe is active-scope only (invariant 8), so `cli regroup --apply` after
    dropping the hold re-enqueues resolve.group and discovery fires for exactly
    the groups the corpus did not cover. Off by default: steady state discovers.
    """

    topk: int = 3
    rank_threshold: float = 0.60
    recency_days: int = 30
    max_search_candidates: int = 10
    #: Ceiling on how many crawl links one recipe may hand the T1 judge when a
    #: library page yields more than its `max_links` (ruled 2026-09-04,
    #: `[crawl-ai-link-triage]`). Above it the recipe's `link_pattern` is too
    #: broad to pay for: the rung logs `pattern-too-broad` and caps in page
    #: order, the pre-ruling behaviour.
    #:
    #: 600 until 2026-09-07 and that was a guess made without a real library in
    #: front of it: NSK's serves **1.329** unique documents, so the largest
    #: catalogue we hold tripped the ceiling and would have fallen back to page
    #: order -- the exact behaviour the ruling replaced. 1.500 clears it with
    #: room; at 50 links a batch that is ~30 Haiku calls, still cents.
    crawl_rank_max: int = 1500
    #: A crawl with fewer surviving links than this is not a collection worth
    #: a ranker call: everything in it is fetched in page order.
    crawl_rank_floor: int = 5
    email_rung_enabled: bool = False
    hold: bool = False
    #: How many groups one press of the manufacturer page's discover button
    #: queues. 25, measured 2026-09-02 against the live registry: 365 of 367
    #: manufacturers have uncovered groups, the median has 3 and 84% have 25 or
    #: fewer -- so one press finishes most manufacturers outright, and the
    #: handful of giants (HENRY SCHEIN 809, INSTITUT STRAUMANN AG 329) take
    #: repeated presses that stay visible and interruptible. Uncapped, one click
    #: on Henry Schein queues up to 2.400 fetches at `topk` 3, most of them
    #: against a single domain at the 2s politeness interval.
    manufacturer_button_cap: int = 25
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


@dataclass(frozen=True)
class Adapters:
    """source_adapter / storage_adapter / search_adapter / email_adapter —
    implementation is invisible downstream (CLAUDE.md invariant 11).

    `email` default `imap` is safe: the S2.4 poll is flag-gated off
    (`scheduler.email_poll_enabled`) and `ImapEmailAdapter.fetch_new` raises
    `EmailNotConfigured` while host/creds are empty, so nothing connects until
    Denis wires the mailbox (GAP G8). Tests inject `FakeEmailAdapter`."""

    source: str = "csv"
    # `local`, not `gdrive` -- Denis's ruling of 2026-07-31 ([storage-default]).
    # GoogleDriveStore.put() raises until G7 is answered, so a gdrive default
    # dead-letters every fetch; gdrive is the explicit opt-in if that day comes.
    # The ruling was applied to load_config() alone and this field kept saying
    # gdrive for eleven weeks -- harmless only because the loader never read it.
    # Corrected 2026-08-19, and the loader now reads these instead of repeating
    # them, so the pair cannot disagree again.
    storage: str = "local"
    search: str = "brave"
    email: str = "imap"


@dataclass(frozen=True)
class Storage:
    """FETCH archive backing (S1.4). `local_root`/`base_url` drive LocalFsStore;
    `gdrive_folder_id` is the Drive root (unused until G7 — see docs/specs/fetch.md
    §2). Selection is via `Adapters.storage`."""

    local_root: str = "./archive"
    base_url: str = ""  # empty -> LocalFsStore returns a file:// URI
    gdrive_folder_id: str = ""  # G7 — inert until GoogleDriveStore lands


@dataclass(frozen=True)
class Models:
    """models.t1 / models.t2 / models.rank / models.email_summary"""

    t1: str = "claude-haiku-4-5"
    t2: str = "claude-sonnet-5"
    rank: str = "claude-haiku-4-5"
    # Inbound email-body summarisation (S2.4, invariant 12 widened + ratified
    # 2026-08-20). Cheap tier only, and deliberately its own key rather than
    # reusing `rank`: the two would otherwise move together in a tier swap that
    # only one of them was diffed for.
    email_summary: str = "claude-haiku-4-5"


@dataclass(frozen=True)
class Batch:
    """batch.poll_interval"""

    poll_interval_s: int = 900


@dataclass(frozen=True)
class Fetch:
    """per-domain politeness / recency window"""

    politeness_ms: int = 2000
    recency_window_days: int = 21
    # How long a robots.txt read stays good. One request per host per window is
    # the politeness argument for reading it without taking the domain lease --
    # the lease interval is what robots.txt is being read to determine, so
    # waiting on it first is circular (`app/robots.py`).
    robots_ttl_hours: int = 24


@dataclass(frozen=True)
class Budget:
    """sweep + monthly budget caps (EUR).

    `eur_per_usd`: extraction_cost.cost_usd is USD; these caps are EUR
    (docs/specs/kpi.md K8). An approximate, manually-updated conversion
    factor — not a live FX rate — rendered next to the converted figure on
    the KPI board so a stale rate is visible rather than silently wrong."""

    sweep_cap_eur: float = 100.0
    monthly_cap_eur: float = 40.0
    eur_per_usd: float = 0.92


@dataclass(frozen=True)
class Renewal:
    """renewal.horizon_days — no env var; TOML- or default-driven.

    30 days is Dentalia's ruling of 2026-08-18 ("bi rekla mesec dni prej"),
    for every document type alike; it replaced the (90, 60, 30) placeholder.
    The scheduler scans at max(...), so this single value IS the horizon --
    raising it is one edit here, which matters because their own reason ("glede
    na to kako dolgo traja da dobimo dokumente") may argue for more once the
    renewal loop has run a few times."""

    horizon_days: tuple[int, ...] = (30,)

    # S2.4 outbound cadence (client ruling 2026-08-20, spec §7.2): at most ONE
    # renewal mail per manufacturer per this many days, listing everything
    # expiring for them. 7 = weekly, which is what Denis asked for; 14 is the
    # other value he named. This is the `period_key` half of
    # `email.request:mfr:{manufacturer}:{period_key}`, so changing it changes
    # the dedupe bucket -- in-flight requests keep their old key and drain.
    request_cadence_days: int = 7

    # How long a request waits before `email.reminder` drafts a chase. Not the
    # same clock as the cadence: the cadence stops us mailing a manufacturer
    # twice in a week, this decides when silence becomes a follow-up.
    reminder_after_days: int = 14

    # Reminders before the chase stops drafting and asks for a human. Without a
    # cap a manufacturer who never replies generates a draft every fortnight
    # forever, and the drafts board becomes noise nobody reads.
    escalate_after_reminders: int = 3


@dataclass(frozen=True)
class Email:
    """EMAIL config (S2.4). `send_policy` is outbound (unchanged); the rest is
    the inbound `email.poll` mailbox — operational, non-secret. IMAP credentials
    live in `Connection` (empty default, env-only), not here, following the
    `SERPER_API_KEY` precedent: a credential is not a tuning key.

    `send_policy` — 'draft' = draft-for-approval (default), 'auto' (outbound slice).
    `imap_host`/`imap_port`/`imap_ssl`/`imap_folder` — the polled mailbox.
    `poll_since` — ISO date (YYYY-MM-DD). Mail that arrived before it is never
    read. Required once the mailbox is configured: the poll refuses to run
    without it rather than read a whole mailbox's history (Denis, 2026-09-24:
    "only from the day of first prod deploy").
    `poll_max_messages` — messages taken per poll. Pacing, not a limit: new mail
    past it stays for the next poll and is counted on the job result."""

    send_policy: str = "draft"
    imap_host: str = ""
    imap_port: int = 993
    imap_ssl: bool = True
    imap_folder: str = "INBOX"
    poll_since: str = ""
    poll_max_messages: int = 200

    # ZIP expansion (2026-08-20). Suppliers send archives of documents, and the
    # magic-byte gate correctly refuses the archive itself -- so without this
    # the PDFs inside were simply lost, counted as `non-pdf` and never chased.
    # Every bound below exists because an attacker (or a careless supplier)
    # controls the bytes: a 42 KB zip can expand to 4.5 GB.
    zip_expand: bool = True
    zip_max_members: int = 50          # entries considered per archive
    zip_max_member_mb: int = 25        # uncompressed size of one entry
    zip_max_total_mb: int = 100        # uncompressed size of the whole archive
    zip_max_ratio: int = 200           # uncompressed:compressed, the bomb guard

    # Body summarisation (2026-08-20). On by default because the poll itself is
    # flag-gated off (SCHEDULER_EMAIL_POLL_ENABLED) -- a mailbox that is not
    # polled cannot spend anything, and enabling the poll without the context
    # it was built for would be the surprising default, not this one.
    summary_enabled: bool = True
    # 0 -- no floor: if there is a body, it gets a summary, however short
    # (Denis, 2026-08-21). The floor was 120, then 40, on the theory that a
    # one-liner is already its own summary. It is not, because the body is
    # never stored: skipping the call left the row with no text at all, and
    # half the fixture mailbox rendered emptier than the long messages beside
    # it. The intent classification earns the call at any length, and cost was
    # never what this bound was for (Denis, 2026-08-20). The key survives as a
    # throttle for a mailbox noisy enough to want one, so `too-short` stays in
    # the status vocabulary rather than being deleted.
    summary_min_chars: int = 0
    summary_max_chars: int = 6000      # input cap; the tail of a long thread is quoted history
    summary_max_tokens: int = 200      # output cap: one or two sentences, not a retelling


@dataclass(frozen=True)
class MfrBinding:
    """mfr_binding.auto_link (default on) with a per-manufacturer override map.
    A paranoid manufacturer can be pinned to per-group review even after
    binding by setting its entry to false (PRD §11 / C4)."""

    auto_link: bool = True
    per_manufacturer: dict[str, bool] = field(default_factory=dict)


@dataclass(frozen=True)
class Ingest:
    """INGEST behaviour (S1.1). Not a PRD §11 key set — operational toggles that
    turn Phase 0's data findings into policy without a code change.

    `process_md_unknown`: BC's device-class column is tri-state — a real MD class
    (RAZRED *), an explicit non-MD marker (NI MP), or BLANK (61% of the LJ export,
    device class not yet recorded). `md_flag` stores all three (True/False/None).
    The handler always skips explicit non-MD (False). This flag governs the
    *unknown* (None) rows: True (default) enqueues them for resolution and counts
    them as `md_unknown` — dropping a real-but-unclassified MD is the costlier
    compliance error, and the choice is reversible before the sweep (S1.7). False
    holds them out of the pipeline (still counted), for a strict-cost run.

    `mfr_ref_source_by_code`: C1 says `mfr_ref` is a LOGICAL field the adapter may
    map per supplier (PRD §1 "per-supplier mapping config if mixed"). Keyed by BC
    manufacturer code (`Šifra proizvajalca`, present at ingest time), value is the
    physical source: 'mfr_ref' (default for absent codes — the vendor-article-no
    column) or 'item_ref' (BC item No. itself is that supplier's article number,
    the Phase 0 finding for the CONFIRMED plain-style suppliers). Empty by default;
    real per-supplier values are the open G4 residue pending BC/IT confirmation."""

    process_md_unknown: bool = True
    mfr_ref_source_by_code: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Validate:
    """VALIDATE tunables. Not a PRD §11 key set — one measured threshold that
    turns a catalogue property into policy without a code change.

    `min_unscoped_ref_len`: the shortest extracted REF allowed to form an
    unscoped `ref-catalogue` link ([validate-ref-catalogue]). Short article
    numbers are not distinctive: measured 2026-08-11, the catalogue holds 85
    REFs of 1-3 characters (`100` -> INTERDENT, `102` -> SANOLABOR, `158` ->
    ULTRADENT). Each is "unambiguous" in the collision sense and worthless as
    identity — a GC declaration listing REF `100` would link to an INTERDENT
    item. At 6, 4.695 of 5.511 distinct REFs (85,2%) stay eligible and every
    one of the real GC corpus matches survives (all are 6 characters). Applies
    ONLY to the unscoped path; the scoped `(manufacturer, ref)` gate has the
    manufacturer to make even a 3-character REF meaningful and is untouched."""

    min_unscoped_ref_len: int = 6


@dataclass(frozen=True)
class Queue:
    """Operational queue tuning — NOT in PRD §11; backs the S0.1 reclaim rule.

    A `running` job whose `claimed_at` is older than `visibility_timeout_s` is
    reclaimable. Retry delay grows from `backoff_base_s`, capped at
    `backoff_cap_s`; after `max_attempts` a job goes to the dead letter.
    `max_attempts` mirrors the schema's job.max_attempts default.

    `visibility_timeout_s` was 300 until 2026-09-02 and that was shorter than
    the longest legitimate job. IVOCLAR's `eudamed.sweep` is 11.366 devices over
    38 pages at `fetch.politeness_ms`; job 35679 was reclaimed at exactly 301s
    while still fetching, and because `app/workers/runner.py` wraps a handler in
    ONE transaction, every page it had already stored rolled back. Raised to
    1800 so the longest sweep we hold fits with margin.

    The cost is honest and global: a worker that genuinely dies now holds its
    job for up to 30 minutes instead of 5. Acceptable at this volume (one worker
    replica, low job rate) and it is config, so it is reversible per
    environment. The structural fix -- a sweep that commits and self-defers per
    page, so no single claim is ever long -- is
    `[eudamed-sweep-is-one-long-transaction]`; this raise is the mitigation
    until that lands, not a substitute for it."""

    visibility_timeout_s: int = 1800
    backoff_base_s: int = 5
    backoff_cap_s: int = 3600
    max_attempts: int = 5


@dataclass(frozen=True)
class Alerts:
    """Where an operator alert goes. NOT in PRD §11; operational only.

    `webhook_url` empty (the default) means alerting is off, which is the right
    default everywhere except the one machine that runs this for real: tests, CI
    and a developer laptop must not post anywhere.

    The URL is the whole credential. An ntfy topic is readable and writable by
    anyone who knows its name and names are enumerable, so a short one leaks job
    failures and manufacturer names and lets a stranger post alerts that look
    like ours. Use a long random topic.
    """

    webhook_url: str = ""


@dataclass(frozen=True)
class Bc:
    """Writing three compliance fields back into Business Central (`bc.push`).

    `write_enabled` is false everywhere by default and that is the point: this
    is the one stage that changes data in a system we do not own, so reaching it
    has to be a deliberate act on one machine rather than a consequence of
    running the code. Off, the handler still computes and reports the diff --
    which is exactly what the bulk preview shows an operator.

    `base_url` is the company-scoped OData root b-s.si issued; the writable
    fields live on its `dataitems` page and `allitems` is read-only.
    """

    write_enabled: bool = False
    base_url: str = ""
    #: A Windows domain account, `DOMAIN\user`: BC logs in with NTLM inside
    #: `Negotiate` (measured 2026-09-24, `app/adapters/bc_client.py`). Empty
    #: means no `Authorization` header at all -- an empty credential is worse
    #: than none, since some servers read it as an anonymous identity.
    username: str = ""
    password: str = ""
    #: Items the drift cron may re-evaluate per run. A rolling window over the
    #: catalogue rather than a full sweep: at 1000/day a 16k catalogue comes
    #: round about every 16 days, and an unchanged item costs a diff and no
    #: PATCH. Raise it when the write path has proven itself against real BC.
    drift_cap: int = 1000


@dataclass(frozen=True)
class Connection:
    """Connection strings and secrets. Secrets default empty — supplied by env."""

    database_url: str = "postgresql://dentalia:dentalia@localhost:5432/dentalia"
    anthropic_api_key: str = ""
    brave_api_key: str = ""
    serper_api_key: str = ""   # Serper fallback SearchAdapter (adapters.search="serper")
    imap_user: str = ""        # S2.4 email.poll mailbox login (GAP G8 — empty until wired)
    imap_password: str = ""    # S2.4 email.poll mailbox password (secret, env-only)


@dataclass(frozen=True)
class Web:
    """Standalone producer-UI process — NOT in PRD §11 (implementation detail,
    same footing as `Queue`). Physically separate container from the worker
    (CLAUDE.md architecture deviation — see PHASES.md); connects
    to Postgres as `dentalia_api`, never as the schema owner (Invariant 1).

    `api_database_url` defaults to the `dentalia_api` role so a bare `docker
    compose up` can't accidentally grant the UI owner privileges by omission.

    `require_authenticated_user` (G3 v0, S1.6): when true, `decided_by` on a
    `gate.apply` decision comes from `trusted_user_header` (set by the Caddy
    reverse proxy after a successful Basic-auth check — see docker-compose.yml
    `caddy` service) rather than a typed-in form field; a missing/empty header
    is a 403, since it means the request bypassed the proxy. Compose sets this
    true; local dev / tests default false (no proxy in front).

    `playbooks_dir` is a TEST AND DEV SEAM, and since slice 4 (2026-08-27) it
    is nothing else: compose no longer mounts `./playbooks` into `web`, which
    reads playbooks from `manufacturer.body` and the refused-host list from
    `refused_host`. Setting this makes the UI read FILES instead -- an explicit
    directory always wins over the configured source, by the same rule that
    lets `manufacturers seed` import into the database it would otherwise be
    reading. Useful for reproducing a malformed-file bug against the real
    wiring (`tests/test_web.py`); **do not set it in production**, where it
    would silently show and save against a second, staler copy. Empty means
    `app.playbooks.PLAYBOOKS_DIR`, which after slice 4 exists in the worker
    image (the seed needs it) and not in the web one.
    """

    host: str = "127.0.0.1"
    port: int = 8000
    imports_dir: str = "/imports"
    upload_max_mb: int = 25
    api_database_url: str = (
        "postgresql://dentalia_api:dentalia_api@localhost:5432/dentalia"
    )
    require_authenticated_user: bool = False
    trusted_user_header: str = "X-Forwarded-User"
    archive_root: str = "./archive"
    #: Where `/reports` reads the weekly HTML copies from. Empty = the page
    #: says so rather than 404ing; the worker writes them, `web` only reads.
    reports_dir: str = ""
    playbooks_dir: str = ""
    # Prefix rewrites for stored archive paths, "from=to[,from=to]".
    # `document.archive_url` records an absolute path as seen by whatever
    # WROTE it — for backfilled corpus documents that is a host path, which
    # does not exist inside the web container where the same bytes are mounted
    # elsewhere. This declares the mapping (e.g.
    # "/home/x/dentalia/imports=/imports") so relocating the archive is an
    # environment change, never a code change. Optional: the file route also
    # falls back to a content-hash-verified search.
    path_rewrites: str = ""
    # Access credentials for the two non-browser caller classes
    # (docs/superpowers/specs/2026-08-25-item-document-access-design.md §8).
    # Both are SECRETS: .env, never a runbook tuning key.
    #
    # `api_keys` is comma-separated so a second consumer, or a rotation, is an
    # env change rather than a downtime swap. Empty disables the `service`
    # class entirely -- empty never means "allow everyone".
    api_keys: str = ""
    # Shared static key for the BC item-card hyperlink, `/item/{ref}?k=...`.
    # NOT per-item and deliberately so: a BC hyperlink is a computed field
    # built by string concatenation, so anything BC must compute is unusable
    # (Denis, 2026-08-25). Empty 404s `/item/*`.
    bc_link_key: str = ""
    # Absolute base for URLs emitted in responses. Empty means relative, which
    # is correct for same-origin browser use and wrong for a webshop backend
    # embedding our links in its own pages.
    public_base_url: str = ""
    # The proxy logins that count as operators (office UI redesign spec § 2,
    # D6), matched exactly against `trusted_user_header`. EMPTY MEANS EVERY
    # LOGIN IS AN OPERATOR: that keeps today's single shared login working
    # unchanged until named logins exist (slice P5b fills the list). Read by
    # `web.access.is_operator`. Not a secret, but it only means anything
    # behind the proxy: with `require_authenticated_user` off there is no
    # login at all, so a non-empty list makes nobody an operator.
    operator_users: tuple[str, ...] = ()


@dataclass(frozen=True)
class Scheduler:
    """SCHEDULER cron cadence (S1.5) — operational, NOT in PRD §11, same footing
    as `Queue`/`Web`.

    `ingest_watch_dir` empty (default) = the monthly ingest tick is a no-op
    (mirrors `Storage.gdrive_folder_id`'s inert-until-configured pattern) — no
    live BC feed exists yet (G4 residue). When set, the tick globs
    `ingest_glob` under that dir and enqueues `ingest.run` against the
    lexicographically-latest match for `ingest_catalogue`.

    `expiry_email_enabled` / `failure_reonboard_enabled` gate emission of
    `email.request` / `playbook.reonboard` (both default off — S2.4/S2.1 don't
    exist yet). The underlying scans always run for real and always feed the
    weekly report regardless of these flags (PHASES.md GAP E)."""

    tick_interval_s: int = 300
    ingest_watch_dir: str = ""
    ingest_glob: str = "*.xlsx"
    ingest_catalogue: str = "LJ"
    expiry_scan_interval_hours: int = 24
    expiry_email_enabled: bool = False
    #: On expiry, also go back to the manufacturer's own sources and look for a
    #: newer document -- not just draft a letter. **Defaults off, and it must.**
    #: `DISCOVER_HOLD` is read only in `resolve.py`, so a cron enqueueing
    #: `discover.group` bypasses it entirely; this flag is the only thing
    #: standing between a daily tick and unattended fetching.
    #: Where `report.weekly` writes its HTML copy. Empty (default) = write no
    #: file, the same inert-until-configured shape `ingest_watch_dir` uses.
    #: Compose points it at `/archive/reports`, inside the volume the worker
    #: already writes and `web` already mounts read-only, so a report survives
    #: a container restart the same way an archived document does.
    report_dir: str = ""
    expiry_rediscover_enabled: bool = False
    #: Groups per tick. Measured 2026-09-02: 86 production documents are
    #: expiring or lapsed inside a 30-day horizon and they cover **431 groups**,
    #: because one document covers many -- so an uncapped tick would queue
    #: roughly 1.300 fetches. Least recently discovered first, so consecutive
    #: ticks walk the backlog instead of repeating its head.
    expiry_rediscover_cap: int = 25
    #: The coverage cron: groups holding no production document go through
    #: DISCOVER. **Defaults off.** Denis ruled 2026-09-02 that `DISCOVER_HOLD`
    #: stays narrow -- it gates RESOLVE's emission only -- so this flag, not the
    #: hold, is what stops this cron.
    coverage_scan_enabled: bool = False
    #: Groups per day. 25 matches the manufacturer button, deliberately: the
    #: same number means the cron is a trickle behind whatever a person is
    #: already doing by hand. Measured 2026-09-02: 6.732 groups hold no
    #: production document and 6.693 have never been discovered, so 25/day is a
    #: ~9-month backfill -- which is why `app.coverage` orders never-discovered
    #: first. A stable ordering under a cap never finishes.
    coverage_scan_cap: int = 25
    failure_monitor_interval_hours: int = 24
    failure_window_days: int = 30
    failure_miss_rate_threshold: float = 0.5
    failure_min_sample: int = 5
    failure_reonboard_enabled: bool = False
    # S2.4 inbound: `email_poll_enabled` gates emission of `email.poll` (default
    # off — no mailbox creds exist, GAP G8; same producer-side gating as the
    # email.request/reonboard flags). `email_poll_interval_hours` sizes the poll
    # period bucket — a mailbox wants sub-day cadence (replies), unlike the
    # day-granularity scans.
    email_poll_enabled: bool = False
    email_poll_interval_hours: int = 6
    # `bc.push-drift` needs BOTH this and `bc.write_enabled`. Separate so writes
    # can be on for the item button and the bulk apply while the cron stays off
    # until the first bulk run has been checked: with an empty ledger every item
    # is "changed" (Denis, 2026-09-24).
    bc_push_drift_enabled: bool = False
    # EUDAMED (Denis, 2026-08-26: "certificates monthly, device sweeps
    # quarterly"). `eudamed_certregister_enabled` gates emission of
    # `eudamed.certregister` -- default off, same footing as the flags above.
    # Its cadence is a DAY count, not hours, and deliberately does NOT feed
    # `period_key_interval`: that function buckets on `now.hour // hours`, and
    # `now.hour` never reaches 24, so any `hours >= 24` always resolves to
    # band 0 -- multiplying this by 24 would silently degrade a 30-day cadence
    # into a DAILY tick (the date component of that function's key still
    # changes every day). `_tick_eudamed_certregister` buckets on the calendar
    # day itself instead (`_period_key_days`, app/scheduler.py).
    #
    # `eudamed_sweep_interval_days` is the per-manufacturer sweep's staleness
    # threshold, read by `_tick_eudamed_sweep_due` -- which only ever marks a
    # manufacturer due and never enqueues (the ruling this pair protects: "no
    # sweep ever runs unattended, an operator releases every run").
    eudamed_certregister_enabled: bool = False
    eudamed_certregister_interval_days: int = 30
    eudamed_sweep_interval_days: int = 90


@dataclass(frozen=True)
class Config:
    """Top-level composed configuration."""

    gate: GateThresholds
    resolve: ResolveThresholds
    discovery: Discovery
    adapters: Adapters
    storage: Storage
    models: Models
    batch: Batch
    fetch: Fetch
    budget: Budget
    renewal: Renewal
    email: Email
    mfr_binding: MfrBinding
    ingest: Ingest
    validate: Validate
    queue: Queue
    alerts: Alerts
    bc: Bc
    connection: Connection
    web: Web
    scheduler: Scheduler
    # Per-manufacturer source-priority list (PRD §11, data-driven). Placeholder
    # until playbooks land (Phase 2); loaded from TOML `[source_priority]`.
    # Shape: {canonical_manufacturer: [source_tag, ...]}.
    source_priority: dict[str, list[str]] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Loading helpers
# --------------------------------------------------------------------------- #
def _load_toml() -> dict:
    """Parse the optional TOML overlay named by DENTALIA_CONFIG_TOML.

    Missing env var -> {}. A set-but-missing/broken file is a hard error: a
    silently ignored config file is a footgun."""

    path = os.environ.get(CONFIG_TOML_ENV)
    if not path:
        return {}
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def _dig(d: dict, *path: str):
    """Return nested dict value at `path`, or None if any hop is absent."""

    cur = d
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def _str(env_key: str | None, toml_val, default: str) -> str:
    if env_key and env_key in os.environ:
        return os.environ[env_key]
    if toml_val is not None:
        return str(toml_val)
    return default


def _float(env_key: str | None, toml_val, default: float) -> float:
    if env_key and env_key in os.environ:
        return float(os.environ[env_key])
    if toml_val is not None:
        return float(toml_val)
    return default


def _int(env_key: str | None, toml_val, default: int) -> int:
    if env_key and env_key in os.environ:
        return int(os.environ[env_key])
    if toml_val is not None:
        return int(toml_val)
    return default


def _bool(env_key: str | None, toml_val, default: bool) -> bool:
    if env_key and env_key in os.environ:
        return os.environ[env_key].strip().lower() in {"1", "true", "yes", "on"}
    if toml_val is not None:
        return bool(toml_val)
    return default


def _str_list(env_key: str | None, toml_val, default: tuple[str, ...]) -> tuple[str, ...]:
    """A list of names: comma-separated in env (or a TOML string), a TOML
    array otherwise. Whitespace is stripped and empty entries dropped, so a
    trailing comma or `" a, b "` cannot smuggle in an empty name that would
    match an empty header."""
    if env_key and env_key in os.environ:
        items = os.environ[env_key].split(",")
    elif toml_val is not None:
        items = toml_val.split(",") if isinstance(toml_val, str) else [str(v) for v in toml_val]
    else:
        return default
    return tuple(v.strip() for v in items if v.strip())


# --------------------------------------------------------------------------- #
# Entrypoint
# --------------------------------------------------------------------------- #
def load_config() -> Config:
    """Compose config from env (primary) over TOML overlay over code defaults."""

    t = _load_toml()

    gate = GateThresholds(
        high=_float("GATE_THRESHOLD_HIGH", _dig(t, "gate", "threshold_high"),
                    GateThresholds.high),
        med=_float("GATE_THRESHOLD_MED", _dig(t, "gate", "threshold_med"),
                   GateThresholds.med),
    )

    resolve = ResolveThresholds(
        name_accept=_float(
            "RESOLVE_NAME_ACCEPT", _dig(t, "resolve", "name_accept"), 0.90
        ),
        # Equal to name_accept by default: collapses the staging band, which
        # withheld group membership (and therefore all document linking) from
        # 36,5% of the catalogue. See ResolveThresholds' docstring. Kept
        # overridable so the band can be restored without a code change.
        name_suggest=_float(
            "RESOLVE_NAME_SUGGEST", _dig(t, "resolve", "name_suggest"), 0.90
        ),
        name_candidate_k=_int(
            "RESOLVE_NAME_CANDIDATE_K", _dig(t, "resolve", "name_candidate_k"), 20
        ),
    )

    # discover.default_source_priority: TOML overlay (list) or the Phase-1 ladder.
    raw_dsp = _dig(t, "discover", "default_source_priority")
    discovery = Discovery(
        topk=_int("DISCOVER_TOPK", _dig(t, "discover", "topk"), 3),
        rank_threshold=_float(
            "DISCOVER_RANK_THRESHOLD", _dig(t, "discover", "rank_threshold"), 0.60
        ),
        recency_days=_int(
            "DISCOVER_RECENCY_DAYS", _dig(t, "discover", "recency_days"), 30
        ),
        max_search_candidates=_int(
            "DISCOVER_MAX_SEARCH_CANDIDATES",
            _dig(t, "discover", "max_search_candidates"),
            10,
        ),
        crawl_rank_max=_int(
            "DISCOVER_CRAWL_RANK_MAX", _dig(t, "discover", "crawl_rank_max"), 1500
        ),
        crawl_rank_floor=_int(
            "DISCOVER_CRAWL_RANK_FLOOR", _dig(t, "discover", "crawl_rank_floor"), 5
        ),
        email_rung_enabled=_bool(
            "DISCOVER_EMAIL_RUNG_ENABLED",
            _dig(t, "discover", "email_rung_enabled"),
            False,
        ),
        hold=_bool("DISCOVER_HOLD", _dig(t, "discover", "hold"), False),
        manufacturer_button_cap=_int(
            "DISCOVER_MANUFACTURER_BUTTON_CAP",
            _dig(t, "discover", "manufacturer_button_cap"),
            25,
        ),
        default_source_priority=(
            [str(s) for s in raw_dsp]
            if raw_dsp is not None
            else Discovery().default_source_priority
        ),
    )

    # Defaults come from the dataclass fields, never repeated here: the same
    # duplication shipped a 90-day renewal horizon against a 30-day ruling and
    # left `storage` defaulting to an adapter that raises.
    adapters = Adapters(
        source=_str("SOURCE_ADAPTER", _dig(t, "adapters", "source"), Adapters.source),
        storage=_str("STORAGE_ADAPTER", _dig(t, "adapters", "storage"), Adapters.storage),
        search=_str("SEARCH_ADAPTER", _dig(t, "adapters", "search"), Adapters.search),
        email=_str("EMAIL_ADAPTER", _dig(t, "adapters", "email"), Adapters.email),
    )

    storage = Storage(
        local_root=_str(
            "STORAGE_LOCAL_ROOT", _dig(t, "storage", "local_root"), "./archive"
        ),
        base_url=_str("STORAGE_BASE_URL", _dig(t, "storage", "base_url"), ""),
        gdrive_folder_id=_str(
            "STORAGE_GDRIVE_FOLDER_ID", _dig(t, "storage", "gdrive_folder_id"), ""
        ),
    )

    if adapters.storage == "local" and not os.path.isabs(storage.local_root):
        log.warning(
            "storage.local_root %r is relative: archived files land under the "
            "process working directory and are lost on container restart. "
            "Set STORAGE_LOCAL_ROOT to an absolute path backed by a volume.",
            storage.local_root,
        )

    models = Models(
        t1=_str("MODELS_T1", _dig(t, "models", "t1"), "claude-haiku-4-5"),
        t2=_str("MODELS_T2", _dig(t, "models", "t2"), "claude-sonnet-5"),
        rank=_str("MODELS_RANK", _dig(t, "models", "rank"), "claude-haiku-4-5"),
        email_summary=_str(
            "MODELS_EMAIL_SUMMARY", _dig(t, "models", "email_summary"),
            Models.email_summary,
        ),
    )

    batch = Batch(
        poll_interval_s=_int(
            "BATCH_POLL_INTERVAL_S", _dig(t, "batch", "poll_interval_s"), 900
        ),
    )

    fetch = Fetch(
        politeness_ms=_int(
            "FETCH_POLITENESS_MS", _dig(t, "fetch", "politeness_ms"), 2000
        ),
        recency_window_days=_int(
            "FETCH_RECENCY_WINDOW_DAYS", _dig(t, "fetch", "recency_window_days"), 21
        ),
        robots_ttl_hours=_int(
            "FETCH_ROBOTS_TTL_HOURS", _dig(t, "fetch", "robots_ttl_hours"), 24
        ),
    )

    budget = Budget(
        sweep_cap_eur=_float(
            "BUDGET_SWEEP_CAP_EUR", _dig(t, "budget", "sweep_cap_eur"), 100.0
        ),
        monthly_cap_eur=_float(
            "BUDGET_MONTHLY_CAP_EUR", _dig(t, "budget", "monthly_cap_eur"), 40.0
        ),
        eur_per_usd=_float(
            "BUDGET_EUR_PER_USD", _dig(t, "budget", "eur_per_usd"), 0.92
        ),
    )

    # renewal.horizon_days: TOML overlay or default; no env var (PRD §11).
    # The fallback is the dataclass's own default, read rather than repeated.
    # It used to be a second, literal (90, 60, 30) -- which is what actually
    # shipped, because this constructor always passes horizon_days= explicitly
    # and so never consults the default beside it. Dentalia's 30-day ruling
    # (2026-08-18) was applied to the dataclass alone and the process kept
    # scanning at 90 for a day ([renewal-horizon-default-ignored]). Naming the
    # field's default here means the next ruling only has to be applied once.
    horizon = _dig(t, "renewal", "horizon_days")
    renewal = Renewal(
        horizon_days=(tuple(int(x) for x in horizon) if horizon is not None
                      else Renewal.horizon_days),
        request_cadence_days=_int(
            "RENEWAL_REQUEST_CADENCE_DAYS",
            _dig(t, "renewal", "request_cadence_days"),
            Renewal.request_cadence_days,
        ),
        reminder_after_days=_int(
            "RENEWAL_REMINDER_AFTER_DAYS",
            _dig(t, "renewal", "reminder_after_days"),
            Renewal.reminder_after_days,
        ),
        escalate_after_reminders=_int(
            "RENEWAL_ESCALATE_AFTER_REMINDERS",
            _dig(t, "renewal", "escalate_after_reminders"),
            Renewal.escalate_after_reminders,
        ),
    )

    # email.send_policy: TOML overlay or default; no env var. The inbound IMAP
    # mailbox is env-driven; credentials live in Connection (secrets).
    email = Email(
        send_policy=_str(None, _dig(t, "email", "send_policy"), "draft"),
        imap_host=_str("IMAP_HOST", _dig(t, "email", "imap_host"), ""),
        imap_port=_int("IMAP_PORT", _dig(t, "email", "imap_port"), 993),
        imap_ssl=_bool("IMAP_SSL", _dig(t, "email", "imap_ssl"), True),
        imap_folder=_str("IMAP_FOLDER", _dig(t, "email", "imap_folder"), "INBOX"),
        poll_since=_str("EMAIL_POLL_SINCE", _dig(t, "email", "poll_since"), ""),
        poll_max_messages=_int(
            "EMAIL_POLL_MAX_MESSAGES", _dig(t, "email", "poll_max_messages"), 200
        ),
        zip_expand=_bool("EMAIL_ZIP_EXPAND", _dig(t, "email", "zip_expand"), Email.zip_expand),
        zip_max_members=_int(
            "EMAIL_ZIP_MAX_MEMBERS", _dig(t, "email", "zip_max_members"), Email.zip_max_members
        ),
        zip_max_member_mb=_int(
            "EMAIL_ZIP_MAX_MEMBER_MB", _dig(t, "email", "zip_max_member_mb"),
            Email.zip_max_member_mb,
        ),
        zip_max_total_mb=_int(
            "EMAIL_ZIP_MAX_TOTAL_MB", _dig(t, "email", "zip_max_total_mb"),
            Email.zip_max_total_mb,
        ),
        zip_max_ratio=_int(
            "EMAIL_ZIP_MAX_RATIO", _dig(t, "email", "zip_max_ratio"), Email.zip_max_ratio
        ),
        summary_enabled=_bool(
            "EMAIL_SUMMARY_ENABLED", _dig(t, "email", "summary_enabled"),
            Email.summary_enabled,
        ),
        summary_min_chars=_int(
            "EMAIL_SUMMARY_MIN_CHARS", _dig(t, "email", "summary_min_chars"),
            Email.summary_min_chars,
        ),
        summary_max_chars=_int(
            "EMAIL_SUMMARY_MAX_CHARS", _dig(t, "email", "summary_max_chars"),
            Email.summary_max_chars,
        ),
        summary_max_tokens=_int(
            "EMAIL_SUMMARY_MAX_TOKENS", _dig(t, "email", "summary_max_tokens"),
            Email.summary_max_tokens,
        ),
    )

    per_mfr = _dig(t, "mfr_binding", "per_manufacturer") or {}
    mfr_binding = MfrBinding(
        auto_link=_bool(None, _dig(t, "mfr_binding", "auto_link"), True),
        per_manufacturer={str(k): bool(v) for k, v in per_mfr.items()},
    )

    mfr_ref_src = _dig(t, "ingest", "mfr_ref_source_by_code") or {}
    ingest = Ingest(
        process_md_unknown=_bool(
            "INGEST_PROCESS_MD_UNKNOWN",
            _dig(t, "ingest", "process_md_unknown"),
            True,
        ),
        mfr_ref_source_by_code={str(k): str(v) for k, v in mfr_ref_src.items()},
    )

    validate = Validate(
        min_unscoped_ref_len=_int(
            "VALIDATE_MIN_UNSCOPED_REF_LEN",
            _dig(t, "validate", "min_unscoped_ref_len"),
            6,
        ),
    )

    queue = Queue(
        visibility_timeout_s=_int(
            "QUEUE_VISIBILITY_TIMEOUT_S",
            _dig(t, "queue", "visibility_timeout_s"),
            # Keep in step with `Queue.visibility_timeout_s`. THIS literal is
            # the effective default -- the dataclass field default never runs,
            # because every field is passed explicitly here. Raising only the
            # dataclass on 2026-09-02 left the worker reclaiming at 300s while
            # the source said 1800, which is the drift this file's reference
            # doc opens by warning about.
            1800,
        ),
        backoff_base_s=_int(
            "QUEUE_BACKOFF_BASE_S", _dig(t, "queue", "backoff_base_s"), 5
        ),
        backoff_cap_s=_int(
            "QUEUE_BACKOFF_CAP_S", _dig(t, "queue", "backoff_cap_s"), 3600
        ),
        max_attempts=_int(
            "QUEUE_MAX_ATTEMPTS", _dig(t, "queue", "max_attempts"), 5
        ),
    )

    alerts = Alerts(
        webhook_url=_str(
            "ALERTS_WEBHOOK_URL", _dig(t, "alerts", "webhook_url"), ""
        ),
    )

    bc = Bc(
        write_enabled=_bool(
            "BC_WRITE_ENABLED", _dig(t, "bc", "write_enabled"), False
        ),
        base_url=_str("BC_BASE_URL", _dig(t, "bc", "base_url"), ""),
        username=_str("BC_USERNAME", _dig(t, "bc", "username"), ""),
        password=_str("BC_PASSWORD", _dig(t, "bc", "password"), ""),
        drift_cap=_int("BC_DRIFT_CAP", _dig(t, "bc", "drift_cap"), 1000),
    )

    connection = Connection(
        database_url=_str(
            "DATABASE_URL",
            _dig(t, "connection", "database_url"),
            "postgresql://dentalia:dentalia@localhost:5432/dentalia",
        ),
        anthropic_api_key=_str(
            "ANTHROPIC_API_KEY", _dig(t, "connection", "anthropic_api_key"), ""
        ),
        brave_api_key=_str(
            "BRAVE_API_KEY", _dig(t, "connection", "brave_api_key"), ""
        ),
        serper_api_key=_str(
            "SERPER_API_KEY", _dig(t, "connection", "serper_api_key"), ""
        ),
        imap_user=_str("IMAP_USER", _dig(t, "connection", "imap_user"), ""),
        imap_password=_str(
            "IMAP_PASSWORD", _dig(t, "connection", "imap_password"), ""
        ),
    )

    web = Web(
        host=_str("WEB_HOST", _dig(t, "web", "host"), "127.0.0.1"),
        port=_int("WEB_PORT", _dig(t, "web", "port"), 8000),
        imports_dir=_str("WEB_IMPORTS_DIR", _dig(t, "web", "imports_dir"), "/imports"),
        upload_max_mb=_int("UPLOAD_MAX_MB", _dig(t, "web", "upload_max_mb"), 25),
        api_database_url=_str(
            "API_DATABASE_URL",
            _dig(t, "web", "api_database_url"),
            "postgresql://dentalia_api:dentalia_api@localhost:5432/dentalia",
        ),
        require_authenticated_user=_bool(
            "WEB_REQUIRE_AUTHENTICATED_USER",
            _dig(t, "web", "require_authenticated_user"),
            False,
        ),
        trusted_user_header=_str(
            "WEB_TRUSTED_USER_HEADER",
            _dig(t, "web", "trusted_user_header"),
            "X-Forwarded-User",
        ),
        archive_root=_str(
            "WEB_ARCHIVE_ROOT", _dig(t, "web", "archive_root"), "./archive"
        ),
        playbooks_dir=_str("WEB_PLAYBOOKS_DIR", _dig(t, "web", "playbooks_dir"), ""),
        reports_dir=_str("WEB_REPORTS_DIR", _dig(t, "web", "reports_dir"), ""),
        path_rewrites=_str(
            "WEB_PATH_REWRITES", _dig(t, "web", "path_rewrites"), ""
        ),
        api_keys=_str("WEB_API_KEYS", _dig(t, "web", "api_keys"), ""),
        bc_link_key=_str("WEB_BC_LINK_KEY", _dig(t, "web", "bc_link_key"), ""),
        public_base_url=_str(
            "WEB_PUBLIC_BASE_URL", _dig(t, "web", "public_base_url"), ""
        ),
        operator_users=_str_list(
            "WEB_OPERATOR_USERS", _dig(t, "web", "operator_users"), Web.operator_users
        ),
    )

    scheduler = Scheduler(
        tick_interval_s=_int(
            "SCHEDULER_TICK_INTERVAL_S", _dig(t, "scheduler", "tick_interval_s"), 300
        ),
        ingest_watch_dir=_str(
            "SCHEDULER_INGEST_WATCH_DIR", _dig(t, "scheduler", "ingest_watch_dir"), ""
        ),
        ingest_glob=_str(
            "SCHEDULER_INGEST_GLOB", _dig(t, "scheduler", "ingest_glob"), "*.xlsx"
        ),
        ingest_catalogue=_str(
            "SCHEDULER_INGEST_CATALOGUE", _dig(t, "scheduler", "ingest_catalogue"), "LJ"
        ),
        expiry_scan_interval_hours=_int(
            "SCHEDULER_EXPIRY_SCAN_INTERVAL_HOURS",
            _dig(t, "scheduler", "expiry_scan_interval_hours"),
            24,
        ),
        expiry_email_enabled=_bool(
            "SCHEDULER_EXPIRY_EMAIL_ENABLED",
            _dig(t, "scheduler", "expiry_email_enabled"),
            False,
        ),
        report_dir=_str(
            "SCHEDULER_REPORT_DIR", _dig(t, "scheduler", "report_dir"), ""),
        expiry_rediscover_enabled=_bool(
            "SCHEDULER_EXPIRY_REDISCOVER_ENABLED",
            _dig(t, "scheduler", "expiry_rediscover_enabled"),
            False,
        ),
        expiry_rediscover_cap=_int(
            "SCHEDULER_EXPIRY_REDISCOVER_CAP",
            _dig(t, "scheduler", "expiry_rediscover_cap"),
            25,
        ),
        coverage_scan_enabled=_bool(
            "SCHEDULER_COVERAGE_SCAN_ENABLED",
            _dig(t, "scheduler", "coverage_scan_enabled"),
            False,
        ),
        coverage_scan_cap=_int(
            "SCHEDULER_COVERAGE_SCAN_CAP",
            _dig(t, "scheduler", "coverage_scan_cap"),
            25,
        ),
        failure_monitor_interval_hours=_int(
            "SCHEDULER_FAILURE_MONITOR_INTERVAL_HOURS",
            _dig(t, "scheduler", "failure_monitor_interval_hours"),
            24,
        ),
        failure_window_days=_int(
            "SCHEDULER_FAILURE_WINDOW_DAYS", _dig(t, "scheduler", "failure_window_days"), 30
        ),
        failure_miss_rate_threshold=_float(
            "SCHEDULER_FAILURE_MISS_RATE_THRESHOLD",
            _dig(t, "scheduler", "failure_miss_rate_threshold"),
            0.5,
        ),
        failure_min_sample=_int(
            "SCHEDULER_FAILURE_MIN_SAMPLE", _dig(t, "scheduler", "failure_min_sample"), 5
        ),
        failure_reonboard_enabled=_bool(
            "SCHEDULER_FAILURE_REONBOARD_ENABLED",
            _dig(t, "scheduler", "failure_reonboard_enabled"),
            False,
        ),
        email_poll_enabled=_bool(
            "SCHEDULER_EMAIL_POLL_ENABLED",
            _dig(t, "scheduler", "email_poll_enabled"),
            False,
        ),
        bc_push_drift_enabled=_bool(
            "SCHEDULER_BC_PUSH_DRIFT_ENABLED",
            _dig(t, "scheduler", "bc_push_drift_enabled"),
            False,
        ),
        email_poll_interval_hours=_int(
            "SCHEDULER_EMAIL_POLL_INTERVAL_HOURS",
            _dig(t, "scheduler", "email_poll_interval_hours"),
            6,
        ),
        eudamed_certregister_enabled=_bool(
            "SCHEDULER_EUDAMED_CERTREGISTER_ENABLED",
            _dig(t, "scheduler", "eudamed_certregister_enabled"),
            False,
        ),
        eudamed_certregister_interval_days=_int(
            "SCHEDULER_EUDAMED_CERTREGISTER_INTERVAL_DAYS",
            _dig(t, "scheduler", "eudamed_certregister_interval_days"),
            30,
        ),
        eudamed_sweep_interval_days=_int(
            "SCHEDULER_EUDAMED_SWEEP_INTERVAL_DAYS",
            _dig(t, "scheduler", "eudamed_sweep_interval_days"),
            90,
        ),
    )

    # Per-manufacturer source-priority list — data-driven, TOML-only for now.
    raw_sp = _dig(t, "source_priority") or {}
    source_priority = {
        str(mfr): [str(s) for s in sources] for mfr, sources in raw_sp.items()
    }

    return Config(
        gate=gate,
        resolve=resolve,
        discovery=discovery,
        adapters=adapters,
        storage=storage,
        models=models,
        batch=batch,
        fetch=fetch,
        budget=budget,
        renewal=renewal,
        email=email,
        mfr_binding=mfr_binding,
        ingest=ingest,
        validate=validate,
        queue=queue,
        alerts=alerts,
        bc=bc,
        connection=connection,
        web=web,
        scheduler=scheduler,
        source_priority=source_priority,
    )


if __name__ == "__main__":  # pragma: no cover - manual inspection helper
    import pprint

    pprint.pprint(load_config())
