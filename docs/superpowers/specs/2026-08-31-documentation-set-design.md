# Documentation set — client guide and developer docs

**Status:** proposed, 2026-08-31. Format approved by Denis the same day (four decisions below); content not yet written.

Two new documentation sets. `docs/guide/` is for Dentalia's compliance staff and
documents the **web image only**. `docs/dev/` is for engineers and covers the whole
project. Neither restates what an existing doc already owns.

## Decisions taken 2026-08-31

| Question | Answer |
|---|---|
| Client guide language | EN + SL pair, matching `2026-08-24-what-the-system-does-{en,sl}.md` |
| Client guide granularity | One page per UI page (18) plus getting-started, daily-work, glossary, FAQ |
| Dev docs boundary | Thin layer; links into the existing maintained docs, never duplicates them |
| Delivery | Markdown under `docs/` is the source. A Python script renders the client set to HTML styled as the app itself (§F) |
| Slovenian pass | One sweep at the end of the writing, but **before** the HTML generator runs (§E step 6) |

## Why these two sets and not more docs in the existing files

Audited 2026-08-31. `docs/` already holds five live, maintained dev docs
(`architecture.md`, `runbook.md`, `code-map.md`, `troubleshooting.md`,
`vocabulary.md`) plus the normative contract set, and CLAUDE.md carries a standing
rule that a change invalidating one of them updates it in the same session. A
second copy of any of that would drift within weeks.

What is genuinely absent:

- **Any task-oriented guide for a non-technical reader.** The only client-facing
  file in the tree is `2026-08-24-what-the-system-does-{en,sl}.md`, and it is a
  dated status report about the data, not a guide to the screens.
- **A regulatory glossary.** MDR, MDD, DoC, IFU, REF, UDI, notified body and SRN
  appear throughout the docs and the UI and are defined nowhere.
  `docs/vocabulary.md` defines the *code's* vocabulary, not the regulation's.
- **A consolidated config reference.** Env vars are documented ad hoc across
  `runbook.md` and `troubleshooting.md`; `app/config.py` and PRD §11 are the real
  source of truth and are referenced but never reproduced.
- **A manufacturer-onboarding guide.** Split across four `superpowers/specs/`
  design docs plus the `/playbooks` route notes in `runbook.md`.

## A. `docs/guide/` — client-facing

```
docs/guide/
  README.md              index: "I want to… → go here"
  00-getting-started.md  logging in, the sidebar, the pulse strip, what the counters mean
  01-daily-work.md       clear Review → clear Manual → check Failed
  pages/
    status.md  review.md  items.md  documents.md  manufacturers.md  playbooks.md
    expiry.md  emails-in.md  drafts-out.md  processing.md  data-quality.md
    manual.md  failed.md  scheduler.md  ingest.md  upload.md  import.md  search.md
  glossary.md            regulatory + system words, plain language
  faq.md                 "why is this still waiting?", "why can't I edit that?"
```

The 18 page files mirror the sidebar in `web/templates/base.html:97-125` exactly,
in its order (Registry, Correspondence, Pipeline), so a reader can go from a screen
to its page without knowing our vocabulary.

### Fixed page template

Every file under `pages/` uses these eight headings, in this order, and nothing
else. A page with nothing to say under a heading says so in one sentence rather
than dropping it.

| Heading | Content |
|---|---|
| **Status** | Live / Partly live / Not yet built. Keeps gated-off features honest |
| **What this is** | One or two sentences, no jargon, no internal term unglossed |
| **When you use it** | The trigger that brings someone to this screen |
| **Before you start** | Requirements: access, data that must already exist, prior steps |
| **What you do** | Numbered steps, one action per step |
| **What happens then** | Expected output stated in registry terms the client cares about |
| **What can go wrong** | The outcome table (below) |
| **Related** | Links to other guide pages, and to glossary entries used on this page |

### The outcome table

The load-bearing part, and the reason this set exists. It turns internal
enumerations into something a compliance officer can act on. Columns are fixed:

| You see | It means | What to do | Good or bad |
|---|---|---|---|

Source material already exists in code — `FLAG_EXPLANATIONS` at `web/app.py:251`
holds one-sentence explanations for the 13 VALIDATE flags, and `_ui.html:42-54`
holds the 11 completeness states. The guide expands each into cause, action and
severity; it does not paraphrase the code, it explains the situation.

### Writing rules for the client guide

Non-negotiable. The first draft of `pages/review.md` broke most of these and had
to be rewritten.

- **One idea per sentence.** Aim under twenty words.
- **Never use a word the app does not show the reader.** Banned outright:
  registry, pipeline, job, queue (in our sense), enqueue, handler, payload, tier,
  invariant, gate, evidence tier, match basis, supersession, staged (except in
  the translation table below).
- **Second person, active voice.** "You approve it", never "it is approved".
- **Every page opens with one sentence** in bold saying what the screen is for,
  before anything else.
- **Every page with buttons opens with a "Can I break anything here?" box.**
  Non-technical readers hesitate before pressing things; answer it first.
- **Name the exact button text in bold**, matching the screen character for
  character.
- **State consequences, not mechanisms.** "The document counts from now on", not
  "the link transitions to production".
- **Group the outcome table by what the reader should do** — usually approve /
  look closer / usually reject — not by internal flag name. A flat list of
  thirteen equal-looking rows is not usable at 9am.
- **A translation table for words the screen still shows.** See below.

### The screen speaks our language, and the guide has to bridge that

Measured 2026-08-31: the Review screen itself prints internal vocabulary at the
user. Literal on-screen text includes *"Staged links on production documents
(C5)"*, *"untrusted-basis (e.g. name-family) links"* and *"Terminal, and nothing
to decide"* (`web/templates/staging.html:19-67`). Other pages do the same.

No guide can make a non-developer comfortable while the screen reads like that.
Until the wording is changed, every client page carries a **Words the screen
uses** table translating what is actually printed. Logged as a followup — the
real fix is in the templates, not the docs.

### Glossary

Two sections, both plain language.

- **The regulation's words:** MDR, MDD, DoC, EC certificate, IFU, ISO certificate,
  SPP, notified body, REF, UDI and Basic UDI-DI, EUDAMED, SRN, device class
  Is/Im/Ir.
- **Our words:** staged, published, filed, rejected, superseded; evidence; group;
  playbook; sweep; link; match basis; tier.

Every value the UI can print gets an entry: 5 document statuses, 4 link statuses,
13 flags, 11 completeness states, 9 match bases, 6 document types, 4 scheduler
verdicts, 4 draft statuses, 4 device-class-check verdicts.

### Language pairing

`docs/guide/…/<name>.md` is English; `docs/guide/…/<name>.sl.md` is Slovenian.
Same headings, same tables, same order, so a diff of one has an obvious
counterpart in the other. English is written first and is the working copy;
Slovenian is what the client reads.

## B. `docs/dev/` — developer-facing

```
docs/dev/
  README.md               what each dev doc owns, and what it deliberately does not
  00-orientation.md       clone → compose up → migrate → ./scripts/test.sh green → first change
  01-lifecycle.md         one document end to end, as built (not as designed)
  handlers.md             the reference table, one row per queue tag
  web.md                  the web layer for devs
  extraction.md           T0→T1→T2→T3 as built
  config-reference.md     every key: default, env var, what it controls, blast radius
  playbook-authoring.md   onboarding a manufacturer, start to first document
  changing-things.md      adding a job type / migration / config key / route / table
  limits.md               what the system will not do, and why
```

`docs/dev/README.md` opens with the boundary statement, so the rule is visible
before anyone adds a page:

> These files link into `architecture.md`, `runbook.md`, `code-map.md`,
> `troubleshooting.md` and `vocabulary.md`. They never restate them. The PRD v3
> and the schema sketch stay normative on contracts and schema. If a dev doc and
> one of those disagree, the other document wins and this one is a bug.

### Per-module page template

Used by `web.md`, `extraction.md`, and any module page added later.

| Heading | Content |
|---|---|
| **Idea** | What this layer is responsible for, one paragraph |
| **Where** | File map with `file:line` for the entry points |
| **Input → Output** | What crosses the boundary in each direction |
| **Rules** | What is enforced, and by what: DB constraint, handler code, or test |
| **Limits** | What it deliberately does not do; stubs, gaps, unwired modules |
| **Gotchas** | The traps that cost an afternoon |
| **Tests** | Which test file pins this |

### `handlers.md`

A compact index table (tag, entry point, emits, tests) plus one five-line block
per tag — consumes, writes, emits with dedupe key, fails how, note. Not 19 files:
the job-type handbook already owns the pseudo-code, so this is a navigational
index over the as-built code, closing with the cross-cutting runner/queue
mechanics and the two ruled invariant-1 exceptions.

### Gotchas that must survive into the dev docs

Found during the 2026-08-31 recon; each is a real trap and each belongs in a
specific file.

- `tiers.run_extraction`'s escalation threshold is a hardcoded `0.95` default
  parameter, never read from config, and is **not** `cfg.gate.high` (0.92).
  Conflating them is easy and wrong. → `extraction.md` Gotchas.
- `cfg.queue.visibility_timeout_s`, `backoff_base_s`, `backoff_cap_s` and
  `max_attempts` are read nowhere. `app/queue.py` uses its own function defaults.
  The four `QUEUE_*` env vars currently do nothing. Verified 2026-08-31.
  → `config-reference.md`, blast-radius column.
- `web.require_authenticated_user=False` makes `gate.apply`'s `decided_by` come
  from an untrusted form field instead of the Caddy-verified header.
  → `config-reference.md` and `web.md` Rules.
- `app/robots.py` is built and tested but has no caller anywhere in `app/` or
  `web/` — landed ahead of the S2.1 crawl recipe. → `limits.md`.
- `playbook.reonboard` has no handler: it resolves to `noop._noop`, which raises
  `UnimplementedStage` and dead-letters rather than reporting false success.
  → `handlers.md` and `limits.md`.
- `GoogleDriveStore.put` raises `NotImplementedError` (gap G7);
  `ImapEmailAdapter` is written but not wired (gap G8). → `limits.md`.
- Invariant 1 has two ruled exceptions, both CLI-only repair scripts, never
  registered against a job tag: `app/repair_archive_urls.py` and
  `app/repair_mfr_overbind.py`. → `changing-things.md`.
- Invariant 5 (MDR never supersedes MDD) is **not** a DB constraint — it compares
  two rows and is enforced in the GATE handler only. Same for invariant 2's
  evidence completeness and the T1/T2-only page rule. → `limits.md` and `web.md`.

## C. Keeping them true

Three guards, each matching something the repo already does.

1. Add `docs/guide/` and `docs/dev/` to the CLAUDE.md working rule that keeps the
   operational docs current.
2. A drift test on the client glossary and on `handlers.md`'s tag list, modelled
   on `tests/test_vocabulary_doc.py`. A new `job_type` enum value or a new
   `doc_status` fails the suite until both docs name it. Note the CLAUDE.md
   selection rule: this test lives in the full-suite tier.
3. Every client page carries the **Status** line, so a gated-off feature is
   documented as *not yet* rather than promised. Ones that need it today: email
   polling (`scheduler.email_poll_enabled` defaults False), Drafts out (the
   system never sends — a human sends by hand and marks `sent`), EUDAMED sweep
   (needs an operator release, never runs unattended).

## D. Verification requirement

Nothing in either set ships on the strength of a recon report. Each page gets a
pass against the live code or the running UI before it is committed, and any
figure it states carries the date it was measured.

## E. Build order

1. `docs/guide/glossary.md` + `.sl.md` — everything else links into it.
2. `docs/guide/00-getting-started.md`, `01-daily-work.md`, `pages/review.md` —
   the daily workflow, the highest-value slice.
3. Remaining 17 `pages/*.md`, in sidebar order.
4. `docs/dev/README.md`, `handlers.md`, `config-reference.md` — the three with no
   existing owner.
5. `docs/dev/00-orientation.md`, `01-lifecycle.md`, `web.md`, `extraction.md`,
   `playbook-authoring.md`, `changing-things.md`, `limits.md`.
6. Slovenian pass over the whole client set. **Before** the generator, so it
   renders both languages in one go and the SL pages never lag the EN ones.
7. `scripts/build-guide.py` (§F), rendering both output shapes.
8. Index both sets in `docs/README.md`; add the drift test; update CLAUDE.md.

## F. HTML output for the client guide

Denis, 2026-08-31: the client set has to exist as HTML, it should look like the
app rather than like a generic document, and a Python script generates it from
the Markdown.

### It wears the app's own skin

Not a documentation theme. The guide uses the same design tokens the UI uses, so
it reads as part of the product the client already knows:

- **Palette** — the dentalia.si mint tokens verbatim from
  `web/static/css/style.css:68-92`: `--dentalia-primary #89c8ac`,
  `--dentalia-primary-strong #56b088`, surfaces `#f3f9f7` / `#e9f5f0` / `#f2f9f6`,
  ink `#33312b`, muted `#464646`, border `#d8ddda`, amber `#ffb354`,
  danger `#d64545`. Same radius scale.
- **Type** — Sora for headings, Work Sans for body, the same vendored woff2 files
  the app serves. Body is 15px rather than the app's 14px: the app's size is
  tuned for dense tables, a guide is read in paragraphs.
- **Layout** — the app's sticky 216px left sidebar, the same `nav-label` section
  headings, the same active-item treatment. The guide's own nav mirrors the app's
  menu order (Registry, Correspondence, Pipeline), so "the page I am on in the
  app" and "the page I am on in the guide" line up.
- **Badges** — the real `.badge-production` / `-staged` / `-rejected` /
  `-superseded` / `-filed` rules, so the translation table shows the actual pill
  the reader sees on screen, in the actual colour.
- **Buttons** — `Approve` in primary-strong, `Reject` outlined, matching
  `btn-approve` / `btn-reject`.
- **No dark mode.** The app has none (`body{background:#fff}`); adding one to the
  guide would make it look like a different product.
- **Print stylesheet** — sidebar hidden, black ink, no page break inside a table,
  callout or bucket. Compliance staff print things.

`docs/guide/review.html` is the worked sample and the styling target the
generator must reproduce.

### The asset problem, and how the generator handles it

The real fonts are 191 KB base64 for the five faces needed (Sora latin + latin-ext,
Work Sans latin + latin-ext + italic). Slovenian needs the `-ext` subsets for
&#269; &#353; &#382;, so neither language can drop them.

Inlining that into every page would be 191 KB &times; ~20 pages &times; 2 languages
&asymp; 8 MB of duplicated font data. So the generator emits **two shapes**:

| Output | Shape | For |
|---|---|---|
| `docs/guide/html/` | One `.html` per page, plus `assets/guide.css` and the woff2 files alongside | Browsing, hosting on a share. Offline, no network calls, same as the app |
| `docs/guide/dentalia-guide-<date>.html` | Every page concatenated into one file, fonts and logo base64-inlined | Emailing, printing, handing over. One file, ~200 KB plus content |

Both are offline. "Self-contained" means no network call, not necessarily one file.

### The script

`scripts/build-guide.py`, run by a developer, never by the app. Reads the
Markdown, renders with `markdown` or `mistune` (one new entry in the dev extra),
injects the shared stylesheet and the nav, writes both shapes. The web image is
untouched: this is not a runtime feature and adds no dependency to it.

The drift test still reads the Markdown, not the HTML, so the generator can never
hide a stale glossary.
