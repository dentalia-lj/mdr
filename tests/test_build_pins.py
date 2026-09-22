"""Every image installs pinned versions (constraints.txt).

pyproject.toml states floors (`>=`), so without a constraints file each build
resolves against whatever PyPI holds that day. On 2026-09-18 the three dev
images already disagreed -- anthropic 1.5.0 in `worker`, 1.1.0 in `test` -- so
the suite was not testing the versions production ran, and a first build on
the client server would have matched neither.

These guard the two ways that silently comes back: a dependency added to
pyproject.toml without a pin, and a `pip install` line that stops passing the
file.
"""
from __future__ import annotations

import pathlib
import re
import tomllib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _pins() -> dict[str, str]:
    out = {}
    for line in (_ROOT / "constraints.txt").read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        name, sep, version = line.partition("==")
        assert sep, f"not an exact pin: {line!r}"
        out[_norm(name)] = version
    return out


def _declared() -> set[str]:
    project = tomllib.loads((_ROOT / "pyproject.toml").read_text())["project"]
    reqs = list(project.get("dependencies", []))
    for group in project.get("optional-dependencies", {}).values():
        reqs.extend(group)
    return {_norm(re.match(r"[A-Za-z0-9._-]+", r).group(0)) for r in reqs}


def test_every_declared_dependency_is_pinned():
    missing = _declared() - set(_pins())
    assert not missing, f"in pyproject.toml, not in constraints.txt: {sorted(missing)}"


@pytest.mark.parametrize("dockerfile", ["Dockerfile", "Dockerfile.web"])
def test_every_pip_install_passes_the_constraints(dockerfile):
    text = (_ROOT / dockerfile).read_text()
    installs = re.findall(r"^RUN pip install .*$", text, flags=re.M)
    assert installs, dockerfile
    for line in installs:
        assert "-c constraints.txt" in line, line
    # The file has to be in the image before the first install reads it.
    first_copy = text.index("constraints.txt ./")
    assert first_copy < text.index(installs[0]), dockerfile


def test_playwright_pin_matches_the_base_image():
    """The base image ships browser binaries for one playwright release only;
    a client from another release looks for a revision that is not there."""
    base = re.search(r"playwright/python:v([0-9.]+)-", (_ROOT / "Dockerfile").read_text())
    assert base and _pins()["playwright"] == base.group(1)
