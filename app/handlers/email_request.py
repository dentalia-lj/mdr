"""email.request / email.reminder — S2.4 EMAIL outbound (spec docs/specs/email.md
§7, handbook rows 12-13, PRD §0 dedupe table).

**This system never sends.** Client ruling 2026-08-20 (spec §7.1): it prepares a
draft and a person sends it from `mdr@dentalia.si`. So the chain ends at a row in
`email_draft`, `ready` is terminal, and there is no send handler to reach.

**One mail per manufacturer per cadence period.** Client ruling 2026-08-20 (spec
§7.2), and it is the reason this handler looks the way it does: the trigger
(one document crossing the expiry horizon) only selects WHO to write to. The
draft is then built from everything currently expiring for that manufacturer.
Measured on the live registry: 52 documents share the expiry date 2026-05-04 and
all 52 are IVOCLAR, so the old per-document keying would have drafted 52 mails
for one morning's work.

    email.request  {doc_id, state}                    (SCHEDULER expiry scan)
                   {group_id, manufacturer, contacts} (DISCOVER exhausted)
      -> resolve the manufacturer
      -> renewal_request (cadence-guarded) + renewal_request_document (all of it)
      -> email_draft (kind request|reminder)
      -> email.reminder {request_id}, run_after = now() + reminder_after_days

    email.reminder {request_id}
      -> still outstanding -> another draft, re-armed
      -> nothing outstanding, or the escalation cap -> stop, leave it to a human

Invariant 1: producer only. Writes `renewal_request`, `renewal_request_document`
and `email_draft` — never document / item_document / evidence. Invariant 12: no
LLM anywhere in here; the copy is deterministic templating.
"""

from __future__ import annotations

import datetime as dt
import logging
import textwrap

from app.config import load_config
from app.handlers import register
from app.results import Result
from app import queue

log = logging.getLogger("dentalia.handler.email_request")

CLOSED_STATES = ("parsed", "received")

# --------------------------------------------------------------------------- #
# copy (spec §7.1 request, §7.3 reminder)
#
# ADAPTED from the client's own mails, not pasted from them. The spec records
# their wording verbatim as the reference; a generated mail must not reproduce a
# one-off human observation ("I just found a DOC for Nexco") as a filled-in slot,
# and must not reproduce their typos as if we had made them. Ruling 2026-08-20.
#
# `RE:` on BOTH kinds: the document chase is a standing thread per supplier, not
# a fresh broadcast, and a reply into an existing thread is what spec §9's
# In-Reply-To/References matching needs to work at all.
# --------------------------------------------------------------------------- #
SUBJECT = "RE: MDR DOCUMENTS"


def subject_for(request_id: int) -> str:
    """The subject every draft of one chase carries: `RE: MDR DOCUMENTS [DENT-18]`.

    The token is the only handle a reply can be matched on. The system never
    sends (ruling 2026-08-20), so it never learns the Message-ID a reply's
    In-Reply-To would cite, but a supplier replying keeps the subject, and
    `email.poll` reads `[DENT-{id}]` back off it ([email-reply-matching],
    2026-09-11). Gap requests have no `renewal_request` and keep the bare
    `SUBJECT`."""
    return f"{SUBJECT} [DENT-{request_id}]"

REQUEST_BODY = """Dear all,

Please send for EACH ordered good this info/documents:

- Class of MD if it is an MD
- Declaration of conformity
- EC certificate
- Instructions for use (if needed)
- UDI code (if present)

Without these documents we cannot pick up the goods into our system or sell
them to customers. We really must take this seriously and without exceptions.

This concerns the following{scope}:

{doc_list}

Thank you very much in advance and best regards,

Nataša Palme
Trženje in prodaja | Marketing and sales
"""

REMINDER_BODY = """Dear all,

A number of certificates and declarations of conformity for your products have
expired or are about to. The largest at the moment is {anchor}.

We need to have valid documents at all times, because of MDR legislation and
because our customers ask us for them.

Would you be so kind as to send the new ones as soon as you have them?

Outstanding on our side{scope}:

{doc_list}

Thank you and best regards,

Nataša Palme
Trženje in prodaja | Marketing and sales
"""

# What a supplier calls the thing, not what our schema calls it.
_TYPE_WORDS = {"DoC": "declaration of conformity", "EC-cert": "EC certificate",
               "IFU": "instructions for use", "ISO": "ISO certificate"}


def _type_words(row) -> str:
    kind = _TYPE_WORDS.get(row["type"], row["type"])
    reg = row["regulation"]
    # 'n.a.' is our placeholder for "the document does not state one" and means
    # nothing to a supplier — say the type alone rather than "DoC / n.a.".
    return f"{reg} {kind}" if reg and reg not in ("n.a.", "unknown") else kind


def _describe(row) -> str:
    """The one lapse to lead with, in a sentence. Certificate and date, not a
    product name: a supplier looks a certificate up, and the product we happen
    to hold is one of hundreds under it."""
    cert = (f"certificate {row['cert_number']}" if row["cert_number"]
            else "a declaration with no certificate number stated on it")
    n = row["items"] or 0
    return (f"the {_type_words(row)} under {cert}, which expired on "
            f"{row['expires']} and covers {n} catalogue item"
            f"{'' if n == 1 else 's'}")


def _render_rows(rows, today: dt.date) -> str:
    """Worst first, by what it costs us: a lapse taking 1.046 items with it
    leads over an older one taking 2."""
    out = []
    for r in sorted(rows, key=lambda r: (-(r["items"] or 0), r["expires"])):
        when = ("expired " if r["expires"] < today else "expires ") + str(r["expires"])
        cert = r["cert_number"] or "no certificate number stated on the document"
        docs = f"{r['documents']} document" + ("" if r["documents"] == 1 else "s")
        items = f"{r['items']} catalogue item" + ("" if r["items"] == 1 else "s")
        out.append(f"- {_type_words(r)}, {cert}\n  {when} — {docs}, {items}")
    return "\n".join(out)


def _wrap(body: str, width: int = 78) -> str:
    """Wrap prose paragraphs after substitution, leave the document list alone.

    Wrapping at authoring time is not enough: a substituted sentence naming a
    certificate and an item count runs long, and produces one 190-column line in
    the middle of otherwise-wrapped prose."""
    out = []
    for para in body.split("\n\n"):
        lines = para.split("\n")
        # Leave a paragraph alone when it needs nothing doing: an authored list
        # (marker-prefixed), or a block whose every line already fits. The
        # second case is what protects a multi-line block that carries no
        # marker -- the sign-off is two authored lines,
        #
        #     Nataša Palme
        #     Trženje in prodaja | Marketing and sales
        #
        # and re-joining them shipped it as one line in every request and
        # reminder, not only in the gap draft where it was spotted (live draft
        # 25, 2026-09-02). Reflowing exists for ONE reason -- a substituted
        # sentence naming a certificate and an item count runs to 190 columns
        # -- so a paragraph with no over-long line has nothing to gain from it
        # and a line break to lose.
        if (any(l.startswith(("- ", "  ")) for l in lines)
                or all(len(l) <= width for l in lines)):
            out.append(para)
        else:
            out.append(textwrap.fill(" ".join(l.strip() for l in lines), width=width))
    return "\n\n".join(out)


def _revision_descriptor(r) -> str:
    """One EUDAMED revision, dated where we have dates. Dates ARE comparable
    (they are independent evidence, not a parsed label) so they are shown; the
    revision LABEL never gets a comparative word attached to it here."""
    label = r["eudamed_revision"] or "no revision"
    parts = []
    issued = r["eudamed_issue_date"]
    valid = r["eudamed_expiry_date"]
    if issued:
        parts.append(f"issued {issued:%Y-%m-%d}")
    if valid:
        parts.append(f"valid to {valid:%Y-%m-%d}")
    if r.get("notified_body_srn"):
        parts.append(f"notified body {r['notified_body_srn']}")
    return f"{label} ({', '.join(parts)})" if parts else label


def _render_drift(rows) -> str:
    """The drift paragraph, cited, asserting nothing about which revision is
    later.

    Deliberately does NOT say `newer`, `latest`, `current`, `out of date` or
    any other word that ranks `Rev. 00` (parsed off our own document's cert
    number suffix) against `Rev. 02` (EUDAMED's own field) -- those two are
    never shown to be established on a comparable ordering, and asserting one
    succeeds the other is exactly the resolution the 2026-08-19 ruling
    refused (fix round 1, 2026-08-26: the first wording made this assertion
    in softer words -- "newer"/"current" ARE an ordering claim). The mail
    reports a difference and asks; it does not rank.

    One bullet per CERTIFICATE NUMBER, not per row: migration 042 stores each
    EUDAMED revision as its own row by design (the primary key includes
    `revision_number` -- Carl Martin's `HZ 1594091-1` is genuinely two live
    rows, Rev. 1 and Rev. 2) so `certificate_drift` can return more than one
    row per certificate. Rendering one bullet per row would show two bullets
    naming different revisions under the same certificate number, which reads
    as the mail contradicting itself (fix round 1, reviewer-reproduced).

    HELD revisions are grouped the same way, for the same reason (fix round
    2, minor #6): when WE hold more than one revision of one certificate
    (`certificate_drift` then carries one row per (held revision, EUDAMED
    revision) pair -- design.md S1.6's `G15 043306 0282` is 96 documents at
    Rev. 00 and 1 at Rev. 01, both against EUDAMED's Rev. 02), naming only the
    first row's `our_revision` silently drops the other held revision, and
    repeats the single EUDAMED revision once per held row instead of once."""
    if not rows:
        return ""
    by_cert: dict[str, dict] = {}
    for r in rows:
        cert = by_cert.setdefault(
            r["certificate_number"], {"held": [], "revisions": []})
        held_label = r["our_revision"] or "no stated revision"
        if held_label not in cert["held"]:
            cert["held"].append(held_label)
        descriptor = _revision_descriptor(r)
        if descriptor not in cert["revisions"]:
            cert["revisions"].append(descriptor)

    lines = [
        "The EU database (EUDAMED) lists the following certificate(s) at a "
        "different revision from the one we hold. Please send the revision "
        "that currently applies, or confirm that the one we hold is correct:",
        "",
    ]
    for cert_number, info in by_cert.items():
        held = " and ".join(info["held"])
        listed = " and ".join(info["revisions"])
        lines.append(f"  - {cert_number}: we hold {held}; EUDAMED lists {listed}")
    return "\n".join(lines)


def compose(manufacturer: str, groups: list, today: dt.date, *,
           drift=()) -> tuple[str, str, str]:
    """(kind, subject, body). `kind` is decided by the data, not the caller: if
    anything has already lapsed this is a chase, not a first ask.

    `drift` is additive only (Task 8): EUDAMED certificate revisions that
    differ from what we hold, for the same manufacturer. Empty by default, so
    every caller that does not pass it gets byte-identical copy to before this
    parameter existed -- the templates carry no `{drift}` placeholder, so a
    caller that never sees a drift row can't be affected by one."""
    lapsed = [r for r in groups if r["expires"] < today]
    kind = "reminder" if lapsed else "request"
    scope = f" for {manufacturer}"
    doc_list = _render_rows(groups, today)
    if kind == "reminder":
        # Lead with the lapse that COSTS the most, not the oldest: a 2020
        # declaration covering 2 items is not the sentence to open with when a
        # 2026 certificate has taken 1.046 items with it.
        anchor = max(lapsed, key=lambda r: (r["items"] or 0, r["expires"]))
        body = REMINDER_BODY.format(anchor=_describe(anchor), scope=scope,
                                    doc_list=doc_list)
        marker = "Thank you and best regards,"
    else:
        body = REQUEST_BODY.format(scope=scope, doc_list=doc_list)
        marker = "Thank you very much in advance and best regards,"

    drift_block = _render_drift(drift)
    if drift_block:
        # Splice before the salutation, exactly one blank line on each side,
        # regardless of what precedes it (a populated doc_list, or -- as in
        # the brief's own tests -- an empty one). Stripping trailing "\n"
        # before re-adding "\n\n" is what stops the double-blank-line case
        # (empty doc_list contributes its own blank line already) from
        # compounding into three (fix round 1, minor).
        idx = body.index(marker)
        prefix = body[:idx].rstrip("\n")
        body = f"{prefix}\n\n{drift_block}\n\n{body[idx:]}"

    return kind, SUBJECT, _wrap(body)


GAP_EXAMPLES = 3


def _render_gap_rows(rows) -> str:
    """One line per Basic UDI-DI group, plus up to `GAP_EXAMPLES` of our own
    article numbers underneath it.

    The examples are the reason this is not just a list of identifiers. CARL
    MARTIN's EUDAMED records carry no `trade_name` at all (measured 2026-09-02:
    populated for IVOCLAR, null for all 3.596 Carl Martin devices), so a bare
    `++ECMSBUDI0166Z` asks a person at the supplier to resolve an identifier
    their own sales side may never use. An article number is printed on the
    physical goods -- client ruling C12 -- so three of them turn the ask into
    something answerable without a lookup.
    """
    out = []
    for r in rows:
        name = f" -- {r['trade_name']}" if r.get("trade_name") else ""
        n = r["articles"] or 0
        out.append(f"  {r['basic_udi_di']}{name}  "
                   f"({n} article{'' if n == 1 else 's'})")
        if r.get("examples"):
            out.append(f"      e.g. {', '.join(r['examples'])}")
    return "\n".join(out)


def _render_cert_rows(rows) -> str:
    """One line per certificate EUDAMED records and we do not hold.

    Everything on the line comes from the manufacturer's OWN entry in the public
    register -- number, revision, type, issue and expiry dates, notified body --
    so the recipient is not asked to work out which document we mean. That is
    the whole advantage this letter has over a declaration gap request: a gap
    names articles and hopes the supplier can map them; this names the document.

    The notified body is printed as its SRN, not a name, because that is what
    EUDAMED publishes here and inventing the trading name from it would be a
    guess in a letter.
    """
    out = []
    for r in rows:
        rev = f" rev. {r['revision_number']}" if r.get("revision_number") else ""
        kind = (r.get("certificate_type") or "").replace("-", " ")
        span = " ".join(x for x in (
            f"issued {r['issue_date']:%d.%m.%Y}" if r.get("issue_date") else "",
            f"valid to {r['expiry_date']:%d.%m.%Y}" if r.get("expiry_date") else "",
        ) if x)
        out.append(f"  {r['certificate_number']}{rev}"
                   f"{f'  ({kind})' if kind else ''}")
        detail = ", ".join(x for x in (
            span, f"notified body {r['notified_body_srn']}"
            if r.get("notified_body_srn") else "") if x)
        if detail:
            out.append(f"      {detail}")
    return "\n".join(out)


def compose_cert_request(manufacturer: str, rows: list) -> tuple[str, str, str]:
    """(kind, subject, body) for "you hold this certificate and we do not".

    Reuses REQUEST_BODY for the same reason `compose_gap` does -- it is the
    client's own approved wording and a second template would be a paraphrase of
    an approved text. What changes is the scope line, which has to say that the
    list came from the manufacturer's own EUDAMED entry rather than from our
    guess: a recipient who does not know where the numbers came from reads them
    as a demand, and one who does reads them as a reconciliation.
    """
    scope = (f" for {manufacturer}, recorded in your own EUDAMED entry and "
             f"missing from our records")
    return ("cert-request", SUBJECT,
            _wrap(REQUEST_BODY.format(scope=scope,
                                      doc_list=_render_cert_rows(rows))))


def review_due_for_manufacturer(conn, manufacturer: str,
                                today: dt.date | None = None) -> list[dict]:
    """Declarations past Dentalia's own five-year review point for one supplier.

    `basis = 'staleness'` is the whole filter, and it is the same column the item
    card (`app/compliance.py`), `/expiry` and the weekly report read -- four
    surfaces, one definition, which is what F34 was about. A document with a
    STATED expiry, or one inheriting a certificate's, is not this: that is a
    renewal and `compose` already writes it.

    The manufacturer is reached through the production links, the same way
    `lapsing_for_manufacturer` reaches it, because a declaration names no
    manufacturer column of its own that the catalogue agrees with.
    """
    today = today or dt.date.today()
    return [dict(r) for r in conn.execute(
        "SELECT d.doc_id, d.validity_from, e.expires, d.stated_class, "
        "       d.regulation "
        "  FROM document d "
        "  JOIN document_effective_expiry e ON e.doc_id = d.doc_id "
        " WHERE d.status = 'production' AND d.type = 'DoC' "
        "   AND e.basis = 'staleness' AND e.expires IS NOT NULL "
        "   AND e.expires <= %s "
        "   AND EXISTS (SELECT 1 FROM item_document l "
        "                 JOIN item_group_member gm ON gm.item_ref = l.item_ref "
        "                 JOIN item_group g ON g.group_id = gm.group_id "
        "                WHERE l.doc_id = d.doc_id AND l.status = 'production' "
        "                  AND g.canonical_manufacturer = %s) "
        " ORDER BY e.expires, d.doc_id",
        (today, manufacturer),
    ).fetchall()]


def _render_review_rows(rows) -> str:
    """One line per declaration, naming the issue date rather than an expiry.

    Printing "expired 2026-05-04" against a document that has not expired is the
    error this whole feature exists to avoid, and it would be worst here -- in a
    letter, in writing, to the manufacturer.
    """
    out = []
    for r in rows:
        issued = (f"issued {r['validity_from']:%d.%m.%Y}"
                  if r.get("validity_from") else "issue date not stated")
        cls = f", class {r['stated_class']}" if r.get("stated_class") else ""
        out.append(f"  Declaration #{r['doc_id']} ({r['regulation']}{cls}) -- {issued}")
    return "\n".join(out)


#: Its own text, and the one place a second template is right. Every other letter
#: this system writes asks for a document we do not have; this one says we HOLD
#: yours, it has not expired, and we are checking. Reusing REQUEST_BODY -- "we
#: cannot pick up the goods into our system or sell them to customers" -- would
#: state a consequence that is not true of these documents and would read as a
#: renewal demand, which is exactly what the 2026-09-11 ruling forbids.
REVIEW_BODY = """Dear all,

We hold the declaration(s) of conformity listed below for your products and we
are not aware of any problem with them. They state no expiry date, so we review
them five years after issue as a matter of our own record-keeping.

Please confirm that each one is still current, or send us the replacement if it
has been reissued:

{doc_list}

No action is needed on your side beyond a short confirmation.

Thank you very much in advance and best regards,

Nataša Palme
Trženje in prodaja | Marketing and sales
"""


def compose_review_confirm(manufacturer: str, rows: list) -> tuple[str, str, str]:
    """(kind, subject, body) for "is this still current?"."""
    return ("review-confirm", SUBJECT,
            _wrap(REVIEW_BODY.format(doc_list=_render_review_rows(rows))))


def cert_gaps_for_manufacturer(conn, manufacturer: str) -> list[dict]:
    """Certificates EUDAMED records for this manufacturer that we do not hold.

    `certificate_gap` (migration 043) is already exactly this question, and is
    what the manufacturer page and `/expiry` render. Reading the view rather
    than rebuilding the comparison keeps the letter and the screen unable to
    disagree -- the same reason `bc_push_view` reads the stage's own query.

    Newest first: a supplier asked for six certificates answers the recent ones
    fastest, and an expired one may be genuinely gone rather than withheld.
    """
    return [dict(r) for r in conn.execute(
        "SELECT * FROM certificate_gap WHERE canonical_name = %s "
        " ORDER BY issue_date DESC NULLS LAST, certificate_number",
        (manufacturer,),
    ).fetchall()]


def compose_gap(manufacturer: str, rows: list) -> tuple[str, str, str]:
    """(kind, subject, body) for the first ask.

    Reuses REQUEST_BODY verbatim -- the client's own wording already reads as a
    first ask ("Please send for EACH ordered good this info/documents ...
    Declaration of conformity"), so a second template would be a paraphrase of
    an approved text, which is worse than no second template. Only the list
    differs: Basic UDI-DI groups we hold no declaration for, rather than
    documents about to expire.

    `kind` is `gap-request` so the drafts board can tell "your certificate
    lapses in 30 days" from "we have never had a declaration for these 281
    articles". Different urgency, often a different person.
    """
    # The list needs one sentence of its own. `++ECMSBUDI0166Z` is the
    # manufacturer's OWN identifier -- they registered it in EUDAMED -- and it is
    # the unit of the DOCUMENT: one declaration covers one Basic UDI-DI group, so
    # asking per group asks for one document each rather than for 2.389 that do
    # not exist separately. But the letter printed the identifier and trusted the
    # reader to know all that, which Denis questioned on 2026-09-15: a recipient
    # cannot be assumed to know what a Basic UDI-DI in the list means. Saying it
    # costs two lines and removes the only thing about this letter a recipient
    # could misread.
    scope = (f" for {manufacturer}. Each line below is one of your own Basic "
             f"UDI-DI groups as registered in EUDAMED, and one declaration "
             f"covering that group answers it; the numbers underneath are "
             f"examples of the articles we stock from it")
    return ("gap-request", SUBJECT,
            _wrap(REQUEST_BODY.format(scope=scope,
                                      doc_list=_render_gap_rows(rows))))


# --------------------------------------------------------------------------- #
# reads
# --------------------------------------------------------------------------- #
def period_key(today: dt.date, cadence_days: int) -> str:
    """The cadence bucket (spec §7.2). Weekly is the ISO week, so two triggers
    in the same week collide on `renewal_request_cadence_key` exactly as
    intended rather than by arithmetic accident."""
    if cadence_days == 7:
        iso = today.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    return f"{today.year}-P{today.toordinal() // max(cadence_days, 1)}"


def gap_groups_for_manufacturer(conn, manufacturer: str,
                                *, examples: int = GAP_EXAMPLES) -> list[dict]:
    """The EUDAMED declaration gap as mail-ready rows, biggest ask first.

    `eudamed_declaration_gap` (migration 046) is the unit of the ask: one row
    per Basic UDI-DI group we hold no DoC for, because a declaration covers the
    whole group. Measured 2026-09-02, that is what turns CARL MARTIN's 2.389
    uncovered articles into 70 documents to request rather than 2.389.

    The examples come from `eudamed_article_status`, restricted to the articles
    that are actually uncovered (`NOT has_production`) -- listing a covered
    article as an example of a gap would be wrong, and the group can contain
    both. `COALESCE(mfr_ref, item_ref)` is the same precedence the gap view and
    `probe_srn` use: the supplier's number when we hold it, ours otherwise.

    Empty for a manufacturer that has never been swept, exactly as for one with
    no gap -- both are "nothing to ask for" as far as this mail is concerned,
    and the manufacturer page is where the difference is shown.
    """
    return [dict(r) for r in conn.execute(
        "SELECT g.basic_udi_di, g.articles, g.trade_name, "
        "       COALESCE(("
        "         SELECT array_agg(x.ref ORDER BY x.ref) FROM ("
        "           SELECT COALESCE(im.mfr_ref, s.item_ref) AS ref"
        "             FROM eudamed_article_status s"
        "             JOIN item_mirror im ON im.item_ref = s.item_ref"
        "            WHERE s.canonical_name = g.canonical_name"
        "              AND s.basic_udi_di = g.basic_udi_di"
        "              AND NOT s.has_production"
        "            ORDER BY COALESCE(im.mfr_ref, s.item_ref)"
        "            LIMIT %s) x), '{}') AS examples "
        "  FROM eudamed_declaration_gap g "
        " WHERE g.canonical_name = %s "
        " ORDER BY g.articles DESC, g.basic_udi_di",
        (examples, manufacturer),
    ).fetchall()]


def groups_for_docs(conn, doc_ids) -> list[int]:
    """Every group covered by these documents, least recently discovered first.

    The renewal chase asks "who do I write to" (`manufacturers_for_docs`); this
    asks "what do I go and look at again". They are different questions with
    different cardinality: on 2026-09-02, 86 expiring or lapsed production
    documents mapped to 6 manufacturers but **431 groups**, because one document
    covers many.

    Ordered never-discovered first, then oldest attempt. The caller caps this,
    and a stable ordering under a cap means later ticks re-do the head of the
    list forever instead of walking the backlog -- the same reasoning as
    `web.registry.uncovered_groups`.
    """
    ids = [d for d in (doc_ids or []) if d is not None]
    if not ids:
        return []
    rows = conn.execute(
        """
        SELECT DISTINCT m.group_id,
               (SELECT max(dl.at) FROM discovery_log dl
                WHERE dl.group_id = m.group_id) AS last_seen
        FROM item_document id
        JOIN item_group_member m ON m.item_ref = id.item_ref
        WHERE id.doc_id = ANY(%s) AND id.status = 'production'
        ORDER BY last_seen ASC NULLS FIRST, m.group_id
        """,
        (ids,),
    ).fetchall()
    return [r["group_id"] for r in rows]


def manufacturers_for_docs(conn, doc_ids) -> dict[int, str]:
    """`{doc_id: canonical_manufacturer}` for the docs that have one.

    A document has no manufacturer column: it is reached through the items it
    covers and the group they belong to, the same path /expiry uses. Documents
    with no production link are simply absent from the mapping -- there is
    nobody to address, which is a real state and not an error.

    Batched and DETERMINISTIC on purpose. SCHEDULER builds the renewal
    dedupe_key from this and the handler resolves the same document again when
    the job runs; a document reaching two groups with different canonical names
    would otherwise be free to answer differently each time, and the key would
    then name a manufacturer other than the one the draft is addressed to.
    `DISTINCT ON` with a full `ORDER BY` picks the same one every call."""
    ids = [d for d in (doc_ids or []) if d is not None]
    if not ids:
        return {}
    rows = conn.execute(
        "SELECT DISTINCT ON (id.doc_id) id.doc_id, g.canonical_manufacturer AS m "
        "FROM item_document id "
        "JOIN item_group_member gm ON gm.item_ref = id.item_ref "
        "JOIN item_group g ON g.group_id = gm.group_id "
        "WHERE id.doc_id = ANY(%s::bigint[]) AND id.status = 'production' "
        "  AND g.canonical_manufacturer IS NOT NULL "
        "ORDER BY id.doc_id, g.canonical_manufacturer",
        (list(ids),),
    ).fetchall()
    return {r["doc_id"]: r["m"] for r in rows}


def _manufacturer_for_doc(conn, doc_id) -> str | None:
    """One document's manufacturer. Thin wrapper so there is exactly one
    resolver: SCHEDULER's key and this handler's draft must agree."""
    if doc_id is None:
        return None
    return manufacturers_for_docs(conn, [doc_id]).get(doc_id)


def lapsing_for_manufacturer(conn, manufacturer: str, *, horizon_days: int,
                             today: dt.date) -> list[dict]:
    """Everything expiring for one manufacturer, consolidated the way /expiry
    consolidates it — one row per (type, regulation, certificate, date), so the
    mail says what the board says.

    No lower bound on the window, deliberately: something that lapsed in 2020
    needs the chase MORE than something lapsing next month, and dropping it
    would stop the chase where it matters (same reasoning as
    `report.expiring_documents`)."""
    rows = conn.execute(
        """
        WITH lapsing AS (
          SELECT DISTINCT d.doc_id, d.type, d.regulation, d.cert_number, e.expires
            FROM document d
            JOIN document_effective_expiry e ON e.doc_id = d.doc_id
            JOIN item_document id ON id.doc_id = d.doc_id AND id.status = 'production'
            JOIN item_group_member gm ON gm.item_ref = id.item_ref
            JOIN item_group g ON g.group_id = gm.group_id
           WHERE d.status = 'production'
             AND e.expires IS NOT NULL
             AND e.expires <= %s
             AND g.canonical_manufacturer = %s
        )
        SELECT l.type, l.regulation, l.cert_number, l.expires,
               array_agg(DISTINCT l.doc_id) AS doc_ids,
               count(DISTINCT l.doc_id)     AS documents,
               (SELECT count(DISTINCT il.item_ref)
                  FROM item_document il
                 WHERE il.status = 'production'
                   AND il.doc_id IN (SELECT doc_id FROM lapsing l2
                                      WHERE l2.type = l.type
                                        AND l2.regulation = l.regulation
                                        AND l2.cert_number IS NOT DISTINCT FROM l.cert_number
                                        AND l2.expires = l.expires)) AS items
          FROM lapsing l
         GROUP BY l.type, l.regulation, l.cert_number, l.expires
         ORDER BY l.expires
        """,
        (today + dt.timedelta(days=horizon_days), manufacturer),
    ).fetchall()
    return list(rows)


def drift_for_manufacturer(conn, manufacturer: str) -> list[dict]:
    """Certificate revisions EUDAMED lists differently from what we hold, for
    one manufacturer. Raw, one row per (certificate, EUDAMED revision) --
    `certificate_drift` (migration 043) can and does return more than one row
    for a single certificate number (migration 042's `eudamed_certificate`
    keys on revision_number, so EUDAMED's own multiple live revisions are
    multiple rows). Grouping those into one bullet per certificate is
    `_render_drift`'s job, not this reader's: this stays a thin, literal SELECT
    so a caller that wants the raw shape (a count, a different render) is not
    forced through the mail's grouping.

    An enhancement, never a main path: `certificate_drift` (Task 6, migration
    043) is empty in a database that has never synced EUDAMED, and this simply
    returns nothing — the mail is then exactly the mail it was before this
    reader existed."""
    return list(conn.execute(
        "SELECT DISTINCT certificate_number, our_revision, eudamed_revision, "
        "       eudamed_issue_date, eudamed_expiry_date, notified_body_srn "
        "  FROM certificate_drift WHERE canonical_name = %s "
        " ORDER BY certificate_number, eudamed_issue_date NULLS LAST, "
        "          eudamed_revision",
        (manufacturer,),
    ).fetchall())


def _contacts(conn, manufacturer: str) -> list[str]:
    """Contacts live in the database, not in the playbooks (ruling 2026-08-20):
    they change without an engineer, the person chasing must be able to fix one
    in the UI, and they are personal data that should not sit in git history
    forever. An empty list is NOT fatal — under drafts-only a person addresses
    the mail; the UI refuses to save a draft with no recipient, so nothing
    unaddressed can be marked ready by accident."""
    row = conn.execute(
        "SELECT contact_emails FROM manufacturer WHERE canonical_name = %s",
        (manufacturer,),
    ).fetchone()
    return list(row["contact_emails"] or []) if row else []


def _draft_review_confirm(conn, manufacturer: str, r) -> dict:
    """One draft per manufacturer asking whether their five-year-old
    declarations are still current.

    Unlike the gap and certificate asks, this one CAN legitimately recur: the
    horizon moves, and a document confirmed in 2026 is due again in 2031. So the
    guard is narrower than theirs -- it refuses only while an unsent draft of
    this kind is still open (`draft` or `ready`), which stops a second press
    stacking a duplicate on somebody's board, and allows a fresh ask once the
    last one was sent or archived.
    """
    open_ask = conn.execute(
        "SELECT id, status FROM email_draft "
        "WHERE kind = 'review-confirm' AND manufacturer = %s "
        "  AND status IN ('draft','ready') ORDER BY id DESC LIMIT 1",
        (manufacturer,),
    ).fetchone()
    if open_ask:
        r.count("review_already_open")
        r.note(f"{manufacturer}: review confirmation #{open_ask['id']} is "
               f"{open_ask['status']} — not asking twice")
        return r.as_dict()

    rows = review_due_for_manufacturer(conn, manufacturer)
    if not rows:
        r.count("no_review_due")
        r.note(f"{manufacturer}: no declaration past its review point")
        return r.as_dict()

    kind, subject, body = compose_review_confirm(manufacturer, rows)
    conn.execute(
        "INSERT INTO email_draft (renewal_request_id, kind, manufacturer, "
        "  to_addrs, subject, body, status, created_at) "
        "VALUES (NULL, %s, %s, %s, %s, %s, 'draft', now())",
        (kind, manufacturer, _contacts(conn, manufacturer), subject, body),
    )
    r.count("review_drafted")
    r.count("review_documents", len(rows))
    r.note(f"{manufacturer}: {len(rows)} declaration(s) put up for confirmation")
    return r.as_dict()


def _draft_cert_request(conn, manufacturer: str, r) -> dict:
    """One draft per manufacturer naming every certificate EUDAMED records and
    we do not hold.

    No `renewal_request` row, exactly as the gap request has none and for the
    same reason: this renews nothing, has no `doc_id` to hang one on, and must
    not consume a slot in the cadence guard -- doing so would silence the real
    renewal mail for that manufacturer for a whole period.

    One ask per manufacturer, and a person's decision about it sticks. Any
    status counts, `cancelled` included: archived means "we are not asking these
    people", and re-offering is what Denis ruled out on 2026-09-03 for the gap
    request. The same rule, because it is the same failure -- a queue dedupe key
    is scoped to ACTIVE jobs by design, so without this guard a second press
    after the first job finished writes a second letter.
    """
    asked = conn.execute(
        "SELECT id, status FROM email_draft "
        "WHERE kind = 'cert-request' AND manufacturer = %s "
        "ORDER BY id DESC LIMIT 1", (manufacturer,),
    ).fetchone()
    if asked:
        r.count("cert_already_asked")
        r.note(f"{manufacturer}: certificate request #{asked['id']} is "
               f"{asked['status']} — not asking twice")
        return r.as_dict()

    rows = cert_gaps_for_manufacturer(conn, manufacturer)
    if not rows:
        r.count("no_cert_gap")
        r.note(f"{manufacturer}: no EUDAMED certificate gap — no draft written")
        return r.as_dict()

    kind, subject, body = compose_cert_request(manufacturer, rows)
    conn.execute(
        "INSERT INTO email_draft (renewal_request_id, kind, manufacturer, "
        "  to_addrs, subject, body, status, created_at) "
        "VALUES (NULL, %s, %s, %s, %s, %s, 'draft', now())",
        (kind, manufacturer, _contacts(conn, manufacturer), subject, body),
    )
    r.count("cert_drafted")
    r.count("cert_certificates", len(rows))
    r.note(f"{manufacturer}: {len(rows)} certificate(s) requested")
    return r.as_dict()


def _draft_gap_request(conn, manufacturer: str, r) -> dict:
    """One draft per manufacturer listing every Basic UDI-DI group we hold no
    declaration for.

    No `renewal_request` row: a gap request renews nothing, has no `doc_id` to
    hang one on, and must not consume a slot in the renewal cadence guard
    (`renewal_request_cadence_key`) -- doing so would silence the real renewal
    mail for that manufacturer for a whole period.

    Drafts-only, spec §7.1: the chain ends at an `email_draft` row and nothing
    sends. An empty `to_addrs` is allowed and the drafts page refuses to release
    it until a person supplies one.
    """
    # One ask per manufacturer, and a person's decision about it sticks.
    #
    # The button behind this (`POST /manufacturers/{name}/gap-request`) dedupes
    # on a QUEUE key, and queue dedupe is scoped to active jobs -- by design,
    # so a terminal job never blocks a re-enqueue. That is right for jobs and
    # wrong for letters: once the job finished, pressing again wrote a second
    # draft. Draft 25 was archived on 2026-09-02 and draft 26 was written
    # anyway, because nothing connected the two rows. Migration 057 gives the
    # draft its own manufacturer, which is the key this guard needs.
    #
    # Any status counts, `cancelled` included: archived means "we are not
    # asking these people", and re-offering is exactly what Denis ruled out
    # (2026-09-03). Per manufacturer, never global.
    asked = conn.execute(
        "SELECT id, status FROM email_draft "
        "WHERE kind = 'gap-request' AND manufacturer = %s "
        "ORDER BY id DESC LIMIT 1", (manufacturer,),
    ).fetchone()
    if asked:
        r.count("gap_already_asked")
        r.note(f"{manufacturer}: gap request #{asked['id']} is "
               f"{asked['status']} — not asking twice")
        return r.as_dict()

    rows = gap_groups_for_manufacturer(conn, manufacturer)
    if not rows:
        r.count("no_gap")
        r.note(f"{manufacturer}: no EUDAMED declaration gap — no draft written")
        return r.as_dict()

    kind, subject, body = compose_gap(manufacturer, rows)
    conn.execute(
        "INSERT INTO email_draft (renewal_request_id, kind, manufacturer, to_addrs, "
        "  subject, body, status, created_at) "
        "VALUES (NULL, %s, %s, %s, %s, %s, 'draft', now())",
        (kind, manufacturer, _contacts(conn, manufacturer), subject, body),
    )
    # One letter written, counted apart from what it covers: `gap_groups` and
    # `gap_articles` size the ask, `gap_drafted` says an ask happened at all --
    # which is the number that pairs with `gap_already_asked`.
    r.count("gap_drafted")
    r.count("gap_groups", len(rows))
    r.count("gap_articles", sum(x["articles"] or 0 for x in rows))
    r.note(f"{manufacturer}: drafted a request for {len(rows)} group(s) "
           f"covering {sum(x['articles'] or 0 for x in rows)} article(s)")
    return r.as_dict()


# --------------------------------------------------------------------------- #
# email.request
# --------------------------------------------------------------------------- #
def handle_email_request(conn, job) -> dict:
    cfg = load_config()
    payload = job.get("payload") or {}
    r = Result()
    today = dt.date.today()

    manufacturer = payload.get("manufacturer") or _manufacturer_for_doc(
        conn, payload.get("doc_id"))
    if not manufacturer:
        # Nothing to group by means nothing to address. Counted, never silent.
        r.count("no_manufacturer")
        r.note(f"no manufacturer for payload {payload!r} — no draft written")
        return r.as_dict()

    # The gap branch. A `reason` of `eudamed-gap` asks for documents we have
    # NEVER held, which the renewal path below cannot express: it composes from
    # documents that exist and are expiring, and returns `nothing_expiring` for
    # exactly the manufacturers whose problem is the largest. CARL MARTIN,
    # measured 2026-09-02, holds zero declarations across 2.389 articles.
    #
    # Deliberately before the horizon read: the two are different questions and
    # a gap request must not be silenced by there being nothing to renew.
    if payload.get("reason") == "eudamed-gap":
        return _draft_gap_request(conn, manufacturer, r)

    # The certificate branch. Same shape as the gap branch and equally unable to
    # be expressed by the renewal path: these are documents we have NEVER held,
    # and the renewal composer builds from documents that exist and are
    # expiring. Measured 2026-09-15: 44 certificates across 25 manufacturers.
    if payload.get("reason") == "certificate-gap":
        return _draft_cert_request(conn, manufacturer, r)

    # The review branch. Also before the horizon read, and for a sharper reason
    # than the other two: these documents ARE in the expiry horizon, and the
    # renewal composer would happily write them a renewal demand -- the exact
    # letter the 2026-09-11 ruling forbids for a document that has not expired.
    if payload.get("reason") == "review-due":
        return _draft_review_confirm(conn, manufacturer, r)

    horizon = max(cfg.renewal.horizon_days)
    groups = lapsing_for_manufacturer(
        conn, manufacturer, horizon_days=horizon, today=today)
    if not groups:
        r.count("nothing_expiring")
        r.note(f"{manufacturer}: nothing expiring within {horizon} days")
        return r.as_dict()

    period = period_key(today, cfg.renewal.request_cadence_days)
    doc_ids = sorted({d for g in groups for d in g["doc_ids"]})

    # The cadence guard is the partial unique index from migration 029, not an
    # if-statement: a second trigger in the same period cannot create a second
    # request even if two workers race.
    row = conn.execute(
        "INSERT INTO renewal_request (doc_id, state, manufacturer, reason, "
        "  period_key, created_at, updated_at) "
        "VALUES (%s, 'requested', %s, %s, %s, now(), now()) "
        "ON CONFLICT DO NOTHING RETURNING id",
        (payload.get("doc_id") or doc_ids[0], manufacturer,
         payload.get("reason", "expiry"), period),
    ).fetchone()

    if row is None:
        existing = conn.execute(
            # `NOT (state = ANY(%s))`, not `NOT IN %s`: psycopg3 binds a tuple
            # as one parameter and Postgres rejects `IN $3`.
            "SELECT id FROM renewal_request WHERE manufacturer = %s AND period_key = %s "
            "  AND NOT (state = ANY(%s)) ORDER BY id DESC LIMIT 1",
            (manufacturer, period, list(CLOSED_STATES)),
        ).fetchone()
        if existing is None:
            r.count("cadence_conflict_unresolved")
            r.note(f"{manufacturer}/{period}: insert conflicted but no live request found")
            return r.as_dict()
        request_id = existing["id"]
        r.count("cadence_attached")
    else:
        request_id = row["id"]
        r.count("requested")

    # "If more than one group is expiring soon, we already know it, we must add
    # it" (client, 2026-08-20). Attaching to an existing request therefore ADDS
    # the newly-expiring documents rather than dropping them for next period.
    added = conn.execute(
        "INSERT INTO renewal_request_document (renewal_request_id, doc_id) "
        "SELECT %s, unnest(%s::bigint[]) ON CONFLICT DO NOTHING RETURNING doc_id",
        (request_id, doc_ids),
    ).fetchall()
    r.count("documents_linked", len(added))

    drift = drift_for_manufacturer(conn, manufacturer)
    r.count("drift_rows", len(drift))
    kind, _, body = compose(manufacturer, groups, today, drift=drift)
    subject = subject_for(request_id)
    to_addrs = _contacts(conn, manufacturer)
    if not to_addrs:
        r.count("no_contact")
        r.note(f"{manufacturer}: no contact_emails — draft written unaddressed")

    # The period's mail is ONE mail, and a person's decision about it sticks.
    #
    #   * an untouched `draft`      -> regenerate in place with fresh content
    #   * `ready` / `sent`          -> they are about to send that exact text,
    #                                  or already have, by hand
    #   * `cancelled` (the Archive  -> they said this one is not needed
    #     button)
    #
    # The last three are SETTLED: nothing new is drafted for this request
    # (Denis, 2026-09-03 -- "the system may not offer a new one if this is
    # marked as archive, or sent"). Until then the else-branch below inserted a
    # second draft whenever the first was not in status `draft`, so archiving
    # one produced another and approving one produced a twin -- observed live
    # on the gap-request pair 25/26 and on four reminder requests.
    #
    # Newly expiring documents are NOT dropped by this: they are attached to
    # the same `renewal_request` above, so the next cadence period drafts one
    # mail carrying all of them. This deliberately replaces the older reading
    # ("a new one, beside the approved one"), which is what stacked them.
    existing = conn.execute(
        "SELECT id, status FROM email_draft WHERE renewal_request_id = %s "
        "ORDER BY (status <> 'draft'), id DESC LIMIT 1", (request_id,),
    ).fetchone()
    if existing and existing["status"] == "draft":
        conn.execute(
            "UPDATE email_draft SET kind=%s, subject=%s, body=%s, to_addrs=%s "
            "WHERE id=%s",
            (kind, subject, body, to_addrs, existing["id"]),
        )
        draft_id = existing["id"]
        r.count("draft_updated")
    elif existing:
        draft_id = existing["id"]
        r.count("draft_settled")
        r.note(f"{manufacturer}: draft #{draft_id} is {existing['status']} — "
               f"no second mail for this period")
    else:
        draft_id = conn.execute(
            "INSERT INTO email_draft (renewal_request_id, kind, manufacturer, "
            "  to_addrs, subject, body, status) "
            "VALUES (%s,%s,%s,%s,%s,%s,'draft') RETURNING id",
            (request_id, kind, manufacturer, to_addrs, subject, body),
        ).fetchone()["id"]
        r.count("draft_created")

    # Arm the follow-up. The reminder then reschedules itself rung by rung
    # (`handle_email_reminder` defers its own job), so this key stays held until
    # the chase closes, escalates or is archived, and a re-run of this request
    # dedupes against it rather than arming a second ladder.
    reminder_id = queue.enqueue(
        conn, "email.reminder", {"request_id": request_id},
        dedupe_key=f"email.reminder:req:{request_id}",
        priority="sweep",
        run_after=dt.datetime.now(dt.timezone.utc)
        + dt.timedelta(days=cfg.renewal.reminder_after_days),
    )
    if reminder_id is not None:
        r.count("reminder_armed")

    r.sample("drafts", {"manufacturer": manufacturer, "period": period,
                        "kind": kind, "draft_id": draft_id,
                        "documents": len(doc_ids)})
    return r.as_dict()


# --------------------------------------------------------------------------- #
# email.reminder
# --------------------------------------------------------------------------- #
def handle_email_reminder(conn, job) -> dict:
    cfg = load_config()
    payload = job.get("payload") or {}
    r = Result()
    today = dt.date.today()

    request_id = payload.get("request_id")
    req = conn.execute(
        "SELECT id, manufacturer, state, escalation_count FROM renewal_request "
        "WHERE id = %s", (request_id,),
    ).fetchone()
    if req is None:
        r.count("request_missing")
        r.note(f"renewal_request #{request_id} does not exist — nothing to chase")
        return r.as_dict()
    if req["state"] in CLOSED_STATES:
        r.count("already_closed")
        return r.as_dict()

    groups = lapsing_for_manufacturer(
        conn, req["manufacturer"], horizon_days=max(cfg.renewal.horizon_days),
        today=today)
    if not groups:
        # Everything we were chasing has been replaced. Say so on the request
        # rather than drafting a chase for nothing -- but do NOT claim `parsed`,
        # which means a reply of theirs closed the loop (spec §7); this closed
        # because the registry moved on.
        conn.execute(
            "UPDATE renewal_request SET state='received', updated_at=now() WHERE id=%s",
            (request_id,))
        r.count("nothing_outstanding")
        return r.as_dict()

    if req["escalation_count"] >= cfg.renewal.escalate_after_reminders:
        # Stop drafting. A manufacturer who has ignored N reminders is a person
        # problem, and a board that grows a draft every fortnight forever is
        # noise nobody reads.
        conn.execute(
            "UPDATE renewal_request SET state='escalated', updated_at=now() WHERE id=%s",
            (request_id,))
        r.count("escalated")
        r.note(f"{req['manufacturer']}: {req['escalation_count']} reminders unanswered "
               f"— stopped drafting, needs a person")
        return r.as_dict()

    # Wired the same way handle_email_request is (fix round 1, important #2):
    # the reminder is the mail that actually goes out once a supplier has not
    # responded, so a `{drift}` placeholder nothing ever fills is worse than no
    # placeholder at all.
    # This path used to INSERT unconditionally, which is how four requests on
    # the live registry ended up carrying two unsent reminder drafts each, a
    # day apart. Two unsent mails for one chase is one too many whichever a
    # person picks, so an untouched draft is regenerated in place.
    #
    # The ladder reads `sent` differently from the request path above, and
    # deliberately: a reminder that went out and was not answered is the REASON
    # for the next rung, not a reason to stop. `cancelled` -- the Archive
    # button -- is a person saying "we are not sending this", and that does
    # stop it (Denis, 2026-09-03).
    prior = conn.execute(
        "SELECT id, status FROM email_draft WHERE renewal_request_id = %s "
        "ORDER BY id DESC LIMIT 1", (request_id,),
    ).fetchone()
    if prior and prior["status"] == "cancelled":
        r.count("reminder_archived")
        r.note(f"{req['manufacturer']}: draft #{prior['id']} archived — chase stopped")
        return r.as_dict()

    drift = drift_for_manufacturer(conn, req["manufacturer"])
    r.count("drift_rows", len(drift))
    kind, _, body = compose(req["manufacturer"], groups, today, drift=drift)
    subject = subject_for(request_id)
    if prior and prior["status"] == "draft":
        conn.execute(
            "UPDATE email_draft SET kind='reminder', subject=%s, body=%s, to_addrs=%s "
            "WHERE id=%s",
            (subject, body, _contacts(conn, req["manufacturer"]), prior["id"]),
        )
        draft_id = prior["id"]
        r.count("reminder_updated")
    else:
        draft_id = conn.execute(
            "INSERT INTO email_draft (renewal_request_id, kind, manufacturer, "
            "  to_addrs, subject, body, status) "
            "VALUES (%s,'reminder',%s,%s,%s,%s,'draft') RETURNING id",
            (request_id, req["manufacturer"], _contacts(conn, req["manufacturer"]),
             subject, body),
        ).fetchone()["id"]
        r.count("reminder_drafted")
    # The escalation is stamped either way: a regenerated draft is still this
    # rung of the ladder, and leaving `last_reminder_at` behind would let the
    # next tick rewrite the same mail forever without the cap ever biting.
    conn.execute(
        "UPDATE renewal_request SET escalation_count = escalation_count + 1, "
        "  last_reminder_at = now(), state = 'awaiting', updated_at = now() "
        "WHERE id = %s", (request_id,))

    # The next rung is THIS job, rescheduled, not a new one. A re-enqueue under
    # `email.reminder:req:{id}` collides with this very row, which the runner has
    # already committed as `running`, so the active-scope dedupe dropped it and
    # every chase stopped at its first reminder (requests 17-20, 08-21 to
    # 09-11). Deferring keeps one job and one key per request, so the arm in
    # `handle_email_request` still dedupes against it; `run_once` commits these
    # writes and skips `finish`, the same contract `scheduler.tick` uses.
    queue.defer(conn, job["id"], cfg.renewal.reminder_after_days * 86400)
    r.count("reminder_rearmed")

    r.sample("reminders", {"manufacturer": req["manufacturer"], "draft_id": draft_id,
                           "n": req["escalation_count"] + 1})
    return {**r.as_dict(), "_deferred": True}


register("email.request", handle_email_request)
register("email.reminder", handle_email_reminder)
