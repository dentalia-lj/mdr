-- The declaration gap and the sweep delta
-- (docs/superpowers/specs/2026-08-25-eudamed-enhancement-design.md §4.4, §3.4).
--
-- EUDAMED holds zero declarations of conformity itself -- it can only say
-- which devices a manufacturer registered, so the most this feature can ever
-- do is tell an operator what to go and ask for. The unit of that ask is the
-- Basic UDI-DI group, not the article: the registry's largest measured gap is
-- 2.569 Carl Martin reusable instruments, which is 70 documents to request,
-- not 2.569 (a declaration covers the whole group).
--
-- `eudamed_article_status` is the shared base every other view here reads --
-- one row per (our medical-device article, EUDAMED match, article-level DoC
-- state) -- so `eudamed_declaration_gap` and `eudamed_gap_summary` cannot
-- silently disagree about what "covered" means.
--
-- `item_canonical` restricts by trusted SRN with EXISTS, not a JOIN: IVOCLAR
-- alone holds two SRNs (001 and 005), and joining `trusted_manufacturer_srn`
-- directly would duplicate every one of its articles once per sibling SRN
-- before the EUDAMED match is even applied, silently doubling every count
-- downstream.
--
-- The reference join reuses `probe_srn`'s own precedence
-- (`app/handlers/eudamed.py`): `COALESCE(mfr_ref, item_ref)` -- the supplier's
-- article number when we have it (basis `ref-list`), Dentalia's own item
-- number otherwise (basis `ref-item`) -- per Invariant 3 and client ruling C12
-- (the primary item number is the key and is what is printed on the physical
-- article).
--
-- `has_production` requires BOTH the link and the document to read
-- 'production', matching `item_document_production`'s own visibility rule
-- (migration 005) rather than inventing a second one. `has_staged` and
-- `has_production` both additionally require the matched document to be
-- `type = 'DoC'` -- this feature answers "do we hold a DECLARATION", not "do
-- we hold anything" -- enforced via `d.doc_id IS NOT NULL` (the LEFT JOIN to
-- `document` only succeeds for a DoC-type row), not by filtering `idoc.status`
-- alone, which would otherwise leak a staged EC/ISO link into the DoC bucket.
CREATE VIEW eudamed_article_status AS
WITH item_canonical AS (
  SELECT DISTINCT im.item_ref, im.mfr_ref, g.canonical_manufacturer AS canonical_name
    FROM item_mirror im
    JOIN item_group_member gm ON gm.item_ref = im.item_ref
    JOIN item_group g         ON g.group_id = gm.group_id
   WHERE im.md_flag IS TRUE
     AND EXISTS (SELECT 1 FROM trusted_manufacturer_srn s
                  WHERE s.canonical_name = g.canonical_manufacturer)
),
-- Excluded on the JOIN, never in WHERE (Denis, 2026-08-26): a WHERE turns
-- this LEFT join inner and drops every article with no link at all -- exactly
-- the population a gap list exists to find. This bug has already been made
-- and fixed once in web/registry.py.
--
-- INVARIANT, added on review 2026-08-27: at most one row per (item_ref,
-- basic_udi_di) leaves this CTE. `trade_name` is per-UDI-DI, not per
-- reference or per basic_udi_di -- migration 039 documents one Ivoclar
-- basic_udi_di group spanning 74 individual device records, each free to
-- carry its own trade_name -- so a plain `SELECT DISTINCT` that includes
-- `trade_name` does NOT collapse two `eudamed_mirror` rows that share a
-- `reference` and a `basic_udi_di` but differ only in `trade_name`: it
-- emitted two rows for the one physical article, and `eudamed_declaration_gap`
-- counted that article twice in the request list an operator works from.
-- `trade_name` is therefore aggregated here (`max`, display-only per
-- migration 039's own ruling on that column -- any one value is as good as
-- another to show) rather than compared for uniqueness, and the GROUP BY is
-- the real key: (item_ref, basic_udi_di). Fixed at this shared base rather
-- than with a `count(DISTINCT ...)` patch downstream -- `eudamed_article_status`
-- is read by more than one view, and a fix here holds for all of them instead
-- of relying on every future consumer to remember to de-duplicate.
eudamed_match AS (
  SELECT ic.item_ref, em.basic_udi_di, max(em.trade_name) AS trade_name
    FROM item_canonical ic
    JOIN trusted_manufacturer_srn s ON s.canonical_name = ic.canonical_name
    JOIN eudamed_mirror em          ON em.manufacturer_srn = s.srn
                                    AND em.reference = COALESCE(ic.mfr_ref, ic.item_ref)
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

-- The request list. `articles` restricts to articles with no ARTICLE-LEVEL
-- production evidence (design doc §4.4) -- a staged article is still an
-- outstanding ask, so it counts here too; `staged` is the subset of it that
-- already has a candidate awaiting review, reported separately per §5.3 ("a
-- gap list that counts a staged declaration as missing sends a person chasing
-- a document we already have") and never folded into `articles`' absent
-- remainder. A group with zero such articles is fully covered and is not a
-- gap -- excluded by the HAVING, not shown as a zero row.
--
-- `mfr_scope_covered` is informational, not a suppression: a manufacturer
-- holding ANY production document with `coverage_scope = 'manufacturer'`
-- (the shape of Carl Martin's ISO 13485 mfr-scope certificate, which bound
-- 2.567 articles in one document -- see web/registry.py's
-- `manufacturer_completeness`) still has its per-article DoC gap listed here;
-- the column only tells the reader that a broader supplier-level document
-- already exists, so they know the group is not a first contact.
CREATE VIEW eudamed_declaration_gap AS
WITH mfr_scope AS (
  SELECT DISTINCT g2.canonical_manufacturer AS canonical_name
    FROM document d2
    JOIN item_document l2      ON l2.doc_id = d2.doc_id AND l2.status <> 'retracted'
    JOIN item_group_member gm2 ON gm2.item_ref = l2.item_ref
    JOIN item_group g2         ON g2.group_id = gm2.group_id
   WHERE d2.coverage_scope = 'manufacturer' AND d2.status = 'production'
)
SELECT a.canonical_name,
       a.basic_udi_di,
       count(*) FILTER (WHERE NOT a.has_production)                  AS articles,
       count(*) FILTER (WHERE NOT a.has_production AND a.has_staged) AS staged,
       bool_or(mf.canonical_name IS NOT NULL)                        AS mfr_scope_covered,
       max(a.trade_name)                                             AS trade_name
  FROM eudamed_article_status a
  LEFT JOIN mfr_scope mf ON mf.canonical_name = a.canonical_name
 WHERE a.in_eudamed
 GROUP BY a.canonical_name, a.basic_udi_di
HAVING count(*) FILTER (WHERE NOT a.has_production) > 0;

-- The denominator. `not_in_eudamed` is its OWN bucket (§5.6: 178 of 2.567
-- Carl Martin articles did not resolve, cause unknown) and is never folded
-- into `covered` or `missing` -- doing so would overstate coverage on exactly
-- the articles nobody has explained yet. `count(DISTINCT item_ref)`, not
-- `count(*)`: an article that happens to match more than one EUDAMED
-- basic_udi_di must not be counted twice in a manufacturer-wide total.
CREATE VIEW eudamed_gap_summary AS
SELECT canonical_name,
       count(DISTINCT item_ref) FILTER (WHERE in_eudamed AND has_production)
         AS covered,
       count(DISTINCT item_ref) FILTER (WHERE in_eudamed AND NOT has_production
                                           AND has_staged)
         AS staged,
       count(DISTINCT item_ref) FILTER (WHERE in_eudamed AND NOT has_production
                                           AND NOT has_staged)
         AS missing,
       count(DISTINCT item_ref) FILTER (WHERE NOT in_eudamed)
         AS not_in_eudamed
  FROM eudamed_article_status
 GROUP BY canonical_name;

-- The sweep delta: what changed since the last review, not the mirror's whole
-- content (already reachable through the two views above).
--
-- RULING 24, Denis 2026-08-26, binding: `last_swept_at IS NOT NULL` is
-- required in the WHERE below -- a manufacturer with no `eudamed_sweep_state`
-- row, or with a NULL `last_swept_at`, yields ZERO delta rows, never
-- "everything is new". A delta is a statement about change since a baseline;
-- with no baseline there is no change to report, and "everything is new"
-- would put 11.364 rows in front of an operator on Ivoclar's first sweep --
-- exactly the noise this view exists to suppress, for the same reason the gap
-- unit above is the Basic UDI-DI group rather than the device.
--
-- `status_changes` is a proxy, not a true diff: nothing in this schema stores
-- a device's PREVIOUS `device_status_type` (§5.7: "read for transitions, acted
-- on nowhere" -- this is that reader), so "changed" cannot be verified against
-- a prior value. What this counts instead: a member device currently
-- reporting anything other than 'on-the-market', re-synced (`synced_at`)
-- after the manufacturer's last reviewed sweep. Once `last_swept_at` advances
-- past that sweep, the same still-unchanged status stops being counted -- it
-- surfaces once, not on every later read, even though the true transition
-- instant is unknown. `IS DISTINCT FROM` so a NULL `device_status_type` (a
-- field EUDAMED does not always populate) is treated as "not on-the-market"
-- rather than silently excluded by a NULL-unsafe `<>`.
CREATE VIEW eudamed_sweep_delta AS
SELECT tm.canonical_name,
       m.basic_udi_di,
       count(*) FILTER (WHERE m.first_seen > s.last_swept_at) AS new_devices,
       count(*) FILTER (WHERE m.device_status_type IS DISTINCT FROM 'on-the-market'
                          AND m.synced_at > s.last_swept_at)  AS status_changes,
       s.last_swept_at
  FROM eudamed_mirror m
  JOIN trusted_manufacturer_srn tm ON tm.srn = m.manufacturer_srn
  JOIN eudamed_sweep_state s       ON s.canonical_name = tm.canonical_name
 WHERE s.last_swept_at IS NOT NULL
 GROUP BY tm.canonical_name, m.basic_udi_di, s.last_swept_at
HAVING count(*) FILTER (WHERE m.first_seen > s.last_swept_at) > 0
    OR count(*) FILTER (WHERE m.device_status_type IS DISTINCT FROM 'on-the-market'
                          AND m.synced_at > s.last_swept_at) > 0;

-- The web process is a producer/reader only (Invariant 1) -- SELECT, nothing
-- more. `eudamed_article_status` is granted alongside the three it feeds,
-- mirroring migration 043's inclusion of `held_certificate`.
GRANT SELECT ON eudamed_article_status, eudamed_declaration_gap,
                eudamed_gap_summary, eudamed_sweep_delta
  TO dentalia_api;
