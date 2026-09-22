-- 033: item_class_check — BC's item class vs the class its production
-- documents state.
--
-- Read-only QA, and it stays that way. `item_mirror.product_class` is BC's, BC
-- is the source of truth for what class an item IS, and NOTHING in this
-- pipeline writes that column from a document (contract PRD §5, 2026-08-21).
-- This view exists so the two can be put beside each other and a person can
-- act: 11.693 of the mirrored items carry no class at all, and a held
-- declaration that states one is the cheapest evidence available for filling
-- that blank. It never feeds a write, and no handler reads it.
--
-- Why `doc_class` COALESCEs a column over a jsonb. `document.stated_class`
-- (032) is filled by GATE, and GATE only runs when a document is adjudicated.
-- The 768 documents already held were extracted before the field existed;
-- `app/repair_stated_class.py` backfills them by appending a new extract_rev
-- and deliberately emits NO `validate.doc`, because re-adjudicating 768 settled
-- dispositions to record a display value would risk far more than the value is
-- worth. So history carries the value in the attempt and current work carries
-- it in the column, and this view reads whichever exists — column first, since
-- that one has been through the gate.
--
-- LEFT JOIN on `latest`, not an inner join: the COALESCE above says the column
-- is the primary source and the attempt is the fallback, and an inner join
-- would quietly invert that by making the fallback mandatory. Every registry
-- document does have an attempt row today (768/768), so the two behave
-- identically now; the LEFT JOIN is what keeps that true if `extraction_attempt`
-- is ever pruned.
--
-- `agree-family` is not a fudge. Documents almost never print the class I
-- subclasses -- Is (sterile), Im (measuring), Ir (reusable surgical) -- 10 of
-- 768 measured. A declaration for an item BC calls `Ir` will say "Class I",
-- because that is what MDR Annex VIII calls it in the sentence that matters.
-- That is the document being less specific, not the two disagreeing, and
-- reporting it as a conflict would bury the real ones: BC holds 2.569 `Ir`
-- items against 69 plain `I`.
--
-- Production-only on BOTH sides, the same visibility rule as
-- `item_document_production`: a staged document or a staged link asserts
-- nothing about an item yet, so it must not raise a question a reviewer would
-- then have to chase.
CREATE VIEW item_class_check AS
WITH latest AS (
  SELECT DISTINCT ON (content_hash) content_hash,
         fields->'stated_class'->>'value' AS attempt_class
    FROM extraction_attempt
   ORDER BY content_hash, extract_rev DESC
)
SELECT id.item_ref,
       NULLIF(im.product_class, '')                    AS bc_class,
       COALESCE(d.stated_class, l.attempt_class)       AS doc_class,
       d.doc_id, d.type,
       CASE
         WHEN NULLIF(im.product_class, '') IS NULL     THEN 'fillable'
         WHEN im.product_class = COALESCE(d.stated_class, l.attempt_class)
                                                        THEN 'agree'
         WHEN im.product_class IN ('Is','Im','Ir')
              AND COALESCE(d.stated_class, l.attempt_class) = 'I'
                                                        THEN 'agree-family'
         ELSE 'conflict'
       END AS verdict
  FROM item_document id
  JOIN document d USING (doc_id)
  LEFT JOIN latest l ON l.content_hash = d.content_hash
  JOIN item_mirror im ON im.item_ref = id.item_ref
 WHERE d.status = 'production' AND id.status = 'production'
   AND COALESCE(d.stated_class, l.attempt_class) IS NOT NULL;

COMMENT ON VIEW item_class_check IS
  'BC item class vs the class its production documents state. Read-only QA: '
  'BC stays the source of truth and this view never feeds a write. verdict is '
  'fillable (BC blank), agree, agree-family (BC Is/Im/Ir vs a document''s less '
  'specific I), or conflict.';

-- Created after 007's blanket GRANT, so grant explicitly (precedent 010/027).
-- SELECT only: the web process is a producer, never a registry writer
-- (invariant 1), and /data-quality is the one page that reads this.
GRANT SELECT ON item_class_check TO dentalia_api;
