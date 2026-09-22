# A crawl recipe on the `playbook` DISCOVER rung — design

Design proposal, 2026-08-21. **Nothing here is implemented.** Decision document
for approval; §7 lists what needs your ruling before a plan can be written.

Implements design item 11 of
`docs/superpowers/specs/2026-08-18-playbooks-across-stages-design.md` §4b(C),
which named the answer and deferred the edges to this document.

Verified against the code on 2026-08-21. Every claim below carries
a file:line; where this document contradicts an older doc, the code wins.

---

## 1. The problem, stated precisely

`fetch.url` is one URL, one document. A manufacturer's download centre is a
**listing page**: one URL yielding N document links, often paginated, often
behind a filter. There is nowhere in the model to say that, so today a human
clicks it — `doc_sources[kind:"portal"]` is read at `app/handlers/discover.py:252`
for one purpose, prefilling a manual task.

The cost is not theoretical. 25 of 33 playbooks carry `doc_sources`; 28 of the 36
authored entries are `kind:"portal"`. Every one of those is a page somebody has
already found, recorded, and cannot use.

## 2. What is true today (verified)

**The rung exists and is empty.** `app/handlers/discover.py:398`:

```python
        if source in ("playbook", "vendor"):   # Phase 2 / folded into search (G6)
            _log(conn, group_id, source, "skipped", {"deferred": True})
            continue
```

DISCOVER's PRD Emits row already lists `fetch.url`, and `_emit_fetch`
(`app/handlers/discover.py:215-230`) already builds the payload
`{url, domain, group_id, source_rank}` with dedupe key
`fetch:{normalize_url(url)}`. **The legal home exists, is reachable, and is
empty.** No PRD change, no new job type, no migration — `discovery_log.source`
is free text (`migrations/003_discovery_fetch.sql:10-17`).

**DISCOVER already makes network calls**, so a crawl is not a new category of
behaviour for the stage: the `search` rung calls a SearchAdapter
(`app/handlers/discover.py:157-181`).

**Nothing in the repo parses HTML.** Zero hits for BeautifulSoup, lxml,
selectolax, html5lib, `html.parser` or `urljoin` across `app/`, `web/`, `tools/`
and `tests/`. The worker image installs `.[extract,resolve,ingest,discover,fetch]`
(`pyproject.toml:41-42`); the only HTML-capable things in it are the stdlib and
Chromium via Playwright, whose fetcher returns `resp.body()` bytes and exposes no
DOM API (`app/adapters/fetcher.py:84-115`).

**Politeness is one lease row per exact host**, `domain_lease`
(`migrations/001_queue.sql:61-66`), acquired only by FETCH
(`app/handlers/fetch.py:131-133`) via `queue.try_domain_lease`
(`app/queue.py:247-265`). `politeness_ms` is stored per row but **overwritten on
every acquire** from `cfg.fetch.politeness_ms` (`EXCLUDED.politeness_ms`), so a
hand-written per-domain value does not survive.

**`kind` is the robots enforcement mechanism, and it is a ruling, not a
convention.** `docs/2026-08-20-robots-blocked-manufacturers.md` records four
manufacturers (KAVO, AMANN GIRRBACH, PRITIDENTA, COLTENE — 822 catalogue items)
whose document hosts publish a written refusal, and your ruling of 2026-08-20:
reachable "by a human, or by an independent Playwright session run deliberately
outside the pipeline — never by the automated fetcher." Its directive 3 is
explicit: *"Never author `kind:"direct"` for these hosts. That single key is what
would turn a recorded URL into a fetch target."*

**No fetch path checks robots.txt.** Zero hits for "robots" in any `.py`. The
refusal is enforced entirely by what a human writes in a playbook.

**FETCH has no content-type gate.** `app/handlers/fetch.py:156` checks only
`status != 200 or body is None`; any 200 body is hashed, archived and emitted as
`extract.doc`. EXTRACT then opens it as a PDF and, on failure, returns
`{"counts": {"unreadable_pdf": 1}}` (`app/handlers/extract.py:281-300`) — the job
**finishes `done`**, nothing dead-letters, nothing is retried. An HTML page routed
through `fetch.url` is archived silently and dead-ends.

## 3. The proposal

A `crawl` block on the playbook, consumed by the `playbook` rung.

> **Corrected 2026-09-03, Denis: `crawl` is a LIST of recipes, not one.** The
> singular shape below was sketched before anyone counted: **12 of the 38
> delivered playbooks author more than one `portal`**, and Edenta needs both of
> its libraries — instructions for use on one page, EC/ISO certificates on
> another, a different `doc_type` each. Changed before any recipe was authored
> against the singular shape, so nothing had to be migrated. Each recipe gets
> its own robots check and its own domain lease, since each may name a
> different host.

```jsonc
"crawl": {
  "index_url": "https://www.voco.dental/en/service/download/instructions-for-use.aspx",
  "link_pattern": "\\.pdf$",              // matched against the RESOLVED absolute URL
  "same_host_only": true,                 // default true; see §4.3
  "doc_type_from": {"ifu": "IFU", "konformit": "DoC"},   // casefolded substring -> type
  "pagination": {"param": "page", "max_pages": 20},
  "max_links": 200
}
```

The rung, in order:

1. Resolve the playbook (already done at `app/handlers/discover.py:322`).
2. If no `crawl` block → fall through to the authored-`direct` behaviour, then
   `miss`. The rung never errors for an unauthored manufacturer.

   > **Corrected 2026-09-03, Denis.** This step was implemented literally, making
   > `crawl` and `direct` mutually exclusive, and that is wrong: **a crawl block
   > adds reach, it never removes any.** The exclusive reading set a silent trap
   > — CARL MARTIN, GC, NSK, RENFERT and ULTRADENT all author `direct` PDFs a
   > human has already verified, and adding a crawl block to any of them would
   > have dropped those declarations out of discovery with nothing in any log to
   > say so. The rung now emits the authored `direct` sources FIRST (a
   > hand-verified document outranks a harvest) and adds the crawl's links to
   > them. A crawl `miss` no longer cancels a `direct` hit.
3. **Robots check** (§4.1) — refuse and log rather than fetch.
4. **Acquire the domain lease** for the index host, exactly as FETCH does, and
   `queue.defer` the DISCOVER job if it is held. This is new: DISCOVER has never
   taken a lease, and a crawl is the first thing that makes it a polite-rate HTTP
   client rather than an API caller.
5. Fetch the index page through the existing `Fetcher` adapter (invariant 11
   holds — the rung must not know which transport is live).
6. Extract links, resolve each against the index URL, filter by `link_pattern`
   and `same_host_only`, dedupe, cap at `max_links`.
7. Emit one `fetch.url` per surviving link through the existing `_emit_fetch`.
8. Log one `discovery_log` row with the full count breakdown (§4.6) and return
   `_result(..., terminal_source="playbook", outcome="fetch", emitted_fetch=n)`.

Everything after step 7 is machinery that already exists and is already tested.

## 4. The edges, and what this document proposes for each

### 4.1 robots.txt — the one that cannot be got wrong

Four manufacturers covering 822 items publish a written refusal, and you have
already ruled the pipeline must not fetch them. Today that ruling is enforced by
a human choosing `kind:"portal"`. A crawl recipe introduces a **second** way to
name a fetch target, so it must inherit the same discipline and preferably a
harder one.

**Proposal, three layers:**

1. **Authoring-time refusal.** `validate()` rejects a `crawl.index_url` whose
   host appears in a `robots_refused` list kept next to the playbooks. The four
   known hosts go in it on day one. An operator cannot author the mistake.
2. **Runtime check.** The rung fetches and parses `robots.txt` for the index host
   before the index page, honours `Disallow` for our user-agent
   (`DentaliaComplianceBot/1.0`, `app/adapters/fetcher.py:27`) and for `*`, and
   refuses with `_log(conn, group_id, "playbook", "skipped",
   {"reason": "robots-disallow", "host": ...})`. Cached in a small table keyed on
   host with a TTL, so it is one request per host per window, not per crawl.
3. **`Crawl-delay` is honoured** where robots states one. Seven playbooks already
   record a crawl-delay in prose (`renfert` 10s, `kuraray-dental` 2s, and five
   more); nothing reads them today.

   Layer 3 needs a mechanism change, found 2026-08-24: the politeness timer is
   `domain_lease.politeness_ms`, and `queue.try_domain_lease`
   (`app/queue.py:247-265`) rewrites it from the caller's value on **every**
   acquire (`politeness_ms = EXCLUDED.politeness_ms`). FETCH always passes
   `cfg.fetch.politeness_ms` (`app/handlers/fetch.py:131`), so a 10s delay a
   crawl writes for `renfert` is overwritten with the global 2s by the next
   `fetch.url` on that host — robots obeyed for exactly one request.

   > **Ruled 2026-08-24, Denis: a new `min_politeness_ms` column on
   > `domain_lease`,** written only by the robots reader. The lease computes
   > `leased_until` from `GREATEST(EXCLUDED.politeness_ms,
   > domain_lease.min_politeness_ms)`; `politeness_ms` keeps taking the caller's
   > config value unchanged. Config is the floor, robots raises it, the robots
   > TTL refresh lowers it again when a host drops its `Crawl-delay`. Chosen
   > over a one-line `GREATEST(existing, incoming)` on `politeness_ms` (a
   > one-way ratchet with no provenance: a bad parse pins a host slow forever
   > and nothing records why) and over having each caller look the delay up
   > (correct only while every future lease taker remembers — the same
   > enforced-by-what-a-human-types boundary the 2026-08-21 robots ruling
   > rejected). Structural, and it matches the per-domain fetch policy already
   > on that table (`needs_playwright`, `migrations/001_queue.sql:61-66`).
   > It is a change to the shared FETCH politeness path, not a crawl-local one.

Layer 2 is the one that matters, because it also protects hosts nobody has
audited yet. Layers 1 and 3 are cheap and should ship with it.

> **Ruled 2026-08-21, Denis: yes — runtime check AND authoring guard.** All
> three layers ship. The argument that decided it: the four refused hosts were
> found by a deliberate sweep, and the next one will not be, so a boundary
> enforced only by what a human remembers to type is not a boundary. The
> pipeline therefore makes one request no playbook authored — `robots.txt` for a
> host it is about to crawl — and that is the *narrowest* possible exception,
> since it exists solely to find out whether the crawl is permitted.

### 4.2 HTML parsing — what does the link extraction

No dependency exists. Three options, in the order I would pick them:

| Option | Cost | Risk |
|---|---|---|
| **stdlib `html.parser`** | none — already in the image | Tolerant enough for `<a href>`; will not run JS |
| `selectolax` / `lxml` | one dependency, C extension | Faster and more correct on malformed markup; a new wheel in the worker image |
| Playwright DOM | already in the image | Renders JS-built listings; heavyweight, and `PlaywrightFetcher` exposes no DOM API today, so it needs a new method |

**Proposed: stdlib `html.parser` for v1**, with the Playwright path as the
documented escalation for a listing that renders client-side — the same
two-tier shape FETCH already has, and consistent with `pyproject.toml:11` ("deps
are added as each stage lands, NOT preemptively").

> **Ruled 2026-08-21, Denis: accepted, and explicitly as a ladder** — "step by
> step improving the chance of a viable parse". So the tier is a property of the
> attempt, not of the manufacturer: stdlib parse first, and only a page that
> yields no links escalates. Each rung records which tier produced the links, so
> "this portal needs the browser" becomes measured rather than authored, the way
> `domain_lease.needs_playwright` already is for FETCH.

**Ruled 2026-08-24, Denis: the browser rung ships IN v1**, after measuring what
it costs. `PlaywrightFetcher.get` returns `resp.body()` (`app/adapters/fetcher.py:107`)
-- the initial HTTP body, not the rendered DOM -- so the escalation needs one new
method, `render(url)`, returning `page.content()`. It goes behind a one-method
`Renderer` Protocol rather than widening `Fetcher`, so invariant 11 holds: the
crawl constructs `PlaywrightFetcher()` explicitly on escalation, the way
`app/handlers/fetch.py:125-126` already does. Chromium is already in the worker
image (`Dockerfile` is `FROM playwright/python`), so no image change. Measured
cost: ~30 lines of production code and ~40 of test across one file the slice did
not already touch.

**Escalation trigger: zero `<a href>` on the page, not zero MATCHING links.**
Zero matches means the authored `link_pattern` is probably wrong, and rendering
such a page spends a 30s `networkidle` wait to still find nothing; zero anchors
is the actual signature of a listing built client-side. A page with anchors but
no matches is a `miss` with its counts, which is the §4.6 output that tells the
author their pattern is wrong.

Note what this cannot do: a portal behind a **POST/form filter** is out of scope
for v1 and should be authored `kind:"portal"` so a human handles it. Say so in
`playbooks/README.md` rather than letting an author discover it.

This supersedes §5's "No JS rendering in v1" bullet, which predates the
measurement.

### 4.3 An over-broad `link_pattern`

`"\\.pdf$"` against a page linking a whole site's PDFs harvests the site.
Defences, all cheap, all proposed together:

- `max_links` is **required**, not optional, with a low default (200) and a hard
  ceiling `validate()` enforces.
- `same_host_only: true` by default, **ruled 2026-08-21, Denis**. A CDN-hosted
  PDF set (Edenta serves from `/zoolu-website/media/document/...` on its own
  host, but Acteon and SUN use asset hosts) widens by naming those hosts in an
  explicit `allow_hosts` list, never by a blanket "follow anything": `max_links`
  is a count, not a boundary, and a `\.pdf$` pattern on a page that links
  offsite would otherwise harvest someone else's documents into our archive.
- The cap is a **counted, reported truncation**, never a silent slice (§4.6).

### 4.4 Pagination

`{"param": "page", "max_pages": 20}` appends `?page=N` and stops on the first of:
`max_pages`, a page yielding zero new links, or a page whose link set is
identical to the previous page's (the common infinite-pagination shape — a site
that clamps out-of-range pages to the last one). All three conditions are needed;
the second alone loops forever on a site that repeats its last page.

### 4.5 The 6 index pages already authored as `direct` — a live hazard

The design doc's item 3 (emit `kind:"direct"` as `fetch.url`) was written when
every authored source was a portal. **That is no longer true.** There are now 8
`direct` entries, and only 2 of them are PDFs:

| Playbook | doc_type | Shape |
|---|---|---|
| carl-martin | DoC | PDF — a real document |
| renfert | DoC | PDF — a real document |
| edenta | IFU | **HTML listing page** |
| polident | IFU, EC | **HTML listing pages** (2) |
| sun | IFU, EC | **HTML listing pages** (2) |
| ustomed | IFU | **HTML listing page** |

Six of the eight are exactly the thing a crawl recipe is for, authored under the
key that means "fetch this directly". Per §2, fetching them archives HTML that
dead-ends at EXTRACT as `unreadable_pdf`, counted but never surfaced as a
failure.

**Proposal:** re-author those six as `kind:"portal"` (which is what they are by
the design's own definition) at the moment item 3 ships, and give each a `crawl`
block when this design lands. Their notes already describe them as listings —
Edenta's records "11 IFU/safety documents, hrefs in the served HTML", including
the fact that its URLs carry no `.pdf` extension, which is precisely the
`link_pattern` trap in §4.3.

### 4.6 Never-silent counting

Per the never-silent rule, one `discovery_log` detail carrying: `links_seen`,
`links_matched`, `links_capped` (how many `max_links` discarded), `pages_fetched`,
`emitted`, `deduped` (how many `_emit_fetch` returned `None` for), and
`robots_delay_ms` if one was honoured. A crawl that harvested 3 links from a page
with 40 documents must be visible as such, not as a quiet success.

### 4.7 Group attribution

A crawled document answers no specific group's question. `_emit_fetch` takes
`facts.group_id`, so a crawl launched from a group's DISCOVER job can carry it —
but the honest value for a document found by sweeping a manufacturer's whole
catalogue is `group_id: null`, the shape backfill already produces
(`app/handlers/backfill.py:419`) and which EXTRACT (`app/handlers/extract.py:262`)
and VALIDATE (`app/handlers/validate.py:767-786`) both handle with a
manufacturer-scoped path. **Ruled 2026-08-21, Denis: carry the requesting `group_id`.** The crawl is
running because that group asked; the other documents it finds are a by-product
VALIDATE attributes on content. `null` stays reserved for a scheduled
whole-catalogue sweep, which genuinely has no requester — and if that is ever
built, note that `validate:{hash}:{rev}:None` is a single shared dedupe slot for
every group-less document, which is fine for backfill's one-shot scan and would
need thought for a recurring one.

### 4.8 Politeness starvation — a defect a crawl amplifies

This is the edge the earlier document named and the one with teeth. Measured
from the code: 500 `fetch.url` jobs for one host serialize at one per
`politeness_ms` (2s default) — **≈16.7 minutes** — and while they wait, each
deferred job still claims. `run_once` returns `True` for a deferred job
(`app/workers/runner.py:36-73`), and `run_forever` only sleeps when nothing
worked (`app/workers/runner.py:118-119`), so a lease-blocked backlog **busy-spins
on claim/defer at full database speed with no backoff**. `queue.defer` refunds
the attempt (`app/queue.py:236-244`), so nothing ever dead-letters; it just
burns.

That is true today with no crawl — a `max_links: 200` crawl makes it routine
rather than hypothetical.

**Proposal: fix this before the crawl ships, as its own slice.** Either
(a) `run_once` distinguishes "deferred" from "worked" so the poll interval
applies, or (b) `claim` skips jobs whose domain lease is held. (a) is three lines
and fixes the symptom; (b) is the real fix and needs a domain column on `job`.
This is a prerequisite, not part of the crawl.

## 5. What this deliberately does not do

- **No link harvest inside FETCH.** Rejected in the earlier design at §4b(B):
  FETCH's PRD Emits row permits `extract.doc`/`validate.doc` only, and its entire
  design property is that it is dumb I/O under a ledger.
- **No new job type.** The rung emits `fetch.url`, which is already its right.
- **No authenticated portals.** `auth_ref` belongs to `fetch_policy` (design item
  4), not here, and a crawl behind a login is a manual task.
- ~~**No JS rendering in v1** (§4.2).~~ **Reversed 2026-08-24** — the browser
  rung ships in v1; the escalation cost was measured at ~30 lines and one extra
  file. See §4.2.

## 6. Suggested sequence

1. The starvation fix (§4.8) — independent, small, and a prerequisite.
2. robots.txt reading + cache + the `robots_refused` authoring guard (§4.1).
3. The `crawl` key: parse, validate, document. Inert.
4. The rung: index fetch, stdlib link extraction, cap, emit, counted log.
5. Author one recipe — Edenta is the best first subject: its note already
   documents the link shape, the extensionless URLs and an empty robots.txt.
6. Pagination (§4.4) only when a real portal needs it.

## 7. Open questions — these need your ruling

**All five are now ruled (2026-08-21). Kept here as the decision record.**

1. ~~**May the pipeline fetch `robots.txt`?**~~ **Yes — runtime check plus the
   authoring guard**, all three layers of §4.1. The one unauthored request the
   pipeline makes, and it exists only to ask permission.
2. ~~**`same_host_only` or never leave the host?**~~ **`same_host_only: true` by
   default, widened only by an explicit `allow_hosts`.** (§4.3)
3. ~~**Do the six mis-authored `direct` entries become `portal` now?**~~
   **Done 2026-08-21**, as part of shipping the rung, because the
   rung made them live fetch targets the moment it merged. Each carries the
   reason in its note. Flagging rather than asking was the wrong order and is
   recorded as such; the change is one `sed` from reversible if you disagree.
   (§4.5)
4. ~~**`group_id` or `null`?**~~ **The requesting `group_id`.** (§4.7)
5. ~~**Is the starvation fix in scope?**~~ **Fixed first, as its own slice**
   (§4.8) — `run_once` now reports a self-defer as no-progress so `run_forever`
   sleeps on it.

A sixth decision arrived with these, and it is not about the crawl: Medentika's
documents have moved to `straumann.com`, which its `domains` does not cover, so
DISCOVER's `site:` restriction and post-filter both exclude their real home.
Ruled 2026-08-21: **support path-scoped domains** (`straumann.com/medentika`)
rather than widening to the whole host, which would hand Medentika searches the
entire Straumann site and let two playbooks claim one domain. That is a change
to `_normalize_domain` in `app/playbooks.py` (hostname-only today, by design)
and to the query builder and post-filter in `app/handlers/discover.py` — its own
slice, tracked separately from this design.
