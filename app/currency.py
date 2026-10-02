"""Per-item currency for readers that hold one (item, document) row at a time.

`document.status` says what happened to a document; it no longer says whether
the document answers for a given item (migration 071: the view decides, per
item and type). Readers that rank or count holdings by status read this
expression instead, so `superseded` keeps meaning "history for this item" to
`app/compliance.py`, `web/registry.py` and `app/bc_fields.py` without any of
them learning the rule.

Aliases: `l` is `item_document`, `d` is `document`."""

DOC_STATUS_FOR_ITEM = """
CASE WHEN l.status = 'production' AND d.status IN ('production', 'superseded')
     THEN CASE WHEN EXISTS (SELECT 1 FROM item_document_production p
                             WHERE p.item_ref = l.item_ref AND p.doc_id = d.doc_id)
               THEN 'production' ELSE 'superseded' END
     ELSE d.status::text END
"""
