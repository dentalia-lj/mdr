# S0.3 review-UI proposal (G3 auth + G16 reconciliation)

For a separate frontend session. The S0.3 **backend** (VALIDATE stub + GATE) is
built and committed; this specifies what the UI reads, what it produces, and the
two open UI gaps. The UI is a **job producer only** — `dentalia_api` has zero
write grants on the registry (inv. 1), so every mutation goes through a
`gate.apply` enqueue, never a direct write. A write attempt as that role is
rejected by the DB (already tested).

## Backend contract now available

**Reads (SELECT granted to `dentalia_api`):** `document`, `item_document`,
`evidence`, `manual_task`, and the `item_document_production` view.

**Produces (INSERT/UPDATE on `job` granted):** `gate.apply` jobs. Exact payloads
from `app/handlers/gate.py::handle_gate_apply`:

| decision | payload | effect |
|---|---|---|
| `approve` | `{doc_id, decision:"approve", decided_by, edits?}` | promote doc + trusted-basis staged links; `edits` land as T3 evidence (conf 1.0) and update the column |
| `reject` | `{doc_id, decision:"reject", decided_by, group_id?}` | mark rejected; `group_id` enqueues an interactive `discover.group` retry |
| `bind-manufacturer` | `{doc_id, decision:"bind-manufacturer", decided_by, manufacturer}` | promote + derive `mfr-scope` production links to all MD items of the manufacturer |
| `confirm-link` | `{doc_id, item_ref, decision:"confirm-link", decided_by}` | C17. Re-base ONE link to `match_basis='manual'` + `production`. Sole writer of `manual`. Document untouched |
| `reject-link` | `{doc_id, item_ref, decision:"reject-link", decided_by}` | C17. Mark ONE link `rejected`, basis kept so the trail records how it was proposed. Machines cannot undo it |
| `reopen-link` | `{doc_id, item_ref, decision:"reopen-link", decided_by}` | C17. Return ONE `rejected` link to `staged` — the only route out of `rejected`, human-only |

`edits` editable fields (whitelist): `type, regulation, validity_from,
validity_to, coverage_scope, basic_udi_di, cert_number`. Any other key → the
handler raises. `decided_by` MUST be a real identity — it lands in
`audit_log.decided_by` (10-y retention). Every decision also resolves the doc's
open `manual_task` — **except the three C17 link decisions**, which settle nothing
about the document and so resolve no task and write no `production-write` for it.
All three require the document to be `production` already and raise otherwise;
their dedupe key carries the item (`apply:{doc_id}:{item_ref}:{decision}`), since
two items under one document are two decisions.

## Staging tab (done-criterion: staging review with per-field evidence)

- List `document WHERE status='staged'`, plus staged `item_document` links and
  binding candidates. A staged link under a PRODUCTION document is its own
  decision, not a symptom of the document's (C17): approving the document never
  publishes a capped-basis link, so those rows carry their own buttons. For each field, render its `evidence` row (page, verbatim,
  tier, confidence) — the evidence join is by `doc_id`.
- Actions → enqueue `gate.apply` per the table above. Suggested dedupe key:
  `apply:{doc_id}:{decision}` (interactive priority).

## Manual tab (done-criterion: manual disposition visible in a list)

- List `manual_task WHERE status='open' ORDER BY created_at` (uses the partial
  index `manual_task_open_idx`). `kind='gate-manual'` payloads carry either
  `{flags, tier_attempts}` (all tier attempts, for a low-confidence disposition)
  or `{route:'mfr-binding', manufacturer}` (a binding decision).
- Resolution = enqueue the matching `gate.apply`; the handler flips the task to
  `resolved` (`resolved_by`, `resolved_at`). The UI never updates `manual_task`
  directly.

## G3 — UI/API auth v0 (needs Denis's decision)

The current `web/` slice is localhost-only, no auth. Proposed v0:

- **HTTP Basic behind a reverse proxy** (caddy/nginx, TLS terminated there), a
  single shared credential; `decided_by` = the basic-auth username, threaded
  into every `gate.apply` payload so the audit trail carries a real identity.
- JSON read API (S1.6): a static bearer token in v0.
- No-proxy alternative: a FastAPI dependency doing HTTP Basic against an
  env-configured credential.
- Full OIDC/IdP is overkill for an internal, low-user-count Phase 0-1 review UI.

## G16 — reconcile the pulled-forward `web/` slice with the real UI

- The slice's routes are `/ingest` + `/enqueue`; S1.6 specs `POST /jobs/ingest`.
  Pick one; fold or drop the generic `/enqueue`.
- Add the tabs this backend now supports: **staging** (evidence + approve/reject/
  edit/bind), **manual** (open `manual_task`), **dead-jobs** (`job.status='dead'`
  + re-run at interactive priority).
- Keep the producer-only contract: all registry mutation via `gate.apply`; the
  DB grant already enforces it.
- KPI board (S1.6 §B) is out of S0.3 scope.
