"""Claim check: the hand-written corpus-analysis doc's hard numbers vs measured.

`docs/dentalia-imports-corpus-analysis.md` states specific counts (1,307 PDFs,
300 scanned, per-brand inventory, §3.4 T0 yields, ...). Those came from
scratchpad probes that no longer exist. This module holds them as DATA and
checks each against what `tools.corpus` measures now — the data analog of the
`drift-check` command for the prose docs.

Two things it exists to surface: (1) drift, when the code changed under a
documented number (e.g. the S1.5 template work moving T0 yields), and (2)
method divergence — the doc's scan count used `pdftotext`/<100 chars, the
pipeline uses `pdf.is_scan`/<20, so a different number is a real methodology
difference, not a bug, and is annotated as such rather than silently reported.

Catalogue claims (export rows, distinct codes, CONFIRMED codes) come from the BC
export, not the PDF corpus — they read `not-measured` here and are answered by
`tools.catalogue` / `tools.crossref`.
"""

from __future__ import annotations

from app.extract import tiers

ROW_COLUMNS = ("id", "source", "documented", "measured", "verdict", "delta", "note")


def _brand_pdfs(brand):
    return lambda s, sup: s["by_brand"].get(brand, {}).get("pdfs")


def _brand_scanned(brand):
    return lambda s, sup: s["by_brand"].get(brand, {}).get("scanned")


def _t0_yield(field):
    return lambda s, sup: s["t0_yield"].get(field, {}).get("pct")


# Per-brand PDF + scanned counts, §1 inventory + §11 verification log.
_BRAND_PDFS = {
    "DENSTPLY": 405, "VOCO": 292, "KOMET": 186, "IVOCLAR": 174, "GC": 149,
    "DENTAURUM": 37, "NEODENT": 34, "STRAUMANN": 13, "PLANMECA": 8, "3SHAPE": 7,
    "LUMIWHITE": 1, "BREDENT": 1,
}
_BRAND_SCANNED = {
    "DENSTPLY": 204, "DENTAURUM": 33, "IVOCLAR": 27, "VOCO": 15, "KOMET": 13,
    "GC": 6, "PLANMECA": 1, "3SHAPE": 1,
}
# §3.4 T0 per-field yield estimates (30-PDF sample, 2026-07-15). Estimates, so a
# generous tolerance — the point is to flag which have drifted materially since.
_T0_YIELD = {
    "type": 75, "regulation": 60, "validity_from": 30, "validity_to": 35,
    "ref_list": 40, "basic_udi_di": 25, "cert_number": 30,
}


def _documented():
    claims: list[dict] = [
        {"id": "total-pdfs", "source": "§1", "documented": 1307, "tolerance": 0,
         "measure": lambda s, sup: s["total_pdfs"], "note": ""},
        {"id": "scanned", "source": "§1/§11", "documented": 300, "tolerance": 0,
         "measure": lambda s, sup: s["scanned"],
         "note": "doc counts scans via pdftotext/<100 chars; this measures pdf.is_scan/<20 "
                 "(the tier-router's own test) — a small delta here is a method difference"},
        {"id": "non-pdf", "source": "§8", "documented": 9, "tolerance": 0,
         "measure": lambda s, sup: len(sup) if sup is not None else None, "note": ""},
        {"id": "neodent-business", "source": "§2.3", "documented": 28, "tolerance": 0,
         "measure": lambda s, sup: s["by_brand"].get("NEODENT", {}).get("business-doc"),
         "note": "the neodent CE/ directory contamination"},
    ]
    for brand, n in _BRAND_PDFS.items():
        claims.append({"id": f"pdfs-{brand}", "source": "§1", "documented": n,
                       "tolerance": 0, "measure": _brand_pdfs(brand), "note": ""})
    for brand, n in _BRAND_SCANNED.items():
        claims.append({"id": f"scanned-{brand}", "source": "§11", "documented": n,
                       "tolerance": 0, "measure": _brand_scanned(brand),
                       "note": "pdftotext/<100 vs is_scan/<20"})
    for field, pct in _T0_YIELD.items():
        if field in tiers.TARGET:
            claims.append({"id": f"t0-yield-{field}", "source": "§3.4",
                           "documented": float(pct), "tolerance": 10.0,
                           "measure": _t0_yield(field),
                           "note": "30-PDF-sample estimate; measured is whole-corpus"})
    # Catalogue-side claims: not answerable from the PDF corpus (Chunk 3/4).
    for cid, src, val, note in (
        ("export-rows", "sweep-doc", 19091, "BC export rows — run `tools catalogue`"),
        ("distinct-codes", "sweep-doc", 381, "distinct mfr codes — run `tools catalogue`"),
        ("confirmed-codes", "sweep-doc", 20, "CONFIRMED codes — run `tools crossref`"),
    ):
        claims.append({"id": cid, "source": src, "documented": val, "tolerance": 0,
                       "measure": lambda s, sup: None, "note": note})
    return claims


DOCUMENTED = _documented()


def _verdict(documented, measured, tolerance):
    if measured is None:
        return "not-measured", None
    delta = measured - documented
    return ("match" if abs(delta) <= tolerance else "mismatch"), delta


def check(summary: dict, support: list | None = None, documented: list | None = None) -> list[dict]:
    """One row per documented claim: measured value + verdict + delta.

    `documented` defaults to the real `DOCUMENTED` set; tests inject a synthetic
    list. `support` is the non-PDF file list (for the 9-support-files claim);
    None means "not provided" and any claim that needs it reads not-measured.
    """
    claims = DOCUMENTED if documented is None else documented
    rows = []
    for c in claims:
        try:
            measured = c["measure"](summary, support)
        except (KeyError, TypeError):
            measured = None
        verdict, delta = _verdict(c["documented"], measured, c.get("tolerance", 0))
        rows.append({
            "id": c["id"], "source": c["source"], "documented": c["documented"],
            "measured": measured, "verdict": verdict, "delta": delta,
            "note": c.get("note", ""),
        })
    return rows


def render(rows: list[dict]) -> list[tuple[str, str]]:
    """Rows -> ordered (heading, body) sections for `report.write_markdown`."""
    from tools import report

    n = {v: sum(1 for r in rows if r["verdict"] == v) for v in ("match", "mismatch", "not-measured")}
    summary_line = (
        f"{len(rows)} documented claims checked: **{n['match']} match**, "
        f"**{n['mismatch']} mismatch**, {n['not-measured']} not-measured "
        f"(catalogue-side, need `tools catalogue`/`crossref`)."
    )

    def _fmt(v):
        return "" if v is None else (f"{v:.1f}" if isinstance(v, float) else str(v))

    table = report.table(
        ["claim", "src", "documented", "measured", "Δ", "verdict", "note"],
        [[r["id"], r["source"], _fmt(r["documented"]), _fmt(r["measured"]),
          _fmt(r["delta"]), r["verdict"], r["note"]] for r in rows],
    )
    mism = [r for r in rows if r["verdict"] == "mismatch"]
    detail = (
        "\n".join(f"- **{r['id']}**: documented {_fmt(r['documented'])}, "
                  f"measured {_fmt(r['measured'])} (Δ {_fmt(r['delta'])}). {r['note']}"
                  for r in mism)
        if mism else "No mismatches."
    )
    return [
        ("Summary", summary_line),
        ("All claims", table),
        ("Mismatches", detail),
    ]
