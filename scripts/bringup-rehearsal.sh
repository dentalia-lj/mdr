#!/usr/bin/env bash
#
# Take an EMPTY database through the exact sequence a deploy performs, and
# assert the registry landed.
#
#   ./scripts/bringup-rehearsal.sh            throwaway database, dropped after
#   ./scripts/bringup-rehearsal.sh --keep     leave it, to poke at
#
# Every piece of this sequence has tests. The SEQUENCE has never been run.
# `tests/test_compose_config.py` guards config drift and
# `test_web_import_closure.py` guards the slim image; neither runs the order,
# and the order is what a deploy does.
#
# It runs against a throwaway database and NEVER touches the live one: the
# name is generated here, and the connection is overridden per-command.
#
# What it deliberately does NOT cover, so nobody reads a pass as more than it
# is: no live fetching (DISCOVER/FETCH reach the network and this must be
# runnable offline and repeatedly), so the archive and `archive_url` rewrite
# path is NOT rehearsed here — that trap is still only covered by its repair
# CLI. It also does not start containers; it exercises the code the containers
# run. Use `scripts/deploy.sh` for the image and schema half.

set -euo pipefail
cd "$(dirname "$0")/.."

KEEP=0
[[ "${1:-}" == "--keep" ]] && KEEP=1

DB="dentalia_rehearsal_$(head -c6 /dev/urandom | od -An -tx1 | tr -d ' \n')"
URL="postgresql://dentalia:dentalia@postgres:5432/${DB}"

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
fail() { printf '\033[31mFAIL: %s\033[0m\n' "$*"; exit 1; }
ok()   { printf '  ok  %s\n' "$*"; }

# Everything runs in the `test` service: it bind-mounts the repo, so the
# rehearsal exercises the TREE, which is what we want to know about.
#
# WEB_IMPORTS_DIR is overridden because the two containers see the same files
# at different paths: `worker` bind-mounts ./imports at /imports (the default),
# while `test` bind-mounts the whole repo at /app, so the same files are at
# /app/imports and the default resolves to nothing. Without this the ingest
# reads zero rows and reports success.
run()  {
  docker compose exec -T -e DATABASE_URL="$URL" -e WEB_IMPORTS_DIR=/app/imports \
    test "$@"
}
q()    { docker compose exec -T postgres psql -U dentalia -d "$DB" -At -c "$1"; }

cleanup() {
  if [[ $KEEP -eq 1 ]]; then
    printf '\nkept: %s\n' "$DB"
  else
    docker compose exec -T postgres psql -U dentalia -d postgres \
      -c "DROP DATABASE IF EXISTS ${DB} WITH (FORCE)" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

say "0. An empty database"
docker compose exec -T postgres psql -U dentalia -d postgres \
  -c "CREATE DATABASE ${DB}" >/dev/null
ok "$DB"

say "1. Migrations"
run python -m app.cli migrate 2>&1 | grep -v '^storage.local_root' | tail -1
n=$(q "SELECT count(*) FROM schema_migrations")
[[ "$n" -gt 0 ]] || fail "no migrations applied"
ok "$n applied"

say "2. Import the BC vendor master"
# ORDER MATTERS, and this rehearsal is how we found out. `manufacturer_bc_code`
# is foreign-keyed to `vendor_master`, so seeding the playbooks FIRST dies with
#   ForeignKeyViolation: Key (code_source, code)=(LJ, 001)
#   is not present in table "vendor_master"
# on a fresh database. Nothing said so: every test seeds `vendor_master` in a
# fixture, and the live database was populated in an order nobody wrote down.
vendors=$(ls -1 imports/Proizvajalci*.xlsx 2>/dev/null | head -1 || true)
[[ -n "$vendors" ]] || fail "no imports/Proizvajalci*.xlsx — the vendor master must load first"
run python -m app.cli vendor-master --file "$vendors" --apply 2>&1 \
  | grep -v '^storage.local_root' | tail -2
v=$(q "SELECT count(*) FROM vendor_master")
[[ "$v" -gt 0 ]] || fail "vendor_master is empty — the playbook seed cannot run"
ok "$v vendor codes"

say "3. Seed manufacturers from the playbooks"
# THE TRAP this rehearsal exists for. `[manufacturer-seed-is-one-shot]`: a
# fresh environment migrates against an empty `manufacturer_alias`, and if the
# seed is skipped or silently no-ops, `manufacturer` stays empty FOREVER and
# every later stage quietly resolves nothing. An empty table here is a pass
# for every unit test and a dead deployment.
run python -m app.cli manufacturers seed --apply 2>&1 \
  | grep -v '^storage.local_root' | tail -3
m=$(q "SELECT count(*) FROM manufacturer")
[[ "$m" -gt 0 ]] || fail "manufacturer is EMPTY after seeding — the one-shot seed trap"
ok "$m manufacturers"

a=$(q "SELECT count(*) FROM manufacturer_alias")
[[ "$a" -eq 0 ]] || ok "$a aliases already"

say "4. Project the aliases"
# The SECOND missing step, also found by this rehearsal. `manufacturers seed`
# writes `manufacturer`, `manufacturer_bc_code` and the playbook bodies, and
# writes NO aliases: 381 manufacturers and 0 alias rows. Aliases are
# `playbooks sync`'s job -- BC codes at entity level, plus each playbook's
# own `aliases` list -- and without them RESOLVE matches on nothing and every
# group comes out unnamed. A deploy that stopped after step 3 would look
# entirely successful.
run python -m app.cli playbooks sync 2>&1 \
  | grep -v '^storage.local_root' | tail -2
a=$(q "SELECT count(*) FROM manufacturer_alias")
[[ "$a" -gt 0 ]] || fail "manufacturer_alias is empty — RESOLVE would match nothing"
ok "$a aliases"

say "5. Ingest a real BC export"
export_file=$(ls -1 imports/Artikli*.xlsx 2>/dev/null | head -1 || true)
[[ -n "$export_file" ]] || fail "no imports/Artikli*.xlsx to ingest"
ref=$(basename "$export_file")
run python -m app.cli enqueue ingest.run "rehearsal:ingest" \
  --payload "{\"source\":\"csv\",\"ref\":\"${ref}\",\"catalogue\":\"LJ\"}" \
  2>&1 | grep -v '^storage.local_root' | tail -1

say "6. Drain the queue"
# The real runner loop, not a hand-stitched call chain: claim, dispatch,
# finish, enqueue the next stage. Bounded, because DISCOVER would otherwise
# reach the network.
run python -c "
from app import db
from app.workers import runner
done = 0
with db.connect() as conn:
    while done < 400 and runner.run_once(conn, 'rehearsal'):
        done += 1
print(f'drained {done} job(s)')
" 2>&1 | grep -v '^storage.local_root' | tail -1

say "7. Did every job succeed?"
# `pending` is EXPECTED and is not a failure: the drain is capped, and INGEST
# fans out one resolve.group per item, so thousands remain queued. What this
# phase asserts is that nothing FAILED — a bounded run that got as far as it
# got, cleanly.
# BEFORE the registry assertions, because a failed job is the CAUSE and
# "item_mirror is empty" is only the symptom. The first draft asserted the
# symptom first and reported "the ingest wrote nothing" for what was actually
# a path misconfiguration the job had already recorded.
q "SELECT '  '||status||': '||count(*) FROM job GROUP BY status ORDER BY status"
bad=$(q "SELECT count(*) FROM job WHERE status IN ('failed','dead')")
if [[ "$bad" -ne 0 ]]; then
  q "SELECT '  '||type||' #'||id||' — '||coalesce(left(last_error, 300),'(no error recorded)')
       FROM job WHERE status IN ('failed','dead') ORDER BY id"
  fail "$bad job(s) failed or dead-lettered"
fi
ok "no failed or dead jobs"

say "8. Did the registry land?"
items=$(q "SELECT count(*) FROM item_mirror")
[[ "$items" -gt 0 ]] || fail "item_mirror is empty — the ingest wrote nothing"
ok "$items items mirrored"

md=$(q "SELECT count(*) FROM item_mirror WHERE md_flag IS TRUE")
[[ "$md" -gt 0 ]] || fail "no md_flag items — the class column did not map"
ok "$md medical-device items"

groups=$(q "SELECT count(*) FROM item_group")
[[ "$groups" -gt 0 ]] || fail "no item_group rows — RESOLVE never ran or matched nothing"
ok "$groups groups"

named=$(q "SELECT count(*) FROM item_group WHERE canonical_manufacturer IS NOT NULL")
[[ "$named" -gt 0 ]] || fail "every group is unnamed — the alias seed did not reach RESOLVE"
ok "$named groups carry a canonical manufacturer"

say "Bring-up rehearsed: empty database to a populated registry"
