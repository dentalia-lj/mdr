"""Per-manufacturer playbook store (S1.7).

`playbooks/{slug}.json` is the single home for authored per-manufacturer
config: BC codes, aliases, official domains, document-source URLs, and (read
by `app.extract.t0_layout`, not here) the T0 parse template.

Two views exist over the same directory on purpose. This module owns the
identity/URL view and NEVER raises in the runtime path -- DISCOVER must not
dead-letter a group because someone fat-fingered a template. `validate()` is
the operator path (`dentalia playbooks validate|sync`) and raises loudly,
because a duplicate BC code silently mapping items to the wrong manufacturer
is exactly the failure this file exists to prevent.

`manufacturer` is the LEGAL manufacturer, because the DoC names the legal
entity and invariant 3's REF gate compares `canonical_manufacturer`. Brand and
product-line names belong in `aliases` (PANTHER is legally SUN
Oberflaechentechnik; MIYO is legally Chemichl AG, which Jensen only sells --
verified against Chemichl's own article list and Jensen's sales correspondence,
2026-08-20).
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import re
from dataclasses import dataclass, field

from urllib.parse import urlsplit

from app.extract.target import TARGET
from app.urls import domain_of

log = logging.getLogger(__name__)

# Repo-root `playbooks/`, per the CLAUDE.md target layout. Shipped into the
# worker image by an explicit Dockerfile COPY only, same as `migrations/` --
# it is NOT inside a package directory and is not part of the wheel build.
PLAYBOOKS_DIR = pathlib.Path(__file__).resolve().parent.parent / "playbooks"

DOC_SOURCE_KINDS = ("portal", "direct")

#: The document types T0 can produce (`_TYPE_MARKERS` in
#: `app/extract/t0_templates.py` maps to exactly these). Kept here rather than
#: imported from the extractor so the loader has no dependency on it -- the
#: parse view must stay importable without PyMuPDF.
DOC_TYPES = ("DoC", "EC", "IFU", "ISO")

#: Every rung `app/handlers/discover.py` can dispatch. Kept here, next to the
#: field that names them, rather than imported from the handler -- this module
#: must stay importable without the extractor/handler stack. `vendor` is folded
#: into `search` (G6) and logs `skipped`, but it is still a legal thing to
#: author, so it belongs in the vocabulary.
#: `extract_hints` size limits (Tier B). Anchored on `_HINT_OVERRIDE`, the
#: 263-character sentence in `app/handlers/extract.py` that tells the model to
#: ignore these notes where the document disagrees -- one hint may be about as
#: long as the sentence that neutralises it, never a wall of text under it.
#: Zero of the delivered playbooks use `extract_hints`, so the caps cost
#: nothing to adopt and everything they refuse is prospective.
HINT_MAX_CHARS = 300
HINT_MAX_TOTAL = 1500

#: `crawl.max_links` (playbook-crawl-recipe design §4.3, ruled 2026-08-21):
#: required in effect -- an unauthored value gets the default, but the field
#: is never "unlimited" -- and capped by a hard ceiling `_parse` enforces
#: regardless of what is authored. The spec names no number for the ceiling;
#: 2000 is a guard against an over-broad `link_pattern` harvesting a whole
#: site, not a value tuned against real crawl traffic.
CRAWL_MAX_LINKS_DEFAULT = 200
CRAWL_MAX_LINKS_CEILING = 2000

#: `crawl.pagination.max_pages` ceiling. Same reasoning as the two constants
#: above -- the spec does not name a number, so 50 is a guard, not a tuned
#: value. A real portal that needs more pages than this is worth a look by
#: hand, not a reason to raise the ceiling.
CRAWL_MAX_PAGES_CEILING = 50

DISCOVER_RUNGS = frozenset({
    "recency", "known_url", "playbook", "eudamed", "search", "email",
    "manual", "vendor",
})



from app import contacts as _contacts_mod

class PlaybookConflict(Exception):
    """Two playbooks claim the same BC code or the same manufacturer name."""


class BrandCollision(PlaybookConflict):
    """A name this playbook claims IS, or CONTAINS, another BC brand's name.

    The `[brand-parent-aliases]` failure, which reached live data on
    2026-08-10: `Maillefer Instruments Holding Sarl` and `Sirona Dental
    Systems GmbH` were authored as DENTSPLY aliases and synced. Both are their
    own BC brands -- MAILLEFER is code 022 (74 items), SIRONA is 012 (29) --
    so the aliases canonicalized 86 documents to DENTSPLY, scoped their group
    search to DENTSPLY's groups, and put a Maillefer certificate one GATE
    approval away from a Dentsply item. Wrong attribution is the costliest
    error this pipeline makes; failing to link is merely incomplete.

    Subclasses `PlaybookConflict` for the reason `RobotsRefused` does: every
    operator command that already reports an authoring conflict reports this
    one too, and the UI save maps it to the same 422.
    """


class RobotsRefused(PlaybookConflict):
    """A playbook names a fetch target on a host we are not permitted to fetch.

    Subclasses `PlaybookConflict` so the operator commands that already catch
    that (`dentalia playbooks validate|reconcile|sync`) report this too, without
    each growing a second except clause -- while the type stays precise enough
    for a caller that wants to tell an authoring mistake from a refusal.
    """


@dataclass(frozen=True)
class BcCode:
    """A BC manufacturer code (`Šifra proizvajalca`).

    One field, because there is one Business Central and one article numbering
    -- the Zagreb operation adds items, not a second integration (Denis
    2026-08-19 closing PHASES.md G17/G11, restated 2026-08-26). This carried a
    `catalogue` tag until then; see `_parse` for why the authored files still
    do.
    """

    code: str


@dataclass(frozen=True)
class DocSource:
    doc_type: str
    kind: str
    url: str
    note: str | None = None


@dataclass(frozen=True)
class Crawl:
    """A listing-page crawl recipe for the (not-yet-built) `playbook` DISCOVER
    rung.

    Design: `docs/superpowers/specs/2026-08-21-playbook-crawl-recipe-design.md`
    §3 for the shape, §4.3 for the over-broad `link_pattern` defences; all five
    open questions in §7 are ruled (2026-08-21, Denis). INERT as of this
    slice -- nothing reads `Playbook.crawl` yet, the rung that consumes it
    (§3 steps 3-8) is its own later slice. What ships here is this dataclass
    plus `_parse`'s shape checks and `validate()`'s robots-refused check, so a
    malformed or robots-refused recipe is caught at authoring time, before
    anything could ever fetch with it.
    """

    index_url: str
    # Matched against the RESOLVED ABSOLUTE url of each candidate link (§3),
    # never the raw href -- an authored `\.pdf$` only means something after a
    # relative-to-absolute join the rung performs, not here. Stored as a
    # string, the same choice `cert_number_pattern`/`ref_pattern` above make:
    # `_parse` checks it compiles and names the playbook and the regex error
    # if it does not, but a live `re.Pattern` is the rung's concern, not this
    # module's cache.
    link_pattern: str
    # Ruled 2026-08-21, §4.3/§7.2: default True, widened ONLY by
    # `allow_hosts` below -- never by flipping this off for "follow anything".
    same_host_only: bool = True
    # Extra hostnames a link may resolve to besides the index host. The ONLY
    # widening mechanism (§4.3): a CDN-hosted asset set (Acteon, SUN) gets
    # named here, not by disabling `same_host_only`. Normalized with
    # `_normalize_domain`, the same function `domains` above uses, so a
    # path-scoped entry behaves identically for both fields.
    allow_hosts: tuple[str, ...] = ()
    # Required in effect, not in the JSON: an unauthored key gets
    # `CRAWL_MAX_LINKS_DEFAULT`, so the field is never "no cap" -- and
    # whatever value IS authored is checked against `CRAWL_MAX_LINKS_CEILING`
    # in `_parse`, a hard ceiling rather than a suggestion.
    max_links: int = CRAWL_MAX_LINKS_DEFAULT
    # Casefolded substring of the resolved URL -> a `DOC_TYPES` member, e.g.
    # `{"ifu": "IFU", "konformit": "DoC"}`. Optional. Every VALUE must be a
    # real doc type OR `null` or `_parse` rejects the whole file -- the same
    # discipline `type_markers` above applies, for the same reason: a value
    # filed under an unknown type would silently type a crawled document as
    # something the registry has no member for.
    #
    # `null` means EXCLUDE: harvested and counted, never typed as a compliance
    # document (ruled Denis 2026-09-03). `{"sds_": null}` keeps NSK's 446
    # safety data sheets out of the registry without turning `link_pattern`
    # into a negative lookahead -- and, unlike a pattern exclusion, the files
    # still appear in the crawl's counts, so a library is never silently
    # under-reported.
    doc_type_from: dict | None = None
    # `{"param": str, "max_pages": int}`. Optional. The three §4.4 stop
    # conditions (max_pages, a page yielding zero new links, a page whose
    # link set repeats the previous page's) are the RUNG's job to enforce --
    # this module only checks the shape and the `max_pages` ceiling.
    pagination: dict | None = None


@dataclass(frozen=True)
class Playbook:
    slug: str
    manufacturer: str
    bc_codes: tuple[BcCode, ...] = ()
    aliases: tuple[str, ...] = ()
    # Official hostnames, each optionally carrying a path scope
    # (`straumann.com/medentika`). Consumed as `site:` tokens by DISCOVER's
    # search rung; see `_normalize_domain`.
    domains: tuple[str, ...] = ()
    doc_sources: tuple[DocSource, ...] = ()
    # Per-manufacturer REF *comparison* rule for VALIDATE's C1 gate (backfill-
    # matching plan, 2026-08-10, task 3) -- distinct from `ref_strategy`
    # above `match`, which is a PARSING strategy (how t0_layout finds the REF
    # list on the page). None means "no rule": every manufacturer without one
    # keeps today's exact-match comparison unchanged. `app/handlers/validate.py`
    # reads this via `for_manufacturer(group["canonical_manufacturer"])`.
    ref_normalize: dict | None = None
    # Which of a manufacturer's files are a document and which are an annex to
    # one. Komet issues a declaration (`..._RA_810_DoC_EU_SIGNED.pdf`) and its
    # product list as a SEPARATE file (`..._RA_812_Liste_DoC.pdf`), paired by a
    # leading number: 39 numbers in the 186-PDF corpus, every one carrying both,
    # zero orphans (measured 2026-08-18). Read by BACKFILL, which pairs the two
    # and declines to enqueue the annex as a document of its own.
    companion: dict | None = None
    # Hand-bumped revision. 0 = unversioned (every file authored before this key
    # existed). Recorded on `extraction_attempt` whenever this playbook steered a
    # read, so an extraction stays defensible after the file changes.
    rev: int = 0
    # Role mailboxes for renewal requests, most-preferred first. Seeded into
    # `manufacturer.contact_emails` by the seed importer, which is what
    # `email.request` reads -- so this is the AUTHORED source and the column
    # stays the single read path. Personal addresses are refused at authoring
    # time (Denis, 2026-09-03): a playbook is a durable artifact in git, a
    # named employee goes stale, and their address is personal data in a repo.
    contacts: tuple[str, ...] = ()
    # Additive date-label vocabulary for T0, {"from": [...], "to": [...]}.
    # MERGED with the global lists in `app/extract/t0_templates.py`, never
    # replacing them -- a replacement would silently lose every date the
    # generic path already reads.
    date_labels: dict = field(default_factory=dict)
    # Additive doc-type vocabulary for T0, {"DoC": [...], "EC": [...]}. Merged
    # AFTER the global markers in `app/extract/t0_templates.py`, so a global
    # phrase wins a tie at the same offset and an authored one can only add a
    # read. Keys are restricted to the four types the extractor can return: a
    # marker filed under an unknown name would type a document as something the
    # registry has no member for, at T0 confidence, silently.
    type_markers: dict = field(default_factory=dict)
    # Per-manufacturer DISCOVER ladder, most-preferred rung first. Supersedes
    # the TOML `[source_priority]` map, which app/config.py has always described
    # as a placeholder for exactly this. Empty tuple means "unauthored": config
    # first, then the Phase-1 default ladder.
    source_priority: tuple[str, ...] = ()
    # Extra certificate-number regex for T0, tried AFTER the global patterns.
    # Must compile and must carry exactly one capture group (the number itself);
    # both are checked at load, so a bad pattern never reaches a document.
    cert_number_pattern: str | None = None
    # Exact REF-code shape for this manufacturer, applied ON TOP of T0's generic
    # filter (`_looks_like_ref`) -- a narrowing only: a value the generic filter
    # already rejected stays rejected. Distinct from `ref_strategy_config.
    # field_pattern`, which selects a COLUMN for one parse strategy; this
    # validates a CODE for all of them.
    ref_pattern: str | None = None
    # Free-text, per-field guidance appended to the T1/T2 system prompt.
    # Keys are field names, or "general". This is the ONLY playbook value an
    # LLM ever sees; every other key drives deterministic code.
    extract_hints: dict = field(default_factory=dict)
    # Komet ships an index -- a spreadsheet and a Word table -- naming which
    # document covers which article. Read by BACKFILL (`_coverage_map_rule`,
    # backfill.py:321) to pick the canonical file and to seed stated coverage,
    # and by the one-shot `komet-coverage` CLI: 246 of 319 items, stated by the
    # manufacturer rather than parsed out of its PDFs (measured 2026-08-18).
    coverage_map: dict | None = None
    # Which files in this manufacturer's corpus folder are not documents at
    # all. Read by BACKFILL, which drops them BEFORE archiving -- unlike
    # `companion` and `coverage_map`, which archive and then decline to emit.
    # The difference is the point: an excluded file must never reach the
    # archive. Neodent's folder carries 28 of Dentalia's own invoices,
    # delivery notes and quotations naming its customers (UKC Ljubljana,
    # Dentalni center dr. Čelesnik); no other corpus folder has any, which is
    # why nothing filtered until 2026-08-19.
    #
    # Filename-only by design. Deciding from content means paying the
    # extraction to learn the document was an invoice, which is the cost being
    # avoided.
    exclude: dict | None = None
    # This manufacturer's corpus dump is not fit to ingest at all, and
    # `backfill.scan` refuses it outright. Distinct from `exclude`, which drops
    # named files from a dump that is otherwise sound: this says the whole
    # folder is untrusted, so there is no file in it worth archiving.
    #
    # Neodent is the case it was written for. Its folder mixed 28 of Dentalia's
    # own invoices in with the documents; asked about it, the client answered
    # that the directory was a mess and to skip it (2026-08-19). A refusal
    # rather than a convention, because "we agreed not to scan that one" is not
    # a thing a re-run can know, and re-ingesting a disowned dump is silent.
    skip_backfill: dict | None = None
    # Listing-page crawl recipes for the `playbook` DISCOVER rung, in authored
    # order. A TUPLE, not one recipe: a manufacturer routinely publishes to
    # more than one library, and 12 of the 38 delivered playbooks already
    # author more than one `portal`. Edenta is the worked case -- instructions
    # for use on one page, EC/ISO certificates on another, different
    # `doc_type` each, both needed (measured live 2026-09-03: 13 and 3
    # documents). Ruled by Denis 2026-09-03, before any recipe was authored
    # against the singular shape the 2026-08-21 design sketched.
    #
    # `validate()` refuses any recipe naming a robots-refused `index_url`
    # host, the same authoring-time guard `doc_sources[kind:"direct"]` gets,
    # so an operator cannot author the mistake.
    crawl: tuple[Crawl, ...] = ()

    def names(self) -> tuple[str, ...]:
        """Every string that should resolve to this playbook."""
        return (self.manufacturer,) + self.aliases


def _normalize_domain(raw: str) -> str:
    """Hostname, plus an authored PATH SCOPE when one is given.

    Accepts a full URL (`https://voco.dental/`) or a bare hostname
    (`voco.dental`); a schemeless string needs `//` prepended first so
    `urlsplit` parses it as a netloc, not a path (`domain_of("voco.dental")`
    would otherwise return ""). The host half reuses `app.urls.domain_of`, the
    same normalizer FETCH/DISCOVER use, rather than a second one.

    A path is kept because a manufacturer's documents sometimes live under a
    path on a host another manufacturer owns: Medentika's moved to
    `straumann.com/medentika`, and `straumann.com` is the straumann playbook's
    own domain, so scoping by host would hand Medentika searches the whole
    Straumann site and let two playbooks claim one host (nothing in `validate()`
    detects that -- it checks BC codes and manufacturer names, not domains).
    Until 2026-08-21 an authored path was dropped here in silence, so the
    obvious authoring fix produced exactly the outcome it was meant to avoid.

    A bare `/` is not a path scope. The value is consumed as a `site:` token by
    `discover._query_for` and displayed by the playbooks page; nothing parses it
    further, and it deliberately never reaches `domain_of`, which keys the
    domain lease and half the fetch dedupe key."""
    raw = raw.strip()
    url = raw if "://" in raw else f"//{raw}"
    host = domain_of(url)
    path = urlsplit(url).path.rstrip("/")
    return f"{host}{path}" if path else host


def _parse(slug: str, data: dict) -> Playbook:
    manufacturer = data["manufacturer"]
    if not isinstance(manufacturer, str) or not manufacturer.strip():
        raise ValueError("manufacturer must be a non-empty string")

    codes = []
    for entry in data.get("bc_codes", []):
        # A `catalogue` key is accepted and DISCARDED. All 33 authored
        # playbooks carry one and are deliberately not being rewritten (Denis,
        # 2026-08-26); there is one catalogue, so the tag distinguishes
        # nothing. Do not restore it as a field.
        codes.append(BcCode(code=str(entry["code"])))

    sources = []
    for entry in data.get("doc_sources", []):
        kind = str(entry.get("kind", "portal"))
        if kind not in DOC_SOURCE_KINDS:
            raise ValueError(f"unknown doc_source kind {kind!r}")
        sources.append(
            DocSource(
                doc_type=str(entry["doc_type"]),
                kind=kind,
                url=str(entry["url"]),
                note=entry.get("note"),
            )
        )

    ref_normalize = data.get("ref_normalize")
    if ref_normalize is not None and not isinstance(ref_normalize, dict):
        raise ValueError("ref_normalize must be an object")

    companion = data.get("companion")
    if companion is not None:
        if not isinstance(companion, dict):
            raise ValueError("companion must be an object")
        missing = {"key", "primary", "annex"} - set(companion)
        if missing:
            raise ValueError(f"companion needs {sorted(missing)}")
        for name in ("key", "primary", "annex"):
            try:
                compiled = re.compile(companion[name])
            except re.error as exc:
                raise ValueError(f"companion.{name} is not a valid regex: {exc}") from exc
            # An authoring slip that costs coverage silently: a `key` with no
            # capture group pairs nothing and every annex is simply dropped.
            if name == "key" and compiled.groups != 1:
                raise ValueError("companion.key needs exactly one capture group")

    rev = data.get("rev", 0)
    if not isinstance(rev, int) or isinstance(rev, bool) or rev < 0:
        raise ValueError("rev must be a non-negative integer")

    date_labels = data.get("date_labels") or {}
    if not isinstance(date_labels, dict):
        raise ValueError("date_labels must be an object")
    for key, vals in date_labels.items():
        if key not in ("from", "to"):
            raise ValueError(f"date_labels key must be 'from' or 'to', not {key!r}")
        if not isinstance(vals, list) or not all(isinstance(v, str) for v in vals):
            raise ValueError(f"date_labels[{key!r}] must be a list of strings")

    source_priority = tuple(str(s) for s in data.get("source_priority", []))
    # Closed vocabulary, checked HERE rather than only at dispatch. An unknown
    # rung raises `ValueError: discover.group: unknown source rung` inside the
    # handler (discover.py:517), which fails the job and, after retries, dead-
    # letters DISCOVER for every group of that manufacturer. A typo in an
    # authored ladder is an authoring error and belongs at parse time, where
    # the CLI reports it and a UI save is refused at 422 -- same reasoning as
    # the `extract_hints` key allowlist below.
    unknown_rungs = sorted(set(source_priority) - DISCOVER_RUNGS)
    if unknown_rungs:
        raise ValueError(
            f"source_priority names unknown rung(s) {unknown_rungs}; "
            f"known rungs are {sorted(DISCOVER_RUNGS)}")

    type_markers = data.get("type_markers") or {}
    if not isinstance(type_markers, dict):
        raise ValueError("type_markers must be an object")
    for key, vals in type_markers.items():
        if key not in DOC_TYPES:
            raise ValueError(
                f"type_markers key must be one of {sorted(DOC_TYPES)}, not {key!r}")
        if not isinstance(vals, list) or not all(isinstance(v, str) for v in vals):
            raise ValueError(f"type_markers[{key!r}] must be a list of strings")

    cert_pattern = data.get("cert_number_pattern")
    if cert_pattern is not None:
        if not isinstance(cert_pattern, str):
            raise ValueError("cert_number_pattern must be a string")
        # Named, the way `companion.key` names itself. A bare `re.error`
        # reaching an operator says "unterminated character set at position 2"
        # and not WHICH of the playbook's several patterns is unterminated --
        # which matters more now that a save is refused at 422 on this message.
        try:
            compiled = re.compile(cert_pattern)
        except re.error as exc:
            raise ValueError(
                f"cert_number_pattern is not a valid regex: {exc}") from exc
        if compiled.groups != 1:
            raise ValueError(
                f"cert_number_pattern must have exactly one capture group, "
                f"has {compiled.groups}"
            )

    ref_pattern = data.get("ref_pattern")
    if ref_pattern is not None:
        if not isinstance(ref_pattern, str):
            raise ValueError("ref_pattern must be a string")
        try:
            re.compile(ref_pattern)
        except re.error as exc:
            raise ValueError(f"ref_pattern is not a valid regex: {exc}") from exc

    extract_hints = data.get("extract_hints")
    if extract_hints is None:
        extract_hints = {}
    elif not isinstance(extract_hints, dict):
        # Checked BEFORE any falsy-default substitution: `"extract_hints": []`
        # is falsy too, and `... or {}` would have silently turned it into an
        # empty (and therefore valid-looking) dict, skipping this check
        # entirely -- the one playbook key an LLM reads is not the place for a
        # type error to fail open.
        raise ValueError("extract_hints must be an object")
    if not all(isinstance(v, str) for v in extract_hints.values()):
        raise ValueError("every extract_hints value must be a string")
    # Keys are constrained to the fields the extractor is actually asked for,
    # plus "general". Until 2026-08-26 ANY key was accepted, so a typo
    # (`validity_form`) produced a hint for a field the model was never asked
    # about: silently ignored guidance that reads, in the file, as applied.
    # This is a correctness fix, not only a guardrail for the UI editor -- it
    # belongs here rather than in the route, so the CLI and the seed refuse it
    # too. `TARGET` comes from `app.extract.target`, a leaf module that imports
    # nothing, and is a plain module-level import above. It was a LAZY import
    # of `app.extract.tiers` until 2026-08-27, to keep DISCOVER off PyMuPDF --
    # but laziness only moved the failure, it did not prevent it: the import
    # ran on every `_parse`, so in the slim web image (no `.[extract]`) every
    # playbook row raised and `/playbooks` reported all 38 as unparseable.
    unknown = sorted(set(extract_hints) - set(TARGET) - {"general"})
    if unknown:
        raise ValueError(
            f"extract_hints keys must be extraction fields or 'general'; "
            f"unknown: {unknown}")
    # Length caps, Tier B (slice 3b task 14). Not a style rule: this block is
    # a SECOND system block on every T1/T2 call for the manufacturer, and it
    # is made safe by one sentence -- `extract.py::_HINT_OVERRIDE`, 263 chars,
    # telling the model these notes are context and to ignore them where the
    # document disagrees. A hint that dwarfs that sentence drowns it, which is
    # the shape of a prompt injection whether or not one was intended; and it
    # is billed on every extraction for that manufacturer forever. So one
    # value may be about as long as the override and no longer, and the whole
    # block stays a note rather than a document.
    long_values = sorted(k for k, v in extract_hints.items()
                         if len(v) > HINT_MAX_CHARS)
    if long_values:
        raise ValueError(
            f"extract_hints values are capped at {HINT_MAX_CHARS} characters "
            f"(the safety preamble they sit under is 263); too long: "
            f"{long_values}")
    total = sum(len(v) for v in extract_hints.values())
    if total > HINT_MAX_TOTAL:
        raise ValueError(
            f"extract_hints total {total} characters exceeds the "
            f"{HINT_MAX_TOTAL}-character cap for one playbook")
    coverage_map = data.get("coverage_map")
    if coverage_map is not None:
        if not isinstance(coverage_map, dict):
            raise ValueError("coverage_map must be an object")
        absent = {"sources", "key_resolves", "canonical"} - set(coverage_map)
        if absent:
            raise ValueError(f"coverage_map needs {sorted(absent)}")
        if not coverage_map["sources"]:
            raise ValueError("coverage_map.sources must not be empty")
        for src in coverage_map["sources"]:
            if not isinstance(src, dict):
                raise ValueError("coverage_map source must be an object")
            if not src.get("file"):
                raise ValueError("coverage_map source needs a file")
            missing_cols = {"article", "reference", "key"} - set(src.get("columns") or {})
            if missing_cols:
                raise ValueError(f"coverage_map source columns need {sorted(missing_cols)}")
        canonical = coverage_map["canonical"]
        if not isinstance(canonical, dict):
            raise ValueError("coverage_map.canonical must be an object")
        # `canonical.key` is the pairing regex and it is deliberately NOT
        # borrowed from `companion`: a manufacturer may split documents without
        # shipping an index, or ship an index without splitting documents.
        if not canonical.get("key"):
            raise ValueError("coverage_map.canonical needs a key regex")
        try:
            if re.compile(canonical["key"]).groups != 1:
                raise ValueError("coverage_map.canonical.key needs exactly one capture group")
        except re.error as exc:
            raise ValueError(f"coverage_map.canonical.key is not a valid regex: {exc}") from exc

    exclude = data.get("exclude")
    if exclude is not None:
        if not isinstance(exclude, dict):
            raise ValueError("exclude must be an object")
        # `reason` is required so an exclusion can never be a bare pattern
        # nobody can account for later. A rule that drops compliance documents
        # is the most expensive authoring slip available here, and the only
        # defence against it is that a human can read why each pattern exists.
        if not str(exclude.get("reason") or "").strip():
            raise ValueError("exclude needs a non-empty reason")
        patterns = exclude.get("filenames")
        if not isinstance(patterns, list) or not patterns:
            raise ValueError("exclude.filenames must be a non-empty list")
        for pattern in patterns:
            if not isinstance(pattern, str) or not pattern.strip():
                raise ValueError("exclude.filenames entries must be non-empty strings")
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(
                    f"exclude.filenames entry {pattern!r} is not a valid regex: {exc}"
                ) from exc

    skip_backfill = data.get("skip_backfill")
    if skip_backfill is not None:
        if not isinstance(skip_backfill, dict):
            raise ValueError("skip_backfill must be an object")
        # Same rule as `exclude`: a refusal nobody can account for later is
        # worse than no refusal, because the symptom is a manufacturer that
        # simply never files anything and no error anyone thinks to look for.
        if not str(skip_backfill.get("reason") or "").strip():
            raise ValueError("skip_backfill needs a non-empty reason")

    # `crawl`: playbook-crawl-recipe design §3/§4.3/§4.4, all ruled 2026-08-21.
    # INERT -- nothing reads `Playbook.crawl` yet -- but every rule below is
    # already enforced, so a malformed recipe cannot be authored ahead of the
    # rung that will one day consume it. Messages name the playbook (`slug`)
    # per the design's rule 2, unlike most other blocks in this function,
    # because an operator editing several manufacturers' crawl recipes at once
    # needs to know WHICH one failed without cross-referencing the traceback.
    # `crawl` is a LIST of recipes (ruled 2026-09-03). One shape, not two: a
    # bare object is refused rather than silently wrapped, because nothing
    # authors this key yet and a single unambiguous shape is worth more than
    # the ergonomics of accepting both.
    crawl_list = data.get("crawl")
    crawl: list[Crawl] = []
    if crawl_list is not None and not isinstance(crawl_list, list):
        raise ValueError(
            f"{slug!r} crawl must be a list of recipe objects, even when there "
            f"is only one")
    for crawl_data in (crawl_list or []):
        if not isinstance(crawl_data, dict):
            raise ValueError(f"{slug!r} crawl entries must be objects")

        index_url = crawl_data.get("index_url")
        if not isinstance(index_url, str) or not index_url.strip():
            raise ValueError(
                f"{slug!r} crawl.index_url is required and must be a "
                f"non-empty string")
        index_url = index_url.strip()
        parsed_index = urlsplit(index_url)
        if parsed_index.scheme not in ("http", "https") or not parsed_index.netloc:
            raise ValueError(
                f"{slug!r} crawl.index_url must be an absolute http(s) URL, "
                f"got {index_url!r}")

        link_pattern = crawl_data.get("link_pattern")
        if not isinstance(link_pattern, str) or not link_pattern:
            raise ValueError(
                f"{slug!r} crawl.link_pattern is required and must be a "
                f"non-empty string")
        try:
            re.compile(link_pattern)
        except re.error as exc:
            raise ValueError(
                f"{slug!r} crawl.link_pattern is not a valid regex: {exc}"
            ) from exc

        same_host_only = crawl_data.get("same_host_only", True)
        if not isinstance(same_host_only, bool):
            raise ValueError(f"{slug!r} crawl.same_host_only must be a boolean")

        allow_hosts_raw = crawl_data.get("allow_hosts", [])
        if not isinstance(allow_hosts_raw, list):
            raise ValueError(f"{slug!r} crawl.allow_hosts must be a list")
        allow_hosts = tuple(_normalize_domain(str(h)) for h in allow_hosts_raw)

        # REQUIRED in effect (design rule 5): absent authors the default, but
        # whatever value is present -- authored or defaulted -- is checked
        # against the hard ceiling. Not merely a suggestion: an authoring
        # mistake here is exactly the over-broad-harvest failure §4.3 exists
        # to prevent.
        max_links = crawl_data.get("max_links", CRAWL_MAX_LINKS_DEFAULT)
        if (not isinstance(max_links, int) or isinstance(max_links, bool)
                or max_links < 1):
            raise ValueError(
                f"{slug!r} crawl.max_links must be a positive integer")
        if max_links > CRAWL_MAX_LINKS_CEILING:
            raise ValueError(
                f"{slug!r} crawl.max_links={max_links} exceeds the hard "
                f"ceiling of {CRAWL_MAX_LINKS_CEILING}")

        doc_type_from = crawl_data.get("doc_type_from")
        if doc_type_from is not None:
            if not isinstance(doc_type_from, dict):
                raise ValueError(f"{slug!r} crawl.doc_type_from must be an object")
            for key, val in doc_type_from.items():
                if not isinstance(key, str) or not key:
                    raise ValueError(
                        f"{slug!r} crawl.doc_type_from keys must be "
                        f"non-empty strings")
                # `null` is a real, ruled value, not a missing one: it means
                # "harvest this link but never type it as a compliance
                # document" -- the exclusion mechanism for a library that
                # mixes safety data sheets in with declarations (NSK publishes
                # 446 SDS beside 160 DoCs). Ruled by Denis 2026-09-03 off the
                # wizard mockup, where the alternative was contorting
                # `link_pattern` into a negative lookahead to keep them out.
                # Excluding by TYPE rather than by URL keeps the counts honest:
                # the file is still seen and still counted, it is simply not
                # claimed as evidence.
                if val is not None and val not in DOC_TYPES:
                    raise ValueError(
                        f"{slug!r} crawl.doc_type_from[{key!r}] must be one "
                        f"of {sorted(DOC_TYPES)} or null, not {val!r}")

        pagination = crawl_data.get("pagination")
        if pagination is not None:
            if not isinstance(pagination, dict):
                raise ValueError(f"{slug!r} crawl.pagination must be an object")
            missing = {"param", "max_pages"} - set(pagination)
            if missing:
                raise ValueError(
                    f"{slug!r} crawl.pagination needs {sorted(missing)}")
            param = pagination["param"]
            if not isinstance(param, str) or not param.strip():
                raise ValueError(
                    f"{slug!r} crawl.pagination.param must be a non-empty "
                    f"string")
            max_pages = pagination["max_pages"]
            if (not isinstance(max_pages, int) or isinstance(max_pages, bool)
                    or max_pages < 1):
                raise ValueError(
                    f"{slug!r} crawl.pagination.max_pages must be a "
                    f"positive integer")
            if max_pages > CRAWL_MAX_PAGES_CEILING:
                raise ValueError(
                    f"{slug!r} crawl.pagination.max_pages={max_pages} "
                    f"exceeds the hard ceiling of {CRAWL_MAX_PAGES_CEILING}")

        crawl.append(Crawl(
            index_url=index_url,
            link_pattern=link_pattern,
            same_host_only=same_host_only,
            allow_hosts=allow_hosts,
            max_links=max_links,
            doc_type_from=doc_type_from,
            pagination=pagination,
        ))

    contacts = _contacts_mod.validate_for_playbook(
        data.get("contacts", []), where=f"{slug!r} contacts")

    return Playbook(
        slug=slug,
        manufacturer=manufacturer.strip(),
        contacts=tuple(contacts),
        bc_codes=tuple(codes),
        aliases=tuple(str(a) for a in data.get("aliases", [])),
        domains=tuple(_normalize_domain(str(d)) for d in data.get("domains", [])),
        doc_sources=tuple(sources),
        ref_normalize=ref_normalize,
        companion=companion,
        rev=rev,
        date_labels=date_labels,
        type_markers=type_markers,
        source_priority=source_priority,
        cert_number_pattern=cert_pattern,
        ref_pattern=ref_pattern,
        extract_hints=extract_hints,
        coverage_map=coverage_map,
        exclude=exclude,
        skip_backfill=skip_backfill,
        crawl=tuple(crawl),
    )


#: Parse cache, keyed on the resolved directory. The value's first element is a
#: signature of the directory's *.json entries; a mismatch reparses. Keyed on
#: the directory rather than held as one module global because the web viewer
#: (`WEB_PLAYBOOKS_DIR`) and the pipeline read different directories in the same
#: process under test.
_CACHE: dict[pathlib.Path, tuple[frozenset, tuple["Playbook", ...]]] = {}


def _dir_signature(dir_path: pathlib.Path) -> frozenset:
    """Cheap change-detector: one scandir, no reads, no JSON. Name + mtime_ns +
    size together catch every edit the authoring flow produces (the directory is
    bind-mounted :ro and hand-edited, so an in-place rewrite to the identical
    size within the same nanosecond is not a case that occurs)."""
    out = set()
    with os.scandir(dir_path) as it:
        for entry in it:
            if entry.name.endswith(".json") and entry.is_file():
                st = entry.stat()
                out.add((entry.name, st.st_mtime_ns, st.st_size))
    return frozenset(out)


#: Set once per process by `set_source`. A zero-argument callable returning a
#: context-managed psycopg connection. `None` means "read the directory", which
#: is what every test that passes an explicit `dir_path` relies on.
_SOURCE = None

#: `(epoch, playbooks, {slug: raw}, present_slugs)`. Separate from `_CACHE` because it is keyed on the
#: database's epoch counter rather than a directory signature, and because
#: `clear_cache()` has to drop both.
_DB_CACHE: tuple[int, tuple["Playbook", ...], dict[str, dict], set[str]] | None = None


def set_source(conn_factory) -> None:
    """Read playbooks from the database instead of the directory.

    Called once at process start (worker, web, CLI). `conn_factory` is invoked
    per load and its connection closed immediately -- workers never WRITE
    playbooks, so this never needs to join the caller's transaction, which is
    what keeps `load_playbooks()` callable from anywhere without threading a
    connection through DISCOVER, VALIDATE, EXTRACT and BACKFILL.

    Pass `None` to go back to the directory (tests, and the seed, which must
    read files or it would import its own output)."""
    global _SOURCE, _DB_CACHE
    _SOURCE = conn_factory
    _DB_CACHE = None


#: One round trip. The identity half is joined back on rather than stored in
#: `body`, because migration 049 shredded it precisely so the constraints could
#: police it -- see `_row_to_raw`.
_DB_QUERY = """
SELECT m.slug,
       m.canonical_name,
       m.body,
       m.playbook_rev,
       (SELECT array_agg(c.code ORDER BY c.code)
          FROM manufacturer_bc_code c
         WHERE c.manufacturer_id = m.id AND c.source = 'playbook') AS codes,
       (SELECT array_agg(n.name ORDER BY n.name_folded)
          FROM manufacturer_name n
         WHERE n.manufacturer_id = m.id AND n.kind = 'alias') AS aliases
  FROM manufacturer m
 WHERE m.slug IS NOT NULL AND m.body IS NOT NULL
 ORDER BY m.slug
"""


def _row_to_raw(row) -> dict:
    """A database row back into the shape `_parse` already reads.

    Deliberately NOT a second parser. `_parse` carries every validation rule
    this subsystem has -- the regex compiles, the `companion.key` capture-group
    check, the doc_source vocabulary -- and a from-row path that reimplemented
    any of it would drift. Reassembling the file's dict and handing it to the
    same function makes DB-and-file parity structural rather than tested.

    `bc_codes` and `aliases` come back sorted, not in authored order. Both are
    lookup sets (`names()` folds them; `for_manufacturer` compares folded), so
    order carries no meaning -- and preserving it would mean an ordinal column
    on `manufacturer_name` for no reader."""
    raw = dict(row["body"] or {})
    raw["manufacturer"] = row["canonical_name"]
    raw["bc_codes"] = [{"code": c} for c in (row["codes"] or ())]
    raw["aliases"] = list(row["aliases"] or ())
    raw["rev"] = row["playbook_rev"]
    return raw


def _with_source(fn):
    """Run `fn(conn)` on a fresh connection, retrying once.

    The retry is for a connection recycled under us (a restarted Postgres, a
    dropped idle connection), not for a broken query -- a second attempt at a
    genuine error just costs a second round trip before the same raise."""
    last: Exception | None = None
    for _ in range(2):
        try:
            with _SOURCE() as conn:
                return fn(conn)
        except Exception as exc:          # noqa: BLE001 -- re-raised below
            last = exc
    raise last                            # type: ignore[misc]


def _read_epoch() -> int:
    """The staleness probe on its own -- one indexed read of a single-row
    table.

    Separate from `_read_rows` because that is the entire point of having an
    epoch. Fetching the rows and THEN checking the counter would re-read every
    playbook on every cache hit, which in VALIDATE's per-group REF gate is a
    full table read per group -- exactly the cost the counter exists to avoid.
    """
    return _with_source(
        lambda c: c.execute("SELECT epoch FROM playbook_epoch").fetchone()["epoch"])


def _read_rows() -> list:
    """Every playbook row. Only called on a cache miss."""
    return _with_source(lambda c: c.execute(_DB_QUERY).fetchall())


def _db_state() -> tuple[tuple["Playbook", ...], dict[str, dict], set[str]]:
    """`(playbooks, {slug: raw}, present_slugs)` for the current epoch.

    All three views come off ONE read. `t0_layout.load_templates` needs the raw
    dicts (`match` and `ref_strategy` are not `Playbook` fields) and is called
    per document by EXTRACT; `slugs()` needs what EXISTS including rows that
    did not parse, and the web index calls it beside `load_playbooks` on the
    same page. Deriving either from its own query would put a second round trip
    where there is no second question.
    """
    global _DB_CACHE

    # A source failure RAISES rather than returning (). Returning empty would
    # strip every manufacturer's rules at once and look like "nothing is
    # authored": BACKFILL alone would then archive Neodent's 28 Dentalia
    # invoices, because `pb is None` disables both `skip_backfill` and
    # `exclude`. Nothing caches on this path either -- a cached () would make
    # one blip persist until the next epoch change.
    epoch = _read_epoch()

    if _DB_CACHE is not None and _DB_CACHE[0] == epoch:
        return _DB_CACHE[1], _DB_CACHE[2], _DB_CACHE[3]

    rows = _read_rows()
    out, raw, present = [], {}, set()
    for row in rows:
        present.add(row["slug"])
        # Per-ROW failure is the other half of the split: one malformed body is
        # logged and skipped, exactly as one malformed file is, so a typo in
        # one playbook cannot take DISCOVER down for the other 32.
        try:
            as_raw = _row_to_raw(row)
            out.append(_parse(row["slug"], as_raw))
            raw[row["slug"]] = as_raw
        except Exception:
            log.warning("playbooks: skipping malformed playbook row %s",
                        row["slug"], exc_info=True)
    loaded = tuple(out)
    _DB_CACHE = (epoch, loaded, raw, present)
    return loaded, raw, present


def _load_from_db() -> tuple["Playbook", ...]:
    return _db_state()[0]


def clear_cache() -> None:
    """Drop the parse caches, both shapes. For tests that repoint
    `PLAYBOOKS_DIR` or swap the source; the runtime never needs it -- a file
    edit is detected by signature and a row edit by the epoch trigger."""
    global _DB_CACHE
    _CACHE.clear()
    _DB_CACHE = None


def load_playbooks(dir_path: pathlib.Path | str | None = None) -> tuple[Playbook, ...]:
    """Every readable playbook, sorted by slug. Never raises: a malformed file
    is logged and skipped, so one bad file cannot take DISCOVER down. Use
    `validate()` when the caller is an operator who wants to be told.

    Two backing stores, one signature. With a source set (`set_source`, once
    per process) and no explicit `dir_path`, this reads rows; otherwise it
    reads the directory. An explicit `dir_path` always wins, which is what lets
    the seed import files into the database it would otherwise be reading, and
    what leaves every test that builds a directory of fixtures unchanged.

    Cached either way, and invalidated by a name/mtime/size signature or by the
    database's epoch counter, so a hot loop (VALIDATE's per-group REF gate)
    pays one scandir or one indexed single-row read instead of N parses, while
    a live edit still lands on the next job."""
    if dir_path is None and _SOURCE is not None:
        return _load_from_db()

    dir_path = pathlib.Path(dir_path if dir_path is not None else PLAYBOOKS_DIR)
    if not dir_path.is_dir():
        log.warning("playbooks: directory %s does not exist", dir_path)
        return ()

    key = dir_path.resolve()
    sig = _dir_signature(key)
    cached = _CACHE.get(key)
    if cached is not None and cached[0] == sig:
        return cached[1]

    out = []
    for f in sorted(dir_path.glob("*.json")):
        try:
            out.append(_parse(f.stem, json.loads(f.read_text())))
        except Exception:
            log.warning("playbooks: skipping malformed playbook %s", f, exc_info=True)
    loaded = tuple(out)
    _CACHE[key] = (sig, loaded)
    return loaded


def load_raw(dir_path: pathlib.Path | str | None = None) -> dict[str, dict]:
    """`{slug: the file's JSON, untouched}`. Uncached and unparsed.

    `load_playbooks` returns `Playbook`s, and a `Playbook` is LOSSY: `_parse`
    reads the sixteen keys it knows and drops the rest. The three T0 parse keys
    (`match`, `ref_strategy`, `ref_strategy_config`) are not `Playbook` fields
    at all -- `app/extract/t0_layout.py` is a second loader that reads them
    straight off the file. So a body built from a `Playbook` would silently drop
    the parse template of the six playbooks that carry one.

    Follows the same two-store rule as `load_playbooks`: with a source set and
    no explicit `dir_path` this returns rows, otherwise files. `t0_layout`
    reads it for the parse template; the seed passes an explicit directory
    precisely so it keeps reading files and cannot import its own output.

    Never raises, for the same reason `load_playbooks` does not: one malformed
    file must not stop the other 32."""
    if dir_path is None and _SOURCE is not None:
        return _db_state()[1]

    dir_path = pathlib.Path(dir_path if dir_path is not None else PLAYBOOKS_DIR)
    if not dir_path.is_dir():
        log.warning("playbooks: directory %s does not exist", dir_path)
        return {}

    out: dict[str, dict] = {}
    for f in sorted(dir_path.glob("*.json")):
        try:
            out[f.stem] = json.loads(f.read_text())
        except Exception:
            log.warning("playbooks: skipping malformed playbook %s", f, exc_info=True)
    return out


def reads_files(dir_path: pathlib.Path | str | None = None) -> bool:
    """True when this call would read the directory rather than the database.

    Callers need it to phrase a store-level failure: "directory not found" is
    a real and reachable condition for files (Dockerfile.web copies only
    `app/` and `web/`, so `/app/playbooks` exists solely because of the compose
    bind mount) and is meaningless for rows, where an unreachable store raises
    instead."""
    return dir_path is not None or _SOURCE is None


def slugs(dir_path: pathlib.Path | str | None = None) -> set[str]:
    """Every playbook PRESENT in the store, whether or not it parses.

    The difference between this and `{pb.slug for pb in load_playbooks()}` is
    exactly the broken set. `load_playbooks` and `load_raw` both drop what they
    cannot read -- by contract, so DISCOVER never dead-letters over an
    authoring typo -- which means neither can tell anyone something was
    dropped. The web index owes the operator that count (CLAUDE.md: skipped
    rows are counted and reported, never silent), and this is what makes it
    answerable without globbing, so it keeps working once the store is rows.

    Follows the same two-store rule: an explicit `dir_path` reads that
    directory.
    """
    if dir_path is None and _SOURCE is not None:
        return _db_state()[2]

    dir_path = pathlib.Path(dir_path if dir_path is not None else PLAYBOOKS_DIR)
    if not dir_path.is_dir():
        return set()
    return {f.stem for f in dir_path.glob("*.json")}


#: Keys `load_raw`'s output carries that do NOT belong in `manufacturer.body`.
#: The first three are shredded into `manufacturer_name` / `manufacturer_bc_code`
#: by migration 049 -- leaving a copy in the blob would undo that and give the
#: constraints something to disagree with. `rev` is promoted to
#: `manufacturer.playbook_rev`, which the revision table keys on; two copies of a
#: version number is one copy too many.
BODY_EXCLUDED_KEYS = ("manufacturer", "bc_codes", "aliases", "rev")


def body_of(raw: dict) -> dict:
    """The BEHAVIOUR half of a raw playbook: everything migration 049 did not
    shred and 050 does not promote to a column."""
    return {k: v for k, v in raw.items() if k not in BODY_EXCLUDED_KEYS}


#: Hosts the pipeline may never fetch, alongside the playbooks that name them.
#: Deliberately NOT a `.json` in that directory -- `load_playbooks` globs `*.json`
#: and would parse this as a playbook.
ROBOTS_REFUSED_FILE = "robots_refused.txt"


def load_robots_refused(dir_path: pathlib.Path | str | None = None) -> frozenset[str]:
    """The refused-host list. Empty (not an error) when the file is absent.

    Two stores, the same rule as `load_playbooks`: an explicit `dir_path`
    always reads FILES (that is how the seed imports into the database it
    would otherwise be reading), and with a source configured and no explicit
    directory it reads `refused_host` rows (migration 051).

    The move matters because slice 4 drops the `./playbooks` bind mount from
    the web service. Without it this function would find no file, return an
    empty set BY DESIGN, and silently disarm the guard that refuses a
    `kind:"direct"` source on a host we have ruled we may never fetch -- in
    the one process where a non-dev authors those.

    Absence must not raise: `load_playbooks` is a never-raises runtime path and
    a missing list would otherwise take DISCOVER down. The consequence of an
    absent file is a lint that finds nothing, never a fetch that should not
    happen -- `app/robots.py` still reads the live robots.txt at runtime. A
    DEAD SOURCE is different and does raise, for the reason `_read_rows` does:
    an empty set that means "the database is unreachable" reads identically to
    "nothing is refused", and here that difference is a fetch we ruled against.
    """
    if dir_path is None and _SOURCE is not None:
        rows = _with_source(
            lambda c: c.execute("SELECT host FROM refused_host").fetchall())
        return frozenset(r["host"].lower() for r in rows)
    dir_path = pathlib.Path(dir_path if dir_path is not None else PLAYBOOKS_DIR)
    f = dir_path / ROBOTS_REFUSED_FILE
    if not f.is_file():
        log.warning("playbooks: no %s in %s", ROBOTS_REFUSED_FILE, dir_path)
        return frozenset()
    hosts = set()
    for line in f.read_text().splitlines():
        entry = line.split("#", 1)[0].strip().lower()
        if entry:
            hosts.add(entry)
    return frozenset(hosts)


def is_refused(host: str, refused: frozenset[str]) -> bool:
    """Exact host, or any subdomain of a listed host.

    Suffix matching because the ruling names `coltene.com` while the playbook
    could reach `www.coltene.com`; the refusal is of the site, not of one label.
    Anchored on a dot so `notcoltene.com` does not match.
    """
    host = (host or "").lower()
    return any(host == r or host.endswith("." + r) for r in refused)


def _name_words(text: str) -> tuple[str, ...]:
    """Casefolded alphanumeric words. Punctuation and legal-form commas drop
    out, so `Ustomed Instrumente Ulrich Storz GmbH & Co. KG` and the master's
    `ULRICH STORZ GMBH & CO. KG` compare on the same tokens."""
    return tuple(re.findall(r"[a-z0-9]+", (text or "").casefold()))


def brand_collision(
    name: str, brands: dict[str, frozenset[str]], *, claimed: frozenset[str] | set[str]
) -> tuple[str, tuple[str, ...]] | None:
    """The BC brand `name` would swallow, as `(master name, its codes)`, or None.

    CONTAINMENT on word boundaries, not equality and not a fuzzy ratio.

    Equality is what `reconcile`'s `alias-collision` finding already tested,
    and it does not catch the failure this exists for: the bad aliases were
    full legal names (`Sirona Dental Systems GmbH`) and the master carries bare
    brand labels (`SIRONA`), so the two strings are never equal.

    A fuzzy ratio does not catch it either, which is worth stating because the
    spec prescribed one. Measured against the real master on 2026-08-27:
    `rapidfuzz.fuzz.ratio('Sirona Dental Systems GmbH', 'SIRONA')` is **12.5**
    and the Maillefer pair **9.3**, against `reconcile.SUGGEST_MIN_SCORE` of
    80 -- the length disparity dominates. `ratio` at that floor scores ZERO
    hits over all 38 delivered playbooks, including the two real collisions
    below. It is the right scorer for `reconcile`'s "did you mean" (a typo of
    a whole name) and the wrong one for "this long name contains a brand".

    `claimed` is the playbook's own BC codes, and the exemption that makes the
    rule usable: `Dentsply DeTrey GmbH` contains DENTSPLY (035), which
    dentsply-sirona claims, so it is its own brand and not a collision. Claim
    the code and the refusal goes away -- which is also the structural fix.

    Multi-word master names match as a phrase, so `ULRICH STORZ GMBH & CO. KG`
    needs those four tokens adjacent and is not matched by a stray `GmbH`.
    """
    words = _name_words(name)
    if not words:
        return None
    for master, codes in brands.items():
        if claimed & codes:
            continue
        mw = _name_words(master)
        if not mw or len(mw) > len(words):
            continue
        if any(words[i:i + len(mw)] == mw for i in range(len(words) - len(mw) + 1)):
            return master, tuple(sorted(codes))
    return None


def validate(playbooks: tuple[Playbook, ...], *, refused: frozenset[str] | None = None,
             brands: dict[str, frozenset[str]] | None = None) -> None:
    """Raise on cross-file conflicts and on a fetch target we may not fetch.

    Authoring errors, not runtime conditions. `refused` defaults to the list
    beside the playbooks; pass an explicit set in tests.

    `brands` is master name -> the BC codes carrying it, and turns on the
    `BrandCollision` guard. It is a parameter rather than a lookup because
    this module never opens a connection -- same shape as `refused`. Callers
    that hold one pass it (`playbooks sync`, `manufacturers seed`, the UI
    save); a caller that does not gets today's checks unchanged, so a pure
    file check stays pure.
    """
    if refused is None:
        refused = load_robots_refused()
    seen_codes: dict[tuple[str, str], str] = {}
    seen_names: dict[str, str] = {}

    for pb in playbooks:
        # A `direct` source is a fetch target: DISCOVER's playbook rung enqueues
        # `fetch.url` for it (app/handlers/discover.py). `portal` is excluded --
        # that is a link a human clicks, which is precisely what the 2026-08-20
        # ruling permits.
        for src in pb.doc_sources:
            if src.kind != "direct":
                continue
            host = domain_of(src.url)
            if is_refused(host, refused):
                raise RobotsRefused(
                    f"{pb.slug!r} authors a kind:\"direct\" source on {host!r}, "
                    f"a host we are not permitted to fetch "
                    f"(playbooks/{ROBOTS_REFUSED_FILE}). Author it as "
                    f'kind:"portal" so a human handles it: {src.url}'
                )

        # `crawl.index_url` is a fetch target too -- the (not-yet-built)
        # `playbook` rung would GET it directly, exactly as a `direct` source
        # is fetched. Same check, same list, so a crawl recipe cannot name a
        # host a human already has to handle by hand (playbook-crawl-recipe
        # design §4.1 layer 1).
        for recipe in pb.crawl:
            host = domain_of(recipe.index_url)
            if is_refused(host, refused):
                raise RobotsRefused(
                    f"{pb.slug!r} authors a crawl.index_url on {host!r}, a "
                    f"host we are not permitted to fetch "
                    f"(playbooks/{ROBOTS_REFUSED_FILE}). Drop that recipe "
                    f'and author a kind:"portal" doc_source instead, '
                    f"so a human handles it: {recipe.index_url}"
                )

        for bc in pb.bc_codes:
            # Keyed on the code alone. It used to be `(catalogue, code)`, which
            # let two files claim one code under different catalogue tags and
            # pass. Verified over the delivered 33 before narrowing it: no code
            # is claimed by more than one file under either key.
            if bc.code in seen_codes:
                raise PlaybookConflict(
                    f"BC code {bc.code} claimed by both "
                    f"{seen_codes[bc.code]!r} and {pb.slug!r}"
                )
            seen_codes[bc.code] = pb.slug

        claimed = {bc.code for bc in pb.bc_codes}
        for name in pb.names():
            low = name.casefold()
            if low in seen_names:
                raise PlaybookConflict(
                    f"name {name!r} claimed by both {seen_names[low]!r} and {pb.slug!r}"
                )
            seen_names[low] = pb.slug

            # Checked on `names()`, not `aliases`: a CANONICAL name that
            # swallows another brand mis-scopes exactly as an alias does, and
            # it is the live `ustomed` case.
            hit = brand_collision(name, brands, claimed=claimed) if brands else None
            if hit:
                master, codes = hit
                raise BrandCollision(
                    f"{pb.slug!r} claims the name {name!r}, which contains the "
                    f"BC brand {master!r} (code(s) {', '.join(codes)}) that this "
                    f"playbook does not claim. Documents naming it would be "
                    f"canonicalized to {pb.manufacturer!r} and scoped to its "
                    f"groups, so {master}'s own items could never reach them. "
                    f"Either add {', '.join(codes)} to bc_codes -- if it really "
                    f"is the same manufacturer -- or drop the name."
                )


def for_manufacturer(
    name: str, playbooks: tuple[Playbook, ...] | None = None
) -> Playbook | None:
    """Playbook whose canonical name or any alias equals `name`, case-folded.
    BC codes are deliberately NOT matched here: the code -> name translation is
    `manufacturer_alias`'s job (seeded by `dentalia playbooks sync`), so a
    caller still holding a raw code has an unseeded-alias bug to fix, not a
    lookup to paper over."""
    if not name:
        return None
    if playbooks is None:
        playbooks = load_playbooks()
    low = name.casefold()
    for pb in playbooks:
        if any(n.casefold() == low for n in pb.names()):
            return pb
    return None
