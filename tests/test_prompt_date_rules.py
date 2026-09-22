"""The date rule is a contract, and it lives in prose.

Both tier prompts are plain markdown loaded at import (`app/extract/llm.py:26`),
so nothing type-checks them and a well-meaning edit can silently invert the
rule. It has already cost us once: three GC declarations stored the signature
line "Leuven, 12/02/2026" as `validity_to` and read as EXPIRED in the review UI
(documents 160, 227, 239).

Denis's ruling, 2026-08-13: a signature or issue date is the START of validity,
never the end. A document takes effect when it is issued. These tests are the
regression guard on the wording that carries that ruling to the model.

They deliberately assert on PHRASES, not on exact sentences -- the prompts are
meant to be reworded as we learn, and a test that pins the whole paragraph
would be rewritten mechanically on every edit instead of being read.
"""

from __future__ import annotations

import pytest

from app.extract import llm


PROMPTS = {"t1": llm.T1_SYSTEM, "t2": llm.T2_SYSTEM}


@pytest.mark.parametrize("name", sorted(PROMPTS))
def test_prompt_sends_the_signing_date_to_validity_from(name):
    """The rule must be stated, not merely implied by omission."""
    body = PROMPTS[name].lower()
    assert "validity_from" in body and "validity_to" in body
    # The issue/signing date is named as a source for validity_from.
    assert "issue" in body
    # The place-and-date example is the one that actually burned us.
    assert "leuven" in body or "place-and-date" in body or "place and date" in body


@pytest.mark.parametrize("name", sorted(PROMPTS))
def test_prompt_never_tells_the_model_to_drop_an_issue_date(name):
    """Guards the exact wording that caused 0 of 127 declarations to carry a
    start date: "NOT the issue / signing date -- null if only an issue date is
    shown". A prompt that says this again reinstates the bug."""
    body = " ".join(PROMPTS[name].lower().split())
    forbidden = [
        "not the issue / signing / print",
        "not the issue/signing date",
        "null if only an issue date",
        "most docs have no `validity_from`",
    ]
    for phrase in forbidden:
        assert phrase not in body, (
            f"{name} prompt reinstates the retired rule: {phrase!r}. A signing "
            "date is validity_from, never null and never validity_to."
        )


@pytest.mark.parametrize("name", sorted(PROMPTS))
def test_prompt_still_bars_a_signing_date_from_validity_to(name):
    """The half of the old rule that was RIGHT and must survive the change: a
    place-and-date line is never an expiry. Losing this re-creates the
    false-expiry alarm from the other direction."""
    body = " ".join(PROMPTS[name].lower().split())
    assert "never" in body
    assert "expiry phrase" in body, (
        f"{name} prompt no longer requires an explicit expiry phrase before "
        "returning validity_to"
    )
