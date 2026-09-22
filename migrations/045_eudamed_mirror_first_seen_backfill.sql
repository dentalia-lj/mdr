-- Backfill eudamed_mirror.first_seen for rows that predate migration 044.
--
-- 044 added `first_seen timestamptz NOT NULL DEFAULT now()`, which stamped every
-- already-mirrored device with the instant the ALTER TABLE ran rather than with
-- anything about the device. Measured immediately afterwards: all 591 rows
-- carried one identical first_seen of 2026-08-26 11:54:55, while their synced_at
-- ranged over 2026-08-25 19:07:00-19:08:01 -- a real prior observation, a full
-- day earlier.
--
-- first_seen is deliberately never touched by ON CONFLICT (it is the whole basis
-- of the sweep delta), so a wrong value here is permanent. `synced_at` is not the
-- true first sighting either -- it is the LAST one, so it is an upper bound --
-- but it is an upper bound derived from a real observation of that device,
-- which "whenever the migration ran" is not.
--
-- Scoped to rows whose first_seen is at or after the 044 apply instant so that
-- any row legitimately written after this point is left alone.
UPDATE eudamed_mirror
   SET first_seen = synced_at
 WHERE synced_at < first_seen;
