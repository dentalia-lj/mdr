"""Send the documents that print one manufacturer name back through VALIDATE.

The step after a playbook alias is added and synced: the documents already
waiting on Review with that printed name were validated before the alias
existed, so they stay `manufacturer-unresolved` until VALIDATE runs again.
`dentalia revalidate --name "<printed>"` finds them and, with `--apply`, queues
`validate.doc` for each. Dry run by default.

Only documents whose last validation flagged the manufacturer unresolved go
back, and only those a machine decided and nobody has touched. The selection
was written and reviewed for the manufacturer-alias branch (`alias-action`,
2026-09-29, not merged) and is kept whole here, plus one exclusion both of its
independent reviewers asked for (reopened documents).

Spec: docs/superpowers/specs/2026-09-29-unmatched-manufacturer-names-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app import manufacturers, queue
from app.manufacturers import normalize

# Every document whose LATEST extraction prints a manufacturer. The name filter
# is `normalize` in Python, the fold VALIDATE and GATE compare with.
_ROWS_SQL = """
SELECT d.doc_id, d.content_hash, d.archive_url, d.type, d.status,
       a.extract_rev, a.fields->'manufacturer'->>'value' AS printed
  FROM document d
  JOIN LATERAL (SELECT extract_rev, fields FROM extraction_attempt x
                 WHERE x.content_hash = d.content_hash
                 ORDER BY x.extract_rev DESC, x.id DESC LIMIT 1) a ON true
 WHERE a.fields->'manufacturer'->>'value' IS NOT NULL
"""

# For the matched documents only: the latest validate.doc (none at all is its
# own case -- a document VALIDATE never ran on has no "ungrouped" verdict to
# trust) and whether it flagged the manufacturer unresolved, reviewer edits
# (T3 evidence), and a reviewer's reopen.
_CONTEXT_SQL = """
SELECT d.doc_id, vj.has_job, vj.group_id, vj.unresolved, vj.finished,
       EXISTS (SELECT 1 FROM evidence e
                WHERE e.doc_id = d.doc_id AND e.tier = 'T3') AS edited,
       EXISTS (SELECT 1 FROM audit_log l
                WHERE l.doc_id = d.doc_id AND l.event = 'reopen') AS reopened
  FROM document d
  LEFT JOIN LATERAL (
      SELECT true AS has_job, j.payload->>'group_id' AS group_id,
             COALESCE(j.result->'flags' ? 'manufacturer-unresolved', false) AS unresolved,
             j.result IS NOT NULL AS finished
        FROM job j
       WHERE j.type = 'validate.doc' AND j.payload->>'content_hash' = d.content_hash
       ORDER BY j.id DESC LIMIT 1
  ) vj ON true
 WHERE d.doc_id = ANY(%s)
"""

#: Why a document printing the name is left alone, in the order they are tested.
LEFT_ALONE = ("production", "rejected", "superseded", "no_validate_job",
              "grouped", "edited", "reopened", "pending", "resolved")


@dataclass
class Plan:
    name: str
    resolves_to: list[str]
    send: list[dict] = field(default_factory=list)
    left_alone: dict[str, int] = field(default_factory=lambda: dict.fromkeys(LEFT_ALONE, 0))
    no_items: bool = False          # the manufacturer has no catalogue groups

    @property
    def refusal(self) -> str | None:
        if not normalize(self.name):
            return "the name is empty"
        if not self.resolves_to:
            return ("this name is not an alias of any manufacturer yet. Add it to a "
                    "playbook, then run `manufacturers seed` and `playbooks sync`")
        if len(self.resolves_to) > 1:
            return (f"this name resolves to {len(self.resolves_to)} manufacturers "
                    f"({', '.join(self.resolves_to)}); fix the aliases first")
        return None


def plan(conn, name: str) -> Plan:
    out = Plan(name, manufacturers.resolve_canonicals(conn, name))
    if out.refusal:
        return out
    out.no_items = normalize(out.resolves_to[0]) not in {
        normalize(r["canonical_manufacturer"]) for r in conn.execute(
            "SELECT DISTINCT canonical_manufacturer FROM item_group").fetchall()}
    target = normalize(name)
    matched = [dict(r) for r in conn.execute(_ROWS_SQL).fetchall()
               if normalize(r["printed"]) == target]
    if not matched:
        return out
    context = {r["doc_id"]: r for r in conn.execute(
        _CONTEXT_SQL, ([r["doc_id"] for r in matched],)).fetchall()}
    for r in matched:
        ctx = context[r["doc_id"]]
        if r["status"] in ("production", "rejected", "superseded"):
            out.left_alone[r["status"]] += 1
        elif not ctx["has_job"]:
            out.left_alone["no_validate_job"] += 1
        elif ctx["group_id"] is not None:
            out.left_alone["grouped"] += 1
        elif ctx["edited"]:
            out.left_alone["edited"] += 1
        elif ctx["reopened"]:
            out.left_alone["reopened"] += 1
        elif not ctx["finished"]:
            # Its latest validation is queued, running or failed: it will
            # read the alias itself when it runs, and has no verdict yet.
            out.left_alone["pending"] += 1
        elif not ctx["unresolved"]:
            # Its last validation already named a manufacturer: the alias
            # changes nothing for it, and sending it round again is churn.
            out.left_alone["resolved"] += 1
        elif r["status"] in ("staged", "filed"):
            out.send.append(r)
    out.send.sort(key=lambda r: r["doc_id"])
    return out


_WORDS = {"production": "published", "rejected": "rejected", "superseded": "superseded",
          "no_validate_job": "never validated", "grouped": "in a catalogue group",
          "edited": "edited by a reviewer", "reopened": "reopened by a reviewer",
          "pending": "validation not finished", "resolved": "manufacturer already found"}


def render(p: Plan) -> list[str]:
    lines = [f"Printed name: {p.name}  (resolves to: {', '.join(p.resolves_to) or 'nothing'})"]
    if p.refusal:
        return lines + [f"Refused: {p.refusal}."]
    if p.no_items:
        lines.append(f"Note: {p.resolves_to[0]} has no products in the catalogue, so validation "
                     f"cannot match these documents however often it runs.")
    lines.append(f"Would send back through validation: {len(p.send)} document(s)")
    if p.send:
        shown = " · ".join(f"{r['doc_id']} {r['type']} {r['status']}" for r in p.send[:12])
        more = f" · ... ({len(p.send) - 12} more)" if len(p.send) > 12 else ""
        lines.append(f"  {shown}{more}")
    left = sum(p.left_alone.values())
    lines.append(f"Left alone: {left}")
    if left:
        lines.append("  " + " · ".join(f"{_WORDS[k]} {p.left_alone[k]}" for k in LEFT_ALONE))
    return lines


def apply(conn, p: Plan) -> dict:
    """Queue `validate.doc` for every planned document, with the payload and
    dedupe key the pipeline uses for a document outside any catalogue group
    (`app/handlers/extract.py`). An active job already holding the key is
    counted, not duplicated (invariant 8)."""
    if p.refusal:
        raise ValueError(p.refusal)
    queued = already = 0
    for r in p.send:
        jid = queue.enqueue(
            conn, "validate.doc",
            {"content_hash": r["content_hash"], "group_id": None,
             "extract_rev": r["extract_rev"], "archive_url": r["archive_url"]},
            f"validate:{r['content_hash']}:{r['extract_rev']}:None")
        if jid is None:
            already += 1
        else:
            queued += 1
    return {"queued": queued, "already_queued": already}
