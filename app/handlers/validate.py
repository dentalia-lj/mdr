"""validate.doc — full VALIDATE v3 rule set (S1.0), replacing the S0.3 thin stub.

Pure rules, zero AI, zero discretion (handbook §6 / PRD v3 §6). Writes nothing
(invariant: validate.doc is stateless) — every decision below is encoded as an
additive key on the emitted `gate.candidate` payload; only GATE ever writes
`document` / `item_document` / `evidence` (invariant 1).

Evaluation order (handbook §6 pseudo-code): evidence-presence pre-check (inv. 2)
-> C4 manufacturer-scope pre-rule (binding flow, short-circuits) -> no-item-
identifier ([validate-itemid]) -> C1 REF gate (incl. backfill manufacturer
scoping) -> date sanity -> C7 never-downgrade -> C6 supersession -> rule 6
type/regulation consistency -> emit.

Rule set (PRD v3 §6 / handbook §6, verbatim flag names win over this docstring):
  1. REF gate (C1): overlap(ext.ref_list, group.member_mfr_refs) OR
     ext.basic_udi_di == group.basic_udi_di. Comparand is always the pair
     (canonical_manufacturer, mfr_ref) — groups are manufacturer-scoped by
     construction, so the normal (group given) path inherits the scoping.
     Backfill/email docs (group_id=null) must scope the lookup by the
     document's own extracted manufacturer, canonicalized read-only through
     `manufacturer_alias` first ([validate-mfr-alias]) since
     item_group.canonical_manufacturer is a BC-vendor-code name, not a
     document-facing spelling; zero groups under the canonicalized name ->
     flag `manufacturer-unresolved`, groups but no overlap -> flag
     `no-ref-overlap`. With no manufacturer at all, a guarded unscoped match
     may still link at the capped `ref-catalogue` basis
     ([validate-ref-catalogue]); a guard-failing hit -> flag `ref-unscoped`.
     ref_gate=False either way, caps at staging. Both the alias lookup and the group
     lookup compare names through `_normalize_manufacturer`
     ([validate-mfr-normalize]), and the REF-list side of the comparison
     goes through a per-manufacturer, playbook-authored REF normalization
     rule ([validate-ref-normalize]).
  2. Manufacturer-scope pre-rule (C4): coverage_scope=manufacturer + no
     item-level identifiers -> binding flow (§7b), one candidate total.
     [qa-device-enumeration] (Denis 2026-08-24): the stored document_text is
     read here, and a certificate whose own text enumerates its covered
     devices (model/code annex, per-device class lines, "valid only for the
     above mentioned" clause — see `_enumerates_devices`) carries flag
     `device-enumeration` on the binding candidate; GATE's C16 machine bind
     refuses on it and the document stages for a human instead.
  3. Date sanity: from <= to AND each date in a plausible year band -> flag
   `date-insane` otherwise (handbook vocab).
  4. Never-downgrade (C7): candidate validity_from older than the current
     production doc for the same (coverage subject, type, regulation), BOTH
     dates present -> emit superseded_by_doc_id=<doc_id> AND keep flag
     `older-than-current` (task 5 fix round 1: both, not either/or). GATE
     (§7) only takes the fast path — straight to the superseded chain, no
     human — when validity_from's own calibrated confidence is >= HIGH and
     no OTHER blocking flag (e.g. `no-item-identifier`) is present; otherwise
     `older-than-current` still forces `manual` exactly as before this task,
     which is what prevents a bad guard result from ever reaching
     `production`. Either date null -> no comparison -> flag
     `downgrade-uncomparable`, caps at staging. Null is never treated as
     older or newer. Both present and EQUAL -> flag `same-date-revision`
     (blocking, Denis ruling 2026-09-04): no ordering exists between two
     documents issued on one day, so neither supersedes and a person picks
     the current one. Before the ruling the equal case matched no branch and
     the second document landed as an unrelated production document; three
     groups held three production DoCs on one date when it was measured.
  5. Supersession (C6): only within identical (coverage subject, type,
     regulation) -> supersedes=<doc_id>. MDR never supersedes MDD — parallel
     chains; a same-type, different-regulation current doc is recorded via
     the additive `related_doc_id` key, never as `supersedes`.
  6. Type/regulation consistency: a DoC with an expiry but referencing no
     certificate -> flag `expiry-on-certless-doc` (handbook §6 pseudo-code).

[validate-itemid] (tasks/followups.md): a non-manufacturer-scope doc with NO
ref_list and NO basic_udi_di cannot be linked to the catalogue and must not
silently stage -> flag `no-item-identifier`. gate.py maps this flag to the
`manual` disposition (BLOCKING_FLAGS) so it routes to review, never staging.

[validate-mfr-alias] (2026-08-10 backfill-matching plan, task 1): backfill/
email docs self-identify their manufacturer from document text (e.g.
"Ivoclar Vivadent AG"), which never equals the BC-vendor-code canonical name
groups actually carry (e.g. "IVOCLAR") — see `_canonicalize_manufacturer`.
The lookup is read-only (unlike RESOLVE's self-seeding `_alias_lookup`) and
falls back to the raw string on a miss, so the pre-existing exact-match path
still works. `manufacturer-unresolved` is non-blocking (caps at staged, like
`ref-unscoped`) since `ref_gate` is already False whenever it fires.

[validate-mfr-normalize] (2026-08-11 backfill-matching plan, task 3, Part A):
the alias lookup above is case/whitespace-insensitive, but the FALLBACK it
returns on a miss (the raw extracted string) used to hit `_resolve_group_scoped`'s
group lookup as a case-SENSITIVE exact match — measured live: a document
saying 'GC Europe N.V.' resolved to 0 groups while 95 groups exist under
'GC EUROPE N.V.', because GC's alias table only carries 'GC' and '008', not
the full document-facing legal name. `_normalize_manufacturer` fixes this by
folding case, whitespace, punctuation-adjacent spacing and accents (NFKD,
strip combining marks) on both sides of BOTH lookups. Accent folding is
justified by two playbook aliases (Dentsply) that exist ONLY to spell the
same name with and without diacritics — the same class of transcription
noise as case, not a source of cross-manufacturer collisions. This is
character-level folding to an exact-equality key, never similarity/fuzzy
matching: no rapidfuzz, no threshold, no partial match, and no legal-form
token (GmbH, AG, N.V., Sàrl, Inc., Ltd) is ever deleted.

[validate-ref-normalize] (2026-08-11 backfill-matching plan, task 3, Part B):
manufacturer DoCs enumerate market/region variants of an article number
(digits + 1-3 trailing letters, e.g. '645986DC'); BC records the base number
('645986'). Measured over 174 Ivoclar corpus PDFs: exact REF matching links
19/174 documents, stripping 1-3 trailing letters links 54/174. A global strip
would corrupt manufacturers whose letters ARE load-bearing (Komet's ISO bur
codes, e.g. '104 H251EF 060') — 937 of 8.876 BC `mfr_ref` values contain a
space. `_ref_gate_for_group` therefore looks up a `ref_normalize` rule on the
group's OWN manufacturer's playbook (`app/playbooks.py`, reachable via
`for_manufacturer(group["canonical_manufacturer"])`) and applies it to both
sides of the REF comparison; a manufacturer with no rule (i.e. everyone but
Ivoclar today) gets the identity function, unchanged behaviour. Comparison-
time only — never mutates `item_group_member.mfr_ref` or the extracted
`ref_list`, both of which remain the evidence.

[validate-ref-catalogue] (2026-08-11 ext-manufacturer plan, phase 1): the rules
above all presuppose a manufacturer. Nothing produces one for a corpus/backfill
document — `TARGET` (app/extract/tiers.py) has no `manufacturer` field, so
`ext.manufacturer` is null on every backfill document today and the scoped path
above can never fire. Rather than block on a new extraction field, measure the
premise. PRD C1 says bare article numbers collide across manufacturers; measured
over this catalogue they barely do: of 5.511 distinct `mfr_ref` values, 24
collide (0,44%), 12 of those are prose already flagged `mfr_ref_prose`, leaving
**12 genuine article numbers (0,22%)** — and some of those are one company after
a spinoff (`205363` -> `3M UNITEK | SOLVENTUM`). So a REF match alone is enough
to PROPOSE a link.

It is not enough to WRITE one, and the reason is not the 0,22%. Our measurement
covers only what Dentalia stocks: REF `698946` mapping to one manufacturer HERE
says nothing about a manufacturer whose products Dentalia does not carry using
the same number. Catalogue-observed uniqueness is not global uniqueness. Hence
`match_basis = 'ref-catalogue'`, which migration 021 bars from `production` in
`item_document_trusted_basis_ck` alongside name-family/fetch-context — the link
is proposed, staged, and reviewable, and no writer can promote it. `ref_gate`
also stays False, so the document cannot reach production either.

Three guards run before the basis is reachable (see `_eligible_unscoped_refs`
and `_manufacturers_holding_refs` for the per-guard measurements): minimum REF
length, prose exclusion, and an ambiguity check that flags
`multi-manufacturer-ref` instead of choosing. Verified end to end against the 61
real GC corpus extractions already in the database: 5 documents link, all to
GC EUROPE N.V., zero ambiguous.
"""

from __future__ import annotations

import datetime
import re

from app import manufacturers
from app import playbooks as playbooks_mod
from app import queue
from app.config import load_config
from app.extract import tiers
from app.handlers import register
from app.results import Result

# Plausible-range bounds for the date-sanity rule (PRD §6 rule 3). A compliance
# document is never dated before the MDD era and never validly extends past this
# ceiling; a value outside the band is hallucinated/OCR garbage (e.g. year 9999
# or a 3-digit OCR of "2013"), not a real date, and must not drive supersession.
_YEAR_MIN = 1990
_YEAR_MAX = 2100

# Must carry an extracted value for the doc to be gateable at all (inv. 2 pre-check).
# Dates / refs / UDI are conditionally empty by doc type, so they are NOT required
# here; the rule set below is what interprets their absence.
REQUIRED_FIELDS = ("type", "regulation", "coverage_scope")


def _has_value(fields: dict, name: str) -> bool:
    ev = fields.get(name)
    return isinstance(ev, dict) and ev.get("value") not in (None, "", [])


def _val(fields: dict, name: str):
    ev = fields.get(name)
    return ev.get("value") if isinstance(ev, dict) else None


def _to_date(v):
    """Normalize a date-ish value to `datetime.date` (extraction fields carry
    ISO strings; rows read back from `document` carry real `date` objects)."""
    if v is None:
        return None
    if isinstance(v, str):
        try:
            return datetime.date.fromisoformat(v)
        except ValueError:
            return None
    return v  # already a date/datetime


# --- group resolution -------------------------------------------------------

#: Phrases that mark a date as an explicitly STATED validity end rather than one
#: the reader inferred from position on the page. Grounded in the corpus: every
#: distinct validity_to verbatim in the registry on 2026-08-17 is one of "Valid
#: until[:]", "Expiry Date[:]", "Scadenza / Valid until" or "This declaration is
#: valid until:". The other languages are extrapolation from the corpus brands'
#: home markets, NOT measured — which is safe here only because an unmatched
#: phrase keeps the flag on (see rule 6). `expir` covers expiry/expires/
#: expiration/expiration date without enumerating them.
_EXPIRY_PHRASE_RE = re.compile(
    r"valid\s+(?:until|till|to|thru)|valable\s+jusqu|expir"
    r"|g(?:ü|ue)ltig\s+bis|scadenz|velja\s+do|geldig\s+tot"
    r"|v(?:á|a)lido\s+hasta|v(?:á|a)lido\s+at(?:é|e)|gyldig\s+til",
    re.IGNORECASE,
)


def _expiry_is_stated(fields: dict) -> bool:
    """Whether the extracted `validity_to` cites a phrase that names it as an
    expiry, read from the field's own evidence verbatim.

    The verbatim is the only place that distinguishes "Valid until 2026-05-04"
    from "Leuven, 12 February 2026" — both parse to a date, and only one of them
    IS one. Missing evidence returns False, so the flag stays on: a date nobody
    can point at on the page is exactly the case this rule exists to catch."""
    ev = fields.get("validity_to")
    verbatim = ev.get("verbatim") if isinstance(ev, dict) else None
    return bool(verbatim and _EXPIRY_PHRASE_RE.search(verbatim))


# --- C4 device-enumeration guard ([qa-device-enumeration], Denis 2026-08-24) --

#: The certificate's own words limiting it to the devices it lists. Measured on
#: doc 310 (Kiwa Cermet MED 31385): "it is valid only for the above mentioned
#: Medical Devices that are subject to survey" / "è valido solo per le tipologie
#: di dispositivi sopra identificate". The German line is extrapolated from the
#: same clause, NOT measured — unlike _EXPIRY_PHRASE_RE the failure direction
#: here is unsafe-on-miss (an unmatched phrase keeps today's auto-bind), so the
#: list must be extended per notified body as their texts land.
_ENUM_RESTRICTION_RE = re.compile(
    r"valid only for the above[- ]mentioned"
    r"|valido solo per le tipologie di dispositivi sopra"
    r"|nur f(?:ü|ue)r die oben genannten",
    re.IGNORECASE,
)
#: Per-device model label: "Modello / Model:" (measured, doc 310) plus the bare
#: EN/DE/FR/ES spellings of the same colon-anchored label. The colon is
#: load-bearing — prose mentioning a "model" never matches.
_ENUM_MODEL_LABEL_RE = re.compile(
    r"\bmod(?:e|è)l(?:lo|le|l|o)?\s*(?:/\s*model\s*)?:", re.IGNORECASE)
#: Per-device article-code label: "Codici / Codes:" (measured, doc 310).
_ENUM_CODES_LABEL_RE = re.compile(
    r"\bcod(?:ici|es)\s*(?:/\s*codes\s*)?:", re.IGNORECASE)
#: Per-device risk-class label, colon-anchored: "Classe di rischio / Risk
#: class:" (measured, doc 310, three occurrences — one per device). BSI's
#: family-level "Risk Classification" schedule header (doc 138) has no colon
#: and must not count: a device-FAMILY schedule is how a genuinely line-wide
#: QA certificate prints its scope.
_ENUM_CLASS_LABEL_RE = re.compile(
    r"(?:classe di rischio|risk class|risikoklasse|classe de risque"
    r"|clase de riesgo)\s*(?:/\s*risk class)?\s*:",
    re.IGNORECASE,
)
#: An article/model number as printed in doc 310's annex ("901595/10004798").
#: EMDN category codes ("Q010501") deliberately do NOT match — they name a
#: device family, not a model (doc 420's list is a correct line-wide scope).
_ENUM_ARTICLE_CODE_RE = re.compile(r"\b\d{5,}\b")


def _enumerates_devices(text: str | None) -> bool:
    """Whether a stored certificate text enumerates the specific devices it
    covers — the [qa-device-enumeration] guard (Denis ruling 2026-08-24).

    A QMS/QA-system certificate that lists its covered devices by model and
    article code is, by its own words, NOT manufacturer-wide, and C16's machine
    binding must not fan it across the catalogue: doc 310 enumerates three
    device types (Dental Prescale II, GC TEMP PRINT, MI Varnish — with model
    codes and per-device class lines) and was bound to 356 GC items.

    Fires when the restriction clause is present (the certificate saying so is
    sufficient on its own, even when the annex pages lost their text layer), or
    when at least TWO independent enumeration signals appear: model labels,
    code labels backed by article-number tokens, or repeated per-device
    risk-class lines. Two-of-three keeps a single stray label from capping a
    legitimate certificate while surviving the loss of any one signal to OCR
    or layout noise.

    Measured blast radius, 2026-08-25, across all 809 stored `document_text`
    rows: exactly one document fires — doc 310. The Carl Martin ISO 13485
    (scope paragraph, doc 816), BSI's device-family schedule (doc 138) and
    TÜV's EMDN category list (doc 420) all stay below the threshold; the
    boundary is pinned in tests/fixtures/qa_cert_texts.py. A missing text or a
    scan (`document_text.source = 'none'`) returns False — nothing measurable
    keeps today's behaviour, deliberately."""
    if not text:
        return False
    if _ENUM_RESTRICTION_RE.search(text):
        return True
    signals = 0
    if _ENUM_MODEL_LABEL_RE.search(text):
        signals += 1
    if (_ENUM_CODES_LABEL_RE.search(text)
            and len(_ENUM_ARTICLE_CODE_RE.findall(text)) >= 2):
        signals += 1
    if len(_ENUM_CLASS_LABEL_RE.findall(text)) >= 2:
        signals += 1
    return signals >= 2


def _stored_text(conn, content_hash: str) -> str | None:
    """The deterministic PyMuPDF read of the archived bytes, or None when no
    text was recovered ('none' rows store an empty content by contract, and an
    absent row means EXTRACT never saw the hash — both unmeasurable)."""
    row = conn.execute(
        "SELECT content FROM document_text WHERE content_hash=%s AND source='pdf-text'",
        (content_hash,),
    ).fetchone()
    return row["content"] if row else None


# Both helpers moved to `app.manufacturers` on 2026-08-17 so GATE compares
# manufacturer names the same way this stage does. They were VALIDATE-private
# while VALIDATE was the only stage doing the comparison; GATE's binding path
# had meanwhile hand-rolled its own exact, case-sensitive, wrong-direction
# lookup and bound zero items in silence. Same reason `app.urls` is shared by
# DISCOVER and FETCH: a divergence here produces wrong links, not an error.
# Re-exported under the old private names -- they are referenced throughout
# this module's docstrings and by tests.
_normalize_manufacturer = manufacturers.normalize
_canonicalize_manufacturer = manufacturers.canonicalize


def _get_group(conn, group_id) -> dict | None:
    return conn.execute(
        "SELECT group_id, canonical_manufacturer, basic_udi_di "
        "FROM item_group WHERE group_id=%s",
        (group_id,),
    ).fetchone()


def _group_members(conn, group_id) -> list[dict]:
    return conn.execute(
        "SELECT item_ref, mfr_ref FROM item_group_member WHERE group_id=%s",
        (group_id,),
    ).fetchall()


def _ref_normalize_rule(manufacturer: str | None) -> dict | None:
    """The `ref_normalize` rule authored on the playbook that claims
    `manufacturer`'s canonical name, or None if no playbook claims it or it
    carries no rule ([validate-ref-normalize]). `for_manufacturer` never
    raises (its own `load_playbooks` contract), so a missing/malformed
    playbooks/ dir degrades to "no rule" here too, never a validate.doc
    failure."""
    if not manufacturer:
        return None
    pb = playbooks_mod.for_manufacturer(manufacturer)
    return pb.ref_normalize if pb else None


def _normalize_ref(ref: str | None, rule: dict | None) -> str | None:
    """Apply `rule` to ONE ref string for C1 comparison only — the caller's
    stored/extracted value is never mutated. `rule=None` (no playbook, or a
    playbook with no `ref_normalize` key) is the identity function: today's
    exact-match behaviour, unchanged, for every manufacturer without an
    authored rule.

    Strategies implemented:

    "strip-trailing-letters" — digits followed by 1-3 letters is a
    manufacturer's market/region suffix (measured on 174 Ivoclar corpus PDFs,
    e.g. '645986DC' -> '645986'; see [validate-ref-normalize]).

    "reorder-shank-figure-size" — the same ISO 6360 bur code written in two
    orders. Dentalia's catalogue writes SHANK FIGURE SIZE separated by spaces
    ('314 H1 006'); Komet prints FIGURE.SHANK.SIZE ('H1.314.006'). Both fold to
    the dotted form, so either side can be either spelling. Measured over the
    186 Komet corpus PDFs on 2026-08-18: catalogue codes found in the documents
    go from **0 to 98** with this rule, and Komet is the only manufacturer that
    needs it (client, 2026-08-18: "ne — komet je specifičen"). The order was
    derived once from Komet's `Where to find DoC.xlsx`, which pairs article
    '000085K3' with reference 'H1.314.006'; the spreadsheet is a working
    document and is deliberately NOT a runtime input (Denis's ruling
    2026-08-18) — only the rule it revealed is.

    A ref that doesn't fully match a strategy's shape passes through unchanged
    regardless of which manufacturer's rule is in effect — the shape check is
    what keeps this safe even if a rule were ever misapplied."""
    if not ref or not rule:
        return ref
    strategy = rule.get("strategy")
    if strategy == "strip-trailing-letters":
        max_letters = rule.get("max_letters", 3)
        m = re.fullmatch(rf"(\d+)([A-Za-z]{{1,{max_letters}}})", ref)
        if m:
            return m.group(1)
    elif strategy == "reorder-shank-figure-size":
        return _fold_bur_code(ref)
    return ref


#: Catalogue spelling: three-digit shank, figure, size — '314 H1 006'.
#: DELIBERATELY strict. Seven Komet items carry a Dentalia packaging suffix
#: ('104 H219A 023-1'), and two of those would fold onto the key of an
#: unsuffixed sibling — one document naming the base code would then link both
#: articles, which is exactly the false link invariant 3 exists to prevent.
#: They stay unfolded (identity) and unmatched until a document names them.
_BUR_SPACED = re.compile(r"(\d{3})\s+([A-Z0-9]{1,10})\s+(\d{1,3})", re.I)
#: Manufacturer spelling: figure.shank.size — 'H1.314.006', and the letterless
#: '1.204.005' that Komet's older list layout prints.
_BUR_DOTTED = re.compile(r"([A-Z]{0,3}\d{0,4})\.(\d{3})\.(\d{1,3})", re.I)


def _fold_bur_code(ref: str) -> str:
    """Fold either spelling of an ISO 6360 bur code to 'FIGURE.SHANK.SIZE',
    size zero-padded to three digits. Anything else is returned untouched --
    'LS SFQ2008' is a Komet catalogue entry that is not a bur code at all, and
    folding must never invent a shape it does not have.

    `000 SFD7 1` was named here as a second such example until 2026-09-15 and is
    not one: it matches `_BUR_SPACED` exactly ('000' + 'SFD7' + '1') and folds to
    'SFD7.000.001'. Whether that is the right answer for it is a question about
    Komet's catalogue, not about this function, which is doing what the shape
    says. Pinned by `tests/test_eudamed_ref_fold.py`, which runs this and the SQL
    implementation over the same inputs."""
    m = _BUR_SPACED.fullmatch(ref.strip())
    if m:
        shank, figure, size = m.groups()
        return f"{figure.upper()}.{shank}.{size.zfill(3)}"
    m = _BUR_DOTTED.fullmatch(ref.strip())
    if m:
        figure, shank, size = m.groups()
        return f"{figure.upper()}.{shank}.{size.zfill(3)}"
    return ref


def _member_article_basis(member: dict, targets: set, rule: dict | None) -> str | None:
    """Which of a member's two article numbers the document's REF list names,
    as a `match_basis`, or None if neither.

    C12 amendment (2026-08-13): the comparand is the pair
    `(canonical_manufacturer, catalogue article number)`, and a catalogue item
    has TWO — the supplier's (`mfr_ref`) and Dentalia's own (`item_ref`). The
    client's ruling is that the second is the important one: it is the number
    printed on the physical article. 7.082 of 15.958 rows carry no `mfr_ref` at
    all, so before this they were unreachable by the gate however plainly a
    document named them.

    `mfr_ref` is tested FIRST on purpose. 7.450 rows hold the same value in both
    columns, and for those the supplier's number matching is the stronger claim;
    recording `ref-item` there would make `ref-list` stop meaning what it says.
    The basis is what the registry keeps as provenance, so it must name the
    column that actually matched, not the widest thing that could have."""
    if member["mfr_ref"] and _normalize_ref(member["mfr_ref"], rule) in targets:
        return "ref-list"
    if member["item_ref"] and _normalize_ref(member["item_ref"], rule) in targets:
        return "ref-item"
    return None


def _ref_gate_for_group(conn, group: dict, ref_list: list, basic_udi_di,
                        basis_override: str | None = None) -> tuple[bool, list[dict]]:
    """C1 REF gate for a known group: Basic UDI-DI match (all members linked)
    takes priority over REF-list overlap (only the overlapping members
    linked) — Basic UDI-DI is the stronger, product-family-wide key.

    The REF-list side is compared through the group's own manufacturer's
    `ref_normalize` playbook rule, applied to both the extracted ref_list and
    the stored article numbers ([validate-ref-normalize]) — never the raw values
    themselves, which stay unchanged as evidence.

    Both of a member's article numbers are comparands here (`_member_article_basis`).
    That is safe at production strength ONLY because this function is the SCOPED
    path: the manufacturer is already established before any number is compared,
    so the number is merely picking items within a known manufacturer rather
    than identifying one. The unscoped caller rewrites every basis it gets from
    here to the capped `ref-catalogue`, which is what keeps that distinction
    from leaking.

    `basis_override` (spec §9a) renames the REF-overlap basis to `map-supplier`
    when the caller's `ref_list` evidence carries `source: coverage-map` — the
    list was read out of the manufacturer's own article -> document index
    rather than off the document itself, and `match_basis` is what the
    registry keeps as provenance. It touches ONLY the REF-overlap branch: a
    Basic UDI-DI match is a different key entirely, keyed off `group_udi`, not
    `ref_list`, and always keeps its own, stronger `basic-udi-di` basis."""
    members = _group_members(conn, group["group_id"])
    group_udi = group.get("basic_udi_di")
    if basic_udi_di and group_udi and basic_udi_di == group_udi:
        links = [{"item_ref": m["item_ref"], "match_basis": "basic-udi-di", "udi": basic_udi_di}
                 for m in members]
        return True, links
    ref_set = {r for r in (ref_list or []) if r}
    if ref_set:
        rule = _ref_normalize_rule(group.get("canonical_manufacturer"))
        normalized_targets = {_normalize_ref(r, rule) for r in ref_set}
        overlapping = [(m, b) for m in members
                       if (b := _member_article_basis(m, normalized_targets, rule))]
        if overlapping:
            links = [{"item_ref": m["item_ref"], "match_basis": basis_override or basis}
                     for m, basis in overlapping]
            return True, links
    return False, []


def _resolve_group_scoped(conn, manufacturer: str, ref_list: list, basic_udi_di) -> tuple[dict | None, int, int]:
    """Backfill/email (group_id=null): search groups under the document's OWN
    extracted/known (caller-canonicalized, see `_canonicalize_manufacturer`)
    manufacturer only — an unscoped hit is not a gate pass.

    Deterministic (ORDER BY group_id): if more than one scoped group passes
    the gate, resolves to the lowest group_id — but the caller is told the
    full match count (never silently drops the other matches; "skipped rows
    -> counted and reported, never silent").

    Returns (best group | None, match_count, groups_found) and is kept for the
    callers that only need to know WHETHER the manufacturer resolved. Use
    `_links_for_manufacturer` to actually form links: resolving to one group and
    linking only its members is what lost 228 items across 23 GC documents
    (Denis, 2026-08-13).

    `groups_found` is
    the number of item_group rows carrying this manufacturer (after
    `_normalize_manufacturer` folding, [validate-mfr-normalize]) BEFORE the
    C1 REF/UDI gate filters them — zero means the manufacturer itself never
    resolved to anything in the catalogue, which the caller reports as flag
    `manufacturer-unresolved`, distinct from a manufacturer we do carry whose
    REF list simply didn't overlap this document.

    Scans the full `item_group` table and filters in Python (same shape as
    `_resolve_group_unscoped` below) rather than pushing the fold into SQL —
    accent-folding has no cheap equivalent without the `unaccent` extension,
    and `item_group` is a few thousand rows, not a hot path (backfill/email
    docs only)."""
    all_groups = conn.execute(
        "SELECT group_id, canonical_manufacturer, basic_udi_di "
        "FROM item_group ORDER BY group_id"
    ).fetchall()
    target = _normalize_manufacturer(manufacturer)
    groups = [g for g in all_groups if _normalize_manufacturer(g["canonical_manufacturer"]) == target]
    matches = [g for g in groups if _ref_gate_for_group(conn, g, ref_list, basic_udi_di)[0]]
    if not matches:
        return None, 0, len(groups)
    return matches[0], len(matches), len(groups)


#: Which basis survives when one item is reachable through more than one group.
#: `item_group_member` is keyed per group, so an item can sit in several, and a
#: duplicate link would violate `item_document`'s (item_ref, doc_id) PK at GATE.
#: Strongest first, mirroring `_ref_gate_for_group`'s own precedence. `map-supplier`
#: ranks just below `basic-udi-di`: a map link is as strong as the REF list it
#: came from (same overlap test, same scoped manufacturer), and stronger than a
#: bare item-number hit, so it must survive over `ref-list`/`ref-item` when the
#: same item is reachable through more than one group (spec §9a).
_BASIS_RANK = {"basic-udi-di": 0, "map-supplier": 1, "ref-list": 2, "ref-item": 3}


def _links_for_manufacturer(conn, manufacturer: str, ref_list: list, basic_udi_di,
                            basis_override: str | None = None) -> tuple[list[dict], int, int, int | None]:
    """Every member the document NAMES, across EVERY group under `manufacturer`.

    Returns `(links, groups_matched, groups_found, primary_group_id)`.

    A declaration listing twenty articles covers twenty articles. `item_group`
    is OUR clustering for discovery -- it decides which items need a document
    searched for, never what a manufacturer's document covers -- so letting it
    decide coverage dropped every match outside one arbitrarily-chosen group.
    Measured 2026-08-13 over the GC corpus: 3.118 extracted codes reach 323
    catalogue items, 96 links were written, 228 items missed across 23
    documents. Document 204 reaches 20 items across 5 groups, all of them the
    same manufacturer, and linked 1.

    Trust is unchanged, because trust never came from the grouping. Every group
    scanned here carries the SAME `canonical_manufacturer` (the caller pinned it
    -- from the requesting group, from the extracted name, or from the
    exactly-one-holder guard), so each link still rests on the invariant-3 pair
    `(canonical_manufacturer, catalogue article number)`. Widening from one group
    to that manufacturer's groups adds items, never manufacturers.

    `primary_group_id` is the lowest matching group, kept as the document's
    group for provenance and for the callers that record one. It no longer
    bounds coverage.
    """
    all_groups = conn.execute(
        "SELECT group_id, canonical_manufacturer, basic_udi_di "
        "FROM item_group ORDER BY group_id"
    ).fetchall()
    target = _normalize_manufacturer(manufacturer)
    groups = [g for g in all_groups
              if _normalize_manufacturer(g["canonical_manufacturer"]) == target]

    best: dict[str, dict] = {}
    matched = 0
    primary = None
    for g in groups:                       # ORDER BY group_id, so the first
        ok, links = _ref_gate_for_group(conn, g, ref_list, basic_udi_di, basis_override)
        if not ok:
            continue
        matched += 1
        if primary is None:                # match IS the lowest, no second pass
            primary = g["group_id"]
        for link in links:
            incumbent = best.get(link["item_ref"])
            if incumbent is None or (_BASIS_RANK.get(link["match_basis"], 99)
                                     < _BASIS_RANK.get(incumbent["match_basis"], 99)):
                best[link["item_ref"]] = link
    return list(best.values()), matched, len(groups), primary


def _eligible_unscoped_refs(ref_list: list, min_len: int) -> list[str]:
    """The extracted REFs allowed to identify a document that carries NO
    manufacturer ([validate-ref-catalogue]). Two guards, both measured:

    * `min_len` (cfg.validate.min_unscoped_ref_len, default 6) — 85 catalogue
      REFs are 1-3 characters (`100` -> INTERDENT, `102` -> SANOLABOR, `158`
      -> ULTRADENT). Each is "unambiguous" in the collision sense and useless
      as identity: a GC declaration listing REF `100` would link to an
      INTERDENT item. At 6 characters 4.695 of 5.511 REFs (85,2%) stay
      eligible and every real GC corpus match survives.
    * prose — 49 distinct catalogue values contain "!" (`UKINJENO!`,
      `NI VEC DOBAVLJIVO!`). INGEST recognises them (`mfr_ref_prose`, the
      deliberate "!"-only rule from the vendor-master analysis: 652 legitimate
      codes contain a space, so no space/letter heuristic is safe) and since
      2026-08-13 scrubs them to NULL before the mirror write. The exclusion
      still earns its place: a member row keeps whatever it was last ingested
      with until its next ingest, so prose can still sit in
      `item_group_member.mfr_ref` here, and the extracted side of the
      comparison is not scrubbed at all. The collision guard catches prose
      spanning several manufacturers; it does NOT catch a prose value unique to
      one, which is why this exclusion is separate.

    Values are stripped so the comparison is consistent on both sides, and
    returned raw otherwise — `ref_normalize` is deliberately NOT applied here,
    see `_manufacturers_holding_refs`."""
    out: list[str] = []
    for raw in ref_list or []:
        if not raw:
            continue
        ref = str(raw).strip()
        if len(ref) < min_len or "!" in ref:
            continue
        out.append(ref)
    return out


def _manufacturers_holding_refs(conn, refs: list[str]) -> list[str]:
    """Every `canonical_manufacturer` holding any of `refs` as a member article
    number — `mfr_ref` OR `item_ref` (C12, 2026-08-13). Exact comparison,
    deliberately raw.

    Both columns are searched because this is the AMBIGUITY guard, and a guard
    that sees fewer holders than the linker does is worse than no guard: it
    would report a number unique while a second manufacturer holds it under the
    other column, which is precisely the wrong-company-certificate outcome the
    unscoped path exists to avoid. Widening the linker without widening this
    would open the hole this closes.

    Why no `ref_normalize` here: measured 2026-08-11 over the whole catalogue,
    applying strip-trailing-letters globally raises genuine cross-manufacturer
    collisions from 12 to 18 (+50%) and — over the 61 real corpus extraction
    attempts — adds exactly ZERO extra document hits. Cost without benefit, so
    the manufacturer is pinned by raw REFs only. Normalization still applies
    afterwards inside `_ref_gate_for_group`, once the manufacturer IS known:
    that is the scoped case, where a market-suffix variant is safe precisely
    because the pair (manufacturer, ref) is established."""
    if not refs:
        return []
    rows = conn.execute(
        "SELECT DISTINCT g.canonical_manufacturer AS mfr "
        "FROM item_group_member m JOIN item_group g USING (group_id) "
        "WHERE m.mfr_ref = ANY(%s) OR m.item_ref = ANY(%s) ORDER BY 1",
        (list(refs), list(refs)),
    ).fetchall()
    return [r["mfr"] for r in rows]


def _resolve_group_unscoped(conn, ref_list: list, basic_udi_di) -> dict | None:
    """Whether ANY group (regardless of manufacturer) would match — used only
    to decide the `ref-unscoped` flag; the result is never trusted/used."""
    groups = conn.execute(
        "SELECT group_id, canonical_manufacturer, basic_udi_di FROM item_group"
    ).fetchall()
    for g in groups:
        ok, _ = _ref_gate_for_group(conn, g, ref_list, basic_udi_di)
        if ok:
            return g
    return None


# --- C6/C7 current-document lookups -----------------------------------------

def _current_production_doc(conn, group_id, doc_type: str, regulation: str,
                            exclude_content_hash: str | None = None) -> dict | None:
    """The current production document for this coverage subject (any member
    of `group_id` carries a production link to it) of the SAME type and
    regulation — the identity C6/C7 both key off. Same-type/regulation scoping
    is what makes "never downgrade" and "supersedes" comparisons meaningful;
    an MDR candidate is never compared against an MDD current (they are
    parallel chains, rule 5).

    `exclude_content_hash` is the candidate's own bytes. A production document
    re-validated at a new `extract_rev` (a repair, a re-extract, a template
    authored later) otherwise found ITSELF here, matched its own date and
    flagged `same-date-revision` into manual (NEODENT doc 521, 2026-09-11:
    241 new links held behind a manual task). The tie rule is for two
    documents."""
    return conn.execute(
        """
        SELECT DISTINCT d.doc_id, d.validity_from
        FROM document d
        JOIN item_document idoc ON idoc.doc_id = d.doc_id AND idoc.status = 'production'
        JOIN item_group_member gm ON gm.item_ref = idoc.item_ref
        WHERE gm.group_id = %s AND d.status = 'production'
          AND d.type = %s AND d.regulation = %s
          AND d.content_hash IS DISTINCT FROM %s
        ORDER BY d.validity_from DESC NULLS LAST, d.doc_id DESC
        LIMIT 1
        """,
        (group_id, doc_type, regulation, exclude_content_hash),
    ).fetchone()


def _related_production_doc(conn, group_id, doc_type: str, exclude_regulation: str) -> dict | None:
    """A current production document of the SAME type but a DIFFERENT
    regulation — MDR/MDD parallel chains (rule 5): recorded as `related`,
    never `superseded_by`."""
    return conn.execute(
        """
        SELECT DISTINCT d.doc_id
        FROM document d
        JOIN item_document idoc ON idoc.doc_id = d.doc_id AND idoc.status = 'production'
        JOIN item_group_member gm ON gm.item_ref = idoc.item_ref
        WHERE gm.group_id = %s AND d.status = 'production'
          AND d.type = %s AND d.regulation <> %s
        ORDER BY d.doc_id DESC
        LIMIT 1
        """,
        (group_id, doc_type, exclude_regulation),
    ).fetchone()


# Trailing revision code on a certificate number: " R000", " R7". Anchored and
# whitespace-separated on purpose -- it must not bite into a number that merely
# ends in a letter and digits.
_RCODE_SUFFIX = re.compile(r"\s+R[0-9]+$")


def _resolve_cited_certificate(conn, fields, manufacturer):
    """Best-effort: find the certificate this DoC cites, in our own registry.

    MDR Annex IV item 8 names the certificate; Article 56 caps its validity at
    five years. That certificate's expiry is the DoC's real renewal trigger,
    since Annex IV requires no expiry on the declaration itself.

    Returns (doc_id | None, resolved_from | None). Null is an ordinary outcome:
    Class I devices have no certificate, and we may not hold the cited one yet.
    """
    numbers = [n for n in ([_val(fields, "cert_number")] +
                           (_val(fields, "referenced_docs") or [])) if n]
    if not numbers:
        return None, None
    # A trailing R-code is a revision marker on the same certificate, and it is
    # stripped from BOTH sides before comparing (Denis's ruling, 2026-08-19).
    # Measured that day, this was the whole reason the inheritance path had never
    # executed: `cert_doc_id` was NULL on all 326 documents, while 80 of the 306
    # declarations cited `MDR 778483` against a certificate we hold as
    # `MDR 778483 R000`. Exact equality missed every one of them.
    #
    # `Rev. NN` is deliberately left alone. `Rev. 00` and `Rev. 01` may be a
    # supersession rather than a spelling, and resolving one to the other would
    # assert that silently; 96 declarations sit in that shape and all carry their
    # own expiry, so refusing costs nothing. Widening this regex is a ruling, not
    # a tidy-up.
    loose = [_RCODE_SUFFIX.sub("", n) for n in numbers]
    row = conn.execute(
        "SELECT doc_id, cert_number FROM document "
        "WHERE type IN ('EC','ISO') AND status IN ('production','superseded') "
        "  AND (cert_number = ANY(%s) "
        "       OR regexp_replace(cert_number, '\\s+R[0-9]+$', '') = ANY(%s)) "
        # The spelling the declaration actually named wins; only then doc_id, so
        # the choice is never left to insertion order when both are held.
        "ORDER BY (cert_number = ANY(%s)) DESC, doc_id LIMIT 1",
        (numbers, loose, numbers),
    ).fetchone()
    if row is None:
        return None, None
    return row["doc_id"], row["cert_number"]


def handle_validate_doc(conn, job: dict) -> dict:
    p = job["payload"]
    content_hash = p["content_hash"]
    extract_rev = p["extract_rev"]
    group_id = p.get("group_id")
    # Carried through to gate.candidate, not consumed here. GATE needs a stored
    # location for document/evidence.archive_url and falls back to
    # fetch_log.url_normalized when the payload has none -- which for a backfill
    # is the corpus SOURCE path, meaningful only on the scanning machine and
    # destroyed by the next re-dump. That fallback is what put a /imports/... URL
    # on all 130 documents of the GC pilot.
    archive_url = p.get("archive_url")

    row = conn.execute(
        "SELECT fields FROM extraction_attempt WHERE content_hash=%s AND extract_rev=%s",
        (content_hash, extract_rev),
    ).fetchone()
    if row is None:
        r = Result()
        r.count("suppressed")
        r.count("suppressed_no_extraction")
        r.note(f"no extraction_attempt row for {content_hash} rev {extract_rev}")
        return {"emitted": False, "reason": "no-extraction", **r.as_dict()}
    fields = row["fields"]

    missing = [f for f in REQUIRED_FIELDS if not _has_value(fields, f)]
    if missing:
        r = Result()
        r.count("suppressed")
        r.count("suppressed_incomplete_evidence")
        for f in missing:
            # Per-field, because "why were 7 of 22 suppressed" is only
            # answerable if the reason is countable: a doc-class short-circuit
            # (MSDS -> no fields at all) and a DoC whose regulation normalized
            # to null look identical under one bare `suppressed` counter.
            r.count(f"suppressed_missing_{f}")
        r.note("required evidence absent: " + ", ".join(missing))
        return {"emitted": False, "reason": "incomplete-evidence",
                "missing": missing, **r.as_dict()}

    coverage_scope = _val(fields, "coverage_scope")
    ref_list = _val(fields, "ref_list") or []
    # A ref_list read out of the manufacturer's own article -> document index
    # rather than off the document. `match_basis` is what the registry keeps as
    # provenance, so it names that. Universal and data-driven: no document
    # written before 2026-08-18 carries the marker, so this is inert for all of
    # them (spec 2026-08-18-komet-coverage-map-design.md, section 9a).
    basis_override = ("map-supplier"
                      if (fields.get("ref_list") or {}).get("source") == "coverage-map"
                      else None)
    basic_udi_di = _val(fields, "basic_udi_di")
    doc_type = _val(fields, "type")
    regulation = _val(fields, "regulation")

    # Extraction-SHAPE flags, computed from the stored fields
    # (tiers.integrity_flags): an LLM `ref_list` that came back at exactly the
    # prompt cap and may therefore be a partial reading of a longer list, and a
    # `type='ISO'` with no ISO standard number anywhere in the evidence. Both
    # REPORT and never rewrite — the list below is used exactly as stored, every
    # code it names still forms its link, and the document keeps the type it was
    # given. Both are in `gate.INFORMATIONAL_FLAGS`, so neither changes a
    # disposition.
    #
    # Computed HERE, above the C4 pre-rule, because BOTH routes need them. Until
    # 2026-09-09 this sat below the mfr-binding return and neither flag could
    # reach a manufacturer-scope candidate — which is the entire population
    # `iso-without-standard-number` was written for, since a QMS or
    # notified-body certificate is manufacturer-scope by definition. Docs 248,
    # 260 and 316, the three the design named, carried `"flags": []` because of
    # it. `[iso-flag-unreachable-on-mfr-binding]`.
    shape_flags = tiers.integrity_flags(fields)

    # C4 pre-rule: manufacturer-scope doc with no item-level identifiers ->
    # binding flow (§7b), exactly one candidate regardless of how many groups
    # requested it.
    if coverage_scope == "manufacturer" and not ref_list and not basic_udi_di:
        manufacturer = None
        if group_id is not None:
            group = _get_group(conn, group_id)
            manufacturer = group["canonical_manufacturer"] if group else None
        else:
            manufacturer = _val(fields, "manufacturer")
        # [qa-device-enumeration]: a certificate whose own text enumerates its
        # covered devices is not manufacturer-wide, whatever coverage_scope
        # says. The flag rides the candidate; GATE's C16 machine bind refuses
        # on it and the document stages for a human instead. gate.apply
        # bind-manufacturer (a person) is deliberately unaffected.
        flags: list[str] = list(shape_flags)
        if _enumerates_devices(_stored_text(conn, content_hash)):
            flags.append("device-enumeration")
        candidate = {"content_hash": content_hash, "extract_rev": extract_rev,
                     "group_id": group_id, "route": "mfr-binding"}
        if flags:
            candidate["flags"] = flags
        if manufacturer:
            candidate["manufacturer"] = manufacturer
        if archive_url:
            candidate["archive_url"] = archive_url
        queue.enqueue(
            conn, "gate.candidate", candidate,
            dedupe_key=f"gate:{content_hash}:{extract_rev}:mfr-binding",
        )
        r = Result()
        r.count("emitted")
        r.count("emitted_mfr_binding")
        if not manufacturer:
            # The candidate still goes to GATE, but its Approve button is
            # disabled until a human names the manufacturer -- countable, so a
            # sweep can report how much of the binding queue is unactionable.
            r.count("mfr_binding_without_manufacturer")
        for f in flags:
            r.count(f"flag_{f.replace('-', '_')}")
        if flags:
            r.count("flagged")
            r.note("flags: " + ", ".join(flags))
        return {"emitted": True, "route": "mfr-binding", "flags": flags,
                **r.as_dict()}

    # Same list the binding route above starts from — see `shape_flags`.
    flags: list[str] = list(shape_flags)

    # [validate-itemid]: a non-manufacturer-scope doc that yields no item id at
    # all cannot be linked to the catalogue -> must not silently stage.
    if not ref_list and not basic_udi_di:
        flags.append("no-item-identifier")

    # C1: REF gate — comparand is always the pair (canonical_manufacturer, mfr_ref).
    cfg = load_config()
    ref_gate = False
    links: list[dict] = []
    resolved_group_id = group_id
    multi_group_matches = 0
    if group_id is not None:
        group = _get_group(conn, group_id)
        if group is not None:
            ref_gate, links = _ref_gate_for_group(conn, group, ref_list, basic_udi_di,
                                                  basis_override)
            if not ref_gate and "no-item-identifier" not in flags:
                # C3 fallback (PRD §4, handbook §4): the doc carries item
                # identifiers but none match the requesting group — its only tie
                # is that it was fetched in this group's discovery context. Link
                # every member at the weakest basis; C5 caps fetch-context at
                # staged structurally (gate TRUSTED_BASES + item_document CHECK).
                # Rule 7 docs (no identifiers at all) stay link-less: blocking
                # manual, never silently staged.
                links = [{"item_ref": m["item_ref"], "match_basis": "fetch-context"}
                         for m in _group_members(conn, group_id)]
    else:
        ext_manufacturer = _val(fields, "manufacturer")
        if ext_manufacturer:
            # Canonicalize before scoping the group lookup — item_group.
            # canonical_manufacturer is the BC-vendor-code name (e.g.
            # 'IVOCLAR'), not the manufacturer's own spelling on its
            # documents (e.g. 'Ivoclar Vivadent AG'); see
            # _canonicalize_manufacturer for why this is read-only.
            canonical_manufacturer = _canonicalize_manufacturer(conn, ext_manufacturer)
            links, match_count, groups_found, primary = _links_for_manufacturer(
                conn, canonical_manufacturer, ref_list, basic_udi_di, basis_override)
            if links:
                ref_gate = True
                resolved_group_id = primary
                if match_count > 1:
                    # More than one scoped group genuinely passes C1 — resolving
                    # to the lowest group_id is deterministic but a doc that
                    # covers several groups must not silently auto-write to
                    # just one; the flag structurally caps disposition at
                    # staging via gate.py's existing flag-cap logic.
                    flags.append("multi-group-match")
                    multi_group_matches = match_count
            elif groups_found == 0:
                # Canonicalization (alias hit, or the raw-string fallback on a
                # miss) still didn't land on any group in the catalogue — the
                # single most likely reason a backfill document fails to
                # link. Counted and reported here, never left to be inferred
                # from an otherwise-unremarkable empty link list.
                flags.append("manufacturer-unresolved")
            else:
                # groups_found > 0 but nothing passed C1: we know whose document
                # this is, it just covers nothing Dentalia stocks. Used to fall
                # through with no flag and no count — indistinguishable, in
                # staging, from a document we failed to identify at all.
                flags.append("no-ref-overlap")
        elif ref_list or basic_udi_di:
            # No manufacturer to scope by ([validate-ref-catalogue]). A REF match
            # alone can still PROPOSE a link — but only through the capped
            # `ref-catalogue` basis, and only past the guards above.
            eligible = _eligible_unscoped_refs(ref_list, cfg.validate.min_unscoped_ref_len)
            holders = _manufacturers_holding_refs(conn, eligible)
            if len(holders) == 1:
                # basic_udi_di is deliberately NOT passed: this path is about
                # article numbers, and a Basic UDI-DI match keeps its existing
                # flag-only treatment rather than gaining a new basis here.
                scoped_links, match_count, _, primary = _links_for_manufacturer(
                    conn, holders[0], eligible, None, basis_override)
                if scoped_links:
                    # Rewrite every basis to the capped one: the pair
                    # (canonical_manufacturer, article number) was never
                    # verified — the manufacturer was INFERRED from the number.
                    # C5 and the item_document CHECK keep these off production.
                    links = [{**link, "match_basis": "ref-catalogue"}
                             for link in scoped_links]
                    if links:
                        resolved_group_id = primary
                        flags.append("ref-catalogue")
                        if match_count > 1:
                            flags.append("multi-group-match")
                            multi_group_matches = match_count
            elif len(holders) > 1:
                # A distributor's combined declaration, or a genuine collision
                # (measured: `878115` is held by both GC EUROPE N.V. and VOCO).
                # Picking one would attach a certificate to another company's
                # product — flag and stage, never choose.
                flags.append("multi-manufacturer-ref")
            if not links:
                # ref_gate stays False either way; these two flags only say WHY
                # the document staged link-less, which is what staging renders.
                if _resolve_group_unscoped(conn, ref_list, basic_udi_di) is not None:
                    # A hit exists but failed a guard (too short, prose, or
                    # ambiguous) — untrustworthy, never used.
                    flags.append("ref-unscoped")
                elif ref_list:
                    flags.append("no-ref-overlap")

    # Date sanity (rule 3): from <= to, and each date within a plausible year
    # band. The "N years from issue" arithmetic needs high-confidence rule text
    # the extraction schema does not currently carry — not implemented.
    validity_from_raw = _val(fields, "validity_from")
    validity_to_raw = _val(fields, "validity_to")
    validity_from = _to_date(validity_from_raw)
    validity_to = _to_date(validity_to_raw)
    # A value that will not parse is the FIRST thing checked, because until
    # 2026-08-17 it was checked nowhere: `_to_date` returns None both for "this
    # document states no date" and for "the model returned something that is not
    # a date", so an unparseable value skipped both branches below in silence and
    # went on to GATE, which casts it with `%s::date`. A Straumann IFU returned
    # `validity_from = "2025"` off the line "(c) Institut Straumann AG, 2025. All
    # rights reserved." and killed the job with `InvalidDatetimeFormat: invalid
    # input syntax for type date: "2025"`. An LLM must never be able to
    # dead-letter a document by returning a badly-shaped string.
    #
    # `date-insane` rather than a new flag: it already means "the date on this
    # document does not make sense", it is already BLOCKING, and an unparseable
    # date is the purest case of it. A new flag would be a vocabulary change for
    # no gain.
    if any(raw and parsed is None for raw, parsed in
           ((validity_from_raw, validity_from), (validity_to_raw, validity_to))):
        flags.append("date-insane")
    elif validity_from is not None and validity_to is not None and validity_from > validity_to:
        flags.append("date-insane")
    elif any(d is not None and not (_YEAR_MIN <= d.year <= _YEAR_MAX)
             for d in (validity_from, validity_to)):
        flags.append("date-insane")

    # C7 (never-downgrade) / C6 (supersession) — both keyed off the current
    # production document for the identical (coverage subject, type, regulation).
    supersedes = None
    related_doc_id = None
    superseded_by_doc_id = None
    if resolved_group_id is not None and doc_type and regulation:
        current = _current_production_doc(conn, resolved_group_id, doc_type, regulation,
                                          exclude_content_hash=content_hash)
        if current is not None:
            current_from = _to_date(current["validity_from"])
            if validity_from is None or current_from is None:
                flags.append("downgrade-uncomparable")
            elif validity_from < current_from:
                # Unambiguously older: both dates present, same coverage subject,
                # type and regulation. Invariant 4 says never downgrade, not never
                # keep: the document is still archived and evidenced, it just files
                # straight into the superseded chain instead of costing a human a
                # review — PROVIDED GATE's guard passes (no other blocking flag,
                # validity_from confidence >= HIGH). superseded_by_doc_id is the
                # fast-path signal; "older-than-current" stays too (kept, not
                # replaced) so that when the guard fails the candidate falls
                # through to GATE's normal BLOCKING_FLAGS handling exactly as it
                # did before this task, instead of silently reaching production.
                # The null-date cases above stay uncomparable and staged.
                superseded_by_doc_id = current["doc_id"]
                flags.append("older-than-current")
                flags.append("auto-superseded")
            elif validity_from > current_from:
                supersedes = current["doc_id"]
            else:
                # Equal, both present. Neither direction of supersession is
                # defensible (two revisions signed on one day carry no order
                # the dates can express), so this is a blocking flag and GATE
                # forces `manual` -- the person compares the two PDFs. Ruled
                # 2026-09-04; until then this case fell through silently.
                flags.append("same-date-revision")
        related = _related_production_doc(conn, resolved_group_id, doc_type, regulation)
        if related is not None:
            related_doc_id = related["doc_id"]

    # Rule 6: a DoC's expiry must be STATED, not inferred. No numeric confidence
    # factor is specified anywhere; the flag alone already keeps gate.py's
    # disposition off "production" (score/calibration is GATE's role, not
    # VALIDATE's — VALIDATE is zero-discretion pure rules).
    #
    # This rule tested the wrong thing until 2026-08-17. It asked whether the
    # declaration cited a certificate, on the reasoning that MDR Annex IV gives a
    # DoC an issue date and no expiry element at all, so an expiry with no
    # certificate behind it must be a misread. True of the regulation, false of
    # this corpus, and wrong in both directions:
    #
    #   * over-fires. Manufacturers do print expiry dates on declarations.
    #     Measured 2026-08-17 across every document in the registry carrying a
    #     validity_to — 156 of 156, three brands, four types — the date is
    #     labelled in its own evidence verbatim ("Valid until", "Expiry Date",
    #     "Scadenza / Valid until"). Fifteen IVOCLAR documents sat at `staged`
    #     with a score of 0.97 and a passing REF gate for citing no certificate.
    #   * under-fires. GC doc 227 reached `production` with "Leuven, 12 February
    #     2026" — a city and a SIGNING date — read as its expiry, because it
    #     happened to name cert `MDR 778483` and so passed this rule untouched.
    #     That is precisely the defect the rule existed to catch.
    #
    # The evidence verbatim answers the real question directly, so it replaces
    # the certificate proxy. Note the failure direction: an expiry phrase this
    # regex does not know keeps the flag ON, which caps at `staged` and puts a
    # human in front of it. An unrecognised language costs review effort, never
    # a wrong auto-write, so the list may be extended as brands land without
    # ever having been unsafe while short.
    if doc_type == "DoC" and validity_to is not None and not _expiry_is_stated(fields):
        flags.append("expiry-on-certless-doc")

    # Cited-certificate resolution (task 4): a DoC's real renewal date lives
    # on the notified-body certificate it cites (Annex IV item 8 / Article
    # 56), not on the declaration itself. Best-effort — an unresolved
    # certificate caps at staged (non-blocking) and self-corrects once the
    # certificate is gated (gate.py's back-resolution).
    manufacturer = _val(fields, "manufacturer")
    cert_doc_id, cert_from = (None, None)
    if doc_type == "DoC":
        cert_doc_id, cert_from = _resolve_cited_certificate(conn, fields, manufacturer)
        if cert_doc_id is None and (_val(fields, "cert_number")
                                    or _val(fields, "referenced_docs")):
            # Raised as an anomaly too, once the Result exists further down --
            # see the note there ([task4-cert-anomaly]).
            flags.append("cert-unresolved")

    candidate = {
        "content_hash": content_hash, "extract_rev": extract_rev,
        "group_id": resolved_group_id,
        "ref_gate": ref_gate, "flags": flags, "supersedes": supersedes, "links": links,
        "cert_doc_id": cert_doc_id, "superseded_by_doc_id": superseded_by_doc_id,
    }
    if related_doc_id is not None:
        candidate["related_doc_id"] = related_doc_id
    if archive_url:
        candidate["archive_url"] = archive_url

    queue.enqueue(
        conn, "gate.candidate", candidate,
        dedupe_key=f"gate:{content_hash}:{extract_rev}:{resolved_group_id}",
    )
    r = Result()
    r.count("emitted")
    # Two vocabularies, one detection, and until 2026-08-19 only one of them was
    # told ([task4-cert-anomaly]). The FLAG above is per-candidate and steers
    # GATE (INFORMATIONAL_FLAGS: it caps the disposition rather than blocking
    # it). The ANOMALY is a standing keyed row answering how often we cite
    # certificates we do not hold, and which numbers -- the /data-quality
    # question, which no flag can answer because a flag does not outlive its
    # candidate. It is raised here rather than beside the flag because the
    # normal path's Result does not exist yet at that point; `flags` carries the
    # decision down to here unchanged.
    if "cert-unresolved" in flags:
        r.anomaly("cert_reference_unresolved", subject=content_hash,
                  detail={"cert_number": _val(fields, "cert_number")})
    r.count("emitted_normal")
    if ref_gate:
        r.count("ref_gate_passed")
    for f in flags:
        # Flags cap the GATE disposition but were previously visible only in the
        # candidate payload, so a run could not say how many documents it staged
        # and for which reason without reading every job by hand.
        r.count(f"flag_{f.replace('-', '_')}")
    if flags:
        r.count("flagged")
        r.note("flags: " + ", ".join(flags))
    result = {"emitted": True, "route": "normal", "ref_gate": ref_gate, "flags": flags,
              **r.as_dict()}
    if multi_group_matches > 1:
        result["multi_group_matches"] = multi_group_matches
    return result


register("validate.doc", handle_validate_doc)
