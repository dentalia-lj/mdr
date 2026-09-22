-- 055_scheduler_tick_seed.sql
-- Arms every cron at migrate time rather than on the next worker restart: an
-- already-running worker never re-enters main(), so bootstrap alone would leave
-- the crons non-existent until something recreated the container.
--
-- ON CONFLICT DO NOTHING against the active-only dedupe index makes this a
-- no-op for any cron already armed, so re-running it is safe. `dead` is NOT in
-- that index, so a dead-lettered cron is re-armed by this same statement --
-- which is the mechanism the worker's arm_crons() reuses on every start.
--
-- The ledger (scheduler_run), not run_after, decides whether a period has
-- fired. run_after is only how often a cron ASKS, so a late poll fires the same
-- period the old wall-clock loop would have.
--
-- The two-minute delay closes a deploy window, and it was measured rather than
-- imagined. `migrate` completes before the NEW worker starts, but the OLD
-- worker is still running and polling once a second while it does, and that
-- worker has no scheduler.tick handler. Claiming one costs an attempt and
-- writes `LookupError: no handler registered for scheduler.tick` -- reproduced
-- on 2026-08-31 against a real database, `status=failed attempts=1`. Five of
-- those and a cron is dead before it ever ran. Two minutes is longer than any
-- container swap here and costs nothing: the first tick is a poll, not a
-- deadline.
--
-- priority 'delta': 'interactive' is reserved for human-triggered work, and
-- 'sweep' would let a daily scan starve for hours behind a backlog.

INSERT INTO job (type, payload, dedupe_key, priority, run_after)
SELECT 'scheduler.tick',
       jsonb_build_object('cron', cron),
       'scheduler.tick:' || cron,
       'delta',
       now() + interval '2 minutes'
  FROM (VALUES
          ('ingest.monthly'),
          ('expiry-scan'),
          ('failure-monitor'),
          ('report.weekly'),
          ('email.poll'),
          ('eudamed.certregister'),
          ('eudamed.sweep-due')
       ) AS c(cron)
ON CONFLICT (dedupe_key) WHERE status IN ('pending','running','failed')
DO NOTHING;
