-- 030_email_body_summary.sql
-- S2.4 EMAIL inbound: what an email ASKED FOR, without keeping what it said.
--
-- `/emails` could show which document arrived and in what state, but not why
-- the message was sent -- "I just found a DOC for Nexco that expired in
-- 04-05-2026" is the whole context, and it lives only in the body. Persisting
-- supplier correspondence verbatim is not something this registry should do
-- (10-year retention, GDPR surface, and it is not evidence). So the body is
-- read into memory at poll time, summarised by the cheap tier, and dropped:
-- what lands here is the summary.
--
-- Invariant 12 was widened for exactly this on 2026-08-20 (CLAUDE.md, PRD §4,
-- spec §6) and ratified the same day. The summary is UI CONTEXT ONLY -- never
-- evidence, never a production value, never a GATE or VALIDATE input, and
-- never re-read by another stage. Nothing joins to it.
--
-- Written by: email.poll (S2.4). Read by: web /emails (dentalia_api, SELECT).
--
-- Run exactly once per message, by construction rather than by a flag: a
-- message is summarised on the single poll that records it, and
-- `email_poll_log_uid_key` means it is never processed again. A FAILED summary
-- still records its row (with `summary_status`), so a failure does not retry
-- either -- a missing line of UI context is not worth a second call.

ALTER TABLE email_poll_log
  -- The summary itself: one or two sentences, capped in the prompt. NULL when
  -- no call was made or the call failed -- `summary_status` says which.
  ADD COLUMN body_summary   text,
  -- What the message was for, as a closed vocabulary the UI can render as a
  -- chip and a human can scan a list by:
  --   sends-documents | requests-info | acknowledges | other
  -- Not enforced by a CHECK: the value comes from a model, and an unexpected
  -- string must be visible in the UI rather than dead-letter a poll.
  ADD COLUMN summary_intent text,
  -- Why there is (or is not) a summary. Never NULL once 030 is live, so a
  -- blank summary always carries its reason (never-silent):
  --   ok | disabled | no-body | too-short | llm-error
  ADD COLUMN summary_status text,
  -- Which model produced it. Recorded next to the text in the UI so a summary
  -- is never mistaken for the sender's own words.
  ADD COLUMN summary_model  text,
  -- Measured, not assumed. The volume is expected to be small and the cost
  -- negligible -- these three columns are how that stays a checkable claim
  -- rather than a belief. cost_usd NULL = unpriced model, tokens still kept
  -- (same never-silent rule as extraction_cost).
  ADD COLUMN summary_input_tokens  int,
  ADD COLUMN summary_output_tokens int,
  ADD COLUMN summary_cost_usd      numeric(12,6);

-- Deliberately NOT indexed and NOT joined: this is display context on a row
-- the UI already fetches by recency. An index here would imply it is a lookup
-- key, which is precisely what invariant 12 says it must never become.

-- email_poll_log's SELECT grant (028) is table-wide and covers new columns.
-- No write grant: the web producer never writes here (invariant 1).
