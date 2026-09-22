# The playbook probe, and the onboarding wizard it becomes — design

Design proposal, 2026-09-03. **Nothing here is implemented.** Approved in mockup
by Denis the same day (`Playbook Onboarding Wizard`, artifact); this document is
the contract that mockup implies.

Implements **S2.2 (onboarding agent)** from `PHASES.md` §C, which has been
`NOT STARTED` since the plan was written. It depends on, and does not duplicate,
`docs/superpowers/specs/2026-08-21-playbook-crawl-recipe-design.md` — that
document owns the crawl recipe and the DISCOVER rung; this one owns the human
who authors a recipe and the proof they see before saving it.

Verified against the code on 2026-09-03. Every claim carries a
file:line; where this document disagrees with an older doc, the code wins.

> **Line numbers in `web/registry.py` and `web/app.py` are volatile.** A parallel
> session was editing both while this was written (BC API integration, EUDAMED
> guide pages, officer scenarios). Symbols are named alongside every line
> reference for that reason; trust the symbol, re-find the line.

---

## 1. The problem, stated precisely

**33 of 384 canonical manufacturers have a playbook** (38 files under
`playbooks/`, several covering more than one BC code). The other 346 are reached
by no recipe, so every document they publish is found by search or not at all.

Authoring a playbook today means: write JSON by hand, run
`dentalia playbooks validate`, run `dentalia manufacturers seed --apply`, and
only then does the web editor at `/playbooks/{slug}` become usable. The editor
itself is good — Tier A/B fields, optimistic locking on `rev`, revision history,
revert, BC-code claiming (`playbooks_page` / `playbook_detail_page` / `playbook_save`, `web/registry.py:1945-2050`). It has one structural
hole:

> **`playbook_detail_page` (`web/registry.py:1976`) reads the `manufacturer` row and sets
> `detail["editable"] = row is not None`.** A playbook that is not already in the
> database cannot be edited, and there is no route that creates one. The editor
> is a *back* door with no front door.

And a second, subtler one: **nothing anywhere proves a recipe works before it is
saved.** An author writes `index_url` and `link_pattern`, saves, and finds out
whether they were right when a discovery run either produces documents or
quietly produces none.

## 2. What is true today (verified 2026-09-03)

**The tag exists and is unbuilt.** `playbook.reonboard` is in the closed
`job_type` enum in the ORIGINAL migration (`migrations/001_queue.sql:27`), not an
addition. It has a config flag (`Scheduler.failure_reonboard_enabled`, default
`False`, `app/config.py:558`), a producer (`app/scheduler.py:346`), and the
raising placeholder as its handler (`app/handlers/noop.py:57`). PHASES.md §Now
item 11 calls it "the **only** unbuilt tag".

**The design always meant one path with two triggers.**
`docs/dentalia-mdr-pipeline-ground-truth.md:41`: *"a frontier agent explores each
site once at onboarding, drafts the playbook, human approves... Runs at
onboarding and on reonboard trigger."* PHASES.md §C S2.2 repeats it. So a probe
for a NEW manufacturer and a re-probe for a manufacturer whose site changed are
the same operation, and `playbook.reonboard` is their shared home.

**Consequence: no PRD change and no `ALTER TYPE`.** Invariant 7 (job types are a
closed enum; a new tag is a PRD change plus a migration) is satisfied by
construction, because this slice adds no tag.

**The harvester is being built as a pure function** (`app/crawl.py`, slice 2 of
the crawl-recipe ladder): `(html, base_url, link_pattern, same_host_only,
allow_hosts, max_links) -> HarvestResult`. No I/O, no DB, no network. That purity
is what lets one implementation serve both the DISCOVER rung and this probe.

**The web process is a producer only.** CLAUDE.md invariant: zero write grants on
`document`/`item_document`/`evidence`. `/manufacturers/{name}/discover`
(`web/registry.py:1627`) already establishes the pattern — the UI enqueues, a
worker acts. This design follows it rather than making the UI fetch.

## 3. The proposal

A probe is a `playbook.reonboard` job whose result is **shown, not acted on**.

```
operator clicks "Probe"  ->  playbook.reonboard {slug, index_url, ...}
                                      |
                          worker: robots -> lease -> fetch -> app.crawl.harvest
                                      |
                              INSERT playbook_probe (counts, sample links)
                                      |
                      HTMX polls /playbooks/{slug}/probe/{id} and renders it
                                      |
                    operator clicks "Save recipe" -> existing save path
```

The handler **emits nothing**. It is the only consumer of `app.crawl` that does
not produce `fetch.url`, and that asymmetry is the whole point: an operator must
be able to look at a manufacturer's library without committing the pipeline to
fetch 1.329 files from it.

### 3.1 Payload

`playbook.reonboard` payload today is `{manufacturer, ...}` from the failure
monitor (`app/scheduler.py:323`). Payloads are **additive-only** (CLAUDE.md), so
the probe extends rather than replaces:

```jsonc
{
  "slug": "nsk",                                  // which playbook this is for
  "index_url": "https://www.nsk-library.com/",    // what to probe
  "link_pattern": "\\.pdf$",                      // the candidate recipe
  "same_host_only": true,
  "max_links": 200,
  "requested_by": "operator@example.test"         // for the audit row
}
```

The handler branches on which keys are present: `index_url` means probe,
`manufacturer` alone means the failure-monitor reonboard path. The reonboard path
stays unbuilt in this slice and must **raise rather than report success**, the
same discipline `app/handlers/noop.py` already applies — a half-built handler that
silently no-ops is worse than the placeholder it replaced.

Dedupe key: `playbook.reonboard:{slug}:{normalize_url(index_url)}`. Per invariant
8 this scopes to active jobs only, so re-probing the same URL after a result is
in is allowed — which is exactly what an author adjusting a pattern does.

### 3.2 The side table

Payloads are immutable after enqueue (invariant 9), so the result cannot be
written back into the job. It goes in a side table, the same shape `batch_ref`
already uses for Batch API ids.

```sql
CREATE TABLE playbook_probe (
  id            bigserial PRIMARY KEY,
  slug          text NOT NULL,
  index_url     text NOT NULL,
  requested_by  text,
  via_job       bigint,          -- soft ref, per invariant 10
  status        text NOT NULL,   -- 'pending' | 'done' | 'refused' | 'failed'
  robots_verdict text,           -- 'allow' | 'disallow' | 'unreachable'
  tier          text,            -- 'static' | 'rendered'  (which parse produced links)
  counts        jsonb,           -- the §4.6 breakdown, verbatim
  sample        jsonb,           -- first N resolved links + their classification
  error         text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  finished_at   timestamptz
);
```

`counts` carries the crawl design's §4.6 set unchanged — `anchors_seen`,
`links_matched`, `links_capped`, `off_host`, `pages_fetched`, `robots_delay_ms`.
Storing it as jsonb rather than columns keeps this table from needing a migration
every time the harvester learns to count one more thing.

**Retention: probes are debris, not registry.** A probe result is a measurement
of someone else's website at a moment, not evidence, and nothing downstream reads
it. Delete rows older than 30 days on the existing scheduler sweep. This is
deliberately NOT the 10-year append-only rule, which governs documents.

### 3.3 What the operator sees

Per the approved mockup, in this order:

1. **The verdict line, before any number** — robots allow/disallow, whether a
   browser was needed, the politeness interval that will apply. A refusal is a
   result, not an error, and it renders as one.
2. **The counts**, with the classified breakdown leading and **the unclassified
   count shown, never hidden** (`701 of 1.329` for NSK). This is the never-silent
   rule reaching the screen: a thin harvest must not read as a complete one.
3. **A sample of ~20 resolved links** with what each reads as, including at least
   one that reads as *not* a compliance document, so the operator can see the
   pattern is over-broad rather than infer it.
4. **Two actions** — adjust the pattern (re-probe), or save the recipe.

### 3.4 Saving

Save goes through `save_playbook_body` (defined `web/registry.py:687`, called from the save route at `:2035`) unchanged:
optimistic lock on `rev`, revision recorded, revert available, `validate()` run
before write. The wizard composes a `crawl` block and folds it into the existing
body exactly as `tier_a_from_form`/`tier_b_from_form` already do; it does not get
its own write path. **A probe never writes a playbook by itself.**

## 4. The edges

### 4.1 A probe is a fetch, so it is bound by every fetch rule
Robots check first (`app/robots.py`, live and RFC 9309), refuse rather than fetch.
Domain lease acquired exactly as FETCH does (`app/handlers/fetch.py:131`), and
`queue.defer` if held — a probe must not jump the politeness queue because a human
is watching. The authoring guard (`playbooks/robots_refused.txt`,
`app/playbooks.py:922`) applies to `index_url` too: a refused host cannot be
probed, not merely cannot be saved.

### 4.2 The probe must not archive or extract anything
It fetches ONE page and parses it in memory. No `store.put`, no ledger row, no
`extract.doc`. This is the reason the probe is not a `fetch.url` with a flag —
that handler's ledger write would make the index URL look already-fetched to the
real crawl later, silently suppressing it (invariant 6).

### 4.3 Classification is a guess and must be labelled as one
`doc_type_from` maps a casefolded filename substring to a doc type. Filenames lie.
The screen says "reads as", not "is", and the unclassified bucket is visible. No
probe output is ever evidence, and nothing it produces reaches VALIDATE or GATE.

### 4.4 The 30s cost of the browser rung
Escalation to `Renderer.render()` triggers on **zero anchors**, not zero matches
(crawl design §4.2, ruled 2026-08-24). A probe that renders takes ~30s, so the UI
must show a real pending state rather than appearing hung, and `tier` is recorded
so "this portal needs a browser" becomes measured rather than authored.

### 4.5 Grants
`playbook_probe` needs INSERT/UPDATE for the worker role and SELECT for
`dentalia_api`. It is not `document`/`item_document`/`evidence`, so invariant 1 is
untouched — but the grant must be written explicitly rather than inherited, and
`docs/dev/changing-things.md` gets the row.

## 5. What this deliberately does not do

- **No new job type.** `playbook.reonboard` exists and this is what it was for.
- **No front door.** Creating a playbook that is not yet in the database is out of
  scope for this slice and is the larger half of S2.2 — see §7.
- **No suggestion engine.** Proposing candidate library URLs from a web search is
  step 3 of the mockup and is not in the MVP. The operator pastes a URL.
- **No authenticated portals.** Euronda's library is registration-gated; a probe
  behind a login is a manual task, per the crawl design §5.
- **No writes to the registry, ever.** A probe is a look, not a change.

## 6. Suggested sequence

Depends on crawl slices 1 (`crawl` key) and 2 (`app/crawl.py`), both in flight.

1. Migration: `playbook_probe` + grants. Inert.
2. The handler: `playbook.reonboard` probe branch — robots, lease, fetch, harvest,
   write the row. Reonboard branch still raises.
3. The route + HTMX partial: enqueue, poll, render. Wired to the existing
   `/playbooks/{slug}`.
4. "Save recipe" — compose the `crawl` block, hand it to the existing save path.
5. Probe one real portal end to end. NSK is the subject: measured 2026-08-27 at
   1.329 PDF hrefs from a plain GET, robots ALLOW, two downloads verified.

## 7. Open questions — these need your ruling

1. **Does the probe respect `budget`?** It costs no AI spend and one HTTP request,
   so I propose **no budget gate** — but it is a network call a UI button triggers,
   and a rate limit on the route may be wanted instead. Proposed: reuse the
   existing web access-class machinery rather than inventing a limiter.
2. **Who may probe?** The editor is already behind HTTP Basic (Caddy, gap G3 v0)
   with no per-user roles. Proposed: anyone who can reach `/playbooks` can probe,
   and `requested_by` records who did. Revisit when the front door lands, because
   that is when a non-engineer gets the button.
3. ~~**Does a refused robots verdict get recorded as a data anomaly?**~~
   **RULED 2026-09-03, Denis: record only, never auto-append.** The probe row
   records the refusal and `playbooks/robots_refused.txt` stays hand-maintained
   and authoritative. A robots.txt read on one day must not silently change what
   the pipeline is permitted to do forever — a site can lift a block, and a
   transient bad file would otherwise become a permanent refusal nobody
   remembers deciding. The refused list stays a human decision record.
4. **The front door — separate quote?** §5 excludes it. It is the half that
   reaches the 346 manufacturers, needs guardrails and non-technical copy, and
   serves a different user (Dentalia staff, not the developer). Recorded here as
   the seam; the commercial call is Denis's.
