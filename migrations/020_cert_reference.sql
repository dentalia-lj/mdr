-- The notified-body certificate a Declaration of Conformity cites.
--
-- Distinct from referenced_doc_id, which holds the C6 cross-regulation sibling
-- (the MDR and MDD versions of one document, parallel chains, never a
-- supersession). Conflating them would lose one of the two relationships.
--
-- Why it matters: MDR Annex IV requires only a date of ISSUE on a DoC, so most
-- carry no expiry. Article 56 caps notified-body certificates at five years.
-- A DoC therefore inherits its renewal date from the certificate named here.
-- Null is normal and never an error: Class I devices have no certificate at
-- all, and we may simply not hold the cited one yet.
ALTER TABLE document ADD COLUMN cert_doc_id bigint REFERENCES document(doc_id);

CREATE INDEX document_cert_doc_idx ON document (cert_doc_id) WHERE cert_doc_id IS NOT NULL;
CREATE INDEX document_cert_number_idx ON document (cert_number) WHERE cert_number IS NOT NULL;
