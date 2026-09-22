# Decisions

**V enem stavku:** kdo je kaj odločil in kdaj — vse odločitve, ki jih je register
kdaj zabeležil, na enem zaslonu.

**Stanje:** Deluje. Preverjeno glede na kodo, 15. 9. 2026.

**Ta stran je samo za branje.** Ničesar na njej ni mogoče urediti ali odstraniti.
V tem je njen smisel: zapis, ki bi ga lahko spremenili, ni zapis.

---

## Kaj je to

Vsakič, ko je dokument objavljen, vložen, zavrnjen, nadomeščen ali ko je
povezava med dokumentom in izdelkom potrjena ali umaknjena, sistem zapiše
vrstico o tem, kaj se je zgodilo, kdo je to storil in kdaj. Ta stran je ta
seznam.

Odločitve, ki jih sprejme oseba, nosijo vašo prijavo. Odločitve, ki jih sistem
sprejme sam, nosijo ime dela sistema, ki jih je sprejel.

---

## Kako brati vrstico

| Stolpec | Pomeni |
|---|---|
| **When** | Trenutek zapisa |
| **What happened** | Kaj se je zgodilo, z besedami: *Approved* (odobreno), *Rejected* (zavrnjeno), *Published* (objavljeno), *Replaced by a newer document* (nadomeščeno z novejšim dokumentom), *Item link withdrawn* (povezava do izdelka umaknjena) in podobno |
| **Document** | Dokument, na katerega se nanaša, če obstaja. Klik vodi nanj |
| **Item no.** | Izdelek, na katerega se nanaša, če obstaja |
| **Decided by** | Oseba ali del sistema, ki je odločil |
| **Why** | Razlog, ki ga je pregledovalec zapisal s svojimi besedami — na primer *Out of date: newer 2025 version exists*. Pomišljaj pomeni, da razloga ni |
| **Technical details** | Shranjeno ime dogodka, opravilo, ki ga je izvedlo, in vse drugo, kar je odločitev zabeležila. Pri starejših vrsticah povezava do opravila lahko ne vodi nikamor, kar je pričakovano — glejte spodaj |

Filtrirajte po tem, kaj se je zgodilo, ali poiščite osebo, številko izdelka ali
številko dokumenta. Seznam za filtriranje uporablja iste besede kot vrstice.

---

## Zakaj povezava do opravila lahko ne vodi nikamor

Vsaka vrstica nosi svojo kopijo tega, kar se je zgodilo, zato ostane berljiva
tudi potem, ko je bilo opravilo za njo pospravljeno. Povezava do opravila pod
**Technical details** je kazalec, ne zapis. Kadar ne vodi nikamor, ni izgubljeno
nič: vrstica še vedno pove, kaj je bilo odločeno, kdo je odločil in kdaj.

---

## Kdaj bi jo uporabili

- Revizor vpraša, kdo je odobril določeno izjavo in kdaj.
- Dokument je objavljen in želite vedeti, ali ga je sprejela oseba ali sistem.
- Dokument je bil zavrnjen in želite vedeti, zakaj. Razlog, ki ga je pregledovalec
  izbral na strani [Review](review.sl.md), je v njegovi vrstici.
- Nekaj je videti narobe in želite zgodovino, preden to spremenite.

---

## Glejte tudi

- [Review](review.sl.md) — kjer je večina teh odločitev sprejeta
- [Documents](documents.sl.md) — dokumenti, na katere se nanašajo
