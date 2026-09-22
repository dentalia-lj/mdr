-- 027_effective_expiry.sql
-- One definition of "when does this document lapse", used by every consumer.
--
-- Client ruling 2026-08-18 (question 14 of docs/2026-08-13-vprasanja-za-dentalio.md):
--   "kjer je datum napisan upoštevaj datum -- kjer ga ni upoštevaj + 5 let za potek."
-- and, on the same reply, BOTH options ticked: follow the certificate the
-- declaration cites, AND "osvežite vsako izjavo, starejšo od 5 let".
--
-- Two rules that both apply means the EARLIER of them fires -- hence LEAST,
-- not COALESCE. A declaration issued in 2019 that cites a certificate running
-- to 2029 is due for review in 2024: the certificate is still valid, the
-- five-year-old declaration is what needs refreshing. A stated expiry does not
-- exempt a document either -- "vsako izjavo" is every declaration.
--
-- Why a view rather than a third COALESCE. The rule already had TWO
-- implementations before this migration -- app/handlers/report.py
-- (report.weekly + the scheduler's expiry scan) and two queries in web/app.py
-- (the items board's next_expiry and the /expiry board) -- and they had already
-- drifted: report.py restricted the cited certificate to production/superseded
-- (a REJECTED certificate must not lend out its expiry, ruled 2026-08-14), the web
-- queries did not. Adding a third branch to three places would have widened
-- that. The view is the single definition; the drift is fixed by construction.
--
-- Legal note, so nobody "corrects" this later: a Declaration of Conformity has
-- no expiry by regulation. MDR Annex IV requires only a place and date of
-- issue, and Article 19(1) requires that "the manufacturer shall continuously
-- update the EU declaration of conformity" -- a duty to keep current, not a
-- validity period. Article 56(2) caps NOTIFIED-BODY certificates at five years;
-- that is a different document. The five years here is therefore a REVIEW
-- horizon Dentalia chose, not a legal expiry, and `basis` says so on every row
-- so no screen can claim a document "expired" when the document claims nothing
-- of the sort.
--
-- Class I self-certified devices have no certificate to chase and their
-- declarations carry no expiry, so `staleness` is the only trigger they will
-- ever have. They are deliberately NOT excluded: excluding them would mean a
-- class I declaration is never looked at again, which is the opposite of
-- Article 19(1). The client's two answers (16: "razred I mora imeti DOC, ne
-- rabi pa certifikata"; 14: "+5 let") only conflict if `staleness` is read as
-- an expiry -- it is not.

CREATE VIEW document_effective_expiry AS
SELECT d.doc_id,
       LEAST(
         d.validity_to,
         c.validity_to,
         -- DoC only. A certificate with no expiry is a data defect (Article 56
         -- makes one mandatory) and must surface as such, never be papered over
         -- with a synthetic date.
         CASE WHEN d.type = 'DoC' AND d.validity_from IS NOT NULL
              THEN (d.validity_from + interval '5 years')::date END
       ) AS expires,
       -- Which of the three won. Ties resolve toward the document's own claim:
       -- stated, then the certificate it cites, then the review horizon.
       CASE
         WHEN d.validity_to IS NOT NULL
              AND d.validity_to <= LEAST(c.validity_to,
                    CASE WHEN d.type = 'DoC' AND d.validity_from IS NOT NULL
                         THEN (d.validity_from + interval '5 years')::date END)
           THEN 'stated'
         WHEN d.validity_to IS NOT NULL AND c.validity_to IS NULL
              AND NOT (d.type = 'DoC' AND d.validity_from IS NOT NULL)
           THEN 'stated'
         WHEN c.validity_to IS NOT NULL
              AND c.validity_to <= COALESCE(
                    CASE WHEN d.type = 'DoC' AND d.validity_from IS NOT NULL
                         THEN (d.validity_from + interval '5 years')::date END,
                    c.validity_to)
           THEN 'inherited'
         WHEN d.type = 'DoC' AND d.validity_from IS NOT NULL
           THEN 'staleness'
       END AS basis,
       -- Kept for the templates that already render it; `basis` is the richer
       -- form and new code should read that.
       (d.validity_to IS NULL AND c.validity_to IS NOT NULL) AS inherited
  FROM document d
  -- Status filter belongs on the JOIN: as a WHERE clause it would turn the
  -- LEFT join into an inner one and drop every document that cites nothing.
  LEFT JOIN document c ON c.doc_id = d.cert_doc_id
                      AND c.status IN ('production', 'superseded');

COMMENT ON VIEW document_effective_expiry IS
  'When a document lapses, and on what grounds: stated (its own validity_to), '
  'inherited (the production/superseded certificate it cites), staleness '
  '(a DoC with no expiry, five years after issue -- a review horizon, not a '
  'legal expiry). NULL expires = nothing to chase.';

-- Created after 007's blanket GRANT, so grant explicitly. SELECT only: the web
-- process is a producer, never a registry writer (invariant 1), and this view
-- is read by both its boards -- /expiry and the items board's next_expiry.
GRANT SELECT ON document_effective_expiry TO dentalia_api;
