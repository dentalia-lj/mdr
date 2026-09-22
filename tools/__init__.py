"""Dev-time analysis tooling. NOT part of the pipeline.

Deliberately outside `app/`: these modules read the local corpus and BC exports
and draw conclusions about them. They never touch Postgres, the queue, the
network, or an LLM, and they are excluded from the wheel
(`[tool.hatch.build.targets.wheel] packages = ["app", "web"]`) so nothing here
can be imported by a handler or ship in a runtime image.

The analyzers call the PIPELINE's own extraction code (`app.extract.*`,
`app.adapters.source`) rather than re-implementing it, so what they measure is
what the pipeline actually does. The 2026-07-15 corpus sweep used `pdftotext`
with a <100-char scan threshold while the pipeline routes to T2 on
`app.extract.pdf.is_scan` (PyMuPDF, <20 chars) — that divergence is exactly the
class of error reuse prevents.

Run: `python -m tools <corpus|catalogue|crossref|claims> [--out DIR]`
"""
