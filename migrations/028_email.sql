-- S2.4 EMAIL, INBOUND slice. The processed-email ledger: the durable,
-- idempotent reprocess guard for `email.poll`. Keyed by mailbox identity +
-- IMAP UIDVALIDITY + UID, so a recorded message is NEVER polled twice -- this
-- is the authority, NOT the IMAP \Seen flag (not durable, not trustworthy).
--
-- Distinct from the content-hash attachment dedupe (fetch_log + extract:{hash}),
-- which stays as-is for the archive/extract side: this table is per SOURCE
-- EMAIL, that one is per PHYSICAL DOCUMENT. A brand-new email whose only
-- attachment we already hold still gets a row here (its origin is auditable)
-- while emitting no new extract.doc.
--
-- `email.poll` is already a value in the closed job_type enum (migration 001)
-- and PRD §8 -- no ALTER TYPE here; this is not a new tag.
--
-- Written by: email.poll (S2.4). Read by: web /emails (dentalia_api, SELECT).
-- Never writes document/item_document/evidence (invariant 1) -- a producer
-- ledger only, same footing as fetch_log.

CREATE TABLE email_poll_log (
  id                 bigserial PRIMARY KEY,
  mailbox            text        NOT NULL,   -- "{host}/{user}/{folder}" (adapter.mailbox_id)
  uid_validity       text        NOT NULL,   -- IMAP UIDVALIDITY epoch (UIDs stable only within it)
  imap_uid           text        NOT NULL,   -- IMAP UID (per-mailbox, within uid_validity)
  message_id         text,                   -- RFC822 Message-ID (durable cross-mailbox identity)
  from_addr          text,
  subject            text,
  received_at        timestamptz,            -- Date header
  in_reply_to        text,                   -- reply-matching hook (outbound slice, spec §9)
  references_hdr     text,                   -- References header (outbound reply matching)
  had_attachments    boolean     NOT NULL DEFAULT false,
  attachments        jsonb       NOT NULL DEFAULT '[]'::jsonb,
                                             -- per-attachment: {filename, content_type, verdict,
                                             -- reason, content_hash, archive_url, disposition}
  emitted_jobs       jsonb       NOT NULL DEFAULT '[]'::jsonb,  -- [{type, dedupe_key}]
  renewal_request_id bigint,                 -- soft ref; populated by the outbound slice's
                                             -- reply matcher (spec §9). NULL inbound-only.
  processed_at       timestamptz NOT NULL DEFAULT now()
);

-- The reprocess guard: one row per (mailbox, uid_validity, uid). A poll checks
-- this before touching a message and skips a recorded UID outright.
CREATE UNIQUE INDEX email_poll_log_uid_key
  ON email_poll_log (mailbox, uid_validity, imap_uid);

-- Reply matching (outbound slice) resolves a reply's In-Reply-To/References to
-- the Message-ID of a message we recorded; index the id we look up by.
CREATE INDEX email_poll_log_message_id_idx ON email_poll_log (message_id);

-- New table after 007's blanket grant, so grant explicitly. The web producer
-- renders the /emails list; it never writes here (invariant 1).
GRANT SELECT ON email_poll_log TO dentalia_api;
