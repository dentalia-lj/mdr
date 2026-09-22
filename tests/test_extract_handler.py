"""extract.doc handler done-criteria (deterministic: T0 + stubbed T1/T2).

Proves the S0.2 spine end to end: one Ivoclar PDF -> extraction_attempt row with
per-field evidence -> validate.doc emitted with the C3 dedupe key. The live LLM
run is deferred until a key is present (S0.4)."""

from __future__ import annotations

import json

from app import queue
from app.extract import pdf as pdf_mod
from app.extract import tiers as tiers_mod
from app.handlers import extract as eh
from app.playbooks import Playbook

ISO_CERT = "IVOCLAR/Ivoclar ISO certifikat do 30_10_2027.pdf"


def _ev(value):
    return {"value": value, "conf": 0.97, "tier": "T1", "verbatim": str(value),
            "page": None, "model_id": "stub"}


class StubLlm:
    """Fills every escalated field so T2 is not reached on a text PDF."""

    def extract(self, tier, doc, filename, missing):
        return {f: _ev("filled") for f in missing}


def _batch_fields(tier, only=None, conf=0.97):
    fields = only if only is not None else tiers_mod.TARGET
    return {f: {"value": f"v_{f}", "conf": conf, "tier": tier,
                "verbatim": f"verb {f}", "page": 1, "model_id": "batchmodel"}
            for f in fields}


class StubBatchClient:
    """Simulates the Batch API across polls. batch_ref (in the DB) is the real
    state; this only maps batch_id -> tier and counts done() checks so a batch
    can report `in_progress` for `ready_after` polls before `ended`."""

    def __init__(self, results_by_tier, ready_after=0):
        self.results_by_tier = results_by_tier
        self.ready_after = ready_after
        self._batches = {}
        self._n = 0
        self.submits = []

    def build_requests(self, content_hash, tier, doc, missing):
        return [{"custom_id": f"{content_hash}:{tier}", "tier": tier}]

    def submit(self, requests):
        self._n += 1
        bid = f"batch-{self._n}"
        self._batches[bid] = {"tier": requests[0]["tier"], "checks": 0}
        self.submits.append(requests[0]["custom_id"])
        return bid

    def done(self, batch_id):
        b = self._batches[batch_id]
        b["checks"] += 1
        return b["checks"] > self.ready_after

    def results(self, batch_id):
        return dict(self.results_by_tier.get(self._batches[batch_id]["tier"], {}))


def _enqueue_extract(conn, content_hash, group_id, path):
    jid = queue.enqueue(
        conn, "extract.doc",
        {"archive_url": path, "content_hash": content_hash, "group_id": group_id},
        f"extract:{content_hash}",
    )
    conn.commit()
    return conn.execute("SELECT * FROM job WHERE id=%s", (jid,)).fetchone()


def _count(conn, sql, *args):
    return conn.execute(sql, args).fetchone()["c"]


def test_extract_doc_end_to_end(conn, fixture_pdf):
    path = fixture_pdf(ISO_CERT)
    job = {"payload": {"archive_url": path, "content_hash": "sha-iso-1", "group_id": 42}}

    result = eh.handle_extract_doc(conn, job, llm=StubLlm())
    conn.commit()

    assert result["tiers_used"] == ["T0", "T1"]  # text PDF, no vision tier

    row = conn.execute(
        "SELECT tier, fields FROM extraction_attempt WHERE content_hash='sha-iso-1'"
    ).fetchone()
    assert row is not None
    assert row["tier"] == "T0+T1"
    # deterministic T0 hit preserved through the ladder
    assert row["fields"]["type"]["value"] == "ISO"
    assert row["fields"]["type"]["tier"] == "T0"
    assert row["fields"]["coverage_scope"]["value"] == "manufacturer"

    v = conn.execute(
        "SELECT payload, dedupe_key FROM job WHERE type='validate.doc'"
    ).fetchone()
    assert v["payload"]["content_hash"] == "sha-iso-1"
    assert v["payload"]["extract_rev"] == 1
    assert v["dedupe_key"] == "validate:sha-iso-1:1:42"


def test_extract_opens_the_archived_pdf_once(conn, fixture_pdf, monkeypatch):
    """One execution, one open. The sidecar text capture and the tier ladder
    both need the same document, and it is the same immutable archived file for
    both -- opening and parsing it twice is a second full PyMuPDF parse bought
    for nothing on every extraction in the corpus."""
    path = fixture_pdf(ISO_CERT)
    job = {"payload": {"archive_url": path, "content_hash": "sha-open-1", "group_id": 1}}

    real_open = pdf_mod.open_doc
    opened = []

    def counting_open(archive_url):
        opened.append(archive_url)
        return real_open(archive_url)

    monkeypatch.setattr(pdf_mod, "open_doc", counting_open)
    eh.handle_extract_doc(conn, job, llm=StubLlm())
    conn.commit()

    assert opened == [path]


SCAN_CERT = {"manu": "IVOCLAR", "name": "MDR Certificate IV AG 2017_745.pdf"}  # is_scan: True


def test_extract_doc_scan_raises_no_text_layer_not_unreadable(conn, fixture_pdf):
    # A genuine scan (document_text row IS written, source='none') must be
    # reported as no_text_layer, never as unreadable_pdf -- the ambiguity
    # text_store.store_text()'s return value alone cannot resolve (Task 3).
    path = fixture_pdf(SCAN_CERT)
    job = {"payload": {"archive_url": path, "content_hash": "sha-scan-1", "group_id": 11}}

    result = eh.handle_extract_doc(conn, job, llm=StubLlm())
    conn.commit()

    assert {"kind": "no_text_layer", "subject": "sha-scan-1", "catalogue": None,
            "detail": {}} in result["anomalies"]
    assert "unreadable_pdf" not in result.get("counts", {})
    row = conn.execute(
        "SELECT source FROM document_text WHERE content_hash='sha-scan-1'"
    ).fetchone()
    assert row["source"] == "none"  # the row exists -- this is the discriminator


def test_extract_doc_unreadable_pdf_counts_and_skips_no_text_layer(conn):
    # An unopenable path (store_text catches the open failure and writes no
    # document_text row at all) must be counted as unreadable_pdf and must NOT
    # raise a no_text_layer anomaly -- the opposite half of the same ambiguity.
    job = {"payload": {"archive_url": "/nonexistent/does-not-exist.pdf",
                        "content_hash": "sha-unreadable-1", "group_id": 1}}

    result = eh.handle_extract_doc(conn, job, llm=StubLlm())
    conn.commit()

    assert result["counts"]["unreadable_pdf"] == 1
    assert result.get("anomalies", []) == []
    assert conn.execute(
        "SELECT 1 FROM document_text WHERE content_hash='sha-unreadable-1'"
    ).fetchone() is None  # the absent row is the discriminator
    assert _count(conn, "SELECT count(*) c FROM job WHERE type='validate.doc'") == 0


def test_a_broken_t0_template_surfaces_on_the_job_result(conn, fixture_pdf, monkeypatch):
    """A playbook template that stops matching is a regression in our own
    parsing. It must reach the job result (and so /data-quality) as a count,
    not be absorbed by the silent fall-through to the paid tiers."""
    from app.extract import t0_layout

    path = fixture_pdf("GC/everX_Posterior_12022026.pdf")
    monkeypatch.setattr(t0_layout, "ref_from_stitched_tables", lambda p: ([], None))
    job = {"payload": {"archive_url": path, "content_hash": "sha-tmpl-1", "group_id": 3}}

    result = eh.handle_extract_doc(conn, job, llm=StubLlm())
    conn.commit()

    kinds = [a["kind"] for a in result.get("anomalies", [])]
    assert "t0_ref_template_miss" in kinds
    # extraction still completed: an anomaly is a count, never an exception
    assert result["tiers_used"][0] == "T0"
    assert conn.execute(
        "SELECT count(*) AS c FROM job WHERE type='validate.doc'").fetchone()["c"] == 1


def test_re_extraction_bumps_rev(conn, fixture_pdf):
    path = fixture_pdf(ISO_CERT)
    job = {"payload": {"archive_url": path, "content_hash": "sha-iso-2", "group_id": 7}}
    eh.handle_extract_doc(conn, job, llm=StubLlm())
    conn.commit()
    again = eh.handle_extract_doc(conn, job, llm=StubLlm())
    conn.commit()
    assert again["extract_rev"] == 2


def test_extraction_attempt_records_the_steering_playbook(conn):
    tiers_mod.write_extraction_attempt(
        conn, "abc123", ["T0"], {"type": {"value": "DoC"}},
        extract_rev=1, playbook_slug="komet", playbook_rev=3,
    )

    row = conn.execute(
        "SELECT playbook_slug, playbook_rev FROM extraction_attempt "
        "WHERE content_hash=%s", ("abc123",),
    ).fetchone()
    assert (row["playbook_slug"], row["playbook_rev"]) == ("komet", 3)


def test_extraction_attempt_without_a_playbook_records_nulls(conn):
    """No playbook claimed this document -- the columns say so rather than
    guessing a slug."""
    tiers_mod.write_extraction_attempt(conn, "def456", ["T0"], {}, extract_rev=1)

    row = conn.execute(
        "SELECT playbook_slug, playbook_rev FROM extraction_attempt "
        "WHERE content_hash=%s", ("def456",),
    ).fetchone()
    assert (row["playbook_slug"], row["playbook_rev"]) == (None, None)


# --- extract_hints (Task 7): per-manufacturer T1/T2 guidance, behind four
# guards -- the only playbook key an LLM ever sees. -------------------------

KOMET = Playbook(slug="komet", manufacturer="KOMET", rev=2, extract_hints={
    "ref_list": "Codes are FIGURE.SHANK.SIZE.",
    "manufacturer": "Gebr. Brasseler is the legal entity.",
})


def test_guard_1_every_hint_block_carries_an_override_instruction():
    """A hint must tell the model to DOUBT, never to confirm. Without this
    sentence a wrong T0 identity gets laundered into a 0.95 confidence."""
    out = eh._hints_for(KOMET, ["ref_list"], authoritative=True)

    assert "ignore" in out.lower()
    assert "document" in out.lower()


def test_guard_2_withholds_only_manufacturer_even_when_other_fields_are_asked():
    """DECISIVE: guard 2 is scoped to `manufacturer` only (spec
    docs/superpowers/specs/2026-08-18-playbooks-across-stages-design.md:275,
    "never let a hint speak to the manufacturer field when manufacturer is in
    missing"). A field-keyed hint for any OTHER field must reach the model
    even when that field is itself being asked -- `ref_list` is asked here and
    its hint must still be present. Earlier code generalised the guard to
    every asked field, which made every field-keyed hint but `manufacturer`
    permanently undeliverable; that was wrong."""
    out = eh._hints_for(KOMET, ["ref_list", "validity_to"], authoritative=True)

    assert out is not None
    assert "FIGURE.SHANK.SIZE" in out
    assert "Gebr. Brasseler" in out


def test_guard_2_withholds_the_manufacturer_hint_when_manufacturer_is_asked():
    """The one circularity guard 2 exists to stop: telling the model the very
    identity it is being asked to determine."""
    out = eh._hints_for(KOMET, ["manufacturer", "ref_list"], authoritative=True)

    assert out is not None
    assert "Gebr. Brasseler" not in out
    assert "FIGURE.SHANK.SIZE" in out


def test_guard_2_delivers_the_manufacturer_hint_when_manufacturer_is_not_asked():
    out = eh._hints_for(KOMET, ["validity_to"], authoritative=True)

    assert out is not None
    assert "Gebr. Brasseler" in out


def test_guard_3_a_guessed_manufacturer_says_so():
    """group_id is None (every backfilled document): the manufacturer is T0's
    guess, and the prompt must not present it as established."""
    out = eh._hints_for(KOMET, ["validity_to"], authoritative=False)

    assert "may" in out.lower() or "appears" in out.lower()


def test_no_playbook_means_no_hint_block_at_all():
    assert eh._hints_for(None, ["validity_to"], authoritative=True) is None


def test_a_playbook_with_no_hints_authored_produces_no_block():
    bare = Playbook(slug="voco", manufacturer="VOCO")

    assert eh._hints_for(bare, ["validity_to"], authoritative=True) is None


def test_withholding_every_applicable_hint_produces_no_block():
    """All hints suppressed by guard 2 must yield None, not a block containing
    only the override boilerplate -- that would be tokens for nothing. The
    only hint that can ever be suppressed is `manufacturer` itself, when
    asked, so that is the only shape that can hit this case."""
    only_mfr = Playbook(slug="k", manufacturer="K",
                        extract_hints={"manufacturer": "K is the legal entity."})

    assert eh._hints_for(only_mfr, ["manufacturer"], authoritative=True) is None


class _RecordingLLM:
    """Fills whatever T1 asks (so the ladder actually completes) and records
    every `hints` value it was called with.

    DEVIATION FROM THE BRIEF, verified empirically before writing this: the
    brief's original stub (`_NoEscalationLLM`) asserted the ladder never
    reaches a paid tier for `GC/Fuji_Coat_LC_12022026.pdf`. That is false for
    this fixture regardless of this task's implementation. Every GC
    compliance-doc fixture's ground truth (tests/fixtures/corpus_manifest.py)
    carries `validity_to: None, cert_number: None` -- these fields are
    genuinely absent from the documents -- and `tiers._ESCALATE_ALWAYS` chases
    both for every non-IFU doc type regardless of whether the document ever
    had them, because T0 cannot tell "absent" from "not found". Measured
    directly (t0_extract on this fixture, both with the real playbooks/gc.json
    and with the synthetic one this test builds): T0 always leaves
    `[validity_to, cert_number, ref_list, basic_udi_di]` outstanding, so T1 is
    always called. What this test actually needs -- proof that a
    playbook-steered read records its provenance, and that the hint reached
    the model -- does not require a zero-call ladder, so this stub completes
    the ladder instead of forbidding it."""

    usage_log = ()

    def __init__(self):
        self.hints_seen = []

    def extract(self, tier, doc, filename, missing, hints=None):
        self.hints_seen.append(hints)
        return {f: {"value": f"v_{f}", "conf": 0.97, "tier": tier,
                    "verbatim": "stub", "page": 1, "model_id": "stub"}
                for f in missing}


def test_guard_4_a_hinted_extraction_records_the_playbook_revision(
        conn, fixture_pdf, monkeypatch, tmp_path):
    """Invariant 2: the evidence must name the rules that shaped it. Uses a real
    committed GC fixture and a playbooks dir containing only GC, so the steering
    playbook is unambiguous."""
    from app import playbooks as pb_mod

    (tmp_path / "gc.json").write_text(json.dumps({
        "manufacturer": "GC EUROPE N.V.", "rev": 4,
        "extract_hints": {"general": "Dates are written DD/MM/YYYY."},
    }))
    monkeypatch.setattr(pb_mod, "PLAYBOOKS_DIR", tmp_path)

    path = fixture_pdf("GC/Fuji_Coat_LC_12022026.pdf")
    content_hash = "gc-fixture-hash"
    job = {"id": 1, "payload": {"content_hash": content_hash,
                                "archive_url": path, "group_id": None}}

    llm = _RecordingLLM()
    eh.handle_extract_doc(conn, job, llm=llm)

    row = conn.execute(
        "SELECT playbook_slug, playbook_rev FROM extraction_attempt "
        "WHERE content_hash=%s", (content_hash,),
    ).fetchone()
    assert (row["playbook_slug"], row["playbook_rev"]) == ("gc", 4)
    # the hint actually reached the model -- provenance without injection
    # would be a hollow guarantee.
    assert any(h and "DD/MM/YYYY" in h for h in llm.hints_seen)


def _gc_group(conn, canonical_manufacturer):
    return conn.execute(
        "INSERT INTO item_group (canonical_manufacturer) VALUES (%s) "
        "RETURNING group_id", (canonical_manufacturer,),
    ).fetchone()["group_id"]


def test_guard_3_caller_hedges_when_the_groups_manufacturer_matches_no_playbook(
        conn, fixture_pdf, monkeypatch, tmp_path):
    """Pins the caller-side fix in `handle_extract_doc`: `group_id` alone is
    NOT enough to call the manufacturer authoritative. `item_group.
    canonical_manufacturer` is NOT NULL (migration 002), so the row here
    carries a real string -- just one no playbook claims, the same outcome
    `archiving.manufacturer_for_group`'s "unknown" sentinel produces for a
    group with no row at all. Either way `override` must resolve to None,
    `steering` must fall back to T0's own text guess, and the hint must still
    hedge -- never assert an identity nobody in the group established."""
    from app import playbooks as pb_mod

    (tmp_path / "gc.json").write_text(json.dumps({
        "manufacturer": "GC EUROPE N.V.", "rev": 4,
        "extract_hints": {"general": "Dates are written DD/MM/YYYY."},
    }))
    monkeypatch.setattr(pb_mod, "PLAYBOOKS_DIR", tmp_path)

    group_id = _gc_group(conn, "Unclaimed Manufacturer Ltd")
    path = fixture_pdf("GC/Fuji_Coat_LC_12022026.pdf")
    content_hash = "gc-unclaimed-mfr-hash"
    job = {"id": 1, "payload": {"content_hash": content_hash,
                                "archive_url": path, "group_id": group_id}}

    llm = _RecordingLLM()
    eh.handle_extract_doc(conn, job, llm=llm)

    hint = next(h for h in llm.hints_seen if h)
    assert "may be from" in hint.lower()
    assert "belongs to" not in hint.lower()


def test_guard_3_caller_asserts_when_the_groups_manufacturer_matches_a_playbook(
        conn, fixture_pdf, monkeypatch, tmp_path):
    """The true branch: the group's OWN canonical_manufacturer resolves to a
    playbook, so `override` is that playbook, `authoritative` is True, and the
    hint may state identity unhedged."""
    from app import playbooks as pb_mod

    (tmp_path / "gc.json").write_text(json.dumps({
        "manufacturer": "GC EUROPE N.V.", "rev": 4,
        "extract_hints": {"general": "Dates are written DD/MM/YYYY."},
    }))
    monkeypatch.setattr(pb_mod, "PLAYBOOKS_DIR", tmp_path)

    group_id = _gc_group(conn, "GC EUROPE N.V.")
    path = fixture_pdf("GC/Fuji_Coat_LC_12022026.pdf")
    content_hash = "gc-claimed-mfr-hash"
    job = {"id": 1, "payload": {"content_hash": content_hash,
                                "archive_url": path, "group_id": group_id}}

    llm = _RecordingLLM()
    eh.handle_extract_doc(conn, job, llm=llm)

    hint = next(h for h in llm.hints_seen if h)
    assert "belongs to gc europe n.v." in hint.lower()


# --- Batch path (C9): the same extract.doc job self-defers until its batch_ref
# batch is ended, then writes the attempt + emits validate.doc. ---------------

def test_batch_path_submits_then_completes(conn, fixture_pdf):
    path = fixture_pdf(ISO_CERT)
    job = _enqueue_extract(conn, "sha-b1", 5, path)
    bc = StubBatchClient({"T1": _batch_fields("T1")}, ready_after=0)

    # Pass 1: batch submitted -> job defers, nothing written yet.
    r1 = eh.handle_extract_doc(conn, job, batch_client=bc, poll_interval_s=0)
    conn.commit()
    assert r1["_deferred"] is True
    assert r1["waiting_on"] == "T1"
    assert _count(conn, "SELECT count(*) c FROM extraction_attempt WHERE content_hash='sha-b1'") == 0
    assert _count(conn, "SELECT count(*) c FROM job WHERE type='validate.doc'") == 0
    assert _count(conn, "SELECT count(*) c FROM batch_ref WHERE content_hash='sha-b1'") == 1
    assert conn.execute(
        "SELECT status FROM job WHERE id=%s", (job["id"],)
    ).fetchone()["status"] == "pending"

    # Pass 2: batch ended -> results merged, attempt written, validate.doc emitted.
    r2 = eh.handle_extract_doc(conn, job, batch_client=bc, poll_interval_s=0)
    conn.commit()
    assert "_deferred" not in r2
    assert r2["tiers_used"] == ["T0", "T1"]  # text PDF, no vision tier
    row = conn.execute(
        "SELECT tier, fields FROM extraction_attempt WHERE content_hash='sha-b1'"
    ).fetchone()
    assert row["tier"] == "T0+T1"
    assert row["fields"]["type"]["value"] == "ISO"   # deterministic T0 preserved
    assert row["fields"]["type"]["tier"] == "T0"
    v = conn.execute(
        "SELECT payload, dedupe_key FROM job WHERE type='validate.doc'"
    ).fetchone()
    assert v["payload"]["content_hash"] == "sha-b1"
    assert v["dedupe_key"] == "validate:sha-b1:1:5"


def test_batch_path_pending_defers_again(conn, fixture_pdf):
    path = fixture_pdf(ISO_CERT)
    job = _enqueue_extract(conn, "sha-b2", 9, path)
    bc = StubBatchClient({"T1": _batch_fields("T1")}, ready_after=1)

    r1 = eh.handle_extract_doc(conn, job, batch_client=bc, poll_interval_s=0)
    conn.commit()
    assert r1["_deferred"] is True and r1["state"] == "submitted"

    r2 = eh.handle_extract_doc(conn, job, batch_client=bc, poll_interval_s=0)
    conn.commit()
    assert r2["_deferred"] is True and r2["state"] == "pending"  # not ready yet
    assert _count(conn, "SELECT count(*) c FROM extraction_attempt WHERE content_hash='sha-b2'") == 0

    r3 = eh.handle_extract_doc(conn, job, batch_client=bc, poll_interval_s=0)
    conn.commit()
    assert "_deferred" not in r3
    assert _count(conn, "SELECT count(*) c FROM extraction_attempt WHERE content_hash='sha-b2'") == 1
    # never resubmitted: exactly one batch for this hash+tier (C9)
    assert bc.submits == ["sha-b2:T1"]


def test_batch_path_escalates_to_second_tier_per_field(conn, fixture_pdf):
    path = fixture_pdf(ISO_CERT)
    job = _enqueue_extract(conn, "sha-b3", 3, path)
    # The ISO cert is manufacturer-scope, so item-ids aren't chased; regulation is
    # the always-escalated field T0 misses. T1 leaves it -> it escalates to T2.
    t1 = _batch_fields("T1", only=[])            # T1 fills nothing it's asked
    t2 = _batch_fields("T2", only=["regulation"])
    bc = StubBatchClient({"T1": t1, "T2": t2}, ready_after=0)

    waited_on = set()
    r = None
    for _ in range(8):
        r = eh.handle_extract_doc(conn, job, batch_client=bc, poll_interval_s=0)
        conn.commit()
        if isinstance(r, dict) and r.get("_deferred"):
            waited_on.add(r["waiting_on"])
            continue
        break

    assert waited_on == {"T1", "T2"}
    assert r["tiers_used"] == ["T0", "T1", "T2"]
    row = conn.execute(
        "SELECT fields FROM extraction_attempt WHERE content_hash='sha-b3'"
    ).fetchone()
    assert row["fields"]["regulation"]["tier"] == "T2"


def test_batch_scan_with_nothing_left_to_extract_skips_the_t2_batch(conn, fixture_pdf):
    """A scan forces the vision tier, but only if there is something to ask it.

    `tiers.run_extraction` already guards this on the sync path ("don't pay for
    a vision call with nothing to extract"); the batch path has to guard it too,
    or a scanned document whose fields T1 already completed submits a T2 batch
    with an empty field list and pays vision rates for an answer nobody reads.
    """
    path = fixture_pdf(SCAN_CERT)
    job = _enqueue_extract(conn, "sha-b5", 6, path)
    bc = StubBatchClient({"T1": _batch_fields("T1")}, ready_after=0)

    r = None
    for _ in range(8):
        r = eh.handle_extract_doc(conn, job, batch_client=bc, poll_interval_s=0)
        conn.commit()
        if r.get("_deferred"):
            continue
        break

    assert r["tiers_used"] == ["T0", "T1"]      # the scan did NOT force T2
    assert bc.submits == ["sha-b5:T1"]          # no second batch was submitted
    assert _count(conn, "SELECT count(*) c FROM batch_ref WHERE content_hash='sha-b5'") == 1


def _empty_ref(tier):
    """The good-faith empty answer: a tier reading GC's page-2 "Artikelliste:
    Gemäß Anhang" field reports no codes rather than the attachment's table."""
    return {"ref_list": {"value": [], "conf": 0.3, "tier": tier,
                         "verbatim": "Artikelliste: Gemäß Anhang",
                         "page": 2, "model_id": "batchmodel"}}


def test_batch_path_empty_answer_never_erases_the_t0_ref_list(conn, fixture_pdf):
    """The batch transport must merge with the same non-destructive rule as the
    sync ladder (tiers.merge), not `dict.update`.

    Mirrors test_run_extraction_keeps_the_t0_ref_list_when_the_llm_returns_none
    on the same fixture. Batch is the mandated transport for sweep work, so an
    erasure here costs a paid re-extraction to undo — the sync-only fix left the
    26-of-61 GC data loss reachable on the exact path the backfill runs."""
    path = fixture_pdf("GC/everX_Posterior_12022026.pdf")
    job = _enqueue_extract(conn, "sha-b4", 4, path)
    bc = StubBatchClient({"T1": _empty_ref("T1"), "T2": _empty_ref("T2")},
                         ready_after=0)

    r = None
    for _ in range(8):
        r = eh.handle_extract_doc(conn, job, batch_client=bc, poll_interval_s=0)
        conn.commit()
        if isinstance(r, dict) and r.get("_deferred"):
            continue
        break

    assert "T2" in r["tiers_used"]   # the escalation really happened
    row = conn.execute(
        "SELECT fields FROM extraction_attempt WHERE content_hash='sha-b4'"
    ).fetchone()
    ref = row["fields"]["ref_list"]
    assert ref["tier"] == "T0"
    assert len(ref["value"]) == 6
    assert "005117" in ref["value"]
