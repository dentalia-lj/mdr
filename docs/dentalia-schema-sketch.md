# Dentalia Pipeline — Schema Sketch (Postgres, with queue tags)

v2 — 2026-07-05 · Implements `dentalia-pipeline-contract-prd-v3.md` §0 + §9. Supersedes v1.
Sketch, not migration — types indicative, indexes minimal, constraints show intent.
Each table is annotated with the **queue tags** (job types) that write it / read it.

**Changelog v1 → v2 (sync to PRD v3):** `item_mirror.mfr_ref` + `item_group_member.mfr_ref` (C1) · `job.dedupe_key` uniqueness via partial index on active statuses (C2) · `item_document.status` link-level gating + `mfr-scope` basis (C4/C5) · supersession identity constraint note (C6) · `batch_ref` side table (C9) · `audit_log.job_snapshot`, `via_job` soft ref (C8) · `validate` dedupe_key gains group_id suffix (C3) · production-visibility view.

**Sync 2026-08-10 (migrations 016–020):** `vendor_master` BC manufacturer master mirror (016, CLI-written, no queue tag) · `manufacturer_alias.source` provenance (017) · `document_text` parsed text per content hash (018, §4) · `job.result` envelope + `data_anomaly` closed-vocabulary ledger (019, §1) · `document.cert_doc_id` so a DoC inherits its renewal date from the certificate it cites (020, MDR Annex IV / Article 56).

**Sync 2026-07-24 (drift-check; migrations 010–013):** `report.weekly` tag (013) · `extraction_cost` + `extraction_spend` view (010, §4) · `item_mirror.mirror_rev` sequence default (011) · `scheduler_run` restart-safety ledger (013, §5c) · dedupe-key extensions beyond the PRD table (§1 note) · C5 CHECK is live in 005, no longer just intent.

**Sync 2026-09-15 (migration 069):** `email_draft.kind` gains `review-confirm` — a fifth letter, asking whether a declaration past Dentalia's own five-year review horizon (`document_effective_expiry.basis='staleness'`) is still current. Never a renewal demand: these documents have not expired, and a class I declaration has no certificate, so this horizon is the only trigger it will ever produce. Vocabulary only; nothing sends.

**Sync 2026-09-15 (migration 068):** two immutable SQL functions, `fold_bur_code` and `eudamed_ref_key`, and `eudamed_article_status` joins through them for a manufacturer whose playbook body declares `ref_normalize.strategy = 'reorder-shank-figure-size'` (KOMET alone). `fold_bur_code` mirrors `app/handlers/validate.py::_fold_bur_code`; `eudamed_ref_key` additionally drops EUDAMED's trailing variant segment (`8885.314.012.K3`), which our catalogue never prints. Measured the same day: KOMET goes from 0 to 108 of its 258 flagged device items matched, with zero trimmed keys spanning more than one Basic UDI-DI. Every other manufacturer keeps the exact comparison. The two implementations are pinned together by `tests/test_eudamed_ref_fold.py`.

**Sync 2026-09-15 (migration 067):** `email_draft.kind` gains `cert-request` — a fourth letter, asking for a certificate EUDAMED's public register records the manufacturer holds and we do not (`certificate_gap`, migration 043). Vocabulary only: no column, no table, and nothing sends. It is the only ask this system can make that names the DOCUMENT rather than the articles.

**Sync 2026-09-09 (migration 066):** `manufacturer_srn.discovered_via` gains `text-mined` and the table gains `source_doc_id` — a fourth discovery path, `dentalia mine-srn`, reading SRNs off documents already in the archive. It exists because the other three all need EUDAMED to know the manufacturer already: the register pull sees only actors holding certificates, and the article probe is unreachable through any UI or scheduler path.

**Sync 2026-08-27 (EUDAMED S2.3 stage 2, migrations 041–046):** two new job-type enum values (§0) · `manufacturer.srn_probed_at`, `manufacturer_srn`, `eudamed_certificate`, `eudamed_sweep_state` (new §6b) · `eudamed_mirror` gains `reference`/`trade_name`/`first_seen`/`device_status_type` (§3) · eleven views (§6b) · tag→table access matrix gains `eudamed.certregister`/`eudamed.sweep` and `dentalia_api`'s first write grant beyond `job` (§7) — still zero write grant on `document`/`item_document`/`evidence`.

---

## 0. Queue tag registry (closed set — PRD §10 drift guard)

```sql
CREATE TYPE job_type AS ENUM (
  -- pipeline spine
  'ingest.run',        -- INGEST
  'resolve.group',     -- RESOLVE
  'discover.group',    -- DISCOVER
  'fetch.url',         -- FETCH
  'extract.doc',       -- EXTRACT
  'validate.doc',      -- VALIDATE
  'gate.candidate',    -- GATE (machine)
  'gate.apply',        -- GATE (human decision from review UI)
  -- peripheral producers
  'backfill.scan',     -- Drive corpus walker  → emits extract.doc
  'email.poll',        -- inbound mailbox      → emits extract.doc
  'email.request',     -- outbound renewal     (Phase 2)
  'email.reminder',    --                      (Phase 2)
  'eudamed.sync',      -- mirror refresh cron
  'eudamed.certregister', -- whole EU certificate register pull + local actor attribution (migration 041)
  'eudamed.sweep',     -- per-manufacturer device catalogue walk, human-released only (migration 041)
  'playbook.reonboard',-- failure-monitor trigger (Phase 2)
  'report.weekly',     -- SCHEDULER weekly report (S1.5, added by migration 013)
  'scheduler.tick',    -- the cron cadence itself (migration 054): one perpetual
                       -- self-deferring job per cron, seeded by 055
  'upload.ingest',     -- web /upload hand-off (migration 014, manual precursor to email.poll)
  'vendor.import',     -- web /import vendor section (migration 041, BC vendor upload slice B):
                       -- Proizvajalci.xlsx -> vendor_master. Emits nothing.
  'bc.push'            -- write three compliance fields back into Business Central
                       -- (migration 062). Not a pipeline stage: consumes no stage's
                       -- output, emits nothing, writes only bc_push_log.
);

CREATE TYPE job_status   AS ENUM ('pending','running','done','failed','dead');
CREATE TYPE job_priority AS ENUM ('interactive','delta','sweep');  -- ordinal 0,1,2
```

## 1. Queue

```sql
CREATE TABLE job (
  id           bigserial PRIMARY KEY,
  type         job_type      NOT NULL,
  payload      jsonb         NOT NULL,   -- IMMUTABLE after enqueue (C9)
  dedupe_key   text          NOT NULL,   -- NOT globally unique — see partial index below
  status       job_status    NOT NULL DEFAULT 'pending',
  priority     job_priority  NOT NULL DEFAULT 'sweep',
  run_after    timestamptz   NOT NULL DEFAULT now(),
  attempts     int           NOT NULL DEFAULT 0,
  max_attempts int           NOT NULL DEFAULT 5,
  last_error   text,
  claimed_by   text,
  claimed_at   timestamptz,
  finished_at  timestamptz,  -- 026: stamped on TERMINAL transitions only
                              -- (queue.finish, and the dead branch of fail).
                              -- A retry is not a finish and defer is not
                              -- terminal. Nullable, no backfill: every job
                              -- already in the table finished at a time this
                              -- column cannot recover.
  created_at   timestamptz   NOT NULL DEFAULT now(),
  result       jsonb,        -- 019: the handler's Result envelope (counts/samples/
                              -- notes/anomalies), written by queue.finish in the
                              -- same transaction as the status flip
  caused_by    bigint        -- 035: soft ref, no FK; NULL = root (CLI/scheduler
                              -- tick/web producer); set implicitly by the runner
                              -- around dispatch (queue.current_job_id), never a
                              -- handler-supplied parameter
);

-- C2: dedupe scoped to ACTIVE jobs only. done/dead never block re-enqueue —
-- monthly re-checks and post-model-swap re-extraction are normal operations.
CREATE UNIQUE INDEX job_dedupe_active_uq ON job (dedupe_key)
  WHERE status IN ('pending','running','failed');

CREATE INDEX job_claim_idx ON job (type, priority, run_after)
  WHERE status = 'pending';
-- claim: SELECT ... FOR UPDATE SKIP LOCKED

-- 037: which job's handler enqueued this one (job_detail page: parent + direct children)
CREATE INDEX job_caused_by_idx ON job (caused_by) WHERE caused_by IS NOT NULL;

-- per-domain politeness (FETCH, and DISCOVER's crawl recipe)
CREATE TABLE domain_lease (
  domain            text PRIMARY KEY,
  leased_until      timestamptz,
  politeness_ms     int NOT NULL DEFAULT 2000,
  needs_playwright  boolean NOT NULL DEFAULT false,
  -- robots.txt Crawl-delay FLOOR (migration 036). The lease holds
  -- GREATEST(caller's politeness_ms, min_politeness_ms). Separate column
  -- because `politeness_ms` is rewritten from the caller on every acquire and
  -- FETCH always passes the global config value, so a per-host value stored
  -- there survives exactly one fetch. Written ONLY by the robots reader.
  min_politeness_ms int NOT NULL DEFAULT 0
);

-- robots.txt cache (migration 036): one fetch per host per TTL window, not one
-- per crawl. Body kept verbatim -- re-parsing a cached body with stdlib
-- `urllib.robotparser` is free, while a stored parse would need a migration
-- every time our interpretation changes. crawl_delay_ms is projected onto
-- domain_lease.min_politeness_ms above.
-- 061: judged crawl links, once per library version (ruled 2026-09-04). Keyed on
-- a hash of the harvested links + texts; the per-group order is computed at read
-- time. Worker-only, no grant, like robots_cache.
CREATE TABLE crawl_link_rank (
  index_url text NOT NULL, harvest_hash text NOT NULL, model_id text,
  judged_at timestamptz NOT NULL DEFAULT now(), judgements jsonb NOT NULL,
  PRIMARY KEY (index_url, harvest_hash)
);

CREATE TABLE robots_cache (
  host           text PRIMARY KEY,
  body           text,                    -- NULL = the robots.txt fetch failed
  status         int,
  crawl_delay_ms int,                     -- parsed Crawl-delay for our UA
  fetched_at     timestamptz NOT NULL DEFAULT now()
);

-- C9: Batch API tracking — payloads stay immutable; crash between submit and
-- record resolves via lookup here (existing row for hash+tier => never resubmit)
CREATE TABLE batch_ref (
  content_hash text NOT NULL,
  tier         text NOT NULL,             -- T1 | T2
  batch_id     text NOT NULL,
  submitted_at timestamptz NOT NULL DEFAULT now(),
  resolved_at  timestamptz,
  PRIMARY KEY (content_hash, tier)
);
```

**dedupe_key conventions** (per PRD v3):

| tag | dedupe_key |
|---|---|
| resolve.group | `resolve:{item_ref}:{mirror_rev}` |
| discover.group | `discover:{group_id}:{cycle}` |
| fetch.url | `fetch:{url_normalized}` |
| extract.doc | `extract:{content_hash}` |
| validate.doc | `validate:{content_hash}:{extract_rev}:{group_id}` *(group_id suffix — C3: same extraction validated against multiple requesting groups)* |
| report.weekly | `report:{period_key}` *(ISO year-week — S1.5, PRD §0)* |
| scheduler.tick | `scheduler.tick:{cron}` *(one row per cron; never terminal, so the key is held for the job's whole life — 2026-08-31)* |

The full producer-by-producer key inventory was ratified into the PRD §0 conventions table on 2026-07-31 (closing `[dedupe-conventions]` except the ingest-prefix unification, which still needs Denis's sign-off) — the PRD table is now the single normative list; DISCOVER's email key gained the `group:` namespace (`email.request:group:{group_id}`) to be symmetric with SCHEDULER's `email.request:doc:{doc_id}`.

## 2. Ingest side

```sql
-- written by: ingest.run · read by: resolve.group
CREATE TABLE item_mirror (
  item_ref         text PRIMARY KEY,        -- BC item ID
  name             text NOT NULL,
  manufacturer_raw text NOT NULL,
  mfr_ref          text,                    -- C1: manufacturer article number — the REF-gate key.
                                            -- LOGICAL field: adapter maps it from whichever BC
                                            -- column holds it (item No. | vendor item no. | mixed).
                                            -- Nullable; null counted in missing_mfr_ref metric,
                                            -- never silently skipped.
  md_flag          boolean,                 -- assumption: present in BC
  product_class    text,                    -- I / IIa / IIb / III — BC = source of truth
  udi              text,                    -- UDI-DI when present
  catalogue        text NOT NULL,           -- 'LJ' | 'ZG'
  mirror_rev       bigint NOT NULL,         -- bumps on change → triggers resolve.group
                                            -- (011: DEFAULT nextval('item_mirror_rev_seq'))
  updated_at       timestamptz NOT NULL
);

-- written by: resolve.group (self-seed, DEFAULT source) · `dentalia playbooks
-- sync` CLI (source='vendor-master'|'playbook', migration 017)
CREATE TABLE manufacturer_alias (
  raw_name       text PRIMARY KEY,
  canonical_name text NOT NULL,
  source         text NOT NULL DEFAULT 'self-seed'  -- (017): self-seed (RESOLVE
                                            -- miss) | vendor-master (playbooks
                                            -- sync, from vendor_master) |
                                            -- playbook (authored claim, wins
                                            -- over the master); CHECK
                                            -- constraint on the 3 values
);

-- written by: `dentalia vendor-master --apply` (CLI, not a queue tag) ·
-- read by: `dentalia playbooks reconcile` / `dentalia playbooks sync` (CLI,
-- via reconcile.load_vendor_master) — no queue tag touches this table.
-- MIRROR, not truth-by-authorship (same posture as item_mirror): BC's
-- manufacturer master (016, `imports/Proizvajalci.xlsx`, Šifra -> Ime).
-- manufacturer_alias stays the runtime projection RESOLVE/GATE use; sync
-- derives that projection from this table plus authored playbooks.
CREATE TABLE vendor_master (
  code_source  text NOT NULL DEFAULT 'LJ',  -- which BC instance issued the
                                            -- code, NOT the warehouse tag.
                                            -- Column kept, parameter removed
                                            -- 2026-08-26: readers and writers
                                            -- use the default, nobody picks.
  code         text NOT NULL,               -- text, not numeric: at least one
                                            -- non-numeric Šifra (`CEFLA`)
  name         text,                        -- nullable: one of 390 delivered
                                            -- rows has no name
  import_batch text NOT NULL,               -- label of the import run that
                                            -- last wrote this row
  first_seen   timestamptz NOT NULL DEFAULT now(),
  last_seen    timestamptz NOT NULL DEFAULT now(),  -- import_batch timestamp
                                            -- of the most recent import that
                                            -- still listed this code (a
                                            -- disappeared vendor keeps its
                                            -- row with a stale last_seen
                                            -- rather than being deleted)
  PRIMARY KEY (code_source, code)
);
GRANT SELECT ON vendor_master TO dentalia_api;   -- migration 016

-- written by: resolve.group · read by: discover.group, validate.doc (REF gate)
CREATE TABLE item_group (
  group_id               bigserial PRIMARY KEY,
  canonical_manufacturer text NOT NULL,
  basic_udi_di           text,              -- when known — strongest group key
  label                  text
);

CREATE TABLE item_group_member (
  group_id    bigint REFERENCES item_group,
  item_ref    text   REFERENCES item_mirror,
  mfr_ref     text,                         -- C1: materialized here at RESOLVE time —
                                            -- group.member_mfr_refs is the REF-gate comparand
  match_basis text NOT NULL,                -- existing-link|udi|basic-udi-di|name-family|singleton|manual
                                            -- (`singleton` = founded own group; S1.2, ratified in handbook §2)
  PRIMARY KEY (group_id, item_ref)
);

-- C1: the REF-gate comparand, always scoped by manufacturer —
-- bare article numbers collide across manufacturers
CREATE VIEW item_group_refs AS
  SELECT g.group_id, g.canonical_manufacturer,
         array_agg(m.mfr_ref) FILTER (WHERE m.mfr_ref IS NOT NULL) AS member_mfr_refs
  FROM item_group g JOIN item_group_member m USING (group_id)
  GROUP BY g.group_id, g.canonical_manufacturer;

-- S1.2 (migration 012): the RESOLVE staging path (PRD §2). An ambiguous
-- name-family cluster (score in [name_suggest, name_accept)) is T1-adjudicated
-- and staged here for human review — grouping is never auto-applied on a name
-- match (invariant 3). Same grant posture as manual_task: API renders (SELECT),
-- the owner role (handlers) writes; resolution enqueues resolve.group with an
-- additive forced group_id, never an API bypass.
-- written by: resolve.group · read by: review UI (staging tab, S1.6)
CREATE TABLE grouping_suggestion (
  id          bigserial PRIMARY KEY,
  item_ref    text NOT NULL REFERENCES item_mirror,
  candidates  jsonb NOT NULL,               -- [{group_id, score, sample_name}]
  suggestion  jsonb,                         -- T1 output; {t1_error} if adjudication failed
  score       real,
  status      text NOT NULL DEFAULT 'open',  -- open | resolved
  created_at  timestamptz NOT NULL DEFAULT now(),
  resolved_by text, resolved_at timestamptz
);
-- at most one open suggestion per item (idempotent under at-least-once delivery)
CREATE UNIQUE INDEX grouping_suggestion_open_item_idx
  ON grouping_suggestion (item_ref) WHERE status = 'open';

-- S1.2 (migration 012): pg_trgm powers name-family candidate generation in-DB.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX item_mirror_name_trgm ON item_mirror USING gin (name gin_trgm_ops);
```

## 3. Discovery & fetch

```sql
-- written by: discover.group · feeds failure monitor + hit-rate stats
CREATE TABLE discovery_log (
  id       bigserial PRIMARY KEY,
  group_id bigint REFERENCES item_group,
  source   text NOT NULL,     -- known-url|playbook|eudamed|search|vendor|email|manual
  outcome  text NOT NULL,     -- hit|miss|skipped
  detail   jsonb,
  at       timestamptz NOT NULL DEFAULT now()
);

-- written by: fetch.url, backfill.scan, email.poll, upload.ingest · read by: fetch.url (skip), discover.group (recency)
CREATE TABLE fetch_log (
  url_normalized  text PRIMARY KEY,
  etag            text,
  last_modified   text,
  content_hash    text,                     -- sha256
  fetched_at      timestamptz,
  last_checked_at timestamptz,
  source          text NOT NULL,            -- live|backfill|email|upload
  doc_id          bigint                    -- FK → document, nullable
);
CREATE INDEX fetch_log_hash_idx ON fetch_log (content_hash);

-- S2.4 inbound processed-email ledger (migration 028). The durable, idempotent
-- reprocess guard for email.poll — NOT the IMAP \Seen flag. Keyed by mailbox
-- identity + UIDVALIDITY + UID; a recorded message is never polled again. Per
-- SOURCE EMAIL, distinct from fetch_log's per-PHYSICAL-DOCUMENT content dedupe.
-- `references_hdr` / `in_reply_to` / `renewal_request_id` are captured for the
-- outbound slice's reply matcher (spec §9), unused inbound.
-- written by: email.poll · read by: web /emails (dentalia_api SELECT)
CREATE TABLE email_poll_log (
  id                 bigserial PRIMARY KEY,
  mailbox            text NOT NULL,            -- "{host}/{user}/{folder}"
  uid_validity       text NOT NULL,            -- IMAP UIDVALIDITY epoch
  imap_uid           text NOT NULL,            -- IMAP UID (within uid_validity)
  message_id         text,                     -- RFC822 Message-ID
  from_addr          text,
  subject            text,
  received_at        timestamptz,
  in_reply_to        text,                     -- outbound reply-match hook
  references_hdr     text,                     -- outbound reply-match hook
  had_attachments    boolean NOT NULL DEFAULT false,
  attachments        jsonb   NOT NULL DEFAULT '[]',  -- per-attachment verdict + hash + disposition
  emitted_jobs       jsonb   NOT NULL DEFAULT '[]',
  renewal_request_id bigint,                   -- soft ref, outbound reply matcher
  processed_at       timestamptz NOT NULL DEFAULT now(),
  -- 030: what the message ASKED FOR, never what it said. The body is read into
  -- memory at poll time, summarised by the cheap tier and dropped -- supplier
  -- correspondence is not a document and is not kept for ten years (inv 12).
  -- summary_status is never null, so a blank summary always carries its reason:
  -- ok | disabled | no-body | too-short | llm-error. Tokens + USD are kept so
  -- "negligible" stays measurable. Not indexed, never joined, never re-read.
  body_summary          text,
  summary_intent        text,       -- sends-documents|requests-info|acknowledges|other (no CHECK: drift must be visible)
  summary_status        text,
  summary_model         text,
  summary_input_tokens  int,
  summary_output_tokens int,
  summary_cost_usd      numeric(12,6)
);
CREATE UNIQUE INDEX email_poll_log_uid_key ON email_poll_log (mailbox, uid_validity, imap_uid);
CREATE INDEX email_poll_log_message_id_idx ON email_poll_log (message_id);

-- written by: eudamed.sync, eudamed.sweep (migration 041/044) · read by: discover.group (free
-- deterministic lookup, still misses because cert_refs stays empty -- EUDAMED
-- publishes no document URLs, measured 2026-08-20), the declaration-gap views (§6b)
CREATE TABLE eudamed_mirror (
  udi_di           text PRIMARY KEY,
  basic_udi_di     text,
  device_name      text,           -- null on 121/121 records measured 2026-08-25; trade_name is the populated field
  trade_name       text,           -- migration 039: EUDAMED's actually-populated name field. Display only, never a link basis
  manufacturer_srn text,
  reference        text,           -- migration 038: MDR Annex VI Part B catalogue number. Not a link basis by itself --
                                    -- ref-eudamed is capped at staged and no handler writes item_document from it
  device_status_type text,         -- migration 044: read for transitions (eudamed_sweep_delta), acted on nowhere
  cert_refs        jsonb,
  first_seen       timestamptz NOT NULL DEFAULT now(),  -- migration 044: NEVER touched by ON CONFLICT -- the sweep delta's basis
  synced_at        timestamptz NOT NULL
);
```

## 4. Extraction

```sql
-- written by: extract.doc (all tier attempts kept — T3 humans see the full ladder)
CREATE TABLE extraction_attempt (
  id            bigserial PRIMARY KEY,
  content_hash  text NOT NULL,
  tier          text NOT NULL,               -- T0|T1|T2|T3
  model_id      text,
  fields        jsonb NOT NULL,              -- extracted values + per-field confidence + evidence
  extract_rev   int NOT NULL DEFAULT 1,
  playbook_slug text,                        -- 031: which playbook (if any) steered this read —
  playbook_rev  int,                         -- evidence must name the rules that shaped it, and
                                              -- extract_rev (a per-hash attempt counter) doesn't;
                                              -- NULL playbook_slug = no playbook claimed the doc
  at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX extraction_hash_idx ON extraction_attempt (content_hash);

-- written by: extract.doc (010 — one row per LLM tier CALL, measured tokens + $).
-- NOT an FK to extraction_attempt: the batch path commits a tier's cost row as
-- that tier resolves, BEFORE the attempt row exists (crash-safety, same model as
-- batch_ref). UNIQUE triple = idempotency guard for batch-retry re-fetches.
CREATE TABLE extraction_cost (
  id                    bigserial PRIMARY KEY,
  content_hash          text NOT NULL,
  tier                  text NOT NULL,        -- T1|T2 (T0 is free, never priced)
  extract_rev           int  NOT NULL DEFAULT 1,
  model_id              text NOT NULL,
  batch                 boolean NOT NULL DEFAULT false,
  input_tokens          int NOT NULL DEFAULT 0,
  output_tokens         int NOT NULL DEFAULT 0,
  cache_read_tokens     int NOT NULL DEFAULT 0,
  cache_creation_tokens int NOT NULL DEFAULT 0,
  cost_usd              numeric(12,6),        -- NULL = model unpriced (tokens still kept, never a silent 0)
  at                    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (content_hash, tier, extract_rev)
);
CREATE INDEX extraction_cost_hash_idx ON extraction_cost (content_hash, extract_rev);

-- KPI K8 / findings read this rollup, never the raw table (SELECT to dentalia_api):
-- spend by model x tier x transport + unpriced_calls (pricing gaps stay visible)
CREATE VIEW extraction_spend AS
SELECT model_id, tier, batch, count(*) AS calls,
       sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens,
       sum(cost_usd) AS cost_usd,
       count(*) FILTER (WHERE cost_usd IS NULL) AS unpriced_calls
FROM extraction_cost GROUP BY model_id, tier, batch;

-- written by: extract.doc (018 -- parsed text per content hash, written before
-- the tier ladder runs, keyed on content_hash rather than doc_id because the
-- text exists before GATE creates a document row). One row per content hash;
-- re-extraction overwrites (bytes are immutable, so this is idempotent).
-- `source='pdf-text'` is a deterministic PyMuPDF read usable to verify
-- evidence.verbatim; `source='none'` is a scan with no text layer -- the row
-- is still written so unrecoverable documents are counted, not guessed at.
CREATE TABLE document_text (
  content_hash text PRIMARY KEY,
  source       text NOT NULL CHECK (source IN ('pdf-text', 'none')),
  engine       text,
  pages        int  NOT NULL DEFAULT 0,
  chars        int  NOT NULL DEFAULT 0,
  content      text NOT NULL DEFAULT '',
  built_at     timestamptz NOT NULL DEFAULT now()
);

-- written by: any handler (019 -- not gate-only, Invariant 1 only covers
-- document/item_document/evidence). Standing ledger of catalogue weirdness;
-- append-only in practice -- a repeat observation bumps seen_count/last_seen
-- rather than inserting again, so the table stays the size of the problem,
-- not the size of the corpus. `kind` is a CLOSED vocabulary, ANOMALY_KINDS in
-- app/results.py -- kept in sync with this table by hand, like job_type.
CREATE TABLE data_anomaly (
  id         bigserial PRIMARY KEY,
  kind       text NOT NULL,
  subject    text NOT NULL,          -- item_ref, manufacturer code, content_hash
  catalogue  text,
  detail     jsonb,
  seen_count int  NOT NULL DEFAULT 1,
  first_seen timestamptz NOT NULL DEFAULT now(),
  last_seen  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX data_anomaly_key ON data_anomaly (kind, subject);
```

## 5. Registry (the deliverable) — **only `gate.candidate` / `gate.apply` write here**

```sql
-- 'filed' (025, C15): read correctly and covering no catalogue item. A
-- disposition, not a judgement -- see docs/vocabulary.md §3.
CREATE TYPE doc_status  AS ENUM ('staged','production','filed','rejected','superseded');
-- 'retracted' (024): a link the CURRENT extraction no longer supports. GATE
-- upserts item_document and never removed a link a later extraction stopped
-- justifying, so coverage could only grow and a correction could not be taken
-- back -- the wrong direction of error for a compliance registry.
CREATE TYPE link_status AS ENUM ('staged','production','rejected','retracted');   -- C5

CREATE TABLE document (
  doc_id            bigserial PRIMARY KEY,
  type              text NOT NULL,          -- DoC|EC|IFU|ISO|SPP|other
  regulation        text NOT NULL,          -- MDR|MDD|n.a.
  validity_from     date,
  validity_to       date,                   -- legitimately NULL (Class I, DoC w/o expiry,
                                             -- or inherited from cert_doc_id — see below)
  coverage_scope    text NOT NULL
    CHECK (coverage_scope IN ('group','manufacturer')),  -- closed, migration 022
    -- `item` retired 2026-08-12: no deterministic signal separates a
    -- single-article DoC from a multi-article one (the regulator permits
    -- either), and `len(ref_list) = 1` answers that question directly.
  basic_udi_di      text,
  cert_number       text,
  canonical_manufacturer text                  -- 053: the CONFIRMED manufacturer
    REFERENCES manufacturer(canonical_name)     -- (human bind or C16), NOT §201's
    ON UPDATE CASCADE,                          -- printed name in evidence. Null
                                                -- means UNDECIDED, never "none".
                                                -- Keyed by name like every other
                                                -- manufacturer attribute here;
                                                -- cascade per migration 052 (a
                                                -- rename renames the thing
                                                -- described). Before this, a
                                                -- document with no item links
                                                -- reached no manufacturer at all
                                                -- — 540 of them on 2026-08-31,
                                                -- 443 being every filed document.
  stated_class      text                    -- 032: the MDR risk class the document
    CHECK (stated_class IN ('I','Is','Im','Ir','IIa','IIb','III')),
                                             -- itself states. QA/display only: never
                                             -- scored, never gated, never written to
                                             -- item_mirror.product_class. BC is the
                                             -- source of truth for item class; this
                                             -- column exists to CROSS-CHECK it and to
                                             -- fill blanks a human decides on, via the
                                             -- item_class_check view below. Nullable
                                             -- by nature: most documents state nothing,
                                             -- and a multi-product declaration that
                                             -- prints the whole class ladder abstains.
  content_hash      text NOT NULL UNIQUE,   -- one registry row per physical document
  source_url        text,
  archive_url       text NOT NULL,          -- our stored copy — mandatory
  referenced_doc_id bigint REFERENCES document,  -- multilist cross-ref; also 'related' cross-regulation pairs (C6)
  cert_doc_id       bigint REFERENCES document,  -- 020: the notified-body certificate a DoC
                                                  -- cites (Annex IV item 8) — distinct from
                                                  -- referenced_doc_id (C6 cross-regulation
                                                  -- sibling). Annex IV requires only a date of
                                                  -- issue on a DoC; Article 56 caps a certificate
                                                  -- at five years, so this is where a DoC's real
                                                  -- renewal date comes from. Best-effort/null-safe:
                                                  -- resolved by VALIDATE, sticky-persisted and
                                                  -- back-resolved by GATE (order-independent).
  superseded_by     bigint REFERENCES document,  -- append-only chain
  supersedes        bigint REFERENCES document,  -- 015: forward pointer, sticky-staged by
                                                  -- gate.candidate until a human/machine
                                                  -- production write applies it (below)
  status            doc_status NOT NULL DEFAULT 'staged',
  created_at        timestamptz NOT NULL DEFAULT now()
);
-- C6: superseded_by may only reference a document with identical (type, regulation)
-- and overlapping coverage subject. MDR never supersedes MDD — parallel chains.
-- Enforced in the GATE handler (gate._assert_chain_identity, closing [gate-c6]
-- 2026-07-31) rather than a row CHECK — comparing two rows needs a query, and
-- the check must run at the moment a doc reaches production, not at insert
-- time (a `supersedes` value can sit on a STAGED doc for a whole review cycle
-- before a human approve or a later gate.candidate call applies it).
-- BOTH writers of the chain pointer call it: _apply_supersession (forward, the
-- candidate supersedes the current doc) and the task-5 auto-file path (inverse,
-- an older candidate files itself under the current doc). Both re-read the two
-- rows LIVE — a payload's target is resolved by VALIDATE and can be re-typed
-- while the job waits in the queue, since type/regulation are overwritten
-- unconditionally on upsert (only `status` is sticky) and are human-editable
-- through gate.apply.

CREATE TABLE item_document (
  item_ref    text   REFERENCES item_mirror,
  doc_id      bigint REFERENCES document,
  udi         text,
  match_basis text NOT NULL,        -- ref-list|ref-item|map-supplier|udi|basic-udi-di|name-family|fetch-context|ref-catalogue|mfr-scope|manual
  status      link_status NOT NULL DEFAULT 'staged',   -- C5: links gated independently
  PRIMARY KEY (item_ref, doc_id)
);
-- C5 invariant (drift guard): status='production' with
-- match_basis IN ('name-family','fetch-context','ref-catalogue') is a bug by definition.
-- 'manual' is the escape hatch out of those three and has exactly one writer:
-- gate.apply confirm-link (PRD C17). It re-bases one link to 'manual' + production
-- on a named human's say-so; reject-link takes the other branch, leaving the basis
-- intact so the trail records how the refused link was proposed; reopen-link puts a
-- rejected link back to staged. 'rejected' is sticky on upsert alongside 'production'
-- -- a later candidate must never resurrect a coverage claim a human refused, and
-- reopen-link is the ONLY way back out. 'retracted' is not sticky: it is a machine
-- state (the current extraction stopped claiming this link), so new evidence
-- reviving it is correct, and no human decision may target it.
-- LIVE since 005 as CONSTRAINT item_document_trusted_basis_ck, widened by 021:
--   CHECK (NOT (status='production'
--               AND match_basis IN ('name-family','fetch-context','ref-catalogue')))
-- `ref-catalogue` (021, 2026-08-11) is an unscoped REF match: the article number
-- was matched against the WHOLE catalogue because the document carried no
-- manufacturer, so the pair (canonical_manufacturer, mfr_ref) was inferred, not
-- verified. Only 24 of 5.511 catalogue REFs collide across manufacturers (0,44%,
-- half of them prose) — but that measures THIS catalogue, and a manufacturer
-- whose products Dentalia does not stock is invisible to it.
--
-- `ref-item` (023, 2026-08-13, client answer to C12) is the SCOPED counterpart
-- and is deliberately NOT in the CHECK: the extracted REF matched the member's
-- own `item_ref` instead of its `mfr_ref`, but the manufacturer was established
-- BEFORE the comparison, so the pair is verified exactly as `ref-list`'s is.
-- The number is only picking items within an already-known manufacturer. It is
-- a distinct basis rather than an alias so the row records WHICH column matched
-- — `ref-list` has to keep meaning "the supplier's own number matched".
-- Measured: `item_ref` collides across manufacturers 0 times (it is
-- `item_mirror`'s PK, so one value is one item is one manufacturer) against
-- `mfr_ref`'s 34, and 7.082 of 15.958 rows carry no `mfr_ref` at all.

-- Visibility rule for ALL consumers (read API, expiry scan, reports):
-- 034 appended expires/expiry_basis (document_effective_expiry, below) so the
-- view's own callers stop disagreeing with it over a document's real expiry;
-- validity_to keeps its raw (stated) meaning, additive-only.
CREATE OR REPLACE VIEW item_document_production AS
  SELECT id.*, d.type, d.regulation, d.validity_from, d.validity_to, d.archive_url,
         ee.expires       AS expires,
         ee.basis         AS expiry_basis
    FROM item_document id
    JOIN document d USING (doc_id)
    LEFT JOIN document_effective_expiry ee ON ee.doc_id = d.doc_id
   WHERE d.status = 'production' AND id.status = 'production';

-- The supersession chain, for the one consumer that asks for it by name
-- (`/api/items/{ref}/documents?include_superseded=true`, 2026-09-16). Same
-- shape as the view above plus `doc_status` and `superseded_by`; the DOCUMENT
-- filter widens to production+superseded and the LINK filter does not move at
-- all, so a retracted or staged link is unreachable through either view.
-- A SECOND view rather than a widening of the first: web/registry.py,
-- web/catalogue.py, the KPI board and 046_eudamed_gap_view all read
-- `item_document_production`, and relaxing its status filter would change what
-- all four mean without a call site being edited. Nothing else was needed to
-- make the rows reachable -- supersession flips `document.status` and sets
-- `superseded_by`, never touching `item_document` (gate.py::_supersede).
CREATE OR REPLACE VIEW item_document_history AS
  SELECT id.*, d.type, d.regulation, d.validity_from, d.validity_to, d.archive_url,
         d.status         AS doc_status,
         d.superseded_by  AS superseded_by,
         ee.expires       AS expires,
         ee.basis         AS expiry_basis
    FROM item_document id
    JOIN document d USING (doc_id)
    LEFT JOIN document_effective_expiry ee ON ee.doc_id = d.doc_id
   WHERE d.status IN ('production', 'superseded') AND id.status = 'production';
GRANT SELECT ON item_document_history TO dentalia_api;   -- migration 070

-- ONE definition of "when does this document lapse", for every consumer
-- (report.expiring_documents, the items board's next_expiry, /expiry). Added
-- 027 because the rule already had three implementations and they had already
-- drifted. Client ruling 2026-08-18: follow the certificate a declaration
-- cites, AND refresh any declaration older than five years -- two rules that
-- both apply, so the EARLIER fires. Hence LEAST, not COALESCE. `basis` names
-- which rule won, so no screen can call a document "expired" when the document
-- claims nothing of the sort. A review horizon, not a legal expiry: MDR
-- Art. 56(2)'s five years governs notified-body certificates, a different
-- document. A certificate never gets a synthetic date (Art. 56 makes its
-- expiry mandatory, so a missing one is a defect that must stay visible).
-- SELECT-only grant: created after 007's blanket GRANT, so granted explicitly.
CREATE VIEW document_effective_expiry AS ...;   -- see migrations/027_effective_expiry.sql

-- 033: BC's item class vs the class that item's production documents state.
-- Read-only QA, and it stays that way: BC (`item_mirror.product_class`) is the
-- source of truth for item class and this view never feeds a write. It answers
-- two questions the registry could not answer before -- which blank BC classes
-- a held document could fill, and which non-blank ones a document contradicts.
--
-- `doc_class` COALESCEs the document COLUMN over the latest extraction attempt's
-- jsonb so the documents extracted BEFORE 032 are compared without being
-- re-adjudicated: the history backfill (`app/repair_stated_class.py`) appends a
-- new extract_rev and deliberately emits no `validate.doc`, so those rows carry
-- the value in the attempt and not (yet) in the column.
--
-- `agree-family` is not a fudge: documents almost never print the I subclasses
-- (Is/Im/Ir -- 10 of 768 measured, 2026-08-21), so BC `Ir` against a document's
-- `I` is the document being less specific, not the two disagreeing.
CREATE VIEW item_class_check AS ...;            -- see migrations/033_item_class_check.sql

-- per written field, per extraction revision — GATE rejects candidates missing
-- any required field's evidence (page required for T1/T2 only, [evidence-page]
-- ruling 2026-07-31; T0/T3 legitimately pageless)
CREATE TABLE evidence (
  id           bigserial PRIMARY KEY,
  doc_id       bigint NOT NULL REFERENCES document,
  field        text   NOT NULL,             -- e.g. 'validity_to'
  value        text   NOT NULL,
  archive_url  text   NOT NULL,
  page         int,
  verbatim     text   NOT NULL,             -- exact string as read
  tier         text   NOT NULL,
  model_id     text,
  confidence   numeric NOT NULL,
  extracted_at timestamptz NOT NULL,
  extract_rev  int NOT NULL DEFAULT 1        -- 014: idempotency guard is now
                                              -- (doc_id, field, extract_rev), not
                                              -- (doc_id, field) — [gate-evidence]
                                              -- ruling 2026-07-31, so a re-extraction
                                              -- always gets matching evidence instead
                                              -- of being blocked by an older rev's row.
                                              -- "Current" = MAX(extract_rev) per field.
);

-- append-only; no UPDATE/DELETE grants
CREATE TABLE audit_log (
  id           bigserial PRIMARY KEY,
  event        text NOT NULL,     -- production-write|approve|reject|link-rejected|edit|supersede|bind-manufacturer|unbind
  doc_id       bigint,
  item_ref     text,
  decided_by   text NOT NULL,     -- 'gate' | user id
  via_job      bigint,            -- C8: SOFT reference — no FK; queue rows prunable on any schedule
  job_snapshot jsonb NOT NULL,    -- C8: type, payload, dedupe_key, claimed_by, timestamps —
                                  -- captured at write time; audit trail self-contained for 10 y
  detail       jsonb,
  at           timestamptz NOT NULL DEFAULT now()
);
```

## 5b. Manual queue (G13) — work items, not registry

```sql
-- Durable home for push_manual_queue(): GATE manual dispositions (gate-manual),
-- DISCOVER dead-ends (discovery-dead-end, S1.3), dead-job follow-ups. A manual_task
-- is WORK, never a shortcut around the queue: resolving one enqueues a job and a
-- handler marks it resolved. Owner-role writes; dentalia_api SELECT-only (renders
-- the manual list). NOT a registry table — inv. 1 (single registry writer) unaffected.
CREATE TYPE manual_kind   AS ENUM ('discovery-dead-end','gate-manual','dead-job-followup');
CREATE TYPE manual_status AS ENUM ('open','resolved');

CREATE TABLE manual_task (
  id          bigserial     PRIMARY KEY,
  kind        manual_kind   NOT NULL,
  group_id    bigint,                             -- soft context (no FK): backfill/email tasks may have none
  doc_id      bigint        REFERENCES document,  -- gate-manual → staged doc; safe FK (docs never deleted)
  payload     jsonb         NOT NULL,             -- gate flags + all tier attempts / prefilled search links / dead-job snapshot
  status      manual_status NOT NULL DEFAULT 'open',
  resolved_by text,
  created_at  timestamptz   NOT NULL DEFAULT now(),
  resolved_at timestamptz
);
CREATE INDEX manual_task_open_idx ON manual_task (kind, created_at) WHERE status='open';
GRANT SELECT ON manual_task TO dentalia_api;   -- migration 009
```

## 5c. Scheduler ledger (S1.5, migration 013 — GAP B restart safety)

```sql
-- One row per (cron name, period) that has fired at least once. A period is
-- "already run" iff this row exists — independent of the job's own lifecycle
-- (done/dead both free the C2 dedupe key, which this ledger does not rely on).
-- job_id is a SOFT reference (no FK), same posture as audit_log.via_job (C8):
-- queue rows may be pruned on any schedule without breaking this ledger.
CREATE TABLE scheduler_run (
  name        text        NOT NULL,
  period_key  text        NOT NULL,
  ran_at      timestamptz NOT NULL DEFAULT now(),
  job_id      bigint,                        -- null when the enqueue deduped (C2)
  PRIMARY KEY (name, period_key)
);
GRANT SELECT ON scheduler_run TO dentalia_api;
```

## 5d. Upload inbox (web-document-upload slice, migration 014) — transient spool, not the registry

```sql
-- Web /upload hands a PDF to the pipeline without an HTTP fetch: the bytes sit
-- here just long enough for a worker to archive + hash + emit the spine job,
-- then the row is deleted. dentalia_api gets INSERT + sequence USAGE only (it
-- enqueues the upload but never reads/deletes the spool and never touches the
-- registry, inv. 1) — new table after 007's blanket grant, so grant explicitly,
-- same posture as manual_task (009). Worker role keeps its default SELECT/DELETE.
CREATE TABLE upload_inbox (
  id              bigserial   PRIMARY KEY,
  filename        text        NOT NULL,
  content         bytea       NOT NULL,
  target_group_id bigint,                 -- soft ref (no FK): null when standalone
  catalogue       text        NOT NULL,
  uploaded_by     text,
  created_at      timestamptz NOT NULL DEFAULT now()
);
GRANT INSERT ON upload_inbox TO dentalia_api;
GRANT USAGE ON SEQUENCE upload_inbox_id_seq TO dentalia_api;
```

No `RETURNING id` on the web side: Postgres requires SELECT on any column named in `RETURNING`, and `dentalia_api` deliberately has none. The route instead reads `currval('upload_inbox_id_seq')` in the same session — needs only the granted sequence USAGE.

## 5e. Import inbox (BC import upload slice A, migration 040) — transient spool, two-phase

```sql
-- Carries an uploaded BC export from the web producer to the worker that
-- parses it. NOT upload_inbox: that table is single-phase (one read is the
-- whole lifecycle — a missing row means "already processed"). Here a PREVIEW
-- job reads the row and LEAVES it, and only the APPLY job's read consumes it,
-- so the two must not share a table or the idempotency contract goes
-- ambiguous. `kind` is CHECK-constrained ('items' | 'vendors') rather than an
-- enum: it routes a spool row inside this feature, not a pipeline contract,
-- and both values exist from the start so slice B (vendors) needs no
-- migration for the discriminator. `catalogue` is nullable: an items row
-- carries 'LJ' (one Business Central — Denis 2026-08-19, closing G17/G11), a
-- vendors row carries none.
CREATE TABLE import_inbox (
  id           bigserial   PRIMARY KEY,
  kind         text        NOT NULL CHECK (kind IN ('items','vendors')),
  filename     text        NOT NULL,
  content      bytea       NOT NULL,
  catalogue    text,
  uploaded_by  text,
  created_at   timestamptz NOT NULL DEFAULT now()
);
GRANT INSERT ON import_inbox TO dentalia_api;
GRANT USAGE ON SEQUENCE import_inbox_id_seq TO dentalia_api;
```

`dentalia_api` holds INSERT and sequence USAGE only — deliberately no SELECT, unlike a table a route later needs to read back: the preview page gets the filename from `job.payload`, which the web role can already read, not from the spool row. Abandoned previews (uploaded, looked at, never applied) are collected by `app/import_spool.gc` opportunistically — run by the import handlers on every import job, not a scheduler tick, since `app/scheduler.py`'s `_tick_*` functions enqueue and never otherwise write — after `SPOOL_RETENTION_DAYS` (7).

## 6. Phase 2 tables (declared, not built in Phase 1)

```sql
-- read by: discover.group (playbook), email.request (contacts), failure monitor (stats)
-- NO LONGER PHASE 2. Two branches gave it a writer within days of each other:
-- migration 042 (S2.3 stage 2) upserts it (`ON CONFLICT DO NOTHING`) before any
-- dependent EUDAMED row -- see §6b -- and migration 049 (2026-08-26) makes
-- `dentalia manufacturers seed` its bulk writer, with `/manufacturers/{name}`
-- editing `contact_emails` and `/playbooks/{slug}` the body. Until then it had
-- 0 rows, so both of the readers named above always returned empty and every
-- renewal draft was unaddressed. `hit_rate_stats`/`quirks` remain unbuilt.
CREATE TABLE manufacturer (
  id             bigserial PRIMARY KEY,
  canonical_name text UNIQUE NOT NULL,
  contact_emails text[],
  playbook_ref   text,            -- HISTORICAL: named a path in a playbook git
                                  -- repo. Never written. The body lives in
                                  -- manufacturer.body from 050.
  eudamed_srn    text,            -- deliberately unused (one canonical name can span several EUDAMED entities) -- see manufacturer_srn, §6b
  hit_rate_stats jsonb,
  quirks         jsonb,           -- {bot_wall: true, login_portal: true, ...}
  srn_probed_at  timestamptz,     -- migration 042: when the article-probe SRN fallback last ran here. NULL = never
                                  -- probed; a timestamp with no manufacturer_srn row = probed and nothing found
  slug           text UNIQUE,     -- 049: claiming playbook; NULL for the ~350 without one
  body           jsonb,           -- 050: the BEHAVIOUR half of a playbook. The 13
                                  -- fields with no cross-row invariant and no
                                  -- reader that queries into them; the three that
                                  -- DO have one are shredded below. NULL = no
                                  -- playbook authored, which is what the
                                  -- onboarding worklist filters on.
  playbook_rev   int NOT NULL DEFAULT 0,   -- doubles as the optimistic lock
  updated_at     timestamptz NOT NULL DEFAULT now(),
  updated_by     text             -- proxy-authenticated user, same value as decided_by
);
-- C4: manufacturer-scope bindings are derivable as a view over audit_log
-- (event='bind-manufacturer' minus 'unbind') or materialized here as binding_doc_ids.

-- 049: the manufacturer ENTITY as rows. `reconcile._entities` computed this
-- inside `playbooks sync` and threw it away; these tables make it one rule that
-- the UI and the CLI cannot disagree about.
--
-- The two PKs below ARE `playbooks.validate()`'s two checks, moved into the
-- database, and the FK is a third check that has never existed anywhere: today
-- a playbook may claim a BC code that BC does not issue and nothing notices.
--
-- written by: `dentalia manufacturers seed` (CLI) · read by: app/playbooks.py (049)
CREATE TABLE manufacturer_bc_code (
  code_source     text NOT NULL,
  code            text NOT NULL,
  manufacturer_id bigint NOT NULL REFERENCES manufacturer,
  source          text NOT NULL CHECK (source IN ('vendor-master','playbook')),
                                  -- same vocabulary as manufacturer_alias.source
                                  -- (017): 'playbook' = a human named this code,
                                  -- and a BC refresh may never re-point it.
  PRIMARY KEY (code_source, code),                        -- one code, one manufacturer
  FOREIGN KEY (code_source, code) REFERENCES vendor_master -- the LINK; never written from here
);

CREATE TABLE manufacturer_name (
  name_folded     text PRIMARY KEY,   -- casefold(); for_manufacturer compares folded,
                                      -- manufacturer_alias.raw_name does not
  name            text NOT NULL,      -- as authored, for display
  manufacturer_id bigint NOT NULL REFERENCES manufacturer,
  kind            text NOT NULL CHECK (kind IN ('canonical','alias'))
);

-- INSERT + UPDATE, never DELETE: the UI authors and corrects manufacturers, it
-- does not remove them. Invariant 1 untouched — none of these is document /
-- item_document / evidence.
GRANT SELECT, INSERT, UPDATE ON manufacturer, manufacturer_bc_code,
      manufacturer_name TO dentalia_api;                  -- migration 049
GRANT USAGE ON SEQUENCE manufacturer_id_seq TO dentalia_api;

-- 050: every edit to a body, kept. Two things already depend on it:
-- `extraction_attempt` has carried (playbook_slug, playbook_rev) since 031, so
-- "extracted under playbook 3" is only useful while playbook 3 is readable;
-- and revert is not a delete, it writes the old body FORWARD as a new
-- revision, so a mistake and its undo both survive.
--
-- written by: web (UI save/revert), `manufacturers seed` (rev 0 = the file's
-- provenance) · read by: web (history card)
CREATE TABLE manufacturer_playbook_revision (
  manufacturer_id bigint NOT NULL REFERENCES manufacturer,
  rev             int    NOT NULL,
  body            jsonb  NOT NULL,
  authored_at     timestamptz NOT NULL DEFAULT now(),
  authored_by     text,
  note            text,               -- required on a UI save (Denis 2026-08-27)
  PRIMARY KEY (manufacturer_id, rev)
);

-- SELECT + INSERT and deliberately NO UPDATE, NO DELETE. Append-only is
-- enforced by the grant, not by convention. Verified against a throwaway
-- database 2026-08-26: `dentalia_api` is refused both.
GRANT SELECT, INSERT ON manufacturer_playbook_revision TO dentalia_api;

-- 050: the loader's cache watermark, one row. Replaces the file loader's
-- `scandir` signature. `max(updated_at)` over `manufacturer` would be a seq
-- scan in VALIDATE's per-group hot loop; this is one indexed read. Bumped by
-- three FOR EACH STATEMENT triggers on manufacturer / manufacturer_bc_code /
-- manufacturer_name.
CREATE TABLE playbook_epoch (
  one   boolean PRIMARY KEY DEFAULT true CHECK (one),
  epoch bigint  NOT NULL DEFAULT 0
);
GRANT SELECT, UPDATE ON playbook_epoch TO dentalia_api;

-- 051: `playbooks/robots_refused.txt` as rows. Hosts we have RULED we may
-- never fetch (Denis 2026-08-20: 5 hosts, 4 manufacturers, 822 items) --
-- which is NOT what a host's robots.txt says. `app/robots.py` reads that at
-- fetch time; this carries a decision that does not depend on it, and COLTENE
-- is why both exist (its robots.txt reads as permitted for our agent).
--
-- It moved when the `./playbooks` bind mount left the `web` service: a missing
-- file is an empty set by design (absence must not raise on DISCOVER's path),
-- so the guard that refuses a kind:"direct" source would have failed OPEN in
-- the one process where a non-dev authors those.
--
-- SELECT only: the UI must be able to refuse a save for this reason and must
-- not be able to lift a refusal, which is taken after asking a manufacturer.
CREATE TABLE refused_host (
  host     text PRIMARY KEY,
  note     text,                  -- WHY it is listed; the only audit there is
  added_at timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT ON refused_host TO dentalia_api;

-- written by: email.request/email.reminder/email.poll (state transitions)
CREATE TABLE renewal_request (
  id               bigserial PRIMARY KEY,
  doc_id           bigint REFERENCES document,
  state            text NOT NULL,      -- due|requested|awaiting|received|parsed|escalated
  email_thread_ref text,
  created_at       timestamptz, updated_at timestamptz
);
```

## 6b. EUDAMED certificate register, sweep state, and their views (S2.3 stage 2, migrations 041-046)

**EUDAMED holds zero declarations of conformity.** Every table and view below
can say a certificate exists, that its standing changed, or that a device is
registered — never supply the document. Nothing here is written by
`gate.candidate` / `gate.apply`, and nothing here is a `match_basis` (`ref-eudamed`
stays capped at `staged` and unwritten — §6.1 of the design spec measured its
yield across six mirrored Basic UDI-DI groups: 116 catalogue items reached, 110
already had article-level production evidence, 6 would be new, and ruled that
not worth a constraint amendment).

```sql
-- written by: eudamed.certregister (whole-register pull) · `dentalia mine-srn`
-- (066) · one canonical name, many EUDAMED entities (IVOCLAR alone is BC codes
-- 001/005/275) -- storing one SRN per manufacturer would report a sibling
-- entity's articles as unregistered.
CREATE TABLE manufacturer_srn (
  canonical_name text NOT NULL REFERENCES manufacturer(canonical_name),
  srn            text NOT NULL,
  actor_name     text,
  discovered_via text NOT NULL     -- register-exact | register-fuzzy | article-probe | text-mined (066)
    CHECK (discovered_via IN ('register-exact','register-fuzzy','article-probe','text-mined')),
  status         text NOT NULL DEFAULT 'pending'  -- THE SRN TRUST RULE: only auto/confirmed are ever swept
    CHECK (status IN ('auto','pending','confirmed','rejected')),
  match_score    real,
  probe_ref      text,             -- the article number that bootstrapped this row, when discovered_via='article-probe'
  source_doc_id  bigint REFERENCES document(doc_id),  -- (066) the document a 'text-mined' SRN was read from; probe_ref's audit twin
  first_seen     timestamptz NOT NULL DEFAULT now(),
  decided_at     timestamptz,
  decided_by     text,
  PRIMARY KEY (canonical_name, srn)
);
-- `text-mined` rows are ALWAYS written 'pending', never 'auto': the pattern
-- matches an importer's or a notified body's SRN quoted inside somebody else's
-- document just as happily as the manufacturer's own, and a confirmation makes
-- that manufacturer's whole catalogue sweepable.

-- written by: eudamed.certregister · keyed (certificate_number, revision_number,
-- actor_srn) -- EUDAMED serves each revision as its own record, tested against
-- all 4.608 rows with zero collisions on this key (2026-08-26).
CREATE TABLE eudamed_certificate (
  certificate_number text NOT NULL,
  revision_number    text NOT NULL DEFAULT '',
  actor_srn          text NOT NULL,
  actor_name         text,
  certificate_type   text,
  certificate_status text,        -- 9 values; withdrawn/cancelled/restricted/suspended drive certificate_status_alert
  issue_date         date,
  starting_validity  date,
  expiry_date        date,
  notified_body_srn  text,
  version_number     integer,
  first_seen         timestamptz NOT NULL DEFAULT now(),  -- NEVER touched by ON CONFLICT -- how a renewal is detected
  synced_at          timestamptz NOT NULL,
  PRIMARY KEY (certificate_number, revision_number, actor_srn)
);

-- written by: eudamed.sweep (last_swept_at) and the web release button (due_at
-- write is the scheduler's, released_at/released_by are a human's, never both
-- at once) -- "no sweep ever runs unattended, an operator releases every run"
CREATE TABLE eudamed_sweep_state (
  canonical_name text PRIMARY KEY REFERENCES manufacturer(canonical_name),
  last_swept_at  timestamptz,
  due_at         timestamptz,
  released_at    timestamptz,
  released_by    text
);
```

**The eleven views** (six in migration 043, one in 044, four in 046). All
`SELECT`-only, nothing here writes the registry:

| View | Migration | Reads | What it answers |
|---|---|---|---|
| `held_certificate` | 043 | `document`, `item_document` (production), `item_group(_member)` | Our own EC/ISO certificates, split into `base_cert_number` + `our_revision` for the join below. Scoped to `type IN ('EC','ISO')` — a DoC that merely *cites* a certificate number is not holding it |
| `trusted_manufacturer_srn` | 043 | `manufacturer_srn` | The SRN trust rule as a view: `status IN ('auto','confirmed')` only |
| `certificate_drift` | 043 | `held_certificate`, `trusted_manufacturer_srn`, `eudamed_certificate` | A held certificate whose revision disagrees with EUDAMED's, joined on the safe baseline split only — both revisions reported, neither resolved (2026-08-19 ruling: resolving one to the other would assert a supersession silently) |
| `certificate_drift_candidate` | 043 | same as above | Near-misses (`trailing-letter`, `sx-hz-prefix`) — shown, never joined; a person confirms |
| `certificate_status_alert` | 043 | `trusted_manufacturer_srn`, `eudamed_certificate` | The adverse four statuses (`withdrawn`/`cancelled`/`suspended`/`restricted`) — a change in a certificate's standing, **not** a document request |
| `certificate_gap` | 043 | `trusted_manufacturer_srn`, `eudamed_certificate`, `held_certificate` | A certificate EUDAMED lists that no revision of ours matches by base number — the cheapest of the four asks (a name + notified body, one line in an e-mail) |
| `eudamed_sweep_due` | 044 | `eudamed_sweep_state`, `trusted_manufacturer_srn` | Due and not yet released — what the release-button page reads |
| `eudamed_article_status` | 046 | `item_mirror`, `item_group(_member)`, `trusted_manufacturer_srn`, `eudamed_mirror`, `item_document`, `document` | Shared base: one row per (our MD article, EUDAMED match, article-level DoC state). `eudamed_declaration_gap` and `eudamed_gap_summary` both read this so they cannot disagree about "covered" |
| `eudamed_declaration_gap` | 046 | `eudamed_article_status` | The request list, grouped by Basic UDI-DI (a declaration covers the group, not the article — Carl Martin's largest gap is 2.569 articles behind 70 documents to request, not 2.569) |
| `eudamed_gap_summary` | 046 | `eudamed_article_status` | The denominator: covered / staged / missing / `not_in_eudamed` per manufacturer, the last never folded into either bucket |
| `eudamed_sweep_delta` | 046 | `eudamed_mirror`, `trusted_manufacturer_srn`, `eudamed_sweep_state` | What changed since the manufacturer's last reviewed sweep — new devices (`first_seen`) and status changes. A never-swept manufacturer (`last_swept_at IS NULL`) reports an EMPTY delta, never "everything is new" (ruling, Denis 2026-08-26: a delta needs a baseline) |

## 6c. Writeback ledger and alert state (2026-09-07, migrations 063-064)

```sql
-- What Business Central actually took, one row PER FIELD.
CREATE TABLE bc_push_log (
  id          bigserial PRIMARY KEY,
  item_ref    text NOT NULL,
  field       text NOT NULL,          -- pteValidDeclaration | pteValidCECertificate | pteWarehouseURL
  old_value   text,
  new_value   text NOT NULL,
  http_status int,                    -- NULL = never sent
  response    text,
  pushed_at   timestamptz NOT NULL DEFAULT now(),
  via_job     bigint                  -- soft ref, invariant 10
);

-- What we have already told a person about, so a condition that persists is
-- one message rather than one every five minutes.
CREATE TABLE alert_state (
  condition   text PRIMARY KEY,
  subject     text NOT NULL,
  first_seen  timestamptz NOT NULL DEFAULT now(),
  last_sent   timestamptz
);
```

`bc_push_log` is **the diff source**, not merely a record: `bc.push` reads the
last row per field whose `http_status` is 2xx and sends only what differs. A
refused attempt is kept and deliberately excluded from that read — counting it
as sent would leave the next diff looking clean while BC held a value it never
took. Nothing ever reads the fields back out of BC, so a value changed inside BC
is invisible here by design.

`dentalia_api` holds **SELECT only** on `bc_push_log` — the bulk preview reads
it, and the write is a worker's.

## 7. Tag → table access matrix

| queue tag | reads | writes |
|---|---|---|
| ingest.run | (source file / BC API), import_inbox (spool read, `source=upload`, added 2026-08-25 — leaves the row on a `dry_run` preview) | item_mirror, import_inbox (consume on non-dry-run apply; opportunistic GC) |
| resolve.group | item_mirror, manufacturer_alias, item_group(_refs), document (current) | item_group(+member incl. mfr_ref), manufacturer_alias, grouping_suggestion |
| discover.group | item_group(_refs), fetch_log, eudamed_mirror, manufacturer* | discovery_log, manual_task (dead-end push + stale-task resolve) |
| fetch.url | fetch_log | fetch_log, domain_lease (politeness upsert + `needs_playwright` flag), archive files |
| backfill.scan | Drive corpus | fetch_log, archive files |
| email.poll | mailbox, extraction_attempt (seen-hash check), email_poll_log (reprocess guard) | fetch_log, archive files, email_poll_log |
| upload.ingest | upload_inbox, item_group (manufacturer lookup), extraction_attempt (seen-hash check) | fetch_log, archive files, upload_inbox (delete on success), manual_task (worker-side dead-end resolve only) |
| vendor.import | import_inbox (spool read, `kind='vendors'` — leaves the row on a `dry_run` preview), vendor_master (the diff's current side) | vendor_master (upsert; a code absent from the file keeps its row with a stale `import_batch`, never deleted), import_inbox (delete on a successful apply, plus opportunistic age-out). Emits nothing — `manufacturer_alias` is left stale on purpose, see the runbook |
| WEB `/upload` (producer, not a handler) | — | upload_inbox (INSERT only), job (enqueue only) |
| WEB `/import` (producer, not a handler, added 2026-08-25; vendor section 2026-08-26) | job.payload (preview page reads the spooled filename back off the job, not the table — `dentalia_api` has no SELECT on `import_inbox`) | import_inbox (INSERT only), job (enqueue only) |
| WEB `/emails` (producer, not a handler) | email_poll_log, document (hash→doc link) | — (read-only) |
| extract.doc | archive files, batch_ref | extraction_attempt, batch_ref, document_text |
| validate.doc | item_group_refs, document (current), extraction_attempt, document_text (C4 device-enumeration guard, 2026-08-25) | — (stateless) |
| gate.candidate / gate.apply | extraction_attempt, document | **document, item_document, evidence, audit_log, manual_task** |
| eudamed.sync | EUDAMED bulk export | eudamed_mirror |
| eudamed.certregister | EUDAMED certificate register (16 calls), manufacturer_alias, manufacturer | eudamed_certificate, manufacturer_srn, manufacturer (upsert) |
| eudamed.sweep | trusted_manufacturer_srn, manufacturer (srn_probed_at), item_mirror + item_group(_member) + manufacturer_alias (article-probe fallback candidates) | eudamed_mirror, eudamed_sweep_state, manufacturer_srn (probe bootstrap only) |
| WEB `/manufacturers/srn-queue` + `/manufacturers/sweep-due` (producer, not a handler, added S2.3 stage 2) | manufacturer_srn, eudamed_sweep_due (view), trusted_manufacturer_srn | manufacturer_srn (confirm/reject a pending SRN), eudamed_sweep_state (release stamp only — `queue.enqueue`'s dedupe return governs whether it re-stamps), job (enqueue `eudamed.sweep` only). **This is the first write grant `dentalia_api` has ever held beyond `job` — it still holds zero write grant on `document` / `item_document` / `evidence` (invariant 1 unchanged)** |
| email.request/reminder | document (expiry horizon), item_document + item_group_member + item_group (the manufacturer a document is reached through), manufacturer (contact_emails), renewal_request (cadence attach) | renewal_request, **renewal_request_document**, **email_draft** *(both added to this row 2026-08-27 — long shipped, never listed. `renewal_request_document` is the request's document set: a second trigger in the same cadence period ADDS its documents to the live request rather than drafting a second mail, so this table, not the request's own `doc_id`, is what the mail is built from. `email_draft` is where the chain terminates: `ready` is terminal and there is no send handler, by client ruling 2026-08-20 — this system prepares a draft and a person sends it.)* |
| report.weekly | document (expiry horizon), job counts, discovery_log + item_group (failure spikes) | — (read-only snapshot) |
| scheduler.tick | scheduler_run, config, plus whatever the named cron reads (the rows above for `report.weekly`, `email.request`, `eudamed.certregister`) | scheduler_run, job (enqueue only), `eudamed_sweep_state.due_at`. Added 2026-08-31: the cadence is a job now, so the process row below is what it replaces |
| SCHEDULER (process, not a tag) | scheduler_run, config, item_document + item_group (resolving the manufacturer the renewal key names, 2026-08-27) | scheduler_run, job (enqueue only). Also `eudamed_sweep_state.due_at` (marks due, never releases — §6b) |
| WORKER loop (a process, not a tag, added 2026-08-27) | service_heartbeat (`cli healthcheck`, each container's own HEALTHCHECK; the web header strip reads it too) | **service_heartbeat** (035) — written by `app/heartbeat.py`'s `beat`/`clear` from the worker loop (`app/workers/runner.py:216,230`), on a schedule rather than on a job. It has no row above because **no tag writes it**: it is liveness, not pipeline state, and a matrix keyed by job tag cannot express it. *Corrected 2026-09-07: this row also named a scheduler loop, which was deleted 2026-09-02 — the worker is the only heartbeat writer now.* |
| bc.push | item_document + document + document_effective_expiry + eudamed_certificate (the holdings the rule reads), bc_push_log (the last accepted value per field) | **bc_push_log** only, plus the PATCH to Business Central's own `dataitems` — which is outside this database entirely. No registry table, so invariant 1 is untouched |
| WEB `/bc-push` + `/items/{ref}/bc-push` (producer, not a handler, added 2026-09-07) | item_mirror, bc_push_log (SELECT — the preview is a live query, never a stored plan) | job (enqueue `bc.push` only) |
| SCHEDULER `health-watch` cron (2026-09-07) | service_heartbeat, job (movement, `max(claimed_at)`), scheduler_run, alert_state | **alert_state** (064) — and an HTTP POST to `alerts.webhook_url`, which is outside this database too |
| any handler (019) | — | job (via `queue.finish`'s `result` column), data_anomaly (any handler may write it -- Invariant 1 covers only document/item_document/evidence) |

| WEB `/data-quality` (producer, not a handler) | data_anomaly, item_class_check | — (read-only) |
| WEB `/playbooks/{slug}` (producer, not a handler; slices 3a/3b) | manufacturer, manufacturer_bc_code, manufacturer_name, manufacturer_playbook_revision, vendor_master (the code picker and the brand-collision map), refused_host | manufacturer (`body`, `playbook_rev`, `updated_by/at`), manufacturer_playbook_revision (INSERT only — no UPDATE, no DELETE granted), manufacturer_bc_code (INSERT/UPDATE only; **no DELETE**, so un-claiming a code is CLI work), playbook_epoch (via the 050 triggers) |
| WEB `/manufacturers/{name}` rename + start-playbook (producer, not a handler; slice 3b/4) | manufacturer, manufacturer_name, manufacturer_bc_code, vendor_master, item_group + item_group_member + item_document (`regroup.rename_impact`, read-only — a SUBSET of `regroup.scope()`, which also counts `upload_inbox` rows this role may not read) | manufacturer (`canonical_name`, `slug`, `body`, `updated_by/at`), manufacturer_name (the old canonical demoted to an alias, never deleted), manufacturer_playbook_revision. **Not** `manufacturer_alias` and **not** `item_group`: SELECT-only on both, so re-pointing and re-resolving stay `playbooks sync` and `regroup` |

\* Phase 2. `extract.doc` additionally writes `extraction_cost` (010); the KPI board reads `extraction_spend`.

`item_class_check` (033) is written by **nobody** — it is a view, and `dentalia_api` holds SELECT on it only. Its inputs are written on the ordinary routes: `document.stated_class` by `gate.candidate`/`gate.apply` (invariant 1, unchanged), `extraction_attempt.fields` by `extract.doc` and by the one-off `app/repair_stated_class.py` (append-only, a new `extract_rev`, never an UPDATE). Nothing anywhere writes `item_mirror.product_class` from a document.

Invariants encoded: single writer for the registry (§5 header) · `content_hash UNIQUE` on document = one row per physical file · link-level status with CHECK on trusted bases (C5) · partial unique dedupe index = terminal jobs never block re-enqueue (C2) · `batch_ref` PK = never double-submit (C9) · `job_snapshot` = audit survives queue pruning (C8) · closed `job_type` enum = new tags require a PRD change first.
