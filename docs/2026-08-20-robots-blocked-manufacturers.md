# Manufacturers we are not permitted to crawl — measured 2026-08-20

Four of the eighteen manufacturers swept on 2026-08-20 publish their compliance
documents behind a written refusal in `robots.txt`. **None of them is a
rendering problem, so none is solved by adding a browser tier to the pipeline.**
They are permission problems, and the pipeline's answer to a permission problem
is to not fetch.

**Ruling, Denis, 2026-08-20:** these four are reachable **by a human, or by an
independent Playwright session run deliberately outside the pipeline** — never
by the automated fetcher.

How that ruling is encoded, in the playbooks themselves: every portal URL for
these four is authored as `doc_sources[kind:"portal"]`, never `"direct"`.
DISCOVER only ever uses a `portal` source to **prefill a manual task**
(`app/handlers/discover.py:245-258`); it never enqueues `fetch.url` for one.
So the URL is recorded, a human can click it, and the fetcher structurally
cannot reach it. `kind:"direct"` is the key that would make it a fetch target
(spec item 3, unbuilt), and it is deliberately absent from all four files.

Combined footprint: **822 catalogue items.**

| Manufacturer | Items | The refusal, verbatim | Playbook |
|---|---|---|---|
| KAVO DENTAL | 331 | `kavo.widen.net`: `Disallow: /` for every agent except Twitterbot | `kavo.json` |
| AMANN GIRRBACH AG | 196 | `media.amanngirrbach.com`: `User-agent: *` / `Disallow: /` | `amann-girrbach.json` |
| PRITIDENTA | 193 | `pritidenta.com`: `Disallow: /*.pdf` (also .xlsx/.doc/.docx) | `pritidenta.json` |
| COLTENE | 102 | `coltene.com` and `media.coltene.com`: `User-agent: ClaudeBot` / `Disallow: /`, **and** `User-agent: CloudflareBrowserRenderingCrawler` / `Disallow: /` | `coltene.json` |

## What is distinctive about each

**KaVo — the block lands on FETCH, not DISCOVER.** Discovery on `kavo.com` is
permitted and works: a browser renders the grid, and `?document_type=514`
deep-links on a cold load, so the type axis needs no clicking. The PDFs live on
a separate Widen DAM host that disallows everything. Splitting DISCOVER from
FETCH does not rescue this, because the stage that would otherwise have been
trivial is the one that is barred.

Two premises that turned out wrong and should not be repeated: the
`/api/download_manager/` endpoint named in `kavo.com/robots.txt` is **not** what
backs the document grid (the page never calls it), and the
`f[0]=field_resource_document_type:NNN` facet URL shape is a stale search-index
artifact from a previous site version — the live parameter is `document_type`.

And the yield would not have justified the work anyway: **"Conformity
Statements" contains exactly one document site-wide**, a VAH hygiene
certificate, not a declaration. The 32 "Certificates" are all corporate/QMS.
There are zero per-article MDR declarations on the site. The 7,271 IFUs are the
only genuine per-product corpus.

**Amann Girrbach — the documents exist, behind the disallow.** Their own printed
IFUs direct users to Services > Downloads > Ergänzende Unterlagen for the
Konformitätserklärung. The Downloads section still exists; it moved to a Bynder
DAM and is reachable only from the live footer. A previous pass failed because
the site was rebuilt on WordPress with `/{lang}-{country}/` paths, every old
Downloads URL died, and `archive.amanngirrbach.com` was retired — while search
engines still rank the dead URLs. Navigating from the live homepage is what
found it. Their `sitemap.xml` lists 38 blog and training URLs and omits every
product page, so the migration is unfinished and the sitemap is useless for
discovery.

**Pritidenta — the cruellest of the four.** It is the one manufacturer in the
entire sweep whose filenames carry REF numbers (`REF078`, `REF_062`,
`REF%20377`, `REF_A20`, plus `Rev_002` revision tokens — three separator forms,
so any pattern must tolerate glued, underscored and URL-encoded spaces). The
declaration filename even carries its own validity date. The HTML listing is
static and freely readable; `Disallow: /*.pdf` blocks every document behind it.

They offer a free paper-IFU request with a 7-day turnaround, which makes
`email.request` a real route rather than a consolation.

Worth recording: Pritidenta's legal entity (`pritidenta GmbH`) was **predicted
offline before any web fetch**, from GC's own spreadsheet in our SFTP corpus,
which names it as legal manufacturer of 76 GC articles. Their legal notice
confirms it. That is the corpus-mining method producing a correct identity for a
manufacturer that has no corpus of its own.

**Coltene — the only operator to refuse us by name.** Their Cloudflare-managed
`robots.txt` names `ClaudeBot` and, separately, `CloudflareBrowserRenderingCrawler`.
So the operator has explicitly refused headless-browser rendering as well as
plain crawling; the HTTP 403 seen on an earlier pass was that policy being
enforced, not a misconfiguration. Rendering it would be circumventing a stated
refusal, and the sweep stopped rather than do so.

Their content signal does carry `use=reference`, which suggests they are not
hostile to being referenced and that an email asking permission may succeed
where crawling is barred.

Their media library reportedly takes a per-article parameter
(`index?q={article_number}`), which would be a high-value per-item lookup. That
shape is **untested** and, if it matters, must come from asking Coltene rather
than from assuming it works.

## What to do with them

1. **`email.request` for all four.** Three of the four have a natural opening:
   Pritidenta already runs a document-request service, Coltene signals
   `use=reference`, and KaVo's DAM restriction is worth raising in the same
   message as a DoC request since it bars fetching regardless of discovery.
2. **A deliberate, human-run Playwright session** where a person wants the
   documents now, per the ruling above. That is a person using a browser, not
   the pipeline crawling; it stays outside `app/` and leaves no automated path.
3. **Never author `kind:"direct"` for these hosts.** That single key is what
   would turn a recorded URL into a fetch target.

## The wider finding these four sit inside

They are not an aberration. Across the eighteen manufacturers swept, **most do
not publish per-article declarations of conformity at all** — SUN, MELAG,
Ustomed, Kuraray, Polident, Acteon and KaVo publish IFUs and company-level
certificates (ISO 13485, MDSAP, MDR CE) and no DoC. MELAG's own documentation
states the Konformitätserklärung is handed over with the device at installation.

So crawling reliably yields IFUs and QMS certificates, and **declarations
largely have to be asked for**. `email.request` is not the fallback for awkward
manufacturers; for this document type it is the main road.
