# Backup: registry, archive, imports, config (offer 2026092301, 2026-09-28)

Ordered by Dentalia under offer 2026092301 (fixed price, 15 h; our budget 10 h).
Overrules the 2026-09-03 ruling that put `[no-registry-or-archive-backup]` out of
scope. **Simplified 2026-09-28 (Denis):** the standard design, restic from the
distribution's package, no Docker wrapper, no copy chain, no custom restore
script. The process and every restore command: `docs/dev/backup.md`.

## Levels, in the order they come online

| Level | What | Needs |
|---|---|---|
| L0 | hourly verified dump + roles + code.txt in `backups/hourly/`, newest 48 | nothing; can run now |
| L1 | hourly `restic backup` straight to the Storage Box over SFTP | the box (Mitja), `apt install restic` on the server |
| L2 | the same to an outside provider | only if Dentalia wants it |

## Built and rehearsed on dev (2026-09-28, restic 0.14.0)

- [x] `scripts/backup.sh` (hourly, weekly, init, status), `scripts/lib/dump.sh`
      (verified dump, roles, one lock; shared with `deploy.sh`), `scripts/lib/env.sh`.
- [x] `deploy.sh`: shared dump routine; lock; rotation counts only deploy dumps.
- [x] Rehearsal: L0; L1 to a local repository; weekly; partial read; heartbeat;
      alert on L1 failure; `restic restore` + roles + `pg_restore` into an empty
      Postgres (counts identical); archive `cp -an` (identical SHA-256).
- [x] Docs: `docs/dev/backup.md` rewritten for the standard design; pointers,
      decisions, troubleshooting, code-map, README, `.env.example`.
- [ ] Tests: `tests/test_backup_script.py` updated for the simplified script;
      full suite once before commit.
- [ ] Commit (commit-units).

## Server, L0 (needs Denis on the server; nothing from Dentalia)

- [ ] `git pull`; `chmod 600 .env backups/*.dump`.
- [ ] `./scripts/backup.sh hourly` by hand; `status`.
- [ ] crontab (§ 3 of the doc); prove the alert (§ 7).

## Server, L1 (after Mitja orders the box)

- [ ] `sudo apt install restic`; keys in `/srv/compliance/secrets/`; `~/.ssh/config`
      `storagebox` entry; `.env` `BACKUP_REPO`, `BACKUP_PASSWORD_FILE`.
- [ ] `init`, first `hourly`, `status`; confirm the repository path on the real box.
- [ ] Box snapshots on (Mitja); confirm the sub-account cannot delete under `/.zfs`.
- [ ] Hand the restic password and SSH key to Dentalia's two custodians.

## Restore test (offer), after the first documents backfill

- [ ] From a machine that is not the server, with only the keys: doc § 5.3 on a
      temporary server; measure time and loss against the offer; compare
      counts; write the test record; delete the server.

## Open

- Heartbeat watcher (`BACKUP_HEARTBEAT_URL`): which service, whom it alerts.
- Deletion protection beyond the box's 10 daily snapshots: L2 with object lock,
  or accept and tell Dentalia at handover.
- Handover docs in Slovene for Dentalia's IT?
