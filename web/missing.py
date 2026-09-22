"""Missing documents: the office's list of items the system searched for and
found nothing (office UI redesign spec § 5).

One kind of manual task only, `discovery-dead-end`, which the DISCOVER ladder
writes when every rung came back empty (`app/handlers/discover.py`,
`_push_manual`). The other two kinds live on the pages that resolve them:
`gate-manual` on Review, `dead-job-followup` on Failed. `/manual` still lists
all three for operators.

Read-only, like `web/failures.py`: every query here is a SELECT. The page's one
write, "Search again", is the existing `POST /manual/{id}/resolve`, and the
page never closes a task itself: a search that finds something closes it in
DISCOVER (`_resolve_dead_end_task`), an upload closes it in `upload.ingest`.

The payload is `{label, manufacturer, sources_tried, prefilled_search_links}`,
read off three dev rows on 2026-09-11 (tasks 215, 220, 455) and matching what
`_push_manual` writes. Nothing here assumes a key is present.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from urllib.parse import urlencode, urlsplit

from app import playbooks
from web import words

log = logging.getLogger("dentalia.web")

#: Spec § 1 rule 6: a queue shows 20 to a page.
PAGE_SIZE = 20
#: Spec § 5: chips for the six manufacturers with the most tasks.
CHIP_COUNT = 6
#: Spec § 4: the Today list names the top three.
SUMMARY_TOP = 3

# The manufacturer a task is about, as ONE expression that the chips, the
# filter, the card and `missing_summary` all read, so none of them can count a
# task under a different name. The item group's canonical name first, since
# spec § 5 takes the card's manufacturer from the group; then the payload's
# copy, which DISCOVER took from that same column when it wrote the task (the
# two agree on all 240 open tasks on dev, 2026-09-11) and which is all a task
# whose group has gone still carries.
_MANUFACTURER = "COALESCE(g.canonical_manufacturer, mt.payload->>'manufacturer')"

# Open dead ends, each joined to its group. `item_group.group_id` is the key,
# so the join never multiplies a task.
_FROM = (
    "FROM manual_task mt "
    "LEFT JOIN item_group g ON g.group_id = mt.group_id "
    "WHERE mt.kind = 'discovery-dead-end' AND mt.status = 'open'"
)

# Where a card says the system looked. NOT `sources_tried`: the ladder appends
# a rung to it before the rung runs, so it lists rungs that skipped (search
# not configured) and rungs that had nothing to look at (no playbook, no Basic
# UDI-DI). The card reads the group's last run in `discovery_log` instead and
# names a rung only when its row proves it looked somewhere:
#   * `search`: any hit or miss. A miss is written only after the search
#     adapter answered (an infra error fails the job instead), and the rung
#     writes `skipped` when search is not configured.
#   * `playbook`: a crawl row that read at least one page of the
#     manufacturer's library (`pages_fetched` >= 1), or a hit. A bare miss is
#     a manufacturer with no playbook, or one whose pages are for people only;
#     a robots refusal is `skipped`; a first page that did not load reads 0.
# Rungs that are never named, because the log cannot show they looked:
#   * `recency` writes no row at all when it finds nothing;
#   * `known_url` and `eudamed` write the same bare miss whether or not there
#     was an earlier address, or a Basic UDI-DI, to look up (when this was
#     written, none of the 240 open dead ends on dev had a Basic UDI-DI, so
#     every card naming EUDAMED was naming a lookup that never happened);
#   * `email` on a dead end was switched off or had nobody to write to (with a
#     contact the ladder ends on `email`, never here), `manual` is this very
#     list, and `vendor` is folded into search and always skipped.
# A rung added later passes through under its stored name once it has run.
SOURCE_WORDS: dict[str, str] = {
    "playbook": "the manufacturer's known pages",
    "search": "the web",
}
NEVER_NAMED = frozenset({"recency", "known_url", "eudamed", "email", "manual", "vendor"})

# The hosts `_prefilled_search_links` builds its web searches on. Classifying
# by host rather than by position: the payload is the playbook's portal pages
# FIRST and the searches after, and the number of portals varies per
# manufacturer.
_SEARCH_HOSTS = frozenset({
    "www.google.com", "google.com", "search.brave.com",
    "www.bing.com", "bing.com", "duckduckgo.com",
})

_NO_NAME = "No name recorded"

# One vocabulary (spec § 9, P7a): "3 Sep 2026" is defined once, in
# `web/words.py`, and this module reads it from there. It had its own copy,
# which is how two screens come to print a date two ways.
day_text = words.day_text


def _join(parts: list[str]) -> str:
    # `parts`, not `words`: this module imports `web.words`, and a parameter of
    # that name shadows it for the length of the function.
    if len(parts) < 2:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _looked(source: str, outcome: str, detail) -> bool:
    """Whether one `discovery_log` row proves its rung looked somewhere."""
    if source in NEVER_NAMED or outcome not in ("hit", "miss"):
        return False
    if source == "playbook":
        pages = detail.get("pages_fetched") if isinstance(detail, dict) else None
        return outcome == "hit" or (isinstance(pages, int) and pages >= 1)
    return True


def places_searched(rows) -> list[str]:
    """The places one run's `discovery_log` rows prove were searched, in
    ladder order, in the card's words. `rows` carry `source`, `outcome` and
    `detail`."""
    places: list[str] = []
    for row in rows:
        if _looked(row["source"], row["outcome"], row["detail"]):
            place = SOURCE_WORDS.get(row["source"], row["source"])
            if place not in places:
                places.append(place)
    return places


def searched_sentence(places: list[str], when: date | datetime) -> str:
    """'Searched the web on 3 Sep 2026. Nothing found.' When no place is
    proven, the sentence claims no search: 'Nothing found on 3 Sep 2026.'"""
    if not places:
        return f"Nothing found on {day_text(when)}."
    return f"Searched {_join(places)} on {day_text(when)}. Nothing found."


def items_phrase(refs: list[str], n: int) -> str:
    """The item numbers a task is about (spec § 5): 'item X' for one, the list
    for up to three, 'N items: a, b, c, +k more' beyond that. `refs` holds the
    first three in order, `n` the full count."""
    if n <= 0:
        return ""
    if n == 1:
        return f"item {refs[0]}"
    if n <= 3:
        return "items " + ", ".join(refs[:n])
    return f"{n:,} items: {', '.join(refs[:3])}, +{n - 3:,} more"


def _web_link(link) -> str | None:
    """`link` if it is an absolute http(s) address, else None. These become
    hrefs, so nothing else gets through."""
    if not isinstance(link, str):
        return None
    parts = urlsplit(link)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return link


def split_links(links) -> tuple[str | None, str | None]:
    """`prefilled_search_links` as the card's two link buttons: the
    manufacturer's own download page and a web search, the first of each."""
    downloads = web = None
    for link in links if isinstance(links, list) else ():
        link = _web_link(link)
        if link is None:
            continue
        if urlsplit(link).hostname in _SEARCH_HOSTS:
            web = web or link
        else:
            downloads = downloads or link
    return downloads, web


def _load_playbooks() -> tuple:
    """Every playbook, or none. The fallback download page is a convenience,
    so a playbook source that cannot be read costs the button, not the page."""
    try:
        return playbooks.load_playbooks()
    except Exception:                      # noqa: BLE001 -- logged, page still renders
        log.warning("missing: playbooks unreadable, no download-page fallback",
                    exc_info=True)
        return ()


def _playbook_portal(manufacturer: str, loaded: tuple) -> str | None:
    """The playbook's first `portal` document source (spec § 5: "or the
    playbook's document sources"), for a task written before that page was
    authored. `direct` sources are fetch targets, not pages for a person,
    which is how `_prefilled_search_links` treats them too."""
    pb = playbooks.for_manufacturer(manufacturer, loaded)
    if pb is None:
        return None
    for source in pb.doc_sources:
        if source.kind == "portal" and _web_link(source.url):
            return source.url
    return None


def _manufacturer_counts(conn) -> list[tuple[str, int]]:
    """Open dead ends per manufacturer, most first, then by name."""
    rows = conn.execute(
        f"SELECT {_MANUFACTURER} AS name, count(*) AS n {_FROM} "
        f"AND {_MANUFACTURER} IS NOT NULL "
        "GROUP BY 1 ORDER BY n DESC, name"
    ).fetchall()
    return [(r["name"], r["n"]) for r in rows]


def missing_summary(conn) -> dict:
    """What the Today page and the menu count say about this list (spec § 4).

    `n`: open `discovery-dead-end` tasks. `top`: the three manufacturers with
    the most, as `(name, count)`, ties by name. `oldest`: the date the oldest
    one was written, None when there are none. Same predicate as the page, so
    the count on Today is the count here."""
    row = conn.execute(
        f"SELECT count(*) AS n, min(mt.created_at) AS oldest {_FROM}"
    ).fetchone()
    return {
        "n": row["n"],
        "top": _manufacturer_counts(conn)[:SUMMARY_TOP],
        "oldest": row["oldest"].date() if row["oldest"] is not None else None,
    }


def _payload(row) -> dict:
    return row["payload"] if isinstance(row["payload"], dict) else {}


def _decorate(conn, rows) -> list[dict]:
    """One card per task, in the words spec § 5 gives."""
    group_ids = sorted({r["group_id"] for r in rows if r["group_id"] is not None})
    members: dict[int, dict] = {}
    last_search: dict[int, datetime] = {}
    last_run: dict[int, list] = {}
    if group_ids:
        members = {r["group_id"]: r for r in conn.execute(
            "SELECT group_id, count(*) AS n, "
            "       (array_agg(item_ref ORDER BY item_ref))[1:3] AS refs "
            "FROM item_group_member WHERE group_id = ANY(%s) GROUP BY group_id",
            (group_ids,),
        ).fetchall()}
        # The group's LAST run that ended here. "Search again" that comes back
        # empty leaves the SAME task open (`_push_manual` writes no second one)
        # and logs a whole new run, so the card's date and places follow it;
        # the task's own `created_at` would stay on the first search forever.
        # One run is one transaction, so its rows share `at` with its `manual`
        # row; ordering by id keeps the ladder's order.
        for r in conn.execute(
            "SELECT d.group_id, d.source, d.outcome, d.detail, d.at "
            "FROM discovery_log d "
            "JOIN (SELECT group_id, max(at) AS at FROM discovery_log "
            "      WHERE source = 'manual' AND group_id = ANY(%s) "
            "      GROUP BY group_id) last "
            "  ON last.group_id = d.group_id AND last.at = d.at "
            "ORDER BY d.group_id, d.id",
            (group_ids,),
        ).fetchall():
            last_search[r["group_id"]] = r["at"]
            last_run.setdefault(r["group_id"], []).append(r)

    loaded = None
    cards = []
    for r in rows:
        payload = _payload(r)
        downloads, web = split_links(payload.get("prefilled_search_links"))
        if downloads is None and r["manufacturer"]:
            if loaded is None:
                loaded = _load_playbooks()
            downloads = _playbook_portal(r["manufacturer"], loaded)
        member = members.get(r["group_id"])
        searched_at = max(filter(None, (last_search.get(r["group_id"]), r["created_at"])))
        upload = {"manual_task_id": r["id"]}
        if r["group_id"] is not None:
            upload = {"group_id": r["group_id"], **upload}
        cards.append({
            "id": r["id"],
            "group_id": r["group_id"],
            "product": payload.get("label") or r["group_label"] or _NO_NAME,
            "manufacturer": r["manufacturer"],
            # Not `items`: in Jinja `task.items` is the dict's own method.
            "item_numbers": items_phrase(member["refs"], member["n"]) if member else "",
            "waiting_since": day_text(r["created_at"]),
            "searched": searched_sentence(
                places_searched(last_run.get(r["group_id"], ())), searched_at),
            "downloads": downloads,
            "web_search": web,
            "upload_query": urlencode(upload),
        })
    return cards


def missing_view(conn, *, manufacturer: str = "", page: int = 0) -> dict:
    """Everything `/missing` draws: one page of cards, oldest first, and the
    manufacturer chips. `page` is clamped into range, so a stale Next link
    lands on the last page rather than on an empty one."""
    counts = _manufacturer_counts(conn)
    n_all = conn.execute(f"SELECT count(*) AS n {_FROM}").fetchone()["n"]

    where = f" AND {_MANUFACTURER} = %(manufacturer)s" if manufacturer else ""
    params = {"manufacturer": manufacturer}
    total = conn.execute(f"SELECT count(*) AS n {_FROM}{where}", params).fetchone()["n"]
    page = min(max(page, 0), max((total - 1) // PAGE_SIZE, 0))
    rows = conn.execute(
        f"SELECT mt.id, mt.group_id, mt.created_at, mt.payload, "
        f"       {_MANUFACTURER} AS manufacturer, g.label AS group_label "
        f"{_FROM}{where} "
        "ORDER BY mt.created_at, mt.id LIMIT %(limit)s OFFSET %(offset)s",
        {**params, "limit": PAGE_SIZE, "offset": page * PAGE_SIZE},
    ).fetchall()

    # Past the top six, alphabetical: 80-odd names are found by reading, not
    # by rank.
    more = sorted(counts[CHIP_COUNT:], key=lambda c: c[0].casefold())
    return {
        "tasks": _decorate(conn, rows),
        "total": total,
        "n_all": n_all,
        "n_manufacturers": len(counts),
        "chips": counts[:CHIP_COUNT],
        "more_chips": more,
        "more_open": any(name == manufacturer for name, _ in more),
        "manufacturer": manufacturer,
        "page": page,
        "pager_params": urlencode({"manufacturer": manufacturer}) if manufacturer else "",
    }


def _as_int(value: str) -> int | None:
    try:
        return int(value.strip())
    except (AttributeError, ValueError):
        return None


def upload_context(conn, *, manual_task_id: str = "", group_id: str = "") -> dict:
    """What the Upload page shows and posts when opened for a task (spec § 5).

    `subject`: 'Uploading a document for ⟨product⟩ (⟨manufacturer⟩, item
    ⟨ref⟩)', or None when nothing resolves and the page is the plain upload
    form it always was. `group_id`: the value of the hidden `target_group_id`.

    When `manual_task_id` resolves to a task, the TASK decides both: the line
    names its item and the form posts its group, whatever `group_id` the URL
    carried. The field is no longer on screen for anyone to correct, so a
    stale or hand-edited URL must not send the document to another item.
    Without a task (or for a task with no group) the URL's group is used."""
    task_id, gid = _as_int(manual_task_id), _as_int(group_id)
    posted = group_id
    row = None
    if task_id is not None:
        row = conn.execute(
            f"SELECT mt.group_id, mt.payload, g.label AS group_label, "
            f"       {_MANUFACTURER} AS manufacturer "
            "FROM manual_task mt LEFT JOIN item_group g ON g.group_id = mt.group_id "
            "WHERE mt.id = %s",
            (task_id,),
        ).fetchone()
    if row is not None and row["group_id"] is not None:
        gid = row["group_id"]
        posted = str(gid)
    if row is None and gid is not None:
        row = conn.execute(
            "SELECT group_id, '{}'::jsonb AS payload, label AS group_label, "
            "       canonical_manufacturer AS manufacturer "
            "FROM item_group WHERE group_id = %s",
            (gid,),
        ).fetchone()
    context = {"subject": None, "group_id": posted}
    if row is None:
        return context

    product = _payload(row).get("label") or row["group_label"]
    if not product and not row["manufacturer"]:
        return context
    member = None
    if gid is not None:
        member = conn.execute(
            "SELECT count(*) AS n, (array_agg(item_ref ORDER BY item_ref))[1:3] AS refs "
            "FROM item_group_member WHERE group_id = %s",
            (gid,),
        ).fetchone()
    items = items_phrase(member["refs"] or [], member["n"]) if member else ""
    detail = ", ".join(part for part in (row["manufacturer"], items) if part)
    context["subject"] = (f"Uploading a document for {product or _NO_NAME}"
                          + (f" ({detail})" if detail else ""))
    return context


def search_again_receipt(queued: bool) -> str:
    """The receipt for "Search again" (spec § 5, and § 9's "Already in
    progress."). The work is queued, not done, so it says what WILL happen."""
    if queued:
        # DISCOVER closes the task on ANY rung that finds something to try (an
        # address to fetch, a stored copy to re-check, a contact to write to),
        # before anyone knows whether it is the document; see
        # `_resolve_dead_end_task`. So the receipt promises no more than that.
        return ("The system will search again for this item. If it finds something "
                "to try, the item leaves this list before the document is checked. "
                "If it finds nothing, the item stays here.")
    return "Already in progress. This item is already being searched for."

