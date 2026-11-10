"""Recent uploads on /upload: what became of each file handed in.

Read-only. Worked out on every view from the job chain and the registry and
stored nowhere, so a later approval, rejection or re-read shows by itself. The
queue is how a file moves; `document` is what came of it, so a document, once
there is one, decides the row. Job rows are only the trail for a file that has
no document (yet).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from web.words import day_text, word

#: Above the 50 files one batch can hold, so a batch never pushes itself off.
RECENT_LIMIT = 100
#: A file with no trail at all (its job was dropped as a duplicate of one
#: already active, or removed by a repair script) stops reading as in progress.
NO_RESULT_AFTER = timedelta(hours=24)

_LOCAL = ZoneInfo("Europe/Ljubljana")
_ACTIVE = ("pending", "running", "failed")     # `failed` is still retrying
_STAGES = ["extract.doc", "validate.doc", "gate.candidate"]
#: `doc_class` values that are not compliance documents. `tiers.NOT_A_DOCUMENT`
#: spelled out: this module runs in the slim web image, which has no extract code.
_NOT_COMPLIANCE = ("not-a-compliance-document", "business-doc", "msds")

# `tone` is the existing badge colour (`.badge-<tone>`): a document wears its own
# status colour, as on every other screen, and red means no document came out.
_BEING_READ = {"state": word("in flight"), "tone": "running", "open": True}
_FAILED = {"state": word("dead"), "tone": "dead", "detail": "Ask a developer", "open": False}
_NOT_A_DOC = {"state": "Nothing filed", "tone": "rejected",
              "detail": "Not a compliance document", "open": False}
_UNREADABLE = {"state": "Nothing filed", "tone": "rejected",
               "detail": "Could not read enough", "open": False}
_NO_RESULT = {"state": "No result", "tone": "dead", "detail": "Ask a developer", "open": False}
_SEEN_BEFORE = {"state": "Nothing filed", "tone": "rejected",
                "detail": "Read before, no document came of it", "open": False}

_RECENT = """
    SELECT id, status::text AS status, payload, result, created_at, dedupe_key
      FROM job
     WHERE type = 'upload.ingest' AND payload ? 'filename'
     ORDER BY id DESC
     LIMIT %s
"""

_CHAIN = """
    WITH RECURSIVE chain AS (
        SELECT id AS root, id, type::text AS type, status::text AS status, result
          FROM job WHERE id = ANY(%s)
        UNION ALL
        SELECT c.root, j.id, j.type::text, j.status::text, j.result
          FROM job j JOIN chain c ON j.caused_by = c.id
    )
    SELECT * FROM chain WHERE type = ANY(%s) ORDER BY root, id
"""

# A rerun of a dead job is a new row, same type and key, with no `caused_by`.
_RERUN = """
    SELECT DISTINCT ON (d.id) d.id AS dead_id, n.id AS rerun_id
      FROM job d JOIN job n ON n.dedupe_key = d.dedupe_key AND n.type = d.type AND n.id > d.id
     WHERE d.id = ANY(%s)
     ORDER BY d.id, n.id DESC
"""

_STAGED_LINKS = """
    SELECT DISTINCT l.doc_id, m.group_id
      FROM item_document l JOIN item_group_member m USING (item_ref)
     WHERE l.status = 'staged' AND l.doc_id = ANY(%s) AND m.group_id = ANY(%s)
"""


def _chains(conn, roots: list[int]) -> dict[int, list[dict]]:
    """The later stages of each root, following reruns of anything that died."""
    chains: dict[int, list[dict]] = {root: [] for root in roots}
    pending = {root: root for root in roots}  # job to walk -> the upload it belongs to
    checked: set[int] = set()
    while pending:
        rows = conn.execute(_CHAIN, (list(pending), _STAGES)).fetchall()
        for row in rows:
            chains[pending[row["root"]]].append(dict(row))
        dead = {j["id"]: root for root, jobs in chains.items() for j in jobs
                if j["status"] == "dead" and j["id"] not in checked}
        checked |= set(dead)
        pending = {}
        if dead:
            reruns = conn.execute(_RERUN, (list(dead),)).fetchall()
            pending = {r["rerun_id"]: dead[r["dead_id"]] for r in reruns}
            replaced = {r["dead_id"] for r in reruns}
            for jobs in chains.values():
                for j in jobs:
                    j["replaced"] = j.get("replaced") or j["id"] in replaced
    return chains


def _document_state(doc: dict, link_waiting: bool, active: bool) -> dict:
    href, label = f"/documents/{doc['doc_id']}", "Open document"
    state, tone = word(doc["status"]), doc["status"]
    if doc["status"] == "staged":
        href, label = f"/staging?doc={doc['doc_id']}", "Open in Review"
    elif doc["status"] == "production" and link_waiting:
        # Published already, but what links it to THIS item is only proposed.
        state, tone, href, label = (word("staged"), "staged", "/staging#staged-links",
                                    "Open in Review")
    return {"state": state, "tone": tone, "href": href, "link_text": label, "open": active}


def _no_document_state(upload: dict, jobs: list[dict], now: datetime) -> dict:
    live = [j for j in jobs if not j.get("replaced")]
    if any(j["status"] in _ACTIVE for j in live):
        return _BEING_READ
    if any(j["status"] == "dead" for j in live):
        return _FAILED
    extract = next((j for j in reversed(live) if j["type"] == "extract.doc"), None)
    if extract and (extract["result"] or {}).get("doc_class") in _NOT_COMPLIANCE:
        return _NOT_A_DOC
    if live:
        return _UNREADABLE
    if (upload["result"] or {}).get("outcome") == "duplicate":
        return _SEEN_BEFORE
    return _NO_RESULT if now - upload["created_at"] > NO_RESULT_AFTER else _BEING_READ


def _outcome(upload, doc, link_waiting, jobs, now) -> dict:
    if upload["status"] in _ACTIVE:
        return _BEING_READ
    if upload["status"] == "dead":
        return _FAILED
    if doc is not None:
        active = any(j["status"] in _ACTIVE and not j.get("replaced") for j in jobs)
        return _document_state(doc, link_waiting, active)
    return _no_document_state(upload, jobs, now)


def _when(created_at: datetime) -> str:
    local = created_at.astimezone(_LOCAL)
    return f"{day_text(local)} {local:%H:%M}"


def recent_uploads(conn, limit: int = RECENT_LIMIT) -> list[dict]:
    """Newest first. A rerun of an upload job is the same upload, so one row."""
    seen: set[str] = set()
    uploads = []
    for row in conn.execute(_RECENT, (limit * 2,)).fetchall():
        if row["dedupe_key"] not in seen:
            seen.add(row["dedupe_key"])
            uploads.append(row)
    uploads = uploads[:limit]
    if not uploads:
        return []

    hashes = [h for u in uploads if (h := (u["result"] or {}).get("content_hash"))]
    docs = {d["content_hash"]: d for d in conn.execute(
        "SELECT doc_id, status::text AS status, content_hash FROM document "
        "WHERE content_hash = ANY(%s)", (hashes,)).fetchall()}
    groups = [u["payload"]["group_id"] for u in uploads if u["payload"].get("group_id")]
    labels = {g["group_id"]: g["label"] for g in conn.execute(
        "SELECT group_id, label FROM item_group WHERE group_id = ANY(%s)", (groups,)).fetchall()}
    waiting = {(r["doc_id"], r["group_id"]) for r in conn.execute(
        _STAGED_LINKS, ([d["doc_id"] for d in docs.values()], groups)).fetchall()}
    chains = _chains(conn, [u["id"] for u in uploads])

    now = datetime.now(timezone.utc)
    rows = []
    for u in uploads:
        doc = docs.get((u["result"] or {}).get("content_hash"))
        group_id = u["payload"].get("group_id")
        link_waiting = doc is not None and (doc["doc_id"], group_id) in waiting
        rows.append({
            "filename": u["payload"]["filename"],
            "when": _when(u["created_at"]),
            "item": labels.get(group_id) or "",
            **_outcome(u, doc, link_waiting, chains[u["id"]], now),
        })
    return rows
