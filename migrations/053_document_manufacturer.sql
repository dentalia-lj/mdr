-- 053_document_manufacturer.sql
-- The edge that never existed: a document's CONFIRMED manufacturer.
--
-- Until now a document reached a manufacturer only by fanning out to catalogue
-- items -- group-scope through `item_document` -> `item_mirror` ->
-- `item_group_member` -> `item_group.canonical_manufacturer`, manufacturer-scope
-- through `mfr-scope` links -> `item_mirror.manufacturer_raw` ->
-- `manufacturer_alias`. Both run through `item_document`, so a document with no
-- item links reached no manufacturer at all.
--
-- Measured on the live registry 2026-08-31: 540 documents in that state, of
-- which 443 are `filed`. That is EVERY filed document, by definition -- C15
-- files exactly what we stock nothing for, and the manufacturer path runs
-- through what we stock. 531 of the 540 carry a `manufacturer` evidence row, so
-- the value was read and then had nowhere to live.
--
-- NOT `evidence.manufacturer` (PRD §201), which is "the legal entity that
-- manufactures the devices, as PRINTED" -- an extracted value with a confidence
-- and a verbatim. This column is what a human or C16 CONFIRMED. A reading and a
-- decision are different things and conflating them is the one way this goes
-- wrong; the evidence row remains the provenance FOR the decision.
--
-- WHY KEYED BY NAME RATHER THAN A SURROGATE ID
--
-- Every other manufacturer attribute in this schema keys by name:
-- `item_group.canonical_manufacturer`, `manufacturer_srn.canonical_name`,
-- `eudamed_sweep_state.canonical_name`. Migration 052 built this exact cascade
-- for that shape and its argument transfers verbatim: "A rename does not
-- invalidate an SRN attribution or a sweep schedule; it renames the thing they
-- describe." A surrogate id would be the only manufacturer reference in the
-- schema keyed that way, adding a join to every read and a second convention to
-- the codebase, to solve a problem 052 already solved.
--
-- ON DELETE stays NO ACTION, also per 052: deleting a manufacturer that
-- something still references SHOULD be refused, never silently cascaded.
--
-- Nullable and additive on purpose. No backfill is required for this migration
-- to be correct, every existing reader keeps working untouched, and null means
-- UNDECIDED -- never "this document has no manufacturer".
--
-- One consequence, handled in the same slice rather than discovered later:
-- `regroup.rename_impact` reports the blast radius of a rename before the UI
-- writes it, and this cascade adds a set of rows it could not previously see.
-- It gains a document count; silently understating that radius is the exact
-- failure 052 was written to close.

ALTER TABLE document
  ADD COLUMN canonical_manufacturer text
    REFERENCES manufacturer(canonical_name) ON UPDATE CASCADE;

-- Partial: null means undecided, and nothing ever looks up "the documents with
-- no manufacturer" by this column. `manufacturer_documents` filters on a NAME,
-- and that query previously reached the same answer by seq-scanning
-- `item_mirror` -- 15.936 rows removed to find 22, measured 2026-08-31, on a
-- catalogue headed for ~100k items.
CREATE INDEX document_manufacturer_idx
  ON document (canonical_manufacturer)
  WHERE canonical_manufacturer IS NOT NULL;

COMMENT ON COLUMN document.canonical_manufacturer IS
  'The CONFIRMED manufacturer (human bind or C16 machine bind), not the printed '
  'name in evidence. Null means undecided, never "none".';
