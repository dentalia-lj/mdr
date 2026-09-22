# Open questions for Dentalia — 11 August 2026

Everything below blocks or degrades automatic processing. Numbers are measured against the
Ljubljana catalogue export (15.958 processed items) and the 1.307 PDFs in the SFTP folders,
verified 2026-08-11, not estimated.

Grouped by what the answer changes. Block A and B are the ones that stop documents from
attaching to items at all.

---

## A. Who the manufacturer is

A document is matched to an item by manufacturer name plus article number. Your catalogue,
our registry and the legal name printed on each declaration are three different strings, and
in one case they share no words at all.

1. **Is Komet the same company as Gebr. Brasseler GmbH & Co. KG?**
   Every Komet declaration we read is signed *Gebr. Brasseler GmbH & Co. KG* — 110 of 186 files.
   The word "Komet" never appears as the manufacturer. Nothing automatic can bridge two names
   with no shared word. Until this is confirmed, 186 documents stay unattached to 319 items.

2. **Dentaurum: one manufacturer or three?**
   Your master lists DENTAURUM ORD (396, 77 items), DENTAURUM TEH (397, 64) and
   DENTAURUM IMPLANTS (10026, 29) as three names, so we treat them as three manufacturers.
   Ivoclar is the opposite case — codes 001, 005 and 275 all named IVOCLAR VIVADENT, which we
   merged into one. We would like to apply one rule, not two.

3. **Should we use your name "IVOCLAR VIVADENT" rather than our short "IVOCLAR"?**
   Your master name is closer to what the documents actually say (*Ivoclar Vivadent AG*,
   124 of 174 files) than the name we chose. We would rather follow your naming.

4. **Dentsply is seven of your codes under one parent. How should that be recorded?**
   010 VDW (164 items) · 012 SIRONA (29) · 022 MAILLEFER (74) · 035 DENTSPLY (52) ·
   115 RINN (1) · 313 SCHICK (3) · 10153 ANKYLOS (2) — 325 items.
   Each is its own manufacturer for purchasing, which is correct. We can either give each brand
   its own identity, or record the parent relationship explicitly. What we must not do is merge
   them: a Maillefer certificate must never end up on a Dentsply item. Until this is decided,
   86 Maillefer and Sirona documents go to the manual queue rather than being filed wrongly.

5. **Confirm Dentsply IH Limited is the UK authorised representative, not a manufacturer.**
   66 files, always in the Authorised Representative field. We have excluded it deliberately.
   If we are wrong, those 66 documents are currently unattributed.

6. **Is GC Corporation (Japan) a different manufacturer from GC Europe N.V.?**
   GC Europe N.V. signs 134 of 149 files; GC Corporation appears in 6. If the Japanese parent
   also makes items you stock, it needs its own identity.

7. **CEFLA appears twice in the catalogue** — once as code 10015 (232 items) and once written
   as the literal text "CEFLA" in the manufacturer column instead of a code (363 items).
   One manufacturer under two identities, or genuinely two?

8. **HENRY SCHEIN (code 004) carries 1.114 items but only 3 are classified as devices.**
   Henry Schein is a distributor. Whose declarations should we chase for these items — the
   actual maker's, or Henry Schein's own-brand documents?

9. **Is code 081 CARL MARTIN a manufacturer, or a bucket for Class I instruments?**
   2.567 items — 60% of every confirmed device in the catalogue — all class RAZRED IR,
   generic instrument names (extraction forceps, crown scissors, scaler), and 2.276 of them
   (89%) have no supplier article number. If it is a bucket code, then the majority of your
   confirmed devices have neither a manufacturer to fetch documents from nor a number to
   match on, and coverage expectations change materially.

---

## B. Device classification

10. **11.693 of 15.958 items (73%) have no device class in the export.**
    We process them anyway, on the basis that leaving a real device out is the more expensive
    mistake. But it means most of the catalogue is unconfirmed in either direction. Is the blank
    column "not yet classified" or "not a device"? Who can fill it, and on what timeline?

11. **The sharpest case: the whole Dentsply family has zero confirmed devices.**
    All 325 items are blank or "NI MP". Yet their folder holds 405 PDFs — 31% of the entire
    document corpus — and 147 of those cite MDR or MDD directly. One of the two records is
    wrong. Same for VOCO (0 of 31 items classified) and DENTSPLY 035 (1 of 53).

---

## C. Article numbers — the matching key

12. **7.082 of 15.958 items (44%) have no supplier article number.**
    A declaration lists the articles it covers by that number. Without it there is nothing to
    match on, so those items can only be covered by hand, or by adding the numbers to BC.

13. **364 article-number fields contain Slovene remarks instead of a number.**
    `NE BO VEČ NA ZALOGI!` (122), `OPERA!` (48), `NI VEČ DOBAVLJIVO!` (27),
    `NI DOBAVLJIVO!!!` (26), `NE NAROČAJ!` (15), `samo po naročilu!` (11) and others.
    These are being compared against document article numbers as if they were codes. Can the
    field be cleaned in BC, or should we filter them out on our side?

14. **Confirm we should match on the base article number and ignore market suffixes.**
    Documents list every market variant of one article — `645986` appears as DC, DS, EA, EG,
    ES, EU, FC, FS, IS, JJ, KS, PB, PP, PS, RS, SS. BC records the base number. Reading the
    suffix as part of the number is why only 19 of 174 Ivoclar documents currently match;
    ignoring it raises that to 54. One caution: Komet's ISO bur codes (`104 H251EF 060`)
    contain letters that *are* part of the number, so this has to be set per manufacturer.

15. **Straumann pack variants.** Items `061.7312` and `061.7314` both correspond to base
    article `061.7310`, which is the number the declaration names. Is that pattern general
    across Straumann, or item-specific?

---

## D. The document corpus

16. **The SFTP folders hold 1.307 PDFs across 12 brands only.**
    DENSTPLY 405 · VOCO 292 · KOMET 186 · IVOCLAR 174 · GC 149 · DENTAURUM 37 · NEODENT 34 ·
    STRAUMANN 13 · PLANMECA 8 · 3SHAPE 7 · BREDENT 1 · LUMIWHITE 1.
    Those 12 brands account for 4.630 of 15.958 catalogue items. Is this everything you hold,
    or are there documents elsewhere (email, shared drives, supplier portals) for the other
    ~370 manufacturer codes?

17. **Two document-to-item ratios look wrong in opposite directions.**
    VOCO: 292 documents for 31 catalogue items. Ivoclar: 174 documents for 1.294 items.
    Neither is impossible, but one of them likely means either missing catalogue items, or
    documents kept for products you no longer stock. Which is it?

18. **Dentaurum's 37 documents have not been read yet** and Dentaurum has no name mapping,
    so they will go to the manual queue on the first run. We will read them before the next
    review — flagging it so it is not read as a failure.

19. **What should we do with non-device documents already in the folders?**
    Cosmetics (LUMIWHITE, Reg. 1223/2009), battery and REACH declarations (VOCO), and
    Low Voltage Directive declarations (Dentsply). Store them for completeness, or discard?

20. **Is "Declaration of Compatibility for Systems/Procedure Packs" (MDR Article 22) a type
    you need tracked?** At least 17 Dentsply endodontic kit documents are this type. It is not
    a Declaration of Conformity and it is not a certificate.

21. **Are the tracker spreadsheets on the SFTP maintained and authoritative?**
    IVOCLAR "MD or NOT.xlsx" and "Copy of Dentalia_UDI.xlsx", KOMET "Where to find DoC.xlsx"
    (3.801 rows, article number → document), GC "Devices by Class.xlsx". If they are kept
    current, they would answer several of the questions above directly and save considerable
    manual work. If they are stale, we should ignore them rather than trust them.

---

## E. Validity and renewal

22. **A Declaration of Conformity has no expiry date, by regulation.**
    MDR Annex IV requires only the place and date of issue. Only notified-body certificates
    carry an expiry (Article 56 caps them at five years). So the rule "each certificate has a
    validity date, replace it when it approaches" cannot be applied to declarations. Two
    options, and we need your ruling: chase the *certificate* the declaration cites and treat
    the declaration as valid while that certificate is, or set a staleness horizon
    ("refresh any declaration older than N years"). Which do you want?

    > **CORRECTED 2026-08-13 — do not send this wording to the client.** The legal claim is
    > right and the practical claim is wrong. Measured over the 831 declarations in the SFTP
    > folders: **251 carry an expiry phrase on page 1** (`expiry date`, `valid until`,
    > `gültig bis`) and **181 of those have a real date beside it** — e.g.
    > `END-DoC-Bioceramic Sealer-EN.pdf`, "issue date: 2020-03-23 expiry date: 2023-07-12".
    > Not every hit is the declaration's own expiry (some are a cited ISO certificate's, and
    > `EI-INS-DoC-DAC Premium Plus` dates its CE 0197 mark under the Pressure Equipment
    > Directive), so 181 is an upper bound — but it is nowhere near zero. Where a declaration
    > states an expiry we use it; the ruling is only needed for the ones that do not. Reworded
    > and reissued as question 14 of `docs/2026-08-13-vprasanja-za-dentalio.md`
    > (question 18 before the 2026-08-17 renumber).
    >
    > Consequence for the build: the planned `[validity-date-guard]` check "a `validity_to` on
    > a DoC is suspicious by type" would have flagged up to 181 legitimate documents. Dropped.

23. **Class I self-certified devices have no certificate at all** — no notified body, nothing
    to expire, nothing to chase. Confirm they are excluded from the renewal loop rather than
    sitting in it permanently unresolved.

24. **How far in advance should a renewal request go out, per document type?**
    E.g. 90 days before certificate expiry. We need one number per type to switch the reminder
    on.

---

## F. Access and scope (not blocking today, needed before the next phase)

25. **Blocked items (`Blokirano = 1`)** — 678 rows, 43 of which BC explicitly classifies as
    devices. Currently out of scope. Confirm: if any were sold previously, MDR retention still
    applies to their documents, and that is not answerable from the export.

26. **Zagreb** — is it the same Business Central instance and the same article numbering, or a
    separate system? This changes the size of the second phase.

27. **Where should the document archive live?** We need either a Google Drive folder with
    access, or confirmation that our own storage is acceptable.

28. **Mailbox access** for the automated renewal emails — which address sends, and how do we
    connect to it. Deferred, but it is the last piece of the renewal loop.
