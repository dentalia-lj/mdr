-- 010_extraction_cost.sql
-- The economics ledger: one row per LLM tier CALL, with measured token usage and
-- its USD cost. T0 is deterministic and free -> it never writes a row. Feeds the
-- S0.4 findings (real per-model $/doc) and the S1.6 KPI board "sweep spend vs
-- budget cap". Turns the chars-based estimate into measured ground truth.
--
-- written by: extract.doc (sync: all tiers at finalize; batch: each tier as its
--             batch resolves -- usage realizes per defer cycle, C9).
--
-- Keyed by (content_hash, tier, extract_rev), NOT an FK to extraction_attempt:
-- the batch path commits a tier's cost row as that tier resolves, BEFORE the
-- attempt row is written at finalize (incremental-commit crash-safety, same model
-- as batch_ref). The UNIQUE on that triple is the idempotency guard -- a batch
-- retry re-fetches results and re-writes; ON CONFLICT DO NOTHING makes it a no-op.
-- Join to extraction_attempt on (content_hash, extract_rev).
--
-- cost_usd is NULLABLE on purpose: an unpriced model_id still records its tokens
-- (cost null) rather than silently costing 0. Query `WHERE cost_usd IS NULL` to
-- find pricing gaps (never-silent rule).

CREATE TABLE extraction_cost (
  id                     bigserial     PRIMARY KEY,
  content_hash          text          NOT NULL,
  tier                  text          NOT NULL,          -- T1 | T2 (T0 is free)
  extract_rev           int           NOT NULL DEFAULT 1,
  model_id              text          NOT NULL,
  batch                 boolean       NOT NULL DEFAULT false,
  input_tokens          int           NOT NULL DEFAULT 0, -- non-cached input (Anthropic accounting)
  output_tokens         int           NOT NULL DEFAULT 0,
  cache_read_tokens     int           NOT NULL DEFAULT 0,
  cache_creation_tokens int           NOT NULL DEFAULT 0,
  cost_usd              numeric(12,6),                    -- null = model unpriced (tokens still kept)
  at                    timestamptz   NOT NULL DEFAULT now(),
  UNIQUE (content_hash, tier, extract_rev)
);

CREATE INDEX extraction_cost_hash_idx ON extraction_cost (content_hash, extract_rev);

-- Rollup for the KPI board / findings: spend by model x tier x transport, with a
-- count of unpriced calls so a pricing gap is visible rather than under-counted.
CREATE VIEW extraction_spend AS
SELECT
  model_id,
  tier,
  batch,
  count(*)                                   AS calls,
  sum(input_tokens)                          AS input_tokens,
  sum(output_tokens)                         AS output_tokens,
  sum(cost_usd)                              AS cost_usd,
  count(*) FILTER (WHERE cost_usd IS NULL)   AS unpriced_calls
FROM extraction_cost
GROUP BY model_id, tier, batch;

-- API (dentalia_api) renders the spend KPI -> SELECT only. New objects, created
-- after 007's blanket grant, so grant explicitly. No write grant: the worker
-- (owner role) writes the ledger, the API only reads it.
GRANT SELECT ON extraction_cost TO dentalia_api;
GRANT SELECT ON extraction_spend TO dentalia_api;
