-- S2.4 EMAIL, OUTBOUND slice: the renewal chase, as reviewable drafts.
--
-- The system NEVER SENDS (client ruling 2026-08-20, spec 7.1): it prepares a
-- draft and a person sends it from mdr@dentalia.si. So `ready` is a terminal
-- state here, not a handoff to a send handler, and there is no send column
-- beyond the record-keeping ones an eventual reversal would need.
--
-- Cadence (client ruling 2026-08-20, spec 7.2): at most ONE mail per
-- manufacturer per week/fortnight, listing EVERY document expiring for that
-- manufacturer. That is why the document set is a link table rather than
-- renewal_request.doc_id: measured on the live registry, 52 documents share the
-- expiry date 2026-05-04 and all 52 are IVOCLAR -- one mail, 52 rows here, not
-- 52 requests.
--
-- Written by: email.request / email.reminder (S2.4 outbound, NOT YET BUILT).
-- Written by web: email_draft only (subject/body/recipients/status), the same
-- footing as upload_inbox -- a human editing correspondence, never the
-- registry. Invariant 1 is untouched: document / item_document / evidence stay
-- GATE-only and this migration grants web nothing on them.

-- ---------------------------------------------------------------------------
-- renewal_request: from one document's chase to one manufacturer's mail
-- ---------------------------------------------------------------------------
ALTER TABLE renewal_request
  ADD COLUMN manufacturer      text,        -- canonical_manufacturer, the cadence key
  ADD COLUMN group_id          bigint,      -- soft ref, discovery-exhausted requests
  ADD COLUMN reason            text,        -- expiry | discovery-exhausted
  ADD COLUMN period_key        text,        -- cadence bucket (spec 7.2)
  ADD COLUMN last_reminder_at  timestamptz,
  ADD COLUMN escalation_count  int NOT NULL DEFAULT 0;

COMMENT ON COLUMN renewal_request.doc_id IS
  'ANCHOR only: the document whose expiry tripped the horizon. The full scope '
  'of the chase is renewal_request_document -- a request covers many documents '
  'under the one-mail-per-manufacturer cadence rule (spec 7.2).';

-- The cadence guard, in the schema rather than in the handler's head: one live
-- request per manufacturer per period. Partial, because a closed chase must not
-- block the next period's, and manufacturer/period are null on rows written
-- before this migration.
CREATE UNIQUE INDEX renewal_request_cadence_key
  ON renewal_request (manufacturer, period_key)
  WHERE manufacturer IS NOT NULL
    AND period_key IS NOT NULL
    AND state NOT IN ('parsed', 'received');

-- ---------------------------------------------------------------------------
-- every document one mail is chasing
-- ---------------------------------------------------------------------------
CREATE TABLE renewal_request_document (
  renewal_request_id bigint      NOT NULL REFERENCES renewal_request(id),
  doc_id             bigint      NOT NULL REFERENCES document(doc_id),
  added_at           timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (renewal_request_id, doc_id)
);

CREATE INDEX renewal_request_document_doc_idx
  ON renewal_request_document (doc_id);

-- ---------------------------------------------------------------------------
-- the draft a person reads, edits and releases
-- ---------------------------------------------------------------------------
CREATE TABLE email_draft (
  id                 bigserial PRIMARY KEY,
  renewal_request_id bigint    REFERENCES renewal_request(id),
  kind               text      NOT NULL,   -- request | reminder (spec 7.1 / 7.3 copy)
  to_addrs           text[]    NOT NULL,
  subject            text      NOT NULL,
  body               text      NOT NULL,
  status             text      NOT NULL DEFAULT 'draft',
  edited_by          text,                 -- X-Forwarded-User at the last edit
  edited_at          timestamptz,
  released_at        timestamptz,          -- when a human marked it ready to send
  sent_message_id    text,                 -- pasted back by hand if we ever need
                                           -- reply matching on an outbound mail (spec 9)
  created_at         timestamptz NOT NULL DEFAULT now()
);

-- Closed vocabularies, enforced here rather than trusted from the app: `sent`
-- exists because a person will want to record that they sent it, NOT because
-- anything here sends.
ALTER TABLE email_draft
  ADD CONSTRAINT email_draft_status_vocabulary
  CHECK (status IN ('draft', 'ready', 'sent', 'cancelled'));

ALTER TABLE email_draft
  ADD CONSTRAINT email_draft_kind_vocabulary
  CHECK (kind IN ('request', 'reminder'));

CREATE INDEX email_draft_status_idx ON email_draft (status, created_at DESC);
CREATE INDEX email_draft_request_idx ON email_draft (renewal_request_id);

-- ---------------------------------------------------------------------------
-- grants: new tables after 007's blanket grant, so grant explicitly
-- ---------------------------------------------------------------------------
GRANT SELECT ON renewal_request_document TO dentalia_api;
GRANT SELECT ON renewal_request TO dentalia_api;

-- The one write the producer UI gets here. Editing and releasing a draft is a
-- human decision on OUR OWN outgoing correspondence -- it creates no registry
-- row, no link, no evidence, and enqueues nothing. Same precedent as web's
-- INSERT on upload_inbox. Deliberately NOT granted INSERT or DELETE: drafts are
-- created by the (unbuilt) email.request handler, and a draft nobody wants is
-- `cancelled`, never removed -- the chase has to stay auditable.
GRANT SELECT, UPDATE ON email_draft TO dentalia_api;
