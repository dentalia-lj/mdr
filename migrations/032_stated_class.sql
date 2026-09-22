-- 032: document.stated_class — the MDR risk class the document itself states.
--
-- QA/display only. It is never scored (absent from `gate.REQUIRED_FIELDS`,
-- which is exactly the tuple `_score` min()s over), never gated (in no VALIDATE
-- rule and no flag), never escalated (in no `_ESCALATE_*` set, so it can never
-- buy an LLM call), and never written to `item_mirror.product_class`.
--
-- BC remains the source of truth for what class an ITEM is; contract PRD §5,
-- 2026-08-21. This column is the other side of a comparison — the
-- `item_class_check` view (033) puts the two beside each other so a blank BC
-- class can be filled by a human and a contradicted one can be questioned.
-- Nothing downstream of that view writes anything.
--
-- Nullable with no backfill, and the backfill that does exist deliberately does
-- not touch this column: `app/repair_stated_class.py` appends a new
-- `extract_rev` to `extraction_attempt` and emits no `validate.doc`, because
-- re-adjudicating 768 settled dispositions to record a display value would be
-- a far larger risk than the value is worth. The 033 view therefore COALESCEs
-- this column over the latest attempt's jsonb, and historic rows are compared
-- without ever being re-decided. A document re-extracted for any ordinary
-- reason fills the column on its next GATE write.
--
-- The CHECK is the Annex VIII vocabulary in full. `Is`/`Im`/`Ir` are the class I
-- subclasses (sterile / measuring / reusable surgical); documents rarely print
-- them (10 of 768 measured 2026-08-21), which is why the view treats BC `Ir`
-- against a document's `I` as agreement rather than conflict.
ALTER TABLE document ADD COLUMN stated_class text
  CHECK (stated_class IN ('I','Is','Im','Ir','IIa','IIb','III'));

COMMENT ON COLUMN document.stated_class IS
  'The MDR risk class this document states about itself (Annex VIII). QA and '
  'display only: never scored, never gated, never escalated, and never written '
  'to item_mirror.product_class -- BC is the source of truth for item class. '
  'NULL means the document states no class, or states several and abstained.';
