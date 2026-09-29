# Sourced by scripts/backup.sh. Not executable on its own.

# env_get KEY: the value from the environment, else from .env. compose reads
# .env itself; scripts run from cron do not, so they read it here.
# Set-but-empty in the environment wins, so `ALERTS_WEBHOOK_URL= ./scripts/...`
# really is silent on a dev box whose .env names the live topic. Parsed the way
# compose parses .env: a quoted value ends at its closing quote; an unquoted one
# ends before ` #`, so a line copied from .env.example with a trailing comment
# does not become a path ending in "# ..." (seen 2026-09-28).
env_get() {
  local v=${!1:-}
  if [[ -z ${!1+set} && -f .env ]]; then
    v=$(sed -n "s/^$1=//p" .env | tail -1)
    case $v in
      \"*) v=${v#\"}; v=${v%%\"*} ;;
      \'*) v=${v#\'}; v=${v%%\'*} ;;
      *)   v=$(printf '%s' "$v" | sed -E 's/[[:space:]]+#.*$//; s/[[:space:]]+$//') ;;
    esac
  fi
  printf '%s' "$v"
}
