-- 050_playbook_body.sql
-- The playbook BODY becomes a column, and edits become an audit trail (spec:
-- docs/superpowers/specs/2026-08-26-playbooks-into-the-database-design.md,
-- slice 2).
--
-- 049 shredded the three fields that carry a cross-row invariant --
-- `bc_codes`, the canonical name, `aliases` -- into constrained tables, so
-- that `playbooks.validate()`'s three checks became primary keys and a foreign
-- key. This adds the other half: the thirteen fields that have no such
-- invariant and that nothing queries into, as one `jsonb`.
--
-- WHY JSONB RATHER THAN THIRTEEN MORE COLUMNS
--
-- `doc_sources`, `date_labels`, `type_markers`, `ref_normalize`, `companion`,
-- `coverage_map`, `exclude`, `match`, `ref_strategy_config` and the rest are
-- nested, optional, and consumed only by `playbooks._parse` -- which reads
-- them into a frozen dataclass and hands that to the handlers. No query
-- filters or joins on them, so columns would buy nothing and cost a migration
-- per authored key. The invariant-carrying three are NOT here; putting them
-- back in the blob would undo 049.
--
-- WHY A REVISION TABLE AND NOT JUST `updated_at`
--
-- Ruled by Denis 2026-08-26: the audit trail is required, and it is also the
-- revert mechanism. Two consequences the rest of the system already depends
-- on:
--
--   1. `extraction_attempt` has carried `(playbook_slug, playbook_rev)` since
--      031. Without the body at that rev stored somewhere, those two columns
--      record a version nobody can reconstruct -- "extracted under playbook 3"
--      is only useful if playbook 3 is still readable.
--   2. Revert is not a delete. It writes the old body FORWARD as a new
--      revision, so the history of a mistake and its undo both survive.
--
-- That second point is why the grant below is `SELECT, INSERT` with no UPDATE
-- and no DELETE. Verified 2026-08-26 against a throwaway database: with these
-- grants `dentalia_api` is REFUSED both. Append-only is enforced by the grant,
-- not by convention -- do not widen this when the revert UI is built in 3a.
--
-- WHY AN EPOCH TABLE
--
-- The loader caches its parsed playbooks and needs a cheap "has anything
-- changed" probe, because VALIDATE calls `for_manufacturer` once per group.
-- The file-backed loader used a directory signature (`scandir`). `max(
-- updated_at)` over `manufacturer` would be a sequential scan in that hot
-- loop; a single-row counter is an index-only read.
--
-- `FOR EACH STATEMENT`, not `FOR EACH ROW`: the seed writes hundreds of rows
-- in one statement and the epoch only has to CHANGE, not to count.

ALTER TABLE manufacturer ADD COLUMN body jsonb;

CREATE TABLE manufacturer_playbook_revision (
  manufacturer_id bigint      NOT NULL REFERENCES manufacturer,
  rev             int         NOT NULL,
  body            jsonb       NOT NULL,
  authored_at     timestamptz NOT NULL DEFAULT now(),
  authored_by     text,
  -- Required on a UI save (enforced in the route, not here: the seed writes
  -- rev 0 with a generated note and a CHECK would have to special-case it).
  note            text,
  PRIMARY KEY (manufacturer_id, rev)
);

CREATE TABLE playbook_epoch (
  one   boolean PRIMARY KEY DEFAULT true CHECK (one),
  epoch bigint  NOT NULL DEFAULT 0
);
INSERT INTO playbook_epoch (one, epoch) VALUES (true, 1);

CREATE FUNCTION bump_playbook_epoch() RETURNS trigger AS $$
BEGIN
  UPDATE playbook_epoch SET epoch = epoch + 1;
  RETURN NULL;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER manufacturer_epoch AFTER INSERT OR UPDATE OR DELETE
  ON manufacturer         FOR EACH STATEMENT EXECUTE FUNCTION bump_playbook_epoch();
CREATE TRIGGER manufacturer_bc_code_epoch AFTER INSERT OR UPDATE OR DELETE
  ON manufacturer_bc_code FOR EACH STATEMENT EXECUTE FUNCTION bump_playbook_epoch();
CREATE TRIGGER manufacturer_name_epoch AFTER INSERT OR UPDATE OR DELETE
  ON manufacturer_name    FOR EACH STATEMENT EXECUTE FUNCTION bump_playbook_epoch();

-- No DELETE on the revision table, deliberately. See above.
GRANT SELECT, INSERT ON manufacturer_playbook_revision TO dentalia_api;
-- UPDATE on the epoch is needed because the trigger runs as the writing role.
GRANT SELECT, UPDATE ON playbook_epoch TO dentalia_api;
