-- Manufacturer identity for the EUDAMED enhancement path
-- (docs/superpowers/specs/2026-08-25-eudamed-enhancement-design.md §3).
--
-- `manufacturer` was created by 006 as a declared-not-built Phase 2 table and
-- nothing has ever written it: 0 rows, re-verified 2026-08-26. This is its
-- first writer. It is shared infrastructure, not an EUDAMED detail -- 006's own
-- comment says email.request reads it for contacts, and that is why
-- email_request._contacts() returns nothing today.
--
-- Seeding it is NOT a choice this migration makes. Denis, 2026-08-20
-- (tasks/followups.md [manufacturer-contacts-empty]): "Two jobs: seed
-- `manufacturer` identity from the playbooks + `manufacturer_alias`, and ask
-- the client for the regulatory contact per supplier." This is the first job.
-- All 384 canonical names, not only the nine with device articles (Denis,
-- 2026-08-26): probing every supplier cross-checks our own medical-device flag.

INSERT INTO manufacturer (canonical_name)
SELECT DISTINCT canonical_name FROM manufacturer_alias
ON CONFLICT (canonical_name) DO NOTHING;

-- Without this, "probed, no SRN exists" and "never probed" are the same null,
-- and the article-probe fallback re-runs against every unregistered supplier
-- forever. ~375 of the 384 are not device manufacturers and will never resolve.
ALTER TABLE manufacturer ADD COLUMN IF NOT EXISTS srn_probed_at timestamptz;

COMMENT ON COLUMN manufacturer.srn_probed_at IS
  'When the article-probe SRN fallback last ran for this manufacturer. NULL '
  'means never probed; a timestamp with no manufacturer_srn row means probed '
  'and nothing found. Do not conflate the two.';

-- One canonical name, many EUDAMED entities. `manufacturer_alias` maps BC
-- codes 001 AND 005 both to IVOCLAR, so our canonical name is a Dentalia-side
-- grouping and a multinational registers per legal entity. Storing one SRN and
-- sweeping it would report articles as unregistered when a sibling entity
-- holds them, and that false alarm becomes a wrong e-mail to a supplier.
--
-- `manufacturer.eudamed_srn` is deliberately left alone -- not repurposed, not
-- dropped. A column no code has ever written is not evidence of an intent.
CREATE TABLE manufacturer_srn (
  canonical_name text NOT NULL REFERENCES manufacturer(canonical_name),
  srn            text NOT NULL,
  actor_name     text,
  -- How this SRN was found. 'register-exact' and 'register-fuzzy' come from
  -- the whole-register pull; 'article-probe' from the device fallback.
  discovered_via text NOT NULL
    CHECK (discovered_via IN ('register-exact','register-fuzzy','article-probe')),
  -- Only 'auto' and 'confirmed' are swept. Attribution is a fuzzy name match
  -- and a wrong one produces a wrong e-mail to a supplier, so anything short
  -- of a normalised exact match waits for a person (spec §4.5).
  status         text NOT NULL DEFAULT 'pending'
    CHECK (status IN ('auto','pending','confirmed','rejected')),
  match_score    real,
  -- The article number, when discovered_via='article-probe'. A wrong bootstrap
  -- is then auditable rather than mysterious (spec §5.2).
  probe_ref      text,
  first_seen     timestamptz NOT NULL DEFAULT now(),
  decided_at     timestamptz,
  decided_by     text,
  PRIMARY KEY (canonical_name, srn)
);

CREATE INDEX manufacturer_srn_status_idx ON manufacturer_srn (status);

-- `eudamed_mirror` is device-shaped and cannot hold these. Keyed on
-- (number, revision, actor): EUDAMED serves each revision as its own record,
-- and that key was tested against all 4.608 register rows on 2026-08-26 with
-- zero collisions. (actor, number, versionNumber) collides 50 times -- do not
-- use it.
CREATE TABLE eudamed_certificate (
  certificate_number text NOT NULL,
  revision_number    text NOT NULL DEFAULT '',
  actor_srn          text NOT NULL,
  actor_name         text,
  certificate_type   text,
  -- Nine values, measured 2026-08-26: issued 2770, supplemented 1007,
  -- amended 422, reissued 197, withdrawn 87, cancelled 68, restricted 31,
  -- suspended 18, reinstated 8. The adverse four drive certificate_status_alert.
  certificate_status text,
  issue_date         date,
  starting_validity  date,
  expiry_date        date,
  notified_body_srn  text,
  version_number     integer,
  -- NEVER touched by ON CONFLICT. A new revision is a new row, so a preserved
  -- first_seen is what makes "a renewal appeared" detectable at all.
  first_seen         timestamptz NOT NULL DEFAULT now(),
  synced_at          timestamptz NOT NULL,
  PRIMARY KEY (certificate_number, revision_number, actor_srn)
);

CREATE INDEX eudamed_certificate_actor_idx ON eudamed_certificate (actor_srn);

GRANT SELECT ON manufacturer, manufacturer_srn, eudamed_certificate
  TO dentalia_api;
-- The confirm queue is a producer decision, not a registry write: the web
-- process may resolve an SRN candidate. It still holds zero write grants on
-- document / item_document / evidence.
GRANT INSERT, UPDATE ON manufacturer_srn TO dentalia_api;
