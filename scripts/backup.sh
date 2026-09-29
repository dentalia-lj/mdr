#!/usr/bin/env bash
#
# Backups in levels. The whole process -- what runs when, what a person does,
# how to restore, what-ifs -- is docs/dev/backup.md; this only says what the
# script does.
#
#   L0  this server: an hourly database dump, proven restorable, newest 48 kept
#   L1  Hetzner Storage Box, restic, hourly            (BACKUP_REPO)
#   L2  an outside provider, restic, hourly, optional  (BACKUP_REPO_2)
#
#   ./scripts/backup.sh hourly   L0 dump, then a restic backup to each configured level   (cron)
#   ./scripts/backup.sh weekly   forget + prune, then check 5 %, on each configured level (cron)
#   ./scripts/backup.sh init     create the restic repository of each configured level that has none
#   ./scripts/backup.sh status   last runs, newest dumps, latest snapshots
#
# restic is the distribution's package (`apt install restic`), called with its
# own environment variables, so every restore command in the doc is plain
# restic. Levels that are not configured are skipped.
#
# Each restic snapshot holds the newest dump, the roles and code.txt (from
# backups/offsite/), the archive (ARCHIVE_HOST), the imports (IMPORTS_HOST),
# .env and caddy/users/. The dump is taken before the files are read, so every
# document the dump names is in the same snapshot.

set -Eeuo pipefail
cd "$(dirname "$0")/.."
source scripts/lib/dump.sh
source scripts/lib/env.sh

DIR=backups
LOG=$DIR/backup.log
HOURLY=$DIR/hourly        # L0
STAGE=$DIR/offsite        # the newest L0 dump, under fixed names, for restic
KEEP_DUMPS=48
HOST_TAG=dentalia
KEEP=(--keep-hourly 48 --keep-daily 14 --keep-weekly 8 --keep-monthly 12 --keep-yearly 10)

mkdir -p "$DIR"
( umask 077; mkdir -p "$HOURLY" "$STAGE" )

ALERT_URL=$(env_get ALERTS_WEBHOOK_URL)
HEARTBEAT_URL=$(env_get BACKUP_HEARTBEAT_URL)
PASSWORD_FILE=$(env_get BACKUP_PASSWORD_FILE)

# Never fails: a full disk must not stop the alert that follows a log line.
log() { echo "$(date -u +%FT%TZ) $*" >> "$LOG" 2>/dev/null || true; }

# Same contract as app/alerts.py (ntfy: body is the message, headers carry the
# title and priority). --fail, so an HTTP error is not taken for delivery.
alert() {  # $1 = title, $2 = message
  [[ -n $ALERT_URL ]] || return 0
  curl -fsS -m 10 --retry 2 -o /dev/null -H "Title: $1" -H "Priority: high" -H "Tags: floppy_disk" \
    --data-binary "$2" "$ALERT_URL" 2>/dev/null || log "alert NOT DELIVERED: $1"
}

# "A backup to the Storage Box just succeeded", to an outside watcher that
# alarms when the pings stop. Only something outside the server can notice a
# dead server, a stopped cron or a broken alert channel. Empty = not set up.
heartbeat() {
  [[ -n $HEARTBEAT_URL ]] || return 0
  curl -fsS -m 10 --retry 2 -o /dev/null "$HEARTBEAT_URL" 2>/dev/null || log "heartbeat NOT DELIVERED"
}

fail() {  # stop this run: alert first, then log
  alert "Dentalia backup: ${MODE:-?} failed" "$1 on $(hostname). See backups/backup.log and docs/dev/backup.md § 6."
  log "mode=${MODE:-?} result=FAILED reason=\"$1\""
  echo "$1" >&2
  exit 1
}

repo_of() { case $1 in 1) env_get BACKUP_REPO ;; 2) env_get BACKUP_REPO_2 ;; esac; }
levels() { local n; for n in 1 2; do [[ -n $(repo_of "$n") ]] && echo "$n"; done; return 0; }

# restic against level N, with restic's own variables. An S3-style level
# takes its keys from the file named by BACKUP_REPO_<n>_ENV_FILE.
restic_at() {
  local n=$1; shift
  [[ -f $PASSWORD_FILE ]] || { echo "BACKUP_PASSWORD_FILE ($PASSWORD_FILE) is not a file." >&2; return 1; }
  local envf; envf=$(env_get "BACKUP_REPO_${n}_ENV_FILE")
  ( export RESTIC_REPOSITORY="$(repo_of "$n")" RESTIC_PASSWORD_FILE="$PASSWORD_FILE"
    if [[ -n $envf ]]; then set -a; source "$envf"; set +a; fi
    exec restic "$@" )
}

# Keep the newest $2 files matching $1 (names start with a UTC stamp).
keep_newest() { { ls -1 $1 2>/dev/null || true; } | head -n -"$2" | xargs -r rm --; }

# ---- modes -----------------------------------------------------------------

do_hourly() {
  local t0=$SECONDS stamp archive imports n rc failed=0 paths=()
  take_lock "$DIR/.lock" 1800 || fail "could not get backups/.lock (a deploy or another backup ran too long)"

  # L0
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  dump_verified "$HOURLY/$stamp.dump" || fail "the database dump failed or does not restore"
  dump_roles "$HOURLY/$stamp.roles.sql" || fail "the roles dump failed"
  # Which code this database ran on: a restore onto a fresh clone needs it.
  ( umask 077
    { echo "commit=$(git rev-parse HEAD 2>/dev/null || echo unknown)"
      echo "uncommitted_files=$(git status --porcelain --untracked-files=no 2>/dev/null | wc -l)"
      echo "last deploys (backups/deploy.log):"
      tail -3 "$DIR/deploy.log" 2>/dev/null || true
    } > "$HOURLY/$stamp.code.txt" )
  keep_newest "$HOURLY/*.dump" "$KEEP_DUMPS"
  keep_newest "$HOURLY/*.roles.sql" "$KEEP_DUMPS"
  keep_newest "$HOURLY/*.code.txt" "$KEEP_DUMPS"
  log "mode=hourly level=L0 result=ok dump=$(du -h "$HOURLY/$stamp.dump" | cut -f1) secs=$((SECONDS - t0))"

  # L1, L2
  [[ -n $(levels) ]] || exit 0
  archive=$(env_get ARCHIVE_HOST)
  [[ -d $archive ]] || fail "ARCHIVE_HOST ($archive) is not a directory; restic cannot back up the archive"
  imports=$(env_get IMPORTS_HOST); imports=${imports:-imports}
  # Fixed names, so restic sees one file changing, not a new file every hour.
  # Hard links: no copy, and restic stores them as ordinary files.
  ln -f "$HOURLY/$stamp.dump" "$STAGE/dentalia.dump"
  ln -f "$HOURLY/$stamp.roles.sql" "$STAGE/roles.sql"
  ln -f "$HOURLY/$stamp.code.txt" "$STAGE/code.txt"
  paths=("$(realpath "$STAGE")" "$(realpath "$archive")")
  [[ -d $imports ]] && paths+=("$(realpath "$imports")")
  [[ -f .env ]] && paths+=("$(realpath .env)")
  [[ -d caddy/users ]] && paths+=("$(realpath caddy/users)")

  for n in $(levels); do
    t0=$SECONDS; rc=0
    restic_at "$n" backup --host "$HOST_TAG" --tag hourly "${paths[@]}" || rc=$?
    case $rc in
      0) log "mode=hourly level=L$n result=ok secs=$((SECONDS - t0))"
         [[ $n == 1 ]] && heartbeat ;;
      3) # A snapshot was saved but some files could not be read. It counts;
         # a person looks at why.
         log "mode=hourly level=L$n result=PARTIAL secs=$((SECONDS - t0))"
         alert "Dentalia backup: L$n skipped unreadable files" \
           "The hourly backup to L$n on $(hostname) saved a snapshot but could not read some files. Run ./scripts/backup.sh hourly by hand to see which."
         [[ $n == 1 ]] && heartbeat ;;
      *) failed=1
         log "mode=hourly level=L$n result=FAILED rc=$rc"
         alert "Dentalia backup: L$n failed" \
           "restic backup to L$n exited $rc on $(hostname). L0 is fine. See backups/backup.log and docs/dev/backup.md § 6." ;;
    esac
  done
  # exit, not return: called plainly, a non-zero return would also fire the
  # ERR trap and alert a second time for what was already alerted above.
  exit $failed
}

do_weekly() {
  local n failed=0
  take_lock "$DIR/.lock" 1800 || fail "could not get backups/.lock"
  for n in $(levels); do
    if ! restic_at "$n" forget --host "$HOST_TAG" "${KEEP[@]}" --prune; then
      failed=1; log "mode=weekly level=L$n prune=FAILED"
      alert "Dentalia backup: L$n prune failed" "restic forget/prune on L$n failed on $(hostname). See docs/dev/backup.md § 6."
    fi
    if ! restic_at "$n" check --read-data-subset=5%; then
      failed=1; log "mode=weekly level=L$n check=FAILED"
      alert "Dentalia backup: L$n failed its check" "restic check found a problem in L$n on $(hostname). Do not prune it. See docs/dev/backup.md § 6."
    else
      log "mode=weekly level=L$n result=ok"
    fi
  done
  # exit, not return: called plainly, a non-zero return would also fire the
  # ERR trap and alert a second time for what was already alerted above.
  exit $failed
}

do_init() {
  local n
  [[ -n $(levels) ]] || { echo "No level configured: set BACKUP_REPO (and BACKUP_PASSWORD_FILE) in .env."; return 1; }
  for n in $(levels); do
    if restic_at "$n" cat config >/dev/null 2>&1; then
      echo "L$n already has a repository: $(repo_of "$n")"
    else
      restic_at "$n" init
      log "mode=init level=L$n result=ok"
    fi
  done
}

do_status() {
  local n
  echo "== last runs"; tail -6 "$LOG" 2>/dev/null || echo "(none)"
  echo "== L0, newest dumps"; ls -1t "$HOURLY"/*.dump 2>/dev/null | head -3 || true
  for n in 1 2; do
    if [[ -z $(repo_of "$n") ]]; then echo "== L$n: not configured"; continue; fi
    echo "== L$n: $(repo_of "$n"), latest snapshots"
    restic_at "$n" snapshots --host "$HOST_TAG" --latest 3 || true
  done
}

# Anything a mode did not handle itself: alert first, then log.
on_error() {
  alert "Dentalia backup: ${MODE:-?} failed" \
    "scripts/backup.sh ${MODE:-?} failed at line $1 on $(hostname). See backups/backup.log and docs/dev/backup.md § 6."
  log "mode=${MODE:-?} result=FAILED line=$1"
}

MODE=${1:-}
case $MODE in hourly|weekly) trap 'on_error $LINENO' ERR ;; esac
case $MODE in
  hourly) do_hourly ;;
  weekly) do_weekly ;;
  init)   do_init ;;
  status) do_status ;;
  *) sed -n '3,15p' "$0"; exit 2 ;;
esac
