"""Measured QMS/QA-certificate text shapes, verbatim from the live registry's
`document_text` rows (2026-08-25 survey for the device-enumeration guard).

These are the boundary cases the guard was designed from and must keep
holding. Each constant is the load-bearing excerpt of one real stored text,
identified by its live doc_id at survey time:

* ``KIWA_ENUMERATING``  — doc 310, Kiwa Cermet EC Quality Assurance System
  certificate MED 31385 (93/42/EEC Annex V). Its technical annex enumerates
  EXACTLY three device types with model codes, per-device risk-class lines,
  and page 3 restricts validity to "the above mentioned Medical Devices".
  MUST fire the detector: C16 machine-bound this to 356 GC items while its
  own text says it covers three devices.
* ``CARL_MARTIN_SCOPE_ONLY`` — doc 816, TÜV Rheinland ISO 13485 for Carl
  Martin GmbH. A scope paragraph, no device enumeration — the genuinely
  manufacturer-wide shape. MUST NOT fire (2.567 mfr-scope links are correct).
* ``GC_DEVICE_SCHEDULE`` — doc 138, BSI MDR Annex XI Part A QA certificate
  for GC Europe. Carries a "Device Schedule" naming device FAMILIES
  ("Implantable dental materials") with one Risk Classification header and
  no model codes. MUST NOT fire — a family-level schedule is how a
  line-wide QA certificate legitimately prints its scope.
* ``KOMET_EMDN_PRODUCTS`` — doc 420, TÜV Rheinland MDR Annex IX QMS
  certificate for Gebr. Brasseler (Komet). Lists EMDN CATEGORY codes
  ("Q010501 DENTAL BURS..."), which cover whole device families, not
  models. MUST NOT fire (258 mfr-scope links are correct).
"""

# doc 310, pages 2-3 (technical annex + restriction clause), verbatim.
KIWA_ENUMERATING = """\
[[page 2]]
MED 31385
Reg. Numero /
Reg. Number
Identificazione dei Dispositivi Medici/ Identification of Medical Devices:
Allegato tecnico al Certificato/
Technical sheet enclosed to the Certificate
Tipologia / Medical Devices:
Dispositivo per la verifica della forza occlusale dentale / Device for dental occlusal force test
Classe di rischio / Risk class:
I m - Limitatamente agli aspetti relativi ai requisiti metrologici / restricted to the aspects concerned the
metrological requirements
Codice NANDO / NANDO codes:
MD 1111
Dental Prescale II
Modello / Model:
Tipologia / Medical Devices:
Materiale odontoiatrico per la realizzazione di ponti e corone temporanei stampabile con tecnologia 3D / 3D
Printable light curing composite for temporary crown and bridge
Classe di rischio / Risk class:
II a
Codice NANDO / NANDO codes:
MD 0402
GC TEMP PRINT
Modello / Model:
901595/10004798, 901596/10004797
Codici / Codes:
Tipologia / Medical Devices:
Vernice odontoiatrica ad applicazione topica per il trattamento dell'ipersensibilità / Dental varnish topical
application for the treatment of hypersensitivity
Classe di rischio / Risk class:
II a
Codice NANDO / NANDO codes:
MD 0402
MI Varnish
Modello / Model:
900746/10003389, 900747/10003390, 900748/10003391, 900749/10003392, 900750/10003393, 901460/10003xxx
Codici / Codes:

[[page 3]]
MED 31385
La lista completa dei codici, relativi ai modelli certificati, è disponibile presso Kiwa Cermet Italia./ The complete list of the codes
related to the certificated models is available at Kiwa Cermet Italia. Il presente Certificato è soggetto al rispetto dei requisiti
contrattuali di Kiwa Cermet Italia ed è valido solo per le tipologie di dispositivi sopra identificate soggette a sorveglianza/ This
Certificate is subject to Kiwa Cermet Italia regulations and it is valid only for the above mentioned Medical Devices that are subject to
survey. L'allegato tecnico è parte integrante del presente Certificato./ The technical sheet is an integrating part of this Certificate.
"""

# doc 310, page 3 only — the restriction clause with the annex pages lost
# (e.g. rasterized annex, text layer only on the closing page). The clause
# alone asserts the limitation, so it must still fire.
KIWA_RESTRICTION_ONLY = """\
[[page 1]]
MED 31385
This Certificate is subject to Kiwa Cermet Italia regulations and it is valid only for the above mentioned Medical Devices that are subject to
survey.
"""

# doc 816, verbatim (whole certificate).
CARL_MARTIN_SCOPE_ONLY = """\
[[page 1]]
Certificate
Quality Management System
EN ISO 13485:2016
Registration No.:
SX 1594091-1
Certificate Holder:
Carl Martin GmbH
Neuenkamper Str. 80-86
42657 Solingen
Germany
The Certification Body of TÜV Rheinland LGA Products GmbH certifies that the organization has established and applies
a quality management system for medical devices.
Proof has been furnished that the requirements specified in the abovementioned standard are fulfilled. The quality
management system is subject to yearly surveillance.
Report No.:
1183809-100
Effective date:
2025-06-07
Expiry date:
2028-06-06
Issue date:
2025-06-05
Replaces certificate SX 1594091-1 issued 2022-06-01
Scope:
Design and development, production, final inspection and
distribution of surgical instruments and dental instruments
"""

# doc 138, page 2 (Device Schedule), verbatim.
GC_DEVICE_SCHEDULE = """\
[[page 2]]
EU Quality Assurance Certificate
Regulation (EU) 2017/745, Annex XI Part A
MDR 778483 R000
First Issue Date: 2025-04-17
Starting Validity Date: 2026-02-06
Current Issue Date: 2026-02-06
Expiry Date: 2030-04-16
Device Schedule: Class IIa, Custom-made and other devices
Device(s)
Risk Classification
Implantable dental materials
Class IIa, implantable
Non-implantable dental materials
Class IIa
"""

# doc 420, page 1 (EMDN category list), verbatim.
KOMET_EMDN_PRODUCTS = """\
[[page 1]]
EU Certificate
Quality Management System
REGULATION (EU) 2017/745 on Medical Devices
Annex IX Chapters I and III
Registration No.:
HZ 1470094-1
Manufacturer:
Gebr. Brasseler GmbH & Co. KG
Products:
Products of class IIa:
L090999 ORTHOPAEDIC SURGERY CUTTING
INSTRUMENTS, REUSABLE - OTHER
L159004 ENDODONTIC RASPS AND FILES, REUSABLE
Q010199 CONSERVATIVE DENTISTRY AND
ENDODONTICS DEVICES - OTHER
Q010501 DENTAL BURS AND ABRASIVE DISKS, SINGLE-
USE
Q010507 ENDODONTIC INSTRUMENTS (CANAL
ENLARGERS, FILES, RASPS, ETC.), SINGLE-USE
V0199 CUTTING DEVICES, SINGLE-USE - OTHER
Q010399 SURGICAL DENTAL DEVICES – OTHER
P091305 BONE SAWS, SINGLE-USE
Q010102 ROOT CANAL FILLING DEVICES
"""
