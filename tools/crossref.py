"""Manufacturer-code cross-reference: the sweep of
`docs/dentalia-manufacturer-code-sweep.md`, as re-runnable code.

BC's LJ export carries a manufacturer *code* (`Šifra proizvajalca`) with no name.
The sweep inferred code→manufacturer by joining each brand's own tracker
spreadsheet against BC `item_ref` and reading off the dominant code. This module
reproduces that: per-brand tracker configs as DATA (mirroring `t0_layout`), the
tier thresholds from the sweep doc §Method as a pure function, and a `sweep()`
that emits a **candidate list** — evidence + tier per tracker.

It deliberately does NOT write pipeline config. Per invariant 3 and the sweep
doc's own posture, only CONFIRMED entries are safe to seed, and a human promotes
them into `Ingest.mfr_ref_source_by_code` / the confirmed-code list after review.
This is the input the pre-S1.7 md-unknown middle path (P2) needs.

Key columns were verified empirically (each is the tracker column whose values
actually hit BC `item_ref`), not read off the doc — e.g. IVOCLAR joins on `Koda`
(the 6-digit code), not the 9-digit `Material`.
"""

from __future__ import annotations

import pathlib
from collections import Counter

ROW_COLUMNS = (
    "brand", "tracker_file", "key_column", "matches", "dominant_code",
    "concentration", "volume_share", "code_total", "tier", "note",
)

#: The 5 usable trackers (sweep §Method). `key_column` is the column that joins
#: to BC `item_ref`, confirmed by match count against the real export.
TRACKERS = [
    {"brand": "IVOCLAR", "file": "IVOCLAR/IVOCLAR MD or NOT.xlsx", "key_column": "Koda"},
    {"brand": "IVOCLAR", "file": "IVOCLAR/Copy of Dentalia_UDI.xlsx", "key_column": "Material"},
    {"brand": "GC", "file": "GC/Devices by Class_22042026.xlsx", "key_column": "Art code"},
    {"brand": "KOMET", "file": "KOMET/1-Where to find DoC (Medical Devices Only).xlsx",
     "key_column": "Reference no."},
    {"brand": "VOCO", "file": "VOCO/Regulatory Product Data/20260305_voco_en.xlsx",
     "key_column": "Exact supplier item number"},
]


def tier(basis: str, matches: int, concentration: float, volume_share: float) -> str | None:
    """CONFIRMED / LIKELY / WEAK / None per the sweep doc §Method thresholds.

    `basis` is 'tracker' or 'text' (text-match has a stricter concentration bar
    since a text hit is weaker evidence than a tracker join)."""
    if matches == 0:
        return None
    if basis == "tracker" and matches >= 20 and concentration >= 0.60:
        return "CONFIRMED"
    if basis == "text" and matches >= 10 and concentration >= 0.85:
        return "CONFIRMED"
    if matches >= 5 and concentration >= 0.50 and volume_share >= 0.05:
        return "LIKELY"
    return "WEAK"


def cross_reference(tracker_keys, bc_rows: list[dict]) -> dict:
    """Join tracker key values against BC `item_ref`; report the dominant
    manufacturer code among the matched rows and its concentration/volume share.

    `bc_rows` is the FULL catalogue (needed to compute the dominant code's total
    volume). Blank codes never win the dominant tally."""
    keys = set(tracker_keys)
    matched = [r for r in bc_rows if r["item_ref"] in keys]
    by_code = Counter(r["manufacturer_raw"] for r in matched if r["manufacturer_raw"])
    if not by_code:
        return {"matches": len(matched), "dominant_code": None,
                "concentration": 0.0, "volume_share": 0.0, "code_total": 0, "by_code": {}}
    dominant, dom_count = by_code.most_common(1)[0]
    code_total = sum(1 for r in bc_rows if r["manufacturer_raw"] == dominant)
    return {
        "matches": len(matched),
        "dominant_code": dominant,
        "concentration": dom_count / len(matched),
        "volume_share": dom_count / code_total if code_total else 0.0,
        "code_total": code_total,
        "by_code": dict(by_code),
    }


def read_tracker_keys(path, key_column: str, sheet=0) -> set[str]:
    """Distinct non-blank values of `key_column` in a tracker spreadsheet. Raises
    if the column is absent (a renamed tracker column must fail loudly)."""
    import pandas as pd

    df = pd.read_excel(path, sheet_name=sheet, dtype=str, keep_default_na=False)
    if key_column not in df.columns:
        raise ValueError(
            f"tracker {pathlib.Path(path).name} missing key column {key_column!r}; "
            f"got {list(df.columns)[:12]}"
        )
    return {str(v).strip() for v in df[key_column] if str(v).strip()}


def _candidate(t: dict, xr: dict | None, note: str) -> dict:
    xr = xr or {}
    matches = xr.get("matches", 0)
    tr = tier("tracker", matches, xr.get("concentration", 0.0), xr.get("volume_share", 0.0)) if xr else None
    return {
        "brand": t["brand"], "tracker_file": t["file"], "key_column": t["key_column"],
        "matches": matches, "dominant_code": xr.get("dominant_code"),
        "concentration": round(xr.get("concentration", 0.0), 3),
        "volume_share": round(xr.get("volume_share", 0.0), 3),
        "code_total": xr.get("code_total", 0), "tier": tr or "", "note": note,
    }


def sweep(corpus_root, export_path, cfg=None) -> list[dict]:
    """One candidate row per configured tracker. Missing/unreadable trackers get
    a noted row rather than aborting the sweep."""
    from tools import catalogue

    bc_rows, _ = catalogue.profile(export_path, cfg=cfg)
    root = pathlib.Path(corpus_root)
    out = []
    for t in TRACKERS:
        p = root / t["file"]
        if not p.is_file():
            out.append(_candidate(t, None, "tracker file absent"))
            continue
        try:
            keys = read_tracker_keys(p, t["key_column"], t.get("sheet", 0))
        except Exception as exc:
            out.append(_candidate(t, None, f"read error: {type(exc).__name__}: {exc}"))
            continue
        out.append(_candidate(t, cross_reference(keys, bc_rows), ""))
    return sorted(out, key=lambda r: (r["brand"], r["tracker_file"]))


def render(rows: list[dict]) -> list[tuple[str, str]]:
    """Candidate rows -> ordered (heading, body) sections for `write_markdown`."""
    from tools import report

    tiers = Counter(r["tier"] or "none" for r in rows)
    summary = (
        f"{len(rows)} trackers swept: "
        + ", ".join(f"{n} {t}" for t, n in sorted(tiers.items()))
        + ". CONFIRMED rows are candidates for `Ingest.mfr_ref_source_by_code` / the "
          "confirmed-code list — a human promotes them after review; this tool never writes config."
    )
    table = report.table(
        ["brand", "tracker", "key col", "matches", "dominant code", "conc.", "vol.", "tier", "note"],
        [[r["brand"], pathlib.Path(r["tracker_file"]).name, r["key_column"], r["matches"],
          r["dominant_code"] or "", r["concentration"], r["volume_share"], r["tier"], r["note"]]
         for r in rows],
    )
    return [("Summary", summary), ("Candidates", table)]
