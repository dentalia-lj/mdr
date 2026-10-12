"""Send chosen documents back through EXTRACT after a model or prompt change.

`python -m app.cli reextract` re-enqueues `extract.doc` for the content hashes a
selector picks. Dry run by default; `--apply` enqueues.

Why this is allowed. Invariant 6 says unchanged content is never re-parsed. The
rule keys on the bytes, and it is right while the reader is fixed. After a
model or prompt change the reader is no longer the same input: a file that was
read under the old prompt can hold a value the new one finds, and nothing else
would ever send it back. This command is the deliberate operator exception,
never a schedule, and a new read APPENDS an `extract_rev` (the attempt history
stays auditable, as `repair-ref-list` and `repair-label-dates` do) rather than
overwriting. The re-read costs real model calls, which is why nothing here runs
without `--apply`.

Selectors (any combination; a hash chosen by two is sent once):

  --doc-cert-null     DoCs whose latest read gave `cert_number` from T1 and left
                      it null: T1 was told DoCs carry no certificate number.
  --dropped-scans     scans (no text layer) that never became a document
                      because the doc-class call refused them
                      (`not-a-compliance-document`) before the scan gate.
  --hashes / --hashes-file   an explicit list of content hashes.

Documents a person touched are left alone, in the manner of
`app/revalidate_name.py`: rejected, superseded, reviewer-edited (any T3
evidence) and reopened ones. So is a hash with an `extract.doc` job already
active, and one with no earlier `extract.doc` job (there is no payload to copy
and none is guessed). Every exclusion is counted by reason, per selector.

The payload of the hash's most recent `extract.doc` job is copied whole
(payloads are immutable once enqueued, consumers tolerate unknown fields but
never missing ones), under the dedupe key every emitter uses,
`extract:<hash>` (active jobs only; terminal ones never block it).

Sync mode only. `batch_ref` is keyed (content_hash, tier) with no revision, so
in batch mode a resolved row hands back the old results and the re-read would
be a silent no-op (`app/extract/batching.py`).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from app import queue
from app.extract.tiers import NOT_A_DOCUMENT

SELECTORS = ("doc-cert-null", "dropped-scans", "hashes")

#: Why a chosen hash is left alone, in the order they are tested.
LEFT_ALONE = ("no_extract_job", "active", "rejected", "superseded", "edited", "reopened")

_WORDS = {"no_extract_job": "no previous extract job", "active": "extract job already active",
          "rejected": "rejected", "superseded": "superseded",
          "edited": "edited by a reviewer", "reopened": "reopened by a reviewer",
          "repeat": "already chosen by an earlier selector"}

# (a) latest read of a DoC: cert_number came from T1 and is null.
_CERT_NULL_SQL = """
SELECT DISTINCT d.content_hash
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
# doc-class call refused it.
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

_CONTEXT_SQL = """
SELECT h.content_hash, ej.payload,
       EXISTS (SELECT 1 FROM job a WHERE a.type = 'extract.doc'
                AND a.payload->>'content_hash' = h.content_hash
                AND a.status IN ('pending','running','failed')) AS active,
       EXISTS (SELECT 1 FROM document d WHERE d.content_hash = h.content_hash
                AND d.status = 'rejected') AS rejected,
       EXISTS (SELECT 1 FROM document d WHERE d.content_hash = h.content_hash
                AND d.status = 'superseded') AS superseded,
       EXISTS (SELECT 1 FROM document d JOIN evidence e ON e.doc_id = d.doc_id
                WHERE d.content_hash = h.content_hash AND e.tier = 'T3') AS edited,
       EXISTS (SELECT 1 FROM document d JOIN audit_log l ON l.doc_id = d.doc_id
                WHERE d.content_hash = h.content_hash AND l.event = 'reopen') AS reopened
  FROM unnest(%s::text[]) AS h(content_hash)
  LEFT JOIN LATERAL (SELECT payload FROM job j
                      WHERE j.type = 'extract.doc' AND j.payload->>'content_hash' = h.content_hash
                      ORDER BY j.id DESC LIMIT 1) ej ON true
"""


def extract_mode() -> str:
    """The mode `app/handlers/extract.py` dispatches on, read the same way."""
    return os.environ.get("DENTALIA_EXTRACT_MODE", "sync").lower()


def refusal() -> str | None:
    if extract_mode() == "batch":
        return ("DENTALIA_EXTRACT_MODE=batch: reextract only works in sync mode. "
                "batch_ref is keyed (content_hash, tier) with no revision, so a resolved "
                "row returns the old results and the re-read would change nothing")
    return None


@dataclass
class Selection:
    selector: str
    found: int = 0
    send: int = 0
    left_alone: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys((*LEFT_ALONE, "repeat"), 0))


@dataclass
class Plan:
    selections: list[Selection] = field(default_factory=list)
    send: list[dict] = field(default_factory=list)   # {content_hash, payload, selectors}


def select_hashes(conn, selector: str) -> list[str]:
    if selector == "doc-cert-null":
        return [r["content_hash"] for r in conn.execute(_CERT_NULL_SQL).fetchall()]
    if selector == "dropped-scans":
        return [r["content_hash"] for r in conn.execute(_SCANS_SQL, (NOT_A_DOCUMENT,)).fetchall()]
    raise ValueError(selector)


def plan(conn, *, doc_cert_null: bool = False, dropped_scans: bool = False,
         hashes: list[str] | None = None) -> Plan:
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

    out = Plan()
    sent: dict[str, dict] = {}
    for name, hs in chosen:
        sel = Selection(name, found=len(hs))
        out.selections.append(sel)
        for h in hs:
            ctx = context[h]
            if ctx["payload"] is None:
                sel.left_alone["no_extract_job"] += 1
                continue
            reason = next((k for k in LEFT_ALONE[1:] if ctx[k]), None)
            if reason:
                sel.left_alone[reason] += 1
            elif h in sent:
                sel.left_alone["repeat"] += 1
                sent[h]["selectors"].append(name)
            else:
                sel.send += 1
                sent[h] = {"content_hash": h, "payload": dict(ctx["payload"]),
                           "selectors": [name]}
    out.send = [sent[h] for h in sorted(sent)]
    return out


def render(p: Plan) -> list[str]:
    lines = []
    for s in p.selections:
        lines.append(f"{s.selector}: found {s.found}, would enqueue {s.send}")
        left = sum(s.left_alone.values())
        lines.append(f"  left alone: {left}")
        if left:
            lines.append("  " + " · ".join(
                f"{_WORDS[k]} {n}" for k, n in s.left_alone.items() if n))
    lines.append(f"Total extract.doc jobs to enqueue: {len(p.send)}")
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
