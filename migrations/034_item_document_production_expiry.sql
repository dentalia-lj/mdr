-- 034: item_document_production learns the effective expiry.
-- Closes [task4-cert-view]: the view (and therefore /api) served raw validity_to,
-- disagreeing with the UI pages that already follow document_effective_expiry (027).
-- Additive: existing columns keep their order and meaning; expires/expiry_basis appended.
CREATE OR REPLACE VIEW item_document_production AS
  SELECT id.*, d.type, d.regulation, d.validity_from, d.validity_to, d.archive_url,
         ee.expires       AS expires,
         ee.basis         AS expiry_basis
    FROM item_document id
    JOIN document d USING (doc_id)
    LEFT JOIN document_effective_expiry ee ON ee.doc_id = d.doc_id
   WHERE d.status = 'production' AND id.status = 'production';
