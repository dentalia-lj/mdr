-- Which authored playbook shaped this extraction, and at what revision.
--
-- `model_id` already records WHICH MODEL read the document. From S1.7 onward a
-- playbook also shapes the read: its lexicons steer T0's date/cert/REF
-- extractors, and its `extract_hints` are appended to the T1/T2 system prompt.
-- Evidence that does not name the rules is not reproducible, and invariant 2
-- requires that a production value can be defended later.
--
-- `extract_rev` does NOT cover this: it is a per-content_hash attempt counter,
-- so it changes when a document is re-extracted for any reason and says nothing
-- about what the rules were at the time.
--
-- Nullable on purpose: most documents are claimed by no playbook, and NULL is
-- the honest answer for them. rev 0 means "claimed, but the file carries no
-- revision stamp" -- distinct from "no playbook at all".
ALTER TABLE extraction_attempt
  ADD COLUMN playbook_slug text,
  ADD COLUMN playbook_rev  int;
