-- Parsed text per archived document, keyed by content hash (not doc_id: text
-- exists from EXTRACT time, before GATE creates a document row). One row per
-- content hash; re-extraction overwrites, since the bytes are immutable.
--
-- `source` records provenance and is load-bearing: 'pdf-text' is a
-- deterministic PyMuPDF read and may be used to verify evidence.verbatim.
-- 'none' means the PDF has no text layer (a scan) and nothing was recovered.
-- Model-produced transcripts are NOT written here today; if that ever changes
-- it gets its own source value and must never be treated as verbatim source.
CREATE TABLE document_text (
  content_hash text PRIMARY KEY,
  source       text NOT NULL CHECK (source IN ('pdf-text', 'none')),
  engine       text,
  pages        int  NOT NULL DEFAULT 0,
  chars        int  NOT NULL DEFAULT 0,
  content      text NOT NULL DEFAULT '',
  built_at     timestamptz NOT NULL DEFAULT now()
);

-- Created after 007's blanket grant, so grant explicitly (precedent 009/012).
GRANT SELECT ON document_text TO dentalia_api;
