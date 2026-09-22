"""Shared manufacturer-name normalization and alias resolution.

Two stages compare manufacturer names against the catalogue, and they must
agree on what "the same manufacturer" means: VALIDATE, scoping a group lookup
to the manufacturer a document self-identifies as, and GATE, deriving
manufacturer-scope links from a human's (or the machine's) binding decision.
This module is to those two what `app.urls` is to DISCOVER and FETCH -- a
single normalization the stages import rather than re-derive, because a
divergence is silent and produces wrong links rather than an error.

The identity problem in one line: the catalogue stores a BC vendor CODE
(`item_mirror.manufacturer_raw = '001'`), `manufacturer_alias` maps that code
to a canonical label (`'IVOCLAR'`), and the document prints the legal entity
(`'Ivoclar Vivadent AG'`). Three spellings of one manufacturer, and the only
thing joining them is this table. Which direction you traverse it decides
whether you find 1069 items or none.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable

_WS_RE = re.compile(r"\s+")
_PUNCT_SPACE_RE = re.compile(r"\s*([.,])\s*")


def normalize(raw: str | None) -> str:
    """Fold a manufacturer name to an identity-comparison key: strip accents
    (Unicode NFKD, drop combining marks), collapse all whitespace (incl.
    non-breaking space) to single spaces, tighten spacing immediately around
    periods/commas, and casefold. Deterministic character normalization to
    an EXACT-equality key -- not similarity/fuzzy matching (no rapidfuzz, no
    threshold, no partial/prefix match), and never deletes a word: legal-form
    tokens (GmbH, AG, N.V., Sarl, Inc., Ltd) always survive, because they are
    what distinguishes e.g. 'DENTSPLY Implants N.V.' from 'DENTSPLY Implants
    Manufacturing GmbH' ([validate-mfr-normalize])."""
    if not raw:
        return ""
    folded = unicodedata.normalize("NFKD", raw)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    folded = folded.replace("\xa0", " ")
    folded = _WS_RE.sub(" ", folded).strip()
    folded = _PUNCT_SPACE_RE.sub(r"\1", folded)
    return folded.casefold()


def canonicalize(conn, raw: str) -> str:
    """Resolve a document-facing manufacturer string (backfill/email
    self-identification, e.g. "Ivoclar Vivadent AG") to its canonical name
    through `manufacturer_alias`. `item_group.canonical_manufacturer` is
    written by RESOLVE from BC vendor codes (all Ivoclar groups carry the
    literal 'IVOCLAR'), never from a manufacturer's own spelling of its name
    on its documents -- an exact string comparison between the two is false
    by construction.

    Read-only. RESOLVE's `_alias_lookup` (resolve.py) inserts `raw -> raw` on
    a miss, which is correct there (a new BC vendor code IS a new
    manufacturer) and wrong here: a miss on OCR/LLM-extracted PDF text must
    never mint a manufacturer_alias row -- that would permanently pollute the
    table with a one-off or garbled document spelling. On a miss this
    returns the raw string unchanged; that fallback preserves the exact-match
    path (a document that literally says "IVOCLAR" still resolves) and gives
    the caller a single value to try, whether or not an alias existed.

    Both sides go through `normalize` before comparing
    ([validate-mfr-normalize]) -- the stored `raw_name` values are never
    rewritten, only read and folded at comparison time."""
    target = normalize(raw)
    if not target:
        return raw
    rows = conn.execute(
        "SELECT raw_name, canonical_name FROM manufacturer_alias"
    ).fetchall()
    matches = sorted(
        (r for r in rows if normalize(r["raw_name"]) == target),
        key=lambda r: r["raw_name"],
    )
    return matches[0]["canonical_name"] if matches else raw


def resolve_canonicals(conn, name: str) -> list[str]:
    """Every distinct canonical manufacturer the given spelling can denote.

    Stricter than `canonicalize`, and deliberately so. `canonicalize` falls back
    to the raw string on a miss, which is right when the caller just needs one
    value to try; here a miss must be legible as a miss, because the caller is
    deciding whether the machine may bind a document to a manufacturer with no
    human in the loop. "We found the manufacturer" has to mean the name matched
    something a human curated -- an alias row -- not that we echoed back an
    unratified spelling the model produced.

    Matches on EITHER side of the table: `raw_name` (the BC vendor code, or a
    legal entity a playbook has taught us) and `canonical_name` (the label
    itself). A document printing '3Shape A/S' has no raw_name row of its own,
    but the canonical '3SHAPE A/S' is right there -- normalization is what
    bridges the shouting.

    The list length IS the guard: 0 = unknown, bind nothing; 1 = unambiguous,
    safe to bind; >1 = two curated entries fold to this spelling under
    different canonicals, and choosing between them is a coin flip over every
    MD item of a manufacturer, so it belongs to a human.
    """
    target = normalize(name)
    if not target:
        return []
    rows = conn.execute(
        "SELECT raw_name, canonical_name FROM manufacturer_alias"
    ).fetchall()
    return sorted({
        r["canonical_name"] for r in rows
        if target in (normalize(r["raw_name"]), normalize(r["canonical_name"]))
    })


def item_codes_for(conn, name: str) -> list[str]:
    """Every distinct `item_mirror.manufacturer_raw` belonging to the same
    manufacturer as `name`, whatever spelling `name` arrives in.

    This is the lookup `gate._derive_mfr_scope_links` got backwards until
    2026-08-17. It looked its argument up as a `canonical_name` and stopped
    there, so it resolved only when the document happened to print the exact
    label the catalogue uses. Measured on the live registry: 'GC EUROPE N.V.'
    worked (it is both a raw_name and the canonical) while 'Ivoclar Vivadent
    AG' -- an alias of 'IVOCLAR' -- matched nothing and bound zero items with
    no error and no flag. Aliases exist BECAUSE manufacturers print their
    legal entity rather than the catalogue's label, so the direction that
    failed was the normal case.

    Three spellings can each name the same manufacturer, so all three are
    accepted:

    * the argument itself, which covers a catalogue row whose
      `manufacturer_raw` has no alias at all (the pre-existing direct-match
      path -- dropping it silently unlinks every manufacturer not yet in the
      alias table);
    * the canonical it resolves to;
    * every `raw_name` under that canonical -- the fan-out, and the point of
      the exercise: one manufacturer spans several BC codes (IVOCLAR is 001,
      005 AND 275), so a manufacturer-scope certificate must reach all of
      them, not just the one its own spelling hints at.

    Both sides fold through `normalize` before comparing, so the catalogue
    shouting '3SHAPE TRIOS A/S' still meets a PDF's '3Shape TRIOS A/S'.

    A name matching nothing yields `[]` and the caller links nothing --
    deliberately narrow, because the only alternative to a precise miss is a
    confident wrong attribution. Returned sorted, so links are written in a
    stable order.

    The rule itself lives in `item_codes_resolver`; this is that resolver
    asked about one name.
    """
    return item_codes_resolver(conn)(name)


def item_codes_resolver(conn) -> Callable[[str], list[str]]:
    """`item_codes_for` for any number of names, over ONE read of the two
    tables it needs.

    GATE asks about one name per job, so a read per call is fine there. The
    Review panel prices every manufacturer in the catalogue at once (the
    binding picker's per-option count, 2026-09-11), and a read per option was
    hundreds of scans of `manufacturer_alias` to open one panel. Rather than a
    second, faster copy of the rule for the UI, this IS the rule and
    `item_codes_for` calls it, so the count a reviewer is shown and the links
    GATE writes cannot come from two implementations.

    Step for step what `item_codes_for` documents: fold the name; resolve it to
    a canonical the way `canonicalize` does (the alias with the lowest
    `raw_name` among those that fold to it, else the name itself); accept the
    name, that canonical, and every `raw_name` under the canonical; return the
    catalogue codes that fold to any of them, sorted.
    """
    aliases = conn.execute(
        "SELECT raw_name, canonical_name FROM manufacturer_alias"
    ).fetchall()
    canonical_of: dict[str, str] = {}
    for a in sorted(aliases, key=lambda r: r["raw_name"]):
        canonical_of.setdefault(normalize(a["raw_name"]), a["canonical_name"])
    raws_under: dict[str, set[str]] = {}
    for a in aliases:
        raws_under.setdefault(normalize(a["canonical_name"]), set()).add(
            normalize(a["raw_name"]))
    codes_by_key: dict[str, list[str]] = {}
    for r in conn.execute(
        "SELECT DISTINCT manufacturer_raw FROM item_mirror "
        "WHERE manufacturer_raw IS NOT NULL"
    ).fetchall():
        codes_by_key.setdefault(normalize(r["manufacturer_raw"]), []).append(
            r["manufacturer_raw"])

    def codes_for(name: str) -> list[str]:
        target = normalize(name)
        if not target:
            return []
        canonical = normalize(canonical_of.get(target, name))
        accepted = {target, canonical} | raws_under.get(canonical, set())
        accepted.discard("")
        return sorted(code for key in accepted for code in codes_by_key.get(key, ()))

    return codes_for
