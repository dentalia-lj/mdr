#!/usr/bin/env bash
#
# The single entry point for running the suite.
#
#   ./scripts/test.sh                                  whole suite, -n 4
#   ./scripts/test.sh tests/test_queue.py              those paths, -n 2
#   ./scripts/test.sh tests/test_queue.py::test_claim  that id, -n 0
#   ./scripts/test.sh --heavy                          same, but -n 2
#   ./scripts/test.sh tests/test_web.py -x -k staging  extra flags pass through
#
# Why a wrapper rather than flags in pyproject's addopts: the right `-n` depends
# on what is being run. A single test id wants no workers at all (xdist setup
# costs more than the test), a file or two wants a couple, the whole suite wants
# four. addopts cannot vary per invocation, and `-n 4` baked in there would
# fight every deliberate `-n 0`.
#
# `--dist loadfile` on the multi-worker paths: it is a WALL-CLOCK choice, not a
# correctness one. test_web.py is not order-dependent (verified under reverse
# order, two shuffled seeds, --dist load and --dist worksteal); loadfile simply
# wins by keeping a file's tests on one worker so that worker's warm fixtures
# get reused. See CLAUDE.md for the measurements.
#
# Runs INSIDE the stack via `docker compose exec`, never `run`, never `--build`:
# the repo is bind-mounted at /app, so the running container already sees
# uncommitted changes. `run` would pay container setup per invocation and
# `--build` would rebuild the image mid-loop. Rebuild by hand when the
# Dockerfile or the dependency set changes:
#
#   docker compose --profile test up -d --build test
#
set -euo pipefail

die() { printf '%s\n' "$*" >&2; exit 1; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# --------------------------------------------------------------------------- #
# Worktrees. The semaphore below is explicitly shared across checkouts, so being
# run from a worktree is expected -- but two things still resolve against the
# MAIN checkout and have to be pointed there by hand:
#
#   * `docker compose` derives its project name from the directory it runs in.
#     From a worktree directory such as `../dentalia-feature` that is project
#     `dentalia-feature`, which owns no
#     containers, so the stack check below fails with "not answering".
#   * the bind mount is the main checkout at /app, and `exec` with no `-w` lands
#     in the image's WORKDIR (/app). Without the `-w` computed here, a worktree
#     invocation runs the MAIN checkout's tests and reports them as yours --
#     a green that says nothing about the tree you are working in.
#
# `--git-common-dir` is the shared `.git`, which is inside the main checkout in
# both cases (plain clone and linked worktree).
# --------------------------------------------------------------------------- #
MAIN_ROOT="$(cd "$(git rev-parse --path-format=absolute --git-common-dir)/.." && pwd)"
case "$REPO_ROOT" in
  "$MAIN_ROOT")   CONTAINER_DIR=/app ;;
  "$MAIN_ROOT"/*) CONTAINER_DIR="/app/${REPO_ROOT#"$MAIN_ROOT"/}" ;;
  *) die "$REPO_ROOT is not inside $MAIN_ROOT; the container cannot see it." ;;
esac

SERVICE=test
SEM_DIR=/tmp/pytest-slots
SLOTS=4

# --------------------------------------------------------------------------- #
# Argument classification
#
# Three shapes, decided by the first non-flag argument:
#   contains `::`      -> a test id      -> -n 0
#   names a path       -> files or dirs  -> -n 2 --dist loadfile
#   nothing at all     -> the suite      -> -n 4 --dist loadfile
# Anything starting with `-` (other than --heavy) is a pytest flag and is
# forwarded untouched, so `-x`, `-k`, `--lf` and friends still work.
#
# Flags that take a SEPARATE value (`-k enqueue`, not `-k=enqueue`) must consume
# that value too, or it gets misread as a path — `-k enqueue tests/x.py` would
# otherwise become `-k tests/x.py enqueue` and silently filter on the wrong
# string. The `=` forms need no special handling. `--` forwards everything after
# it verbatim, for anything not listed here.
# --------------------------------------------------------------------------- #
VALUE_FLAGS=" -k -m -p -c -o -r -W -n --dist --maxfail --ignore --deselect --rootdir --junitxml --log-level --timeout --tb "

heavy=0
targets=()
passthrough=()
mode=suite

while [ $# -gt 0 ]; do
  arg="$1"; shift
  case "$arg" in
    --heavy) heavy=1 ;;
    --)
      passthrough+=("$@")
      break
      ;;
    -*)
      passthrough+=("$arg")
      # Only when the value is a separate token: `--maxfail=2` already carries it.
      if [[ "$arg" != *=* && "$VALUE_FLAGS" == *" $arg "* ]]; then
        [ $# -gt 0 ] || die "$arg needs a value"
        passthrough+=("$1"); shift
      fi
      ;;
    *)
      # Absolute paths inside the repo become repo-relative: the container sees
      # the tree at /app, not at the host path.
      case "$arg" in
        "$REPO_ROOT"/*) arg="${arg#"$REPO_ROOT"/}" ;;
      esac
      targets+=("$arg")
      case "$arg" in
        *::*) mode=id ;;
        *)    [ "$mode" = id ] || mode=path ;;
      esac
      ;;
  esac
done

case "$mode" in
  id)    workers=(-n 0) ;;
  path)  workers=(-n 2 --dist loadfile) ;;
  suite) workers=(-n 4 --dist loadfile) ;;
esac

# --heavy means the machine is loaded: never more than 2 workers. A single test
# id stays at -n 0, which is already lighter than 2.
if [ "$heavy" = 1 ] && [ "$mode" != id ]; then
  workers=(-n 2 --dist loadfile)
fi

# --------------------------------------------------------------------------- #
# The stack must already be up. This script deliberately does NOT start it:
# bringing up Postgres is a side effect nobody asked for when they typed a test
# id, and it hides an unhealthy stack behind an automatic restart.
# --------------------------------------------------------------------------- #
if ! (cd "$MAIN_ROOT" && docker compose ps --services --status running) >/dev/null 2>&1; then
  die "docker compose is not answering for this project ($(basename "$REPO_ROOT")). Is the docker daemon up?"
fi

running="$( (cd "$MAIN_ROOT" && docker compose ps --services --status running) 2>/dev/null || true)"
missing=()
grep -qx postgres <<<"$running" || missing+=(postgres)
grep -qx "$SERVICE" <<<"$running" || missing+=("$SERVICE")

if [ ${#missing[@]} -gt 0 ]; then
  {
    printf 'Stack is not up: %s not running.\n\n' "${missing[*]}"
    printf 'Start it, then re-run this script:\n\n'
    printf '  docker compose up -d\n'
    printf '  docker compose --profile test up -d %s\n\n' "$SERVICE"
    printf 'This script never starts the stack for you: that would hide an\n'
    printf 'unhealthy stack behind an automatic restart.\n'
  } >&2
  exit 1
fi

# --------------------------------------------------------------------------- #
# Concurrency semaphore: 4 slots, shared by every worktree on this machine.
#
# The per-run `-n` above bounds ONE run. It says nothing about the three other
# worktrees that may each be running their own suite, and 4 runs x 4 workers on
# 12 cores is how the machine ends up thrashing. The slots are flock'd files in
# a fixed /tmp path, so they are shared across checkouts by construction. The fd
# stays open for the life of this script; the kernel drops the lock when the
# process exits, including on kill, so a crashed run never leaks a slot.
# --------------------------------------------------------------------------- #
mkdir -p "$SEM_DIR"
chmod 1777 "$SEM_DIR" 2>/dev/null || true

slot_fd=
announced=0
while [ -z "$slot_fd" ]; do
  for ((i = 0; i < SLOTS; i++)); do
    exec {fd}>"$SEM_DIR/slot.$i"
    if flock -n "$fd"; then
      slot_fd=$fd
      slot_n=$i
      break
    fi
    exec {fd}>&-
  done
  if [ -z "$slot_fd" ]; then
    if [ "$announced" = 0 ]; then
      printf 'All %d test slots busy (other worktrees are running). Waiting...\n' "$SLOTS" >&2
      announced=1
    fi
    sleep 2
  fi
done

# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
# -T only when stdout is not a terminal: keeps colour and progress interactively,
# stays safe in CI and under a non-interactive shell.
tty_flag=()
[ -t 1 ] || tty_flag=(-T)

cmd=(python -m pytest -q "${workers[@]}" "${passthrough[@]}" "${targets[@]}")

printf '[test.sh] slot %d/%d | mode=%s | %s | %s\n' \
  "$((slot_n + 1))" "$SLOTS" "$mode" "$CONTAINER_DIR" "${cmd[*]}" >&2

cd "$MAIN_ROOT"
exec docker compose exec "${tty_flag[@]}" -w "$CONTAINER_DIR" "$SERVICE" "${cmd[@]}"
