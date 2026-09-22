"""The extraction target schema, alone in a leaf module.

Split out of `app/extract/tiers.py` on 2026-08-27. `TARGET` is a list of field
names and needs nothing to define it, but importing it from `tiers` drags in
`app.extract.pdf` and therefore PyMuPDF. Two callers outside the extractor need
the field names and must never need the extractor: `app.playbooks._parse`
(imported by DISCOVER, and by the web UI) and the playbook editor route in
`web/registry.py`. The web image is slim by design -- `Dockerfile.web` installs
`.[web]`, not `.[extract]` -- so in that container the `tiers` import raised
`ModuleNotFoundError: No module named 'pymupdf'`, `_parse` failed for EVERY
playbook row, and `/playbooks` rendered "38 playbook file(s) failed to parse"
with an empty table and no reachable editor.

So this module imports NOTHING. `tiers` re-exports `TARGET` from here, which
leaves `tiers.TARGET` working for the extractor, the tools and the tests that
already read it there.
"""

from __future__ import annotations

# The 11-field target schema (handbook 5 / PRD 5). referenced_docs is low
# priority; validity_to and ref_list are legitimately empty for some doc types.
# `manufacturer` is the tenth (2026-08-13): without it nothing produces identity
# for a corpus document, so VALIDATE's scoped path can never fire.
# `stated_class` is the eleventh (2026-08-21): the risk class the document
# itself prints. T0-only and opportunistic, on the `referenced_docs` model --
# it appears in NO `_ESCALATE_*` set in `tiers`, and that absence is the entire
# mechanism keeping it free. QA/display only: BC's `item_mirror.product_class`
# stays the source of truth for what class an item IS, and nothing in this
# pipeline writes that column from a document.
TARGET = [
    "type", "regulation", "validity_from", "validity_to", "coverage_scope",
    "ref_list", "basic_udi_di", "referenced_docs", "cert_number", "manufacturer",
    "stated_class",
]
