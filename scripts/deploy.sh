#!/usr/bin/env bash
#
# Bring the running stack up to the working tree, and prove that it is.
#
#   ./scripts/deploy.sh              build, dump, migrate, restart, verify
#   ./scripts/deploy.sh --check      verify only, change nothing
#
# Every deploy dumps the database to backups/ before migrating (the newest five
# are kept) and appends one line to backups/deploy.log: when, which commit, how
# many uncommitted files, the commit that ran before, the dump, and whether the
# verification passed. That line and that dump are the rollback: runbook
# § Deploy the working tree, "Rolling back".
#
# This exists because nothing made a rebuild happen. Only the `test` service
# bind-mounts the repo; `worker`, `scheduler` and `web` run baked images, so a
# commit changes the tree and not the running system. On 2026-09-03 the worker
# happened to carry current code only because an unrelated rebuild had run
# minutes earlier, and the running web image was two files behind the tree
# with nothing anywhere reporting it.
#
# The verification is the point. A deploy that builds and restarts without
# checking has told you nothing: the four 2026-08-12 incidents were all a
# green-looking run against stale code.

set -euo pipefail
cd "$(dirname "$0")/.."

CHECK_ONLY=0
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=1

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

# The tree's own answer, computed by the host's python3 from the checkout.
# Every comparison below is against this. It used to run inside the `test`
# container, which bind-mounts the repo -- but that is a 2.9 GB dev-only image
# a client server has no reason to build. app/version.py is standard library
# only and app/__init__.py is empty, so the host interpreter gives the same
# digest from the same files.
tree_part() {  # $1 = root name ("app" | "migrations" | "web")
  python3 -c "
from app import version
print(next(p.digest for p in version.fingerprint_parts() if p.root == '$1'))
"
}

command -v python3 >/dev/null || {
  echo "deploy.sh needs python3 on the host to hash the tree." >&2; exit 1; }

# The prod overlay is opt-in through COMPOSE_FILE in .env, and nothing at
# runtime can tell whether it loaded: STORAGE_LOCAL_ROOT is /archive either way.
# Miss it and the archive lives in the named volume `archive_data`, which
# `docker compose down -v` deletes and no host backup ever sees --
# docs/state/2026-09-04.md:17 calls that "worse than losing both".
#
# ARCHIVE_HOST is the statement of intent: it exists only in the overlay, so
# setting it means a prod install was meant. If it is set and the merged config
# still names archive_data, COMPOSE_FILE was not set and the overlay never
# loaded. Dev sets neither, so dev skips this entirely.
#
# Verified on Compose v5.3.0, 2026-09-22: with the overlay the merge drops the
# volume everywhere, top-level declaration included. NOT yet verified on the
# server's Compose 2.40.3. Check there before the first up:
#   docker compose config | grep -i archive
# expects the host path twice and archive_data nowhere.
if [[ -n "${ARCHIVE_HOST:-}" ]] && docker compose config 2>/dev/null | grep -q 'archive_data'; then
  echo "ARCHIVE_HOST is set but the prod overlay is not loaded: the archive would" >&2
  echo "go to the archive_data volume, which 'down -v' deletes. Set in .env:" >&2
  echo "  COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml" >&2
  exit 1
fi

DUMP_DIR=backups
DEPLOY_LOG=$DUMP_DIR/deploy.log
KEEP_DUMPS=5
mkdir -p "$DUMP_DIR"

# The commit the running stack was last deployed from, per the log.
previous_sha() {
  local sha=""
  [[ -s $DEPLOY_LOG ]] && sha=$(tail -1 "$DEPLOY_LOG" | sed -n 's/.* commit=\([0-9a-f]*\).*/\1/p')
  echo "${sha:-none}"
}

# Must never abort the script: an image too old to answer is exactly the
# condition being tested for, so a failure here is a RESULT, not an error.
# `fingerprint_parts` did not exist before 2026-09-03, and under `set -e` a
# failing command substitution would kill the run before it could say so.
# Three answers, not two. ABSENT means "this image knows the root and does not
# carry it" (Dockerfile.web has no migrations/, the worker's app stage has no
# web/) and is legitimate. UNKNOWN means the image's own `version.py` has never
# heard of the root — which makes it older than the root was added, i.e. stale,
# and must not be waved through the way ABSENT is.
image_part() {  # $1 = service, $2 = root name
  local out
  out=$(docker compose exec -T "$1" python -c "
from app import version
p = next((p for p in version.fingerprint_parts() if p.root == '$2'), None)
print('UNKNOWN' if p is None else p.digest if p.present else 'ABSENT')
" 2>/dev/null | tail -1) || true
  printf '%s' "${out:-UNAVAILABLE}"
}

COMMIT=$(git rev-parse --short HEAD)
DIRTY=$(git status --porcelain --untracked-files=no | wc -l | tr -d ' ')
PREVIOUS=$(previous_sha)
DUMP=none

if [[ $CHECK_ONLY -eq 0 ]]; then
  say "Building images"
  docker compose build worker web

  # After the build, so the dump is as fresh as it can be, and before the
  # migration, so it holds the schema the previous commit ran on. pg_dump reads
  # a snapshot: reads, writes and the workers carry on while it runs; only DDL
  # waits, and the migration below starts after it finishes. Written to a
  # .partial name and renamed only once it has been restored for real, so a
  # dump that died halfway, or one that does not restore, never looks like a
  # rollback point. Under `set -e` a failed dump stops the deploy before
  # anything migrates.
  say "Dumping the database (commit ${PREVIOUS} is running)"
  DUMP="$DUMP_DIR/$(date -u +%Y%m%dT%H%M%SZ)-ran-${PREVIOUS}.dump"
  docker compose exec -T postgres sh -c \
    'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$DUMP.partial"

  # A readable table of contents is not a restorable dump: on 2026-09-18 the
  # dev database's dump listed cleanly and failed to restore on four orphaned
  # array types. So restore it, strictly, into a scratch database in the same
  # cluster, and drop that. No migration runs there, so it touches no role.
  # About 4 s for the 64 MB dev database.
  CHECK_DB="dentalia_restore_check_$(head -c6 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  docker compose exec -T postgres sh -c "createdb -U \"\$POSTGRES_USER\" $CHECK_DB"
  restored=1
  docker compose exec -T postgres sh -c \
    "pg_restore -U \"\$POSTGRES_USER\" -d $CHECK_DB --exit-on-error" < "$DUMP.partial" || restored=0
  docker compose exec -T postgres sh -c "dropdb -U \"\$POSTGRES_USER\" --force $CHECK_DB"
  if [[ $restored -ne 1 ]]; then
    echo "The dump does not restore ($DUMP.partial kept). Nothing was migrated." >&2
    exit 1
  fi
  mv "$DUMP.partial" "$DUMP"
  echo "$DUMP  $(du -h "$DUMP" | cut -f1)"
  # Keep the newest $KEEP_DUMPS; name order is time order (UTC stamp first).
  ls -1 "$DUMP_DIR"/*.dump | head -n -"$KEEP_DUMPS" | xargs -r rm --

  say "Applying migrations"
  docker compose run --rm migrate

  say "Restarting services"
  docker compose up -d worker web
  # Compose returns as soon as the container is started, not once the app
  # inside it can answer. Give the import a moment before interrogating it.
  sleep 3
fi

say "Verifying the running images against the tree"

TREE_APP=$(tree_part app)
TREE_MIG=$(tree_part migrations)
TREE_WEB=$(tree_part web)
echo "tree      app=$TREE_APP  migrations=$TREE_MIG  web=$TREE_WEB"

fail=0
for svc in worker web; do
  got_app=$(image_part "$svc" app)
  got_mig=$(image_part "$svc" migrations)
  got_web=$(image_part "$svc" web)
  printf '%-9s app=%s  migrations=%s  web=%s' "$svc" "$got_app" "$got_mig" "$got_web"

  # Compare per root, never the combined digest: Dockerfile.web carries no
  # migrations/ and the worker's `app` stage carries no web/, so a combined
  # comparison against the host differs forever and says nothing about
  # staleness. ABSENT is therefore a legitimate answer for either root and is
  # never a failure; a root that IS present and differs is. See app/version.py.
  #
  # `web` was added 2026-09-14 and is the reason this loop exists in this shape:
  # until then the running web image could be three commits behind -- no
  # `web/missing.py`, `/missing` a 404 -- and this script printed "Deployed and
  # verified", because neither hashed root had moved.
  if [[ "$got_app" == "UNAVAILABLE" ]]; then
    printf '   <-- CANNOT ANSWER (image predates fingerprint_parts, so: stale)\n'
    fail=1
  elif [[ "$got_web" == "UNKNOWN" || "$got_mig" == "UNKNOWN" ]]; then
    printf '   <-- DOES NOT KNOW THAT ROOT (image predates it, so: stale)\n'
    fail=1
  elif [[ "$got_app" != "$TREE_APP" ]]; then
    printf '   <-- STALE app/\n'; fail=1
  elif [[ "$got_mig" != "ABSENT" && "$got_mig" != "$TREE_MIG" ]]; then
    printf '   <-- STALE migrations/\n'; fail=1
  elif [[ "$got_web" != "ABSENT" && "$got_web" != "$TREE_WEB" ]]; then
    printf '   <-- STALE web/\n'; fail=1
  else
    printf '   ok\n'
  fi
done

say "Verifying the database against the migrations"
docker compose exec -T worker python -m app.cli schema-drift || fail=1

result=verified
[[ $fail -ne 0 ]] && result=NOT-VERIFIED

if [[ $CHECK_ONLY -eq 0 ]]; then
  echo "$(date -u +%FT%TZ) commit=$COMMIT dirty=$DIRTY previous=$PREVIOUS dump=$DUMP result=$result" \
    >> "$DEPLOY_LOG"
else
  echo "last deploy: $(tail -1 "$DEPLOY_LOG" 2>/dev/null || echo 'none recorded')"
fi

if [[ $fail -ne 0 ]]; then
  say "DEPLOY NOT VERIFIED"
  echo "Something above does not match the tree. Do not trust a run until it does."
  exit 1
fi

say "Deployed and verified (commit $COMMIT, $DIRTY uncommitted file(s))"
