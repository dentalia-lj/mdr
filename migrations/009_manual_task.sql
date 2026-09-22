-- 009_manual_task.sql
-- G13: manual-queue persistence. `push_manual_queue()` (GATE manual disposition
-- in §7; later DISCOVER dead-ends and dead-job follow-ups) had no durable home.
--
-- Invariant it keeps: a manual_task is a WORK item, never a shortcut around the
-- queue. Resolving one enqueues a job (gate.apply, discover.group, ...) and a
-- handler marks the task resolved — the API never mutates the registry through
-- here. So the API role gets SELECT only (it renders the manual list); the owner
-- role (dentalia, which runs the handlers) writes.

CREATE TYPE manual_kind   AS ENUM ('discovery-dead-end', 'gate-manual', 'dead-job-followup');
CREATE TYPE manual_status AS ENUM ('open', 'resolved');

CREATE TABLE manual_task (
  id           bigserial     PRIMARY KEY,
  kind         manual_kind   NOT NULL,
  group_id     bigint,                              -- soft context (no FK): backfill/email tasks may have none
  doc_id       bigint        REFERENCES document,   -- gate-manual points at the staged doc; safe FK (docs never deleted)
  payload      jsonb         NOT NULL,              -- kind-specific: gate flags + all tier attempts / prefilled search links / dead-job snapshot
  status       manual_status NOT NULL DEFAULT 'open',
  resolved_by  text,                                -- user id that resolved it (null while open)
  created_at   timestamptz   NOT NULL DEFAULT now(),
  resolved_at  timestamptz
);

-- The queue-facing query is "open tasks, oldest first, by kind" — partial index
-- keeps it cheap and mirrors the job_claim_idx pattern.
CREATE INDEX manual_task_open_idx ON manual_task (kind, created_at) WHERE status = 'open';

-- API (dentalia_api) renders the manual list -> SELECT only. New table, created
-- after 007's blanket GRANT, so it must be granted explicitly. No write grant:
-- resolution flows through the queue, handled by the owner role.
GRANT SELECT ON manual_task TO dentalia_api;
