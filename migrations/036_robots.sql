-- 036: robots.txt support for the playbook DISCOVER rung's crawl recipe.
-- Design: docs/superpowers/specs/2026-08-21-playbook-crawl-recipe-design.md §4.1.
-- Two things, both robots-derived, shipped together; the reader that writes
-- them lands in the next slice, so both are inert on arrival.

-- 1. A robots.txt cache, so the runtime check is one request per host per TTL
-- window rather than one per crawl. `body` is kept verbatim (not a parsed
-- rule set) because the parser is stdlib `urllib.robotparser` and re-parsing a
-- cached body is free, while a stored parse would need a migration every time
-- our interpretation changes. `fetched_at` drives the TTL; `crawl_delay_ms` is
-- the value projected onto domain_lease.min_politeness_ms below.
CREATE TABLE robots_cache (
  host           text PRIMARY KEY,
  body           text,                     -- verbatim robots.txt; NULL = fetch failed
  status         int,                      -- HTTP status of the robots.txt fetch
  crawl_delay_ms int,                      -- parsed Crawl-delay for our UA, NULL if none
  fetched_at     timestamptz NOT NULL DEFAULT now()
);

-- 2. The politeness floor a robots.txt Crawl-delay sets, ruled 2026-08-24.
-- `politeness_ms` cannot hold it: `queue.try_domain_lease` rewrites that column
-- from the caller's value on EVERY acquire, and FETCH always passes
-- cfg.fetch.politeness_ms, so a crawl-written 10s delay for renfert is stomped
-- back to the global 2s by the very next fetch.url on that host -- robots
-- obeyed for exactly one request. This column is written ONLY by the robots
-- reader and never by a lease acquire, so config is the floor, robots raises
-- it, and the robots TTL refresh lowers it again when a host drops its
-- Crawl-delay. The lease takes GREATEST(caller, min_politeness_ms).
ALTER TABLE domain_lease ADD COLUMN min_politeness_ms int NOT NULL DEFAULT 0;
