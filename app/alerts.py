"""Push one line off this machine when something needs a person.

**Why this exists.** Nothing in `app/` or `web/` could reach a human. A dead
worker showed as a red chip inside a password-protected page and a stale
`service_heartbeat` row -- both PULL, both requiring somebody to go and look.
Measured 2026-09-04: zero hits for slack, webhook, pagerduty, ntfy, telegram or
any SMTP client across the tree. A job that dead-lettered on a Friday evening
sat unseen until someone opened `/dead`.

**Not the email feature.** `email.request` writes supplier drafts a person
sends by hand (ruling 2026-08-20, "this system never sends"). That ruling is
about correspondence with manufacturers. This is an operator alert to the
person running the system, it carries no registry content beyond an identifier,
and it goes to one endpoint the operator configured. Keep the two apart: an
alert must never become a channel for supplier mail.

**It cannot break the caller.** Every failure -- unset URL, DNS, timeout, a 500
from the endpoint -- is swallowed and logged. An alert is a courtesy on top of
work that already succeeded or already failed; a queue that dead-letters
correctly and then raises inside its own notification would turn a contained
failure into an uncontained one. That is why `notify` returns a bool nobody has
to check rather than raising.

**Generic POST, not an ntfy client.** The body is the message and the headers
carry the title and priority, which is ntfy's contract; Slack and Discord read
JSON instead and would need their own shape. Only ntfy is wired because only
ntfy needs no credential -- see `docs/dev/config-reference.md`. If a second
backend is ever wanted, it belongs behind this same function, not at the call
sites.

**The topic is the secret.** An ntfy topic is world-readable and world-writable
to anyone who knows its name, and names are enumerable. A short one leaks job
failures and manufacturer names to whoever guesses it, and lets them post
alerts that look like ours. Use a long random topic; the config reference says
so too.
"""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger("dentalia.alerts")

#: Kept short on purpose. This runs on the failure path, sometimes while a
#: worker is already unhealthy, and must never be the thing that hangs.
_TIMEOUT_S = 5.0

#: ntfy's own vocabulary. `high` reaches a phone through a do-not-disturb
#: window; `default` does not. Reserve `high` for "a person must act", not for
#: "a person will want to know".
LOW = "low"
DEFAULT = "default"
HIGH = "high"


def notify(webhook_url: str | None, title: str, message: str,
           *, priority: str = DEFAULT, tags: str = "") -> bool:
    """POST one alert. Returns whether it was delivered; never raises.

    `webhook_url` empty or None is the normal unconfigured case and is a silent
    no-op, not a warning: most environments (tests, a developer's laptop, CI)
    have no endpoint and should not fill their logs saying so.
    """
    if not webhook_url:
        return False
    headers = {"Title": title, "Priority": priority}
    if tags:
        headers["Tags"] = tags
    try:
        resp = httpx.post(webhook_url, content=message.encode("utf-8"),
                          headers=headers, timeout=_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 - an alert must not break the caller
        log.warning("alert not delivered (%s): %s", type(exc).__name__, exc)
        return False
    if resp.status_code >= 400:
        # The body, not just the code: ntfy explains a rejected topic name or a
        # rate limit in it, and without that line the only symptom is silence.
        log.warning("alert rejected: HTTP %s %s", resp.status_code, resp.text[:200])
        return False
    return True
