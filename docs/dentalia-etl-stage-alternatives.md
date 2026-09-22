# Dentalia — ETL Stage Alternatives Analysis

v1 — 2026-07-03
Scope: INGEST, RESOLVE, DISCOVER, QUEUE. (FETCH/EXTRACT tooling was settled in the runtime decision — PyMuPDF/pdfplumber/Playwright; VALIDATE/GATE are pure logic, no tooling choice.)
Ratings 1–5: **Fit** = match to this system's requirements · **Diff** = implementation + operational difficulty (5 = easiest) · **Flex** = adaptability to change (source swap, scale-up, requirement drift).

---

## 1. INGEST

Two independent choices: (a) file-parsing library, (b) BC access path for the v2 adapter.

### 1a. Parsing CSV/Excel exports

| Option | Pros | Cons | Fit | Diff | Flex |
|---|---|---|---|---|---|
| **pandas + openpyxl** | Ubiquitous; handles messy real-world Excel (merged cells, mixed types); every edge case has a Stack Overflow answer | Heavy import; memory-hungry at extremes (irrelevant at 116k rows) | 5 | 5 | 4 |
| polars | Fast, clean API, low memory | Speed solves a problem we don't have; younger Excel support; team familiarity lower | 3 | 4 | 4 |
| stdlib csv + openpyxl direct | Zero deps beyond openpyxl | Hand-rolling type coercion, encoding, dialect sniffing pandas gives free | 2 | 3 | 3 |
| DuckDB (read_csv/xlsx via SQL) | Elegant for diffing snapshots in SQL | Second query engine next to Postgres for no gain | 2 | 3 | 3 |

**→ pandas + openpyxl.** Boring is correct here; the diff-against-mirror logic is the actual work, not the parsing.

### 1b. BC access (BcApiAdapter, v2)

| Option | Pros | Cons | Fit | Diff | Flex |
|---|---|---|---|---|---|
| **OData V4 / standard BC API pages** | Documented, supported, versioned REST; works on cloud and on-prem; delta reads via filters on `lastModifiedDateTime` | Requires Entra app registration + client credentials from their IT; page-size pagination quirks | 5 | 4 | 5 |
| Custom AL extension (purpose-built API page) | Exact fields, one call | Needs BC developer + deployment into their tenant — new dependency on their vendor; overkill for reading an item list | 2 | 2 | 3 |
| Direct SQL (on-prem only) | Full power | Unsupported, breaks on BC upgrades, only if on-prem, vendor hostility guaranteed | 1 | 2 | 1 |
| Scheduled export drop (BC job queue → file to shared folder/SFTP/Drive) | Zero credentials for us; their side owns it; degenerate case of the CSV adapter | Someone must build/maintain the BC-side job; no on-demand reads | 4 | 5 | 3 |
| Power Automate / Logic Apps bridge | Low-code, their team may already use it | Another moving part outside the repo; flaky auth renewals; debugging in a GUI | 2 | 3 | 2 |

**→ Standard OData/API pages** when credentials materialize; **scheduled export drop** is the sanctioned fallback that keeps everything on our side identical to the v1 CSV adapter. Custom AL only if a needed field turns out unexposed (product class and MD flag are standard-exposable — verify in the export-format check, open item v2 §7.2).

## 2. RESOLVE

Sub-problems: manufacturer normalization, item→group clustering, REF/name matching. Options are composable, not exclusive.

| Option | Pros | Cons | Fit | Diff | Flex |
|---|---|---|---|---|---|
| **rapidfuzz** | C++-fast token/partial ratios; perfect for alias tables and candidate scoring; zero infra | Pairwise only — needs our blocking logic (by manufacturer) to avoid O(n²) | 5 | 5 | 4 |
| **pg_trgm (in-Postgres)** | Similarity + GIN index inside the DB we already run; great for candidate *generation* (top-k similar names in one query) | Trigram similarity is crude alone; tuning threshold per language is fiddly | 4 | 4 | 4 |
| splink | Proper probabilistic record linkage (Fellegi–Sunter), explainable match weights — pairs beautifully with the evidence model | Designed for person/entity dedup at millions of rows; model training ceremony oversized for grouped catalogue data | 3 | 2 | 4 |
| dedupe (python lib) | Active learning — humans label pairs, model improves | Maintenance has slowed; labeling UX must be built; review queue already gives us human labels for free | 2 | 2 | 3 |
| recordlinkage | Academic, clean pandas API | Slow, unmaintained feel, nothing rapidfuzz+pg_trgm don't cover | 1 | 3 | 2 |
| Embeddings + pgvector clustering | Catches semantic matches fuzzy string misses ("curing light" ≈ "polymerization lamp"); cheap (local sentence-transformers or API embeddings) | New failure mode: confidently similar-meaning wrong matches; needs calibration; adds pgvector dependency | 3 | 3 | 4 |
| T1 LLM adjudication (in design) | Handles the genuinely ambiguous tail; already confidence-gated to staging | Per-call cost; nondeterministic — which is why it's last and gated | 4 | 4 | 5 |

**→ Layered, per the design:** pg_trgm generates candidates in-DB → rapidfuzz scores → deterministic accept above threshold → T1 adjudicates the ambiguous middle → staging. **splink is the upgrade path** if Phase 0 shows name-matching quality is worse than expected — its explainable weights fit the audit story better than embeddings do. Embeddings stay in the back pocket; for a compliance matcher, "explainably similar strings" beats "opaquely similar meanings."

## 3. DISCOVER

Context that shapes everything: discovery is **playbook/EUDAMED-first by design** — the search API only serves the un-playbooked tail, so monthly volume is small (hundreds of queries, not tens of thousands). Quality-per-query and provenance matter more than price. Market note: Bing Search API retired Aug 2025; Google sued SerpApi (DMCA, Dec 2025) — scraper-based SERP APIs now carry documented legal/platform risk; Tavily was acquired by Nebius (Feb 2026).

| Option | Pros | Cons | Fit | Diff | Flex |
|---|---|---|---|---|---|
| **Brave Search API** | Independent index (30B+ pages) — no scraping dependency; no query logging (clean story for a medical client's data); ~$5/1k, low latency | Index smaller than Google; long-tail dental manufacturer sites occasionally missed; free tier reportedly removed | 5 | 4 | 4 |
| Serper (Google SERP) | Google-quality results, cheapest at $0.30–1/1k | Scraper category: legal/platform risk now concrete (SerpApi lawsuit); results provenance = someone else's scraping | 3 | 5 | 3 |
| SerpAPI | Most mature scraper, 25+ engines | $10/1k; same scraper-category risk, now named in a Google DMCA suit | 2 | 4 | 3 |
| Tavily | LLM-ready output, popular in agent frameworks | Aggregator (mixed provenance); markdown-extraction noise; reported flakiness on JS pages; post-acquisition roadmap uncertainty; $8/1k | 2 | 4 | 3 |
| Exa | Semantic search | Wrong shape: we do exact-document lookup ("Ivoclar Tetric PowerFill DoC PDF"), not concept exploration; credit pricing compounds | 1 | 3 | 3 |
| Google Programmable Search (CSE JSON API) | Official Google, $5/1k, 100/day free | 10k/day cap; designed for site-restricted search — though that restriction is actually *useful* per-manufacturer | 3 | 4 | 2 |
| SearXNG self-hosted | Free, private | Upstream engines rate-limit your IP; reliability unacceptable for a pipeline SLA | 1 | 2 | 2 |
| **Site-native discovery (no API):** sitemap.xml parse, site search endpoints, doc-library crawl | Free, deterministic, per-manufacturer — this *is* the playbook mechanism | Requires playbook per site (already the V4 plan) | 5 | 3 | 5 |
| EUDAMED mirror | Free, deterministic, legally mandated growth | Coverage ramping through Nov 2026/May 2027 (established in workflow doc §6) | 5 | 4 | 5 |

**→ SearchAdapter abstraction with Brave primary.** The no-logging independent index is a genuine selling point when Dentalia asks "where do our product names get sent?" (open item: external-AI-API policy — same conversation). Serper as config-switchable fallback when Brave's index misses a small manufacturer — accepting the scraper risk for a fallback role only. Google CSE worth a note: a per-manufacturer restricted CSE is effectively a hosted playbook-lite; could serve as an interim before real playbooks exist. Volume math at tail-only usage: even 2k queries/month ≈ €10 on Brave — search API choice is a provenance/reliability decision, not a cost decision.

## 4. QUEUE

Requirements recap: Postgres-backed (transactional writes with registry+audit), priority lanes, per-domain politeness lease, dedupe keys, dead-letter visibility, at-least-once + idempotent.

| Option | Pros | Cons | Fit | Diff | Flex |
|---|---|---|---|---|---|
| **Hand-rolled SKIP LOCKED (~200 lines)** | Exact semantics we specified (domain lease, priority lanes, dedupe_key) with zero translation; fully understood by its one maintainer; jobs table joins directly with registry/audit in one transaction | We own every bug; no dashboard for free; retry/backoff/reaper written by hand (it's ~50 of the 200 lines) | 5 | 3 | 5 |
| **pgqueuer** | Modern async Python; LISTEN/NOTIFY + SKIP LOCKED (sub-second wake without poll loops); built-in scheduling, concurrency limits; active development | Younger project, smaller community; per-domain politeness lease still custom on top; schema is its own, not ours | 4 | 4 | 4 |
| procrastinate | Mature, sync+async, periodic tasks, retries; **arbitrary task locks** could implement the domain lease natively; Django-grade docs | Project publicly seeking additional maintainers — sustainability question for a 10-year system; heavier abstraction than needed | 4 | 4 | 3 |
| Celery + Redis/RabbitMQ | Industry default, every feature | Second infra service (broker) killing the one-DB transactional property; visibility-timeout semantics infamous; operational cost unjustified at tens of jobs/sec | 1 | 2 | 3 |
| Dramatiq / arq (Redis) | Simpler than Celery, clean APIs | Same broker objection: loses transactional queue+registry writes, adds Redis to compose | 2 | 3 | 3 |
| Hatchet | Postgres-based, durable workflows, dashboard UI, DAG support | A workflow *server* to run and upgrade — the modular-monolith premise inverted; DAG engine unneeded (our topology is a simple enqueue chain) | 2 | 2 | 4 |
| Temporal | Bulletproof durable execution | Cluster + worker SDK ceremony for a 6-stage linear pipeline; absurd operational footprint here | 1 | 1 | 4 |

**→ Hand-rolled first, pgqueuer as the escape hatch.** The deciding factors: (1) the transactional claim-job/write-result/append-audit property is the design's consistency backbone and is trivially native when the jobs table is *our* table; (2) the two exotic requirements (domain lease, priority lanes) are custom code under any library anyway; (3) a 10-year compliance system maintained by one person favors 200 lines that person wrote over a dependency whose maintainer pool is uncertain (procrastinate's call for maintainers is exactly this risk realized). If hand-rolled queue plumbing starts eating hours in Phase 1, pgqueuer's model is close enough that migration is days, not weeks.

## 5. Summary

| Stage | Primary | Fallback / upgrade path |
|---|---|---|
| INGEST parse | pandas + openpyxl | — |
| INGEST BC access | OData/standard API pages | Scheduled export drop (= v1 CSV adapter unchanged) |
| RESOLVE | pg_trgm candidates → rapidfuzz scoring → T1 gated adjudication | splink if Phase 0 match quality disappoints |
| DISCOVER | Playbooks + EUDAMED mirror; Brave for the tail | Serper (accepting scraper risk); per-manufacturer Google CSE as playbook-lite interim |
| QUEUE | Hand-rolled SKIP LOCKED | pgqueuer (near-drop-in model) |

Common thread: every stage keeps the swap behind an adapter (SourceAdapter, SearchAdapter, queue interface), consistent with the config-switch requirement from the decision log. Nothing above changes the €10–40/month steady-state envelope; the only recurring third-party cost introduced is the search API at ~€1–10/month at tail-only volume.
