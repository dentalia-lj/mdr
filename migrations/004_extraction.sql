-- 004_extraction.sql
-- Sketch §4 (extraction).
-- Encodes: every tier attempt kept (T3 humans see the full ladder), keyed by
--          content_hash so re-extraction after a model swap appends a new rev.

-- written by: extract.doc (all tier attempts kept — T3 humans see the full ladder)
CREATE TABLE extraction_attempt (
  id           bigserial PRIMARY KEY,
  content_hash text NOT NULL,
  tier         text NOT NULL,               -- T0|T1|T2|T3
  model_id     text,
  fields       jsonb NOT NULL,              -- extracted values + per-field confidence + evidence
  extract_rev  int NOT NULL DEFAULT 1,
  at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX extraction_hash_idx ON extraction_attempt (content_hash);
