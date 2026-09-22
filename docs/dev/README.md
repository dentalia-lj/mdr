# Developer documentation

**Status:** live, started 2026-08-31.

## What this folder owns, and what it does not

These files **link into** the maintained docs one level up. They never restate
them.

| Topic | Owner | Not here |
|---|---|---|
| Contracts: stages, job types, payloads, drift guards | [dentalia-pipeline-contract-prd-v3.md](../dentalia-pipeline-contract-prd-v3.md) — **normative** | — |
| Schema, tag→table access matrix | [dentalia-schema-sketch.md](../dentalia-schema-sketch.md) | — |
| Per-tag pseudo-code, payload examples | [dentalia-job-type-handbook.md](../dentalia-job-type-handbook.md) | — |
| System overview, write-path separation, tier ladder | [architecture.md](../architecture.md) | — |
| Path → purpose → tests, one line per module | [code-map.md](../code-map.md) | — |
| Deploy, compose, routes, CLI (local dev and day-to-day) | [runbook.md](../runbook.md) | First install on a client server, which is [deployment.md](deployment.md) |
| Known failure modes | [troubleshooting.md](../troubleshooting.md) | — |
| The code's own vocabulary, drift-guarded by a test | [vocabulary.md](../vocabulary.md) | — |
| Test wrapper, xdist choice, portability | [test-infra.md](../test-infra.md) | — |
| Why a decision was taken, and what was rejected | [dentalia-mdr-pipeline-ground-truth.md](../dentalia-mdr-pipeline-ground-truth.md) — §7 before touching extraction or validation | — |
| Current scope, gaps, decision log | [PHASES.md](../../PHASES.md) | — |

**If a file here disagrees with one of those, the other document wins and this
one is a bug.**

## What is here

| File | Owns |
|---|---|
| `00-orientation.md` | Clone to first shipped change |
| `01-lifecycle.md` | One document end to end, as built rather than as designed |
| [handlers.md](handlers.md) | Index over the 19 queue tags: where, consumes, writes, emits, fails how, tests |
| `web.md` | The web layer for developers |
| `extraction.md` | The T0→T1→T2→T3 ladder as built |
| `config-reference.md` | Every config key: default, env var, what it controls, blast radius |
| `playbook-authoring.md` | Onboarding a manufacturer, start to first document |
| `changing-things.md` | Adding a job type, migration, config key, route, table |
| `limits.md` | What the system will not do, and why |
| [deployment.md](deployment.md) | First install on a client server: order, source combinations, corpus load, handover checklist |

Files not yet written are listed above so the gap is visible rather than silent.

## Why this folder exists at all

Audited 2026-08-31. The docs one level up are complete and maintained, but every
one of them is written for somebody who already knows the system. Four things had
no owner anywhere:

- A narrative route from clone to first change.
- A single config reference. Env vars are documented ad hoc across the runbook
  and troubleshooting; `app/config.py` and PRD §11 are the truth and are
  referenced but never reproduced.
- A manufacturer-onboarding walkthrough, currently split across four design docs
  and the `/playbooks` route notes.
- A written list of what is deliberately not built.

## The client guide

`docs/guide/` documents the web UI for Dentalia's compliance staff. Different
audience, different rules, and it is not a shortcut to understanding the system —
it deliberately hides every mechanism. See
[the design spec](../superpowers/specs/2026-08-31-documentation-set-design.md).

Rebuild the HTML with `python3 scripts/build-guide.py` after any change — see
[runbook.md](../runbook.md#client-guide-docsguide). Editing a page in one
language only, or leaving the bundles stale, fails `tests/test_docs_sets.py`.

One thing there is a developer's problem: the UI prints internal vocabulary at
the user, so every client page carries a translation table. Logged in
`tasks/followups.md` as `[ui-wording]`. The real fix is in the templates.
