-- 040_import_inbox.sql
-- The spool that carries an uploaded BC export (items now, manufacturers in
-- slice B) from the web producer to the worker that parses it.
-- Design: docs/superpowers/specs/2026-08-25-bc-import-upload-design.md
--
-- WHY NOT upload_inbox. That table's consumer treats a missing row as "already
-- processed" -- one read is the whole lifecycle. This spool is TWO-PHASE: a
-- preview job reads the row and LEAVES it so the apply job can read the same
-- bytes, and only the apply consumes it. Sharing one table would make
-- upload.ingest's idempotency contract ambiguous, and a wrong-kind row
-- reaching it would archive an xlsx as a compliance document.
--
-- `kind` is CHECK-constrained text rather than an enum: it routes a spool row
-- inside this feature, it is not a pipeline contract, and CHECK is cheaper to
-- extend than an enum type. Both values exist from the start so slice B needs
-- no migration for the discriminator.
--
-- `catalogue` is nullable: an items row carries 'LJ' (there is one Business
-- Central -- Denis 2026-08-19, closing G17/G11), a vendors row carries none.
--
-- No job_type change here. Items reuse `ingest.run` with source='upload';
-- slice B's 041 adds `vendor.import`.

CREATE TABLE import_inbox (
  id           bigserial   PRIMARY KEY,
  kind         text        NOT NULL CHECK (kind IN ('items','vendors')),
  filename     text        NOT NULL,
  content      bytea       NOT NULL,
  catalogue    text,
  uploaded_by  text,
  created_at   timestamptz NOT NULL DEFAULT now()
);

-- Abandoned previews are collected by the handler (app/import_spool.gc), which
-- scans by age on every import run.
CREATE INDEX import_inbox_created_at_idx ON import_inbox (created_at);

-- New table after 007's blanket GRANT, so grant explicitly. INSERT only, the
-- same posture as upload_inbox: the producer spools and never reads it back.
-- Deliberately no SELECT -- the preview page reads the filename out of
-- job.payload, which the web role can already read.
GRANT INSERT ON import_inbox TO dentalia_api;
GRANT USAGE ON SEQUENCE import_inbox_id_seq TO dentalia_api;
