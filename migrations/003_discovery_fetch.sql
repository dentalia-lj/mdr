-- 003_discovery_fetch.sql
-- Sketch §3 (discovery & fetch).
-- Encodes: Invariant 6 (unchanged content never re-fetched — fetch_log holds
--          ETag / hash / recency), free deterministic EUDAMED lookup.
-- fetch_log.doc_id is a nullable link to document (registry, created in 005);
-- the FK constraint is added in 005 once `document` exists (file ordering:
-- later files FK-reference earlier tables).

-- written by: discover.group · feeds failure monitor + hit-rate stats
CREATE TABLE discovery_log (
  id       bigserial PRIMARY KEY,
  group_id bigint REFERENCES item_group,
  source   text NOT NULL,     -- known-url|playbook|eudamed|search|vendor|email|manual
  outcome  text NOT NULL,     -- hit|miss|skipped
  detail   jsonb,
  at       timestamptz NOT NULL DEFAULT now()
);

-- written by: fetch.url, backfill.scan, email.poll, upload.ingest · read by: fetch.url (skip), discover.group (recency)
CREATE TABLE fetch_log (
  url_normalized  text PRIMARY KEY,
  etag            text,
  last_modified   text,
  content_hash    text,                     -- sha256
  fetched_at      timestamptz,
  last_checked_at timestamptz,
  source          text NOT NULL,            -- live|backfill|email|upload
  doc_id          bigint                    -- FK -> document (added in 005), nullable
);
CREATE INDEX fetch_log_hash_idx ON fetch_log (content_hash);

-- written by: eudamed.sync · read by: discover.group (free deterministic lookup)
CREATE TABLE eudamed_mirror (
  udi_di           text PRIMARY KEY,
  basic_udi_di     text,
  device_name      text,
  manufacturer_srn text,
  cert_refs        jsonb,
  synced_at        timestamptz NOT NULL
);
