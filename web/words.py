"""One vocabulary for the whole UI (office UI redesign spec § 9).

Rule 2 of the eight: *one word per thing*. The menu, the page title, the status
pill, the button and the guide all use the same word, and the mapping lives
here — in ONE place — rather than being spelled again in each template that
happens to render a status.

Nothing here changes what is stored. `document.status` is still
`production|staged|filed|superseded|rejected`, a dead job is still `dead`, and
every query, payload and API response keeps the stored value. This module is
the last step before a value reaches a person's eye.

The core of it is four things, and each of them is a screen word, not a
datum (the narrower vocabularies added since -- audit events, discovery
rungs, document types, renewal-draft states -- have their own section
below, and their own reason for not being folded into `WORDS`):

* `WORDS` / `word()` — the § 9 table. Unknown values pass through unchanged: a
  label added to an enum must show as itself, because a word list that swallowed
  it would hide exactly the thing that needs mapping.
* `doc_display_name()` — rule 1, name first and number second.
* `day_text()` / `week_label()` — a date and a week as a person reads them.
* `num()` — a count with a thousands separator.

Every one of them is a pure function of its argument. Nothing here reads the
database or the request, so a template filter can call it and a handler can
call it and neither has to know about the other.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta

# --------------------------------------------------------------------------- #
# the word list (spec § 9)
# --------------------------------------------------------------------------- #
#: Stored value (lower-cased) -> the word on screen.
#:
#: Keys are the machine-side spellings a screen might hold: enum labels
#: (`production`, `staged`, `dead`), the old menu entries (`drafts out`,
#: `emails in`), and the field names a technical panel still prints
#: (`mfr_ref`, `match basis`). Several keys deliberately share one word —
#: "article" and "product" both became "Item" — because the point of the table
#: is that the reader meets one word, whichever of ours they came in through.
#:
#: Three rows of § 9 are NOT here, on purpose:
#:  * the three `manual_task` kinds are marked "(not shown)" — each is rendered
#:    by the page that resolves it and never named as a kind;
#:  * "Target group id" and "Priority" are marked "(removed)" — they left the
#:    Upload form entirely (Task 3);
#:  * "Item no." is the label for a REF and is written where the ref is shown,
#:    not derived from a stored value.
WORDS: dict[str, str] = {
    # document.status
    "production": "Published",
    "staged": "Waiting for review",
    "filed": "On file",
    "superseded": "Replaced",
    "rejected": "Rejected",
    # job and queue state
    "dead": "Failed",
    "dead jobs": "Failed",
    "dead job": "Failed",
    "processing": "Being read",
    "in flight": "Being read",
    "in_flight": "Being read",
    # the lists themselves
    "manual": "Missing documents",
    "manual queue": "Missing documents",
    "drafts out": "Renewal emails",
    "renewal drafts": "Renewal emails",
    "chase": "Renewal emails",
    "emails in": "Emails received",
    # The office lands on Today, which is a screen of its own and not a
    # translation of anything stored; the board it replaced keeps the operator
    # name it carries in the menu.
    "status": "System status",
    "queue status": "System status",
    "kpi board": "System status",
    # EUDAMED
    "sweep": "EUDAMED check",
    "release": "Start the check",
    "sweep due": "Check due",
    "srn": "EUDAMED ID (SRN)",
    # playbooks
    "recipe": "Playbook",
    "playbook": "Playbook",
    "slug": "Short name",
    # manufacturers
    "canonical entity": "Manufacturer",
    "alias source": "Name in Business Central",
    "vendor-master": "Name in Business Central",
    # the catalogue
    "item": "Item",
    "article": "Item",
    "product": "Item",
    "mfr_ref": "Manufacturer's article no.",
    "match basis": "How it was matched",
    "match_basis": "How it was matched",
}


def word(value: str | None) -> str:
    """The screen word for a stored value.

    Unknown values pass through unchanged and `None` becomes an empty string,
    so this is safe to put in front of any column: it can only ever improve a
    value, never lose one.
    """
    if value is None:
        return ""
    return WORDS.get(str(value).strip().lower(), value)


# --------------------------------------------------------------------------- #
# the narrower vocabularies, each in its OWN namespace
# --------------------------------------------------------------------------- #
# `WORDS` above is one flat namespace over stored values and menu labels
# together, which is exactly why none of the ones below is folded into it.
# `filed` is already a document status there ("On file"); as an audit event it
# means something else entirely ("the system filed this, it covers no item"),
# `manual` is a rung as well as a list, and `draft` and `sent` are ordinary
# English that a third vocabulary could claim tomorrow. A second meaning added
# to a flat table does not extend it, it corrupts it, so each of these gets its
# own dict and its own accessor, the same split `DOC_TYPE_WORDS` and
# `DOC_TYPE_SUBJECTS` already use one section down.
#
# Own namespace is not the same as own module. Each of these is read by more
# than one screen, so all of them live HERE: a map kept beside the one route
# that first needed it is a map the second screen re-spells by hand.

#: What `audit_log.event` says on the Decisions page.
#:
#: Every value the codebase writes: the four human decisions and the
#: consequence rows GATE writes beside them (`app/handlers/gate.py`), plus the
#: three repair CLIs (`app/repair_*.py`). The stored key stays the filter's
#: value and reads under Technical details on each row. A machine-side name
#: is what somebody greps for, and this is what they READ.
AUDIT_EVENT_WORDS: dict[str, str] = {
    # a person's four decisions (`gate.apply`)
    "approve": "Approved",
    "reject": "Rejected",
    "reopen": "Reopened for review",
    "bind-manufacturer": "Manufacturer confirmed",
    # what GATE writes beside them, and on its own machine route
    "production-write": "Published",
    "filed": "Filed, covers no item here",
    "supersede": "Replaced by a newer document",
    "auto-superseded": "Filed as already replaced",
    "link-confirmed": "Item link published",
    "link-rejected": "Item link rejected",
    "link-reopened": "Item link reopened",
    "link-retracted": "Item link withdrawn",
    # the repair CLIs
    "manufacturer-backfilled": "Manufacturer filled in",
    "archive-url-repair": "Archived file re-addressed",
    "task-reason-backfilled": "Review reason filled in",
}

#: Where DISCOVER looked, as a place rather than a rung name.
#:
#: The keys are `app.playbooks.DISCOVER_RUNGS`, all eight. `web/missing.py`
#: holds the SENTENCE forms of the same two rungs a dead-end card can name
#: ("the manufacturer's known pages"); these are the LABEL forms, for a table
#: cell. Same split, same reason, as `DOC_TYPE_WORDS` / `DOC_TYPE_SUBJECTS`:
#: lower-casing a label does not make a phrase that reads inside a sentence,
#: and title-casing a phrase does not make a column heading.
RUNG_WORDS: dict[str, str] = {
    "recency": "A document we already had",
    "known_url": "An address we had before",
    "playbook": "The manufacturer's known pages",
    "eudamed": "EUDAMED",
    "search": "The web",
    "email": "An email to the manufacturer",
    "manual": "A person",
    "vendor": "The supplier's own site",
}

#: What one rung came back with.
RUNG_OUTCOME_WORDS: dict[str, str] = {
    "hit": "Found something",
    "miss": "Nothing found",
    "skipped": "Not tried",
}


#: A renewal email's own state, and its errand (spec § 9, P7c).
#:
#: `email_draft.status` and `email_draft.kind`. Their own namespaces rather
#: than rows of `WORDS`, for the reason the block above gives: `draft` and
#: `sent` are ordinary English that other vocabularies could claim.
#:
#: `cancelled` is the one that had to move: the button has said "Archive this
#: draft" since 2026-09-03 while the pill said "cancelled", which is rule 2
#: broken on one screen. The STORED value is untouched -- renaming a value
#: nothing else reads would buy a migration and no clarity.
DRAFT_STATUS_WORDS: dict[str, str] = {
    "draft": "Draft",
    "ready": "Ready to send",
    "sent": "Sent",
    "cancelled": "Archived",
}

#: What a renewal email is asking for.
DRAFT_KIND_WORDS: dict[str, str] = {
    "request": "First ask",
    "reminder": "Reminder",
    "gap-request": "First ask, nothing on file",
}


def draft_status(value: str | None) -> str:
    """A renewal email's state as a person reads it. Unknown states pass
    through, so a value added to the enum shows as itself."""
    if not value:
        return ""
    return DRAFT_STATUS_WORDS.get(value, value)


def draft_kind(value: str | None) -> str:
    """What a renewal email is asking for. Unknown kinds pass through."""
    if not value:
        return ""
    return DRAFT_KIND_WORDS.get(value, value)


def audit_event(value: str | None) -> str:
    """An audit event as a person reads it. Unknown events pass through."""
    if not value:
        return ""
    return AUDIT_EVENT_WORDS.get(value, value)


def rung(value: str | None) -> str:
    """A discovery rung as the place it looked. Unknown rungs pass through."""
    if not value:
        return ""
    return RUNG_WORDS.get(value, value)


def rung_outcome(value: str | None) -> str:
    """What a rung came back with. Unknown outcomes pass through."""
    if not value:
        return ""
    return RUNG_OUTCOME_WORDS.get(value, value)


# --------------------------------------------------------------------------- #
# document type words
# --------------------------------------------------------------------------- #
#: The type as a LABEL — what a column, a heading or a display name says. This
#: IS `web.app.DOC_TYPE_LABELS`: that name now points here, so the review form's
#: dropdown, the Review row and a document's display name cannot drift apart.
#:
#: The vocabulary is the schema's own (`docs/dentalia-schema-sketch.md`):
#: DoC | EC | IFU | ISO | SPP | other. Client ruling 2026-08-18 (question 11)
#: made SPP its own type — neither a declaration nor a certificate, because
#: supersession is scoped by (type, regulation) and a pack statement must never
#: replace a DoC.
DOC_TYPE_WORDS: dict[str, str] = {
    "DoC": "Declaration of Conformity",
    "EC": "EC certificate",
    "IFU": "Instructions for use",
    "ISO": "ISO certificate",
    "SPP": "Systems/procedure pack statement (MDR Art. 22)",
    "other": "Other document",
}

#: The type as a SENTENCE SUBJECT — what reads after "This …" and before a
#: singular verb. Written out rather than derived by lower-casing the label:
#: lower-casing gave "This instructions for use says", which does not parse,
#: and "MDR art. 22", which is not how the article is written. IFU is the one
#: type whose label is plural, so it gets a noun to hang on.
DOC_TYPE_SUBJECTS: dict[str, str] = {
    "DoC": "declaration of conformity",
    "EC": "EC certificate",
    "IFU": "instructions-for-use document",
    "ISO": "ISO certificate",
    "SPP": "systems/procedure pack statement (MDR Art. 22)",
    "other": "document",
}


def doc_type_word(doc_type: str | None) -> str:
    """The type as a label. Unknown types show as themselves."""
    if not doc_type:
        return "Document"
    return DOC_TYPE_WORDS.get(doc_type, doc_type)


def doc_type_subject(doc_type: str | None) -> str:
    """The type as it reads inside a sentence ("This {subject} says …")."""
    if not doc_type:
        return "document"
    return DOC_TYPE_SUBJECTS.get(doc_type, "document")


def _file_name(row: Mapping) -> str:
    """The last path segment of whatever address the row carries.

    `archive_url` is the one under our control (`local://…`, the hash-addressed
    archive); `source_url` is the supplier's and is the fallback. A query string
    is dropped — it is never part of the name a person would recognise.
    """
    for key in ("filename", "archive_url", "source_url", "url"):
        value = (row.get(key) or "").strip()
        if not value:
            continue
        name = value.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
        if name:
            return name
    return ""


def doc_display_name(row: Mapping) -> str:
    """A document as a person names it (rule 1: name first, number second).

    "⟨Manufacturer⟩ · ⟨type⟩ (⟨regulation⟩)" where the manufacturer is known,
    the file name where it is not, and "Document #id" where there is neither.
    The regulation is shown only when it is one — `n.a.` is the absence of a
    regulation and a "(no regulation)" tail on every ISO certificate says
    nothing the type has not already said.

    Accepts any mapping that a document query produces: `manufacturer` or
    `manufacturer_name` for the name, `type`, `regulation`, and `doc_id` or
    `id` for the number.
    """
    name = (row.get("manufacturer") or row.get("manufacturer_name") or "").strip()
    doc_id = row.get("doc_id") if row.get("doc_id") is not None else row.get("id")
    if name:
        # The label as it stands. This is a NAME, not a sentence — "IVOCLAR ·
        # Declaration of Conformity (MDR)" — so nothing is lower-cased here;
        # the sentence forms are `doc_type_subject`.
        out = f"{name} · {doc_type_word(row.get('type'))}"
        regulation = (row.get("regulation") or "").strip()
        if regulation and regulation != "n.a.":
            out += f" ({regulation})"
        return out
    filename = _file_name(row)
    if filename:
        return filename
    return f"Document #{doc_id}" if doc_id is not None else "Document"


# --------------------------------------------------------------------------- #
# dates, weeks and counts
# --------------------------------------------------------------------------- #
#: Month names spelled out rather than left to `strftime('%b')`, which follows
#: the process locale — a container's locale is not a design decision.
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def day_text(d) -> str:
    """'3 Sep 2026'. A date an office reader reads, not an ISO stamp."""
    if d is None:
        return "—"
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def week_label(d: date) -> str:
    """The ISO week a date falls in: "Week 37 (7–13 Sep)".

    Monday to Sunday, en dash, abbreviated month, and both months named when
    the week straddles two: "Week 36 (31 Aug–6 Sep)". A week is a week to the
    person reading a weekly report, not a file name and not a number on its
    own.
    """
    year, week, _ = d.isocalendar()
    start = date.fromisocalendar(year, week, 1)
    end = start + timedelta(days=6)
    span = (f"{start.day}–{end.day} {MONTHS[end.month - 1]}"
            if start.month == end.month else
            f"{start.day} {MONTHS[start.month - 1]}"
            f"–{end.day} {MONTHS[end.month - 1]}")
    return f"Week {week} ({span})"


def num(value) -> str:
    """A count with a thousands separator. Rule 7's other half: a figure is
    read, and "4265" is read one digit at a time where "4,265" is read once.
    `None` is an em dash — a figure that has not arrived is not zero."""
    if value is None:
        return "—"
    return f"{value:,}"


# --------------------------------------------------------------------------- #
# receipts (spec § 9, P7a: "receipts in words, not job numbers")
# --------------------------------------------------------------------------- #
#: What a receipt says when `queue.enqueue` deduped (C2): an active job already
#: holds this key, so the work is not starting — it is already under way.
ALREADY_IN_PROGRESS = "Already in progress."


def receipt(sentence: str, job_id: int | None) -> str:
    """The receipt for an enqueue: what will happen, or that it already is.

    The work is QUEUED, not done, so every caller's sentence says what the
    system will do and none of them claims it is done. `job_id is None` is
    `queue.enqueue`'s dedupe answer and never an error.
    """
    return sentence if job_id is not None else ALREADY_IN_PROGRESS
