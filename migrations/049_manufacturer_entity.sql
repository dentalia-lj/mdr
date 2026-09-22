-- 049_manufacturer_entity.sql
-- The manufacturer ENTITY becomes a row (spec: docs/superpowers/specs/
-- 2026-08-26-playbooks-into-the-database-design.md, slice 1).
--
-- `manufacturer` was declared in 006 as a Phase 2 table and had no writer for
-- most of its life: 0 rows when this work started, which is why
-- `discover._contact_known` and `email_request._contacts` both silently
-- returned empty and every renewal draft has been unaddressed.
--
-- 042_eudamed_manufacturer.sql (the eudamed-enhancement branch, applied to the
-- live DB 2026-08-26 while this was being written) got there first and seeded
-- the table from `SELECT DISTINCT canonical_name FROM manufacturer_alias` --
-- 384 rows. The two derivations were diffed the same day and **agree on all
-- 383 real names**; the only difference is the empty string `''`, which the
-- alias projection carries and this one deliberately does not (see the seed).
-- So this migration adds STRUCTURE to rows that now already exist rather than
-- creating them, and the seed's insert path is a no-op on a live database that
-- has taken 042.
--
-- Note 042 also makes `manufacturer.canonical_name` an FK target
-- (`manufacturer_srn.canonical_name`). Renaming a canonical name is therefore
-- no longer a local edit -- see the rename guardrail in the spec (§4.4 D1),
-- which now has a second reason to be a confirmed action rather than a form
-- field.
--
-- The playbook body itself lands in 050.
--
-- WHAT AN "ENTITY" IS, AND WHY IT NEEDS A ROW
--
-- `reconcile._entities` already computes it: union-find over BC codes, joined by
-- shared vendor-master name and by co-claim from one playbook. `001`, `005` and
-- `275` are all IVOCLAR VIVADENT. Today that grouping is computed at
-- `dentalia playbooks sync` time and thrown away, so the same question gets
-- re-derived by the UI (`web/registry.annotate_playbooks`) with a different rule.
-- Measured 2026-08-26 the two rules agree exactly (383 shared names, 0
-- disagreements) -- but agreeing today is not the same as being one rule, and
-- these tables make it one.
--
-- `vendor_master` IS NOT WRITTEN BY THIS PATH
--
-- It stays what 016 made it: a mirror of BC's manufacturer master, written only
-- by `dentalia vendor-master import`. `manufacturer_bc_code` LINKS to it by
-- foreign key. That is the whole point of the split -- authored truth and the BC
-- mirror stay separate objects, and neither can quietly overwrite the other.
--
-- CONSTRAINTS ARE `playbooks.validate()`, MOVED INTO THE DATABASE
--
--   duplicate BC code   -> manufacturer_bc_code PRIMARY KEY
--   duplicate name      -> manufacturer_name PRIMARY KEY (casefolded)
--   code BC never issued-> FOREIGN KEY to vendor_master  (NO equivalent today)
--
-- The third has no check at all right now: a playbook may claim a code that does
-- not exist and nothing notices. All 39 codes the 33 current playbooks claim are
-- present in `vendor_master` and all are catalogue `LJ` (measured 2026-08-26),
-- so this FK grandfathers nothing.
--
-- This is the reason for shredding identity rather than storing the whole
-- playbook as one jsonb blob: it turns "authoring error discovered later by
-- RESOLVE, after items are already mis-grouped" into "save refused at 422".
-- Non-developers will be authoring these through the web UI, which is what makes
-- the difference matter.

ALTER TABLE manufacturer
  -- NULL for the ~350 entities with no playbook. Postgres UNIQUE permits many
  -- NULLs, which is exactly the behaviour wanted here.
  ADD COLUMN slug         text UNIQUE,
  -- Auto-incremented on every save from 044 onward. Recorded on
  -- `extraction_attempt.playbook_rev` (031) so an extraction stays defensible
  -- after the body changes.
  ADD COLUMN playbook_rev int NOT NULL DEFAULT 0,
  ADD COLUMN updated_at   timestamptz NOT NULL DEFAULT now(),
  -- The proxy-authenticated username (`web/app.py::_authenticated_user`, the
  -- same value `gate.apply` records as `decided_by`).
  ADD COLUMN updated_by   text;

COMMENT ON COLUMN manufacturer.playbook_ref IS
  'HISTORICAL: named a path in a playbook git repo, back when the files were '
  'truth and this table was a pointer. Never written. The body lives in '
  'manufacturer.body from migration 044. Left in place rather than dropped '
  'because DROP COLUMN is the one part of this change that is not trivially '
  'reversible, and an unwritten column costs nothing.';

-- written by: `dentalia manufacturers seed` (CLI) · read by: app/playbooks.py
CREATE TABLE manufacturer_bc_code (
  code_source     text   NOT NULL,
  code            text   NOT NULL,
  manufacturer_id bigint NOT NULL REFERENCES manufacturer,
  -- Same vocabulary as `manufacturer_alias.source` (017), deliberately: it
  -- answers the same question. 'playbook' = a human named this code.
  -- 'vendor-master' = the entity union-find added it because another code of the
  -- same entity was claimed. A BC refresh may re-point the latter, never the
  -- former.
  source          text   NOT NULL CHECK (source IN ('vendor-master','playbook')),
  -- One code, one manufacturer. This IS validate()'s duplicate-code check.
  PRIMARY KEY (code_source, code),
  -- The LINK. `vendor_master` is never written from here.
  FOREIGN KEY (code_source, code) REFERENCES vendor_master
);

-- Every string that should resolve to a manufacturer -- its canonical name and
-- its authored aliases -- in ONE namespace, which is what makes the PK equal to
-- validate()'s name check.
--
-- Keyed on the CASEFOLDED name because `playbooks.for_manufacturer` has always
-- compared case-insensitively, while `manufacturer_alias.raw_name` is a
-- case-sensitive text PK. That difference is live today: 3 item_group rows carry
-- '3SHAPE MEDICAL A/S' while the alias row spells it '3Shape Medical A/S', so
-- the alias cannot rescue them.
--
-- written by: `dentalia manufacturers seed` (CLI) · read by: app/playbooks.py
CREATE TABLE manufacturer_name (
  name_folded     text   PRIMARY KEY,
  name            text   NOT NULL,          -- as authored, for display
  manufacturer_id bigint NOT NULL REFERENCES manufacturer,
  kind            text   NOT NULL CHECK (kind IN ('canonical','alias'))
);

CREATE INDEX manufacturer_bc_code_mfr_idx ON manufacturer_bc_code (manufacturer_id);
CREATE INDEX manufacturer_name_mfr_idx    ON manufacturer_name (manufacturer_id);

-- 007's blanket `GRANT SELECT ON ALL TABLES` covered only the tables existing
-- then, so grant explicitly -- same posture as 016.
--
-- INSERT + UPDATE, never DELETE: the web UI authors and corrects manufacturers,
-- it does not remove them. Invariant 1 is untouched -- `dentalia_api` still has
-- no write grant on document / item_document / evidence, and these three tables
-- are none of those. Precedent for a narrow producer-side write grant:
-- `job` (007), `upload_inbox` (014), `email_draft` (029).
GRANT SELECT, INSERT, UPDATE ON manufacturer, manufacturer_bc_code,
      manufacturer_name TO dentalia_api;
GRANT USAGE ON SEQUENCE manufacturer_id_seq TO dentalia_api;
