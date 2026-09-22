-- 035: service_heartbeat — is a long-running process still turning over?
--
-- The web container answers that question on every page and had no way to.
-- `docker compose ps` knows, but web has no Docker socket and must never get
-- one: mounting it would hand a job PRODUCER root on the host, for a status
-- chip. Postgres is the one channel every service already holds open, so the
-- signal goes here.
--
-- Why not derive it from the queue. `max(job.claimed_at)` was the no-migration
-- alternative and it is wrong in the state this system sits in most of the
-- time -- the queue is drained, so a healthy idle worker and a dead one are
-- indistinguishable. A heartbeat is the only thing that separates "nothing to
-- do" from "nobody home".
--
-- Keyed on (service, instance) because `docker compose up --scale worker=N` is
-- the documented way to run this. Three replicas is three rows and one service;
-- the reader collapses to the freshest, since one wedged replica out of three
-- does not mean the service is down.
--
-- Not a ledger. This table is upserted in place and holds exactly one row per
-- live instance -- a worker beating every second for a week would otherwise
-- leave 600.000 rows to answer a yes/no question. Nothing here is evidence,
-- nothing audits it, and dropping the whole table costs a restart's worth of
-- observability and nothing else.
CREATE TABLE service_heartbeat (
    service   text        NOT NULL,
    instance  text        NOT NULL,
    last_seen timestamptz NOT NULL DEFAULT now(),
    detail    jsonb,
    PRIMARY KEY (service, instance)
);

COMMENT ON TABLE service_heartbeat IS
  'Liveness only: one upserted row per running instance, no history. Read by '
  'the web header strip and by each container''s own HEALTHCHECK. Never '
  'evidence, never audited, safe to truncate.';

-- The web process reads this for the header strip. SELECT only -- it is a
-- producer and writes no registry table (invariant 1); its own beat goes
-- through the app role, not this one.
GRANT SELECT ON service_heartbeat TO dentalia_api;
