-- 054_scheduler_tick.sql
-- The cron cadence becomes queue state. One tag, with the cron in the payload:
-- the cron is data, exactly as email.request carries `state`, so a future cron
-- is another row rather than another contract change.
--
-- ALTER TYPE ... ADD VALUE cannot be used in the same transaction that also
-- USES the new value, and run_migrations gives each file one transaction
-- (app/db.py). This file therefore adds the value and nothing else; 055 seeds
-- the rows. Migration 013 hit the same constraint for report.weekly and says so
-- in its own header.
--
-- 053 was taken by 053_document_manufacturer.sql on a parallel worktree
-- (2026-08-31), which is why this pair starts at 054.

ALTER TYPE job_type ADD VALUE 'scheduler.tick';
