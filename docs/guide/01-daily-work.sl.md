# Vaš dnevni krog

**V enem stavku:** vsako jutro spraznite tri čakalne vrste, vsak teden pa
preglejte še tri druge stvari.

**Stanje:** V uporabi. Preverjeno na delujočem zaslonu 14. 9. 2026.

---

## Ali lahko s tem karkoli pokvarim?

Ne. Vse spodaj je bodisi odločitev, ki jo lahko sprejmete znova, bodisi
zgolj branje. Nič od tega ne izbriše dokumenta.

---

## Vsako jutro: tri čakalne vrste, v tem vrstnem redu

[Today](pages/today.sl.md) je zaslon, na katerega pridete, in je cel krog na
eni strani: štirje seznami, vsak s svojim številom in gumbom, nato napake, ki
jih lahko rešite, nato stavek o pokritosti in tedensko poročilo.

Sezname obdelujte v vrstnem redu, v katerem jih pokaže Today. Vsak napaja
naslednjega, zato pomeni, da če najprej spraznite Review, v druge prispe manj
stvari.

### 1. Review

**Številka, ki jo spremljate:** **Documents to review** na Today in isto število
ob **Review** v meniju.

Dokumenti, ki jih je sistem našel, a jih brez vas ne bo objavil. To je pravo
delo in tja naj gre vaš čas.

Odprite vsako vrstico, preberite enovrstični razlog, preverite PDF,
odobrite ali zavrnite.

Podrobna navodila: [Review](pages/review.sl.md).

**Kdaj nehati:** ko število pokaže nič ali ko ste temu namenili čas, kolikor
ga imate. Nedotaknjena čakalna vrsta Review čez noč ne stane nič — nič se ne
izgubi in nič ne poteče, ker do tega niste prišli.

### 2. Missing documents

**Številka, ki jo spremljate:** **Missing documents** na Today in isto število
v meniju.

Artikli, za katere je sistem iskal povsod, kjer zna, in ni našel nobenega
dokumenta. Začnite z najstarejšo kartico: poskusite proizvajalčevo stran za
prenos ali spletno iskanje, ki ga ponuja, in naložite, kar najdete. Če menite,
da je proizvajalec medtem kaj objavil, pritisnite **Search again**.

Podrobna navodila: [Missing documents](pages/missing.sl.md).

Drugi dve vrsti opravil, ki jih sistem preda človeku, nista na tem seznamu:
dokument, ki potrebuje odločitev, čaka na Review, opravilo, ki je dokončno
spodletelo, pa je pod Failed. Zaslon [Manual](pages/manual.sl.md) za
operaterje še vedno navaja vse tri.

### 3. Failed tasks

**Številke, ki jih spremljate:** vrstice pod *Failed tasks* na Today.
Vrstica, ki je ne vidite, je vrstica pri nič, celoten razdelek pa izgine, ko ni
nič spodletelo.

Opravila, ki jih je sistem večkrat poskusil in obupal, v treh vrstah. Prvi dve
imata gumb že na Today, vse tri pa pojasnjuje
[Failed tasks](pages/failed.sl.md):

- **Websites that took too long.** Pritisnite **Try the N again**. Običajno
  uspe.
- **Addresses that no longer work.** Pritisnite **Search again for these N**.
  Sistem poišče nov naslov vsakega dokumenta.
- **For the developer.** Ni vam treba storiti ničesar. Ponoven poskus ne bo
  pomagal, dokler vzrok ni odpravljen.

Opravilo, ki ste ga poslali nazaj, je prikazano kot *trying again* ali
*searching again* in se ne šteje več, zato so ta tri števila skupaj lahko
manjša od ploščice **dead jobs** na
[System status](pages/status.sl.md), ki šteje vsako spodletelo opravilo.

Če se ista spletna stran ali isti naslov vedno znova vrača, ne pritiskajte
naprej. Povejte razvijalcu in navedite spletno stran.

Podrobna navodila: [Failed](pages/failed.sl.md).

---

## Enkrat na teden

### Expiry

Kateri dokumenti potekajo in kateri so že potekli.

Sistem označi dokument **30 dni** pred njegovim datumom. Izjave nimajo
lastnega datuma poteka, zato so namesto tega označene pet let po izdaji —
glejte [pravilo petih let](glossary.sl.md#pravilo-petih-let).

Poglejte razdelek z dokumenti, ki jim veljavnost kmalu poteče. Karkoli je tam, pomeni dobavitelja, ki ga
je treba priganjati.

Podrobna navodila: [Expiry](pages/expiry.sl.md).

### Renewal emails

Zahtevki za obnovitev, ki jih je sistem napisal namesto vas, na podlagi
tega, kar je našel Expiry.

**Sistem jih nikoli ne pošlje.** Preberite sporočilo, po želji ga uredite,
pošljite ga iz svoje lastne pošte, nato pa ga označite kot *Sent*, da vas
sistem preneha opominjati.

Podrobna navodila: [Renewal emails](pages/drafts-out.sl.md).

### Emails received

Kaj je prispelo in kaj je sistem našel priloženo. Večinoma le potrditev, da
je bil dobaviteljev odgovor zaznan. Nič za odločati.

Podrobna navodila: [Emails received](pages/emails-in.sl.md).

---

## Enkrat na mesec ali na zahtevo

| Zaslon | Zakaj |
|---|---|
| [Today](pages/today.sl.md) | Ali pokritost narašča? Stavek pri dnu je številka za navedbo |
| [System status](pages/status.sl.md) | Koliko je sistem stal? Operaterski zaslon |
| [Check due](pages/manufacturers.sl.md) | Dobavitelji, ki so na vrsti za preverjanje v EUDAMED. Vsak čaka, da pritisnete **Start the check**; nič ne steče samo od sebe |
| [EUDAMED ID (SRN) queue](pages/manufacturers.sl.md) | Potrdite, katera identiteta v EUDAMED pripada kateremu dobavitelju |
| [Data quality](pages/data-quality.sl.md) | Nenavadnosti, vredne pozornosti. Samo za branje |

---

## Ko prispe nekaj novega

| Situacija | Kaj storiti |
|---|---|
| Dobavitelj vam dokument pošlje neposredno po e-pošti | Naložite ga prek [Upload](pages/upload.sl.md). En PDF naenkrat |
| Nov seznam artiklov iz Business Centrala | [Import](pages/import.sl.md). Pokaže vam, kaj se bo spremenilo, preden se karkoli zgodi |
| Nov dobavitelj, za katerega še nikoli niste imeli dokumentov | Najprej ga dodajte pod [Manufacturers](pages/manufacturers.sl.md), nato lahko sistem začne iskati |
| Revizor vpraša, od kod izvira datum | Odprite dokument pod [Documents](pages/documents.sl.md). Vsako dejstvo pokaže stran, s katere je bilo prebrano |

---

## Kako izgleda dobro stanje

- Review večino dni pri nič ali blizu nič.
- Na Today razdelka *Failed tasks* sploh ni ali pa je v njem le vrstica za
  razvijalca.
- Coverage nespremenjena ali naraščajoča.
- V Expiry nič, česar niste bodisi že pognali za dobaviteljem bodisi se
  odločili opustiti.

Če se Review polni hitreje, kot ga zmorete prazniti, je to vredno
izpostaviti. Navadno pomeni, da je en dobavitelj spremenil način
objavljanja, in razvijalec to lahko popravi enkrat, namesto da bi vi o tem
odločali stokrat.

---

## Kam naprej

- [Review](pages/review.sl.md) — zaslon, ki ga boste uporabljali največ
- [Začnite tukaj](00-getting-started.sl.md) — če ga še niste prebrali
- [Slovar](glossary.sl.md) — vse besede na enem mestu
