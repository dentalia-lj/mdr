"""Basic UDI-DI check-character validation.

The algorithm is transcribed from HIBCC's `HIBC Basic UDI-DI` specification
(Tables 1 and 2 plus the worked example). GS1's GMN check character pair is
documented as the same MOD 1021,32 scheme; GS1's own specification PDF is
served 403 to us, so the GS1 half is pinned here by EUDAMED-published codes
instead of by a quoted worked example -- see
`test_gs1_ground_truth_from_eudamed`.

The corpus figures quoted below were measured on 2026-08-20 against
`document.basic_udi_di` and four EUDAMED catalogue sweeps; they are recorded
in `docs/2026-08-20-udi-identifiers-and-registries.md` §2.
"""
from __future__ import annotations

import pytest

from app.extract import udi


# --------------------------------------------------------------------------- #
# the specification's own worked example
# --------------------------------------------------------------------------- #
def test_hibcc_specification_worked_example():
    """HIBCC's published example: ++A999MODELIDENTIFIER11 -> check pair S8.

    The spec walks this one through by hand and names both intermediates, so
    it pins the character table, the prime weighting and the 32-character
    encode in a single assertion. If this fails, the transcription is wrong.
    """
    assert udi.check_pair("++A999MODELIDENTIFIER11") == "S8"


def test_the_check_value_is_encoded_as_a1_times_32_plus_a2():
    """774 -> (24, 6) -> 'S', '8' in the depleted 32-character set."""
    assert udi.CHECK_SET[24] == "S"
    assert udi.CHECK_SET[6] == "8"
    assert len(udi.CHECK_SET) == 32


def test_the_check_set_omits_the_visually_confusable_characters():
    """The set exists to survive keying and OCR: 0/O and 1/I are removed.

    This is the property that makes the validator catch our actual corpus
    failure -- a letter O read where a digit 0 belongs.
    """
    for ch in "01OI":
        assert ch not in udi.CHECK_SET


def test_the_data_character_set_matches_hibcc_table_1():
    assert len(udi.DATA_SET) == 82
    for ch, value in (("!", 0), ("/", 12), ("0", 13), ("9", 22),
                      ("A", 29), ("Z", 54), ("_", 55), ("a", 56), ("z", 81)):
        assert udi.DATA_SET.index(ch) == value, ch


# --------------------------------------------------------------------------- #
# agency detection
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value,agency", [
    ("++E22100160100000008Z", udi.HIBCC),
    ("+D8691100000", udi.HIBCC),
    ("0763003170002Q6", udi.GS1),
    # EUDAMED mints this shape itself for devices whose manufacturer never
    # obtained a real Basic UDI-DI: "B-" prefixed onto the UDI-DI verbatim.
    ("B-E22124861", udi.EUDAMED_ASSIGNED),
    ("Document", udi.UNKNOWN),
])
def test_agency_is_read_off_the_prefix(value, agency):
    assert udi.agency_of(value) == agency


# --------------------------------------------------------------------------- #
# verdicts
# --------------------------------------------------------------------------- #
def test_a_well_formed_hibcc_code_is_valid():
    v = udi.validate_basic_udi("++E22100160100000008Z")
    assert v.status == udi.VALID
    assert v.agency == udi.HIBCC


def test_a_well_formed_gs1_code_is_valid():
    v = udi.validate_basic_udi("0763003170002Q6")
    assert v.status == udi.VALID
    assert v.agency == udi.GS1


def test_the_ocr_corruption_in_our_corpus_is_caught():
    """Two real rows from `document.basic_udi_di`, letter O for digit 0.

    Both were also unresolvable in EUDAMED, which on its own is ambiguous --
    a manufacturer that never registered looks identical. The check pair is
    what separates "not registered" from "we misread it".
    """
    for value in ("++E221049001OOOOOOOFX", "++E22104911OOOOOOOOGY"):
        v = udi.validate_basic_udi(value)
        assert v.status == udi.INVALID, value
        assert "expected" in v.detail


def test_a_corruption_with_no_visible_tell_is_caught():
    """`++E221023102000000093` looks fine and is not: expected check pair 9B.

    Found only by running the validator over the corpus. This is the case that
    justifies the module -- eyeballing would never have flagged it.
    """
    v = udi.validate_basic_udi("++E221023102000000093")
    assert v.status == udi.INVALID
    assert "9B" in v.detail


def test_a_single_character_corruption_anywhere_is_caught():
    """Mutating any one payload character must break the pair.

    Weighting by distinct primes is what buys this; a plain sum would let
    transpositions through.
    """
    good = "++E22100160100000008Z"
    for i in range(2, len(good) - 2):
        bad = good[:i] + ("7" if good[i] != "7" else "5") + good[i + 1:]
        assert udi.validate_basic_udi(bad).status == udi.INVALID, bad


def test_a_transposition_is_caught():
    good = "0763003170002Q6"
    bad = good[:5] + good[6] + good[5] + good[7:]
    assert bad != good
    assert udi.validate_basic_udi(bad).status == udi.INVALID


def test_a_eudamed_assigned_code_is_unscorable_not_invalid():
    """`B-` codes carry no check pair. Absence of a check is not a failure.

    Calling these invalid would punish a manufacturer for EUDAMED's own
    fallback and would put a real identifier through pointless re-extraction.
    """
    v = udi.validate_basic_udi("B-E22124861")
    assert v.status == udi.UNSCORABLE


def test_an_unrecognised_shape_is_unscorable():
    for value in ("Document", "Phasor", "Grandio"):
        assert udi.validate_basic_udi(value).status == udi.UNSCORABLE


def test_a_value_past_the_25_character_maximum_is_invalid():
    """Both agencies cap the Basic UDI-DI at 25 characters including the pair.

    Over-length in our corpus means the regex swallowed two codes or a code
    plus prose, which is a extraction defect worth surfacing, not a shrug.
    """
    v = udi.validate_basic_udi("++E221001601000000080000000000Z")
    assert v.status == udi.INVALID
    assert "25" in v.detail


def test_empty_and_none_are_unscorable_and_do_not_raise():
    for value in (None, "", "   "):
        assert udi.validate_basic_udi(value).status == udi.UNSCORABLE


def test_validation_is_case_sensitive_on_the_check_pair():
    """The check set is upper-case; a lower-cased pair is not the same pair."""
    assert udi.validate_basic_udi("0763003170002q6").status == udi.INVALID


# --------------------------------------------------------------------------- #
# ground truth: codes EUDAMED itself publishes must all pass
# --------------------------------------------------------------------------- #
# Sampled 2026-08-20 from `GET /devices/udiDiData?srn=...` for Straumann,
# Ivoclar, VOCO and JJGC. These are the manufacturers' own registered values,
# so a false positive here would be a bug in the validator, not in the data.
_EUDAMED_HIBCC = [
    "++E2210016010000000 8Z".replace(" ", ""),
    "++E2210229000000000EE",
    "++E2210575000000000K4",
    "++E2210140060000000AL",
    "++E2210065020000000DJ",
    "++E2210478000000000LN",
    "++E2210427000000000EW",
    "++E2210637000000000J7",
]
_EUDAMED_GS1 = [
    "0763003170001Q4",
    "0763003170002Q6",
    "0763003170005QC",
    "0763003170006QE",
    "0763003170014QD",
    "0763003170058QZ",
    "0763003170065QW",
    "0763003170097RB",
]


@pytest.mark.parametrize("value", _EUDAMED_HIBCC)
def test_hibcc_ground_truth_from_eudamed(value):
    assert udi.validate_basic_udi(value).status == udi.VALID, value


@pytest.mark.parametrize("value", _EUDAMED_GS1)
def test_gs1_ground_truth_from_eudamed(value):
    """Pins the GS1 half of the claim.

    GS1's check-character-pair PDF is served 403, so rather than quote a spec
    we cannot read, this asserts against codes GS1 subscribers registered in a
    government database. Measured over the full sweep: 185 of 185 pass.
    """
    assert udi.validate_basic_udi(value).status == udi.VALID, value
