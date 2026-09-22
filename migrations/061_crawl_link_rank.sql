-- 061: judged crawl links, once per library version.
-- Ruling 2026-09-04 (PHASES §D, [crawl-ai-link-triage]): a document-library
-- page's links are judged by the T1 link ranker -- type and article numbers per
-- link -- and the fetch budget is spent in that order. The judgement is a
-- property of the LIBRARY, not of the group that happened to crawl it: NSK has
-- 136 groups and every discover.group re-reads the same index page, so without
-- this table the same 1.329 links would be judged 136 times for one answer.
--
-- Keyed on the HARVEST (sha256 over the resolved links and their texts in page
-- order), not on the raw HTML, which carries timestamps and session tokens that
-- would miss the cache on every read. A library that changes its list changes
-- the key and is judged again; the old row is left, it is a record of what was
-- judged when. The per-group ORDER (which of our article numbers a link names)
-- is computed from `judgements` at read time and never stored.
--
-- Worker-only, like robots_cache (036): no grant to dentalia_api on purpose --
-- the UI has no reason to read a fetch-order hint, and must never write one.
CREATE TABLE crawl_link_rank (
  index_url    text        NOT NULL,
  harvest_hash text        NOT NULL,        -- sha256 over [[url, text], ...]
  model_id     text,                        -- which model judged; NULL for an injected judge
  judged_at    timestamptz NOT NULL DEFAULT now(),
  judgements   jsonb       NOT NULL,        -- [{url, type, article_numbers, confidence, reason}]
  PRIMARY KEY (index_url, harvest_hash)
);
