-- What a job actually did. Handlers already compute counts and samples and
-- return them; until now the runner discarded the value. Written in the same
-- transaction that finishes the job, so a result and its terminal status can
-- never disagree.
ALTER TABLE job ADD COLUMN result jsonb;

-- Standing ledger of data weirdness. Append-only in practice: a repeat
-- observation bumps last_seen and the counter rather than inserting again,
-- so the table stays the size of the problem, not the size of the corpus.
--
-- `kind` is a CLOSED vocabulary mirrored in app/results.py ANOMALY_KINDS.
-- Adding a kind is a deliberate act in both places, like a job type.
CREATE TABLE data_anomaly (
  id         bigserial PRIMARY KEY,
  kind       text NOT NULL,
  subject    text NOT NULL,          -- item_ref, manufacturer code, content_hash
  catalogue  text,
  detail     jsonb,
  seen_count int  NOT NULL DEFAULT 1,
  first_seen timestamptz NOT NULL DEFAULT now(),
  last_seen  timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX data_anomaly_key ON data_anomaly (kind, subject);
CREATE INDEX data_anomaly_kind_idx ON data_anomaly (kind);

GRANT SELECT ON data_anomaly TO dentalia_api;
