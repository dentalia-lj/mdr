# S2.4 EMAIL — unit spec

Contract: PRD v3 §4 (alternate producers), §5 EXTRACT, §8 job-type table, §0
dedupe conventions. Handbook rows 6 (`email.poll`), 12 (`email.request`),
13 (`email.reminder`); handbook "sibling-producers" note. Schema-sketch §3
(`fetch_log`), §6 (`renewal_request`, declared migration 006). This spec fixes
only what those do not.

**Slice boundary (2026-08-19).** This session builds the **INBOUND** half only:
`email.poll` (mailbox → archive attachments → enter the spine at `extract.doc`)
plus its read-only producer UI. The **OUTBOUND** half (`email.request` /
`email.reminder` send-on-approval, editable drafts, the `renewal_request`
lifecycle, request↔reply matching, outbound UI) is the **next slice** — it is
fully *designed* here (§7–§9) so the inbound data model does not corner it, but
**not implemented**. Nothing in this slice writes `document` / `item_document`
/ `evidence` (invariant 1); `email.poll` is a producer exactly like `fetch.url`
/ `backfill.scan` / `upload.ingest`, and only GATE writes the registry.

---

## 1. Where `email.poll` sits

`email.poll` is a sibling producer that enters the spine at `extract.doc`, the
same door `backfill.scan` and `upload.ingest` use (PRD §4 table). It reads a
dedicated mailbox, pulls PDF attachments, archives each under our control
(hash-addressed, PRD §9), and hands the spine forward:

```
new content hash            -> archive + extract.doc {archive_url, content_hash, group_id=None, source_url}
seen hash, requesting group -> validate.doc (C3 link candidate)   [see §4: inbound group_id is None today]
seen hash, no group         -> nothing to emit (already extracted; content dedupe)
```

Inbound attachments carry **`group_id = None`** and self-identify from their
content (REF list / Basic UDI-DI), exactly like `backfill.scan`. The `group_id`
seam exists in the handler (so a future reply that resolves to a renewal
request's group takes the C3 `validate.doc` path — §7), but no inbound message
supplies a group in this slice.

`email.poll` is **AI: none for fetch** (invariant 12 — it never fetches with an
LLM). It *does* reuse the deterministic page-1 doc-class classifier
(`app.extract.t0_templates.classify_doc_class`) to drop obvious non-compliance
noise before archiving; that is keyword matching over extracted text, not an
LLM call, and it is the same classifier EXTRACT already short-circuits on.

## 2. Two dedupes, kept distinct

There are **two** independent idempotency guards, and conflating them is the
trap:

1. **Processed-email guard (new, this slice).** A durable ledger,
   `email_poll_log`, with the reprocess guard keyed on the IMAP-native
   `(mailbox, uid_validity, imap_uid)`, plus a **Message-ID second guard within
   the same mailbox**: if UIDVALIDITY resets (a mailbox migration/rebuild) the
   same message reappears under a new UID, and the globally-unique `Message-ID`
   still recognises it (scoped to the mailbox, so the same message delivered to
   two polled mailboxes keeps its own provenance row). A recorded message is
   **never reprocessed**. This is the authority — **not** the IMAP `\Seen` flag,
   which is neither durable nor trustworthy (an external client, a crash between
   archive and flag-set, or a mailbox migration can clear or set it wrongly).
   `\Seen` is used only as the adapter's cheap first-pass filter
   (`UID SEARCH UNSEEN`); the ledger is what actually decides.

2. **Content-attachment dedupe (existing).** The `fetch_log` content-hash
   ledger + `extract:{content_hash}` dedupe key. Two different emails carrying
   the *same* PDF archive one physical file and re-enter at `validate.doc`
   (C3), exactly as `fetch.url` handles same-hash-new-URL. This is the
   archive/extract side and is unchanged.

The processed-email guard is per **source email**; the content dedupe is per
**physical document**. A brand-new email whose only attachment we already hold
is still recorded in `email_poll_log` (its origin is auditable) while emitting
no new `extract.doc`.

## 3. Module layout

```
app/adapters/email.py       EmailAdapter protocol + ImapEmailAdapter (lazy imaplib)
                            + FakeEmailAdapter + make_email_adapter + EmailNotConfigured
app/handlers/email_poll.py  handle_email_poll(conn, job, *, adapter=None, store=None)
migrations/028_email.sql    email_poll_log (inbound ledger) + GRANT SELECT dentalia_api
web/app.py  GET /emails     read-only processed-email list (+ web/templates/emails.html)
```

Archive path / fetch-ledger / emit helpers are **reused** from
`app/handlers/archiving.py` (the home shared by FETCH + UPLOAD, and — per its
own docstring — "later, email.poll"). No archive/dedupe logic is re-authored.

### 3.1 EmailAdapter (invariant 11 — nothing downstream knows which is live)

```python
@dataclass(frozen=True)
class EmailAttachment:
    filename: str
    content_type: str
    content: bytes

@dataclass(frozen=True)
class EmailMessage:
    uid: str                 # IMAP UID (stable within uid_validity)
    uid_validity: str        # IMAP UIDVALIDITY epoch (UIDs only stable within it)
    message_id: str          # RFC822 Message-ID (durable cross-mailbox identity)
    from_addr: str
    subject: str
    received_at: datetime | None      # Date header, tz-aware
    in_reply_to: str | None           # reply-matching hook (outbound slice, §9)
    references: str | None            # reply-matching hook (outbound slice, §9)
    attachments: list[EmailAttachment]

class EmailAdapter(Protocol):
    @property
    def mailbox_id(self) -> str: ...          # "{host}/{user}/{folder}" — ledger scope
    def fetch_unseen(self, *, limit: int | None = None) -> list[EmailMessage]: ...
    def mark_seen(self, uid: str) -> None: ...
```

- `ImapEmailAdapter` — `imaplib` **lazy-imported** inside the connect path (the
  fake-adapter tests and the web container never import it). Uses UID commands
  throughout (`SELECT`, `UID SEARCH UNSEEN`, `UID FETCH <uid> (RFC822)`,
  `UID STORE <uid> +FLAGS (\Seen)`), parses with the stdlib `email` package,
  reads `UIDVALIDITY` from the SELECT response. TLS by default (`IMAP4_SSL`).
  Verified against the CPython `imaplib`/`email` docs; **never connected live in
  this slice** (no creds exist — GAP G8). Construction is inert; `fetch_unseen`
  raises `EmailNotConfigured` when host/user/password are empty (mirrors
  `SearchNotConfigured` — a keyless poll is a skipped rung, not a dead job).
- `FakeEmailAdapter` — in-memory messages, injected in tests; never touches the
  network. `mark_seen` records the UID; `fetch_unseen` returns messages whose
  UID it has not been told is seen (the handler's ledger is still the authority).
- `make_email_adapter(cfg)` — closed enum `imap | fake` over `cfg.adapters.email`.

### 3.2 Handler signature & flow

```python
def handle_email_poll(conn, job, *, adapter=None, store=None) -> dict:
    # adapter/store injected in tests; live path builds from load_config()
```

Per message (skipping any `(mailbox, uid_validity, uid)` already in
`email_poll_log` — the reprocess guard, counted as `already_processed`):

1. For each attachment: keep only PDFs (extension `.pdf` **and** `%PDF` magic
   bytes). Non-PDF → verdict `skipped`, reason `non-pdf`.
2. Classify PDF via `classify_doc_class(first_page_text(bytes))`
   (lazy-imported from `app.extract`). `msds` → reason `noise-msds`;
   `business-doc` → reason `noise-business`; both `skipped`.
   `compliance-doc` / `unknown` → **useful**, proceed. A scanned PDF with no
   text layer classifies `unknown` and proceeds (EXTRACT's vision tier handles
   it — never false-skipped).
3. Useful attachment: `content_hash = sha256`; `url_norm = email:{content_hash}`;
   `archiving.ledger_upsert(..., source="email")`. Then, mirroring `fetch.py`
   step 6/7:
   - already extracted (`hash_extracted_rev` not None): disposition `deduped`;
     emit `validate.doc` **iff** a `group_id` is present (none inbound today),
     else emit nothing.
   - new content: `store.put(...)` under `archiving.archive_path("unknown", …)`
     (group_id None → manufacturer "unknown", like backfill), then
     `archiving.emit_extract(conn, archive_url, content_hash, group_id=None,
     source_url=f"email:{message_id}")`. disposition `archived`.
4. Write **one** `email_poll_log` row per message: mailbox, uid_validity, uid,
   message_id, from_addr, subject, received_at, in_reply_to, references,
   had_attachments, `attachments` jsonb (per-attachment
   `{filename, content_type, verdict, reason, content_hash, archive_url,
   disposition}`), `emitted_jobs` jsonb, `renewal_request_id` NULL (populated
   by the outbound slice's reply-matching, §9).
5. `adapter.mark_seen(uid)` — courtesy only; the ledger row is the durable guard.

Counts roll up into the standard `Result` envelope (`app/results.py`):
`messages_seen`, `already_processed`, `attachments`, `pdf`, `archived`,
`deduped`, `skipped_non_pdf`, `skipped_noise`. Nothing skipped is silent
(CLAUDE.md) — every skip is both counted *and* recorded per-attachment in the
durable ledger.

`source_url = f"email:{message_id}"` follows the `upload.ingest` precedent
(`source_url = f"upload:{filename}"`) — no `extract.doc` payload contract change
(PRD §5 already allows `source_url | email_ref`; the value simply carries the
`email:` namespace). Full provenance (from/subject/received/headers) lives in
the durable `email_poll_log`, not in the regenerable job payload (invariant 10).

## 4. Error taxonomy

| condition | disposition |
|---|---|
| `EmailNotConfigured` (empty host/creds) | **skipped rung** — handler returns a `{outcome: not-configured}` result, job `done`. A keyless poll must not dead-letter (mirrors DISCOVER's `SearchNotConfigured`). |
| live IMAP connect / auth / socket error (creds present) | propagate → job `failed` → queue backoff → `dead` (a real outage is a visible work item). |
| one attachment unreadable / not a PDF despite extension | counted `skipped` + reason, never raises; other attachments proceed. |
| `store.put` / DB error | propagate → job fails → backoff (partial writes roll back with the transaction; the ledger row and the emit commit atomically with `finish`). |
| duplicate poll job for the same period | active-scope dedupe on `email.poll:{period_key}` (C2) makes it a no-op. |

Idempotency: re-running a poll that already recorded every UID emits nothing and
archives nothing (the two-cycle property, AC5) — the ledger guard makes it free
even if `\Seen` was cleared.

## 5. Scheduler tie-in

`_tick_email_poll` (gated `scheduler.email_poll_enabled`, default **off** until
G8 creds exist — same producer-side gating as DISCOVER's email rung and the
expiry/reonboard flags). Emits `email.poll` with payload
`{"mailbox": <folder>, "since": <period_key>}` and dedupe key
`email.poll:{period_key}`, where `period_key` is an interval bucket
`YYYY-MM-DD:HH-band` sized by `email_poll_interval_hours` (default 6) — a
mailbox genuinely wants sub-day cadence (replies), unlike the day-granularity
scans. `scheduler_run(name="email.poll", period_key)` is the restart-safe
ledger, same as every other tick. With the flag off the tick is a logged no-op.

## 6. Config

- **`Email`** section (operational, non-secret): `imap_host`, `imap_port`
  (993), `imap_ssl` (true), `imap_folder` ("INBOX"), `poll_max_messages`
  (200), plus the existing `send_policy` (outbound, unchanged). Env vars
  `IMAP_HOST` / `IMAP_PORT` / `IMAP_SSL` / `IMAP_FOLDER` / `EMAIL_POLL_MAX_MESSAGES`.
- **`Connection`** secrets (empty default, env-only, **not** tuning keys —
  SERPER_API_KEY precedent): `imap_user`, `imap_password` (`IMAP_USER` /
  `IMAP_PASSWORD`).
- **`Adapters.email`** = `imap` (default) | `fake`. Default `imap` is safe: the
  poll is flag-gated off and `fetch_unseen` raises `EmailNotConfigured` while
  host/creds are empty, so nothing connects until Denis wires G8.
- **`Scheduler`**: `email_poll_enabled` (off), `email_poll_interval_hours` (6).
- **Outbound, not yet implemented** (declared here so the slice does not invent
  its own names): `renewal.request_cadence_days` — the §7.2 hard rule's bucket,
  7 (weekly) or 14 (fortnightly), and the `period_key` half of
  `email.request:mfr:{manufacturer}:{period_key}`.
- **`models.email_summary`** (S2.4 inbound, added 2026-08-20, **built
  2026-08-20**): the cheap-tier model that summarises an inbound body at poll
  time. Invariant 12 was widened for this and **ratified by Denis on
  2026-08-20** (CLAUDE.md, PRD §4). Summary is UI context for `/emails` only —
  never evidence, never a production value, never a VALIDATE/GATE input. The
  body itself is not stored; the summary is what persists, which is the whole
  point (§10).
- **`Email` summarisation bounds** (`EMAIL_SUMMARY_*`): `summary_enabled`
  (true — the poll itself is flag-gated off, so a mailbox that is not polled
  cannot spend anything), `summary_min_chars` (**0 — no floor**, Denis
  2026-08-21: if there is a body it gets a summary, however short. It was 120,
  then 40 (measured: the fixture mailbox's bodies run 9-80 chars and a 120
  floor summarised none of them), on the theory that the summary of
  "Attached." would BE the body. That reasoning ignored where it lands: the
  body is never stored, so skipping the call left the row blank, and half the
  fixture mailbox rendered emptier than the long messages beside it. The intent
  classification earns the call at any length, and cost was never what the
  bound was for. The key survives as a throttle, so `too-short` stays in the
  status vocabulary with nothing setting it),
  `summary_max_chars` (6000, truncated from the FRONT — in a reply the tail is
  quoted history), `summary_max_tokens` (200: one or two sentences).

## 7. Outbound half — DESIGN ONLY (next slice)

Not built here. The renewal loop per workflow-v2 §2.1 and system-design §2.8:

```
SCHEDULER expiry scan  -> email.request {doc_id, state}   (already emitted, gated off)
DISCOVER exhausted     -> email.request {group_id, manufacturer, contacts, reason}
email.request handler  -> look up manufacturer contact -> compose renewal draft
                          -> renewal_request(state=requested) + email_draft(status=draft)
                          -> email.reminder (run_after = now()+N days)
[human reviews/edits draft in the outbound UI, approves -> status=ready]
[NO SEND HANDLER -- client ruling 2026-08-20, see 7.1: a ready draft is sent by
 a person from mdr@dentalia.si, never by this system]
email.reminder {request_id}: state still awaiting -> reminder draft or escalate to human
Loop closes: email.poll -> extract.doc -> GATE promotes a superseding doc
             -> renewal_request.state=parsed (reply matched back, §9)
```

Send policy is `email.send_policy` (`draft` = draft-for-approval, default).
Drafts are **editable before send** and carry a status lifecycle; a request is
matched back to its manufacturer/document via `renewal_request`, and a later
inbound reply is matched to the request via message headers (§9).

### 7.1 Client ruling, 2026-08-20: drafts only, and the copy to draft

**The system never sends.** Denis relayed the client's decision: this pipeline
prepares drafts and a person sends them. `draft` therefore stops being a
*default* for `email.send_policy` and becomes the only implemented policy --
there is no send handler to build, and `ready` is a terminal state for us.

Two consequences worth stating, because they shrink work rather than add it:

1. **G8 no longer needs send-as rights.** The gap asked the client for IMAP-vs-
   Graph access *and* permission to send as the compliance address. Only the
   read side and a way to deposit a draft remain. Reversing this ruling later
   re-opens the send-as ask; nothing else in the design changes, which is why
   the send step above is struck out rather than deleted.
2. **The mailbox is named: `mdr@dentalia.si`.** Still outstanding for G8: host,
   protocol and credentials.

The client also supplied the copy they send today. It is recorded verbatim so
the drafter reproduces the ask they already make of manufacturers rather than
inventing a new one (sender: Nataša Palme, Trženje in prodaja / Marketing and
sales; introduced as "nek mušter za pošiljanje iz mdr@dentalia.si"):

> Dear all,
>
> Please send for EACH ordered good this info/documents:
>
> - Class of MD if it is an MD
> - Declaration of conformity
> - EC certificate
> - Instructions for use (if needed)
> - UDI code (if present)
>
> Without these documents we cannot pick up the goods into our system or sell
> them to customers.
>
> We really must take this seriously and without exceptions.
>
> Thank you very much in advance and best regards,

**Resolved 2026-08-20 — see §7.2.** That copy asks per **ordered good**, while
`email.request` was keyed per **group** (DISCOVER exhausted) or per **document**
(expiry scan). The client's cadence rule settles it: one mail per manufacturer
per cadence bucket, with the affected documents and their items rendered into
the body.

### 7.2 Client cadence rule, 2026-08-20: one mail per manufacturer per period

**HARD RULE.** At most **one renewal mail per manufacturer per week (or
fortnight — `renewal.request_cadence_days`)**. Not one per document, not one per
group, not one per item. If more than one group or document for that
manufacturer is expiring soon, the mail **must list them all** — we already know
about them, so leaving one out to "keep the mail focused" means a second chase
later for something we could see today.

Consequences, in order of what they change:

1. **The dedupe key moves off the document.** `email.request:doc:{doc_id}` and
   `email.request:group:{group_id}` become
   `email.request:mfr:{canonical_manufacturer}:{period_key}` (PRD §0 updated).
   The old keying is not a style preference that survives: measured against the
   live registry on 2026-08-20, **52 documents share the expiry date
   2026-05-04 and every one of them is IVOCLAR** — the per-document key would
   have sent IVOCLAR 52 separate mails on the same morning.
2. **The handler aggregates rather than reacts.** A trigger (one document
   crossing the horizon) selects the manufacturer; the draft is then built from
   *everything* currently expiring for that manufacturer, not from the trigger.
3. **The body carries the list.** That is also how the per-ordered-good ask in
   §7.1 is satisfied without one mail per item: each row names the document, its
   expiry, and the catalogue items losing coverage.

Measured shape of the problem (live registry, 2026-08-20), which is why
manufacturer is the right grouping key and date is not:

| expires | documents | manufacturers |
|---|---|---|
| 2026-05-04 | 52 | IVOCLAR only |
| 2024-05-26 | 14 | GC + IVOCLAR (already lapsed) |
| 2031-03-03 | 14 | KOMET only |
| 2029-02-13 | 12 | IVOCLAR only |

Every large same-day cluster is a single manufacturer, so grouping by
manufacturer collapses the worst day from 52 mails to one.

**Certificate-level consolidation already works — via `cert_number`, not
`cert_doc_id`.** `/expiry` groups on `(manufacturer, type, regulation,
cert_number, expires)`, and `cert_number` is populated, so the IVOCLAR cluster
renders as ONE row reading *52 documents, 1046 items affected* against
certificate `G15 043306 0282 Rev. 00`. The drafter can therefore already say
"this one certificate expires" and list what hangs off it; it should group the
same way rather than inventing its own.

What `cert_doc_id` is sparse for (**2 of 740 documents**) is the separate job of
linking a declaration to the certificate *document* it cites, so the declaration
can inherit that document's date and link to it. That affects date inheritance
and navigation, not the ability to consolidate a renewal ask.

### 7.3 Reminder copy (client-supplied, 2026-08-20)

`email.reminder` is a draft like any other (§7.1 — the system never sends). The
client's own follow-up wording, recorded verbatim so the reminder chases the way
they already chase. Note it is a **RE:** on the original thread, which is what
§9's `In-Reply-To`/`References` matching depends on:

> Subject: RE: MDR DOCUMENTS
>
> Dear Sara,
>
> Quite a few certificates and DOCs will soon expire or have expired already?
> For example I just found a DOC for Nexco that expired in 04-05-2026.
> We need to have valid documents at all times becuase of MDR legislation and
> because also our customers demand them.
>
> Would you ve so kind to send all new ones as soon as you have them?
>
> Thank you and br,

Three things this copy fixes about the reminder's shape:

- **It names a concrete lapsed example.** "a DOC for Nexco that expired in
  04-05-2026" is `2026-05-04` — the exact 52-document IVOCLAR cluster measured
  above. The reminder drafter should pick the same kind of anchor automatically:
  the oldest already-lapsed document for that manufacturer, named with its date.
- **It asks for everything outstanding, not just the anchor** ("send all new
  ones as soon as you have them") — the same aggregate shape as §7.2.
- **It is addressed to a named person** ("Dear Sara"), not to `Dear all` as the
  first-contact copy in §7.1 is. Reminder copy follows a thread that already has
  a human on the other end; the contact lookup has to carry that name.

Typos are the client's and are kept **in this quotation**: it is the record of
what they actually send.

**The generated draft adapts this copy; it does not paste it** (ruling, Denis
2026-08-20 — the first cut was "too literal"). Three rules the drafter follows:

1. **Subject is `RE: MDR DOCUMENTS` for both kinds**, request and reminder. The
   document chase is a standing thread per supplier, not a fresh broadcast — and
   a reply into an existing thread is what §9's `In-Reply-To`/`References`
   matching needs to work at all.
2. **A one-off human observation is not a template slot.** "I just found a DOC
   for Nexco that expired in 04-05-2026" is Nataša noticing something; filling
   a product name and date into that sentence mechanically produced *"I just
   found a DoC for Cervitec F 20X0,26G + 50 Vivabrush that expired in
   03-07-2020"*, which reads as a form letter and led with a 2020 document
   covering 2 items. The generated mail states the position instead, and its
   anchor is **the lapse that costs the most**, described by certificate and
   date rather than by a product name — a supplier can look a certificate up,
   and the product we happen to hold is one of hundreds under it.
3. **Their typos are not reproduced in our generated text.** Quoting them is
   the record; emitting "becuase" in a mail we composed is our mistake, not
   their voice.

What is kept: their asks, their reason (MDR plus customers demanding it), their
directness, and their sign-off.

## 8. Outbound schema — DESIGN ONLY

`renewal_request` already exists (declared, migration 006). The outbound slice
extends it and adds one draft table. **Not created in migration 028.**

**Amended 2026-08-20 by the §7.2 cadence rule.** §8 was written when a request
was one document's chase, so `renewal_request.doc_id` was a single column. One
mail per manufacturer per period means **one request covers many documents** (52
for IVOCLAR on 2026-05-04), so the set moves to a link table and `doc_id` stays
only as the anchor that triggered the request. Without this the handler would
have to write 52 requests to send one mail, which is the per-document keying the
cadence rule exists to kill.

```sql
-- extend renewal_request (outbound slice): + manufacturer text, group_id bigint
--   (soft ref), reason text (expiry|discovery-exhausted), last_reminder_at,
--   escalation_count int, period_key text (the cadence bucket, §7.2).
--   state stays due|requested|awaiting|received|parsed|escalated.
--   doc_id becomes the ANCHOR (the document that tripped the horizon), not the
--   scope -- the scope is renewal_request_document below.

-- new (outbound slice): every document this one mail is chasing.
CREATE TABLE renewal_request_document (
  renewal_request_id  bigint NOT NULL REFERENCES renewal_request,
  doc_id              bigint NOT NULL REFERENCES document(doc_id),
  PRIMARY KEY (renewal_request_id, doc_id)
);

-- new (outbound slice): editable outbound drafts, one per send attempt.
CREATE TABLE email_draft (
  id                  bigserial PRIMARY KEY,
  renewal_request_id  bigint REFERENCES renewal_request,
  to_addrs            text[]  NOT NULL,
  subject             text    NOT NULL,
  body                text    NOT NULL,
  status              text    NOT NULL,   -- draft|ready|sent|failed|cancelled
  edited_by           text,
  edited_at           timestamptz,
  sent_at             timestamptz,
  sent_message_id     text,               -- the Message-ID we send; reply-match key (§9)
  created_at          timestamptz NOT NULL DEFAULT now()
);
```

The outbound UI (next slice) lists `email_draft` rows by status, edits
`draft`/`ready` ones (subject/body/recipients), and offers approve-to-send
(`draft`→`ready`) — a producer that enqueues the send, never sends inline.

## 9. Reply matching — DESIGN ONLY (why headers are captured now)

`email_poll_log.in_reply_to` / `references` are recorded **this slice** though
unused, precisely so the outbound slice is not cornered: a reply is matched to
its request by `In-Reply-To`/`References` ⊇ `email_draft.sent_message_id`, which
resolves the `renewal_request`, which supplies the `group_id` that lets the
attachment take the C3 `validate.doc` link path (§1) and drives
`renewal_request.state → received → parsed`. `email_poll_log.renewal_request_id`
is the nullable soft ref the matcher populates. Inbound-only, it stays NULL.

## 10. Inbound UI (this slice)

`GET /emails` — read-only producer page, `dentalia_api` SELECT on
`email_poll_log`, same auth/patterns as `/inflight` / `/dead`. Lists processed
emails newest first: received/processed time, from, subject, per-attachment
verdict (useful → link through to the document detail when GATE has created it,
resolved by `content_hash`; skipped → reason). Nav link under **Pipeline**.
Zero writes.

**The body summary** (migration 030, built 2026-08-20). Under each subject sits
one line saying what the message asked for, with the model id beside it so it is
never read as the sender's own words. This is the whole reason the body is read
at all: `/emails` could say which document arrived and in what state, but not
why it was sent, and "I just found a DOC for Nexco that expired in 04-05-2026"
is the context that makes the page actionable.

What does NOT happen is the point. The body is held in memory
(`EmailMessage.body_text`, bounded by `BODY_CHAR_CAP`), summarised, and dropped.
This registry keeps documents for ten years; supplier correspondence is not a
document, is not evidence, and has no business being retained verbatim. Nothing
joins to the summary and no stage re-reads it (invariant 12).

It runs **exactly once per message**, structurally rather than by a flag: a
message is summarised on the single poll that records it in `email_poll_log`,
and the UID guard means it is never polled again. A FAILED call still records
its row, so a failure does not retry either — a missing line of UI context is
not worth a second call, and a poll must never dead-letter over it.
**Measured, not extrapolated** (2026-08-20, live against the GreenMail mailbox,
`models.email_summary` = claude-haiku-4-5): 10 messages in, **5 summarised for
$0.003243 total** — 471-506 input tokens and 30-36 output tokens each, about
**$0.00065 per message**. The other five recorded `too-short` and made no call.
At the client's expected volume this is noise against the €10-40/month target;
the per-row token and cost columns are what keep that a measurement rather than
a memory.

`summary_status` (`ok` | `disabled` | `no-body` | `too-short` | `llm-error`) is
never null, so a row without a summary always carries its reason; the page
renders that reason rather than a blank cell, except for `no-body` (a bare PDF
with no covering note is the common case, and saying so on every such row would
be true and useless).

## 11. Table-driven test cases (against real Postgres)

`tests/test_email_adapter.py` (fakes only, no network):
- `make_email_adapter` returns Imap/Fake per `cfg.adapters.email`; unknown → ValueError.
- `ImapEmailAdapter.fetch_unseen` raises `EmailNotConfigured` with empty host/creds.
- `FakeEmailAdapter`: fetch_unseen returns injected; mark_seen tracked; mailbox_id shape.

`tests/test_email_poll_handler.py` (FakeEmailAdapter + LocalFsStore):
| case | expect |
|---|---|
| one message, one new compliance PDF | fetch_log(source=email) + archive + `extract.doc` emitted; ledger row disposition `archived`; counts |
| same PDF a second poll (seen hash, group None) | no new extract.doc; ledger row disposition `deduped` |
| message already in ledger (same mailbox/uid_validity/uid) | skipped, `already_processed`, nothing emitted/archived |
| MSDS attachment | `skipped_noise`, reason `noise-msds`, no archive/emit, ledger records it |
| business-doc attachment | `skipped_noise`, reason `noise-business` |
| non-PDF attachment (.docx / image) | `skipped_non_pdf`, no archive/emit |
| message with no attachments | ledger row `had_attachments=false`, nothing emitted |
| body over the floor | summary + intent + model + tokens + cost on the row; `summary_ok` counted |
| body under the floor / absent | `too-short` / `no-body` recorded, **no client constructed** |
| summariser raises | `llm-error` recorded, attachments still archived and `extract.doc` still emitted |
| same message polled again | summariser called once in total, failed summaries included |
| any outcome | `msg.body_text` appears nowhere in the row (`to_jsonb` assertion) |
| two attachments, one useful one noise | one archived + emitted, one skipped; both in the ledger's `attachments` |
| `EmailNotConfigured` from the adapter | job result `not-configured`, no rows, no raise (done) |
| end-to-end through the runner | claim → handle → finish; job `done`, result persisted |

`tests/test_email_poll_web.py`:
- `/emails` renders processed rows; useful attachment links to the doc when one
  exists (content_hash → doc_id); skipped shows the reason; empty state renders.
- `dentalia_api` has only SELECT on `email_poll_log` (write refused).

`tests/test_scheduler.py` (+cases): `_tick_email_poll` off = no emit; on =
emits `email.poll` with `email.poll:{period_key}`; `scheduler_run` recorded.

## 12. PHASES / contract deltas applied here

- PRD §0 dedupe conventions: **add** `email.poll | SCHEDULER | email.poll:{period_key}`.
- Schema-sketch: add `email_poll_log` under §3, plus the §7 access-matrix row.
- `email.poll` was already in the closed `job_type` enum (migration 001) and
  PRD §8 — no `ALTER TYPE` needed; this is not a new tag.
- `[FILLED — needs review]` items are called out in PHASES.md S2.4.
