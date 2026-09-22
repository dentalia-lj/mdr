-- 001_queue.sql
-- Sketch §0 (queue-tag registry) + §1 (queue).
-- Encodes: closed job_type enum (Invariant 7 / PRD §10 drift guard),
--          C2 active-only dedupe (partial unique index),
--          C9 Batch API tracking in a side table keyed by (content_hash, tier),
--          per-domain politeness lease (FETCH).
-- Runner applies this file once, inside one transaction.

-- §0 Queue tag registry — CLOSED SET. New tag = PRD change + migration first,
-- never a runtime string (Invariant 7). Exactly the 14 values from sketch §0.
CREATE TYPE job_type AS ENUM (
  -- pipeline spine
  'ingest.run',          -- INGEST
  'resolve.group',       -- RESOLVE
  'discover.group',      -- DISCOVER
  'fetch.url',           -- FETCH
  'extract.doc',         -- EXTRACT
  'validate.doc',        -- VALIDATE
  'gate.candidate',      -- GATE (machine)
  'gate.apply',          -- GATE (human decision from review UI)
  -- peripheral producers
  'backfill.scan',       -- Drive corpus walker   -> emits extract.doc
  'email.poll',          -- inbound mailbox        -> emits extract.doc
  'email.request',       -- outbound renewal       (Phase 2)
  'email.reminder',      --                        (Phase 2)
  'eudamed.sync',        -- mirror refresh cron
  'playbook.reonboard'   -- failure-monitor trigger (Phase 2)
);

CREATE TYPE job_status   AS ENUM ('pending', 'running', 'done', 'failed', 'dead');
CREATE TYPE job_priority AS ENUM ('interactive', 'delta', 'sweep');  -- ordinal 0,1,2

-- §1 Queue.
CREATE TABLE job (
  id           bigserial     PRIMARY KEY,
  type         job_type      NOT NULL,
  payload      jsonb         NOT NULL,                 -- IMMUTABLE after enqueue (C9)
  dedupe_key   text          NOT NULL,                 -- NOT globally unique — see partial index below
  status       job_status    NOT NULL DEFAULT 'pending',
  priority     job_priority  NOT NULL DEFAULT 'sweep',
  run_after    timestamptz   NOT NULL DEFAULT now(),
  attempts     int           NOT NULL DEFAULT 0,
  max_attempts int           NOT NULL DEFAULT 5,
  last_error   text,
  claimed_by   text,
  claimed_at   timestamptz,
  created_at   timestamptz   NOT NULL DEFAULT now()
);

-- C2: dedupe scoped to ACTIVE jobs only. done/dead never block re-enqueue —
-- monthly re-checks and post-model-swap re-extraction are normal operations.
CREATE UNIQUE INDEX job_dedupe_active_uq ON job (dedupe_key)
  WHERE status IN ('pending', 'running', 'failed');

-- Claim index: SELECT ... FOR UPDATE SKIP LOCKED over pending work,
-- ordered by (type, priority, run_after).
CREATE INDEX job_claim_idx ON job (type, priority, run_after)
  WHERE status = 'pending';

-- Per-domain politeness (FETCH only).
CREATE TABLE domain_lease (
  domain           text PRIMARY KEY,
  leased_until     timestamptz,
  politeness_ms    int     NOT NULL DEFAULT 2000,
  needs_playwright boolean NOT NULL DEFAULT false
);

-- C9: Batch API tracking — payloads stay immutable; a crash between submit and
-- record resolves via lookup here (existing row for hash+tier => never resubmit).
CREATE TABLE batch_ref (
  content_hash text        NOT NULL,
  tier         text        NOT NULL,        -- T1 | T2
  batch_id     text        NOT NULL,
  submitted_at timestamptz NOT NULL DEFAULT now(),
  resolved_at  timestamptz,
  PRIMARY KEY (content_hash, tier)
);
