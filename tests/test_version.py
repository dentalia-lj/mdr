"""Source fingerprint — the stale-image detector.

Four incidents in one day (2026-08-12): 61 extraction jobs run through outdated
handler code and reported green; `cli migrate` answering "up to date" while a
migration existed on the host but not in the image; and a resolve re-run twice
executing an old threshold. In two of them the check I ran (grepping the
container for a string) returned a number that looked like confirmation and was
not.

So the fingerprint is computed FROM THE SOURCE at runtime rather than stamped at
build time: a build arg is one more thing to forget, and forgetting it produces
the same silent mismatch. Same function, host and container, over the files that
decide behaviour.
"""

from __future__ import annotations

import pathlib

from app import version


def test_fingerprint_is_stable_across_calls():
    assert version.source_fingerprint() == version.source_fingerprint()


def test_fingerprint_is_short_and_hex():
    fp = version.source_fingerprint()
    assert len(fp) == 12
    int(fp, 16)                                   # raises if not hex


def test_fingerprint_changes_when_a_source_file_changes(tmp_path):
    root = tmp_path / "src"
    (root / "app").mkdir(parents=True)
    (root / "migrations").mkdir()
    handler = root / "app" / "h.py"
    handler.write_text("x = 1\n")
    (root / "migrations" / "001.sql").write_text("SELECT 1;\n")

    before = version.source_fingerprint(root)
    handler.write_text("x = 2\n")                 # the stale-code case

    assert version.source_fingerprint(root) != before


def test_fingerprint_changes_when_a_migration_is_added(tmp_path):
    # The `migrate: up to date` incident: the file existed on the host and not
    # in the image, so migrations count as behaviour.
    root = tmp_path / "src"
    (root / "app").mkdir(parents=True)
    (root / "migrations").mkdir()
    (root / "app" / "h.py").write_text("x = 1\n")
    (root / "migrations" / "001.sql").write_text("SELECT 1;\n")

    before = version.source_fingerprint(root)
    (root / "migrations" / "002.sql").write_text("SELECT 2;\n")

    assert version.source_fingerprint(root) != before


def test_fingerprint_ignores_files_that_do_not_decide_behaviour(tmp_path):
    root = tmp_path / "src"
    (root / "app").mkdir(parents=True)
    (root / "migrations").mkdir()
    (root / "app" / "h.py").write_text("x = 1\n")

    before = version.source_fingerprint(root)
    (root / "app" / "notes.md").write_text("docs change\n")
    (root / "app" / "h.pyc").write_bytes(b"\x00")
    (root / "app" / "__pycache__").mkdir()

    assert version.source_fingerprint(root) == before


def test_fingerprint_is_independent_of_the_root_path(tmp_path):
    # The host sees the host checkout, the container sees /app. Identical
    # content must fingerprint identically or the comparison is useless.
    a, b = tmp_path / "host", tmp_path / "app"
    for root in (a, b):
        (root / "app").mkdir(parents=True)
        (root / "migrations").mkdir()
        (root / "app" / "h.py").write_text("x = 1\n")
        (root / "migrations" / "001.sql").write_text("SELECT 1;\n")

    assert version.source_fingerprint(a) == version.source_fingerprint(b)


def test_real_tree_fingerprints():
    repo = pathlib.Path(version.__file__).resolve().parent.parent
    assert version.source_fingerprint(repo)


# --------------------------------------------------------------------------- #
# Per-root parts: the combined digest lies across images carrying different
# subsets of the source, which is every comparison involving `web`.
# --------------------------------------------------------------------------- #
def _tree(base: pathlib.Path, *, migrations: bool, web: bool = False) -> pathlib.Path:
    (base / "app").mkdir(parents=True)
    (base / "app" / "handler.py").write_text("x = 1\n")
    if migrations:
        (base / "migrations").mkdir()
        (base / "migrations" / "001_a.sql").write_text("SELECT 1;\n")
    if web:
        (base / "web" / "templates").mkdir(parents=True)
        (base / "web" / "app.py").write_text("routes = 1\n")
        (base / "web" / "templates" / "page.html").write_text("<p>hello</p>\n")
    return base


def test_a_root_the_image_does_not_carry_is_reported_absent_not_empty(tmp_path):
    """`Dockerfile.web` COPYs no `migrations/`. "Not in this image" and "in this
    image and empty" are different facts and the report must not conflate
    them."""
    parts = {p.root: p for p in version.fingerprint_parts(
        _tree(tmp_path, migrations=False))}

    assert parts["migrations"].present is False
    assert parts["migrations"].digest == ""
    assert parts["app"].present is True
    assert parts["app"].files == 1


def test_the_app_digest_matches_across_images_that_differ_only_in_migrations(tmp_path):
    """The whole point. Two trees with identical `app/` and different
    `migrations/` presence: the COMBINED digest differs (which is what makes it
    useless for web-vs-host), while the per-root `app` digest agrees — so a
    real comparison is still possible."""
    with_m = _tree(tmp_path / "worker", migrations=True)
    without_m = _tree(tmp_path / "web", migrations=False)

    assert version.source_fingerprint(with_m) != version.source_fingerprint(without_m)

    app_of = lambda root: next(  # noqa: E731 - one expression, read once
        p.digest for p in version.fingerprint_parts(root) if p.root == "app")
    assert app_of(with_m) == app_of(without_m)


def test_a_changed_app_file_moves_the_app_digest_even_with_migrations_absent(tmp_path):
    """The staleness the combined number could not see: on 2026-09-03 the web
    image was two `app/` files behind and its digest differed anyway, for the
    unrelated reason that it carries no migrations."""
    root = _tree(tmp_path, migrations=False)
    app_of = lambda r: next(  # noqa: E731
        p.digest for p in version.fingerprint_parts(r) if p.root == "app")
    before = app_of(root)

    (root / "app" / "new_module.py").write_text("y = 2\n")

    assert app_of(root) != before


def test_parts_render_one_line_each(tmp_path):
    lines = [str(p) for p in version.fingerprint_parts(_tree(tmp_path, migrations=True))]
    assert all("\n" not in line for line in lines)
    assert any("migrations" in line for line in lines)


# --------------------------------------------------------------------------- #
# web/ — added 2026-09-14, after a green check over a three-commit-stale image
# --------------------------------------------------------------------------- #
def _web_of(root: pathlib.Path) -> str:
    return next(p.digest for p in version.fingerprint_parts(root) if p.root == "web")


def test_a_new_web_module_moves_the_web_digest(tmp_path):
    """The live case on 2026-09-14: `web/missing.py` existed in the tree and not
    in the running image, `/missing` answered 404, and `deploy.sh --check`
    printed "Deployed and verified" because no hashed root had moved."""
    root = _tree(tmp_path, migrations=False, web=True)
    before = _web_of(root)

    (root / "web" / "missing.py").write_text("def board(): ...\n")

    assert _web_of(root) != before


def test_a_template_only_change_moves_the_web_digest(tmp_path):
    """A template is what the user reads. An image serving last week's wording
    is stale in the way that matters most to the person using it."""
    root = _tree(tmp_path, migrations=False, web=True)
    before = _web_of(root)

    (root / "web" / "templates" / "page.html").write_text("<p>goodbye</p>\n")

    assert _web_of(root) != before


def test_the_worker_image_carrying_no_web_reports_absent(tmp_path):
    """`Dockerfile`'s `app` stage COPYs no `web/`. Absent is a legitimate answer
    there and must never read as stale, exactly as `migrations` is absent from
    the web image."""
    parts = {p.root: p for p in version.fingerprint_parts(
        _tree(tmp_path, migrations=True, web=False))}

    assert parts["web"].present is False
    assert parts["web"].digest == ""


def test_a_binary_asset_does_not_move_the_web_digest(tmp_path):
    """Fonts and images change with neither code nor copy, and a fingerprint
    that moves for them is one people learn to ignore."""
    root = _tree(tmp_path, migrations=False, web=True)
    (root / "web" / "static").mkdir()
    before = _web_of(root)

    (root / "web" / "static" / "f.woff2").write_bytes(b"\x00\x01")

    assert _web_of(root) == before


def test_the_real_tree_carries_all_three_roots():
    """A guard on the configuration itself: dropping a root here would make the
    check silently narrower, which is the failure this whole module exists for."""
    repo = pathlib.Path(version.__file__).resolve().parent.parent
    parts = {p.root: p for p in version.fingerprint_parts(repo)}

    assert set(parts) == {"app", "migrations", "web"}
    assert all(p.present and p.files for p in parts.values())
