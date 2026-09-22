-- 070: the supersession chain, readable by an item-scoped consumer.
--
-- `item_document_production` answers "what covers this item NOW" and is what
-- every consumer sees by default. This view answers the second question a
-- compliance consumer eventually has -- "what covered it BEFORE, and what
-- replaced it" -- and is reached only through `?include_superseded=true`.
--
-- A SEPARATE view rather than a widening of 034, deliberately: `web/registry.py`,
-- `web/catalogue.py`, `docs/specs/kpi.md`'s board and `046_eudamed_gap_view` all
-- read `item_document_production`, and relaxing its status filter would change
-- what all four mean without a single call site being edited.
--
-- The DOCUMENT filter widens to production+superseded and stops there. `staged`
-- is ungated machine output, `rejected` and `filed` are a decision that this is
-- not coverage -- none of the three is a consumer's to see (CLAUDE.md invariant,
-- "visibility rule for ALL consumers"). The LINK filter does not move at all:
-- a non-production link is a human saying this document does not cover this
-- item, and no flag may override that.
--
-- Nothing extra is needed to make superseded rows reachable. Supersession flips
-- `document.status` and sets `superseded_by`; it never touches `item_document`
-- (app/handlers/gate.py::_supersede), so the old document's link is still
-- `production` and the row drops out of 034 purely on `d.status`.
CREATE OR REPLACE VIEW item_document_history AS
  SELECT id.*, d.type, d.regulation, d.validity_from, d.validity_to, d.archive_url,
         d.status         AS doc_status,
         d.superseded_by  AS superseded_by,
         ee.expires       AS expires,
         ee.basis         AS expiry_basis
    FROM item_document id
    JOIN document d USING (doc_id)
    LEFT JOIN document_effective_expiry ee ON ee.doc_id = d.doc_id
   WHERE d.status IN ('production', 'superseded') AND id.status = 'production';

GRANT SELECT ON item_document_history TO dentalia_api;
