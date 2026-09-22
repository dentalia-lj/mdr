-- 008_api_role_dev_password.sql
-- 007 creates `dentalia_api` with `LOGIN` and no password (peer/local dev —
-- the migration runner and CLI connect locally as the owner role and never
-- needed a password). The standalone web container connects to Postgres over
-- TCP as `dentalia_api` (Invariant 1 — job producer only, enforced by the 007
-- grants), and the stock `postgres:16` image defaults to scram-sha-256, which
-- rejects a passwordless LOGIN role over TCP. This sets a fixed DEV-ONLY
-- password so `docker compose up` works out of the box.
--
-- Matches `app/config.py` Web.api_database_url default
-- (postgresql://dentalia_api:dentalia_api@...). Prod sets a real password out
-- of band (ALTER ROLE ... PASSWORD, then update the deployed API_DATABASE_URL)
-- — same posture as the 007 comment on the role itself.
--
-- EDITED IN PLACE 2026-09-18, by Denis's ruling, and deliberately against the
-- never-edit-an-applied-migration rule (docs/dev/changing-things.md). The role
-- is cluster-global while this file runs once per DATABASE, so every fresh
-- database migrated in the same cluster ran it again -- schema-drift on every
-- scripts/deploy.sh, the test suite's own database, bringup-rehearsal -- and
-- the old unconditional ALTER put the published literal back over a real
-- password: on a client server, the first deploy after the operator set one
-- would have cut `web` off. A new numbered file cannot fix that, because the
-- harm is this file running on fresh databases. The edit is safe to make in
-- place: a role password is no schema object, so schema-drift cannot see it
-- and no long-lived database diverges from the tree.
--
-- Now: the literal only when the role has no password at all (a fresh cluster,
-- where 007 created it without one); otherwise only LOGIN, and whatever
-- password was set stays. Reads pg_authid, so it needs the superuser the
-- compose `postgres` service gives the migration runner.

DO $$
BEGIN
  IF (SELECT rolpassword IS NULL FROM pg_authid WHERE rolname = 'dentalia_api') THEN
    ALTER ROLE dentalia_api LOGIN PASSWORD 'dentalia_api';
  ELSE
    ALTER ROLE dentalia_api LOGIN;
  END IF;
END
$$;
