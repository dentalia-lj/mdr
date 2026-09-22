-- Web document upload (manual sibling of the Phase-2 email.poll). The web
-- /upload form inserts one transient upload_inbox row (the PDF bytes) and
-- enqueues upload.ingest; a worker archives + hashes + emits extract.doc /
-- validate.doc, then deletes the row. dentalia_api (web) may INSERT the spool
-- but never reads/deletes it (worker-only) and never touches the registry
-- (invariant 1).
--
-- ALTER TYPE ... ADD VALUE cannot run in the same transaction that USES the new
-- value (PG16); this file only adds the value and creates the table — nothing
-- here inserts an 'upload.ingest' job row. Precedent: 013_scheduler.sql.

ALTER TYPE job_type ADD VALUE 'upload.ingest';

CREATE TABLE upload_inbox (
  id              bigserial PRIMARY KEY,
  filename        text        NOT NULL,
  content         bytea       NOT NULL,
  target_group_id bigint,                 -- soft ref (no FK): null when standalone
  catalogue       text        NOT NULL,
  uploaded_by     text,
  created_at      timestamptz NOT NULL DEFAULT now()
);

-- New table after 007's blanket grant, so grant explicitly. Producer role gets
-- INSERT only: it enqueues uploads but never reads/deletes the spool and never
-- writes the registry.
GRANT INSERT ON upload_inbox TO dentalia_api;
GRANT USAGE ON SEQUENCE upload_inbox_id_seq TO dentalia_api;
