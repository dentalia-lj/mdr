-- 035: job.caused_by — which job's handler enqueued this one. Soft ref, no FK
-- (audit_log.via_job rationale: queue rows prune on any schedule; the queue is
-- regenerable state, the registry's trail is audit_log.job_snapshot). NULL =
-- a root: CLI, scheduler tick, web producer.
ALTER TABLE job ADD COLUMN caused_by bigint;
CREATE INDEX job_caused_by_idx ON job (caused_by) WHERE caused_by IS NOT NULL;
