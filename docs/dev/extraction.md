# Extraction, as built: T0 → T1 → T2 → T3

**Status:** live. Measured against the tree at 2026-08-31; the scan ladder
(a document with no text page) added 2026-10-12, extraction-ladder plan Task 3;
the text ladder rebuilt the same day (T-3 on Haiku 5.5, plan Task 4); the
second reads that confirm the gate fields added 2026-10-13 (plan Task 5b).

Covers `app/extract/` and the parts of `app/handlers/extract.py` and
`app/handlers/gate.py` that finish what `extract/` starts. Does not restate
the tier ladder's *rationale* (`dentalia-job-type-handbook.md` §5,
`dentalia-mdr-pipeline-ground-truth.md` §7) — this is what the code actually
does, and where the surprises are.

## Idea

Four tiers read one document's fields, cheapest first, escalating per field
rather than per document: T0 is free deterministic parsing (regex, keyword
tables, pdfplumber tables, manufacturer-authored layout templates), T1/T2 are
Anthropic structured-output calls (text, then vision), and T3 is a human
correction made through `gate.apply`; it never touches `app/extract/` at all.
Since 2026-10-12 a field moves up the ladder while it is not SETTLED (the
evidence checks prove it, two reads agree, or the null rule holds), not while a
model calls itself unsure. A text document takes T1 (Haiku 5.5), a cheap image
read for what T1 leaves unproven, and Sonnet only for what those two do not
settle. A document with no text page at all (a scan) skips T1 and takes its own
ladder of image reads: a cheap read, a second cheap read for what that leaves
open, Sonnet only for what the two do not settle. Once a ladder has run, the
three fields GATE scores (`type`, `regulation`, `coverage_scope`) carry what
proves or confirms them beyond one read's own number: a regulation its one
printed citation proves, or a second read that gave the same value, which GATE
combines with the first. A gate field nothing proves or confirms is asked of a
second read made for that alone (`T1-second` on text; on a scan it rides in
`T2-second`). `extract.doc` (`app/handlers/extract.py`)
is the handler that drives the ladder and writes its two audit tables
(`extraction_attempt`, `extraction_cost`); GATE is what turns the ladder's
output into `evidence` rows, and constructs several fields of those rows
itself.

## Where

| Module | Lines | Owns |
|---|---|---|
| `app/extract/target.py` | 35 | `TARGET` — the 11-field schema, alone, importing nothing (see Gotchas) |
| `app/extract/t0_templates.py` | 1546 | T0: doc-class classification, keyword tables, REF-list extraction, `field_ev()` evidence shape |
| `app/extract/t0_layout.py` | 307 | The T0 REF-list layout-template engine (anchors + text-relative regions, read from playbooks) |
| `app/extract/udi.py` | 159 | Offline Basic UDI-DI check-character validation (HIBCC + GS1, MOD 1021/32); `is_word` (no agency shape, no digit) |
| `app/extract/tiers.py` | 1461 | The ladder itself: `needs_escalation`, `merge` (stronger read prevails; of two agreeing reads a verified one, then the surer), `calibrate`, `run_extraction` (splits text / scan), `run_text_ladder` (T-3) and `_image_pages` (its page choice), `run_scan_ladder` (S-2), `_stamp_agreement` / `_to_confirm` (the gate fields' second reads), `read_disagreements`, `failed_reads`, `next_extract_rev`, `extraction_attempt`/`extraction_cost` writers |
| `app/extract/checks.py` | 360 | Evidence checks, pure: `settled` / `null_settled` / `both_null_settled` against the text layer; `citation_proven` (a regulation proven by its one printed citation, for GATE's 1.0); `well_formed`, `normalized` and `agree` (shape and agreement between two reads); a Basic UDI-DI that is a word never passes (`udi.is_word`) |
| `app/extract/llm.py` | 766 | T1/T2 Anthropic clients (sync `AnthropicLlm`, batch `AnthropicBatchClient`), `READ_STEPS`, the JSON schema, per-field evidence construction, the doc-class calls (`classify` on page 1-2 text, `classify_image` on page 1-2 images) |
| `app/extract/batching.py` | 66 | Batch API state machine (`advance_batch`) over the `batch_ref` side table |
| `app/extract/economics.py` | 153 | Token usage → USD, per-model `PRICES`, `UnknownModelPricing` |
| `app/extract/pdf.py` | 247 | The only module touching PyMuPDF directly: text, rasterization, `is_scan`, `bounded_text`, per-page classes (`page_classes`; `has_text_page` decides text ladder or scan ladder) |
| `app/extract/text_store.py` | 77 | `document_text` sidecar — what was actually read, independent of the tier ladder |
| `app/handlers/extract.py` | 472 | `handle_extract_doc`: sync/batch dispatch, hint assembly, `_finalize` (writes + emits `validate.doc`) |
| `app/handlers/gate.py` | — | `_insert_evidence` (622-678), `_require_complete_evidence` (238-266), `_apply_edits` (1118-1139) — see Rules and Gotchas |

## Input → Output

| Direction | Shape |
|---|---|
| In | `extract.doc` payload: `content_hash`, `archive_url`, optional `group_id`, `companion_archive_url` (`docs/dev/handlers.md` `extract.doc` row) |
| In (steering) | An `extract_hints` block from the resolved manufacturer playbook, guarded and hedged (`_hints_for`, `app/handlers/extract.py:51-90`) |
| Out (DB) | One `extraction_attempt` row per revision (`tiers.write_extraction_attempt`, `tiers.py:315-333`), one `extraction_cost` row per priced LLM call (`tiers.py:336-353`), one `document_text` row (`text_store.store_text`) |
| Out (queue) | `validate.doc`, dedupe key `validate:{hash}:{rev}:{group_id}` (`app/handlers/extract.py:151-156`) |
| Out (evidence) | **Not written here.** `extract.doc` never inserts into `evidence` — it emits `validate.doc`, which emits `gate.candidate`, and GATE is what turns `extraction_attempt.fields` into `evidence` rows (see Rules) |

## Rules

**Escalation** (`app/extract/tiers.py:99-125`). `needs_escalation(fields,
threshold)` returns fields absent or below `threshold`. `_ESCALATE_ALWAYS`
(type, regulation, validity_from, validity_to, coverage_scope, cert_number,
manufacturer) always pursues; `_ESCALATE_ITEM_ID` (ref_list, basic_udi_di) is
suppressed once `coverage_scope` is `manufacturer`
(`tiers.py:122-124`); `_ESCALATE_CERTIFICATE` (validity_from, validity_to,
cert_number) is suppressed once `type` is confidently one of
`_NO_CERTIFICATE_TYPES = ("IFU",)` (`tiers.py:75, 118-120`) — an IFU asked
for a validity date returns a fabricated one, not a blank (two STRAUMANN
cases, `tiers.py:57-69`). `referenced_docs` and `stated_class` never force
escalation. The two ladders apply the same sets with "confidently" read as
KNOWN (T0 at 0.95 or more, or settled by a read): `_text_pursued` keeps the
scope suppression by the scope's VALUE, as before (decisions 2026-10-12, "the
text ladder keeps today's suppression"); `_scan_pursued` only once the scope is
known. A read answers every field (the response schema requires them all, and
`llm.extract` returns them all since Task 4); a ladder merges only the fields
it pursues, so an IFU's certificate fields and `stated_class` never come from a
read.

**The ladder** (`run_extraction`): T0 always runs first; business-doc/msds
classes short-circuit with no LLM call at all. Then `pdf.has_text_page(doc)`
splits it: a document with any text page (an OCR layer over a scan counts, so
does a mixed PDF) takes the **text ladder**, a document with none the **scan
ladder**. Both transports run the same two functions: the batch path
(`_handle_batch`, `app/handlers/extract.py`) hands them a `read` that submits
each step as a batch-of-1 under its label and raises `tiers.Deferred` while it
runs, and re-runs the ladder from the top on every poll (a resolved step is
re-fetched, never resubmitted).

**The text ladder, T-3** (`run_text_ladder`; PRD v3 §5 amended 2026-10-12, plan
Task 4). Steps and labels from `llm.READ_STEPS`:

1. T0 left the file `unknown`: the doc-class call on the page 1-2 text
   (`classify`, `doc-class`, `models.t1`, thinking off, 256 tokens). When pages
   1-2 hold no text (a mixed PDF that opens with scanned pages; the review of
   2026-10-13 found 2 real compliance documents like this in the mirror), or
   one of them is a scanned page (`pdf.page_classes` `image`; Task 9 replay,
   doc 520: a scanned USTOMED declaration on page 1, its article list as text
   on pages 2-3, which the text call read alone and refused for good), or
   their text layer is under 30% letters (`tiers._unreadable`: fonts that map
   glyphs to arbitrary codes; 10 ISO 13485, MDSAP and CE certificates
   production holds, 8 of which Haiku 5.5 refused as "unreadable", and their
   images read as what they are), their images are asked instead (`classify_image`,
   `doc-class-image`), as on a scan; a client with no image call keeps the
   old refusal without a call. On the SFTP mirror (2026-10-13) this moves 4 of
   the 22 PDFs T0 leaves `unknown` with a text page to the image call (Dentsply
   files whose page 2 is scanned) and the 30% cut none; of the 104 text files
   production sent through the call, 11 (the image call accepted all 11, one
   of them a declaration production had refused) and the cut 10 (accepted, 10
   of 10). The
   doc-class prompt names user manuals, operating, processing and
   reprocessing instructions as `IFU` (doc-class check 2026-10-13, below).
   `not-a-compliance-document` is terminal. The answer becomes `type` evidence
   (step `doc-class` or `doc-class-image`, ranked below every field read). Batch
   mode makes the same calls since Task 4 (it made none before, so an `unknown`
   text document was read as a compliance document).
2. `T1` (`models.t1`, Haiku 5.5 since Task 4, the whole text up to
   `pdf.LLM_TEXT_BUDGET_CHARS`) for every pursued field T0 does not hold at
   0.95. A field is settled when the value it then holds passes
   `checks.settled` (quote on the cited page, value in the text, dates parsed
   from the quote, regulation proven by the one citation printed, a Basic
   UDI-DI whose check pair is not invalid, a `cert_number` that is not a
   notified-body number; `type` and `coverage_scope` by a quote on the cited
   page), or when T1 ANSWERED it empty and `checks.null_settled` holds (no label
   for it in the text, no image or OCR page, text not cut). A T0 value under
   0.95 settles the same way when the field still holds it (T1 read nothing
   better): never against a different value T1 read, which the image read then
   decides. A REF list stores the list that settled it (`_proven`, review
   2026-10-13): `merge` lets a longer list win, but a longer one that fails the
   check (a code not printed) is not kept over a read's own shorter list that
   passes; when both pass, the longer one stays. A list T0 wrote is never
   replaced (review round 2): it fails the check only where its codes sit in a
   companion annex (Komet's RA_812 list beside the RA_810 declaration, 63 of 63
   on the mirror), and a read's passing list then settles the field with T0's
   list kept. T0 at 0.95 is never asked.
3. Fields pursued only after T1 (a scope T1 changed from `manufacturer` brings
   the item identifiers in) are taken from T1's answer, which carries every
   field, and settle as in step 2; recorded as T1's.
4. `T2-first` (`models.vision_first`) for what is open, on the pages
   `_image_pages` picks (below). A field settles when its own value passes the
   checks (and then holds that value, as in step 2), when it is a Basic UDI-DI whose check pair verifies
   (`checks.udi_check_valid`; the text layer may be an OCR misread, "OOIYC" for
   "001YC"), when it agrees with T1 (`checks.agree` with no per-read minimum:
   ruling 4, "as measured"), or when both reads ANSWERED it empty and
   `checks.both_null_settled` allows it: never a Basic UDI-DI the text labels
   (a multi-UDI list goes to Sonnet), never with an image page no read rendered
   or text past the budget.
5. Type to scope after each read, as on a scan (step 5 below). And once the
   type is known to be an IFU, the certificate fields a read wrote before the
   type was known are dropped (`_drop_ifu_certificate_reads`, review
   2026-10-13): T1 is asked `validity_from` while the type is still open, and on
   an IFU it reads one off a revision code. T0's own value comes back where it
   had one; the read stays in `_reads`. The scan ladder applies the same rule.
   Only for a type someone read: T0 at 0.95 or more, or a read's value. A T0
   guess under 0.95 (a file named "IFU") that became known because both reads
   answered `type` empty drops nothing.
6. `T2` (`models.t2`, Sonnet) for what is still open, on the pages
   `_image_pages` picks for those fields. Its answer closes what it was asked,
   an empty answer included, with the scan ladder's guards (`_sonnet_closes`).
7. The gate fields (plan Task 5b; see "The gate fields' evidence" below): a
   `regulation` a read holds that `checks.citation_proven` proves is marked
   `proven: "citation"`, and each gate field a read holds names the read that
   agreed with it (`_stamp_agreement`). A gate field held by a read, with a
   well-formed value, that is neither citation-proven nor agreed with by a read
   where both reach `gate.agreement_min` (`_to_confirm`) is asked of
   `T1-second` (`models.second_read`, thinking off, the whole text, hints
   included): one call for all of them, at most one per extraction. It only
   confirms: its value never replaces the stored one, and no field is sent on
   because of it. When it does not agree it is a disagreement on the job
   result. When it agrees, the stored evidence is chosen as `merge` chooses it
   (ruling D1, one rule for every read, Denis 2026-10-13;
   `_prefer_confirming`): its own evidence is stored where its quote is on its
   page and the stored read's is not, or where it is surer and both are, or
   neither is, verified; the former stored read then becomes the agreeing
   read. Where that changes the type's stored read, type to scope runs again,
   so a derived scope carries the stored type's own number. A failed `T1-second` stamps nothing and is
   counted. Not asked: T0 at 0.95 (GATE's own number), a scope that follows the
   type (T0 evidence), the doc-class answer, an empty field, and a field whose
   stored read is under `gate.agreement_min` (review 2026-10-13, F4: its own
   number is half of any combination, and no later read raises it). On the
   eval (cached replay, 2026-10-13) 35 of 79 documents made the call.

A failed read (no answer at all) is never an empty answer: its fields stay
open, it is marked `failed` in `_reads`, and the job result counts it, as on a
scan. The text ladder writes `_reads` and the job result's `disagreements`
exactly as the scan ladder does (below).

**Page-aware image reads** (`_image_pages`, Denis 2026-10-12). A text
document's image read renders, chosen in this order: the pages T1 cited for the
open fields, the first page where each open field's label occurs
(`checks.LABELS`), the image pages of a mixed PDF, then page 1 onward; at most
4, each clamped to the page count. They are sent in document order, so a choice
of pages 1-4 is the old request byte for byte and no page list is passed at all
then. On the eval one open field pointed past page 4 (neodent's certificate,
cited on page 26). **Sync only**: a batch image read is always pages 1-4,
because `batch_ref` does not store a page list
([limits.md](limits.md#built-but-not-wired)).

**The scan ladder, S-2** (`run_scan_ladder`; PRD v3 §5 amended 2026-10-12).
Steps and labels from `llm.READ_STEPS`:

1. T0 left the file `unknown`: the doc-class call on the page 1-2 images
   (`classify_image`, `doc-class-image`, `models.vision_first`, thinking off,
   256 tokens). `not-a-compliance-document` is terminal, as for text. The text
   doc-class call is never made on a scan: it saw no text and refused the file
   without a call, which is how 281 production scans were never read.
2. `T2-first` (`models.vision_first`, pages 1-4) for every pursued field T0
   has not settled (T0 at 0.95 or more). A field it answers **well-formed**
   (`checks.well_formed`: an ISO date that is a real day, `type` / `coverage_scope`
   / `regulation` from their vocabularies, a Basic UDI-DI whose check pair is
   not invalid, a `cert_number` that is not a notified-body number, a list with
   a code) at `extract.image_settle` (0.85) or above is settled.
3. `T2-second` (`models.second_read`, thinking off) for what is open. Settled
   when both reads agree (`checks.agree`: the same well-formed value after
   `checks.normalized` — dates as ISO, codes compacted, lists as sets — each read
   at least `gate.agreement_min`, 0.70) or both ANSWERED it empty and
   `checks.both_null_settled` allows it (never a Basic UDI-DI on a scan).
   A **failed read** is not an empty answer: a real read returns every field it
   is asked, null included (the response schema requires them all), so a read
   that returns nothing failed (a response that would not parse or was cut at
   `max_tokens`, or an errored batch request). Its fields stay open and go on
   to the next read; it is marked `failed` in `_reads` and counted on the job
   result (`failed_reads`, `counts.read_failures`; review 2026-10-12: two
   failed cheap reads used to "settle" every field but the UDI as empty).
   The gate fields the first read settled (`_to_confirm`, plan Task 5b) ride
   in the same call, in the ladder's field order, so it is made even when
   nothing else is open: for those it only confirms, as `T1-second` does on
   text (a counted disagreement when it does not agree, never sent on to
   Sonnet, its value never replacing the stored one; when it agrees and is the
   surer, its evidence is stored and the first read's becomes the agreeing
   one, ruling D1, with no text on a scan to verify a quote against). One call per label, or that call's cost row would
   be lost. On the eval 8 of 34 scans make the call for the gate fields alone;
   Task 3 made none there, and gave 6 scans production instead of 25.
4. A scan longer than the four pages the image reads see: a field both cheap
   reads answered empty is **unread**, not settled and not sent to Sonnet (who
   sees the same pages). Recorded under `_pages_unread`; VALIDATE raises the
   informational flag `pages-unread` from it (`tiers.integrity_flags`). Two
   failed reads say nothing about the pages, so their fields go to Sonnet.
5. Type to scope: once the type is known to be EC, ISO or IFU, the scope follows
   from it (`t0_templates.derive_coverage_scope`) and replaces a scope that
   disagrees or is empty, as T0 evidence ("type=EC") at the type's confidence
   where lower. An agreeing scope keeps its evidence unless the derivation is
   surer (Task 4, accepted by Denis 2026-10-13): T0's own derivation from a
   guessed type (0.6), or a read that stated the same scope at less. So a
   confirmed EC or ISO certificate's `manufacturer` scope carries the type's
   confidence, and that is what GATE's manufacturer auto-bind reads
   (`scope_stated`, PRD v3 §7: at least `gate.high`). A derived scope is
   marked `follows: "type"` (`tiers.FOLLOWS_KEY`, an IFU's included; Denis,
   ruling D3, 2026-10-13), and GATE leaves it out of the score: the type is
   in it already, combined when a second read confirmed it, so a certificate
   typed at 0.85 and confirmed at 0.85 scores 0.9775, not the scope's 0.85.
   A read's own scope kept because it agrees is not marked.
   Once the type is known to be an IFU, the certificate fields a read wrote
   before that are dropped, as on text (`_drop_ifu_certificate_reads`, review
   2026-10-13): the first image read is asked them while the type is open. Runs after each read, so a type the first read
   settles spares the second read the scope. Measured: two cheap reads agreed on
   `group` for an MDD certificate in the eval; this caught it.
6. `T2` (`models.t2`, Sonnet) for what is still open, never when nothing is.
   Its answer closes what it was asked, an **empty answer included**
   (`tiers._sonnet_closes`): the field held a value no rule settled, and the
   strongest read found nothing there. Two guards stay: an empty answer never
   shortens a `ref_list`, and T0 at 0.95 is never overwritten. A field Sonnet
   gave no answer for (a failed read) keeps what it holds. Review 2026-10-12:
   on the eval, four `cert_number` values only the second cheap read gave,
   contradicted by the first read and by Sonnet, survived Sonnet's null; one
   was a manufacturer SRN. Type to scope runs once more.
7. Each gate field read twice to the same value names its agreeing read
   (`_stamp_agreement`), as on text. A scan has no text to prove a regulation
   by its citation (`n.a.` would pass the check on an empty text layer), so it
   is never marked `proven`.

The pursued set is `needs_escalation`'s, keyed to what is KNOWN (T0 at 0.95 or
settled by a read) rather than to a value, so it only shrinks: a known IFU
stops the certificate fields, a known manufacturer-wide scope stops the item
identifiers. T0's own scope on a scan is mostly a guess (a DoC typed off its
file name gets `manufacturer` at 0.6), and by value it would have kept the item
identifiers from ever being asked.

Every read is kept: `_reads` on the attempt's `fields`, a list in read order of
`{step, model_id, fields: {field: {value, conf, page}}}` (a null `value` for
a field answered empty, None for a field the read gave no answer for) and
`failed: true` on a read that answered nothing; a list because jsonb sorts an
object's keys. Every field two reads answered differently is on the job result
as `disagreements` (`{field, steps, values}`) and counted as
`counts.read_disagreements` (`tiers.read_disagreements`; a read with no answer
for the field is left out, it gave no reading); disagreements are counted, not
sent to review (Denis, 2026-10-12). Failed reads are on the job result as
`failed_reads` (their steps) and counted as `counts.read_failures`
(`tiers.failed_reads`). A scan field's evidence carries `step` (its label), which
GATE ignores and `merge` ranks by.

**The gate fields' evidence** (plan Task 5, "Split 2026-10-12"; GATE reads it in
`gate._confidences`). On `type`, `regulation` and `coverage_scope` (`tiers.
GATE_FIELDS`, held equal to `gate.REQUIRED_FIELDS` by a test):

- `conf` stays the stored read's own raw number, with its value, quote, page
  and `model_id`.
- `agreed_model_id` / `agreed_conf` name another read that was asked the field
  and gave the same well-formed value (`checks.agree`, normalized), and its own
  raw number: the surest such read where several did. Stamped whatever the
  numbers; GATE combines the two only when each, calibrated, is at least
  `gate.agreement_min`, and stores the combination in `evidence.confidence`.
  Only a read can agree or be agreed with: never T0, the doc-class answer, or a
  scope type to scope derived (GATE never combines on a row with no model id).
- `proven: "citation"`, on `regulation` only, on the text ladder only: the
  checks prove it from the one citation printed (`checks.citation_proven`:
  a well-formed value, `settled`, and text for the whole document, since
  "only 2017/745 printed" is also a claim that 93/42 is not; a scanned page of
  a mixed PDF, an unread page or text past the budget leaves that unknown; an
  OCR layer counts as text, as `settled` and the measured route read it, so
  Celalux 3 is proven `n.a.`). A scanned page with a short visible overlay (a
  stamp, a scanner's caption: under 200 characters over a page images cover at
  least half of) is an `image` page (`pdf.page_classes`, review 2026-10-13,
  F7), so it stops every citation proof; a sparse page with no image (a cover,
  LUMIWHITE's signature page) is text and stops none. GATE scores it 1.0.
- `follows: "type"`, on `coverage_scope` only: the scope type to scope
  derived, on either ladder (`_scope_from_type`; step 5 of each; ruling D3).
  GATE leaves it out of the score; it keeps the type's own number, which
  `scope_stated` reads.

**Merge policy** (`merge`): the **stronger** read wins for the fields it
*returned* (rulings 2026-10-12): Sonnet's image read over a cheap image read
(`T2-first`, and below it `T2-second`, which only confirms) over T1 over a T0
guess under 0.95; T0 at 0.95 or more is never overwritten; equal strength,
later wins. On the text ladder each read is stronger than the one before, so
there it is exactly the old later-wins rule. **Two reads that agree** (plan
Task 5b, review finding 9; Denis's ruling D1, 2026-10-13): when the new read
gives the same well-formed value as the read the field holds, the stored
evidence is a read the checks verify (`checks.settled` against the text the
ladder read: its quote on its cited page, its value in the text), the surer of
those, the one held on a tie; only where neither is verified, or on a scan with
no text to check, is it simply the surer read (`_stores_over`). The other
becomes its agreeing read; the strength rule decides only between different
values. It applies after Sonnet too: Sonnet giving T1's value with its quote on
the page, T1's not, stores Sonnet's evidence. It applies to a confirming read
as well (`T1-second`, `T2-second` on a gate field it only confirms;
`_prefer_confirming`), whose value is never merged: where it wins, its evidence
is stored and the former stored read becomes the agreeing one. On the eval
replay (2026-10-13) the rule between merged reads changed no stored evidence;
on confirming reads it stored the confirming read on 8 of 79 text documents
and 12 of 34 scans (533048's scope among them, 0.97 over T1's 0.9), with no
disposition changed. Before, an image read
agreeing with T1 replaced T1's evidence by strength alone, its 0.5 over T1's
0.9 on one eval document (533048 RA_810, `coverage_scope`), and GATE's
`scope_stated` and supersede guard read that number. Considered: keeping the
earlier read on agreement (the plan's suggestion). On the eval replay that cost
two documents production and moved a third from staged to manual, all where
the earlier read was the less sure one (a Komet declaration whose T1 read MDR
at 0.6 and the image read at 0.8, an inferred answer; a VOCO scan whose two
cheap reads gave the scope at 0.6 and 0.8), so the surer one is kept instead. A T0 guess or the
doc-class answer is not a read: a read giving the same value replaces it as
before. `_proven` never has to choose between agreeing reads: `merge` has
already stored a verified one where either is. A blank later answer never erases
an earlier hit (one exception, outside `merge`: Sonnet's empty answer closes
the field on both ladders, step 6 above)
(`_is_blank`, `tiers.py:128-136`); one shrink guard applies only to
`_ENUMERATED_FIELDS = {"ref_list"}` (`tiers.py:142,145-154`) — a later tier
may not replace a longer list with a shorter one, because an LLM `ref_list`
is a bounded sample by construction (`REF_LIST_PROMPT_CAP = 150`,
`tiers.py:211`, injected into both prompts via `{{REF_LIST_CAP}}`,
`llm.py:29-38`) while T0's pdfplumber read is a full enumeration. For a list
the length decides before the strength: a longer list wins from any read
(except over T0 at 0.95), and only two lists of one length are settled by
strength. Keys that are not evidence (`_date_label_refused`, `_reads`,
`_pages_unread`) are copied as given. `merge(..., by_strength=False)` is the
pre-2026-10-12 rule, kept for `app/repair_ref_list.py`, which replays today's
T0 under stored answers and must not re-derive the fields T0 now reads at 0.95. Measured
loss from getting this wrong: 26 of 61 correct REF lists deleted by an
unconditional merge; 199 catalogue items of coverage lost to a truncated
LLM list overwriting a full T0 one (`tiers.py:167-182`). `integrity_flags`
(`tiers.py:224-247`) reports (never prunes) `ref-list-possibly-truncated`
when a T1/T2 list lands at exactly the cap — length is deterministic and
doesn't trust the model's own truncation self-report, which was measured
lying (doc 258: claimed "40 returned", actually 46 against a true 53).

**Calibration** (`calibrate`, `tiers.py:254-262`): `effective = raw *
factor(model_id, field)`, clamped `[0,1]`; T0 (`model_id is None`) is never
calibrated; factor defaults to 1.0 pending S0.4 calibration data.

**Batch state machine** (`app/extract/batching.py`). `advance_batch`
(`batching.py:42-56`) is a 3-state machine over `batch_ref`, keyed
`(content_hash, tier)`, where `tier` is the read's label (so two reads of one
kind never share a row): no row → submit + record, return `"submitted"`;
row with `resolved_at` → idempotent re-fetch, return `"done"`; row without →
poll `batch_client.done()`, return `"pending"` or resolve + `"done"`. It
lives in a side table, never in the job payload, so a crash between submit
and record can never double-submit (Invariant 9) — `batch_ref` is
`(content_hash, tier)` PRIMARY KEY (`migrations/001_queue.sql:70-77`). The
handler surfaces a non-done state as `{"_deferred": True, "waiting_on":
tier, "state": state}` (`app/handlers/extract.py:224-248`), which the runner
reads to skip `finish` and re-poll later (`docs/dev/handlers.md`
cross-cutting mechanics).

**Cost ledger** (`app/extract/economics.py`, `extraction_cost` table,
`migrations/010_extraction_cost.sql`). `cost_usd(model_id, usage, batch)`
(`economics.py:78-94`) raises `UnknownModelPricing` for a model with no
`PRICES` entry rather than costing it 0 — the never-silent rule applied to
money. `CallCost.cost` is a property for exactly this reason
(`economics.py:97-110`): capturing a call's tokens must never fail, so
`write_extraction_cost` (`tiers.py:336-353`) catches the exception and
stores `cost_usd = NULL` while keeping every token count — `extraction_cost`
has `cost_usd numeric(12,6)` nullable by design
(`migrations/010_extraction_cost.sql:32`), and the `extraction_spend` view's
`unpriced_calls` column exists to find those rows (`010_extraction_cost.sql:
41-50`).

**One row per paid call, keyed by the read's label** (since 2026-10-12). The
ledger is unique on `(content_hash, tier, extract_rev)`, and its `tier` column
holds the call's label from `llm.READ_STEPS` (`doc-class`, `doc-class-image`,
`T1`, `T1-second`, `T2-first`, `T2-second`, `T2`), not the evidence tier. Before
that the doc-class call (`AnthropicLlm.classify`) and the T1 read both logged
`T1`, so on every document T0 could not type the conflict clause dropped one of
the two rows without a word. `/status` → **Spend** shows the breakdown per
model, label and transport. The text ladder sends `doc-class`, `T1`,
`T2-first`, `T2` and `T1-second` (since plan Task 5b, for the gate fields); the
scan ladder `doc-class-image`, `T2-first`, `T2-second` (since Task 5b also for
the gate fields the first read settled) and `T2`. Both doc-class calls send
thinking off. A doc-class answer cut at `max_tokens` raises (`llm._classify_answer`)
instead of reading as a refusal, on text since Task 4 too (the job retries, then
lands in dead jobs); so does a batch doc-class result that carries no answer
(`app/handlers/extract.py` `_batch_classifier`).

**Read steps** (`app/extract/llm.py`, `READ_STEPS` / `resolve_read`). A read is
a label plus a kind (text / image), a model, a thinking setting and, for an
image read, an explicit page list (default pages 1-4, at most
`MAX_VISION_PAGES`). The request is routed by kind, never by label: until
2026-10-12 `_params` sent every label but `T1` to `models.t2`. The kind also
stamps `evidence.tier` (`T1` text, `T2` image), so GATE's page rule and the
REF-cap flag keep seeing exactly those two values. A label in `READ_STEPS`
takes its kind and model from its row, and naming either is refused: a
different read gets its own label, so it never shares a ledger row with the
real one. A label with no row must name both. `thinking=None` sends no
thinking key even where the row names one. With an explicit page list (sync
path only), the page the vision prompt cites (an image index) is translated
back to the document page. A cite outside the images sent becomes `None`, keeps
its value and quote, and is counted: the extract handler puts the total on the
job result as `counts.cited_page_outside_images`. GATE then raises
`evidence-page-missing` (blocking, manual review) only when the field is
`type`, `regulation` or `coverage_scope`; any other field is stored without a
page. The options reach `extract` / `build_requests` only as keyword
arguments, so every stub implementing `extract(tier, doc, filename, missing)`
keeps working. `AnthropicBatchClient.build_requests` refuses a page list until
`batch_ref` stores the pages sent ([limits.md](limits.md#built-but-not-wired));
`results` maps each result's label back to the read it was built from that
poll, else to the label's `READ_STEPS` row, and a label with no row raises
instead of being priced as `models.t2`, the old fallback. One extraction sends
each label at most once: `_finalize` in `app/handlers/extract.py` fails the
job before writing anything when a label repeats, since the ledger would keep
one of the two rows.

**Haiku 5.5 is priced by prompt length** (`economics.PRICES`, read off the
pricing page 2026-10-12): $0.10 / $0.50 per MTok up to 100,000 prompt tokens,
$0.50 / $2.50 over, for the whole call. Prompt length counts cache reads and
writes. Sonnet 5.5 reads its cache at 0.05x, not the default 0.1x.

**Prompt caching** (Task 6, 2026-10-12). `_system` (`app/extract/llm.py`) always
returns a list of blocks now: the static T1 or T2 prompt first, carrying
`cache_control: {"type": "ephemeral"}` (the default 5-minute cache), then the
playbook hints block when there is one, never marked. Sync reads and batch
requests share `_request`, so both carry it. What is cached is the marked block
alone, the prompt file's text: `t1_text.md` is 7,473 characters and
`t2_vision.md` 4,252, roughly 2,700 and 1,550 tokens on the 5.5 models. That is
an estimate, not a count: the research doc's fixed part of a call (system prompt,
schema and request line, counted with `count_tokens` on the 5.5 models) is 4,870
tokens for T1 and 3,696 for T2, and the 3,221 characters between the two prompts
make up the 1,174-token difference, about 2.7 characters a token. Whether the
format instructions the API adds for structured output fall inside the cached
prefix is not documented. The minimum cacheable prefix is 512 tokens on Haiku 5.5
and Sonnet 5.5 and 4,096 on Haiku 4.5; below it the API ignores the marker, with
no error. Since Task 4 (`models.t1` on Haiku 5.5) T1 and every image read clear
the minimum; on Haiku 4.5 T1 did not. A hit needs another call with the same label and model within 5
minutes: T1 and T2 never share an entry (different prompt, different model), so
reads come from the next document, as in a backfill, and a lone document pays the
write (1.25x on the prefix) and reads nothing. Batch requests carry the same
marker; hits there are best-effort. The doc-class prompt (2,738 characters) is
not marked: it would save about $0.0001 a call on Haiku 5.5 (estimated the
same way), and on Haiku 4.5, which `models.t1` named until Task 4, it falls
under the minimum. `Usage` records cache
read and write tokens but cannot tell a 1-hour write from a 5-minute one, which
is why only the default cache is used. `tools/haiku_compare.request_key` drops
`cache_control` before hashing, and turns a one-block system back into its text,
so responses cached before Task 6 still replay. Whether
`cache_read_input_tokens` rises above 0 is read off the first real run's ledger
rows; it is not provable offline.

**T0 template engine** ("engine-agnostic data", `app/extract/t0_layout.py`).
A `Template` (`t0_layout.py:37-43`) is `slug`, `manufacturer`, `anchors`
(text to match, never pixel/bbox coordinates), `ref_strategy` (one of
`table` / `text-column` / `stitched-table`), `ref_strategy_config`. Templates
are read through `app.playbooks.load_raw` (`t0_layout.py:58`), the same
store the rest of a playbook comes from — not a second glob with its own
cache. A playbook with no parse section is valid and silently skipped; a
malformed one is logged and skipped, never crashes the worker
(`t0_layout.py:10-15`).

**Evidence field construction — where each piece actually comes from.**
T0's `field_ev()` (`t0_templates.py:37-38`) builds `{value, conf, tier="T0",
verbatim, page}` — no `model_id`, no `archive_url`, no `extracted_at`. T1/T2's
`_to_ev()` (`llm.py:136-144`) builds `{value, conf, tier, verbatim, page,
model_id}` — still no `archive_url`, no `extracted_at`. **Both**
`archive_url` and `extracted_at` on the `evidence` row are filled by GATE,
not by anything in `app/extract/`: `_insert_evidence`
(`app/handlers/gate.py:622-678`) computes `field_url = ev.get("archive_url")
or archive_url` (`gate.py:646`) and writes `extracted_at` as a bare `now()`
in the `INSERT` itself (`gate.py:652`). The one exception is a per-field
override, not the general case: `_ref_from_companion`
(`t0_templates.py:1089-1118`) sets `ev["archive_url"] = companion_url`
(`t0_templates.py:1116`) when a
manufacturer's REF list is read off a separate annex file (Komet:
declaration + `..._RA_812_Liste_DoC.pdf`) — GATE's `field_url = ev.get(...)
or ...` is what makes that override survive into the row instead of being
overwritten by the document's own handle.

**T3 is not a tier `app/extract/` implements.** A human correction through
`gate.apply` is written entirely inside `_apply_edits`
(`app/handlers/gate.py:1118-1139`): `tier='T3'`, `model_id=NULL`,
`confidence=1.0`, `page=NULL`, `verbatim` = the human's own input, stamped at
`extract_rev = MAX(extract_rev)+1` for that doc so "current" always resolves
to the human's value. `app/extract/` never sees a T3 evidence row, with one
exception: `tiers.next_extract_rev` allocates a new reading's rev past both
`extraction_attempt` and the document's `evidence` revs (plan Task 5b, from
the Task 5a review), so a reading made after an edit no longer takes the
edit's rev. An edit made while a reading is in flight can still share its rev;
GATE holds edited fields against machine writes for that (`_reviewer_held`).
The batch path allocates the rev for each step's cost row as that step
resolves, so an edit landing between two polls splits one run's cost rows
across two revs.

**Evidence-page requirement** (Invariant 2's T1/T2-only page rule). Enforced
in `_require_complete_evidence` (`app/handlers/gate.py:238-266`): a required
field with `tier in ("T1","T2")` and `page is None` raises the blocking flag
`evidence-page-missing` (`PAGE_MISSING_FLAG`, `gate.py:235`); T0 and T3 are
exempt by construction (T0's filename-only path genuinely has no page, T3's
`page` is always `NULL`). This is **handler logic only** — `evidence.page`
is a nullable `int` with no `CHECK` distinguishing tiers
(`migrations/005_registry.sql:70`), so nothing in `app/extract/` or the
schema enforces it.

## Limits

- No config-driven escalation threshold — see Gotchas.
- `app/extract/pdf.py`'s `archive_url` handling is an explicit stopgap
  (`resolve_local`, `pdf.py:20-25`): a filesystem path or `file://` URL only,
  documented as standing in for the real `StorageAdapter` (`pdf.py:7-9`).
- `models.rank` (`app/config.py:206-216`) is defined but read by nothing —
  see Gotchas.
- The Batch API transport is batch-of-1 per `(content_hash, tier)`
  (`llm.py:302-305`); cross-document batching (the "sweep economy") is a
  later concern, not implemented here.

## Gotchas

- **The escalation threshold is a hardcoded default parameter, and it is
  NOT `cfg.gate.high`.** `run_extraction(..., threshold: float = 0.95, ...)`
  (`tiers.py:265`) is never called with an explicit `threshold=` anywhere in
  `app/handlers/extract.py` (the sync path calls `run_extraction`, the batch
  path the two ladders, all with the unpassed default). Since 2026-10-12 it
  decides only which T0 fields are KNOWN; a read's own confidence no longer
  moves a field up the ladder (the scan ladder's `extract.image_settle` aside).
  `cfg.gate.high` is a
  *separate* number, `0.92` (`app/config.py:57-70`), read only inside
  `app/handlers/gate.py` to gate four unrelated GATE-time decisions
  (coverage-scope confidence, the supersession fast path, document- and
  link-level production disposition). The two numbers *used* to be the same:
  `app/config.py:57-58` records that `gate.threshold.high` was `0.95` in the
  original scaffold and was moved to `0.92` by Denis's ruling of 2026-08-19 —
  `tiers.py`'s default was never updated to track that move. Conflating them
  is an easy, wrong assumption to make from the matching digits alone.
- **A scan is "no text page", not "page 1 has no text".** Since 2026-10-12
  the ladder routes on `pdf.has_text_page` (`page_classes`: a page of 200+
  characters, or an OCR layer), and a document with none takes the scan
  ladder: no T1 read of empty text, no forced T2. `pdf.is_scan` (page 1 under
  20 characters) still has two callers: `text_store.store_text`, whose
  `document_text.source = 'none'` (and so the `no_text_layer` anomaly and
  `reextract --dropped-scans`) still means "page 1 has no text". The text
  ladder no longer reads it (Task 4): a mixed PDF's image pages are what the
  page choice renders and what the null rule counts as unread. A document whose
  every page is short (`sparse`, under 200 characters) has no text page either,
  so it is read off its images too.
- **`T0` scope on a filename-typed certificate.** Until Task 4
  `derive_coverage_scope` gave EC and ISO `manufacturer` at 1.0 whatever the
  type's confidence, so a scan T0 typed `EC` off its file name carried a KNOWN
  scope and its item identifiers were never asked. The EC, ISO and IFU branches
  now carry the type's confidence below 0.95 (`_follows_type`), as the DoC
  branch did; type to scope derives the scope again once a read settles the type.
  On the eval this changed T0's scope on 7 of 79 text documents and 5 of 34
  scans, and no routing or disposition (cached replay, 2026-10-12).
- **A word after a Basic UDI-DI label is not a Basic UDI-DI** (review
  2026-10-13). T0's UDI regex took the token after the label, and
  `udi.validate_basic_udi` calls a plain word "unscorable", not invalid, so the
  checks let it pass: on the SFTP mirror 12 documents held a word (`PRODUCT`,
  `Thermaprep`, `Document`, ...; END-DoC-Aryane on the eval). Now `udi.is_word`
  (no agency's shape and no digit) refuses it in three places: T0 skips such a
  match and tries the next label (`extract_basic_udi`), and `checks.settled` and
  `checks.well_formed` never pass it. GS1, HIBCC and EUDAMED-assigned values,
  and unscorable codes with digits, are judged as before.
- **`models.rank` is read by nothing.** `app/config.py:206-216` defines
  `models.t1` / `.t2` / `.rank` / `.email_summary`; grepping `app/` for
  `.rank` (2026-08-31) turns up only that docstring and no code reading
  `cfg.models.rank`. The live T1 candidate ranker DISCOVER would use it for
  is not wired: `_default_rank` (`app/handlers/discover.py:167-170`) raises
  on every call, tracked as followup `discover-t1-ranking`, so a ranker
  failure degrades to a logged miss (`discover.py:203-204`) rather than
  reaching any model.
- **`archive_url` and `extracted_at` are not set anywhere in
  `app/extract/`** in the general case — both are GATE-constructed columns
  (`gate.py:646, 652`, detailed under Rules above). The one place
  `app/extract/` sets an `archive_url` at all is the companion-annex REF-list
  override (`t0_templates.py:1116`), and that is a per-field value GATE
  reads back, not the row's primary handle.
- **T3 evidence has no representation in `app/extract/` at all** — it is
  constructed entirely by `_apply_edits` in `app/handlers/gate.py:1118-1139`.
  A grep for `tier.*T3` inside `app/extract/` returns nothing.
- **`TARGET` lives in a leaf module for a reason that bit web, not
  extraction.** `app/extract/target.py` imports nothing on purpose: two
  callers outside the extractor (`app.playbooks._parse` and the playbook
  editor route in `web/registry.py`) need the 11 field names and run in the
  slim web image, which has no PyMuPDF. Importing `TARGET` from `tiers.py`
  (which chains to `app.extract.pdf` → `pymupdf`) broke every `/playbooks`
  row parse in that container on 2026-08-27; see `docs/dev/web.md` Gotchas
  for the user-visible failure. `tiers.py:29` re-exports `TARGET` so
  `tiers.TARGET` still works for the extractor's own callers.
- **A device's user manual is an `IFU` to the doc-class call, and it was not
  until 2026-10-13.** `prompts/doc_class.md` listed "an operation manual or
  user guide" as not a compliance document. Haiku 4.5 ignored that about half
  the time; Haiku 5.5 obeys it, and a refusal is terminal (Task 9 replay:
  MIYO processing instructions, doc 503, and the RAYFace user manual, doc
  1130, both stored as IFUs). Measured on 301 files T0 leaves `unknown`
  (production's doc-class traffic and web-fetched files from the local fetch
  history, hand-labelled; `tools/doc_class_check.py`, `eval/doc-class-check/
  20261013T203817`): false refusals of 165 compliance documents 108 as built,
  76 with adaptive thinking, 26 for Haiku 4.5 as production recorded it, and
  13 with the prompt naming user manuals, operating, processing and
  reprocessing instructions as `IFU` (thinking off; 12 with adaptive
  thinking, not worth its tokens); false acceptances of 92 other files 10,
  8, 10 and 10. The same prompt on the image call (51 files whose route the
  scanned-page rule changed): 13 false refusals of 29 before, 0 after, 0
  false acceptances of 21. Of the 13 left, 9 are text layers under the 30%
  cut, which `_unreadable` now sends to the image call (the 8 measured there
  were accepted). Service, repair and
  installation manuals for technicians stay out.

## Tests

| File | Pins |
|---|---|
| `tests/test_tiers.py` | Escalation gating, `merge`/shrink-guard, two agreeing reads in `merge` (a verified one first, then the surer; with no text, the surer; T0 and the doc-class answer are not reads), `GATE_FIELDS` equal to GATE's, `next_extract_rev` past evidence revs, `run_extraction`, `test_a_scan_takes_the_image_ladder_and_never_a_text_read` |
| `tests/test_tiers_text.py` | The text ladder: T0 settled fields never asked, what the text proves after T1, T1's empty answer and the null rule (labels, image pages, truncation), a failed read, T0 under 0.95, the image read's check / check-valid UDI / agreement / both empty, Sonnet prevailing and closing, the two suppressions, T1's pool, type to scope, the page choice, the reads record, hints, the text doc-class call, `T1-second` (agrees / disagrees / empty / failed, one call, what is not asked, a stored read under the floor, citation proof, an agreement under the floor, the verified read stored, Sonnet as the agreeing read or the stored one, the `asked` guard, a confirming read stored by ruling D1 and the scope following the type it took), the type-derived scope marked, the image doc-class route (a scanned page or an unreadable text layer among pages 1-2) |
| `tests/test_tiers_scan.py` | The scan ladder: routing (a mixed PDF stays text), the image doc-class call, 1/3/6-page scans and `pages-unread`, settle at 0.85, agreement at 0.70, both-null except the UDI, failed reads (never an empty answer, counted), type to scope, Sonnet only for what is open and its empty answer closing a field, the reads record, disagreements, `merge` by strength, `T2-second` confirming the gate fields (one call, its value never stored, never sent on, the surer of two agreeing reads stored, the derived scope following a type it took, no citation proof on a scan), the type-derived scope marked (an IFU's too) |
| `tests/test_checks.py` | `checks.py`: the text checks, page classes (a scan with a short overlay is an image page), `citation_proven` (also over real documents), and the scan shape / agreement checks |
| `tests/test_tiers_corpus.py` | Ladder behaviour against the seed corpus |
| `tests/test_t0.py`, `tests/test_t0_corpus.py` | `t0_templates.py` classification and field extraction |
| `tests/test_t0_layout.py` | The layout-template engine (`t0_layout.py`) |
| `tests/test_llm.py`, `tests/test_llm_schema_vocabulary.py`, `tests/test_llm_usage.py` | `llm.py` — schema shape, evidence construction, usage capture |
| `tests/test_llm_credentials.py` | Lazy client construction needs no key until a call is made |
| `tests/test_recorded_llm.py` | Replayed real API responses |
| `tests/test_batching.py` | `advance_batch` state machine |
| `tests/test_economics.py` | Pricing, `UnknownModelPricing`, batch discount |
| `tests/test_pdf.py`, `tests/test_pdf_text_sanitising.py` | `pdf.py` — `is_scan`, `bounded_text`, NUL sanitisation |
| `tests/test_udi_validation.py` | `udi.py` check-character algorithm against the HIBCC worked example |
| `tests/test_playbook_tier_a.py`, `tests/test_playbook_tier_b.py` | Playbook-steered extraction (hints, identity) |
| `tests/test_extract_handler.py` | `app/handlers/extract.py` — sync/batch dispatch, `_finalize`, defer signal |
| `tests/test_extract_cost.py` | `extraction_cost` writes from the handler |
| `tests/test_gate_candidate_handler.py` | GATE's `_insert_evidence`, `_require_complete_evidence`, `evidence-page-missing` |
| `tests/test_gate_apply_handler.py` | GATE's `_apply_edits` — T3 evidence construction |
