-- KOMET's articles could never match their own EUDAMED entry, and the reason
-- was not the one the finding named.
--
-- F32 said the view compares raw strings while KOMET's playbook already
-- declares the reorder rule VALIDATE uses, so applying that rule here would fix
-- it. Measured 2026-09-15 over KOMET's 258 flagged device items and its 1.810
-- mirrored EUDAMED rows: applying the fold to both sides moves the match count
-- **from 0 to 0**. The order of the segments is not the difference.
--
-- EUDAMED prints a FOURTH segment our catalogue does not -- `8885.314.012.K3`,
-- `856P.314.018.R0`, `NS2314.513.M1` -- on 1.586 of those 1.810 rows. It is a
-- packaging or variant code that lives on the manufacturer's registration and
-- not on the Dentalia article. Fold the order AND drop that suffix from the
-- EUDAMED side and 108 of the 258 items match.
--
-- **Why dropping it is safe here, measured rather than argued:** zero trimmed
-- keys span more than one `basic_udi_di` for KOMET, so the trim merges no two
-- devices into one. And this view WRITES NOTHING -- it feeds the gap views and
-- the example article numbers in a gap letter (`email_request.py:446`); nothing
-- creates an `item_document` link from it. The worst a wrong match can do is
-- put a wrong row in a list a person reads, never a false production link.
-- Invariant 3 is not in play.
--
-- **Only the EUDAMED side is trimmed.** Our catalogue does not print the
-- suffix, so trimming our refs would strip something else -- a real part of a
-- Dentalia article number -- and invent matches rather than find them.
--
-- **Only for a manufacturer whose playbook declares the strategy.** KOMET is
-- the only one (client, 2026-08-18: "ne -- komet je specifičen"), and everyone
-- else keeps today's exact comparison, byte for byte.

-- The fold, mirroring `app/handlers/validate.py::_fold_bur_code`. Two
-- spellings of one ISO 6360 bur code:
--   spaced   '314 H1 006'  (Dentalia: SHANK FIGURE SIZE)
--   dotted   'H1.314.006'  (Komet:    FIGURE.SHANK.SIZE)
-- both fold to FIGURE.SHANK.SIZE with the size zero-padded to three digits.
--
-- Anything that is not a bur code comes back untouched. 'LS SFQ2008' and
-- '000 SFD7 1' are real KOMET catalogue entries that are not bur codes, and
-- folding must never invent a shape they do not have. The spaced pattern is
-- deliberately strict for the same reason the Python is: seven KOMET items
-- carry a Dentalia packaging suffix ('104 H219A 023-1') and two of those would
-- otherwise fold onto an unsuffixed sibling's key.
CREATE OR REPLACE FUNCTION fold_bur_code(ref text) RETURNS text AS $$
  SELECT CASE
    WHEN ref IS NULL THEN NULL
    WHEN btrim(ref) ~ '^[0-9]{3}[[:space:]]+[A-Za-z0-9]{1,10}[[:space:]]+[0-9]{1,3}$'
      THEN upper(regexp_replace(btrim(ref),
             '^([0-9]{3})[[:space:]]+([A-Za-z0-9]{1,10})[[:space:]]+([0-9]{1,3})$',
             '\2.\1.'))
           || lpad(regexp_replace(btrim(ref),
                '^[0-9]{3}[[:space:]]+[A-Za-z0-9]{1,10}[[:space:]]+([0-9]{1,3})$',
                '\1'), 3, '0')
    WHEN btrim(ref) ~ '^[A-Za-z]{0,3}[0-9]{0,4}\.[0-9]{3}\.[0-9]{1,3}$'
      THEN upper(regexp_replace(btrim(ref),
             '^([A-Za-z]{0,3}[0-9]{0,4})\.([0-9]{3})\.([0-9]{1,3})$', '\1.\2.'))
           || lpad(regexp_replace(btrim(ref),
                '^[A-Za-z]{0,3}[0-9]{0,4}\.[0-9]{3}\.([0-9]{1,3})$', '\1'), 3, '0')
    ELSE ref
  END
$$ LANGUAGE sql IMMUTABLE;

-- EUDAMED's own spelling: the fold, after dropping a trailing variant segment.
-- The segment must START with a letter, which is what keeps it off the size:
-- 'RC.070.027' keeps its '.027' and folds normally.
CREATE OR REPLACE FUNCTION eudamed_ref_key(ref text) RETURNS text AS $$
  SELECT fold_bur_code(regexp_replace(ref, '\.[A-Za-z][A-Za-z0-9]{0,2}$', ''))
$$ LANGUAGE sql IMMUTABLE;

-- The view, with one changed join condition. Everything else is migration 046
-- verbatim; see that file for why `item_canonical` uses EXISTS rather than a
-- JOIN, why the gap exclusion sits on the JOIN, and what the (item_ref,
-- basic_udi_di) uniqueness invariant protects.
--
-- CREATE OR REPLACE, not DROP and re-create: the column list is unchanged, so
-- `eudamed_declaration_gap` and `eudamed_gap_summary` keep working and this
-- migration does not have to restate two views it does not change.
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
                                                 AND d.status = 'production'),
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
