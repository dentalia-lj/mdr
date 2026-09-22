-- 066_srn_text_mined.sql
-- A fourth way to discover an SRN: read it off a document we already hold.
--
-- `manufacturer_srn` is filled only from the EUDAMED certificate register
-- ('register-exact' / 'register-fuzzy') and from the device fallback
-- ('article-probe'). A manufacturer with NO certificates never appears in the
-- register pull, and the article probe that exists for exactly that case is
-- unreachable -- `probe_srn` runs only inside a sweep, and the release route
-- refuses with 400 on the same empty-SRN condition. GC EUROPE sits in that
-- deadlock with 356 device articles and zero certificates.
--
-- It need not: measured 2026-09-09, GC EUROPE's SRN `BE-MF-000001608` appears
-- in 126 documents already in the archive, and 6 of the 340 manufacturers
-- carrying item groups with no trusted SRN have theirs sitting in text we have
-- already parsed. No network call, no EUDAMED round trip.
--
-- `status` is deliberately NOT widened and mined rows are written 'pending':
-- the pattern also matches an importer's or a notified body's SRN quoted
-- inside somebody else's document, and a wrong confirmation makes the WRONG
-- manufacturer's whole catalogue sweepable. Every mined row waits for the
-- /manufacturers/srn-queue decision that already exists.

ALTER TABLE manufacturer_srn DROP CONSTRAINT manufacturer_srn_discovered_via_check;

ALTER TABLE manufacturer_srn
  ADD CONSTRAINT manufacturer_srn_discovered_via_check
  CHECK (discovered_via IN ('register-exact','register-fuzzy','article-probe','text-mined'));

-- Which document the SRN was read out of. The audit twin of `probe_ref`, which
-- records the ARTICLE that bootstrapped an article-probe row: a reviewer
-- deciding a mined candidate has to be able to open the page it came from,
-- otherwise they are confirming a bare string. Nullable because the three
-- older discovery paths have no source document.
ALTER TABLE manufacturer_srn
  ADD COLUMN source_doc_id bigint REFERENCES document (doc_id);

COMMENT ON COLUMN manufacturer_srn.source_doc_id IS
  'Document the SRN was text-mined from; NULL for register and probe rows.';
