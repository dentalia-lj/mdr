"""Offline check-character validation for Basic UDI-DIs.

A Basic UDI-DI ends in a two-character keying check pair computed over
everything to its left. Verifying it needs no network, no key and no registry:
it is arithmetic, and it is the cheapest quality gate we have on a field that
Invariant 3 treats as production-capable (`validate.py` links on
`match_basis="basic-udi-di"`).

**Algorithm.** Transcribed from HIBCC's *HIBC Basic UDI-DI* specification --
Table 1 (the 82-character data set and its values), Table 2 (the depleted
32-character encode set) and the worked example, which
`tests/test_udi_validation.py` asserts against directly. Multiply each data
character's value by successive primes starting at 2 from the character
adjacent to the check pair and moving LEFT, sum, take MOD 1021; encode that
value as `A1 * 32 + A2` over the 32-character set. The `++` flag is part of
the weighted payload, not a prefix to strip -- the spec's own example weights
both plus signs.

GS1's Global Model Number uses the same MOD 1021,32 scheme. GS1's check
character pair PDF is served 403 to us, so that half is pinned empirically
rather than by quotation: 185 of 185 GS1 Basic UDI-DIs published by EUDAMED
validate under this implementation, and 0 fail. Combined with the HIBCC side
that is 306 of 306 known-good codes accepted -- no false positives -- while 3
of our own 352 extracted codes fail, all three genuinely corrupt
(`docs/2026-08-20-udi-identifiers-and-registries.md` §2).

Nothing here reaches out. Deliberately: this runs upstream of any registry
question, and it is the only check that stays free and available when EUDAMED
changes shape under us.
"""
from __future__ import annotations

from typing import NamedTuple

# --------------------------------------------------------------------------- #
# issuing agencies
# --------------------------------------------------------------------------- #
HIBCC = "hibcc"
GS1 = "gs1"
#: EUDAMED mints these itself for devices whose manufacturer never obtained a
#: Basic UDI-DI: "B-" prefixed onto the UDI-DI verbatim. Observed on 148 of
#: VOCO's 865 EUDAMED devices; no normative definition was findable, so it is
#: tolerated and never scored, never rejected.
EUDAMED_ASSIGNED = "eudamed-assigned"
UNKNOWN = "unknown"

# --------------------------------------------------------------------------- #
# verdicts
# --------------------------------------------------------------------------- #
VALID = "valid"
INVALID = "invalid"
#: No check pair is computable -- an unrecognised shape, or an agency that
#: does not carry one. Distinct from INVALID on purpose: absence of a check is
#: not evidence of corruption, and conflating the two would punish a
#: manufacturer for EUDAMED's fallback naming.
UNSCORABLE = "unscorable"

#: HIBCC Table 1, in value order. Index == the character's data value.
DATA_SET = (
    "!\"%&'()*+,-./"
    "0123456789"
    ":;<=>?"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "_"
    "abcdefghijklmnopqrstuvwxyz"
)

#: HIBCC Table 2: alphanumerics with the visually confusable 0, 1, O and I
#: removed, leaving exactly 32 for the two check characters. That removal is
#: why this validator catches the OCR failure our corpus actually has.
CHECK_SET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"

#: Ascending primes, one per payload character, applied right to left. 23 of
#: them covers the longest legal payload: both agencies cap the Basic UDI-DI
#: at 25 characters including the check pair.
PRIMES = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61,
          67, 71, 73, 79, 83)

MAX_LENGTH = 25


class Verdict(NamedTuple):
    agency: str
    status: str
    detail: str


def agency_of(value: str | None) -> str:
    """Which issuing agency's rules a value is shaped like.

    Prefix only -- this answers "how do I parse this", not "who issued it".
    """
    v = (value or "").strip()
    if not v:
        return UNKNOWN
    if v.startswith("+"):
        return HIBCC
    if v.startswith("B-"):
        return EUDAMED_ASSIGNED
    if v[0].isdigit():
        return GS1
    return UNKNOWN


def check_pair(payload: str) -> str | None:
    """The two check characters that belong on the end of `payload`.

    `payload` is the whole Basic UDI-DI minus its check pair, flag included.
    Returns None when the value cannot be scored -- a character outside the
    82-character set, or a payload longer than the prime table.
    """
    if len(payload) > len(PRIMES):
        return None
    total = 0
    for i, ch in enumerate(reversed(payload)):
        pos = DATA_SET.find(ch)
        if pos < 0:
            return None
        total += pos * PRIMES[i]
    value = total % 1021
    return CHECK_SET[value // 32] + CHECK_SET[value % 32]


def validate_basic_udi(value: str | None) -> Verdict:
    """Score a Basic UDI-DI against its own check pair.

    Never raises and never rewrites the value: a caller's job is to lower its
    confidence and count the anomaly, not to discard an identifier a human may
    still need to read.
    """
    v = (value or "").strip()
    agency = agency_of(v)
    if agency in (UNKNOWN, EUDAMED_ASSIGNED):
        return Verdict(agency, UNSCORABLE, "no check pair to verify")
    if len(v) > MAX_LENGTH:
        return Verdict(
            agency, INVALID,
            f"{len(v)} characters, over the 25-character maximum "
            f"(likely two codes captured as one)")
    if len(v) < 4:
        return Verdict(agency, UNSCORABLE, "too short to carry a check pair")

    expected = check_pair(v[:-2])
    if expected is None:
        return Verdict(agency, UNSCORABLE, "characters outside the data set")
    if expected == v[-2:]:
        return Verdict(agency, VALID, f"check pair {expected}")
    return Verdict(agency, INVALID, f"expected {expected}, got {v[-2:]}")
