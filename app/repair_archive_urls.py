"""One-off repair: re-home documents whose archive_url is a corpus path.

Backfill runs before the archive fix recorded the `/imports` corpus path as
`archive_url` — a handle into the hand-managed SFTP dump the pipeline must
never treat as its archive (end-state goal #2: hash-addressed archive under
our control; docker-compose.yml mounts `/imports` read-only for exactly this
reason). Measured 2026-08-24: 388 documents (55 staged, 137 production, 192
filed, 4 superseded). Today's backfill archives via `store.put()` and is not
affected; this repairs only rows persisted before that fix.

Per document: re-read the original file, verify its sha256 equals the stored
`content_hash` (a mismatch is reported and skipped, never re-pointed), put the
bytes through the same StorageAdapter and `archive_path()` layout FETCH uses,
then re-point `document.archive_url` and the document's matching
`evidence.archive_url` rows at the new handle, with one `audit_log` entry per
document. Writing the registry outside GATE is an invariant-1 exception,
ruled by Denis 2026-08-24: an ops script, to be run immediately —
`decided_by` records it.

Dry run by default, like `repair-ref-list`:

    python -m app.cli repair-archive-urls            # report only
    python -m app.cli repair-archive-urls --apply
"""

from __future__ import annotations

import hashlib
import pathlib

from psycopg.types.json import Json

from app.handlers.archiving import archive_path

DECIDED_BY = "ops:repair-archive-urls"
EVENT = "archive-url-repair"


def default_resolver(archive_url: str, content_hash: str = "",
                     store_root: str | pathlib.Path | None = None) -> pathlib.Path | None:
    """Container-path resolver for the two handle shapes this repair heals.

    `/imports/...` -- backfill's corpus path, mounted read-only in the worker
    image; the file is where the handle says.

    `http(s)://...` -- the C3 clobber (doc 843, [c3-validate-payload-clobbers-
    archive-url]): GATE refreshed `archive_url` to the SOURCE url while the
    fetched copy sat in the store all along, named `{content_hash[:12]}__*` by
    FETCH's layout. Needs `store_root` to search under; found only when the
    hash-prefix glob matches EXACTLY one file (zero is a missing copy, two is
    a layout no repair should guess about), and the sha256 gate below applies
    to it the same as to a corpus file.

    Anything else is not a shape this repair understands."""
    if archive_url.startswith("/imports/"):
        p = pathlib.Path(archive_url)
        return p if p.is_file() else None
    if (archive_url.startswith(("http://", "https://"))
            and store_root and content_hash):
        matches = list(pathlib.Path(store_root).glob(f"**/{content_hash[:12]}__*"))
        return matches[0] if len(matches) == 1 else None
    return None


def _rows(conn, prefix: str) -> list[dict]:
    """Every document whose handle is not under the archive base, with the
    manufacturer its latest evidence names (cosmetic archive dir only —
    the archive is hash-addressed, the dir is for humans)."""
    return conn.execute(
        """
        SELECT d.doc_id, d.content_hash, d.archive_url, d.status,
               COALESCE((
                   SELECT e.value FROM evidence e
                   WHERE e.doc_id = d.doc_id AND e.field = 'manufacturer'
                   ORDER BY e.extract_rev DESC LIMIT 1
               ), 'unknown') AS manufacturer
        FROM document d
        WHERE d.archive_url NOT LIKE %s
        ORDER BY d.doc_id
        """,
        (prefix + "%",),
    ).fetchall()


def plan(conn, prefix: str = "/archive/", resolver=default_resolver):
    """`(repairs, skipped)`. A repair carries the verified local file and the
    relative archive path it will be stored under; a skip carries its reason.
    Re-runnable: applied rows fall under `prefix` and stop being selected."""
    repairs: list[dict] = []
    skipped: list[dict] = []
    for row in _rows(conn, prefix):
        local = resolver(row["archive_url"], row["content_hash"])
        if local is None:
            skipped.append({**row, "reason": "file missing or unrecognized handle shape"})
            continue
        digest = hashlib.sha256(local.read_bytes()).hexdigest()
        if digest != row["content_hash"]:
            skipped.append({**row, "reason": f"sha256 mismatch ({digest[:12]}...)"})
            continue
        repairs.append({
            **row,
            "local": local,
            "path_rel": archive_path(
                row["manufacturer"], row["content_hash"],
                row["archive_url"], "application/pdf"),
        })
    return repairs, skipped


def apply(conn, store, repairs: list[dict]) -> dict:
    """Archive each verified file and re-point the registry rows. The caller
    owns the transaction. Counted, never silent."""
    docs = evidence_rows = 0
    for r in repairs:
        new_url = store.put(r["local"].read_bytes(), r["path_rel"])
        conn.execute(
            "UPDATE document SET archive_url=%s WHERE doc_id=%s",
            (new_url, r["doc_id"]))
        ev = conn.execute(
            "UPDATE evidence SET archive_url=%s WHERE doc_id=%s AND archive_url=%s "
            "RETURNING 1",
            (new_url, r["doc_id"], r["archive_url"])).fetchall()
        conn.execute(
            "INSERT INTO audit_log (event, doc_id, decided_by, via_job, job_snapshot, detail) "
            "VALUES (%s, %s, %s, NULL, %s, %s)",
            (EVENT, r["doc_id"], DECIDED_BY,
             Json({"tool": "repair-archive-urls", "ruling": "Denis 2026-08-24",
                   "old": r["archive_url"], "new": new_url,
                   "content_hash": r["content_hash"]}),
             Json({"evidence_rows_repointed": len(ev), "status": r["status"]})))
        docs += 1
        evidence_rows += len(ev)
    return {"documents": docs, "evidence_rows": evidence_rows}


def render(repairs: list[dict], skipped: list[dict]) -> list[str]:
    """Report lines. Skips are printed, never silent (CLAUDE.md)."""
    out = []
    for r in repairs:
        out.append(f"  {r['doc_id']:>5}  {r['status']:<10} "
                   f"{r['archive_url'][:64]} -> /{r['path_rel'][:56]}")
    for s in skipped:
        out.append(f"  SKIPPED doc {s['doc_id']} ({s['status']}): {s['reason']}  "
                   f"{s['archive_url'][:56]}")
    out.append(f"\n{len(repairs)} to re-home, {len(skipped)} skipped")
    return out
