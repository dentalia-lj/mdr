"""Tier ladder: per-field escalation, merge, calibration (G2), and the
extraction_attempt write. LLM tiers are injected (stubbed here — no live calls)."""

from __future__ import annotations

import pytest

from app.extract import pdf, t0_templates, tiers

ISO_CERT = "IVOCLAR/Ivoclar ISO certifikat do 30_10_2027.pdf"


def ev(value, conf, tier="T1"):
    return {"value": value, "conf": conf, "tier": tier, "verbatim": str(value), "page": None}


class StubLlm:
    """Injected LLM tier: records calls, returns canned per-field evidence."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def extract(self, tier, doc, filename, missing):
        self.calls.append((tier, tuple(missing)))
        return {f: self.responses[tier][f] for f in missing if f in self.responses.get(tier, {})}


# --------------------------------------------------------------------------- #
# escalation + merge (pure)
# --------------------------------------------------------------------------- #
def test_needs_escalation_flags_absent_and_low_confidence():
    fields = {"type": ev("DoC", 1.0, "T0"), "regulation": ev("MDR", 0.5, "T0")}
    missing = tiers.needs_escalation(fields, threshold=0.95)
    assert "regulation" in missing  # present but low
    assert "validity_to" in missing  # absent
    assert "type" not in missing  # present and high


def test_an_ifu_does_not_chase_a_certificate_it_cannot_have():
    # Both STRAUMANN IFUs invented a date rather than returning none, 2026-08-17:
    # "2025" off "(c) Institut Straumann AG, 2025. All rights reserved." and
    # "2026-04-26" off the revision code "701593/M/12 04/26". The second landed
    # in the registry at 0.97 as doc 326 and no downstream guard could see it,
    # which is why the fix is to stop asking rather than to check the answer.
    ifu = tiers.needs_escalation({"type": ev("IFU", 1.0, "T0")})
    assert "validity_from" not in ifu
    assert "validity_to" not in ifu
    assert "cert_number" not in ifu
    # ...but identity and the item ids are still pursued: an IFU names its
    # manufacturer and may enumerate the family it accompanies, and that is the
    # only route by which it reaches an item at all.
    assert "manufacturer" in ifu
    assert "ref_list" in ifu and "basic_udi_di" in ifu


def test_a_document_only_maybe_an_ifu_still_chases_its_dates():
    # The conservative direction, mirroring "unknown scope still pursues an id".
    # Suppressing on a shaky type would silently drop a real expiry from a
    # declaration the classifier merely guessed wrong about -- far worse than
    # paying for one tier.
    maybe = tiers.needs_escalation({"type": ev("IFU", 0.6, "T0")})
    assert "validity_from" in maybe and "cert_number" in maybe


def test_a_declaration_is_untouched_by_the_ifu_rule():
    # The mutation guard: dropping the type condition would strip validity and
    # certificate chasing from every document in the corpus.
    doc = tiers.needs_escalation({"type": ev("DoC", 1.0, "T0")})
    assert "validity_from" in doc and "validity_to" in doc and "cert_number" in doc


def test_escalation_pursues_item_ids_except_manufacturer_certs():
    # validity_from JOINED the always-escalate set 2026-08-13 (Denis's ruling):
    # under the old rule ("the start of the validity PERIOD, not the issue
    # date") most declarations genuinely had no value and the tier was paid to
    # find nothing -- S0.4's biggest false-escalation driver. Under the new rule
    # the issue/signing date IS the start, so it is present nearly always. T0
    # answers a place-and-date signature line at 0.95, which clears the
    # threshold, so a signed declaration still does not escalate for it.
    assert "validity_from" in tiers.needs_escalation({})
    assert "validity_from" not in tiers.needs_escalation(
        {"validity_from": ev("2026-02-12", 0.95, "T0")})
    # item / group / unknown scope MUST yield an item id -> ref_list + UDI pursued.
    grp = tiers.needs_escalation({"coverage_scope": ev("group", 1.0, "T0")})
    assert "ref_list" in grp and "basic_udi_di" in grp
    # a manufacturer / catalogue-wide cert (ISO 27001/13485) has no item id by nature
    # -> not chased (it routes to the binding flow). No wasted vision escalation.
    # `manufacturer` joined the always-escalate set 2026-08-13; filled here so
    # this test keeps asserting what it was written for -- that the ITEM-ID
    # fields are not chased on a QMS cert -- rather than the tenth field.
    mfr = {f: ev("x", 1.0, "T0") for f in
           ("type", "regulation", "validity_from", "validity_to", "cert_number",
            "manufacturer")}
    mfr["coverage_scope"] = ev("manufacturer", 1.0, "T0")
    assert tiers.needs_escalation(mfr) == []


def test_optional_absent_fields_do_not_reach_the_llm(monkeypatch):
    # a compliance doc whose escalate-fields T0 all satisfies makes no LLM call,
    # even with validity_from/ref_list absent (they no longer force escalation).
    filled = {f: ev("x", 1.0, "T0") for f in tiers.ESCALATE}
    monkeypatch.setattr(tiers, "t0_extract",
                        lambda doc, filename="", result=None, companion_url=None: ("compliance-doc", filled))
    monkeypatch.setattr(tiers.pdfutil, "is_scan", lambda doc: False)
    stub = StubLlm({})
    _, _, used = tiers.run_extraction(None, "x.pdf", llm=stub, threshold=0.95)
    assert used == ["T0"] and stub.calls == []


def test_merge_new_tier_wins_for_returned_fields_only():
    base = {"type": ev("DoC", 1.0, "T0"), "regulation": ev("MDD", 0.4, "T0")}
    new = {"regulation": ev("MDR", 0.97, "T1")}
    merged = tiers.merge(base, new)
    assert merged["regulation"]["value"] == "MDR"
    assert merged["regulation"]["tier"] == "T1"
    assert merged["type"]["tier"] == "T0"  # untouched


# --------------------------------------------------------------------------- #
# merge is non-destructive: escalation fills gaps, it never erases.
#
# T0/pdfplumber is the full-list REF enumerator and the LLM is the fallback, but
# extract_ref_list stamps a flat 0.9 that can never clear the 0.95 threshold, so
# a *successful* T0 ref_list escalates every time. Measured on the GC corpus, a
# vision tier that reads the page-2 "Artikelliste: Gemäß Anhang" field answers
# `[]` at conf 0.2-0.3 — and an unconditional `{**base, **new}` let that empty
# answer delete 26 of 61 correctly-parsed REF lists.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("blank", [[], "", None, {}])
def test_merge_empty_later_value_never_erases_an_earlier_hit(blank):
    base = {"ref_list": ev(["003477", "003478"], 0.9, "T0")}
    merged = tiers.merge(base, {"ref_list": ev(blank, 0.3, "T2")})
    assert merged["ref_list"]["value"] == ["003477", "003478"]
    assert merged["ref_list"]["tier"] == "T0"


def test_merge_empty_later_value_still_fills_an_absent_field():
    # The rule protects existing hits; it must not block a genuine "no value"
    # answer from landing where nothing was known before.
    merged = tiers.merge({}, {"ref_list": ev([], 0.3, "T2")})
    assert merged["ref_list"]["value"] == []
    assert merged["ref_list"]["tier"] == "T2"


def test_merge_empty_later_value_replaces_an_earlier_empty_one():
    merged = tiers.merge({"ref_list": ev([], 1.0, "T0")}, {"ref_list": ev([], 0.3, "T2")})
    assert merged["ref_list"]["tier"] == "T2"


def test_merge_non_empty_later_value_still_wins_over_an_earlier_hit():
    # Deliberately NOT confidence-aware: a later tier that actually found
    # something different is still authoritative (19 of 61 GC documents).
    base = {"ref_list": ev(["003477"], 0.9, "T0")}
    merged = tiers.merge(base, {"ref_list": ev(["900681", "900685"], 0.3, "T2")})
    assert merged["ref_list"]["value"] == ["900681", "900685"]
    assert merged["ref_list"]["tier"] == "T2"


def test_merge_zero_is_a_value_not_a_blank():
    merged = tiers.merge({"pages": ev(7, 1.0, "T0")}, {"pages": ev(0, 0.3, "T2")})
    assert merged["pages"]["value"] == 0  # falsy but real — 0 is not "no answer"


# --------------------------------------------------------------------------- #
# integrity flags — a REF list that came back at exactly the prompt cap
# --------------------------------------------------------------------------- #
# The prompt caps `ref_list` at REF_LIST_PROMPT_CAP codes, chosen on the measured
# corpus distribution (median 4; top ten 53/56/67/67/84/130/133/150/150/1121).
# One GC document holds 1.121 codes and no prompt cap reaches it, so when T0
# fails on a document of that shape the stored list is silently partial. The
# model's own count claim is NOT usable as the signal -- document 258 reported
# "total of 44 codes listed, first 40 returned" while returning 46 against a true
# 53. Length == cap is deterministic and needs no self-report.
def _ref_fields(n: int, tier: str) -> dict:
    return {"ref_list": {"value": [f"R{i}" for i in range(n)], "conf": 0.9,
                         "tier": tier, "verbatim": f"{n} REF codes", "page": 1}}


def test_a_capped_llm_ref_list_is_flagged_as_possibly_truncated():
    cap = tiers.REF_LIST_PROMPT_CAP
    assert "ref-list-possibly-truncated" in tiers.integrity_flags(_ref_fields(cap, "T1"))
    assert "ref-list-possibly-truncated" in tiers.integrity_flags(_ref_fields(cap, "T2"))


@pytest.mark.parametrize("delta", [-1, 1])
def test_only_exactly_the_cap_is_a_truncation_suspect(delta):
    # A list one short of the cap ran out of codes; one over it never obeyed the
    # cap in the first place. Neither is evidence the prompt truncated anything.
    fields = _ref_fields(tiers.REF_LIST_PROMPT_CAP + delta, "T1")
    assert tiers.integrity_flags(fields) == []


def test_a_t0_list_at_the_cap_is_not_flagged():
    # T0 enumerates the document deterministically and is not subject to the
    # prompt cap: 150 codes from pdfplumber means the document has 150 codes.
    fields = _ref_fields(tiers.REF_LIST_PROMPT_CAP, "T0")
    fields["ref_list"]["conf"] = 1.0
    assert tiers.integrity_flags(fields) == []


def test_an_absent_or_scalar_ref_list_raises_nothing():
    assert tiers.integrity_flags({}) == []
    assert tiers.integrity_flags({"ref_list": {"value": None, "tier": "T1"}}) == []


def test_the_prompt_cap_is_read_from_one_place():
    """The number the tiers are ASKED for and the number this module tests
    against must be the same number, or the flag fires on the wrong length."""
    import re

    from app.extract import llm as llm_mod

    for text in (llm_mod.T1_SYSTEM, llm_mod.T2_SYSTEM):
        stated = int(re.search(r"\*\*at most (\d+)\*\*", text).group(1))
        assert stated == tiers.REF_LIST_PROMPT_CAP


def test_the_truncation_flag_never_shortens_the_stored_list():
    """Denis, 2026-08-13: the guard reports, it does not prune.

    A suspected truncation is context for a reviewer. It must never discard,
    shorten or reject a stored `ref_list` -- the shrink guard still wins, and
    the flag is computed from what was stored, not the other way round."""
    long_t0 = _ref_fields(1121, "T0")
    capped_t1 = _ref_fields(tiers.REF_LIST_PROMPT_CAP, "T1")

    merged = tiers.merge(long_t0, capped_t1)

    assert len(merged["ref_list"]["value"]) == 1121   # shrink guard still wins
    assert merged["ref_list"]["tier"] == "T0"
    assert merged["ref_list"]["value"] == long_t0["ref_list"]["value"]
    # and the flag on the capped answer changed nothing about that answer either
    assert tiers.integrity_flags(capped_t1) == ["ref-list-possibly-truncated"]
    assert len(capped_t1["ref_list"]["value"]) == tiers.REF_LIST_PROMPT_CAP


def test_run_extraction_keeps_the_t0_ref_list_when_the_llm_returns_none(fixture_pdf):
    """End-to-end on the committed GC fixture whose 8-language article table is
    embedded on page 5 — the exact layout that lost its REFs in the corpus run."""
    path = fixture_pdf("GC/everX_Posterior_12022026.pdf")
    doc = pdf.open_doc(path)
    empty_ref = {"ref_list": ev([], 0.3, "T2")}
    stub = StubLlm({"T1": empty_ref, "T2": empty_ref})
    _, fields, used = tiers.run_extraction(doc, path, llm=stub, threshold=0.95)
    assert "T2" in used  # the escalation really happened
    assert fields["ref_list"]["tier"] == "T0"
    assert len(fields["ref_list"]["value"]) == 6
    assert "005117" in fields["ref_list"]["value"]


# --------------------------------------------------------------------------- #
# calibration mechanism (G2) — raw x factor, clamped, default identity
# --------------------------------------------------------------------------- #
def test_calibrate_uses_field_factor():
    cmap = {"claude-haiku-4-5": {"validity_from": 0.8, "*": 0.9}}
    assert tiers.calibrate(1.0, "claude-haiku-4-5", "validity_from", cmap) == 0.8


def test_calibrate_falls_back_to_star_then_identity():
    cmap = {"claude-haiku-4-5": {"*": 0.9}}
    assert tiers.calibrate(0.9, "claude-haiku-4-5", "type", cmap) == pytest.approx(0.81)
    assert tiers.calibrate(1.0, "other-model", "type", cmap) == 1.0  # identity default


def test_calibrate_clamps_to_unit_interval():
    cmap = {"m": {"type": 1.5}}
    assert tiers.calibrate(0.9, "m", "type", cmap) == 1.0  # 1.35 -> clamp


def test_t0_confidence_is_uncalibrated():
    # T0 hits have model_id None -> identity factor regardless of map.
    cmap = {"claude-haiku-4-5": {"type": 0.5}}
    assert tiers.calibrate(1.0, None, "type", cmap) == 1.0


# --------------------------------------------------------------------------- #
# run_extraction (T0 -> T1 -> T2) with an injected LLM
# --------------------------------------------------------------------------- #
def test_business_doc_short_circuits_before_llm(monkeypatch):
    monkeypatch.setattr(tiers, "t0_extract",
                        lambda doc, filename="", result=None, companion_url=None: ("business-doc", {}))
    stub = StubLlm({})
    doc_class, fields, used = tiers.run_extraction(None, "x.pdf", llm=stub, threshold=0.95)
    assert doc_class == "business-doc"
    assert stub.calls == []  # no LLM for non-compliance docs
    assert used == ["T0"]


def test_run_extraction_escalates_missing_fields_to_t1(fixture_pdf):
    path = fixture_pdf(ISO_CERT)
    doc = pdf.open_doc(path)
    fill = {f: ev("filled", 0.97, "T1") for f in
            ["validity_from", "validity_to", "cert_number", "basic_udi_di",
             "referenced_docs", "regulation", "ref_list", "coverage_scope", "type"]}
    stub = StubLlm({"T1": fill})
    doc_class, fields, used = tiers.run_extraction(doc, path, llm=stub, threshold=0.95)
    assert doc_class == "compliance-doc"
    assert "T1" in used
    assert stub.calls and stub.calls[0][0] == "T1"
    assert fields["type"]["tier"] == "T0"  # T0's high-confidence hit is preserved


def test_scanned_doc_forces_t2(fixture_pdf):
    path = fixture_pdf("IVOCLAR/MDR Certificate IV AG 2017_745.pdf")  # a scan
    doc = pdf.open_doc(path)
    stub = StubLlm({"T1": {}, "T2": {}})
    _, _, used = tiers.run_extraction(doc, path, llm=stub, threshold=0.95)
    assert "T2" in used  # is_scan -> T2 regardless of T1


# --------------------------------------------------------------------------- #
# extraction_attempt persistence (real Postgres)
# --------------------------------------------------------------------------- #
def test_write_extraction_attempt_roundtrip(conn):
    fields = {"type": ev("DoC", 1.0, "T0"), "regulation": ev("MDR", 0.97, "T1")}
    aid = tiers.write_extraction_attempt(conn, "hash123", ["T0", "T1"], fields, extract_rev=1)
    conn.commit()
    row = conn.execute(
        "SELECT content_hash, tier, fields, extract_rev FROM extraction_attempt WHERE id=%s",
        (aid,),
    ).fetchone()
    assert row["content_hash"] == "hash123"
    assert row["tier"] == "T0+T1"
    assert row["fields"]["regulation"]["value"] == "MDR"
    assert row["extract_rev"] == 1


def test_next_extract_rev_increments(conn):
    assert tiers.next_extract_rev(conn, "h") == 1
    tiers.write_extraction_attempt(conn, "h", ["T0"], {}, extract_rev=1)
    conn.commit()
    assert tiers.next_extract_rev(conn, "h") == 2


def test_a_deduced_coverage_scope_no_longer_escalates():
    # The whole point of the deduction: coverage_scope stops dragging documents
    # into a vision call that then overwrites it with a guess. `type` here is
    # settled at T0, so the scope rides on a premise the ladder will not revise.
    fields = {
        "type": {"value": "DoC", "conf": 1.0, "tier": "T0"},
        "regulation": {"value": "MDR", "conf": 1.0, "tier": "T0"},
        "coverage_scope": t0_templates.derive_coverage_scope("DoC", True, type_conf=1.0),
        "ref_list": {"value": ["1055"], "conf": 1.0, "tier": "T0"},
        "basic_udi_di": {"value": "++J022", "conf": 1.0, "tier": "T0"},
        "validity_to": {"value": None, "conf": 1.0, "tier": "T0"},
        "cert_number": {"value": "X", "conf": 1.0, "tier": "T0"},
    }

    assert "coverage_scope" not in tiers.needs_escalation(fields)


def test_a_filename_typed_document_still_escalates_its_coverage_scope():
    # `type` is unsettled, so the scope deduced from it must escalate too: if
    # T1 returns EC or ISO, the scope has to be re-decided as manufacturer.
    fields = {
        "type": {"value": "DoC", "conf": t0_templates.FILENAME_CONF, "tier": "T0"},
        "regulation": {"value": "MDR", "conf": 1.0, "tier": "T0"},
        "coverage_scope": t0_templates.derive_coverage_scope(
            "DoC", True, type_conf=t0_templates.FILENAME_CONF),
        "ref_list": {"value": ["1055"], "conf": 1.0, "tier": "T0"},
        "basic_udi_di": {"value": "++J022", "conf": 1.0, "tier": "T0"},
        "validity_to": {"value": None, "conf": 1.0, "tier": "T0"},
        "cert_number": {"value": "X", "conf": 1.0, "tier": "T0"},
    }

    escalating = tiers.needs_escalation(fields)
    assert "coverage_scope" in escalating and "type" in escalating


# --------------------------------------------------------------------------- #
# A capped sample must not replace a deterministic enumeration.
#
# The prompt caps `ref_list` at N codes and says so explicitly: "a deterministic
# parser enumerates the full list -- you only need enough to identify the
# coverage" (2026-07-07, a max_tokens guard). That premise was never
# enforced. `extract_ref_list` stamps a flat 0.9 against a 0.95 threshold, so a
# perfectly parsed table escalated on EVERY document, and merge's "a different,
# non-empty later value still wins" then stored the sample over the enumeration.
# Measured on the GC corpus 2026-08-13: 1.502 codes stored where T0's
# stitched-table reads 2.843, and the missing codes reach 199 more catalogue
# items. Document 258 stored 46 where T0 reads 53.
# --------------------------------------------------------------------------- #

def test_a_shorter_later_ref_list_never_replaces_a_longer_one():
    base = {"ref_list": ev(["001", "002", "003", "004"], 0.9, "T0")}
    new = {"ref_list": ev(["001", "002"], 0.95, "T1")}
    merged = tiers.merge(base, new)
    assert merged["ref_list"]["value"] == ["001", "002", "003", "004"]
    assert merged["ref_list"]["tier"] == "T0"


def test_a_longer_later_ref_list_still_wins():
    # The KOMET case: T0's text-column parser under-reads and the LLM sees more.
    # Under-capture is the failure mode of BOTH tiers, so the fuller answer wins.
    base = {"ref_list": ev(["001"], 0.9, "T0")}
    new = {"ref_list": ev(["001", "002", "003"], 0.95, "T1")}
    merged = tiers.merge(base, new)
    assert merged["ref_list"]["value"] == ["001", "002", "003"]
    assert merged["ref_list"]["tier"] == "T1"


def test_the_shrink_guard_is_scoped_to_enumerated_fields():
    # A later tier correcting a scalar is the ordinary, wanted behaviour and
    # must not be caught by a rule written for list enumeration.
    base = {"type": ev("other", 0.5, "T0")}
    new = {"type": ev("DoC", 0.97, "T1")}
    assert tiers.merge(base, new)["type"]["value"] == "DoC"


def test_an_equal_length_later_ref_list_still_wins():
    # Same size, different codes: no evidence either is fuller, so the ordinary
    # later-tier-wins rule stands rather than pinning the earlier one forever.
    base = {"ref_list": ev(["001", "002"], 0.9, "T0")}
    new = {"ref_list": ev(["003", "004"], 0.95, "T1")}
    assert tiers.merge(base, new)["ref_list"]["value"] == ["003", "004"]


# --------------------------------------------------------------------------- #
# `manufacturer` becomes the tenth target field (ext-manufacturer Phase 2).
#
# Nothing produced a manufacturer for a corpus document, so `validate.py`'s
# scoped path could never fire: 0 of 159 extraction attempts carried the key and
# every backfill document fell to the capped unscoped branch. Measured
# 2026-08-13, that leaves 51 documents linking at `ref-catalogue` (barred from
# production) and 3 blocked entirely by `multi-manufacturer-ref`, one of them
# document 235, where a single mis-attributed catalogue row hides 202 items.
#
# It escalates ALWAYS: identity is a classification, and a document naming no
# manufacturer we can read is exactly the case worth paying a tier for. It costs
# no extra call in practice -- T1 already ran on 60 of 61 GC documents because
# coverage_scope trips escalation on its own.
# --------------------------------------------------------------------------- #

def test_manufacturer_is_a_target_field():
    assert "manufacturer" in tiers.TARGET


def test_manufacturer_escalates_when_absent_or_unsure():
    assert "manufacturer" in tiers.needs_escalation({})
    assert "manufacturer" in tiers.needs_escalation(
        {"manufacturer": ev("GC EUROPE N.V.", 0.5, "T0")})
    assert "manufacturer" not in tiers.needs_escalation(
        {"manufacturer": ev("GC EUROPE N.V.", 0.99, "T0")})


def test_manufacturer_is_pursued_even_on_a_manufacturer_scope_cert():
    """The opposite of the item-id fields. A QMS/ISO certificate carries no REF
    list by nature, which is why `_ESCALATE_ITEM_ID` is suppressed for it -- but
    identity is the ONE thing such a document does assert, and the C4 binding
    flow needs it. Suppressing it here would keep the mfr-binding route
    unactionable, which is the state that made every binding task read
    'manufacturer unknown'."""
    fields = {"coverage_scope": ev("manufacturer", 0.99, "T0")}
    missing = tiers.needs_escalation(fields)
    assert "manufacturer" in missing
    assert "ref_list" not in missing        # unchanged: no item ids on a QMS cert


# --------------------------------------------------------------------------- #
# stated_class: a target that must never buy an LLM call
# --------------------------------------------------------------------------- #
def test_stated_class_is_a_target_that_never_escalates():
    """ABSENCE from the escalate sets IS the mechanism -- the `referenced_docs`
    precedent. Adding `stated_class` to one of them would turn an opportunistic
    T0 read into a per-document T1/T2 call across the whole corpus, which is the
    one thing this field was approved on condition of never doing."""
    assert "stated_class" in tiers.TARGET
    assert "stated_class" not in tiers._ESCALATE_ALWAYS
    assert "stated_class" not in tiers._ESCALATE_ITEM_ID
    assert "stated_class" not in tiers._ESCALATE_CERTIFICATE
    assert "stated_class" not in tiers.ESCALATE


def test_a_missing_stated_class_alone_asks_for_nothing():
    """The behavioural half of the test above: a document answering every other
    target confidently and stating no class must escalate NOTHING."""
    fields = {f: ev("x", 1.0, "T0") for f in tiers.TARGET if f != "stated_class"}
    assert tiers.needs_escalation(fields) == []


# --------------------------------------------------------------------------- #
# `type=ISO` without an ISO standard number
#
# Doc 967 is an MDR Annex IX quality-management certificate. T1 typed it `ISO`
# because the prompt's own preamble equates ISO with "Quality Management System
# certificates" -- the exact conflation removed from T0's `_TYPE_MARKERS` on
# 2026-08-20, after doc 420 reached `production` as ISO, and never removed from
# the prompt.
#
# The naive cross-check (`type=ISO` together with a regulation) does NOT work: a
# genuine ISO 13485 certificate may cite MDR in its scope, and `regulation` is
# defined as "cites anywhere". This rule instead extends the 2026-08-20 ruling
# -- `iso 13485` is the ONLY ISO signal -- to whatever tier produced the type.
#
# Design: docs/superpowers/specs/2026-09-03-label-adjacency-and-type-conflation-design.md
# --------------------------------------------------------------------------- #

def _typed(value, *, verbatims=()):
    fields = {"type": {"value": value, "conf": 0.95, "tier": "T1",
                       "verbatim": "", "page": 1}}
    for n, v in enumerate(verbatims):
        fields[f"f{n}"] = {"value": "x", "conf": 1.0, "tier": "T1",
                           "verbatim": v, "page": 1}
    return fields


def test_iso_without_a_standard_number_is_flagged():
    """Doc 967's shape: typed ISO, but nothing in the evidence names ISO 13485
    or ISO 9001. A real ISO certificate always names its standard."""
    flags = tiers.integrity_flags(_typed(
        "ISO", verbatims=("EU Quality Management Certificate", "(EU) 2017/745")))
    assert tiers.ISO_WITHOUT_STANDARD_FLAG in flags


def test_iso_naming_its_standard_is_not_flagged():
    """Doc 966: a genuine DQS ISO 13485 certificate."""
    flags = tiers.integrity_flags(_typed(
        "ISO", verbatims=("Certificate registration no. 549934", "ISO 13485")))
    assert tiers.ISO_WITHOUT_STANDARD_FLAG not in flags


def test_iso_9001_also_counts_as_a_standard_number():
    flags = tiers.integrity_flags(_typed("ISO", verbatims=("ISO 9001:2015",)))
    assert tiers.ISO_WITHOUT_STANDARD_FLAG not in flags


def test_an_iso_certificate_citing_mdr_in_its_scope_is_not_flagged():
    """The edge case that kills the naive `type=ISO + regulation=MDR` rule: a
    real ISO 13485 certificate may name MDR in its scope, and flagging it would
    be a false positive on a correct document."""
    flags = tiers.integrity_flags(_typed("ISO", verbatims=(
        "ISO 13485:2016", "quality system for devices under Regulation (EU) 2017/745")))
    assert tiers.ISO_WITHOUT_STANDARD_FLAG not in flags


def test_a_non_iso_type_is_never_flagged():
    for t in ("DoC", "EC", "IFU", "SPP", "other"):
        assert tiers.ISO_WITHOUT_STANDARD_FLAG not in tiers.integrity_flags(_typed(t))


def test_the_adjacency_refusal_becomes_a_flag_and_an_absence_does_not():
    from app.extract import t0_templates as t0
    assert tiers.integrity_flags({t0.DATE_LABEL_REFUSED_KEY: ["validity_to"]}) == [
        tiers.DATE_LABEL_NOT_ADJACENT_FLAG]
    assert tiers.integrity_flags({t0.DATE_LABEL_REFUSED_KEY: []}) == []
    assert tiers.integrity_flags({}) == []


def test_the_refusal_survives_escalation_to_a_paid_tier():
    """`merge` copies `base` wholesale and only overwrites keys the later tier
    returned, so a T0-only marker reaches the stored fields VALIDATE reads. That
    is deliberate and is the interesting case: a document whose expiry T0 lost
    and T1 then read is exactly the one worth a `t0_layout` template, because
    the template would stop us paying for it."""
    from app.extract import t0_templates as t0
    base = {t0.DATE_LABEL_REFUSED_KEY: ["validity_to"]}
    merged = tiers.merge(base, {"validity_to": {"value": "2027-12-19", "conf": 0.95,
                                                "verbatim": "Expiry date 2027-12-19",
                                                "tier": "T1"}})
    assert merged[t0.DATE_LABEL_REFUSED_KEY] == ["validity_to"]
    assert tiers.DATE_LABEL_NOT_ADJACENT_FLAG in tiers.integrity_flags(merged)
