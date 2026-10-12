"""Send chosen documents back through EXTRACT after a model or prompt change.

`python -m app.cli reextract` re-enqueues `extract.doc` for the content hashes a
selector picks. Dry run by default; `--apply` enqueues.

Why this is allowed. Invariant 6 says unchanged content is never re-parsed, and
it keys on the bytes. The contract already names the exception: PRD v3 C2 freed
terminal `extract:<hash>` keys precisely so that "re-extracting after a model
swap is a normal operation", and the 2026-10-12 ruling "Certificate number on a
DoC" (`docs/decisions.md`) asks for a re-read of the T1-read DoCs. This command
is that operation, run by an operator, never on a schedule. A new read APPENDS
an `extract_rev` (the attempt history stays auditable) rather than overwriting,
and it costs real model calls, which is why nothing here runs without
`--apply`.

Selectors (any combination; a hash chosen by two is sent once):

  --doc-cert-null     DoCs whose latest read gave `cert_number` from T1 and left
                      it null: T1 was told DoCs carry no certificate number.
  --dropped-scans     scans (no text layer) that never became a document
                      because the doc-class call refused them
                      (`not-a-compliance-document`) before the scan gate.
  --hashes / --hashes-file   an explicit list of content hashes.

Documents a person touched are left alone, in the manner of
`app/revalidate_name.py`: rejected, superseded, approved or bound by a reviewer
(an `approve`, `bind-manufacturer` or `production-write` audit row by anyone but
a machine actor), reviewer-edited (any T3 evidence) and reopened ones. Published
documents nobody approved are left alone too unless `include_published` is
set: re-reading one is a decision, not a default. Since 2026-10-12 GATE
replaces a published document only with a read that itself reaches production
and otherwise keeps it as it was, counting the fields the read saw differently
(`gate._kept`).
Also left alone: a hash with an `extract.doc` job already active, and one with
no earlier `extract.doc` job (there is no payload to copy and none is guessed).
Every exclusion is counted by reason, per selector.

The payload of the hash's most recent `extract.doc` job is copied (payloads are
immutable once enqueued, consumers tolerate unknown fields but never missing
ones), with one change: when the registry holds the document, its
`archive_url` replaces the job's, as `archiving.emit_validate` and
`revalidate_name.apply` do, because GATE prefers the payload's handle and an old
one would undo a re-homed archive (`repair-archive-urls`). The dedupe key is
the one every emitter uses, `extract:<hash>` (active jobs only; terminal ones
never block it).

Sync mode only. `batch_ref` is keyed (content_hash, tier) with no revision, so
in batch mode a resolved row hands back the old results and the re-read would
change nothing (`app/extract/batching.py`).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app import queue
from app.extract import batching
from app.extract.tiers import NOT_A_DOCUMENT

#: Why a chosen hash is left alone, in the order they are tested. `published`
#: applies only without `include_published`.
LEFT_ALONE = ("no_extract_job", "active", "rejected", "superseded", "approved",
              "edited", "reopened", "published")

#: How many planned hashes the dry run prints by name.
SHOWN = 12

_WORDS = {"no_extract_job": "no previous extract job", "active": "extract job already active",
          "rejected": "rejected", "superseded": "superseded",
          "approved": "approved by a reviewer",
          "edited": "edited by a reviewer", "reopened": "reopened by a reviewer",
          "published": "published (add --include-published to read them too)",
          "repeat": "already chosen by an earlier selector"}

# Audit actors that are not a person: GATE itself, RESOLVE's `system`, and the
# `ops:` / `tool:` repair scripts. Every other `decided_by` is a reviewer (the
# proxy login, or `user:admin` when there is none, `web/app.py`).
_MACHINE_ACTOR_SQL = ("(l.decided_by IN ('gate', 'system') "
                      "OR l.decided_by LIKE 'ops:%%' OR l.decided_by LIKE 'tool:%%')")

# (a) latest read of a DoC: cert_number came from T1 and is null.
_CERT_NULL_SQL = """
SELECT d.content_hash
  FROM document d
  JOIN LATERAL (SELECT fields FROM extraction_attempt x
                 WHERE x.content_hash = d.content_hash
                 ORDER BY x.extract_rev DESC, x.id DESC LIMIT 1) a ON true
 WHERE d.type = 'DoC'
   AND a.fields->'cert_number'->>'tier' = 'T1'
   AND a.fields->'cert_number'->>'value' IS NULL
 ORDER BY 1
"""

# (b) no text layer, no document row, and the latest extract.doc result says the
# doc-class call refused it. A scan whose latest job is still active or dead has
# no result and is not found: it is not known to have been refused.
_SCANS_SQL = """
SELECT t.content_hash
  FROM document_text t
  JOIN LATERAL (SELECT result FROM job j
                 WHERE j.type = 'extract.doc' AND j.payload->>'content_hash' = t.content_hash
                 ORDER BY j.id DESC LIMIT 1) lj ON true
 WHERE t.source = 'none'
   AND NOT EXISTS (SELECT 1 FROM document d WHERE d.content_hash = t.content_hash)
   AND lj.result->>'doc_class' = %s
 ORDER BY 1
"""

_CONTEXT_SQL = f"""
SELECT h.content_hash, ej.payload, d.archive_url AS doc_archive_url,
       EXISTS (SELECT 1 FROM job a WHERE a.type = 'extract.doc'
                AND a.payload->>'content_hash' = h.content_hash
                AND a.status IN ('pending','running','failed')) AS active,
       COALESCE(d.status = 'rejected', false) AS rejected,
       COALESCE(d.status = 'superseded', false) AS superseded,
       COALESCE(d.status = 'production', false) AS published,
       EXISTS (SELECT 1 FROM audit_log l
                WHERE l.doc_id = d.doc_id
                  AND l.event IN ('approve', 'bind-manufacturer', 'production-write')
                  AND NOT {_MACHINE_ACTOR_SQL}) AS approved,
       EXISTS (SELECT 1 FROM evidence e
                WHERE e.doc_id = d.doc_id AND e.tier = 'T3') AS edited,
       EXISTS (SELECT 1 FROM audit_log l
                WHERE l.doc_id = d.doc_id AND l.event = 'reopen') AS reopened
  FROM unnest(%s::text[]) AS h(content_hash)
  LEFT JOIN document d ON d.content_hash = h.content_hash
  LEFT JOIN LATERAL (SELECT payload FROM job j
                      WHERE j.type = 'extract.doc' AND j.payload->>'content_hash' = h.content_hash
                      ORDER BY j.id DESC LIMIT 1) ej ON true
"""


def refusal() -> str | None:
    if batching.extract_mode() == "batch":
        return ("DENTALIA_EXTRACT_MODE=batch: reextract only works in sync mode. "
                "batch_ref is keyed (content_hash, tier) with no revision, so a resolved "
                "row returns the old results and the re-read would change nothing")
    return None


@dataclass
class Selection:
    selector: str
    found: int = 0
    eligible: int = 0
    published: int = 0          # eligible hashes that are published (include_published)
    left_alone: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys((*LEFT_ALONE, "repeat"), 0))


@dataclass
class Plan:
    selections: list[Selection] = field(default_factory=list)
    send: list[dict] = field(default_factory=list)   # {content_hash, payload, selectors}
    held_back: int = 0          # eligible, but past `limit`
    limit: int | None = None
    rehomed: int = 0            # payloads whose archive_url the registry replaced


def select_hashes(conn, selector: str) -> list[str]:
    if selector == "doc-cert-null":
        return [r["content_hash"] for r in conn.execute(_CERT_NULL_SQL).fetchall()]
    if selector == "dropped-scans":
        return [r["content_hash"] for r in conn.execute(_SCANS_SQL, (NOT_A_DOCUMENT,)).fetchall()]
    raise ValueError(selector)


def plan(conn, *, doc_cert_null: bool = False, dropped_scans: bool = False,
         hashes: list[str] | None = None, include_published: bool = False,
         limit: int | None = None) -> Plan:
    """What would be sent, and every hash left alone by reason. `limit` keeps
    the first N eligible hashes in hash order (an unbiased sample, as content
    hashes are uniform) and counts the rest as held back."""
    chosen: list[tuple[str, list[str]]] = []
    if doc_cert_null:
        chosen.append(("doc-cert-null", select_hashes(conn, "doc-cert-null")))
    if dropped_scans:
        chosen.append(("dropped-scans", select_hashes(conn, "dropped-scans")))
    if hashes is not None:
        chosen.append(("hashes", list(dict.fromkeys(hashes))))

    universe = sorted({h for _, hs in chosen for h in hs})
    context = {r["content_hash"]: r
               for r in conn.execute(_CONTEXT_SQL, (universe,)).fetchall()} if universe else {}

    out = Plan(limit=limit)
    sent: dict[str, dict] = {}
    for name, hs in chosen:
        sel = Selection(name, found=len(hs))
        out.selections.append(sel)
        for h in hs:
            ctx = context[h]
            if ctx["payload"] is None:
                sel.left_alone["no_extract_job"] += 1
                continue
            reason = next((k for k in LEFT_ALONE[1:-1] if ctx[k]), None)
            if reason is None and ctx["published"] and not include_published:
                reason = "published"
            if reason:
                sel.left_alone[reason] += 1
            elif h in sent:
                sel.left_alone["repeat"] += 1
                sent[h]["selectors"].append(name)
            else:
                sel.eligible += 1
                sel.published += bool(ctx["published"])
                payload = dict(ctx["payload"])
                registry_url = ctx["doc_archive_url"]
                if registry_url and payload.get("archive_url") != registry_url:
                    payload["archive_url"] = registry_url
                    out.rehomed += 1
                sent[h] = {"content_hash": h, "payload": payload, "selectors": [name]}
    ordered = [sent[h] for h in sorted(sent)]
    if limit is not None and len(ordered) > limit:
        out.held_back = len(ordered) - limit
        ordered = ordered[:limit]
    out.send = ordered
    return out


def render(p: Plan) -> list[str]:
    lines = []
    for s in p.selections:
        pub = f" ({s.published} published)" if s.published else ""
        lines.append(f"{s.selector}: found {s.found}, eligible {s.eligible}{pub}")
        left = sum(s.left_alone.values())
        lines.append(f"  left alone: {left}")
        if left:
            lines.append("  " + " · ".join(
                f"{_WORDS[k]} {n}" for k, n in s.left_alone.items() if n))
    total = f"Total extract.doc jobs to enqueue: {len(p.send)}"
    if p.held_back:
        total += f" (--limit {p.limit}; {p.held_back} more eligible, left for a later run)"
    lines.append(total)
    if p.rehomed:
        lines.append(f"  archive handle taken from the registry, not the old job: {p.rehomed}")
    for r in p.send[:SHOWN]:
        lines.append(f"  {r['content_hash']}  {', '.join(r['selectors'])}")
    if len(p.send) > SHOWN:
        lines.append(f"  ... ({len(p.send) - SHOWN} more)")
    return lines


def apply(conn, p: Plan) -> dict:
    """Enqueue `extract.doc` for every planned hash with its copied payload. A
    job that became active since the plan was made is counted, not duplicated
    (invariant 8)."""
    reason = refusal()
    if reason:
        raise ValueError(reason)
    queued = already = 0
    for r in p.send:
        jid = queue.enqueue(conn, "extract.doc", r["payload"], f"extract:{r['content_hash']}")
        if jid is None:
            already += 1
        else:
            queued += 1
    return {"queued": queued, "already_queued": already}
