"""One-off backfill: record the class the already-held documents state.

`stated_class` became the eleventh extraction target on 2026-08-21 (PRD §5,
migration 032). Every document extracted before that carries no such key, and
re-extracting 768 of them to record a display value would be absurd: T0 reads
the class off plain text, the text is already stored (`document_text`, migration
018), and the bytes have not changed.

The shape is `repair_ref_list`'s -- plan / render / apply, dry run by default --
with two deliberate departures, both of them simplifications:

  * the source is `document_text.content`, not the archived PDF. No file is
    reopened and pymupdf is never touched, so a moved or unreadable corpus file
    cannot skip a row the way it can in `repair_ref_list`.

  * **no `validate.doc` re-emission and no pending-job re-pointing.**
    `repair_ref_list` re-emits because it changes what a document COVERS, and
    coverage has to be re-adjudicated. This changes nothing a disposition is
    made of: `stated_class` is in no VALIDATE rule, in no GATE flag, and in
    `REQUIRED_FIELDS` nowhere near `_score`. Re-running 768 settled dispositions
    to record it would put the whole registry's status back through the gate for
    a value no gate reads.

    The consequence is intended and is not a gap: historic rows get the value in
    `extraction_attempt` and NOT in `document.stated_class`, which only a GATE
    write can fill (invariant 1). The `item_class_check` view (033) therefore
    COALESCEs the column over the latest attempt's jsonb, and history is
    compared without ever being re-decided. A document re-extracted later for
    any ordinary reason fills its column on the next GATE write.

Append-only, matching migration 004's "every tier attempt kept": a repair lands
as a NEW `extract_rev` and never overwrites the original. The new rev copies
every field of the old one and adds exactly one key -- the latest rev is what
later readers take, so it must never be the poorest.

A hash that already carries the key is RE-CHECKED, not waved through
(2026-08-24, after the first `--apply` ran with the pre-guard parser and baked
two boilerplate reads into rev 2 -- docs 389 `III` and 791 `I`): the stored
text is re-read with the current parser, agreement counts `already_has_key`,
disagreement appends a corrective rev carrying the fresh value -- or carrying
NO `stated_class` key at all when the current parser abstains, which is how a
wrong read is retracted under an append-only contract. Correction compares
VALUES only: a verbatim that would read differently today does not churn a rev
over a value that has not changed.

Nothing is silent (CLAUDE.md): every scanned hash lands in exactly one of
`stated` / `corrected` / `abstained_multi` / `no_mention` / `already_has_key`.

Dry run by default, like `repair-ref-list` and `regroup`:

    python -m app.cli repair-stated-class            # report only
    python -m app.cli repair-stated-class --apply
"""

from __future__ import annotations

from app.extract import tiers
from app.extract.t0_templates import extract_stated_class, mentions_class

#: Every scanned hash lands in exactly one of these. Named here so the report,
#: the CLI and the arithmetic guard in the tests all read the same list.
OUTCOMES = ("stated", "corrected", "abstained_multi", "no_mention", "already_has_key")


def _latest_attempts(conn) -> list[dict]:
    """One row per content_hash — its highest `extract_rev` — for every hash the
    registry actually holds a document for, joined to the text already extracted
    from it (migration 018).

    `JOIN document` is the scope: an extraction whose document never reached the
    registry has nothing to be compared against (the 033 view joins `document`
    too), so repairing it would append revs no screen can read. `JOIN
    document_text` is belt and braces — every registry document has a row,
    measured 768/768 on 2026-08-21 — but a hash without stored text simply has
    nothing to read and must not become a crash.
    """
    return conn.execute(
        """
        SELECT DISTINCT ON (ea.content_hash)
               ea.content_hash, ea.extract_rev, ea.tier, ea.model_id, ea.fields,
               ea.playbook_slug, ea.playbook_rev,
               dt.content AS text
        FROM extraction_attempt ea
        JOIN document d       ON d.content_hash  = ea.content_hash
        JOIN document_text dt ON dt.content_hash = ea.content_hash
        ORDER BY ea.content_hash, ea.extract_rev DESC
        """
    ).fetchall()


def plan(conn) -> tuple[list[dict], dict]:
    """`(repairs, counts)`. A repair is a latest attempt whose stored text states
    exactly one class and whose fields do not already carry the key -- or one
    whose carried key disagrees with what the current parser reads (`corrected`).

    Planning off the LATEST rev is what makes this re-runnable: once applied, the
    repair's own output becomes the incumbent and plans empty -- an added or
    corrected value re-reads as agreement (`already_has_key`), a retraction
    re-reads as the abstention or silence that caused it.
    """
    repairs: list[dict] = []
    counts = {"scanned": 0, **{k: 0 for k in OUTCOMES}}
    for row in _latest_attempts(conn):
        counts["scanned"] += 1
        old = row["fields"] or {}
        text = row["text"] or ""
        if "stated_class" in old:
            # Re-check, never wave through: the key may have been written by
            # an older parser (the first --apply predated the plural-devices
            # guard and recorded two boilerplate reads). Values compared, not
            # verbatims -- an unchanged value never earns a rev.
            fresh = extract_stated_class(text)
            if fresh is not None and fresh["value"] == old["stated_class"].get("value"):
                counts["already_has_key"] += 1
                continue
            fields_new = {k: v for k, v in old.items() if k != "stated_class"}
            if fresh is not None:
                fields_new["stated_class"] = fresh
            counts["corrected"] += 1
            repairs.append({**row, "fields_new": fields_new, "kind": "corrected",
                            "old_value": old["stated_class"].get("value")})
            continue
        ev = extract_stated_class(text)
        if ev is None:
            # The two ways T0 returns nothing are different findings and are
            # reported apart: a document that never names a class has nothing to
            # give, one that names several is the parser declining.
            counts["abstained_multi" if mentions_class(text) else "no_mention"] += 1
            continue
        counts["stated"] += 1
        repairs.append({**row, "fields_new": {**old, "stated_class": ev},
                        "kind": "stated"})
    return repairs, counts


def apply(conn, repairs: list[dict]) -> dict:
    """Append each repair as a new `extract_rev`. Emits no job, touches no
    registry table. The caller owns the transaction.

    `playbook_slug`/`playbook_rev` are carried forward from the attempt being
    copied, never recomputed: the old fields were shaped by that playbook and
    invariant 2 needs the attribution to stay with them. `tier` likewise — the
    row summarises the ladder that produced the FIELDS, and the added key is a
    T0 read from a ladder that always begins at T0.
    """
    for r in repairs:
        tiers.write_extraction_attempt(
            conn, r["content_hash"], [r["tier"]], r["fields_new"],
            extract_rev=tiers.next_extract_rev(conn, r["content_hash"]),
            model_id=r["model_id"],
            playbook_slug=r["playbook_slug"], playbook_rev=r["playbook_rev"],
        )
    return {"appended": len(repairs)}


def render(repairs: list[dict], counts: dict, limit: int = 40) -> list[str]:
    """Report lines. Every outcome is printed, including the ones that produced
    no repair — an abstain is a finding, not a shrug."""
    out = []
    for r in repairs[:limit]:
        ev = r["fields_new"].get("stated_class")
        if r["kind"] == "corrected":
            new = f"{ev['value']} {ev['verbatim'][:38]!r}" if ev else "(retracted)"
            out.append(f"  {r['content_hash'][:12]}  rev{r['extract_rev']} -> "
                       f"{r['extract_rev'] + 1}  {r['old_value']} -> {new}")
        else:
            out.append(f"  {r['content_hash'][:12]}  rev{r['extract_rev']} -> "
                       f"{r['extract_rev'] + 1}  {ev['value']:4s} {ev['verbatim'][:44]!r}")
    if len(repairs) > limit:
        out.append(f"  ... and {len(repairs) - limit} more not shown")
    out.append("")
    out.append(f"scanned {counts['scanned']}: "
               + ", ".join(f"{k} {counts[k]}" for k in OUTCOMES))
    return out
