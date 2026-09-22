-- The device's name, which is not in the field called `deviceName`.
--
-- `eudamed_mirror.device_name` has been fed from the API's `deviceName` since
-- migration 004, and that field comes back NULL. Measured 2026-08-25 on two
-- unrelated groups: Homecare Enterprise's `471070188CNQQ` (47 devices) and
-- Ivoclar's `76152082APROS001VT` (74 devices) -- `deviceName` null on 121 of
-- 121, `tradeName` populated on 74 of 74 for Ivoclar
-- ("SR Triplex Cold Standard Kit pink-V"). So the document page rendered a
-- column of em-dashes while the name sat one key away.
--
-- Stored beside `device_name` rather than into it: the mirror is a mirror, and
-- collapsing two upstream fields into one column loses which one answered.
-- The UI coalesces.
ALTER TABLE eudamed_mirror ADD COLUMN IF NOT EXISTS trade_name text;

COMMENT ON COLUMN eudamed_mirror.trade_name IS
  'EUDAMED "Trade name" for this UDI-DI. In practice the only populated name '
  'field on the public device endpoint -- deviceName was null on every record '
  'measured 2026-08-25. Display only: never evidence, never a link basis.';

GRANT SELECT ON eudamed_mirror TO dentalia_api;
