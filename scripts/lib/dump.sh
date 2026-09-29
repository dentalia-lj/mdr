# Sourced by scripts/deploy.sh and scripts/backup.sh.
# Not executable on its own.
#
# One way to take a database dump, so a deploy's rollback point and an hourly
# backup are proven the same way.
#
# Every step checks its own exit status. Callers use these functions both
# plainly and as `f || ...`, and bash switches errexit off for the whole body
# of a function called from a `||` list (verified 2026-09-28), so `set -e`
# cannot be relied on in here.

# dump_verified <dest>: dump the database to <dest>, and only if it restores.
#
# pg_dump reads a snapshot: reads, writes and the workers carry on while it
# runs; only DDL waits. Written to <dest>.partial and renamed only once it has
# been restored for real, so a dump that died halfway, or one that does not
# restore, never looks like a good one. Returns non-zero and leaves the
# .partial for inspection if anything fails.
#
# A readable table of contents is not a restorable dump: on 2026-09-18 the
# dev database's dump listed cleanly and failed to restore on four orphaned
# array types. So restore it, strictly, into a scratch database in the same
# cluster, and drop that. About 4 s for the 64 MB dev database.
#
# The file is the whole registry, so it is created 0600: the server is shared
# with Dentalia's PocketBase, which runs as its own user.
dump_verified() {
  local dest=$1 check_db restored=1
  ( umask 077; : > "$dest.partial" ) || return 1
  docker compose exec -T postgres sh -c \
    'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$dest.partial" \
    || { echo "pg_dump failed ($dest.partial kept)." >&2; return 1; }
  check_db="dentalia_restore_check_$(head -c6 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  docker compose exec -T postgres sh -c "createdb -U \"\$POSTGRES_USER\" $check_db" \
    || { echo "Could not create the scratch database for the trial restore." >&2; return 1; }
  docker compose exec -T postgres sh -c \
    "pg_restore -U \"\$POSTGRES_USER\" -d $check_db --exit-on-error" < "$dest.partial" || restored=0
  docker compose exec -T postgres sh -c "dropdb -U \"\$POSTGRES_USER\" --force $check_db" \
    || echo "WARNING: scratch database $check_db was not dropped; drop it by hand." >&2
  if [[ $restored -ne 1 ]]; then
    echo "The dump does not restore ($dest.partial kept)." >&2
    return 1
  fi
  mv "$dest.partial" "$dest" || return 1
}

# dump_roles <dest>: the cluster's roles, which pg_dump leaves out. The dump's
# GRANTs name `dentalia_api` (migration 007), so a restore into a fresh cluster
# fails without it. Carries the password hashes, never the passwords; 0600.
dump_roles() {
  ( umask 077; : > "$1.partial" ) || return 1
  docker compose exec -T postgres sh -c \
    'pg_dumpall -U "$POSTGRES_USER" --roles-only' > "$1.partial" \
    || { echo "pg_dumpall --roles-only failed." >&2; return 1; }
  mv "$1.partial" "$1" || return 1
}

# take_lock <file> <seconds>: hold an exclusive lock on fd 9 for the rest of the
# calling script. deploy.sh and backup.sh share backups/.lock, so a migration
# never runs while a backup is dumping, and a backup never snapshots half a
# deploy.
# Call it once per script: opening fd 9 again would drop the lock for a moment.
take_lock() {
  exec 9>"$1"
  flock -w "$2" 9 || { echo "Could not take $1 within $2 s: another deploy or backup is running." >&2; return 1; }
}
