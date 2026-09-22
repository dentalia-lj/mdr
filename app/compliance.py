"""What Dentalia must hold for a device, and how to colour what it holds.

The rule that used to live in one person's head and in the shape of a folder
tree. A folder can show you what is in it; it cannot tell you what should be.
This module is the "should be", written down.

**Not configuration.** This deliberately does not live in `app/config.py`:
everything there is overridable by an env var or a TOML overlay, and a legal
obligation that can be switched off with `export` is not an obligation.
Changing these rules is a code change with a test diff (`tests/test_compliance.py`,
which carries the full regulatory citation for every row).

Dentalia is a **distributor**, not a manufacturer, and that decides the whole
matrix. MDR Art. 14(2) gives a distributor four verification duties — CE mark
plus EU declaration of conformity drawn up, the Art. 10(11) information (label
and instructions for use), importer compliance, and a UDI assigned. The
notified-body certificate is not among them; ECJ C-10/24 (4 June 2026)
narrowed the first duty further, to checking that the four-digit NB number is
present where the documentation indicates a notified body was involved. So the
certificate and the ISO 13485 registration are evidence we are glad to hold,
never rows anyone can mark us down on.

UDI is an Art. 14(2)(d) duty and is deliberately **not** scored here (Denis,
2026-08-24): the item side holds none at all, and a third all-red row teaches
the reader to ignore the card.
"""

from __future__ import annotations

from datetime import date

__all__ = [
    "ROW_DOC",
    "ROW_IFU",
    "ROW_NB",
    "ROW_EC",
    "ROW_ISO",
    "SCORED_ROWS",
    "EVIDENCE_ROWS",
    "ROW_LABELS",
    "REQUIRED",
    "CONDITIONAL",
    "NOT_APPLICABLE",
    "UNKNOWN",
    "EVIDENCE",
    "EXPIRY_HORIZON_DAYS",
    "BUCKETS",
    "bucket_for",
    "requirements_for_class",
    "cell_state",
    "worst_severity",
]

# --------------------------------------------------------------------------- #
# rows
# --------------------------------------------------------------------------- #
# The first four match `document.type` exactly, so a query can key straight off
# the registry vocabulary. ROW_NB is the odd one out: it is derived from the
# declaration (does it print a notified-body number?), not a document type.
ROW_DOC = "DoC"
ROW_IFU = "IFU"
ROW_NB = "nb_number"
ROW_EC = "EC"
ROW_ISO = "ISO"

#: Rows that can be red. These are the Art. 14 duties.
SCORED_ROWS = (ROW_DOC, ROW_IFU, ROW_NB)

#: Rows that are worth showing and never scored. Holding them is good practice
#: and useful leverage with a supplier; not holding them breaches nothing a
#: distributor is answerable for.
EVIDENCE_ROWS = (ROW_EC, ROW_ISO)

ROW_LABELS = {
    ROW_DOC: "Declaration of conformity",
    ROW_IFU: "Instructions for use",
    ROW_NB: "Notified-body number",
    ROW_EC: "EC certificate",
    ROW_ISO: "ISO 13485",
}

# --------------------------------------------------------------------------- #
# requirement levels
# --------------------------------------------------------------------------- #
#: Must be held. Missing is red.
REQUIRED = "required"
#: Required unless the Annex I 23.1(d) exception applies — class I and IIa
#: devices that can be used safely without instructions, justified by risk
#: analysis in the technical documentation. Missing is amber: go and check
#: whether that justification exists, not "you are in breach".
CONDITIONAL = "conditional"
#: The class rules this row out entirely. Grey.
NOT_APPLICABLE = "not-applicable"
#: BC has not told us the device class, so we cannot say. Amber — answering it
#: green would be a guess about a compliance question.
UNKNOWN = "unknown"
#: Shown, never scored.
EVIDENCE = "evidence"

#: How far ahead an expiry counts as "expiring". 30 days is Dentalia's own
#: ruling of 2026-08-18 ("bi rekla mesec dni prej"), and it must stay equal to
#: `app.config.Renewal.horizon_days` -- the scheduler chases renewals on that
#: clock, and a card warning on a different one would tell a reader the document
#: is fine on a day the system is already chasing it. Guarded by
#: `tests/test_compliance.py::test_expiry_horizon_follows_dentalias_own_renewal_window`.
#: Unlike the matrix above this IS a preference, not a legal rule; it lives here
#: only so the two numbers sit next to their guard.
EXPIRY_HORIZON_DAYS = 30

# Art. 52(7): class I is self-declared, except where the device is sterile (a),
# has a measuring function (b), or is a reusable surgical instrument (c) — then
# a notified body is involved for that aspect alone and issues a certificate,
# whose number the declaration must carry. For (c) the notified body's remit
# explicitly covers "the related instructions for use", which is why Ir cannot
# take the Annex I 23.1(d) exception the way plain I and IIa can.
_BY_CLASS: dict[str, dict[str, str]] = {
    "I": {ROW_DOC: REQUIRED, ROW_IFU: CONDITIONAL, ROW_NB: NOT_APPLICABLE},
    "Is": {ROW_DOC: REQUIRED, ROW_IFU: CONDITIONAL, ROW_NB: REQUIRED},
    "Im": {ROW_DOC: REQUIRED, ROW_IFU: CONDITIONAL, ROW_NB: REQUIRED},
    "Ir": {ROW_DOC: REQUIRED, ROW_IFU: REQUIRED, ROW_NB: REQUIRED},
    "IIa": {ROW_DOC: REQUIRED, ROW_IFU: CONDITIONAL, ROW_NB: REQUIRED},
    "IIb": {ROW_DOC: REQUIRED, ROW_IFU: REQUIRED, ROW_NB: REQUIRED},
    "III": {ROW_DOC: REQUIRED, ROW_IFU: REQUIRED, ROW_NB: REQUIRED},
}

# A class BC has never sent before, or none at all. The declaration duty does
# not depend on the class, so it survives; everything the class decides falls
# through to UNKNOWN rather than to a default that reads as compliant.
_UNKNOWN_CLASS = {ROW_DOC: REQUIRED, ROW_IFU: UNKNOWN, ROW_NB: UNKNOWN}


def requirements_for_class(product_class: str | None) -> dict[str, str]:
    """Requirement level per row for one device class (BC's `product_class`)."""
    scored = _BY_CLASS.get((product_class or "").strip(), _UNKNOWN_CLASS)
    return {**scored, ROW_EC: EVIDENCE, ROW_ISO: EVIDENCE}


# --------------------------------------------------------------------------- #
# rollup buckets
# --------------------------------------------------------------------------- #
#: How a whole manufacturer's devices are counted for one row. NOT severity:
#: `expired`, `superseded` and `missing` are all severity `bad`, so tallying by
#: severity collapsed them into one column. Rendered under a heading that said
#: "Missing" it read 1.050 for IVOCLAR where 16 articles actually had none --
#: one line under a headline that said 16.
BUCKETS = ("held", "attention", "lapsed", "none", "not_required")

_STATE_BUCKET = {
    "held": "held",
    "expiring": "attention",
    "review-due": "attention",
    "review": "attention",
    "mfr-scope-only": "attention",
    "class-unknown": "attention",
    "expired": "lapsed",
    "superseded": "lapsed",
    "missing": "none",
    "missing-exception-possible": "none",
    "not-required": "not_required",
}


def bucket_for(state: str, requirement: str) -> str:
    """Which column of the manufacturer card one cell falls in."""
    # An ISO 13485 or EC certificate is issued to the manufacturer and to
    # nothing else -- manufacturer scope is the only form it comes in, so for
    # those rows it is the document behaving exactly as designed. Counting it
    # as "needs attention" put every one of a supplier's articles under an
    # alarm for a correct document. The same holding on a DECLARATION stays a
    # warning, because there manufacturer scope really is the weaker evidence.
    if requirement == EVIDENCE and state == "mfr-scope-only":
        return "held"
    return _STATE_BUCKET[state]


# --------------------------------------------------------------------------- #
# cell state
# --------------------------------------------------------------------------- #
_SEVERITY_ORDER = ("none", "ok", "warn", "bad")


def worst_severity(severities) -> str:
    """The severity a group of cells rolls up to. Empty rolls up to `none`."""
    worst = "none"
    for s in severities:
        if _SEVERITY_ORDER.index(s) > _SEVERITY_ORDER.index(worst):
            worst = s
    return worst


def _cap(severity: str, requirement: str) -> str:
    """Evidence rows never reach `bad` — nothing a distributor is answerable
    for turns on them."""
    if requirement == EVIDENCE and severity == "bad":
        return "warn"
    return severity


def cell_state(
    requirement: str,
    held: dict | None,
    *,
    today: date,
    horizon_days: int = EXPIRY_HORIZON_DAYS,
) -> dict:
    """One cell of the completeness card.

    `held` is `None`, or the best holding for that row:
    `{doc_status, link_status, match_basis, expires}` — statuses straight from
    the registry, `expires` already resolved through `document_effective_expiry`
    by the caller, `match_basis` from `item_document`.

    Returns `{"state", "severity"}`. `severity` is one of `none` / `ok` /
    `warn` / `bad` and is the only thing the templates colour by; `state` is
    the word the reader sees.
    """
    if held is None:
        if requirement == REQUIRED:
            return {"state": "missing", "severity": "bad"}
        if requirement == CONDITIONAL:
            return {"state": "missing-exception-possible", "severity": "warn"}
        if requirement == UNKNOWN:
            return {"state": "class-unknown", "severity": "warn"}
        if requirement == NOT_APPLICABLE:
            return {"state": "not-required", "severity": "none"}
        return {"state": "missing", "severity": "none"}

    doc_status = held.get("doc_status")
    link_status = held.get("link_status")

    # Supersession flips the document and never the link, so a superseded
    # document is still reachable through a production link. It is history,
    # not current evidence.
    if doc_status == "superseded":
        return {"state": "superseded", "severity": _cap("bad", requirement)}

    # Anything not fully production is a decision someone still owes.
    if doc_status != "production" or link_status != "production":
        return {"state": "review", "severity": "warn"}

    expires = held.get("expires")
    if expires is not None:
        if expires < today:
            # A declaration of conformity has no expiry under MDR -- Annex IV
            # requires only an issue date. When the only date we have is
            # Dentalia's own five-year review horizon (`basis='staleness'`,
            # migration 027: "a review horizon, not a legal expiry"), a passed
            # date means the declaration is due a look, not that the article is
            # non-compliant. 139 of the registry's declarations lapse on that
            # basis (2026-08-24) -- a minority today, and the branch has to
            # exist anyway: reporting a house rule as a breach is the one error
            # this card cannot afford to make.
            if held.get("expiry_basis") == "staleness":
                return {"state": "review-due", "severity": "warn"}
            return {"state": "expired", "severity": _cap("bad", requirement)}
        if (expires - today).days <= horizon_days:
            return {"state": "expiring", "severity": "warn"}

    # The trap this card exists for. On 2026-08-24 one ISO 13485 certificate
    # bound 2.567 Carl Martin articles by `mfr-scope` and the coverage figure
    # read 99,7%. A claim about the supplier's quality system is not evidence
    # about the article, so it can report, but it can never be green.
    if held.get("match_basis") == "mfr-scope":
        return {"state": "mfr-scope-only", "severity": "warn"}

    return {"state": "held", "severity": "ok"}
