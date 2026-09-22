#!/usr/bin/env python3
"""Mirror the dentalia.si MDR publication into the imports corpus.

The client publishes their compliance documents at
`https://www.dentalia.si/mdr-dokumentacija/`. The page itself is an empty JS
shell; the whole tree comes from one public JSON endpoint, `/api/data`, as a
nested map of `{name, path, parents}` nodes, and each file is a plain GET under
`/api/files/<BRAND>/...` carrying `ETag` and `Last-Modified`.

That is a better corpus source than the SFTP drop it mirrors: one request
inventories the tree, every file carries a change signal, and nothing needs
credentials. This script pulls it down into the shape `backfill.scan` already
walks -- one directory per manufacturer -- so the import path afterwards is the
existing per-brand command, with no new job type and no new adapter.

Runs on the HOST, not in a container: the worker mounts `/imports` read-only on
purpose (it must never write to a hand-managed dump), and stdlib-only means no
image rebuild and no new dependency.

    ./scripts/mirror-mdr-site.py --dry-run          # inventory, no bytes
    ./scripts/mirror-mdr-site.py                    # first run, ~2 GB
    ./scripts/mirror-mdr-site.py --brand GC -v      # one brand, loud

Re-runs are conditional: the local file's mtime is sent as `If-Modified-Since`
and a 304 costs one round trip and no bytes. The filesystem is the only state;
there is no manifest to keep in sync.

Nothing is ever deleted. A file that disappears from the index is reported, and
`--prune --yes` moves it into `.removed/<date>/`, still on disk.
"""

from __future__ import annotations

import argparse
import dataclasses
import email.utils
import json
import os
import pathlib
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

DEFAULT_BASE = "https://www.dentalia.si"
INDEX_PATH = "/api/data"
FILES_PREFIX = "/api/files/"
DEFAULT_SUBDIR = "dentalia-web"
USER_AGENT = "dentalia-compliance-mirror/1.0 (+mdr@dentalia.si)"

# ext4 caps a single name at 255 bytes; the index's longest today is 119.
MAX_COMPONENT_BYTES = 255
RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


class MirrorError(Exception):
    """Fatal: the endpoint did not answer in the shape this script knows."""


@dataclasses.dataclass(frozen=True)
class Entry:
    """One published file: where it lives on the site, where it lands here."""

    brand: str
    parts: tuple[str, ...]      # brand first, filename last
    url_path: str               # percent-encoded, as the index gives it

    @property
    def relpath(self) -> str:
        return "/".join(self.parts)

    @property
    def name(self) -> str:
        return self.parts[-1]


# --- pure helpers (unit-tested, no network) ---------------------------------


def check_component(part: str) -> None:
    """Reject anything that could escape the destination directory.

    The index is external input served by someone else's CMS. It is clean
    today (2771 entries, nothing unsafe, longest component 119 bytes), which
    is a reading, not a guarantee.
    """
    if part in ("", ".", ".."):
        raise ValueError(f"unsafe path component: {part!r}")
    if "/" in part or "\\" in part or "\x00" in part:
        raise ValueError(f"unsafe path component: {part!r}")
    if len(part.encode("utf-8")) > MAX_COMPONENT_BYTES:
        raise ValueError(f"path component too long ({len(part.encode())}B): {part!r}")


def parse_index(obj) -> tuple[list[Entry], list[str]]:
    """Flatten the nested index into entries, collecting rejects rather than raising.

    A node carrying both `name` and `path` is a file; any other dict is a
    folder whose keys are its children. Folder keys are plain text, the `path`
    field is percent-encoded, and the leaf key equals `name` -- all three
    verified against the live index before this was written.
    """
    if not isinstance(obj, dict) or not obj:
        raise MirrorError("index is not a non-empty object; the endpoint changed shape")

    entries: list[Entry] = []
    rejected: list[str] = []

    def walk(node, parts: list[str]) -> None:
        if not isinstance(node, dict):
            rejected.append(f"{'/'.join(parts)}: not an object ({type(node).__name__})")
            return
        name, path = node.get("name"), node.get("path")
        if isinstance(name, str) and isinstance(path, str):
            leaf = parts[:-1] + [name]          # trust `name` over the key
            if not path.startswith(FILES_PREFIX):
                rejected.append(f"{'/'.join(leaf)}: path outside {FILES_PREFIX}")
                return
            if len(leaf) < 2:
                rejected.append(f"{'/'.join(leaf)}: file sits above any brand folder")
                return
            try:
                for p in leaf:
                    check_component(p)
            except ValueError as exc:
                rejected.append(str(exc))
                return
            entries.append(Entry(brand=leaf[0], parts=tuple(leaf), url_path=path))
            return
        for key, child in node.items():
            walk(child, parts + [key])

    walk(obj, [])
    if not entries:
        raise MirrorError("index parsed but held no files; the endpoint changed shape")
    return entries, rejected


def http_date(ts: float) -> str:
    return email.utils.formatdate(ts, usegmt=True)


def parse_http_date(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return email.utils.parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError):
        return None


def importable(entry: Entry) -> bool:
    """Would `backfill.scan` pick this file up? It walks `*.pdf` only."""
    return entry.name.lower().endswith(".pdf")


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


# --- network ----------------------------------------------------------------


def _open(url: str, headers: dict[str, str], timeout: float):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers})
    return urllib.request.urlopen(req, timeout=timeout)


def fetch_index(base: str, timeout: float) -> dict:
    url = base.rstrip("/") + INDEX_PATH
    try:
        with _open(url, {"Accept": "application/json"}, timeout) as resp:
            raw = resp.read()
    except (urllib.error.URLError, OSError) as exc:
        raise MirrorError(f"cannot read the index at {url}: {exc}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MirrorError(f"index at {url} is not JSON: {exc}") from exc


def download(url: str, dest: pathlib.Path, *, timeout: float, retries: int,
             delay: float) -> tuple[str, int]:
    """Conditionally fetch one file. Returns (outcome, bytes_written).

    Outcome is `new`, `updated` or `unchanged`. The mtime is written only
    after a complete body lands, so an interrupted run leaves a file that the
    next run re-fetches rather than one it silently trusts.
    """
    headers: dict[str, str] = {}
    existed = dest.exists()
    if existed:
        headers["If-Modified-Since"] = http_date(dest.stat().st_mtime)

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f".{dest.name}.part"

    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with _open(url, headers, timeout) as resp:
                mtime = parse_http_date(resp.headers.get("Last-Modified"))
                with open(tmp, "wb") as fh:
                    shutil.copyfileobj(resp, fh, 1 << 16)
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                return "unchanged", 0
            if exc.code in RETRY_STATUS and attempt < retries:
                last = exc
                time.sleep(min(30.0, max(delay, 1.0) * (2 ** attempt)))
                continue
            raise
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            if attempt < retries:
                last = exc
                time.sleep(min(30.0, max(delay, 1.0) * (2 ** attempt)))
                continue
            raise
        else:
            nbytes = tmp.stat().st_size
            os.replace(tmp, dest)
            if mtime is not None:
                os.utime(dest, (mtime, mtime))
            return ("updated" if existed else "new"), nbytes
        finally:
            # A failed or retried attempt leaves no half file behind. The real
            # file is only ever replaced by a complete one, above.
            tmp.unlink(missing_ok=True)
    raise MirrorError(f"exhausted retries for {url}: {last}")


# --- run --------------------------------------------------------------------


def default_dest() -> pathlib.Path:
    root = os.environ.get("IMPORTS_HOST") or "./imports"
    return pathlib.Path(root).expanduser() / DEFAULT_SUBDIR


def find_orphans(dest: pathlib.Path, entries: list[Entry],
                 brands: set[str]) -> list[pathlib.Path]:
    """Local files under the scanned brands that the index no longer lists."""
    known = {dest / e.relpath for e in entries}
    out = []
    for brand in sorted(brands):
        folder = dest / brand
        if not folder.is_dir():
            continue
        for p in sorted(folder.rglob("*")):
            if p.is_file() and p not in known and not p.name.startswith("."):
                out.append(p)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Mirror the dentalia.si MDR publication into the imports corpus.")
    ap.add_argument("--dest", type=pathlib.Path, default=None,
                    help="destination root (default: $IMPORTS_HOST/%s)" % DEFAULT_SUBDIR)
    ap.add_argument("--base-url", default=DEFAULT_BASE)
    ap.add_argument("--container-imports", default="/imports",
                    help="where $IMPORTS_HOST is mounted in the worker (default /imports); "
                         "only used to print the backfill.scan commands")
    ap.add_argument("--brand", action="append", default=None,
                    help="only this brand folder; repeatable, case-insensitive")
    ap.add_argument("--delay", type=float, default=0.25,
                    help="seconds between requests (default 0.25) -- their production site")
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0, help="stop after N downloads (smoke run)")
    ap.add_argument("--dry-run", action="store_true", help="inventory only, fetch nothing")
    ap.add_argument("--prune", action="store_true",
                    help="move files no longer in the index into .removed/<date>/")
    ap.add_argument("--yes", action="store_true", help="required by --prune")
    ap.add_argument("-v", "--verbose", action="store_true", help="one line per file")
    args = ap.parse_args(argv)

    dest = (args.dest or default_dest()).expanduser()
    base = args.base_url.rstrip("/")

    print(f"index   {base}{INDEX_PATH}", file=sys.stderr)
    index = fetch_index(base, args.timeout)
    entries, rejected = parse_index(index)

    all_brands = sorted({e.brand for e in entries})
    if args.brand:
        wanted = {b.casefold() for b in args.brand}
        entries = [e for e in entries if e.brand.casefold() in wanted]
        missing = wanted - {e.brand.casefold() for e in entries}
        if missing:
            print(f"error: no such brand in the index: {', '.join(sorted(missing))}",
                  file=sys.stderr)
            print(f"known: {', '.join(all_brands)}", file=sys.stderr)
            return 2
    brands = sorted({e.brand for e in entries})

    print(f"dest    {dest}", file=sys.stderr)
    print(f"files   {len(entries)} in {len(brands)} brand folder(s)"
          f"{'' if not rejected else f', {len(rejected)} rejected'}", file=sys.stderr)
    for r in rejected:
        print(f"  reject {r}", file=sys.stderr)

    if args.dry_run:
        per_brand = {b: sum(1 for e in entries if e.brand == b) for b in brands}
        for b in brands:
            pdfs = sum(1 for e in entries if e.brand == b and importable(e))
            print(f"  {b:<50s} {per_brand[b]:5d} files  {pdfs:5d} pdf")
        print("\ndry run: nothing fetched")
        return 0

    # Provenance: keep the index we acted on. The dot-directory is invisible to
    # backfill.scan, which is pointed at a brand folder.
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    idx_dir = dest / ".index"
    idx_dir.mkdir(parents=True, exist_ok=True)
    (idx_dir / f"api-data-{stamp}.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")

    counts = {"new": 0, "updated": 0, "unchanged": 0, "failed": 0}
    per_brand: dict[str, dict[str, int]] = {
        b: {"new": 0, "updated": 0, "unchanged": 0, "failed": 0, "bytes": 0} for b in brands}
    failures: list[tuple[str, str]] = []
    written = 0
    done = 0
    interrupted = False

    try:
        for entry in sorted(entries, key=lambda e: e.parts):
            if args.limit and (counts["new"] + counts["updated"]) >= args.limit:
                print(f"--limit {args.limit} reached", file=sys.stderr)
                break
            target = dest / entry.relpath
            url = base + entry.url_path
            try:
                outcome, nbytes = download(url, target, timeout=args.timeout,
                                           retries=args.retries, delay=args.delay)
            except Exception as exc:                      # noqa: BLE001 - reported, not raised
                counts["failed"] += 1
                per_brand[entry.brand]["failed"] += 1
                failures.append((entry.relpath, str(exc)))
                if args.verbose:
                    print(f"  FAIL  {entry.relpath}: {exc}", file=sys.stderr)
            else:
                counts[outcome] += 1
                per_brand[entry.brand][outcome] += 1
                per_brand[entry.brand]["bytes"] += nbytes
                written += nbytes
                if args.verbose:
                    print(f"  {outcome:<9s} {entry.relpath} ({human(nbytes)})",
                          file=sys.stderr)
            done += 1
            if not args.verbose and done % 50 == 0:
                print(f"  {done}/{len(entries)} ... {human(written)}", file=sys.stderr)
            if args.delay:
                time.sleep(args.delay)   # politeness applies to the 304s too
    except KeyboardInterrupt:
        interrupted = True
        print("\ninterrupted -- the mirror is resumable, re-run to continue",
              file=sys.stderr)

    print()
    print(f"{'brand':<50s} {'new':>5s} {'upd':>5s} {'same':>5s} {'fail':>5s} {'bytes':>9s}")
    for b in brands:
        s = per_brand[b]
        if not any(s.values()):
            continue
        print(f"{b:<50s} {s['new']:5d} {s['updated']:5d} {s['unchanged']:5d} "
              f"{s['failed']:5d} {human(s['bytes']):>9s}")
    print(f"{'TOTAL':<50s} {counts['new']:5d} {counts['updated']:5d} "
          f"{counts['unchanged']:5d} {counts['failed']:5d} {human(written):>9s}")

    not_pdf = [e for e in entries if not importable(e)]
    if not_pdf:
        print(f"\n{len(not_pdf)} mirrored file(s) are not .pdf, so backfill.scan "
              f"(which walks *.pdf) will not import them:")
        for e in sorted(not_pdf, key=lambda e: e.parts)[:20]:
            print(f"  {e.relpath}")
        if len(not_pdf) > 20:
            print(f"  ... and {len(not_pdf) - 20} more")

    orphans = find_orphans(dest, entries, set(brands))
    if orphans:
        print(f"\n{len(orphans)} local file(s) are no longer in the index:")
        for p in orphans[:20]:
            print(f"  {p.relative_to(dest)}")
        if len(orphans) > 20:
            print(f"  ... and {len(orphans) - 20} more")
        if args.prune and args.yes:
            quarantine = dest / ".removed" / stamp
            for p in orphans:
                moved = quarantine / p.relative_to(dest)
                moved.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(p), str(moved))
            print(f"  moved into {quarantine} (nothing deleted)")
        elif args.prune:
            print("  --prune needs --yes; left in place")
        else:
            print("  left in place (--prune --yes moves them to .removed/)")

    if failures:
        print(f"\n{len(failures)} failure(s):")
        for rel, why in failures[:20]:
            print(f"  {rel}: {why}")
        if len(failures) > 20:
            print(f"  ... and {len(failures) - 20} more")

    touched = [b for b in brands if per_brand[b]["new"] or per_brand[b]["updated"]]
    if touched:
        # backfill.scan takes the path the WORKER sees, never a host path: the
        # corpus is bind-mounted read-only at $IMPORTS_HOST -> /imports.
        imports_root = pathlib.Path(
            os.environ.get("IMPORTS_HOST") or "./imports").expanduser().resolve()
        try:
            inside = dest.resolve().relative_to(imports_root)
            container = pathlib.PurePosixPath(args.container_imports) / inside
        except ValueError:
            container = None
        print(f"\nImport these {len(touched)} brand(s), one job per brand:")
        if container is None:
            print(f"  WARNING: {dest} is not under IMPORTS_HOST ({imports_root}), so the "
                  f"worker cannot see it. Move it there, or pass --container-imports.")
        for b in touched:
            folder = f"{container}/{b}" if container else f"<worker path>/{b}"
            payload = json.dumps({"drive_folder": folder})
            print(f"  docker compose run --rm --no-deps worker python -m app.cli enqueue \\\n"
                  f"    backfill.scan 'backfill:{b}:{stamp}' \\\n"
                  f"    --payload '{payload}' --priority interactive")

    if interrupted:
        return 130
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
