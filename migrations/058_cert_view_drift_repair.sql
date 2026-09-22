-- Re-apply migration 043's two corrected certificate views to a database that
-- was migrated before the correction was written.
--
-- Migration 043 was applied here on 2026-08-26 10:28 UTC. A later change
-- ("eudamed: normalise revision digits for comparison, dedupe the near-miss
-- view", 2026-08-27 10:37 UTC) then edited 043 IN PLACE. `schema_migrations`
-- keys on filename, so the edit never re-ran: every database migrated before
-- that change still serves the ORIGINAL definitions, while every database
-- built from scratch afterwards -- including every test database, which is
-- why the whole suite is green -- serves the corrected ones.
--
-- Measured on the dev database 2026-09-03, before this migration:
--
--   certificate_drift_candidate   5.650 rows, of which 4 distinct
--   /expiry                       1.43 MiB of HTML
--
-- `held_certificate` fans out one row per (doc, production `item_document`
-- link, `item_group_member` row) -- by design, and its own consumers use
-- `count(DISTINCT doc_id)` -- and the near-miss view selects only doc- and
-- certificate-level columns, so a single KOMET near-miss rendered once per
-- bound article. `tests/test_eudamed_views.py::
-- test_certificate_drift_candidate_is_distinct_per_bound_article` has pinned
-- the corrected shape since 2026-08-27 and passes; it could never have caught
-- this, because the defect is not in the tree at all -- it is the gap between
-- the tree and a long-lived database.
--
-- The `certificate_drift` half is latent rather than live: all 3 drift rows
-- present on 2026-09-03 survive the corrected comparison unchanged. It is
-- repaired here anyway so the two databases stop disagreeing at all.
--
-- Idempotent and safe to re-run: both views are dropped and recreated from
-- 043's current text verbatim. Nothing depends on either view (checked via
-- pg_depend 2026-09-03), so no cascade is needed and none is used -- a
-- CASCADE here would silently drop a future dependent.

DROP VIEW IF EXISTS certificate_drift_candidate;
DROP VIEW IF EXISTS certificate_drift;

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
   -- measured in at least six live formats -- `Rev. 02`, `Rev.0`, `Rev. 5`,
   -- `Rev. 07`, `1`, `2` -- while our own extraction always keeps the `Rev`
   -- token, so a byte-for-byte compare reports `Rev. 00` against `Rev.0`, or
   -- `Rev. 02` against bare `2`, as drift when they are the SAME revision
   -- differently spelled. Strip everything but digits and cast to int so a
   -- leading zero does not fool it either. Both displayed columns stay the
   -- RAW strings, so the UI and the e-mail keep showing what each side
   -- literally says. See 043 for the full ruling this rests on.
   AND nullif(regexp_replace(coalesce(h.our_revision, ''), '\D', '', 'g'), '')::int
       IS DISTINCT FROM
       nullif(regexp_replace(coalesce(c.revision_number, ''), '\D', '', 'g'), '')::int;

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

-- Dropping a view drops its grants with it; 043 granted both to the API role.
GRANT SELECT ON certificate_drift, certificate_drift_candidate
  TO dentalia_api;
