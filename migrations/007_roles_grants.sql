-- 007_roles_grants.sql
-- Invariant: "The FastAPI process is a job PRODUCER only — it has zero write
-- grants on document / item_document / evidence." (CLAUDE.md architecture para +
-- Invariant 1). Human decisions enqueue gate.apply; only GATE handlers, running
-- as the owner role, write the registry.
--
-- This file grants the least privilege the api service needs:
--   - SELECT on every table and view (it reads the whole model for the UI/API);
--   - INSERT + UPDATE on `job` only (it enqueues gate.apply / interactive re-runs);
--   - USAGE on job's identity sequence so those INSERTs can allocate ids.
-- It deliberately grants NO INSERT/UPDATE/DELETE on document, item_document,
-- evidence, or audit_log — SELECT only. The owner role `dentalia` (POSTGRES_USER)
-- owns every object and retains full access implicitly.
--
-- Roles are cluster-global; creation is guarded so re-application is a no-op
-- (the runner applies each file once, but the role may pre-exist from another DB
-- in the same cluster). This is the ONLY file that needs an existence guard.
--
-- S0.3 wires the `api` compose service to connect as this role and adds the
-- enforcement test (attempt a write to document/item_document/evidence -> denied).

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'dentalia_api') THEN
    CREATE ROLE dentalia_api LOGIN;   -- no password (peer/local dev; prod sets it out of band)
  END IF;
END
$$;

-- Schema access (USAGE is granted to PUBLIC by default on `public`, but make it
-- explicit so the grant set is self-describing and survives a locked-down schema).
GRANT USAGE ON SCHEMA public TO dentalia_api;

-- Read the whole model — all tables and views.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO dentalia_api;

-- Producer writes: enqueue only. INSERT + UPDATE on `job`, nothing else.
GRANT INSERT, UPDATE ON job TO dentalia_api;

-- Allocate job ids on INSERT (bigserial -> sequence job_id_seq).
GRANT USAGE ON SEQUENCE job_id_seq TO dentalia_api;

-- NOTE (belt-and-braces): no grant is emitted for INSERT/UPDATE/DELETE on
-- document, item_document, evidence, or audit_log. dentalia_api holds SELECT on
-- them (via ALL TABLES above) and nothing more. Do not add write grants here —
-- doing so breaks Invariant 1 and the S0.3 enforcement test.
