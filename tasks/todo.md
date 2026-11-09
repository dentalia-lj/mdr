# Monday 2026-11-09: Nataša's review feedback, continued

Done 2026-11-06 and deployed (server on `1cc1274`): doc 16 back in production
under Chemichl AG (jobs 28438 reopen, 28439 bind); BC brand names in both
manufacturer lists (`099dc1b`); INGEST mirrors `NI MP` items as `md_flag=FALSE`,
never grouped (`5a108af`); BC sync run by hand, manufacturers then items (job
28461: 20.355 seen, 4.121 changed, 3.560 non-MD; CATTANI 087 now 99 items).

- [ ] Check in the UI that doc 894's "Add items this document covers..." finds the
      CATTANI compressors (search "AC" or "kompresor"). Not checked 2026-11-06.
- [ ] Reply to Nataša, task 5: BC to us is a manual weekly sync for now; us to BC
      is the item button and the bulk push, the daily drift push is off. Turning
      the weekly read on means building the cron first: designed in
      `docs/superpowers/specs/2026-10-05-bc-weekly-sync-design.md` § 4, not built.
- [ ] Reply to Nataša, task 9: one Word document for her issues? IFUs in search
      (auto-search stays off).
- [ ] Remove linked items in the picker, Nataša's must-fix
      (`[review-remove-linked-items]`): spec first. Needs a new `remove-link`
      decision (PRD change), an explicit removal list, a warning when an item
      would be left with no current document, a BC push flag, and re-ticking a
      refused item = reopen + confirm.
- [ ] Waiting on Nataša, then doc 188 fixes: which items (55002-MULTI, 55065,
      55066); why MIYO and JENSEN are two BC codes; doc 506 (older 2023 MIYO DoC);
      CATTANI cannulas MD or not. Then the T0 fixes (brand alias inside a product
      list; "Decla ration" and "Izjava EU o skladnosti" DoC markers, measured on
      the corpus first) and a manufacturer correction on Review (spec).
- [ ] Denis to decide: should a whole-range bind link non-MD items; when to turn on
      `SCHEDULER_BC_PUSH_DRIFT_ENABLED` (after her review); MIYO/JENSEN device
      class in BC (doc 16 links 0 items until it is set).

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
- [x] It counts as the offer's restore test (Denis, 2026-09-30); no
      temporary Hetzner server.

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
