"""KOMET's articles matching their own EUDAMED entry (F32).

The finding said the view compares raw strings while KOMET's playbook already
declares the reorder rule VALIDATE uses, so applying that rule here would fix
it. Measured 2026-09-15: applying the fold to both sides moved the match count
**from 0 to 0**. The order of the segments was never the difference. EUDAMED
prints a fourth segment our catalogue does not -- `8885.314.012.K3` -- on 1.586
of KOMET's 1.810 mirrored rows.

Two properties are worth a test and one of them is a drift guard:

  * the SQL fold and `validate._fold_bur_code` must agree, because there are now
    two implementations of one rule. This is the class of bug F40 was: a preview
    computing the rule differently from the job;
  * the trim applies to the EUDAMED side ONLY. Our catalogue does not print the
    suffix, so trimming our refs would strip a real part of a Dentalia article
    number and invent matches rather than find them;

and one refusal: a manufacturer whose playbook declares no strategy keeps the
exact comparison, byte for byte.
"""

from __future__ import annotations

import pytest

from app.handlers.validate import _fold_bur_code

#: Every shape the rule has to survive: both spellings, the letterless one, a
#: three-digit shank that must not be read as a variant suffix, a Dentalia
#: packaging suffix, two real KOMET catalogue entries, EUDAMED's four-segment
#: form, and the empty string. The assertion over this list is agreement between
#: the two implementations, not a fixed answer per input.
FOLD_CASES = [
    "314 H1 006",          # catalogue spelling: shank figure size
    "H1.314.006",          # manufacturer spelling: figure.shank.size
    "1.204.005",           # letterless, from Komet's older list layout
    "RC.070.027",          # three digits of shank, not a variant suffix
    "104 H219A 023-1",     # Dentalia packaging suffix: must NOT fold
    "LS SFQ2008",          # not a bur code at all
    "000 SFD7 1",          # not a bur code at all
    "8885.314.012.K3",     # EUDAMED's four-segment spelling
    "",
]


def _sql_fold(conn, ref):
    return conn.execute("SELECT fold_bur_code(%s) AS f", (ref,)).fetchone()["f"]


@pytest.mark.parametrize("ref", FOLD_CASES)
def test_the_sql_fold_and_the_python_fold_agree(conn, ref):
    """The drift guard. Two implementations of one rule is exactly how a preview
    comes to disagree with the job it previews (F40), and the only thing that
    keeps them together is a test that runs both over the same input."""
    assert _sql_fold(conn, ref) == _fold_bur_code(ref)


def test_the_fold_reaches_the_same_key_from_either_spelling(conn):
    """The property the whole rule exists for: Dentalia writes '314 H1 006' and
    Komet prints 'H1.314.006', and they are the same bur."""
    assert _sql_fold(conn, "314 H1 006") == _sql_fold(conn, "H1.314.006")
    assert _sql_fold(conn, "314 H1 006") == "H1.314.006"


def test_the_size_is_padded_so_006_and_6_are_one_key(conn):
    assert _sql_fold(conn, "H1.314.6") == _sql_fold(conn, "H1.314.006")


@pytest.mark.parametrize("ref", ["104 H219A 023-1", "LS SFQ2008"])
def test_what_is_not_a_bur_code_comes_back_untouched(conn, ref):
    """Seven KOMET items carry a Dentalia packaging suffix and two of those would
    fold onto an unsuffixed sibling's key -- one document naming the base code
    would then link both articles, which is the false link invariant 3 exists to
    prevent.

    `000 SFD7 1` is deliberately NOT in this list. `validate._fold_bur_code`'s
    docstring names it alongside `LS SFQ2008` as something folding leaves alone,
    and that is wrong: it matches the spaced shape exactly (`000` + `SFD7` + `1`)
    and both implementations fold it to `SFD7.000.001`. They agree, which is what
    the drift guard above is for; the docstring was the only thing that claimed
    otherwise and it has been corrected."""
    assert _sql_fold(conn, ref) == ref


# --- the EUDAMED side ------------------------------------------------------

def test_the_variant_suffix_is_dropped_before_folding(conn):
    """`8885.314.012.K3` is one bur in EUDAMED's spelling. The fourth segment is
    a packaging or variant code that lives on the manufacturer's registration
    and never on the Dentalia article."""
    row = conn.execute(
        "SELECT eudamed_ref_key('8885.314.012.K3') AS k, fold_bur_code('314 8885 012') AS f"
    ).fetchone()
    assert row["k"] == row["f"] == "8885.314.012"


def test_a_size_is_not_mistaken_for_a_variant(conn):
    """The trim requires the last segment to START with a letter. Without that,
    'RC.070.027' would lose its size and fold to something else entirely."""
    assert conn.execute(
        "SELECT eudamed_ref_key('RC.070.027') AS k").fetchone()["k"] == "RC.070.027"


def test_our_own_refs_are_never_trimmed(conn):
    """Only the EUDAMED side. A Dentalia article number ending in a letter group
    is a real article number, and trimming it would invent matches."""
    assert _sql_fold(conn, "8885.314.012.K3") == "8885.314.012.K3"


# --- the view --------------------------------------------------------------

KOMET = "KOMET"
SRN = "DE-MF-000006446"


@pytest.fixture
def komet(conn):
    """One KOMET device item, its trusted SRN, and the authored strategy."""
    conn.execute(
        "INSERT INTO manufacturer (canonical_name, body) VALUES (%s, %s) "
        "ON CONFLICT (canonical_name) DO UPDATE SET body = EXCLUDED.body",
        (KOMET, '{"ref_normalize": {"strategy": "reorder-shank-figure-size"}}'))
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, status) "
        "VALUES (%s,%s,'text-mined','confirmed') ON CONFLICT DO NOTHING", (KOMET, SRN))
    return KOMET


def _item(conn, ref, mfr_ref, manufacturer):
    conn.execute(
        "INSERT INTO item_mirror (item_ref, name, manufacturer_raw, catalogue, "
        "  mfr_ref, md_flag, updated_at) VALUES (%s,'Bur','081','LJ',%s,TRUE,now()) "
        "ON CONFLICT (item_ref) DO UPDATE SET mfr_ref = EXCLUDED.mfr_ref", (ref, mfr_ref))
    gid = conn.execute(
        "INSERT INTO item_group (canonical_manufacturer, label) VALUES (%s,'Burs') "
        "RETURNING group_id", (manufacturer,)).fetchone()["group_id"]
    conn.execute("INSERT INTO item_group_member (group_id, item_ref, match_basis) "
                 "VALUES (%s,%s,'manual')", (gid, ref))
    return ref


def _eudamed(conn, reference, udi="++TESTUDI0001"):
    conn.execute(
        "INSERT INTO eudamed_mirror (udi_di, manufacturer_srn, basic_udi_di, "
        "  reference, first_seen, synced_at) VALUES (%s,%s,%s,%s, now(), now()) "
        "ON CONFLICT DO NOTHING",
        (f"{udi}-{reference}", SRN, udi, reference))


def test_an_item_matches_its_eudamed_row_through_the_fold(conn, komet):
    """The 108 of 258. Neither side needed to change its spelling; the view
    learned to read both."""
    ref = _item(conn, "K-FOLD-1", "314 H1 006", komet)
    _eudamed(conn, "H1.314.006.K3")

    row = conn.execute(
        "SELECT in_eudamed FROM eudamed_article_status WHERE item_ref = %s",
        (ref,)).fetchone()
    assert row and row["in_eudamed"] is True


def test_a_manufacturer_without_the_strategy_keeps_the_exact_comparison(conn):
    """KOMET is the only manufacturer that needs this (client, 2026-08-18:
    "ne -- komet je specifičen"). Everyone else must be unchanged, byte for
    byte, or this migration widened matching for 28 manufacturers nobody
    measured."""
    other = "FOLDTEST OTHER"
    conn.execute("INSERT INTO manufacturer (canonical_name) VALUES (%s) "
                 "ON CONFLICT DO NOTHING", (other,))
    conn.execute(
        "INSERT INTO manufacturer_srn (canonical_name, srn, discovered_via, status) "
        "VALUES (%s,'DE-MF-000077777','text-mined','confirmed') "
        "ON CONFLICT DO NOTHING", (other,))
    ref = _item(conn, "K-FOLD-2", "314 H1 006", other)
    conn.execute(
        "INSERT INTO eudamed_mirror (udi_di, manufacturer_srn, basic_udi_di, "
        "  reference, first_seen, synced_at) VALUES ('++OTHERUDI1-x', "
        "  'DE-MF-000077777','++OTHERUDI1','H1.314.006.K3', now(), now()) "
        "ON CONFLICT DO NOTHING")

    row = conn.execute(
        "SELECT in_eudamed FROM eudamed_article_status WHERE item_ref = %s",
        (ref,)).fetchone()
    assert row and row["in_eudamed"] is False


def test_an_exact_match_still_matches_under_the_strategy(conn, komet):
    """Folding must ADD reach, never remove any: a reference that already
    matched exactly still has to match."""
    ref = _item(conn, "K-FOLD-3", "SFQ2008", komet)
    _eudamed(conn, "SFQ2008", udi="++TESTUDI0003")

    row = conn.execute(
        "SELECT in_eudamed FROM eudamed_article_status WHERE item_ref = %s",
        (ref,)).fetchone()
    assert row and row["in_eudamed"] is True
