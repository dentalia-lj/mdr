"""Attributing an EUDAMED actor to one of our canonical manufacturers.

This is the step where a mistake becomes a wrong e-mail to a supplier, so the
module is pure and the rules are explicit. `actorName=` on the certificates
endpoint is a SUBSTRING match -- `Ivo` returns 4 records, `Ivoclar` returns 1
(measured 2026-08-26) -- which is exactly why attribution does not use a query
at all: the register is mirrored whole and matched here, locally.

Ruling (Denis, 2026-08-26): a normalised exact match auto-stores; anything else
is queued for a human. No LLM (Invariant 12) -- rapidfuzz only.
"""

from __future__ import annotations

import pytest

from app.eudamed_names import (
    EXACT_VIA, FUZZY_VIA, FUZZY_FLOOR, match_actor, normalise,
)

CANDIDATES = {
    "IVOCLAR": ["IVOCLAR VIVADENT", "Ivoclar Vivadent AG"],
    "CARL MARTIN": ["CARL MARTIN"],
    "KOMET": ["KOMET", "Gebr. Brasseler"],
    "GC EUROPE N.V.": ["GC EUROPE N.V."],
}


@pytest.mark.parametrize("raw,want", [
    ("Ivoclar Vivadent AG", "ivoclar vivadent"),
    ("IVOCLAR VIVADENT A.G.", "ivoclar vivadent"),
    ("Carl Martin GmbH", "carl martin"),
    ("GC EUROPE N.V.", "gc europe"),
    ("Gebr. Brasseler GmbH & Co. KG", "gebr brasseler"),
    ("  Institut   Straumann   AG  ", "institut straumann"),
])
def test_normalise_strips_case_punctuation_and_legal_suffixes(raw, want):
    assert normalise(raw) == want


def test_an_exact_normalised_match_is_auto():
    canonical, via, score = match_actor("Ivoclar Vivadent AG", CANDIDATES)
    assert canonical == "IVOCLAR"
    assert via == EXACT_VIA
    assert score == 100.0


def test_a_near_match_is_fuzzy_and_scored():
    """A misspelling is the real near-match case. Punctuation and legal-suffix
    variants are NOT near matches -- they normalise to the same key and are
    exact (see the test below)."""
    canonical, via, score = match_actor("Ivoclar Vivdent AG", CANDIDATES)
    assert canonical == "IVOCLAR"
    assert via == FUZZY_VIA
    assert FUZZY_FLOOR <= score < 100.0


def test_an_unrelated_actor_matches_nothing():
    """3.196 distinct actors are in the register and 375 of our 384 canonical
    names are not device manufacturers. Most pairings must simply not match."""
    canonical, via, score = match_actor("PAUL HARTMANN AG", CANDIDATES)
    assert canonical is None


def test_a_substring_of_a_canonical_name_is_not_a_match():
    """`Ivo` returns 4 records from the API's substring matcher. Nothing that
    merely starts with our name may be attributed to us."""
    canonical, _, _ = match_actor("Ivo Medical Supplies Ltd", CANDIDATES)
    assert canonical is None


def test_the_second_alias_of_a_canonical_name_also_matches():
    """`manufacturer_alias` maps BC codes 001 and 005 both to IVOCLAR, and KOMET
    trades as Gebr. Brasseler. Matching must consider every alias, not the
    canonical string alone -- Brasseler is the actor name that carries KOMET's
    certificate HZ 1470094-1."""
    canonical, via, _ = match_actor("Gebr. Brasseler GmbH & Co. KG", CANDIDATES)
    assert canonical == "KOMET"
    assert via == EXACT_VIA


def test_a_legal_suffix_variant_is_exact_not_fuzzy():
    """`GC Europe NV` and `GC EUROPE N.V.` are one company. Normalising legal
    suffixes exists precisely so this auto-attributes instead of waiting on a
    human every month (spec §4.5)."""
    canonical, via, score = match_actor("GC Europe NV", CANDIDATES)
    assert canonical == "GC EUROPE N.V."
    assert via == EXACT_VIA
    assert score == 100.0


def test_matching_is_deterministic_when_two_canonicals_tie_on_score():
    """Ties must resolve the same way on every run, or the confirm queue
    churns — a person clears a candidate and it reappears next month under a
    different canonical name.

    Both aliases below are one substitution from the query and score
    identically, so this exercises the fuzzy loop's `sorted()` iteration and
    its strict `>` comparison. An exact-match tie would not: that returns from
    the earlier loop and never reaches the scoring code this pins.
    """
    cands = {"ACME ZETA": ["Acme Beta Zeta"], "ACME FETA": ["Acme Beta Feta"]}
    canonical, via, score = match_actor("Acme Beta Meta", cands)

    assert via == FUZZY_VIA          # not the exact-match early return
    assert canonical == "ACME FETA"  # alphabetically first wins, every time

    # Same answer regardless of dict insertion order.
    flipped = {"ACME FETA": ["Acme Beta Feta"], "ACME ZETA": ["Acme Beta Zeta"]}
    assert match_actor("Acme Beta Meta", flipped) == (canonical, via, score)

    for _ in range(5):
        assert match_actor("Acme Beta Meta", cands) == (canonical, via, score)
