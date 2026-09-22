-- A fourth kind of letter: "EUDAMED records that you hold this certificate,
-- and we do not."
--
-- `email_draft.kind` is a closed vocabulary (029) for the same reason job types
-- are: the drafts board reads it to tell one urgency from another, and a kind
-- invented as a string would render as itself on a page that has no column for
-- it. The three existing kinds all speak about documents we HAVE -- `request`
-- and `reminder` chase a renewal of something expiring, `gap-request` asks for
-- declarations we have never held for articles we stock.
--
-- `cert-request` is narrower than any of them and is the strongest ask this
-- system can make, because it is the only one that can name the document:
-- EUDAMED's public register lists the certificate number, its revision, its
-- type, its issue and expiry dates and the notified body that issued it
-- (`certificate_gap`, migration 043). Measured 2026-09-15: 44 such certificates
-- across 25 manufacturers. "Please send your documents" and "please send
-- MDR 835077, revision R001, issued 18 June 2026" are not the same letter.
--
-- Vocabulary only. No new column, no new table, and nothing sends -- the chain
-- still ends at a draft a person releases (spec 7.1).

ALTER TABLE email_draft DROP CONSTRAINT email_draft_kind_vocabulary;

ALTER TABLE email_draft ADD CONSTRAINT email_draft_kind_vocabulary
  CHECK (kind = ANY (ARRAY['request', 'reminder', 'gap-request', 'cert-request']));
