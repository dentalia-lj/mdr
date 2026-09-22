# LLM tier fixtures (recorded responses)

CI-safe playback for the LLM extraction tiers. `RecordedLlm` (in `__init__.py`)
implements the real tier interface — `extract(tier, doc, filename, missing)`, the
same one `app.extract.llm.AnthropicLlm` exposes and `tiers.run_extraction` calls —
returning recorded per-field evidence instead of live Anthropic calls. No network,
no key, fully deterministic.

Used by `tests/test_recorded_llm.py` (the stub's own tests) and
`tests/test_tiers_corpus.py` (the ladder run over the committed corpus fixtures).
The unit-level tier tests (`test_tiers.py`, `test_llm.py`, `test_batching.py`,
`test_extract_handler.py`) use their own inline stubs; this recorded stub adds the
corpus-grounded, CI-runnable dimension.

## What these files are (and are not)

- **Are:** canned model outputs for *orchestration* tests — per-field escalation,
  evidence assembly, scan→T2 routing — over real files. Values need only be
  well-formed, not accurate.
- **Are not:** ground truth. Model *accuracy* is measured separately by the
  (deferred) opt-in live-accuracy harness, which calls the real model and compares
  against `corpus_manifest.ground_truth` — never against these files.

## Format

```json
{
  "content_hash": "sha256:<hex>",   // sha256 of the fixture PDF bytes
  "tier": "T1",                      // T1 (text) or T2 (vision)
  "model_id": "haiku-4.5",
  "fields": {
    "<field>": {"value": ..., "conf": 0.0-1.0, "verbatim": "...", "page": <int|null>}
  }
}
```

`content_hash` is the real digest of the fixture (`RecordedLlm.hash_file`), so when
the ladder hashes the same file the matching response is selected. `tier`/`model_id`
are stamped onto each returned field by the stub.

| file | fixture | tier | fills |
|---|---|---|---|
| `ivoclar_emax_t1_udi.json` | IVOCLAR/IPS e.max Ceram.pdf | T1 | `basic_udi_di` |
| `dentsply_scan_t2.json` | DENSTPLY/IMP - DOC - ATIS TX ... .pdf | T2 | `regulation`, `validity_to` (scanned → vision) |

`ivoclar_emax_t1_udi.json` no longer serves the ladder: S1.5 broadened `_UDI_LABEL`
so T0 reads e.max's `basic_udi_di` itself (tier T0, conf 1.0) and the T1 escalation
this answers never happens. **It is still load-bearing** — `tests/test_recorded_llm.py`
is the stub's own unit-test file and uses this recording as its only T1 subject
(recorded value, wrong-tier miss, unrecorded-field miss, call recording). Deleting
it fails four tests. Verified 2026-08-14, followup `[extract-t0]`.

## Recording a real response

Run the tier against the fixture once with logging on, copy the returned per-field
evidence into a new `responses/<name>.json`, and set `content_hash` to
`RecordedLlm.hash_file(path)`. Keep values well-formed; commit nothing sensitive.
