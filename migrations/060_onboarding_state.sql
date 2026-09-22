-- 060_onboarding_state.sql
-- The onboarding queue's memory of the answers that are NOT "here is the
-- recipe" (S2.2, the guided flow).
--
-- 346 of 384 suppliers have no search recipe, and the queue orders them by
-- item count so the biggest gap comes first. Measured 2026-09-04, the top of
-- that queue is SANOLABOR (94 items) -- which is a DISTRIBUTOR, not a
-- manufacturer, and has no compliance paperwork of its own to find. Without
-- somewhere to record that, the queue offers the same dead end to the same
-- person every morning, and the flow is unusable by the second row.
--
-- Three real answers besides finishing:
--   not-a-manufacturer  the entity issues no declarations (a distributor, a
--                       reseller, our own house label)
--   no-website          they have no site we can search
--   no-library          they have a site but no page that lists documents
--
-- None of them is a failure and none is permanent: every one carries a reason
-- and a name, and `cleared_at` is how somebody puts an entity back in the
-- queue when the answer changes (a distributor that starts manufacturing, a
-- supplier that finally publishes a library).
--
-- NOT a registry table. Nothing downstream reads it, no value derives from it,
-- it is never evidence, and deleting every row costs only the queue's memory
-- of what has been triaged. It is a worklist, in the same spirit as
-- `playbook_probe` being debris rather than record.

CREATE TABLE onboarding_state (
  -- Soft ref to `manufacturer.canonical_name`, deliberately not an FK: this
  -- outlives a rename, and a triage note that vanished when somebody
  -- renamed an entity would silently put a settled dead end back in the queue.
  canonical_name text        NOT NULL,
  state          text        NOT NULL,
  -- Required in effect: the route refuses a blank one. Months later,
  -- "this supplier files nothing" and "we agreed it has nothing to file" look
  -- identical from the outside -- the same reasoning that makes
  -- `skip_backfill.reason` mandatory at parse time.
  reason         text        NOT NULL,
  decided_by     text        NOT NULL,
  at             timestamptz NOT NULL DEFAULT now(),
  -- Set when somebody puts this entity back in the queue. The row is KEPT:
  -- who decided what, and who undid it, are both part of the trail.
  cleared_at     timestamptz,
  cleared_by     text,
  id             bigserial PRIMARY KEY,
  CONSTRAINT onboarding_state_state_ck
    CHECK (state IN ('not-a-manufacturer', 'no-website', 'no-library')),
  CONSTRAINT onboarding_state_reason_ck CHECK (btrim(reason) <> '')
);

-- One ACTIVE note per entity; a cleared one never blocks a new decision. Same
-- shape as the queue's own partial unique index on active jobs (invariant 8),
-- and for the same reason: a terminal row must not stop the next one.
CREATE UNIQUE INDEX onboarding_state_active_idx
  ON onboarding_state (canonical_name) WHERE cleared_at IS NULL;

-- The queue's only read: "which entities are triaged out right now".
CREATE INDEX onboarding_state_active_lookup_idx
  ON onboarding_state (canonical_name, state) WHERE cleared_at IS NULL;

-- `dentalia_api` is the web role (migrations 007/008). The flow writes these
-- notes directly rather than through a job: this is a worklist annotation, not
-- a registry write, so invariant 1 -- which names document / item_document /
-- evidence and nothing else -- is untouched. UPDATE is granted for `cleared_at`
-- only; there is no DELETE, because a triage decision is part of the trail.
GRANT SELECT, INSERT, UPDATE ON onboarding_state TO dentalia_api;
GRANT USAGE, SELECT ON SEQUENCE onboarding_state_id_seq TO dentalia_api;
