"""discover.group — S1.3 DISCOVER (spec: docs/specs/discover.md, contract: PRD §3
/ handbook §3).

Walks a per-manufacturer source-priority ladder for a group that lacks a current
production document. First rung to produce candidate URLs emits `fetch.url` ×k
and stops; `email`/`manual` are terminal; `vendor` is a skip. `recency` is a
guard that can also C3-link (defect fix, 2026-08-24): when everything the group
knows is ledger-fresh and already extracted, it emits `validate.doc` — the same
re-entry FETCH's hash-dedupe branch performs — and is terminal; fresh-but-
unextracted stays a logged skip. Every rung tried writes a `discovery_log` row
(feeds hit-rate stats + the failure monitor).

Deterministic by default; the only AI is the T1 candidate ranking on the search
rung, and even there the fetch/no-fetch call is a threshold rule, not the LLM
(invariant 12). DISCOVER never fetches — it emits `fetch.url` and lets FETCH
(S1.4) own the network, the ledger, and the domain lease (invariant 6).

Two seams (invariant 11 + testability, same pattern as resolve/extract/gate):
the SearchAdapter and the ranker are injectable. The live T1 ranker landed
2026-09-02 (`app/extract/ranking.py`, `[discover-t1-ranking]`) — before that the
default raised and every search-path group degraded to `manual`. The degrade
path is unchanged and still carries any ranker failure: a missing API key or a
truncated response logs `detail.rank_error` and falls through to manual, so
wiring the ranker can cost a group its search rung but never the group.

Playbooks (S1.7) feed the ladder without being a rung of their own: the group's
manufacturer is looked up once (`playbooks.for_manufacturer`) and the result
shapes three things — the search query is `site:`-restricted to the playbook's
official `domains` (with one wide retry when the restricted query finds nothing,
because compliance PDFs often sit on a CDN or subsidiary domain), the manual
task is prefilled with the playbook's `portal` doc_sources, and both land in the
`discovery_log` detail. The ladder *rung* named `playbook` is **live**: it
fetches the direct `doc_sources` a playbook authors, and on 2026-08-24 it
produced this pipeline's first open-web document (843, off renfert.com).
S2.1's per-manufacturer **crawl recipe**
(docs/superpowers/specs/2026-08-21-playbook-crawl-recipe-design.md) is wired
into this same rung as of this slice — `_crawl_playbook` below, reached only
when a playbook authors a `crawl` block, which none of the 33 delivered
playbooks do yet (that is slice 5, so the branch is inert in production
today). A playbook authoring both `crawl` and a `direct` doc_source emits
BOTH -- direct first, crawl added to it (ruled Denis 2026-09-03, correcting a
literal reading of design §3 step 2 that made them exclusive).
`vendor` remains folded into `search` per G6 and is skipped if a config ladder
names it.

Phase-1 note: `eudamed_mirror` is empty until `eudamed.sync` (S2.3) and the
`manufacturer` contact table is unpopulated, so in Phase 1 those rungs
deterministically miss and most doc-less groups land in `manual` (or on the
known_url rung after a backfill). The email rung is additionally gated by
`discovery.email_rung_enabled` (default off): until the S2.4 EMAIL handler
exists, an emitted `email.request` would be swallowed by the no-op, so the rung
skips even when a contact is known.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import quote_plus

from psycopg.types.json import Json

from app import crawl as crawl_mod
from app import queue
from app import playbooks as playbooks_mod
from app import robots as robots_mod
from app.adapters.fetcher import make_fetcher
from app.adapters.search import SearchNotConfigured, make_search_adapter, SearchCandidate
from app.config import load_config
from app.extract import ranking
from app.handlers import archiving, register
from app.urls import domain_of, normalize_url

log = logging.getLogger("dentalia.handler.discover")


@dataclass(frozen=True)
class GroupFacts:
    group_id: int
    manufacturer: str          # item_group.canonical_manufacturer
    label: str | None
    basic_udi_di: str | None
    member_mfr_refs: list[str]


# --- group facts ------------------------------------------------------------

def _load_group(conn, group_id) -> GroupFacts:
    row = conn.execute(
        "SELECT group_id, canonical_manufacturer, label, basic_udi_di "
        "FROM item_group WHERE group_id=%s",
        (group_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"discover.group: group_id {group_id} not in item_group")
    refs = conn.execute(
        "SELECT mfr_ref FROM item_group_member "
        "WHERE group_id=%s AND mfr_ref IS NOT NULL ORDER BY mfr_ref",
        (group_id,),
    ).fetchall()
    return GroupFacts(
        group_id=row["group_id"],
        manufacturer=row["canonical_manufacturer"],
        label=row["label"],
        basic_udi_di=row["basic_udi_di"],
        member_mfr_refs=[r["mfr_ref"] for r in refs],
    )


def _known_url_rows(conn, group_id, recency_days) -> list[dict]:
    """URLs of prior fetches of any document linked to a group member, each
    tagged fresh/stale against the recency window (computed in-DB against now()
    to avoid worker-clock skew). Carries the ledger's content_hash so the
    recency rung can C3-link a fresh, already-extracted document without the
    network (defect fix, 2026-08-24)."""
    return conn.execute(
        "SELECT DISTINCT fl.url_normalized AS url, fl.content_hash, "
        "  (fl.last_checked_at IS NOT NULL "
        "   AND fl.last_checked_at >= now() - make_interval(days => %s)) AS is_fresh "
        "FROM item_group_member gm "
        "JOIN item_document idoc ON idoc.item_ref = gm.item_ref "
        "JOIN fetch_log fl ON fl.doc_id = idoc.doc_id "
        # Exclude web-upload rows: their url_normalized is a synthetic `upload:{hash}`
        # pseudo-key (source='upload'), not an http(s) URL FETCH can re-fetch. Letting
        # it into the known_url rung would emit a fetch.url that dead-letters on the
        # unsupported scheme and short-circuits the ladder on a false hit.
        "WHERE gm.group_id = %s AND fl.url_normalized IS NOT NULL "
        "AND fl.source <> 'upload'",
        (recency_days, group_id),
    ).fetchall()


# --- ladder -----------------------------------------------------------------

def _source_priority(cfg, manufacturer, playbook=None) -> list[str]:
    """Per-manufacturer ladder (data-driven, PRD §3). Playbook first -- the
    per-manufacturer file is the home for per-manufacturer data -- then the TOML
    `[source_priority]` map that predates playbooks and still serves anyone
    without one, then the Phase-1 default."""
    if playbook is not None and playbook.source_priority:
        return list(playbook.source_priority)
    return cfg.source_priority.get(manufacturer, cfg.discovery.default_source_priority)


def _ledgered_fresh_rows(conn, urls, recency_days) -> list[dict]:
    """Fetch-ledger rows for the given URLs that are fresh within the recency
    window and carry a content hash — the candidates the recency rung may
    C3-link without touching the network."""
    if not urls:
        return []
    return conn.execute(
        "SELECT url_normalized AS url, content_hash FROM fetch_log "
        "WHERE url_normalized = ANY(%s) AND content_hash IS NOT NULL "
        "AND last_checked_at IS NOT NULL "
        "AND last_checked_at >= now() - make_interval(days => %s)",
        ([normalize_url(u) for u in urls], recency_days),
    ).fetchall()


def _eudamed_lookup(conn, facts) -> list[str]:
    """Free deterministic EUDAMED-mirror lookup by Basic UDI-DI. Empty mirror in
    Phase 1 -> []. `cert_refs` jsonb is Phase-2-owned; tolerate both a list of
    URL strings and a list of {url} objects (additive)."""
    if not facts.basic_udi_di:
        return []
    row = conn.execute(
        "SELECT cert_refs FROM eudamed_mirror WHERE basic_udi_di=%s LIMIT 1",
        (facts.basic_udi_di,),
    ).fetchone()
    if not row or not row["cert_refs"]:
        return []
    out = []
    for ref in row["cert_refs"]:
        url = ref.get("url") if isinstance(ref, dict) else ref
        if isinstance(url, str) and url:
            out.append(url)
    return out


def _default_rank(facts, candidates):
    """Live T1 ranker (`[discover-t1-ranking]`, wired 2026-09-02).

    Built per call rather than per process, deliberately: `handle_discover_group`
    is the only caller, the search rung is reached at most once per job, and a
    module-level client would bind an API key read at import time -- which is
    what let a worker run 20 minutes on a placeholder key on 2026-09-02 without
    anything noticing.

    Every failure here (missing key, HTTP error, truncated response) is caught by
    `_search`, logged as a `discovery_log` search `miss` with `detail.rank_error`,
    and falls through the ladder to manual. That is the same degrade path the
    deferred `NotImplementedError` had, so wiring this can lose a group its
    search rung but never the group."""
    import anthropic

    cfg = load_config()
    ranker = ranking.AnthropicRanker(
        anthropic.Anthropic(api_key=cfg.connection.anthropic_api_key), cfg.models
    )
    return ranker(facts, candidates)


def _threshold_topk(ranked, cfg) -> list:
    """The fetch/no-fetch threshold rule (PRD §3): keep candidates scoring >=
    rank_threshold, best `topk` first."""
    keep = [(c, s) for c, s in ranked if s >= cfg.discovery.rank_threshold]
    keep.sort(key=lambda cs: cs[1], reverse=True)
    return [c for c, _ in keep[: cfg.discovery.topk]]


def _search(conn, facts, cfg, search_adapter, rank, playbook=None) -> tuple[list[str], dict]:
    adapter = search_adapter or make_search_adapter(cfg)
    ranker = rank or _default_rank
    q = _query_for(facts, playbook)
    candidates = adapter.query(q)   # infra error propagates (fail -> retry -> dead)
    detail = {"query": q, "n_candidates": len(candidates), "n_ranked": 0}

    # A site:-restricted query that finds nothing is not a dead end: compliance
    # PDFs frequently sit on a CDN or a subsidiary domain. Retry once, wide.
    if not candidates and playbook and playbook.domains:
        detail["restricted_miss"] = True
        q = _query_for(facts, None)
        candidates = adapter.query(q)
        detail["query_retry"] = q
        detail["n_candidates"] = len(candidates)

    try:
        ranked = ranker(facts, candidates)
    except Exception as exc:        # ranker (AI) failure never loses the group
        log.warning("discover: T1 ranking unavailable for group %s: %s", facts.group_id, exc)
        detail["rank_error"] = str(exc)
        return [], detail
    top = _threshold_topk(ranked, cfg)
    detail["n_ranked"] = len(top)
    return [c.url for c in top], detail


def _query_for(facts, playbook=None) -> str:
    """Search query for the SEARCH rung. With a playbook's official domains
    known, restrict to them: it sharply raises precision, which matters because
    the T1 ranker is the only thing standing between a candidate and a fetch.
    `_search` retries unrestricted on a miss, so the restriction can never be
    the reason a group finds nothing.

    `facts.label` is Dentalia's internal BC product description (often
    Slovene category prose, e.g. "ROKAVICE L NEPUD. PREMIUM 100KOS") and never
    appears verbatim on a manufacturer's site. Quoting it as a required exact
    phrase ([discover-query-exact-matches-slovene-label]) made the query
    unsatisfiable, so it is never quoted. When the label is nothing but a
    category noun plus generic descriptors it carries no distinguishing
    product name either, so `_carries_product_name` drops it from the query
    entirely rather than adding it as noise; a label with any other token
    (a brand family name or a catalogue code) is kept whole, unquoted."""
    label = f"{facts.label} " if _carries_product_name(facts.label) else ""
    base = f"{facts.manufacturer} {label}declaration of conformity pdf".strip()
    if playbook and playbook.domains:
        sites = " OR ".join(f"site:{d}" for d in playbook.domains)
        return f"{base} ({sites})"
    return base


# Leading Slovene category nouns measured against the live `item_group.label`
# distribution (8208 labels, 2026-09-02; followup
# [discover-query-exact-matches-slovene-label]): the most frequent leading
# tokens that name a device *category* rather than a specific product (e.g.
# SVEDER "drill", KLEŠČE "pliers", ROKAVICE "gloves"). Brand-ish leading
# tokens observed at similar frequency (E.MAX, MIYO, IVOCOLOR, PLANMECA,
# 3SHAPE, SONICFLEX, NEXCO) are deliberately excluded -- they already ARE the
# product name.
_CATEGORY_NOUNS = frozenset({
    "SVEDER", "KLEŠČE", "ŠČETKA", "KIRETA", "VOSEK", "PINCETA", "NASTAVEK",
    "NASTAVKI", "ROKAVICE", "DRŽALO", "RAZKUŽILO", "SET", "DVIGALO",
    "GUMICA", "MATRICE", "MATRICA", "IGLA", "SONDA", "LOPATICA",
    "INSTRUMENT", "ŠKARJE", "KOFFERDAM", "ŠIVALNI", "ŠIVALNIK", "POLIRNA",
    "SCALER", "TLAČILEC", "ŽLICA", "TRAK", "ROBČKI", "FOLIJA", "PRENOSNIK",
    "FREZA", "VREČKE", "KOLENČNIK", "BRIZGA", "ZAGOZDE", "MAVEC", "POSODA",
    "PILICA", "MASKA", "RASPATORIJ", "ZATIČKI", "NOŽ", "STOJALO", "SPRAY",
    "SESALCI", "SESALEC",
})

# Generic descriptors that carry no product-identifying signal on their own:
# sizes, colours, connectors, sterility/material words, marketing adjectives,
# shape/anatomy/direction words, and packaging terms -- curated from the same
# label distribution's remainder tokens after the leading category noun.
# `HS` is Henry Schein's own supplier-code suffix (48 labels, always trailing
# a generic description), redundant with `facts.manufacturer`.
_FILLER_WORDS = frozenset({
    "S", "M", "L", "XL", "XS", "MINI", "MALI", "MALA", "MAJHEN", "MAJHNA",
    "VELIK", "VELIKA", "SREDNJI", "SREDNJA", "SREDNJE",
    "MODER", "MODRA", "MODRI", "MODRO", "MODRE", "ROZA", "RDEČ", "RDEČA",
    "RDEČE", "RUMEN", "RUMENA", "RUMENI", "RUMENE", "ZELEN", "ZELENA",
    "ZELENI", "ZELENE", "ČRN", "ČRNA", "ČRNI", "ČRNE", "BEL", "BELA", "BELI",
    "BELE", "ORANŽNA", "VIJOLIČNA", "SIVA", "RJAVA", "ZLAT", "ZLATA",
    "ZA", "Z", "V", "S", "IN", "ALI", "FOR", "WITH", "PLUS", "+", "-", "/", "X",
    "STERILNI", "STERILNE", "STERILNA", "NESTERILNI", "NESTERILNE", "NEPUD",
    "LATEX", "NITRIL", "VINIL", "NYLON", "PVC", "KOVINSKI", "KOVINSKE",
    "LESENE", "KARBID", "TITAN", "PLASTIČNA", "SILIKON", "SILIKONSKI",
    "PREMIUM", "STANDARD", "SOFT", "PRO", "COMFORT", "MAX", "GOLD", "FLOW",
    "FIT", "XTRA", "MICRO", "LUX", "UNIVERZALNI", "UNIVERSAL", "NOVI",
    "DVOJNI", "TROJNI", "EXTRA", "SUPER", "CLASSIC", "BASIC",
    "RAVNA", "RAVNE", "RAVNO", "RAVEN", "UKRIVLJEN", "UKRIVLJENA",
    "UKRIVLJENE", "OKROGEL", "OKROGLA", "NAZOBČANA", "ANATOMSKA", "LEVO",
    "DESNO", "LEVA", "DESNA", "ZGORNJI", "SPODNJI", "GROBA", "GROB", "GROBE",
    "FINA", "FIN",
    "KOS", "PAR", "ŠT", "DB", "KIT", "SET",
    "MM", "CM", "ML", "KG", "G",
    "HS",
})

# A number+unit token (100KOS, 6,0MM, 500G, 10X, ...) is a quantity or a
# dimension, not a product identifier -- excluded the same way a bare filler
# word is. A bare number (558/6, 1131, T63) is NOT excluded: it is routinely
# the manufacturer's own catalogue/reference number, i.e. exactly the "model
# number" case that must be kept (measured: SVEDER T63 - 1,5 / 6,0 MM).
_UNIT_RE = re.compile(
    r"^\d+([.,]\d+)?(KOS|ML|L|G|KG|MM|CM|MG|PAR|X|MCG|DB|M)$", re.I
)


def _carries_product_name(label) -> bool:
    """True when `label` should be included in the search query: either its
    leading token is not a recognised category noun (it already names the
    product), or something beyond the leading noun and generic filler words
    remains -- a brand family name or a catalogue/model code."""
    if not label:
        return False
    tokens = label.split()
    first = tokens[0].strip(".,;:()").upper()
    if first not in _CATEGORY_NOUNS:
        return True
    for tok in tokens[1:]:
        t = tok.strip(".,;:()").upper()
        if not t or _UNIT_RE.match(t) or t in _FILLER_WORDS:
            continue
        return True
    return False


def _contact_known(conn, manufacturer) -> list[str]:
    row = conn.execute(
        "SELECT contact_emails FROM manufacturer WHERE canonical_name=%s",
        (manufacturer,),
    ).fetchone()
    return list(row["contact_emails"]) if row and row["contact_emails"] else []


# --- writes / emits ---------------------------------------------------------

def _log(conn, group_id, source, outcome, detail=None) -> None:
    conn.execute(
        "INSERT INTO discovery_log (group_id, source, outcome, detail) VALUES (%s,%s,%s,%s)",
        (group_id, source, outcome, Json(detail) if detail is not None else None),
    )


def _emit_fetch(conn, facts, urls, source_rank, ignore_recency=False) -> int:
    """One fetch.url per URL, deduped on the normalized URL so two groups (or a
    re-delivery) collapse to a single fetch (invariant 8). Returns the count
    actually enqueued (dedupe returns None).

    `ignore_recency` is added to the child payload only when true, so a
    default (missing-flag) run's payload is byte-identical to before this
    admin-refetch flag existed (additive-only, [admin-refetch-item])."""
    emitted = 0
    for url in urls:
        payload = {"url": url, "domain": domain_of(url), "group_id": facts.group_id,
                   "source_rank": source_rank}
        if ignore_recency:
            payload["ignore_recency"] = True
        jid = queue.enqueue(
            conn,
            "fetch.url",
            payload,
            dedupe_key=f"fetch:{normalize_url(url)}",
        )
        if jid is not None:
            emitted += 1
    return emitted


def _emit_email_request(conn, facts, contacts) -> None:
    # `group:` namespace — SCHEDULER's expiry scan keys on `email.request:doc:{id}`;
    # both producers share one flat dedupe keyspace, so each prefixes its id kind.
    queue.enqueue(
        conn,
        "email.request",
        {"group_id": facts.group_id, "manufacturer": facts.manufacturer,
         "contacts": contacts, "reason": "discovery-exhausted"},
        dedupe_key=f"email.request:group:{facts.group_id}",
    )


def _direct_urls(playbook=None) -> list[str]:
    """Authored `kind:"direct"` doc_sources -- URLs an operator has already
    confirmed point AT a document.

    `kind:"portal"` is excluded, and that exclusion is load-bearing twice over.
    A portal is a LISTING page, so fetching one archives HTML that no extractor
    can read (FETCH has no content-type gate: `app/handlers/fetch.py:156` takes
    any 200 body, and EXTRACT then counts `unreadable_pdf` and finishes `done`,
    so the dead end is silent). And for the four robots-refused manufacturers,
    `kind` IS the enforcement of Denis's 2026-08-20 ruling -- portal means a
    human clicks it and the fetcher structurally cannot reach it
    (`docs/2026-08-20-robots-blocked-manufacturers.md`). Turning a portal into
    documents is the crawl recipe (2026-08-21 design), not this rung."""
    if not playbook:
        return []
    return [s.url for s in playbook.doc_sources if s.kind == "direct"]


# --- playbook crawl recipe (S2.1) -------------------------------------------
#
# Design: docs/superpowers/specs/2026-08-21-playbook-crawl-recipe-design.md §3
# (the 8 ordered steps this function realizes as steps 3-8; steps 1-2 are the
# playbook lookup and the no-`crawl`-block fallback, both in the ladder loop
# below), §4.1 (robots), §4.3 (cap defences), §4.6 (never-silent counting),
# §4.7 (group attribution — carries the requesting group_id via `_emit_fetch`,
# never null), §4.8 (politeness starvation — fixed upstream in queue/runner;
# this rung is the first DISCOVER path that benefits from it by taking a
# domain lease at all). Reached only when `playbook.crawl` is authored, which
# none of the 33 delivered playbooks do yet — inert in production until
# slice 5 authors a real recipe.

def _excluded_by_type_rule(url: str, doc_type_from: dict | None) -> str | None:
    """The authored needle that EXCLUDES this link, or None to keep it.

    `doc_type_from` maps a casefolded URL substring to a `DOC_TYPES` member --
    or to `null`, which Denis ruled on 2026-09-03 means "harvested and counted,
    never typed as a compliance document … keeps NSK's 446 safety data sheets
    OUT OF THE REGISTRY … the files still appear in the crawl's counts"
    (`app/playbooks.py`). Counted by the harvest, never fetched: that is the
    only reading that satisfies both halves, and it is the one that does not
    spend an extraction budget on documents somebody explicitly ruled out.

    Until 2026-09-04 this key was read by the PROBE and by nothing else, so a
    recipe's exclusions changed the number on the operator's screen and
    nothing about what the pipeline did. On NSK that was 446 safety data sheets
    and ~$20 of extraction the screen said would not happen, against copy in
    three places promising otherwise -- `[crawl-doc-type-from-is-probe-only]`.

    The POSITIVE half is deliberately not acted on here. T0/T1 type a document
    from its own content, and a filename is a guess (`playbook_probe._classify`
    says so on screen); overriding a reading of the page with a substring of a
    URL would be the opposite of invariant 2. A positive rule is a probe hint,
    and that is all it has ever been.
    """
    if not doc_type_from:
        return None
    low = url.casefold()
    for needle, doc_type in doc_type_from.items():
        if doc_type is None and needle.casefold() in low:
            return needle
    return None


#: Fetch-order rank per predicted link type (ruled 2026-09-04): declarations
#: first, then notified-body certificates, then QMS certificates, then
#: instructions for use, then everything else. A hint for ORDER only.
TYPE_RANK = {"DoC": 0, "EC": 1, "ISO": 2, "IFU": 3, "other": 4}


def _norm_ref(ref: str) -> str:
    """Article numbers compare after casefolding and dropping whitespace --
    the same loose equality a person applies reading `196.644.050` off a
    link. Deliberately NOT the playbook `ref_normalize` rule: that is
    VALIDATE's C1 comparand and this is a fetch-order hint."""
    return "".join(ref.split()).casefold()


def _default_crawl_rank(manufacturer, candidates):
    """Live crawl triage, built per call for the same reason `_default_rank`
    is. Refuses up front without a key so a test or a keyless worker never
    constructs a client: `_rank_links` turns the refusal into page order."""
    cfg = load_config()
    if not cfg.connection.anthropic_api_key:
        raise RuntimeError("crawl rank: no anthropic api key configured")
    import anthropic

    ranker = ranking.CrawlLinkRanker(
        anthropic.Anthropic(api_key=cfg.connection.anthropic_api_key), cfg.models
    )
    return ranker(manufacturer, candidates)


def _harvest_hash(links, texts) -> str:
    """Identity of a library version: the resolved links and their texts in
    page order. Not the raw HTML -- that carries timestamps and session tokens
    and would miss on every read."""
    body = json.dumps([[u, texts.get(u, "")] for u in links], ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _judge_links(conn, index_url, facts, links, texts, ranker, batch):  # noqa: PLR0913
    """The model's answers for a library version, judged once and reused
    (migration 061). Returns `(judged_by_url, cached, batches)`. A cache hit
    costs one SELECT and no LLM call; a miss judges in batches and stores the
    result, so the next group crawling the same page -- NSK has 136 -- reads
    it back. `conn=None` (unit callers) skips the cache entirely."""
    key = _harvest_hash(links, texts)
    if conn is not None and index_url:
        row = conn.execute(
            "SELECT judgements FROM crawl_link_rank WHERE index_url=%s AND harvest_hash=%s",
            (index_url, key)).fetchone()
        if row is not None:
            judged = {j["url"]: ranking.LinkJudgement(
                j["url"], j.get("type", "other"), tuple(j.get("article_numbers") or ()),
                float(j.get("confidence") or 0.0), j.get("reason") or "")
                for j in row["judgements"]}
            return judged, True, 0
    cands = [SearchCandidate(url=u, title=texts.get(u, ""), snippet="", rank=i)
             for i, u in enumerate(links)]
    judged: dict[str, ranking.LinkJudgement] = {}
    batches = 0
    for start in range(0, len(cands), batch):
        batches += 1
        for j in ranker(facts.manufacturer, cands[start:start + batch]):
            judged[j.url] = j
    if conn is not None and index_url:
        conn.execute(
            "INSERT INTO crawl_link_rank (index_url, harvest_hash, model_id, judgements) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (index_url, key, getattr(ranker, "model_id", None),
             Json([{"url": j.url, "type": j.type, "article_numbers": list(j.article_numbers),
                    "confidence": j.confidence, "reason": j.reason}
                   for j in judged.values()])))
    return judged, False, batches


def _rank_links(facts, links, texts, budget, ranker, batch=ranking.CRAWL_BATCH, *,
                conn=None, index_url=None):
    """Spend a crawl budget by judgement instead of page order (ruled
    2026-09-04, `[crawl-ai-link-triage]`). The model says, per link, what kind
    of document it is and which article numbers it names -- once per library
    version (`_judge_links`); the ORDER is this function's rule and never the
    prompt's (invariant 12):

        1. a link naming one of the group's own article numbers, first
           ("item relation is utmost", Denis 2026-09-04) -- a weak signal,
           because a DoC covering 900 REFs names none of them in its anchor,
           so absence is neutral, never a penalty;
        2. then predicted type, DoC > EC > ISO > IFU > other;
        3. then the model's confidence, then page order.

    A ranking, never a filter: nothing is dropped for what it is, only for
    the budget. Under budget it decides the fetch order and nothing else.
    Returns `(kept, detail)`, kept best-first -- also the enqueue order.

    Any failure degrades to the pre-ruling behaviour, the first `budget`
    links in page order, and says so in the log."""
    try:
        judged, cached, batches = _judge_links(conn, index_url, facts, links, texts,
                                               ranker, batch)
    except Exception as exc:                    # noqa: BLE001 - degrade, never fail the job
        return list(links[:budget]), {
            "error": f"{type(exc).__name__}: {exc}", "fallback": "page-order",
            "kept": min(budget, len(links))}

    ours = {_norm_ref(r) for r in facts.member_mfr_refs}
    rows = []
    for i, url in enumerate(links):
        j = judged.get(url)
        kind = j.type if j is not None and j.type in TYPE_RANK else "other"
        conf = j.confidence if j is not None else 0.0
        match = bool(j) and any(_norm_ref(a) in ours for a in j.article_numbers)
        rows.append(((not match, TYPE_RANK[kind], -conf, i), url, kind, conf, match))
    rows.sort(key=lambda r: r[0])
    kept, dropped = rows[:budget], rows[budget:]
    by_type: dict[str, int] = {}
    for _, _, kind, _, _ in rows:
        by_type[kind] = by_type.get(kind, 0) + 1
    return [r[1] for r in kept], {
        "judged": len(links), "kept": len(kept), "batches": batches, "cached": cached,
        "by_type": by_type, "ref_matches": sum(1 for r in rows if r[4]),
        "kept_links": [[u, k, round(c, 2), m] for _, u, k, c, m in kept],
        "dropped_top": [[u, k, round(c, 2), m] for _, u, k, c, m in dropped[:20]],
    }


def _crawl_playbook(conn, job_id, facts, crawl, cfg, fetcher, ignore_recency,
                     renderer=None, already_leased=frozenset(),
                     crawl_rank=None) -> tuple[str, int | None]:
    """The crawl path of the `playbook` rung. Returns `(outcome, emitted)`:

    Never returns `"deferred"`: the domain lease is taken by the caller, once
    per host, before ANY recipe fetches (§4.8 and the 2026-09-03 re-fetch-loop
    fix). DISCOVER had never taken a domain lease before this rung; a crawl is
    the first thing that makes it a polite-rate HTTP client rather than an API
    caller.
    - `("miss", None)` — robots refused (§4.1), the index page did not fetch
      cleanly (a manufacturer's site being briefly down must not dead-letter
      the group — the same "never errors for an unauthored manufacturer"
      discipline design §3 step 2 states for the no-`crawl`-block case, here
      extended to a transient fault on an authored one), or the harvest
      matched zero links. The last case covers two DISTINCT signals, both
      logged so an author can tell them apart (§4.2): anchors present but
      none matching `link_pattern` ("your pattern is wrong", never escalated
      — rendering it would spend a ~30s `networkidle` wait to still find
      nothing), or zero anchors at all -- the actual signature of a listing
      built client-side, which now escalates to `renderer.render(url)` and
      re-harvests the rendered DOM (§4.2, ruled 2026-08-24). Every branch
      here is logged, including which tier (`static`/`rendered`) produced the
      links that were used; the ladder falls through on a miss either way.
    - `("hit", n)` — `n` `fetch.url` jobs enqueued (post-dedupe).
    """
    group_id = facts.group_id

    # 3. robots — refuse and LOG, never fetch (§4.1 layer 2; layer 1 is
    # `playbooks.validate()`'s authoring-time guard on `crawl.index_url`,
    # already enforced before a playbook naming a refused host could exist —
    # this call is what also protects a host nobody has audited yet). Same
    # `fetcher` as the index-page fetch below, per invariant 11: this rung
    # must not construct a second, differently-sourced transport.
    decision = robots_mod.check(conn, crawl.index_url, fetcher=fetcher)
    if not decision.allowed:
        _log(conn, group_id, "playbook", "skipped",
             {"reason": f"robots-{decision.reason}", "host": decision.host,
              "index_url": crawl.index_url})
        return "miss", None

    # 4. domain lease — exactly as FETCH acquires one (app/handlers/fetch.py
    # step 1), new for DISCOVER. A held lease defers the JOB rather than
    # logging a miss: nothing has been fetched yet, so there is nothing to
    # record beyond "try again shortly".
    # The lease was acquired by the CALLER, once for every host this
    # playbook's recipes name, BEFORE any of them fetched (see the rung).
    # Acquiring it here instead is what caused the 2026-09-03 re-fetch loop:
    # Edenta's two recipes share a host, so recipe 2 always found the lease
    # recipe 1 had just taken, deferred the whole job, and the retry re-ran
    # recipe 1 from the top -- 56 live fetches of one page before it was
    # stopped by hand. `already_leased` is the set of hosts the caller holds;
    # a host missing from it is a caller bug, not a runtime condition.
    domain = domain_of(crawl.index_url)
    assert domain in already_leased, (
        f"{domain} was not leased by the rung before _crawl_playbook ran")

    # 5-6. fetch (+ paginate if authored, §4.4) through the existing Fetcher,
    # then harvest each page with app.crawl.harvest. One page unless
    # `crawl.pagination` is set.
    paginator = None
    if crawl.pagination:
        paginator = crawl_mod.Paginator(
            crawl.index_url,
            param=crawl.pagination["param"],
            max_pages=crawl.pagination["max_pages"],
        )
    url = crawl.index_url
    pages_fetched = 0
    anchors_seen = links_matched = links_capped = off_host = 0
    ordered: list[str] = []
    seen: set[str] = set()
    texts: dict[str, str] = {}
    # Harvest up to the RANK CEILING, not the budget (ruled 2026-09-04): the
    # budget is spent by score below, and a ranker can only order what it was
    # shown. Under budget the two numbers are equal and nothing is ranked.
    ceiling = max(crawl.max_links, cfg.discovery.crawl_rank_max)
    # The `max_links` budget is spent ACROSS the whole crawl, not per page
    # (§4.3: the cap is a boundary against an over-broad `link_pattern`
    # harvesting a whole site, and pagination must not be a way around it).
    # Passing the remaining budget as each page's own `max_links` reuses
    # `harvest()`'s own capping rather than a second copy of that logic here.
    remaining = ceiling
    # Which tier actually produced the links used (§4.2, ruled 2026-08-21):
    # measured per attempt, not authored per manufacturer. Starts `"static"`
    # and flips to `"rendered"` the moment any page's zero-anchor escalation
    # SUCCEEDS (§4.2's "record which tier produced the links") -- it stays
    # `"rendered"` even if that render itself found nothing (still a real
    # attempt, still worth telling apart from "never tried" in the log), and
    # it stays `"static"` if a render raises, because the result actually
    # used in that case is the static (zero-anchor) one, not the browser's.
    tier = "static"

    while url is not None and remaining > 0:
        try:
            resp = fetcher.get(url)
        except Exception as exc:            # noqa: BLE001 - a fetch fault is a
            # miss for THIS rung, never a job failure (see the docstring).
            _log(conn, group_id, "playbook", "miss",
                 {"reason": "fetch-error", "url": url, "error": str(exc),
                  "pages_fetched": pages_fetched})
            return "miss", None
        if resp.status != 200 or resp.body is None:
            _log(conn, group_id, "playbook", "miss",
                 {"reason": "fetch-status", "url": url, "status": resp.status,
                  "pages_fetched": pages_fetched})
            return "miss", None
        pages_fetched += 1

        page = crawl_mod.harvest(
            resp.body.decode("utf-8", errors="replace"),
            base_url=url,
            link_pattern=crawl.link_pattern,
            same_host_only=crawl.same_host_only,
            allow_hosts=crawl.allow_hosts,
            max_links=remaining,
        )

        # Browser escalation (§4.2, ruled 2026-08-24). Trigger is ZERO
        # anchors on the page, never zero MATCHES -- `page.anchors_seen == 0`
        # only, so a page with anchors but a wrong `link_pattern` stays a
        # plain `links_matched == 0` miss and never touches the renderer. At
        # most one `renderer.render(url)` call per page: this `if` is only
        # reached once per iteration of the loop, so there is no path back
        # into it for the same `url`.
        if page.anchors_seen == 0 and renderer is not None:
            try:
                rendered_html = renderer.render(url)
            except Exception:                   # noqa: BLE001 - a render
                # fault (timeout, crash) degrades to the static, already-
                # computed zero-anchor `page` and a logged miss -- it must
                # NEVER dead-letter `discover.group` (§4.2). `tier` stays
                # "static": the result actually used is the static one.
                pass
            else:
                page = crawl_mod.harvest(
                    rendered_html,
                    base_url=url,
                    link_pattern=crawl.link_pattern,
                    same_host_only=crawl.same_host_only,
                    allow_hosts=crawl.allow_hosts,
                    max_links=remaining,
                )
                # The render was attempted and produced a usable (if empty)
                # result -- "rendered" even when it still finds nothing, so
                # the log can tell "we tried the browser, still nothing"
                # apart from "we never tried" (§4.2 requirement).
                tier = "rendered"

        anchors_seen += page.anchors_seen
        links_matched += page.links_matched
        links_capped += page.links_capped
        off_host += page.off_host
        for link in page.links:
            if link not in seen:
                seen.add(link)
                ordered.append(link)
                texts[link] = page.texts.get(link, "")
        remaining = ceiling - len(ordered)

        if paginator is None or remaining <= 0:
            break
        url = paginator.record(page.links)

    # 7. `links_capped` is the SUM of each page's own truncation (each
    # `harvest()` call above was capped at that page's remaining budget) —
    # a counted truncation, never a silent slice (§4.3/§4.6).
    harvested = ordered[:ceiling]
    at_ceiling = links_capped > 0     # the harvest itself hit the ceiling

    detail = {
        "index_url": crawl.index_url,
        # Design §4.6 names this "links_seen"; `anchors_seen` is the actual
        # field HarvestResult carries (app/crawl.py), used here verbatim
        # rather than introducing a second name for the same count.
        "anchors_seen": anchors_seen,
        "links_matched": links_matched,
        "links_capped": links_capped,
        "off_host": off_host,
        "pages_fetched": pages_fetched,
        # Which tier produced the links used -- the measurement §4.2 (ruled
        # 2026-08-21) asks for, so "this portal needs the browser" is read
        # off the log rather than authored by a human.
        "tier": tier,
    }
    if decision.crawl_delay_ms:
        detail["robots_delay_ms"] = decision.crawl_delay_ms

    if not harvested:
        _log(conn, group_id, "playbook", "miss", detail)
        return "miss", None

    # 7b. Authored exclusions. Counted here and dropped BEFORE `_emit_fetch`,
    # so an excluded family costs a line in the log and nothing else -- no
    # request to the manufacturer, no archive row, no extraction. The counts
    # above are untouched on purpose: `anchors_seen` / `links_matched` still
    # describe the library as it is, which is what "still appear in the crawl's
    # counts" means and what keeps a library from being silently
    # under-reported.
    excluded_by: dict[str, int] = {}
    keep: list[str] = []
    for link in harvested:
        needle = _excluded_by_type_rule(link, crawl.doc_type_from)
        if needle is None:
            keep.append(link)
        else:
            excluded_by[needle] = excluded_by.get(needle, 0) + 1
    if excluded_by:
        detail["excluded"] = len(harvested) - len(keep)
        # Per needle, not just a total: an author who wrote two exclusions
        # needs to see which one did the work, and a rule that matched NOTHING
        # never appears here -- that absence is the signal it is wrong.
        detail["excluded_by"] = excluded_by

    # 7b. the budget. Over it, rank the survivors by score (ruled 2026-09-04)
    # -- unless the harvest itself hit the ceiling, which means the pattern is
    # too broad to pay a ranker for; then page order, as before, and the log
    # tells the author to narrow it.
    budget = crawl.max_links
    over = max(0, len(keep) - budget)
    if at_ceiling:
        # The harvest itself hit the ceiling: the pattern is too broad to pay
        # a ranker for. Page order, as before the ruling, and the log tells
        # the author to narrow it.
        detail["rank"] = {"skipped": "pattern-too-broad", "ceiling": ceiling,
                          "survivors": len(keep)}
        keep = keep[:budget]
    elif len(keep) >= cfg.discovery.crawl_rank_floor:
        # A collection. Judge it and spend the budget in that order; under
        # budget this sets the fetch order and drops nothing.
        keep, detail["rank"] = _rank_links(facts, keep, texts, budget,
                                           crawl_rank or _default_crawl_rank,
                                           conn=conn, index_url=crawl.index_url)
    else:
        keep = keep[:budget]      # too few to choose between
    detail["links_capped"] = links_capped + over

    # 8. emit + log the full breakdown. `_emit_fetch` returns the count
    # actually enqueued (dedupe -> None), so `deduped` falls out of the two —
    # never a separate count that could drift from what really happened.
    n = _emit_fetch(conn, facts, keep, "playbook", ignore_recency)
    detail["emitted"] = n
    detail["deduped"] = len(keep) - n
    # A crawl whose every link was excluded is a HIT, not a miss: the recipe
    # worked, the library was read, and we chose not to fetch any of it.
    # Logging it as a miss would send the ladder on to search for documents we
    # have just decided we do not want.
    _log(conn, group_id, "playbook", "hit", detail)
    return "hit", n


def _prefilled_search_links(facts, playbook=None) -> list[str]:
    """Links shown on a discovery dead-end manual task. A known document portal
    goes first: it lands the operator on the manufacturer's own download centre
    instead of a bare web search. `direct` sources are excluded -- those are
    fetch targets for S2.1's crawler, not things a human needs to click."""
    terms = " ".join(filter(None, [facts.manufacturer, facts.label, "declaration of conformity"]))
    q = quote_plus(terms)
    portals = [s.url for s in playbook.doc_sources if s.kind == "portal"] if playbook else []
    return portals + [
        f"https://www.google.com/search?q={quote_plus(terms + ' pdf')}",
        f"https://www.google.com/search?q={q}+filetype%3Apdf",
        f"https://search.brave.com/search?q={quote_plus(terms + ' pdf')}",
    ]


def _push_manual(conn, facts, sources_tried, playbook=None) -> bool:
    """Discovery dead-end -> manual_task with prefilled search links. Guarded so
    a re-delivered job never stacks duplicate open tasks. Returns True if a new
    task was written."""
    existing = conn.execute(
        "SELECT 1 FROM manual_task WHERE kind='discovery-dead-end' "
        "AND group_id=%s AND status='open'",
        (facts.group_id,),
    ).fetchone()
    if existing is not None:
        return False
    payload = {
        "prefilled_search_links": _prefilled_search_links(facts, playbook),
        "manufacturer": facts.manufacturer,
        "label": facts.label,
        "sources_tried": sources_tried,
    }
    conn.execute(
        "INSERT INTO manual_task (kind, group_id, payload) "
        "VALUES ('discovery-dead-end', %s, %s)",
        (facts.group_id, Json(payload)),
    )
    return True


def _resolve_dead_end_task(conn, group_id) -> None:
    """A prior discover.group run for this group exhausted the ladder and left
    an open discovery-dead-end manual_task (migration 009). If THIS run reaches
    a terminal outcome other than manual — e.g. a human resolved the task by
    re-enqueueing discover.group and a rung now hits (new known_url, a fresh
    search result, a newly-added contact) — that old task is stale; resolve it
    so the manual-count KPI and the manual tab reflect reality. Never touches
    an outcome that lands on manual itself (that path leaves its own task open
    or creates a fresh one, per _push_manual)."""
    conn.execute(
        "UPDATE manual_task SET status='resolved', resolved_by='discover', resolved_at=now() "
        "WHERE kind='discovery-dead-end' AND group_id=%s AND status='open'",
        (group_id,),
    )


def _result(group_id, terminal_source, outcome, sources_tried, *,
            emitted_fetch=0, emitted_email=False, manual_task=False,
            candidates_seen=0, emitted_validate=0) -> dict:
    return {
        "group_id": group_id,
        "terminal_source": terminal_source,
        "outcome": outcome,
        "emitted_fetch": emitted_fetch,
        "emitted_email": emitted_email,
        "manual_task": manual_task,
        "sources_tried": sources_tried,
        "candidates_seen": candidates_seen,
        "emitted_validate": emitted_validate,
    }


# --- entry ------------------------------------------------------------------

def handle_discover_group(conn, job, *, search_adapter=None, rank=None, fetcher=None,
                           renderer=None, crawl_rank=None) -> dict:
    cfg = load_config()
    group_id = job["payload"]["group_id"]
    ignore_recency = bool(job["payload"].get("ignore_recency", False))
    facts = _load_group(conn, group_id)
    playbook = playbooks_mod.for_manufacturer(facts.manufacturer)
    ladder = _source_priority(cfg, facts.manufacturer, playbook)
    known = _known_url_rows(conn, group_id, cfg.discovery.recency_days)
    if ignore_recency:
        # Admin refetch ([admin-refetch-item]): force every known row stale so
        # the recency rung never logs `skipped` and the known_url rung's
        # freshness filter (`if not r["is_fresh"]`) keeps every row rather
        # than excluding the fresh ones. Single override point for both.
        known = [{**r, "is_fresh": False} for r in known]

    sources_tried: list[str] = []
    candidates_seen = 0

    for source in ladder:
        sources_tried.append(source)

        if source == "recency":
            # C3 re-entry without the network (defect fix, 2026-08-24): when
            # everything this group knows is ledger-fresh AND the content is
            # already extracted, emit the same validate.doc FETCH's dedupe
            # branch emits — mirrored via archiving.emit_validate, which also
            # attaches the registry's stored-copy handle. Before this the rung
            # only logged `skipped`, so a group whose document was first
            # fetched for another group ended its ladder with no link
            # candidate and no manual task (measured: RENFERT groups
            # 3138/3139/3140/5254 never linked to doc 843). Two sources feed
            # it: the known-url rows (all of them must be fresh, else the
            # ladder falls through so known_url can re-fetch the stale ones)
            # and the playbook's authored `direct` URLs, whose fetch.url the
            # playbook rung would otherwise dedupe away against another
            # group's in-flight fetch, orphaning this group. Fresh but
            # unextracted stays a guard skip — the in-flight extract chain
            # links it. `ignore_recency` (admin refetch) bypasses the whole
            # branch: a forced re-fetch must reach the network and dedupe
            # inside FETCH, never short-circuit here.
            if not ignore_recency and all(r["is_fresh"] for r in known):
                candidates = {r["content_hash"]: r["url"]
                              for r in known if r["content_hash"]}
                for row in _ledgered_fresh_rows(conn, _direct_urls(playbook),
                                                cfg.discovery.recency_days):
                    candidates.setdefault(row["content_hash"], row["url"])
                validated = []
                for h in candidates:
                    rev = archiving.hash_extracted_rev(conn, h)
                    if rev is not None:
                        archiving.emit_validate(conn, h, rev, group_id)
                        validated.append(h)
                if validated:
                    _log(conn, group_id, "recency", "hit",
                         {"recency_days": cfg.discovery.recency_days,
                          "content_hashes": validated,
                          "urls": [candidates[h] for h in validated]})
                    _resolve_dead_end_task(conn, group_id)
                    return _result(group_id, "recency", "validate", sources_tried,
                                   emitted_validate=len(validated),
                                   candidates_seen=candidates_seen)
            if known and all(r["is_fresh"] for r in known):
                _log(conn, group_id, "recency", "skipped",
                     {"recency_days": cfg.discovery.recency_days, "known": len(known)})
            continue

        if source == "known_url":
            urls = [r["url"] for r in known if not r["is_fresh"]]
            if urls:
                _log(conn, group_id, "known_url", "hit", {"urls": urls})
                n = _emit_fetch(conn, facts, urls, "known_url", ignore_recency)
                _resolve_dead_end_task(conn, group_id)
                return _result(group_id, "known_url", "fetch", sources_tried,
                               emitted_fetch=n, candidates_seen=candidates_seen)
            _log(conn, group_id, "known_url", "miss")
            continue

        if source == "eudamed":
            urls = _eudamed_lookup(conn, facts)
            if urls:
                _log(conn, group_id, "eudamed", "hit", {"urls": urls})
                n = _emit_fetch(conn, facts, urls, "eudamed", ignore_recency)
                _resolve_dead_end_task(conn, group_id)
                return _result(group_id, "eudamed", "fetch", sources_tried,
                               emitted_fetch=n, candidates_seen=candidates_seen)
            _log(conn, group_id, "eudamed", "miss")
            continue

        if source == "search":
            try:
                urls, detail = _search(conn, facts, cfg, search_adapter, rank, playbook)
            except SearchNotConfigured as exc:   # search not enabled -> skip, not dead-letter
                _log(conn, group_id, "search", "skipped", {"reason": str(exc)})
                continue
            candidates_seen = detail["n_candidates"]
            _log(conn, group_id, "search", "hit" if urls else "miss", detail)
            if urls:
                n = _emit_fetch(conn, facts, urls, "search", ignore_recency)
                _resolve_dead_end_task(conn, group_id)
                return _result(group_id, "search", "fetch", sources_tried,
                               emitted_fetch=n, candidates_seen=candidates_seen)
            continue

        if source == "email":
            if not cfg.discovery.email_rung_enabled:
                # No EMAIL handler until S2.4 — an emitted email.request would
                # land on the no-op and complete silently. Skip (logged), fall
                # through to manual: work is never lost, never swallowed.
                _log(conn, group_id, "email", "skipped", {"reason": "email-producer-disabled"})
                continue
            contacts = _contact_known(conn, facts.manufacturer)
            if contacts:
                _emit_email_request(conn, facts, contacts)
                _log(conn, group_id, "email", "hit", {"contacts": len(contacts)})
                _resolve_dead_end_task(conn, group_id)
                return _result(group_id, "email", "email", sources_tried,
                               emitted_email=True, candidates_seen=candidates_seen)
            _log(conn, group_id, "email", "miss")
            continue

        if source == "manual":
            wrote = _push_manual(conn, facts, sources_tried, playbook)
            # `handoff`, not `hit` (F3). Every other rung logs `hit` when it
            # FOUND something; this one logs that it gave up and opened a task.
            # Recorded as a hit, it made 258 groups read "Searched, found
            # something" on the very screens that exist to say the opposite,
            # and made the `EMPTY` state unreachable after a completed run.
            _log(conn, group_id, "manual", "handoff", {"new_task": wrote})
            return _result(group_id, "manual", "manual", sources_tried,
                           manual_task=wrote, candidates_seen=candidates_seen)

        if source == "playbook":
            # BOTH sources are emitted, not one or the other. A literal read of
            # design §3 step 2 ("if no crawl block -> fall through to the
            # authored-direct behaviour") makes them exclusive, and this rung
            # shipped that way for a few hours on 2026-09-03. Ruled by Denis the
            # same day: **a crawl block adds reach, it never removes any**. The
            # exclusive reading set a silent trap — CARL MARTIN, GC, NSK,
            # RENFERT and ULTRADENT all author `direct` PDFs a human has already
            # verified, and adding a crawl block to any of them would have taken
            # those declarations out of discovery without a word in any log.
            # A hand-verified document outranks a harvest by construction, so it
            # is emitted first and the crawl adds to it.
            emitted = 0
            hit = False
            # `_crawl_playbook` writes its OWN discovery_log row on every
            # outcome, hit or miss, carrying the §4.6 count breakdown. So the
            # bare fallback miss below must not fire as well, or a crawl that
            # found nothing logs twice — once with its counts and once empty —
            # and the empty row is the one that reads as the explanation.
            crawl_logged = False

            urls = _direct_urls(playbook)
            if urls:
                _log(conn, group_id, "playbook", "hit", {"urls": urls})
                emitted += _emit_fetch(conn, facts, urls, "playbook", ignore_recency)
                hit = True

            if playbook is not None and playbook.crawl:
                # One recipe per authored library, in order (ruled 2026-09-03).
                # Edenta is the worked case: instructions for use on one page,
                # EC/ISO certificates on another. Each recipe gets its own
                # robots check and its own domain lease, because each may name
                # a different host.
                #
                # Fetcher/renderer resolved lazily (mirrors app/handlers/
                # fetch.py) so the overwhelming majority of discover.group
                # calls, which never reach a crawl block, never construct
                # one. `PlaywrightFetcher()` itself does no I/O (invariant
                # 11: the crawl constructs it explicitly on escalation, the
                # way app/handlers/fetch.py:125-126 already does) -- only
                # `.render()` launches a browser, and only a zero-anchor page
                # ever calls it.
                live_fetcher = fetcher if fetcher is not None else make_fetcher(cfg)
                live_renderer = renderer
                if live_renderer is None:
                    from app.adapters.fetcher import PlaywrightFetcher
                    live_renderer = PlaywrightFetcher()
                # ONE lease per host, taken for the WHOLE crawl BEFORE any
                # recipe fetches. Ruled 2026-09-03 after a live run looped:
                # Edenta's two recipes share edenta.com, so acquiring per
                # recipe meant recipe 2 always found the lease recipe 1 had
                # just taken, deferred the whole job, and the retry re-ran
                # recipe 1 from the top -- 56 live fetches of one document
                # list before it was stopped by hand.
                #
                # Deferring HERE is safe in a way deferring mid-crawl is not:
                # nothing has been fetched yet, so a retry costs the
                # manufacturer nothing. That is exactly the property the loop
                # destroyed.
                hosts = {domain_of(r.index_url) for r in playbook.crawl}
                if not all(queue.try_domain_lease(conn, h, cfg.fetch.politeness_ms)
                           for h in sorted(hosts)):
                    queue.defer(conn, job["id"],
                                max(1, cfg.fetch.politeness_ms // 1000))
                    return {"group_id": group_id, "terminal_source": "playbook",
                            "outcome": "lease-wait", "_deferred": True}

                for i, recipe in enumerate(playbook.crawl):
                    # Space recipes on one host by the politeness interval.
                    # The lease bought the right to crawl this host, not the
                    # right to hit it as fast as the loop turns. FETCH gets
                    # this spacing free because each URL is its own job; a
                    # multi-recipe crawl is ONE job making several requests,
                    # so it has to pause itself.
                    if i and cfg.fetch.politeness_ms:
                        time.sleep(cfg.fetch.politeness_ms / 1000.0)
                    crawl_logged = True
                    outcome, n = _crawl_playbook(
                        conn, job["id"], facts, recipe, cfg, live_fetcher, ignore_recency,
                        renderer=live_renderer, already_leased=hosts,
                        crawl_rank=crawl_rank,
                    )
                    if outcome == "hit":
                        emitted += n
                        hit = True
                    # outcome == "miss" (robots refusal / fetch fault / zero
                    # matched links) is already logged inside _crawl_playbook,
                    # per recipe. It cancels neither a `direct` hit nor another
                    # recipe's hit.

            if hit:
                _resolve_dead_end_task(conn, group_id)
                return _result(group_id, "playbook", "fetch", sources_tried,
                               emitted_fetch=emitted, candidates_seen=candidates_seen)
            # Neither an authored direct source nor a crawl block produced
            # anything: the rung never errors for an unauthored manufacturer.
            # Skipped when the crawl already logged its own miss, so the counted
            # row stays the only explanation on record.
            if not crawl_logged:
                _log(conn, group_id, "playbook", "miss")
            continue

        if source == "vendor":                 # folded into search (G6)
            _log(conn, group_id, source, "skipped", {"deferred": True})
            continue

        raise ValueError(f"discover.group: unknown source rung {source!r}")

    # Ladder exhausted with no terminal rung (misconfigured priority list) — never
    # let a group vanish silently (CLAUDE.md: nothing skipped is silent).
    wrote = _push_manual(conn, facts, sources_tried, playbook)
    _log(conn, group_id, "manual", "handoff", {"new_task": wrote, "fallback": True})
    return _result(group_id, "manual", "manual", sources_tried,
                   manual_task=wrote, candidates_seen=candidates_seen)


register("discover.group", handle_discover_group)
