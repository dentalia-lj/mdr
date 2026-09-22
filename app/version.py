"""Source fingerprint: a stale-image detector that cannot be forgotten.

An image built before a change runs old code while reporting success, and every
symptom looks like a bug in the new code. It happened four times on 2026-08-12:
61 extraction jobs green through outdated handlers; `cli migrate` answering "up
to date" for a migration that existed on the host but not in the image; and a
resolve re-run twice executing the pre-change threshold. Two of those were
"verified" by grepping the container for a string, and the number that came back
looked like confirmation while being nothing of the sort.

Deliberately NOT a build-time stamp (`ARG GIT_SHA`). A build arg is one more
thing to pass, and forgetting it yields exactly the silent mismatch this exists
to catch. This is computed from the source at runtime, by the same function on
both sides, so it is always present and always current:

    python -m app.cli version                                    # host
    docker compose run --rm worker python -m app.cli version     # image

Different fingerprints mean the image is stale. Rebuild before trusting a run.
The worker logs it at startup, so a job that misbehaves can be traced to the
code that actually ran rather than the code in the editor.

Covers `app/**/*.py`, `migrations/*.sql` and `web/**/*.{py,html,css,js}` — the
files that decide behaviour. Docs, tests and playbooks are excluded: they change
constantly and a fingerprint that changes for a typo is one people learn to
ignore.

**`web/` joined on 2026-09-14, and it was the blind spot that proved the rule.**
Only `app/` and `migrations/` were hashed, and the web image carries neither
exclusively — it carries `web/`. So on that morning `./scripts/deploy.sh --check`
printed "Deployed and verified" while the running web container had no
`web/missing.py` at all, a different `web/app.py`, and answered 404 on
`/missing`, the screen the office is meant to work from. A check that is green
for a stale image is worse than no check. Templates count as behaviour for the
same reason the missing route did: what the page says is what the user gets.
Binary assets under `web/static` (fonts, images) stay out — they change with
neither code nor copy.

**Compare per root, not the combined digest, whenever the two sides may carry
different subsets of the source.** `Dockerfile.web` copies no `migrations/`, so
the web container's combined digest can never equal the host's and says nothing
about staleness — see `fingerprint_parts`, which is what `cli version` prints.
Measured 2026-09-03, that blind spot was hiding a real one: the running web
image was two files behind the tree.
"""

from __future__ import annotations

import hashlib
import pathlib
import typing

#: Root -> the glob patterns that decide behaviour inside it. Several per root,
#: because `web/` is code AND the words on the screen: a template-only change is
#: a change the user sees, and one pattern per root could not say so.
_SOURCES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("app", ("*.py",)),
    ("migrations", ("*.sql",)),
    ("web", ("*.py", "*.html", "*.css", "*.js")),
)
_SKIP_DIRS = {"__pycache__"}


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent


def _matching(base: pathlib.Path, root: pathlib.Path,
              patterns: tuple[str, ...]) -> list[pathlib.Path]:
    """Every behaviour-deciding file under one source root, sorted and unique.

    `set()` because two patterns can name one file if the list ever grows an
    overlap; hashing a file twice would make the digest depend on the pattern
    order rather than on the content.
    """
    out: set[pathlib.Path] = set()
    for pattern in patterns:
        out.update(
            p for p in base.rglob(pattern)
            if p.is_file() and not _SKIP_DIRS & set(p.relative_to(root).parts)
        )
    return sorted(out)


def _files(root: pathlib.Path) -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for subdir, patterns in _SOURCES:
        base = root / subdir
        if not base.is_dir():
            continue
        out.extend(_matching(base, root, patterns))
    return sorted(out)


class Part(typing.NamedTuple):
    """One source root's own digest. `present` is False when the root is not in
    this image at all, which is a different statement from "it is empty"."""

    root: str
    digest: str
    files: int
    present: bool

    def __str__(self) -> str:
        if not self.present:
            return f"  {self.root:<12} —            not in this image"
        return f"  {self.root:<12} {self.digest}  {self.files} file(s)"


def fingerprint_parts(root: pathlib.Path | str | None = None) -> list[Part]:
    """Per-root digests, so two images are only ever compared like with like.

    The combined `source_fingerprint` is worthless across images that carry
    DIFFERENT SUBSETS of the source. Measured 2026-09-03: `Dockerfile.web`
    copies no `migrations/` at all, so the web container hashes 71 files
    against the tree's 132 and its combined digest differs permanently --
    whether or not it is stale. An operator following the documented
    "compare and rebuild" would see a mismatch every single time, which is
    exactly the "fingerprint people learn to ignore" this module's own
    docstring warns about.

    Per root, the comparison works: the web image's `app` digest against the
    host's `app` digest is a real answer. On the same measurement the web image
    was genuinely two files behind (`app/schema_drift.py`,
    `app/handlers/playbook_probe.py`) and the combined number could not say so.
    """
    base = pathlib.Path(root) if root is not None else _repo_root()
    out: list[Part] = []
    for subdir, patterns in _SOURCES:
        d = base / subdir
        if not d.is_dir():
            out.append(Part(subdir, "", 0, False))
            continue
        files = _matching(d, base, patterns)
        h = hashlib.sha256()
        for path in files:
            h.update(str(path.relative_to(base)).replace("\\", "/").encode())
            h.update(b"\0")
            h.update(path.read_bytes())
            h.update(b"\0")
        out.append(Part(subdir, h.hexdigest()[:12], len(files), True))
    return out


def source_fingerprint(root: pathlib.Path | str | None = None) -> str:
    """12 hex chars over the behaviour-deciding source under `root`.

    Path-independent by construction: the digest mixes each file's path
    RELATIVE to `root` plus its bytes, because the host sees this tree at the
    host checkout and the container sees it at /app. Absolute
    paths would make every comparison differ and the check worthless.
    """
    base = pathlib.Path(root) if root is not None else _repo_root()
    h = hashlib.sha256()
    for path in _files(base):
        h.update(str(path.relative_to(base)).replace("\\", "/").encode())
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()[:12]
