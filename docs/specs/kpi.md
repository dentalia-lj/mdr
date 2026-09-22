# Unit spec — KPI board v1 (S1.6, GAP G14)

Normative sources (this spec adds only what they leave open — do not restate):
- PHASES.md § 1 (S1.6, S2.5) for status; the metric list this spec fills in with exact SQL is the former PHASES.md §B, archived verbatim in `docs/build-log.md` § "§A and §C as PHASES.md v3 carried them".
- PHASES.md S1.6 "KPI board per §B (Phase 1 metrics) on `GET /`" (quoted as
  written; the board answers on `GET /status` since 2026-09-14, `/` being Today).
- Schema `migrations/005_registry.sql` (`item_document_production` view — the mandatory read surface for anything coverage-shaped, per invariant "visibility rule for ALL consumers"), `010_extraction_cost.sql` (`extraction_spend` view), `009_manual_task.sql`, `012_resolve.sql` (`grouping_suggestion`).
- Invariant 2 (every production value carries complete evidence — not directly a KPI concern, but why K2 structurally cannot show an untrusted `match_basis`).

**Rule this spec exists to enforce:** numbers come from `item_document_production` (or another view), never a raw join of `document`/`item_document` — the view already encodes "visible iff both document AND link are production," and reimplementing that join ad hoc in a KPI query is exactly the kind of drift invariant 3 exists to prevent.

## 1. Metric registry

| id | metric | source |
|---|---|---|
| K1 | coverage % (strict + processed-scope + device + **declared**) | `item_mirror` × `EXISTS (item_document_production)`, unexpired evidence only; the device line adds `p.regulation IN ('MDR','MDD')`, the declared line adds `p.type = 'DoC'` |
| K2 | `match_basis` distribution (production links) | `item_document_production` |
| K3 | `missing_mfr_ref` rate | `item_mirror` |
| K4 | staging queue size + oldest age | `document`, `item_document`, `grouping_suggestion` |
| K5 | open `manual_task` count by kind | `manual_task` |
| K6 | dead-job count | `job` |
| K7 | job counts by (type, status) | `job` |
| K8 | sweep spend vs budget cap | `extraction_cost`, `extraction_spend` |

## 2. SQL per metric

**K1 — coverage.** Four lines, not one — see §3 for why. Two independent
axes: the DENOMINATOR varies (strict vs processed-scope, §3.1) and the
NUMERATOR varies (any production paper vs paper issued under a device
regulation, §3.4). The device line reuses the strict denominator, so it reads
as strict's honest subset rather than as an unrelated percentage.

All three numerators exclude lapsed evidence (`p.expires IS NULL OR p.expires
>= CURRENT_DATE`) — Denis ruling 2026-08-24 ([expiry-is-reported-never-
enforced]): lapse changes this KPI and nothing else. Documents keep their
status, links stay, the chase continues; but 85 lapsed production documents
were holding 1.451 production links and 40 items had no unexpired evidence at
all, so the headline overstated by exactly those 40.

```sql
-- strict: md_flag IS TRUE only
SELECT m.catalogue,
       count(*) FILTER (WHERE EXISTS (
         SELECT 1 FROM item_document_production p WHERE p.item_ref = m.item_ref
           AND (p.expires IS NULL OR p.expires >= CURRENT_DATE)
       )) AS covered,
       count(*) AS total
FROM item_mirror m
WHERE m.md_flag IS TRUE
GROUP BY m.catalogue ORDER BY m.catalogue;

-- processed scope: md_flag IS NOT FALSE (TRUE or NULL — what Ingest.process_md_unknown
-- actually sends into the pipeline)
... WHERE m.md_flag IS NOT FALSE ...

-- device: strict's denominator, but only paper issued under a device regime.
-- An ISO 13485 certificate is production paper about the manufacturer's
-- quality system, not about any article, so it counts in the two lines above
-- and not in this one.
       count(*) FILTER (WHERE EXISTS (
         SELECT 1 FROM item_document_production p
         WHERE p.item_ref = m.item_ref AND p.regulation IN ('MDR', 'MDD')
           AND (p.expires IS NULL OR p.expires >= CURRENT_DATE)
       )) AS covered
... WHERE m.md_flag IS TRUE ...
```

**K2 — match_basis distribution.**

```sql
SELECT match_basis, count(*) AS n
FROM item_document_production
GROUP BY match_basis ORDER BY n DESC;
```

**K3 — missing_mfr_ref rate.** Whole-mirror, not grouped: K1 and K3 both read
`per catalogue` until 2026-08-26, when the second-catalogue dimension was
removed from the code paths (one Business Central, one article numbering). Both
now return one row rather than a list of one, which is a shape change for
`/api/kpi` as well as for the board.

```sql
SELECT count(*) FILTER (WHERE mfr_ref IS NULL) AS missing,
       count(*) AS total
FROM item_mirror GROUP BY catalogue ORDER BY catalogue;
```

Since 2026-08-14 the NULL count also includes items whose BC vendor-article cell
held a stock remark rather than a code (`NE BO VEČ NA ZALOGI!` and friends):
INGEST scrubs those to NULL before the mirror write, counts them separately as
`mfr_ref_prose` on the job result, and files the original value under the
`mfr_ref_prose` anomaly. They genuinely have no article number for the REF gate,
so K3 counting them is the honest reading, and the rate rises accordingly.

**K4 — staging queue.** Three sub-counts, not one number (they are different tables with different review actions):

```sql
-- staged documents (age-trackable)
SELECT count(*) AS n, min(created_at) AS oldest FROM document WHERE status='staged';

-- staged links on production docs (C5) — item_document has no created_at column,
-- so only a count is possible, never an age. Do not fabricate one.
SELECT count(*) AS n
FROM item_document lnk JOIN document d USING (doc_id)
WHERE lnk.status='staged' AND d.status='production';

-- open grouping suggestions (age-trackable)
SELECT count(*) AS n, min(created_at) AS oldest FROM grouping_suggestion WHERE status='open';
```

**K5 — manual_task by kind.**

```sql
SELECT kind, count(*) AS n FROM manual_task WHERE status='open' GROUP BY kind ORDER BY kind;
```

**K6 — dead jobs.**

```sql
SELECT count(*) AS n FROM job WHERE status='dead';
```

**K7 — jobs by (type, status).**

```sql
SELECT type, status, count(*) AS n FROM job GROUP BY type, status ORDER BY type, status;
```

**K8 — spend vs cap.** USD totals from SQL; EUR conversion happens in Python (§3), never baked into the query, so the rate used is always the one rendered next to the number.

```sql
-- all-time (compare to budget.sweep_cap_eur)
SELECT sum(cost_usd) AS usd, count(*) FILTER (WHERE cost_usd IS NULL) AS unpriced_calls
FROM extraction_cost;

-- trailing 30 days (compare to budget.monthly_cap_eur)
SELECT sum(cost_usd) AS usd, count(*) FILTER (WHERE cost_usd IS NULL) AS unpriced_calls
FROM extraction_cost WHERE at >= now() - interval '30 days';

-- breakdown by model x tier x transport (already a view, just select it)
SELECT * FROM extraction_spend;
```

## 3. `[FILLED — needs review]` interpretive choices

1. **K1 has no single number.** `item_mirror.md_flag` is tri-state: `TRUE` (RAZRED *), `FALSE` (NI MP, dropped before the mirror), `NULL` (61% of the LJ export, unclassified). `Ingest.process_md_unknown` (default `True`) sends `NULL` rows through the pipeline alongside `TRUE` ones. A single coverage % would have to silently pick a denominator — the board reports both **strict** (`md_flag IS TRUE`) and **processed-scope** (`md_flag IS NOT FALSE`) lines instead. Needs Denis sign-off (mirrors the open G4/AC1 denominator question).
2. **K2 will never show `name-family` or `fetch-context`.** The `item_document_trusted_basis_ck` CHECK (migration 005) forbids `status='production'` for those two bases — that is invariant 3 working correctly, not a bug or a data gap. The spec notes this so the zero isn't misread as "the board is broken." Those bases are visible instead in K4's staged-links sub-count.
4. **K1's device line, added 2026-08-21.** `item_document_production` counts any production document, and until this line existed nothing distinguished "we hold paper for this article" from "this article's device conformity is evidenced". An ISO 13485 certificate parts them: it is real, current, production paper, and it certifies the manufacturer's quality system rather than any article's conformity. The client ruling of 2026-08-18 already said non-MDR documents "bind to the manufacturer, exclude from device coverage" — the first half was implemented (C16 mfr-scope binding), the second half was not. **CARL MARTIN made it concrete on 2026-08-21**: its backfill bound one QMS certificate to all 2.567 items and the board read 2.567/2.567 = 100% strict, while both of its MDR declarations sat in staging contributing zero. Measured the same day, 2.567 of 4.253 covered items — 60% — had no device document behind them, and all 2.567 were that one manufacturer. Filtered on `regulation`, never on `type`: the ruling is about the regime a document was issued under, and 258 items are covered by documents that are type `ISO` under regulation `MDR`, which do count. `n.a.` is the only non-device value the registry currently holds. **The expiry side was closed the same day**, with the same predicate in the one place that aggregates expiry to an ITEM (`web/app.py`, the `/items` board's `next_expiry`). Document-level expiry is deliberately left alone — `document_effective_expiry`, `report.py`'s scan and the renewal chase all list DOCUMENTS, and an ISO 13485 certificate genuinely expires and should genuinely be chased. Measured over the live catalogue: 2.925 items change, of which **2.570 lose a horizon they never had evidence for** (2.567 CARL MARTIN) and **355 move LATER** (244 KOMET, 111 IVOCLAR) because a QMS certificate was lapsing before their device paper. Nothing moves earlier, so the board was previously both falsely reassuring on one manufacturer and falsely urgent on three.

5. **K1's DECLARED line, added 2026-09-03.** The device line filters on
`regulation`, and an MDR Annex IX quality-system certificate satisfies it — it
is genuinely issued under MDR and says nothing whatever about any article's
conformity. So `device` still counts an item as evidenced on the strength of a
certificate about the manufacturer's processes. A Declaration of Conformity is
the manufacturer's own statement about the DEVICE, and it is the only
production document that answers the question a client is asking. Measured
2026-09-03 over the 4.265 confirmed MD items: **4.254 hold some production
paper (99,7%)**, **1.684 hold paper under a device regulation (39,5%)**,
**1.461 hold a declaration of any age (34,3%)** and **489 hold an UNEXPIRED one
(11,5%)**. The gap between the last two is the 972-device expired-declaration
pile that `[completeness-card]` tracks; lapsed evidence does not count as
coverage (Denis, 2026-08-24), so **11,5% is the honest figure and 99,7% was the
headline until this line existed**. PHASES.md quoted 34,3% because it ignored
expiry. This is the number to put in front of Dentalia; the 2026-08-31 delivery
report had to quote article-level coverage and explain why, which was the
workaround for not having it.
5. **K8 needs an FX rate that does not exist anywhere in the codebase.** `extraction_cost.cost_usd` is USD; `budget.sweep_cap_eur` / `budget.monthly_cap_eur` are EUR. Added `Budget.eur_per_usd: float = 0.92` (`app/config.py`, env `BUDGET_EUR_PER_USD`) — an approximate, manually-updated conversion factor, not a live rate. The board renders the rate next to the converted figure so a stale rate is visible rather than silently wrong. `unpriced_calls` is surfaced as-is (never-silent rule) — no threshold logic, a human reads the count.
6. **The board's headline is not a K1 line, since 2026-09-11 (office UI
redesign D4).** The status board leads with one sentence, "Declarations on
file for X of Y medical-device items (Z%)", computed by
`web.app.coverage_headline`. It counts AC1, the client acceptance metric
recorded in `docs/decisions.md` the same day: `md_flag IS TRUE` items holding a
production DoC linked at article level (`ref-list`, `ref-item`,
`basic-udi-di`, `map-supplier`), with **no expiry filter** (the ruling states
the unexpired share separately). Dev reading 2026-09-11: 1,460 of 4,265
(34.2%), replacing the Strict tile's 4,254 of 4,265 (99.7%). One predicate,
`_HAS_DECLARATION`, also selects Coverage gaps' "No Declaration of Conformity"
list, so `total - covered` is that list's count by construction (2,805; the
list read 2,804 under its old any-basis DoC predicate, the difference being one
item linked only by `manual`). The four K1 lines above stay in the Coverage
tab, each under its own name.

## 4. Rendering surface

`web/app.py` gets one `_kpi_*(conn)` function per metric (K1–K8), each returning plain dicts/lists — no ORM, no ad hoc joins outside §2's queries. `GET /status` renders them in a new board section on `status.html`, above the existing job-status cards, reusing the `.stat-row`/`.stat-tile` CSS already in `web/static/css/style.css`. `GET /api/kpi` (S1.6 read API) returns the same functions' output as JSON — one implementation, two renderings, per the "don't duplicate logic" default.

Every function must render correctly against an **empty** database (no division-by-zero, no crash) — Phase 1 start state, and the first thing a fresh `docker compose up` sees.

## 5. Table-driven test cases (`tests/test_web.py`, real Postgres)

Seeded via `tests/fixtures/seed_ui.py`, extended with a production link, an `md_flag IS NULL` item, and `extraction_cost` rows (including one with `cost_usd IS NULL`).

| # | Input state | Expected board output |
|---|---|---|
| 1 | one item with a production doc + production link, one without | K1 strict and processed-scope both show 1/N covered |
| 2 | one item with `md_flag IS NULL` | processed-scope denominator includes it; strict does not |
| 3 | a production link with `match_basis='ref-list'`, no name-family/fetch-context production rows | K2 shows `ref-list`; no entry for name-family/fetch-context (structural, not a gap) |
| 4 | one item with `mfr_ref IS NULL` | K3 missing count includes it |
| 5 | one staged document, one staged link on a production doc, one open grouping suggestion | K4 reports three separate counts; staged-doc and suggestion report an oldest age, the link count does not |
| 6 | two open `manual_task` rows of different kinds | K5 groups by kind |
| 7 | one dead job | K6 = 1 |
| 8 | jobs in `pending`/`done`/`dead` across two types | K7 matches the exact (type, status) breakdown |
| 9 | `extraction_cost` rows incl. one `cost_usd IS NULL` | K8 sums usd, converts at `eur_per_usd`, surfaces `unpriced_calls=1` |
| 10 | empty database (no seed at all) | `GET /status` returns 200, every KPI renders a zero/empty state, no exception |
