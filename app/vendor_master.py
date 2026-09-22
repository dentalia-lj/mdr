"""BC vendor/manufacturer master mirror (S1.8).

`imports/Proizvajalci.xlsx` maps BC's manufacturer code (`Šifra`) to a vendor
name (`Ime`). The LJ item export carries only the code, so without this the
canonical manufacturer of an item is the literal string `001`.

This module owns reading that file and reconciling it against the
`vendor_master` table. It writes nothing else: `manufacturer_alias` stays the
projection that RESOLVE and GATE read, and `playbooks sync` derives it from
this mirror plus authored playbooks.

Why the diff exists rather than a plain upsert: `item_group.
canonical_manufacturer` is effectively write-once, because RESOLVE's ladder
short-circuits on `_existing_link` and never re-derives it for an item already
in a group. So silently re-pointing a code whose old name is already baked into
existing groups produces a split-brain that no later import can repair. A
rename is a decision the operator takes deliberately (`--allow-renames`), not
something an import performs on its way past.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass

CODE_COLUMN = "Šifra"
NAME_COLUMN = "Ime"

DEFAULT_CODE_SOURCE = "LJ"


class RenameRefused(Exception):
    """The import would re-point one or more codes to a different manufacturer."""


@dataclass(frozen=True)
class VendorRow:
    code: str
    name: str | None


@dataclass(frozen=True)
class Diff:
    added: tuple[VendorRow, ...] = ()
    # (code, old_name, new_name)
    renamed: tuple[tuple[str, str | None, str | None], ...] = ()
    disappeared: tuple[str, ...] = ()
    unchanged: int = 0

    def is_clean(self) -> bool:
        return not (self.added or self.renamed or self.disappeared)


def _clean(value) -> str | None:
    """Trim, and treat pandas' NaN / empty string as absent."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    return text


def _rows_from_frame(df, label: str) -> tuple[VendorRow, ...]:
    """Rows out of a parsed export.

    Rows with no code are dropped (they cannot be joined to anything); rows with
    a code but no name are KEPT, because a nameless code is still a real code
    that items point at, and one of the 390 delivered rows is exactly that.
    """
    missing = {CODE_COLUMN, NAME_COLUMN} - set(df.columns)
    if missing:
        raise ValueError(
            f"{label}: missing column(s) {sorted(missing)}; found {list(df.columns)}"
        )

    out = []
    for record in df.to_dict("records"):
        code = _clean(record.get(CODE_COLUMN))
        if code is None:
            continue
        out.append(VendorRow(code=code, name=_clean(record.get(NAME_COLUMN))))
    return tuple(out)


#: `allmanufacturers` (b-s.si, 2026-09-07) mapped to the two fields
#: `VendorRow` carries. **Unconfirmed:** nobody has seen a payload -- access is
#: blocked and no payload has been supplied -- so these are the names BC uses
#: for code and name on every other page, and `_assert_profile_matches` is what
#: turns a wrong guess into a raise instead of 390 nameless codes.
BC_MANUFACTURER_PROFILE = {"code": "no", "name": "name"}


def read_odata(records) -> tuple[VendorRow, ...]:
    """Manufacturer rows from BC's `allmanufacturers` page.

    Returns exactly what `read_file` and `read_bytes` return, so `diff` and
    `apply` -- including the rename refusal and the two-phase preview -- work
    against this source with no change at all.
    """
    from app.adapters.source import _assert_profile_matches

    rows, seen = [], 0
    for record in records:
        if not seen:
            _assert_profile_matches(record, BC_MANUFACTURER_PROFILE)
        seen += 1
        code = _clean(record.get(BC_MANUFACTURER_PROFILE["code"]))
        if code is None:
            continue
        rows.append(VendorRow(
            code=code, name=_clean(record.get(BC_MANUFACTURER_PROFILE["name"]))
        ))
    if not seen:
        # `diff` reports every existing code as `disappeared` against an empty
        # read, and `apply` would act on it. A transport failure must never look
        # like BC deleting its own manufacturer master.
        raise RuntimeError(
            "BC returned no manufacturer records; refusing to report an empty "
            "master as a successful read"
        )
    return tuple(rows)


def read_file(path: pathlib.Path | str) -> tuple[VendorRow, ...]:
    """Parse the BC export from disk -- what `python -m app.cli vendor-master`
    calls.

    Reads through `app.adapters.source.read_frame`, the same function the item
    import uses, so the two cannot drift on suffix handling or on the pandas
    options. `dtype=str` there is load-bearing: without it pandas reads `001` as
    the integer 1 and the code stops matching `item_mirror.manufacturer_raw`. At
    least one real Šifra is non-numeric (`CEFLA`), so the column is text
    throughout.
    """
    from app.adapters.source import read_frame

    path = pathlib.Path(path)
    return _rows_from_frame(read_frame(path, path.name), str(path))


def read_bytes(content: bytes, filename: str) -> tuple[VendorRow, ...]:
    """Parse the same export from bytes spooled in `import_inbox` -- what the
    `vendor.import` handler calls.

    `filename` is what picks the parser, so it must be the name the operator
    uploaded and never a placeholder: a CSV under an `.xlsx` name reaches
    `pd.read_excel` and fails on bytes that were perfectly readable.
    """
    import io

    from app.adapters.source import read_frame

    return _rows_from_frame(read_frame(io.BytesIO(content), filename), filename)


def _existing(conn) -> dict[str, str | None]:
    rows = conn.execute(
        "SELECT code, name FROM vendor_master WHERE code_source=%s",
        (DEFAULT_CODE_SOURCE,),
    ).fetchall()
    return {r["code"]: r["name"] for r in rows}


def brand_index(conn) -> dict[str, frozenset[str]]:
    """Master name -> the BC codes carrying it, for `playbooks.validate`'s
    `BrandCollision` guard.

    Lives here because this module owns the table; `playbooks` never opens a
    connection, so the guard is fed rather than looking anything up. The one
    nameless row is dropped -- it can collide with nothing.
    """
    out: dict[str, set[str]] = {}
    for r in conn.execute(
        "SELECT code, name FROM vendor_master "
        "WHERE code_source=%s AND name IS NOT NULL AND name <> ''",
        (DEFAULT_CODE_SOURCE,),
    ).fetchall():
        out.setdefault(r["name"], set()).add(r["code"])
    return {name: frozenset(codes) for name, codes in out.items()}


def diff(conn, rows) -> Diff:
    """What `apply` would change. Read-only.

    Scoped to `DEFAULT_CODE_SOURCE`, not to a caller's choice: there is one
    Business Central and one article numbering (Denis, 2026-08-19 closing
    PHASES.md G17/G11, restated 2026-08-26). The column stays -- it is half the
    PK and already defaults to this value -- but nothing offers the choice.
    """
    existing = _existing(conn)
    incoming = {r.code: r.name for r in rows}

    added, renamed, unchanged = [], [], 0
    for row in rows:
        if row.code not in existing:
            added.append(row)
        elif existing[row.code] != row.name:
            renamed.append((row.code, existing[row.code], row.name))
        else:
            unchanged += 1

    disappeared = tuple(sorted(c for c in existing if c not in incoming))
    return Diff(
        added=tuple(added),
        renamed=tuple(renamed),
        disappeared=disappeared,
        unchanged=unchanged,
    )


def apply(
    conn,
    rows,
    *,
    batch: str,
    allow_renames: bool = False,
) -> dict:
    """Write the mirror. Returns counts; raises `RenameRefused` (writing
    nothing) when the import would re-point a code and `allow_renames` is not
    set.

    A code that has disappeared from the export keeps its row with a stale
    `import_batch`/`last_seen` rather than being deleted: it is a fact to
    report, and deleting it would strand any alias already derived from it.
    """
    d = diff(conn, rows)
    if d.renamed and not allow_renames:
        detail = "; ".join(f"{c}: {old!r} -> {new!r}" for c, old, new in d.renamed)
        raise RenameRefused(
            f"{len(d.renamed)} code(s) would be re-pointed to a different "
            f"manufacturer: {detail}. canonical_manufacturer is effectively "
            "write-once, so re-run with allow_renames once you have checked "
            "which existing groups this affects."
        )

    for row in rows:
        conn.execute(
            "INSERT INTO vendor_master (code_source, code, name, import_batch) "
            "VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (code_source, code) DO UPDATE SET "
            "  name = EXCLUDED.name, "
            "  import_batch = EXCLUDED.import_batch, "
            "  last_seen = now()",
            (DEFAULT_CODE_SOURCE, row.code, row.name, batch),
        )

    return {
        "added": len(d.added),
        "renamed": len(d.renamed),
        "disappeared": len(d.disappeared),
        "unchanged": d.unchanged,
    }
