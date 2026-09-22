-- 059_playbook_probe.sql
-- The playbook PROBE: a look at a manufacturer's document library that emits
-- nothing (spec: docs/superpowers/specs/2026-09-03-playbook-probe-wizard-design.md,
-- §3.2). Inert on arrival -- the handler that writes these rows lands with it,
-- the UI that reads them is a later slice.
--
-- Why a side table rather than the job payload: payloads are immutable after
-- enqueue (invariant 9), so a result cannot be written back into the job. This
-- is the same shape `batch_ref` already uses for Batch API ids.
--
-- Why this exists at all: an operator must be able to look at a library
-- WITHOUT committing the pipeline to fetch it. NSK's index yields 1.329 PDFs;
-- authoring a recipe blind and finding out afterwards is how you spend a
-- manufacturer's goodwill and your own extraction budget on a guess.

CREATE TABLE playbook_probe (
  id             bigserial PRIMARY KEY,
  slug           text        NOT NULL,   -- soft ref: a probe outlives a rename
  index_url      text        NOT NULL,
  requested_by   text,                   -- X-Forwarded-User, per §7 q2
  via_job        bigint,                 -- soft ref (invariant 10), never an FK
  status         text        NOT NULL,   -- pending | done | refused | failed
  robots_verdict text,                   -- allow | disallow | unreachable
  -- Which parse produced the links, per the 2026-08-21 ladder ruling: measured
  -- per attempt, never authored per manufacturer.
  tier           text,                   -- static | rendered
  -- The crawl design's §4.6 breakdown, verbatim and whole. jsonb rather than
  -- columns so the harvester can learn to count one more thing without a
  -- migration -- these rows are a measurement, not registry truth.
  counts         jsonb,
  -- A sample of resolved links with what each reads as. Bounded by the writer,
  -- not by a constraint: a probe of a 1.329-link index must not put 1.329 rows
  -- of someone else's URLs in our database to show an operator twenty.
  sample         jsonb,
  error          text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  finished_at    timestamptz
);

-- The UI reads the latest probe for one playbook, which is the only access
-- pattern this table has.
CREATE INDEX playbook_probe_slug_idx ON playbook_probe (slug, created_at DESC);

-- Retention: probe rows are DEBRIS, not registry. A probe is a measurement of
-- somebody else's website at a moment; nothing downstream reads it, no value
-- derives from it, and it is never evidence. The 10-year append-only rule
-- governs documents and does not reach here -- deliberately, and said out loud
-- so nobody later mistakes silence for an oversight. The scheduler sweep drops
-- rows older than 30 days.

-- `dentalia_api` is the web role (migrations 007/008). It INSERTs a probe
-- request row and SELECTs the result; the worker updates it. Invariant 1 is
-- untouched -- this is not document/item_document/evidence -- but the grant is
-- written explicitly rather than inherited, per docs/dev/changing-things.md.
GRANT SELECT, INSERT ON playbook_probe TO dentalia_api;
GRANT USAGE, SELECT ON SEQUENCE playbook_probe_id_seq TO dentalia_api;
