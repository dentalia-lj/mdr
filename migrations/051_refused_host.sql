-- 051_refused_host.sql
-- `playbooks/robots_refused.txt` becomes a table (spec:
-- docs/superpowers/specs/2026-08-26-playbooks-into-the-database-design.md §8,
-- slice 4).
--
-- WHY IT HAS TO MOVE WITH THE DIRECTORY
--
-- The list is what `playbooks.validate()` checks a `kind:"direct"` source
-- against, and since slice 3b a non-dev authors direct sources in the UI. The
-- web process reads the list only because `docker-compose.yml` bind-mounts
-- `./playbooks` into it; drop that mount and `load_robots_refused` finds no
-- file, returns an empty set BY DESIGN (absence must not raise -- it is on
-- DISCOVER's never-raises path), and the guard goes quietly inert. A guard
-- that fails open when a mount is removed is worse than no guard, because
-- nothing in the UI would say so.
--
-- WHAT THIS LIST IS NOT
--
-- It is not what a host's robots.txt says. `app/robots.py` reads that at fetch
-- time and obeys it. This records an OPERATOR DECISION that does not depend on
-- the file, and COLTENE is why both exist: its robots.txt refuses `ClaudeBot`
-- and `CloudflareBrowserRenderingCrawler` by name and says nothing about
-- `DentaliaComplianceBot`, so a runtime check reads it as permitted. The
-- refusal there is Denis's (2026-08-20), and only a list can carry it.
--
-- Removing a row means asking the manufacturer and getting a yes. Record that
-- in the playbook's note, with a date, before deleting anything here.
--
-- The `note` column is not decoration: every line of the file carried one, and
-- they are what makes a refusal auditable a year later ("Disallow: / for every
-- agent except Twitterbot"). Dropping them in the move would lose the only
-- record of WHY each host is listed.

CREATE TABLE refused_host (
  host     text PRIMARY KEY,
  note     text,
  added_at timestamptz NOT NULL DEFAULT now()
);

-- The five hosts of the 2026-08-20 ruling: 4 manufacturers, 822 catalogue
-- items, copied from `playbooks/robots_refused.txt` verbatim including the
-- reason each was listed. The file stays in git as the authoring record, the
-- same posture the 33 playbook JSONs keep.
INSERT INTO refused_host (host, note) VALUES
  ('kavo.widen.net',
   'Disallow: / for every agent except Twitterbot (KAVO DENTAL, 331 items)'),
  ('media.amanngirrbach.com',
   'User-agent: * / Disallow: / -- the entire DAM (AMANN GIRRBACH, 196 items)'),
  ('pritidenta.com',
   'Disallow: /*.pdf, .xlsx, .doc, .docx -- every document (PRITIDENTA, 193 items)'),
  ('coltene.com',
   'ClaudeBot AND CloudflareBrowserRenderingCrawler refused: rendering barred too (COLTENE, 102 items)'),
  ('media.coltene.com',
   'same Cloudflare-managed refusal as the brand host');

-- SELECT only. The web must be able to REFUSE a save for this reason; it must
-- not be able to lift a refusal, which is an operator ruling taken after
-- asking a manufacturer. Same posture as `vendor_master` (016).
GRANT SELECT ON refused_host TO dentalia_api;
