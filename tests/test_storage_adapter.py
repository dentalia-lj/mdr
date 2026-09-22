"""StorageAdapter (S1.4) — LocalFsStore + GoogleDriveStore skeleton + factory.

Spec: docs/specs/fetch.md §1/§2. Invariant 11 (downstream never knows which
store is live). G7: live Drive deferred — GoogleDriveStore.put raises until the
client answers auth mechanics.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.adapters.storage import (
    GoogleDriveStore,
    LocalFsStore,
    make_storage_adapter,
)
from app.config import load_config

PDF = b"%PDF-1.4 fake body\n"
PATH = "Ivoclar/ec/abc123def456__cert.pdf"


def test_put_writes_body_and_returns_url(tmp_path):
    store = LocalFsStore(str(tmp_path), base_url="https://arch.example/root")
    url = store.put(PDF, PATH)
    assert (tmp_path / PATH).read_bytes() == PDF
    assert url == "https://arch.example/root/Ivoclar/ec/abc123def456__cert.pdf"


def test_put_creates_intermediate_dirs(tmp_path):
    store = LocalFsStore(str(tmp_path))
    store.put(PDF, "a/b/c/deep.pdf")
    assert (tmp_path / "a" / "b" / "c" / "deep.pdf").is_file()


def test_put_default_base_url_is_file_uri(tmp_path):
    store = LocalFsStore(str(tmp_path))  # no base_url
    url = store.put(PDF, PATH)
    assert url.startswith("file://")
    assert url.endswith("/Ivoclar/ec/abc123def456__cert.pdf")


def test_put_with_base_url_returns_served_path(tmp_path):
    store = LocalFsStore(str(tmp_path), base_url="/archive")
    url = store.put(b"%PDF-1.4 body", "VOCO/doc/abc123__x.pdf")
    assert url == "/archive/VOCO/doc/abc123__x.pdf"
    assert (tmp_path / "VOCO/doc/abc123__x.pdf").read_bytes() == b"%PDF-1.4 body"


def test_put_without_base_url_still_returns_file_uri(tmp_path):
    store = LocalFsStore(str(tmp_path), base_url="")
    url = store.put(b"x", "A/doc/h__f.pdf")
    assert url.startswith("file://")


def test_put_is_idempotent_one_physical_file(tmp_path):
    """Content-addressed re-run (at-least-once delivery) must not duplicate or
    error; returns the same url and leaves exactly one file."""
    store = LocalFsStore(str(tmp_path), base_url="https://arch.example")
    url1 = store.put(PDF, PATH)
    url2 = store.put(PDF, PATH)
    assert url1 == url2
    # exactly one physical file for that content-addressed path
    matches = list(tmp_path.rglob("*__cert.pdf"))
    assert len(matches) == 1
    assert matches[0].read_bytes() == PDF


def test_gdrive_store_put_raises_until_g7(tmp_path):
    store = GoogleDriveStore(folder_id="root-folder")
    with pytest.raises(NotImplementedError):
        store.put(PDF, PATH)


def test_factory_selects_local(tmp_path):
    cfg = load_config()
    cfg = replace(
        cfg,
        adapters=replace(cfg.adapters, storage="local"),
        storage=replace(cfg.storage, local_root=str(tmp_path)),
    )
    assert isinstance(make_storage_adapter(cfg), LocalFsStore)


def test_factory_selects_local_default():
    # Default flipped gdrive -> local 2026-07-31 ([storage-default], Denis):
    # GoogleDriveStore.put() raises until G7, so a default compose stack would
    # dead-letter every fetch. gdrive becomes the explicit opt-in when G7 lands.
    cfg = load_config()
    assert isinstance(make_storage_adapter(cfg), LocalFsStore)


def test_the_adapter_defaults_are_declared_in_exactly_one_place():
    """The 2026-07-31 ruling was applied to load_config() and not to the field
    it overrides, so `Adapters.storage` stayed "gdrive" for eleven weeks while
    every process ran on local -- a default that resolves to an adapter whose
    put() raises. Nothing caught it because every test went through
    load_config(), which passed its own literal.

    Asserted on the dataclass directly, which is the half no other test covers.
    Same failure as [renewal-horizon-default-ignored] and the gate thresholds:
    a value declared twice is a value that will disagree with itself."""
    from app.config import Adapters

    assert Adapters().storage == "local"
    assert Adapters().source == "csv"
    assert Adapters().search == "brave"
    # and the loader must hand back the same thing when nothing overrides it
    cfg = load_config()
    assert (cfg.adapters.storage, cfg.adapters.source, cfg.adapters.search) == (
        "local", "csv", "brave",
    )


def test_factory_selects_gdrive_when_configured():
    cfg = load_config()
    cfg = replace(cfg, adapters=replace(cfg.adapters, storage="gdrive"))
    assert isinstance(make_storage_adapter(cfg), GoogleDriveStore)


def test_factory_rejects_unknown():
    cfg = load_config()
    cfg = replace(cfg, adapters=replace(cfg.adapters, storage="s3-does-not-exist-yet"))
    with pytest.raises(ValueError):
        make_storage_adapter(cfg)


# --- B2: the archive write survives an interrupted write --------------------
#
# `put` used to be `if not dest.exists(): dest.write_bytes(body)`, so an
# interruption mid-write (ENOSPC, an OOM kill, a Docker daemon restart) left a
# truncated file at a content-addressed path. The DB transaction rolled back;
# the filesystem write did not. On retry `dest.exists()` was True, `put`
# returned the archive_url without writing, and EXTRACT ran against the stump.
# Measured 2026-09-22: 1414 of 1414 live files intact, so this has never bitten
# -- but it is silent, permanent, and nothing in the tree would ever detect it.


def test_put_rewrites_a_truncated_file(tmp_path):
    """A half-written file from a killed run is replaced, not trusted."""
    store = LocalFsStore(str(tmp_path))
    dest = tmp_path / PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(PDF[:5])          # what an interrupted write leaves

    store.put(PDF, PATH)

    assert dest.read_bytes() == PDF


def test_put_rewrites_a_zero_length_file(tmp_path):
    """The degenerate case: the open succeeded and nothing was written."""
    store = LocalFsStore(str(tmp_path))
    dest = tmp_path / PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.touch()

    store.put(PDF, PATH)

    assert dest.read_bytes() == PDF


def test_put_leaves_an_identical_file_alone(tmp_path):
    """Content-addressed dedupe still holds: a complete hit is not rewritten."""
    store = LocalFsStore(str(tmp_path))
    store.put(PDF, PATH)
    dest = tmp_path / PATH
    before = dest.stat().st_mtime_ns

    store.put(PDF, PATH)

    assert dest.stat().st_mtime_ns == before
    assert dest.read_bytes() == PDF


def test_put_leaves_no_part_file_behind(tmp_path):
    """The temp sibling is renamed, never left for backfill.scan to trip over."""
    store = LocalFsStore(str(tmp_path))
    store.put(PDF, PATH)

    assert [p.name for p in (tmp_path / PATH).parent.iterdir()] == [
        "abc123def456__cert.pdf"
    ]
