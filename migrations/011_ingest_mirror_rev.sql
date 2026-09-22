-- 011_ingest_mirror_rev.sql
-- S1.1 INGEST. Backs item_mirror.mirror_rev (migration 002) with a global
-- sequence so every change gets a fresh, monotonic, race-free revision number.
--
-- Why a sequence and not max(mirror_rev)+1: ingest.run diffs an export against
-- the mirror and upserts changed rows. mirror_rev is the version token in the
-- resolve.group dedupe key (resolve:{item_ref}:{mirror_rev}, PRD §1/§2) — it
-- MUST advance on every change so a re-changed item enqueues a fresh resolve
-- job instead of colliding with a terminal one. A read-modify-write of
-- max(rev)+1 would race under concurrent ingest; nextval() is atomic and needs
-- no read. The sequence is global (not per-item): revs are only ever compared
-- for equality inside a dedupe key, never ordered across items, so a shared
-- counter is correct and simplest.
--
-- 002 declared `mirror_rev bigint NOT NULL` with no default. An UPDATE does not
-- re-fire a column DEFAULT, so the handler always assigns nextval() explicitly
-- on both INSERT and the change-path UPDATE; the DEFAULT here is a safety net
-- so a bare INSERT that forgets rev still gets a valid, unique value rather
-- than failing NOT NULL.

CREATE SEQUENCE IF NOT EXISTS item_mirror_rev_seq AS bigint;

ALTER TABLE item_mirror
  ALTER COLUMN mirror_rev SET DEFAULT nextval('item_mirror_rev_seq');
