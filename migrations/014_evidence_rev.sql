-- 014_evidence_rev.sql
-- [gate-evidence] ruling (Denis, 2026-07-31): a rev-N+1 re-extraction of a
-- production document was overwriting `document`'s field columns while
-- `evidence` stayed guarded NOT EXISTS per (doc_id, field) — production
-- values ended up pointing at rev-1 evidence (invariant-2 break). Evidence
-- now scopes per extraction revision: the NOT-EXISTS idempotency guard
-- becomes (doc_id, field, extract_rev), so every write carries evidence from
-- the SAME extraction that produced it, and prior revisions' evidence is kept
-- (append-only — no UPDATE/DELETE grants, unchanged from 005/007).

ALTER TABLE evidence ADD COLUMN extract_rev int NOT NULL DEFAULT 1;

-- Backing index for the new per-rev idempotency guard (gate._insert_evidence)
-- and for "give me the evidence for exactly this revision" reads. No new GRANT
-- needed: 007's blanket `GRANT SELECT ON ALL TABLES` already covers this column.
CREATE INDEX evidence_doc_field_rev_idx ON evidence (doc_id, field, extract_rev);
