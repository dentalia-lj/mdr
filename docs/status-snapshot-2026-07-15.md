# Dentalia Pipeline — Status Snapshot

**Date:** 2026-07-15
**Method:** verified against live repo state (handlers registered, migrations, `pytest --collect-only`, git log), not the docs' self-report.
**Repo:** 58 commits, 357 tests, branch `master`.

---

## Headline

| | |
|---|---|
| **Overall build (by effort)** | **~30%** |
| **By component count (§A, 19 parts)** | ~40% |
| **Critical-path build risk retired** | ~45-50% |
| **Phase 0 (spike)** | 100% DONE — client demo checkpoint met |
| **Phase 1 (LJ core)** | ~25% |
| **Phase 2 (ZG + playbooks)** | 0% |
| **Project health** | Strong (A-) |

The completed third is the hardest, highest-risk, most-reused third: the deterministic-by-default queue, the tiered extract ladder, the gate invariant enforcement, and the full validate rule set. Remaining Phase 1 handlers are more mechanical work against a frozen contract on a proven spine.

---

## What is actually built (verified in code)

**Live handlers** (real, not stubs): `backfill.scan`, `extract.doc`, `validate.doc`, `gate.candidate`, `gate.apply`. That is the whole Phase 0 spine plus the S1.0 validate engine, proven end-to-end by the DB-proof run.

**No-op stubs only** (registered so the enqueue graph doesn't break): `ingest.run`, `resolve.group`, `discover.group`, `fetch.url`, `email.*`, `eudamed.sync`, `playbook.reonboard`.

**Present:** queue core, migrations 001-010 (Phase 2 declared-only), extract tiers (real 342-line T0 + T1/T2 + batch protocol), economics ledger, `web/` producer UI (status board + ingest/enqueue + staging/manual/dead tabs, live-verified against `gate.apply`).

**Absent:** `app/scheduler.py`, `app/api.py`, any real `adapters/` implementation (dir is `__init__.py` only), `playbooks/`, the S1.5 T0 *layout-template* engine (`t0_templates.py` is the deterministic-regex T0, not the anchor/region engine).

---

## Phase-by-phase

| Phase | Sessions | State |
|---|---|---|
| **Phase 0 — spike** | S0.1-S0.4 | **100% DONE.** Queue, extract, gate, manual_task, validate stub, backfill, DB-proof spine run + findings memo. |
| **Phase 1 — LJ core** | S1.0-S1.7 | **~25%.** S1.0 VALIDATE done (full C1-C7). S1.6 UI ~65% (tabs shipped + live-verified; no KPI board, no read-API-per-spec, no auth, route naming unreconciled = G16). S1.1 INGEST, S1.2 RESOLVE, S1.3 DISCOVER, S1.4 FETCH, S1.5 SCHEDULER+templates, S1.7 sweep not started. |
| **Phase 2 — ZG + playbooks** | S2.1-S2.5 | **0%.** No playbooks, EUDAMED, email, or Zagreb adapter. |

---

## Health rating: strong (A-)

Disciplined for this stage:
- 357 tests against real Postgres; table-driven for queue, gate, and validate as CLAUDE.md mandates.
- Invariants enforced structurally (the API role has zero registry write grants, tested by attempting an insert), not by convention.
- Cost target proven, not assumed: $0.028/doc measured, well inside the EUR 10-40/mo envelope.
- Honest, current gap register and followups (the docs flag their own staleness, e.g. G16).

The risk has shifted from "can we build the machine" to "will the data and the open web support the acceptance target."

---

## Open risks (ordered)

1. **mfr_ref is a much weaker signal than the design assumed** — ~8% genuinely distinct, 44.5% blank. AC1 ceiling re-derived at ~8% strict / ~25% per-supplier-mapping. **Needs Denis/client sign-off** before Phase 1 acceptance means anything (G4/G15).
2. **No live DISCOVER/FETCH yet.** The "can we actually find these docs on the open web / HTML doc-libraries" risk (G6) is entirely unretired. Everything proven so far runs on the backfill corpus, not live acquisition.
3. **SFTP source has real operational hazards logged** (WeOnlyDo attribute bug, connection drops, UTF-8 mangling) before an adapter can even be designed.
4. **Acceptance-criteria registry (G15) still undefined** beyond AC1/AC5, and Phase 1 "done" depends on it.

---

## Single most valuable next move

Not more code. Get **Denis/client sign-off on the AC1/mfr_ref ceiling and the manufacturer-code sweep** (the two items blocking a meaningful Phase 1 acceptance definition). Then start **S1.4 FETCH or S1.3 DISCOVER** to retire the untested "live acquisition works" risk. Building S1.1/S1.2 first would be lower-risk but leaves the biggest unknown standing.
