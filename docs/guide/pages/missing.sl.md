# Missing documents

**V eni povedi:** artikli, za katere je sistem iskal dokument in ga ni našel,
da ga lahko poiščete in naložite vi.

**Stanje:** V uporabi. Preverjeno v kodi 14. 9. 2026.

---

## Ali lahko tu kaj pokvarim?

Ne.

- Nič na tem zaslonu ničesar ne izbriše.
- **Upload what I found** samo odpre stran Upload. Nič se ne zgodi, dokler
  tam ne naložite datoteke.
- **Search again** prosi sistem, naj še enkrat poišče dokument za ta
  artikel. Ne dotakne se nobenega dokumenta, ki ga že imate. Brskalnik vas
  najprej prosi za potrditev.
- Dvakratni pritisk na **Search again** ne škodi. Iskanje, ki že čaka, se ne
  doda še enkrat.
- Gumbi z oznako ↗ odprejo drugo spletno stran v novem zavihku. Tukaj ne
  spremenijo ničesar.
- Če gumb vrne napako, se ni zgodilo nič. Poskusite znova ali vprašajte
  razvijalca.

---

## Kaj je to

Seznam artiklov, za katere je sistem iskal, pa ni našel nobenega dokumenta:
ne izjave, ne certifikata ne navodil, ki bi jih lahko uporabil. Vsaka kartica
je en manjkajoč dokument, za en artikel ali za skupino artiklov z enakim
imenom. Najstarejša kartica je na vrhu.

Vsaka kartica pove, kje je sistem iskal, preden je obupal, a le tam, kjer
njegov dnevnik kaže, da je res iskal:

| Kartica pravi | Kje je sistem iskal |
|---|---|
| **the manufacturer's known pages** | Na proizvajalčevih straneh za prenos, ki jih je razvijalec nastavil za branje, kadar je prebral vsaj eno od njih |
| **the web** | S spletnim iskanjem |

Sistem preveri tudi svoje datoteke, prejšnje naslove in EUDAMED, kjer lahko,
a njegov dnevnik ne pokaže, ali je bilo tam sploh kaj za preveriti, zato jih
kartica ne navaja. Kadar ni iskal na nobenem od mest zgoraj, kartica pove le
"Nothing found" in datum.

## Kdaj to uporabite

Odprite **Missing documents** kot drugi korak vašega dnevnega kroga, po
Review.

## Preden začnete

Nič. Poznavanje proizvajalca pomaga le, če želite videti artikle enega samega
proizvajalca.

## Kaj storite

1. Odprite **Missing documents**. Naslov strani se konča na `/missing`.
2. Da vidite artikle enega proizvajalca, kliknite njegovo ime v vrsti imen na
   vrhu. Najprej je šest proizvajalcev z največ manjkajočimi dokumenti,
   **+ N more** pa odpre ostale. **All** znova pokaže vse.
3. Preberite kartico: ime artikla, proizvajalca, številke artiklov in kdaj je
   sistem nazadnje iskal.
4. Dokument poiščite sami. Gumb **downloads ↗**, poimenovan po proizvajalcu
   (na primer **KAVO DENTAL downloads ↗**), odpre proizvajalčevo stran za
   prenos, kadar jo sistem pozna. **Search the web ↗** odpre spletno iskanje,
   že izpolnjeno s proizvajalcem in imenom artikla. Oba se odpreta v novem
   zavihku.
5. Če dokument najdete, shranite PDF na računalnik, kliknite **Upload what I
   found** in ga naložite. Stran Upload že ve, za kateri artikel gre.
6. Če ga ne najdete, a menite, da ga je proizvajalec morda objavil po datumu
   na kartici, kliknite **Search again** in potrdite.
7. Seznam kaže po 20 kartic naenkrat. Uporabite **Next** in **Previous** na
   dnu.

## Kaj se zgodi nato

- Po nalaganju kartica izgine s seznama, takoj ko sistem datoteko prevzame.
  Nato jo prebere in poveže z artiklom. Če je prepričan, dokument šteje
  takoj. Če ni, počaka na vas na strani [Review](review.sl.md).
- Po **Search again** se pod kartico pojavi vrstica: "The system will search
  again for this item." Če iskanje najde kaj, kar je vredno poskusiti, kartica
  takoj izgine s seznama, še preden je znano, ali je to pravi dokument.
  Najdeno gre nato skozi običajna preverjanja, zato se lahko pojavi na strani
  Review. Če to ni pravi dokument, se artikel ne vrne na ta seznam takoj:
  odprite ga pod [Items](items.sl.md) in pritisnite **Re-discover documents**.
  Če iskanje ne najde ničesar, kartica ostane in pokaže nov datum.
- Nič na tem zaslonu ne vpraša proizvajalca po e-pošti. Tega še ni.

## Kaj lahko gre narobe

| Vidite | Pomeni | Kaj storiti | Dobro ali slabo |
|---|---|---|---|
| "No items are waiting for a document." | Trenutno ne manjka nič, kar je sistem iskal | Ni ničesar za storiti | Dobro |
| "No missing documents for …" | Izbrani proizvajalec trenutno nima manjkajočih dokumentov | Kliknite **Show all** | V redu |
| Kartica brez gumba **downloads ↗** | Sistem ne pozna proizvajalčeve strani za prenos | Uporabite **Search the web ↗** ali sami odprite proizvajalčevo spletno stran | Običajno |
| "Already in progress." po **Search again** | Iskanje za ta artikel že čaka ali že teče | Nič. Preverite pozneje | Običajno |
| "Task #… is already closed." po **Search again** | Odkar ste odprli stran, je nekdo naložil dokument zanj ali pa je iskanje našlo kaj, kar je vredno poskusiti | Znova naložite stran | V redu |
| Kartica, ki pove le "Nothing found on …" | Sistem je tisti dan poskusil, a njegov dnevnik ne navaja nobenega mesta, kjer je iskal, na primer ker spletno iskanje ni nastavljeno | Dokument poiščite sami. Če to piše na vsaki kartici, povejte razvijalcu | Potrebno je vaše dejanje |
| Ista kartica še dneve po **Search again** | Sistem je znova iskal in spet ni našel ničesar | Dokument poiščite sami ali ga prosite pri proizvajalcu | Potrebno je vaše dejanje |
| S kartice ste naložili napačno datoteko | Kartica je vseeno izginila s seznama | Dokument zavrnite na strani [Review](review.sl.md). Da sistem artikel znova poišče, ga odprite pod [Items](items.sl.md) in pritisnite **Re-discover documents**. Če iskanje ne najde ničesar, se artikel vrne sem | Popravljivo |
| Sporočilo o napaki, ki ga ne prepoznate | Nekaj je spodletelo na strani sistema in ni se zgodilo nič | Poskusite znova ali vprašajte razvijalca | Poskusite znova |

## Besede, ki jih uporablja zaslon

| Zaslon pravi | Pomeni |
|---|---|
| **waiting since** | Datum, ko je sistem pri tem artiklu prvič obupal |
| **Searched … on 3 Sep 2026. Nothing found.** | Kje je sistem iskal (glejte tabelo zgoraj) in kdaj je nazadnje iskal |
| **Nothing found on 3 Sep 2026.** | Sistem je tisti dan poskusil, a ne more pokazati nobenega mesta, kjer je iskal |
| **item 12345** / **items …** / **5 items: …, +2 more** | Številke artiklov, za katere manjka dokument, kot v Business Centralu. Pri več kot treh so prikazane prve tri, ostale pa preštete |
| **#455** | Lastna številka kartice. Uporabna le, če morate o njej vprašati razvijalca |
| **+ N more** | Drugi proizvajalci z manjkajočimi dokumenti, po abecedi |

## Sorodno

- [Upload](upload.sl.md): kamor vas pripelje **Upload what I found**
- [Review](review.sl.md): kjer lahko naloženi ali najdeni dokument naslednji čaka na vas
- [Manual](manual.sl.md): celoten seznam za operaterje, ki vsebuje tudi te kartice
- [Vaš dnevni krog](../01-daily-work.sl.md): kam se Missing documents umešča v vaše jutro
- [Slovar](../glossary.sl.md): vse besede na enem mestu
