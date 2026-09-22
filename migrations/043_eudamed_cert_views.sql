-- The three certificate findings, as views
-- (docs/superpowers/specs/2026-08-25-eudamed-enhancement-design.md §4.1-4.3).
--
-- Views, not tables: the queue is regenerable state and the registry is truth;
-- a findings table is a third thing that can disagree with both.
--
-- STANDING RULING, Denis 2026-08-19 (app/handlers/validate.py:711-745):
-- `Rev. NN` is deliberately NOT resolved. "Rev. 00 and Rev. 01 may be a
-- supersession rather than a spelling, and resolving one to the other would
-- assert that silently." These views SPLIT our cert_number to join at all --
-- we store `G15 043306 0282 Rev. 00`, EUDAMED stores the number bare with the
-- revision in its own field -- and then REPORT both sides. Nothing here writes
-- superseded_by, cert_doc_id, or any registry column.
--
-- RULING, Denis 2026-08-26: join on baseline only. Looser normalisations are a
-- separate `possible_match` view for a human to confirm.

-- Our side, split once so three views agree on it.
CREATE VIEW held_certificate AS
SELECT d.doc_id,
       d.type,
       d.status,
       g.canonical_manufacturer AS canonical_name,
       d.cert_number AS raw_cert_number,
       -- Strip the trailing R-code (validate.py's existing, ruled-on rule),
       -- then split off `Rev. NN` -- which is REPORTED, never resolved.
       btrim(regexp_replace(
           regexp_replace(d.cert_number, '\s+R[0-9]+$', ''),
           '\s*Rev\.?\s*[0-9]+$', '', 'i')) AS base_cert_number,
       nullif(btrim(substring(d.cert_number from '(?i)Rev\.?\s*[0-9]+$')), '')
           AS our_revision
  FROM document d
  JOIN item_document idoc ON idoc.doc_id = d.doc_id
                         AND idoc.status = 'production'
  JOIN item_group_member gm ON gm.item_ref = idoc.item_ref
  JOIN item_group g ON g.group_id = gm.group_id
 WHERE d.cert_number IS NOT NULL AND d.cert_number <> ''
   AND g.canonical_manufacturer IS NOT NULL
   -- EC and ISO only. A declaration of conformity CITES the notified-body
   -- certificate number verbatim -- 'G15 043306 0282 Rev. 00' appears on 96 of
   -- ours -- so without this restriction a DoC reads as though we held the
   -- certificate itself. That suppressed certificate_gap (which asks whether we
   -- hold a copy at all) while certificate_drift's own type filter skipped it,
   -- leaving EUDAMED-listed certificates invisible to every view. Measured on
   -- the dev database 2026-08-26: 4 (manufacturer, certificate) pairs.
   AND d.type IN ('EC', 'ISO');

-- Only `auto` and `confirmed` attributions are trusted. A `pending` guess must
-- not raise an alert against a manufacturer it may not belong to.
CREATE VIEW trusted_manufacturer_srn AS
SELECT canonical_name, srn
  FROM manufacturer_srn
 WHERE status IN ('auto', 'confirmed');

CREATE VIEW certificate_drift AS
SELECT DISTINCT
       h.doc_id,
       h.canonical_name,
       h.base_cert_number AS certificate_number,
       h.our_revision,
       h.raw_cert_number,
       c.revision_number  AS eudamed_revision,
       c.certificate_status,
       c.certificate_type,
       c.issue_date       AS eudamed_issue_date,
       c.expiry_date      AS eudamed_expiry_date,
       c.notified_body_srn,
       c.actor_srn,
       c.actor_name
  FROM held_certificate h
  JOIN trusted_manufacturer_srn s ON s.canonical_name = h.canonical_name
  JOIN eudamed_certificate c ON c.actor_srn = s.srn
                            AND c.certificate_number = h.base_cert_number
 WHERE h.type IN ('EC', 'ISO')
   -- Comparison-only normalisation, not a resolution. `revisionNumber` is
   -- measured (design.md S1.3, 08-26) in at least six live formats --
   -- `Rev. 02`, `Rev.0`, `Rev. 5`, `Rev. 07`, `1`, `2` -- while our own
   -- extraction always keeps the `Rev` token (`held_certificate`'s regex
   -- above), so a byte-for-byte compare reports `Rev. 00` against `Rev.0`,
   -- or `Rev. 02` against bare `2`, as drift when they are the SAME revision
   -- differently spelled. Strip everything but digits and cast to int so a
   -- leading zero does not fool it either (`02` and `2` must compare equal).
   -- This is still comparison-only: both `our_revision` and `eudamed_revision`
   -- above stay the RAW strings, unchanged, so the UI and the e-mail keep
   -- showing exactly what each side literally says. The 2026-08-19 ruling
   -- forbids resolving `Rev. NN` to one another -- asserting that one
   -- revision succeeds or supersedes another. Recognising that two spellings
   -- NAME the same revision is the opposite claim, not a version of it: `Rev.
   -- 00` still differs from `Rev. 02` (0 <> 2, still drift), and an unstated
   -- revision on one side still differs from any stated revision on the
   -- other (NULL IS DISTINCT FROM any int is true).
   AND nullif(regexp_replace(coalesce(h.our_revision, ''), '\D', '', 'g'), '')::int
       IS DISTINCT FROM
       nullif(regexp_replace(coalesce(c.revision_number, ''), '\D', '', 'g'), '')::int;

-- Shown, never joined. Each row names the rule that WOULD have matched it, so
-- a person sees what they are being asked to accept. Measured 2026-08-26:
-- baseline reaches 107 of 391 certificate-carrying documents, the trailing
-- letter takes it to 143, the SX->HZ prefix to 145.
-- DISTINCT, matching certificate_drift's own guard above: held_certificate
-- fans out one row per (doc, production item_document link, item_group_member
-- row) -- this branch's own migration comment on trusted_manufacturer_srn's
-- sibling table records a manufacturer-scope certificate binding 2.567
-- articles at once -- and this view selects only doc- and certificate-level
-- columns, nothing that distinguishes the article, so without DISTINCT every
-- near-miss renders once per bound article on the unpaginated /expiry panel.
CREATE VIEW certificate_drift_candidate AS
SELECT DISTINCT
       h.doc_id,
       h.canonical_name,
       h.raw_cert_number,
       c.certificate_number AS possible_match,
       c.revision_number    AS eudamed_revision,
       c.certificate_status,
       c.expiry_date        AS eudamed_expiry_date,
       CASE WHEN btrim(regexp_replace(h.base_cert_number, '\s+[A-Z]$', ''))
                 = c.certificate_number THEN 'trailing-letter'
            WHEN regexp_replace(h.base_cert_number, '^SX\M', 'HZ')
                 = c.certificate_number THEN 'sx-hz-prefix'
       END AS possible_rule
  FROM held_certificate h
  JOIN trusted_manufacturer_srn s ON s.canonical_name = h.canonical_name
  JOIN eudamed_certificate c ON c.actor_srn = s.srn
 WHERE h.type IN ('EC', 'ISO')
   AND c.certificate_number <> h.base_cert_number
   AND (btrim(regexp_replace(h.base_cert_number, '\s+[A-Z]$', ''))
            = c.certificate_number
     OR regexp_replace(h.base_cert_number, '^SX\M', 'HZ')
            = c.certificate_number);

-- The adverse four only. `issued`, `supplemented`, `reissued` and `amended`
-- are ordinary lifecycle events -- 4.404 of the 4.608 live records, measured
-- 2026-08-26 (issued 2.770, supplemented 1.007, amended 422, reissued 197) --
-- and alerting on them would bury the 204 that matter (withdrawn 87,
-- cancelled 68, restricted 31, suspended 18).
CREATE VIEW certificate_status_alert AS
SELECT s.canonical_name,
       c.certificate_number,
       c.revision_number,
       c.certificate_status,
       c.certificate_type,
       c.expiry_date,
       c.notified_body_srn,
       c.actor_srn,
       c.actor_name,
       c.first_seen
  FROM trusted_manufacturer_srn s
  JOIN eudamed_certificate c ON c.actor_srn = s.srn
 WHERE c.certificate_status IN
       ('withdrawn', 'cancelled', 'suspended', 'restricted');

-- EUDAMED lists it, we hold no copy. The cheapest ask of the four: a named
-- number and issuing notified body, one line in an e-mail. The reverse case --
-- we hold a certificate EUDAMED does not list -- is expected for MDD-era
-- paperwork predating the register and is deliberately NOT a finding.
--
-- Cross-view handoff, not a gap in this view: holding ANY revision of a base
-- certificate number suppresses the gap for that number entirely (the
-- NOT EXISTS below matches on base_cert_number alone, revision-blind). A
-- missing NEWER revision then surfaces in certificate_drift instead --
-- verified: hold Rev. 00, EUDAMED lists Rev. 00 and Rev. 02 ->
-- certificate_gap is empty for that number, certificate_drift reports
-- our_revision='Rev. 00', eudamed_revision='Rev. 02'. Nothing is lost between
-- the two views; do not "fix" this by adding a revision comparison here.
CREATE VIEW certificate_gap AS
SELECT s.canonical_name,
       c.certificate_number,
       c.revision_number,
       c.certificate_type,
       c.certificate_status,
       c.issue_date,
       c.expiry_date,
       c.notified_body_srn,
       c.actor_srn,
       c.actor_name
  FROM trusted_manufacturer_srn s
  JOIN eudamed_certificate c ON c.actor_srn = s.srn
 WHERE NOT EXISTS (
       SELECT 1 FROM held_certificate h
        WHERE h.canonical_name = s.canonical_name
          AND h.base_cert_number = c.certificate_number);

GRANT SELECT ON held_certificate, trusted_manufacturer_srn, certificate_drift,
                certificate_drift_candidate, certificate_status_alert,
                certificate_gap
  TO dentalia_api;
