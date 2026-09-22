"""What "this group has no document" means, in one place.

Three callers ask the same question and must not answer it differently: the
manufacturer page's button (`web.registry.uncovered_groups`), the coverage cron
(`scheduler._tick_coverage_scan`), and anything that reports a coverage figure.
A second definition drifting from the first is how a button comes to promise
work the cron has already done, or a KPI to disagree with the screen beside it.

**Production, not staged.** `staged` is the review queue, not coverage: a group
whose only link is capped at `fetch-context` has nothing a compliance officer
can rely on, and is exactly what discovery should be chasing.

**Ordered never-discovered first, then least recently discovered.** Every caller
caps its result, and a stable ordering under a cap means the next run re-does the
head of the list forever instead of walking the backlog. Measured 2026-09-02:
6.732 groups have no production document and 6.693 of them have never been
discovered at all, so at the shipped cap of 25 a day the ordering is the only
thing that makes the backlog finite.
"""

from __future__ import annotations

_UNCOVERED_SQL = """
SELECT g.group_id,
       (SELECT max(d.at) FROM discovery_log d WHERE d.group_id = g.group_id) AS last_seen
FROM item_group g
-- Cast, or Postgres cannot infer the type of a bare parameter compared only
-- against NULL: `could not determine data type of parameter $1`.
WHERE (%(manufacturer)s::text IS NULL
       OR g.canonical_manufacturer = %(manufacturer)s::text)
  AND NOT EXISTS (
        SELECT 1 FROM item_group_member m
        JOIN item_document i ON i.item_ref = m.item_ref
        WHERE m.group_id = g.group_id AND i.status = 'production')
ORDER BY last_seen ASC NULLS FIRST, g.group_id
"""


def uncovered_groups(conn, *, manufacturer: str | None = None, limit: int) -> dict:
    """`{"total": int, "group_ids": [...]}` — the real total, and the first
    `limit` ids to send through DISCOVER.

    The total is never capped: a button has to be able to say "809 groups, this
    press covers 25", and a cron has to report what it is leaving behind.

    `manufacturer=None` is the whole registry (the coverage cron); a name scopes
    it to one supplier (the manufacturer page's button).
    """
    rows = conn.execute(_UNCOVERED_SQL, {"manufacturer": manufacturer}).fetchall()
    return {"total": len(rows), "group_ids": [r["group_id"] for r in rows[:limit]]}


#: The three states a group can be in with respect to DISCOVER. Lifted out of
#: `web/app.py`'s `_DISCOVERY_STATES` on 2026-09-04 so the item page and the
#: `/discovery` list cannot drift: "never looked" and "looked and found nothing"
#: are different facts about our own diligence, and a screen that renders them
#: identically is telling a compliance officer we did not do work we did.
NEVER = "never"
EMPTY = "empty"
HANDOFF = "handoff"
FOUND = "found"

#: `HANDOFF` is `EMPTY` plus the one fact the office needs next: we stopped
#: looking and opened a task, so the work now sits on a screen a person reads.
#: It is named after the `discovery_log` outcome the manual rung writes rather
#: than after the screen, because the screen has been renamed once already --
#: the office's Manual queue became Missing documents on 2026-09-14 -- and a
#: state named after a screen would have had to be migrated with it.
STATE_LABELS = {
    NEVER: "Never searched",
    EMPTY: "Searched, found nothing",
    HANDOFF: "Searched, nothing found, sent to Missing documents",
    FOUND: "Searched, found something",
}

_ITEM_DISCOVERY_SQL = """
SELECT m.group_id,
       g.label,
       count(d.group_id)                                   AS attempts,
       count(*) FILTER (WHERE d.outcome = 'hit')           AS hits,
       count(*) FILTER (WHERE d.outcome = 'handoff')       AS handoffs,
       max(d.at)                                           AS last_at
FROM item_group_member m
JOIN item_group g ON g.group_id = m.group_id
LEFT JOIN discovery_log d ON d.group_id = m.group_id
WHERE m.item_ref = %(item_ref)s
GROUP BY m.group_id, g.label
"""

#: Per-rung detail for the one group, newest attempt per source. What a reviewer
#: asks next after "was it searched" is "where did we look", and the answer is
#: already in `discovery_log.source`.
_ITEM_RUNGS_SQL = """
SELECT DISTINCT ON (d.source) d.source, d.outcome, d.at
FROM discovery_log d
JOIN item_group_member m ON m.group_id = d.group_id
WHERE m.item_ref = %(item_ref)s
ORDER BY d.source, d.at DESC
"""


def discovery_state(conn, item_ref: str) -> dict | None:
    """What DISCOVER has done for this item's group, or None if it has no group.

    Returns `{group_id, label, state, attempts, hits, last_at, rungs}`. `state`
    is one of `NEVER` / `EMPTY` / `HANDOFF` / `FOUND` and uses the same
    predicate the `/discovery` list does: no `discovery_log` row at all is
    `never`, any hit is `found`, no hit but a manual handoff is `handoff`, and
    no hit and no handoff is `empty`.

    `EMPTY` is the rare one, not the common one. A ladder that runs to the end
    always reaches the manual rung, so a group that was searched and yielded
    nothing normally ends `handoff`; `empty` is left for a run that stopped
    before that -- every rung deferred, or the group re-queued mid-ladder.

    Deliberately independent of whether the item holds a document. An item can
    hold a production document that arrived by backfill while its group was
    never searched -- 755 of the 870 documents in the registry arrived that way
    (measured 2026-09-04) -- so "covered" and "searched" are different questions
    and this one answers only the second.
    """
    row = conn.execute(_ITEM_DISCOVERY_SQL, {"item_ref": item_ref}).fetchone()
    if row is None:
        return None
    if row["attempts"] == 0:
        state = NEVER
    elif row["hits"] > 0:
        state = FOUND
    elif row["handoffs"] > 0:
        state = HANDOFF
    else:
        state = EMPTY
    rungs = conn.execute(_ITEM_RUNGS_SQL, {"item_ref": item_ref}).fetchall()
    return {
        "group_id": row["group_id"],
        "label": row["label"],
        "state": state,
        "label_text": STATE_LABELS[state],
        "attempts": row["attempts"],
        "hits": row["hits"],
        "handoffs": row["handoffs"],
        "last_at": row["last_at"],
        "rungs": rungs,
    }
