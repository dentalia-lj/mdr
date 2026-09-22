# Label-adjacent dates, and the ISO/EC conflation — design

Design, 2026-09-03. Approach B approved by Denis the same day, chosen over
four alternatives on measured data (§3).

**Build status, re-verified 2026-09-09** (this header said "Nothing here is
implemented" for six days after most of it shipped):

| Part | State |
|---|---|
| D1 — label-proximity date pairing (§1-3) | **Shipped.** `t0_templates.py:740-770`, tests in `test_t0.py:1712+` |
| D2 — the T1 prompt conflation (§4) | **Shipped**. `t1_text.md:27-40` defines all six `type` values and states that a notified-body QMS certificate is `EC`, not `ISO` |
| D3 — the two flags (§5) | **Shipped as constants** (`tiers.ISO_WITHOUT_STANDARD_FLAG`, `tiers.DATE_LABEL_NOT_ADJACENT_FLAG`), both in `gate.INFORMATIONAL_FLAGS`. **But `iso-without-standard-number` is unreachable on the manufacturer-scope route** — `validate.py` returns at `:861` before computing it at `:871`, which is every QMS certificate. See `[iso-flag-unreachable-on-mfr-binding]` |
| §6 — the correction pass | **Half done.** The DATE half shipped as `dentalia repair-label-dates` and ran 2026-09-04 (67 attempts rewritten, no LLM spend). The TYPE half never ran: docs 248, 260 and 316 are still `ISO`/`MDR` staged, last paid T1 call 2026-09-03 10:26 — before the prompt fix at 21:02. 19 ISO-typed settled documents have had no paid call since. See `[type-fix-never-backfilled]` |

Three defects found by running the first crawl recipe against a live
manufacturer site. All three share one shape: **a low-information signal is
stored as high-confidence fact, and nothing downstream can tell.**

Verified against the code and the dev database on 2026-09-03. Every count below
was measured, not estimated.

---

## 1. D1 — the label-proximity date pairing

### What the code does

`app/extract/t0_templates.py:748-757`:

```python
i = low.find(lab)                                              # first "expiry date"
m = _DATE_RE.search(text[i : i + len(lab) + _LABEL_WINDOW])    # first date within 60 chars
out[field_name] = field_ev(iso, 1.0, f"{text[i:i+len(lab)]} {m.group(1)}")
```

It takes **the first date within 60 characters after a label**, pairs them at
**confidence 1.0**, and **synthesises the verbatim** as `label + " " + date`.

### Why that is wrong

The heuristic assumes the label and its value are adjacent in PyMuPDF's linear
text. In a two-column label/value table they are not. Doc 966 (Edenta, a DQS
ISO 13485 certificate) extracts as:

```
Effective date
Expiry date
Frankfurt am Main
549934 MP2021
1000316844
2026-05-21     <- Effective
2027-12-19     <- Expiry
2026-05-21     <- signature date
```

The first date within 60 chars of `Expiry date` is **2026-05-21**, the
*Effective* date. We stored it as the expiry — wrong by 19 months, and the
certificate reads as already expired when it runs to 2027-12-19.

**The verbatim is the more serious half.** `"Expiry date 2026-05-21"` is a
sentence the document does not contain; it was assembled from a label and an
unrelated value. Invariant 2 requires every production value to carry a
`verbatim`, and the whole point is that a human can check it against the page. A
manufactured quote defeats that, and it is what made the wrong value look
trustworthy.

### Measured blast radius

Every document holding a T0, pageless, confidence-1.0 `validity_to` that is not
filename-derived — 184 rows across `production`, `staged` and `filed`:

| | count | verdict |
|---|---|---|
| date immediately after the label | 178 | correct |
| real content between label and date | 4 | **3 wrong, 1 correct** |
| no label / unreadable | 2 | n/a |

The three wrong ones are all the same **DQS two-column certificate**: docs 903,
966, 967. Doc 903 stored `validity_to = 2019-12-16` when the document says
**2024-05-26** — off by four and a half years.

**The filename path is NOT implicated.** `FILENAME_CONF` is already 0.6 with an
honest `filename:{name}` verbatim (`t0_templates.py:535`). An earlier reading of
this defect blamed it; that was wrong and is recorded here so nobody re-derives
it.

## 2. The rule

**A labelled date counts only when the date immediately follows the label,
separated by nothing but separator characters. Otherwise that label does not
match, and the loop continues to the next label.**

Separators are exactly: whitespace (including newlines), `:`, `,`, `.`, `-`,
`–`, `—`. Everything else is content.

The existing loop already breaks on "the first label that finds a date". The
change is to break on "the first label that finds an **adjacent** date", so
falling through to the next label is the natural consequence rather than new
machinery.

And the verbatim becomes the **real span** — the text actually spanning label
through date — never `label + " " + date` assembled from two places.

## 3. Why this rule and not the others

All four alternatives were tested against the measured set before choosing.

| Option | Result |
|---|---|
| Refuse when >1 date is in the window | **Inverted on both sides.** Would refuse docs 44, 405, 420, 816 — all correct, all shaped `Expiry date: X / Issue date: Y` — and would miss doc 966, whose window holds only one date. Discarded. |
| **Adjacency, falling through to the next label** | **0 false positives, 3 of 3 true positives.** Chosen. |
| Geometric pairing from PyMuPDF coordinates | Would *read* 2027-12-19 instead of escalating, but it is real coordinate maths, brittle across layouts, and T1 already recovers the value for about $0.004. This is what per-manufacturer `t0_layout` templates exist for; author a DQS template if volume ever justifies it. |
| Shrink `_LABEL_WINDOW` below 60 | 966's gap is 47 chars so ~30 would catch it — arbitrary, breaks longer legitimate separators, leaves the mechanism intact. |
| Keep the value at reduced confidence when not adjacent | Same end state as the chosen rule (T1 decides), but leaves an unsupported value in the evidence trail if T1 fails. Do not assert what you cannot support. |

**The fall-through is what makes it precise.** Doc 317 is a bilingual
declaration: an English `valid until` heading with no value, then
`Diese Konformitätserklärung ist gültig bis: 13.02.2031`. Adjacency fails on the
English label, the loop continues, `gültig bis` matches adjacently, and the
correct date is read with no escalation. A rule that refused instead of falling
through would have cost this document.

### Edge cases

- **Bilingual documents** — handled by the fall-through, as above.
- **Two-column certificates** — no label is adjacent, no value, escalate to T1.
  This is the intended outcome: T1 reads the page and answers.
- **The same label appearing twice**, once as a bare heading and once with a
  value: `low.find(lab)` only ever tries the FIRST occurrence, so a document of
  that shape still fails. Deliberately **not** fixed — no measured case needs
  it, and the fall-through already covers the bilingual variant, which is the
  shape that actually occurs. Recorded so the limit is known.
- **`validity_from` uses the same loop** and gets the same fix. Its
  place-and-date fallback (`_PLACE_AND_DATE`, 0.95) is untouched.
- **Scans with no text layer** — unaffected; no labels are found either way.

## 4. D2 — T1 types MDR quality-system certificates as ISO

Doc 967 is an MDR Annex IX quality-management certificate. Its evidence:

```
type        "EU Quality Management Certificate"   T1  0.95
regulation  "(EU) 2017/745"                       T0  1.00
```

Stored as `type=ISO, regulation=MDR`. An ISO 13485 certificate is not issued
under MDR.

**Root cause is the prompt, and it is a known conflation left in one place.**
`app/extract/prompts/t1_text.md:3` describes the corpus as *"EC certificates,
ISO 13485 / Quality Management System certificates"* — equating ISO with
quality-management certificates. That exact equation was removed from T0's
`_TYPE_MARKERS` on 2026-08-20 after doc 420 reached `production` as ISO
(`t0_templates.py:61-66` records it). Nobody removed it from the prompt, so T1
still makes the error the T0 fix was written to prevent.

Compounding it, the prompt's `type` field defines **only `SPP`** of its six
values (`t1_text.md:27-32`). `DoC`, `EC`, `ISO`, `IFU` and `other` are left to
inference.

**Fix:** define all six values, and state the distinction plainly — a
notified-body certificate issued under MDR/MDD is `EC` *even when what it
certifies is a quality system*; `ISO` is a certificate against the ISO
13485/9001 standard, named as such.

### The check, and the naive version that does not work

`type=ISO` together with `regulation ∈ {MDR, MDD}` is **not** reliably a
contradiction: a genuine ISO 13485 certificate may name MDR in its scope
("quality system for devices under Regulation (EU) 2017/745"), and `regulation`
is defined as "cites anywhere". Such a rule would fire on correct documents.

The rule that does hold reuses the 2026-08-20 ruling — *`iso 13485` is the ONLY
ISO signal*: **`type='ISO'` requires an ISO standard number (13485 or 9001) in
the document's evidence.** Doc 966 has it and stays ISO; doc 967 does not and is
flagged. A real ISO certificate always names its standard, so there is no false
positive, and a combined ISO+MDR certificate names ISO 13485 and survives.

Measured today: 5 documents are `ISO` with a regulation (docs 248, 260, 316,
530, 967). **None is at production.**

## 5. D3 — the two flags

Both **informational** (ruled by Denis, 2026-09-03): they appear on the document
and in review, cap nothing, block nothing. Nothing that auto-writes today stops
auto-writing, and the counts tell us whether a blocking version is ever
warranted.

- `date-label-not-adjacent` — a labelled date was rejected because content sat
  between label and value. Records that the document has a layout T0 cannot
  read, which is the signal that would justify a `t0_layout` template.
- `iso-without-standard-number` — `type='ISO'` with no ISO standard number in
  evidence.

Both need a `docs/vocabulary.md` row; `tests/test_vocabulary_doc.py` enforces it.

## 6. The correction pass

Ruled by Denis: **re-extract and correct, no status change.** Invariant 4 forbids
downgrading, and these are corrections to evidence rather than new candidates.

Scope is **3 documents**, not the 82 an earlier reading suggested: docs 903, 966
and 967. 178 of the 184 are correct as they stand.

Shape: a `repair-label-dates` CLI command following the established
`repair-ref-list` / `repair-archive-urls` / `repair-stated-class` pattern — dry
run by default, `--apply` to write. Those tools already carry the documented
operational exception to invariant 1 (`[invariant1-ops-exception-undocumented]`).
It must not change `status`.

## 7. Sequence

1. The adjacency rule + the real-span verbatim (`t0_templates.py`, tests).
2. The two VALIDATE flags + their `vocabulary.md` rows.
3. The T1 prompt: define all six types, remove the ISO/QMS equation. Diff a
   sample before and after — a prompt change moves extraction behaviour across
   the whole corpus, and the only honest way to ship one is to look.
4. `repair-label-dates`, then run it over the three documents.

1 and 2 are independent of 3. 4 must come last, so the repair re-extracts under
the corrected rules.

## 8. Open questions

1. **Should `regulation` mean "issued under" rather than "cites anywhere"?** The
   current definition (`t1_text.md:38-39`) is what makes `ISO + MDR` ambiguous,
   and `regulation` scopes supersession (invariant 5: MDR never supersedes MDD).
   An incidental mention therefore mis-scopes a supersession chain. Out of scope
   here; flagged because it is the deeper version of D2 and wants its own
   measurement.
2. **Does the T1 prompt change need the Phase 0 diff protocol?** CLAUDE.md
   mandates it for a tier swap. A prompt edit is not a tier swap but moves the
   same numbers. Proposed: a sample diff (step 3), not the full protocol.
3. **Is a DQS `t0_layout` template worth authoring?** Three documents today, and
   DQS certifies many manufacturers, so the shape will recur. Proposed: wait for
   the `date-label-not-adjacent` counts to say.
