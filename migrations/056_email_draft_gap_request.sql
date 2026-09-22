-- A third draft kind: the first ask, not a renewal.
--
-- `email.request` composes from `lapsing_for_manufacturer` -- documents we
-- ALREADY HOLD that are expiring -- and returns at `if not groups` when there
-- are none. That is every manufacturer whose problem is that we hold nothing:
-- measured 2026-09-02 after the EUDAMED sweeps, CARL MARTIN has 2.389 articles
-- with no declaration at all, resolving to 70 documents to request, and the
-- renewal path can express none of them.
--
-- `gap-request` is that letter. It reuses REQUEST_BODY verbatim (the client's
-- own wording, which already reads as a first ask) and differs only in what
-- fills the list: Basic UDI-DI groups from `eudamed_declaration_gap` rather
-- than expiring documents.
--
-- A separate KIND rather than a widened `request`, because the drafts board
-- must be able to tell "your certificate lapses in 30 days" from "we have
-- never had a declaration for these 281 articles" -- they go to different
-- people and carry different urgency. `renewal_request_id` stays NULL for
-- these: a gap request is not a renewal, has no document to renew, and must
-- not take a slot in the renewal cadence guard.
ALTER TABLE email_draft DROP CONSTRAINT email_draft_kind_vocabulary;
ALTER TABLE email_draft ADD CONSTRAINT email_draft_kind_vocabulary
  CHECK (kind = ANY (ARRAY['request'::text, 'reminder'::text,
                           'gap-request'::text]));
