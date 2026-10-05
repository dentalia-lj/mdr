-- Read-only. The Review item picker's two thresholds (spec 2026-10-02 §4.4).
-- For each (manufacturer, query, word the intended items carry), how many of
-- the manufacturer's items clear each threshold, split into intended items
-- (name contains the word) and noise. Creates nothing, writes nothing.

\echo '== 1. Search: word_similarity(query, name) per threshold'
WITH cases(mfr_like, q, word) AS (
  VALUES ('%straumann%', 'varibase',   'variobase'),
         ('%straumann%', 'bone level', 'bone level'),
         ('%ivoclar%',   'emax',       'e.max')
),
codes AS (
  SELECT c.mfr_like, a.raw_name AS code
    FROM cases c JOIN manufacturer_alias a ON a.canonical_name ILIKE c.mfr_like
  UNION
  SELECT c.mfr_like, m.manufacturer_raw
    FROM cases c JOIN item_mirror m ON m.manufacturer_raw ILIKE c.mfr_like
),
scored AS (
  SELECT DISTINCT c.q, c.word, m.item_ref, m.name,
         word_similarity(c.q, m.name) AS ws,
         m.name ILIKE '%' || c.word || '%' AS intended
    FROM cases c
    JOIN codes k ON k.mfr_like = c.mfr_like
    JOIN item_mirror m ON m.manufacturer_raw = k.code
)
SELECT q, t,
       count(*) FILTER (WHERE intended)                 AS intended_total,
       count(*) FILTER (WHERE intended AND ws >= t)     AS intended_hit,
       count(*) FILTER (WHERE NOT intended AND ws >= t) AS noise_hit,
       count(*)                                         AS mfr_items
  FROM scored, unnest(ARRAY[0.3, 0.4, 0.5, 0.6, 0.7]) AS t
 GROUP BY q, t ORDER BY q, t;

\echo '== 2. Search at 0.5: noise that clears it, and intended items it misses (10 each)'
WITH cases(mfr_like, q, word) AS (
  VALUES ('%straumann%', 'varibase',   'variobase'),
         ('%straumann%', 'bone level', 'bone level'),
         ('%ivoclar%',   'emax',       'e.max')
),
codes AS (
  SELECT c.mfr_like, a.raw_name AS code
    FROM cases c JOIN manufacturer_alias a ON a.canonical_name ILIKE c.mfr_like
  UNION
  SELECT c.mfr_like, m.manufacturer_raw
    FROM cases c JOIN item_mirror m ON m.manufacturer_raw ILIKE c.mfr_like
),
scored AS (
  SELECT DISTINCT c.q, m.item_ref, m.name,
         round(word_similarity(c.q, m.name)::numeric, 2) AS ws,
         m.name ILIKE '%' || c.word || '%' AS intended
    FROM cases c
    JOIN codes k ON k.mfr_like = c.mfr_like
    JOIN item_mirror m ON m.manufacturer_raw = k.code
),
ranked AS (
  SELECT *, CASE WHEN intended AND ws < 0.5 THEN 'missed'
                 WHEN NOT intended AND ws >= 0.5 THEN 'noise' END AS kind,
         row_number() OVER (PARTITION BY q, intended ORDER BY ws DESC, item_ref) AS n
    FROM scored
   WHERE (intended AND ws < 0.5) OR (NOT intended AND ws >= 0.5)
)
SELECT q, kind, ws, item_ref, name FROM ranked WHERE n <= 10 ORDER BY q, kind, ws DESC;

\echo '== 3. Similar: similarity(name, example name), example 010.6042'
WITH ex AS (SELECT name, manufacturer_raw FROM item_mirror WHERE item_ref = '010.6042')
SELECT t,
       count(*) FILTER (WHERE m.name ILIKE '%variobase%')                                     AS intended_total,
       count(*) FILTER (WHERE m.name ILIKE '%variobase%' AND similarity(m.name, ex.name) >= t)     AS intended_hit,
       count(*) FILTER (WHERE m.name NOT ILIKE '%variobase%' AND similarity(m.name, ex.name) >= t) AS noise_hit
  FROM ex JOIN item_mirror m ON m.manufacturer_raw = ex.manufacturer_raw AND m.item_ref <> '010.6042',
       unnest(ARRAY[0.2, 0.3, 0.4, 0.5, 0.6]) AS t
 GROUP BY t ORDER BY t;

\echo '== 4. Similar: the 15 closest names to 010.6042'
WITH ex AS (SELECT name, manufacturer_raw FROM item_mirror WHERE item_ref = '010.6042')
SELECT round(similarity(m.name, ex.name)::numeric, 2) AS sim, m.item_ref, m.name
  FROM ex JOIN item_mirror m ON m.manufacturer_raw = ex.manufacturer_raw AND m.item_ref <> '010.6042'
 ORDER BY sim DESC, m.item_ref LIMIT 15;
