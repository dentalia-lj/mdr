# Queues & health

**V enem stavku:** ena vrata do petih zaslonov o poganjanju sistema, ki pokažejo,
koliko hrani vsak, da vam ni treba odpirati vseh petih.

**Stanje:** Deluje. Preverjeno glede na kodo, 11. 9. 2026.

---

## Kaj je to

Do 4. septembra 2026 je imel vsak od teh petih zaslonov svojo vrstico v meniju.
Skupaj s Status, Import, Upload in Weekly reports je bil razdelek Pipeline dolg
deset vrstic — večina menija, za delo, ki ga oseba za skladnost redko opravlja.
Teh pet se je preselilo za to stran.

**Nič ni bilo odvzeto.** Vsak zaslon je nespremenjen in ima še vedno svoj naslov;
ta stran je tam, kjer jih zdaj najdete, in vam pove, koliko hrani vsak, še
preden kliknete.

Nobeden od petih ni odločitev o skladnosti. So to, kar sistem počne, česar ni
znal razumeti in kar je opustil.

---

## Teh pet

| Zaslon | Kaj vsebuje |
|---|---|
| [Processing](processing.sl.md) | Dokumenti, ki se prav zdaj premikajo skozi sistem |
| [Data quality](data-quality.sl.md) | Kar je izvoz iz Business Centrala naredil in česar nismo znali razumeti |
| [Manual](manual.sl.md) | Opravila, ki so se ustavila in potrebujejo osebo |
| [Failed](failed.sl.md) | Opravila, ki so porabila vse ponovne poskuse in za katera ni v vrsti novega poskusa |
| [Scheduler](scheduler.sl.md) | Deset ponavljajočih se preverjanj, kdaj je vsako nazadnje teklo in kdaj teče spet |
| [Business Central](bc-push.sl.md) | Artikli, pri katerih se skladnostna polja v Business Centralu ne ujemajo več s tem, kar imamo, in dva načina, kako jih poslati |

Stolpec **How many** je živo štetje in vsaka številka pove, kaj šteje:
**documents being read** (dokumenti, ki se berejo), **notes on items and
documents** (opombe o artiklih in dokumentih), **open tasks** (odprta
opravila), **failed tasks** (spodletela opravila) ali **recurring checks
scheduled** (načrtovana ponavljajoča se preverjanja). Pomišljaj pomeni nič.
Zapisan je kot pomišljaj in ne kot nič, da vrstica, v kateri nekaj je,
izstopa.

Številka pri Data quality je velika, v desettisočih, in ni delo, ki bi na
koga čakalo. Šteje stalne opombe: eno za vsako stvar, ki jo je izvoz iz
Business Centrala ali dokument naredil in je nismo znali razumeti, na primer
artikel brez razreda pripomočka v Business Centralu ali brez dobaviteljeve
številke artikla ali skeniran dokument brez besedila. Do 11. septembra 2026 je
bil stolpec naslovljen **Waiting**, zato se je ta številka brala kot zaostanek.

---

## Kaj storiti z vrstico

Odprite zaslon. Ta stran ničesar ne sproži in ničesar ne spremeni.

Eno je vredno vedeti: postavko **Manual** *preberete* na zaslonu Manual,
odločitev pa se sprejme na [Review](review.sl.md), ki je zaslon z dokumentom in
dokazili zanj. Manual vam pove, da nekaj tiči; Review je tam, kjer to odtaknete.

---

## Kam je šlo ostalo iz Pipeline

Štirje zasloni, ki so bili prej pod Pipeline, niso poganjanje sistema in noben
od njih ni skrit:

- [Import](import.sl.md) — uvoz seznama izdelkov iz Business Centrala. V meniju,
  pod **Add**.
- [Upload](upload.sl.md) — ročno dodajanje dokumenta. V meniju, pod **Add**.
- [Weekly reports](reports.sl.md) — poročilo, ki ga pošljete naprej. Dosežete ga
  z zaslona [Today](today.sl.md), ki povezuje zadnja dva tedna.
- [System status](status.sl.md) — pregledna plošča. V bloku **Operator** na dnu
  menija, skupaj s tem zaslonom.

---

## Glejte tudi

- [System status](status.sl.md) — pregled, na katerem se ta števila prav tako pojavijo
- [Review](review.sl.md) — kjer se odločitve dejansko sprejemajo
