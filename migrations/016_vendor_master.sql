-- 016_vendor_master.sql
-- BC's vendor/manufacturer master, delivered by Dentalia BC/IT 2026-08-05
-- (`imports/Proizvajalci.xlsx`, 390 rows: `Šifra` -> `Ime`). Closes the ask in
-- `dentalia-manufacturer-code-sweep.md` §Rec 1: the LJ item export carries a
-- manufacturer CODE and no name, so before this the only code->name mapping was
-- the sweep's statistical inference (~32 codes, 42.5% of the catalogue). The
-- master resolves all 381 LJ codes.
--
-- MIRROR, not truth-by-authorship: same posture as `item_mirror`. Written only
-- by `dentalia vendor-master import`, never hand-edited, re-importable on every
-- BC refresh. Nothing reads it at runtime — `manufacturer_alias` stays the
-- projection that RESOLVE and GATE use, and `playbooks sync` derives that
-- projection from this table plus authored playbooks. This migration therefore
-- has no consumer and is undoable with a DROP.
--
-- `code_source` is deliberately NOT named `catalogue`. It records which BC
-- instance issued the code, which is not the same question as which warehouse
-- an item sits in. Gap G11 (client, pre-S2.5) asks whether Ljubljana and Zagreb
-- share a system; Denis's answer is that they are intended to converge into ONE
-- BC. If that holds, a Zagreb-warehouse item living in the Ljubljana BC keeps
-- `code_source='LJ'` and resolves correctly. Keying this on the warehouse tag
-- instead is precisely the fragmentation that got the alias-namespacing
-- migration cut on this branch's predecessor. Values match the playbook
-- `bc_codes[].catalogue` strings so the join needs no mapping.
--
-- `code` is text, not numeric: the master contains at least one non-numeric
-- Šifra (`CEFLA`).
--
-- `name` is nullable: one of the 390 delivered rows has no name.
--
-- The mapping is many-codes-to-one-manufacturer (`001`, `005`, `275` are all
-- IVOCLAR VIVADENT), which is why derivation must group codes by name into an
-- "entity" rather than resolving each code independently.

CREATE TABLE vendor_master (
  code_source  text NOT NULL DEFAULT 'LJ',
  code         text NOT NULL,
  name         text,
  import_batch text NOT NULL,        -- label of the import run that last wrote this row
  first_seen   timestamptz NOT NULL DEFAULT now(),
  last_seen    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (code_source, code)
);

-- A code present in an earlier import but absent from the current one keeps its
-- row with a stale `last_seen` rather than being deleted: a disappeared vendor
-- is a fact to report, not one to silently forget (and a delete would strand any
-- alias derived from it). The import diffs on this.
COMMENT ON COLUMN vendor_master.last_seen IS
  'import_batch timestamp of the most recent import that still listed this code';

-- New table, created after 007's blanket GRANT, so grant explicitly. SELECT
-- only: the web role is a producer and never writes the mirror.
GRANT SELECT ON vendor_master TO dentalia_api;
