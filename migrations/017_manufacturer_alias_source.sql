-- 017_manufacturer_alias_source.sql
-- Provenance for `manufacturer_alias` rows (S1.8).
--
-- The table is a PROJECTION, not truth: three different things write it, and
-- until now a row could not say which.
--
--   self-seed      RESOLVE, on an alias miss (`_alias_lookup` inserts the raw
--                  value as its own canonical). The pre-existing behaviour, so
--                  it is the DEFAULT -- every row that exists before this
--                  migration got there that way.
--   vendor-master  derived from BC's manufacturer master by `playbooks sync`.
--   playbook       an authored `playbooks/{slug}.json` claim, which wins over
--                  the master for every code of its entity.
--
-- Why it is needed: a re-sync has to tell its OWN rows from RESOLVE's
-- self-seeds. A self-seeded row is `code -> code` (the literal string `001`),
-- which is exactly what the seed is there to replace; a row a human authored
-- is not something a later import should quietly overwrite.
--
-- Deliberately NOT an enum. The set is small and stable, and a check
-- constraint keeps the values honest without a migration to add one.

ALTER TABLE manufacturer_alias
  ADD COLUMN source text NOT NULL DEFAULT 'self-seed';

ALTER TABLE manufacturer_alias
  ADD CONSTRAINT manufacturer_alias_source_ck
  CHECK (source IN ('self-seed', 'vendor-master', 'playbook'));

COMMENT ON COLUMN manufacturer_alias.source IS
  'who wrote this row: self-seed (RESOLVE miss) | vendor-master | playbook';
