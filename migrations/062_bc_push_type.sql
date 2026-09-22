-- 062_bc_push_type.sql
-- `bc.push` joins the closed job_type enum (invariant 7: a new tag is a PRD
-- change plus a migration, never a string). The stage row is PRD §0.
--
-- ALTER TYPE ... ADD VALUE cannot be used in the same transaction that also
-- USES the new value, and run_migrations gives each file one transaction
-- (app/db.py). This file therefore adds the value and nothing else; 063 builds
-- the ledger. Migrations 013 and 054 hit the same constraint and say so too.

ALTER TYPE job_type ADD VALUE 'bc.push';
