"""Undo RESOLVE's grouping for a set of items, so they resolve again (S1.8).

This exists to make the first `manufacturer_alias` seed reversible.

`item_group.canonical_manufacturer` is effectively write-once: RESOLVE's ladder
is stop-at-first-hit with `_existing_link` as its first rung, so once an item
belongs to a group, re-running `resolve.group` returns that group and never
re-derives the manufacturer. An alias fixed after the fact does not retro-fix
the groups already built under the old value -- that is what `playbooks sync`
reports as `orphaned_groups`. Without a way to drop those groups, a wrong seed
is permanent.

Repair is cheaper than "no repair path" implies, and the distinction matters:

- A group whose `canonical_manufacturer` is merely WRONG can be fixed in place
  with an UPDATE. That is a one-line correction, not this module.
- A group whose MEMBERSHIP is wrong (items that should never have been pooled,
  or should have been pooled and were not) can only be fixed by dropping the
  group and letting RESOLVE rebuild it. That is this module.

What makes it unrecoverable is not the grouping: it is documents. Once a member
item carries an `item_document` row, dropping its group throws away the link
between a real document and a real item, and nothing rebuilds that. So a group
with documents is refused, never regrouped.

The other refusals are dangling soft references. `item_group` has exactly two
FK children (`item_group_member`, `discovery_log`), which are deleted here; but
`manual_task.group_id`, `upload_inbox.target_group_id` and the `group_id`s
inside `grouping_suggestion.candidates` are deliberately FK-free, so Postgres
would let this leave them pointing at groups that no longer exist. A human
later resolving one of those would write against a dead id.

Two of those three are refused; the third is rebuilt, and the difference is
whether the row is DERIVED or OWNED:

- An open `grouping_suggestion` is derived state. `resolve.group` produced it
  and `resolve.group` will produce it again, so it is deleted and its item
  re-enqueued along with the group's members. Blocking on it instead was the
  first thing tried and it made the tool useless on real data: on a 400-row
  slice of the LJ export, 26 of 141 groups were unregroupable, and the items
  holding those suggestions are precisely the ones whose grouping is in
  question. They are also NOT members of any group, so without pulling them in
  explicitly a regroup would strand them: no group, and an open suggestion
  pointing at group ids that no longer exist.
- An open `manual_task` and an undrained `upload_inbox` row are owned. The task
  may carry a human's in-progress context; the upload row carries file bytes
  that exist nowhere else. Neither is reproducible by re-running anything, so
  both refuse.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app import queue

#: Re-resolving after an alias correction is a correction, not a backfill.
DEFAULT_PRIORITY = "delta"


@dataclass(frozen=True)
class Blocker:
    kind: str
    group_id: int
    count: int
    detail: str


@dataclass(frozen=True)
class Scope:
    """What a regroup would touch, and what stops it."""

    groups: tuple[dict, ...] = ()
    items: tuple[str, ...] = ()          # members + stranded suggestion items
    blockers: tuple[Blocker, ...] = ()
    discovery_rows: int = 0
    #: open grouping_suggestion ids that reference a selected group. Deleted and
    #: regenerated, not refused -- see the module docstring.
    suggestions: tuple[int, ...] = ()

    @property
    def blocked_group_ids(self) -> frozenset[int]:
        return frozenset(b.group_id for b in self.blockers)

    @property
    def clear(self) -> bool:
        return not self.blockers


def _ids(groups) -> list[int]:
    return [g["group_id"] for g in groups]


def scope(
    conn,
    *,
    manufacturers=None,
    group_ids=None,
    all_groups: bool = False,
) -> Scope:
    """Read-only: the groups selected, the items that would be re-enqueued, and
    every reason not to. Selection is by canonical manufacturer (the unit an
    alias correction moves), by explicit group id, or all.
    """
    if all_groups:
        groups = conn.execute(
            "SELECT group_id, canonical_manufacturer, label FROM item_group "
            "ORDER BY group_id"
        ).fetchall()
    else:
        clauses, params = [], []
        if manufacturers:
            clauses.append("canonical_manufacturer = ANY(%s)")
            params.append(list(manufacturers))
        if group_ids:
            clauses.append("group_id = ANY(%s)")
            params.append([int(g) for g in group_ids])
        if not clauses:
            return Scope()
        groups = conn.execute(
            "SELECT group_id, canonical_manufacturer, label FROM item_group "
            f"WHERE {' OR '.join(clauses)} ORDER BY group_id",
            tuple(params),
        ).fetchall()

    if not groups:
        return Scope()

    gids = _ids(groups)
    members = conn.execute(
        "SELECT group_id, item_ref FROM item_group_member WHERE group_id = ANY(%s) "
        "ORDER BY item_ref",
        (gids,),
    ).fetchall()

    blockers: list[Blocker] = []

    # The one irreversible loss: a document already linked to a member item.
    for row in conn.execute(
        "SELECT m.group_id, count(*) AS n "
        "FROM item_group_member m JOIN item_document d USING (item_ref) "
        "WHERE m.group_id = ANY(%s) GROUP BY m.group_id ORDER BY m.group_id",
        (gids,),
    ).fetchall():
        blockers.append(Blocker(
            kind="has-documents",
            group_id=row["group_id"],
            count=row["n"],
            detail=(
                f"{row['n']} item_document link(s) on its members. Regrouping "
                "would discard document-to-item links nothing rebuilds."
            ),
        ))

    # Dangling soft references (no FK, so Postgres will not stop this).
    for row in conn.execute(
        "SELECT group_id, count(*) AS n FROM manual_task "
        "WHERE status='open' AND group_id = ANY(%s) GROUP BY group_id "
        "ORDER BY group_id",
        (gids,),
    ).fetchall():
        blockers.append(Blocker(
            kind="open-manual-task",
            group_id=row["group_id"],
            count=row["n"],
            detail=(
                f"{row['n']} open manual_task(s) point here. Resolving one after "
                "the group is gone writes against a dead group_id."
            ),
        ))

    # Derived, so rebuilt rather than refused: an open suggestion offering a
    # selected group as a candidate is deleted, and its item joins the
    # re-enqueue set. Those items belong to no group, so without this they
    # would be stranded holding a candidate list of dead ids.
    stranded = conn.execute(
        "SELECT DISTINCT s.id, s.item_ref "
        "FROM grouping_suggestion s, jsonb_array_elements(s.candidates) c "
        "WHERE s.status='open' AND (c->>'group_id')::bigint = ANY(%s) "
        "ORDER BY s.id",
        (gids,),
    ).fetchall()

    for row in conn.execute(
        "SELECT target_group_id AS group_id, count(*) AS n FROM upload_inbox "
        "WHERE target_group_id = ANY(%s) GROUP BY 1 ORDER BY 1",
        (gids,),
    ).fetchall():
        blockers.append(Blocker(
            kind="undrained-upload",
            group_id=row["group_id"],
            count=row["n"],
            detail=(
                f"{row['n']} spooled upload(s) target this group and have not "
                "been drained yet."
            ),
        ))

    discovery_rows = conn.execute(
        "SELECT count(*) AS n FROM discovery_log WHERE group_id = ANY(%s)",
        (gids,),
    ).fetchone()["n"]

    items = {m["item_ref"] for m in members} | {s["item_ref"] for s in stranded}
    return Scope(
        groups=tuple(groups),
        items=tuple(sorted(items)),
        blockers=tuple(blockers),
        discovery_rows=discovery_rows,
        suggestions=tuple(s["id"] for s in stranded),
    )


@dataclass(frozen=True)
class RenameImpact:
    """What renaming one canonical manufacturer strands. Read-only."""

    manufacturer: str
    groups: int = 0                  # item_group rows still carrying the name
    items: int = 0                   # members of those groups
    with_documents: int = 0          # of those groups, ones a regroup refuses
    documents: int = 0               # item_document links across them
    bound_documents: int = 0         # 053: document rows the FK cascade rewrites

    @property
    def clean(self) -> bool:
        """No groups carry the old name, so nothing is stranded by renaming."""
        return self.groups == 0

    @property
    def command(self) -> str:
        return f'dentalia regroup --manufacturer "{self.manufacturer}" --apply'


def rename_impact(conn, manufacturer: str) -> RenameImpact:
    """The blast radius of a rename, for the UI to show BEFORE it writes.

    A deliberate SUBSET of `scope()`, and the reason is grants, not taste. The
    web runs as `dentalia_api`, which is INSERT-only on `upload_inbox` -- "it
    must never read the spool back" (migration 014), because that column holds
    the uploaded file bytes -- so `scope()`'s undrained-upload query raises a
    permission error in that process. Rather than widen the grant for a count,
    this asks only what the web may already read, and the UI points at
    `dentalia regroup --dry-run` for the full blocker list.

    The two numbers here are the ones a rename decision turns on: how many
    groups still carry the old name (they do NOT follow a rename -- RESOLVE
    short-circuits on `_existing_link`), and how many of those can never be
    regrouped because a member already carries a document link. A test pins
    both against `scope()` so the subset cannot quietly stop agreeing with it.
    """
    # 053: counted BEFORE the early return. A manufacturer whose groups were all
    # regrouped away can still hold filed documents -- 443 of the 540 link-less
    # documents are filed -- and the FK's ON UPDATE CASCADE rewrites every one
    # of them on a rename. Reporting groups but not these would understate the
    # blast radius, which is the failure 052 was written to close.
    bound_documents = conn.execute(
        "SELECT count(*) AS n FROM document WHERE canonical_manufacturer = %s",
        (manufacturer,),
    ).fetchone()["n"]

    groups = conn.execute(
        "SELECT group_id FROM item_group WHERE canonical_manufacturer = %s "
        "ORDER BY group_id",
        (manufacturer,),
    ).fetchall()
    if not groups:
        return RenameImpact(manufacturer=manufacturer,
                            bound_documents=bound_documents)

    gids = _ids(groups)
    items = conn.execute(
        "SELECT count(*) AS n FROM item_group_member WHERE group_id = ANY(%s)",
        (gids,),
    ).fetchone()["n"]
    doc_rows = conn.execute(
        "SELECT m.group_id, count(*) AS n "
        "FROM item_group_member m JOIN item_document d USING (item_ref) "
        "WHERE m.group_id = ANY(%s) GROUP BY m.group_id",
        (gids,),
    ).fetchall()
    return RenameImpact(
        manufacturer=manufacturer,
        groups=len(groups),
        items=items,
        with_documents=len(doc_rows),
        documents=sum(r["n"] for r in doc_rows),
        bound_documents=bound_documents,
    )


def apply(conn, sc: Scope, *, priority: str = DEFAULT_PRIORITY) -> dict:
    """Drop the selected groups and re-enqueue `resolve.group` per member item.

    Refuses outright when anything is blocked. Not "skip the blocked ones and
    do the rest": the selection is an operator's stated intent, and silently
    doing three quarters of it is how a half-regrouped catalogue happens. Fix
    or narrow the selection and re-run.

    Deletion order follows the FKs inward: discovery_log -> item_group_member
    -> item_group.
    """
    if not sc.groups:
        return {"groups": 0, "items": 0, "enqueued": 0, "already_queued": 0,
                "discovery_rows": 0, "suggestions": 0}
    if sc.blockers:
        raise ValueError(
            f"{len(sc.blockers)} blocker(s) across "
            f"{len(sc.blocked_group_ids)} group(s); refusing to regroup any of "
            f"the {len(sc.groups)} selected."
        )

    gids = _ids(sc.groups)
    if sc.suggestions:
        # Before the groups, so the unique open-per-item index cannot collide
        # with a suggestion the re-resolve recreates.
        conn.execute(
            "DELETE FROM grouping_suggestion WHERE id = ANY(%s)",
            (list(sc.suggestions),),
        )
    conn.execute("DELETE FROM discovery_log WHERE group_id = ANY(%s)", (gids,))
    conn.execute("DELETE FROM item_group_member WHERE group_id = ANY(%s)", (gids,))
    conn.execute("DELETE FROM item_group WHERE group_id = ANY(%s)", (gids,))

    # Same dedupe key INGEST uses, so a still-active resolve job for this item
    # at this revision dedupes instead of stacking a second one (invariant 8:
    # terminal jobs never block, so the normal case does enqueue).
    enqueued = already = 0
    for item_ref in sc.items:
        row = conn.execute(
            "SELECT mirror_rev FROM item_mirror WHERE item_ref=%s", (item_ref,)
        ).fetchone()
        if row is None:
            # A member whose mirror row is gone cannot be re-resolved; the FK
            # makes this unreachable, and it is counted rather than assumed.
            continue
        jid = queue.enqueue(
            conn,
            "resolve.group",
            {"item_ref": item_ref},
            dedupe_key=f"resolve:{item_ref}:{row['mirror_rev']}",
            priority=priority,
        )
        if jid is None:
            already += 1
        else:
            enqueued += 1

    return {
        "groups": len(sc.groups),
        "items": len(sc.items),
        "enqueued": enqueued,
        "already_queued": already,
        "discovery_rows": sc.discovery_rows,
        "suggestions": len(sc.suggestions),
    }


def render(sc: Scope, *, limit: int = 20) -> list[str]:
    if not sc.groups:
        return ["no groups match the selection."]

    out = [
        f"{len(sc.groups)} group(s), {len(sc.items)} item(s), "
        f"{sc.discovery_rows} discovery_log row(s) would be dropped and "
        f"re-resolved",
    ]
    if sc.suggestions:
        out.append(
            f"  + {len(sc.suggestions)} open grouping suggestion(s) deleted and "
            "their items re-resolved (derived state, regenerated)"
        )
    by_mfr: dict[str, int] = {}
    for g in sc.groups:
        by_mfr[g["canonical_manufacturer"]] = by_mfr.get(g["canonical_manufacturer"], 0) + 1
    out.append("  by canonical_manufacturer:")
    for name, n in sorted(by_mfr.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]:
        out.append(f"    {name:<40} {n:>5} group(s)")
    if len(by_mfr) > limit:
        out.append(f"    ... {len(by_mfr) - limit} more manufacturer(s)")

    if sc.blockers:
        out.append("")
        out.append(f"BLOCKED ({len(sc.blockers)} across "
                   f"{len(sc.blocked_group_ids)} group(s))")
        for b in sc.blockers[:limit]:
            out.append(f"  [{b.kind}] group {b.group_id}: {b.detail}")
        if len(sc.blockers) > limit:
            out.append(f"  ... {len(sc.blockers) - limit} more")
    return out
