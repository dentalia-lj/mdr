"""Evidence-shape contracts (PRD v3 invariant 2 / handbook 5).

Two shapes, deliberately distinct:

* EXTRACTOR_KEYS  — what any tier attaches to a field it extracts, in-memory:
                    value, conf, tier, verbatim, page. LLM tiers additionally
                    tag ``model_id``.
* PRODUCTION_KEYS — what a value must carry once GATE writes it to the registry:
                    archive_url, page, verbatim, tier, model_id, confidence,
                    extracted_at. GATE rejects any candidate missing these.

Shared by the T0 corpus tests and the T1/T2 tier scaffold so the contract is
asserted the same way everywhere.
"""

from __future__ import annotations

EXTRACTOR_KEYS = frozenset({"value", "conf", "tier", "verbatim", "page"})
LLM_EXTRACTOR_KEYS = EXTRACTOR_KEYS | {"model_id"}
PRODUCTION_KEYS = frozenset(
    {"archive_url", "page", "verbatim", "tier", "model_id", "confidence", "extracted_at"}
)


def assert_extractor_evidence(ev: dict, *, expected_tier: str | None = None, llm: bool = False) -> None:
    keys = LLM_EXTRACTOR_KEYS if llm else EXTRACTOR_KEYS
    missing = keys - set(ev)
    assert not missing, f"incomplete extractor evidence, missing {sorted(missing)}: {ev}"
    conf = ev["conf"]
    assert conf is None or 0.0 <= conf <= 1.0, f"conf out of range: {conf}"
    if expected_tier is not None:
        assert ev["tier"] == expected_tier, f"tier {ev['tier']!r} != {expected_tier!r}"


def is_production_evidence_complete(ev: dict) -> bool:
    """GATE's admission rule: all provenance keys present and verbatim non-empty."""
    return PRODUCTION_KEYS <= set(ev) and bool(ev.get("verbatim"))
