# Replace the gate thresholds with a measurement, or with a measured "we don't know"

**Goal:** find out whether `cfg.gate.high = 0.95` and `cfg.gate.med = 0.75` are anywhere near right, using the ground truth we already hold, and move a threshold only where the data is unambiguous.

**Denis's question, 2026-08-13:** *"could we measure it from files in ftp? then see if this makes any sense and correct the ranges for high, med?"*

**The honest answer, and the shape of this plan:** yes, partially. We hold **24 corpus fixtures with full human-read ground truth** (`tests/fixtures/corpus_manifest.py`, `FIXTURES`, every entry has `ground_truth`). That is enough to see whether a threshold is badly wrong. It is not enough to set one precisely. **A measured "these 24 do not justify moving it" is a valid and useful outcome — do not manufacture a threshold change to look productive.**

**Tech stack:** Python 3.12, pytest, the existing corpus fixtures. Analysis code belongs in `tools/` (dev-time, read-only, excluded from the wheel).

## Global constraints

- `CLAUDE.md` invariants are binding. Read `docs/dentalia-mdr-pipeline-ground-truth.md` §7 (domain traps) before interpreting any field-level result.
- **Cost.** T0 is free. Re-running T1/T2 over 24 fixtures costs real money (~$0.02/doc sync, so under $1) and needs `ANTHROPIC_API_KEY`. **Do not spend anything without asking Denis first.** Prefer the persisted `extraction_attempt` rows, which already carry raw self-reported confidence per field per tier — that is what calibration is defined against (`app/extract/tiers.py` `calibrate()`).
- Do NOT edit `web/app.py`, `docs/code-map.md`, `docs/runbook.md`, `tasks/todo.md`, `tests/test_web.py`, `web/templates/*`, `web/static/*`.
- Commit messages: plain imperative subject, explain why, no Claude/AI/agent mentions, no emojis, no em-dashes.

---

### Task 1: measure self-reported confidence against truth

**Files:**
- Create: `tools/calibration.py`
- Read: `tests/fixtures/corpus_manifest.py` (`FIXTURES`, 24 entries with `ground_truth`)
- Read: the `extraction_attempt` table (raw per-field confidence, per tier, per `extract_rev`)

- [ ] **Step 1: build the join.** For each fixture and each of the 10 TARGET fields, produce a row: `(field, tier, model_id, reported_confidence, extracted_value, true_value, correct)`.
- [ ] **Step 2: report accuracy by confidence band**, per field and per tier:

```
field           tier  band        n   correct   accuracy
validity_to     T1    0.95-1.00  12        11      92%
validity_to     T1    0.75-0.95   4         1      25%
...
```

- [ ] **Step 3: answer the actual question in the output.** For `cfg.gate.high = 0.95`: of the values at or above it, what fraction were right? For `cfg.gate.med = 0.75`: same. A threshold is justified when accuracy above it is materially better than below it. If the bands are indistinguishable, the threshold is not doing anything and that is the finding.
- [ ] **Step 4: note the known prior.** Ten date samples measured earlier: correct extractions came in at 0.95-1.0 confidence, the three wrong ones at 0.75-0.85. Ten samples is far too thin to set a threshold on — see whether 24 fixtures reproduce the separation or dissolve it.
- [ ] **Step 5: commit the tool** with its output in the commit message.

---

### Task 2: propose calibration factors, or state that you cannot

**Files:**
- Modify: `app/config.py` (add a `[calibration]` section if and only if the data supports one)
- Modify: `docs/vocabulary.md` §7 if tier semantics change

Calibration is a gate-time transform: `effective = raw × factor(model_id, field)`, clamped to [0,1], in `app/extract/tiers.py` `calibrate()`. T0 (`model_id` None) is **never** calibrated. The config map does not exist yet, so the factor is identity everywhere.

- [ ] **Step 1:** for each `(model_id, field)` with enough samples, compute the factor that would align reported confidence with observed accuracy.
- [ ] **Step 2: state the sample size beside every proposed factor.** A factor derived from 3 samples is noise wearing a decimal point. Propose nothing under 10 samples for that pair; report it as "insufficient data" instead.
- [ ] **Step 3: do NOT ship a factor Denis has not seen.** Write the proposal into the plan's results section and stop. Applying it is a separate, explicitly approved step.

---

### Task 3: report, and say what would settle it

- [ ] **Step 1:** append the findings to `tasks/followups.md` under `[gate-thresholds]`, replacing the placeholder text with the measurement.
- [ ] **Step 2:** if 24 fixtures are too few — the likely outcome — say exactly what would settle it: the sweep produces ~1,158 extractions, and calibrating on the sample you already reviewed is circular. Recommend measuring on the sweep's output, and record 0.95/0.75 as a **deliberate, documented placeholder** rather than an unexamined default. That is the real deliverable if the data is thin.

---

## Self-review before finishing

1. Did you re-run any paid tier without asking? You should not have.
2. Does every proposed factor carry its sample size?
3. Is the conclusion falsifiable — could a reader disagree with it from your own table?
4. If the answer is "the data does not justify a change", did you say so plainly rather than shipping a small change to look busy?
