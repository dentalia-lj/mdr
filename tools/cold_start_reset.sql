-- Corpus-first cold start: reset the registry, keep the reference data.
--
-- Destructive and deliberate. Run once, before the first full ingest + corpus
-- backfill. Everything it drops is regenerable: the queue is regenerable state
-- by design (CLAUDE.md "registry-derives-queue"), and the registry rows present
-- when this was written were dev-run residue -- 4 `local://seed/*.pdf` rows from
-- tests/fixtures/seed_ui.py and 15 pointing into tests/fixtures/corpus/.
--
-- pytest never touches this database: tests/conftest.py drops and creates a
-- separate `dentalia_test`. This script is for the real `dentalia` DB only.
--
-- KEPT, deliberately:
--   vendor_master        real S1.8 vendor-code import (390 rows)
--   manufacturer_alias   the code -> canonical map derived from it (389 rows)
--   schema_migrations    migration ledger; wiping it would re-run every migration
--
-- WHY item_mirror IS IN THE LIST. It looks like real catalogue data worth
-- keeping, and dropping it looks wasteful. It is not optional. INGEST emits
-- resolve.group only for rows whose comparand tuple CHANGED
-- (app/handlers/ingest.py, `_DIFF_COLS`). Truncating item_group while leaving
-- item_mirror populated means the next ingest diffs those rows as `unchanged`,
-- emits no resolve.group for them, and they end up in the mirror with no group
-- at all -- invisible to the pipeline and to every UI page, with nothing
-- counted anywhere. Wipe both or neither.
--
-- fetch_log matters for the same class of reason: backfill.scan skips any file
-- whose content_hash is already in it. All 22 hashes recorded there are also
-- present in imports/dentalia-sftp (the committed test fixtures are copies of
-- real corpus files), so leaving it would silently route 22 genuine documents
-- to the `seen` counter instead of extraction.

BEGIN;

TRUNCATE TABLE
    audit_log,
    batch_ref,
    data_anomaly,
    discovery_log,
    document,
    document_text,
    domain_lease,
    eudamed_mirror,
    evidence,
    extraction_attempt,
    extraction_cost,
    fetch_log,
    grouping_suggestion,
    item_document,
    item_group,
    item_group_member,
    item_mirror,
    job,
    manual_task,
    manufacturer,
    renewal_request,
    scheduler_run,
    upload_inbox
RESTART IDENTITY CASCADE;

-- Proof the keeps survived. No foreign key anywhere in the schema points at
-- vendor_master or manufacturer_alias, so the CASCADE above cannot reach them;
-- this is the assertion, not the mechanism.
SELECT 'vendor_master' AS kept, count(*) FROM vendor_master
UNION ALL
SELECT 'manufacturer_alias', count(*) FROM manufacturer_alias
UNION ALL
SELECT 'schema_migrations', count(*) FROM schema_migrations;

COMMIT;
