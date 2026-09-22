"""The Failed split (office UI redesign spec § 4): what a dead job means to the
person looking at it, and which dead jobs still need anyone.

`/dead` used to be one list of error groups written for a developer. The office
reads it as three questions instead, in this order:

  * `timeout`       -- a supplier website took too long. Usually temporary;
                       trying the same work again often works.
  * `dead-address`  -- a document address no longer works (HTTP 404 / 410, or a
                       host name that does not resolve). The same address will
                       fail again; searching again looks for the new one.
  * `developer`     -- everything else. Retrying will not help until the cause
                       is fixed.

**The patterns** come from how the worker writes `last_error` --
`f"{type(exc).__name__}: {exc}"` (app/workers/runner.py), so the exception
class leads -- and from the 87 dead jobs on the dev database on 2026-09-11:
`fetch.url` raises `FetchError: unexpected status 404 for <url>` for any
non-200 (app/handlers/fetch.py), httpx raises `ConnectTimeout` / `ReadTimeout`
/ `ConnectError: [Errno -2] Name or service not known`, and the Playwright tier
raises `TimeoutError: Page.goto: Timeout 30000ms exceeded.` The table in
tests/test_web_failed_split.py is drawn from those rows.

**Retried work stops counting.** A dead row is terminal -- every `UPDATE job`
lives in app/queue.py and none selects a dead row -- so "was it dealt with" is
read from what came after it:

  * newer work for the same job: a job with the same dedupe key and a higher
    id, from any button or from the pipeline re-finding the same URL. For
    `fetch.url` it must also carry the same `group_id`: FETCH keys on the URL
    alone while its payload names the group, so another group's fetch of the
    same address is not work for this one (live on dev 2026-09-11, jobs 36210
    and 37434 share a key across groups 2067 and 2100). Once any such newer
    job has finished it speaks for this row -- a newer dead row is counted in
    its own right, a done one means it worked -- and until then this row shows
    as "trying again";
  * for a dead address, a `discover.group` for its item group created after the
    job died. While it is active the job shows as "searching again".

Everything here is a read. The web role enqueues and nothing else; the worker
closes the `dead-job-followup` alarms when later work succeeds (app/queue.py).
Stdlib only, so the slim web image imports it (tests/test_web_import_closure.py).
"""

from __future__ import annotations

import re
from collections import Counter
from urllib.parse import urlsplit

TIMEOUT = "timeout"
DEAD_ADDRESS = "dead-address"
DEVELOPER = "developer"
CLASSES = (TIMEOUT, DEAD_ADDRESS, DEVELOPER)

PAGE_GONE = "page-gone"
HOST_NOT_FOUND = "host-not-found"
#: The dead-address sub-groups, in the order the page shows them.
KIND_LABELS = {PAGE_GONE: "Page no longer exists", HOST_NOT_FOUND: "Website not found"}

#: The job types that contact supplier websites: FETCH, DISCOVER's crawl rung,
#: and the playbook probe. A timeout anywhere else (the LLM API behind
#: `extract.doc`, EUDAMED behind `eudamed.*`) is not a supplier being slow, and
#: nothing the office can retry its way out of.
SUPPLIER_WEB_TYPES = frozenset({"fetch.url", "discover.group", "playbook.reonboard"})

_TIMEOUT = re.compile(
    # The exception class leads: ConnectTimeout, ReadTimeout, WriteTimeout,
    # PoolTimeout, TimeoutError (Python's and Playwright's), TimeoutException,
    # optionally module-qualified (`httpx.ConnectTimeout`).
    r"^(?:[\w.]+\.)?\w*[Tt]imeout\w*:"
    # Chromium, when the Playwright tier times out below the page API.
    r"|net::ERR_(?:CONNECTION_)?TIMED_OUT"
    # EAI_AGAIN: the resolver did not answer in time. Unlike [Errno -2] the
    # name may well exist, so this is "try again", not "the address is dead".
    r"|\[Errno -3\] Temporary failure in name resolution"
)
_PAGE_GONE = re.compile(
    r"unexpected status (?:404|410)\b"
    # `bot-wall persists ... (httpx 403, playwright 404)`: the browser tier got
    # past the wall and the page was not there.
    r"|playwright (?:404|410)\)"
)
_HOST_NOT_FOUND = re.compile(
    r"\[Errno -2\] Name or service not known"          # glibc EAI_NONAME
    r"|\[Errno -5\] No address associated with hostname"  # glibc EAI_NODATA
    r"|nodename nor servname provided"                  # BSD / macOS
    r"|getaddrinfo failed"                              # Windows
    r"|net::ERR_NAME_NOT_RESOLVED"                      # Chromium
)

# One pass over `job` for the dead rows and what came after each. Aggregate
# joins rather than correlated EXISTS: there is no index leading with
# `dedupe_key` outside the active-only unique index, and the planner turned the
# correlated form into one sequential scan of `job` PER DEAD ROW for the
# discover side (80 ms on dev, 2026-09-11). Hash joins over `dead` and a
# materialised `disc` read the table twice in all: 16 ms.
_SPLIT_SQL = """
WITH dead AS (
    SELECT id, type::text AS type, payload, last_error, dedupe_key,
           attempts, max_attempts, created_at, finished_at,
           coalesce(finished_at, created_at) AS died,
           payload->>'group_id' AS gid
    FROM job
    WHERE status = 'dead'
), newer AS (
    SELECT d.id, bool_or(n.status IN ('done', 'dead')) AS finished
    FROM dead d
    JOIN job n ON n.dedupe_key = d.dedupe_key AND n.id > d.id
              -- fetch.url keys on the URL alone; another group's fetch of the
              -- same address is not newer work for this job
              AND (d.type <> 'fetch.url'
                   OR n.payload->>'group_id' IS NOT DISTINCT FROM d.gid)
    GROUP BY d.id
), disc AS MATERIALIZED (
    SELECT payload->>'group_id' AS gid, created_at, status
    FROM job
    WHERE type = 'discover.group'
), search AS (
    SELECT d.id, bool_or(s.status IN ('pending', 'running', 'failed')) AS active
    FROM dead d
    JOIN disc s ON s.gid = d.gid AND s.created_at > d.died
    WHERE d.type = 'fetch.url'
    GROUP BY d.id
)
SELECT dead.*,
       newer.id IS NOT NULL             AS rerun,
       coalesce(newer.finished, false)  AS rerun_finished,
       search.id IS NOT NULL            AS searched,
       coalesce(search.active, false)   AS search_active
FROM dead
LEFT JOIN newer USING (id)
LEFT JOIN search USING (id)
ORDER BY dead.id
"""


def dead_address_kind(last_error: str | None) -> str | None:
    """`page-gone` or `host-not-found` for a dead address's error, else None."""
    error = last_error or ""
    if _PAGE_GONE.search(error):
        return PAGE_GONE
    if _HOST_NOT_FOUND.search(error):
        return HOST_NOT_FOUND
    return None


def classify_failure(job_type: str, last_error: str | None) -> str:
    """`timeout`, `dead-address` or `developer` (spec § 4).

    A timeout counts only on a type that contacts supplier websites, and a dead
    address only on `fetch.url` -- a 404 anywhere else is a bug, not a moved
    document. An empty error is a developer's: nothing says what happened.
    """
    error = (last_error or "").strip()
    if not error:
        return DEVELOPER
    if job_type in SUPPLIER_WEB_TYPES and _TIMEOUT.search(error):
        return TIMEOUT
    if job_type == "fetch.url" and dead_address_kind(error):
        return DEAD_ADDRESS
    return DEVELOPER


def _domain(payload) -> str | None:
    if not isinstance(payload, dict):
        return None
    if payload.get("domain"):
        return str(payload["domain"])
    if payload.get("url"):
        return urlsplit(str(payload["url"])).hostname
    return None


def _domains(rows) -> list[str]:
    """Distinct domains, the most frequent first, then alphabetical."""
    counts = Counter(r["domain"] for r in rows if r["domain"])
    return [d for d, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def _state(row) -> str:
    """`counted`, `trying-again`, `searching-again`, or `settled`.

    A newer job that has FINISHED settles this row whatever else is queued
    after it: a newer dead row is the one that counts (two dead rows and one
    retry must read as one "trying again", matching the receipt), and a done
    one means the work succeeded, so a later unrelated run is not a retry of
    this failure. With no finished newer job, the newer ones are all active.
    """
    if row["rerun"]:
        return "settled" if row["rerun_finished"] else "trying-again"
    if row["cls"] == DEAD_ADDRESS and row["searched"]:
        return "searching-again" if row["search_active"] else "settled"
    return "counted"


def failed_split(conn) -> dict:
    """The three sections, keyed by class.

    Every section carries `n` (the dead jobs still counted), `domains`, `jobs`
    (the counted rows, oldest first) and `trying_again`. The dead-address
    section also carries `kinds` (label, n and domains per error kind, in page
    order), `group_ids` (distinct, sorted), `no_group` (counted jobs with no
    item group, which a search cannot cover) and `searching_again`.

    `jobs` is what the POST routes act on, so a button always acts on exactly
    what the page counted, re-derived server-side at click time.
    """
    rows = conn.execute(_SPLIT_SQL).fetchall()
    for r in rows:
        r["cls"] = classify_failure(r["type"], r["last_error"])
        r["kind"] = dead_address_kind(r["last_error"]) if r["cls"] == DEAD_ADDRESS else None
        r["domain"] = _domain(r["payload"])
        r["state"] = _state(r)

    split: dict[str, dict] = {}
    for cls in CLASSES:
        mine = [r for r in rows if r["cls"] == cls]
        counted = [r for r in mine if r["state"] == "counted"]
        split[cls] = {
            "n": len(counted),
            "domains": _domains(counted),
            "jobs": counted,
            "trying_again": sum(r["state"] == "trying-again" for r in mine),
        }

    section = split[DEAD_ADDRESS]
    counted = section["jobs"]
    section["kinds"] = [
        {"kind": kind, "label": label, "n": len(of_kind), "domains": _domains(of_kind)}
        for kind, label in KIND_LABELS.items()
        if (of_kind := [r for r in counted if r["kind"] == kind])
    ]
    gids = {r["payload"].get("group_id") for r in counted if isinstance(r["payload"], dict)}
    gids.discard(None)
    # ints as `discover._emit_fetch` writes them; the key only keeps a stray
    # string id from crashing the sort
    section["group_ids"] = sorted(gids, key=lambda g: (isinstance(g, str), g))
    section["no_group"] = sum(
        1 for r in counted
        if not isinstance(r["payload"], dict) or r["payload"].get("group_id") is None
    )
    section["searching_again"] = sum(
        r["state"] == "searching-again" for r in rows if r["cls"] == DEAD_ADDRESS
    )
    return split


def failed_counts(conn) -> dict[str, int]:
    """The three counts only, for a page that shows one line per class."""
    split = failed_split(conn)
    return {cls: split[cls]["n"] for cls in CLASSES}


# One pass for the ONE class the office menu's health line needs. Narrower than
# `_SPLIT_SQL` on purpose: that one reads `job` about three times (the dead
# rows, the newer-work join, and a materialised scan of every
# `discover.group`), which was fine for a page somebody opens and wrong for a
# fragment every open tab re-fetches every 15 seconds on a table that only
# grows. Two narrowings, neither of which can change the answer:
#
#   * `type = ANY(SUPPLIER_WEB_TYPES)` -- `classify_failure` returns `timeout`
#     for no other type, so the rows dropped here could never have counted;
#   * no `disc` CTE -- `searching again` is a dead-address state
#     (`_state`), and a timeout is settled or not by newer work alone.
#
# Measured on dev 2026-09-14 (17.862 jobs, 94 of them dead, no index on
# `dedupe_key` outside the active-only unique one): both plans are hash joins
# and the wall-clock today is a wash -- 9.4ms against `failed_split`'s 10.1ms,
# min of 12, same answer. The win is the scan this one does NOT do. `EXPLAIN
# ANALYZE` counts three scans of `job` for the split and two for this, and the
# dropped one is the materialised read of every `discover.group` row ever
# enqueued, which is the fastest-growing table in the query. A fragment every
# open tab re-fetches every 15 seconds should not carry that.
_TIMEOUT_SQL = """
WITH dead AS (
    SELECT id, type::text AS type, last_error, dedupe_key,
           payload->>'group_id' AS gid
    FROM job
    WHERE status = 'dead' AND type::text = ANY(%s)
), newer AS (
    SELECT d.id
    FROM dead d
    JOIN job n ON n.dedupe_key = d.dedupe_key AND n.id > d.id
              -- fetch.url keys on the URL alone; another group's fetch of the
              -- same address is not newer work for this job
              AND (d.type <> 'fetch.url'
                   OR n.payload->>'group_id' IS NOT DISTINCT FROM d.gid)
    GROUP BY d.id
)
SELECT dead.type, dead.last_error
FROM dead
WHERE NOT EXISTS (SELECT 1 FROM newer WHERE newer.id = dead.id)
"""


def timeout_count(conn) -> int:
    """Website timeouts that still need someone -- `failed_counts(conn)["timeout"]`,
    read with one narrower query.

    The classification stays in Python, `classify_failure` and nothing else, so
    this and `/dead` can never disagree about what a timeout is; the SQL only
    decides which rows are worth asking about. `tests/test_web_failed_split.py`
    pins the two together on the same seeded data.
    """
    rows = conn.execute(_TIMEOUT_SQL, (sorted(SUPPLIER_WEB_TYPES),)).fetchall()
    return sum(classify_failure(r["type"], r["last_error"]) == TIMEOUT for r in rows)


# --------------------------------------------------------------------------- #
# Receipts in words (spec § 1, rule 5). The work is queued, not done, so a
# receipt never claims it succeeded.
# --------------------------------------------------------------------------- #

def _n(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def retry_receipt(section: dict, queued: int) -> str:
    """After "Try the N again": `section` is the timeout section the route
    acted on, `queued` how many of its jobs were actually enqueued.

    An enqueue comes back empty when an active job already holds the dedupe
    key. For `fetch.url` that can be another group's fetch of the same
    address, which does not take this row out of the count, so the receipt
    says the address is already queued rather than claiming a retry.
    """
    counted = section["n"]
    if not counted:
        if section["trying_again"]:
            return (f"Already in progress: {_n(section['trying_again'], 'website timeout is', 'website timeouts are')}"
                    " being tried again.")
        return "Nothing to try again: no website timeouts are waiting."
    if not queued:
        these = "this address is" if counted == 1 else f"these {counted} addresses are"
        return (f"Nothing new queued: {these} already in the queue. Anything still "
                "listed here once that finishes can be tried again.")
    text = f"Trying the {queued} again. Until each one finishes, this page lists it as trying again."
    if queued < counted:
        text += (f" {_n(counted - queued, 'other address is', 'other addresses are')} already"
                 " in the queue, and can be tried again once that finishes.")
    return text


def search_receipt(section: dict, started: int) -> str:
    """After "Search again for these N": `section` is the dead-address section
    the route acted on, `started` the searches it actually enqueued."""
    if not section["n"]:
        if section["searching_again"]:
            return (f"Already in progress: {_n(section['searching_again'], 'address is', 'addresses are')}"
                    " being searched for again.")
        return "Nothing to search again for: no document addresses are waiting."
    parts = []
    groups = len(section["group_ids"])
    if groups:
        covered = section["n"] - section["no_group"]
        already = groups - started
        if started:
            parts.append(f"Searching again for {_n(covered, 'address', 'addresses')}: "
                         f"{_n(started, 'search', 'searches')} queued.")
            if already:
                parts.append(f"{_n(already, 'search was', 'searches were')} already running.")
            parts.append("What they find goes through the usual checks.")
        else:
            parts.append(f"Nothing new queued: {_n(already, 'search is', 'searches are')} already"
                         f" running for {'this address' if covered == 1 else 'these addresses'}.")
    if section["no_group"]:
        k = section["no_group"]
        parts.append(f"{_n(k, 'address is', 'addresses are')} not linked to an item, so there "
                     f"is nothing to search for. {'It stays' if k == 1 else 'They stay'} on this list.")
    return " ".join(parts)
