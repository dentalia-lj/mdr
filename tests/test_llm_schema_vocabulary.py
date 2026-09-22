"""The structured-output schema must bar values the registry rejects.

`coverage_scope` was retired to `group|manufacturer` at the T0 producer and
closed with a CHECK constraint (migration 022), but the LLM tiers were never
told: `llm.py`'s SCHEMA left the field a free string and both prompts still
listed `item` as an option. So T1/T2 kept returning it, and migration 022 --
doing exactly its job -- rejected the row and dead-lettered the document. Four
GC pilot documents were lost that way.

Prompt wording is a request; the JSON Schema is enforced by the API. Constrain
the vocabulary where it cannot be ignored, so a retired value cannot be returned
at all rather than being caught two stages later by a constraint that kills the
document.
"""

from __future__ import annotations

from app.extract import llm


def _scope_schema():
    return llm.SCHEMA["properties"]["coverage_scope"]["properties"]["value"]


def _scope_vocabulary():
    """The allowed values, however the schema spells nullability. Since
    2026-08-13 that is anyOf(string-enum, null) rather than a union type with an
    enum, which the API rejects with a 400 -- see
    test_no_field_declares_an_enum_beside_a_union_type."""
    node = _scope_schema()
    if "enum" in node:
        return list(node["enum"])
    values = []
    for branch in node.get("anyOf", []):
        if "enum" in branch:
            values.extend(branch["enum"])
        elif branch.get("type") == "null":
            values.append(None)
    return values


def test_coverage_scope_is_a_closed_vocabulary():
    vocab = _scope_vocabulary()
    assert vocab, "coverage_scope must not be a free string"
    assert set(v for v in vocab if v is not None) == {"group", "manufacturer"}


def test_retired_item_value_is_not_offered():
    # The exact value migration 022 rejects.
    assert "item" not in _scope_vocabulary()


def test_null_stays_allowed():
    # A tier that genuinely cannot tell must still be able to say so; the ladder
    # escalates on a missing field, which is a better outcome than a guess.
    assert None in _scope_vocabulary()


def test_prompts_do_not_offer_the_retired_value():
    # Schema and prompt must agree: a prompt still listing `item` would have the
    # model fighting the schema, which shows up as refusals and retries.
    for text in (llm.T1_SYSTEM, llm.T2_SYSTEM):
        assert "item | group" not in text
        assert "item|group" not in text


def test_prompts_warn_that_a_signing_date_is_not_an_expiry():
    # 3 of 10 extracted dates were a place-and-date signature line ("Leuven,
    # 12/02/2026") stored as validity_to, which reads as an expired certificate.
    # validity_from carried this warning and validity_to did not, so the guard on
    # one field pushed the error into its unguarded neighbour.
    for text in (llm.T1_SYSTEM, llm.T2_SYSTEM):
        lower = text.lower()
        assert "signing" in lower or "signed" in lower
        assert "valid until" in lower or "expiry phrase" in lower


def test_both_prompts_carry_the_same_ref_list_cap():
    # The cap started at 40 (2026-07-07, a max_tokens guard) under the premise that
    # "a deterministic parser enumerates the full list". Nothing enforced that
    # premise, so the capped sample was stored over T0's enumeration and the GC
    # corpus lost 1.341 of 2.843 codes. tiers.merge now refuses the shrink; the
    # cap is raised so the tiers lose less when T0 finds nothing at all.
    # Both prompts must agree: a drift makes T2's answer shrink T1's.
    import re
    caps = []
    for text in (llm.T1_SYSTEM, llm.T2_SYSTEM):
        m = re.search(r"\*\*at most (\d+)\*\*", text)
        assert m, "the ref_list cap must stay stated and greppable in the prompt"
        caps.append(int(m.group(1)))
    assert caps[0] == caps[1], f"prompt caps drifted: {caps}"
    assert caps[0] >= 150


def test_the_cap_fits_the_token_budget():
    # 40 existed so the fallback could not blow max_tokens. That constraint is
    # real and still has to hold: ~12 bytes per code plus JSON overhead, against
    # DEFAULT_MAX_TOKENS raised to 8192 in S0.4. Assert the headroom rather than
    # trusting the arithmetic stayed true.
    import re
    cap = int(re.search(r"\*\*at most (\d+)\*\*", llm.T1_SYSTEM).group(1))
    worst_case_tokens = cap * 12       # generous bytes-per-code, ~1 token each
    assert worst_case_tokens < llm.DEFAULT_MAX_TOKENS / 2


def test_manufacturer_is_in_the_structured_output_schema():
    # SCHEMA is generated from _FIELD_VALUE so both tiers stay in step; a field
    # in TARGET but not here would be requested and never returned.
    from app.extract import tiers
    props = llm.SCHEMA["properties"]
    assert "manufacturer" in props
    assert set(tiers.TARGET) <= set(props), set(tiers.TARGET) - set(props)


def test_both_prompts_define_manufacturer_and_bar_the_representative():
    # 66 corpus files name `Dentsply IH Limited`, always as the authorised
    # representative and never as the manufacturer. Reading that as identity
    # would file Maillefer and VDW documents under the wrong company.
    for text in (llm.T1_SYSTEM, llm.T2_SYSTEM):
        lower = text.lower()
        assert "manufacturer" in lower
        assert "authorised representative" in lower or "authorized representative" in lower


def test_no_field_declares_an_enum_beside_a_union_type():
    """The exact 400 that broke every extraction between 2026-08-12 and 2026-08-13:

        output_config.format.schema: Invalid schema:
        Enum value 'group' does not match declared type '['string', 'null']'

    The API refuses an `enum` alongside a union `type`. Nullable enums must use
    anyOf. This is a structural check because the real one costs an API call --
    see test_schema_is_accepted_by_the_live_api, which is opt-in."""
    def walk(node, path="schema"):
        if not isinstance(node, dict):
            return
        if "enum" in node and isinstance(node.get("type"), list):
            raise AssertionError(f"{path}: enum beside a union type is a 400; use anyOf")
        for k, v in node.items():
            if isinstance(v, dict):
                walk(v, f"{path}.{k}")
            elif isinstance(v, list):
                for i, item in enumerate(v):
                    walk(item, f"{path}.{k}[{i}]")
    walk(llm.SCHEMA)


def test_schema_is_accepted_by_the_live_api():
    """Opt-in, and the only test that would have caught the outage. Skipped
    without a key so CI stays free; run it before any paid sweep.

        ANTHROPIC_API_KEY=... pytest -k accepted_by_the_live_api

    Costs a handful of tokens on the cheapest model."""
    import os
    import pytest
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        pytest.skip("no ANTHROPIC_API_KEY; opt-in live schema check")
    import anthropic
    client = anthropic.Anthropic(api_key=key)
    client.messages.create(
        model="claude-haiku-4-5-20251001", max_tokens=64,
        messages=[{"role": "user", "content": "Return nulls for every field."}],
        output_config={"format": {"type": "json_schema", "schema": llm.SCHEMA}},
    )


def test_schema_stays_under_the_union_parameter_limit():
    """The API caps a schema at 16 union-typed parameters ("exponential
    compilation cost"). A nullable `page` on every field spent 10 of them, and
    the tenth target field took the total to 18 -- a 400 that killed every
    extraction with no tokens billed and no test to catch it.

    This is the offline guard. It counts what the API counts, so an eleventh
    field or a newly-nullable property fails here rather than in production."""
    unions = [f"{field}.{prop}"
              for field, spec in llm.SCHEMA["properties"].items()
              for prop, p in spec["properties"].items()
              if isinstance(p.get("type"), list) or "anyOf" in p]
    assert len(unions) <= 16, f"{len(unions)} union-typed parameters: {unions}"


def test_page_is_optional_rather_than_nullable():
    """How the union budget is kept. Omitting `page` says "no page" as well as a
    null does: `_to_ev` reads it with .get(), and GATE turns the absence into
    `evidence-page-missing` -- archived and reviewed, not lost."""
    page = llm.SCHEMA["properties"]["type"]["properties"]["page"]
    assert page == {"type": "integer"}
    assert "page" not in llm.SCHEMA["properties"]["type"]["required"]
    assert llm._to_ev({"value": "DoC", "confidence": 1.0, "verbatim": "x"},
                      "T1", "m")["page"] is None
