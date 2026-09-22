"""The slim web image's import closure.

`Dockerfile.web` installs `.[web]`, deliberately NOT `.[extract]`: the UI is a
job producer and never parses a PDF. Nothing in the suite noticed, because the
`test` service is built from `Dockerfile` with `.[extract,...]`, so PyMuPDF is
importable in every other test and a web-side import of the extractor stack
looks fine right up until the image is built.

It was not fine. On 2026-08-27 `_parse` grew `from app.extract.tiers import
TARGET`, which chains to `app.extract.pdf` and `import pymupdf`. In the web
container that raised for EVERY playbook row: `/playbooks` rendered "38
playbook file(s) failed to parse", an empty table, and therefore no link to the
per-playbook editor, which 404s on its own because it reads the same loader.

These tests run the imports in a subprocess with the extract-only dependencies
blocked, which is the one thing the container has that this container does not.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from app.extract.target import TARGET

REPO = pathlib.Path(__file__).resolve().parent.parent

#: Everything the worker installs and `.[web]` does not. Ground-truthed against
#: the running web container on 2026-08-27 rather than read off `pyproject.toml`:
#: all eight packages are absent there, and `fastapi`/`jinja2`/`psycopg`/
#: `multipart` are present. `fitz` is pymupdf's legacy import name, blocked
#: alongside it so an old-style import cannot slip through.
_ABSENT_FROM_WEB_IMAGE = (
    "pymupdf", "fitz", "pdfplumber", "anthropic",
    "rapidfuzz", "httpx", "pandas", "openpyxl", "playwright",
)

#: Refuse the packages `.[web]` does not install, before anything imports them.
#: A meta_path finder rather than `sys.modules[name] = None` so submodules
#: (`pymupdf.utils`) are refused too, exactly as a missing install would.
_BLOCK = """
import sys

_ABSENT = set(('pymupdf', 'fitz', 'pdfplumber', 'anthropic', 'rapidfuzz', 'httpx', 'pandas', 'openpyxl', 'playwright'))


class _NoExtractDeps:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in _ABSENT:
            raise ImportError(f"No module named {name!r}")
        return None


sys.meta_path.insert(0, _NoExtractDeps())
"""


def _run_without_extract_deps(body: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", _BLOCK + body],
        cwd=REPO, capture_output=True, text=True, timeout=120,
    )


def test_the_blocker_actually_blocks():
    """Guard the guard. If PyMuPDF became importable under `_BLOCK` these tests
    would pass while proving nothing."""
    done = _run_without_extract_deps(
        "import pymupdf\nprint('NOT BLOCKED')\n")
    assert done.returncode != 0, done.stdout
    assert "No module named" in done.stderr, done.stderr


def test_parse_reads_extract_hints_without_the_extractor():
    """`_parse` validates hint keys against `TARGET`. Getting that list must
    not require the extractor: DISCOVER imports this module, and so does the
    web UI, and neither has PyMuPDF."""
    done = _run_without_extract_deps("""
from app import playbooks

pb = playbooks._parse("acme", {
    "manufacturer": "Acme",
    "bc_codes": [{"code": "A1"}],
    "extract_hints": {"general": "certs are in the annex"},
})
print("PARSED", pb.slug, pb.manufacturer)

try:
    playbooks._parse("acme", {
        "manufacturer": "Acme",
        "extract_hints": {"validity_form": "typo"},
    })
except ValueError as exc:
    print("REFUSED", "validity_form" in str(exc))
else:
    raise AssertionError("a bogus hint key was accepted")
""")
    assert done.returncode == 0, done.stderr
    assert "PARSED acme Acme" in done.stdout, done.stdout
    assert "REFUSED True" in done.stdout, done.stdout


def test_playbook_editor_route_module_imports_without_the_extractor():
    """`web/registry.py` builds the editor's hint-key list off `TARGET` too.
    Importing the module is not enough to prove it -- that import is inside the
    route -- so run the route's own statement."""
    pytest.importorskip("fastapi")
    done = _run_without_extract_deps("""
import web.registry
from app.extract.target import TARGET

print("KEYS", len(list(TARGET) + ["general"]))
""")
    assert done.returncode == 0, done.stderr
    assert f"KEYS {len(TARGET) + 1}" in done.stdout, done.stdout


def test_tiers_still_exports_the_same_list():
    """The leaf is the definition; `tiers.TARGET` is the address the extractor,
    `tools/` and the existing tests read. They must not drift apart."""
    from app.extract import tiers

    assert tiers.TARGET is TARGET


#: Every module under `web/`. The package is small and flat, so this is a list
#: rather than a walk -- a new module has to be added here, which is the point:
#: nothing should join the web image's import graph without someone confirming
#: it runs in that image.
WEB_MODULES = [
    "web.access", "web.app", "web.bc_push_view", "web.catalogue", "web.failures",
    "web.item_docs", "web.item_link", "web.missing", "web.onboarding", "web.registry",
    "web.scheduler_view", "web.words",
]


def test_every_web_module_imports_without_the_worker_only_packages():
    """The general form of the bug this file was written for.

    `_parse` reaching for PyMuPDF was one instance; the class is any module the
    web image loads acquiring a top-level import of something `.[web]` does not
    install. That cannot fail in this container -- the test image installs the
    worker's dependency set -- so it fails in production instead, as a page
    that renders wrong or a route that 500s.

    A module needing one of these legitimately is not forbidden; it has to
    import it inside the function that uses it, on a path the web image never
    takes. This test is what makes that a decision rather than an accident."""
    pytest.importorskip("fastapi")
    body = "\n".join(f"import {m}" for m in WEB_MODULES) + '\nprint("ALL IMPORTED")\n'
    done = _run_without_extract_deps(body)
    assert done.returncode == 0, done.stderr
    assert "ALL IMPORTED" in done.stdout, done.stdout


def test_web_modules_list_is_complete():
    """The list above is only a guard while it names every module."""
    on_disk = {
        f"web.{p.stem}"
        for p in (REPO / "web").glob("*.py")
        if p.stem != "__init__"
    }
    assert on_disk == set(WEB_MODULES), on_disk.symmetric_difference(WEB_MODULES)
