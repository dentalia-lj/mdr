-- 064_alert_state.sql
-- What we have already told a person about.
--
-- `app/health.py` evaluates the same conditions every poll, so without memory a
-- stale worker would push a line every five minutes until somebody fixed it --
-- which is how an alert channel gets muted, and a muted channel is worse than
-- none. One row per condition key: alerted once, re-alerted only after the
-- cooldown, and cleared with a recovery line when the condition goes away.

CREATE TABLE alert_state (
  key          text        PRIMARY KEY,   -- e.g. 'service-stale:worker:i1'
  kind         text        NOT NULL,      -- the condition family, for grouping
  message      text        NOT NULL,      -- as last sent
  first_seen   timestamptz NOT NULL DEFAULT now(),
  last_alerted timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE alert_state IS
  'Open operator alerts, one row per condition key. A row exists while the '
  'condition holds; deleting it is what sends the recovery line. Not an audit '
  'trail -- `audit_log` is for decisions, this is for suppression.';

-- The status board shows what is currently sounding; it never writes here.
GRANT SELECT ON alert_state TO dentalia_api;

-- The ninth cron. Same shape as 055, which seeded the first eight, and
-- `runner.arm_crons()` revives it on any restart after it dead-letters.
INSERT INTO job (type, payload, dedupe_key, priority, run_after)
VALUES ('scheduler.tick', jsonb_build_object('cron', 'health-watch'),
        'scheduler.tick:health-watch', 'delta', now() + interval '2 minutes')
ON CONFLICT (dedupe_key) WHERE status IN ('pending','running','failed')
DO NOTHING;
