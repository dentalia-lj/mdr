-- `ref-catalogue`: a link formed by matching an extracted REF against the whole
-- catalogue, WITHOUT a manufacturer to scope the lookup (backfill/corpus
-- documents that carry no usable identity — see [validate-ref-catalogue] in
-- app/handlers/validate.py).
--
-- Why it must be barred from `production` (C5 / invariant 3):
--
-- Our collision measurement covers only what Dentalia stocks. Measured
-- 2026-08-11 over 5.511 distinct catalogue REFs, 24 collide across
-- manufacturers (12 of them prose already flagged `mfr_ref_prose`, 12 genuine
-- article numbers). That 0,22% is the rate WITHIN this catalogue; it says
-- nothing about a manufacturer whose products Dentalia does not carry using the
-- same article number. Catalogue-observed uniqueness is not global uniqueness,
-- and absence of evidence is not evidence of absence.
--
-- So an unscoped REF match is strong enough to PROPOSE a link and never strong
-- enough to WRITE one unreviewed. Joining name-family and fetch-context in this
-- CHECK is what makes that structural rather than a convention a future handler
-- could forget: the guards in validate.py can be edited, this cannot be
-- bypassed by any writer, including a hand-written UPDATE.
--
-- The scoped path is unaffected. A document that resolves its manufacturer and
-- then matches a REF still links at `ref-list`, still reaches production. This
-- basis exists precisely for the case where identity is unknown.

ALTER TABLE item_document DROP CONSTRAINT item_document_trusted_basis_ck;

ALTER TABLE item_document ADD CONSTRAINT item_document_trusted_basis_ck
  CHECK (NOT (status = 'production'
              AND match_basis IN ('name-family', 'fetch-context', 'ref-catalogue')));

COMMENT ON CONSTRAINT item_document_trusted_basis_ck ON item_document IS
  'C5/inv. 3: link-level cap. name-family, fetch-context and ref-catalogue are '
  'never production — each identifies an item without a verified '
  '(canonical_manufacturer, mfr_ref) pair or Basic UDI-DI.';
