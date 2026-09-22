"""The D2 brand-collision guard (slice 3b, task 12).

The failure it exists for reached live data on 2026-08-10
(`[brand-parent-aliases]`): `Maillefer Instruments Holding Sarl` and `Sirona
Dental Systems GmbH` were authored as DENTSPLY aliases and synced. Both are
their own BC brands -- MAILLEFER is code 022 (74 items), SIRONA is 012 (29) --
so the aliases canonicalized 86 documents to DENTSPLY and scoped their group
search to DENTSPLY's groups, putting a Maillefer certificate one GATE approval
away from a Dentsply item.

Nothing refused it. `playbooks.validate` checked name UNIQUENESS and both
names were unique; `reconcile`'s `alias-collision` finding tested string
EQUALITY against a master name and the master carries `SIRONA`, not the legal
name. The rule here is word-boundary CONTAINMENT, which is what separates
those two strings, and it is shared by the report and the refusal so they
cannot drift.

No database: the guard is a pure function over a name and a brand map.
"""

from __future__ import annotations

import pytest

from app import playbooks as pb_mod
from app import reconcile
from app.vendor_master import VendorRow


#: The Dentsply Sirona parent as BC actually issues it: seven brands, seven
#: codes, one of which the playbook claims. Item counts from the live master.
BRANDS = {
    "DENTSPLY": frozenset({"035"}),
    "SIRONA": frozenset({"012"}),
    "MAILLEFER": frozenset({"022"}),
    "VDW": frozenset({"010"}),
    "RINN": frozenset({"115"}),
    "IVOCLAR VIVADENT": frozenset({"001", "005", "275"}),
    "ULRICH STORZ GMBH & CO. KG": frozenset({"10111"}),
}


def _pb(slug, manufacturer, codes=(), aliases=()):
    return pb_mod.Playbook(
        slug=slug,
        manufacturer=manufacturer,
        bc_codes=tuple(pb_mod.BcCode(c) for c in codes),
        aliases=tuple(aliases),
    )


# --------------------------------------------------------------------------- #
# the incident
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "alias, brand, code",
    [
        ("Sirona Dental Systems GmbH", "SIRONA", "012"),
        ("Maillefer Instruments Holding Sarl", "MAILLEFER", "022"),
        ("Maillefer Instruments Holding Sàrl", "MAILLEFER", "022"),
    ],
    ids=["sirona", "maillefer", "maillefer-accented"],
)
def test_the_brand_parent_aliases_incident_is_refused(alias, brand, code):
    """The headline case. Both aliases must be refused, NAMING the brand and
    its code -- an operator who is told only "refused" cannot act on it."""
    pb = _pb("dentsply-sirona", "DENTSPLY", codes=("035",), aliases=(alias,))

    with pytest.raises(pb_mod.BrandCollision) as exc:
        pb_mod.validate((pb,), refused=frozenset(), brands=BRANDS)

    assert brand in str(exc.value)
    assert code in str(exc.value)
    assert alias in str(exc.value)


def test_a_canonical_name_is_checked_too_not_only_aliases():
    """The live `ustomed` case: the collision is in the playbook's own
    `manufacturer`, which mis-scopes exactly as an alias does."""
    pb = _pb("ustomed", "Ustomed Instrumente Ulrich Storz GmbH & Co. KG",
             codes=("10119",))

    with pytest.raises(pb_mod.BrandCollision) as exc:
        pb_mod.validate((pb,), refused=frozenset(), brands=BRANDS)

    assert "ULRICH STORZ" in str(exc.value)


def test_the_refusal_names_both_ways_out():
    """Claim the code, or drop the name. A refusal with no exit is a wall."""
    pb = _pb("d", "DENTSPLY", codes=("035",), aliases=("Sirona Dental Systems GmbH",))

    with pytest.raises(pb_mod.BrandCollision) as exc:
        pb_mod.validate((pb,), refused=frozenset(), brands=BRANDS)

    msg = str(exc.value)
    assert "bc_codes" in msg and "drop the name" in msg


# --------------------------------------------------------------------------- #
# what must NOT be refused
# --------------------------------------------------------------------------- #
def test_a_genuinely_new_supplier_name_passes():
    pb = _pb("voco", "VOCO GmbH", codes=("041",),
             aliases=("VOCO Dental Ltd", "Voco Vertriebs GmbH"))

    pb_mod.validate((pb,), refused=frozenset(), brands=BRANDS)


def test_a_brand_the_playbook_itself_claims_is_not_a_collision():
    """`Dentsply DeTrey GmbH` contains DENTSPLY, and dentsply-sirona claims
    035. Its own brand is not a collision -- and claiming the code is exactly
    the escape the refusal offers, so it has to actually work."""
    pb = _pb("dentsply-sirona", "DENTSPLY", codes=("035",),
             aliases=("Dentsply DeTrey GmbH", "DENTSPLY Implants N.V."))

    pb_mod.validate((pb,), refused=frozenset(), brands=BRANDS)


def test_claiming_the_colliding_code_lifts_the_refusal():
    bad = _pb("d", "DENTSPLY", codes=("035",), aliases=("Sirona Dental Systems GmbH",))
    with pytest.raises(pb_mod.BrandCollision):
        pb_mod.validate((bad,), refused=frozenset(), brands=BRANDS)

    fixed = _pb("d", "DENTSPLY", codes=("035", "012"),
                aliases=("Sirona Dental Systems GmbH",))
    pb_mod.validate((fixed,), refused=frozenset(), brands=BRANDS)


def test_a_multi_code_brand_is_exempt_via_any_of_its_codes():
    """IVOCLAR VIVADENT is 001/005/275. A playbook claiming one of them owns
    the name -- the master maps a name to a SET of codes, and any overlap is
    ownership."""
    pb = _pb("ivoclar", "IVOCLAR VIVADENT", codes=("001",),
             aliases=("Ivoclar Vivadent Manufacturing GmbH",))

    pb_mod.validate((pb,), refused=frozenset(), brands=BRANDS)


# --------------------------------------------------------------------------- #
# the matching rule itself
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "name, expected",
    [
        # word boundaries: a brand name inside a longer WORD is not that brand
        ("Sironada Dental Ltd", None),
        ("Presirona AG", None),
        ("Sirona Dental Systems GmbH", "SIRONA"),
        # first, last and middle position all match
        ("Sirona", "SIRONA"),
        ("Dental Systems Sirona", "SIRONA"),
        # punctuation is not a word character, so it neither joins nor blocks
        ("Sirona-Dental, GmbH", "SIRONA"),
        ("(SIRONA)", "SIRONA"),
        # a multi-word master name matches only as an adjacent phrase
        ("Ustomed Instrumente Ulrich Storz GmbH & Co. KG",
         "ULRICH STORZ GMBH & CO. KG"),
        ("Ulrich Instrumente Storz GmbH & Co. KG", None),
        # a bare legal form is not a brand match on its own
        ("Some Other GmbH & Co. KG", None),
        # a master name longer than the candidate cannot be contained in it
        ("Ulrich", None),
        ("", None),
    ],
)
def test_containment_rule(name, expected):
    hit = pb_mod.brand_collision(name, BRANDS, claimed=frozenset())
    assert (hit[0] if hit else None) == expected


def test_brands_absent_leaves_todays_checks_unchanged():
    """A caller with no connection -- `playbooks validate` is a pure file check
    -- gets the pre-2026-08-27 behaviour, not a crash and not a silent pass of
    the other rules."""
    pb = _pb("d", "DENTSPLY", codes=("035",), aliases=("Sirona Dental Systems GmbH",))
    pb_mod.validate((pb,), refused=frozenset())
    pb_mod.validate((pb,), refused=frozenset(), brands={})

    dup = (_pb("a", "ACME", codes=("100",)), _pb("b", "OTHER", codes=("100",)))
    with pytest.raises(pb_mod.PlaybookConflict):
        pb_mod.validate(dup, refused=frozenset())


def test_brand_collision_subclasses_playbook_conflict():
    """Every operator command and the UI save already catch `PlaybookConflict`
    and render it at 422. A sibling class would need each of them changed."""
    assert issubclass(pb_mod.BrandCollision, pb_mod.PlaybookConflict)


# --------------------------------------------------------------------------- #
# why not the fuzzy matcher the spec prescribed
# --------------------------------------------------------------------------- #
def test_the_prescribed_fuzzy_scorer_cannot_see_this_failure():
    """The spec said to reuse `reconcile._suggest` (rapidfuzz `fuzz.ratio`,
    floor `SUGGEST_MIN_SCORE`). Measured here rather than argued: on the real
    incident it scores in the low teens, because the length disparity between
    a legal name and a bare brand label dominates `ratio`.

    Pinned so nobody "simplifies" this guard back into `_suggest`. `_suggest`
    stays right for what it does -- a typo of a WHOLE name, `DENSTPLY` ->
    `DENTSPLY` at 88 -- which is a different question.
    """
    from rapidfuzz import fuzz

    assert fuzz.ratio("Sirona Dental Systems GmbH", "SIRONA") < 20
    assert fuzz.ratio("Maillefer Instruments Holding Sarl", "MAILLEFER") < 20
    assert reconcile.SUGGEST_MIN_SCORE == 80.0
    # and the typo it IS for still scores above the floor
    assert fuzz.ratio("DENSTPLY", "DENTSPLY") >= reconcile.SUGGEST_MIN_SCORE


# --------------------------------------------------------------------------- #
# the report and the refusal agree
# --------------------------------------------------------------------------- #
def test_reconcile_reports_the_same_collision_the_refusal_raises():
    """One rule, two surfaces. `reconcile` is a read-only report an operator
    can skip; `sync` is the write and cannot be. They must not disagree about
    what a collision is -- until 2026-08-27 the report tested equality and
    would have passed this."""
    rows = (VendorRow("035", "DENTSPLY"), VendorRow("012", "SIRONA"))
    pbs = (_pb("dentsply-sirona", "DENTSPLY", codes=("035",),
               aliases=("Sirona Dental Systems GmbH",)),)

    r = reconcile.build(rows, pbs, {}, {"012": 29})

    (found,) = [f for f in r.findings if f.kind == "alias-collision"]
    assert found.blocking and found.items == 29
    assert "SIRONA" in found.detail and "012" in found.detail
