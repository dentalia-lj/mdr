-- 063_bc_push_log.sql
-- What we told Business Central, and when.
--
-- Two jobs, and the second is why this is a table rather than an audit_log
-- entry. (1) Invariant 10: writing into someone else's ERP without a
-- self-contained record leaves "why does BC say true?" unanswerable. (2) It is
-- the diff source -- the drift cron asks this table what we last sent instead
-- of reading BC back, so detecting drift costs no API calls.
--
-- One row per FIELD, not per item: the three fields change independently and a
-- PATCH carries only the ones that differ, so a per-item row could not say
-- which value was actually sent.

CREATE TABLE bc_push_log (
  id          bigserial PRIMARY KEY,
  item_ref    text        NOT NULL,
  field       text        NOT NULL,
  old_value   text,                   -- what we believed BC held; null on first send
  new_value   text        NOT NULL,   -- booleans as 'true'/'false', the URL verbatim
  http_status int,                    -- null means the request never completed
  response    text,
  pushed_at   timestamptz NOT NULL DEFAULT now(),
  -- Soft reference, deliberately no FK (invariant 10): deleting every job row
  -- must leave this trail fully interpretable.
  via_job     bigint,

  CONSTRAINT bc_push_log_field_vocabulary
    CHECK (field IN ('pteValidDeclarationOfConformity',
                     'pteValidCECertificate',
                     'pteWarehouseURL'))
);

-- The diff query: the latest row per (item_ref, field). DESC on pushed_at so
-- the planner can walk straight to it.
CREATE INDEX bc_push_log_latest_idx
  ON bc_push_log (item_ref, field, pushed_at DESC);

COMMENT ON TABLE bc_push_log IS
  'Every field value sent to Business Central: what, when, by which job, and '
  'what BC answered. Append-only by convention; also the source the drift cron '
  'diffs against, so BC is never read back to find out what we last sent.';

COMMENT ON COLUMN bc_push_log.old_value IS
  'The value this write replaced, as we believed it -- read from this table, '
  'not from BC. Null on the first send for a field.';

-- The web process is a job producer: it reads this to show an operator what was
-- sent and enqueues `bc.push`, and never writes a row itself. Only the worker
-- writes, under the worker role.
GRANT SELECT ON bc_push_log TO dentalia_api;
