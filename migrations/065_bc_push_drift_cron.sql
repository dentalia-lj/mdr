-- 065_bc_push_drift_cron.sql
-- The tenth cron: bring Business Central back in step with the registry.
--
-- Seeded like 055 and 064, and revived by `runner.arm_crons()` on any restart
-- after it dead-letters. It does nothing at all while `bc.write_enabled` is
-- false, which is the default -- the tick is armed so that turning the flag on
-- is the only step, not "turn it on and remember to arm a cron".

INSERT INTO job (type, payload, dedupe_key, priority, run_after)
VALUES ('scheduler.tick', jsonb_build_object('cron', 'bc.push-drift'),
        'scheduler.tick:bc.push-drift', 'delta', now() + interval '2 minutes')
ON CONFLICT (dedupe_key) WHERE status IN ('pending','running','failed')
DO NOTHING;
