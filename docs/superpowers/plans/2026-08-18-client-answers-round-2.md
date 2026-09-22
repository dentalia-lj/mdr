# Plan — implementing Dentalia's round-2 answers (2026-08-18)

Source: the client's reply to `docs/2026-08-13-vprasanja-za-dentalio.md`, received
2026-08-18. Every number below was measured on 2026-08-17/18 against the live
registry, the BC export (`imports/Artikli 3.7.2026.xlsx`) and the 1.307-PDF SFTP
corpus — not carried over from earlier notes.

**Numbering trap.** They answered the 19-question version, in which #3 was the
Dentsply IH question that has since been deleted. Their 4–19 are the current
doc's 3–18. Their numbering is used in this plan's headings; the current doc's
number follows in brackets.

---

## Wave 1 — data only, no code

### 1.1 GC Corporation is the same manufacturer as GC Europe N.V. (their 4 [3])

*Answer:* "GC je enako oboje – na japonskem je uradni sedež – roba pa ponavadi
prihaja iz N.V."

*Now:* `playbooks/gc.json` carries `manufacturer: "GC EUROPE N.V."`,
`aliases: ["GC"]`, `bc_codes: [{LJ, 008}]`. `manufacturer_alias` holds
`008 → GC EUROPE N.V.` and `GC → GC EUROPE N.V.` (source `playbook`). A document
signed "GC Corporation" canonicalizes to nothing and binds zero items
(`app/manufacturers.py::canonicalize` is deliberately a no-mint lookup).

*Change:* add `"GC Corporation"` to `aliases`. Then `dentalia playbooks validate`
→ `dentalia playbooks sync`.

*Acceptance:* `manufacturer_alias` gains `GC Corporation → GC EUROPE N.V.`; the 6
corpus files naming GC Corporation bind on their next `gate.candidate`.

### 1.2 3SHAPE is one manufacturer (their 2 [2])

*Answer:* "Vsi trije so isti proizvajalec – včasih so bili trije – mislim da je
zdaj samo še 3shape trios."

*Now:* three vendor-master aliases (`10003 → 3SHAPE A/S`, `10004 → 3SHAPE MEDICAL
A/S`, `10005 → 3SHAPE TRIOS A/S`), no playbook. Items: 0 / 3 / 194. All 7 corpus
PDFs are in the registry and all 7 are unlinked.

*Change:* author `playbooks/3shape.json` — the Dentaurum pattern:
`manufacturer: "3SHAPE TRIOS A/S"` (the entity on the MDR CE certificates and the
code carrying 194 of the 197 items), `bc_codes` claiming 10003/10004/10005,
`aliases` covering the spellings the documents actually print — measured in the
corpus: `3Shape A/S`, `3Shape TRIOS A/S`, `3Shape Group`, `3Shape Poland Sp. z
o.o.` (all at Holmens Kanal 7, Copenhagen). Then validate → sync.

*Acceptance:* `playbooks reconcile` clean; the three codes resolve to one
canonical; the 7 documents bind. Closes the 3SHAPE half of
`[playbook-entity-rulings]`.

### 1.3 Renewal lead time is 30 days (their 18 [17])

*Answer:* "Bi rekla mesec dni prej."

*Now:* `Renewal.horizon_days = (90, 60, 30)` (`app/config.py:215`); the scheduler
scans at `max(...)` = 90 (`app/scheduler.py:229`); no TOML overlay is in use
(`DENTALIA_CONFIG_TOML` unset), so the dataclass default IS the live value.

*Change:* default becomes `(30,)`. One test asserting the scan horizon.

*Flag back to them:* 30 days is tight against their own "glede na to kako dolgo
traja da dobimo dokumente". Trivial to raise later — it is one config value.

---

## Wave 2 — small code, with tests

### 2.1 A declaration with no expiry expires 5 years after issue (their 15 [14])

*Answer:* "Kjer je datum napisan upoštevaj datum – kjer ga ni upoštevaj + 5 let
za potek."

*Now:* `report.expiring_documents` (`app/handlers/report.py:19-55`) selects
production documents by `COALESCE(d.validity_to, c.validity_to)` — the document's
own expiry, else the expiry of the certificate it cites (`cert_doc_id`). A
declaration with neither never enters the renewal loop. The same function feeds
the scheduler's expiry scan (`app/scheduler.py:127`), so the rule has exactly one
implementation site.

*Change:* a third COALESCE branch, `d.validity_from + interval '5 years'`, applied
to `type = 'DoC'` only. **Derived in the query, never stored**: a written
`validity_to` would be a production value with no evidence behind it
(invariant 2), and the extracted evidence must keep saying "this document states
no expiry".

*Test:* table-driven over the four cases — own expiry; no expiry + live cert;
no expiry + no cert + issue date; no expiry + no issue date (stays out of the
loop, nothing to compute from).

*Conflict to resolve first (blocking, see §5):* their answer to 16 [15] says class
I devices must have a DoC but need no certificate. Combined with this rule, every
class-I declaration now enters the renewal loop on a 5-year clock — the opposite
of what question 16 proposed and what the current docstring implements.

### 2.2 MDR Article 22 packs get their own document type (their 12 [11])

*Answer:* "Lahko se doda tudi to – posebna mapa."

*Now:* the type vocabulary is `DoC | EC | ISO | IFU | other`, defined in the
extraction prompt (`app/extract/prompts/t1_text.md:27`) and documented in
`docs/vocabulary.md:223`. `document.type` is plain text in SQL, and supersession
is scoped by `(type, regulation)` (migration 005), so a new type self-isolates —
no Article 22 pack can ever supersede a DoC and vice versa.

*Change:* add one value (proposal: `SPP`, systems/procedure packs), in the prompt,
`docs/vocabulary.md`, the PRD type table, and the UI labels; then re-extract the
~17 Dentsply endodontic-kit documents so they stop landing as `other`.

*Correction to what "posebna mapa" can mean:* the archive path is built at FETCH
time from a *guessed* bucket (`app/handlers/archiving.py:30-59`), before
`document.type` exists — so a new type does not produce a new archive folder.
Deliver the separation as a type plus a filter in the review UI, and say so when
answering them.

---

## Wave 3 — work packages (approval needed before any code)

### 3.1 CARL MARTIN is a real manufacturer (their 5 [4]) — the biggest one

*Answer:* "Absolutno proizvajalec – tudi direktno od njih iz Nemčije dobimo robo.
Vsi artikli, ki imajo oznako 081."

*Now, measured:* 2.567 items, every one flagged MD (60% of all 4.265 confirmed
devices), spread over 886 groups; alias `081 → CARL MARTIN` from vendor-master;
**no playbook, no SFTP folder, zero documents in the registry**; 2.276 items (89%)
have no `mfr_ref`, so `ref-list` matching has almost nothing to bite on and
`ref-item` (the C12 basis) plus name-family will carry the load.

*Work:* author `playbooks/carl-martin.json` (legal entity + domain +
`doc_sources`) → run DISCOVER over the 886 groups (Brave adapter is configured,
`adapters.search = brave`) → FETCH/EXTRACT what comes back → measure link yield
before promising coverage. Expect an email request for a document pack to be
faster than crawling; that depends on Wave 3.4.

*Check first:* alias `283 → MARTIN GEBRUEDER` exists as a separate BC code. Carl
Martin GmbH belongs to the Gebrüder Martin / KLS Martin group — if 283 is the same
maker, two identities will split its documents. One question to the client, or
one look at what 283's items actually are.

*Estimate:* 1–2 days to first linked documents; coverage unknown until we see what
Carl Martin publishes. This is the single largest coverage lever in the project.

### 3.2 KOMET code conversion (their 9 [8]) — still blocked

They answered only sub-question 4 ("ne – komet je specifičen", so no other
manufacturer needs a code translation) and attached a price list — **which never
reached the repo**: the newest spreadsheet under `imports/` is the 6 July
`1-Where to find DoC (Medical Devices Only).xlsx` (3.801 rows). Sub-questions
1 (is the reordering an official ISO 6360 rule), 2 (who owns and refreshes the
mapping) and 3 (the 82 unmatched articles) are unanswered.

*When unblocked:* the reorder is a second `ref_normalize` strategy beside
`strip-trailing-letters` (`playbooks/README.md`, applied in
`validate._ref_gate_for_group`), plus a decision about where the xlsx mapping
lives — it is a third matching input, neither document nor catalogue, and today
there is no home for one.

*Size:* small — the strategy + tests once the rule is confirmed; the
mapping-table home is a design question worth its own short spec.

### 3.3 Device class from documents (their 6 [5]) — see §4

### 3.4 Email stage (their 19 [18])

`mdr@dentalia.si`, an Outlook mailbox with no Microsoft 365 — so no Graph API.
Open options for them/IT: IMAP with an app password if their Exchange allows it,
or a forwarded copy into a mailbox we control.

*Now:* `email.poll` / `email.request` / `email.reminder` exist in the closed
`job_type` enum (migration 001) but have **only the no-op handler** — there is no
`app/handlers/email.py`. The scheduler already emits `email.request` for expiring
documents behind `scheduler.expiry_email_enabled = False` (`app/config.py:370`,
`app/scheduler.py:134`), so the producer side is wired and the consumer is empty.

*Estimate:* L, and independent of which connection method they pick.

### 3.5 Deployment (their 20)

Contact is Mitja; a microserver already exists for a CRM/service application and
the MDR documents would sit on the same box. Needed before anything: OS, whether
Docker runs there, disk for the archive, backup, and how ingress is exposed
(today: caddy + HTTP Basic on loopback). Ask Mitja directly.

---

## 4. Device class — is it even readable from the documents?

Measured 2026-08-18 over all 1.303 readable corpus PDFs (PyMuPDF, first 6–10
pages per file).

**Yes, it is there — in three distinct shapes.**

| Shape | Example | Files |
|---|---|---|
| Labelled field, document-level | IVOCLAR: "EU Risk Class (MDR Annex VIII) **Class IIa**" | 122 (IVOCLAR) |
| Labelled field, document-level | VOCO: "Risk class …" | 110 (VOCO) + 8 Dentsply, 3 Komet, 1 GC, 1 Planmeca |
| **Per-article table column** | KOMET `100-008_Liste.pdf`: `ID / Packing group / REF / Description / UMDNS / **MD Class** / CE Sign` → `1.204.005 … IIa` | 70 (KOMET) |
| German label | KOMET DoC: "**Klasse IIa** Regel 6" | 3 |

Aggregate: **623 of the 1.027 text-bearing files carry a class value**; 573 of
those state exactly one class for the whole document, 50 state several. 276 files
are scans with no text layer at all (201 of them Dentsply) and would need vision.
The multi-value files are mostly EC certificates and Article 120 / 2023/607
confirmation letters, where "class IIa, IIb, III" is transition boilerplate, not a
statement about a product — 66 files match that boilerplate pattern and must be
excluded, or the extractor will confidently assign a class to nothing.

**Two manufacturer spreadsheets already carry per-article class**, which is
cheaper than any extraction:

| File | Rows | Class column | Catalogue items it matches | of which blank in BC |
|---|---|---|---|---|
| `GC/Devices by Class_22042026.xlsx` | 3.098 | `Class` | 377 | **69** |
| `IVOCLAR/IVOCLAR MD or NOT.xlsx` | 8.353 | `MDR Classification (EU)` | 1.126 | **59** |

### The hard finding: extraction cannot fix the blank column

**9.763 of the 11.693 unclassified items (83%) belong to manufacturer codes with
no corpus folder at all** — no documents exist for them yet, so there is nothing
to read a class from. Only 1.930 blanks sit under the 12 brands whose documents we
hold, and only the subset a document actually names can be classified. The biggest
blank blocks are 004 HENRY SCHEIN (1.111), 002 INSTITUT STRAUMANN (918), 160
NEODENT (796), CEFLA (363 + 232), 053 IMES-ICORE (331), 043 INTERDENT (232).

So class extraction **follows** document coverage, it cannot lead it. It is worth
building, but it is not the answer to "who fills the column" — the honest answer to
Dentalia is: the class arrives with the documents, one manufacturer at a time, and
until then the coverage denominator is the 4.265 confirmed devices.

### Proposed process (needs approval — it adds an extracted field, a PRD change)

1. **New extracted field `device_class`** on the document, values from a closed
   vocabulary (`I`, `Is`, `Ir`, `Im`, `IIa`, `IIb`, `III`), with the usual evidence
   tuple. T0 templates for the three measured shapes (they are anchor-plus-region
   patterns, exactly what `t0_templates.py` already does for dates), LLM tiers as
   fallback, boilerplate rejected by an explicit "confirmation letter / Article
   120" guard.
2. **Per-row class where the table carries it** (KOMET's `MD Class` column): this
   is a `ref_strategy` extension, not a scalar field — the REF list gains a class
   per row. Highest-value single template in the corpus.
3. **Propagation, read-only:** an item linked to a document that states one class
   inherits it as a *proposal*, never as a write to `item_mirror.product_class` —
   BC stays the source of truth for the catalogue.
4. **Feedback loop:** a report/CSV of proposed classes per item for Dentalia to
   load into BC. That is what actually moves the 11.693, and it is what they asked
   for when they said the class is on the documents.
5. **Spreadsheet ingest (optional, cheap):** GC and IVOCLAR class columns as a
   second proposal source — but their answer to question 12 [11] (are the
   spreadsheets current?) is still missing, so this stays behind that ruling.

*Estimate:* field + templates + tests M (~1 day); per-row class S–M; feedback
report S. Sequence it after the full-corpus backfill, since every extra document
raises the yield.

---

## 5. Decisions I need from Denis before Wave 2/3

1. **The `md_flag IS TRUE` gate on manufacturer-scope links**
   (`app/handlers/gate.py:694` and `:982`). Given the class column will never be
   filled by Dentalia, blank-class items can never receive a manufacturer-wide
   document. Measured today: 1.425 items hold mfr-scope links, every one
   `md_flag = true`; the 11.693 blanks hold 273 REF-based links and nothing else.
   Options: relax to `IS NOT FALSE` (one predicate, ~11.7k items suddenly in
   scope), keep as is and define coverage over confirmed devices only, or make it
   per-manufacturer. **My recommendation: keep the gate, redefine the denominator,
   and let class extraction + the feedback report move items into `true` honestly.**
2. **Where a proposed class lives** — report only, or a nullable
   `proposed_class` beside `product_class`. Report-only is invariant-safe and
   reversible; a column invites someone to treat it as truth.
3. **Class I in the renewal loop** — their answers to 15 and 16 conflict (§2.1).
   This needs one line back from them before the +5y rule ships.

---

## 6. Still blocked on the client

Carried into the round-3 question set (`docs/2026-08-13-vprasanja-za-dentalio.md`):
non-MDR documents; are the SFTP spreadsheets current; documents with no items
(keep/discard + where they should be visible); ratification of the signing-date
rule; the class-I renewal conflict; the Komet price list and sub-questions 1–3;
how 004 HENRY SCHEIN's own-brand items can be told apart; the mailbox connection
method; and Mitja's server details.

---

## 3.6 Re-attaching a filed document when a new item appears (added 2026-08-18)

Denis, 2026-08-18: *"if it doesnt autoattach, let's plan what needs to change and
where"*.

**It does not today, verified:** `ingest` emits `resolve.group` for new/changed
MD items only (`app/handlers/ingest.py:195`) and nothing ever revisits a settled
document. A document that was `filed` because Dentalia stocked none of the 1.605
codes it names stays filed forever, even after Dentalia starts stocking one.

**Why it is cheap.** Matching re-runs for free over persisted extractions — the
precedent is `app/repair_ref_list.py:105`, which re-emits `validate.doc` with
`{content_hash, group_id, extract_rev}` and dedupe key
`validate:{hash}:{rev}:{group}`. No LLM call, no fetch. `validate.doc` is already
in the closed enum, so this is not a PRD change. `filed` is in
`SETTLED_DISPOSITIONS` only for resolving manual tasks; nothing bars re-gating,
and `_is_out_of_catalogue` simply stops returning true once a link exists.

**Two places it could live:**

1. **In RESOLVE, per changed group.** Emit `validate.doc` for every `filed`
   document bound to that group's canonical manufacturer. Immediate, but fans
   out per item: a batch adding 50 IVOCLAR items would emit up to 90 documents ×
   50 groups before dedupe.
2. **In the SCHEDULER, one nightly tick** (`refile-scan`, modelled on
   `expiry-scan`, `app/scheduler.py:115`): re-emit `validate.doc` for filed
   documents whose manufacturer gained or changed items since the last tick.
   One job per document per night at most, and the work is idempotent.

**Recommendation: (2).** Coverage is not a same-minute concern, the fan-out is
bounded by construction, and the scheduler already owns exactly this shape of
periodic re-derivation. Add the count to the tick's result so a run that re-files
nothing is visible as such.

**Estimate:** S–M, ~half a day including a table-driven test that a filed
document flips to production when a matching item is ingested — the test is the
point of the feature.

**Live sizing (2026-08-18):** 162 filed documents, of which 155 are declarations
that link nothing.
