"""Placeholder handlers — every queue tag has one until its stage lands.

Importing this module registers a placeholder for every job type; real handlers
replace them module by module and override the placeholder here (last
registration wins). **As of 2026-09-07 all 21 are real** -- the placeholder is
reached by nothing, and the module stays for the gap between the migration that
adds a tag and the handler that serves it, which is exactly when a silent no-op
would do the damage.

The prose here no longer carries a count of what is unbuilt, because it kept
going stale: the vendor commit (2026-08-26) touched one line of this file, the
tuple entry, and left the prose saying sixteen; the 2026-09-07 BC commit left it
saying nineteen of twenty while the tuple below listed `bc.push` as the
twentieth. `tests/test_runner.py::test_noop_job_types_match_the_db_enum` now
holds the tuple to the enum, so the SET cannot drift even where the prose does.

History, for why a tag is or is not here: `playbook.reonboard` was the last
placeholder, until its probe branch landed 2026-09-03
(`app/handlers/playbook_probe.py`); the failure-monitor branch of that same tag
still raises from inside the real handler rather than from here. `eudamed.sync`
left the list 2026-08-25 with `app/handlers/eudamed.py`; `email.request` and
`email.reminder` left it with S2.4. `vendor.import`, `eudamed.certregister`,
`eudamed.sweep` and `bc.push` each arrived with a handler already built and were
never reached by the placeholder, though they are listed below like every other
tag -- `setdefault` makes that harmless, and leaving a tag out is the failure
this module exists to prevent.

**A placeholder RAISES; it does not succeed.** It used to log and return, which
made the job finish `done` -- indistinguishable from work actually performed.
Three producers already exist for two of the unbuilt tags, each behind a config
flag that defaults off: `scheduler.py` emits `email.request` under
`expiry_email_enabled` and `playbook.reonboard` under
`failure_reonboard_enabled`, and `discover.py` emits `email.request` under
`email_rung_enabled`. Flipping any one of those before its consumer exists
would have silently swallowed renewal requests, and CLAUDE.md is explicit that
a job whose loss loses work is a design bug. Raising sends it to the dead-jobs
board instead, where it is visible and re-enqueueable once the handler lands
(the queue is regenerable state; the registry is truth).

The original S0.1 reason for succeeding -- proving the spine ran end to end
before any stage existed -- expired when the stages landed.
"""

from __future__ import annotations

import logging

from app.handlers import HANDLERS, register

log = logging.getLogger("dentalia.handler.noop")

#: The closed job_type set. Held to the real enum by
#: `tests/test_runner.py::test_noop_job_types_match_the_db_enum` -- 001 is no
#: longer the whole story, and `scheduler.tick` is what proved it: added by
#: migration 054, never added here, unnoticed because its real handler always
#: imported.
JOB_TYPES = (
    "ingest.run",
    "resolve.group",
    "discover.group",
    "fetch.url",
    "extract.doc",
    "validate.doc",
    "gate.candidate",
    "gate.apply",
    "backfill.scan",
    "email.poll",
    "email.request",
    "email.reminder",
    "eudamed.sync",
    "eudamed.certregister",
    "eudamed.sweep",
    "playbook.reonboard",
    "report.weekly",
    "upload.ingest",
    "vendor.import",
    "bc.push",
    "scheduler.tick",
)


class UnimplementedStage(Exception):
    """A job reached a queue tag whose handler has not been built."""


def _noop(conn, job: dict) -> None:
    """Fail loudly. See the module docstring for why this does not return."""
    log.error(
        "unimplemented stage: job id=%s type=%s has no handler; dead-lettering "
        "rather than reporting success", job["id"], job["type"]
    )
    raise UnimplementedStage(
        f"{job['type']}: no handler is built for this queue tag. The job is kept "
        f"in dead-jobs rather than silently finished; re-enqueue it once the "
        f"stage lands."
    )


# `setdefault`, NOT `register`: a placeholder must never overwrite a real
# handler. `register` is unconditional, so this module clobbered whatever was
# already registered whenever it happened to be imported second -- which is
# exactly what pytest's collection order does, and it put `extract.doc` and
# `backfill.scan` on the placeholder for the whole suite. `runner.py` imports
# noop first and so never saw it, and while the placeholder merely logged and
# returned the symptom was a stage that silently did nothing. Registration
# order is now irrelevant in both directions: a real handler imported first
# survives this loop, and one imported later still overrides via `register`.
for _t in JOB_TYPES:
    HANDLERS.setdefault(_t, _noop)
