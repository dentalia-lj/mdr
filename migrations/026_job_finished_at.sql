-- When a job stopped, so the board can say how long it took.
--
-- `job` records when work was queued (`created_at`) and when a worker picked
-- it up (`claimed_at`) and nothing about when it ended. The status board's
-- recent-jobs table therefore carried no time column at all -- not a design
-- choice, an absence: "did last night's sweep finish, and how long did it
-- take" was unanswerable from the UI, and the widest column on that table was
-- the dedupe key.
--
-- Terminal transitions only (`done` and `dead`). A `failed` job is going to
-- run again, so stamping it would make the column mean "when the last attempt
-- stopped", which is a different question and the one `claimed_at` already
-- half-answers. `defer` is explicitly not terminal either -- a batch poll
-- rescheduling itself has not finished.
--
-- Nullable with no backfill, deliberately. Every job already in the table
-- finished at a time this column cannot recover, and inventing one (created_at
-- + something, or now()) would put fiction in an audit-adjacent table to save
-- the UI a NULL check. The board renders an em dash for those instead, which
-- is true.
ALTER TABLE job ADD COLUMN finished_at timestamptz;

-- Duration is `finished_at - claimed_at`, and the board sorts by recency, so
-- this index serves the "what ran recently" question the status page asks.
-- Partial: the NULL rows are every pending job and every pre-migration row,
-- and neither is ever the answer to that question.
CREATE INDEX job_finished_at_idx ON job (finished_at DESC) WHERE finished_at IS NOT NULL;
