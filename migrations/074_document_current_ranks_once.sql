-- 074: `document_current` ranks the registry once per query (2026-11-12).
--
-- Same set as 072, different plan. 072 wrote the first half as a correlated
-- `EXISTS (... item_document_production p WHERE p.doc_id = d.doc_id)` under an
-- OR. An EXISTS under an OR cannot become a semi-join, and a `doc_id` filter
-- cannot be applied below the rank window, which is partitioned by item. So
-- whenever the planner ran it per row rather than hashed, every document
-- re-ranked every production link. Measured on the server, that was 1,959
-- re-rankings of 12.5k links (24.6M rows): the menu's expiring count took 28 s
-- and `/expiry` 35 s. The plan flips on estimated call counts, so the old form
-- was only as fast as the statistics happened to be.
--
-- As a UNION nothing refers to the outer row: the rank runs once and the
-- second half is an anti-join. Both halves yield `document.doc_id` (bigint,
-- never NULL: `item_document.doc_id` is part of its primary key and a foreign
-- key to `document`), and UNION removes the overlap, so the rows are 072's.
-- Measured on the server against 072's body, EXCEPT both ways: 0 and 0. Same
-- single column, so CREATE OR REPLACE keeps the grant to `dentalia_api`.
--
-- Guarded by tests/test_per_item_currency.py: no WindowAgg under a SubPlan in
-- the menu count, the /expiry board or the renewal letter.
CREATE OR REPLACE VIEW document_current AS
  SELECT doc_id FROM item_document_production
  UNION
  SELECT d.doc_id
    FROM document d
   WHERE d.status = 'production'
     AND NOT EXISTS (SELECT 1 FROM item_document l
                      WHERE l.doc_id = d.doc_id AND l.status = 'production');
