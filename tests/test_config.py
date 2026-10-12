"""Config keys: the web process's access credentials, the queue's reclaim
default, and the extraction-ladder keys of 2026-10-12.

Env-only and secret: `WEB_API_KEYS` / `WEB_BC_LINK_KEY` never appear in a TOML
overlay committed to the repo.
"""

from __future__ import annotations

import dataclasses

import pytest

from app import config as config_mod
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


# --------------------------------------------------------------------------- #
# Extraction-ladder keys (plan 2026-10-12, Task 1). Plumbing only: nothing in
# the ladder reads them yet. Table-driven over the three load paths, so a key
# whose dataclass default and loader fallback disagree fails here, not in a
# container -- the drift the queue tests above were written after.
# --------------------------------------------------------------------------- #

#: (section, field, env var, TOML table, TOML key, plan default, override)
LADDER_KEYS = [
    ("models", "vision_first", "MODELS_VISION_FIRST", "models", "vision_first",
     "claude-haiku-5-5", "claude-sonnet-5-5"),
    ("models", "second_read", "MODELS_SECOND_READ", "models", "second_read",
     "claude-haiku-5-5", "claude-haiku-4-5"),
    ("extract", "image_settle", "EXTRACT_IMAGE_SETTLE", "extract", "image_settle",
     0.85, 0.9),
    ("gate", "agreement_min", "GATE_AGREEMENT_MIN", "gate", "agreement_min",
     0.70, 0.65),
]

_IDS = [f"{s}.{f}" for s, f, *_ in LADDER_KEYS]


def _section_class(section):
    return {f.name: f.type for f in dataclasses.fields(config_mod.Config)}[section]


@pytest.mark.parametrize("section, field, env, _t, _k, plan, _o", LADDER_KEYS, ids=_IDS)
def test_ladder_key_default_is_the_planned_one_and_reaches_the_process(
        monkeypatch, section, field, env, _t, _k, plan, _o):
    monkeypatch.delenv(env, raising=False)
    monkeypatch.delenv(config_mod.CONFIG_TOML_ENV, raising=False)
    cfg = load_config()
    effective = getattr(getattr(cfg, section), field)
    cls = getattr(config_mod, _section_class(section))
    assert effective == plan
    assert effective == getattr(cls, field)       # the dataclass default IS the effective one


@pytest.mark.parametrize("section, field, env, _t, _k, _p, override", LADDER_KEYS, ids=_IDS)
def test_ladder_key_reads_env(monkeypatch, section, field, env, _t, _k, _p, override):
    monkeypatch.setenv(env, str(override))
    assert getattr(getattr(load_config(), section), field) == override


@pytest.mark.parametrize("section, field, env, table, key, _p, override", LADDER_KEYS, ids=_IDS)
def test_ladder_key_reads_toml(monkeypatch, tmp_path, section, field, env, table, key,
                               _p, override):
    monkeypatch.delenv(env, raising=False)
    value = f'"{override}"' if isinstance(override, str) else repr(override)
    path = tmp_path / "overlay.toml"
    path.write_text(f"[{table}]\n{key} = {value}\n")
    monkeypatch.setenv(config_mod.CONFIG_TOML_ENV, str(path))
    assert getattr(getattr(load_config(), section), field) == override
