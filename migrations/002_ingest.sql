-- 002_ingest.sql
-- Sketch §2 (ingest side).
-- Encodes: C1 mfr_ref as an explicit logical field on the item mirror and on
--          group membership; the REF-gate comparand is the manufacturer-scoped
--          view item_group_refs (bare article numbers collide across makers).
-- FK-references 001 only via nothing; self-contained ingest tables.

-- written by: ingest.run · read by: resolve.group
CREATE TABLE item_mirror (
  item_ref         text PRIMARY KEY,        -- BC item ID
  name             text NOT NULL,
  manufacturer_raw text NOT NULL,
  mfr_ref          text,                    -- C1: manufacturer article number — the REF-gate key.
                                            -- LOGICAL field: adapter maps it from whichever BC
                                            -- column holds it (item No. | vendor item no. | mixed).
                                            -- Nullable; null counted in missing_mfr_ref metric,
                                            -- never silently skipped.
  md_flag          boolean,                 -- assumption: present in BC
  product_class    text,                    -- I / IIa / IIb / III — BC = source of truth
  udi              text,                    -- UDI-DI when present
  catalogue        text NOT NULL,           -- 'LJ'; one BC, no separate ZG system
  mirror_rev       bigint NOT NULL,         -- bumps on change -> triggers resolve.group
  updated_at       timestamptz NOT NULL
);

-- written by: resolve.group
CREATE TABLE manufacturer_alias (
  raw_name       text PRIMARY KEY,
  canonical_name text NOT NULL
);

-- written by: resolve.group · read by: discover.group, validate.doc (REF gate)
CREATE TABLE item_group (
  group_id               bigserial PRIMARY KEY,
  canonical_manufacturer text NOT NULL,
  basic_udi_di           text,              -- when known — strongest group key
  label                  text
);

CREATE TABLE item_group_member (
  group_id    bigint REFERENCES item_group,
  item_ref    text   REFERENCES item_mirror,
  mfr_ref     text,                         -- C1: materialized here at RESOLVE time —
                                            -- group.member_mfr_refs is the REF-gate comparand
  match_basis text NOT NULL,                -- existing-link|udi|basic-udi-di|name-family|manual
  PRIMARY KEY (group_id, item_ref)
);

-- C1: the REF-gate comparand, always scoped by manufacturer —
-- bare article numbers collide across manufacturers.
CREATE VIEW item_group_refs AS
  SELECT g.group_id, g.canonical_manufacturer,
         array_agg(m.mfr_ref) FILTER (WHERE m.mfr_ref IS NOT NULL) AS member_mfr_refs
  FROM item_group g JOIN item_group_member m USING (group_id)
  GROUP BY g.group_id, g.canonical_manufacturer;
