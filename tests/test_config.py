"""Config keys for the web process's access credentials.

Env-only and secret: `WEB_API_KEYS` / `WEB_BC_LINK_KEY` never appear in a TOML
overlay committed to the repo.
"""

from __future__ import annotations

from app.config import load_config


def test_web_access_keys_default_to_empty(monkeypatch):
    """Empty means the class is disabled, never means 'allow everyone'."""
    for var in ("WEB_API_KEYS", "WEB_BC_LINK_KEY", "WEB_PUBLIC_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    cfg = load_config()
    assert cfg.web.api_keys == ""
    assert cfg.web.bc_link_key == ""
    assert cfg.web.public_base_url == ""


def test_web_access_keys_read_from_env(monkeypatch):
    monkeypatch.setenv("WEB_API_KEYS", "shop-key-1,shop-key-2")
    monkeypatch.setenv("WEB_BC_LINK_KEY", "bc-shared")
    monkeypatch.setenv("WEB_PUBLIC_BASE_URL", "https://api.cw.dentalia.si")
    cfg = load_config()
    assert cfg.web.api_keys == "shop-key-1,shop-key-2"
    assert cfg.web.bc_link_key == "bc-shared"
    assert cfg.web.public_base_url == "https://api.cw.dentalia.si"


def test_web_operator_users_default_to_empty(monkeypatch):
    """Empty means every login is an operator (office UI spec § 2), which keeps
    today's single shared login working until named logins exist."""
    monkeypatch.delenv("WEB_OPERATOR_USERS", raising=False)
    assert load_config().web.operator_users == ()


def test_web_operator_users_parse_a_comma_separated_list(monkeypatch):
    monkeypatch.setenv("WEB_OPERATOR_USERS", " a, b ,")
    assert load_config().web.operator_users == ("a", "b")


# --------------------------------------------------------------------------- #
# The dataclass default is not the effective default.
#
# `load_config()` passes its own literal fallback to `_int` for every key, so a
# field default on the dataclass is dead code unless the two agree. Raising
# `Queue.visibility_timeout_s` to 1800 on 2026-09-02 changed only the dataclass
# and left the literal at 300, so the running worker still reclaimed jobs at
# five minutes -- the exact drift `docs/dev/config-reference.md` opens by
# warning about, caught only by reading the value out of the container.
# --------------------------------------------------------------------------- #

def test_queue_visibility_timeout_default_is_the_one_that_reaches_the_worker(
        monkeypatch):
    monkeypatch.delenv("QUEUE_VISIBILITY_TIMEOUT_S", raising=False)
    assert load_config().queue.visibility_timeout_s == 1800


def test_queue_visibility_timeout_still_reads_env(monkeypatch):
    """The raise is a default, not a hard-coding: an operator must still be
    able to put it back for one environment."""
    monkeypatch.setenv("QUEUE_VISIBILITY_TIMEOUT_S", "300")
    assert load_config().queue.visibility_timeout_s == 300
