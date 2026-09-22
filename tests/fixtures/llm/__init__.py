"""RecordedLlm: a CI-safe LLM tier stub at the REAL tier interface.

The tier ladder (``app.extract.tiers.run_extraction``) injects an LLM object and
calls ``extract(tier, doc, filename, missing) -> {field: evidence}`` (see
``app.extract.llm.AnthropicLlm``). This stub implements that exact interface with
RECORDED responses instead of live Anthropic calls, so corpus-level ladder tests
are deterministic and need no API key.

Responses are keyed by ``(content_hash, tier)`` where ``content_hash`` is the
sha256 of the file at ``filename`` — the same digest the pipeline uses — so a
recorded answer follows its fixture. Every ``extract`` call is recorded in
``.calls`` for escalation assertions (only-missing-fields-are-asked).

``fill_missing=True`` returns a generic well-formed evidence for any requested
field with no recorded value, so the ladder can run to completion on docs we have
not hand-recorded. Recorded values always win over the generic fill.

Response file format (one JSON object per file under ``responses/``), matching the
handbook 5 evidence shape (``tier``/``model_id`` are stamped by the stub, so the
per-field objects omit them)::

    {"content_hash": "sha256:<hex>", "tier": "T1", "model_id": "haiku-4.5",
     "fields": {"basic_udi_di": {"value": "...", "conf": 0.92,
                                 "verbatim": "...", "page": 1}}}
"""

from __future__ import annotations

import hashlib
import json
import pathlib

from app.extract import pdf as pdfutil

RESPONSES_DIR = pathlib.Path(__file__).parent / "responses"


def hash_file(path: str) -> str:
    return "sha256:" + hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


class RecordedLlm:
    def __init__(self, responses: list[dict] | None = None, *, fill_missing: bool = False,
                 fill_conf: float = 0.9):
        self._fields: dict[tuple[str, str], dict] = {}
        self._model: dict[tuple[str, str], str] = {}
        self.calls: list[tuple[str, tuple]] = []
        self.fill_missing = fill_missing
        self.fill_conf = fill_conf
        for r in responses or []:
            self.register(**r)

    @classmethod
    def from_dir(cls, path: str | pathlib.Path = RESPONSES_DIR, **kwargs) -> "RecordedLlm":
        resp = [json.loads(fp.read_text()) for fp in sorted(pathlib.Path(path).glob("*.json"))]
        return cls(resp, **kwargs)

    def register(self, content_hash: str, tier: str, fields: dict, model_id: str = "fake-model") -> None:
        self._fields[(content_hash, tier)] = fields
        self._model[(content_hash, tier)] = model_id

    def extract(self, tier: str, doc, filename: str, missing: list[str]) -> dict:
        """Real tier interface. Returns evidence for requested `missing` fields:
        recorded values where present, generic fill otherwise (if enabled)."""
        self.calls.append((tier, tuple(missing)))
        # A text tier reads nothing off a scanned page — model that so scans fall
        # through to T2 (matches AnthropicLlm T1 getting empty full_text).
        if tier == "T1" and doc is not None and pdfutil.is_scan(doc):
            return {}
        key = (hash_file(filename), tier)
        recorded = self._fields.get(key, {})
        model_id = self._model.get(key, "fake-model")
        out: dict = {}
        for field in missing:
            if field in recorded:
                ev = dict(recorded[field])
                ev["tier"] = tier
                ev["model_id"] = model_id
                ev.setdefault("page", None)
                out[field] = ev
            elif self.fill_missing:
                out[field] = {
                    "value": f"<{tier}:{field}>", "conf": self.fill_conf, "tier": tier,
                    "verbatim": f"stub {tier} {field}", "page": None, "model_id": model_id,
                }
        return out
