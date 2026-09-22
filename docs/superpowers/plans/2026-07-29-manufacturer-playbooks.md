# Per-manufacturer playbooks + BC identity seed — Implementation Plan

**Goal:** Give every manufacturer one git-versioned config file (`playbooks/{slug}.json`) carrying its BC codes, domains and document-source URLs, and seed `manufacturer_alias` from it so `item_group.canonical_manufacturer` stops being a bare BC code.

**Architecture:** Authored config lives in git JSON; the database holds only derived state. `app/playbooks.py` owns the identity/URL view of those files and never raises in the runtime path. `app/extract/t0_layout.py` keeps its own template view of the same directory, because a malformed parse template must not break identity lookup and vice versa. A new `dentalia playbooks sync` CLI command projects `bc_codes` into `manufacturer_alias`, which is what makes every name-keyed mechanism in the pipeline start matching.

**Tech Stack:** Python 3.12 (declared), psycopg 3 with raw SQL, argparse CLI, pytest against a real Postgres.

Design spec: [2026-07-29-manufacturer-playbooks-design.md](../specs/2026-07-29-manufacturer-playbooks-design.md). Read §1 to §6 before starting.

## Global Constraints

- **No new job types.** The `job_type` enum is closed (invariant 7). Nothing in this plan enqueues a new tag.
- **No mocking Postgres.** Tests use the real `dentalia_test` database via the `conn` / `connect_test` / `test_db_url` fixtures in `tests/conftest.py`.
- **This plan adds no migration.** The catalogue-namespacing migration was cut on 2026-07-29 before implementation: Denis confirmed LJ and ZG are intended to converge into one BC, so keying `manufacturer_alias` on the warehouse tag would fragment a single code namespace rather than protect it. Deferred to gap G11 (`PHASES.md`, owner: client). See design spec §3.
- **Run tests with `/srv/dentalia/.venv/bin/pytest` from the worktree root.** The worktree has no `.venv` of its own, and the `dentalia` package is not pip-installed, so `app` resolves from the current directory: the main repo's venv binary runs the worktree's code. Verified before Task A (16 passed on `tests/test_t0_layout.py`). That venv is Python 3.11.2 while `pyproject.toml` declares `>=3.12` (tracked in `tasks/followups.md` `[venv-python-version]`). Do not "fix" this as part of these tasks.
- **Baseline is 699 passed, 1 skipped.** Any task that ends with fewer passing tests than it started with, other than tests it deliberately rewrote, is a regression.
- **Commit messages must never reference Claude, AI, or contain "claude".**
- **`manufacturer` in a playbook is the legal manufacturer where it is documented**, otherwise keep the existing brand string and list the file under "Still to author" in `playbooks/README.md`. Brand and product-line names go in `aliases`. Never invent a legal entity name that no source states.
- **Additive-only payloads and config.** Consumers tolerate unknown fields, never missing ones.

---

## File Structure

| Path | Responsibility | Task |
|---|---|---|
| `app/playbooks.py` | Load and validate playbook files; expose the identity/URL view (`Playbook`, `load_playbooks`, `validate`, `for_manufacturer`). Owns `PLAYBOOKS_DIR`. | A |
| `playbooks/*.json` | The authored per-manufacturer config. One file per manufacturer. | A |
| `tests/test_playbooks.py` | Loader, validation, and a guard that the real `playbooks/` dir validates. | A |
| `app/cli.py` | `dentalia playbooks sync` and `dentalia playbooks validate`. | B |
| `tests/test_playbooks_sync.py` | Sync idempotency, conflict handling, orphan reporting. | B |
| `app/handlers/discover.py` | `site:` restriction, zero-result fallback, portal URLs in manual prefill. | C |
| `app/extract/t0_layout.py` | `slug` on `Template`; silent skip for files with no parse section; default dir repointed to `playbooks/`. | D |
| `Dockerfile`, `pyproject.toml` | Ship `playbooks/` into the worker image and the wheel. | D |

**Ordering:** A must land before B (sync reads playbook files). C and D depend on A only, and are independent of each other and of B.

---

### Task A: Playbook store and loader

**Files:**
- Create: `app/playbooks.py`
- Create: `playbooks/voco.json`, `playbooks/komet.json`, `playbooks/ivoclar.json`, `playbooks/gc.json`, `playbooks/dentsply-sirona.json`
- Test: `tests/test_playbooks.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces, relied on by B, C and D:
  - `app.playbooks.PLAYBOOKS_DIR: pathlib.Path`
  - `class BcCode` with fields `catalogue: str`, `code: str`
  - `class DocSource` with fields `doc_type: str`, `kind: str`, `url: str`, `note: str | None`
  - `class Playbook` with fields `slug: str`, `manufacturer: str`, `bc_codes: tuple[BcCode, ...]`, `aliases: tuple[str, ...]`, `domains: tuple[str, ...]`, `doc_sources: tuple[DocSource, ...]`
  - `load_playbooks(dir_path: pathlib.Path | str | None = None) -> tuple[Playbook, ...]` — never raises; skips and warns on a malformed file
  - `validate(playbooks: tuple[Playbook, ...]) -> None` — raises `PlaybookConflict`
  - `for_manufacturer(name: str, playbooks: tuple[Playbook, ...] | None = None) -> Playbook | None` — matches `manufacturer` or any alias, case-insensitively
  - `class PlaybookConflict(Exception)`

Why two loaders over one directory: `load_playbooks` must never raise in DISCOVER's path, while `validate` must raise loudly for the operator. Keeping the parse-template view in `t0_layout.py` means a malformed `ref_strategy` cannot break identity lookup, and a malformed `bc_codes` cannot break extraction.

- [ ] **Step 1: Write the failing test**

Create `tests/test_playbooks.py`:

```python
"""Playbook store (S1.7): the authored per-manufacturer config in `playbooks/`.

`load_playbooks` is the runtime path and never raises; `validate` is the
operator path and raises loudly on cross-file conflicts.
"""

from __future__ import annotations

import json

import pytest

from app import playbooks


def _write(dir_path, filename, data):
    (dir_path / filename).write_text(json.dumps(data))


VOCO = {
    "manufacturer": "VOCO GmbH",
    "bc_codes": [{"catalogue": "LJ", "code": "062"}],
    "aliases": ["VOCO"],
    "domains": ["voco.dental"],
    "doc_sources": [
        {"doc_type": "doc", "kind": "portal", "url": "https://www.voco.dental/downloads"}
    ],
}


def test_load_empty_dir_returns_empty(tmp_path):
    assert playbooks.load_playbooks(tmp_path) == ()


def test_load_missing_dir_returns_empty(tmp_path):
    assert playbooks.load_playbooks(tmp_path / "nope") == ()


def test_load_parses_all_fields(tmp_path):
    _write(tmp_path, "voco.json", VOCO)

    (pb,) = playbooks.load_playbooks(tmp_path)

    assert pb.slug == "voco"
    assert pb.manufacturer == "VOCO GmbH"
    assert pb.bc_codes[0].catalogue == "LJ"
    assert pb.bc_codes[0].code == "062"
    assert pb.aliases == ("VOCO",)
    assert pb.domains == ("voco.dental",)
    assert pb.doc_sources[0].kind == "portal"
    assert pb.doc_sources[0].doc_type == "doc"


def test_load_tolerates_missing_optional_sections(tmp_path):
    _write(tmp_path, "bare.json", {"manufacturer": "Bare Co"})

    (pb,) = playbooks.load_playbooks(tmp_path)

    assert pb.manufacturer == "Bare Co"
    assert pb.bc_codes == ()
    assert pb.domains == ()
    assert pb.doc_sources == ()


def test_load_tolerates_parse_template_keys(tmp_path):
    """t0_layout owns `match`/`ref_strategy`; the identity loader ignores them."""
    _write(tmp_path, "komet.json", dict(VOCO, match={"anchors": ["x"]}, ref_strategy="table"))

    (pb,) = playbooks.load_playbooks(tmp_path)

    assert pb.manufacturer == "VOCO GmbH"


def test_load_skips_malformed_and_never_raises(tmp_path):
    _write(tmp_path, "good.json", VOCO)
    (tmp_path / "bad.json").write_text("{not json")
    (tmp_path / "nameless.json").write_text(json.dumps({"domains": ["x.com"]}))

    loaded = playbooks.load_playbooks(tmp_path)

    assert [p.manufacturer for p in loaded] == ["VOCO GmbH"]


def test_validate_rejects_duplicate_bc_code(tmp_path):
    _write(tmp_path, "a.json", VOCO)
    _write(tmp_path, "b.json", dict(VOCO, manufacturer="Other GmbH", aliases=[]))

    with pytest.raises(playbooks.PlaybookConflict) as exc:
        playbooks.validate(playbooks.load_playbooks(tmp_path))

    assert "LJ/062" in str(exc.value)


def test_validate_allows_same_code_in_different_catalogues(tmp_path):
    _write(tmp_path, "a.json", VOCO)
    _write(
        tmp_path,
        "b.json",
        dict(VOCO, manufacturer="Other GmbH", aliases=[],
             bc_codes=[{"catalogue": "ZG", "code": "062"}]),
    )

    playbooks.validate(playbooks.load_playbooks(tmp_path))


def test_validate_rejects_duplicate_manufacturer(tmp_path):
    _write(tmp_path, "a.json", VOCO)
    _write(tmp_path, "b.json", dict(VOCO, bc_codes=[], aliases=[]))

    with pytest.raises(playbooks.PlaybookConflict) as exc:
        playbooks.validate(playbooks.load_playbooks(tmp_path))

    assert "VOCO GmbH" in str(exc.value)


def test_validate_rejects_alias_colliding_with_another_manufacturer(tmp_path):
    _write(tmp_path, "a.json", VOCO)
    _write(
        tmp_path,
        "b.json",
        dict(VOCO, manufacturer="Rival GmbH", bc_codes=[], aliases=["VOCO GmbH"]),
    )

    with pytest.raises(playbooks.PlaybookConflict):
        playbooks.validate(playbooks.load_playbooks(tmp_path))


def test_for_manufacturer_matches_canonical_and_alias(tmp_path):
    _write(tmp_path, "voco.json", VOCO)
    loaded = playbooks.load_playbooks(tmp_path)

    assert playbooks.for_manufacturer("VOCO GmbH", loaded).slug == "voco"
    assert playbooks.for_manufacturer("VOCO", loaded).slug == "voco"
    assert playbooks.for_manufacturer("voco gmbh", loaded).slug == "voco"
    assert playbooks.for_manufacturer("062", loaded) is None
    assert playbooks.for_manufacturer("Unknown", loaded) is None


def test_shipped_playbooks_are_valid():
    """Authoring guard: the real playbooks/ dir must always validate."""
    loaded = playbooks.load_playbooks()

    assert loaded, "playbooks/ is empty or unreadable"
    playbooks.validate(loaded)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/srv/dentalia/.venv/bin/pytest tests/test_playbooks.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'app.playbooks'`.

- [ ] **Step 3: Implement the loader**

Create `app/playbooks.py`:

```python
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
Oberflaechentechnik; MIYO is legally Jensen Dental).
"""

from __future__ import annotations

import json
import logging
import pathlib
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Repo-root `playbooks/`, per the CLAUDE.md target layout. Shipped into the
# worker image by an explicit Dockerfile COPY and into the wheel by
# force-include -- it is NOT inside a package directory.
PLAYBOOKS_DIR = pathlib.Path(__file__).resolve().parent.parent / "playbooks"

DOC_SOURCE_KINDS = ("portal", "direct")


class PlaybookConflict(Exception):
    """Two playbooks claim the same BC code or the same manufacturer name."""


@dataclass(frozen=True)
class BcCode:
    catalogue: str
    code: str


@dataclass(frozen=True)
class DocSource:
    doc_type: str
    kind: str
    url: str
    note: str | None = None


@dataclass(frozen=True)
class Playbook:
    slug: str
    manufacturer: str
    bc_codes: tuple[BcCode, ...] = ()
    aliases: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    doc_sources: tuple[DocSource, ...] = ()

    def names(self) -> tuple[str, ...]:
        """Every string that should resolve to this playbook."""
        return (self.manufacturer,) + self.aliases


def _parse(slug: str, data: dict) -> Playbook:
    manufacturer = data["manufacturer"]
    if not isinstance(manufacturer, str) or not manufacturer.strip():
        raise ValueError("manufacturer must be a non-empty string")

    codes = []
    for entry in data.get("bc_codes", []):
        codes.append(BcCode(catalogue=str(entry["catalogue"]), code=str(entry["code"])))

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

    return Playbook(
        slug=slug,
        manufacturer=manufacturer.strip(),
        bc_codes=tuple(codes),
        aliases=tuple(str(a) for a in data.get("aliases", [])),
        domains=tuple(str(d) for d in data.get("domains", [])),
        doc_sources=tuple(sources),
    )


def load_playbooks(dir_path: pathlib.Path | str | None = None) -> tuple[Playbook, ...]:
    """Every readable playbook, sorted by slug. Never raises: a malformed file
    is logged and skipped, so one bad file cannot take DISCOVER down. Use
    `validate()` when the caller is an operator who wants to be told."""
    dir_path = pathlib.Path(dir_path if dir_path is not None else PLAYBOOKS_DIR)
    if not dir_path.is_dir():
        log.warning("playbooks: directory %s does not exist", dir_path)
        return ()

    out = []
    for f in sorted(dir_path.glob("*.json")):
        try:
            out.append(_parse(f.stem, json.loads(f.read_text())))
        except Exception:
            log.warning("playbooks: skipping malformed playbook %s", f, exc_info=True)
    return tuple(out)


def validate(playbooks: tuple[Playbook, ...]) -> None:
    """Raise on cross-file conflicts. Authoring errors, not runtime conditions."""
    seen_codes: dict[tuple[str, str], str] = {}
    seen_names: dict[str, str] = {}

    for pb in playbooks:
        for bc in pb.bc_codes:
            key = (bc.catalogue, bc.code)
            if key in seen_codes:
                raise PlaybookConflict(
                    f"BC code {bc.catalogue}/{bc.code} claimed by both "
                    f"{seen_codes[key]!r} and {pb.slug!r}"
                )
            seen_codes[key] = pb.slug

        for name in pb.names():
            low = name.casefold()
            if low in seen_names:
                raise PlaybookConflict(
                    f"name {name!r} claimed by both {seen_names[low]!r} and {pb.slug!r}"
                )
            seen_names[low] = pb.slug


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
```

- [ ] **Step 4: Author the five playbooks that already have parse templates**

Create `playbooks/` and one file per manufacturer. Copy `match`, `ref_strategy` and `ref_strategy_config` **verbatim** from the matching file in `app/extract/t0_layout/` so Task D's directory switch is a pure move with no behaviour change. Read each source file first; do not retype the anchors from memory.

`playbooks/voco.json` (merge `app/extract/t0_layout/voco.json` into this shape):

```json
{
  "manufacturer": "VOCO GmbH",
  "aliases": ["VOCO"],
  "bc_codes": [],
  "domains": ["voco.dental"],
  "doc_sources": [],
  "match": {"anchors": ["COPY FROM app/extract/t0_layout/voco.json"]},
  "ref_strategy": "table"
}
```

Rules for this step:

- `manufacturer` is the legal entity. Where the corpus analysis states it, use that: [dentalia-imports-corpus-analysis.md §6](../../dentalia-imports-corpus-analysis.md) records "GC EUROPE N.V." as GC's legal manufacturer. Where you do not know it, leave the existing brand string from the t0_layout file and add the file to the authoring checklist in Step 6 rather than inventing a legal name.
- `bc_codes` stays `[]` unless the code is CONFIRMED or LIKELY in [dentalia-manufacturer-code-sweep.md](../../dentalia-manufacturer-code-sweep.md). WEAK is not good enough to auto-seed an alias.
- `domains` and `doc_sources` may be `[]`. Empty is honest; a guessed URL is not.
- Slug is a short stable brand handle (`voco`, `komet`, `ivoclar`, `gc`, `dentsply-sirona`), never derived from the legal name, because legal names change on acquisition and a renamed file loses its git history.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `/srv/dentalia/.venv/bin/pytest tests/test_playbooks.py -q`
Expected: PASS, 12 tests. `test_shipped_playbooks_are_valid` proves the five authored files load and do not conflict.

- [ ] **Step 6: Add the authoring checklist**

Create `playbooks/README.md`:

```markdown
# Playbooks

One file per manufacturer. `{slug}.json`, slug is a short stable brand handle.

Authored by hand; `dentalia playbooks validate` checks the whole directory.

## Fields

| Field | Required | Meaning |
|---|---|---|
| `manufacturer` | yes | The LEGAL manufacturer, as it appears on the DoC. Not the brand. |
| `aliases` | no | Brand and product-line names. PANTHER is an alias of SUN Oberflaechentechnik. |
| `bc_codes` | no | `[{"catalogue": "LJ", "code": "001"}]`. CONFIRMED or LIKELY tiers only. Seeds `manufacturer_alias`. |
| `domains` | no | Official domains, hostname only. Restricts DISCOVER's search query. |
| `doc_sources` | no | `[{"doc_type", "kind", "url", "note"}]`. `kind` is `portal` or `direct`. |
| `match`, `ref_strategy`, `ref_strategy_config` | no | T0 parse template, read by `app/extract/t0_layout.py`. |

Empty is honest. A guessed URL is worse than no URL.

## Still to author

Track per-manufacturer research here; delete a line when its file is complete.
```

- [ ] **Step 7: Commit**

```bash
git add app/playbooks.py playbooks/ tests/test_playbooks.py
git commit -m "feat: per-manufacturer playbook store

playbooks/{slug}.json is the single home for authored per-manufacturer
config. load_playbooks never raises (DISCOVER path); validate raises on
duplicate BC codes and duplicate names (operator path).

Seeds the five manufacturers that already have T0 parse templates; their
match/ref_strategy blocks are copied verbatim so the Task D directory
switch is a pure move."
```

---

### Task B: `playbooks sync` and the alias seed

**Files:**
- Modify: `app/cli.py` (imports, `build_parser`, new `cmd_playbooks` and `sync_aliases`)
- Test: `tests/test_playbooks_sync.py`

**Interfaces:**
- Consumes: `app.playbooks.load_playbooks`, `validate`, `PlaybookConflict`, `Playbook.bc_codes`, `Playbook.manufacturer`.
- Produces:
  - `app.cli.cmd_playbooks(args) -> int`
  - `app.cli.sync_aliases(conn, loaded) -> dict` returning `{"inserted": int, "updated": int, "unchanged": int, "orphaned_groups": int}`

**Do not touch `app/handlers/resolve.py` in this task.** `_alias_lookup(conn, raw)` keeps its current two-argument signature. The catalogue-namespacing migration that would have changed it was cut (see Global Constraints).

Two design points that follow from cutting the migration:

1. **Seed `bc_codes` only, not `aliases`.** Both LJ adapter profiles supply a manufacturer *code*, never a name (`app/adapters/source.py` maps `Šifra proizvajalca` for CSV and `ManufacturerCode` for OData), so a `manufacturer_alias` row keyed on the raw string `"VOCO"` can never be hit by anything. Aliases stay an in-memory concern for `playbooks.for_manufacturer`, which Task C uses.
2. **Sync must fail loudly when two catalogues claim one code.** `playbooks.validate` deliberately permits `{"LJ","062"}` and `{"ZG","062"}` on different manufacturers, because the file format is namespaced. The table is not. Rather than let one silently overwrite the other, sync raises. That turns the deferred G11 risk into a loud failure at the exact moment it becomes real, which is what makes deferring the migration safe.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_playbooks_sync.py`:

```python
"""`dentalia playbooks sync`: projects playbook bc_codes/aliases into
manufacturer_alias. This is what turns item_group.canonical_manufacturer from
the bare BC code '001' into the manufacturer's real name.
"""

from __future__ import annotations

import json

import pytest

from app import cli, playbooks
from app.handlers.resolve import _alias_lookup


def _write(dir_path, filename, data):
    (dir_path / filename).write_text(json.dumps(data))


IVOCLAR = {
    "manufacturer": "Ivoclar Vivadent AG",
    "aliases": ["IVOCLAR"],
    "bc_codes": [{"catalogue": "LJ", "code": "001"}],
}


def test_sync_seeds_bc_codes_only(conn, tmp_path):
    """Both LJ adapter profiles supply a manufacturer CODE, never a name, so a
    row keyed on the raw string 'IVOCLAR' is unreachable and is not written."""
    _write(tmp_path, "ivoclar.json", IVOCLAR)

    stats = cli.sync_aliases(conn, playbooks.load_playbooks(tmp_path))

    assert stats["inserted"] == 1
    rows = conn.execute(
        "SELECT raw_name, canonical_name FROM manufacturer_alias"
    ).fetchall()
    assert [(r["raw_name"], r["canonical_name"]) for r in rows] == [
        ("001", "Ivoclar Vivadent AG")
    ]


def test_sync_is_idempotent(conn, tmp_path):
    _write(tmp_path, "ivoclar.json", IVOCLAR)
    loaded = playbooks.load_playbooks(tmp_path)

    cli.sync_aliases(conn, loaded)
    second = cli.sync_aliases(conn, loaded)

    assert second["inserted"] == 0
    assert second["updated"] == 0
    assert second["unchanged"] == 1


def test_sync_overwrites_the_self_seeded_row(conn, tmp_path):
    """RESOLVE self-seeds ('001','001') on a miss; sync must correct it. This is
    the whole point of the command: it turns canonical_manufacturer from the
    bare BC code into the manufacturer's real name."""
    canonical, created = _alias_lookup(conn, "001")
    assert (canonical, created) == ("001", True)

    _write(tmp_path, "ivoclar.json", IVOCLAR)
    stats = cli.sync_aliases(conn, playbooks.load_playbooks(tmp_path))

    assert stats["updated"] == 1
    assert _alias_lookup(conn, "001") == ("Ivoclar Vivadent AG", False)


def test_sync_refuses_conflicting_playbooks_and_writes_nothing(conn, tmp_path):
    _write(tmp_path, "a.json", IVOCLAR)
    _write(tmp_path, "b.json", dict(IVOCLAR, manufacturer="Rival AG", aliases=[]))

    with pytest.raises(playbooks.PlaybookConflict):
        cli.sync_aliases(conn, playbooks.load_playbooks(tmp_path))

    assert conn.execute("SELECT count(*) AS n FROM manufacturer_alias").fetchone()["n"] == 0


def test_sync_refuses_one_code_claimed_by_two_catalogues(conn, tmp_path):
    """The playbook format namespaces bc_codes by catalogue; manufacturer_alias
    does not (the namespacing migration is deferred to G11). Sync must refuse
    rather than let one catalogue's meaning silently overwrite the other's."""
    _write(tmp_path, "ivoclar.json", IVOCLAR)
    _write(
        tmp_path,
        "zagreb.json",
        {"manufacturer": "Zagreb Maker d.o.o.",
         "bc_codes": [{"catalogue": "ZG", "code": "001"}]},
    )

    loaded = playbooks.load_playbooks(tmp_path)
    playbooks.validate(loaded)          # valid as FILES: catalogues differ

    with pytest.raises(playbooks.PlaybookConflict) as exc:
        cli.sync_aliases(conn, loaded)

    assert "001" in str(exc.value)
    assert conn.execute("SELECT count(*) AS n FROM manufacturer_alias").fetchone()["n"] == 0


def test_sync_allows_one_code_repeated_for_the_same_manufacturer(conn, tmp_path):
    """Same code in both catalogues for the SAME manufacturer is the converged
    case Denis described, and is fine: one row, one meaning."""
    _write(
        tmp_path,
        "ivoclar.json",
        dict(IVOCLAR, bc_codes=[{"catalogue": "LJ", "code": "001"},
                                {"catalogue": "ZG", "code": "001"}]),
    )

    stats = cli.sync_aliases(conn, playbooks.load_playbooks(tmp_path))

    assert stats["inserted"] == 1


def test_sync_reports_orphaned_groups(conn, tmp_path):
    """Re-pointing an alias does not retro-fix groups already resolved under the
    old value. Sync must say so instead of leaving it silent."""
    conn.execute(
        "INSERT INTO item_group (canonical_manufacturer, label) VALUES ('001','old')"
    )
    _write(tmp_path, "ivoclar.json", IVOCLAR)

    stats = cli.sync_aliases(conn, playbooks.load_playbooks(tmp_path))

    assert stats["orphaned_groups"] == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/srv/dentalia/.venv/bin/pytest tests/test_playbooks_sync.py -q`
Expected: FAIL, `AttributeError: module 'app.cli' has no attribute 'sync_aliases'`.

- [ ] **Step 3: Implement `sync_aliases` and the CLI command**

In `app/cli.py`, add to the imports:

```python
from app import db, playbooks, queue
```

Add above `build_parser`:

```python
def sync_aliases(conn, loaded) -> dict:
    """Project playbook bc_codes into manufacturer_alias.

    Only bc_codes: both LJ adapter profiles supply a manufacturer CODE, never a
    name (app/adapters/source.py), so a row keyed on the raw string 'VOCO'
    could never be hit. `aliases` stay an in-memory concern for
    playbooks.for_manufacturer.

    Validates first and writes nothing on conflict: a duplicate BC code is an
    authoring error that would silently map items to the wrong manufacturer.

    Reports `orphaned_groups` -- item_group rows still carrying a
    canonical_manufacturer this sync has just re-pointed. Re-pointing an alias
    does NOT retro-fix groups already resolved under the old value, so run sync
    BEFORE the ingest, and re-resolve if this count is non-zero.
    """
    playbooks.validate(loaded)

    # The playbook format namespaces bc_codes by catalogue; manufacturer_alias
    # does not (namespacing deferred to gap G11). Collapse to raw code -> name
    # and refuse if two catalogues disagree about what one code means, rather
    # than letting whichever sorts last silently win.
    intended: dict[str, str] = {}
    for pb in loaded:
        for bc in pb.bc_codes:
            prior = intended.get(bc.code)
            if prior is not None and prior != pb.manufacturer:
                raise playbooks.PlaybookConflict(
                    f"BC code {bc.code} means {prior!r} in one catalogue and "
                    f"{pb.manufacturer!r} in another. manufacturer_alias is not "
                    "namespaced by catalogue (gap G11); resolve the codes or land "
                    "the namespacing migration before syncing."
                )
            intended[bc.code] = pb.manufacturer

    stats = {"inserted": 0, "updated": 0, "unchanged": 0, "orphaned_groups": 0}
    remapped: list[str] = []

    for raw, manufacturer in intended.items():
        row = conn.execute(
            "SELECT canonical_name FROM manufacturer_alias WHERE raw_name=%s",
            (raw,),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO manufacturer_alias (raw_name, canonical_name) VALUES (%s,%s)",
                (raw, manufacturer),
            )
            stats["inserted"] += 1
            remapped.append(raw)
        elif row["canonical_name"] != manufacturer:
            conn.execute(
                "UPDATE manufacturer_alias SET canonical_name=%s WHERE raw_name=%s",
                (manufacturer, raw),
            )
            stats["updated"] += 1
            remapped.append(raw)
        else:
            stats["unchanged"] += 1

    if remapped:
        row = conn.execute(
            "SELECT count(*) AS n FROM item_group WHERE canonical_manufacturer = ANY(%s)",
            (remapped,),
        ).fetchone()
        stats["orphaned_groups"] = row["n"]

    return stats


def cmd_playbooks(args: argparse.Namespace) -> int:
    loaded = playbooks.load_playbooks()
    if not loaded:
        print(f"no playbooks found in {playbooks.PLAYBOOKS_DIR}", file=sys.stderr)
        return 2

    if args.action == "validate":
        try:
            playbooks.validate(loaded)
        except playbooks.PlaybookConflict as exc:
            print(f"playbook conflict: {exc}", file=sys.stderr)
            return 2
        print(f"{len(loaded)} playbook(s) valid")
        return 0

    with db.connect() as conn:
        try:
            stats = sync_aliases(conn, loaded)
        except playbooks.PlaybookConflict as exc:
            print(f"playbook conflict: {exc}", file=sys.stderr)
            return 2
        conn.commit()

    print(
        f"aliases: {stats['inserted']} inserted, {stats['updated']} updated, "
        f"{stats['unchanged']} unchanged"
    )
    if stats["orphaned_groups"]:
        print(
            f"WARNING: {stats['orphaned_groups']} item_group row(s) still carry a "
            "canonical_manufacturer this sync re-pointed. Re-resolve those items, "
            "or run sync before the next ingest."
        )
    return 0
```

Register it in `build_parser`, after the `inspect` block:

```python
    pb = sub.add_parser("playbooks", help="validate or sync playbooks/")
    pb.add_argument("action", choices=["validate", "sync"])
    pb.set_defaults(func=cmd_playbooks)
```

- [ ] **Step 4: Run the new tests**

Run: `/srv/dentalia/.venv/bin/pytest tests/test_playbooks_sync.py -q`
Expected: PASS, 7 tests.

- [ ] **Step 5: Run the full suite**

Run: `/srv/dentalia/.venv/bin/pytest -q`
Expected: PASS, 718 passed and 1 skipped (699 baseline + 12 from Task A + 7 here). `app/handlers/resolve.py` is untouched, so no existing alias test can be affected. If any previously passing test now fails, stop and fix before committing.

- [ ] **Step 6: Commit**

```bash
git add app/cli.py tests/test_playbooks_sync.py
git commit -m "feat: dentalia playbooks sync seeds manufacturer aliases

Projects playbook bc_codes into manufacturer_alias, which is what turns
item_group.canonical_manufacturer from the bare BC code '001' into the
manufacturer's real name and makes every name-keyed lookup start
matching.

Seeds codes only: both LJ adapter profiles supply a code, never a name,
so a row keyed on 'VOCO' would be unreachable. Refuses to write when two
catalogues claim one code, since manufacturer_alias is not namespaced by
catalogue (deferred to gap G11). Reports item_group rows left orphaned by
a re-pointed alias instead of leaving it silent."
```

---

### Task C: DISCOVER domains and portal prefill

**Files:**
- Modify: `app/handlers/discover.py` (`_query_for`, `_search`, `_prefilled_search_links`, and the `handle_discover_group` call sites)
- Test: `tests/test_discover_handler.py` (append)

**Interfaces:**
- Consumes: `app.playbooks.for_manufacturer`, `Playbook.domains`, `Playbook.doc_sources`, `DocSource.kind`, `DocSource.url`.
- Produces: `_query_for(facts, playbook)`, `_prefilled_search_links(facts, playbook)`. Both accept `playbook=None` and behave exactly as today when it is `None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_discover_handler.py`:

```python
# --------------------------------------------------------------------------- #
# playbook domains + portal prefill (S1.7)
# --------------------------------------------------------------------------- #
def _voco_playbook():
    from app.playbooks import DocSource, Playbook

    return Playbook(
        slug="voco",
        manufacturer="VOCO GmbH",
        domains=("voco.dental", "voco.de"),
        doc_sources=(
            DocSource(doc_type="doc", kind="portal", url="https://voco.dental/downloads"),
            DocSource(doc_type="doc", kind="direct", url="https://voco.dental/a.pdf"),
        ),
    )


def _facts(manufacturer="VOCO GmbH", label="Grandio"):
    from app.handlers.discover import GroupFacts

    return GroupFacts(
        group_id=1, manufacturer=manufacturer, label=label,
        basic_udi_di=None, member_mfr_refs=[],
    )


def test_query_is_site_restricted_when_domains_known():
    from app.handlers.discover import _query_for

    q = _query_for(_facts(), _voco_playbook())

    assert "site:voco.dental" in q
    assert "site:voco.de" in q
    assert "declaration of conformity" in q


def test_query_unchanged_without_a_playbook():
    from app.handlers.discover import _query_for

    assert _query_for(_facts(), None) == 'VOCO GmbH "Grandio" declaration of conformity pdf'


def test_query_unchanged_when_playbook_has_no_domains():
    from app.playbooks import Playbook
    from app.handlers.discover import _query_for

    pb = Playbook(slug="x", manufacturer="VOCO GmbH")

    assert _query_for(_facts(), pb) == 'VOCO GmbH "Grandio" declaration of conformity pdf'


def test_prefill_puts_portal_urls_first():
    from app.handlers.discover import _prefilled_search_links

    links = _prefilled_search_links(_facts(), _voco_playbook())

    assert links[0] == "https://voco.dental/downloads"
    assert "https://voco.dental/a.pdf" not in links   # direct sources are not prefill
    assert any("google.com/search" in u for u in links)


def test_prefill_unchanged_without_a_playbook():
    from app.handlers.discover import _prefilled_search_links

    links = _prefilled_search_links(_facts(), None)

    assert all(u.startswith("https://www.google.com") or u.startswith("https://search.brave.com")
               for u in links)


def test_search_retries_unrestricted_when_restricted_finds_nothing(conn):
    """Compliance PDFs often sit on a CDN or a subsidiary domain, so a
    site:-restricted query that returns nothing must not end the rung."""
    from app.handlers.discover import _search
    from app.config import load_config

    queries = []

    class _Adapter:
        def query(self, q):
            queries.append(q)
            return [] if "site:" in q else [_candidate("https://cdn.example/x.pdf")]

    urls, detail = _search(
        conn, _facts(), load_config(), _Adapter(), lambda f, c: [(c[0], 0.99)],
        playbook=_voco_playbook(),
    )

    assert len(queries) == 2
    assert "site:" in queries[0] and "site:" not in queries[1]
    assert detail["restricted_miss"] is True
    assert urls == ["https://cdn.example/x.pdf"]


def test_search_does_not_retry_when_restricted_query_hits(conn):
    from app.handlers.discover import _search
    from app.config import load_config

    queries = []

    class _Adapter:
        def query(self, q):
            queries.append(q)
            return [_candidate("https://voco.dental/x.pdf")]

    urls, detail = _search(
        conn, _facts(), load_config(), _Adapter(), lambda f, c: [(c[0], 0.99)],
        playbook=_voco_playbook(),
    )

    assert len(queries) == 1
    assert detail.get("restricted_miss") is not True
```

Before writing these, read the top of `tests/test_discover_handler.py` and reuse its existing helper for building a search candidate. If it has one, use it instead of `_candidate`; if it does not, add:

```python
def _candidate(url, rank=0):
    from app.adapters.search import SearchCandidate

    return SearchCandidate(url=url, title="t", snippet="s", rank=rank)
```

Check `app/adapters/search.py` for `SearchCandidate`'s real field names before writing this helper; do not guess them.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/srv/dentalia/.venv/bin/pytest tests/test_discover_handler.py -q -k "playbook or site_restricted or prefill or restricted"`
Expected: FAIL, `_query_for() takes 1 positional argument but 2 were given`.

- [ ] **Step 3: Implement the three functions**

In `app/handlers/discover.py`, add the import:

```python
from app import playbooks as playbooks_mod
```

Replace `_query_for`:

```python
def _query_for(facts, playbook=None) -> str:
    """Search query for the SEARCH rung. With a playbook's official domains
    known, restrict to them: it sharply raises precision, which matters because
    the T1 ranker is the only thing standing between a candidate and a fetch.
    `_search` retries unrestricted on a miss, so the restriction can never be
    the reason a group finds nothing."""
    label = f'"{facts.label}" ' if facts.label else ""
    base = f"{facts.manufacturer} {label}declaration of conformity pdf".strip()
    if playbook and playbook.domains:
        sites = " OR ".join(f"site:{d}" for d in playbook.domains)
        return f"{base} ({sites})"
    return base
```

Replace `_prefilled_search_links`:

```python
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
```

Replace `_search`:

```python
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
```

- [ ] **Step 4: Thread the playbook through the handler**

In `handle_discover_group`, load the playbook once after the group facts are fetched, and pass it to the three call sites. Read the handler body first to place this correctly; the load belongs immediately after `facts` is built and before the ladder loop:

```python
    playbook = playbooks_mod.for_manufacturer(facts.manufacturer)
```

Then pass `playbook` to `_search(...)` and to `_push_manual(...)`, and have `_push_manual` forward it into `_prefilled_search_links(facts, playbook)`. Update `_push_manual`'s signature to `_push_manual(conn, facts, sources_tried, playbook=None)`.

- [ ] **Step 5: Run the new tests**

Run: `/srv/dentalia/.venv/bin/pytest tests/test_discover_handler.py -q`
Expected: PASS, including every pre-existing DISCOVER test. Those call `_query_for(facts)` and `_prefilled_search_links(facts)` with one argument, which still works because `playbook` defaults to `None`.

- [ ] **Step 6: Run the full suite**

Run: `/srv/dentalia/.venv/bin/pytest -q`
Expected: PASS, no regressions against the 718 from Task B.

- [ ] **Step 7: Commit**

```bash
git add app/handlers/discover.py tests/test_discover_handler.py
git commit -m "feat: DISCOVER uses playbook domains and portal URLs

site:-restricted search query when the manufacturer's official domains
are known, with a single unrestricted retry when the restricted query
finds nothing (compliance PDFs often sit on a CDN or subsidiary domain).
Both attempts are logged to discovery_log.

Known document portals are prepended to a dead-end manual task's
prefill links. direct sources are excluded: those are S2.1 crawler
targets, not human click targets."
```

---

### Task D: EXTRACT template path move and packaging

**Files:**
- Modify: `app/extract/t0_layout.py` (`Template`, `load_templates`, `_DEFAULT_DIR`)
- Delete: `app/extract/t0_layout/` (all five JSON files, after confirming they were merged into `playbooks/` in Task A)
- Modify: `tests/test_t0_layout.py` (five assertions at lines 93 to 128)
- Modify: `Dockerfile`, `pyproject.toml`

**Interfaces:**
- Consumes: `app.playbooks.PLAYBOOKS_DIR`.
- Produces: `Template` gains `slug: str`. `load_templates` keeps its existing signature and return type.

Why `slug` is added: five tests currently assert `found.manufacturer == "VOCO"` against the default directory. Task A changed `manufacturer` to the legal entity, so those assertions must change. Asserting on the slug instead makes them stable against future legal-name corrections, which are expected as the authoring pass fills in real entity names.

- [ ] **Step 1: Write the failing tests**

In `tests/test_t0_layout.py`, add to the temp-dir section:

```python
def test_load_templates_carries_slug(tmp_path):
    from app.extract.t0_layout import load_templates

    _write_template(
        tmp_path,
        "acme-corp.json",
        {"manufacturer": "Acme Corp AG", "match": {"anchors": ["Acme"]},
         "ref_strategy": "table"},
    )

    (tpl,) = load_templates(tmp_path)

    assert tpl.slug == "acme-corp"


def test_load_templates_silently_skips_files_with_no_parse_section(tmp_path, caplog):
    """A URLs-only playbook is a normal, complete file -- not a malformed one.
    Warning on it would bury real authoring errors under ~15 lines of noise."""
    from app.extract.t0_layout import load_templates

    _write_template(
        tmp_path,
        "urls-only.json",
        {"manufacturer": "NSK", "domains": ["nsk-dental.com"]},
    )

    with caplog.at_level("WARNING"):
        assert load_templates(tmp_path) == ()

    assert caplog.records == []


def test_load_templates_still_warns_on_a_real_error(tmp_path, caplog):
    from app.extract.t0_layout import load_templates

    _write_template(
        tmp_path,
        "broken.json",
        {"manufacturer": "BAD", "match": {"anchors": ["x"]}, "ref_strategy": "nonsense"},
    )

    with caplog.at_level("WARNING"):
        assert load_templates(tmp_path) == ()

    assert any("malformed" in r.message for r in caplog.records)
```

Then change the five default-dir assertions (currently lines 93, 104, 112, 120, 128) from manufacturer names to slugs:

```python
    assert found is not None and found.slug == "voco"
    assert found is not None and found.slug == "dentsply-sirona"
    assert found is not None and found.slug == "komet"
    assert found is not None and found.slug == "ivoclar"
    assert found is not None and found.slug == "gc"
```

Read each assertion in context before editing; the surrounding `match(txt, load_templates())` calls stay as they are.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `/srv/dentalia/.venv/bin/pytest tests/test_t0_layout.py -q`
Expected: FAIL. `AttributeError: 'Template' object has no attribute 'slug'`, and the URLs-only case fails because it currently logs `skipping malformed template`.

- [ ] **Step 3: Update the loader**

In `app/extract/t0_layout.py`, add `slug` to `Template`:

```python
@dataclass(frozen=True)
class Template:
    slug: str
    manufacturer: str
    anchors: tuple[str, ...]
    ref_strategy: str
    ref_strategy_config: dict = field(default_factory=dict)
```

Repoint the default directory. Replace the `_DEFAULT_DIR` assignment at line 27:

```python
from app.playbooks import PLAYBOOKS_DIR as _DEFAULT_DIR
```

Place that with the other imports and delete the old `_DEFAULT_DIR = pathlib.Path(__file__).parent / "t0_layout"` line.

Replace the body of the `for` loop in `load_templates`:

```python
    templates = []
    for f in sorted(dir_path.glob("*.json")):
        try:
            data = json.loads(f.read_text())
        except Exception:
            log.warning("t0_layout: skipping malformed template %s", f, exc_info=True)
            continue

        # A playbook with no parse section is a complete, valid file (it may
        # carry only identity and URLs). Skipping it is normal, not an error --
        # warning here would bury real authoring mistakes in noise.
        if "ref_strategy" not in data and "match" not in data:
            continue

        try:
            strategy = data["ref_strategy"]
            if strategy not in REF_STRATEGIES:
                raise ValueError(f"unknown ref_strategy {strategy!r}")
            templates.append(
                Template(
                    slug=f.stem,
                    manufacturer=data["manufacturer"],
                    anchors=tuple(data["match"]["anchors"]),
                    ref_strategy=strategy,
                    ref_strategy_config=data.get("ref_strategy_config", {}),
                )
            )
        except Exception:
            log.warning("t0_layout: skipping malformed template %s", f, exc_info=True)
    return tuple(templates)
```

Update the module docstring: templates now live in `playbooks/*.json`, not `app/extract/t0_layout/*.json`.

- [ ] **Step 4: Delete the old template directory**

First prove every parse block survived the Task A merge. This compares the three
parse fields of each old template against whichever playbook carries the same
anchors, and exits non-zero if any old template has no match:

```bash
.venv/bin/python - <<'PY'
import json, pathlib, sys

old_dir = pathlib.Path("app/extract/t0_layout")
new = [json.loads(p.read_text()) for p in pathlib.Path("playbooks").glob("*.json")]

def parse_block(d):
    return (
        tuple(d.get("match", {}).get("anchors", [])),
        d.get("ref_strategy"),
        json.dumps(d.get("ref_strategy_config", {}), sort_keys=True),
    )

missing = []
for p in sorted(old_dir.glob("*.json")):
    want = parse_block(json.loads(p.read_text()))
    if any(parse_block(n) == want for n in new):
        print(f"OK   {p.name}: parse block found in playbooks/")
    else:
        missing.append(p.name)
        print(f"FAIL {p.name}: no playbook carries anchors {want[0]}")

sys.exit(1 if missing else 0)
PY
```

Expected: five `OK` lines and exit 0. A `FAIL` means Task A dropped or retyped
an anchor; fix the playbook file before deleting anything. Then:

```bash
git rm -r app/extract/t0_layout/
```

- [ ] **Step 5: Ship `playbooks/` into the wheel and the image**

`playbooks/` is at the repo root, so it is in neither `packages = ["app", "web"]` nor the Dockerfile's `COPY app/`. Without this step the worker image has no playbooks at all, `load_templates` returns `()` for a missing directory, and extraction silently degrades to the generic T0 path with no error.

In `pyproject.toml`, under `[tool.hatch.build.targets.wheel]`:

```toml
[tool.hatch.build.targets.wheel]
packages = ["app", "web"]
# playbooks/ is authored config at the repo root, not a package. Without this
# it is absent from the wheel and load_templates() silently returns ().
force-include = {"playbooks" = "playbooks"}
```

In `Dockerfile`, add alongside the existing `COPY migrations/ ./migrations/` at line 31:

```dockerfile
COPY playbooks/ ./playbooks/
```

- [ ] **Step 6: Run the tests**

Run: `/srv/dentalia/.venv/bin/pytest tests/test_t0_layout.py tests/test_t0.py tests/test_t0_corpus.py tests/test_playbooks.py -q`
Expected: PASS. The five default-dir match tests now resolve templates out of `playbooks/`, which proves the Task A merge preserved every anchor.

- [ ] **Step 7: Verify the packaging fix for real**

Run:

```bash
docker compose build worker && \
docker compose run --rm worker python -c "
from app.extract.t0_layout import load_templates
from app.playbooks import PLAYBOOKS_DIR, load_playbooks
print('dir:', PLAYBOOKS_DIR, 'exists:', PLAYBOOKS_DIR.is_dir())
print('templates:', [t.slug for t in load_templates()])
print('playbooks:', [p.slug for p in load_playbooks()])
"
```

Expected: the directory exists and both lists are non-empty. An empty template list means the packaging change did not take, and merging this task would silently degrade extraction in production.

Note: `docker compose` currently fails with `required variable DENTALIA_WEB_PASSWORD_HASH is missing`. That is tracked at `tasks/todo.md:234`. Set it in `.env` first (`docker compose run --rm caddy caddy hash-password --plaintext <pw>`), or run this check with `docker build -f Dockerfile --target app -t dentalia-check .` and `docker run --rm dentalia-check python -c ...` instead.

- [ ] **Step 8: Run the full suite**

Run: `/srv/dentalia/.venv/bin/pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 9: Commit**

```bash
git add app/extract/t0_layout.py tests/test_t0_layout.py pyproject.toml Dockerfile
git add -A app/extract/t0_layout playbooks/
git commit -m "refactor: T0 templates move to playbooks/

One home for per-manufacturer config. Template gains a slug (from the
filename) so tests assert on a stable identifier rather than the
manufacturer name, which is now the legal entity and will change as the
authoring pass fills in real names.

A playbook with no parse section is a complete file, so it is skipped
silently; only genuine errors still warn. Adds the Dockerfile COPY and
the hatch force-include without which playbooks/ is absent from the
worker image and load_templates() silently returns ()."
```

---

## Post-implementation

- [ ] **Update the operational docs.** `docs/code-map.md` references the 12-manufacturer corpus and `app/extract/t0_layout/`; `docs/runbook.md` lists the CLI surface. Both need the new `playbooks/` path and the `dentalia playbooks validate|sync` command. CLAUDE.md's repo layout already lists `playbooks/` but calls it "(Phase 2)" -- correct that to note the S1.7 identity/URL use.
- [ ] **Update `PHASES.md`.** S1.7 gains the playbook store; GAP C (T0 template ownership) is now closed by Task D rather than deferred to S2.1.
- [ ] **Run `dentalia playbooks sync` against the real dev database before the next ingest**, and act on the `orphaned_groups` warning if it is non-zero.
- [ ] **Author the remaining playbooks.** Only five exist after Task A. The other ~15 CONFIRMED manufacturers from the code sweep need files. Track in `playbooks/README.md`.
- [ ] **Send the BC/IT vendor master request** (design spec §11 item 2). It replaces the inferred 42% code-to-name coverage with 100% and needs no playbook re-authoring, because `bc_codes` already carries `{catalogue, code}`.

## Self-review notes

Spec coverage checked section by section. §1 artifact is Task A. §2 identity is Task B. §3 namespacing is Task B steps 1 and 4. §4 Phase 1 consumers is Task C plus Task D. §5 exclusions are respected: no `manufacturer` table migration, `doc_sources` is never fetched, no ladder rung activated, the three TOML config homes are untouched. §6 vendor master is a post-implementation item, not code. §7 task split matches. §8 testing is distributed across the four tasks.

Four deviations from the spec, all deliberate:

0. **The catalogue-namespacing migration is cut** (spec §3, revised). Denis confirmed LJ and ZG are intended to converge into one BC, which inverts the risk: keying `manufacturer_alias` on the warehouse tag would fragment a single namespace and reintroduce bare BC codes for ZG-tagged items. Whether the systems differ is open gap G11, owner client, due pre-S2.5. Deferring is cheap because `manufacturer_alias` is regenerable from playbooks plus a sync re-run, and Phase 1 ingests Ljubljana only so no collision can occur first. Task B instead refuses to sync when two catalogues claim one code, turning the deferred risk into a loud failure.
0b. **Sync seeds `bc_codes` only, not `aliases`.** Both LJ adapter profiles supply a code and never a name, so an alias row keyed on `"VOCO"` is unreachable. Aliases remain in-memory for `for_manufacturer`.

1. Spec §7 called Task D a `git mv`. It is not: Task A merges the parse blocks into the new files and Task D deletes the old directory, because the five brands with templates are also five of the manufacturers needing playbooks, and one file per manufacturer is the whole point.
2. Spec §7 did not mention packaging. `pyproject.toml` limits the wheel to `packages = ["app", "web"]` and the Dockerfile copies only `app/` and `migrations/`, so a root-level `playbooks/` ships in neither. Task D step 5 fixes both, and step 7 verifies it in a real container rather than trusting the config.
