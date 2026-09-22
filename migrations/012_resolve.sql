-- 012_resolve.sql
-- S1.2 RESOLVE (resolve.group). Two additions the handler needs:
--   1. pg_trgm — in-DB candidate generation for the name-family rung (CLAUDE.md
--      tooling row: pg_trgm candidates -> rapidfuzz scoring). The GIN index makes
--      the `name % :query` similarity search cheap; scoping to a manufacturer is
--      the caller's job (bare names collide across makers, same C1 reasoning as
--      item_group_refs).
--   2. grouping_suggestion — durable home for the RESOLVE staging path (PRD §2:
--      ambiguous name clustering -> T1 suggestion -> staging, never auto-applied).
--      manual_task is work-items only (discovery/gate/dead-job); a grouping
--      suggestion is a "review this proposed membership", so it gets its own
--      table. Same grant posture as manual_task: API renders (SELECT), the owner
--      role (handlers) writes; resolution flows back through the queue
--      (resolve.group with an additive forced group_id), never an API bypass.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- name-family candidate generation: item.name %-similar to grouped members' names.
CREATE INDEX item_mirror_name_trgm ON item_mirror USING gin (name gin_trgm_ops);

-- written by: resolve.group (T1 staging path) · read by: review UI (staging tab, S1.6)
CREATE TABLE grouping_suggestion (
  id          bigserial   PRIMARY KEY,
  item_ref    text        NOT NULL REFERENCES item_mirror,
  candidates  jsonb       NOT NULL,            -- [{group_id, score, sample_name}] — the ambiguous cluster
  suggestion  jsonb,                           -- T1 adjudication output; null on T1 error (with t1_error)
  score       real,                            -- best candidate score (the middle-band value that staged it)
  status      text        NOT NULL DEFAULT 'open',   -- open | resolved
  created_at  timestamptz NOT NULL DEFAULT now(),
  resolved_by text,                            -- user id that resolved (null while open)
  resolved_at timestamptz
);

-- At most one OPEN suggestion per item — idempotent under at-least-once delivery
-- (a re-delivered resolve.group no-ops instead of stacking duplicates).
CREATE UNIQUE INDEX grouping_suggestion_open_item_idx
  ON grouping_suggestion (item_ref) WHERE status = 'open';

-- New table, created after 007's blanket GRANT, so grant explicitly. SELECT only:
-- resolution is a queued job handled by the owner role, never an API write.
GRANT SELECT ON grouping_suggestion TO dentalia_api;
