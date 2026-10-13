"""Slice 3: the economics ledger (extraction_cost). Drives the REAL extract.doc
handler with the REAL tier clients wrapping usage-carrying fakes, so the whole
capture -> price -> persist path is exercised against Postgres. No API key.

Covers: sync writes one cost row per LLM tier at the attempt's rev; a T0-only doc
(MSDS) writes none; the batch path writes each tier's row as it resolves and stays
idempotent across the polls that re-visit an already-resolved tier; an unpriced
model still records tokens with a null cost; the extraction_spend rollup sums up.
Since 2026-10-12 a row's `tier` is the read's label, so the doc-class call and the
T1 read of one document are two rows, not one silently dropped.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app import queue
from app.extract import llm
from app.extract import tiers as tiers_mod
from app.handlers import extract as eh
from tests.fixtures import corpus_manifest as m

ISO_CERT = m.by_name("Ivoclar ISO certifikat do 30_10_2027.pdf")
MSDS = m.by_name("pattern-resin-ls-liquid-sds-ca-en.pdf")


class Models:
    t1 = "claude-haiku-4-5"
    t2 = "claude-sonnet-4-6"
    rank = "claude-haiku-4-5"
    vision_first = "claude-haiku-5-5"
    second_read = "claude-haiku-5-5"


class UnpricedModels:
    t1 = "some-unpriced-model"
    t2 = "some-unpriced-model"
    rank = "some-unpriced-model"
    vision_first = "some-unpriced-model"
    second_read = "some-unpriced-model"


def _usage(i, o):
    return SimpleNamespace(input_tokens=i, output_tokens=o,
                           cache_read_input_tokens=None, cache_creation_input_tokens=None)


def _resp(payload, usage):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=payload)], usage=usage)


def _payload(fields, conf=0.99, regulation="n.a."):
    """Structured-output JSON for the given fields (arrays for list fields).
    `regulation` is `n.a.` by default: the ISO fixture, where T0 leaves only
    that field open, cites neither regulation, so the text proves it
    (`checks.settled`) and the text ladder stops at T1."""
    d = {}
    for f in fields:
        val = [] if f in ("ref_list", "referenced_docs") else f"v_{f}"
        if f == "regulation":
            val = regulation
        d[f] = {"value": val, "confidence": conf, "verbatim": f"verb {f}", "page": 1}
    return json.dumps(d)


def _rows(conn, content_hash):
    return conn.execute(
        "SELECT tier, model_id, batch, input_tokens, output_tokens, cost_usd "
        "FROM extraction_cost WHERE content_hash=%s ORDER BY tier",
        (content_hash,),
    ).fetchall()


# --- sync path --------------------------------------------------------------------

class _SyncClient:
    """A low-level Anthropic stand-in: messages.create returns a canned response
    carrying `.usage`. Wrapped by the real AnthropicLlm so capture runs for real."""

    def __init__(self, payload, usage):
        self.messages = SimpleNamespace(create=lambda **kw: _resp(payload, usage))


def test_sync_writes_one_cost_row_per_llm_tier(conn, fixture_pdf):
    # ISO cert (text) -> T0 then T1 proves the one field left (no image read).
    tier_client = llm.AnthropicLlm(_SyncClient(_payload(tiers_mod.TARGET), _usage(1500, 400)),
                                   Models())
    job = {"payload": {"archive_url": fixture_pdf(ISO_CERT),
                       "content_hash": "sha-cost-sync", "group_id": 1}}

    result = eh.handle_extract_doc(conn, job, llm=tier_client)
    conn.commit()

    assert result["tiers_used"] == ["T0", "T1"]
    rows = _rows(conn, "sha-cost-sync")
    assert len(rows) == 1                                    # T0 is free -> no row
    (r,) = rows
    assert r["tier"] == "T1"
    assert r["model_id"] == "claude-haiku-4-5"
    assert r["batch"] is False
    assert (r["input_tokens"], r["output_tokens"]) == (1500, 400)
    # Haiku (1500*1 + 400*5)/1e6 = 0.0035
    assert float(r["cost_usd"]) == 0.0035


class _RoutingClient:
    """messages.create answers the doc-class call and the field read apart, each
    with its own usage, so each row in the ledger can be told from the other."""

    def __init__(self):
        self.messages = SimpleNamespace(create=self._create)

    @staticmethod
    def _create(**kw):
        if kw["system"] == llm.DOC_CLASS_SYSTEM:
            return _resp(json.dumps({"doc_class": "DoC", "quote": "Declaration"}),
                         _usage(300, 20))
        return _resp(_payload(tiers_mod.TARGET), _usage(1500, 400))


def _untyped_pdf(tmp_path) -> str:
    """Text T0 cannot type (no header marker), so the doc-class call runs. A
    full page of it: a page under 200 characters is `sparse`, and a document
    with no text page takes the scan ladder (`pdf.has_text_page`)."""
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page()
    for i in range(12):
        page.insert_text((72, 72 + 20 * i), "no markers anywhere in this text", fontsize=12)
    path = tmp_path / "untyped.pdf"
    doc.save(str(path))
    doc.close()
    return str(path)


def test_doc_class_and_t1_write_a_row_each(conn, tmp_path):
    """The regression. Both calls logged tier `T1` and the ledger is unique on
    (content_hash, tier, extract_rev), so `ON CONFLICT DO NOTHING` silently
    dropped the T1 read on every document T0 could not type. Since Task 4 the
    generic answers this client gives are not proven by the text, so the
    image reads run too: every paid call, one row each."""
    tier_client = llm.AnthropicLlm(_RoutingClient(), Models())
    job = {"payload": {"archive_url": _untyped_pdf(tmp_path),
                       "content_hash": "sha-cost-docclass", "group_id": 9}}

    result = eh.handle_extract_doc(conn, job, llm=tier_client)
    conn.commit()

    assert result["doc_class"] == "compliance-doc"
    assert result["tiers_used"] == ["T0", "doc-class", "T1", "T2-first", "T2"]
    rows = _rows(conn, "sha-cost-docclass")
    assert len(rows) == 4
    assert {(r["tier"], r["model_id"], r["input_tokens"]) for r in rows} == {
        ("T1", "claude-haiku-4-5", 1500),
        ("doc-class", "claude-haiku-4-5", 300),
        ("T2-first", "claude-haiku-5-5", 1500),
        ("T2", "claude-sonnet-4-6", 1500),
    }


def test_ledger_keeps_one_row_per_label_and_stays_idempotent(conn):
    """Distinct labels at one revision are distinct rows; the same label twice
    (a batch poll re-fetching a resolved result) stays one."""
    from app.extract.economics import CallCost, Usage

    calls = [CallCost(label, "claude-haiku-4-5", Usage(100, 10))
             for label in ("doc-class", "T1", "T1-second", "T2-first", "T2-second", "T2", "T1")]
    for call in calls:
        tiers_mod.write_extraction_cost(conn, "sha-cost-labels", 1, call)
    conn.commit()

    tiers = [r["tier"] for r in _rows(conn, "sha-cost-labels")]
    assert len(tiers) == 6
    assert set(tiers) == {"doc-class", "T1", "T1-second", "T2-first", "T2-second", "T2"}


class _TwiceLlm:
    """A ladder bug: two paid calls under one label in one extraction."""

    def __init__(self):
        from app.extract.economics import CallCost, Usage
        self.usage_log = []
        self._call = lambda: CallCost("T1", "claude-haiku-4-5", Usage(100, 10))

    def extract(self, tier, doc, filename, missing):
        self.usage_log += [self._call(), self._call()]
        return {}


def test_a_label_sent_twice_in_one_extraction_fails_before_writing(conn, fixture_pdf):
    """The ledger would keep one of the two rows and drop the other without a
    word, so `_finalize` refuses before writing the attempt or any cost row."""
    job = {"payload": {"archive_url": fixture_pdf(ISO_CERT),
                       "content_hash": "sha-cost-twice", "group_id": 1}}

    with pytest.raises(ValueError, match="T1"):
        eh.handle_extract_doc(conn, job, llm=_TwiceLlm())

    assert _rows(conn, "sha-cost-twice") == []
    assert conn.execute("SELECT count(*) AS c FROM extraction_attempt "
                        "WHERE content_hash='sha-cost-twice'").fetchone()["c"] == 0


def test_t0_only_doc_writes_no_cost_rows(conn, fixture_pdf):
    # MSDS short-circuits at T0 (no LLM call) -> no cost rows at all.
    tier_client = llm.AnthropicLlm(_SyncClient(_payload(tiers_mod.TARGET), _usage(1, 1)),
                                   Models())
    job = {"payload": {"archive_url": fixture_pdf(MSDS),
                       "content_hash": "sha-cost-msds", "group_id": 2}}

    result = eh.handle_extract_doc(conn, job, llm=tier_client)
    conn.commit()

    assert result["doc_class"] == "msds"
    assert result["tiers_used"] == ["T0"]
    assert _rows(conn, "sha-cost-msds") == []
    assert tier_client.usage_log == []                       # LLM never called


def test_unpriced_model_records_tokens_with_null_cost(conn, fixture_pdf):
    tier_client = llm.AnthropicLlm(_SyncClient(_payload(tiers_mod.TARGET), _usage(120, 40)),
                                   UnpricedModels())
    job = {"payload": {"archive_url": fixture_pdf(ISO_CERT),
                       "content_hash": "sha-cost-unpriced", "group_id": 3}}

    eh.handle_extract_doc(conn, job, llm=tier_client)
    conn.commit()

    (r,) = _rows(conn, "sha-cost-unpriced")
    assert r["input_tokens"] == 120                          # tokens kept
    assert r["cost_usd"] is None                             # never silently 0


# --- batch path -------------------------------------------------------------------

def _item(tier, content_hash, payload, usage):
    return SimpleNamespace(
        custom_id=f"{tier}_{content_hash}",
        result=SimpleNamespace(type="succeeded", message=_resp(payload, usage)),
    )


class _LowBatches:
    """Anthropic messages.batches stand-in: batches are immediately 'ended'; the
    real AnthropicBatchClient.results() reads `.usage` off each item's message."""

    def __init__(self, items_by_tier):
        self.items_by_tier = items_by_tier
        self._tier = {}
        self._n = 0

    def create(self, requests):
        self._n += 1
        bid = f"batch-{self._n}"
        self._tier[bid] = requests[0]["custom_id"].split("_", 1)[0]
        return SimpleNamespace(id=bid)

    def retrieve(self, bid):
        return SimpleNamespace(processing_status="ended")

    def results(self, bid):
        return list(self.items_by_tier[self._tier[bid]])


def _low_client(items_by_tier):
    return SimpleNamespace(messages=SimpleNamespace(batches=_LowBatches(items_by_tier)))


def _enqueue(conn, content_hash, group_id, path):
    jid = queue.enqueue(conn, "extract.doc",
                        {"archive_url": path, "content_hash": content_hash, "group_id": group_id},
                        f"extract:{content_hash}")
    conn.commit()
    return conn.execute("SELECT * FROM job WHERE id=%s", (jid,)).fetchone()


def _drive_batch(conn, job, batch_client, limit=8):
    r = None
    for _ in range(limit):
        r = eh.handle_extract_doc(conn, job, batch_client=batch_client, poll_interval_s=0)
        conn.commit()
        if isinstance(r, dict) and r.get("_deferred"):
            continue
        break
    return r


def test_batch_writes_cost_row_at_batch_rate(conn, fixture_pdf):
    job = _enqueue(conn, "sha-cost-b1", 5, fixture_pdf(ISO_CERT))
    items = {"T1": [_item("T1", "sha-cost-b1", _payload(tiers_mod.TARGET), _usage(1500, 400))]}
    bc = llm.AnthropicBatchClient(_low_client(items), Models())

    r = _drive_batch(conn, job, bc)

    assert r["tiers_used"] == ["T0", "T1"]
    (row,) = _rows(conn, "sha-cost-b1")
    assert row["tier"] == "T1" and row["batch"] is True
    # Haiku batch: 0.0035 / 2 = 0.00175
    assert float(row["cost_usd"]) == 0.00175


def test_batch_three_reads_three_rows_idempotent(conn, fixture_pdf):
    # The ISO cert is manufacturer-scope, so item-ids aren't chased; regulation is
    # the always-escalated field T0 misses. T1 answers it empty (the word
    # "Regulation" is printed, so that settles nothing), the cheap image read
    # answers MDD (not proven: neither citation is printed), Sonnet n.a. Across
    # the polls each resolved read is re-visited; its row must stay unique.
    items = {
        "T1": [_item("T1", "sha-cost-b2", _payload(["regulation"], regulation=None),
                     _usage(1500, 400))],
        "T2-first": [_item("T2-first", "sha-cost-b2",
                           _payload(["regulation"], regulation="MDD"), _usage(2000, 100))],
        "T2": [_item("T2", "sha-cost-b2", _payload(["regulation"]), _usage(8000, 500))],
    }
    job = _enqueue(conn, "sha-cost-b2", 6, fixture_pdf(ISO_CERT))
    bc = llm.AnthropicBatchClient(_low_client(items), Models())

    r = _drive_batch(conn, job, bc)

    assert r["tiers_used"] == ["T0", "T1", "T2-first", "T2"]
    rows = _rows(conn, "sha-cost-b2")
    assert [x["tier"] for x in rows] == ["T1", "T2", "T2-first"]   # one row each
    t1, t2, first = rows
    assert float(t1["cost_usd"]) == 0.00175                  # Haiku 4.5 batch
    # Sonnet batch: (8000*3 + 500*15)/1e6 / 2 = 0.0315 / 2 = 0.01575
    assert float(t2["cost_usd"]) == 0.01575
    # Haiku 5.5 batch: (2000*0.10 + 100*0.50)/1e6 / 2 = 0.000125
    assert float(first["cost_usd"]) == 0.000125


def test_spend_view_sums_measured_cost(conn, fixture_pdf):
    tier_client = llm.AnthropicLlm(_SyncClient(_payload(tiers_mod.TARGET), _usage(1000, 200)),
                                   Models())
    job = {"payload": {"archive_url": fixture_pdf(ISO_CERT),
                       "content_hash": "sha-cost-view", "group_id": 8}}
    eh.handle_extract_doc(conn, job, llm=tier_client)
    conn.commit()

    row = conn.execute(
        "SELECT calls, input_tokens, cost_usd, unpriced_calls FROM extraction_spend "
        "WHERE model_id='claude-haiku-4-5' AND tier='T1' AND batch=false"
    ).fetchone()
    assert row["calls"] >= 1
    assert row["unpriced_calls"] == 0
    assert row["cost_usd"] is not None


# --- the scan ladder: one row per label --------------------------------------

class ScanModels:
    t1 = "claude-haiku-4-5"
    t2 = "claude-sonnet-4-6"
    rank = "claude-haiku-4-5"
    vision_first = "claude-haiku-5-5"
    second_read = "claude-haiku-5-5"


def _is_classify(kw):
    return kw["output_config"]["format"]["schema"] == llm.DOC_CLASS_SCHEMA


def _scan_answer(kw):
    """Each scan step told apart by its request: the doc-class schema, the
    second read's `thinking` off, Sonnet's model, else the first cheap read.
    The two cheap reads disagree on `type`, so every step runs."""
    if _is_classify(kw):
        return _resp(json.dumps({"doc_class": "DoC", "quote": "Declaration"}), _usage(3000, 20))
    if "thinking" in kw:
        return _resp(json.dumps({"type": {"value": "ISO", "confidence": 0.8,
                                          "verbatim": "x", "page": 1}}), _usage(5000, 100))
    if kw["model"] == ScanModels.t2:
        return _resp(json.dumps({"type": {"value": "DoC", "confidence": 0.9,
                                          "verbatim": "x", "page": 1}}), _usage(9000, 300))
    answers = {f: {"value": v, "confidence": 0.95, "verbatim": f"read {f}", "page": 1}
               for f, v in {"regulation": "MDR", "validity_from": "2025-08-25",
                            "validity_to": "2030-05-26", "coverage_scope": "group",
                            "cert_number": "C-1", "manufacturer": "GC EUROPE N.V.",
                            "ref_list": ["003477"], "basic_udi_di": "++J022MD0098KV"}.items()}
    answers["type"] = {"value": "DoC", "confidence": 0.8, "verbatim": "x", "page": 1}
    return _resp(json.dumps(answers), _usage(6000, 400))


def _untyped_scan(tmp_path) -> str:
    import pymupdf
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 80), False)
    pix.clear_with(200)
    doc = pymupdf.open()
    doc.new_page().insert_image(pymupdf.Rect(0, 0, 595, 842), stream=pix.tobytes("png"))
    path = tmp_path / "scan.pdf"
    doc.save(str(path))
    doc.close()
    return str(path)


def test_a_scan_writes_one_cost_row_per_label(conn, tmp_path):
    """doc-class-image, T2-first, T2-second and T2: four paid calls, four rows,
    each priced at its own model."""
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: _scan_answer(kw)))
    tier_client = llm.AnthropicLlm(client, ScanModels())
    job = {"payload": {"archive_url": _untyped_scan(tmp_path),
                       "content_hash": "sha-cost-scan", "group_id": 9}}

    result = eh.handle_extract_doc(conn, job, llm=tier_client)
    conn.commit()

    assert result["tiers_used"] == ["T0", "doc-class-image", "T2-first", "T2-second", "T2"]
    rows = {r["tier"]: r for r in _rows(conn, "sha-cost-scan")}
    assert set(rows) == {"doc-class-image", "T2-first", "T2-second", "T2"}
    assert {t: (r["model_id"], r["input_tokens"]) for t, r in rows.items()} == {
        "doc-class-image": ("claude-haiku-5-5", 3000),
        "T2-first": ("claude-haiku-5-5", 6000),
        "T2-second": ("claude-haiku-5-5", 5000),
        "T2": ("claude-sonnet-4-6", 9000),
    }
    assert all(r["batch"] is False for r in rows.values())


def test_a_batch_scan_writes_one_cost_row_per_label(conn, tmp_path):
    """The same four steps over the Batch API, each its own batch and row,
    and the polls that re-fetch a resolved step write nothing twice."""
    h = "sha-cost-bscan"

    def item(label, kw):
        return _item(label, h, _scan_answer(kw).content[0].text, _scan_answer(kw).usage)

    probe = {"output_config": {"format": {"schema": None}}, "model": ""}
    items = {
        "doc-class-image": [item("doc-class-image", {**probe, "output_config": {
            "format": {"schema": llm.DOC_CLASS_SCHEMA}}})],
        "T2-first": [item("T2-first", probe)],
        "T2-second": [item("T2-second", {**probe, "thinking": {"type": "disabled"}})],
        "T2": [item("T2", {**probe, "model": ScanModels.t2})],
    }
    job = _enqueue(conn, h, 9, _untyped_scan(tmp_path))
    bc = llm.AnthropicBatchClient(_low_client(items), ScanModels())

    r = _drive_batch(conn, job, bc, limit=12)

    assert r["tiers_used"] == ["T0", "doc-class-image", "T2-first", "T2-second", "T2"]
    rows = _rows(conn, h)
    assert sorted(x["tier"] for x in rows) == sorted(["doc-class-image", "T2-first",
                                                      "T2-second", "T2"])
    assert all(x["batch"] is True for x in rows)
