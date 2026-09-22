-- 013_scheduler.sql
-- SCHEDULER (S1.5). PHASES.md GAP A: ratifies PRD §8's "weekly report" prose
-- into a real job type. GAP B: restart-safety ledger — C2's active-only
-- dedupe frees a completed cron's key immediately, so a scheduler restart
-- mid-period would re-enqueue without an out-of-queue period marker.
--
-- ALTER TYPE ... ADD VALUE cannot be used in the same transaction that also
-- USES the new value (PG16) — this file only adds the value and creates the
-- ledger table; nothing here inserts a 'report.weekly' row. Verified live
-- against a scratch Postgres before this file was written.

ALTER TYPE job_type ADD VALUE 'report.weekly';

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

-- New table, created after 007's blanket GRANT, so grant explicitly. SELECT only:
GRANT SELECT ON scheduler_run TO dentalia_api;
