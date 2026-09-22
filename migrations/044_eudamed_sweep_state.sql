-- The sweep delta and the propose/release state
-- (docs/superpowers/specs/2026-08-25-eudamed-enhancement-design.md §3.4-3.5).
--
-- Before `first_seen`, the eudamed_mirror upsert set synced_at = now() on every
-- conflict, so after one sweep a device registered last week and one registered
-- in 2021 were indistinguishable. "What's new" was not derivable at all.
ALTER TABLE eudamed_mirror
  ADD COLUMN IF NOT EXISTS first_seen timestamptz NOT NULL DEFAULT now();

COMMENT ON COLUMN eudamed_mirror.first_seen IS
  'First time this udi_di was seen. NEVER updated by ON CONFLICT -- it is the '
  'whole basis of the sweep delta.';

-- Read for transitions, acted on nowhere. A withdrawn device does NOT leave the
-- declaration gap list: MDR retention runs ten years past the last device
-- placed, so the DoC is still owed. Any other retention rule is Dentalia's call.
ALTER TABLE eudamed_mirror
  ADD COLUMN IF NOT EXISTS device_status_type text;

-- RULING, Denis 2026-08-20, restated 2026-08-26: "No sweep ever runs
-- unattended... an operator releases every run." The scheduler writes due_at
-- and nothing else; released_at is written by a human clicking in the UI.
CREATE TABLE eudamed_sweep_state (
  canonical_name text PRIMARY KEY REFERENCES manufacturer(canonical_name),
  last_swept_at  timestamptz,
  due_at         timestamptz,
  released_at    timestamptz,
  released_by    text
);

-- Due and not yet released. Releasing clears nothing; the sweep handler resets
-- released_at when it finishes, which is what makes the next cycle possible.
CREATE VIEW eudamed_sweep_due AS
SELECT s.canonical_name,
       s.last_swept_at,
       s.due_at,
       (SELECT count(*) FROM trusted_manufacturer_srn t
         WHERE t.canonical_name = s.canonical_name) AS srns
  FROM eudamed_sweep_state s
 WHERE s.due_at IS NOT NULL
   AND s.due_at <= now()
   AND s.released_at IS NULL;

GRANT SELECT ON eudamed_sweep_state, eudamed_sweep_due TO dentalia_api;
-- Releasing a sweep is a producer decision, like enqueueing any other job.
GRANT INSERT, UPDATE ON eudamed_sweep_state TO dentalia_api;
