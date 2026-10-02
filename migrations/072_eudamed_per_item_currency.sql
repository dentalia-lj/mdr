-- 072: the EUDAMED declaration state, per item (ruling 2026-10-02, the
-- per-item currency work, spec docs/superpowers/specs/2026-09-15-per-item-
-- currency-design.md).
--
-- `eudamed_article_status` (046, restated by 068) decides "do we hold a
-- declaration for this article" inline, and until now it asked the document's
-- own status: `idoc.status = 'production' AND d.status = 'production'`. Since
-- 071 that status is not currency. A DoC the old document-wide rule marked
-- `superseded` still answers for every article nothing newer of its type
-- covers (the server's doc 29), and read by status it was "missing a
-- declaration" in `eudamed_gap_summary`, in `eudamed_declaration_gap`, and in
-- the gap letter to the supplier (`email_request.gap_groups_for_manufacturer`).
--
-- The one change: the document condition becomes `d.status IN ('production',
-- 'superseded')`, the published set 071's views rank. The LINK condition is
-- untouched. That equals "the article has a current DoC" without reading the
-- ranked view: any published DoC on a production link means the article has
-- at least one current DoC, because the rank always puts some published DoC
-- first (a tie keeps them all current). A history DoC therefore adds no
-- coverage beyond the current one beside it.
--
-- Correction to 071's header, which said "every dependent view (046)" when
-- explaining why its views keep their columns: nothing in 046 reads
-- `item_document_production` or `item_document_history`. 046's views join
-- `item_document` and `document` directly, which is why they kept reading
-- `document.status` after 071 and why this migration exists. 071 is applied
-- and is not edited.
--
-- Everything else is 068's definition verbatim (same columns, same order), so
-- CREATE OR REPLACE keeps `eudamed_declaration_gap`, `eudamed_gap_summary` and
-- 046's grant to `dentalia_api`; 068 granted nothing of its own.
CREATE OR REPLACE VIEW eudamed_article_status AS
WITH item_canonical AS (
  SELECT DISTINCT im.item_ref, im.mfr_ref, g.canonical_manufacturer AS canonical_name
    FROM item_mirror im
    JOIN item_group_member gm ON gm.item_ref = im.item_ref
    JOIN item_group g         ON g.group_id = gm.group_id
   WHERE im.md_flag IS TRUE
     AND EXISTS (SELECT 1 FROM trusted_manufacturer_srn s
                  WHERE s.canonical_name = g.canonical_manufacturer)
),
-- The authored rule, read from the playbook body the seed importer keeps in
-- `manufacturer.body` (migration 050). Absent for every manufacturer but KOMET,
-- and an absent rule means the exact comparison this view has always used.
normalize_rule AS (
  SELECT canonical_name, body->'ref_normalize'->>'strategy' AS strategy
    FROM manufacturer
),
eudamed_match AS (
  SELECT ic.item_ref, em.basic_udi_di, max(em.trade_name) AS trade_name
    FROM item_canonical ic
    LEFT JOIN normalize_rule nr ON nr.canonical_name = ic.canonical_name
    JOIN trusted_manufacturer_srn s ON s.canonical_name = ic.canonical_name
    JOIN eudamed_mirror em          ON em.manufacturer_srn = s.srn
                                    AND (
      CASE WHEN nr.strategy = 'reorder-shank-figure-size'
           THEN eudamed_ref_key(em.reference)
                = fold_bur_code(COALESCE(ic.mfr_ref, ic.item_ref))
           ELSE em.reference = COALESCE(ic.mfr_ref, ic.item_ref)
      END)
   GROUP BY ic.item_ref, em.basic_udi_di
),
doc_link AS (
  SELECT ic.item_ref,
         COALESCE(bool_or(d.doc_id IS NOT NULL AND idoc.status = 'production'
                                                 AND d.status IN ('production',
                                                                    'superseded')),
                  FALSE) AS has_production,
         COALESCE(bool_or(d.doc_id IS NOT NULL AND idoc.status = 'staged'),
                  FALSE) AS has_staged
    FROM item_canonical ic
    LEFT JOIN item_document idoc ON idoc.item_ref = ic.item_ref
                                 AND idoc.status <> 'retracted'
    LEFT JOIN document d         ON d.doc_id = idoc.doc_id AND d.type = 'DoC'
   GROUP BY ic.item_ref
)
SELECT ic.item_ref,
       ic.canonical_name,
       em.basic_udi_di,
       em.trade_name,
       (em.item_ref IS NOT NULL) AS in_eudamed,
       dl.has_production,
       dl.has_staged
  FROM item_canonical ic
  LEFT JOIN eudamed_match em ON em.item_ref = ic.item_ref
  JOIN doc_link dl           ON dl.item_ref = ic.item_ref;
