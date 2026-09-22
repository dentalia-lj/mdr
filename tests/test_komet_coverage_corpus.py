"""The measured coverage of Komet's index, pinned.

Skips when the local corpus is absent, exactly as tests/conftest.py's
`corpus_pdf` fixture does -- these numbers are Denis's machine, not CI.

Measured 2026-08-18: the two index files cover 246 of the 319 catalogue items
on BC code 077, and 229 of the 258 that BC flags as medical devices. The floor
is what those numbers must not silently fall below; when the index or the
catalogue moves, this is the test that says so.

Re-verified 2026-08-19 directly against the archived xlsx/docx and a read-only
query of the real `dentalia` database's item_mirror: 3800 xlsx rows over 42
keys, 39 docx rows over 11 keys, 246/319 items covered, 229/258 device items
covered -- exactly the numbers above. No discrepancy to report.
"""

import pathlib
import re

import pytest

from app import coverage_map as cm, playbooks
from app.handlers import backfill as bh
from app.handlers.validate import _fold_bur_code as fold

CORPUS = pathlib.Path("imports/dentalia-sftp/KOMET")

pytestmark = pytest.mark.skipif(not CORPUS.is_dir(), reason="local corpus absent")

ITEMS_FLOOR = 246
DEVICE_ITEMS_FLOOR = 229


def _keys():
    pb = playbooks.for_manufacturer("KOMET")
    keys = set()
    for source in pb.coverage_map["sources"]:
        for row in cm.read_source(CORPUS, source):
            keys.add(row.article)
            keys.add(fold(row.reference))
    return keys


def test_the_index_covers_the_measured_share_of_the_catalogue(conn):
    keys = _keys()
    rows = conn.execute(
        "SELECT item_ref, COALESCE(mfr_ref,'') mfr_ref, md_flag "
        "FROM item_mirror WHERE manufacturer_raw='077'").fetchall()
    if not rows:
        pytest.skip("Komet catalogue not ingested on this database")

    def covered(r):
        for v in (r["item_ref"], r["mfr_ref"]):
            v = (v or "").strip()
            if v and (v in keys or fold(v) in keys):
                return True
        return False

    total = sum(1 for r in rows if covered(r))
    devices = sum(1 for r in rows if r["md_flag"] and covered(r))
    assert total >= ITEMS_FLOOR, f"{total} of {len(rows)} covered, floor {ITEMS_FLOOR}"
    assert devices >= DEVICE_ITEMS_FLOOR, (
        f"{devices} device items covered, floor {DEVICE_ITEMS_FLOOR}")


def test_no_suppression_crosses_a_family_boundary():
    """Controller addendum, carried from Task 3: `backfill` suppresses a bare
    declaration when a canonical merged form of the same (family, regulation)
    exists (`_canonical_choice`). On the real KOMET tree this is correct --
    the date-prefixed `26.05.2024_100-008_DoC_List_EU...` file's key regex
    captures the garbage '26', which collides with nothing and so suppresses
    nothing and is suppressed by nothing.

    Re-derives each redundant file's family key and its winner's family key
    independently of `_canonical_choice`'s own bookkeeping (rather than
    trusting the dict it returns), so a future change to how the winner is
    picked cannot start silently suppressing across families without this
    test catching it. The failure this guards against: a future Komet dump
    adds a file matching a `prefer` pattern with no regulation token AND a
    colliding key, silently suppressing a declaration that should have been
    filed.
    """
    pb = playbooks.for_manufacturer("KOMET")
    rule = pb.coverage_map["canonical"]
    candidates = sorted(bh._pdfs(CORPUS))
    _redundant_paths, canon = bh._canonical_choice(candidates, rule)
    # A vacuous pass (nothing suppressed) would prove nothing -- the real tree
    # is known to suppress at least the three `prefer`-matching bare/merged
    # pairs this assertion exists to guard.
    assert canon["redundant"], "expected at least one suppression on the real tree"

    key_re = re.compile(rule["key"])

    def family(name: str) -> str:
        m = key_re.search(name)
        return m.group(1) if m else name

    cross_family = [
        (path.name, reason) for path, reason in canon["redundant"].items()
        if family(path.name) != family(reason.removeprefix("superseded by "))
    ]
    assert cross_family == [], f"cross-family suppression(s): {cross_family}"
