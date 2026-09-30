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

- [x] `git pull`; `chmod 600 .env backups/*.dump`. **Live 2026-09-30** (read on the
      server: `.env` 0600, crontab has both lines, hourly L0 ok at 08:07, 09:07,
      10:07 UTC, dumps 4.9-5.6 MB, 3-4 s).
- [x] `./scripts/backup.sh hourly` by hand; `status`.
- [x] crontab (§ 3 of the doc). Alert proof from the server: not verified from here.

## Server, L1 (after Mitja orders the box)

- [x] `sudo apt install restic` (0.18.1); keys in `/srv/compliance/secrets/`;
      `~/.ssh/config` `storagebox` entry; `.env` `BACKUP_REPO`, `BACKUP_PASSWORD_FILE`.
      Main account `u679983`, not a sub-account. 2026-09-30.
- [x] `init`, first `hourly` (13 s, 1.34 GiB stored), `status`, check, dump
      restored from the box byte-identical. Path: `sftp:storagebox:dentalia`.
- [ ] Box snapshots on (Mitja); confirm the sub-account cannot delete under `/.zfs`.
- [ ] Hand the restic password and SSH key to Dentalia's two custodians.

## Restore test (offer)

- [x] 2026-09-30, in a throwaway `ubuntu:26.04` container on dev, with only the
      two keys, via `ProxyJump` through the server (the box answers only inside
      Hetzner): ~5 min to a restored database and archive; all table counts,
      the dump and all 1.572 archive files identical to production.
      docs/dev/backup.md § 9 (and .sl.md).
- [ ] Decide: does this container test count as the offer's restore test
      ("na nov, začasen strežnik"), or is a temporary Hetzner server still run?

## Decided 2026-09-30 (Denis), docs/decisions.md

- Deletion protection: the box's 10 daily snapshots are accepted, no L2. Say
  at handover: 10 days back, nothing survives losing the Hetzner account.
- [ ] Box snapshots: switch on automatic daily snapshots (Hetzner Console).
- [ ] Heartbeat watcher, **later**: pick a service (healthchecks.io recommended),
      set `BACKUP_HEARTBEAT_URL`. Belongs in followups `[backup-heartbeat-watcher]`;
      not written there yet because another session has followups.md emptied
      in its working tree (2026-09-30).
- [ ] Handover: **full Slovene translation** of `docs/dev/backup.md`, kept in
      step with the English one. After the restore test, so the restore sections
      are translated once, as they ran.
