-- 005_registry.sql
-- Sketch §5 (registry — the deliverable).
-- Encodes:
--   Invariant 1  — only gate.candidate / gate.apply handlers write these tables
--                  (enforced by GRANTs in 007, not by DDL here).
--   Invariant 2  — every production value carries complete evidence (evidence table;
--                  GATE rejects candidates missing any field).
--   C5 (Invariant 3) — link-level status; production + {name-family,fetch-context}
--                  is a bug by definition -> CHECK constraint below.
--   C6 (Invariant 5) — supersession within identical (type, regulation); the
--                  self-referencing superseded_by chain is append-only; the
--                  cross-(type,regulation) guard is enforced in the GATE handler.
--   C8 (Invariant 10) — audit_log is self-contained: job_snapshot jsonb NOT NULL,
--                  via_job a SOFT reference (no FK) so queue pruning never breaks it.
--   content_hash UNIQUE — one registry row per physical document.

CREATE TYPE doc_status  AS ENUM ('staged', 'production', 'rejected', 'superseded');
CREATE TYPE link_status AS ENUM ('staged', 'production', 'rejected');   -- C5

CREATE TABLE document (
  doc_id            bigserial PRIMARY KEY,
  type              text NOT NULL,          -- DoC|EC|IFU|ISO|other
  regulation        text NOT NULL,          -- MDR|MDD|n.a.
  validity_from     date,
  validity_to       date,                   -- legitimately NULL (Class I, DoC w/o expiry)
  coverage_scope    text NOT NULL,          -- group|manufacturer (022 adds the CHECK)
  basic_udi_di      text,
  cert_number       text,
  content_hash      text NOT NULL UNIQUE,   -- one registry row per physical document
  source_url        text,
  archive_url       text NOT NULL,          -- our stored copy — mandatory
  referenced_doc_id bigint REFERENCES document,  -- multilist cross-ref; also 'related' cross-regulation pairs (C6)
  superseded_by     bigint REFERENCES document,  -- append-only chain
  status            doc_status NOT NULL DEFAULT 'staged',
  created_at        timestamptz NOT NULL DEFAULT now()
);
-- C6: superseded_by may only reference a document with identical (type, regulation)
-- and overlapping coverage subject. MDR never supersedes MDD — parallel chains.
-- Enforced in the GATE handler (cannot be expressed as a row CHECK — it compares
-- two rows). Belt-and-braces BEFORE UPDATE trigger is optional and not added here.

CREATE TABLE item_document (
  item_ref    text   REFERENCES item_mirror,
  doc_id      bigint REFERENCES document,
  udi         text,
  match_basis text NOT NULL,        -- ref-list|udi|basic-udi-di|name-family|fetch-context|mfr-scope|manual
  status      link_status NOT NULL DEFAULT 'staged',   -- C5: links gated independently
  PRIMARY KEY (item_ref, doc_id),
  -- C5 drift guard (Invariant 3): a production link on a name-family/fetch-context
  -- basis is a bug by definition — reject it structurally, at link level.
  CONSTRAINT item_document_trusted_basis_ck
    CHECK (NOT (status = 'production' AND match_basis IN ('name-family', 'fetch-context')))
);

-- Visibility rule for ALL consumers (read API, expiry scan, reports):
-- a document is visible FOR an item only when both the document and that item's
-- link are production.
CREATE VIEW item_document_production AS
  SELECT id.*, d.type, d.regulation, d.validity_from, d.validity_to, d.archive_url
  FROM item_document id JOIN document d USING (doc_id)
  WHERE d.status = 'production' AND id.status = 'production';

-- per written field — GATE rejects candidates missing any of these (Invariant 2)
CREATE TABLE evidence (
  id           bigserial PRIMARY KEY,
  doc_id       bigint NOT NULL REFERENCES document,
  field        text   NOT NULL,             -- e.g. 'validity_to'
  value        text   NOT NULL,
  archive_url  text   NOT NULL,
  page         int,
  verbatim     text   NOT NULL,             -- exact string as read
  tier         text   NOT NULL,
  model_id     text,
  confidence   numeric NOT NULL,
  extracted_at timestamptz NOT NULL
);

-- append-only; no UPDATE/DELETE grants (see 007).
CREATE TABLE audit_log (
  id           bigserial PRIMARY KEY,
  event        text NOT NULL,     -- production-write|approve|reject|edit|supersede|bind-manufacturer|unbind
  doc_id       bigint,            -- soft ref — no FK (audit outlives registry churn)
  item_ref     text,              -- soft ref — no FK
  decided_by   text NOT NULL,     -- 'gate' | user id
  via_job      bigint,            -- C8: SOFT reference — no FK; queue rows prunable on any schedule
  job_snapshot jsonb NOT NULL,    -- C8: type, payload, dedupe_key, claimed_by, timestamps —
                                  -- captured at write time; audit trail self-contained for 10 y
  detail       jsonb,
  at           timestamptz NOT NULL DEFAULT now()
);

-- fetch_log.doc_id -> document (deferred from 003; document now exists).
-- Nullable link set by FETCH on the same-hash/new-URL path (C3). Documents are
-- never deleted (10-y retention), so a hard FK is safe.
ALTER TABLE fetch_log
  ADD CONSTRAINT fetch_log_doc_id_fk FOREIGN KEY (doc_id) REFERENCES document (doc_id);
