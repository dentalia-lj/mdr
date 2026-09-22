-- 015_supersession.sql
-- [gate-c6] ruling (Denis, 2026-07-31): VALIDATE has always computed `supersedes`
-- on every candidate it emits (staged or production), but `gate.candidate` never
-- persisted it — `superseded_by` had zero writers repo-wide, so C6 was computed
-- and immediately dropped. Persistence lands "at production transition": the
-- machine path (a candidate landing directly on production) can act the moment
-- gate.candidate runs; the human path (gate.apply approve, promoting an earlier
-- STAGED candidate) needs the supersession target to survive the staging
-- period, since a staged doc must never actually supersede anything (PRD §7:
-- "staged entities invisible to all consumers"). `document.supersedes` carries
-- it across that gap — written (sticky) by every gate.candidate call regardless
-- of disposition, read and applied by both the gate.candidate production path
-- and gate.apply's approve path.

ALTER TABLE document ADD COLUMN supersedes bigint REFERENCES document;
