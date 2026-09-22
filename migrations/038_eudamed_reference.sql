-- EUDAMED's catalogue number, and the only reason to call EUDAMED at all.
--
-- Every device record on `GET /devices/udiDiData` carries `reference` --
-- MDR Annex VI Part B's "Reference/Catalogue number" -- and it was populated
-- on 100% of the 16.502 records swept on 2026-08-20
-- (docs/2026-08-20-eudamed-reachability-research.md §1). That field is what
-- turns a Basic UDI-DI into the article numbers a document actually covers,
-- which is the article-level evidence 65,8% of our covered devices lack.
--
-- The mirror already held udi_di / basic_udi_di / device_name /
-- manufacturer_srn / cert_refs (migration 004). `cert_refs` stays empty:
-- the same research measured that EUDAMED returns no document URLs, so
-- DISCOVER's eudamed rung -- which reads cert_refs -- keeps missing. Nothing
-- here changes that, and nothing here writes an item_document link.
ALTER TABLE eudamed_mirror ADD COLUMN IF NOT EXISTS reference text;

COMMENT ON COLUMN eudamed_mirror.reference IS
  'EUDAMED Reference/Catalogue number for this UDI-DI (Annex VI Part B). '
  'Populated on 100% of records measured 2026-08-20. Not a link basis by '
  'itself: ref-eudamed is capped at staged (Denis, 2026-08-25) and no '
  'handler writes item_document from this column yet.';

GRANT SELECT ON eudamed_mirror TO dentalia_api;
