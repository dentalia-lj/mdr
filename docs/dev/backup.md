# Backup and restore

**Status:** live, written 2026-09-28. Owns **everything about backups**: what
is protected, the levels, what runs by itself and when, what a person does,
how to restore, what to do when something goes wrong, and what this does
**not** protect against. Ordered under offer 2026092301.

The design is the standard one: a verified `pg_dump` on the server, and
[restic](https://restic.readthedocs.io/) from the distribution's package
backing up to a Hetzner Storage Box over SFTP. One script,
[`scripts/backup.sh`](../../scripts/backup.sh), run by cron. Every restore is
plain restic and plain Postgres commands, written out below.

---

## 1. What is protected

| Data | Where it lives | Can it be recreated if lost? |
|---|---|---|
| Database: registry, evidence, audit log, reviewers' decisions, playbooks, fetch history | Postgres (`PGDATA_HOST`) | **No.** Reviewers' decisions are gone for good; extractions only by paying again |
| Archive: every downloaded document | `ARCHIVE_HOST` | **No.** Manufacturers withdraw documents; email attachments arrive once |
| Imports: corpus and BC exports | `IMPORTS_HOST` | Partly, by asking again |
| Config: `.env`, `caddy/users/` | the checkout | Only by hand |
| Code | GitHub | Yes. Each backup records **which commit** ran (`code.txt`) |

**Targets** (from the offer): at most **1 hour** of data lost, at most
**4 hours** from deciding to restore to a working system on a new server.

---

## 2. The levels

| Level | Where | What | When | Protects against |
|---|---|---|---|---|
| **L0** | this server, `backups/hourly/` | the database dump (proven restorable), roles, `code.txt`; newest 48 kept. Plus `deploy.sh`'s dump before every migration (newest 5) | hourly | a bad import, a wrong repair, a bad migration |
| **L1** | Hetzner Storage Box, restic (`BACKUP_REPO`) | the newest L0 dump, roles and `code.txt`, the archive, the imports, `.env`, `caddy/users/` | hourly, right after L0 | losing the disk or the server |
| **L2** (optional) | an outside provider, restic (`BACKUP_REPO_2`) | the same as L1, as its own repository | hourly | losing or being locked out of the Hetzner account |

**Until L1 runs there is no backup in the offer's sense.** L0 lives on the
disk it protects. The archive needs no L0 copy: it is never changed or deleted
by the pipeline, so on the server it is its own copy, and L1 is its backup.

### What the server can and cannot destroy

The offer promises that "the server cannot delete or overwrite the backups".
Exactly:

| | Can the server, or someone who breaks into it, destroy it? |
|---|---|
| L0 | **Yes.** It is the server's own disk |
| L1, the repository | **Yes.** The server's SFTP login writes there and prunes there |
| L1, Storage Box snapshots | **No.** Hetzner's own daily copies of the whole box: read-only under `/.zfs/snapshot`, managed only in the Hetzner Console. BX11 keeps 10: **10 days** back |
| L2 | Only if the provider allows it. With object lock / versioning, **no** |

So the promise holds through the Storage Box's snapshots (and L2 with object
lock), for 10 days. Say this to Dentalia at handover.

---

## 3. What runs by itself

Crontab of user `denis` on the server (no root). Times are UTC.

```cron
7 * * * *   cd /srv/compliance/app && ./scripts/backup.sh hourly >> backups/cron.log 2>&1
37 3 * * 0  cd /srv/compliance/app && ./scripts/backup.sh weekly >> backups/cron.log 2>&1
```

| When | What | If it fails |
|---|---|---|
| hourly at :07 | **L0:** dump, trial-restore it into a scratch database, keep it with roles and `code.txt`; drop dumps beyond 48. **L1/L2:** `restic backup` of the newest dump, the archive, imports and config, to each configured level. After a good L1 backup, ping the heartbeat | alert "hourly failed" (L0), or "L1 failed" with L0 kept. Repeats every hour until fixed |
| Sundays 03:37 | `restic forget --prune` (48 hourly, 14 daily, 8 weekly, 12 monthly, 10 yearly) and `restic check --read-data-subset=5%` on each level | alert "prune failed" / "failed its check" |
| every deploy | `deploy.sh` dumps before migrating | the deploy stops |

A level not set in `.env` is skipped, so the crontab is the same before and
after the Storage Box exists.

- **One lock.** `deploy.sh` and `backup.sh` share `backups/.lock`: a deploy
  waits for a backup (up to 15 min), a backup for a deploy (up to 30 min).
- **Private files.** Dumps, roles and `code.txt` are created 0600: they are the
  whole registry, and the server is shared with Dentalia's PocketBase user.
- **One log line per run** in `backups/backup.log`:
  ```
  2026-09-28T12:30:49Z mode=hourly level=L0 result=ok dump=7.0M secs=7
  2026-09-28T12:30:50Z mode=hourly level=L1 result=ok secs=1
  ```
  `result=PARTIAL` means restic saved a snapshot but could not read some files;
  an alert says so, and the snapshot counts.

### Alerts, and what the server cannot report

Failures alert to `ALERTS_WEBHOOK_URL` (the pipeline's ntfy channel), high
priority, titled `Dentalia backup: ...`.

**A dead server, a stopped cron, or a broken alert channel cannot report
itself.** That is what `BACKUP_HEARTBEAT_URL` is for: an outside watcher
(healthchecks.io, Better Stack, Uptime Kuma, or similar) that the server pings
after every good L1 backup and that alarms when the pings stop for 2 hours.
**Until one is set, "more than 2 hours without a backup triggers an alert"
holds only while the server, cron and ntfy all work.** Open decision, § 8.

To see where things stand: `./scripts/backup.sh status`.

---

## 4. What a person does

| What | Who | When |
|---|---|---|
| Keep the restic password and the Storage Box SSH key | two people at Dentalia, plus Denis | always (§ 7) |
| Read backup alerts and heartbeat alarms | whoever receives them | when one arrives (§ 6) |
| Back up before a risky operation | whoever runs it | before a repair tool with `--apply`, a big backfill, manual SQL: `./scripts/backup.sh hourly` |
| Restore test | Denis | at handover; quarterly if ordered (§ 5.3) |
| Turn on Storage Box snapshots | Mitja | once, Hetzner Console, automatic daily |

### Operating it: on, off, status, run now

All on the server, as `denis`, in `/srv/compliance/app`.

| To | Do | Effect |
|---|---|---|
| **See the state** | `./scripts/backup.sh status` | last runs from the log, newest L0 dumps, latest snapshots per level |
| **See the schedule** | `crontab -l` | the two lines of § 3, or nothing |
| **Read the history** | `tail -20 backups/backup.log`; errors in `backups/cron.log` | one line per run and level |
| **Run a backup now** | `./scripts/backup.sh hourly` | exactly what cron runs; waits if a deploy holds the lock |
| **Run the weekly now** | `./scripts/backup.sh weekly` | prune and check every configured level |
| **Use restic by hand** (`check`, `snapshots`, `find`, `restore`) | first `export RESTIC_REPOSITORY=sftp:storagebox:dentalia RESTIC_PASSWORD_FILE=/srv/compliance/secrets/restic-password`; without them restic stops with "Please specify repository location" | plain restic against L1 |
| **Turn everything on** | `crontab -e`, add the two lines of § 3 | hourly from the next :07 |
| **Pause everything** | `crontab -e`, put `#` in front of both lines | nothing runs; existing dumps and snapshots stay. **No alert says backups stopped** (only the heartbeat watcher would, § 3) |
| **Resume** | remove the `#` again | the next :07 runs normally; nothing to catch up |
| **Turn L1 (or L2) off, keep L0** | comment out `BACKUP_REPO` (or `BACKUP_REPO_2`) in `.env` | the next run makes only the L0 dump; the repository and its snapshots stay untouched |
| **Turn L1 on again** | uncomment the line | the next run continues in the same repository, uploading only what changed |
| **Remove it for good** | delete the two cron lines; `backups/hourly/` can then be deleted | the Storage Box repository is **not** deleted by any of this; that is a separate, deliberate step in the Hetzner Console |

Stopping only needs cron. Nothing else runs in the background: no service, no
container, no daemon.

---

## 5. Restore

Restoring needs only restic, Docker and the checkout. **Any restic from 0.14 on
reads the repository** (restic's format 2, "readable using restic 0.14.0 or
newer"), so the distribution's package on whatever machine is at hand is
enough. Rehearsed 2026-09-28 with Debian's restic 0.14.0 (§ 9).

For every restic command below, first:

```bash
export RESTIC_REPOSITORY='sftp:storagebox:dentalia'     # L1; see § 7 for the SSH setup
export RESTIC_PASSWORD_FILE=/srv/compliance/secrets/restic-password
```

### 5.1 Bad data, server fine: from L0

A wrong import, a repair that went wrong, a bad migration.

```bash
docker compose stop worker web
ls -1t backups/hourly/*.dump | head              # pick the last one from before it happened
cat backups/hourly/<stamp>.code.txt              # the commit it ran on
docker compose exec -T postgres sh -c 'psql -q -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE \"$POSTGRES_DB\" WITH (FORCE)" -c "CREATE DATABASE \"$POSTGRES_DB\""'
docker compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --exit-on-error' < backups/hourly/<stamp>.dump
git checkout <commit>                            # only if a deploy caused it; else deploy.sh re-applies the bad migration
./scripts/deploy.sh                              # starts worker and web, verifies
```

The deploy dumps (`backups/<stamp>-ran-<commit>.dump`) work the same way:
runbook § Rolling back.

**What rolling back means**, besides losing work done after the dump:

- Jobs finished after the dump run **again**: extraction is paid twice, and
  `extraction_cost` forgets the first payment. Batches submitted after it are
  submitted again.
- Values pushed to BC after the dump **stay in BC** while `bc_push_log`
  forgets them; the drift check then misses the difference. List them first
  if BC writing is on.
- Email drafts marked sent go back to unsent: do not send them twice.
- Harmless: the IMAP poll only reads.

### 5.2 One archive file

```bash
restic find --host dentalia '<file name>'        # which snapshots hold it
umask 077; restic restore <snapshot> --target /srv/compliance/restore --include '<file name>'
sudo cp -an /srv/compliance/restore/srv/compliance/archive/. /srv/compliance/archive/
rm -rf /srv/compliance/restore
```

`cp -an` only adds what is missing. A damaged file must be moved away first
(`sudo mv`: the archive is owned by root).

### 5.3 A new server: from L1

The real case: the server is gone, a new one gets whatever restic its
distribution ships, and restores from the Storage Box.

**First:** if the old server may still run, stop it (a half-dead worker keeps
polling IMAP, pushing to BC and spending money). If it was broken into, revoke
its Storage Box SSH key in the Hetzner Console and restore from a box snapshot
(§ 5.4).

Needed: the restic password and the Storage Box SSH key (§ 7); disk for twice
the archive.

1. Docker, the user in the `docker` group, the checkout in
   `/srv/compliance/app`, the directories: [deployment.md § 1](deployment.md).
   Then `sudo apt install restic`.
2. The password and SSH key into `/srv/compliance/secrets/` (0700 directory,
   0600 files), the `storagebox` entry into `~/.ssh/config` (§ 7), and the two
   `export`s above.
3. Get everything:
   ```bash
   umask 077
   restic snapshots --latest 3
   restic restore latest --target /srv/compliance/restore
   R=/srv/compliance/restore/srv/compliance      # restic keeps the original paths
   cat $R/app/backups/offsite/code.txt           # the commit the server ran
   ```
4. `git checkout <commit>` in the checkout.
5. Config: `cp $R/app/.env .env && chmod 600 .env`, and
   `cp -a $R/app/caddy/users/. caddy/users/`. Check the paths in `.env`
   (`PGDATA_HOST`, `ARCHIVE_HOST`, `IMPORTS_HOST`, `BACKUP_*`) exist here.
6. Files: `sudo cp -an $R/archive/. /srv/compliance/archive/` and
   `cp -an $R/imports/. <IMPORTS_HOST>/`.
7. Database, into the new, empty Postgres. Wait over **TCP**: the image first
   runs a temporary server on the socket only, and a socket check passes while
   that one is about to shut down (the 2026-09-28 rehearsal failed exactly so):
   ```bash
   docker compose up -d postgres
   until docker compose exec -T postgres sh -c 'pg_isready -q -h 127.0.0.1 -U "$POSTGRES_USER"'; do sleep 1; done
   D=$R/app/backups/offsite
   # roles first (the dump's GRANTs name them), except the superuser this stack connects as
   docker compose exec -T postgres sh -c 'grep -vE "ROLE \"?$POSTGRES_USER\"?( |;)" | psql -q -U "$POSTGRES_USER" -d postgres' < $D/roles.sql
   docker compose exec -T postgres sh -c 'psql -q -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS \"$POSTGRES_DB\" WITH (FORCE)" -c "CREATE DATABASE \"$POSTGRES_DB\""'
   docker compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --exit-on-error' < $D/dentalia.dump
   ```
8. `./scripts/deploy.sh` (builds, migrates if needed, starts worker and web,
   verifies), then `docker compose up -d caddy`, and the way in: the host
   Caddy site and DNS if the address changed ([deployment.md § 7](deployment.md)).
9. Backups again: `./scripts/backup.sh hourly` once by hand, then the crontab
   (§ 3). L1 continues in the same repository.
10. `rm -rf /srv/compliance/restore`: it holds the database and `.env` in clear.

### 5.4 From a Storage Box snapshot (after a break-in)

Snapshots are read-only folders on the box. Point restic at the snapshot's copy
of the repository and read without a lock:

```bash
export RESTIC_REPOSITORY='sftp:storagebox:.zfs/snapshot/<snapshot-name>/dentalia'   # /home/.zfs/snapshot on port 23
restic snapshots --no-lock
restic restore latest --no-lock --target /srv/compliance/restore
```

Pick a box snapshot from **before** the break-in, then continue at § 5.3 step 4.
The exact path under `.zfs` is to be confirmed on the real box.

---

## 6. What if

| It happens | Do |
|---|---|
| **"hourly failed"** alert (L0) | `tail backups/backup.log backups/cron.log`. The previous dumps are still in `backups/hourly/` |
| **"L1 failed"** | L0 is fine. Check the box in the Hetzner Console and `ssh storagebox`; the next hour catches up by itself |
| **"L1 skipped unreadable files"** | run `./scripts/backup.sh hourly` by hand to see which; usually a file the worker wrote with a mode `denis` cannot read |
| **"failed its check"** | **do not prune.** `restic check --read-data` for the full picture, then restic's `repair` commands (restic docs, "Troubleshooting") |
| **Heartbeat watcher alarms** | the server, cron, docker or the network is down. Log in; if you cannot, the server is lost (§ 5.3) |
| The dump does not restore | the run stops rather than keep a bad dump; the `.partial` in `backups/hourly/` is kept for inspection. Seen 2026-09-18: orphaned types (runbook § Rolling back) |
| Disk full | L0 dumps take ~7 MB each (×48); the trial restore needs room for a second database. `df -h`, then free space |
| A deploy while a backup runs | the second waits for the lock |
| No alert arrives at all | the channel or cron is broken: `./scripts/backup.sh status`, `crontab -l`, `curl -fsS -d test "$(sed -n 's/^ALERTS_WEBHOOK_URL=//p' .env)"` |
| Server broken into | § 2: L0 and the L1 repository may be destroyed. Revoke the box key, § 5.4 |
| Hetzner account lost | L0 and L1 are gone together. L2, if it exists |
| The key is lost | no backup can be opened, by anyone. There is no way back |

---

## 7. Setting up, and the keys

### L0 (now)

```bash
chmod 600 .env backups/*.dump        # .env holds every secret; older deploy dumps predate 0600
./scripts/backup.sh hourly            # once by hand
crontab -e                            # the two lines in § 3
```

Prove the alert once, without touching anything live: point L1 at a
repository that does not exist for one run,
`BACKUP_REPO=/nonexistent BACKUP_PASSWORD_FILE=/dev/null ./scripts/backup.sh hourly`,
and see "L1 failed" arrive. (L0 runs normally in that same run.)

Who else can read these files: `/srv/compliance` is group `deploy`
(deployment.md § 1.1), and anyone in `deploy` or `docker` can read the dumps
whatever their mode. `getent group deploy docker` shows who.

### L1 (when Mitja has ordered the box)

1. `sudo apt install restic` (Ubuntu 26.04 ships 0.18.1).
2. On the box: enable SSH. (A sub-account limited to one directory is the
   tidier option; the live setup uses the main account `u679983`, which works
   the same and still cannot touch the box's snapshots.)
3. Keys, on the server:
   ```bash
   mkdir -p /srv/compliance/secrets && chmod 700 /srv/compliance/secrets
   head -c 32 /dev/urandom | base64 > /srv/compliance/secrets/restic-password
   ssh-keygen -t ed25519 -N '' -f /srv/compliance/secrets/storagebox-key
   chmod 600 /srv/compliance/secrets/*
   ```
   Install the public key on the box (Hetzner Console, or
   `cat storagebox-key.pub | ssh -p 23 <user>@<user>.your-storagebox.de install-ssh-key`
   with a key the box already accepts).
4. `~/.ssh/config`:
   ```
   Host storagebox
       HostName u679983.your-storagebox.de
       User u679983
       Port 23
       IdentityFile /srv/compliance/secrets/storagebox-key
       IdentitiesOnly yes
   ```
   Then `ssh storagebox ls` once, comparing the host key with the Hetzner Console
   (ED25519 `SHA256:XqONwb1S0zuj5A1CDxpOSuD2hnAArV1A3wKY7Z3sdgM` on 2026-09-30).
5. `.env`:
   ```
   BACKUP_REPO=sftp:storagebox:dentalia
   BACKUP_PASSWORD_FILE=/srv/compliance/secrets/restic-password
   ```
6. `./scripts/backup.sh init`, then `./scripts/backup.sh hourly` (the first run
   reads everything), then `./scripts/backup.sh status`.
7. Mitja turns on automatic daily snapshots. Confirm the sub-account cannot
   delete anything under `/.zfs`.

The repository path is **relative**: `sftp:storagebox:dentalia` is
`/home/dentalia` on the box, the login's home. The `.zfs` path (§ 5.4) is still
to be confirmed once snapshots exist.

### L2 (only if Dentalia wants it)

Any backend restic supports. `BACKUP_REPO_2` is the restic repository string;
S3-style keys go in a file named by `BACKUP_REPO_2_ENV_FILE`
(`AWS_ACCESS_KEY_ID=...`, `AWS_SECRET_ACCESS_KEY=...`), mode 0600. Then
`./scripts/backup.sh init`. Prefer object lock / versioning: that is what makes
L2 survive a compromised server.

### The keys

- **The restic password** opens L1 and L2. Without it no backup can be opened,
  by anyone, including us. It lives on the server (the hourly backup needs it),
  with Denis, and at Dentalia in a password manager **and** a sealed paper copy
  held by two named people. "The client holds the key" means Dentalia has its
  own copy.
- **The Storage Box SSH key** (and the box login) is needed to restore from L1.
  Kept the same way. Neither key is inside the backup.
- **If the password leaks, changing it is not enough.** restic's password only
  wraps the master key; `restic key add` / `key remove` does not re-encrypt
  anything, so the old password plus any copy of the repository (a box snapshot,
  say) still opens everything. The fix is a new repository with a new password,
  and deleting the old one once the new one has history.

---

## 8. Open decisions and limits

- **Heartbeat watcher** (§ 3): which service, and whom it alerts. Until then a
  dead server is noticed by people.
- **Deletion protection beyond 10 days** (§ 2): the Storage Box snapshots are
  the only copy the server cannot touch. Longer needs L2 with object lock.
- **Not backed up:** other applications on the server (PocketBase), the host
  Caddy configuration, the crontab (it is in § 3), the keys (kept by people).

---

## 9. Rehearsals

### 2026-09-28, dev, restic 0.14.0 (Debian's package)

Dev database 64 MB (dump 7.0 MB); archive 1.425 files, copied into a
root-owned directory the way the server's `ARCHIVE_HOST` is; imports; 1.48 GiB
in total. L1 was a local repository standing in for the Storage Box.

| Step | Time | Result |
|---|---|---|
| L0 alone (no L1 configured) | 12 s | dump, roles, `code.txt`, all 0600 |
| L1 `init`, first backup | 50 s | 930 MiB stored |
| Next hourly | 8 s | 2.5 MiB added |
| Weekly: forget, prune, check 5 % | | no errors |
| An unreadable file | | snapshot saved, `result=PARTIAL`, alert sent |
| Heartbeat | | pinged after each good L1 backup |
| `restic restore latest` | 26 s | full tree under the target, original paths |
| Database into a **new, empty** Postgres (§ 5.3 step 7) | 11 s | `document` 896, `evidence` 7.588, `audit_log` 2.175, `item_document` 9.134, roles 2: **identical** to live |
| Archive `cp -an` into an empty directory | 14 s | 1.425 files, **identical** SHA-256; a second run changed nothing |

Caught by the rehearsal and fixed in § 5.3: the first database restore failed
because `pg_isready` over the socket passed while the image's temporary init
server was about to shut down; waiting over TCP fixed it.

### 2026-09-30, the server, L1 on the real Storage Box (restic 0.18.1)

`init` on `sftp:storagebox:dentalia`, then the first `hourly` by hand: 4.351
files, 2.54 GiB read, 1.34 GiB stored, **13 s** in total (L0 3 s, L1 9 s).
`restic check --read-data-subset=5%`: no errors. The dump restored from the box
had the same SHA-256 as the one on the server. Cron picks L1 up from the next
:07 with no crontab change.

**Not run yet:** box snapshots and `--no-lock` from `.zfs` (§ 5.4), and § 5.3
end to end on a real new server, which is the offer's restore test.

An earlier version of this setup (a Docker-wrapped restic, a local restic
repository copied onward, a custom restore script) was reviewed three times the
same day and replaced by this standard one; the reviews' findings that still
apply are built in above (verified dumps, private files, one lock, alerts that
must be delivered, the heartbeat, the restore steps, the honest delete table).
