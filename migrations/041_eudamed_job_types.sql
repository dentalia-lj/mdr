-- Two new queue tags for the EUDAMED enhancement path
-- (docs/superpowers/specs/2026-08-25-eudamed-enhancement-design.md §4).
--
-- `eudamed.certregister` takes no manufacturer: the whole register is pulled in
-- 16 calls and matched locally, because actorSrn arrives on every certificate
-- record, so SRN discovery is a by-product of the pull rather than a
-- prerequisite for it (measured 2026-08-26).
--
-- ALTER TYPE ... ADD VALUE cannot be used in the same transaction that also
-- USES the new value (PG16), and the migration runner applies each file inside
-- one transaction. Migration 013 hit this and documents it. So this file adds
-- the values and NOTHING else -- the tables that reference them ship in the
-- next file, and nothing here inserts a job row.

ALTER TYPE job_type ADD VALUE 'eudamed.certregister';
ALTER TYPE job_type ADD VALUE 'eudamed.sweep';
