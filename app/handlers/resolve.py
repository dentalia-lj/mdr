"""resolve.group — S1.2 RESOLVE (spec: docs/specs/resolve.md, contract: PRD §2 /
handbook §2).

Manufacturer canonicalization (manufacturer_alias) + a stop-at-first-hit
resolution ladder assigns each new/changed item to an item_group and
materializes the member's mfr_ref (C1 — the REF-gate comparand). Deterministic
by default; the only AI is a T1 adjudication of the ambiguous name-family middle
band, and it stages a suggestion for human review — grouping is never
auto-applied on a name match (invariant 3). Emits discover.group for any group
still lacking a current production document.

Ladder: existing-link -> udi -> basic-udi-di -> name-family (pg_trgm candidate
generation in-DB, rapidfuzz token_set_ratio scoring). Deterministic rungs score
1.0; only name-family produces a middling score, so only it can land in the
staging band.

RESOLVE writes item_group / item_group_member / manufacturer_alias /
grouping_suggestion only — never document / item_document / evidence (invariant
1). The C4 manufacturer-binding link for a newly-resolved item is a GATE write
and is deferred (see `_apply_manufacturer_bindings`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from psycopg.types.json import Json
from rapidfuzz import fuzz

from app import queue
from app.config import load_config
from app.handlers import register

log = logging.getLogger("dentalia.handler.resolve")

# Discovery cycle for RESOLVE-originated discover.group jobs. Phase 1 has no
# SCHEDULER (S1.5) yet; a fixed "initial" cycle is enough because dedupe is
# active-scope only (invariant 8) — a completed discover never blocks a re-check,
# and a still-active one correctly blocks a duplicate.
_RESOLVE_CYCLE = 0


@dataclass(frozen=True)
class Match:
    group_id: int
    score: float           # 1.0 for deterministic rungs; rapidfuzz [0,1] for name-family
    basis: str             # existing-link | udi | basic-udi-di | name-family
    candidates: list = field(default_factory=list)   # populated by name-family only


# --- reads ------------------------------------------------------------------

def _get_item(conn, item_ref):
    row = conn.execute(
        "SELECT item_ref, name, manufacturer_raw, mfr_ref, md_flag, udi "
        "FROM item_mirror WHERE item_ref=%s",
        (item_ref,),
    ).fetchone()
    if row is None:
        # INGEST commits the mirror row before emitting resolve.group; absence is
        # a real fault, surfaced loudly (fail -> retry -> dead), never silent.
        raise LookupError(f"resolve.group: item_ref {item_ref!r} not in item_mirror")
    return row


def _alias_lookup(conn, raw):
    """Canonical manufacturer for a raw name. Miss -> insert raw as its own
    canonical and report it (a new alias needs eyeballing / seeding from the
    Phase-0 sweep). Returns (canonical, was_created)."""
    row = conn.execute(
        "SELECT canonical_name FROM manufacturer_alias WHERE raw_name=%s", (raw,)
    ).fetchone()
    if row:
        return row["canonical_name"], False
    conn.execute(
        "INSERT INTO manufacturer_alias (raw_name, canonical_name) VALUES (%s,%s) "
        "ON CONFLICT (raw_name) DO NOTHING",
        (raw, raw),
    )
    return raw, True


# --- resolution ladder (stop at first hit) ----------------------------------

def _existing_link(conn, item_ref):
    row = conn.execute(
        "SELECT group_id FROM item_group_member WHERE item_ref=%s "
        "ORDER BY group_id LIMIT 1",
        (item_ref,),
    ).fetchone()
    return Match(row["group_id"], 1.0, "existing-link") if row else None


def _by_udi(conn, item):
    """Catalogue-local exact UDI equality: another already-grouped item carries
    the same UDI-DI. (udi -> eudamed_mirror -> basic_udi_di resolution is the
    S2.3 upgrade path.)"""
    if not item["udi"]:
        return None
    row = conn.execute(
        "SELECT gm.group_id FROM item_group_member gm "
        "JOIN item_mirror m ON m.item_ref = gm.item_ref "
        "WHERE m.udi = %s AND m.item_ref <> %s "
        "ORDER BY gm.group_id LIMIT 1",
        (item["udi"], item["item_ref"]),
    ).fetchone()
    return Match(row["group_id"], 1.0, "udi") if row else None


def _by_basic_udi_di(conn, item):
    """The item's UDI equals a group's recorded Basic UDI-DI (the strongest group
    key when a group already carries one)."""
    if not item["udi"]:
        return None
    row = conn.execute(
        "SELECT group_id FROM item_group WHERE basic_udi_di = %s "
        "ORDER BY group_id LIMIT 1",
        (item["udi"],),
    ).fetchone()
    return Match(row["group_id"], 1.0, "basic-udi-di") if row else None


def _by_name_family(conn, item, canonical, k):
    """pg_trgm surfaces same-manufacturer candidates (GIN index, recall);
    rapidfuzz token_set_ratio scores them (precision). Group score = max over the
    group's surfaced members."""
    rows = conn.execute(
        """
        SELECT gm.group_id, m.name
        FROM item_group g
        JOIN item_group_member gm ON gm.group_id = g.group_id
        JOIN item_mirror m        ON m.item_ref  = gm.item_ref
        WHERE g.canonical_manufacturer = %s
          AND m.item_ref <> %s
          AND m.name %% %s
        ORDER BY similarity(m.name, %s) DESC
        LIMIT %s
        """,
        (canonical, item["item_ref"], item["name"], item["name"], k),
    ).fetchall()
    if not rows:
        return None
    best: dict[int, tuple[float, str]] = {}
    for r in rows:
        s = fuzz.token_set_ratio(item["name"], r["name"]) / 100.0
        cur = best.get(r["group_id"])
        if cur is None or s > cur[0]:
            best[r["group_id"]] = (s, r["name"])
    candidates = sorted(
        (
            {"group_id": gid, "score": round(sc, 4), "sample_name": nm}
            for gid, (sc, nm) in best.items()
        ),
        key=lambda c: c["score"],
        reverse=True,
    )
    top = candidates[0]
    return Match(top["group_id"], top["score"], "name-family", candidates)


# --- writes -----------------------------------------------------------------

def _ensure_member(conn, group_id, item_ref, mfr_ref, basis):
    """Materialize membership + mfr_ref (C1). Idempotent: on re-delivery the
    mfr_ref is refreshed (the mirror may have updated it) but the ORIGINAL
    match_basis is preserved — a re-resolve that now hits existing-link must not
    overwrite how the item first joined."""
    conn.execute(
        "INSERT INTO item_group_member (group_id, item_ref, mfr_ref, match_basis) "
        "VALUES (%s,%s,%s,%s) "
        "ON CONFLICT (group_id, item_ref) DO UPDATE SET mfr_ref = EXCLUDED.mfr_ref",
        (group_id, item_ref, mfr_ref, basis),
    )


def _create_singleton(conn, item, canonical, basis="singleton"):
    gid = conn.execute(
        "INSERT INTO item_group (canonical_manufacturer, label) VALUES (%s,%s) "
        "RETURNING group_id",
        (canonical, item["name"]),
    ).fetchone()["group_id"]
    _ensure_member(conn, gid, item["item_ref"], item["mfr_ref"], basis)
    return gid


def _push_grouping_suggestion(conn, item, candidates, suggestion, score):
    """Stage the ambiguous cluster for human review (PRD §2 staging path).
    Idempotent under at-least-once delivery via the open-item partial-unique
    index — a re-delivered resolve.group no-ops instead of stacking rows."""
    conn.execute(
        "INSERT INTO grouping_suggestion (item_ref, candidates, suggestion, score) "
        "VALUES (%s,%s,%s,%s) "
        "ON CONFLICT (item_ref) WHERE status='open' DO NOTHING",
        (item["item_ref"], Json(candidates),
         Json(suggestion) if suggestion is not None else None, score),
    )


def _resolve_open_suggestion(conn, item_ref, decided_by="system"):
    conn.execute(
        "UPDATE grouping_suggestion SET status='resolved', resolved_by=%s, resolved_at=now() "
        "WHERE item_ref=%s AND status='open'",
        (decided_by, item_ref),
    )


def _has_current_doc_or_candidate(conn, group_id):
    """PRD §4 (C3): a production doc/link OR a staged candidate awaiting review
    suppresses re-discovery — a group served only via the fetch-context path
    (staged forever, C5) must not re-trigger search every cycle. Rejected and
    superseded rows never suppress; gate-reject re-enqueues discovery itself."""
    return conn.execute(
        "SELECT 1 FROM document d "
        "JOIN item_document idoc ON idoc.doc_id = d.doc_id "
        " AND idoc.status IN ('staged','production') "
        "JOIN item_group_member gm ON gm.item_ref = idoc.item_ref "
        "WHERE gm.group_id = %s AND d.status IN ('staged','production') LIMIT 1",
        (group_id,),
    ).fetchone() is not None


def _record_discover(conn, group_id, result, *, hold):
    """Emit discover.group unless suppressed, recording WHICH reason in `result`.

    Two different situations produce `emitted_discover=False`, and the job report
    has to tell them apart (CLAUDE.md: nothing skipped is silent):

      * the group already has a current doc or candidate (PRD §4 / C3) — there is
        nothing left to discover, a settled decision;
      * `discover.hold` is on — the corpus-first cold start. Discovery is
        DEFERRED, not decided, and `discover_held` is what says so. See the
        Discovery config docstring for why the hold exists and how it unwinds
        (`cli regroup --apply` once it is dropped).
    """
    if _has_current_doc_or_candidate(conn, group_id):
        result["emitted_discover"] = False
        return
    if hold:
        result["emitted_discover"] = False
        result["discover_held"] = 1
        return
    queue.enqueue(
        conn, "discover.group", {"group_id": group_id},
        dedupe_key=f"discover:{group_id}:{_RESOLVE_CYCLE}",
    )
    result["emitted_discover"] = True


def _apply_manufacturer_bindings(conn, item, canonical):
    """C4 §7b hook — INERT in S1.2. The handbook says a newly-resolved item
    "receives the mfr-scope link at RESOLVE time", but invariant 1 forbids
    RESOLVE writing item_document, gate.candidate cannot take a link-only
    candidate, and there is no queryable manufacturer->binding association yet.
    The link write is a GATE responsibility, deferred to followup `resolve-c4`
    (mirrors S1.0 leaving C6 inert until a GATE write path lands). RESOLVE's
    contribution to C4 is the canonicalization above, which unblocks
    gate.apply(bind-manufacturer) switching to canonical (followup
    `gate-binding`). Returns the count of applicable bindings (0 today)."""
    return 0


def _default_adjudicator(cfg):
    """The live T1 ranking prompt/call is a followup (`resolve-t1-ranking`).
    Until it lands, an un-injected SUGGEST-band item still stages its candidate
    cluster for human review — the adjudicator failing is handled by the caller,
    which records `t1_error` and keeps the suggestion (work is never lost)."""
    def _adjudicate(item, candidates):
        raise NotImplementedError(
            "resolve.group live T1 ranking not wired (followup resolve-t1-ranking); "
            "inject `adjudicate` or review the staged candidates manually"
        )
    return _adjudicate


# --- entry ------------------------------------------------------------------

def handle_resolve_group(conn, job, *, adjudicate=None):
    # One load_config() per job: load_config re-reads env + TOML on every call
    # (it is not cached), and this handler needs two of its sections.
    full_cfg = load_config()
    cfg = full_cfg.resolve
    hold = full_cfg.discovery.hold
    payload = job["payload"]
    item = _get_item(conn, payload["item_ref"])

    # `discover_held` is pre-seeded like every other counter so the report shape
    # is stable whether or not the hold is on (same rule as ingest.py's counters).
    result = {
        "resolved": False, "basis": None, "group_id": None,
        "emitted_discover": False, "discover_held": 0, "aliases_created": 0,
        "missing_mfr_ref": 0, "grouping_suggestions": 0, "bindings_applicable": 0,
    }

    def _note_mfr(item):
        if item["mfr_ref"] is None:
            result["missing_mfr_ref"] = 1

    # Forced-assign — the grouping-suggestion resolution loop (spec §5). The
    # review UI enqueues resolve.group with an additive group_id; the human is
    # the authority, so basis is `manual` and the open suggestion is resolved.
    forced = payload.get("group_id")
    if forced is not None:
        if conn.execute(
            "SELECT 1 FROM item_group WHERE group_id=%s", (forced,)
        ).fetchone() is None:
            raise LookupError(f"resolve.group: forced group_id {forced} does not exist")
        _ensure_member(conn, forced, item["item_ref"], item["mfr_ref"], "manual")
        _resolve_open_suggestion(conn, item["item_ref"])
        _note_mfr(item)
        result.update(resolved=True, basis="manual", group_id=forced)
        _record_discover(conn, forced, result, hold=hold)
        return result

    # Forced start-new-group — the other half of the resolution loop (spec §5):
    # the review UI's "start new group" action on a grouping suggestion. Human
    # is the authority, same as the forced group_id branch, so basis is
    # `manual` too (not the automatic-fallback `singleton`).
    if payload.get("force_new_group"):
        canonical, created = _alias_lookup(conn, item["manufacturer_raw"])
        if created:
            result["aliases_created"] = 1
        gid = _create_singleton(conn, item, canonical, basis="manual")
        _resolve_open_suggestion(conn, item["item_ref"])
        _note_mfr(item)
        result.update(resolved=True, basis="manual", group_id=gid)
        _record_discover(conn, gid, result, hold=hold)
        return result

    canonical, created = _alias_lookup(conn, item["manufacturer_raw"])
    if created:
        result["aliases_created"] = 1

    match = (
        _existing_link(conn, item["item_ref"])
        or _by_udi(conn, item)
        or _by_basic_udi_di(conn, item)
        or _by_name_family(conn, item, canonical, cfg.name_candidate_k)
    )

    if match and match.score >= cfg.name_accept:                     # deterministic accept
        _ensure_member(conn, match.group_id, item["item_ref"], item["mfr_ref"], match.basis)
        # An open suggestion asks which group this item belongs to; placing it in
        # one answers that, so the row is stale review noise from here on.
        _resolve_open_suggestion(conn, item["item_ref"])
        result["bindings_applicable"] = _apply_manufacturer_bindings(conn, item, canonical)
        _note_mfr(item)
        result.update(resolved=True, basis=match.basis, group_id=match.group_id)
        _record_discover(conn, match.group_id, result, hold=hold)

    elif match and match.basis == "name-family" and match.score >= cfg.name_suggest:
        suggestion = None                                            # ambiguous middle -> T1 -> staging
        try:
            fn = adjudicate or _default_adjudicator(cfg)
            suggestion = fn(item, match.candidates)
        except Exception as exc:                                     # never lose the suggestion
            log.warning("resolve: T1 adjudication failed for %s: %s", item["item_ref"], exc)
            suggestion = {"t1_error": str(exc)}
        _push_grouping_suggestion(conn, item, match.candidates, suggestion, match.score)
        result["grouping_suggestions"] = 1

    else:                                                            # singleton fallback
        gid = _create_singleton(conn, item, canonical)
        _resolve_open_suggestion(conn, item["item_ref"])             # same reasoning as accept
        result["bindings_applicable"] = _apply_manufacturer_bindings(conn, item, canonical)
        _note_mfr(item)
        result.update(resolved=True, basis=None, group_id=gid)
        _record_discover(conn, gid, result, hold=hold)

    return result


register("resolve.group", handle_resolve_group)
