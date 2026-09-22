"""FETCH storage adapters (S1.4) — PRD §9 archive, handbook §4.

`fetch.url` archives every new document under our control, deduplicated by
content hash, addressable via `archive_url`. The physical store is hidden behind
`StorageAdapter` so nothing downstream knows which is live (invariant 11):

    LocalFsStore     (v1/dev/tests) — writes under a local archive root.
    GoogleDriveStore (v1 client)    — SKELETON. `put` raises until G7 (Drive auth
                                       mechanics: service-account vs OAuth, root
                                       folder) is answered by the client.

Archive layout (PRD §9, identical across both stores):

    {manufacturer_canonical}/{doc_type}/{content_hash[:12]}__{original_filename}

The hash prefix guarantees uniqueness + dedupe; the original filename is kept
for humans. One physical file regardless of how many items link to it; 10-year
retention, append-only, never delete (supersession is a DB relation).

`put` is idempotent (at-least-once delivery): re-storing the same content-
addressed path is a no-op that returns the same `archive_url`.
"""

from __future__ import annotations

import os
import pathlib
from typing import Protocol, runtime_checkable

__all__ = [
    "StorageAdapter",
    "LocalFsStore",
    "GoogleDriveStore",
    "make_storage_adapter",
]


@runtime_checkable
class StorageAdapter(Protocol):
    def put(self, body: bytes, path: str) -> str:
        """Archive `body` at layout-relative `path`; return its `archive_url`."""
        ...


class LocalFsStore:
    """Filesystem-backed archive under `root`. `archive_url` is `base_url`/path,
    or a `file://` URI when no base_url is configured (dev default)."""

    def __init__(self, root: str, base_url: str | None = None) -> None:
        self.root = pathlib.Path(root)
        self.base_url = base_url or None  # treat "" as unset

    def put(self, body: bytes, path: str) -> str:
        dest = self.root / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Content-addressed, so a COMPLETE existing file is identical and is
        # left alone -- but `exists()` alone trusted a stump. An interrupted
        # write (ENOSPC, an OOM kill, a Docker daemon restart under
        # unattended-upgrades) left a truncated file at the hashed path, the
        # transaction rolled back, and the retry then skipped the write and
        # handed EXTRACT the stump. Size is the test because truncation is the
        # failure mode; re-hashing every hit would read the whole archive over
        # time to catch only same-length corruption, which this cannot produce.
        try:
            complete = dest.stat().st_size == len(body)
        except FileNotFoundError:
            complete = False
        if not complete:
            # Same directory, so `os.replace` is atomic: the real name only
            # ever appears complete. The pid keeps two workers racing on the
            # same body off each other's temp file.
            tmp = dest.with_name(f".{dest.name}.{os.getpid()}.part")
            try:
                tmp.write_bytes(body)
                os.replace(tmp, dest)
            finally:
                tmp.unlink(missing_ok=True)
        if self.base_url:
            return f"{self.base_url.rstrip('/')}/{path}"
        return dest.resolve().as_uri()


class GoogleDriveStore:
    """Client-owned Drive archive (PRD §9 v1). SKELETON: construction is inert,
    but `put` raises until G7 is answered — auth mechanics (service-account vs
    OAuth) and the root folder are client input. When G7 lands only this class
    is implemented; the handler, path logic, and tests are untouched (inv. 11).
    """

    def __init__(self, folder_id: str | None = None) -> None:
        self.folder_id = folder_id or None

    def put(self, body: bytes, path: str) -> str:
        raise NotImplementedError(
            "G7: Drive auth unresolved (service-account vs OAuth, root folder). "
            "Set adapters.storage='local' until the client answers G7."
        )


def make_storage_adapter(cfg) -> StorageAdapter:
    """Select the storage adapter from `cfg.adapters.storage` (invariant 11)."""
    kind = cfg.adapters.storage
    if kind == "local":
        return LocalFsStore(cfg.storage.local_root, cfg.storage.base_url)
    if kind == "gdrive":
        return GoogleDriveStore(folder_id=cfg.storage.gdrive_folder_id)
    raise ValueError(f"unknown storage adapter: {kind!r}")
