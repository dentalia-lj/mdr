-- 071: per-item currency (spec docs/superpowers/specs/2026-09-15-per-item-
-- currency-design.md; rulings 2026-09-14, 2026-09-15, 2026-10-02).
--
-- Which published document answers for an item is decided HERE, per item and
-- per document type, every time it is asked. Nothing stores it. The old rule
-- retired a whole document when a newer one shared a catalogue group with it,
-- which is the wrong subject 68% of the time (spec §1).
--
-- The pick: among documents with status production OR superseded (the old
-- flag is not consulted, so a document it retired wrongly answers again) on a
-- production link, MDR ranks above everything else, then the newest
-- validity_from; ties are all current; undated ranks below dated.
--
-- `item_document_history` keeps 070's name, columns and filter and gains
-- `current`; `item_document_production` keeps 034's columns and becomes the
-- current rows of it. Both are CREATE OR REPLACE with the existing columns in
-- their existing order, so every dependent view (046) and every grant stays.
CREATE OR REPLACE VIEW item_document_history AS
  SELECT id.item_ref, id.doc_id, id.udi, id.match_basis, id.status,
         d.type, d.regulation, d.validity_from, d.validity_to, d.archive_url,
         d.status         AS doc_status,
         d.superseded_by  AS superseded_by,
         ee.expires       AS expires,
         ee.basis         AS expiry_basis,
         rank() OVER (PARTITION BY id.item_ref, d.type
                      ORDER BY (d.regulation = 'MDR') DESC,
                               d.validity_from DESC NULLS LAST) = 1 AS current
    FROM item_document id
    JOIN document d USING (doc_id)
    LEFT JOIN document_effective_expiry ee ON ee.doc_id = d.doc_id
   WHERE d.status IN ('production', 'superseded') AND id.status = 'production';

CREATE OR REPLACE VIEW item_document_production AS
  SELECT item_ref, doc_id, udi, match_basis, status, type, regulation,
         validity_from, validity_to, archive_url, expires, expiry_basis
    FROM item_document_history
   WHERE current;

-- A document "answers for something" when it is current for at least one item,
-- or when it is production and carries no production link at all (a binding
-- whose manufacturer has no device items: nothing can outrank it). The expiry
-- board, the weekly report and the renewal letters read this instead of
-- `document.status = 'production'`, so a document that is history everywhere
-- draws no renewal request.
CREATE VIEW document_current AS
  SELECT d.doc_id
    FROM document d
   WHERE EXISTS (SELECT 1 FROM item_document_production p WHERE p.doc_id = d.doc_id)
      OR (d.status = 'production'
          AND NOT EXISTS (SELECT 1 FROM item_document l
                           WHERE l.doc_id = d.doc_id AND l.status = 'production'));

GRANT SELECT ON document_current TO dentalia_api;
