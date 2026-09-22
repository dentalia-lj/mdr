# Today

**V enem stavku:** zaslon, na katerem pristanete, in na njem vse, kar trenutno
čaka na vas, in nič drugega.

**Stanje:** Deluje. Preverjeno po kodi 15. 9. 2026.

**Ta zaslon je samo za branje, z dvema izjemama**: gumba pod *Failed tasks*, ki
sistem prosita, naj neko delo poskusi znova. Nobeden ne pošlje ničesar
dobavitelju in nobeden ne spremeni dokumenta. To sta edina gumba na strani, in
to namenoma: gumb tukaj pomeni, da pritisk nekaj spremeni. Vse drugo je
povezava, ki le odpre seznam.

---

## Kaj je to

Prvi zaslon po prijavi in tisti, na katerega se vrnete, ko nekaj končate.
Odgovarja na eno vprašanje: kaj čaka name?

14. septembra 2026 je nadomestil nadzorno ploščo sistema. Ta še vedno obstaja
kot [System status](status.sl.md), v bloku **Operator** na dnu menija, in skoraj
nič na njej ni bilo kdaj vaše delo.

Vsaka številka na tej strani se prešteje znova, vsakič ko stran odprete, in
vsaka je ista številka, kot jo pokaže zaslon za njo. Če Today pravi, da 34
dokumentov čaka na pregled, jih Review našteje 34.

---

## Kdaj to uporabite

- Prva stvar zjutraj.
- Ko končate eno vrsto in želite videti, kaj je naslednje.
- Ko kdo vpraša, kako gre pokritost: stavek pri dnu je številka za navedbo.

---

## Preden začnete

Nič. Dovolj je, da ste prijavljeni.

---

## Kaj storite

Štirje seznami so na vrhu strani drug ob drugem, na običajnem zaslonu dva v
vrsti, na ozkem oknu pa eden. Vsak ima število, en stavek, kaj to je, in eno
povezavo, ki ga odpre.

| Seznam | Kaj je v njem | Odpre |
|---|---|---|
| **Documents to review** | Dokumenti, ki jih je sistem našel in jih brez vaše odločitve ne bo objavil. Pokaže datum, od kdaj čaka najstarejši, in tri dobavitelje z največ dokumenti | **Start reviewing** → [Review](review.sl.md) |
| **Missing documents** | Artikli, za katere je sistem iskal in ni našel ničesar. Pokaže najstarejšega in tri dobavitelje z največ postavkami | **Open the list** → [Missing documents](missing.sl.md) |
| **Expiring certificates** | Certifikati, ki so potekli ali bodo kmalu potekli; ena vrstica na certifikat, ne glede na to, koliko dokumentov se nanj sklicuje. Obe okni sta navedeni v vrstici: naslednjih 30 dni in zadnjih 180 | **See which** → [Expiry](expiry.sl.md) |
| **Renewal emails to send** | Dopisi za podaljšanje, ki jih je sistem napisal iz tega, kar poteka. Nikoli jih ne pošlje | **Open drafts** → [Renewal emails](drafts-out.sl.md) |

Seznam, na katerem ni ničesar, pove **Nothing waiting** in obdrži svojo povezavo.

---

## Kaj se zgodi nato

### Failed tasks

Pod štirimi seznami so do tri vrstice o delu, ki ni uspelo. **Vrstica, ki je ne
vidite, je vrstica s številom nič** — celoten razdelek izgine, ko ni nič
spodletelo.

| Vrstica | Kaj pomeni | Kaj storite |
|---|---|---|
| **N supplier websites took too long to answer** | Stran dobavitelja je bila počasna ali nedosegljiva | Pritisnite **Try the N again**. Vpraša enkrat in navede število. Običajno uspe |
| **N document addresses no longer work** | Naslova, na katerem je dokument prej živel, ni več | Pritisnite **Search again**. Sistem poišče, kje dokument živi zdaj |
| **N technical faults are waiting for the developer** | Nekaj se je pokvarilo in ponovni poskus tega ne popravi | Nič. **What are they?** odpre [Failed tasks](failed.sl.md), če želite pogledati |

Ta števila so samo napake, ki še potrebujejo koga. Delo, ki ste ga že poslali
nazaj, izpade iz njih in se na zaslonu Failed pokaže kot *trying again* ali
*searching again*.

### Stavek o izjavah

**Declarations on file for X of Y medical-device items (Z%)** je številka za
navedbo Dentalii, vrstica pod njo pa pove točno, kaj šteje: artikle, ki jih
Business Central označi kot medicinske pripomočke, in, če takšni so, koliko
artiklov BC sploh ni razvrstil, zato niso šteti. Povezava odpre
[Coverage gaps](coverage.sl.md), kjer je število **No Declaration of
Conformity** vedno isto.

### Tedensko poročilo

Zadnja kartica poveže zadnje [tedensko poročilo](reports.sl.md) po imenu —
*Week 37 (7–13 Sep)* — in tisto pred njim, poleg tega pa **All weekly reports**
za vsa ostala. To je edina pot do tega zaslona iz menija.

---

## Kaj lahko gre narobe

| Kaj vidite | Kaj pomeni | Kaj storite | Dobro ali slabo |
|---|---|---|---|
| **Documents to review** iz dneva v dan narašča | Sistem najde več, kot zmorete pregledati | Namenite čas zaslonu Review. Če vas še naprej prehiteva, povejte razvijalcu: običajno je en dobavitelj spremenil način objave | Vaše delo |
| Datum najstarejšega čakajočega je star več tednov | Nekaj na dnu vrste se vsak dan preskoči | Odprite Review in delajte od najstarejšega, kar je vrstni red, v katerem so našteti | Vredno enega dopoldneva |
| **N supplier websites took too long** vsak dan, isto število | Ponovni poskus tega ne odpravlja | Nehajte pritiskati gumb in povejte razvijalcu, navedite dobavitelja | Razvijalčevo |
| Številka izjav pada | Manj vaših medicinskih pripomočkov ima izjavo | Običajno so iz Business Centrala prišli novi artikli brez papirjev. Vredno vprašanja | Slabo, ni nujno |
| **No weekly report yet** | Tedensko poročilo na tem računalniku ni teklo | Če tako ostane več kot teden dni, povejte razvijalcu | Razvijalčevo |

---

## Besede, ki jih uporablja zaslon

| Zaslon pravi | Pomeni |
|---|---|
| **Documents to review** | Vrsta Review. Isto število kot **Review** v meniju |
| **Missing documents** | Artikli, za katere je sistem iskal in ni našel ničesar. Isto število kot **Missing documents** v meniju |
| **Expiring certificates** | Certifikati, ki potekajo ali so potekli znotraj obeh navedenih oken. Isto število kot **Expiring** v meniju |
| **Renewal emails to send** | Osnutki, ki jih nihče ni označil kot *sent* ali arhiviral. Isto število kot **Renewal emails** v meniju |
| **Failed tasks** | Delo, ki ga je sistem večkrat poskusil in obupal. Glejte [Failed tasks](failed.sl.md) |
| **Declarations on file** | Objavljena izjava o skladnosti, povezana s samim artiklom. Glejte [Coverage gaps](coverage.sl.md) |

---

## Sorodno

- [Vaš dnevni krog](../01-daily-work.sl.md) — v kakšnem vrstnem redu delati te sezname
- [Review](review.sl.md) — kjer porabite največ časa
- [System status](status.sl.md) — nadzorna plošča sistema, za operaterje
- [Glossary](../glossary.sl.md) — vse besede na enem mestu
