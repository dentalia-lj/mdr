"""scripts/backup.sh: the properties a rehearsal cannot keep true on its own.

The behaviour (dump, restic backup, restore) is proven by the dev rehearsal
recorded in docs/dev/backup.md § 9, against real restic and a real Postgres,
not here. These guard what drifts silently.
"""
from __future__ import annotations

import pathlib
import subprocess

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_BACKUP = (_ROOT / "scripts" / "backup.sh").read_text()
_DEPLOY = (_ROOT / "scripts" / "deploy.sh").read_text()


def _code(text: str) -> list[str]:
    return [l for l in text.splitlines() if not l.lstrip().startswith("#")]


def test_one_dump_routine_for_deploy_and_backup():
    # A deploy's rollback dump and an hourly backup must be proven the same way
    # (the trial restore). Two copies of the routine drift; one does not.
    for name, text in (("deploy.sh", _DEPLOY), ("backup.sh", _BACKUP)):
        assert "source scripts/lib/dump.sh" in text, f"{name} does not source the shared dump"
        assert not any("pg_dump" in l for l in _code(text)), f"{name} runs its own pg_dump"


def test_deploy_and_backup_share_one_lock():
    # A migration must never run while a backup dumps, and the reverse.
    assert 'take_lock "$DUMP_DIR/.lock"' in _DEPLOY and "DUMP_DIR=backups" in _DEPLOY
    assert 'take_lock "$DIR/.lock"' in _BACKUP and "\nDIR=backups\n" in _BACKUP


def test_deploy_rotation_only_counts_deploy_dumps():
    # deploy.sh keeps the newest five dumps in backups/. Anything else there that
    # matched its glob took a slot for good, and once five existed each deploy
    # deleted the rollback dump it had just made (found 2026-09-28).
    rotate = [l for l in _DEPLOY.splitlines() if "head -n -" in l and "rm" in l]
    assert rotate, "no rotation line found"
    assert all('"$DUMP_DIR"/[0-9]*.dump' in l for l in rotate), rotate
    # The hourly dumps live in their own directory, never in backups/ itself.
    assert "HOURLY=$DIR/hourly" in _BACKUP


def test_restic_is_the_system_binary_not_a_container():
    # The design is the standard one (decisions.md 2026-09-28): the
    # distribution's restic, so every restore command in the doc is plain restic.
    code = "\n".join(_code(_BACKUP))
    assert "exec restic " in code
    assert "docker run" not in code


def test_env_values_are_read_the_way_compose_reads_them(tmp_path):
    # The script reads .env itself. A value copied from .env.example with a
    # trailing comment once became a path ending in "# level 1, ..." (2026-09-28).
    (tmp_path / ".env").write_text(
        "BACKUP_REPO=/srv/compliance/backup-repo      # level 1\n"
        "BACKUP_PASSWORD_FILE='/srv/x y/pass' # quoted\n"
        'BACKUP_REPO_2="sftp:storagebox:/dentalia"\n'
        "BACKUP_HEARTBEAT_URL=\n"
        "ALERTS_WEBHOOK_URL=https://ntfy.sh/abc#not-a-comment\n"
    )
    lib = _ROOT / "scripts" / "lib" / "env.sh"
    out = subprocess.run(
        ["bash", "-c", f"source {lib}; for k in BACKUP_REPO BACKUP_PASSWORD_FILE "
         "BACKUP_REPO_2 BACKUP_HEARTBEAT_URL ALERTS_WEBHOOK_URL; do printf '%s\\n' \"$(env_get $k)\"; done"],
        cwd=tmp_path, capture_output=True, text=True, check=True,
        env={"PATH": "/usr/bin:/bin"},
    ).stdout.splitlines()
    assert out == ["/srv/compliance/backup-repo", "/srv/x y/pass", "sftp:storagebox:/dentalia", "",
                   "https://ntfy.sh/abc#not-a-comment"]


def _structure(path: pathlib.Path) -> tuple[list[str], list[str]]:
    """(heading levels with their § numbers, fenced code blocks) of a doc."""
    import re
    text = path.read_text()
    heads = [m.group(1) + (m.group(2) or "") for m in
             re.finditer(r"^(#{2,3}) (\d+(?:\.\d+)?\.?)?", text, re.M)]
    blocks = re.findall(r"^ *```[a-z]*\n(.*?)^ *```", text, re.M | re.S)
    return heads, [b.strip() for b in blocks]


def test_backup_doc_english_and_slovene_stay_in_step():
    # Ruled 2026-09-30: the backup doc exists in both languages and is kept in
    # step. The prose is translated; the sections and every command are the same.
    en = _structure(_ROOT / "docs" / "dev" / "backup.md")
    sl = _structure(_ROOT / "docs" / "dev" / "backup.sl.md")
    assert en[0] == sl[0], "the two docs have different sections"
    assert en[1] == sl[1], "a command block differs between backup.md and backup.sl.md"
