# EUDAMED checks

**V enem stavku:** kaj evropski register pove o vseh vaših dobaviteljih na enem
zaslonu, namesto da jih odpirate enega po enega.

**Stanje:** Deluje. Preverjeno glede na kodo, 15. 9. 2026.

**Edino, kar ta stran sproži, je preverjanje**, in sicer na vrsticah, kjer je
to že zapadlo. Ne zapiše dokumenta in ničesar ne pošlje.

---

## Kaj je to

EUDAMED je evropski register medicinskih pripomočkov in certifikatov za njimi.
Sistem hrani kopijo tega, kar register pove o vaših dobaviteljih, in stran
posameznega dobavitelja je od nekdaj kazala dvoje: kaj se je spremenilo od
zadnjega preverjanja in za katere registrirane pripomočke nimate izjave.

Ta stran je oboje za vse dobavitelje hkrati. Pri več kot 380 dobaviteljih
različica po posameznem dobavitelju odgovori na "kaj je novega" le, če odprete
prav vse.

V meniju je to **EUDAMED checks**, pod Records. Dva seznama, ki sta bila prej
svoji postavki v meniju, dosežete iz vrstice povezav pod naslovom: **EUDAMED ID
(SRN) queue**, kjer potrdite, katera identiteta EUDAMED pripada kateremu
dobavitelju, in **Check due**, dobavitelji, katerih preverjanje lahko sprožite
zdaj. Oba sta opisana na strani [Manufacturers](manufacturers.sl.md).

---

## Kako brati vrstico

| Stolpec | Pomeni |
|---|---|
| **New devices** | Pripomočki, ki jih je EUDAMED registriral za tega dobavitelja in jih ob zadnjem pregledanem preverjanju ni bilo |
| **Status changes** | Registracije, ki so spremenile stanje — začasno odvzet ali umaknjen certifikat se pokaže tu |
| **Missing** | Družine pripomočkov, ki jih EUDAMED registrira, vi pa za njih nimate nobene izjave |
| **Waiting for review** | Najdeno, a čaka na vašo odločitev na zaslonu Review |
| **Covered** | Družine pripomočkov, za katere dokumentacijo že imate |
| **Last checked** | Kdaj je bil ta dobavitelj nazadnje preverjen. Prazno pomeni **nikoli preverjen**, kar ni isto kot "ni česa najti" |

Pomišljaj pomeni nič. **New devices** in **Status changes** se štejeta glede na
zadnje preverjanje, ki ste ga dejansko pogledali, zato vrstica preneha
opozarjati, ko ste jo predelali — ne pa preprosto zato, ker je minil čas.

---

## Kaj storiti z vrstico

Vsaka vrstica je povezava do dobavitelja. Eno dejanje je na vrstici sami, drugo
ostaja tam, kamor sodi:

- **Start the check** se pokaže samo na vrsticah, kjer je preverjanje že
  zapadlo. Preverjanje je resnična zahteva evropski storitvi, zato se nikoli ne
  sproži samo od sebe in gumb vedno pritisne oseba — brskalnik pred tem prosi za
  potrditev. Dobavitelja, čigar identifikatorja ni nihče potrdil, sploh ni
  mogoče preveriti — gumb je tam, a je siv, razlog pa je v opisu ob njem.
  **Check due** iste zapadle vrstice našteje posebej, če želite obdelati samo
  te.
- Za prošnjo dobavitelju za izjave, ki jih niste nikoli imeli, odprite njegovo
  stran in uporabite **Draft request**. Pripravi pismo; nikoli ga ne pošlje.

---

## Ko je videti prazno

Prazna ali skoraj prazna stran je pošteno stanje na novi namestitvi: še nič ni
bilo preverjeno. Dobavitelji se tu pojavijo, ko se izvede njihovo prvo
preverjanje. Če dobavitelja, ki ga pričakujete, sploh ni, sistem zanj še nima
povezane registracije v EUDAMED — to se začne v **EUDAMED ID (SRN) queue**, do
katere vodi povezava na vrhu te strani.

---

## Glejte tudi

- [Manufacturers](manufacturers.sl.md) — po enem dobavitelju, z gumbi
- [Documents](documents.sl.md) — kaj dejansko imate
- [Expiry](expiry.sl.md) — kaj poteče, vključno s certifikati, ki jih navaja EUDAMED
