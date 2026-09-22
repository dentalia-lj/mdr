# Playbooks

> **HISTORICAL AS OF 2026-08-27. The database is the playbook store; this
> directory is the authoring record.**
>
> `manufacturer.body` plus the identity tables (migrations 046/047) are what
> the pipeline reads. Editing a file here changes **nothing** until
> `python -m app.cli manufacturers seed --apply` imports it, and the import
> **refuses** to overwrite a body or a canonical name edited since the last
> run — a UI edit is a decision, and an import must not silently revert one.
>
> These files stay in git, not deleted, because 33 files' worth of authoring
> rationale lives in their notes and nothing else carries it. New
> manufacturers are onboarded in the UI: `/manufacturers?no_playbook=1` lists
> who needs one, "Start a playbook" creates it, and `/playbooks/{slug}` edits
> it with the Tier A/B controls and their guards. `robots_refused.txt` moved
> to the `refused_host` table in the same slice (migration 048), because the
> web process no longer has this directory mounted and a refusal that fails
> open when a mount goes away is worse than no refusal.
>
> What still reads files: the seed (that is the import), the worker image's
> copy of this directory (so the seed runs in a container), and any test that
> passes an explicit directory. An explicit directory always wins.

One file per manufacturer. `{slug}.json`, slug is a short stable brand handle.

Authored by hand; `dentalia playbooks validate` checks the whole directory.

## Fields

| Field | Required | Meaning |
|---|---|---|
| `manufacturer` | yes | The LEGAL manufacturer, as it appears on the DoC. Not the brand. |
| `aliases` | no | Brand and product-line names. PANTHER is an alias of SUN Oberflaechentechnik. |
| `bc_codes` | no | `[{"catalogue": "LJ", "code": "001"}]`. CONFIRMED or LIKELY tiers only. Seeds `manufacturer_alias`. |
| `domains` | no | Official domains, restricting DISCOVER's `site:` query. Hostname (`voco.dental`), or hostname plus a **path scope** (`straumann.com/medentika`) when the documents live under a path on a host someone else owns — Medentika's moved onto Straumann's site, and scoping by host alone would hand its searches the whole Straumann site and let two playbooks claim one host, which `validate()` does not detect. Verified against the live Brave API 2026-08-21: a path-scoped `site:` token is honoured. A bare `/` is not a path scope. Authored either bare or as a full URL; a full URL keeps its `www.` and a bare hostname does not, so pick one spelling per host. |
| `doc_sources` | no | `[{"doc_type", "kind", "url", "note"}]`. `kind` is `portal` or `direct`, and the difference is now load-bearing: **`direct` is a fetch target** -- the `playbook` DISCOVER rung enqueues `fetch.url` for every one -- while `portal` only ever prefills a manual task for a human. Author `direct` ONLY for a URL that returns the document itself. A listing page authored `direct` is archived as an unreadable PDF and dead-ends at EXTRACT silently, and for the four robots-refused manufacturers (`docs/2026-08-20-robots-blocked-manufacturers.md`) `portal` is the entire mechanism keeping the fetcher out -- never author `direct` for those hosts. |
| `match`, `ref_strategy`, `ref_strategy_config` | no | T0 parse template, read by `app/extract/t0_layout.py`. |
| `ref_normalize` | no | REF *comparison* rule for VALIDATE's C1 gate, read by `app/handlers/validate.py`. See below — do not confuse with `ref_strategy`. |
| `rev` | no | Hand-bumped non-negative integer, default `0` ("unversioned" — every file authored before this key existed). Recorded on `extraction_attempt.playbook_rev` whenever this playbook steered a read, so an extraction stays defensible after the file changes later. Bump it whenever you change `date_labels`, `cert_number_pattern`, `ref_pattern` or `extract_hints`. Must actually be an integer — a string (`"3"`) or a negative value fails to parse, and that failure drops the WHOLE playbook file silently (identity, `bc_codes`, everything), not just this key. Always run `dentalia playbooks validate` after an edit. |
| `date_labels` | no | `{"from": [...], "to": [...]}`. Extra date-label spellings for T0, MERGED with the global EN/DE/SL list. Read by `app/extract/t0_templates.py`. |
| `type_markers` | no | `{"DoC": [...], "EC": [...], "IFU": [...], "ISO": [...]}`. Extra doc-type phrases for T0, MERGED with the global marker list. Keys must be one of those four -- the types T0 can return -- and a key outside them fails to parse, dropping the whole file. Authored phrases are appended AFTER the globals, and `extract_type` picks the EARLIEST match in the document with list order as the tie-break, so a global phrase starting at the same offset still wins. Read by `app/extract/t0_templates.py`. |
| `source_priority` | no | DISCOVER ladder for this manufacturer, most-preferred rung first. Overrides the TOML `[source_priority]` map, which only serves manufacturers without this key. Rungs: `recency` (guard, never emits), `known_url`, `playbook` (authored `kind:"direct"` doc_sources), `eudamed`, `search`, `email` and `manual` (terminal), `vendor` (folded into `search` per G6, logs `skipped`). An unknown rung name fails the job loudly. Read by `app/handlers/discover.py`. |
| `cert_number_pattern` | no | Extra certificate-number regex (string) for T0, tried AFTER the two global label patterns — additive only, so it can add a read but never change one the global patterns already made. Must compile and have exactly one capture group, checked at load — but that group is allowed to be OPTIONAL within the pattern (e.g. a label that isn't always followed by a number). If it doesn't participate in a given match, T0 treats that as no match, not an error. Read by `app/extract/t0_templates.py`. |
| `ref_pattern` | no | Exact REF-code shape (a plain regex string, no capture group required) applied ON TOP of T0's generic REF filter (`_looks_like_ref`) as a narrowing `re.search` — a value the generic filter already rejected stays rejected; this never widens what qualifies. Distinct from `ref_strategy_config.field_pattern`, which selects a COLUMN for one parse strategy; this validates a CODE for all of them. Too strict and it doesn't just drop codes — it can make a real article table look empty and get skipped entirely, so measure before keeping one, the same as `extract_hints` below. Read by `app/extract/t0_templates.py` / `app/extract/t0_layout.py`. |
| `companion` | no | `{key, primary, annex}` regexes: which of this manufacturer's files are an ANNEX to another file's document. Read by BACKFILL. See below. |
| `extract_hints` | no | Free-text guidance appended to the T1/T2 prompt. The ONLY playbook value an LLM ever sees. See below. |
| `coverage_map` | no | `{sources, key_resolves, canonical}`. The manufacturer's own article -> document index. Read by the one-shot `komet-coverage` CLI, never on the live path. See below. |
| `exclude` | no | `{reason, filenames}`. Which files in this manufacturer's corpus folder are not documents at all. Read by BACKFILL, which drops them before archiving. See below. |
| `skip_backfill` | no | `{reason}`. This manufacturer's corpus dump is not fit to ingest at all; `backfill.scan` refuses it. See below. |
| `crawl` | no | A **list** of listing-page crawl recipes for the `playbook` DISCOVER rung, each `{index_url, link_pattern, same_host_only?, allow_hosts?, max_links?, doc_type_from?, pagination?, note?}`. A list because a manufacturer routinely publishes to more than one library — Edenta has instructions for use on one page and EC/ISO certificates on another. Live as of 2026-09-03; `edenta.json` is the only file authoring one. See below. |

Empty is honest. A guessed URL is worse than no URL.

## `robots_refused.txt` — hosts no playbook may name as a fetch target

Not a playbook field: a plain-text list beside the playbooks, one host per line,
`#` starting a comment. `playbooks.validate()` refuses any playbook that authors
a `kind:"direct"` source on a listed host or a subdomain of one, so
`dentalia playbooks validate|reconcile|sync` fails instead of the pipeline
fetching a host that refused it. Deliberately not a `.json` — `load_playbooks`
globs `*.json` in this directory and would read it as a playbook.

It carries Denis's ruling of 2026-08-20
(`docs/2026-08-20-robots-blocked-manufacturers.md`): five hosts across four
manufacturers and 822 catalogue items, reachable "by a human, or by an
independent Playwright session run deliberately outside the pipeline — never by
the automated fetcher."

**This is not the same boundary as `app/robots.py`.** That module reads a host's
live `robots.txt` and obeys it at runtime; this file records a decision that does
not depend on what the file says. COLTENE is why both exist: its `robots.txt`
disallows `ClaudeBot` and `CloudflareBrowserRenderingCrawler` by name and says
nothing about `DentaliaComplianceBot`, so a runtime check reads it as
**permitted**. Only the list carries the refusal.

Removing a line means asking the manufacturer and getting a yes. Record that in
the playbook's `note`, with a date, before editing the list.

## `ref_normalize` — REF normalization is not REF parsing

`ref_strategy` (above) picks a strategy for *finding* the REF list on the
page — it has nothing to do with matching. `ref_normalize` is the opposite
concern: how to fold a REF string for *comparing* it against catalogue
`mfr_ref` values. VALIDATE applies it to both sides of the C1 REF gate
(`app/handlers/validate.py::_ref_gate_for_group`), keyed off the group's own
`canonical_manufacturer` — it never mutates the stored `mfr_ref` or the
extracted `ref_list`, both of which remain the evidence.

Two strategies exist today:

```json
"ref_normalize": {"strategy": "strip-trailing-letters", "max_letters": 3}
```

`strip-trailing-letters`: a ref that is ALL digits followed by 1-3 letters
(e.g. `645986DC`) is compared as its digit prefix (`645986`). Authored for
Ivoclar only — measured over 174 Ivoclar corpus PDFs, manufacturer DoCs
enumerate every market/region variant of an article number (trailing letters
read as a market list: `AN`, `WW`, `CN`, `US`, ...) while BC records only the
base number Dentalia actually stocks; stripping the suffix took linkable
documents from 19/174 (exact match) to 54/174.

```json
"ref_normalize": {"strategy": "reorder-shank-figure-size"}
```

`reorder-shank-figure-size`: one ISO 6360 bur code written in two orders.
Dentalia's catalogue writes SHANK FIGURE SIZE separated by spaces
(`314 H1 006`); Komet prints FIGURE.SHANK.SIZE (`H1.314.006`, and `1.204.005`
where the figure carries no letters). Both fold to the dotted form with the size
zero-padded, so either side may be either spelling. Derived once from Komet's
`Where to find DoC.xlsx` (article `000085K3` = reference `H1.314.006`) and then
measured against the corpus itself: over the 186 Komet PDFs, catalogue codes
found in the documents go from **0 to 98** with the rule. The spreadsheet is a
working document and is deliberately not a runtime input (Denis's ruling,
2026-08-18) — only the rule it revealed is. A Dentalia packaging suffix
(`104 H219A 023-1`) is left unfolded on purpose: two of the seven such items
would otherwise share a key with an unsuffixed sibling and one document would
link both articles.

This must stay per-manufacturer, authored only where evidenced. A global
strip would corrupt manufacturers whose letters ARE load-bearing — e.g.
Komet's ISO bur codes (`104 H251EF 060`, space-containing; letters are part
of the identity, not a market suffix). No manufacturer without an authored
`ref_normalize` rule is affected: absence of the key is the identity
function, today's exact-match behaviour.

## `companion` — when one document arrives as two files

Komet issues a Declaration of Conformity and that declaration's product list as
two separate PDFs sharing a leading number:

```
532624_RA_810_DoC_EU_SIGNED.pdf      the declaration — 1 page, no article codes
532624_RA_812_Liste_DoC.pdf          the list — 116 article codes
```

Measured over the 186-PDF Komet corpus on 2026-08-18: **39 leading numbers,
every one carrying both, zero orphans.** 81 of the 102 declarations carry no
list of their own; the other 21 print one inline and keep it.

```json
"companion": {"key": "^(\\d+)_", "primary": "_RA_810_", "annex": "_RA_812_"}
```

- `key` — one capture group, matched against the FILENAME; its value is the
  pairing key. A `key` with no capture group is rejected at load: it would pair
  nothing and drop every annex without a word.
- `primary` / `annex` — which side of the pair a filename is on.

Two things follow, both in `app/handlers/backfill.py`:

1. **An annex never becomes a document.** It is archived and written to the
   fetch ledger like anything else, but no `extract.doc` is emitted for it.
   Left to itself a `_RA_812_Liste_` reads as a Declaration of Conformity in its
   own right — same manufacturer, group, regulation and dates as the declaration
   it belongs to — which is exactly the `(coverage subject, type, regulation)`
   triple invariant 5 supersedes on. Filing both leaves two DoCs racing to
   supersede each other over one product list.
2. **The primary carries `companion_archive_url`** on its `extract.doc` payload.
   T0 reads the list off the annex *only when the primary has none of its own*,
   and the resulting `ref_list` evidence carries **the annex's** `archive_url`,
   not the declaration's — invariant 2 asks where the value was read, and the
   declaration has no such page.

The mapping is many-to-one on purpose: `532624` ships an MDD declaration and an
MDR one over the same list, so an annex is not consumed by the first primary
that claims it.

A key claimed by **two** annexes is dropped whole rather than resolved. `533173`
ships its list once per device class (`..._Liste_DoC_Klasse_I.pdf`,
`..._Liste_DoC_Klasse_Is.pdf`) against three declarations, and the leading number
does not say which list belongs to which. Its declarations keep no list and go to
the manual queue; a confident wrong coverage claim is worse than none. The scan
result counts it as `companion_ambiguous`.

Authored for Komet only. Like `ref_normalize`, it is a measured, manufacturer-
specific shape, not a default worth inheriting.

## `extract_hints` — the only key an LLM reads

Every other key drives deterministic code. This one is sent to a model, so it
carries risks none of the others do.

```json
"extract_hints": {
  "ref_list": "REF codes on this manufacturer's declarations are ISO 6360 bur codes printed FIGURE.SHANK.SIZE (e.g. H1.314.006). Return them exactly as printed; do not reorder.",
  "general": "..."
}
```

Rules:

- **Describe the document, never the answer.** "Dates are written DD.MM.YYYY" is
  a hint. "The expiry is usually 2030" is a defect.
- Only the `manufacturer` hint is ever **withheld automatically** (guard 2),
  and only when `manufacturer` is itself the field being asked — that is the
  one circularity the guard exists to stop: telling the model the very
  identity it is meant to determine. Every other field's hint, including the
  `ref_list` example above, reaches the model whether or not that field is
  currently being asked; it could not answer an unasked field anyway, since
  the prompt only ever requests the fields actually missing.
- Every block is prefixed with an override instruction telling the model to
  ignore the hint if the document disagrees. That is what makes a hint from a
  *guessed* manufacturer safe.
- **Bump `rev` when you change a hint.** The revision is recorded on every
  extraction it steered (`extraction_attempt.playbook_rev`); without the bump,
  two different extractions claim the same provenance.
- **Measure before keeping one.** Run the corpus diff for that manufacturer. A
  hint that moves nothing is tokens for nothing.
## `coverage_map` — the manufacturer's own article -> document index

Separately from the PDF corpus, Komet ships two files that name which
declaration covers which catalogue article:

```
1-Where to find DoC (Medical Devices Only).xlsx
Artikli, kjer je originalni proizvajalec drug kot Komet/SPECIFIKACIJA.docx
```

The first is a spreadsheet covering Komet's own medical-device articles; the
second is a Word table covering the articles where the original manufacturer
is someone other than Komet. Each row names an article and reference number
and, in its `key` column, the document that covers it -- not a filename, but
a short string that still has to be resolved to one:

```json
"coverage_map": {
  "sources": [...],
  "key_resolves": [
    {"kind": "filename-prefix", "under": "DOC"},
    {"kind": "folder", "under": "Artikli, kjer je originalni proizvajalec drug kot Komet"}
  ],
  "canonical": {"key": "^(\\d+)",
                "prefer": ["_List_SIGNED", "_DoC_List_"],
                "annex": ["_RA_812_Liste", "_Liste.pdf"]}
}
```

A `key` value from the first source resolves as a filename PREFIX under the
`DOC/` folder; a `key` value from the second resolves as a FOLDER name under
`Artikli, kjer je originalni proizvajalec drug kot Komet/`. `canonical` then
picks the one file the resolved set should be read as covering: `key` is the
same leading-number capture as `companion`'s, `prefer` lists suffixes that
mark a signed, current list, and `annex` lists suffixes that mark a list
subordinate to one of those.

Read by BACKFILL on the live path (`_coverage_map_rule`, `app/handlers/
backfill.py:321`) to pick the canonical file and to seed stated coverage, and
by the one-shot `komet-coverage` CLI. Measured 2026-08-18: 246 of Komet's 319
items are covered by the index (229 of the 258 BC flags as medical devices).

## `exclude` — files in the folder that are not documents

Every corpus folder imported before 2026-08-19 (GC, IVOCLAR, KOMET, DENTSPLY,
VOCO, STRAUMANN) held compliance documents and nothing else, which is why
BACKFILL had no filter. Neodent's folder is the first that does not: of its 34
PDFs, 28 are Dentalia's own invoices, delivery notes and quotations, and they
carry its customers' names and addresses.

```json
"exclude": {
  "reason": "Dentalia's own sales paperwork, not compliance documents (2026-08-19).",
  "filenames": ["^PDO\\d", "^PRA\\d", "^dobavnica", "^ra[cč]un"]
}
```

Each entry is a regex matched with `re.search`, case-insensitively, against
the **basename only** -- never the path, because a corpus path carries the
brand folder and the dump's own directory names, and a pattern accidentally
matching one of those would drop files it was never written for. Anchor with
`^` where you mean it.

Filename-only by design: deciding from content means paying the extraction to
learn the document was an invoice, which is the cost being avoided.

Unlike `companion` and `coverage_map`, which archive a file and then decline
to make a document of it, `exclude` drops the file **before** it is hashed,
archived or ledgered. That is the whole point -- an excluded file must not
reach the archive.

Two guards, because a rule that quietly eats declarations is the expensive
authoring slip here:

- `reason` is required. A pattern nobody can account for later is rejected at
  load, along with a blank pattern (which matches every filename) and one that
  does not compile.
- Every pattern's hit count lands on the job result as `excluded_by`, so an
  over-firing rule is visible without re-reading the corpus. A rule that
  excludes the entire corpus fails the scan rather than reporting `scanned: 0`.

**No playbook authors an `exclude` rule today.** Neodent was the case it was
built for, and the client's answer to the mess was to skip the folder outright
rather than filter it (see `skip_backfill` below). The key stays because the
situation will recur and because a filtered scan is the better outcome when a
dump is *mostly* sound; but it is currently unused, and an unused mechanism is
worth deleting if the next messy folder also gets skipped rather than filtered.

## `skip_backfill` — a dump that is not fit to ingest at all

```json
"skip_backfill": {
  "reason": "Client ruling 2026-08-19: the SFTP folder is a mess and is not to be scanned. ..."
}
```

`backfill.scan` raises `BackfillError` and the job lands in dead-jobs. The
check runs before the folder is even required to exist and before a storage
adapter is built: a disowned dump is refused on identity, never on anything
found inside it. So a wrong path under a skipped brand reports the skip, which
is the actionable fact, rather than "drive_folder does not exist".

Why a refusal rather than a convention: "we agreed not to scan that one" is not
something a re-run can know. Anyone following the runbook would re-ingest a
dump the client has disowned, and the only symptom would be documents
reappearing in the review queue.

`reason` is required, for the same reason `exclude` requires one — the symptom
of an unexplained refusal is a manufacturer that never files anything, with no
error anyone thinks to look for. Say what happened and when, and say what would
lift it.

Distinct from `exclude`, which drops named files from a dump that is otherwise
sound. This says the whole folder is untrusted, so no file in it is worth
archiving.

## `crawl` — a listing-page crawl recipe (INERT — parsed and validated, not yet run)

Design: `docs/superpowers/specs/2026-08-21-playbook-crawl-recipe-design.md`
(§3 for the shape, §4.3 for the defences below); all five open questions in
its §7 are ruled, 2026-08-21, Denis. **Nothing reads `Playbook.crawl` yet.**
The DISCOVER rung that fetches the index page, extracts links and emits
`fetch.url` per surviving link is its own later slice. What exists today is
narrower and deliberate: a malformed or robots-refused recipe is refused at
authoring time, before anything could ever fetch with it.

```json
"crawl": {
  "index_url": "https://www.voco.dental/en/service/download/instructions-for-use.aspx",
  "link_pattern": "\\.pdf$",
  "same_host_only": true,
  "allow_hosts": ["cdn.example.com"],
  "doc_type_from": {"ifu": "IFU", "konformit": "DoC"},
  "pagination": {"param": "page", "max_pages": 20},
  "max_links": 200
}
```

- `index_url` — required. Must be an absolute `http(s)` URL: the page DISCOVER
  will fetch and read links off of.
- `link_pattern` — required. A regex, matched against the **resolved absolute**
  URL of every candidate link (never the raw `href`) — must compile, or the
  whole file fails to parse, naming the playbook and the regex error.
- `same_host_only` — optional, **defaults to `true`** (ruled 2026-08-21). Only
  links resolving to the index page's own host survive the filter.
- `allow_hosts` — optional list of extra hostnames a link may resolve to
  besides the index host. **The only way to widen beyond `same_host_only`** —
  never flip that off for "follow anything". Normalized the same way
  `domains` is (`_normalize_domain`): a bare hostname or a full URL fold to
  the same value, and a path scope (`straumann.com/medentika`) is kept.
- `max_links` — required *in effect*: an unauthored value defaults to 200, but
  the field is never "unlimited", and whatever value is authored is checked
  against a hard ceiling (2000 — a guard against an over-broad harvest, not a
  tuned number). This is the last line of defence against the trap below.
- `doc_type_from` — optional. `{casefolded substring: doc type}`, e.g.
  `{"ifu": "IFU"}`. Every value must be one of `DoC`/`EC`/`IFU`/`ISO` **or
  `null`**, or the file fails to parse — the same discipline `type_markers`
  applies, for the same reason: a value filed under an unknown type would
  silently type a crawled document as something the registry has no member for.

  **`null` means exclude** (ruled 2026-09-03): the link is harvested and
  counted, but never typed as a compliance document. Use it for a library that
  mixes other paperwork in with the declarations — NSK publishes 446 safety
  data sheets beside 160 declarations, and `{"sds_": null}` keeps them out of
  the registry. Prefer this over a negative lookahead in `link_pattern`: a
  pattern exclusion drops the files from the crawl's counts too, so a partial
  harvest reads as a complete one, which is exactly the silence the never-silent
  rule forbids.
- `note` — optional, free text, ignored by the code. Record **what you
  measured and when**: the anchor count, how many matched, whether a browser
  was needed, and the robots verdict. Edenta's own notes are the model — and
  the reason they matter is that its 2026-08-20 note said 11 documents while
  the live page served 13 on 2026-09-03. A recipe authored from a stale note
  is the failure mode here.
- `pagination` — optional. `{"param": str, "max_pages": int}`. `max_pages`
  must be at least 1, capped at 50 (also a guard, not a tuned number). The
  actual stop conditions (`max_pages`, a page yielding zero new links, or a
  page whose link set repeats the previous page's — the common
  infinite-pagination shape) are the rung's job to enforce; this only checks
  the shape.

### The two traps this design names

**An over-broad `link_pattern` harvests a whole site.** `"\\.pdf$"` against a
page that links a whole site's PDFs — not just this manufacturer's — pulls
someone else's documents into our archive. Three defences, all required
together: `same_host_only: true` by default so a match can only resolve to the
index host unless a host is explicitly named in `allow_hosts`; `max_links`
capped hard rather than left unbounded; and (once the rung ships) every crawl
logging a full count breakdown — links seen, matched, capped, deduped — so a
crawl that harvested 3 of 40 real documents is visible as a bad match, not a
quiet success.

**A POST/form-filtered portal is out of scope.** This mechanism reads one GET
of `index_url`, follows pagination by appending a query parameter, and stops.
A listing that requires submitting a form, logging in, or filtering via
POST is not a case `crawl` can express — author it `doc_sources`
`kind:"portal"` instead, so a human handles it. Don't let the next author
discover this the hard way; if a manufacturer's download centre needs a
click before it shows anything, `crawl` is the wrong tool for it.

### robots — a crawl recipe is a second way to name a fetch target

`doc_sources[kind:"direct"]` and `crawl.index_url` are both fetch targets, and
`validate()` refuses **either** on a host listed in `robots_refused.txt` (see
above) — the same guard, not a parallel one, so the 2026-08-20 ruling covers a
crawl recipe exactly as it covers a `direct` source. The message tells you to
drop the crawl block and author `kind:"portal"` instead, so a human handles it.

## Still to author

Track per-manufacturer research here; delete a line when its file is complete.

- `voco.json` — `manufacturer` is still the brand string `VOCO` carried over from `app/extract/t0_layout/voco.json`. No source in `docs/` states VOCO's legal entity name; needs research before promoting to a legal name.
- `komet.json` — `manufacturer` is still the brand string `KOMET` carried over from `app/extract/t0_layout/komet.json`. Legal entity not documented in `docs/`.
- `ivoclar.json` — `manufacturer` is still the brand string `IVOCLAR` carried over from `app/extract/t0_layout/ivoclar.json`. Legal entity not documented in `docs/` (note: `docs/dentalia-job-type-handbook.md` uses "Ivoclar Vivadent AG" in a worked example, but that is illustrative prose, not a sourced fact — do not promote from it without verification).
- `ultradent.json`, `euronda.json`, `detax.json`, `kerrhawe.json`, `cattani.json` — each `manufacturer` is the vendor master's brand string (`ULTRADENT`, `EURONDA`, `DETAX`, `KERRHAWE`, `CATTANI`), not a legal entity. Deliberate: the name is the join key `for_manufacturer` matches on, and `manufacturer_alias` maps the BC code to exactly that string, so renaming one without an alias makes its playbook unreachable. No source in `docs/` states any of these legal entities; research before promoting, and keep the vendor-master spelling as an alias when you do.
- `dentsply-sirona.json` — `manufacturer` is still `DENSTPLY`, verbatim from `app/extract/t0_layout/denstply.json` including its misspelling (missing legal-entity research; the corpus's own DoCs name several distinct Dentsply legal entities — e.g. "Dentsply Implants Manufacturing GmbH", "Dentsply Implants NV" — so this may need per-division handling rather than one name).

## Pending manufacturer codes

The codes below are CONFIRMED or LIKELY candidates from the 2026-07-06 manufacturer-code sweep (`docs/dentalia-manufacturer-code-sweep.md`), but do not yet have a playbook file carrying them in `bc_codes`. Until a code is authored into a playbook, `playbooks sync` cannot seed it into the `manufacturer_alias` table, leaving those items' canonical manufacturer as a bare code rather than the legal entity. Adding these codes to playbook files is necessary to unlock manufacturer-keyed discovery for the remaining catalogue items.

Authored on 2026-08-11 and therefore removed from the table below: `002` STRAUMANN (`straumann.json`), `028` PLANMECA (`planmeca.json`), `036` BREDENT (`bredent.json`). Each carries the vendor master's own name verbatim, so `playbooks reconcile` predicts a zero-code alias diff — the playbook wins the entity without renaming it. `160` NEODENT (`neodent.json`) was authored at the same time; it never appeared here because vendor-master had already resolved it.

Authored on 2026-08-27 from the client's own hand-off of the URLs Dentalia used manually before this project, and therefore removed from the table below: `168` ULTRADENT (`ultradent.json`). Four more were authored in the same pass and never appeared here, because the sweep had not resolved them: `052` EURONDA (`euronda.json`), `164` DETAX (`detax.json`), `021` KERRHAWE (`kerrhawe.json`) and `087` CATTANI (`cattani.json`). All five take their `manufacturer` verbatim from the vendor master, so `playbooks reconcile` predicts a zero-code alias diff — measured 2026-08-27, identical before and after: 0 blocking, 0 of 389 codes change. The last two settle the sweep's KERR/`010` and CATTANI/`10015` conflict annotations, which the vendor master supersedes (`docs/dentalia-manufacturer-code-sweep.md`, correction 5).

Domains and doc_sources in these files come from the SFTP corpus itself — URLs the manufacturers' own documents print — never from general knowledge. Certification bodies (BSI, TÜV SÜD, SGS, MDC), e-signature services and chemical databases were excluded on purpose: `domains` restricts DISCOVER's search, so a certifier's host would point a manufacturer's document search at the wrong site.

All data taken verbatim from the sweep. Where a brand name is a product line rather than the legal manufacturer, the sweep notes the correct legal entity — use that name as the playbook's `manufacturer` field.

| Code | Items | Brand | Confidence | Note |
|---|---|---|---|---|
| `003` | 88 | KULZER | LIKELY | |
| `010` | 165 | VDW | CONFIRMED | |
| `011` | 331 | KAVO | LIKELY | |
| `030` | 187 | MELAG | CONFIRMED | |
| `037` | 22 | HAHNENKRAT | LIKELY | |
| `038` | 43 | BECHT | LIKELY | |
| `042` | 32 | ASA | CONFIRMED | |
| `055` | 38 | SCHEU | LIKELY | |
| `067` | 72 | BEGO | LIKELY | |
| `078` | 83 | ERKODENT | CONFIRMED | |
| `081` | 2567 | CARL MARTIN | LIKELY | Only 3 text-match hits, but real-world corroboration is strong: Carl Martin GmbH (Solingen, since 1916, one of Europe's largest dental instrument makers) sells exactly this product category — code `081`'s actual items are forceps/elevators/curettes. Still needs BC/IT confirmation given the tiny direct sample against 2,567 total items. |
| `099` | 247 | NSK | CONFIRMED | |
| `100` | 20 | VITA | LIKELY | |
| `128` | 47 | RHEIN | LIKELY | |
| `241` | 26 | DREVE | CONFIRMED | |
| `246` | 26 | ECOLAB | LIKELY | |
| `6586` | 67 | TAVOM | CONFIRMED | |
| `10005` | 195 | 3SHAPE | CONFIRMED | |
| `10008` | 52 | ASIGA | CONFIRMED | |
| `10084` | 193 | PRITIDENTA | CONFIRMED | |
| `10122` | 24 | AMBER MILL | CONFIRMED | legal manufacturer is **HASS Corporation** (Korea); Amber Mill is their CAD/CAM block product line |
| `10127` | 98 | MIYO | CONFIRMED | legal manufacturer is **Chemichl AG** (Liechtenstein); MiYO is its liquid-ceramic line, and Jensen GmbH only sells it (corrected 2026-08-20 — the earlier "Jensen Dental" reading was wrong) |
| `10135` | 33 | SABANA | CONFIRMED | |
| `10176` | 199 | PANTHER | CONFIRMED | legal manufacturer is **SUN Oberflächentechnik GmbH**; Panther is their product line (matches SFTP folder name) |
| `10188` | 19 | ORDOLINE | CONFIRMED | |
| `10192` | 8 | LUMIWHITE | LIKELY | |
