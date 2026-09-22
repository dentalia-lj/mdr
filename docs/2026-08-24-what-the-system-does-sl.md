# Compliance Warehouse — stanje

**Dentalia d.o.o. · 25. 8. 2026 · poročilo o stanju**

Vse številke odčitane iz živega sistema 25. 8. 2026.

---

## 1. Zgrajeno in deluje

- Register: 4.265 pripomočkov, 769 dokumentov, 8.563 povezav artikel–dokument.
- Arhiv: 769 datotek. Vsaka shranjena enkrat. Podvojitve se ne shranijo.
- Brisanje ni mogoče. Hramba 10 let.
- Bralni API. Business Central bere neposredno.
- 194 od 194 objavljenih dokumentov nosi dokazila. Izjem ni.
- 16.221 opravil zaključenih. Izgubljenih nič.
- Katalog: Ljubljana. En Business Central, ena številčenja artiklov.

## 2. Kaj je nadomestilo mape

| Mapa | Sistem |
|---|---|
| Mapa na dobavitelja | Stran na dobavitelja: vsi dokumenti, vsi artikli |
| Podmapa na vrsto | Filter po vrsti: izjava, certifikat, navodila, ISO |
| Datum v imenu datoteke | Datum prebran iz dokumenta, sistem ga spremlja |
| Odpiranje PDF-a | En klik, izvirna datoteka |
| Ročno iskanje | Eno iskalno polje: artikli, dokumenti, certifikati, dobavitelji |
| Vedeti, kaj manjka | Razpredelnica na vsaki strani (točka 3) |

Noben PDF ni izgubljen.

## 3. Preverjanje popolnosti

Sistem za vsak artikel ve, kaj je zahtevano — iz razreda pripomočka.

| Zahtevano | Za koga |
|---|---|
| Izjava o skladnosti | Vsak pripomoček |
| Navodila za uporabo | Vsak pripomoček, ozka izjema za nižje razrede |
| Navedba certifikacijskega organa | Nad najpreprostejšim razredom |

Prikazano, nikoli označeno rdeče: certifikat ES, certifikat ISO 13485. Dentalia je distributer, ne proizvajalec.

Barve: zeleno = imamo in velja. Oranžno = poglej. Rdeče = manjka ali poteklo.

Mesto: stran vsakega dobavitelja, stran vsakega artikla.

## 4. Strošek in avtomatizacija

| | Ponudba | Danes |
|---|---|---|
| Delež brez umetne inteligence | raste z recepti | **62,2 %** (3.822 od 6.144 vrednosti) |
| Prvi celoviti pregled | 250–850 € | **35,76 USD skupaj** |
| Ustaljeno stanje | 10–40 €/mesec | pod spodnjim robom |

## 5. Podatki

| | |
|---|---|
| Artiklov iz Business Centrala | 15.958 |
| Medicinskih pripomočkov | 4.265 |
| Dokumentov | 769 |
| Objavljenih | 194 |
| Čaka na pregled | 128 |
| Dokazanih podatkov | 6.144 |
| Povezav artikel–dokument | 8.563 |

**Pokritost — dve številki:**

- 99,7 % (4.253) pripomočkov ima nek dokument.
- 34,2 % (1.460) ima dokument, ki navaja prav ta artikel.

Razlika: certifikat, izdan proizvajalcu, velja za vse njegove izdelke. Govori o proizvajalcu, ne o artiklu.

**Izjave o skladnosti:**

| Razred | Artiklov | Veljavna | Potekla | Nadomeščena | Čaka | Je ni |
|---|---:|---:|---:|---:|---:|---:|
| Ir | 2.569 | 0 | 0 | 0 | 0 | **2.569** |
| IIa | 1.594 | 444 | 941 | 76 | 48 | 85 |
| I | 69 | 44 | 0 | 0 | 0 | 25 |
| IIb | 33 | 0 | **31** | 0 | 0 | 2 |
| **Skupaj** | **4.265** | **488** | **972** | **76** | **48** | **2.681** |

Navodila za uporabo: **0**, v vseh razredih.

## 6. Ugotovitve

- 2.569 instrumentov za večkratno uporabo (Ir) nima lastne izjave. Pokriva jih en certifikat ISO 13485, veljaven do 2028. Ta govori o proizvajalcu.
- Ti isti artikli nimajo navodil za ponovno obdelavo. Pri instrumentih za večkratno uporabo so obvezna.
- 972 izjavam je potekla veljavnost. Niso izgubljene. Potrebna je nova različica od dobavitelja.
- 31 od 33 artiklov razreda IIb ima potekle izjave. Najvišji razred, najmanjša skupina.
- Nobenih navodil za uporabo v celotnem registru.
- 128 dokumentov in 103 postavke čakajo na potrditev.

## 7. EUDAMED

- Izmerjeno 20. 8. 2026: 47,7 % naših Basic UDI-DI se v EUDAMED najde.
- EUDAMED nima množičnega izvoza in ne vrača povezav do dokumentov.
- API ni uraden in ni dokumentiran. Deluje, a ga lahko kadarkoli spremenijo brez najave.
- Zgrajen je gumb: na strani dokumenta poizvedba po Basic UDI-DI, klik za klikom.
- Vrne seznam pripomočkov v skupini in njihove kataloške številke.
- Samodejnega zrcala celotne baze ni. Ni množičnega izvoza.
- Zgrajeno je offline preverjanje kontrolnih znakov Basic UDI-DI. Brez omrežja. 306 od 306 znanih pravilnih kod sprejetih. 3 od naših 352 zavrnjene, vse tri dejansko pokvarjene.

## 8. Ni zgrajeno

- Samodejno iskanje po straneh proizvajalcev je zgrajeno, a še ni pripeljalo dokumentov.
- Vir dokumentov danes: 862 iz arhiva, 17 iz e-pošte, 1 s spleta.

## 9. Odprto pri vas

| Postavka | Stanje |
|---|---|
| Oseba za pregledno vrsto | Nataša |
| Kontakti dobaviteljev | Na FTP. Potreben ponovni zagon strežnika |
| Razredi pripomočkov | Manjkajo pri delu artiklov. Kjer razreda ni, sistem vrstice ne oceni |
| Interna politika | Če je strožja od zakona (npr. certifikat ES vedno hranite), to postane rdeča vrstica |

## Opombe

- E-poštni agent ne pošilja ničesar. Pripravi osnutek. Pošlje človek.
