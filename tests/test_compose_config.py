"""docker-compose.yml declares every config key the code reads.

Compose reads `.env` for ${} interpolation only; it does NOT inject it into
containers (there is no `env_file:` in this file). A key absent from a
service's `environment:` block is unsettable, silently, and the process runs
on its code default. Verified live 2026-08-31: 14 of the 16 SCHEDULER_* keys
were unreachable on both `scheduler` and `web`.
"""
from __future__ import annotations

import pathlib
import re

import pytest

yaml = pytest.importorskip("yaml")

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _keys_config_reads(prefix: str) -> set[str]:
    src = (_ROOT / "app" / "config.py").read_text()
    return set(re.findall(rf'"({prefix}[A-Z_]+)"', src))


def _keys_service_declares(service: str, prefix: str) -> set[str]:
    compose = yaml.safe_load((_ROOT / "docker-compose.yml").read_text())
    env = compose["services"][service].get("environment") or {}
    return {k for k in env if k.startswith(prefix)}


# `worker` joined this list when the crons moved onto the queue: the tick
# handler runs in the worker process, so SCHEDULER_* is read there too, and a
# key missing from that service is a cron running on a hardcoded default.
@pytest.mark.parametrize("service", ["web", "worker"])
def test_every_scheduler_key_reaches_the_container(service):
    missing = _keys_config_reads("SCHEDULER_") - _keys_service_declares(service, "SCHEDULER_")
    assert missing == set(), (
        f"{service} cannot be configured for: {sorted(missing)}. "
        "Compose does not inject .env; add them to that service's environment: block."
    )


# BC runs in the worker: `ingest.run` (bc_odata) builds the client there, and
# `bc.push` plus its drift cron read the write switch there. Unwired, all of it
# ran on empty defaults: no client, writes off, and no way to change either.
def test_every_bc_key_reaches_the_worker():
    missing = _keys_config_reads("BC_") - _keys_service_declares("worker", "BC_")
    assert missing == set(), (
        f"worker cannot be configured for: {sorted(missing)}. "
        "Compose does not inject .env; add them to worker's environment: block."
    )


def test_web_sees_the_same_bc_write_switch_as_the_worker():
    """The item button and the bulk preview say whether a push will write.
    Reading a different value than the worker that actually writes would show
    "withheld" for a push that goes through, or the reverse."""
    assert "BC_WRITE_ENABLED" in _keys_service_declares("web", "BC_")


def test_bc_credentials_stay_out_of_web():
    """web builds no BC client; a secret it cannot use is only exposure."""
    assert {"BC_USERNAME", "BC_PASSWORD"} & _keys_service_declares("web", "BC_") == set()


def test_worker_gets_what_bc_push_builds_the_warehouse_url_from():
    """`pteWarehouseURL` is `{WEB_PUBLIC_BASE_URL}/item/{ref}?k={WEB_BC_LINK_KEY}`,
    built in the worker. Declared on web only, a push would write a link with
    no host and no key into every BC item card (followup [bc-push-link-env])."""
    declared = _keys_service_declares("worker", "WEB_")
    assert {"WEB_PUBLIC_BASE_URL", "WEB_BC_LINK_KEY"} <= declared


def test_the_crons_start_without_a_profile_flag():
    """The guard that caught the ten silent days, repointed.

    It used to name the `scheduler` service. That service was deleted
    2026-09-02 and the crons became `scheduler.tick` jobs claimed by `worker`,
    so `worker` is now the service whose profile-gating would stop every cron.
    """
    compose = yaml.safe_load((_ROOT / "docker-compose.yml").read_text())
    assert "scheduler" not in compose["services"], (
        "the standalone scheduler process was deleted; its crons run on the "
        "queue as scheduler.tick jobs claimed by `worker`"
    )
    assert "profiles" not in compose["services"]["worker"], (
        "a profile-gated worker is skipped by `docker compose up -d`, and it "
        "now runs the crons; profile-gating the scheduler produced no cron run "
        "between 2026-08-21 and 2026-08-31"
    )


def test_postgres_data_dir_is_required_not_defaulted():
    """A default here is the registry's own directory in every checkout.

    Until 2026-09-11 the bind read `${PGDATA_HOST:-/srv/pgdata/dentalia}`,
    so a worktree's `docker compose up` started a second Postgres on the same
    files. One ran from 08-27 to 09-09 and the registry would not start on 09-11
    (docs/state/2026-09-11.md). Required-with-message is the fix; any default,
    whatever its value, reopens it for the next checkout that inherits it.
    """
    compose = yaml.safe_load((_ROOT / "docker-compose.yml").read_text())
    binds = [v for v in compose["services"]["postgres"]["volumes"]
             if str(v).endswith(":/var/lib/postgresql/data")]
    assert len(binds) == 1, binds
    source = binds[0].rsplit(":/var/lib/postgresql/data", 1)[0]
    assert source.startswith("${PGDATA_HOST:?"), (
        f"postgres data bind must be `${{PGDATA_HOST:?...}}` with no default, got {source!r}"
    )


# The client-server overlay (docker-compose.prod.yml, 2026-09-18). What a
# shared server needs and a dev checkout does not: the archive on a host path,
# a memory cap on the worker, and rotating logs. The merge itself is Compose's
# and cannot run here (no Docker CLI in the test image); these pin the shapes
# the merge depends on.

def _prod() -> dict:
    return yaml.safe_load((_ROOT / "docker-compose.prod.yml").read_text())


def _base() -> dict:
    return yaml.safe_load((_ROOT / "docker-compose.yml").read_text())


@pytest.mark.parametrize("service,mode", [("worker", ""), ("web", ":ro")])
def test_prod_overlay_binds_the_archive_where_the_base_mounts_the_volume(service, mode):
    """Compose merges `volumes` by target path, so the overlay replaces the
    `archive_data` mount only if it names the same target. A different target
    would ADD a mount and leave the archive in the volume, silently."""
    assert f"archive_data:/archive{mode}" in _base()["services"][service]["volumes"]
    assert _prod()["services"][service]["volumes"] == [
        "${ARCHIVE_HOST:?set ARCHIVE_HOST in .env}:/archive" + mode
    ]


def test_prod_overlay_caps_the_worker():
    assert _prod()["services"]["worker"]["mem_limit"] == "3g"


def test_prod_overlay_rotates_the_log_of_every_default_service():
    """A service added to the base without a profile runs on the server, and
    without an entry here its log grows without bound."""
    defaults = {n for n, s in _base()["services"].items() if not s.get("profiles")}
    prod = _prod()["services"]
    unrotated = {n for n in defaults
                 if (prod.get(n, {}).get("logging") or {}).get("options", {}).get("max-size") is None}
    assert not unrotated, sorted(unrotated)
