-- `ref-item`: a scoped REF match made against the catalogue's OWN item number
-- (`item_mirror.item_ref` / `item_group_member.item_ref`) rather than against
-- the supplier-article column (`mfr_ref`).
--
-- Client ruling (Nataša Palme, C12, restated 2026-08-13): the supplier article
-- number "ni tako pomembna" — the primary item number is the key, and it is the
-- number printed on the physical article. So the REF-gate comparand is the
-- catalogue's article number for an item, whichever column holds it.
--
-- Why this basis reaches `production` while `ref-catalogue` cannot:
--
-- The two are not the same match. `ref-catalogue` is UNSCOPED — no manufacturer
-- is known, so the manufacturer is INFERRED from the number, and
-- catalogue-observed uniqueness is not global uniqueness. `ref-item` is SCOPED:
-- the manufacturer is established independently (inherited from the requesting
-- group, or extracted and canonicalized through `manufacturer_alias`) BEFORE any
-- number is compared. That is the same pair guarantee `ref-list` rests on, with
-- the same scoping doing the same work.
--
-- Measured 2026-08-12 over the live registry (`tools/match_key_analysis.py`):
-- `item_ref` collides across manufacturers 0 times against `mfr_ref`'s 34, and
-- that zero is structural rather than lucky — `item_ref` is `item_mirror`'s
-- PRIMARY KEY, so one value is one item is one manufacturer. It carries no prose
-- values against `mfr_ref`'s 64, and its reach is a strict SUPERSET (`mfr_only`
-- was 0 over 1.502 distinct extracted REFs). By every measure that made
-- `mfr_ref` trustworthy enough to write, `item_ref` is at least as strong.
--
-- It gets its OWN basis rather than being folded into `ref-list` so the registry
-- records which number matched. A link that says `ref-list` must keep meaning
-- "the supplier's article number matched"; laundering a Dentalia item number
-- through that name would make the audit trail claim something untrue.
--
-- The CHECK is deliberately UNCHANGED: `ref-item` is not added to the bar list.
-- The unscoped path still rewrites every basis it forms to `ref-catalogue`
-- (app/handlers/validate.py), so an item_ref match with no manufacturer stays
-- capped exactly as before.

COMMENT ON COLUMN item_document.match_basis IS
  'How this item was identified as covered by this document. Vocabulary: '
  'ref-list (supplier article number matched, scoped) | ref-item (catalogue '
  'item number matched, scoped — 023) | udi | basic-udi-di | mfr-scope | '
  'manual | name-family | fetch-context | ref-catalogue (unscoped REF — 021). '
  'The last three are barred from production by item_document_trusted_basis_ck.';

COMMENT ON CONSTRAINT item_document_trusted_basis_ck ON item_document IS
  'C5/inv. 3: link-level cap. name-family, fetch-context and ref-catalogue are '
  'never production — each identifies an item without a verified '
  '(canonical_manufacturer, catalogue article number) pair or Basic UDI-DI. '
  'ref-list and ref-item both carry that verified pair and are unaffected.';
