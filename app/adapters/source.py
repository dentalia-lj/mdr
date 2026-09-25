"""INGEST source adapters (S1.1) — PRD §1, handbook §1.

`ingest.run` reads a BC catalogue snapshot and normalizes it to a fixed row
shape the rest of the pipeline consumes. Two readers, ONE normalization core:

    CsvExportAdapter  (v1) — a BC export file (.xlsx / .csv), pandas-read.
    BcApiAdapter      (v2) — BC OData; a stub here (no live endpoint yet).

Both feed the same `normalize_record`, so their downstream output is byte-
identical — nothing after INGEST may detect which source is live (invariant 11).

Normalization owns three data facts Phase 0 established on the real LJ export
(docs/dentalia-findings-phase0.md §G4):

  * `md_flag` is TRI-STATE. BC's device-class column carries a real MD class
    (`RAZRED IIA` ...), an explicit non-MD marker (`NI MP`), or is BLANK (61% —
    class not yet recorded). We keep all three as True / False / None; the
    handler skips only explicit False and lets config decide the None rows.
  * `mfr_ref` (C1) is a LOGICAL field. It sources from the vendor-article column
    by default, or from `item_ref` itself for suppliers whose BC item No. IS the
    manufacturer article number (`Ingest.mfr_ref_source_by_code`).
  * No UDI column exists in the LJ export -> `udi` is always None there.

The adapter NEVER drops a row (not even one missing its key). It yields a
NormalizedRow for every input record with `raw` attached; the handler decides
what to skip and reports it (CLAUDE.md: skipped rows are counted, never silent).
"""

from __future__ import annotations

import io
import pathlib
import re
from collections.abc import Iterator
from dataclasses import dataclass

from app.config import Ingest

__all__ = [
    "NormalizedRow",
    "normalize_record",
    "CsvExportAdapter",
    "UploadExportAdapter",
    "BcApiAdapter",
    "make_source_adapter",
    "LJ_CSV_PROFILE",
    "LJ_ODATA_PROFILE",
    "EXPORT_SUFFIXES",
]


@dataclass(frozen=True)
class NormalizedRow:
    """One catalogue item, source-agnostic. `md_flag` tri-state; `mfr_ref`/`udi`
    nullable (C1). `raw` is the original record, kept for skipped-row reporting."""

    item_ref: str | None
    name: str | None
    manufacturer_raw: str | None
    mfr_ref: str | None
    md_flag: bool | None
    product_class: str | None
    udi: str | None
    catalogue: str
    raw: dict


# BC device-class vocabulary (same backend whether read as CSV or OData).
_NON_MD_MARKER = "NI MP"
_MD_PREFIX = "RAZRED"
# A roman-numeral class core (I/II/III) optionally followed by a letter subclass
# (Is/Im/Ir, IIa/IIb). Anchored after the RAZRED prefix.
_CLASS_RE = re.compile(r"^(I{1,3})\s*([A-Za-z]*)$")


def _clean(v) -> str | None:
    """Strip to a non-empty string, else None. Blank / NaN -> None."""
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _parse_device_class(md_class: str | None) -> tuple[bool | None, str | None]:
    """Map BC device-class text -> (md_flag, product_class).

    RAZRED <class> -> (True, normalized class) · NI MP -> (False, None) ·
    blank/unknown -> (None, None). An MD row with an unparseable class keeps a
    best-effort class string rather than silently dropping the signal.
    """
    if md_class is None:
        return None, None
    upper = md_class.upper()
    if upper == _NON_MD_MARKER:
        return False, None
    if upper.startswith(_MD_PREFIX):
        # Match on the upper-cased remainder so a lower/mixed-case export
        # ("razred iia") still normalizes to the canonical "IIa".
        rest = upper[len(_MD_PREFIX):].strip()
        m = _CLASS_RE.match(rest)
        if m:
            core, sub = m.group(1).upper(), m.group(2).lower()
            return True, core + sub
        return True, rest or None  # MD, class unparseable -> carry what we have
    return None, None  # unrecognized vocabulary -> unknown, not a crash


def normalize_record(raw: dict, catalogue: str, cfg: Ingest) -> NormalizedRow:
    """Derive a NormalizedRow from a logical-field dict.

    `raw` keys: item_ref, name, manufacturer_raw, mfr_ref, md_class, udi
    (raw strings or None). Values are cleaned; `md_flag`/`product_class` derived
    from md_class; `mfr_ref` sourced per the C1 override map.
    """
    item_ref = _clean(raw.get("item_ref"))
    manufacturer_raw = _clean(raw.get("manufacturer_raw"))
    md_flag, product_class = _parse_device_class(_clean(raw.get("md_class")))

    # C1 mfr_ref logical mapping: default = vendor-article column; a per-supplier
    # override (keyed by manufacturer code) can source it from item_ref instead.
    source = cfg.mfr_ref_source_by_code.get(manufacturer_raw or "", "mfr_ref")
    mfr_ref = item_ref if source == "item_ref" else _clean(raw.get("mfr_ref"))

    return NormalizedRow(
        item_ref=item_ref,
        name=_clean(raw.get("name")),
        manufacturer_raw=manufacturer_raw,
        mfr_ref=mfr_ref,
        md_flag=md_flag,
        product_class=product_class,
        udi=_clean(raw.get("udi")),
        catalogue=catalogue,
        raw=raw,
    )


# --------------------------------------------------------------------------- #
# Column profiles — physical source shape -> logical field. Versioned with the
# format knowledge (code, not config): the Slovenian LJ export headers are an
# intrinsic property of that BC export, not an operator setting. There is one
# Business Central and one article numbering (Denis, 2026-08-19, closing
# PHASES.md G17/G11) -- the Zagreb operation adds items to it, not a second
# export shape -- so LJ is the only profile there is. A genuinely different BC
# export would add a profile here; it would not reshape the normalization core.
# `name_fallback` is used only when the primary name column is blank.
# --------------------------------------------------------------------------- #
LJ_CSV_PROFILE = {
    "item_ref": "Št.",
    "name": "Opis",
    "name_fallback": "Opis za iskanje",
    "manufacturer_raw": "Šifra proizvajalca",
    "mfr_ref": "Dobaviteljeva št. artikla",
    "md_class": "Razred medicinskega pripomočka",
    # No UDI / Basic UDI-DI column exists in the LJ export (Phase 0 §G4).
}

# OData property names for the v2 BC adapter stub. Distinct from the CSV headers
# but mapped to the SAME logical keys, so both routes hit one normalization core.
# Corrected 2026-09-07 against the captured payload. Every value here was
# PascalCase, guessed before anyone had seen a live response, and BC's v1.0 API
# pages return camelCase -- so the profile was wrong on all six keys and nothing
# said so, because a missing property reads as None all the way through
# normalization. `UnknownOdataProperty` below is the guard that ends that.
#
# `udi` is deliberately absent: no property carries one. `gtin` is the only
# candidate and a GTIN is not a Basic UDI-DI, so mapping it would manufacture
# evidence rather than read it.
LJ_ODATA_PROFILE = {
    "item_ref": "no",
    "name": "description",
    "name_fallback": "searchDescription",
    "manufacturer_raw": "pteManufCodePrimary",
    "mfr_ref": "vendorItemNo",
    "md_class": "pteMedicalDeviceClass",
}


class UnknownOdataProperty(KeyError):
    """A profile names a property the payload does not have.

    Raised on the FIRST record rather than tolerated. The failure this prevents
    was measured on 2026-09-03: with the old PascalCase profile, three records
    went in and three all-null rows came out, no error and no anomaly -- an
    import that reports success and mirrors nothing.
    """

_LOGICAL_FIELDS = ("item_ref", "name", "manufacturer_raw", "mfr_ref", "md_class", "udi")


#: Profile keys whose absence from a payload is a fact, not a typo.
#: `name_fallback` is a fallback by definition -- a source without one simply
#: has nothing to fall back to, which is how the CSV route has always behaved.
#: Everything else is an identity or matching field: if it is not there under
#: the name we asked for, we are reading the wrong property.
_OPTIONAL_PROFILE_KEYS = ("name_fallback",)


def _assert_profile_matches(record: dict, profile: dict) -> None:
    """Every physical name in `profile` must exist on the record.

    Checked once, against the first record, because a page is homogeneous and
    the point is to fail before anything is written -- not to pay a set
    comparison per row. Present-but-empty is fine: a blank device class is a
    real value and `data_anomaly` already reports it.
    """
    missing = sorted(
        (logical, physical)
        for logical, physical in profile.items()
        if logical not in _OPTIONAL_PROFILE_KEYS and physical not in record
    )
    if missing:
        detail = ", ".join(f"{logical} -> {physical!r}" for logical, physical in missing)
        raise UnknownOdataProperty(
            f"OData profile names properties this payload does not have: {detail}. "
            f"BC v1.0 pages return camelCase; the payload has "
            f"{sorted(record)[:8]}..."
        )


def _raw_from_profile(record: dict, profile: dict) -> dict:
    """Pull the logical-field dict `normalize_record` consumes out of a physical
    record, using `profile` (logical -> physical key). `name` honours an optional
    `name_fallback` physical column when the primary is blank. Missing physical
    keys -> None (the field is absent in this source)."""
    raw = {f: record.get(profile[f]) for f in _LOGICAL_FIELDS if f in profile}
    if not _clean(raw.get("name")) and "name_fallback" in profile:
        raw["name"] = record.get(profile["name_fallback"])
    return raw


#: Export file types the reader accepts. The web upload form validates against
#: this exact tuple, so it can never reject a file the adapter would have read
#: nor accept one it would raise on.
EXPORT_SUFFIXES = (".xlsx", ".xlsm", ".xls", ".csv")


def read_frame(source, name: str):
    """Read a BC export into a DataFrame. `source` is a path or a binary
    file-like; `name` supplies the suffix and the error text.

    ONE reader for every route, so the upload path cannot drift from the file
    path (invariant 11). Public, and shared beyond this module:
    `app/vendor_master.py` reads the manufacturer master through it too, so a
    change to suffix handling or to the pandas options below changes the item
    import and the vendor import together -- which is the point. Everything is
    read as strings (`dtype=str`,
    `keep_default_na=False`): BC item numbers and vendor article numbers are
    codes -- a leading-zero or long numeric silently coerced to a float would
    corrupt the REF-gate key. Blank cells arrive as '' and normalize to None.
    """
    import pandas as pd

    suffix = pathlib.Path(name).suffix.lower()
    opts = {"dtype": str, "keep_default_na": False}
    if suffix in (".xlsx", ".xlsm", ".xls"):
        return pd.read_excel(source, **opts)
    if suffix == ".csv":
        return pd.read_csv(source, **opts)
    raise ValueError(f"unsupported export file type: {suffix!r}")


class _ExportAdapter:
    """Shared core: checked frame -> NormalizedRows. Subclasses supply only
    where the bytes come from, which is the one thing that differs."""

    profile: dict
    catalogue: str
    cfg: Ingest

    def _frame(self):
        raise NotImplementedError

    def _checked(self, df, name: str):
        """Fail loudly on schema drift: a renamed/missing BC column would
        otherwise map every row to None and silently skip the whole export (or,
        worse, fan every row into RESOLVE as md_unknown). A hard error
        dead-letters the job with an actionable message instead."""
        missing = [col for key, col in self.profile.items()
                   if key not in _OPTIONAL_PROFILE_KEYS and col not in df.columns]
        if missing:
            raise ValueError(
                f"export {name} missing expected column(s) {missing}; "
                f"got {list(df.columns)}"
            )
        return df

    def read(self) -> Iterator[NormalizedRow]:
        for record in self._frame().to_dict("records"):
            raw = _raw_from_profile(record, self.profile)
            yield normalize_record(raw, self.catalogue, self.cfg)


class CsvExportAdapter(_ExportAdapter):
    """Reads a BC export file (.xlsx / .xlsm / .xls / .csv) from a path."""

    def __init__(self, path: str, catalogue: str, cfg: Ingest, profile: dict | None = None):
        self.path = pathlib.Path(path)
        self.catalogue = catalogue
        self.cfg = cfg
        self.profile = profile or LJ_CSV_PROFILE

    def _frame(self):
        return self._checked(read_frame(self.path, self.path.name), self.path.name)


class UploadExportAdapter(_ExportAdapter):
    """The same BC export, handed to us as bytes through the web /import form
    instead of sitting under /imports.

    Exists because /imports is mounted read-only in both web and worker, so a
    browser upload cannot become a file. Downstream this is indistinguishable
    from CsvExportAdapter -- same reader, same profile, same NormalizedRows
    (invariant 11).
    """

    def __init__(self, content: bytes, filename: str, catalogue: str,
                 cfg: Ingest, profile: dict | None = None):
        self.content = content
        self.filename = filename
        self.catalogue = catalogue
        self.cfg = cfg
        self.profile = profile or LJ_CSV_PROFILE

    def _frame(self):
        return self._checked(
            read_frame(io.BytesIO(self.content), self.filename), self.filename)


class BcApiAdapter:
    """BC OData reader (v2) — a stub. The live OData fetch (httpx against the BC
    endpoint named in `ref`) is not built yet; this exists so the source enum is
    complete and the byte-identity contract can be proven now. `records` injects
    OData-shaped dicts for tests/dev; without them, `read()` raises rather than
    silently yielding an empty catalogue (which would read as "0 items changed").
    """

    def __init__(
        self,
        ref,
        catalogue: str,
        cfg: Ingest,
        records: list[dict] | None = None,
        profile: dict | None = None,
        client=None,
    ):
        self.ref = ref
        self.catalogue = catalogue
        self.cfg = cfg
        self._records = records
        self.profile = profile or LJ_ODATA_PROFILE
        self._client = client

    def _pages(self) -> Iterator[dict]:
        """Every record on the endpoint, following BC's own cursor.

        `@odata.nextLink` and not a computed `$skip`: the server decides its
        page size, and computing our own would drift the day it changes -- into
        skipped or duplicated items, both silent.
        """
        url = self.ref
        while url:
            body = self._client.get(url)
            yield from body.get("value", ())
            url = body.get("@odata.nextLink")

    def read(self) -> Iterator[NormalizedRow]:
        if self._records is None and self._client is None:
            raise NotImplementedError(
                "BcApiAdapter needs either injected `records` or an HTTP client "
                "— or use source='csv'"
            )
        source = self._records if self._records is not None else self._pages()
        seen = 0
        for record in source:
            if not seen:
                _assert_profile_matches(record, self.profile)
            seen += 1
            raw = _raw_from_profile(record, self.profile)
            yield normalize_record(raw, self.catalogue, self.cfg)
        if not seen and self._client is not None:
            # INGEST diffs against `item_mirror`, so an empty read reports
            # "every item unchanged" and mirrors nothing -- the one outcome
            # that must never be produced by a transport problem. An injected
            # empty `records` list is a test's business; an empty ENDPOINT is
            # not.
            raise RuntimeError(
                f"BC returned no records from {self.ref!r}; refusing to report "
                f"an empty catalogue as a successful read"
            )


def make_source_adapter(
    payload: dict,
    cfg: Ingest,
    *,
    imports_dir: str | None = None,
    records: list[dict] | None = None,
    spool: dict | None = None,
    client=None,
):
    """Config-switched adapter factory over the closed `source` enum (PRD §1).

    A relative csv `ref` is resolved against `imports_dir` (the operator picks a
    filename from the import directory in the UI); an absolute path is used as-is.
    `records` forwards injected OData rows to the BC stub (tests/dev). `spool`
    forwards an `import_inbox` row to the upload reader — injected by the handler
    for the same reason `records` is, because this factory has no connection.
    """
    source = payload.get("source")
    catalogue = payload["catalogue"]
    ref = payload["ref"]

    if source == "csv":
        path = pathlib.Path(ref)
        if not path.is_absolute() and imports_dir:
            path = pathlib.Path(imports_dir) / ref
        return CsvExportAdapter(str(path), catalogue, cfg)
    if source == "bc_odata":
        return BcApiAdapter(ref, catalogue, cfg, records=records, client=client)
    if source == "upload":
        if spool is None:
            raise ValueError(
                "source='upload' needs the spool row: the handler reads it from "
                "import_inbox and passes it as `spool=`"
            )
        return UploadExportAdapter(spool["content"], spool["filename"], catalogue, cfg)
    raise ValueError(
        f"unknown ingest source {source!r} (closed enum: csv | bc_odata | upload)")
