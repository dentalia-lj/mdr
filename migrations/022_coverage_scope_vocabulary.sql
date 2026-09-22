-- `coverage_scope` is a closed vocabulary of TWO values: group | manufacturer.
--
-- `item` is retired (Denis's ruling, 2026-08-12). 005_registry.sql declared the
-- column as bare `text` with the vocabulary only in a trailing comment, so
-- nothing stopped a third value being written -- and something did. Measured on
-- the first real corpus run: 37 of 57 registered documents carried `item`, a
-- value the committed ground truth never assigns (tests/fixtures/
-- corpus_manifest.py: 20 group, 25 manufacturer, 0 item).
--
-- Why `item` cannot be produced honestly:
--
--   * It has no deterministic signal. A DoC listing one article and a DoC
--     listing forty are structurally the same document, and the regulator
--     explicitly permits either -- "Multiple Basic UDI-DI can be in one DoC"
--     (EC UDI helpdesk). Nothing in the document says which it is.
--   * Asking a model instead produced contradictions on identical input: two
--     42-REF GC DoCs came back one `item` and one `group`, both from T1, both
--     at confidence 0.95.
--   * "Covers exactly one article" is `len(ref_list) = 1`. It was always
--     answerable from data we already hold, and never needed a scope value.
--
-- It was retired at the producer on 2026-08-12 (`derive_coverage_scope` emits `group`
-- for a DoC with a parsed REF list, `manufacturer` otherwise). This makes that
-- structural rather than a property of one function: a hand-written UPDATE, a
-- future handler, or a re-introduced model answer cannot put `item` back.
--
-- No data migration accompanies this. The registry was cleared 2026-08-12
-- before the corpus re-run, so no row holds `item`; the constraint is validated
-- against an empty table and would fail loudly if that were not so.

ALTER TABLE document
  ADD CONSTRAINT document_coverage_scope_vocabulary
  CHECK (coverage_scope IN ('group', 'manufacturer'));

COMMENT ON COLUMN document.coverage_scope IS
  'group | manufacturer (closed, document_coverage_scope_vocabulary). '
  '"item" retired 2026-08-12: no deterministic signal distinguishes a '
  'single-article DoC from a multi-article one, and len(ref_list) answers '
  'that question directly.';
