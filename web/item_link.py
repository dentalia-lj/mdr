"""The BC item-card link (spec §3).

`https://api.cw.dentalia.si/item/{item_ref}?k={bc_link_key}` — built by BC
through string concatenation in a computed field. That constraint is the whole
reason this URL carries a shared static key instead of a per-item signature:
BC cannot compute anything (Denis, 2026-08-25).

One URL, two renderings. A support rep clicking from the item card gets a page
they can read and download from; the webshop backend asking with
`Accept: application/json` (or `?format=json`, because BC's client may send
`*/*`) gets the same payload as JSON.

Read-only. The access policy in `web/access.py` decides who gets here at all.
"""

from __future__ import annotations

import io
import zipfile

from fastapi import HTTPException, Request
from fastapi.responses import Response

from web.item_docs import item_documents


def wants_json(request: Request, fmt: str) -> bool:
    """`?format=json` wins over Accept, because BC's HTTP client is not
    guaranteed to send a useful Accept header and a rep must never land on a
    raw JSON blob."""
    if fmt == "json":
        return True
    return "application/json" in request.headers.get("accept", "")


def register_routes(app, templates, conn_factory, cfg) -> None:
    # Registered BEFORE `/item/{item_ref:path}`. Order matters: the path
    # converter is greedy and would otherwise match `/item/X/documents.zip`
    # with item_ref="X/documents.zip". The trailing literal `/documents.zip`
    # is what anchors this pattern, the same way
    # `/api/items/{item_ref:path}/documents` is anchored.
    @app.get("/item/{item_ref:path}/documents.zip")
    def item_documents_zip(item_ref: str):
        """Every production document for one item, in one archive.

        The "papers" a client asks for on the phone are plural; one click
        beats five. Built in memory -- an item's documents are a handful of
        PDFs, not a corpus."""
        # Local import, deliberately: importing these at module top level
        # would make `web.item_link` and `web.app` import each other in a
        # cycle, since `web.app` imports this module.
        from web.app import (
            _download_name, _parse_path_rewrites, _resolve_archive_path,
        )

        with conn_factory() as conn:
            data = item_documents(conn, item_ref)
            rows = conn.execute(
                "SELECT d.doc_id, d.archive_url, d.content_hash FROM document d "
                "JOIN item_document_production p ON p.doc_id = d.doc_id "
                "WHERE p.item_ref = %s ORDER BY d.doc_id",
                (item_ref,),
            ).fetchall()
        if not rows:
            # An empty archive is a worse answer than saying there is nothing.
            raise HTTPException(status_code=404, detail="no documents for this item")

        rewrites = _parse_path_rewrites(cfg.path_rewrites)
        buf = io.BytesIO()
        written = 0
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for row in rows:
                target = _resolve_archive_path(
                    row["archive_url"] or "", [cfg.archive_root, cfg.imports_dir],
                    rewrites=rewrites, content_hash=row["content_hash"],
                )
                if target is None:
                    # Counted, never silent (CLAUDE.md): a document whose bytes
                    # are unreachable is a data problem, not a reason to 500.
                    continue
                zf.write(target, _download_name(target.name, row["content_hash"]))
                written += 1
        if not written:
            raise HTTPException(
                status_code=404, detail="no archived files are reachable for this item"
            )
        name = (data["item_name"] or item_ref).replace("/", "-")
        return Response(
            content=buf.getvalue(),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{name}-documents.zip"'
            },
        )

    @app.get("/item/{item_ref:path}")
    def item_link(
        request: Request, item_ref: str, format: str = "", view: str = "full"
    ):
        with conn_factory() as conn:
            data = item_documents(
                conn, item_ref, view=view, base_url=cfg.public_base_url
            )
        if wants_json(request, format):
            return data
        return templates.TemplateResponse(request, "item_card.html", data)
