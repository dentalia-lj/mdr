-- 006_phase2_declared.sql
-- Sketch §6 (Phase 2 tables — DECLARED, not built/used in Phase 1).
-- Schema exists so Phase 1 FKs and views can be reasoned about; no handler
-- writes these until Phase 2 (email-out state machine, playbook onboarding).

-- read by: discover.group (playbook), email.request (contacts), failure monitor (stats)
CREATE TABLE manufacturer (
  id             bigserial PRIMARY KEY,
  canonical_name text UNIQUE NOT NULL,
  contact_emails text[],
  playbook_ref   text,            -- path in playbook git repo
  eudamed_srn    text,
  hit_rate_stats jsonb,
  quirks         jsonb            -- {bot_wall: true, login_portal: true, ...}
);
-- C4: manufacturer-scope bindings are derivable as a view over audit_log
-- (event='bind-manufacturer' minus 'unbind') or materialized here as binding_doc_ids.

-- written by: email.request/email.reminder/email.poll (state transitions)
CREATE TABLE renewal_request (
  id               bigserial PRIMARY KEY,
  doc_id           bigint REFERENCES document,
  state            text NOT NULL,      -- due|requested|awaiting|received|parsed|escalated
  email_thread_ref text,
  created_at       timestamptz,
  updated_at       timestamptz
);
