-- Read-only. Migration 071's per-item pick computed inline against a registry
-- that does not have 071 yet, compared with today's views. Run with psql; it
-- creates nothing and writes nothing.
WITH hist AS (
  SELECT id.item_ref, id.doc_id, d.type, d.regulation, d.validity_from, d.status AS doc_status,
         rank() OVER (PARTITION BY id.item_ref, d.type
                      ORDER BY (d.regulation = 'MDR') DESC,
                               (CASE WHEN d.validity_from <= current_date THEN d.validity_from END) DESC NULLS LAST) = 1 AS current
    FROM item_document id JOIN document d USING (doc_id)
   WHERE d.status IN ('production','superseded') AND id.status = 'production'
), now_prod AS (SELECT item_ref, doc_id, type FROM item_document_production),
new_prod AS (SELECT item_ref, doc_id, type FROM hist WHERE current)
SELECT 'rows today' AS what, count(*) FROM now_prod
UNION ALL SELECT 'rows after', count(*) FROM new_prod
UNION ALL SELECT 'items with a DoC today', count(DISTINCT item_ref) FROM now_prod WHERE type='DoC'
UNION ALL SELECT 'items with a DoC after', count(DISTINCT item_ref) FROM new_prod WHERE type='DoC'
UNION ALL SELECT 'item/type pairs with >1 current today',
       count(*) FROM (SELECT item_ref, type FROM now_prod GROUP BY 1,2 HAVING count(*)>1) x
UNION ALL SELECT 'item/type pairs with >1 current after (ties)',
       count(*) FROM (SELECT item_ref, type FROM new_prod GROUP BY 1,2 HAVING count(*)>1) x
UNION ALL SELECT 'superseded docs current for some item after',
       count(DISTINCT doc_id) FROM hist WHERE current AND doc_status='superseded'
UNION ALL SELECT 'production docs current for no item after',
       count(*) FROM document d WHERE d.status='production'
        AND EXISTS (SELECT 1 FROM item_document l WHERE l.doc_id=d.doc_id AND l.status='production')
        AND NOT EXISTS (SELECT 1 FROM hist h WHERE h.doc_id=d.doc_id AND h.current);
