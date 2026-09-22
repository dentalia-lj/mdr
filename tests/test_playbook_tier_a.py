"""Tier A form controls (slice 3a, task 11).

Tier A is the set of fields a non-dev may edit, drawn on BLAST RADIUS: the
worst a wrong value here can do is cost the search rung a miss or read one
extra date, and the history card makes every one of them reversible.

Most of what these cover is the additive fold. A body carries Tier B and C keys
this form never renders -- `cert_number_pattern`, `skip_backfill`, the T0 parse
template -- and a save built by serialising the form would delete them and
report success. That is a data-loss bug that looks exactly like a working
editor, so it is asserted key by key rather than assumed.

No database: these are pure functions over a body and a form.
"""
from __future__ import annotations

import pytest

from app.playbooks import DISCOVER_RUNGS
from web.registry import site_query_preview, tier_a_from_form


class _Form(dict):
    """Stand-in for starlette's FormData: dict access plus `getlist` for the
    repeated checkbox field."""

    def __init__(self, values, multi=None):
        super().__init__(values)
        self._multi = multi or {}

    def getlist(self, key):
        return self._multi.get(key, [])


def _body():
    return {
        "domains": ["old.example"],
        "doc_sources": [
            {"doc_type": "DoC", "kind": "portal", "url": "https://portal/"},
            {"doc_type": "EC", "kind": "direct", "url": "https://cert.pdf",
             "note": "verified 2026-08-27"},
        ],
        "cert_number_pattern": r"HZ (\d+)",       # Tier C
        "skip_backfill": {"reason": "folder is a mess"},   # Tier B
        "match": {"anchors": ["Artikelnummer"]},           # Tier C
        "ref_strategy": "text-column",                     # Tier C
    }


def _form(**over):
    base = {
        "domains": "", "doc_source_count": "0",
        "date_labels.from": "", "date_labels.to": "",
        "type_markers.DoC": "", "type_markers.EC": "",
        "type_markers.IFU": "", "type_markers.ISO": "",
    }
    multi = over.pop("_multi", {})
    base.update(over)
    return _Form(base, multi)


# --------------------------------------------------------------------------- #
# the fold is additive

@pytest.mark.parametrize("key", ["cert_number_pattern", "skip_backfill",
                                 "match", "ref_strategy"])
def test_a_tier_b_or_c_key_survives_a_tier_a_save(key):
    """The failure this exists to prevent: an editor that quietly strips the
    keys it does not render. Komet would lose its parse template and Neodent
    its backfill refusal, and the save would report success either way."""
    before = _body()
    after = tier_a_from_form(before, _form(domains="new.example"))
    assert after[key] == before[key]


def test_the_body_passed_in_is_not_mutated():
    """Callers hold the pre-edit body to compare against; mutating it in place
    would make the optimistic-lock comparison read as no change."""
    before = _body()
    tier_a_from_form(before, _form(domains="new.example"))
    assert before["domains"] == ["old.example"]


# --------------------------------------------------------------------------- #
# doc_sources: portals are Tier A, direct sources are not

def test_a_direct_source_is_carried_through_untouched():
    """`direct` is a FETCH TARGET -- the playbook rung enqueues `fetch.url` for
    each one -- so it is Tier B and the form never renders it as editable.
    Rebuilding the list from the portal rows alone would delete NSK's four
    verified certificate URLs and read on screen as a successful edit."""
    after = tier_a_from_form(_body(), _form(
        doc_source_count="1",
        **{"doc_source.0.doc_type": "DoC",
           "doc_source.0.url": "https://portal/changed",
           "doc_source.0.note": "moved"}))

    kinds = [s["kind"] for s in after["doc_sources"]]
    assert kinds == ["portal", "direct"]
    assert after["doc_sources"][1] == _body()["doc_sources"][1]


def test_clearing_every_portal_still_keeps_the_direct_sources():
    after = tier_a_from_form(_body(), _form())
    assert after["doc_sources"] == [_body()["doc_sources"][1]]


def test_a_cleared_url_removes_that_row():
    """How a portal is deleted: there is no separate delete button, and a row
    with no URL is not a source."""
    after = tier_a_from_form(_body(), _form(
        doc_source_count="1",
        **{"doc_source.0.doc_type": "DoC", "doc_source.0.url": "   ",
           "doc_source.0.note": "orphan"}))
    assert [s["kind"] for s in after["doc_sources"]] == ["direct"]


def test_an_added_portal_omits_note_rather_than_storing_an_empty_one():
    after = tier_a_from_form(_body(), _form(
        doc_source_count="1",
        **{"doc_source.0.doc_type": "IFU",
           "doc_source.0.url": "https://portal/ifu",
           "doc_source.0.note": ""}))
    assert after["doc_sources"][0] == {"doc_type": "IFU", "kind": "portal",
                                       "url": "https://portal/ifu"}


# --------------------------------------------------------------------------- #
# the line-based fields

def test_blank_lines_are_not_values():
    after = tier_a_from_form(_body(), _form(
        domains="a.example\n\n  \nb.example\n"))
    assert after["domains"] == ["a.example", "b.example"]


def test_commas_are_not_separators():
    """A `site:` path scope and a doc-type marker can both contain one, so the
    separator is the line break and only the line break."""
    after = tier_a_from_form(_body(), _form(domains="straumann.com/medentika"))
    assert after["domains"] == ["straumann.com/medentika"]


@pytest.mark.parametrize("key", ["domains", "date_labels", "source_priority"])
def test_clearing_a_field_drops_the_key_rather_than_storing_it_empty(key):
    """`{}` and `[]` are not the same as absent to `_parse`'s defaults, and an
    empty key in a body is noise in every diff of it afterwards."""
    populated = tier_a_from_form(_body(), _form(
        domains="a.example", **{"date_labels.from": "Rev. Stand"},
        _multi={"source_priority": ["playbook"]}))
    assert key in populated

    cleared = tier_a_from_form(populated, _form())
    assert key not in cleared


def test_only_non_empty_lexicon_keys_are_kept():
    after = tier_a_from_form(_body(), _form(
        **{"date_labels.from": "Rev. Stand\nRevisionsstand",
           "date_labels.to": "",
           "type_markers.DoC": "Konformitätserklärung"}))
    assert after["date_labels"] == {"from": ["Rev. Stand", "Revisionsstand"]}
    assert after["type_markers"] == {"DoC": ["Konformitätserklärung"]}


def test_source_priority_comes_from_the_checkbox_group():
    after = tier_a_from_form(_body(), _form(
        _multi={"source_priority": ["playbook", "search", "manual"]}))
    assert after["source_priority"] == ["playbook", "search", "manual"]
    assert set(after["source_priority"]) <= DISCOVER_RUNGS


# --------------------------------------------------------------------------- #
# the site: preview

def test_the_preview_is_the_query_discover_would_build():
    """The one affordance that lets a non-dev reason about `domains`, whose
    effect is otherwise invisible. It must agree with `_query_for`, not merely
    look like it -- so this asserts against that function's own join."""
    from app.handlers.discover import _query_for
    from app.playbooks import Playbook

    domains = ("voco.dental", "straumann.com/medentika")
    pb = Playbook(slug="x", manufacturer="X", domains=domains)
    facts = type("F", (), {"manufacturer": "X", "label": None})()

    assert site_query_preview(list(domains)) in _query_for(facts, pb)


def test_no_domains_says_the_search_runs_unrestricted():
    """Not an empty string: a blank preview reads as "nothing happens", when
    what actually happens is a search across the whole web."""
    assert "unrestricted" in site_query_preview([])


# --------------------------------------------------------------------------- #
# contacts (2026-09-03)
# --------------------------------------------------------------------------- #
# `contacts` shipped into the playbook schema, the loader, the validator and the
# seed the same day -- and NOT into this form, which is the only place a person
# edits a playbook. The addresses were therefore authorable in a file and
# uneditable in the UI. Tier A because the blast radius is a renewal request
# going to the wrong mailbox, which a person notices and can revert.

def test_contacts_are_folded_in_like_domains():
    after = tier_a_from_form(_body(), _form(contacts="info@voco.de\ndealersupport@voco.de"))
    assert after["contacts"] == ["info@voco.de", "dealersupport@voco.de"]


def test_contacts_are_lowercased_and_deduplicated():
    after = tier_a_from_form(_body(), _form(contacts=" INFO@Voco.de \ninfo@voco.de\n"))
    assert after["contacts"] == ["info@voco.de"]


def test_clearing_the_field_removes_the_key():
    body = dict(_body(), contacts=["info@voco.de"])
    assert "contacts" not in tier_a_from_form(body, _form(contacts=""))


def test_a_personal_address_is_refused_not_saved():
    # The same rule the playbook loader enforces, at the other door. Without it
    # the file refuses `a.novak@` and the form accepts it, and the two
    # authoring paths disagree about what a playbook may contain.
    with pytest.raises(ValueError, match="names an individual"):
        tier_a_from_form(_body(), _form(contacts="a.novak@voco.de"))


def test_a_malformed_address_is_refused():
    with pytest.raises(ValueError, match="not an e-mail address"):
        tier_a_from_form(_body(), _form(contacts="not-an-address"))


def test_contacts_absent_from_the_form_leaves_an_existing_value_alone():
    # A form that never rendered the field must not delete the key -- the same
    # additive rule the Tier B and C keys rely on.
    body = dict(_body(), contacts=["info@voco.de"])
    assert tier_a_from_form(body, _form())["contacts"] == ["info@voco.de"]
