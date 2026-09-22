#!/usr/bin/env python3
"""PreToolUse(Bash) guard: pytest goes through scripts/test.sh.

A bare `pytest` picks up pyproject's addopts but NOT the parallelism, which the
wrapper decides per invocation, so it runs single-process at ~490s instead of
~150s. It also skips the flock semaphore, so several worktrees can oversubscribe
the 12 cores at once. Exit 2 blocks the call and hands the message back.

Deliberately NOT blocked:
  - anything that already goes through scripts/test.sh
  - --collect-only / --version / --help, which run no tests and are how the
    wrapper and the config itself get verified
Mentions of the word in other commands (grep, pip list, an editor) are not
invocations and never match: only the command position of a segment is checked.
"""
import json
import re
import shlex
import sys

MESSAGE = (
    "Use ./scripts/test.sh <test_id|path>. Bare pytest runs "
    "single-process (~490s)."
)

# Wrappers that may sit in front of the real command without changing it.
# `rtk` is the local CLI proxy, so `rtk pytest` is just pytest.
PREFIXES = {"time", "sudo", "nohup", "nice", "ionice", "env", "stdbuf", "xargs", "rtk"}
# Flags that run no tests, so there is nothing to parallelise or serialise.
EXEMPT_FLAGS = {"--collect-only", "--co", "--version", "--help", "-h", "--fixtures"}
SPLIT = re.compile(r"(?:\|\||&&|\||;|\n)")


def invokes_pytest(segment: str) -> bool:
    try:
        tokens = shlex.split(segment)
    except ValueError:
        return False
    if any(t in EXEMPT_FLAGS for t in tokens):
        return False
    if any("scripts/test.sh" in t for t in tokens):
        return False

    # Drop leading env assignments and benign wrappers to reach the real command.
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", t) or t in PREFIXES:
            i += 1
        elif t == "timeout":
            i += 2  # `timeout <duration> cmd`
        else:
            break
    tokens = tokens[i:]
    if not tokens:
        return False

    head = tokens[0].rsplit("/", 1)[-1]
    if head == "pytest":
        return True
    # python / python3 / python3.12 -m pytest, venv paths included.
    if re.fullmatch(r"python[0-9.]*", head):
        return "-m" in tokens[:3] and "pytest" in tokens[:4]
    # `docker compose exec test python -m pytest` reaches the right container but
    # skips the semaphore and the per-shape `-n`, which is the whole point of the
    # wrapper. The wrapper's own exec is never seen here: the tool call it comes
    # from is `./scripts/test.sh`.
    if head in {"docker", "docker-compose"} and "exec" in tokens:
        return any(t.rsplit("/", 1)[-1] == "pytest" for t in tokens)
    return False


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # Never block on a payload we cannot read.
    command = (payload.get("tool_input") or {}).get("command") or ""
    if any(invokes_pytest(seg) for seg in SPLIT.split(command)):
        print(MESSAGE, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
