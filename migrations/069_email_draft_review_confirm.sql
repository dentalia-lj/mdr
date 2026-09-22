-- A fifth kind of letter: "is this declaration still current?"
--
-- A declaration of conformity carries no expiry of its own -- MDR Annex IV
-- requires only an issue date, and Article 19(1) sets no validity period -- so
-- Dentalia lists one five years after issue. Ruled 2026-09-11: that is **review
-- due, not expired**, and what it earns is "please confirm this declaration is
-- still current", never a renewal demand. Denis restated it on 2026-09-15:
-- five years stays the default, and the reviewer sees it as its own group.
--
-- F34 taught the item card, the expiry board, the menu count and the weekly
-- report to draw that line. This is the piece F34 left: the draft itself. There
-- were four kinds and none of them was it -- `request` and `reminder` chase a
-- renewal of something expiring, `gap-request` asks for declarations we have
-- never held, `cert-request` (067) asks for a certificate EUDAMED records.
--
-- Asking the wrong question here is not a small error. A class I device has no
-- notified body and no certificate, so this horizon is the ONLY trigger it will
-- ever produce; a renewal demand sent against it asks a manufacturer to reissue
-- a document that has not lapsed and that they are under no obligation to
-- reissue. Measured 2026-09-15: 48 production documents carry `basis =
-- 'staleness'`, 21 of them already past the date.
--
-- Vocabulary only. Nothing sends -- the chain still ends at a draft a person
-- releases (spec 7.1).

ALTER TABLE email_draft DROP CONSTRAINT email_draft_kind_vocabulary;

ALTER TABLE email_draft ADD CONSTRAINT email_draft_kind_vocabulary
  CHECK (kind = ANY (ARRAY['request', 'reminder', 'gap-request',
                           'cert-request', 'review-confirm']));
