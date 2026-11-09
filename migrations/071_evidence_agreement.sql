-- 071: the agreeing read on `evidence` (PRD v3 section 5, amended 2026-10-12;
-- extraction-ladder plan, Task 5).
--
-- Two reads that agree on a gate field raise its confidence to
-- 1 - (1 - c1)(1 - c2) when each is at least `gate.agreement_min` (Denis,
-- 2026-10-12, "Gate confidence with Haiku 5.5 at T1"). The row's `model_id`
-- and `verbatim` stay the FIRST read's, the one whose value is stored; these
-- two columns name the read that agreed with it and that read's own
-- confidence, so the combined number in `confidence` can be taken apart
-- again. Denis answered the same day that evidence gets the agreeing read
-- through a migration rather than inside a jsonb.
--
-- Nullable, no default, no backfill: every row written before this file, every
-- T0 and T3 row, and every field one read settled has no agreeing read, and
-- NULL is the true value for all of them.
--
-- No GRANT: 007's `GRANT SELECT ON ALL TABLES` is table-level and covers new
-- columns, and `dentalia_api` must keep no write grant on `evidence`
-- (invariant 1), as migration 014 noted for `extract_rev`.

ALTER TABLE evidence ADD COLUMN agreed_model_id text;
ALTER TABLE evidence ADD COLUMN agreed_confidence numeric;
