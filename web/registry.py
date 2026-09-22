"""Read-only registry views: manufacturers and playbooks (S1.x).

Split out of `web/app.py` (already 1100 lines) rather than appended to it.
Producer-only boundary, unchanged: nothing here writes `document`,
`item_document` or `evidence`, and the `dentalia_api` grants would refuse it.
Every route WAS a GET until three narrow producer-side writes arrived --
`manufacturer.contact_emails` editing and the playbook body (migrations
049/050), and the EUDAMED SRN confirm/reject decision plus the sweep release
(042/044, which also enqueue through `app.queue.enqueue`). None of those is a
table invariant 1 names, and a narrow producer-side write grant is the
established pattern (`job` 007, `upload_inbox` 014, `email_draft` 029), not an
exception to it.

`app.playbooks` is imported deliberately — it is a pure data loader with no DB
or queue access, the same footing on which `web/app.py` imports `app.queue`.
It is NOT `app.handlers`/`app.workers`, which stay out of `web/` structurally.

Filtering and sorting are done in Python, not SQL: the entity set is bounded
by the catalogue's manufacturer count (~390), and playbook presence is not a
column on the row being filtered -- it is the name-or-code union rule
`annotate_playbooks` shares with `playbooks sync`, which no single predicate
expresses. So one code path for both beats half a filter in each. It read
"a filesystem fact" until slice 2 moved the bodies into `manufacturer`; what
keeps it in Python is the rule, not the store.
"""

from __future__ import annotations

import json
import pathlib
import re
from datetime import date

from fastapi import Depends, Form, HTTPException, Query, Request
from psycopg.types.json import Json
from fastapi.responses import HTMLResponse, RedirectResponse

from urllib.parse import quote

from app import compliance
from app import coverage
from app import manufacturer_seed
from app import playbooks as playbooks_mod
from app import queue
from app import regroup
from app.config import load_config
from app.playbooks import DOC_TYPES as DOC_TYPES_FOR_FORM
from app.urls import normalize_url
from app import vendor_master
from app.vendor_master import DEFAULT_CODE_SOURCE

# One row per canonical entity. LEFT JOINs so an entity with no items still
# appears with 0 — post-cold-start that is every entity, and a page that hid
# them would render empty and read as broken.
_MANUFACTURER_ROWS_SQL = """
SELECT a.canonical_name,
       array_agg(DISTINCT a.raw_name) AS codes,
       count(DISTINCT i.item_ref)     AS items,
       count(DISTINCT ip.doc_id)      AS docs
FROM manufacturer_alias a
LEFT JOIN item_mirror i               ON i.manufacturer_raw = a.raw_name
LEFT JOIN item_document_production ip ON ip.item_ref = i.item_ref
GROUP BY a.canonical_name
"""


def manufacturer_rows(conn) -> list[dict]:
    # `sources`, an entity-level array_agg of `manufacturer_alias.source`, was
    # dropped here in P7b (2026-09-15) with the column it fed. The question it
    # answered -- where a spelling came from -- is per CODE, and the per-code
    # answer is on `/manufacturers/{name}` under Technical details, which is
    # strictly finer. An aggregate no reader reads is a query nobody can
    # falsify, so it goes with its column rather than sitting here.
    rows = conn.execute(_MANUFACTURER_ROWS_SQL).fetchall()
    for r in rows:
        r["codes"] = sorted(r["codes"])
    return rows


def load_playbook_index(playbooks_dir: str | None):
    """`(playbooks, error)` — never raises. A malformed directory must render
    as a visible error row, not a 500: `load_playbooks` already swallows
    per-file parse errors by contract (DISCOVER must not dead-letter a job
    over an authoring typo), which means it never tells anyone a file was
    dropped. `error` is this layer's own visibility duty (CLAUDE.md: skipped
    rows are counted and reported, never silent) — it compares the `*.json`
    files actually present in the directory against the slugs that parsed and
    names whatever is missing, so a fat-fingered comma shows up as "this
    playbook is broken", not as "no playbook authored"."""
    try:
        path = pathlib.Path(playbooks_dir) if playbooks_dir else None
        pbs = playbooks_mod.load_playbooks(path)
        # Every playbook that EXISTS, including the ones that did not parse --
        # `load_playbooks` drops those by contract and so cannot report them.
        # The difference between the two sets is exactly the broken set, which
        # is what this layer owes the operator. Replaces a `*.json` glob, so
        # the answer no longer depends on the store being a directory.
        present = playbooks_mod.slugs(path)
    except Exception as exc:  # pragma: no cover - defensive
        return (), str(exc)

    error = None
    broken = sorted(present - {pb.slug for pb in pbs})
    if broken:
        names = ", ".join(f"{slug}.json" for slug in broken)
        error = (
            f"{len(broken)} playbook file(s) failed to parse and are not "
            f"shown: {names}. Check the worker logs for the parse error."
        )
    elif playbooks_mod.reads_files(path):
        # A MISSING directory must not look identical to "no playbooks
        # authored" -- `load_playbooks` returns `()` for both, so without this
        # every entity renders as playbook-less with no warning (finding 3,
        # 2026-08-11 review). Reachable in the shipped configuration:
        # Dockerfile.web copies only `app/` and `web/`, so `/app/playbooks`
        # exists solely because of the compose bind mount. An EMPTY directory
        # is not this case -- it genuinely has nothing authored. Rows have no
        # equivalent: an unreachable database raises and is caught above.
        dir_path = path if path is not None else playbooks_mod.PLAYBOOKS_DIR
        if not dir_path.is_dir():
            error = f"playbooks directory not found: {dir_path}"
    return pbs, error


def annotate_playbooks(rows: list[dict], playbooks) -> list[dict]:
    """Attach `playbook_slug` per entity, entity-level: a playbook claiming any
    BC code of an entity covers the whole entity. Same union rule as
    `playbooks sync`, so the UI and the CLI cannot disagree."""
    by_name = {}
    by_code = {}
    for pb in playbooks:
        for name in pb.names():
            by_name[name.strip().upper()] = pb.slug
        for bc in pb.bc_codes:
            by_code[bc.code] = pb.slug
    for r in rows:
        slug = by_name.get(r["canonical_name"].strip().upper())
        if slug is None:
            for code in r["codes"]:
                if code in by_code:
                    slug = by_code[code]
                    break
        r["playbook_slug"] = slug
    return rows


def filter_and_sort(rows: list[dict], q: str, only_without_playbook: bool) -> list[dict]:
    """Triage order: gaps first, then biggest first, then name. Sorting on
    `items` alone would bury a large uncovered manufacturer under covered ones,
    which is the question this page exists to answer."""
    needle = q.strip().lower()
    if needle:
        rows = [
            r for r in rows
            if needle in r["canonical_name"].lower()
            or any(needle in c.lower() for c in r["codes"])
        ]
    if only_without_playbook:
        rows = [r for r in rows if r["playbook_slug"] is None]
    return sorted(
        rows,
        key=lambda r: (r["playbook_slug"] is not None, -r["items"], r["canonical_name"]),
    )


# vendor_master's key is (code_source, code) (migration 016), so the join must
# scope on code_source or it can fan out one BC code into two rows the moment
# a second code_source is imported with an overlapping code string. Every
# other reader (app/vendor_master.py, app/reconcile.py, app/cli.py) scopes the
# same way; this bind-parameters the same DEFAULT_CODE_SOURCE they default to,
# rather than a second hardcoded 'LJ' literal. manufacturer_alias itself has
# no catalogue/code_source column to join through, so this can't be widened
# to "both catalogues" until Zagreb's vendor-master import exists (gap G11) —
# revisit the same day that import ships.
_DETAIL_CODES_SQL = """
SELECT a.raw_name AS code, a.source, vm.name AS vendor_name
FROM manufacturer_alias a
LEFT JOIN vendor_master vm
       ON vm.code = a.raw_name
      AND vm.code_source = %s
WHERE a.canonical_name = %s
ORDER BY a.raw_name
"""

# Both lists below are capped for page-render sanity — CARL MARTIN alone has
# 2567 items — but the cap must never be silent (CLAUDE.md: skipped rows are
# counted and reported, never silent). A module-level constant, read fresh on
# every call rather than baked into the SQL text, so tests can monkeypatch it
# down to exercise the truncation path without seeding hundreds of rows.
_DETAIL_LIST_CAP = 500

_DETAIL_ITEMS_SQL = """
SELECT i.item_ref, i.name, i.catalogue
FROM item_mirror i
JOIN manufacturer_alias a ON a.raw_name = i.manufacturer_raw
WHERE a.canonical_name = %s
ORDER BY i.item_ref
LIMIT %s
"""

_DETAIL_ITEMS_COUNT_SQL = """
SELECT count(*) AS total
FROM item_mirror i
JOIN manufacturer_alias a ON a.raw_name = i.manufacturer_raw
WHERE a.canonical_name = %s
"""

_DETAIL_DOCS_SQL = """
SELECT DISTINCT ip.doc_id, ip.type, ip.regulation, ip.validity_to
FROM item_document_production ip
JOIN item_mirror i          ON i.item_ref = ip.item_ref
JOIN manufacturer_alias a   ON a.raw_name = i.manufacturer_raw
WHERE a.canonical_name = %s
ORDER BY ip.doc_id
LIMIT %s
"""

_DETAIL_DOCS_COUNT_SQL = """
SELECT count(DISTINCT ip.doc_id) AS total
FROM item_document_production ip
JOIN item_mirror i          ON i.item_ref = ip.item_ref
JOIN manufacturer_alias a   ON a.raw_name = i.manufacturer_raw
WHERE a.canonical_name = %s
"""


# Documents attributed to this manufacturer by the DOCUMENT'S OWN evidence,
# not by an item link. The production list above can only ever reach documents
# that link to something we stock, so a manufacturer's `filed` documents --
# read correctly, attributed correctly, covering nothing in the catalogue --
# were invisible on their own manufacturer's page: 74 of GC's 131 on
# 2026-08-17, reachable only by knowing to type ?status=filed into /documents.
# Matched on the exact spelling (canonical plus every raw_name/alias), never
# fuzzily: this page is navigation, and a wrong attribution here would read as
# a compliance claim.
_DETAIL_ATTRIBUTED_SQL = """
SELECT d.doc_id, d.type, d.regulation, d.status, d.validity_to, d.cert_number
FROM document d
WHERE EXISTS (
  SELECT 1 FROM evidence e
  WHERE e.doc_id = d.doc_id AND e.field = 'manufacturer'
    AND lower(btrim(e.value)) = ANY(%s)
)
ORDER BY array_position(
           ARRAY['staged','production','filed','superseded','rejected']::text[],
           d.status::text),
         d.doc_id DESC
LIMIT %s
"""

_DETAIL_ATTRIBUTED_COUNT_SQL = """
SELECT d.status::text AS status, count(*) AS n
FROM document d
WHERE EXISTS (
  SELECT 1 FROM evidence e
  WHERE e.doc_id = d.doc_id AND e.field = 'manufacturer'
    AND lower(btrim(e.value)) = ANY(%s)
)
GROUP BY 1 ORDER BY 2 DESC
"""


def manufacturer_detail(conn, canonical_name: str) -> dict | None:
    codes = conn.execute(
        _DETAIL_CODES_SQL, (DEFAULT_CODE_SOURCE, canonical_name)
    ).fetchall()
    if not codes:
        return None
    items_total = conn.execute(
        _DETAIL_ITEMS_COUNT_SQL, (canonical_name,)
    ).fetchone()["total"]
    docs_total = conn.execute(
        _DETAIL_DOCS_COUNT_SQL, (canonical_name,)
    ).fetchone()["total"]
    # every spelling this entity is known by, folded for an exact comparison
    # against what a document printed about itself
    spellings = sorted(
        ({canonical_name.strip().lower()}
         | {c["code"].strip().lower() for c in codes if c["code"]}
         | {(c["vendor_name"] or "").strip().lower() for c in codes})
        - {""}
    )
    return {
        "canonical_name": canonical_name,
        "codes": codes,
        "items": conn.execute(
            _DETAIL_ITEMS_SQL, (canonical_name, _DETAIL_LIST_CAP)
        ).fetchall(),
        "items_total": items_total,
        "docs": conn.execute(
            _DETAIL_DOCS_SQL, (canonical_name, _DETAIL_LIST_CAP)
        ).fetchall(),
        "docs_total": docs_total,
        "attributed": conn.execute(
            _DETAIL_ATTRIBUTED_SQL, (spellings, _DETAIL_LIST_CAP)
        ).fetchall(),
        "attributed_by_status": conn.execute(
            _DETAIL_ATTRIBUTED_COUNT_SQL, (spellings,)
        ).fetchall(),
        # `manufacturer` is seeded by `dentalia manufacturers seed` (049). A
        # missing row is normal before the first seed, and means the contact
        # form has nothing to write to -- the template says so rather than
        # rendering an input that silently does nothing.
        **_contacts(conn, canonical_name),
    }


def _contacts(conn, canonical_name: str) -> dict:
    """Contact state for the detail page.

    Contacts live here rather than in the playbooks (ruling 2026-08-20): they
    change without an engineer, the person chasing must be able to fix one in
    the UI, and they are personal data that should not sit in git history
    forever. Until migration 049 and the seed, that ruling had no writer and no
    data -- `discover._contact_known` and `email_request._contacts` both read
    this column and both always returned [].
    """
    row = conn.execute(
        "SELECT id, contact_emails FROM manufacturer WHERE canonical_name = %s",
        (canonical_name,),
    ).fetchone()
    return {
        "manufacturer_id": row["id"] if row else None,
        "contact_emails": list(row["contact_emails"] or []) if row else [],
    }


# --------------------------------------------------------------------------- #
# completeness card
# --------------------------------------------------------------------------- #
# What replaced the folder. A folder shows what is in it; it cannot say what
# should be. `app.compliance` holds the "should be" (and every regulatory
# citation behind it); everything here just finds the best thing we hold for
# each row and hands the pair to it.
#
# One query per page, grouped in Python rather than in SQL. The set is bounded
# by one manufacturer's device count -- CARL MARTIN, the largest, is 2.567 --
# and the ranking below ("article-level beats manufacturer-scope beats staged")
# is a business rule that reads as a rule in Python and as a window function
# nobody can review in SQL.
_HOLDINGS_COLUMNS = """
       d.doc_id, d.type, d.status::text AS doc_status, d.cert_number,
       l.status::text AS link_status, l.match_basis,
       ee.expires, ee.basis AS expiry_basis
"""

# `status <> 'retracted'` on the JOIN, not in a WHERE: as a WHERE clause it
# would turn the LEFT join into an inner one and drop every article that has no
# link at all -- which is most of them, and exactly the ones the card exists to
# show. Retracted links are history (doc 235: 19 retracted, zero live), and
# /items and /documents have excluded them since; the card agrees with them or
# the same document reads as held here and absent two clicks away.
_HOLDINGS_JOINS = """
LEFT JOIN item_document l              ON l.item_ref = i.item_ref
                                      AND l.status <> 'retracted'
LEFT JOIN document d                   ON d.doc_id = l.doc_id
LEFT JOIN document_effective_expiry ee ON ee.doc_id = d.doc_id
"""

# md_flag IS TRUE, not `IS NOT FALSE`: the card answers an MDR question, and an
# item BC has not called a device has no answer to give. 11.693 of 15.958 rows
# are in that state -- rendering a green card over them would teach the reader
# that green means nothing.
_ITEM_HOLDINGS_SQL = f"""
SELECT i.product_class,
{_HOLDINGS_COLUMNS}
FROM item_mirror i
{_HOLDINGS_JOINS}
WHERE i.item_ref = %s AND i.md_flag IS TRUE
"""

_MFR_HOLDINGS_SQL = f"""
SELECT i.item_ref, i.product_class,
{_HOLDINGS_COLUMNS}
FROM item_mirror i
JOIN manufacturer_alias a ON a.raw_name = i.manufacturer_raw
{_HOLDINGS_JOINS}
WHERE a.canonical_name = %s AND i.md_flag IS TRUE
"""

# The supplier-level claim, kept OUT of the scored table on purpose. One ISO
# 13485 certificate bound 2.567 CARL MARTIN articles by `mfr-scope` on
# 2026-08-21 and took device coverage from 39,5% to 99,7% in a day. It is a
# statement about the manufacturer's quality system, not evidence about any
# article, and the page has to be able to say both things at once.
_MFR_SUPPLIER_DOCS_SQL = """
SELECT DISTINCT d.doc_id, d.type, d.validity_to
FROM document d
JOIN item_document l      ON l.doc_id = d.doc_id
JOIN item_mirror i        ON i.item_ref = l.item_ref
JOIN manufacturer_alias a ON a.raw_name = i.manufacturer_raw
WHERE a.canonical_name = %s
  AND d.coverage_scope = 'manufacturer'
  AND d.status = 'production'
ORDER BY d.type, d.doc_id
"""


def _holding_rank(h: dict) -> int:
    """How much a holding is worth, so the best one wins its row.

    Superseded ranks below staged deliberately: a staged document is a decision
    someone still owes, which is actionable, while a superseded one is settled
    history and can never become current again.
    """
    if h["doc_status"] == "superseded":
        return 0
    live = h["doc_status"] == "production" and h["link_status"] == "production"
    if live and h["match_basis"] != "mfr-scope":
        return 3
    if live:
        return 2
    return 1


def _best_by_row(holdings) -> dict[str, dict]:
    """Best holding per card row, keyed by `document.type`."""
    best: dict[str, dict] = {}
    for h in holdings:
        if h["doc_id"] is None:
            continue
        row = h["type"]
        if row not in best or _holding_rank(h) > _holding_rank(best[row]):
            best[row] = h
    return best


def _nb_holding(best: dict[str, dict]) -> dict | None:
    """What satisfies the notified-body row.

    ECJ C-10/24 (4 June 2026): the distributor's duty is that the four-digit
    number is *present* where notified-body involvement is indicated, not that
    the certificate is held or correct. So a declaration printing a certificate
    number satisfies it -- and so, more strongly, does holding the certificate
    itself.
    """
    doc = best.get(compliance.ROW_DOC)
    if doc is not None and doc["cert_number"]:
        return doc
    return best.get(compliance.ROW_EC)


def _card_rows(product_class, holdings, *, today) -> list[dict]:
    """One row per card row, for a single item."""
    req = compliance.requirements_for_class(product_class)
    best = _best_by_row(holdings)
    rows = []
    for row in compliance.SCORED_ROWS + compliance.EVIDENCE_ROWS:
        held = _nb_holding(best) if row == compliance.ROW_NB else best.get(row)
        cell = compliance.cell_state(req[row], held, today=today)
        rows.append({
            "row": row,
            "label": compliance.ROW_LABELS[row],
            "requirement": req[row],
            "scored": row in compliance.SCORED_ROWS,
            "doc_id": held["doc_id"] if held else None,
            "expires": held["expires"] if held else None,
            "expiry_basis": held["expiry_basis"] if held else None,
            "match_basis": held["match_basis"] if held else None,
            **cell,
        })
    return rows


def item_completeness(conn, item_ref: str, *, today=None) -> dict | None:
    """The card for one article, or None when the card does not apply.

    None covers both "no such item" and "not a medical device" -- the caller
    renders nothing in either case, and neither deserves a distinct message on
    a page that is already about something else.
    """
    today = today or date.today()
    holdings = conn.execute(_ITEM_HOLDINGS_SQL, (item_ref,)).fetchall()
    if not holdings:
        return None
    product_class = holdings[0]["product_class"]
    rows = _card_rows(product_class, holdings, today=today)
    return {
        "item_ref": item_ref,
        "product_class": product_class,
        "rows": rows,
        "severity": compliance.worst_severity(
            [r["severity"] for r in rows if r["scored"]]
        ),
    }


def manufacturer_completeness(conn, canonical_name: str, *, today=None) -> dict:
    """The same card, tallied across every device this manufacturer supplies.

    Per row: how many articles are green, amber and red. Plus the two figures
    the client actually asks for -- how many articles carry no declaration at
    all, and what the supplier-level certificates are that make the coverage
    number look finished.
    """
    today = today or date.today()
    by_item: dict[str, list[dict]] = {}
    classes: dict[str, str | None] = {}
    for h in conn.execute(_MFR_HOLDINGS_SQL, (canonical_name,)).fetchall():
        by_item.setdefault(h["item_ref"], []).append(h)
        classes[h["item_ref"]] = h["product_class"]

    tally = {
        row: dict(
            row=row,
            label=compliance.ROW_LABELS[row],
            scored=row in compliance.SCORED_ROWS,
            **{b: 0 for b in compliance.BUCKETS},
        )
        for row in compliance.SCORED_ROWS + compliance.EVIDENCE_ROWS
    }
    # Two different problems with two different answers. Counting them as one
    # told IVOCLAR that 1.050 of its 1.069 articles had no declaration, when
    # the true figure was 16 -- the other 1.034 hold one that is out of date,
    # superseded or still awaiting review.
    no_declaration = 0
    stale_declaration = 0
    for item_ref, holdings in by_item.items():
        for r in _card_rows(classes[item_ref], holdings, today=today):
            tally[r["row"]][compliance.bucket_for(r["state"], r["requirement"])] += 1
            if r["row"] == compliance.ROW_DOC and r["severity"] != "ok":
                if r["state"] == "missing":
                    no_declaration += 1
                else:
                    stale_declaration += 1

    rows = []
    for row in compliance.SCORED_ROWS + compliance.EVIDENCE_ROWS:
        t = tally[row]
        # An evidence row never colours the card, however its buckets fall.
        t["severity"] = compliance.worst_severity(
            [s for s, n in (("bad", t["lapsed"] + t["none"]),
                            ("warn", t["attention"]),
                            ("ok", t["held"])) if n]
        ) if t["scored"] else "none"
        rows.append(t)

    # NOT "items": Jinja resolves `card.items` by getattr before getitem, so a
    # key by that name renders `dict.items` -- the bound method -- into the
    # page. Caught by the render test, invisible to every unit test above.
    return {
        "canonical_name": canonical_name,
        "md_items": len(by_item),
        "rows": rows,
        "no_declaration_items": no_declaration,
        "stale_declaration_items": stale_declaration,
        "supplier_docs": conn.execute(
            _MFR_SUPPLIER_DOCS_SQL, (canonical_name,)
        ).fetchall(),
        "severity": compliance.worst_severity(
            [r["severity"] for r in rows if r["scored"]]
        ),
    }


def playbook_rows(playbooks_dir: str | None) -> tuple[list[dict], str | None]:
    pbs, error = load_playbook_index(playbooks_dir)
    rows = [
        {
            "slug": pb.slug,
            "manufacturer": pb.manufacturer,
            "aliases": list(pb.aliases),
            "codes": [bc.code for bc in pb.bc_codes],
            "domains": list(pb.domains),
            "source_count": len(pb.doc_sources),
        }
        for pb in pbs
    ]
    return sorted(rows, key=lambda r: r["slug"]), error


def playbook_drift(conn, playbooks_dir: str | None) -> dict | None:
    """What the repo's `playbooks/*.json` and the database disagree about, or
    `None` when there is nothing to compare.

    THE STORE IS THE DATABASE (migration 050). The files are the authoring
    record, and nothing keeps them honest: a UI save bumps `playbook_rev` and
    leaves the file at whatever it said, so the next `manufacturers seed`
    refuses and the next reader of the repo sees a body that has not been true
    for weeks. `dentalia playbooks drift` says so on demand; this is the same
    answer without having to ask.

    Same classifier as the CLI (`manufacturer_seed.drift`), deliberately not a
    second implementation -- the whole value of the banner is that it agrees
    with the command it tells you to run.

    `None`, not an empty result, when the directory is absent. That is the
    DEPLOYED configuration, not a fault: `Dockerfile.web` copies only `app/`
    and `web/`, so there are no files to be stale, and `drift()` would report
    every row as `db-only` -- an alarm about nothing. Compose bind-mounts the
    directory read-only for development, where the authoring actually happens.

    An EXPLICIT `dir_path` on every path below. `load_raw(None)` returns ROWS
    once `set_source` is set, which is this process's own configuration -- the
    default would compare the database against itself and always report
    everything in sync (the bug this cost a session on 2026-08-31).
    """
    dir_path = pathlib.Path(playbooks_dir) if playbooks_dir else playbooks_mod.PLAYBOOKS_DIR
    if not dir_path.is_dir():
        return None

    # Defensive, same posture as `load_playbook_index`: this is a secondary
    # panel on a page that has to render. A comparison that fails must show as
    # a message, never as a 500 that hides the playbook list behind it.
    try:
        rows = manufacturer_seed.drift(conn, dir_path=dir_path)
    except Exception as exc:  # pragma: no cover - defensive
        return {"error": str(exc), "clean": False, "stale": [], "diverged": [],
                "db_only": [], "not_imported": [], "in_sync": 0, "total": 0}
    out = {
        "error": None,
        "stale": [
            {"slug": slug, "rev": d["rev"], "matches_rev": d["matches_rev"],
             "delta": d["delta"]}
            for slug, state, d in rows if state == manufacturer_seed.FILE_STALE
        ],
        "diverged": [
            {"slug": slug, "rev": d["rev"], "delta": d["delta"]}
            for slug, state, d in rows if state == manufacturer_seed.DIVERGED
        ],
        # "no readable file", not "file deleted": `load_raw` skips a malformed
        # file rather than raising, so a JSON typo lands here too. The phrasing
        # has to be true of both.
        "db_only": [slug for slug, state, _ in rows
                    if state == manufacturer_seed.DB_ONLY],
        "not_imported": [slug for slug, state, _ in rows
                         if state == manufacturer_seed.NOT_IMPORTED],
        "in_sync": sum(1 for _, state, _ in rows
                       if state == manufacturer_seed.IN_SYNC),
        "total": len(rows),
    }
    out["clean"] = not (out["stale"] or out["diverged"]
                        or out["db_only"] or out["not_imported"])
    # `diverged` is the only state a human MUST resolve -- both sides were
    # edited, so no mechanical repair is safe. Everything else has a command.
    out["severity"] = ("bad" if out["diverged"]
                       else "ok" if out["clean"] else "warn")
    return out


def playbook_detail(playbooks_dir: str | None, slug: str) -> dict | None:
    pbs, error = load_playbook_index(playbooks_dir)
    pb = next((p for p in pbs if p.slug == slug), None)
    if pb is None:
        return None
    # Rendered from the store this page already read, not re-read off disk.
    # The old path opened `{slug}.json` a second time, which meant the panel
    # could show something the loader above had not parsed.
    try:
        raw = json.dumps(playbooks_mod.load_raw(
            pathlib.Path(playbooks_dir) if playbooks_dir else None)[slug], indent=2)
    except Exception as exc:
        raw = f"(could not read the playbook body: {exc})"
    return {
        "slug": pb.slug,
        "manufacturer": pb.manufacturer,
        "aliases": list(pb.aliases),
        "codes": [bc.code for bc in pb.bc_codes],
        "domains": list(pb.domains),
        "contacts": list(pb.contacts),
        "doc_sources": [
            {"doc_type": s.doc_type, "kind": s.kind, "url": s.url, "note": s.note}
            for s in pb.doc_sources
        ],
        "raw": raw,
        "playbook_error": error,
    }


class SaveRefused(Exception):
    """A save the operator must see and fix, rendered at 422 rather than 500.

    Carries the message verbatim: `playbooks.validate` and `RobotsRefused`
    already phrase these for a human, and rewording them here would give the
    UI and the CLI two vocabularies for one refusal.
    """


class StaleForm(SaveRefused):
    """Someone else saved while this form was open."""


def _proposed_set(pbs, slug: str, proposed):
    """Every playbook, with `slug` replaced by `proposed`.

    `validate` is a whole-set check -- duplicate BC code, duplicate name,
    refused fetch host -- so validating the edited playbook alone would pass
    exactly the authoring mistakes the check exists to catch.
    """
    return tuple(proposed if p.slug == slug else p for p in pbs)


def save_playbook_body(conn, *, slug: str, body: dict, note: str, user: str,
                       expected_rev: int, playbooks_dir=None) -> int:
    """Write a new body and append the revision that records it. Returns the
    new `playbook_rev`. Caller owns the transaction.

    Four refusals, in this order, because each is cheaper than the next and a
    later one must not run against a set an earlier one has already condemned:

    1. `note` is required (Denis, 2026-08-27). A revision list of timestamps
       and usernames does not make a revert obvious months later, which is the
       job the audit trail was ruled in to do.
    2. The body must PARSE. `_parse` carries every rule this subsystem has --
       the regex compiles, `companion.key`'s single capture group, the
       doc_source vocabulary, the `extract_hints` key allowlist -- so a save
       that fails it is an authoring error caught at 422 rather than a
       never-raises skip that silently drops the playbook from the next job.
    3. `playbooks.validate()` on the PROPOSED SET, not the one playbook. This
       is the trap the spec names: a UI save must be refused the way
       `playbooks sync` refuses a file-level conflict, not accepted and
       discovered later by RESOLVE mapping items to the wrong manufacturer.
       The vendor-master brand map goes in with it, so the `BrandCollision`
       guard is live here too -- the UI and the CLI must not be able to
       disagree about what an authoring error is.
    4. The optimistic lock. `WHERE playbook_rev = %s` and a `None` return means
       someone saved while this form was open -- their edit stays, and this one
       is reported rather than silently overwriting it.

    The revision is INSERTed before the UPDATE so that a body can never be live
    without the row that explains it; both are in the caller's transaction, so
    a failure at either point leaves neither.
    """
    if not note or not note.strip():
        raise SaveRefused("a note is required: say what you changed and why")

    row = conn.execute(
        "SELECT id, canonical_name, playbook_rev FROM manufacturer WHERE slug=%s",
        (slug,)).fetchone()
    if row is None:
        raise SaveRefused(f"no playbook {slug!r}")

    codes = [r["code"] for r in conn.execute(
        "SELECT code FROM manufacturer_bc_code WHERE manufacturer_id=%s "
        "AND source='playbook' ORDER BY code", (row["id"],)).fetchall()]
    aliases = [r["name"] for r in conn.execute(
        "SELECT name FROM manufacturer_name WHERE manufacturer_id=%s "
        "AND kind='alias' ORDER BY name_folded", (row["id"],)).fetchall()]

    # Identity is NOT editable here -- it lives in the constrained tables and
    # is Tier B/D. Re-attached so `_parse` sees the same shape it would from a
    # file, and so `validate` can see the identity this body ships with.
    raw = dict(body)
    raw["manufacturer"] = row["canonical_name"]
    raw["bc_codes"] = [{"code": c} for c in codes]
    raw["aliases"] = aliases

    try:
        proposed = playbooks_mod._parse(slug, raw)
    except Exception as exc:
        raise SaveRefused(str(exc)) from exc

    current = playbooks_mod.load_playbooks(playbooks_dir)
    try:
        playbooks_mod.validate(_proposed_set(current, slug, proposed),
                               brands=vendor_master.brand_index(conn))
    except playbooks_mod.PlaybookConflict as exc:
        raise SaveRefused(str(exc)) from exc

    # The lock is `expected_rev` -- the revision the FORM was rendered at --
    # never the one just read here. Locking on the fresh read would compare a
    # value to itself: the UPDATE would always match and the parameter would
    # be an optimistic lock in name only, which is how two people editing one
    # playbook silently lose the first edit.
    if row["playbook_rev"] != expected_rev:
        raise StaleForm(
            f"{slug} changed while this form was open (it is at revision "
            f"{row['playbook_rev']}, this form was opened at {expected_rev}). "
            f"Reload and re-apply your edit.")

    new_rev = expected_rev + 1
    encoded = Json(playbooks_mod.body_of(raw))
    conn.execute(
        "INSERT INTO manufacturer_playbook_revision "
        "  (manufacturer_id, rev, body, authored_by, note) VALUES (%s,%s,%s,%s,%s)",
        (row["id"], new_rev, encoded, user, note.strip()))

    # Belt and braces: the check above is a read-then-write, so a concurrent
    # save between the two would slip past it. This is the one that cannot.
    saved = conn.execute(
        "UPDATE manufacturer SET body=%s, playbook_rev=%s, updated_by=%s, "
        "  updated_at=now() WHERE id=%s AND playbook_rev=%s RETURNING id",
        (encoded, new_rev, user, row["id"], expected_rev)).fetchone()
    if saved is None:
        raise StaleForm(
            f"{slug} changed while this form was open. Reload and re-apply "
            f"your edit.")
    return new_rev


def rename_manufacturer(conn, *, canonical_name: str, new_name: str, note: str,
                        user: str, confirm: bool) -> dict:
    """Rename one manufacturer's canonical name. Caller owns the transaction.

    D1, and the reason it is a confirmed ACTION rather than a form field: the
    canonical name is effectively write-once downstream. `resolve.py`'s ladder
    is `_existing_link(...) or _by_udi(...) or ...` and short-circuits, so an
    item already in a group never re-derives its manufacturer. Groups built
    under the old name keep it, and nothing here can fix that -- see below.

    WHAT THIS WRITES, and it is deliberately less than the plan assumed:
    `manufacturer.canonical_name` and the `manufacturer_name` rows. That is
    the whole of what the web may write. `dentalia_api` is SELECT-only on
    `item_group`, `item_group_member` and `manufacturer_alias` (invariant 1 --
    the UI is a job producer, not a registry writer), and the re-resolve means
    DELETEing groups, which `regroup.apply` does from a worker. There is no
    job type for it and inventing one is a PRD change, so this returns the
    work it could not do instead of pretending it did it. `RenameImpact.command`
    is the exact CLI line.

    The old name is DEMOTED to an alias, never deleted. Documents already print
    it -- that is what a legal name on a certificate is -- and dropping the row
    would make every one of them stop resolving. It also lands back in the
    playbook's `aliases` on the next load (`_row_to_raw` reads `kind='alias'`),
    which is where it belongs.

    Refusals, cheapest first, and `confirm` is checked LAST on purpose: an
    operator who ticks the box should still be told about a collision rather
    than having the tick treated as an answer to a question they were not
    asked.
    """
    new_name = (new_name or "").strip()
    if not new_name:
        raise SaveRefused("a new name is required")

    row = conn.execute(
        "SELECT id, canonical_name, slug FROM manufacturer WHERE canonical_name=%s",
        (canonical_name,)).fetchone()
    if row is None:
        raise SaveRefused(
            f"no manufacturer row for {canonical_name!r} — run "
            "`dentalia manufacturers seed` first")
    if new_name == row["canonical_name"]:
        raise SaveRefused(f"{new_name!r} is already the name")

    if not note or not note.strip():
        raise SaveRefused("a note is required: say why the name is changing")

    owner = conn.execute(
        "SELECT manufacturer_id, name FROM manufacturer_name WHERE name_folded=%s",
        (new_name.casefold(),)).fetchone()
    if owner is not None and owner["manufacturer_id"] != row["id"]:
        other = conn.execute(
            "SELECT canonical_name FROM manufacturer WHERE id=%s",
            (owner["manufacturer_id"],)).fetchone()
        raise SaveRefused(
            f"{new_name!r} already belongs to {other['canonical_name']!r} "
            f"(as {owner['name']!r}). One name, one manufacturer.")

    # D2 applies to a rename with a bigger blast radius than to an alias: this
    # name becomes what every future fetch files under. Both the new name and
    # the demoted old one are checked -- the old one is about to become an
    # alias, and an alias is exactly what the guard exists for.
    claimed = frozenset(r["code"] for r in conn.execute(
        "SELECT code FROM manufacturer_bc_code WHERE manufacturer_id=%s",
        (row["id"],)).fetchall())
    brands = vendor_master.brand_index(conn)
    for candidate in (new_name, row["canonical_name"]):
        hit = playbooks_mod.brand_collision(candidate, brands, claimed=claimed)
        if hit:
            master, codes = hit
            raise SaveRefused(
                f"{candidate!r} contains the BC brand {master!r} (code(s) "
                f"{', '.join(codes)}), which this manufacturer does not claim. "
                f"Documents naming it would resolve here and never reach "
                f"{master}'s own items. Claim the code, or choose another name.")

    impact = regroup.rename_impact(conn, canonical_name)
    if not confirm:
        return {"applied": False, "impact": impact, "new_name": new_name}

    conn.execute(
        "UPDATE manufacturer SET canonical_name=%s, updated_by=%s, updated_at=now() "
        "WHERE id=%s",
        (new_name, user, row["id"]))
    # Demote first: the new row may be the SAME row (a case-only change folds
    # to one `name_folded`), and inserting before demoting would collide with
    # itself on the primary key.
    conn.execute(
        "UPDATE manufacturer_name SET kind='alias' "
        "WHERE manufacturer_id=%s AND kind='canonical'",
        (row["id"],))
    conn.execute(
        "INSERT INTO manufacturer_name (name_folded, name, manufacturer_id, kind) "
        "VALUES (%s,%s,%s,'canonical') "
        "ON CONFLICT (name_folded) DO UPDATE SET name=EXCLUDED.name, "
        "  kind='canonical' WHERE manufacturer_name.manufacturer_id=EXCLUDED.manufacturer_id",
        (new_name.casefold(), new_name, row["id"]))
    return {"applied": True, "impact": impact, "new_name": new_name,
            "old_name": row["canonical_name"], "slug": row["slug"], "note": note.strip()}


#: A slug is a URL path segment and was a filename for the 33 delivered
#: playbooks (`dentsply-sirona`, `kuraray-dental`, `3shape`). Kept to that
#: shape so the two eras are one namespace and a slug can still be read aloud.
_SLUG_OK = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def slug_for(name: str) -> str:
    """A suggested slug from a canonical name. Suggested, not imposed -- the
    delivered ones are shorter than their manufacturer names on purpose
    (`ivoclar`, not `ivoclar-vivadent-ag`), so the form offers this and lets a
    human shorten it."""
    out = re.sub(r"[^a-z0-9]+", "-", (name or "").casefold()).strip("-")
    return out


def start_playbook(conn, *, canonical_name: str, slug: str, user: str) -> dict:
    """Give a manufacturer that has none its first playbook. Caller owns the tx.

    The half the onboarding worklist was missing. `/manufacturers?no_playbook=1`
    has always listed who needs one -- gaps first, biggest first -- and until
    now clicking through offered nothing to author: `manufacturer.slug` was
    written by `manufacturers seed` and nowhere else, so starting a playbook
    meant hand-writing `playbooks/{slug}.json` in the repo and running an
    import. That is the engineering bottleneck this whole move exists to
    remove, and removing it is what makes the worklist a worklist.

    It creates an EMPTY body on purpose. Every Tier A and Tier B control
    already built then applies, each with its own guard, rather than this
    route growing a second authoring surface that would drift from them.

    Identity comes from the row that already exists -- the canonical name, its
    BC codes and its vendor-master aliases were derived by the seed from
    `vendor_master`. Nothing here invents an entity; it attaches a playbook to
    one.
    """
    slug = (slug or "").strip().casefold()
    if not slug:
        raise SaveRefused("a slug is required")
    if not _SLUG_OK.match(slug):
        raise SaveRefused(
            f"{slug!r} is not a usable slug: lower-case letters, digits and "
            f"single hyphens only (it is a URL path segment)")

    row = conn.execute(
        "SELECT id, slug FROM manufacturer WHERE canonical_name=%s",
        (canonical_name,)).fetchone()
    if row is None:
        raise SaveRefused(
            f"no manufacturer row for {canonical_name!r} — run "
            "`dentalia manufacturers seed` first")
    if row["slug"]:
        raise SaveRefused(
            f"{canonical_name!r} already has playbook {row['slug']!r}")

    taken = conn.execute(
        "SELECT canonical_name FROM manufacturer WHERE slug=%s", (slug,)).fetchone()
    if taken:
        raise SaveRefused(
            f"slug {slug!r} is already {taken['canonical_name']!r}'s. Pick "
            f"another — a slug is one playbook's name for itself.")

    conn.execute(
        "UPDATE manufacturer SET slug=%s, body='{}'::jsonb, playbook_rev=0, "
        "  updated_by=%s, updated_at=now() WHERE id=%s",
        (slug, user, row["id"]))
    # Revision 0, same as the seed writes for a file's provenance: an
    # `extraction_attempt` carrying (slug, 0) must resolve to something, and
    # "started empty, by a person, on this date" is what it resolves to here.
    conn.execute(
        "INSERT INTO manufacturer_playbook_revision "
        "  (manufacturer_id, rev, body, authored_by, note) "
        "VALUES (%s,0,'{}'::jsonb,%s,%s) "
        "ON CONFLICT (manufacturer_id, rev) DO NOTHING",
        (row["id"], user, f"started in the UI for {canonical_name}"))
    return {"slug": slug, "manufacturer": canonical_name}


def claimable_codes(conn, slug: str) -> list[dict]:
    """BC codes this playbook could claim, with what claiming one would move.

    The picker's whole point is that a code is never free text: `bc_codes` is
    foreign-keyed to `vendor_master`, so an invented code is already
    impossible, and offering a list makes it obvious rather than merely
    refused. Codes another PLAYBOOK claims are excluded -- one code, one
    playbook, and `validate` refuses the pair anyway -- so what is left is the
    unclaimed brands, which is exactly the set a `BrandCollision` refusal
    points at.

    That includes a code THIS playbook's own manufacturer holds only from the
    vendor master. `validate` counts a code as claimed only when its source is
    `playbook`, so such a code is exactly what a BrandCollision names, and
    until 2026-09-11 an `owner.slug <> this slug` filter hid it from the one
    list that could fix it (futura-dental and 10044, which blocked every
    playbook save in the editor).
    """
    return [dict(r) for r in conn.execute(
        """
        SELECT vm.code, vm.name,
               (SELECT count(*) FROM item_mirror i
                 WHERE i.manufacturer_raw = vm.code) AS items,
               mbc.source AS claimed_as,
               owner.canonical_name AS owner, owner.slug AS owner_slug
          FROM vendor_master vm
          LEFT JOIN manufacturer_bc_code mbc
                 ON mbc.code_source = vm.code_source AND mbc.code = vm.code
          LEFT JOIN manufacturer owner ON owner.id = mbc.manufacturer_id
         WHERE vm.code_source = %s
           AND vm.name IS NOT NULL
           AND coalesce(mbc.source, 'vendor-master') <> 'playbook'
         ORDER BY items DESC, vm.code
        """, (DEFAULT_CODE_SOURCE,)).fetchall()]


def claim_bc_code(conn, *, slug: str, code: str, user: str, confirm: bool) -> dict:
    """Attach one more BC code to this playbook's manufacturer.

    ADD only, and that is a grant, not a preference: migration 049 gives
    `dentalia_api` SELECT/INSERT/UPDATE on `manufacturer_bc_code` and no
    DELETE. Un-claiming a code means removing the row, so it stays CLI work --
    which is the right way round, because adding a claim is what a
    `BrandCollision` refusal asks for ("either add 012 to bc_codes ... or drop
    the name") and removing one strands whatever was resolved under it.

    A code another playbook claims is refused: one code, one playbook, and
    `playbooks.validate` refuses that pair on the next load anyway. Better to
    say so here, naming the other playbook, than to write a row that makes
    every subsequent save fail.

    Two-step (Task 0 contract), and `confirm` is checked LAST, same reasoning
    as `rename_manufacturer`: every refusal above must still fire on the
    un-confirmed press, so an operator sees a collision before deciding
    rather than after ticking the box.
    """
    mfr = conn.execute(
        "SELECT id, canonical_name FROM manufacturer WHERE slug=%s",
        (slug,)).fetchone()
    if mfr is None:
        raise SaveRefused(
            f"no manufacturer row for playbook {slug!r} — run "
            "`dentalia manufacturers seed` first")

    vendor = conn.execute(
        "SELECT name FROM vendor_master WHERE code_source=%s AND code=%s",
        (DEFAULT_CODE_SOURCE, code)).fetchone()
    if vendor is None:
        raise SaveRefused(
            f"BC code {code!r} is not in the vendor master. Import it first — "
            "a code BC does not issue can never reach a catalogue item.")

    existing = conn.execute(
        "SELECT mbc.manufacturer_id, mbc.source, m.canonical_name, m.slug "
        "FROM manufacturer_bc_code mbc JOIN manufacturer m "
        "  ON m.id = mbc.manufacturer_id "
        "WHERE mbc.code_source=%s AND mbc.code=%s",
        (DEFAULT_CODE_SOURCE, code)).fetchone()
    own = existing is not None and existing["manufacturer_id"] == mfr["id"]
    if own and existing["source"] == "playbook":
        raise SaveRefused(f"{slug} already claims BC code {code}")
    # `own` with source `vendor-master` falls through to the UPDATE below, which
    # promotes it to `playbook`: the manufacturer holds the code, the playbook
    # does not claim it, and `validate` only believes the second.
    if existing and existing["source"] == "playbook":
        raise SaveRefused(
            f"BC code {code} is already claimed by playbook "
            f"{existing['slug']!r} ({existing['canonical_name']}). One code, "
            f"one playbook.")

    items = conn.execute(
        "SELECT count(*) AS n FROM item_mirror WHERE manufacturer_raw=%s",
        (code,)).fetchone()["n"]

    if not confirm:
        return {"code": code, "vendor_name": vendor["name"], "items": items,
                "manufacturer": mfr["canonical_name"],
                "current_owner": existing["canonical_name"] if existing and not own else None,
                "applied": False}

    if existing:
        conn.execute(
            "UPDATE manufacturer_bc_code SET manufacturer_id=%s, source='playbook' "
            "WHERE code_source=%s AND code=%s",
            (mfr["id"], DEFAULT_CODE_SOURCE, code))
    else:
        conn.execute(
            "INSERT INTO manufacturer_bc_code (code_source, code, manufacturer_id, "
            "  source) VALUES (%s,%s,%s,'playbook')",
            (DEFAULT_CODE_SOURCE, code, mfr["id"]))
    conn.execute(
        "UPDATE manufacturer SET updated_by=%s, updated_at=now() WHERE id=%s",
        (user, mfr["id"]))
    return {"code": code, "vendor_name": vendor["name"], "items": items,
            "manufacturer": mfr["canonical_name"],
            "moved_from": existing["canonical_name"] if existing and not own else None,
            "applied": True}


def revisions(conn, slug: str) -> list[dict]:
    """Newest first. The `body` is carried so a revert needs no second read."""
    return [dict(r) for r in conn.execute(
        "SELECT r.rev, r.body, r.authored_at, r.authored_by, r.note "
        "FROM manufacturer_playbook_revision r "
        "JOIN manufacturer m ON m.id = r.manufacturer_id "
        "WHERE m.slug = %s ORDER BY r.rev DESC", (slug,)).fetchall()]


def revert_playbook(conn, *, slug: str, to_rev: int, user: str,
                    expected_rev: int, playbooks_dir=None) -> int:
    """Restore an old body by writing it FORWARD as a new revision.

    Never a delete and never an UPDATE of history: `dentalia_api` is granted
    neither on `manufacturer_playbook_revision` (migration 050), so this is
    the only shape a revert can take -- which is the point. The mistake and
    its undo both stay readable.
    """
    old = conn.execute(
        "SELECT r.body FROM manufacturer_playbook_revision r "
        "JOIN manufacturer m ON m.id = r.manufacturer_id "
        "WHERE m.slug=%s AND r.rev=%s", (slug, to_rev)).fetchone()
    if old is None:
        raise SaveRefused(f"{slug} has no revision {to_rev}")
    return save_playbook_body(
        conn, slug=slug, body=old["body"], user=user, expected_rev=expected_rev,
        note=f"revert to revision {to_rev}", playbooks_dir=playbooks_dir)


#: Tier A -- the fields a non-dev may edit (spec §4.1). Everything else on a
#: playbook renders read-only in this slice. The line is drawn on BLAST RADIUS,
#: not on how hard the field is to type: the worst a wrong value here can do is
#: cost a search rung a miss or read one extra date, and every one of them is
#: reversible from the history card. `bc_codes`, `doc_sources` kind="direct",
#: `skip_backfill` and `extract_hints` are Tier B and need their own guards;
#: the regex and parse-template keys are Tier C and engineer-only.
TIER_A_FIELDS = ("domains", "doc_sources", "date_labels", "type_markers",
                 "source_priority")


def _split_lines(raw: str) -> list[str]:
    """One value per line, blanks dropped. Commas are NOT separators: a
    `site:` path scope and a doc-type marker can both legitimately contain
    one."""
    return [ln.strip() for ln in (raw or "").splitlines() if ln.strip()]


def site_query_preview(domains) -> str:
    """The `site:` restriction `discover._query_for` would build.

    The one affordance that lets a non-dev reason about `domains`, whose effect
    is otherwise invisible: the field changes which corner of the web the
    search rung looks at, and nothing on the page said so. Built by the same
    join `_query_for` uses -- if that ever changes shape, this must follow, and
    a test asserts they agree.
    """
    if not domains:
        return "(no domains authored — the search rung runs unrestricted)"
    return "(" + " OR ".join(f"site:{d}" for d in domains) + ")"


from app import contacts as contacts_mod

def tier_a_from_form(body: dict, form) -> dict:
    """Fold Tier A form fields into an EXISTING body, leaving everything else
    exactly as it was.

    Additive by construction: a body carries Tier B and C keys this form never
    renders, and rebuilding it from the form alone would silently delete them.
    That is the failure mode a "just serialise the form" save has, and it would
    look like a successful edit.
    """
    out = dict(body)

    domains = _split_lines(form.get("domains", ""))
    if domains:
        out["domains"] = domains
    else:
        out.pop("domains", None)

    # `contacts` (2026-09-03). Tier A on blast radius: the worst a wrong entry
    # does is send a renewal request to the wrong mailbox, which a person
    # notices and the history card reverts. Validated by the SAME function the
    # playbook loader uses -- a rule enforced at only one door is a rule the
    # other door can violate, and the file already refuses a personal address.
    if "contacts" in form:
        contacts = contacts_mod.validate_for_playbook(
            _split_lines(form.get("contacts", "")))
        if contacts:
            out["contacts"] = contacts
        else:
            out.pop("contacts", None)

    priority = [r for r in form.getlist("source_priority")] \
        if hasattr(form, "getlist") else list(form.get("source_priority") or [])
    if priority:
        out["source_priority"] = priority
    else:
        out.pop("source_priority", None)

    # `doc_sources` is a list of objects and only the kind="portal" entries are
    # Tier A. The kind="direct" ones are FETCH TARGETS (Tier B) and are carried
    # through untouched -- rebuilding the list from the portal rows alone would
    # silently delete them, which for NSK would drop four verified certificate
    # URLs and read on screen as a successful edit.
    kept_direct = [src for src in (body.get("doc_sources") or [])
                   if src.get("kind") != "portal"]
    portals = []
    for i in range(int(form.get("doc_source_count") or 0)):
        url = (form.get(f"doc_source.{i}.url") or "").strip()
        if not url:
            continue                       # a cleared row is a deletion
        entry = {
            "doc_type": (form.get(f"doc_source.{i}.doc_type") or "").strip(),
            "kind": "portal",
            "url": url,
        }
        note = (form.get(f"doc_source.{i}.note") or "").strip()
        if note:
            entry["note"] = note
        portals.append(entry)
    combined = portals + kept_direct
    if combined:
        out["doc_sources"] = combined
    else:
        out.pop("doc_sources", None)

    for key in ("date_labels", "type_markers"):
        collected = {}
        for sub in _LABEL_KEYS[key]:
            values = _split_lines(form.get(f"{key}.{sub}", ""))
            if values:
                collected[sub] = values
        if collected:
            out[key] = collected
        else:
            out.pop(key, None)

    return out


#: The keys each additive lexicon may carry. Fixed, not free text: a
#: `type_markers` entry filed under an unknown name would type a document as
#: something the registry has no member for, at T0 confidence, silently --
#: which is why `_parse` restricts them and why the form offers exactly these.
_LABEL_KEYS = {
    "date_labels": ("from", "to"),
    "type_markers": DOC_TYPES_FOR_FORM,
}

#: Tier B -- editable, but each one does something a Tier A field cannot, so
#: the form shows the consequence next to the control (spec §4.2).
TIER_B_FIELDS = ("doc_sources.direct", "skip_backfill", "extract_hints", "bc_codes")


def tier_b_from_form(body: dict, form) -> dict:
    """Fold the Tier B body keys in, on top of `tier_a_from_form`'s result.

    Separate from Tier A on purpose. These are not harder to type, they are
    harder to be wrong about:

    * a `kind:"direct"` source is a FETCH TARGET -- DISCOVER's playbook rung
      enqueues `fetch.url` for every one, so a typo here is a request we send
      to somebody's server, and a host on the refused list is a request we
      have ruled we may never send. `validate` already raises `RobotsRefused`
      for that; the save path turns it into a 422 that quotes it, which is why
      the message is worth reading rather than rephrasing.
    * `skip_backfill` refuses a whole corpus folder. The reason is mandatory
      at parse time already (`_parse`), because a refusal nobody can account
      for later reads as a manufacturer that simply never files anything.
    * `extract_hints` is the only playbook value an LLM ever sees. Keys and
      lengths are both capped in `_parse`, so the CLI and the seed refuse what
      this form refuses.

    `bc_codes` is Tier B too but is NOT here: it is identity, it lives in
    `manufacturer_bc_code` rather than in the body, and it has its own route.
    """
    out = dict(body)

    kept_portal = [src for src in (out.get("doc_sources") or [])
                   if src.get("kind") == "portal"]
    directs = []
    for i in range(int(form.get("direct_count") or 0)):
        url = (form.get(f"direct.{i}.url") or "").strip()
        if not url:
            continue                       # a cleared row is a deletion
        entry = {
            "doc_type": (form.get(f"direct.{i}.doc_type") or "").strip(),
            "kind": "direct",
            "url": url,
        }
        note = (form.get(f"direct.{i}.note") or "").strip()
        if note:
            entry["note"] = note
        directs.append(entry)
    combined = kept_portal + directs
    if combined:
        out["doc_sources"] = combined
    else:
        out.pop("doc_sources", None)

    if form.get("skip_backfill"):
        # The reason rides along rather than being defaulted: `_parse` refuses
        # a blank one, so an unticked box and a ticked box with no reason are
        # different outcomes and the second is an error, not an empty string.
        entry = dict(out.get("skip_backfill") or {})
        entry["reason"] = (form.get("skip_backfill_reason") or "").strip()
        out["skip_backfill"] = entry
    else:
        out.pop("skip_backfill", None)

    hints = {}
    for key in form.keys() if hasattr(form, "keys") else ():
        if not key.startswith("hint."):
            continue
        value = (form.get(key) or "").strip()
        if value:
            hints[key[len("hint."):]] = value
    if hints:
        out["extract_hints"] = hints
    else:
        out.pop("extract_hints", None)

    return out


# --------------------------------------------------------------------------- #
# EUDAMED SRN confirm queue
# --------------------------------------------------------------------------- #
# Task 4's handler attributes each EUDAMED actor to a canonical manufacturer.
# A normalised exact name match auto-stores (`status='auto'`); anything
# fuzzier waits here for a person, because a wrong attribution eventually
# becomes a wrong e-mail to a supplier (Denis, 2026-08-26).
_SRN_CONFIRM_ROWS_SQL = """
SELECT s.canonical_name, s.srn, s.actor_name, s.match_score,
       s.discovered_via, s.first_seen,
       (SELECT count(*) FROM eudamed_certificate c
         WHERE c.actor_srn = s.srn) AS certificates
  FROM manufacturer_srn s
 WHERE s.status = 'pending'
 ORDER BY s.match_score DESC NULLS LAST, s.canonical_name
"""


def srn_confirm_rows(conn) -> list[dict]:
    """Candidates awaiting a human. `auto` never appears here -- a normalised
    exact match needs no confirmation -- and neither do decided rows."""
    return list(conn.execute(_SRN_CONFIRM_ROWS_SQL).fetchall())


# --------------------------------------------------------------------------- #
# Certificate findings (Task 6's six views, on screen)
# --------------------------------------------------------------------------- #
# Migration 043's four finding views, read once and handed to the
# `cert_findings` macro (`_ui.html`). `canonical_name` scopes every view to
# one manufacturer's page; `None` is the whole-registry board on `/expiry`.
def certificate_findings(conn, canonical_name: str | None = None) -> dict:
    """The four certificate findings plus their denominator.

    `coverage` exists because a finding list without its denominator reads as
    "we checked our certificates" when reach is a fraction of the corpus.

    Ruling 19 (2026-08-26): `held_certificate` can only ever count a document
    that reaches a production `item_document` link and, through it, a
    `canonical_manufacturer` -- so an EC/ISO document that carries a
    `cert_number` but has NO production link is invisible to `held`,
    `matched` AND `unmatched`, and to `certificate_gap`, which would then
    report the certificate it cites as missing and generate a request for a
    document we already hold. Measured on the live dev database 2026-08-26,
    EC/ISO documents carrying a `cert_number` only (391 counts EVERY document
    type and is not this bucket's denominator): 43 carry a `cert_number`, 34
    of those have no production `item_document` link (`unattributable`), 9
    reach `held_certificate` -- 34 + 9 = 43.
    `unattributable` counts that bucket on purpose, kept apart from both
    `matched` and `unmatched` -- it is never folded into either. It is a
    GLOBAL count, never scoped by `canonical_name`: by construction, a
    document in this bucket has no `canonical_manufacturer` to filter on.
    """
    where, args = "", ()
    if canonical_name:
        where, args = " WHERE canonical_name = %s", (canonical_name,)

    def rows(view):
        return list(conn.execute(f"SELECT * FROM {view}{where}", args).fetchall())

    held = conn.execute(
        "SELECT count(DISTINCT doc_id) c FROM held_certificate" + where, args
    ).fetchone()["c"]
    matched = conn.execute(
        "SELECT count(DISTINCT h.doc_id) c FROM held_certificate h "
        " JOIN trusted_manufacturer_srn s ON s.canonical_name = h.canonical_name "
        " JOIN eudamed_certificate c ON c.actor_srn = s.srn "
        "  AND c.certificate_number = h.base_cert_number" +
        (" WHERE h.canonical_name = %s" if canonical_name else ""), args
    ).fetchone()["c"]
    unattributable = conn.execute(
        "SELECT count(DISTINCT d.doc_id) c FROM document d "
        " WHERE d.type IN ('EC', 'ISO') AND d.cert_number IS NOT NULL "
        "   AND d.cert_number <> '' "
        "   AND NOT EXISTS (SELECT 1 FROM item_document idoc "
        "                    WHERE idoc.doc_id = d.doc_id "
        "                      AND idoc.status = 'production')"
    ).fetchone()["c"]
    synced = conn.execute(
        "SELECT max(synced_at) s FROM eudamed_certificate"
    ).fetchone()["s"]

    return {
        "drift": rows("certificate_drift"),
        "candidates": rows("certificate_drift_candidate"),
        "alerts": rows("certificate_status_alert"),
        "gaps": rows("certificate_gap"),
        "coverage": {"held": held, "matched": matched,
                     "unmatched": held - matched,
                     "unattributable": unattributable},
        "synced_at": synced,
    }


# --------------------------------------------------------------------------- #
# EUDAMED sweep due list and the operator release
# --------------------------------------------------------------------------- #
# Ruling, Denis 2026-08-20, restated 2026-08-26: "No sweep ever runs
# unattended -- an operator releases every run." Migration 044's scheduler
# writes only `due_at`; a person clicking the release button is the only path
# to `released_at`, which is what this reader and the POST route below exist
# for.
def sweep_due_rows(conn) -> list[dict]:
    """Manufacturers whose sweep is due and not yet released.

    Reads `eudamed_sweep_due`, which already filters to `due_at <= now() AND
    released_at IS NULL` -- a released sweep drops off this list on its own;
    nothing here re-filters for that.
    """
    return list(conn.execute(
        "SELECT canonical_name, last_swept_at, due_at, srns "
        "FROM eudamed_sweep_due ORDER BY due_at"
    ).fetchall())


# --------------------------------------------------------------------------- #
# EUDAMED declaration gap (Task 13's three views, on screen)
# --------------------------------------------------------------------------- #
# EUDAMED holds zero declarations itself -- it can only say what to go and ask
# for. A declaration covers a Basic UDI-DI group, not an article (the
# registry's largest measured gap is 2.569 Carl Martin reusable instruments,
# which is 70 documents to request, not 2.569), so `eudamed_declaration_gap`
# is grouped that way and this reader hands the template exactly what it
# groups.
_GAP_SUMMARY_DEFAULT = {"covered": 0, "missing": 0, "staged": 0, "not_in_eudamed": 0}


def uncovered_groups(conn, canonical_name: str, *, limit: int) -> dict:
    """This manufacturer's groups holding no production document.

    Delegates to `app.coverage`, which is the single definition shared with the
    coverage cron -- two implementations of "uncovered" would drift, and then
    this button promises work the cron has already done.

    **Groups, not articles.** The rest of this page counts articles
    (`manufacturer_completeness`); DISCOVER operates on groups, 8.208 of them
    against ~16k articles. Printing the article figure on this button would name
    a number the press cannot act on.
    """
    return coverage.uncovered_groups(
        conn, manufacturer=canonical_name, limit=limit)


def declaration_gap_summary(conn, canonical_name: str) -> dict:
    """The gap list, its denominator, and the sweep delta, for one
    manufacturer's page.

    `swept_at` is read straight off `eudamed_sweep_state`, never derived from
    whether any gap or delta row exists -- a manufacturer with zero gaps
    because it has never been swept must read "never swept", not "no gaps".
    These are different facts, and conflating them is the exact failure
    ruling 24 (migration 046) guards `eudamed_sweep_delta` against: a `None`
    here, not an empty list, is what tells the template which one is true.
    """
    totals = conn.execute(
        "SELECT covered, missing, staged, not_in_eudamed "
        "FROM eudamed_gap_summary WHERE canonical_name = %s",
        (canonical_name,),
    ).fetchone() or dict(_GAP_SUMMARY_DEFAULT)
    swept = conn.execute(
        "SELECT last_swept_at FROM eudamed_sweep_state WHERE canonical_name = %s",
        (canonical_name,),
    ).fetchone()
    return {
        "canonical_name": canonical_name,
        "totals": totals,
        "gaps": list(conn.execute(
            "SELECT basic_udi_di, articles, staged, mfr_scope_covered, trade_name "
            "FROM eudamed_declaration_gap WHERE canonical_name = %s "
            "ORDER BY articles DESC, basic_udi_di", (canonical_name,),
        ).fetchall()),
        "delta": list(conn.execute(
            "SELECT basic_udi_di, new_devices, status_changes "
            "FROM eudamed_sweep_delta WHERE canonical_name = %s "
            "ORDER BY basic_udi_di", (canonical_name,),
        ).fetchall()),
        "swept_at": swept["last_swept_at"] if swept else None,
    }


def eudamed_digest(conn) -> list[dict]:
    """Every manufacturer EUDAMED knows something about, on one page.

    W7 in `docs/officer-scenarios.md`: the per-manufacturer delta and gap cards
    already existed on `/manufacturers/{name}`, which answers "what is new" only
    if you open all 384 pages. This is the same two views without their
    `WHERE canonical_name = %s`, so there is no second definition of a gap or a
    delta to keep in sync -- if `eudamed_declaration_gap` changes meaning, this
    page changes with it.

    The name set is a UNION of four sources rather than a scan of `manufacturer`:
    a manufacturer with no EUDAMED footprint at all has nothing to say here and
    would only dilute the page. `eudamed_sweep_due` is in the union so that
    **never swept** appears as its own row rather than as an absence -- the same
    distinction ruling 24 protects inside `declaration_gap_summary` (a `None`
    `swept_at` means never swept, not "no gaps").

    Ordering is by what needs a person: anything that changed since the last
    reviewed sweep first, then the biggest declaration gaps, then never-swept,
    then oldest sweep. Measured 2026-09-03 on the dev database: 238ms over 27
    swept manufacturers -- a page, deliberately not header-strip material.
    """
    return list(conn.execute(
        """
        WITH names AS (
          SELECT canonical_name FROM eudamed_gap_summary
          UNION SELECT canonical_name FROM eudamed_sweep_delta
          UNION SELECT canonical_name FROM eudamed_sweep_state
          UNION SELECT canonical_name FROM eudamed_sweep_due
        ), delta AS (
          SELECT canonical_name,
                 count(*)               AS changed_udis,
                 sum(new_devices)       AS new_devices,
                 sum(status_changes)    AS status_changes
          FROM eudamed_sweep_delta GROUP BY canonical_name
        )
        SELECT n.canonical_name,
               COALESCE(g.covered, 0)        AS covered,
               COALESCE(g.staged, 0)         AS staged,
               COALESCE(g.missing, 0)        AS missing,
               COALESCE(g.not_in_eudamed, 0) AS not_in_eudamed,
               COALESCE(d.changed_udis, 0)   AS changed_udis,
               COALESCE(d.new_devices, 0)    AS new_devices,
               COALESCE(d.status_changes, 0) AS status_changes,
               s.last_swept_at,
               s.due_at,
               -- The digest is the page that SHOWS what needs doing, so it is
               -- also where the doing belongs. `eudamed_sweep_due` already
               -- filters `due_at <= now() AND released_at IS NULL`, so joining
               -- it -- rather than re-deriving the predicate here -- is what
               -- keeps this page and /manufacturers/sweep-due from ever
               -- offering a different set of releasable manufacturers.
               (du.canonical_name IS NOT NULL) AS due_now,
               COALESCE(du.srns, 0)            AS srns
        FROM names n
        LEFT JOIN eudamed_gap_summary g ON g.canonical_name = n.canonical_name
        LEFT JOIN delta d               ON d.canonical_name = n.canonical_name
        LEFT JOIN eudamed_sweep_state s ON s.canonical_name = n.canonical_name
        LEFT JOIN eudamed_sweep_due du  ON du.canonical_name = n.canonical_name
        ORDER BY COALESCE(d.new_devices, 0) + COALESCE(d.status_changes, 0) DESC,
                 COALESCE(g.missing, 0) DESC,
                 (s.last_swept_at IS NULL) DESC,
                 s.last_swept_at ASC NULLS LAST,
                 n.canonical_name
        """
    ).fetchall())


# --- the crawl-recipe probe (S2.2) ------------------------------------------
#
# Spec: `docs/superpowers/specs/2026-09-03-playbook-probe-wizard-design.md`.
# The editor could always author a crawl recipe; nothing anywhere proved one
# worked before it was saved. An author wrote `index_url` and `link_pattern`,
# saved, and found out whether they were right when a discovery run either
# produced documents or quietly produced none.
#
# Producer only, like every other button here (invariant 1): these routes
# enqueue `playbook.reonboard` and read back what the worker wrote. The web
# process never fetches.


class ProbeRefused(Exception):
    """The recipe cannot even be probed. Rendered as a 422, not a 500."""


def crawl_from_form(form, existing: dict | None = None) -> dict:
    """The probe form's fields as ONE `crawl` recipe.

    Used by both the probe route (which fetches with it) and the save route
    (which stores it), deliberately: two parsers would let an operator probe
    one recipe and save a different one, which is the exact failure this
    slice exists to prevent.

    `existing` is the recipe already stored for this `index_url`, if any. Its
    keys survive unless the form overrides them -- `pagination` has no control
    on this form, and a save that silently dropped it would turn a working
    two-page recipe into a broken one-page one with no error anywhere.
    """
    out = dict(existing or {})

    index_url = (form.get("index_url") or "").strip()
    if not index_url:
        raise ProbeRefused("a library URL is required")
    if not index_url.startswith(("http://", "https://")):
        raise ProbeRefused(
            f"the library URL must be absolute and http(s): {index_url!r}")
    out["index_url"] = index_url

    link_pattern = (form.get("link_pattern") or "").strip()
    if not link_pattern:
        raise ProbeRefused("a link pattern is required (try \\.pdf$)")
    try:
        re.compile(link_pattern)
    except re.error as exc:
        raise ProbeRefused(f"the link pattern is not a valid regex: {exc}")
    out["link_pattern"] = link_pattern

    # `allow_hosts` -- the ONLY host-widening mechanism (crawl design §4.3).
    # On the form, unlike `same_host_only` below, because an empty box means
    # exactly what it looks like: no other host. Ultradent is the case that
    # needs it -- the library page is on ultradent.com and every one of its 147
    # PDFs is on assets.ctfassets.net, so without this the harvest is 0 of 149.
    # Present on BOTH the probe and the save (they share this parser), so an
    # operator can never probe a wider recipe than the one they store.
    if "allow_hosts" in form:
        hosts = []
        for raw in _split_lines(form.get("allow_hosts", "")):
            # Stored NORMALIZED, unlike `domains` a few hundred lines up, which
            # keeps what was typed because a path scope there is meaningful and
            # only `_parse` can say what it folds to. Paths are refused below,
            # so the fold is total here: what the recipe table shows is then
            # exactly what `app.crawl` compares a link's host against.
            host = playbooks_mod._normalize_domain(raw)
            if not host:
                raise ProbeRefused(f"{raw!r} is not a hostname")
            if "/" in host:
                # `_normalize_domain` keeps an authored path scope on purpose,
                # because `domains` needs one (Medentika lives under
                # straumann.com/medentika). The crawl's host check compares a
                # bare hostname (`app/crawl.py`, `allowed_hosts`), so a path
                # here would widen NOTHING and raise nothing. Refuse at the
                # door rather than store a value that cannot match.
                raise ProbeRefused(
                    f"{raw!r} carries a path. An extra host is matched whole, "
                    f"so write the host alone: {host.split('/')[0]}")
            hosts.append(host)
        if hosts:
            out["allow_hosts"] = hosts
        else:
            out.pop("allow_hosts", None)

    raw_max = (form.get("max_links") or "").strip()
    if raw_max:
        try:
            max_links = int(raw_max)
        except ValueError:
            raise ProbeRefused(f"the cap must be a whole number: {raw_max!r}")
        if max_links < 1 or max_links > playbooks_mod.CRAWL_MAX_LINKS_CEILING:
            raise ProbeRefused(
                f"the cap must be between 1 and "
                f"{playbooks_mod.CRAWL_MAX_LINKS_CEILING}")
        out["max_links"] = max_links

    mapping = doc_type_from_lines(form.get("doc_type_from") or "")
    if mapping:
        out["doc_type_from"] = mapping
    else:
        out.pop("doc_type_from", None)

    # Never authored from this form. `same_host_only` is the guard that keeps a
    # recipe from following a link off the manufacturer's site, and the crawl
    # design ruled it widened ONLY by naming a host in `allow_hosts` -- which
    # is above. So this form pins it True rather than offering a checkbox whose
    # unticked state is indistinguishable from "the operator did not see it":
    # widening is done by naming the host you mean, never by removing a guard.
    out["same_host_only"] = True
    return out


def doc_type_from_lines(text: str) -> dict:
    """`needle=TYPE` per line -> the `doc_type_from` map.

    An empty value is an EXCLUSION (`sds_=` keeps NSK's 446 safety data sheets
    out of the registry), which is why this cannot just skip blank right-hand
    sides: `null` is a meaning here, not a missing value.
    """
    mapping: dict[str, str | None] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        needle, sep, doc_type = line.partition("=")
        needle = needle.strip()
        doc_type = doc_type.strip()
        if not needle:
            continue
        if not sep:
            raise ProbeRefused(
                f"{line!r} is missing the = sign. Write ifu=IFU, or sds_= to "
                f"exclude a family from the registry while still counting it")
        if doc_type and doc_type not in playbooks_mod.DOC_TYPES:
            raise ProbeRefused(
                f"{doc_type!r} is not a document type. One of: "
                f"{', '.join(sorted(playbooks_mod.DOC_TYPES))}")
        mapping[needle] = doc_type or None
    return mapping


def probe_dedupe_key(slug: str, index_url: str) -> str:
    """Per invariant 8 this scopes to ACTIVE jobs only, so re-probing the same
    URL once a result is in is allowed — which is exactly what an author
    adjusting a pattern does. A double-click, meanwhile, joins the run already
    going rather than asking the manufacturer's server twice."""
    return f"playbook.reonboard:{slug}:{normalize_url(index_url)}"


#: Classified buckets lead; this one is always rendered when anything was
#: harvested, never folded away. A thin harvest must not read as a complete one.
UNCLASSIFIED = "unclassified"


def probe_view(conn, slug: str, job_id: int) -> dict:
    """What the operator sees: the job, the row the worker wrote, and whether
    the partial should keep polling."""
    job = conn.execute(
        "SELECT id, status, last_error, attempts, payload FROM job WHERE id=%s",
        (job_id,)).fetchone()
    probe = conn.execute(
        "SELECT id, index_url, status, robots_verdict, tier, counts, sample, "
        "       error, created_at, finished_at "
        "FROM playbook_probe WHERE via_job=%s ORDER BY id LIMIT 1",
        (job_id,)).fetchone()

    counts = (probe["counts"] if probe else None) or {}
    by_type = counts.get("by_type") or {}
    kept = counts.get("links_kept", 0)
    breakdown = sorted(
        ((k, v) for k, v in by_type.items() if k != UNCLASSIFIED),
        key=lambda kv: (-kv[1], kv[0]))
    if kept:
        breakdown.append((UNCLASSIFIED, by_type.get(UNCLASSIFIED, 0)))

    waiting = probe is None or probe["status"] == "pending"
    # Stated on the verdict line, before any count: what a real crawl of this
    # library would cost the manufacturer's server. An operator reading "1.329
    # links" should see the interval in the same breath.
    politeness_ms = load_config().fetch.politeness_ms
    # A dead job never runs again, so polling it forever is a spinner that
    # means nothing. A `failed` one is still in backoff and WILL run again.
    stopped = job is None or job["status"] == "dead"
    return {
        "slug": slug,
        "job": job,
        "job_id": job_id,
        "probe": probe,
        "counts": counts,
        "breakdown": breakdown,
        "sample": (probe["sample"] if probe else None) or [],
        "politeness_ms": politeness_ms,
        "polling": waiting and not stopped,
        "stopped_without_result": waiting and stopped,
    }


def save_crawl_recipe(body: dict, recipe: dict) -> dict:
    """Fold one recipe into a playbook body's `crawl` LIST, replacing the entry
    with the same `index_url` rather than appending a second one.

    `crawl` has been a list since 2026-09-03 precisely so a manufacturer with
    two libraries needs no second shape. Matching on `index_url` is what makes
    "probe, adjust the pattern, probe again, save" converge on one recipe
    instead of accumulating near-duplicates that each fetch the same page.
    """
    out = dict(body)
    recipes = [dict(r) for r in (out.get("crawl") or [])]
    for i, existing in enumerate(recipes):
        if existing.get("index_url") == recipe["index_url"]:
            recipes[i] = recipe
            break
    else:
        recipes.append(recipe)
    out["crawl"] = recipes
    return out


def register_routes(app, templates, conn_factory, playbooks_dir: str | None,
                    authenticated_user=None, default_user: str = "user:admin",
                    require_operator=None) -> None:
    """`authenticated_user` is `web/app.py::_authenticated_user` -- passed in
    rather than re-derived here, so there is ONE place that decides who the
    proxy says you are, and `decided_by` on a gate decision and `updated_by` on
    a contact edit can never disagree about it.

    `require_operator` is `web/access.py::operator_guard`, passed in the same
    way and for the same reason. FIVE routes here are D3 writes and
    `_operator_only` below turns it into the `dependencies=` list each of them
    declares: the playbook save, the BC-code claim, the revert (a body written
    forward as a new revision is a playbook edit whichever button asked for
    it), and `probe` and `crawl`, which are the ones that spend money -- the
    probe enqueues a job that fetches, and `crawl` saves what it found. None (a
    caller that did not pass one) means no route-level guard, which is what
    every test that builds these routes directly expects.
    """

    def _operator_only():
        return [Depends(require_operator)] if require_operator else []

    def _who(request: Request) -> str:
        return (authenticated_user(request) if authenticated_user else None) or default_user

    # Registered above `/manufacturers/{canonical_name:path}` deliberately --
    # that route's `:path` converter would otherwise swallow `srn-queue` as a
    # canonical name and render the manufacturer-detail page instead of this
    # one (a 200, not a 404 -- easy to miss).
    @app.get("/manufacturers/srn-queue", response_class=HTMLResponse)
    def srn_queue(request: Request):
        with conn_factory() as conn:
            rows = srn_confirm_rows(conn)
        return templates.TemplateResponse(
            request,
            "srn_queue.html",
            {"rows": rows, "total": len(rows)},
        )

    @app.post("/manufacturers/srn-queue/{canonical_name}/{srn}")
    def srn_decide(canonical_name: str, srn: str, decision: str = Form(...)):
        # A closed set, checked here rather than trusted: this endpoint
        # decides which manufacturer's entire catalogue gets swept.
        if decision not in ("confirm", "reject"):
            raise HTTPException(status_code=400, detail="unknown decision")
        status = "confirmed" if decision == "confirm" else "rejected"
        with conn_factory() as conn:
            # `AND status = 'pending'` guards a double-submit or a stale form
            # from flipping an already-decided row.
            conn.execute(
                "UPDATE manufacturer_srn SET status = %s, decided_at = now(), "
                "       decided_by = 'ui' "
                " WHERE canonical_name = %s AND srn = %s AND status = 'pending'",
                (status, canonical_name, srn),
            )
            conn.commit()
        return RedirectResponse("/manufacturers/srn-queue", status_code=303)

    # Registered above `/manufacturers/{canonical_name:path}` for the same
    # reason as `srn-queue` above: that route's `:path` converter would
    # otherwise swallow `eudamed` as a canonical name.
    #
    # Under /manufacturers rather than at the top level (2026-09-03) because
    # the sidebar renders it as a sub-item of Manufacturers alongside the two
    # queues, and the parent's active rule is `startswith('/manufacturers')` —
    # at `/eudamed` the parent link stayed unlit while its own child was
    # highlighted. The path is the grouping; the indent only draws it.
    @app.get("/manufacturers/eudamed", response_class=HTMLResponse)
    def eudamed_overview(request: Request):
        """The cross-manufacturer EUDAMED digest (W7).

        Read-only and a producer of nothing: the two actions EUDAMED work needs
        -- release a sweep, ask for a missing declaration -- stay on the pages
        that own them, and each row links there.
        """
        with conn_factory() as conn:
            rows = eudamed_digest(conn)
        return templates.TemplateResponse(
            request,
            "eudamed.html",
            {
                "rows": rows,
                "total": len(rows),
                "changed": sum(1 for r in rows
                               if r["new_devices"] or r["status_changes"]),
                "never_swept": sum(1 for r in rows if r["last_swept_at"] is None),
            },
        )

    @app.get("/manufacturers/sweep-due", response_class=HTMLResponse)
    def sweep_due_queue(request: Request):
        with conn_factory() as conn:
            rows = sweep_due_rows(conn)
        return templates.TemplateResponse(
            request,
            "sweep_due.html",
            {"rows": rows, "total": len(rows)},
        )

    #: Where a release may return to. An allowlist, not a redirect the caller
    #: names: `back` arrives in a form post, and echoing an arbitrary value into
    #: a Location header is an open redirect. Anything unrecognised falls back
    #: to the sweep-due board, which is where every release went before the
    #: digest grew its own button.
    _SWEEP_BACK = ("/manufacturers/sweep-due", "/manufacturers/eudamed")

    # `:path`, like `contacts`/`playbook`/`rename` and the detail GET (F43,
    # 2026-09-15). Three canonical manufacturer names contain a slash, across
    # 102 item groups, and the plain converter cannot match one -- so for those
    # three the button rendered and the POST 404'd. The trailing literal segment
    # is what keeps the greedy converter honest: the regex backtracks to the
    # LAST `/sweep`, exactly as it already does for `/contacts`.
    @app.post("/manufacturers/{canonical_name:path}/sweep")
    def sweep_release(canonical_name: str, back: str = Form("")):
        """The release button. Ruling, Denis 2026-08-20 restated 2026-08-26:
        "no sweep ever runs unattended" -- the scheduler only ever proposes by
        writing `due_at`; this is the one path to `released_at`.

        Rendered on TWO pages since 2026-09-04: the sweep-due board and the
        EUDAMED digest. Same route, same guard, same set of releasable
        manufacturers (both read `eudamed_sweep_due`) -- only the page you land
        back on differs. `[eudamed-digest-cannot-act]`, UI audit 2026-09-04.

        Refused with 400, no job enqueued, when `trusted_manufacturer_srn`
        holds nothing for this manufacturer: releasing anyway would burn the
        politeness lease against EUDAMED for a sweep that can only no-op, and
        still report success.
        """
        with conn_factory() as conn:
            trusted = conn.execute(
                "SELECT count(*) c FROM trusted_manufacturer_srn "
                "WHERE canonical_name = %s", (canonical_name,),
            ).fetchone()["c"]
            if not trusted:
                raise HTTPException(
                    status_code=400,
                    detail=f"no trusted SRN on file for {canonical_name} -- nothing to sweep",
                )
            jid = queue.enqueue(
                conn, "eudamed.sweep", {"manufacturer": canonical_name},
                f"eudamed.sweep:{canonical_name}",
            )
            # `enqueue` returns None when an active eudamed.sweep job already
            # holds this dedupe key. That means an earlier release already
            # stamped released_at/released_by for the run that is actually
            # executing -- stamping again here would overwrite the audit of
            # who released it with this click's identity instead.
            if jid is not None:
                conn.execute(
                    "UPDATE eudamed_sweep_state SET released_at = now(), "
                    "       released_by = 'ui' WHERE canonical_name = %s",
                    (canonical_name,),
                )
            conn.commit()
        dest = back if back in _SWEEP_BACK else "/manufacturers/sweep-due"
        return RedirectResponse(dest, status_code=303)

    @app.post("/manufacturers/{canonical_name:path}/discover")
    def manufacturer_discover(canonical_name: str, cap: int | None = None):
        """Send this manufacturer's uncovered groups through DISCOVER.

        The registry had a per-ITEM trigger and no way to say "go look for
        everything this manufacturer is missing". This is it, and it is capped
        (`discovery.manufacturer_button_cap`, default 25) because the
        distribution is extremely skewed: measured 2026-09-02, 365 of 367
        manufacturers have uncovered groups, the median has 3 and 84% have 25 or
        fewer -- but HENRY SCHEIN has 809, and uncapped that is one click
        queueing up to 2.400 fetches at `topk` 3, most of them against a single
        domain at the 2s politeness interval.

        `ignore_recency` is true, mirroring `/items/{item_ref}/rediscover`
        (Denis, 2026-09-02): a press means look again now. That is also why
        `uncovered_groups` orders never-discovered first and then least recently
        discovered -- with a forced re-look and a stable ordering, every press
        would re-do the same 25 and a large manufacturer could never be walked
        through.

        Producer only (invariant 1): enqueues and nothing else. Dedupe is per
        group per day, the same key shape the item button and the CLI use, so a
        double-click, a refresh and `discover-item` on one of these groups all
        collapse onto one job.
        """
        # `cap` may only ever make a press SMALLER. Without the clamp
        # `?cap=100000` queues all 809 of HENRY SCHEIN's groups from the address
        # bar, which is exactly what the configured cap exists to prevent -- and
        # a limit a caller can raise is not a limit.
        configured = load_config().discovery.manufacturer_button_cap
        limit = max(0, min(cap, configured)) if cap is not None else configured
        with conn_factory() as conn:
            known = conn.execute(
                "SELECT 1 FROM item_group WHERE canonical_manufacturer=%s LIMIT 1",
                (canonical_name,),
            ).fetchone()
            if known is None:
                raise HTTPException(status_code=404, detail="unknown manufacturer")
            found = uncovered_groups(conn, canonical_name, limit=limit)
            # Postgres current_date, not Python's -- worker-clock skew, same
            # reasoning as discover.py's _known_url_rows.
            today = conn.execute("SELECT current_date AS d").fetchone()["d"]
            queued = 0
            for gid in found["group_ids"]:
                if queue.enqueue(
                    conn, "discover.group",
                    {"group_id": gid, "ignore_recency": True},
                    f"discover:refetch:{gid}:{today.isoformat()}",
                    priority="sweep",
                ) is not None:
                    queued += 1
            conn.commit()
        if not found["total"]:
            outcome = "nothing-to-do"
        elif not queued:
            outcome = "deduped"
        else:
            outcome = f"queued:{queued}"
        return RedirectResponse(
            f"/manufacturers/{quote(canonical_name)}?discover={outcome}",
            status_code=303,
        )

    @app.post("/manufacturers/{canonical_name:path}/gap-request")
    def gap_request_draft(canonical_name: str, request: Request):
        """Draft the first ask for every Basic UDI-DI group we hold no
        declaration for.

        A PRODUCER, like every other button here (invariant 1): it enqueues
        `email.request` with `reason='eudamed-gap'` and writes no draft itself.
        The handler composes and inserts the `email_draft` row, and nothing
        sends -- spec 7.1, the chain ends at a draft a person releases.

        Refused with 400 and no job when the gap is empty, for the same reason
        the sweep button refuses an untrusted SRN: enqueueing a job that can
        only no-op reads as success and teaches the operator to distrust the
        button. Note an empty gap has two very different causes -- nothing
        missing, or never swept -- and the panel above the button is where that
        distinction is shown.
        """
        with conn_factory() as conn:
            gaps = conn.execute(
                "SELECT count(*) c FROM eudamed_declaration_gap "
                "WHERE canonical_name = %s", (canonical_name,),
            ).fetchone()["c"]
            if not gaps:
                raise HTTPException(
                    status_code=400,
                    detail=(f"no EUDAMED declaration gap for {canonical_name} "
                            "-- nothing to request"),
                )
            queue.enqueue(
                conn, "email.request",
                {"manufacturer": canonical_name, "reason": "eudamed-gap"},
                f"email.request:gap:{canonical_name}",
            )
            # Post-redirect-get with an outcome, the same shape the discover
            # button uses. Read, not decided, here: the HANDLER refuses a
            # second ask (Denis 2026-09-03), and this only says so before the
            # job runs, so a press is never silent. Without it the button
            # looked broken -- it enqueued, the handler declined, and the page
            # came back identical.
            asked = conn.execute(
                "SELECT id, status FROM email_draft WHERE kind = 'gap-request' "
                "AND manufacturer = %s ORDER BY id DESC LIMIT 1",
                (canonical_name,),
            ).fetchone()
            conn.commit()
        outcome = f"asked:{asked['id']}:{asked['status']}" if asked else "queued"
        return RedirectResponse(
            f"/manufacturers/{quote(canonical_name)}?gap={outcome}",
            status_code=303)

    @app.post("/manufacturers/{canonical_name:path}/cert-request")
    def cert_request_draft(canonical_name: str, request: Request):
        """Draft an ask for every certificate EUDAMED records and we do not hold.

        The sibling of `gap_request_draft` and deliberately its twin: a PRODUCER
        (invariant 1) that enqueues `email.request` with
        `reason='certificate-gap'` and writes no draft itself, refuses with 400
        when there is nothing to ask for, and reports an existing ask rather
        than looking broken when the handler declines a second one.

        It is a separate button from the gap request because it is a separate
        letter. A declaration gap names articles and asks the supplier to work
        out which document covers them; this names the document -- number,
        revision, dates, notified body -- off their own public register entry.
        Often a different person answers it.
        """
        with conn_factory() as conn:
            gaps = conn.execute(
                "SELECT count(*) c FROM certificate_gap "
                "WHERE canonical_name = %s", (canonical_name,),
            ).fetchone()["c"]
            if not gaps:
                raise HTTPException(
                    status_code=400,
                    detail=(f"no EUDAMED certificate gap for {canonical_name} "
                            "-- nothing to request"),
                )
            queue.enqueue(
                conn, "email.request",
                {"manufacturer": canonical_name, "reason": "certificate-gap"},
                f"email.request:cert:{canonical_name}",
            )
            asked = conn.execute(
                "SELECT id, status FROM email_draft WHERE kind = 'cert-request' "
                "AND manufacturer = %s ORDER BY id DESC LIMIT 1",
                (canonical_name,),
            ).fetchone()
            conn.commit()
        outcome = f"asked:{asked['id']}:{asked['status']}" if asked else "queued"
        return RedirectResponse(
            f"/manufacturers/{quote(canonical_name)}?cert={outcome}",
            status_code=303)

    @app.post("/manufacturers/{canonical_name:path}/review-confirm")
    def review_confirm_draft(canonical_name: str, request: Request):
        """Ask whether this supplier's five-year-old declarations are still
        current.

        The third of the three producer buttons on this page and the same shape
        as its siblings: it enqueues `email.request` with `reason='review-due'`,
        writes no draft itself, and refuses with 400 when there is nothing to
        ask about.

        Its guard is deliberately narrower than the gap and certificate asks.
        Those refuse for good once asked; this one recurs by design -- a
        declaration confirmed in 2026 is due again in 2031 -- so the handler
        refuses only while an unsent draft of this kind is still open.
        """
        with conn_factory() as conn:
            due = conn.execute(
                "SELECT count(*) c FROM document d "
                "  JOIN document_effective_expiry e ON e.doc_id = d.doc_id "
                " WHERE d.status='production' AND d.type='DoC' "
                "   AND e.basis='staleness' AND e.expires <= current_date "
                "   AND EXISTS (SELECT 1 FROM item_document l "
                "                 JOIN item_group_member gm ON gm.item_ref=l.item_ref "
                "                 JOIN item_group g ON g.group_id=gm.group_id "
                "                WHERE l.doc_id=d.doc_id AND l.status='production' "
                "                  AND g.canonical_manufacturer=%s)",
                (canonical_name,),
            ).fetchone()["c"]
            if not due:
                raise HTTPException(
                    status_code=400,
                    detail=(f"no declaration past its review point for "
                            f"{canonical_name} -- nothing to confirm"))
            queue.enqueue(
                conn, "email.request",
                {"manufacturer": canonical_name, "reason": "review-due"},
                f"email.request:review:{canonical_name}",
            )
            asked = conn.execute(
                "SELECT id, status FROM email_draft "
                "WHERE kind = 'review-confirm' AND manufacturer = %s "
                "  AND status IN ('draft','ready') ORDER BY id DESC LIMIT 1",
                (canonical_name,),
            ).fetchone()
            conn.commit()
        outcome = f"asked:{asked['id']}:{asked['status']}" if asked else "queued"
        return RedirectResponse(
            f"/manufacturers/{quote(canonical_name)}?review={outcome}",
            status_code=303)

    @app.get("/manufacturers", response_class=HTMLResponse)
    def manufacturers(
        request: Request,
        q: str = Query(default=""),
        no_playbook: int = Query(default=0),
    ):
        pbs, pb_error = load_playbook_index(playbooks_dir)
        with conn_factory() as conn:
            rows = manufacturer_rows(conn)
        rows = annotate_playbooks(rows, pbs)
        rows = filter_and_sort(rows, q, bool(no_playbook))
        return templates.TemplateResponse(
            request,
            "manufacturers.html",
            {
                "rows": rows,
                "q": q,
                "no_playbook": bool(no_playbook),
                "playbook_error": pb_error,
                "total": len(rows),
            },
        )

    @app.get("/manufacturers/{canonical_name:path}", response_class=HTMLResponse)
    def manufacturer_detail_page(request: Request, canonical_name: str, tab: str = "",
                                 discover: str = "", gap: str = "", cert: str = "",
                                 review: str = ""):
        """`?tab=` selects which of the three sections opens.

        The page used to print all three stacked, and the item list is by far
        the longest of them: measured 2026-08-18, IVOCLAR ran to 23.065px --
        14,5 viewports -- with "Production documents" starting at 15.541px and
        the attributed list at 17.737px. The compliance documents are what the
        page exists for, and they were ten screens below a catalogue dump.

        `all` is the default because it is the superset and the question a
        reader arrives with ("what do we hold from this manufacturer?"),
        including the `filed` documents that no item link can reach.
        """
        with conn_factory() as conn:
            detail = manufacturer_detail(conn, canonical_name)
            if detail is not None:
                detail["completeness"] = manufacturer_completeness(
                    conn, canonical_name
                )
                detail["cert_findings"] = certificate_findings(conn, canonical_name)
                detail["gap_summary"] = declaration_gap_summary(conn, canonical_name)
                # Groups, not articles: DISCOVER operates on groups, and the
                # completeness card above counts articles. Printing the article
                # figure on this button would name a number the press cannot act on.
                cap = load_config().discovery.manufacturer_button_cap
                detail["discover_cap"] = cap
                detail["uncovered_group_count"] = uncovered_groups(
                    conn, canonical_name, limit=0)["total"]
        if detail is None:
            raise HTTPException(status_code=404, detail="unknown manufacturer")
        detail["tab"] = tab if tab in ("all", "production", "items") else "all"
        detail["discover"] = discover
        detail["gap"] = gap
        detail["cert"] = cert
        detail["review"] = review
        pbs, pb_error = load_playbook_index(playbooks_dir)
        row = annotate_playbooks(
            [{
                "canonical_name": canonical_name,
                "codes": [c["code"] for c in detail["codes"]],
            }],
            pbs,
        )[0]
        detail["playbook_slug"] = row["playbook_slug"]
        detail["playbook_error"] = pb_error
        detail["suggested_slug"] = slug_for(canonical_name)
        return templates.TemplateResponse(request, "manufacturer_detail.html", detail)

    @app.post("/manufacturers/{canonical_name:path}/contacts", response_class=HTMLResponse)
    def manufacturer_contacts_save(request: Request, canonical_name: str,
                                   contact_emails: str = Form("")):
        """Save the addresses `email.request` will draft to.

        An EMPTY list is legal and is not an error: `email_request._contacts`
        treats it as "a person addresses this one by hand", and the drafts UI
        already refuses to release a draft with no recipient. So the failure
        this guards is a malformed address, not an absent one.

        Guarded UPDATE ... RETURNING, the same shape as `draft_save`: if the
        manufacturer row does not exist (no seed run yet), nothing is written
        and the reason says which, rather than silently creating an entity the
        seed did not derive.
        """
        ctx = {"request": request}
        raw = [a.strip() for a in contact_emails.replace(";", ",").split(",") if a.strip()]
        bad = [a for a in raw if "@" not in a or " " in a or a.startswith("@")
               or a.endswith("@")]
        if bad:
            ctx["error"] = f"not an email address: {', '.join(bad)}"
            return templates.TemplateResponse(request, "_result.html", ctx, status_code=422)

        user = _who(request)
        with conn_factory() as conn:
            row = conn.execute(
                "UPDATE manufacturer SET contact_emails=%s, updated_by=%s, "
                "  updated_at=now() "
                "WHERE canonical_name=%s RETURNING id",
                (raw, user, canonical_name),
            ).fetchone()
            conn.commit()

        if row is None:
            ctx["error"] = (
                f"no manufacturer row for {canonical_name!r} — run "
                "`dentalia manufacturers seed` first"
            )
            return templates.TemplateResponse(request, "_result.html", ctx, status_code=422)

        ctx["saved"] = (
            f"{len(raw)} contact(s) saved by {user}" if raw
            else f"contacts cleared by {user}"
        )
        return templates.TemplateResponse(request, "_result.html", ctx)

    @app.post("/manufacturers/{canonical_name:path}/playbook", response_class=HTMLResponse,
              dependencies=_operator_only())
    def manufacturer_start_playbook(request: Request, canonical_name: str,
                                    slug: str = Form("")):
        """Start a playbook for a manufacturer that has none.

        The other half of `/manufacturers?no_playbook=1`: that list has always
        said who needs one, and this is the first thing that can make one
        without an engineer and a git checkout.

        Operator-only (D3), and it was the one route that was not. It reaches
        the same `start_playbook` as `/onboarding/{name}/start`, which P5b
        guarded, by a second and older door -- found by asking "can the guard
        be walked around" rather than by reading the list of routes the slice
        had named. A guard on a writer is only worth the weakest route that
        reaches it.
        """
        ctx = {"request": request}
        with conn_factory() as conn:
            try:
                out = start_playbook(conn, canonical_name=canonical_name,
                                     slug=slug or slug_for(canonical_name),
                                     user=_who(request))
            except SaveRefused as exc:
                conn.rollback()
                ctx["error"] = str(exc)
                return templates.TemplateResponse(
                    request, "_result.html", ctx, status_code=422)
            conn.commit()

        ctx["saved"] = (
            f"playbook {out['slug']!r} started, empty, at revision 0. "
            f"Author it at /playbooks/{out['slug']} — domains and document "
            f"sources first; DISCOVER can use those immediately.")
        ctx["link_href"] = f"/playbooks/{out['slug']}"
        ctx["link_text"] = f"Author {out['slug']} now"
        return templates.TemplateResponse(request, "_result.html", ctx)

    @app.post("/manufacturers/{canonical_name:path}/rename", response_class=HTMLResponse)
    def manufacturer_rename(request: Request, canonical_name: str,
                            new_name: str = Form(""), note: str = Form(""),
                            confirm: str = Form("")):
        """Two-step, and the first step is the point.

        Without `confirm` this writes nothing and answers with the blast
        radius: how many groups still carry the old name (they do not follow a
        rename), how many of those can never be regrouped because a member
        already carries a document link, and the CLI line that finishes the
        job. Every refusal is reachable from the un-confirmed step too, so an
        operator sees a collision before deciding, not after ticking a box.
        """
        ctx = {"request": request}
        with conn_factory() as conn:
            try:
                out = rename_manufacturer(
                    conn, canonical_name=canonical_name, new_name=new_name,
                    note=note, user=_who(request), confirm=bool(confirm))
            except SaveRefused as exc:
                conn.rollback()
                ctx["error"] = str(exc)
                return templates.TemplateResponse(
                    request, "_result.html", ctx, status_code=422)
            conn.commit()

        impact = out["impact"]
        if not out["applied"]:
            lines = [f"Rename {canonical_name!r} to {out['new_name']!r}?"]
            # 053: these FOLLOW the rename (ON UPDATE CASCADE) rather than being
            # stranded by it, so this line is stated before the stranding ones
            # and separately from them -- it is what WILL be rewritten, not what
            # will be left behind. Reported at all because a rename that
            # silently rewrites rows is the failure 052 closed.
            if impact.bound_documents:
                lines.append(
                    f"{impact.bound_documents} document(s) are bound to "
                    f"{canonical_name!r} and will follow the rename "
                    f"automatically — no action needed for them.")
            if impact.clean:
                lines.append("No item group carries the old name, so nothing "
                             "is stranded.")
            else:
                lines.append(
                    f"{impact.groups} item group(s) ({impact.items} item(s)) "
                    f"still carry {canonical_name!r} and will keep it — RESOLVE "
                    f"short-circuits on an existing link, so a rename does not "
                    f"reach them.")
                if impact.with_documents:
                    lines.append(
                        f"{impact.with_documents} of those group(s) hold "
                        f"{impact.documents} document link(s) and CANNOT be "
                        f"regrouped: dropping them would discard "
                        f"document-to-item links nothing rebuilds.")
                lines.append(f"Finish with: {impact.command}")
            ctx["confirm_needed"] = lines
            ctx["confirm_url"] = f"/manufacturers/{canonical_name}/rename"
            ctx["confirm_target"] = "rename-result"
            ctx["confirm_fields"] = {"new_name": out["new_name"], "note": note}
            ctx["confirm_label"] = "Yes, rename it"
            return templates.TemplateResponse(request, "_result.html", ctx)

        done = [f"renamed to {out['new_name']!r} by {_who(request)}; "
                f"{canonical_name!r} kept as an alias so documents printing it "
                f"still resolve."]
        done.append(
            "Still to do, and NOT done here: `dentalia playbooks sync` "
            "re-points `manufacturer_alias`, which this process may not write.")
        if not impact.clean:
            done.append(f"Then: {impact.command} ({impact.groups} group(s)).")
        ctx["saved"] = " ".join(done)
        return templates.TemplateResponse(request, "_result.html", ctx)

    @app.get("/playbooks", response_class=HTMLResponse)
    def playbooks_page(request: Request):
        rows, error = playbook_rows(playbooks_dir)
        with conn_factory() as conn:
            drift = playbook_drift(conn, playbooks_dir)
        return templates.TemplateResponse(
            request,
            "playbooks.html",
            {"rows": rows, "playbook_error": error, "drift": drift},
        )

    @app.get("/playbooks/{slug}", response_class=HTMLResponse)
    def playbook_detail_page(request: Request, slug: str):
        detail = playbook_detail(playbooks_dir, slug)
        if detail is None:
            raise HTTPException(status_code=404, detail="unknown playbook")
        with conn_factory() as conn:
            row = conn.execute(
                "SELECT playbook_rev, updated_by, updated_at, body FROM manufacturer "
                "WHERE slug=%s", (slug,)).fetchone()
            detail["revisions"] = revisions(conn, slug)
            detail["claimable"] = claimable_codes(conn, slug) if row else []
        # `rev` is rendered into the form as a hidden field and comes back on
        # save: it is what makes the optimistic lock possible at all.
        detail["rev"] = row["playbook_rev"] if row else 0
        detail["updated_by"] = row["updated_by"] if row else None
        detail["updated_at"] = row["updated_at"] if row else None
        # Editable only once the playbook is IN the database. Before
        # `manufacturers seed` has imported it there is no row to lock against
        # and no revision to record, and a form that saved anyway would write
        # a body the seed then refuses to reconcile.
        detail["editable"] = row is not None
        detail["portal_sources"] = [s for s in detail["doc_sources"]
                                    if s["kind"] == "portal"]
        detail["direct_sources"] = [s for s in detail["doc_sources"]
                                    if s["kind"] != "portal"]
        detail["site_query"] = site_query_preview(detail["domains"])
        detail["doc_types"] = list(playbooks_mod.DOC_TYPES)
        detail["rungs"] = sorted(playbooks_mod.DISCOVER_RUNGS)
        detail["source_priority"] = list(
            (row["body"] or {}).get("source_priority", []) if row else [])
        # From the DATABASE body, like every other Tier B key on this page --
        # `playbook_detail` above reads the file store, and the file is the
        # authoring record, not what the pipeline runs.
        detail["crawl"] = list((row["body"] or {}).get("crawl", []) if row else [])
        detail["crawl_max_links_default"] = playbooks_mod.CRAWL_MAX_LINKS_DEFAULT
        detail["crawl_max_links_ceiling"] = playbooks_mod.CRAWL_MAX_LINKS_CEILING
        detail["date_labels"] = (row["body"] or {}).get("date_labels", {}) if row else {}
        detail["type_markers"] = (row["body"] or {}).get("type_markers", {}) if row else {}
        # Tier B (task 14). The hint KEYS come from the extractor's own target
        # list rather than a copy of it, so a field added there shows up here
        # instead of being silently unhintable; `_parse` allows exactly this
        # set plus "general", and refuses anything else. From
        # `app.extract.target`, the leaf that holds the list -- NOT from
        # `app.extract.tiers`, which pulls in PyMuPDF and is absent from the
        # slim web image this route runs in (2026-08-27).
        from app.extract.target import TARGET

        body = (row["body"] or {}) if row else {}
        detail["skip_backfill"] = body.get("skip_backfill")
        detail["extract_hints"] = body.get("extract_hints", {})
        detail["hint_keys"] = list(TARGET) + ["general"]
        detail["hint_max_chars"] = playbooks_mod.HINT_MAX_CHARS
        detail["hint_max_total"] = playbooks_mod.HINT_MAX_TOTAL
        return templates.TemplateResponse(request, "playbook_detail.html", detail)

    @app.post("/playbooks/{slug}", response_class=HTMLResponse,
              dependencies=_operator_only())
    async def playbook_save(request: Request, slug: str):
        """Save the Tier A fields.

        Reads the raw form rather than declaring parameters: `doc_sources` is a
        variable-length list of objects (`doc_source.0.url`, ...), which FastAPI
        cannot express as typed arguments without a schema per row.

        The EXISTING body is the starting point and the form is folded into it,
        so the Tier B and C keys this form never renders survive the save. A
        route that serialised the form alone would delete them and report
        success.
        """
        form = await request.form()
        ctx = {"request": request}
        try:
            rev = int(form.get("rev") or 0)
        except ValueError:
            ctx["error"] = "the form did not carry a revision; reload the page"
            return templates.TemplateResponse(request, "_result.html", ctx,
                                              status_code=422)

        with conn_factory() as conn:
            row = conn.execute(
                "SELECT body FROM manufacturer WHERE slug=%s", (slug,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="unknown playbook")
            proposed = tier_b_from_form(tier_a_from_form(row["body"] or {}, form),
                                        form)
            try:
                new_rev = save_playbook_body(
                    conn, slug=slug, body=proposed, note=form.get("note") or "",
                    user=_who(request), expected_rev=rev,
                    playbooks_dir=playbooks_dir)
                conn.commit()
            except SaveRefused as exc:
                conn.rollback()
                ctx["error"] = str(exc)
                return templates.TemplateResponse(request, "_result.html", ctx,
                                                  status_code=422)

        ctx["saved"] = f"{slug} saved as revision {new_rev}"
        return templates.TemplateResponse(request, "_result.html", ctx)

    @app.post("/playbooks/{slug}/codes", response_class=HTMLResponse,
              dependencies=_operator_only())
    def playbook_claim_code(request: Request, slug: str, code: str = Form(...),
                            confirm: str = Form("")):
        """Claim one more BC code for this playbook (Tier B).

        Add only -- 049 grants no DELETE on `manufacturer_bc_code` -- which
        matches what this is for: it is the escape a `BrandCollision` refusal
        names, and it is how a multi-code manufacturer stops being split.

        Two-step (Task 0 contract): the first press prices the move -- which
        code, whose it is today, whose it would become -- and writes nothing;
        only `confirm=1` moves it.
        """
        ctx = {"request": request}
        with conn_factory() as conn:
            try:
                out = claim_bc_code(conn, slug=slug, code=code,
                                    user=_who(request), confirm=bool(confirm))
            except SaveRefused as exc:
                conn.rollback()
                ctx["error"] = str(exc)
                return templates.TemplateResponse(
                    request, "_result.html", ctx, status_code=422)
            conn.commit()

        if not out["applied"]:
            target = out["manufacturer"]
            owner = out["current_owner"]
            line = (f"Move {out['code']} from {owner} to {target}?" if owner
                    else f"Claim {out['code']} for {target}?")
            ctx["confirm_needed"] = [
                line,
                "Run `dentalia playbooks sync` afterwards to re-point "
                "`manufacturer_alias` — this process may not write it — "
                "and check its `orphaned_groups` count.",
            ]
            ctx["confirm_url"] = f"/playbooks/{slug}/codes"
            ctx["confirm_target"] = "code-result"
            ctx["confirm_fields"] = {"code": code}
            ctx["confirm_label"] = "Yes, claim it"
            return templates.TemplateResponse(request, "_result.html", ctx)

        msg = (f"{slug} now claims BC code {out['code']} "
               f"({out['vendor_name']}, {out['items']} item(s)).")
        if out["moved_from"]:
            msg += f" Moved from {out['moved_from']!r}."
        msg += (" Run `dentalia playbooks sync` to re-point `manufacturer_alias` "
                "— this process may not write it — and check its "
                "`orphaned_groups` count.")
        ctx["saved"] = msg
        return templates.TemplateResponse(request, "_result.html", ctx)

    @app.post("/playbooks/{slug}/revert", response_class=HTMLResponse,
              dependencies=_operator_only())
    def playbook_revert(request: Request, slug: str,
                        to_rev: int = Form(...), rev: int = Form(...)):
        """Restore an earlier body by writing it forward as a NEW revision.

        `rev` is the revision the page was rendered at, not the one being
        restored -- reverting from a stale page is the same hazard as saving
        from one, and gets the same refusal.
        """
        ctx = {"request": request}
        try:
            with conn_factory() as conn:
                new_rev = revert_playbook(
                    conn, slug=slug, to_rev=to_rev, user=_who(request),
                    expected_rev=rev, playbooks_dir=playbooks_dir)
                conn.commit()
        except SaveRefused as exc:
            ctx["error"] = str(exc)
            return templates.TemplateResponse(request, "_result.html", ctx,
                                              status_code=422)
        ctx["saved"] = (f"{slug} restored to revision {to_rev}, recorded as "
                        f"revision {new_rev}")
        return templates.TemplateResponse(request, "_result.html", ctx)

    # --- crawl-recipe probe (S2.2) ------------------------------------------

    @app.post("/playbooks/{slug}/probe", response_class=HTMLResponse,
              dependencies=_operator_only())
    async def playbook_probe_start(request: Request, slug: str):
        """Look at a manufacturer's document library without fetching any of it.

        Enqueues `playbook.reonboard` and returns the polling partial. This
        route performs no HTTP request of its own: the worker does robots, the
        politeness lease and the one GET, which is what keeps a UI button from
        being a way around the fetch rules.
        """
        form = await request.form()
        try:
            recipe = crawl_from_form(form)
        except ProbeRefused as exc:
            return templates.TemplateResponse(
                request, "_result.html", {"request": request, "error": str(exc)},
                status_code=422)

        payload = dict(recipe)
        payload["slug"] = slug
        payload["requested_by"] = _who(request)
        key = probe_dedupe_key(slug, recipe["index_url"])
        with conn_factory() as conn:
            job_id = queue.enqueue(conn, "playbook.reonboard", payload, key,
                                   priority="interactive")
            if job_id is None:
                # Deduped: a probe of this URL is already in flight. Join it
                # rather than reporting nothing -- a second click means "show
                # me", not "ask their server again".
                row = conn.execute(
                    "SELECT id FROM job WHERE dedupe_key=%s AND status IN "
                    "('pending','running','failed') ORDER BY id DESC LIMIT 1",
                    (key,)).fetchone()
                job_id = row["id"] if row else None
            conn.commit()
            if job_id is None:
                # The active job finished between the enqueue and the lookup.
                # Rare, and a retry is the honest answer.
                return templates.TemplateResponse(
                    request, "_result.html",
                    {"request": request,
                     "error": "that probe finished as this one was queued; "
                              "press Probe again to see it"},
                    status_code=409)
            ctx = probe_view(conn, slug, job_id)
        ctx["request"] = request
        ctx["recipe"] = recipe
        return templates.TemplateResponse(request, "_probe.html", ctx)

    @app.get("/playbooks/{slug}/probe/{job_id}", response_class=HTMLResponse)
    def playbook_probe_poll(request: Request, slug: str, job_id: int):
        """The polling half. Re-arms itself only while there is no result, so a
        finished probe stops asking and a dead job says so instead of spinning.
        """
        with conn_factory() as conn:
            ctx = probe_view(conn, slug, job_id)
            if ctx["probe"] is None and ctx["job"] is None:
                raise HTTPException(status_code=404, detail="unknown probe")
            row = conn.execute(
                "SELECT playbook_rev FROM manufacturer WHERE slug=%s",
                (slug,)).fetchone()
        # `rev` rides into the save form as a hidden field: it is what makes
        # the optimistic lock possible at all, and 0 for a playbook not yet in
        # the database (whose save is refused, correctly).
        ctx["rev"] = row["playbook_rev"] if row else 0
        ctx["request"] = request
        # The recipe the probe RAN WITH, read back off the job payload rather
        # than off the browser's form -- so "Save recipe" saves what was
        # proved, not what someone edited in the box while it ran.
        ctx["recipe"] = ctx["job"]["payload"] if ctx["job"] else None
        return templates.TemplateResponse(request, "_probe.html", ctx)

    @app.post("/playbooks/{slug}/crawl", response_class=HTMLResponse,
              dependencies=_operator_only())
    async def playbook_save_crawl(request: Request, slug: str):
        """Save the probed recipe into the playbook body.

        Goes through `save_playbook_body` unchanged: optimistic lock on `rev`,
        revision recorded, revert available, `validate()` run over the PROPOSED
        SET before anything is written. The wizard composes a `crawl` block and
        hands it over; it does not get its own write path, and a probe never
        writes a playbook by itself.
        """
        form = await request.form()
        ctx = {"request": request}
        try:
            rev = int(form.get("rev") or 0)
        except ValueError:
            ctx["error"] = "the form did not carry a revision; reload the page"
            return templates.TemplateResponse(request, "_result.html", ctx,
                                              status_code=422)
        with conn_factory() as conn:
            row = conn.execute(
                "SELECT body FROM manufacturer WHERE slug=%s", (slug,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="unknown playbook")
            body = row["body"] or {}
            index_url = (form.get("index_url") or "").strip()
            prior = next((r for r in (body.get("crawl") or [])
                          if r.get("index_url") == index_url), None)
            try:
                recipe = crawl_from_form(form, prior)
            except ProbeRefused as exc:
                ctx["error"] = str(exc)
                return templates.TemplateResponse(request, "_result.html", ctx,
                                                  status_code=422)
            try:
                new_rev = save_playbook_body(
                    conn, slug=slug, body=save_crawl_recipe(body, recipe),
                    note=form.get("note") or f"crawl recipe for {index_url}",
                    user=_who(request), expected_rev=rev,
                    playbooks_dir=playbooks_dir)
                conn.commit()
            except SaveRefused as exc:
                conn.rollback()
                ctx["error"] = str(exc)
                return templates.TemplateResponse(request, "_result.html", ctx,
                                                  status_code=422)
        ctx["saved"] = (
            f"{slug} saved as revision {new_rev}. The recipe is stored, not "
            f"run — DISCOVER crawls it on this manufacturer's next discovery "
            f"job. `playbooks/{slug}.json` still holds the old body and will "
            f"now show up in `dentalia playbooks drift`; the database is what "
            f"the pipeline reads.")
        return templates.TemplateResponse(request, "_result.html", ctx)
