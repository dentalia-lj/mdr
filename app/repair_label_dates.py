"""One-off repair: replace the manufactured date verbatims with the real span.

Until 2026-09-03 `extract_dates` synthesised its verbatim as
`label + " " + date` -- a sentence the document may not contain. Invariant 2
requires every production value to carry a verbatim, and the whole point is
that a human can find the quote on the page. A manufactured one defeats that,
and it is what made doc 966's wrong expiry look trustworthy.

The 2026-09-03 change fixed the reader. It fixed nothing already stored:
measured 2026-09-04, **every** document whose current date evidence came from
T0 was extracted before that fix. Re-running the corrected reader over all of them
gives **0 value changes and 85 verbatim changes** -- 30 of them at production.
So this is a VERBATIM repair, not a date repair, which makes it a much smaller
thing than the spec anticipated: no value moves, no disposition should move,
and invariant 4 is not in play at all.

    doc 138  stores `Expiry Date 2030-04-16`; the page says `Expiry Date: 2030-04-16`
    doc  14  stores `Valid from 2021-05-25`;  the page says `Valid from Schaan, 2021-05-25`

Shape is `repair_ref_list`'s -- plan / render / apply, dry run by default --
with three deliberate departures:

  * **the source is the archived PDF, not `document_text`.**
    `repair_stated_class` reads the stored text and says so as a
    simplification, and copying that here would be wrong: `store_text` stores
    `full_text(page_markers=True)` while the live extractor reads
    `"\\n".join(page_texts(doc))`. The `[[page N]]` markers are not in the text
    `extract_dates` ever sees, so a span computed over them could contain one
    -- and writing `[[page 2]]` into a verbatim is the same defect this repair
    exists to remove.

  * **a value disagreement is reported and SKIPPED, never applied.** Zero
    today, so nothing is lost by refusing. A repair that silently rewrote a
    compliance date would be a much larger event than a quote correction, and
    it should be somebody's decision rather than a side effect of this one.

  * **only fields whose current evidence is T0.** A T1/T2 verbatim is the
    model's own quote of the page; re-deriving it from T0 would replace a
    genuine quote with a different one and misattribute the tier.

`validate.doc` IS re-emitted, unlike `repair_stated_class`. That one could skip
it because `stated_class` is in no VALIDATE rule and no GATE flag, so the value
could sit in `extraction_attempt` and be COALESCEd by a view. Evidence has no
such view: the `evidence` table is the record and is what the review screen
shows, so a corrected verbatim that never reaches it has repaired nothing.
Only GATE may write evidence (invariant 1), so the job is how the correction
gets there.

Append-only, matching migration 004's "every tier attempt kept": a repair lands
as a NEW `extract_rev` and never overwrites the original. The wrong quote stays
in the record at its own rev, which is correct -- it is what a reviewer saw.

Nothing is silent (CLAUDE.md): every scanned hash lands in exactly one of
`repaired` / `already_true` / `value_disagrees` / `t0_now_silent` / `unreadable`.

Dry run by default, like `repair-ref-list` and `repair-stated-class`:

    python -m app.cli repair-label-dates            # report only
    python -m app.cli repair-label-dates --apply
"""

from __future__ import annotations

from app import queue
from app.extract import pdf as pdfutil
from app.extract import t0_templates as t0
from app.extract import tiers
from app import playbooks as playbooks_mod

#: The two fields this touches. `extract_dates` produces exactly these.
DATE_FIELDS = ("validity_to", "validity_from")

#: Every scanned hash lands in exactly one of these.
OUTCOMES = ("repaired", "already_true", "value_disagrees", "t0_now_silent",
            "unreadable")


def _latest_attempts(conn) -> list[dict]:
    """One row per content_hash -- its highest `extract_rev` -- for every hash
    the registry holds a document for, with that document's `archive_url`.

    `JOIN document` is the scope and the source: an extraction whose document
    never reached the registry has no evidence rows to correct, and the
    archived path is what gets reopened.
    """
    return conn.execute(
        """
        SELECT DISTINCT ON (ea.content_hash)
               ea.content_hash, ea.extract_rev, ea.tier, ea.model_id, ea.fields,
               ea.playbook_slug, ea.playbook_rev,
               d.archive_url, d.doc_id, d.status
        FROM extraction_attempt ea
        JOIN document d ON d.content_hash = ea.content_hash
        ORDER BY ea.content_hash, ea.extract_rev DESC
        """
    ).fetchall()


def _t0_date_fields(fields: dict) -> list[str]:
    """Which date fields this attempt holds AS A T0 READ.

    The tier is per-field on the evidence dict, not per-attempt: a document
    whose expiry T0 read and whose start date a vision tier supplied has one of
    each in the same row.
    """
    out = []
    for name in DATE_FIELDS:
        ev = fields.get(name)
        if isinstance(ev, dict) and ev.get("tier") == "T0" and ev.get("value"):
            out.append(name)
    return out


def _reread(archive_url: str, playbooks) -> dict:
    """Exactly the live path, deliberately: open the archived bytes, join the
    page texts WITHOUT markers, resolve the steering playbook from the
    document's own text. Any shortcut here produces a verbatim the running
    extractor would never produce, which is a new fabrication, not a repair."""
    doc = pdfutil.open_doc(archive_url)
    try:
        full = "\n".join(pdfutil.page_texts(doc))
        mfr = t0.extract_manufacturer(full, playbooks)
        steering = t0.steering_playbook(full, playbooks, manufacturer_ev=mfr)
        return t0.extract_dates(full, steering)
    finally:
        doc.close()


def plan(conn, playbooks=None, reread=None) -> tuple[list[dict], dict, list[dict]]:
    """`(repairs, counts, skipped)`.

    Planning off the LATEST rev is what makes this re-runnable: once applied,
    the repair's own output is the incumbent and re-reads as `already_true`.
    """
    if playbooks is None:
        playbooks = playbooks_mod.load_playbooks()
    # Injectable for the same reason `repair_archive_urls` takes a `resolver`:
    # the default is the live path over a real archived PDF, and a test that
    # had to write PDFs to exercise the classification would be testing
    # PyMuPDF rather than this module's decisions.
    if reread is None:
        reread = _reread
    repairs: list[dict] = []
    skipped: list[dict] = []
    counts = {"scanned": 0, **{k: 0 for k in OUTCOMES}}

    for row in _latest_attempts(conn):
        old = row["fields"] or {}
        targets = _t0_date_fields(old)
        if not targets:
            continue                       # nothing here this repair may touch
        counts["scanned"] += 1
        try:
            fresh = reread(row["archive_url"], playbooks)
        except Exception as exc:           # noqa: BLE001 - a missing file is a finding
            counts["unreadable"] += 1
            skipped.append({**row, "reason": f"unreadable: {type(exc).__name__}: {exc}"})
            continue

        changed: dict = {}
        for name in targets:
            stored, now = old[name], fresh.get(name)
            if now is None:
                counts["t0_now_silent"] += 1
                skipped.append({**row, "reason":
                                f"{name}: T0 reads nothing today (stored "
                                f"{stored.get('value')!r} came from a rule that "
                                f"no longer pairs); left alone"})
                continue
            if now.get("value") != stored.get("value"):
                counts["value_disagrees"] += 1
                skipped.append({**row, "reason":
                                f"{name}: VALUE would change "
                                f"{stored.get('value')!r} -> {now.get('value')!r}; "
                                f"refused -- a date correction is not this tool's "
                                f"call"})
                continue
            if (now.get("verbatim") or "") == (stored.get("verbatim") or ""):
                counts["already_true"] += 1
                continue
            # The value and everything else about the evidence stand; only the
            # quote is replaced, so page/tier/confidence keep their meaning.
            changed[name] = {**stored, "verbatim": now["verbatim"]}

        if changed:
            counts["repaired"] += len(changed)
            repairs.append({**row, "fields_new": {**old, **changed},
                            "changed": changed})
    return repairs, counts, skipped


def apply(conn, repairs: list[dict]) -> dict:
    """Append each repair as a new `extract_rev` and re-point its `validate.doc`
    job at that rev. The caller owns the transaction.

    `playbook_slug`/`playbook_rev`/`tier`/`model_id` are carried forward from
    the attempt being copied, never recomputed: the old fields were shaped by
    that playbook and invariant 2 needs the attribution to stay with them.
    """
    deleted = emitted = 0
    for r in repairs:
        rev = tiers.next_extract_rev(conn, r["content_hash"])
        tiers.write_extraction_attempt(
            conn, r["content_hash"], [r["tier"]], r["fields_new"],
            extract_rev=rev, model_id=r["model_id"],
            playbook_slug=r["playbook_slug"], playbook_rev=r["playbook_rev"],
        )
        # Only jobs not yet started: a running job is mid-flight and a terminal
        # one is history. Payloads are immutable (invariant 9), so a stale
        # `validate.doc` pinned to the old rev is deleted and re-emitted rather
        # than edited.
        stale = conn.execute(
            "DELETE FROM job WHERE type='validate.doc' AND status='pending' "
            "AND payload->>'content_hash'=%s AND (payload->>'extract_rev')::int=%s "
            "RETURNING payload",
            (r["content_hash"], r["extract_rev"]),
        ).fetchall()
        deleted += len(stale)
        group_id = stale[0]["payload"].get("group_id") if stale else None
        # `archive_url` rides along, unlike `repair_ref_list`'s emission. We
        # have the stored one right here, and VALIDATE's fallback when the
        # payload has none is `fetch_log.url_normalized` -- the corpus SOURCE
        # path for a backfill, meaningful only on the scanning machine. That
        # fallback is what put a /imports/... URL on all 130 documents of the
        # GC pilot.
        if queue.enqueue(
            conn, "validate.doc",
            {"content_hash": r["content_hash"], "group_id": group_id,
             "extract_rev": rev, "archive_url": r["archive_url"]},
            dedupe_key=f"validate:{r['content_hash']}:{rev}:{group_id}",
        ) is not None:
            emitted += 1
    return {"appended": len(repairs), "jobs_deleted": deleted,
            "jobs_emitted": emitted}


def render(repairs: list[dict], counts: dict, skipped: list[dict],
           limit: int = 40) -> list[str]:
    """Report lines. Skips are printed, never silent (CLAUDE.md), and the
    before/after quotes are printed in full: the whole subject of this repair is
    what the quote says, so a truncated report would hide its own result."""
    out = []
    for r in repairs[:limit]:
        for name, ev in r["changed"].items():
            was = (r["fields"][name].get("verbatim") or "")
            out.append(f"  doc {r['doc_id']:<5} {r['status']:<10} rev"
                       f"{r['extract_rev']} -> {r['extract_rev'] + 1}  {name}")
            out.append(f"        was: {was!r}")
            out.append(f"        now: {ev['verbatim']!r}")
    if len(repairs) > limit:
        out.append(f"  ... and {len(repairs) - limit} more document(s) not shown")
    for s in skipped:
        out.append(f"  SKIPPED doc {s['doc_id']}  {s['reason']}")
    out.append("")
    out.append(f"scanned {counts['scanned']} document(s) holding a T0 date: "
               + ", ".join(f"{k} {counts[k]}" for k in OUTCOMES))
    out.append(f"{len(repairs)} document(s) would be re-revved.")
    return out
