# Dentalia's hand-off URLs — live probe results

2026-08-27. Probe session on `worktree-eudamed-enhancement`; **authoring belongs
wherever the playbook content lands** (these are `doc_sources` edits — pure
content, no code). Nothing was edited: no playbook file was touched in any
worktree.

Denis supplied 12 URLs that Dentalia used **manually, before this project**, to
find compliance documents. This file records what each one actually is when our
own fetcher asks.

## Method

All 12 fetched live on 2026-08-27 with `_USER_AGENT` from
`app/adapters/fetcher.py` (`DentaliaComplianceBot/1.0`). `robots.txt` read first
for every host and parsed with `app/robots.py::_parse` — the production RFC 9309
parser, not `urllib.robotparser` — and a DISALLOW verdict meant the page was not
fetched. `pritidenta.com` was excluded entirely under Denis's 2026-08-20 ruling
(`playbooks/robots_refused.txt`).

Reported counts are `.pdf` hrefs in the **served HTML of a plain GET** — i.e.
what DISCOVER sees without a browser tier.

## The correction that matters: `playbooks/nsk.json` is wrong

The authored note says *"the PDF href sits behind a Download button, not in the
static HTML — a plain GET yields no document URLs."* Not true on 2026-08-27:

* `https://www.nsk-library.com/` → **1.329 unique `.pdf` URLs** in the served
  HTML, under `/admin/wp-content/uploads/`.
* `robots.txt` disallows only `/admin/wp-admin/`. The uploads path is **not**
  covered. Verdict: ALLOW.
* Two were downloaded to prove they are documents, not link text:
  * `000018-12-21-04_008_EC_Doc_VA-SET.pdf` → 200, **5.747.074 B**, magic
    `%PDF-`, `application/pdf`
  * `CE_623882_BSI_NL.pdf` (uk host) → 200, **391.181 B**, real PDF
* Doc-type labels in the page text: Safety Data Sheet 446, **Declaration of
  Conformity 160**, Certificate 22, Self Declaration 2.

NSK is BC code `099`, 247 items, CONFIRMED tier.

**The index itself must stay `kind:"portal"`.** `playbooks/README.md` is explicit
that a listing page authored `direct` is archived as an unreadable PDF and
dead-ends at EXTRACT silently. What changed is not the kind — it is that the
**discovery bottleneck is gone**: a crawl recipe over that page harvests 1.329
URLs from a plain GET, no Playwright. This is now the strongest crawl-recipe
candidate in the corpus (S2.1).

`https://www.uk.nsk-dental.com/support/certificates/` — the URL `nsk.json`
already names as "unfetched" — is live: robots.txt 404 (unrestricted), **7**
statically-linked PDFs: DAkkS ISO 9001, EN ISO 13485, ISO 14001,
`CE_623882_BSI_NL.pdf`, `MD_623878.pdf`, `HD601485080001.pdf`, a 13485 cert.
Corporate certs, not per-article; small enough to hand-author as 7 `direct`
sources.

## Per-URL results

| Manufacturer | URL | Verified 2026-08-27 |
|---|---|---|
| NSK | `nsk-library.com` | 200 · **1.329 PDF hrefs** · robots ALLOW · downloads verified. Note above is false |
| NSK | `uk.nsk-dental.com/support/certificates/` | 200 · 7 PDFs · robots.txt 404 = unrestricted |
| Dürr Dental | `duerrdental.**net**/en/services/download-centre/` | robots.txt **404**; 302 → `/home?return_url=…`; title **"Das Händlernetz: DuerrDental.NET"** — the **dealer extranet, login-gated**. 28 "login" refs, login form, **0 PDFs**. NOT a public mirror of the `.com` page |
| Dürr Dental | `duerrdental.com/…/download-center/` (authored) | 200 · **0 PDF hrefs** — client-rendered, exactly as `duerr-dental.json` records |
| Ultradent | `/resources/safety-data-sheets` (supplied) | 200 · 0 PDF hrefs · SDS only — **wrong page** |
| Ultradent | `/resources/product-instructions` (found) | robots **ALLOW** · **147 IFU PDFs** static · fetched one at 845.107 B, real PDF |
| Ultradent | `/resources/certifications` (found) | robots **DISALLOW** (`Disallow: /resources/certifications`) — but its 3 PDFs (ISO 13485 ×2, MDSAP) sit on `assets.ctfassets.net`, robots.txt **404**; fetched the ISO 13485 at 488.019 B |
| Kerr | `kerrdental.com/en-eu/download-center?f[0]=…629` | robots ALLOWS, but the page is a **3.038 B F5 "Client Challenge" JS bot-wall**. No content to a plain GET. Correct target shape, technically unfetchable |
| Euronda | `download.euronda.com` | 200 · **0 PDFs** · links are `/members/login` + `/members/register/` — **registration-gated**. 9 country siblings (`.de`, `.it`, `.cz`, `.es`, `.fr`, `.pl`, `.ro`, russia). Also exposes a public SharePoint `EUIT-TECHLIBRARY` share that returned 200 anonymously |
| Cattani | `cattani.it` (root only, as supplied) | 200 · root **does** link `/certificazioni/` and `/area-download/` (EN `/en/download-area/`), both 200, robots ALLOW — but only 2 privacy-notice PDFs are statically linked; documents are not in the served HTML |
| DETAX | `detax.com/dental/3d-resins` | 200 · 9 unique PDFs: 3D-Guide brochures ×6 languages, 2 validation matrices, general T&Cs. **Zero compliance documents** |
| Dentsply Sirona | filtered download-center (already authored) | 200 · 14 PDF hrefs, but all symbol glossaries / sustainability report / brochures — the DoC filter is applied **client-side**. Confirms `portal`; no edit needed |
| Solventum | `eifu.solventum.com` (already authored) | still **HTTP 500** to a non-browser client, a week after the note. Unchanged |
| Solventum | `content-portal.productsup.com/Solventum/catalog/<uuid>` | SPA shell, 5 hrefs, no content. See the robots trap below |

## Proposed authoring (not done)

Keep:

* **`nsk.json`** — correct the false note; keep the index `portal`; flag as
  crawl-recipe candidate. Add the 7 `uk.nsk-dental.com` certs as `direct`.
* **`ultradent.json`** (new) — `/resources/product-instructions` as `portal`.
  Ultradent is 3 of the 12 devices still uncovered (`PHASES.md`).
* **`duerr-dental.json`** — add the `.net` dealer portal as a second `portal`
  with the login note, so nobody re-derives it or mistakes it for a public host.
* **`cattani.json`** (new) — `/en/download-area/` + `/certificazioni/` as
  `portal`. Replaces "no usable entry point".
* **`euronda.json`** (new) — `download.euronda.com` as `portal`, note the
  registration gate.
* **`kerr.json`** (new) — `portal` only, note the F5 bot-wall.

Drop: DETAX product page · Ultradent SDS page · the productsup portal.
No edit: Dentsply, Solventum eIFU, Pritidenta.

## Open decisions (Denis)

1. **Kerr and Cattani have no BC code of their own.** Kerr is a 19-hit conflict
   claimant on VDW's `010`; Cattani a 6-hit claimant on DURR's `10015`
   (`docs/dentalia-manufacturer-code-sweep.md`). Author them without `bc_codes`
   — valid, but they link to no items — or hold for the BC vendor master?
2. **Ultradent's certifications page is robots-Disallowed while its PDFs sit on
   a host that publishes no robots.txt.** Treat the listing's Disallow as
   covering the assets (author nothing), or author the 3 certs as `direct` on
   the permitted CDN host?
3. **Kerr's bot-wall is not a robots refusal.** Add `kerrdental.com` to
   `playbooks/robots_refused.txt` (the COLTENE precedent — an operator decision
   a runtime robots read cannot see), or leave it portal-only?

## Two things for THIS branch specifically

1. **A robots.txt that answers 200 with HTML reads as blanket permission.**
   `content-portal.productsup.com/robots.txt` returns the **83.776-byte SPA
   shell**, HTTP 200. Run through the real parser: 0 rules, and `allows()`
   returns `True` for every path, including `/anything/at/all.pdf`.
   `app/robots.py` fails closed on an unreachable host but never checks
   content-type, so a catch-all SPA route reads as fully permitted. Affects
   nothing currently authored. Verified by running `_parse` on the live body.

2. **`PHASES.md:329` needs amending.** It records Denis's 2026-08-20 ruling as
   *"Contacts in the database, crawl URLs in the playbooks … the database never
   contains a crawl URL."* Migration `047_playbook_body.sql` stores
   `doc_sources` in `playbook_body` — crawl URLs in the database. The ruling or
   the boundary statement has to move; it should not drift silently.

## Where the raw output is

Probe scripts and their JSON output were written to the eudamed-enhancement
session scratchpad only (`probe.py`, `hrefs.py`, `deep*.py`, `probe.out`) — not
committed anywhere. Every figure above is reproducible by re-running a GET; none
of it came from cache or memory.
