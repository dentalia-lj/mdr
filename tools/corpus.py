"""PDF corpus analyzer: walk a tree, classify each document, measure T0.

One row per PDF, one summary over the rows. Every classification comes from the
pipeline's own code — `pdf.is_scan` decides scanned, `t0_templates.t0_extract`
decides doc_class and fields, `t0_layout.match` decides which S1.5 manufacturer
template fires — so the report describes real pipeline behaviour, not a parallel
implementation of it.

Reads only. No database, no network, no LLM: T0 is deterministic and free, so a
re-run on unchanged input reproduces byte-identical output.
"""

from __future__ import annotations

import pathlib
from collections import Counter

from app.extract import pdf as pdfutil
from app.extract import t0_layout, tiers
from app.extract.t0_templates import steering_playbook as t0_steering
from app.extract.t0_templates import t0_extract

_BASE_COLUMNS = (
    "relpath", "brand", "bytes", "pages", "doc_class", "is_scan",
    "template", "pb_slug", "ref_count", "error",
)

#: CSV/row schema. One `t0_<field>` column per target field, holding the T0
#: confidence, or "" when T0 produced no value for it.
COLUMNS = _BASE_COLUMNS + tuple(f"t0_{f}" for f in tiers.TARGET)

#: t0_extract short-circuits these with no fields — they are never extraction
#: targets, so counting them as T0 misses would understate the yield.
INELIGIBLE = ("business-doc", "msds")

_DOC_CLASSES = ("compliance-doc", "unknown", "business-doc", "msds")


def _is_pdf(path: pathlib.Path) -> bool:
    return path.is_file() and path.suffix.lower() == ".pdf"


def _scan_one(root: pathlib.Path, path: pathlib.Path, playbooks=None) -> dict:
    rel = path.relative_to(root).as_posix()
    row = dict.fromkeys(COLUMNS, "")
    row["relpath"] = rel
    row["brand"] = rel.split("/")[0] if "/" in rel else ""
    row["bytes"] = path.stat().st_size
    row["pages"] = 0
    row["is_scan"] = False

    try:
        doc = pdfutil.open_doc(str(path))
    except Exception as exc:                      # unreadable/corrupt: one bad
        row["error"] = f"{type(exc).__name__}: {exc}"   # file must never kill a
        return row                                       # 1,307-file run

    try:
        row["pages"] = doc.page_count
        row["is_scan"] = pdfutil.is_scan(doc)
        template = t0_layout.match(pdfutil.first_page_text(doc))
        row["template"] = template.manufacturer if template else ""

        # Resolve the manufacturer's playbook from the document text and hand
        # it to T0, which is what the pipeline does for a document with no
        # group (`steering_playbook`'s own fallback). Until 2026-08-31 this
        # called `t0_extract(doc, str(path))` with no playbook at all, so the
        # report measured extraction with steering SWITCHED OFF -- an authored
        # `date_labels`, `cert_number_pattern` or `ref_pattern` was invisible
        # here, and a scan could not tell a working rule from a broken one.
        # `pb_slug` is recorded so a resolution MISS is visible rather than
        # reading as "the rule changed nothing".
        pb = t0_steering(
            "\n".join(pdfutil.page_texts(doc)), playbooks=playbooks
        ) if playbooks is not None else None
        row["pb_slug"] = pb.slug if pb else ""
        doc_class, fields = t0_extract(doc, str(path), playbook=pb)
        row["doc_class"] = doc_class
        for name, ev in fields.items():
            if f"t0_{name}" in row:
                row[f"t0_{name}"] = ev.get("conf", "")
        ref = fields.get("ref_list")
        if ref and ref.get("value") is not None:
            row["ref_count"] = len(ref["value"])
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        doc.close()

    return row


def scan(root, playbooks=None) -> list[dict]:
    """One row per PDF under `root`, sorted by relative path (determinism).

    `playbooks` steers T0 the way the pipeline does. Pass `None` (the default)
    for the unsteered baseline -- the two together are how an authored playbook
    rule is measured, since the difference between them IS the rule's effect.
    Callers load the tuple themselves so this module keeps its promise of no
    database and no network: `playbooks.load_playbooks()` reads files, or rows
    if the caller has set a source."""
    root = pathlib.Path(root)
    rows = [_scan_one(root, p, playbooks) for p in root.rglob("*") if _is_pdf(p)]
    return sorted(rows, key=lambda r: r["relpath"])


def support_files(root) -> list[str]:
    """Non-PDF files in the tree — trackers and spreadsheets. Corpus inventory,
    not extraction targets; `tools.crossref` is what reads them."""
    root = pathlib.Path(root)
    return sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() != ".pdf"
    )


def _pct(n: int, total: int) -> float:
    return round(100.0 * n / total, 1) if total else 0.0


def aggregate(rows: list[dict]) -> dict:
    """Summary over `scan` rows. Percentages are rounded, so equal inputs give
    equal outputs — the property the whole exercise exists to create."""
    total = len(rows)
    eligible = [r for r in rows if not r["error"] and r["doc_class"] not in INELIGIBLE]
    n_eligible = len(eligible)

    by_brand: dict[str, dict] = {}
    brand_eligible: dict[str, list[dict]] = {}
    for row in rows:
        b = by_brand.setdefault(
            row["brand"],
            {"pdfs": 0, "scanned": 0, "errors": 0} | dict.fromkeys(_DOC_CLASSES, 0),
        )
        b["pdfs"] += 1
        b["scanned"] += bool(row["is_scan"])
        b["errors"] += bool(row["error"])
        if row["doc_class"] in b:
            b[row["doc_class"]] += 1
        brand_eligible.setdefault(row["brand"], [])
        if not row["error"] and row["doc_class"] not in INELIGIBLE:
            brand_eligible[row["brand"]].append(row)

    # Per-brand yield answers "which manufacturers are cheap to extract" — the
    # input to S1.7's top-~20-manufacturer pilot choice.
    for brand, stat in by_brand.items():
        eligible_rows = brand_eligible[brand]
        stat["eligible"] = len(eligible_rows)
        stat["t0_yield"] = {
            f: _pct(sum(1 for r in eligible_rows if r[f"t0_{f}"] != ""), len(eligible_rows))
            for f in tiers.TARGET
        }

    t0_yield = {}
    for f in tiers.TARGET:
        n = sum(1 for r in eligible if r[f"t0_{f}"] != "")
        t0_yield[f] = {"n": n, "pct": _pct(n, n_eligible)}

    fully_missed = sum(
        1 for r in eligible if all(r[f"t0_{f}"] == "" for f in tiers.TARGET)
    )

    templates: dict[str, dict] = {}
    for row in rows:
        if not row["template"]:
            continue
        t = templates.setdefault(row["template"], {"docs": 0, "docs_with_ref": 0, "ref_codes": 0})
        t["docs"] += 1
        if row["ref_count"] != "":
            t["docs_with_ref"] += 1
            t["ref_codes"] += row["ref_count"]

    with_ref = [r["ref_count"] for r in rows if r["ref_count"] != ""]

    return {
        "total_pdfs": total,
        "total_bytes": sum(r["bytes"] for r in rows),
        "errors": sum(1 for r in rows if r["error"]),
        "scanned": sum(1 for r in rows if r["is_scan"]),
        "scanned_pct": _pct(sum(1 for r in rows if r["is_scan"]), total),
        "extraction_eligible": n_eligible,
        "doc_class": dict(sorted(Counter(r["doc_class"] for r in rows).items())),
        "by_brand": dict(sorted(by_brand.items())),
        "t0_yield": t0_yield,
        "t0_fully_missed": fully_missed,
        "t0_fully_missed_pct": _pct(fully_missed, n_eligible),
        "templates": dict(sorted(templates.items())),
        "ref_list": {
            "docs_with_ref": len(with_ref),
            "total_codes": sum(with_ref),
            "max_codes": max(with_ref, default=0),
        },
    }


_METHOD = """\
Every number here is measured by the pipeline's own deterministic code, not a
re-implementation: `app.extract.pdf.is_scan` decides scanned (PyMuPDF, page-1
text under 20 non-whitespace characters), `app.extract.t0_templates.t0_extract`
decides `doc_class` and the T0 fields, and `app.extract.t0_layout.match` decides
which S1.5 manufacturer template fires. No LLM tier ran: T0 only, zero cost, so
re-running on unchanged input reproduces this report exactly.

`business-doc` and `msds` short-circuit before field extraction, so the T0 yield
denominator below is **extraction-eligible** documents ({eligible} of {total}),
not the whole tree."""


def render(summary: dict, support: list[str]) -> list[tuple[str, str]]:
    """Summary -> ordered (heading, body) sections for `report.write_markdown`."""
    from tools import report

    brands = summary["by_brand"]

    inventory = report.table(
        ["brand", "PDFs", "scanned", "scan %", "compliance", "unknown", "business", "msds", "errors"],
        [
            [b, s["pdfs"], s["scanned"], _pct(s["scanned"], s["pdfs"]),
             s["compliance-doc"], s["unknown"], s["business-doc"], s["msds"], s["errors"]]
            for b, s in brands.items()
        ],
    )
    totals = (
        f"**{summary['total_pdfs']} PDFs** across {len(brands)} brands · "
        f"{summary['scanned']} scanned ({summary['scanned_pct']}%) · "
        f"{summary['extraction_eligible']} extraction-eligible · "
        f"{summary['errors']} unreadable."
    )

    yield_table = report.table(
        ["field", "extracted", "% of eligible"],
        [[f, s["n"], s["pct"]] for f, s in summary["t0_yield"].items()],
    )
    missed = (
        f"\n\n**{summary['t0_fully_missed']} eligible documents "
        f"({summary['t0_fully_missed_pct']}%) yielded no T0 field at all** — "
        f"these are the documents the sweep pays T1/T2 to read."
    )

    by_brand_yield = report.table(
        ["brand", "eligible", *summary["t0_yield"]],
        [
            [b, s["eligible"], *(s["t0_yield"][f] for f in summary["t0_yield"])]
            for b, s in brands.items()
        ],
    )

    templates = summary["templates"]
    template_body = (
        report.table(
            ["template", "docs matched", "docs with REF list", "REF codes"],
            [[t, s["docs"], s["docs_with_ref"], s["ref_codes"]] for t, s in templates.items()],
        )
        if templates
        else "No S1.5 layout template matched any document in this tree."
    )

    ref = summary["ref_list"]
    ref_body = (
        f"{ref['docs_with_ref']} documents carry a REF list, "
        f"{ref['total_codes']} codes in total, largest single list {ref['max_codes']}."
    )

    support_body = (
        "\n".join(f"- `{p}`" for p in support)
        if support
        else "None found."
    )

    return [
        ("Method", _METHOD.format(
            eligible=summary["extraction_eligible"], total=summary["total_pdfs"])),
        ("Inventory", f"{totals}\n\n{inventory}"),
        ("T0 field yield", yield_table + missed),
        ("T0 yield by brand", by_brand_yield),
        ("Layout templates", template_body),
        ("REF-list recovery", ref_body),
        ("Support files", support_body),
    ]
