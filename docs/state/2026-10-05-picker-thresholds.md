# Review item picker thresholds, measured on the server (2026-10-05)

Read-only, `tools/picker_thresholds.sql` plus one follow-up query (punctuation
removed before matching), against the server's registry (91.98.42.140).
"Intended" = the item's name contains the word the query means; "other" =
every other item of the same manufacturer that clears the threshold.

## Search: `word_similarity(query, name)`

| Query (manufacturer, items) | Intended | t = 0.4 | t = 0.5 | t = 0.6 |
|---|---|---|---|---|
| `varibase` (Straumann, 914) | 54 Variobase | 54 / 0 other | 54 / 0 | 0 / 0 |
| `bone level` (Straumann, 914) | 4 | 4 / 10 | 4 / 2 | 4 / 0 |
| `emax` (Ivoclar, 1.320) | 431 E.MAX | 431 / 50 | **0** / 0 | 0 / 0 |
| `emax`, punctuation ignored | 431 E.MAX | 431 / 50 | **431 / 0** | 431 / 0 |

"E.MAX" is split by its dot, so `emax` scores exactly 0.40 against it, the
same as the 50 Empress items it is not. Removing punctuation from the name
before matching (`greatest(word_similarity(q, name), word_similarity(q,
regexp_replace(name, '[^[:alnum:][:space:]]', '', 'g')))`) leaves `varibase`
and `bone level` unchanged and finds every E.MAX item at 0.5 with nothing else.
At 0.5 the only other items are two `bone level` hits ("ABUT. LEVEL",
"IMPL. LEVEL", 0.55).

## Similar: `similarity(name, example name)`

Example `010.6042` NC VARIOBASE FOR CROWN AS; 53 other Variobase items.

| t | Variobase listed | other listed |
|---|---|---|
| 0.2 | 49 | 16 |
| 0.3 | 32 | 8 |
| 0.4 | 17 | 1 |
| 0.5 | 6 | 0 |
| 0.6 | 2 | 0 |

The 15 closest are all Variobase crown items (0.79 down to 0.45). No
threshold lists all 53: the bridge, bar and coping items are named less like
a crown base. Finding every Variobase item is the search's job; Similar lists
an item's nearest relatives.

## Nearby item numbers

The items just before and after an item in the manufacturer's item-number
order (`ORDER BY item_ref`), for each member of a family: the share of those
neighbours in the same family, and how many family members one example reaches.

| Family | ±3 | ±5 | ±10 | ±20 |
|---|---|---|---|---|
| E.MAX (Ivoclar, 430 others) | 91%, 5.5 found | 88%, 8.8 | 83%, 16.5 | 75%, 29.7 |
| Variobase (Straumann, 53 others) | 64%, 3.9 | 59%, 5.9 | 50%, 10.1 | 34%, 13.7 |
| bone level (Straumann, 3 others) | 0% | 0% | 0% | 0% |

## Ruled the same day (Denis)

Names: 0.5, punctuation ignored. Similar: 0.2. Nearby numbers: ±10, listed
below the number matches, never ticked.
