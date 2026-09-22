"""Playbook <-> vendor-master reconciliation (S1.8). Read-only, always.

The gate between `playbooks validate` (are the files well-formed?) and
`playbooks sync` (write `manufacturer_alias`). It answers the question neither
of those asks: **do the authored playbooks actually join to anything BC emits?**

They mostly do not, today. The LJ export carries a manufacturer CODE
(`Šifra proizvajalca`) and no name, so `bc_codes` is the only join key that
exists -- `manufacturer` is not one. A playbook with no `bc_codes` is
unreachable no matter how good its `domains` and `doc_sources` are, and nothing
in the pipeline says so out loud: DISCOVER's `for_manufacturer` simply returns
None and the group falls through to an unrestricted search.

Why this is a separate command rather than part of `sync`: the first alias
write is the point of no return. `item_group.canonical_manufacturer` is
effectively write-once (RESOLVE's ladder short-circuits on `_existing_link`),
so a code that syncs to the wrong name before an ingest cannot be repaired by
re-running anything. The operator gets to see the diff, in items, first.

Entity, not code. `001`, `005` and `275` are all IVOCLAR VIVADENT, and a
playbook claiming only `001` would -- under per-code precedence -- split that
manufacturer into two disjoint namespaces that `_by_name_family` (scoped on
`canonical_manufacturer`) can never rejoin. So codes are grouped into entities
first: same vendor-master name, or co-claimed by one playbook. Findings and the
alias diff are computed at entity level, which is what `sync` must also do.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from app import playbooks as pb_mod

#: Below this rapidfuzz `ratio`, a "did you mean" is noise rather than a hint.
#: Measured against the real 387-name master: the one real typo in the tree
#: (`DENSTPLY` -> `DENTSPLY`) scores 88 and its runner-up 62, while a playbook
#: name that is legitimately absent from the master (`GC EUROPE N.V.`) tops out
#: at 50. Anything in between would be guesswork either way.
SUGGEST_MIN_SCORE = 80.0

#: Findings that mean "a catalogue item can never reach this playbook, or would
#: reach it under the wrong name". These set the non-zero exit.
BLOCKING = (
    "dead-playbook",
    "unjoined-playbook",
    "partial-claim",
    "alias-collision",
    "unknown-code",
)


@dataclass(frozen=True)
class Entity:
    """One real manufacturer: the codes BC issues for it, plus the playbook (if
    any) that claims it. `canonical` is what `sync` would write for every code."""

    codes: tuple[str, ...]
    vendor_names: tuple[str, ...]       # distinct master names over these codes
    slug: str | None                    # claiming playbook, if any
    canonical: str | None               # None only when nameless and unclaimed

    @property
    def authored(self) -> bool:
        return self.slug is not None


@dataclass(frozen=True)
class Finding:
    kind: str
    subject: str            # playbook slug, or code, depending on kind
    detail: str
    items: int = 0          # catalogue items affected, for triage order

    @property
    def blocking(self) -> bool:
        return self.kind in BLOCKING


@dataclass(frozen=True)
class AliasChange:
    code: str
    current: str | None     # manufacturer_alias.canonical_name today
    would_be: str
    source: str             # 'playbook' | 'vendor-master'
    items: int = 0

    @property
    def is_change(self) -> bool:
        return self.current != self.would_be


@dataclass(frozen=True)
class Report:
    entities: tuple[Entity, ...] = ()
    findings: tuple[Finding, ...] = ()
    alias_diff: tuple[AliasChange, ...] = ()
    uncovered: tuple[tuple[str, int], ...] = ()   # (code, items) with no master row
    vendor_codes: int = 0
    vendor_names: int = 0
    items_source: str = ""
    items_total: int = 0

    @property
    def blocking(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.blocking)

    @property
    def info(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if not f.blocking)

    @property
    def changes(self) -> tuple[AliasChange, ...]:
        return tuple(c for c in self.alias_diff if c.is_change)


# --------------------------------------------------------------------------- #
# entity construction
# --------------------------------------------------------------------------- #
class _Union:
    """Union-find over BC codes. Two edges join codes into one entity: sharing a
    vendor-master name, and being claimed by one playbook."""

    def __init__(self):
        self.parent: dict[str, str] = {}

    def add(self, code: str) -> None:
        self.parent.setdefault(code, code)

    def find(self, code: str) -> str:
        self.add(code)
        root = code
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[code] != root:      # path compression
            self.parent[code], code = root, self.parent[code]
        return root

    def join(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def _entities(vendor: dict[str, str | None], loaded) -> tuple[Entity, ...]:
    """`vendor` is code -> master name (None for the one nameless row)."""
    u = _Union()
    for code in vendor:
        u.add(code)

    by_name: dict[str, list[str]] = defaultdict(list)
    for code, name in vendor.items():
        if name is not None:
            by_name[name].append(code)
    for codes in by_name.values():
        for code in codes[1:]:
            u.join(codes[0], code)

    claimed_by: dict[str, str] = {}     # code -> slug
    for pb in loaded:
        codes = [bc.code for bc in pb.bc_codes]
        for code in codes:
            claimed_by[code] = pb.slug
            u.add(code)
        for code in codes[1:]:
            u.join(codes[0], code)

    grouped: dict[str, list[str]] = defaultdict(list)
    for code in list(u.parent):
        grouped[u.find(code)].append(code)

    authored = {pb.slug: pb.manufacturer for pb in loaded}
    out = []
    for codes in grouped.values():
        codes = tuple(sorted(codes))
        names = tuple(sorted({vendor[c] for c in codes if vendor.get(c) is not None}))
        slugs = sorted({claimed_by[c] for c in codes if c in claimed_by})
        slug = slugs[0] if slugs else None
        # Authored wins: the playbook name is under human control and does not
        # churn on a BC refresh. `playbooks.validate` has already refused two
        # playbooks claiming one code, so `slugs` cannot disagree here.
        canonical = authored[slug] if slug else (names[0] if names else None)
        out.append(Entity(codes=codes, vendor_names=names, slug=slug, canonical=canonical))
    return tuple(sorted(out, key=lambda e: e.codes))


def _suggest(name: str, candidates) -> tuple[str, float] | None:
    """Nearest master name, or None when the best is too weak to act on."""
    from rapidfuzz import fuzz, process

    if not candidates:
        return None
    hit = process.extractOne(name, list(candidates), scorer=fuzz.ratio)
    if hit is None or hit[1] < SUGGEST_MIN_SCORE:
        return None
    return hit[0], hit[1]


# --------------------------------------------------------------------------- #
# the report
# --------------------------------------------------------------------------- #
def build(
    vendor_rows,
    loaded,
    aliases: dict[str, str],
    item_counts: dict[str, int],
    *,
    items_source: str = "",
) -> Report:
    """Pure: everything the DB and the export contribute is already in the
    arguments. `vendor_rows` are `vendor_master.VendorRow`s, `loaded` are
    `playbooks.Playbook`s, `aliases` is raw_name -> canonical_name as stored
    today, `item_counts` is BC code -> catalogue items carrying it."""
    vendor = {r.code: r.name for r in vendor_rows}
    entities = _entities(vendor, loaded)
    names_to_entity = {n: e for e in entities for n in e.vendor_names}
    master_names = sorted(names_to_entity)
    brand_codes = {n: frozenset(e.codes) for n, e in names_to_entity.items()}

    def items(codes) -> int:
        return sum(item_counts.get(c, 0) for c in codes)

    findings: list[Finding] = []

    for pb in loaded:
        if not pb.bc_codes:
            # Nothing BC emits can reach this playbook: `manufacturer_alias` is
            # keyed on the raw code, and `sync` seeds it from `bc_codes` only.
            hit = names_to_entity.get(pb.manufacturer)
            if hit is None:
                for alias in pb.aliases:
                    hit = names_to_entity.get(alias)
                    if hit is not None:
                        break
            if hit is not None:
                findings.append(Finding(
                    kind="unjoined-playbook",
                    subject=pb.slug,
                    detail=(
                        f"no bc_codes, but {pb.manufacturer!r} is vendor-master "
                        f"code(s) {', '.join(hit.codes)}. Add them to bc_codes."
                    ),
                    items=items(hit.codes),
                ))
                continue
            near = _suggest(pb.manufacturer, master_names)
            hint = ""
            if near:
                other = names_to_entity[near[0]]
                hint = (
                    f" Nearest master name: {near[0]!r} (code(s) "
                    f"{', '.join(other.codes)}, {near[1]:.0f}% match, "
                    f"{items(other.codes)} item(s))."
                )
            findings.append(Finding(
                kind="dead-playbook",
                subject=pb.slug,
                detail=(
                    f"no bc_codes and {pb.manufacturer!r} matches no vendor-master "
                    f"name, so no catalogue item can ever reach it "
                    f"({len(pb.domains)} domain(s), {len(pb.doc_sources)} "
                    f"doc_source(s) unreachable).{hint}"
                ),
            ))
            continue

        # A claimed code the master never issued. Every claim is checked
        # against the one master: the per-catalogue filter went with the tag
        # itself (2026-08-26). `vendor_master.code_source` survives as a column
        # and is deliberately not the same question as an item's warehouse
        # (gap G11), but it holds one value and nobody chooses it.
        unknown = tuple(
            bc.code for bc in pb.bc_codes if bc.code not in vendor
        )
        if unknown:
            findings.append(Finding(
                kind="unknown-code",
                subject=pb.slug,
                detail=(
                    f"claims code(s) {', '.join(unknown)} that the BC "
                    f"master does not issue. A typo here syncs an alias no item "
                    f"will ever carry, and the playbook stays unreachable."
                ),
                items=items(unknown),
            ))

        entity = next((e for e in entities if e.slug == pb.slug), None)
        claimed = {bc.code for bc in pb.bc_codes}
        extra = tuple(c for c in entity.codes if c not in claimed) if entity else ()
        if extra:
            findings.append(Finding(
                kind="partial-claim",
                subject=pb.slug,
                detail=(
                    f"claims {', '.join(sorted(claimed))} of entity "
                    f"{'/'.join(entity.vendor_names) or '(nameless)'} "
                    f"{{{', '.join(entity.codes)}}}; sync would extend "
                    f"{pb.manufacturer!r} to {', '.join(extra)}. Claim them "
                    f"explicitly, or split the entity."
                ),
                items=items(extra),
            ))

        for name in entity.vendor_names if entity else ():
            if name != pb.manufacturer:
                findings.append(Finding(
                    kind="rename",
                    subject=pb.slug,
                    detail=(
                        f"code(s) {', '.join(entity.codes)} are {name!r} in the "
                        f"master; the playbook name {pb.manufacturer!r} wins."
                    ),
                    items=items(entity.codes),
                ))

        # CONTAINMENT, not equality, and over `names()` rather than `aliases`.
        # Equality is what this finding tested until 2026-08-27 and it cannot
        # see the failure the finding exists for: `[brand-parent-aliases]`'s
        # bad aliases were full legal names and the master carries bare brand
        # labels, so the strings never matched. One rule, shared with the
        # `BrandCollision` refusal `sync` now raises -- the report and the
        # refusal must not be able to disagree.
        for name in pb.names():
            hit = pb_mod.brand_collision(name, brand_codes, claimed=claimed)
            if hit is None:
                continue
            master, codes = hit
            if names_to_entity[master].slug == pb.slug:
                continue        # its own entity; `partial-claim` says it better
            findings.append(Finding(
                kind="alias-collision",
                subject=pb.slug,
                detail=(
                    f"{name!r} contains the master name {master!r} of a "
                    f"different entity (code(s) {', '.join(codes)}). Documents "
                    f"naming it would resolve to {pb.manufacturer!r} and never "
                    f"reach {master}'s own items. Resolve before syncing -- "
                    f"either claim those codes or drop the name."
                ),
                items=items(codes),
            ))

    uncovered = tuple(
        sorted(((c, n) for c, n in item_counts.items() if c not in vendor),
               key=lambda t: (-t[1], t[0]))
    )
    for code, n in uncovered:
        findings.append(Finding(
            kind="uncovered-code",
            subject=code,
            detail=f"carried by {n} catalogue item(s) but absent from the master.",
            items=n,
        ))

    diff = []
    for e in entities:
        if e.canonical is None:
            continue
        for code in e.codes:
            diff.append(AliasChange(
                code=code,
                current=aliases.get(code),
                would_be=e.canonical,
                source="playbook" if e.authored else "vendor-master",
                items=item_counts.get(code, 0),
            ))

    return Report(
        entities=entities,
        findings=tuple(findings),
        alias_diff=tuple(sorted(diff, key=lambda c: (-c.items, c.code))),
        uncovered=uncovered,
        vendor_codes=len(vendor),
        vendor_names=len({n for n in vendor.values() if n is not None}),
        items_source=items_source,
        items_total=sum(item_counts.values()),
    )


# --------------------------------------------------------------------------- #
# inputs
# --------------------------------------------------------------------------- #
def derive_aliases(vendor_rows, loaded):
    """What `playbooks sync` should write: BC code -> (canonical_name, source).

    Entity-level, for the reason the whole module exists: resolving each code
    independently would let a playbook claiming one code of a multi-code
    manufacturer split it into two `canonical_manufacturer` namespaces.

    Returns `(intended, skipped)`. `skipped` holds codes two playbooks claim
    with DIFFERENT manufacturers. Those codes are dropped and counted rather
    than letting whichever sorts last win, and dropped BEFORE entities are
    built so a bad claim cannot merge two real manufacturers into one entity.

    Since 2026-08-26 `playbooks.validate` refuses that pair outright -- the
    conflict key used to carry a catalogue tag, which made a cross-catalogue
    collision legal as files and left this the only place it could be caught.
    `sync_aliases` validates first, so `skipped` is empty on that path now.
    The guard stays because this function is callable without that gate, and
    what it prevents -- two real manufacturers merged into one entity -- is not
    something to leave to the caller.
    """
    from dataclasses import replace

    claims: dict[str, set[str]] = {}
    for pb in loaded:
        for bc in pb.bc_codes:
            claims.setdefault(bc.code, set()).add(pb.manufacturer)
    conflicted = {code for code, names in claims.items() if len(names) > 1}

    filtered = [
        replace(pb, bc_codes=tuple(b for b in pb.bc_codes if b.code not in conflicted))
        for pb in loaded
    ]

    vendor = {r.code: r.name for r in vendor_rows}
    intended: dict[str, tuple[str, str]] = {}
    for e in _entities(vendor, filtered):
        if e.canonical is None:
            continue                      # nameless, unclaimed: nothing to project
        for code in e.codes:
            intended[code] = (e.canonical, "playbook" if e.authored else "vendor-master")

    skipped = tuple(
        (code, f"claimed as {sorted(claims[code])} by different playbooks; "
               "manufacturer_alias is not namespaced by catalogue (G11)")
        for code in sorted(conflicted)
    )
    return intended, skipped


def counts_from_export(path: str, catalogue: str = "LJ") -> dict[str, int]:
    """Items per manufacturer code, straight from a BC export.

    Drives the pipeline's own `CsvExportAdapter`, so these are the codes INGEST
    would write to `item_mirror.manufacturer_raw` -- not a parallel reader that
    could disagree about, say, a leading zero. Needed because the useful moment
    to run this report is BEFORE the first ingest, when `item_mirror` is empty.
    """
    from app.adapters.source import CsvExportAdapter
    from app.config import Ingest

    counts: dict[str, int] = defaultdict(int)
    for row in CsvExportAdapter(path, catalogue, Ingest()).read():
        if row.manufacturer_raw:
            counts[row.manufacturer_raw] += 1
    return dict(counts)


def counts_from_mirror(conn) -> dict[str, int]:
    """Items per manufacturer code as ingested. Not filtered by catalogue:
    `manufacturer_alias` is not namespaced either (gap G11), so the report must
    show the same collapsed view `sync` would write."""
    rows = conn.execute(
        "SELECT manufacturer_raw, count(*) AS n FROM item_mirror "
        "GROUP BY manufacturer_raw"
    ).fetchall()
    return {r["manufacturer_raw"]: r["n"] for r in rows}


def load_aliases(conn) -> dict[str, str]:
    rows = conn.execute(
        "SELECT raw_name, canonical_name FROM manufacturer_alias"
    ).fetchall()
    return {r["raw_name"]: r["canonical_name"] for r in rows}


def load_vendor_master(conn):
    from app.vendor_master import DEFAULT_CODE_SOURCE, VendorRow

    rows = conn.execute(
        "SELECT code, name FROM vendor_master WHERE code_source=%s ORDER BY code",
        (DEFAULT_CODE_SOURCE,),
    ).fetchall()
    return tuple(VendorRow(code=r["code"], name=r["name"]) for r in rows)


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def render(report: Report, *, limit: int = 40) -> list[str]:
    """Report as lines. Blocking findings first, ordered by items affected --
    the operator fixes the playbook that costs the most catalogue first."""
    out = [
        f"vendor master: {report.vendor_codes} code(s), "
        f"{report.vendor_names} distinct name(s), "
        f"{sum(1 for e in report.entities if len(e.codes) > 1)} multi-code entit(ies)",
        f"item counts from: {report.items_source} ({report.items_total} item(s))",
        "",
    ]

    blocking = sorted(report.blocking, key=lambda f: (-f.items, f.kind, f.subject))
    if blocking:
        out.append(f"BLOCKING ({len(blocking)})")
        for f in blocking:
            items = f" [{f.items} item(s)]" if f.items else ""
            out.append(f"  [{f.kind}] {f.subject}{items}")
            out.append(f"      {f.detail}")
    else:
        out.append("BLOCKING (0): every playbook joins to the catalogue.")
    out.append("")

    info = sorted(report.info, key=lambda f: (-f.items, f.kind, f.subject))
    if info:
        out.append(f"INFO ({len(info)})")
        for f in info:
            out.append(f"  [{f.kind}] {f.subject}: {f.detail}")
        out.append("")

    changes = report.changes
    out.append(
        f"would-be alias diff: {len(changes)} of {len(report.alias_diff)} code(s) "
        f"change, {sum(c.items for c in changes)} item(s) affected"
    )
    if changes:
        out.append(f"  {'code':<10} {'items':>7}  {'current':<24} {'would become':<28} source")
        for c in changes[:limit]:
            current = c.current if c.current is not None else "(none)"
            out.append(
                f"  {c.code:<10} {c.items:>7}  {current:<24} {c.would_be:<28} {c.source}"
            )
        if len(changes) > limit:
            out.append(f"  ... {len(changes) - limit} more (raise --limit to see them)")
    return out
