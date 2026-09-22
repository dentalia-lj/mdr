-- 052_manufacturer_rename_cascade.sql
-- A merge artefact, and the only one of this merge that is a real defect.
--
-- Two branches touched `manufacturer` without seeing each other. S2.3 stage 2
-- made `manufacturer.canonical_name` an FK TARGET twice --
-- `manufacturer_srn.canonical_name` (042) and `eudamed_sweep_state.canonical_name`
-- (044) -- and neither declared `ON UPDATE`, so both default to NO ACTION.
-- Meanwhile the playbooks-into-db branch built the D1 rename guardrail, whose
-- confirmed step is literally `UPDATE manufacturer SET canonical_name = %s`
-- (`web/registry.py::rename_manufacturer`).
--
-- Apart, each is correct. Together, renaming a manufacturer that has ever been
-- attributed an SRN or marked sweep-due raises
-- `ForeignKeyViolation: update or delete on table "manufacturer" violates
--  foreign key constraint "manufacturer_srn_canonical_name_fkey"`
-- -- a 500 out of a route whose whole point is to refuse cleanly at 422 after
-- pricing the blast radius. Neither branch's tests could catch it: the rename
-- did not exist on one side and these tables did not exist on the other.
--
-- WHY CASCADE RATHER THAN UPDATING THE CHILDREN IN THE ROUTE
--
-- `dentalia_api` does hold INSERT/UPDATE on both children, so the route COULD
-- write them itself -- but not in the right order. The parent UPDATE is checked
-- at statement end, so the children are already dangling by the time the route
-- could reach them, and fixing that needs the constraints DEFERRABLE, which is
-- a schema change of the same size as this one and leaves the same hole open
-- for the CLI and for any future writer.
--
-- The cascade is also what these rows MEAN. Both children are keyed by the
-- canonical name rather than by `manufacturer.id` -- they are attributes of the
-- named entity, not independent records that happen to cite it. A rename does
-- not invalidate an SRN attribution or a sweep schedule; it renames the thing
-- they describe. `ON DELETE` is deliberately left at NO ACTION: deleting a
-- manufacturer that holds an SRN or a sweep state SHOULD still be refused.
--
-- The rename remains a confirmed two-step action that reports what it cannot
-- fix (`item_group.canonical_manufacturer` is effectively write-once because
-- RESOLVE short-circuits on `_existing_link`, and the web is SELECT-only on
-- `item_group` / `manufacturer_alias` -- invariant 1). This migration widens
-- what the rename repairs by exactly two tables, both of which it owns.

ALTER TABLE manufacturer_srn
  DROP CONSTRAINT manufacturer_srn_canonical_name_fkey,
  ADD  CONSTRAINT manufacturer_srn_canonical_name_fkey
       FOREIGN KEY (canonical_name) REFERENCES manufacturer(canonical_name)
       ON UPDATE CASCADE;

ALTER TABLE eudamed_sweep_state
  DROP CONSTRAINT eudamed_sweep_state_canonical_name_fkey,
  ADD  CONSTRAINT eudamed_sweep_state_canonical_name_fkey
       FOREIGN KEY (canonical_name) REFERENCES manufacturer(canonical_name)
       ON UPDATE CASCADE;
