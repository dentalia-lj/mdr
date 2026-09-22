"""BC export analyzer: profile a catalogue snapshot the way INGEST sees it.

Drives the pipeline's own `CsvExportAdapter` (+ `normalize_record`), so the
`md_flag` tri-state, `mfr_ref` sourcing, and item-ref shapes reported here are
byte-for-byte what INGEST would act on — not a parallel reader. Read-only; no DB,
no enqueue.

Feeds the pre-S1.7 threshold-calibration session (P4): the per-manufacturer-code
counts and the name/mfr_ref columns are the sampling frame for hand-labeling
RESOLVE name pairs, and `missing_mfr_ref` is today's AC1 ceiling input.
"""

from __future__ import annotations

import pathlib
from collections import Counter

from app.adapters.source import CsvExportAdapter
from app.config import Ingest

COLUMNS = (
    "item_ref", "name", "manufacturer_raw", "mfr_ref", "md_flag",
    "product_class", "has_mfr_ref", "item_ref_style",
)

#: chars that mark a "dotted"/suffixed item ref vs a plain BC No. (§G4).
_DOTTED = set("./-")


def _style(item_ref: str | None) -> str:
    if not item_ref:
        return "plain"
    return "dotted" if any(c in _DOTTED for c in item_ref) else "plain"


def _pct(n: int, total: int) -> float:
    return round(100.0 * n / total, 1) if total else 0.0


def _row(nr) -> dict:
    return {
        "item_ref": nr.item_ref,
        "name": nr.name,
        "manufacturer_raw": nr.manufacturer_raw,
        "mfr_ref": nr.mfr_ref,
        "md_flag": nr.md_flag,
        "product_class": nr.product_class,
        "has_mfr_ref": nr.mfr_ref is not None,
        "item_ref_style": _style(nr.item_ref),
    }


def profile(path: str, cfg: Ingest | None = None, catalogue: str = "LJ") -> tuple[list[dict], dict]:
    """One row per catalogue item + a summary. Uses the real adapter, so a
    renamed/missing BC column raises (the adapter's schema-drift guard) rather
    than silently mapping every row to None."""
    cfg = cfg or Ingest()
    rows = [_row(nr) for nr in CsvExportAdapter(path, catalogue, cfg).read()]

    total = len(rows)
    tri = {"true": 0, "false": 0, "unknown": 0}
    for r in rows:
        tri["true" if r["md_flag"] is True else "false" if r["md_flag"] is False else "unknown"] += 1

    missing = sum(1 for r in rows if not r["has_mfr_ref"])
    by_code = Counter(r["manufacturer_raw"] or "" for r in rows)
    style = Counter(r["item_ref_style"] for r in rows)

    summary = {
        "total_items": total,
        "md_flag": tri,
        "missing_mfr_ref": {"n": missing, "pct": _pct(missing, total)},
        "distinct_codes": len(by_code),
        "by_code": dict(by_code),
        "item_ref_style": {"plain": style.get("plain", 0), "dotted": style.get("dotted", 0)},
    }
    return rows, summary


def render(summary: dict, top: int = 20) -> list[tuple[str, str]]:
    """Summary -> ordered (heading, body) sections for `report.write_markdown`."""
    from tools import report

    tri = summary["md_flag"]
    total = summary["total_items"]
    class_body = report.table(
        ["md_flag", "items", "% of catalogue"],
        [["confirmed MD (True)", tri["true"], _pct(tri["true"], total)],
         ["non-MD (False)", tri["false"], _pct(tri["false"], total)],
         ["unknown (blank)", tri["unknown"], _pct(tri["unknown"], total)]],
    )

    mref = summary["missing_mfr_ref"]
    mref_body = (
        f"{mref['n']} of {total} items ({mref['pct']}%) have no `mfr_ref` — "
        f"today's AC1 ceiling input (per-supplier `item_ref`-as-mfr_ref mapping is "
        f"what lifts it, findings-phase0 §G4)."
    )

    top_codes = sorted(summary["by_code"].items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    codes_body = report.table(
        ["code", "items", "% of catalogue"],
        [[c or "(blank)", n, _pct(n, total)] for c, n in top_codes],
    )

    style = summary["item_ref_style"]
    style_body = (
        f"plain (BC No. usable as the manufacturer number for §G4 CONFIRMED "
        f"suppliers): {style['plain']} ({_pct(style['plain'], total)}%) · "
        f"dotted/suffixed: {style['dotted']} ({_pct(style['dotted'], total)}%)."
    )

    return [
        ("Overview", f"**{total} items**, {summary['distinct_codes']} distinct manufacturer codes."),
        ("Device-class split", class_body),
        ("mfr_ref coverage", mref_body),
        ("Item-ref style", style_body),
        (f"Top manufacturer codes", f"Top {len(top_codes)} by item count:\n\n{codes_body}"),
    ]
