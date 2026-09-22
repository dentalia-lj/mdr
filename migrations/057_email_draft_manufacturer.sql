-- Who a draft is for, on the draft itself.
--
-- `email_draft` has never carried a manufacturer. Renewal drafts borrow one
-- through `renewal_request_id`, and that worked while every draft was a
-- renewal. Migration 056 added `gap-request`, which deliberately leaves
-- `renewal_request_id` NULL -- a gap request renews nothing and must not take
-- a slot in the renewal cadence guard -- so those drafts belong to nobody.
--
-- Two live consequences, both observed 2026-09-03:
--
--   * `/drafts` renders an empty Manufacturer cell for every gap request: the
--     board joins `renewal_request` for that column and there is nothing to
--     join to.
--   * The button that produces them can never tell that it has already asked.
--     `POST /manufacturers/{name}/gap-request` dedupes on an ACTIVE job key,
--     so once the job finishes, pressing again writes a second draft. Draft 25
--     was archived on 2026-09-02 and draft 26 was written anyway -- the
--     archive decision could not stick, because nothing connected the two
--     rows.
--
-- Nullable and additive: renewal drafts keep answering through the join, and
-- the backfill below fills what is already known so the column is not a second
-- source of truth for rows that have one. Readers COALESCE, join first.
ALTER TABLE email_draft ADD COLUMN manufacturer text;

UPDATE email_draft d
   SET manufacturer = r.manufacturer
  FROM renewal_request r
 WHERE r.id = d.renewal_request_id
   AND d.manufacturer IS NULL;

-- The lookup the gap-request guard makes: "is there already a draft of this
-- kind for this manufacturer, in any state". Partial, because it is only ever
-- asked of drafts that carry the column.
CREATE INDEX IF NOT EXISTS email_draft_manufacturer_kind_idx
    ON email_draft (manufacturer, kind)
 WHERE manufacturer IS NOT NULL;
